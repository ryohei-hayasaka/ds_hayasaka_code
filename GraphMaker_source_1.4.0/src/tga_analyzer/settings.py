from __future__ import annotations

import json
import os
from pathlib import Path

from .branding import APP_DATA_DIR_NAME, LEGACY_APP_DATA_DIR_NAME

APP_NAME = APP_DATA_DIR_NAME


def default_settings_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_NAME / "settings.json"


def legacy_settings_path() -> Path:
    """Return the settings location used before the GraphMaker rename."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / LEGACY_APP_DATA_DIR_NAME / "settings.json"


def load_last_root(settings_path: Path | None = None) -> Path | None:
    paths = (settings_path,) if settings_path is not None else (
        default_settings_path(),
        legacy_settings_path(),
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            root = Path(payload["last_root"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            continue
        if root.is_dir():
            return root
    return None


def save_last_root(root: Path | str, settings_path: Path | None = None) -> None:
    path = settings_path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"last_root": str(Path(root))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)
