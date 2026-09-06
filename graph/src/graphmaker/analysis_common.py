from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from typing import Sequence

from .compat import strict_zip


class AnalysisError(ValueError):
    """A numerical analysis could not be completed without guessing."""


@dataclass(frozen=True)
class XRange:
    start: float
    end: float

    def validate(self, label: str = "範囲") -> None:
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise AnalysisError(f"{label}は有限値で指定してください。")
        if self.start >= self.end:
            raise AnalysisError(f"{label}の開始値は終了値より小さくしてください。")

    def contains(self, value: float) -> bool:
        return self.start <= value <= self.end


@dataclass(frozen=True)
class LineFit:
    slope: float
    intercept: float

    def at(self, x_value: float) -> float:
        return self.slope * x_value + self.intercept


def validate_xy(
    x_values: Sequence[float], y_values: Sequence[float], label: str, minimum: int = 2
) -> None:
    if len(x_values) != len(y_values) or len(x_values) < minimum:
        raise AnalysisError(f"{label}に必要なデータ点が不足しています。")
    if any(not math.isfinite(float(value)) for value in tuple(x_values) + tuple(y_values)):
        raise AnalysisError(f"{label}に有限値ではないデータがあります。")
    if any(float(x_values[index]) <= float(x_values[index - 1]) for index in range(1, len(x_values))):
        raise AnalysisError(f"{label}のX値は重複のない昇順にしてください。")


def linear_regression(
    x_values: Sequence[float], y_values: Sequence[float], label: str = "回帰範囲"
) -> LineFit:
    validate_xy(x_values, y_values, label, minimum=2)
    mean_x = sum(float(value) for value in x_values) / len(x_values)
    mean_y = sum(float(value) for value in y_values) / len(y_values)
    denominator = sum((float(value) - mean_x) ** 2 for value in x_values)
    if denominator <= 0:
        raise AnalysisError(f"{label}の回帰を計算できません。")
    numerator = sum(
        (float(x_value) - mean_x) * (float(y_value) - mean_y)
        for x_value, y_value in strict_zip(x_values, y_values, context="analysis_common.linear_regression")
    )
    slope = numerator / denominator
    intercept = mean_y - slope * mean_x
    if not math.isfinite(slope) or not math.isfinite(intercept):
        raise AnalysisError(f"{label}の回帰を計算できません。")
    return LineFit(float(slope), float(intercept))


def interpolate_series(
    x_values: Sequence[float], y_values: Sequence[float], target_x: float, label: str = "補間"
) -> float:
    validate_xy(x_values, y_values, label, minimum=2)
    if not math.isfinite(target_x):
        raise AnalysisError(f"{label}位置は有限値で指定してください。")
    if target_x < x_values[0] or target_x > x_values[-1]:
        raise AnalysisError(
            f"{label}位置 {target_x:g} はデータ範囲 {x_values[0]:g}～{x_values[-1]:g} の外です。"
        )
    index = bisect_left(x_values, target_x)
    if index < len(x_values) and x_values[index] == target_x:
        return float(y_values[index])
    if index == 0 or index == len(x_values):
        raise AnalysisError(f"{label}に必要な前後2点を取得できません。")
    x1, x2 = float(x_values[index - 1]), float(x_values[index])
    y1, y2 = float(y_values[index - 1]), float(y_values[index])
    return y1 + (target_x - x1) / (x2 - x1) * (y2 - y1)


def clipped_points(
    x_values: Sequence[float], y_values: Sequence[float], selected_range: XRange
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    selected_range.validate()
    validate_xy(x_values, y_values, "積分データ", minimum=2)
    if selected_range.start < x_values[0] or selected_range.end > x_values[-1]:
        raise AnalysisError("選択範囲がデータ範囲外です。")
    xs = [selected_range.start]
    ys = [interpolate_series(x_values, y_values, selected_range.start)]
    for x_value, y_value in strict_zip(x_values, y_values, context="analysis_common.clipped_points"):
        if selected_range.start < x_value < selected_range.end:
            xs.append(float(x_value))
            ys.append(float(y_value))
    xs.append(selected_range.end)
    ys.append(interpolate_series(x_values, y_values, selected_range.end))
    return tuple(xs), tuple(ys)


def trapezoid_integral(x_values: Sequence[float], y_values: Sequence[float]) -> float:
    validate_xy(x_values, y_values, "積分範囲", minimum=2)
    total = 0.0
    for index in range(1, len(x_values)):
        total += (x_values[index] - x_values[index - 1]) * (
            y_values[index] + y_values[index - 1]
        ) / 2.0
    return float(total)


def moving_average(values: Sequence[float], window: int) -> tuple[float, ...]:
    if window < 1 or window % 2 == 0:
        raise AnalysisError("平滑化点数は1以上の奇数にしてください。")
    if not values:
        return ()
    radius = window // 2
    output = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        output.append(sum(float(value) for value in values[start:end]) / (end - start))
    return tuple(output)


def line_intersection(first: LineFit, second: LineFit, label: str = "直線交点") -> float:
    denominator = first.slope - second.slope
    if abs(denominator) <= 1e-12:
        raise AnalysisError(f"{label}を計算できません（直線が平行です）。")
    value = (second.intercept - first.intercept) / denominator
    if not math.isfinite(value):
        raise AnalysisError(f"{label}を計算できません。")
    return float(value)
