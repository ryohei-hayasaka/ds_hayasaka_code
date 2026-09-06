"""CSV import-profile editor.

Same profile mechanics as before (``import_profiles`` is untouched); the UI is
rebuilt on :class:`BaseDialog`, and validation errors now appear inline in the
dialog footer instead of stacking modal alerts.
"""

from __future__ import annotations

from typing import Dict, Optional, Union

import json
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from . import theme
from .dialogs import BaseDialog, confirm_destructive
from .import_profiles import (
    AUTO,
    ColumnMapping,
    ImportPreview,
    ImportProfile,
    ImportProfileError,
    ImportTestResult,
    MetadataRule,
    ProfileStore,
    column_letter,
    detect_profile,
    preview_csv,
    test_import,
)
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
from .widgets import ButtonRow, section_label, separator


ENCODING_LABELS = {
    "自動": AUTO,
    "UTF-8 BOM": "utf-8-sig",
    "UTF-8": "utf-8",
    "CP932": "cp932",
}
DELIMITER_LABELS = {
    "自動": AUTO,
    "カンマ": "comma",
    "タブ": "tab",
    "セミコロン": "semicolon",
}
HEADER_LABELS = {
    "自動検索": AUTO,
    "行番号指定": "row",
    "キーワード行": "keyword",
    "ヘッダーなし": "none",
}
START_LABELS = {
    "ヘッダーの次の行": "header_next",
    "ヘッダーから指定行数後": "header_offset",
    "絶対行番号": "absolute",
    "キーワード行から指定行数後": "keyword_offset",
}
END_LABELS = {
    "ファイル末尾まで": "eof",
    "X/Yが空の最初の行まで": "mapped_blank",
    "指定行まで": "absolute",
    "終了キーワードの直前まで": "before_keyword",
    "最初のX/Y非数値行まで": "first_non_numeric",
    "X/Y非数値がN行連続するまで": "non_numeric_run",
}
MAPPING_LABELS = ("ヘッダー名", "列番号", "未使用")
ROLE_TITLES = {
    "x": "X列",
    "y": "Y列",
    "time": "時間",
    "record_id": "Record ID",
    "sample_mass": "試料重量",
    "heating_rate": "昇温速度",
}
DEFAULT_UNITS = {
    TGA: ("°C", "mg"),
    DSC: ("°C", "mW"),
    IR: ("cm-1", "Absorbance"),
    UV_VIS: ("nm", "Absorbance"),
    GPC: ("min", "mV"),
    PARTICLE_SIZE: ("um", "%"),
    TEMPERATURE_LOGGER: ("min", "°C"),
    SS_CURVE: ("mm", "N"),
    ADHESION: ("mm", "N"),
}


def _reverse(mapping, value: str, default: str) -> str:
    for label, stored in mapping.items():
        if stored == value:
            return label
    return default


def _optional_positive_int(value: str, label: str) -> Optional[int]:
    text = value.strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        raise ImportProfileError("{0}は整数で入力してください。".format(label))
    if number < 1:
        raise ImportProfileError("{0}は1以上にしてください。".format(label))
    return number


def _integer(value: str, label: str, default: int = 0) -> int:
    text = value.strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        raise ImportProfileError("{0}は整数で入力してください。".format(label))


class ImportSettingsDialog(BaseDialog):
    """Preview a CSV, edit its import profile, test it, apply or save it."""

    def __init__(self, app, path, measurement_type: str, store: ProfileStore, apply_callback):
        super().__init__(
            app,
            "読込設定",
            "{0}  —  測定モード: {1}".format(Path(path).name, measurement_type),
            min_size=(1120, 720),
        )
        self.app = app
        self.path = Path(path)
        self.measurement_type = measurement_type
        self.store = store
        self.submit = app.submit
        self.apply_callback = apply_callback
        self.current_profile = None  # type: Optional[ImportProfile]
        self.preview = None  # type: Optional[ImportPreview]
        self.profile_by_label = {}
        self.operation_number = 0
        self.closed = False

        self.geometry("{0}x{1}".format(theme.scale_int(1200), theme.scale_int(760)))
        self._build()
        self._refresh_profile_choices()
        self._new_manual_profile()
        self._request_plain_preview()
        self.auto_detect()
        self.present()

    # -- construction ------------------------------------------------------
    def _build(self) -> None:
        self.body.columnconfigure(0, weight=0)
        self.body.columnconfigure(1, weight=1)
        self.body.rowconfigure(0, weight=1)

        settings = ttk.Frame(self.body, style="App.TFrame", width=theme.scale_int(430))
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, theme.SPACE_L))
        settings.columnconfigure(0, weight=1)
        settings.rowconfigure(4, weight=1)

        section_label(settings, "プロファイル", panel=False).grid(row=0, column=0, sticky="w")
        self.profile_choice_var = tk.StringVar()
        self.profile_box = ttk.Combobox(
            settings, textvariable=self.profile_choice_var, state="readonly"
        )
        self.profile_box.grid(row=1, column=0, sticky="ew", pady=(theme.SPACE_XS, theme.SPACE_S))
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_selected)

        name_row = ttk.Frame(settings, style="App.TFrame")
        name_row.grid(row=2, column=0, sticky="ew")
        name_row.columnconfigure(1, weight=1)
        ttk.Label(name_row, text="名称", style="Second.TLabel").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.name_var).grid(
            row=0, column=1, sticky="ew", padx=(theme.SPACE_S, 0)
        )
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            settings, text="自動判定で使用する", variable=self.enabled_var
        ).grid(row=3, column=0, sticky="w", pady=(theme.SPACE_XS, theme.SPACE_S))

        notebook = ttk.Notebook(settings)
        notebook.grid(row=4, column=0, sticky="nsew")
        basic = ttk.Frame(notebook, style="Panel.TFrame", padding=theme.SPACE_M)
        columns = ttk.Frame(notebook, style="Panel.TFrame", padding=theme.SPACE_M)
        ending = ttk.Frame(notebook, style="Panel.TFrame", padding=theme.SPACE_M)
        notebook.add(basic, text="基本・開始")
        notebook.add(columns, text="列・単位")
        notebook.add(ending, text="終了・判定")
        self._build_basic_tab(basic)
        self._build_columns_tab(columns)
        self._build_end_tab(ending)

        right = ttk.Frame(self.body, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=3)
        right.rowconfigure(4, weight=1)

        section_label(right, "プレビュー（最大300行）", panel=False).grid(
            row=0, column=0, sticky="w", pady=(0, theme.SPACE_XS)
        )
        table = ttk.Frame(right, style="App.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(table, show="headings")
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_y = ttk.Scrollbar(table, orient="vertical", command=self.preview_tree.yview)
        preview_y.grid(row=0, column=1, sticky="ns")
        preview_x = ttk.Scrollbar(table, orient="horizontal", command=self.preview_tree.xview)
        preview_x.grid(row=1, column=0, sticky="ew")
        self.preview_tree.configure(
            yscrollcommand=preview_y.set, xscrollcommand=preview_x.set
        )

        self.preview_status_var = tk.StringVar(value="プレビューを読み込み中…")
        ttk.Label(
            right, textvariable=self.preview_status_var, style="Second.TLabel",
            wraplength=theme.scale_int(640), justify="left",
        ).grid(row=2, column=0, sticky="ew", pady=theme.SPACE_S)

        section_label(right, "テスト読込結果", panel=False).grid(row=3, column=0, sticky="w")
        result_frame = ttk.Frame(right, style="App.TFrame")
        result_frame.grid(row=4, column=0, sticky="nsew", pady=(theme.SPACE_XS, 0))
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        self.result_text = tk.Text(result_frame, height=8, wrap="word", state="disabled")
        theme.configure_text_widget(self.result_text)
        self.result_text.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(
            result_frame, orient="vertical", command=self.result_text.yview
        )
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=result_scroll.set)

        left_actions = ButtonRow(self.footer_left)
        left_actions.configure(style="App.TFrame")
        left_actions.grid(row=0, column=0, sticky="w")
        left_actions.add("自動検出", self.auto_detect)
        left_actions.add("プレビュー更新", self.refresh_preview)
        left_actions.add("この設定でテスト", self.test_current)
        more = ButtonRow(self.footer_left)
        more.configure(style="App.TFrame")
        more.grid(row=1, column=0, sticky="w", pady=(theme.SPACE_S, 0))
        more.add("プロファイルとして保存", self.save_profile)
        more.add("複製", self.duplicate_profile)
        more.add("削除", self.delete_profile, style="Danger.TButton")
        self.add_footer_buttons("今回だけ適用")

    def _build_basic_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        self.encoding_var = tk.StringVar(value="自動")
        self.delimiter_var = tk.StringVar(value="自動")
        self.header_mode_var = tk.StringVar(value="自動検索")
        self.header_row_var = tk.StringVar()
        self.header_keyword_var = tk.StringVar()
        self.start_mode_var = tk.StringVar(value="ヘッダーの次の行")
        self.start_row_var = tk.StringVar()
        self.start_keyword_var = tk.StringVar()
        self.start_offset_var = tk.StringVar(value="1")
        row = 0
        for label, variable, values in (
            ("文字コード", self.encoding_var, tuple(ENCODING_LABELS)),
            ("区切り文字", self.delimiter_var, tuple(DELIMITER_LABELS)),
            ("ヘッダー検出", self.header_mode_var, tuple(HEADER_LABELS)),
        ):
            row = self._combo_row(parent, row, label, variable, values)
        for label, variable in (
            ("ヘッダー行（1始まり）", self.header_row_var),
            ("ヘッダーキーワード", self.header_keyword_var),
        ):
            row = self._entry_row(parent, row, label, variable)
        separator(parent).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=theme.SPACE_M
        )
        row += 1
        row = self._combo_row(
            parent, row, "データ開始方式", self.start_mode_var, tuple(START_LABELS)
        )
        for label, variable in (
            ("データ開始行（1始まり）", self.start_row_var),
            ("開始キーワード", self.start_keyword_var),
            ("相対行数", self.start_offset_var),
        ):
            row = self._entry_row(parent, row, label, variable)

    def _combo_row(self, parent, row, label, variable, values):
        ttk.Label(parent, text=label, style="PanelSecond.TLabel").grid(
            row=row, column=0, sticky="w", pady=theme.SPACE_XS
        )
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").grid(
            row=row, column=1, sticky="ew", padx=(theme.SPACE_S, 0), pady=theme.SPACE_XS
        )
        return row + 1

    def _entry_row(self, parent, row, label, variable):
        ttk.Label(parent, text=label, style="PanelSecond.TLabel").grid(
            row=row, column=0, sticky="w", pady=theme.SPACE_XS
        )
        ttk.Entry(parent, textvariable=variable, style="Panel.TEntry").grid(
            row=row, column=1, sticky="ew", padx=(theme.SPACE_S, 0), pady=theme.SPACE_XS
        )
        return row + 1

    def _build_columns_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(2, weight=1)
        for column, label in ((0, "役割"), (1, "指定方法"), (2, "列（A=1）")):
            ttk.Label(parent, text=label, style="PanelSecond.TLabel").grid(
                row=0, column=column, sticky="w"
            )
        self.mapping_method_vars = {}
        self.mapping_value_vars = {}
        self.mapping_boxes = {}
        roles = ["x", "y", "time", "record_id"]
        if self.measurement_type == DSC:
            roles.extend(("sample_mass", "heating_rate"))
        for row, role in enumerate(roles, start=1):
            ttk.Label(parent, text=ROLE_TITLES[role], style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", pady=theme.SPACE_XS
            )
            method = tk.StringVar(value="ヘッダー名" if role in ("x", "y") else "未使用")
            value = tk.StringVar()
            self.mapping_method_vars[role] = method
            self.mapping_value_vars[role] = value
            ttk.Combobox(
                parent, textvariable=method, values=MAPPING_LABELS,
                state="readonly", width=10,
            ).grid(row=row, column=1, sticky="ew", padx=theme.SPACE_XS, pady=theme.SPACE_XS)
            box = ttk.Combobox(parent, textvariable=value)
            box.grid(row=row, column=2, sticky="ew", pady=theme.SPACE_XS)
            self.mapping_boxes[role] = box

        unit_row = len(roles) + 2
        separator(parent).grid(
            row=unit_row - 1, column=0, columnspan=3, sticky="ew", pady=theme.SPACE_M
        )
        self.x_unit_var = tk.StringVar(value=DEFAULT_UNITS[self.measurement_type][0])
        self.y_unit_var = tk.StringVar(value=DEFAULT_UNITS[self.measurement_type][1])
        self.time_unit_var = tk.StringVar(value="min")
        self.sample_mass_unit_var = tk.StringVar(value="mg")
        self.heating_rate_unit_var = tk.StringVar(value="°C/min")
        units = [("X単位", self.x_unit_var), ("Y単位", self.y_unit_var),
                 ("時間単位", self.time_unit_var)]
        if self.measurement_type == DSC:
            units.append(("試料重量単位", self.sample_mass_unit_var))
            units.append(("昇温速度単位", self.heating_rate_unit_var))
        for offset, (label, variable) in enumerate(units):
            ttk.Label(parent, text=label, style="PanelSecond.TLabel").grid(
                row=unit_row + offset, column=0, sticky="w", pady=theme.SPACE_XS
            )
            ttk.Entry(parent, textvariable=variable, style="Panel.TEntry").grid(
                row=unit_row + offset, column=1, columnspan=2, sticky="ew", pady=theme.SPACE_XS
            )

    def _build_end_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        self.end_mode_var = tk.StringVar(value="ファイル末尾まで")
        self.end_row_var = tk.StringVar()
        self.end_keyword_var = tk.StringVar()
        self.non_numeric_count_var = tk.StringVar(value="1")
        self.file_patterns_var = tk.StringVar(value="*.csv")
        self.required_keywords_var = tk.StringVar()
        self.skip_blank_var = tk.BooleanVar(value=False)
        self.metadata_rules_var = tk.StringVar(value="{}")
        row = self._combo_row(parent, 0, "終了条件", self.end_mode_var, tuple(END_LABELS))
        for label, variable in (
            ("終了行（1始まり）", self.end_row_var),
            ("終了キーワード", self.end_keyword_var),
            ("非数値行の連続回数", self.non_numeric_count_var),
            ("ファイル名パターン（;区切り）", self.file_patterns_var),
            ("必須キーワード（;区切り）", self.required_keywords_var),
            ("メタデータ規則（JSON）", self.metadata_rules_var),
        ):
            row = self._entry_row(parent, row, label, variable)
        ttk.Checkbutton(
            parent, text="完全な空行を読み飛ばす（互換形式向け）", variable=self.skip_blank_var
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(theme.SPACE_M, 0))

    # -- background work ---------------------------------------------------
    def _next_operation(self) -> int:
        self.operation_number += 1
        return self.operation_number

    def _request_plain_preview(self) -> None:
        token = self._next_operation()
        self.preview_status_var.set("プレビューをバックグラウンドで読み込み中…")
        self.submit("import_dialog_preview", (self, token), preview_csv, self.path, None)

    def _request_preview(self, profile: ImportProfile) -> None:
        token = self._next_operation()
        self.preview_status_var.set("設定を使ってプレビューを確認中…")
        self.submit("import_dialog_preview", (self, token), preview_csv, self.path, profile)

    def auto_detect(self) -> None:
        token = self._next_operation()
        self.preview_status_var.set("保存済みプロファイルから自動検出中…")
        self.submit(
            "import_dialog_detect", (self, token), detect_profile,
            self.path, self.measurement_type, self.store.all(self.measurement_type),
        )

    def refresh_preview(self) -> None:
        profile = self._safe_build()
        if profile is not None:
            self._request_preview(profile)

    def test_current(self) -> None:
        profile = self._safe_build()
        if profile is None:
            return
        token = self._next_operation()
        self.preview_status_var.set("この設定で全データをテスト読込中…")
        self.submit("import_dialog_test", (self, token), test_import, self.path, profile)

    def _safe_build(self) -> Optional[ImportProfile]:
        try:
            profile = self.build_profile()
        except ImportProfileError as exc:
            self.set_message(str(exc))
            return None
        self.set_message("")
        return profile

    def handle_background_result(self, event_type, token, result, error) -> None:
        if self.closed or not self.winfo_exists() or token != self.operation_number:
            return
        if error is not None:
            self.preview_status_var.set(str(error))
            if event_type == "import_dialog_detect":
                self.set_message("自動検出できませんでした: {0}".format(error))
                self._request_plain_preview()
            elif event_type != "import_dialog_preview":
                self.set_message(str(error))
            return
        if event_type == "import_dialog_detect":
            if isinstance(result, ImportProfile):
                self._set_profile(result)
                self.preview_status_var.set("自動検出: {0}".format(result.name))
                self._request_preview(result)
            return
        if event_type == "import_dialog_preview" and isinstance(result, ImportPreview):
            self.preview = result
            self._render_preview(result)
            self._update_column_choices(result)
            details = [
                "文字コード: {0}".format(result.encoding),
                "区切り: {0}".format(repr(result.delimiter)),
            ]
            if result.resolved_header_row is not None:
                details.append("ヘッダー: {0}行目".format(result.resolved_header_row))
            if result.resolved_start_row is not None:
                details.append("開始候補: {0}行目".format(result.resolved_start_row))
            if result.truncated:
                details.append("301行目以降はプレビュー省略")
            self.preview_status_var.set(" / ".join(details))
            return
        if event_type == "import_dialog_test" and isinstance(result, ImportTestResult):
            self._show_test_result(result.summary())
            self.preview_status_var.set(
                "テスト成功: {0}点（{1}～{2}行）".format(
                    result.point_count, result.data_start_row, result.data_end_row
                )
            )

    def _render_preview(self, preview: ImportPreview) -> None:
        max_columns = min(max((len(row.values) for row in preview.rows), default=0), 20)
        columns = ("line", "mark") + tuple(
            "c{0}".format(index) for index in range(1, max_columns + 1)
        )
        self.preview_tree.configure(columns=columns)
        self.preview_tree.delete(*self.preview_tree.get_children(""))
        self.preview_tree.heading("line", text="行")
        self.preview_tree.column("line", width=theme.scale_int(50), stretch=False, anchor="e")
        self.preview_tree.heading("mark", text="判定")
        self.preview_tree.column("mark", width=theme.scale_int(70), stretch=False)
        for index in range(1, max_columns + 1):
            name = "c{0}".format(index)
            self.preview_tree.heading(
                name, text="{0} ({1})".format(column_letter(index), index)
            )
            self.preview_tree.column(name, width=theme.scale_int(110), stretch=False)
        candidates = set(preview.header_candidates)
        for row in preview.rows:
            marks = []
            if row.line_number in candidates:
                marks.append("Header?")
            if row.line_number == preview.resolved_header_row:
                marks.append("Header")
            if row.line_number == preview.resolved_start_row:
                marks.append("Start")
            values = list(row.values[:max_columns])
            values.extend([""] * (max_columns - len(values)))
            self.preview_tree.insert(
                "", "end", values=tuple([row.line_number, "/".join(marks)] + values)
            )

    def _update_column_choices(self, preview: ImportPreview) -> None:
        header = None
        for row in preview.rows:
            if row.line_number == preview.resolved_header_row:
                header = row
                break
        max_columns = max((len(row.values) for row in preview.rows), default=0)
        choices = []
        for index in range(1, max_columns + 1):
            value = ""
            if header is not None and index <= len(header.values):
                value = header.values[index - 1].strip()
            choices.append("{0}: {1}".format(index, value) if value else str(index))
        for box in self.mapping_boxes.values():
            box.configure(values=choices)

    def _show_test_result(self, value: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", value)
        self.result_text.configure(state="disabled")

    # -- profile plumbing --------------------------------------------------
    def _refresh_profile_choices(self) -> None:
        profiles = self.store.all(self.measurement_type, enabled_only=False)
        self.profile_by_label = {}
        for profile in profiles:
            label = "{0}{1} [{2}]".format(
                "[組込] " if profile.built_in else "", profile.name, profile.profile_id
            )
            self.profile_by_label[label] = profile
        self.profile_box.configure(
            values=("（手動・今回のみ）",) + tuple(self.profile_by_label)
        )
        if self.current_profile is None:
            self.profile_choice_var.set("（手動・今回のみ）")
        else:
            selected = "（手動・今回のみ）"
            for label, profile in self.profile_by_label.items():
                if profile.profile_id == self.current_profile.profile_id:
                    selected = label
                    break
            self.profile_choice_var.set(selected)

    def _profile_selected(self, _event=None) -> None:
        profile = self.profile_by_label.get(self.profile_choice_var.get())
        if profile is None:
            self._new_manual_profile()
            return
        self._set_profile(profile)
        self._request_preview(profile)

    def _new_manual_profile(self) -> None:
        x_unit, y_unit = DEFAULT_UNITS[self.measurement_type]
        profile = ImportProfile(
            profile_id="temporary-{0}".format(uuid.uuid4()),
            name="{0} 読込設定".format(self.measurement_type),
            measurement_type=self.measurement_type,
            header_mode="none",
            start_mode="absolute",
            start_row=1,
            columns={"x": ColumnMapping(column=1), "y": ColumnMapping(column=2)},
            units={"x": x_unit, "y": y_unit},
        )
        self.current_profile = profile
        self._set_profile(profile)
        self.profile_choice_var.set("（手動・今回のみ）")

    def _set_profile(self, profile: ImportProfile) -> None:
        self.current_profile = profile
        self.name_var.set(profile.name)
        self.enabled_var.set(profile.enabled)
        self.encoding_var.set(_reverse(ENCODING_LABELS, profile.encoding, "自動"))
        self.delimiter_var.set(_reverse(DELIMITER_LABELS, profile.delimiter, "自動"))
        self.header_mode_var.set(_reverse(HEADER_LABELS, profile.header_mode, "自動検索"))
        self.header_row_var.set("" if profile.header_row is None else str(profile.header_row))
        self.header_keyword_var.set(profile.header_keyword or "")
        self.start_mode_var.set(
            _reverse(START_LABELS, profile.start_mode, "ヘッダーの次の行")
        )
        self.start_row_var.set("" if profile.start_row is None else str(profile.start_row))
        self.start_keyword_var.set(profile.start_keyword or "")
        self.start_offset_var.set(str(profile.start_offset))
        self.end_mode_var.set(_reverse(END_LABELS, profile.end_mode, "ファイル末尾まで"))
        self.end_row_var.set("" if profile.end_row is None else str(profile.end_row))
        self.end_keyword_var.set(profile.end_keyword or "")
        self.non_numeric_count_var.set(str(profile.non_numeric_count))
        self.file_patterns_var.set(";".join(profile.file_patterns))
        self.required_keywords_var.set(";".join(profile.required_keywords))
        self.skip_blank_var.set(profile.skip_blank_rows)
        self.x_unit_var.set(profile.units.get("x", DEFAULT_UNITS[self.measurement_type][0]))
        self.y_unit_var.set(profile.units.get("y", DEFAULT_UNITS[self.measurement_type][1]))
        self.time_unit_var.set(profile.units.get("time", "min"))
        self.sample_mass_unit_var.set(profile.units.get("sample_mass", "mg"))
        self.heating_rate_unit_var.set(profile.units.get("heating_rate", "°C/min"))
        self.metadata_rules_var.set(
            json.dumps(
                dict((name, asdict(rule)) for name, rule in profile.metadata_rules.items()),
                ensure_ascii=False,
            )
            if profile.metadata_rules
            else "{}"
        )
        for role, method_var in self.mapping_method_vars.items():
            mapping = profile.columns.get(role)
            if mapping is None:
                method_var.set("未使用")
                self.mapping_value_vars[role].set("")
            elif mapping.header:
                method_var.set("ヘッダー名")
                self.mapping_value_vars[role].set(mapping.header)
            else:
                method_var.set("列番号")
                self.mapping_value_vars[role].set(str(mapping.column or ""))
        self._refresh_profile_choices()

    def _mapping_from_controls(self, role: str) -> Optional[ColumnMapping]:
        method = self.mapping_method_vars[role].get()
        raw = self.mapping_value_vars[role].get().strip()
        if method == "未使用":
            return None
        prefix, sep, remainder = raw.partition(":")
        if method == "列番号":
            try:
                return ColumnMapping(column=int(prefix.strip()))
            except ValueError:
                raise ImportProfileError(
                    "{0}の列番号が不正です: {1}".format(ROLE_TITLES[role], raw)
                )
        header = remainder.strip() if sep else raw
        if not header:
            raise ImportProfileError(
                "{0}のヘッダー名を入力してください。".format(ROLE_TITLES[role])
            )
        return ColumnMapping(header=header)

    def build_profile(self) -> ImportProfile:
        mappings = {}
        for role in self.mapping_method_vars:
            mapping = self._mapping_from_controls(role)
            if mapping is not None:
                mappings[role] = mapping
        try:
            raw_metadata = json.loads(self.metadata_rules_var.get().strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ImportProfileError("メタデータ規則JSONが不正です: {0}".format(exc))
        if not isinstance(raw_metadata, dict):
            raise ImportProfileError("メタデータ規則はJSONオブジェクトにしてください。")
        try:
            metadata_rules = dict(
                (str(name), MetadataRule(**value))
                for name, value in raw_metadata.items()
                if isinstance(value, dict)
            )
        except TypeError as exc:
            raise ImportProfileError("メタデータ規則の項目が不正です: {0}".format(exc))
        source = self.current_profile
        profile_id = (
            source.profile_id if source is not None else "temporary-{0}".format(uuid.uuid4())
        )
        units = {"x": self.x_unit_var.get().strip(), "y": self.y_unit_var.get().strip()}
        if "time" in mappings:
            units["time"] = self.time_unit_var.get().strip()
        if self.measurement_type == DSC:
            units["sample_mass"] = self.sample_mass_unit_var.get().strip()
            units["heating_rate"] = self.heating_rate_unit_var.get().strip()
        return ImportProfile(
            profile_id=profile_id,
            name=self.name_var.get().strip(),
            measurement_type=self.measurement_type,
            enabled=self.enabled_var.get(),
            encoding=ENCODING_LABELS[self.encoding_var.get()],
            delimiter=DELIMITER_LABELS[self.delimiter_var.get()],
            file_patterns=tuple(
                item.strip() for item in self.file_patterns_var.get().split(";") if item.strip()
            ),
            required_keywords=tuple(
                item.strip()
                for item in self.required_keywords_var.get().split(";")
                if item.strip()
            ),
            header_mode=HEADER_LABELS[self.header_mode_var.get()],
            header_row=_optional_positive_int(self.header_row_var.get(), "ヘッダー行"),
            header_keyword=self.header_keyword_var.get().strip() or None,
            start_mode=START_LABELS[self.start_mode_var.get()],
            start_row=_optional_positive_int(self.start_row_var.get(), "データ開始行"),
            start_offset=_integer(self.start_offset_var.get(), "相対行数", 1),
            start_keyword=self.start_keyword_var.get().strip() or None,
            columns=mappings,
            units=units,
            end_mode=END_LABELS[self.end_mode_var.get()],
            end_row=_optional_positive_int(self.end_row_var.get(), "データ終了行"),
            end_keyword=self.end_keyword_var.get().strip() or None,
            non_numeric_count=_optional_positive_int(
                self.non_numeric_count_var.get(), "非数値行の連続回数"
            ) or 1,
            metadata_rules=metadata_rules,
            version=(source.version if source else 1),
            created_at=(
                source.created_at if source else datetime.now(timezone.utc).isoformat()
            ),
            built_in=(source.built_in if source else False),
            skip_blank_rows=self.skip_blank_var.get(),
        )

    # -- commands ----------------------------------------------------------
    def accept(self) -> None:
        """［今回だけ適用］"""
        profile = self._safe_build()
        if profile is None:
            return
        self.apply_callback(self.path, replace(profile, built_in=False))
        self.close()

    def save_profile(self) -> None:
        try:
            profile = self.build_profile()
            if profile.built_in:
                raise ImportProfileError(
                    "組み込みプロファイルは上書きできません。先に［複製］してください。"
                )
            if profile.profile_id.startswith("temporary-"):
                profile = replace(profile, profile_id="user-{0}".format(uuid.uuid4()))
            saved = self.store.save(profile)
        except ImportProfileError as exc:
            self.set_message(str(exc))
            return
        self.set_message("")
        self._set_profile(saved)
        self.preview_status_var.set("プロファイルを保存しました: {0}".format(saved.name))

    def duplicate_profile(self) -> None:
        if self.current_profile is None:
            return
        try:
            duplicate = self.store.duplicate(self.current_profile.profile_id)
        except ImportProfileError:
            built = self._safe_build()
            if built is None:
                return
            duplicate = replace(
                built,
                profile_id="user-{0}".format(uuid.uuid4()),
                name="{0} のコピー".format(self.name_var.get().strip()),
                built_in=False,
            )
        self._set_profile(duplicate)
        self.profile_choice_var.set("（手動・今回のみ）")
        self.preview_status_var.set(
            "複製した設定を編集中です。［プロファイルとして保存］で確定します。"
        )

    def delete_profile(self) -> None:
        profile = self.current_profile
        if profile is None:
            return
        if not confirm_destructive(
            self, "プロファイル削除", "「{0}」を削除しますか？".format(profile.name)
        ):
            return
        try:
            self.store.delete(profile.profile_id)
        except ImportProfileError as exc:
            self.set_message(str(exc))
            return
        self.set_message("")
        self.current_profile = None
        self._new_manual_profile()
        self._refresh_profile_choices()
        self.preview_status_var.set("ユーザープロファイルを削除しました。")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, "import_dialog", None) is self:
            self.app.import_dialog = None
        super().close()
