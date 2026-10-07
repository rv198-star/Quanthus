"""Exact holder repairs with explicitly proved four-frame forward rolling windows.

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
from zoneinfo import ZoneInfo

from .base22 import _physical, canonical, digest, file_sha
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_holder_literals import TABLE
from .base22_holder_literals import holder_observation as ownership_observation
from .financial_reporting import FinancialCompanyResolver

TABLES = (TABLE,)
CONTRACT_ID = "p10-holder-four-frame-floor-repair-v3"
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


def _day(row: dict[str, Any]) -> str:
    return _clock(row["observation_start"]).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _frames(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Both top-ten types must publish all ten ranks at each of four dates."""
    frames: dict[str, set[str]] = {}
    counts: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        day = (
            _clock(row["observation_start"])
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .date()
            .isoformat()
        )
        kind = row["holding_type"]
        if kind not in {"TOP_SHAREHOLDERS", "TOP_FLOATING_SHAREHOLDERS"}:
            raise BoundedError("HOLDER_ROLLING_EXACT_FRAME_TYPE_REQUIRED")
        counts.setdefault((day, kind), []).append(row["holder_rank"])
        frames.setdefault(day, set()).add(kind)
    if (
        len(frames) != 4
        or len(rows) != 80
        or any(
            kinds != {"TOP_SHAREHOLDERS", "TOP_FLOATING_SHAREHOLDERS"} for kinds in frames.values()
        )
        or any(sorted(ranks) != list(range(1, 11)) for ranks in counts.values())
    ):
        raise BoundedError("HOLDER_ROLLING_COMPLETE_FOUR_TOP_TEN_FRAMES_REQUIRED")
    return frames


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
    if len(native) != 80 or not 3 <= len(sources) <= 8:
        raise BoundedError("OWNERSHIP_FLOOR_SELECTED_SCOPE_BOUND_INVALID")
    if any(r.get("knowledge_to") is not None for r in native):
        raise BoundedError("OWNERSHIP_FLOOR_CLOSED_NATIVE_VERSION_UNSUPPORTED")
    if (
        len({(s["raw"]["source"], s["raw"]["stock_code"], s["raw"]["category"]) for s in sources})
        != 1
        or sources[0]["raw"]["category"] != "股东研究"
    ):
        raise BoundedError("OWNERSHIP_FLOOR_PROVIDER_OR_CATEGORY_UNQUALIFIED")
    key_fields = rule["business_key"]
    if key_fields != [
        "company_id",
        "security_id",
        "shareholder_source_name",
        "holding_type",
        "observation_start",
        "holder_rank",
    ] or rule["version_key"] != key_fields + ["knowledge_from"]:
        raise BoundedError("HOLDER_FLOOR_EXACT_MODEL_KEYS_REQUIRED")

    def key(row: dict[str, Any]) -> str:
        return digest([row[k] for k in key_fields])

    old_by_key = {key(r): r for r in native}
    if len(old_by_key) != len(native):
        raise BoundedError("OWNERSHIP_FLOOR_DUPLICATE_NATIVE_BUSINESS_KEY")
    native_frames = _frames(native)
    parsed: list[dict[str, dict[str, Any]]] = []
    clocks = []
    frame_census: list[dict[str, Any]] = []
    out_of_window_absences = 0
    new_window_observations = 0
    for source in sources:
        rows, evidence = ownership_observation(
            source["raw"], source_sha256=source["source_sha256"], resolver=resolver
        )
        if evidence["held_observations"] or set(rows) != {TABLE}:
            raise BoundedError("HOLDER_FLOOR_INCOMPLETE_DATED_SOURCE_CLOSURE")
        clock = evidence["actual_acquisition"]
        if clock > observed:
            raise BoundedError("OWNERSHIP_FLOOR_SOURCE_TIME_OR_IDENTITY_UNQUALIFIED")
        clocks.append(clock)
        current = {key(r): r for r in rows[TABLES[0]]}
        if len(current) != len(rows[TABLE]) or (not parsed and set(current) != set(old_by_key)):
            raise BoundedError("HOLDER_ROLLING_EXACT_ORIGINAL_KEY_SET_CHANGED")
        frames = _frames(rows[TABLE])
        days = sorted(frames)
        if frame_census and days != frame_census[-1]["frame_dates"]:
            previous = frame_census[-1]["frame_dates"]
            # This version authorizes only one oldest-frame replacement, with
            # the three retained dated frames unchanged in membership. A hole,
            # backward roll or multi-frame jump requires a different contract.
            if days[:-1] != previous[1:] or days[-1] <= previous[-1]:
                raise BoundedError("HOLDER_ROLLING_FORWARD_ONE_FRAME_TRANSITION_REQUIRED")
        if parsed:
            previous_rows = parsed[-1]
            for day in set(frames) & set(frame_census[-1]["frame_dates"]):
                previous_keys = {
                    k
                    for k, r in previous_rows.items()
                    if _clock(r["observation_start"])
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .date()
                    .isoformat()
                    == day
                }
                current_keys = {
                    k
                    for k, r in current.items()
                    if _clock(r["observation_start"])
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .date()
                    .isoformat()
                    == day
                }
                if previous_keys != current_keys:
                    raise BoundedError("HOLDER_ROLLING_RETAINED_CAPTURE_FRAME_KEY_SET_CHANGED")
            if len(parsed) >= 2 and any(
                _body(current[k]) != _body(previous_rows[k])
                for k in set(current) & set(previous_rows)
            ):
                raise BoundedError("HOLDER_ROLLING_LATER_RETAINED_BODY_CONFLICT")
        for day in set(frames) & set(native_frames):
            original_keys = {
                k
                for k, r in old_by_key.items()
                if _clock(r["observation_start"])
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat()
                == day
            }
            current_keys = {
                k
                for k, r in current.items()
                if _clock(r["observation_start"])
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat()
                == day
            }
            if original_keys != current_keys:
                raise BoundedError("HOLDER_ROLLING_RETAINED_FRAME_KEY_SET_CHANGED")
        missing = set(old_by_key) - set(current)
        if any(
            _clock(old_by_key[k]["observation_start"])
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .date()
            .isoformat()
            >= days[0]
            for k in missing
        ):
            raise BoundedError("HOLDER_ROLLING_MISSING_KEY_INSIDE_PUBLISHED_WINDOW")
        extra = set(current) - set(old_by_key)
        if any(
            _clock(current[k]["observation_start"])
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .date()
            .isoformat()
            <= max(native_frames)
            for k in extra
        ):
            raise BoundedError("HOLDER_ROLLING_UNQUALIFIED_OLDER_EXTRA_KEY")
        out_of_window_absences += len(missing)
        new_window_observations += len(extra)
        frame_census.append(
            {
                "frame_dates": days,
                "typed_rows_sha256": digest(rows[TABLE]),
                "retained_keys": len(set(current) & set(old_by_key)),
                "older_out_of_window_keys": len(missing),
                "newer_frame_keys": len(extra),
            }
        )
        parsed.append(current)
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
        "source_frame_census": frame_census,
        "out_of_window_absences": out_of_window_absences,
        "new_window_observations": new_window_observations,
        "absence_semantics": "OLDER_THAN_COMPLETE_FORWARD_WINDOW_NOT_DELETION_OR_ZERO",
        "rule_sha256": digest(rule),
        "evaluator_sha256": file_sha(Path(__file__)),
        "projector_sha256": file_sha(Path(__file__).with_name("base22_holder_literals.py")),
    }
    input_identity = digest(closure)
    revisions = {}
    for ident, old in old_by_key.items():
        if any(ident not in p for p in parsed[:3]):
            raise BoundedError("HOLDER_ROLLING_TWO_CHANGED_CAPTURE_WITNESSES_REQUIRED")
        original = parsed[0][ident]
        after = [p[ident] for p in parsed[1:] if ident in p]
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
    if pf.metadata.num_rows != 80 or not pf.schema_arrow.equals(schema, check_metadata=False):
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
