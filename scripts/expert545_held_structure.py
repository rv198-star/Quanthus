#!/usr/bin/env python3
"""One immutable held-path structure TMP and one fixed-policy pointwise comparison."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import resource
import shutil
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_protocols import RequiredAction
from quanthus.expert_strategy import ExpertStrategyRegistry, FormalStrategyRunner
from quanthus.expert_strategy.held_structure_inputs import historical_daily_inputs
from quanthus.expert_strategy.held_structure_shadow import (
    SupportDecisionInput,
    evaluate_shadow,
    restore_context,
    restore_position_facts,
    support_decision_inputs,
)
from quanthus.stage_projection import load_stage_projection_contract
from quanthus.structure_support_facts import build_support_failure_facts, support_fact_spec
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import InputPolicy, admit_daily, canonical, digest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/research/expert545-held-structure.v1.json"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024**2), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    with path.open("xb") as handle:
        handle.write(canonical(value) + b"\n")


def _guard(job: Path) -> None:
    scope = json.loads((job / "scope.frozen.json").read_bytes())
    if datetime.now().astimezone() >= datetime.fromisoformat(scope["deadline"]):
        raise ValueError("HELD_JOB_DEADLINE")
    if shutil.disk_usage(job).free < scope["minimum_free_bytes"]:
        raise ValueError("HELD_JOB_FREE_SPACE")
    if sum(p.stat().st_size for p in job.rglob("*") if p.is_file()) > scope["maximum_new_bytes"]:
        raise ValueError("HELD_JOB_OUTPUT_CAP")


def freeze(job: Path, archive_freeze: Path, held_file: Path, footprint: Path) -> dict[str, Any]:
    import duckdb

    _guard(job)
    if (job / "inputs.final.frozen.json").exists():
        raise ValueError("HELD_INPUT_FREEZE_EXISTS")
    trace = job / "trace/held-contexts.private.jsonl.gz"
    expected = json.loads(footprint.read_bytes())["shared_intermediate"]["sha256"].removeprefix(
        "sha256:"
    )
    if sha(held_file) != expected:
        raise ValueError("HELD_POPULATION_BYTES")
    held = json.loads(held_file.read_bytes())["rows"]["B"]
    if (
        len(held),
        len({r["security_code"] for r in held}),
        len({r["entry_key"] for r in held}),
    ) != (8393, 343, 538):
        raise ValueError("HELD_FROZEN_DENOMINATOR")
    archive = json.loads(archive_freeze.read_bytes())
    bindings = archive["roles"]["primary"]["bindings"]
    effective = json.loads((job / "trace/effective-members.json").read_bytes())
    selected = {
        name: effective[name][0]
        for name in (
            "daily_ohlcv_none",
            "security_code_validity",
            "trading_calendar",
            "corporate_actions_normalized",
            "corporate_action_exceptions",
        )
        if len(effective[name]) == 1
    }
    if len(selected) != 5 or any(p not in bindings for p in selected.values()):
        raise ValueError("HELD_EFFECTIVE_SOURCE_MEMBERS")
    supplemental = [
        p
        for p in bindings
        if p.endswith("/official/facts.json")
        or p.endswith("/rights-event-source-facts.json")
        or p == str(Path(archive["roles"]["primary"]["source"]) / "official/manifest.json")
    ]
    pins = {p: bindings[p] for p in [*selected.values(), *supplemental]}
    for p, identity in pins.items():
        if (
            Path(p).resolve() != Path(p)
            or Path(p).stat().st_size != identity["bytes"]
            or "sha256:" + sha(Path(p)) != identity["sha256"]
        ):
            raise ValueError("HELD_SELECTED_SOURCE_CHANGED")
    code_pins = {
        str(p.relative_to(ROOT)): sha(p)
        for p in (
            CONFIG,
            ROOT / "src/quanthus/expert_strategy/held_structure_inputs.py",
            ROOT / "src/quanthus/expert_strategy/held_structure_shadow.py",
            ROOT / "src/quanthus/structure_v2.py",
            ROOT / "src/quanthus/structure_v2_inputs.py",
            ROOT / "src/quanthus/structure_support_facts.py",
            ROOT / "src/quanthus/stage_projection.py",
            ROOT / "config/stage-10-11-projection.v1.json",
            ROOT / "config/expert-strategies/p4-trend-risk.v1.0.1.json",
            ROOT / "src/quanthus/expert_strategy/runner.py",
            Path(__file__),
        )
    }
    ranges: dict[str, dict[str, Any]] = {}
    for r in held:
        code = r["security_code"]
        item = ranges.setdefault(code, {"code": code, "through": r["market_date"], "held_rows": 0})
        item["through"] = max(item["through"], r["market_date"])
        item["held_rows"] += 1
    with duckdb.connect(":memory:") as c:
        # Census reads identities/dates only; no result-selected warmup or sample.
        counts = c.execute(
            "SELECT stock_code,count(*) FROM read_parquet(?) "
            "WHERE date >= '2011-01-04' AND date <= '2021-12-30' GROUP BY stock_code",
            [selected["daily_ohlcv_none"]],
        ).fetchall()
    for code, count in counts:
        if code in ranges:
            ranges[code]["raw_rows_upper_bound"] = count
    value = {
        "kind": "expert545-held-structure-inputs-v1",
        "archive_freeze": str(archive_freeze),
        "archive_freeze_sha256": sha(archive_freeze),
        "source_manifest_sha256": (
            "sha256:bb02e9695716d29683e78c9c497b87286743b35f087d06509eafe43985ee2ff8"
        ),
        "selected": selected,
        "supplemental": supplemental,
        "source_pins": pins,
        "held_file": str(held_file),
        "held_sha256": sha(held_file),
        "trace": str(trace),
        "trace_qualification": "PENDING_UNCHANGED_B_REPRODUCTION_BEFORE_COMPARISON",
        "code_config_sha256": code_pins,
        "config": json.loads(CONFIG.read_bytes()),
        "ranges": sorted(ranges.values(), key=lambda r: (-r["raw_rows_upper_bound"], r["code"])),
        "source_cutoff": "2026-10-25T08:51:06.934678+00:00",
        "concurrency": 2,
        "ownership": "job-local TMP; one producer; both representations reuse",
        "budget": {"maximum_new_bytes": 2 * 1024**3, "minimum_free_bytes": 8 * 1024**3},
        "same_UTC_snapshot": {
            "observed_at": datetime.now().astimezone(),
            "device": job.stat().st_dev,
            "free_bytes": shutil.disk_usage(job).free,
            "free_inodes": __import__("os").statvfs(job).f_favail,
        },
    }
    write_json(job / "inputs.final.frozen.json", value)
    return {
        "held_rows": 8393,
        "securities": 343,
        "episodes": 538,
        "selected_members": len(pins),
        "input_freeze_sha256": sha(job / "inputs.final.frozen.json"),
    }


def _security_build(task: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    rows = historical_daily_inputs(**task["inputs"])
    config = task["config"]
    spec = digest(support_fact_spec(config))
    calendar = task["inputs"]["calendar_member_sha256"]
    policy = InputPolicy(spec, (task["code"],), calendar)
    admissions = tuple(admit_daily(row, policy) for row in rows)
    structure = build_structure_v2(
        admissions, spec_digest=spec, calendar_ref=calendar, lookbacks=(20,)
    )
    facts = build_support_failure_facts(
        admissions, structure, spec_digest=spec, maximum_sessions=60
    )
    contract = load_stage_projection_contract(ROOT / config["projection_contract"])
    decision_inputs = support_decision_inputs(facts, contract)
    availability = {r["market_date"]: r for r in structure.availability}
    ongoing = {r.market_date for r in structure.episodes}
    by_day = {a.row.market_date: a for a in admissions}
    output = []
    for day in task["held_days"]:
        key = (task["code"], day)
        if key in decision_inputs:
            item = decision_inputs[key]
        else:
            attempt = by_day.get(day)
            ready = attempt is not None and attempt.admitted and availability[day]["available"]
            reason = (
                "missing_calendar_attempt"
                if attempt is None
                else attempt.reason
                if not attempt.admitted
                else "insufficient_history"
                if not ready
                else "outside_declared_support_horizon"
                if day in ongoing
                else "no_support_process"
            )
            item = SupportDecisionInput(
                task["code"],
                day,
                bool(ready and day not in ongoing),
                reason,
                None,
                None,
                None,
                None,
                None,
                None,
                False,
                None,
                False,
                False,
                None,
                digest(
                    {
                        "attempt": None if attempt is None else attempt.row.member_digest,
                        "reason": reason,
                    }
                ),
            )
        output.append(asdict(item))
    target = Path(task["output"]) / (task["code"] + ".private.json.gz")
    payload = {
        "kind": "held-support-TMP-v1",
        "lifecycle": "TMP",
        "inputs_digest": digest([asdict(row) for row in rows]),
        "source_timing": {
            "dwd_known_from": "unavailable",
            "provider_acquired_at": "unavailable",
            "built_at_meaning": "artifact_generation_only_not_a_knowledge_clock",
            "source_built_at_by_date": {
                bar["date"].isoformat(): bar["built_at"] for bar in task["inputs"]["bars"]
            },
        },
        "facts": facts.semantic(),
        "held_decision_inputs": output,
    }
    with gzip.open(target, "xb") as handle:
        handle.write(canonical(payload))
    receipt = {
        "code": task["code"],
        "attempts": len(rows),
        "admitted": sum(a.admitted for a in admissions),
        "held_day_count": len(output),
        "held_available_days": sum(r["available"] for r in output),
        "unavailable_reasons": dict(Counter(r["reason"] for r in output if not r["available"])),
        "facts_sha256": sha(target),
        "bytes": target.stat().st_size,
        "elapsed_seconds": time.monotonic() - start,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }
    return receipt


def build(job: Path) -> dict[str, Any]:
    import duckdb
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    _guard(job)
    start = time.monotonic()
    frozen = json.loads((job / "inputs.final.frozen.json").read_bytes())
    for name, expected in frozen["code_config_sha256"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("HELD_FROZEN_CODE_CHANGED:" + name)
    output = job / "facts"
    output.mkdir()
    selected = frozen["selected"]
    held = json.loads(Path(frozen["held_file"]).read_bytes())["rows"]["B"]
    codes = sorted({r["security_code"] for r in held})
    identities = pq.read_table(
        selected["security_code_validity"], filters=[("stock_code", "in", codes)]
    ).to_pylist()
    if len(identities) != len(codes) or len({r["stock_code"] for r in identities}) != len(codes):
        raise ValueError("HELD_LISTING_COVERAGE")
    validity = {r["stock_code"]: r for r in identities}
    calendars: dict[str, list[date]] = defaultdict(list)
    for row in pq.read_table(selected["trading_calendar"]).to_pylist():
        if row["is_open"] == 1 and date(2011, 1, 4) <= row["trade_date"] <= date(2021, 12, 30):
            calendars[row["exchange"]].append(row["trade_date"])
    if any(len(values) != len(set(values)) for values in calendars.values()):
        raise ValueError("HELD_CALENDAR_DUPLICATE")
    for values in calendars.values():
        values.sort()
    boundaries: dict[str, dict[date, list[str]]] = defaultdict(lambda: defaultdict(list))
    actions = pq.read_table(
        selected["corporate_actions_normalized"],
        columns=["security_code", "effective_date", "action_id", "evidence_digest"],
    ).to_pylist()
    for a in actions:
        if a["security_code"] in codes and a["effective_date"] is not None:
            boundaries[a["security_code"]][a["effective_date"]].append(
                a["action_id"] + ":" + a["evidence_digest"]
            )
    for name in frozen["supplemental"]:
        raw = json.loads(Path(name).read_bytes())
        events = raw.get("events", [])
        if "rights-event-source-facts.json" in name:
            events = [{"security_code": raw["security_code"], "facts": raw["facts"]}]
        if raw.get("terms"):
            terms = raw["terms"]
            events = [
                {
                    "security_code": terms["security_id"],
                    "facts": {
                        k: {"value": v}
                        for k, v in terms.items()
                        if k in {"resume_unbounded_date", "new_share_listing_date"}
                    },
                }
            ]
        for event in events:
            for field in ("ex_date", "ex_right_date", "resume_unbounded_date"):
                value = event["facts"].get(field)
                value = value.get("value") if isinstance(value, dict) else value
                if isinstance(value, str):
                    boundaries[event["security_code"]][date.fromisoformat(value)].append(
                        frozen["source_pins"][name]["sha256"] + ":" + field
                    )
    blocked: dict[str, list[tuple[date, date, str]]] = defaultdict(list)
    for r in pq.read_table(selected["corporate_action_exceptions"]).to_pylist():
        if r["security_code"] in codes:
            blocked[r["security_code"]].append(
                (
                    r["blocked_from"] or date(2011, 1, 4),
                    r["blocked_through"] or date(2021, 12, 30),
                    r["exception_id"],
                )
            )
    action_identity = digest(
        {
            "action_members": {
                p: v
                for p, v in frozen["source_pins"].items()
                if p
                not in [
                    selected["daily_ohlcv_none"],
                    selected["security_code_validity"],
                    selected["trading_calendar"],
                ]
            }
        }
    )
    held_days: dict[str, set[date]] = defaultdict(set)
    for r in held:
        held_days[r["security_code"]].add(date.fromisoformat(r["market_date"]))
    receipts = []
    with duckdb.connect(
        ":memory:",
        config={
            "threads": 2,
            "memory_limit": "1GB",
            "temp_directory": str(job / "tmp/duckdb"),
            "max_temp_directory_size": "128MB",
        },
    ) as c:
        # One shared input projection; later workers never reopen the raw source.
        placeholders = ",".join("?" for _ in codes)
        c.execute(
            "CREATE TEMP TABLE selected_raw AS SELECT * FROM read_parquet(?) "
            "WHERE date >= '2011-01-04' AND date <= '2021-12-30' AND stock_code IN ("
            + placeholders
            + ")",
            [selected["daily_ohlcv_none"], *codes],
        )
        with ProcessPoolExecutor(max_workers=2) as pool:
            pending = set()
            for number, item in enumerate(frozen["ranges"]):
                _guard(job)
                code = item["code"]
                through = date.fromisoformat(item["through"])
                rows = (
                    c.execute(
                        "SELECT * FROM selected_raw WHERE stock_code=? AND date <= ? ORDER BY date",
                        [code, through],
                    )
                    .fetch_arrow_table()
                    .to_pylist()
                )
                task = {
                    "code": code,
                    "held_days": sorted(held_days[code]),
                    "config": frozen["config"],
                    "output": str(output),
                    "inputs": {
                        "code": code,
                        "validity": validity[code],
                        "bars": rows,
                        "sessions": calendars[validity[code]["exchange"]],
                        "through": through,
                        "source_cutoff": datetime.fromisoformat(frozen["source_cutoff"]),
                        "raw_member_sha256": frozen["source_pins"][selected["daily_ohlcv_none"]][
                            "sha256"
                        ],
                        "calendar_member_sha256": frozen["source_pins"][
                            selected["trading_calendar"]
                        ]["sha256"],
                        "action_closure_sha256": action_identity,
                        "action_boundaries": {
                            d: tuple(sorted(v)) for d, v in boundaries[code].items()
                        },
                        "blocked_intervals": tuple(blocked[code]),
                    },
                }
                if number == 0:
                    # Largest declared input first; fail engineering cheaply before broad fanout.
                    result = _security_build(task)
                    receipts.append(result)
                    write_json(job / "evidence/largest-security-cost.safe.json", result)
                    print(json.dumps({"stage": "largest_security_pass", **result}), flush=True)
                    continue
                pending.add(pool.submit(_security_build, task))
                if len(pending) >= 4:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    receipts.extend(f.result() for f in done)
                    print(
                        json.dumps(
                            {
                                "stage": "facts",
                                "securities_completed": len(receipts),
                                "elapsed_seconds": time.monotonic() - start,
                            }
                        ),
                        flush=True,
                    )
            receipts.extend(f.result() for f in pending)
    result = {
        "kind": "expert545-one-materialization-safe-v1",
        "securities": len(receipts),
        "input_freeze_sha256": sha(job / "inputs.final.frozen.json"),
        "materializations": 1,
        "materialization_scope": "one successful qualified generation; failed attempts retained",
        "consumers": ["continuous_confirmation", "stage10_11"],
        "receipts": sorted(receipts, key=lambda r: r["code"]),
        "elapsed_seconds": time.monotonic() - start,
        "peak_parent_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }
    if len(receipts) != 343:
        raise ValueError("HELD_FACT_DENOMINATOR")
    write_json(job / "evidence/facts-materialization.safe.json", result)
    return {k: v for k, v in result.items() if k != "receipts"}


def compare(job: Path) -> dict[str, Any]:
    _guard(job)
    start = time.monotonic()
    frozen = json.loads((job / "inputs.final.frozen.json").read_bytes())
    materialized = json.loads((job / "evidence/facts-materialization.safe.json").read_bytes())
    if materialized["input_freeze_sha256"] != sha(job / "inputs.final.frozen.json"):
        raise ValueError("HELD_FACT_INPUT_IDENTITY")
    inputs: dict[tuple[str, date], SupportDecisionInput] = {}
    for r in materialized["receipts"]:
        path = job / "facts" / (r["code"] + ".private.json.gz")
        if sha(path) != r["facts_sha256"]:
            raise ValueError("HELD_FACT_BYTES")
        with gzip.open(path, "rt") as handle:
            values = json.load(handle)["held_decision_inputs"]
        for raw in values:
            raw["market_date"] = date.fromisoformat(raw["market_date"])
            raw["confirmation_date"] = (
                None
                if raw["confirmation_date"] is None
                else date.fromisoformat(raw["confirmation_date"])
            )
            for name in ("signed_distance", "rebound"):
                raw[name] = None if raw[name] is None else Decimal(raw[name])
            raw["process_revisions"] = tuple(raw["process_revisions"])
            support_row = SupportDecisionInput(**raw)
            if (support_row.security_code, support_row.market_date) in inputs:
                raise ValueError("HELD_FACT_DUPLICATE")
            inputs[support_row.security_code, support_row.market_date] = support_row
    held = json.loads(Path(frozen["held_file"]).read_bytes())["rows"]["B"]
    identities = {(r["market_date"], r["unit_id"]): r for r in held}
    registry = ExpertStrategyRegistry(ROOT)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    changed: list[dict[str, str]] = []
    all_rows = []
    seen = set()
    trace = Path(frozen["trace"])
    proof = json.loads((job / "trace/receipt.safe.json").read_bytes())
    if (
        proof["rows"] != 8393
        or proof["source_freeze_sha256"] != frozen["archive_freeze_sha256"]
        or not all(v["equal"] for v in proof["comparisons"].values())
        or sha(trace) != proof["trace_sha256"]
    ):
        raise ValueError("HELD_TRACE_CHANGED")
    with gzip.open(trace, "rt") as handle:
        for line in handle:
            raw = json.loads(line)
            key = raw["market_date"], raw["unit_id"]
            if key in seen or key not in identities:
                raise ValueError("HELD_CONTEXT_DENOMINATOR")
            seen.add(key)
            identity = identities[key]
            if (raw["security_code"], raw["quantity"]) != (
                identity["security_code"],
                identity["quantity"],
            ):
                raise ValueError("HELD_CONTEXT_SECURITY_OR_QUANTITY")
            day = date.fromisoformat(raw["market_date"])
            support = inputs[raw["security_code"], day]
            context = restore_context(raw["context"])
            facts = restore_position_facts(raw["position_facts"])
            kwargs = dict(
                event_id=raw["event_id"],
                context=context,
                position_facts=facts,
                base_required_action=RequiredAction(raw["base_required_action"]),
                base_reason_code=raw["base_reason_code"],
                support=support,
                quantity=raw["quantity"],
                native_security_code=raw["security_code"],
                target_notional=Decimal(raw["target_notional"]),
                last_mark_price=None
                if raw["last_mark_price"] is None
                else Decimal(raw["last_mark_price"]),
            )
            left = evaluate_shadow(runner, representation="continuous", **kwargs)
            right = evaluate_shadow(runner, representation="stage10_11", **kwargs)
            if (
                left.baseline.digest != raw["baseline_decision_digest"]
                or left.candidate != right.candidate
            ):
                raise ValueError("HELD_BASELINE_OR_REPRESENTATION_MISMATCH")
            counts["held_rows"] += 1
            counts["structural_available_rows"] += support.available
            counts["confirmed_stage10_rows"] += support.stage10_active
            counts["stage11_explanation_rows"] += support.stage11_active
            counts["proposed_rows"] += left.proposed
            counts["final_action_changed_rows"] += left.final_action_changed
            counts["proposed_but_baseline_safety_wins"] += (
                left.proposed and not left.final_action_changed
            )
            reasons[left.reason] += 1
            row = {
                "market_date": raw["market_date"],
                "unit_id": raw["unit_id"],
                "security_code": raw["security_code"],
                "entry_key": identity["entry_key"],
                "support_available": support.available,
                "support_reason": support.reason,
                "support_lineage_digest": support.lineage_digest,
                "baseline_decision_digest": left.baseline.digest,
                "candidate_digest": left.candidate.digest,
                "baseline_action": left.baseline.action.final_action.value,
                "candidate_action": left.candidate.action.final_action.value,
                "proposed": left.proposed,
                "final_action_changed": left.final_action_changed,
                "reason": left.reason,
            }
            all_rows.append(row)
            if left.final_action_changed:
                changed.append(
                    {k: row[k] for k in ("security_code", "entry_key", "market_date", "unit_id")}
                )
    if seen != set(identities) or counts["held_rows"] != 8393:
        raise ValueError("HELD_FULL_DENOMINATOR_NOT_RETAINED")
    with gzip.open(job / "decision-shadow.private.json.gz", "xb") as handle:
        handle.write(canonical(all_rows))
    episodes = {r["entry_key"] for r in changed}
    result = {
        "kind": "expert545-fixed-B-shadow-safe-v1",
        "counts": dict(counts),
        "reasons": dict(reasons),
        "full_denominator": {"held_reviews": 8393, "issuers": 343, "entry_episodes": 538},
        "changed_distinct_issuers": len({r["security_code"] for r in changed}),
        "changed_distinct_entry_episodes": len(episodes),
        "changed_unique_dates": len({r["market_date"] for r in changed}),
        "changed_rows_by_year": dict(
            sorted(Counter(r["market_date"][:4] for r in changed).items())
        ),
        "changed_rows_by_issuer": dict(
            sorted(Counter(r["security_code"] for r in changed).items())
        ),
        "available_denominator_reason_counts": dict(Counter(r["support_reason"] for r in all_rows)),
        "continuous_stage_final_decision_mismatches": 0,
        "baseline_decision_digest_mismatches": 0,
        "decision": "PARK_NO_DISTRIBUTED_DECISION_DELTA"
        if len(episodes) <= 1
        else "DISTRIBUTED_SHADOW_DELTA_SEPARATE_CAPITAL_COMPARISON_MAY_BE_PROPOSED",
        "input_freeze_sha256": sha(job / "inputs.final.frozen.json"),
        "trace_proof_sha256": sha(job / "trace/receipt.safe.json"),
        "decision_trace_sha256": sha(job / "decision-shadow.private.json.gz"),
        "elapsed_seconds": time.monotonic() - start,
        "economic_replay": "NOT_RUN_NOT_AUTHORIZED",
        "effect_on_returns": None,
        "historical_PIT_claim": False,
        "independent_holdout_claim": False,
        "production_or_P6_authorized": False,
        "limitation": (
            "Repeated pointwise proposals share original B state; unique entry episodes "
            "are not executed reductions or a counterfactual portfolio."
        ),
    }
    write_json(job / "evidence/decision-comparison.safe.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "build", "compare"))
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--archive-freeze", type=Path)
    parser.add_argument("--held-file", type=Path)
    parser.add_argument("--footprint", type=Path)
    args = parser.parse_args()
    if args.phase == "freeze":
        print(
            json.dumps(
                freeze(args.job, args.archive_freeze, args.held_file, args.footprint), indent=2
            )
        )
    else:
        print(json.dumps((build if args.phase == "build" else compare)(args.job), indent=2))
