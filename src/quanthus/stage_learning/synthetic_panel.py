"""Synthetic expert-panel governance for development-only stock-stage semantic review."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SyntheticPanelError(ValueError):
    """Raised when the synthetic expert-panel contract is invalid."""


@dataclass(frozen=True)
class SyntheticPersona:
    persona_id: str
    display_name: str
    style_cluster: str
    public_style_anchor: str | None
    semantic_vote_authorized: bool


@dataclass(frozen=True)
class SyntheticAssignment:
    context_id: str
    primary_a: str
    primary_b: str
    adjudicator: str
    auditor: str
    evidence_tier: str = "SYNTHETIC_EXPERT_CONSENSUS"


def load_synthetic_panel(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticPanelError(f"cannot read synthetic panel: {path}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != (
        "quanthus.stage-synthetic-expert-panel.v1"
    ):
        raise SyntheticPanelError("synthetic panel schema differs")
    if raw.get("evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS":
        raise SyntheticPanelError("synthetic panel evidence tier differs")
    personas = raw.get("personas")
    assignment = raw.get("assignment")
    development = raw.get("development_use")
    non_claims = raw.get("non_claims")
    if not isinstance(personas, list) or not personas:
        raise SyntheticPanelError("synthetic personas are missing")
    if not isinstance(assignment, Mapping) or not isinstance(development, Mapping):
        raise SyntheticPanelError("synthetic panel assignment/development contract is missing")
    has_human_claim_ceiling = isinstance(non_claims, list) and any(
        "not HUMAN_EXPERT_REFERENCE" in str(value) for value in non_claims
    )
    if not has_human_claim_ceiling:
        raise SyntheticPanelError("synthetic-vs-human claim ceiling is missing")
    ids: set[str] = set()
    clusters: dict[str, str] = {}
    voters: set[str] = set()
    for item in personas:
        if not isinstance(item, Mapping):
            raise SyntheticPanelError("synthetic persona must be an object")
        persona_id = str(item.get("persona_id") or "")
        cluster = str(item.get("style_cluster") or "")
        if not persona_id or not cluster or persona_id in ids:
            raise SyntheticPanelError("synthetic persona identity is invalid or duplicated")
        ids.add(persona_id)
        clusters[persona_id] = cluster
        if item.get("semantic_vote_authorized", True) is not False:
            voters.add(persona_id)
        if item.get("public_style_anchor") is not None and item.get(
            "not_identity_or_personal_view"
        ) is not True:
            raise SyntheticPanelError(
                "public style anchors require explicit non-identity disclaimer"
            )
    primary_a = str(assignment.get("primary_a") or "")
    primary_b = str(assignment.get("primary_b") or "")
    adjudicator = str(assignment.get("adjudicator") or "")
    challenge = tuple(str(v) for v in assignment.get("institutional_challenge_reviewers", ()))
    auditor = str(assignment.get("audit_persona") or "")
    declared = (primary_a, primary_b, adjudicator, *challenge, auditor)
    if not all(declared) or not set(declared) <= ids:
        raise SyntheticPanelError("synthetic panel assignment references unknown personas")
    if len({primary_a, primary_b, adjudicator}) != 3:
        raise SyntheticPanelError("primary and adjudicator semantic personas must be distinct")
    if not {primary_a, primary_b, adjudicator, *challenge} <= voters or auditor in voters:
        raise SyntheticPanelError("semantic-vote/audit authority differs")
    if clusters[primary_a] == clusters[primary_b]:
        raise SyntheticPanelError("synthetic primaries must come from different style clusters")
    if assignment.get("primary_reviews_are_mutually_hidden") is not True:
        raise SyntheticPanelError("synthetic primary reviews must be mutually hidden")
    bounded_challenge = assignment.get(
        "institutional_challenge_does_not_override_primary_resolution_without_new_versioned_policy"
    )
    if bounded_challenge is not True:
        raise SyntheticPanelError("institutional challenge authority is not bounded")
    if development.get("formal_qualification_allowed") is not False:
        raise SyntheticPanelError("synthetic panel cannot authorize formal qualification")
    if development.get("r0_r1_development_learning_allowed_after_complete_resolution") is not True:
        raise SyntheticPanelError("synthetic panel does not authorize bounded development learning")
    return raw


def assign_synthetic_reviewers(panel: Mapping[str, Any], context_id: str) -> SyntheticAssignment:
    if not context_id.startswith("sha256:"):
        raise SyntheticPanelError("context_id must be a digest-bound identity")
    assignment = panel["assignment"]
    primary_a = str(assignment["primary_a"])
    primary_b = str(assignment["primary_b"])
    adjudicator = str(assignment["adjudicator"])
    auditor = str(assignment["audit_persona"])
    return SyntheticAssignment(context_id, primary_a, primary_b, adjudicator, auditor)


def assignment_manifest(panel: Mapping[str, Any], context_ids: Sequence[str]) -> dict[str, Any]:
    if len(set(context_ids)) != len(context_ids):
        raise SyntheticPanelError("context identities contain duplicates")
    assignments = [assign_synthetic_reviewers(panel, context_id) for context_id in context_ids]
    return {
        "schema_version": "quanthus.stage-synthetic-review-assignment.v1",
        "panel_id": panel["panel_id"],
        "evidence_tier": panel["evidence_tier"],
        "context_count": len(assignments),
        "assignments": [item.__dict__ for item in assignments],
        "formal_qualification_allowed": False,
    }
