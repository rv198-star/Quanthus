"""Uncontested new action entities; a different source ID never permits replacement.

The natural-date census covers every selected native and competing-provider action.
Qualification of raw sources, old normalization restoration and full-domain admission
remain separate gates. Selected observations retain their original IDs and clocks.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError, Budget, Member
from .base22_tail_union import member_identity

CONTRACT_ID = "p10-base22-uncontested-absent-action-entities-v1"
MAX_NATIVE_ROWS = 131072
MAX_PROVIDER_ROWS = 65536
KEY = ("company_id", "security_id", "ex_date")
PROJECTION = ("corporate_action_id", *KEY)


def _action_key(row: dict[str, Any]) -> tuple[Any, ...]:
    if any(not isinstance(row.get(f), str) or not row[f] for f in PROJECTION if f != "ex_date"):
        raise BoundedError("ACTION_UNION_IDENTITY_INVALID")
    day = row["ex_date"]
    if day is not None and (type(day) is not date or day > THROUGH):
        raise BoundedError("ACTION_UNION_BUSINESS_DATE_INVALID")
    return tuple(row[k] for k in KEY)


def select_uncontested_actions(
    native: list[Member],
    incoming: list[Member],
    competing: list[Member],
    *,
    schema: Any,
    budget: Budget,
) -> dict[str, Any]:
    """Only one-provider entities absent by dated identity from all other inputs.

    No numerical comparison or priority decision is made. Even identical source
    observations are held when a native/competing date exists. Full input inventory
    completeness and source eligibility are supplied by the frozen job contract.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    all_members = [*native, *incoming, *competing]
    if not native or not incoming or len({m.path for m in all_members}) != len(all_members):
        raise BoundedError("ACTION_UNION_EXACT_INPUT_SET_INVALID")
    groups = {"native": native, "incoming": incoming, "competing": competing}
    keys: dict[str, set[tuple[Any, ...]]] = {}
    incoming_rows: dict[str, tuple[Any, ...]] = {}
    counts: dict[str, int] = {}
    fingerprints = [m.verify() for m in all_members]
    for role, members in groups.items():
        limit = MAX_NATIVE_ROWS if role == "native" else MAX_PROVIDER_ROWS
        if sum(m.rows for m in members) > limit:
            raise BoundedError("ACTION_UNION_ROW_SCOPE_EXCEEDED")
        keys[role] = set()
        count = 0
        for member in members:
            file = pq.ParquetFile(member.path)
            if file.metadata.num_rows != member.rows or not file.schema_arrow.equals(schema):
                raise BoundedError("ACTION_UNION_SCHEMA_OR_CENSUS_INVALID")
            for batch in file.iter_batches(batch_size=budget.limits.batch_rows, columns=PROJECTION):
                budget.check()
                for row in batch.to_pylist():
                    key = _action_key(row)
                    keys[role].add(key)
                    count += 1
                    if role == "incoming":
                        identifier = row["corporate_action_id"]
                        if identifier in incoming_rows:
                            raise BoundedError("ACTION_UNION_PROVIDER_ENTITY_NOT_UNIQUE")
                        incoming_rows[identifier] = key
        if count != sum(m.rows for m in members):
            raise BoundedError("ACTION_UNION_INPUT_ROW_CONSERVATION_FAILED")
        counts[role] = count
    multiplicity = Counter(incoming_rows.values())
    dispositions: Counter[str] = Counter()
    chosen = []
    for identifier, key in incoming_rows.items():
        reason = (
            "EX_DATE_UNAVAILABLE"
            if key[-1] is None
            else "NATIVE_DATED_EVENT_EXISTS"
            if key in keys["native"]
            else "OTHER_QUALIFIED_PROVIDER_DATED_EVENT_EXISTS"
            if key in keys["competing"]
            else "MULTIPLE_THIS_PROVIDER_ENTITIES"
            if multiplicity[key] != 1
            else "UNCONTESTED_ABSENT_DATED_EVENT"
        )
        dispositions[reason] += 1
        if reason == "UNCONTESTED_ABSENT_DATED_EVENT":
            chosen.append(identifier)
    if any(m.verify() != fp for m, fp in zip(all_members, fingerprints, strict=True)):
        raise BoundedError("ACTION_UNION_INPUT_CHANGED")
    result = {
        "contract_id": CONTRACT_ID,
        "input_members": {role: [member_identity(m) for m in ms] for role, ms in groups.items()},
        "input_rows": counts,
        "native_distinct_dated_event_keys": len(keys["native"]),
        "dispositions": dict(sorted(dispositions.items())),
        "selected_entity_ids": sorted(chosen),
        "selected_entity_id_census_sha256": digest(sorted(chosen)),
        "source_priority_granted": False,
        "native_rows_replaced": 0,
        "original_normalization_restored": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result


def selected_rows(
    members: list[Member], *, selected_ids: set[str], schema: Any, budget: Budget
) -> Any:
    """Stream exact old typed observations; no rounding, clock or identity rewrite."""
    import pyarrow.parquet as pq

    if len(selected_ids) > MAX_PROVIDER_ROWS:
        raise BoundedError("ACTION_UNION_SELECTION_SCOPE_EXCEEDED")
    for member in members:
        before = member.verify()
        file = pq.ParquetFile(member.path)
        if file.metadata.num_rows != member.rows or not file.schema_arrow.equals(schema):
            raise BoundedError("ACTION_UNION_SCHEMA_OR_CENSUS_INVALID")
        for batch in file.iter_batches(batch_size=budget.limits.batch_rows):
            budget.check()
            for row in batch.to_pylist():
                if row["corporate_action_id"] in selected_ids:
                    yield row
        if member.verify() != before:
            raise BoundedError("ACTION_UNION_INPUT_CHANGED")
