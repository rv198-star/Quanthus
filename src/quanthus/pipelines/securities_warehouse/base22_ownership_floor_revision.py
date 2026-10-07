"""Explicit new-knowledge repairs for source captures collapsed by a parent floor.

No historical priority is inferred from a package date. The exact native atom
must reproduce from its original provider row; the changed row must be an actual
later capture from the same provider before the common parent knowledge floor.
Every later retained observation must agree. A new governance version retains
the old native atom and archives every original acquisition without backdating.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import _physical, canonical, digest, file_sha
from .base22_bounded import BoundedError
from .base22_f10_ownership import TABLES, ownership_observation
from .base22_financial import _clock
from .financial_reporting import FinancialCompanyResolver

CONTRACT_ID = "p10-ownership-parent-floor-repair-v1"
_LINEAGE = {
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "knowledge_from",
    "knowledge_to",
}


def _body(row: dict[str, Any]) -> str:
    return digest({k: v for k, v in row.items() if k not in _LINEAGE})


def qualify_floor_revisions(
    native: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    resolver: FinancialCompanyResolver,
    rule: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Qualify a bounded exact source closure; never select an arbitrary provider."""
    observed = _clock(observed_at)
    if not 0 < len(native) <= 64 or not 2 <= len(sources) <= 8:
        raise BoundedError("OWNERSHIP_FLOOR_SELECTED_SCOPE_BOUND_INVALID")
    if any(r.get("knowledge_to") is not None for r in native):
        raise BoundedError("OWNERSHIP_FLOOR_CLOSED_NATIVE_VERSION_UNSUPPORTED")
    if (
        len({(s["raw"]["source"], s["raw"]["stock_code"], s["raw"]["category"]) for s in sources})
        != 1
        or sources[0]["raw"]["category"] != "股本结构"
    ):
        raise BoundedError("OWNERSHIP_FLOOR_PROVIDER_OR_CATEGORY_UNQUALIFIED")
    key_fields = rule["business_key"]

    def key(row: dict[str, Any]) -> str:
        return digest([row[k] for k in key_fields])

    old_by_key = {key(r): r for r in native}
    if len(old_by_key) != len(native):
        raise BoundedError("OWNERSHIP_FLOOR_DUPLICATE_NATIVE_BUSINESS_KEY")
    parsed = []
    clocks = []
    for source in sources:
        rows, evidence = ownership_observation(
            source["raw"], source_sha256=source["source_sha256"], resolver=resolver
        )
        clock = evidence["actual_acquisition"]
        if clock > observed:
            raise BoundedError("OWNERSHIP_FLOOR_SOURCE_TIME_OR_IDENTITY_UNQUALIFIED")
        clocks.append(clock)
        parsed.append({key(r): r for r in rows[TABLES[0]]})
    if clocks != sorted(clocks) or len(set(clocks)) != len(clocks):
        raise BoundedError("OWNERSHIP_FLOOR_ACTUAL_SOURCE_CHRONOLOGY_UNQUALIFIED")
    closure = {
        "contract_id": CONTRACT_ID,
        "observed_at": observed,
        "native_atoms": sorted(digest(r) for r in native),
        "source_rows": [
            {
                "raw_row_sha256": digest(s["raw"]),
                "source_sha256": s["source_sha256"],
                "actual_acquisition": c,
            }
            for s, c in zip(sources, clocks, strict=True)
        ],
        "rule_sha256": digest(rule),
        "evaluator_sha256": file_sha(Path(__file__)),
        "projector_sha256": file_sha(Path(__file__).with_name("base22_f10_ownership.py")),
    }
    input_identity = digest(closure)
    revisions = {}
    for ident, old in old_by_key.items():
        if any(ident not in p for p in parsed):
            raise BoundedError("OWNERSHIP_FLOOR_SELECTED_KEY_RAW_CLOSURE_MISSING")
        original = parsed[0][ident]
        after = [p[ident] for p in parsed[1:]]
        floor = _clock(old["knowledge_from"])
        if (
            _body(original) != _body(old)
            or original["source_artifact_sha256"] != old["source_artifact_sha256"]
            or original["record_source"] != old["record_source"]
            or original["knowledge_from"] != floor
        ):
            raise BoundedError("OWNERSHIP_FLOOR_EXACT_ORIGINAL_NATIVE_RAW_BINDING_FAILED")
        # This narrow successor handles precisely one changed pre-floor capture.
        # A different later body or another pre-floor revision needs a separate
        # contract, rather than a last/largest/source-priority guess.
        if (
            not clocks[0] < clocks[1] < floor < observed
            or after[0]["knowledge_from"] != floor
            or any(c < floor for c in clocks[2:])
            or _body(old) == _body(after[0])
            or any(_body(r) != _body(after[0]) for r in after)
        ):
            raise BoundedError("OWNERSHIP_FLOOR_EXACT_CHANGED_SOURCE_SEQUENCE_UNQUALIFIED")
        new = {
            **after[0],
            "knowledge_from": observed,
            "knowledge_to": None,
            "source_revision": CONTRACT_ID + ":" + input_identity.removeprefix("sha256:"),
            "source_artifact_sha256": input_identity,
        }
        item = {
            "contract_id": CONTRACT_ID,
            "business_key_sha256": ident,
            "input_identity": input_identity,
            "complete_group_census": dict(Counter(digest(r) for r in [old, *after])),
            "native_row_sha256": digest(old),
            "selected_source_row_sha256": digest(after[0]),
            "observed_at": observed,
            "output": [{**old, "knowledge_to": observed}, new],
        }
        item["identity"] = digest(item)
        revisions[ident] = item
    result = {
        **closure,
        "input_identity": input_identity,
        "revisions": revisions,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result


def apply_floor_revision(
    rows: list[dict[str, Any]], rule: dict[str, Any], proof: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply only a previously raw-reproduced revision to the exact whole group."""
    if (
        proof.get("contract_id") != CONTRACT_ID
        or proof.get("identity") != digest({k: v for k, v in proof.items() if k != "identity"})
        or not 0 < len(rows) <= 64
        or dict(Counter(digest(r) for r in rows)) != proof["complete_group_census"]
        or {digest([r[k] for k in rule["business_key"]]) for r in rows}
        != {proof["business_key_sha256"]}
    ):
        raise BoundedError("OWNERSHIP_FLOOR_COMPLETE_QUALIFIED_GROUP_CHANGED")
    # The offline verifier regenerates the proof from local original raw atoms,
    # native files, actual governance clock and code before calling this helper.
    return [dict(r) for r in proof["output"]]


def replay_floor_evidence(
    root: Path,
    item: dict[str, Any],
    *,
    resolver: FinancialCompanyResolver,
    rule: dict[str, Any],
    schema: Any,
) -> dict[str, Any]:
    """Requalify the exact repair from local typed originals, never source roots."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    p = _physical(root, item["native_before"], independent=True)
    pf = pq.ParquetFile(p)
    if not 0 < pf.metadata.num_rows <= 64 or not pf.schema_arrow.equals(
        schema, check_metadata=False
    ):
        raise BoundedError("OWNERSHIP_FLOOR_LOCAL_NATIVE_SCHEMA_OR_BOUND_INVALID")
    native = pf.read().to_pylist()
    if not 2 <= len(item["raw_inputs"]) <= 8:
        raise BoundedError("OWNERSHIP_FLOOR_LOCAL_SOURCE_COUNT_INVALID")
    sources = []
    for member in item["raw_inputs"]:
        path = _physical(root, member["path"], independent=True)
        raw_file = pq.ParquetFile(path)
        if raw_file.metadata.num_rows != 1 or path.stat().st_size > 2 * 1024**2:
            raise BoundedError("OWNERSHIP_FLOOR_LOCAL_RAW_ROW_BOUND_INVALID")
        row = raw_file.read().to_pylist()[0]
        if digest(row) != member["raw_row_sha256"]:
            raise BoundedError("OWNERSHIP_FLOOR_LOCAL_ORIGINAL_RAW_ROW_CHANGED")
        sources.append({"raw": row, "source_sha256": member["source_sha256"]})
    proof = qualify_floor_revisions(
        native, sources, resolver=resolver, rule=rule, observed_at=_clock(item["observed_at"])
    )
    if (
        file_sha(_physical(root, item["input_artifact"], independent=True))
        != proof["input_identity"]
    ):
        raise BoundedError("OWNERSHIP_FLOOR_EXACT_INPUT_ARTIFACT_CHANGED")
    expected = _physical(root, item["qualification"], independent=True).read_bytes()
    if expected != canonical(proof) + b"\n":
        raise BoundedError("OWNERSHIP_FLOOR_REPRODUCED_QUALIFICATION_CHANGED")
    return proof
