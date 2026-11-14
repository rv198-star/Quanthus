"""Three complete controlled capital paths and evidence-derived feedback."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.backtest.engine import UnsupportedModeError
from quanthus.backtest.native_capital_replay import CapitalInputHold
from quanthus.expert_evaluation.serialization import ExpertEvaluationError
from quanthus.r10_inputs import NativeInputs, check_contract, digest, read, rows, sha, write
from quanthus.r10_replay import ControlledReplay
from quanthus.r10_study import admitted, compare, fit_correction, load_bound, screen

PHASE = {"name": "starting", "deadline": float("inf")}


def begin_phase(name, seconds):
    PHASE.update(name=name, deadline=time.monotonic() + seconds)


def failure_class(exc):
    source_hold = (
        isinstance(exc, CapitalInputHold)
        or (
            isinstance(exc, ExpertEvaluationError)
            and exc.reason_code == "formal_sell_bonus_share_unsupported"
        )
        or (
            isinstance(exc, UnsupportedModeError)
            and str(exc)
            in {
                "corporate action creates a fractional share outside the first P2 rule",
                "corporate action cannot preserve the pending sell proportion",
            }
        )
    )
    return "SOURCE_HOLD" if source_hold else "IMPLEMENTATION_FAILURE"


def check_fit(contract, qualification, fit):
    c, admission = admitted(contract, qualification)
    frozen = read(fit / "manifest.json")
    if (
        frozen["contract_sha256"] != sha(contract)
        or frozen["steps"] != 100
        or frozen["selected_sha256"] != sha(qualification / "selected.private.json")
    ):
        raise ValueError("R10_CAPITAL_FIT_BINDING")
    for name, expected in frozen["files"].items():
        if sha(fit / name) != expected:
            raise ValueError("R10_CAPITAL_SEALED_FIT_CHANGED")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(admission["C_deadline"]):
        raise ValueError("R10_C_DEADLINE")
    return c, admission, frozen


def trade_summary(events, identities):
    """Cash plus external-pool flows reconcile closed round trips without outcome labels."""
    active = {}
    closed = []
    for event in sorted(events, key=lambda e: (e["event_time"], e["sequence"], e["event_id"])):
        p = event["payload"]
        if event["event_type"] == "ledger_entry":
            postings = p["postings"]
            if sum((Decimal(v["amount"]) for v in postings), Decimal(0)) != 0:
                raise ValueError("R10_CAPITAL_LEDGER_UNBALANCED")
            unit = p["unit_id"]
            if p["reference_type"] == "fill" and p["quantity_delta"] > 0:
                if unit in active:
                    raise ValueError("R10_CAPITAL_ROUNDTRIP_OVERLAP")
                listing = p["security_code"]
                active[unit] = {
                    "listing_id": listing,
                    "company_id": identities[listing]["company_id"],
                    "entry_fill_date": event["event_time"][:10],
                    "cash_flow": Decimal(0),
                }
            if unit in active:
                active[unit]["cash_flow"] += sum(
                    (
                        Decimal(v["amount"])
                        for v in postings
                        if v["account"].endswith(("/cash", "/external_pool"))
                    ),
                    Decimal(0),
                )
        elif event["event_type"] == "daily_snapshot":
            unit = p["unit_id"]
            if p["position_quantity"] == 0 and unit in active:
                closed.append(dict(active.pop(unit), exit_date=p["session_date"]))
    wins = [r["cash_flow"] for r in closed if r["cash_flow"] > 0]
    losses = [r["cash_flow"] for r in closed if r["cash_flow"] < 0]
    by_company, by_entry_date = defaultdict(Decimal), defaultdict(Decimal)
    for r in closed:
        by_company[r["company_id"]] += r["cash_flow"]
        by_entry_date[r["entry_fill_date"]] += r["cash_flow"]
    return {
        "closed_roundtrips": len(closed),
        "open_roundtrips": len(active),
        "wins": len(wins),
        "losses": len(losses),
        "ties": len(closed) - len(wins) - len(losses),
        "win_rate": None if not closed else str(Decimal(len(wins)) / len(closed)),
        "average_win_to_average_loss": None
        if not wins or not losses
        else str((sum(wins) / len(wins)) / (-sum(losses) / len(losses))),
        "closed_pnl_by_company": dict(sorted(by_company.items())),
        "closed_pnl_by_entry_fill_date": dict(sorted(by_entry_date.items())),
        "closed_roundtrip_rows": closed,
        "contribution_scope": "closed trips; open positions remain in equity and final ledgers",
    }


def capital(project, contract, qualification, *, native=None, fit=None, output=None):
    begin_phase("three_capital_arms", 1800)
    fit = fit or qualification.parent / "fit"
    output = output or qualification.parent / "capital"
    c, admission, fitted = check_fit(contract, qualification, fit)
    comparison = fit / "comparison.safe.json"
    if not comparison.is_file():
        raise ValueError("R10_CAPITAL_REQUIRES_SEALED_RECOGNITION_COMPARISON")
    if read(comparison)["prediction_commitment_sha256"] != sha(fit / "manifest.json"):
        raise ValueError("R10_CAPITAL_RECOGNITION_COMMITMENT_CHANGED")
    output.mkdir()
    write(
        output / "attempt.json",
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "contract_sha256": sha(contract),
            "fit_manifest_sha256": sha(fit / "manifest.json"),
            "maximum_arms": 3,
        },
    )
    owned_native = native is None
    native = native or NativeInputs(project, contract, qualification / "native")
    results = {}
    active_arm = None
    runtime = None
    try:
        benchmark = rows(
            native.db,
            """SELECT CAST(b.observation_start AS DATE) market_date,b.close
            FROM dwd_index_bar b JOIN dwd_market_index m USING(index_id)
            WHERE m.index_code='000300.SH' AND b.interval_size='1d' AND b.currency='CNY'
            AND CAST(b.observation_start AS DATE) BETWEEN DATE '2021-06-02' AND DATE '2021-12-31'
            ORDER BY market_date""",
        )
        if [r["market_date"] for r in benchmark] != [
            d for d in native.dates if str(d) >= "2021-06-02"
        ]:
            raise ValueError("R10_BENCHMARK_CALENDAR_BINDING")
        if not all(
            isinstance(r["close"], Decimal) and r["close"].is_finite() and r["close"] > 0
            for r in benchmark
        ):
            raise ValueError("R10_BENCHMARK_PRICE")
        benchmark_return = benchmark[-1]["close"] / benchmark[0]["close"] - 1
        write(
            output / "benchmark.private.json",
            {
                "rows": benchmark,
                "native_source_digest": native.content_digest,
                "benchmark": c["benchmark"],
            },
        )
        with ControlledReplay(
            project, native, qualification / "E.private.json", fit / "predictions.sealed.json"
        ) as runtime:
            for name, intervention in (("C0", None), ("C1", "r10-v0"), ("C2", "r10-v1")):
                active_arm = name
                if datetime.now(timezone.utc) >= datetime.fromisoformat(admission["C_deadline"]):
                    raise ValueError("R10_C_DEADLINE")
                arm = output / name
                arm.mkdir()
                runtime.session.reader.store.arm = name
                evidence, artifacts = runtime.run_variant(intervention, arm, allocation_audit=True)
                for filename, raw in artifacts.items():
                    with (arm / filename).open("xb") as stream:
                        stream.write(raw)
                events, decisions, equity = (
                    read(arm / f"{n}.json") for n in ("engine-events", "decisions", "equity")
                )
                entry = [r for r in decisions if r["surface"] == "entry"]
                monthly = {}
                previous = Decimal(equity[0]["system_equity"])
                by_month = {}
                for r in equity[1:]:
                    by_month[r["market_date"][:7]] = Decimal(r["system_equity"])
                for month, value in sorted(by_month.items()):
                    monthly[month] = value / previous - 1
                    previous = value
                trades = trade_summary(events, native.identities)
                audit = read(arm / "entry-allocation-audit.json")["frames"]
                write(arm / "roundtrips.private.json", trades)
                results[name] = {
                    "net_return": evidence.metrics["capital_constrained_net_total_return_delta"],
                    "maximum_drawdown": evidence.metrics["maximum_drawdown_delta"],
                    "fees": evidence.metrics["fee_delta"],
                    "turnover": evidence.metrics["turnover_delta"],
                    "average_idle_capital_ratio": 1
                    - evidence.metrics["average_invested_capital_delta"],
                    "time_in_market": evidence.metrics["time_in_market_delta"],
                    "ending_equity": equity[-1]["system_equity"],
                    "monthly_returns": monthly,
                    "entry_candidates": len(entry),
                    "legal_trial_entries": sum(r["final_action"] == "trial_entry" for r in entry),
                    "reserved_entries": sum(
                        len(r["allocation"]["new_reservations"]) for r in audit
                    ),
                    "filled_entries": sum(r["filled"] for r in entry),
                    "buy_orders": sum(
                        e["event_type"] == "order_created" and e["payload"]["side"] == "buy"
                        for e in events
                    ),
                    "sell_orders": sum(
                        e["event_type"] == "order_created" and e["payload"]["side"] == "sell"
                        for e in events
                    ),
                    "buy_fills": sum(
                        e["event_type"] == "fill" and e["payload"]["side"] == "buy" for e in events
                    ),
                    "sell_fills": sum(
                        e["event_type"] == "fill" and e["payload"]["side"] == "sell" for e in events
                    ),
                    "engine_reason_counts": dict(Counter(e["reason_code"] for e in events)),
                    "entry_reason_counts": dict(
                        Counter(x for r in entry for x in r["reason_codes"])
                    ),
                    "reconciliation": read(arm / "reconciliation.json"),
                    "closed_roundtrips": trades["closed_roundtrips"],
                    "open_roundtrips": trades["open_roundtrips"],
                    "win_rate": trades["win_rate"],
                    "average_win_to_average_loss": trades["average_win_to_average_loss"],
                    "artifacts": {p.name: sha(p) for p in sorted(arm.iterdir())},
                }
                results[name]["benchmark_return"] = benchmark_return
                results[name]["excess_vs_benchmark"] = (
                    results[name]["net_return"] - benchmark_return
                )
            write(
                output / "source-accesses.private.json",
                {
                    "rows": runtime.session.reader.store.accesses,
                },
            )
        recognition = read(comparison)
        entry_actions = {
            arm: {
                r["decision_key"]: r
                for r in read(output / arm / "decisions.json")
                if r["surface"] == "entry"
            }
            for arm in ("C0", "C1", "C2")
        }
        population = {
            "entry:" + r["event"]["event_id"]: r
            for r in read(qualification / "E.private.json")["rows"]
        }
        breadth = {}
        for old in ("C0", "C1"):
            changed = [
                population[k]
                for k in population
                if entry_actions[old][k]["final_action"] != entry_actions["C2"][k]["final_action"]
            ]
            breadth[old + "_to_C2"] = {
                "events": len(changed),
                "companies": len({r["company_id"] for r in changed}),
                "signal_dates": len({r["event"]["signal_date"] for r in changed}),
            }
        economic = all(
            results["C2"]["net_return"] > results[k]["net_return"]
            and results["C2"]["maximum_drawdown"] <= results[k]["maximum_drawdown"]
            for k in ("C0", "C1")
        )
        result = {
            "status": "THREE_COMPLETE_CONTROLLED_PATHS",
            "arms": results,
            "economic_gate": economic,
            "retention_gate": fitted["retention"]["accepted"],
            "recognition_gate": recognition["recognition_gate"],
            "action_breadth": breadth,
            "source_state_available": read(qualification / "source-freeze.safe.json")[
                "E_available"
            ],
            "benchmark_sha256": sha(output / "benchmark.private.json"),
            "consumer_admission": "NOT_GRANTED_LEGACY_EXPOSURE_UNKNOWN",
            "contract_sha256": sha(contract),
            "E_sha256": sha(qualification / "E.private.json"),
            "fit_manifest_sha256": sha(fit / "manifest.json"),
            "source_policy_exception": "P10 #679: action-open valuation; original execution limits",
            "source_access_sha256": sha(output / "source-accesses.private.json"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        write(output / "comparison.safe.json", result)
        return result
    except Exception as exc:
        last_source_access = None
        if runtime is not None:
            store = runtime.session.reader.store
            last_source_access = store.last_access
            write(output / "source-accesses.partial.private.json", {"rows": store.accesses})
        write(
            output / "failure.safe.json",
            {
                "status": failure_class(exc),
                "active_arm": active_arm,
                "last_source_access": last_source_access,
                "type": type(exc).__name__,
                "reason": str(exc),
                "completed_arms": list(results),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise
    finally:
        if owned_native:
            native.close()


def audit_semantic(path):
    value = read(path)
    value.pop("audit_digest")
    for frame in value["frames"]:
        frame.pop("observed_at")
    return digest(value)


def cold(project, contract, qualification):
    """Copy raw members; rebuild features, the old parent, child and three arms."""
    from quanthus.r9_study import head_rows
    from quanthus.r10_prepare import prepare
    from quanthus.stage_learning import head2_revision as hr
    from quanthus.stage_learning import local_head as lh

    begin_phase("cold_source_and_models", 600)
    c, admission = cold_authority(contract, qualification)
    output = qualification.parent / "cold"
    output.mkdir()
    raw_root = output / "source/DWD"
    raw_root.mkdir(parents=True)
    members = read(qualification / "native/source-members.json")["members"]
    copied = []
    for m in members:
        key = "primary_dwd_root" if m["root"] == "primary" else "full_dwd_root"
        source = Path(c["source"][key]) / m["path"]
        if source.is_symlink() or not source.is_file() or source.stat().st_size != m["size_bytes"]:
            raise ValueError("R10_COLD_RAW_SOURCE_IDENTITY")
        destination = raw_root / m["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha(destination) != m["sha256"].removeprefix("sha256:"):
            raise ValueError("R10_COLD_RAW_COPY_HASH")
        copied.append(
            {"path": m["path"], "sha256": sha(destination), "bytes": destination.stat().st_size}
        )
    shutil.copy2(Path(c["source"]["full_dwd_root"]) / "manifest.json", raw_root / "manifest.json")
    write(
        output / "physical-copy.safe.json",
        {
            "members": copied,
            "source_manifest_sha256": sha(raw_root / "manifest.json"),
            "primary_features_or_models_or_capital_used_for_construction": False,
        },
    )
    q = output / "qualification"
    prepare(project, contract, q, physical_dwd_root=raw_root, deadline=admission["C_deadline"])
    native = NativeInputs(project, contract, q / "native", physical_dwd_root=raw_root)
    try:
        old = load_bound(c, c["old_assets"]["selected"])["rows"]
        old_rows = native.qualify_cases([r["identity"] for r in old])
        train_labels = load_bound(c, c["old_assets"]["train_reference"])["resolved"]
        original = Path(c["old_assets"]["root"])
        publication = original / "publication-manifest.json"
        if sha(publication) != "d54cafeb0a8365e85e1d7a9a8ff6fa85c5071610d119c4bf8949fe51172b35c7":
            raise ValueError("R10_COLD_ORIGINAL_PROVENANCE")
        original_manifest = original / "fit/manifest.json"
        member = next(
            r for r in read(publication)["files"] if r["path"] == "study/fit/manifest.json"
        )
        if sha(original_manifest) != member["sha256"]:
            raise ValueError("R10_COLD_ORIGINAL_BINDINGS")
        bindings = lh.HeadBindings(**read(original_manifest)["bindings"])
        train = head_rows([r for r in old_rows if r["identity"]["role"] == "train"], train_labels)
        v0 = lh.fit_head(train, policy=hr.revision_policy(), bindings=bindings)
        if hashlib.sha256(lh.dump_checkpoint(v0)).hexdigest() != c["old_assets"]["parent_sha256"]:
            raise ValueError("R10_COLD_PARENT_RECONSTRUCTION")
        screen(
            project,
            contract,
            q,
            native=native,
            parent_checkpoint=v0,
            deadline=admission["C_deadline"],
        )
        ref = qualification.parent / "reference"
        fit_correction(
            contract, q, reference_root=ref, parent_checkpoint=v0, old_rows_override=old_rows
        )
        compare(contract, q, reference_root=ref)
        capital(project, contract, q, native=native)
    finally:
        native.close()
    # Open primary result files only after the independent paths are sealed.
    admitted(contract, qualification)
    checks = {}
    source_freeze = read(qualification / "source-freeze.safe.json")
    for name, key in (
        ("selected.private.json", "selection_sha256"),
        ("E.private.json", "E_sha256"),
        ("E-cases.private.json", "E_cases_sha256"),
    ):
        checks["qualification/" + name] = sha(q / name) == source_freeze[key]
    for name in (
        "v0.json",
        "v1.json",
        "checkpoint-17.json",
        "steps.private.json",
        "retention-choice.json",
        "predictions.sealed.json",
        "original-restoration.safe.json",
    ):
        checks["fit/" + name] = sha(output / "fit" / name) == sha(
            qualification.parent / "fit" / name
        )
    core = (
        "decisions.json",
        "engine-events.json",
        "equity.json",
        "daily-returns.json",
        "final-ledgers.json",
        "reconciliation.json",
        "manifest.json",
        "roundtrips.private.json",
    )
    for arm in ("C0", "C1", "C2"):
        for name in core:
            checks[f"capital/{arm}/{name}"] = sha(output / "capital" / arm / name) == sha(
                qualification.parent / "capital" / arm / name
            )
        checks[f"capital/{arm}/allocation-audit-semantic"] = audit_semantic(
            output / "capital" / arm / "entry-allocation-audit.json"
        ) == audit_semantic(qualification.parent / "capital" / arm / "entry-allocation-audit.json")
    result = {
        "status": "INDEPENDENT_COLD_MATCH" if all(checks.values()) else "COLD_DIVERGENCE",
        "checks": checks,
        "raw_physical_copy_members": len(copied),
        "old_parent_refit_steps": 200,
        "scheduled_correction_steps": 100,
        "new_reference_calls": 0,
        "primary_results_opened_after_cold_construction": True,
        "allocation_audit_comparison": "all fields except observed_at and derived audit_digest",
        "consumer_admission": "NOT_GRANTED_LEGACY_EXPOSURE_UNKNOWN",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    write(output / "verification.safe.json", result)
    if not all(checks.values()):
        raise ValueError("R10_COLD_DIVERGENCE")
    return result


def feedback(contract, qualification):
    admitted(contract, qualification)
    job = qualification.parent
    read(job / "capital/comparison.safe.json")
    records = {r["event"]["event_id"]: r for r in read(qualification / "E.private.json")["rows"]}
    cases = {r["event_id"]: r for r in read(qualification / "E-cases.private.json")["rows"]}
    scores = {
        r["event_id"]: r
        for r in read(job / "fit/predictions.sealed.json")["rows"]
        if r["event_id"] is not None
    }
    actions = {}
    for arm in ("C0", "C1", "C2"):
        actions[arm] = {
            r["decision_key"].removeprefix("entry:"): r
            for r in read(job / "capital" / arm / "decisions.json")
            if r["surface"] == "entry"
        }
    groups = {
        "changed_decision": [
            k
            for k in sorted(records)
            if actions["C1"][k]["final_action"] != actions["C2"][k]["final_action"]
        ],
        "original_guard_blocked": [
            k for k in sorted(records) if actions["C0"][k]["final_action"] == "no_action"
        ],
        "state_unavailable": [
            k
            for k in sorted(records)
            if actions["C0"][k]["final_action"] == "trial_entry"
            and any(
                actions[arm][k]["final_action"] == "no_action"
                and "R10_CONFIRM_UNAVAILABLE" in actions[arm][k]["reason_codes"]
                for arm in ("C1", "C2")
            )
        ],
    }
    output = []
    for kind, keys in groups.items():
        if not keys:
            output.append({"kind": kind, "status": "ABSENT_IN_ACTUAL_FROZEN_RUN", "count": 0})
            continue
        key = keys[0]
        output.append(
            {
                "kind": kind,
                "count": len(keys),
                "status": "ACTUAL_TRACE",
                "event_id": key,
                "company_id": records[key]["company_id"],
                "listing_id": records[key]["listing_id"],
                "signal_date": records[key]["event"]["signal_date"],
                "scores": scores[key],
                "source_available": cases[key]["available"],
                "source_reason": cases[key]["reason"],
                "actions": {arm: actions[arm][key] for arm in actions},
                "question": {
                    "changed_decision": "Which governed action changed after the update?",
                    "original_guard_blocked": "Did state confirmation preserve the original block?",
                    "state_unavailable": "How did missing state evidence affect entry?",
                }[kind],
                "decision": {
                    "changed_decision": "Keep the trace and use the sealed version gates.",
                    "original_guard_blocked": "Preserve the original expert and guardian block.",
                    "state_unavailable": "Block new confirmation entry; retain the source reason.",
                }[kind],
            }
        )
    result = {
        "cases": output,
        "manual_stitch_seconds": None,
        "manual_stitch_accounting": "not separately instrumented; agent assembly from frozen keys",
        "next_action": "Use the sealed gates to choose the next bounded action.",
        "consumer_admission": "NOT_GRANTED_LEGACY_EXPOSURE_UNKNOWN",
    }
    write(job / "feedback.safe.json", result)
    return result


def cold_authority(contract, qualification):
    # Authority and resource metadata only; never hash/open primary feature/model bytes here.
    c = read(contract)
    check_contract(c)
    admission = read(qualification / "A-admission.safe.json")
    if not admission["result_execution_ready"] or admission["contract_sha256"] != sha(contract):
        raise ValueError("R10_COLD_AUTHORITY")
    return c, admission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["capital", "cold", "feedback"])
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    args = parser.parse_args()
    c, admission = (
        cold_authority(args.contract, args.qualification)
        if args.command == "cold"
        else admitted(args.contract, args.qualification)
    )
    capital_deadline = None
    if args.command == "capital":
        job = args.qualification.parent
        window = job / "capital-window.safe.json"
        if not window.exists():
            attempts = [read(p)["started_at"] for p in job.glob("capital*/attempt.json")]
            first = (
                min(datetime.fromisoformat(v) for v in attempts)
                if attempts
                else datetime.now(timezone.utc)
            )
            write(
                window,
                {
                    "started_at": first.isoformat(),
                    "deadline": (first + timedelta(seconds=1800)).isoformat(),
                    "scope": "all primary capital attempts; no clock reset on repair",
                },
            )
        capital_deadline = datetime.fromisoformat(read(window)["deadline"])
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[: c["runtime"]["cpu_cores"]])
    done = threading.Event()

    def monitor():
        from quanthus.r9_calibration import directory_bytes, process_tree_rss

        while not done.wait(0.5):
            reason = None
            if datetime.now(timezone.utc) >= datetime.fromisoformat(admission["C_deadline"]):
                reason = "R10_C_DEADLINE"
            elif capital_deadline is not None and datetime.now(timezone.utc) >= capital_deadline:
                reason = "R10_CUMULATIVE_PRIMARY_CAPITAL_WINDOW"
            elif time.monotonic() >= PHASE["deadline"]:
                reason = "R10_PHASE_COMPUTE_LIMIT:" + PHASE["name"]
            elif process_tree_rss(os.getpid()) > c["runtime"]["rss_bytes"]:
                reason = "R10_RSS_LIMIT"
            elif directory_bytes(args.qualification.parent) > c["runtime"]["job_new_bytes"]:
                reason = "R10_JOB_WRITE_LIMIT"
            elif (
                sum(
                    p.stat().st_size
                    for p in args.qualification.parent.rglob("*")
                    if p.is_file()
                    and not {"source", "native", "repo"}.intersection(
                        p.relative_to(args.qualification.parent).parts
                    )
                    and p.suffix in {".log", ".json", ".jsonl", ".txt"}
                )
                > c["runtime"]["log_bytes"]
            ):
                reason = "R10_EVIDENCE_LIMIT"
            if reason:
                write(
                    args.qualification.parent / (args.command + "-runtime-stop.safe.json"),
                    {"reason": reason},
                )
                os._exit(70)

    threading.Thread(target=monitor, daemon=True).start()
    project = Path(__file__).resolve().parents[2]
    result = (
        capital(project, args.contract, args.qualification)
        if args.command == "capital"
        else cold(project, args.contract, args.qualification)
        if args.command == "cold"
        else feedback(args.contract, args.qualification)
    )
    done.set()
    print(
        __import__("json").dumps(
            {"status": result.get("status", "FEEDBACK_RECORDED")}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
