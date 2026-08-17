from __future__ import annotations

from .compat import strict_zip

from typing import Union

import math
import tkinter as tk
from collections.abc import Sequence

from .dsc_analysis import DscAnalysisSession, LineFit, TemperatureRange
from .dsc_selection import DscFourPointSelection
from .display_series import (
    DisplaySeries,
    display_axis_titles,
    mode_logarithmic_x,
    mode_reverse_x,
    to_display_series,
)
from .model import DSC, IR, PARTICLE_SIZE, TGA, AxisRange, CurveData, normalize_measurement_type
from .processing import ProcessedCurveData


PLOT_MARGINS = (78.0, 28.0, 48.0, 68.0)


def plot_bounds(width: float, height: float) -> Union[tuple[float, float, float, float], None]:
    margin_left, margin_right, margin_top, margin_bottom = PLOT_MARGINS
    plot_left = margin_left
    plot_right = width - margin_right
    plot_top = margin_top
    plot_bottom = height - margin_bottom
    if plot_right - plot_left < 80 or plot_bottom - plot_top < 80:
        return None
    return plot_left, plot_right, plot_top, plot_bottom


def canvas_point_to_x(
    canvas_x: float,
    canvas_y: float,
    width: float,
    height: float,
    axis_range: AxisRange,
    *,
    reverse_x: bool = False,
    logarithmic_x: bool = False,
) -> Union[float, None]:
    bounds = plot_bounds(width, height)
    if bounds is None:
        return None
    plot_left, plot_right, plot_top, plot_bottom = bounds
    if not (plot_left <= canvas_x <= plot_right and plot_top <= canvas_y <= plot_bottom):
        return None
    fraction = (canvas_x - plot_left) / (plot_right - plot_left)
    if reverse_x:
        fraction = 1.0 - fraction
    if logarithmic_x:
        if axis_range.x_min <= 0 or axis_range.x_max <= 0:
            return None
        plot_min = math.log10(axis_range.x_min)
        plot_max = math.log10(axis_range.x_max)
        return 10 ** (plot_min + fraction * (plot_max - plot_min))
    return axis_range.x_min + fraction * (axis_range.x_max - axis_range.x_min)


def canvas_point_to_temperature(
    canvas_x: float,
    canvas_y: float,
    width: float,
    height: float,
    axis_range: AxisRange,
) -> Union[float, None]:
    return canvas_point_to_x(canvas_x, canvas_y, width, height, axis_range)


def nice_ticks(value_min: float, value_max: float, target_count: int = 8) -> list[float]:
    if value_min >= value_max:
        return [value_min]
    raw_step = (value_max - value_min) / max(target_count, 1)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    fraction = raw_step / magnitude
    if fraction <= 1:
        nice_fraction = 1
    elif fraction <= 2:
        nice_fraction = 2
    elif fraction <= 5:
        nice_fraction = 5
    else:
        nice_fraction = 10
    step = nice_fraction * magnitude
    first = math.ceil(value_min / step) * step
    ticks: list[float] = []
    value = first
    tolerance = step * 1e-9
    while value <= value_max + tolerance and len(ticks) < 100:
        ticks.append(0.0 if abs(value) < tolerance else value)
        value += step
    return ticks


def logarithmic_ticks(
    value_min: float, value_max: float
) -> tuple[list[float], list[float]]:
    """Return base-10 major and optional minor ticks in data coordinates."""
    if value_min <= 0 or value_max <= 0 or value_min >= value_max:
        return [], []
    first_power = math.floor(math.log10(value_min))
    last_power = math.ceil(math.log10(value_max))
    majors: list[float] = []
    minors: list[float] = []
    for power in range(first_power, last_power + 1):
        decade = 10.0**power
        if value_min <= decade <= value_max:
            majors.append(decade)
        for multiple in range(2, 10):
            value = multiple * decade
            if value_min <= value <= value_max:
                minors.append(value)
    return majors, minors


def format_tick(value: float, ticks: Sequence[float]) -> str:
    if len(ticks) >= 2:
        step = abs(ticks[1] - ticks[0])
    else:
        step = abs(value) or 1.0
    if step >= 10:
        return f"{value:.0f}"
    if step >= 1:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    decimals = min(max(0, -math.floor(math.log10(step)) + 1), 6)
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


def clip_segment(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    bounds: AxisRange,
) -> Union[tuple[float, float, float, float], None]:
    left, right, bottom, top = 1, 2, 4, 8

    def code(x: float, y: float) -> int:
        result = 0
        if x < bounds.x_min:
            result |= left
        elif x > bounds.x_max:
            result |= right
        if y < bounds.y_min:
            result |= bottom
        elif y > bounds.y_max:
            result |= top
        return result

    code1 = code(x1, y1)
    code2 = code(x2, y2)
    for _ in range(16):
        if not (code1 | code2):
            return x1, y1, x2, y2
        if code1 & code2:
            return None
        outside = code1 or code2
        if outside & top:
            if y2 == y1:
                return None
            x = x1 + (x2 - x1) * (bounds.y_max - y1) / (y2 - y1)
            y = bounds.y_max
        elif outside & bottom:
            if y2 == y1:
                return None
            x = x1 + (x2 - x1) * (bounds.y_min - y1) / (y2 - y1)
            y = bounds.y_min
        elif outside & right:
            if x2 == x1:
                return None
            y = y1 + (y2 - y1) * (bounds.x_max - x1) / (x2 - x1)
            x = bounds.x_max
        else:
            if x2 == x1:
                return None
            y = y1 + (y2 - y1) * (bounds.x_min - x1) / (x2 - x1)
            x = bounds.x_min
        if outside == code1:
            x1, y1 = x, y
            code1 = code(x1, y1)
        else:
            x2, y2 = x, y
            code2 = code(x2, y2)
    return None


class MeasurementPlotCanvas(tk.Canvas):
    def __init__(self, master, **kwargs):
        super().__init__(master, background="#FFFFFF", highlightthickness=0, **kwargs)
        self._curves: tuple[DisplaySeries, ...] = ()
        self._axis_range = AxisRange(0.0, 100.0, 0.0, 105.0)
        self._measurement_type = TGA
        self._dsc_session: Union[DscAnalysisSession, None] = None
        self._dsc_curve: Union[CurveData, None] = None
        self._dsc_selection: Union[DscFourPointSelection, None] = None
        self._annotation_visibility: dict[str, bool] = {}
        self._normalization_wavenumber: Union[float, None] = None
        self._normalization_diameter_um: Union[float, None] = None
        self.bind("<Configure>", lambda _event: self.after_idle(self.redraw))

    def temperature_from_canvas_point(self, canvas_x: float, canvas_y: float) -> Union[float, None]:
        return self.x_from_canvas_point(canvas_x, canvas_y)

    def x_from_canvas_point(self, canvas_x: float, canvas_y: float) -> Union[float, None]:
        return canvas_point_to_x(
            canvas_x,
            canvas_y,
            max(self.winfo_width(), 300),
            max(self.winfo_height(), 250),
            self._axis_range,
            reverse_x=mode_reverse_x(self._measurement_type),
            logarithmic_x=mode_logarithmic_x(self._measurement_type),
        )

    def set_plot(
        self,
        curves: Sequence[Union[CurveData, ProcessedCurveData, DisplaySeries]],
        axis_range: AxisRange,
        measurement_type: str = TGA,
        dsc_session: Union[DscAnalysisSession, None] = None,
        dsc_curve: Union[CurveData, None] = None,
        dsc_selection: Union[DscFourPointSelection, None] = None,
        annotation_visibility: Union[dict[str, bool], None] = None,
        normalization_wavenumber: Union[float, None] = None,
        normalization_diameter_um: Union[float, None] = None,
    ) -> None:
        self._curves = tuple(to_display_series(curve) for curve in curves)
        self._axis_range = axis_range
        self._measurement_type = normalize_measurement_type(measurement_type)
        self._dsc_session = dsc_session
        self._dsc_curve = dsc_curve
        self._dsc_selection = dsc_selection
        self._annotation_visibility = dict(annotation_visibility or {})
        self._normalization_wavenumber = normalization_wavenumber
        self._normalization_diameter_um = normalization_diameter_um
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 300)
        height = max(self.winfo_height(), 250)
        bounds = plot_bounds(width, height)
        if bounds is None:
            return
        plot_left, plot_right, plot_top, plot_bottom = bounds

        axis = self._axis_range
        logarithmic_x = mode_logarithmic_x(self._measurement_type)
        if logarithmic_x and (axis.x_min <= 0 or axis.x_max <= 0):
            self.create_text(
                width / 2,
                height / 2,
                text="粒度分布のX軸範囲は0より大きい値にしてください。",
                fill="#B42318",
            )
            return
        plot_x_min = math.log10(axis.x_min) if logarithmic_x else axis.x_min
        plot_x_max = math.log10(axis.x_max) if logarithmic_x else axis.x_max
        x_span = plot_x_max - plot_x_min
        y_span = axis.y_max - axis.y_min

        def px(x: float) -> float:
            plot_x = math.log10(x) if logarithmic_x else x
            fraction = (plot_x - plot_x_min) / x_span
            if mode_reverse_x(self._measurement_type):
                fraction = 1.0 - fraction
            return plot_left + fraction * (plot_right - plot_left)

        def py(y: float) -> float:
            return plot_bottom - (y - axis.y_min) / y_span * (plot_bottom - plot_top)

        self.create_text(
            (plot_left + plot_right) / 2,
            22,
            text=f"{self._measurement_type} Comparison",
            font=("Segoe UI", 14, "bold"),
            fill="#17324D",
        )
        self.create_rectangle(
            plot_left,
            plot_top,
            plot_right,
            plot_bottom,
            outline="#344054",
            width=1,
        )

        if logarithmic_x:
            x_ticks, x_minor_ticks = logarithmic_ticks(axis.x_min, axis.x_max)
        else:
            x_ticks = nice_ticks(axis.x_min, axis.x_max, 9)
            x_minor_ticks = []
        y_ticks = nice_ticks(axis.y_min, axis.y_max, 7)
        for tick in x_minor_ticks:
            x = px(tick)
            self.create_line(x, plot_top, x, plot_bottom, fill="#F2F4F7")
        for tick in x_ticks:
            x = px(tick)
            self.create_line(x, plot_top, x, plot_bottom, fill="#E4E7EC")
            self.create_text(
                x,
                plot_bottom + 20,
                text=f"{tick:g}" if logarithmic_x else format_tick(tick, x_ticks),
                font=("Segoe UI", 9),
                fill="#475467",
            )
        for tick in y_ticks:
            y = py(tick)
            self.create_line(plot_left, y, plot_right, y, fill="#E4E7EC")
            self.create_text(
                plot_left - 12,
                y,
                text=format_tick(tick, y_ticks),
                anchor="e",
                font=("Segoe UI", 9),
                fill="#475467",
            )

        x_axis_title, y_axis_title = display_axis_titles(
            self._curves, self._measurement_type
        )
        self.create_text(
            (plot_left + plot_right) / 2,
            height - 22,
            text=x_axis_title,
            font=("Segoe UI", 10),
            fill="#344054",
        )
        self.create_text(
            20,
            (plot_top + plot_bottom) / 2,
            text=y_axis_title,
            angle=90,
            font=("Segoe UI", 10),
            fill="#344054",
        )

        if not self._curves:
            self.create_text(
                (plot_left + plot_right) / 2,
                (plot_top + plot_bottom) / 2,
                text="左のファイル一覧からCSVを選択し、［グラフへ追加］を押してください。",
                font=("Segoe UI", 11),
                fill="#667085",
            )
            return

        if self._measurement_type == DSC and self._dsc_session is not None:
            self._draw_dsc_background(px, py, plot_left, plot_right, plot_top, plot_bottom)
        if self._measurement_type == DSC and self._dsc_selection is not None:
            self._draw_selection_background(px, plot_left, plot_right, plot_top, plot_bottom)

        for curve in self._curves:
            polylines = self._clipped_polylines(curve, axis)
            for polyline in polylines:
                screen_points: list[float] = []
                for x_value, y_value in polyline:
                    screen_points.extend((px(x_value), py(y_value)))
                if len(screen_points) >= 4:
                    self.create_line(
                        *screen_points,
                        fill=curve.color,
                        width=2,
                        capstyle=tk.ROUND,
                        joinstyle=tk.ROUND,
                    )
        if self._measurement_type == DSC and self._dsc_session is not None:
            self._draw_dsc_foreground(px, py, plot_left, plot_right, plot_top, plot_bottom)
        if self._measurement_type == DSC and self._dsc_selection is not None:
            self._draw_selection_foreground(px, plot_top, plot_bottom)
        if self._measurement_type == IR and self._normalization_wavenumber is not None:
            self._draw_ir_normalization_marker(px, plot_top, plot_bottom)
        if (
            self._measurement_type == PARTICLE_SIZE
            and self._normalization_diameter_um is not None
        ):
            self._draw_particle_normalization_marker(px, plot_top, plot_bottom)
        self._draw_legend(plot_right, plot_top)
        normalized_flags = {
            curve.is_normalized
            for curve in self._curves
            if curve.is_normalized is not None
        }
        if self._measurement_type in {IR, PARTICLE_SIZE} and len(normalized_flags) > 1:
            self.create_text(
                plot_left + 6,
                plot_top + 6,
                text="警告: 規格化済みと未規格化の系列が混在しています",
                anchor="nw",
                font=("Segoe UI", 9, "bold"),
                fill="#B42318",
            )

    def _y_axis_title(self) -> str:
        return display_axis_titles(self._curves, self._measurement_type)[1]

    def _visible(self, key: str) -> bool:
        return self._annotation_visibility.get(key, True)

    def _draw_dsc_background(
        self,
        px,
        py,
        plot_left: float,
        plot_right: float,
        plot_top: float,
        plot_bottom: float,
    ) -> None:
        session = self._dsc_session
        if session is None:
            return
        if session.tg_result is not None and self._visible("tg_range"):
            self._draw_range_band(
                px,
                session.tg_result.analysis_range,
                plot_left,
                plot_right,
                plot_top,
                plot_bottom,
                "#D6EAF8",
            )
        melting = session.melting_result
        if melting is not None and self._visible("melt_range"):
            self._draw_range_band(
                px,
                melting.analysis_range,
                plot_left,
                plot_right,
                plot_top,
                plot_bottom,
                "#FDEBD0",
            )
        if melting is not None and self._visible("enthalpy_area"):
            upper = [
                (px(x), py(y))
                for x, y in strict_zip(melting.integration_temperatures, melting.integration_heat_flow, context='plot_canvas.py:438')
                if self._axis_range.x_min <= x <= self._axis_range.x_max
            ]
            lower = [
                (px(x), py(y))
                for x, y in reversed(
                    tuple(
                        strict_zip(melting.integration_temperatures, melting.integration_baseline, context='plot_canvas.py:449')
                    )
                )
                if self._axis_range.x_min <= x <= self._axis_range.x_max
            ]
            polygon = upper + lower
            if len(polygon) >= 4:
                flattened = [coordinate for point in polygon for coordinate in point]
                self.create_polygon(
                    *flattened,
                    fill="#F5B7B1",
                    outline="",
                    stipple="gray50",
                )

    def _draw_dsc_foreground(
        self,
        px,
        py,
        plot_left: float,
        plot_right: float,
        plot_top: float,
        plot_bottom: float,
    ) -> None:
        session = self._dsc_session
        if session is None:
            return
        tg = session.tg_result
        if tg is not None:
            if self._visible("tg_smoothed") and self._dsc_curve is not None:
                curve = self._dsc_curve
                if len(curve.temperatures) == len(tg.smoothed_heat_flow):
                    points = []
                    for x, y in strict_zip(curve.temperatures, tg.smoothed_heat_flow, context='plot_canvas.py:486'):
                        if self._axis_range.x_min <= x <= self._axis_range.x_max:
                            points.extend((px(x), py(y)))
                    if len(points) >= 4:
                        self.create_line(*points, fill="#7D3C98", width=1, dash=(4, 3))
            if self._visible("tg_baselines"):
                self._draw_line_fit(px, py, tg.pre_baseline, tg.pre_range, "#2471A3")
                self._draw_line_fit(px, py, tg.post_baseline, tg.post_range, "#2471A3")
            markers = (
                ("tg_onset", session.overrides.get("tg_onset", tg.onset_c), "Tg On", "#1B4F72"),
                ("tg_midpoint", session.overrides.get("tg_midpoint", tg.midpoint_c), "Tg Mid", "#117864"),
                ("tg_inflection", session.overrides.get("tg_inflection", tg.inflection_c), "Tg Inf", "#7D3C98"),
            )
            for key, temperature, label, color in markers:
                if self._visible(key):
                    self._draw_marker(px, temperature, label, color, plot_top, plot_bottom)

        melting = session.melting_result
        if melting is not None:
            if self._visible("melt_baseline"):
                self._draw_line_fit(px, py, melting.baseline, melting.analysis_range, "#B03A2E")
            if self._visible("melt_points"):
                markers = (
                    (session.overrides.get("melt_onset", melting.onset_c), "Melt On"),
                    (session.overrides.get("melt_peak", melting.peak_c), "Peak"),
                    (session.overrides.get("melt_end", melting.end_c), "End"),
                )
                for temperature, label in markers:
                    self._draw_marker(px, temperature, label, "#B03A2E", plot_top, plot_bottom)

    def _draw_selection_background(
        self,
        px,
        plot_left: float,
        plot_right: float,
        plot_top: float,
        plot_bottom: float,
    ) -> None:
        selection = self._dsc_selection
        if selection is None:
            return
        points = selection.points
        if selection.analysis_type == "tg":
            pre_fill, search_fill, post_fill = "#D6EAF8", "#D5F5E3", "#D6EAF8"
        else:
            pre_fill, search_fill, post_fill = "#FDEBD0", "#FCF3CF", "#FDEBD0"
        bands = (
            (0, 1, pre_fill),
            (1, 2, search_fill),
            (2, 3, post_fill),
        )
        for start_index, end_index, fill in bands:
            if len(points) > end_index:
                self._draw_range_band(
                    px,
                    TemperatureRange(points[start_index], points[end_index]),
                    plot_left,
                    plot_right,
                    plot_top,
                    plot_bottom,
                    fill,
                )

    def _draw_selection_foreground(
        self,
        px,
        plot_top: float,
        plot_bottom: float,
    ) -> None:
        selection = self._dsc_selection
        if selection is None:
            return
        color = "#0E7490" if selection.analysis_type == "tg" else "#C2410C"
        short_roles = ("前開始", "前終了", "後開始", "後終了")
        for index, temperature in enumerate(selection.points):
            if not self._axis_range.x_min <= temperature <= self._axis_range.x_max:
                continue
            x = px(temperature)
            self.create_line(
                x,
                plot_top,
                x,
                plot_bottom,
                fill=color,
                width=2,
                dash=(5, 4),
            )
            self.create_text(
                x + 4,
                plot_top + 7,
                text=f"{index + 1} {short_roles[index]}  {temperature:.2f}℃",
                anchor="nw",
                angle=270,
                font=("Segoe UI", 8, "bold"),
                fill=color,
            )

    def _draw_range_band(
        self,
        px,
        selected_range: TemperatureRange,
        plot_left: float,
        plot_right: float,
        plot_top: float,
        plot_bottom: float,
        fill: str,
    ) -> None:
        left = max(plot_left, min(plot_right, px(selected_range.start)))
        right = max(plot_left, min(plot_right, px(selected_range.end)))
        if right > left:
            self.create_rectangle(
                left,
                plot_top,
                right,
                plot_bottom,
                fill=fill,
                outline="",
                stipple="gray75",
            )

    def _draw_line_fit(self, px, py, line: LineFit, selected_range: TemperatureRange, color: str) -> None:
        start = max(selected_range.start, self._axis_range.x_min)
        end = min(selected_range.end, self._axis_range.x_max)
        if start < end:
            self.create_line(
                px(start),
                py(line.at(start)),
                px(end),
                py(line.at(end)),
                fill=color,
                width=2,
                dash=(6, 3),
            )

    def _draw_marker(
        self,
        px,
        temperature: float,
        label: str,
        color: str,
        plot_top: float,
        plot_bottom: float,
    ) -> None:
        if not self._axis_range.x_min <= temperature <= self._axis_range.x_max:
            return
        x = px(temperature)
        self.create_line(x, plot_top, x, plot_bottom, fill=color, width=1, dash=(3, 3))
        self.create_text(
            x + 3,
            plot_bottom - 5,
            text=f"{label} {temperature:.2f}°C",
            anchor="sw",
            angle=90,
            font=("Segoe UI", 8),
            fill=color,
        )

    def _clipped_polylines(
        self, curve: DisplaySeries, axis: AxisRange
    ) -> list[list[tuple[float, float]]]:
        result: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        logarithmic_x = curve.logarithmic_x
        if logarithmic_x and (axis.x_min <= 0 or axis.x_max <= 0):
            return result
        clip_axis = (
            AxisRange(math.log10(axis.x_min), math.log10(axis.x_max), axis.y_min, axis.y_max)
            if logarithmic_x
            else axis
        )
        points = (
            (
                math.log10(x_value) if logarithmic_x and x_value > 0 else x_value,
                y_value,
            )
            for x_value, y_value in strict_zip(curve.x_values, curve.y_values, context='plot_canvas.py:661')
            if not logarithmic_x or x_value > 0
        )
        iterator = iter(points)
        try:
            previous = next(iterator)
        except StopIteration:
            return result
        for point in iterator:
            clipped = clip_segment(previous[0], previous[1], point[0], point[1], clip_axis)
            previous = point
            if clipped is None:
                if len(current) >= 2:
                    result.append(current)
                current = []
                continue
            start = (
                10**clipped[0] if logarithmic_x else clipped[0],
                clipped[1],
            )
            end = (
                10**clipped[2] if logarithmic_x else clipped[2],
                clipped[3],
            )
            if current and _same_point(current[-1], start):
                current.append(end)
            else:
                if len(current) >= 2:
                    result.append(current)
                current = [start, end]
        if len(current) >= 2:
            result.append(current)
        return result

    def _draw_ir_normalization_marker(self, px, plot_top: float, plot_bottom: float) -> None:
        wavenumber = self._normalization_wavenumber
        if wavenumber is None or not self._axis_range.x_min <= wavenumber <= self._axis_range.x_max:
            return
        x = px(wavenumber)
        self.create_line(x, plot_top, x, plot_bottom, fill="#0E7490", width=2, dash=(5, 4))
        self.create_text(
            x + 4,
            plot_top + 7,
            text=f"Norm {wavenumber:g} cm⁻¹",
            anchor="nw",
            angle=270,
            font=("Segoe UI", 8, "bold"),
            fill="#0E7490",
        )

    def _draw_particle_normalization_marker(
        self, px, plot_top: float, plot_bottom: float
    ) -> None:
        diameter = self._normalization_diameter_um
        if (
            diameter is None
            or diameter <= 0
            or not self._axis_range.x_min <= diameter <= self._axis_range.x_max
        ):
            return
        x = px(diameter)
        self.create_line(x, plot_top, x, plot_bottom, fill="#0E7490", width=2, dash=(5, 4))
        self.create_text(
            x + 4,
            plot_top + 7,
            text=f"Norm {diameter:g} µm",
            anchor="nw",
            angle=270,
            font=("Segoe UI", 8, "bold"),
            fill="#0E7490",
        )

    def _draw_legend(self, plot_right: float, plot_top: float) -> None:
        visible = self._curves[:12]
        max_chars = max(min(len(curve.legend_label), 34) for curve in visible)
        box_width = max(180, max_chars * 7 + 55)
        row_height = 20
        extra = 1 if len(self._curves) > len(visible) else 0
        box_height = 16 + row_height * (len(visible) + extra)
        x1 = plot_right - box_width - 10
        y1 = plot_top + 10
        self.create_rectangle(
            x1,
            y1,
            plot_right - 10,
            y1 + box_height,
            fill="#FFFFFF",
            outline="#D0D5DD",
        )
        for index, curve in enumerate(visible):
            y = y1 + 14 + index * row_height
            self.create_line(x1 + 12, y, x1 + 38, y, fill=curve.color, width=3)
            label = curve.legend_label
            if len(label) > 34:
                label = f"{label[:31]}..."
            self.create_text(
                x1 + 46,
                y,
                text=label,
                anchor="w",
                font=("Segoe UI", 9),
                fill="#344054",
            )
        if extra:
            y = y1 + 14 + len(visible) * row_height
            self.create_text(
                x1 + 12,
                y,
                text=f"ほか {len(self._curves) - len(visible)} 系列",
                anchor="w",
                font=("Segoe UI", 9),
                fill="#667085",
            )


def _same_point(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return math.isclose(left[0], right[0], rel_tol=1e-10, abs_tol=1e-10) and math.isclose(
        left[1], right[1], rel_tol=1e-10, abs_tol=1e-10
    )


# Kept for source compatibility with the initial TGA-only version.
TgaPlotCanvas = MeasurementPlotCanvas
