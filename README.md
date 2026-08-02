# Fast F1 App

A small Textual terminal app for browsing current-season Formula 1 event results with FastF1.

This is an unofficial project. It is not associated in any way with Formula 1, the FIA, or any of their affiliates. F1, FORMULA 1 and related marks are trademarks of Formula One Licensing B.V.

![The app showing the 2026 Belgian Grand Prix race classification with a two-driver comparison](https://raw.githubusercontent.com/ekss1121/fast-f1-app/main/docs/app_screenshot.png)

## What you get

- **The current weekend, already loaded.** On startup the app finds the most recent race weekend that has begun and opens it — no button to press.
- **One tab per session.** The tab strip is built from the weekend itself, so a sprint weekend shows Sprint Qualifying and Sprint where a conventional one shows FP2 and FP3. Tabs load lazily and keep their own state, so switching back and forth costs nothing.
- **Team-coloured classification.** Position, driver, team, status and gap, with medals for the podium. Sessions that have run but are not yet classified (practice, sprint qualifying) fall back to a ranking by best lap.
- **Per-driver detail.** Select a row for lap-time stats, tire compounds and stops, plus — in qualifying — a sector-by-sector delta to the fastest lap of the session.
- **Two-driver comparison.** Both drivers' lap times on one graph next to a metrics table with an explicit Δ column.
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

| Key | Action |
| --- | --- |
| `Enter` | Show details for the highlighted driver |
| `c` | Mark the highlighted driver for comparison (slot `A`, then `B`) |
| `x` | Clear the comparison |
| `r` | Reload the active session from FastF1 |
| `q` | Quit |

### Comparing two drivers

Marking a second driver with `c` opens the comparison view, as in the screenshot above: both drivers' lap times on one scatter graph — coloured by team, falling back to cyan/magenta when teammates share a colour — plus a side-by-side table of laps, fastest/slowest/average lap, stops and compounds, with the A−B delta in the last column. Qualifying comparisons also list each driver's delta to pole and their sector times with sector-by-sector deltas.

Pressing `c` on a third driver starts a fresh comparison from that driver.

## Test

```bash
uv run python -m unittest discover -s tests
```

## License

MIT — see [LICENSE](LICENSE).
