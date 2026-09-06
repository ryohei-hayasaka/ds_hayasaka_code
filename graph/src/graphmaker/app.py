"""Main window.

Single window, three panes: data rail | plot | inspector.  The old edition
split the work across a settings window and a graph window, which forced a
round trip for every DSC analysis; here the plot is always on screen and every
control acts on it in place.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple, Union

import math
import queue
import re
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

from . import panels, theme
from .analysis_common import AnalysisError
from .branding import SUPPORTED_MODES_TEXT
from .dialogs import (
    ColorEditorDialog,
    ColorPickerDialog,
    LegendEditorDialog,
    SampleInfoDialog,
    confirm_destructive,
    report_os_error,
)
from .display_series import DisplaySeries, to_display_series
from .dsc_analysis import (
    DscAnalysisError,
    DscAnalysisSession,
    DscAnalysisSettings,
    analyze_melting,
    analyze_tg,
    infer_heating_rate,
    suggest_dsc_ranges,
)
from .dsc_selection import DscFourPointSelection
from .excel_export import export_excel
from .filesystem import find_folders_by_keyword, list_child_directories, list_csv_names
from .generic_selection import XPointSelection
from .import_dialog import ImportSettingsDialog
from .import_profiles import (
    ImportProfile,
    ImportProfileError,
    ProfileStore,
    ProfiledCurveLoader,
)
from .model import (
    ADHESION,
    DSC,
    GPC,
    IR,
    MEASUREMENT_TYPES,
    PARTICLE_SIZE,
    SS_CURVE,
    TEMPERATURE_LOGGER,
    TGA,
    UV_VIS,
    AxisRange,
    CurveData,
    PlotState,
    path_key,
)
from .new_mode_analysis import (
    AdhesionAnalysisSession,
    AdhesionSampleSettings,
    DerivedCurveData,
    SsAnalysisSession,
    SsSampleSettings,
    TemperatureAnalysisSession,
    apply_ss_yield_range,
    auto_analyze_temperature_peak,
    convert_adhesive_force,
    convert_ss_curve,
    calculate_young_modulus,
    detect_ss_maximum_candidate,
)
from .parser import (
    MeasurementDataError,
    load_adhesion_csv,
    load_dsc_csv,
    load_gpc_csv,
    load_ir_csv,
    load_particle_size_csv,
    load_ss_curve_csv,
    load_temperature_logger_csv,
    load_tga_csv,
    load_uvvis_csv,
)
from .particle_size_processing import (
    ParticleSizeCommonSettings,
    ParticleSizeProcessedData,
    ParticleSizeSeriesSettings,
    process_particle_size_curve,
    raw_particle_size_curve,
)
from .plot_view import PlotView
from .processing import (
    BLANK_CAPABLE_MODES,
    NORMALIZATION_CAPABLE_MODES,
    ZERO_CAPABLE_MODES,
    CommonProcessingSettings,
    ProcessedCurveData,
    SeriesProcessingSettings,
    process_dsc_curve,
    process_gpc_curve,
    process_ir_curve,
    process_uvvis_curve,
    raw_processed_curve,
)
from .result_copy import rows_to_tsv
from .results import analysis_table_for_mode, result_spec
from .series_edit import normalize_color
from .settings import load_mode_keywords, load_mode_roots, save_mode_keyword, save_mode_root
from .value_readout import ValueReadoutError, interpolate_display_value
from .widgets import (
    ButtonRow,
    ResultTable,
    ScrollFrame,
    SeriesTable,
    StatusBar,
    Tooltip,
    numeric_entry,
    section_label,
    set_enabled,
)


WINDOW_TITLE = "GraphMaker"

#: Modes whose displayed curves come from a processing pipeline.
PROCESSED_MODES = (DSC, IR, UV_VIS, GPC, PARTICLE_SIZE, SS_CURVE, ADHESION)

#: process_*_curve dispatch, keyed by mode. DSC keeps its own blank-only
#: pipeline; the others share processing.py's generalized _process_curve.
PROCESSORS = {
    DSC: process_dsc_curve,
    IR: process_ir_curve,
    UV_VIS: process_uvvis_curve,
    GPC: process_gpc_curve,
}

#: Unit shown next to a normalization/zero value in the series table and the
#: plot overlay marker, per mode (IR wavenumbers, UV-Vis wavelengths, GPC
#: retention times).
NORMALIZATION_UNIT_LABEL = {IR: "cm⁻¹", UV_VIS: "nm", GPC: "min"}

SERIES_COLUMNS = {
    TGA: ("visible", "legend", "readout", "source"),
    DSC: ("visible", "legend", "readout", "blank", "source"),
    IR: ("visible", "legend", "readout", "blank", "zero", "normalization", "source"),
    UV_VIS: ("visible", "legend", "readout", "blank", "normalization", "source"),
    GPC: ("visible", "legend", "readout", "normalization", "zero", "source"),
    PARTICLE_SIZE: ("visible", "legend", "readout", "particle", "source"),
    TEMPERATURE_LOGGER: ("visible", "legend", "readout", "source"),
    SS_CURVE: ("visible", "legend", "readout", "source"),
    ADHESION: ("visible", "legend", "readout", "source"),
}


class GraphMakerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        theme.init(self)
        self.title("{0} — {1}".format(WINDOW_TITLE, SUPPORTED_MODES_TEXT))
        self.geometry("{0}x{1}".format(theme.scale_int(1440), theme.scale_int(900)))
        self.minsize(theme.scale_int(1060), theme.scale_int(700))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- per-mode state (each mode keeps its own curves and settings) --
        self.mode_var = tk.StringVar(value=TGA)
        self.states = {
            mode: PlotState(measurement_type=mode) for mode in MEASUREMENT_TYPES
        }
        self.state_model = self.states[TGA]
        self.selected_curve_keys = {mode: [] for mode in MEASUREMENT_TYPES}
        self.curve_visibility = {mode: {} for mode in MEASUREMENT_TYPES}
        self.value_readout_x = {mode: None for mode in MEASUREMENT_TYPES}
        self.value_readout_values = {mode: {} for mode in MEASUREMENT_TYPES}
        self.series_import_profiles = {mode: {} for mode in MEASUREMENT_TYPES}

        self.common_processing = {
            DSC: CommonProcessingSettings(),
            IR: CommonProcessingSettings(),
            UV_VIS: CommonProcessingSettings(),
            GPC: CommonProcessingSettings(),
        }
        self.series_processing = {DSC: {}, IR: {}, UV_VIS: {}, GPC: {}}
        self.processed_curves = {DSC: {}, IR: {}, UV_VIS: {}, GPC: {}}
        self.blank_choice_keys = {DSC: {}, IR: {}, UV_VIS: {}}

        self.particle_common_processing = ParticleSizeCommonSettings()
        self.particle_series_processing = {}
        self.particle_processed_curves = {}

        self.temperature_sessions = {}
        self.ss_settings = {}
        self.ss_processed_curves = {}
        self.ss_sessions = {}
        self.adhesion_settings = {}
        self.adhesion_processed_curves = {}
        self.adhesion_sessions = {}

        self.dsc_sessions = {}
        self.dsc_analysis_tokens = {}
        self.dsc_active_key = None
        self.dsc_range_selection = None  # type: Optional[DscFourPointSelection]

        self.generic_selection = None  # type: Optional[XPointSelection]
        self.generic_selection_kind = None
        self.zero_selection_key = None
        self.zero_preview = None
        self.normalization_selection_key = None
        self.normalization_preview = None
        self.particle_normalization_selection_key = None
        self.particle_normalization_preview = None

        self.tga_active_key = None
        self.tga_custom_results = {}

        # ---- infrastructure ------------------------------------------------
        self.mode_roots = load_mode_roots()
        self.mode_keywords = load_mode_keywords()
        self.current_root = None  # type: Optional[Path]
        self.folder_search_token = 0
        self.folder_search_results = []  # type: List[Path]
        self.folder_search_item_paths = {}
        self.profile_store = ProfileStore()
        self.profiled_loader = ProfiledCurveLoader(self.profile_store)
        self.file_profile_overrides = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="graphmaker")
        self.events = queue.Queue()
        self.closed = False
        self.root_generation = 0
        self.file_scan_token = 0
        self.folder_tree_paths = {}
        self.tree_children_loaded = set()
        self.tree_children_loading = set()
        self.current_folder = None  # type: Optional[Path]
        self.current_file_names = []
        self.file_item_names = {}
        self.sample_info_dialog = None
        self.import_dialog = None
        self._syncing_axis = False

        self._build_ui()
        self.bind("<Escape>", self._on_escape)
        self.after(80, self._poll_events)
        self.after(120, self._restore_last_root)
        self._on_mode_changed()
        if self.profile_store.errors:
            self.after(200, self._report_profile_errors)

    # =====================================================================
    # Layout
    # =====================================================================
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_app_bar()

        self.paned = ttk.Panedwindow(self, orient="horizontal")
        self.paned.grid(row=1, column=0, sticky="nsew",
                        padx=theme.SPACE_S, pady=(theme.SPACE_S, 0))

        self.rail = ttk.Frame(self.paned, style="Panel.TFrame")
        self.center = ttk.Frame(self.paned, style="Panel.TFrame")
        self.inspector = ttk.Frame(self.paned, style="Panel.TFrame")
        self.paned.add(self.rail, weight=0)
        self.paned.add(self.center, weight=1)
        self.paned.add(self.inspector, weight=0)
        self._rail_visible = True
        self._inspector_visible = True

        self._build_rail(self.rail)
        self._build_center(self.center)
        self._build_inspector(self.inspector)
        self._build_status_bar()
        self.after(180, self._set_initial_sashes)

    def _set_initial_sashes(self) -> None:
        try:
            self.paned.sashpos(0, theme.scale_int(theme.LEFT_RAIL_WIDTH))
            total = self.paned.winfo_width()
            self.paned.sashpos(1, total - theme.scale_int(theme.INSPECTOR_WIDTH))
        except tk.TclError:
            pass

    # -- app bar -----------------------------------------------------------
    def _build_app_bar(self) -> None:
        bar = ttk.Frame(self, style="Bar.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(3, weight=1)

        ttk.Label(bar, text=WINDOW_TITLE, style="Title.TLabel").grid(
            row=0, column=0, sticky="w",
            padx=(theme.SPACE_L, theme.SPACE_XL), pady=theme.SPACE_M,
        )
        ttk.Label(bar, text="測定モード", style="PanelSecond.TLabel").grid(
            row=0, column=1, sticky="e", padx=(0, theme.SPACE_S)
        )
        self.mode_box = ttk.Combobox(
            bar,
            textvariable=self.mode_var,
            values=MEASUREMENT_TYPES,
            state="readonly",
            style="Mode.TCombobox",
            width=10,
        )
        self.mode_box.grid(row=0, column=2, sticky="w")
        self.mode_box.bind("<<ComboboxSelected>>", self._on_mode_changed)

        self.root_path_var = tk.StringVar(value="データフォルダ未選択")
        folder = ttk.Frame(bar, style="Bar.TFrame")
        folder.grid(row=0, column=3, sticky="ew", padx=theme.SPACE_XL)
        folder.columnconfigure(1, weight=1)
        ttk.Button(folder, text="フォルダ…", style="Ghost.TButton",
                   command=self._choose_root).grid(row=0, column=0)
        ttk.Label(
            folder, textvariable=self.root_path_var, style="PanelSecond.TLabel", anchor="w"
        ).grid(row=0, column=1, sticky="ew", padx=(theme.SPACE_S, 0))

        self.excel_button = ttk.Button(
            bar, text="Excelへ出力", style="Accent.TButton", command=self._export_excel
        )
        self.excel_button.grid(row=0, column=4, padx=(0, theme.SPACE_L))
        self.excel_tooltip = Tooltip(self.excel_button, "先にCSVをグラフへ追加してください。")

        ttk.Frame(self, style="Divider.TFrame", height=1).grid(
            row=0, column=0, sticky="sew"
        )

    # -- left rail ---------------------------------------------------------
    def _build_rail(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        parent.rowconfigure(4, weight=2)

        head = ttk.Frame(parent, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew",
                  padx=theme.SPACE_M, pady=(theme.SPACE_M, theme.SPACE_S))
        head.columnconfigure(0, weight=1)
        section_label(head, "データ").grid(row=0, column=0, sticky="w")
        ttk.Button(head, text="⟨", width=3, style="Rail.TButton",
                   command=self.toggle_rail).grid(row=0, column=1, sticky="e")

        # Search and the manual tree are two routes to the same folder, so they
        # share one tabbed area instead of stacking and squeezing the CSV list.
        self.folder_notebook = ttk.Notebook(parent)
        self.folder_notebook.grid(row=1, column=0, sticky="nsew",
                                  padx=theme.SPACE_M, pady=(0, theme.SPACE_M))
        search_tab = ttk.Frame(self.folder_notebook, style="Panel.TFrame",
                               padding=theme.SPACE_S)
        tree_tab = ttk.Frame(self.folder_notebook, style="Panel.TFrame",
                             padding=theme.SPACE_S)
        self.folder_notebook.add(search_tab, text="フォルダ検索")
        self.folder_notebook.add(tree_tab, text="フォルダ")

        search_tab.columnconfigure(0, weight=1)
        search_tab.rowconfigure(2, weight=1)
        keyword_row = ttk.Frame(search_tab, style="Panel.TFrame")
        keyword_row.grid(row=0, column=0, sticky="ew")
        keyword_row.columnconfigure(0, weight=1)
        self.keyword_var = tk.StringVar()
        keyword_entry = ttk.Entry(keyword_row, textvariable=self.keyword_var, style="Panel.TEntry")
        keyword_entry.grid(row=0, column=0, sticky="ew")
        keyword_entry.bind("<Return>", lambda _e: self._search_folders())
        ttk.Button(keyword_row, text="検索", command=self._search_folders).grid(
            row=0, column=1, padx=(theme.SPACE_S, 0)
        )

        result_head = ttk.Frame(search_tab, style="Panel.TFrame")
        result_head.grid(row=1, column=0, sticky="ew",
                         pady=(theme.SPACE_XS, theme.SPACE_XS))
        result_head.columnconfigure(0, weight=1)
        self.folder_filter_var = tk.StringVar()
        ttk.Entry(result_head, textvariable=self.folder_filter_var, style="Panel.TEntry").grid(
            row=0, column=0, sticky="ew"
        )
        self.folder_search_count_var = tk.StringVar(value="")
        ttk.Label(
            result_head, textvariable=self.folder_search_count_var, style="PanelSecond.TLabel"
        ).grid(row=0, column=1, sticky="e", padx=(theme.SPACE_S, 0))
        self.folder_filter_var.trace_add("write", lambda *_a: self._render_folder_search_results())

        search_result_holder = ttk.Frame(search_tab, style="Panel.TFrame")
        search_result_holder.grid(row=2, column=0, sticky="nsew")
        search_result_holder.rowconfigure(0, weight=1)
        search_result_holder.columnconfigure(0, weight=1)
        self.folder_search_tree = ttk.Treeview(
            search_result_holder, show="tree", selectmode="browse"
        )
        self.folder_search_tree.grid(row=0, column=0, sticky="nsew")
        search_result_scroll = ttk.Scrollbar(
            search_result_holder, orient="vertical", command=self.folder_search_tree.yview
        )
        search_result_scroll.grid(row=0, column=1, sticky="ns")
        self.folder_search_tree.configure(yscrollcommand=search_result_scroll.set)
        self.folder_search_tree.bind(
            "<<TreeviewSelect>>", lambda _e: self._on_folder_search_select()
        )

        tree_tab.rowconfigure(0, weight=1)
        tree_tab.columnconfigure(0, weight=1)
        self.folder_tree = ttk.Treeview(tree_tab, show="tree", selectmode="browse")
        self.folder_tree.grid(row=0, column=0, sticky="nsew")
        folder_scroll = ttk.Scrollbar(
            tree_tab, orient="vertical", command=self.folder_tree.yview
        )
        folder_scroll.grid(row=0, column=1, sticky="ns")
        self.folder_tree.configure(yscrollcommand=folder_scroll.set)
        self.folder_tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.folder_tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        files_head = ttk.Frame(parent, style="Panel.TFrame")
        files_head.grid(row=2, column=0, sticky="ew", padx=theme.SPACE_M)
        files_head.columnconfigure(1, weight=1)
        ttk.Label(files_head, text="CSVファイル", style="PanelSecond.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.file_count_var = tk.StringVar(value="0件")
        ttk.Label(files_head, textvariable=self.file_count_var, style="PanelSecond.TLabel").grid(
            row=0, column=2, sticky="e"
        )

        self.search_var = tk.StringVar()
        search = ttk.Entry(parent, textvariable=self.search_var, style="Panel.TEntry")
        search.grid(row=3, column=0, sticky="ew",
                    padx=theme.SPACE_M, pady=(theme.SPACE_XS, theme.SPACE_XS))
        self.search_var.trace_add("write", lambda *_a: self._render_file_names())

        file_holder = ttk.Frame(parent, style="Panel.TFrame")
        file_holder.grid(row=4, column=0, sticky="nsew", padx=theme.SPACE_M)
        file_holder.rowconfigure(0, weight=1)
        file_holder.columnconfigure(0, weight=1)
        self.file_tree = ttk.Treeview(
            file_holder, columns=("name",), show="headings", selectmode="extended"
        )
        self.file_tree.heading("name", text="ファイル名")
        self.file_tree.column("name", width=theme.scale_int(220), stretch=True)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        file_scroll = ttk.Scrollbar(
            file_holder, orient="vertical", command=self.file_tree.yview
        )
        file_scroll.grid(row=0, column=1, sticky="ns")
        self.file_tree.configure(yscrollcommand=file_scroll.set)
        self.file_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_file_actions())
        self.file_tree.bind("<Double-Button-1>", lambda _e: self._add_selected_files())

        actions = ttk.Frame(parent, style="Panel.TFrame")
        actions.grid(row=5, column=0, sticky="ew",
                     padx=theme.SPACE_M, pady=theme.SPACE_M)
        actions.columnconfigure(0, weight=1)
        self.add_button = ttk.Button(
            actions, text="グラフへ追加", style="Accent.TButton",
            command=self._add_selected_files,
        )
        self.add_button.grid(row=0, column=0, sticky="ew")
        self.add_tooltip = Tooltip(self.add_button, "左の一覧からCSVを選択してください。")
        self.import_button = ttk.Button(
            actions, text="読込設定", command=self._open_import_settings
        )
        self.import_button.grid(row=0, column=1, padx=(theme.SPACE_S, 0))
        self.import_tooltip = Tooltip(
            self.import_button, "読込設定を確認するCSVを1つだけ選択してください。"
        )

    # -- centre ------------------------------------------------------------
    def _build_center(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(parent, style="Panel.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew",
                     padx=theme.SPACE_M, pady=(theme.SPACE_M, theme.SPACE_S))
        toolbar.columnconfigure(2, weight=1)

        self.rail_toggle = ttk.Button(
            toolbar, text="⟨", width=3, style="Rail.TButton", command=self.toggle_rail
        )
        self.rail_toggle.grid(row=0, column=0, padx=(0, theme.SPACE_M))
        Tooltip(self.rail_toggle, "データパネルの表示/非表示")

        # Axis range and readout live here and nowhere else (D6).
        axis = ttk.Frame(toolbar, style="Panel.TFrame")
        axis.grid(row=0, column=1, sticky="w")
        self.x_min_var = tk.StringVar()
        self.x_max_var = tk.StringVar()
        self.y_min_var = tk.StringVar()
        self.y_max_var = tk.StringVar()
        column = 0
        for label, variable in (
            ("X", self.x_min_var), ("–", self.x_max_var),
            ("Y", self.y_min_var), ("–", self.y_max_var),
        ):
            ttk.Label(axis, text=label, style="PanelSecond.TLabel").grid(
                row=0, column=column,
                padx=(theme.SPACE_M if label in ("X", "Y") and column else 0, theme.SPACE_XS),
            )
            entry = numeric_entry(axis, variable, width=8)
            entry.grid(row=0, column=column + 1)
            entry.bind("<Return>", lambda _e: self._apply_manual_range())
            entry.bind("<FocusOut>", lambda _e: self._apply_manual_range(quiet=True))
            column += 2
        ttk.Button(axis, text="自動範囲", command=self._apply_auto_range).grid(
            row=0, column=column, padx=(theme.SPACE_M, 0)
        )

        readout = ttk.Frame(toolbar, style="Panel.TFrame")
        readout.grid(row=0, column=3, sticky="e", padx=(theme.SPACE_L, theme.SPACE_M))
        ttk.Label(readout, text="読取X", style="PanelSecond.TLabel").grid(
            row=0, column=0, padx=(0, theme.SPACE_XS)
        )
        self.value_readout_x_var = tk.StringVar()
        readout_entry = numeric_entry(readout, self.value_readout_x_var, width=10)
        readout_entry.grid(row=0, column=1)
        readout_entry.bind("<Return>", lambda _e: self._calculate_value_readout())
        ttk.Button(readout, text="解除", style="Ghost.TButton",
                   command=self._clear_value_readout).grid(
            row=0, column=2, padx=(theme.SPACE_XS, 0)
        )

        self.inspector_toggle = ttk.Button(
            toolbar, text="⟩", width=3, style="Rail.TButton", command=self.toggle_inspector
        )
        self.inspector_toggle.grid(row=0, column=4, sticky="e")
        Tooltip(self.inspector_toggle, "インスペクタの表示/非表示")

        self.axis_hint_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.axis_hint_var, style="PanelError.TLabel").grid(
            row=1, column=0, sticky="w", padx=theme.SPACE_M
        )

        plot_holder = ttk.Frame(parent, style="Panel.TFrame")
        plot_holder.grid(row=2, column=0, sticky="nsew",
                         padx=theme.SPACE_M, pady=(0, theme.SPACE_M))
        plot_holder.rowconfigure(0, weight=1)
        plot_holder.columnconfigure(0, weight=1)
        self.plot = PlotView(plot_holder)
        self.plot.grid(row=0, column=0, sticky="nsew")
        self.plot.set_click_handler(self._on_plot_click)
        self.plot.set_alt_click_handler(self._on_plot_alt_click)

        self.instruction_var = tk.StringVar(value="")
        self.instruction_label = ttk.Label(
            parent, textvariable=self.instruction_var, style="Status.TLabel", anchor="w"
        )
        self.instruction_label.grid(row=3, column=0, sticky="ew",
                                    padx=theme.SPACE_M, pady=(0, theme.SPACE_S))

    # -- inspector ---------------------------------------------------------
    def _build_inspector(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        head = ttk.Frame(parent, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew",
                  padx=theme.SPACE_M, pady=(theme.SPACE_M, theme.SPACE_S))
        head.columnconfigure(1, weight=1)
        ttk.Button(head, text="⟩", width=3, style="Rail.TButton",
                   command=self.toggle_inspector).grid(row=0, column=0, sticky="w")
        self.inspector_title_var = tk.StringVar(value="TGA")
        ttk.Label(head, textvariable=self.inspector_title_var, style="PanelHead.TLabel").grid(
            row=0, column=1, sticky="w", padx=(theme.SPACE_S, 0)
        )

        # Series selection stays visible at all times; only 処理/解析/結果 are
        # tabbed below it. Switching to an analysis tab used to hide the series
        # table, forcing a tab round-trip every time the selection changed.
        self.inspector_split = ttk.Panedwindow(parent, orient="vertical")
        self.inspector_split.grid(row=1, column=0, sticky="nsew",
                                  padx=theme.SPACE_S, pady=(0, theme.SPACE_S))

        self.series_tab = ttk.Frame(self.inspector_split, style="Panel.TFrame")
        self.inspector_split.add(self.series_tab, weight=1)
        self._build_series_tab(self.series_tab)

        tabs_holder = ttk.Frame(self.inspector_split, style="Panel.TFrame")
        self.inspector_split.add(tabs_holder, weight=3)
        tabs_holder.columnconfigure(0, weight=1)
        tabs_holder.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(tabs_holder)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.processing_tab = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.analysis_tab = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.results_tab = ttk.Frame(self.notebook, style="Panel.TFrame")
        for frame, label in (
            (self.processing_tab, "処理"),
            (self.analysis_tab, "解析"),
            (self.results_tab, "結果"),
        ):
            self.notebook.add(frame, text=label)

        self._build_results_tab(self.results_tab)

        self.processing_scroll = ScrollFrame(self.processing_tab)
        self.processing_scroll.pack(fill="both", expand=True)
        self.analysis_scroll = ScrollFrame(self.analysis_tab)
        self.analysis_scroll.pack(fill="both", expand=True)
        self.panels = panels.create_panels(
            self, self.processing_scroll.body, self.analysis_scroll.body
        )
        self.after(180, self._set_inspector_sash)

    def _set_inspector_sash(self) -> None:
        try:
            self.inspector_split.sashpos(0, theme.scale_int(280))
        except tk.TclError:
            pass

    def _build_series_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        self.series_summary_var = tk.StringVar(value="選択中：なし")
        ttk.Label(parent, textvariable=self.series_summary_var, style="PanelSecond.TLabel").grid(
            row=0, column=0, sticky="w", padx=theme.SPACE_M, pady=(theme.SPACE_M, theme.SPACE_XS)
        )
        self.series_table = SeriesTable(
            parent,
            on_selection_changed=self._on_series_selection,
            on_toggle_visible=self._toggle_curve_visibility,
            on_pick_color=self._pick_series_color,
            on_rename=self._set_curve_legend_name,
        )
        self.series_table.grid(row=1, column=0, sticky="nsew", padx=theme.SPACE_M)

        actions = ttk.Frame(parent, style="Panel.TFrame")
        actions.grid(row=2, column=0, sticky="ew",
                     padx=theme.SPACE_M, pady=theme.SPACE_M)
        row_one = ButtonRow(actions)
        row_one.grid(row=0, column=0, sticky="w")
        self.color_button = row_one.add("色を一括変更", self._open_color_editor)
        self.legend_button = row_one.add("凡例名を編集", self._open_legend_editor)
        row_two = ButtonRow(actions)
        row_two.grid(row=1, column=0, sticky="w", pady=(theme.SPACE_S, 0))
        self.reload_button = row_two.add("再読込", self._reload_selected_curves)
        self.remove_button = row_two.add("削除", self._remove_selected_curves, style="Danger.TButton")
        row_two.add("すべて表示", self._show_all_curves)
        self.reload_tooltip = Tooltip(self.reload_button, "系列を選択してください。")
        self.remove_tooltip = Tooltip(self.remove_button, "系列を選択してください。")
        self.color_tooltip = Tooltip(self.color_button, "先に系列を追加してください。")
        self.legend_tooltip = Tooltip(self.legend_button, "先に系列を追加してください。")

    def _build_results_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        self.results_caption_var = tk.StringVar(value="")
        ttk.Label(
            parent, textvariable=self.results_caption_var,
            style="PanelSecond.TLabel", justify="left",
        ).grid(row=0, column=0, sticky="w",
               padx=theme.SPACE_M, pady=(theme.SPACE_M, theme.SPACE_XS))
        self.results_holder = ttk.Frame(parent, style="Panel.TFrame")
        self.results_holder.grid(row=1, column=0, sticky="nsew", padx=theme.SPACE_M)
        self.results_holder.rowconfigure(0, weight=1)
        self.results_holder.columnconfigure(0, weight=1)
        self.result_table = None  # type: Optional[ResultTable]
        self._result_mode = None

        copy_row = ButtonRow(parent)
        copy_row.grid(row=2, column=0, sticky="w",
                      padx=theme.SPACE_M, pady=theme.SPACE_M)
        copy_row.add("表全体をコピー", lambda: self._copy_results(True))
        copy_row.add("選択行をコピー", lambda: self._copy_results(False))

    def _build_status_bar(self) -> None:
        ttk.Frame(self, style="Divider.TFrame", height=1).grid(
            row=2, column=0, sticky="ew"
        )
        self.status = StatusBar(self)
        self.status.grid(row=3, column=0, sticky="ew")

    # -- pane collapsing ---------------------------------------------------
    def toggle_rail(self) -> None:
        if self._rail_visible:
            self.paned.forget(self.rail)
            self._rail_visible = False
            self.rail_toggle.configure(text="⟩")
        else:
            self.paned.insert(0, self.rail, weight=0)
            self._rail_visible = True
            self.rail_toggle.configure(text="⟨")
            self.after(60, lambda: self._restore_sash(0, theme.LEFT_RAIL_WIDTH))

    def toggle_inspector(self) -> None:
        if self._inspector_visible:
            self.paned.forget(self.inspector)
            self._inspector_visible = False
            self.inspector_toggle.configure(text="⟨")
        else:
            self.paned.add(self.inspector, weight=0)
            self._inspector_visible = True
            self.inspector_toggle.configure(text="⟩")
            self.after(60, self._restore_inspector_sash)

    def _restore_sash(self, index: int, width: int) -> None:
        try:
            self.paned.sashpos(index, theme.scale_int(width))
        except tk.TclError:
            pass

    def _restore_inspector_sash(self) -> None:
        try:
            index = 1 if self._rail_visible else 0
            self.paned.sashpos(
                index, self.paned.winfo_width() - theme.scale_int(theme.INSPECTOR_WIDTH)
            )
        except tk.TclError:
            pass

    # =====================================================================
    # Notifications
    # =====================================================================
    def notify(self, message: str, level: str = "info") -> None:
        self.status.show(message, level)

    def set_instruction(self, message: str) -> None:
        self.instruction_var.set(message)
        self.instruction_label.configure(
            style="StatusSuccess.TLabel" if message else "Status.TLabel"
        )

    # =====================================================================
    # Mode switching
    # =====================================================================
    @property
    def mode(self) -> str:
        return self.mode_var.get()

    def _on_mode_changed(self, _event=None) -> None:
        self._cancel_all_selections(quiet=True)
        previous = self.state_model.measurement_type
        self.selected_curve_keys[previous] = self.series_table.selected_keys() if hasattr(
            self, "series_table"
        ) else []
        mode = self.mode
        self.state_model = self.states[mode]
        self.inspector_title_var.set(mode)
        self.series_table.set_visible_columns(SERIES_COLUMNS[mode])

        saved_readout = self.value_readout_x.get(mode)
        self.value_readout_x_var.set(
            "" if saved_readout is None else "{0:.8g}".format(saved_readout)
        )

        self.keyword_var.set(self.mode_keywords.get(mode, mode))
        self.folder_search_results = []
        self.folder_filter_var.set("")

        for panel in self.panels.values():
            panel.hide()
        panel = self.panels[mode]
        panel.show()
        self._configure_tabs(panel)

        self._refresh_processing_choices(mode)
        self._rebuild_result_table(mode)
        self._refresh_all()

        mode_root = self.mode_roots.get(mode) or self.mode_roots.get("__legacy__")
        if mode_root is not None and mode_root != self.current_folder:
            self._set_root(mode_root)
            self.notify("{0}モードへ切り替え、保存済みのデータフォルダを復元しました。".format(mode))
        else:
            self.notify("{0}モードへ切り替えました。".format(mode))

    def _configure_tabs(self, panel) -> None:
        """Hide processing/analysis tabs a mode does not use (no empty tabs)."""
        self._set_tab_visible(self.processing_tab, panel.has_processing, "処理", 0)
        self._set_tab_visible(self.analysis_tab, panel.has_analysis, "解析", 1)
        if panel.has_analysis:
            self.notebook.select(self.analysis_tab)
        elif panel.has_processing:
            self.notebook.select(self.processing_tab)
        else:
            self.notebook.select(self.results_tab)

    def _set_tab_visible(self, frame: ttk.Frame, visible: bool, label: str, index: int) -> None:
        tabs = self.notebook.tabs()
        present = str(frame) in tabs
        if visible and not present:
            self.notebook.insert(min(index, len(tabs)), frame, text=label)
        elif not visible and present:
            self.notebook.hide(frame)

    # =====================================================================
    # Folder browsing
    # =====================================================================
    def _restore_last_root(self) -> None:
        root = self.mode_roots.get(self.mode) or self.mode_roots.get("__legacy__")
        if root is not None:
            self._set_root(root)

    def _choose_root(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="{0}データのルートフォルダ".format(self.mode),
            initialdir=str(self.current_folder) if self.current_folder else None,
        )
        if selected:
            self._set_root(Path(selected))

    def _set_root(self, root: Path) -> None:
        if not root.is_dir():
            report_os_error(self, "フォルダエラー", "フォルダを開けません:\n{0}".format(root))
            return
        self.current_root = root
        self.root_generation += 1
        self.folder_tree.delete(*self.folder_tree.get_children(""))
        self.folder_tree_paths.clear()
        self.tree_children_loaded.clear()
        self.tree_children_loading.clear()
        item = self.folder_tree.insert("", "end", text=root.name or str(root), open=True)
        self.folder_tree_paths[item] = root
        self.folder_tree.insert(item, "end", text="読み込み中…")
        self.folder_tree.selection_set(item)
        self.folder_tree.focus(item)
        self.root_path_var.set(str(root))
        try:
            self.mode_roots[self.mode] = root
            save_mode_root(self.mode, root)
        except OSError:
            self.notify("フォルダを開きましたが、設定の保存に失敗しました。", "warning")
        self._request_tree_children(item)
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
        self._submit(
            "tree_children", (item, self.root_generation), list_child_directories, path
        )

    def _select_folder(self, folder: Path) -> None:
        self.current_folder = folder
        self.file_scan_token += 1
        self.current_file_names = []
        self._render_file_names()
        self._submit("file_names", self.file_scan_token, list_csv_names, folder)

    def _render_file_names(self) -> None:
        query = self.search_var.get().strip().casefold()
        names = [name for name in self.current_file_names if query in name.casefold()]
        self.file_tree.delete(*self.file_tree.get_children(""))
        self.file_item_names.clear()
        for index, name in enumerate(names):
            item = self.file_tree.insert("", "end", iid="file_{0}".format(index), values=(name,))
            self.file_item_names[item] = name
        self.file_count_var.set("{0}件".format(len(names)))
        self._update_file_actions()

    def _search_folders(self) -> None:
        if self.current_root is None:
            self.notify("先にルートフォルダを選択してください。", "warning")
            return
        keyword_text = self.keyword_var.get()
        try:
            save_mode_keyword(self.mode, keyword_text)
        except OSError:
            self.notify("検索ワードを保存できませんでした。", "warning")
        self.mode_keywords[self.mode] = keyword_text
        keywords = [word for word in re.split(r"[,\s]+", keyword_text.strip()) if word]
        self.folder_search_token += 1
        self.folder_search_count_var.set("検索中…")
        self._submit(
            "folder_search", self.folder_search_token,
            find_folders_by_keyword, self.current_root, keywords,
        )

    def _render_folder_search_results(self) -> None:
        query = self.folder_filter_var.get().strip().casefold()
        root = self.current_root
        matches = []
        for path in self.folder_search_results:
            label = str(path.relative_to(root)) if root is not None else str(path)
            if query in label.casefold():
                matches.append((label, path))
        self.folder_search_tree.delete(*self.folder_search_tree.get_children(""))
        self.folder_search_item_paths.clear()
        for index, (label, path) in enumerate(matches):
            item = self.folder_search_tree.insert(
                "", "end", iid="found_{0}".format(index), text=label
            )
            self.folder_search_item_paths[item] = path
        self.folder_search_count_var.set("{0}件".format(len(matches)))

    def _on_folder_search_select(self) -> None:
        selection = self.folder_search_tree.selection()
        if not selection:
            return
        path = self.folder_search_item_paths.get(selection[0])
        if path is not None:
            self._select_folder(path)

    def _update_file_actions(self) -> None:
        selected = [
            item for item in self.file_tree.selection() if item in self.file_item_names
        ]
        if self.current_folder is None:
            reason = "先にデータフォルダを選択してください。"
        elif not selected:
            reason = "一覧からCSVを選択してください。"
        else:
            reason = ""
        set_enabled(self.add_button, not reason, self.add_tooltip, reason)
        single_reason = reason or (
            "読込設定を確認するCSVを1つだけ選択してください。" if len(selected) != 1 else ""
        )
        set_enabled(
            self.import_button, not single_reason, self.import_tooltip, single_reason
        )

    def _selected_file_path(self) -> Optional[Path]:
        if self.current_folder is None:
            return None
        selected = self.file_tree.selection()
        if len(selected) != 1:
            return None
        name = self.file_item_names.get(selected[0])
        return None if name is None else self.current_folder / name

    def _report_profile_errors(self) -> None:
        if not self.profile_store.errors or self.closed:
            return
        self.notify(
            "{0}件の読込プロファイルJSONを無効化しました（他のプロファイルは利用できます）: {1}".format(
                len(self.profile_store.errors), " / ".join(self.profile_store.errors)
            ),
            "warning",
        )

    # =====================================================================
    # Loading curves
    # =====================================================================
    def _add_selected_files(self) -> None:
        if self.current_folder is None:
            return
        selected = self.file_tree.selection()
        paths = [
            self.current_folder / self.file_item_names[item]
            for item in selected
            if item in self.file_item_names
        ]
        if not paths:
            return
        new_paths = [p for p in paths if path_key(p) not in self.state_model.curves]
        if not new_paths:
            self.notify("選択したファイルはすでにグラフへ追加されています。")
            return
        mode = self.mode
        overrides = {
            path_key(path): self.file_profile_overrides[(mode, path_key(path))]
            for path in new_paths
            if (mode, path_key(path)) in self.file_profile_overrides
        }
        self.notify("{0}件のCSVを読み込み中…".format(len(new_paths)))
        self._submit(
            "curves_loaded", mode, _load_curve_batch,
            new_paths, mode, self.profiled_loader, overrides,
        )

    def _reload_selected_curves(self) -> None:
        keys = self.series_table.selected_keys()
        if not keys:
            return
        mode = self.mode
        paths = [self.state_model.curves[key].path for key in keys]
        profiles = {}
        for key in keys:
            one_time = self.file_profile_overrides.get((mode, key))
            previous = self.series_import_profiles[mode].get(key)
            if one_time is not None:
                profiles[key] = one_time
            elif previous is not None:
                profiles[key] = self.profile_store.get(previous.profile_id) or previous
        self.notify("{0}件を再読込中…".format(len(paths)))
        self._submit(
            "curves_reloaded", mode, _load_curve_batch,
            paths, mode, self.profiled_loader, profiles,
        )

    def _remove_selected_curves(self) -> None:
        keys = self.series_table.selected_keys()
        if not keys:
            return
        names = [self.state_model.curves[k].display_name for k in keys if k in self.state_model.curves]
        if not confirm_destructive(
            self,
            "系列の削除",
            "次の{0}系列をグラフから削除します。よろしいですか？\n\n{1}".format(
                len(names), "\n".join(names[:10]) + ("\n…" if len(names) > 10 else "")
            ),
        ):
            return
        mode = self.mode
        self.selected_curve_keys[mode] = [
            key for key in self.selected_curve_keys[mode] if key not in keys
        ]
        if self.dsc_range_selection is not None and self.dsc_range_selection.curve_key in keys:
            self._cancel_dsc_selection(restore=False, quiet=True)
        if self.generic_selection is not None and self.generic_selection.curve_key in keys:
            self._cancel_generic_selection(quiet=True)
        for key in keys:
            self.state_model.remove_curve(key)
            self.series_import_profiles[mode].pop(key, None)
            self.curve_visibility[mode].pop(key, None)
            self.value_readout_values[mode].pop(key, None)
            if mode == TGA:
                self.tga_custom_results.pop(key, None)
                if self.tga_active_key == key:
                    self.tga_active_key = None
            elif mode == DSC:
                self.dsc_sessions.pop(key, None)
                self.dsc_analysis_tokens.pop(key, None)
                if self.dsc_active_key == key:
                    self.dsc_active_key = None
            elif mode == PARTICLE_SIZE:
                self.particle_series_processing.pop(key, None)
                self.particle_processed_curves.pop(key, None)
            elif mode == TEMPERATURE_LOGGER:
                self.temperature_sessions.pop(key, None)
            elif mode == SS_CURVE:
                self.ss_settings.pop(key, None)
                self.ss_processed_curves.pop(key, None)
                self.ss_sessions.pop(key, None)
            elif mode == ADHESION:
                self.adhesion_settings.pop(key, None)
                self.adhesion_processed_curves.pop(key, None)
                self.adhesion_sessions.pop(key, None)
        if mode in PROCESSORS:
            if mode == DSC:
                self.invalidate_dsc_sessions(set(self.states[DSC].curves))
            self._refresh_processing_choices(mode)
            self.reprocess(mode)
        elif mode in (PARTICLE_SIZE, SS_CURVE, ADHESION):
            self.reprocess(mode)
        self._refresh_all()
        self.notify("{0}件をグラフから削除しました。".format(len(keys)), "success")

    # =====================================================================
    # Processing pipelines
    # =====================================================================
    def reprocess(self, mode: str) -> None:
        if mode in PROCESSORS:
            self._reprocess_blank_mode(mode)
        elif mode == PARTICLE_SIZE:
            self._reprocess_particle_size()
        elif mode == SS_CURVE:
            self._reprocess_ss_curves()
        elif mode == ADHESION:
            self._reprocess_adhesion_curves()
        if mode == self.mode:
            self._refresh_all()

    def _reprocess_blank_mode(self, mode: str) -> None:
        state = self.states[mode]
        processor = PROCESSORS[mode]
        output = {}
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
            self.apply_display_auto_range(mode)

    def _reprocess_particle_size(self) -> None:
        state = self.states[PARTICLE_SIZE]
        output = {}
        for curve in state.ordered_curves():
            setting = self.particle_series_processing.setdefault(
                curve.key, ParticleSizeSeriesSettings()
            )
            output[curve.key] = process_particle_size_curve(
                curve, self.particle_common_processing, setting
            )
        self.particle_processed_curves = output
        if state.auto_axes:
            self.apply_display_auto_range(PARTICLE_SIZE)

    def _reprocess_ss_curves(self) -> None:
        output = {}
        for curve in self.states[SS_CURVE].ordered_curves():
            settings = self.ss_settings.setdefault(curve.key, SsSampleSettings())
            session = self.ss_sessions.setdefault(curve.key, SsAnalysisSession())
            try:
                display = convert_ss_curve(curve, settings)
                output[curve.key] = display
                candidate = detect_ss_maximum_candidate(display.display_x, display.display_y)
                if session.yield_range is not None:
                    candidate = apply_ss_yield_range(
                        candidate,
                        display.display_x,
                        display.display_y,
                        session.yield_range.start,
                        session.yield_range.end,
                    )
                session.candidate = candidate
                if session.young_modulus is not None:
                    previous = session.young_modulus
                    session.young_modulus = calculate_young_modulus(
                        display.display_x,
                        display.display_y,
                        previous.range_start_percent,
                        previous.range_end_percent,
                    )
                session.status = (
                    "再計算済み" if session.yield_range or session.young_modulus else "自動候補"
                )
                session.warnings = []
            except AnalysisError as exc:
                session.status = "算出不可"
                session.warnings = [str(exc)]
        self.ss_processed_curves = output
        if self.states[SS_CURVE].auto_axes and output:
            self.apply_display_auto_range(SS_CURVE)

    def _reprocess_adhesion_curves(self) -> None:
        output = {}
        for curve in self.states[ADHESION].ordered_curves():
            settings = self.adhesion_settings.setdefault(curve.key, AdhesionSampleSettings())
            try:
                settings.validate()
                converted = convert_adhesive_force(curve, float(settings.width_mm))
                if isinstance(converted, DerivedCurveData):
                    output[curve.key] = converted
            except AnalysisError as exc:
                session = self.adhesion_sessions.setdefault(
                    curve.key, AdhesionAnalysisSession()
                )
                session.status = "算出不可"
                session.warnings = [str(exc)]
        self.adhesion_processed_curves = output
        if self.states[ADHESION].auto_axes and output:
            self.apply_display_auto_range(ADHESION)

    def invalidate_dsc_sessions(self, keys) -> None:
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

    def apply_display_auto_range(self, mode: str) -> AxisRange:
        """Auto range over the *displayed* curves, not the raw ones."""
        state = self.states[mode]
        curves = self._processed_for(mode)
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

    def _processed_for(self, mode: str):
        if mode in PROCESSORS:
            return tuple(self.processed_curves[mode].values())
        if mode == PARTICLE_SIZE:
            return tuple(self.particle_processed_curves.values())
        if mode == SS_CURVE:
            return tuple(self.ss_processed_curves.values())
        if mode == ADHESION:
            return tuple(self.adhesion_processed_curves.values())
        return ()

    def display_curves(self, mode: Optional[str] = None):
        selected = mode or self.mode
        if selected in PROCESSORS:
            return tuple(
                self.processed_curves[selected].get(curve.key, raw_processed_curve(curve))
                for curve in self.states[selected].ordered_curves()
            )
        if selected == PARTICLE_SIZE:
            return tuple(
                self.particle_processed_curves.get(curve.key, raw_particle_size_curve(curve))
                for curve in self.states[PARTICLE_SIZE].ordered_curves()
            )
        if selected == SS_CURVE:
            return tuple(
                self.ss_processed_curves[curve.key]
                for curve in self.states[SS_CURVE].ordered_curves()
                if curve.key in self.ss_processed_curves
            )
        if selected == ADHESION:
            return tuple(
                self.adhesion_processed_curves[curve.key]
                for curve in self.states[ADHESION].ordered_curves()
                if curve.key in self.adhesion_processed_curves
            )
        return self.states[selected].ordered_curves()

    def display_series(self, mode: Optional[str] = None, include_hidden: bool = False):
        selected = mode or self.mode
        series = tuple(to_display_series(curve) for curve in self.display_curves(selected))
        if include_hidden:
            return series
        visibility = self.curve_visibility[selected]
        return tuple(item for item in series if visibility.get(item.series_key, True))

    def _refresh_processing_choices(self, mode: str) -> None:
        if mode not in BLANK_CAPABLE_MODES:
            return
        mapping = {
            "{0} — {1}".format(curve.display_name, curve.path): curve.key
            for curve in self.states[mode].ordered_curves()
        }
        self.blank_choice_keys[mode] = mapping
        self.panels[mode].refresh_blank_choices(("(なし)",) + tuple(mapping.keys()))

    def blank_choice_for_key(self, mode: str, key: Optional[str]) -> str:
        if key is None:
            return "(なし)"
        for label, candidate in self.blank_choice_keys[mode].items():
            if candidate == key:
                return label
        return "(削除済み) {0}".format(key)

    def blank_key_from_choice(self, mode: str, value: str) -> Optional[str]:
        if not value or value == "(なし)":
            return None
        prefix = "(削除済み) "
        if value.startswith(prefix):
            return value[len(prefix):].strip() or None
        return self.blank_choice_keys[mode].get(value)

    # =====================================================================
    # Series table interaction
    # =====================================================================
    def selected_keys(self) -> List[str]:
        return self.series_table.selected_keys()

    def selected_single_key(self, operation: str) -> Optional[str]:
        keys = self.selected_keys()
        if len(keys) != 1:
            self.notify("{0}の対象系列を［系列］タブで1つだけ選択してください。".format(operation), "warning")
            return None
        return keys[0]

    def selected_single_curve(self, mode: str, operation: str) -> Optional[CurveData]:
        if self.mode != mode:
            return None
        key = self.selected_single_key(operation)
        if key is None:
            return None
        return self.states[mode].curves.get(key)

    def _on_series_selection(self, keys) -> None:
        mode = self.mode
        self.selected_curve_keys[mode] = list(keys)
        if mode == DSC and keys:
            if self.dsc_range_selection is not None and (
                len(keys) != 1 or keys[0] != self.dsc_range_selection.curve_key
            ):
                self._cancel_dsc_selection(quiet=True)
            self.dsc_active_key = keys[0]
        if mode in (TEMPERATURE_LOGGER, SS_CURVE, ADHESION):
            if self.generic_selection is not None and (
                len(keys) != 1 or keys[0] != self.generic_selection.curve_key
            ):
                self._cancel_generic_selection(quiet=True)
        self.panels[mode].on_selection(list(keys))
        self._update_series_actions(keys)
        if self.result_table is not None and len(keys) == 1:
            self.result_table.select_key(keys[0])
        self.refresh_plot()

    def _toggle_curve_visibility(self, key: str) -> None:
        mode = self.mode
        visible = self.curve_visibility[mode].get(key, True)
        self.curve_visibility[mode][key] = not visible
        self._refresh_series_table()
        self.refresh_plot()
        self.notify(
            "{0}を{1}にしました。Excel出力には全系列が含まれます。".format(
                self.state_model.curves[key].display_name, "非表示" if visible else "表示"
            )
        )

    def _show_all_curves(self) -> None:
        self.curve_visibility[self.mode] = {}
        self._refresh_series_table()
        self.refresh_plot()
        self.notify("すべての系列を表示しました。")

    def _pick_series_color(self, key: str) -> None:
        curve = self.state_model.curves.get(key)
        if curve is None:
            return
        ColorPickerDialog(
            self,
            curve.display_name,
            curve.color,
            lambda color: self._apply_curve_colors({key: color}),
        )

    def _apply_curve_colors(self, colors) -> None:
        normalized = {}
        for key, color in colors.items():
            if key in self.state_model.curves:
                normalized[key] = normalize_color(color)
        if not normalized:
            return
        selected = self.selected_keys()
        for key, color in normalized.items():
            self.state_model.set_color(key, color)
        self._refresh_series_table()
        self.series_table.select_keys(selected)
        self.refresh_plot()
        self.notify("{0}系列の色を更新しました。".format(len(normalized)), "success")

    def _set_curve_legend_name(self, key: str, legend_name: str) -> bool:
        if key not in self.state_model.curves:
            return False
        try:
            self.state_model.set_legend_name(key, legend_name)
        except ValueError as exc:
            self.notify(str(exc), "error")
            return False
        self._refresh_series_table()
        self.series_table.select_keys([key])
        self.refresh_plot()
        self.notify(
            "凡例名を「{0}」に変更しました。".format(self.state_model.curves[key].legend_label),
            "success",
        )
        return True

    def _apply_curve_legend_names(self, legend_names) -> None:
        selected = self.selected_keys()
        applied = 0
        for key, value in legend_names.items():
            if key not in self.state_model.curves:
                continue
            try:
                self.state_model.set_legend_name(key, value)
                applied += 1
            except ValueError as exc:
                self.notify(str(exc), "error")
                return
        if not applied:
            return
        self._refresh_series_table()
        self.series_table.select_keys(selected)
        self.refresh_plot()
        self.notify("{0}系列の凡例名を更新しました。".format(applied), "success")

    def _open_color_editor(self) -> None:
        curves = self.state_model.ordered_curves()
        if not curves:
            return
        ColorEditorDialog(self, curves, self._apply_curve_colors, self.selected_keys())

    def _open_legend_editor(self) -> None:
        curves = self.state_model.ordered_curves()
        if not curves:
            return
        rows = [(c.key, c.legend_label, c.display_name) for c in curves]
        LegendEditorDialog(self, rows, self._apply_curve_legend_names)

    def _update_series_actions(self, keys=None) -> None:
        selected = list(keys) if keys is not None else self.selected_keys()
        count = len(self.state_model.curves)
        if len(selected) == 1:
            curve = self.state_model.curves.get(selected[0])
            summary = "選択中：{0}".format(curve.legend_label if curve else "1系列")
        elif selected:
            summary = "{0}系列を選択中".format(len(selected))
        else:
            summary = "選択中：なし（{0}系列を読み込み済み）".format(count)
        self.series_summary_var.set(summary)

        no_selection = "" if selected else "系列を選択してください。"
        set_enabled(self.reload_button, bool(selected), self.reload_tooltip, no_selection)
        set_enabled(self.remove_button, bool(selected), self.remove_tooltip, no_selection)
        no_curves = "" if count else "先にCSVをグラフへ追加してください。"
        set_enabled(self.color_button, bool(count), self.color_tooltip, no_curves)
        set_enabled(self.legend_button, bool(count), self.legend_tooltip, no_curves)
        set_enabled(self.excel_button, bool(count), self.excel_tooltip, no_curves)

    # =====================================================================
    # Refresh
    # =====================================================================
    def _refresh_all(self) -> None:
        mode = self.mode
        readout_x = self.value_readout_x.get(mode)
        if readout_x is None:
            self.value_readout_values[mode].clear()
        else:
            self._update_value_readout_values(mode, readout_x)
        self._refresh_series_table()
        self.panels[mode].refresh()
        self.refresh_results()
        if self.state_model.auto_axes:
            self._sync_axis_entries()
        self.refresh_plot()

    def _refresh_series_table(self) -> None:
        mode = self.mode
        preserved = [
            key for key in self.selected_curve_keys[mode] if key in self.state_model.curves
        ]
        rows = []
        for curve in self.state_model.ordered_curves():
            rows.append(
                {
                    "key": curve.key,
                    "color": curve.color,
                    "legend": curve.legend_label,
                    "visible": self.curve_visibility[mode].get(curve.key, True),
                    "readout": self.value_readout_values[mode].get(curve.key, ""),
                    "blank": self._blank_label(curve.key),
                    "normalization": self._normalization_label(curve.key),
                    "zero": self._zero_label(curve.key),
                    "particle": self._particle_label(curve.key),
                    "source": curve.path.name,
                }
            )
        self.series_table.populate(rows, preserved)
        self.selected_curve_keys[mode] = preserved
        self._update_series_actions(preserved)

    def _blank_label(self, key: str) -> str:
        mode = self.mode
        if mode not in BLANK_CAPABLE_MODES:
            return "—"
        processed = self.processed_curves[mode].get(key)
        if processed is None:
            return "なし"
        name = (processed.blank_name or "").strip()
        if name and name != "Failed":
            return name
        return "不明" if processed.blank_failed else "なし"

    def _normalization_label(self, key: str) -> str:
        mode = self.mode
        if mode not in NORMALIZATION_CAPABLE_MODES:
            return "—"
        processed = self.processed_curves[mode].get(key)
        if processed is None or processed.normalization_wavenumber is None:
            return "なし"
        suffix = "（未適用）" if getattr(processed, "normalization_failed", False) else ""
        unit = NORMALIZATION_UNIT_LABEL.get(mode, "")
        return "{0:g} {1}{2}".format(processed.normalization_wavenumber, unit, suffix)

    def _zero_label(self, key: str) -> str:
        mode = self.mode
        if mode not in ZERO_CAPABLE_MODES:
            return "—"
        processed = self.processed_curves[mode].get(key)
        if processed is None or getattr(processed, "zero_wavenumber", None) is None:
            return "なし"
        suffix = "（未適用）" if getattr(processed, "zero_failed", False) else ""
        unit = NORMALIZATION_UNIT_LABEL.get(mode, "")
        return "{0:g} {1}{2}".format(processed.zero_wavenumber, unit, suffix)

    def _particle_label(self, key: str) -> str:
        if self.mode != PARTICLE_SIZE:
            return "—"
        processed = self.particle_processed_curves.get(key)
        if processed is None or processed.normalization_diameter_um is None:
            return "なし"
        suffix = "（未適用）" if processed.normalization_failed else ""
        return "{0:g} µm{1}".format(processed.normalization_diameter_um, suffix)

    def refresh_plot(self) -> None:
        mode = self.mode
        self.plot.set_placeholder(self._plot_placeholder(mode))
        overlay = self.panels[mode].overlay()
        self.plot.set_plot(
            self.display_series(),
            self.state_model.axis_range,
            self.state_model.measurement_type,
            overlay=overlay,
            hidden_keys=[
                key for key, visible in self.curve_visibility[mode].items() if not visible
            ],
            highlight_key=(
                self.selected_keys()[0] if len(self.selected_keys()) == 1 else None
            ),
            readout_x=self.value_readout_x.get(mode),
        )

    def _plot_placeholder(self, mode: str) -> str:
        """Explain an empty plot instead of leaving the user guessing."""
        if not self.state_model.curves:
            return "左のファイル一覧からCSVを選び［グラフへ追加］を押してください。"
        if mode == SS_CURVE and not self.ss_processed_curves:
            return (
                "SSカーブは幅・厚み・L0 から Strain / Stress を計算して表示します。"
                "［解析］タブの［試料情報を入力］へ。"
            )
        if mode == ADHESION and not self.adhesion_processed_curves:
            return (
                "粘着力は試料幅から N/25 mm へ換算して表示します。"
                "［解析］タブの［試料幅を入力］へ。"
            )
        visibility = self.curve_visibility[mode]
        if visibility and not any(visibility.get(key, True) for key in self.state_model.curves):
            return "すべての系列が非表示です。［系列］タブで表示に戻してください。"
        return "左のファイル一覧からCSVを選び［グラフへ追加］を押してください。"

    # =====================================================================
    # Results tab
    # =====================================================================
    def _rebuild_result_table(self, mode: str) -> None:
        if self._result_mode == mode and self.result_table is not None:
            return
        if self.result_table is not None:
            self.result_table.destroy()
        columns, caption = result_spec(mode)
        self.results_caption_var.set(caption)
        self.result_table = ResultTable(self.results_holder, columns)
        self.result_table.grid(row=0, column=0, sticky="nsew")
        self.result_table.tree.bind("<<TreeviewSelect>>", self._on_result_selected)
        self._result_mode = mode

    def refresh_results(self) -> None:
        if self.result_table is None:
            return
        rows, keys = self.result_rows(self.mode)
        self.result_table.populate(rows, keys)
        if self.mode == DSC and self.dsc_active_key is not None:
            self.result_table.select_key(self.dsc_active_key)

    def result_rows(self, mode: str):
        from .results import result_rows

        return result_rows(self, mode)

    def _on_result_selected(self, _event=None) -> None:
        key = self.result_table.key_for_selection()
        if key is None or key not in self.state_model.curves:
            return
        if self.selected_keys() == [key]:
            return
        self.series_table.select_keys([key])
        self._on_series_selection([key])

    def _copy_results(self, all_rows: bool) -> None:
        if self.result_table is None:
            return
        rows = self.result_table.all_rows() if all_rows else self.result_table.selected_rows()
        try:
            text = rows_to_tsv(self.result_table.headers, rows, True)
        except ValueError as exc:
            self.notify(str(exc), "warning")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.notify("{0}行をタブ区切りでコピーしました。".format(len(rows)), "success")

    # =====================================================================
    # Axis and readout
    # =====================================================================
    def _apply_manual_range(self, quiet: bool = False) -> None:
        if self._syncing_axis:
            return
        try:
            axis = AxisRange(
                float(self.x_min_var.get()),
                float(self.x_max_var.get()),
                float(self.y_min_var.get()),
                float(self.y_max_var.get()),
            )
            self.state_model.set_manual_range(axis)
        except ValueError as exc:
            message = "軸範囲は数値で入力してください。" if "float" in str(exc) else str(exc)
            self.axis_hint_var.set(message)
            return
        self.axis_hint_var.set("")
        self.refresh_plot()
        if not quiet:
            self.notify("表示範囲を適用しました。")

    def _apply_auto_range(self) -> None:
        if self.mode in PROCESSED_MODES:
            self.apply_display_auto_range(self.mode)
        else:
            self.state_model.apply_auto_range()
        self.axis_hint_var.set("")
        self._sync_axis_entries()
        self.refresh_plot()
        self.notify("表示範囲を自動設定しました。")

    def _sync_axis_entries(self) -> None:
        axis = self.state_model.axis_range
        self._syncing_axis = True
        try:
            self.x_min_var.set("{0:g}".format(axis.x_min))
            self.x_max_var.set("{0:g}".format(axis.x_max))
            self.y_min_var.set("{0:g}".format(axis.y_min))
            self.y_max_var.set("{0:g}".format(axis.y_max))
        finally:
            self._syncing_axis = False

    def _calculate_value_readout(self) -> None:
        text = self.value_readout_x_var.get().strip()
        if not text:
            self._clear_value_readout()
            return
        try:
            target = float(text)
            if not math.isfinite(target):
                raise ValueError
        except ValueError:
            self.notify("読取Xへ有限の数値を入力してください。", "error")
            return
        mode = self.mode
        self.value_readout_x[mode] = target
        self._update_value_readout_values(mode, target)
        self._refresh_series_table()
        self.refresh_plot()
        self.notify("読取X {0:g} のY値を系列表に表示しました。".format(target))

    def _update_value_readout_values(self, mode: str, target: float) -> None:
        values = {}
        warnings = []
        for series in self.display_series(mode):
            try:
                result = interpolate_display_value(
                    series.x_values, series.y_values, target, series.logarithmic_x
                )
                values[series.series_key] = _format_readout(result.y_value)
            except ValueReadoutError as exc:
                values[series.series_key] = "—"
                warnings.append("{0}: {1}".format(series.display_name, exc))
        self.value_readout_values[mode] = values
        if warnings:
            self.notify(" / ".join(warnings), "warning")

    def _clear_value_readout(self) -> None:
        mode = self.mode
        self.value_readout_x[mode] = None
        self.value_readout_values[mode].clear()
        self.value_readout_x_var.set("")
        self._refresh_series_table()
        self.refresh_plot()

    # =====================================================================
    # Plot interaction
    # =====================================================================
    def _on_plot_click(self, x_value: float, _y_value: float) -> None:
        mode = self.mode
        if self.zero_selection_key is not None:
            self.panels[mode].commit_clicked_zero(x_value)
            return
        if self.normalization_selection_key is not None:
            self.panels[mode].commit_clicked_normalization(x_value)
            return
        if mode == PARTICLE_SIZE and self.particle_normalization_selection_key is not None:
            self.panels[PARTICLE_SIZE].commit_clicked_normalization(x_value)
            return
        if self.generic_selection is not None:
            try:
                self.generic_selection.add_x(float("{0:.8g}".format(x_value)))
            except AnalysisError as exc:
                self.notify(str(exc), "warning")
                return
            self.panels[mode].sync_selection_entries()
            self.set_instruction(self.generic_selection.next_instruction)
            self.refresh_plot()
            if self.generic_selection.complete:
                self.panels[mode].on_selection_complete()
            return
        if self.dsc_range_selection is not None:
            try:
                self.dsc_range_selection.add_temperature(round(x_value, 2))
            except DscAnalysisError as exc:
                self.notify(str(exc), "warning")
                return
            self.panels[DSC].sync_selection_points()
            self.set_instruction(self.dsc_range_selection.next_instruction)
            self.refresh_plot()
            return
        self.value_readout_x_var.set("{0:.8g}".format(x_value))
        self._calculate_value_readout()

    def _on_plot_alt_click(self) -> None:
        if self.zero_selection_key is not None or self.normalization_selection_key is not None:
            self.panels[self.mode].cancel_click_selection()
            return
        if self.particle_normalization_selection_key is not None:
            self.panels[PARTICLE_SIZE].cancel_click_selection()
            return
        if self.generic_selection is not None:
            if self.generic_selection.points:
                self.generic_selection.undo()
                self.panels[self.mode].sync_selection_entries()
                self.set_instruction(self.generic_selection.next_instruction)
                self.refresh_plot()
            else:
                self._cancel_generic_selection()
            return
        if self.dsc_range_selection is not None:
            if self.dsc_range_selection.points:
                self.dsc_range_selection.undo()
                self.panels[DSC].sync_selection_points()
                self.set_instruction(self.dsc_range_selection.next_instruction)
                self.refresh_plot()
            else:
                self._cancel_dsc_selection()

    def _on_escape(self, _event=None):
        if self._cancel_all_selections(quiet=False):
            return "break"
        return None

    def _cancel_all_selections(self, quiet: bool = True) -> bool:
        cancelled = False
        if self.zero_selection_key is not None or self.normalization_selection_key is not None:
            # state_model still refers to the mode being left when this runs
            # from _on_mode_changed (it is swapped afterwards), and to the
            # current mode otherwise, so either way this is the right panel.
            self.panels[self.state_model.measurement_type].cancel_click_selection(quiet=quiet)
            cancelled = True
        if self.particle_normalization_selection_key is not None:
            self.panels[PARTICLE_SIZE].cancel_click_selection(quiet=quiet)
            cancelled = True
        if self.generic_selection is not None:
            self._cancel_generic_selection(quiet=quiet)
            cancelled = True
        if self.dsc_range_selection is not None:
            self._cancel_dsc_selection(quiet=quiet)
            cancelled = True
        return cancelled

    # -- generic point selection ------------------------------------------
    def start_generic_selection(self, curve_key, kind, roles, x_values, title) -> None:
        if self.dsc_range_selection is not None:
            self._cancel_dsc_selection(quiet=True)
        self.zero_selection_key = None
        self.normalization_selection_key = None
        self.particle_normalization_selection_key = None
        self.generic_selection = XPointSelection(
            curve_key, roles, float(min(x_values)), float(max(x_values)), title=title
        )
        self.generic_selection_kind = kind
        self.set_instruction(self.generic_selection.next_instruction)
        self.refresh_plot()

    def _cancel_generic_selection(self, quiet: bool = True) -> None:
        self.generic_selection = None
        self.generic_selection_kind = None
        self.set_instruction("")
        self.refresh_plot()
        if not quiet:
            self.notify("範囲選択をキャンセルしました。既存の解析結果は変更していません。")

    def cancel_generic_selection(self, quiet: bool = True) -> None:
        self._cancel_generic_selection(quiet)

    # -- DSC four-point selection -----------------------------------------
    def start_dsc_selection(self, analysis_type: str, key: str, curve: CurveData) -> None:
        if self.dsc_range_selection is not None:
            self._cancel_dsc_selection(quiet=True)
        self.dsc_active_key = key
        self.dsc_range_selection = DscFourPointSelection(
            analysis_type=analysis_type,
            curve_key=key,
            curve_min_c=min(curve.temperatures),
            curve_max_c=max(curve.temperatures),
        )
        self.set_instruction(self.dsc_range_selection.next_instruction)
        self.refresh_plot()

    def _cancel_dsc_selection(self, restore: bool = True, quiet: bool = True) -> None:
        if self.dsc_range_selection is None:
            return
        self.dsc_range_selection = None
        self.panels[DSC].on_selection_cancelled(restore)
        self.set_instruction("")
        self.refresh_plot()
        if not quiet:
            self.notify("4点選択をキャンセルしました。既存の解析結果は変更していません。")

    def cancel_dsc_selection(self, restore: bool = True, quiet: bool = True) -> None:
        self._cancel_dsc_selection(restore, quiet)

    # =====================================================================
    # DSC analysis plumbing
    # =====================================================================
    def dsc_analysis_curve(self, key: str) -> CurveData:
        processed = self.processed_curves[DSC].get(key)
        if processed is None:
            processed = raw_processed_curve(self.states[DSC].curves[key])
        if processed.blank_failed:
            reason = " / ".join(processed.warnings) or "ブランク補正に失敗しました。"
            raise DscAnalysisError(
                "ブランク補正が要求されていますが失敗しています。"
                "設定を修正するか補正なしを選択してください。{0}".format(reason)
            )
        return processed.as_curve()

    def start_dsc_auto_analysis(self, curve: CurveData) -> None:
        try:
            analysis_curve = self.dsc_analysis_curve(curve.key)
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
            self.refresh_results()
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
                settings=settings, status="自動解析中"
            )
        else:
            settings = replace(existing.settings)
            existing.status = "自動解析中"
        token = self.dsc_analysis_tokens.get(curve.key, 0) + 1
        self.dsc_analysis_tokens[curve.key] = token
        self.refresh_results()
        self._submit(
            "dsc_auto_analyzed", (curve.key, token),
            _auto_analyze_dsc_curve, replace(analysis_curve), settings,
        )

    def start_dsc_manual_analysis(self, key: str, analysis_type: str, settings) -> None:
        try:
            analysis_curve = self.dsc_analysis_curve(key)
        except DscAnalysisError as exc:
            self.notify(str(exc), "error")
            return
        session = self.dsc_sessions.setdefault(key, DscAnalysisSession(settings=settings))
        session.settings = settings
        session.status = "Tg解析中" if analysis_type == "tg" else "融解解析中"
        token = self.dsc_analysis_tokens.get(key, 0) + 1
        self.dsc_analysis_tokens[key] = token
        self.refresh_results()
        self._submit(
            "dsc_tg_analyzed" if analysis_type == "tg" else "dsc_melt_analyzed",
            (key, token),
            analyze_tg if analysis_type == "tg" else analyze_melting,
            replace(analysis_curve),
            replace(settings),
        )
        self.notify("{0}解析を実行中…".format("Tg" if analysis_type == "tg" else "融解"))

    # =====================================================================
    # Sample information
    # =====================================================================
    def open_sample_info(self, mode: str) -> None:
        curves = self.states[mode].ordered_curves()
        if not curves:
            self.notify("先に系列をグラフへ追加してください。", "warning")
            return
        if mode == SS_CURVE:
            fields = (
                ("width_mm", "試料幅 (mm)"),
                ("thickness_um", "試料厚み (µm)"),
                ("l0_mm", "L0 (mm)"),
            )
            current = {}
            for curve in curves:
                setting = self.ss_settings.get(curve.key, SsSampleSettings())
                current[curve.key] = {
                    "width_mm": setting.width_mm,
                    "thickness_um": setting.thickness_um,
                    "l0_mm": setting.l0_mm,
                }
            title = "試料情報入力"
        elif mode == DSC:
            fields = (("sample_mass_mg", "試料重量 (mg)"),)
            current = {}
            for curve in curves:
                session = self.panels[DSC].ensure_session(curve.key)
                current[curve.key] = {
                    "sample_mass_mg": session.settings.sample_mass_mg if session else None
                }
            title = "試料情報入力（試料重量）"
        else:
            fields = (("width_mm", "試料幅 (mm)"),)
            current = {
                curve.key: {
                    "width_mm": self.adhesion_settings.get(
                        curve.key, AdhesionSampleSettings()
                    ).width_mm
                }
                for curve in curves
            }
            title = "粘着力 試料幅入力"
        SampleInfoDialog(
            self, curves, fields, current,
            lambda values: self._apply_sample_info(mode, values),
            title=title,
        )

    def _apply_sample_info(self, mode: str, values) -> None:
        if mode == SS_CURVE:
            for key, row in values.items():
                setting = self.ss_settings.setdefault(key, SsSampleSettings())
                setting.width_mm = row["width_mm"]
                setting.thickness_um = row["thickness_um"]
                setting.l0_mm = row["l0_mm"]
            self.reprocess(mode)
        elif mode == DSC:
            for key, row in values.items():
                session = self.panels[DSC].ensure_session(key)
                if session is not None:
                    session.settings.sample_mass_mg = row["sample_mass_mg"]
            # ΔH is not recomputed automatically here, matching how changing
            # the heat-flow unit or heating rate also waits for the next
            # 自動候補/算出 press rather than re-analysing on every edit.
            if self.dsc_active_key is not None:
                self.panels[DSC].load_session(self.dsc_active_key)
        else:
            for key, row in values.items():
                self.adhesion_settings.setdefault(
                    key, AdhesionSampleSettings()
                ).width_mm = row["width_mm"]
            self.reprocess(mode)
        self.notify("{0}の試料情報を更新しました。".format(mode), "success")

    # =====================================================================
    # Import settings
    # =====================================================================
    def _open_import_settings(self) -> None:
        path = self._selected_file_path()
        if path is None:
            return
        if self.import_dialog is not None:
            try:
                if self.import_dialog.winfo_exists():
                    self.import_dialog.lift()
                    return
            except tk.TclError:
                pass
        self.import_dialog = ImportSettingsDialog(
            self, path, self.mode, self.profile_store, self._apply_import_profile
        )

    def _apply_import_profile(self, path: Path, profile: ImportProfile) -> None:
        mode = self.mode
        key = path_key(path)
        self.file_profile_overrides[(mode, key)] = profile
        self.profiled_loader.invalidate(path)
        self.notify("読込設定を適用して{0}を読み込みます。".format(path.name))
        self._submit(
            "curves_loaded" if key not in self.state_model.curves else "curves_reloaded",
            mode, _load_curve_batch, [path], mode, self.profiled_loader, {key: profile},
        )

    # =====================================================================
    # Excel export
    # =====================================================================
    def _export_excel(self) -> None:
        series = self.display_series(include_hidden=True)
        if not series:
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        output = filedialog.asksaveasfilename(
            parent=self,
            title="編集可能なExcelグラフを保存",
            defaultextension=".xlsx",
            filetypes=(("Excel workbook", "*.xlsx"),),
            initialfile="{0}_Comparison_{1}.xlsx".format(self.mode, timestamp),
        )
        if not output:
            return
        snapshot = PlotState(
            curves={
                curve.key: replace(curve) for curve in self.state_model.ordered_curves()
            },
            axis_range=self.state_model.axis_range,
            auto_axes=self.state_model.auto_axes,
            measurement_type=self.state_model.measurement_type,
        )
        self.notify("Excelファイルを作成中…")
        self._submit(
            "excel_exported", self.mode, export_excel,
            series, snapshot, Path(output), analysis_table_for_mode(self, self.mode),
        )

    # =====================================================================
    # Background work
    # =====================================================================
    def _submit(self, event_type: str, context, function, *args) -> None:
        future = self.executor.submit(function, *args)

        def completed(done_future) -> None:
            try:
                self.events.put((event_type, context, done_future.result(), None))
            except BaseException as exc:  # handed to the Tk thread below
                self.events.put((event_type, context, None, exc))

        future.add_done_callback(completed)

    submit = _submit

    def _poll_events(self) -> None:
        if self.closed:
            return
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(*event)
        self.after(80, self._poll_events)

    def _handle_event(self, event_type, context, result, error) -> None:
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
                self.notify("フォルダを開けません: {0}".format(error), "error")
                return
            self._replace_tree_children(item, result)
            return

        if event_type == "file_names":
            if context != self.file_scan_token:
                return
            if error is not None:
                self.current_file_names = []
                self._render_file_names()
                self.notify("CSVファイル名の取得に失敗しました: {0}".format(error), "error")
                return
            self.current_file_names = list(result)
            self._render_file_names()
            self.notify("{0}件のCSVを一覧しました（本文は未読込）。".format(len(result)))
            return

        if event_type == "folder_search":
            if context != self.folder_search_token:
                return
            if error is not None:
                self.folder_search_results = []
                self._render_folder_search_results()
                self.notify("フォルダ検索でエラーが発生しました: {0}".format(error), "error")
                return
            self.folder_search_results = result
            self._render_folder_search_results()
            self.notify("{0}件のフォルダが見つかりました。".format(len(result)))
            return

        if event_type in ("curves_loaded", "curves_reloaded"):
            self._handle_curves_loaded(event_type, str(context), result, error)
            return

        if event_type in ("dsc_auto_analyzed", "dsc_tg_analyzed", "dsc_melt_analyzed"):
            self._handle_dsc_event(event_type, context, result, error)
            return

        if event_type == "temperature_analyzed":
            key = str(context)
            session = self.temperature_sessions.setdefault(key, TemperatureAnalysisSession())
            if error is not None:
                session.status = "解析失敗"
                session.warnings = [str(error)]
                self.notify("温度ロガー解析: {0}".format(error), "warning")
            else:
                session.result = result
                session.status = "候補"
                session.warnings = list(result.warnings)
                self._cancel_generic_selection(quiet=True)
                self.notify("温度ロガーのピーク解析が完了しました。", "success")
            self.refresh_results()
            self.refresh_plot()
            return

        if event_type == "temperature_auto_analyzed":
            key = str(context)
            if error is not None:
                # A genuinely broken curve; a flat/noisy one just gets
                # "自動候補なし" from the worker instead of raising.
                self.notify("温度ロガー自動候補: {0}".format(error), "warning")
                return
            self.temperature_sessions[key] = result
            if result.result is not None:
                curve = self.states[TEMPERATURE_LOGGER].curves.get(key)
                name = curve.display_name if curve is not None else key
                self.notify(
                    "{0}: 変化点の自動候補を作成しました。".format(name), "success"
                )
            self.refresh_results()
            self.refresh_plot()
            return

        if event_type in ("ss_candidates", "ss_young"):
            self._handle_ss_event(event_type, str(context), result, error)
            return

        if event_type == "adhesion_average":
            key = str(context)
            session = self.adhesion_sessions.setdefault(key, AdhesionAnalysisSession())
            if error is not None:
                session.status = "解析失敗"
                session.warnings = [str(error)]
                self.notify("平均粘着力: {0}".format(error), "warning")
            else:
                from .analysis_common import XRange

                session.result = result
                session.selected_range = XRange(result.range_start_mm, result.range_end_mm)
                session.status = "算出済み"
                session.warnings = []
                self._cancel_generic_selection(quiet=True)
                self.notify("平均粘着力を算出しました。", "success")
            self.refresh_results()
            self.refresh_plot()
            return

        if event_type == "excel_exported":
            if error is not None:
                report_os_error(self, "Excel出力エラー", str(error))
                self.notify("Excel出力に失敗しました。", "error")
                return
            self.notify("Excelファイルを保存しました: {0}".format(result), "success")

    def _handle_curves_loaded(self, event_type, mode, result, error) -> None:
        if error is not None:
            self.notify("CSV読込エラー: {0}".format(error), "error")
            return
        curves, errors = result
        state = self.states[mode]
        added = 0
        changed = []
        for curve in curves:
            if event_type == "curves_loaded":
                accepted = state.add_curve(curve)
                if accepted:
                    # model.add_curve assigns from the legacy palette; the UI
                    # owns colour choice so the Okabe-Ito palette wins.
                    curve.color = theme.series_color(len(state.curves) - 1)
            else:
                accepted = state.replace_curve(curve)
            added += int(accepted)
            if not accepted:
                continue
            changed.append(curve)
            provenance = curve.import_provenance
            if provenance is not None:
                profile = self.profile_store.get(provenance.profile_id)
                if profile is not None and profile.fingerprint == provenance.profile_fingerprint:
                    self.series_import_profiles[mode][curve.key] = profile
        if mode in PROCESSORS:
            for curve in changed:
                self.series_processing[mode].setdefault(curve.key, SeriesProcessingSettings())
            self._refresh_processing_choices(mode)
            self.reprocess(mode)
        elif mode == PARTICLE_SIZE:
            for curve in changed:
                self.particle_series_processing.setdefault(
                    curve.key, ParticleSizeSeriesSettings()
                )
            self.reprocess(mode)
        elif mode == TEMPERATURE_LOGGER:
            for curve in changed:
                self.temperature_sessions.setdefault(curve.key, TemperatureAnalysisSession())
                self.submit(
                    "temperature_auto_analyzed", curve.key,
                    auto_analyze_temperature_peak, curve,
                )
        elif mode == SS_CURVE:
            for curve in changed:
                self.ss_settings.setdefault(curve.key, SsSampleSettings())
                self.ss_sessions.setdefault(curve.key, SsAnalysisSession())
            self.reprocess(mode)
            if changed and not self.ss_processed_curves:
                self.notify(
                    "SSカーブの表示には幅・厚み・L0 が必要です。［解析］タブで試料情報を入力してください。",
                    "warning",
                )
        elif mode == ADHESION:
            for curve in changed:
                self.adhesion_settings.setdefault(curve.key, AdhesionSampleSettings())
                self.adhesion_sessions.setdefault(curve.key, AdhesionAnalysisSession())
            self.reprocess(mode)
        if mode == self.mode:
            self._refresh_all()
        if mode == DSC:
            for curve in changed:
                self.start_dsc_auto_analysis(curve)
        action = "追加" if event_type == "curves_loaded" else "再読込"
        if errors:
            self.notify(
                "{0}件を{1}、{2}件を読み込めませんでした: {3}".format(
                    added, action, len(errors), " / ".join(errors)
                ),
                "warning",
            )
        else:
            self.notify("{0}: {1}件を{2}しました。".format(mode, added, action), "success")

    def _handle_dsc_event(self, event_type, context, result, error) -> None:
        key, token = context
        if self.dsc_analysis_tokens.get(key) != token:
            return
        session = self.dsc_sessions.get(key)
        if session is None:
            return
        if error is not None:
            session.status = "解析失敗"
            session.warnings.append(str(error))
            self.refresh_results()
            self.refresh_plot()
            self.notify("DSC解析に失敗しました: {0}".format(error), "warning")
            return
        if event_type == "dsc_auto_analyzed":
            self.dsc_sessions[key] = result
            self.notify("DSCの自動解析候補を作成しました。採用または除外を選んでください。", "success")
        elif event_type == "dsc_tg_analyzed":
            session.tg_result = result
            session.status = "Tg再計算済み"
            session.decision = "候補"
            for name in ("tg_onset", "tg_midpoint", "tg_inflection"):
                session.overrides.pop(name, None)
            self.notify("Tg解析を再計算しました。", "success")
        else:
            session.melting_result = result
            session.status = "融解再計算済み"
            session.decision = "候補"
            for name in ("melt_onset", "melt_peak", "melt_end"):
                session.overrides.pop(name, None)
            self.notify("融解解析を再計算しました。", "success")
        if key == self.dsc_active_key:
            self.panels[DSC].load_session(key)
        self.refresh_results()
        self.refresh_plot()

    def _handle_ss_event(self, event_type, key, result, error) -> None:
        session = self.ss_sessions.setdefault(key, SsAnalysisSession())
        if error is not None:
            session.status = "解析失敗"
            session.warnings = [str(error)]
            self.notify("SSカーブ解析: {0}".format(error), "warning")
        elif event_type == "ss_candidates":
            display = self.ss_processed_curves.get(key)
            if session.yield_range is not None and display is not None:
                try:
                    result = apply_ss_yield_range(
                        result,
                        display.display_x,
                        display.display_y,
                        session.yield_range.start,
                        session.yield_range.end,
                    )
                except AnalysisError as exc:
                    session.status = "解析失敗"
                    session.warnings = [str(exc)]
                    self.refresh_results()
                    self.refresh_plot()
                    return
            session.candidate = result
            session.status = "自動候補"
            session.warnings = []
            self.notify("最大応力候補を算出しました。", "success")
        else:
            session.young_modulus = result
            session.status = "ヤング率算出済み"
            session.warnings = []
            self._cancel_generic_selection(quiet=True)
            self.notify("ヤング率を算出しました。", "success")
        self.refresh_results()
        self.refresh_plot()

    def _replace_tree_children(self, item: str, directories) -> None:
        self.folder_tree.delete(*self.folder_tree.get_children(item))
        for directory in directories:
            child = self.folder_tree.insert(item, "end", text=directory.name)
            self.folder_tree_paths[child] = directory
            self.folder_tree.insert(child, "end", text="読み込み中…")
        self.tree_children_loaded.add(item)

    # =====================================================================
    def _on_close(self) -> None:
        self.closed = True
        if self.import_dialog is not None:
            try:
                if self.import_dialog.winfo_exists():
                    self.import_dialog.cancel()
            except tk.TclError:
                pass
            self.import_dialog = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def _format_readout(value: float) -> str:
    absolute = abs(value)
    if absolute != 0 and (absolute < 0.0001 or absolute >= 1000000):
        return "{0:.4e}".format(value)
    return "{0:.6g}".format(value)


def _load_curve_batch(
    paths,
    measurement_type: str = TGA,
    profiled_loader: Optional[ProfiledCurveLoader] = None,
    profiles: Optional[dict] = None,
):
    """Worker-thread CSV batch loader; never touches Tk."""
    curves = []
    errors = []
    loader = {
        TGA: load_tga_csv,
        DSC: load_dsc_csv,
        IR: load_ir_csv,
        UV_VIS: load_uvvis_csv,
        GPC: load_gpc_csv,
        PARTICLE_SIZE: load_particle_size_csv,
        TEMPERATURE_LOGGER: load_temperature_logger_csv,
        SS_CURVE: load_ss_curve_csv,
        ADHESION: load_adhesion_csv,
    }[measurement_type]
    for path in paths:
        try:
            if profiled_loader is None:
                curves.append(loader(path))
            else:
                selected = (profiles or {}).get(path_key(path))
                curves.append(profiled_loader.load(path, measurement_type, selected))
        except (MeasurementDataError, ImportProfileError) as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - reported to the user verbatim
            errors.append("{0}: {1}".format(Path(path).name, exc))
    return curves, errors


def _auto_analyze_dsc_curve(curve: CurveData, settings: DscAnalysisSettings):
    """Worker-thread DSC candidate search."""
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
    theme.enable_dpi_awareness()
    app = GraphMakerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
