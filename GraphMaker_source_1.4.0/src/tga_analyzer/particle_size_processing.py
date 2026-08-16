from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from typing import Sequence

from .model import PARTICLE_SIZE, CurveData
from .processing import (
    NORMALIZATION_FAILED,
    NORMALIZED,
    RAW,
    OVERRIDE_MODES,
    ProcessingError,
    USE_COMMON,
    USE_INDIVIDUAL,
    USE_NONE,
)


MIN_REFERENCE_VALUE = 1e-6


@dataclass(slots=True)
class ParticleSizeCommonSettings:
    normalization_diameter_um: float | None = None


@dataclass(slots=True)
class ParticleSizeSeriesSettings:
    normalization_mode: str = USE_COMMON
    normalization_diameter_um: float | None = None

    def validate(self) -> None:
        if self.normalization_mode not in OVERRIDE_MODES:
            raise ProcessingError(f"不明な粒度分布規格化設定です: {self.normalization_mode}")


@dataclass(frozen=True, slots=True)
class ParticleSizeProcessedData:
    source: CurveData
    display_x: tuple[float, ...]
    display_y: tuple[float, ...]
    status: str = RAW
    normalization_diameter_um: float | None = None
    reference_value: float | None = None
    warnings: tuple[str, ...] = ()
    normalized_y: tuple[float, ...] | None = None
    normalization_failed: bool = False

    @property
    def source_key(self) -> str:
        return self.source.key

    @property
    def key(self) -> str:
        return self.source.key

    @property
    def measurement_type(self) -> str:
        return self.source.measurement_type

    @property
    def path(self):
        return self.source.path

    @property
    def display_name(self) -> str:
        return self.source.display_name

    @property
    def legend_label(self) -> str:
        return self.source.legend_label

    @property
    def color(self) -> str:
        return self.source.color

    @property
    def point_count(self) -> int:
        return len(self.display_x)

    @property
    def plot_x(self) -> tuple[float, ...]:
        return self.display_x

    @property
    def plot_y(self) -> tuple[float, ...]:
        return self.display_y

    @property
    def is_normalized(self) -> bool:
        return self.status == NORMALIZED

    def header_metadata(self) -> tuple[str, ...]:
        if self.normalization_diameter_um is None:
            return ()
        reference = f"Reference={self.normalization_diameter_um:g} um"
        if self.normalization_failed:
            reference += " (not applied)"
        parts = (reference, f"Status={self.status}")
        if self.warnings:
            parts += (f"Warning={' / '.join(self.warnings)}",)
        return parts


def resolve_particle_normalization_diameter(
    common: ParticleSizeCommonSettings,
    series: ParticleSizeSeriesSettings,
) -> float | None:
    series.validate()
    if series.normalization_mode == USE_NONE:
        return None
    if series.normalization_mode == USE_INDIVIDUAL:
        return series.normalization_diameter_um
    return common.normalization_diameter_um


def particle_reference_value(
    diameters_um: Sequence[float],
    volume_frequency_percent: Sequence[float],
    reference_diameter_um: float,
) -> float:
    if len(diameters_um) != len(volume_frequency_percent) or len(diameters_um) < 2:
        raise ProcessingError("指定粒径の線形補間に必要なデータ点がありません。")
    try:
        target = float(reference_diameter_um)
    except (TypeError, ValueError) as exc:
        raise ProcessingError("指定粒径は数値で入力してください。") from exc
    if not math.isfinite(target):
        raise ProcessingError("指定粒径は有限値で入力してください。")
    if target <= 0:
        raise ProcessingError("指定粒径は0より大きい値を入力してください。")

    xs = tuple(float(value) for value in diameters_um)
    ys = tuple(float(value) for value in volume_frequency_percent)
    if any(not math.isfinite(value) for value in (*xs, *ys)):
        raise ProcessingError("粒径または体積頻度に有限値ではないデータがあります。")
    if any(value <= 0 for value in xs):
        raise ProcessingError("粒径0以下のデータは対数軸で表示できません。")
    if any(xs[index] <= xs[index - 1] for index in range(1, len(xs))):
        raise ProcessingError("粒径は重複のない厳密な昇順である必要があります。")
    if target < xs[0] or target > xs[-1]:
        raise ProcessingError(
            f"指定粒径{target:g} µmはこの系列の粒径範囲外です。"
        )

    index = bisect_left(xs, target)
    tolerance = max(abs(target) * 1e-12, 1e-12)
    if index < len(xs) and math.isclose(xs[index], target, rel_tol=1e-12, abs_tol=tolerance):
        return ys[index]
    if index > 0 and math.isclose(
        xs[index - 1], target, rel_tol=1e-12, abs_tol=tolerance
    ):
        return ys[index - 1]
    if index == 0 or index >= len(xs):
        raise ProcessingError("指定粒径の線形補間に必要なデータ点がありません。")
    d1, d2 = xs[index - 1], xs[index]
    v1, v2 = ys[index - 1], ys[index]
    return float(v1 + (target - d1) / (d2 - d1) * (v2 - v1))


def normalize_particle_size_curve(
    curve: CurveData,
    reference_diameter_um: float,
) -> tuple[tuple[float, ...], float]:
    if curve.measurement_type != PARTICLE_SIZE:
        raise ProcessingError("粒度分布系列だけを指定粒径規格化できます。")
    reference = particle_reference_value(
        curve.particle_diameter_um,
        curve.volume_frequency_percent,
        reference_diameter_um,
    )
    if not math.isfinite(reference):
        raise ProcessingError("規格化基準値が有限値ではないため規格化できません。")
    if reference < 0:
        raise ProcessingError(f"規格化基準値が負のため規格化できません（{reference:.6g}）。")
    if reference <= MIN_REFERENCE_VALUE:
        raise ProcessingError("規格化基準値が1×10⁻⁶以下のため規格化できません。")
    return (
        tuple(value / reference for value in curve.volume_frequency_percent),
        reference,
    )


def process_particle_size_curve(
    curve: CurveData,
    common: ParticleSizeCommonSettings,
    series: ParticleSizeSeriesSettings,
) -> ParticleSizeProcessedData:
    if curve.measurement_type != PARTICLE_SIZE:
        raise ProcessingError("粒度分布系列だけを粒度分布処理できます。")
    series.validate()
    reference_diameter = resolve_particle_normalization_diameter(common, series)
    if series.normalization_mode == USE_INDIVIDUAL and reference_diameter is None:
        return _normalization_failure(curve, None, "指定粒径を入力してください。")
    if reference_diameter is None:
        return raw_particle_size_curve(curve)
    try:
        normalized, reference_value = normalize_particle_size_curve(
            curve, reference_diameter
        )
    except ProcessingError as exc:
        return _normalization_failure(curve, reference_diameter, str(exc))
    return ParticleSizeProcessedData(
        source=curve,
        display_x=curve.particle_diameter_um,
        display_y=normalized,
        status=NORMALIZED,
        normalization_diameter_um=float(reference_diameter),
        reference_value=reference_value,
        normalized_y=normalized,
    )


def raw_particle_size_curve(curve: CurveData) -> ParticleSizeProcessedData:
    return ParticleSizeProcessedData(
        source=curve,
        display_x=curve.particle_diameter_um,
        display_y=curve.volume_frequency_percent,
    )


def _normalization_failure(
    curve: CurveData,
    reference_diameter_um: float | None,
    reason: str,
) -> ParticleSizeProcessedData:
    return ParticleSizeProcessedData(
        source=curve,
        display_x=curve.particle_diameter_um,
        display_y=curve.volume_frequency_percent,
        status=NORMALIZATION_FAILED,
        normalization_diameter_um=(
            None if reference_diameter_um is None else float(reference_diameter_um)
        ),
        warnings=(reason,),
        normalization_failed=True,
    )


def particle_mixed_normalization(
    processed: Sequence[ParticleSizeProcessedData],
) -> bool:
    if not processed:
        return False
    return len({curve.is_normalized for curve in processed}) > 1
