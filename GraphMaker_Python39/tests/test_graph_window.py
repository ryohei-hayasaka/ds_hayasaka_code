import unittest

from tga_analyzer.graph_window import (
    GRAPH_CURVE_COLUMNS,
    ensure_single_window,
    visible_graph_curve_columns,
)
from tga_analyzer.gui import (
    MAIN_CURVE_COLUMNS,
    apply_clicked_particle_normalization,
    apply_clicked_ir_normalization,
    ir_normalization_label,
    processing_blank_label,
    particle_size_normalization_label,
    visible_main_curve_columns,
)
from tga_analyzer.model import DSC, GPC, IR, PARTICLE_SIZE, TGA, UV_VIS
from tga_analyzer.particle_size_processing import (
    ParticleSizeProcessedData,
    ParticleSizeSeriesSettings,
)
from tga_analyzer.processing import (
    NORMALIZATION_FAILED,
    ProcessedCurveData,
    SeriesProcessingSettings,
    USE_COMMON,
    USE_INDIVIDUAL,
)


class FakeWindow:
    def __init__(self, shared_state: dict[str, object], *, alive: bool = True) -> None:
        self.shared_state = shared_state
        self.alive = alive
        self.present_count = 0

    def is_alive(self) -> bool:
        return self.alive

    def present(self) -> None:
        self.present_count += 1


class GraphWindowLifecycleTests(unittest.TestCase):
    def test_curve_list_columns_hide_point_count_and_graph_adds_legend_name(self):
        self.assertEqual(
            MAIN_CURVE_COLUMNS,
            (
                "color",
                "name",
                "blank",
                "normalization",
                "particle_normalization",
                "source",
            ),
        )
        self.assertEqual(
            GRAPH_CURVE_COLUMNS,
            (
                "color",
                "name",
                "legend",
                "blank",
                "normalization",
                "particle_normalization",
                "source",
            ),
        )
        self.assertNotIn("points", MAIN_CURVE_COLUMNS)
        self.assertNotIn("points", GRAPH_CURVE_COLUMNS)
        self.assertNotIn("status", MAIN_CURVE_COLUMNS)
        self.assertNotIn("status", GRAPH_CURVE_COLUMNS)

    def test_processing_columns_are_shown_only_in_relevant_modes(self):
        self.assertEqual(visible_main_curve_columns(TGA), ("color", "name", "source"))
        self.assertEqual(
            visible_main_curve_columns(UV_VIS), ("color", "name", "source")
        )
        self.assertEqual(
            visible_main_curve_columns(GPC), ("color", "name", "source")
        )
        self.assertEqual(
            visible_main_curve_columns(DSC), ("color", "name", "blank", "source")
        )
        self.assertEqual(visible_main_curve_columns(IR), MAIN_CURVE_COLUMNS)
        self.assertEqual(
            visible_main_curve_columns(PARTICLE_SIZE),
            ("color", "name", "particle_normalization", "source"),
        )
        self.assertEqual(
            visible_graph_curve_columns(TGA), ("color", "name", "legend", "source")
        )
        self.assertEqual(
            visible_graph_curve_columns(UV_VIS),
            ("color", "name", "legend", "source"),
        )
        self.assertEqual(
            visible_graph_curve_columns(GPC),
            ("color", "name", "legend", "source"),
        )
        self.assertEqual(
            visible_graph_curve_columns(DSC),
            ("color", "name", "legend", "blank", "source"),
        )
        self.assertEqual(visible_graph_curve_columns(IR), GRAPH_CURVE_COLUMNS)
        self.assertEqual(
            visible_graph_curve_columns(PARTICLE_SIZE),
            ("color", "name", "legend", "particle_normalization", "source"),
        )

    def test_blank_and_ir_normalization_labels_show_processing_sources(self):
        processed = object.__new__(ProcessedCurveData)
        object.__setattr__(processed, "blank_name", "IR_blank")
        object.__setattr__(processed, "blank_failed", False)
        object.__setattr__(processed, "normalization_wavenumber", 1600.0)
        self.assertEqual(processing_blank_label(processed), "IR_blank")
        self.assertEqual(ir_normalization_label(processed), "1600 cm⁻¹")
        self.assertEqual(processing_blank_label(None), "なし")
        self.assertEqual(ir_normalization_label(None), "なし")

    def test_failed_ir_normalization_keeps_target_and_marks_it_unapplied(self):
        processed = object.__new__(ProcessedCurveData)
        object.__setattr__(processed, "normalization_wavenumber", 1720.5)
        object.__setattr__(processed, "normalization_failed", True)
        object.__setattr__(processed, "status", NORMALIZATION_FAILED)
        self.assertEqual(ir_normalization_label(processed), "1720.5 cm⁻¹（未適用）")

    def test_graph_selected_normalization_updates_only_target_series(self):
        settings = {
            "first": SeriesProcessingSettings(),
            "second": SeriesProcessingSettings(
                normalization_mode=USE_COMMON,
                normalization_wavenumber=None,
            ),
        }

        changed = apply_clicked_ir_normalization(settings, "second", 1600.0)

        self.assertEqual(changed.normalization_mode, USE_INDIVIDUAL)
        self.assertEqual(changed.normalization_wavenumber, 1600.0)
        self.assertEqual(settings["first"].normalization_mode, USE_COMMON)
        self.assertIsNone(settings["first"].normalization_wavenumber)

    def test_particle_normalization_label_and_click_update_only_target(self):
        processed = object.__new__(ParticleSizeProcessedData)
        object.__setattr__(processed, "normalization_diameter_um", 10.0)
        object.__setattr__(processed, "normalization_failed", True)
        self.assertEqual(
            particle_size_normalization_label(processed), "10 µm（未適用）"
        )
        settings = {
            "first": ParticleSizeSeriesSettings(),
            "second": ParticleSizeSeriesSettings(),
        }
        changed = apply_clicked_particle_normalization(settings, "second", 12.5)
        self.assertEqual(changed.normalization_mode, USE_INDIVIDUAL)
        self.assertEqual(changed.normalization_diameter_um, 12.5)
        self.assertEqual(settings["first"].normalization_mode, USE_COMMON)
        self.assertIsNone(settings["first"].normalization_diameter_um)

    def test_repeated_open_presents_the_existing_window(self):
        state = {"curves": ["sample.csv"], "axis": (20, 300, -1, 2)}
        current = FakeWindow(state)
        factory_calls = 0

        def factory() -> FakeWindow:
            nonlocal factory_calls
            factory_calls += 1
            return FakeWindow(state)

        opened, created = ensure_single_window(current, factory)

        self.assertIs(opened, current)
        self.assertFalse(created)
        self.assertEqual(factory_calls, 0)
        self.assertEqual(current.present_count, 1)

    def test_reopen_after_close_uses_the_same_external_analysis_state(self):
        state = {
            "curves": ["sample.csv"],
            "color": "#112233",
            "legend_name": "Edited legend",
            "dsc_decision": "採用",
            "annotations": {"tg_range": False},
        }
        closed = FakeWindow(state, alive=False)

        reopened, created = ensure_single_window(closed, lambda: FakeWindow(state))

        self.assertTrue(created)
        self.assertIsNot(reopened, closed)
        self.assertIs(reopened.shared_state, state)
        self.assertEqual(reopened.shared_state["legend_name"], "Edited legend")
        self.assertEqual(reopened.shared_state["dsc_decision"], "採用")
        self.assertEqual(reopened.shared_state["annotations"], {"tg_range": False})


if __name__ == "__main__":
    unittest.main()
