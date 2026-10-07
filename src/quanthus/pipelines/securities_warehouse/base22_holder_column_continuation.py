"""Scoped holder atoms retain literal percentage bases and separate change tables.

This successor does not alter the frozen ownership projector or its receipts.
Numeric percentages are source numerals with their original denominator text;
they are never converted to a common total-capital denominator or consumer input.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _QUANTITY, _cells, _modeled_cell_census
from .base22_financial import _clock
from .base22_holder_literals import _holder_view as _literal_view
from .base22_precision import decimal_profile
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    _effective_start,
    ownership_capital_schemas,
    parse_f10_shareholding_tables,
)

CONTRACT_ID = "p10-base22-holder-column-text-continuation-v3"
TABLE = "dwd_shareholding_observation"
_PERCENT = re.compile(r"(?P<number>\d+(?:\.\d+)?)%?(?P<basis>\s+占A股)?")
_DATES = re.compile(r"截至日期：\d{4}-\d{2}-\d{2}\s+十大(?:流通)?股东情况")


def _holder_view(content: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Join only exact column-aligned text; scalars and source bytes stay fixed."""
    view, separate = _literal_view(content)
    output: list[str] = []
    active = dated = False
    width = 0
    previous: int | None = None
    folded = 0
    comments: list[str] = []
    scalar_ledger: list[tuple[str, str]] = []
    numeric_row_indices: list[int] = []
    allowed_headers = {
        ("股东名称", "持股数(股)", percentage, classification, "增减情况(股)")
        for percentage in ("占总股本比(%)", "占流通股比(%)", "占总流通股比(%)")
        for classification in ("股份性质", "股东性质")
    } | {("股东名称", "持股数量", "占总股本", "股份性质")}
    for line in view.splitlines():
        cells = _cells(line)
        if _DATES.search(line):
            active, dated, previous = False, True, None
        elif dated and cells and cells[0] == "股东名称":
            if tuple(cells) not in allowed_headers:
                raise BoundedError("HOLDER_COLUMN_HEADER_UNQUALIFIED")
            active, previous, width = True, None, len(cells)
        elif active and line.strip().startswith("└"):
            active, dated, previous = False, False, None
        elif active and cells and any(cells):
            if len(cells) == width and cells[0] and _QUANTITY.fullmatch(cells[1].replace(",", "")):
                previous = len(output)
                scalar_ledger.append((cells[1], cells[2]))
                numeric_row_indices.append(previous)
                if width == 5 and cells[4]:
                    comments.append(digest(cells[4]))
            elif previous is not None and cells[0] and (len(cells) == 1 or not any(cells[1:])):
                row = _cells(output[previous])
                row[0] += cells[0]
                output[previous] = "｜" + "｜".join(row) + "｜"
                continue
            elif (
                previous is not None
                and len(cells) == width
                and not any(cells[1:3])
                and cells[3]
                and not any(cells[4:])
                and re.fullmatch(r"[A-Za-z\u4e00-\u9fff -]+", cells[3])
            ):
                row = _cells(output[previous])
                row[0] += cells[0]
                row[3] += cells[3]
                output[previous] = "｜" + "｜".join(row) + "｜"
                folded += 1
                continue
            else:
                # No carry of a holder identity into a new numeric row; no
                # inference from share class, rank or neighboring records.
                raise BoundedError("HOLDER_COLUMN_NONEMPTY_ROW_UNACCOUNTED")
        output.append(line)
    rewritten = "\n".join(output)
    actual_ledger = [(c[1], c[2]) for i in numeric_row_indices for c in [_cells(output[i])]]
    if scalar_ledger != actual_ledger:
        raise BoundedError("HOLDER_COLUMN_SCALAR_LEDGER_CHANGED")
    return (
        rewritten,
        separate,
        {
            "column_continuation_rows": folded,
            "original_layout_view_sha256": digest(view),
            "modeled_scalar_ledger_sha256": digest(scalar_ledger),
            "numeric_token_sequence_preserved": True,
            "numeric_token_sequence_scope": "modeled_quantity_and_percentage_cells_only",
            "unqualified_increment_comments": {
                "source_field": "增减情况(股)",
                "nonempty_cells": len(comments),
                "ordered_cell_sha256": digest(comments),
                "qualification": "NOT_GRANTED",
                "mapped_to_change_observations": False,
            },
        },
    )


def holder_observation(
    raw: dict[str, Any], *, source_sha256: str, resolver: FinancialCompanyResolver
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """A literal source ratio, not a denominator conversion or full-category PASS."""
    import pyarrow as pa  # type: ignore[import-untyped]

    content, code = raw.get("content"), raw.get("stock_code")
    if (
        raw.get("category") != "股东研究"
        or not isinstance(content, str)
        or not 0 < len(content.encode()) <= 1024**2
        or not isinstance(code, str)
        or re.fullmatch(r"\d{6}", code) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) is None
        or raw.get("content_sha256") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        or not isinstance(raw.get("source"), str)
        or not raw["source"]
        or not isinstance(raw.get("source_revision"), str)
        or not raw["source_revision"]
    ):
        raise BoundedError("HOLDER_LITERAL_ORIGINAL_SOURCE_BINDING_INVALID")
    try:
        acquisition = _clock(raw.get("acquired_at"))
    except (BoundedError, RuntimeError, ValueError):
        raise BoundedError("HOLDER_COLUMN_ACQUISITION_CLOCK_INVALID") from None
    view, separate, layout = _holder_view(content)
    census = _modeled_cell_census(view, "股东研究")
    with localcontext() as context:
        context.prec = 64
        parsed = parse_f10_shareholding_tables(view)
    if not 0 < len(parsed) <= 4096 or len(parsed) != census.get("numeric_cells", 0):
        raise BoundedError("HOLDER_LITERAL_MATRIX_NOT_FULLY_ACCOUNTED")
    rows: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    bases: Counter[str] = Counter()
    seen = set()
    for item in parsed:
        percentage = item["raw_holding_percentage"].strip()
        match = _PERCENT.fullmatch(percentage)
        expected = Decimal(match["number"]) if match else None
        if (
            _QUANTITY.fullmatch(item["raw_holding_quantity"].strip().replace(",", "")) is None
            or item["holding_quantity"] < 0
            or (percentage not in {"", "-"} and match is None)
            or item["holding_percentage"] != expected
            or (expected is not None and not 0 <= expected <= 100)
            or not decimal_profile(item["holding_quantity"], precision=38, scale=10)["fits"]
            or (
                expected is not None
                and not decimal_profile(expected, precision=18, scale=12)["fits"]
            )
        ):
            raise BoundedError("HOLDER_LITERAL_UNIT_SCALAR_OR_EXACT_PRECISION_UNQUALIFIED")
        basis = (
            "LITERAL_A_SHARE_DENOMINATOR"
            if match and match["basis"]
            else "SOURCE_PERCENTAGE_DENOMINATOR_NOT_INFERRED"
            if match
            else "EXPLICIT_NULL"
        )
        bases[basis] += 1
        day = item["observation_date"]
        if type(day) is not date:
            raise BoundedError("HOLDER_LITERAL_BUSINESS_DATE_INVALID")
        if day > THROUGH:
            held.append({"business_date": day.isoformat(), "reason": "AFTER_IMAGE_CUTOFF"})
            continue
        identity, reason = resolver.resolve_for_report(code, period_end=day)
        if identity is None:
            held.append({"business_date": day.isoformat(), "reason": reason})
            continue
        row = {
            "record_source": raw["source"],
            "source_record_id": (
                f"stock_code={code}&category=股东研究&content_sha256={raw['content_sha256']}"
            ),
            "source_revision": raw["source_revision"],
            "source_artifact_sha256": source_sha256,
            "knowledge_from": max(acquisition, identity.knowledge_from),
            "knowledge_to": None,
            "observation_start": _effective_start(day),
            "observation_end": None,
            "company_id": identity.company_id,
            "security_id": identity.security_id,
            "shareholder_id": None,
            **{k: v for k, v in item.items() if k != "observation_date"},
        }
        key = (day, row["holding_type"], row["shareholder_source_name"], row["holder_rank"])
        if key in seen:
            raise BoundedError("HOLDER_LITERAL_DUPLICATE_BUSINESS_KEY")
        seen.add(key)
        rows.append(row)
    restored = pa.Table.from_pylist(rows, schema=ownership_capital_schemas()[TABLE]).to_pylist()
    if restored != rows:
        raise BoundedError("HOLDER_LITERAL_TYPED_ATOM_OR_CLOCK_CHANGED")
    proof = {
        "contract_id": CONTRACT_ID,
        "raw_row_sha256": digest(raw),
        "source_member_sha256": source_sha256,
        "content_sha256": raw["content_sha256"],
        "actual_acquisition": acquisition,
        "original_raw_acquisition": raw["acquired_at"],
        "parser_view_sha256": digest(view),
        **layout,
        "parsed_observations": len(parsed),
        "typed_observations": len(rows),
        "modeled_cell_census": census,
        "percentage_basis_counts": dict(bases),
        "separate_nonempty_domains": separate,
        "held_observations": held,
        "typed_rows_sha256": digest(restored),
        "denominators_converted_or_erased": False,
        "whole_category_and_history_union_admission": "SEPARATE_GATE",
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "shared_parses_built": 1,
    }
    proof["identity"] = digest(proof)
    return {TABLE: restored}, proof
