import unittest

from tga_analyzer.dsc_analysis import (
    DscAnalysisError,
    DscAnalysisSession,
    DscAnalysisSettings,
    TemperatureRange,
)
from tga_analyzer.dsc_selection import (
    DscFourPointSelection,
    four_points_to_ranges,
    ranges_to_four_points,
    settings_with_four_points,
)


class DscFourPointSelectionTests(unittest.TestCase):
    def test_tg_four_points_map_to_analysis_baselines_and_search(self):
        converted = four_points_to_ranges((50, 65, 95, 110), "tg")
        self.assertEqual(converted.analysis, TemperatureRange(50, 110))
        self.assertEqual(converted.pre_baseline, TemperatureRange(50, 65))
        self.assertEqual(converted.search, TemperatureRange(65, 95))
        self.assertEqual(converted.post_baseline, TemperatureRange(95, 110))

    def test_melting_four_points_map_to_analysis_baselines_and_search(self):
        converted = four_points_to_ranges((110, 125, 175, 195), "melt")
        self.assertEqual(converted.analysis, TemperatureRange(110, 195))
        self.assertEqual(converted.pre_baseline, TemperatureRange(110, 125))
        self.assertEqual(converted.search, TemperatureRange(125, 175))
        self.assertEqual(converted.post_baseline, TemperatureRange(175, 195))

    def test_less_than_four_points_cannot_be_calculated(self):
        with self.assertRaisesRegex(DscAnalysisError, "4点が必要"):
            four_points_to_ranges((50, 65, 95), "tg")

    def test_points_must_be_strictly_increasing_without_duplicates(self):
        for points in ((50, 95, 65, 110), (50, 65, 65, 110)):
            with self.subTest(points=points):
                with self.assertRaisesRegex(DscAnalysisError, "温度の低い順"):
                    four_points_to_ranges(points, "tg")

    def test_click_selection_validates_curve_range_and_supports_undo(self):
        selection = DscFourPointSelection("tg", "curve-a", 20, 200)
        for value in (50, 65, 95, 110):
            selection.add_temperature(value)
        self.assertTrue(selection.complete)
        self.assertEqual(selection.validate().search, TemperatureRange(65, 95))
        self.assertEqual(selection.undo(), 110)
        self.assertFalse(selection.complete)
        with self.assertRaisesRegex(DscAnalysisError, "温度範囲"):
            selection.add_temperature(250)

    def test_canceling_selection_does_not_change_existing_session(self):
        settings = DscAnalysisSettings(
            tg_range=TemperatureRange(40, 120),
            tg_pre_range=TemperatureRange(40, 60),
            tg_post_range=TemperatureRange(100, 120),
        )
        session = DscAnalysisSession(settings=settings, decision="採用", status="Tg再計算済み")
        selection = DscFourPointSelection("tg", "curve-a", 20, 200)
        selection.add_temperature(50)
        selection.add_temperature(65)
        selection.points.clear()
        self.assertIs(session.settings, settings)
        self.assertEqual(session.settings.tg_range, TemperatureRange(40, 120))
        self.assertEqual(session.decision, "採用")
        self.assertEqual(session.status, "Tg再計算済み")

    def test_only_selected_series_settings_are_updated(self):
        first = DscAnalysisSettings(sample_mass_mg=10)
        second = DscAnalysisSettings(sample_mass_mg=12)
        updated_first = settings_with_four_points(first, (50, 65, 95, 110), "tg")
        self.assertEqual(updated_first.tg_range, TemperatureRange(50, 110))
        self.assertIsNone(second.tg_range)
        self.assertEqual(updated_first.sample_mass_mg, 10)
        self.assertEqual(second.sample_mass_mg, 12)

    def test_numeric_ranges_round_trip_to_graph_points(self):
        points = ranges_to_four_points(
            TemperatureRange(50, 110),
            TemperatureRange(50, 65),
            TemperatureRange(95, 110),
            "tg",
        )
        self.assertEqual(points, (50, 65, 95, 110))
        with self.assertRaisesRegex(DscAnalysisError, "一致"):
            ranges_to_four_points(
                TemperatureRange(45, 110),
                TemperatureRange(50, 65),
                TemperatureRange(95, 110),
                "tg",
            )


if __name__ == "__main__":
    unittest.main()
