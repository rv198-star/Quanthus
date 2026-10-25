"""Read-only review of completed historical studies, never consumer admission.

This module checks existing files and reconciles actual cash flows. It does not
open warehouse/source roots, infer PIT, rerun a strategy, or authorize execution.
The caller supplies an explicit frozen archive contract; hashes establish that
contract's identity, not the scientific truth of its source qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from quanthus.backtest.contracts import MARKET_TIMEZONE
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy.p3_dated_share_validation import business_bundle

VERSION = "p3-completed-history-review-v1"
BUSINESS_FILES = frozenset(
    {
        "decisions.json",
        "engine-events.json",
        "equity.json",
        "daily-returns.json",
        "final-ledgers.json",
        "reconciliation.json",
    }
)
ROLES = ("primary", "independent_cold_reproduction")
Q = Decimal
RATE = Q("0.00000001")


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("HISTORY_REVIEW_" + reason)


def number(value: Any) -> Decimal:
    require(isinstance(value, (str, int)) and not isinstance(value, bool), "NUMBER_TYPE")
    result = Q(value)
    require(result.is_finite(), "NONFINITE")
    return result


def local_day(value: str) -> str:
    instant = datetime.fromisoformat(value)
    require(instant.utcoffset() is not None, "UNKNOWN_TIMEZONE")
    return instant.astimezone(MARKET_TIMEZONE).date().isoformat()


def read_json(path: Path, expected_sha256: str | None = None) -> tuple[Any, str]:
    """Read/hash the same bytes once; reject links and concurrent replacement."""
    for part in (path, *path.parents):
        require(not part.is_symlink(), "SYMLINK")
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "PHYSICAL_FILE")

    def identity(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    with path.open("rb") as handle:
        require(identity(os.fstat(handle.fileno())) == identity(before), "FILE_CHANGED")
        raw = handle.read()
        require(identity(os.fstat(handle.fileno())) == identity(before), "FILE_CHANGED")
    require(identity(path.stat()) == identity(before), "FILE_CHANGED")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    require(expected_sha256 is None or digest == expected_sha256, "FILE_HASH")
    return json.loads(raw), digest


@dataclass(frozen=True)
class ArchiveContract:
    purpose: str
    request_digest: str
    execution_digest: str
    source_manifest_sha256: str
    source_information_cutoff: str
    optional_rights_policy_sha256: str
    scope: dict[str, str]
    arms: tuple[str, ...]
    initial_unit_capital: str
    unit_ids: tuple[str, ...]
    sessions: int
    completion_sha256: dict[str, str]
    coordinate_sha256: dict[str, str]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArchiveContract:
        fields = set(cls.__dataclass_fields__)
        require(set(value) == fields, "CONTRACT_FIELDS")
        result = cls(
            **{
                **value,
                "arms": tuple(value["arms"]),
                "unit_ids": tuple(value["unit_ids"]),
            }
        )
        require(
            len(result.arms) == len(set(result.arms)) > 0
            and len(result.unit_ids) == len(set(result.unit_ids)) > 0
            and set(result.completion_sha256) == set(ROLES)
            and set(result.coordinate_sha256) == set(ROLES)
            and result.sessions > 0
            and number(result.initial_unit_capital) > 0,
            "CONTRACT_IDENTITIES",
        )
        require(
            set(result.scope) == {"decision_start", "decision_end", "execution_end"}
            and result.scope["decision_start"]
            <= result.scope["decision_end"]
            < result.scope["execution_end"],
            "CONTRACT_SCOPE",
        )
        return result


@dataclass
class ReviewedStudy:
    primary: dict[str, dict[str, Any]]
    coordinates: dict[str, str]
    summaries: dict[str, dict[str, Any]]
    proof: dict[str, Any]


def _snapshot_identity(contract: ArchiveContract) -> str:
    return contract.purpose + "/" + contract.request_digest.removeprefix("sha256:")


def verify_original_admission(
    freeze: dict[str, Any],
    acceptance: dict[str, Any],
    amendment: dict[str, Any],
    contract: ArchiveContract,
    artifact_bytes: int,
    role_telemetry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Check the historical receipt, without renewing an expired execution clock.

    The enclosing Job must bind these receipts' byte hashes before this call.
    This is historical admission verification, never new replay authorization.
    """
    start = datetime.fromisoformat(freeze["UTC"])
    deadline = datetime.fromisoformat(freeze["deadline_UTC"])
    accepted = datetime.fromisoformat(acceptance["UTC"])
    require(
        all(t.utcoffset() is not None for t in (start, deadline, accepted))
        and start <= accepted <= deadline,
        "ORIGINAL_ACCEPTANCE_DEADLINE",
    )
    require(
        acceptance["status"] == "PASS"
        and acceptance["purpose"] == contract.purpose
        and acceptance["source_manifest_sha256"] == contract.source_manifest_sha256
        and acceptance["request_digest"] == freeze["request_digest"] == contract.request_digest
        and acceptance["execution_digest"]
        == freeze["execution_digest"]
        == contract.execution_digest,
        "ORIGINAL_ACCEPTANCE_IDENTITY",
    )
    require(
        amendment["previous_cap_bytes"] == freeze["maximum_all_role_artifacts_bytes"]
        and amendment["only_existing_six_completed_runs"] is True
        and amendment["no_replay_source_collection_or_science_change"] is True
        and amendment["all_other_limits_and_failures_unchanged"] is True
        and amendment["original_execution_deadline_UTC"] == freeze["deadline_UTC"]
        and 0 < artifact_bytes <= amendment["maximum_all_role_artifacts_bytes"],
        "ORIGINAL_ARTIFACT_CAP",
    )
    if role_telemetry is not None:
        require(set(role_telemetry) == set(ROLES), "ORIGINAL_TELEMETRY_ROLES")
        require(
            all(
                0 < row["elapsed_seconds"] <= freeze["maximum_role_seconds"]
                and 0 < row["peak_RSS_bytes"] <= freeze["maximum_role_RSS_bytes"]
                for row in role_telemetry.values()
            )
            and sum(row["peak_RSS_bytes"] for row in role_telemetry.values())
            <= freeze["maximum_aggregate_role_RSS_bytes"],
            "ORIGINAL_RESOURCE_LIMIT",
        )
    return {
        "status": "PASS_ORIGINAL_RECEIPT_BOUNDARIES",
        "original_deadline_UTC": freeze["deadline_UTC"],
        "accepted_UTC": acceptance["UTC"],
        "artifact_bytes": artifact_bytes,
        "new_replay_authorized": False,
        "reported_role_telemetry_rechecked": role_telemetry is not None,
    }


def validate_bundle(
    artifacts: dict[str, Any], coordinates: dict[str, str], contract: ArchiveContract
) -> dict[str, Any]:
    """Independent sums of actual fills, terminal marks, dividends and fees."""
    require(set(artifacts) == BUSINESS_FILES, "BUSINESS_FILES")
    require(artifacts["reconciliation.json"].get("reconciled") is True, "RECONCILIATION")
    require(
        artifacts["reconciliation.json"].get("sealed_partition_access") is False,
        "PROTECTED_PARTITION",
    )
    events = artifacts["engine-events.json"]
    decisions = artifacts["decisions.json"]
    equity = artifacts["equity.json"]
    returns = artifacts["daily-returns.json"]
    require(len(equity) == contract.sessions + 1 and len(returns) == contract.sessions, "SESSIONS")
    days = [row["market_date"] for row in equity]
    require(days == sorted(set(days)), "EQUITY_DATES")
    require(
        days[0] == contract.scope["decision_start"]
        and days[-1] == contract.scope["execution_end"]
        and [row["market_date"] for row in returns] == days[1:],
        "DATE_SCOPE",
    )
    entry = [row for row in decisions if row["surface"] == "entry"]
    require(
        len(entry) == len(coordinates)
        and {row["decision_key"] for row in entry} == set(coordinates)
        and len({row["decision_key"] for row in entry}) == len(entry),
        "COORDINATE_CLOSURE",
    )
    for row in decisions:
        require(
            contract.scope["decision_start"]
            <= row["market_date"]
            <= contract.scope["decision_end"],
            "DECISION_DATE",
        )
        if row["surface"] == "entry":
            require(
                coordinates[row["decision_key"]].rsplit("@", 1)[-1] == row["market_date"],
                "COORDINATE_DATE",
            )
    for event in events:
        require(
            contract.scope["decision_start"]
            <= local_day(event["event_time"])
            <= contract.scope["execution_end"],
            "EVENT_DATE",
        )
    orders = {
        event["payload"]["order_id"]: event["payload"]
        for event in events
        if event["event_type"] == "order_created"
    }
    require(
        len(orders) == sum(e["event_type"] == "order_created" for e in events),
        "DUPLICATE_ORDER",
    )
    fills = [event for event in events if event["event_type"] == "fill"]
    for event in fills:
        fill = event["payload"]
        require(fill["order_id"] in orders, "UNKNOWN_ORDER")
        order = orders[fill["order_id"]]
        require(
            fill["side"] == order["side"]
            and type(fill["quantity"]) is int
            and fill["quantity"] > 0
            and order["unit_id"] in contract.unit_ids
            and local_day(fill["filled_at"]) == local_day(event["event_time"]),
            "FILL_IDENTITY",
        )
        require(
            number(fill["gross_amount"])
            == (number(fill["price"]) * fill["quantity"]).quantize(Q(".01"), ROUND_HALF_UP),
            "FILL_GROSS",
        )
        components = [number(fill[k]) for k in ("commission", "stamp_duty", "transfer_fee")]
        require(all(x >= 0 for x in components), "NEGATIVE_FEE")
        require(sum(components, Q(0)) == number(fill["total_fees"]), "FILL_FEES")
    snaps = {
        (event["payload"]["unit_id"], event["payload"]["session_date"]): event["payload"]
        for event in events
        if event["event_type"] == "daily_snapshot"
    }
    require(
        len(snaps) == sum(e["event_type"] == "daily_snapshot" for e in events),
        "DUPLICATE_SNAPSHOT",
    )
    balances = {unit: number(contract.initial_unit_capital) for unit in contract.unit_ids}
    market = {unit: Q(0) for unit in contract.unit_ids}
    active = dict.fromkeys(contract.unit_ids, 0)
    initial = sum(balances.values(), Q(0))
    require(number(equity[0]["system_equity"]) == initial, "INITIAL_CAPITAL")
    for index, row in enumerate(equity[1:], 1):
        for unit in contract.unit_ids:
            snap = snaps.get((unit, row["market_date"]))
            if snap is not None:
                require(
                    snap["unit_id"] == unit and local_day(snap["as_of"]) == snap["session_date"],
                    "SNAPSHOT_IDENTITY",
                )
                balances[unit] = sum(
                    (number(snap[k]) for k in ("cash", "market_value", "external_pool")), Q(0)
                )
                market[unit] = number(snap["market_value"])
                active[unit] = int(snap["position_quantity"] > 0)
        total, invested = sum(balances.values(), Q(0)), sum(market.values(), Q(0))
        require(
            total == number(row["system_equity"])
            and invested == number(row["market_value"])
            and sum(active.values()) == row["active_unit_count"],
            "DAILY_ACCOUNTING",
        )
        require(
            (invested / total).quantize(RATE, ROUND_HALF_UP)
            == number(row["invested_capital_ratio"]),
            "EXPOSURE",
        )
        expected_return = total / number(equity[index - 1]["system_equity"]) - 1
        require(
            expected_return.quantize(RATE, ROUND_HALF_UP) == number(returns[index - 1]["return"]),
            "RETURN",
        )
    fees = sum((number(event["payload"]["total_fees"]) for event in fills), Q(0))
    require(
        fees
        == sum(
            (
                number(row[key])
                for row in artifacts["final-ledgers.json"]
                for key in ("commission_expense", "stamp_duty_expense", "transfer_fee_expense")
            ),
            Q(0),
        ),
        "FINAL_FEES",
    )
    cycles = position_cycles(artifacts, coordinates, contract)
    cycle_pnl = sum((number(row["net_pnl"]) for row in cycles), Q(0))
    require(cycle_pnl == number(equity[-1]["system_equity"]) - initial, "CYCLE_ACCOUNTING")
    peak, drawdown = initial, Q(0)
    for row in equity:
        wealth = number(row["system_equity"])
        peak = max(peak, wealth)
        drawdown = max(drawdown, (peak - wealth) / peak)
    return {
        "sessions": contract.sessions,
        "position_cycles": len(cycles),
        "fills": len(fills),
        "net_pnl": str(cycle_pnl),
        "fees": str(fees),
        "net_total_return": str(cycle_pnl / initial),
        "maximum_drawdown": str(drawdown),
        "average_invested_capital": str(
            sum(
                (number(r["market_value"]) / number(r["system_equity"]) for r in equity[1:]),
                Q(0),
            )
            / contract.sessions
        ),
        "accounting_residual": "0",
    }


def position_cycles(
    artifacts: dict[str, Any], coordinates: dict[str, str], contract: ArchiveContract
) -> list[dict[str, Any]]:
    """Actual position episodes; unmatched trades are never counterfactual profits."""
    events = artifacts["engine-events.json"]
    orders = {
        e["payload"]["order_id"]: e["payload"] for e in events if e["event_type"] == "order_created"
    }
    signals = {
        e["payload"]["signal_id"]: e["payload"]
        for e in events
        if e["event_type"] == "signal_accepted"
    }
    decisions = {d["formal_decision_digest"]: d for d in artifacts["decisions.json"]}
    snaps = {
        (e["payload"]["unit_id"], e["payload"]["session_date"]): e["payload"]
        for e in events
        if e["event_type"] == "daily_snapshot"
    }
    fills = sorted(
        [e for e in events if e["event_type"] == "fill"],
        key=lambda e: (e["event_time"], e["sequence"]),
    )
    result: list[dict[str, Any]] = []
    for unit in contract.unit_ids:
        unit_fills = [e for e in fills if orders[e["payload"]["order_id"]]["unit_id"] == unit]
        buys = [e for e in unit_fills if e["payload"]["side"] == "buy"]
        for index, buy in enumerate(buys):
            begin = local_day(buy["event_time"])
            following = (
                local_day(buys[index + 1]["event_time"]) if index + 1 < len(buys) else "9999"
            )
            days = sorted(day for u, day in snaps if u == unit and begin <= day < following)
            require(bool(days) and days[0] == begin, "CYCLE_SNAPSHOT")
            closed = [day for day in days if snaps[unit, day]["position_quantity"] == 0]
            end = closed[0] if closed else days[-1]
            first, last = snaps[unit, begin], snaps[unit, end]
            episode = [
                e["payload"] for e in unit_fills if begin <= local_day(e["event_time"]) <= end
            ]
            order = orders[buy["payload"]["order_id"]]
            signal = signals[order["signal_id"]]
            decision = decisions[signal["decision_version"].rpartition("|")[2]]
            coordinate = coordinates[decision["decision_key"]]
            require(
                coordinate.rsplit("@", 1)[0] == order["security_code"]
                and decision["unit_id"] == unit
                and decision["filled"] is True,
                "CYCLE_COORDINATE",
            )
            gross = sum(
                (
                    number(fill["gross_amount"]) * (1 if fill["side"] == "sell" else -1)
                    for fill in episode
                ),
                Q(0),
            )
            fee = sum((number(fill["total_fees"]) for fill in episode), Q(0))
            dividend = number(last["dividend_income"]) - number(first["dividend_income"])
            terminal = number(last["market_value"])
            pnl = gross + terminal + dividend - fee
            result.append(
                {
                    "coordinate": coordinate,
                    "unit_id": unit,
                    "entry_date": begin,
                    "end_date": end,
                    "closed": bool(closed),
                    "holding_calendar_days": (
                        date.fromisoformat(end) - date.fromisoformat(begin)
                    ).days,
                    "fills": len(episode),
                    "gross_and_terminal_mark": str(gross + terminal),
                    "dividends": str(dividend),
                    "fees": str(fee),
                    "net_pnl": str(pnl),
                }
            )
    require(len({r["coordinate"] for r in result}) == len(result), "DUPLICATE_CYCLE_COORDINATE")
    return result


def review_pair(primary: Path, cold: Path, contract: ArchiveContract) -> ReviewedStudy:
    """Revalidate two already completed physical archives without source access."""
    require(
        not primary.resolve().is_relative_to(cold.resolve())
        and not cold.resolve().is_relative_to(primary.resolve()),
        "ROOT_OVERLAP",
    )
    identities: set[tuple[int, int]] = set()
    primary_data: dict[str, dict[str, Any]] = {}
    primary_coordinates: dict[str, str] = {}
    summaries: dict[str, dict[str, Any]] = {}
    graphs: dict[str, dict[str, str]] = {}
    file_proof: dict[str, dict[str, str]] = {}
    role_telemetry: dict[str, dict[str, Any]] = {}
    artifact_bytes = 0
    for role, root in zip(ROLES, (primary, cold), strict=True):
        coordinates, _ = read_json(
            root / "event-coordinates.json", contract.coordinate_sha256[role]
        )
        require(
            isinstance(coordinates, dict) and len(set(coordinates.values())) == len(coordinates),
            "COORDINATE_BIJECTION",
        )
        complete, _ = read_json(root / "replays/COMPLETE.json", contract.completion_sha256[role])
        artifact_bytes += (root / "replays/COMPLETE.json").stat().st_size
        role_telemetry[role] = {
            "elapsed_seconds": complete.get("elapsed_seconds"),
            "peak_RSS_bytes": complete.get("peak_RSS_bytes"),
        }
        require(
            complete["status"] == "COMPLETE"
            and complete["role"] == role
            and complete["kind"] == contract.purpose
            and complete["request_digest"] == contract.request_digest
            and complete["execution_digest"] == contract.execution_digest
            and complete["execution_adapter"] == contract.purpose
            and complete["source_manifest_sha256"] == contract.source_manifest_sha256
            and complete["production_authorized"] is False
            and set(complete["variants"]) == set(contract.arms),
            "COMPLETION_BINDING",
        )
        require(
            {p.name for p in (root / "replays").iterdir()}
            == {"COMPLETE.json", *("variant." + a for a in contract.arms)},
            "REPLAY_FILE_SET",
        )
        if role == ROLES[0]:
            primary_coordinates = coordinates
        else:
            require(coordinates == primary_coordinates, "COORDINATE_EQUIVALENCE")
        for arm in contract.arms:
            folder = root / "replays" / ("variant." + arm)
            require(
                {p.name for p in folder.iterdir()} == BUSINESS_FILES | {"manifest.json"},
                "ARTIFACT_FILE_SET",
            )
            expected = complete["variants"][arm]
            require(set(expected["file_sha256"]) == BUSINESS_FILES | {"manifest.json"}, "HASH_SET")
            files: dict[str, Any] = {}
            hashes: dict[str, str] = {}
            for name in sorted(BUSINESS_FILES | {"manifest.json"}):
                path = folder / name
                files[name], hashes[name] = read_json(path, expected["file_sha256"][name])
                info = path.stat()
                identity = (info.st_dev, info.st_ino)
                require(identity not in identities, "SHARED_PHYSICAL_FILE")
                identities.add(identity)
                artifact_bytes += info.st_size
            manifest = files.pop("manifest.json")
            require(
                manifest["purpose"] == contract.purpose
                and manifest["scope"] == contract.scope
                and manifest["source_manifest_sha256"] == contract.source_manifest_sha256
                and manifest["source_information_cutoff"] == contract.source_information_cutoff
                and manifest["execution_digest"] == contract.execution_digest
                and manifest["execution_adapter"] == contract.purpose
                and manifest["optional_rights_policy_sha256"]
                == contract.optional_rights_policy_sha256
                and manifest["no_subscription_all_arms"] is True
                and all(
                    manifest[k] is False
                    for k in (
                        "historical_availability_claim",
                        "execution_authorized",
                        "production_authorized",
                    )
                ),
                "MANIFEST_BINDING",
            )
            require(
                digest_value({name: digest_value(v) for name, v in sorted(files.items())})
                == manifest["replay_digest"]
                == expected["replay_digest"],
                "SEMANTIC_DIGEST",
            )
            graph = business_bundle(
                files,
                coordinates,
                snapshot=_snapshot_identity(contract),
                anchor_snapshot=_snapshot_identity(contract),
                snapshot_suffixes=[],
            )
            summary = validate_bundle(files, coordinates, contract)
            if role == ROLES[0]:
                primary_data[arm], graphs[arm], summaries[arm] = files, graph, summary
            else:
                require(graph == graphs[arm], "BUSINESS_GRAPH_MISMATCH")
                require(summary == summaries[arm], "ACCOUNTING_MISMATCH")
            file_proof[role + "/" + arm] = hashes
    return ReviewedStudy(
        primary=primary_data,
        coordinates=primary_coordinates,
        summaries=summaries,
        proof={
            "version": VERSION,
            "status": "PASS_EXISTING_ARCHIVE_REVALIDATION",
            "contract_digest": digest_value(contract.__dict__),
            "files": file_proof,
            "distinct_physical_files": len(identities),
            "artifact_bytes": artifact_bytes,
            "role_telemetry": role_telemetry,
            "business_graph_equal": True,
            "source_roots_opened": False,
            "new_replay": False,
            "consumer_admission_granted": False,
        },
    )


def review_admitted_pair(
    contract_path: Path, primary: Path, cold: Path, admission_evidence: Path
) -> tuple[ReviewedStudy, ArchiveContract]:
    raw, _ = read_json(contract_path)
    contract = ArchiveContract.from_dict(raw)
    bindings, _ = read_json(admission_evidence)
    require(set(bindings) == {"freeze", "acceptance", "amendment"}, "ADMISSION_EVIDENCE_FIELDS")
    receipts = {
        name: read_json(Path(record["path"]), record["sha256"])[0]
        for name, record in bindings.items()
    }
    reviewed = review_pair(primary, cold, contract)
    reviewed.proof["original_admission"] = verify_original_admission(
        receipts["freeze"],
        receipts["acceptance"],
        receipts["amendment"],
        contract,
        reviewed.proof["artifact_bytes"],
        reviewed.proof["role_telemetry"],
    )
    return reviewed, contract


def check_output(output: Path, input_roots: tuple[Path, ...]) -> None:
    require(not output.exists() and not output.is_symlink(), "OUTPUT_EXISTS")
    for root in input_roots:
        require(
            not output.resolve().is_relative_to(root.resolve())
            and not root.resolve().is_relative_to(output.resolve()),
            "OUTPUT_OVERLAP",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--cold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--admission-evidence",
        type=Path,
        required=True,
        help="Explicit path/SHA bindings for freeze, acceptance and cap amendment JSON receipts",
    )
    args = parser.parse_args()
    check_output(args.output, (args.primary, args.cold))
    reviewed, _ = review_admitted_pair(
        args.contract, args.primary, args.cold, args.admission_evidence
    )
    result = {**reviewed.proof, "summaries": reviewed.summaries}
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
