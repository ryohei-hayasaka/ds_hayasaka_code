from __future__ import annotations

from typing import Union

import json
import os
from pathlib import Path

from .branding import APP_DATA_DIR_NAME

APP_NAME = APP_DATA_DIR_NAME


def default_settings_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_NAME / "settings.json"


def load_last_root(settings_path: Union[Path, None] = None) -> Union[Path, None]:
    path = settings_path or default_settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        root = Path(payload["last_root"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None
    return root if root.is_dir() else None


def save_last_root(root: Union[Path, str], settings_path: Union[Path, None] = None) -> None:
    path = settings_path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"last_root": str(Path(root))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)
