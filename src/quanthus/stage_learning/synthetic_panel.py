"""Deterministic synthetic expert-panel review for development-only stock-stage semantics.

The panel is intentionally not a human-reference substitute.  Public-figure names in the frozen
configuration are style anchors only.  Each persona executes a versioned T-only policy over the
review packet; primary policies do not see each other's outputs, disagreements are adjudicated by
one third policy, institutional policies only emit challenge notes, and the quantitative auditor
may veto malformed or non-T-only evidence but never casts a semantic vote.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.stage_learning.reference import ReferenceKind, ReviewerJudgement


class SyntheticPanelError(ValueError):
    """Raised when the synthetic expert-panel contract or T-only packet is invalid."""


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


@dataclass(frozen=True)
class PacketFeatures:
    context_id: str
    market_date: date
    observation: str
    phase: str
    hold_count: int
    distance_pct: Decimal | None
    activity_ratio: Decimal | None
    ret5_pct: Decimal | None
    ret20_pct: Decimal | None
    evidence_digest: str


_DISALLOWED_PACKET_KEYS = {
    "engineering_candidate_state",
    "engineering_primary_stage",
    "future_prices",
    "future_settlement",
    "strategy_result",
}
_ALLOWED_ADJACENT = {
    "consolidation_like",
    "trend_background",
    "breakout_failure",
    "other",
}


def load_synthetic_panel(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticPanelError(f"cannot read synthetic panel: {path}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") not in {
        "quanthus.stage-synthetic-expert-panel.v1",
        "quanthus.stage-synthetic-expert-panel.v2",
    }:
        raise SyntheticPanelError("synthetic panel schema differs")
    if raw["schema_version"].endswith(".v1"):
        raw.setdefault("semantic_policy_version", "stage-first-group-synthetic-policy-v1")
    elif raw.get("semantic_policy_version") != "stage-first-group-synthetic-policy-v2":
        raise SyntheticPanelError("synthetic panel semantic policy version differs")
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


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SyntheticPanelError(
            "synthetic review packet contains invalid decimal evidence"
        ) from exc
    if not result.is_finite():
        raise SyntheticPanelError("synthetic review packet contains non-finite evidence")
    return result


def _pct_change(values: Sequence[Decimal], periods: int) -> Decimal | None:
    if periods < 1 or len(values) <= periods or values[-periods - 1] == 0:
        return None
    return (values[-1] / values[-periods - 1] - Decimal(1)) * Decimal(100)


def _packet_features(packet: Mapping[str, Any], evidence_digest: str) -> PacketFeatures:
    context_id = str(packet.get("context_id") or "")
    if not context_id.startswith("sha256:") or not evidence_digest.startswith("sha256:"):
        raise SyntheticPanelError("synthetic review packet identity is not digest-bound")
    try:
        market_date = date.fromisoformat(str(packet["market_date"]))
        process = packet["t_only_process_facts"]
        frozen = packet["frozen_reference"]
    except (KeyError, ValueError) as exc:
        raise SyntheticPanelError("synthetic review packet core fields are invalid") from exc
    if not isinstance(process, Mapping) or not isinstance(frozen, Mapping):
        raise SyntheticPanelError("synthetic review packet core evidence is malformed")
    close = _decimal(process.get("close"))
    frozen_high = _decimal(frozen.get("frozen_high"))
    distance_pct = None
    if close is not None and frozen_high is not None and frozen_high > 0:
        distance_pct = (close / frozen_high - Decimal(1)) * Decimal(100)
    activity = packet.get("activity")
    activity_ratio = None
    if isinstance(activity, Mapping):
        activity_ratio = _decimal(activity.get("volume_ratio"))
    history = packet.get("history")
    if not isinstance(history, list) or not history:
        raise SyntheticPanelError("synthetic review packet history is missing")
    closes = [
        value
        for item in history
        if isinstance(item, Mapping)
        for value in [_decimal(item.get("close"))]
        if value is not None
    ]
    try:
        hold_count = int(process["hold_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SyntheticPanelError("synthetic review packet hold_count is invalid") from exc
    return PacketFeatures(
        context_id=context_id,
        market_date=market_date,
        observation=str(process.get("observation") or ""),
        phase=str(process.get("phase") or ""),
        hold_count=hold_count,
        distance_pct=distance_pct,
        activity_ratio=activity_ratio,
        ret5_pct=_pct_change(closes, 5),
        ret20_pct=_pct_change(closes, 20),
        evidence_digest=evidence_digest,
    )


def audit_review_packet(packet: Mapping[str, Any], evidence_digest: str) -> dict[str, Any]:
    """Audit only evidence closure; never cast a stage vote."""
    features = _packet_features(packet, evidence_digest)
    top_level = set(packet)
    leaked = sorted(top_level & _DISALLOWED_PACKET_KEYS)
    history = packet.get("history")
    future_dates: list[str] = []
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, Mapping):
                continue
            observed = item.get("market_date")
            if observed is None:
                continue
            try:
                observed_date = date.fromisoformat(str(observed))
            except ValueError:
                future_dates.append(str(observed))
                continue
            if observed_date > features.market_date:
                future_dates.append(observed_date.isoformat())
    forbidden_declared = packet.get("forbidden_fields")
    expected_forbidden = sorted(_DISALLOWED_PACKET_KEYS)
    declared = sorted(str(item) for item in forbidden_declared) if isinstance(
        forbidden_declared, list
    ) else []
    problems: list[str] = []
    if leaked:
        problems.append("forbidden_field_present")
    if future_dates:
        problems.append("post_t_history_present")
    if declared != expected_forbidden:
        problems.append("forbidden_field_contract_drift")
    return {
        "schema_version": "quanthus.stage-synthetic-review-audit.v1",
        "context_id": features.context_id,
        "evidence_digest": evidence_digest,
        "veto": bool(problems),
        "reason_codes": problems or ["t_only_evidence_contract_pass"],
        "future_dates": future_dates,
        "semantic_vote": None,
    }


def _judgement(
    features: PacketFeatures,
    persona_id: str,
    role: str,
    kind: ReferenceKind,
    stages: tuple[int, ...],
    adjacent: tuple[str, ...],
    reasons: Sequence[str],
) -> ReviewerJudgement:
    if any(value not in _ALLOWED_ADJACENT for value in adjacent):
        raise SyntheticPanelError("synthetic adjacent-context token is not admitted")
    return ReviewerJudgement(
        context_id=features.context_id,
        reviewer_id=persona_id,
        reviewer_role=role,
        reference_kind=kind,
        supported_stage_codes=stages,
        adjacent_contexts=adjacent,
        reason_codes=tuple(reasons),
        evidence_digest=features.evidence_digest,
    )


def _ambiguous_hold(
    features: PacketFeatures,
    persona_id: str,
    role: str,
    *reasons: str,
) -> ReviewerJudgement:
    return _judgement(
        features,
        persona_id,
        role,
        ReferenceKind.AMBIGUOUS_SET,
        (3, 4),
        (),
        reasons or ("hold_acceptance_not_yet_resolved",),
    )


def review_packet(
    packet: Mapping[str, Any],
    *,
    evidence_digest: str,
    persona_id: str,
    reviewer_role: str,
    policy_version: str = "stage-first-group-synthetic-policy-v1",
) -> ReviewerJudgement:
    """Execute one frozen persona policy over one T-only packet."""
    features = _packet_features(packet, evidence_digest)
    audit = audit_review_packet(packet, evidence_digest)
    if audit["veto"]:
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.UNJUDGEABLE,
            (),
            (),
            ("audit_veto",),
        )
    observation = features.observation
    activity = features.activity_ratio
    distance = features.distance_pct
    ret5 = features.ret5_pct
    ret20 = features.ret20_pct
    hold = features.hold_count

    if observation in {"below", "lost"} or features.phase == "below":
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.DEFINITE,
            (),
            ("breakout_failure",),
            ("same_anchor_acceptance_lost",),
        )
    if observation == "reclaimed" or features.phase == "reclaimed":
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.AMBIGUOUS_SET,
            (),
            ("breakout_failure", "other"),
            ("reclaim_after_prior_loss_requires_separate_repair_semantics",),
        )
    if observation == "started":
        if activity is None:
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.UNJUDGEABLE,
                (),
                (),
                ("activity_evidence_unavailable",),
            )
        if policy_version == "stage-first-group-synthetic-policy-v1":
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.DEFINITE,
                (3,),
                (),
                ("first_same_anchor_probe_breakout", "t_only_activity_visible"),
            )
        if policy_version != "stage-first-group-synthetic-policy-v2":
            raise SyntheticPanelError("synthetic semantic policy version is unsupported")
        if persona_id.startswith("youzi-emotion-cycle-"):
            if activity >= Decimal("1.15") or (
                activity >= Decimal("1.05") and ret5 is not None and ret5 >= Decimal("3")
            ):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.DEFINITE,
                    (3,),
                    (),
                    ("probe_has_short_tactical_activity_support",),
                )
            if activity >= Decimal("0.95"):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.AMBIGUOUS_SET,
                    (3,),
                    ("consolidation_like",),
                    ("probe_activity_is_borderline_for_short_tactical_acceptance",),
                )
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.DEFINITE,
                (),
                ("consolidation_like",),
                ("probe_lacks_short_tactical_activity_support",),
            )
        if persona_id.startswith("youzi-capacity-trend-"):
            if activity >= Decimal("1.25") or (
                activity >= Decimal("1.10") and ret20 is not None and ret20 >= Decimal("8")
            ):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.DEFINITE,
                    (3,),
                    (),
                    ("probe_has_capacity_or_medium_term_activity_support",),
                )
            if activity >= Decimal("1.00"):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.AMBIGUOUS_SET,
                    (3,),
                    ("consolidation_like",),
                    ("probe_activity_is_borderline_for_capacity_acceptance",),
                )
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.DEFINITE,
                (),
                ("consolidation_like",),
                ("probe_lacks_capacity_activity_support",),
            )
        if persona_id.startswith("youzi-breakout-tempo-"):
            if activity >= Decimal("1.20") or (
                activity >= Decimal("1.05") and ret5 is not None and ret5 >= Decimal("5")
            ):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.DEFINITE,
                    (3,),
                    (),
                    ("probe_has_breakout_tempo_activity_support",),
                )
            if activity >= Decimal("1.00"):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.AMBIGUOUS_SET,
                    (3,),
                    ("consolidation_like",),
                    ("probe_tempo_is_not_yet_decisive",),
                )
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.DEFINITE,
                (),
                ("consolidation_like",),
                ("probe_activity_is_too_weak_for_breakout_tempo",),
            )
        raise SyntheticPanelError(f"persona has no frozen stage-3 policy: {persona_id}")
    if observation != "held" or features.phase != "active" or distance is None:
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.UNJUDGEABLE,
            (),
            (),
            ("first_group_process_not_judgeable",),
        )
    if activity is None:
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.UNJUDGEABLE,
            (),
            (),
            ("activity_evidence_unavailable",),
        )

    # Emotion-cycle / near-term acceptance archetype.
    if persona_id.startswith("youzi-emotion-cycle-"):
        if hold == 1:
            if activity >= Decimal("1.35") and distance >= Decimal("1.0") and (
                ret5 is None or ret5 >= 0
            ):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.DEFINITE,
                    (4,),
                    (),
                    ("fast_acceptance_with_volume_and_follow_through",),
                )
            return _ambiguous_hold(
                features,
                persona_id,
                reviewer_role,
                "one_session_hold_needs_stronger_acceptance",
            )
        if hold >= 80 and distance >= Decimal("15"):
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.COEXISTENCE,
                (4,),
                ("trend_background",),
                ("accepted_breakout_coexists_with_mature_trend",),
            )
        if (
            activity < Decimal("0.45")
            and ret20 is not None
            and ret20 < Decimal("-5")
            and distance < Decimal("5")
        ):
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.AMBIGUOUS_SET,
                (4,),
                ("trend_background",),
                ("price_still_above_anchor_but_current_acceptance_is_weak",),
            )
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.DEFINITE,
            (4,),
            (),
            ("same_anchor_hold_established",),
        )

    # Capacity / larger-fund trend archetype.
    if persona_id.startswith("youzi-capacity-trend-"):
        if hold == 1:
            if distance >= Decimal("2") and (
                activity >= Decimal("1.50") or (ret20 is not None and ret20 >= Decimal("10"))
            ):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.DEFINITE,
                    (4,),
                    (),
                    ("one_session_acceptance_has_capacity_or_trend_confirmation",),
                )
            return _ambiguous_hold(
                features,
                persona_id,
                reviewer_role,
                "one_session_hold_insufficient_for_capacity_acceptance",
            )
        if hold >= 40 and distance >= Decimal("8"):
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.COEXISTENCE,
                (4,),
                ("trend_background",),
                ("accepted_breakout_has_become_capacity_trend_background",),
            )
        if ret20 is not None and ret20 < Decimal("-5") and distance < Decimal("1"):
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.AMBIGUOUS_SET,
                (4,),
                ("trend_background",),
                ("anchor_hold_is_marginal_against_weak_medium_term_tape",),
            )
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.DEFINITE,
            (4,),
            (),
            ("same_anchor_acceptance_has_persistence",),
        )

    # Breakout-tempo adjudicator.
    if persona_id.startswith("youzi-breakout-tempo-"):
        if hold == 1:
            if activity >= Decimal("1.50") and distance >= Decimal("2"):
                return _judgement(
                    features,
                    persona_id,
                    reviewer_role,
                    ReferenceKind.DEFINITE,
                    (4,),
                    (),
                    ("strong_one_session_follow_through_counts_as_acceptance",),
                )
            return _ambiguous_hold(
                features,
                persona_id,
                reviewer_role,
                "tempo_requires_more_than_weak_one_session_hold",
            )
        if hold >= 60 and distance >= Decimal("10"):
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.COEXISTENCE,
                (4,),
                ("trend_background",),
                ("mature_accepted_breakout_overlaps_trend_background",),
            )
        if (
            activity < Decimal("0.40")
            and ret20 is not None
            and ret20 < Decimal("-10")
            and distance < Decimal("5")
        ):
            return _judgement(
                features,
                persona_id,
                reviewer_role,
                ReferenceKind.AMBIGUOUS_SET,
                (4,),
                ("trend_background",),
                ("weak_tape_makes_persistent_acceptance_uncertain",),
            )
        return _judgement(
            features,
            persona_id,
            reviewer_role,
            ReferenceKind.DEFINITE,
            (4,),
            (),
            ("tempo_confirms_same_anchor_hold",),
        )

    raise SyntheticPanelError(f"persona has no frozen semantic policy: {persona_id}")


def _signature(value: ReviewerJudgement) -> tuple[Any, ...]:
    return (value.reference_kind, value.supported_stage_codes, value.adjacent_contexts)


def institutional_challenge(
    packet: Mapping[str, Any],
    *,
    evidence_digest: str,
    persona_id: str,
    resolved: ReviewerJudgement,
) -> dict[str, Any]:
    features = _packet_features(packet, evidence_digest)
    reasons: list[str] = []
    if 4 in resolved.supported_stage_codes:
        if features.ret20_pct is not None and features.ret20_pct < Decimal("-5"):
            reasons.append("medium_term_durability_weak")
        if features.activity_ratio is not None and features.activity_ratio < Decimal("0.50"):
            reasons.append("current_activity_thin")
        if features.hold_count >= 60:
            reasons.append("stage4_stage5_overlap_risk")
        if features.distance_pct is not None and features.distance_pct >= Decimal("50"):
            reasons.append("late_trend_state_overlap_risk")
        if features.hold_count == 1:
            reasons.append("single_session_acceptance_needs_caution")
    if (
        persona_id.startswith("institutional-cycle-tolerance-")
        and features.observation in {"below", "lost"}
        and features.distance_pct is not None
        and features.distance_pct > Decimal("-1")
    ):
        reasons.append("near_anchor_loss_may_be_normal_pullback_context")
    return {
        "schema_version": "quanthus.stage-synthetic-institutional-challenge.v1",
        "context_id": features.context_id,
        "reviewer_id": persona_id,
        "evidence_digest": evidence_digest,
        "challenge": bool(reasons),
        "reason_codes": reasons or ["no_additional_institutional_challenge"],
        "overrides_resolution": False,
    }


def evaluate_panel(
    panel: Mapping[str, Any],
    packets: Sequence[Mapping[str, Any]],
    evidence_by_context: Mapping[str, str],
) -> dict[str, Any]:
    """Execute the full panel deterministically without reading any future settlement."""
    assignment = panel["assignment"]
    policy_version = str(panel.get("semantic_policy_version") or "")
    if policy_version not in {
        "stage-first-group-synthetic-policy-v1",
        "stage-first-group-synthetic-policy-v2",
    }:
        raise SyntheticPanelError("synthetic panel semantic policy version is unsupported")
    primary_a_id = str(assignment["primary_a"])
    primary_b_id = str(assignment["primary_b"])
    adjudicator_id = str(assignment["adjudicator"])
    institutional_ids = tuple(
        str(value) for value in assignment["institutional_challenge_reviewers"]
    )
    packet_by_id: dict[str, Mapping[str, Any]] = {}
    for packet in packets:
        context_id = str(packet.get("context_id") or "")
        if context_id in packet_by_id:
            raise SyntheticPanelError("synthetic panel received duplicate review packets")
        packet_by_id[context_id] = packet
    if set(packet_by_id) != set(evidence_by_context):
        raise SyntheticPanelError("synthetic panel packet identities differ from sample manifest")

    audits: list[dict[str, Any]] = []
    primary_a: list[ReviewerJudgement] = []
    primary_b: list[ReviewerJudgement] = []
    disagreements: list[str] = []
    for context_id in sorted(packet_by_id):
        packet = packet_by_id[context_id]
        evidence = evidence_by_context[context_id]
        audit = audit_review_packet(packet, evidence)
        audits.append(audit)
        if audit["veto"]:
            raise SyntheticPanelError(f"quantitative audit veto: {context_id}")
        left = review_packet(
            packet,
            evidence_digest=evidence,
            persona_id=primary_a_id,
            reviewer_role="primary",
            policy_version=policy_version,
        )
        right = review_packet(
            packet,
            evidence_digest=evidence,
            persona_id=primary_b_id,
            reviewer_role="primary",
            policy_version=policy_version,
        )
        primary_a.append(left)
        primary_b.append(right)
        if _signature(left) != _signature(right):
            disagreements.append(context_id)

    adjudicator: list[ReviewerJudgement] = []
    left_by_id = {item.context_id: item for item in primary_a}
    right_by_id = {item.context_id: item for item in primary_b}
    resolved_for_challenge: dict[str, ReviewerJudgement] = {}
    for context_id in sorted(packet_by_id):
        if context_id in disagreements:
            judgement = review_packet(
                packet_by_id[context_id],
                evidence_digest=evidence_by_context[context_id],
                persona_id=adjudicator_id,
                reviewer_role="adjudicator",
                policy_version=policy_version,
            )
            adjudicator.append(judgement)
            resolved_for_challenge[context_id] = judgement
        else:
            resolved_for_challenge[context_id] = left_by_id[context_id]
            if _signature(left_by_id[context_id]) != _signature(right_by_id[context_id]):
                raise SyntheticPanelError("primary agreement accounting differs")

    agreements = sorted(set(packet_by_id) - set(disagreements))
    challenge_sample_count = max(1, (len(agreements) + 4) // 5) if agreements else 0
    challenge_sample = sorted(
        agreements,
        key=lambda context_id: hashlib.sha256(
            f"{panel['panel_id']}|institutional-audit|{context_id}".encode()
        ).hexdigest(),
    )[:challenge_sample_count]
    challenge_contexts = sorted(set(disagreements) | set(challenge_sample))
    challenges: list[dict[str, Any]] = []
    for context_id in challenge_contexts:
        for persona_id in institutional_ids:
            challenges.append(
                institutional_challenge(
                    packet_by_id[context_id],
                    evidence_digest=evidence_by_context[context_id],
                    persona_id=persona_id,
                    resolved=resolved_for_challenge[context_id],
                )
            )

    return {
        "primary_a": tuple(primary_a),
        "primary_b": tuple(primary_b),
        "adjudicator": tuple(adjudicator),
        "audits": tuple(audits),
        "challenges": tuple(challenges),
        "disagreement_context_ids": tuple(sorted(disagreements)),
        "challenge_context_ids": tuple(challenge_contexts),
        "review_engine": policy_version,
        "single_model_role_isolated_policy_authoring": True,
        "row_execution_deterministic": True,
        "future_settlement_read": False,
        "formal_qualification_allowed": False,
    }
