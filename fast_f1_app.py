from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import date

import fastf1
import pandas as pd
import plotext as plt
# Private on purpose: see load_track_map for why the public telemetry API is unusable.
from fastf1 import _api as fastf1_api
from fastf1.plotting import get_team_color
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)


SESSION_SLOT_COUNT = 5
SESSION_CODES = {
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
    "Qualifying": "Q",
    "Sprint Qualifying": "SQ",
    "Sprint Shootout": "SQ",
    "Sprint": "S",
    "Race": "R",
}
QUALIFYING_SESSION_TYPE = "Q"
SPRINT_QUALIFYING_SESSION_TYPE = "SQ"
# Both are qualifying in nature, so both get the sector-versus-fastest breakdown.
QUALIFYING_SESSION_TYPES = {QUALIFYING_SESSION_TYPE, SPRINT_QUALIFYING_SESSION_TYPE}
QUALIFYING_TIME_COLUMNS = ("Q3", "Q2", "Q1")
POSITION_MEDALS = {
    1: ("🥇", "gold1"),
    2: ("🥈", "bright_white"),
    3: ("🥉", "dark_orange"),
}
COMPOUND_STYLES = {
    "SOFT": "red",
    "MEDIUM": "yellow",
    "HARD": "white",
}
COMPARE_SLOTS = ("A", "B")
COMPARE_FALLBACK_COLORS = ("cyan", "magenta")
# TrackStatus flags for safety car, safety car ending, red flag and virtual safety car.
# Laps run under any of them say nothing about the driver's pace, so the graphs drop them.
EXCLUDED_TRACK_STATUS = "4567"

# The panel's text is rendered at a fixed size, so these must stay in step with
# the #track rule in the app's CSS: the content is the styled width and height
# less the one-cell border on each side.
TRACK_CONTENT_WIDTH = 60
TRACK_CONTENT_HEIGHT = 12
TRACK_TEXT_WIDTH = 22
# Blank columns between the map and the text, so a wide circuit like Monaco
# cannot run its last corner into the first letter of the description.
TRACK_GUTTER = 2
TRACK_MARKER = "•"
LEGEND_MARKER = "■"
SECTOR_COLORS = {1: "red", 2: "cyan", 3: "yellow"}
# The position API reports coordinates in tenths of a metre.
POSITION_UNITS_PER_METRE = 10.0
# A terminal character cell is about twice as tall as it is wide. Every projection
# has to divide the vertical span by this or the circuit comes out squashed.
CHARACTER_ASPECT = 2.0
# Published circuit lengths in metres, keyed by the F1 API's circuit key.
# Measuring the trace instead is systematically short — a polyline cuts corners,
# and the error reaches 2% at Monaco — so the published figure wins where we have
# it. Circuits absent here (a brand-new venue) fall back to the traced estimate,
# which is labelled as approximate.
OFFICIAL_CIRCUIT_LENGTHS = {
    2: 5891,  # Silverstone
    4: 4381,  # Hungaroring
    6: 4909,  # Imola
    7: 7004,  # Spa-Francorchamps
    9: 5513,  # Circuit of the Americas
    10: 5278,  # Albert Park
    14: 4309,  # Interlagos
    15: 4657,  # Barcelona-Catalunya
    19: 4318,  # Red Bull Ring
    22: 3337,  # Monte Carlo
    23: 4361,  # Gilles Villeneuve
    39: 5793,  # Monza
    46: 5807,  # Suzuka
    49: 5451,  # Shanghai
    55: 4259,  # Zandvoort
    61: 4940,  # Marina Bay
    63: 5412,  # Sakhir
    65: 4304,  # Hermanos Rodriguez
    70: 5281,  # Yas Marina
    144: 6003,  # Baku
    149: 6174,  # Jeddah
    150: 5419,  # Lusail
    151: 5412,  # Miami
    152: 6201,  # Las Vegas
}


class TextualLogHandler(logging.Handler):
    def __init__(self, app: "F1ResultsApp") -> None:
        super().__init__()
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        try:
            self.app.call_from_thread(self.app.write_log_message, message)
        except RuntimeError:
            self.app.write_log_message(message)


class SessionResultsView(Horizontal):
    """Results table plus the driver-detail and comparison panels for one session.

    Owns every widget and every piece of state belonging to a single session, so
    that several of these can coexist without colliding.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.result_rows: list[dict[str, object]] = []
        self.compare_indexes: list[int] = []
        self.compare_column_key = None
        self.result_row_keys: list[object] = []
        self.year = date.today().year
        self.event_name = ""
        self.session_type = "R"
        self.session_name = ""
        self.session_start = None
        self.has_started = True
        self.loaded = False
        self.is_loading = False

    def compose(self) -> ComposeResult:
        message = Static("", classes="session-message")
        message.display = False
        yield message
        yield DataTable(classes="results")
        details = Static("", classes="driver-details")
        details.display = False
        yield details
        with Vertical(classes="comparison"):
            yield Static("", classes="compare-graph")
            yield DataTable(classes="compare-metrics")

    def on_mount(self) -> None:
        self.query_one(".results", DataTable).cursor_type = "row"
        self.query_one(".comparison", Vertical).display = False
        self.query_one(".session-message", Static).display = False

    def set_status(self, message: str) -> None:
        self.app.set_status(message)

    def set_context(self, year: int, event_name: str, session: dict[str, object]) -> None:
        self.year = year
        self.event_name = event_name
        self.session_type = str(session["code"])
        self.session_name = str(session["name"])
        self.session_start = session.get("start")
        self.has_started = bool(session.get("has_started", True))
        self.loaded = False
        self.is_loading = False

    def show_message(self, message: str) -> None:
        """Give the whole tab over to one message: not started yet, or failed.

        Kept inside the view so a session that cannot be shown says so where the
        user is looking, and leaves the other tabs untouched.
        """
        self.result_rows = []
        self.compare_indexes = []
        self.result_row_keys = []
        self.query_one(".results", DataTable).display = False
        self.query_one(".driver-details", Static).display = False
        self.query_one(".comparison", Vertical).display = False
        panel = self.query_one(".session-message", Static)
        panel.display = True
        panel.update(message)

    def show_results(self, rows: list[dict[str, object]]) -> None:
        self.result_rows = rows
        self.compare_indexes = []
        self.query_one(".session-message", Static).display = False
        self.query_one(".results", DataTable).display = True
        details = self.query_one(".driver-details", Static)
        details.display = False
        details.update("")
        self.query_one(".comparison", Vertical).display = False

        table = self.query_one(".results", DataTable)
        table.clear(columns=True)
        column_keys = table.add_columns("Cmp", "Pos", "No", "Driver", "Team", "Status", "Time")
        self.compare_column_key = column_keys[0]

        self.result_row_keys = [
            table.add_row(
                "",
                format_position_cell(row["position"]),
                str(row["number"]),
                str(row["driver"]),
                Text(str(row["team"]), style=str(row["team_color"])),
                str(row["status"]),
                str(row["time"]),
                key=str(row["driver_number"]),
            )
            for row in rows
        ]

    def refresh_compare_markers(self) -> None:
        table = self.query_one(".results", DataTable)
        if self.compare_column_key is None:
            return

        colors = self.compare_colors_for_indexes()
        for index, row_key in enumerate(self.result_row_keys):
            marker = ""
            if index in self.compare_indexes:
                slot = self.compare_indexes.index(index)
                marker = Text(COMPARE_SLOTS[slot], style=f"bold {colors[slot]}")
            table.update_cell(row_key, self.compare_column_key, marker)

    def compare_colors_for_indexes(self) -> tuple[str, str]:
        if len(self.compare_indexes) < 2:
            return tuple(
                str(self.result_rows[index]["team_color"]) for index in self.compare_indexes
            ) + COMPARE_FALLBACK_COLORS[len(self.compare_indexes) :]
        first, second = self.compare_indexes
        return resolve_comparison_colors(
            str(self.result_rows[first]["team_color"]),
            str(self.result_rows[second]["team_color"]),
        )

    def clear_compare(self) -> None:
        self.compare_indexes = []
        self.refresh_compare_markers()
        self.query_one(".comparison", Vertical).display = False
        self.set_status("Comparison cleared.")

    def toggle_compare(self) -> None:
        table = self.query_one(".results", DataTable)
        index = table.cursor_row
        if not self.result_rows or index is None or index >= len(self.result_rows):
            return

        if index in self.compare_indexes:
            self.compare_indexes.remove(index)
        elif len(self.compare_indexes) >= len(COMPARE_SLOTS):
            self.compare_indexes = [index]
        else:
            self.compare_indexes.append(index)

        self.refresh_compare_markers()

        if len(self.compare_indexes) < len(COMPARE_SLOTS):
            self.query_one(".comparison", Vertical).display = False
            names = [str(self.result_rows[i]["driver"]) for i in self.compare_indexes]
            if names:
                self.set_status(f"Marked {names[0]} as A. Press c on another driver to compare.")
            else:
                self.set_status("Press c on a driver row to mark it for comparison.")
            return

        self.run_worker(self.load_comparison(), exclusive=True)

    async def load_comparison(self) -> None:
        comparison = self.query_one(".comparison", Vertical)
        graph = self.query_one(".compare-graph", Static)
        metrics_table = self.query_one(".compare-metrics", DataTable)

        rows = [self.result_rows[index] for index in self.compare_indexes]
        names = [str(row["driver"]) for row in rows]
        self.query_one(".driver-details", Static).display = False
        comparison.display = True
        graph.update(f"Loading comparison for {names[0]} vs {names[1]}...")
        metrics_table.clear(columns=True)
        self.set_status(f"Loading comparison for {names[0]} vs {names[1]}...")

        try:
            details = await asyncio.to_thread(
                load_comparison_details,
                self.year,
                self.event_name,
                self.session_type,
                [str(row["driver_number"]) for row in rows],
            )
        except Exception as exc:
            graph.update(f"Could not load comparison: {exc}")
            self.set_status(f"Could not load comparison: {exc}")
            return

        colors = self.compare_colors_for_indexes()
        graph.update(
            make_comparison_lap_time_graph(
                [
                    {
                        "label": str(row["abbreviation"]),
                        "lap_numbers": detail["lap_numbers"],
                        "lap_times": detail["lap_times"],
                        "color": color,
                    }
                    for row, detail, color in zip(rows, details, colors)
                ]
            )
        )

        metrics_table.clear(columns=True)
        metrics_table.add_column("Metric", width=14)
        for row, color in zip(rows, colors):
            metrics_table.add_column(Text(str(row["abbreviation"]), style=f"bold {color}"), width=12)
        metrics_table.add_column("Δ A-B", width=10)
        for metric in build_comparison_metrics(details[0], details[1], self.session_type):
            metrics_table.add_row(*metric)

        self.set_status(f"Comparing {names[0]} vs {names[1]}.")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not event.data_table.has_class("results"):
            return
        if event.cursor_row >= len(self.result_rows):
            return

        row = self.result_rows[event.cursor_row]
        details = self.query_one(".driver-details", Static)
        driver = str(row["driver"])
        driver_number = str(row["driver_number"])
        self.query_one(".comparison", Vertical).display = False
        details.display = True
        details.update(f"Loading lap details for {driver}...")
        self.set_status(f"Loading lap details for {driver}...")

        try:
            driver_details = await asyncio.to_thread(
                load_driver_details,
                self.year,
                self.event_name,
                self.session_type,
                driver_number,
            )
        except Exception as exc:
            details.update(f"Could not load driver details: {exc}")
            self.set_status(f"Could not load driver details: {exc}")
            return

        details.update(render_driver_details(row, driver_details))
        self.set_status(f"Loaded lap details for {driver}.")


class F1ResultsApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #controls {
        height: auto;
        padding: 1;
    }

    Select {
        width: 20;
        margin-right: 1;
    }

    #event {
        width: 36;
    }

    #status {
        height: 3;
        padding: 1;
    }

    #sessions {
        height: 1fr;
    }

    SessionResultsView {
        height: 1fr;
    }

    .results {
        width: 1fr;
        height: 1fr;
    }

    .session-message {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }

    .driver-details {
        width: 64;
        height: 1fr;
        padding: 1;
        border: solid green;
    }

    .comparison {
        width: 66;
        height: 1fr;
        padding: 1;
        border: solid yellow;
    }

    .compare-graph {
        height: auto;
    }

    .compare-metrics {
        height: auto;
        margin-top: 1;
    }

    /* The track panel's text is rendered to a fixed size, so these two rules and
       TRACK_CONTENT_WIDTH / TRACK_CONTENT_HEIGHT are one decision spelled in three
       places: #bottom's height and #track's width each carry the constant plus the
       one-cell border on each side. Change one, change all three. */
    #bottom {
        height: 14;
    }

    #track {
        width: 62;
        height: 1fr;
        border: solid magenta;
    }

    #log_area {
        width: 1fr;
        height: 1fr;
    }

    #log_title {
        height: 1;
        padding: 0 1;
    }

    #logs {
        height: 1fr;
        border: solid blue;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_session", "Refresh session"),
        ("c", "toggle_compare", "Mark for compare"),
        ("x", "clear_compare", "Clear compare"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="controls"):
            yield Label("Event")
            yield Select([], prompt="Loading events...", allow_blank=True, id="event", disabled=True)
        yield Static("Finding the current race weekend...", id="status")
        yield TabbedContent(id="sessions")
        with Horizontal(id="bottom"):
            yield Static("", id="track")
            with Vertical(id="log_area"):
                yield Static("Logs", id="log_title")
                yield RichLog(id="logs", markup=True, highlight=True)
        yield Footer()

    @property
    def active_view(self) -> SessionResultsView | None:
        pane = self.query_one("#sessions", TabbedContent).active_pane
        if pane is None:
            return None
        views = pane.query(SessionResultsView)
        return views.first(SessionResultsView) if views else None

    def set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def on_mount(self) -> None:
        self.event_names: list[str] = []
        self.season_year = date.today().year
        self.schedule = None
        self.tabs_ready = False
        self.loaded_event_name: str | None = None
        # The circuit belongs to the weekend, not to a session, so it is held here
        # rather than on the views and fetched once per event.
        self.track: dict[str, object] | None = None
        self.track_event_name: str | None = None
        self.track_loading = False
        self.query_one("#track", Static).border_title = "Track"

        self.log_handler = TextualLogHandler(self)
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
        )
        self.managed_loggers = [logging.getLogger(name) for name in ("fastf1", "req", "core", "api")]
        self.previous_logger_handlers = {
            logger: list(logger.handlers) for logger in self.managed_loggers
        }
        self.previous_logger_levels = {
            logger: logger.level for logger in self.managed_loggers
        }
        for logger in self.managed_loggers:
            for handler in self.previous_logger_handlers[logger]:
                logger.removeHandler(handler)
            logger.addHandler(self.log_handler)
            logger.setLevel(logging.INFO)

        self.run_worker(self.load_startup_weekend(), exclusive=True)

    def on_unmount(self) -> None:
        if not hasattr(self, "log_handler"):
            return

        for logger in self.managed_loggers:
            logger.removeHandler(self.log_handler)
            for handler in self.previous_logger_handlers[logger]:
                logger.addHandler(handler)
            logger.setLevel(self.previous_logger_levels[logger])

    def write_log_message(self, message: str) -> None:
        logs = self.query_one("#logs", RichLog)
        logs.write(message)

    async def load_startup_weekend(self) -> None:
        status = self.query_one("#status", Static)
        event_select = self.query_one("#event", Select)
        status.update("Finding the current race weekend...")

        try:
            resolved = await asyncio.to_thread(resolve_startup_weekend, utc_now())
        except Exception as exc:
            status.update(format_schedule_error(utc_now().year, exc))
            return

        self.schedule = resolved["schedule"]
        event_names = list(resolved["event_names"])
        self.event_names = event_names
        event_select.set_options([(name, name) for name in event_names])
        if not event_names:
            event_select.disabled = True
            status.update(format_empty_season_message(int(resolved["year"])))
            return

        event_select.disabled = False

        plan = resolved["plan"]
        if plan is None:
            status.update("Select a Grand Prix to load its weekend.")
            event_select.value = event_names[0]
            return

        # Claim the event before assigning it, so the Changed message this
        # assignment posts is recognised as our own and does not load twice.
        self.loaded_event_name = str(plan["event_name"])
        event_select.value = str(plan["event_name"])
        await self.show_weekend(plan)

    async def show_weekend(self, plan: dict[str, object]) -> None:
        """Rebuild the tab strip for one weekend and load only the opening tab."""
        status = self.query_one("#status", Static)
        tabs = self.query_one("#sessions", TabbedContent)

        self.season_year = int(plan["year"])
        event_name = str(plan["event_name"])
        sessions = list(plan["sessions"])
        self.loaded_event_name = event_name

        # A new weekend means a new circuit; drop the old one so it cannot be
        # shown against the wrong event while the new one loads.
        self.track = None
        self.track_event_name = event_name
        self.track_loading = False
        self.show_track_message("Loading track...")

        self.tabs_ready = False
        await tabs.clear_panes()
        for session in sessions:
            view = SessionResultsView()
            view.set_context(self.season_year, event_name, session)
            await tabs.add_pane(TabPane(str(session["name"]), view, id=tab_id(session["code"])))

        tabs.active = tab_id(str(plan["default_session"]))
        self.tabs_ready = True
        status.update(f"{self.season_year} {event_name} - {len(sessions)} sessions.")
        await self.ensure_active_tab_loaded()

    async def ensure_active_tab_loaded(self) -> None:
        view = self.active_view
        if view is not None:
            await self.ensure_tab_loaded(view)

    async def ensure_tab_loaded(self, view: SessionResultsView) -> None:
        if view.loaded or view.is_loading:
            return

        status = self.query_one("#status", Static)
        if not view.has_started:
            view.show_message(format_not_started_message(view.session_name, view.session_start))
            status.update(format_not_started_status(view.session_name, view.session_start))
            return

        view.is_loading = True
        view.loading = True
        status.update(f"Loading {view.event_name} {view.session_name}...")

        try:
            rows = await asyncio.to_thread(
                load_results, view.year, view.event_name, view.session_type
            )
        except Exception as exc:
            # Left unloaded on purpose: coming back to the tab retries.
            view.is_loading = False
            view.loading = False
            view.show_message(format_session_error(view.session_name, exc))
            status.update(f"Could not load {view.session_name}: {exc}")
            return

        view.loading = False
        view.is_loading = False
        view.loaded = True
        view.show_results(rows)
        status.update(f"Loaded {len(rows)} result rows for {view.session_name}.")
        self.ensure_track_loaded(view)

    def ensure_track_loaded(self, view: SessionResultsView) -> None:
        """Fetch the circuit once per weekend, off a session that has already loaded.

        Driven from a successful session load rather than from startup, because the
        geometry is taken from a real lap: a Friday morning with no completed laps
        has nothing to draw, and the next session to load retries on its own.
        """
        if self.track is not None or self.track_loading:
            return

        self.track_loading = True
        self.run_worker(self.load_track(view.year, view.event_name, view.session_type))

    async def load_track(self, year: int, event_name: str, session_type: str) -> None:
        try:
            track = await asyncio.to_thread(load_track_map, year, event_name, session_type)
        except Exception as exc:
            # Left unloaded on purpose: the next session to load tries again.
            self.track_loading = False
            if event_name == self.track_event_name:
                self.show_track_message(format_track_error(exc))
            return

        self.track_loading = False
        if event_name != self.track_event_name:
            # The weekend changed while this was in flight.
            return

        self.track = track
        self.query_one("#track", Static).update(
            render_track_panel(track, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)
        )

    def show_track_message(self, message: str) -> None:
        self.query_one("#track", Static).update(Text(message, style="dim"))

    async def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if not self.tabs_ready:
            return
        await self.ensure_active_tab_loaded()

    def is_valid_event_name(self, value: object) -> bool:
        return isinstance(value, str) and value in self.event_names

    async def on_select_changed(self, event: Select.Changed) -> None:
        """Picking another Grand Prix rebuilds the weekend, with no button press."""
        if event.select.id != "event" or not hasattr(self, "event_names"):
            return
        if not self.is_valid_event_name(event.value):
            return

        event_name = str(event.value)
        if event_name == self.loaded_event_name:
            # Startup and set_options assign the value themselves; only a change
            # to a weekend we are not already showing is worth rebuilding for.
            return

        plan = build_event_plan(self.schedule, event_name, utc_now())
        if plan is None:
            self.set_status(f"No sessions found for {event_name}.")
            return

        await self.show_weekend(plan)

    def action_refresh_session(self) -> None:
        view = self.active_view
        if view is None or view.is_loading:
            return
        self.run_worker(self.refresh_tab(view))

    async def refresh_tab(self, view: SessionResultsView) -> None:
        """Discard one tab's cached rows and fetch them again, leaving others alone."""
        view.loaded = False
        await self.ensure_tab_loaded(view)

    def action_toggle_compare(self) -> None:
        view = self.active_view
        if view is not None:
            view.toggle_compare()

    def action_clear_compare(self) -> None:
        view = self.active_view
        if view is not None:
            view.clear_compare()


def load_event_names(year: int) -> list[str]:
    schedule = fastf1.get_event_schedule(year)
    schedule = schedule[schedule["RoundNumber"] > 0]
    return [str(name) for name in schedule["EventName"].tolist()]


def event_names_of(schedule: object) -> list[str]:
    rounds = schedule[schedule["RoundNumber"] > 0]
    return [str(name) for name in rounds["EventName"].tolist()]


def utc_now() -> object:
    return pd.Timestamp.utcnow().tz_localize(None)


def session_code(name: str) -> str:
    return SESSION_CODES.get(name, name)


def tab_id(code: str) -> str:
    """Widget ids must be identifiers, so codes become tab-prefixed ids."""
    return "tab_" + re.sub(r"\W", "_", code)


def weekend_sessions(event: object) -> list[dict[str, object]]:
    """Read one schedule row's sessions, earliest first.

    The session set is a property of the weekend's format, not a fixed list:
    sprint weekends carry Sprint Qualifying and Sprint in place of FP2 and FP3.
    """
    sessions: list[dict[str, object]] = []
    for slot in range(1, SESSION_SLOT_COUNT + 1):
        name = event.get(f"Session{slot}")
        start = event.get(f"Session{slot}DateUtc")
        if not isinstance(name, str) or not name:
            continue
        if start is None or start != start:
            continue
        sessions.append(
            {
                "code": session_code(name),
                "name": name,
                "start": start,
            }
        )
    sessions.sort(key=lambda session: session["start"])
    return sessions


def plan_from_event(event: object, now: object) -> dict[str, object] | None:
    """Describe one schedule row's weekend: its sessions and which to open on."""
    sessions = weekend_sessions(event)
    if not sessions:
        return None

    for session in sessions:
        session["has_started"] = bool(session["start"] <= now)
    started = [session for session in sessions if session["has_started"]]

    return {
        "year": int(event["EventDate"].year),
        "event_name": str(event["EventName"]),
        "sessions": sessions,
        "default_session": str(started[-1]["code"]) if started else str(sessions[0]["code"]),
    }


def build_event_plan(schedule: object, event_name: str, now: object) -> dict[str, object] | None:
    """Describe a named weekend, whether or not it has started yet."""
    rounds = schedule[schedule["RoundNumber"] > 0]
    matches = rounds[rounds["EventName"] == event_name]
    if matches.empty:
        return None
    return plan_from_event(matches.iloc[0], now)


def build_weekend_plan(schedule: object, now: object) -> dict[str, object] | None:
    """Describe the most recent weekend that has started, or None if none has.

    "Started" means the weekend's first session is in the past, so during a race
    weekend this picks that weekend rather than the previously completed round.
    Feeding this the previous season's schedule yields its final round, which is
    how the off-season falls back.
    """
    rounds = schedule[schedule["RoundNumber"] > 0]

    chosen = None
    for _, event in rounds.iterrows():
        sessions = weekend_sessions(event)
        if not sessions or sessions[0]["start"] > now:
            continue
        if chosen is None or sessions[0]["start"] > chosen[0]:
            chosen = (sessions[0]["start"], event)

    if chosen is None:
        return None
    return plan_from_event(chosen[1], now)


def resolve_startup_weekend(now: object) -> dict[str, object]:
    """Resolve the weekend to open on, falling back to last season out of season."""
    year = now.year
    schedule = fastf1.get_event_schedule(year)
    plan = build_weekend_plan(schedule, now)
    if plan is None:
        year = now.year - 1
        schedule = fastf1.get_event_schedule(year)
        plan = build_weekend_plan(schedule, now)
    return {
        "plan": plan,
        "event_names": event_names_of(schedule),
        "schedule": schedule,
        "year": year,
    }


def format_session_start(start: object) -> str:
    """Spell out a session's scheduled start; schedule times are UTC-naive."""
    if start is None:
        return "an unannounced time"
    try:
        if start != start:
            return "an unannounced time"
    except Exception:
        pass
    return pd.Timestamp(start).strftime("%a %d %b %Y %H:%M") + " UTC"


def format_not_started_message(session_name: str, start: object) -> str:
    return (
        f"{session_name} has not started yet.\n\n"
        f"Scheduled to start at {format_session_start(start)}.\n\n"
        "Results will be available once the session has run."
    )


def format_not_started_status(session_name: str, start: object) -> str:
    return f"{session_name} has not started yet - scheduled for {format_session_start(start)}."


def format_session_error(session_name: str, error: object) -> str:
    return (
        f"Could not load {session_name}.\n\n"
        f"{error}\n\n"
        "The other sessions of this weekend are unaffected. "
        "Switch to another tab and back to try this one again."
    )


def format_schedule_error(year: int, error: object) -> str:
    """Distinct from an empty season: the fetch itself failed."""
    # The lead clause carries the distinction, since the status line is one line
    # tall and a library error message can be long enough to be clipped.
    return f"Could not fetch the {year} race schedule - no events could be listed. {error}"


def format_empty_season_message(year: int) -> str:
    return f"The {year} race schedule was fetched successfully but lists no Grand Prix events."


TEAM_COLOR_FALLBACKS = {
    "Alpine": "#0090ff",
    "Aston Martin": "#006f62",
    "Ferrari": "#dc0000",
    "Haas F1 Team": "#b6babd",
    "Kick Sauber": "#52e252",
    "McLaren": "#ff8700",
    "Mercedes": "#00d2be",
    "RB": "#6692ff",
    "Racing Bulls": "#6692ff",
    "Red Bull Racing": "#0600ef",
    "Williams": "#005aff",
}


def format_result_time(value: object) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except Exception:
        pass

    text = str(value)
    if text in {"NaT", "nan", "None"}:
        return ""
    if " days " in text:
        return text.split(" days ", 1)[1]
    return text


def format_qualifying_time(result: object) -> str:
    for column in QUALIFYING_TIME_COLUMNS:
        time = format_result_time(result.get(column, ""))
        if time and time not in {"NaT", "nan", "None"}:
            return time
    return ""


def get_result_team_color(team_name: str, session: fastf1.core.Session) -> str:
    try:
        return get_team_color(team_name, session=session)
    except Exception:
        return TEAM_COLOR_FALLBACKS.get(team_name, "white")


def to_position(value: object) -> object:
    try:
        return int(value)
    except (TypeError, ValueError):
        return ""


def format_position_cell(position: object) -> Text:
    try:
        numeric_position = int(position)
    except (TypeError, ValueError):
        return Text(str(position))

    medal = POSITION_MEDALS.get(numeric_position)
    if medal is None:
        return Text(str(numeric_position))

    emoji, style = medal
    return Text(f"{emoji} {numeric_position}", style=style)


def format_compounds(compounds: list[str]) -> Text:
    if not compounds:
        return Text("None")

    text = Text()
    for index, compound in enumerate(compounds):
        if index:
            text.append(", ")
        normalized = compound.upper()
        text.append("🛞 ")
        text.append(compound, style=COMPOUND_STYLES.get(normalized, ""))
    return text


def time_to_seconds(value: object) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    if not hasattr(value, "total_seconds"):
        return None
    return float(value.total_seconds())


def format_delta(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    if abs(seconds) < 0.0005:
        return "+0.000"
    return f"{seconds:+.3f}"


def format_lap_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    remaining = seconds - minutes * 60
    return f"{minutes}:{remaining:06.3f}"


def format_practice_lap_time(value: object) -> str:
    if hasattr(value, "total_seconds"):
        return format_lap_time(float(value.total_seconds()))
    return format_result_time(value)


def make_y_ticks(minimum: float, maximum: float, target_count: int = 10) -> list[float]:
    span = maximum - minimum
    if span <= 0:
        minimum -= 0.1
        span = 0.2

    raw_step = span / target_count
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for multiplier in (1, 2, 5, 10):
        step = multiplier * magnitude
        if raw_step <= step:
            break

    decimals = max(0, -math.floor(math.log10(step)))
    start = math.floor(minimum / step) * step
    count = math.ceil((maximum - start) / step) + 1
    return [round(start + index * step, decimals) for index in range(count)]


def make_lap_ticks(lap_numbers: list[int]) -> list[int]:
    """Four-ish evenly spaced x ticks that always include the first and last lap."""
    first, last = lap_numbers[0], lap_numbers[-1]
    step = max(1, (last - first) // 4)
    ticks = list(range(first, last + 1, step))
    if ticks[-1] != last:
        ticks.append(last)
    return ticks


def make_lap_time_graph(lap_numbers: list[int], lap_times: list[float]) -> str:
    if not lap_times:
        return "No lap times available."

    plt.clear_figure()
    y_ticks = make_y_ticks(min(lap_times), max(lap_times))

    plt.plotsize(58, max(14, min(26, len(y_ticks) * 2)))
    plt.theme("clear")
    plt.title("Lap times (clean laps)")
    plt.xlabel("Lap")
    plt.ylabel("Seconds")
    plt.ylim(y_ticks[0], y_ticks[-1])
    plt.yticks(y_ticks)
    plt.xticks(make_lap_ticks(lap_numbers))
    # scatter, not plot: the x axis carries real lap numbers, so filtered-out laps leave
    # gaps that a line would silently bridge with a trend that was never driven.
    plt.scatter(lap_numbers, lap_times, marker="dot")
    graph = plt.uncolorize(plt.build())
    plt.clear_figure()
    return graph


def format_clean_lap_note(lap_count: int, clean_count: int) -> Text:
    """Say how many laps the graph and the timing stats are actually built from.

    Fastest/Slowest/Average all come from the clean laps, but Laps stays the real
    number of laps completed, so the difference has to be spelled out rather than
    left for the reader to notice.
    """
    excluded = lap_count - clean_count
    if excluded <= 0:
        return Text("Timings from all " + str(clean_count) + " laps.", style="dim")
    return Text(
        "Timings and graph from "
        + str(clean_count)
        + " clean laps; "
        + str(excluded)
        + " excluded (pit in/out, safety car, red flag).",
        style="dim",
    )


def render_driver_details(row: dict[str, object], details: dict[str, object]) -> Text:
    compounds = details["compounds"]
    lap_times = details["lap_times"]
    fastest = format_lap_time(min(lap_times)) if lap_times else "N/A"
    slowest = format_lap_time(max(lap_times)) if lap_times else "N/A"

    text = Text()
    text.append(str(row["driver"]) + " #" + str(row["driver_number"]) + " - " + str(row["team"]) + "\n")
    qualifying = details.get("qualifying")
    if qualifying:
        text.append("Qualifying best lap: " + str(qualifying["lap_time"]))
        text.append(" (delta to pole " + str(qualifying["delta_to_pole"]) + ")\n")
        text.append("Sectors vs pole:\n")
        for sector in qualifying["sectors"]:
            text.append("  " + str(sector["name"]) + ": " + str(sector["time"]))
            text.append(" (" + str(sector["delta_to_pole"]) + ")\n")
        text.append("\n")

    text.append("Stops: " + str(details["stops"]) + " | Tire compounds used: " + str(len(compounds)) + " (")
    text.append_text(format_compounds(compounds))
    text.append(")\n")
    text.append("Lap time graph:\n" + make_lap_time_graph(details["lap_numbers"], lap_times) + "\n")
    text.append("Laps: " + str(details["lap_count"]) + " | Fastest: " + fastest + " | Slowest: " + slowest)
    text.append("\n")
    text.append_text(format_clean_lap_note(int(details["lap_count"]), len(lap_times)))
    return text


def format_optional_lap_time(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    return format_lap_time(seconds)


def to_plot_color(color: str) -> object:
    text = str(color).strip()
    if text.startswith("#") and len(text) == 7:
        return tuple(int(text[index : index + 2], 16) for index in (1, 3, 5))
    return text


def resolve_comparison_colors(first: str, second: str) -> tuple[str, str]:
    if str(first).strip().lower() == str(second).strip().lower():
        return COMPARE_FALLBACK_COLORS
    return str(first), str(second)


def build_lap_deltas(
    first: dict[str, object], second: dict[str, object]
) -> list[tuple[int, float]]:
    """Per-lap ``second - first`` on the laps both drivers ran cleanly, in lap order.

    Laps only one of them has — a lap where just one pitted, or ran behind the safety
    car — have no meaningful delta, so they are left out entirely rather than compared
    against a neighbouring lap.
    """
    first_by_lap = dict(zip(first["lap_numbers"], first["lap_times"]))
    second_by_lap = dict(zip(second["lap_numbers"], second["lap_times"]))
    shared = sorted(set(first_by_lap) & set(second_by_lap))
    return [(lap, second_by_lap[lap] - first_by_lap[lap]) for lap in shared]


def make_comparison_lap_time_graph(series: list[dict[str, object]]) -> Text:
    """Per-lap delta between two drivers, coloured by who was quicker on that lap.

    Overlaying both drivers' absolute lap times does not work in a terminal: they are
    usually within a second or two of each other while the y axis has to span several,
    so the two series land on the same rows. Plotting the difference gives the whole
    axis to the quantity being compared.
    """
    if len(series) < 2:
        return Text("Two drivers are needed for a comparison.")

    first, second = series[0], series[1]
    deltas = build_lap_deltas(first, second)
    if not deltas:
        return Text("No laps in common to compare.")

    lap_numbers = [lap for lap, _ in deltas]
    values = [delta for _, delta in deltas]

    plt.clear_figure()
    y_ticks = make_y_ticks(min(values), max(values))
    plt.plotsize(58, max(14, min(26, len(y_ticks) * 2)))
    plt.theme("clear")
    plt.title(f"Lap delta: {second['label']} minus {first['label']}")
    plt.xlabel("Lap")
    plt.ylabel("Seconds")
    plt.ylim(y_ticks[0], y_ticks[-1])
    plt.yticks(y_ticks)
    plt.xticks(make_lap_ticks(lap_numbers))
    plt.hline(0)

    # Split by sign so each point carries the colour of whoever was quicker that lap.
    # The delta is second minus first, so a negative delta is a lap second won.
    for entry, keep_negative in ((second, True), (first, False)):
        points = [
            (lap, delta)
            for lap, delta in deltas
            if (delta < 0) == keep_negative and delta != 0
        ]
        if not points:
            continue
        plt.scatter(
            [lap for lap, _ in points],
            [delta for _, delta in points],
            marker="dot",
            color=to_plot_color(str(entry["color"])),
            label=f"{entry['label']} quicker",
        )

    graph = plt.build()
    plt.clear_figure()
    return Text.from_ansi(graph)


def average_lap_time(lap_times: list[float]) -> float | None:
    if not lap_times:
        return None
    return sum(lap_times) / len(lap_times)


def compare_time_metric(
    name: str, first: float | None, second: float | None
) -> tuple[str, str, str, str]:
    delta = None
    if first is not None and second is not None:
        delta = first - second
    return (
        name,
        format_optional_lap_time(first),
        format_optional_lap_time(second),
        format_delta(delta),
    )


def build_comparison_metrics(
    first: dict[str, object], second: dict[str, object], session_type: str
) -> list[tuple[str, str, str, str]]:
    first_times = list(first["lap_times"])
    second_times = list(second["lap_times"])

    metrics = [
        (
            "Laps",
            str(first["lap_count"]),
            str(second["lap_count"]),
            f"{int(first['lap_count']) - int(second['lap_count']):+d}",
        ),
        # Every timing row below is built from these laps, not from the Laps row above.
        (
            "Clean laps",
            str(len(first_times)),
            str(len(second_times)),
            f"{len(first_times) - len(second_times):+d}",
        ),
        compare_time_metric(
            "Fastest",
            min(first_times) if first_times else None,
            min(second_times) if second_times else None,
        ),
        compare_time_metric(
            "Slowest",
            max(first_times) if first_times else None,
            max(second_times) if second_times else None,
        ),
        compare_time_metric(
            "Average", average_lap_time(first_times), average_lap_time(second_times)
        ),
        (
            "Stops",
            str(first["stops"]),
            str(second["stops"]),
            f"{int(first['stops']) - int(second['stops']):+d}",
        ),
        (
            "Compounds",
            ", ".join(first["compounds"]) or "None",
            ", ".join(second["compounds"]) or "None",
            "",
        ),
    ]

    first_qualifying = first.get("qualifying")
    second_qualifying = second.get("qualifying")
    if session_type in QUALIFYING_SESSION_TYPES and first_qualifying and second_qualifying:
        metrics.append(
            (
                "Delta to pole",
                str(first_qualifying["delta_to_pole"]),
                str(second_qualifying["delta_to_pole"]),
                "",
            )
        )
        for first_sector, second_sector in zip(
            first_qualifying["sectors"], second_qualifying["sectors"]
        ):
            metrics.append(
                compare_time_metric(
                    str(first_sector["name"]),
                    first_sector.get("seconds"),
                    second_sector.get("seconds"),
                )
            )
    return metrics


def build_qualifying_details(laps: object, driver_laps: object) -> dict[str, object] | None:
    timed_laps = laps.dropna(subset=["LapTime"])
    driver_timed_laps = driver_laps.dropna(subset=["LapTime"])
    if timed_laps.empty or driver_timed_laps.empty:
        return None

    pole_lap = timed_laps.loc[timed_laps["LapTime"].idxmin()]
    driver_best_lap = driver_timed_laps.loc[driver_timed_laps["LapTime"].idxmin()]
    pole_lap_time = time_to_seconds(pole_lap.get("LapTime"))
    driver_lap_time = time_to_seconds(driver_best_lap.get("LapTime"))
    if pole_lap_time is None or driver_lap_time is None:
        return None

    sectors: list[dict[str, str]] = []
    for name, column in (
        ("S1", "Sector1Time"),
        ("S2", "Sector2Time"),
        ("S3", "Sector3Time"),
    ):
        driver_sector = time_to_seconds(driver_best_lap.get(column))
        pole_sector = time_to_seconds(pole_lap.get(column))
        sector_delta = None
        if driver_sector is not None and pole_sector is not None:
            sector_delta = driver_sector - pole_sector
        sectors.append(
            {
                "name": name,
                "seconds": driver_sector,
                "time": format_optional_lap_time(driver_sector),
                "delta_to_pole": format_delta(sector_delta),
            }
        )

    return {
        "lap_time_seconds": driver_lap_time,
        "lap_time": format_lap_time(driver_lap_time),
        "delta_to_pole": format_delta(driver_lap_time - pole_lap_time),
        "sectors": sectors,
    }


def select_clean_laps(laps: "pd.DataFrame") -> "pd.DataFrame":
    """Laps that represent pure pace: timed, not in or out of the pits, run at green flag.

    Equivalent to FastF1's ``pick_wo_box().pick_track_status("4567", how="none")`` but
    written against the columns, so it also works on a plain DataFrame in the tests.
    A single out-lap is enough to stretch the graph's y axis over 20 seconds and flatten
    every racing lap onto one row, which is what this exists to prevent.
    """
    keep = laps["LapTime"].notna()
    for column in ("PitInTime", "PitOutTime"):
        if column in laps:
            keep &= laps[column].isna()
    if "TrackStatus" in laps:
        status = laps["TrackStatus"].fillna("").astype(str)
        keep &= ~status.apply(lambda value: any(flag in value for flag in EXCLUDED_TRACK_STATUS))
    return laps[keep]


def extract_driver_details(
    session: fastf1.core.Session, session_type: str, driver_number: str
) -> dict[str, object]:
    laps = session.laps[session.laps["DriverNumber"].astype(str) == driver_number]
    clean = select_clean_laps(laps)

    lap_times = [float(lap_time.total_seconds()) for lap_time in clean["LapTime"]]
    if "LapNumber" in clean:
        lap_numbers = [int(number) for number in clean["LapNumber"]]
    else:
        lap_numbers = list(range(1, len(lap_times) + 1))

    compounds = [str(compound) for compound in laps["Compound"].dropna().unique().tolist()]
    stops = int(laps["PitInTime"].notna().sum()) if "PitInTime" in laps else 0

    details: dict[str, object] = {
        "lap_count": int(len(laps)),
        "lap_numbers": lap_numbers,
        "lap_times": lap_times,
        "stops": stops,
        "compounds": compounds,
    }
    if session_type in QUALIFYING_SESSION_TYPES:
        qualifying = build_qualifying_details(session.laps, laps)
        if qualifying is not None:
            details["qualifying"] = qualifying
    return details


def load_driver_details(
    year: int, event_name: str, session_type: str, driver_number: str
) -> dict[str, object]:
    session = fastf1.get_session(year, event_name, session_type)
    session.load(telemetry=False, weather=False, messages=False)
    return extract_driver_details(session, session_type, driver_number)


def load_comparison_details(
    year: int, event_name: str, session_type: str, driver_numbers: list[str]
) -> list[dict[str, object]]:
    session = fastf1.get_session(year, event_name, session_type)
    session.load(telemetry=False, weather=False, messages=False)
    return [
        extract_driver_details(session, session_type, driver_number)
        for driver_number in driver_numbers
    ]


def has_official_classification(results: object) -> bool:
    """Report whether a results table carries a usable official classification.

    The session type does not say which ranking path applies, the data does.
    Practice and sprint qualifying both come back with a full set of driver rows
    whose position column is entirely empty, and so does any session that has run
    but has not been classified yet. Anything with at least one real position is
    treated as classified, so a partly classified session still uses its own
    positions.
    """
    if results is None or len(results) == 0:
        return False
    if "Position" not in results:
        return False
    return any(to_position(value) != "" for value in results["Position"].tolist())


def load_results(year: int, event_name: str, session_type: str) -> list[dict[str, object]]:
    session = fastf1.get_session(year, event_name, session_type)
    session.load(telemetry=False, weather=False, messages=False)

    if not has_official_classification(session.results):
        return load_best_lap_results(session)

    results = session.results.fillna("")

    rows: list[dict[str, object]] = []
    for _, result in results.iterrows():
        team_name = str(result.get("TeamName", ""))
        rows.append(
            {
                "position": to_position(result.get("Position", "")),
                "number": result.get("DriverNumber", ""),
                "driver_number": str(result.get("DriverNumber", "")),
                "driver": result.get("FullName", ""),
                "abbreviation": str(result.get("Abbreviation", "") or result.get("DriverNumber", "")),
                "team": team_name,
                "team_color": get_result_team_color(team_name, session),
                "status": result.get("Status", ""),
                "time": format_qualifying_time(result)
                if session_type in QUALIFYING_SESSION_TYPES
                else format_result_time(result.get("Time", "")),
            }
        )
    return rows


def load_best_lap_results(session: fastf1.core.Session) -> list[dict[str, object]]:
    """Rank the field by each driver's best lap, for sessions with no classification."""
    results = session.results.fillna("")
    driver_info_by_number = {
        str(result.get("DriverNumber", "")): result
        for _, result in results.iterrows()
    }
    timed_laps = session.laps.dropna(subset=["LapTime"])
    best_laps = timed_laps.loc[timed_laps.groupby("DriverNumber")["LapTime"].idxmin()]
    best_laps = best_laps.sort_values("LapTime")

    rows: list[dict[str, object]] = []
    for position, (_, lap) in enumerate(best_laps.iterrows(), start=1):
        driver_number = str(lap.get("DriverNumber", ""))
        driver_info = driver_info_by_number.get(driver_number)
        team_name = str(lap.get("Team", ""))
        driver_name = str(lap.get("Driver", ""))
        abbreviation = str(lap.get("Driver", "") or driver_number)
        if driver_info is not None:
            team_name = str(driver_info.get("TeamName", team_name))
            driver_name = str(driver_info.get("FullName", driver_name))
            abbreviation = str(driver_info.get("Abbreviation", abbreviation) or abbreviation)

        rows.append(
            {
                "position": position,
                "number": driver_number,
                "driver_number": driver_number,
                "driver": driver_name,
                "abbreviation": abbreviation,
                "team": team_name,
                "team_color": get_result_team_color(team_name, session),
                "status": "Best lap",
                "time": format_practice_lap_time(lap.get("LapTime", "")),
            }
        )
    return rows


def official_circuit_length(circuit_key: object) -> float | None:
    """The published length of a circuit, or None if we don't have it on file."""
    if circuit_key is None:
        return None
    try:
        key = int(circuit_key)
    except (TypeError, ValueError):
        return None
    length = OFFICIAL_CIRCUIT_LENGTHS.get(key)
    return float(length) if length is not None else None


def select_lap_position_samples(position_data: "pd.DataFrame", lap: object) -> "pd.DataFrame":
    """One lap's position samples, with the API's no-fix placeholders removed."""
    samples = position_data
    start = lap.get("LapStartTime")
    lap_time = lap.get("LapTime")
    if start is not None and start == start and lap_time is not None and lap_time == lap_time:
        samples = samples[(samples["Time"] >= start) & (samples["Time"] <= start + lap_time)]
    # (0, 0) is what the position API sends when it has no fix for the car. Those
    # samples are not on the track, and keeping them drags the map toward the origin.
    return samples[(samples["X"] != 0) | (samples["Y"] != 0)]


def rotate_points(points: list[tuple[float, float]], degrees: float) -> list[tuple[float, float]]:
    """Turn the coordinate system to match the orientation of the official circuit map."""
    angle = math.radians(degrees)
    cos = math.cos(angle)
    sin = math.sin(angle)
    return [(x * cos - y * sin, x * sin + y * cos) for x, y in points]


def assign_sectors(times: list[object], lap: object) -> list[int]:
    """Label each sample with the official sector it was recorded in.

    Split on time rather than on distance: the position-only path carries no
    Distance column, and the sector session times say exactly when the driver
    crossed each line. A lap missing them stays in one sector rather than failing.
    """
    first = lap.get("Sector1SessionTime")
    second = lap.get("Sector2SessionTime")
    if first is None or first != first or second is None or second != second:
        return [1] * len(times)
    return [1 if time < first else (2 if time < second else 3) for time in times]


def polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(start, end) for start, end in zip(points, points[1:]))


def pick_geometry_lap(laps: "pd.DataFrame", position_data: dict) -> tuple[object, object]:
    """The lap whose position trace covers the circuit best, and that driver's samples.

    Deliberately not the fastest lap. In a race the position feed updates any one
    car far less often than in qualifying: at the 2026 Hungarian Grand Prix the
    fastest lap carried 26 distinct positions where another car's lap carried 341,
    which draws the circuit as two dozen scattered dots. The shape of a circuit
    does not depend on who drove it, so the trace with the most distinct positions
    wins.
    """
    best: tuple[object, object] = (None, None)
    best_score = 0

    for driver_number, driver_laps in laps.groupby("DriverNumber"):
        if not driver_laps["LapTime"].notna().any():
            continue
        samples = position_data.get(str(driver_number))
        if samples is None or samples.empty:
            continue

        lap = driver_laps.loc[driver_laps["LapTime"].idxmin()]
        selected = select_lap_position_samples(samples, lap)
        score = len(set(zip(selected["X"], selected["Y"])))
        if score > best_score:
            best = (lap, samples)
            best_score = score

    return best


def extract_track_map(
    position_data: "pd.DataFrame",
    lap: object,
    *,
    rotation: float = 0.0,
    corner_count: int = 0,
    circuit_key: object = None,
    name: str = "",
    location: str = "",
) -> dict[str, object]:
    """Reduce one lap's position data to a plain description of the circuit.

    Everything FastF1-shaped stops here: the result is points, sectors and text,
    so the renderer never has to know where any of it came from.
    """
    samples = select_lap_position_samples(position_data, lap)
    points = rotate_points(
        [
            (float(x) / POSITION_UNITS_PER_METRE, float(y) / POSITION_UNITS_PER_METRE)
            for x, y in zip(samples["X"], samples["Y"])
        ],
        rotation,
    )
    sectors = assign_sectors(list(samples["Time"]), lap)
    official = official_circuit_length(circuit_key)

    return {
        "name": name,
        "location": location,
        "corner_count": int(corner_count),
        "points": points,
        "sectors": sectors,
        "length_m": float(official if official is not None else polyline_length(points)),
        "length_is_official": official is not None,
    }


def project_track(
    points: list[tuple[float, float]], sectors: list[int], width: int, height: int
) -> list[Text]:
    """Draw the lap into a character grid, keeping the circuit's real proportions.

    Both axes take the same scale — that is what makes the drawn shape the
    circuit's own shape — but the vertical span is converted into character-cell
    units first, because a cell is about twice as tall as it is wide. Without that
    conversion the track comes out squashed to half its true height.

    The track therefore rarely fills the grid: an equal-aspect square circuit in
    the 36x12 map region occupies about 24 columns. That whitespace is the price of
    an honest shape, not a bug to be fixed by stretching.
    """
    cells: list[list[int | None]] = [[None] * width for _ in range(height)]

    if points:
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        left, bottom = min(xs), min(ys)
        span_x = max(xs) - left
        span_y = max(ys) - bottom
        if span_x <= 0 and span_y <= 0:
            # Every sample in the same place; a scale of zero puts the one
            # marker in the middle instead of dividing by nothing.
            scale = 0.0
        else:
            scale = min(
                (width - 1) / span_x if span_x > 0 else math.inf,
                (height - 1) / (span_y / CHARACTER_ASPECT) if span_y > 0 else math.inf,
            )
        offset_x = (width - 1 - span_x * scale) / 2
        offset_y = (height - 1 - span_y * scale / CHARACTER_ASPECT) / 2

        for (x, y), sector in zip(points, sectors):
            column = int(round((x - left) * scale + offset_x))
            row = int(round((height - 1) - ((y - bottom) * scale / CHARACTER_ASPECT + offset_y)))
            if 0 <= row < height and 0 <= column < width:
                cells[row][column] = sector

    lines: list[Text] = []
    for row_cells in cells:
        line = Text()
        for sector in row_cells:
            if sector is None:
                line.append(" ")
            else:
                line.append(TRACK_MARKER, style=SECTOR_COLORS.get(sector, ""))
        lines.append(line)
    return lines


def format_track_length(track: dict[str, object]) -> str:
    """Say the length, and say plainly when it is only a measurement of the trace."""
    length = float(track.get("length_m", 0.0) or 0.0)
    if length <= 0:
        return "Length   unknown"
    if track.get("length_is_official"):
        return f"Length   {length:.0f} m"
    return f"Length   ≈ {length:.0f} m"


def describe_track(track: dict[str, object], height: int) -> list[Text]:
    """The text column: what circuit this is, how long, and what the colours mean."""
    lines: list[Text] = []
    name = str(track.get("name", ""))
    location = str(track.get("location", ""))
    if name:
        lines.append(Text(name, style="bold"))
    if location:
        lines.append(Text(location, style="dim"))
    lines.append(Text(""))
    lines.append(Text(format_track_length(track)))
    corner_count = int(track.get("corner_count", 0) or 0)
    if corner_count:
        lines.append(Text(f"Corners  {corner_count}"))
    lines.append(Text(""))
    for sector in (1, 2, 3):
        entry = Text(LEGEND_MARKER + " ", style=SECTOR_COLORS[sector])
        entry.append(f"Sector {sector}")
        lines.append(entry)

    # Hard truncate: a long circuit name that wraps would push every following
    # row down and tear the map away from its own description.
    for line in lines:
        line.truncate(TRACK_TEXT_WIDTH, overflow="ellipsis")

    lines = lines[:height]
    lines.extend(Text("") for _ in range(height - len(lines)))
    return lines


def render_track_panel(track: dict[str, object], width: int, height: int) -> Text:
    """The whole panel: the circuit on the left, what it is on the right."""
    map_width = max(width - TRACK_TEXT_WIDTH - TRACK_GUTTER, 1)
    map_lines = project_track(
        list(track.get("points", [])), list(track.get("sectors", [])), map_width, height
    )
    text_lines = describe_track(track, height)

    panel = Text()
    for index, (map_line, text_line) in enumerate(zip(map_lines, text_lines)):
        panel.append_text(map_line)
        panel.append(" " * TRACK_GUTTER)
        panel.append_text(text_line)
        if index < height - 1:
            panel.append("\n")
    return panel


def format_track_error(exc: Exception) -> str:
    """Say why there is no circuit yet, and that it is not the user's move."""
    return f"No track map yet: {exc}.\nIt will load with the next session."


def format_circuit_location(event: object, name: str = "") -> str:
    """Where the circuit is, without repeating what it is already called.

    Several circuits share their name with their town — Spa-Francorchamps and
    Monte Carlo among them — and printing both reads as a stutter.
    """
    location = str(event.get("Location", "") or "")
    country = str(event.get("Country", "") or "")
    parts = [location, country] if location and location != name else [country]
    return ", ".join(part for part in parts if part)


def load_track_map(year: int, event_name: str, session_type: str) -> dict[str, object]:
    """Fetch one weekend's circuit geometry from a session that has run.

    Deliberately avoids ``Session.get_telemetry()``. For the 2026 season FastF1's
    car-data parser raises on every session, and because car data is loaded first
    that failure aborts the whole telemetry load and takes the position data with
    it. The position data itself is perfectly good, so it is read straight from
    the API layer instead — that is the only reason this panel works this season.
    """
    session = fastf1.get_session(year, event_name, session_type)
    session.load(telemetry=False, weather=False, messages=False)

    if session.laps.empty or not session.laps["LapTime"].notna().any():
        raise ValueError("no completed lap to draw the circuit from")

    position_data = fastf1_api.position_data(session.api_path)
    lap, samples = pick_geometry_lap(session.laps, position_data)
    if lap is None or samples is None:
        raise ValueError("no position data for any completed lap")

    circuit_info = session.get_circuit_info()
    circuit = ((session.session_info or {}).get("Meeting", {}) or {}).get("Circuit", {}) or {}
    name = str(circuit.get("ShortName", "") or event_name)
    return extract_track_map(
        samples,
        lap,
        rotation=float(getattr(circuit_info, "rotation", 0.0) or 0.0),
        corner_count=len(circuit_info.corners) if circuit_info is not None else 0,
        circuit_key=circuit.get("Key"),
        name=name,
        location=format_circuit_location(session.event, name),
    )


def main() -> None:
    F1ResultsApp().run()


if __name__ == "__main__":
    main()
