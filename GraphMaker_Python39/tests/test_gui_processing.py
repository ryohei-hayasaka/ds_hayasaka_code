import unittest
from pathlib import Path
from types import SimpleNamespace

from tga_analyzer.dsc_analysis import (
    DscAnalysisSession,
    DscAnalysisSettings,
    TemperatureRange,
)
from tga_analyzer.gui import TgaAnalyzerApp, _load_curve_batch
from tga_analyzer.model import IR, PARTICLE_SIZE, CurveData
from tga_analyzer.particle_size_processing import ParticleSizeSeriesSettings
from tga_analyzer.processing import SeriesProcessingSettings, USE_COMMON, USE_INDIVIDUAL


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeTree:
    def __init__(self):
        self.rows = []

    def get_children(self, _parent=""):
        return tuple(range(len(self.rows)))

    def delete(self, *_items):
        self.rows.clear()

    def tag_configure(self, *_args, **_kwargs):
        pass

    def insert(self, _parent, _position, **kwargs):
        self.rows.append(kwargs["values"])
        return kwargs.get("iid")


class GuiProcessingIntegrationTests(unittest.TestCase):
    def test_deleted_blank_choice_round_trips_without_losing_the_key(self):
        key = r"C:\measurements\IR_blank.csv"
        fake = SimpleNamespace(blank_choice_keys={IR: {}})
        label = TgaAnalyzerApp._blank_choice_for_key(fake, IR, key)
        self.assertEqual(label, f"(削除済み) {key}")
        self.assertEqual(TgaAnalyzerApp._blank_key_from_choice(fake, IR, label), key)

    def test_main_and_graph_tables_use_the_same_processing_labels(self):
        curve = CurveData(
            path=Path("C:/sample.csv"),
            display_name="sample",
            temperatures=(),
            mass_mg=(),
            weight_percent=(),
            measurement_type=IR,
            wavenumbers_cm1=(1800.0, 1600.0),
            absorbance=(0.5, 1.0),
            legend_name="Sample legend",
        )
        main_tree = FakeTree()
        graph_tree = FakeTree()
        fake = SimpleNamespace(
            state_model=SimpleNamespace(ordered_curves=lambda: (curve,)),
            loaded_tree=main_tree,
            _processing_blank=lambda _key: "IR_blank",
            _ir_normalization=lambda _key: "1600 cm⁻¹",
            _particle_normalization=lambda _key: "—",
        )

        TgaAnalyzerApp._populate_loaded_tree(fake, main_tree)
        TgaAnalyzerApp._populate_loaded_tree(fake, graph_tree)

        self.assertEqual(main_tree.rows[0][2:4], ("IR_blank", "1600 cm⁻¹"))
        self.assertEqual(graph_tree.rows[0][3:5], ("IR_blank", "1600 cm⁻¹"))

    def test_graph_click_commits_normalization_and_reprocesses_immediately(self):
        settings = {
            "target": SeriesProcessingSettings(),
            "other": SeriesProcessingSettings(normalization_mode=USE_COMMON),
        }
        calls = []
        fake = SimpleNamespace(
            mode_var=FakeVariable(IR),
            ir_normalization_selection_key="target",
            graph_window=SimpleNamespace(
                plot_canvas=SimpleNamespace(x_from_canvas_point=lambda _x, _y: 1720.5)
            ),
            ir_individual_norm_var=FakeVariable(""),
            series_processing={IR: settings},
            _graph_window_alive=lambda: True,
            _reprocess_mode=lambda mode: calls.append(("reprocess", mode)),
            _load_processing_controls=lambda key: calls.append(("controls", key)),
            _set_status=lambda text: calls.append(("status", text)),
        )

        result = TgaAnalyzerApp._on_plot_left_click(fake, SimpleNamespace(x=10, y=20))

        self.assertEqual(result, "break")
        self.assertEqual(settings["target"].normalization_mode, USE_INDIVIDUAL)
        self.assertEqual(settings["target"].normalization_wavenumber, 1720.5)
        self.assertEqual(settings["other"].normalization_mode, USE_COMMON)
        self.assertIn(("reprocess", IR), calls)
        self.assertIn(("controls", "target"), calls)

    def test_particle_graph_click_commits_only_selected_series(self):
        settings = {
            "target": ParticleSizeSeriesSettings(),
            "other": ParticleSizeSeriesSettings(normalization_mode=USE_COMMON),
        }
        calls = []
        fake = SimpleNamespace(
            mode_var=FakeVariable(PARTICLE_SIZE),
            ir_normalization_selection_key=None,
            particle_normalization_selection_key="target",
            graph_window=SimpleNamespace(
                plot_canvas=SimpleNamespace(x_from_canvas_point=lambda _x, _y: 12.5)
            ),
            particle_individual_norm_var=FakeVariable(""),
            particle_selection_instruction_var=FakeVariable(""),
            particle_series_processing=settings,
            _graph_window_alive=lambda: True,
            _reprocess_particle_size=lambda: calls.append(("reprocess", PARTICLE_SIZE)),
            _load_particle_processing_controls=lambda key: calls.append(("controls", key)),
            _set_status=lambda text: calls.append(("status", text)),
        )

        result = TgaAnalyzerApp._on_plot_left_click(
            fake, SimpleNamespace(x=10, y=20)
        )

        self.assertEqual(result, "break")
        self.assertEqual(settings["target"].normalization_mode, USE_INDIVIDUAL)
        self.assertEqual(settings["target"].normalization_diameter_um, 12.5)
        self.assertEqual(settings["other"].normalization_mode, USE_COMMON)
        self.assertIsNone(settings["other"].normalization_diameter_um)
        self.assertIn(("reprocess", PARTICLE_SIZE), calls)
        self.assertIn(("controls", "target"), calls)

    def test_ir_batch_loader_uses_ir_parser(self):
        curves, errors = _load_curve_batch(
            [Path("demo_data/IR/raw_data/IR_demo_01.csv")],
            IR,
        )
        self.assertFalse(errors)
        self.assertEqual(len(curves), 1)
        self.assertEqual(curves[0].measurement_type, IR)

    def test_blank_change_invalidation_keeps_four_point_ranges(self):
        settings = DscAnalysisSettings(
            tg_range=TemperatureRange(50, 110),
            tg_pre_range=TemperatureRange(50, 65),
            tg_post_range=TemperatureRange(95, 110),
            melt_range=TemperatureRange(100, 200),
            melt_pre_range=TemperatureRange(100, 115),
            melt_post_range=TemperatureRange(185, 200),
        )
        session = DscAnalysisSession(
            settings=settings,
            decision="採用",
            status="解析済み",
            warnings=["old"],
            overrides={"tg_onset": 75.0},
        )

        class FakeApp:
            dsc_sessions = {"curve": session}

        TgaAnalyzerApp._invalidate_dsc_sessions(FakeApp(), {"curve"})

        self.assertEqual(session.settings.tg_range, TemperatureRange(50, 110))
        self.assertEqual(session.settings.melt_range, TemperatureRange(100, 200))
        self.assertIsNone(session.tg_result)
        self.assertIsNone(session.melting_result)
        self.assertEqual(session.decision, "候補")
        self.assertFalse(session.overrides)
        self.assertIn("再計算", session.status)


if __name__ == "__main__":
    unittest.main()
