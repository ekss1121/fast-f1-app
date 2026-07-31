import unittest

import pandas as pd

from textual.widgets import Select

from fast_f1_app import (
    build_comparison_metrics,
    build_qualifying_details,
    F1ResultsApp,
    format_compounds,
    format_lap_time,
    format_position_cell,
    format_qualifying_time,
    format_result_time,
    make_comparison_lap_time_graph,
    make_lap_time_graph,
    make_y_ticks,
    render_driver_details,
    resolve_comparison_colors,
    to_plot_color,
    to_position,
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
        graph = make_lap_time_graph([83.4, 82.9, 83.1])

        self.assertIn("Lap times", graph)
        self.assertIn("Lap", graph)
        self.assertNotIn(chr(27), graph)

    def test_make_lap_time_graph_plots_one_marker_per_lap(self):
        lap_times = [83.4, 92.1, 85.0, 110.6, 84.2]
        graph = make_lap_time_graph(lap_times)

        self.assertEqual(graph.count("•"), len(lap_times))


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
            {"stops": 1, "compounds": ["SOFT"], "lap_times": [83.4], "lap_count": 1},
        )

        self.assertIn("🛞 SOFT", details.plain)

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


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.first = {
            "lap_count": 58,
            "lap_times": [83.4, 84.1, 85.0],
            "stops": 1,
            "compounds": ["MEDIUM", "HARD"],
        }
        self.second = {
            "lap_count": 57,
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

    def test_qualifying_comparison_adds_sector_deltas(self):
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

        metrics = self.metrics_by_name("Q")

        self.assertEqual(metrics["Delta to pole"], ("+0.500", "+0.900", ""))
        self.assertEqual(metrics["S1"], ("0:20.100", "0:20.400", "-0.300"))

    def test_qualifying_metrics_are_omitted_for_a_race(self):
        self.assertNotIn("S1", self.metrics_by_name())

    def test_comparison_graph_plots_both_series_in_their_own_color(self):
        graph = make_comparison_lap_time_graph(
            [
                {"label": "RUS", "lap_times": self.first["lap_times"], "color": "#00d2be"},
                {"label": "NOR", "lap_times": self.second["lap_times"], "color": "#ff8700"},
            ]
        )
        styles = {str(span.style) for span in graph.spans}

        self.assertIn("#00d2be", styles)
        self.assertIn("#ff8700", styles)
        self.assertIn("RUS", graph.plain)
        self.assertIn("NOR", graph.plain)

    def test_comparison_graph_handles_a_driver_with_no_laps(self):
        graph = make_comparison_lap_time_graph(
            [
                {"label": "RUS", "lap_times": [83.4, 84.0], "color": "cyan"},
                {"label": "STR", "lap_times": [], "color": "magenta"},
            ]
        )

        self.assertIn("RUS", graph.plain)
        self.assertNotIn("STR", graph.plain)

    def test_comparison_graph_without_any_laps_reports_no_data(self):
        graph = make_comparison_lap_time_graph(
            [{"label": "STR", "lap_times": [], "color": "cyan"}]
        )

        self.assertEqual(graph.plain, "No lap times available.")


class PositionTests(unittest.TestCase):
    def test_missing_position_becomes_blank_instead_of_raising(self):
        self.assertEqual(to_position(""), "")
        self.assertEqual(to_position(None), "")
        self.assertEqual(to_position(3.0), 3)

    def test_blank_position_renders_as_an_empty_cell(self):
        self.assertEqual(format_position_cell(to_position("")).plain, "")


class EventValidationTests(unittest.TestCase):
    def test_event_name_must_be_a_loaded_event(self):
        app = F1ResultsApp()
        app.event_names = ["Australian Grand Prix"]

        self.assertTrue(app.is_valid_event_name("Australian Grand Prix"))
        self.assertFalse(app.is_valid_event_name(""))
        self.assertFalse(app.is_valid_event_name(Select.BLANK))
        self.assertFalse(app.is_valid_event_name(None))
        self.assertFalse(app.is_valid_event_name("Unknown Grand Prix"))


if __name__ == "__main__":
    unittest.main()
