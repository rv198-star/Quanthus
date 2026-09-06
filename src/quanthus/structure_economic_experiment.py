"""Preregistered complete-P2 economic comparison for Structure Intelligence.

This module is research-only. It compares exactly three entry-scoring variants over the
accepted 2020-2021 formal-strategy population while preserving the existing position-risk,
allocation, execution, settlement, cost, and held-position paths:

C0: accepted formal strategy native entry scoring.
C1: C0 plus a continuous structure score derived from the accepted Structure MVP trajectory.
C2: C0 plus a coarse structure-state score derived from the same trajectory.

The structure observation is never an execution authority. Missing structure evidence falls back
to C0 because the existing system and strategy veto/constraint path remains authoritative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.aggregator import ScoreActionResult, aggregate_score_and_map_action
from quanthus.expert_evaluation.experts.trend_stability import evaluate_trend_stability
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_protocols import (
    EvidenceKind,
    ExpertApplicability,
    ExpertContext,
    ExpertEvidenceRef,
    ScoringOutput,
    run_scoring_expert,
)
from quanthus.expert_strategy.formal_attribution import FormalReplayEvidence
from quanthus.expert_strategy.formal_p2_replay import (
    DECISION_END,
    DECISION_START,
    EXECUTION_END,
    FormalP2ReplayRuntime,
    _EntryCandidate,
    _stable_id,
    run_paired_block_bootstrap,
)
from quanthus.expert_strategy.runner import build_formal_expert_context
from quanthus.policy import find_project_root
from quanthus.structure_intelligence import (
    StructureAdmRow,
    StructureMvpState,
    StructureResearchAdm,
    StructureSourceRow,
    build_structure_research_adm,
)

RUN_SCHEMA = "quanthus.structure-economic-run.v1"
RESULT_SCHEMA = "quanthus.structure-economic-result.v1"
MANIFEST_SCHEMA = "quanthus.structure-economic-artifact-manifest.v1"
READY_SCHEMA = "quanthus.structure-economic-ready.v1"
DEFAULT_RUN_CONFIG = "config/structure-economic-run.development.v1.json"
DEFAULT_ADM_POLICY = "config/structure-research-adm.mvp.v1.json"
STRUCTURE_SCAN_START = date(2019, 10, 8)
C1_KEY = "structure.continuous.v1"
C2_KEY = "structure.coarse.v1"
VARIANT_IDS = {
    None: "variant.native-baseline",
    C1_KEY: "variant.structure-continuous",
    C2_KEY: "variant.structure-coarse",
}
STRUCTURE_EVIDENCE_VERSION = "0.1.0"
CONTINUOUS_EXPERT_ID = "expert.structure-continuous-research"
COARSE_EXPERT_ID = "expert.structure-coarse-research"
MATERIAL_RETURN = Decimal("0.01")
MATERIAL_DRAWDOWN = Decimal("0.01")
MATERIAL_ACTION_RATE = Decimal("0.02")
EXPECTED_NATIVE_RETURN = Decimal("0.06704618")
EXPECTED_NATIVE_DRAWDOWN = Decimal("0.07102827")
EXPECTED_SCORE_MAPPING = {
    "continuous": {
        "support_threat_band": "0.02",
        "support_at_band_edge": "-0.50",
        "support_at_or_below": "-1.00",
        "breakout_base": "0.50",
        "breakout_margin_scale": "0.02",
        "breakout_margin_weight": "0.25",
        "post_attempt_hold_step": "0.25",
        "maximum_signal": "1.00"
    },
    "coarse": {
        "unavailable": None,
        "range_intact": "0.00",
        "breakout_attempt": "0.50",
        "breakout_accepted": "1.00",
        "support_threatened": "-0.50",
        "support_breach": "-1.00"
    }
}


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


@dataclass(frozen=True)
class StructureEconomicRunRequest:
    provider_config: str
    structure_adm_policy: str
    strategy_id: str
    strategy_version: str
    decision_start: date
    decision_end: date
    execution_end: date
    structure_scan_start: date
    variants: tuple[str, ...]
    primary_comparison: str
    secondary_comparisons: tuple[str, ...]
    material_return: Decimal
    material_drawdown: Decimal
    material_action_rate: Decimal
    bootstrap_block_sessions: int
    bootstrap_resamples: int
    raw_digest: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StructureEconomicRunRequest:
        expected = {
            "schema_version",
            "provider_config",
            "structure_adm_policy",
            "strategy",
            "scope",
            "variants",
            "comparisons",
            "materiality",
            "uncertainty",
            "score_mapping",
            "governance",
        }
        if set(value) != expected or value.get("schema_version") != RUN_SCHEMA:
            raise ValueError("structure economic run fields differ")
        strategy = value.get("strategy")
        scope = value.get("scope")
        variants = value.get("variants")
        comparisons = value.get("comparisons")
        materiality = value.get("materiality")
        uncertainty = value.get("uncertainty")
        score_mapping = value.get("score_mapping")
        governance = value.get("governance")
        if not all(
            isinstance(item, dict)
            for item in (strategy, scope, comparisons, materiality, uncertainty, governance)
        ) or not isinstance(variants, list):
            raise ValueError("structure economic run nested fields are invalid")
        if score_mapping != EXPECTED_SCORE_MAPPING:
            raise ValueError("structure economic score mapping differs")
        if strategy != {
            "strategy_id": "candidate.p4-trend-risk",
            "strategy_version": "1.0.1",
        }:
            raise ValueError("structure economic strategy differs")
        if set(scope) != {
            "decision_start",
            "decision_end",
            "execution_end",
            "structure_scan_start",
        }:
            raise ValueError("structure economic scope fields differ")
        if comparisons.keys() != {"primary", "secondary"}:
            raise ValueError("structure economic comparison fields differ")
        if materiality.keys() != {
            "absolute_return_delta",
            "absolute_drawdown_delta",
            "action_change_rate",
        }:
            raise ValueError("structure economic materiality fields differ")
        if uncertainty != {
            "method": "paired_nonoverlap_22_session_block_bootstrap",
            "block_sessions": 22,
            "resamples": 2000,
            "confidence_level": "0.95",
        }:
            raise ValueError("structure economic uncertainty differs")
        if governance != {
            "partition": "development",
            "sealed_partition_access": False,
            "execution_authorized": False,
            "shadow_authorized": False,
            "model_training_authorized": False,
            "result_driven_parameter_changes": False,
        }:
            raise ValueError("structure economic governance differs")
        expected_variants = [
            {
                "variant_id": "variant.native-baseline",
                "structure_effect": "none",
            },
            {
                "variant_id": "variant.structure-continuous",
                "structure_effect": (
                    "second_entry_scoring_output_from_continuous_structure_intensity"
                ),
            },
            {
                "variant_id": "variant.structure-coarse",
                "structure_effect": "second_entry_scoring_output_from_coarse_structure_state",
            },
        ]
        if variants != expected_variants:
            raise ValueError("structure economic variants differ")
        variant_ids = tuple(str(item["variant_id"]) for item in variants)
        if variant_ids != (
            "variant.native-baseline",
            "variant.structure-continuous",
            "variant.structure-coarse",
        ):
            raise ValueError("structure economic variants differ")
        secondary = comparisons.get("secondary")
        if not isinstance(secondary, list):
            raise ValueError("structure economic secondary comparisons are invalid")
        request = cls(
            provider_config=str(value["provider_config"]),
            structure_adm_policy=str(value["structure_adm_policy"]),
            strategy_id=str(strategy.get("strategy_id")),
            strategy_version=str(strategy.get("strategy_version")),
            decision_start=date.fromisoformat(str(scope.get("decision_start"))),
            decision_end=date.fromisoformat(str(scope.get("decision_end"))),
            execution_end=date.fromisoformat(str(scope.get("execution_end"))),
            structure_scan_start=date.fromisoformat(str(scope.get("structure_scan_start"))),
            variants=variant_ids,
            primary_comparison=str(comparisons.get("primary")),
            secondary_comparisons=tuple(str(item) for item in secondary),
            material_return=Decimal(str(materiality.get("absolute_return_delta"))),
            material_drawdown=Decimal(str(materiality.get("absolute_drawdown_delta"))),
            material_action_rate=Decimal(str(materiality.get("action_change_rate"))),
            bootstrap_block_sessions=int(uncertainty.get("block_sessions")),
            bootstrap_resamples=int(uncertainty.get("resamples")),
            raw_digest=digest_value(dict(value)),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.provider_config != "config/replay-data-provider.development.v2.json":
            raise ValueError("structure economic provider differs")
        if self.structure_adm_policy != DEFAULT_ADM_POLICY:
            raise ValueError("structure economic ADM policy differs")
        if (self.strategy_id, self.strategy_version) != ("candidate.p4-trend-risk", "1.0.1"):
            raise ValueError("structure economic strategy differs")
        if (self.decision_start, self.decision_end, self.execution_end) != (
            DECISION_START,
            DECISION_END,
            EXECUTION_END,
        ):
            raise ValueError("structure economic formal scope differs")
        if self.structure_scan_start != STRUCTURE_SCAN_START:
            raise ValueError("structure economic structure scan start differs")
        if self.primary_comparison != "C2-C1" or self.secondary_comparisons != ("C1-C0", "C2-C0"):
            raise ValueError("structure economic comparison order differs")
        if (
            self.material_return != MATERIAL_RETURN
            or self.material_drawdown != MATERIAL_DRAWDOWN
            or self.material_action_rate != MATERIAL_ACTION_RATE
        ):
            raise ValueError("structure economic materiality differs")
        if self.bootstrap_block_sessions != 22 or self.bootstrap_resamples != 2000:
            raise ValueError("structure economic uncertainty differs")

    @property
    def digest(self) -> str:
        return self.raw_digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_SCHEMA,
            "provider_config": self.provider_config,
            "structure_adm_policy": self.structure_adm_policy,
            "strategy": {
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
            },
            "scope": {
                "decision_start": self.decision_start.isoformat(),
                "decision_end": self.decision_end.isoformat(),
                "execution_end": self.execution_end.isoformat(),
                "structure_scan_start": self.structure_scan_start.isoformat(),
            },
            "variants": list(self.variants),
            "comparisons": {
                "primary": self.primary_comparison,
                "secondary": list(self.secondary_comparisons),
            },
            "materiality": {
                "absolute_return_delta": str(self.material_return),
                "absolute_drawdown_delta": str(self.material_drawdown),
                "action_change_rate": str(self.material_action_rate),
            },
            "uncertainty": {
                "block_sessions": self.bootstrap_block_sessions,
                "resamples": self.bootstrap_resamples,
                "confidence_level": "0.95",
            },
        }


def load_structure_economic_request(path: Path) -> StructureEconomicRunRequest:
    return StructureEconomicRunRequest.from_dict(_load_json(path, "structure economic request"))


@dataclass(frozen=True)
class EconomicStructureObservation:
    row: StructureAdmRow
    active_attempt_level: Decimal | None
    attempt_hold_count: int
    continuous_signal: Decimal | None
    coarse_signal: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row.to_dict(),
            "active_attempt_level": (
                None if self.active_attempt_level is None else str(self.active_attempt_level)
            ),
            "attempt_hold_count": self.attempt_hold_count,
            "continuous_signal": (
                None if self.continuous_signal is None else str(self.continuous_signal)
            ),
            "coarse_signal": None if self.coarse_signal is None else str(self.coarse_signal),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())


def _coarse_signal(state: StructureMvpState) -> Decimal | None:
    return {
        StructureMvpState.UNAVAILABLE: None,
        StructureMvpState.RANGE_INTACT: Decimal(
            EXPECTED_SCORE_MAPPING["coarse"]["range_intact"]
        ),
        StructureMvpState.BREAKOUT_ATTEMPT: Decimal(
            EXPECTED_SCORE_MAPPING["coarse"]["breakout_attempt"]
        ),
        StructureMvpState.BREAKOUT_ACCEPTED: Decimal(
            EXPECTED_SCORE_MAPPING["coarse"]["breakout_accepted"]
        ),
        StructureMvpState.SUPPORT_THREATENED: Decimal(
            EXPECTED_SCORE_MAPPING["coarse"]["support_threatened"]
        ),
        StructureMvpState.SUPPORT_BREACH: Decimal(
            EXPECTED_SCORE_MAPPING["coarse"]["support_breach"]
        ),
    }[state]


def _continuous_signal(
    row: StructureAdmRow,
    *,
    active_attempt_level: Decimal | None,
    attempt_hold_count: int,
) -> Decimal | None:
    if row.state is StructureMvpState.UNAVAILABLE:
        return None
    if row.state is StructureMvpState.RANGE_INTACT:
        return Decimal("0")
    if row.state is StructureMvpState.SUPPORT_BREACH:
        return Decimal("-1")
    if row.state is StructureMvpState.SUPPORT_THREATENED:
        if row.prior_low20 is None or row.prior_low20 <= 0:
            return None
        margin = row.close / row.prior_low20 - Decimal("1")
        band = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["support_threat_band"])
        lower = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["support_at_or_below"])
        upper = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["support_at_band_edge"])
        margin = min(band, max(Decimal("0"), margin))
        return lower + (upper - lower) * (margin / band)
    if row.state in {StructureMvpState.BREAKOUT_ATTEMPT, StructureMvpState.BREAKOUT_ACCEPTED}:
        if active_attempt_level is None or active_attempt_level <= 0:
            return None
        margin = max(Decimal("0"), row.close / active_attempt_level - Decimal("1"))
        margin_scale = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["breakout_margin_scale"])
        margin_weight = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["breakout_margin_weight"])
        hold_step = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["post_attempt_hold_step"])
        base = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["breakout_base"])
        maximum = Decimal(EXPECTED_SCORE_MAPPING["continuous"]["maximum_signal"])
        margin_component = min(Decimal("1"), margin / margin_scale) * margin_weight
        progress_component = Decimal(min(2, max(0, attempt_hold_count))) * hold_step
        return min(maximum, base + margin_component + progress_component)
    raise ValueError("unknown structure state")


def build_economic_structure_observations(
    adm: StructureResearchAdm,
) -> tuple[EconomicStructureObservation, ...]:
    active: dict[str, Decimal | None] = {}
    holds: dict[str, int] = {}
    output: list[EconomicStructureObservation] = []
    for row in adm.rows:
        code = row.security_code
        level = active.get(code)
        hold_count = holds.get(code, 0)
        clear_after = False
        if row.state is StructureMvpState.UNAVAILABLE:
            level = None
            hold_count = 0
            clear_after = True
        elif row.reason_code == "close_above_prior_high20_with_volume":
            if row.prior_high20 is None:
                raise ValueError("breakout attempt lacks prior high")
            level = row.prior_high20
            hold_count = 0
        elif row.reason_code == "breakout_confirmation_pending":
            if level is None:
                raise ValueError("breakout confirmation lacks active attempt")
            hold_count += 1
        elif row.state is StructureMvpState.BREAKOUT_ACCEPTED:
            if level is None:
                raise ValueError("breakout acceptance lacks active attempt")
            hold_count += 1
            clear_after = True
        elif row.state is StructureMvpState.SUPPORT_BREACH:
            clear_after = True
        observation = EconomicStructureObservation(
            row=row,
            active_attempt_level=level,
            attempt_hold_count=hold_count,
            continuous_signal=_continuous_signal(
                row,
                active_attempt_level=level,
                attempt_hold_count=hold_count,
            ),
            coarse_signal=_coarse_signal(row.state),
        )
        output.append(observation)
        if clear_after:
            active[code] = None
            holds[code] = 0
        else:
            if (
                row.state is StructureMvpState.RANGE_INTACT
                and level is not None
                and row.close < level
            ):
                level = None
                hold_count = 0
            active[code] = level
            holds[code] = hold_count
    return tuple(output)


class _FixedStructureScorer:
    expert_version = STRUCTURE_EVIDENCE_VERSION

    def __init__(self, *, expert_id: str, evidence_id: str, signal: Decimal | None) -> None:
        self.expert_id = expert_id
        self.evidence_id = evidence_id
        self.signal = signal

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        evidence = tuple(
            item for item in context.evidence_refs if item.evidence_id == self.evidence_id
        )
        if len(evidence) != 1:
            raise ValueError("structure economic evidence is missing")
        if self.signal is None:
            return ScoringOutput(
                self.expert_id,
                self.expert_version,
                ExpertApplicability.ABSTAINED,
                "structure_evidence_unavailable",
                "structure_evidence_unavailable",
                evidence,
                None,
                None,
            )
        return ScoringOutput(
            self.expert_id,
            self.expert_version,
            ExpertApplicability.APPLICABLE,
            "structure_research_score_available",
            "structure_research_score_available",
            evidence,
            self.signal,
            Decimal("1"),
        )


@dataclass(frozen=True)
class StructureEconomicDecision:
    event_id: str
    variant_id: str
    base_formal_decision_digest: str
    structure_observation_digest: str | None
    structure_state: str | None
    structure_signal: Decimal | None
    action: ScoreActionResult
    target_maximum_position: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.structure-economic-decision.v1",
            "event_id": self.event_id,
            "variant_id": self.variant_id,
            "base_formal_decision_digest": self.base_formal_decision_digest,
            "structure_observation_digest": self.structure_observation_digest,
            "structure_state": self.structure_state,
            "structure_signal": (
                None if self.structure_signal is None else str(self.structure_signal)
            ),
            "action": self.action.to_dict(),
            "target_maximum_position": (
                None if self.target_maximum_position is None else str(self.target_maximum_position)
            ),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())


class StructureEconomicP2ReplayRuntime(FormalP2ReplayRuntime):
    def __init__(self, repository_root: Path, request: StructureEconomicRunRequest) -> None:
        self.request = request
        super().__init__(repository_root)
        if (
            self.registry.card.strategy_id != request.strategy_id
            or self.registry.card.strategy_version != request.strategy_version
        ):
            raise ValueError("structure economic formal strategy identity differs")
        self.structure_adm, self.structure_observations = self._build_structure_observations()
        self.structure_by_key = {
            (item.row.market_date, item.row.security_code): item
            for item in self.structure_observations
            if item.row.market_date >= request.decision_start
        }

    def _provider_scope_start(self) -> date:
        return self.request.structure_scan_start

    def _validate_intervention_id(self, intervention_id: str | None) -> None:
        if intervention_id not in {None, C1_KEY, C2_KEY}:
            raise ValueError("structure economic variant is not admitted")

    def _held_intervention_id(self, intervention_id: str | None) -> str | None:
        return None

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        try:
            return VARIANT_IDS[intervention_id]
        except KeyError as exc:
            raise ValueError("structure economic variant is not admitted") from exc

    def _build_structure_observations(
        self,
    ) -> tuple[StructureResearchAdm, tuple[EconomicStructureObservation, ...]]:
        identity_sets: dict[str, set[str]] = {}
        for item in self.events:
            identity_sets.setdefault(item.panel_security_id, set()).add(
                item.record.identity.security_id
            )
        invalid = {key: sorted(value) for key, value in identity_sets.items() if len(value) != 1}
        if invalid:
            raise ValueError(f"structure economic security identity is ambiguous: {invalid}")
        identity_map = {key: next(iter(value)) for key, value in identity_sets.items()}
        selected = tuple(sorted(identity_map))
        provider_revision = str(self.session.identity.get("provider_lineage_digest") or "")
        source_artifact = str(self.session.identity.get("materialized_content_digest") or "")
        if not provider_revision.startswith("sha256:") or not source_artifact.startswith("sha256:"):
            raise ValueError("structure economic provider lineage is incomplete")
        rows: list[StructureSourceRow] = []
        reset_pending = {code: False for code in selected}
        for frame in self.session.reader.frames(
            self.request.structure_scan_start, self.request.decision_end
        ):
            if frame.decision_view is None:
                raise ValueError("structure economic run requires the PIT decision view")
            bars = {item.security_code: item for item in frame.decision_view.current_bars()}
            for code in selected:
                bar = bars.get(code)
                if (
                    bar is None
                    or not bar.data_valid
                    or bar.high_price is None
                    or bar.low_price is None
                    or bar.close_price is None
                ):
                    reset_pending[code] = True
                    continue
                rows.append(
                    StructureSourceRow(
                        market_date=frame.snapshot.market_date,
                        security_code=code,
                        dwd_security_id=identity_map[code],
                        snapshot_id=frame.snapshot.snapshot_id,
                        knowledge_cutoff=frame.snapshot.knowledge_cutoff,
                        high=bar.high_price,
                        low=bar.low_price,
                        close=bar.close_price,
                        volume=bar.volume,
                        source_revision=provider_revision,
                        source_artifact_sha256=source_artifact,
                        history_reset=reset_pending[code],
                    )
                )
                reset_pending[code] = False
        policy = _load_json(
            self.repository_root / self.request.structure_adm_policy,
            "structure ADM policy",
        )
        adm = build_structure_research_adm(
            rows,
            upstream_identity=digest_value(
                {
                    "provider_lineage": provider_revision,
                    "source_artifact": source_artifact,
                    "security_count": len(selected),
                    "security_digest": digest_value(selected),
                    "scan_start": self.request.structure_scan_start.isoformat(),
                    "decision_end": self.request.decision_end.isoformat(),
                }
            ),
            policy_digest=digest_value(policy),
        )
        return adm, build_economic_structure_observations(adm)

    def _entry_candidates(
        self,
        frame: Any,
        runner: Any,
        intervention_id: str | None,
    ) -> tuple[_EntryCandidate, ...]:
        if intervention_id is None:
            return super()._entry_candidates(frame, runner, None)
        variant_id = self._variant_id(intervention_id)
        output: list[_EntryCandidate] = []
        for item in sorted(
            self.events_by_date.get(frame.snapshot.market_date, ()),
            key=lambda value: value.record.event_id,
        ):
            feature = self.panel.feature(item.record.identity.signal_date, item.panel_security_id)
            formal_feature = (
                None
                if feature is None
                else replace(feature, security_id=item.record.identity.security_id)
            )
            observation = evaluate_trend_stability(
                event=item.record,
                feature=formal_feature,
                config=self.trend_config,
                spec=self.trend_spec,
            )
            context = build_formal_expert_context(
                event=item.record,
                feature=formal_feature,
                feature_registry=self.feature_registry,
                panel_report=self.panel.report,
                position_state="empty",
                trend_observation_digest=observation.digest,
            )
            native = runner.evaluate_entry(
                event_id=item.record.event_id,
                context=context,
                trend_state=(None if observation.raw_value is None else str(observation.raw_value)),
                entry_evidence_ready=formal_feature is not None and formal_feature.entry_eligible,
                intervention_id=None,
            )
            structure = self.structure_by_key.get(
                (item.record.identity.signal_date, item.panel_security_id)
            )
            signal = None
            structure_state = None
            structure_digest = None
            if structure is not None:
                structure_state = structure.row.state.value
                structure_digest = structure.digest
                signal = (
                    structure.continuous_signal
                    if intervention_id == C1_KEY
                    else structure.coarse_signal
                )
            evidence_id = f"structure-economic:{item.record.event_id}:{variant_id}"
            evidence_ref = ExpertEvidenceRef(
                EvidenceKind.SNAPSHOT,
                evidence_id,
                STRUCTURE_EVIDENCE_VERSION,
                digest_value(
                    {
                        "variant_id": variant_id,
                        "observation_digest": structure_digest,
                        "state": structure_state,
                        "signal": None if signal is None else str(signal),
                    }
                ),
            )
            context = replace(
                context,
                additional_evidence_refs=tuple(
                    sorted(
                        (*context.additional_evidence_refs, evidence_ref),
                        key=lambda value: value.key,
                    )
                ),
            )
            scorer = _FixedStructureScorer(
                expert_id=(CONTINUOUS_EXPERT_ID if intervention_id == C1_KEY else COARSE_EXPERT_ID),
                evidence_id=evidence_id,
                signal=signal,
            )
            structure_score = run_scoring_expert(scorer, context)
            scores = (*native.action.scoring_outputs, structure_score)
            action = (
                native.action
                if structure_score.applicability is ExpertApplicability.ABSTAINED
                else aggregate_score_and_map_action(
                    context,
                    native.governance,
                    scores,
                    runner.score_policy,
                )
            )
            decision = StructureEconomicDecision(
                event_id=item.record.event_id,
                variant_id=variant_id,
                base_formal_decision_digest=native.digest,
                structure_observation_digest=structure_digest,
                structure_state=structure_state,
                structure_signal=signal,
                action=action,
                target_maximum_position=native.target_maximum_position,
            )
            output.append(
                _EntryCandidate(
                    item,
                    feature,
                    decision,
                    _stable_id("opportunity", item.record.event_id),
                )
            )
        return tuple(output)


def _write_variant(
    root: Path,
    evidence: FormalReplayEvidence,
    artifacts: Mapping[str, bytes],
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=False)
    for name, payload in sorted(artifacts.items()):
        _write_bytes(root / name, payload)
    return {
        "variant_id": evidence.variant_id,
        "intervention_id": evidence.intervention_id,
        "metrics": {key: str(value) for key, value in sorted(evidence.metrics.items())},
        "replay_digest": evidence.replay_digest,
        "reconciliation_digest": evidence.reconciliation_digest,
        "decision_trace_digest": evidence.decision_trace_digest,
        "opportunity_trace_digest": evidence.opportunity_trace_digest,
        "daily_return_artifact_digest": evidence.daily_return_artifact_digest,
        "files": {
            name: _sha256_bytes(payload) for name, payload in sorted(artifacts.items())
        },
    }


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("structure economic decision artifact is invalid")
    return value


def _entry_action_map(decisions: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Pair only the stable candidate-entry surface across capital-path variants.

    Structure intervenes only on entry scoring. Held-review decision identities are downstream
    consequences of earlier entry/allocation changes and therefore are not a stable paired
    population. Their economic effects remain fully represented in the complete P2 equity,
    drawdown, turnover, fee, and exposure paths.
    """

    pairs = [
        (
            (str(item["market_date"]), str(item["decision_key"])),
            str(item["final_action"]),
        )
        for item in decisions
        if item.get("surface") == "entry"
    ]
    if not pairs or any(not key[1].startswith("entry:") for key, _action in pairs):
        raise ValueError("structure economic entry decision population is invalid")
    if len(pairs) != len({key for key, _action in pairs}):
        raise ValueError("structure economic entry decision population contains duplicates")
    return dict(pairs)


def _comparison(
    *,
    candidate_id: str,
    candidate_root: Path,
    candidate_metrics: Mapping[str, str],
    baseline_id: str,
    baseline_root: Path,
    baseline_metrics: Mapping[str, str],
    strategy_digest: str,
    question_id: str,
) -> dict[str, Any]:
    candidate_decisions = _load_decisions(candidate_root / "decisions.json")
    baseline_decisions = _load_decisions(baseline_root / "decisions.json")
    baseline_map = _entry_action_map(baseline_decisions)
    candidate_map = _entry_action_map(candidate_decisions)
    if set(candidate_map) != set(baseline_map):
        raise ValueError("structure economic entry decision populations differ")
    changed = sum(candidate_map[key] != baseline_map[key] for key in sorted(baseline_map))
    action_rate = (
        Decimal(changed) / Decimal(len(baseline_map)) if baseline_map else Decimal("0")
    )
    return_delta = Decimal(
        candidate_metrics["capital_constrained_net_total_return_delta"]
    ) - Decimal(baseline_metrics["capital_constrained_net_total_return_delta"])
    drawdown_delta = Decimal(candidate_metrics["maximum_drawdown_delta"]) - Decimal(
        baseline_metrics["maximum_drawdown_delta"]
    )
    tail_delta = Decimal(candidate_metrics["tail_loss_delta"]) - Decimal(
        baseline_metrics["tail_loss_delta"]
    )
    material = (
        abs(return_delta) >= MATERIAL_RETURN
        or abs(drawdown_delta) >= MATERIAL_DRAWDOWN
        or abs(action_rate) >= MATERIAL_ACTION_RATE
    )
    bootstrap = run_paired_block_bootstrap(
        baseline_root=candidate_root,
        intervention_root=baseline_root,
        question_id=question_id,
        method="paired_nonoverlap_22_session_block_bootstrap_candidate_minus_baseline",
        strategy_card_digest=strategy_digest,
    )
    return {
        "candidate": candidate_id,
        "baseline": baseline_id,
        "return_delta": str(return_delta),
        "maximum_drawdown_delta": str(drawdown_delta),
        "tail_loss_delta": str(tail_delta),
        "decision_change_count": changed,
        "action_change_rate": str(action_rate),
        "material": material,
        "bootstrap_mean_daily_return_difference": bootstrap,
    }


def _lifecycle_decision(comparisons: Mapping[str, Mapping[str, Any]]) -> str:
    c1_c0 = comparisons["C1-C0"]
    c2_c0 = comparisons["C2-C0"]
    c2_c1 = comparisons["C2-C1"]
    c1_return = Decimal(str(c1_c0["return_delta"]))
    c2_return = Decimal(str(c2_c0["return_delta"]))
    c1_dd = Decimal(str(c1_c0["maximum_drawdown_delta"]))
    c2_dd = Decimal(str(c2_c0["maximum_drawdown_delta"]))

    # The preregistration explicitly makes C2-C1 the primary question. Evaluate that
    # comparison before the C1/C2-vs-C0 secondary questions.
    if c2_c1["material"]:
        c2_dominated = c2_return <= c1_return and c2_dd >= c1_dd and (
            c2_return < c1_return or c2_dd > c1_dd
        )
        if c2_dominated:
            return "prefer_continuous_structure_facts"
        return "coarse_state_increment_detected_requires_human_review"
    if c1_c0["material"] or c2_c0["material"]:
        return "continuous_facts_sufficient_no_material_coarse_increment"
    return "no_material_structure_signal"


def run_structure_economic_experiment(
    *,
    repository_root: Path,
    request: StructureEconomicRunRequest,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if len(code_revision) != 40 or any(
        char not in "0123456789abcdef" for char in code_revision
    ):
        raise ValueError("structure economic code revision must be a lowercase 40-char SHA")
    output_root = output_root.resolve()
    if output_root.exists():
        raise ValueError("structure economic output already exists")
    output_root.mkdir(parents=True)
    variants_root = output_root / "variants"
    variants_root.mkdir()
    with StructureEconomicP2ReplayRuntime(repository_root, request) as runtime:
        variant_results: dict[str, dict[str, Any]] = {}
        roots: dict[str, Path] = {}
        for intervention_id in (None, C1_KEY, C2_KEY):
            variant_id = runtime._variant_id(intervention_id)
            root = variants_root / variant_id
            evidence, artifacts = runtime.run_variant(intervention_id, root)
            variant_results[variant_id] = _write_variant(root, evidence, artifacts)
            roots[variant_id] = root
            if intervention_id is None:
                metrics = evidence.metrics
                if (
                    metrics["capital_constrained_net_total_return_delta"] != EXPECTED_NATIVE_RETURN
                    or metrics["maximum_drawdown_delta"] != EXPECTED_NATIVE_DRAWDOWN
                ):
                    raise ValueError("structure economic C0 baseline reproduction mismatch")
        strategy_digest = runtime.registry.card.semantic_digest
        comparisons = {
            "C1-C0": _comparison(
                candidate_id="variant.structure-continuous",
                candidate_root=roots["variant.structure-continuous"],
                candidate_metrics=variant_results["variant.structure-continuous"]["metrics"],
                baseline_id="variant.native-baseline",
                baseline_root=roots["variant.native-baseline"],
                baseline_metrics=variant_results["variant.native-baseline"]["metrics"],
                strategy_digest=strategy_digest,
                question_id="question.structure-continuous-vs-native",
            ),
            "C2-C0": _comparison(
                candidate_id="variant.structure-coarse",
                candidate_root=roots["variant.structure-coarse"],
                candidate_metrics=variant_results["variant.structure-coarse"]["metrics"],
                baseline_id="variant.native-baseline",
                baseline_root=roots["variant.native-baseline"],
                baseline_metrics=variant_results["variant.native-baseline"]["metrics"],
                strategy_digest=strategy_digest,
                question_id="question.structure-coarse-vs-native",
            ),
            "C2-C1": _comparison(
                candidate_id="variant.structure-coarse",
                candidate_root=roots["variant.structure-coarse"],
                candidate_metrics=variant_results["variant.structure-coarse"]["metrics"],
                baseline_id="variant.structure-continuous",
                baseline_root=roots["variant.structure-continuous"],
                baseline_metrics=variant_results["variant.structure-continuous"]["metrics"],
                strategy_digest=strategy_digest,
                question_id="question.structure-coarse-vs-continuous",
            ),
        }
        structure_rows = tuple(
            item
            for item in runtime.structure_adm.rows
            if request.decision_start <= item.market_date <= request.decision_end
        )
        state_counts: dict[str, int] = {}
        for item in structure_rows:
            state_counts[item.state.value] = state_counts.get(item.state.value, 0) + 1
        summary = {
            "schema_version": RESULT_SCHEMA,
            "research_only": True,
            "execution_authorized": False,
            "shadow_authorized": False,
            "sealed_partition_access": False,
            "economic_claim_scope": "development_counterfactual_only",
            "code_revision": code_revision,
            "request_digest": request.digest,
            "strategy_id": runtime.registry.card.strategy_id,
            "strategy_version": runtime.registry.card.strategy_version,
            "strategy_digest": strategy_digest,
            "provider_identity": dict(runtime.session.identity),
            "structure_adm_manifest": runtime.structure_adm.manifest,
            "structure_adm_manifest_digest": runtime.structure_adm.manifest_digest,
            "structure_report_row_count": len(structure_rows),
            "structure_state_counts": dict(sorted(state_counts.items())),
            "variants": variant_results,
            "comparisons": comparisons,
            "primary_comparison": "C2-C1",
            "lifecycle_decision": _lifecycle_decision(comparisons),
            "claim_ceiling": (
                "development historical simulator evidence only; no forward causality, promotion, "
                "execution authority, or thirteen-state activation"
            ),
        }
    summary["result_digest"] = digest_value(
        {key: value for key, value in summary.items() if key != "result_digest"}
    )
    summary_bytes = canonical_json_bytes(summary)
    _write_bytes(output_root / "summary.json", summary_bytes)
    files: dict[str, str] = {"summary.json": _sha256_bytes(summary_bytes)}
    for path in sorted(variants_root.rglob("*")):
        if path.is_file():
            files[path.relative_to(output_root).as_posix()] = _sha256_bytes(path.read_bytes())
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "research_only": True,
        "execution_authorized": False,
        "code_revision": code_revision,
        "request_digest": request.digest,
        "result_digest": summary["result_digest"],
        "structure_adm_manifest_digest": summary["structure_adm_manifest_digest"],
        "files": files,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    _write_bytes(output_root / "manifest.json", manifest_bytes)
    ready = {
        "schema_version": READY_SCHEMA,
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "result_digest": summary["result_digest"],
    }
    _write_bytes(output_root / "READY", canonical_json_bytes(ready))
    return summary


def verify_structure_economic_artifact(
    root: Path, *, code_revision: str | None = None, request_digest: str | None = None
) -> dict[str, Any]:
    root = root.resolve()
    manifest = _load_json(root / "manifest.json", "structure economic manifest")
    summary = _load_json(root / "summary.json", "structure economic summary")
    ready = _load_json(root / "READY", "structure economic READY")
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or summary.get("schema_version") != RESULT_SCHEMA
    ):
        raise ValueError("structure economic artifact schema differs")
    if ready.get("schema_version") != READY_SCHEMA:
        raise ValueError("structure economic READY schema differs")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise ValueError("structure economic code revision differs")
    if request_digest is not None and manifest.get("request_digest") != request_digest:
        raise ValueError("structure economic request digest differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("structure economic manifest files are invalid")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise ValueError("structure economic manifest file entry is invalid")
        path = (root / name).resolve()
        if (
            root not in path.parents
            or not path.is_file()
            or _sha256_bytes(path.read_bytes()) != expected
        ):
            raise ValueError("structure economic artifact file digest differs")
    manifest_sha = _sha256_bytes((root / "manifest.json").read_bytes())
    if ready.get("manifest_sha256") != manifest_sha:
        raise ValueError("structure economic READY manifest digest differs")
    semantic_summary = {key: value for key, value in summary.items() if key != "result_digest"}
    if digest_value(semantic_summary) != summary.get("result_digest"):
        raise ValueError("structure economic semantic result digest differs")
    if (
        manifest.get("result_digest") != summary.get("result_digest")
        or ready.get("result_digest") != summary.get("result_digest")
    ):
        raise ValueError("structure economic result digest differs")
    return {
        "artifact_ref": str(root),
        "result_digest": str(summary["result_digest"]),
        "structure_adm_manifest_digest": str(summary["structure_adm_manifest_digest"]),
        "lifecycle_decision": str(summary["lifecycle_decision"]),
        "comparisons": dict(summary["comparisons"]),
        "variants": dict(summary["variants"]),
        "sealed_partition_access": bool(summary["sealed_partition_access"]),
        "execution_authorized": bool(summary["execution_authorized"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bounded Structure economic experiment")
    parser.add_argument("--request", default=DEFAULT_RUN_CONFIG)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = find_project_root()
    request_path = (repository_root / args.request).resolve()
    if repository_root not in request_path.parents:
        raise ValueError("structure economic request is outside the repository")
    request = load_structure_economic_request(request_path)
    output = Path(args.output)
    try:
        run_structure_economic_experiment(
            repository_root=repository_root,
            request=request,
            output_root=output,
            code_revision=args.code_revision,
        )
    except BaseException:
        if output.exists():
            shutil.rmtree(output, ignore_errors=True)
        raise
    verify_structure_economic_artifact(
        output,
        code_revision=args.code_revision,
        request_digest=request.digest,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
