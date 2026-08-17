from __future__ import annotations

from typing import Union

import os
import re
from pathlib import Path


_NATURAL_PART = re.compile(r"(\d+)")


def natural_sort_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in _NATURAL_PART.split(value))


def list_csv_names(folder: Union[Path, str]) -> list[str]:
    """Return direct-child CSV names without opening any file contents."""
    folder_path = Path(folder)
    names: list[str] = []
    with os.scandir(folder_path) as entries:
        for entry in entries:
            if entry.name.lower().endswith(".csv") and entry.is_file(follow_symlinks=False):
                names.append(entry.name)
    names.sort(key=natural_sort_key)
    return names


def list_child_directories(folder: Union[Path, str]) -> list[Path]:
    folder_path = Path(folder)
    directories: list[Path] = []
    with os.scandir(folder_path) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                directories.append(Path(entry.path))
    directories.sort(key=lambda path: natural_sort_key(path.name))
    return directories


def has_child_directories(folder: Union[Path, str]) -> bool:
    folder_path = Path(folder)
    try:
        with os.scandir(folder_path) as entries:
            return any(entry.is_dir(follow_symlinks=False) for entry in entries)
    except (FileNotFoundError, PermissionError, OSError):
        return False
