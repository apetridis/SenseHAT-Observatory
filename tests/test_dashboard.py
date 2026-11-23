"""Contract tests for the generated Grafana dashboard."""

from __future__ import annotations

import unittest

from scripts.generate_dashboard import build_dashboard


class DashboardTests(unittest.TestCase):
    def test_dashboard_contains_required_sections_and_time_controls(self) -> None:
        dashboard = build_dashboard()
        titles = {panel["title"] for panel in dashboard["panels"]}

        self.assertEqual(dashboard["title"], "SenseHAT Observatory")
        self.assertEqual(dashboard["refresh"], "30s")
        self.assertEqual(dashboard["time"]["from"], "now-24h")
        self.assertTrue({"1h", "6h", "24h", "7d", "30d"}.issubset(
            set(dashboard["timepicker"]["time_options"])
        ))
        self.assertTrue(
            {
                "Overview",
                "Environmental monitoring",
                "Motion monitoring",
                "Hardware and collector health",
                "Daily temperature minimum / maximum / average",
                "Daily humidity minimum / maximum / average",
                "Daily pressure minimum / maximum / average",
                "Temperature difference (humidity - pressure sensor)",
                "Compass heading",
                "Movement detection",
                "Joystick, movement and orientation events",
            }.issubset(titles)
        )
        status_panel = next(
            item for item in dashboard["panels"] if item["title"] == "Collector status"
        )
        self.assertIn("90s", status_panel["targets"][0]["query"])
        self.assertIn("offline", status_panel["targets"][0]["query"])

    def test_panels_have_unique_ids_and_fit_the_dashboard_grid(self) -> None:
        panels = build_dashboard()["panels"]
        ids = [panel["id"] for panel in panels]
        self.assertEqual(len(ids), len(set(ids)))
        for panel in panels:
            grid = panel["gridPos"]
            self.assertGreater(grid["w"], 0)
            self.assertGreater(grid["h"], 0)
            self.assertGreaterEqual(grid["x"], 0)
            self.assertLessEqual(grid["x"] + grid["w"], 24)

    def test_motion_boolean_is_converted_before_flux_yield(self) -> None:
        panel = next(
            item
            for item in build_dashboard()["panels"]
            if item["title"] == "Movement detection"
        )
        query = panel["targets"][0]["query"]
        self.assertLess(query.index("|> map("), query.index("|> yield("))


if __name__ == "__main__":
    unittest.main()
