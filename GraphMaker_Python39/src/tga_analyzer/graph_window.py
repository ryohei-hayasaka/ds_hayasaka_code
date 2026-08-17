from __future__ import annotations

from typing import Union

import tkinter as tk
from collections.abc import Callable
from tkinter import colorchooser, messagebox, ttk
from typing import TYPE_CHECKING, Protocol, TypeVar

from .branding import APP_DISPLAY_NAME
from .model import DSC, IR, PARTICLE_SIZE
from .plot_canvas import MeasurementPlotCanvas
from .series_dialogs import ColorEditorDialog, LegendEditorDialog
from .series_edit import is_color_column, is_legend_column

if TYPE_CHECKING:
    from .gui import TgaAnalyzerApp


class ManagedWindow(Protocol):
    def is_alive(self) -> bool: ...

    def present(self) -> None: ...


WindowT = TypeVar("WindowT", bound=ManagedWindow)

GRAPH_CURVE_COLUMNS = (
    "color",
    "name",
    "legend",
    "blank",
    "normalization",
    "particle_normalization",
    "source",
)
GRAPH_CURVE_HEADINGS = {
    "color": ("色", 80, False),
    "name": ("系列名", 220, True),
    "legend": ("凡例名", 220, True),
    "blank": ("ブランク", 190, True),
    "normalization": ("規格化波数", 130, False),
    "particle_normalization": ("規格化粒径", 140, False),
    "source": ("元ファイル", 460, True),
}


def visible_graph_curve_columns(measurement_type: str) -> tuple[str, ...]:
    """Return the graph table columns relevant to the active mode."""
    if measurement_type == IR:
        return GRAPH_CURVE_COLUMNS
    if measurement_type == DSC:
        return ("color", "name", "legend", "blank", "source")
    if measurement_type == PARTICLE_SIZE:
        return ("color", "name", "legend", "particle_normalization", "source")
    return ("color", "name", "legend", "source")


def ensure_single_window(
    current: Union[WindowT, None], factory: Callable[[], WindowT]
) -> tuple[WindowT, bool]:
    if current is not None and current.is_alive():
        current.present()
        return current, False
    return factory(), True


class GraphWindow(tk.Toplevel):
    def __init__(self, app: TgaAnalyzerApp) -> None:
        super().__init__(app)
        self.app = app
        self.title(f"グラフ — {APP_DISPLAY_NAME}")
        self.geometry("1220x760")
        self.minsize(900, 580)
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.color_editor: Union[ColorEditorDialog, None] = None
        self.legend_editor: Union[LegendEditorDialog, None] = None
        self.inline_legend_editor: Union[ttk.Entry, None] = None
        self.inline_legend_item: Union[str, None] = None

        self._build_axis_toolbar()
        self._build_plot()
        self._build_curve_list()
        self._build_dsc_controls()
        self._build_ir_controls()
        self._build_particle_size_controls()
        self.bind("<Escape>", self.app._on_escape_pressed)
        self.update_mode(self.app.mode_var.get())

    def _build_axis_toolbar(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="表示範囲", style="Section.TLabel").grid(
            row=0, column=0, padx=(0, 8)
        )
        variables = (
            ("X最小", self.app.x_min_var),
            ("X最大", self.app.x_max_var),
            ("Y最小", self.app.y_min_var),
            ("Y最大", self.app.y_max_var),
        )
        column = 1
        for label, variable in variables:
            ttk.Label(toolbar, text=label).grid(row=0, column=column, padx=(4, 2))
            ttk.Entry(toolbar, textvariable=variable, width=10).grid(
                row=0, column=column + 1
            )
            column += 2
        ttk.Button(toolbar, text="適用", command=self.app._apply_manual_range).grid(
            row=0, column=column, padx=(10, 4)
        )
        ttk.Button(
            toolbar,
            text="自動範囲",
            command=self.app._apply_auto_range,
        ).grid(row=0, column=column + 1)

    def _build_plot(self) -> None:
        self.plot_canvas = MeasurementPlotCanvas(self)
        self.plot_canvas.grid(row=1, column=0, sticky="nsew", padx=8)
        self.plot_canvas.bind("<Button-1>", self.app._on_plot_left_click, add="+")
        self.plot_canvas.bind("<Button-3>", self.app._on_plot_right_click, add="+")

    def _build_curve_list(self) -> None:
        loaded_frame = ttk.LabelFrame(self, text="グラフ上の曲線", padding=5)
        loaded_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(7, 0))
        loaded_frame.columnconfigure(0, weight=1)
        loaded_frame.rowconfigure(0, weight=1)
        self.loaded_tree = ttk.Treeview(
            loaded_frame,
            columns=GRAPH_CURVE_COLUMNS,
            show="headings",
            height=4,
            selectmode="extended",
        )
        for column_name, (text, width, stretch) in GRAPH_CURVE_HEADINGS.items():
            self.loaded_tree.heading(column_name, text=text)
            self.loaded_tree.column(column_name, width=width, stretch=stretch)
        self.loaded_tree.configure(
            displaycolumns=visible_graph_curve_columns(self.app.mode_var.get())
        )
        scroll = ttk.Scrollbar(
            loaded_frame, orient=tk.VERTICAL, command=self.loaded_tree.yview
        )
        self.loaded_tree.configure(yscrollcommand=scroll.set)
        self.loaded_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.loaded_tree.bind(
            "<<TreeviewSelect>>", self.app._on_graph_loaded_curve_selected
        )
        self.loaded_tree.bind("<Double-1>", self._on_curve_table_double_click, add="+")
        self.loaded_tree.bind("<F2>", self.open_legend_editor, add="+")
        self.loaded_tree.bind("<Return>", self.open_legend_editor, add="+")

        buttons = ttk.Frame(loaded_frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(
            buttons,
            text="色を変更",
            command=self.open_color_editor,
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="凡例名を編集",
            command=self.open_legend_editor,
        ).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(
            buttons,
            text="再読込",
            command=lambda: self.app._reload_selected_curves(self.loaded_tree),
        ).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(
            buttons,
            text="グラフから削除",
            command=lambda: self.app._remove_selected_curves(self.loaded_tree),
        ).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(
            buttons,
            text="Excelへ出力",
            style="Accent.TButton",
            command=lambda: self.app._export_excel(parent=self),
        ).pack(side=tk.RIGHT)

    @staticmethod
    def _dialog_alive(dialog: Union[tk.Toplevel, None]) -> bool:
        if dialog is None:
            return False
        try:
            return bool(dialog.winfo_exists())
        except tk.TclError:
            return False

    def _restore_focus(self) -> None:
        try:
            self.lift()
            self.focus_set()
        except tk.TclError:
            pass

    def _on_curve_table_double_click(self, event):
        column = self.loaded_tree.identify_column(event.x)
        item = self.loaded_tree.identify_row(event.y)
        key = self.app.loaded_item_keys.get(item)
        if key is None or key not in self.app.state_model.curves:
            return None
        self.loaded_tree.selection_set(item)
        self.loaded_tree.focus(item)
        if is_legend_column(column):
            self._begin_inline_legend_edit(item)
            return "break"
        if not is_color_column(column):
            return None
        current = self.app.state_model.curves[key].color
        _rgb, color = colorchooser.askcolor(
            color=current,
            title="系列色を選択",
            parent=self,
        )
        self._restore_focus()
        if color:
            self.app._apply_curve_colors({key: color})
        return "break"

    def _begin_inline_legend_edit(self, item: str) -> None:
        self.cancel_inline_legend_edit()
        bbox = self.loaded_tree.bbox(item, "legend")
        if not bbox:
            return
        x, y, width, height = bbox
        editor = ttk.Entry(self.loaded_tree)
        editor.insert(0, self.loaded_tree.set(item, "legend"))
        editor.select_range(0, tk.END)
        editor.place(x=x, y=y, width=width, height=height)
        editor.bind("<Return>", self._commit_inline_legend_edit)
        editor.bind("<Escape>", self._cancel_inline_legend_edit_event)
        editor.bind("<FocusOut>", self._commit_inline_legend_edit)
        self.inline_legend_editor = editor
        self.inline_legend_item = item
        editor.focus_set()

    def _commit_inline_legend_edit(self, _event=None):
        editor = self.inline_legend_editor
        item = self.inline_legend_item
        if editor is None or item is None:
            return "break"
        value = editor.get().strip()
        if not value:
            messagebox.showwarning(
                "凡例名",
                "凡例名は空にできません。",
                parent=self,
            )
            self.after_idle(self._refocus_inline_legend_editor)
            return "break"
        self.inline_legend_editor = None
        self.inline_legend_item = None
        editor.destroy()
        self.app._set_curve_legend_name(item, value, parent=self)
        self._restore_focus()
        return "break"

    def _refocus_inline_legend_editor(self) -> None:
        editor = self.inline_legend_editor
        if editor is None:
            return
        try:
            editor.focus_set()
            editor.select_range(0, tk.END)
        except tk.TclError:
            pass

    def _cancel_inline_legend_edit_event(self, _event=None):
        self.cancel_inline_legend_edit()
        self._restore_focus()
        return "break"

    def cancel_inline_legend_edit(self) -> None:
        editor = self.inline_legend_editor
        self.inline_legend_editor = None
        self.inline_legend_item = None
        if editor is not None:
            try:
                editor.destroy()
            except tk.TclError:
                pass

    def open_color_editor(self, _event=None):
        if self._dialog_alive(self.color_editor):
            self.color_editor.lift()
            self.color_editor.focus_set()
            return "break"
        curves = self.app.state_model.ordered_curves()
        if not curves:
            messagebox.showinfo(
                "曲線未選択", "グラフへ曲線を追加してください。", parent=self
            )
            return "break"
        self.color_editor = ColorEditorDialog(
            self,
            curves,
            self.app._apply_curve_colors,
        )
        return "break"

    def open_legend_editor(self, _event=None):
        if self._dialog_alive(self.legend_editor):
            self.legend_editor.lift()
            self.legend_editor.focus_set()
            return "break"
        curves = self.app.state_model.ordered_curves()
        if not curves:
            messagebox.showinfo(
                "曲線未選択", "グラフへ曲線を追加してください。", parent=self
            )
            return "break"
        self.legend_editor = LegendEditorDialog(
            self,
            curves,
            self.app._apply_curve_legend_names,
        )
        return "break"

    def _build_dsc_controls(self) -> None:
        self.dsc_controls = ttk.LabelFrame(self, text="DSCグラフ解析", padding=5)
        self.dsc_controls.grid(row=3, column=0, sticky="ew", padx=8, pady=(7, 8))

        actions = ttk.Frame(self.dsc_controls)
        actions.grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="Tg解析", command=self.app._analyze_tg_selected).pack(
            side=tk.LEFT
        )
        ttk.Button(
            actions, text="融解解析", command=self.app._analyze_melting_selected
        ).pack(side=tk.LEFT, padx=(4, 0))
        self.calculate_button = ttk.Button(
            actions,
            text="算出",
            command=self.app._calculate_dsc_selection,
            state=tk.DISABLED,
        )
        self.calculate_button.pack(side=tk.LEFT, padx=(8, 0))
        self.undo_button = ttk.Button(
            actions,
            text="1点戻す",
            command=self.app._undo_dsc_selection,
            state=tk.DISABLED,
        )
        self.undo_button.pack(side=tk.LEFT, padx=(4, 0))
        self.clear_button = ttk.Button(
            actions,
            text="選択解除",
            command=self.app._cancel_dsc_selection,
            state=tk.DISABLED,
        )
        self.clear_button.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            self.dsc_controls,
            textvariable=self.app.dsc_selection_instruction_var,
            foreground="#0E7490",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        visibility = ttk.Frame(self.dsc_controls)
        visibility.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        for column, (label, key, _default) in enumerate(self.app.DSC_VISIBILITY_ITEMS):
            ttk.Checkbutton(
                visibility,
                text=label,
                variable=self.app.dsc_visibility_vars[key],
                command=self.app._refresh_plot,
            ).grid(row=0, column=column, padx=(0, 7), sticky="w")

    def _build_ir_controls(self) -> None:
        self.ir_controls = ttk.LabelFrame(self, text="IR規格化位置", padding=5)
        self.ir_controls.grid(row=3, column=0, sticky="ew", padx=8, pady=(7, 8))
        ttk.Button(
            self.ir_controls,
            text="規格化位置をグラフで選択",
            command=self.app._begin_ir_normalization_selection,
        ).pack(side=tk.LEFT)
        ttk.Button(
            self.ir_controls,
            text="選択系列へ設定を適用",
            command=lambda: self.app._apply_series_processing(IR),
        ).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(
            self.ir_controls,
            text="クリック位置は個別規格化波数欄と同期します。",
            foreground="#0E7490",
        ).pack(side=tk.LEFT, padx=(10, 0))

    def _build_particle_size_controls(self) -> None:
        self.particle_size_controls = ttk.LabelFrame(
            self, text="指定粒径規格化", padding=5
        )
        self.particle_size_controls.grid(
            row=3, column=0, sticky="ew", padx=8, pady=(7, 8)
        )
        ttk.Button(
            self.particle_size_controls,
            text="規格化位置をグラフで選択",
            command=self.app._begin_particle_normalization_selection,
        ).pack(side=tk.LEFT)
        ttk.Button(
            self.particle_size_controls,
            text="選択系列へ設定を適用",
            command=self.app._apply_particle_series_settings,
        ).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(
            self.particle_size_controls,
            textvariable=self.app.particle_selection_instruction_var,
            foreground="#0E7490",
        ).pack(side=tk.LEFT, padx=(10, 0))

    def update_mode(self, mode: str) -> None:
        self.title(f"{mode}グラフ — {APP_DISPLAY_NAME}")
        self.loaded_tree.configure(displaycolumns=visible_graph_curve_columns(mode))
        if mode == DSC:
            self.dsc_controls.grid()
        else:
            self.dsc_controls.grid_remove()
        if mode == IR:
            self.ir_controls.grid()
        else:
            self.ir_controls.grid_remove()
        if mode == PARTICLE_SIZE:
            self.particle_size_controls.grid()
        else:
            self.particle_size_controls.grid_remove()

    def set_selection_state(self, *, complete: bool, has_points: bool) -> None:
        self.calculate_button.configure(state=tk.NORMAL if complete else tk.DISABLED)
        self.undo_button.configure(state=tk.NORMAL if has_points else tk.DISABLED)
        self.clear_button.configure(state=tk.NORMAL)

    def clear_selection_state(self) -> None:
        self.calculate_button.configure(state=tk.DISABLED)
        self.undo_button.configure(state=tk.DISABLED)
        self.clear_button.configure(state=tk.DISABLED)

    def is_alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def present(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _request_close(self) -> None:
        self.cancel_inline_legend_edit()
        self.app._on_graph_window_closing(self)
