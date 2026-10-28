#!/usr/bin/env python3
"""Freeze and run the bounded, development-only B/R0/seven-feature entry census.

No acquisition, fitting, labels, returns, orders or capital simulation. Private
input paths are supplied in a separately frozen spec, never committed to Git.
"""

from __future__ import annotations

import argparse
import gzip
import json
import resource
import shutil
import socket
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_strategy.held_structure_inputs import historical_daily_inputs
from quanthus.expert_strategy.held_structure_integrity import execution_pins, file_sha
from quanthus.stage_learning.entry_baseline import alias_exclusion, recover_baseline
from quanthus.stage_learning.entry_consolidation import (
    EntryOpportunity,
    diagnose_entry_with_facts,
    pre_entry_window,
    summarize,
)
from quanthus.stage_learning.local_head import HeadBindings, load_checkpoint
from quanthus.stage_learning.order_rule_screen import load_rule
from quanthus.structure_v2_inputs import InputPolicy, admit_daily, canonical, digest

ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "config/research/learning-mvp/entry-consolidation-diagnostic.v1.json"


def read(path: str | Path) -> Any:
    return json.loads(Path(path).read_bytes())


def write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def guard(job: Path) -> None:
    budget = read(job / "evidence/budget.frozen.json")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(budget["deadline"]):
        raise ValueError("ENTRY_DEADLINE")
    if shutil.disk_usage(job).free < budget["minimum_free_bytes"]:
        raise ValueError("ENTRY_DISK_FLOOR")
    if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > budget["maximum_rss_bytes"]:
        raise ValueError("ENTRY_RSS_LIMIT")
    if sum(p.stat().st_size for p in job.rglob("*") if p.is_file()) > budget[
        "system_maximum_new_bytes"
    ]:
        raise ValueError("ENTRY_OUTPUT_LIMIT")


def code_pins() -> dict[str, str]:
    pins = execution_pins(ROOT)
    pins[Path(__file__).relative_to(ROOT).as_posix()] = file_sha(Path(__file__))
    return pins


def verify(pins: dict[str, dict[str, Any]]) -> None:
    for name, pin in pins.items():
        p = Path(name)
        if p.stat().st_size != pin["bytes"] or file_sha(p) != pin["sha256"].removeprefix(
            "sha256:"
        ):
            raise ValueError("ENTRY_INPUT_CHANGED:" + name)


def calendars(path: str) -> dict[str, list[date]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    output: dict[str, list[date]] = defaultdict(list)
    for row in pq.read_table(path, columns=["trade_date", "exchange", "is_open"]).to_pylist():
        if row["is_open"] == 1 and date(2012, 1, 1) <= row["trade_date"] <= date(2021, 12, 30):
            output[row["exchange"]].append(row["trade_date"])
    for values in output.values():
        if len(values) != len(set(values)):
            raise ValueError("ENTRY_CALENDAR_DUPLICATE")
        values.sort()
    return output


def freeze(job: Path, spec_path: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    guard(job)
    if (job / "inputs.frozen.private.json").exists():
        raise ValueError("ENTRY_FREEZE_EXISTS")
    spec = read(spec_path)
    verify(spec["pins"])
    source = read(spec["source_plan"])
    source_pins = source["source_pins"]
    verify(source_pins)
    model_raw = Path(spec["model"]).read_bytes()
    model_value = json.loads(model_raw)
    purpose = read(ROOT / PURPOSE)
    if file_sha(Path(spec["model"])) != purpose["controls"]["checkpoint_sha256"]:
        raise ValueError("ENTRY_CHECKPOINT_NOT_APPROVED")
    model = load_checkpoint(model_raw, expected_sha256="sha256:" + file_sha(Path(spec["model"])),
                            bindings=HeadBindings(**model_value["bindings"]))
    last_training = max(date.fromisoformat(u.anchor) for u in model.uses)
    r2 = read(spec["exposure_r2"])
    excluded_ids = set(r2["preserved_security_ids"])
    excluded_codes: set[str] = set()
    old = read(spec["old_metadata_index"])
    for pin in old["metadata_bindings"]:
        p = Path(spec["old_metadata_root"]) / pin["artifact"] / pin["name"]
        if file_sha(p) != pin["sha256"] or p.stat().st_size != pin["bytes"]:
            raise ValueError("ENTRY_OLD_EXPOSURE_METADATA")
        value = read(p)
        excluded_ids.update(value.get("evaluation_pool", {}))
        excluded_codes.update(str(v) for v in value.get("evaluation_pool", {}).values())
    # Conservative whole-issuer exclusion of all recent Head2 roles and original31.
    for path in spec["recent_selections"]:
        for row in read(path)["selected"]:
            excluded_ids.add(row["security_id"])
            excluded_codes.add(row["security_code"])
    for row in old["rows"]:
        excluded_ids.add(row["security_id"])
        excluded_codes.add(row["code"])
    identities, blocked_codes = alias_exclusion(
        pq.read_table(spec["identity_listing"],
                      columns=["security_id", "listing_code"]).to_pylist(),
        pq.read_table(spec["identity_security"], columns=["security_id", "company_id"]).to_pylist(),
        excluded_ids, excluded_codes,
    )
    runtime = read(spec["runtime_request"])
    prep = read(spec["preparation"])
    if (runtime["source_manifest_sha256"] != source["source_manifest_sha256"]
            or prep["effective_source_manifest_sha256"] != runtime["source_manifest_sha256"]
            or prep["source_information_cutoff"] != source["source_cutoff"]):
        raise ValueError("ENTRY_CONTROL_SOURCE_CLOSURE")
    snapshot = ("p3-restructuring-disposition-successor-v1/"
                + digest(runtime).removeprefix("sha256:"))
    original = recover_baseline(
        prep, read(spec["decisions"]),
        load_candidate_rule_card(
            ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"),
        runtime_snapshot_id=snapshot,
    )
    cal = calendars(source["selected"]["trading_calendar"])
    validity = {r["stock_code"]: r for r in pq.read_table(
        source["selected"]["security_code_validity"]
    ).to_pylist()}
    selection, excluded = [], []
    reasons: Counter[str] = Counter()
    for event in original:
        code = event.security_code
        if event.signal_date <= last_training:
            reason = "signal_not_after_training"
        elif code in blocked_codes:
            reason = "sealed_evaluation_or_recent_issuer_alias"
        elif code not in identities:
            reason = "missing_or_ambiguous_issuer"
        elif code not in validity:
            reason = "missing_accepted_validity"
        else:
            window = pre_entry_window(cal[validity[code]["exchange"]], event.signal_date)
            reason = "anchor_not_after_training" if window[-1] <= last_training else "INCLUDE"
            if reason == "INCLUDE" and window[0] < date.fromisoformat(validity[code]["valid_from"]):
                reason = "insufficient_listing_history"
        reasons[reason] += 1
        if reason == "INCLUDE":
            selection.append(asdict(replace(event, issuer_id=identities[code])))
        else:
            excluded.append({"event_id": event.event_id, "reason": reason})
    write(job / "selection.frozen.private.json", selection)
    write(job / "exclusions.frozen.private.json", excluded)
    combined_pins = {**spec["pins"], **source_pins}
    for path in (spec_path, job / "selection.frozen.private.json",
                 job / "exclusions.frozen.private.json", job / "evidence/budget.frozen.json"):
        p = Path(path)
        combined_pins[str(p)] = {"bytes": p.stat().st_size, "sha256": file_sha(p)}
    frozen = {"kind": "entry-consolidation-development-inputs-v1", "spec": spec,
              "source": source, "pins": combined_pins, "code": code_pins(),
              "model_bindings": model_value["bindings"], "model_digest": model.digest,
              "last_training_observation": last_training.isoformat(),
              "selection_counts": dict(reasons), "original_B_opportunities": len(original),
              "selected_opportunities": len(selection), "exclusion_scope": purpose["exposure"],
              "UTC": datetime.now(timezone.utc).isoformat()}
    write(job / "inputs.frozen.private.json", frozen)
    receipt = {k: frozen[k] for k in ["kind", "UTC", "selection_counts",
               "original_B_opportunities", "selected_opportunities", "last_training_observation"]}
    receipt.update({"sealed_identity_count": len(excluded_ids),
                    "blocked_alias_count": len(blocked_codes), "new_market_rows_read": 0,
                    "frozen_inputs_sha256": file_sha(job / "inputs.frozen.private.json")})
    write(job / "evidence/selection-freeze.safe.json", receipt)
    return receipt


def run(job: Path) -> dict[str, Any]:
    import duckdb
    import pyarrow.parquet as pq

    guard(job)
    started = time.monotonic()
    frozen = read(job / "inputs.frozen.private.json")
    if code_pins() != frozen["code"]:
        raise ValueError("ENTRY_CODE_DRIFT")
    for name, module in tuple(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if name.startswith("quanthus.") and isinstance(origin, str):
            Path(origin).resolve().relative_to(ROOT / "src")
    verify(frozen["pins"])
    source, spec = frozen["source"], frozen["spec"]
    selected = read(job / "selection.frozen.private.json")
    model = load_checkpoint(Path(spec["model"]).read_bytes(),
                            expected_sha256="sha256:" + file_sha(Path(spec["model"])),
                            bindings=HeadBindings(**frozen["model_bindings"]))
    rule = load_rule(ROOT)
    cal = calendars(source["selected"]["trading_calendar"])
    codes = sorted({r["security_code"] for r in selected})
    validity = {r["stock_code"]: r for r in pq.read_table(
        source["selected"]["security_code_validity"]
    ).to_pylist()}
    boundaries: dict[str, dict[date, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in pq.read_table(source["selected"]["corporate_actions_normalized"], columns=[
        "security_code", "effective_date", "action_id", "evidence_digest"
    ]).to_pylist():
        if row["security_code"] in codes and row["effective_date"] is not None:
            boundaries[row["security_code"]][row["effective_date"]].append(
                row["action_id"] + ":" + row["evidence_digest"]
            )
    for path in source["supplemental"]:
        raw = read(path)
        events = raw.get("events", [])
        if "rights-event-source-facts.json" in path:
            events = [{"security_code": raw["security_code"], "facts": raw["facts"]}]
        if raw.get("terms"):
            terms = raw["terms"]
            events = [{"security_code": terms["security_id"], "facts": terms}]
        for event in events:
            for field in ("ex_date", "ex_right_date", "resume_unbounded_date",
                          "new_share_listing_date"):
                value = event["facts"].get(field)
                value = value.get("value") if isinstance(value, dict) else value
                if isinstance(value, str):
                    boundaries[event["security_code"]][date.fromisoformat(value)].append(
                        source["source_pins"][path]["sha256"] + ":" + field
                    )
    blocked: dict[str, list[tuple[date, date, str]]] = defaultdict(list)
    for row in pq.read_table(source["selected"]["corporate_action_exceptions"]).to_pylist():
        blocked[row["security_code"]].append((row["blocked_from"] or date(2012, 1, 1),
                   row["blocked_through"] or date(2021, 12, 30), row["exception_id"]))
    action_digest = digest({p: v for p, v in source["source_pins"].items()
                            if p not in [source["selected"][name] for name in (
                                "daily_ohlcv_none", "security_code_validity", "trading_calendar")]})
    windows = {r["event_id"]: pre_entry_window(
        cal[validity[r["security_code"]]["exchange"]], date.fromisoformat(r["signal_date"])
    ) for r in selected}
    ranges = {code: (min(windows[r["event_id"]][0] for r in selected
                         if r["security_code"] == code),
                     max(windows[r["event_id"]][-1] for r in selected
                         if r["security_code"] == code)) for code in codes}
    policy = InputPolicy(digest(read(ROOT / PURPOSE)), tuple(codes) or ("NO_OPPORTUNITIES",),
                         digest({"calendar": source["selected"]["trading_calendar"]}))
    results = []
    raw_rows = 0
    with duckdb.connect(":memory:", config={"threads": 1, "memory_limit": "512MB",
            "temp_directory": str(job / "tmp/duckdb"), "max_temp_directory_size": "64MB",
            "enable_external_access": True, "autoinstall_known_extensions": False,
            "autoload_known_extensions": False}) as connection, gzip.open(
                job / "facts-and-decisions.private.jsonl.gz", "xb"
            ) as output:
        if codes:
            first = min(start for start, _ in ranges.values())
            last = max(end for _, end in ranges.values())
            marks = ",".join("?" for _ in codes)
            query = (
                "SELECT stock_code,security_id,identity_exchange,adj_mode,date,high,low,"
                "close,volume,built_at FROM read_parquet(?) WHERE date>=? AND date<=? "
                "AND stock_code IN (" + marks + ")"
            )
            parameters = [source["selected"]["daily_ohlcv_none"], first, last, *codes]
            plan = connection.execute("EXPLAIN " + query, parameters).fetchall()
            write(job / "evidence/projection-plan.safe.json", {
                "query": query, "codes": len(codes), "first": first, "last": last,
                "plan": plan, "source_identity": source["source_pins"][
                    source["selected"]["daily_ohlcv_none"]],
            })
            connection.execute("CREATE TEMP TABLE selected_raw AS " + query, parameters)
        for code in codes:
            guard(job)
            start, end = ranges[code]
            sessions = cal[validity[code]["exchange"]]
            bounded_sessions = [d for d in sessions if start <= d <= end]
            bars = connection.execute(
                "SELECT * FROM selected_raw WHERE stock_code=? AND date>=? AND date<=? "
                "ORDER BY date", [code, start, end]
            ).fetch_arrow_table().to_pylist()
            raw_rows += len(bars)
            inputs = historical_daily_inputs(
                code=code, validity=validity[code], bars=bars, sessions=bounded_sessions,
                through=end, source_cutoff=datetime.fromisoformat(source["source_cutoff"]),
                raw_member_sha256=source["source_pins"][source["selected"]["daily_ohlcv_none"]][
                    "sha256"], calendar_member_sha256=source["source_pins"][
                        source["selected"]["trading_calendar"]]["sha256"],
                action_closure_sha256=action_digest,
                action_boundaries={d: tuple(sorted(v)) for d, v in boundaries[code].items()},
                blocked_intervals=tuple(blocked[code]),
            )
            admissions = {row.market_date: admit_daily(row, policy) for row in inputs}
            for row in selected:
                if row["security_code"] != code:
                    continue
                event = EntryOpportunity(**{**row, "signal_date": date.fromisoformat(
                    row["signal_date"])})
                result, facts = diagnose_entry_with_facts(
                    event, sessions=sessions,
                    admissions=tuple(admissions[d] for d in windows[event.event_id]),
                    checkpoint=model, rule=rule,
                )
                output.write(canonical({"facts": facts.semantic(), "decision": asdict(result)})
                             + b"\n")
                results.append(result)
    verify(frozen["pins"])
    if code_pins() != frozen["code"]:
        raise ValueError("ENTRY_CODE_DRIFT")
    summary = summarize(results)
    summary.update({"input_identity": digest(frozen), "source_files_unchanged": True,
                    "new_collection_requests": 0, "new_fits_or_updates": 0,
                    "future_return_or_economic_files_requested": 0,
                    "shared_facts_materializations": len(results), "facts_consumers": 2,
                    "projected_raw_rows": raw_rows, "codes": len(codes),
                    "elapsed_seconds": time.monotonic() - started,
                    "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                    "facts_file_sha256": file_sha(job / "facts-and-decisions.private.jsonl.gz")})
    guard(job)
    write(job / "evidence/entry-comparison.safe.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run"])
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args()
    # Refuse networking in this process; no external data query exists in either stage.
    def deny(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("ENTRY_NETWORK_NOT_AUTHORIZED")
    socket.create_connection = deny
    socket.socket.connect = deny  # type: ignore[method-assign]
    if args.command == "freeze":
        if args.spec is None:
            parser.error("freeze requires --spec")
        result = freeze(args.job, args.spec)
    else:
        result = run(args.job)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
