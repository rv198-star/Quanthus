"""Bounded, offline support-risk overlay on an unchanged B held-position path.

Neither the accepted PositionRiskPolicy nor its card is modified. Both equivalent
representations consume the same support confirmation history. No fills or capital
path are simulated here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from quanthus.aggregator import GovernedAction, aggregate_governance
from quanthus.expert_protocols import (
    EvidenceKind,
    ExpertApplicability,
    ExpertEvidenceRef,
    RequiredAction,
    VetoAuthority,
    VetoOutput,
)
from quanthus.expert_strategy.context import FormalExpertContext
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.runner import FormalStrategyDecision, FormalStrategyRunner
from quanthus.feature_registry import FeatureCutoffRule, FeatureLineage, FeatureSourceEvidence
from quanthus.label_registry import FeatureFact
from quanthus.stage_projection import StageProjectionContract, project_support_evolution_stages
from quanthus.structure_support_facts import SupportFactsResult
from quanthus.structure_v2_inputs import digest

POLICY = "shadow.support-failure-reduce-only.v1"


def _evidence(raw: dict[str, Any]) -> ExpertEvidenceRef:
    return ExpertEvidenceRef(
        EvidenceKind(raw["kind"]), raw["evidence_id"], raw["version"], raw["digest"]
    )


def restore_context(raw: dict[str, Any]) -> FormalExpertContext:
    """Decode only the two frozen decimal features, retaining full original lineage."""
    if (
        raw["label_results"]
        or {r["feature_id"] for r in raw["feature_facts"]}
        != {"price.rsi14", "risk.realized_volatility20"}
        or len(raw["feature_facts"]) != 2
    ):
        raise ValueError("SHADOW_CONTEXT_FEATURE_CONTRACT")
    features = []
    for value in raw["feature_facts"]:
        lineage = dict(value["lineage"])
        lineage["market_date"] = date.fromisoformat(lineage["market_date"])
        lineage["knowledge_cutoff"] = datetime.fromisoformat(lineage["knowledge_cutoff"])
        lineage["cutoff_rule"] = FeatureCutoffRule(lineage["cutoff_rule"])
        lineage["sources"] = tuple(
            FeatureSourceEvidence(
                **{**s, "knowledge_cutoff": datetime.fromisoformat(s["knowledge_cutoff"])}
            )
            for s in lineage["sources"]
        )
        features.append(
            FeatureFact(
                value["security_code"],
                value["feature_id"],
                value["feature_version"],
                FeatureLineage(**lineage),
                None if value["value"] is None else Decimal(value["value"]),
                value["unavailable_reason"],
            )
        )
    return FormalExpertContext(
        raw["security_code"],
        raw["snapshot_id"],
        datetime.fromisoformat(raw["knowledge_cutoff"]),
        raw["position_state"],
        tuple(features),
        (),
        tuple(_evidence(r) for r in raw["additional_evidence_refs"]),
    )


def restore_position_facts(raw: dict[str, Any]) -> HeldPositionFacts:
    return HeldPositionFacts(
        raw["holding_sessions"],
        None if raw["marked_return"] is None else Decimal(raw["marked_return"]),
        raw["prior_strategy_reduction_count"],
        _evidence(raw["evidence"]),
    )


@dataclass(frozen=True)
class SupportDecisionInput:
    security_code: str
    market_date: date
    available: bool
    reason: str
    process_id: str | None
    process_revision: str | None
    phase: str | None
    age_sessions: int | None
    signed_distance: Decimal | None
    rebound: Decimal | None
    continuous_confirmed: bool
    confirmation_date: date | None
    stage10_active: bool
    stage11_active: bool
    confirmation_ref: str | None
    lineage_digest: str
    process_count: int = 1
    unavailable_process_count: int = 0
    process_revisions: tuple[str, ...] = ()


def support_decision_inputs(
    facts: SupportFactsResult,
    contract: StageProjectionContract,
) -> dict[tuple[str, date], SupportDecisionInput]:
    """Independently retain continuous confirmation memory; compare it with projection.

    A current-distance-only control would lose persistent confirmation and would
    not be an equivalent information set.
    """
    projection = project_support_evolution_stages(facts, contract)
    projected = {(r.process_id, r.market_date): r for r in projection.observations}
    distances = {r.identity.revision_id: r for r in facts.distances}
    rebounds = {r.identity.revision_id: r for r in facts.rebounds}
    confirmed: dict[str, date] = {}
    by_day: dict[tuple[str, date], list[SupportDecisionInput]] = {}
    for row in sorted(facts.observations, key=lambda r: (r.market_date, r.process_id)):
        if row.scale != 20:
            continue
        distance = (
            None
            if row.support_distance_revision_ref is None
            else distances[row.support_distance_revision_ref].value
        )
        rebound = (
            None
            if row.post_break_rebound_revision_ref is None
            else rebounds[row.post_break_rebound_revision_ref].value
        )
        if (
            row.phase == "unreclaimed"
            and row.unreclaimed_age_sessions is not None
            and distance is not None
            and row.unreclaimed_age_sessions >= contract.persistence_sessions
            and distance <= -contract.failure_depth_abs_min
        ):
            confirmed.setdefault(row.process_id, row.market_date)
        continuous = row.phase == "unreclaimed" and row.process_id in confirmed
        stage = projected[row.process_id, row.market_date]
        if continuous != stage.stage_10_context_active:
            raise ValueError("SHADOW_CONTINUOUS_STAGE_CONFIRMATION_MISMATCH")
        key = (row.security_code, row.market_date)
        value = SupportDecisionInput(
            row.security_code,
            row.market_date,
            row.phase != "censored",
            row.censor_reason or row.phase,
            row.process_id,
            row.revision_id,
            row.phase,
            row.unreclaimed_age_sessions,
            distance,
            rebound,
            continuous,
            confirmed.get(row.process_id),
            stage.stage_10_context_active,
            stage.stage_11_rebound_condition_active,
            stage.stage_10_confirmation_ref,
            digest(
                {
                    "process": row.revision_id,
                    "distance": row.support_distance_revision_ref,
                    "rebound": row.post_break_rebound_revision_ref,
                    "projection": contract.contract_digest,
                }
            ),
        )
        by_day.setdefault(key, []).append(value)
    output: dict[tuple[str, date], SupportDecisionInput] = {}
    for key, values in by_day.items():
        ordered = sorted(values, key=lambda r: r.process_id or "")
        qualified = [r for r in ordered if r.available and r.continuous_confirmed]
        available = [r for r in ordered if r.available]
        # Existential mapping over ALL declared 20-session processes. A stable
        # witness carries the action evidence; its lexicographic ID never selects
        # a threshold, price outcome, or profitable process. Keep every revision.
        witness = (qualified or available or ordered)[0]
        output[key] = replace(
            witness,
            available=bool(available),
            continuous_confirmed=bool(qualified),
            stage10_active=any(r.available and r.stage10_active for r in ordered),
            stage11_active=any(r.available and r.stage11_active for r in ordered),
            lineage_digest=digest([r.lineage_digest for r in ordered]),
            process_count=len(ordered),
            unavailable_process_count=sum(not r.available for r in ordered),
            process_revisions=tuple(r.process_revision or "" for r in ordered),
        )
    return output


@dataclass(frozen=True)
class ShadowResult:
    baseline: FormalStrategyDecision
    candidate: FormalStrategyDecision
    proposed: bool
    reason: str
    target_quantity: int | None

    @property
    def final_action_changed(self) -> bool:
        return self.baseline.action.final_action != self.candidate.action.final_action


def evaluate_shadow(
    runner: FormalStrategyRunner,
    *,
    event_id: str,
    context: FormalExpertContext,
    position_facts: HeldPositionFacts,
    base_required_action: RequiredAction,
    base_reason_code: str,
    support: SupportDecisionInput,
    representation: str,
    quantity: int,
    target_notional: Decimal,
    last_mark_price: Decimal | None,
    native_security_code: str | None = None,
) -> ShadowResult:
    """Apply one reduce-only proposal through the existing aggregator and guardians."""
    if representation not in {"continuous", "stage10_11"}:
        raise ValueError("SHADOW_REPRESENTATION")
    days = {f.lineage.market_date for f in context.feature_facts}
    if support.market_date not in days or len(days) != 1:
        raise ValueError("SHADOW_SUPPORT_DATE_MISMATCH")
    if support.security_code != (native_security_code or context.security_code):
        raise ValueError("SHADOW_SUPPORT_SECURITY_MISMATCH")
    # security_code is the native panel identity, whereas an original context may
    # carry its formal alias. The externally bound identity join must prove that
    # relation before calling this function; never infer it from a ticker prefix.
    if quantity <= 0 or not target_notional.is_finite() or target_notional <= 0:
        raise ValueError("SHADOW_POSITION_QUANTITY_OR_NOTIONAL")
    policy = runner.position_policy
    if (
        policy.minimum_reduce_holding_sessions,
        policy.maximum_strategy_reductions,
        policy.reduce_target_maximum_position,
    ) != (3, 1, Decimal("0.10")):
        raise ValueError("SHADOW_FROZEN_BASE_POLICY_DRIFT")
    baseline = runner.evaluate_held(
        event_id=event_id,
        context=context,
        position_facts=position_facts,
        base_required_action=base_required_action,
        base_reason_code=base_reason_code,
        intervention_id="intervention.position-risk-base-policy",
    )
    active = (
        support.continuous_confirmed if representation == "continuous" else support.stage10_active
    )
    reason = "structural_not_confirmed"
    target: int | None = None
    if not support.available:
        reason = "structural_unavailable_no_extra_sell"
    elif not active:
        pass
    elif (
        support.phase != "unreclaimed"
        or support.process_id is None
        or not support.confirmation_ref
        or support.confirmation_date is None
        or support.confirmation_date > support.market_date
    ):
        raise ValueError("SHADOW_CONFIRMATION_NOT_CAUSAL")
    elif position_facts.holding_sessions < policy.minimum_reduce_holding_sessions:
        reason = "minimum_holding_not_met"
    elif position_facts.prior_strategy_reduction_count >= policy.maximum_strategy_reductions:
        reason = "prior_reduction_preserved"
    elif last_mark_price is None or not last_mark_price.is_finite() or last_mark_price <= 0:
        reason = "position_mark_unavailable_no_extra_sell"
    else:
        target = (
            int(
                (
                    target_notional * policy.reduce_target_maximum_position / last_mark_price / 100
                ).to_integral_value(rounding=ROUND_DOWN)
            )
            * 100
        )
        reason = (
            "already_at_or_below_cap" if quantity <= target else "structural_reduce_once_proposed"
        )
    proposed = reason == "structural_reduce_once_proposed"
    if not proposed:
        return ShadowResult(baseline, baseline, False, reason, target)
    assert support.process_id is not None
    reference = ExpertEvidenceRef(
        EvidenceKind.SNAPSHOT,
        "support-shadow:" + support.process_id,
        "1.0.0",
        support.lineage_digest,
    )
    augmented = replace(
        context, additional_evidence_refs=(*context.additional_evidence_refs, reference)
    )
    structural = VetoOutput(
        "shadow.support-failure",
        "1.0.0",
        ExpertApplicability.APPLICABLE,
        "held_structural_review",
        "support_failure_reduce_once",
        (reference,),
        VetoAuthority.STRATEGY,
        False,
        RequiredAction.REDUCE_ONLY,
    )
    governance = aggregate_governance(
        augmented,
        (*baseline.governance.veto_outputs, structural),
        baseline.governance.constraint_outputs,
    )
    action = runner._map_action(augmented, governance, baseline.action.scoring_outputs)
    candidate = FormalStrategyDecision(
        event_id,
        POLICY,
        digest({"base_topology": baseline.topology_digest, "overlay": POLICY}),
        None,
        governance,
        action,
        Decimal("0")
        if action.final_action is GovernedAction.FULL_EXIT
        else policy.reduce_target_maximum_position,
    )
    return ShadowResult(baseline, candidate, True, reason, target)
