from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence


# Okabe-Ito colors plus two high-contrast additions.  They remain distinct on a
# white background and are commonly suitable for scientific comparison plots.
PAPER_COLOR_PALETTE = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#F0E442",
    "#000000",
    "#7F7F7F",
    "#6A3D9A",
)


def normalize_color(color: str) -> str:
    normalized = color.strip().upper()
    if len(normalized) != 7 or not normalized.startswith("#"):
        raise ValueError("色は#RRGGBB形式で指定してください。")
    try:
        int(normalized[1:], 16)
    except ValueError as exc:
        raise ValueError("色は#RRGGBB形式で指定してください。") from exc
    return normalized


def is_color_column(column_identifier: str) -> bool:
    """Return whether a GraphWindow hit-test points at the color column."""

    return column_identifier == "#1"


def is_legend_column(column_identifier: str) -> bool:
    """Return whether a GraphWindow hit-test points at the legend-name column."""

    return column_identifier == "#3"


@dataclass
class ColorEditSession:
    original: dict[str, str]
    pending: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.original = {
            key: normalize_color(color) for key, color in self.original.items()
        }
        self.pending = dict(self.original)

    def apply(self, keys: Iterable[str], color: str) -> None:
        normalized = normalize_color(color)
        for key in keys:
            if key in self.pending:
                self.pending[key] = normalized

    def changed(self) -> dict[str, str]:
        return {
            key: color
            for key, color in self.pending.items()
            if color != self.original[key]
        }

    def reset(self) -> None:
        self.pending = dict(self.original)


@dataclass
class LegendEditSession:
    order: tuple[str, ...]
    original: dict[str, str]
    pending: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        if set(self.order) != set(self.original):
            raise ValueError("凡例名の系列順と系列キーが一致しません。")
        normalized: dict[str, str] = {}
        for key in self.order:
            value = self.original[key].strip()
            if not value:
                raise ValueError("凡例名は空にできません。")
            normalized[key] = value
        self.original = normalized
        self.pending = dict(normalized)

    def set_name(self, key: str, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("凡例名は空にできません。")
        if key not in self.pending:
            raise KeyError(key)
        self.pending[key] = normalized
        return normalized

    def paste_lines(self, start_index: int, text: str) -> int:
        """Paste consecutive non-empty lines, preserving rows for blank lines."""

        if not self.order:
            return 0
        start = max(0, min(start_index, len(self.order) - 1))
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        last = start
        for offset, line in enumerate(lines):
            index = start + offset
            if index >= len(self.order):
                break
            value = line.strip()
            if value:
                self.pending[self.order[index]] = value
            last = index
        return last

    def changed(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self.pending.items()
            if value != self.original[key]
        }

    def reset(self) -> None:
        self.pending = dict(self.original)


def navigate_legend_index(index: int, count: int, direction: str) -> int:
    if count <= 0:
        return 0
    if direction in {"next", "down", "right"}:
        return min(index + 1, count - 1)
    if direction in {"previous", "up", "left"}:
        return max(index - 1, 0)
    raise ValueError(f"未対応の移動方向です: {direction}")


def colors_from_mapping(values: Mapping[str, str]) -> ColorEditSession:
    return ColorEditSession(dict(values))


def legends_from_rows(rows: Sequence[tuple[str, str]]) -> LegendEditSession:
    return LegendEditSession(
        order=tuple(key for key, _value in rows),
        original={key: value for key, value in rows},
    )
