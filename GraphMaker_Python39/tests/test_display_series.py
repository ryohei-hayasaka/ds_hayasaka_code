import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from tga_analyzer.display_series import (
    display_axis_titles,
    mode_reverse_x,
    to_display_series,
)
from tga_analyzer.model import DSC, GPC, IR, TGA, UV_VIS, CurveData
from tga_analyzer.processing import (
    CommonProcessingSettings,
    SeriesProcessingSettings,
    process_dsc_curve,
)


class DisplaySeriesTests(unittest.TestCase):
    def test_mode_specific_raw_data_is_projected_without_mutation(self):
        uv = CurveData(
            path=Path("uv.csv"), display_name="uv", temperatures=(), mass_mg=(),
            weight_percent=(), measurement_type=UV_VIS,
            wavelengths_nm=(200.0, 300.0), uvvis_absorbance=(-0.1, 0.8),
        )
        gpc = CurveData(
            path=Path("gpc.csv"), display_name="gpc", temperatures=(), mass_mg=(),
            weight_percent=(), measurement_type=GPC,
            retention_times_min=(0.0, 30.0), ri_signal_mv=(0.6, 1.2),
        )
        uv_before = (uv.wavelengths_nm, uv.uvvis_absorbance)
        gpc_before = (gpc.retention_times_min, gpc.ri_signal_mv)

        uv_display = to_display_series(uv)
        gpc_display = to_display_series(gpc)

        self.assertEqual((uv_display.x_values, uv_display.y_values), uv_before)
        self.assertEqual((gpc_display.x_values, gpc_display.y_values), gpc_before)
        self.assertEqual((uv.wavelengths_nm, uv.uvvis_absorbance), uv_before)
        self.assertEqual((gpc.retention_times_min, gpc.ri_signal_mv), gpc_before)
        self.assertEqual(
            display_axis_titles((uv_display,), UV_VIS),
            ("Wavelength (nm)", "Absorbance"),
        )
        self.assertEqual(
            display_axis_titles((gpc_display,), GPC),
            ("Retention time (min)", "RI response (mV)"),
        )
        self.assertFalse(uv_display.reverse_x)
        self.assertFalse(gpc_display.reverse_x)
        with self.assertRaises(FrozenInstanceError):
            uv_display.legend_name = "changed"

    def test_only_ir_reverses_x(self):
        self.assertTrue(mode_reverse_x(IR))
        for mode in (TGA, DSC, UV_VIS, GPC):
            with self.subTest(mode=mode):
                self.assertFalse(mode_reverse_x(mode))

    def test_dsc_projection_uses_existing_processed_curve_not_raw_heat_flow(self):
        sample = CurveData(
            path=Path("sample.csv"), display_name="sample", temperatures=(20.0, 30.0),
            mass_mg=(), weight_percent=(), measurement_type=DSC,
            heat_flow_mw=(3.0, 5.0), time_min=(0.0, 1.0), heat_flow_unit="mW",
            source_heat_flow_header="HeatFlow_mW",
        )
        blank = CurveData(
            path=Path("blank.csv"), display_name="blank", temperatures=(20.0, 30.0),
            mass_mg=(), weight_percent=(), measurement_type=DSC,
            heat_flow_mw=(1.0, 1.0), time_min=(0.0, 1.0), heat_flow_unit="mW",
            source_heat_flow_header="HeatFlow_mW",
        )
        processed = process_dsc_curve(
            sample, CommonProcessingSettings(blank_key=blank.key),
            SeriesProcessingSettings(), {sample.key: sample, blank.key: blank},
        )

        display = to_display_series(processed)

        self.assertEqual(display.y_values, (2.0, 4.0))
        self.assertEqual(sample.heat_flow_mw, (3.0, 5.0))
        self.assertEqual(display.extra_columns[0].header, "Time_min")
        self.assertIn("Status=Blank corrected", display.header_metadata)


if __name__ == "__main__":
    unittest.main()
