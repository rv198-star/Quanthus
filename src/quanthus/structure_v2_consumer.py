"""B5 direct qualified-target compatibility consumer and separate physical audit receipts."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from quanthus.aggregator import (
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.backtest.contracts import BacktestRunSpec, BacktestSignal, EventType, OrderSide
from quanthus.backtest.engine import BacktestEngine
from quanthus.evidence_consumption import (
    ConsumedRevision,
    FactConsumptionEvent,
    GenerationReceipt,
    PublicationReceipt,
    validate_consumption_binding,
)
from quanthus.expert_protocols import (
    ExpertApplicability,
    ExpertContext,
    ScoringOutput,
    run_scoring_expert,
)
from quanthus.feature_registry import FeatureCutoffRule, FeatureLineage, FeatureSourceEvidence
from quanthus.label_registry import FeatureFact
from quanthus.structure_mvp_run import _base_constraint, _system_veto
from quanthus.structure_semantics import ClockView
from quanthus.structure_v2 import Measurement, StructureV2Result
from quanthus.structure_v2_inputs import digest
from quanthus.temporal_contracts import QueryPurpose, TemporalQuery

FEATURE = "structure.geometry.signed-distance"
EXPERT = "expert.structure-v2-compatibility"


class StructureV2CompatibilityExpert:
    """Consumes ONE target-qualified continuous fact, not a coarse-state projection."""
    expert_id = EXPERT
    expert_version = "1.0.0"

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        if len(context.feature_facts) != 1 or context.feature_facts[0].key != (FEATURE, "1.0.0"):
            raise ValueError("compatibility invocation requires one exact target feature")
        value = context.feature_facts[0].value
        if not isinstance(value, Decimal):
            raise ValueError("qualified target distance must be Decimal")
        refs = tuple(item for item in context.evidence_refs if item.kind.value == "feature")
        return ScoringOutput(
            expert_id=self.expert_id, expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="qualified_continuous_target_available",
            reason_code="signed_distance_direct_not_investment_mapping", evidence=refs,
            signal=max(Decimal(-1), min(Decimal(1), value)), confidence=Decimal(1),
        )


def _context(item: Measurement, snapshot: Any) -> ExpertContext:
    revision = item.identity
    lineage = FeatureLineage(
        feature_id=FEATURE, feature_version="1.0.0", definition_digest=item.spec_digest,
        snapshot_id=snapshot.snapshot_id, market_date=item.market_date,
        knowledge_cutoff=snapshot.knowledge_cutoff, source_revision=revision.revision_id,
        universe_policy_version="structure-v2-compatibility-v1",
        sources=(FeatureSourceEvidence(
            dataset="structure-facts-v2", column="signed-distance", dataset_version="2.0.0",
            manifest_id=revision.revision_id, knowledge_cutoff=snapshot.knowledge_cutoff,
        ),), history_sessions=item.scale + 1, cross_section_complete=None,
        cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
    )
    return ExpertContext(
        security_code=item.security_code, snapshot_id=snapshot.snapshot_id,
        knowledge_cutoff=snapshot.knowledge_cutoff, position_state="empty",
        feature_facts=(FeatureFact(item.security_code, FEATURE, "1.0.0", lineage, item.value),),
        label_results=(),
    )


def consume(
    structure: StructureV2Result, frames: dict[date, Any], *, code_revision: str,
    report_start: date, report_end: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Publish an in-memory payload handoff, then log each actual compatibility invocation.

    The source knowledge view is retrospective. Physical use clocks are now, never historical T.
    Audit verification happens locally before serialization and again at the artifact boundary.
    """
    # Only active process targets are sent to this compatibility adapter; all measurements persist.
    targets = {(item.security_code, item.market_date, item.scale, item.anchor_key)
               for item in structure.episodes if item.phase != "censored"
               and report_start <= item.market_date <= report_end}
    selected = [item for item in structure.measurements
                if (item.security_code, item.market_date, item.scale, item.anchor_key) in targets]
    if not selected:
        raise ValueError("frozen compatibility slice contains no target inputs")
    by_spec: dict[str, list[Measurement]] = {}
    for item in selected:
        by_spec.setdefault(item.spec_digest, []).append(item)
    generations: dict[str, GenerationReceipt] = {}
    publications: dict[str, PublicationReceipt] = {}
    physical_run = "structure-v2-" + datetime.now(timezone.utc).isoformat()
    payload_digest = structure.digest
    for spec, members in sorted(by_spec.items()):
        generation = GenerationReceipt(
            digest({"run": physical_run, "spec": spec}), physical_run, code_revision, spec,
            tuple(sorted({item.identity.revision_id for item in members})),
            datetime.now(timezone.utc),
        )
        publication = PublicationReceipt(
            digest({"generation": generation.receipt_id, "payload": payload_digest}),
            generation.receipt_id, payload_digest, datetime.now(timezone.utc), "direct_handoff",
        )
        generations[spec], publications[spec] = generation, publication
    outputs, events = [], []
    expert = StructureV2CompatibilityExpert()
    for item in selected:
        generation, publication = generations[item.spec_digest], publications[item.spec_digest]
        frame = frames[item.market_date]
        context = _context(item, frame.snapshot)
        used_at = datetime.now(timezone.utc)
        score = run_scoring_expert(expert, context)
        governance = aggregate_governance(
            context, (_system_veto(context),), (_base_constraint(context),),
        )
        result = aggregate_score_and_map_action(
            context, governance, (score,),
            ScoreActionPolicy("structure-v2-compatibility-v1", Decimal(0)),
        )
        decided_at = datetime.now(timezone.utc)
        identity = item.identity
        decision_key = digest({"observation": identity.observation_key, "expert": EXPERT})
        event = FactConsumptionEvent(
            digest({"run": physical_run, "decision": decision_key}), physical_run, EXPERT,
            decision_key, TemporalQuery(
                item.business_time.result_as_of, ClockView.RETROSPECTIVE,
                QueryPurpose.RECOMPUTATION, "pinned-retrospective-source-v1",
            ),
            used_at, decided_at, datetime.now(timezone.utc),
            (ConsumedRevision(identity.observation_key, identity.revision_id, item.spec_digest,
                              generation.receipt_id, publication.receipt_id,
                              publication.artifact_digest),),
        )
        validate_consumption_binding(event, identity, generation, publication)
        events.append(event.to_dict())
        outputs.append({"decision_key": decision_key, "security_code": item.security_code,
                        "market_date": item.market_date, "scale": item.scale,
                        "anchor_key": item.anchor_key, "observation_key": identity.observation_key,
                        "revision_id": identity.revision_id, "spec_digest": item.spec_digest,
                        "score": result.aggregate_score, "action": result.final_action.value})
    return outputs, {"schema_version": "quanthus.structure-v2-consumption-audit.v1",
                     "generations": [asdict(item) for item in generations.values()],
                     "publications": [asdict(item) for item in publications.values()],
                     "events": events}


def p2_probes(
    decisions: list[dict[str, Any]], frames: dict[date, Any], *, security_codes: tuple[str, ...],
    maximum: int, quantity: int, request_digest: str, structure_digest: str,
    adm_manifest_digest: str, execution_policy: Any,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {code: [] for code in security_codes}
    seen = set()
    for item in sorted(decisions, key=lambda value: (
        value["market_date"], value["security_code"], value["scale"], value["anchor_key"],
    )):
        key = (item["security_code"], item["market_date"])
        if item["action"] == "trial_entry" and key not in seen:
            buckets[item["security_code"]].append(item)
            seen.add(key)
    selected = []
    for index in range(maximum):
        for code in security_codes:
            if index < len(buckets[code]) and len(selected) < maximum:
                selected.append(buckets[code][index])
    engine = BacktestEngine(execution_policy)
    output = []
    for index, decision in enumerate(selected):
        frame = frames[decision["market_date"]]
        signal = BacktestSignal(
            signal_id=f"structure-v2-probe-{index}", snapshot=frame.snapshot,
            decision_at=frame.decision_at, profile_id="close-to-next-open-v1", window_id=None,
            security_code=decision["security_code"], side=OrderSide.BUY,
            unit_id=f"structure-v2-unit-{index}", quantity=quantity,
            aggregate_score=decision["score"], reason_code="structure_v2_compatibility_only",
            decision_version="structure-v2-compatibility-v1",
        )
        spec = BacktestRunSpec(
            code_version="structure-v2-compatibility-v1", parameter_hash=request_digest,
            data_snapshot_id=frame.snapshot.snapshot_id, feature_version="structure-facts@2.0.0",
            rule_version="structure-v2-compatibility-v1", run_id=f"structure-v2-probe-{index}",
            execution_policy_version=execution_policy.policy_version,
            execution_profile_id="close-to-next-open-v1",
            schedule_version=execution_policy.active_profile.schedule_version, official=False,
            data_content_digest=structure_digest, data_manifest_sha256=adm_manifest_digest,
        )
        result = engine.run_session(run=spec, signal=signal,
                                    session=frame.session(decision["security_code"]),
                                    initial_cash=Decimal("1000000"))
        output.append({"decision_key": decision["decision_key"],
                       "security_code": decision["security_code"],
                       "market_date": decision["market_date"],
                       "execution_date": frame.execution_date,
                       "fill_count": sum(event.event_type is EventType.FILL
                                         for event in result.events),
                       "result_digest": digest(result.to_dict()), "result": result.to_dict()})
    return output
