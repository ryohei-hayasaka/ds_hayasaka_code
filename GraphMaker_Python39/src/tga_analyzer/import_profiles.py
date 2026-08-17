from __future__ import annotations

from typing import Union

import csv
import fnmatch
import hashlib
import io
import json
import math
import os
import re
import sys
import threading
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .branding import APP_DATA_DIR_NAME
from .model import (
    DSC,
    GPC,
    IR,
    PARTICLE_SIZE,
    TGA,
    UV_VIS,
    CurveData,
    ImportProvenance,
    normalize_measurement_type,
    path_key,
)


AUTO = "auto"
ENCODINGS = (AUTO, "utf-8-sig", "utf-8", "cp932")
DELIMITER_NAMES = (AUTO, "comma", "tab", "semicolon")
DELIMITERS = {"comma": ",", "tab": "\t", "semicolon": ";"}
HEADER_MODES = (AUTO, "row", "keyword", "none")
START_MODES = ("header_next", "header_offset", "absolute", "keyword_offset")
END_MODES = (
    "eof",
    "mapped_blank",
    "absolute",
    "before_keyword",
    "first_non_numeric",
    "non_numeric_run",
)

ROLE_LABELS = {
    "x": "X列",
    "y": "Y列",
    "time": "時間列",
    "record_id": "Record ID列",
    "sample_mass": "試料重量列",
    "heating_rate": "昇温速度列",
}


class ImportProfileError(ValueError):
    """A profile, detection, or profiled CSV read could not be completed."""


class AmbiguousProfileError(ImportProfileError):
    def __init__(self, message: str, candidates: Iterable["ImportProfile"] = ()) -> None:
        super().__init__(message)
        self.candidates = tuple(candidates)


@dataclass(frozen=True)
class ColumnMapping:
    header: Union[str, None] = None
    column: Union[int, None] = None

    def validate(self, role: str) -> None:
        if self.column is not None and self.column < 1:
            raise ImportProfileError(f"{ROLE_LABELS.get(role, role)}の列番号は1以上にしてください。")
        if not (self.header or "").strip() and self.column is None:
            raise ImportProfileError(f"{ROLE_LABELS.get(role, role)}が指定されていません。")


@dataclass(frozen=True)
class MetadataRule:
    keyword: Union[str, None] = None
    row: Union[int, None] = None
    column: int = 2
    pattern: Union[str, None] = None
    unit: Union[str, None] = None


@dataclass(frozen=True)
class ImportProfile:
    profile_id: str
    name: str
    measurement_type: str
    enabled: bool = True
    encoding: str = AUTO
    delimiter: str = AUTO
    file_patterns: tuple[str, ...] = ("*.csv",)
    required_keywords: tuple[str, ...] = ()
    header_mode: str = AUTO
    header_row: Union[int, None] = None
    header_keyword: Union[str, None] = None
    start_mode: str = "header_next"
    start_row: Union[int, None] = None
    start_offset: int = 1
    start_keyword: Union[str, None] = None
    columns: dict[str, ColumnMapping] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    end_mode: str = "eof"
    end_row: Union[int, None] = None
    end_keyword: Union[str, None] = None
    non_numeric_count: int = 1
    metadata_rules: dict[str, MetadataRule] = field(default_factory=dict)
    version: int = 1
    created_at: str = ""
    built_in: bool = False
    skip_blank_rows: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "measurement_type", normalize_measurement_type(self.measurement_type))
        if not self.profile_id.strip():
            raise ImportProfileError("プロファイルIDは空にできません。")
        if not self.name.strip():
            raise ImportProfileError("プロファイル名は空にできません。")
        if self.encoding not in ENCODINGS:
            raise ImportProfileError(f"未対応の文字コード設定です: {self.encoding}")
        if self.delimiter not in DELIMITER_NAMES:
            raise ImportProfileError(f"未対応の区切り文字設定です: {self.delimiter}")
        if self.header_mode not in HEADER_MODES:
            raise ImportProfileError(f"未対応のヘッダー検出方式です: {self.header_mode}")
        if self.start_mode not in START_MODES:
            raise ImportProfileError(f"未対応のデータ開始方式です: {self.start_mode}")
        if self.end_mode not in END_MODES:
            raise ImportProfileError(f"未対応の終了条件です: {self.end_mode}")
        if self.header_row is not None and self.header_row < 1:
            raise ImportProfileError("ヘッダー行は1以上にしてください。")
        if self.start_row is not None and self.start_row < 1:
            raise ImportProfileError("データ開始行は1以上にしてください。")
        if self.end_row is not None and self.end_row < 1:
            raise ImportProfileError("データ終了行は1以上にしてください。")
        if self.non_numeric_count < 1:
            raise ImportProfileError("非数値行の連続回数は1以上にしてください。")
        for required in ("x", "y"):
            mapping = self.columns.get(required)
            if mapping is None:
                raise ImportProfileError(f"{ROLE_LABELS[required]}が指定されていません。")
            mapping.validate(required)
        x_map = self.columns["x"]
        y_map = self.columns["y"]
        if x_map.column is not None and x_map.column == y_map.column:
            raise ImportProfileError("X列とY列へ同じ列番号は指定できません。")
        if (
            x_map.column is None
            and y_map.column is None
            and (x_map.header or "").strip() == (y_map.header or "").strip()
        ):
            raise ImportProfileError("X列とY列へ同じヘッダー名は指定できません。")
        if self.header_mode == AUTO and (not x_map.header or not y_map.header):
            raise ImportProfileError(
                "ヘッダー自動検索ではX列とY列をヘッダー名で指定してください。"
            )

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("built_in", None)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["file_patterns"] = list(self.file_patterns)
        result["required_keywords"] = list(self.required_keywords)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, object], *, built_in: Union[bool, None] = None) -> "ImportProfile":
        payload = dict(data)
        raw_columns = payload.get("columns", {})
        if not isinstance(raw_columns, dict):
            raise ImportProfileError("columnsはJSONオブジェクトで指定してください。")
        payload["columns"] = {
            str(role): value if isinstance(value, ColumnMapping) else ColumnMapping(**value)
            for role, value in raw_columns.items()
            if isinstance(value, (dict, ColumnMapping))
        }
        raw_metadata = payload.get("metadata_rules", {})
        if not isinstance(raw_metadata, dict):
            raise ImportProfileError("metadata_rulesはJSONオブジェクトで指定してください。")
        payload["metadata_rules"] = {
            str(name): value if isinstance(value, MetadataRule) else MetadataRule(**value)
            for name, value in raw_metadata.items()
            if isinstance(value, (dict, MetadataRule))
        }
        payload["file_patterns"] = tuple(payload.get("file_patterns", ("*.csv",)))
        payload["required_keywords"] = tuple(payload.get("required_keywords", ()))
        if built_in is not None:
            payload["built_in"] = built_in
        try:
            return cls(**payload)
        except TypeError as exc:
            raise ImportProfileError(f"プロファイル項目が不正です: {exc}") from exc


@dataclass(frozen=True)
class PreviewRow:
    line_number: int
    values: tuple[str, ...]


@dataclass(frozen=True)
class ImportPreview:
    path: Path
    encoding: str
    delimiter: str
    rows: tuple[PreviewRow, ...]
    truncated: bool
    header_candidates: tuple[int, ...] = ()
    resolved_header_row: Union[int, None] = None
    resolved_start_row: Union[int, None] = None
    column_indices: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportTestResult:
    curve: CurveData
    profile: ImportProfile
    header_row: Union[int, None]
    data_start_row: int
    data_end_row: int
    point_count: int
    first_x: float
    last_x: float
    first_y: float
    last_y: float
    columns: dict[str, str]
    units: dict[str, str]
    metadata: dict[str, Union[float, str]]
    encoding: str
    delimiter: str
    warnings: tuple[str, ...] = ()

    def summary(self) -> str:
        header = "なし" if self.header_row is None else str(self.header_row)
        unit_text = ", ".join(f"{key}={value}" for key, value in self.units.items()) or "なし"
        metadata_text = ", ".join(f"{key}={value}" for key, value in self.metadata.items()) or "なし"
        warning_text = " / ".join(self.warnings) or "なし"
        return (
            f"ヘッダー行: {header}\n"
            f"データ範囲: {self.data_start_row}～{self.data_end_row}行\n"
            f"点数: {self.point_count}\n"
            f"X: {self.first_x:g} → {self.last_x:g}\n"
            f"Y: {self.first_y:g} → {self.last_y:g}\n"
            f"列: {', '.join(f'{key}={value}' for key, value in self.columns.items())}\n"
            f"単位: {unit_text}\n"
            f"メタデータ: {metadata_text}\n"
            f"文字コード: {self.encoding}\n"
            f"区切り文字: {delimiter_display(self.delimiter)}\n"
            f"警告: {warning_text}"
        )


def default_user_profiles_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_DATA_DIR_NAME / "profiles"


def bundled_profiles_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")) / "profiles"
    return Path(__file__).resolve().parents[2] / "profiles"


class ProfileStore:
    """Load immutable bundled profiles and editable per-user JSON profiles."""

    def __init__(
        self,
        built_in_dir: Union[Path, None] = None,
        user_dir: Union[Path, None] = None,
    ) -> None:
        self.built_in_dir = built_in_dir or bundled_profiles_dir()
        self.user_dir = user_dir or default_user_profiles_dir()
        self._profiles: dict[str, ImportProfile] = {}
        self.errors: list[str] = []
        self.reload()

    def reload(self) -> None:
        profiles: dict[str, ImportProfile] = {}
        errors: list[str] = []
        for directory, built_in in ((self.built_in_dir, True), (self.user_dir, False)):
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.json"), key=lambda item: item.name.casefold()):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        raise ImportProfileError("JSONの最上位はオブジェクトにしてください。")
                    profile = ImportProfile.from_dict(raw, built_in=built_in)
                    if profile.profile_id in profiles:
                        owner = "組み込み" if profiles[profile.profile_id].built_in else "ユーザー"
                        raise ImportProfileError(
                            f"ID '{profile.profile_id}' は既存の{owner}プロファイルと重複しています。"
                        )
                    profiles[profile.profile_id] = profile
                except (OSError, UnicodeError, json.JSONDecodeError, ImportProfileError) as exc:
                    errors.append(f"{path.name}: {exc}")
        self._profiles = profiles
        self.errors = errors

    def all(self, measurement_type: Union[str, None] = None, *, enabled_only: bool = True) -> tuple[ImportProfile, ...]:
        mode = normalize_measurement_type(measurement_type) if measurement_type else None
        return tuple(
            profile
            for profile in self._profiles.values()
            if (mode is None or profile.measurement_type == mode)
            and (profile.enabled or not enabled_only)
        )

    def get(self, profile_id: str) -> Union[ImportProfile, None]:
        return self._profiles.get(profile_id)

    def save(self, profile: ImportProfile) -> ImportProfile:
        existing = self._profiles.get(profile.profile_id)
        if profile.built_in or (existing is not None and existing.built_in):
            raise ImportProfileError("組み込みプロファイルは上書きできません。複製して保存してください。")
        self.user_dir.mkdir(parents=True, exist_ok=True)
        saved = replace(
            profile,
            built_in=False,
            created_at=profile.created_at or datetime.now(timezone.utc).isoformat(),
        )
        destination = self.user_dir / f"{_safe_profile_filename(saved.profile_id)}.json"
        temporary = destination.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(saved.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError as exc:
            raise ImportProfileError(f"プロファイルを保存できません: {destination} ({exc})") from exc
        self.reload()
        return self._profiles[saved.profile_id]

    def duplicate(self, profile_id: str, name: Union[str, None] = None) -> ImportProfile:
        source = self._profiles.get(profile_id)
        if source is None:
            raise ImportProfileError(f"プロファイルが見つかりません: {profile_id}")
        new_id = f"user-{uuid.uuid4()}"
        return replace(
            source,
            profile_id=new_id,
            name=(name or f"{source.name} のコピー").strip(),
            built_in=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def delete(self, profile_id: str) -> None:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ImportProfileError(f"プロファイルが見つかりません: {profile_id}")
        if profile.built_in:
            raise ImportProfileError("組み込みプロファイルは削除できません。")
        destination = self.user_dir / f"{_safe_profile_filename(profile.profile_id)}.json"
        try:
            destination.unlink()
        except FileNotFoundError:
            matching = []
            for path in self.user_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if isinstance(data, dict) and data.get("profile_id") == profile.profile_id:
                    matching.append(path)
            if not matching:
                raise ImportProfileError(f"プロファイルファイルが見つかりません: {profile.profile_id}")
            matching[0].unlink()
        except OSError as exc:
            raise ImportProfileError(f"プロファイルを削除できません: {exc}") from exc
        self.reload()


def _safe_profile_filename(profile_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_id).strip("._")
    return normalized or f"profile-{uuid.uuid4()}"


def delimiter_display(delimiter: str) -> str:
    return {",": "カンマ", "\t": "タブ", ";": "セミコロン"}.get(delimiter, delimiter)


def column_letter(column: int) -> str:
    if column < 1:
        raise ValueError("column must be one-based")
    result = ""
    value = column
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _read_preview_bytes(path: Path, max_lines: int = 300) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    try:
        with path.open("rb") as stream:
            for _ in range(max_lines):
                line = stream.readline()
                if not line:
                    return b"".join(chunks), False
                chunks.append(line)
            truncated = bool(stream.readline())
    except FileNotFoundError as exc:
        raise ImportProfileError(f"ファイルが見つかりません: {path}") from exc
    except PermissionError as exc:
        raise ImportProfileError(f"ファイルを読み取る権限がありません: {path}") from exc
    except OSError as exc:
        raise ImportProfileError(f"ファイルを読み取れません: {path} ({exc})") from exc
    return b"".join(chunks), truncated


def _read_all_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise ImportProfileError(f"ファイルが見つかりません: {path}") from exc
    except PermissionError as exc:
        raise ImportProfileError(f"ファイルを読み取る権限がありません: {path}") from exc
    except OSError as exc:
        raise ImportProfileError(f"ファイルを読み取れません: {path} ({exc})") from exc


def _decode_bytes(data: bytes, requested: str, filename: str) -> tuple[str, str]:
    if not data or not data.strip():
        raise ImportProfileError(f"空のCSVファイルです: {filename}")
    candidates = ("utf-8-sig", "utf-8", "cp932") if requested == AUTO else (requested,)
    failures: list[UnicodeDecodeError] = []
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            failures.append(exc)
    raise ImportProfileError(f"文字コードを判定できませんでした: {filename}") from failures[-1]


def _parse_csv_rows(text: str, delimiter: str) -> list[PreviewRow]:
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows: list[PreviewRow] = []
    try:
        for values in reader:
            rows.append(PreviewRow(reader.line_num, tuple(values)))
    except csv.Error as exc:
        raise ImportProfileError(f"CSVを解析できません（{reader.line_num}行目）: {exc}") from exc
    return rows


def _detect_delimiter(text: str, filename: str) -> str:
    sample_lines = [line for line in text.splitlines()[:100] if line.strip()]
    scores: dict[str, tuple[int, int, int]] = {}
    for delimiter in DELIMITERS.values():
        try:
            rows = list(csv.reader(sample_lines, delimiter=delimiter))
        except csv.Error:
            continue
        widths = [len(row) for row in rows if len(row) > 1]
        if not widths:
            continue
        most_common_width, consistent = Counter(widths).most_common(1)[0]
        scores[delimiter] = (consistent, most_common_width, sum(widths))
    if not scores:
        raise ImportProfileError(f"区切り文字を判定できません: {filename}")
    best_score = max(scores.values())
    winners = [delimiter for delimiter, score in scores.items() if score == best_score]
    if len(winners) != 1:
        labels = "、".join(delimiter_display(item) for item in winners)
        raise ImportProfileError(
            f"区切り文字を一意に判定できません（候補: {labels}）: {filename}"
        )
    return winners[0]


def _delimiter_for(profile: ImportProfile, text: str, filename: str) -> str:
    if profile.delimiter == AUTO:
        return _detect_delimiter(text, filename)
    return DELIMITERS[profile.delimiter]


def preview_csv(path: Union[Path, str], profile: Union[ImportProfile, None] = None) -> ImportPreview:
    file_path = Path(path)
    data, truncated = _read_preview_bytes(file_path, 300)
    requested_encoding = profile.encoding if profile else AUTO
    text, encoding = _decode_bytes(data, requested_encoding, file_path.name)
    delimiter = _delimiter_for(profile, text, file_path.name) if profile else _detect_delimiter(text, file_path.name)
    rows = _parse_csv_rows(text, delimiter)
    if not rows:
        raise ImportProfileError(f"空のCSVファイルです: {file_path.name}")
    if profile is None:
        return ImportPreview(file_path, encoding, delimiter, tuple(rows), truncated)
    header_candidates = _header_candidates(rows, profile)
    header_row, start_row, indices = _resolve_layout(rows, profile)
    return ImportPreview(
        file_path,
        encoding,
        delimiter,
        tuple(rows),
        truncated,
        tuple(header_candidates),
        header_row,
        start_row,
        indices,
    )


def _row_text(row: PreviewRow) -> str:
    return "\t".join(row.values)


def _find_keyword_rows(rows: list[PreviewRow], keyword: str, *, start: int = 1) -> list[int]:
    needle = keyword.strip()
    if not needle:
        raise ImportProfileError("検索キーワードが指定されていません。")
    return [row.line_number for row in rows if row.line_number >= start and needle in _row_text(row)]


def _row_by_line(rows: list[PreviewRow], line_number: int) -> Union[PreviewRow, None]:
    return next((row for row in rows if row.line_number == line_number), None)


def _column_indices(header: Union[PreviewRow, None], profile: ImportProfile) -> dict[str, int]:
    indices: dict[str, int] = {}
    normalized_headers = [value.strip() for value in header.values] if header is not None else []
    for role, mapping in profile.columns.items():
        if mapping.column is not None:
            index = mapping.column
        else:
            wanted = (mapping.header or "").strip()
            matches = [position + 1 for position, value in enumerate(normalized_headers) if value == wanted]
            if not matches:
                if role in {"x", "y"}:
                    raise ImportProfileError(f"必須列がありません: {wanted or ROLE_LABELS.get(role, role)}")
                continue
            if len(matches) > 1:
                raise ImportProfileError(f"列名 '{wanted}' が複数あり一意に決定できません。")
            index = matches[0]
        indices[role] = index
    if "x" not in indices or "y" not in indices:
        raise ImportProfileError("X列またはY列がありません。")
    if indices["x"] == indices["y"]:
        raise ImportProfileError("X列とY列へ同じ列は指定できません。")
    return indices


def _cell(row: PreviewRow, column: int) -> str:
    return row.values[column - 1].strip() if column <= len(row.values) else ""


def _finite_number(value: str) -> Union[float, None]:
    try:
        number = float(value.strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _has_numeric_run(
    rows: list[PreviewRow], start_line: int, indices: dict[str, int], count: int = 3
) -> bool:
    found = 0
    for row in rows:
        if row.line_number < start_line:
            continue
        x = _finite_number(_cell(row, indices["x"]))
        y = _finite_number(_cell(row, indices["y"]))
        if x is None or y is None:
            return False
        found += 1
        if found >= count:
            return True
    return False


def _candidate_start_line(
    rows: list[PreviewRow], profile: ImportProfile, header_line: Union[int, None]
) -> int:
    if profile.start_mode == "header_next":
        if header_line is None:
            raise ImportProfileError("ヘッダーなし形式では『ヘッダーの次』を開始位置にできません。")
        return header_line + 1
    if profile.start_mode == "header_offset":
        if header_line is None:
            raise ImportProfileError("ヘッダーなし形式ではヘッダー相対位置を指定できません。")
        return header_line + profile.start_offset
    if profile.start_mode == "absolute":
        if profile.start_row is None:
            raise ImportProfileError("データ開始行が指定されていません。")
        return profile.start_row
    matches = _find_keyword_rows(rows, profile.start_keyword or "")
    if not matches:
        raise ImportProfileError(f"開始キーワードが見つかりません: {profile.start_keyword}")
    if len(matches) > 1:
        raise ImportProfileError(f"開始キーワードが複数あり一意に決定できません: {profile.start_keyword}")
    return matches[0] + profile.start_offset


def _header_candidates(rows: list[PreviewRow], profile: ImportProfile) -> list[int]:
    if profile.header_mode != AUTO:
        return []
    candidates: list[int] = []
    for row in rows:
        try:
            indices = _column_indices(row, profile)
            start_line = _candidate_start_line(rows, profile, row.line_number)
        except ImportProfileError:
            continue
        if _has_numeric_run(rows, start_line, indices, 3):
            candidates.append(row.line_number)
    return candidates


def _resolve_header(rows: list[PreviewRow], profile: ImportProfile) -> Union[int, None]:
    if profile.header_mode == "none":
        return None
    if profile.header_mode == "row":
        if profile.header_row is None:
            raise ImportProfileError("ヘッダー行が指定されていません。")
        if _row_by_line(rows, profile.header_row) is None:
            raise ImportProfileError(f"ヘッダー行がファイル範囲外です: {profile.header_row}行目")
        return profile.header_row
    if profile.header_mode == "keyword":
        matches = _find_keyword_rows(rows, profile.header_keyword or "")
        if not matches:
            raise ImportProfileError(f"ヘッダーキーワードが見つかりません: {profile.header_keyword}")
        valid: list[int] = []
        for line in matches:
            row = _row_by_line(rows, line)
            try:
                _column_indices(row, profile)
            except ImportProfileError:
                continue
            valid.append(line)
        if not valid:
            raise ImportProfileError("キーワード行に必要なX/Y列がありません。")
        if len(valid) > 1:
            raise ImportProfileError("ヘッダー候補が複数あり一意に決定できません。")
        return valid[0]
    candidates = _header_candidates(
        [row for row in rows if row.line_number <= 300], profile
    )
    if not candidates:
        raise ImportProfileError("ヘッダー候補がありません。直後のX/Y数値データ3行も確認してください。")
    if len(candidates) > 1:
        raise ImportProfileError(
            "ヘッダー候補が複数あり一意に決定できません: "
            + ", ".join(f"{line}行目" for line in candidates)
        )
    return candidates[0]


def _resolve_layout(
    rows: list[PreviewRow], profile: ImportProfile
) -> tuple[Union[int, None], int, dict[str, int]]:
    if not rows:
        raise ImportProfileError("CSVに行がありません。")
    header_line = _resolve_header(rows, profile)
    header = _row_by_line(rows, header_line) if header_line is not None else None
    indices = _column_indices(header, profile)
    start_line = _candidate_start_line(rows, profile, header_line)
    last_line = rows[-1].line_number
    if start_line < 1 or start_line > last_line:
        raise ImportProfileError(
            f"データ開始行がファイル範囲外です: {start_line}行目（最終行: {last_line}）"
        )
    return header_line, start_line, indices


def _profile_file_matches(path: Path, profile: ImportProfile) -> bool:
    return not profile.file_patterns or any(
        fnmatch.fnmatch(path.name.casefold(), pattern.casefold()) for pattern in profile.file_patterns
    )


def _match_profile(path: Path, profile: ImportProfile) -> int:
    if not profile.enabled or not _profile_file_matches(path, profile):
        raise ImportProfileError("ファイル名パターンが一致しません。")
    preview = preview_csv(path, profile)
    joined = "\n".join(_row_text(row) for row in preview.rows)
    missing = [word for word in profile.required_keywords if word not in joined]
    if missing:
        raise ImportProfileError(f"必須キーワードがありません: {', '.join(missing)}")
    if preview.resolved_start_row is None or not _has_numeric_run(
        list(preview.rows), preview.resolved_start_row, preview.column_indices, 3
    ):
        raise ImportProfileError("開始位置後にX/Y数値データが3行連続していません。")
    score = 30 + len(profile.required_keywords) * 4
    score += sum(3 for mapping in profile.columns.values() if mapping.header)
    score += sum(5 for pattern in profile.file_patterns if pattern not in {"*", "*.csv"})
    if profile.delimiter != AUTO:
        score += 2
    if profile.encoding != AUTO:
        score += 1
    return score


def detect_profile(
    path: Union[Path, str],
    measurement_type: str,
    profiles: Iterable[ImportProfile],
) -> ImportProfile:
    file_path = Path(path)
    mode = normalize_measurement_type(measurement_type)
    candidates: list[tuple[int, ImportProfile]] = []
    for profile in profiles:
        if profile.measurement_type != mode or not profile.enabled:
            continue
        try:
            score = _match_profile(file_path, profile)
        except ImportProfileError:
            continue
        candidates.append((score, profile))
    if not candidates:
        raise ImportProfileError(
            f"{file_path.name}: 適用できる{mode}読込プロファイルがありません。［読込設定］で確認してください。"
        )
    candidates.sort(key=lambda item: (-item[0], item[1].name.casefold()))
    best_score = candidates[0][0]
    best = [profile for score, profile in candidates if score == best_score]
    if len(best) != 1:
        raise AmbiguousProfileError(
            f"{file_path.name}: 読込プロファイル候補が複数あり自動適用できません: "
            + ", ".join(profile.name for profile in best),
            best,
        )
    return best[0]


def _parse_number(
    row: PreviewRow,
    column: int,
    role: str,
    path: Path,
) -> float:
    raw = _cell(row, column)
    number = _finite_number(raw)
    if number is None:
        label = ROLE_LABELS.get(role, role)
        header = f"{label}（{column_letter(column)}列／{column}列目）"
        raise ImportProfileError(
            f"{path.name} の{row.line_number}行目: {header}が数値ではありません。値: {raw!r}"
        )
    return number


def _unit_key(value: Union[str, None]) -> str:
    return (value or "").strip().replace(" ", "").replace("μ", "µ").casefold()


def _convert_x(mode: str, value: float, unit: Union[str, None]) -> float:
    key = _unit_key(unit)
    if mode in {TGA, DSC}:
        if key in {"°c", "℃", "c"}:
            return value
        if key == "k":
            return value - 273.15
    elif mode == IR and key in {"cm-1", "cm^-1", "cm⁻¹"}:
        return value
    elif mode == UV_VIS:
        if key == "nm":
            return value
        if key in {"µm", "um"}:
            return value * 1000.0
    elif mode == GPC:
        if key in {"min", "minute", "minutes"}:
            return value
        if key in {"s", "sec", "second", "seconds"}:
            return value / 60.0
    elif mode == PARTICLE_SIZE:
        if key in {"µm", "um"}:
            return value
        if key == "nm":
            return value / 1000.0
    raise ImportProfileError(f"{mode}のX単位を認識できません: {unit or '未指定'}")


def _convert_y(mode: str, value: float, unit: Union[str, None]) -> tuple[float, Union[str, None]]:
    key = _unit_key(unit)
    if mode == TGA:
        if key == "mg":
            return value, "mg"
        if key == "g":
            return value * 1000.0, "mg"
    elif mode == DSC:
        canonical = {
            "mw": "mW",
            "w/g": "W/g",
            "w·g-1": "W/g",
            "mw/mg": "mW/mg",
        }.get(key)
        if canonical is not None:
            return value, canonical
        if key in {"", "unknown", "不明"}:
            return value, None
    elif mode in {IR, UV_VIS} and key in {"abs", "absorbance", "au", "a.u.", "a.u"}:
        return value, "Absorbance"
    elif mode == GPC and key in {"mv", "ri_mv", "rimv"}:
        return value, "mV"
    elif mode == PARTICLE_SIZE and key in {"%", "percent", "vol%", "volume%"}:
        return value, "%"
    raise ImportProfileError(f"{mode}のY単位を認識できません: {unit or '未指定'}")


def _extract_metadata(
    rows: list[PreviewRow], profile: ImportProfile
) -> dict[str, Union[float, str]]:
    metadata: dict[str, Union[float, str]] = {}
    for name, rule in profile.metadata_rules.items():
        target: Union[PreviewRow, None] = None
        if rule.row is not None:
            target = _row_by_line(rows, rule.row)
        elif rule.keyword:
            matches = _find_keyword_rows(rows[:300], rule.keyword)
            if len(matches) == 1:
                target = _row_by_line(rows, matches[0])
        if target is None:
            continue
        raw = _cell(target, rule.column)
        if rule.pattern:
            match = re.search(rule.pattern, raw)
            if match is None:
                continue
            raw = match.group(1) if match.groups() else match.group(0)
        number = _finite_number(raw)
        metadata[name] = number if number is not None else raw
    return metadata


def _read_profiled_data(path: Path, profile: ImportProfile) -> ImportTestResult:
    data = _read_all_bytes(path)
    text, encoding = _decode_bytes(data, profile.encoding, path.name)
    delimiter = _delimiter_for(profile, text[: min(len(text), 256_000)], path.name)
    rows = _parse_csv_rows(text, delimiter)
    if not rows:
        raise ImportProfileError(f"空のCSVファイルです: {path.name}")
    header_line, start_line, indices = _resolve_layout(rows, profile)
    last_file_line = rows[-1].line_number
    if profile.end_mode == "absolute":
        if profile.end_row is None:
            raise ImportProfileError("データ終了行が指定されていません。")
        if profile.end_row < start_line:
            raise ImportProfileError(
                f"データ終了行が開始行以前です: 開始={start_line}、終了={profile.end_row}"
            )
        if profile.end_row > last_file_line:
            raise ImportProfileError(
                f"データ終了行がファイル範囲外です: {profile.end_row}行目（最終行: {last_file_line}）"
            )
        hard_end = profile.end_row
    elif profile.end_mode == "before_keyword":
        matches = _find_keyword_rows(rows, profile.end_keyword or "", start=start_line)
        if not matches:
            raise ImportProfileError(f"終了キーワードが見つかりません: {profile.end_keyword}")
        hard_end = matches[0] - 1
        if hard_end < start_line:
            raise ImportProfileError("終了キーワードがデータ開始行以前にあります。")
    else:
        hard_end = last_file_line

    x_values: list[float] = []
    y_values: list[float] = []
    aux_values: dict[str, list[float]] = {
        role: [] for role in indices if role not in {"x", "y"}
    }
    actual_start: Union[int, None] = None
    actual_end: Union[int, None] = None
    pending_bad: list[PreviewRow] = []

    for row in rows:
        if row.line_number < start_line or row.line_number > hard_end:
            continue
        x_raw = _cell(row, indices["x"])
        y_raw = _cell(row, indices["y"])
        both_blank = not x_raw and not y_raw
        if both_blank and profile.end_mode == "mapped_blank":
            break
        if both_blank and profile.skip_blank_rows:
            continue
        x_number = _finite_number(x_raw)
        y_number = _finite_number(y_raw)
        invalid = x_number is None or y_number is None
        if invalid and profile.end_mode == "first_non_numeric":
            break
        if invalid and profile.end_mode == "non_numeric_run":
            pending_bad.append(row)
            if len(pending_bad) >= profile.non_numeric_count:
                break
            continue
        if not invalid and pending_bad:
            first = pending_bad[0]
            raise ImportProfileError(
                f"{path.name} の{first.line_number}行目: 非数値行が"
                f"{profile.non_numeric_count}行連続する前に数値データが再開しました。"
            )
        if invalid:
            _parse_number(row, indices["x"], "x", path)
            _parse_number(row, indices["y"], "y", path)
        x_values.append(float(x_number))
        y_values.append(float(y_number))
        for role, column in indices.items():
            if role in {"x", "y"}:
                continue
            aux_values[role].append(_parse_number(row, column, role, path))
        actual_start = row.line_number if actual_start is None else actual_start
        actual_end = row.line_number

    if pending_bad and len(pending_bad) < profile.non_numeric_count:
        first = pending_bad[0]
        raise ImportProfileError(
            f"{path.name} の{first.line_number}行目: 非数値行が終了条件の"
            f"{profile.non_numeric_count}行に達していません。"
        )
    if len(x_values) < 2 or actual_start is None or actual_end is None:
        raise ImportProfileError(f"数値データが不足しています（2点以上必要です）: {path.name}")

    converted_x = tuple(_convert_x(profile.measurement_type, value, profile.units.get("x")) for value in x_values)
    converted_y_values: list[float] = []
    canonical_y_unit: Union[str, None] = None
    for value in y_values:
        converted, canonical = _convert_y(profile.measurement_type, value, profile.units.get("y"))
        converted_y_values.append(converted)
        canonical_y_unit = canonical
    converted_y = tuple(converted_y_values)
    warnings: list[str] = []
    if profile.measurement_type == DSC and canonical_y_unit is None:
        warnings.append("熱流単位を特定できないため、融解エンタルピーは算出できません。")

    metadata = _extract_metadata(rows, profile)
    for role in ("sample_mass", "heating_rate"):
        values = aux_values.get(role)
        if values:
            metadata[role] = values[0]
    if isinstance(metadata.get("sample_mass"), (int, float)):
        sample_unit = _unit_key(profile.units.get("sample_mass", "mg"))
        if sample_unit == "g":
            metadata["sample_mass"] = float(metadata["sample_mass"]) * 1000.0
        elif sample_unit != "mg":
            raise ImportProfileError(
                f"DSCの試料重量単位を認識できません: {profile.units.get('sample_mass') or '未指定'}"
            )
    if isinstance(metadata.get("heating_rate"), (int, float)):
        rate_unit = _unit_key(profile.units.get("heating_rate", "°c/min"))
        if rate_unit not in {"°c/min", "℃/min", "c/min", "k/min"}:
            raise ImportProfileError(
                f"DSCの昇温速度単位を認識できません: {profile.units.get('heating_rate') or '未指定'}"
            )
    provenance = ImportProvenance(
        profile_id=profile.profile_id,
        profile_name=profile.name,
        profile_fingerprint=profile.fingerprint,
        header_row=header_line,
        data_start_row=actual_start,
        data_end_row=actual_end,
        x_column=_column_description(indices["x"], _row_by_line(rows, header_line)),
        y_column=_column_description(indices["y"], _row_by_line(rows, header_line)),
        encoding=encoding,
        delimiter=delimiter,
        warnings=tuple(warnings),
    )
    curve = _curve_from_values(
        path,
        profile,
        converted_x,
        converted_y,
        aux_values,
        canonical_y_unit,
        metadata,
        provenance,
    )
    columns = {
        role: _column_description(column, _row_by_line(rows, header_line))
        for role, column in indices.items()
    }
    return ImportTestResult(
        curve=curve,
        profile=profile,
        header_row=header_line,
        data_start_row=actual_start,
        data_end_row=actual_end,
        point_count=len(converted_x),
        first_x=converted_x[0],
        last_x=converted_x[-1],
        first_y=converted_y[0],
        last_y=converted_y[-1],
        columns=columns,
        units=dict(profile.units),
        metadata=metadata,
        encoding=encoding,
        delimiter=delimiter,
        warnings=tuple(warnings),
    )


def _column_description(column: int, header: Union[PreviewRow, None]) -> str:
    name = _cell(header, column) if header is not None else ""
    prefix = f"{column_letter(column)}列（{column}）"
    return f"{prefix}: {name}" if name else prefix


def _curve_from_values(
    path: Path,
    profile: ImportProfile,
    x: tuple[float, ...],
    y: tuple[float, ...],
    aux: dict[str, list[float]],
    y_unit: Union[str, None],
    metadata: dict[str, Union[float, str]],
    provenance: ImportProvenance,
) -> CurveData:
    common = dict(
        path=path.resolve(),
        display_name=path.stem,
        measurement_type=profile.measurement_type,
        import_provenance=provenance,
    )
    if profile.measurement_type == TGA:
        if y[0] <= 0:
            raise ImportProfileError(f"先頭の質量は0より大きい値にしてください: {path.name}")
        normalized = tuple(value / y[0] * 100.0 for value in y)
        return CurveData(
            temperatures=x,
            mass_mg=y,
            weight_percent=normalized,
            time_min=tuple(aux.get("time", ())),
            **common,
        )
    if profile.measurement_type == DSC:
        sample_mass = metadata.get("sample_mass")
        heating_rate = metadata.get("heating_rate")
        return CurveData(
            temperatures=x,
            mass_mg=(),
            weight_percent=(),
            heat_flow_mw=y,
            time_min=tuple(aux.get("time", ())),
            heat_flow_unit=y_unit,
            source_heat_flow_header=profile.columns["y"].header,
            sample_mass_mg=float(sample_mass) if isinstance(sample_mass, (int, float)) else None,
            heating_rate_c_min=float(heating_rate) if isinstance(heating_rate, (int, float)) else None,
            **common,
        )
    if profile.measurement_type == IR:
        return CurveData(
            temperatures=(), mass_mg=(), weight_percent=(),
            wavenumbers_cm1=x, absorbance=y, **common,
        )
    if profile.measurement_type == UV_VIS:
        return CurveData(
            temperatures=(), mass_mg=(), weight_percent=(),
            wavelengths_nm=x, uvvis_absorbance=y, **common,
        )
    if profile.measurement_type == GPC:
        return CurveData(
            temperatures=(), mass_mg=(), weight_percent=(),
            retention_times_min=x, ri_signal_mv=y, **common,
        )
    if any(value <= 0 for value in x):
        raise ImportProfileError(f"粒径は0より大きい値にしてください: {path.name}")
    for previous, current in zip(x, x[1:]):
        if current <= previous:
            reason = f"粒径 {current:g} µm が重複しています。" if current == previous else "粒径は厳密な昇順にしてください。"
            raise ImportProfileError(f"{path.name}: {reason}")
    return CurveData(
        temperatures=(), mass_mg=(), weight_percent=(),
        particle_diameter_um=x,
        volume_frequency_percent=y,
        source_particle_diameter_header=profile.columns["x"].header,
        source_volume_frequency_header=profile.columns["y"].header,
        **common,
    )


def test_import(path: Union[Path, str], profile: ImportProfile) -> ImportTestResult:
    return _read_profiled_data(Path(path), profile)


def load_curve_with_profile(path: Union[Path, str], profile: ImportProfile) -> CurveData:
    return test_import(path, profile).curve


class ProfiledCurveLoader:
    """Profile selection plus a profile-aware, file-version-aware curve cache."""

    def __init__(self, store: ProfileStore) -> None:
        self.store = store
        self._cache: dict[tuple[str, int, int, str], CurveData] = {}
        self._lock = threading.Lock()
        self.cache_hits = 0

    def load(
        self,
        path: Union[Path, str],
        measurement_type: str,
        profile: Union[ImportProfile, None] = None,
    ) -> CurveData:
        file_path = Path(path)
        selected = profile or detect_profile(
            file_path, measurement_type, self.store.all(measurement_type)
        )
        try:
            stat = file_path.stat()
        except FileNotFoundError as exc:
            raise ImportProfileError(f"ファイルが見つかりません: {file_path}") from exc
        except PermissionError as exc:
            raise ImportProfileError(f"ファイルを読み取る権限がありません: {file_path}") from exc
        key = (path_key(file_path), stat.st_mtime_ns, stat.st_size, selected.fingerprint)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.cache_hits += 1
                return replace(cached)
        curve = load_curve_with_profile(file_path, selected)
        with self._lock:
            self._cache[key] = curve
        return replace(curve)

    def invalidate(self, path: Union[Path, str, None] = None) -> None:
        with self._lock:
            if path is None:
                self._cache.clear()
                return
            wanted = path_key(path)
            self._cache = {key: value for key, value in self._cache.items() if key[0] != wanted}
