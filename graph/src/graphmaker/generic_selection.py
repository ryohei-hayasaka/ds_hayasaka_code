from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .analysis_common import AnalysisError, XRange


@dataclass
class XPointSelection:
    curve_key: str
    roles: tuple[str, ...]
    curve_min: float
    curve_max: float
    points: list[float] = field(default_factory=list)
    title: str = "範囲"

    @property
    def complete(self) -> bool:
        return len(self.points) == len(self.roles)

    @property
    def next_instruction(self) -> str:
        if self.complete:
            return f"{self.title}の選択が完了しました。算出前に範囲を確認してください。"
        return f"{self.roles[len(self.points)]}を選択してください（{len(self.points) + 1}/{len(self.roles)}）"

    def add_x(self, value: float) -> None:
        if self.complete:
            raise AnalysisError("選択点はすでに揃っています。再選択してください。")
        if not math.isfinite(value):
            raise AnalysisError("選択位置は有限値で指定してください。")
        if value < self.curve_min or value > self.curve_max:
            raise AnalysisError("選択位置が曲線の範囲外です。")
        if self.points and value <= self.points[-1]:
            raise AnalysisError("選択点はX値の小さい順に、重複なく指定してください。")
        self.points.append(float(value))

    def undo(self) -> None:
        if self.points:
            self.points.pop()

    def clear(self) -> None:
        self.points.clear()

    def validate(self) -> None:
        if not self.complete:
            raise AnalysisError(f"{len(self.roles)}点が揃っていません。")
        if any(self.points[index] <= self.points[index - 1] for index in range(1, len(self.points))):
            raise AnalysisError("選択点はX値の小さい順に、重複なく指定してください。")
        if self.points[0] < self.curve_min or self.points[-1] > self.curve_max:
            raise AnalysisError("選択位置が曲線の範囲外です。")

    def set_points(self, values: Sequence[float]) -> None:
        self.points = [float(value) for value in values]
        if len(self.points) > len(self.roles):
            raise AnalysisError("選択点が多すぎます。")
        if self.complete:
            self.validate()

    def analysis_range(self) -> XRange:
        self.validate()
        return XRange(self.points[0], self.points[-1])

    def bands(self) -> tuple[XRange, ...]:
        if len(self.points) == 2 and len(self.roles) == 2:
            return (XRange(self.points[0], self.points[1]),)
        if len(self.points) == 4:
            return (
                XRange(self.points[0], self.points[1]),
                XRange(self.points[1], self.points[2]),
                XRange(self.points[2], self.points[3]),
            )
        return ()
