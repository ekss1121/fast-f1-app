# Fast F1 App

A small Textual terminal app for browsing current-season Formula 1 event results with FastF1.

## Run

```bash
uv run python main.py
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
