#!/usr/bin/env python3
"""Materialize the approved fixed87 Facts/reference packet, without scoring.

All paths are supplied by the private job spec. No source recovery or networking.
The later reference gate and fit consume this one immutable job-local TMP.
"""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import socket
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_strategy.held_structure_inputs import historical_daily_inputs
from quanthus.expert_strategy.held_structure_integrity import execution_pins, file_sha
from quanthus.stage_learning.entry_baseline import recover_baseline
from quanthus.stage_learning.entry_confirmation import training_support
from quanthus.stage_learning.entry_consolidation import pre_entry_window
from quanthus.stage_learning.head2_facts import build_head2_window_facts
from quanthus.stage_learning.head2_revision import encode_reference_facts
from quanthus.structure_v2_inputs import InputPolicy, admit_daily, canonical, digest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "config/research/learning-mvp/entry-march-confirmation.v1.json"


def read(path: str | Path) -> Any:
    return json.loads(Path(path).read_bytes())


def write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def verify(pins: dict[str, Any]) -> None:
    for name, pin in pins.items():
        path = Path(name)
        if (
            path.is_symlink()
            or path.stat().st_size != pin["bytes"]
            or file_sha(path) != (pin["sha256"].removeprefix("sha256:"))
        ):
            raise ValueError("ENTRY_SOURCE_CHANGED:" + name)


def guard(job: Path) -> None:
    budget = read(job / "evidence/budget.frozen.json")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(budget["deadline_UTC"]):
        raise ValueError("ENTRY_DEADLINE")
    for path, key in (
        (job, "maximum_data_job_logical_bytes"),
        (Path(budget["control"]), "maximum_system_job_logical_bytes"),
    ):
        if shutil.disk_usage(path).free < budget["minimum_available_bytes_per_written_device"]:
            raise ValueError("ENTRY_DISK_FLOOR")
        if sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) > budget[key]:
            raise ValueError("ENTRY_WRITE_LIMIT")
    if (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        > budget["maximum_process_RSS_bytes"]
    ):
        raise ValueError("ENTRY_RSS_LIMIT")


def code_pins() -> dict[str, str]:
    return {**execution_pins(ROOT), "scripts/entry_march_confirmation.py": file_sha(Path(__file__))}


def freeze(job: Path, spec_path: Path) -> dict[str, Any]:
    guard(job)
    spec = read(spec_path)
    verify(spec["pins"])
    source = read(spec["source_plan"])
    verify(source["source_pins"])
    for key, identity in (("training", "training_identity"), ("evaluation", "evaluation_identity")):
        if file_sha(Path(spec[identity])) != read(ROOT / CONTRACT)[key]["identity_sha256"]:
            raise ValueError("ENTRY_PROPOSAL_CHANGED")
    pins = {**spec["pins"], **source["source_pins"]}
    for path in (spec_path, job / "evidence/budget.frozen.json"):
        pins[str(path)] = {"bytes": path.stat().st_size, "sha256": file_sha(path)}
    frozen = {
        "kind": "entry-march-confirmation-inputs-v1",
        "spec": spec,
        "source": source,
        "pins": pins,
        "code": code_pins(),
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "purpose_digest": digest(read(ROOT / CONTRACT)),
        "UTC": datetime.now(timezone.utc).isoformat(),
    }
    write(job / "inputs.frozen.private.json", frozen)
    return {
        "frozen_sha256": file_sha(job / "inputs.frozen.private.json"),
        "source_members": len(source["source_pins"]),
        "scores_or_new_fits": 0,
    }


def qualify(job: Path) -> dict[str, Any]:
    import duckdb
    import pyarrow.parquet as pq

    started = time.monotonic()
    guard(job)
    frozen = read(job / "inputs.frozen.private.json")
    if frozen["code"] != code_pins():
        raise ValueError("ENTRY_CODE_CHANGED")
    verify(frozen["pins"])
    source, spec = frozen["source"], frozen["spec"]
    contract = read(ROOT / CONTRACT)
    proposal = read(spec["evaluation_identity"])["rows"]
    train_ids = read(spec["training_identity"])["rows"]
    wanted = {r["sample_id"] for r in train_ids}
    training = [r for r in read(spec["training_facts"])["rows"] if r["sample_id"] in wanted]
    train_receipt = training_support(training, train_ids, cutoff=contract["training"]["cutoff"])
    if len(proposal) != 87 or len({(r["code"], r["signal_date"]) for r in proposal}) != 87:
        raise ValueError("ENTRY_EXACT87_REQUIRED")
    if len({r["company_id"] for r in proposal}) != 66 or (
        {r["company_id"] for r in proposal} & {r["company_id"] for r in training}
    ):
        raise ValueError("ENTRY_COMPANY_DISJOINTNESS")
    runtime = read(spec["runtime_request"])
    original = recover_baseline(
        read(spec["preparation"]),
        read(spec["decisions"]),
        load_candidate_rule_card(
            ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
        ),
        runtime_snapshot_id="p3-restructuring-disposition-successor-v1/" + digest(runtime)[7:],
    )
    original_map = {(e.security_code, e.signal_date.isoformat()): e for e in original}
    cal: dict[str, list[date]] = defaultdict(list)
    for row in pq.read_table(source["selected"]["trading_calendar"]).to_pylist():
        if row["is_open"] == 1 and date(2012, 1, 1) <= row["trade_date"] <= date(2021, 12, 31):
            cal[row["exchange"]].append(row["trade_date"])
    for sessions in cal.values():
        sessions.sort()
        if len(sessions) != len(set(sessions)):
            raise ValueError("ENTRY_CALENDAR_DUPLICATE")
    validity = {
        r["stock_code"]: r
        for r in pq.read_table(source["selected"]["security_code_validity"]).to_pylist()
    }
    codes = sorted({r["code"] for r in proposal})
    boundaries: dict[str, dict[date, list[str]]] = defaultdict(lambda: defaultdict(list))
    future_actions = []
    for row in pq.read_table(source["selected"]["corporate_actions_normalized"]).to_pylist():
        if row["security_code"] not in codes or row["effective_date"] is None:
            continue
        boundaries[row["security_code"]][row["effective_date"]].append(
            row["action_id"] + ":" + row["evidence_digest"]
        )
        if date(2021, 3, 31) < row["effective_date"] <= date(2021, 12, 31):
            future_actions.append(row)
    for path in source["supplemental"]:
        raw = read(path)
        events = raw.get("events", [])
        if "rights-event-source-facts.json" in path:
            events = [{"security_code": raw["security_code"], "facts": raw["facts"]}]
        if raw.get("terms"):
            events = [{"security_code": raw["terms"]["security_id"], "facts": raw["terms"]}]
        for event in events:
            for field in (
                "ex_date",
                "ex_right_date",
                "resume_unbounded_date",
                "new_share_listing_date",
            ):
                value = event["facts"].get(field)
                value = value.get("value") if isinstance(value, dict) else value
                if isinstance(value, str):
                    boundaries[event["security_code"]][date.fromisoformat(value)].append(
                        source["source_pins"][path]["sha256"] + ":" + field
                    )
    blocked: dict[str, list[tuple[date, date, str]]] = defaultdict(list)
    for row in pq.read_table(source["selected"]["corporate_action_exceptions"]).to_pylist():
        blocked[row["security_code"]].append(
            (
                row["blocked_from"] or date(2012, 1, 1),
                row["blocked_through"] or date(2021, 12, 31),
                row["exception_id"],
            )
        )
    action_digest = digest(
        {
            p: v
            for p, v in source["source_pins"].items()
            if p
            not in [
                source["selected"][name]
                for name in ("daily_ohlcv_none", "security_code_validity", "trading_calendar")
            ]
        }
    )
    windows = {
        (r["code"], r["signal_date"]): pre_entry_window(
            cal[validity[r["code"]]["exchange"]], date.fromisoformat(r["signal_date"])
        )
        for r in proposal
    }
    # Each code is admitted once over the union of its fixed windows.
    ranges = {
        code: (
            min(windows[(r["code"], r["signal_date"])][0] for r in proposal if r["code"] == code),
            max(windows[(r["code"], r["signal_date"])][-1] for r in proposal if r["code"] == code),
        )
        for code in codes
    }
    policy = InputPolicy(
        frozen["purpose_digest"], tuple(codes), digest(source["selected"]["trading_calendar"])
    )
    facts_rows, packets, identities = [], [], []
    raw_rows = 0
    with duckdb.connect(
        ":memory:",
        config={
            "threads": 1,
            "memory_limit": "512MB",
            "temp_directory": str(job / "tmp/duckdb"),
            "max_temp_directory_size": "64MB",
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        },
    ) as con:
        first = min(v[0] for v in ranges.values())
        last = max(v[1] for v in ranges.values())
        marks = ",".join("?" for _ in codes)
        query = (
            "SELECT stock_code,security_id,identity_exchange,adj_mode,date,"
            "high,low,close,volume,built_at "
            "FROM read_parquet(?) WHERE date>=? AND date<=? AND stock_code IN (" + marks + ")"
        )
        params = [source["selected"]["daily_ohlcv_none"], first, last, *codes]
        write(
            job / "evidence/projection.safe.json",
            {
                "codes": len(codes),
                "first": first,
                "last": last,
                "plan": con.execute("EXPLAIN " + query, params).fetchall(),
            },
        )
        con.execute("CREATE TEMP TABLE selected_raw AS " + query, params)
        for code in codes:
            guard(job)
            start, end = ranges[code]
            sessions = cal[validity[code]["exchange"]]
            bars = (
                con.execute(
                    "SELECT * FROM selected_raw WHERE stock_code=? "
                    "AND date>=? AND date<=? ORDER BY date",
                    [code, start, end],
                )
                .fetch_arrow_table()
                .to_pylist()
            )
            raw_rows += len(bars)
            inputs = historical_daily_inputs(
                code=code,
                validity=validity[code],
                bars=bars,
                sessions=[d for d in sessions if start <= d <= end],
                through=end,
                source_cutoff=datetime.fromisoformat(source["source_cutoff"]),
                raw_member_sha256=source["source_pins"][source["selected"]["daily_ohlcv_none"]][
                    "sha256"
                ],
                calendar_member_sha256=source["source_pins"][
                    source["selected"]["trading_calendar"]
                ]["sha256"],
                action_closure_sha256=action_digest,
                action_boundaries={d: tuple(sorted(v)) for d, v in boundaries[code].items()},
                blocked_intervals=tuple(blocked[code]),
            )
            admissions = {r.market_date: admit_daily(r, policy) for r in inputs}
            for row in proposal:
                if row["code"] != code:
                    continue
                window = windows[(code, row["signal_date"])]
                event = original_map[(code, row["signal_date"])]
                index = sessions.index(date.fromisoformat(row["signal_date"]))
                if (
                    row["anchor"] != window[-1].isoformat()
                    or row["window_start"] != window[0].isoformat()
                    or row["execution_session"] != sessions[index + 1].isoformat()
                    or (row["original_B_action"] == "trial_entry") != event.baseline_allowed
                ):
                    raise ValueError("ENTRY_FROZEN_COORDINATE_CHANGED")
                current = tuple(admissions[d] for d in window)
                facts = build_head2_window_facts(current, anchor=window[-1])
                features, reason = None, facts.reason
                primary, path = facts.reference_facts()
                if facts.available:
                    encoded = encode_reference_facts(
                        primary,
                        path,
                        anchor=row["anchor"],
                        source_digest=facts.source_closure_digest,
                    )
                    features, reason = encoded.values, encoded.reason
                    if features is not None and features[6] not in train_receipt["mask_support"]:
                        features, reason = None, "unsupported_training_mask"
                blind_id = (
                    "Q"
                    + digest(
                        {"frozen_opportunity": asdict(event), "purpose": frozen["purpose_digest"]}
                    )[7:19]
                )
                packet: dict[str, Any] = {
                    "blind_id": blind_id,
                    "available": features is not None,
                    "reason": reason,
                }
                if features is not None:
                    base = current[0].row.close
                    assert base is not None and base > 0
                    packet.update(
                        {
                            "facts": {**primary, **path},
                            "normalized_HLC40": [
                                [
                                    float((v / base * Decimal(100)).quantize(Decimal("0.0001")))
                                    for v in (a.row.high, a.row.low, a.row.close)
                                ]
                                for a in current
                            ],
                        }
                    )
                packets.append(packet)
                identities.append(
                    {
                        "blind_id": blind_id,
                        "company_id": row["company_id"],
                        "time_block": row["signal_date"][:7],
                        "available": features is not None,
                    }
                )
                facts_rows.append(
                    {
                        "blind_id": blind_id,
                        "proposal": row,
                        "event": asdict(event),
                        "facts": facts.semantic(),
                        "facts_digest": facts.digest,
                        "features": features,
                        "feature_reason": reason,
                    }
                )
    if len(facts_rows) != 87:
        raise ValueError("ENTRY_DENOMINATOR_LOST")
    # Blind order does not disclose chronological sequence, issuer, B or model output.
    packets.sort(key=lambda r: r["blind_id"])
    write(job / "private/training20.json", {"rows": training})
    write(job / "private/facts87.json", {"rows": facts_rows})
    write(job / "private/reference-identities.json", identities)
    write(
        job / "reference/packet.json",
        {"schema": "anonymous-observed-order-reference-v1", "rows": packets},
    )
    verify(frozen["pins"])
    if frozen["code"] != code_pins():
        raise ValueError("ENTRY_CODE_CHANGED")
    receipt = {
        "opportunities": 87,
        "companies": 66,
        "training": train_receipt,
        "facts_available": sum(r["features"] is not None for r in facts_rows),
        "unavailability_reasons": dict(
            Counter(r["feature_reason"] for r in facts_rows if r["features"] is None)
        ),
        "selected_2021_normalized_action_rows": len(future_actions),
        "action_semantic_capital_admission": "NOT_YET_REPLAY_ADMITTED",
        "source_files_unchanged": True,
        "source_members": len(source["source_pins"]),
        "elapsed_seconds": time.monotonic() - started,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "projected_raw_rows": raw_rows,
        "facts_materializations": 1,
        "model_scores_or_new_fits": 0,
        "source_requests": 0,
        "consumer_admission": "NOT_GRANTED",
        "facts_sha256": file_sha(job / "private/facts87.json"),
        "packet_sha256": file_sha(job / "reference/packet.json"),
    }
    guard(job)
    write(job / "evidence/qualification.safe.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "qualify"))
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args()

    def deny(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("ENTRY_NETWORK_NOT_AUTHORIZED")

    socket.create_connection = deny
    socket.socket.connect = deny  # type: ignore[method-assign]
    if args.command == "freeze":
        if args.spec is None:
            parser.error("freeze requires --spec")
        result = freeze(args.job, args.spec)
    else:
        result = qualify(args.job)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
