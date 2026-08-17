import unittest
from pathlib import Path

from tga_analyzer.model import AxisRange, CurveData
from tga_analyzer.plot_canvas import (
    MeasurementPlotCanvas,
    canvas_point_to_x,
    canvas_point_to_temperature,
    clip_segment,
    logarithmic_ticks,
    nice_ticks,
)


class PlottingTests(unittest.TestCase):
    def test_drawn_legend_uses_edited_legend_name(self):
        curve = CurveData(
            path=Path("sample.csv"),
            display_name="sample",
            legend_name="Edited legend",
            temperatures=(20.0, 30.0),
            mass_mg=(10.0, 9.0),
            weight_percent=(100.0, 90.0),
        )

        class FakeCanvas:
            _curves = (curve,)

            def __init__(self):
                self.texts = []

            def create_rectangle(self, *args, **kwargs):
                return None

            def create_line(self, *args, **kwargs):
                return None

            def create_text(self, *args, **kwargs):
                self.texts.append(kwargs.get("text"))

        canvas = FakeCanvas()
        MeasurementPlotCanvas._draw_legend(canvas, 600, 20)

        self.assertIn("Edited legend", canvas.texts)
        self.assertNotIn("sample", canvas.texts)

    def test_nice_ticks_stay_within_range(self):
        ticks = nice_ticks(25, 800, 8)
        self.assertTrue(ticks)
        self.assertGreaterEqual(ticks[0], 25)
        self.assertLessEqual(ticks[-1], 800)

    def test_clip_segment_crossing_plot(self):
        bounds = AxisRange(0, 10, 0, 10)
        clipped = clip_segment(-5, 5, 15, 5, bounds)
        self.assertEqual(clipped, (0, 5.0, 10, 5.0))

    def test_clip_segment_outside_plot(self):
        bounds = AxisRange(0, 10, 0, 10)
        self.assertIsNone(clip_segment(-5, -5, -1, -1, bounds))

    def test_canvas_coordinate_converts_to_temperature(self):
        axis = AxisRange(0, 300, -1, 3)
        # Plot area is x=78..972 for a 1000 px wide canvas.
        self.assertAlmostEqual(
            canvas_point_to_temperature(525, 200, 1000, 500, axis),
            150.0,
            places=6,
        )
        self.assertIsNone(canvas_point_to_temperature(40, 200, 1000, 500, axis))
        self.assertIsNone(canvas_point_to_temperature(525, 20, 1000, 500, axis))

    def test_canvas_coordinate_uses_changed_axis_range(self):
        first = canvas_point_to_temperature(525, 200, 1000, 500, AxisRange(0, 300, -1, 3))
        changed = canvas_point_to_temperature(525, 200, 1000, 500, AxisRange(50, 250, -2, 4))
        self.assertAlmostEqual(first, 150.0, places=6)
        self.assertAlmostEqual(changed, 150.0, places=6)
        quarter = canvas_point_to_temperature(301.5, 200, 1000, 500, AxisRange(50, 250, -2, 4))
        self.assertAlmostEqual(quarter, 100.0, places=6)

    def test_ir_canvas_coordinate_is_high_wavenumber_on_left(self):
        axis = AxisRange(400, 4000, -0.1, 1.0)
        self.assertAlmostEqual(
            canvas_point_to_x(78, 200, 1000, 500, axis, reverse_x=True),
            4000.0,
        )
        self.assertAlmostEqual(
            canvas_point_to_x(972, 200, 1000, 500, axis, reverse_x=True),
            400.0,
        )
        changed = AxisRange(1000, 3000, -0.1, 1.0)
        self.assertAlmostEqual(
            canvas_point_to_x(301.5, 200, 1000, 500, changed, reverse_x=True),
            2500.0,
        )

    def test_logarithmic_canvas_coordinate_places_decades_evenly(self):
        axis = AxisRange(0.1, 1000.0, 0.0, 10.0)
        left, right = 78.0, 972.0
        plot_width = right - left
        for index, expected in enumerate((0.1, 1.0, 10.0, 100.0, 1000.0)):
            actual = canvas_point_to_x(
                left + plot_width * index / 4,
                200,
                1000,
                500,
                axis,
                logarithmic_x=True,
            )
            self.assertAlmostEqual(actual, expected, places=8)

    def test_logarithmic_coordinate_respects_axis_changes_and_rejects_zero(self):
        actual = canvas_point_to_x(
            525,
            200,
            1000,
            500,
            AxisRange(1.0, 100.0, 0.0, 10.0),
            logarithmic_x=True,
        )
        self.assertAlmostEqual(actual, 10.0, places=8)
        self.assertIsNone(
            canvas_point_to_x(
                525,
                200,
                1000,
                500,
                AxisRange(0.0, 100.0, 0.0, 10.0),
                logarithmic_x=True,
            )
        )
        majors, _minors = logarithmic_ticks(0.1, 1000.0)
        self.assertEqual(majors, [0.1, 1.0, 10.0, 100.0, 1000.0])


if __name__ == "__main__":
    unittest.main()
