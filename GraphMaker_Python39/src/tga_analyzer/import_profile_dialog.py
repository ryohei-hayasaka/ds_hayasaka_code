from __future__ import annotations

from typing import Union

import json
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

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
from .model import DSC, GPC, IR, PARTICLE_SIZE, TGA, UV_VIS


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
}


def _reverse(mapping: dict[str, str], value: str, default: str) -> str:
    return next((label for label, stored in mapping.items() if stored == value), default)


def _optional_positive_int(value: str, label: str) -> Union[int, None]:
    text = value.strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError as exc:
        raise ImportProfileError(f"{label}は整数で入力してください。") from exc
    if number < 1:
        raise ImportProfileError(f"{label}は1以上にしてください。")
    return number


def _integer(value: str, label: str, default: int = 0) -> int:
    text = value.strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise ImportProfileError(f"{label}は整数で入力してください。") from exc


class ImportSettingsDialog(tk.Toplevel):
    """Modal CSV profile editor. File work is delegated to the app executor."""

    def __init__(
        self,
        parent: tk.Misc,
        path: Path,
        measurement_type: str,
        store: ProfileStore,
        submit,
        apply_callback,
        initial_profile: Union[ImportProfile, None] = None,
    ) -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.path = Path(path)
        self.measurement_type = measurement_type
        self.store = store
        self.submit = submit
        self.apply_callback = apply_callback
        self.current_profile: Union[ImportProfile, None] = initial_profile
        self.preview: Union[ImportPreview, None] = None
        self.operation_number = 0
        self.closed = False
        self.title(f"読込設定 — {self.path.name}")
        self.geometry("1240x800")
        self.minsize(1040, 680)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._build()
        self._refresh_profile_choices()
        if initial_profile is not None:
            self._set_profile(initial_profile)
            self._request_preview(initial_profile)
        else:
            self._new_manual_profile()
            self._request_plain_preview()
            self.auto_detect()
        self.after_idle(self._activate_modal)

    def _activate_modal(self) -> None:
        if not self.winfo_exists():
            return
        self.grab_set()
        self.lift()
        self.focus_set()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        heading = ttk.Frame(self, padding=(10, 8, 10, 4))
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(1, weight=1)
        ttk.Label(heading, text="対象ファイル", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(heading, text=str(self.path), foreground="#475467").grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Label(heading, text=f"測定モード: {self.measurement_type}").grid(
            row=0, column=2, padx=(8, 0)
        )

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=1, column=0, sticky="nsew", padx=10)
        settings = ttk.Frame(paned, width=500)
        preview = ttk.Frame(paned)
        paned.add(settings, weight=0)
        paned.add(preview, weight=1)
        settings.columnconfigure(0, weight=1)
        settings.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=3)
        preview.rowconfigure(3, weight=1)

        profile_frame = ttk.LabelFrame(settings, text="プロファイル", padding=6)
        profile_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        profile_frame.columnconfigure(1, weight=1)
        ttk.Label(profile_frame, text="使用設定").grid(row=0, column=0, sticky="w")
        self.profile_choice_var = tk.StringVar()
        self.profile_box = ttk.Combobox(profile_frame, textvariable=self.profile_choice_var, state="readonly")
        self.profile_box.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_selected)
        ttk.Label(profile_frame, text="プロファイル名").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.name_var = tk.StringVar()
        ttk.Entry(profile_frame, textvariable=self.name_var).grid(
            row=1, column=1, sticky="ew", padx=(5, 0), pady=(5, 0)
        )
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(profile_frame, text="自動判定で使用する", variable=self.enabled_var).grid(
            row=2, column=1, sticky="w", padx=(5, 0), pady=(4, 0)
        )

        notebook = ttk.Notebook(settings)
        notebook.grid(row=1, column=0, sticky="nsew")
        basic = ttk.Frame(notebook, padding=8)
        columns = ttk.Frame(notebook, padding=8)
        ending = ttk.Frame(notebook, padding=8)
        notebook.add(basic, text="基本・開始")
        notebook.add(columns, text="列・単位")
        notebook.add(ending, text="終了・判定")
        self._build_basic_tab(basic)
        self._build_columns_tab(columns)
        self._build_end_tab(ending)

        ttk.Label(preview, text="プレビュー（最大300行）", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        preview_table_frame = ttk.Frame(preview)
        preview_table_frame.grid(row=1, column=0, sticky="nsew")
        preview_table_frame.columnconfigure(0, weight=1)
        preview_table_frame.rowconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(preview_table_frame, show="headings")
        preview_y = ttk.Scrollbar(preview_table_frame, orient=tk.VERTICAL, command=self.preview_tree.yview)
        preview_x = ttk.Scrollbar(preview_table_frame, orient=tk.HORIZONTAL, command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=preview_y.set, xscrollcommand=preview_x.set)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_y.grid(row=0, column=1, sticky="ns")
        preview_x.grid(row=1, column=0, sticky="ew")

        self.preview_status_var = tk.StringVar(value="プレビューを読み込み中...")
        ttk.Label(preview, textvariable=self.preview_status_var, foreground="#475467", wraplength=680).grid(
            row=2, column=0, sticky="ew", pady=(5, 4)
        )
        result_frame = ttk.LabelFrame(preview, text="テスト読込結果", padding=4)
        result_frame.grid(row=3, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        self.result_text = tk.Text(result_frame, height=10, wrap="word", state="disabled")
        result_scroll = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=result_scroll.set)
        self.result_text.grid(row=0, column=0, sticky="nsew")
        result_scroll.grid(row=0, column=1, sticky="ns")

        buttons = ttk.Frame(self, padding=(10, 8, 10, 10))
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure(7, weight=1)
        ttk.Button(buttons, text="自動検出", command=self.auto_detect).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(buttons, text="プレビュー更新", command=self.refresh_preview).grid(row=0, column=1, padx=5)
        ttk.Button(buttons, text="この設定でテスト", command=self.test_current).grid(row=0, column=2, padx=5)
        ttk.Button(buttons, text="今回だけ適用", style="Accent.TButton", command=self.apply_once).grid(
            row=0, column=3, padx=5
        )
        ttk.Button(buttons, text="プロファイルとして保存", command=self.save_profile).grid(
            row=0, column=4, padx=5
        )
        ttk.Button(buttons, text="複製", command=self.duplicate_profile).grid(row=0, column=5, padx=5)
        ttk.Button(buttons, text="削除", command=self.delete_profile).grid(row=0, column=6, padx=5)
        ttk.Button(buttons, text="キャンセル", command=self.cancel).grid(row=0, column=8, sticky="e")

    def _build_basic_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        row = 0
        self.encoding_var = tk.StringVar(value="自動")
        self.delimiter_var = tk.StringVar(value="自動")
        self.header_mode_var = tk.StringVar(value="自動検索")
        self.header_row_var = tk.StringVar()
        self.header_keyword_var = tk.StringVar()
        self.start_mode_var = tk.StringVar(value="ヘッダーの次の行")
        self.start_row_var = tk.StringVar()
        self.start_keyword_var = tk.StringVar()
        self.start_offset_var = tk.StringVar(value="1")
        controls = (
            ("文字コード", self.encoding_var, tuple(ENCODING_LABELS)),
            ("区切り文字", self.delimiter_var, tuple(DELIMITER_LABELS)),
            ("ヘッダー検出", self.header_mode_var, tuple(HEADER_LABELS)),
        )
        for label, variable, values in controls:
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").grid(
                row=row, column=1, sticky="ew", pady=2
            )
            row += 1
        for label, variable in (("ヘッダー行（1始まり）", self.header_row_var), ("ヘッダーキーワード", self.header_keyword_var)):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=2)
            row += 1
        ttk.Separator(parent).grid(row=row, column=0, columnspan=2, sticky="ew", pady=7)
        row += 1
        ttk.Label(parent, text="データ開始方式").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Combobox(parent, textvariable=self.start_mode_var, values=tuple(START_LABELS), state="readonly").grid(
            row=row, column=1, sticky="ew", pady=2
        )
        row += 1
        for label, variable in (
            ("データ開始行（1始まり）", self.start_row_var),
            ("開始キーワード", self.start_keyword_var),
            ("相対行数", self.start_offset_var),
        ):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=2)
            row += 1

    def _build_columns_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(2, weight=1)
        ttk.Label(parent, text="役割").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="指定方法").grid(row=0, column=1, sticky="w")
        ttk.Label(parent, text="列（A=1）").grid(row=0, column=2, sticky="w")
        self.mapping_method_vars: dict[str, tk.StringVar] = {}
        self.mapping_value_vars: dict[str, tk.StringVar] = {}
        roles = ["x", "y", "time", "record_id"]
        if self.measurement_type == DSC:
            roles.extend(("sample_mass", "heating_rate"))
        for row, role in enumerate(roles, start=1):
            ttk.Label(parent, text=ROLE_TITLES[role]).grid(row=row, column=0, sticky="w", pady=2)
            method = tk.StringVar(value="ヘッダー名" if role in {"x", "y"} else "未使用")
            value = tk.StringVar()
            self.mapping_method_vars[role] = method
            self.mapping_value_vars[role] = value
            ttk.Combobox(parent, textvariable=method, values=MAPPING_LABELS, state="readonly", width=10).grid(
                row=row, column=1, sticky="ew", padx=(4, 4), pady=2
            )
            combo = ttk.Combobox(parent, textvariable=value, state="normal")
            combo.grid(row=row, column=2, sticky="ew", pady=2)
            combo.bind("<<ComboboxSelected>>", lambda _event: None)
            setattr(self, f"_{role}_column_box", combo)
        unit_row = len(roles) + 2
        self.x_unit_var = tk.StringVar(value=DEFAULT_UNITS[self.measurement_type][0])
        self.y_unit_var = tk.StringVar(value=DEFAULT_UNITS[self.measurement_type][1])
        self.time_unit_var = tk.StringVar(value="min")
        self.sample_mass_unit_var = tk.StringVar(value="mg")
        self.heating_rate_unit_var = tk.StringVar(value="°C/min")
        ttk.Separator(parent).grid(row=unit_row - 1, column=0, columnspan=3, sticky="ew", pady=7)
        ttk.Label(parent, text="X単位").grid(row=unit_row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.x_unit_var).grid(row=unit_row, column=1, columnspan=2, sticky="ew")
        ttk.Label(parent, text="Y単位").grid(row=unit_row + 1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(parent, textvariable=self.y_unit_var).grid(
            row=unit_row + 1, column=1, columnspan=2, sticky="ew", pady=(4, 0)
        )
        if "time" in roles:
            ttk.Label(parent, text="時間単位").grid(row=unit_row + 2, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(parent, textvariable=self.time_unit_var).grid(
                row=unit_row + 2, column=1, columnspan=2, sticky="ew", pady=(4, 0)
            )
        if self.measurement_type == DSC:
            ttk.Label(parent, text="試料重量単位").grid(row=unit_row + 3, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(parent, textvariable=self.sample_mass_unit_var).grid(
                row=unit_row + 3, column=1, columnspan=2, sticky="ew", pady=(4, 0)
            )
            ttk.Label(parent, text="昇温速度単位").grid(row=unit_row + 4, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(parent, textvariable=self.heating_rate_unit_var).grid(
                row=unit_row + 4, column=1, columnspan=2, sticky="ew", pady=(4, 0)
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
        controls = (
            ("終了条件", self.end_mode_var, "combo"),
            ("終了行（1始まり）", self.end_row_var, "entry"),
            ("終了キーワード", self.end_keyword_var, "entry"),
            ("非数値行の連続回数", self.non_numeric_count_var, "entry"),
            ("ファイル名パターン（;区切り）", self.file_patterns_var, "entry"),
            ("必須キーワード（;区切り）", self.required_keywords_var, "entry"),
            ("メタデータ規則（JSON）", self.metadata_rules_var, "entry"),
        )
        for row, (label, variable, kind) in enumerate(controls):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
            if kind == "combo":
                widget = ttk.Combobox(parent, textvariable=variable, values=tuple(END_LABELS), state="readonly")
            else:
                widget = ttk.Entry(parent, textvariable=variable)
            widget.grid(row=row, column=1, sticky="ew", pady=2)
        ttk.Checkbutton(
            parent,
            text="完全な空行を読み飛ばす（互換形式向け）",
            variable=self.skip_blank_var,
        ).grid(row=len(controls), column=0, columnspan=2, sticky="w", pady=(7, 0))

    def _next_operation(self) -> int:
        self.operation_number += 1
        return self.operation_number

    def _request_plain_preview(self) -> None:
        token = self._next_operation()
        self.preview_status_var.set("プレビューをバックグラウンドで読み込み中...")
        self.submit("import_dialog_preview", (self, token), preview_csv, self.path, None)

    def _request_preview(self, profile: ImportProfile) -> None:
        token = self._next_operation()
        self.preview_status_var.set("設定を使ってプレビューを確認中...")
        self.submit("import_dialog_preview", (self, token), preview_csv, self.path, profile)

    def auto_detect(self) -> None:
        token = self._next_operation()
        self.preview_status_var.set("保存済みプロファイルから自動検出中...")
        self.submit(
            "import_dialog_detect",
            (self, token),
            detect_profile,
            self.path,
            self.measurement_type,
            self.store.all(self.measurement_type),
        )

    def refresh_preview(self) -> None:
        try:
            profile = self.build_profile()
        except ImportProfileError as exc:
            messagebox.showerror("読込設定エラー", str(exc), parent=self)
            return
        self._request_preview(profile)

    def test_current(self) -> None:
        try:
            profile = self.build_profile()
        except ImportProfileError as exc:
            messagebox.showerror("読込設定エラー", str(exc), parent=self)
            return
        token = self._next_operation()
        self.preview_status_var.set("この設定で全データをテスト読込中...")
        self.submit("import_dialog_test", (self, token), test_import, self.path, profile)

    def handle_background_result(
        self,
        event_type: str,
        token: int,
        result: Union[object, None],
        error: Union[BaseException, None],
    ) -> None:
        if self.closed or not self.winfo_exists() or token != self.operation_number:
            return
        if error is not None:
            self.preview_status_var.set(str(error))
            if event_type == "import_dialog_detect":
                messagebox.showwarning("自動検出できませんでした", str(error), parent=self)
                self._request_plain_preview()
                return
            if event_type != "import_dialog_preview":
                messagebox.showwarning("読込設定", str(error), parent=self)
            return
        if event_type == "import_dialog_detect":
            profile = result
            if isinstance(profile, ImportProfile):
                self._set_profile(profile)
                self.preview_status_var.set(f"自動検出: {profile.name}")
                self._request_preview(profile)
            return
        if event_type == "import_dialog_preview" and isinstance(result, ImportPreview):
            self.preview = result
            self._render_preview(result)
            self._update_column_choices(result)
            details = [f"文字コード: {result.encoding}", f"区切り: {repr(result.delimiter)}"]
            if result.resolved_header_row is not None:
                details.append(f"ヘッダー: {result.resolved_header_row}行目")
            if result.resolved_start_row is not None:
                details.append(f"開始候補: {result.resolved_start_row}行目")
            if result.truncated:
                details.append("301行目以降はプレビュー省略")
            self.preview_status_var.set(" / ".join(details))
            return
        if event_type == "import_dialog_test" and isinstance(result, ImportTestResult):
            self._show_test_result(result.summary())
            self.preview_status_var.set(
                f"テスト成功: {result.point_count}点（{result.data_start_row}～{result.data_end_row}行）"
            )

    def _render_preview(self, preview: ImportPreview) -> None:
        max_columns = min(max((len(row.values) for row in preview.rows), default=0), 20)
        columns = ("line", "mark") + tuple(f"c{index}" for index in range(1, max_columns + 1))
        self.preview_tree.configure(columns=columns)
        self.preview_tree.delete(*self.preview_tree.get_children(""))
        self.preview_tree.heading("line", text="行")
        self.preview_tree.column("line", width=55, stretch=False, anchor="e")
        self.preview_tree.heading("mark", text="判定")
        self.preview_tree.column("mark", width=70, stretch=False)
        for index in range(1, max_columns + 1):
            name = f"c{index}"
            self.preview_tree.heading(name, text=f"{column_letter(index)} ({index})")
            self.preview_tree.column(name, width=120, stretch=False)
        candidates = set(preview.header_candidates)
        for row in preview.rows:
            marks: list[str] = []
            if row.line_number in candidates:
                marks.append("Header?")
            if row.line_number == preview.resolved_header_row:
                marks.append("Header")
            if row.line_number == preview.resolved_start_row:
                marks.append("Start")
            values = list(row.values[:max_columns])
            values.extend([""] * (max_columns - len(values)))
            self.preview_tree.insert("", tk.END, values=(row.line_number, "/".join(marks), *values))

    def _update_column_choices(self, preview: ImportPreview) -> None:
        header = next(
            (row for row in preview.rows if row.line_number == preview.resolved_header_row),
            None,
        )
        max_columns = max((len(row.values) for row in preview.rows), default=0)
        choices = []
        for index in range(1, max_columns + 1):
            header_value = header.values[index - 1].strip() if header and index <= len(header.values) else ""
            choices.append(f"{index}: {header_value}" if header_value else str(index))
        for role in self.mapping_value_vars:
            box = getattr(self, f"_{role}_column_box")
            box.configure(values=choices)

    def _show_test_result(self, value: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", value)
        self.result_text.configure(state="disabled")

    def _refresh_profile_choices(self) -> None:
        profiles = self.store.all(self.measurement_type, enabled_only=False)
        self.profile_by_label = {
            f"{'[組込] ' if profile.built_in else ''}{profile.name} [{profile.profile_id}]": profile
            for profile in profiles
        }
        self.profile_box.configure(values=("（手動・今回のみ）", *self.profile_by_label))
        if self.current_profile is None:
            self.profile_choice_var.set("（手動・今回のみ）")
        else:
            label = next(
                (label for label, profile in self.profile_by_label.items() if profile.profile_id == self.current_profile.profile_id),
                "（手動・今回のみ）",
            )
            self.profile_choice_var.set(label)

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
            profile_id=f"temporary-{uuid.uuid4()}",
            name=f"{self.measurement_type} 読込設定",
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
        self.start_mode_var.set(_reverse(START_LABELS, profile.start_mode, "ヘッダーの次の行"))
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
                {name: asdict(rule) for name, rule in profile.metadata_rules.items()},
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

    def _mapping_from_controls(self, role: str) -> Union[ColumnMapping, None]:
        method = self.mapping_method_vars[role].get()
        raw = self.mapping_value_vars[role].get().strip()
        if method == "未使用":
            return None
        prefix, separator, remainder = raw.partition(":")
        if method == "列番号":
            candidate = prefix.strip()
            try:
                column = int(candidate)
            except ValueError as exc:
                raise ImportProfileError(f"{ROLE_TITLES[role]}の列番号が不正です: {raw}") from exc
            return ColumnMapping(column=column)
        header = remainder.strip() if separator else raw
        if not header:
            raise ImportProfileError(f"{ROLE_TITLES[role]}のヘッダー名を入力してください。")
        return ColumnMapping(header=header)

    def build_profile(self) -> ImportProfile:
        mappings: dict[str, ColumnMapping] = {}
        for role in self.mapping_method_vars:
            mapping = self._mapping_from_controls(role)
            if mapping is not None:
                mappings[role] = mapping
        try:
            raw_metadata = json.loads(self.metadata_rules_var.get().strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ImportProfileError(f"メタデータ規則JSONが不正です: {exc}") from exc
        if not isinstance(raw_metadata, dict):
            raise ImportProfileError("メタデータ規則はJSONオブジェクトにしてください。")
        try:
            metadata_rules = {
                str(name): MetadataRule(**value) for name, value in raw_metadata.items() if isinstance(value, dict)
            }
        except TypeError as exc:
            raise ImportProfileError(f"メタデータ規則の項目が不正です: {exc}") from exc
        source = self.current_profile
        profile_id = source.profile_id if source is not None else f"temporary-{uuid.uuid4()}"
        units = {
            "x": self.x_unit_var.get().strip(),
            "y": self.y_unit_var.get().strip(),
        }
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
            file_patterns=tuple(item.strip() for item in self.file_patterns_var.get().split(";") if item.strip()),
            required_keywords=tuple(item.strip() for item in self.required_keywords_var.get().split(";") if item.strip()),
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
            non_numeric_count=_optional_positive_int(self.non_numeric_count_var.get(), "非数値行の連続回数") or 1,
            metadata_rules=metadata_rules,
            version=(source.version if source else 1),
            created_at=(source.created_at if source else datetime.now(timezone.utc).isoformat()),
            built_in=(source.built_in if source else False),
            skip_blank_rows=self.skip_blank_var.get(),
        )

    def apply_once(self) -> None:
        try:
            profile = self.build_profile()
        except ImportProfileError as exc:
            messagebox.showerror("読込設定エラー", str(exc), parent=self)
            return
        self.apply_callback(self.path, replace(profile, built_in=False))
        self._close()

    def save_profile(self) -> None:
        try:
            profile = self.build_profile()
            if profile.built_in:
                raise ImportProfileError("組み込みプロファイルは上書きできません。先に［複製］してください。")
            if profile.profile_id.startswith("temporary-"):
                profile = replace(profile, profile_id=f"user-{uuid.uuid4()}")
            saved = self.store.save(profile)
        except ImportProfileError as exc:
            messagebox.showerror("プロファイル保存エラー", str(exc), parent=self)
            return
        self._set_profile(saved)
        self.profile_choice_var.set(next(
            (label for label, item in self.profile_by_label.items() if item.profile_id == saved.profile_id),
            "（手動・今回のみ）",
        ))
        self.preview_status_var.set(f"プロファイルを保存しました: {saved.name}")

    def duplicate_profile(self) -> None:
        if self.current_profile is None:
            return
        try:
            duplicate = self.store.duplicate(self.current_profile.profile_id)
        except ImportProfileError:
            duplicate = replace(
                self.build_profile(),
                profile_id=f"user-{uuid.uuid4()}",
                name=f"{self.name_var.get().strip()} のコピー",
                built_in=False,
            )
        self._set_profile(duplicate)
        self.profile_choice_var.set("（手動・今回のみ）")
        self.preview_status_var.set("複製した設定を編集中です。［プロファイルとして保存］で確定します。")

    def delete_profile(self) -> None:
        profile = self.current_profile
        if profile is None:
            return
        if not messagebox.askyesno("プロファイル削除", f"「{profile.name}」を削除しますか？", parent=self):
            return
        try:
            self.store.delete(profile.profile_id)
        except ImportProfileError as exc:
            messagebox.showerror("プロファイル削除エラー", str(exc), parent=self)
            return
        self.current_profile = None
        self._new_manual_profile()
        self._refresh_profile_choices()
        self.preview_status_var.set("ユーザープロファイルを削除しました。")

    def cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        try:
            self.parent_window.lift()
            self.parent_window.focus_set()
        except tk.TclError:
            pass
