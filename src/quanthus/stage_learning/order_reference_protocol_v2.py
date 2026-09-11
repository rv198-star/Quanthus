"""Primary-first Stage 0/2 reference protocol for P3-STAGE-008-B.

Primary 20-session atomic opinions are committed before explicit 10-session derived facts are
revealed. Recent modifiers cannot mutate the committed primary ledger or resolver outcome.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.structure_directional_path_geometry import (
    COUNTER_DIRECTION_EXCURSION_SHARE,
    RANGE_TRAVERSAL,
    DirectionalPathMeasurement,
)
from quanthus.structure_v2_inputs import digest, exact_sha

PROTOCOL_PATH = "config/stage-learning/order-reference-protocol.v2.json"
PROTOCOL_DIGEST = "sha256:8db37fb3d197668cfe16f9daabaa57c625617460be94ace13a28ccb81e89c88b"
PRIMARY_OPINION_SCHEMA = "quanthus.stage-order-primary-opinion.v2"
PRIMARY_COMMIT_SCHEMA = "quanthus.stage-order-primary-commit.v2"
RECENT_MODIFIER_SCHEMA = "quanthus.stage-order-recent-modifier.v2"
RECENT_COMMIT_SCHEMA = "quanthus.stage-order-recent-commit.v2"
PRIMARY_PROPOSITIONS = (
    "structural_instability_20",
    "coherent_directional_relocation_20",
    "orderly_consolidation_20",
)
ROLES = ("short_term", "fund")
TRUTH_VALUES = {"TRUE", "FALSE", "UNKNOWN"}
RECENT_WIDTH = {"CONTRACTING", "EXPANDING", "STABLE", "UNKNOWN", "UNAVAILABLE"}
RECENT_CENTER = {"STABLE", "SHIFTING", "UNKNOWN", "UNAVAILABLE"}
RECENT_RELATION = {"SUPPORTS", "CONTRADICTS", "NEUTRAL", "UNKNOWN"}


class OrderReferenceV2Error(ValueError):
    """The primary-first reference contract was violated."""


def _plain(value: Any) -> Any:
    from quanthus.structure_v2_inputs import canonical

    return json.loads(canonical(value))


def _exact_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise OrderReferenceV2Error(f"{label}_fields_differ")
    return value


def _text(value: Any, label: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise OrderReferenceV2Error(f"{label}_invalid")
    return value


def load_protocol(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / PROTOCOL_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OrderReferenceV2Error("protocol_v2_unreadable") from exc
    if digest(value) != PROTOCOL_DIGEST:
        raise OrderReferenceV2Error("protocol_v2_drift")
    for contract in value["required_fact_contracts"].values():
        fact = json.loads((root / contract["path"]).read_text(encoding="utf-8"))
        if digest(fact) != contract["digest"]:
            raise OrderReferenceV2Error("protocol_v2_fact_contract_drift")
    return value


def _case_identity(case: Mapping[str, Any]) -> str:
    case_id = case.get("case_id")
    if not isinstance(case, dict) or not exact_sha(case_id):
        raise OrderReferenceV2Error("case_identity_invalid")
    if digest({key: value for key, value in case.items() if key != "case_id"}) != case_id:
        raise OrderReferenceV2Error("case_digest_mismatch")
    if (
        case.get("primary_window_sessions") != 20
        or case.get("recent_window_sessions") != 10
        or case.get("knowledge_view") != "retrospective"
    ):
        raise OrderReferenceV2Error("case_profile_mismatch")
    return case_id


def _path_facts(
    case: Mapping[str, Any], measurements: Sequence[DirectionalPathMeasurement]
) -> dict[str, dict[str, Any]]:
    case_id = _case_identity(case)
    expected = {RANGE_TRAVERSAL, COUNTER_DIRECTION_EXCURSION_SHARE}
    if len(measurements) != 2 or {item.concept_id for item in measurements} != expected:
        raise OrderReferenceV2Error("directional_path_fact_set_mismatch")
    output = {}
    for item in measurements:
        if (
            item.security_code != case["security_code"]
            or item.security_id != case["security_id"]
            or item.listing_id != case["listing_id"]
            or item.market_date.isoformat() != case["market_date"]
            or item.coordinate_ref != case["coordinate_ref"]
            or item.window_sessions != 20
        ):
            raise OrderReferenceV2Error("directional_path_fact_case_binding")
        output[item.concept_id] = {
            "value": item.value,
            "revision_id": item.identity.revision_id,
            "reason": item.availability_reason,
        }
    if not case_id:
        raise OrderReferenceV2Error("case_identity_missing")
    return _plain(output)


def build_primary_packet(
    case: Mapping[str, Any],
    price_history: Sequence[Mapping[str, Any]],
    path_measurements: Sequence[DirectionalPathMeasurement],
) -> dict[str, Any]:
    """Expose only 20-session primary evidence plus raw T-only path, never explicit recent facts."""
    case_id = _case_identity(case)
    if not isinstance(price_history, Sequence) or len(price_history) < 21:
        raise OrderReferenceV2Error("primary_history_requires_21_rows")
    history_refs = case.get("history_refs")
    if not isinstance(history_refs, list) or len(history_refs) != len(price_history):
        raise OrderReferenceV2Error("primary_history_reference_denominator")
    retained = []
    retained_refs = []
    for ref, row in zip(history_refs[-21:], price_history[-21:], strict=True):
        if not exact_sha(ref) or not isinstance(row, Mapping) or digest(dict(row)) != ref:
            raise OrderReferenceV2Error("primary_history_reference_mismatch")
        if str(row.get("market_date")) > str(case["market_date"]):
            raise OrderReferenceV2Error("primary_history_contains_future")
        retained.append(dict(row))
        retained_refs.append(ref)
    primary = case.get("primary")
    if not isinstance(primary, dict) or set(primary) != {
        "directional_efficiency", "range_width", "width_change", "center_shift"
    }:
        raise OrderReferenceV2Error("primary_fact_shape")
    path_facts = _path_facts(case, path_measurements)
    packet = _plain({
        "schema_version": "quanthus.stage-order-primary-packet.v2",
        "protocol_digest": PROTOCOL_DIGEST,
        "case_id": case_id,
        "security_code": case["security_code"],
        "security_id": case["security_id"],
        "listing_id": case["listing_id"],
        "market_date": case["market_date"],
        "coordinate_ref": case["coordinate_ref"],
        "primary_available": case["primary_available"],
        "primary_window_sessions": 20,
        "primary_facts": primary,
        "directional_path_facts": path_facts,
        "price_history": retained,
        "history_refs": retained_refs,
        "candidate_output_visible": False,
        "future_rows_visible": False,
        "explicit_10_session_derived_facts_visible": False,
    })
    return {**packet, "packet_digest": digest(packet)}


def _provenance(value: Any) -> dict[str, Any]:
    result = _exact_fields(value, {"kind", "origin_id", "session_id"}, "provenance")
    if result["kind"] not in {"self_simulated", "deterministic_policy", "external_ai", "human"}:
        raise OrderReferenceV2Error("provenance_kind_invalid")
    _text(result["origin_id"], "origin_id", 200)
    _text(result["session_id"], "session_id", 200)
    return result


def commit_primary_opinion(packet: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "case_id", "protocol_digest", "packet_digest", "reviewer_id", "role",
        "provenance", "primary", "support_refs", "contradiction_refs", "reason", "unknowns",
    }
    opinion = _exact_fields(dict(raw), expected, "primary_opinion")
    if (
        opinion["schema_version"] != PRIMARY_OPINION_SCHEMA
        or opinion["case_id"] != packet.get("case_id")
        or opinion["protocol_digest"] != PROTOCOL_DIGEST
        or opinion["packet_digest"] != packet.get("packet_digest")
    ):
        raise OrderReferenceV2Error("primary_opinion_binding")
    _text(opinion["reviewer_id"], "reviewer_id", 200)
    if opinion["role"] not in ROLES:
        raise OrderReferenceV2Error("primary_role_invalid")
    _provenance(opinion["provenance"])
    primary = _exact_fields(opinion["primary"], set(PRIMARY_PROPOSITIONS), "atomic_primary")
    if any(value not in TRUTH_VALUES for value in primary.values()):
        raise OrderReferenceV2Error("atomic_primary_truth_value")
    available_refs = set(packet["history_refs"])
    for area in (packet["primary_facts"], packet["directional_path_facts"]):
        available_refs.update(
            item["revision_id"] for item in area.values() if item.get("revision_id") is not None
        )
    for key in ("support_refs", "contradiction_refs", "unknowns"):
        values = opinion[key]
        if (
            not isinstance(values, list)
            or len(values) > 100
            or len(values) != len(set(values))
        ):
            raise OrderReferenceV2Error("primary_opinion_list_invalid")
        if any(not isinstance(value, str) for value in values):
            raise OrderReferenceV2Error("primary_opinion_list_invalid")
    if not set(opinion["support_refs"] + opinion["contradiction_refs"]) <= available_refs:
        raise OrderReferenceV2Error("primary_opinion_reference_outside_primary_packet")
    _text(opinion["reason"], "primary_reason")
    for reason in opinion["unknowns"]:
        _text(reason, "primary_unknown_reason")
    if not packet["primary_available"] and set(primary.values()) != {"UNKNOWN"}:
        raise OrderReferenceV2Error("unavailable_primary_must_remain_unknown")
    if "TRUE" in primary.values() and not opinion["support_refs"]:
        raise OrderReferenceV2Error("positive_primary_requires_support")
    if "UNKNOWN" in primary.values() and not opinion["unknowns"]:
        raise OrderReferenceV2Error("unknown_primary_requires_reason")
    normalized = _plain(opinion)
    return {
        "schema_version": PRIMARY_COMMIT_SCHEMA,
        "opinion": normalized,
        "primary_opinion_digest": digest(normalized),
    }


def validate_primary_commit(commit: Mapping[str, Any]) -> dict[str, Any]:
    value = _exact_fields(
        dict(commit), {"schema_version", "opinion", "primary_opinion_digest"}, "primary_commit"
    )
    if value["schema_version"] != PRIMARY_COMMIT_SCHEMA:
        raise OrderReferenceV2Error("primary_commit_schema")
    opinion = _exact_fields(
        value["opinion"],
        {
            "schema_version", "case_id", "protocol_digest", "packet_digest", "reviewer_id", "role",
            "provenance", "primary", "support_refs", "contradiction_refs", "reason", "unknowns",
        },
        "committed_primary_opinion",
    )
    if (
        opinion["schema_version"] != PRIMARY_OPINION_SCHEMA
        or opinion["protocol_digest"] != PROTOCOL_DIGEST
        or not exact_sha(opinion["case_id"])
        or not exact_sha(opinion["packet_digest"])
        or opinion["role"] not in ROLES
    ):
        raise OrderReferenceV2Error("committed_primary_opinion_identity")
    _text(opinion["reviewer_id"], "reviewer_id", 200)
    _provenance(opinion["provenance"])
    primary = _exact_fields(opinion["primary"], set(PRIMARY_PROPOSITIONS), "atomic_primary")
    if any(item not in TRUTH_VALUES for item in primary.values()):
        raise OrderReferenceV2Error("atomic_primary_truth_value")
    _text(opinion["reason"], "primary_reason")
    for key in ("support_refs", "contradiction_refs", "unknowns"):
        values = opinion[key]
        if not isinstance(values, list) or len(values) > 100 or len(values) != len(set(values)):
            raise OrderReferenceV2Error("committed_primary_opinion_list_invalid")
        if any(not isinstance(item, str) for item in values):
            raise OrderReferenceV2Error("committed_primary_opinion_list_invalid")
    if any(
        not exact_sha(ref)
        for ref in opinion["support_refs"] + opinion["contradiction_refs"]
    ):
        raise OrderReferenceV2Error("committed_primary_evidence_ref_invalid")
    for reason in opinion["unknowns"]:
        _text(reason, "primary_unknown_reason")
    if "TRUE" in primary.values() and not opinion["support_refs"]:
        raise OrderReferenceV2Error("positive_primary_requires_support")
    if "UNKNOWN" in primary.values() and not opinion["unknowns"]:
        raise OrderReferenceV2Error("unknown_primary_requires_reason")
    if digest(opinion) != value["primary_opinion_digest"]:
        raise OrderReferenceV2Error("primary_commit_digest")
    if not exact_sha(value["primary_opinion_digest"]):
        raise OrderReferenceV2Error("primary_commit_digest")
    return value


def build_recent_packet(
    case: Mapping[str, Any], primary_commit: Mapping[str, Any]
) -> dict[str, Any]:
    """Reveal explicit recent facts only after a valid immutable primary commit exists."""
    case_id = _case_identity(case)
    commit = validate_primary_commit(primary_commit)
    opinion = commit["opinion"]
    if opinion["case_id"] != case_id:
        raise OrderReferenceV2Error("recent_packet_primary_case_mismatch")
    recent = case.get("recent")
    if not isinstance(recent, dict) or set(recent) != {"width_change", "center_shift"}:
        raise OrderReferenceV2Error("recent_fact_shape")
    packet = _plain({
        "schema_version": "quanthus.stage-order-recent-packet.v2",
        "protocol_digest": PROTOCOL_DIGEST,
        "case_id": case_id,
        "reviewer_id": opinion["reviewer_id"],
        "role": opinion["role"],
        "primary_opinion_digest": commit["primary_opinion_digest"],
        "recent_available": case["recent_available"],
        "recent_window_sessions": 10,
        "recent_facts": recent,
        "may_modify_primary": False,
        "candidate_output_visible": False,
        "future_rows_visible": False,
    })
    return {**packet, "packet_digest": digest(packet)}


def commit_recent_modifier(packet: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "case_id", "protocol_digest", "packet_digest",
        "primary_opinion_digest", "reviewer_id", "role", "recent",
        "support_refs", "contradiction_refs", "reason", "unknowns",
    }
    modifier = _exact_fields(dict(raw), expected, "recent_modifier")
    if (
        modifier["schema_version"] != RECENT_MODIFIER_SCHEMA
        or modifier["case_id"] != packet.get("case_id")
        or modifier["protocol_digest"] != PROTOCOL_DIGEST
        or modifier["packet_digest"] != packet.get("packet_digest")
        or modifier["primary_opinion_digest"] != packet.get("primary_opinion_digest")
        or modifier["reviewer_id"] != packet.get("reviewer_id")
        or modifier["role"] != packet.get("role")
    ):
        raise OrderReferenceV2Error("recent_modifier_binding")
    recent = _exact_fields(modifier["recent"], {"width", "center", "relation"}, "recent")
    if (
        recent["width"] not in RECENT_WIDTH
        or recent["center"] not in RECENT_CENTER
        or recent["relation"] not in RECENT_RELATION
    ):
        raise OrderReferenceV2Error("recent_modifier_value")
    available_refs = {
        item["revision_id"]
        for item in packet["recent_facts"].values()
        if item.get("revision_id") is not None
    }
    for key in ("support_refs", "contradiction_refs", "unknowns"):
        values = modifier[key]
        if (
            not isinstance(values, list)
            or len(values) > 100
            or len(values) != len(set(values))
        ):
            raise OrderReferenceV2Error("recent_modifier_list_invalid")
        if any(not isinstance(value, str) for value in values):
            raise OrderReferenceV2Error("recent_modifier_list_invalid")
    if not set(modifier["support_refs"] + modifier["contradiction_refs"]) <= available_refs:
        raise OrderReferenceV2Error("recent_modifier_reference_outside_recent_packet")
    _text(modifier["reason"], "recent_reason")
    for reason in modifier["unknowns"]:
        _text(reason, "recent_unknown_reason")
    if not packet["recent_available"] and (
        recent["width"] != "UNAVAILABLE" or recent["center"] != "UNAVAILABLE"
    ):
        raise OrderReferenceV2Error("unavailable_recent_must_remain_unavailable")
    if packet["recent_available"] and "UNAVAILABLE" in {recent["width"], recent["center"]}:
        raise OrderReferenceV2Error("available_recent_cannot_be_unavailable")
    normalized = _plain(modifier)
    return {
        "schema_version": RECENT_COMMIT_SCHEMA,
        "modifier": normalized,
        "recent_modifier_digest": digest(normalized),
    }


def validate_recent_commit(commit: Mapping[str, Any]) -> dict[str, Any]:
    value = _exact_fields(
        dict(commit), {"schema_version", "modifier", "recent_modifier_digest"}, "recent_commit"
    )
    if value["schema_version"] != RECENT_COMMIT_SCHEMA:
        raise OrderReferenceV2Error("recent_commit_schema")
    modifier = _exact_fields(
        value["modifier"],
        {
            "schema_version", "case_id", "protocol_digest", "packet_digest",
            "primary_opinion_digest", "reviewer_id", "role", "recent",
            "support_refs", "contradiction_refs", "reason", "unknowns",
        },
        "committed_recent_modifier",
    )
    if (
        modifier["schema_version"] != RECENT_MODIFIER_SCHEMA
        or modifier["protocol_digest"] != PROTOCOL_DIGEST
        or not exact_sha(modifier["case_id"])
        or not exact_sha(modifier["packet_digest"])
        or not exact_sha(modifier["primary_opinion_digest"])
        or modifier["role"] not in ROLES
    ):
        raise OrderReferenceV2Error("committed_recent_modifier_identity")
    _text(modifier["reviewer_id"], "reviewer_id", 200)
    recent = _exact_fields(modifier["recent"], {"width", "center", "relation"}, "recent")
    if (
        recent["width"] not in RECENT_WIDTH
        or recent["center"] not in RECENT_CENTER
        or recent["relation"] not in RECENT_RELATION
    ):
        raise OrderReferenceV2Error("recent_modifier_value")
    _text(modifier["reason"], "recent_reason")
    for key in ("support_refs", "contradiction_refs", "unknowns"):
        values = modifier[key]
        if not isinstance(values, list) or len(values) > 100 or len(values) != len(set(values)):
            raise OrderReferenceV2Error("committed_recent_modifier_list_invalid")
        if any(not isinstance(item, str) for item in values):
            raise OrderReferenceV2Error("committed_recent_modifier_list_invalid")
    if any(
        not exact_sha(ref)
        for ref in modifier["support_refs"] + modifier["contradiction_refs"]
    ):
        raise OrderReferenceV2Error("committed_recent_evidence_ref_invalid")
    for reason in modifier["unknowns"]:
        _text(reason, "recent_unknown_reason")
    if digest(modifier) != value["recent_modifier_digest"]:
        raise OrderReferenceV2Error("recent_commit_digest")
    return value


def project_atomic_primary(primary: Mapping[str, str], *, available: bool = True) -> dict[str, Any]:
    atomic = _exact_fields(dict(primary), set(PRIMARY_PROPOSITIONS), "atomic_primary")
    if any(value not in TRUTH_VALUES for value in atomic.values()):
        raise OrderReferenceV2Error("atomic_primary_truth_value")
    if not available:
        return {"status": "INPUT_UNAVAILABLE", "reason_codes": ["primary_input_unavailable"]}
    instability = atomic["structural_instability_20"]
    relocation = atomic["coherent_directional_relocation_20"]
    order = atomic["orderly_consolidation_20"]
    if order == "TRUE" and (instability == "TRUE" or relocation == "TRUE"):
        return {
            "status": "AMBIGUOUS_CONFLICT",
            "reason_codes": ["positive_order_conflicts_with_instability_or_relocation"],
        }
    if (instability, relocation, order) == ("TRUE", "FALSE", "FALSE"):
        return {"status": "STAGE_0", "reason_codes": ["positive_direction_neutral_instability"]}
    if (instability, relocation, order) == ("FALSE", "FALSE", "TRUE"):
        return {"status": "STAGE_2", "reason_codes": ["positive_orderly_consolidation"]}
    if relocation == "TRUE" and order == "FALSE":
        reasons = ["coherent_directional_relocation_outside_stage_0_2"]
        if instability == "TRUE":
            reasons.append("mixed_directional_instability")
        return {"status": "NEITHER", "reason_codes": reasons}
    if instability == "FALSE" and order == "FALSE":
        return {"status": "NEITHER", "reason_codes": ["no_positive_stage_0_or_stage_2_proposition"]}
    return {"status": "AMBIGUOUS", "reason_codes": ["unknown_dependent_primary_resolution"]}


def resolve_primary_roles(
    primary_commits: Sequence[Mapping[str, Any]], *, primary_available: bool = True
) -> dict[str, Any]:
    if len(primary_commits) != 2:
        raise OrderReferenceV2Error("both_semantic_roles_required")
    commits = [validate_primary_commit(item) for item in primary_commits]
    opinions = [item["opinion"] for item in commits]
    if {item["role"] for item in opinions} != set(ROLES):
        raise OrderReferenceV2Error("both_semantic_roles_required")
    if len({item["case_id"] for item in opinions}) != 1:
        raise OrderReferenceV2Error("primary_role_case_mismatch")
    if len({item["packet_digest"] for item in opinions}) != 1:
        raise OrderReferenceV2Error("primary_role_packet_mismatch")
    if len({item["reviewer_id"] for item in opinions}) != 2:
        raise OrderReferenceV2Error("reviewer_cannot_impersonate_two_roles")
    projected = [
        project_atomic_primary(item["primary"], available=primary_available)
        for item in opinions
    ]
    if projected[0]["status"] != projected[1]["status"]:
        status = "AMBIGUOUS"
        reasons = ["cross_role_primary_disagreement"]
    else:
        status = projected[0]["status"]
        reasons = list(dict.fromkeys(projected[0]["reason_codes"] + projected[1]["reason_codes"]))
    return {
        "case_id": opinions[0]["case_id"],
        "primary_reference": status,
        "reason_codes": reasons,
        "role_statuses": {
            item["role"]: value["status"]
            for item, value in zip(opinions, projected, strict=True)
        },
        "primary_opinion_digests": [item["primary_opinion_digest"] for item in commits],
    }


def resolve_case(
    primary_commits: Sequence[Mapping[str, Any]],
    recent_commits: Sequence[Mapping[str, Any]],
    *,
    primary_available: bool = True,
) -> dict[str, Any]:
    """Resolve primary unchanged by recent modifiers and retain modifiers separately."""
    primary = resolve_primary_roles(primary_commits, primary_available=primary_available)
    if len(recent_commits) != 2:
        raise OrderReferenceV2Error("both_recent_role_modifiers_required")
    recent = [validate_recent_commit(item) for item in recent_commits]
    modifiers = [item["modifier"] for item in recent]
    if {item["role"] for item in modifiers} != set(ROLES):
        raise OrderReferenceV2Error("both_recent_role_modifiers_required")
    if {item["case_id"] for item in modifiers} != {primary["case_id"]}:
        raise OrderReferenceV2Error("recent_role_case_mismatch")
    primary_by_role = {
        commit["opinion"]["role"]: commit["primary_opinion_digest"]
        for commit in (validate_primary_commit(item) for item in primary_commits)
    }
    for item in modifiers:
        if item["primary_opinion_digest"] != primary_by_role[item["role"]]:
            raise OrderReferenceV2Error("recent_modifier_wrong_primary_commit")
    return {
        **primary,
        "recent_relations": {item["role"]: item["recent"] for item in modifiers},
        "recent_modifier_digests": [item["recent_modifier_digest"] for item in recent],
        "recent_can_modify_primary": False,
    }
