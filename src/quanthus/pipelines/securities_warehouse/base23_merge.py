"""Exact scoped September members and sparse, value-preserving version closure.

Only typed key/clock metadata enters the join. Original Arrow columns, including
decimal256, never pass through DuckDB's full-row conversion. An immutable closed
historical interval cannot be rewritten by a new observation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock

SCOPES = {
    "p10-base23-retained-core-observations-v2":
        {"dwd_trading_calendar", "dwd_security_bar", "dwd_tradability_state"},
    "p10-base23-retained-typed-daily-core-v1":
        {"dwd_security_bar", "dwd_tradability_state", "dwd_security_valuation", "dwd_index_bar"},
    "p10-base23-retained-daily-measures-v1": {"dwd_security_valuation", "dwd_index_bar"},
    "p10-base23-retained-cninfo-latest-capture-v1": {"dwd_disclosure", "dwd_disclosure_subject"},
    "p10-base23-retained-dated-static-industry-v1": {
        "dwd_company_industry_membership", "dwd_industry_node", "dwd_industry_scheme",
        "dwd_listed_company", "dwd_security", "dwd_security_identifier", "dwd_trading_venue",
    },
    "p10-base23-retained-exact-statement-observations-v1":
        {"dwd_financial_report", "dwd_financial_fact", "dwd_financial_concept"},
    "p10-base23-retained-exact-quarterly-observations-v1":
        {"dwd_financial_report", "dwd_financial_fact", "dwd_financial_concept"},
    "p10-base23-retained-holder-count-exact13-v1": {"dwd_shareholder_count_observation"},
    "p10-base23-retained-f10-ownership-exact-v1":
        {"dwd_shareholding_observation", "dwd_share_capital_observation"},
    "p10-base23-retained-literal-dividend-exact-v1":
        {"dwd_corporate_action", "dwd_corporate_action_term"},
}


def scope_outputs(body: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Normalize the two existing explicit layouts; unknown scopes fail closed."""
    contract = body.get("contract_id")
    if (contract not in SCOPES
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(body.get(k) is not False for k in ("READY", "complete_Base23"))):
        raise BoundedError("SEPTEMBER_MERGE_SCOPE_UNQUALIFIED")
    layouts = [body.get("outputs", {})]
    if contract == "p10-base23-retained-daily-measures-v1":
        days = body.get("days", [])
        if not days or len(days) > 23 or len({i["date"] for i in days}) != len(days):
            raise BoundedError("SEPTEMBER_MERGE_DAILY_MEASURES_LAYOUT_INVALID")
        layouts = [i["outputs"] for i in days]
    normalized: dict[str, list[dict[str, Any]]] = {}
    physical = body.get("physical_members", {})
    seen = set()
    for layout in layouts:
        for table, values in layout.items():
            if table not in SCOPES[contract]:
                raise BoundedError("SEPTEMBER_MERGE_UNDECLARED_SCOPE_TABLE")
            for item in [values] if isinstance(values, dict) else values:
                path = item["path"]
                member = physical.get(path, {})
                rows = item.get("rows", item.get("semantic_scan", {}).get("rows"))
                part = item.get("partition")
                if contract == "p10-base23-retained-dated-static-industry-v1":
                    if path != f"payload/{table}/all/part-000000.parquet":
                        raise BoundedError("SEPTEMBER_MERGE_INDUSTRY_EXACT_FLAT_LAYOUT_CHANGED")
                    part = "all"
                if (not path.startswith("payload/"+table+"/") or path in seen
                    or any(p in {"", ".", ".."} for p in path.split("/"))
                    or not isinstance(rows, int) or isinstance(rows, bool) or rows <= 0
                    or not member.get("sha256") or member.get("size_bytes", 0) <= 0
                    or item.get("sha256", member["sha256"]) != member["sha256"]
                    or item.get("size_bytes", member["size_bytes"]) != member["size_bytes"]
                    or not part):
                    raise BoundedError("SEPTEMBER_MERGE_UNDECLARED_SCOPE_MEMBER")
                seen.add(path)
                normalized.setdefault(table, []).append({
                    **item, "sha256": member["sha256"], "size_bytes": member["size_bytes"],
                    "rows": rows, "partition": part,
                })
    if set(normalized) != SCOPES[contract]:
        raise BoundedError("SEPTEMBER_MERGE_EXACT_SCOPE_TABLE_SET_INVALID")
    return normalized


def prior_closure(
    knowledge_from: datetime, knowledge_to: datetime | None, successor: datetime,
) -> datetime | None:
    start, next_start = _clock(knowledge_from), _clock(successor)
    end = None if knowledge_to is None else _clock(knowledge_to)
    if next_start <= start:
        raise BoundedError("SEPTEMBER_MERGE_SUCCESSOR_NOT_STRICTLY_NEW")
    if end is not None and end > next_start:
        raise BoundedError("SEPTEMBER_MERGE_CLOSED_HISTORY_INTERVAL_CONFLICT")
    return next_start if end is None else end


def close_sparse_batch(
    batch: Any, *, row_offset: int, closures: list[tuple[int, datetime]],
) -> Any:
    """Change exactly registered row positions' knowledge_to, preserving atoms."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    if type(row_offset) is not int or row_offset < 0:
        raise BoundedError("SEPTEMBER_MERGE_ROW_OFFSET_INVALID")
    positions = [p for p, _ in closures]
    if (positions != sorted(set(positions))
        or any(type(p) is not int or not row_offset <= p < row_offset+batch.num_rows
               for p in positions)):
        raise BoundedError("SEPTEMBER_MERGE_SPARSE_CLOSURE_POSITIONS_INVALID")
    if not closures:
        return batch
    kf, kt = batch.column("knowledge_from"), batch.column("knowledge_to")
    indices = pc.index_in(pa.array(range(row_offset, row_offset+batch.num_rows), type=pa.int64()),
                          value_set=pa.array(positions, type=pa.int64()))
    successors = pa.array([_clock(c) for _, c in closures], type=kf.type)
    replacement = pc.take(successors, indices)
    selected = pc.is_valid(indices)
    bad_start = pc.and_(selected, pc.less_equal(replacement, kf))
    if pc.any(bad_start).as_py():
        raise BoundedError("SEPTEMBER_MERGE_SUCCESSOR_NOT_STRICTLY_NEW")
    if pc.any(pc.and_(selected, pc.is_valid(kt))).as_py():
        raise BoundedError("SEPTEMBER_MERGE_CLOSED_HISTORY_ROW_TARGETED")
    result = batch.set_column(batch.schema.get_field_index("knowledge_to"),
                              batch.schema.field("knowledge_to"),
                              pc.if_else(selected, replacement, kt))
    if not result.schema.equals(batch.schema):
        raise BoundedError("SEPTEMBER_MERGE_PHYSICAL_SCHEMA_CHANGED")
    for name in batch.schema.names:
        if name != "knowledge_to" and not result.column(name).equals(batch.column(name)):
            raise BoundedError("SEPTEMBER_MERGE_ORIGINAL_TYPED_ATOM_CHANGED")
    return result
