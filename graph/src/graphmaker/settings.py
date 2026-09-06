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


def load_mode_roots(settings_path: Union[Path, None] = None) -> dict[str, Path]:
    """Load accessible mode-specific roots, with the legacy root as fallback."""
    path = settings_path or default_settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return {}
    roots = {}
    raw_roots = payload.get("mode_roots", {})
    if isinstance(raw_roots, dict):
        for mode, value in raw_roots.items():
            try:
                root = Path(value)
            except TypeError:
                continue
            if isinstance(mode, str) and root.is_dir():
                roots[mode] = root
    legacy = payload.get("last_root")
    if legacy and "__legacy__" not in roots:
        try:
            legacy_root = Path(legacy)
        except TypeError:
            legacy_root = None
        if legacy_root is not None and legacy_root.is_dir():
            roots["__legacy__"] = legacy_root
    return roots


def save_last_root(root: Union[Path, str], settings_path: Union[Path, None] = None) -> None:
    path = settings_path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"last_root": str(Path(root))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def save_mode_root(
    measurement_type: str,
    root: Union[Path, str],
    settings_path: Union[Path, None] = None,
) -> None:
    """Persist one mode root without discarding roots saved for other modes."""
    path = settings_path or default_settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
    except (FileNotFoundError, TypeError, ValueError, OSError, json.JSONDecodeError):
        payload = {}
    mode_roots = payload.get("mode_roots")
    if not isinstance(mode_roots, dict):
        mode_roots = {}
    normalized_root = str(Path(root))
    mode_roots[str(measurement_type)] = normalized_root
    payload["mode_roots"] = mode_roots
    payload["last_root"] = normalized_root
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def load_mode_keywords(settings_path: Union[Path, None] = None) -> dict[str, str]:
    """Load per-mode folder-search keywords (free text, may contain multiple words)."""
    path = settings_path or default_settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return {}
    raw_keywords = payload.get("mode_keywords", {})
    if not isinstance(raw_keywords, dict):
        return {}
    return {
        mode: value
        for mode, value in raw_keywords.items()
        if isinstance(mode, str) and isinstance(value, str)
    }


def save_mode_keyword(
    measurement_type: str,
    keyword: str,
    settings_path: Union[Path, None] = None,
) -> None:
    """Persist one mode's folder-search keyword without discarding other settings."""
    path = settings_path or default_settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
    except (FileNotFoundError, TypeError, ValueError, OSError, json.JSONDecodeError):
        payload = {}
    mode_keywords = payload.get("mode_keywords")
    if not isinstance(mode_keywords, dict):
        mode_keywords = {}
    mode_keywords[str(measurement_type)] = str(keyword)
    payload["mode_keywords"] = mode_keywords
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)
