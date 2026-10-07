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
from .base22_precision import decimal_profile
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    _effective_start,
    ownership_capital_schemas,
    parse_f10_shareholding_tables,
)

CONTRACT_ID = "p10-base22-holder-literal-percentage-basis-v2"
TABLE = "dwd_shareholding_observation"
_PERCENT = re.compile(r"(?P<number>\d+(?:\.\d+)?)%?(?P<basis>\s+占A股)?")
_DATES = re.compile(r"截至日期：\d{4}-\d{2}-\d{2}\s+十大(?:流通)?股东情况")
_CHANGE_HEADERS = {
    ("日期", "股东名称", "持股数", "变动股数", "变动后", "变动比例", "备注"),
    ("变动日期", "股东名称", "持股数", "变动股数", "变动后", "变动后占", "变动类型"),
}


def _holder_view(content: str) -> tuple[str, list[dict[str, Any]]]:
    """An exact other-domain header closes a matrix; unknown cells still fail."""
    lines = content.splitlines()
    selected = []
    separate = []
    current: list[str] | None = None

    def finish() -> None:
        if current is None:
            return
        data = [line for line in current[1:] if any(_cells(line))]
        separate.append(
            {
                "table": "dwd_shareholding_change",
                "original_block_sha256": digest("\n".join(current)),
                "nonempty_data_rows": len(data),
                "qualification": "NOT_GRANTED",
            }
        )

    for line in lines:
        cells = _cells(line)
        if tuple(cells) in _CHANGE_HEADERS:
            finish()
            current = [line]
            # This is a hashed parser-view boundary, never a rewritten source.
            selected.append("└")
        elif current is not None and not _DATES.search(line):
            current.append(line)
        else:
            if current is not None:
                finish()
                current = None
            selected.append(line)
    finish()
    return "\n".join(selected), separate


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
        view, separate = _holder_view(content)
        census = _modeled_cell_census(view, "股东研究")
    except (BoundedError, RuntimeError, ValueError):
        raise BoundedError("HOLDER_LITERAL_CLOCK_OR_MATRIX_CENSUS_INVALID") from None
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
