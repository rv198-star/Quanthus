"""Freeze P6 historical windows and replay their input-admission checks.

This is the pre-result stage of historical replay, separate from prospective
activation. It never turns an unavailable source purpose or a late timestamp
into permission to evaluate a strategy. A zero-admission run is BLOCKED.
"""

from __future__ import annotations

import importlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    _fingerprint,
    _member,
    _tree,
    disjoint_output,
    physical_path,
)

SCHEMA = "quanthus.p6-historical-replay-plan.v1"
ADMISSION_SCHEMA = "quanthus.p6-historical-input-admission.v1"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        fail("p6_history_object", path.name)
    return value


def _range(sessions: Sequence[str], spec: Mapping[str, Any]) -> list[str]:
    start, end = date.fromisoformat(spec["start"]), date.fromisoformat(spec["end"])
    selected = [d for d in sessions if str(start) <= d <= str(end)]
    if (
        type(spec["sessions"]) is not int
        or spec["sessions"] < 1
        or len(selected) != spec["sessions"]
        or selected[0] != str(start)
        or selected[-1] != str(end)
    ):
        fail("p6_history_window", str(spec))
    return selected


def bind_calendar(plan: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fix actual sessions before outcome access; never infer weekdays/holidays."""
    if plan["schema_version"] != SCHEMA or plan["mode"] != "historical_replay":
        fail("p6_history_plan", "explicit historical mode required")
    if plan["timezone"] != "Asia/Shanghai":
        fail("p6_history_plan", "Shanghai calendar required")
    scope = plan["scope"]
    if scope["venues"] != ["SSE", "SZSE"] or not (
        "2026-01-01" <= scope["start"] <= scope["end"] <= "2026-12-31"
    ):
        fail("p6_history_partition", "only the separately authorized 2026 simulation scope")
    expected_semantics = {
        "signal_profile": "close-to-next-open-v1",
        "feature_cutoff": "bound_session_close",
        "slice_ledger_reset": False,
        "slice_independence_claim": False,
        "warmup_counts_as_observation": False,
        "future_data_required": False,
        "unknown_knowledge_time": "block_affected_entry_preserve_unknown",
        "zero_admitted_days": "blocked_not_successful_replay",
        "historical_availability_is_source_qualification": True,
    }
    if plan["semantics"] != expected_semantics or plan["authority"] != dict.fromkeys(
        (
            "protected_partitions_authorized",
            "prospective_budget_write",
            "sealed_ledger_write",
            "notification",
            "broker",
            "live_capital",
        ),
        False,
    ):
        fail("p6_history_authority", "historical successor cannot inherit prospective authority")
    calendars: dict[str, list[str]] = {v: [] for v in scope["venues"]}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        day, venue = str(r["trade_date"]), str(r["exchange"])
        if venue not in calendars or not scope["start"] <= day <= scope["end"]:
            continue
        if (venue, day) in seen:
            fail("p6_history_calendar", "duplicate venue/date")
        seen.add((venue, day))
        if r["is_open"] == 1:
            close = r["close_at"]
            from zoneinfo import ZoneInfo

            if (
                not isinstance(close, datetime)
                or close.tzinfo is None
                or str(close.astimezone(ZoneInfo("Asia/Shanghai")).date()) != day
                or close.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S") != "15:00:00"
            ):
                fail("p6_history_calendar", "daily closing instant differs")
            calendars[venue].append(day)
    a, b = (sorted(calendars[v]) for v in scope["venues"])
    if a != b or not a or digest_value(a) != plan["source"]["open_sessions_sha256"]:
        fail("p6_history_calendar", "exact two-venue session identity differs")
    groups = {n: _range(a, plan[n]) for n in ("warmup", "decisions", "settlement_tail", "smoke")}
    if a != groups["warmup"] + groups["decisions"] + groups["settlement_tail"]:
        fail("p6_history_window", "warmup/decision/tail must be contiguous and disjoint")
    if len(groups["warmup"]) < 120 or len(groups["settlement_tail"]) != 20:
        fail("p6_history_window", "preserve listing history and target horizon")
    slices = plan["report_slices"]
    if len(slices) != 3 or [s["id"] for s in slices] != ["S1", "S2", "S3"]:
        fail("p6_history_window", "three predetermined reporting slices required")
    selected = [_range(a, s) for s in slices]
    if [day for days in selected for day in days] != groups["decisions"]:
        fail("p6_history_window", "report slices must partition decisions exactly")
    if groups["smoke"] != groups["decisions"][: len(groups["smoke"])]:
        fail("p6_history_window", "smoke must start at the first fixed decision")
    return {
        **groups,
        "all_sessions": a,
        "report_slices": dict(zip(("S1", "S2", "S3"), selected, strict=True)),
    }


def verify_source(root: Path, plan: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """One physical verification of the existing P10 handoff; no source repair.

    Its retained negative purpose assessment is evidence, never a permission
    bit to flip. This does not claim a new full P10 semantic qualification.
    """
    root = physical_path(root)
    before = {rel: _fingerprint(root / rel) for rel in _tree(root)}
    m = _read(root / "manifest.json")
    sha = file_sha(root / "manifest.json")
    if (
        sha != plan["source"]["manifest_sha256"]
        or (root / "READY").read_text().strip() != sha
        or m["schema_version"] != plan["source"]["schema_version"]
        or set(before) != set(m["files"]) | {"manifest.json", "READY"}
    ):
        fail("p6_history_source", "manifest/READY/physical inventory differs")
    for rel, member in m["files"].items():
        path = _member(root, rel)
        if path.stat().st_size != member["bytes"] or file_sha(path) != member["sha256"]:
            fail("p6_history_source", "member bytes/hash differ: " + rel)
    calendar = plan["source"]["calendar_member"]
    if m["files"][calendar]["sha256"] != plan["source"]["calendar_sha256"]:
        fail("p6_history_source", "calendar binding differs")
    check_unchanged(root, before)
    return m, before


def check_unchanged(root: Path, before: Mapping[str, Any]) -> None:
    if _tree(root) != set(before) or any(
        _fingerprint(root / rel) != st for rel, st in before.items()
    ):
        fail("p6_history_source_changed", "input namespace or bytes changed")


def run_admission(
    *,
    plan_path: Path,
    source: Path,
    output: Path,
    code_revision: str,
    repository_root: Path,
) -> dict[str, Any]:
    """Execute the fixed smoke and full-window input checks, before strategy reads.

    Only metadata, identity and clocks are queried. No OHLCV value or economic
    result is read by the query, and no official DB/controller is used.
    """
    import duckdb

    pq = importlib.import_module("pyarrow.parquet")

    from quanthus.forward_validation.base24_cycle import load_runtime

    started = time.monotonic()
    plan_path, source = physical_path(plan_path), physical_path(source)
    output = disjoint_output(output, [source, plan_path.parent, repository_root])
    if output.exists():
        fail("p6_history_output_exists", "preserve sealed and failed attempts")
    plan_fingerprint = _fingerprint(plan_path)
    plan = _read(plan_path)
    plan_sha = file_sha(plan_path)
    if _fingerprint(plan_path) != plan_fingerprint:
        fail("p6_history_plan_changed", "during plan binding")
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        fail("p6_history_code_revision", "full Git revision required")
    policy_path = _member(physical_path(repository_root), plan["policy_reference"]["path"])
    if file_sha(policy_path) != plan["policy_reference"]["sha256"]:
        fail("p6_history_policy", "frozen policy reference differs")
    runtime = load_runtime(policy_path, root=repository_root)
    policy_closure = {name: file_sha(path) for name, path in runtime.bindings.items()}
    code_and_policy = {
        path: _fingerprint(path)
        for path in [Path(__file__), policy_path, *runtime.bindings.values()]
    }
    manifest, before = verify_source(source, plan)
    calendar_path = source / plan["source"]["calendar_member"]
    calendar = pq.read_table(
        calendar_path, columns=["trade_date", "exchange", "is_open", "close_at"]
    ).to_pylist()
    windows = bind_calendar(plan, calendar)
    output.mkdir(parents=True)
    (output / "plan.json").write_bytes(canonical_json_bytes(plan) + b"\n")

    def q(p: Any) -> str:
        return "'" + str(p).replace("'", "''") + "'"

    # One shared temporary connection, not fifty copied historical windows.
    with TemporaryDirectory(prefix="p6-history-", dir=output) as temp:
        c = duckdb.connect()
        try:
            c.execute("SET threads=2")
            c.execute("SET memory_limit='2GB'")
            c.execute("SET temp_directory=" + q(temp))
            bars = q(source / "source/tables/daily_ohlcv_none.parquet")
            cal = q(calendar_path)
            start, end = plan["decisions"]["start"], plan["settlement_tail"]["end"]
            c.execute(f"""CREATE TEMP TABLE admission AS SELECT
                b.date,b.dwd_listing_id,b.identity_exchange,
                b.dwd_bar_knowledge_from,b.dwd_bar_knowledge_to,
                b.dwd_security_knowledge_from,b.dwd_listing_knowledge_from,
                b.dwd_state_knowledge_from,k.close_at,
                b.dwd_bar_knowledge_from IS NOT NULL
                AND b.dwd_bar_knowledge_from<=k.close_at
                AND (b.dwd_bar_knowledge_to IS NULL OR b.dwd_bar_knowledge_to>k.close_at)
                  AS bar_at_close,
                b.dwd_security_knowledge_from IS NOT NULL
                AND b.dwd_security_knowledge_from<=k.close_at
                AND b.dwd_listing_knowledge_from IS NOT NULL
                AND b.dwd_listing_knowledge_from<=k.close_at
                AND b.dwd_state_knowledge_from IS NOT NULL
                AND b.dwd_state_knowledge_from<=k.close_at AS identity_state_at_close
              FROM read_parquet({bars}) b LEFT JOIN read_parquet({cal}) k
                ON k.exchange=b.identity_exchange AND k.trade_date=b.date AND k.is_open=1
              WHERE b.date BETWEEN DATE '{start}' AND DATE '{end}'""")
            duplicates = c.execute("""SELECT count(*) FROM (SELECT date,dwd_listing_id
                  FROM admission GROUP BY 1,2 HAVING count(*)<>1)""").fetchone()
            assert duplicates is not None
            if duplicates[0]:
                fail("p6_history_duplicate", "listing/session is not unique")
            missing = c.execute("SELECT count(*) FROM admission WHERE close_at IS NULL").fetchone()
            assert missing is not None
            if missing[0]:
                fail("p6_history_calendar", "bar date/venue has no open session")
            raw = c.execute("""SELECT date,count(*),
                count(*) FILTER(WHERE bar_at_close),
                count(*) FILTER(WHERE bar_at_close AND identity_state_at_close),
                count(*) FILTER(WHERE dwd_bar_knowledge_from IS NULL),
                min(dwd_bar_knowledge_from),max(dwd_bar_knowledge_from)
                FROM admission GROUP BY date ORDER BY date""").fetchall()
        finally:
            c.close()
    by_day = {
        str(r[0]): {
            "market_date": str(r[0]),
            "rows": r[1],
            "bar_visible_at_close": r[2],
            "necessary_bar_identity_state_visible_at_close": r[3],
            "bar_knowledge_unknown": r[4],
            "earliest_bar_knowledge_from": None if r[5] is None else r[5].isoformat(),
            "latest_bar_knowledge_from": None if r[6] is None else r[6].isoformat(),
        }
        for r in raw
    }
    required = windows["decisions"] + windows["settlement_tail"]
    if set(by_day) != set(required):
        fail("p6_history_missing_session", "fixed decision/tail session has no market observations")
    assessment = _read(source / "assessment.json")
    source_purpose_granted = assessment.get("historical_pit_granted") is True
    necessary_days = [
        d for d in required if by_day[d]["necessary_bar_identity_state_visible_at_close"]
    ]
    # Passing necessary clocks is not sufficient: 61/22 windows, execution,
    # actions and the remaining source families still need qualification.
    reasons = []
    if not source_purpose_granted:
        reasons.append("P10_HISTORICAL_REPLAY_PURPOSE_NOT_GRANTED")
    if not necessary_days:
        reasons.append("NO_SESSION_WITH_NECESSARY_HISTORICAL_INPUT_CLOCKS")
    result = {
        "schema_version": ADMISSION_SCHEMA,
        "mode": "historical_input_admission",
        "status": "BLOCKED",
        "strategy_replay_executed": False,
        "reason_codes": reasons or ["FULL_HISTORICAL_CONSUMER_QUALIFICATION_REQUIRED"],
        "plan_sha256": plan_sha,
        "code_revision": code_revision,
        "policy_closure_sha256": policy_closure,
        "producer_sha256": file_sha(Path(__file__)),
        "source_manifest_sha256": plan["source"]["manifest_sha256"],
        "source_bytes_hashed": sum(v["bytes"] for v in manifest["files"].values()),
        "source_file_count": len(before),
        "source_hash_passes": 1,
        "source_original_clocks_preserved": True,
        "source_unchanged": True,
        "historical_purpose_granted": source_purpose_granted,
        "source_qualification_recomputed": False,
        "windows": windows,
        "smoke": [by_day[d] for d in windows["smoke"]],
        "daily_admission": [by_day[d] for d in required],
        "days_with_necessary_historical_clocks": len(necessary_days),
        "official_observations_created": 0,
        "strategy_decisions_created": 0,
        "paper_orders_created": 0,
        "economic_results_read": 0,
        "prospective_database_used": False,
        "existing_prospective_activation_required": False,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": format(time.monotonic() - started, ".3f"),
        "claim_ceiling": "fixed historical input admission only; not completed P6 replay",
    }
    check_unchanged(source, before)
    if _fingerprint(plan_path) != plan_fingerprint or any(
        _fingerprint(path) != st for path, st in code_and_policy.items()
    ):
        fail("p6_history_plan_changed", "input plan, code or frozen policy changed during read")
    (output / "result.json").write_bytes(canonical_json_bytes(result) + b"\n")
    # This marker certifies only that the negative assessment completed.
    (output / "ASSESSMENT_COMPLETE").write_text(file_sha(output / "result.json") + "\n")
    return result
