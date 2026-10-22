"""Source-proved classification text restoration, never a numeric priority rule."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from decimal import localcontext
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_holder_column_continuation import holder_observation
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    _effective_start,
    ownership_capital_schemas,
    parse_f10_shareholding_tables,
)

CONTRACT_ID = "p10-base22-holder-original-column-text-restoration-v1"
TABLE = "dwd_shareholding_observation"
_META = {
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "knowledge_from",
    "knowledge_to",
}
_KEYS = [
    "company_id",
    "security_id",
    "shareholder_source_name",
    "holding_type",
    "observation_start",
    "holder_rank",
]
_CLASS = {"holder_category", "share_nature"}


def _body(row: dict[str, Any]) -> str:
    return digest({k: v for k, v in row.items() if k not in _META})


def _key(row: dict[str, Any]) -> str:
    return digest([row[k] for k in _KEYS])


def qualify_column_restorations(
    native: list[dict[str, Any]],
    weekly: list[dict[str, Any]],
    *,
    original_raw: dict[str, Any],
    original_member_sha256: str,
    actual_weekly_acquisitions: dict[tuple[str, str], datetime],
    resolver: FinancialCompanyResolver,
    rule: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Parse the original once per stock; all selected keys share that result.

    The caller must pin the complete native/group selection and the independently
    replayed immutable weekly source scope. Acquisitions come from its original
    local raw rows, not package creation, META or inferred publication dates.
    This function grants a narrow new revision only; complete native union,
    source-category coverage and consumer admission remain separate gates.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    observed = _clock(observed_at)
    if (
        not 0 < len(native) <= 80
        or len(weekly) > 5120
        or rule.get("business_key") != _KEYS
        or rule.get("version_key") != _KEYS + ["knowledge_from"]
        or original_raw.get("category") != "股东研究"
        or any(r.get("knowledge_to") is not None for r in native + weekly)
    ):
        raise BoundedError("HOLDER_COLUMN_REPAIR_BOUND_OR_MODEL_SCOPE_INVALID")
    # This verifies original content hash, literal cells, Decimal, actual clock
    # and dated identity before any original-prefix or later-witness comparison.
    restored_rows, original_proof = holder_observation(
        original_raw, source_sha256=original_member_sha256, resolver=resolver
    )
    original_clock = original_proof["actual_acquisition"]
    if observed <= original_clock:
        raise BoundedError("HOLDER_COLUMN_REPAIR_GOVERNANCE_CLOCK_NOT_LATER")
    canonical_original = {_key(r): r for r in restored_rows[TABLE]}
    original_before = {}
    with localcontext() as context:
        context.prec = 64
        parsed = parse_f10_shareholding_tables(original_raw["content"])
    if not 0 < len(parsed) <= 4096:
        raise BoundedError("HOLDER_COLUMN_REPAIR_ORIGINAL_LEXICAL_BOUND_INVALID")
    schema = ownership_capital_schemas()[TABLE]
    for item in parsed:
        company_identity, _ = resolver.resolve_for_report(
            original_raw["stock_code"], period_end=item["observation_date"]
        )
        if company_identity is None:
            continue
        row = {
            "record_source": original_raw["source"],
            "source_record_id": (
                f"stock_code={original_raw['stock_code']}&category=股东研究&"
                f"content_sha256={original_raw['content_sha256']}"
            ),
            "source_revision": original_raw["source_revision"],
            "source_artifact_sha256": original_member_sha256,
            "knowledge_from": max(original_clock, company_identity.knowledge_from),
            "knowledge_to": None,
            "observation_start": _effective_start(item["observation_date"]),
            "observation_end": None,
            "company_id": company_identity.company_id,
            "security_id": company_identity.security_id,
            "shareholder_id": None,
            **{k: v for k, v in item.items() if k != "observation_date"},
        }
        typed = pa.Table.from_pylist([row], schema=schema).to_pylist()[0]
        ident = _key(typed)
        if ident in original_before:
            raise BoundedError("HOLDER_COLUMN_REPAIR_ORIGINAL_DUPLICATE_KEY")
        original_before[ident] = typed
    native_by_key = {_key(r): r for r in native}
    if len(native_by_key) != len(native):
        raise BoundedError("HOLDER_COLUMN_REPAIR_NATIVE_DUPLICATE_KEY")
    later: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in weekly:
        ident = _key(row)
        if ident not in native_by_key:
            raise BoundedError("HOLDER_COLUMN_REPAIR_UNREGISTERED_WEEKLY_KEY")
        later[ident].append(row)
    closure = {
        "contract_id": CONTRACT_ID,
        "observed_at": observed,
        "original_member_sha256": original_member_sha256,
        "original_raw_row_sha256": digest(original_raw),
        "original_column_projection_identity": original_proof["identity"],
        "original_actual_acquisition": original_clock,
        "native_atoms": sorted(digest(r) for r in native),
        "all_weekly_atoms": sorted(digest(r) for r in weekly),
        "actual_weekly_acquisitions": sorted(
            [sha, record, _clock(clock)]
            for (sha, record), clock in actual_weekly_acquisitions.items()
        ),
        "rule_sha256": digest(rule),
        "evaluator_sha256": file_sha(Path(__file__)),
        "projector_sha256": file_sha(
            Path(__file__).with_name("base22_holder_column_continuation.py")
        ),
    }
    identity = digest(closure)
    revisions, held = {}, {}
    for ident, before in sorted(native_by_key.items()):
        try:
            old, after = original_before.get(ident), canonical_original.get(ident)
            witnesses = later[ident]
            if (
                old is None
                or after is None
                or _body(old) != _body(before)
                or before["source_artifact_sha256"] != original_member_sha256
                or before["record_source"] != original_raw["source"]
                or before["knowledge_from"] != old["knowledge_from"]
            ):
                raise BoundedError("HOLDER_COLUMN_REPAIR_EXACT_ORIGINAL_NATIVE_BINDING_FAILED")
            fields = {k for k in after if k not in _META and digest(before[k]) != digest(after[k])}
            if len(fields) != 1 or not fields <= _CLASS:
                raise BoundedError("HOLDER_COLUMN_REPAIR_NOT_EXACT_ORIGINAL_TEXT_RESTORATION")
            if not witnesses or len(witnesses) + 1 > 64:
                raise BoundedError("HOLDER_COLUMN_REPAIR_LATER_WITNESS_UNAVAILABLE")
            clocks = []
            for row in witnesses:
                acquisition = actual_weekly_acquisitions.get(
                    (row["source_artifact_sha256"], row["source_record_id"])
                )
                if acquisition is None:
                    raise BoundedError("HOLDER_COLUMN_REPAIR_ACTUAL_LATER_ACQUISITION_UNAVAILABLE")
                instant = _clock(acquisition)
                if (
                    not original_clock < instant < observed
                    or row["record_source"] != original_raw["source"]
                    or _body(row) != _body(after)
                    or row["knowledge_from"] != max(instant, old["knowledge_from"])
                ):
                    raise BoundedError("HOLDER_COLUMN_REPAIR_LATER_BODY_OR_CHRONOLOGY_CONFLICT")
                clocks.append(instant)
            if len(set(clocks)) != len(clocks) or observed <= _clock(before["knowledge_from"]):
                raise BoundedError("HOLDER_COLUMN_REPAIR_DISTINCT_CLOCK_OR_GOVERNANCE_INVALID")
            repaired = {
                **after,
                "knowledge_from": observed,
                "knowledge_to": None,
                "source_revision": CONTRACT_ID + ":" + identity.removeprefix("sha256:"),
                "source_artifact_sha256": identity,
            }
            outputs = [{**before, "knowledge_to": observed}, repaired]
            if pa.Table.from_pylist(outputs, schema=schema).to_pylist() != outputs:
                raise BoundedError("HOLDER_COLUMN_REPAIR_SERIALIZED_ATOM_OR_TIME_CHANGED")
            item = {
                "contract_id": CONTRACT_ID,
                "business_key_sha256": ident,
                "input_identity": identity,
                "observed_at": observed,
                "native_row_sha256": digest(before),
                "original_restored_row_sha256": digest(after),
                "complete_group_census": dict(Counter(digest(r) for r in [before, *witnesses])),
                "original_and_later_actual_acquisitions": [original_clock, *sorted(clocks)],
                "changed_fields": sorted(fields),
                "output": outputs,
            }
            item["identity"] = digest(item)
            revisions[ident] = item
        except BoundedError as exc:
            held[ident] = str(exc)
    result = {
        **closure,
        "input_identity": identity,
        "revisions": revisions,
        "held": held,
        "original_native_rows_changed_in_place": False,
        "numeric_or_other_body_priority_granted": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result


def apply_column_restoration(
    rows: list[dict[str, Any]], rule: dict[str, Any], proof: dict[str, Any]
) -> list[dict[str, Any]]:
    """Consume only the raw-replayed exact complete selected source group."""
    if (
        proof.get("contract_id") != CONTRACT_ID
        or proof.get("identity") != digest({k: v for k, v in proof.items() if k != "identity"})
        or not 0 < len(rows) <= 64
        or dict(Counter(digest(r) for r in rows)) != proof["complete_group_census"]
        or {digest([r[k] for k in rule["business_key"]]) for r in rows}
        != {proof["business_key_sha256"]}
    ):
        raise BoundedError("HOLDER_COLUMN_REPAIR_COMPLETE_QUALIFIED_GROUP_CHANGED")
    return [dict(r) for r in proof["output"]]
