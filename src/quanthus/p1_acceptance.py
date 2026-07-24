"""Structural validation for the Chapter 01 D1/D2/D3 acceptance evidence bundle."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


class AcceptanceEvidenceError(RuntimeError):
    """Raised when an evidence bundle cannot be parsed."""


@dataclass(frozen=True)
class EvidenceCheck:
    check_id: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.check_id, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class P1AcceptanceReport:
    snapshot_id: str
    checks: tuple[EvidenceCheck, ...]

    @property
    def has_failures(self) -> bool:
        return any(not check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "passed": not self.has_failures,
            "checks": [check.to_dict() for check in self.checks],
        }


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceEvidenceError(f"{field_name} must be a non-empty string")
    return value


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceEvidenceError(f"{field_name} must be an object")
    return value


def _as_cases(value: Any, field_name: str) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise AcceptanceEvidenceError(f"{field_name} must be an array")
    if not all(isinstance(item, dict) for item in value):
        raise AcceptanceEvidenceError(f"{field_name} entries must be objects")
    return value


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_security_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _all_passed_with_references(cases: Sequence[Mapping[str, Any]]) -> bool:
    return all(
        case.get("status") == "pass"
        and isinstance(case.get("reference_id"), str)
        and bool(case["reference_id"].strip())
        for case in cases
    )


def _blocker_checks(raw: Mapping[str, Any]) -> list[EvidenceCheck]:
    blockers = _as_mapping(raw.get("blockers"), "blockers")
    checks: list[EvidenceCheck] = []
    for blocker_id in ("C1", "C2", "S1"):
        evidence = blockers.get(blocker_id)
        if not isinstance(evidence, dict):
            checks.append(EvidenceCheck(blocker_id, False, "missing blocker evidence"))
            continue
        evidence_id = evidence.get("evidence_id")
        passed = (
            evidence.get("status") == "pass"
            and isinstance(evidence_id, str)
            and bool(evidence_id.strip())
        )
        checks.append(
            EvidenceCheck(
                blocker_id,
                passed,
                "passed evidence is required" if not passed else "passed evidence is recorded",
            )
        )
    return checks


def _d1_check(raw: Mapping[str, Any]) -> EvidenceCheck:
    evidence = _as_mapping(raw.get("D1"), "D1")
    cases = _as_cases(evidence.get("cases"), "D1.cases")
    dates = {case.get("market_date") for case in cases if _valid_date(case.get("market_date"))}
    securities = {
        case.get("security_id")
        for case in cases
        if isinstance(case.get("security_id"), str) and case["security_id"].strip()
    }
    pairs = {(case.get("market_date"), case.get("security_id")) for case in cases}
    complete_matrix = len(pairs) == len(dates) * len(securities)
    passed = (
        len(cases) == 100
        and len(dates) == 10
        and len(securities) == 10
        and len(pairs) == 100
        and complete_matrix
        and _all_passed_with_references(cases)
        and bool(str(evidence.get("independent_reference", "")).strip())
    )
    detail = (
        f"cases={len(cases)}, dates={len(dates)}, securities={len(securities)}, "
        f"unique_pairs={len(pairs)}"
    )
    return EvidenceCheck("D1", passed, detail)


def _d2_check(raw: Mapping[str, Any]) -> EvidenceCheck:
    evidence = _as_mapping(raw.get("D2"), "D2")
    cases = _as_cases(evidence.get("cases"), "D2.cases")
    case_keys = {
        (case.get("market_date"), case.get("security_id"), case.get("case_type")) for case in cases
    }
    valid_types = all(case.get("case_type") in {"limit", "suspension"} for case in cases)
    valid_dates = all(_valid_date(case.get("market_date")) for case in cases)
    valid_securities = all(_valid_security_id(case.get("security_id")) for case in cases)
    passed = (
        len(cases) >= 20
        and len(case_keys) == len(cases)
        and valid_types
        and valid_dates
        and valid_securities
        and _all_passed_with_references(cases)
        and bool(str(evidence.get("independent_reference", "")).strip())
    )
    return EvidenceCheck("D2", passed, f"cases={len(cases)}, unique_cases={len(case_keys)}")


def _d3_check(raw: Mapping[str, Any]) -> EvidenceCheck:
    evidence = _as_mapping(raw.get("D3"), "D3")
    cases = _as_cases(evidence.get("cases"), "D3.cases")
    case_keys = {(case.get("ex_date"), case.get("security_id")) for case in cases}
    valid_dates = all(_valid_date(case.get("ex_date")) for case in cases)
    valid_securities = all(_valid_security_id(case.get("security_id")) for case in cases)
    no_artificial_jumps = all(case.get("artificial_jump_detected") is False for case in cases)
    passed = (
        bool(cases)
        and len(case_keys) == len(cases)
        and valid_dates
        and valid_securities
        and no_artificial_jumps
        and _all_passed_with_references(cases)
        and bool(str(evidence.get("independent_reference", "")).strip())
    )
    return EvidenceCheck("D3", passed, f"cases={len(cases)}, unique_cases={len(case_keys)}")


def validate_p1_acceptance_evidence(raw: Mapping[str, Any]) -> P1AcceptanceReport:
    """Validate evidence structure; gate configuration remains a separate explicit change."""

    if raw.get("schema_version") != "1.0.0":
        raise AcceptanceEvidenceError("schema_version must be 1.0.0")
    snapshot_id = _non_empty_string(raw.get("snapshot_id"), "snapshot_id")
    checks = [*_blocker_checks(raw), _d1_check(raw), _d2_check(raw), _d3_check(raw)]
    return P1AcceptanceReport(snapshot_id=snapshot_id, checks=tuple(checks))


def load_p1_acceptance_evidence(path: Path) -> P1AcceptanceReport:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AcceptanceEvidenceError(f"evidence bundle does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AcceptanceEvidenceError(f"invalid JSON in evidence bundle: {exc}") from exc
    if not isinstance(raw, dict):
        raise AcceptanceEvidenceError("evidence bundle root must be an object")
    return validate_p1_acceptance_evidence(raw)
