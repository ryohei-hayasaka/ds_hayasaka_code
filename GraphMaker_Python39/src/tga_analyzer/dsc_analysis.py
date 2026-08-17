from __future__ import annotations

from .compat import strict_zip

from typing import Union

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .model import DSC, CurveData


KNOWN_HEAT_FLOW_UNITS = ("mW", "W/g", "mW/mg")


class DscAnalysisError(ValueError):
    pass


@dataclass(frozen=True)
class TemperatureRange:
    start: float
    end: float

    def validate(self, label: str = "解析範囲") -> None:
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise DscAnalysisError(f"{label}に有限値を入力してください。")
        if self.start >= self.end:
            raise DscAnalysisError(f"{label}の開始温度は終了温度より小さくしてください。")

    def contains(self, temperature: float) -> bool:
        return self.start <= temperature <= self.end


@dataclass(frozen=True)
class LineFit:
    slope: float
    intercept: float

    def at(self, temperature: float) -> float:
        return self.slope * temperature + self.intercept


@dataclass
class DscAnalysisSettings:
    heat_flow_unit: Union[str, None] = None
    heating_rate_c_min: Union[float, None] = None
    sample_mass_mg: Union[float, None] = None
    endotherm_up: bool = True
    smoothing_window: int = 7
    tg_range: Union[TemperatureRange, None] = None
    tg_pre_range: Union[TemperatureRange, None] = None
    tg_post_range: Union[TemperatureRange, None] = None
    melt_range: Union[TemperatureRange, None] = None
    melt_pre_range: Union[TemperatureRange, None] = None
    melt_post_range: Union[TemperatureRange, None] = None


@dataclass(frozen=True)
class TgResult:
    onset_c: float
    midpoint_c: float
    inflection_c: float
    analysis_range: TemperatureRange
    pre_range: TemperatureRange
    post_range: TemperatureRange
    pre_baseline: LineFit
    post_baseline: LineFit
    tangent: LineFit
    smoothed_heat_flow: tuple[float, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MeltingResult:
    onset_c: float
    peak_c: float
    end_c: float
    enthalpy_j_g: Union[float, None]
    enthalpy_signed_j_g: Union[float, None]
    analysis_range: TemperatureRange
    pre_range: TemperatureRange
    post_range: TemperatureRange
    baseline: LineFit
    integration_temperatures: tuple[float, ...]
    integration_heat_flow: tuple[float, ...]
    integration_baseline: tuple[float, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DscRangeSuggestions:
    tg_range: Union[TemperatureRange, None]
    tg_pre_range: Union[TemperatureRange, None]
    tg_post_range: Union[TemperatureRange, None]
    melt_range: Union[TemperatureRange, None]
    melt_pre_range: Union[TemperatureRange, None]
    melt_post_range: Union[TemperatureRange, None]
    warnings: tuple[str, ...] = ()


@dataclass
class DscAnalysisSession:
    settings: DscAnalysisSettings
    tg_result: Union[TgResult, None] = None
    melting_result: Union[MeltingResult, None] = None
    decision: str = "候補"
    status: str = "未解析"
    warnings: list[str] = field(default_factory=list)
    overrides: dict[str, float] = field(default_factory=dict)


def infer_heating_rate(curve: CurveData) -> Union[float, None]:
    if curve.measurement_type != DSC or len(curve.time_min) != curve.point_count:
        return None
    rates: list[float] = []
    for index in range(1, curve.point_count):
        delta_time = curve.time_min[index] - curve.time_min[index - 1]
        delta_temperature = curve.temperatures[index] - curve.temperatures[index - 1]
        if delta_time and math.isfinite(delta_time) and math.isfinite(delta_temperature):
            rate = abs(delta_temperature / delta_time)
            if rate > 0:
                rates.append(rate)
    return float(statistics.median(rates)) if rates else None


def measurement_segment_label(curve: CurveData) -> str:
    delta = curve.temperatures[-1] - curve.temperatures[0]
    if delta > 0:
        return "昇温"
    if delta < 0:
        return "冷却"
    return "区分不明"


def moving_average(values: Sequence[float], window: int) -> tuple[float, ...]:
    normalized = _normalize_window(window, len(values))
    half = normalized // 2
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - half)
        end = min(len(values), index + half + 1)
        smoothed.append((prefix[end] - prefix[start]) / (end - start))
    return tuple(smoothed)


def suggest_dsc_ranges(
    curve: CurveData,
    *,
    endotherm_up: bool = True,
    smoothing_window: int = 7,
) -> DscRangeSuggestions:
    temperatures, heat_flow = _ordered_curve(curve)
    if len(temperatures) < 25:
        raise DscAnalysisError("自動候補には25点以上のDSCデータが必要です。")
    smoothed = moving_average(heat_flow, smoothing_window)
    count = len(temperatures)
    edge_count = max(5, count // 12)
    edge_indices = list(range(edge_count)) + list(range(count - edge_count, count))
    global_baseline = _linear_fit(
        [temperatures[index] for index in edge_indices],
        [smoothed[index] for index in edge_indices],
        "全体ベースライン",
    )
    direction = 1.0 if endotherm_up else -1.0
    corrected = tuple(
        direction * (value - global_baseline.at(temperature))
        for temperature, value in strict_zip(temperatures, smoothed, context='dsc_analysis.py:169')
    )
    noise = _rms(
        [corrected[index] for index in edge_indices]
    )
    interior = range(edge_count, count - edge_count)
    peak_index = max(interior, key=lambda index: corrected[index])
    peak_height = corrected[peak_index]
    warnings: list[str] = []

    melt_range = melt_pre = melt_post = None
    if peak_height > max(noise * 4.0, 1e-8):
        threshold = max(peak_height * 0.03, noise * 3.0)
        left = peak_index
        while left > 1 and corrected[left] > threshold:
            left -= 1
        right = peak_index
        while right < count - 2 and corrected[right] > threshold:
            right += 1
        padding = max(2, (right - left) // 6)
        left = max(0, left - padding)
        right = min(count - 1, right + padding)
        # A permanent baseline step (for example Tg) can keep the global
        # corrected signal above the threshold long after the melting peak.
        # Cap the automatic candidate around the peak; users can widen it.
        maximum_half_span = max(
            (temperatures[-1] - temperatures[0]) * 0.12,
            (temperatures[1] - temperatures[0]) * 12.0,
        )
        left = max(
            left,
            _left_index(temperatures, temperatures[peak_index] - maximum_half_span),
        )
        right = min(
            right,
            _left_index(temperatures, temperatures[peak_index] + maximum_half_span),
        )
        melt_range = TemperatureRange(temperatures[left], temperatures[right])
        melt_pre, melt_post = _edge_ranges(melt_range, 0.22)
    else:
        warnings.append("融解ピークの自動候補を検出できませんでした。")

    search_end = count - edge_count
    if melt_range is not None:
        search_end = max(edge_count * 3 + 1, _left_index(temperatures, melt_range.start))
    window = max(5, count // 55)
    best_index: Union[int, None] = None
    best_score = 0.0
    for index in range(window * 3, search_end - window * 3):
        left_values = smoothed[index - 3 * window : index - 2 * window]
        right_values = smoothed[index + 2 * window : index + 3 * window]
        if not left_values or not right_values:
            continue
        step = statistics.fmean(right_values) - statistics.fmean(left_values)
        local_noise = _rms(
            [value - statistics.fmean(left_values) for value in left_values]
            + [value - statistics.fmean(right_values) for value in right_values]
        )
        score = abs(step) / max(local_noise, 1e-8)
        if score > best_score:
            best_score = score
            best_index = index

    tg_range = tg_pre = tg_post = None
    if best_index is not None and best_score >= 4.0:
        half_points = max(window * 4, count // 28)
        start_index = max(0, best_index - half_points)
        end_index = min(count - 1, best_index + half_points)
        tg_range = TemperatureRange(temperatures[start_index], temperatures[end_index])
        tg_pre, tg_post = _edge_ranges(tg_range, 0.25)
    else:
        warnings.append("Tgの自動候補を検出できませんでした。")

    return DscRangeSuggestions(
        tg_range=tg_range,
        tg_pre_range=tg_pre,
        tg_post_range=tg_post,
        melt_range=melt_range,
        melt_pre_range=melt_pre,
        melt_post_range=melt_post,
        warnings=tuple(warnings),
    )


def analyze_tg(curve: CurveData, settings: DscAnalysisSettings) -> TgResult:
    analysis_range = _required_range(settings.tg_range, "Tg解析範囲")
    pre_range = _required_range(settings.tg_pre_range, "Tg前ベースライン範囲")
    post_range = _required_range(settings.tg_post_range, "Tg後ベースライン範囲")
    _validate_nested_ranges(analysis_range, pre_range, post_range, "Tg")
    temperatures, heat_flow = _ordered_curve(curve)
    _require_data_in_range(temperatures, analysis_range, 9, "Tg解析範囲")
    pre_x, pre_y = _values_in_range(temperatures, heat_flow, pre_range)
    post_x, post_y = _values_in_range(temperatures, heat_flow, post_range)
    pre_line = _linear_fit(pre_x, pre_y, "Tg前ベースライン")
    post_line = _linear_fit(post_x, post_y, "Tg後ベースライン")
    smoothed = moving_average(heat_flow, settings.smoothing_window)
    derivatives = _derivative(temperatures, smoothed)
    transition_start = max(analysis_range.start, pre_range.end)
    transition_end = min(analysis_range.end, post_range.start)
    candidates = [
        index
        for index, temperature in enumerate(temperatures)
        if transition_start <= temperature <= transition_end
    ]
    if len(candidates) < 3:
        raise DscAnalysisError("Tgベースライン間に変化点を探索できる範囲がありません。")
    baseline_slope = (pre_line.slope + post_line.slope) / 2.0
    inflection_index = max(
        candidates,
        key=lambda index: abs(derivatives[index] - baseline_slope),
    )
    inflection_c = temperatures[inflection_index]
    local_half = max(2, _normalize_window(settings.smoothing_window, len(temperatures)) // 2)
    tangent_start = max(0, inflection_index - local_half)
    tangent_end = min(len(temperatures), inflection_index + local_half + 1)
    tangent = _linear_fit(
        temperatures[tangent_start:tangent_end],
        smoothed[tangent_start:tangent_end],
        "Tg変曲点接線",
    )
    denominator = tangent.slope - pre_line.slope
    if abs(denominator) < 1e-12:
        raise DscAnalysisError("Tgオンセットを求める接線と前ベースラインが平行です。")
    onset_c = (pre_line.intercept - tangent.intercept) / denominator
    if not analysis_range.contains(onset_c):
        raise DscAnalysisError("Tgオンセットが解析範囲外になりました。範囲を修正してください。")

    midpoint_line = LineFit(
        (pre_line.slope + post_line.slope) / 2.0,
        (pre_line.intercept + post_line.intercept) / 2.0,
    )
    midpoint_c = _nearest_crossing(
        temperatures,
        tuple(value - midpoint_line.at(temp) for temp, value in zip(temperatures, smoothed)),
        transition_start,
        transition_end,
        inflection_c,
        "Tg中点",
    )
    step_height = abs(post_line.at(inflection_c) - pre_line.at(inflection_c))
    noise = max(_fit_rms(pre_x, pre_y, pre_line), _fit_rms(post_x, post_y, post_line))
    if step_height <= max(noise * 3.0, 1e-10):
        raise DscAnalysisError("ベースライン間の段差がノイズに対して小さく、Tgを確定できません。")
    return TgResult(
        onset_c=onset_c,
        midpoint_c=midpoint_c,
        inflection_c=inflection_c,
        analysis_range=analysis_range,
        pre_range=pre_range,
        post_range=post_range,
        pre_baseline=pre_line,
        post_baseline=post_line,
        tangent=tangent,
        smoothed_heat_flow=smoothed,
        warnings=(f"移動平均{_normalize_window(settings.smoothing_window, len(temperatures))}点を変曲点検出だけに使用",),
    )


def analyze_melting(curve: CurveData, settings: DscAnalysisSettings) -> MeltingResult:
    analysis_range = _required_range(settings.melt_range, "融解解析・積分範囲")
    pre_range = _required_range(settings.melt_pre_range, "融解前ベースライン範囲")
    post_range = _required_range(settings.melt_post_range, "融解後ベースライン範囲")
    _validate_nested_ranges(analysis_range, pre_range, post_range, "融解")
    temperatures, heat_flow = _ordered_curve(curve)
    _require_data_in_range(temperatures, analysis_range, 9, "融解解析範囲")
    pre_x, pre_y = _values_in_range(temperatures, heat_flow, pre_range)
    post_x, post_y = _values_in_range(temperatures, heat_flow, post_range)
    baseline = _linear_fit(pre_x + post_x, pre_y + post_y, "融解ベースライン")
    direction = 1.0 if settings.endotherm_up else -1.0
    corrected = tuple(
        direction * (value - baseline.at(temperature))
        for temperature, value in strict_zip(temperatures, heat_flow, context='dsc_analysis.py:340')
    )
    integration_candidates = [
        index for index, temperature in enumerate(temperatures) if analysis_range.contains(temperature)
    ]
    search_start = max(analysis_range.start, pre_range.end)
    search_end = min(analysis_range.end, post_range.start)
    peak_candidates = [
        index
        for index, temperature in enumerate(temperatures)
        if search_start <= temperature <= search_end
    ]
    if len(peak_candidates) < 3:
        raise DscAnalysisError("融解前後のベースライン間にピークを探索できるデータ点がありません。")
    peak_index = max(peak_candidates, key=lambda index: corrected[index])
    peak_height = corrected[peak_index]
    noise = max(_fit_rms(pre_x, pre_y, baseline), _fit_rms(post_x, post_y, baseline))
    if peak_height <= max(noise * 4.0, 1e-10):
        raise DscAnalysisError("吸熱方向の融解ピークを検出できません。範囲または吸熱方向を確認してください。")
    threshold = max(peak_height * 0.02, noise * 3.0)
    onset_c = _threshold_crossing(
        temperatures, corrected, integration_candidates[0], peak_index, threshold, rising=True
    )
    end_c = _threshold_crossing(
        temperatures, corrected, peak_index, integration_candidates[-1], threshold, rising=False
    )
    if onset_c is None or end_c is None:
        raise DscAnalysisError("融解ピークのオンセットまたは終了位置を検出できません。")

    integration_indices = integration_candidates
    integration_temperatures = tuple(temperatures[index] for index in integration_indices)
    integration_heat_flow = tuple(heat_flow[index] for index in integration_indices)
    integration_baseline = tuple(
        baseline.at(temperatures[index]) for index in integration_indices
    )
    signed_area = _trapz(
        integration_temperatures,
        tuple(
            value - base
            for value, base in strict_zip(integration_heat_flow, integration_baseline, context='dsc_analysis.py:379')
        ),
    )
    enthalpy_signed, enthalpy, enthalpy_warnings = _enthalpy_from_area(
        signed_area,
        settings.heat_flow_unit,
        settings.heating_rate_c_min,
        settings.sample_mass_mg,
    )
    return MeltingResult(
        onset_c=onset_c,
        peak_c=temperatures[peak_index],
        end_c=end_c,
        enthalpy_j_g=enthalpy,
        enthalpy_signed_j_g=enthalpy_signed,
        analysis_range=analysis_range,
        pre_range=pre_range,
        post_range=post_range,
        baseline=baseline,
        integration_temperatures=integration_temperatures,
        integration_heat_flow=integration_heat_flow,
        integration_baseline=integration_baseline,
        warnings=tuple(enthalpy_warnings),
    )


def _enthalpy_from_area(
    signed_area: float,
    heat_flow_unit: Union[str, None],
    heating_rate_c_min: Union[float, None],
    sample_mass_mg: Union[float, None],
) -> tuple[Union[float, None], Union[float, None], list[str]]:
    warnings: list[str] = []
    if heat_flow_unit not in KNOWN_HEAT_FLOW_UNITS:
        warnings.append("熱流単位を特定できません。融解エンタルピーは算出不可です。")
        return None, None, warnings
    if heating_rate_c_min is None:
        warnings.append("昇温速度が未入力です。融解エンタルピーは算出不可です。")
        return None, None, warnings
    if heating_rate_c_min <= 0:
        warnings.append("昇温速度は0より大きい値が必要です。融解エンタルピーは算出不可です。")
        return None, None, warnings
    specific_area = signed_area
    if heat_flow_unit == "mW":
        if sample_mass_mg is None:
            warnings.append("mWデータのため試料重量が必要です。融解エンタルピーは算出不可です。")
            return None, None, warnings
        if sample_mass_mg <= 0:
            warnings.append("試料重量は0より大きい値が必要です。融解エンタルピーは算出不可です。")
            return None, None, warnings
        specific_area = signed_area / sample_mass_mg
    signed = specific_area * 60.0 / heating_rate_c_min
    return signed, abs(signed), warnings


def _ordered_curve(curve: CurveData) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if curve.measurement_type != DSC:
        raise DscAnalysisError("DSC曲線ではありません。")
    if curve.point_count < 3:
        raise DscAnalysisError("温度または熱流データが不足しています。")
    pairs = list(strict_zip(curve.temperatures, curve.heat_flow_mw, context='dsc_analysis.py:439'))
    delta = curve.temperatures[-1] - curve.temperatures[0]
    if delta < 0:
        pairs.reverse()
    temperatures = tuple(pair[0] for pair in pairs)
    heat_flow = tuple(pair[1] for pair in pairs)
    if any(right <= left for left, right in zip(temperatures, temperatures[1:])):
        raise DscAnalysisError("複数の昇温・冷却区間を含むデータは区間を分けて解析してください。")
    return temperatures, heat_flow


def _normalize_window(window: int, count: int) -> int:
    if window < 1:
        raise DscAnalysisError("平滑化点数は1以上にしてください。")
    normalized = int(window)
    if normalized % 2 == 0:
        normalized += 1
    maximum = count if count % 2 else count - 1
    return max(1, min(normalized, maximum))


def _linear_fit(xs: Sequence[float], ys: Sequence[float], label: str) -> LineFit:
    if len(xs) != len(ys) or len(xs) < 2:
        raise DscAnalysisError(f"{label}を計算できるデータ点が不足しています。")
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator <= 0:
        raise DscAnalysisError(f"{label}の温度範囲が不足しています。")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in strict_zip(xs, ys, context='dsc_analysis.py:468')) / denominator
    return LineFit(slope=slope, intercept=mean_y - slope * mean_x)


def _values_in_range(
    temperatures: Sequence[float],
    values: Sequence[float],
    selected_range: TemperatureRange,
) -> tuple[list[float], list[float]]:
    selected_range.validate()
    selected = [
        (temperature, value)
        for temperature, value in strict_zip(temperatures, values, context='dsc_analysis.py:480')
        if selected_range.contains(temperature)
    ]
    return [item[0] for item in selected], [item[1] for item in selected]


def _required_range(value: Union[TemperatureRange, None], label: str) -> TemperatureRange:
    if value is None:
        raise DscAnalysisError(f"{label}を設定してください。")
    value.validate(label)
    return value


def _validate_nested_ranges(
    analysis: TemperatureRange,
    pre: TemperatureRange,
    post: TemperatureRange,
    label: str,
) -> None:
    if pre.start < analysis.start or post.end > analysis.end:
        raise DscAnalysisError(f"{label}ベースライン範囲を解析範囲内に設定してください。")
    if pre.end >= post.start:
        raise DscAnalysisError(f"{label}前後のベースライン範囲が重ならないようにしてください。")


def _require_data_in_range(
    temperatures: Sequence[float], selected_range: TemperatureRange, minimum: int, label: str
) -> None:
    count = sum(selected_range.contains(value) for value in temperatures)
    if count < minimum:
        raise DscAnalysisError(f"{label}に必要なデータ点がありません（最低{minimum}点）。")


def _derivative(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, ...]:
    result: list[float] = []
    for index in range(len(xs)):
        left = max(0, index - 1)
        right = min(len(xs) - 1, index + 1)
        delta = xs[right] - xs[left]
        result.append(0.0 if delta == 0 else (ys[right] - ys[left]) / delta)
    return tuple(result)


def _nearest_crossing(
    xs: Sequence[float],
    values: Sequence[float],
    start: float,
    end: float,
    preferred: float,
    label: str,
) -> float:
    crossings: list[float] = []
    for index in range(1, len(xs)):
        if xs[index] < start or xs[index - 1] > end:
            continue
        left, right = values[index - 1], values[index]
        if left == 0:
            crossings.append(xs[index - 1])
        elif left * right <= 0 and right != left:
            fraction = -left / (right - left)
            crossings.append(xs[index - 1] + fraction * (xs[index] - xs[index - 1]))
    if not crossings:
        raise DscAnalysisError(f"{label}を解析範囲内で検出できません。")
    return min(crossings, key=lambda value: abs(value - preferred))


def _threshold_crossing(
    xs: Sequence[float],
    values: Sequence[float],
    start_index: int,
    end_index: int,
    threshold: float,
    *,
    rising: bool,
) -> Union[float, None]:
    if rising:
        indices = range(start_index + 1, end_index + 1)
    else:
        indices = range(end_index, start_index, -1)
    for index in indices:
        left_index = index - 1
        left = values[left_index] - threshold
        right = values[index] - threshold
        if left == 0:
            return xs[left_index]
        if left * right <= 0 and right != left:
            fraction = -left / (right - left)
            return xs[left_index] + fraction * (xs[index] - xs[left_index])
    return None


def _trapz(xs: Sequence[float], ys: Sequence[float]) -> float:
    return sum(
        (xs[index] - xs[index - 1]) * (ys[index] + ys[index - 1]) / 2.0
        for index in range(1, len(xs))
    )


def _fit_rms(xs: Sequence[float], ys: Sequence[float], line: LineFit) -> float:
    return _rms([value - line.at(x) for x, value in strict_zip(xs, ys, context='dsc_analysis.py:579')])


def _rms(values: Iterable[float]) -> float:
    data = list(values)
    return math.sqrt(statistics.fmean(value * value for value in data)) if data else 0.0


def _edge_ranges(
    analysis_range: TemperatureRange, fraction: float
) -> tuple[TemperatureRange, TemperatureRange]:
    width = analysis_range.end - analysis_range.start
    edge = width * fraction
    return (
        TemperatureRange(analysis_range.start, analysis_range.start + edge),
        TemperatureRange(analysis_range.end - edge, analysis_range.end),
    )


def _left_index(values: Sequence[float], target: float) -> int:
    for index, value in enumerate(values):
        if value >= target:
            return index
    return len(values) - 1
