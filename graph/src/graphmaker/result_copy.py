from __future__ import annotations

from typing import Sequence


def rows_to_tsv(
    headers: Sequence[str], rows: Sequence[Sequence[object]], include_header: bool = True
) -> str:
    if not rows:
        raise ValueError("コピーできる解析結果がありません。")
    width = len(headers)
    if any(len(row) != width for row in rows):
        raise ValueError("解析結果の列数が一致していません。")
    output = []
    if include_header:
        output.append("\t".join(str(value) for value in headers))
    output.extend("\t".join(str(value) for value in row) for row in rows)
    return "\n".join(output)
