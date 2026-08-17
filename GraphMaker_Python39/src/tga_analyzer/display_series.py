from __future__ import annotations

from typing import Union

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .model import (
    DSC,
    GPC,
    IR,
    PARTICLE_SIZE,
    TGA,
    UV_VIS,
    CurveData,
    normalize_measurement_type,
)
from .particle_size_processing import ParticleSizeProcessedData
from .processing import NORMALIZED, ProcessedCurveData


@dataclass(frozen=True)
class DisplayColumn:
    header: str
    values: tuple[float, ...]
    number_format: str


@dataclass(frozen=True)
class DisplaySeries:
    """Read-only display/export projection; never used as analytical source data."""

    series_key: str
    display_name: str
    legend_name: str
    color: str
    x_values: tuple[float, ...]
    y_values: tuple[float, ...]
    x_axis_title: str
    y_axis_title: str
    x_data_header: str
    y_data_header: str
    reverse_x: bool
    logarithmic_x: bool
    measurement_type: str
    source_path: Path
    x_number_format: str = "0.000"
    y_number_format: str = "0.000000"
    extra_columns: tuple[DisplayColumn, ...] = ()
    header_metadata: tuple[str, ...] = ()
    is_normalized: Union[bool, None] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "measurement_type", normalize_measurement_type(self.measurement_type)
        )
        if len(self.x_values) != len(self.y_values) or len(self.x_values) < 1:
            raise ValueError("表示系列のX/Yデータ点数が一致していません。")
        if any(len(column.values) != len(self.x_values) for column in self.extra_columns):
            raise ValueError("表示系列の追加列データ点数が一致していません。")
        if self.logarithmic_x and any(value <= 0 for value in self.x_values):
            raise ValueError("粒径0以下のデータは対数軸で表示できません。")

    @property
    def point_count(self) -> int:
        return len(self.x_values)

    @property
    def key(self) -> str:
        return self.series_key

    @property
    def legend_label(self) -> str:
        return self.legend_name

    @property
    def plot_x(self) -> tuple[float, ...]:
        return self.x_values

    @property
    def plot_y(self) -> tuple[float, ...]:
        return self.y_values


def to_display_series(
    curve: Union[CurveData, ProcessedCurveData, ParticleSizeProcessedData, DisplaySeries],
) -> DisplaySeries:
    if isinstance(curve, DisplaySeries):
        return curve

    processed_types = (ProcessedCurveData, ParticleSizeProcessedData)
    source = curve.source if isinstance(curve, processed_types) else curve
    metadata = curve.header_metadata() if isinstance(curve, processed_types) else ()
    mode = source.measurement_type

    if mode == TGA:
        return _make(
            source,
            source.temperatures,
            source.weight_percent,
            "Temperature (°C)",
            "Weight (%)",
            "Temperature_C",
            "Weight_percent",
            y_format="0.000",
            extra=(DisplayColumn("Mass_mg", source.mass_mg, "0.0000"),),
        )

    if mode == DSC:
        x_values = curve.display_x if isinstance(curve, ProcessedCurveData) else source.temperatures
        y_values = curve.display_y if isinstance(curve, ProcessedCurveData) else source.heat_flow_mw
        if isinstance(curve, ProcessedCurveData):
            times = curve.time_min
        else:
            times = source.time_min
        if not times:
            times = tuple(float(index) for index in range(len(x_values)))
        heat_header = source.source_heat_flow_header or "HeatFlow"
        y_title = f"Heat Flow ({source.heat_flow_unit})" if source.heat_flow_unit else "Heat Flow"
        return _make(
            source,
            x_values,
            y_values,
            "Temperature (°C)",
            y_title,
            "Temperature_C",
            heat_header,
            y_format="0.0000",
            extra=(DisplayColumn("Time_min", times, "0.0000"),),
            metadata=metadata,
        )

    if mode == IR:
        x_values = curve.display_x if isinstance(curve, ProcessedCurveData) else source.wavenumbers_cm1
        y_values = curve.display_y if isinstance(curve, ProcessedCurveData) else source.absorbance
        normalized = isinstance(curve, ProcessedCurveData) and curve.status == NORMALIZED
        return _make(
            source,
            x_values,
            y_values,
            "Wavenumber (cm⁻¹)",
            "Normalized Absorbance" if normalized else "Absorbance",
            "Wavenumber_cm-1",
            "Absorbance",
            reverse_x=True,
            metadata=metadata,
            normalized=normalized,
        )

    if mode == UV_VIS:
        return _make(
            source,
            source.wavelengths_nm,
            source.uvvis_absorbance,
            "Wavelength (nm)",
            "Absorbance",
            "Wavelength_nm",
            "Absorbance",
        )

    if mode == PARTICLE_SIZE:
        processed = curve if isinstance(curve, ParticleSizeProcessedData) else None
        x_values = (
            processed.display_x if processed is not None else source.particle_diameter_um
        )
        y_values = (
            processed.display_y
            if processed is not None
            else source.volume_frequency_percent
        )
        normalized = processed.is_normalized if processed is not None else False
        return _make(
            source,
            x_values,
            y_values,
            "Particle diameter (µm)",
            "Normalized volume" if normalized else "Volume (%)",
            "ParticleDiameter_um",
            "NormalizedVolume" if normalized else "VolumeFrequency_percent",
            logarithmic_x=True,
            x_format="0.000000",
            metadata=metadata,
            normalized=normalized,
        )

    return _make(
        source,
        source.retention_times_min,
        source.ri_signal_mv,
        "Retention time (min)",
        "RI response (mV)",
        "RetentionTime_min",
        "RI_mV",
    )


def _make(
    source: CurveData,
    x_values: tuple[float, ...],
    y_values: tuple[float, ...],
    x_axis_title: str,
    y_axis_title: str,
    x_header: str,
    y_header: str,
    *,
    reverse_x: bool = False,
    logarithmic_x: bool = False,
    x_format: str = "0.000",
    y_format: str = "0.000000",
    extra: tuple[DisplayColumn, ...] = (),
    metadata: tuple[str, ...] = (),
    normalized: Union[bool, None] = None,
) -> DisplaySeries:
    return DisplaySeries(
        series_key=source.key,
        display_name=source.display_name,
        legend_name=source.legend_label,
        color=source.color,
        x_values=tuple(x_values),
        y_values=tuple(y_values),
        x_axis_title=x_axis_title,
        y_axis_title=y_axis_title,
        x_data_header=x_header,
        y_data_header=y_header,
        reverse_x=reverse_x,
        logarithmic_x=logarithmic_x,
        measurement_type=source.measurement_type,
        source_path=source.path,
        x_number_format=x_format,
        y_number_format=y_format,
        extra_columns=extra,
        header_metadata=metadata,
        is_normalized=normalized,
    )


def display_axis_titles(
    series: Sequence[DisplaySeries], measurement_type: str
) -> tuple[str, str]:
    mode = normalize_measurement_type(measurement_type)
    x_titles = {item.x_axis_title for item in series}
    y_titles = {item.y_axis_title for item in series}
    x_title = next(iter(x_titles)) if len(x_titles) == 1 else _default_axis_titles(mode)[0]
    if len(y_titles) == 1:
        y_title = next(iter(y_titles))
    elif mode == DSC:
        y_title = "Heat Flow"
    elif mode == IR:
        y_title = "Absorbance / Normalized Absorbance"
    elif mode == PARTICLE_SIZE:
        y_title = "Volume / Normalized volume"
    else:
        y_title = _default_axis_titles(mode)[1]
    return x_title, y_title


def mode_reverse_x(measurement_type: str) -> bool:
    return normalize_measurement_type(measurement_type) == IR


def mode_logarithmic_x(measurement_type: str) -> bool:
    return normalize_measurement_type(measurement_type) == PARTICLE_SIZE


def _default_axis_titles(measurement_type: str) -> tuple[str, str]:
    return {
        TGA: ("Temperature (°C)", "Weight (%)"),
        DSC: ("Temperature (°C)", "Heat Flow"),
        IR: ("Wavenumber (cm⁻¹)", "Absorbance"),
        UV_VIS: ("Wavelength (nm)", "Absorbance"),
        GPC: ("Retention time (min)", "RI response (mV)"),
        PARTICLE_SIZE: ("Particle diameter (µm)", "Volume (%)"),
    }[measurement_type]
