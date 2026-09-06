"""The `結果` tab and the Excel analysis tables.

Every mode gets a real result table here — including UV-Vis and GPC, which have
no analysis and therefore show a measured-data summary instead of the old
"look in the other tab" message (D5).

``analysis_table_for_mode`` produces exactly the table the previous edition
wrote into the workbook, so Excel output is unchanged.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .dsc_analysis import measurement_segment_label
from .excel_export import AnalysisResultTable
from .model import (
    ADHESION,
    DSC,
    GPC,
    IR,
    PARTICLE_SIZE,
    SS_CURVE,
    TEMPERATURE_LOGGER,
    TGA,
    UV_VIS,
)
from .new_mode_analysis import AdhesionSampleSettings, SsSampleSettings
from .tga_analysis import calculate_standard_td, format_td_temperature


UNAVAILABLE = "算出不可"
EMPTY = "—"


def _number(value: Optional[float], unavailable: str = EMPTY) -> str:
    return unavailable if value is None else "{0:.2f}".format(value)


# ---------------------------------------------------------------------------
# Column layouts
# ---------------------------------------------------------------------------
_SUMMARY_COLUMNS = (
    ("file", "ファイル名", 150),
    ("legend", "凡例名", 130),
    ("points", "点数", 60),
    ("x_min", "X 最小", 90),
    ("x_max", "X 最大", 90),
    ("y_min", "Y 最小", 90),
    ("y_max", "Y 最大", 90),
)

_SPECS = {
    TGA: (
        (
            ("file", "ファイル名", 150),
            ("legend", "凡例名", 120),
            ("td5", "Td5 (℃)", 80),
            ("td50", "Td50 (℃)", 80),
            ("td95", "Td95 (℃)", 80),
            ("tdx", "任意Tdx", 80),
            ("tdx_temp", "温度 (℃)", 85),
            ("status", "状態", 110),
            ("warning", "警告", 240),
        ),
        "全TGA系列のTd5・Td50・Td95。任意残存率のTdxは［解析］タブで算出します。",
    ),
    DSC: (
        (
            ("file", "ファイル名", 150),
            ("segment", "区間", 60),
            ("tg_onset", "Tg Onset ℃", 90),
            ("tg_midpoint", "Tg Mid ℃", 85),
            ("tg_inflection", "Tg Inf ℃", 85),
            ("melt_onset", "融解On ℃", 85),
            ("melt_peak", "融解Peak ℃", 90),
            ("melt_end", "融解End ℃", 85),
            ("enthalpy", "ΔH J/g", 80),
            ("status", "解析状態", 120),
            ("warning", "警告", 260),
        ),
        "行を選ぶと解析対象の系列が切り替わります。採用・除外は［解析］タブで設定します。",
    ),
    IR: (_SUMMARY_COLUMNS, "画面に表示中の処理済みIRデータの範囲です。ブランク・0点・規格化は［処理］タブで設定します。"),
    UV_VIS: (_SUMMARY_COLUMNS, "UV-Visは生データの比較表示とExcel出力のみに対応します。表示中データの範囲です。"),
    GPC: (_SUMMARY_COLUMNS, "GPCは生データの比較表示とExcel出力のみに対応します。表示中データの範囲です。"),
    PARTICLE_SIZE: (
        _SUMMARY_COLUMNS,
        "画面に表示中の粒度分布データの範囲です。指定粒径規格化は［処理］タブで設定します。",
    ),
    TEMPERATURE_LOGGER: (
        (
            ("file", "ファイル名", 160),
            ("legend", "凡例名", 120),
            ("start", "開始 min", 90),
            ("peak", "Peak min", 90),
            ("end", "終了 min", 90),
            ("area", "面積 ℃・min", 110),
            ("status", "解析状態", 100),
            ("warning", "警告", 240),
        ),
        "4点で回帰したベースラインから求めたピーク開始・トップ・終了と正のピーク面積です。",
    ),
    SS_CURVE: (
        (
            ("file", "ファイル名", 150),
            ("legend", "凡例名", 120),
            ("yield_start", "降伏範囲開始 %", 110),
            ("yield_end", "降伏範囲終了 %", 110),
            ("yield_strain", "降伏Strain %", 105),
            ("yield_stress", "降伏Stress MPa", 115),
            ("max_stress", "最大応力 MPa", 105),
            ("max_strain", "最大応力Strain %", 125),
            ("elongation", "破断伸度 %", 95),
            ("young", "ヤング率 MPa", 105),
            ("status", "解析状態", 105),
            ("warning", "警告", 240),
        ),
        "選択した2点範囲の最大応力と、手動範囲のヤング率回帰結果です。",
    ),
    ADHESION: (
        (
            ("file", "ファイル名", 160),
            ("legend", "凡例名", 120),
            ("width", "試料幅 mm", 90),
            ("start", "平均開始 mm", 100),
            ("end", "平均終了 mm", 100),
            ("average", "平均粘着力 N/25 mm", 140),
            ("status", "解析状態", 100),
            ("warning", "警告", 240),
        ),
        "選択した距離範囲を台形積分し、距離で割った距離加重平均です。",
    ),
}


def result_spec(mode: str):
    """Return ``(columns, caption)`` for the results table of ``mode``."""
    return _SPECS[mode]


# ---------------------------------------------------------------------------
# Rows for the on-screen table
# ---------------------------------------------------------------------------
def result_rows(app, mode: str):
    """Return ``(rows, keys)`` where ``keys[i]`` is the curve of ``rows[i]``."""
    if mode == TGA:
        return _tga_rows(app)
    if mode == DSC:
        return _dsc_rows(app)
    if mode == TEMPERATURE_LOGGER:
        return _temperature_rows(app)
    if mode == SS_CURVE:
        return _ss_rows(app)
    if mode == ADHESION:
        return _adhesion_rows(app)
    return _summary_rows(app, mode)


def _summary_rows(app, mode: str):
    rows = []
    keys = []
    for series in app.display_series(mode, include_hidden=True):
        rows.append(
            (
                series.source_path.name,
                series.legend_name,
                series.point_count,
                "{0:g}".format(min(series.x_values)),
                "{0:g}".format(max(series.x_values)),
                "{0:g}".format(min(series.y_values)),
                "{0:g}".format(max(series.y_values)),
            )
        )
        keys.append(series.series_key)
    return rows, keys


def _tga_rows(app):
    rows = []
    keys = []
    for curve in app.states[TGA].ordered_curves():
        summary = calculate_standard_td(curve)
        custom = app.tga_custom_results.get(curve.key, (EMPTY, None, "", ""))
        status = "／".join(part for part in (summary.status, custom[2]) if part)
        warnings = list(summary.warnings)
        if custom[3]:
            warnings.append(custom[3])
        rows.append(
            (
                curve.path.name,
                curve.legend_label,
                format_td_temperature(summary.td5_c),
                format_td_temperature(summary.td50_c),
                format_td_temperature(summary.td95_c),
                custom[0],
                format_td_temperature(custom[1]) if custom[0] != EMPTY else EMPTY,
                status,
                " / ".join(warnings),
            )
        )
        keys.append(curve.key)
    return rows, keys


def _dsc_rows(app):
    rows = []
    keys = []
    for curve in app.states[DSC].ordered_curves():
        session = app.dsc_sessions.get(curve.key)
        if session is None:
            rows.append(
                (
                    curve.display_name,
                    measurement_segment_label(curve),
                    EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, UNAVAILABLE,
                    "未解析", "",
                )
            )
        else:
            tg = session.tg_result
            melting = session.melting_result
            warnings = list(session.warnings)
            if tg is not None:
                warnings.extend(tg.warnings)
            if melting is not None:
                warnings.extend(melting.warnings)
            rows.append(
                (
                    curve.display_name,
                    measurement_segment_label(curve),
                    _number(_effective(session, "tg_onset", tg.onset_c if tg else None)),
                    _number(_effective(session, "tg_midpoint", tg.midpoint_c if tg else None)),
                    _number(_effective(session, "tg_inflection", tg.inflection_c if tg else None)),
                    _number(_effective(session, "melt_onset", melting.onset_c if melting else None)),
                    _number(_effective(session, "melt_peak", melting.peak_c if melting else None)),
                    _number(_effective(session, "melt_end", melting.end_c if melting else None)),
                    _number(melting.enthalpy_j_g if melting else None, UNAVAILABLE),
                    "{0}・{1}".format(session.decision, session.status),
                    " / ".join(dict.fromkeys(warnings)),
                )
            )
        keys.append(curve.key)
    return rows, keys


def _effective(session, name: str, value):
    if value is None:
        return None
    return session.overrides.get(name, value)


def _temperature_rows(app):
    rows = []
    keys = []
    for curve in app.states[TEMPERATURE_LOGGER].ordered_curves():
        session = app.temperature_sessions.get(curve.key)
        result = session.result if session else None
        rows.append(
            (
                curve.display_name,
                curve.legend_label,
                _number(result.start_time_min if result else None),
                _number(result.peak_time_min if result else None),
                _number(result.end_time_min if result else None),
                _number(result.area_c_min if result else None),
                session.status if session else "未解析",
                " / ".join(session.warnings) if session else "",
            )
        )
        keys.append(curve.key)
    return rows, keys


def _ss_rows(app):
    rows = []
    keys = []
    for curve in app.states[SS_CURVE].ordered_curves():
        session = app.ss_sessions.get(curve.key)
        candidate = session.candidate if session else None
        young = session.young_modulus if session else None
        yield_range = session.yield_range if session else None
        rows.append(
            (
                curve.display_name,
                curve.legend_label,
                _number(yield_range.start if yield_range else None),
                _number(yield_range.end if yield_range else None),
                _number(candidate.yield_strain_percent if candidate else None),
                _number(candidate.yield_stress_mpa if candidate else None),
                _number(candidate.maximum_stress_mpa if candidate else None),
                _number(candidate.maximum_strain_percent if candidate else None),
                _number(candidate.elongation_at_break_percent if candidate else None),
                _number(young.modulus_mpa if young else None),
                session.status if session else "未解析",
                " / ".join(session.warnings) if session else "",
            )
        )
        keys.append(curve.key)
    return rows, keys


def _adhesion_rows(app):
    rows = []
    keys = []
    for curve in app.states[ADHESION].ordered_curves():
        session = app.adhesion_sessions.get(curve.key)
        result = session.result if session else None
        setting = app.adhesion_settings.get(curve.key, AdhesionSampleSettings())
        rows.append(
            (
                curve.display_name,
                curve.legend_label,
                _number(setting.width_mm),
                _number(result.range_start_mm if result else None),
                _number(result.range_end_mm if result else None),
                _number(result.average_n_per_25mm if result else None),
                session.status if session else "未解析",
                " / ".join(session.warnings) if session else "",
            )
        )
        keys.append(curve.key)
    return rows, keys


# ---------------------------------------------------------------------------
# Excel analysis tables — byte-for-byte the previous edition's content
# ---------------------------------------------------------------------------
def analysis_table_for_mode(app, mode: str):
    if mode == TGA:
        headers = (
            "ファイル名", "凡例名", "Td5 (℃)", "Td50 (℃)", "Td95 (℃)",
            "任意Tdx", "任意Tdx温度 (℃)", "状態", "警告",
        )
        rows = []
        for curve in app.states[TGA].ordered_curves():
            summary = calculate_standard_td(curve)
            custom = app.tga_custom_results.get(curve.key, (EMPTY, None, "", ""))
            rows.append(
                (
                    curve.path.name,
                    curve.legend_label,
                    summary.td5_c if summary.td5_c is not None else UNAVAILABLE,
                    summary.td50_c if summary.td50_c is not None else UNAVAILABLE,
                    summary.td95_c if summary.td95_c is not None else UNAVAILABLE,
                    custom[0],
                    custom[1] if custom[1] is not None else UNAVAILABLE,
                    "／".join(filter(None, (summary.status, custom[2]))),
                    " / ".join((tuple(summary.warnings) + (custom[3],))),
                )
            )
        return AnalysisResultTable(headers, tuple(rows))

    if mode == TEMPERATURE_LOGGER:
        headers = (
            "ファイル名", "凡例名", "開始時間 (min)", "ピークトップ時間 (min)",
            "終了時間 (min)", "ピーク面積 (℃・min)", "状態", "警告",
        )
        rows = []
        for curve in app.states[mode].ordered_curves():
            session = app.temperature_sessions.get(curve.key)
            result = session.result if session else None
            rows.append(
                (
                    curve.path.name,
                    curve.legend_label,
                    result.start_time_min if result else UNAVAILABLE,
                    result.peak_time_min if result else UNAVAILABLE,
                    result.end_time_min if result else UNAVAILABLE,
                    result.area_c_min if result else UNAVAILABLE,
                    session.status if session else "未解析",
                    " / ".join(session.warnings) if session else "未解析です。",
                )
            )
        return AnalysisResultTable(headers, tuple(rows))

    if mode == SS_CURVE:
        headers = (
            "ファイル名", "凡例名", "幅 (mm)", "厚み (µm)", "L0 (mm)",
            "伸び0点 (mm)", "荷重0点 (N)", "降伏点範囲開始 (%)", "降伏点範囲終了 (%)",
            "降伏点Strain (%)", "降伏点Stress (MPa)", "最大応力 (MPa)",
            "最大応力時Strain (%)", "破断伸度 (%)", "ヤング率 (MPa)", "状態", "警告",
        )
        rows = []
        for curve in app.states[mode].ordered_curves():
            setting = app.ss_settings.get(curve.key, SsSampleSettings())
            session = app.ss_sessions.get(curve.key)
            candidate = session.candidate if session else None
            young = session.young_modulus if session else None
            yield_range = session.yield_range if session else None
            if setting.zero_mode == "first":
                zero_index = 0
            elif setting.zero_mode == "selected":
                zero_index = setting.zero_index
            else:
                zero_index = None
            if setting.zero_mode == "numeric":
                zero_extension = setting.zero_extension_mm
                zero_force = setting.zero_force_n
            elif zero_index is not None and 0 <= zero_index < len(curve.extension_mm):
                zero_extension = curve.extension_mm[zero_index]
                zero_force = curve.force_n[zero_index]
            else:
                zero_extension = ""
                zero_force = ""
            rows.append(
                (
                    curve.path.name,
                    curve.legend_label,
                    setting.width_mm or "",
                    setting.thickness_um or "",
                    setting.l0_mm or "",
                    zero_extension,
                    zero_force,
                    yield_range.start if yield_range else UNAVAILABLE,
                    yield_range.end if yield_range else UNAVAILABLE,
                    candidate.yield_strain_percent
                    if candidate and candidate.yield_strain_percent is not None
                    else UNAVAILABLE,
                    candidate.yield_stress_mpa
                    if candidate and candidate.yield_stress_mpa is not None
                    else UNAVAILABLE,
                    candidate.maximum_stress_mpa if candidate else UNAVAILABLE,
                    candidate.maximum_strain_percent if candidate else UNAVAILABLE,
                    candidate.elongation_at_break_percent if candidate else UNAVAILABLE,
                    young.modulus_mpa if young else UNAVAILABLE,
                    session.status if session else "未解析",
                    " / ".join(session.warnings)
                    if session
                    else "試料情報または解析結果がありません.",
                )
            )
        return AnalysisResultTable(headers, tuple(rows))

    if mode == ADHESION:
        headers = (
            "ファイル名", "凡例名", "試料幅 (mm)", "平均範囲開始 (mm)",
            "平均範囲終了 (mm)", "平均粘着力 (N/25 mm)", "状態", "警告",
        )
        rows = []
        for curve in app.states[mode].ordered_curves():
            setting = app.adhesion_settings.get(curve.key, AdhesionSampleSettings())
            session = app.adhesion_sessions.get(curve.key)
            result = session.result if session else None
            rows.append(
                (
                    curve.path.name,
                    curve.legend_label,
                    setting.width_mm or "",
                    result.range_start_mm if result else UNAVAILABLE,
                    result.range_end_mm if result else UNAVAILABLE,
                    result.average_n_per_25mm if result else UNAVAILABLE,
                    session.status if session else "未解析",
                    " / ".join(session.warnings)
                    if session
                    else "試料幅または解析結果がありません。",
                )
            )
        return AnalysisResultTable(headers, tuple(rows))

    if mode == DSC:
        headers = (
            "ファイル名", "凡例名", "区間", "Tg Onset (℃)", "Tg Midpoint (℃)",
            "Tg Inflection (℃)", "融解 Onset (℃)", "融解 Peak (℃)", "融解 End (℃)",
            "ΔH (J/g)", "状態", "警告",
        )
        rows = []
        for curve in app.states[DSC].ordered_curves():
            session = app.dsc_sessions.get(curve.key)
            tg = session.tg_result if session else None
            melting = session.melting_result if session else None
            warnings = []
            if session:
                warnings.extend(session.warnings)
            if tg:
                warnings.extend(tg.warnings)
            if melting:
                warnings.extend(melting.warnings)
            rows.append(
                (
                    curve.path.name,
                    curve.legend_label,
                    measurement_segment_label(curve),
                    tg.onset_c if tg else UNAVAILABLE,
                    tg.midpoint_c if tg else UNAVAILABLE,
                    tg.inflection_c if tg else UNAVAILABLE,
                    melting.onset_c if melting else UNAVAILABLE,
                    melting.peak_c if melting else UNAVAILABLE,
                    melting.end_c if melting else UNAVAILABLE,
                    melting.enthalpy_j_g
                    if melting and melting.enthalpy_j_g is not None
                    else UNAVAILABLE,
                    "{0}・{1}".format(session.decision, session.status)
                    if session
                    else "未解析",
                    " / ".join(dict.fromkeys(warnings))
                    or ("未解析です。" if session is None else ""),
                )
            )
        return AnalysisResultTable(headers, tuple(rows))

    return None
