"""Per-mode inspector panels.

Each mode owns one panel that builds the `処理` and `解析` tab contents, keeps
its tk variables, and turns its analysis session into a :class:`PlotOverlay`.
Modes without processing or analysis simply declare so and the corresponding
tab disappears rather than showing an empty page.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import math
import tkinter as tk
from tkinter import ttk

from . import theme
from .analysis_common import AnalysisError
from .dsc_analysis import (
    DscAnalysisError,
    DscAnalysisSession,
    DscAnalysisSettings,
    TemperatureRange,
    infer_heating_rate,
)
from .dsc_selection import settings_with_four_points
from .model import (
    ADHESION,
    DSC,
    GPC,
    IR,
    PARTICLE_SIZE,
    SS_CURVE,
    TEMPERATURE_LOGGER,
    TGA,
    UV_VIS,
)
from .new_mode_analysis import (
    AdhesionAnalysisSession,
    SsAnalysisSession,
    SsSampleSettings,
    TemperatureAnalysisSession,
    TemperaturePeakSettings,
    analyze_temperature_peak,
    apply_ss_yield_range,
    auto_analyze_temperature_peak,
    calculate_average_adhesive_force,
    calculate_young_modulus,
    detect_ss_maximum_candidate,
    nearest_data_index,
)
from .particle_size_processing import (
    ParticleSizeSeriesSettings,
    particle_mixed_normalization,
)
from .plot_view import (
    EMPTY_OVERLAY,
    OverlayArea,
    OverlayBand,
    OverlayCurve,
    OverlayLine,
    OverlayMarker,
    PlotOverlay,
)
from .processing import (
    SeriesProcessingSettings,
    USE_COMMON,
    USE_INDIVIDUAL,
    USE_NONE,
    ProcessingError,
    mixed_normalization,
    validate_blank_reference,
)
from .tga_analysis import (
    TgaTdError,
    calculate_custom_td_for_selection,
    calculate_standard_td,
    format_td_temperature,
    selected_tga_curve,
    td_label_from_remaining_percent,
)
from .widgets import (
    ButtonRow,
    FieldGrid,
    Tooltip,
    hint_label,
    numeric_entry,
    section_label,
    separator,
)


BLANK_MODE_LABELS = (
    ("共通ブランクを使用", USE_COMMON),
    ("補正なし", USE_NONE),
    ("個別ブランクを指定", USE_INDIVIDUAL),
)
#: Shared by IR (波数)/UV-Vis (波長)/GPC (保持時間); the wording stays unit-
#: agnostic here and the field label next to it names the actual unit.
NORM_MODE_LABELS = (
    ("共通規格化位置を使用", USE_COMMON),
    ("規格化なし", USE_NONE),
    ("個別の規格化値を指定", USE_INDIVIDUAL),
)
PARTICLE_MODE_LABELS = (
    ("共通指定粒径を使用", USE_COMMON),
    ("規格化なし", USE_NONE),
    ("個別の指定粒径を使用", USE_INDIVIDUAL),
)


def _label_to_mode(labels, text: str, default: str) -> str:
    for label, mode in labels:
        if label == text:
            return mode
    return default


def _mode_to_label(labels, mode: str, default: str) -> str:
    for label, candidate in labels:
        if candidate == mode:
            return label
    return default


def _optional_float(text: str, label: str) -> Optional[float]:
    normalized = text.strip()
    if not normalized:
        return None
    try:
        value = float(normalized)
    except ValueError:
        raise ProcessingError("{0}は数値で入力してください。".format(label))
    if not math.isfinite(value):
        raise ProcessingError("{0}は有限値で入力してください。".format(label))
    return value


# ---------------------------------------------------------------------------
class ModePanel(object):
    """Base panel.  Subclasses opt into the processing and analysis tabs."""

    mode = ""
    has_processing = False
    has_analysis = False

    def __init__(self, app, processing_parent, analysis_parent) -> None:
        self.app = app
        self.processing = None  # type: Optional[ttk.Frame]
        self.analysis = None  # type: Optional[ttk.Frame]
        if self.has_processing:
            self.processing = ttk.Frame(processing_parent, style="Panel.TFrame")
            self.build_processing(self.processing)
        if self.has_analysis:
            self.analysis = ttk.Frame(analysis_parent, style="Panel.TFrame")
            self.build_analysis(self.analysis)

    # -- construction hooks -----------------------------------------------
    def build_processing(self, parent: ttk.Frame) -> None:
        pass

    def build_analysis(self, parent: ttk.Frame) -> None:
        pass

    # -- visibility --------------------------------------------------------
    def show(self) -> None:
        for frame in (self.processing, self.analysis):
            if frame is not None:
                frame.pack(fill="both", expand=True,
                           padx=theme.SPACE_M, pady=theme.SPACE_M)

    def hide(self) -> None:
        for frame in (self.processing, self.analysis):
            if frame is not None:
                frame.pack_forget()

    # -- app callbacks (no-ops unless a mode needs them) -------------------
    def refresh(self) -> None:
        pass

    def on_selection(self, keys: List[str]) -> None:
        pass

    def overlay(self) -> PlotOverlay:
        return EMPTY_OVERLAY

    def refresh_blank_choices(self, values) -> None:
        pass

    def sync_selection_entries(self) -> None:
        pass

    def on_selection_complete(self) -> None:
        pass

    def sync_selection_points(self) -> None:
        pass

    def on_selection_cancelled(self, restore: bool) -> None:
        pass

    def load_session(self, key: str) -> None:
        pass

    def cancel_click_selection(self, quiet: bool = True) -> None:
        pass

    # -- helpers -----------------------------------------------------------
    def _single_key(self, operation: str) -> Optional[str]:
        return self.app.selected_single_key(operation)


# ---------------------------------------------------------------------------
class TgaPanel(ModePanel):
    mode = TGA
    has_analysis = True

    def build_analysis(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        self.selected_var = tk.StringVar(value="解析対象のTGA系列を1つ選択してください")
        section_label(parent, "Td解析").grid(row=0, column=0, sticky="w")
        hint_label(parent, self.selected_var).grid(
            row=1, column=0, sticky="w", pady=(2, theme.SPACE_M)
        )

        standard = ttk.Frame(parent, style="Panel.TFrame")
        standard.grid(row=2, column=0, sticky="ew")
        self.td_vars = {name: tk.StringVar(value="—") for name in ("Td5", "Td50", "Td95")}
        for index, name in enumerate(("Td5", "Td50", "Td95")):
            standard.columnconfigure(index, weight=1, uniform="td")
            ttk.Label(standard, text="{0} (℃)".format(name), style="PanelSecond.TLabel").grid(
                row=0, column=index, sticky="w"
            )
            ttk.Label(
                standard, textvariable=self.td_vars[name], style="PanelHead.TLabel"
            ).grid(row=1, column=index, sticky="w")

        separator(parent).grid(row=3, column=0, sticky="ew", pady=theme.SPACE_L)

        section_label(parent, "任意残存率から算出").grid(row=4, column=0, sticky="w")
        custom = ttk.Frame(parent, style="Panel.TFrame")
        custom.grid(row=5, column=0, sticky="ew", pady=(theme.SPACE_S, 0))
        ttk.Label(custom, text="残存率 (%)", style="PanelSecond.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.remaining_var = tk.StringVar(value="90")
        entry = numeric_entry(custom, self.remaining_var, width=8)
        entry.grid(row=0, column=1, padx=(theme.SPACE_S, theme.SPACE_S))
        entry.bind("<Return>", lambda _e: self.calculate_custom())
        ttk.Button(custom, text="算出", command=self.calculate_custom).grid(row=0, column=2)

        self.custom_label_var = tk.StringVar(value="—")
        self.custom_temperature_var = tk.StringVar(value="—")
        readout = ttk.Frame(parent, style="Panel.TFrame")
        readout.grid(row=6, column=0, sticky="ew", pady=(theme.SPACE_S, 0))
        ttk.Label(readout, text="Tdx", style="PanelSecond.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(readout, textvariable=self.custom_label_var, style="PanelHead.TLabel").grid(
            row=0, column=1, sticky="w", padx=(theme.SPACE_S, theme.SPACE_XL)
        )
        ttk.Label(readout, text="温度 (℃)", style="PanelSecond.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(
            readout, textvariable=self.custom_temperature_var, style="PanelHead.TLabel"
        ).grid(row=0, column=3, sticky="w", padx=(theme.SPACE_S, 0))

        separator(parent).grid(row=7, column=0, sticky="ew", pady=theme.SPACE_L)
        self.status_var = tk.StringVar(value="未選択")
        self.warning_var = tk.StringVar(value="解析対象のTGA系列を1つ選択してください")
        ttk.Label(parent, textvariable=self.status_var, style="Panel.TLabel").grid(
            row=8, column=0, sticky="w"
        )
        ttk.Label(
            parent, textvariable=self.warning_var, style="PanelError.TLabel",
            wraplength=theme.scale_int(320), justify="left",
        ).grid(row=9, column=0, sticky="ew", pady=(theme.SPACE_XS, 0))

    def refresh(self) -> None:
        self.on_selection(self.app.selected_keys())

    def on_selection(self, keys) -> None:
        app = self.app
        previous_active = app.tga_active_key
        try:
            curve = selected_tga_curve(app.state_model, keys)
        except TgaTdError as exc:
            app.tga_active_key = None
            self.selected_var.set("解析対象のTGA系列を1つ選択してください")
            for variable in self.td_vars.values():
                variable.set("—")
            self.status_var.set("未選択")
            self.warning_var.set(str(exc))
            self.custom_label_var.set("—")
            self.custom_temperature_var.set("—")
            return
        summary = calculate_standard_td(curve)
        app.tga_active_key = curve.key
        self.selected_var.set("解析対象: {0}".format(curve.display_name))
        self.td_vars["Td5"].set(format_td_temperature(summary.td5_c))
        self.td_vars["Td50"].set(format_td_temperature(summary.td50_c))
        self.td_vars["Td95"].set(format_td_temperature(summary.td95_c))
        self.status_var.set(summary.status)
        self.warning_var.set(" / ".join(summary.warnings))
        if previous_active != curve.key:
            self.custom_label_var.set("—")
            self.custom_temperature_var.set("—")

    def calculate_custom(self) -> None:
        app = self.app
        keys = app.selected_keys()
        try:
            selected_tga_curve(app.state_model, keys)
            label, temperature = calculate_custom_td_for_selection(
                app.state_model, keys, self.remaining_var.get()
            )
        except TgaTdError as exc:
            try:
                label = td_label_from_remaining_percent(self.remaining_var.get())
            except TgaTdError:
                label = "—"
            self.custom_label_var.set(label)
            self.custom_temperature_var.set("算出不可")
            self.warning_var.set(str(exc))
            if len(keys) == 1:
                app.tga_custom_results[keys[0]] = (label, None, "算出不可", str(exc))
            app.refresh_results()
            app.notify(str(exc), "warning")
            return
        self.custom_label_var.set(label)
        self.custom_temperature_var.set(format_td_temperature(temperature))
        self.warning_var.set("")
        app.tga_custom_results[keys[0]] = (label, temperature, "算出済み", "")
        self.status_var.set("{0}算出済み".format(label))
        app.refresh_results()
        app.notify("{0} = {1} ℃".format(label, format_td_temperature(temperature)), "success")

    def overlay(self) -> PlotOverlay:
        markers = []
        key = self.app.tga_active_key
        if key is not None and key in self.app.state_model.curves:
            curve = self.app.state_model.curves[key]
            summary = calculate_standard_td(curve)
            for value, label in (
                (summary.td5_c, "Td5"),
                (summary.td50_c, "Td50"),
                (summary.td95_c, "Td95"),
            ):
                if value is not None:
                    markers.append(
                        OverlayMarker(value, label, theme.OVERLAY_MARKER, rotated=True)
                    )
            custom = self.app.tga_custom_results.get(key)
            if custom is not None and custom[1] is not None:
                markers.append(
                    OverlayMarker(custom[1], custom[0], theme.OVERLAY_TG_INFLECTION, rotated=True)
                )
        return PlotOverlay(markers=tuple(markers))


# ---------------------------------------------------------------------------
class BlankPanelMixin(object):
    """Shared blank-correction controls for DSC and IR."""

    def _build_blank_controls(self, parent: ttk.Frame, row: int) -> int:
        app = self.app
        section_label(parent, "共通設定").grid(row=row, column=0, sticky="w")
        common = FieldGrid(parent, columns=1)
        common.grid(row=row + 1, column=0, sticky="ew")
        self.common_blank_var = tk.StringVar(value="(なし)")
        self.common_blank_box = common.add(
            "共通ブランク",
            lambda master: ttk.Combobox(
                master, textvariable=self.common_blank_var, state="readonly"
            ),
        )
        extra_row = self._build_common_extras(parent, row + 2)
        buttons = ButtonRow(parent)
        buttons.grid(row=extra_row, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        buttons.add("共通設定を適用", self.apply_common, style="Accent.TButton")

        separator(parent).grid(row=extra_row + 1, column=0, sticky="ew", pady=theme.SPACE_L)
        self.series_title_var = tk.StringVar(value="選択系列の上書き")
        section_label(parent, "選択系列の上書き").grid(row=extra_row + 2, column=0, sticky="w")
        self.selected_var = tk.StringVar(value="系列を1つ選択してください")
        hint_label(parent, self.selected_var).grid(
            row=extra_row + 3, column=0, sticky="w", pady=(2, theme.SPACE_S)
        )
        series = FieldGrid(parent, columns=1)
        series.grid(row=extra_row + 4, column=0, sticky="ew")
        self.blank_mode_var = tk.StringVar(value=BLANK_MODE_LABELS[0][0])
        series.add(
            "ブランク",
            lambda master: ttk.Combobox(
                master,
                textvariable=self.blank_mode_var,
                values=[label for label, _mode in BLANK_MODE_LABELS],
                state="readonly",
            ),
        )
        self.individual_blank_var = tk.StringVar(value="(なし)")
        self.individual_blank_box = series.add(
            "個別ブランク系列",
            lambda master: ttk.Combobox(
                master, textvariable=self.individual_blank_var, state="readonly"
            ),
        )
        self.blank_mode_var.trace_add(
            "write", lambda *_a: self.update_individual_control_state()
        )
        return extra_row + 5

    def update_individual_control_state(self, *_args) -> None:
        """Grey out the fields a mode does not use, instead of failing later."""
        blank_mode = _label_to_mode(
            BLANK_MODE_LABELS, self.blank_mode_var.get(), USE_COMMON
        )
        self.individual_blank_box.configure(
            state="readonly" if blank_mode == USE_INDIVIDUAL else "disabled"
        )

    def _build_common_extras(self, parent: ttk.Frame, row: int) -> int:
        return row

    def refresh_blank_choices(self, values) -> None:
        for box in (self.common_blank_box, self.individual_blank_box):
            box.configure(values=values)
        self.common_blank_var.set(
            self.app.blank_choice_for_key(
                self.mode, self.app.common_processing[self.mode].blank_key
            )
        )

    def load_blank_controls(self, key: str) -> None:
        """Populate the blank-mode fields for the selected series."""
        app = self.app
        setting = app.series_processing[self.mode].setdefault(key, SeriesProcessingSettings())
        self.blank_mode_var.set(
            _mode_to_label(BLANK_MODE_LABELS, setting.blank_mode, BLANK_MODE_LABELS[0][0])
        )
        self.individual_blank_var.set(app.blank_choice_for_key(self.mode, setting.blank_key))


class NormalizationPanelMixin(object):
    """Shared 規格化［＋0点合わせ］ controls for IR / UV-Vis / GPC.

    A concrete panel sets ``mode`` and ``normalization_unit`` (the label
    shown next to a value, e.g. "cm⁻¹", "nm", "min"), then calls
    ``_build_normalization_controls(parent, row, include_zero=...)`` from its
    own ``build_processing``. When the panel also mixes in
    :class:`BlankPanelMixin`, ``apply_series`` picks up the blank fields too;
    GPC has no blank UI, so it is skipped there automatically.
    """

    normalization_unit = ""
    zero_var = None  # type: Optional[tk.StringVar]

    def _build_normalization_controls(self, parent: ttk.Frame, row: int, include_zero: bool = False) -> int:
        series_extra = FieldGrid(parent, columns=1)
        series_extra.grid(row=row, column=0, sticky="ew")
        self.norm_mode_var = tk.StringVar(value=NORM_MODE_LABELS[0][0])
        series_extra.add(
            "規格化",
            lambda master: ttk.Combobox(
                master, textvariable=self.norm_mode_var,
                values=[label for label, _mode in NORM_MODE_LABELS], state="readonly",
            ),
        )
        self.individual_norm_var = tk.StringVar()
        self.individual_norm_entry = series_extra.add(
            "個別規格化値 {0}".format(self.normalization_unit),
            lambda master: numeric_entry(master, self.individual_norm_var, width=12),
        )
        self.norm_mode_var.trace_add(
            "write", lambda *_a: self.update_normalization_control_state()
        )
        if include_zero:
            self.zero_var = tk.StringVar()
            series_extra.add(
                "0点合わせ {0}".format(self.normalization_unit),
                lambda master: numeric_entry(master, self.zero_var, width=12),
            )
        else:
            self.zero_var = None

        row += 1
        picks = ButtonRow(parent)
        picks.grid(row=row, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        picks.add("グラフで規格化位置を選ぶ", self.begin_normalization_selection)
        row += 1
        if include_zero:
            zero_picks = ButtonRow(parent)
            zero_picks.grid(row=row, column=0, sticky="w", pady=(theme.SPACE_S, 0))
            zero_picks.add("グラフで0点を選ぶ", self.begin_zero_selection)
            zero_picks.add("0点を解除", self.clear_zero)
            row += 1

        apply_row = ButtonRow(parent)
        apply_row.grid(row=row, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        apply_row.add("系列設定を適用", self.apply_series, style="Accent.TButton")
        row += 1

        self.status_var = tk.StringVar(value="処理状態: Raw")
        self.warning_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.status_var, style="Panel.TLabel").grid(
            row=row, column=0, sticky="w"
        )
        row += 1
        ttk.Label(
            parent, textvariable=self.warning_var, style="PanelError.TLabel",
            wraplength=theme.scale_int(320), justify="left",
        ).grid(row=row, column=0, sticky="ew", pady=(theme.SPACE_XS, 0))
        row += 1
        self.update_normalization_control_state()
        return row

    def update_normalization_control_state(self, *_args) -> None:
        entry = getattr(self, "individual_norm_entry", None)
        if entry is None:  # a blank-mode trace can fire mid-construction
            return
        normalization_mode = _label_to_mode(
            NORM_MODE_LABELS, self.norm_mode_var.get(), USE_COMMON
        )
        entry.configure(
            state="normal" if normalization_mode == USE_INDIVIDUAL else "disabled"
        )

    # -- actions -------------------------------------------------------
    def apply_series(self) -> None:
        app = self.app
        mode = self.mode
        key = self._single_key("処理設定")
        if key is None:
            return
        setting = app.series_processing[mode].setdefault(key, SeriesProcessingSettings())
        has_blank_ui = isinstance(self, BlankPanelMixin)
        blank_mode = setting.blank_mode
        blank_key = setting.blank_key
        if has_blank_ui:
            blank_mode = _label_to_mode(BLANK_MODE_LABELS, self.blank_mode_var.get(), USE_COMMON)
            blank_key = app.blank_key_from_choice(mode, self.individual_blank_var.get())
            error = _validate_blank_choice(app, mode, key, blank_mode, blank_key, setting)
            if error:
                self.warning_var.set(error)
                app.notify(error, "error")
                return
        normalization_mode = _label_to_mode(
            NORM_MODE_LABELS, self.norm_mode_var.get(), USE_COMMON
        )
        try:
            normalization = _optional_float(self.individual_norm_var.get(), "個別規格化値")
            zero = (
                _optional_float(self.zero_var.get(), "0点値")
                if self.zero_var is not None
                else None
            )
        except ProcessingError as exc:
            self.warning_var.set(str(exc))
            app.notify(str(exc), "error")
            return
        if normalization_mode == USE_INDIVIDUAL and normalization is None:
            message = "個別の規格化値を入力してください。"
            self.warning_var.set(message)
            app.notify(message, "error")
            return
        self.warning_var.set("")
        if has_blank_ui:
            setting.blank_mode = blank_mode
            setting.blank_key = blank_key
        setting.normalization_mode = normalization_mode
        setting.normalization_wavenumber = normalization
        setting.zero_wavenumber = zero
        app.normalization_selection_key = None
        app.normalization_preview = (
            normalization if normalization_mode == USE_INDIVIDUAL else None
        )
        app.zero_selection_key = None
        app.zero_preview = zero
        app.reprocess(mode)
        app.notify(
            "{0} の処理設定を適用しました。".format(app.states[mode].curves[key].display_name),
            "success",
        )

    def begin_normalization_selection(self) -> None:
        key = self._single_key("規格化位置の選択")
        if key is None:
            return
        app = self.app
        app.cancel_generic_selection(quiet=True)
        app.zero_selection_key = None
        app.normalization_selection_key = key
        app.set_instruction("グラフ上で規格化に使う位置をクリックしてください。")
        app.refresh_plot()

    def begin_zero_selection(self) -> None:
        key = self._single_key("0点位置の選択")
        if key is None:
            return
        app = self.app
        app.cancel_generic_selection(quiet=True)
        app.normalization_selection_key = None
        app.zero_selection_key = key
        app.set_instruction("グラフ上で0点に合わせる位置をクリックしてください。")
        app.refresh_plot()

    def commit_clicked_normalization(self, x_value: float) -> None:
        app = self.app
        mode = self.mode
        key = app.normalization_selection_key
        if key is None:
            return
        value = round(x_value, 2)
        app.normalization_preview = value
        self.individual_norm_var.set("{0:g}".format(value))
        self.norm_mode_var.set(
            _mode_to_label(NORM_MODE_LABELS, USE_INDIVIDUAL, NORM_MODE_LABELS[2][0])
        )
        setting = app.series_processing[mode].setdefault(key, SeriesProcessingSettings())
        setting.normalization_mode = USE_INDIVIDUAL
        setting.normalization_wavenumber = value
        app.normalization_selection_key = None
        app.set_instruction("")
        app.reprocess(mode)
        app.notify(
            "規格化位置 {0:g} {1} を適用しました。".format(value, self.normalization_unit), "success"
        )

    def commit_clicked_zero(self, x_value: float) -> None:
        app = self.app
        mode = self.mode
        key = app.zero_selection_key
        if key is None:
            return
        value = round(x_value, 2)
        app.zero_preview = value
        if self.zero_var is not None:
            self.zero_var.set("{0:g}".format(value))
        setting = app.series_processing[mode].setdefault(key, SeriesProcessingSettings())
        setting.zero_wavenumber = value
        app.zero_selection_key = None
        app.set_instruction("")
        app.reprocess(mode)
        app.notify("0点 {0:g} {1} を適用しました。".format(value, self.normalization_unit), "success")

    def clear_zero(self) -> None:
        app = self.app
        mode = self.mode
        key = self._single_key("0点合わせの解除")
        if key is None:
            return
        setting = app.series_processing[mode].setdefault(key, SeriesProcessingSettings())
        setting.zero_wavenumber = None
        if self.zero_var is not None:
            self.zero_var.set("")
        app.zero_preview = None
        app.reprocess(mode)
        app.notify("0点合わせを解除しました。", "success")

    def cancel_click_selection(self, quiet: bool = True) -> None:
        app = self.app
        app.zero_selection_key = None
        app.normalization_selection_key = None
        app.set_instruction("")
        app.refresh_plot()
        if not quiet:
            app.notify("グラフからの位置選択をキャンセルしました。")

    def load_normalization_controls(self, key: str) -> None:
        app = self.app
        mode = self.mode
        setting = app.series_processing[mode].setdefault(key, SeriesProcessingSettings())
        processed = app.processed_curves[mode].get(key)
        self.norm_mode_var.set(
            _mode_to_label(NORM_MODE_LABELS, setting.normalization_mode, NORM_MODE_LABELS[0][0])
        )
        self.individual_norm_var.set(
            ""
            if setting.normalization_wavenumber is None
            else "{0:g}".format(setting.normalization_wavenumber)
        )
        if self.zero_var is not None:
            self.zero_var.set(
                "" if setting.zero_wavenumber is None else "{0:g}".format(setting.zero_wavenumber)
            )
        self.status_var.set("処理状態: {0}".format(processed.status if processed else "Raw"))
        warnings = list(processed.warnings) if processed else []
        if mixed_normalization(app.display_curves(mode)):
            warnings.append("規格化済みと未規格化の系列が混在しています。")
        self.warning_var.set(" / ".join(dict.fromkeys(warnings)))

    def normalization_overlay(self) -> PlotOverlay:
        app = self.app
        markers = []
        if app.normalization_preview is not None:
            markers.append(
                OverlayMarker(
                    app.normalization_preview,
                    "Norm {0:g} {1}".format(app.normalization_preview, self.normalization_unit),
                    theme.OVERLAY_MARKER,
                    rotated=True,
                )
            )
        if self.zero_var is not None and app.zero_preview is not None:
            markers.append(
                OverlayMarker(
                    app.zero_preview,
                    "0点 {0:g} {1}".format(app.zero_preview, self.normalization_unit),
                    theme.OVERLAY_READOUT,
                    rotated=True,
                )
            )
        return PlotOverlay(markers=tuple(markers))


class DscPanel(BlankPanelMixin, ModePanel):
    mode = DSC
    has_processing = True
    has_analysis = True

    VISIBILITY_ITEMS = (
        ("Tg範囲", "tg_range", True),
        ("Tg基線", "tg_baselines", True),
        ("平滑化曲線", "tg_smoothed", False),
        ("Tg解析点", "tg_points", True),
        ("融解範囲", "melt_range", True),
        ("融解基線", "melt_baseline", True),
        ("融解点", "melt_points", True),
        ("積分面積", "enthalpy_area", True),
    )
    OVERRIDE_ITEMS = (
        ("Tg On", "tg_onset"),
        ("Tg Mid", "tg_midpoint"),
        ("Tg Inf", "tg_inflection"),
        ("融解 On", "melt_onset"),
        ("Peak", "melt_peak"),
        ("End", "melt_end"),
    )

    def __init__(self, app, processing_parent, analysis_parent) -> None:
        self.range_vars = {}
        self._syncing_ranges = False
        self._range_snapshot = {}
        super().__init__(app, processing_parent, analysis_parent)

    # -- processing --------------------------------------------------------
    def build_processing(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        row = self._build_blank_controls(parent, 0)
        buttons = ButtonRow(parent)
        buttons.grid(row=row, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        buttons.add("系列設定を適用", self.apply_series, style="Accent.TButton")
        self.update_individual_control_state()
        self.processing_status_var = tk.StringVar(value="")
        ttk.Label(
            parent, textvariable=self.processing_status_var, style="PanelSecond.TLabel",
            wraplength=theme.scale_int(320), justify="left",
        ).grid(row=row + 1, column=0, sticky="ew", pady=(theme.SPACE_M, 0))

    def apply_common(self) -> None:
        app = self.app
        common = app.common_processing[DSC]
        new_blank = app.blank_key_from_choice(DSC, self.common_blank_var.get())
        changed = common.blank_key != new_blank
        common.blank_key = new_blank
        if changed:
            app.invalidate_dsc_sessions(set(app.states[DSC].curves))
        app.reprocess(DSC)
        app.notify("DSCの共通処理設定を適用しました。", "success")

    def apply_series(self) -> None:
        app = self.app
        key = self._single_key("処理設定")
        if key is None:
            return
        setting = app.series_processing[DSC].setdefault(key, SeriesProcessingSettings())
        blank_mode = _label_to_mode(BLANK_MODE_LABELS, self.blank_mode_var.get(), USE_COMMON)
        blank_key = app.blank_key_from_choice(DSC, self.individual_blank_var.get())
        error = _validate_blank_choice(app, DSC, key, blank_mode, blank_key, setting)
        if error:
            self.processing_status_var.set(error)
            app.notify(error, "error")
            return
        self.processing_status_var.set("")
        blank_changed = setting.blank_mode != blank_mode or setting.blank_key != blank_key
        setting.blank_mode = blank_mode
        setting.blank_key = blank_key
        if blank_changed:
            app.invalidate_dsc_sessions(set([key]))
        app.reprocess(DSC)
        app.notify(
            "{0} の処理設定を適用しました。".format(app.states[DSC].curves[key].display_name),
            "success",
        )

    # -- analysis ----------------------------------------------------------
    def build_analysis(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        self.selected_curve_var = tk.StringVar(value="曲線を選択してください")
        section_label(parent, "測定条件").grid(row=0, column=0, sticky="w")
        hint_label(parent, self.selected_curve_var).grid(
            row=1, column=0, sticky="w", pady=(2, theme.SPACE_XS)
        )

        self.unit_var = tk.StringVar(value="mW")
        self.rate_var = tk.StringVar()
        self.direction_var = tk.StringVar(value="上向き")
        self.smoothing_var = tk.StringVar(value="7")
        conditions = FieldGrid(parent, columns=2)
        conditions.grid(row=2, column=0, sticky="ew")
        conditions.add(
            "熱流単位",
            lambda master: ttk.Combobox(
                master, textvariable=self.unit_var,
                values=("mW", "W/g", "mW/mg", "不明"), state="readonly", width=8,
            ),
        )
        conditions.add("昇温速度 ℃/min", lambda master: numeric_entry(master, self.rate_var))
        conditions.add(
            "吸熱方向",
            lambda master: ttk.Combobox(
                master, textvariable=self.direction_var,
                values=("上向き", "下向き"), state="readonly", width=8,
            ),
        )
        conditions.add(
            "平滑化 点数",
            lambda master: ttk.Spinbox(
                master, from_=1, to=101, increment=2,
                textvariable=self.smoothing_var, width=6,
            ),
        )

        # Sample mass is entered through the shared spreadsheet-style dialog
        # (SS/粘着力 と同じ方式) so several series can be filled in one pass,
        # instead of a lone inline field for whichever curve is selected.
        mass_row = ttk.Frame(parent, style="Panel.TFrame")
        mass_row.grid(row=3, column=0, sticky="ew", pady=(theme.SPACE_S, 0))
        self.mass_display_var = tk.StringVar(value="試料重量: —")
        ttk.Label(mass_row, textvariable=self.mass_display_var, style="PanelSecond.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            mass_row, text="試料情報入力", command=lambda: self.app.open_sample_info(DSC)
        ).grid(row=0, column=1, padx=(theme.SPACE_M, 0))

        separator(parent).grid(row=4, column=0, sticky="ew", pady=theme.SPACE_L)
        self._build_range_group(parent, 5, "Tg", "tg")
        separator(parent).grid(row=7, column=0, sticky="ew", pady=theme.SPACE_L)
        self._build_range_group(parent, 8, "融解・積分", "melt")

        separator(parent).grid(row=10, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "解析の実行").grid(row=11, column=0, sticky="w")
        actions = ButtonRow(parent)
        actions.grid(row=12, column=0, sticky="w", pady=(theme.SPACE_S, 0))
        actions.add("自動候補", self.suggest)
        actions.add("採用", lambda: self.set_decision("採用"))
        actions.add("除外", lambda: self.set_decision("除外"))
        selection_actions = ButtonRow(parent)
        selection_actions.grid(row=13, column=0, sticky="w", pady=(theme.SPACE_S, 0))
        selection_actions.add("Tg 4点選択", lambda: self.begin_selection("tg"))
        selection_actions.add("融解 4点選択", lambda: self.begin_selection("melt"))
        self.calculate_button = selection_actions.add(
            "算出", self.calculate_selection, style="Accent.TButton"
        )
        undo_actions = ButtonRow(parent)
        undo_actions.grid(row=14, column=0, sticky="w", pady=(theme.SPACE_S, 0))
        self.undo_button = undo_actions.add("1点戻す", self.undo_point)
        self.clear_button = undo_actions.add("選択解除", self.cancel_selection)
        self.selection_hint_var = tk.StringVar(
            value="［Tg 4点選択］または［融解 4点選択］を押し、グラフ上で温度の低い順に4点をクリックします。"
        )
        ttk.Label(
            parent, textvariable=self.selection_hint_var, style="PanelSecond.TLabel",
            wraplength=theme.scale_int(320), justify="left",
        ).grid(row=15, column=0, sticky="ew", pady=(theme.SPACE_S, 0))

        separator(parent).grid(row=16, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "解析点の補正 (℃)").grid(row=17, column=0, sticky="w")
        overrides = FieldGrid(parent, columns=3)
        overrides.grid(row=18, column=0, sticky="ew")
        self.override_vars = {}
        for label, key in self.OVERRIDE_ITEMS:
            variable = tk.StringVar()
            self.override_vars[key] = variable
            overrides.add(label, lambda master, v=variable: numeric_entry(master, v, width=7))
        override_actions = ButtonRow(parent)
        override_actions.grid(row=19, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        override_actions.add("解析点を反映", self.apply_overrides)

        separator(parent).grid(row=20, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "グラフ注釈の表示").grid(row=21, column=0, sticky="w")
        visibility = ttk.Frame(parent, style="Panel.TFrame")
        visibility.grid(row=22, column=0, sticky="ew", pady=(theme.SPACE_S, 0))
        self.visibility_vars = {}
        for index, (label, key, default) in enumerate(self.VISIBILITY_ITEMS):
            variable = tk.BooleanVar(value=default)
            self.visibility_vars[key] = variable
            ttk.Checkbutton(
                visibility, text=label, variable=variable,
                style="Panel.TCheckbutton", command=self.app.refresh_plot,
            ).grid(row=index // 2, column=index % 2, sticky="w")

    def _build_range_group(self, parent: ttk.Frame, row: int, title: str, prefix: str) -> None:
        section_label(parent, "{0}範囲 (℃)".format(title)).grid(row=row, column=0, sticky="w")
        grid = FieldGrid(parent, columns=2)
        grid.grid(row=row + 1, column=0, sticky="ew")
        for suffix, label in (
            ("analysis_start", "解析開始"),
            ("analysis_end", "解析終了"),
            ("pre_start", "前基線開始"),
            ("pre_end", "前基線終了"),
            ("post_start", "後基線開始"),
            ("post_end", "後基線終了"),
        ):
            name = "{0}_{1}".format(prefix, suffix)
            variable = tk.StringVar()
            self.range_vars[name] = variable
            variable.trace_add(
                "write", lambda *_a, changed=name: self._on_range_changed(changed)
            )
            grid.add(label, lambda master, v=variable: numeric_entry(master, v, width=8))

    # -- selection ---------------------------------------------------------
    def begin_selection(self, analysis_type: str) -> None:
        app = self.app
        key = self._single_key("DSC 4点選択")
        if key is None:
            return
        try:
            curve = app.dsc_analysis_curve(key)
        except DscAnalysisError as exc:
            app.notify(str(exc), "error")
            return
        if app.dsc_active_key != key:
            app.dsc_active_key = key
            self.load_session(key)
        names = self._selection_names(analysis_type)
        self._range_snapshot = {name: self.range_vars[name].get() for name in names}
        app.start_dsc_selection(analysis_type, key, curve)
        self._syncing_ranges = True
        try:
            for name in names:
                self.range_vars[name].set("")
        finally:
            self._syncing_ranges = False
        self._update_selection_hint()

    def _selection_names(self, analysis_type: str):
        prefix = "tg" if analysis_type == "tg" else "melt"
        return tuple(
            "{0}_{1}".format(prefix, suffix)
            for suffix in (
                "analysis_start", "analysis_end",
                "pre_start", "pre_end", "post_start", "post_end",
            )
        )

    def sync_selection_points(self) -> None:
        selection = self.app.dsc_range_selection
        if selection is None:
            return
        prefix = selection.analysis_type
        self._syncing_ranges = True
        try:
            for name in self._selection_names(prefix):
                self.range_vars[name].set("")
            points = selection.points
            if len(points) >= 1:
                value = "{0:.2f}".format(points[0])
                self.range_vars["{0}_analysis_start".format(prefix)].set(value)
                self.range_vars["{0}_pre_start".format(prefix)].set(value)
            if len(points) >= 2:
                self.range_vars["{0}_pre_end".format(prefix)].set("{0:.2f}".format(points[1]))
            if len(points) >= 3:
                self.range_vars["{0}_post_start".format(prefix)].set("{0:.2f}".format(points[2]))
            if len(points) >= 4:
                value = "{0:.2f}".format(points[3])
                self.range_vars["{0}_analysis_end".format(prefix)].set(value)
                self.range_vars["{0}_post_end".format(prefix)].set(value)
        finally:
            self._syncing_ranges = False
        self._update_selection_hint()

    def _on_range_changed(self, changed: str) -> None:
        if self._syncing_ranges:
            return
        selection = self.app.dsc_range_selection
        if selection is None or not changed.startswith("{0}_".format(selection.analysis_type)):
            return
        prefix = selection.analysis_type
        mirrors = {
            "{0}_analysis_start".format(prefix): "{0}_pre_start".format(prefix),
            "{0}_pre_start".format(prefix): "{0}_analysis_start".format(prefix),
            "{0}_analysis_end".format(prefix): "{0}_post_end".format(prefix),
            "{0}_post_end".format(prefix): "{0}_analysis_end".format(prefix),
        }
        mirror = mirrors.get(changed)
        if mirror is not None:
            self._syncing_ranges = True
            try:
                self.range_vars[mirror].set(self.range_vars[changed].get())
            finally:
                self._syncing_ranges = False
        points = []
        for suffix in ("pre_start", "pre_end", "post_start", "post_end"):
            text = self.range_vars["{0}_{1}".format(prefix, suffix)].get().strip()
            if not text:
                break
            try:
                points.append(float(text))
            except ValueError:
                break
        selection.points = points
        self._update_selection_hint()
        self.app.refresh_plot()

    def _update_selection_hint(self) -> None:
        selection = self.app.dsc_range_selection
        if selection is None:
            self.selection_hint_var.set(
                "［Tg 4点選択］または［融解 4点選択］を押し、グラフ上で温度の低い順に4点をクリックします。"
            )
        else:
            self.selection_hint_var.set(selection.next_instruction)
        self.app.set_instruction(
            "" if selection is None else selection.next_instruction
        )

    def undo_point(self) -> None:
        selection = self.app.dsc_range_selection
        if selection is None:
            return
        selection.undo()
        self.sync_selection_points()
        self.app.refresh_plot()

    def cancel_selection(self) -> None:
        self.app.cancel_dsc_selection(restore=True, quiet=False)

    def on_selection_cancelled(self, restore: bool) -> None:
        if restore and self._range_snapshot:
            self._syncing_ranges = True
            try:
                for name, value in self._range_snapshot.items():
                    self.range_vars[name].set(value)
            finally:
                self._syncing_ranges = False
        self._range_snapshot = {}
        self._update_selection_hint()

    def calculate_selection(self) -> None:
        app = self.app
        selection = app.dsc_range_selection
        if selection is None:
            app.notify("先に4点選択を開始してください。", "warning")
            return
        keys = app.selected_keys()
        if len(keys) != 1 or keys[0] != selection.curve_key:
            app.notify("4点を選択した系列を1つだけ選択してください。", "error")
            return
        try:
            selection.validate()
            settings = settings_with_four_points(
                self.settings_from_controls(), selection.points, selection.analysis_type
            )
        except DscAnalysisError as exc:
            app.notify(str(exc), "error")
            return
        session = app.dsc_sessions.setdefault(
            selection.curve_key, DscAnalysisSession(settings=settings)
        )
        session.settings = settings
        analysis_type = selection.analysis_type
        app.cancel_dsc_selection(restore=False, quiet=True)
        app.start_dsc_manual_analysis(keys[0], analysis_type, settings)

    # -- settings ----------------------------------------------------------
    def _current_sample_mass_mg(self) -> Optional[float]:
        """Sample mass now lives in the 試料情報入力 dialog, not an inline field."""
        session = self.app.dsc_sessions.get(self.app.dsc_active_key)
        return session.settings.sample_mass_mg if session is not None else None

    def settings_from_controls(self) -> DscAnalysisSettings:
        unit_text = self.unit_var.get().strip()
        unit = None if unit_text == "不明" else unit_text
        try:
            smoothing = int(self.smoothing_var.get())
        except ValueError:
            raise DscAnalysisError("平滑化点数は整数で入力してください。")
        if smoothing < 1:
            raise DscAnalysisError("平滑化点数は1以上にしてください。")
        return DscAnalysisSettings(
            heat_flow_unit=unit,
            heating_rate_c_min=_positive_float(self.rate_var.get(), "昇温速度"),
            sample_mass_mg=self._current_sample_mass_mg(),
            endotherm_up=self.direction_var.get() == "上向き",
            smoothing_window=smoothing,
            tg_range=self._range("tg_analysis", "Tg解析範囲"),
            tg_pre_range=self._range("tg_pre", "Tg前ベースライン範囲"),
            tg_post_range=self._range("tg_post", "Tg後ベースライン範囲"),
            melt_range=self._range("melt_analysis", "融解解析・積分範囲"),
            melt_pre_range=self._range("melt_pre", "融解前ベースライン範囲"),
            melt_post_range=self._range("melt_post", "融解後ベースライン範囲"),
        )

    def _range(self, prefix: str, label: str) -> Optional[TemperatureRange]:
        start = self.range_vars["{0}_start".format(prefix)].get().strip()
        end = self.range_vars["{0}_end".format(prefix)].get().strip()
        if not start and not end:
            return None
        if not start or not end:
            raise DscAnalysisError("{0}は開始・終了の両方を入力してください。".format(label))
        try:
            selected = TemperatureRange(float(start), float(end))
        except ValueError:
            raise DscAnalysisError("{0}は数値で入力してください。".format(label))
        selected.validate(label)
        return selected

    def suggest(self) -> None:
        app = self.app
        key = app.dsc_active_key or self._single_key("DSC自動解析")
        if key is None or key not in app.states[DSC].curves:
            app.notify("解析するDSC曲線を選択してください。", "warning")
            return
        try:
            settings = self.settings_from_controls()
        except DscAnalysisError as exc:
            app.notify(str(exc), "error")
            return
        session = app.dsc_sessions.setdefault(key, DscAnalysisSession(settings=settings))
        session.settings = settings
        app.start_dsc_auto_analysis(app.states[DSC].curves[key])
        app.notify("DSCのTg・融解候補を解析中…")

    def set_decision(self, decision: str) -> None:
        app = self.app
        key = app.dsc_active_key
        if key is None or key not in app.dsc_sessions:
            app.notify("先に解析対象のDSC曲線を選択してください。", "warning")
            return
        session = app.dsc_sessions[key]
        if session.tg_result is None and session.melting_result is None:
            app.notify("採用・除外できる解析結果がありません。", "warning")
            return
        session.decision = decision
        app.refresh_results()
        app.notify(
            "{0} を{1}にしました。".format(app.states[DSC].curves[key].display_name, decision),
            "success",
        )

    def apply_overrides(self) -> None:
        app = self.app
        key = app.dsc_active_key
        if key is None or key not in app.dsc_sessions:
            return
        session = app.dsc_sessions[key]
        overrides = {}
        try:
            for name, variable in self.override_vars.items():
                text = variable.get().strip()
                if not text:
                    continue
                value = float(text)
                if name.startswith("tg_") and session.tg_result is not None:
                    selected_range = session.tg_result.analysis_range
                elif name.startswith("melt_") and session.melting_result is not None:
                    selected_range = session.melting_result.analysis_range
                else:
                    raise DscAnalysisError("{0}の解析結果がないため補正できません。".format(name))
                if not selected_range.contains(value):
                    raise DscAnalysisError("{0}の補正値が解析範囲外です。".format(name))
                overrides[name] = value
        except ValueError:
            app.notify("解析点は数値で入力してください。", "error")
            return
        except DscAnalysisError as exc:
            app.notify(str(exc), "error")
            return
        session.overrides = overrides
        session.status = "解析点補正済み"
        app.refresh_results()
        app.refresh_plot()
        app.notify("DSC解析点の補正値を反映しました。", "success")

    # -- lifecycle ---------------------------------------------------------
    def refresh(self) -> None:
        app = self.app
        if app.states[DSC].curves:
            if app.dsc_active_key not in app.states[DSC].curves:
                app.dsc_active_key = next(iter(app.states[DSC].curves))
            self.load_session(app.dsc_active_key)
        else:
            self.selected_curve_var.set("曲線を選択してください")
        self._update_selection_hint()

    def on_selection(self, keys) -> None:
        if len(keys) == 1:
            self.app.dsc_active_key = keys[0]
            self.load_session(keys[0])
            self._load_processing_controls(keys[0])

    def _load_processing_controls(self, key: str) -> None:
        app = self.app
        setting = app.series_processing[DSC].setdefault(key, SeriesProcessingSettings())
        curve = app.states[DSC].curves.get(key)
        if curve is not None:
            self.selected_var.set("処理対象: {0}".format(curve.display_name))
        self.blank_mode_var.set(
            _mode_to_label(BLANK_MODE_LABELS, setting.blank_mode, BLANK_MODE_LABELS[0][0])
        )
        self.individual_blank_var.set(app.blank_choice_for_key(DSC, setting.blank_key))

    def ensure_session(self, key: str) -> Optional[DscAnalysisSession]:
        """Return this curve's session, seeding sensible defaults on first use."""
        app = self.app
        curve = app.states[DSC].curves.get(key)
        if curve is None:
            return None
        session = app.dsc_sessions.get(key)
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
            app.dsc_sessions[key] = session
        return session

    def load_session(self, key: str) -> None:
        session = self.ensure_session(key)
        curve = self.app.states[DSC].curves.get(key)
        if curve is None or session is None:
            return
        settings = session.settings
        self.selected_curve_var.set("解析対象: {0}".format(curve.display_name))
        self.unit_var.set(settings.heat_flow_unit or "不明")
        self.rate_var.set(
            "" if settings.heating_rate_c_min is None else "{0:g}".format(settings.heating_rate_c_min)
        )
        self.mass_display_var.set(
            "試料重量: —"
            if settings.sample_mass_mg is None
            else "試料重量: {0:g} mg".format(settings.sample_mass_mg)
        )
        self.direction_var.set("上向き" if settings.endotherm_up else "下向き")
        self.smoothing_var.set(str(settings.smoothing_window))
        ranges = {
            "tg_analysis": settings.tg_range,
            "tg_pre": settings.tg_pre_range,
            "tg_post": settings.tg_post_range,
            "melt_analysis": settings.melt_range,
            "melt_pre": settings.melt_pre_range,
            "melt_post": settings.melt_post_range,
        }
        self._syncing_ranges = True
        try:
            for prefix, selected in ranges.items():
                self.range_vars["{0}_start".format(prefix)].set(
                    "" if selected is None else "{0:.2f}".format(selected.start)
                )
                self.range_vars["{0}_end".format(prefix)].set(
                    "" if selected is None else "{0:.2f}".format(selected.end)
                )
        finally:
            self._syncing_ranges = False
        values = {
            "tg_onset": session.tg_result.onset_c if session.tg_result else None,
            "tg_midpoint": session.tg_result.midpoint_c if session.tg_result else None,
            "tg_inflection": session.tg_result.inflection_c if session.tg_result else None,
            "melt_onset": session.melting_result.onset_c if session.melting_result else None,
            "melt_peak": session.melting_result.peak_c if session.melting_result else None,
            "melt_end": session.melting_result.end_c if session.melting_result else None,
        }
        for name, value in values.items():
            effective = session.overrides.get(name, value)
            self.override_vars[name].set(
                "" if effective is None else "{0:.2f}".format(effective)
            )
        self._load_processing_controls(key)

    # -- overlay -----------------------------------------------------------
    def overlay(self) -> PlotOverlay:
        app = self.app
        bands = []
        areas = []
        curves = []
        lines = []
        markers = []
        key = app.dsc_active_key
        session = app.dsc_sessions.get(key) if key else None
        visible = lambda name: self.visibility_vars[name].get()

        if session is not None:
            tg = session.tg_result
            melting = session.melting_result
            if tg is not None:
                if visible("tg_range"):
                    bands.append(
                        OverlayBand(tg.analysis_range.start, tg.analysis_range.end, theme.OVERLAY_PRE)
                    )
                if visible("tg_baselines"):
                    lines.append(_fit_line(tg.pre_baseline, tg.pre_range, theme.OVERLAY_BASELINE))
                    lines.append(_fit_line(tg.post_baseline, tg.post_range, theme.OVERLAY_BASELINE))
                if visible("tg_smoothed") and key in app.states[DSC].curves:
                    try:
                        analysis_curve = app.dsc_analysis_curve(key)
                    except DscAnalysisError:
                        analysis_curve = None
                    if analysis_curve is not None and len(analysis_curve.temperatures) == len(
                        tg.smoothed_heat_flow
                    ):
                        curves.append(
                            OverlayCurve(
                                tuple(analysis_curve.temperatures),
                                tuple(tg.smoothed_heat_flow),
                                theme.OVERLAY_SMOOTH,
                            )
                        )
                if visible("tg_points"):
                    for name, value, label, color in (
                        ("tg_onset", tg.onset_c, "Tg On", theme.OVERLAY_TG_ONSET),
                        ("tg_midpoint", tg.midpoint_c, "Tg Mid", theme.OVERLAY_TG_MID),
                        ("tg_inflection", tg.inflection_c, "Tg Inf", theme.OVERLAY_TG_INFLECTION),
                    ):
                        effective = session.overrides.get(name, value)
                        markers.append(
                            OverlayMarker(
                                effective,
                                "{0} {1:.2f}℃".format(label, effective),
                                color,
                                rotated=True,
                            )
                        )
            if melting is not None:
                if visible("melt_range"):
                    bands.append(
                        OverlayBand(
                            melting.analysis_range.start,
                            melting.analysis_range.end,
                            theme.OVERLAY_POST,
                        )
                    )
                if visible("enthalpy_area"):
                    areas.append(
                        OverlayArea(
                            tuple(melting.integration_temperatures),
                            tuple(melting.integration_heat_flow),
                            tuple(melting.integration_baseline),
                            theme.OVERLAY_AREA,
                        )
                    )
                if visible("melt_baseline"):
                    lines.append(
                        _fit_line(melting.baseline, melting.analysis_range, theme.OVERLAY_MELT)
                    )
                if visible("melt_points"):
                    for name, value, label in (
                        ("melt_onset", melting.onset_c, "Melt On"),
                        ("melt_peak", melting.peak_c, "Peak"),
                        ("melt_end", melting.end_c, "End"),
                    ):
                        effective = session.overrides.get(name, value)
                        markers.append(
                            OverlayMarker(
                                effective,
                                "{0} {1:.2f}℃".format(label, effective),
                                theme.OVERLAY_MELT,
                                rotated=True,
                            )
                        )

        selection = app.dsc_range_selection
        if selection is not None and selection.curve_key == key:
            roles = ("前開始", "前終了", "後開始", "後終了")
            points = selection.points
            color = (
                theme.OVERLAY_SELECT
                if selection.analysis_type == "tg"
                else theme.OVERLAY_SELECT_ALT
            )
            for index, (start, end) in enumerate(((0, 1), (1, 2), (2, 3))):
                if len(points) > end:
                    bands.append(
                        OverlayBand(
                            points[start], points[end],
                            theme.SELECTION_BAND_COLORS[index % len(theme.SELECTION_BAND_COLORS)],
                        )
                    )
            for index, value in enumerate(points):
                markers.append(
                    OverlayMarker(
                        value,
                        "{0} {1} {2:.2f}℃".format(index + 1, roles[index], value),
                        color,
                        rotated=True,
                    )
                )
        return PlotOverlay(
            bands=tuple(bands),
            areas=tuple(areas),
            curves=tuple(curves),
            lines=tuple(lines),
            markers=tuple(markers),
        )


def _fit_line(fit, selected_range, color: str) -> OverlayLine:
    return OverlayLine(
        selected_range.start,
        fit.at(selected_range.start),
        selected_range.end,
        fit.at(selected_range.end),
        color,
    )


def _positive_float(text: str, label: str) -> Optional[float]:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = float(stripped)
    except ValueError:
        raise DscAnalysisError("{0}は数値で入力してください。".format(label))
    if value <= 0:
        raise DscAnalysisError("{0}は0より大きい値にしてください。".format(label))
    return value


def _validate_blank_choice(app, mode, key, blank_mode, blank_key, setting) -> str:
    from dataclasses import replace

    if blank_mode == USE_INDIVIDUAL and blank_key is None:
        return "個別ブランクを選択してください。"
    if blank_key == key:
        return "系列自身をブランクとして指定できません。"
    if blank_mode == USE_INDIVIDUAL and blank_key is not None:
        staged = dict(app.series_processing[mode])
        staged[key] = replace(setting, blank_mode=blank_mode, blank_key=blank_key)
        try:
            validate_blank_reference(
                key, blank_key, app.common_processing[mode],
                app.states[mode].curves, staged,
            )
        except ProcessingError as exc:
            return str(exc)
    return ""


# ---------------------------------------------------------------------------
class IrPanel(BlankPanelMixin, NormalizationPanelMixin, ModePanel):
    mode = IR
    has_processing = True
    normalization_unit = "cm⁻¹"

    def build_processing(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        row = self._build_blank_controls(parent, 0)
        row = self._build_normalization_controls(parent, row, include_zero=True)
        separator(parent).grid(row=row, column=0, sticky="ew", pady=theme.SPACE_L)
        ttk.Label(
            parent,
            text="処理は必ず 原データ → ブランク補正 → 0点合わせ → 規格化 の順で適用されます。",
            style="PanelSecond.TLabel",
            wraplength=theme.scale_int(320),
            justify="left",
        ).grid(row=row + 1, column=0, sticky="ew", pady=(theme.SPACE_M, 0))

    def _build_common_extras(self, parent: ttk.Frame, row: int) -> int:
        grid = FieldGrid(parent, columns=1)
        grid.grid(row=row, column=0, sticky="ew")
        self.common_norm_var = tk.StringVar()
        grid.add(
            "共通規格化波数 cm⁻¹",
            lambda master: numeric_entry(master, self.common_norm_var, width=12),
        )
        return row + 1

    def update_individual_control_state(self, *_args) -> None:
        BlankPanelMixin.update_individual_control_state(self)
        self.update_normalization_control_state()

    # -- actions -----------------------------------------------------------
    def apply_common(self) -> None:
        app = self.app
        common = app.common_processing[IR]
        try:
            new_norm = _optional_float(self.common_norm_var.get(), "共通規格化波数")
        except ProcessingError as exc:
            self.warning_var.set(str(exc))
            return
        self.warning_var.set("")
        common.blank_key = app.blank_key_from_choice(IR, self.common_blank_var.get())
        common.normalization_wavenumber = new_norm
        app.reprocess(IR)
        app.notify("IRの共通処理設定を適用しました。", "success")

    # -- lifecycle ---------------------------------------------------------
    def refresh(self) -> None:
        app = self.app
        common = app.common_processing[IR]
        self.common_norm_var.set(
            ""
            if common.normalization_wavenumber is None
            else "{0:g}".format(common.normalization_wavenumber)
        )
        keys = app.selected_keys()
        if len(keys) == 1:
            self.on_selection(keys)
        elif app.states[IR].curves:
            self.selected_var.set("処理対象のIR系列を1つ選択してください")

    def on_selection(self, keys) -> None:
        app = self.app
        if len(keys) != 1:
            self.selected_var.set("処理対象のIR系列を1つ選択してください")
            return
        key = keys[0]
        curve = app.states[IR].curves.get(key)
        if curve is None:
            return
        self.selected_var.set("処理対象: {0}".format(curve.display_name))
        self.load_blank_controls(key)
        self.load_normalization_controls(key)

    def overlay(self) -> PlotOverlay:
        return self.normalization_overlay()


# ---------------------------------------------------------------------------
class ParticleSizePanel(ModePanel):
    mode = PARTICLE_SIZE
    has_processing = True

    def build_processing(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        section_label(parent, "共通設定").grid(row=0, column=0, sticky="w")
        common = FieldGrid(parent, columns=1)
        common.grid(row=1, column=0, sticky="ew")
        self.common_norm_var = tk.StringVar()
        common.add(
            "共通規格化粒径 (µm)",
            lambda master: numeric_entry(master, self.common_norm_var, width=12),
        )
        common_actions = ButtonRow(parent)
        common_actions.grid(row=2, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        common_actions.add("共通設定を適用", self.apply_common, style="Accent.TButton")

        separator(parent).grid(row=3, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "選択系列の規格化").grid(row=4, column=0, sticky="w")
        self.selected_var = tk.StringVar(value="処理対象の系列を1つ選択してください")
        hint_label(parent, self.selected_var).grid(
            row=5, column=0, sticky="w", pady=(2, theme.SPACE_S)
        )
        series = FieldGrid(parent, columns=1)
        series.grid(row=6, column=0, sticky="ew")
        self.norm_mode_var = tk.StringVar(value=PARTICLE_MODE_LABELS[0][0])
        series.add(
            "規格化モード",
            lambda master: ttk.Combobox(
                master, textvariable=self.norm_mode_var,
                values=[label for label, _mode in PARTICLE_MODE_LABELS], state="readonly",
            ),
        )
        self.individual_norm_var = tk.StringVar()
        self.individual_norm_entry = series.add(
            "個別規格化粒径 (µm)",
            lambda master: numeric_entry(master, self.individual_norm_var, width=12),
        )
        self.norm_mode_var.trace_add(
            "write", lambda *_a: self.update_individual_control_state()
        )
        self.update_individual_control_state()
        actions = ButtonRow(parent)
        actions.grid(row=7, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        actions.add("グラフで粒径を選ぶ", self.begin_selection)
        actions.add("系列設定を適用", self.apply_series, style="Accent.TButton")

        separator(parent).grid(row=8, column=0, sticky="ew", pady=theme.SPACE_L)
        self.status_var = tk.StringVar(value="処理状態: Raw")
        self.warning_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.status_var, style="Panel.TLabel").grid(
            row=9, column=0, sticky="w"
        )
        ttk.Label(
            parent, textvariable=self.warning_var, style="PanelError.TLabel",
            wraplength=theme.scale_int(320), justify="left",
        ).grid(row=10, column=0, sticky="ew", pady=(theme.SPACE_XS, 0))

    def update_individual_control_state(self, *_args) -> None:
        """Grey out the per-series diameter unless that mode is selected."""
        mode = _label_to_mode(PARTICLE_MODE_LABELS, self.norm_mode_var.get(), USE_COMMON)
        self.individual_norm_entry.configure(
            state="normal" if mode == USE_INDIVIDUAL else "disabled"
        )

    def apply_common(self) -> None:
        app = self.app
        try:
            value = _optional_float(self.common_norm_var.get(), "共通規格化粒径")
            if value is not None and value <= 0:
                raise ProcessingError("共通規格化粒径は0より大きい値を入力してください。")
        except ProcessingError as exc:
            self.warning_var.set(str(exc))
            app.notify(str(exc), "error")
            return
        self.warning_var.set("")
        app.particle_common_processing.normalization_diameter_um = value
        app.reprocess(PARTICLE_SIZE)
        app.notify("粒度分布の共通設定を適用しました。", "success")

    def apply_series(self) -> None:
        app = self.app
        key = self._single_key("規格化設定")
        if key is None:
            return
        setting = app.particle_series_processing.setdefault(
            key, ParticleSizeSeriesSettings()
        )
        mode = _label_to_mode(PARTICLE_MODE_LABELS, self.norm_mode_var.get(), USE_COMMON)
        try:
            value = _optional_float(self.individual_norm_var.get(), "個別規格化粒径")
            if mode == USE_INDIVIDUAL and (value is None or value <= 0):
                raise ProcessingError("個別規格化粒径は0より大きい値を入力してください。")
        except ProcessingError as exc:
            self.warning_var.set(str(exc))
            app.notify(str(exc), "error")
            return
        self.warning_var.set("")
        setting.normalization_mode = mode
        setting.normalization_diameter_um = value
        app.particle_normalization_selection_key = None
        app.particle_normalization_preview = value if mode == USE_INDIVIDUAL else None
        app.reprocess(PARTICLE_SIZE)
        app.notify("選択系列の規格化設定を適用しました。", "success")

    def begin_selection(self) -> None:
        key = self._single_key("規格化粒径の選択")
        if key is None:
            return
        app = self.app
        app.cancel_generic_selection(quiet=True)
        app.particle_normalization_selection_key = key
        app.set_instruction("グラフ上で規格化に使う粒径をクリックしてください。")
        app.refresh_plot()

    def commit_clicked_normalization(self, diameter: float) -> None:
        app = self.app
        key = app.particle_normalization_selection_key
        if key is None:
            return
        if diameter <= 0:
            app.notify("規格化粒径は0より大きい位置を選んでください。", "warning")
            return
        value = float("{0:.6g}".format(diameter))
        app.particle_normalization_preview = value
        self.individual_norm_var.set("{0:g}".format(value))
        self.norm_mode_var.set(PARTICLE_MODE_LABELS[2][0])
        setting = app.particle_series_processing.setdefault(
            key, ParticleSizeSeriesSettings()
        )
        setting.normalization_mode = USE_INDIVIDUAL
        setting.normalization_diameter_um = value
        app.particle_normalization_selection_key = None
        app.set_instruction("")
        app.reprocess(PARTICLE_SIZE)
        app.notify("規格化粒径 {0:g} µm を適用しました。".format(value), "success")

    def cancel_click_selection(self, quiet: bool = True) -> None:
        app = self.app
        app.particle_normalization_selection_key = None
        app.set_instruction("")
        app.refresh_plot()
        if not quiet:
            app.notify("粒径のクリック選択をキャンセルしました。")

    def refresh(self) -> None:
        app = self.app
        common = app.particle_common_processing
        self.common_norm_var.set(
            ""
            if common.normalization_diameter_um is None
            else "{0:g}".format(common.normalization_diameter_um)
        )
        self.on_selection(app.selected_keys())

    def on_selection(self, keys) -> None:
        app = self.app
        if len(keys) != 1:
            self.selected_var.set("処理対象の系列を1つ選択してください")
            return
        key = keys[0]
        curve = app.states[PARTICLE_SIZE].curves.get(key)
        if curve is None:
            return
        setting = app.particle_series_processing.setdefault(
            key, ParticleSizeSeriesSettings()
        )
        processed = app.particle_processed_curves.get(key)
        self.selected_var.set("処理対象: {0}".format(curve.display_name))
        self.norm_mode_var.set(
            _mode_to_label(
                PARTICLE_MODE_LABELS, setting.normalization_mode, PARTICLE_MODE_LABELS[0][0]
            )
        )
        self.individual_norm_var.set(
            ""
            if setting.normalization_diameter_um is None
            else "{0:g}".format(setting.normalization_diameter_um)
        )
        self.status_var.set(
            "処理状態: {0}".format("規格化" if processed and processed.is_normalized else "Raw")
        )
        warnings = list(processed.warnings) if processed else []
        if particle_mixed_normalization(app.display_curves(PARTICLE_SIZE)):
            warnings.append("規格化済みと未規格化の系列が混在しています。")
        self.warning_var.set(" / ".join(dict.fromkeys(warnings)))

    def overlay(self) -> PlotOverlay:
        preview = self.app.particle_normalization_preview
        if preview is None:
            return EMPTY_OVERLAY
        return PlotOverlay(
            markers=(
                OverlayMarker(
                    preview, "Norm {0:g} µm".format(preview),
                    theme.OVERLAY_MARKER, rotated=True,
                ),
            )
        )


# ---------------------------------------------------------------------------
class TemperatureLoggerPanel(ModePanel):
    mode = TEMPERATURE_LOGGER
    has_analysis = True

    RANGE_NAMES = ("pre_start", "pre_end", "post_start", "post_end")

    def build_analysis(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        section_label(parent, "ピーク解析").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="4点で前基線・ピーク探索・後基線を指定し、前後の全点から1本のベースラインを回帰します。",
            style="PanelSecond.TLabel",
            wraplength=theme.scale_int(320),
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(2, theme.SPACE_M))

        conditions = FieldGrid(parent, columns=2)
        conditions.grid(row=2, column=0, sticky="ew")
        self.smoothing_var = tk.StringVar(value="5")
        self.tangent_var = tk.StringVar(value="5")
        conditions.add("平滑化 点数", lambda master: numeric_entry(master, self.smoothing_var, width=7))
        conditions.add("接線 点数", lambda master: numeric_entry(master, self.tangent_var, width=7))

        separator(parent).grid(row=3, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "4点範囲 (min)").grid(row=4, column=0, sticky="w")
        ranges = FieldGrid(parent, columns=2)
        ranges.grid(row=5, column=0, sticky="ew")
        self.range_vars = {}
        for name, label in (
            ("pre_start", "前基線開始"),
            ("pre_end", "前基線終了"),
            ("post_start", "後基線開始"),
            ("post_end", "後基線終了"),
        ):
            variable = tk.StringVar()
            self.range_vars[name] = variable
            entry = ranges.add(label, lambda master, v=variable: numeric_entry(master, v, width=8))
            entry.bind("<FocusOut>", lambda _e: self._sync_selection_from_entries())

        actions = ButtonRow(parent)
        actions.grid(row=6, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        actions.add(
            "自動候補", self.suggest,
            tooltip="傾きが閾値を超える点から4点を自動推定し、すぐに算出します。",
        )
        actions.add("グラフで4点を選ぶ", self.begin_selection)
        actions.add("算出", self.calculate, style="Accent.TButton")

        separator(parent).grid(row=7, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "グラフ注釈の表示").grid(row=8, column=0, sticky="w")
        visibility = ttk.Frame(parent, style="Panel.TFrame")
        visibility.grid(row=9, column=0, sticky="ew", pady=(theme.SPACE_S, 0))
        self.overlay_vars = {}
        for index, (name, label) in enumerate(
            (("range", "範囲"), ("baseline", "基線"), ("tangent", "接線"),
             ("points", "解析点"), ("area", "積分範囲"))
        ):
            variable = tk.BooleanVar(value=True)
            self.overlay_vars[name] = variable
            ttk.Checkbutton(
                visibility, text=label, variable=variable,
                style="Panel.TCheckbutton", command=self.app.refresh_plot,
            ).grid(row=index // 3, column=index % 3, sticky="w")

    def begin_selection(self) -> None:
        curve = self.app.selected_single_curve(TEMPERATURE_LOGGER, "温度ロガー4点解析")
        if curve is None:
            return
        self.app.start_generic_selection(
            curve.key,
            "temperature_peak",
            ("ピーク前ベースライン開始", "ピーク前ベースライン終了",
             "ピーク後ベースライン開始", "ピーク後ベースライン終了"),
            curve.logger_time_min,
            "温度ロガー4点解析",
        )

    def suggest(self) -> None:
        """Re-run the 傾き閾値法 auto-candidate for the selected curve on demand."""
        app = self.app
        curve = app.selected_single_curve(TEMPERATURE_LOGGER, "自動候補")
        if curve is None:
            return
        app.cancel_generic_selection(quiet=True)
        app.submit(
            "temperature_auto_analyzed", curve.key,
            auto_analyze_temperature_peak, curve,
        )
        app.notify("変化点の自動候補を計算中…")

    def on_selection(self, keys) -> None:
        """Show the selected curve's last 4 points (auto or manual) in the fields."""
        if len(keys) != 1 or self.app.generic_selection is not None:
            return
        session = self.app.temperature_sessions.get(keys[0])
        points = session.settings.points if session else ()
        for index, name in enumerate(self.RANGE_NAMES):
            self.range_vars[name].set(
                "{0:g}".format(points[index]) if index < len(points) else ""
            )

    def sync_selection_entries(self) -> None:
        selection = self.app.generic_selection
        if selection is None or self.app.generic_selection_kind != "temperature_peak":
            return
        points = selection.points
        for index, name in enumerate(self.RANGE_NAMES):
            self.range_vars[name].set(
                "{0:g}".format(points[index]) if index < len(points) else ""
            )

    def _sync_selection_from_entries(self) -> None:
        selection = self.app.generic_selection
        if selection is None or self.app.generic_selection_kind != "temperature_peak":
            return
        try:
            values = [float(self.range_vars[name].get()) for name in self.RANGE_NAMES]
            selection.set_points(values)
        except (ValueError, AnalysisError):
            return
        self.app.set_instruction(selection.next_instruction)
        self.app.refresh_plot()

    def calculate(self) -> None:
        app = self.app
        curve = app.selected_single_curve(TEMPERATURE_LOGGER, "温度ロガー解析")
        if curve is None:
            return
        try:
            points = tuple(float(self.range_vars[name].get()) for name in self.RANGE_NAMES)
            settings = TemperaturePeakSettings(
                points, int(self.smoothing_var.get()), int(self.tangent_var.get())
            )
        except ValueError:
            app.notify("4点範囲・平滑化・接線点数を数値で入力してください。", "error")
            return
        session = app.temperature_sessions.setdefault(
            curve.key, TemperatureAnalysisSession()
        )
        session.settings = settings
        app.submit("temperature_analyzed", curve.key, analyze_temperature_peak, curve, settings)
        app.notify("温度ロガーのピーク解析を実行中…")

    def overlay(self) -> PlotOverlay:
        app = self.app
        keys = app.selected_keys()
        bands = []
        areas = []
        lines = []
        markers = []
        if len(keys) == 1:
            session = app.temperature_sessions.get(keys[0])
            result = session.result if session else None
            if result is not None:
                visible = lambda name: self.overlay_vars[name].get()
                if visible("range"):
                    bands.extend(
                        (
                            OverlayBand(result.pre_baseline_range.start,
                                        result.pre_baseline_range.end, theme.OVERLAY_PRE),
                            OverlayBand(result.peak_search_range.start,
                                        result.peak_search_range.end, theme.OVERLAY_SEARCH),
                            OverlayBand(result.post_baseline_range.start,
                                        result.post_baseline_range.end, theme.OVERLAY_POST),
                        )
                    )
                if visible("area") and result.integration_x:
                    # Shaded curve-vs-baseline polygon, matching how DSC shows
                    # the melting enthalpy integral (rather than a plain
                    # rectangular range indicator).
                    areas.append(
                        OverlayArea(
                            result.integration_x,
                            result.integration_y,
                            tuple(result.baseline.at(x) for x in result.integration_x),
                            theme.OVERLAY_AREA,
                        )
                    )
                if visible("baseline"):
                    lines.append(
                        _fit_line(result.baseline, result.analysis_range, theme.PLOT_FRAME)
                    )
                if visible("tangent"):
                    lines.append(
                        OverlayLine(
                            result.pre_baseline_range.start,
                            result.rising_tangent.at(result.pre_baseline_range.start),
                            result.peak_time_min,
                            result.rising_tangent.at(result.peak_time_min),
                            theme.OVERLAY_BASELINE,
                        )
                    )
                    lines.append(
                        OverlayLine(
                            result.peak_time_min,
                            result.falling_tangent.at(result.peak_time_min),
                            result.post_baseline_range.end,
                            result.falling_tangent.at(result.post_baseline_range.end),
                            theme.OVERLAY_MELT,
                        )
                    )
                if visible("points"):
                    markers.extend(
                        (
                            OverlayMarker(result.start_time_min, "開始", theme.OVERLAY_TG_ONSET, rotated=True),
                            OverlayMarker(result.peak_time_min, "Peak", theme.WARNING, rotated=True),
                            OverlayMarker(result.end_time_min, "終了", theme.OVERLAY_MELT, rotated=True),
                        )
                    )
        return _with_selection(
            app,
            PlotOverlay(
                bands=tuple(bands), areas=tuple(areas),
                lines=tuple(lines), markers=tuple(markers),
            ),
        )


# ---------------------------------------------------------------------------
class SsCurvePanel(ModePanel):
    mode = SS_CURVE
    has_analysis = True

    def build_analysis(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        section_label(parent, "試料情報").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="幅・厚み・L0 から Strain (%) と Stress (MPa) を計算します。厚みはµm入力です。",
            style="PanelSecond.TLabel",
            wraplength=theme.scale_int(320),
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(2, theme.SPACE_S))
        info = ButtonRow(parent)
        info.grid(row=2, column=0, sticky="w")
        info.add("試料情報を入力", lambda: self.app.open_sample_info(SS_CURVE),
                 style="Accent.TButton")

        separator(parent).grid(row=3, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "0点補正").grid(row=4, column=0, sticky="w")
        zero = FieldGrid(parent, columns=2)
        zero.grid(row=5, column=0, sticky="ew")
        self.zero_extension_var = tk.StringVar(value="0")
        self.zero_force_var = tk.StringVar(value="0")
        zero.add("伸び0点 mm", lambda master: numeric_entry(master, self.zero_extension_var, width=8))
        zero.add("荷重0点 N", lambda master: numeric_entry(master, self.zero_force_var, width=8))
        zero_actions = ButtonRow(parent)
        zero_actions.grid(row=6, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        zero_actions.add("先頭点を0に", self.zero_first)
        zero_actions.add("数値0点を適用", self.apply_numeric_zero)
        zero_actions2 = ButtonRow(parent)
        zero_actions2.grid(row=7, column=0, sticky="w", pady=(theme.SPACE_S, 0))
        zero_actions2.add("グラフで0点を選ぶ", lambda: self.begin_point_selection("ss_zero"))
        zero_actions2.add("補正解除", self.clear_zero)

        separator(parent).grid(row=8, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "最大応力・降伏点").grid(row=9, column=0, sticky="w")
        yield_grid = FieldGrid(parent, columns=2)
        yield_grid.grid(row=10, column=0, sticky="ew")
        self.yield_start_var = tk.StringVar()
        self.yield_end_var = tk.StringVar()
        yield_grid.add("探索開始 Strain %", lambda master: numeric_entry(master, self.yield_start_var, width=8))
        yield_grid.add("探索終了 Strain %", lambda master: numeric_entry(master, self.yield_end_var, width=8))
        for variable in (self.yield_start_var, self.yield_end_var):
            variable.trace_add(
                "write", lambda *_a: self._sync_two_point("ss_yield_range",
                                                          self.yield_start_var, self.yield_end_var)
            )
        yield_actions = ButtonRow(parent)
        yield_actions.grid(row=11, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        yield_actions.add("最大応力候補", self.calculate_candidates)
        yield_actions.add("グラフで2点を選ぶ", lambda: self.begin_two_point("ss_yield_range"))
        yield_actions2 = ButtonRow(parent)
        yield_actions2.grid(row=12, column=0, sticky="w", pady=(theme.SPACE_S, 0))
        yield_actions2.add("範囲内の最大応力を算出", self.calculate_yield, style="Accent.TButton")
        yield_actions2.add("最大応力点を手動指定", lambda: self.begin_point_selection("ss_max"))

        separator(parent).grid(row=13, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "ヤング率").grid(row=14, column=0, sticky="w")
        young = FieldGrid(parent, columns=2)
        young.grid(row=15, column=0, sticky="ew")
        self.young_start_var = tk.StringVar()
        self.young_end_var = tk.StringVar()
        young.add("開始 Strain %", lambda master: numeric_entry(master, self.young_start_var, width=8))
        young.add("終了 Strain %", lambda master: numeric_entry(master, self.young_end_var, width=8))
        for variable in (self.young_start_var, self.young_end_var):
            variable.trace_add(
                "write", lambda *_a: self._sync_two_point("ss_young",
                                                          self.young_start_var, self.young_end_var)
            )
        young_actions = ButtonRow(parent)
        young_actions.grid(row=16, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        young_actions.add("グラフで2点を選ぶ", lambda: self.begin_two_point("ss_young"))
        young_actions.add("ヤング率を算出", self.calculate_young, style="Accent.TButton")

    # -- helpers -----------------------------------------------------------
    def _display(self, key):
        return self.app.ss_processed_curves.get(key)

    def _require_display(self, operation: str):
        curve = self.app.selected_single_curve(SS_CURVE, operation)
        if curve is None:
            return None, None
        display = self._display(curve.key)
        if display is None:
            self.app.notify("先に幅・厚み・L0 を入力してください。", "warning")
            return None, None
        return curve, display

    def zero_first(self) -> None:
        curve = self.app.selected_single_curve(SS_CURVE, "0点補正")
        if curve is None:
            return
        setting = self.app.ss_settings.setdefault(curve.key, SsSampleSettings())
        setting.zero_mode, setting.zero_index = "first", 0
        self.app.reprocess(SS_CURVE)
        self.app.notify("先頭点を伸び・荷重の0点にしました。", "success")

    def clear_zero(self) -> None:
        curve = self.app.selected_single_curve(SS_CURVE, "0点補正")
        if curve is None:
            return
        setting = self.app.ss_settings.setdefault(curve.key, SsSampleSettings())
        setting.zero_mode, setting.zero_index = "none", None
        self.app.reprocess(SS_CURVE)
        self.app.notify("0点補正を解除しました。", "success")

    def apply_numeric_zero(self) -> None:
        curve = self.app.selected_single_curve(SS_CURVE, "0点補正")
        if curve is None:
            return
        try:
            extension = float(self.zero_extension_var.get())
            force = float(self.zero_force_var.get())
            if not math.isfinite(extension) or not math.isfinite(force):
                raise ValueError
        except ValueError:
            self.app.notify("伸び0点と荷重0点を有限の数値で入力してください。", "error")
            return
        setting = self.app.ss_settings.setdefault(curve.key, SsSampleSettings())
        setting.zero_mode, setting.zero_index = "numeric", None
        setting.zero_extension_mm, setting.zero_force_n = extension, force
        self.app.reprocess(SS_CURVE)
        self.app.notify("数値0点を適用しました。", "success")

    def begin_point_selection(self, kind: str) -> None:
        curve = self.app.selected_single_curve(SS_CURVE, "SSカーブ点選択")
        if curve is None:
            return
        display = self._display(curve.key)
        x_values = display.display_x if display is not None else curve.extension_mm
        label = {"ss_zero": "0点", "ss_max": "最大応力点"}[kind]
        self.app.start_generic_selection(curve.key, kind, (label,), x_values, label)

    def begin_two_point(self, kind: str) -> None:
        curve, display = self._require_display("範囲選択")
        if curve is None:
            return
        roles = ("範囲開始", "範囲終了")
        title = "降伏探索範囲" if kind == "ss_yield_range" else "ヤング率範囲"
        self.app.start_generic_selection(curve.key, kind, roles, display.display_x, title)

    def sync_selection_entries(self) -> None:
        selection = self.app.generic_selection
        kind = self.app.generic_selection_kind
        if selection is None:
            return
        points = selection.points
        if kind == "ss_young":
            self.young_start_var.set("{0:g}".format(points[0]) if points else "")
            self.young_end_var.set("{0:g}".format(points[1]) if len(points) > 1 else "")
        elif kind == "ss_yield_range":
            self.yield_start_var.set("{0:g}".format(points[0]) if points else "")
            self.yield_end_var.set("{0:g}".format(points[1]) if len(points) > 1 else "")

    def _sync_two_point(self, kind: str, start_var, end_var) -> None:
        selection = self.app.generic_selection
        if selection is None or self.app.generic_selection_kind != kind:
            return
        try:
            selection.set_points((float(start_var.get()), float(end_var.get())))
        except (ValueError, AnalysisError):
            return
        self.app.set_instruction(selection.next_instruction)
        self.app.refresh_plot()

    def on_selection_complete(self) -> None:
        app = self.app
        selection = app.generic_selection
        kind = app.generic_selection_kind
        if selection is None or kind not in ("ss_zero", "ss_max"):
            return
        key, x_value = selection.curve_key, selection.points[0]
        curve = app.states[SS_CURVE].curves[key]
        display = self._display(key)
        if kind == "ss_zero":
            x_values = display.display_x if display is not None else curve.extension_mm
            y_values = display.display_y if display is not None else curve.force_n
            index = nearest_data_index(x_values, y_values, x_value)
            setting = app.ss_settings.setdefault(key, SsSampleSettings())
            setting.zero_mode = "selected"
            setting.zero_index = index
            app.cancel_generic_selection(quiet=True)
            app.reprocess(SS_CURVE)
            app.notify("選択点を0点にしました。", "success")
            return
        if display is None:
            app.notify("先に試料情報を入力してください。", "warning")
            app.cancel_generic_selection(quiet=True)
            return
        from dataclasses import replace

        session = app.ss_sessions.setdefault(key, SsAnalysisSession())
        if session.candidate is None:
            session.candidate = detect_ss_maximum_candidate(display.display_x, display.display_y)
        index = nearest_data_index(display.display_x, display.display_y, x_value)
        session.candidate = replace(
            session.candidate,
            maximum_index=index,
            maximum_stress_mpa=display.display_y[index],
            maximum_strain_percent=display.display_x[index],
            elongation_at_break_percent=display.display_x[index],
        )
        session.status = "手動補正"
        app.cancel_generic_selection(quiet=True)
        app.refresh_results()
        app.refresh_plot()
        app.notify("最大応力点を手動で指定しました。", "success")

    def calculate_candidates(self) -> None:
        curve, display = self._require_display("SSカーブ自動解析")
        if curve is None:
            return
        self.app.submit(
            "ss_candidates", curve.key, detect_ss_maximum_candidate,
            display.display_x, display.display_y,
        )
        self.app.notify("最大応力候補を計算中…")

    def calculate_yield(self) -> None:
        app = self.app
        curve, display = self._require_display("降伏点")
        if curve is None:
            return
        try:
            start = float(self.yield_start_var.get())
            end = float(self.yield_end_var.get())
            session = app.ss_sessions.setdefault(curve.key, SsAnalysisSession())
            base = session.candidate or detect_ss_maximum_candidate(
                display.display_x, display.display_y
            )
            session.candidate = apply_ss_yield_range(
                base, display.display_x, display.display_y, start, end
            )
            from .analysis_common import XRange

            session.yield_range = XRange(start, end)
            session.status = "降伏点算出済み"
            session.warnings = []
        except (ValueError, AnalysisError) as exc:
            app.notify(str(exc) if isinstance(exc, AnalysisError) else "探索範囲を数値で入力してください。", "error")
            return
        app.cancel_generic_selection(quiet=True)
        app.refresh_results()
        app.refresh_plot()
        app.notify("選択範囲の最大応力を算出しました。", "success")

    def calculate_young(self) -> None:
        app = self.app
        curve, display = self._require_display("ヤング率")
        if curve is None:
            return
        try:
            start = float(self.young_start_var.get())
            end = float(self.young_end_var.get())
        except ValueError:
            app.notify("開始と終了のStrainを数値で入力してください。", "error")
            return
        app.submit(
            "ss_young", curve.key, calculate_young_modulus,
            display.display_x, display.display_y, start, end,
        )
        app.notify("ヤング率を計算中…")

    def overlay(self) -> PlotOverlay:
        app = self.app
        keys = app.selected_keys()
        bands = []
        lines = []
        markers = []
        if len(keys) == 1:
            session = app.ss_sessions.get(keys[0])
            if session is not None:
                candidate = session.candidate
                if candidate is not None:
                    if candidate.yield_strain_percent is not None:
                        markers.append(
                            OverlayMarker(
                                candidate.yield_strain_percent, "降伏点",
                                theme.OVERLAY_TG_ONSET, candidate.yield_stress_mpa,
                            )
                        )
                    markers.append(
                        OverlayMarker(
                            candidate.maximum_strain_percent, "最大応力",
                            theme.OVERLAY_MELT, candidate.maximum_stress_mpa,
                        )
                    )
                if session.yield_range is not None:
                    bands.append(
                        OverlayBand(
                            session.yield_range.start, session.yield_range.end, theme.OVERLAY_PRE
                        )
                    )
                young = session.young_modulus
                if young is not None:
                    bands.append(
                        OverlayBand(
                            young.range_start_percent, young.range_end_percent, theme.OVERLAY_SEARCH
                        )
                    )
                    lines.append(
                        OverlayLine(
                            young.range_start_percent,
                            young.regression.at(young.range_start_percent),
                            young.range_end_percent,
                            young.regression.at(young.range_end_percent),
                            theme.OVERLAY_SELECT,
                        )
                    )
        return _with_selection(
            app, PlotOverlay(bands=tuple(bands), lines=tuple(lines), markers=tuple(markers))
        )


# ---------------------------------------------------------------------------
class AdhesionPanel(ModePanel):
    mode = ADHESION
    has_analysis = True

    def build_analysis(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        section_label(parent, "試料幅").grid(row=0, column=0, sticky="w")
        ttk.Label(
            parent,
            text="試料幅 W から F × 25 / W で N/25 mm へ換算します。新規系列は 25 mm です。",
            style="PanelSecond.TLabel",
            wraplength=theme.scale_int(320),
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(2, theme.SPACE_S))
        info = ButtonRow(parent)
        info.grid(row=2, column=0, sticky="w")
        info.add("試料幅を入力", lambda: self.app.open_sample_info(ADHESION),
                 style="Accent.TButton")

        separator(parent).grid(row=3, column=0, sticky="ew", pady=theme.SPACE_L)
        section_label(parent, "距離加重平均").grid(row=4, column=0, sticky="w")
        grid = FieldGrid(parent, columns=2)
        grid.grid(row=5, column=0, sticky="ew")
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        grid.add("平均開始 mm", lambda master: numeric_entry(master, self.start_var, width=8))
        grid.add("平均終了 mm", lambda master: numeric_entry(master, self.end_var, width=8))
        for variable in (self.start_var, self.end_var):
            variable.trace_add("write", lambda *_a: self._sync_two_point())
        actions = ButtonRow(parent)
        actions.grid(row=6, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        actions.add("グラフで2点を選ぶ", self.begin_two_point)
        actions.add("算出", self.calculate, style="Accent.TButton")

    def begin_two_point(self) -> None:
        curve = self.app.selected_single_curve(ADHESION, "範囲選択")
        if curve is None:
            return
        display = self.app.adhesion_processed_curves.get(curve.key)
        if display is None:
            self.app.notify("先に試料幅を入力してください。", "warning")
            return
        self.app.start_generic_selection(
            curve.key, "adhesion_average", ("範囲開始", "範囲終了"),
            display.display_x, "平均粘着力範囲",
        )

    def sync_selection_entries(self) -> None:
        selection = self.app.generic_selection
        if selection is None or self.app.generic_selection_kind != "adhesion_average":
            return
        points = selection.points
        self.start_var.set("{0:g}".format(points[0]) if points else "")
        self.end_var.set("{0:g}".format(points[1]) if len(points) > 1 else "")

    def _sync_two_point(self) -> None:
        selection = self.app.generic_selection
        if selection is None or self.app.generic_selection_kind != "adhesion_average":
            return
        try:
            selection.set_points((float(self.start_var.get()), float(self.end_var.get())))
        except (ValueError, AnalysisError):
            return
        self.app.set_instruction(selection.next_instruction)
        self.app.refresh_plot()

    def calculate(self) -> None:
        app = self.app
        curve = app.selected_single_curve(ADHESION, "平均粘着力")
        if curve is None:
            return
        display = app.adhesion_processed_curves.get(curve.key)
        if display is None:
            app.notify("先に試料幅を入力してください。", "warning")
            return
        try:
            start = float(self.start_var.get())
            end = float(self.end_var.get())
        except ValueError:
            app.notify("平均範囲の開始と終了を数値で入力してください。", "error")
            return
        app.submit(
            "adhesion_average", curve.key, calculate_average_adhesive_force,
            display.display_x, display.display_y, start, end,
        )
        app.notify("平均粘着力を計算中…")

    def overlay(self) -> PlotOverlay:
        app = self.app
        keys = app.selected_keys()
        bands = []
        if len(keys) == 1:
            session = app.adhesion_sessions.get(keys[0])
            if session is not None and session.selected_range is not None:
                bands.append(
                    OverlayBand(
                        session.selected_range.start,
                        session.selected_range.end,
                        theme.OVERLAY_SEARCH,
                    )
                )
        return _with_selection(app, PlotOverlay(bands=tuple(bands)))


def _with_selection(app, overlay: PlotOverlay) -> PlotOverlay:
    """Append the in-progress point selection to a mode's overlay."""
    selection = app.generic_selection
    if selection is None or selection.curve_key not in app.state_model.curves:
        return overlay
    bands = list(overlay.bands)
    markers = list(overlay.markers)
    for index, band in enumerate(selection.bands()):
        bands.append(
            OverlayBand(
                band.start, band.end,
                theme.SELECTION_BAND_COLORS[index % len(theme.SELECTION_BAND_COLORS)],
            )
        )
    for index, value in enumerate(selection.points):
        markers.append(
            OverlayMarker(
                value, "{0} {1:g}".format(index + 1, value),
                theme.OVERLAY_SELECT, rotated=True,
            )
        )
    return PlotOverlay(
        bands=tuple(bands),
        areas=overlay.areas,
        curves=overlay.curves,
        lines=overlay.lines,
        markers=tuple(markers),
        warnings=overlay.warnings,
    )


# ---------------------------------------------------------------------------
class UvVisPanel(BlankPanelMixin, NormalizationPanelMixin, ModePanel):
    """UV-Vis: blank correction + normalization, no zero point (like IR minus 0点)."""

    mode = UV_VIS
    has_processing = True
    normalization_unit = "nm"

    def build_processing(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        row = self._build_blank_controls(parent, 0)
        self._build_normalization_controls(parent, row, include_zero=False)

    def _build_common_extras(self, parent: ttk.Frame, row: int) -> int:
        grid = FieldGrid(parent, columns=1)
        grid.grid(row=row, column=0, sticky="ew")
        self.common_norm_var = tk.StringVar()
        grid.add(
            "共通規格化波長 nm",
            lambda master: numeric_entry(master, self.common_norm_var, width=12),
        )
        return row + 1

    def update_individual_control_state(self, *_args) -> None:
        BlankPanelMixin.update_individual_control_state(self)
        self.update_normalization_control_state()

    def apply_common(self) -> None:
        app = self.app
        common = app.common_processing[UV_VIS]
        try:
            new_norm = _optional_float(self.common_norm_var.get(), "共通規格化波長")
        except ProcessingError as exc:
            self.warning_var.set(str(exc))
            return
        self.warning_var.set("")
        common.blank_key = app.blank_key_from_choice(UV_VIS, self.common_blank_var.get())
        common.normalization_wavenumber = new_norm
        app.reprocess(UV_VIS)
        app.notify("UV-Visの共通処理設定を適用しました。", "success")

    def refresh(self) -> None:
        app = self.app
        common = app.common_processing[UV_VIS]
        self.common_norm_var.set(
            ""
            if common.normalization_wavenumber is None
            else "{0:g}".format(common.normalization_wavenumber)
        )
        keys = app.selected_keys()
        if len(keys) == 1:
            self.on_selection(keys)
        elif app.states[UV_VIS].curves:
            self.selected_var.set("処理対象のUV-Vis系列を1つ選択してください")

    def on_selection(self, keys) -> None:
        app = self.app
        if len(keys) != 1:
            self.selected_var.set("処理対象のUV-Vis系列を1つ選択してください")
            return
        key = keys[0]
        curve = app.states[UV_VIS].curves.get(key)
        if curve is None:
            return
        self.selected_var.set("処理対象: {0}".format(curve.display_name))
        self.load_blank_controls(key)
        self.load_normalization_controls(key)

    def overlay(self) -> PlotOverlay:
        return self.normalization_overlay()


class GpcPanel(NormalizationPanelMixin, ModePanel):
    """GPC: normalization + zero point, no blank correction."""

    mode = GPC
    has_processing = True
    normalization_unit = "min"

    def build_processing(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        section_label(parent, "共通設定").grid(row=0, column=0, sticky="w")
        common = FieldGrid(parent, columns=1)
        common.grid(row=1, column=0, sticky="ew")
        self.common_norm_var = tk.StringVar()
        common.add(
            "共通規格化保持時間 min",
            lambda master: numeric_entry(master, self.common_norm_var, width=12),
        )
        common_actions = ButtonRow(parent)
        common_actions.grid(row=2, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        common_actions.add("共通設定を適用", self.apply_common, style="Accent.TButton")

        separator(parent).grid(row=3, column=0, sticky="ew", pady=theme.SPACE_L)
        self.series_title_var = tk.StringVar(value="選択系列の上書き")
        section_label(parent, "選択系列の上書き").grid(row=4, column=0, sticky="w")
        self.selected_var = tk.StringVar(value="処理対象のGPC系列を1つ選択してください")
        hint_label(parent, self.selected_var).grid(
            row=5, column=0, sticky="w", pady=(2, theme.SPACE_S)
        )
        self._build_normalization_controls(parent, 6, include_zero=True)

    def apply_common(self) -> None:
        app = self.app
        common = app.common_processing[GPC]
        try:
            new_norm = _optional_float(self.common_norm_var.get(), "共通規格化保持時間")
        except ProcessingError as exc:
            self.warning_var.set(str(exc))
            return
        self.warning_var.set("")
        common.normalization_wavenumber = new_norm
        app.reprocess(GPC)
        app.notify("GPCの共通処理設定を適用しました。", "success")

    def refresh(self) -> None:
        app = self.app
        common = app.common_processing[GPC]
        self.common_norm_var.set(
            ""
            if common.normalization_wavenumber is None
            else "{0:g}".format(common.normalization_wavenumber)
        )
        keys = app.selected_keys()
        if len(keys) == 1:
            self.on_selection(keys)
        elif app.states[GPC].curves:
            self.selected_var.set("処理対象のGPC系列を1つ選択してください")

    def on_selection(self, keys) -> None:
        app = self.app
        if len(keys) != 1:
            self.selected_var.set("処理対象のGPC系列を1つ選択してください")
            return
        key = keys[0]
        curve = app.states[GPC].curves.get(key)
        if curve is None:
            return
        self.selected_var.set("処理対象: {0}".format(curve.display_name))
        self.load_normalization_controls(key)

    def overlay(self) -> PlotOverlay:
        return self.normalization_overlay()


# ---------------------------------------------------------------------------
def create_panels(app, processing_parent, analysis_parent):
    """Build one panel per mode; the app shows exactly one at a time."""
    return {
        TGA: TgaPanel(app, processing_parent, analysis_parent),
        DSC: DscPanel(app, processing_parent, analysis_parent),
        IR: IrPanel(app, processing_parent, analysis_parent),
        UV_VIS: UvVisPanel(app, processing_parent, analysis_parent),
        GPC: GpcPanel(app, processing_parent, analysis_parent),
        PARTICLE_SIZE: ParticleSizePanel(app, processing_parent, analysis_parent),
        TEMPERATURE_LOGGER: TemperatureLoggerPanel(app, processing_parent, analysis_parent),
        SS_CURVE: SsCurvePanel(app, processing_parent, analysis_parent),
        ADHESION: AdhesionPanel(app, processing_parent, analysis_parent),
    }
