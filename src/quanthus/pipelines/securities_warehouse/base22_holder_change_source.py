"""Source-reported holder changes, separate from ranked-list comments.

Only complete physical change tables with explicit fields/units are projected.
Natural-key collisions remain held. The scope never grants consumer admission,
historical knowledge, a legacy source alias or a cross-provider event priority.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, Inexact, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base22_financial import _clock
from .base22_holder_literals import _CHANGE_HEADERS
from .base22_precision import decimal_profile
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import _effective_start, _stable_holder_key, _stable_id

CONTRACT_ID = "p10-base22-complete-retained-holder-change-source-v1"
TABLE = "dwd_shareholding_change"
_QUANTITY = re.compile(r"(?P<n>[+-]?\d+(?:\.\d+)?)(?P<u>亿股?|万股?|股)?")
_RATIO = re.compile(r"[+-]?\d+(?:\.\d+)?%?")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _quantity(token: str, *, explicit_share_unit: bool, signed: bool) -> Decimal | None:
    if token in {"", "-", "--"}:
        return None
    match = _QUANTITY.fullmatch(token.replace(",", ""))
    if not match or not explicit_share_unit and not match["u"]:
        raise BoundedError("HOLDER_CHANGE_SOURCE_QUANTITY_UNIT_UNAVAILABLE")
    with localcontext() as context:
        context.prec = 64
        context.traps[Inexact] = True
        value = Decimal(match["n"]) * (
            Decimal(100000000)
            if match["u"] and match["u"].startswith("亿")
            else Decimal(10000)
            if match["u"] and match["u"].startswith("万")
            else Decimal(1)
        )
        if not signed and value < 0 or not decimal_profile(value, precision=38, scale=10)["fits"]:
            raise BoundedError("HOLDER_CHANGE_SOURCE_QUANTITY_RANGE_OR_PRECISION_INVALID")
        return value.quantize(Decimal("1e-10"))


def _ratio(token: str, *, explicit_percent_unit: bool, delta: bool) -> Decimal | None:
    if token in {"", "-", "--"}:
        return None
    if not _RATIO.fullmatch(token) or not explicit_percent_unit and not token.endswith("%"):
        raise BoundedError("HOLDER_CHANGE_SOURCE_RATIO_UNIT_UNAVAILABLE")
    value = Decimal(token.removesuffix("%"))
    if (
        not (-100 if delta else 0) <= value <= 100
        or not decimal_profile(value, precision=18, scale=12)["fits"]
    ):
        raise BoundedError("HOLDER_CHANGE_SOURCE_RATIO_RANGE_OR_PRECISION_INVALID")
    return value.quantize(Decimal("1e-12"))


def project_change_tables(
    raw: dict[str, Any],
    *,
    source_sha256: str,
    resolver: FinancialCompanyResolver,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """One bounded original category yields native atoms and explicit block holds."""
    content, code = raw.get("content"), raw.get("stock_code")
    if (
        raw.get("category") != "股东研究"
        or not isinstance(content, str)
        or not 0 < len(content.encode()) <= 1024**2
        or not isinstance(code, str)
        or not re.fullmatch(r"\d{6}", code)
        or raw.get("content_sha256") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)
        or not raw.get("source")
        or not raw.get("source_revision")
    ):
        raise BoundedError("HOLDER_CHANGE_EXACT_RAW_CATEGORY_BINDING_INVALID")
    acquired = _clock(raw.get("acquired_at"))
    lines = content.splitlines()
    starts = [n for n, line in enumerate(lines) if tuple(_cells(line)) in _CHANGE_HEADERS]
    if len(starts) > 16:
        raise BoundedError("HOLDER_CHANGE_SOURCE_TABLE_COUNT_BOUND_INVALID")
    blocks = []
    candidates: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for index, start in enumerate(starts):
        bound = starts[index + 1] if index + 1 < len(starts) else len(lines)
        end = next(
            (n + 1 for n in range(start + 1, bound) if lines[n].strip().startswith("└")), bound
        )
        block, header = lines[start:end], _cells(lines[start])
        proof: dict[str, Any] = {
            "block_index": index,
            "original_block_sha256": digest("\n".join(block)),
            "exact_header": header,
        }
        pending: list[list[str]] = []
        units: list[str] | None = None
        try:
            if not block[-1].strip().startswith("└"):
                raise BoundedError("HOLDER_CHANGE_SOURCE_PHYSICAL_TABLE_INCOMPLETE")
            for line in block[1:-1]:
                cells = _cells(line)
                if not cells or not any(cells):
                    if line.strip() and not line.strip().startswith(("├", "┌", "│")):
                        raise BoundedError("HOLDER_CHANGE_SOURCE_NONEMPTY_ROW_UNACCOUNTED")
                    continue
                if len(cells) != 7:
                    raise BoundedError("HOLDER_CHANGE_SOURCE_NONEMPTY_ROW_UNACCOUNTED")
                if not cells[0] and not cells[1] and any("股" in c or "%" in c for c in cells[2:6]):
                    if units is not None or pending:
                        raise BoundedError("HOLDER_CHANGE_SOURCE_UNIT_HEADER_CONFLICT")
                    units = cells
                elif pending and not cells[0] and cells[1] and not any(cells[2:]):
                    pending[-1][1] += cells[1]
                elif _DAY.fullmatch(cells[0]):
                    pending.append(cells)
                else:
                    raise BoundedError("HOLDER_CHANGE_SOURCE_DATE_OR_NONEMPTY_ROW_UNACCOUNTED")
            if len(pending) > 2048:
                raise BoundedError("HOLDER_CHANGE_SOURCE_ROW_BOUND_INVALID")
            parsed: list[tuple[str, dict[str, Any], str]] = []
            for cells in pending:
                day = date.fromisoformat(cells[0])
                if day > THROUGH:
                    counts["excluded_after_cutoff_rows"] += 1
                    continue
                if day > acquired.astimezone(ZoneInfo("Asia/Shanghai")).date():
                    raise BoundedError("HOLDER_CHANGE_FUTURE_OBSERVATION_STATUS_UNPROVED")
                if not cells[1] or not cells[6] or re.search(r"拟|计划|预计|意向", cells[6]):
                    raise BoundedError(
                        "HOLDER_CHANGE_SOURCE_ACTUAL_HOLDER_AND_CHANGE_KIND_REQUIRED"
                    )
                parent, reason = resolver.resolve_for_report(code, period_end=day)
                if parent is None:
                    raise BoundedError(reason)
                # Retain before quantity in the source atom proof; the native
                # model has no shares-before column. Do not derive a delta.
                _quantity(
                    cells[2], explicit_share_unit=bool(units and "股" in units[2]), signed=False
                )
                changed = _quantity(
                    cells[3], explicit_share_unit=bool(units and "股" in units[3]), signed=True
                )
                after = _quantity(
                    cells[4], explicit_share_unit=bool(units and "股" in units[4]), signed=False
                )
                ratio = _ratio(
                    cells[5],
                    explicit_percent_unit=bool(units and "%" in units[5]),
                    delta=header[5] == "变动比例",
                )
                natural = [raw["source"], code, day, cells[1], cells[6]]
                key = digest(natural)
                atom = digest({"header": header, "unit_header": units, "cells": cells})
                row = {
                    "record_source": "p10_retained_f10_holder_change_table",
                    "source_record_id": digest([code, atom]),
                    "source_revision": raw["source_revision"],
                    "source_artifact_sha256": source_sha256,
                    "knowledge_from": max(acquired, parent.knowledge_from),
                    "knowledge_to": None,
                    "effective_at": _effective_start(day),
                    "announced_at": None,
                    "shareholding_change_id": _stable_id("shareholding_change", CONTRACT_ID, key),
                    "company_id": parent.company_id,
                    "security_id": parent.security_id,
                    "shareholder_source_name": cells[1],
                    "shareholder_id": None,
                    "change_type": cells[6],
                    "share_change": changed,
                    "percentage_change": ratio if header[5] == "变动比例" else None,
                    "post_holding": after,
                    "post_percentage": ratio if header[5] == "变动后占" else None,
                    "disclosure_id": None,
                    "holder_match_key": _stable_holder_key(cells[1]),
                    "raw_share_change": cells[3],
                    "share_change_unit": "SHARE" if changed is not None else None,
                    "raw_percentage_change": cells[5] if header[5] == "变动比例" else None,
                    "percentage_change_unit": "SOURCE_DECLARED_PERCENT_CHANGE_BASIS_UNAVAILABLE"
                    if ratio is not None and header[5] == "变动比例"
                    else None,
                    "raw_post_holding": cells[4],
                    "post_holding_unit": "SHARE" if after is not None else None,
                    "raw_post_percentage": cells[5] if header[5] == "变动后占" else None,
                    "post_percentage_unit": "PERCENT_OF_SOURCE_TOTAL_SHARES"
                    if ratio is not None and header[5] == "变动后占"
                    else None,
                    "parser_version": CONTRACT_ID,
                }
                parsed.append((key, row, atom))
            for key, row, atom in parsed:
                candidates[key].append((row, atom))
            proof.update(
                {
                    "status": "EXACT_COMPLETE_SOURCE_CHANGE_TABLE",
                    "source_data_rows": len(pending),
                    "typed_candidate_rows": len(parsed),
                    "source_atoms_sha256": digest([a for _, _, a in parsed]),
                }
            )
        except (BoundedError, ValueError) as error:
            counts["held_blocks"] += 1
            proof.update({"status": "HELD_NOT_EMPTY_COVERAGE", "reason": str(error)})
        blocks.append(proof)
    output = []
    for _key, values in sorted(candidates.items()):
        if len({atom for _, atom in values}) != 1:
            counts["held_natural_keys"] += 1
        else:
            output.append(values[0][0])
            counts["exact_duplicate_source_rows"] += len(values) - 1
    if not starts and re.search(r"【\d+\.股东持股变动】", content):
        counts["held_blocks"] += 1
    proof = {
        "contract_id": CONTRACT_ID,
        "raw_row_sha256": digest(raw),
        "content_sha256": raw["content_sha256"],
        "source_member_sha256": source_sha256,
        "actual_acquisition": acquired,
        "exact_change_tables": len(starts),
        "blocks": blocks,
        "typed_rows": len(output),
        "typed_rows_sha256": digest(output),
        "held_blocks": counts["held_blocks"],
        "held_natural_keys": counts["held_natural_keys"],
        "exact_duplicate_source_rows": counts["exact_duplicate_source_rows"],
        "excluded_after_cutoff_rows": counts["excluded_after_cutoff_rows"],
        "whole_change_scope": "HELD_NOT_EMPTY_COVERAGE"
        if counts["held_blocks"] or counts["held_natural_keys"]
        else "COMPLETE_SELECTED_CHANGE_TABLES"
        if starts
        else "NO_SELECTED_CHANGE_TABLE_IN_EXACT_RAW_CATEGORY",
        "date_or_quantity_inferred": False,
        "list_comments_are_change_events": False,
        "original_publication_unavailable": True,
        "historical_PIT_admission": False,
        "legacy_source_alias_inferred": False,
        "consumer_admission": "NOT_GRANTED",
        "full_native_version_union": "SEPARATE_GATE",
    }
    proof["identity"] = digest(proof)
    return {TABLE: output}, proof
