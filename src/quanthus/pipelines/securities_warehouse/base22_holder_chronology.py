"""Same-provider source snapshots establish a new governance version, never old PIT.

Complete retained raw observations are replayed once per stock. Their real
acquisition order selects the latest literal body, including disclosed revisions.
Unknown/equal conflicting clocks, different providers and unproved native origin
remain blocked. Earlier raw observations remain evidence, not fabricated accepted
knowledge intervals between the old native version and this governance decision.
"""

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
from .base22_holder_column_revision import _CLASS, _KEYS, _META, TABLE, _body, _key
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    _effective_start,
    ownership_capital_schemas,
    parse_f10_shareholding_tables,
)

CONTRACT_ID = "p10-base22-same-provider-holder-retained-chronology-v1"
_ALLOWED = _CLASS | {
    "holding_percentage",
    "raw_holding_percentage",
    "holding_quantity",
    "raw_holding_quantity",
}


def _native_origin(
    raw: dict[str, Any], source_sha256: str, resolver: FinancialCompanyResolver
) -> dict[str, dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    acquisition = _clock(raw["acquired_at"])
    schema = ownership_capital_schemas()[TABLE]
    with localcontext() as context:
        context.prec = 64
        parsed = parse_f10_shareholding_tables(raw["content"])
    if not 0 < len(parsed) <= 4096:
        raise BoundedError("HOLDER_CHRONOLOGY_ORIGINAL_LEXICAL_SCOPE_INVALID")
    result = {}
    for item in parsed:
        parent, _ = resolver.resolve_for_report(
            raw["stock_code"], period_end=item["observation_date"]
        )
        if parent is None:
            continue
        row = {
            "record_source": raw["source"],
            "source_record_id": (
                f"stock_code={raw['stock_code']}&category=股东研究"
                f"&content_sha256={raw['content_sha256']}"
            ),
            "source_revision": raw["source_revision"],
            "source_artifact_sha256": source_sha256,
            "knowledge_from": max(acquisition, parent.knowledge_from),
            "knowledge_to": None,
            "observation_start": _effective_start(item["observation_date"]),
            "observation_end": None,
            "company_id": parent.company_id,
            "security_id": parent.security_id,
            "shareholder_id": None,
            **{k: v for k, v in item.items() if k != "observation_date"},
        }
        row = pa.Table.from_pylist([row], schema=schema).to_pylist()[0]
        key = _key(row)
        if key in result:
            raise BoundedError("HOLDER_CHRONOLOGY_ORIGINAL_DUPLICATE_KEY")
        result[key] = row
    return result


def qualify_retained_chronology(
    native: list[dict[str, Any]],
    weekly: list[dict[str, Any]],
    *,
    original_raw: dict[str, Any],
    original_member_sha256: str,
    later_raw: list[tuple[str, dict[str, Any]]],
    resolver: FinancialCompanyResolver,
    rule: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Scope is explicit native keys and all retained witnesses, not a feed priority."""
    import pyarrow as pa

    clock = _clock(observed_at)
    if (
        not 0 < len(native) <= 80
        or len(weekly) > 5120
        or not 1 <= len(later_raw) <= 4
        or rule.get("business_key") != _KEYS
        or rule.get("version_key") != _KEYS + ["knowledge_from"]
        or any(r.get("knowledge_to") is not None for r in native + weekly)
    ):
        raise BoundedError("HOLDER_CHRONOLOGY_MODEL_OR_SELECTED_SCOPE_INVALID")
    # These calls validate original raw hash, literal units, schema, dated identity
    # and real acquisition. No partial/unparseable category can establish priority.
    _, original_proof = holder_observation(
        original_raw, source_sha256=original_member_sha256, resolver=resolver
    )
    origin = _native_origin(original_raw, original_member_sha256, resolver)
    original_clock = original_proof["actual_acquisition"]
    before = {_key(r): r for r in native}
    if len(before) != len(native) or clock <= original_clock:
        raise BoundedError("HOLDER_CHRONOLOGY_NATIVE_DUPLICATE_OR_GOVERNANCE_CLOCK_INVALID")
    expected = {}
    raw_closure = []
    frames = []
    seen_members = set()
    for sha, raw in later_raw:
        if (
            sha in seen_members
            or raw.get("stock_code") != original_raw.get("stock_code")
            or raw.get("source") != original_raw.get("source")
        ):
            raise BoundedError("HOLDER_CHRONOLOGY_DIFFERENT_PROVIDER_CODE_OR_DUPLICATE_MEMBER")
        seen_members.add(sha)
        rows, proof = holder_observation(raw, source_sha256=sha, resolver=resolver)
        acquisition = proof["actual_acquisition"]
        if not original_clock < acquisition < clock:
            raise BoundedError("HOLDER_CHRONOLOGY_ACTUAL_OBSERVATION_ORDER_UNAVAILABLE")
        raw_closure.append(
            {
                "member_sha256": sha,
                "raw_row_sha256": digest(raw),
                "projection_identity": proof["identity"],
                "actual_acquisition": acquisition,
            }
        )
        frames.append(
            (
                acquisition,
                {
                    (r["company_id"], r["security_id"], r["holding_type"], r["observation_start"])
                    for r in rows[TABLE]
                },
                {_key(r) for r in rows[TABLE]},
            )
        )
        for row in rows[TABLE]:
            raw_binding = (sha, row["source_record_id"], _key(row))
            if raw_binding in expected:
                raise BoundedError("HOLDER_CHRONOLOGY_RAW_DUPLICATE_OBSERVATION")
            expected[raw_binding] = (row, acquisition)
    groups: dict[str, list[tuple[dict[str, Any], datetime]]] = defaultdict(list)
    seen_rows = set()
    for row in weekly:
        key = _key(row)
        binding = (row["source_artifact_sha256"], row["source_record_id"], key)
        if key not in before or binding not in expected or binding in seen_rows:
            raise BoundedError("HOLDER_CHRONOLOGY_COMPLETE_WITNESS_BINDING_INVALID")
        seen_rows.add(binding)
        raw_row, acquisition = expected[binding]
        if digest(row) != digest(raw_row):
            raise BoundedError("HOLDER_CHRONOLOGY_TYPED_WITNESS_RAW_MISMATCH")
        groups[key].append((row, acquisition))
    relevant = {k for k in expected if k[2] in before}
    if seen_rows != relevant:
        raise BoundedError("HOLDER_CHRONOLOGY_MISSING_RETAINED_RELEVANT_WITNESS")
    closure = {
        "contract_id": CONTRACT_ID,
        "original_member_sha256": original_member_sha256,
        "original_raw_row_sha256": digest(original_raw),
        "original_projection_identity": original_proof["identity"],
        "original_actual_acquisition": original_clock,
        "later_complete_raw_closure": raw_closure,
        "native_atoms": sorted(digest(r) for r in native),
        "all_weekly_atoms": sorted(digest(r) for r in weekly),
        "rule_sha256": digest(rule),
        "observed_at": clock,
        "evaluator_sha256": file_sha(Path(__file__)),
    }
    identity = digest(closure)
    revisions, held = {}, {}
    schema = ownership_capital_schemas()[TABLE]
    for key, row in sorted(before.items()):
        try:
            if key not in origin or digest(origin[key]) != digest(row):
                raise BoundedError("HOLDER_CHRONOLOGY_EXACT_ORIGINAL_NATIVE_BINDING_FAILED")
            observations = groups[key]
            if (
                not observations
                or len(observations) >= 64
                or clock <= _clock(row["knowledge_from"])
            ):
                raise BoundedError("HOLDER_CHRONOLOGY_WITNESS_BOUND_OR_NEW_CLOCK_INVALID")
            by_clock: dict[datetime, set[str]] = defaultdict(set)
            for candidate, acquisition in observations:
                by_clock[acquisition].add(_body(candidate))
            if any(len(bodies) != 1 for bodies in by_clock.values()):
                raise BoundedError("HOLDER_CHRONOLOGY_EQUAL_ACQUISITION_BODY_CONFLICT")
            latest_at = max(by_clock)
            frame = (
                row["company_id"],
                row["security_id"],
                row["holding_type"],
                row["observation_start"],
            )
            if any(
                at > latest_at and frame in available and key not in raw_keys
                for at, available, raw_keys in frames
            ):
                raise BoundedError("HOLDER_CHRONOLOGY_LATER_FRAME_OMISSION_NEEDS_DISPOSITION")
            latest = [r for r, acquisition in observations if acquisition == latest_at]
            candidate = min(
                latest, key=digest
            )  # Only identical bodies at the latest proven instant.
            changes = {
                k for k in candidate if k not in _META and digest(row[k]) != digest(candidate[k])
            }
            # A later source can reconfirm the native body after an intervening
            # source revision. It still has a new, real governance interval.
            if not changes <= _ALLOWED:
                raise BoundedError("HOLDER_CHRONOLOGY_UNREGISTERED_BODY_CHANGE")
            after = {
                **candidate,
                "knowledge_from": clock,
                "knowledge_to": None,
                "source_revision": CONTRACT_ID + ":" + identity.removeprefix("sha256:"),
                "source_artifact_sha256": identity,
            }
            output = [{**row, "knowledge_to": clock}, after]
            if pa.Table.from_pylist(output, schema=schema).to_pylist() != output:
                raise BoundedError("HOLDER_CHRONOLOGY_SERIALIZED_NUMERIC_ATOM_OR_INSTANT_CHANGED")
            item = {
                "contract_id": CONTRACT_ID,
                "business_key_sha256": key,
                "input_identity": identity,
                "observed_at": clock,
                "native_row_sha256": digest(row),
                "latest_literal_source_atom_sha256": digest(candidate),
                "latest_actual_acquisition": latest_at,
                "retained_source_observation_sha256s": sorted(digest(r) for r, _ in observations),
                "actual_acquisition_instants": sorted(by_clock),
                "changed_fields": sorted(changes),
                "latest_source_reconfirms_native_body": not changes,
                "complete_group_census": dict(
                    Counter(digest(r) for r in [row, *[r for r, _ in observations]])
                ),
                "earlier_source_observations_preserved_as_evidence_not_inferred_PIT": True,
                "output": output,
            }
            item["identity"] = digest(item)
            revisions[key] = item
        except BoundedError as exc:
            held[key] = str(exc)
    result = {
        **closure,
        "input_identity": identity,
        "revisions": revisions,
        "held": held,
        "different_provider_priority_granted": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result


def apply_retained_chronology(
    rows: list[dict[str, Any]], rule: dict[str, Any], proof: dict[str, Any]
) -> list[dict[str, Any]]:
    if (
        proof.get("contract_id") != CONTRACT_ID
        or proof.get("identity") != digest({k: v for k, v in proof.items() if k != "identity"})
        or len(rows) > 64
        or not rows
        or dict(Counter(digest(r) for r in rows)) != proof.get("complete_group_census")
        or {_key(r) for r in rows} != {proof.get("business_key_sha256")}
        or rule.get("business_key") != _KEYS
        or rule.get("version_key") != _KEYS + ["knowledge_from"]
    ):
        raise BoundedError("HOLDER_CHRONOLOGY_COMPLETE_QUALIFIED_GROUP_CHANGED")
    return [dict(r) for r in proof["output"]]
