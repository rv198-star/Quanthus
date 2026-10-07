"""Bounded weekly capital/top-holder projection through dated native identities.

One raw category is parsed once. Missing identity, unsupported units or nonempty
unparseable tables are explicit held observations, never empty coverage evidence.
The caller owns the frozen complete weekly/raw/history input closure and union.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date
from decimal import localcontext
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_precision import decimal_profile
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    _effective_start,
    ownership_capital_schemas,
    parse_f10_capital_structure,
    parse_f10_shareholding_tables,
)

CONTRACT_ID = "p10-base22-weekly-f10-ownership-exact-v1"
TABLES = ("dwd_share_capital_observation", "dwd_shareholding_observation")
CATEGORIES = {"股本结构": TABLES[0], "股东研究": TABLES[1]}
_QUANTITY = re.compile(r"[+-]?(?:\d+(?:\.\d+)?)(?:亿股?|万股?|股)?")
_PERCENT = re.compile(r"[+-]?\d+(?:\.\d+)?%?")


def _cells(line: str) -> list[str]:
    """Remove one outer border, preserving empty interior/leading columns."""
    if "｜" not in line:
        return []
    value = line.strip()
    if value.startswith("｜"):
        value = value[1:]
    if value.endswith("｜"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("｜")]


def _modeled_cell_census(content: str, category: str) -> dict[str, int]:
    """Account for every cell in the explicitly modeled matrix/top-holder tables.

    This independent accounting catches the legacy parser's skipped malformed
    nonempty cells. Explicit blanks are disclosed, not manufactured observations.
    Other F10 sections retain their separate domain contracts.
    """
    counts: Counter[str] = Counter()
    active = False
    dates: list[str] = []
    holder_header = re.compile(r"截至日期：\d{4}-\d{2}-\d{2}\s+十大(?:流通)?股东情况")
    pending = False
    dated_holder_table = False
    for line in content.splitlines():
        if category == "股本结构":
            if "【1.股本结构】" in line:
                active = True
                continue
            if active and re.match(r"【[2-9]", line.strip()):
                break
            cells = _cells(line)
            if not active or not cells:
                continue
            if cells[0].startswith("单位"):
                dates = cells[1:]
                continue
            if not any(cells):
                counts["blank_structural_rows"] += 1
                continue
            if not cells[0] and any(cells[1:]):
                raise BoundedError("OWNERSHIP_NONEMPTY_CAPITAL_MATRIX_CELL_UNACCOUNTED")
            if not any(cells[1:]):
                continue
            if not dates or len(cells) != len(dates) + 1:
                raise BoundedError("OWNERSHIP_NONEMPTY_CAPITAL_MATRIX_CELL_UNACCOUNTED")
            for day, token in zip(dates, cells[1:], strict=True):
                if token in {"", "-"}:
                    counts["explicit_null_cells"] += 1
                    continue
                if not day or day == "-" or not _QUANTITY.fullmatch(token.replace(",", "")):
                    raise BoundedError("OWNERSHIP_NONEMPTY_CAPITAL_MATRIX_CELL_UNACCOUNTED")
                counts["numeric_cells"] += 1
        else:
            if holder_header.search(line):
                active, pending = False, False
                dated_holder_table = True
                continue
            cells = _cells(line)
            if dated_holder_table and cells and cells[0] == "股东名称":
                active, pending = True, False
                continue
            if active and line.strip().startswith("└"):
                active = False
                dated_holder_table = False
            if not active or not cells:
                continue
            if not any(cells):
                counts["blank_structural_rows"] += 1
                continue
            if len(cells) == 1 and cells[0] and pending:
                counts["continuation_rows"] += 1
                continue
            if len(cells) >= 4 and cells[0] and not any(cells[1:]) and pending:
                counts["continuation_rows"] += 1
                continue
            if len(cells) < 4 or not cells[0] or not _QUANTITY.fullmatch(
                cells[1].replace(",", "")
            ):
                raise BoundedError("OWNERSHIP_NONEMPTY_HOLDER_TABLE_CELL_UNACCOUNTED")
            counts["numeric_cells"] += 1
            pending = True
    return dict(counts)


def ownership_observation(
    raw: dict[str, Any], *, source_sha256: str, resolver: FinancialCompanyResolver
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Preserve explicit units/clocks; resolve every business date independently."""
    import pyarrow as pa  # type: ignore[import-untyped]

    content = raw.get("content")
    category = raw.get("category")
    stock_code = raw.get("stock_code")
    if (
        category not in CATEGORIES or not isinstance(content, str)
        or not 0 < len(content.encode("utf-8")) <= 1024**2
        or not isinstance(stock_code, str) or re.fullmatch(r"\d{6}", stock_code) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) is None
        or raw.get("content_sha256") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        or not isinstance(raw.get("source_revision"), str) or not raw["source_revision"]
        or not isinstance(raw.get("source"), str) or not raw["source"]
    ):
        raise BoundedError("OWNERSHIP_EXACT_RAW_CATEGORY_BINDING_INVALID")
    acquisition = _clock(raw.get("acquired_at"))
    table = CATEGORIES[category]
    cell_census = _modeled_cell_census(content, category)
    with localcontext() as context:
        context.prec = 64
        parsed = (
            parse_f10_capital_structure(content) if category == "股本结构"
            else parse_f10_shareholding_tables(content)
        )
    if not parsed or len(parsed) > 4096:
        raise BoundedError("OWNERSHIP_NONEMPTY_CATEGORY_UNPARSED_OR_BOUND_EXCEEDED")
    if cell_census.get("numeric_cells", 0) != len(parsed):
        raise BoundedError("OWNERSHIP_MODELED_CELL_CENSUS_NOT_FULLY_PARSED")
    record = (
        f"stock_code={stock_code}&category={category}&content_sha256={raw['content_sha256']}"
    )
    common = {
        "record_source": raw["source"], "source_record_id": record,
        "source_revision": raw["source_revision"], "source_artifact_sha256": source_sha256,
        "knowledge_from": acquisition, "knowledge_to": None,
    }
    rows = []
    held: list[dict[str, Any]] = []
    scalars: Counter[str] = Counter()
    seen: set[tuple[Any, ...]] = set()
    for item in parsed:
        key: tuple[Any, ...]
        day = item["observation_date"]
        if type(day) is not date:
            raise BoundedError("OWNERSHIP_BUSINESS_DATE_INVALID")
        if day > THROUGH:
            held.append({"business_date": day.isoformat(), "reason": "AFTER_IMAGE_CUTOFF"})
            continue
        identity, reason = resolver.resolve_for_report(stock_code, period_end=day)
        if identity is None:
            held.append({"business_date": day.isoformat(), "reason": reason})
            continue
        row = {
            **common, "knowledge_from": max(acquisition, identity.knowledge_from),
            "observation_start": _effective_start(day), "observation_end": None,
            "security_id": identity.security_id,
        }
        if table == TABLES[0]:
            token = item["raw_value"].strip().replace(",", "")
            if not _QUANTITY.fullmatch(token) or item["share_quantity"] < 0:
                raise BoundedError("OWNERSHIP_CAPITAL_UNIT_OR_SCALAR_UNQUALIFIED")
            numbers = [(item["share_quantity"], 38, 10)]
            row.update({k: v for k, v in item.items() if k != "observation_date"})
            key = (identity.security_id, day, row["capital_type"])
        else:
            token = item["raw_holding_quantity"].strip().replace(",", "")
            percent = item["raw_holding_percentage"].strip()
            if (
                not _QUANTITY.fullmatch(token) or item["holding_quantity"] < 0
                or (percent not in {"", "-"} and not _PERCENT.fullmatch(percent))
                or (item["holding_percentage"] is not None
                    and not 0 <= item["holding_percentage"] <= 100)
            ):
                raise BoundedError("OWNERSHIP_HOLDING_UNIT_OR_SCALAR_UNQUALIFIED")
            numbers = [(item["holding_quantity"], 38, 10), (item["holding_percentage"], 18, 12)]
            row.update(
                company_id=identity.company_id, shareholder_id=None,
                **{k: v for k, v in item.items() if k != "observation_date"},
            )
            key = (identity.security_id, day, row["holding_type"],
                   row["shareholder_source_name"], row["holder_rank"])
        if key in seen:
            raise BoundedError("OWNERSHIP_DUPLICATE_RAW_BUSINESS_KEY")
        seen.add(key)
        for number, precision, scale in numbers:
            profile = decimal_profile(number, precision=precision, scale=scale)
            scalars[profile["classification"]] += 1
            if number is not None and not profile["fits"]:
                raise BoundedError("OWNERSHIP_EXACT_DECIMAL_NOT_REPRESENTABLE")
        rows.append(row)
    restored = pa.Table.from_pylist(rows, schema=ownership_capital_schemas()[table]).to_pylist()
    if restored != rows:
        raise BoundedError("OWNERSHIP_TYPED_ATOM_OR_CLOCK_CHANGED")
    evidence = {
        "contract_id": CONTRACT_ID, "raw_row_sha256": digest(raw),
        "source_member_sha256": source_sha256, "content_sha256": raw["content_sha256"],
        "category": category, "stock_code": stock_code,
        "raw_acquisition": raw["acquired_at"], "actual_acquisition": acquisition,
        "parsed_observations": len(parsed), "typed_observations": len(restored),
        "modeled_cell_census": cell_census,
        "held_observations": held, "scalar_classes": dict(scalars),
        "typed_rows_sha256": digest(restored), "shared_parses_built": 1,
        "provider_updated_date_claim": raw.get("provider_updated_date"),
        "original_publication_inferred": False, "historical_PIT_admission": False,
        "whole_category_and_history_union_admission": "SEPARATE_GATE",
    }
    evidence["identity"] = digest(evidence)
    return {table: restored}, evidence
