"""Qualify retained weekly ST captures for exact native reuse, never PIT admission.

The original dated interval and notice audit must be present locally. A repeated
window is evidence about that interval, not a new interval or an inferred change.
Unknown baselines remain unknown. The provider's reported capture clock remains
an observation claim; it cannot recover an absent original wire acquisition.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_outputs import instant
from .base22_sources import _tree

CONTRACT_ID = "p10-base22-weekly-st-exact-native-reuse-v1"
BODY_FIELDS = (
    "interval_id",
    "stock_code",
    "exchange",
    "status_label",
    "is_st",
    "effective_start",
    "effective_end",
    "announcement_date",
    "knowledge_cutoff",
    "source",
    "source_url_or_notice_id",
    "evidence_path",
    "event_kind",
    "confidence",
    "notes",
)
MIC = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE"}
TABLE = "dwd_security_status_interval"
TABLES = {TABLE, "dwd_listing", "dwd_security", "dwd_listed_company", "dwd_trading_venue"}
ORIGINAL_SUFFIXES = {
    "META.json", "events_audit.parquet", "evidence/cninfo_st_events.parquet",
    "gold_samples.json", "ref_st_status_pit.parquet", "unknown_by_year.json",
}


def _day(value: Any, *, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise BoundedError("ST_REUSE_DATE_UNKNOWN_OR_INVALID")


def _body(row: dict[str, Any]) -> dict[str, Any]:
    if not set(BODY_FIELDS) <= set(row):
        raise BoundedError("ST_REUSE_ORIGINAL_BODY_INCOMPLETE")
    result = {k: row[k] for k in BODY_FIELDS}
    for k in ("effective_start", "effective_end", "announcement_date", "knowledge_cutoff"):
        result[k] = _day(result[k], nullable=k in {"effective_end", "announcement_date"})
    flag = result["is_st"]
    if type(flag) is int and flag in (0, 1):
        result["is_st"] = str(flag)
    elif not isinstance(flag, str) or flag not in ("0", "1"):
        raise BoundedError("ST_REUSE_FLAG_UNKNOWN")
    if (
        result["exchange"] not in MIC
        or result["confidence"] not in {"proven", "not_proven"}
        or result["status_label"] not in {"normal", "ST", "delisting_risk", "unknown"}
        or result["effective_start"] > THROUGH
        or (result["announcement_date"] is not None and result["announcement_date"] > THROUGH)
    ):
        raise BoundedError("ST_REUSE_BODY_SCOPE_INVALID")
    if result["confidence"] == "not_proven" and (
        result["status_label"] != "unknown"
        or result["event_kind"] != "no_proven_event"
        or result["source_url_or_notice_id"] is not None
    ):
        raise BoundedError("ST_REUSE_UNKNOWN_BASELINE_MUST_REMAIN_UNKNOWN")
    return result


def qualify_capture(
    capture: dict[str, Any],
    original: dict[str, Any],
    native: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    listing: dict[str, Any],
    venue: dict[str, Any],
    original_source_sha256: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Prove the selected source body/notice/dated identity already exists unchanged.

    All original clocks and native atoms are hashed, not rewritten. Full source
    files/manifests, the metadata's original gate and the entire native partition
    are independently checked by the caller. This receipt grants neither whole
    August coverage nor qualification of unselected intervals/provider states.
    """
    body, captured = _body(original), _body(capture)
    if body != captured:
        raise BoundedError("ST_REUSE_CAPTURE_ORIGINAL_BODY_CHANGED")
    clock = instant(observed_at)
    provider_claim = capture.get("provider_acquired_at")
    if instant(provider_claim) > clock:
        raise BoundedError("ST_REUSE_REPORTED_CAPTURE_AFTER_OBSERVATION")
    if not capture.get("source_revision") or capture.get("provider_dataset") != "st-status":
        raise BoundedError("ST_REUSE_CAPTURE_REVISION_OR_DATASET_UNKNOWN")
    start = datetime.combine(body["effective_start"], time(), ZoneInfo("Asia/Shanghai"))
    end = (
        datetime.combine(
            body["effective_end"] + timedelta(days=1), time(), ZoneInfo("Asia/Shanghai")
        )
        if body["effective_end"] is not None
        else None
    )
    if (
        listing["listing_code"] != body["stock_code"]
        or listing["venue_id"] != venue["venue_id"]
        or venue["mic_code"] != MIC[body["exchange"]]
        or listing["security_id"] != native["security_id"]
        or not (
            listing["effective_from"] <= start
            and (listing["effective_to"] is None or start < listing["effective_to"])
        )
    ):
        raise BoundedError("ST_REUSE_EXACT_DATED_IDENTITY_VENUE_MISMATCH")
    for parent in (listing, venue):
        if not (
            parent["knowledge_from"] <= native["knowledge_from"]
            and (
                parent["knowledge_to"] is None
                or native["knowledge_from"] < parent["knowledge_to"]
            )
        ):
            raise BoundedError("ST_REUSE_PARENT_KNOWLEDGE_MISMATCH")
    expected = {
        "record_source": "st_status_pit",
        "source_revision": original["source_revision"],
        "source_artifact_sha256": original_source_sha256,
        "status_type": "SPECIAL_TREATMENT",
        "status_value": "UNKNOWN"
        if body["status_label"] == "unknown"
        else body["status_label"].upper()
        if body["is_st"] == "1"
        else "NORMAL",
        "reason_code": "BASELINE_NOT_PROVEN"
        if body["confidence"] == "not_proven"
        else body["event_kind"],
        "effective_from": start.astimezone(timezone.utc),
        "effective_to": end.astimezone(timezone.utc) if end is not None else None,
    }
    if (
        any(native.get(k) != v for k, v in expected.items())
        or not native["source_record_id"].endswith("#interval_id=" + body["interval_id"])
        or native["knowledge_from"] > clock
    ):
        raise BoundedError("ST_REUSE_NATIVE_ATOMS_OR_LINEAGE_DIFFER")
    matched = []
    if body["confidence"] == "proven":
        matched = [
            event
            for event in events
            if event["stock_code"] == body["stock_code"]
            and event["exchange"] == body["exchange"]
            and event["event_kind"] == body["event_kind"]
            and _day(event["announcement_date"]) == body["announcement_date"]
            and _day(event["effective_start_parsed"]) == body["effective_start"]
            and body["source_url_or_notice_id"] in {str(event["ann_id"]), event["url"]}
        ]
        if len(matched) != 1:
            raise BoundedError("ST_REUSE_ORIGINAL_NOTICE_NOT_UNIQUE_OR_MISSING")
    proof = {
        "contract_id": CONTRACT_ID,
        "disposition": "EXACT_NO_CHANGE_NATIVE_ENTITY_REUSE",
        "original_row_sha256": digest(original),
        "capture_row_sha256": digest(capture),
        "original_body_sha256": digest(body),
        "native_row_sha256": digest(native),
        "listing_row_sha256": digest(listing),
        "venue_row_sha256": digest(venue),
        "notice_row_sha256": digest(matched[0]) if matched else None,
        "native_status_unknown": body["confidence"] == "not_proven",
        "reported_provider_capture_clock": provider_claim,
        "original_wire_provider_acquisition": None,
        "warehouse_verified_at": clock.isoformat(),
        "native_rows_mutated": 0,
        "new_intervals_or_backfilled_PIT_times": 0,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "whole_August_or_Base22_acceptance": False,
    }
    proof["identity"] = digest(proof)
    return proof


def audit_scope(
    root: Path,
    body: dict[str, Any],
    budget: Budget,
    *,
    descriptor_name: str = "QUALIFICATION.json",
) -> dict[str, Any]:
    """Image-local full copy/global/notice replay; archival paths are never opened."""
    import json

    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "P10 exact retained weekly capture/no-change native evidence"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(k) is not False
            for k in ("READY", "complete_Base22", "historical_PIT_admission")
        )
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or set(body.get("outputs", {})) != TABLES
    ):
        raise BoundedError("ST_REUSE_OFFLINE_SCOPE_INVALID")
    if Path(descriptor_name).name != descriptor_name or not descriptor_name:
        raise BoundedError("ST_REUSE_DESCRIPTOR_NAME_INVALID")
    if _tree(root) != set(body["physical_members"]) | {descriptor_name}:
        raise BoundedError("ST_REUSE_PHYSICAL_FILE_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        path = _physical(root, rel, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("ST_REUSE_PHYSICAL_HASH_CHANGED")

    def document(rel: str) -> dict[str, Any]:
        return dict(json.loads(_physical(root, rel, independent=True).read_bytes()))

    def rows(rel: str, maximum: int) -> list[dict[str, Any]]:
        file = pq.ParquetFile(_physical(root, rel, independent=True))
        if file.metadata.num_rows > maximum:
            raise BoundedError("ST_REUSE_LOCAL_SCOPE_ROW_BOUND")
        budget.check()
        return list(file.read().to_pylist())

    c, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    native_manifest = document(body["original_native_manifest"])
    ods_manifest = document(body["original_ods_manifest"])
    prefix = "payload/source-evidence/st_status_pit_v1_1_20260725/"
    expected_originals = {prefix + name for name in ORIGINAL_SUFFIXES}
    if (
        len(body["original_source_members"]) != len(expected_originals)
        or {i["original_path"] for i in body["original_source_members"]} != expected_originals
        or len(body["weekly_sources"]) != 4
        or len({i["label"] for i in body["weekly_sources"]}) != 4
    ):
        raise BoundedError("ST_REUSE_EXACT_ORIGINAL_CLOSURE_OR_SELECTED_RELEASE_SET_CHANGED")
    for item in body["original_source_members"]:
        declarations = [
            i for i in ods_manifest["content"]["files"] if i["path"] == item["original_path"]
        ]
        if len(declarations) != 1 or declarations[0]["sha256"] != file_sha(root / item["path"]):
            raise BoundedError("ST_REUSE_ORIGINAL_ODS_MEMBER_IDENTITY_CHANGED")
    scans, parents = {}, {}
    for table, item in body["outputs"].items():
        original_entries = [i for i in native_manifest["files"] if i["path"] == item["native_path"]]
        path = _physical(root, item["path"], independent=True)
        if len(original_entries) != 1 or original_entries[0]["sha256"] != file_sha(path):
            raise BoundedError("ST_REUSE_NATIVE_ORIGINAL_BYTES_DIFFER")
        scans[table] = semantic_scan([path], ss[table], batch_rows=1024, check=budget.check)
        if scans[table] != item["semantic_scan"]:
            raise BoundedError("ST_REUSE_NATIVE_TYPED_SUMMARY_CHANGED")
        parents[table] = rows(item["path"], 65536)
    original_rows = rows(body["original_intervals"], 8192)
    original_by_id = {r["interval_id"]: r for r in original_rows}
    native_rows = [r for r in parents[TABLE] if r["record_source"] == "st_status_pit"]
    native_by_id = {r["source_record_id"].split("#interval_id=")[-1]: r for r in native_rows}
    if len(original_by_id) != len(original_rows) or len(native_by_id) != len(native_rows):
        raise BoundedError("ST_REUSE_ORIGINAL_OR_NATIVE_INTERVAL_NOT_UNIQUE")
    events = rows(body["original_events"], 4096)
    meta = document(body["original_meta"])
    if (
        meta.get("rows") != len(original_rows)
        or meta.get("events_used") != len(events)
        or meta.get("validate_ok") is not True
        or "sha256:" + meta.get("sha256", "") != file_sha(root / body["original_intervals"])
        or meta.get("gate") != body["preserved_original_gate"]
    ):
        raise BoundedError("ST_REUSE_ORIGINAL_METADATA_OR_GATE_CHANGED")
    venues = {r["venue_id"]: r for r in parents["dwd_trading_venue"]}
    proofs = []
    for item in body["weekly_sources"]:
        manifest = document(item["manifest"])
        sha = file_sha(root / item["manifest"])
        if manifest.get("status") != "ready" or (root / item["READY"]).read_text().strip() != sha:
            raise BoundedError("ST_REUSE_LOCAL_READY_MISMATCH")
        members = [a for a in manifest["artifacts"] if a["name"] == "st_announcement_trail"]
        if len(members) != 1 or members[0]["sha256"] != file_sha(root / item["raw"]):
            raise BoundedError("ST_REUSE_WEEKLY_DECLARED_MEMBER_CHANGED")
        captures = rows(item["raw"], 512)
        if (
            len(captures) != item["rows"]
            or len({r["interval_id"] for r in captures}) != len(captures)
        ):
            raise BoundedError("ST_REUSE_CAPTURE_KEY_CENSUS_CHANGED")
        for capture in captures:
            key = capture["interval_id"]
            if key not in original_by_id or key not in native_by_id:
                raise BoundedError("ST_REUSE_CAPTURE_NOT_IN_ORIGINAL_NATIVE")
            original, native = original_by_id[key], native_by_id[key]
            start_day = _day(original["effective_start"])
            if start_day is None:
                raise BoundedError("ST_REUSE_START_DATE_REQUIRED")
            start = datetime.combine(start_day, time(), ZoneInfo("Asia/Shanghai"))
            listings = [
                listing for listing in parents["dwd_listing"]
                if listing["listing_code"] == capture["stock_code"]
                and listing["security_id"] == native["security_id"]
                and venues[listing["venue_id"]]["mic_code"] == MIC[capture["exchange"]]
                and listing["effective_from"] <= start
                and (listing["effective_to"] is None or start < listing["effective_to"])
                and listing["knowledge_from"] <= native["knowledge_from"]
                and (
                    listing["knowledge_to"] is None
                    or native["knowledge_from"] < listing["knowledge_to"]
                )
            ]
            if len(listings) != 1:
                raise BoundedError("ST_REUSE_LOCAL_DATED_LISTING_NOT_UNIQUE")
            proof = qualify_capture(
                capture, original, native, events=events, listing=listings[0],
                venue=venues[listings[0]["venue_id"]],
                original_source_sha256=file_sha(root / body["original_intervals"]),
                observed_at=instant(body["observed_at"]),
            )
            proofs.append({"release_id": manifest["release_id"], "proof": proof})
    if digest(proofs) != body["capture_proofs_identity"]:
        raise BoundedError("ST_REUSE_EXACT_CAPTURE_PROOFS_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in sorted(TABLES):
            scan.add(table, [root / body["outputs"][table]["path"]])
            rule = c["tables"][table]
            scan.keys(table, rule["business_key"], rule["version_key"])
            scan.row_rules(table, ss[table], rule)
            scan.lineage(table, [tuple(x) for x in body["source_registries"][table]])
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in TABLES]},
            {"tables": {t: c["tables"][t] for t in TABLES}},
        )
    result = {
        "status": "SCOPED_EXACT_ST_CAPTURE_NATIVE_REUSE_PASS_NOT_WHOLE_AUGUST_OR_BASE22",
        "scope_identity": body["identity"],
        "rows": {t: s["rows"] for t, s in scans.items()},
        "native_typed_summaries": scans,
        "capture_proofs_identity": digest(proofs),
        "captures": len(proofs),
        "unique_intervals": len({p["proof"]["original_body_sha256"] for p in proofs}),
        "unknown_capture_observations": sum(p["proof"]["native_status_unknown"] for p in proofs),
        "preserved_original_gate": meta["gate"],
        "provider_states_outside_selected_captures_qualified": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
        "native_source_bytes_unchanged": True,
    }
    result["identity"] = digest(result)
    return result
