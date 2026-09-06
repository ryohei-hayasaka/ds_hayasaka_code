"""Dialogs.

Every ``Toplevel`` in the app derives from :class:`BaseDialog`, which owns the
background colour, the modal grab, centring and the Esc/Enter bindings.  That
is what stops the OS grey from showing through (C4).
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple, Union

import tkinter as tk
from tkinter import colorchooser, ttk

from . import theme
from .series_edit import (
    ColorEditSession,
    LegendEditSession,
    navigate_legend_index,
    normalize_color,
)
from .widgets import ButtonRow, Tooltip, numeric_entry, section_label


class BaseDialog(tk.Toplevel):
    """Themed, modal, centred dialog with a standard footer."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str = "",
        resizable: bool = True,
        min_size: Optional[Tuple[int, int]] = None,
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.configure(background=theme.BG_APP)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(resizable, resizable)
        if min_size is not None:
            self.minsize(theme.scale_int(min_size[0]), theme.scale_int(min_size[1]))
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", self.cancel)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew",
                    padx=theme.SPACE_XL, pady=(theme.SPACE_L, 0))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=title, style="Head.TLabel").grid(row=0, column=0, sticky="w")
        if subtitle:
            ttk.Label(header, text=subtitle, style="Second.TLabel", justify="left").grid(
                row=1, column=0, sticky="w", pady=(2, 0)
            )

        self.body = ttk.Frame(self, style="App.TFrame")
        self.body.grid(row=1, column=0, sticky="nsew",
                       padx=theme.SPACE_XL, pady=theme.SPACE_M)

        self._footer = ttk.Frame(self, style="App.TFrame")
        self._footer.grid(row=2, column=0, sticky="ew",
                          padx=theme.SPACE_XL, pady=(0, theme.SPACE_L))
        self._footer.columnconfigure(0, weight=1)
        self.footer_left = ttk.Frame(self._footer, style="App.TFrame")
        self.footer_left.grid(row=0, column=0, sticky="w")
        self._footer_right = ttk.Frame(self._footer, style="App.TFrame")
        self._footer_right.grid(row=0, column=1, sticky="e")
        self.message_var = tk.StringVar(value="")
        ttk.Label(
            self._footer, textvariable=self.message_var, style="Error.TLabel"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(theme.SPACE_S, 0))

    # -- footer ------------------------------------------------------------
    def add_footer_buttons(self, accept_text: str = "OK", cancel_text: str = "キャンセル") -> None:
        ttk.Button(
            self._footer_right, text=cancel_text, command=self.cancel
        ).grid(row=0, column=0, padx=(0, theme.SPACE_S))
        self.accept_button = ttk.Button(
            self._footer_right, text=accept_text, style="Accent.TButton", command=self.accept
        )
        self.accept_button.grid(row=0, column=1)
        self.bind("<Return>", lambda _event: self.accept())

    def set_message(self, text: str) -> None:
        self.message_var.set(text)

    # -- lifecycle ---------------------------------------------------------
    def present(self) -> None:
        """Size, centre, grab and focus.  Call once the body is populated."""
        self.update_idletasks()
        try:
            parent = self.parent.winfo_toplevel()
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
            self.geometry("+{0}+{1}".format(max(x, 0), max(y, 0)))
        except tk.TclError:
            pass
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()

    def accept(self) -> None:
        self.close()

    def cancel(self, _event=None):
        self.close()
        return "break"

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.parent.winfo_toplevel().focus_set()
        except tk.TclError:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# Colour editing
# ---------------------------------------------------------------------------
class ColorEditorDialog(BaseDialog):
    """Assign one colour to several series at once."""

    COLUMNS = ("selected", "current", "pending", "legend", "name")
    HEADINGS = (
        ("selected", "選択", 50),
        ("current", "現在", 90),
        ("pending", "変更後", 90),
        ("legend", "凡例名", 190),
        ("name", "系列名", 220),
    )

    def __init__(
        self,
        parent: tk.Misc,
        curves,
        on_apply: Callable[[dict], None],
        preselected=(),
    ) -> None:
        super().__init__(
            parent,
            "色を変更",
            "行を選び、パレットまたは［任意の色…］で適用します。",
            min_size=(720, 460),
        )
        self._curves = tuple(curves)
        self._on_apply = on_apply
        self._session = ColorEditSession(
            {curve.key: curve.color for curve in self._curves}
        )
        self._item_keys = {}
        self._swatches = {}
        self._build(preselected)
        self.present()

    def _build(self, preselected) -> None:
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)

        table = ttk.Frame(self.body, style="App.TFrame")
        table.grid(row=0, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table, columns=self.COLUMNS, show="headings", selectmode="extended"
        )
        for name, label, width in self.HEADINGS:
            self.tree.heading(name, text=label)
            self.tree.column(
                name,
                width=theme.scale_int(width),
                minwidth=theme.scale_int(46),
                stretch=name in {"legend", "name"},
                anchor="center" if name == "selected" else "w",
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_changed)
        self.tree.bind("<Double-Button-1>", self._on_double_click)

        palette = ttk.Frame(self.body, style="App.TFrame")
        palette.grid(row=1, column=0, sticky="ew", pady=(theme.SPACE_M, 0))
        section_label(palette, "パレット", panel=False).grid(
            row=0, column=0, columnspan=len(theme.SERIES_PALETTE), sticky="w"
        )
        for index, color in enumerate(theme.SERIES_PALETTE):
            button = tk.Button(
                palette,
                background=color,
                activebackground=color,
                relief="flat",
                borderwidth=0,
                highlightthickness=1,
                highlightbackground=theme.BORDER_STRONG,
                width=2,
                height=1,
                command=lambda value=color: self._apply_to_selection(value),
            )
            button.grid(row=1, column=index, padx=(0, theme.SPACE_XS), pady=(theme.SPACE_XS, 0))
            Tooltip(button, color)

        actions = ButtonRow(self.body)
        actions.configure(style="App.TFrame")
        actions.grid(row=2, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        actions.add("任意の色…", self._choose_for_selection)
        actions.add("元に戻す", self._reset)

        self.add_footer_buttons("適用")
        self._refresh_rows()
        if preselected:
            wanted = [
                item for item, key in self._item_keys.items() if key in set(preselected)
            ]
            if wanted:
                self.tree.selection_set(wanted)

    def _refresh_rows(self) -> None:
        selection = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children(""))
        self._item_keys.clear()
        self._swatches.clear()
        for index, curve in enumerate(self._curves):
            item = "color_{0}".format(index)
            self._item_keys[item] = curve.key
            pending = self._session.pending[curve.key]
            self.tree.insert(
                "",
                "end",
                iid=item,
                values=(
                    "●" if item in selection else "",
                    self._session.original[curve.key],
                    pending,
                    curve.legend_label,
                    curve.display_name,
                ),
            )
            tag = "pending_{0}".format(index)
            self.tree.tag_configure(tag, foreground=pending)
            self.tree.item(item, tags=(tag,))
        if selection:
            self.tree.selection_set([item for item in selection if item in self._item_keys])

    def _on_selection_changed(self, _event=None) -> None:
        chosen = set(self.tree.selection())
        for item in self._item_keys:
            self.tree.set(item, "selected", "●" if item in chosen else "")

    def _selected_keys(self) -> list:
        keys = [
            self._item_keys[item]
            for item in self.tree.selection()
            if item in self._item_keys
        ]
        return keys or list(self._item_keys.values())

    def _apply_to_selection(self, color: str) -> None:
        try:
            self._session.apply(self._selected_keys(), color)
        except ValueError as exc:
            self.set_message(str(exc))
            return
        self.set_message("")
        self._refresh_rows()

    def _choose_for_selection(self) -> None:
        keys = self._selected_keys()
        current = self._session.pending.get(keys[0], theme.SERIES_PALETTE[0]) if keys else None
        chosen = colorchooser.askcolor(color=current, parent=self)
        if chosen and chosen[1]:
            self._apply_to_selection(chosen[1])

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item not in self._item_keys:
            return None
        self.tree.selection_set(item)
        self._choose_for_selection()
        return "break"

    def _reset(self) -> None:
        self._session.reset()
        self._refresh_rows()

    def accept(self) -> None:
        changed = self._session.changed()
        self.close()
        if changed:
            self._on_apply(changed)


class ColorPickerDialog(BaseDialog):
    """Single-series colour picker opened from the series table swatch."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        current: str,
        on_apply: Callable[[str], None],
    ) -> None:
        super().__init__(parent, "色を選択", title, resizable=False)
        self._on_apply = on_apply
        self._value = tk.StringVar(value=normalize_color(current))
        self._build()
        self.present()

    def _build(self) -> None:
        grid = ttk.Frame(self.body, style="App.TFrame")
        grid.grid(row=0, column=0, sticky="ew")
        for index, color in enumerate(theme.SERIES_PALETTE):
            button = tk.Button(
                grid,
                background=color,
                activebackground=color,
                relief="flat",
                borderwidth=0,
                highlightthickness=1,
                highlightbackground=theme.BORDER_STRONG,
                width=3,
                height=1,
                command=lambda value=color: self._pick(value),
            )
            button.grid(row=index // 5, column=index % 5,
                        padx=theme.SPACE_XS, pady=theme.SPACE_XS)
            Tooltip(button, color)

        row = ttk.Frame(self.body, style="App.TFrame")
        row.grid(row=1, column=0, sticky="ew", pady=(theme.SPACE_M, 0))
        ttk.Label(row, text="#RRGGBB", style="Second.TLabel").grid(row=0, column=0, sticky="w")
        entry = numeric_entry(row, self._value, width=12)
        entry.configure(justify="left")
        entry.grid(row=0, column=1, padx=(theme.SPACE_S, theme.SPACE_S))
        ttk.Button(row, text="任意の色…", command=self._choose).grid(row=0, column=2)
        self.add_footer_buttons("適用")

    def _pick(self, color: str) -> None:
        self._value.set(color)
        self.accept()

    def _choose(self) -> None:
        chosen = colorchooser.askcolor(color=self._value.get(), parent=self)
        if chosen and chosen[1]:
            self._value.set(chosen[1].upper())

    def accept(self) -> None:
        try:
            color = normalize_color(self._value.get())
        except ValueError as exc:
            self.set_message(str(exc))
            return
        self.close()
        self._on_apply(color)


# ---------------------------------------------------------------------------
# Legend names
# ---------------------------------------------------------------------------
class LegendEditorDialog(BaseDialog):
    """Continuous editing of every legend name, with multi-line paste."""

    COLUMNS = ("index", "legend", "name")

    def __init__(
        self,
        parent: tk.Misc,
        rows: Sequence[Tuple[str, str, str]],
        on_apply: Callable[[dict], None],
    ) -> None:
        super().__init__(
            parent,
            "凡例名を編集",
            "ダブルクリックまたはF2で編集、Enterで次の行へ。複数行の貼り付けにも対応します。",
            min_size=(640, 460),
        )
        self._on_apply = on_apply
        self._order = tuple(key for key, _legend, _name in rows)
        self._names = {key: name for key, _legend, name in rows}
        self._session = LegendEditSession(
            order=self._order,
            original={key: legend for key, legend, _name in rows},
        )
        self._item_keys = {}
        self._editor = None
        self._editor_item = None
        self._build()
        self.present()

    def _build(self) -> None:
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        table = ttk.Frame(self.body, style="App.TFrame")
        table.grid(row=0, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table, columns=self.COLUMNS, show="headings", selectmode="browse"
        )
        for name, label, width, stretch in (
            ("index", "#", 40, False),
            ("legend", "凡例名", 260, True),
            ("name", "系列名", 260, True),
        ):
            self.tree.heading(name, text=label)
            self.tree.column(
                name, width=theme.scale_int(width), minwidth=theme.scale_int(40),
                stretch=stretch,
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<F2>", self.begin_edit)
        self.tree.bind("<Control-v>", self._paste)
        self.add_footer_buttons("適用")
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        selection = list(self.tree.selection())
        self.tree.delete(*self.tree.get_children(""))
        self._item_keys.clear()
        for index, key in enumerate(self._order):
            item = "legend_{0}".format(index)
            self._item_keys[item] = key
            self.tree.insert(
                "",
                "end",
                iid=item,
                values=(index + 1, self._session.pending[key], self._names[key]),
            )
        if selection and selection[0] in self._item_keys:
            self.tree.selection_set(selection[0])

    def _index_for_item(self, item: str) -> int:
        try:
            return int(item.split("_")[1])
        except (IndexError, ValueError):
            return 0

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item in self._item_keys:
            self.tree.selection_set(item)
            self.begin_edit()
        return "break"

    def begin_edit(self, _event=None):
        self._cancel_editor()
        selection = self.tree.selection()
        if not selection:
            return "break"
        item = selection[0]
        try:
            box = self.tree.bbox(item, "legend")
        except tk.TclError:
            return "break"
        if not box:
            return "break"
        x, y, width, height = box
        editor = ttk.Entry(self.tree)
        editor.insert(0, self.tree.set(item, "legend"))
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.bind("<Return>", lambda event: self._commit_and_move(event, "next"))
        editor.bind("<Escape>", self._cancel_editor)
        editor.bind("<Tab>", lambda event: self._commit_and_move(event, "next"))
        self._editor = editor
        self._editor_item = item
        return "break"

    def _commit_editor(self) -> bool:
        if self._editor is None or self._editor_item is None:
            return True
        key = self._item_keys.get(self._editor_item)
        value = self._editor.get()
        try:
            self._session.set_name(key, value)
        except ValueError as exc:
            self.set_message(str(exc))
            return False
        self.set_message("")
        self._cancel_editor()
        self._refresh_rows()
        return True

    def _commit_and_move(self, _event, direction: str):
        item = self._editor_item
        if not self._commit_editor():
            return "break"
        if item is None:
            return "break"
        index = navigate_legend_index(
            self._index_for_item(item), len(self._order), direction
        )
        target = "legend_{0}".format(index)
        if target in self._item_keys:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.begin_edit()
        return "break"

    def _cancel_editor(self, _event=None):
        if self._editor is not None:
            try:
                self._editor.destroy()
            except tk.TclError:
                pass
        self._editor = None
        self._editor_item = None
        return "break"

    def _paste(self, _event=None):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"
        selection = self.tree.selection()
        start = self._index_for_item(selection[0]) if selection else 0
        last = self._session.paste_lines(start, text)
        self._refresh_rows()
        target = "legend_{0}".format(last)
        if target in self._item_keys:
            self.tree.selection_set(target)
        return "break"

    def accept(self) -> None:
        if not self._commit_editor():
            return
        changed = self._session.changed()
        self.close()
        if changed:
            self._on_apply(changed)

    def cancel(self, _event=None):
        self._cancel_editor()
        return super().cancel(_event)


# ---------------------------------------------------------------------------
# Sample information
# ---------------------------------------------------------------------------
class SampleInfoDialog(BaseDialog):
    """Per-series numeric sample data (width / thickness / L0 / sample mass).

    Navigation matches a spreadsheet: click or arrow keys select a cell,
    typing a digit replaces its value, F2/double-click edits the existing
    value, and Enter commits and moves down. Every key handler that could
    otherwise leak up to the dialog's own <Return> (mapped to "適用") returns
    "break" explicitly — see the Enter-propagation bug this fixes.
    """

    _NAVIGATION_KEYSYMS = (
        "Up", "Down", "Left", "Right", "Return", "KP_Enter", "F2", "Tab",
        "ISO_Left_Tab", "Escape",
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Caps_Lock", "Home", "End", "Prior", "Next",
    )

    def __init__(
        self,
        parent: tk.Misc,
        curves,
        fields: Sequence[Tuple[str, str]],
        current: dict,
        on_apply: Callable[[dict], None],
        title: str = "試料情報入力",
    ) -> None:
        super().__init__(
            parent,
            title,
            "セルを選んで直接入力、F2/ダブルクリックで既存値を編集、Enter/矢印キーでセル移動します。"
            "［代表値を全系列へ］で一括入力できます。",
            min_size=(640, 420),
        )
        self._curves = tuple(curves)
        self._fields = tuple(fields)
        self._field_order = tuple(name for name, _label in self._fields)
        self._on_apply = on_apply
        self._values = {
            curve.key: {
                name: current.get(curve.key, {}).get(name)
                for name, _label in self._fields
            }
            for curve in self._curves
        }
        self._item_keys = {}
        self._editor = None
        self._editor_cell = None
        self._selected_cell = None  # type: Optional[Tuple[str, str]]
        self._build()
        self.present()
        # present() focuses the dialog itself; steer keyboard focus to the
        # grid so arrow keys and typing work without an extra click. force()
        # is needed because the window manager may not have handed real
        # input focus to a freshly mapped Toplevel yet.
        self.tree.focus_force()

    def _build(self) -> None:
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        columns = ("name",) + self._field_order
        table = ttk.Frame(self.body, style="App.TFrame")
        table.grid(row=0, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table, columns=columns, show="headings", selectmode="browse"
        )
        self.tree.heading("name", text="系列名")
        self.tree.column("name", width=theme.scale_int(240), stretch=True)
        for name, label in self._fields:
            self.tree.heading(name, text=label)
            self.tree.column(
                name, width=theme.scale_int(130), minwidth=theme.scale_int(80),
                stretch=False, anchor="e",
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<F2>", self._on_f2)
        self.tree.bind("<Up>", lambda _e: self._on_move(-1, 0))
        self.tree.bind("<Down>", lambda _e: self._on_move(1, 0))
        self.tree.bind("<Left>", lambda _e: self._on_move(0, -1))
        self.tree.bind("<Right>", lambda _e: self._on_move(0, 1))
        self.tree.bind("<Return>", lambda _e: self._on_move(1, 0))
        self.tree.bind("<KP_Enter>", lambda _e: self._on_move(1, 0))
        self.tree.bind("<Key>", self._on_type_key)

        actions = ButtonRow(self.body)
        actions.configure(style="App.TFrame")
        actions.grid(row=1, column=0, sticky="w", pady=(theme.SPACE_M, 0))
        actions.add("代表値を全系列へ", self._apply_representative,
                    tooltip="選択セルの値を同じ列の全系列へ適用します。")
        self.add_footer_buttons("適用")
        self._refresh_rows()
        if self._item_keys and self._field_order:
            self._select_cell("sample_0", self._field_order[0])

    def _refresh_rows(self) -> None:
        previous = self._selected_cell
        self.tree.delete(*self.tree.get_children(""))
        self._item_keys.clear()
        for index, curve in enumerate(self._curves):
            item = "sample_{0}".format(index)
            self._item_keys[item] = curve.key
            values = [curve.display_name]
            for name, _label in self._fields:
                value = self._values[curve.key][name]
                values.append("" if value is None else "{0:g}".format(value))
            self.tree.insert("", "end", iid=item, values=tuple(values))
        if previous is not None and previous[0] in self._item_keys:
            self._select_cell(*previous)

    # -- cell addressing -----------------------------------------------
    def _field_for_column(self, column: str) -> Optional[str]:
        try:
            index = int(column[1:]) - 2
        except (ValueError, IndexError):
            return None
        if 0 <= index < len(self._fields):
            return self._fields[index][0]
        return None

    def _column_for_field(self, field: str) -> Optional[str]:
        try:
            return "#{0}".format(self._field_order.index(field) + 2)
        except ValueError:
            return None

    def _row_index(self, item: Optional[str]) -> Optional[int]:
        if item is None:
            return None
        try:
            return int(item.split("_")[1])
        except (IndexError, ValueError):
            return None

    def _select_cell(self, item: str, field: str) -> None:
        self._selected_cell = (item, field)
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)

    # -- selection and navigation ---------------------------------------
    def _on_click(self, event) -> Optional[str]:
        self._cancel_edit()
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item not in self._item_keys:
            return None
        field = self._field_for_column(column)
        if field is None:
            # Clicked the read-only name column: move the row but keep field.
            field = (self._selected_cell or (None, self._field_order[0]))[1]
        self._select_cell(item, field)
        return None

    def _on_move(self, delta_row: int, delta_col: int) -> str:
        if self._editor is not None:
            if not self._finish_edit(True):
                return "break"
        if self._selected_cell is None:
            return "break"
        item, field = self._selected_cell
        row = self._row_index(item)
        col = self._field_order.index(field) if field in self._field_order else 0
        if row is None:
            return "break"
        new_row = max(0, min(len(self._curves) - 1, row + delta_row))
        new_col = max(0, min(len(self._field_order) - 1, col + delta_col))
        self._select_cell("sample_{0}".format(new_row), self._field_order[new_col])
        return "break"

    def _on_f2(self, _event) -> str:
        if self._selected_cell is not None:
            self._start_edit(*self._selected_cell)
        return "break"

    def _on_double_click(self, event) -> str:
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        field = self._field_for_column(column)
        if item in self._item_keys and field is not None:
            self._select_cell(item, field)
            self._start_edit(item, field)
        return "break"

    def _on_type_key(self, event) -> Optional[str]:
        if self._editor is not None:
            return None
        if event.keysym in self._NAVIGATION_KEYSYMS:
            return None
        if event.state & 0x4:  # Control held: let accelerators pass through.
            return None
        char = event.char
        if not char or not char.isprintable():
            return None
        if self._selected_cell is None:
            return None
        item, field = self._selected_cell
        self._start_edit(item, field, initial_char=char)
        return "break"

    # -- editing ----------------------------------------------------------
    def _start_edit(self, item, field, initial_char: Optional[str] = None) -> None:
        self._cancel_edit()
        if item is None or field is None or item not in self._item_keys:
            return
        column = self._column_for_field(field)
        if column is None:
            return
        try:
            box = self.tree.bbox(item, column)
        except tk.TclError:
            return
        if not box:
            return
        x, y, width, height = box
        editor = ttk.Entry(self.tree, justify="right", style="Num.TEntry")
        if initial_char is None:
            editor.insert(0, self.tree.set(item, field))
            editor.select_range(0, "end")
        else:
            editor.insert(0, initial_char)
            editor.icursor("end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.bind("<Return>", lambda _e: self._commit_and_move(1, 0))
        editor.bind("<KP_Enter>", lambda _e: self._commit_and_move(1, 0))
        editor.bind("<Tab>", lambda _e: self._commit_and_move(0, 1))
        editor.bind("<ISO_Left_Tab>", lambda _e: self._commit_and_move(0, -1))
        editor.bind("<Shift-Tab>", lambda _e: self._commit_and_move(0, -1))
        editor.bind("<Escape>", lambda _e: self._cancel_edit())
        editor.bind("<FocusOut>", lambda _e: self._finish_edit(True))
        self._editor = editor
        self._editor_cell = (item, field)

    def _commit_and_move(self, delta_row: int, delta_col: int) -> str:
        if not self._finish_edit(True):
            return "break"
        return self._on_move(delta_row, delta_col)

    def _finish_edit(self, commit: bool) -> bool:
        if self._editor is None or self._editor_cell is None:
            return True
        item, field = self._editor_cell
        text = self._editor.get().strip()
        self._cancel_edit()
        if not commit:
            return True
        key = self._item_keys.get(item)
        if key is None:
            return True
        if not text:
            self._values[key][field] = None
        else:
            try:
                value = float(text)
            except ValueError:
                self.set_message("{0}は数値で入力してください。".format(field))
                return False
            if value <= 0:
                self.set_message("{0}は0より大きい値を入力してください。".format(field))
                return False
            self._values[key][field] = value
        self.set_message("")
        self._refresh_rows()
        return True

    def _cancel_edit(self, _event=None) -> str:
        if self._editor is not None:
            try:
                self._editor.destroy()
            except tk.TclError:
                pass
        self._editor = None
        self._editor_cell = None
        # Destroying the entry drops focus to the dialog itself; keep it on
        # the grid so arrow keys keep navigating without an extra click.
        try:
            self.tree.focus_set()
        except tk.TclError:
            pass
        return "break"

    def _apply_representative(self) -> None:
        cell = self._selected_cell
        if cell is None:
            self.set_message("代表値にするセルを選んでください。")
            return
        item, field = cell
        key = self._item_keys.get(item)
        if key is None:
            self.set_message("代表値にするセルを選んでください。")
            return
        value = self._values[key][field]
        for row in self._values.values():
            row[field] = value
        self.set_message("")
        self._refresh_rows()

    def accept(self) -> None:
        if not self._finish_edit(True):
            return
        values = {key: dict(row) for key, row in self._values.items()}
        self.close()
        self._on_apply(values)


def confirm_destructive(parent: tk.Misc, title: str, message: str) -> bool:
    """Modal yes/no.  One of only two places a messagebox is allowed."""
    from tkinter import messagebox

    return bool(
        messagebox.askyesno(title, message, parent=parent.winfo_toplevel(), icon="warning")
    )


def report_os_error(parent: tk.Misc, title: str, message: str) -> None:
    """The other allowed messagebox: an OS-level failure the user must see."""
    from tkinter import messagebox

    messagebox.showerror(title, message, parent=parent.winfo_toplevel())
