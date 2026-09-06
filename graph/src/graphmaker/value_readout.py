from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


class ValueReadoutError(ValueError):
    """Raised when a displayed curve cannot be read at the requested X value."""


@dataclass(frozen=True)
class ValueReadoutResult:
    x_value: float
    y_value: float
    exact: bool


def interpolate_display_value(
    x_values: Sequence[float],
    y_values: Sequence[float],
    target_x: float,
    logarithmic_x: bool = False,
) -> ValueReadoutResult:
    """Read Y from the displayed points without extrapolation.

    Descending X data are supported.  Particle-size plots request interpolation
    in log10(X), matching their displayed logarithmic axis.
    """
    if len(x_values) != len(y_values):
        raise ValueReadoutError("X/Yデータ点数が一致しません。")
    if len(x_values) < 1:
        raise ValueReadoutError("読み取りに必要なデータ点がありません。")
    try:
        xs = tuple(float(value) for value in x_values)
        ys = tuple(float(value) for value in y_values)
        target = float(target_x)
    except (TypeError, ValueError) as exc:
        raise ValueReadoutError("X/Y値は有限数である必要があります。") from exc
    if not math.isfinite(target) or any(
        not math.isfinite(value) for value in (*xs, *ys)
    ):
        raise ValueReadoutError("X/Y値は有限数である必要があります。")
    if logarithmic_x and (target <= 0 or any(value <= 0 for value in xs)):
        raise ValueReadoutError("対数軸のX値は0より大きい必要があります。")
    if target < min(xs) or target > max(xs):
        raise ValueReadoutError("指定X値は系列の表示データ範囲外です。")
    for index, value in enumerate(xs):
        if math.isclose(value, target, rel_tol=1e-12, abs_tol=1e-12):
            return ValueReadoutResult(target, ys[index], True)
    if len(xs) < 2:
        raise ValueReadoutError("補間に必要なデータ点が不足しています。")
    direction = 1 if xs[-1] > xs[0] else -1
    if any((xs[index] - xs[index - 1]) * direction <= 0 for index in range(1, len(xs))):
        raise ValueReadoutError("X値が単調でないため補間できません。")
    for index in range(1, len(xs)):
        x1, x2 = xs[index - 1], xs[index]
        if min(x1, x2) <= target <= max(x1, x2):
            transform = math.log10 if logarithmic_x else float
            tx1, tx2, tt = transform(x1), transform(x2), transform(target)
            fraction = (tt - tx1) / (tx2 - tx1)
            return ValueReadoutResult(
                target,
                ys[index - 1] + fraction * (ys[index] - ys[index - 1]),
                False,
            )
    raise ValueReadoutError("補間に必要な区間を取得できません。")
