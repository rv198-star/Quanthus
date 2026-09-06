"""Fixed-input real-DWD B2-B5 research runner; one cold provider construction per Job."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import time
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.backtest.data_provider import ReplayDataProvider, load_replay_data_provider_config
from quanthus.backtest.policy import load_execution_policy
from quanthus.policy import find_project_root
from quanthus.structure_v2 import StructureV2Result, build_structure_v2
from quanthus.structure_v2_artifact import FILES, MANIFEST_SCHEMA, verify_structure_v2_artifact
from quanthus.structure_v2_consumer import consume, p2_probes
from quanthus.structure_v2_inputs import (
    Admission,
    DailyInput,
    InputPolicy,
    admit_daily,
    build_readiness,
    calculation_spec,
    canonical,
    digest,
)
from quanthus.structure_v2_review import report_counts, select_review_sample

REQUEST_PATH = "config/structure-facts-v2-run.development.v1.json"
SCHEMA = "quanthus.structure-facts-v2-run.v1"


def load_request(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    keys = {"schema_version", "provider_config", "execution_policy", "scan_start", "report_start",
            "report_end", "security_codes", "lookbacks", "knowledge_view", "coordinate_policy",
            "consumer_mapping", "maximum_p2_probes", "p2_probe_quantity", "maximum_wall_seconds",
            "cold_result_job_budget", "review_sample_limit", "research_only",
            "execution_authorized", "economic_claim"}
    if not isinstance(raw, dict) or set(raw) != keys or raw["schema_version"] != SCHEMA:
        raise ValueError("v2 fixed request schema differs")
    fixed = {
        "provider_config": "config/replay-data-provider.development.v2.json",
        "execution_policy": "config/p2-execution-policy.research.v1.json",
        "lookbacks": [20, 60], "knowledge_view": "retrospective",
        "coordinate_policy": "raw-cny-action-epoch-reset-v1",
        "consumer_mapping": "one-qualified-target-per-invocation-clamped-signed-distance-v1",
        "maximum_p2_probes": 5, "p2_probe_quantity": 100, "maximum_wall_seconds": 1200,
        "cold_result_job_budget": 2, "review_sample_limit": 12,
        "research_only": True, "execution_authorized": False, "economic_claim": False,
    }
    if any(raw[key] != value or type(raw[key]) is not type(value) for key, value in fixed.items()):
        raise ValueError("v2 frozen input policy differs")
    codes = raw["security_codes"]
    if not isinstance(codes, list) or not codes or len(codes) > 3 or len(set(codes)) != len(codes):
        raise ValueError("v2 bounded securities invalid")
    if any(not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) for code in codes):
        raise ValueError("v2 security syntax invalid")
    start, first, last = (date.fromisoformat(raw[key]) for key in
                          ("scan_start", "report_start", "report_end"))
    if not start <= first <= last or (last - start).days > 365:
        raise ValueError("v2 bounded date scope invalid")
    return raw


def _stable_provider(identity: dict[str, Any]) -> dict[str, Any]:
    keys = ("provider_id", "provider_version", "provider_config_digest", "mode", "scope",
            "dwd_authority", "dwd_correction", "research_references", "materializer_version",
            "history_start", "materialized_rows", "materialized_content_digest",
            "provider_lineage_digest", "point_in_time")
    return {key: identity[key] for key in keys if key in identity}


def _source_projection(session: Any, request: dict) -> tuple[dict, dict, dict]:
    import duckdb

    root = session.reader.bundle.root / "tables"
    codes = request["security_codes"]
    placeholders = ",".join("?" for _ in codes)
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute(
            f"SELECT * FROM read_parquet(?) WHERE security_id IN ({placeholders}) "
            "AND date BETWEEN ? AND ? ORDER BY date, security_id",
            [str(root / "daily_ohlcv_none.parquet"), *codes,
             request["scan_start"], request["report_end"]],
        )
        columns = [item[0] for item in cursor.description]
        records = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        members = {(str(row["security_id"]), row["date"]): row for row in records}
        if len(members) != len(records):
            raise ValueError("DWD source projection has ambiguous daily listing grain")
        cursor = connection.execute(
            f"SELECT * FROM read_parquet(?) WHERE security_code IN ({placeholders}) "
            "AND effective_date <= ? ORDER BY effective_date, security_code, action_id",
            [str(root / "corporate_actions_normalized.parquet"), *codes, request["report_end"]],
        )
        columns = [item[0] for item in cursor.description]
        actions: dict[str, list[dict]] = {code: [] for code in codes}
        for values in cursor.fetchall():
            item = dict(zip(columns, values, strict=True))
            actions[str(item["security_code"])].append(item)
        cursor = connection.execute(
            "SELECT * FROM read_parquet(?) WHERE is_open=1 "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date, exchange",
            [str(root / "trading_calendar.parquet"), request["scan_start"], request["report_end"]],
        )
        columns = [item[0] for item in cursor.description]
        calendar: dict[date, list[dict]] = {}
        for values in cursor.fetchall():
            item = dict(zip(columns, values, strict=True))
            calendar.setdefault(item["trade_date"], []).append(item)
        return members, actions, calendar
    finally:
        connection.close()


def extract_admissions(
    session: Any, request: dict,
) -> tuple[tuple[Admission, ...], dict, InputPolicy]:
    """Read verified source members once; enumerate the independent calendar denominator."""
    members, actions, calendar = _source_projection(session, request)
    calendar_ref = "reference.calendar.trading-session:SSE-SZSE-daily-v1"
    policy = InputPolicy(digest(calculation_spec(request)),
                         tuple(request["security_codes"]), calendar_ref)
    frames = {frame.snapshot.market_date: frame for frame in session.reader.frames(
        date.fromisoformat(request["scan_start"]), date.fromisoformat(request["report_end"]),
    )}
    if set(frames) != set(calendar) or not calendar:
        raise ValueError("frame/calendar denominator differs")
    output = []
    last_epoch = {}
    for index, day in enumerate(sorted(calendar)):
        frame = frames[day]
        if frame.decision_view is None:
            raise ValueError("accepted daily decision view missing")
        for code in request["security_codes"]:
            member = members.get((code, day), {})
            known_actions = [item for item in actions[code] if item["effective_date"] <= day]
            epoch = (max(item["effective_date"] for item in known_actions).isoformat()
                     if known_actions else "original-price-quantity-epoch")
            reset = None
            if code in last_epoch and last_epoch[code] != epoch:
                reset = "corporate_action_coordinate_change"
            last_epoch[code] = epoch
            reason = None
            try:
                bar = frame.decision_view.bar(code)
            except KeyError:
                bar = None
                reason = "security_outside_decision_universe"
            if bar is not None:
                if bar.coverage_status.value != "trading":
                    reason = "no_observation_" + bar.coverage_status.value
                elif not bar.data_valid:
                    reason = "invalid_or_missing_daily_bar"
                elif bar.corporate_action_block_reason:
                    reason = "corporate_action_exception"
                elif bar.exchange != member.get("identity_exchange"):
                    reason = "source_identity_exchange_mismatch"
                else:
                    for name in ("high", "low", "close"):
                        exact = member.get(f"dwd_{name}_decimal")
                        observed = getattr(bar, f"{name}_price")
                        if exact is None or observed != Decimal(str(exact)):
                            reason = "source_projection_price_mismatch"
            artifact = str(member.get("dwd_bar_source_artifact_sha256") or "")
            if re.fullmatch(r"[0-9a-f]{64}", artifact):
                artifact = "sha256:" + artifact
            row = DailyInput(
                code, day, index, frame.snapshot.knowledge_cutoff,
                str(member.get("dwd_security_id") or ""),
                str(member.get("dwd_listing_id") or ""),
                str(member.get("identity_exchange") or ""),
                None if bar is None else bar.high_price,
                None if bar is None else bar.low_price,
                None if bar is None else bar.close_price,
                None if bar is None else bar.volume,
                str(member.get("dwd_bar_source_record_id") or ""),
                str(member.get("dwd_bar_source_revision") or ""), artifact,
                str(member.get("dwd_bar_knowledge_from") or ""), epoch,
                "final", reason, reset,
                calendar_member_digest=digest(calendar[day]),
                action_member_digest=digest(known_actions),
            )
            output.append(admit_daily(row, policy))
    return tuple(output), frames, policy


def run_from_admissions(
    request: dict, admissions: tuple[Admission, ...], frames: dict, policy: InputPolicy,
    provider_identity: dict, *, code_revision: str,
) -> tuple[dict, StructureV2Result, dict, dict, list[dict]]:
    stages = []
    first, last = (date.fromisoformat(request[key]) for key in ("report_start", "report_end"))
    started = time.perf_counter()
    readiness = build_readiness(admissions, policy, _stable_provider(provider_identity),
                                report_start=first, report_end=last)
    if not readiness["admitted"]:
        raise ValueError("real input slice has no admissible observations")
    stages.append({"stage": "B2-admission", "elapsed_seconds": time.perf_counter() - started,
                   "input_rows": len(admissions), "output_rows": readiness["admitted"]})
    started = time.perf_counter()
    structure = build_structure_v2(admissions, spec_digest=digest(calculation_spec(request)),
                                   calendar_ref=policy.calendar_ref,
                                   lookbacks=tuple(request["lookbacks"]))
    stages.append({"stage": "B3-B5-causal-build", "elapsed_seconds": time.perf_counter() - started,
                   "input_rows": len(admissions), "output_rows": len(structure.measurements)})
    started = time.perf_counter()
    decisions, audit = consume(structure, frames, code_revision=code_revision,
                               report_start=first, report_end=last)
    stages.append({"stage": "expert-aggregator-consumption",
                   "elapsed_seconds": time.perf_counter() - started,
                   "input_rows": len(decisions), "output_rows": len(audit["events"])})
    started = time.perf_counter()
    adm_manifest = {"schema_version": "quanthus.structure-v2-adm.v1",
                    "content_digest": structure.digest,
                    "spec_digest": digest(calculation_spec(request)),
                    "input_readiness_digest": digest(readiness), "research_only": True,
                    "execution_authorized": False, "knowledge_view": "retrospective"}
    probes = p2_probes(
        decisions, frames, security_codes=tuple(request["security_codes"]),
        maximum=request["maximum_p2_probes"], quantity=request["p2_probe_quantity"],
        request_digest=digest(request), structure_digest=structure.digest,
        adm_manifest_digest=digest(adm_manifest),
        execution_policy=load_execution_policy(find_project_root() / request["execution_policy"]),
    )
    stages.append({"stage": "P2-compatibility", "elapsed_seconds": time.perf_counter() - started,
                   "input_rows": len(probes),
                   "output_rows": sum(item["fill_count"] for item in probes)})
    counts = report_counts(structure, request)
    summary = {
        "schema_version": "quanthus.structure-v2-result.v1", "request": request,
        "code_revision": code_revision, "research_only": True, "execution_authorized": False,
        "economic_claim": False, "knowledge_view": "retrospective",
        "structure_digest": structure.digest, "input_readiness_digest": digest(readiness),
        "adm_manifest": adm_manifest,
        "report_counts": counts, "consumer_decisions": decisions, "p2_probes": probes,
        "review_sample": select_review_sample(structure, request),
    }
    return readiness, structure, summary, audit, stages


def write_result(
    root: Path, request: dict, admissions: tuple[Admission, ...], readiness: dict,
    structure: StructureV2Result, summary: dict, audit: dict, runtime: dict,
) -> None:
    root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "input-readiness.json": canonical(readiness),
        "structure.json": canonical(structure.semantic()),
        "source-inputs.jsonl": b"".join(canonical(asdict(item.row)) + b"\n" for item in admissions),
        "summary.json": canonical(summary), "audit.json": canonical(audit),
    }
    runtime = {**runtime,
               "published_business_and_audit_bytes": sum(len(value) for value in payloads.values())}
    payloads["runtime.json"] = canonical(runtime)
    if set(payloads) != FILES:
        raise ValueError("v2 publication inventory differs")
    manifest = {"schema_version": MANIFEST_SCHEMA, "code_revision": summary["code_revision"],
                "request_digest": digest(request), "research_only": True,
                "execution_authorized": False, "structure_digest": structure.digest,
                "input_readiness_digest": digest(readiness), "result_digest": digest(summary),
                "files": {name: "sha256:" + hashlib.sha256(value).hexdigest()
                          for name, value in payloads.items()}}
    payloads["manifest.json"] = canonical(manifest)
    payloads["READY"] = canonical({"schema_version": "quanthus.structure-v2-ready.v1",
                                   "manifest_sha256": digest(manifest)})
    for name, data in payloads.items():
        with (root / name).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    root = find_project_root()
    if (Path(args.request).as_posix() != REQUEST_PATH
        or not re.fullmatch(r"[0-9a-f]{40}", args.code_revision)):
        raise ValueError("v2 runner requires the fixed request and exact code revision")
    request = load_request(root / REQUEST_PATH)
    def deadline(_signum: int, _frame: object) -> None:
        raise TimeoutError("structure_v2_wall_budget_exceeded")
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(request["maximum_wall_seconds"])
    config = load_replay_data_provider_config(root / request["provider_config"])
    first, last = (date.fromisoformat(request[key]) for key in ("scan_start", "report_end"))
    if config.scope.partition != "development":
        raise ValueError("v2 sealed/non-development scope forbidden")
    config.scope.authorize(first, last)
    started = time.perf_counter()
    provider = ReplayDataProvider(config)
    with provider.open(scope_start=first, scope_end=last) as session:
        provider_seconds = time.perf_counter() - started
        extract_start = time.perf_counter()
        admissions, frames, policy = extract_admissions(session, request)
        extract_seconds = time.perf_counter() - extract_start
        readiness, structure, summary, audit, stages = run_from_admissions(
            request, admissions, frames, policy, session.identity, code_revision=args.code_revision,
        )
        runtime = {"schema_version": "quanthus.structure-v2-runtime.v1", "provider_builds": 1,
                   "cross_job_cache_hits": 0, "provider_lifecycle": "job_local_TMP",
                   "stages": [{"stage": "Direct-DWD-build-once",
                               "elapsed_seconds": provider_seconds,
                               "disposition": "built_once_shared_by_B2_B5_P2"},
                              {"stage": "source-member-extraction",
                               "elapsed_seconds": extract_seconds,
                               "output_rows": len(admissions)}, *stages]}
        write_result(
            Path(args.output), request, admissions, readiness, structure, summary, audit, runtime,
        )
    result = verify_structure_v2_artifact(Path(args.output), code_revision=args.code_revision,
                                          request_digest=digest(request))
    signal.alarm(0)
    print(canonical(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
