import math
import unittest
from pathlib import Path

from tga_analyzer.dsc_analysis import (
    DscAnalysisSettings,
    TemperatureRange,
    analyze_melting,
    analyze_tg,
    infer_heating_rate,
    suggest_dsc_ranges,
)
from tga_analyzer.model import DSC, CurveData


def dsc_curve(name: str, temperatures, heat_flow, *, unit="W/g", times=()) -> CurveData:
    return CurveData(
        path=Path(name + ".csv"),
        display_name=name,
        temperatures=tuple(temperatures),
        mass_mg=(),
        weight_percent=(),
        measurement_type=DSC,
        heat_flow_mw=tuple(heat_flow),
        time_min=tuple(times),
        heat_flow_unit=unit,
    )


class DscAnalysisTests(unittest.TestCase):
    def test_tg_onset_midpoint_and_inflection_for_known_step(self):
        temperatures = tuple(float(value) for value in range(0, 161))
        heat_flow = tuple(
            0.001 * temp + 0.2 * (1.0 + math.tanh((temp - 80.0) / 4.0))
            for temp in temperatures
        )
        curve = dsc_curve("tg", temperatures, heat_flow)
        settings = DscAnalysisSettings(
            heat_flow_unit="W/g",
            smoothing_window=5,
            tg_range=TemperatureRange(50, 110),
            tg_pre_range=TemperatureRange(50, 65),
            tg_post_range=TemperatureRange(95, 110),
        )
        result = analyze_tg(curve, settings)
        self.assertAlmostEqual(result.onset_c, 76.0, delta=1.5)
        self.assertAlmostEqual(result.midpoint_c, 80.0, delta=0.8)
        self.assertAlmostEqual(result.inflection_c, 80.0, delta=1.0)

    def test_melting_peak_and_enthalpy_for_specific_heat_flow(self):
        temperatures = tuple(value / 2 for value in range(100, 401))
        heat_flow = tuple(
            0.0005 * temp + 2.0 * math.exp(-0.5 * ((temp - 150.0) / 8.0) ** 2)
            for temp in temperatures
        )
        curve = dsc_curve("melt", temperatures, heat_flow)
        settings = DscAnalysisSettings(
            heat_flow_unit="W/g",
            heating_rate_c_min=10.0,
            endotherm_up=True,
            melt_range=TemperatureRange(100, 200),
            melt_pre_range=TemperatureRange(100, 115),
            melt_post_range=TemperatureRange(185, 200),
        )
        result = analyze_melting(curve, settings)
        expected = 2.0 * 8.0 * math.sqrt(2.0 * math.pi) * 60.0 / 10.0
        self.assertAlmostEqual(result.peak_c, 150.0, delta=0.5)
        self.assertAlmostEqual(result.enthalpy_j_g, expected, delta=1.0)
        self.assertGreater(result.onset_c, 115)
        self.assertLess(result.end_c, 185)

    def test_endotherm_down_keeps_signed_enthalpy_and_displays_positive(self):
        temperatures = tuple(float(value) for value in range(100, 201))
        heat_flow = tuple(-math.exp(-0.5 * ((temp - 150.0) / 7.0) ** 2) for temp in temperatures)
        curve = dsc_curve("down", temperatures, heat_flow)
        settings = DscAnalysisSettings(
            heat_flow_unit="mW/mg",
            heating_rate_c_min=10.0,
            endotherm_up=False,
            melt_range=TemperatureRange(100, 200),
            melt_pre_range=TemperatureRange(100, 115),
            melt_post_range=TemperatureRange(185, 200),
        )
        result = analyze_melting(curve, settings)
        self.assertAlmostEqual(result.peak_c, 150.0, delta=0.5)
        self.assertLess(result.enthalpy_signed_j_g, 0.0)
        self.assertAlmostEqual(result.enthalpy_j_g, abs(result.enthalpy_signed_j_g))

    def test_total_mw_uses_sample_mass(self):
        temperatures = tuple(float(value) for value in range(100, 201))
        specific = tuple(math.exp(-0.5 * ((temp - 150.0) / 7.0) ** 2) for temp in temperatures)
        total_mw = tuple(value * 12.5 for value in specific)
        curve = dsc_curve("total", temperatures, total_mw, unit="mW")
        settings = DscAnalysisSettings(
            heat_flow_unit="mW",
            heating_rate_c_min=10.0,
            sample_mass_mg=12.5,
            melt_range=TemperatureRange(100, 200),
            melt_pre_range=TemperatureRange(100, 115),
            melt_post_range=TemperatureRange(185, 200),
        )
        result = analyze_melting(curve, settings)
        expected = 7.0 * math.sqrt(2.0 * math.pi) * 60.0 / 10.0
        self.assertAlmostEqual(result.enthalpy_j_g, expected, delta=1.0)

    def test_unknown_unit_or_missing_mass_does_not_produce_enthalpy(self):
        temperatures = tuple(float(value) for value in range(100, 201))
        heat_flow = tuple(math.exp(-0.5 * ((temp - 150.0) / 7.0) ** 2) for temp in temperatures)
        curve = dsc_curve("unknown", temperatures, heat_flow, unit=None)
        base = dict(
            heating_rate_c_min=10.0,
            melt_range=TemperatureRange(100, 200),
            melt_pre_range=TemperatureRange(100, 115),
            melt_post_range=TemperatureRange(185, 200),
        )
        unknown = analyze_melting(curve, DscAnalysisSettings(heat_flow_unit=None, **base))
        no_mass = analyze_melting(curve, DscAnalysisSettings(heat_flow_unit="mW", **base))
        self.assertIsNone(unknown.enthalpy_j_g)
        self.assertIsNone(no_mass.enthalpy_j_g)
        self.assertIn("熱流単位を特定できません", unknown.warnings[0])
        self.assertIn("mWデータのため試料重量が必要です", no_mass.warnings[0])

    def test_missing_heating_rate_has_specific_warning(self):
        temperatures = tuple(float(value) for value in range(100, 201))
        heat_flow = tuple(math.exp(-0.5 * ((temp - 150.0) / 7.0) ** 2) for temp in temperatures)
        curve = dsc_curve("missing-rate", temperatures, heat_flow, unit="W/g")
        result = analyze_melting(
            curve,
            DscAnalysisSettings(
                heat_flow_unit="W/g",
                melt_range=TemperatureRange(100, 200),
                melt_pre_range=TemperatureRange(100, 115),
                melt_post_range=TemperatureRange(185, 200),
            ),
        )
        self.assertIsNone(result.enthalpy_j_g)
        self.assertIn("昇温速度が未入力です", result.warnings[0])

    def test_melting_peak_search_is_limited_between_baselines(self):
        temperatures = tuple(value / 2 for value in range(200, 401))
        heat_flow = tuple(
            2.0 * math.exp(-0.5 * ((temp - 150.0) / 7.0) ** 2)
            + 5.0 * math.exp(-0.5 * ((temp - 105.0) / 1.0) ** 2)
            for temp in temperatures
        )
        curve = dsc_curve("search-range", temperatures, heat_flow, unit="W/g")
        result = analyze_melting(
            curve,
            DscAnalysisSettings(
                heat_flow_unit="W/g",
                heating_rate_c_min=10,
                melt_range=TemperatureRange(100, 200),
                melt_pre_range=TemperatureRange(110, 120),
                melt_post_range=TemperatureRange(180, 190),
            ),
        )
        self.assertAlmostEqual(result.peak_c, 150.0, delta=0.5)

    def test_infers_heating_rate_and_suggests_demo_ranges(self):
        temperatures = tuple(float(value) for value in range(20, 301))
        times = tuple((value - 20.0) / 10.0 for value in temperatures)
        heat_flow = tuple(
            -0.15
            + 0.0008 * (temp - 20)
            + 0.18 * math.tanh((temp - 78) / 4)
            + 2.1 * math.exp(-0.5 * ((temp - 150) / 7) ** 2)
            - 0.75 * math.exp(-0.5 * ((temp - 112) / 5.5) ** 2)
            for temp in temperatures
        )
        curve = dsc_curve("demo", temperatures, heat_flow, unit="mW", times=times)
        suggestions = suggest_dsc_ranges(curve)
        self.assertAlmostEqual(infer_heating_rate(curve), 10.0, places=6)
        self.assertIsNotNone(suggestions.tg_range)
        self.assertIsNotNone(suggestions.melt_range)
        self.assertTrue(suggestions.tg_range.contains(78.0))
        self.assertTrue(suggestions.melt_range.contains(150.0))


if __name__ == "__main__":
    unittest.main()
