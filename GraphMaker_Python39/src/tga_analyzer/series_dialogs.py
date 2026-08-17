from __future__ import annotations

from typing import Union

import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import colorchooser, messagebox, ttk

from .model import CurveData
from .series_edit import (
    PAPER_COLOR_PALETTE,
    ColorEditSession,
    LegendEditSession,
    navigate_legend_index,
)


def _restore_parent_focus(window: tk.Toplevel, parent: tk.Toplevel) -> None:
    try:
        window.grab_release()
    except tk.TclError:
        pass
    window.destroy()
    try:
        parent.after_idle(lambda: (parent.lift(), parent.focus_set()))
    except tk.TclError:
        pass


class _ModalEditor(tk.Toplevel):
    def __init__(self, parent: tk.Toplevel, title: str) -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.title(title)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", self.cancel)

    def show(self) -> None:
        self.update_idletasks()
        self.lift()
        self.focus_set()
        self.grab_set()

    def close(self) -> None:
        _restore_parent_focus(self, self.parent_window)

    def cancel(self, _event=None):
        self.close()
        return "break"


class ColorEditorDialog(_ModalEditor):
    COLUMNS = ("selected", "current", "pending", "legend", "name")

    def __init__(
        self,
        parent: tk.Toplevel,
        curves: Sequence[CurveData],
        on_commit: Callable[[dict[str, str]], None],
    ) -> None:
        super().__init__(parent, "系列色をまとめて編集")
        self.geometry("880x520")
        self.minsize(720, 430)
        self.on_commit = on_commit
        self.curves = tuple(curves)
        self.item_keys = {
            f"color_{index}": curve.key for index, curve in enumerate(self.curves)
        }
        self.session = ColorEditSession(
            {curve.key: curve.color for curve in self.curves}
        )
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_ui()
        self._refresh_rows()
        if self.tree.get_children(""):
            first = self.tree.get_children("")[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
        self.show()

    def _build_ui(self) -> None:
        ttk.Label(
            self,
            text="系列を複数選択し、下のプリセット色または［その他の色］を適用します。",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))

        table_frame = ttk.Frame(self)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=10)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            columns=self.COLUMNS,
            show="headings",
            selectmode="extended",
            height=10,
        )
        headings = {
            "selected": ("選択", 55, False),
            "current": ("現在色", 105, False),
            "pending": ("変更後", 105, False),
            "legend": ("凡例名", 220, True),
            "name": ("系列名", 220, True),
        }
        for column, (label, width, stretch) in headings.items():
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, stretch=stretch, anchor=tk.W)
        scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_changed)
        self.tree.bind("<Double-1>", self._on_double_click)

        palette = ttk.LabelFrame(self, text="論文向けプリセット", padding=8)
        palette.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 0))
        for index, color in enumerate(PAPER_COLOR_PALETTE):
            button = tk.Button(
                palette,
                text=color,
                background=color,
                foreground="#FFFFFF" if color not in {"#F0E442"} else "#000000",
                activebackground=color,
                activeforeground="#FFFFFF" if color not in {"#F0E442"} else "#000000",
                width=9,
                relief=tk.RAISED,
                command=lambda selected=color: self._apply_to_selection(selected),
            )
            button.grid(row=index // 5, column=index % 5, padx=3, pady=3)
        ttk.Button(
            palette, text="その他の色...", command=self._choose_for_selection
        ).grid(row=0, column=5, rowspan=2, padx=(12, 3), pady=3, sticky="ns")
        selection_actions = ttk.Frame(palette)
        selection_actions.grid(row=0, column=6, rowspan=2, padx=(8, 0), sticky="ns")
        ttk.Button(
            selection_actions,
            text="全系列を選択",
            command=lambda: self.tree.selection_set(self.tree.get_children("")),
        ).pack(fill=tk.X)
        ttk.Button(
            selection_actions,
            text="選択解除",
            command=lambda: self.tree.selection_remove(self.tree.selection()),
        ).pack(fill=tk.X, pady=(4, 0))

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="e", padx=10, pady=10)
        ttk.Button(actions, text="OK", command=self._ok).pack(side=tk.LEFT)
        ttk.Button(actions, text="キャンセル", command=self.cancel).pack(
            side=tk.LEFT, padx=(6, 0)
        )

    def _refresh_rows(self) -> None:
        selected = set(self.tree.selection()) if hasattr(self, "tree") else set()
        self.tree.delete(*self.tree.get_children(""))
        for index, curve in enumerate(self.curves):
            item = f"color_{index}"
            pending = self.session.pending[curve.key]
            tag = f"pending_{index}"
            self.tree.tag_configure(tag, foreground=pending)
            self.tree.insert(
                "",
                tk.END,
                iid=item,
                values=(
                    "●" if item in selected else "",
                    f"■ {self.session.original[curve.key]}",
                    f"■ {pending}",
                    curve.legend_label,
                    curve.display_name,
                ),
                tags=(tag,),
            )
        if selected:
            existing = [item for item in selected if self.tree.exists(item)]
            self.tree.selection_set(existing)

    def _on_selection_changed(self, _event=None) -> None:
        selected = set(self.tree.selection())
        for item in self.tree.get_children(""):
            values = list(self.tree.item(item, "values"))
            values[0] = "●" if item in selected else ""
            self.tree.item(item, values=values)

    def _selected_keys(self) -> list[str]:
        return [
            self.item_keys[item]
            for item in self.tree.selection()
            if item in self.item_keys
        ]

    def _apply_to_selection(self, color: str) -> None:
        keys = self._selected_keys()
        if not keys:
            messagebox.showinfo(
                "系列を選択", "色を適用する系列を選択してください。", parent=self
            )
            return
        self.session.apply(keys, color)
        self._refresh_rows()

    def _ask_color(self, current: str) -> Union[str, None]:
        _rgb, color = colorchooser.askcolor(
            color=current,
            title="系列色を選択",
            parent=self,
        )
        self.lift()
        self.focus_set()
        return color

    def _choose_for_selection(self) -> None:
        keys = self._selected_keys()
        if not keys:
            messagebox.showinfo(
                "系列を選択", "色を適用する系列を選択してください。", parent=self
            )
            return
        color = self._ask_color(self.session.pending[keys[0]])
        if color:
            self.session.apply(keys, color)
            self._refresh_rows()

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or column not in {"#2", "#3"}:
            return None
        key = self.item_keys[item]
        self.tree.selection_set(item)
        self.tree.focus(item)
        color = self._ask_color(self.session.pending[key])
        if color:
            self.session.apply((key,), color)
            self._refresh_rows()
        return "break"

    def _ok(self) -> None:
        changes = self.session.changed()
        if changes:
            self.on_commit(changes)
        self.close()


class LegendEditorDialog(_ModalEditor):
    COLUMNS = ("color", "name", "legend", "source")

    def __init__(
        self,
        parent: tk.Toplevel,
        curves: Sequence[CurveData],
        on_commit: Callable[[dict[str, str]], None],
    ) -> None:
        super().__init__(parent, "凡例名をまとめて編集")
        self.geometry("980x520")
        self.minsize(760, 420)
        self.on_commit = on_commit
        self.curves = tuple(curves)
        self.items = tuple(f"legend_{index}" for index in range(len(self.curves)))
        self.item_keys = {
            item: curve.key for item, curve in zip(self.items, self.curves)
        }
        self.session = LegendEditSession(
            order=tuple(curve.key for curve in self.curves),
            original={curve.key: curve.legend_label for curve in self.curves},
        )
        self.editor: Union[ttk.Entry, None] = None
        self.edit_item: Union[str, None] = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_ui()
        self._refresh_rows()
        if self.items:
            self._select_item(self.items[0])
        self.show()

    def _build_ui(self) -> None:
        ttk.Label(
            self,
            text="F2またはダブルクリックで編集。Enter／Tabで次、Shift付きで前へ移動します。",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))

        table_frame = ttk.Frame(self)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=10)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            columns=self.COLUMNS,
            show="headings",
            selectmode="browse",
            height=12,
        )
        headings = {
            "color": ("現在色", 105, False),
            "name": ("系列名", 230, True),
            "legend": ("凡例名（編集可能）", 260, True),
            "source": ("元ファイル名", 260, True),
        }
        for column, (label, width, stretch) in headings.items():
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, stretch=stretch, anchor=tk.W)
        scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<F2>", self.begin_edit)
        self.tree.bind("<Return>", self.begin_edit)
        self.tree.bind("<Up>", lambda event: self._tree_move(event, "up"))
        self.tree.bind("<Down>", lambda event: self._tree_move(event, "down"))
        self.tree.bind("<Left>", lambda event: self._tree_move(event, "left"))
        self.tree.bind("<Right>", lambda event: self._tree_move(event, "right"))
        self.tree.bind("<Tab>", lambda event: self._tree_move(event, "next"))
        self.tree.bind("<Shift-Tab>", lambda event: self._tree_move(event, "previous"))

        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, sticky="e", padx=10, pady=10)
        ttk.Button(actions, text="OK", command=self._ok).pack(side=tk.LEFT)
        ttk.Button(actions, text="キャンセル", command=self.cancel).pack(
            side=tk.LEFT, padx=(6, 0)
        )

    def _refresh_rows(self) -> None:
        current = self.tree.focus() if hasattr(self, "tree") else ""
        self.tree.delete(*self.tree.get_children(""))
        for item, curve in zip(self.items, self.curves):
            tag = f"legend_color_{item}"
            self.tree.tag_configure(tag, foreground=curve.color)
            self.tree.insert(
                "",
                tk.END,
                iid=item,
                values=(
                    f"■ {curve.color.upper()}",
                    curve.display_name,
                    self.session.pending[curve.key],
                    curve.path.name,
                ),
                tags=(tag,),
            )
        if current and self.tree.exists(current):
            self._select_item(current)

    def _select_item(self, item: str) -> None:
        if not item or not self.tree.exists(item):
            return
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)

    def _index_for_item(self, item: str) -> int:
        return self.items.index(item)

    def _move_item(self, item: str, direction: str) -> str:
        index = navigate_legend_index(
            self._index_for_item(item), len(self.items), direction
        )
        target = self.items[index]
        self._select_item(target)
        return target

    def _tree_move(self, _event, direction: str):
        item = self.tree.focus() or (self.items[0] if self.items else "")
        if item:
            self._move_item(item, direction)
        return "break"

    def _on_double_click(self, event):
        if self.tree.identify_column(event.x) != "#3":
            return None
        item = self.tree.identify_row(event.y)
        if not item:
            return None
        self._select_item(item)
        return self.begin_edit()

    def begin_edit(self, _event=None):
        if self.editor is not None and not self._commit_editor():
            return "break"
        item = self.tree.focus()
        if not item:
            return "break"
        bbox = self.tree.bbox(item, "legend")
        if not bbox:
            return "break"
        x, y, width, height = bbox
        editor = ttk.Entry(self.tree)
        editor.insert(0, self.session.pending[self.item_keys[item]])
        editor.select_range(0, tk.END)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        self.editor = editor
        self.edit_item = item
        editor.bind("<Return>", lambda event: self._commit_and_move(event, "down"))
        editor.bind(
            "<Shift-Return>", lambda event: self._commit_and_move(event, "up")
        )
        editor.bind("<Up>", lambda event: self._commit_and_move(event, "up"))
        editor.bind("<Down>", lambda event: self._commit_and_move(event, "down"))
        editor.bind("<Tab>", lambda event: self._commit_and_move(event, "next"))
        editor.bind(
            "<Shift-Tab>", lambda event: self._commit_and_move(event, "previous")
        )
        editor.bind("<Escape>", self._cancel_editor)
        editor.bind("<<Paste>>", self._paste)
        return "break"

    def _commit_editor(self) -> bool:
        editor = self.editor
        item = self.edit_item
        if editor is None or item is None:
            return True
        try:
            self.session.set_name(self.item_keys[item], editor.get())
        except ValueError as exc:
            messagebox.showwarning("凡例名", str(exc), parent=self)
            editor.focus_set()
            return False
        self.editor = None
        self.edit_item = None
        editor.destroy()
        self._refresh_rows()
        self._select_item(item)
        return True

    def _commit_and_move(self, _event, direction: str):
        item = self.edit_item
        if item is None or not self._commit_editor():
            return "break"
        target = self._move_item(item, direction)
        self._select_item(target)
        self.begin_edit()
        return "break"

    def _cancel_editor(self, _event=None):
        editor = self.editor
        item = self.edit_item
        self.editor = None
        self.edit_item = None
        if editor is not None:
            editor.destroy()
        if item:
            self._select_item(item)
        self.tree.focus_set()
        return "break"

    def _paste(self, _event=None):
        editor = self.editor
        item = self.edit_item
        if editor is None or item is None:
            return "break"
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"
        if "\n" not in text and "\r" not in text:
            try:
                if editor.selection_present():
                    editor.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            editor.insert(tk.INSERT, text)
            return "break"
        start_index = self._index_for_item(item)
        last_index = self.session.paste_lines(start_index, text)
        self.editor = None
        self.edit_item = None
        editor.destroy()
        self._refresh_rows()
        target = self.items[last_index]
        self._select_item(target)
        self.tree.focus_set()
        return "break"

    def _ok(self) -> None:
        if not self._commit_editor():
            return
        changes = self.session.changed()
        if changes:
            self.on_commit(changes)
        self.close()

    def cancel(self, _event=None):
        self._cancel_editor()
        return super().cancel()
