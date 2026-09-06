"""Reusable UI pieces shared by the main window and the dialogs."""

from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple, Union

import tkinter as tk
from tkinter import ttk

from . import theme


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def section_label(parent, text: str, panel: bool = True, **grid):
    """A section heading.  Grouping is whitespace plus this, never a frame."""
    style = "PanelHead.TLabel" if panel else "Head.TLabel"
    label = ttk.Label(parent, text=text, style=style)
    if grid:
        label.grid(**grid)
    return label


def hint_label(parent, textvariable, panel: bool = True, **grid):
    style = "PanelSecond.TLabel" if panel else "Second.TLabel"
    label = ttk.Label(parent, textvariable=textvariable, style=style, justify="left")
    if grid:
        label.grid(**grid)
    return label


def error_label(parent, textvariable, panel: bool = True, **grid):
    style = "PanelError.TLabel" if panel else "Error.TLabel"
    label = ttk.Label(parent, textvariable=textvariable, style=style, justify="left")
    if grid:
        label.grid(**grid)
    return label


def separator(parent, **grid):
    line = ttk.Separator(parent, orient="horizontal")
    if grid:
        line.grid(**grid)
    return line


class Tooltip:
    """Hover text.  Carries the reason a control is disabled (see D7)."""

    def __init__(self, widget: tk.Misc, text: str = "") -> None:
        self.widget = widget
        self._text = text
        self._window = None  # type: Optional[tk.Toplevel]
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self._text = text
        if not text:
            self._hide()

    def _schedule(self, _event=None) -> None:
        if not self._text:
            return
        self._cancel()
        self._job = self.widget.after(450, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        if self._window is not None or not self._text:
            return
        try:
            x = self.widget.winfo_rootx() + theme.scale_int(8)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + theme.scale_int(4)
        except tk.TclError:
            return
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.configure(background=theme.BORDER_STRONG)
        label = tk.Label(
            window,
            text=self._text,
            background=theme.TEXT,
            foreground=theme.TEXT_ON_ACCENT,
            font=theme.FONT_LABEL,
            justify="left",
            padx=theme.scale_int(8),
            pady=theme.scale_int(4),
        )
        label.pack(padx=1, pady=1)
        window.wm_geometry("+{0}+{1}".format(int(x), int(y)))
        self._window = window

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def set_enabled(widget, enabled: bool, tooltip: Optional[Tooltip] = None, reason: str = "") -> None:
    """Disable instead of popping a dialog later; explain why on hover."""
    widget.configure(state="normal" if enabled else "disabled")
    if tooltip is not None:
        tooltip.set_text("" if enabled else reason)


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------
class StatusBar(ttk.Frame):
    """Transient messages.  Replaces the old wall of ``messagebox`` popups."""

    _STYLES = {
        "info": "Status.TLabel",
        "success": "StatusSuccess.TLabel",
        "warning": "StatusWarning.TLabel",
        "error": "StatusError.TLabel",
    }

    def __init__(self, master, **kwargs):
        super().__init__(master, style="Bar.TFrame", **kwargs)
        self.columnconfigure(1, weight=1)
        self._dot = ttk.Label(self, text="●", style="Status.TLabel")
        self._dot.grid(row=0, column=0, padx=(theme.SPACE_M, theme.SPACE_XS), pady=theme.SPACE_XS)
        self._message = tk.StringVar(value="")
        self._label = ttk.Label(
            self, textvariable=self._message, style="Status.TLabel", anchor="w"
        )
        self._label.grid(row=0, column=1, sticky="ew")
        self._actions = ttk.Frame(self, style="Bar.TFrame")
        self._actions.grid(row=0, column=2, sticky="e", padx=(0, theme.SPACE_M))
        self._fade_job = None

    @property
    def actions(self) -> ttk.Frame:
        return self._actions

    def show(self, message: str, level: str = "info", fade_ms: int = 6000) -> None:
        style = self._STYLES.get(level, "Status.TLabel")
        self._label.configure(style=style)
        self._dot.configure(style=style)
        self._message.set(message)
        if self._fade_job is not None:
            try:
                self.after_cancel(self._fade_job)
            except Exception:
                pass
            self._fade_job = None
        if fade_ms and level in {"info", "success"}:
            self._fade_job = self.after(fade_ms, self._fade)

    def _fade(self) -> None:
        self._fade_job = None
        self._label.configure(style="Status.TLabel")
        self._dot.configure(style="Status.TLabel")

    def message(self) -> str:
        return self._message.get()


# ---------------------------------------------------------------------------
# Scrollable panel
# ---------------------------------------------------------------------------
class ScrollFrame(ttk.Frame):
    """Vertically scrollable container whose inner frame tracks the width."""

    def __init__(self, master, background: str = theme.BG_PANEL, **kwargs):
        super().__init__(master, **kwargs)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._canvas = tk.Canvas(
            self, background=background, highlightthickness=0, borderwidth=0
        )
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._scroll = ttk.Scrollbar(
            self, orient="vertical", command=self._canvas.yview
        )
        self._scroll.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=self._on_scroll_set)
        self.body = ttk.Frame(self._canvas, style="Panel.TFrame")
        self._window = self._canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self._canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_scroll_set(self, first, last) -> None:
        # Hide the scrollbar when everything fits; a permanent empty trough
        # reads as clutter in a narrow inspector.
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scroll.grid_remove()
        else:
            self._scroll.grid()
        self._scroll.set(first, last)

    def _on_body_configure(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        try:
            first, last = self._canvas.yview()
        except tk.TclError:
            return
        if first <= 0.0 and last >= 1.0:
            return
        self._canvas.yview_scroll(int(-event.delta / 120), "units")


# ---------------------------------------------------------------------------
# Series table
# ---------------------------------------------------------------------------
VISIBLE_ON = "●"
VISIBLE_OFF = "○"


class SeriesTable(ttk.Frame):
    """The single series table (D4).

    One row per loaded curve with a visibility toggle, a real colour chip, an
    inline-editable legend name and mode-specific processing columns.
    """

    #: The colour chip lives in the tree column (#0) because only that column
    #: can hold an image; the rest are ordinary value columns.
    BASE_COLUMNS = ("visible", "legend", "readout", "blank", "normalization",
                    "zero", "particle", "source")
    HEADINGS = {
        "visible": ("表示", 44, False),
        "legend": ("凡例名", 150, True),
        "readout": ("Y値", 80, False),
        "blank": ("ブランク", 120, True),
        "normalization": ("規格化位置", 105, False),
        "zero": ("0点位置", 95, False),
        "particle": ("規格化粒径", 105, False),
        "source": ("元ファイル", 150, True),
    }

    def __init__(
        self,
        master,
        on_selection_changed: Callable[[list], None],
        on_toggle_visible: Callable[[str], None],
        on_pick_color: Callable[[str], None],
        on_rename: Callable[[str, str], bool],
        **kwargs
    ):
        super().__init__(master, style="Panel.TFrame", **kwargs)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._on_selection_changed = on_selection_changed
        self._on_toggle_visible = on_toggle_visible
        self._on_pick_color = on_pick_color
        self._on_rename = on_rename
        self._item_keys = {}
        self._swatches = {}  # keep PhotoImage references alive
        self._editor = None  # type: Optional[ttk.Entry]
        self._editor_item = None
        self._suspend_select = False

        self.tree = ttk.Treeview(
            self,
            columns=self.BASE_COLUMNS,
            show="tree headings",
            selectmode="extended",
        )
        self.tree.column(
            "#0", width=theme.scale_int(40), minwidth=theme.scale_int(40),
            stretch=False, anchor="center",
        )
        self.tree.heading("#0", text="色")
        for name in self.BASE_COLUMNS:
            label, width, stretch = self.HEADINGS[name]
            self.tree.heading(name, text=label)
            self.tree.column(
                name,
                width=theme.scale_int(width),
                minwidth=theme.scale_int(36),
                stretch=stretch,
                anchor="center" if name == "visible" else "w",
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.tag_configure("hidden", foreground=theme.TEXT_MUTED)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<Double-Button-1>", self._on_double_click)

    # -- population --------------------------------------------------------
    def set_visible_columns(self, columns: Sequence[str]) -> None:
        self.tree.configure(displaycolumns=tuple(columns))

    def populate(self, rows, selected_keys=()) -> None:
        """``rows`` is a sequence of dicts describing each series."""
        self.cancel_edit()
        self._suspend_select = True
        try:
            self.tree.delete(*self.tree.get_children(""))
            self._item_keys.clear()
            self._swatches.clear()
            for index, row in enumerate(rows):
                item = "series_{0}".format(index)
                self._item_keys[item] = row["key"]
                image = self._swatch(row["color"])
                self._swatches[item] = image
                self.tree.insert(
                    "",
                    "end",
                    iid=item,
                    image=image,
                    values=(
                        VISIBLE_ON if row.get("visible", True) else VISIBLE_OFF,
                        row.get("legend", ""),
                        row.get("readout", ""),
                        row.get("blank", "—"),
                        row.get("normalization", "—"),
                        row.get("zero", "—"),
                        row.get("particle", "—"),
                        row.get("source", ""),
                    ),
                    tags=() if row.get("visible", True) else ("hidden",),
                )
            wanted = [
                item for item, key in self._item_keys.items() if key in set(selected_keys)
            ]
            if wanted:
                self.tree.selection_set(wanted)
                self.tree.focus(wanted[0])
        finally:
            self._suspend_select = False

    def _swatch(self, color: str) -> tk.PhotoImage:
        size = theme.scale_int(13)
        image = tk.PhotoImage(master=self.tree, width=size, height=size)
        image.put(color, to=(0, 0, size, size))
        return image

    # -- selection ---------------------------------------------------------
    def selected_keys(self) -> list:
        return [
            self._item_keys[item]
            for item in self.tree.selection()
            if item in self._item_keys
        ]

    def select_keys(self, keys) -> None:
        wanted = [item for item, key in self._item_keys.items() if key in set(keys)]
        current = list(self.tree.selection())
        if current == wanted:
            return
        self._suspend_select = True
        try:
            self.tree.selection_set(wanted)
            if wanted:
                self.tree.focus(wanted[0])
        finally:
            self._suspend_select = False

    def _on_select(self, _event=None) -> None:
        if self._suspend_select:
            return
        self._on_selection_changed(self.selected_keys())

    # -- cell interaction --------------------------------------------------
    def _on_click(self, event) -> Optional[str]:
        self.cancel_edit()
        region = self.tree.identify_region(event.x, event.y)
        if region not in {"cell", "tree"}:
            return None
        item = self.tree.identify_row(event.y)
        key = self._item_keys.get(item)
        if key is None:
            return None
        column = self.tree.identify_column(event.x)
        name = self._column_name(column)
        if name == "visible":
            self._on_toggle_visible(key)
            return "break"
        if name == "#0":
            self._on_pick_color(key)
            return "break"
        return None

    def _on_double_click(self, event) -> Optional[str]:
        item = self.tree.identify_row(event.y)
        if item not in self._item_keys:
            return None
        if self._column_name(self.tree.identify_column(event.x)) != "legend":
            return None
        self._begin_edit(item)
        return "break"

    def _column_name(self, column: str) -> str:
        if column in ("#0", ""):
            return "#0"
        try:
            index = int(column[1:]) - 1
        except ValueError:
            return ""
        displayed = self.tree.cget("displaycolumns")
        if displayed in ("", "#all", ("#all",)):
            names = self.BASE_COLUMNS
        else:
            names = tuple(displayed)
        if 0 <= index < len(names):
            return names[index]
        return ""

    # -- inline legend edit ------------------------------------------------
    def _begin_edit(self, item: str) -> None:
        self.cancel_edit()
        try:
            box = self.tree.bbox(item, "legend")
        except tk.TclError:
            return
        if not box:
            return
        x, y, width, height = box
        editor = ttk.Entry(self.tree, style="Panel.TEntry")
        editor.insert(0, self.tree.set(item, "legend"))
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.bind("<Return>", lambda _e: self._commit_edit())
        editor.bind("<Escape>", lambda _e: self.cancel_edit())
        editor.bind("<FocusOut>", lambda _e: self._commit_edit())
        self._editor = editor
        self._editor_item = item

    def _commit_edit(self) -> None:
        editor, item = self._editor, self._editor_item
        if editor is None or item is None:
            return
        value = editor.get()
        self._editor = None
        self._editor_item = None
        try:
            editor.destroy()
        except tk.TclError:
            pass
        key = self._item_keys.get(item)
        if key is not None:
            self._on_rename(key, value)

    def cancel_edit(self) -> None:
        if self._editor is None:
            return
        editor = self._editor
        self._editor = None
        self._editor_item = None
        try:
            editor.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Result table
# ---------------------------------------------------------------------------
class ResultTable(ttk.Frame):
    """Read-only analysis results with TSV copy buttons."""

    def __init__(self, master, columns: Sequence[Tuple[str, str, int]], **kwargs):
        super().__init__(master, style="Panel.TFrame", **kwargs)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._names = tuple(name for name, _label, _width in columns)
        self._headers = tuple(label for _name, label, _width in columns)
        self.tree = ttk.Treeview(
            self, columns=self._names, show="headings", selectmode="extended"
        )
        for name, label, width in columns:
            self.tree.heading(name, text=label)
            self.tree.column(
                name,
                width=theme.scale_int(width),
                minwidth=theme.scale_int(50),
                stretch=False,
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self._item_keys = {}

    @property
    def headers(self) -> Tuple[str, ...]:
        return self._headers

    def populate(self, rows, keys=None) -> None:
        self.tree.delete(*self.tree.get_children(""))
        self._item_keys.clear()
        for index, values in enumerate(rows):
            item = "result_{0}".format(index)
            self.tree.insert("", "end", iid=item, values=tuple(values))
            if keys is not None and index < len(keys):
                self._item_keys[item] = keys[index]

    def key_for_selection(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self._item_keys.get(selection[0])

    def select_key(self, key) -> None:
        for item, candidate in self._item_keys.items():
            if candidate == key:
                if list(self.tree.selection()) != [item]:
                    self.tree.selection_set(item)
                return

    def all_rows(self):
        return [
            self.tree.item(item, "values") for item in self.tree.get_children("")
        ]

    def selected_rows(self):
        return [self.tree.item(item, "values") for item in self.tree.selection()]


# ---------------------------------------------------------------------------
# Field layout
# ---------------------------------------------------------------------------
class FieldGrid(ttk.Frame):
    """Label-above-control grid that wraps after ``MAX_FIELDS_PER_ROW`` (D8/D9)."""

    def __init__(self, master, columns: int = theme.MAX_FIELDS_PER_ROW, **kwargs):
        super().__init__(master, style="Panel.TFrame", **kwargs)
        self._columns = max(1, min(columns, theme.MAX_FIELDS_PER_ROW))
        self._index = 0
        for column in range(self._columns):
            self.columnconfigure(column, weight=1, uniform="field")

    def add(self, label: str, factory: Callable[[tk.Misc], tk.Misc], span: int = 1):
        """Place one labelled control; returns the widget the factory built."""
        span = max(1, min(span, self._columns))
        row = (self._index // self._columns) * 2
        column = self._index % self._columns
        if column + span > self._columns:
            self._index += self._columns - column
            row = (self._index // self._columns) * 2
            column = 0
        ttk.Label(self, text=label, style="PanelSecond.TLabel").grid(
            row=row,
            column=column,
            columnspan=span,
            sticky="w",
            padx=(0, theme.SPACE_S),
            pady=(theme.SPACE_S, 0),
        )
        widget = factory(self)
        widget.grid(
            row=row + 1,
            column=column,
            columnspan=span,
            sticky="ew",
            padx=(0, theme.SPACE_S),
            pady=(2, 0),
        )
        self._index += span
        return widget

    def new_row(self) -> None:
        remainder = self._index % self._columns
        if remainder:
            self._index += self._columns - remainder


class ButtonRow(ttk.Frame):
    """Left-aligned row of buttons with consistent gaps."""

    def __init__(self, master, **kwargs):
        super().__init__(master, style="Panel.TFrame", **kwargs)
        self._count = 0

    def add(self, text: str, command, style: str = "TButton", tooltip: str = ""):
        button = ttk.Button(self, text=text, command=command, style=style)
        button.grid(
            row=0,
            column=self._count,
            padx=(0 if self._count == 0 else theme.SPACE_S, 0),
            sticky="w",
        )
        self._count += 1
        if tooltip:
            Tooltip(button, tooltip)
        return button


def numeric_entry(parent, variable, width: int = 10):
    """Right-sized monospaced entry so digit columns line up."""
    return ttk.Entry(
        parent,
        textvariable=variable,
        width=width,
        style="Num.TEntry",
        justify="right",
    )
