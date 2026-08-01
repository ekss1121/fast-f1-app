from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import date

import fastf1
import pandas as pd
import plotext as plt
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

    #log_title {
        height: 1;
        padding: 0 1;
    }

    #logs {
        height: 12;
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


def make_lap_time_graph(lap_times: list[float]) -> str:
    if not lap_times:
        return "No lap times available."

    plt.clear_figure()
    y_ticks = make_y_ticks(min(lap_times), max(lap_times))

    plt.plotsize(58, max(14, min(26, len(y_ticks) * 2)))
    plt.theme("clear")
    plt.title("Lap times")
    plt.xlabel("Lap")
    plt.ylabel("Seconds")
    plt.ylim(y_ticks[0], y_ticks[-1])
    plt.yticks(y_ticks)
    lap_numbers = list(range(1, len(lap_times) + 1))
    tick_step = max(1, len(lap_numbers) // 4)
    ticks = list(range(1, len(lap_numbers) + 1, tick_step))
    if ticks[-1] != len(lap_numbers):
        ticks.append(len(lap_numbers))
    plt.xticks(ticks)
    plt.scatter(lap_numbers, lap_times, marker="dot")
    graph = plt.uncolorize(plt.build())
    plt.clear_figure()
    return graph


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
    text.append("Lap time graph:\n" + make_lap_time_graph(lap_times) + "\n")
    text.append("Laps: " + str(details["lap_count"]) + " | Fastest: " + fastest + " | Slowest: " + slowest)
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


def make_comparison_lap_time_graph(series: list[dict[str, object]]) -> Text:
    plotted = [entry for entry in series if entry["lap_times"]]
    if not plotted:
        return Text("No lap times available.")

    all_times = [lap_time for entry in plotted for lap_time in entry["lap_times"]]
    longest = max(len(entry["lap_times"]) for entry in plotted)

    plt.clear_figure()
    y_ticks = make_y_ticks(min(all_times), max(all_times))
    plt.plotsize(58, max(14, min(26, len(y_ticks) * 2)))
    plt.theme("clear")
    plt.title("Lap times")
    plt.xlabel("Lap")
    plt.ylabel("Seconds")
    plt.ylim(y_ticks[0], y_ticks[-1])
    plt.yticks(y_ticks)

    tick_step = max(1, longest // 4)
    ticks = list(range(1, longest + 1, tick_step))
    if ticks[-1] != longest:
        ticks.append(longest)
    plt.xticks(ticks)

    for entry in plotted:
        lap_times = entry["lap_times"]
        plt.scatter(
            list(range(1, len(lap_times) + 1)),
            lap_times,
            marker="dot",
            color=to_plot_color(str(entry["color"])),
            label=str(entry["label"]),
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


def extract_driver_details(
    session: fastf1.core.Session, session_type: str, driver_number: str
) -> dict[str, object]:
    laps = session.laps[session.laps["DriverNumber"].astype(str) == driver_number]

    lap_times: list[float] = []
    for lap_time in laps["LapTime"].dropna().tolist():
        lap_times.append(float(lap_time.total_seconds()))

    compounds = [str(compound) for compound in laps["Compound"].dropna().unique().tolist()]
    stops = int(laps["PitInTime"].notna().sum()) if "PitInTime" in laps else 0

    details: dict[str, object] = {
        "lap_count": int(len(laps)),
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


def main() -> None:
    F1ResultsApp().run()


if __name__ == "__main__":
    main()
