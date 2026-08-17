from __future__ import annotations

from .compat import strict_zip

from typing import Union

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .model import DSC, IR, CurveData


USE_COMMON = "common"
USE_NONE = "none"
USE_INDIVIDUAL = "individual"
OVERRIDE_MODES = (USE_COMMON, USE_NONE, USE_INDIVIDUAL)

RAW = "Raw"
BLANK_CORRECTED = "Blank corrected"
NORMALIZED = "Normalized"
BLANK_FAILED = "Blank failed / Raw displayed"
NORMALIZATION_FAILED = "Normalization failed / Unnormalized displayed"


class ProcessingError(ValueError):
    pass


@dataclass
class CommonProcessingSettings:
    blank_key: Union[str, None] = None
    normalization_wavenumber: Union[float, None] = None


@dataclass
class SeriesProcessingSettings:
    blank_mode: str = USE_COMMON
    blank_key: Union[str, None] = None
    normalization_mode: str = USE_COMMON
    normalization_wavenumber: Union[float, None] = None

    def validate(self) -> None:
        if self.blank_mode not in OVERRIDE_MODES:
            raise ProcessingError(f"不明なブランク設定です: {self.blank_mode}")
        if self.normalization_mode not in OVERRIDE_MODES:
            raise ProcessingError(f"不明な規格化設定です: {self.normalization_mode}")


@dataclass(frozen=True)
class ProcessedCurveData:
    source: CurveData
    display_x: tuple[float, ...]
    display_y: tuple[float, ...]
    status: str = RAW
    blank_name: Union[str, None] = None
    normalization_wavenumber: Union[float, None] = None
    warnings: tuple[str, ...] = ()
    blank_corrected_x: Union[tuple[float, ...], None] = None
    blank_corrected_y: Union[tuple[float, ...], None] = None
    normalized_y: Union[tuple[float, ...], None] = None
    blank_failed: bool = False
    normalization_failed: bool = False

    @property
    def source_key(self) -> str:
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
    def temperatures(self) -> tuple[float, ...]:
        return self.display_x if self.measurement_type == DSC else self.source.temperatures

    @property
    def wavenumbers_cm1(self) -> tuple[float, ...]:
        return self.display_x if self.measurement_type == IR else ()

    @property
    def heat_flow_mw(self) -> tuple[float, ...]:
        return self.display_y if self.measurement_type == DSC else ()

    @property
    def absorbance(self) -> tuple[float, ...]:
        return self.display_y if self.measurement_type == IR else ()

    @property
    def heat_flow_unit(self) -> Union[str, None]:
        return self.source.heat_flow_unit

    @property
    def source_heat_flow_header(self) -> Union[str, None]:
        return self.source.source_heat_flow_header

    @property
    def time_min(self) -> tuple[float, ...]:
        if self.measurement_type != DSC or not self.source.time_min:
            return ()
        return _values_matching_x(self.source.plot_x, self.source.time_min, self.display_x)

    @property
    def is_normalized(self) -> bool:
        return self.status == NORMALIZED

    def as_curve(self) -> CurveData:
        """Create a disposable curve for the existing DSC analysis routines."""
        if self.measurement_type != DSC:
            raise ProcessingError("DSC処理済み曲線だけを解析用CurveDataへ変換できます。")
        return CurveData(
            path=self.source.path,
            display_name=self.source.display_name,
            temperatures=self.display_x,
            mass_mg=(),
            weight_percent=(),
            color=self.source.color,
            measurement_type=DSC,
            heat_flow_mw=self.display_y,
            time_min=self.time_min,
            heat_flow_unit=self.source.heat_flow_unit,
            source_heat_flow_header=self.source.source_heat_flow_header,
            legend_name=self.source.legend_label,
        )

    def header_metadata(self) -> tuple[str, ...]:
        blank = "Failed" if self.blank_failed else (self.blank_name or "None")
        if self.normalization_failed:
            norm = (
                f"{self.normalization_wavenumber:g} cm-1 (not applied)"
                if self.normalization_wavenumber is not None
                else "Failed"
            )
        elif self.normalization_wavenumber is None:
            norm = "None"
        else:
            norm = f"{self.normalization_wavenumber:g} cm-1"
        parts = (f"Blank={blank}", f"Norm={norm}", f"Status={self.status}")
        if self.warnings:
            parts += (f"Warning={' / '.join(self.warnings)}",)
        return parts


def raw_processed_curve(curve: CurveData) -> ProcessedCurveData:
    return ProcessedCurveData(curve, curve.plot_x, curve.plot_y)


def resolve_effective_blank(
    common_setting: CommonProcessingSettings,
    series_override: SeriesProcessingSettings,
) -> Union[str, None]:
    series_override.validate()
    if series_override.blank_mode == USE_NONE:
        return None
    if series_override.blank_mode == USE_INDIVIDUAL:
        return series_override.blank_key
    return common_setting.blank_key


def resolve_effective_normalization(
    common_setting: CommonProcessingSettings,
    series_override: SeriesProcessingSettings,
) -> Union[float, None]:
    series_override.validate()
    if series_override.normalization_mode == USE_NONE:
        return None
    if series_override.normalization_mode == USE_INDIVIDUAL:
        return series_override.normalization_wavenumber
    return common_setting.normalization_wavenumber


def blank_reference_name(
    blank_key: Union[str, None],
    available_curves: Mapping[str, CurveData],
) -> Union[str, None]:
    """Return a stable user-facing name even after a configured blank is removed."""
    if blank_key is None:
        return None
    blank = available_curves.get(blank_key)
    if blank is not None:
        return blank.display_name
    key_text = str(blank_key).strip()
    if not key_text:
        return None
    return Path(key_text).name.strip() or key_text


def calculate_overlap(sample_x: Sequence[float], blank_x: Sequence[float]) -> tuple[float, float]:
    _validate_xy(sample_x, sample_x, "試料")
    _validate_xy(blank_x, blank_x, "ブランク")
    low = max(min(sample_x), min(blank_x))
    high = min(max(sample_x), max(blank_x))
    if low >= high:
        raise ProcessingError("試料とブランクに有効な重複範囲がありません。")
    return float(low), float(high)


def interpolate_value(
    x_values: Sequence[float], y_values: Sequence[float], target_x: float
) -> float:
    _validate_xy(x_values, y_values, "補間元")
    if not math.isfinite(target_x):
        raise ProcessingError("補間位置は有限値にしてください。")
    xs, ys = _sorted_unique_xy(x_values, y_values)
    return _interpolate_sorted(xs, ys, target_x)


def _interpolate_sorted(xs: Sequence[float], ys: Sequence[float], target_x: float) -> float:
    if target_x < xs[0] or target_x > xs[-1]:
        raise ProcessingError(
            f"指定位置 {target_x:g} はデータ範囲 {xs[0]:g}～{xs[-1]:g} の外です。"
        )
    index = bisect_left(xs, target_x)
    if index < len(xs) and xs[index] == target_x:
        return float(ys[index])
    if index == 0 or index == len(xs):
        raise ProcessingError("補間に必要な前後2点を取得できません。")
    x1, x2 = xs[index - 1], xs[index]
    y1, y2 = ys[index - 1], ys[index]
    return float(y1 + (target_x - x1) / (x2 - x1) * (y2 - y1))


def subtract_ir_blank(sample: CurveData, blank: CurveData) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if sample.measurement_type != IR or blank.measurement_type != IR:
        raise ProcessingError("IRブランク補正にはIR系列を指定してください。")
    return _subtract_with_interpolation(
        sample.wavenumbers_cm1, sample.absorbance, blank.wavenumbers_cm1, blank.absorbance
    )


def subtract_dsc_blank(sample: CurveData, blank: CurveData) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if sample.measurement_type != DSC or blank.measurement_type != DSC:
        raise ProcessingError("DSCブランク補正にはDSC系列を指定してください。")
    if sample.heat_flow_unit != blank.heat_flow_unit:
        raise ProcessingError(
            f"DSCの熱流単位が一致しません（試料={sample.heat_flow_unit or '不明'}, "
            f"ブランク={blank.heat_flow_unit or '不明'}）。"
        )
    if sample.heat_flow_unit != "mW":
        raise ProcessingError("DSCブランク補正は試料・ブランクとも総熱流mWの場合だけ実行できます。")
    sample_direction = _direction(sample.temperatures)
    blank_direction = _direction(blank.temperatures)
    if sample_direction == 0 or blank_direction == 0:
        raise ProcessingError("DSCの昇温／冷却方向を判定できません。")
    if sample_direction != blank_direction:
        raise ProcessingError("DSC試料とブランクの昇温／冷却方向が一致しません。")
    return _subtract_with_interpolation(
        sample.temperatures, sample.heat_flow_mw, blank.temperatures, blank.heat_flow_mw
    )


def normalize_ir_curve(
    curve_or_x: Union[CurveData, ProcessedCurveData, Sequence[float]],
    normalization_wavenumber: float,
    y_values: Union[Sequence[float], None] = None,
) -> tuple[float, ...]:
    if isinstance(curve_or_x, CurveData):
        if curve_or_x.measurement_type != IR:
            raise ProcessingError("IR系列だけを規格化できます。")
        x_values = curve_or_x.wavenumbers_cm1
        values = curve_or_x.absorbance
    elif isinstance(curve_or_x, ProcessedCurveData):
        if curve_or_x.measurement_type != IR:
            raise ProcessingError("IR系列だけを規格化できます。")
        x_values = curve_or_x.display_x
        values = curve_or_x.display_y
    else:
        x_values = tuple(curve_or_x)
        if y_values is None:
            raise ProcessingError("規格化する吸光度データがありません。")
        values = tuple(y_values)
    reference = interpolate_value(x_values, values, normalization_wavenumber)
    if reference < 0:
        raise ProcessingError(f"規格化基準吸光度が負です（{reference:.6g}）。")
    if reference < 0.001:
        raise ProcessingError(
            f"規格化基準吸光度が0.001未満です（{reference:.6g}）。"
        )
    return tuple(value / reference for value in values)


def process_ir_curve(
    curve: CurveData,
    common_setting: CommonProcessingSettings,
    series_setting: SeriesProcessingSettings,
    available_curves: Mapping[str, CurveData],
    all_series_settings: Union[Mapping[str, SeriesProcessingSettings], None] = None,
) -> ProcessedCurveData:
    if curve.measurement_type != IR:
        raise ProcessingError("IR系列だけをIR処理できます。")
    return _process_curve(
        curve, common_setting, series_setting, available_curves, all_series_settings, IR
    )


def process_dsc_curve(
    curve: CurveData,
    common_setting: CommonProcessingSettings,
    series_setting: SeriesProcessingSettings,
    available_curves: Mapping[str, CurveData],
    all_series_settings: Union[Mapping[str, SeriesProcessingSettings], None] = None,
) -> ProcessedCurveData:
    if curve.measurement_type != DSC:
        raise ProcessingError("DSC系列だけをDSC処理できます。")
    return _process_curve(
        curve, common_setting, series_setting, available_curves, all_series_settings, DSC
    )


def validate_blank_reference(
    sample_key: str,
    blank_key: str,
    common_setting: CommonProcessingSettings,
    available_curves: Mapping[str, CurveData],
    all_series_settings: Mapping[str, SeriesProcessingSettings],
) -> None:
    """Validate a proposed per-series blank assignment before committing UI state."""
    _validate_blank_reference(
        sample_key, blank_key, common_setting, available_curves, all_series_settings
    )


def _process_curve(
    curve: CurveData,
    common: CommonProcessingSettings,
    series: SeriesProcessingSettings,
    curves: Mapping[str, CurveData],
    all_series_settings: Union[Mapping[str, SeriesProcessingSettings], None],
    mode: str,
) -> ProcessedCurveData:
    series.validate()
    raw_x, raw_y = curve.plot_x, curve.plot_y
    blank_key = resolve_effective_blank(common, series)
    normalization = resolve_effective_normalization(common, series) if mode == IR else None
    blank_name: Union[str, None] = None
    corrected_x, corrected_y = raw_x, raw_y
    blank_applied = False
    if series.blank_mode == USE_INDIVIDUAL and not blank_key:
        return _blank_failure(
            curve,
            "個別ブランクが未指定です。",
            normalization=normalization,
        )
    if blank_key == curve.key and series.blank_mode == USE_COMMON:
        # The designated common blank remains displayable as a normal raw series.
        # It is still always consumed from its raw source when correcting samples.
        blank_key = None
    if blank_key:
        blank_name = blank_reference_name(blank_key, curves)
        try:
            blank = curves[blank_key]
            blank_name = blank.display_name
            _validate_blank_reference(
                curve.key, blank_key, common, curves, all_series_settings or {}
            )
            if mode == IR:
                corrected_x, corrected_y = subtract_ir_blank(curve, blank)
            else:
                corrected_x, corrected_y = subtract_dsc_blank(curve, blank)
            blank_applied = True
        except KeyError:
            return _blank_failure(
                curve,
                "指定したブランクが未読込または削除済みです。",
                blank_name=blank_name,
                normalization=normalization,
            )
        except ProcessingError as exc:
            return _blank_failure(
                curve,
                str(exc),
                blank_name=blank_name,
                normalization=normalization,
            )

    if mode == DSC:
        return ProcessedCurveData(
            curve,
            corrected_x,
            corrected_y,
            status=BLANK_CORRECTED if blank_applied else RAW,
            blank_name=blank_name,
            blank_corrected_x=corrected_x if blank_applied else None,
            blank_corrected_y=corrected_y if blank_applied else None,
        )

    if series.normalization_mode == USE_INDIVIDUAL and normalization is None:
        return _normalization_failure(
            curve, corrected_x, corrected_y, blank_name, blank_applied, "個別の規格化波数が未入力です。"
        )
    if normalization is None:
        return ProcessedCurveData(
            curve,
            corrected_x,
            corrected_y,
            status=BLANK_CORRECTED if blank_applied else RAW,
            blank_name=blank_name,
            blank_corrected_x=corrected_x if blank_applied else None,
            blank_corrected_y=corrected_y if blank_applied else None,
        )
    try:
        normalized = normalize_ir_curve(corrected_x, normalization, corrected_y)
    except ProcessingError as exc:
        return _normalization_failure(
            curve, corrected_x, corrected_y, blank_name, blank_applied, str(exc), normalization
        )
    return ProcessedCurveData(
        curve,
        corrected_x,
        normalized,
        status=NORMALIZED,
        blank_name=blank_name,
        normalization_wavenumber=float(normalization),
        blank_corrected_x=corrected_x if blank_applied else None,
        blank_corrected_y=corrected_y if blank_applied else None,
        normalized_y=normalized,
    )


def _blank_failure(
    curve: CurveData,
    reason: str,
    *,
    blank_name: Union[str, None] = None,
    normalization: Union[float, None] = None,
) -> ProcessedCurveData:
    return ProcessedCurveData(
        curve,
        curve.plot_x,
        curve.plot_y,
        status=BLANK_FAILED,
        blank_name=blank_name,
        normalization_wavenumber=normalization,
        warnings=(reason,),
        blank_failed=True,
        normalization_failed=normalization is not None,
    )


def _normalization_failure(
    curve: CurveData,
    x_values: tuple[float, ...],
    y_values: tuple[float, ...],
    blank_name: Union[str, None],
    blank_applied: bool,
    reason: str,
    normalization: Union[float, None] = None,
) -> ProcessedCurveData:
    return ProcessedCurveData(
        curve,
        x_values,
        y_values,
        status=NORMALIZATION_FAILED,
        blank_name=blank_name,
        normalization_wavenumber=normalization,
        warnings=(reason,),
        blank_corrected_x=x_values if blank_applied else None,
        blank_corrected_y=y_values if blank_applied else None,
        normalization_failed=True,
    )


def _validate_blank_reference(
    sample_key: str,
    blank_key: str,
    common: CommonProcessingSettings,
    curves: Mapping[str, CurveData],
    all_settings: Mapping[str, SeriesProcessingSettings],
) -> None:
    if sample_key == blank_key:
        raise ProcessingError("系列自身をブランクとして指定できません。")
    if blank_key not in curves:
        raise ProcessingError("指定したブランクが未読込または削除済みです。")
    seen = {sample_key}
    current = blank_key
    while current:
        if current in seen:
            raise ProcessingError("ブランクの循環参照は指定できません。")
        seen.add(current)
        setting = all_settings.get(current)
        if setting is None or setting.blank_mode != USE_INDIVIDUAL:
            break
        current = setting.blank_key


def _subtract_with_interpolation(
    sample_x: Sequence[float],
    sample_y: Sequence[float],
    blank_x: Sequence[float],
    blank_y: Sequence[float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    _validate_xy(sample_x, sample_y, "試料")
    _validate_xy(blank_x, blank_y, "ブランク")
    low, high = calculate_overlap(sample_x, blank_x)
    sorted_blank_x, sorted_blank_y = _sorted_unique_xy(blank_x, blank_y)
    output_x: list[float] = []
    output_y: list[float] = []
    for x_value, sample_value in strict_zip(sample_x, sample_y, context='processing.py:524'):
        if low <= x_value <= high:
            output_x.append(float(x_value))
            output_y.append(
                float(sample_value - _interpolate_sorted(sorted_blank_x, sorted_blank_y, x_value))
            )
    if len(output_x) < 2:
        raise ProcessingError("重複範囲内の補正データ点が不足しています。")
    return tuple(output_x), tuple(output_y)


def _validate_xy(x_values: Sequence[float], y_values: Sequence[float], label: str) -> None:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        raise ProcessingError(f"{label}の処理に必要なデータ点が不足しています。")
    if any(not math.isfinite(float(value)) for value in (*x_values, *y_values)):
        raise ProcessingError(f"{label}に有限値ではないデータがあります。")


def _sorted_unique_xy(
    x_values: Sequence[float], y_values: Sequence[float]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    pairs = sorted(strict_zip(x_values, y_values, context='processing.py:545'))
    xs = tuple(float(pair[0]) for pair in pairs)
    ys = tuple(float(pair[1]) for pair in pairs)
    if any(xs[index] == xs[index - 1] for index in range(1, len(xs))):
        raise ProcessingError("補間元のX値が重複しているため補間できません。")
    return xs, ys


def _direction(values: Sequence[float]) -> int:
    delta = values[-1] - values[0]
    return 1 if delta > 0 else -1 if delta < 0 else 0


def _values_matching_x(
    source_x: Sequence[float], source_values: Sequence[float], target_x: Sequence[float]
) -> tuple[float, ...]:
    by_x = {x: value for x, value in strict_zip(source_x, source_values, context='processing.py:561')}
    return tuple(float(by_x[x]) for x in target_x if x in by_x)


def ir_mixed_normalization(processed: Sequence[ProcessedCurveData]) -> bool:
    if not processed:
        return False
    flags = {curve.is_normalized for curve in processed}
    return len(flags) > 1
