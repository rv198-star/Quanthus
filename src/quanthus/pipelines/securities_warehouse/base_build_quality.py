"""Versioned, fail-closed historical publication gates.

The v2 policy records only governed dispositions. It never rewrites source values,
grades, or held windows, and every accepted case must bind an evidence file hash.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget
from .base_build_delta import _connection, literal, relation
from .base_build_inputs import file_sha, identity, member
from .base_build_period import load

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = "p10-standard-base-historical-gates-v2"
POLICY = REPOSITORY_ROOT / "config/pipelines/base-build-historical-gates.v2.json"
EVIDENCE_ROOT = REPOSITORY_ROOT / "config/pipelines/historical-gate-evidence-v2"
EXPECTED = {
    ("SZSE-300114-20250217-volume-coverage", "SZSE", "300114", "2025-02-17"),
    ("SZSE-002336-20200617-tradability", "SZSE", "002336", "2020-06-17"),
    ("SZSE-002500-20200617-tradability", "SZSE", "002500", "2020-06-17"),
    ("SSE-688065-20230615-tradability", "SSE", "688065", "2023-06-15"),
}
ALLOWED_DISPOSITIONS = {
    "PENDING",
    "REPAIRED_IN_PREDECESSOR",
    "ACCEPTED_MINUTE_LOT_GRANULARITY",
    "ACCEPTED_PERMANENT_HISTORICAL_LIMITATION",
}
RESTRICTIONS = {
    "PENDING": {"KEEP_PENDING"},
    "REPAIRED_IN_PREDECESSOR": {"PRESERVE_ORIGINAL_SOURCE", "NO_FURTHER_OVERRIDE"},
    "ACCEPTED_MINUTE_LOT_GRANULARITY": {
        "FORBID_EXACT_DAILY_MINUTE_VOLUME_EQUALITY",
        "PRESERVE_DAILY_AND_MINUTE_VALUES",
    },
    "ACCEPTED_PERMANENT_HISTORICAL_LIMITATION": {
        "HISTORICAL_PIT_UNAVAILABLE",
        "CROSS_PROVIDER_EXACT_PRICE_UNAVAILABLE",
        "COMPLETE_MARKET_COVERAGE_UNAVAILABLE",
        "NATIVE_5M_UNAVAILABLE",
    },
}
EXPECTED_EVIDENCE = {
    "SZSE-300114-20250217-volume-coverage": {
        "owner-302132-granularity.json",
        "300114-lineage.json",
        "300114-alias-coverage.json",
    },
    "SZSE-002336-20200617-tradability": {
        "owner-daily-revision.json",
        "base24r1-target-verification.json",
        "base24r1-repair-receipt.json",
    },
    "SZSE-002500-20200617-tradability": {
        "owner-daily-revision.json",
        "base24r1-target-verification.json",
        "base24r1-repair-receipt.json",
    },
    "SSE-688065-20230615-tradability": {
        "owner-688065-permanent-limitation.json",
        "historical-disposition-decision.json",
        "backfill-raw-provenance-review.json",
    },
}
INHERITED_CONTRACTS = {
    "p10-standard-base-historical-gates-v1",
    CONTRACT,
}
INHERITED_STATUSES = {
    "PENDING_GOVERNED_DISPOSITION",
    "NO_MATCHING_OBSERVATION",
    "REPAIRED_IN_PREDECESSOR",
    "ACCEPTED_MINUTE_LOT_GRANULARITY",
    "ACCEPTED_PERMANENT_HISTORICAL_LIMITATION",
}


def _sha(value: str) -> bool:
    return re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def validate_policy(policy: dict[str, Any]) -> None:
    cases = policy.get("cases")
    case_keys = {
        "id",
        "venue",
        "stock_code",
        "market_date",
        "disposition",
        "reason",
        "restrictions",
        "evidence",
    }
    if (
        set(policy)
        != {
            "contract",
            "rule",
            "new_quality_held_allowed",
            "inherited_held",
            "evidence_root",
            "cases",
        }
        or policy.get("contract") != CONTRACT
        or not isinstance(policy.get("rule"), str)
        or not policy["rule"].strip()
        or type(policy.get("new_quality_held_allowed")) is not int
        or policy["new_quality_held_allowed"] != 0
        or policy.get("inherited_held") != "EXACT_PREDECESSOR_SET_NO_EXPANSION"
        or policy.get("evidence_root") != "config/pipelines/historical-gate-evidence-v2"
        or not isinstance(cases, list)
        or len(cases) != len(EXPECTED)
    ):
        raise BoundedError("BUILD_HISTORICAL_POLICY_CHANGED_OR_UNGOVERNED")
    for case in cases:
        if (
            not isinstance(case, dict)
            or set(case) != case_keys
            or not all(
                isinstance(case[k], str) and case[k].strip()
                for k in ("id", "venue", "stock_code", "market_date", "disposition", "reason")
            )
            or case["disposition"] not in ALLOWED_DISPOSITIONS
            or not isinstance(case["restrictions"], list)
            or not case["restrictions"]
            or any(not isinstance(v, str) or not v.strip() for v in case["restrictions"])
            or len(set(case["restrictions"])) != len(case["restrictions"])
            or set(case["restrictions"]) != RESTRICTIONS[case["disposition"]]
            or not isinstance(case["evidence"], list)
            or not case["evidence"]
        ):
            raise BoundedError("BUILD_HISTORICAL_POLICY_CHANGED_OR_UNGOVERNED")
        evidence_paths = set()
        for item in case["evidence"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "sha256"}
                or not isinstance(item["path"], str)
                or Path(item["path"]).is_absolute()
                or ".." in Path(item["path"]).parts
                or not _sha(item["sha256"])
            ):
                raise BoundedError("BUILD_HISTORICAL_POLICY_CHANGED_OR_UNGOVERNED")
            evidence_paths.add(item["path"])
            evidence_file = EVIDENCE_ROOT / item["path"]
            if not evidence_file.is_file() or file_sha(evidence_file) != item["sha256"]:
                raise BoundedError("BUILD_HISTORICAL_EVIDENCE_CHANGED")
        if evidence_paths != EXPECTED_EVIDENCE.get(case["id"]):
            raise BoundedError("BUILD_HISTORICAL_POLICY_CHANGED_OR_UNGOVERNED")
    if {(c["id"], c["venue"], c["stock_code"], c["market_date"]) for c in cases} != EXPECTED:
        raise BoundedError("BUILD_HISTORICAL_POLICY_CHANGED_OR_UNGOVERNED")


def inherited_cases(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Registrations survive missing observations and accept v1 or v2 receipts."""
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
        or gate.get("contract") not in INHERITED_CONTRACTS
        or gate.get("identity") != identity({k: v for k, v in gate.items() if k != "identity"})
        or not isinstance(cases, list)
        or len(cases) != len(EXPECTED)
        or any(not isinstance(c, dict) or not keys <= set(c) for c in cases)
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
            or case["status"] not in INHERITED_STATUSES
            or any(
                not isinstance(case[k], str) for k in ("id", "venue", "stock_code", "market_date")
            )
        ):
            raise BoundedError("BUILD_HISTORICAL_INHERITED_REGISTRY_INVALID")
    if {(c["id"], c["venue"], c["stock_code"], c["market_date"]) for c in cases} != EXPECTED:
        raise BoundedError("BUILD_HISTORICAL_INHERITED_REGISTRY_INVALID")
    return {r["id"]: r for r in cases}


def evaluate_historical(ods: Path, dwd: Path, budget: Budget) -> dict[str, Any]:
    """Evaluate governed dispositions after the complete paired audit."""
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
                join = (
                    " FROM "
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
                )
                present = bool(
                    connection.execute("SELECT EXISTS(SELECT 1" + join + ")").fetchone()[0]
                )
                if case["disposition"] == "REPAIRED_IN_PREDECESSOR" and present:
                    bad = connection.execute(
                        "SELECT count(*) FROM (SELECT b.volume,b.turnover"
                        + join
                        + " AND (b.volume IS NULL OR b.turnover IS NULL "
                        + "OR b.volume<>0 OR b.turnover<>0))"
                    ).fetchone()[0]
                    if bad:
                        raise BoundedError("BUILD_HISTORICAL_REPAIRED_VALUE_NOT_ZERO")
            applies = (
                registered
                or inherited.get(case["id"], {}).get("scope_registered", False)
                or present
            )
            disposition = case["disposition"]
            if not applies:
                status = "NO_MATCHING_OBSERVATION"
            elif disposition == "PENDING":
                status = "PENDING_GOVERNED_DISPOSITION"
            elif disposition == "REPAIRED_IN_PREDECESSOR":
                if not present:
                    raise BoundedError("BUILD_HISTORICAL_REPAIRED_OBSERVATION_MISSING")
                status = disposition
            elif disposition == "ACCEPTED_MINUTE_LOT_GRANULARITY":
                if not present:
                    raise BoundedError("BUILD_HISTORICAL_MINUTE_OBSERVATION_MISSING")
                status = disposition
            else:
                status = disposition
            observations.append(
                {
                    "id": case["id"],
                    "venue": case["venue"],
                    "stock_code": case["stock_code"],
                    "market_date": case["market_date"],
                    "observations_present": present,
                    "scope_registered": applies,
                    "status": status,
                    "disposition": disposition,
                    "restrictions": list(case["restrictions"]),
                    "evidence_sha256": [item["sha256"] for item in case["evidence"]],
                    "facts_or_grades_changed": False,
                    "held_added": 0,
                }
            )
    publication_eligible = not any(
        r["scope_registered"] and r["status"] == "PENDING_GOVERNED_DISPOSITION"
        for r in observations
    )
    result = {
        "contract": CONTRACT,
        "policy_sha256": file_sha(POLICY),
        "cases": observations,
        "publication_eligible": publication_eligible,
        "disposition_rule": "GOVERNED_CASE_DISPOSITIONS_ENFORCED",
        "historical_disposition": "PASS_ALL_REGISTERED_CASES_GOVERNED"
        if publication_eligible
        else "PENDING_REGISTERED_CASE_DISPOSITION",
        "facts_or_grades_changed": False,
        "held_added": 0,
    }
    result["identity"] = identity(result)
    return result
