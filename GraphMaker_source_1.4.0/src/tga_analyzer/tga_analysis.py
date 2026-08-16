from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence

from .model import TGA, CurveData, PlotState


class TgaTdError(ValueError):
    """Raised when a decomposition temperature cannot be calculated."""


@dataclass(frozen=True, slots=True)
class TgaTdSummary:
    curve_key: str
    curve_name: str
    td5_c: float | None
    td50_c: float | None
    td95_c: float | None
    status: str
    warnings: tuple[str, ...] = ()


def format_td_temperature(value: float | None) -> str:
    if value is None:
        return "算出不可"
    if not math.isfinite(value):
        raise TgaTdError("温度または重量％データが不正です")
    return f"{value:.2f}"


def parse_remaining_percent(value: object) -> float:
    text = "" if value is None else str(value).strip()
    if not text:
        raise TgaTdError("残存率を入力してください")
    try:
        remaining = float(text)
    except (TypeError, ValueError) as exc:
        raise TgaTdError("残存率は数値で入力してください") from exc
    if not math.isfinite(remaining) or not 0.0 < remaining < 100.0:
        raise TgaTdError(
            "残存率は0より大きく100より小さい値を入力してください"
        )
    return remaining


def _format_percent(value: float) -> str:
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as exc:
        raise TgaTdError("残存率は数値で入力してください") from exc
    text = format(decimal_value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def td_label_from_remaining_percent(remaining_percent: object) -> str:
    remaining = parse_remaining_percent(remaining_percent)
    loss = Decimal("100") - Decimal(str(remaining))
    text = format(loss.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"Td{text}"


def find_decomposition_temperature(
    temperatures: Sequence[float],
    weight_percent: Sequence[float],
    target_remaining_percent: object,
) -> float:
    target = parse_remaining_percent(target_remaining_percent)
    if len(temperatures) != len(weight_percent):
        raise TgaTdError("温度と重量％のデータ点数が一致しません")
    if len(temperatures) < 2:
        raise TgaTdError("熱分解温度の算出に必要なデータ点が不足しています")

    try:
        pairs = tuple(
            (float(temperature), float(weight))
            for temperature, weight in zip(temperatures, weight_percent)
        )
    except (TypeError, ValueError) as exc:
        raise TgaTdError("温度または重量％データが不正です") from exc
    if any(not math.isfinite(value) for pair in pairs for value in pair):
        raise TgaTdError("温度または重量％データが不正です")

    for (t1, w1), (t2, w2) in zip(pairs, pairs[1:]):
        if w1 < target or w2 > target:
            continue
        if w1 == target:
            return t1
        if w2 == target:
            return t2
        if w1 == w2:
            continue
        return t1 + (target - w1) / (w2 - w1) * (t2 - t1)

    target_text = _format_percent(target)
    if min(weight for _temperature, weight in pairs) > target:
        raise TgaTdError(
            f"この測定範囲では残存率{target_text}%に到達していません"
        )
    raise TgaTdError(
        f"残存率{target_text}%に対応する下向き交差を検出できません"
    )


def selected_tga_curve(state: PlotState, selected_keys: Sequence[str]) -> CurveData:
    if state.measurement_type != TGA:
        raise TgaTdError("DSCモードではTGAのTd解析を実行できません")
    if len(selected_keys) != 1:
        raise TgaTdError("解析対象のTGA系列を1つ選択してください")
    curve = state.curves.get(selected_keys[0])
    if curve is None or curve.measurement_type != TGA:
        raise TgaTdError("解析対象のTGA系列を1つ選択してください")
    return curve


def calculate_standard_td(curve: CurveData) -> TgaTdSummary:
    if curve.measurement_type != TGA:
        raise TgaTdError("DSCモードではTGAのTd解析を実行できません")

    values: dict[str, float | None] = {}
    warnings: list[str] = []
    for label, remaining in (("Td5", 95.0), ("Td50", 50.0), ("Td95", 5.0)):
        try:
            values[label] = find_decomposition_temperature(
                curve.temperatures,
                curve.weight_percent,
                remaining,
            )
        except TgaTdError as exc:
            values[label] = None
            warnings.append(f"{label}: {exc}")

    calculated = sum(value is not None for value in values.values())
    if calculated == 3:
        status = "算出済み"
    elif calculated:
        status = "一部算出不可"
    else:
        status = "算出不可"
    return TgaTdSummary(
        curve_key=curve.key,
        curve_name=curve.display_name,
        td5_c=values["Td5"],
        td50_c=values["Td50"],
        td95_c=values["Td95"],
        status=status,
        warnings=tuple(warnings),
    )


def calculate_custom_td_for_selection(
    state: PlotState,
    selected_keys: Sequence[str],
    remaining_percent: object,
) -> tuple[str, float]:
    curve = selected_tga_curve(state, selected_keys)
    remaining = parse_remaining_percent(remaining_percent)
    label = td_label_from_remaining_percent(remaining)
    temperature = find_decomposition_temperature(
        curve.temperatures,
        curve.weight_percent,
        remaining,
    )
    return label, temperature
