# Fast F1 App

[![tests](https://img.shields.io/github/actions/workflow/status/ekss1121/fast-f1-app/tests.yml?branch=main&label=tests)](https://github.com/ekss1121/fast-f1-app/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/fast-f1-app)](https://pypi.org/project/fast-f1-app/)
[![Python versions](https://img.shields.io/pypi/pyversions/fast-f1-app)](https://pypi.org/project/fast-f1-app/)
[![License](https://img.shields.io/pypi/l/fast-f1-app)](https://github.com/ekss1121/fast-f1-app/blob/main/LICENSE)

A small Textual terminal app for browsing current-season Formula 1 event results with FastF1.

This is an unofficial project. It is not associated in any way with Formula 1, the FIA, or any of their affiliates. F1, FORMULA 1 and related marks are trademarks of Formula One Licensing B.V.

![The app showing the 2026 Hungarian Grand Prix race classification, a two-driver comparison, and a track panel with the Hungaroring drawn in sector colours](https://raw.githubusercontent.com/ekss1121/fast-f1-app/main/docs/app_screenshot.png)

## What you get

- **The current weekend, already loaded.** On startup the app finds the most recent race weekend that has begun and opens it — no button to press.
- **One tab per session.** The tab strip is built from the weekend itself, so a sprint weekend shows Sprint Qualifying and Sprint where a conventional one shows FP2 and FP3. Tabs load lazily and keep their own state, so switching back and forth costs nothing.
- **Team-coloured classification.** Position, driver, team, status and gap, with medals for the podium. Sessions that have run but are not yet classified (practice, sprint qualifying) fall back to a ranking by best lap.
- **Per-driver detail.** Select a row for lap-time stats, tire compounds and stops, plus — in qualifying — a sector-by-sector delta to the fastest lap of the session.
- **Two-driver comparison.** Both drivers' lap times on one graph next to a metrics table with an explicit Δ column.
- **The circuit itself.** A track panel draws the lap in its real proportions with each official sector in its own colour, alongside the circuit's name, location, length and corner count.
- **A log pane.** FastF1's own chatter (cache hits, downloads, warnings) is piped into the panel at the bottom instead of scribbling over the UI.

## Install

Install it as a standalone command available from any directory:

```bash
uv tool install fast-f1-app
fast-f1-app
```

This builds an isolated environment for the app, so it does not touch your system Python. The executable lands in `~/.local/bin` (`C:\Users\<you>\.local\bin` on Windows); if the command is not found, run `uv tool update-shell` and restart your terminal.

To move to a newer release later, `uv tool upgrade fast-f1-app`. To remove it, `uv tool uninstall fast-f1-app`.

If you prefer pipx: `pipx install fast-f1-app` works the same way.

### From a checkout

To install the version in your working tree rather than the published one, run the same command against the directory:

```bash
uv tool install .
```

That installs a *copy*, so changes to the source will not appear until you re-run it. `uv tool install --editable .` makes the command track your working tree instead.

## Run from a checkout

Without installing:

```bash
uv run python fast_f1_app.py
```

## Using it

The app opens on the current weekend with its first session loaded. Pick a different Grand Prix from the **Event** dropdown and the tabs rebuild for that weekend; switch sessions with the tab strip.

Selecting a result row loads lap details for that driver. FastF1 caches session data, so the first load of a session is slow and later ones are quick.

The lap-time graphs and the fastest/slowest/average figures are built from *clean* laps only — pit in and out laps, and laps run under a safety car, VSC or red flag, are left out. A single out-lap is around twenty seconds off the pace, which is enough to stretch the axis and flatten every racing lap onto one row. The panel says how many laps were excluded, `Laps` still counts every lap completed, and the graph keeps real lap numbers on the x axis, so the excluded laps show up as gaps.

| Key | Action |
| --- | --- |
| `Enter` | Show details for the highlighted driver |
| `c` | Mark the highlighted driver for comparison (slot `A`, then `B`) |
| `x` | Clear the comparison |
| `r` | Reload the active session from FastF1 |
| `q` | Quit |

### Comparing two drivers

Marking a second driver with `c` opens the comparison view, as in the screenshot above: a per-lap delta graph of `B − A`, with each lap coloured by whoever was quicker — team colours, falling back to cyan/magenta when teammates share one — plus a side-by-side table of laps, clean laps, fastest/slowest/average lap, stops and compounds, with the A−B delta in the last column. Qualifying comparisons also list each driver's delta to pole and their sector times with sector-by-sector deltas.

The graph plots the difference rather than both drivers' lap times together because two drivers are usually within a second of each other while the axis has to span several, so overlaid series land on the same rows and cannot be told apart. Only laps *both* drivers ran cleanly get a delta.

Pressing `c` on a third driver starts a fresh comparison from that driver.

### The track panel

The panel at the bottom left, visible in the screenshot above, draws the weekend's circuit from position telemetry, rotated to match the official circuit map and coloured by official sector: Sector 1 red, Sector 2 cyan, Sector 3 yellow.

It belongs to the weekend rather than to a session, so it loads once and stays put while you switch tabs. The drawing keeps the circuit's real proportions, which is why a roughly square circuit like the Hungaroring leaves space either side rather than being stretched to fill the panel.

Lengths are the published figures. Where a circuit isn't on file — a brand-new venue, say — the length is measured off the trace instead and shown with a `≈`, since measuring a lap that way runs short by up to about 2%.

## Test

```bash
uv run python -m unittest discover -s tests
```

## License

MIT — see [LICENSE](LICENSE).
