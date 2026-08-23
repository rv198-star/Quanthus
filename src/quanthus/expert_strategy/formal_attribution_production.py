"""Worker operation that publishes the bounded formal attribution release."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.contracts import ExpertEvaluationSpec
from quanthus.expert_evaluation.production import _parquet, production_dwd_identity
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
from quanthus.expert_strategy.formal_attribution import FormalAttributionExecutor
from quanthus.expert_strategy.formal_attribution_semantics import (
    PREREGISTRATION_KEY,
    FormalStrategyAttributionAdapter,
)
from quanthus.expert_strategy.formal_p2_replay import (
    DECISION_END,
    DECISION_START,
    JANUARY_SENTINEL_DECISION_DIGEST,
    PARENT_EVENT_RUN_DIGEST,
)
from quanthus.expert_strategy.production import (
    FormalStrategySentinelProducer,
    formal_sentinel_job_input,
)
from quanthus.expert_strategy.registry import (
    POSITION_SPEC_REGISTRY_KEY,
    ExpertStrategyRegistry,
)
from quanthus.strategy_attribution_pipeline import AttributionPipeline, PipelineRunState
from quanthus.strategy_attribution_planning import PlanNodeKind, compile_attribution_plan

FORMAL_ATTRIBUTION_PRODUCER_ID = "producer.formal-p4-trend-risk-attribution"
FORMAL_ATTRIBUTION_PRODUCER_VERSION = "1.0.0"
FORMAL_RELEASE_SCHEMA = "quanthus.formal-attribution-release.v1"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _verify_sentinel_reproduction(
    artifacts: tuple[ProducedArtifact, ...],
) -> dict[str, Any]:
    matches = tuple(item for item in artifacts if item.path == "scorecard.json")
    if len(matches) != 1 or matches[0].payload is None:
        raise ExpertEvaluationError(
            "baseline_reproduction_missing", "January Sentinel scorecard is missing"
        )
    try:
        scorecard = json.loads(matches[0].payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "baseline_reproduction_invalid", "January Sentinel scorecard is unreadable"
        ) from exc
    if (
        not isinstance(scorecard, dict)
        or scorecard.get("decision_set_digest") != JANUARY_SENTINEL_DECISION_DIGEST
        or scorecard.get("decision_count") != 267
        or scorecard.get("strategy_id") != "candidate.p4-trend-risk"
        or scorecard.get("strategy_version") != "1.0.1"
        or scorecard.get("window")
        != {"start": "2020-01-01", "end": "2020-01-31"}
        or scorecard.get("sealed_partition_access") is not False
    ):
        raise ExpertEvaluationError(
            "baseline_reproduction_mismatch", "January Sentinel reproduction differs"
        )
    return scorecard


def formal_attribution_producer_binding(
    registry: ExpertStrategyRegistry,
) -> ProducerBinding:
    adapter = FormalStrategyAttributionAdapter(registry.repository_root)
    request = adapter.run_request()
    plan = compile_attribution_plan(request)
    return ProducerBinding(
        FORMAL_ATTRIBUTION_PRODUCER_ID,
        FORMAL_ATTRIBUTION_PRODUCER_VERSION,
        digest_value(
            {
                "producer_id": FORMAL_ATTRIBUTION_PRODUCER_ID,
                "producer_version": FORMAL_ATTRIBUTION_PRODUCER_VERSION,
                "strategy_card_digest": registry.card.semantic_digest,
                "adapter_manifest_digest": adapter.capability_manifest().digest,
                "request_digest": request.digest,
                "plan_digest": plan.digest,
                "preregistration_key": PREREGISTRATION_KEY,
            }
        ),
    )


def formal_attribution_job_input(
    registry: ExpertStrategyRegistry,
) -> ExpertEvaluationJobInput:
    trend = registry.card.experts[0]
    adapter = FormalStrategyAttributionAdapter(registry.repository_root)
    plan = compile_attribution_plan(adapter.run_request())
    references = tuple(
        sorted(
            (
                RegistryArtifactRef(
                    str(trend["expert_id"]),
                    str(trend["expert_version"]),
                    str(trend["evaluation_spec_digest"]),
                    str(trend["evaluation_spec_repository_key"]).removeprefix(
                        "config/expert-evaluations/"
                    ),
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
        "bounded_intervention",
        references,
        registry.formal_attribution_ref(plan.digest),
        production_dwd_identity(),
        PartitionBinding(
            artifact_id=registry.position_spec.partition.artifact_id,
            version=registry.position_spec.partition.version,
            digest=registry.position_spec.partition.digest,
            decision_start=DECISION_START,
            decision_end=DECISION_END,
        ),
        tuple(sorted(registry.intervention_ids)),
        (PARENT_EVENT_RUN_DIGEST,),
        formal_attribution_producer_binding(registry),
    )


def verify_formal_attribution_release(root: Path) -> dict[str, Any]:
    resolved = root.resolve()
    try:
        manifest_bytes = (resolved / "release.json").read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "formal_release_unreadable", "formal attribution release is unreadable"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != FORMAL_RELEASE_SCHEMA
        or canonical_json_bytes(manifest) != manifest_bytes
    ):
        raise ExpertEvaluationError(
            "formal_release_invalid", "formal attribution release manifest is invalid"
        )
    declared = manifest.get("files")
    if not isinstance(declared, list):
        raise ExpertEvaluationError("formal_release_invalid", "release files are invalid")
    expected = set()
    for item in declared:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            raise ExpertEvaluationError("formal_release_invalid", "release file record differs")
        path = resolved / str(item["path"])
        if resolved not in path.resolve().parents or path.is_symlink():
            raise ExpertEvaluationError("path_escape", "formal release file escapes root")
        if (
            not path.is_file()
            or _sha256(path) != item["sha256"]
            or path.stat().st_size != item["size_bytes"]
        ):
            raise ExpertEvaluationError(
                "formal_release_checksum_mismatch", "formal release file differs"
            )
        expected.add(str(item["path"]))
    physical = {
        item.relative_to(resolved).as_posix() for item in resolved.rglob("*") if item.is_file()
    }
    if physical != expected.union({"release.json", "READY"}):
        raise ExpertEvaluationError(
            "formal_release_inventory_mismatch", "formal release inventory differs"
        )
    observed_digest = digest_value(
        {key: value for key, value in manifest.items() if key != "release_digest"}
    )
    if manifest.get("release_digest") != observed_digest:
        raise ExpertEvaluationError(
            "formal_release_digest_mismatch", "formal release digest differs"
        )
    if (resolved / "READY").read_text(encoding="ascii") != observed_digest + "\n":
        raise ExpertEvaluationError("formal_release_not_ready", "READY differs")
    return manifest


class FormalAttributionOperationProducer:
    def __init__(self, registry: ExpertStrategyRegistry) -> None:
        self.registry = registry
        self.repository_root = registry.repository_root

    def produce(
        self,
        job_input: ExpertEvaluationJobInput,
        specs: tuple[ExpertEvaluationSpec, ...],
        progress: Callable[[str, Mapping[str, Any]], None],
        canceled: Callable[[], bool],
    ) -> tuple[ProducedArtifact, ...]:
        expected = formal_attribution_job_input(self.registry)
        if job_input != expected:
            raise ExpertEvaluationError(
                "formal_attribution_job_mismatch", "formal attribution Job input differs"
            )
        if (
            tuple(
                sorted((item.assessment_id, item.assessment_version, item.digest) for item in specs)
            )
            != self.registry.evaluation_spec_identities
        ):
            raise ExpertEvaluationError(
                "formal_strategy_spec_mismatch", "formal attribution spec closure differs"
            )
        if canceled():
            raise ExpertEvaluationError("canceled", "formal attribution was canceled")
        release_root = Path(
            os.environ.get(
                "QUANTHUS_ATTRIBUTION_RELEASE_ROOT",
                "/data/expert-evaluation-runs/attribution-releases",
            )
        ).resolve()
        release_root.mkdir(parents=True, exist_ok=True)
        event_root = Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))
        sentinel_artifacts = FormalStrategySentinelProducer(
            self.registry, event_root
        ).produce(
            formal_sentinel_job_input(PARENT_EVENT_RUN_DIGEST, self.registry),
            specs,
            lambda *_args: None,
            canceled,
        )
        sentinel_scorecard = _verify_sentinel_reproduction(sentinel_artifacts)
        with tempfile.TemporaryDirectory(
            prefix="formal-attribution-", dir=release_root
        ) as temporary:
            work = Path(temporary)
            adapter = FormalStrategyAttributionAdapter(self.repository_root)
            request = adapter.run_request()
            progress(
                "producing-observations",
                {"physical_replays": 4, "uncertainty_checks": 3},
            )
            result = AttributionPipeline(work / "pipeline").run(
                request,
                FormalAttributionExecutor(self.repository_root, work / "replays"),
            )
            if result.state is not PipelineRunState.EVALUATED or len(result.claims) != 3:
                raise ExpertEvaluationError(
                    "formal_attribution_pipeline_incomplete",
                    "formal attribution Pipeline did not evaluate all questions",
                )
            progress("settling-outcomes", {"state": "not_requested"})
            physical = tuple(
                item
                for item in result.plan.experiments
                if item.kind in {PlanNodeKind.BASELINE_REPLAY, PlanNodeKind.SINGLE_INTERVENTION}
            )
            evidence = {}
            for variant in (
                "variant.native-baseline",
                "variant.trend-fallback",
                "variant.position-risk-fallback",
                "variant.joint-fallback",
            ):
                evidence[variant] = json.loads(
                    (work / "replays" / "variants" / variant / "evidence.json").read_bytes()
                )
            interaction = {}
            for metric_id in (
                "capital_constrained_net_total_return_delta",
                "maximum_drawdown_delta",
            ):
                values = {
                    key: Decimal(item["metrics"][metric_id]) for key, item in evidence.items()
                }
                interaction[metric_id] = str(
                    values["variant.native-baseline"]
                    - values["variant.trend-fallback"]
                    - values["variant.position-risk-fallback"]
                    + values["variant.joint-fallback"]
                )
            claims = [item.to_dict() for item in result.claims]
            checks = [item.to_dict() for item in result.analysis_checks]
            scorecard = {
                "schema_version": "quanthus.formal-attribution-scorecard.v1",
                "strategy_id": self.registry.card.strategy_id,
                "strategy_version": self.registry.card.strategy_version,
                "strategy_card_digest": self.registry.card.semantic_digest,
                "topology_manifest_digest": self.registry.topology_manifest.digest,
                "adapter_manifest_digest": adapter.capability_manifest().digest,
                "preregistration_digest": adapter.preregistration["semantic_digest"],
                "request_digest": request.digest,
                "plan_digest": result.plan.digest,
                "pipeline_run_id": result.run_id,
                "pipeline_evaluation_digest": result.evaluation_bundle.digest,
                "physical_replay_count": len(physical),
                "claims": claims,
                "analysis_checks": checks,
                "variant_evidence": evidence,
                "interaction": {
                    "formula": adapter.preregistration["interaction"]["formula"],
                    "effects": interaction,
                    "allocated_to_experts": False,
                },
                "limitations": [
                    "Development 2020-2021 only; no forward-performance claim.",
                    "Market dates, securities, windows, and capital units are dependent.",
                    "Interaction is reported beside and never allocated to either expert.",
                    "Arbitration 2022-2024 and permanent-blind 2025 remain sealed.",
                ],
                "sealed_partition_access": False,
                "execution_authorized": False,
                "sentinel_reproduction": {
                    "decision_count": sentinel_scorecard["decision_count"],
                    "decision_set_digest": sentinel_scorecard["decision_set_digest"],
                    "parent_event_run_digest": PARENT_EVENT_RUN_DIGEST,
                    "window": sentinel_scorecard["window"],
                },
            }
            progress("building-scorecard", {"claim_count": len(claims)})
            files = [
                item
                for item in work.rglob("*")
                if item.is_file() and item.name not in {"release.json", "READY"}
            ]
            combined = sum(item.stat().st_size for item in files)
            if combined > 1024**3:
                raise ExpertEvaluationError(
                    "resource_boundary_exceeded", "formal release exceeds 1 GiB"
                )
            manifest = {
                "schema_version": FORMAL_RELEASE_SCHEMA,
                "release_id": f"formal-attribution-{result.evaluation_bundle.digest[7:31]}",
                "strategy_id": self.registry.card.strategy_id,
                "strategy_version": self.registry.card.strategy_version,
                "strategy_card_digest": self.registry.card.semantic_digest,
                "preregistration_digest": adapter.preregistration["semantic_digest"],
                "plan_digest": result.plan.digest,
                "evaluation_bundle_digest": result.evaluation_bundle.digest,
                "file_count": len(files),
                "combined_size_bytes": combined,
                "sealed_partition_access": False,
                "files": [
                    {
                        "path": item.relative_to(work).as_posix(),
                        "sha256": _sha256(item),
                        "size_bytes": item.stat().st_size,
                    }
                    for item in sorted(files)
                ],
            }
            manifest["release_digest"] = digest_value(manifest)
            (work / "release.json").write_bytes(canonical_json_bytes(manifest))
            (work / "READY").write_text(str(manifest["release_digest"]) + "\n", encoding="ascii")
            verify_formal_attribution_release(work)
            final = release_root / str(manifest["release_id"])
            if final.exists():
                existing = verify_formal_attribution_release(final)
                if existing["release_digest"] != manifest["release_digest"]:
                    raise ExpertEvaluationError(
                        "immutable_formal_release_conflict", "formal release id differs"
                    )
            else:
                os.replace(work, final)
            verified = verify_formal_attribution_release(final)
            scorecard["release_id"] = verified["release_id"]
            scorecard["release_digest"] = verified["release_digest"]
            rows = tuple(
                {
                    "variant_id": key,
                    "intervention_id": item["intervention_id"],
                    "evidence_digest": digest_value(item),
                    "metrics_json": json.dumps(item["metrics"], sort_keys=True),
                }
                for key, item in sorted(evidence.items())
            )
            ledger = {
                "schema_version": "quanthus.formal-attribution-sample-ledger.v1",
                "physical_replays": 4,
                "uncertainty_checks": 3,
                "claims": len(claims),
                "sealed_partition_access": False,
            }
            progress("running-interventions", {"completed": 3})
            intervention_artifacts = tuple(
                ProducedArtifact(
                    f"interventions/{intervention_id}.json",
                    canonical_json_bytes(
                        next(
                            item
                            for item in evidence.values()
                            if item["intervention_id"] == intervention_id
                        )
                    ),
                    1,
                )
                for intervention_id in job_input.requested_interventions
            )
            return (
                ProducedArtifact("observations.parquet", _parquet(rows), len(rows)),
                ProducedArtifact("settlements.parquet", None, 0, "not_requested"),
                ProducedArtifact("sample-ledger.json", canonical_json_bytes(ledger), 1),
                ProducedArtifact("scorecard.json", canonical_json_bytes(scorecard), 1),
                *intervention_artifacts,
            )


def formal_attribution_producer_for(
    job_input: ExpertEvaluationJobInput, registry_root: Path
) -> tuple[FormalAttributionOperationProducer, ExpertStrategyRegistry]:
    repository_root = registry_root.resolve().parents[1]
    registry = ExpertStrategyRegistry(repository_root, registry_root)
    if job_input.producer.producer_id != FORMAL_ATTRIBUTION_PRODUCER_ID:
        raise ExpertEvaluationError(
            "producer_not_admitted", "formal attribution producer is not admitted"
        )
    return FormalAttributionOperationProducer(registry), registry
