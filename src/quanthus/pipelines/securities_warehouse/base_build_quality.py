"""Fail-closed historical publication gates, without rewriting original facts.

Current rules intentionally leave the four registered real-data diagnostics
unresolved. Synthetic data with no matching observations can prove absence;
presence never becomes repaired data or a new held window by declaration.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget
from .base_build_delta import _connection, literal, relation
from .base_build_inputs import file_sha, identity, member
from .base_build_period import load

CONTRACT = "p10-standard-base-historical-gates-v1"
POLICY = (
    Path(__file__).resolve().parents[4] / "config/pipelines/base-build-historical-gates.v1.json"
)
EXPECTED = {
    ("SZSE-300114-20250217-volume-coverage", "SZSE", "300114", "2025-02-17"),
    ("SZSE-002336-20200617-tradability", "SZSE", "002336", "2020-06-17"),
    ("SZSE-002500-20200617-tradability", "SZSE", "002500", "2020-06-17"),
    ("SSE-688065-20230615-tradability", "SSE", "688065", "2023-06-15"),
}


def validate_policy(policy: dict[str, Any]) -> None:
    cases = policy.get("cases")
    case_keys = {"id", "venue", "stock_code", "market_date", "disposition", "reason"}
    if (
        set(policy) != {"contract", "rule", "new_quality_held_allowed", "inherited_held", "cases"}
        or policy.get("contract") != CONTRACT
        or not isinstance(policy.get("rule"), str)
        or not policy["rule"].strip()
        or type(policy.get("new_quality_held_allowed")) is not int
        or policy["new_quality_held_allowed"] != 0
        or policy.get("inherited_held") != "EXACT_PREDECESSOR_SET_NO_EXPANSION"
        or not isinstance(cases, list)
        or len(cases) != len(EXPECTED)
        or any(
            not isinstance(c, dict)
            or set(c) != case_keys
            or any(not isinstance(v, str) or not v.strip() for v in c.values())
            for c in cases
        )
        or {(c.get("id"), c.get("venue"), c.get("stock_code"), c.get("market_date")) for c in cases}
        != EXPECTED
        or any(c.get("disposition") != "PENDING" or not c.get("reason") for c in cases)
    ):
        raise BoundedError("BUILD_HISTORICAL_POLICY_CHANGED_OR_UNGOVERNED")


def inherited_cases(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Registrations survive a missing observation and cannot be false-like strings."""
    gate = manifest.get("pairing", {}).get("historical_gate")
    if gate is None:
        return {}
    keys = {
        "id",
        "venue",
        "stock_code",
        "market_date",
        "observations_present",
        "scope_registered",
        "status",
        "facts_or_grades_changed",
        "held_added",
    }
    cases = gate.get("cases") if isinstance(gate, dict) else None
    if (
        not isinstance(gate, dict)
        or gate.get("contract") != CONTRACT
        or gate.get("identity") != identity({k: v for k, v in gate.items() if k != "identity"})
        or not isinstance(cases, list)
        or len(cases) != len(EXPECTED)
        or any(not isinstance(c, dict) or set(c) != keys for c in cases)
    ):
        raise BoundedError("BUILD_HISTORICAL_INHERITED_REGISTRY_INVALID")
    for case in cases:
        if (
            any(
                type(case[k]) is not bool
                for k in ("observations_present", "scope_registered", "facts_or_grades_changed")
            )
            or case["facts_or_grades_changed"] is not False
            or type(case["held_added"]) is not int
            or case["held_added"] != 0
            or (case["observations_present"] and not case["scope_registered"])
            or case["status"]
            != (
                "PENDING_GOVERNED_DISPOSITION"
                if case["scope_registered"]
                else "NO_MATCHING_OBSERVATION"
            )
            or any(
                not isinstance(case[k], str) for k in ("id", "venue", "stock_code", "market_date")
            )
        ):
            raise BoundedError("BUILD_HISTORICAL_INHERITED_REGISTRY_INVALID")
    if {(c["id"], c["venue"], c["stock_code"], c["market_date"]) for c in cases} != EXPECTED:
        raise BoundedError("BUILD_HISTORICAL_INHERITED_REGISTRY_INVALID")
    return {r["id"]: r for r in cases}


def evaluate_historical(ods: Path, dwd: Path, budget: Budget) -> dict[str, Any]:
    """Called after full native/paired closure; only inspect locally audited data."""
    policy = load(POLICY)
    validate_policy(policy)
    frozen = load(member(ods, "evidence/frozen-inputs.json"))
    registered = any(
        r["purpose"] == "historical_quality"
        for r in frozen["content"]["selection"].get("evidence", [])
    )
    archive = load(member(ods, "MATERIALIZATION.json"))
    predecessor = next(r for r in archive["sources"].values() if r["role"] == "ods")
    old_manifest = load(member(ods, predecessor["prefix"] + "/manifest.json"))
    inherited = inherited_cases(old_manifest)
    record = load(member(dwd, "MATERIALIZATION.json"))
    layout = record["layout"]
    listings = [member(dwd, r["path"]) for r in layout["dwd_listing"]["files"]]
    venues = [member(dwd, r["path"]) for r in layout["dwd_trading_venue"]["files"]]
    observations = []
    with _connection(budget) as connection:
        for case in policy["cases"]:
            budget.begin_stage("historical-presence-" + case["id"])
            day = date.fromisoformat(case["market_date"])
            paths = []
            for row in layout["dwd_security_bar"]["files"]:
                if row["partition"] == "empty":
                    continue
                tags = dict(piece.split("=", 1) for piece in row["partition"].split("/"))
                if not {"observation_start_year", "observation_start_month"} <= tags.keys():
                    raise BoundedError("BUILD_HISTORICAL_PARTITION_RULE_CHANGED")
                if (
                    int(tags["observation_start_year"]) == day.year
                    and int(tags["observation_start_month"]) == day.month
                ):
                    paths.append(member(dwd, row["path"]))
            present = False
            if paths:
                mic = "XSHE" if case["venue"] == "SZSE" else "XSHG"
                query = (
                    "SELECT EXISTS(SELECT 1 FROM "
                    + relation(paths)
                    + " b JOIN "
                    + relation(listings)
                    + " l ON b.listing_id=l.listing_id JOIN "
                    + relation(venues)
                    + " v ON l.venue_id=v.venue_id WHERE l.listing_code="
                    + literal(case["stock_code"])
                    + " AND v.mic_code="
                    + literal(mic)
                    + " AND CAST(timezone('Asia/Shanghai',b.observation_start) AS DATE)=DATE "
                    + literal(case["market_date"])
                    + ")"
                )
                present = bool(connection.execute(query).fetchone()[0])
            applies = (
                registered
                or inherited.get(case["id"], {}).get("scope_registered", False)
                or present
            )
            observations.append(
                {
                    "id": case["id"],
                    "venue": case["venue"],
                    "stock_code": case["stock_code"],
                    "market_date": case["market_date"],
                    "observations_present": present,
                    "scope_registered": applies,
                    "status": "PENDING_GOVERNED_DISPOSITION"
                    if applies
                    else "NO_MATCHING_OBSERVATION",
                    "facts_or_grades_changed": False,
                    "held_added": 0,
                }
            )
    result = {
        "contract": CONTRACT,
        "policy_sha256": file_sha(POLICY),
        "cases": observations,
        "publication_eligible": not any(r["scope_registered"] for r in observations),
        "disposition_rule": "PRESENCE_REMAINS_PENDING_NO_JOB_OVERRIDE",
        "facts_or_grades_changed": False,
        "held_added": 0,
    }
    result["identity"] = identity(result)
    return result
