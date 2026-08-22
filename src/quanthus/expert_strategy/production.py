"""Development-only Worker producer for the approved January 2020 Sentinel."""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeaturePanel
from quanthus.backtest.data_provider import ReplayDataProvider, load_replay_data_provider_config
from quanthus.expert_evaluation.contracts import ExpertEvaluationSpec
from quanthus.expert_evaluation.experts.trend_stability import (
    evaluate_trend_stability,
    load_trend_stability_config,
)
from quanthus.expert_evaluation.production import (
    _find_parent_package,
    _load_events,
    _parquet,
    production_dwd_identity,
)
from quanthus.expert_evaluation.runner import (
    ExpertEvaluationJobInput,
    PartitionBinding,
    ProducedArtifact,
    ProducerBinding,
    RegistryArtifactRef,
)
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy.attribution_adapter import FormalAttributionAdapter
from quanthus.expert_strategy.registry import (
    POSITION_SPEC_REGISTRY_KEY,
    ExpertStrategyRegistry,
)
from quanthus.expert_strategy.runner import FormalStrategyRunner, build_formal_expert_context
from quanthus.feature_registry import load_feature_registry

FORMAL_PRODUCER_ID = "producer.formal-p4-trend-risk-development"
FORMAL_PRODUCER_VERSION = "1.0.3"
FORMAL_PROJECTION_POLICY = "event-security-materialized-source-projection-v3"
SENTINEL_START = date(2020, 1, 1)
SENTINEL_END = date(2020, 1, 31)


def formal_producer_binding(registry: ExpertStrategyRegistry) -> ProducerBinding:
    return ProducerBinding(
        FORMAL_PRODUCER_ID,
        FORMAL_PRODUCER_VERSION,
        digest_value(
            {
                "producer_id": FORMAL_PRODUCER_ID,
                "producer_version": FORMAL_PRODUCER_VERSION,
                "strategy_card_digest": registry.card.semantic_digest,
                "topology_manifest_digest": registry.topology_manifest.digest,
                "sentinel_plan_digest": registry.sentinel_plan_digest,
                "evaluation_specs": registry.evaluation_spec_identities,
                "projection_policy": FORMAL_PROJECTION_POLICY,
            }
        ),
    )


def formal_sentinel_job_input(
    parent_run_digest: str, registry: ExpertStrategyRegistry
) -> ExpertEvaluationJobInput:
    trend = registry.card.experts[0]
    references = tuple(
        sorted(
            (
                RegistryArtifactRef(
                    str(trend["expert_id"]),
                    str(trend["expert_version"]),
                    str(trend["evaluation_spec_digest"]),
                    "specs/trend-stability-p4-first-passage.evaluation.v1.json",
                ),
                RegistryArtifactRef(
                    registry.position_spec.assessment_id,
                    registry.position_spec.assessment_version,
                    registry.position_spec.digest,
                    POSITION_SPEC_REGISTRY_KEY,
                ),
            ),
            key=lambda item: item.identity,
        )
    )
    return ExpertEvaluationJobInput(
        operation="development_evaluation",
        evaluation_specs=references,
        formal_strategy=registry.formal_strategy_ref(),
        dwd=production_dwd_identity(),
        partition=PartitionBinding(
            "partition.development-2020-2021",
            "1.0.0",
            "sha256:accba0894d9892df25c3465f2e572f25d859631bac4a14fa0bcaa236db621bbc",
            SENTINEL_START,
            SENTINEL_END,
        ),
        requested_interventions=(),
        expected_parent_run_digests=(parent_run_digest,),
        producer=formal_producer_binding(registry),
    )


class FormalStrategySentinelProducer:
    def __init__(
        self,
        registry: ExpertStrategyRegistry,
        event_run_root: Path,
    ) -> None:
        self.registry = registry
        self.event_run_root = event_run_root.resolve()
        self.repository_root = registry.repository_root

    def produce(
        self,
        job_input: ExpertEvaluationJobInput,
        specs: tuple[ExpertEvaluationSpec, ...],
        progress: Callable[[str, Mapping[str, Any]], None],
        canceled: Callable[[], bool],
    ) -> tuple[ProducedArtifact, ...]:
        if job_input.producer != formal_producer_binding(self.registry):
            raise ExpertEvaluationError("producer_binding_mismatch", "formal producer differs")
        if job_input.formal_strategy != self.registry.formal_strategy_ref():
            raise ExpertEvaluationError("formal_strategy_identity_mismatch", "strategy differs")
        if job_input.dwd != production_dwd_identity():
            raise ExpertEvaluationError("dwd_binding_mismatch", "effective DWD v2.1 differs")
        if (
            job_input.operation != "development_evaluation"
            or job_input.partition.decision_start != SENTINEL_START
            or job_input.partition.decision_end != SENTINEL_END
            or job_input.requested_interventions
        ):
            raise ExpertEvaluationError(
                "sentinel_scope_mismatch", "#149 producer is fixed to the January sentinel"
            )
        observed_specs = tuple(
            sorted(
                (spec.assessment_id, spec.assessment_version, spec.digest) for spec in specs
            )
        )
        if observed_specs != self.registry.evaluation_spec_identities:
            raise ExpertEvaluationError("formal_strategy_spec_mismatch", "spec closure differs")
        trend_spec = next(spec for spec in specs if spec.assessment_id == "expert.trend-stability")
        event_root, _label_root = _find_parent_package(
            self.event_run_root, job_input.expected_parent_run_digests
        )
        events = _load_events(event_root, SENTINEL_START, SENTINEL_END)
        selected_security_ids = tuple(
            sorted({item.panel_security_id for item in events})
        )
        if not selected_security_ids:
            raise ExpertEvaluationError(
                "sentinel_population_empty", "January Sentinel contains no event securities"
            )
        provider_config = load_replay_data_provider_config(
            self.repository_root / "config/replay-data-provider.development.v2.json"
        )
        feature_registry = load_feature_registry(
            self.repository_root / "config/p3-baseline-features.research.v1.json"
        )
        trend_config = load_trend_stability_config(
            self.repository_root / "config/expert-evaluations/experts/trend-stability.v1.json"
        )
        topology = self.registry.topology_manifest
        runner = FormalStrategyRunner(self.registry.card, topology.digest)
        observation_rows: list[dict[str, str | None]] = []
        action_counts: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        decision_digests: list[str] = []
        applicable = 0
        abstained = 0
        provider = ReplayDataProvider(provider_config)
        with tempfile.TemporaryDirectory(prefix="quanthus-formal-sentinel-") as temporary:
            panel_path = Path(temporary) / "feature-panel.parquet"
            progress(
                "building-shared-projection",
                {
                    "event_count": len(events),
                    "selected_security_count": len(selected_security_ids),
                    "projection_policy": FORMAL_PROJECTION_POLICY,
                },
            )
            with provider.open(scope_start=SENTINEL_START, scope_end=SENTINEL_END) as session, (
                BaselineFeaturePanel.build(
                    session.reader.bundle,
                    feature_registry,
                    scope_start=SENTINEL_START,
                    scope_end=SENTINEL_END,
                    output=panel_path,
                    data_content_digest=session.reader.data_content_digest,
                    data_manifest_sha256=session.reader.data_manifest_sha256,
                    allowed_security_ids=selected_security_ids,
                )
            ) as panel:
                progress("producing-observations", {"event_count": len(events)})
                for index, item in enumerate(events, start=1):
                    if canceled():
                        raise ExpertEvaluationError("canceled", "formal sentinel was canceled")
                    feature = panel.feature(
                        item.record.identity.signal_date,
                        item.panel_security_id,
                    )
                    if feature is not None:
                        feature = replace(
                            feature,
                            security_id=item.record.identity.security_id,
                        )
                    observation = evaluate_trend_stability(
                        event=item.record,
                        feature=feature,
                        config=trend_config,
                        spec=trend_spec,
                    )
                    context = build_formal_expert_context(
                        event=item.record,
                        feature=feature,
                        feature_registry=feature_registry,
                        panel_report=panel.report,
                        position_state="empty",
                        trend_observation_digest=observation.digest,
                    )
                    decision = runner.evaluate_entry(
                        event_id=item.record.event_id,
                        context=context,
                        trend_state=(
                            None if observation.raw_value is None else str(observation.raw_value)
                        ),
                        entry_evidence_ready=feature is not None and feature.entry_eligible,
                    )
                    applicable += int(observation.applicability.value == "applicable")
                    abstained += int(observation.applicability.value == "abstained")
                    action_counts[decision.action.final_action.value] += 1
                    reason_counts.update(decision.action.reason_codes)
                    decision_digests.append(decision.digest)
                    observation_rows.append(
                        {
                            "subject_id": item.record.event_id,
                            "observation_date": item.record.identity.signal_date.isoformat(),
                            "observation_json": observation.canonical_bytes().decode("utf-8"),
                            "decision_json": decision.canonical_bytes.decode("utf-8"),
                            "decision_digest": decision.digest,
                        }
                    )
                    if index % 1000 == 0:
                        progress(
                            "producing-observations", {"completed": index, "total": len(events)}
                        )
        ledger = {
            "schema_version": "quanthus.formal-strategy-sentinel-ledger.v1",
            "eligible": len(events),
            "applicable": applicable,
            "abstained": abstained,
            "decision_count": len(observation_rows),
        }
        progress("settling-outcomes", {"state": "not_requested"})
        scorecard = {
            "schema_version": "quanthus.formal-strategy-sentinel-scorecard.v1",
            "strategy_id": self.registry.card.strategy_id,
            "strategy_version": self.registry.card.strategy_version,
            "strategy_card_digest": self.registry.card.semantic_digest,
            "topology_manifest": topology.to_dict(),
            "topology_manifest_digest": topology.digest,
            "attribution_adapter_digest": FormalAttributionAdapter(self.registry).digest,
            "sentinel_plan_digest": self.registry.sentinel_plan_digest,
            "window": {"start": SENTINEL_START.isoformat(), "end": SENTINEL_END.isoformat()},
            "decision_count": len(observation_rows),
            "action_counts": dict(sorted(action_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
            "decision_set_digest": digest_value(sorted(decision_digests)),
            "economic_evidence": False,
            "sealed_partition_access": False,
        }
        progress("building-scorecard", {"decision_count": len(observation_rows)})
        progress("running-interventions", {"state": "not_requested"})
        return (
            ProducedArtifact(
                "observations.parquet", _parquet(observation_rows), len(observation_rows)
            ),
            ProducedArtifact("settlements.parquet", None, 0, "not_requested"),
            ProducedArtifact("sample-ledger.json", canonical_json_bytes(ledger), 1),
            ProducedArtifact("scorecard.json", canonical_json_bytes(scorecard), 1),
        )


def formal_producer_for(
    job_input: ExpertEvaluationJobInput, registry_root: Path
) -> tuple[FormalStrategySentinelProducer, ExpertStrategyRegistry]:
    repository_root = registry_root.resolve().parents[1]
    registry = ExpertStrategyRegistry(repository_root, registry_root)
    if job_input.producer.producer_id != FORMAL_PRODUCER_ID:
        raise ExpertEvaluationError("producer_not_admitted", "formal producer is not admitted")
    event_root = Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))
    return FormalStrategySentinelProducer(registry, event_root), registry
