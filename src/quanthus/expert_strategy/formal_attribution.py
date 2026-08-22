"""Trusted generic-Pipeline executor for the formal strategy attribution Run."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy.formal_attribution_semantics import (
    METRIC_CALCULATOR_ID,
    METRIC_CALCULATOR_VERSION,
    SOURCE_KIND,
    FormalStrategyAttributionAdapter,
)
from quanthus.strategy_attribution import (
    FORMAL_ARTIFACT_PRODUCER_ID,
    FORMAL_ARTIFACT_PRODUCER_VERSION,
    AnalysisCheckKind,
    digest,
)
from quanthus.strategy_attribution_pipeline import (
    ExecutorBinding,
    PlannedExecutionWork,
    RawAnalysisCheck,
    RawExecutionResult,
    raw_observation_basis,
    register_formal_executor_implementation,
)
from quanthus.strategy_attribution_planning import PlanNodeKind

FORMAL_REPLAY_EVIDENCE_SCHEMA = "quanthus.formal-p2-attribution-replay-evidence.v1"
FORMAL_BOOTSTRAP_EVIDENCE_SCHEMA = "quanthus.formal-attribution-bootstrap-evidence.v1"


@dataclass(frozen=True)
class FormalReplayEvidence:
    variant_id: str
    intervention_id: str | None
    metrics: Mapping[str, Decimal]
    replay_digest: str
    reconciliation_digest: str
    decision_trace_digest: str
    opportunity_trace_digest: str
    daily_return_artifact_digest: str
    sealed_partition_access: bool = False

    def __post_init__(self) -> None:
        if self.sealed_partition_access:
            raise ExpertEvaluationError(
                "sealed_partition_access", "formal replay evidence accessed a sealed partition"
            )
        expected = {
            "capital_constrained_net_total_return_delta",
            "maximum_drawdown_delta",
            "tail_loss_delta",
            "average_invested_capital_delta",
            "time_in_market_delta",
            "turnover_delta",
            "fee_delta",
            "missed_opportunity_count",
            "decision_change_counts",
            "action_change_rate",
        }
        if set(self.metrics) != expected:
            raise ExpertEvaluationError(
                "formal_metric_set_mismatch", "formal replay metric set is incomplete"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FORMAL_REPLAY_EVIDENCE_SCHEMA,
            "variant_id": self.variant_id,
            "intervention_id": self.intervention_id,
            "metrics": {key: str(value) for key, value in sorted(self.metrics.items())},
            "replay_digest": self.replay_digest,
            "reconciliation_digest": self.reconciliation_digest,
            "decision_trace_digest": self.decision_trace_digest,
            "opportunity_trace_digest": self.opportunity_trace_digest,
            "daily_return_artifact_digest": self.daily_return_artifact_digest,
            "sealed_partition_access": self.sealed_partition_access,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> FormalReplayEvidence:
        try:
            raw = json.loads(payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExpertEvaluationError(
                "formal_replay_evidence_unreadable", "formal replay evidence is unreadable"
            ) from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != FORMAL_REPLAY_EVIDENCE_SCHEMA:
            raise ExpertEvaluationError(
                "formal_replay_evidence_invalid", "formal replay evidence schema differs"
            )
        try:
            return cls(
                variant_id=raw["variant_id"],
                intervention_id=raw["intervention_id"],
                metrics={key: Decimal(value) for key, value in raw["metrics"].items()},
                replay_digest=raw["replay_digest"],
                reconciliation_digest=raw["reconciliation_digest"],
                decision_trace_digest=raw["decision_trace_digest"],
                opportunity_trace_digest=raw["opportunity_trace_digest"],
                daily_return_artifact_digest=raw["daily_return_artifact_digest"],
                sealed_partition_access=raw["sealed_partition_access"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ExpertEvaluationError):
                raise
            raise ExpertEvaluationError(
                "formal_replay_evidence_invalid", "formal replay evidence fields differ"
            ) from exc


class FormalP2ReplayBackend:
    """Persist and resume exact semantic variants independently of Pipeline memory."""

    def __init__(self, repository_root: Path, workspace_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._runtime: Any | None = None

    @staticmethod
    def variant_id(intervention_id: str | None) -> str:
        return {
            None: "variant.native-baseline",
            "intervention.trend-stability-abstain": "variant.trend-fallback",
            "intervention.position-risk-base-policy": "variant.position-risk-fallback",
            "intervention.both-experts-fallback": "variant.joint-fallback",
        }[intervention_id]

    def _variant_root(self, intervention_id: str | None) -> Path:
        return self.workspace_root / "variants" / self.variant_id(intervention_id)

    def load(self, intervention_id: str | None) -> FormalReplayEvidence | None:
        path = self._variant_root(intervention_id) / "evidence.json"
        if not path.is_file():
            return None
        evidence = FormalReplayEvidence.from_bytes(path.read_bytes())
        if (
            evidence.variant_id != self.variant_id(intervention_id)
            or evidence.intervention_id != intervention_id
        ):
            raise ExpertEvaluationError(
                "formal_replay_variant_mismatch", "persisted replay variant differs"
            )
        return evidence

    def execute_variant(self, intervention_id: str | None) -> FormalReplayEvidence:
        existing = self.load(intervention_id)
        if existing is not None:
            return existing
        if self._runtime is None:
            from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime

            self._runtime = FormalP2ReplayRuntime(self.repository_root)
        evidence, artifacts = self._runtime.run_variant(
            intervention_id, self._variant_root(intervention_id)
        )
        target = self._variant_root(intervention_id)
        target.mkdir(parents=True, exist_ok=True)
        for relative, payload in artifacts.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != payload:
                raise ExpertEvaluationError(
                    "immutable_formal_replay_conflict", "formal replay artifact differs"
                )
            path.write_bytes(payload)
        path = target / "evidence.json"
        payload = canonical_json_bytes(evidence.to_dict())
        if path.exists() and path.read_bytes() != payload:
            raise ExpertEvaluationError(
                "immutable_formal_replay_conflict", "formal replay evidence differs"
            )
        path.write_bytes(payload)
        if all(
            self.load(item) is not None
            for item in (
                None,
                "intervention.trend-stability-abstain",
                "intervention.position-risk-base-policy",
                "intervention.both-experts-fallback",
            )
        ):
            self._runtime.close()
            self._runtime = None
        return evidence

    def run_uncertainty(
        self, intervention_id: str, question_id: str, method: str
    ) -> tuple[bool, str]:
        baseline = self.load(None)
        intervention = self.load(intervention_id)
        if baseline is None or intervention is None:
            raise ExpertEvaluationError(
                "formal_replay_dependency_missing", "bootstrap requires both paired replays"
            )
        from quanthus.expert_strategy.formal_p2_replay import run_paired_block_bootstrap

        payload = run_paired_block_bootstrap(
            baseline_root=self._variant_root(None),
            intervention_root=self._variant_root(intervention_id),
            question_id=question_id,
            method=method,
        )
        digest = digest_value(payload)
        target = self.workspace_root / "uncertainty" / f"{question_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_json_bytes(payload)
        if target.exists() and target.read_bytes() != encoded:
            raise ExpertEvaluationError(
                "immutable_formal_bootstrap_conflict", "bootstrap artifact differs"
            )
        target.write_bytes(encoded)
        return bool(payload["completed"] and payload["passed"]), digest


class FormalAttributionExecutor:
    """Execute only preregistered formal variants through one exact backend."""

    def __init__(self, repository_root: Path, workspace_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.adapter = FormalStrategyAttributionAdapter(self.repository_root)
        self.backend = FormalP2ReplayBackend(self.repository_root, workspace_root)

    def binding(self) -> ExecutorBinding:
        return ExecutorBinding(
            executor_id=FORMAL_ARTIFACT_PRODUCER_ID,
            executor_version=FORMAL_ARTIFACT_PRODUCER_VERSION,
            producer_id=FORMAL_ARTIFACT_PRODUCER_ID,
            producer_version=FORMAL_ARTIFACT_PRODUCER_VERSION,
            source_kind=SOURCE_KIND,
            metric_calculator_id=METRIC_CALCULATOR_ID,
            metric_calculator_version=METRIC_CALCULATOR_VERSION,
            synthetic_fixture=False,
        )

    def execute(self, work: PlannedExecutionWork) -> RawExecutionResult:
        if work.adapter_manifest_digest != self.adapter.capability_manifest().digest:
            raise ExpertEvaluationError(
                "formal_adapter_mismatch", "execution work does not bind the formal adapter"
            )
        physical = work.experiment.kind in {
            PlanNodeKind.BASELINE_REPLAY,
            PlanNodeKind.SINGLE_INTERVENTION,
        }
        analysis_check: RawAnalysisCheck | None = None
        metrics: Mapping[str, Decimal] = {}
        trace: dict[str, Any]
        if physical:
            intervention_id = (
                None
                if work.experiment.kind is PlanNodeKind.BASELINE_REPLAY
                else work.experiment.intervention_ids[0]
            )
            evidence = self.backend.execute_variant(intervention_id)
            metrics = evidence.metrics
            trace = {
                "formal_replay_evidence_digest": evidence.digest,
                "formal_replay_digest": evidence.replay_digest,
                "formal_reconciliation_digest": evidence.reconciliation_digest,
                "formal_decision_trace_digest": evidence.decision_trace_digest,
                "formal_opportunity_trace_digest": evidence.opportunity_trace_digest,
                "formal_daily_return_artifact_digest": evidence.daily_return_artifact_digest,
                "sealed_partition_access": evidence.sealed_partition_access,
            }
        elif work.experiment.kind is PlanNodeKind.UNCERTAINTY_CHECK:
            intervention_id = work.experiment.intervention_ids[0]
            question_id = work.consumers[0].question_id
            passed, artifact_digest = self.backend.run_uncertainty(
                intervention_id, question_id, str(work.experiment.method_id)
            )
            analysis_check = RawAnalysisCheck(
                kind=AnalysisCheckKind.UNCERTAINTY,
                method=str(work.experiment.method_id),
                completed=True,
                passed=passed,
            )
            trace = {"formal_bootstrap_artifact_digest": artifact_digest}
        else:
            raise ExpertEvaluationError(
                "formal_pipeline_node_unsupported",
                f"formal executor does not support {work.experiment.kind.value}",
            )
        basis = raw_observation_basis(
            SOURCE_KIND,
            work.digest,
            work.experiment.digest,
            metrics,
            (),
            analysis_check,
        )
        observations = {
            label: digest({**basis, "observation": label})
            for label in (
                "replay",
                "reconciliation",
                "decision_trace",
                "opportunity_trace",
            )
        }
        return RawExecutionResult(
            work_id=work.work_id,
            work_digest=work.digest,
            executor_binding_digest=work.executor_binding_digest,
            planned_experiment_id=work.experiment.experiment_id,
            planned_experiment_digest=work.experiment.digest,
            node_kind=work.experiment.kind,
            parent_raw_result_digests=work.parent_raw_result_digests,
            actual_execution_scope_digest=work.experiment.execution_scope_digest,
            actual_component_ids=work.experiment.component_ids,
            actual_intervention_ids=work.experiment.intervention_ids,
            metric_spec_digests=work.experiment.metric_spec_digests,
            metrics=metrics,
            condition_slices=(),
            analysis_check=analysis_check,
            replay_reference_digest=observations["replay"],
            replay_observation_digest=observations["replay"],
            reconciliation_reference_digest=observations["reconciliation"],
            reconciliation_observation_digest=observations["reconciliation"],
            decision_trace_reference_digest=observations["decision_trace"],
            decision_trace_observation_digest=observations["decision_trace"],
            opportunity_trace_reference_digest=observations["opportunity_trace"],
            opportunity_trace_observation_digest=observations["opportunity_trace"],
            pit_context_digest=work.context_digest,
            authorization_scope_digest=work.experiment.execution_scope_digest,
            capital_constrained_observed=True,
            trace_payload=trace,
        )


register_formal_executor_implementation(FormalAttributionExecutor)
