"""Double-click source launcher for GraphMaker on Windows.

The launcher runs ``src/tga_analyzer`` with the installed Python interpreter.
It does not use or bundle a PyInstaller executable.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _show_error(title: str, message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message, parent=root)
    root.destroy()


def main() -> None:
    if sys.version_info < (3, 12):
        _show_error(
            "Pythonバージョンエラー",
            "GraphMakerの実行にはPython 3.12以上が必要です。\n"
            f"現在のバージョン: {sys.version.split()[0]}",
        )
        return

    try:
        import openpyxl  # noqa: F401
    except ImportError:
        _show_error(
            "ライブラリ不足",
            "openpyxlがインストールされていません。\n\n"
            "承認済みの方法でrequirements.txtの依存関係を導入してください。\n"
            "必要なバージョン: openpyxl==3.1.5",
        )
        return

    try:
        from tga_analyzer.gui import main as run_app
    except ImportError as exc:
        _show_error(
            "起動エラー",
            "GraphMakerのソースを読み込めませんでした。\n"
            f"srcフォルダの配置を確認してください。\n\n{exc}",
        )
        return

    run_app()


if __name__ == "__main__":
    main()

