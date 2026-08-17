import unittest
from pathlib import Path

from tga_analyzer.model import PARTICLE_SIZE, CurveData
from tga_analyzer.particle_size_processing import (
    ParticleSizeCommonSettings,
    ParticleSizeSeriesSettings,
    normalize_particle_size_curve,
    particle_mixed_normalization,
    particle_reference_value,
    process_particle_size_curve,
)
from tga_analyzer.processing import USE_COMMON, USE_INDIVIDUAL, USE_NONE, ProcessingError


def make_curve(
    name: str = "sample",
    diameters=(1.0, 2.0, 4.0),
    frequencies=(2.0, 4.0, 8.0),
) -> CurveData:
    return CurveData(
        path=Path(f"{name}.csv"),
        display_name=name,
        temperatures=(),
        mass_mg=(),
        weight_percent=(),
        measurement_type=PARTICLE_SIZE,
        particle_diameter_um=tuple(diameters),
        volume_frequency_percent=tuple(frequencies),
    )


class ParticleSizeNormalizationTests(unittest.TestCase):
    def test_exact_point_normalizes_reference_to_one_without_mutating_raw(self):
        curve = make_curve()
        raw = curve.volume_frequency_percent
        normalized, reference = normalize_particle_size_curve(curve, 2.0)
        self.assertEqual(reference, 4.0)
        self.assertEqual(normalized, (0.5, 1.0, 2.0))
        self.assertEqual(curve.volume_frequency_percent, raw)

    def test_interpolation_is_linear_in_diameter_not_log_diameter(self):
        curve = make_curve(diameters=(1.0, 4.0), frequencies=(2.0, 8.0))
        reference = particle_reference_value(
            curve.particle_diameter_um,
            curve.volume_frequency_percent,
            2.0,
        )
        self.assertAlmostEqual(reference, 4.0)
        self.assertNotAlmostEqual(reference, 5.0)

    def test_out_of_range_does_not_extrapolate(self):
        curve = make_curve()
        with self.assertRaisesRegex(ProcessingError, "範囲外"):
            normalize_particle_size_curve(curve, 0.5)

    def test_reference_threshold_is_inclusive(self):
        for reference in (1e-6, 0.5e-6):
            curve = make_curve(frequencies=(reference, 1.0, 2.0))
            with self.assertRaisesRegex(ProcessingError, "1×10⁻⁶以下"):
                normalize_particle_size_curve(curve, 1.0)
        curve = make_curve(frequencies=(1.000001e-6, 1.0, 2.0))
        normalized, _reference = normalize_particle_size_curve(curve, 1.0)
        self.assertEqual(normalized[0], 1.0)

    def test_common_individual_none_and_failure_are_isolated(self):
        first = make_curve("first")
        second = make_curve("second", frequencies=(1.0, 3.0, 9.0))
        common = ParticleSizeCommonSettings(normalization_diameter_um=2.0)
        first_setting = ParticleSizeSeriesSettings(normalization_mode=USE_COMMON)
        second_setting = ParticleSizeSeriesSettings(
            normalization_mode=USE_INDIVIDUAL,
            normalization_diameter_um=4.0,
        )
        first_result = process_particle_size_curve(first, common, first_setting)
        second_result = process_particle_size_curve(second, common, second_setting)
        self.assertTrue(first_result.is_normalized)
        self.assertTrue(second_result.is_normalized)
        self.assertEqual(first_result.normalization_diameter_um, 2.0)
        self.assertEqual(second_result.normalization_diameter_um, 4.0)

        none_result = process_particle_size_curve(
            first,
            common,
            ParticleSizeSeriesSettings(normalization_mode=USE_NONE),
        )
        failed_result = process_particle_size_curve(
            second,
            common,
            ParticleSizeSeriesSettings(
                normalization_mode=USE_INDIVIDUAL,
                normalization_diameter_um=0.2,
            ),
        )
        self.assertFalse(none_result.is_normalized)
        self.assertTrue(failed_result.normalization_failed)
        self.assertEqual(failed_result.display_y, second.volume_frequency_percent)
        self.assertTrue(first_result.is_normalized)
        self.assertTrue(particle_mixed_normalization((first_result, failed_result)))


if __name__ == "__main__":
    unittest.main()
