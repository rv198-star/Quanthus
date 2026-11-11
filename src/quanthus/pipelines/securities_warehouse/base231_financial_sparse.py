"""Recover dated financial cells beside explicitly undated F10 columns.

The frozen literal evaluator and its accepted images remain byte-identical.
This successor accounts for every original cell before deriving a dated view;
undated values are held, never assigned a period. Only previously invalid frames
are emitted. All file/body/block identities in the output refer to original bytes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base231_financial_summary import (
    _DAY,
    _frames,
    _matrix,
    financial_literal_identity,
    summary_observation,
)
from .financial_reporting import FinancialCompanyResolver

CONTRACT_ID = "p10-base231-financial-explicit-undated-columns-v1"
_UNDATED = "-"
_EMPTY = {"", "-", "--", "—"}


def _dated_view(lines: list[str]) -> tuple[list[str], dict[str, Any]]:
    if not lines[0].strip().startswith("┌") or not lines[-1].strip().startswith("└"):
        raise BoundedError("SPARSE_FINANCIAL_FRAME_INCOMPLETE")
    headers = [_cells(line) for line in lines if _cells(line)[:1] == ["财务指标"]]
    if len(headers) != 1:
        raise BoundedError("SPARSE_FINANCIAL_DATE_HEADER_AMBIGUOUS")
    header = headers[0]
    if (not 2 <= len(header) <= 17 or _UNDATED not in header[1:]
            or any(v != _UNDATED and not _DAY.fullmatch(v) for v in header[1:])):
        raise BoundedError("SPARSE_FINANCIAL_DATE_HEADER_UNQUALIFIED")
    dates = [date.fromisoformat(v) for v in header[1:] if v != _UNDATED]
    if not dates or len(set(dates)) != len(dates):
        raise BoundedError("SPARSE_FINANCIAL_DATE_HEADER_AMBIGUOUS")
    kept = [0] + [n for n, value in enumerate(header) if n and value != _UNDATED]
    removed = [n for n, value in enumerate(header) if n and value == _UNDATED]
    result = []
    cells = nonempty = 0
    header_seen = False
    for line in lines:
        row = _cells(line)
        if not row:
            result.append(line)
            continue
        if len(row) != len(header):
            raise BoundedError("SPARSE_FINANCIAL_ORIGINAL_ROW_WIDTH_INVALID")
        if row[0] == "财务指标":
            header_seen = True
        elif not header_seen:
            raise BoundedError("SPARSE_FINANCIAL_CELLS_BEFORE_HEADER")
        else:
            cells += len(removed)
            nonempty += sum(row[n] not in _EMPTY for n in removed)
        result.append("｜" + "｜".join(row[n] for n in kept) + "｜")
    _matrix(result)  # Reuse the frozen full dated-row grammar, including wraps.
    return result, {"dated_columns": len(dates), "undated_columns": len(removed),
                    "excluded_undated_cells": cells, "undated_nonempty_cells": nonempty,
                    "original_header_sha256": digest(header)}


def repair_sparse_summary(
    raw: dict[str, Any], *, source_sha256: str, revision: str,
    resolver: FinancialCompanyResolver, observed_at: datetime, contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    original = financial_literal_identity(raw, observed_at)
    frames, held, views = [], [], []
    block_map: dict[str, str] = {}
    reused = 0
    for number, lines in enumerate(_frames(raw["content"])):
        block = digest("\n".join(lines))
        try:
            _matrix(lines)
        except (BoundedError, ValueError):
            pass
        else:
            reused += 1
            continue
        try:
            transformed, counts = _dated_view(lines)
            derived_sha = digest("\n".join(transformed))
            if derived_sha in block_map and block_map[derived_sha] != block:
                raise BoundedError("SPARSE_FINANCIAL_DERIVED_BLOCK_IDENTITY_AMBIGUOUS")
            block_map[derived_sha] = block
            views.append("\n".join(transformed))
            frames.append({"frame_index": number, "original_block_sha256": block,
                           "derived_dated_block_sha256": derived_sha, **counts})
        except (BoundedError, ValueError) as exc:
            held.append({"frame_index": number, "original_block_sha256": block,
                         "reason": str(exc)})
    projected: dict[str, list[dict[str, Any]]] = {
        "dwd_financial_report": [], "dwd_financial_concept": [], "dwd_financial_fact": [],
    }
    evaluation: dict[str, Any] = {}
    derived_content_sha: str | None = None
    if views:
        content = "【主要财务指标】\n" + "\n".join(views) + "\n"
        derived_content_sha = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        derived = {**raw, "content": content, "content_sha256": derived_content_sha}
        projected, evaluation = summary_observation(
            derived, source_sha256=source_sha256, revision=revision, resolver=resolver,
            observed_at=observed_at, contract=contract,
        )
        for fact in projected["dwd_financial_fact"]:
            dimensions = json.loads(fact["dimensions_json"])
            dimensions["source_content_sha256"] = raw["content_sha256"]
            dimensions["source_block_sha256"] = block_map[dimensions["source_block_sha256"]]
            fact["dimensions_json"] = json.dumps(
                dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            fact["context_hash"] = hashlib.sha256(fact["dimensions_json"].encode()).hexdigest()
    proof = {
        "contract_id": CONTRACT_ID,
        "complete_original_raw_row_sha256": original["complete_raw_row_sha256"],
        "original_content_sha256": raw["content_sha256"],
        "source_member_sha256": source_sha256, "source_revision": revision,
        "original_acquired_at": raw["acquired_at"],
        "original_provider_updated_date": raw["provider_updated_date"],
        "derived_view_content_sha256": derived_content_sha,
        "governance_observed_at": observed_at,
        "frames": frames, "held_frames": held, "already_valid_frames_reused": reused,
        "excluded_undated_cells": sum(f["excluded_undated_cells"] for f in frames),
        "undated_nonempty_cells": sum(f["undated_nonempty_cells"] for f in frames),
        "undated_nonempty_values_promoted": False, "period_or_PIT_inferred": False,
        "frozen_evaluator": evaluation,
        "row_counts": {t: len(rows) for t, rows in projected.items()},
        "typed_rows_sha256": {t: digest(rows) for t, rows in projected.items()},
    }
    return projected, proof
