from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence, Union

from .analysis_common import (
    AnalysisError,
    LineFit,
    XRange,
    clipped_points,
    line_intersection,
    linear_regression,
    moving_average,
    trapezoid_integral,
    validate_xy,
)
from .compat import strict_zip
from .model import ADHESION, SS_CURVE, TEMPERATURE_LOGGER, CurveData


@dataclass(frozen=True)
class DerivedCurveData:
    source: CurveData
    display_x: tuple[float, ...]
    display_y: tuple[float, ...]
    x_axis_title: str
    y_axis_title: str
    x_header: str
    y_header: str
    metadata: tuple[str, ...] = ()

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

    def header_metadata(self) -> tuple[str, ...]:
        return self.metadata


@dataclass
class TemperaturePeakSettings:
    points: tuple[float, ...] = ()
    smoothing_window: int = 5
    tangent_points: int = 5


@dataclass(frozen=True)
class TemperaturePeakResult:
    start_time_min: float
    peak_time_min: float
    end_time_min: float
    area_c_min: float
    signed_area_c_min: float
    baseline: LineFit
    rising_tangent: LineFit
    falling_tangent: LineFit
    analysis_range: XRange
    pre_baseline_range: XRange
    peak_search_range: XRange
    post_baseline_range: XRange
    status: str = "候補"
    warnings: tuple[str, ...] = ()
    # Raw curve points within [start_time_min, end_time_min], kept so the UI
    # can shade the integration area against the baseline (like DSC melting)
    # instead of only drawing a rectangular range indicator.
    integration_x: tuple[float, ...] = ()
    integration_y: tuple[float, ...] = ()


@dataclass
class TemperatureAnalysisSession:
    settings: TemperaturePeakSettings = field(default_factory=TemperaturePeakSettings)
    result: Union[TemperaturePeakResult, None] = None
    status: str = "未解析"
    warnings: list[str] = field(default_factory=list)
    visible: bool = True


@dataclass
class SsSampleSettings:
    width_mm: Union[float, None] = None
    thickness_um: Union[float, None] = None
    l0_mm: Union[float, None] = None
    zero_mode: str = "none"
    zero_index: Union[int, None] = None
    zero_extension_mm: Union[float, None] = None
    zero_force_n: Union[float, None] = None

    @property
    def thickness_mm(self) -> Union[float, None]:
        return None if self.thickness_um is None else float(self.thickness_um) / 1000.0

    def validate_dimensions(self) -> None:
        _positive(self.width_mm, "試料幅")
        _positive(self.thickness_um, "試料厚み")
        _positive(self.l0_mm, "L0")
        if self.zero_mode not in {"none", "first", "selected", "numeric"}:
            raise AnalysisError("不明なSSカーブ0点補正設定です。")


@dataclass(frozen=True)
class SsCandidateResult:
    yield_index: Union[int, None]
    yield_strain_percent: Union[float, None]
    yield_stress_mpa: Union[float, None]
    maximum_index: int
    maximum_stress_mpa: float
    maximum_strain_percent: float
    elongation_at_break_percent: float


@dataclass(frozen=True)
class YoungModulusResult:
    range_start_percent: float
    range_end_percent: float
    modulus_mpa: float
    regression: LineFit
    point_count: int


@dataclass
class SsAnalysisSession:
    candidate: Union[SsCandidateResult, None] = None
    yield_range: Union[XRange, None] = None
    young_modulus: Union[YoungModulusResult, None] = None
    status: str = "未解析"
    warnings: list[str] = field(default_factory=list)


@dataclass
class AdhesionSampleSettings:
    width_mm: Union[float, None] = 25.0

    def validate(self) -> None:
        _positive(self.width_mm, "試料幅")


@dataclass(frozen=True)
class AdhesionAverageResult:
    range_start_mm: float
    range_end_mm: float
    average_n_per_25mm: float
    integral_n: float


@dataclass
class AdhesionAnalysisSession:
    selected_range: Union[XRange, None] = None
    result: Union[AdhesionAverageResult, None] = None
    status: str = "未解析"
    warnings: list[str] = field(default_factory=list)


def temperature_four_points_to_ranges(
    points: Sequence[float], curve_range: Union[XRange, None] = None
) -> tuple[XRange, XRange, XRange, XRange]:
    values = _strict_points(points, 4, "温度ロガーの4点")
    analysis = XRange(values[0], values[3])
    pre = XRange(values[0], values[1])
    search = XRange(values[1], values[2])
    post = XRange(values[2], values[3])
    if curve_range is not None and (
        analysis.start < curve_range.start or analysis.end > curve_range.end
    ):
        raise AnalysisError("選択位置が曲線の時間範囲外です。")
    return analysis, pre, search, post


def fit_combined_baseline(
    x_values: Sequence[float],
    y_values: Sequence[float],
    pre_range: XRange,
    post_range: XRange,
) -> LineFit:
    validate_xy(x_values, y_values, "温度ロガーデータ", minimum=5)
    pairs = [
        (float(x_value), float(y_value))
        for x_value, y_value in strict_zip(x_values, y_values, context="new_mode_analysis.fit_combined_baseline")
        if pre_range.contains(float(x_value)) or post_range.contains(float(x_value))
    ]
    pre_count = sum(1 for x_value, _y in pairs if pre_range.contains(x_value))
    post_count = sum(1 for x_value, _y in pairs if post_range.contains(x_value))
    if pre_count < 2:
        raise AnalysisError("ピーク前ベースラインのデータ点が不足しています。")
    if post_count < 2:
        raise AnalysisError("ピーク後ベースラインのデータ点が不足しています。")
    return linear_regression(
        tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs), "前後ベースライン"
    )


@dataclass(frozen=True)
class TemperatureChangePointSuggestion:
    """Auto-candidate for the 4-point peak selection (傾き閾値法).

    ``points`` is empty when no clear departure from the baseline was found;
    callers should fall back to manual 4-point selection in that case.
    """

    points: tuple = ()
    warnings: tuple = ()


def suggest_temperature_change_points(
    curve: CurveData,
    *,
    smoothing_window: int = 5,
    slope_threshold_c_per_min: Union[float, None] = None,
) -> TemperatureChangePointSuggestion:
    """Auto-suggest the 4 boundary points analyze_temperature_peak expects.

    Mirrors dsc_analysis.suggest_dsc_ranges: smooth the curve, estimate the
    baseline and its noise from the quiet edges, then walk outward from the
    peak while the slope (°C/min) stays above a threshold to find where the
    signal departs from — and returns to — that baseline. Never raises; a
    curve with no clear excursion gets an empty suggestion plus a warning,
    the same fallback style suggest_dsc_ranges uses for a missing candidate.
    """
    if curve.measurement_type != TEMPERATURE_LOGGER:
        raise AnalysisError("温度ロガー系列ではありません。")
    x_values = curve.logger_time_min
    y_values = curve.logger_temperature_c
    count = len(x_values)
    if count < 25:
        return TemperatureChangePointSuggestion(
            warnings=("自動候補には25点以上のデータが必要です。",)
        )
    smoothed = moving_average(y_values, smoothing_window)
    edge_count = max(5, count // 12)
    edge_indices = list(range(edge_count)) + list(range(count - edge_count, count))

    baseline = linear_regression(
        [x_values[index] for index in edge_indices],
        [smoothed[index] for index in edge_indices],
        "全体ベースライン",
    )
    corrected = tuple(
        smoothed[index] - baseline.at(x_values[index]) for index in range(count)
    )
    noise = _rms(corrected[index] for index in edge_indices)

    interior = range(edge_count, count - edge_count)
    peak_index = max(interior, key=lambda index: corrected[index])
    if corrected[peak_index] <= max(noise * 4.0, 1e-8):
        return TemperatureChangePointSuggestion(
            warnings=("有意な変化点を検出できませんでした。手動で4点を選択してください。",)
        )

    slopes = [0.0] * count
    for index in range(1, count - 1):
        dx = x_values[index + 1] - x_values[index - 1]
        slopes[index] = (smoothed[index + 1] - smoothed[index - 1]) / dx if dx else 0.0
    slope_noise = _rms(slopes[index] for index in edge_indices)
    threshold = slope_threshold_c_per_min
    if threshold is None:
        threshold = max(0.3, slope_noise * 4.0)

    # Slope is near zero at the peak itself, so the walk must look at the
    # *next* point in each direction rather than the current one — otherwise
    # it would stop after zero steps every time.
    left = peak_index
    while left > edge_count and slopes[left - 1] > threshold:
        left -= 1
    right = peak_index
    while right < count - edge_count - 1 and slopes[right + 1] < -threshold:
        right += 1

    if left <= edge_count or right >= count - edge_count - 1:
        return TemperatureChangePointSuggestion(
            warnings=("変化点がデータ端に近すぎるため自動候補を作成できませんでした。",)
        )

    padding = max(3, (right - left) // 4)
    pre_start_index = max(0, left - padding)
    post_end_index = min(count - 1, right + padding)
    if not (pre_start_index < left < right < post_end_index):
        return TemperatureChangePointSuggestion(
            warnings=("前後ベースラインのデータ点が不足しているため自動候補を作成できませんでした。",)
        )

    points = (
        x_values[pre_start_index],
        x_values[left],
        x_values[right],
        x_values[post_end_index],
    )
    return TemperatureChangePointSuggestion(points=points)


def _rms(values) -> float:
    data = list(values)
    return math.sqrt(sum(value * value for value in data) / len(data)) if data else 0.0


def analyze_temperature_peak(
    curve_or_x: Union[CurveData, Sequence[float]],
    settings_or_y: Union[TemperaturePeakSettings, Sequence[float]],
    points: Sequence[float] = (),
    smoothing_window: int = 5,
    tangent_points: int = 5,
) -> TemperaturePeakResult:
    if isinstance(curve_or_x, CurveData):
        if curve_or_x.measurement_type != TEMPERATURE_LOGGER:
            raise AnalysisError("温度ロガー系列ではありません。")
        x_values = curve_or_x.logger_time_min
        y_values = curve_or_x.logger_temperature_c
        if not isinstance(settings_or_y, TemperaturePeakSettings):
            raise AnalysisError("温度ロガー解析設定がありません。")
        settings = settings_or_y
    else:
        x_values = tuple(float(value) for value in curve_or_x)
        y_values = tuple(float(value) for value in settings_or_y)  # type: ignore[arg-type]
        settings = TemperaturePeakSettings(tuple(points), smoothing_window, tangent_points)
    validate_xy(x_values, y_values, "温度ロガーデータ", minimum=9)
    analysis, pre, search, post = temperature_four_points_to_ranges(
        settings.points, XRange(float(x_values[0]), float(x_values[-1]))
    )
    baseline = fit_combined_baseline(x_values, y_values, pre, post)
    indices = [
        index for index, x_value in enumerate(x_values) if analysis.contains(float(x_value))
    ]
    corrected = tuple(float(y_values[index]) - baseline.at(float(x_values[index])) for index in indices)
    smooth = moving_average(corrected, settings.smoothing_window)
    search_positions = [
        position for position, index in enumerate(indices) if search.contains(float(x_values[index]))
    ]
    if len(search_positions) < 3:
        raise AnalysisError("ピーク探索範囲のデータ点が不足しています。")
    peak_position = max(search_positions, key=lambda position: smooth[position])
    if smooth[peak_position] <= 0:
        raise AnalysisError("上向きピークを検出できません。")
    derivatives = []
    for position in range(1, len(indices)):
        left = indices[position - 1]
        right = indices[position]
        derivatives.append(
            (smooth[position] - smooth[position - 1])
            / (float(x_values[right]) - float(x_values[left]))
        )
    rising_candidates = [position for position in range(1, peak_position + 1)]
    falling_candidates = [position for position in range(peak_position + 1, len(indices))]
    if not rising_candidates:
        raise AnalysisError("上昇側接線を計算できません。")
    if not falling_candidates:
        raise AnalysisError("下降側接線を計算できません。")
    rising_position = max(rising_candidates, key=lambda position: derivatives[position - 1])
    falling_position = min(falling_candidates, key=lambda position: derivatives[position - 1])
    if derivatives[rising_position - 1] <= 0:
        raise AnalysisError("上昇側の正の最大傾斜を検出できません。")
    if derivatives[falling_position - 1] >= 0:
        raise AnalysisError("下降側の負の最大傾斜を検出できません。")
    rising_tangent = _local_tangent(x_values, y_values, indices[rising_position], settings.tangent_points, "上昇側")
    falling_tangent = _local_tangent(x_values, y_values, indices[falling_position], settings.tangent_points, "下降側")
    start_time = line_intersection(rising_tangent, baseline, "開始時間")
    end_time = line_intersection(falling_tangent, baseline, "終了時間")
    peak_time = float(x_values[indices[peak_position]])
    if not analysis.contains(start_time) or not analysis.contains(end_time):
        raise AnalysisError("接線交点が解析範囲外です。")
    if not start_time < peak_time < end_time:
        raise AnalysisError("開始時間、ピークトップ時間、終了時間の順序が不正です。")
    area_x, area_y = clipped_points(x_values, y_values, XRange(start_time, end_time))
    corrected_area = tuple(
        max(0.0, y_value - baseline.at(x_value))
        for x_value, y_value in strict_zip(area_x, area_y, context="new_mode_analysis.temperature_area")
    )
    signed_values = tuple(
        y_value - baseline.at(x_value)
        for x_value, y_value in strict_zip(area_x, area_y, context="new_mode_analysis.temperature_signed_area")
    )
    area = trapezoid_integral(area_x, corrected_area)
    signed_area = trapezoid_integral(area_x, signed_values)
    if area <= 0:
        raise AnalysisError("正のピーク面積が存在しません。")
    return TemperaturePeakResult(
        start_time,
        peak_time,
        end_time,
        abs(area),
        signed_area,
        baseline,
        rising_tangent,
        falling_tangent,
        analysis,
        pre,
        search,
        post,
        integration_x=area_x,
        integration_y=area_y,
    )


def auto_analyze_temperature_peak(curve: CurveData) -> TemperatureAnalysisSession:
    """Change-point auto-candidate (傾き閾値法) + immediate peak analysis.

    Mirrors dsc_analysis' auto-candidate flow: suggest a 4-point candidate,
    then run the real analysis immediately so 結果 shows a number, not just a
    guess. A flat or noisy curve is not an error — it just has no candidate
    yet, so this never raises; the caller always gets a session back. Runs
    on a worker thread, so it must not touch any Tk state.
    """
    suggestion = suggest_temperature_change_points(curve)
    if not suggestion.points:
        return TemperatureAnalysisSession(
            status="自動候補なし", warnings=list(suggestion.warnings)
        )
    settings = TemperaturePeakSettings(suggestion.points)
    try:
        result = analyze_temperature_peak(curve, settings)
    except AnalysisError as exc:
        return TemperatureAnalysisSession(
            settings=settings, status="自動候補なし", warnings=[str(exc)]
        )
    return TemperatureAnalysisSession(
        settings=settings, result=result, status="自動候補",
        warnings=list(result.warnings),
    )


def convert_ss_curve(
    curve_or_extension: Union[CurveData, Sequence[float]],
    force_or_settings: Union[Sequence[float], SsSampleSettings],
    width_mm: Union[float, None] = None,
    thickness_um: Union[float, None] = None,
    l0_mm: Union[float, None] = None,
    zero_index: Union[int, None] = None,
) -> DerivedCurveData:
    if isinstance(curve_or_extension, CurveData):
        curve = curve_or_extension
        if curve.measurement_type != SS_CURVE or not isinstance(force_or_settings, SsSampleSettings):
            raise AnalysisError("SSカーブ系列または試料情報が不正です。")
        settings = force_or_settings
        extension = curve.extension_mm
        force = curve.force_n
    else:
        extension = tuple(float(value) for value in curve_or_extension)
        force = tuple(float(value) for value in force_or_settings)  # type: ignore[arg-type]
        settings = SsSampleSettings(width_mm, thickness_um, l0_mm, "selected" if zero_index is not None else "none", zero_index)
        curve = CurveData(
            path=Path("ss_curve.csv"),
            display_name="SS curve",
            temperatures=(), mass_mg=(), weight_percent=(), measurement_type=SS_CURVE,
            extension_mm=extension, force_n=force,
        )
    settings.validate_dimensions()
    validate_xy(extension, force, "SSカーブデータ", minimum=2)
    index = _zero_index(settings.zero_mode, settings.zero_index, len(extension))
    if settings.zero_mode == "numeric":
        zero_extension = float(settings.zero_extension_mm or 0.0)
        zero_force = float(settings.zero_force_n or 0.0)
    else:
        zero_extension = float(extension[index]) if index is not None else 0.0
        zero_force = float(force[index]) if index is not None else 0.0
    strain = tuple((float(value) - zero_extension) / float(settings.l0_mm) * 100.0 for value in extension)
    stress = tuple((float(value) - zero_force) / (float(settings.width_mm) * float(settings.thickness_um) / 1000.0) for value in force)
    return DerivedCurveData(
        curve,
        strain,
        stress,
        "Strain (%)",
        "Stress (MPa)",
        "Strain_percent",
        "Stress_MPa",
        (
            f"Width={float(settings.width_mm):g} mm",
            f"Thickness={float(settings.thickness_um):g} µm",
            f"L0={float(settings.l0_mm):g} mm",
            f"ExtensionZero={zero_extension:g} mm",
            f"ForceZero={zero_force:g} N",
        ),
    )


def split_measurement_halves(point_count: int) -> tuple[range, range]:
    if point_count < 2:
        raise AnalysisError("前半・後半の解析に必要なデータ点が不足しています。")
    split = point_count // 2
    if split < 1 or point_count - split < 1:
        raise AnalysisError("前半または後半のデータ点が不足しています。")
    return range(0, split), range(split, point_count)


def detect_ss_candidates(
    strain_percent: Sequence[float], stress_mpa: Sequence[float]
) -> SsCandidateResult:
    validate_xy(strain_percent, stress_mpa, "SS解析データ", minimum=2)
    first_half, second_half = split_measurement_halves(len(stress_mpa))
    yield_index = max(first_half, key=lambda index: stress_mpa[index])
    maximum_index = max(second_half, key=lambda index: stress_mpa[index])
    return SsCandidateResult(
        yield_index,
        float(strain_percent[yield_index]),
        float(stress_mpa[yield_index]),
        maximum_index,
        float(stress_mpa[maximum_index]),
        float(strain_percent[maximum_index]),
        float(strain_percent[maximum_index]),
    )


def detect_ss_maximum_candidate(
    strain_percent: Sequence[float], stress_mpa: Sequence[float]
) -> SsCandidateResult:
    """Detect only the second-half maximum; yield remains user-selected."""
    validate_xy(strain_percent, stress_mpa, "SS解析データ", minimum=2)
    _first_half, second_half = split_measurement_halves(len(stress_mpa))
    maximum_index = max(second_half, key=lambda index: stress_mpa[index])
    return SsCandidateResult(
        None,
        None,
        None,
        maximum_index,
        float(stress_mpa[maximum_index]),
        float(strain_percent[maximum_index]),
        float(strain_percent[maximum_index]),
    )


def apply_ss_yield_range(
    candidate: SsCandidateResult,
    strain_percent: Sequence[float],
    stress_mpa: Sequence[float],
    start_percent: float,
    end_percent: float,
) -> SsCandidateResult:
    selected = XRange(float(start_percent), float(end_percent))
    selected.validate("降伏点探索範囲")
    validate_xy(strain_percent, stress_mpa, "SS解析データ", minimum=2)
    indices = [
        index
        for index, strain in enumerate(strain_percent)
        if selected.contains(float(strain))
    ]
    if not indices:
        raise AnalysisError("降伏点探索範囲にデータ点がありません。")
    index = max(indices, key=lambda item: stress_mpa[item])
    return SsCandidateResult(
        index,
        float(strain_percent[index]),
        float(stress_mpa[index]),
        candidate.maximum_index,
        candidate.maximum_stress_mpa,
        candidate.maximum_strain_percent,
        candidate.elongation_at_break_percent,
    )


def nearest_data_index(
    x_values: Sequence[float], y_values: Sequence[float], x_value: float, y_value: Union[float, None] = None
) -> int:
    validate_xy(x_values, y_values, "選択対象データ", minimum=1)
    if y_value is None:
        return min(range(len(x_values)), key=lambda index: abs(float(x_values[index]) - x_value))
    x_span = max(x_values) - min(x_values) or 1.0
    y_span = max(y_values) - min(y_values) or 1.0
    return min(
        range(len(x_values)),
        key=lambda index: ((float(x_values[index]) - x_value) / x_span) ** 2
        + ((float(y_values[index]) - y_value) / y_span) ** 2,
    )


def calculate_young_modulus(
    strain_percent: Sequence[float], stress_mpa: Sequence[float], start_percent: float, end_percent: float
) -> YoungModulusResult:
    selected = XRange(float(start_percent), float(end_percent))
    selected.validate("ヤング率範囲")
    validate_xy(strain_percent, stress_mpa, "SS解析データ", minimum=3)
    pairs = [
        (float(strain), float(stress))
        for strain, stress in strict_zip(strain_percent, stress_mpa, context="new_mode_analysis.young_modulus")
        if selected.contains(float(strain))
    ]
    if len(pairs) < 3:
        raise AnalysisError("ヤング率範囲には3点以上必要です。")
    dimensionless = tuple(strain / 100.0 for strain, _stress in pairs)
    stresses = tuple(stress for _strain, stress in pairs)
    fit = linear_regression(dimensionless, stresses, "ヤング率範囲")
    graph_fit = LineFit(fit.slope / 100.0, fit.intercept)
    return YoungModulusResult(selected.start, selected.end, fit.slope, graph_fit, len(pairs))


def convert_adhesive_force(
    curve_or_force: Union[CurveData, Sequence[float]], width_mm: float
) -> Union[DerivedCurveData, tuple[float, ...]]:
    _positive(width_mm, "試料幅")
    if isinstance(curve_or_force, CurveData):
        curve = curve_or_force
        if curve.measurement_type != ADHESION:
            raise AnalysisError("粘着力系列ではありません。")
        values = tuple(float(value) * 25.0 / width_mm for value in curve.force_n)
        return DerivedCurveData(
            curve,
            curve.distance_mm,
            values,
            "Distance (mm)",
            "Adhesive force (N/25 mm)",
            "Distance_mm",
            "AdhesiveForce_N_per_25mm",
            (f"Width={width_mm:g} mm",),
        )
    return tuple(float(value) * 25.0 / width_mm for value in curve_or_force)


def calculate_average_adhesive_force(
    distance_mm: Sequence[float],
    force_n_per_25mm: Sequence[float],
    start_mm: float,
    end_mm: float,
) -> AdhesionAverageResult:
    selected = XRange(float(start_mm), float(end_mm))
    selected.validate("平均範囲")
    xs, ys = clipped_points(distance_mm, force_n_per_25mm, selected)
    if len(xs) < 2:
        raise AnalysisError("積分可能なデータ点が不足しています。")
    integral = trapezoid_integral(xs, ys)
    average = integral / (selected.end - selected.start)
    if not math.isfinite(average):
        raise AnalysisError("平均粘着力を計算できません。")
    return AdhesionAverageResult(selected.start, selected.end, average, integral)


def _local_tangent(
    x_values: Sequence[float], y_values: Sequence[float], center_index: int, point_count: int, label: str
) -> LineFit:
    if point_count < 3:
        raise AnalysisError("接線算出点数は3以上にしてください。")
    if point_count % 2 == 0:
        raise AnalysisError("接線算出点数は奇数にしてください。")
    radius = point_count // 2
    start = max(0, center_index - radius)
    end = min(len(x_values), center_index + radius + 1)
    if end - start < 3:
        raise AnalysisError(f"{label}接線のデータ点が不足しています。")
    return linear_regression(x_values[start:end], y_values[start:end], f"{label}接線")


def _strict_points(points: Sequence[float], expected: int, label: str) -> tuple[float, ...]:
    if len(points) != expected:
        raise AnalysisError(f"{label}が揃っていません。")
    values = tuple(float(value) for value in points)
    if any(not math.isfinite(value) for value in values):
        raise AnalysisError(f"{label}は有限値で指定してください。")
    if any(values[index] <= values[index - 1] for index in range(1, len(values))):
        raise AnalysisError(f"{label}は小さい順に、重複なく指定してください。")
    return values


def _positive(value: Union[float, None], label: str) -> None:
    if value is None:
        raise AnalysisError(f"{label}が未入力です。")
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise AnalysisError(f"{label}は0より大きい有限数を入力してください。")


def _zero_index(mode: str, selected: Union[int, None], count: int) -> Union[int, None]:
    if mode in {"none", "numeric"}:
        return None
    if mode == "first":
        return 0
    if mode == "selected" and selected is not None and 0 <= selected < count:
        return selected
    raise AnalysisError("0点補正に使用するデータ点が不正です。")
