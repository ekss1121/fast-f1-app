import unittest

import pandas as pd

from textual.widgets import Select

from fast_f1_app import (
    build_comparison_metrics,
    build_lap_deltas,
    build_qualifying_details,
    build_weekend_plan,
    extract_driver_details,
    extract_track_map,
    F1ResultsApp,
    format_compounds,
    format_empty_season_message,
    format_lap_time,
    format_not_started_message,
    format_not_started_status,
    format_position_cell,
    format_qualifying_time,
    format_result_time,
    format_schedule_error,
    format_session_error,
    format_circuit_location,
    format_session_start,
    format_track_error,
    has_official_classification,
    make_comparison_lap_time_graph,
    make_lap_time_graph,
    make_y_ticks,
    pick_geometry_lap,
    render_driver_details,
    render_track_panel,
    resolve_comparison_colors,
    select_clean_laps,
    to_plot_color,
    to_position,
    TRACK_CONTENT_HEIGHT,
    TRACK_CONTENT_WIDTH,
    TRACK_GUTTER,
    TRACK_TEXT_WIDTH,
)


class FormattingTests(unittest.TestCase):
    def test_format_result_time_strips_day_prefix(self):
        self.assertEqual(format_result_time("0 days 01:23:45.678000"), "01:23:45.678000")

    def test_format_lap_time_formats_seconds_as_minutes(self):
        self.assertEqual(format_lap_time(83.456), "1:23.456")

    def test_format_qualifying_time_uses_fastest_available_session_time(self):
        result = {"Q1": "0 days 01:25.000000", "Q2": "0 days 01:24.000000", "Q3": "NaT"}
        self.assertEqual(format_qualifying_time(result), "01:24.000000")

    def test_make_lap_time_graph_uses_plotext_output(self):
        graph = make_lap_time_graph([1, 2, 3], [83.4, 82.9, 83.1])

        self.assertIn("Lap times", graph)
        self.assertIn("Lap", graph)
        self.assertNotIn(chr(27), graph)

    def test_make_lap_time_graph_plots_one_marker_per_lap(self):
        lap_times = [83.4, 92.1, 85.0, 110.6, 84.2]
        graph = make_lap_time_graph([1, 2, 3, 4, 5], lap_times)

        self.assertEqual(graph.count("•"), len(lap_times))

    def test_make_lap_time_graph_labels_the_real_lap_numbers(self):
        # Laps dropped by the clean-lap filter must not renumber the ones that remain.
        graph = make_lap_time_graph([12, 13, 14, 40], [83.4, 82.9, 83.1, 82.8])

        self.assertIn("12", graph)
        self.assertIn("40", graph)


class YTickTests(unittest.TestCase):
    def test_ticks_are_evenly_spaced_and_cover_the_data_range(self):
        for minimum, maximum in ((82.67, 125.94), (80.1, 80.9), (83.4, 95.2)):
            with self.subTest(minimum=minimum, maximum=maximum):
                ticks = make_y_ticks(minimum, maximum)
                steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}

                self.assertEqual(len(steps), 1)
                self.assertLessEqual(ticks[0], minimum)
                self.assertGreaterEqual(ticks[-1], maximum)

    def test_ticks_use_a_round_step_instead_of_one_per_tenth(self):
        self.assertEqual(make_y_ticks(82.67, 125.94)[:3], [80, 85, 90])

    def test_ticks_handle_a_single_lap_time(self):
        ticks = make_y_ticks(83.4, 83.4)

        self.assertGreater(len(ticks), 1)
        self.assertLessEqual(ticks[0], 83.4)
        self.assertGreaterEqual(ticks[-1], 83.4)


class ResultDecorationTests(unittest.TestCase):
    def test_format_position_cell_adds_medals_for_podium(self):
        self.assertEqual(format_position_cell(1).plain, "🥇 1")
        self.assertEqual(format_position_cell(2).plain, "🥈 2")
        self.assertEqual(format_position_cell(3).plain, "🥉 3")
        self.assertEqual(format_position_cell(4).plain, "4")

    def test_format_compounds_adds_tire_emoji_and_styles_known_compounds(self):
        compounds = format_compounds(["SOFT", "MEDIUM", "HARD"])

        self.assertEqual(compounds.plain, "🛞 SOFT, 🛞 MEDIUM, 🛞 HARD")
        self.assertTrue(any(str(span.style) == "red" for span in compounds.spans))
        self.assertTrue(any(str(span.style) == "yellow" for span in compounds.spans))
        self.assertTrue(any(str(span.style) == "white" for span in compounds.spans))

    def test_render_driver_details_includes_compound_decoration(self):
        details = render_driver_details(
            {"driver": "Max Verstappen", "driver_number": "1", "team": "Red Bull Racing"},
            {
                "stops": 1,
                "compounds": ["SOFT"],
                "lap_numbers": [1],
                "lap_times": [83.4],
                "lap_count": 1,
            },
        )

        self.assertIn("🛞 SOFT", details.plain)

    def test_render_driver_details_says_how_many_laps_were_excluded(self):
        details = render_driver_details(
            {"driver": "Max Verstappen", "driver_number": "1", "team": "Red Bull Racing"},
            {
                "stops": 1,
                "compounds": ["SOFT"],
                "lap_numbers": [1, 2],
                "lap_times": [83.4, 83.9],
                "lap_count": 5,
            },
        )

        self.assertIn("2 clean laps", details.plain)
        self.assertIn("3 excluded", details.plain)

    def test_build_qualifying_details_compares_driver_best_lap_to_pole(self):
        laps = pd.DataFrame(
            {
                "DriverNumber": ["1", "44"],
                "LapTime": pd.to_timedelta([80.0, 80.5], unit="s"),
                "Sector1Time": pd.to_timedelta([20.0, 20.1], unit="s"),
                "Sector2Time": pd.to_timedelta([30.0, 30.2], unit="s"),
                "Sector3Time": pd.to_timedelta([30.0, 30.2], unit="s"),
            }
        )

        details = build_qualifying_details(laps, laps[laps["DriverNumber"] == "44"])

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details["lap_time"], "1:20.500")
        self.assertEqual(details["delta_to_pole"], "+0.500")
        self.assertEqual(details["sectors"][0]["time"], "0:20.100")
        self.assertEqual(details["sectors"][0]["delta_to_pole"], "+0.100")

    def test_render_driver_details_includes_qualifying_sector_delta(self):
        details = render_driver_details(
            {"driver": "Lewis Hamilton", "driver_number": "44", "team": "Ferrari"},
            {
                "stops": 0,
                "compounds": ["SOFT"],
                "lap_numbers": [1],
                "lap_times": [80.5],
                "lap_count": 1,
                "qualifying": {
                    "lap_time": "1:20.500",
                    "delta_to_pole": "+0.500",
                    "sectors": [
                        {"name": "S1", "time": "0:20.100", "delta_to_pole": "+0.100"},
                    ],
                },
            },
        )

        self.assertIn("Qualifying best lap: 1:20.500", details.plain)
        self.assertIn("S1: 0:20.100 (+0.100)", details.plain)


class CleanLapTests(unittest.TestCase):
    def make_laps(self, **overrides):
        laps = {
            "LapNumber": [1.0, 2.0, 3.0, 4.0, 5.0],
            "LapTime": pd.to_timedelta([83.4, 83.9, 102.3, 84.1, 83.7], unit="s"),
            "PitInTime": pd.to_timedelta([None, None, None, None, None]),
            "PitOutTime": pd.to_timedelta([None, None, None, None, None]),
            "TrackStatus": ["1", "1", "1", "1", "1"],
        }
        laps.update(overrides)
        return pd.DataFrame(laps)

    def kept(self, laps):
        return [int(number) for number in select_clean_laps(laps)["LapNumber"]]

    def test_untimed_laps_are_dropped(self):
        laps = self.make_laps(
            LapTime=pd.to_timedelta([83.4, None, 102.3, 84.1, 83.7], unit="s")
        )

        self.assertEqual(self.kept(laps), [1, 3, 4, 5])

    def test_the_in_lap_and_the_out_lap_are_dropped(self):
        laps = self.make_laps(
            PitInTime=pd.to_timedelta([None, 90.0, None, None, None], unit="s"),
            PitOutTime=pd.to_timedelta([None, None, 91.0, None, None], unit="s"),
        )

        self.assertEqual(self.kept(laps), [1, 4, 5])

    def test_safety_car_and_red_flag_laps_are_dropped(self):
        # 4 safety car, 5 safety car ending, 6/7 virtual safety car, 1 green.
        laps = self.make_laps(TrackStatus=["1", "4", "6", "15", "1"])

        self.assertEqual(self.kept(laps), [1, 5])

    def test_a_green_flag_lap_with_a_multi_digit_status_is_kept(self):
        laps = self.make_laps(TrackStatus=["1", "12", "1", "1", "1"])

        self.assertEqual(self.kept(laps), [1, 2, 3, 4, 5])

    def test_lap_data_without_the_optional_columns_still_filters_on_lap_time(self):
        laps = pd.DataFrame(
            {
                "LapNumber": [1.0, 2.0],
                "LapTime": pd.to_timedelta([83.4, None], unit="s"),
            }
        )

        self.assertEqual(self.kept(laps), [1])

    def test_extract_driver_details_reports_real_lap_numbers_for_clean_laps(self):
        laps = self.make_laps(
            DriverNumber=["44", "44", "44", "44", "44"],
            Compound=["SOFT", "SOFT", "SOFT", "MEDIUM", "MEDIUM"],
            TrackStatus=["1", "4", "1", "1", "1"],
        )

        details = extract_driver_details(LoadedSession(laps), "R", "44")

        self.assertEqual(details["lap_numbers"], [1, 3, 4, 5])
        self.assertEqual(details["lap_count"], 5)
        self.assertEqual(len(details["lap_times"]), 4)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.first = {
            "lap_count": 58,
            "lap_numbers": [1, 2, 3],
            "lap_times": [83.4, 84.1, 85.0],
            "stops": 1,
            "compounds": ["MEDIUM", "HARD"],
        }
        self.second = {
            "lap_count": 57,
            "lap_numbers": [1, 2, 3],
            "lap_times": [83.9, 83.5, 86.2],
            "stops": 2,
            "compounds": ["SOFT"],
        }

    def metrics_by_name(self, session_type="R"):
        return {
            metric[0]: metric[1:]
            for metric in build_comparison_metrics(self.first, self.second, session_type)
        }

    def test_teammates_fall_back_to_contrasting_colors(self):
        self.assertEqual(resolve_comparison_colors("#00d2be", "#00D2BE"), ("cyan", "magenta"))

    def test_different_teams_keep_their_team_colors(self):
        self.assertEqual(
            resolve_comparison_colors("#6692ff", "#ff8700"), ("#6692ff", "#ff8700")
        )

    def test_hex_team_color_converts_to_rgb_for_plotext(self):
        self.assertEqual(to_plot_color("#6692ff"), (102, 146, 255))
        self.assertEqual(to_plot_color("cyan"), "cyan")

    def test_metrics_report_both_drivers_and_the_delta(self):
        metrics = self.metrics_by_name()

        self.assertEqual(metrics["Fastest"], ("1:23.400", "1:23.500", "-0.100"))
        self.assertEqual(metrics["Laps"], ("58", "57", "+1"))
        self.assertEqual(metrics["Stops"], ("1", "2", "-1"))
        self.assertEqual(metrics["Compounds"], ("MEDIUM, HARD", "SOFT", ""))

    def give_both_drivers_qualifying_details(self):
        qualifying = {
            "lap_time_seconds": 80.5,
            "lap_time": "1:20.500",
            "delta_to_pole": "+0.500",
            "sectors": [{"name": "S1", "seconds": 20.1, "time": "0:20.100"}],
        }
        self.first["qualifying"] = qualifying
        self.second["qualifying"] = {**qualifying, "delta_to_pole": "+0.900"}
        self.second["qualifying"]["sectors"] = [
            {"name": "S1", "seconds": 20.4, "time": "0:20.400"}
        ]

    def test_qualifying_comparison_adds_sector_deltas(self):
        self.give_both_drivers_qualifying_details()

        metrics = self.metrics_by_name("Q")

        self.assertEqual(metrics["Delta to pole"], ("+0.500", "+0.900", ""))
        self.assertEqual(metrics["S1"], ("0:20.100", "0:20.400", "-0.300"))

    def test_sprint_qualifying_comparison_adds_sector_deltas_too(self):
        self.give_both_drivers_qualifying_details()

        metrics = self.metrics_by_name("SQ")

        self.assertEqual(metrics["Delta to pole"], ("+0.500", "+0.900", ""))
        self.assertEqual(metrics["S1"], ("0:20.100", "0:20.400", "-0.300"))

    def test_qualifying_metrics_are_omitted_for_a_race(self):
        self.assertNotIn("S1", self.metrics_by_name())

    def compare_entry(self, label, detail, color):
        return {
            "label": label,
            "lap_numbers": detail["lap_numbers"],
            "lap_times": detail["lap_times"],
            "color": color,
        }

    def test_lap_deltas_are_second_minus_first_per_lap(self):
        deltas = build_lap_deltas(self.first, self.second)

        self.assertEqual([lap for lap, _ in deltas], [1, 2, 3])
        self.assertAlmostEqual(deltas[0][1], 0.5)
        self.assertAlmostEqual(deltas[1][1], -0.6)

    def test_lap_deltas_only_cover_laps_both_drivers_ran_cleanly(self):
        # A lap only one of them has -- one pitted, or sat behind the safety car --
        # has nothing to compare against and must not shift the others along.
        first = {"lap_numbers": [1, 2, 5], "lap_times": [83.4, 84.1, 85.0]}
        second = {"lap_numbers": [2, 5, 6], "lap_times": [83.9, 83.5, 86.2]}

        deltas = build_lap_deltas(first, second)

        self.assertEqual([lap for lap, _ in deltas], [2, 5])

    def test_comparison_graph_colors_each_lap_by_whoever_was_quicker(self):
        graph = make_comparison_lap_time_graph(
            [
                self.compare_entry("RUS", self.first, "#00d2be"),
                self.compare_entry("NOR", self.second, "#ff8700"),
            ]
        )
        styles = {str(span.style) for span in graph.spans}

        # Laps 1 and 3 went to RUS, lap 2 to NOR, so both colors appear.
        self.assertIn("#00d2be", styles)
        self.assertIn("#ff8700", styles)
        self.assertIn("RUS quicker", graph.plain)
        self.assertIn("NOR quicker", graph.plain)

    def test_comparison_graph_titles_the_direction_of_the_delta(self):
        graph = make_comparison_lap_time_graph(
            [
                self.compare_entry("RUS", self.first, "#00d2be"),
                self.compare_entry("NOR", self.second, "#ff8700"),
            ]
        )

        self.assertIn("NOR minus RUS", graph.plain)

    def test_comparison_graph_handles_a_driver_with_no_laps(self):
        graph = make_comparison_lap_time_graph(
            [
                {"label": "RUS", "lap_numbers": [1, 2], "lap_times": [83.4, 84.0], "color": "cyan"},
                {"label": "STR", "lap_numbers": [], "lap_times": [], "color": "magenta"},
            ]
        )

        self.assertEqual(graph.plain, "No laps in common to compare.")

    def test_comparison_graph_needs_two_drivers(self):
        graph = make_comparison_lap_time_graph(
            [{"label": "STR", "lap_numbers": [], "lap_times": [], "color": "cyan"}]
        )

        self.assertEqual(graph.plain, "Two drivers are needed for a comparison.")

    def test_clean_lap_count_is_reported_next_to_the_real_lap_count(self):
        metrics = self.metrics_by_name()

        self.assertEqual(metrics["Laps"], ("58", "57", "+1"))
        self.assertEqual(metrics["Clean laps"], ("3", "3", "+0"))


class PositionTests(unittest.TestCase):
    def test_missing_position_becomes_blank_instead_of_raising(self):
        self.assertEqual(to_position(""), "")
        self.assertEqual(to_position(None), "")
        self.assertEqual(to_position(3.0), 3)

    def test_blank_position_renders_as_an_empty_cell(self):
        self.assertEqual(format_position_cell(to_position("")).plain, "")


def make_results(positions):
    """Build a results table shaped like the timing provider's, with given positions."""
    return pd.DataFrame(
        {
            "DriverNumber": [str(index + 1) for index in range(len(positions))],
            "FullName": [f"Driver {index + 1}" for index in range(len(positions))],
            "Position": positions,
            "Q1": [pd.NaT] * len(positions),
            "Q2": [pd.NaT] * len(positions),
            "Q3": [pd.NaT] * len(positions),
        }
    )


class LoadedSession:
    """Stand-in for a loaded session, carrying only the lap data under test."""

    def __init__(self, laps):
        self.laps = laps


class ClassificationTests(unittest.TestCase):
    def test_populated_position_column_is_official_classification(self):
        self.assertTrue(has_official_classification(make_results([1.0, 2.0, 3.0])))

    def test_wholly_empty_position_column_is_not_official_classification(self):
        # What sprint qualifying and practice return: a full field, no positions.
        self.assertFalse(has_official_classification(make_results([float("nan")] * 22)))

    def test_partly_classified_session_keeps_its_official_positions(self):
        self.assertTrue(has_official_classification(make_results([1.0, float("nan")])))

    def test_results_without_a_position_column_are_not_classified(self):
        self.assertFalse(has_official_classification(pd.DataFrame({"DriverNumber": ["1"]})))

    def test_an_empty_results_table_is_not_classified(self):
        self.assertFalse(has_official_classification(make_results([])))


class SprintQualifyingTests(unittest.TestCase):
    def setUp(self):
        self.laps = pd.DataFrame(
            {
                "DriverNumber": ["1", "44"],
                "Driver": ["VER", "HAM"],
                "LapTime": pd.to_timedelta([80.0, 80.5], unit="s"),
                "Sector1Time": pd.to_timedelta([20.0, 20.1], unit="s"),
                "Sector2Time": pd.to_timedelta([30.0, 30.2], unit="s"),
                "Sector3Time": pd.to_timedelta([30.0, 30.2], unit="s"),
                "Compound": ["SOFT", "SOFT"],
            }
        )

    def test_sprint_qualifying_gets_the_sector_breakdown(self):
        details = extract_driver_details(LoadedSession(self.laps), "SQ", "44")

        qualifying = details["qualifying"]
        self.assertEqual(qualifying["delta_to_pole"], "+0.500")
        self.assertEqual([sector["name"] for sector in qualifying["sectors"]], ["S1", "S2", "S3"])

    def test_the_sprint_race_does_not_get_the_sector_breakdown(self):
        details = extract_driver_details(LoadedSession(self.laps), "S", "44")

        self.assertNotIn("qualifying", details)


def make_event(round_number, name, year, first_day, sessions):
    """Build one schedule row; sessions is a list of (name, day offset, hour)."""
    row = {
        "RoundNumber": round_number,
        "EventName": name,
        "EventDate": pd.Timestamp(f"{year}-{first_day}") + pd.Timedelta(days=2),
    }
    for slot in range(1, 6):
        if slot <= len(sessions):
            session_name, day_offset, hour = sessions[slot - 1]
            row[f"Session{slot}"] = session_name
            row[f"Session{slot}DateUtc"] = (
                pd.Timestamp(f"{year}-{first_day}")
                + pd.Timedelta(days=day_offset, hours=hour)
            )
        else:
            row[f"Session{slot}"] = None
            row[f"Session{slot}DateUtc"] = pd.NaT
    return row


CONVENTIONAL = [
    ("Practice 1", 0, 12),
    ("Practice 2", 0, 16),
    ("Practice 3", 1, 12),
    ("Qualifying", 1, 16),
    ("Race", 2, 14),
]
SPRINT = [
    ("Practice 1", 0, 12),
    ("Sprint Qualifying", 0, 16),
    ("Sprint", 1, 12),
    ("Qualifying", 1, 16),
    ("Race", 2, 14),
]


def make_schedule(events):
    return pd.DataFrame([{"RoundNumber": 0, "EventName": "Pre-Season Testing",
                          "EventDate": pd.Timestamp("2026-02-01"),
                          **{f"Session{s}": None for s in range(1, 6)},
                          **{f"Session{s}DateUtc": pd.NaT for s in range(1, 6)}}] + events)


class WeekendPlanTests(unittest.TestCase):
    def test_conventional_weekend_lists_five_sessions_in_schedule_order(self):
        schedule = make_schedule([make_event(1, "Australian Grand Prix", 2026, "03-06", CONVENTIONAL)])

        plan = build_weekend_plan(schedule, pd.Timestamp("2026-03-10 00:00"))

        self.assertEqual(plan["event_name"], "Australian Grand Prix")
        self.assertEqual(plan["year"], 2026)
        self.assertEqual(
            [session["code"] for session in plan["sessions"]],
            ["FP1", "FP2", "FP3", "Q", "R"],
        )
        self.assertEqual(plan["default_session"], "R")

    def test_sprint_weekend_swaps_practice_for_sprint_sessions(self):
        schedule = make_schedule([make_event(1, "Chinese Grand Prix", 2026, "03-13", SPRINT)])

        plan = build_weekend_plan(schedule, pd.Timestamp("2026-03-17 00:00"))

        codes = [session["code"] for session in plan["sessions"]]
        self.assertEqual(codes, ["FP1", "SQ", "S", "Q", "R"])
        self.assertNotIn("FP2", codes)
        self.assertNotIn("FP3", codes)

    def test_mid_weekend_marks_future_sessions_and_defaults_to_last_finished(self):
        schedule = make_schedule([make_event(1, "Australian Grand Prix", 2026, "03-06", CONVENTIONAL)])

        # Saturday evening: qualifying has run, the race has not.
        plan = build_weekend_plan(schedule, pd.Timestamp("2026-03-07 18:00"))

        started = {session["code"]: session["has_started"] for session in plan["sessions"]}
        self.assertEqual(started, {"FP1": True, "FP2": True, "FP3": True, "Q": True, "R": False})
        self.assertEqual(plan["default_session"], "Q")

    def test_between_rounds_picks_the_most_recently_started_weekend(self):
        schedule = make_schedule([
            make_event(1, "Australian Grand Prix", 2026, "03-06", CONVENTIONAL),
            make_event(2, "Chinese Grand Prix", 2026, "03-13", SPRINT),
        ])

        plan = build_weekend_plan(schedule, pd.Timestamp("2026-03-18 00:00"))

        self.assertEqual(plan["event_name"], "Chinese Grand Prix")
        self.assertEqual(plan["default_session"], "R")

    def test_an_upcoming_weekend_is_not_chosen_before_it_starts(self):
        schedule = make_schedule([
            make_event(1, "Australian Grand Prix", 2026, "03-06", CONVENTIONAL),
            make_event(2, "Chinese Grand Prix", 2026, "03-13", SPRINT),
        ])

        plan = build_weekend_plan(schedule, pd.Timestamp("2026-03-11 00:00"))

        self.assertEqual(plan["event_name"], "Australian Grand Prix")

    def test_returns_none_before_the_seasons_first_session(self):
        schedule = make_schedule([make_event(1, "Australian Grand Prix", 2026, "03-06", CONVENTIONAL)])

        self.assertIsNone(build_weekend_plan(schedule, pd.Timestamp("2026-01-15 00:00")))

    def test_previous_seasons_schedule_yields_its_final_round(self):
        # How the off-season fallback works: the same call, fed last season.
        schedule = make_schedule([
            make_event(1, "Australian Grand Prix", 2025, "03-14", CONVENTIONAL),
            make_event(24, "Abu Dhabi Grand Prix", 2025, "12-05", CONVENTIONAL),
        ])

        plan = build_weekend_plan(schedule, pd.Timestamp("2026-01-15 00:00"))

        self.assertEqual(plan["event_name"], "Abu Dhabi Grand Prix")
        self.assertEqual(plan["year"], 2025)
        self.assertEqual(plan["default_session"], "R")

    def test_testing_events_are_ignored(self):
        schedule = make_schedule([make_event(1, "Australian Grand Prix", 2026, "03-06", CONVENTIONAL)])

        plan = build_weekend_plan(schedule, pd.Timestamp("2026-03-10 00:00"))

        self.assertNotEqual(plan["event_name"], "Pre-Season Testing")


class TabStateMessageTests(unittest.TestCase):
    def test_not_started_message_names_the_session_and_its_start_time(self):
        message = format_not_started_message("Race", pd.Timestamp("2026-08-23 13:00"))

        self.assertIn("Race has not started yet", message)
        self.assertIn("Sun 23 Aug 2026 13:00 UTC", message)

    def test_not_started_status_is_a_one_line_version_of_the_same_thing(self):
        status = format_not_started_status("Qualifying", pd.Timestamp("2026-08-22 14:00"))

        self.assertNotIn("\n", status)
        self.assertIn("Qualifying", status)
        self.assertIn("Sat 22 Aug 2026 14:00 UTC", status)

    def test_a_session_without_a_published_start_time_still_reads_sensibly(self):
        for start in (None, pd.NaT):
            with self.subTest(start=start):
                self.assertEqual(format_session_start(start), "an unannounced time")
                self.assertIn("an unannounced time", format_not_started_message("Sprint", start))

    def test_session_error_reports_the_failure_and_how_to_retry(self):
        message = format_session_error("Practice 2", ValueError("no data for this session"))

        self.assertIn("Could not load Practice 2", message)
        self.assertIn("no data for this session", message)
        self.assertIn("unaffected", message)
        self.assertIn("again", message)

    def test_schedule_fetch_failure_reads_differently_from_an_empty_season(self):
        failure = format_schedule_error(2026, ConnectionError("name resolution failed"))
        empty = format_empty_season_message(2026)

        self.assertIn("name resolution failed", failure)
        self.assertNotIn("name resolution failed", empty)
        self.assertIn("no Grand Prix events", empty)
        self.assertNotIn("no Grand Prix events", failure)
        self.assertNotEqual(failure, empty)


class EventValidationTests(unittest.TestCase):
    def test_event_name_must_be_a_loaded_event(self):
        app = F1ResultsApp()
        app.event_names = ["Australian Grand Prix"]

        self.assertTrue(app.is_valid_event_name("Australian Grand Prix"))
        self.assertFalse(app.is_valid_event_name(""))
        self.assertFalse(app.is_valid_event_name(Select.BLANK))
        self.assertFalse(app.is_valid_event_name(None))
        self.assertFalse(app.is_valid_event_name("Unknown Grand Prix"))


def square_lap_position_data(points_per_side=25):
    """One closed 100 m square lap, in the tenths of a metre the position API reports.

    Square on purpose: the aspect correction is the easiest thing to get silently
    wrong, and a square is the shape that shows it. Held away from the origin
    because (0, 0) is the API's no-fix placeholder and gets filtered out.
    """
    side = 1000.0
    origin = 1000.0
    xs: list[float] = []
    ys: list[float] = []
    for step in range(points_per_side):
        xs.append(origin + side * step / points_per_side)
        ys.append(origin)
    for step in range(points_per_side):
        xs.append(origin + side)
        ys.append(origin + side * step / points_per_side)
    for step in range(points_per_side):
        xs.append(origin + side - side * step / points_per_side)
        ys.append(origin + side)
    for step in range(points_per_side):
        xs.append(origin)
        ys.append(origin + side - side * step / points_per_side)
    xs.append(origin)
    ys.append(origin)
    return pd.DataFrame(
        {
            "Time": [pd.Timedelta(seconds=index) for index in range(len(xs))],
            "X": xs,
            "Y": ys,
        }
    )


def square_lap(sample_count=101):
    last = sample_count - 1
    return pd.Series(
        {
            "LapStartTime": pd.Timedelta(seconds=0),
            "LapTime": pd.Timedelta(seconds=last),
            "Sector1SessionTime": pd.Timedelta(seconds=last / 3),
            "Sector2SessionTime": pd.Timedelta(seconds=2 * last / 3),
        }
    )


def marker_cells(text, marker="•"):
    cells = []
    for row, line in enumerate(text.plain.split("\n")):
        for column, character in enumerate(line):
            if character == marker:
                cells.append((row, column))
    return cells


def span_style_counts(text):
    counts = {}
    for span in text.spans:
        counts[str(span.style)] = counts.get(str(span.style), 0) + 1
    return counts


class TrackMapTests(unittest.TestCase):
    def test_traced_length_matches_hand_computed_geometry(self):
        track = extract_track_map(square_lap_position_data(), square_lap())

        self.assertAlmostEqual(float(track["length_m"]), 400.0, places=6)
        self.assertFalse(track["length_is_official"])

    def test_official_length_from_the_table_wins_over_the_traced_estimate(self):
        track = extract_track_map(
            square_lap_position_data(), square_lap(), circuit_key=4
        )

        self.assertEqual(float(track["length_m"]), 4381.0)
        self.assertTrue(track["length_is_official"])

    def test_zero_coordinate_samples_are_dropped(self):
        position_data = square_lap_position_data()
        blank = pd.DataFrame({"Time": [pd.Timedelta(seconds=1.5)], "X": [0.0], "Y": [0.0]})
        noisy = pd.concat([position_data, blank]).sort_values("Time").reset_index(drop=True)

        track = extract_track_map(noisy, square_lap())

        self.assertAlmostEqual(float(track["length_m"]), 400.0, places=6)
        self.assertEqual(len(track["points"]), len(position_data))

    def test_sector_times_split_the_lap_into_three_groups(self):
        track = extract_track_map(square_lap_position_data(), square_lap())

        sectors = list(track["sectors"])
        self.assertEqual(sorted(set(sectors)), [1, 2, 3])
        self.assertEqual(sectors, sorted(sectors))
        self.assertEqual(len(sectors), len(track["points"]))

    def test_missing_sector_times_leave_the_whole_lap_in_one_sector(self):
        lap = square_lap()
        lap["Sector1SessionTime"] = pd.NaT
        lap["Sector2SessionTime"] = pd.NaT

        track = extract_track_map(square_lap_position_data(), lap)

        self.assertEqual(set(track["sectors"]), {1})

    def test_rotation_turns_the_track_to_the_official_orientation(self):
        straight = pd.DataFrame(
            {
                "Time": [pd.Timedelta(seconds=0), pd.Timedelta(seconds=1)],
                "X": [0.0, 1000.0],
                "Y": [0.0, 0.0],
            }
        )
        lap = pd.Series(
            {
                "LapStartTime": pd.Timedelta(seconds=0),
                "LapTime": pd.Timedelta(seconds=1),
                "Sector1SessionTime": pd.NaT,
                "Sector2SessionTime": pd.NaT,
            }
        )

        track = extract_track_map(straight, lap, rotation=90.0)

        end_x, end_y = track["points"][-1]
        self.assertAlmostEqual(end_x, 0.0, places=6)
        self.assertAlmostEqual(end_y, 100.0, places=6)

    def test_samples_outside_the_lap_window_are_ignored(self):
        position_data = square_lap_position_data()
        stray = pd.DataFrame(
            {"Time": [pd.Timedelta(seconds=500)], "X": [90000.0], "Y": [90000.0]}
        )
        noisy = pd.concat([position_data, stray]).reset_index(drop=True)

        track = extract_track_map(noisy, square_lap())

        self.assertEqual(len(track["points"]), len(position_data))
        self.assertAlmostEqual(float(track["length_m"]), 400.0, places=6)

    def test_a_lap_without_usable_samples_yields_no_points(self):
        blank = pd.DataFrame(
            {
                "Time": [pd.Timedelta(seconds=0), pd.Timedelta(seconds=1)],
                "X": [0.0, 0.0],
                "Y": [0.0, 0.0],
            }
        )

        track = extract_track_map(blank, square_lap())

        self.assertEqual(list(track["points"]), [])
        self.assertEqual(float(track["length_m"]), 0.0)

    def test_the_circuit_description_is_carried_through(self):
        track = extract_track_map(
            square_lap_position_data(),
            square_lap(),
            corner_count=16,
            name="Hungaroring",
            location="Budapest, Hungary",
        )

        self.assertEqual(track["name"], "Hungaroring")
        self.assertEqual(track["location"], "Budapest, Hungary")
        self.assertEqual(track["corner_count"], 16)


class GeometryLapTests(unittest.TestCase):
    def laps_for(self, *driver_numbers):
        return pd.DataFrame(
            {
                "DriverNumber": list(driver_numbers),
                "LapTime": pd.to_timedelta([80.0] * len(driver_numbers), unit="s"),
                "LapStartTime": pd.to_timedelta([0.0] * len(driver_numbers), unit="s"),
                "Sector1SessionTime": pd.to_timedelta([26.0] * len(driver_numbers), unit="s"),
                "Sector2SessionTime": pd.to_timedelta([53.0] * len(driver_numbers), unit="s"),
            }
        )

    def samples_with(self, distinct_positions):
        # A feed that repeats the same coordinate is what a race looks like for
        # most cars; the repeats are padded out to a constant sample count so the
        # choice cannot be explained by raw sample volume.
        rows = []
        for index in range(60):
            step = index % distinct_positions
            rows.append((pd.Timedelta(seconds=index), 1000.0 + step, 2000.0 + step))
        return pd.DataFrame(rows, columns=["Time", "X", "Y"])

    def test_the_densest_trace_wins_over_the_fastest_lap(self):
        laps = self.laps_for("1", "77")
        position_data = {"1": self.samples_with(3), "77": self.samples_with(40)}

        lap, samples = pick_geometry_lap(laps, position_data)

        self.assertEqual(lap["DriverNumber"], "77")
        self.assertIs(samples, position_data["77"])

    def test_a_driver_without_position_data_is_skipped(self):
        laps = self.laps_for("1", "77")
        position_data = {"77": self.samples_with(5)}

        lap, _ = pick_geometry_lap(laps, position_data)

        self.assertEqual(lap["DriverNumber"], "77")

    def test_no_usable_trace_yields_nothing(self):
        lap, samples = pick_geometry_lap(self.laps_for("1"), {})

        self.assertIsNone(lap)
        self.assertIsNone(samples)


class TrackPanelTests(unittest.TestCase):
    def square_track(self, **overrides):
        track = extract_track_map(
            square_lap_position_data(),
            square_lap(),
            corner_count=16,
            name="Hungaroring",
            location="Budapest, Hungary",
        )
        track.update(overrides)
        return track

    def test_a_square_track_is_drawn_square(self):
        panel = render_track_panel(self.square_track(), TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        cells = marker_cells(panel)
        columns = [column for _, column in cells]
        rows = [row for row, _ in cells]
        width = max(columns) - min(columns) + 1
        height = max(rows) - min(rows) + 1

        # A character cell is about twice as tall as it is wide, so a square
        # track has to come out about twice as wide as it is tall.
        self.assertAlmostEqual(width, height * 2, delta=2)

    def test_drawn_points_stay_inside_the_map_region(self):
        panel = render_track_panel(self.square_track(), TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        cells = marker_cells(panel)
        self.assertTrue(cells)
        self.assertLess(max(column for _, column in cells), TRACK_CONTENT_WIDTH - TRACK_TEXT_WIDTH - TRACK_GUTTER)
        self.assertLess(max(row for row, _ in cells), TRACK_CONTENT_HEIGHT)

    def test_a_wide_track_keeps_clear_of_the_text_column(self):
        # A circuit far wider than it is tall fills the map region edge to edge,
        # which is when it would otherwise run into the description.
        wide = self.square_track(
            points=[(x / 2.0, 0.0) for x in range(200)],
            sectors=[1] * 200,
        )

        panel = render_track_panel(wide, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        for line in panel.plain.split("\n"):
            gutter = line[TRACK_CONTENT_WIDTH - TRACK_TEXT_WIDTH - TRACK_GUTTER : TRACK_CONTENT_WIDTH - TRACK_TEXT_WIDTH]
            self.assertEqual(gutter.strip(), "")

    def test_a_long_circuit_name_is_truncated_rather_than_wrapped(self):
        track = self.square_track(
            name="Autodromo Internazionale Enzo e Dino Ferrari",
            location="Spa-Francorchamps, Belgium",
        )

        panel = render_track_panel(track, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        lines = panel.plain.split("\n")
        self.assertEqual(len(lines), TRACK_CONTENT_HEIGHT)
        for line in lines:
            self.assertLessEqual(len(line), TRACK_CONTENT_WIDTH)

    def test_each_sector_is_drawn_in_its_own_colour(self):
        panel = render_track_panel(self.square_track(), TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        # The legend contributes exactly one span per colour, so anything above
        # one is the map itself being coloured.
        counts = span_style_counts(panel)
        for colour in ("red", "cyan", "yellow"):
            self.assertGreater(counts.get(colour, 0), 1)

    def test_the_legend_names_all_three_sectors(self):
        panel = render_track_panel(self.square_track(), TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        for sector in ("Sector 1", "Sector 2", "Sector 3"):
            self.assertIn(sector, panel.plain)

    def test_an_official_length_is_shown_plainly(self):
        track = self.square_track(length_m=4381.0, length_is_official=True)

        panel = render_track_panel(track, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        self.assertIn("4381 m", panel.plain)
        self.assertNotIn("≈", panel.plain)

    def test_an_estimated_length_is_marked_approximate(self):
        track = self.square_track(length_m=3272.0, length_is_official=False)

        panel = render_track_panel(track, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        self.assertIn("≈ 3272 m", panel.plain)

    def test_the_circuit_name_location_and_corner_count_are_shown(self):
        panel = render_track_panel(self.square_track(), TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        self.assertIn("Hungaroring", panel.plain)
        self.assertIn("Budapest", panel.plain)
        self.assertIn("16", panel.plain)

    def test_a_single_point_track_does_not_raise(self):
        track = self.square_track(points=[(10.0, 20.0)], sectors=[1])

        panel = render_track_panel(track, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        self.assertEqual(len(marker_cells(panel)), 1)

    def test_identical_points_do_not_raise(self):
        track = self.square_track(points=[(5.0, 5.0)] * 20, sectors=[1] * 20)

        panel = render_track_panel(track, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        self.assertEqual(len(marker_cells(panel)), 1)

    def test_a_track_without_points_still_renders_its_description(self):
        track = self.square_track(points=[], sectors=[])

        panel = render_track_panel(track, TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        self.assertEqual(marker_cells(panel), [])
        self.assertIn("Hungaroring", panel.plain)

    def test_the_panel_fills_the_requested_height(self):
        panel = render_track_panel(self.square_track(), TRACK_CONTENT_WIDTH, TRACK_CONTENT_HEIGHT)

        self.assertEqual(len(panel.plain.split("\n")), TRACK_CONTENT_HEIGHT)

    def test_the_track_error_says_it_retries_by_itself(self):
        message = format_track_error(ValueError("no completed lap to draw the circuit from"))

        self.assertIn("no completed lap to draw the circuit from", message)
        self.assertIn("next session", message)

    def test_a_circuit_named_after_its_town_does_not_repeat_the_town(self):
        event = {"Location": "Spa-Francorchamps", "Country": "Belgium"}

        self.assertEqual(format_circuit_location(event, "Spa-Francorchamps"), "Belgium")

    def test_a_circuit_in_a_differently_named_town_names_both(self):
        event = {"Location": "Budapest", "Country": "Hungary"}

        self.assertEqual(format_circuit_location(event, "Hungaroring"), "Budapest, Hungary")


if __name__ == "__main__":
    unittest.main()
