import unittest

from tga_analyzer.series_dialogs import ColorEditorDialog, LegendEditorDialog
from tga_analyzer.series_edit import (
    PAPER_COLOR_PALETTE,
    ColorEditSession,
    LegendEditSession,
    is_color_column,
    is_legend_column,
    navigate_legend_index,
)


class ColorEditTests(unittest.TestCase):
    def test_only_color_cell_starts_direct_color_edit(self):
        self.assertTrue(is_color_column("#1"))
        for column in ("#2", "#3", "#4", ""):
            self.assertFalse(is_color_column(column))

    def test_legend_cell_starts_direct_legend_edit(self):
        self.assertTrue(is_legend_column("#3"))
        for column in ("#1", "#2", "#4", ""):
            self.assertFalse(is_legend_column(column))

    def test_individual_and_multiple_colors_are_staged(self):
        session = ColorEditSession(
            {"a": "#112233", "b": "#445566", "c": "#778899"}
        )
        session.apply(("b",), "#abcdef")
        self.assertEqual(session.pending["a"], "#112233")
        self.assertEqual(session.pending["b"], "#ABCDEF")
        self.assertEqual(session.pending["c"], "#778899")

        session.apply(("a", "c"), "#0072b2")
        self.assertEqual(session.pending["a"], "#0072B2")
        self.assertEqual(session.pending["c"], "#0072B2")
        self.assertEqual(
            session.changed(),
            {"a": "#0072B2", "b": "#ABCDEF", "c": "#0072B2"},
        )

    def test_cancel_reset_restores_all_original_colors(self):
        session = ColorEditSession({"a": "#112233", "b": "#445566"})
        session.apply(("a", "b"), "#000000")
        session.reset()
        self.assertEqual(session.pending, session.original)
        self.assertEqual(session.changed(), {})

    def test_paper_palette_is_high_contrast_and_unique(self):
        self.assertGreaterEqual(len(PAPER_COLOR_PALETTE), 8)
        self.assertEqual(len(PAPER_COLOR_PALETTE), len(set(PAPER_COLOR_PALETTE)))
        self.assertIn("#000000", PAPER_COLOR_PALETTE)
        self.assertIn("#0072B2", PAPER_COLOR_PALETTE)

    def test_color_dialog_shows_selection_current_and_pending_color(self):
        self.assertEqual(
            ColorEditorDialog.COLUMNS,
            ("selected", "current", "pending", "legend", "name"),
        )


class LegendEditTests(unittest.TestCase):
    def make_session(self) -> LegendEditSession:
        return LegendEditSession(
            order=("a", "b", "c"),
            original={"a": "Alpha", "b": "Beta", "c": "Gamma"},
        )

    def test_all_series_are_kept_in_order_and_trimmed(self):
        session = self.make_session()
        self.assertEqual(session.order, ("a", "b", "c"))
        self.assertEqual(session.set_name("b", "  Edited Beta  "), "Edited Beta")
        self.assertEqual(session.pending["a"], "Alpha")
        self.assertEqual(session.pending["b"], "Edited Beta")
        self.assertEqual(session.pending["c"], "Gamma")

    def test_empty_legend_is_not_adopted(self):
        session = self.make_session()
        with self.assertRaisesRegex(ValueError, "空"):
            session.set_name("b", "   ")
        self.assertEqual(session.pending["b"], "Beta")

    def test_multiline_paste_updates_consecutive_legends(self):
        session = self.make_session()
        last = session.paste_lines(0, " First \r\nSecond\n Third ")
        self.assertEqual(last, 2)
        self.assertEqual(
            session.pending, {"a": "First", "b": "Second", "c": "Third"}
        )

    def test_blank_pasted_line_preserves_existing_legend(self):
        session = self.make_session()
        session.paste_lines(0, "First\n   \nThird")
        self.assertEqual(session.pending["b"], "Beta")

    def test_cancel_reset_discards_all_staged_legend_changes(self):
        session = self.make_session()
        session.set_name("a", "Changed A")
        session.set_name("b", "Changed B")
        session.reset()
        self.assertEqual(session.pending, session.original)
        self.assertEqual(session.changed(), {})

    def test_enter_shift_enter_arrows_and_tab_navigation(self):
        self.assertEqual(navigate_legend_index(0, 3, "down"), 1)
        self.assertEqual(navigate_legend_index(1, 3, "up"), 0)
        self.assertEqual(navigate_legend_index(1, 3, "right"), 2)
        self.assertEqual(navigate_legend_index(1, 3, "left"), 0)
        self.assertEqual(navigate_legend_index(1, 3, "next"), 2)
        self.assertEqual(navigate_legend_index(1, 3, "previous"), 0)
        self.assertEqual(navigate_legend_index(2, 3, "next"), 2)
        self.assertEqual(navigate_legend_index(0, 3, "previous"), 0)

    def test_legend_dialog_has_editable_legend_column_for_all_rows(self):
        self.assertEqual(
            LegendEditorDialog.COLUMNS, ("color", "name", "legend", "source")
        )


if __name__ == "__main__":
    unittest.main()
