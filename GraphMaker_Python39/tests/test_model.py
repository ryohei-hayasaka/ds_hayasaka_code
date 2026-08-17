import unittest
from pathlib import Path

from tga_analyzer.model import DSC, AxisRange, CurveData, PlotState


def curve(name: str, temps=(25.0, 800.0), weights=(100.0, 10.0)) -> CurveData:
    masses = tuple(value / 10 for value in weights)
    return CurveData(Path(name + ".csv"), name, tuple(temps), masses, tuple(weights))


class ModelTests(unittest.TestCase):
    def test_add_duplicate_remove_and_auto_range(self):
        state = PlotState()
        first = curve("first")
        self.assertTrue(state.add_curve(first))
        self.assertFalse(state.add_curve(curve("first")))
        self.assertEqual(state.axis_range, AxisRange(25.0, 800.0, 0.0, 105.0))
        self.assertTrue(state.remove_curve(first.key))
        self.assertEqual(state.axis_range, AxisRange(0.0, 100.0, 0.0, 105.0))

    def test_manual_range_is_preserved_when_curve_is_added(self):
        state = PlotState()
        manual = AxisRange(100, 500, 20, 100)
        state.set_manual_range(manual)
        state.add_curve(curve("first"))
        self.assertEqual(state.axis_range, manual)
        self.assertFalse(state.auto_axes)

    def test_reloaded_curve_keeps_color_and_order(self):
        state = PlotState()
        original = curve("first")
        state.add_curve(original)
        state.set_color(original.key, "#ABCDEF")
        state.set_legend_name(original.key, "Edited legend")
        replacement = curve("first", temps=(30.0, 700.0), weights=(100.0, 20.0))
        self.assertTrue(state.replace_curve(replacement))
        self.assertEqual(state.ordered_curves()[0].color, "#ABCDEF")
        self.assertEqual(state.ordered_curves()[0].legend_label, "Edited legend")

    def test_legend_name_defaults_to_series_name_and_is_trimmed(self):
        state = PlotState()
        first = curve("first")
        second = curve("second")
        state.add_curve(first)
        state.add_curve(second)

        self.assertEqual(first.legend_label, "first")
        state.set_legend_name(first.key, "  Main sample  ")

        self.assertEqual(first.legend_label, "Main sample")
        self.assertEqual(second.legend_label, "second")

    def test_empty_legend_name_is_rejected_without_changing_value(self):
        state = PlotState()
        first = curve("first")
        state.add_curve(first)
        state.set_legend_name(first.key, "Kept legend")

        with self.assertRaisesRegex(ValueError, "空にできません"):
            state.set_legend_name(first.key, "   ")

        self.assertEqual(first.legend_label, "Kept legend")

    def test_tga_and_dsc_states_keep_independent_legend_names(self):
        tga_state = PlotState(measurement_type="TGA")
        dsc_state = PlotState(measurement_type=DSC)
        tga_curve = curve("shared")
        dsc_curve = CurveData(
            path=Path("shared.csv"),
            display_name="shared",
            temperatures=(20.0, 30.0),
            mass_mg=(),
            weight_percent=(),
            measurement_type=DSC,
            heat_flow_mw=(0.0, 1.0),
        )
        tga_state.add_curve(tga_curve)
        dsc_state.add_curve(dsc_curve)
        tga_state.set_legend_name(tga_curve.key, "TGA legend")
        dsc_state.set_legend_name(dsc_curve.key, "DSC legend")

        self.assertEqual(tga_curve.legend_label, "TGA legend")
        self.assertEqual(dsc_curve.legend_label, "DSC legend")

    def test_tga_and_dsc_states_keep_independent_colors(self):
        tga_state = PlotState(measurement_type="TGA")
        dsc_state = PlotState(measurement_type=DSC)
        tga_curve = curve("shared")
        dsc_curve = CurveData(
            path=Path("shared.csv"),
            display_name="shared",
            temperatures=(20.0, 30.0),
            mass_mg=(),
            weight_percent=(),
            measurement_type=DSC,
            heat_flow_mw=(0.0, 1.0),
        )
        tga_state.add_curve(tga_curve)
        dsc_state.add_curve(dsc_curve)
        tga_state.set_color(tga_curve.key, "#112233")
        dsc_state.set_color(dsc_curve.key, "#AABBCC")

        self.assertEqual(tga_curve.color, "#112233")
        self.assertEqual(dsc_curve.color, "#AABBCC")

    def test_axis_validation(self):
        with self.assertRaises(ValueError):
            AxisRange(10, 10, 0, 100).validate()
        with self.assertRaises(ValueError):
            AxisRange(0, 10, 100, 100).validate()

    def test_dsc_auto_range_uses_heat_flow_with_padding(self):
        dsc_curve = CurveData(
            path=Path("dsc.csv"),
            display_name="dsc",
            temperatures=(20.0, 100.0, 200.0),
            mass_mg=(),
            weight_percent=(),
            measurement_type=DSC,
            heat_flow_mw=(-2.0, 1.0, 4.0),
            time_min=(0.0, 1.0, 2.0),
        )
        state = PlotState(measurement_type=DSC)
        state.add_curve(dsc_curve)
        self.assertEqual(state.axis_range.x_min, 20.0)
        self.assertEqual(state.axis_range.x_max, 200.0)
        self.assertLess(state.axis_range.y_min, -2.0)
        self.assertGreater(state.axis_range.y_max, 4.0)

    def test_rejects_curve_from_different_measurement_mode(self):
        with self.assertRaisesRegex(ValueError, "追加できません"):
            PlotState(measurement_type=DSC).add_curve(curve("tga"))


if __name__ == "__main__":
    unittest.main()
