from __future__ import annotations

from typing import Union

import queue
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .branding import MAIN_WINDOW_TITLE
from .dsc_analysis import (
    DscAnalysisError,
    DscAnalysisSession,
    DscAnalysisSettings,
    TemperatureRange,
    analyze_melting,
    analyze_tg,
    infer_heating_rate,
    measurement_segment_label,
    suggest_dsc_ranges,
)
from .dsc_selection import DscFourPointSelection, settings_with_four_points
from .display_series import DisplaySeries, to_display_series
from .excel_export import export_excel
from .filesystem import list_child_directories, list_csv_names
from .graph_window import GraphWindow, ensure_single_window
from .import_profile_dialog import ImportSettingsDialog
from .import_profiles import (
    ImportProfile,
    ImportProfileError,
    ProfileStore,
    ProfiledCurveLoader,
)
from .model import (
    DSC,
    GPC,
    IR,
    PARTICLE_SIZE,
    TGA,
    UV_VIS,
    MEASUREMENT_TYPES,
    AxisRange,
    CurveData,
    PlotState,
    path_key,
)
from .parser import (
    MeasurementDataError,
    load_dsc_csv,
    load_gpc_csv,
    load_ir_csv,
    load_particle_size_csv,
    load_tga_csv,
    load_uvvis_csv,
)
from .particle_size_processing import (
    ParticleSizeCommonSettings,
    ParticleSizeProcessedData,
    ParticleSizeSeriesSettings,
    particle_mixed_normalization,
    process_particle_size_curve,
    raw_particle_size_curve,
)
from .processing import (
    BLANK_FAILED,
    CommonProcessingSettings,
    ProcessedCurveData,
    ProcessingError,
    SeriesProcessingSettings,
    USE_COMMON,
    USE_INDIVIDUAL,
    USE_NONE,
    ir_mixed_normalization,
    process_dsc_curve,
    process_ir_curve,
    raw_processed_curve,
    validate_blank_reference,
)
from .settings import load_last_root, save_last_root
from .series_edit import normalize_color
from .tga_analysis import (
    TgaTdError,
    calculate_custom_td_for_selection,
    calculate_standard_td,
    format_td_temperature,
    selected_tga_curve,
    td_label_from_remaining_percent,
)

MAIN_CURVE_COLUMNS = (
    "color",
    "name",
    "blank",
    "normalization",
    "particle_normalization",
    "source",
)
MAIN_CURVE_HEADINGS = {
    "color": ("色", 70, False),
    "name": ("系列名", 250, True),
    "blank": ("ブランク", 190, True),
    "normalization": ("規格化波数", 130, False),
    "particle_normalization": ("規格化粒径", 140, False),
    "source": ("元ファイル", 430, True),
}


def visible_main_curve_columns(measurement_type: str) -> tuple[str, ...]:
    """Return the columns relevant to the active measurement mode."""
    if measurement_type == IR:
        return MAIN_CURVE_COLUMNS
    if measurement_type == DSC:
        return ("color", "name", "blank", "source")
    if measurement_type == PARTICLE_SIZE:
        return ("color", "name", "particle_normalization", "source")
    return ("color", "name", "source")


def analysis_panel_for_mode(measurement_type: str) -> str:
    return {
        TGA: "tga",
        DSC: "dsc",
        IR: "ir",
        UV_VIS: "raw",
        GPC: "raw",
        PARTICLE_SIZE: "particle_size",
    }[measurement_type]


def processing_blank_label(processed: Union[ProcessedCurveData, None]) -> str:
    """Format the actual blank source used for a displayed DSC/IR curve."""
    if processed is None:
        return "なし"
    blank_name = (processed.blank_name or "").strip()
    if blank_name and blank_name != "Failed":
        return blank_name
    return "不明" if processed.blank_failed else "なし"


def ir_normalization_label(processed: Union[ProcessedCurveData, None]) -> str:
    """Format the IR normalization peak wavenumber for the series table."""
    if processed is None or processed.normalization_wavenumber is None:
        return "なし"
    suffix = "（未適用）" if getattr(processed, "normalization_failed", False) else ""
    return f"{processed.normalization_wavenumber:g} cm⁻¹{suffix}"


def apply_clicked_ir_normalization(
    settings: dict[str, SeriesProcessingSettings],
    curve_key: str,
    wavenumber: float,
) -> SeriesProcessingSettings:
    """Commit a graph-selected IR peak to exactly one series setting."""
    if not math.isfinite(wavenumber):
        raise ProcessingError("規格化波数は有限値で指定してください。")
    setting = settings.setdefault(curve_key, SeriesProcessingSettings())
    setting.normalization_mode = USE_INDIVIDUAL
    setting.normalization_wavenumber = float(wavenumber)
    return setting


def particle_size_normalization_label(
    processed: Union[ParticleSizeProcessedData, None],
) -> str:
    if processed is None or processed.normalization_diameter_um is None:
        return "なし"
    suffix = "（未適用）" if processed.normalization_failed else ""
    return f"{processed.normalization_diameter_um:g} µm{suffix}"


def apply_clicked_particle_normalization(
    settings: dict[str, ParticleSizeSeriesSettings],
    curve_key: str,
    diameter_um: float,
) -> ParticleSizeSeriesSettings:
    if not math.isfinite(diameter_um):
        raise ProcessingError("指定粒径は有限値で指定してください。")
    if diameter_um <= 0:
        raise ProcessingError("指定粒径は0より大きい値を入力してください。")
    setting = settings.setdefault(curve_key, ParticleSizeSeriesSettings())
    setting.normalization_mode = USE_INDIVIDUAL
    setting.normalization_diameter_um = float(diameter_um)
    return setting


class TgaAnalyzerApp(tk.Tk):
    DSC_VISIBILITY_ITEMS = (
        ("Tg範囲", "tg_range", True),
        ("平滑化曲線", "tg_smoothed", False),
        ("Tg基線", "tg_baselines", True),
        ("Tg On", "tg_onset", True),
        ("Tg Mid", "tg_midpoint", True),
        ("Tg Inf", "tg_inflection", True),
        ("融解範囲", "melt_range", True),
        ("融解基線", "melt_baseline", True),
        ("融解点", "melt_points", True),
        ("積分面積", "enthalpy_area", True),
    )

    def __init__(self) -> None:
        super().__init__()
        self.title(MAIN_WINDOW_TITLE)
        self.geometry("1360x840")
        self.minsize(1050, 680)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.mode_var = tk.StringVar(value=TGA)
        self.states = {
            TGA: PlotState(measurement_type=TGA),
            DSC: PlotState(measurement_type=DSC),
            IR: PlotState(measurement_type=IR),
            UV_VIS: PlotState(measurement_type=UV_VIS),
            GPC: PlotState(measurement_type=GPC),
            PARTICLE_SIZE: PlotState(measurement_type=PARTICLE_SIZE),
        }
        self.selected_curve_keys: dict[str, list[str]] = {
            mode: [] for mode in MEASUREMENT_TYPES
        }
        self.common_processing = {
            DSC: CommonProcessingSettings(),
            IR: CommonProcessingSettings(),
        }
        self.series_processing: dict[str, dict[str, SeriesProcessingSettings]] = {
            DSC: {},
            IR: {},
        }
        self.processed_curves: dict[str, dict[str, ProcessedCurveData]] = {
            DSC: {},
            IR: {},
        }
        self.blank_choice_keys: dict[str, dict[str, str]] = {DSC: {}, IR: {}}
        self.ir_normalization_selection_key: Union[str, None] = None
        self.ir_normalization_preview: Union[float, None] = None
        self.particle_common_processing = ParticleSizeCommonSettings()
        self.particle_series_processing: dict[str, ParticleSizeSeriesSettings] = {}
        self.particle_processed_curves: dict[str, ParticleSizeProcessedData] = {}
        self.particle_normalization_selection_key: Union[str, None] = None
        self.particle_normalization_preview: Union[float, None] = None
        self.particle_selection_instruction_var = tk.StringVar(value="")
        self.state_model = self.states[TGA]
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thermal-worker")
        self.events: queue.Queue[tuple[str, object, Union[object, None], Union[BaseException, None]]] = queue.Queue()
        self.root_generation = 0
        self.folder_tree_paths: dict[str, Path] = {}
        self.tree_children_loaded: set[str] = set()
        self.tree_children_loading: set[str] = set()
        self.current_folder: Union[Path, None] = None
        self.current_file_names: list[str] = []
        self.file_item_names: dict[str, str] = {}
        self.loaded_item_keys: dict[str, str] = {}
        self.tga_active_key: Union[str, None] = None
        self.tga_custom_curve_key: Union[str, None] = None
        self.tga_standard_status = "未選択"
        self.tga_standard_warning = "解析対象のTGA系列を1つ選択してください"
        self.file_scan_token = 0
        self.dsc_sessions: dict[str, DscAnalysisSession] = {}
        self.dsc_analysis_tokens: dict[str, int] = {}
        self.dsc_result_item_keys: dict[str, str] = {}
        self.dsc_active_key: Union[str, None] = None
        self.dsc_range_selection: Union[DscFourPointSelection, None] = None
        self.dsc_selection_snapshot: dict[str, str] = {}
        self._syncing_dsc_range_controls = False
        self.graph_window: Union[GraphWindow, None] = None
        self.import_dialog: Union[ImportSettingsDialog, None] = None
        self.profile_store = ProfileStore()
        self.profiled_loader = ProfiledCurveLoader(self.profile_store)
        self.file_profile_overrides: dict[tuple[str, str], ImportProfile] = {}
        self.series_import_profiles: dict[str, dict[str, ImportProfile]] = {
            mode: {} for mode in MEASUREMENT_TYPES
        }
        self.closed = False

        self._configure_style()
        self._build_ui()
        self.bind("<Escape>", self._on_escape_pressed)
        self.after(80, self._poll_events)
        self.after(100, self._restore_last_root)
        if self.profile_store.errors:
            self.after(180, self._report_profile_errors)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(9, 5), font=("Segoe UI", 9))
        style.configure("Accent.TButton", foreground="#FFFFFF", background="#0F766E")
        style.map("Accent.TButton", background=[("active", "#0B5F59")])
        style.configure("Treeview", rowheight=25, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"), foreground="#17324D")
        style.configure("Status.TLabel", padding=(8, 5), foreground="#475467")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=0, column=0, sticky="nsew")
        left = ttk.Frame(paned, padding=8, width=370)
        right = ttk.Frame(paned, padding=(4, 8, 8, 8))
        paned.add(left, weight=0)
        paned.add(right, weight=1)
        self.after(200, lambda: self._set_initial_sash(paned))

        self._build_left_panel(left)
        self._build_right_panel(right)

        self.status_var = tk.StringVar(value="ルートフォルダを選択してください。")
        ttk.Separator(self, orient=tk.HORIZONTAL).grid(row=1, column=0, sticky="ew")
        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel").grid(
            row=2, column=0, sticky="ew"
        )

    def _set_initial_sash(self, paned: ttk.Panedwindow) -> None:
        try:
            paned.sashpos(0, 370)
        except tk.TclError:
            pass

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=2)
        parent.rowconfigure(6, weight=3)

        root_bar = ttk.Frame(parent)
        root_bar.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        root_bar.columnconfigure(0, weight=1)
        ttk.Label(root_bar, text="測定モード", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 5)
        )
        mode_box = ttk.Combobox(
            root_bar,
            textvariable=self.mode_var,
            values=MEASUREMENT_TYPES,
            state="readonly",
            width=10,
        )
        mode_box.grid(row=0, column=1, sticky="e", pady=(0, 5))
        mode_box.bind("<<ComboboxSelected>>", self._on_mode_changed)
        ttk.Button(root_bar, text="ルートフォルダを選択", command=self._choose_root).grid(
            row=1, column=0, sticky="ew"
        )
        ttk.Button(root_bar, text="更新", width=8, command=self._refresh_current_folder).grid(
            row=1, column=1, padx=(5, 0)
        )

        self.root_path_var = tk.StringVar(value="未選択")
        ttk.Label(parent, textvariable=self.root_path_var, foreground="#667085", wraplength=340).grid(
            row=1, column=0, sticky="ew", pady=(0, 7)
        )

        tree_frame = ttk.LabelFrame(parent, text="フォルダ", padding=4)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.folder_tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.folder_tree.yview)
        self.folder_tree.configure(yscrollcommand=tree_scroll.set)
        self.folder_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.folder_tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.folder_tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        ttk.Label(parent, text="ファイル名検索", style="Section.TLabel").grid(
            row=3, column=0, sticky="w", pady=(8, 3)
        )
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self._render_file_names())
        ttk.Entry(parent, textvariable=self.search_var).grid(row=4, column=0, sticky="ew")

        file_label_bar = ttk.Frame(parent)
        file_label_bar.grid(row=5, column=0, sticky="ew", pady=(8, 3))
        file_label_bar.columnconfigure(0, weight=1)
        ttk.Label(file_label_bar, text="CSVファイル", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.file_count_var = tk.StringVar(value="0件")
        ttk.Label(file_label_bar, textvariable=self.file_count_var, foreground="#667085").grid(
            row=0, column=1, sticky="e"
        )

        file_frame = ttk.Frame(parent)
        file_frame.grid(row=6, column=0, sticky="nsew")
        file_frame.columnconfigure(0, weight=1)
        file_frame.rowconfigure(0, weight=1)
        self.file_tree = ttk.Treeview(
            file_frame,
            columns=("name",),
            show="headings",
            selectmode="extended",
        )
        self.file_tree.heading("name", text="ファイル名")
        self.file_tree.column("name", width=310, stretch=True)
        file_scroll = ttk.Scrollbar(file_frame, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=file_scroll.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        file_scroll.grid(row=0, column=1, sticky="ns")
        self.file_tree.bind("<Double-1>", lambda _event: self._add_selected_files())

        file_actions = ttk.Frame(parent)
        file_actions.grid(row=7, column=0, sticky="ew", pady=(7, 0))
        file_actions.columnconfigure(1, weight=1)
        ttk.Button(
            file_actions,
            text="読込設定",
            command=self._open_import_settings,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(
            file_actions,
            text="グラフへ追加",
            style="Accent.TButton",
            command=self._add_selected_files,
        ).grid(row=0, column=1, sticky="ew")

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        self.x_min_var = tk.StringVar(value="0")
        self.x_max_var = tk.StringVar(value="100")
        self.y_min_var = tk.StringVar(value="0")
        self.y_max_var = tk.StringVar(value="105")

        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="読み込み済み系列と解析結果",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            header,
            text="グラフウィンドウを開く",
            style="Accent.TButton",
            command=self._open_graph_window,
        ).grid(row=0, column=1, sticky="e")

        loaded_frame = ttk.LabelFrame(parent, text="読み込み済み系列", padding=5)
        loaded_frame.grid(row=1, column=0, sticky="ew")
        loaded_frame.columnconfigure(0, weight=1)
        loaded_frame.rowconfigure(0, weight=1)
        self.loaded_tree = ttk.Treeview(
            loaded_frame,
            columns=MAIN_CURVE_COLUMNS,
            show="headings",
            height=7,
            selectmode="extended",
        )
        for column_name, (text, width, stretch) in MAIN_CURVE_HEADINGS.items():
            self.loaded_tree.heading(column_name, text=text)
            self.loaded_tree.column(column_name, width=width, stretch=stretch)
        self.loaded_tree.configure(
            displaycolumns=visible_main_curve_columns(self.mode_var.get())
        )
        loaded_scroll = ttk.Scrollbar(
            loaded_frame, orient=tk.VERTICAL, command=self.loaded_tree.yview
        )
        self.loaded_tree.configure(yscrollcommand=loaded_scroll.set)
        self.loaded_tree.grid(row=0, column=0, sticky="nsew")
        loaded_scroll.grid(row=0, column=1, sticky="ns")
        self.loaded_tree.bind("<<TreeviewSelect>>", self._on_loaded_curve_selected)

        self._build_tga_panel(parent)
        self._build_dsc_panel(parent)
        self._build_ir_panel(parent)
        self._build_particle_size_panel(parent)
        self._build_raw_comparison_panel(parent)

    def _build_raw_comparison_panel(self, parent: ttk.Frame) -> None:
        self.raw_comparison_panel = ttk.LabelFrame(
            parent, text="生データ比較", padding=12
        )
        self.raw_comparison_panel.grid(row=2, column=0, sticky="nsew", pady=(7, 0))
        self.raw_comparison_message_var = tk.StringVar()
        ttk.Label(
            self.raw_comparison_panel,
            textvariable=self.raw_comparison_message_var,
            foreground="#475467",
            wraplength=850,
        ).pack(anchor="nw")
        self.raw_comparison_panel.grid_remove()

    def _build_tga_panel(self, parent: ttk.Frame) -> None:
        self.tga_panel = ttk.LabelFrame(parent, text="Td解析", padding=8)
        self.tga_panel.grid(row=2, column=0, sticky="nsew", pady=(7, 0))
        self.tga_panel.columnconfigure(0, weight=1)

        self.tga_selected_var = tk.StringVar(
            value="解析対象のTGA系列を1つ選択してください"
        )
        ttk.Label(
            self.tga_panel,
            textvariable=self.tga_selected_var,
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")

        standard = ttk.LabelFrame(
            self.tga_panel, text="標準熱分解温度（重量減少率）", padding=7
        )
        standard.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.tga_td_vars = {
            "Td5": tk.StringVar(value="—"),
            "Td50": tk.StringVar(value="—"),
            "Td95": tk.StringVar(value="—"),
        }
        for column, label in enumerate(("Td5", "Td50", "Td95")):
            cell = ttk.Frame(standard)
            cell.grid(row=0, column=column, padx=(0, 28), sticky="w")
            ttk.Label(cell, text=f"{label}（℃）").pack(side=tk.LEFT)
            ttk.Label(
                cell,
                textvariable=self.tga_td_vars[label],
                style="Section.TLabel",
                width=12,
            ).pack(side=tk.LEFT, padx=(5, 0))

        custom = ttk.LabelFrame(
            self.tga_panel, text="任意残存率から算出", padding=7
        )
        custom.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        self.tga_remaining_var = tk.StringVar(value="90")
        self.tga_custom_label_var = tk.StringVar(value="—")
        self.tga_custom_temperature_var = tk.StringVar(value="—")
        ttk.Label(custom, text="残存率（%）").grid(row=0, column=0, sticky="w")
        remaining_entry = ttk.Entry(
            custom, textvariable=self.tga_remaining_var, width=10
        )
        remaining_entry.grid(row=0, column=1, padx=(5, 5))
        remaining_entry.bind("<Return>", self._calculate_custom_tga_td)
        ttk.Button(
            custom, text="算出", command=self._calculate_custom_tga_td
        ).grid(row=0, column=2, padx=(0, 20))
        ttk.Label(custom, text="Tdx").grid(row=0, column=3, sticky="e")
        ttk.Label(
            custom,
            textvariable=self.tga_custom_label_var,
            style="Section.TLabel",
            width=11,
        ).grid(row=0, column=4, padx=(5, 18), sticky="w")
        ttk.Label(custom, text="温度（℃）").grid(row=0, column=5, sticky="e")
        ttk.Label(
            custom,
            textvariable=self.tga_custom_temperature_var,
            style="Section.TLabel",
            width=12,
        ).grid(row=0, column=6, padx=(5, 0), sticky="w")

        status_row = ttk.Frame(self.tga_panel)
        status_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(status_row, text="解析状態:").pack(side=tk.LEFT)
        self.tga_td_status_var = tk.StringVar(value=self.tga_standard_status)
        ttk.Label(
            status_row,
            textvariable=self.tga_td_status_var,
            style="Section.TLabel",
        ).pack(side=tk.LEFT, padx=(5, 0))

        self.tga_td_warning_var = tk.StringVar(value=self.tga_standard_warning)
        ttk.Label(
            self.tga_panel,
            textvariable=self.tga_td_warning_var,
            foreground="#B42318",
            wraplength=850,
            justify=tk.LEFT,
        ).grid(row=4, column=0, sticky="ew", pady=(5, 0))

    def _clear_custom_tga_td(self) -> None:
        self.tga_custom_curve_key = None
        self.tga_custom_label_var.set("—")
        self.tga_custom_temperature_var.set("—")

    def _set_tga_warning(self, extra: Union[str, None] = None) -> None:
        messages = [message for message in (self.tga_standard_warning, extra) if message]
        self.tga_td_warning_var.set(" / ".join(messages))

    def _update_tga_td_panel(self, keys: list[str]) -> None:
        if self.mode_var.get() != TGA:
            return
        previous_active = self.tga_active_key
        previous_custom = self.tga_custom_curve_key
        try:
            curve = selected_tga_curve(self.state_model, keys)
        except TgaTdError as exc:
            self.tga_active_key = None
            self.tga_selected_var.set("解析対象のTGA系列を1つ選択してください")
            for variable in self.tga_td_vars.values():
                variable.set("—")
            self.tga_standard_status = "未選択"
            self.tga_standard_warning = str(exc)
            self.tga_td_status_var.set(self.tga_standard_status)
            self._clear_custom_tga_td()
            self._set_tga_warning()
            return

        summary = calculate_standard_td(curve)
        self.tga_active_key = curve.key
        self.tga_selected_var.set(f"解析対象: {curve.display_name}")
        self.tga_td_vars["Td5"].set(format_td_temperature(summary.td5_c))
        self.tga_td_vars["Td50"].set(format_td_temperature(summary.td50_c))
        self.tga_td_vars["Td95"].set(format_td_temperature(summary.td95_c))
        self.tga_standard_status = summary.status
        self.tga_standard_warning = " / ".join(summary.warnings)
        self.tga_td_status_var.set(summary.status)
        self._set_tga_warning()

        if previous_active != curve.key:
            self._clear_custom_tga_td()
        elif previous_custom == curve.key:
            self._calculate_custom_tga_td()

    def _calculate_custom_tga_td(self, _event=None):
        try:
            selected_tga_curve(self.state_model, self._selected_loaded_keys())
            label, temperature = calculate_custom_td_for_selection(
                self.state_model,
                self._selected_loaded_keys(),
                self.tga_remaining_var.get(),
            )
        except TgaTdError as exc:
            try:
                label = td_label_from_remaining_percent(self.tga_remaining_var.get())
            except TgaTdError:
                label = "—"
            self.tga_custom_curve_key = None
            self.tga_custom_label_var.set(label)
            self.tga_custom_temperature_var.set("算出不可")
            self.tga_td_status_var.set("任意Tdx算出不可")
            self._set_tga_warning(str(exc))
            return "break"

        self.tga_custom_curve_key = self._selected_loaded_keys()[0]
        self.tga_custom_label_var.set(label)
        self.tga_custom_temperature_var.set(format_td_temperature(temperature))
        self.tga_td_status_var.set(f"{self.tga_standard_status}／{label}算出済み")
        self._set_tga_warning()
        return "break"

    def _build_dsc_panel(self, parent: ttk.Frame) -> None:
        self.dsc_panel = ttk.LabelFrame(parent, text="DSC解析", padding=5)
        self.dsc_panel.grid(row=2, column=0, sticky="nsew", pady=(7, 0))
        self.dsc_panel.columnconfigure(0, weight=1)
        self.dsc_panel.rowconfigure(4, weight=1)

        self.dsc_selected_var = tk.StringVar(value="曲線を選択してください")
        self.dsc_unit_var = tk.StringVar(value="mW")
        self.dsc_rate_var = tk.StringVar()
        self.dsc_mass_var = tk.StringVar()
        self.dsc_direction_var = tk.StringVar(value="上向き")
        self.dsc_smoothing_var = tk.StringVar(value="7")
        meta = ttk.Frame(self.dsc_panel)
        meta.grid(row=0, column=0, sticky="ew")
        ttk.Label(meta, textvariable=self.dsc_selected_var, style="Section.TLabel").grid(
            row=0, column=0, padx=(0, 10), sticky="w"
        )
        fields = (
            ("熱流単位", self.dsc_unit_var, ("mW", "W/g", "mW/mg", "不明"), 9),
            ("昇温速度 ℃/min", self.dsc_rate_var, None, 8),
            ("試料重量 mg", self.dsc_mass_var, None, 8),
            ("吸熱方向", self.dsc_direction_var, ("上向き", "下向き"), 8),
        )
        column = 1
        for label, variable, choices, width in fields:
            ttk.Label(meta, text=label).grid(row=0, column=column, padx=(4, 2))
            if choices is None:
                widget = ttk.Entry(meta, textvariable=variable, width=width)
            else:
                widget = ttk.Combobox(
                    meta, textvariable=variable, values=choices, state="readonly", width=width
                )
            widget.grid(row=0, column=column + 1)
            column += 2
        ttk.Label(meta, text="平滑化 点").grid(row=0, column=column, padx=(4, 2))
        ttk.Spinbox(
            meta,
            from_=1,
            to=101,
            increment=2,
            textvariable=self.dsc_smoothing_var,
            width=6,
        ).grid(row=0, column=column + 1)

        self.dsc_common_blank_var = tk.StringVar(value="(なし)")
        self.dsc_blank_mode_var = tk.StringVar(value="共通ブランクを使用")
        self.dsc_individual_blank_var = tk.StringVar(value="(なし)")
        ttk.Label(meta, text="共通ブランク").grid(row=1, column=0, pady=(5, 0), sticky="w")
        self.dsc_common_blank_box = ttk.Combobox(
            meta, textvariable=self.dsc_common_blank_var, state="readonly", width=28
        )
        self.dsc_common_blank_box.grid(row=1, column=1, columnspan=3, pady=(5, 0), sticky="w")
        ttk.Button(
            meta, text="共通設定を適用", command=lambda: self._apply_common_processing(DSC)
        ).grid(row=1, column=4, columnspan=2, padx=(4, 8), pady=(5, 0), sticky="w")
        ttk.Label(meta, text="選択系列").grid(row=1, column=6, pady=(5, 0))
        ttk.Combobox(
            meta,
            textvariable=self.dsc_blank_mode_var,
            values=("共通ブランクを使用", "補正なし", "個別ブランクを指定"),
            state="readonly",
            width=18,
        ).grid(row=1, column=7, pady=(5, 0))
        self.dsc_individual_blank_box = ttk.Combobox(
            meta, textvariable=self.dsc_individual_blank_var, state="readonly", width=25
        )
        self.dsc_individual_blank_box.grid(row=1, column=8, columnspan=3, padx=(4, 0), pady=(5, 0))
        ttk.Button(
            meta, text="系列設定を適用", command=lambda: self._apply_series_processing(DSC)
        ).grid(row=1, column=11, columnspan=2, padx=(4, 0), pady=(5, 0))

        ranges = ttk.Frame(self.dsc_panel)
        ranges.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ranges.columnconfigure(0, weight=1)
        ranges.columnconfigure(1, weight=1)
        self.dsc_range_vars: dict[str, tk.StringVar] = {}
        self._build_dsc_range_group(ranges, "Tg", 0, "tg")
        self._build_dsc_range_group(ranges, "融解・積分", 1, "melt")

        actions = ttk.Frame(self.dsc_panel)
        actions.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(actions, text="自動候補", command=self._suggest_dsc_selected).pack(side=tk.LEFT)
        ttk.Button(actions, text="採用", command=lambda: self._set_dsc_decision("採用")).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(actions, text="除外", command=lambda: self._set_dsc_decision("除外")).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        self.dsc_selection_instruction_var = tk.StringVar(
            value="Tg解析または融解解析を押すと、グラフ上で4点を選択できます。"
        )
        self.dsc_visibility_vars = {
            key: tk.BooleanVar(value=default)
            for _label, key, default in self.DSC_VISIBILITY_ITEMS
        }

        override_row = ttk.Frame(self.dsc_panel)
        override_row.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(override_row, text="解析点補正（℃）", style="Section.TLabel").pack(
            side=tk.LEFT, padx=(0, 5)
        )
        override_labels = (
            ("Tg On", "tg_onset"),
            ("Tg Mid", "tg_midpoint"),
            ("Tg Inf", "tg_inflection"),
            ("Melt On", "melt_onset"),
            ("Peak", "melt_peak"),
            ("End", "melt_end"),
        )
        self.dsc_override_vars: dict[str, tk.StringVar] = {}
        for label, key in override_labels:
            ttk.Label(override_row, text=label).pack(side=tk.LEFT, padx=(7, 1))
            variable = tk.StringVar()
            self.dsc_override_vars[key] = variable
            ttk.Entry(override_row, textvariable=variable, width=7).pack(side=tk.LEFT)
        ttk.Button(override_row, text="解析点を反映", command=self._apply_dsc_overrides).pack(
            side=tk.LEFT, padx=(5, 0)
        )

        result_frame = ttk.Frame(self.dsc_panel)
        result_frame.grid(row=4, column=0, sticky="nsew", pady=(5, 0))
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        result_columns = (
            "file",
            "segment",
            "tg_onset",
            "tg_midpoint",
            "tg_inflection",
            "melt_onset",
            "melt_peak",
            "melt_end",
            "enthalpy",
            "status",
            "warning",
        )
        self.dsc_result_tree = ttk.Treeview(
            result_frame,
            columns=result_columns,
            show="headings",
            height=3,
            selectmode="browse",
        )
        headings = {
            "file": ("ファイル名", 150),
            "segment": ("区間", 55),
            "tg_onset": ("Tg Onset ℃", 85),
            "tg_midpoint": ("Tg Mid ℃", 80),
            "tg_inflection": ("Tg Inf ℃", 80),
            "melt_onset": ("融解On ℃", 80),
            "melt_peak": ("融解Peak ℃", 85),
            "melt_end": ("融解End ℃", 80),
            "enthalpy": ("ΔH J/g", 80),
            "status": ("解析状態", 115),
            "warning": ("警告", 340),
        }
        for name, (label, width) in headings.items():
            self.dsc_result_tree.heading(name, text=label)
            self.dsc_result_tree.column(name, width=width, minwidth=50, stretch=name in {"file", "warning"})
        result_y = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.dsc_result_tree.yview)
        result_x = ttk.Scrollbar(result_frame, orient=tk.HORIZONTAL, command=self.dsc_result_tree.xview)
        self.dsc_result_tree.configure(yscrollcommand=result_y.set, xscrollcommand=result_x.set)
        self.dsc_result_tree.grid(row=0, column=0, sticky="nsew")
        result_y.grid(row=0, column=1, sticky="ns")
        result_x.grid(row=1, column=0, sticky="ew")
        self.dsc_result_tree.bind("<<TreeviewSelect>>", self._on_dsc_result_selected)
        self.dsc_panel.grid_remove()

    def _build_ir_panel(self, parent: ttk.Frame) -> None:
        self.ir_panel = ttk.LabelFrame(parent, text="IR処理", padding=8)
        self.ir_panel.grid(row=2, column=0, sticky="nsew", pady=(7, 0))
        self.ir_panel.columnconfigure(0, weight=1)
        self.ir_selected_var = tk.StringVar(value="処理対象のIR系列を1つ選択してください")
        ttk.Label(self.ir_panel, textvariable=self.ir_selected_var, style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        common = ttk.LabelFrame(self.ir_panel, text="IRモード共通設定", padding=7)
        common.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.ir_common_blank_var = tk.StringVar(value="(なし)")
        self.ir_common_norm_var = tk.StringVar()
        ttk.Label(common, text="共通ブランク").grid(row=0, column=0, sticky="w")
        self.ir_common_blank_box = ttk.Combobox(
            common, textvariable=self.ir_common_blank_var, state="readonly", width=40
        )
        self.ir_common_blank_box.grid(row=0, column=1, padx=(5, 14), sticky="w")
        ttk.Label(common, text="共通規格化波数 cm⁻¹").grid(row=0, column=2, sticky="w")
        ttk.Entry(common, textvariable=self.ir_common_norm_var, width=12).grid(
            row=0, column=3, padx=(5, 8)
        )
        ttk.Button(common, text="共通設定を適用", command=lambda: self._apply_common_processing(IR)).grid(
            row=0, column=4
        )

        series = ttk.LabelFrame(self.ir_panel, text="選択系列の上書き", padding=7)
        series.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        self.ir_blank_mode_var = tk.StringVar(value="共通ブランクを使用")
        self.ir_individual_blank_var = tk.StringVar(value="(なし)")
        self.ir_norm_mode_var = tk.StringVar(value="共通規格化位置を使用")
        self.ir_individual_norm_var = tk.StringVar()
        ttk.Label(series, text="ブランク").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            series,
            textvariable=self.ir_blank_mode_var,
            values=("共通ブランクを使用", "補正なし", "個別ブランクを指定"),
            state="readonly",
            width=18,
        ).grid(row=0, column=1, padx=(5, 5))
        self.ir_individual_blank_box = ttk.Combobox(
            series, textvariable=self.ir_individual_blank_var, state="readonly", width=34
        )
        self.ir_individual_blank_box.grid(row=0, column=2, padx=(0, 14))
        ttk.Label(series, text="規格化").grid(row=0, column=3, sticky="w")
        ttk.Combobox(
            series,
            textvariable=self.ir_norm_mode_var,
            values=("共通規格化位置を使用", "規格化なし", "個別の規格化波数を指定"),
            state="readonly",
            width=22,
        ).grid(row=0, column=4, padx=(5, 5))
        ir_norm_entry = ttk.Entry(series, textvariable=self.ir_individual_norm_var, width=11)
        ir_norm_entry.grid(row=0, column=5)
        self.ir_individual_norm_var.trace_add("write", self._on_ir_norm_entry_changed)
        ttk.Button(series, text="グラフで選択", command=self._begin_ir_normalization_selection).grid(
            row=0, column=6, padx=(5, 0)
        )
        ttk.Button(series, text="系列設定を適用", command=lambda: self._apply_series_processing(IR)).grid(
            row=0, column=7, padx=(5, 0)
        )

        self.ir_processing_status_var = tk.StringVar(value="Raw")
        self.ir_processing_warning_var = tk.StringVar()
        ttk.Label(self.ir_panel, textvariable=self.ir_processing_status_var).grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            self.ir_panel,
            textvariable=self.ir_processing_warning_var,
            foreground="#B42318",
            wraplength=900,
            justify=tk.LEFT,
        ).grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.ir_panel.grid_remove()

    def _build_particle_size_panel(self, parent: ttk.Frame) -> None:
        self.particle_panel = ttk.LabelFrame(
            parent, text="粒度分布処理 — 指定粒径規格化", padding=8
        )
        self.particle_panel.grid(row=2, column=0, sticky="nsew", pady=(7, 0))
        self.particle_panel.columnconfigure(0, weight=1)
        self.particle_selected_var = tk.StringVar(
            value="処理対象の粒度分布系列を1つ選択してください"
        )
        ttk.Label(
            self.particle_panel,
            textvariable=self.particle_selected_var,
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")

        common = ttk.LabelFrame(
            self.particle_panel, text="粒度分布モード共通設定", padding=7
        )
        common.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.particle_common_norm_var = tk.StringVar()
        ttk.Label(common, text="共通規格化粒径（µm）").grid(row=0, column=0, sticky="w")
        ttk.Entry(common, textvariable=self.particle_common_norm_var, width=14).grid(
            row=0, column=1, padx=(5, 8)
        )
        ttk.Button(
            common,
            text="共通設定を適用",
            command=self._apply_particle_common_settings,
        ).grid(row=0, column=2)

        series = ttk.LabelFrame(
            self.particle_panel, text="選択系列の規格化設定", padding=7
        )
        series.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        self.particle_norm_mode_var = tk.StringVar(value="共通指定粒径を使用")
        self.particle_individual_norm_var = tk.StringVar()
        ttk.Label(series, text="規格化モード").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            series,
            textvariable=self.particle_norm_mode_var,
            values=("共通指定粒径を使用", "規格化なし", "個別の指定粒径を使用"),
            state="readonly",
            width=22,
        ).grid(row=0, column=1, padx=(5, 12))
        ttk.Label(series, text="個別規格化粒径（µm）").grid(row=0, column=2, sticky="w")
        ttk.Entry(
            series, textvariable=self.particle_individual_norm_var, width=14
        ).grid(row=0, column=3, padx=(5, 5))
        self.particle_individual_norm_var.trace_add(
            "write", self._on_particle_norm_entry_changed
        )
        ttk.Button(
            series,
            text="グラフで選択",
            command=self._begin_particle_normalization_selection,
        ).grid(row=0, column=4, padx=(5, 0))
        ttk.Button(
            series,
            text="系列設定を適用",
            command=self._apply_particle_series_settings,
        ).grid(row=0, column=5, padx=(5, 0))

        self.particle_processing_status_var = tk.StringVar(value="処理状態: Raw")
        self.particle_processing_warning_var = tk.StringVar()
        ttk.Label(
            self.particle_panel, textvariable=self.particle_processing_status_var
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            self.particle_panel,
            textvariable=self.particle_processing_warning_var,
            foreground="#B42318",
            wraplength=900,
            justify=tk.LEFT,
        ).grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.particle_panel.grid_remove()

    def _build_dsc_range_group(
        self, parent: ttk.Frame, title: str, column: int, prefix: str
    ) -> None:
        frame = ttk.LabelFrame(parent, text=f"{title}範囲（℃）", padding=3)
        frame.grid(row=0, column=column, sticky="ew", padx=(0, 4) if column == 0 else (4, 0))
        fields = (("解析", "analysis"), ("前基線", "pre"), ("後基線", "post"))
        for index, (label, suffix) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=0, column=index * 4, padx=(2, 1))
            start_var = tk.StringVar()
            end_var = tk.StringVar()
            start_key = f"{prefix}_{suffix}_start"
            end_key = f"{prefix}_{suffix}_end"
            self.dsc_range_vars[start_key] = start_var
            self.dsc_range_vars[end_key] = end_var
            start_var.trace_add(
                "write", lambda *_args, key=start_key: self._on_dsc_range_var_changed(key)
            )
            end_var.trace_add(
                "write", lambda *_args, key=end_key: self._on_dsc_range_var_changed(key)
            )
            ttk.Entry(frame, textvariable=start_var, width=7).grid(row=0, column=index * 4 + 1)
            ttk.Label(frame, text="–").grid(row=0, column=index * 4 + 2)
            ttk.Entry(frame, textvariable=end_var, width=7).grid(row=0, column=index * 4 + 3)

    @staticmethod
    def _blank_mode_from_label(label: str) -> str:
        return {
            "補正なし": USE_NONE,
            "個別ブランクを指定": USE_INDIVIDUAL,
        }.get(label, USE_COMMON)

    @staticmethod
    def _blank_mode_label(mode: str) -> str:
        return {
            USE_NONE: "補正なし",
            USE_INDIVIDUAL: "個別ブランクを指定",
        }.get(mode, "共通ブランクを使用")

    @staticmethod
    def _norm_mode_from_label(label: str) -> str:
        return {
            "規格化なし": USE_NONE,
            "個別の規格化波数を指定": USE_INDIVIDUAL,
        }.get(label, USE_COMMON)

    @staticmethod
    def _norm_mode_label(mode: str) -> str:
        return {
            USE_NONE: "規格化なし",
            USE_INDIVIDUAL: "個別の規格化波数を指定",
        }.get(mode, "共通規格化位置を使用")

    @staticmethod
    def _particle_norm_mode_from_label(label: str) -> str:
        return {
            "規格化なし": USE_NONE,
            "個別の指定粒径を使用": USE_INDIVIDUAL,
        }.get(label, USE_COMMON)

    @staticmethod
    def _particle_norm_mode_label(mode: str) -> str:
        return {
            USE_NONE: "規格化なし",
            USE_INDIVIDUAL: "個別の指定粒径を使用",
        }.get(mode, "共通指定粒径を使用")

    def _refresh_processing_choices(self, mode: str) -> None:
        if mode not in {DSC, IR}:
            return
        mapping = {
            f"{curve.display_name} — {curve.path}": curve.key
            for curve in self.states[mode].ordered_curves()
        }
        self.blank_choice_keys[mode] = mapping
        values = ("(なし)", *mapping.keys())
        widgets = (
            (self.dsc_common_blank_box, self.dsc_individual_blank_box)
            if mode == DSC
            else (self.ir_common_blank_box, self.ir_individual_blank_box)
        )
        for widget in widgets:
            widget.configure(values=values)
        common_var = self.dsc_common_blank_var if mode == DSC else self.ir_common_blank_var
        common_var.set(self._blank_choice_for_key(mode, self.common_processing[mode].blank_key))

    def _blank_choice_for_key(self, mode: str, key: Union[str, None]) -> str:
        if key is None:
            return "(なし)"
        for label, candidate in self.blank_choice_keys[mode].items():
            if candidate == key:
                return label
        return f"(削除済み) {key}"

    def _blank_key_from_choice(self, mode: str, value: str) -> Union[str, None]:
        if not value or value == "(なし)":
            return None
        deleted_prefix = "(削除済み) "
        if value.startswith(deleted_prefix):
            retained_key = value[len(deleted_prefix) :].strip()
            return retained_key or None
        return self.blank_choice_keys[mode].get(value)

    @staticmethod
    def _optional_finite_float(text: str, label: str) -> Union[float, None]:
        normalized = text.strip()
        if not normalized:
            return None
        try:
            value = float(normalized)
        except ValueError as exc:
            raise ProcessingError(f"{label}は数値で入力してください。") from exc
        if not math.isfinite(value):
            raise ProcessingError(f"{label}は有限値で入力してください。")
        return value

    def _apply_common_processing(self, mode: str) -> None:
        common = self.common_processing[mode]
        blank_var = self.dsc_common_blank_var if mode == DSC else self.ir_common_blank_var
        new_blank = self._blank_key_from_choice(mode, blank_var.get())
        try:
            new_norm = (
                self._optional_finite_float(self.ir_common_norm_var.get(), "共通規格化波数")
                if mode == IR
                else None
            )
        except ProcessingError as exc:
            messagebox.showerror("処理設定エラー", str(exc), parent=self)
            return
        changed = common.blank_key != new_blank or (
            mode == IR and common.normalization_wavenumber != new_norm
        )
        common.blank_key = new_blank
        if mode == IR:
            common.normalization_wavenumber = new_norm
        if changed and mode == DSC:
            self._invalidate_dsc_sessions(set(self.states[DSC].curves))
        self._reprocess_mode(mode)
        self._set_status(f"{mode}の共通処理設定を適用しました。")

    def _apply_series_processing(self, mode: str) -> None:
        keys = self._selected_loaded_keys()
        if len(keys) != 1:
            messagebox.showinfo(
                "処理対象",
                f"処理設定を変更する{mode}系列を1つ選択してください。",
                parent=self,
            )
            return
        key = keys[0]
        setting = self.series_processing[mode].setdefault(key, SeriesProcessingSettings())
        if mode == DSC:
            blank_mode_var = self.dsc_blank_mode_var
            blank_choice_var = self.dsc_individual_blank_var
        else:
            blank_mode_var = self.ir_blank_mode_var
            blank_choice_var = self.ir_individual_blank_var
        blank_mode = self._blank_mode_from_label(blank_mode_var.get())
        blank_key = self._blank_key_from_choice(mode, blank_choice_var.get())
        if blank_mode == USE_INDIVIDUAL and blank_key is None:
            messagebox.showerror("処理設定エラー", "個別ブランクを選択してください。", parent=self)
            return
        if blank_key == key:
            messagebox.showerror("処理設定エラー", "系列自身をブランクとして指定できません。", parent=self)
            return
        normalization_mode = USE_NONE
        normalization = None
        if mode == IR:
            normalization_mode = self._norm_mode_from_label(self.ir_norm_mode_var.get())
            try:
                normalization = self._optional_finite_float(
                    self.ir_individual_norm_var.get(), "個別規格化波数"
                )
            except ProcessingError as exc:
                messagebox.showerror("処理設定エラー", str(exc), parent=self)
                return
            if normalization_mode == USE_INDIVIDUAL and normalization is None:
                messagebox.showerror("処理設定エラー", "個別の規格化波数を入力してください。", parent=self)
                return
        if blank_mode == USE_INDIVIDUAL and blank_key is not None:
            staged_settings = dict(self.series_processing[mode])
            staged_settings[key] = replace(
                setting, blank_mode=blank_mode, blank_key=blank_key
            )
            try:
                validate_blank_reference(
                    key,
                    blank_key,
                    self.common_processing[mode],
                    self.states[mode].curves,
                    staged_settings,
                )
            except ProcessingError as exc:
                messagebox.showerror("処理設定エラー", str(exc), parent=self)
                return
        blank_changed = setting.blank_mode != blank_mode or setting.blank_key != blank_key
        setting.blank_mode = blank_mode
        setting.blank_key = blank_key
        setting.normalization_mode = normalization_mode
        setting.normalization_wavenumber = normalization
        if blank_changed and mode == DSC:
            self._invalidate_dsc_sessions({key})
        if mode == IR:
            self.ir_normalization_selection_key = None
            self.ir_normalization_preview = normalization if normalization_mode == USE_INDIVIDUAL else None
        self._reprocess_mode(mode)
        self._load_processing_controls(key)
        self._set_status(f"{self.states[mode].curves[key].display_name} の処理設定を適用しました。")

    def _load_processing_controls(self, key: str) -> None:
        mode = self.mode_var.get()
        if mode not in {DSC, IR} or key not in self.states[mode].curves:
            return
        setting = self.series_processing[mode].setdefault(key, SeriesProcessingSettings())
        choice = self._blank_choice_for_key(mode, setting.blank_key)
        processed = self.processed_curves[mode].get(key)
        if mode == DSC:
            self.dsc_blank_mode_var.set(self._blank_mode_label(setting.blank_mode))
            self.dsc_individual_blank_var.set(choice)
        else:
            curve = self.states[IR].curves[key]
            self.ir_selected_var.set(f"処理対象: {curve.display_name}")
            self.ir_blank_mode_var.set(self._blank_mode_label(setting.blank_mode))
            self.ir_individual_blank_var.set(choice)
            self.ir_norm_mode_var.set(self._norm_mode_label(setting.normalization_mode))
            self.ir_individual_norm_var.set(
                "" if setting.normalization_wavenumber is None else f"{setting.normalization_wavenumber:g}"
            )
            self.ir_processing_status_var.set(
                f"処理状態: {processed.status if processed else 'Raw'}"
            )
            warnings = list(processed.warnings) if processed else []
            if ir_mixed_normalization(self._display_curves(IR)):
                warnings.append("規格化済みと未規格化の系列が混在しています。")
            self.ir_processing_warning_var.set(" / ".join(dict.fromkeys(warnings)))

    def _apply_particle_common_settings(self) -> None:
        try:
            value = self._optional_finite_float(
                self.particle_common_norm_var.get(), "共通規格化粒径"
            )
            if value is not None and value <= 0:
                raise ProcessingError("共通規格化粒径は0より大きい値を入力してください。")
        except ProcessingError as exc:
            messagebox.showerror("粒度分布処理設定エラー", str(exc), parent=self)
            return
        self.particle_common_processing.normalization_diameter_um = value
        self._reprocess_particle_size()
        self._set_status("粒度分布の共通規格化粒径を適用しました。")

    def _apply_particle_series_settings(self) -> None:
        keys = self._selected_loaded_keys()
        if len(keys) != 1:
            messagebox.showinfo(
                "粒度分布処理対象",
                "処理設定を変更する粒度分布系列を1つ選択してください。",
                parent=self,
            )
            return
        key = keys[0]
        mode = self._particle_norm_mode_from_label(self.particle_norm_mode_var.get())
        try:
            value = self._optional_finite_float(
                self.particle_individual_norm_var.get(), "個別規格化粒径"
            )
            if value is not None and value <= 0:
                raise ProcessingError("個別規格化粒径は0より大きい値を入力してください。")
            if mode == USE_INDIVIDUAL and value is None:
                raise ProcessingError("指定粒径を入力してください。")
        except ProcessingError as exc:
            messagebox.showerror("粒度分布処理設定エラー", str(exc), parent=self)
            return
        setting = self.particle_series_processing.setdefault(
            key, ParticleSizeSeriesSettings()
        )
        setting.normalization_mode = mode
        setting.normalization_diameter_um = value
        self.particle_normalization_selection_key = None
        self.particle_normalization_preview = value if mode == USE_INDIVIDUAL else None
        self.particle_selection_instruction_var.set("")
        self._reprocess_particle_size()
        self._load_particle_processing_controls(key)
        self._set_status(
            f"{self.states[PARTICLE_SIZE].curves[key].display_name} の規格化設定を適用しました。"
        )

    def _load_particle_processing_controls(self, key: str) -> None:
        if key not in self.states[PARTICLE_SIZE].curves:
            return
        curve = self.states[PARTICLE_SIZE].curves[key]
        setting = self.particle_series_processing.setdefault(
            key, ParticleSizeSeriesSettings()
        )
        processed = self.particle_processed_curves.get(key)
        self.particle_selected_var.set(f"処理対象: {curve.display_name}")
        self.particle_norm_mode_var.set(
            self._particle_norm_mode_label(setting.normalization_mode)
        )
        self.particle_individual_norm_var.set(
            ""
            if setting.normalization_diameter_um is None
            else f"{setting.normalization_diameter_um:g}"
        )
        self.particle_processing_status_var.set(
            f"処理状態: {processed.status if processed else 'Raw'}"
        )
        warnings = list(processed.warnings) if processed else []
        if particle_mixed_normalization(tuple(self.particle_processed_curves.values())):
            warnings.append(
                "規格化済みと未規格化の系列が混在し、異なる単位を同じY軸へ表示しています。"
            )
        self.particle_processing_warning_var.set(" / ".join(dict.fromkeys(warnings)))

    def _invalidate_dsc_sessions(self, keys: set[str]) -> None:
        for key in keys:
            session = self.dsc_sessions.get(key)
            if session is None:
                continue
            session.tg_result = None
            session.melting_result = None
            session.overrides.clear()
            session.decision = "候補"
            session.status = "ブランク変更のため再計算が必要"
            session.warnings = ["ブランク変更のため再計算が必要です。"]

    def _reprocess_mode(self, mode: str) -> None:
        if mode not in {DSC, IR}:
            return
        state = self.states[mode]
        output: dict[str, ProcessedCurveData] = {}
        processor = process_dsc_curve if mode == DSC else process_ir_curve
        for curve in state.ordered_curves():
            setting = self.series_processing[mode].setdefault(
                curve.key, SeriesProcessingSettings()
            )
            output[curve.key] = processor(
                curve,
                self.common_processing[mode],
                setting,
                state.curves,
                self.series_processing[mode],
            )
        self.processed_curves[mode] = output
        if state.auto_axes:
            self._apply_display_auto_range(mode)
        if mode == self.mode_var.get():
            self._refresh_loaded_curves()

    def _reprocess_particle_size(self) -> None:
        state = self.states[PARTICLE_SIZE]
        output: dict[str, ParticleSizeProcessedData] = {}
        for curve in state.ordered_curves():
            setting = self.particle_series_processing.setdefault(
                curve.key, ParticleSizeSeriesSettings()
            )
            output[curve.key] = process_particle_size_curve(
                curve, self.particle_common_processing, setting
            )
        self.particle_processed_curves = output
        if state.auto_axes:
            self._apply_display_auto_range(PARTICLE_SIZE)
        if self.mode_var.get() == PARTICLE_SIZE:
            self._refresh_loaded_curves()

    def _apply_display_auto_range(self, mode: str) -> AxisRange:
        state = self.states[mode]
        curves = (
            tuple(self.particle_processed_curves.values())
            if mode == PARTICLE_SIZE
            else tuple(self.processed_curves[mode].values())
        )
        if not curves:
            return state.apply_auto_range()
        x_min = min(min(curve.display_x) for curve in curves)
        x_max = max(max(curve.display_x) for curve in curves)
        y_min = min(min(curve.display_y) for curve in curves)
        y_max = max(max(curve.display_y) for curve in curves)
        if x_min == x_max:
            x_min, x_max = x_min - 1.0, x_max + 1.0
        span = y_max - y_min
        padding = max(span * 0.05, max(abs(y_min), abs(y_max)) * 0.02, 0.01)
        if y_min == y_max:
            padding = max(abs(y_min) * 0.05, 0.1)
        display_y_min = 0.0 if mode == PARTICLE_SIZE and y_min >= 0 else y_min - padding
        state.axis_range = AxisRange(x_min, x_max, display_y_min, y_max + padding)
        state.auto_axes = True
        return state.axis_range

    def _display_curves(self, mode: Union[str, None] = None):
        selected_mode = mode or self.mode_var.get()
        if selected_mode in {DSC, IR}:
            return tuple(
                self.processed_curves[selected_mode].get(curve.key, raw_processed_curve(curve))
                for curve in self.states[selected_mode].ordered_curves()
            )
        if selected_mode == PARTICLE_SIZE:
            return tuple(
                self.particle_processed_curves.get(
                    curve.key, raw_particle_size_curve(curve)
                )
                for curve in self.states[PARTICLE_SIZE].ordered_curves()
            )
        return self.states[selected_mode].ordered_curves()

    def _display_series(self, mode: Union[str, None] = None) -> tuple[DisplaySeries, ...]:
        return tuple(to_display_series(curve) for curve in self._display_curves(mode))

    def _dsc_analysis_curve(self, key: str) -> CurveData:
        processed = self.processed_curves[DSC].get(key)
        if processed is None:
            processed = raw_processed_curve(self.states[DSC].curves[key])
        if processed.blank_failed:
            reason = " / ".join(processed.warnings) or "ブランク補正に失敗しました。"
            raise DscAnalysisError(
                f"ブランク補正が要求されていますが失敗しています。設定を修正するか補正なしを選択してください。{reason}"
            )
        return processed.as_curve()

    def _begin_ir_normalization_selection(self) -> None:
        if self.mode_var.get() != IR:
            return
        keys = self._selected_loaded_keys()
        if len(keys) != 1:
            messagebox.showinfo("IR規格化", "規格化位置を指定するIR系列を1つ選択してください。", parent=self)
            return
        self.ir_normalization_selection_key = keys[0]
        self.ir_norm_mode_var.set("個別の規格化波数を指定")
        try:
            self.ir_normalization_preview = self._optional_finite_float(
                self.ir_individual_norm_var.get(), "個別規格化波数"
            )
        except ProcessingError:
            self.ir_normalization_preview = None
        self._refresh_plot()
        self._set_status("IRグラフのプロット領域をクリックして規格化波数を選択してください。")

    def _on_ir_norm_entry_changed(self, *_args) -> None:
        if not hasattr(self, "ir_individual_norm_var") or self.mode_var.get() != IR:
            return
        try:
            self.ir_normalization_preview = self._optional_finite_float(
                self.ir_individual_norm_var.get(), "個別規格化波数"
            )
        except ProcessingError:
            self.ir_normalization_preview = None
        if self._graph_window_alive():
            self._refresh_plot()

    def _begin_particle_normalization_selection(self) -> None:
        if self.mode_var.get() != PARTICLE_SIZE:
            return
        keys = self._selected_loaded_keys()
        if len(keys) != 1:
            messagebox.showinfo(
                "指定粒径規格化",
                "規格化位置を指定する粒度分布系列を1つ選択してください。",
                parent=self,
            )
            return
        self.particle_normalization_selection_key = keys[0]
        self.particle_norm_mode_var.set("個別の指定粒径を使用")
        try:
            value = self._optional_finite_float(
                self.particle_individual_norm_var.get(), "個別規格化粒径"
            )
            self.particle_normalization_preview = (
                value if value is not None and value > 0 else None
            )
        except ProcessingError:
            self.particle_normalization_preview = None
        self._refresh_plot()
        self.particle_selection_instruction_var.set(
            "プロット領域をクリックして規格化粒径を選択してください。"
        )
        self._set_status(
            "粒度分布グラフのプロット領域をクリックして規格化粒径を選択してください。"
        )

    def _on_particle_norm_entry_changed(self, *_args) -> None:
        if (
            not hasattr(self, "particle_individual_norm_var")
            or self.mode_var.get() != PARTICLE_SIZE
        ):
            return
        try:
            value = self._optional_finite_float(
                self.particle_individual_norm_var.get(), "個別規格化粒径"
            )
            self.particle_normalization_preview = (
                value if value is not None and value > 0 else None
            )
        except ProcessingError:
            self.particle_normalization_preview = None
        if self._graph_window_alive():
            self._refresh_plot()

    def _restore_last_root(self) -> None:
        root = load_last_root()
        if root is not None:
            self._set_root(root)

    def _choose_root(self) -> None:
        initial = str(self.current_folder) if self.current_folder else None
        selected = filedialog.askdirectory(
            parent=self,
            title=f"{self.mode_var.get()}データのルートフォルダ",
            initialdir=initial,
        )
        if selected:
            self._set_root(Path(selected))

    def _on_mode_changed(self, _event=None) -> None:
        if self.dsc_range_selection is not None:
            self._cancel_dsc_selection(quiet=True)
        previous_mode = self.state_model.measurement_type
        self.selected_curve_keys[previous_mode] = self._selected_loaded_keys()
        mode = self.mode_var.get()
        self.state_model = self.states[mode]
        self.loaded_tree.configure(displaycolumns=visible_main_curve_columns(mode))
        self.tga_panel.grid_remove()
        self.dsc_panel.grid_remove()
        self.ir_panel.grid_remove()
        self.particle_panel.grid_remove()
        self.raw_comparison_panel.grid_remove()
        panel = analysis_panel_for_mode(mode)
        if panel == "tga":
            self.tga_panel.grid()
        elif panel == "dsc":
            self.dsc_panel.grid()
        elif panel == "ir":
            self.ir_panel.grid()
        elif panel == "particle_size":
            self.particle_common_norm_var.set(
                ""
                if self.particle_common_processing.normalization_diameter_um is None
                else f"{self.particle_common_processing.normalization_diameter_um:g}"
            )
            self.particle_panel.grid()
        else:
            self.raw_comparison_panel.configure(text=f"{mode}表示")
            self.raw_comparison_message_var.set(
                f"{mode}初版は、生データの比較表示とExcel出力のみ対応しています。"
            )
            self.raw_comparison_panel.grid()
        if mode != IR:
            self.ir_normalization_selection_key = None
            self.ir_normalization_preview = None
        if mode != PARTICLE_SIZE:
            self.particle_normalization_selection_key = None
            self.particle_normalization_preview = None
            self.particle_selection_instruction_var.set("")
        self._refresh_processing_choices(mode)
        if self._graph_window_alive():
            self.graph_window.update_mode(mode)
        self._refresh_loaded_curves()
        self._sync_axis_entries()
        self._set_status(
            f"{mode}モードへ切り替えました。CSV一覧は共通で、内容は追加時に{mode}形式として読み込みます。"
        )

    def _graph_window_alive(self) -> bool:
        return self.graph_window is not None and self.graph_window.is_alive()

    def _open_graph_window(self) -> GraphWindow:
        self.graph_window, created = ensure_single_window(
            self.graph_window,
            lambda: GraphWindow(self),
        )
        if created:
            self.graph_window.present()
        self.graph_window.update_mode(self.mode_var.get())
        self._refresh_loaded_curves()
        self._sync_axis_entries()
        self._update_dsc_selection_ui()
        self._refresh_plot()
        return self.graph_window

    def _on_graph_window_closing(self, window: GraphWindow) -> None:
        if self.dsc_range_selection is not None:
            self._cancel_dsc_selection(quiet=True)
        self.ir_normalization_selection_key = None
        self.ir_normalization_preview = None
        self.particle_normalization_selection_key = None
        self.particle_normalization_preview = None
        self.particle_selection_instruction_var.set("")
        if self.graph_window is window:
            self.graph_window = None
        window.destroy()

    def _set_root(self, root: Path) -> None:
        if not root.is_dir():
            messagebox.showerror("フォルダエラー", f"フォルダを開けません:\n{root}", parent=self)
            return
        self.root_generation += 1
        self.folder_tree.delete(*self.folder_tree.get_children(""))
        self.folder_tree_paths.clear()
        self.tree_children_loaded.clear()
        self.tree_children_loading.clear()
        label = root.name or str(root)
        root_item = self.folder_tree.insert("", tk.END, text=label, open=True)
        self.folder_tree_paths[root_item] = root
        self.folder_tree.insert(root_item, tk.END, text="読み込み中...")
        self.folder_tree.selection_set(root_item)
        self.folder_tree.focus(root_item)
        self.root_path_var.set(str(root))
        try:
            save_last_root(root)
        except OSError:
            self._set_status("ルートフォルダを開きました（設定の保存には失敗しました）。")
        self._request_tree_children(root_item)
        self._select_folder(root)

    def _on_tree_open(self, _event=None) -> None:
        item = self.folder_tree.focus()
        if item:
            self._request_tree_children(item)

    def _on_tree_select(self, _event=None) -> None:
        selection = self.folder_tree.selection()
        if not selection:
            return
        path = self.folder_tree_paths.get(selection[0])
        if path is not None:
            self._select_folder(path)

    def _request_tree_children(self, item: str) -> None:
        if item in self.tree_children_loaded or item in self.tree_children_loading:
            return
        path = self.folder_tree_paths.get(item)
        if path is None:
            return
        self.tree_children_loading.add(item)
        generation = self.root_generation
        self._submit("tree_children", (item, generation), list_child_directories, path)

    def _select_folder(self, folder: Path) -> None:
        self.current_folder = folder
        self.file_scan_token += 1
        token = self.file_scan_token
        self.current_file_names = []
        self._render_file_names()
        self._set_status(f"ファイル名を取得中: {folder}")
        self._submit("file_names", token, list_csv_names, folder)

    def _refresh_current_folder(self) -> None:
        if self.current_folder is not None:
            self._select_folder(self.current_folder)

    def _render_file_names(self) -> None:
        query = self.search_var.get().strip().casefold() if hasattr(self, "search_var") else ""
        names = [name for name in self.current_file_names if query in name.casefold()]
        self.file_tree.delete(*self.file_tree.get_children(""))
        self.file_item_names.clear()
        for index, name in enumerate(names):
            item = self.file_tree.insert("", tk.END, iid=f"file_{index}", values=(name,))
            self.file_item_names[item] = name
        self.file_count_var.set(f"{len(names)}件")

    def _report_profile_errors(self) -> None:
        if not self.profile_store.errors or self.closed:
            return
        messagebox.showwarning(
            "一部の読込プロファイルを無効化しました",
            "次のJSONプロファイルは読み込めませんでした。ほかのプロファイルとアプリは利用できます。\n\n"
            + "\n".join(self.profile_store.errors),
            parent=self,
        )

    def _selected_file_path(self) -> Union[Path, None]:
        if self.current_folder is None:
            return None
        selected = self.file_tree.selection()
        if len(selected) != 1:
            return None
        name = self.file_item_names.get(selected[0])
        return None if name is None else self.current_folder / name

    def _open_import_settings(self) -> None:
        path = self._selected_file_path()
        if path is None:
            messagebox.showinfo(
                "CSVファイルを1つ選択",
                "読込設定を確認するCSVファイルを1つだけ選択してください。",
                parent=self,
            )
            return
        if self.import_dialog is not None:
            try:
                if self.import_dialog.winfo_exists():
                    self.import_dialog.lift()
                    self.import_dialog.focus_set()
                    return
            except tk.TclError:
                pass
        mode = self.mode_var.get()
        key = path_key(path)
        initial = self.file_profile_overrides.get((mode, key))
        if initial is None:
            initial = self.series_import_profiles[mode].get(key)
        self.import_dialog = ImportSettingsDialog(
            self,
            path,
            mode,
            self.profile_store,
            self._submit,
            self._apply_import_profile,
            initial_profile=initial,
        )

    def _apply_import_profile(self, path: Path, profile: ImportProfile) -> None:
        mode = self.mode_var.get()
        key = path_key(path)
        self.file_profile_overrides[(mode, key)] = profile
        self.series_import_profiles[mode][key] = profile
        self.profiled_loader.invalidate(path)
        event_type = "curves_reloaded" if key in self.states[mode].curves else "curves_loaded"
        self._set_status(f"{path.name}を指定した読込設定で読み込み中...")
        self._submit(
            event_type,
            mode,
            _load_curve_batch,
            [path],
            mode,
            self.profiled_loader,
            {key: profile},
        )

    def _add_selected_files(self) -> None:
        if self.current_folder is None:
            messagebox.showinfo("フォルダ未選択", "先にフォルダを選択してください。", parent=self)
            return
        selected = self.file_tree.selection()
        paths = [self.current_folder / self.file_item_names[item] for item in selected]
        new_paths = [path for path in paths if path_key(path) not in self.state_model.curves]
        if not paths:
            messagebox.showinfo("ファイル未選択", "CSVファイルを選択してください。", parent=self)
            return
        if not new_paths:
            self._set_status("選択したファイルはすでにグラフへ追加されています。")
            return
        self._set_status(f"{len(new_paths)}件のCSVを読み込み中...")
        mode = self.mode_var.get()
        overrides = {
            path_key(path): self.file_profile_overrides[(mode, path_key(path))]
            for path in new_paths
            if (mode, path_key(path)) in self.file_profile_overrides
        }
        self._submit(
            "curves_loaded",
            mode,
            _load_curve_batch,
            new_paths,
            mode,
            self.profiled_loader,
            overrides,
        )

    def _reload_selected_curves(self, tree: Union[ttk.Treeview, None] = None) -> None:
        keys = self._selected_loaded_keys(tree)
        if not keys:
            messagebox.showinfo("曲線未選択", "再読込する曲線を選択してください。", parent=self)
            return
        paths = [self.state_model.curves[key].path for key in keys]
        self._set_status(f"{len(paths)}件を再読込中...")
        mode = self.mode_var.get()
        profiles: dict[str, ImportProfile] = {}
        for key in keys:
            one_time = self.file_profile_overrides.get((mode, key))
            previous = self.series_import_profiles[mode].get(key)
            if one_time is not None:
                profiles[key] = one_time
            elif previous is not None:
                profiles[key] = self.profile_store.get(previous.profile_id) or previous
        self._submit(
            "curves_reloaded",
            mode,
            _load_curve_batch,
            paths,
            mode,
            self.profiled_loader,
            profiles,
        )

    def _remove_selected_curves(self, tree: Union[ttk.Treeview, None] = None) -> None:
        keys = self._selected_loaded_keys(tree)
        if not keys:
            return
        mode = self.mode_var.get()
        self.selected_curve_keys[mode] = [
            key for key in self.selected_curve_keys[mode] if key not in keys
        ]
        if self.dsc_range_selection is not None and self.dsc_range_selection.curve_key in keys:
            self._cancel_dsc_selection(restore=False, quiet=True)
        for key in keys:
            self.state_model.remove_curve(key)
            self.series_import_profiles[mode].pop(key, None)
            if mode == DSC:
                self.dsc_sessions.pop(key, None)
                self.dsc_analysis_tokens.pop(key, None)
                if self.dsc_active_key == key:
                    self.dsc_active_key = None
        if mode in {DSC, IR}:
            if mode == DSC:
                self._invalidate_dsc_sessions(set(self.states[DSC].curves))
            self._refresh_processing_choices(mode)
            self._reprocess_mode(mode)
        elif mode == PARTICLE_SIZE:
            for key in keys:
                self.particle_series_processing.pop(key, None)
                self.particle_processed_curves.pop(key, None)
            self._reprocess_particle_size()
        self._refresh_loaded_curves()
        self._set_status(f"{len(keys)}件をグラフから削除しました。")

    def _editing_selection_keys(self) -> list[str]:
        if self._graph_window_alive():
            return self._selected_loaded_keys(self.graph_window.loaded_tree)
        return self._selected_loaded_keys(self.loaded_tree)

    def _apply_curve_colors(self, colors: dict[str, str]) -> None:
        normalized = {
            key: normalize_color(color)
            for key, color in colors.items()
            if key in self.state_model.curves
        }
        if not normalized:
            return
        selected = self._editing_selection_keys()
        for key, color in normalized.items():
            self.state_model.set_color(key, color)
        self._refresh_loaded_curves()
        self._sync_loaded_tree_selection(selected)
        self._set_status(f"{len(normalized)}系列の色を更新しました。")

    def _apply_curve_legend_names(self, legend_names: dict[str, str]) -> None:
        normalized = {
            key: value.strip()
            for key, value in legend_names.items()
            if key in self.state_model.curves
        }
        if not normalized:
            return
        if any(not value for value in normalized.values()):
            messagebox.showwarning(
                "凡例名",
                "凡例名は空にできません。",
                parent=self.graph_window if self._graph_window_alive() else self,
            )
            return
        selected = self._editing_selection_keys()
        for key, value in normalized.items():
            self.state_model.set_legend_name(key, value)
        self._refresh_loaded_curves()
        self._sync_loaded_tree_selection(selected)
        self._set_status(f"{len(normalized)}系列の凡例名を更新しました。")

    def _set_curve_legend_name(
        self, item: str, legend_name: str, *, parent: Union[tk.Misc, None] = None
    ) -> bool:
        key = self.loaded_item_keys.get(item)
        if key is None or key not in self.state_model.curves:
            return False
        try:
            self.state_model.set_legend_name(key, legend_name)
        except ValueError as exc:
            messagebox.showwarning("凡例名", str(exc), parent=parent or self)
            return False
        curve = self.state_model.curves[key]
        self._refresh_loaded_curves()
        self._sync_loaded_tree_selection([key])
        self._set_status(f"凡例名を「{curve.legend_label}」に変更しました。")
        return True

    def _selected_loaded_keys(self, tree: Union[ttk.Treeview, None] = None) -> list[str]:
        source = tree or self.loaded_tree
        return [
            self.loaded_item_keys[item]
            for item in source.selection()
            if item in self.loaded_item_keys
        ]

    def _on_loaded_curve_selected(self, event=None) -> None:
        tree = event.widget if event is not None else self.loaded_tree
        self._activate_loaded_selection(self._selected_loaded_keys(tree), tree)

    def _on_graph_loaded_curve_selected(self, event=None) -> None:
        if not self._graph_window_alive():
            return
        tree = event.widget if event is not None else self.graph_window.loaded_tree
        self._activate_loaded_selection(self._selected_loaded_keys(tree), tree)

    def _activate_loaded_selection(
        self, keys: list[str], source_tree: ttk.Treeview
    ) -> None:
        self.selected_curve_keys[self.mode_var.get()] = list(keys)
        self._sync_loaded_tree_selection(keys, source_tree)
        if self.mode_var.get() == TGA:
            self._update_tga_td_panel(keys)
            return
        if self.mode_var.get() == IR:
            if len(keys) == 1:
                self._load_processing_controls(keys[0])
            else:
                self.ir_selected_var.set("処理対象のIR系列を1つ選択してください")
            self._refresh_plot()
            return
        if self.mode_var.get() == PARTICLE_SIZE:
            if len(keys) == 1:
                self._load_particle_processing_controls(keys[0])
            else:
                self.particle_selected_var.set(
                    "処理対象の粒度分布系列を1つ選択してください"
                )
            self._refresh_plot()
            return
        if self.mode_var.get() != DSC:
            return
        if not keys:
            return
        if self.dsc_range_selection is not None and (
            len(keys) != 1 or keys[0] != self.dsc_range_selection.curve_key
        ):
            self._cancel_dsc_selection(quiet=True)
        self.dsc_active_key = keys[0]
        self._load_processing_controls(keys[0])
        self._load_dsc_session_controls(keys[0])
        self._refresh_plot()

    def _sync_loaded_tree_selection(
        self, keys: list[str], source_tree: Union[ttk.Treeview, None] = None
    ) -> None:
        item_ids = [
            item for item, key in self.loaded_item_keys.items() if key in set(keys)
        ]
        trees = [self.loaded_tree]
        if self._graph_window_alive():
            trees.append(self.graph_window.loaded_tree)
        for tree in trees:
            if tree is source_tree:
                continue
            current = list(tree.selection())
            if current == item_ids:
                continue
            tree.selection_set(item_ids)
            if item_ids:
                tree.focus(item_ids[0])

    def _on_dsc_result_selected(self, _event=None) -> None:
        selection = self.dsc_result_tree.selection()
        if not selection:
            return
        key = self.dsc_result_item_keys.get(selection[0])
        if key is None or key not in self.state_model.curves:
            return
        if self.dsc_range_selection is not None and key != self.dsc_range_selection.curve_key:
            self._cancel_dsc_selection(quiet=True)
        self.dsc_active_key = key
        self._sync_loaded_tree_selection([key])
        self._load_dsc_session_controls(key)
        self._refresh_plot()

    def _start_dsc_auto_analysis(self, curve: CurveData) -> None:
        try:
            analysis_curve = self._dsc_analysis_curve(curve.key)
        except DscAnalysisError as exc:
            session = self.dsc_sessions.setdefault(
                curve.key,
                DscAnalysisSession(
                    settings=DscAnalysisSettings(heat_flow_unit=curve.heat_flow_unit)
                ),
            )
            session.tg_result = None
            session.melting_result = None
            session.decision = "候補"
            session.status = "ブランク補正失敗・解析不可"
            session.warnings = [str(exc)]
            self._refresh_dsc_results()
            return
        existing = self.dsc_sessions.get(curve.key)
        if existing is None:
            settings = DscAnalysisSettings(
                heat_flow_unit=curve.heat_flow_unit,
                heating_rate_c_min=(
                    curve.heating_rate_c_min
                    if curve.heating_rate_c_min is not None
                    else infer_heating_rate(analysis_curve)
                ),
                sample_mass_mg=curve.sample_mass_mg,
                endotherm_up=True,
                smoothing_window=7,
            )
            self.dsc_sessions[curve.key] = DscAnalysisSession(
                settings=settings,
                status="自動解析中",
            )
        else:
            settings = replace(existing.settings)
            existing.status = "自動解析中"
        token = self.dsc_analysis_tokens.get(curve.key, 0) + 1
        self.dsc_analysis_tokens[curve.key] = token
        self._refresh_dsc_results()
        self._submit(
            "dsc_auto_analyzed",
            (curve.key, token),
            _auto_analyze_dsc_curve,
            replace(analysis_curve),
            settings,
        )

    def _suggest_dsc_selected(self) -> None:
        key = self._current_dsc_key()
        if key is None:
            messagebox.showinfo("DSC曲線未選択", "解析するDSC曲線を選択してください。", parent=self)
            return
        try:
            settings = self._settings_from_dsc_controls()
        except DscAnalysisError as exc:
            messagebox.showerror("DSC解析条件エラー", str(exc), parent=self)
            return
        session = self.dsc_sessions.setdefault(key, DscAnalysisSession(settings=settings))
        session.settings = settings
        session.status = "自動解析中"
        self._start_dsc_auto_analysis(self.state_model.curves[key])
        self._set_status("DSCのTg・融解候補をバックグラウンドで解析中...")

    def _analyze_tg_selected(self) -> None:
        self._begin_dsc_four_point_selection("tg")

    def _analyze_melting_selected(self) -> None:
        self._begin_dsc_four_point_selection("melt")

    def _begin_dsc_four_point_selection(self, analysis_type: str) -> None:
        if self.mode_var.get() != DSC:
            return
        keys = self._selected_loaded_keys()
        if len(keys) != 1:
            reason = (
                "解析対象のDSC曲線を1系列選択してください。"
                if not keys
                else "複数系列が選択されています。解析対象を1系列だけ選択してください。"
            )
            messagebox.showinfo("DSC解析対象", reason, parent=self)
            return
        key = keys[0]
        if self.dsc_range_selection is not None:
            self._cancel_dsc_selection(quiet=True)
        if key != self.dsc_active_key:
            self.dsc_active_key = key
            self._load_dsc_session_controls(key)
        try:
            curve = self._dsc_analysis_curve(key)
        except DscAnalysisError as exc:
            messagebox.showerror("DSCブランク補正エラー", str(exc), parent=self)
            return
        normalized_type = "tg" if analysis_type == "tg" else "melt"
        prefix = normalized_type
        selection_keys = self._selection_control_keys(prefix)
        self.dsc_selection_snapshot = {
            name: self.dsc_range_vars[name].get() for name in selection_keys
        }
        self.dsc_range_selection = DscFourPointSelection(
            analysis_type=normalized_type,
            curve_key=key,
            curve_min_c=min(curve.temperatures),
            curve_max_c=max(curve.temperatures),
        )
        self._syncing_dsc_range_controls = True
        try:
            for name in selection_keys:
                self.dsc_range_vars[name].set("")
        finally:
            self._syncing_dsc_range_controls = False
        self._update_dsc_selection_ui()
        self._refresh_plot()
        self._set_status(
            f"{curve.display_name}: {self.dsc_range_selection.next_instruction}"
        )

    def _selection_control_keys(self, analysis_type: str) -> tuple[str, ...]:
        prefix = "tg" if analysis_type == "tg" else "melt"
        return (
            f"{prefix}_analysis_start",
            f"{prefix}_analysis_end",
            f"{prefix}_pre_start",
            f"{prefix}_pre_end",
            f"{prefix}_post_start",
            f"{prefix}_post_end",
        )

    def _on_plot_left_click(self, event) -> Union[str, None]:
        if self.mode_var.get() == IR and self.ir_normalization_selection_key is not None:
            if not self._graph_window_alive():
                return None
            wavenumber = self.graph_window.plot_canvas.x_from_canvas_point(event.x, event.y)
            if wavenumber is None:
                self._set_status("IRグラフのプロット領域内をクリックしてください。")
                return "break"
            key = self.ir_normalization_selection_key
            self.ir_normalization_preview = round(wavenumber, 2)
            self.ir_individual_norm_var.set(f"{self.ir_normalization_preview:g}")
            apply_clicked_ir_normalization(
                self.series_processing[IR], key, self.ir_normalization_preview
            )
            self._reprocess_mode(IR)
            self._load_processing_controls(key)
            self._set_status(
                f"規格化波数 {self.ir_normalization_preview:g} cm⁻¹ を選択系列へ適用しました。"
            )
            return "break"
        if (
            self.mode_var.get() == PARTICLE_SIZE
            and self.particle_normalization_selection_key is not None
        ):
            if not self._graph_window_alive():
                return None
            diameter = self.graph_window.plot_canvas.x_from_canvas_point(event.x, event.y)
            if diameter is None or diameter <= 0:
                self._set_status("粒度分布グラフのプロット領域内をクリックしてください。")
                return "break"
            key = self.particle_normalization_selection_key
            self.particle_normalization_preview = float(f"{diameter:.6g}")
            self.particle_selection_instruction_var.set(
                f"選択中: {self.particle_normalization_preview:g} µm"
            )
            self.particle_individual_norm_var.set(
                f"{self.particle_normalization_preview:g}"
            )
            apply_clicked_particle_normalization(
                self.particle_series_processing,
                key,
                self.particle_normalization_preview,
            )
            self._reprocess_particle_size()
            self._load_particle_processing_controls(key)
            self._set_status(
                f"規格化粒径 {self.particle_normalization_preview:g} µm を選択系列へ適用しました。"
            )
            return "break"
        selection = self.dsc_range_selection
        if selection is None:
            return None
        if not self._graph_window_alive():
            return None
        temperature = self.graph_window.plot_canvas.temperature_from_canvas_point(
            event.x, event.y
        )
        if temperature is None:
            self._set_status("プロット領域内のDSC曲線付近をクリックしてください。")
            return "break"
        try:
            selection.add_temperature(round(temperature, 2))
        except DscAnalysisError as exc:
            messagebox.showwarning("4点選択エラー", str(exc), parent=self)
            self._set_status(str(exc))
            return "break"
        self._sync_selection_points_to_controls()
        self._update_dsc_selection_ui()
        self._refresh_plot()
        self._set_status(selection.next_instruction)
        return "break"

    def _on_plot_right_click(self, _event) -> Union[str, None]:
        if self.ir_normalization_selection_key is not None:
            self.ir_normalization_selection_key = None
            self.ir_normalization_preview = None
            self._refresh_plot()
            self._set_status("IR規格化位置のクリック選択をキャンセルしました。")
            return "break"
        if self.particle_normalization_selection_key is not None:
            self.particle_normalization_selection_key = None
            self.particle_normalization_preview = None
            self.particle_selection_instruction_var.set("")
            self._refresh_plot()
            self._set_status("粒度分布の規格化位置選択をキャンセルしました。")
            return "break"
        selection = self.dsc_range_selection
        if selection is None:
            return None
        if selection.points:
            self._undo_dsc_selection()
        else:
            self._cancel_dsc_selection()
        return "break"

    def _on_escape_pressed(self, _event=None) -> Union[str, None]:
        if self.ir_normalization_selection_key is not None:
            self.ir_normalization_selection_key = None
            self.ir_normalization_preview = None
            self._refresh_plot()
            self._set_status("IR規格化位置のクリック選択をキャンセルしました。")
            return "break"
        if self.particle_normalization_selection_key is not None:
            self.particle_normalization_selection_key = None
            self.particle_normalization_preview = None
            self.particle_selection_instruction_var.set("")
            self._refresh_plot()
            self._set_status("粒度分布の規格化位置選択をキャンセルしました。")
            return "break"
        if self.dsc_range_selection is None:
            return None
        self._cancel_dsc_selection()
        return "break"

    def _undo_dsc_selection(self) -> None:
        selection = self.dsc_range_selection
        if selection is None:
            return
        selection.undo()
        self._sync_selection_points_to_controls()
        self._update_dsc_selection_ui()
        self._refresh_plot()
        self._set_status(selection.next_instruction)

    def _cancel_dsc_selection(
        self,
        _event=None,
        *,
        restore: bool = True,
        quiet: bool = False,
    ) -> None:
        if self.dsc_range_selection is None:
            return
        if restore:
            self._syncing_dsc_range_controls = True
            try:
                for name, value in self.dsc_selection_snapshot.items():
                    self.dsc_range_vars[name].set(value)
            finally:
                self._syncing_dsc_range_controls = False
        self.dsc_range_selection = None
        self.dsc_selection_snapshot = {}
        self._update_dsc_selection_ui()
        self._refresh_plot()
        if not quiet:
            self._set_status("4点選択をキャンセルしました。既存の解析結果は変更していません。")

    def _sync_selection_points_to_controls(self) -> None:
        selection = self.dsc_range_selection
        if selection is None:
            return
        prefix = selection.analysis_type
        keys = self._selection_control_keys(prefix)
        self._syncing_dsc_range_controls = True
        try:
            for name in keys:
                self.dsc_range_vars[name].set("")
            points = selection.points
            if len(points) >= 1:
                value = f"{points[0]:.2f}"
                self.dsc_range_vars[f"{prefix}_analysis_start"].set(value)
                self.dsc_range_vars[f"{prefix}_pre_start"].set(value)
            if len(points) >= 2:
                self.dsc_range_vars[f"{prefix}_pre_end"].set(f"{points[1]:.2f}")
            if len(points) >= 3:
                self.dsc_range_vars[f"{prefix}_post_start"].set(f"{points[2]:.2f}")
            if len(points) >= 4:
                value = f"{points[3]:.2f}"
                self.dsc_range_vars[f"{prefix}_analysis_end"].set(value)
                self.dsc_range_vars[f"{prefix}_post_end"].set(value)
        finally:
            self._syncing_dsc_range_controls = False

    def _on_dsc_range_var_changed(self, changed_key: str) -> None:
        if self._syncing_dsc_range_controls:
            return
        selection = self.dsc_range_selection
        if selection is None or not changed_key.startswith(f"{selection.analysis_type}_"):
            return
        prefix = selection.analysis_type
        mirror_keys = {
            f"{prefix}_analysis_start": f"{prefix}_pre_start",
            f"{prefix}_pre_start": f"{prefix}_analysis_start",
            f"{prefix}_analysis_end": f"{prefix}_post_end",
            f"{prefix}_post_end": f"{prefix}_analysis_end",
        }
        mirror = mirror_keys.get(changed_key)
        if mirror is not None:
            self._syncing_dsc_range_controls = True
            try:
                self.dsc_range_vars[mirror].set(self.dsc_range_vars[changed_key].get())
            finally:
                self._syncing_dsc_range_controls = False
        point_keys = (
            f"{prefix}_pre_start",
            f"{prefix}_pre_end",
            f"{prefix}_post_start",
            f"{prefix}_post_end",
        )
        points: list[float] = []
        for name in point_keys:
            text = self.dsc_range_vars[name].get().strip()
            if not text:
                break
            try:
                points.append(float(text))
            except ValueError:
                break
        selection.points = points
        self._update_dsc_selection_ui()
        self._refresh_plot()

    def _update_dsc_selection_ui(self) -> None:
        selection = self.dsc_range_selection
        if selection is None:
            self.dsc_selection_instruction_var.set(
                "Tg解析または融解解析を押すと、グラフ上で4点を選択できます。"
            )
            if self._graph_window_alive():
                self.graph_window.clear_selection_state()
            return
        self.dsc_selection_instruction_var.set(selection.next_instruction)
        if self._graph_window_alive():
            self.graph_window.set_selection_state(
                complete=selection.complete,
                has_points=bool(selection.points),
            )

    def _calculate_dsc_selection(self) -> None:
        selection = self.dsc_range_selection
        if selection is None:
            messagebox.showinfo(
                "4点未選択", "Tg解析または融解解析を押して4点を選択してください。", parent=self
            )
            return
        keys = self._selected_loaded_keys()
        if len(keys) != 1 or keys[0] != selection.curve_key:
            messagebox.showerror(
                "DSC解析対象",
                "4点を選択した解析対象の系列を1つだけ選択してください。",
                parent=self,
            )
            return
        try:
            selection.validate()
            settings = settings_with_four_points(
                self._settings_from_dsc_controls(),
                selection.points,
                selection.analysis_type,
            )
        except DscAnalysisError as exc:
            messagebox.showerror("DSC解析条件エラー", str(exc), parent=self)
            self._set_status(str(exc))
            return
        session = self.dsc_sessions.setdefault(
            selection.curve_key, DscAnalysisSession(settings=settings)
        )
        session.settings = settings
        analysis_type = selection.analysis_type
        self._cancel_dsc_selection(restore=False, quiet=True)
        self._start_dsc_manual_analysis(analysis_type)

    def _start_dsc_manual_analysis(self, analysis_type: str) -> None:
        key = self._current_dsc_key()
        if key is None:
            messagebox.showinfo("DSC曲線未選択", "解析するDSC曲線を選択してください。", parent=self)
            return
        try:
            settings = self._settings_from_dsc_controls()
            analysis_curve = self._dsc_analysis_curve(key)
        except DscAnalysisError as exc:
            messagebox.showerror("DSC解析条件エラー", str(exc), parent=self)
            return
        session = self.dsc_sessions.setdefault(key, DscAnalysisSession(settings=settings))
        session.settings = settings
        session.status = "Tg解析中" if analysis_type == "tg" else "融解解析中"
        token = self.dsc_analysis_tokens.get(key, 0) + 1
        self.dsc_analysis_tokens[key] = token
        event_type = "dsc_tg_analyzed" if analysis_type == "tg" else "dsc_melt_analyzed"
        function = analyze_tg if analysis_type == "tg" else analyze_melting
        self._refresh_dsc_results()
        self._submit(
            event_type,
            (key, token),
            function,
            replace(analysis_curve),
            replace(settings),
        )
        self._set_status(f"{'Tg' if analysis_type == 'tg' else '融解'}解析を実行中...")

    def _settings_from_dsc_controls(self) -> DscAnalysisSettings:
        unit_text = self.dsc_unit_var.get().strip()
        unit = None if unit_text == "不明" else unit_text
        rate = self._optional_positive_float(self.dsc_rate_var.get(), "昇温速度")
        mass = self._optional_positive_float(self.dsc_mass_var.get(), "試料重量")
        try:
            smoothing = int(self.dsc_smoothing_var.get())
        except ValueError as exc:
            raise DscAnalysisError("平滑化点数は整数で入力してください。") from exc
        if smoothing < 1:
            raise DscAnalysisError("平滑化点数は1以上にしてください。")
        return DscAnalysisSettings(
            heat_flow_unit=unit,
            heating_rate_c_min=rate,
            sample_mass_mg=mass,
            endotherm_up=self.dsc_direction_var.get() == "上向き",
            smoothing_window=smoothing,
            tg_range=self._range_from_controls("tg_analysis", "Tg解析範囲"),
            tg_pre_range=self._range_from_controls("tg_pre", "Tg前ベースライン範囲"),
            tg_post_range=self._range_from_controls("tg_post", "Tg後ベースライン範囲"),
            melt_range=self._range_from_controls("melt_analysis", "融解解析・積分範囲"),
            melt_pre_range=self._range_from_controls("melt_pre", "融解前ベースライン範囲"),
            melt_post_range=self._range_from_controls("melt_post", "融解後ベースライン範囲"),
        )

    def _range_from_controls(self, prefix: str, label: str) -> Union[TemperatureRange, None]:
        start_text = self.dsc_range_vars[f"{prefix}_start"].get().strip()
        end_text = self.dsc_range_vars[f"{prefix}_end"].get().strip()
        if not start_text and not end_text:
            return None
        if not start_text or not end_text:
            raise DscAnalysisError(f"{label}は開始・終了の両方を入力してください。")
        try:
            selected = TemperatureRange(float(start_text), float(end_text))
        except ValueError as exc:
            raise DscAnalysisError(f"{label}は数値で入力してください。") from exc
        selected.validate(label)
        return selected

    def _optional_positive_float(self, text: str, label: str) -> Union[float, None]:
        stripped = text.strip()
        if not stripped:
            return None
        try:
            value = float(stripped)
        except ValueError as exc:
            raise DscAnalysisError(f"{label}は数値で入力してください。") from exc
        if value <= 0:
            raise DscAnalysisError(f"{label}は0より大きい値にしてください。")
        return value

    def _current_dsc_key(self) -> Union[str, None]:
        if self.mode_var.get() != DSC:
            return None
        keys = self._selected_loaded_keys()
        if keys:
            return keys[0]
        if self.dsc_active_key in self.state_model.curves:
            return self.dsc_active_key
        return next(iter(self.state_model.curves), None)

    def _load_dsc_session_controls(self, key: str) -> None:
        curve = self.state_model.curves.get(key)
        if curve is None:
            return
        session = self.dsc_sessions.get(key)
        if session is None:
            session = DscAnalysisSession(
                settings=DscAnalysisSettings(
                    heat_flow_unit=curve.heat_flow_unit,
                    heating_rate_c_min=(
                        curve.heating_rate_c_min
                        if curve.heating_rate_c_min is not None
                        else infer_heating_rate(curve)
                    ),
                    sample_mass_mg=curve.sample_mass_mg,
                )
            )
            self.dsc_sessions[key] = session
        settings = session.settings
        self.dsc_selected_var.set(curve.display_name)
        self.dsc_unit_var.set(settings.heat_flow_unit or "不明")
        self.dsc_rate_var.set("" if settings.heating_rate_c_min is None else f"{settings.heating_rate_c_min:g}")
        self.dsc_mass_var.set("" if settings.sample_mass_mg is None else f"{settings.sample_mass_mg:g}")
        self.dsc_direction_var.set("上向き" if settings.endotherm_up else "下向き")
        self.dsc_smoothing_var.set(str(settings.smoothing_window))
        ranges = {
            "tg_analysis": settings.tg_range,
            "tg_pre": settings.tg_pre_range,
            "tg_post": settings.tg_post_range,
            "melt_analysis": settings.melt_range,
            "melt_pre": settings.melt_pre_range,
            "melt_post": settings.melt_post_range,
        }
        self._syncing_dsc_range_controls = True
        try:
            for prefix, selected in ranges.items():
                self.dsc_range_vars[f"{prefix}_start"].set(
                    "" if selected is None else f"{selected.start:.2f}"
                )
                self.dsc_range_vars[f"{prefix}_end"].set(
                    "" if selected is None else f"{selected.end:.2f}"
                )
        finally:
            self._syncing_dsc_range_controls = False
        self._sync_dsc_override_entries(session)

    def _sync_dsc_override_entries(self, session: DscAnalysisSession) -> None:
        values = {
            "tg_onset": session.tg_result.onset_c if session.tg_result else None,
            "tg_midpoint": session.tg_result.midpoint_c if session.tg_result else None,
            "tg_inflection": session.tg_result.inflection_c if session.tg_result else None,
            "melt_onset": session.melting_result.onset_c if session.melting_result else None,
            "melt_peak": session.melting_result.peak_c if session.melting_result else None,
            "melt_end": session.melting_result.end_c if session.melting_result else None,
        }
        for key, value in values.items():
            effective = session.overrides.get(key, value)
            self.dsc_override_vars[key].set("" if effective is None else f"{effective:.2f}")

    def _apply_dsc_overrides(self) -> None:
        key = self._current_dsc_key()
        if key is None or key not in self.dsc_sessions:
            return
        session = self.dsc_sessions[key]
        overrides: dict[str, float] = {}
        try:
            for name, variable in self.dsc_override_vars.items():
                text = variable.get().strip()
                if not text:
                    continue
                value = float(text)
                selected_range = (
                    session.tg_result.analysis_range
                    if name.startswith("tg_") and session.tg_result is not None
                    else session.melting_result.analysis_range
                    if name.startswith("melt_") and session.melting_result is not None
                    else None
                )
                if selected_range is None:
                    raise DscAnalysisError(f"{name}の解析結果がないため補正できません。")
                if not selected_range.contains(value):
                    raise DscAnalysisError(f"{name}の補正値が解析範囲外です。")
                overrides[name] = value
        except ValueError as exc:
            messagebox.showerror("解析点補正エラー", "解析点は数値で入力してください。", parent=self)
            return
        except DscAnalysisError as exc:
            messagebox.showerror("解析点補正エラー", str(exc), parent=self)
            return
        session.overrides = overrides
        session.status = "解析点補正済み"
        self._refresh_dsc_results()
        self._refresh_plot()
        self._set_status("DSC解析点の補正値を表示・結果へ反映しました。")

    def _set_dsc_decision(self, decision: str) -> None:
        key = self._current_dsc_key()
        if key is None or key not in self.dsc_sessions:
            return
        session = self.dsc_sessions[key]
        if session.tg_result is None and session.melting_result is None:
            messagebox.showinfo("解析結果なし", "採用・除外する解析結果がありません。", parent=self)
            return
        session.decision = decision
        self._refresh_dsc_results()
        self._set_status(f"{self.state_model.curves[key].display_name} を{decision}にしました。")

    def _refresh_dsc_results(self) -> None:
        if not hasattr(self, "dsc_result_tree"):
            return
        self.dsc_result_tree.delete(*self.dsc_result_tree.get_children(""))
        self.dsc_result_item_keys.clear()
        for index, curve in enumerate(self.states[DSC].ordered_curves()):
            session = self.dsc_sessions.get(curve.key)
            if session is None:
                values = (curve.display_name, measurement_segment_label(curve), "", "", "", "", "", "", "算出不可", "未解析", "")
            else:
                tg = session.tg_result
                melting = session.melting_result
                effective = lambda name, value: session.overrides.get(name, value) if value is not None else None
                warnings = list(session.warnings)
                if tg is not None:
                    warnings.extend(tg.warnings)
                if melting is not None:
                    warnings.extend(melting.warnings)
                warning_text = " / ".join(dict.fromkeys(warnings))
                status = f"{session.decision}・{session.status}"
                values = (
                    curve.display_name,
                    measurement_segment_label(curve),
                    self._format_result(effective("tg_onset", tg.onset_c if tg else None)),
                    self._format_result(effective("tg_midpoint", tg.midpoint_c if tg else None)),
                    self._format_result(effective("tg_inflection", tg.inflection_c if tg else None)),
                    self._format_result(effective("melt_onset", melting.onset_c if melting else None)),
                    self._format_result(effective("melt_peak", melting.peak_c if melting else None)),
                    self._format_result(effective("melt_end", melting.end_c if melting else None)),
                    self._format_result(melting.enthalpy_j_g if melting else None, unavailable="算出不可"),
                    status,
                    warning_text,
                )
            item = f"dsc_result_{index}"
            self.dsc_result_tree.insert("", tk.END, iid=item, values=values)
            self.dsc_result_item_keys[item] = curve.key
            if curve.key == self.dsc_active_key:
                self.dsc_result_tree.selection_set(item)

    @staticmethod
    def _format_result(value: Union[float, None], unavailable: str = "—") -> str:
        return unavailable if value is None else f"{value:.2f}"

    def _apply_manual_range(self) -> None:
        try:
            axis = AxisRange(
                float(self.x_min_var.get()),
                float(self.x_max_var.get()),
                float(self.y_min_var.get()),
                float(self.y_max_var.get()),
            )
            self.state_model.set_manual_range(axis)
        except ValueError as exc:
            messagebox.showerror("表示範囲エラー", str(exc), parent=self)
            return
        self._refresh_plot()
        self._set_status("表示範囲を適用しました。")

    def _apply_auto_range(self) -> None:
        if self.mode_var.get() in {DSC, IR, PARTICLE_SIZE}:
            self._apply_display_auto_range(self.mode_var.get())
        else:
            self.state_model.apply_auto_range()
        self._sync_axis_entries()
        self._refresh_plot()
        self._set_status("表示範囲を自動設定しました。")

    def _sync_axis_entries(self) -> None:
        axis = self.state_model.axis_range
        self.x_min_var.set(f"{axis.x_min:g}")
        self.x_max_var.set(f"{axis.x_max:g}")
        self.y_min_var.set(f"{axis.y_min:g}")
        self.y_max_var.set(f"{axis.y_max:g}")

    def _refresh_loaded_curves(self) -> None:
        mode = self.mode_var.get()
        current_keys = (
            self._selected_loaded_keys()
            if hasattr(self, "loaded_tree") and self.loaded_item_keys
            else []
        )
        preserved_keys = [
            key for key in current_keys if key in self.state_model.curves
        ] or list(self.selected_curve_keys[mode])
        if self._graph_window_alive():
            self.graph_window.cancel_inline_legend_edit()
        self.loaded_item_keys.clear()
        for index, curve in enumerate(self.state_model.ordered_curves()):
            item = f"curve_{index}"
            self.loaded_item_keys[item] = curve.key
        self._populate_loaded_tree(self.loaded_tree)
        if self._graph_window_alive():
            self._populate_loaded_tree(self.graph_window.loaded_tree)
        valid_preserved = [
            key for key in preserved_keys if key in self.state_model.curves
        ]
        self.selected_curve_keys[mode] = list(valid_preserved)
        if self.mode_var.get() == TGA:
            if (
                not valid_preserved
                and self.tga_active_key is not None
                and self.tga_active_key in self.state_model.curves
            ):
                valid_preserved = [self.tga_active_key]
            if self.tga_active_key not in self.state_model.curves:
                self.tga_active_key = None
            self._sync_loaded_tree_selection(valid_preserved)
            self.selected_curve_keys[mode] = list(valid_preserved)
            self._update_tga_td_panel(valid_preserved)
        if self.mode_var.get() == DSC and self.state_model.curves:
            if self.dsc_active_key not in self.state_model.curves:
                self.dsc_active_key = next(iter(self.state_model.curves))
            self._sync_loaded_tree_selection([self.dsc_active_key])
            self.selected_curve_keys[mode] = [self.dsc_active_key]
            self._load_dsc_session_controls(self.dsc_active_key)
        if self.mode_var.get() == DSC:
            self._refresh_dsc_results()
        if self.mode_var.get() in {DSC, IR}:
            self._refresh_processing_choices(self.mode_var.get())
        if self.mode_var.get() == IR:
            if valid_preserved:
                self._sync_loaded_tree_selection(valid_preserved)
                self.selected_curve_keys[mode] = list(valid_preserved)
                if len(valid_preserved) == 1:
                    self._load_processing_controls(valid_preserved[0])
            elif self.state_model.curves:
                first_key = next(iter(self.state_model.curves))
                self._sync_loaded_tree_selection([first_key])
                self.selected_curve_keys[mode] = [first_key]
                self._load_processing_controls(first_key)
        if mode in {UV_VIS, GPC}:
            self._sync_loaded_tree_selection(valid_preserved)
        if mode == PARTICLE_SIZE:
            self._sync_loaded_tree_selection(valid_preserved)
            if len(valid_preserved) == 1:
                self._load_particle_processing_controls(valid_preserved[0])
            elif not valid_preserved and self.state_model.curves:
                first_key = next(iter(self.state_model.curves))
                self._sync_loaded_tree_selection([first_key])
                self.selected_curve_keys[mode] = [first_key]
                self._load_particle_processing_controls(first_key)
        if self.state_model.auto_axes:
            self._sync_axis_entries()
        self._refresh_plot()

    def _populate_loaded_tree(self, tree: ttk.Treeview) -> None:
        tree.delete(*tree.get_children(""))
        for index, curve in enumerate(self.state_model.ordered_curves()):
            item = f"curve_{index}"
            tag = f"color_{index}"
            tree.tag_configure(tag, foreground=curve.color)
            blank = self._processing_blank(curve.key)
            normalization = self._ir_normalization(curve.key)
            particle_normalization = self._particle_normalization(curve.key)
            if tree is self.loaded_tree:
                values = (
                    "■ " + curve.color.upper(),
                    curve.display_name,
                    blank,
                    normalization,
                    particle_normalization,
                    str(curve.path),
                )
            else:
                values = (
                    "■ " + curve.color.upper(),
                    curve.display_name,
                    curve.legend_label,
                    blank,
                    normalization,
                    particle_normalization,
                    str(curve.path),
                )
            tree.insert(
                "",
                tk.END,
                iid=item,
                values=values,
                tags=(tag,),
            )

    def _processing_blank(self, key: str) -> str:
        mode = self.mode_var.get()
        if mode not in {DSC, IR}:
            return "—"
        processed = self.processed_curves[mode].get(key)
        return processing_blank_label(processed)

    def _ir_normalization(self, key: str) -> str:
        if self.mode_var.get() != IR:
            return "—"
        return ir_normalization_label(self.processed_curves[IR].get(key))

    def _particle_normalization(self, key: str) -> str:
        if self.mode_var.get() != PARTICLE_SIZE:
            return "—"
        return particle_size_normalization_label(
            self.particle_processed_curves.get(key)
        )

    def _refresh_plot(self) -> None:
        if not self._graph_window_alive():
            return
        session = None
        dsc_curve = None
        if self.mode_var.get() == DSC and self.dsc_active_key is not None:
            session = self.dsc_sessions.get(self.dsc_active_key)
            processed = self.processed_curves[DSC].get(self.dsc_active_key)
            if processed is not None and not processed.blank_failed:
                dsc_curve = processed.as_curve()
            else:
                dsc_curve = self.state_model.curves.get(self.dsc_active_key)
        self.graph_window.plot_canvas.set_plot(
            self._display_series(),
            self.state_model.axis_range,
            self.state_model.measurement_type,
            dsc_session=session,
            dsc_curve=dsc_curve,
            dsc_selection=(
                self.dsc_range_selection
                if self.dsc_range_selection is not None
                and self.dsc_range_selection.curve_key == self.dsc_active_key
                else None
            ),
            annotation_visibility={
                key: variable.get() for key, variable in self.dsc_visibility_vars.items()
            }
            if hasattr(self, "dsc_visibility_vars")
            else None,
            normalization_wavenumber=(
                self.ir_normalization_preview if self.mode_var.get() == IR else None
            ),
            normalization_diameter_um=(
                self.particle_normalization_preview
                if self.mode_var.get() == PARTICLE_SIZE
                else None
            ),
        )

    def _export_excel(self, parent: Union[tk.Misc, None] = None) -> None:
        dialog_parent = parent or self
        display_series = self._display_series()
        if not display_series:
            messagebox.showinfo(
                "出力対象なし",
                "グラフへ曲線を追加してください。",
                parent=dialog_parent,
            )
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        output = filedialog.asksaveasfilename(
            parent=dialog_parent,
            title="編集可能なExcelグラフを保存",
            defaultextension=".xlsx",
            filetypes=(("Excel workbook", "*.xlsx"),),
            initialfile=f"{self.mode_var.get()}_Comparison_{timestamp}.xlsx",
        )
        if not output:
            return
        snapshot_state = PlotState(
            curves={curve.key: replace(curve) for curve in self.state_model.ordered_curves()},
            axis_range=self.state_model.axis_range,
            auto_axes=self.state_model.auto_axes,
            measurement_type=self.state_model.measurement_type,
        )
        self._set_status("Excelファイルを作成中...")
        self._submit(
            "excel_exported",
            self.mode_var.get(),
            export_excel,
            display_series,
            snapshot_state,
            Path(output),
        )

    def _submit(self, event_type: str, context: object, function, *args) -> None:
        future = self.executor.submit(function, *args)

        def completed(done_future) -> None:
            try:
                result = done_future.result()
                self.events.put((event_type, context, result, None))
            except BaseException as exc:  # transferred to the Tk main thread
                self.events.put((event_type, context, None, exc))

        future.add_done_callback(completed)

    def _poll_events(self) -> None:
        if self.closed:
            return
        while True:
            try:
                event_type, context, result, error = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event_type, context, result, error)
        self.after(80, self._poll_events)

    def _handle_event(
        self, event_type: str, context: object, result: Union[object, None], error: Union[BaseException, None]
    ) -> None:
        if event_type.startswith("import_dialog_"):
            dialog, token = context
            try:
                dialog.handle_background_result(event_type, token, result, error)
            except tk.TclError:
                pass
            return

        if event_type == "tree_children":
            item, generation = context
            self.tree_children_loading.discard(item)
            if generation != self.root_generation or item not in self.folder_tree_paths:
                return
            if error is not None:
                self._replace_tree_children(item, [])
                self._set_status(f"フォルダを開けません: {error}")
                return
            self._replace_tree_children(item, result)
            return

        if event_type == "file_names":
            if context != self.file_scan_token:
                return
            if error is not None:
                self.current_file_names = []
                self._render_file_names()
                messagebox.showerror("フォルダ読込エラー", str(error), parent=self)
                self._set_status("CSVファイル名の取得に失敗しました。")
                return
            self.current_file_names = list(result)
            self._render_file_names()
            self._set_status(
                f"{len(self.current_file_names)}件のCSVファイル名を取得しました（内容は未読込）。"
            )
            return

        if event_type in {"curves_loaded", "curves_reloaded"}:
            if error is not None:
                messagebox.showerror("CSV読込エラー", str(error), parent=self)
                return
            curves, errors = result
            mode = str(context)
            target_state = self.states[mode]
            added = 0
            changed_curves: list[CurveData] = []
            for curve in curves:
                if event_type == "curves_loaded":
                    changed = target_state.add_curve(curve)
                else:
                    changed = target_state.replace_curve(curve)
                added += int(changed)
                if changed:
                    changed_curves.append(curve)
                    provenance = curve.import_provenance
                    if provenance is not None:
                        profile = self.profile_store.get(provenance.profile_id)
                        if profile is not None and profile.fingerprint == provenance.profile_fingerprint:
                            self.series_import_profiles[mode][curve.key] = profile
            if mode in {DSC, IR}:
                for curve in changed_curves:
                    self.series_processing[mode].setdefault(
                        curve.key, SeriesProcessingSettings()
                    )
                self._refresh_processing_choices(mode)
                self._reprocess_mode(mode)
            elif mode == PARTICLE_SIZE:
                for curve in changed_curves:
                    self.particle_series_processing.setdefault(
                        curve.key, ParticleSizeSeriesSettings()
                    )
                self._reprocess_particle_size()
            elif mode == self.mode_var.get():
                self._refresh_loaded_curves()
            if mode == DSC:
                for curve in changed_curves:
                    self._start_dsc_auto_analysis(curve)
            if errors:
                messagebox.showwarning("一部のCSVを読めませんでした", "\n\n".join(errors), parent=self)
            action = "追加" if event_type == "curves_loaded" else "再読込"
            self._set_status(f"{mode}: {added}件を{action}しました。")
            return

        if event_type in {"dsc_auto_analyzed", "dsc_tg_analyzed", "dsc_melt_analyzed"}:
            key, token = context
            if self.dsc_analysis_tokens.get(key) != token:
                return
            session = self.dsc_sessions.get(key)
            if session is None:
                return
            if error is not None:
                session.status = "解析失敗"
                session.warnings.append(str(error))
                self._refresh_dsc_results()
                self._refresh_plot()
                messagebox.showwarning("DSC解析警告", str(error), parent=self)
                self._set_status("DSC解析に失敗しました。既存の曲線・解析結果は保持されています。")
                return
            if event_type == "dsc_auto_analyzed":
                self.dsc_sessions[key] = result
                session = result
                self._set_status("DSCの自動解析候補を作成しました。確認して採用または除外してください。")
            elif event_type == "dsc_tg_analyzed":
                session.tg_result = result
                session.status = "Tg再計算済み"
                session.decision = "候補"
                for name in ("tg_onset", "tg_midpoint", "tg_inflection"):
                    session.overrides.pop(name, None)
                self._set_status("Tg解析を再計算しました。")
            else:
                session.melting_result = result
                session.status = "融解再計算済み"
                session.decision = "候補"
                for name in ("melt_onset", "melt_peak", "melt_end"):
                    session.overrides.pop(name, None)
                self._set_status("融解解析を再計算しました。")
            if key == self.dsc_active_key:
                self._load_dsc_session_controls(key)
            self._refresh_dsc_results()
            self._refresh_plot()
            return

        if event_type == "excel_exported":
            if error is not None:
                messagebox.showerror("Excel出力エラー", str(error), parent=self)
                self._set_status("Excel出力に失敗しました。")
                return
            self._set_status(f"Excelファイルを保存しました: {result}")
            messagebox.showinfo("Excel出力完了", f"保存しました:\n{result}", parent=self)

    def _replace_tree_children(self, item: str, directories: list[Path]) -> None:
        self.folder_tree.delete(*self.folder_tree.get_children(item))
        for directory in directories:
            child = self.folder_tree.insert(item, tk.END, text=directory.name)
            self.folder_tree_paths[child] = directory
            self.folder_tree.insert(child, tk.END, text="読み込み中...")
        self.tree_children_loaded.add(item)

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _on_close(self) -> None:
        self.closed = True
        if self.import_dialog is not None:
            try:
                if self.import_dialog.winfo_exists():
                    self.import_dialog.cancel()
            except tk.TclError:
                pass
            self.import_dialog = None
        if self._graph_window_alive():
            self.graph_window.destroy()
            self.graph_window = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def _load_curve_batch(
    paths: list[Path],
    measurement_type: str = TGA,
    profiled_loader: Union[ProfiledCurveLoader, None] = None,
    profiles: Union[dict[str, ImportProfile], None] = None,
) -> tuple[list[CurveData], list[str]]:
    curves: list[CurveData] = []
    errors: list[str] = []
    loader = {
        TGA: load_tga_csv,
        DSC: load_dsc_csv,
        IR: load_ir_csv,
        UV_VIS: load_uvvis_csv,
        GPC: load_gpc_csv,
        PARTICLE_SIZE: load_particle_size_csv,
    }[measurement_type]
    for path in paths:
        try:
            if profiled_loader is None:
                curves.append(loader(path))
            else:
                selected_profile = (profiles or {}).get(path_key(path))
                curves.append(profiled_loader.load(path, measurement_type, selected_profile))
        except (MeasurementDataError, ImportProfileError) as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return curves, errors


def _auto_analyze_dsc_curve(
    curve: CurveData, settings: DscAnalysisSettings
) -> DscAnalysisSession:
    working = replace(settings)
    suggestions = suggest_dsc_ranges(
        curve,
        endotherm_up=working.endotherm_up,
        smoothing_window=working.smoothing_window,
    )
    working.tg_range = suggestions.tg_range
    working.tg_pre_range = suggestions.tg_pre_range
    working.tg_post_range = suggestions.tg_post_range
    working.melt_range = suggestions.melt_range
    working.melt_pre_range = suggestions.melt_pre_range
    working.melt_post_range = suggestions.melt_post_range
    if working.heating_rate_c_min is None:
        working.heating_rate_c_min = infer_heating_rate(curve)
    warnings = list(suggestions.warnings)
    tg_result = None
    melting_result = None
    if working.tg_range is not None:
        try:
            tg_result = analyze_tg(curve, working)
        except DscAnalysisError as exc:
            warnings.append(str(exc))
    if working.melt_range is not None:
        try:
            melting_result = analyze_melting(curve, working)
        except DscAnalysisError as exc:
            warnings.append(str(exc))
    status = "自動候補" if tg_result is not None or melting_result is not None else "解析失敗"
    return DscAnalysisSession(
        settings=working,
        tg_result=tg_result,
        melting_result=melting_result,
        decision="候補",
        status=status,
        warnings=warnings,
    )


def main() -> None:
    app = TgaAnalyzerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
