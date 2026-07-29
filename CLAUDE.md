# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run the app:
```bash
uv run python main.py
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

This is a single-file Textual TUI app (`main.py`) that browses current-season F1 results via the `fastf1` library. There is no package layout — everything lives in `main.py`, and `tests/test_main.py` imports directly from it.

The code splits into two halves:

1. **`F1ResultsApp` (Textual `App` subclass)** — UI state and event wiring. On mount it loads the current year's event list in a worker (`load_current_year_events`), populating the `#event` `Select`. Loading results (`on_button_pressed`) and loading a driver's lap details (`on_data_table_row_selected`) both run the blocking FastF1 calls via `asyncio.to_thread`, since `fastf1` is synchronous. All widget updates happen back on the app after the thread call returns.

2. **Free functions (data/formatting layer)** — pure-ish functions that call FastF1 (`load_event_names`, `load_results`, `load_practice_results`, `load_driver_details`, `build_qualifying_details`) and functions that format data into `rich.text.Text` for display (`format_position_cell`, `format_compounds`, `render_driver_details`, `format_lap_time`, `format_delta`, etc.). These are kept separate from the App class specifically so they're unit-testable without spinning up the TUI — most of `tests/test_main.py` exercises this layer directly.

The comparison feature (`c` marks up to two rows, `x` clears) lives in `action_toggle_compare` / `load_comparison`. `self.compare_indexes` holds indexes into `self.result_rows`; slot markers are written back into the results table's `Cmp` column via `update_cell`, so `show_results` also records `self.result_row_keys` and `self.compare_column_key`. The right-hand side of `#results_area` holds two mutually exclusive panels — `#driver_details` (single driver) and `#comparison` (graph + metrics `DataTable`) — and each view hides the other.

Key data-flow points:
- Session type (`SESSION_TYPES`) determines the result-loading path: qualifying uses `format_qualifying_time` (best of Q3/Q2/Q1), practice sessions (`PRACTICE_SESSION_TYPES`) go through `load_practice_results` (ranks drivers by best lap since there's no official classification), and race/sprint use `session.results` directly.
- Team colors come from `fastf1.plotting.get_team_color`, with `TEAM_COLOR_FALLBACKS` as a static backup when FastF1 can't resolve a color (e.g. team not in its current-season mapping).
- The driver-details panel (`load_driver_details` + `render_driver_details`) builds lap-time stats, tire compounds/stops, and — for qualifying only — a sector-by-sector delta to the pole lap (`build_qualifying_details`). Session loading is split so `extract_driver_details` works off an already-loaded session; `load_comparison_details` uses this to load the session once for both drivers.
- Two graph renderers, deliberately different: the single-driver `make_lap_time_graph` strips ANSI (`plt.uncolorize`) and returns `str`, while `make_comparison_lap_time_graph` keeps plotext's ANSI and returns `Text.from_ansi(...)` so the two series stay colour-coded. Both use `make_y_ticks` for evenly spaced round-number ticks, and `plt.scatter` (not `plt.plot`) so only real laps get a marker — `plot` interpolates markers between points and invents data. Team hex colours must go through `to_plot_color` (plotext wants an RGB tuple, not `#rrggbb`).
- Logging: FastF1's loggers (`fastf1`, `req`, `core`, `api`) are redirected into the in-app `RichLog` widget (`#logs`) via `TextualLogHandler`, and restored to their original handlers on unmount.

Tests do not mock FastF1 network calls — they test the formatting/decoration functions and pure logic (`build_qualifying_details`, `is_valid_event_name`) directly with constructed inputs (including real `pandas` DataFrames for lap data).
