"""Concrete labels and rule experts for the first governed P3 research candidate."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.aggregator import (
    GovernanceResult,
    GovernedAction,
    ScoreActionPolicy,
    ScoreActionResult,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.contracts import DecisionDisposition, DecisionEvent
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.exit_arbitration import (
    ExitArbitrationResult,
    ExitEvidenceKind,
    ExitEvidenceRef,
    ExitTriggerEvaluation,
    ExitTriggerSource,
    ExitTriggerStatus,
    arbitrate_exit,
)
from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    EvidenceKind,
    ExpertApplicability,
    ExpertContext,
    ExpertEvidenceRef,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
    run_constraint_expert,
    run_scoring_expert,
    run_veto_expert,
)
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureDefinition,
    FeatureLifecycle,
    FeatureLineage,
    FeatureNullPolicy,
    FeatureOutputType,
    FeatureRegistry,
    FeatureSource,
    FeatureSourceEvidence,
    load_feature_registry,
)
from quanthus.label_registry import (
    FeatureFact,
    LabelCard,
    LabelDecision,
    LabelEvaluationContext,
    LabelEvidence,
    LabelRegistry,
    LabelResult,
    LabelResultStatus,
    evaluate_label,
)
from quanthus.policy import P0Policy
from quanthus.position_lifecycle import PositionStage
from quanthus.strategy_policy import GovernedStrategyPolicy

GOVERNED_LABEL_EVALUATOR_VERSION = "1.0.0"
GOVERNED_EXPERT_VERSION = "1.0.0"
GOVERNED_EXIT_EVALUATOR_VERSION = "1.0.0"
_BASE_FEATURE_IDS = {
    "price.total_return_index",
    "price.sma20",
    "price.sma60",
    "price.momentum20",
    "price.prior_high20",
    "price.rsi14",
    "liquidity.volume_ratio20",
    "risk.realized_volatility20",
}
_MARKET_BREADTH_ID = "market.breadth_above_sma60"


class GovernedStrategyError(ValueError):
    """Raised when governed candidate evidence cannot produce a safe decision."""


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise GovernedStrategyError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GovernedStrategyError(f"{path} must be a non-empty string")
    return value


def _non_negative_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GovernedStrategyError(f"{path} must be a non-negative integer")
    return value


def _positive_int(value: object, path: str) -> int:
    parsed = _non_negative_int(value, path)
    if parsed == 0:
        raise GovernedStrategyError(f"{path} must be a positive integer")
    return parsed


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def load_governed_feature_registry(path: Path) -> FeatureRegistry:
    """Load one digest-bound extension over the accepted baseline registry."""

    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GovernedStrategyError(f"cannot read governed feature extension: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GovernedStrategyError(f"invalid governed feature extension JSON: {exc}") from exc
    if not isinstance(raw_value, dict):
        raise GovernedStrategyError("governed feature extension must be an object")
    raw = raw_value
    _exact_keys(
        raw,
        {"schema_version", "registry_version", "base_registry", "definitions"},
        "feature_extension",
    )
    if raw["schema_version"] != "1.0.0":
        raise GovernedStrategyError("governed feature extension schema must be 1.0.0")
    base_raw = raw["base_registry"]
    if not isinstance(base_raw, dict):
        raise GovernedStrategyError("base_registry must be an object")
    _exact_keys(base_raw, {"path", "registry_version", "digest"}, "base_registry")
    base = load_feature_registry(path.parent / _text(base_raw["path"], "base_registry.path"))
    if base.registry_version != base_raw["registry_version"] or base.digest != base_raw["digest"]:
        raise GovernedStrategyError("base feature registry identity or digest mismatch")
    definitions_raw = raw["definitions"]
    if not isinstance(definitions_raw, list) or len(definitions_raw) != 1:
        raise GovernedStrategyError("governed feature extension requires exactly one definition")
    definition_raw = definitions_raw[0]
    if not isinstance(definition_raw, dict):
        raise GovernedStrategyError("governed feature definition must be an object")
    definition_keys = {
        "feature_id",
        "version",
        "formula",
        "inputs",
        "unit",
        "null_policy",
        "lookback_sessions",
        "minimum_history_sessions",
        "cutoff_rule",
        "output_type",
        "owner",
        "lifecycle",
    }
    _exact_keys(definition_raw, definition_keys, "definitions[0]")
    inputs_raw = definition_raw["inputs"]
    if not isinstance(inputs_raw, list):
        raise GovernedStrategyError("definitions[0].inputs must be an array")
    inputs: list[FeatureSource] = []
    for index, item in enumerate(inputs_raw):
        if not isinstance(item, dict):
            raise GovernedStrategyError(f"definitions[0].inputs[{index}] must be an object")
        _exact_keys(item, {"dataset", "column"}, f"definitions[0].inputs[{index}]")
        inputs.append(
            FeatureSource(
                dataset=_text(item["dataset"], f"definitions[0].inputs[{index}].dataset"),
                column=_text(item["column"], f"definitions[0].inputs[{index}].column"),
            )
        )
    try:
        extension = FeatureDefinition(
            feature_id=_text(definition_raw["feature_id"], "definitions[0].feature_id"),
            version=_text(definition_raw["version"], "definitions[0].version"),
            formula=_text(definition_raw["formula"], "definitions[0].formula"),
            inputs=tuple(inputs),
            unit=_text(definition_raw["unit"], "definitions[0].unit"),
            null_policy=FeatureNullPolicy(definition_raw["null_policy"]),
            lookback_sessions=_positive_int(
                definition_raw["lookback_sessions"], "definitions[0].lookback_sessions"
            ),
            minimum_history_sessions=_positive_int(
                definition_raw["minimum_history_sessions"],
                "definitions[0].minimum_history_sessions",
            ),
            cutoff_rule=FeatureCutoffRule(definition_raw["cutoff_rule"]),
            output_type=FeatureOutputType(definition_raw["output_type"]),
            owner=_text(definition_raw["owner"], "definitions[0].owner"),
            lifecycle=FeatureLifecycle(definition_raw["lifecycle"]),
        )
    except (TypeError, ValueError) as exc:
        raise GovernedStrategyError(f"invalid governed feature definition: {exc}") from exc
    registry = FeatureRegistry(
        path=path.resolve(),
        schema_version="1.0.0",
        registry_version=_text(raw["registry_version"], "registry_version"),
        definitions=(*base.definitions, extension),
    )
    active_ids = {
        item.feature_id
        for item in registry.definitions
        if item.lifecycle is FeatureLifecycle.ACTIVE
    }
    if active_ids != _BASE_FEATURE_IDS | {_MARKET_BREADTH_ID}:
        raise GovernedStrategyError("governed feature registry does not match candidate contract")
    return registry


def _feature_value(
    row: BaselineFeatureRow,
    feature_id: str,
    breadth: Decimal | None,
) -> Decimal | None:
    values: dict[str, Decimal | None] = {
        "price.total_return_index": row.total_return_index,
        "price.sma20": row.sma20,
        "price.sma60": row.sma60,
        "price.momentum20": row.momentum20,
        "price.prior_high20": row.prior_high20,
        "price.rsi14": row.rsi14,
        "liquidity.volume_ratio20": row.volume_ratio20,
        "risk.realized_volatility20": row.realized_volatility20,
        _MARKET_BREADTH_ID: breadth,
    }
    if feature_id not in values:
        raise GovernedStrategyError(f"unsupported governed feature: {feature_id}")
    return values[feature_id]


def build_governed_feature_facts(
    bundle: HistoricalReplayBundle,
    frame: StrategyReplayFrame,
    row: BaselineFeatureRow,
    breadth: Decimal | None,
    registry: FeatureRegistry,
) -> tuple[FeatureFact, ...]:
    """Bind panel values to the exact historical frame and immutable bundle manifests."""

    if row.market_date != frame.snapshot.market_date:
        raise GovernedStrategyError("candidate row and replay frame dates do not match")
    manifests = {item.dataset: item for item in bundle.datasets}
    facts: list[FeatureFact] = []
    for definition in sorted(registry.definitions, key=lambda item: item.key):
        evidence: list[FeatureSourceEvidence] = []
        for source in definition.inputs:
            manifest = manifests.get(source.dataset)
            if manifest is None:
                raise GovernedStrategyError(
                    f"historical bundle is missing feature dataset: {source.dataset}"
                )
            evidence.append(
                FeatureSourceEvidence(
                    dataset=source.dataset,
                    column=source.column,
                    dataset_version=manifest.version,
                    manifest_id=manifest.manifest_id,
                    knowledge_cutoff=frame.snapshot.knowledge_cutoff,
                )
            )
        lineage = FeatureLineage(
            feature_id=definition.feature_id,
            feature_version=definition.version,
            definition_digest=definition.digest,
            snapshot_id=frame.snapshot.snapshot_id,
            market_date=frame.snapshot.market_date,
            knowledge_cutoff=frame.snapshot.knowledge_cutoff,
            source_revision=frame.snapshot.source_revision,
            universe_policy_version=bundle.universe_policy_version,
            sources=tuple(evidence),
            history_sessions=row.listing_sessions,
            cross_section_complete=(
                True
                if definition.cutoff_rule is FeatureCutoffRule.COMPLETE_T_CLOSE_CROSS_SECTION
                else None
            ),
            cutoff_rule=definition.cutoff_rule,
        )
        value = _feature_value(row, definition.feature_id, breadth)
        facts.append(
            FeatureFact(
                security_code=row.security_id,
                feature_id=definition.feature_id,
                feature_version=definition.version,
                lineage=lineage,
                value=value,
                unavailable_reason=None if value is not None else "feature_unavailable",
            )
        )
    return tuple(facts)


def _fact_decimal(context: LabelEvaluationContext, feature_id: str) -> Decimal | None:
    matches = [fact for fact in context.facts if fact.feature_id == feature_id]
    if len(matches) != 1:
        raise GovernedStrategyError(f"label context is missing feature: {feature_id}")
    value = matches[0].value
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise GovernedStrategyError(f"governed feature must be decimal: {feature_id}")
    return value


def _evidence(**values: Decimal | str) -> tuple[LabelEvidence, ...]:
    return tuple(LabelEvidence(name, str(value)) for name, value in sorted(values.items()))


class GovernedLabelEvaluator:
    evaluator_id = "candidate.governed-labels"
    evaluator_version = GOVERNED_LABEL_EVALUATOR_VERSION

    def __init__(self, policy: GovernedStrategyPolicy) -> None:
        self.policy = policy

    def evaluate(self, card: LabelCard, context: LabelEvaluationContext) -> LabelDecision:
        entry = self.policy.entry
        if card.label_id == "market.breadth-regime":
            breadth = _fact_decimal(context, _MARKET_BREADTH_ID)
            evidence = _evidence(
                breadth="unavailable" if breadth is None else breadth,
                minimum_breadth=entry.minimum_market_breadth_above_sma60,
            )
            if breadth is None:
                return LabelDecision(
                    LabelResultStatus.UNKNOWN, None, "feature_unavailable", evidence
                )
            supportive = breadth >= entry.minimum_market_breadth_above_sma60
            return LabelDecision(
                LabelResultStatus.APPLIED,
                "supportive" if supportive else "weak",
                "breadth_supportive" if supportive else "breadth_weak",
                evidence,
            )
        if card.label_id == "trend.moderate-uptrend":
            price = _fact_decimal(context, "price.total_return_index")
            sma20 = _fact_decimal(context, "price.sma20")
            sma60 = _fact_decimal(context, "price.sma60")
            momentum = _fact_decimal(context, "price.momentum20")
            values = (price, sma20, sma60, momentum)
            evidence = _evidence(
                momentum20="unavailable" if momentum is None else momentum,
                price_to_sma20=("unavailable" if price is None or sma20 is None else price / sma20),
                sma20_to_sma60=("unavailable" if sma20 is None or sma60 is None else sma20 / sma60),
            )
            if any(value is None for value in values):
                return LabelDecision(
                    LabelResultStatus.UNKNOWN, None, "feature_unavailable", evidence
                )
            assert price is not None and sma20 is not None and sma60 is not None
            assert momentum is not None
            moderate = (
                price > sma20 > sma60
                and entry.minimum_momentum20 <= momentum <= entry.maximum_momentum20
            )
            return LabelDecision(
                LabelResultStatus.APPLIED,
                "moderate" if moderate else "not_moderate",
                "moderate_uptrend" if moderate else "trend_outside_bounds",
                evidence,
            )
        if card.label_id == "price-volume.breakout-confirmation":
            price = _fact_decimal(context, "price.total_return_index")
            prior_high = _fact_decimal(context, "price.prior_high20")
            volume = _fact_decimal(context, "liquidity.volume_ratio20")
            rsi = _fact_decimal(context, "price.rsi14")
            breakout = None if price is None or prior_high is None else price / prior_high - 1
            evidence = _evidence(
                breakout20="unavailable" if breakout is None else breakout,
                rsi14="unavailable" if rsi is None else rsi,
                volume_ratio20="unavailable" if volume is None else volume,
            )
            if breakout is None or volume is None or rsi is None:
                return LabelDecision(
                    LabelResultStatus.UNKNOWN, None, "feature_unavailable", evidence
                )
            confirmed = (
                entry.minimum_breakout20 <= breakout <= entry.maximum_breakout20
                and entry.minimum_volume_ratio20 <= volume <= entry.maximum_volume_ratio20
                and entry.minimum_rsi14 <= rsi <= entry.maximum_rsi14
            )
            return LabelDecision(
                LabelResultStatus.APPLIED,
                "confirmed" if confirmed else "not_confirmed",
                "breakout_confirmed" if confirmed else "breakout_not_confirmed",
                evidence,
            )
        if card.label_id == "risk.extension-state":
            volatility = _fact_decimal(context, "risk.realized_volatility20")
            rsi = _fact_decimal(context, "price.rsi14")
            evidence = _evidence(
                realized_volatility20=("unavailable" if volatility is None else volatility),
                rsi14="unavailable" if rsi is None else rsi,
            )
            if volatility is None or rsi is None:
                return LabelDecision(
                    LabelResultStatus.UNKNOWN, None, "feature_unavailable", evidence
                )
            contained = (
                volatility <= entry.maximum_realized_volatility20 and rsi <= entry.maximum_rsi14
            )
            return LabelDecision(
                LabelResultStatus.APPLIED,
                "contained" if contained else "extended",
                "extension_contained" if contained else "extension_excessive",
                evidence,
            )
        raise GovernedStrategyError(f"unsupported governed label: {card.label_id}")


def evaluate_governed_labels(
    policy: GovernedStrategyPolicy,
    feature_registry: FeatureRegistry,
    label_registry: LabelRegistry,
    *,
    security_id: str,
    snapshot_id: str,
    knowledge_cutoff: datetime,
    facts: tuple[FeatureFact, ...],
) -> tuple[LabelResult, ...]:
    if feature_registry.registry_version != policy.bindings.feature_registry_version:
        raise GovernedStrategyError("strategy and feature registry versions do not match")
    if label_registry.registry_version != policy.bindings.label_registry_version:
        raise GovernedStrategyError("strategy and label registry versions do not match")
    evaluator = GovernedLabelEvaluator(policy)
    results: list[LabelResult] = []
    for card in sorted(label_registry.cards, key=lambda item: item.key):
        required = {item.key for item in card.feature_requirements}
        context = LabelEvaluationContext(
            security_code=security_id,
            snapshot_id=snapshot_id,
            knowledge_cutoff=knowledge_cutoff,
            facts=tuple(fact for fact in facts if fact.key in required),
        )
        results.append(evaluate_label(card, feature_registry, context, evaluator))
    return tuple(results)


def _context_ref(context: ExpertContext, kind: EvidenceKind, evidence_id: str) -> ExpertEvidenceRef:
    matches = [
        item
        for item in context.evidence_refs
        if item.kind is kind and item.evidence_id == evidence_id
    ]
    if len(matches) != 1:
        raise GovernedStrategyError(f"expert context is missing evidence: {evidence_id}")
    return matches[0]


def _label_value(context: ExpertContext, label_id: str) -> str | None:
    matches = [item for item in context.label_results if item.label_id == label_id]
    if len(matches) != 1:
        raise GovernedStrategyError(f"expert context is missing label: {label_id}")
    value = matches[0].value
    if value is not None and not isinstance(value, str):
        raise GovernedStrategyError(f"governed label value must be text: {label_id}")
    return value


def _feature_decimal(context: ExpertContext, feature_id: str) -> Decimal:
    matches = [item for item in context.feature_facts if item.feature_id == feature_id]
    if len(matches) != 1 or not isinstance(matches[0].value, Decimal):
        raise GovernedStrategyError(f"expert context is missing decimal feature: {feature_id}")
    return matches[0].value


class EntryEvidenceVeto:
    expert_id = "veto.entry-evidence"
    expert_version = GOVERNED_EXPERT_VERSION

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def evaluate(self, context: ExpertContext) -> VetoOutput:
        action = RequiredAction.NONE if self.allowed else RequiredAction.BLOCK_NEW
        return VetoOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="snapshot_available",
            reason_code="entry_evidence_ready" if self.allowed else "entry_evidence_blocked",
            evidence=(_context_ref(context, EvidenceKind.SNAPSHOT, context.snapshot_id),),
            authority=VetoAuthority.SYSTEM,
            allow=self.allowed,
            required_action=action,
        )


class CandidateStateVeto:
    expert_id = "veto.candidate-state"
    expert_version = GOVERNED_EXPERT_VERSION

    _EXPECTED = {
        "market.breadth-regime": "supportive",
        "trend.moderate-uptrend": "moderate",
        "price-volume.breakout-confirmation": "confirmed",
        "risk.extension-state": "contained",
    }

    def evaluate(self, context: ExpertContext) -> VetoOutput:
        allowed = all(
            _label_value(context, label_id) == expected
            for label_id, expected in self._EXPECTED.items()
        )
        return VetoOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="labels_evaluated",
            reason_code="candidate_state_allowed" if allowed else "candidate_state_blocked",
            evidence=tuple(
                _context_ref(context, EvidenceKind.LABEL, label_id)
                for label_id in sorted(self._EXPECTED)
            ),
            authority=VetoAuthority.STRATEGY,
            allow=allowed,
            required_action=RequiredAction.NONE if allowed else RequiredAction.BLOCK_NEW,
        )


class TrialPositionConstraint:
    expert_id = "constraint.trial-position"
    expert_version = GOVERNED_EXPERT_VERSION

    def __init__(self, policy: GovernedStrategyPolicy) -> None:
        self.policy = policy

    def evaluate(self, context: ExpertContext) -> ConstraintOutput:
        return ConstraintOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="candidate_review",
            reason_code="trial_position_bounded",
            evidence=(_context_ref(context, EvidenceKind.LABEL, "risk.extension-state"),),
            minimum_position=Decimal("0"),
            maximum_position=self.policy.portfolio.trial_position_fraction,
            stop_rule=f"loss_at_or_below_{self.policy.exit.constraint_stop_loss}",
            hold_rule=f"maximum_{self.policy.exit.maximum_holding_sessions}_sessions",
            scope=ConstraintScope.UNIT,
        )


def _bounded_ratio(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), (value - low) / (high - low)))


class CandidateScoringExpert:
    expert_version = GOVERNED_EXPERT_VERSION

    def __init__(self, kind: str, policy: GovernedStrategyPolicy) -> None:
        if kind not in {"trend", "breakout", "risk"}:
            raise GovernedStrategyError(f"unsupported scoring expert: {kind}")
        self.kind = kind
        self.policy = policy
        self.expert_id = f"score.{kind}"

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        entry = self.policy.entry
        if self.kind == "trend":
            momentum = _feature_decimal(context, "price.momentum20")
            signal = _bounded_ratio(momentum, entry.minimum_momentum20, entry.maximum_momentum20)
            label_id = "trend.moderate-uptrend"
        elif self.kind == "breakout":
            price = _feature_decimal(context, "price.total_return_index")
            prior = _feature_decimal(context, "price.prior_high20")
            breakout = price / prior - Decimal("1")
            extension = _bounded_ratio(breakout, entry.minimum_breakout20, entry.maximum_breakout20)
            signal = Decimal("1") - extension
            label_id = "price-volume.breakout-confirmation"
        else:
            volatility = _feature_decimal(context, "risk.realized_volatility20")
            signal = max(
                Decimal("0"),
                Decimal("1") - volatility / entry.maximum_realized_volatility20,
            )
            label_id = "risk.extension-state"
        return ScoringOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="label_available",
            reason_code=f"{self.kind}_score_computed",
            evidence=(_context_ref(context, EvidenceKind.LABEL, label_id),),
            signal=signal,
            confidence=Decimal("1"),
        )


@dataclass(frozen=True)
class GovernedCandidateDecision:
    market_date: date
    security_id: str
    feature_facts: tuple[FeatureFact, ...]
    label_results: tuple[LabelResult, ...]
    veto_outputs: tuple[VetoOutput, ...]
    constraint_outputs: tuple[ConstraintOutput, ...]
    scoring_outputs: tuple[ScoringOutput, ...]
    governance: GovernanceResult
    action: ScoreActionResult

    @property
    def digest(self) -> str:
        return self.action.digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "security_id": self.security_id,
            "feature_lineage_digests": [item.lineage.digest for item in self.feature_facts],
            "label_results": [item.to_dict() for item in self.label_results],
            "veto_outputs": [item.to_dict() for item in self.veto_outputs],
            "constraint_outputs": [item.to_dict() for item in self.constraint_outputs],
            "scoring_outputs": [item.to_dict() for item in self.scoring_outputs],
            "governance": self.governance.to_dict(),
            "action": self.action.to_dict(),
        }


def evaluate_governed_candidate(
    bundle: HistoricalReplayBundle,
    frame: StrategyReplayFrame,
    row: BaselineFeatureRow,
    breadth: Decimal | None,
    policy: GovernedStrategyPolicy,
    feature_registry: FeatureRegistry,
    label_registry: LabelRegistry,
) -> GovernedCandidateDecision:
    facts = build_governed_feature_facts(bundle, frame, row, breadth, feature_registry)
    labels = evaluate_governed_labels(
        policy,
        feature_registry,
        label_registry,
        security_id=row.security_id,
        snapshot_id=frame.snapshot.snapshot_id,
        knowledge_cutoff=frame.snapshot.knowledge_cutoff,
        facts=facts,
    )
    context = ExpertContext(
        security_code=row.security_id,
        snapshot_id=frame.snapshot.snapshot_id,
        knowledge_cutoff=frame.snapshot.knowledge_cutoff,
        position_state="empty",
        feature_facts=facts,
        label_results=labels,
    )
    if frame.decision_view is None:
        raise GovernedStrategyError("governed candidate requires a PIT decision view")
    current_bar = frame.decision_view.bar(row.security_id)
    day_return_ready = (
        row.daily_total_return is not None
        and abs(row.daily_total_return) <= policy.entry.maximum_absolute_entry_day_return
    )
    entry_evidence_ready = (
        row.entry_eligible
        and current_bar.data_valid
        and current_bar.is_executable
        and current_bar.entry_policy_allows
        and day_return_ready
    )
    vetoes = (
        run_veto_expert(EntryEvidenceVeto(entry_evidence_ready), context),
        run_veto_expert(CandidateStateVeto(), context),
    )
    constraints = (run_constraint_expert(TrialPositionConstraint(policy), context),)
    scores = tuple(
        run_scoring_expert(CandidateScoringExpert(kind, policy), context)
        for kind in ("trend", "breakout", "risk")
    )
    governance = aggregate_governance(context, vetoes, constraints)
    action = aggregate_score_and_map_action(
        context,
        governance,
        scores,
        ScoreActionPolicy(
            policy_version=f"{policy.strategy_id}@{policy.strategy_version}",
            minimum_entry_score=policy.entry.minimum_aggregate_score,
        ),
    )
    return GovernedCandidateDecision(
        market_date=frame.snapshot.market_date,
        security_id=row.security_id,
        feature_facts=facts,
        label_results=labels,
        veto_outputs=vetoes,
        constraint_outputs=constraints,
        scoring_outputs=scores,
        governance=governance,
        action=action,
    )


def _exit_evidence(
    *,
    kind: ExitEvidenceKind,
    source: ExitTriggerSource,
    frame: StrategyReplayFrame,
    values: Mapping[str, object],
) -> ExitEvidenceRef:
    payload = {
        "source": source.value,
        "snapshot_id": frame.snapshot.snapshot_id,
        "knowledge_cutoff": frame.snapshot.knowledge_cutoff.isoformat(),
        "values": dict(values),
    }
    return ExitEvidenceRef(
        kind=kind,
        evidence_id=f"{source.value}:{frame.snapshot.snapshot_id}",
        version=GOVERNED_EXIT_EVALUATOR_VERSION,
        digest=_canonical_digest(payload),
    )


def _exit_evaluation(
    *,
    source: ExitTriggerSource,
    status: ExitTriggerStatus,
    reason_code: str,
    evidence_kind: ExitEvidenceKind,
    frame: StrategyReplayFrame,
    unit_id: str,
    opportunity_id: str,
    security_id: str,
    lifecycle_stage: PositionStage,
    values: Mapping[str, object],
) -> ExitTriggerEvaluation:
    return ExitTriggerEvaluation(
        source=source,
        status=status,
        unit_id=unit_id,
        opportunity_id=opportunity_id,
        security_code=security_id,
        snapshot_id=frame.snapshot.snapshot_id,
        knowledge_cutoff=frame.snapshot.knowledge_cutoff,
        lifecycle_stage=lifecycle_stage,
        evaluator_version=GOVERNED_EXIT_EVALUATOR_VERSION,
        reason_code=reason_code,
        evidence=(
            _exit_evidence(
                kind=evidence_kind,
                source=source,
                frame=frame,
                values=values,
            ),
        ),
    )


@dataclass(frozen=True)
class GovernedPositionReview:
    market_date: date
    security_id: str
    unit_id: str
    opportunity_id: str
    lifecycle_stage: PositionStage
    holding_sessions: int
    deterioration_streak: int
    marked_return: Decimal | None
    evaluations: tuple[ExitTriggerEvaluation, ...]
    arbitration: ExitArbitrationResult

    @property
    def digest(self) -> str:
        return self.arbitration.digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "security_id": self.security_id,
            "unit_id": self.unit_id,
            "opportunity_id": self.opportunity_id,
            "lifecycle_stage": self.lifecycle_stage.value,
            "holding_sessions": self.holding_sessions,
            "deterioration_streak": self.deterioration_streak,
            "marked_return": None if self.marked_return is None else str(self.marked_return),
            "evaluations": [item.to_dict() for item in self.evaluations],
            "arbitration": self.arbitration.to_dict(),
        }


def evaluate_governed_position_review(
    *,
    frame: StrategyReplayFrame,
    row: BaselineFeatureRow,
    policy: GovernedStrategyPolicy,
    p0_policy: P0Policy,
    unit_id: str,
    opportunity_id: str,
    lifecycle_stage: PositionStage,
    holding_sessions: int,
    prior_deterioration_streak: int,
    marked_return: Decimal | None,
) -> GovernedPositionReview:
    """Evaluate all frozen exit sources without turning missing features into an exit."""

    if row.market_date != frame.snapshot.market_date:
        raise GovernedStrategyError("position row and replay frame dates do not match")
    if lifecycle_stage not in {PositionStage.TRIAL, PositionStage.DYNAMIC}:
        raise GovernedStrategyError("position review requires a Trial or Dynamic lifecycle")
    holding_sessions = _positive_int(holding_sessions, "holding_sessions")
    prior_deterioration_streak = _non_negative_int(
        prior_deterioration_streak, "prior_deterioration_streak"
    )
    if marked_return is not None and not marked_return.is_finite():
        raise GovernedStrategyError("marked_return must be finite when present")

    structure_values = {
        "price": None if row.total_return_index is None else str(row.total_return_index),
        "sma20": None if row.sma20 is None else str(row.sma20),
        "sma60": None if row.sma60 is None else str(row.sma60),
    }
    if row.total_return_index is None or row.sma20 is None or row.sma60 is None:
        structural_status = ExitTriggerStatus.UNAVAILABLE
        structural_reason = "structural_break_evidence_unavailable"
        structural_kind = ExitEvidenceKind.MISSING_DATA
    else:
        structural_break = row.total_return_index < row.sma60 and row.sma20 <= row.sma60
        structural_status = (
            ExitTriggerStatus.TRIGGERED if structural_break else ExitTriggerStatus.CLEAR
        )
        structural_reason = (
            "structural_break_triggered" if structural_break else "structural_break_clear"
        )
        structural_kind = ExitEvidenceKind.GOVERNANCE

    if marked_return is None:
        stop_status = ExitTriggerStatus.UNAVAILABLE
        stop_reason = "constraint_stop_evidence_unavailable"
        stop_kind = ExitEvidenceKind.MISSING_DATA
    else:
        stopped = marked_return <= -policy.exit.constraint_stop_loss
        stop_status = ExitTriggerStatus.TRIGGERED if stopped else ExitTriggerStatus.CLEAR
        stop_reason = "constraint_stop_triggered" if stopped else "constraint_stop_clear"
        stop_kind = ExitEvidenceKind.CONSTRAINT

    deterioration_values = {
        "price": None if row.total_return_index is None else str(row.total_return_index),
        "sma20": None if row.sma20 is None else str(row.sma20),
        "momentum20": None if row.momentum20 is None else str(row.momentum20),
        "prior_streak": prior_deterioration_streak,
        "minimum_hold": policy.exit.minimum_holding_sessions,
        "required_streak": policy.exit.deterioration_sessions,
    }
    if row.total_return_index is None or row.sma20 is None or row.momentum20 is None:
        deterioration_streak = prior_deterioration_streak
        deterioration_status = ExitTriggerStatus.UNAVAILABLE
        deterioration_reason = "deterioration_evidence_unavailable"
        deterioration_kind = ExitEvidenceKind.MISSING_DATA
    else:
        deteriorating = row.total_return_index < row.sma20 or row.momentum20 <= 0
        deterioration_streak = prior_deterioration_streak + 1 if deteriorating else 0
        deterioration_triggered = (
            holding_sessions >= policy.exit.minimum_holding_sessions
            and deterioration_streak >= policy.exit.deterioration_sessions
        )
        deterioration_status = (
            ExitTriggerStatus.TRIGGERED if deterioration_triggered else ExitTriggerStatus.CLEAR
        )
        deterioration_reason = (
            "consecutive_deterioration_triggered"
            if deterioration_triggered
            else "consecutive_deterioration_clear"
        )
        deterioration_kind = ExitEvidenceKind.REVIEW
        deterioration_values["deteriorating"] = deteriorating
        deterioration_values["current_streak"] = deterioration_streak

    maximum_hold = holding_sessions >= policy.exit.maximum_holding_sessions
    maximum_status = ExitTriggerStatus.TRIGGERED if maximum_hold else ExitTriggerStatus.CLEAR
    maximum_reason = (
        "maximum_holding_period_triggered" if maximum_hold else "maximum_holding_period_clear"
    )

    evaluations = (
        _exit_evaluation(
            source=ExitTriggerSource.STRUCTURAL_BREAK,
            status=structural_status,
            reason_code=structural_reason,
            evidence_kind=structural_kind,
            frame=frame,
            unit_id=unit_id,
            opportunity_id=opportunity_id,
            security_id=row.security_id,
            lifecycle_stage=lifecycle_stage,
            values=structure_values,
        ),
        _exit_evaluation(
            source=ExitTriggerSource.CONSTRAINT_STOP,
            status=stop_status,
            reason_code=stop_reason,
            evidence_kind=stop_kind,
            frame=frame,
            unit_id=unit_id,
            opportunity_id=opportunity_id,
            security_id=row.security_id,
            lifecycle_stage=lifecycle_stage,
            values={
                "marked_return": None if marked_return is None else str(marked_return),
                "threshold": str(-policy.exit.constraint_stop_loss),
            },
        ),
        _exit_evaluation(
            source=ExitTriggerSource.CONSECUTIVE_DETERIORATION,
            status=deterioration_status,
            reason_code=deterioration_reason,
            evidence_kind=deterioration_kind,
            frame=frame,
            unit_id=unit_id,
            opportunity_id=opportunity_id,
            security_id=row.security_id,
            lifecycle_stage=lifecycle_stage,
            values=deterioration_values,
        ),
        _exit_evaluation(
            source=ExitTriggerSource.MAXIMUM_HOLDING_PERIOD,
            status=maximum_status,
            reason_code=maximum_reason,
            evidence_kind=ExitEvidenceKind.CALENDAR,
            frame=frame,
            unit_id=unit_id,
            opportunity_id=opportunity_id,
            security_id=row.security_id,
            lifecycle_stage=lifecycle_stage,
            values={
                "holding_sessions": holding_sessions,
                "maximum_holding_sessions": policy.exit.maximum_holding_sessions,
            },
        ),
    )
    arbitration = arbitrate_exit(p0_policy, evaluations)
    return GovernedPositionReview(
        market_date=frame.snapshot.market_date,
        security_id=row.security_id,
        unit_id=unit_id,
        opportunity_id=opportunity_id,
        lifecycle_stage=lifecycle_stage,
        holding_sessions=holding_sessions,
        deterioration_streak=deterioration_streak,
        marked_return=marked_return,
        evaluations=evaluations,
        arbitration=arbitration,
    )


def governed_candidate_decision_event(
    *,
    decision: GovernedCandidateDecision,
    frame: StrategyReplayFrame,
    policy: GovernedStrategyPolicy,
    run_id: str,
    unit_id: str,
    profile_id: str,
    disposition: DecisionDisposition,
    signal_id: str | None = None,
    linked_order_ids: tuple[str, ...] = (),
    final_action: str | None = None,
    additional_reason_codes: tuple[str, ...] = (),
) -> DecisionEvent:
    """Materialize the governed candidate chain in the accepted P2 audit schema."""

    expert_outputs: dict[str, Mapping[str, Any]] = {}
    for veto in decision.veto_outputs:
        expert_outputs[veto.expert_id] = veto.to_dict()
    for constraint in decision.constraint_outputs:
        expert_outputs[constraint.expert_id] = constraint.to_dict()
    for scoring in decision.scoring_outputs:
        expert_outputs[scoring.expert_id] = scoring.to_dict()
    merged = decision.governance.merged_constraints
    constraints = {
        "minimum_position": "unavailable" if merged is None else str(merged.minimum_position),
        "maximum_position": "unavailable" if merged is None else str(merged.maximum_position),
    }
    reasons = tuple(dict.fromkeys((*decision.action.reason_codes, *additional_reason_codes)))
    return DecisionEvent(
        decision_id=(
            "decision_"
            + _canonical_digest(
                [run_id, frame.snapshot.snapshot_id, decision.security_id, unit_id, reasons]
            ).removeprefix("sha256:")[:24]
        ),
        run_id=run_id,
        signal_id=signal_id,
        snapshot=frame.snapshot,
        decided_at=frame.decision_at,
        profile_id=profile_id,
        window_id=None,
        security_code=decision.security_id,
        unit_id=unit_id,
        lifecycle_stage=PositionStage.EMPTY.value,
        aggregate_score=decision.action.aggregate_score,
        expert_outputs=expert_outputs,
        vetoes=decision.governance.terminating_veto_ids,
        constraints=constraints,
        proposed_action=decision.action.proposed_action.value,
        final_action=final_action or decision.action.final_action.value,
        reason_codes=reasons,
        policy_versions={
            "strategy": f"{policy.strategy_id}@{policy.strategy_version}",
            "feature_registry": policy.bindings.feature_registry_version,
            "label_registry": policy.bindings.label_registry_version,
            "execution": policy.bindings.execution_policy_version,
            "p0": policy.bindings.p0_policy_version,
        },
        linked_order_ids=linked_order_ids,
        disposition=disposition,
    )


def governed_position_decision_event(
    *,
    review: GovernedPositionReview,
    frame: StrategyReplayFrame,
    policy: GovernedStrategyPolicy,
    run_id: str,
    profile_id: str,
    disposition: DecisionDisposition,
    signal_id: str | None = None,
    linked_order_ids: tuple[str, ...] = (),
) -> DecisionEvent:
    """Materialize one complete held-position review in the accepted P2 audit schema."""

    exit_requested = review.arbitration.exit_requested
    final_action = (
        GovernedAction.FULL_EXIT.value if exit_requested else GovernedAction.NO_ACTION.value
    )
    proposed_action = final_action
    return DecisionEvent(
        decision_id=(
            "decision_"
            + _canonical_digest(
                [
                    run_id,
                    frame.snapshot.snapshot_id,
                    review.unit_id,
                    review.opportunity_id,
                    review.arbitration.digest,
                ]
            ).removeprefix("sha256:")[:24]
        ),
        run_id=run_id,
        signal_id=signal_id,
        snapshot=frame.snapshot,
        decided_at=frame.decision_at,
        profile_id=profile_id,
        window_id=None,
        security_code=review.security_id,
        unit_id=review.unit_id,
        lifecycle_stage=review.lifecycle_stage.value,
        aggregate_score=Decimal("-1") if exit_requested else Decimal("0"),
        expert_outputs={f"exit.{item.source.value}": item.to_dict() for item in review.evaluations},
        vetoes=tuple(item.value for item in review.arbitration.triggered_sources),
        constraints={
            "constraint_stop_loss": str(policy.exit.constraint_stop_loss),
            "maximum_holding_sessions": str(policy.exit.maximum_holding_sessions),
        },
        proposed_action=proposed_action,
        final_action=final_action,
        reason_codes=review.arbitration.reason_codes,
        policy_versions={
            "strategy": f"{policy.strategy_id}@{policy.strategy_version}",
            "exit_evaluator": GOVERNED_EXIT_EVALUATOR_VERSION,
            "execution": policy.bindings.execution_policy_version,
            "p0": policy.bindings.p0_policy_version,
            "position_state_machine": policy.bindings.position_state_machine_version,
        },
        linked_order_ids=linked_order_ids,
        disposition=disposition,
    )
