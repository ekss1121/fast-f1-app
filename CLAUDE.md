# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run the app from the checkout:
```bash
uv run python fast_f1_app.py
```

Install/reinstall it as the standalone `fast-f1-app` command (`uv tool install .` copies the source, so re-run it after changes, or use `--editable`):
```bash
uv tool install --force .
```

Run all tests:
```bash
uv run python -m unittest discover -s tests
```

Run a single test file / class / method:
```bash
uv run python -m unittest tests.test_main
uv run python -m unittest tests.test_main.FormattingTests
uv run python -m unittest tests.test_main.FormattingTests.test_format_lap_time_formats_seconds_as_minutes
```

Package management is via `uv` (see `uv.lock`); add dependencies with `uv add <package>` rather than editing `pyproject.toml` by hand.

## Architecture

This is a single-module Textual TUI app (`fast_f1_app.py`) that browses current-season F1 results via the `fastf1` library. There is no package directory — everything lives in that one module, and `tests/test_main.py` imports directly from it. The module name must stay in sync with the project name in `pyproject.toml`: hatchling auto-detects `fast_f1_app.py` from the `fast-f1-app` project name, and `[project.scripts]` points the console command at `fast_f1_app:main`.

The code splits into two halves:

1. **`F1ResultsApp` (Textual `App` subclass)** — weekend resolution, the tab strip, and event wiring. On mount it runs `load_startup_weekend`, which resolves the current weekend and populates both the `#event` `Select` and the `#sessions` `TabbedContent`. There is no load button: changing the event in the dropdown is what triggers a rebuild, via `on_select_changed`. All blocking FastF1 calls run through `asyncio.to_thread`, since `fastf1` is synchronous, and all widget updates happen back on the app after the thread call returns.

2. **`SessionResultsView` (Textual `Horizontal` subclass)** — everything belonging to *one* session: the results table, the driver-detail panel, the comparison panel, and the state behind them. One instance per `TabPane`, which is what makes per-tab state isolation structural rather than something the app has to save and restore. Because several instances coexist, this half is styled by class (`.results`, `.driver-details`, `.comparison`) — never by unique id.

3. **Free functions (data/formatting layer)** — pure-ish functions that call FastF1 (`load_event_names`, `load_results`, `load_best_lap_results`, `load_driver_details`, `build_qualifying_details`) and functions that format data into `rich.text.Text` for display (`format_position_cell`, `format_compounds`, `render_driver_details`, `format_lap_time`, `format_delta`, etc.). These are kept separate from the App class specifically so they're unit-testable without spinning up the TUI — most of `tests/test_main.py` exercises this layer directly. Note the tests do not render widgets, so UI-visible regressions pass them; verify UI changes by driving the app with Textual's `run_test()` pilot.

The comparison feature (`c` marks up to two rows, `x` clears) lives on the view, in `toggle_compare` / `load_comparison`; the app's actions only delegate to whichever view is in the active tab. `compare_indexes` holds indexes into that view's `result_rows`; slot markers are written back into the results table's `Cmp` column via `update_cell`, so `show_results` also records `result_row_keys` and `compare_column_key`. Each view holds three mutually exclusive panels — the results table, the driver-detail panel, and the comparison panel (graph + metrics `DataTable`) — plus a message panel for the not-started and failure states, and showing one hides the others.

Tab mechanics worth knowing before touching them:
- The tab set is derived from the weekend, never hardcoded. `build_weekend_plan` (most recent weekend that has started) and `build_event_plan` (a named weekend) both return the same plan shape via `plan_from_event`; `show_weekend` turns that into panes. Sprint weekends therefore produce Sprint Qualifying and Sprint where conventional ones produce FP2 and FP3.
- `show_weekend` closes the `tabs_ready` latch while it adds panes. `TabbedContent` activates the first pane as it is added, which would otherwise load the wrong session — the latch is what keeps startup to a single load. Do not remove it.
- Tabs load lazily via `ensure_tab_loaded`, guarded by the view's `loaded` / `is_loading` flags. `r` refreshes by clearing `loaded` from outside and calling it again, rather than by reaching into the method.
- `on_select_changed` fires for programmatic assignment and for `set_options` too, so `loaded_event_name` is claimed *before* the startup path assigns the dropdown value. Without that, startup loads twice.

Key data-flow points:
- The result-loading path is chosen from the data, not the session type: `load_results` asks `has_official_classification(session.results)` and falls back to `load_best_lap_results` (rank by best lap) whenever the position column is entirely empty. That covers practice, sprint qualifying — whose position and Q1/Q2/Q3 columns come back empty even though its lap data is complete — and any session that has run but is not yet classified. Classified sessions use `session.results` directly, with qualifying sessions (`QUALIFYING_SESSION_TYPES`, i.e. Q and SQ) taking their time from `format_qualifying_time` (best of Q3/Q2/Q1).
- Team colors come from `fastf1.plotting.get_team_color`, with `TEAM_COLOR_FALLBACKS` as a static backup when FastF1 can't resolve a color (e.g. team not in its current-season mapping).
- The driver-details panel (`load_driver_details` + `render_driver_details`) builds lap-time stats, tire compounds/stops, and — for qualifying and sprint qualifying — a sector-by-sector delta to the fastest lap of the session (`build_qualifying_details`). Session loading is split so `extract_driver_details` works off an already-loaded session; `load_comparison_details` uses this to load the session once for both drivers.
- Every graph is built from *clean* laps only: `select_clean_laps` drops untimed laps, pit in/out laps, and laps run under `EXCLUDED_TRACK_STATUS` (safety car, VSC, red flag). This is the column-level equivalent of FastF1's `pick_wo_box().pick_track_status("4567", how="none")`, written against the columns so the tests can feed it a plain DataFrame. It exists because the y axis spans min-to-max: one out-lap is ~20s off the pace and flattens every racing lap onto a single row. `extract_driver_details` therefore returns parallel `lap_numbers` / `lap_times`, and `lap_count` stays the *real* number of laps completed — the gap between the two is disclosed by `format_clean_lap_note` and the `Clean laps` metric row, since Fastest/Slowest/Average are all clean-lap figures.
- The x axis carries real `LapNumber`s, not positional indexes, so excluded laps leave honest gaps and two drivers' series line up on the same lap. Both renderers use `plt.scatter` (never `plt.plot`): with gaps in the x axis a line silently bridges laps that were filtered out, inventing a trend nobody drove. `make_lap_ticks` keeps the first and last lap labelled.
- Two graph renderers, deliberately different: the single-driver `make_lap_time_graph` strips ANSI (`plt.uncolorize`) and returns `str`, while `make_comparison_lap_time_graph` keeps plotext's ANSI and returns `Text.from_ansi(...)`. The comparison graph plots the *per-lap delta* (`build_lap_deltas`, second minus first, on the laps both drivers ran cleanly) rather than two overlaid series — two drivers are usually within a second of each other while the axis has to span several, so overlaid series land on the same rows. It splits the points by sign into two `plt.scatter` calls so each lap carries the colour of whoever was quicker. Both use `make_y_ticks` for evenly spaced round-number ticks. Team hex colours must go through `to_plot_color` (plotext wants an RGB tuple, not `#rrggbb`).
- Logging: FastF1's loggers (`fastf1`, `req`, `core`, `api`) are redirected into the in-app `RichLog` widget (`#logs`) via `TextualLogHandler`, and restored to their original handlers on unmount.

Tests do not mock FastF1 network calls — they test the formatting/decoration functions and pure logic (`build_qualifying_details`, `is_valid_event_name`) directly with constructed inputs (including real `pandas` DataFrames for lap data).

## Agent skills

### Issue tracker

Issues and PRDs live in this repo's GitHub Issues, operated via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each mapped to an identically-named label. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
