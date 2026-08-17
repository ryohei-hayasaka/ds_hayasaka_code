import unittest
from pathlib import Path

from tga_analyzer.model import DSC, IR, CurveData
from tga_analyzer.processing import (
    BLANK_CORRECTED,
    BLANK_FAILED,
    NORMALIZED,
    NORMALIZATION_FAILED,
    CommonProcessingSettings,
    ProcessingError,
    SeriesProcessingSettings,
    USE_INDIVIDUAL,
    USE_NONE,
    calculate_overlap,
    interpolate_value,
    ir_mixed_normalization,
    normalize_ir_curve,
    process_dsc_curve,
    process_ir_curve,
    blank_reference_name,
    resolve_effective_blank,
    resolve_effective_normalization,
    subtract_dsc_blank,
    subtract_ir_blank,
)


def ir_curve(name, xs=(4000.0, 3000.0, 2000.0, 1000.0), ys=(0.4, 0.3, 0.2, 0.1)):
    return CurveData(
        path=Path(f"C:/{name}.csv"), display_name=name, temperatures=(), mass_mg=(),
        weight_percent=(), measurement_type=IR, wavenumbers_cm1=tuple(xs), absorbance=tuple(ys),
    )


def dsc_curve(name, xs=(20.0, 30.0, 40.0), ys=(1.0, 2.0, 3.0), unit="mW"):
    return CurveData(
        path=Path(f"C:/{name}.csv"), display_name=name, temperatures=tuple(xs), mass_mg=(),
        weight_percent=(), measurement_type=DSC, heat_flow_mw=tuple(ys),
        heat_flow_unit=unit, source_heat_flow_header="HeatFlow_mW",
    )


class InterpolationTests(unittest.TestCase):
    def test_interpolates_descending_grid_without_extrapolation(self):
        self.assertAlmostEqual(interpolate_value((4, 2, 0), (8, 4, 0), 3), 6)
        with self.assertRaisesRegex(ProcessingError, "データ範囲"):
            interpolate_value((4, 2, 0), (8, 4, 0), 5)

    def test_overlap_returns_only_shared_range(self):
        self.assertEqual(calculate_overlap((4000, 2000), (3000, 1000)), (2000.0, 3000.0))


class IrProcessingTests(unittest.TestCase):
    def test_removed_blank_keeps_configured_file_name(self):
        sample = ir_curve("sample")
        missing_key = str(Path("C:/measurements/IR_blank.csv"))
        failed = process_ir_curve(
            sample,
            CommonProcessingSettings(blank_key=missing_key),
            SeriesProcessingSettings(),
            {sample.key: sample},
        )
        self.assertEqual(failed.status, BLANK_FAILED)
        self.assertEqual(failed.blank_name, "IR_blank.csv")
        self.assertEqual(blank_reference_name(missing_key, {}), "IR_blank.csv")

    def test_blank_failure_keeps_requested_normalization_as_unapplied(self):
        sample = ir_curve("sample")
        failed = process_ir_curve(
            sample,
            CommonProcessingSettings(
                blank_key="missing_blank.csv", normalization_wavenumber=1600.0
            ),
            SeriesProcessingSettings(),
            {sample.key: sample},
        )
        self.assertEqual(failed.blank_name, "missing_blank.csv")
        self.assertEqual(failed.normalization_wavenumber, 1600.0)
        self.assertTrue(failed.normalization_failed)

    def test_same_grid_and_interpolated_blank_subtraction(self):
        sample = ir_curve("sample", ys=(0.5, 0.4, 0.3, 0.2))
        blank = ir_curve("blank", ys=(0.1, 0.1, 0.1, 0.1))
        xs, ys = subtract_ir_blank(sample, blank)
        self.assertEqual(xs, sample.wavenumbers_cm1)
        self.assertEqual(tuple(round(v, 8) for v in ys), (0.4, 0.3, 0.2, 0.1))
        short = ir_curve("short", (3500, 2500, 1500), (0.15, 0.1, 0.05))
        xs, ys = subtract_ir_blank(sample, short)
        self.assertEqual(xs, (3000.0, 2000.0))
        self.assertAlmostEqual(ys[0], 0.275)
        self.assertAlmostEqual(ys[1], 0.225)

    def test_common_individual_and_none_precedence(self):
        common = CommonProcessingSettings(blank_key="common", normalization_wavenumber=1600)
        self.assertEqual(resolve_effective_blank(common, SeriesProcessingSettings()), "common")
        self.assertIsNone(resolve_effective_blank(common, SeriesProcessingSettings(blank_mode=USE_NONE)))
        own = SeriesProcessingSettings(
            blank_mode=USE_INDIVIDUAL, blank_key="own",
            normalization_mode=USE_INDIVIDUAL, normalization_wavenumber=1700,
        )
        self.assertEqual(resolve_effective_blank(common, own), "own")
        self.assertEqual(resolve_effective_normalization(common, own), 1700)
        self.assertIsNone(resolve_effective_normalization(common, SeriesProcessingSettings(normalization_mode=USE_NONE)))

    def test_exact_and_interpolated_normalization_reference_become_one(self):
        curve = ir_curve("sample")
        exact = normalize_ir_curve(curve, 3000)
        self.assertAlmostEqual(exact[1], 1.0)
        interpolated = normalize_ir_curve(curve, 2500)
        self.assertAlmostEqual(interpolate_value(curve.plot_x, interpolated, 2500), 1.0)

    def test_normalization_threshold(self):
        allowed = ir_curve("allowed", ys=(0.002, 0.001, 0.002, 0.003))
        self.assertAlmostEqual(normalize_ir_curve(allowed, 3000)[1], 1.0)
        for value, message in ((-0.1, "負"), (0.0, "0.001未満"), (0.0009, "0.001未満")):
            with self.assertRaisesRegex(ProcessingError, message):
                normalize_ir_curve(ir_curve("bad", ys=(0.2, value, 0.2, 0.3)), 3000)

    def test_processing_order_is_blank_then_normalization(self):
        sample = ir_curve("sample", ys=(0.6, 0.5, 0.4, 0.3))
        blank = ir_curve("blank", ys=(0.1, 0.1, 0.1, 0.1))
        result = process_ir_curve(
            sample,
            CommonProcessingSettings(blank_key=blank.key, normalization_wavenumber=3000),
            SeriesProcessingSettings(), {sample.key: sample, blank.key: blank},
        )
        self.assertEqual(result.status, NORMALIZED)
        self.assertAlmostEqual(result.blank_corrected_y[1], 0.4)
        self.assertAlmostEqual(result.display_y[1], 1.0)

    def test_blank_uses_raw_data_and_failures_fallback_explicitly(self):
        sample = ir_curve("sample", ys=(0.5, 0.4, 0.3, 0.2))
        blank = ir_curve("blank", ys=(0.1, 0.1, 0.1, 0.1))
        settings = {
            sample.key: SeriesProcessingSettings(blank_mode=USE_INDIVIDUAL, blank_key=blank.key, normalization_mode=USE_NONE),
            blank.key: SeriesProcessingSettings(normalization_mode=USE_INDIVIDUAL, normalization_wavenumber=3000),
        }
        corrected = process_ir_curve(sample, CommonProcessingSettings(), settings[sample.key], {sample.key: sample, blank.key: blank}, settings)
        self.assertEqual(corrected.status, BLANK_CORRECTED)
        self.assertAlmostEqual(corrected.display_y[0], 0.4)
        failed = process_ir_curve(sample, CommonProcessingSettings(blank_key="missing", normalization_wavenumber=3000), SeriesProcessingSettings(), {sample.key: sample})
        self.assertEqual(failed.status, BLANK_FAILED)
        self.assertEqual(failed.display_y, sample.absorbance)
        self.assertIn("削除済み", failed.warnings[0])

    def test_blank_name_is_retained_when_known_blank_processing_fails(self):
        sample = ir_curve("sample")
        blank = ir_curve("blank", xs=(9000, 8000, 7000, 6000))
        failed = process_ir_curve(
            sample,
            CommonProcessingSettings(blank_key=blank.key),
            SeriesProcessingSettings(),
            {sample.key: sample, blank.key: blank},
        )
        self.assertEqual(failed.status, BLANK_FAILED)
        self.assertEqual(failed.blank_name, "blank")

    def test_normalization_failure_keeps_unnormalized_and_cycle_is_reported(self):
        sample = ir_curve("sample", ys=(0.2, 0.0, 0.2, 0.3))
        failed = process_ir_curve(sample, CommonProcessingSettings(normalization_wavenumber=3000), SeriesProcessingSettings(), {sample.key: sample})
        self.assertEqual(failed.status, NORMALIZATION_FAILED)
        self.assertEqual(failed.display_y, sample.absorbance)
        a, b = ir_curve("a"), ir_curve("b")
        settings = {
            a.key: SeriesProcessingSettings(blank_mode=USE_INDIVIDUAL, blank_key=b.key),
            b.key: SeriesProcessingSettings(blank_mode=USE_INDIVIDUAL, blank_key=a.key),
        }
        cyclic = process_ir_curve(a, CommonProcessingSettings(), settings[a.key], {a.key: a, b.key: b}, settings)
        self.assertEqual(cyclic.status, BLANK_FAILED)
        self.assertIn("循環", cyclic.warnings[0])

    def test_mixed_normalization_is_detected(self):
        raw_curve = ir_curve("raw")
        norm_curve = ir_curve("norm")
        raw = process_ir_curve(raw_curve, CommonProcessingSettings(), SeriesProcessingSettings(normalization_mode=USE_NONE), {raw_curve.key: raw_curve})
        norm = process_ir_curve(norm_curve, CommonProcessingSettings(normalization_wavenumber=3000), SeriesProcessingSettings(), {norm_curve.key: norm_curve})
        self.assertTrue(ir_mixed_normalization((raw, norm)))


class DscProcessingTests(unittest.TestCase):
    def test_subtracts_mw_with_interpolation_and_overlap(self):
        sample = dsc_curve("sample", (20, 30, 40), (2, 4, 6))
        blank = dsc_curve("blank", (25, 35, 45), (1, 2, 3))
        xs, ys = subtract_dsc_blank(sample, blank)
        self.assertEqual(xs, (30.0, 40.0))
        self.assertEqual(ys, (2.5, 3.5))

    def test_rejects_unit_direction_and_falls_back_raw(self):
        sample = dsc_curve("sample")
        bad = dsc_curve("blank", unit="W/g")
        with self.assertRaisesRegex(ProcessingError, "単位"):
            subtract_dsc_blank(sample, bad)
        with self.assertRaisesRegex(ProcessingError, "方向"):
            subtract_dsc_blank(sample, dsc_curve("reverse", (40, 30, 20), (1, 2, 3)))
        result = process_dsc_curve(sample, CommonProcessingSettings(blank_key=bad.key), SeriesProcessingSettings(), {sample.key: sample, bad.key: bad})
        self.assertEqual(result.status, BLANK_FAILED)
        self.assertEqual(result.display_y, sample.heat_flow_mw)
        self.assertIn("単位", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
