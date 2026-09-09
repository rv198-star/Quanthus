from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.policy import ConfigurationError, find_project_root


def _read_gate_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"gate file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("gate file root must be an object")
    return raw


def validate_gates(raw: Mapping[str, Any]) -> None:
    if not isinstance(raw.get("gate_version"), str) or not raw["gate_version"].strip():
        raise ConfigurationError("gate_version must be a non-empty string")
    if raw.get("p1_implementation") not in {"allowed", "blocked"}:
        raise ConfigurationError("p1_implementation must be allowed or blocked")
    if raw.get("p1_acceptance") not in {"passed", "blocked"}:
        raise ConfigurationError("p1_acceptance must be passed or blocked")
    if raw.get("p2_implementation") not in {"allowed", "blocked"}:
        raise ConfigurationError("p2_implementation must be allowed or blocked")

    blockers = raw.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        raise ConfigurationError("blockers must be a non-empty array")
    blocker_ids: list[str] = []
    open_blockers: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, dict):
            raise ConfigurationError("each blocker must be an object")
        blocker_id = blocker.get("id")
        status = blocker.get("status")
        if not isinstance(blocker_id, str) or not blocker_id:
            raise ConfigurationError("each blocker must have an id")
        if status not in {"open", "closed"}:
            raise ConfigurationError(f"blocker {blocker_id} has an invalid status")
        blocker_ids.append(blocker_id)
        if status == "open":
            open_blockers.append(blocker_id)
    if len(blocker_ids) != len(set(blocker_ids)):
        raise ConfigurationError("blocker ids must be unique")
    if raw.get("p1_acceptance") == "passed" and open_blockers:
        raise ConfigurationError("P1 acceptance cannot pass with open blockers")

    conditional = raw.get("conditional_acceptance")
    if conditional is not None:
        if not isinstance(conditional, dict):
            raise ConfigurationError("conditional_acceptance must be an object")
        if conditional.get("id") != "C1":
            raise ConfigurationError("conditional_acceptance must apply to C1")
        if conditional.get("exception_count") != 4173:
            raise ConfigurationError("conditional_acceptance.exception_count must be 4173")
        for field_name in (
            "policy_version",
            "snapshot_id",
            "exception_ledger_revision",
            "runtime_behavior",
        ):
            value = conditional.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(
                    f"conditional_acceptance.{field_name} must be a non-empty string"
                )
        if raw.get("p1_acceptance") != "passed":
            raise ConfigurationError("conditional_acceptance requires P1 acceptance to pass")

    chapter = raw.get("chapter_01_acceptance")
    if not isinstance(chapter, dict) or chapter.get("status") not in {"pending", "passed"}:
        raise ConfigurationError("chapter_01_acceptance.status must be pending or passed")
    checks = chapter.get("checks")
    if not isinstance(checks, list) or len(checks) != 3:
        raise ConfigurationError("Chapter 01 must have exactly three acceptance checks")
    if raw.get("p2_implementation") == "allowed" and chapter.get("status") != "passed":
        raise ConfigurationError("P2 cannot be allowed before Chapter 01 acceptance")


@dataclass(frozen=True)
class GateReport:
    path: Path
    version: str
    p1_can_start: bool
    p1_acceptance_passed: bool
    p2_can_start: bool
    open_blockers: tuple[str, ...]
    chapter_01_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "p1_can_start": self.p1_can_start,
            "p1_acceptance_passed": self.p1_acceptance_passed,
            "p2_can_start": self.p2_can_start,
            "open_blockers": list(self.open_blockers),
            "chapter_01_status": self.chapter_01_status,
        }


def load_gate_report(path: Path | None = None) -> GateReport:
    resolved = (path or find_project_root() / "config" / "p1-gates.v1.json").resolve()
    raw = _read_gate_file(resolved)
    validate_gates(raw)
    blockers = tuple(
        str(blocker["id"]) for blocker in raw["blockers"] if blocker["status"] == "open"
    )
    chapter_status = str(raw["chapter_01_acceptance"]["status"])
    return GateReport(
        path=resolved,
        version=str(raw["gate_version"]),
        p1_can_start=raw["p1_implementation"] == "allowed",
        p1_acceptance_passed=raw["p1_acceptance"] == "passed" and not blockers,
        p2_can_start=(raw["p2_implementation"] == "allowed" and chapter_status == "passed"),
        open_blockers=blockers,
        chapter_01_status=chapter_status,
    )


_P2_WORK_ITEMS = {
    "P2-ENG-001",
    "P2-POLICY-001",
    "P2-MATCH-001",
    "P2-LEDGER-001",
    "P2-ORDER-001",
    "P2-AUDIT-001",
    "P2-METRIC-001",
    "P2-OBS-001",
    "P2-HYP-001",
    "P2-ACCEPT-001",
}


def validate_p2_gates(raw: Mapping[str, Any]) -> None:
    if not isinstance(raw.get("gate_version"), str) or not raw["gate_version"].strip():
        raise ConfigurationError("P2 gate_version must be a non-empty string")
    acceptance = raw.get("p2_acceptance")
    if acceptance not in {"pending", "passed-conditional"}:
        raise ConfigurationError("p2_acceptance must be pending or passed-conditional")
    if raw.get("p3_implementation") not in {"allowed", "blocked"}:
        raise ConfigurationError("p3_implementation must be allowed or blocked")
    if raw.get("p3_implementation") == "allowed" and acceptance != "passed-conditional":
        raise ConfigurationError("P3 cannot be allowed before conditional P2 acceptance")
    for field_name in ("accepted_scope", "accepted_profile", "acceptance_evidence"):
        value = raw.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"{field_name} must be a non-empty string")

    policy = raw.get("execution_policy")
    if not isinstance(policy, Mapping):
        raise ConfigurationError("execution_policy must be an object")
    for field_name in ("path", "version"):
        value = policy.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"execution_policy.{field_name} must be non-empty")

    conditional = raw.get("conditional_acceptance")
    if not isinstance(conditional, Mapping):
        raise ConfigurationError("conditional_acceptance must be an object")
    if conditional.get("id") != "P2-TARGET-PROFILE-BLOCKED":
        raise ConfigurationError("P2 conditional acceptance id is invalid")
    for field_name in ("blocked_profile", "runtime_behavior"):
        value = conditional.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"conditional_acceptance.{field_name} must be non-empty")

    work_items = raw.get("work_items")
    if not isinstance(work_items, Mapping) or set(work_items) != _P2_WORK_ITEMS:
        raise ConfigurationError("P2 work_items must contain the exact accepted work-item set")
    if any(status not in {"open", "closed"} for status in work_items.values()):
        raise ConfigurationError("P2 work-item status must be open or closed")
    if acceptance == "passed-conditional" and any(
        status != "closed" for status in work_items.values()
    ):
        raise ConfigurationError("P2 acceptance cannot pass with open P2 work items")

    blockers = raw.get("activation_blockers")
    if not isinstance(blockers, list) or not blockers:
        raise ConfigurationError("activation_blockers must be a non-empty array")
    blocker_ids: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, Mapping):
            raise ConfigurationError("each P2 activation blocker must be an object")
        blocker_id = blocker.get("id")
        if not isinstance(blocker_id, str) or not blocker_id.strip():
            raise ConfigurationError("each P2 activation blocker must have an id")
        if blocker.get("status") not in {"open", "closed"}:
            raise ConfigurationError(f"activation blocker {blocker_id} has invalid status")
        for field_name in ("blocks", "required_evidence"):
            value = blocker.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(
                    f"activation blocker {blocker_id}.{field_name} must be non-empty"
                )
        blocker_ids.append(blocker_id)
    if len(blocker_ids) != len(set(blocker_ids)):
        raise ConfigurationError("P2 activation blocker ids must be unique")


@dataclass(frozen=True)
class P2GateReport:
    path: Path
    version: str
    acceptance_status: str
    p2_acceptance_passed: bool
    p3_can_start: bool
    accepted_profile: str
    open_activation_blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "acceptance_status": self.acceptance_status,
            "p2_acceptance_passed": self.p2_acceptance_passed,
            "p3_can_start": self.p3_can_start,
            "accepted_profile": self.accepted_profile,
            "open_activation_blockers": list(self.open_activation_blockers),
        }


def load_p2_gate_report(path: Path | None = None) -> P2GateReport:
    resolved = (path or find_project_root() / "config" / "p2-gates.v1.json").resolve()
    raw = _read_gate_file(resolved)
    validate_p2_gates(raw)
    acceptance = str(raw["p2_acceptance"])
    blockers = tuple(
        str(blocker["id"]) for blocker in raw["activation_blockers"] if blocker["status"] == "open"
    )
    return P2GateReport(
        path=resolved,
        version=str(raw["gate_version"]),
        acceptance_status=acceptance,
        p2_acceptance_passed=acceptance == "passed-conditional",
        p3_can_start=(acceptance == "passed-conditional" and raw["p3_implementation"] == "allowed"),
        accepted_profile=str(raw["accepted_profile"]),
        open_activation_blockers=blockers,
    )


_P3_WORK_ITEMS = {
    "P3-REPLAY-001",
    "P3-FEAT-001",
    "P3-FEAT-002",
    "P3-STRAT-001",
    "P3-LABEL-001",
    "P3-LABEL-002",
    "P3-LABEL-003",
    "P3-LABEL-004",
    "P3-EXP-001",
    "P3-AGG-001",
    "P3-AGG-002",
    "P3-SHADOW-001",
    "P3-EXP-002",
    "P3-POS-001",
    "P3-POLICY-001",
    "P3-RISK-001",
    "P3-POS-002",
    "P3-EXIT-001",
    "P3-POS-003",
    "P3-FUNNEL-001",
    "P3-ATTR-001",
    "P3-DASH-001",
    "P3-ACCEPT-001",
}
_P3_DEFERRABLE_WORK_ITEMS = {"P3-LABEL-004", "P3-ATTR-001", "P3-DASH-001"}


def validate_p3_gates(raw: Mapping[str, Any]) -> None:
    if not isinstance(raw.get("gate_version"), str) or not raw["gate_version"].strip():
        raise ConfigurationError("P3 gate_version must be a non-empty string")
    acceptance = raw.get("p3_acceptance")
    if acceptance not in {"pending", "passed"}:
        raise ConfigurationError("p3_acceptance must be pending or passed")
    if raw.get("p4_implementation") not in {"allowed", "blocked"}:
        raise ConfigurationError("p4_implementation must be allowed or blocked")
    if raw.get("p4_implementation") == "allowed" and acceptance != "passed":
        raise ConfigurationError("P4 cannot be allowed before P3 acceptance")
    for field_name in ("accepted_scope", "accepted_profile", "acceptance_evidence"):
        value = raw.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"{field_name} must be a non-empty string")

    fixture = raw.get("capability_fixture")
    if not isinstance(fixture, Mapping):
        raise ConfigurationError("capability_fixture must be an object")
    for field_name in ("strategy_id", "strategy_version", "role"):
        value = fixture.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"capability_fixture.{field_name} must be non-empty")
    if fixture.get("role") != "frozen-capability-fixture":
        raise ConfigurationError("capability_fixture role must be frozen-capability-fixture")
    for field_name in ("promotion_authorized", "tuning_authorized"):
        if fixture.get(field_name) is not False:
            raise ConfigurationError(f"capability_fixture.{field_name} must be false")

    work_items = raw.get("work_items")
    if not isinstance(work_items, Mapping) or set(work_items) != _P3_WORK_ITEMS:
        raise ConfigurationError("P3 work_items must contain the exact disposition set")
    if any(status not in {"closed", "deferred"} for status in work_items.values()):
        raise ConfigurationError("P3 work-item status must be closed or deferred")
    if work_items.get("P3-ACCEPT-001") != "closed":
        raise ConfigurationError("P3-ACCEPT-001 must be closed before P3 acceptance")

    deferred = raw.get("deferred_capabilities")
    if not isinstance(deferred, list):
        raise ConfigurationError("deferred_capabilities must be an array")
    deferred_ids: list[str] = []
    for item in deferred:
        if not isinstance(item, Mapping):
            raise ConfigurationError("each deferred capability must be an object")
        item_id = item.get("id")
        resume_condition = item.get("resume_condition")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ConfigurationError("each deferred capability must have an id")
        if not isinstance(resume_condition, str) or not resume_condition.strip():
            raise ConfigurationError(f"deferred capability {item_id} needs a resume_condition")
        deferred_ids.append(item_id)
    if len(deferred_ids) != len(set(deferred_ids)):
        raise ConfigurationError("deferred capability ids must be unique")
    expected_deferred = {item_id for item_id, status in work_items.items() if status == "deferred"}
    if expected_deferred != _P3_DEFERRABLE_WORK_ITEMS:
        raise ConfigurationError("P3 gate has an invalid deferred work-item set")
    if set(deferred_ids) != expected_deferred:
        raise ConfigurationError("deferred capabilities must match deferred P3 work items")

    unopened = raw.get("unopened_partitions")
    if not isinstance(unopened, list) or not unopened:
        raise ConfigurationError("unopened_partitions must be a non-empty array")
    if any(not isinstance(value, str) or not value.strip() for value in unopened):
        raise ConfigurationError("unopened partition ids must be non-empty strings")

    exceptions = raw.get("bounded_partition_exceptions")
    if not isinstance(exceptions, list) or len(exceptions) != 1:
        raise ConfigurationError("P3 gate requires the exact bounded partition exception set")
    exception = exceptions[0]
    if not isinstance(exception, Mapping):
        raise ConfigurationError("bounded partition exception must be an object")
    expected_exception_fields = {
        "id",
        "partition",
        "config",
        "qualification_digest",
        "candidate_digest",
        "access_budget_runs",
        "remaining_access_budget_runs",
        "state",
        "consumed_job_id",
        "read_only",
        "parameter_search_authorized",
        "permanent_blind_access_authorized",
        "claim_ceiling",
    }
    if set(exception) != expected_exception_fields:
        raise ConfigurationError("bounded partition exception fields differ")
    qualification_digest = exception.get("qualification_digest")
    candidate_digest = exception.get("candidate_digest")
    if (
        exception.get("id") != "P3-STAGE-001-F"
        or exception.get("partition") != "2022-2024-arbitration"
        or exception.get("partition") not in unopened
        or exception.get("config") != "config/stage-learning/qualification.v1.json"
        or qualification_digest
        != "sha256:e235bd53ff7226358dc19eb6b80a08348cfeacf19455dd3bfa37626df43d15ac"
        or not isinstance(candidate_digest, str)
        or len(candidate_digest) != 71
        or not candidate_digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in candidate_digest[7:])
        or exception.get("access_budget_runs") != 1
        or exception.get("remaining_access_budget_runs") != 0
        or exception.get("state") != "consumed_inconclusive"
        or exception.get("consumed_job_id") != "stage_cf5b89c16c5b4fce8b3c5cdc00a156f7"
        or exception.get("read_only") is not True
        or exception.get("parameter_search_authorized") is not False
        or exception.get("permanent_blind_access_authorized") is not False
        or exception.get("claim_ceiling")
        != "sample_feasibility_inconclusive_no_shadow_or_qualified_claim"
    ):
        raise ConfigurationError("bounded partition exception exceeds P3-STAGE-001-F authority")
    if "2025-permanent-blind" not in unopened:
        raise ConfigurationError("permanent-blind partition must remain unopened")


@dataclass(frozen=True)
class P3GateReport:
    path: Path
    version: str
    acceptance_status: str
    p3_acceptance_passed: bool
    p4_can_start: bool
    accepted_profile: str
    capability_fixture_id: str
    deferred_work_items: tuple[str, ...]
    unopened_partitions: tuple[str, ...]
    bounded_partition_exceptions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "acceptance_status": self.acceptance_status,
            "p3_acceptance_passed": self.p3_acceptance_passed,
            "p4_can_start": self.p4_can_start,
            "accepted_profile": self.accepted_profile,
            "capability_fixture_id": self.capability_fixture_id,
            "deferred_work_items": list(self.deferred_work_items),
            "unopened_partitions": list(self.unopened_partitions),
            "bounded_partition_exceptions": list(self.bounded_partition_exceptions),
        }


def load_p3_gate_report(path: Path | None = None) -> P3GateReport:
    resolved = (path or find_project_root() / "config" / "p3-gates.v1.json").resolve()
    raw = _read_gate_file(resolved)
    validate_p3_gates(raw)
    acceptance = str(raw["p3_acceptance"])
    fixture = raw["capability_fixture"]
    deferred = tuple(
        sorted(item_id for item_id, status in raw["work_items"].items() if status == "deferred")
    )
    return P3GateReport(
        path=resolved,
        version=str(raw["gate_version"]),
        acceptance_status=acceptance,
        p3_acceptance_passed=acceptance == "passed",
        p4_can_start=acceptance == "passed" and raw["p4_implementation"] == "allowed",
        accepted_profile=str(raw["accepted_profile"]),
        capability_fixture_id=f"{fixture['strategy_id']}@{fixture['strategy_version']}",
        deferred_work_items=deferred,
        unopened_partitions=tuple(str(value) for value in raw["unopened_partitions"]),
        bounded_partition_exceptions=tuple(
            str(item["id"]) for item in raw["bounded_partition_exceptions"]
        ),
    )
