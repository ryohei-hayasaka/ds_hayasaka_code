from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path


TGA = "TGA"
DSC = "DSC"
IR = "IR"
UV_VIS = "UV-Vis"
GPC = "GPC"
PARTICLE_SIZE = "粒度分布"
MEASUREMENT_TYPES = (TGA, DSC, IR, UV_VIS, GPC, PARTICLE_SIZE)

DEFAULT_PALETTE = (
    "#1F77B4",
    "#D62728",
    "#2CA02C",
    "#9467BD",
    "#FF7F0E",
    "#17BECF",
    "#8C564B",
    "#E377C2",
    "#7F7F7F",
    "#BCBD22",
)


@dataclass(frozen=True, slots=True)
class ImportProvenance:
    """Resolved CSV import settings retained with a loaded series."""

    profile_id: str
    profile_name: str
    profile_fingerprint: str
    header_row: int | None
    data_start_row: int
    data_end_row: int
    x_column: str
    y_column: str
    encoding: str
    delimiter: str
    warnings: tuple[str, ...] = ()


def path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def normalize_measurement_type(value: str) -> str:
    normalized = value.strip().upper()
    canonical = {
        TGA.upper(): TGA,
        DSC.upper(): DSC,
        IR.upper(): IR,
        UV_VIS.upper(): UV_VIS,
        "UVVIS": UV_VIS,
        GPC.upper(): GPC,
        PARTICLE_SIZE.upper(): PARTICLE_SIZE,
        "PARTICLE_SIZE": PARTICLE_SIZE,
    }.get(normalized)
    if canonical is None:
        raise ValueError(f"未対応の測定モードです: {value}")
    return canonical


@dataclass(slots=True)
class CurveData:
    path: Path
    display_name: str
    temperatures: tuple[float, ...]
    mass_mg: tuple[float, ...]
    weight_percent: tuple[float, ...]
    color: str = DEFAULT_PALETTE[0]
    measurement_type: str = TGA
    heat_flow_mw: tuple[float, ...] = ()
    time_min: tuple[float, ...] = ()
    heat_flow_unit: str | None = None
    source_heat_flow_header: str | None = None
    legend_name: str | None = None
    wavenumbers_cm1: tuple[float, ...] = ()
    absorbance: tuple[float, ...] = ()
    wavelengths_nm: tuple[float, ...] = ()
    uvvis_absorbance: tuple[float, ...] = ()
    retention_times_min: tuple[float, ...] = ()
    ri_signal_mv: tuple[float, ...] = ()
    particle_diameter_um: tuple[float, ...] = ()
    volume_frequency_percent: tuple[float, ...] = ()
    source_particle_diameter_header: str | None = None
    source_volume_frequency_header: str | None = None
    sample_mass_mg: float | None = None
    heating_rate_c_min: float | None = None
    import_provenance: ImportProvenance | None = None

    def __post_init__(self) -> None:
        self.measurement_type = normalize_measurement_type(self.measurement_type)
        if self.measurement_type == IR:
            count = len(self.wavenumbers_cm1)
        elif self.measurement_type == UV_VIS:
            count = len(self.wavelengths_nm)
        elif self.measurement_type == GPC:
            count = len(self.retention_times_min)
        elif self.measurement_type == PARTICLE_SIZE:
            count = len(self.particle_diameter_um)
        else:
            count = len(self.temperatures)
        if not count:
            raise ValueError("CurveData requires at least one point")
        if self.time_min and len(self.time_min) != count:
            raise ValueError("Time and temperature arrays must have the same length")
        if self.measurement_type == TGA:
            if len(self.mass_mg) != count or len(self.weight_percent) != count:
                raise ValueError("All TGA curve arrays must have the same length")
        elif self.measurement_type == DSC and len(self.heat_flow_mw) != count:
            raise ValueError("All DSC curve arrays must have the same length")
        elif self.measurement_type == IR and len(self.absorbance) != count:
            raise ValueError("All IR curve arrays must have the same length")
        elif self.measurement_type == UV_VIS and len(self.uvvis_absorbance) != count:
            raise ValueError("All UV-Vis curve arrays must have the same length")
        elif self.measurement_type == GPC and len(self.ri_signal_mv) != count:
            raise ValueError("All GPC curve arrays must have the same length")
        elif (
            self.measurement_type == PARTICLE_SIZE
            and len(self.volume_frequency_percent) != count
        ):
            raise ValueError("All particle-size curve arrays must have the same length")
        if not self.color.startswith("#") or len(self.color) != 7:
            raise ValueError("Color must be a #RRGGBB value")
        normalized_legend = (self.legend_name or "").strip()
        self.legend_name = normalized_legend or self.display_name

    @property
    def point_count(self) -> int:
        return len(self.plot_x)

    @property
    def key(self) -> str:
        return path_key(self.path)

    @property
    def plot_y(self) -> tuple[float, ...]:
        if self.measurement_type == TGA:
            return self.weight_percent
        if self.measurement_type == DSC:
            return self.heat_flow_mw
        if self.measurement_type == IR:
            return self.absorbance
        if self.measurement_type == UV_VIS:
            return self.uvvis_absorbance
        if self.measurement_type == PARTICLE_SIZE:
            return self.volume_frequency_percent
        return self.ri_signal_mv

    @property
    def plot_x(self) -> tuple[float, ...]:
        if self.measurement_type == IR:
            return self.wavenumbers_cm1
        if self.measurement_type == UV_VIS:
            return self.wavelengths_nm
        if self.measurement_type == GPC:
            return self.retention_times_min
        if self.measurement_type == PARTICLE_SIZE:
            return self.particle_diameter_um
        return self.temperatures

    @property
    def legend_label(self) -> str:
        """Return the user-facing legend text with a safe series-name fallback."""
        return (self.legend_name or "").strip() or self.display_name


@dataclass(frozen=True, slots=True)
class AxisRange:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def validate(self) -> None:
        if not all(
            math.isfinite(value)
            for value in (self.x_min, self.x_max, self.y_min, self.y_max)
        ):
            raise ValueError("軸範囲は有限の数値で指定してください。")
        if self.x_min >= self.x_max:
            raise ValueError("X軸の最小値は最大値より小さくしてください。")
        if self.y_min >= self.y_max:
            raise ValueError("Y軸の最小値は最大値より小さくしてください。")


def default_axis_range(measurement_type: str) -> AxisRange:
    mode = normalize_measurement_type(measurement_type)
    if mode == TGA:
        return AxisRange(0.0, 100.0, 0.0, 105.0)
    if mode == UV_VIS:
        return AxisRange(200.0, 800.0, 0.0, 1.0)
    if mode == GPC:
        return AxisRange(0.0, 30.0, 0.0, 1.0)
    if mode == PARTICLE_SIZE:
        return AxisRange(0.1, 1000.0, 0.0, 10.0)
    return AxisRange(0.0, 100.0, -1.0, 1.0)


@dataclass(slots=True)
class PlotState:
    curves: dict[str, CurveData] = field(default_factory=dict)
    axis_range: AxisRange | None = None
    auto_axes: bool = True
    measurement_type: str = TGA

    def __post_init__(self) -> None:
        self.measurement_type = normalize_measurement_type(self.measurement_type)
        if self.axis_range is None:
            self.axis_range = default_axis_range(self.measurement_type)
        for curve in self.curves.values():
            self._validate_curve_mode(curve)

    def _validate_curve_mode(self, curve: CurveData) -> None:
        if curve.measurement_type != self.measurement_type:
            raise ValueError(
                f"{curve.measurement_type}曲線を{self.measurement_type}モードへ追加できません。"
            )

    def add_curve(self, curve: CurveData) -> bool:
        self._validate_curve_mode(curve)
        if curve.key in self.curves:
            return False
        curve.color = DEFAULT_PALETTE[len(self.curves) % len(DEFAULT_PALETTE)]
        self.curves[curve.key] = curve
        if self.auto_axes:
            self.apply_auto_range()
        return True

    def replace_curve(self, curve: CurveData) -> bool:
        self._validate_curve_mode(curve)
        existing = self.curves.get(curve.key)
        if existing is None:
            return False
        curve.color = existing.color
        curve.legend_name = existing.legend_label
        self.curves[curve.key] = curve
        if self.auto_axes:
            self.apply_auto_range()
        return True

    def remove_curve(self, key: str) -> bool:
        removed = self.curves.pop(key, None) is not None
        if removed and self.auto_axes:
            self.apply_auto_range()
        return removed

    def set_color(self, key: str, color: str) -> None:
        if not color.startswith("#") or len(color) != 7:
            raise ValueError("Color must be a #RRGGBB value")
        self.curves[key].color = color.upper()

    def set_legend_name(self, key: str, legend_name: str) -> None:
        normalized = legend_name.strip()
        if not normalized:
            raise ValueError("凡例名は空にできません。")
        self.curves[key].legend_name = normalized

    def set_manual_range(self, axis_range: AxisRange) -> None:
        axis_range.validate()
        if self.measurement_type == PARTICLE_SIZE and (
            axis_range.x_min <= 0 or axis_range.x_max <= 0
        ):
            raise ValueError("粒度分布のX軸範囲は0より大きい値で指定してください。")
        self.axis_range = axis_range
        self.auto_axes = False

    def apply_auto_range(self) -> AxisRange:
        if not self.curves:
            self.axis_range = default_axis_range(self.measurement_type)
        else:
            x_min = min(min(curve.plot_x) for curve in self.curves.values())
            x_max = max(max(curve.plot_x) for curve in self.curves.values())
            if x_min == x_max:
                padding = max(abs(x_min) * 0.05, 1.0)
                x_min -= padding
                x_max += padding
            if self.measurement_type == TGA:
                y_min, y_max = 0.0, 105.0
            elif self.measurement_type == PARTICLE_SIZE:
                y_min = min(min(curve.plot_y) for curve in self.curves.values())
                y_max = max(max(curve.plot_y) for curve in self.curves.values())
                span = y_max - y_min
                padding = max(span * 0.08, abs(y_max) * 0.05, 0.01)
                y_min = 0.0 if y_min >= 0 else y_min - padding
                y_max += padding
            else:
                y_min = min(min(curve.plot_y) for curve in self.curves.values())
                y_max = max(max(curve.plot_y) for curve in self.curves.values())
                span = y_max - y_min
                padding = max(span * 0.05, max(abs(y_min), abs(y_max)) * 0.02, 0.1)
                y_min -= padding
                y_max += padding
            self.axis_range = AxisRange(float(x_min), float(x_max), float(y_min), float(y_max))
        self.auto_axes = True
        return self.axis_range

    def ordered_curves(self) -> tuple[CurveData, ...]:
        return tuple(self.curves.values())
