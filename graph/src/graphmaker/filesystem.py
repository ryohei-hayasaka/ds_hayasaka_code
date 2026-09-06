from __future__ import annotations

from typing import Iterable, Union

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


def find_folders_by_keyword(
    root: Union[Path, str], keywords: Iterable[str], *, max_results: int = 500
) -> list[Path]:
    """Recursively find subdirectories under root whose name contains any keyword.

    Matching is a case-insensitive substring test against the directory's own
    name (not its full path). Unreadable directories are skipped rather than
    raising. The root itself is never returned, only its descendants.
    """
    root_path = Path(root)
    needles = [keyword.casefold() for keyword in keywords if keyword]
    if not needles:
        return []
    matches: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root_path, onerror=lambda _exc: None):
        for name in dirnames:
            folded = name.casefold()
            if any(needle in folded for needle in needles):
                matches.append(Path(dirpath) / name)
        if len(matches) >= max_results:
            break
    matches.sort(key=lambda path: natural_sort_key(str(path)))
    return matches[:max_results]
