"""Exact negative tail observations; retained controls are never rewritten."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import business_date, digest
from .base22_bounded import BoundedError
from .base22_outputs import instant, tail_observation
from .base22_quality import _exception

CONTRACT_ID = "p10-base22-tail-state-reconciliation-v1"
STATE_COLUMNS = (
    "listing_id",
    "calendar_session_id",
    "observation_start",
    "observation_end",
    "knowledge_from",
    "knowledge_to",
    "is_tradable",
    "can_buy",
    "can_sell",
    "reason_code",
)


def reconcile_tail_observation(
    raw: Mapping[str, Any],
    *,
    bar: Mapping[str, Any],
    listing: Mapping[str, Any],
    calendar: Mapping[str, Any],
    states: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    detected_at: datetime,
    source_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind genuine raw, typed observation and exact retained negative controls.

    The caller supplies all selected state/gap versions for the exact date, from
    frozen members. This function does not decide a supplier priority, repair
    tradability, resolve an old gap, or grant consumer admission. Numeric values
    participate only in sealed equality/hashes; the returned proof is safe.
    """
    if raw["stock_code"] != listing["listing_code"]:
        raise BoundedError("TAIL_RECONCILIATION_SOURCE_IDENTITY_MISMATCH")
    expected, raw_proof = tail_observation(
        raw,
        listing=listing,
        calendar=calendar,
        observed_at=bar["knowledge_from"],
        source_sha256=source_sha256,
        revision=bar["source_revision"],
    )
    if dict(bar) != expected:
        raise BoundedError("TAIL_RECONCILIATION_TYPED_RAW_MISMATCH")
    if not raw_proof["classification"].startswith("QUARANTINED"):
        raise BoundedError("TAIL_RECONCILIATION_NOT_QUARANTINED")
    detected = instant(detected_at)
    if detected < bar["knowledge_from"]:
        raise BoundedError("TAIL_RECONCILIATION_DETECTION_PRECEDES_OBSERVATION")
    if not states or not gaps:
        raise BoundedError("TAIL_RECONCILIATION_CONTROL_CLOSURE_MISSING")
    day = raw["trade_date"]
    state_proofs = []
    for state in states:
        if (
            state["listing_id"] != listing["listing_id"]
            or state["calendar_session_id"] != calendar["calendar_session_id"]
            or business_date(state["observation_start"]) != day
            or state["observation_end"] <= state["observation_start"]
            or instant(state["knowledge_from"]) > detected
        ):
            raise BoundedError("TAIL_RECONCILIATION_STATE_CLOSURE_MISMATCH")
        if any(type(state[field]) is not bool for field in ("is_tradable", "can_buy", "can_sell")):
            raise BoundedError("TAIL_RECONCILIATION_CONTROL_FLAG_INVALID")
        projection = {field: state[field] for field in STATE_COLUMNS}
        state_proofs.append({"control_projection": projection, "sha256": digest(state)})
    gap_proofs = []
    for gap in gaps:
        if (
            gap["listing_id"] != listing["listing_id"]
            or business_date(gap["affected_effective_at"]) != day
            or gap["interval_size"] != "1d"
            or instant(gap["knowledge_from"]) > detected
        ):
            raise BoundedError("TAIL_RECONCILIATION_GAP_CLOSURE_MISMATCH")
        gap_proofs.append({"gap_reason": gap["gap_reason"], "sha256": digest(gap)})
    disagreement = any(s["is_tradable"] or s["can_buy"] or s["can_sell"] for s in states)
    proof = {
        "contract_id": CONTRACT_ID,
        "stock_code": raw["stock_code"],
        "trade_date": day,
        "raw_row_sha256": digest(raw),
        "typed_bar_sha256": digest(bar),
        "listing_row_sha256": digest(listing),
        "calendar_row_sha256": digest(calendar),
        "calendar_is_open": calendar["is_open"],
        "raw_nontrading": raw["tradestatus"] != "1",
        "zero_volume": raw["volume"] == 0,
        "zero_turnover": raw["amount"] == 0,
        "retained_states": state_proofs,
        "retained_gaps": gap_proofs,
        "state_disagreement": disagreement,
        "provider_acquisition": None,
        "warehouse_observed_at": bar["knowledge_from"],
        "detected_at": detected,
        "state_mutations": 0,
        "gap_resolutions": 0,
        "historical_accepted_child": "NOT_ASSERTED",
        "consumer_admission": "NOT_GRANTED",
        "disposition": "PRESERVE_RAW_AND_OLD_CONTROLS; BLOCK_NEW_ENTRIES; NO_FORCED_SALE",
    }
    row = _exception(
        key={
            "listing_id": listing["listing_id"],
            "calendar_session_id": calendar["calendar_session_id"],
            "trade_date": day,
            "observed_bar_version": bar["knowledge_from"],
        },
        effective=day,
        detected_at=detected,
        rule="P10-472-RAW-STATE-DISAGREEMENT" if disagreement else "P10-472-NONTRADING-RAW",
        domain="tradability_state",
        affected_table="dwd_security_bar",
        source_sha256=source_sha256,
        evidence=proof,
    )
    return row, proof


def audit_tail_governance_scope(root: Path) -> dict[str, Any]:
    """Full local native reads and raw replay, with no source-path dereferencing."""
    import json

    import duckdb
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import canonical, file_sha, load_contract, schemas
    from .base22_bounded import semantic_scan
    from .base22_sources import _tree, physical_path, tail_raw_projection

    root = physical_path(root)
    body = json.loads((root / "SCOPE.json").read_bytes())
    if (
        body.get("kind") != "sealed_negative_quality_scope_NOT_COMPLETE_BASE"
        or body.get("contract_id") != CONTRACT_ID
        or digest({k: v for k, v in body.items() if k != "identity"}) != body.get("identity")
        or body.get("full_472_acceptance") is not False
        or body.get("old_control_mutations") != 0
    ):
        raise BoundedError("TAIL_GOVERNANCE_DESCRIPTOR_INVALID")
    _, model, extension = load_contract(root / "contract")
    typed_schemas = schemas(model, extension)
    paths: dict[str, list[Path]] = {}
    scans = {}
    physical = {"SCOPE.json", "PREWRITE.json", body["evidence_path"]}
    for item in [*body["members"], *body["outputs"]]:
        relative = item["path"]
        path = physical_path(root / relative)
        if root not in path.parents or relative in physical or path.stat().st_nlink != 1:
            raise BoundedError("TAIL_GOVERNANCE_PHYSICAL_MEMBER_INVALID")
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("TAIL_GOVERNANCE_MEMBER_HASH_MISMATCH")
        physical.add(relative)
        role = item.get("role", "dwd_quality_exception")
        paths.setdefault(role, []).append(path)
        table = "dwd_security_bar" if role == "new_bar" else role
        if table in typed_schemas:
            scan = semantic_scan([path], typed_schemas[table], batch_rows=256)
            if scan["rows"] != item["rows"] or (
                "semantic_scan" in item and item["semantic_scan"] != scan
            ):
                raise BoundedError("TAIL_GOVERNANCE_SCHEMA_CENSUS_MISMATCH")
            scans[relative] = scan
    if set(_tree(root)) != physical:
        raise BoundedError("TAIL_GOVERNANCE_PHYSICAL_SET_MISMATCH")
    if len(paths.get("raw_tail", [])) != 1:
        raise BoundedError("TAIL_GOVERNANCE_RAW_CLOSURE_INVALID")
    raw_item = next(item for item in body["members"] if item["role"] == "raw_tail")
    raw = tail_raw_projection(paths["raw_tail"][0], raw_item["sha256"], body["stock_code"])
    dates = body["exact_dates"]
    if len(dates) != len(set(dates)) or len(dates) != 11:
        raise BoundedError("TAIL_GOVERNANCE_EXACT_DATE_SCOPE_INVALID")
    selected = {
        str(row["trade_date"]): row for row in raw.to_pylist() if str(row["trade_date"]) in dates
    }
    if set(selected) != set(dates):
        raise BoundedError("TAIL_GOVERNANCE_RAW_KEY_CLOSURE_INVALID")
    connection = duckdb.connect(
        config={
            "threads": "1",
            "memory_limit": "2GB",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
        }
    )
    proofs = []
    expected_rows = []
    try:
        for table, files in paths.items():
            if table in typed_schemas or table == "new_bar":
                connection.read_parquet(
                    [str(p) for p in files], hive_partitioning=False
                ).create_view(table)
        listings = (
            connection.execute(
                "SELECT * FROM dwd_listing WHERE listing_code=?", [body["stock_code"]]
            )
            .to_arrow_table()
            .to_pylist()
        )
        if len(listings) != 1:
            raise BoundedError("TAIL_GOVERNANCE_LISTING_CLOSURE_INVALID")
        listing = listings[0]
        for day in dates:
            calendar = (
                connection.execute(
                    "SELECT * FROM dwd_trading_calendar WHERE venue_id=? AND trade_date=? "
                    "AND session_type='REGULAR_DAY'",
                    [listing["venue_id"], day],
                )
                .to_arrow_table()
                .to_pylist()
            )
            bars = (
                connection.execute(
                    "SELECT * FROM new_bar WHERE listing_id=? "
                    "AND CAST(timezone('Asia/Shanghai',observation_start) AS DATE)=?",
                    [listing["listing_id"], day],
                )
                .to_arrow_table()
                .to_pylist()
            )
            if len(calendar) != 1 or len(bars) != 1:
                raise BoundedError("TAIL_GOVERNANCE_DATE_REFERENCE_CLOSURE_INVALID")
            states = (
                connection.execute(
                    "SELECT * EXCLUDE(upper_price,lower_price) FROM dwd_tradability_state "
                    "WHERE listing_id=? "
                    "AND CAST(timezone('Asia/Shanghai',observation_start) AS DATE)=?",
                    [listing["listing_id"], day],
                )
                .to_arrow_table()
                .to_pylist()
            )
            gaps = (
                connection.execute(
                    "SELECT * FROM dwd_observation_gap WHERE listing_id=? AND interval_size='1d' "
                    "AND CAST(timezone('Asia/Shanghai',affected_effective_at) AS DATE)=?",
                    [listing["listing_id"], day],
                )
                .to_arrow_table()
                .to_pylist()
            )
            row, proof = reconcile_tail_observation(
                selected[day],
                bar=bars[0],
                listing=listing,
                calendar=calendar[0],
                states=states,
                gaps=gaps,
                detected_at=instant(body["detected_at"]),
                source_sha256=raw_item["sha256"],
            )
            expected_rows.append(digest(row))
            proofs.append(proof)
    finally:
        connection.close()
    actual_rows = [
        digest(row)
        for p in paths["dwd_quality_exception"]
        for batch in pq.ParquetFile(p).iter_batches(batch_size=256)
        for row in batch.to_pylist()
    ]
    evidence_path = root / body["evidence_path"]
    if file_sha(evidence_path) != body["evidence_sha256"]:
        raise BoundedError("TAIL_GOVERNANCE_EVIDENCE_HASH_MISMATCH")
    evidence = json.loads(evidence_path.read_bytes())
    if (
        sorted(actual_rows) != sorted(expected_rows)
        or len(set(actual_rows)) != 11
        or canonical(evidence["proofs"]) != canonical(proofs)
    ):
        raise BoundedError("TAIL_GOVERNANCE_RAW_REPLAY_OR_OUTPUT_MISMATCH")
    return {
        "status": "LOCAL_NATIVE_SCAN_AND_RAW_REPLAY_PASS_NOT_BASE22_ACCEPTANCE",
        "scope_identity": body["identity"],
        "quality_rows": len(actual_rows),
        "raw_state_disagreements": sum(p["state_disagreement"] for p in proofs),
        "consistent_nontrading_controls": sum(not p["state_disagreement"] for p in proofs),
        "all_local_typed_scans": scans,
        "state_mutations": 0,
        "gap_resolutions": 0,
        "provider_acquisition": None,
        "source_paths_dereferenced": 0,
    }
