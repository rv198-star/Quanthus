"""Dated September ownership observations from complete retained literal bodies.

Reuse the reviewed literal matrix grammar and units, with an explicit successor
cutoff and real new governance time. Unsupported frames stay evidence-bound holds.
An observed future business-date claim does not establish actual implementation
or historical availability. Native union and consumer admission remain separate.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from decimal import localcontext
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _QUANTITY, _modeled_cell_census
from .base22_financial import _clock
from .base22_holder_components_v2 import (
    _DATE,
    COMPONENT_KEYS,
    _matrix,
    component_key,
)
from .base22_holder_components_v2 import (
    CONTRACT_ID as GRAIN_ID,
)
from .base22_holder_literals import _holder_view
from .base22_precision import decimal_profile
from .base23_ownership_reconfirmation import literal_identity
from .base23_sources import THROUGH
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    OwnershipCapitalBuildError,
    _effective_start,
    _stable_holder_key,
    ownership_capital_schemas,
    parse_f10_capital_structure,
)

CONTRACT_ID = "p10-base23-retained-f10-ownership-exact-v1"
QUALIFICATION = "COMPLETE_CHANGED_LITERAL_ROWS_QUALIFIED_FRAMES_NOT_PROVIDER_PIT"
CAPITAL = "dwd_share_capital_observation"
HOLDING = "dwd_shareholding_observation"
TABLES = (CAPITAL, HOLDING)


def ownership_observation(
    raw: dict[str, Any], *, source_sha256: str, revision: str,
    resolver: FinancialCompanyResolver, observed_at: datetime,
    component_contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    literal = literal_identity(raw)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) or not revision:
        raise BoundedError("SEPTEMBER_OWNERSHIP_SOURCE_BINDING_INVALID")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if acquired > observed:
        raise BoundedError("SEPTEMBER_OWNERSHIP_CAPTURE_AFTER_GOVERNANCE")
    if (component_contract.get("contract_id") != GRAIN_ID
            or component_contract.get("business_key") != COMPONENT_KEYS):
        raise BoundedError("SEPTEMBER_OWNERSHIP_LITERAL_GRAIN_UNQUALIFIED")
    common = {
        "record_source": raw["source"],
        "source_record_id": (f"stock_code={raw['stock_code']}&category={raw['category']}"
                             f"&content_sha256={raw['content_sha256']}"),
        "source_revision": revision, "source_artifact_sha256": source_sha256,
        "knowledge_from": observed, "knowledge_to": None,
    }
    rows: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    future: Counter[str] = Counter()
    table = CAPITAL if raw["category"] == "股本结构" else HOLDING

    def parent_for(day: date) -> Any:
        if day > THROUGH:
            raise BoundedError("AFTER_IMAGE_CUTOFF")
        parent, reason = resolver.resolve_for_report(raw["stock_code"], period_end=day)
        if parent is None:
            raise BoundedError(reason)
        if parent.knowledge_from > observed:
            raise BoundedError("SEPTEMBER_OWNERSHIP_PARENT_NOT_AVAILABLE")
        if day > acquired.astimezone(ZoneInfo("Asia/Shanghai")).date():
            future["BUSINESS_DATE_AFTER_RETAINED_CAPTURE_NOT_IMPLEMENTATION_PROOF"] += 1
        return parent

    separate: list[dict[str, Any]] = []
    if table == CAPITAL:
        try:
            census = _modeled_cell_census(raw["content"], raw["category"])
            with localcontext() as context:
                context.prec = 64
                parsed = parse_f10_capital_structure(raw["content"])
            if not 0 < len(parsed) <= 4096 or len(parsed) != census.get("numeric_cells", 0):
                raise BoundedError("CAPITAL_MODELED_LITERAL_MATRIX_NOT_FULLY_ACCOUNTED")
            capital_groups: dict[tuple[str, date, str], dict[str, Any]] = {}
            capital_conflicts = set()
            for item in parsed:
                day = item["observation_date"]
                try:
                    parent = parent_for(day)
                    token = item["raw_value"].strip().replace(",", "")
                    if (not _QUANTITY.fullmatch(token) or item["share_quantity"] < 0
                            or not decimal_profile(item["share_quantity"], precision=38,
                                                   scale=10)["fits"]):
                        raise BoundedError("CAPITAL_LITERAL_UNIT_OR_EXACT_SCALAR_UNQUALIFIED")
                    key = parent.security_id, day, item["capital_type"]
                    row = {**common, "security_id": parent.security_id,
                           "observation_start": _effective_start(day), "observation_end": None,
                           **{k: v for k, v in item.items() if k != "observation_date"},
                           "parser_version": CONTRACT_ID}
                    if key in capital_groups and capital_groups[key] != row:
                        capital_conflicts.add(key)
                        raise BoundedError("CAPITAL_EQUAL_DATE_TYPE_CONFLICT_NO_SOURCE_PRIORITY")
                    capital_groups[key] = row
                except BoundedError as exc:
                    held.append({"business_date": day.isoformat(), "reason": str(exc)})
            rows = [capital_groups[k] for k in sorted(capital_groups) if k not in capital_conflicts]
        except (BoundedError, OwnershipCapitalBuildError, ValueError) as exc:
            # A grammar/closed-cell failure affects this complete literal body.
            rows = []
            held.append({"scope": "complete_capital_matrix", "reason": str(exc)})
    else:
        _, separate = _holder_view(raw["content"])
        lines = raw["content"].splitlines()
        starts = [n for n, line in enumerate(lines) if _DATE.search(line)]
        if not 0 < len(starts) <= 256:
            held.append({"scope": "complete_holder_body", "reason": "NO_DATED_MATRIX_OR_BOUND"})
        else:
            grouped: dict[tuple[date, str], list[dict[str, Any]]] = {}
            conflicts = set()
            for index, start in enumerate(starts):
                bound = starts[index + 1] if index + 1 < len(starts) else len(lines)
                end = next((n + 1 for n in range(start + 1, bound)
                            if lines[n].strip().startswith("└")), bound)
                block = lines[start:end]
                match = _DATE.search(lines[start])
                assert match is not None
                frame: dict[str, Any] = {"frame_index": index,
                                         "source_block_sha256": digest("\n".join(block))}
                try:
                    day = date.fromisoformat(match[1])
                    parent = parent_for(day)
                    items, holder_census = _matrix(block, floating=bool(match[2]))
                    kind = "TOP_FLOATING_SHAREHOLDERS" if match[2] else "TOP_SHAREHOLDERS"
                    projected = []
                    for item in items:
                        projected.append({
                            **common, "observation_start": _effective_start(day),
                            "observation_end": None, "company_id": parent.company_id,
                            "security_id": parent.security_id, "shareholder_id": None,
                            "shareholder_source_name": item["name"],
                            "holder_match_key": _stable_holder_key(item["name"]),
                            "holder_rank": item["rank"], "table_kind": kind, "holding_type": kind,
                            "share_nature": item["classification"]
                            if holder_census["header"][3] == "股份性质" else None,
                            "holder_category": item["classification"]
                            if holder_census["header"][3] == "股东性质" else None,
                            "holding_quantity": item["quantity"],
                            "holding_percentage": item["percentage"],
                            "raw_holding_quantity": item["raw_quantity"],
                            "raw_holding_percentage": item["raw_percentage"],
                            "holding_quantity_unit": "亿股" if "亿" in item["raw_quantity"]
                            else "万股" if "万" in item["raw_quantity"] else "股",
                            "holding_percentage_unit": "PERCENT_OF_LITERAL_"
                            + item["basis"][1] + "_SHARES"
                            if item["basis"] in {"占A股", "占B股", "占H股"}
                            else "PERCENT" if item["percentage"] is not None else None,
                            "parser_version": CONTRACT_ID,
                        })
                    if len({component_key(r) for r in projected}) != len(projected):
                        raise BoundedError("HOLDER_LITERAL_FRAME_DUPLICATE_COMPONENT_KEY")
                    group = day, kind
                    if group in grouped:
                        if sorted(map(digest, projected)) != sorted(map(digest, grouped[group])):
                            conflicts.add(group)
                            raise BoundedError(
                                "HOLDER_EQUAL_DATE_FRAME_CONFLICT_NO_SOURCE_PRIORITY"
                            )
                        frame["status"] = "IDENTICAL_COMPLETE_FRAME_RECONFIRMED"
                    else:
                        grouped[group] = projected
                        frame["status"] = "QUALIFIED_COMPLETE_LITERAL_MATRIX"
                    frame.update(business_date=day.isoformat(), holding_type=kind,
                                 census=holder_census, typed_rows_sha256=digest(projected))
                except (BoundedError, ValueError) as exc:
                    frame.update(status="HELD_NOT_EMPTY_COVERAGE", reason=str(exc))
                frames.append(frame)
            for group in sorted(grouped):
                if group not in conflicts:
                    rows.extend(grouped[group])
            for frame in frames:
                if any(frame.get("business_date") == day.isoformat()
                       and frame.get("holding_type") == kind for day, kind in conflicts):
                    frame.update(status="HELD_NOT_EMPTY_COVERAGE",
                                 reason="HOLDER_EQUAL_DATE_FRAME_CONFLICT_NO_SOURCE_PRIORITY")
    schema = ownership_capital_schemas()[table]
    typed = pa.Table.from_pylist(rows, schema=schema)
    if typed.to_pylist() != rows or any(
        not f.nullable and typed.column(f.name).null_count for f in schema
    ):
        raise BoundedError("SEPTEMBER_OWNERSHIP_SERIALIZED_ATOM_CHANGED")
    proof = {
        "contract_id": CONTRACT_ID, "literal_identity": literal,
        "source_member_sha256": source_sha256, "source_revision": revision,
        "governance_observed_at": observed, "table": table, "frames": frames,
        "held": held, "future_capture_business_date_claims": dict(future),
        "separate_nonempty_domains": separate, "typed_rows": len(rows),
        "typed_rows_sha256": digest(rows), "provider_PIT_qualified": False,
        "implementation_proof_from_future_date": False, "native_history_changed": False,
    }
    return {table: rows}, proof
