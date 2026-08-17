import unittest
from pathlib import Path

from tga_analyzer.model import DSC, TGA, CurveData, PlotState
from tga_analyzer.tga_analysis import (
    TgaTdError,
    calculate_custom_td_for_selection,
    calculate_standard_td,
    find_decomposition_temperature,
    format_td_temperature,
    parse_remaining_percent,
    selected_tga_curve,
    td_label_from_remaining_percent,
)


def make_tga_curve(
    name: str,
    temperatures=(0.0, 100.0),
    weights=(100.0, 0.0),
) -> CurveData:
    return CurveData(
        path=Path(name + ".csv"),
        display_name=name,
        temperatures=tuple(temperatures),
        mass_mg=tuple(weights),
        weight_percent=tuple(weights),
        measurement_type=TGA,
    )


class TgaTdCalculationTests(unittest.TestCase):
    def test_standard_td5_td50_td95(self):
        summary = calculate_standard_td(make_tga_curve("sample"))
        self.assertAlmostEqual(summary.td5_c, 5.0)
        self.assertAlmostEqual(summary.td50_c, 50.0)
        self.assertAlmostEqual(summary.td95_c, 95.0)
        self.assertEqual(summary.status, "算出済み")

    def test_linear_interpolation_between_two_points(self):
        temperature = find_decomposition_temperature(
            (100.0, 200.0), (100.0, 80.0), 90.0
        )
        self.assertAlmostEqual(temperature, 150.0)

    def test_exact_data_point_uses_that_temperature(self):
        temperature = find_decomposition_temperature(
            (100.0, 150.0, 200.0), (100.0, 90.0, 80.0), 90.0
        )
        self.assertEqual(temperature, 150.0)

    def test_flat_target_uses_first_point(self):
        temperature = find_decomposition_temperature(
            (100.0, 120.0, 140.0), (95.0, 95.0, 90.0), 95.0
        )
        self.assertEqual(temperature, 100.0)

    def test_non_integer_loss_generates_compact_label(self):
        self.assertEqual(td_label_from_remaining_percent(87.5), "Td12.5")
        self.assertEqual(td_label_from_remaining_percent(90.0), "Td10")

    def test_temperature_display_uses_two_decimal_places(self):
        self.assertEqual(format_td_temperature(123.456), "123.46")
        self.assertEqual(format_td_temperature(None), "算出不可")

    def test_first_downward_crossing_is_used_when_noise_crosses_more_than_once(self):
        temperature = find_decomposition_temperature(
            (0.0, 100.0, 200.0, 300.0),
            (100.0, 80.0, 95.0, 70.0),
            90.0,
        )
        self.assertAlmostEqual(temperature, 50.0)

    def test_upward_crossing_is_not_used(self):
        temperature = find_decomposition_temperature(
            (0.0, 100.0, 200.0), (80.0, 95.0, 85.0), 90.0
        )
        self.assertAlmostEqual(temperature, 150.0)

    def test_unreached_target_has_specific_warning(self):
        with self.assertRaisesRegex(TgaTdError, "残存率95%に到達していません"):
            find_decomposition_temperature((0.0, 100.0), (100.0, 96.0), 95.0)

    def test_insufficient_points_has_specific_warning(self):
        with self.assertRaisesRegex(TgaTdError, "データ点が不足"):
            find_decomposition_temperature((100.0,), (100.0,), 95.0)

    def test_invalid_temperature_or_weight_is_rejected(self):
        with self.assertRaisesRegex(TgaTdError, "データが不正"):
            find_decomposition_temperature((0.0, float("nan")), (100.0, 90.0), 95.0)

    def test_invalid_remaining_percent_inputs_are_rejected(self):
        cases = (
            ("", "入力してください"),
            ("abc", "数値"),
            (0, "0より大きく100より小さい"),
            (100, "0より大きく100より小さい"),
            (-1, "0より大きく100より小さい"),
        )
        for value, message in cases:
            with self.subTest(value=value), self.assertRaisesRegex(TgaTdError, message):
                parse_remaining_percent(value)


class TgaTdSelectionTests(unittest.TestCase):
    def setUp(self):
        self.first = make_tga_curve("first", (0.0, 100.0), (100.0, 0.0))
        self.second = make_tga_curve("second", (0.0, 200.0), (100.0, 0.0))
        self.state = PlotState(measurement_type=TGA)
        self.state.add_curve(self.first)
        self.state.add_curve(self.second)

    def test_no_selection_is_rejected(self):
        with self.assertRaisesRegex(TgaTdError, "1つ選択"):
            selected_tga_curve(self.state, [])

    def test_multiple_selection_is_rejected(self):
        with self.assertRaisesRegex(TgaTdError, "1つ選択"):
            selected_tga_curve(self.state, [self.first.key, self.second.key])

    def test_only_selected_curve_is_calculated(self):
        first = calculate_standard_td(selected_tga_curve(self.state, [self.first.key]))
        second = calculate_standard_td(selected_tga_curve(self.state, [self.second.key]))
        self.assertEqual(first.curve_name, "first")
        self.assertEqual(second.curve_name, "second")
        self.assertAlmostEqual(first.td50_c, 50.0)
        self.assertAlmostEqual(second.td50_c, 100.0)

    def test_reload_uses_replacement_data(self):
        replacement = make_tga_curve("first", (0.0, 300.0), (100.0, 0.0))
        self.assertTrue(self.state.replace_curve(replacement))
        summary = calculate_standard_td(selected_tga_curve(self.state, [self.first.key]))
        self.assertAlmostEqual(summary.td50_c, 150.0)

    def test_deleted_curve_is_no_longer_selected(self):
        self.state.remove_curve(self.first.key)
        with self.assertRaisesRegex(TgaTdError, "1つ選択"):
            selected_tga_curve(self.state, [self.first.key])

    def test_dsc_mode_rejects_td_analysis(self):
        state = PlotState(measurement_type=DSC)
        with self.assertRaisesRegex(TgaTdError, "DSCモード"):
            selected_tga_curve(state, [])

    def test_custom_td_uses_selected_curve_and_compact_label(self):
        label, temperature = calculate_custom_td_for_selection(
            self.state, [self.first.key], "87.5"
        )
        self.assertEqual(label, "Td12.5")
        self.assertAlmostEqual(temperature, 12.5)


if __name__ == "__main__":
    unittest.main()
