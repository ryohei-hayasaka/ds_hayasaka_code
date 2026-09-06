"""Canvas plotting engine.

The only data type this module understands is :class:`DisplaySeries`.  Mode
specific knowledge (which curve is a baseline, what a band means) lives in the
analysis panels, which express it through a :class:`PlotOverlay`.  That keeps
the renderer free of ``if measurement_type == ...`` branches.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple, Union

import math
import tkinter as tk
from dataclasses import dataclass

from . import theme
from .display_series import DisplaySeries, display_axis_titles, to_display_series
from .model import AxisRange, normalize_measurement_type


# ---------------------------------------------------------------------------
# Overlay vocabulary
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OverlayBand:
    """Shaded vertical range, drawn behind the curves."""

    start: float
    end: float
    color: str = theme.OVERLAY_SEARCH


@dataclass(frozen=True)
class OverlayArea:
    """Filled region between two y series (integration area)."""

    x_values: Tuple[float, ...]
    upper_values: Tuple[float, ...]
    lower_values: Tuple[float, ...]
    color: str = theme.OVERLAY_AREA


@dataclass(frozen=True)
class OverlayLine:
    """Straight segment in data coordinates (baseline fits, tangents)."""

    x_start: float
    y_start: float
    x_end: float
    y_end: float
    color: str = theme.OVERLAY_BASELINE
    dashed: bool = True
    width: float = 2.0


@dataclass(frozen=True)
class OverlayCurve:
    """Derived polyline such as the smoothed heat-flow trace."""

    x_values: Tuple[float, ...]
    y_values: Tuple[float, ...]
    color: str = theme.OVERLAY_SMOOTH
    dashed: bool = True
    width: float = 1.0


@dataclass(frozen=True)
class OverlayMarker:
    """Vertical dashed marker, or a dot when ``y`` is given."""

    x: float
    label: str = ""
    color: str = theme.OVERLAY_MARKER
    y: Union[float, None] = None
    rotated: bool = False


@dataclass(frozen=True)
class PlotOverlay:
    bands: Tuple[OverlayBand, ...] = ()
    areas: Tuple[OverlayArea, ...] = ()
    curves: Tuple[OverlayCurve, ...] = ()
    lines: Tuple[OverlayLine, ...] = ()
    markers: Tuple[OverlayMarker, ...] = ()
    warnings: Tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.bands
            or self.areas
            or self.curves
            or self.lines
            or self.markers
            or self.warnings
        )


EMPTY_OVERLAY = PlotOverlay()


# ---------------------------------------------------------------------------
# Geometry — pure functions, usable without a display
# ---------------------------------------------------------------------------
#: left, right, top, bottom margins in logical pixels.
PLOT_MARGINS = (78.0, 28.0, 24.0, 64.0)

MIN_PLOT_WIDTH = 80.0
MIN_PLOT_HEIGHT = 80.0


def plot_bounds(
    width: float, height: float, scale: float = 1.0
) -> Optional[Tuple[float, float, float, float]]:
    """Return ``(left, right, top, bottom)`` of the plot rectangle in pixels."""
    margin_left, margin_right, margin_top, margin_bottom = (
        value * scale for value in PLOT_MARGINS
    )
    plot_left = margin_left
    plot_right = width - margin_right
    plot_top = margin_top
    plot_bottom = height - margin_bottom
    if plot_right - plot_left < MIN_PLOT_WIDTH * scale:
        return None
    if plot_bottom - plot_top < MIN_PLOT_HEIGHT * scale:
        return None
    return plot_left, plot_right, plot_top, plot_bottom


def canvas_point_to_x(
    canvas_x: float,
    canvas_y: float,
    width: float,
    height: float,
    axis_range: AxisRange,
    reverse_x: bool = False,
    logarithmic_x: bool = False,
    scale: float = 1.0,
) -> Optional[float]:
    """Data X under the cursor, or ``None`` outside the plot rectangle."""
    bounds = plot_bounds(width, height, scale)
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


def canvas_point_to_y(
    canvas_x: float,
    canvas_y: float,
    width: float,
    height: float,
    axis_range: AxisRange,
    scale: float = 1.0,
) -> Optional[float]:
    """Data Y under the cursor, or ``None`` outside the plot rectangle."""
    bounds = plot_bounds(width, height, scale)
    if bounds is None:
        return None
    plot_left, plot_right, plot_top, plot_bottom = bounds
    if not (plot_left <= canvas_x <= plot_right and plot_top <= canvas_y <= plot_bottom):
        return None
    fraction = (plot_bottom - canvas_y) / (plot_bottom - plot_top)
    return axis_range.y_min + fraction * (axis_range.y_max - axis_range.y_min)


def data_point_to_canvas(
    x_value: float,
    y_value: float,
    width: float,
    height: float,
    axis_range: AxisRange,
    reverse_x: bool = False,
    logarithmic_x: bool = False,
    scale: float = 1.0,
) -> Optional[Tuple[float, float]]:
    """Inverse of :func:`canvas_point_to_x` / :func:`canvas_point_to_y`."""
    bounds = plot_bounds(width, height, scale)
    if bounds is None:
        return None
    plot_left, plot_right, plot_top, plot_bottom = bounds
    if logarithmic_x:
        if axis_range.x_min <= 0 or axis_range.x_max <= 0 or x_value <= 0:
            return None
        plot_min = math.log10(axis_range.x_min)
        plot_max = math.log10(axis_range.x_max)
        plot_value = math.log10(x_value)
    else:
        plot_min = axis_range.x_min
        plot_max = axis_range.x_max
        plot_value = x_value
    if plot_max == plot_min or axis_range.y_max == axis_range.y_min:
        return None
    fraction = (plot_value - plot_min) / (plot_max - plot_min)
    if reverse_x:
        fraction = 1.0 - fraction
    canvas_x = plot_left + fraction * (plot_right - plot_left)
    y_fraction = (y_value - axis_range.y_min) / (axis_range.y_max - axis_range.y_min)
    canvas_y = plot_bottom - y_fraction * (plot_bottom - plot_top)
    return canvas_x, canvas_y


def nice_ticks(value_min: float, value_max: float, target_count: int = 8) -> list:
    """1/2/5 x 10^n ticks strictly inside ``[value_min, value_max]``."""
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
    ticks = []
    value = first
    tolerance = step * 1e-9
    while value <= value_max + tolerance and len(ticks) < 100:
        ticks.append(0.0 if abs(value) < tolerance else value)
        value += step
    return ticks


def logarithmic_ticks(value_min: float, value_max: float):
    """Return ``(major, minor)`` base-10 ticks in data coordinates."""
    if value_min <= 0 or value_max <= 0 or value_min >= value_max:
        return [], []
    first_power = math.floor(math.log10(value_min))
    last_power = math.ceil(math.log10(value_max))
    majors = []
    minors = []
    for power in range(first_power, last_power + 1):
        decade = 10.0 ** power
        if value_min <= decade <= value_max:
            majors.append(decade)
        for multiple in range(2, 10):
            value = multiple * decade
            if value_min <= value <= value_max:
                minors.append(value)
    return majors, minors


def format_tick(value: float, ticks: Sequence[float]) -> str:
    """Format a tick with just enough decimals for the current step size."""
    if len(ticks) >= 2:
        step = abs(ticks[1] - ticks[0])
    else:
        step = abs(value) or 1.0
    if step >= 10:
        return "{0:.0f}".format(value)
    if step >= 1:
        return "{0:.1f}".format(value).rstrip("0").rstrip(".")
    decimals = min(max(0, -math.floor(math.log10(step)) + 1), 6)
    return "{0:.{1}f}".format(value, decimals).rstrip("0").rstrip(".")


def clip_segment(
    x1: float, y1: float, x2: float, y2: float, bounds: AxisRange
) -> Optional[Tuple[float, float, float, float]]:
    """Cohen-Sutherland clip of one segment against the axis rectangle."""
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


def _same_point(left, right) -> bool:
    return math.isclose(left[0], right[0], rel_tol=1e-10, abs_tol=1e-10) and math.isclose(
        left[1], right[1], rel_tol=1e-10, abs_tol=1e-10
    )


def clipped_polylines(
    x_values: Sequence[float],
    y_values: Sequence[float],
    axis: AxisRange,
    logarithmic_x: bool = False,
) -> list:
    """Split a curve into the visible polylines inside ``axis``."""
    result = []
    current = []
    if logarithmic_x and (axis.x_min <= 0 or axis.x_max <= 0):
        return result
    clip_axis = (
        AxisRange(math.log10(axis.x_min), math.log10(axis.x_max), axis.y_min, axis.y_max)
        if logarithmic_x
        else axis
    )
    points = (
        (math.log10(x_value) if logarithmic_x else x_value, y_value)
        for x_value, y_value in zip(x_values, y_values)
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
        start = (10 ** clipped[0] if logarithmic_x else clipped[0], clipped[1])
        end = (10 ** clipped[2] if logarithmic_x else clipped[2], clipped[3])
        if current and _same_point(current[-1], start):
            current.append(end)
        else:
            if len(current) >= 2:
                result.append(current)
            current = [start, end]
    if len(current) >= 2:
        result.append(current)
    return result


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------
@dataclass
class _Geometry:
    """Cached mapping used by the draw pass and by hit testing."""

    width: float
    height: float
    left: float
    right: float
    top: float
    bottom: float
    axis: AxisRange
    reverse_x: bool
    logarithmic_x: bool

    def px(self, x_value: float) -> float:
        if self.logarithmic_x:
            plot_min = math.log10(self.axis.x_min)
            plot_max = math.log10(self.axis.x_max)
            plot_value = math.log10(x_value) if x_value > 0 else plot_min
        else:
            plot_min = self.axis.x_min
            plot_max = self.axis.x_max
            plot_value = x_value
        span = plot_max - plot_min
        fraction = 0.0 if span == 0 else (plot_value - plot_min) / span
        if self.reverse_x:
            fraction = 1.0 - fraction
        return self.left + fraction * (self.right - self.left)

    def py(self, y_value: float) -> float:
        span = self.axis.y_max - self.axis.y_min
        if span == 0:
            return self.bottom
        return self.bottom - (y_value - self.axis.y_min) / span * (self.bottom - self.top)


class PlotView(tk.Canvas):
    """Scientific line plot on a plain tkinter canvas."""

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            background=theme.PLOT_BG,
            highlightthickness=0,
            borderwidth=0,
            **kwargs
        )
        self._series = ()  # type: Tuple[DisplaySeries, ...]
        self._axis = AxisRange(0.0, 100.0, 0.0, 105.0)
        self._measurement_type = "TGA"
        self._overlay = EMPTY_OVERLAY
        self._hidden = frozenset()
        self._highlight = None  # type: Optional[str]
        self._hover = None  # type: Optional[str]
        self._readout_x = None  # type: Optional[float]
        self._placeholder = "左のファイル一覧からCSVを選び［グラフへ追加］を押してください。"
        self._geometry = None  # type: Optional[_Geometry]
        self._click_handler = None  # type: Optional[Callable[[float, float], None]]
        self._alt_click_handler = None  # type: Optional[Callable[[], None]]
        self._hover_handler = None  # type: Optional[Callable[[Optional[str]], None]]
        self._redraw_job = None

        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Button-3>", self._on_alt_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)

    # -- public API --------------------------------------------------------
    def set_click_handler(self, handler: Optional[Callable[[float, float], None]]) -> None:
        """Called with data ``(x, y)`` for clicks inside the plot rectangle."""
        self._click_handler = handler
        self.configure(cursor="crosshair" if handler is not None else "")

    def set_alt_click_handler(self, handler: Optional[Callable[[], None]]) -> None:
        """Right-click, used by the point-selection state machines to undo."""
        self._alt_click_handler = handler

    def set_hover_handler(self, handler: Optional[Callable[[Optional[str]], None]]) -> None:
        self._hover_handler = handler

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text

    def set_plot(
        self,
        series,
        axis_range: AxisRange,
        measurement_type: str,
        overlay: Optional[PlotOverlay] = None,
        hidden_keys=(),
        highlight_key: Optional[str] = None,
        readout_x: Optional[float] = None,
    ) -> None:
        self._series = tuple(to_display_series(item) for item in series)
        self._axis = axis_range
        self._measurement_type = normalize_measurement_type(measurement_type)
        self._overlay = overlay or EMPTY_OVERLAY
        self._hidden = frozenset(hidden_keys)
        self._highlight = highlight_key
        self._readout_x = readout_x
        self.redraw()

    def visible_series(self):
        return tuple(item for item in self._series if item.key not in self._hidden)

    @property
    def axis_range(self) -> AxisRange:
        return self._axis

    def x_from_canvas_point(self, canvas_x: float, canvas_y: float) -> Optional[float]:
        return canvas_point_to_x(
            canvas_x,
            canvas_y,
            self._width(),
            self._height(),
            self._axis,
            reverse_x=self._reverse_x(),
            logarithmic_x=self._logarithmic_x(),
            scale=theme.current_scale(),
        )

    def y_from_canvas_point(self, canvas_x: float, canvas_y: float) -> Optional[float]:
        return canvas_point_to_y(
            canvas_x,
            canvas_y,
            self._width(),
            self._height(),
            self._axis,
            scale=theme.current_scale(),
        )

    def canvas_from_data(self, x_value: float, y_value: float):
        return data_point_to_canvas(
            x_value,
            y_value,
            self._width(),
            self._height(),
            self._axis,
            reverse_x=self._reverse_x(),
            logarithmic_x=self._logarithmic_x(),
            scale=theme.current_scale(),
        )

    # -- events ------------------------------------------------------------
    def _on_configure(self, _event) -> None:
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except Exception:
                pass
        self._redraw_job = self.after(16, self._deferred_redraw)

    def _deferred_redraw(self) -> None:
        self._redraw_job = None
        self.redraw()

    def _on_click(self, event) -> None:
        if self._click_handler is None:
            return
        x_value = self.x_from_canvas_point(event.x, event.y)
        if x_value is None:
            return
        y_value = self.y_from_canvas_point(event.x, event.y)
        self._click_handler(x_value, y_value if y_value is not None else 0.0)

    def _on_alt_click(self, _event) -> None:
        if self._alt_click_handler is not None:
            self._alt_click_handler()

    def _on_motion(self, event) -> None:
        key = self._series_near(event.x, event.y)
        if key == self._hover:
            return
        self._hover = key
        if self._hover_handler is not None:
            self._hover_handler(key)
        self.redraw()

    def _on_leave(self, _event) -> None:
        if self._hover is None:
            return
        self._hover = None
        if self._hover_handler is not None:
            self._hover_handler(None)
        self.redraw()

    def _series_near(self, canvas_x: float, canvas_y: float, tolerance: float = 6.0):
        """Key of the closest visible series within ``tolerance`` device px."""
        geometry = self._geometry
        if geometry is None:
            return None
        if not (
            geometry.left <= canvas_x <= geometry.right
            and geometry.top <= canvas_y <= geometry.bottom
        ):
            return None
        limit = theme.scale(tolerance)
        best_key = None
        best_distance = limit
        for item in self.visible_series():
            distance = self._distance_to_series(item, geometry, canvas_x, canvas_y, limit)
            if distance is not None and distance < best_distance:
                best_distance = distance
                best_key = item.key
        return best_key

    def _distance_to_series(self, item, geometry, canvas_x, canvas_y, limit):
        """Vertical pixel gap between the cursor and the curve at that X."""
        x_value = canvas_point_to_x(
            canvas_x,
            canvas_y,
            geometry.width,
            geometry.height,
            geometry.axis,
            reverse_x=geometry.reverse_x,
            logarithmic_x=geometry.logarithmic_x,
            scale=theme.current_scale(),
        )
        if x_value is None:
            return None
        y_value = _interpolate(item.x_values, item.y_values, x_value)
        if y_value is None:
            return None
        return abs(geometry.py(y_value) - canvas_y)

    # -- drawing -----------------------------------------------------------
    def _width(self) -> float:
        return float(max(self.winfo_width(), theme.scale(300)))

    def _height(self) -> float:
        return float(max(self.winfo_height(), theme.scale(220)))

    def _reverse_x(self) -> bool:
        return any(item.reverse_x for item in self._series)

    def _logarithmic_x(self) -> bool:
        return any(item.logarithmic_x for item in self._series)

    def redraw(self) -> None:
        self.delete("all")
        width = self._width()
        height = self._height()
        scale = theme.current_scale()
        bounds = plot_bounds(width, height, scale)
        if bounds is None:
            self._geometry = None
            return
        plot_left, plot_right, plot_top, plot_bottom = bounds
        axis = self._axis
        logarithmic_x = self._logarithmic_x()
        if logarithmic_x and (axis.x_min <= 0 or axis.x_max <= 0):
            self._geometry = None
            self.create_text(
                width / 2,
                height / 2,
                text="対数X軸の範囲は0より大きい値にしてください。",
                fill=theme.DANGER,
                font=theme.FONT_BODY,
            )
            return

        geometry = _Geometry(
            width=width,
            height=height,
            left=plot_left,
            right=plot_right,
            top=plot_top,
            bottom=plot_bottom,
            axis=axis,
            reverse_x=self._reverse_x(),
            logarithmic_x=logarithmic_x,
        )
        self._geometry = geometry

        self._draw_grid(geometry)
        self._draw_bands(geometry)
        self._draw_areas(geometry)
        self._draw_series(geometry)
        self._draw_overlay_curves(geometry)
        self._draw_overlay_lines(geometry)
        self._draw_markers(geometry)
        self._draw_readout(geometry)
        self._draw_frame(geometry)
        self._draw_axis_titles(geometry)
        self._draw_legend(geometry)
        self._draw_warnings(geometry)
        if not self.visible_series():
            self.create_text(
                (plot_left + plot_right) / 2,
                (plot_top + plot_bottom) / 2,
                text=self._placeholder,
                font=theme.FONT_BODY,
                fill=theme.PLOT_PLACEHOLDER,
            )

    def _draw_grid(self, geometry: _Geometry) -> None:
        axis = geometry.axis
        if geometry.logarithmic_x:
            x_ticks, x_minor = logarithmic_ticks(axis.x_min, axis.x_max)
        else:
            x_ticks, x_minor = nice_ticks(axis.x_min, axis.x_max, 9), []
        y_ticks = nice_ticks(axis.y_min, axis.y_max, 7)

        tick_length = theme.scale(5)
        for tick in x_minor:
            x = geometry.px(tick)
            self.create_line(
                x, geometry.top, x, geometry.bottom, fill=theme.PLOT_GRID_MINOR
            )
        for tick in x_ticks:
            x = geometry.px(tick)
            self.create_line(x, geometry.top, x, geometry.bottom, fill=theme.PLOT_GRID)
            # Inward tick, matching the Excel output style.
            self.create_line(
                x,
                geometry.bottom,
                x,
                geometry.bottom - tick_length,
                fill=theme.PLOT_FRAME,
            )
            self.create_text(
                x,
                geometry.bottom + theme.scale(14),
                text="{0:g}".format(tick) if geometry.logarithmic_x else format_tick(tick, x_ticks),
                font=theme.FONT_LABEL,
                fill=theme.PLOT_TICK_TEXT,
            )
        for tick in y_ticks:
            y = geometry.py(tick)
            self.create_line(geometry.left, y, geometry.right, y, fill=theme.PLOT_GRID)
            self.create_line(
                geometry.left, y, geometry.left + tick_length, y, fill=theme.PLOT_FRAME
            )
            self.create_text(
                geometry.left - theme.scale(8),
                y,
                text=format_tick(tick, y_ticks),
                anchor="e",
                font=theme.FONT_LABEL,
                fill=theme.PLOT_TICK_TEXT,
            )

    def _draw_frame(self, geometry: _Geometry) -> None:
        self.create_rectangle(
            geometry.left,
            geometry.top,
            geometry.right,
            geometry.bottom,
            outline=theme.PLOT_FRAME,
            width=max(1.0, theme.scale(1.2)),
        )

    def _draw_axis_titles(self, geometry: _Geometry) -> None:
        visible = self.visible_series() or self._series
        x_title, y_title = display_axis_titles(visible, self._measurement_type)
        self.create_text(
            (geometry.left + geometry.right) / 2,
            geometry.height - theme.scale(22),
            text=x_title,
            font=theme.FONT_HEAD,
            fill=theme.PLOT_AXIS_TITLE,
        )
        self.create_text(
            theme.scale(18),
            (geometry.top + geometry.bottom) / 2,
            text=y_title,
            angle=90,
            font=theme.FONT_HEAD,
            fill=theme.PLOT_AXIS_TITLE,
        )

    def _draw_bands(self, geometry: _Geometry) -> None:
        for band in self._overlay.bands:
            left = max(geometry.left, min(geometry.right, geometry.px(band.start)))
            right = max(geometry.left, min(geometry.right, geometry.px(band.end)))
            if right < left:
                left, right = right, left
            if right - left < 1:
                continue
            self.create_rectangle(
                left,
                geometry.top,
                right,
                geometry.bottom,
                fill=band.color,
                outline="",
            )

    def _draw_areas(self, geometry: _Geometry) -> None:
        axis = geometry.axis
        for area in self._overlay.areas:
            upper = [
                (geometry.px(x), geometry.py(y))
                for x, y in zip(area.x_values, area.upper_values)
                if axis.x_min <= x <= axis.x_max
            ]
            lower = [
                (geometry.px(x), geometry.py(y))
                for x, y in reversed(list(zip(area.x_values, area.lower_values)))
                if axis.x_min <= x <= axis.x_max
            ]
            polygon = upper + lower
            if len(polygon) < 3:
                continue
            flattened = [value for point in polygon for value in point]
            self.create_polygon(*flattened, fill=area.color, outline="")

    def _draw_series(self, geometry: _Geometry) -> None:
        base_width = max(1.0, theme.scale(1.8))
        highlight_width = max(2.0, theme.scale(3.0))
        emphasised = self._highlight or self._hover
        for item in self.visible_series():
            polylines = clipped_polylines(
                item.x_values, item.y_values, geometry.axis, item.logarithmic_x
            )
            is_emphasised = item.key == emphasised
            width = highlight_width if is_emphasised else base_width
            for polyline in polylines:
                points = _decimate(
                    [(geometry.px(x), geometry.py(y)) for x, y in polyline]
                )
                if len(points) < 2:
                    continue
                flattened = [value for point in points for value in point]
                self.create_line(
                    *flattened,
                    fill=item.color,
                    width=width,
                    capstyle=tk.ROUND,
                    joinstyle=tk.ROUND
                )

    def _draw_overlay_curves(self, geometry: _Geometry) -> None:
        dash = (max(3, theme.scale_int(4)), max(2, theme.scale_int(3)))
        for curve in self._overlay.curves:
            polylines = clipped_polylines(
                curve.x_values, curve.y_values, geometry.axis, geometry.logarithmic_x
            )
            for polyline in polylines:
                points = _decimate(
                    [(geometry.px(x), geometry.py(y)) for x, y in polyline]
                )
                if len(points) < 2:
                    continue
                flattened = [value for point in points for value in point]
                self.create_line(
                    *flattened,
                    fill=curve.color,
                    width=max(1.0, theme.scale(curve.width)),
                    dash=dash if curve.dashed else ()
                )

    def _draw_overlay_lines(self, geometry: _Geometry) -> None:
        dash = (max(4, theme.scale_int(6)), max(2, theme.scale_int(3)))
        for line in self._overlay.lines:
            self.create_line(
                geometry.px(line.x_start),
                geometry.py(line.y_start),
                geometry.px(line.x_end),
                geometry.py(line.y_end),
                fill=line.color,
                width=max(1.0, theme.scale(line.width)),
                dash=dash if line.dashed else (),
            )

    def _draw_markers(self, geometry: _Geometry) -> None:
        slot = 0
        for marker in self._overlay.markers:
            if not geometry.axis.x_min <= marker.x <= geometry.axis.x_max:
                continue
            if marker.y is None:
                # Analysis points often sit a few degrees apart; staggering the
                # rotated labels keeps them from overprinting each other.
                self._draw_vertical_marker(
                    geometry, marker.x, marker.label, marker.color, marker.rotated,
                    slot=slot,
                )
                slot += 1
            else:
                x_pixel = geometry.px(marker.x)
                y_pixel = geometry.py(marker.y)
                radius = theme.scale(4)
                self.create_oval(
                    x_pixel - radius,
                    y_pixel - radius,
                    x_pixel + radius,
                    y_pixel + radius,
                    fill=marker.color,
                    outline=theme.BG_PANEL,
                )
                if marker.label:
                    self.create_text(
                        x_pixel + theme.scale(6),
                        y_pixel - theme.scale(8),
                        text=marker.label,
                        anchor="sw",
                        fill=marker.color,
                        font=theme.FONT_LABEL,
                    )

    def _draw_vertical_marker(
        self, geometry: _Geometry, x_value: float, label: str, color: str,
        rotated: bool, slot: int = 0
    ) -> None:
        x_pixel = geometry.px(x_value)
        dash = (max(4, theme.scale_int(5)), max(2, theme.scale_int(3)))
        self.create_line(
            x_pixel,
            geometry.top,
            x_pixel,
            geometry.bottom,
            fill=color,
            width=max(1.0, theme.scale(1.5)),
            dash=dash,
        )
        if not label:
            return
        if rotated:
            stagger = theme.scale(6 + (slot % 3) * 58)
            self.create_text(
                x_pixel + theme.scale(4),
                geometry.top + stagger,
                text=label,
                anchor="nw",
                angle=270,
                fill=color,
                font=theme.FONT_LABEL,
            )
        else:
            self.create_text(
                x_pixel + theme.scale(4),
                geometry.top + theme.scale(4),
                text=label,
                anchor="nw",
                fill=color,
                font=theme.FONT_LABEL,
            )

    def _draw_readout(self, geometry: _Geometry) -> None:
        value = self._readout_x
        if value is None:
            return
        if not geometry.axis.x_min <= value <= geometry.axis.x_max:
            return
        self._draw_vertical_marker(
            geometry, value, "X = {0:g}".format(value), theme.OVERLAY_READOUT, False
        )


    def _draw_legend(self, geometry: _Geometry) -> None:
        visible = self.visible_series()
        if not visible:
            return
        shown = visible[:12]
        max_chars = max(min(len(item.legend_label), 30) for item in shown)
        box_width = max(theme.scale(170), theme.scale(max_chars * 7 + 52))
        row_height = theme.scale(19)
        extra = 1 if len(visible) > len(shown) else 0
        box_height = theme.scale(14) + row_height * (len(shown) + extra)
        x1 = geometry.right - box_width - theme.scale(10)
        y1 = geometry.top + theme.scale(10)
        self.create_rectangle(
            x1,
            y1,
            geometry.right - theme.scale(10),
            y1 + box_height,
            fill=theme.PLOT_LEGEND_BG,
            outline=theme.PLOT_LEGEND_BORDER,
        )
        for index, item in enumerate(shown):
            y = y1 + theme.scale(13) + index * row_height
            self.create_line(
                x1 + theme.scale(11),
                y,
                x1 + theme.scale(35),
                y,
                fill=item.color,
                width=max(2.0, theme.scale(3)),
            )
            label = item.legend_label
            if len(label) > 30:
                label = label[:27] + "..."
            self.create_text(
                x1 + theme.scale(42),
                y,
                text=label,
                anchor="w",
                font=theme.FONT_LABEL,
                fill=theme.TEXT if item.key == (self._highlight or self._hover) else theme.TEXT_SECOND,
            )
        if extra:
            y = y1 + theme.scale(13) + len(shown) * row_height
            self.create_text(
                x1 + theme.scale(11),
                y,
                text="ほか {0} 系列".format(len(visible) - len(shown)),
                anchor="w",
                font=theme.FONT_LABEL,
                fill=theme.TEXT_MUTED,
            )

    def _draw_warnings(self, geometry: _Geometry) -> None:
        warnings = list(self._overlay.warnings)
        normalized_flags = set(
            item.is_normalized
            for item in self.visible_series()
            if item.is_normalized is not None
        )
        if len(normalized_flags) > 1:
            warnings.insert(0, "規格化済みと未規格化の系列が混在しています")
        for index, message in enumerate(warnings[:3]):
            self.create_text(
                geometry.left + theme.scale(8),
                geometry.top + theme.scale(8) + index * theme.scale(15),
                text=message,
                anchor="nw",
                font=theme.FONT_LABEL,
                fill=theme.WARNING,
            )


def _decimate(points):
    """Drop points that land on the same device pixel as their predecessor."""
    if len(points) <= 2:
        return points
    result = [points[0]]
    last_x, last_y = int(points[0][0]), int(points[0][1])
    for x, y in points[1:-1]:
        rounded_x, rounded_y = int(x), int(y)
        if rounded_x == last_x and rounded_y == last_y:
            continue
        result.append((x, y))
        last_x, last_y = rounded_x, rounded_y
    result.append(points[-1])
    return result


def _interpolate(x_values, y_values, target):
    """Linear interpolation at ``target`` for monotone-or-not sampled data."""
    count = len(x_values)
    if count == 0:
        return None
    if count == 1:
        return y_values[0] if math.isclose(x_values[0], target) else None
    for index in range(count - 1):
        left = x_values[index]
        right = x_values[index + 1]
        low, high = (left, right) if left <= right else (right, left)
        if low <= target <= high:
            if math.isclose(left, right):
                return y_values[index]
            ratio = (target - left) / (right - left)
            return y_values[index] + ratio * (y_values[index + 1] - y_values[index])
    return None
