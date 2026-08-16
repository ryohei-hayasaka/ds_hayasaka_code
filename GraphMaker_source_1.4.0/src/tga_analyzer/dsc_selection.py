from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Literal, Sequence

from .dsc_analysis import DscAnalysisError, DscAnalysisSettings, TemperatureRange


AnalysisType = Literal["tg", "melt"]


ROLE_LABELS: dict[AnalysisType, tuple[str, str, str, str]] = {
    "tg": (
        "Tg前ベースライン開始",
        "Tg前ベースライン終了",
        "Tg後ベースライン開始",
        "Tg後ベースライン終了",
    ),
    "melt": (
        "融解前ベースライン開始",
        "融解前ベースライン終了",
        "融解後ベースライン開始",
        "融解後ベースライン終了",
    ),
}


@dataclass(frozen=True, slots=True)
class FourPointRanges:
    analysis: TemperatureRange
    pre_baseline: TemperatureRange
    search: TemperatureRange
    post_baseline: TemperatureRange


def four_points_to_ranges(
    points: Sequence[float], analysis_type: AnalysisType
) -> FourPointRanges:
    label = "Tg" if analysis_type == "tg" else "融解"
    if len(points) != 4:
        raise DscAnalysisError(f"{label}解析には4点が必要です（現在{len(points)}点）。")
    normalized = tuple(float(value) for value in points)
    if any(not math.isfinite(value) for value in normalized):
        raise DscAnalysisError(f"{label}の4点には有限の温度を指定してください。")
    if any(right <= left for left, right in zip(normalized, normalized[1:])):
        raise DscAnalysisError(
            f"{label}の4点は温度の低い順に、重複しない値で指定してください。"
        )
    first, second, third, fourth = normalized
    return FourPointRanges(
        analysis=TemperatureRange(first, fourth),
        pre_baseline=TemperatureRange(first, second),
        search=TemperatureRange(second, third),
        post_baseline=TemperatureRange(third, fourth),
    )


def ranges_to_four_points(
    analysis: TemperatureRange | None,
    pre_baseline: TemperatureRange | None,
    post_baseline: TemperatureRange | None,
    analysis_type: AnalysisType,
) -> tuple[float, float, float, float]:
    label = "Tg" if analysis_type == "tg" else "融解"
    if analysis is None or pre_baseline is None or post_baseline is None:
        raise DscAnalysisError(f"{label}の解析・前基線・後基線範囲をすべて入力してください。")
    points = (
        pre_baseline.start,
        pre_baseline.end,
        post_baseline.start,
        post_baseline.end,
    )
    converted = four_points_to_ranges(points, analysis_type)
    if not math.isclose(analysis.start, converted.analysis.start, abs_tol=1e-9) or not math.isclose(
        analysis.end, converted.analysis.end, abs_tol=1e-9
    ):
        raise DscAnalysisError(
            f"{label}解析範囲の開始・終了を、前基線開始・後基線終了と一致させてください。"
        )
    return points


def settings_with_four_points(
    settings: DscAnalysisSettings,
    points: Sequence[float],
    analysis_type: AnalysisType,
) -> DscAnalysisSettings:
    converted = four_points_to_ranges(points, analysis_type)
    updated = replace(settings)
    if analysis_type == "tg":
        updated.tg_range = converted.analysis
        updated.tg_pre_range = converted.pre_baseline
        updated.tg_post_range = converted.post_baseline
    else:
        updated.melt_range = converted.analysis
        updated.melt_pre_range = converted.pre_baseline
        updated.melt_post_range = converted.post_baseline
    return updated


@dataclass(slots=True)
class DscFourPointSelection:
    analysis_type: AnalysisType
    curve_key: str
    curve_min_c: float
    curve_max_c: float
    points: list[float] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return len(self.points) == 4

    @property
    def next_instruction(self) -> str:
        if self.complete:
            return "4点を選択しました。範囲を確認し、必要なら数値を微調整して［算出］を押してください。"
        role = ROLE_LABELS[self.analysis_type][len(self.points)]
        return f"{role}を選択してください（{len(self.points) + 1}/4）"

    def add_temperature(self, temperature: float) -> None:
        label = "Tg" if self.analysis_type == "tg" else "融解"
        if self.complete:
            raise DscAnalysisError(
                f"{label}の4点は選択済みです。［1点戻す］または［{label}解析］で再選択してください。"
            )
        value = float(temperature)
        if not math.isfinite(value):
            raise DscAnalysisError("クリック位置を温度へ変換できません。")
        if value < self.curve_min_c or value > self.curve_max_c:
            raise DscAnalysisError(
                f"選択温度 {value:.2f} ℃ は対象曲線の温度範囲 "
                f"{self.curve_min_c:.2f}～{self.curve_max_c:.2f} ℃ 外です。"
            )
        if self.points and value <= self.points[-1]:
            raise DscAnalysisError("4点は温度の低い側から、重複しない位置を選択してください。")
        self.points.append(value)

    def undo(self) -> float | None:
        return self.points.pop() if self.points else None

    def validate(self) -> FourPointRanges:
        ranges = four_points_to_ranges(self.points, self.analysis_type)
        if ranges.analysis.start < self.curve_min_c or ranges.analysis.end > self.curve_max_c:
            raise DscAnalysisError(
                f"選択範囲を対象曲線の温度範囲 "
                f"{self.curve_min_c:.2f}～{self.curve_max_c:.2f} ℃ 内に設定してください。"
            )
        return ranges
