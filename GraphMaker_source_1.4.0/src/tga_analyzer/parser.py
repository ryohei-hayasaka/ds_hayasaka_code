from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import TypeVar

from .model import DSC, GPC, IR, PARTICLE_SIZE, TGA, UV_VIS, CurveData


TGA_REQUIRED_HEADERS = ("Record_ID", "Time_min", "Temperature_C", "Mass_mg")
DSC_REQUIRED_HEADERS = ("Temperature_C", "HeatFlow_mW")
DSC_HEAT_FLOW_HEADERS = {
    "HeatFlow_mW": "mW",
    "HeatFlow_W_g": "W/g",
    "HeatFlow_mW_mg": "mW/mg",
}
IR_REQUIRED_HEADERS = ("Record_ID", "Wavenumber_cm-1", "Absorbance")
UV_VIS_REQUIRED_HEADERS = ("Record_ID", "Wavelength_nm", "Absorbance")
GPC_REQUIRED_HEADERS = ("Record_ID", "RetentionTime_min", "RI_mV")
PARTICLE_SIZE_HEADER_VARIANTS = (
    ("Record_ID", "ParticleDiameter_um", "VolumeFrequency_percent"),
    ("Record_ID", "ParticleSize_um", "Volume_percent"),
)
# Backward-compatible public name used by the initial TGA version.
REQUIRED_HEADERS = TGA_REQUIRED_HEADERS


class MeasurementDataError(ValueError):
    pass


class TgaDataError(MeasurementDataError):
    pass


class DscDataError(MeasurementDataError):
    pass


class IrDataError(MeasurementDataError):
    pass


class UvVisDataError(MeasurementDataError):
    pass


class GpcDataError(MeasurementDataError):
    pass


class ParticleSizeDataError(MeasurementDataError):
    pass


ErrorType = TypeVar("ErrorType", bound=MeasurementDataError)


def _read_text(path: Path, error_type: type[ErrorType]) -> str:
    decoding_errors: list[UnicodeDecodeError] = []
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            decoding_errors.append(exc)
    raise error_type(f"文字コードを判定できませんでした: {path.name}") from decoding_errors[-1]


def _open_csv(
    path: Path | str,
    required_headers: tuple[str, ...],
    error_type: type[ErrorType],
) -> tuple[Path, csv.DictReader]:
    file_path = Path(path)
    try:
        text = _read_text(file_path, error_type)
    except FileNotFoundError as exc:
        raise error_type(f"ファイルが見つかりません: {file_path}") from exc
    except PermissionError as exc:
        raise error_type(f"ファイルを読み取る権限がありません: {file_path}") from exc
    except OSError as exc:
        raise error_type(f"ファイルを読み取れません: {file_path} ({exc})") from exc

    if not text.strip():
        raise error_type(f"空のCSVファイルです: {file_path.name}")

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise error_type(f"ヘッダーがありません: {file_path.name}")
    normalized_fields = {field.strip(): field for field in reader.fieldnames if field is not None}
    missing = [name for name in required_headers if name not in normalized_fields]
    if missing:
        raise error_type(f"必須列が不足しています: {', '.join(missing)} ({file_path.name})")
    # DictReader keeps the original header spellings. Map canonical names to them.
    reader.canonical_fields = normalized_fields  # type: ignore[attr-defined]
    return file_path, reader


def _number(
    row: dict[str, str | None],
    key: str,
    canonical_name: str,
    file_path: Path,
    line_number: int,
    error_type: type[ErrorType],
) -> float:
    try:
        value = float(str(row.get(key, "")).strip())
    except (TypeError, ValueError) as exc:
        raise error_type(
            f"{file_path.name} の{line_number}行目: {canonical_name}が数値ではありません。"
        ) from exc
    if not math.isfinite(value):
        raise error_type(
            f"{file_path.name} の{line_number}行目: {canonical_name}が有限値ではありません。"
        )
    return value


def load_tga_csv(path: Path | str) -> CurveData:
    file_path, reader = _open_csv(path, TGA_REQUIRED_HEADERS, TgaDataError)
    fields = reader.canonical_fields  # type: ignore[attr-defined]
    time_key = fields["Time_min"]
    temp_key = fields["Temperature_C"]
    mass_key = fields["Mass_mg"]
    times: list[float] = []
    temperatures: list[float] = []
    masses: list[float] = []

    for row in reader:
        if not row or all(value is None or not str(value).strip() for value in row.values()):
            continue
        line_number = reader.line_num
        times.append(_number(row, time_key, "Time_min", file_path, line_number, TgaDataError))
        temperatures.append(
            _number(row, temp_key, "Temperature_C", file_path, line_number, TgaDataError)
        )
        masses.append(_number(row, mass_key, "Mass_mg", file_path, line_number, TgaDataError))

    if not temperatures:
        raise TgaDataError(f"データ行がありません: {file_path.name}")
    initial_mass = masses[0]
    if initial_mass <= 0:
        raise TgaDataError(f"先頭のMass_mgは0より大きい値にしてください: {file_path.name}")

    normalized = tuple(mass / initial_mass * 100.0 for mass in masses)
    return CurveData(
        path=file_path.resolve(),
        display_name=file_path.stem,
        temperatures=tuple(temperatures),
        mass_mg=tuple(masses),
        weight_percent=normalized,
        measurement_type=TGA,
        time_min=tuple(times),
    )


def load_dsc_csv(path: Path | str) -> CurveData:
    file_path, reader = _open_csv(path, ("Temperature_C",), DscDataError)
    fields = reader.canonical_fields  # type: ignore[attr-defined]
    heat_headers = [header for header in DSC_HEAT_FLOW_HEADERS if header in fields]
    if not heat_headers:
        accepted = ", ".join(DSC_HEAT_FLOW_HEADERS)
        raise DscDataError(
            f"熱流列がありません。対応列名: {accepted} ({file_path.name})"
        )
    if len(heat_headers) > 1:
        raise DscDataError(
            f"熱流列を一意に決定できません: {', '.join(heat_headers)} ({file_path.name})"
        )
    heat_header = heat_headers[0]
    time_key = fields.get("Time_min")
    temp_key = fields["Temperature_C"]
    heat_key = fields[heat_header]
    times: list[float] = []
    temperatures: list[float] = []
    heat_flow: list[float] = []

    for row in reader:
        if not row or all(value is None or not str(value).strip() for value in row.values()):
            continue
        line_number = reader.line_num
        if time_key is not None:
            times.append(_number(row, time_key, "Time_min", file_path, line_number, DscDataError))
        temperatures.append(
            _number(row, temp_key, "Temperature_C", file_path, line_number, DscDataError)
        )
        heat_flow.append(
            _number(row, heat_key, "HeatFlow_mW", file_path, line_number, DscDataError)
        )

    if not temperatures:
        raise DscDataError(f"データ行がありません: {file_path.name}")
    return CurveData(
        path=file_path.resolve(),
        display_name=file_path.stem,
        temperatures=tuple(temperatures),
        mass_mg=(),
        weight_percent=(),
        measurement_type=DSC,
        heat_flow_mw=tuple(heat_flow),
        time_min=tuple(times),
        heat_flow_unit=DSC_HEAT_FLOW_HEADERS[heat_header],
        source_heat_flow_header=heat_header,
    )


def load_ir_csv(path: Path | str) -> CurveData:
    """Load the fixed three-column absorbance demo format without guessing headers."""
    file_path, reader = _open_csv(path, IR_REQUIRED_HEADERS, IrDataError)
    fields = reader.canonical_fields  # type: ignore[attr-defined]
    wavenumber_key = fields["Wavenumber_cm-1"]
    absorbance_key = fields["Absorbance"]
    wavenumbers: list[float] = []
    absorbance: list[float] = []

    for row in reader:
        if not row or all(value is None or not str(value).strip() for value in row.values()):
            continue
        line_number = reader.line_num
        wavenumbers.append(
            _number(
                row,
                wavenumber_key,
                "Wavenumber_cm-1",
                file_path,
                line_number,
                IrDataError,
            )
        )
        absorbance.append(
            _number(
                row,
                absorbance_key,
                "Absorbance",
                file_path,
                line_number,
                IrDataError,
            )
        )

    if len(wavenumbers) < 2:
        raise IrDataError(f"IRデータ点が不足しています: {file_path.name}")
    return CurveData(
        path=file_path.resolve(),
        display_name=file_path.stem,
        temperatures=(),
        mass_mg=(),
        weight_percent=(),
        measurement_type=IR,
        wavenumbers_cm1=tuple(wavenumbers),
        absorbance=tuple(absorbance),
    )


def load_uvvis_csv(path: Path | str) -> CurveData:
    """Load fixed absorbance UV-Vis columns without guessing or conversion."""
    file_path, reader = _open_csv(path, UV_VIS_REQUIRED_HEADERS, UvVisDataError)
    fields = reader.canonical_fields  # type: ignore[attr-defined]
    record_key = fields["Record_ID"]
    wavelength_key = fields["Wavelength_nm"]
    absorbance_key = fields["Absorbance"]
    wavelengths: list[float] = []
    absorbance: list[float] = []

    for row in reader:
        if not row or all(value is None or not str(value).strip() for value in row.values()):
            continue
        line_number = reader.line_num
        _number(row, record_key, "Record_ID", file_path, line_number, UvVisDataError)
        wavelengths.append(
            _number(
                row, wavelength_key, "Wavelength_nm", file_path, line_number, UvVisDataError
            )
        )
        absorbance.append(
            _number(row, absorbance_key, "Absorbance", file_path, line_number, UvVisDataError)
        )

    if len(wavelengths) < 2:
        raise UvVisDataError(f"UV-Visデータ点が不足しています: {file_path.name}")
    return CurveData(
        path=file_path.resolve(),
        display_name=file_path.stem,
        temperatures=(),
        mass_mg=(),
        weight_percent=(),
        measurement_type=UV_VIS,
        wavelengths_nm=tuple(wavelengths),
        uvvis_absorbance=tuple(absorbance),
    )


def load_gpc_csv(path: Path | str) -> CurveData:
    """Load the fixed RI detector GPC columns without peak processing."""
    file_path, reader = _open_csv(path, GPC_REQUIRED_HEADERS, GpcDataError)
    fields = reader.canonical_fields  # type: ignore[attr-defined]
    record_key = fields["Record_ID"]
    time_key = fields["RetentionTime_min"]
    ri_key = fields["RI_mV"]
    retention_times: list[float] = []
    ri_signal: list[float] = []

    for row in reader:
        if not row or all(value is None or not str(value).strip() for value in row.values()):
            continue
        line_number = reader.line_num
        _number(row, record_key, "Record_ID", file_path, line_number, GpcDataError)
        retention_times.append(
            _number(
                row,
                time_key,
                "RetentionTime_min",
                file_path,
                line_number,
                GpcDataError,
            )
        )
        ri_signal.append(
            _number(row, ri_key, "RI_mV", file_path, line_number, GpcDataError)
        )

    if len(retention_times) < 2:
        raise GpcDataError(f"GPCデータ点が不足しています: {file_path.name}")
    return CurveData(
        path=file_path.resolve(),
        display_name=file_path.stem,
        temperatures=(),
        mass_mg=(),
        weight_percent=(),
        measurement_type=GPC,
        retention_times_min=tuple(retention_times),
        ri_signal_mv=tuple(ri_signal),
    )


def load_particle_size_csv(path: Path | str) -> CurveData:
    """Load one of the two explicitly supported particle-size CSV formats."""
    file_path, reader = _open_csv(path, ("Record_ID",), ParticleSizeDataError)
    fields = reader.canonical_fields  # type: ignore[attr-defined]
    selected_headers = next(
        (
            headers
            for headers in PARTICLE_SIZE_HEADER_VARIANTS
            if all(header in fields for header in headers)
        ),
        None,
    )
    if selected_headers is None:
        accepted = " または ".join(" / ".join(headers) for headers in PARTICLE_SIZE_HEADER_VARIANTS)
        raise ParticleSizeDataError(
            f"粒度分布CSVの必須列が不足しています。対応形式: {accepted} ({file_path.name})"
        )

    record_header, diameter_header, frequency_header = selected_headers
    record_key = fields[record_header]
    diameter_key = fields[diameter_header]
    frequency_key = fields[frequency_header]
    diameters: list[float] = []
    frequencies: list[float] = []

    for row in reader:
        if not row or all(value is None or not str(value).strip() for value in row.values()):
            continue
        line_number = reader.line_num
        _number(
            row,
            record_key,
            "Record_ID",
            file_path,
            line_number,
            ParticleSizeDataError,
        )
        diameter = _number(
            row,
            diameter_key,
            diameter_header,
            file_path,
            line_number,
            ParticleSizeDataError,
        )
        frequency = _number(
            row,
            frequency_key,
            frequency_header,
            file_path,
            line_number,
            ParticleSizeDataError,
        )
        if diameter <= 0:
            raise ParticleSizeDataError(
                f"{file_path.name} の{line_number}行目: 粒径は0より大きい値にしてください。"
            )
        if diameters and diameter <= diameters[-1]:
            if diameter == diameters[-1]:
                reason = f"粒径 {diameter:g} µm が重複しています。"
            else:
                reason = "粒径は厳密な昇順にしてください。"
            raise ParticleSizeDataError(
                f"{file_path.name} の{line_number}行目: {reason}"
            )
        diameters.append(diameter)
        frequencies.append(frequency)

    if len(diameters) < 2:
        raise ParticleSizeDataError(
            f"粒度分布データ点が不足しています（2点以上必要です）: {file_path.name}"
        )
    return CurveData(
        path=file_path.resolve(),
        display_name=file_path.stem,
        temperatures=(),
        mass_mg=(),
        weight_percent=(),
        measurement_type=PARTICLE_SIZE,
        particle_diameter_um=tuple(diameters),
        volume_frequency_percent=tuple(frequencies),
        source_particle_diameter_header=diameter_header,
        source_volume_frequency_header=frequency_header,
    )
