"""Read-only P6 mechanism review of already sealed fixed-snapshot artifacts.

This is not the signed, time-matured prospective review. It neither executes a
strategy again nor reads original data roots, computes returns or grants authority.
One cycle at a time is reconciled against the frozen paper policy. A matching
independent run alone cannot excuse a false census or a broken ledger chain.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.backtest.snapshot_clock import SnapshotReplayClock
from quanthus.event_library.contracts import event_record_from_dict
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.forward_validation.base24_cycle import load_runtime
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.historical_admission import check_unchanged
from quanthus.forward_validation.paper_ledger import (
    initial_paper_ledger,
    load_paper_policy,
    paper_ledger_from_dict,
    reconcile_paper_ledger,
)
from quanthus.forward_validation.paper_projection import paper_pending_intent_from_dict
from quanthus.forward_validation.serialization import digest_value
from quanthus.forward_validation.snapshot_replay import VERSION, load_plan, open_history
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    _fingerprint,
    _tree,
    physical_path,
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        fail("p6_review_object", path.name)
    return value


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        fail("p6_review_census", "count must be a nonnegative integer")
    return value


def _claims(value: object, expected: dict[str, Any]) -> None:
    if (
        not isinstance(value, dict)
        or value != expected
        or any(type(v) is not bool for v in value.values())
    ):
        fail("p6_review_authority", "fixed-snapshot claims differ")


def _sealed(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    m = _read(root / "manifest.json")
    if (
        m.get("schema_version") != VERSION
        or (root / "READY").read_text().strip() != file_sha(root / "manifest.json")
        or set(_tree(root)) != set(m["files"]) | {"manifest.json", "READY"}
    ):
        fail("p6_review_physical", "run manifest or physical namespace differs")
    before = {rel: _fingerprint(root / rel) for rel in _tree(root)}
    if any(file_sha(root / rel) != sha for rel, sha in m["files"].items()):
        fail("p6_review_physical", "run member bytes differ")
    return m, _read(root / "result.safe.json"), before


def review_snapshot_replay(
    *,
    history: Path,
    primary: Path,
    independent: Path,
    shared_owner: Path,
    repository: Path,
    config: Path,
    expected_manifests: Mapping[str, str],
) -> dict[str, Any]:
    """Verify the full mechanism artifact closure; return only safe evidence.

    Expected manifest hashes must be frozen by the caller before this read.
    Neither historical source_root strings nor receipt claims are dereferenced.
    Original input/production files, services and old ledgers are never written.
    """
    roots = {
        name: physical_path(path)
        for name, path in (
            ("history", history),
            ("primary", primary),
            ("independent", independent),
            ("shared_owner", shared_owner),
        )
    }
    if any(
        a == b or a in b.parents or b in a.parents
        for i, a in enumerate(roots.values())
        for b in list(roots.values())[i + 1 :]
    ):
        fail("p6_review_independence", "distinct nonoverlapping artifact roots required")
    if set(expected_manifests) != set(roots):
        fail("p6_review_binding", "four explicit manifest bindings required")
    for name, path in roots.items():
        _tree(path)  # Reject links before dereferencing even a metadata member.
        if file_sha(path / "manifest.json") != expected_manifests[name]:
            fail("p6_review_binding", name)
    history, primary, independent, shared_owner = (
        roots[n] for n in ("history", "primary", "independent", "shared_owner")
    )
    raw, plan = load_plan(repository, config)
    h, history_before = open_history(history)
    _claims(h["claims"], raw["claims"])
    runtime = load_runtime(repository / plan["policy_reference"]["path"], root=repository)
    if (
        h["closure"]["mode_config"] != file_sha(config)
        or h["closure"]["window_plan"] != raw["window_plan"]["sha256"]
        or h["closure"]["policy_hashes"] != {n: file_sha(p) for n, p in runtime.bindings.items()}
    ):
        fail("p6_review_binding", "history policy closure differs")
    policy = load_paper_policy(runtime.bindings["paper_lane"], root=repository)
    topology = ExpertStrategyRegistry(repository).topology_manifest.digest
    manifests, results, fingerprints = {}, {}, {"history": history_before}
    windows = h["windows"]
    dates = windows["decisions"] + windows["settlement_tail"]
    binding = digest_value(
        {"history": expected_manifests["history"], "mode_config": file_sha(config)}
    )
    for name, path in list(roots.items())[1:]:
        m, result, before = _sealed(path)
        manifests[name], results[name], fingerprints[name] = m, result, before
        _claims(result["claims"], raw["claims"])
        smoke = name == "shared_owner"
        days = windows["smoke"] if smoke else dates
        wanted = {"result.safe.json", "candidate-scan.safe.json", "final-state.private.json"}
        wanted.update(f"cycles/{day}.{kind}.json" for day in days for kind in ["private", "safe"])
        builds = int(name != "primary")
        if builds:
            wanted.update(["candidates.private.json", "feature-panel.parquet"])
        if set(m["files"]) != wanted:
            fail("p6_review_physical", "exact run file/date set differs")
        if (
            m["history_manifest"] != expected_manifests["history"]
            or result["history_identity"] != h["input_identity"]
            or result["knowledge_cutoff"] != h["knowledge_cutoff"]
            or result["schema_version"] != VERSION
            or result["smoke"] is not smoke
            or result["shared_prerequisite_binding"] != binding
            or result["shared_disposition"] != ("build" if builds else "reuse")
            or result["shared_builds"]
            != {k: builds for k in ("feature_panel", "candidate_scan", "frame_reader")}
        ):
            fail("p6_review_binding", "run scope or prerequisite identity differs")

    total: Counter[str] = Counter()
    smoke_total: Counter[str] = Counter()
    smoke_state: dict[str, Any] = {}
    reasons: Counter[str] = Counter()
    expert_paths: Counter[str] = Counter()
    applicability: Counter[str] = Counter()
    prior_ledger = initial_paper_ledger(policy).digest
    prior_intents: set[str] = set()
    seen_events: set[str] = set()
    cutoff = datetime.fromisoformat(h["knowledge_cutoff"])
    last_state: dict[str, Any] = {}
    for day in dates:
        cycle = _read(primary / "cycles" / (day + ".private.json"))
        summary = cycle["summary"]
        _claims(cycle["claims"], raw["claims"])
        clock = SnapshotReplayClock.from_json(json.dumps(cycle["clock"]))
        if clock != SnapshotReplayClock(date.fromisoformat(day), cutoff):
            fail("p6_review_clock", "cycle clock differs from frozen scope")
        last_state = cycle["state"]
        ledger = paper_ledger_from_dict(last_state["paper_ledger"])
        reconciled = reconcile_paper_ledger(ledger, policy=policy)
        if (
            not reconciled.balanced
            or summary["ledger_before"] != prior_ledger
            or summary["ledger_after"] != ledger.digest
            or summary["ledger_balanced"] is not True
        ):
            fail("p6_review_ledger", "recomputed account or continuous chain differs")
        if any(
            _count(last_state[k]) != 0 for k in ("collection_session_count", "tail_session_count")
        ):
            fail("p6_review_authority", "mechanism state credited an official budget")
        if last_state["market_date"] != day:
            fail("p6_review_census", "state date differs")
        intents = [paper_pending_intent_from_dict(x) for x in last_state["pending_paper_intents"]]
        intent_ids = {x.paper_intent_id for x in intents}
        if len(intent_ids) != len(intents):
            fail("p6_review_census", "duplicate pending intent")
        entries, held = cycle["entry_decisions"], cycle["held_decisions"]
        decision_phase = day in windows["decisions"]
        if entries and not decision_phase:
            fail("p6_review_tail", "new entry candidates in settlement tail")
        actions: Counter[str] = Counter()
        for row in entries + held:
            decision = row["formal_decision"]
            event = event_record_from_dict(row["event"])
            action, governance = decision["action"], decision["governance"]
            if (
                row["formal_decision_digest"] != digest_value(decision)
                or decision["event_id"] != event.event_id
                or decision["topology_digest"] != topology
                or action["governance_digest"] != digest_value(governance)
                or event.knowledge_cutoff != cutoff
                or event.identity.signal_date.isoformat() != day
                or event.snapshot_replay_clock_json != clock.to_json()
                or datetime.fromisoformat(action["knowledge_cutoff"]) != cutoff
                or datetime.fromisoformat(governance["knowledge_cutoff"]) != cutoff
            ):
                fail("p6_review_decision", "sealed expert/event/clock binding differs")
            actions[str(action["final_action"])] += 1
            reasons.update(str(x) for x in action["reason_codes"])
            for kind, outputs in (
                ("scoring", action["scoring_outputs"]),
                ("veto", governance["veto_outputs"]),
                ("constraint", governance["constraint_outputs"]),
            ):
                for item in outputs:
                    expert_paths[f"{kind}:{item['expert_id']}"] += 1
                    applicability[f"{kind}:{item['applicability']}"] += 1
        for row in entries:
            if row["trend_observation_digest"] != digest_value(row["trend_observation"]):
                fail("p6_review_decision", "trend observation binding differs")
            event_id = row["formal_decision"]["event_id"]
            if event_id in seen_events:
                fail("p6_review_census", "candidate counted more than once")
            seen_events.add(event_id)
        fills = 0
        for settlement in cycle["settlements"]:
            if (
                settlement["authority"] != "paper"
                or settlement["actual_execution_authorized"] is not False
                or settlement["market_date"] != day
                or settlement["reconciliation"]["balanced"] is not True
            ):
                fail("p6_review_settlement", "settlement authority/date/account differs")
            fills += _count(settlement["fill_count"])
        recomputed = {
            "market_date": day,
            "decision_phase": decision_phase,
            "candidate_decisions": len(entries),
            "held_reviews": len(held),
            "actions": dict(actions),
            "new_intents": len(intent_ids - prior_intents),
            "simulated_fills": fills,
            "ledger_before": prior_ledger,
            "ledger_after": ledger.digest,
            "ledger_balanced": True,
            "held_positions": len(last_state["positions"]),
            "pending_intents": len(intents),
        }
        if summary != recomputed or _read(primary / "cycles" / (day + ".safe.json")) != recomputed:
            fail("p6_review_census", "day counts differ from sealed decisions/state")
        total.update(actions)
        total.update(
            candidates=len(entries),
            held_reviews=len(held),
            simulated_fills=fills,
            new_intents=recomputed["new_intents"],
            settled_intents=len(cycle["settlements"]),
        )
        if day in windows["smoke"]:
            smoke_total.update(actions)
            smoke_total.update(
                candidates=len(entries),
                held_reviews=len(held),
                simulated_fills=fills,
                new_intents=recomputed["new_intents"],
                settled_intents=len(cycle["settlements"]),
            )
            smoke_state = last_state
        prior_ledger, prior_intents = ledger.digest, intent_ids
    result = results["primary"]
    if (
        result["counters"] != dict(total)
        or _count(result["processed_sessions"]) != len(dates)
        or _count(result["decision_sessions"]) != len(windows["decisions"])
        or _count(result["tail_sessions"]) != len(windows["settlement_tail"])
        or result["run_start"] != dates[0]
        or result["run_end"] != dates[-1]
        or result["all_ledgers_balanced"] is not True
        or _count(result["held_positions_at_end"]) != len(last_state["positions"])
        or _count(result["pending_intents_at_end"]) != len(prior_intents)
        or _read(primary / "final-state.private.json") != last_state
    ):
        fail("p6_review_census", "full-run counts/final state differ")
    for name in ("primary", "independent", "shared_owner"):
        result = results[name]
        smoke = name == "shared_owner"
        expected_counts = smoke_total if smoke else total
        expected_state = smoke_state if smoke else last_state
        days = windows["smoke"] if smoke else dates
        if (
            result["counters"] != dict(expected_counts)
            or _count(result["processed_sessions"]) != len(days)
            or _count(result["decision_sessions"])
            != len(windows["smoke"] if smoke else windows["decisions"])
            or _count(result["tail_sessions"]) != (0 if smoke else len(windows["settlement_tail"]))
            or result["run_start"] != days[0]
            or result["run_end"] != days[-1]
            or result["all_ledgers_balanced"] is not True
            or result["real_path_coverage"]
            != {
                "nonempty_candidate_and_expert_path": expected_counts["candidates"] > 0,
                "simulated_fill_path": expected_counts["simulated_fills"] > 0,
                "held_review_path": expected_counts["held_reviews"] > 0,
            }
            or result["slices"]
            != ([] if smoke else [{**s, "ledger_reset": False} for s in plan["report_slices"]])
            or _count(result["held_positions_at_end"]) != len(expected_state["positions"])
            or _count(result["pending_intents_at_end"])
            != len(expected_state["pending_paper_intents"])
            or result["result"] != "MECHANISM_REPLAY_COMPLETED_NOT_PIT_OR_ECONOMIC_ACCEPTANCE"
            or _read(roots[name] / "final-state.private.json") != expected_state
        ):
            fail("p6_review_census", "reproduction or smoke census/final state differs")
    # Physical hash equality is checked after primary semantics so repackaged
    # false summaries cannot be excused by a matching independent counterfeit.
    pairs = [
        (primary / rel, independent / rel)
        for rel in manifests["primary"]["files"]
        if rel != "result.safe.json"
    ]
    pairs.extend(
        (shared_owner / rel, independent / rel)
        for rel in ("feature-panel.parquet", "candidates.private.json")
    )
    pairs.extend(
        (
            shared_owner / "cycles" / (day + ".private.json"),
            primary / "cycles" / (day + ".private.json"),
        )
        for day in windows["smoke"]
    )
    for a, b in pairs:
        if file_sha(a) != file_sha(b) or (a.stat().st_dev, a.stat().st_ino) == (
            b.stat().st_dev,
            b.stat().st_ino,
        ):
            fail(
                "p6_review_reproduction", "independent/prefix bytes or physical independence differ"
            )
    statuses = Counter(str(x["status"]) for x in last_state["forecasts"])
    forecast_ids = {x["forecast_id"] for x in last_state["forecasts"]}
    if (
        len(forecast_ids) != len(last_state["forecasts"])
        or {x["event_id"] for x in last_state["forecasts"]} != seen_events
    ):
        fail("p6_review_census", "forecast/candidate closure differs")
    held_reasons = Counter(
        str(x["reason_code"])
        for x in last_state["forecasts"]
        if x["status"] in {"not_executable", "unavailable", "abstained"}
    )
    unresolved = sum(
        n
        for state, n in statuses.items()
        if state not in {"abstained", "settled", "not_executable", "unavailable"}
    )
    for name, path in roots.items():
        check_unchanged(path, fingerprints[name])
    return {
        "schema_version": "quanthus.p6-fixed-snapshot-mechanism-review.v1",
        "mechanism_review": "VERIFIED",
        "input_manifests": dict(expected_manifests),
        "producer_code_revision": h["closure"]["code_revision"],
        "claims": raw["claims"],
        "knowledge_cutoff": h["knowledge_cutoff"],
        "scope": {
            "selected_listings": h["census"]["selected_listings"],
            "hindsight_engineering_cohort": True,
            "representative_market_sample": False,
        },
        "sessions": len(dates),
        "ledger_reconciliations": len(dates),
        "counters": dict(sorted(total.items())),
        "action_reason_counts": dict(sorted(reasons.items())),
        "expert_output_path_counts": dict(sorted(expert_paths.items())),
        "expert_applicability_counts": dict(sorted(applicability.items())),
        "forecast_terminal_status_counts": dict(sorted(statuses.items())),
        "held_forecast_reason_counts": dict(sorted(held_reasons.items())),
        "unresolved_forecasts": unresolved,
        "comparison": {
            "independent_cycles_byte_equal": len(dates),
            "smoke_prefix_cycles_byte_equal": len(windows["smoke"]),
            "feature_and_candidate_bytes_equal": True,
            "separate_physical_files": True,
        },
        "original_source_roots_read": False,
        "sealed_inputs_unchanged": True,
        "official_budget_credit": 0,
        "formal_p6_review_ready": False,
        "formal_review_disposition": "NOT_OFFICIAL_PROSPECTIVE_EVIDENCE",
    }
