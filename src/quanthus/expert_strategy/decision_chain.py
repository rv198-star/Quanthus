"""Read-only original-B entry/order/held/exit chain; no evaluator or replay.

The builder authenticates a frozen selection and reuses the sealed held review.
Cold readers need only this package and ordinary code. Archive paths stay inert.
"""

from __future__ import annotations

import argparse
import bisect
import html
import json
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.expert_strategy.decision_review import (
    _code_identity,
    _json,
    _physical,
    _stat,
    _write,
    encoded,
    sha,
)
from quanthus.expert_strategy.decision_review import (
    read_package as read_held_package,
)

CONTRACT = "config/research/decision-chain-delivery.v1.json"
SCHEMA = "quanthus.recorded-B-decision-chain.v1"
PURPOSE = "RECORDED_B_DECISION_CHAIN_REVIEW_ONLY"
MAX_BYTES = 64 * 1024**2
ROLES = {
    "decisions",
    "events",
    "ledgers",
    "coordinates",
    "original_manifest",
    "reconciliation",
    "completion",
    "freeze",
    "trace_receipt",
    "review_manifest",
    "review_rows",
}


class DecisionChainError(ValueError):
    """The retained identity or recorded linkage cannot support this view."""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise DecisionChainError(reason)


def business_day(value: str) -> str:
    instant = datetime.fromisoformat(value)
    require(instant.tzinfo is not None, "CHAIN_NAIVE_TIME")
    return instant.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def load_contract(root: Path) -> dict[str, Any]:
    contract: dict[str, Any] = _json((root / CONTRACT).read_bytes())
    require(
        contract["schema_version"] == "quanthus.B-chain-contract.v1"
        and contract["purpose"] == PURPOSE
        and contract["first_date"] == "2012-01-01"
        and contract["last_date"] == "2021-12-31"
        and contract["historical_PIT_qualified"] is False
        and contract["execution_authorized"] is False
        and contract["consumer_admission"] == "RESEARCH_DIAGNOSTIC_ONLY"
        and set(contract["roles"]) == ROLES,
        "CHAIN_UNSUPPORTED_CONTRACT",
    )
    return contract


def decode(roles: Mapping[str, bytes]) -> dict[str, Any]:
    return {
        k: ([_json(line) for line in v.splitlines()] if k == "review_rows" else _json(v))
        for k, v in roles.items()
    }


def provenance(data: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    """Offline source closure: validate bodies, never open their archived paths."""
    original, trace, review = (
        data[k] for k in ("original_manifest", "trace_receipt", "review_manifest")
    )
    require(
        original["variant_id"] == "variant.B"
        and original["source_manifest_sha256"] == contract["source_manifest_sha256"]
        and original["production_authorized"] is False
        and original["scope"]
        == {
            "decision_start": "2012-01-04",
            "decision_end": "2021-12-30",
            "execution_end": "2021-12-31",
        },
        "CHAIN_ORIGINAL_B_IDENTITY",
    )
    require(
        trace["kind"] == "expert545-unchanged-B-trace-v1"
        and trace["source_freeze_sha256"] == contract["roles"]["freeze"]["sha256"]
        and trace["new_scientific_result"] is False,
        "CHAIN_TRACE_IDENTITY",
    )
    for role, name in (
        ("decisions", "decisions"),
        ("events", "engine-events"),
        ("original_manifest", "manifest"),
    ):
        check = trace["comparisons"][name + ".json"]
        require(
            check["before_sha256"] == check["after_sha256"] == contract["roles"][role]["sha256"]
            and check["equal"] is True,
            "CHAIN_TRACE_BODY_BINDING",
        )
    require(
        review["scope"] == {k: contract[k] for k in ("first_date", "last_date", "source_cutoff")}
        and review["execution_authorized"] is False
        and review["historical_PIT_qualified"] is False
        and review["purpose"] == "CONSUMED_RETROSPECTIVE_DECISION_REVIEW_ONLY"
        and review["retained_producer"]["source_manifest_sha256"]
        == contract["source_manifest_sha256"]
        and review["files"]["reviews.jsonl"] == contract["roles"]["review_rows"],
        "CHAIN_RETAINED_REVIEW_BINDING",
    )
    retained = review["members"]
    require(
        any(
            m["archive_path"].endswith("/trace/receipt.safe.json")
            and m["sha256"] == contract["roles"]["trace_receipt"]["sha256"]
            for m in retained
        )
        and any(
            m["archive_path"].endswith("/trace/held-contexts.private.jsonl.gz")
            and m["sha256"] == trace["trace_sha256"]
            for m in retained
        ),
        "CHAIN_RETAINED_TRACE_CLOSURE",
    )
    require(
        data["reconciliation"]["reconciled"] is True
        and data["reconciliation"]["sealed_partition_access"] is False,
        "CHAIN_ORIGINAL_RECONCILIATION",
    )


def project(data: Mapping[str, Any]) -> dict[str, Any]:
    """Build one immutable index from explicit references and actual filled intervals.

    ``allocated`` is preserved as producer consideration, not an order/reservation.
    Event sequence numbers are local to engine calls and never grouping keys.
    """
    decisions, events, reviews, coordinates = (
        data[k] for k in ("decisions", "events", "review_rows", "coordinates")
    )
    nodes, decision_node, decision_signal_key = {}, {}, defaultdict(list)
    held_keys = {}
    for i, row in enumerate(decisions):
        day, unit, surface = (row[k] for k in ("market_date", "unit_id", "surface"))
        require(
            "2012-01-01" <= day <= "2021-12-31" and date.fromisoformat(day).isoformat() == day,
            "CHAIN_DECISION_DATE",
        )
        require(
            type(row["filled"]) is bool and type(row["allocated"]) is bool, "CHAIN_DECISION_FLAGS"
        )
        require(surface in {"entry", "held"}, "CHAIN_DECISION_SURFACE")
        side = "buy" if surface == "entry" else "sell"
        decision_signal_key[day, unit, row["formal_decision_digest"], side].append(i)
        if surface == "entry":
            key = row["decision_key"]
            require(key not in nodes and key in coordinates, "CHAIN_ENTRY_IDENTITY")
            security, coordinate_day = coordinates[key].rsplit("@", 1)
            require(coordinate_day == day and bool(security), "CHAIN_ENTRY_COORDINATE")
            nodes[key] = {
                "decision": i,
                "security_code": security,
                "held": [],
                "events": [],
                "reviews": [],
                "signals": [],
                "filled_buy": [],
                "filled_sell": [],
            }
            decision_node[i] = key
        else:
            require((day, unit) not in held_keys, "CHAIN_DUPLICATE_HELD_KEY")
            held_keys[day, unit] = i
    require(set(coordinates) == set(nodes), "CHAIN_COORDINATE_SET")
    seen_review = set()
    for ri, row in enumerate(reviews):
        r = row["identity"]
        hk, key = (r["market_date"], r["unit_id"]), r["entry_key"]
        require(hk in held_keys and hk not in seen_review and key in nodes, "CHAIN_REVIEW_KEY")
        di, node = held_keys[hk], nodes[key]
        entry = decisions[node["decision"]]
        require(
            entry["filled"]
            and entry["unit_id"] == r["unit_id"]
            and entry["market_date"] == r["entry_date"] < r["market_date"]
            and node["security_code"] == r["security_code"]
            and row["baseline"]["baseline_decision_digest"]
            == decisions[di]["formal_decision_digest"]
            and row["baseline"]["baseline_action"] == decisions[di]["final_action"],
            "CHAIN_REVIEW_DECISION_BINDING",
        )
        seen_review.add(hk)
        node["held"].append(di)
        node["reviews"].append(ri)
        decision_node[di] = key
    require(seen_review == set(held_keys), "CHAIN_HELD_DENOMINATOR")

    event_ids, types = set(), defaultdict(list)
    for i, e in enumerate(events):
        require(e["event_id"] not in event_ids, "CHAIN_DUPLICATE_EVENT")
        require("2012-01-01" <= business_day(e["event_time"]) <= "2021-12-31", "CHAIN_EVENT_DATE")
        event_ids.add(e["event_id"])
        types[e["event_type"]].append(i)
    expected_types = {
        "signal_accepted",
        "order_created",
        "order_intent_created",
        "fill",
        "order_status",
        "ledger_entry",
        "daily_snapshot",
        "unit_settlement",
        "corporate_action",
    }
    require(set(types) <= expected_types, "CHAIN_UNKNOWN_EVENT_TYPE")
    assigned: dict[int, str] = {}
    signal_node, signal_decision, signals, fills = {}, {}, {}, {}
    orders: dict[str, dict[str, Any]] = {}
    intents: dict[str, dict[str, Any]] = {}

    def assign(ei: int, key: str) -> None:
        require(ei not in assigned or assigned[ei] == key, "CHAIN_EVENT_CONFLICT")
        assigned[ei] = key

    for ei in types["signal_accepted"]:
        p = events[ei]["payload"]
        matches = decision_signal_key[
            business_day(p["decision_at"]),
            p["unit_id"],
            p["decision_version"].rsplit("|", 1)[-1],
            p["side"],
        ]
        require(len(matches) == 1 and matches[0] in decision_node, "CHAIN_SIGNAL_DECISION")
        key = decision_node[matches[0]]
        require(
            nodes[key]["security_code"] == p["security_code"] and p["signal_id"] not in signals,
            "CHAIN_SIGNAL_IDENTITY",
        )
        signal_node[p["signal_id"]], signals[p["signal_id"]] = key, p
        signal_decision[p["signal_id"]] = matches[0]
        nodes[key]["signals"].append(ei)
        assign(ei, key)
    for kind, target, id_field in (
        ("order_created", orders, "order_id"),
        ("order_intent_created", intents, "intent_id"),
    ):
        for ei in types[kind]:
            p = events[ei]["payload"]
            require(
                p["signal_id"] in signals and p[id_field] not in target, "CHAIN_ORDER_REFERENCE"
            )
            signal = signals[p["signal_id"]]
            require(
                all(p[k] == signal[k] for k in ("unit_id", "security_code", "side", "quantity")),
                "CHAIN_ORDER_SIGNAL_BINDING",
            )
            target[p[id_field]] = p
            assign(ei, signal_node[p["signal_id"]])
    require(len(orders) == len(intents) == len(signals), "CHAIN_ORDER_CENSUS")
    require(
        {p["signal_id"] for p in orders.values()}
        == {p["signal_id"] for p in intents.values()}
        == set(signals),
        "CHAIN_ORDER_SIGNAL_SET",
    )
    for p in orders.values():
        require(
            p["intent_id"] in intents and intents[p["intent_id"]]["signal_id"] == p["signal_id"],
            "CHAIN_INTENT_REFERENCE",
        )
    filled_decisions: Counter[int] = Counter()
    for ei in types["fill"]:
        p = events[ei]["payload"]
        require(p["order_id"] in orders and p["fill_id"] not in fills, "CHAIN_FILL_REFERENCE")
        order = orders[p["order_id"]]
        require(p["side"] == order["side"] and p["side"] in {"buy", "sell"}, "CHAIN_FILL_SIDE")
        key = signal_node[order["signal_id"]]
        filled_decisions[signal_decision[order["signal_id"]]] += 1
        fills[p["fill_id"]] = (key, p, order)
        nodes[key]["filled_buy" if p["side"] == "buy" else "filled_sell"].append(ei)
        assign(ei, key)
    require(
        all(
            filled_decisions[i] == int(r["filled"])
            for i, r in enumerate(decisions)
            if r["surface"] == "entry"
        ),
        "CHAIN_DECISION_FILL_FLAGS",
    )
    for node in nodes.values():
        require(
            len(node["filled_buy"]) == int(decisions[node["decision"]]["filled"]),
            "CHAIN_FILLED_ENTRY_CENSUS",
        )
        require(len(node["filled_sell"]) <= 1, "CHAIN_MULTIPLE_EXITS")

    action_units: dict[str, set[str]] = defaultdict(set)
    settlements = {}
    for ei in types["unit_settlement"]:
        p = events[ei]["payload"]
        require(p["settlement_id"] not in settlements, "CHAIN_DUPLICATE_SETTLEMENT")
        settlements[p["settlement_id"]] = p
    for ei in types["order_status"]:
        p = events[ei]["payload"]
        require(p["order_id"] in orders, "CHAIN_STATUS_REFERENCE")
        order = orders[p["order_id"]]
        assign(ei, signal_node[order["signal_id"]])
        if p.get("action_id"):
            action_units[p["action_id"]].add(order["unit_id"])
    for ei in types["ledger_entry"]:
        p = events[ei]["payload"]
        ref, kind = p["reference_id"], p["reference_type"]
        if kind == "fill":
            require(ref in fills, "CHAIN_LEDGER_FILL_REFERENCE")
            key, _, order = fills[ref]
            require(
                p["unit_id"] == order["unit_id"] and p["security_code"] == order["security_code"],
                "CHAIN_LEDGER_UNIT",
            )
            assign(ei, key)
        elif kind == "corporate_action_cash_dividend":
            action_units[ref].add(p["unit_id"])
        else:
            require(
                kind == "unit_close_settlement"
                and ref in settlements
                and p["unit_id"] == settlements[ref]["unit_id"],
                "CHAIN_LEDGER_SETTLEMENT_REFERENCE",
            )

    # Only actual filled lifecycles establish intervals. Pending orders do not.
    intervals: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for key, node in nodes.items():
        if not node["filled_buy"]:
            require(not node["filled_sell"] and not node["held"], "CHAIN_UNFILLED_HELD")
            continue
        start = business_day(events[node["filled_buy"][0]]["payload"]["filled_at"])
        end = (
            business_day(events[node["filled_sell"][0]]["payload"]["filled_at"])
            if node["filled_sell"]
            else "2021-12-31"
        )
        require(start <= end, "CHAIN_REVERSED_LIFECYCLE")
        intervals[decisions[node["decision"]]["unit_id"]].append((start, end, key))
    starts = {}
    for unit, values in intervals.items():
        values.sort()
        require(
            all(a[1] < b[0] for a, b in zip(values, values[1:], strict=False)),
            "CHAIN_OVERLAPPING_UNIT_LIFECYCLE",
        )
        starts[unit] = [v[0] for v in values]

    def interval(unit: str, day: str, security: str | None) -> str | None:
        values = intervals.get(unit, [])
        n = bisect.bisect_right(starts.get(unit, []), day) - 1
        if n < 0 or day > values[n][1]:
            return None
        key = values[n][2]
        require(
            security is None or security == nodes[key]["security_code"], "CHAIN_LIFECYCLE_SECURITY"
        )
        return key

    actions = {}
    for ei in types["corporate_action"]:
        p = events[ei]["payload"]
        require(
            p["action_id"] not in actions and len(action_units[p["action_id"]]) == 1,
            "CHAIN_ACTION_UNIT_ANCHOR",
        )
        unit = next(iter(action_units[p["action_id"]]))
        key = interval(unit, p["effective_date"], p["security_code"])
        if key is None:
            raise DecisionChainError("CHAIN_ACTION_LIFECYCLE")
        actions[p["action_id"]] = key
        assign(ei, key)
    require(set(action_units) == set(actions), "CHAIN_ACTION_REFERENCE_SET")
    for ei in types["ledger_entry"]:
        p = events[ei]["payload"]
        if p["reference_type"] == "corporate_action_cash_dividend":
            require(p["reference_id"] in actions, "CHAIN_LEDGER_ACTION_REFERENCE")
            assign(ei, actions[p["reference_id"]])
        elif p["reference_type"] == "unit_close_settlement":
            key = interval(p["unit_id"], business_day(events[ei]["event_time"]), p["security_code"])
            if key is None:
                raise DecisionChainError("CHAIN_SETTLEMENT_LIFECYCLE")
            assign(ei, key)
    for kind in ("daily_snapshot", "unit_settlement"):
        for ei in types[kind]:
            p = events[ei]["payload"]
            day = (
                p["session_date"]
                if kind == "daily_snapshot"
                else business_day(events[ei]["event_time"])
            )
            key = interval(p["unit_id"], day, p.get("security_code"))
            if key is not None:
                assign(ei, key)
            else:
                require(
                    kind == "daily_snapshot"
                    and p["position_quantity"] == 0
                    and p["security_code"] is None,
                    "CHAIN_UNASSIGNED_ACTIVE_EVENT",
                )
    snapshot_keys = {}
    for ei in types["daily_snapshot"]:
        p = events[ei]["payload"]
        hk = p["session_date"], p["unit_id"]
        require(hk not in snapshot_keys, "CHAIN_DUPLICATE_SNAPSHOT")
        snapshot_keys[hk] = ei
    for hk, di in held_keys.items():
        require(
            hk in snapshot_keys and assigned.get(snapshot_keys[hk]) == decision_node[di],
            "CHAIN_HELD_SNAPSHOT",
        )
    for ei, key in assigned.items():
        nodes[key]["events"].append(ei)
    ledger_units = [p["unit_id"] for p in data["ledgers"]]
    require(
        len(set(ledger_units)) == len(ledger_units) and set(intervals) <= set(ledger_units),
        "CHAIN_FINAL_LEDGER_UNITS",
    )
    for node in nodes.values():
        node["events"].sort(
            key=lambda i: (datetime.fromisoformat(events[i]["event_time"]), events[i]["event_id"])
        )
    counts = {
        "entry_decisions": len(nodes),
        "held_decisions": len(held_keys),
        "engine_events": len(events),
        "filled_entries": sum(bool(n["filled_buy"]) for n in nodes.values()),
        "filled_exits": sum(bool(n["filled_sell"]) for n in nodes.values()),
        "review_rows": len(reviews),
    }
    require(
        data["reconciliation"]["decision_count"] == len(decisions)
        and data["reconciliation"]["engine_event_count"] == len(events)
        and data["reconciliation"]["fill_count"] == len(fills)
        and data["reconciliation"]["ledger_count"] == len(types["ledger_entry"]),
        "CHAIN_ORIGINAL_COUNTS",
    )
    return {
        "nodes": nodes,
        "unassigned_events": sorted(set(range(len(events))) - assigned.keys()),
        "counts": counts,
        "event_types": dict(sorted(Counter(e["event_type"] for e in events).items())),
    }


def _scope(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {k: contract[k] for k in ("first_date", "last_date", "source_cutoff")}


def build_package(selection: Path, output: Path, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    contract, code = load_contract(root), _code_identity()
    selection, output = _physical(selection), output.absolute()
    parent = _physical(output.parent)
    require(not output.exists() and not output.is_symlink(), "CHAIN_OUTPUT_EXISTS")
    raw_selection = selection.read_bytes()
    require(sha(raw_selection) == contract["selection_sha256"], "CHAIN_SELECTION_CHANGED")
    selected = _json(raw_selection)["roles"]
    require(set(selected) == ROLES, "CHAIN_INPUT_ROLE_SET")
    protected = {selection.parent, *(_physical(Path(p["path"])).parent for p in selected.values())}
    for source in protected:
        require(
            not (
                output == source or output.is_relative_to(source) or source.is_relative_to(output)
            ),
            "CHAIN_OUTPUT_SOURCE_OVERLAP",
        )
    raw, before = {}, {}
    for role, pin in selected.items():
        path = _physical(Path(pin["path"]))
        before[str(path)] = _stat(path)
        require(
            {k: pin[k] for k in ("bytes", "sha256")} == contract["roles"][role], "CHAIN_INPUT_PIN"
        )
        raw[role] = path.read_bytes()
        require(
            len(raw[role]) == pin["bytes"] and sha(raw[role]) == pin["sha256"],
            "CHAIN_INPUT_CHANGED:" + role,
        )
    read_held_package(
        Path(selected["review_manifest"]["path"]).parent,
        contract["roles"]["review_manifest"]["sha256"],
    )
    data = decode(raw)
    provenance(data, contract)
    index = project(data)
    require(index["counts"] == contract["expected_counts"], "CHAIN_EXPECTED_COUNTS")
    files = {k + (".jsonl" if k == "review_rows" else ".json"): v for k, v in raw.items()}
    files["index.json"] = encoded(index) + b"\n"
    files["contract.json"] = encoded(contract) + b"\n"
    manifest = {
        "schema_version": SCHEMA,
        "purpose": PURPOSE,
        "scope": _scope(contract),
        "execution_authorized": False,
        "historical_PIT_qualified": False,
        "consumer_admission": "RESEARCH_DIAGNOSTIC_ONLY",
        "observed_execution": "ORIGINAL_SIMULATION_ONLY",
        "contract_sha256": sha(files["contract.json"]),
        "code_sha256": code,
        "members": selected,
        "selection_archive_path": str(selection),
        "files": {n: {"bytes": len(v), "sha256": sha(v)} for n, v in files.items()},
        "counts": index["counts"],
        "unassigned_empty_snapshots": len(index["unassigned_events"]),
        "stages": {
            "index_build_count": 1,
            "facts_materializations": 0,
            "replays": 0,
            "model_scores": 0,
        },
        "unobserved": [
            "unrecorded allocation/rejection reasons",
            "counterfactual fills",
            "per-episode performance attribution",
            "live trading",
        ],
    }
    body = encoded(manifest) + b"\n"
    size = sum(map(len, files.values())) + len(body) + 65
    require(size <= min(MAX_BYTES, contract["maximum_output_bytes"]), "CHAIN_OUTPUT_BUDGET")
    fs = os.statvfs(parent)
    require(fs.f_bavail * fs.f_frsize - size >= contract["minimum_free_bytes"], "CHAIN_FREE_SPACE")
    require(code == _code_identity(), "CHAIN_CODE_CHANGED")
    require(all(_stat(Path(p)) == stat for p, stat in before.items()), "CHAIN_INPUT_MUTATED")
    output.mkdir(mode=0o700)
    for name, value in sorted(files.items()):
        _write(output / name, value)
    _write(output / "manifest.json", body)
    digest = sha(body)
    _write(output / "READY", (digest + "\n").encode())
    return {
        "status": "SEALED_RECORDED_B_REVIEW_ONLY",
        "manifest_sha256": digest,
        "output_bytes": size,
        "counts": index["counts"],
        "elapsed_seconds": time.monotonic() - started,
    }


def read_package(
    package: Path, digest: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    package = _physical(package)
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "CHAIN_TRUSTED_DIGEST")
    names = {r + (".jsonl" if r == "review_rows" else ".json") for r in ROLES} | {
        "index.json",
        "contract.json",
        "manifest.json",
        "READY",
    }
    require({p.name for p in package.iterdir()} == names, "CHAIN_PHYSICAL_SET")
    size = 0
    for p in package.iterdir():
        _physical(p)
        require(p.is_file() and p.stat().st_nlink == 1, "CHAIN_NONINDEPENDENT_FILE")
        size += p.stat().st_size
    require(size <= MAX_BYTES, "CHAIN_PACKAGE_SIZE")
    body = (package / "manifest.json").read_bytes()
    require(
        sha(body) == digest and (package / "READY").read_bytes() == (digest + "\n").encode(),
        "CHAIN_MANIFEST_CHANGED",
    )
    manifest = _json(body)
    require(
        manifest["schema_version"] == SCHEMA
        and manifest["purpose"] == PURPOSE
        and manifest["execution_authorized"] is False
        and manifest["historical_PIT_qualified"] is False
        and manifest["consumer_admission"] == "RESEARCH_DIAGNOSTIC_ONLY"
        and manifest["observed_execution"] == "ORIGINAL_SIMULATION_ONLY",
        "CHAIN_SCOPE",
    )
    require(set(manifest["files"]) == names - {"manifest.json", "READY"}, "CHAIN_FILE_MANIFEST")
    raw = {}
    for name, pin in manifest["files"].items():
        value = (package / name).read_bytes()
        require(
            len(value) == pin["bytes"] and sha(value) == pin["sha256"],
            "CHAIN_PAYLOAD_CHANGED:" + name,
        )
        raw[name] = value
    contract = _json(raw.pop("contract.json"))
    index = _json(raw.pop("index.json"))
    require(
        sha((package / "contract.json").read_bytes()) == manifest["contract_sha256"]
        and contract["purpose"] == PURPOSE
        and _scope(contract) == manifest["scope"]
        and contract["first_date"] == "2012-01-01"
        and contract["last_date"] == "2021-12-31"
        and set(contract["roles"]) == set(manifest["members"]) == ROLES
        and contract["historical_PIT_qualified"] is False
        and contract["execution_authorized"] is False
        and contract["consumer_admission"] == "RESEARCH_DIAGNOSTIC_ONLY",
        "CHAIN_OFFLINE_CONTRACT",
    )
    roles = {k: raw[k + (".jsonl" if k == "review_rows" else ".json")] for k in ROLES}
    for k, value in roles.items():
        require(
            {"bytes": len(value), "sha256": sha(value)}
            == contract["roles"][k]
            == {f: manifest["members"][k][f] for f in ("bytes", "sha256")},
            "CHAIN_OFFLINE_SOURCE_IDENTITY",
        )
    data = decode(roles)
    provenance(data, contract)
    require(
        project(data) == index
        and index["counts"] == contract["expected_counts"] == manifest["counts"]
        and len(index["unassigned_events"]) == manifest["unassigned_empty_snapshots"],
        "CHAIN_INDEX_RECONCILIATION",
    )
    return manifest, data, index


def query(
    data: Mapping[str, Any],
    index: Mapping[str, Any],
    *,
    security: str | None = None,
    day: str | None = None,
    unit: str | None = None,
    episode: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    require(type(limit) is int and 1 <= limit <= 100, "CHAIN_QUERY_LIMIT")
    if day is not None:
        require(date.fromisoformat(day).isoformat() == day, "CHAIN_QUERY_DATE")
    selected = []
    for key, node in sorted(
        index["nodes"].items(),
        key=lambda p: (data["decisions"][p[1]["decision"]]["market_date"], p[0]),
    ):
        d = data["decisions"][node["decision"]]
        dates = {
            d["market_date"],
            *(data["decisions"][i]["market_date"] for i in node["held"]),
            *(business_day(data["events"][i]["event_time"]) for i in node["events"]),
        }
        if (
            (security is None or node["security_code"] == security)
            and (unit is None or d["unit_id"] == unit)
            and (episode is None or key == episode)
            and (day is None or day in dates)
        ):
            selected.append((key, node))
    rows = []
    for key, node in selected[:limit]:
        decision = data["decisions"][node["decision"]]
        rows.append(
            {
                "entry_key": key,
                "security_code": node["security_code"],
                "unit_id": decision["unit_id"],
                "entry_decision": {"row": node["decision"], "recorded": decision},
                "observed_state": "FILLED_AND_EXITED"
                if node["filled_sell"]
                else "FILLED_STILL_OPEN_AT_RUN_END"
                if node["filled_buy"]
                else "ORDER_OBSERVED_NOT_FILLED"
                if node["signals"]
                else "NO_ORDER_OBSERVED",
                "allocation_detail": (
                    "NOT_RECORDED; allocated flag denotes producer consideration only"
                ),
                "unrecorded_rejection_reason": "NOT_RECORDED",
                "events": [{"row": i, "recorded": data["events"][i]} for i in node["events"]],
                "held_decisions": [
                    {"row": i, "recorded": data["decisions"][i]} for i in node["held"]
                ],
                "held_reviews": [
                    {"row": i, "recorded": data["review_rows"][i]} for i in node["reviews"]
                ],
                "end_of_run_unit_ledger_context": [
                    p for p in data["ledgers"] if p["unit_id"] == decision["unit_id"]
                ],
                "ledger_context_warning": "END_OF_RUN_UNIT_CONTEXT_NOT_EPISODE_PERFORMANCE",
            }
        )
    return {
        "matched": len(selected),
        "returned": len(rows),
        "truncated": len(selected) > len(rows),
        "rows": rows,
    }


def render_html(result: Mapping[str, Any], manifest: Mapping[str, Any], digest: str) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    states = {
        "FILLED_AND_EXITED": "原模拟已买入并退出",
        "FILLED_STILL_OPEN_AT_RUN_END": "原模拟已买入，运行结束时仍持有",
        "ORDER_OBSERVED_NOT_FILLED": "有原模拟订单，未成交",
        "NO_ORDER_OBSERVED": "未观察到原模拟订单",
    }
    kinds = {
        "signal_accepted": "原信号接收",
        "order_created": "原模拟订单",
        "order_intent_created": "原订单意图",
        "fill": "原模拟成交",
        "order_status": "原订单状态",
        "ledger_entry": "原账本记账",
        "daily_snapshot": "原持仓快照",
        "unit_settlement": "原资金单元结算",
        "corporate_action": "原公司行动处理",
    }
    cards = []
    for row in result["rows"]:
        dates: dict[str, dict[str, list[Any]]] = defaultdict(
            lambda: {"decisions": [], "events": []}
        )
        decision = row["entry_decision"]["recorded"]
        dates[decision["market_date"]]["decisions"].append(("入场决定", decision))
        for value in row["held_reviews"]:
            r = value["recorded"]
            dates[r["identity"]["market_date"]]["decisions"].append(
                ("持仓审查 · " + r["baseline"]["base_reason_code"], r)
            )
        for value in row["events"]:
            e = value["recorded"]
            dates[business_day(e["event_time"])]["events"].append(e)

        def item(title: str, value: Any) -> str:
            return (
                "<li><b>"
                + esc(title)
                + "</b><details><summary>原始记录</summary><pre>"
                + esc(json.dumps(value, ensure_ascii=False, indent=2))
                + "</pre></details></li>"
            )

        days = []
        for day, groups in sorted(dates.items()):
            recorded_decisions = "".join(item(title, value) for title, value in groups["decisions"])
            engine_events = "".join(
                item(e["event_time"] + " · " + kinds[e["event_type"]] + " · " + e["reason_code"], e)
                for e in sorted(
                    groups["events"],
                    key=lambda e: (datetime.fromisoformat(e["event_time"]), e["event_id"]),
                )
            )
            days.append(
                "<section><h3>"
                + esc(day)
                + "（上海业务日）</h3>"
                + (
                    "<p>决定只记录业务日，未单列时刻；不据此推断日内先后。</p><ul>"
                    + recorded_decisions
                    + "</ul>"
                    if recorded_decisions
                    else ""
                )
                + (
                    "<p>以下模拟引擎事件按实际时刻排列：</p><ol>" + engine_events + "</ol>"
                    if engine_events
                    else ""
                )
                + "</section>"
            )
        items = "".join(days)
        cards.append(
            "<article><h2>"
            + esc(row["security_code"])
            + " · "
            + esc(row["unit_id"])
            + "</h2><p>"
            + esc(states[row["observed_state"]])
            + "</p><p>分配细节或未记录的拒绝原因：未知。allocated 仅为原分配考虑标记。</p>"
            + items
            + "<details><summary>原运行期末资金单元账本（不是本次持仓绩效）</summary><pre>"
            + esc(json.dumps(row["end_of_run_unit_ledger_context"], ensure_ascii=False, indent=2))
            + "</pre></details></article>"
        )
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;">'
        "<title>原B决策链</title><style>body{font:16px/1.6 system-ui;max-width:1100px;"
        "margin:30px auto;padding:0 20px;background:#f4f6f8}article{background:white;"
        "padding:20px;margin:20px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;"
        "font-size:12px}li{margin:12px 0}</style><h1>原B：入场—模拟成交—持仓—退出</h1>"
        "<p>只读回溯材料，2012–2021；所有成交均为原模拟记录，未授予生产执行或历史PIT资格。"
        "原来源观察截止："
        + esc(manifest["scope"]["source_cutoff"])
        + "</p><p>匹配 "
        + esc(result["matched"])
        + "，展示 "
        + esc(result["returned"])
        + "；未执行的替代路径仍未观察。</p>"
        + ("".join(cards) if cards else "<p>没有匹配记录。</p>")
        + "<footer>manifest SHA256："
        + esc(digest)
        + "</footer></html>"
    )


def write_query(path: Path, value: bytes, package: Path, manifest: Mapping[str, Any]) -> None:
    path = path.absolute()
    _physical(path.parent)
    protected = {
        package.absolute(),
        Path(manifest["selection_archive_path"]).parent,
        *(Path(p["path"]).parent for p in manifest["members"].values()),
    }
    require(
        not any(path == p or path.is_relative_to(p) for p in protected),
        "CHAIN_QUERY_SOURCE_OVERLAP",
    )
    _write(path, value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--selection", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    for name in ("verify", "query"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--package", type=Path, required=True)
        cmd.add_argument("--manifest-sha256", required=True)
        if name == "query":
            for field in ("security", "date", "unit", "episode"):
                cmd.add_argument("--" + field)
            cmd.add_argument("--limit", type=int, default=5)
            cmd.add_argument("--format", choices=("json", "html"), default="json")
            cmd.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build_package(args.selection, args.output, args.root)))
        return
    manifest, data, index = read_package(args.package, args.manifest_sha256)
    if args.command == "verify":
        print(
            json.dumps(
                {
                    "status": "VERIFIED_RECORDED_B_REVIEW_ONLY",
                    "counts": index["counts"],
                    "unassigned_empty_snapshots": len(index["unassigned_events"]),
                }
            )
        )
        return
    result = query(
        data,
        index,
        security=args.security,
        day=args.date,
        unit=args.unit,
        episode=args.episode,
        limit=args.limit,
    )
    value = (
        render_html(result, manifest, args.manifest_sha256).encode()
        if args.format == "html"
        else encoded(result) + b"\n"
    )
    if args.output:
        write_query(args.output, value, args.package, manifest)
        print(
            json.dumps(
                {
                    "matched": result["matched"],
                    "returned": result["returned"],
                    "output": str(args.output),
                }
            )
        )
    else:
        print(value.decode())


if __name__ == "__main__":
    main()
