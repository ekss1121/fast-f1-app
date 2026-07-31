# Fast F1 App

A small Textual terminal app for browsing current-season Formula 1 event results with FastF1.

## Install

Install it as a standalone command available from any directory:

```bash
uv tool install .
fast-f1-app
```

This builds an isolated environment for the app, so it does not touch your system Python. The executable lands in `~/.local/bin` (`C:\Users\<you>\.local\bin` on Windows); if the command is not found, run `uv tool update-shell` and restart your terminal.

`uv tool install .` installs a *copy*, so changes to the source will not appear until you re-run it. To have the command track your working tree instead, use `uv tool install --editable .`. To remove it, `uv tool uninstall fast-f1-app`.

If you prefer pipx: `pipx install .` works the same way.

## Run from a checkout

Without installing:

```bash
uv run python fast_f1_app.py
```

On startup, the app loads the current year's Grand Prix list. Select an event and session type, then choose **Load Results**. Select a result row to load lap details for that driver.

### Comparing two drivers

| Key | Action |
| --- | --- |
| `c` | Mark the highlighted driver for comparison (slot `A`, then `B`) |
| `x` | Clear the comparison |
| `Enter` | Show single-driver details for the highlighted row |

Marking a second driver opens the comparison view: both drivers' lap times on one graph — coloured by team, falling back to cyan/magenta when teammates share a colour — plus a side-by-side table of laps, fastest/slowest/average lap, stops and compounds. Qualifying comparisons also list each driver's delta to pole and their sector times with sector-by-sector deltas.

Pressing `c` on a third driver starts a fresh comparison from that driver.

## Test

```bash
uv run python -m unittest discover -s tests
```
