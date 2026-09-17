"""The sole composition root for admitted Platform Kernel capabilities."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from quanthus.data_catalog import DataIndicatorCatalog
from quanthus.expert_evaluation.product_view import ProductExpertEvidence
from quanthus.forward_validation.product_view import ForwardValidationProductView
from quanthus.platform.adapters.quanthus import (
    QuanthusAttributionVerificationAdapter,
    QuanthusGateQueryAdapter,
)
from quanthus.platform.api import PlatformHttpApp, create_http_app
from quanthus.platform.candidate_approval import CandidateApprovalService
from quanthus.platform.identity import FernetSecretCipher, IdentityService, SecretCipher
from quanthus.platform.jobs import (
    ATTRIBUTION_VERIFICATION_JOB,
    CANDIDATE_REPLAY_JOB,
    EXPERT_EVALUATION_JOB,
    P6_FORWARD_CYCLE_JOB,
    DurableJobQueue,
    LocalJobRunner,
    attribution_verification_handler,
)
from quanthus.platform.manifest import (
    FeatureManifest,
    load_feature_manifest,
    validate_manifest_against_capability_gate,
)
from quanthus.platform.observability import Metrics
from quanthus.platform.persistence import PlatformStore, PostgresPlatformStore
from quanthus.platform.ports import AttributionVerificationPort, GateQueryPort
from quanthus.platform.product_query import ProductCandidateEvidence, ProductResearchCatalog
from quanthus.policy import find_project_root


@dataclass(frozen=True)
class PlatformKernel:
    manifest: FeatureManifest
    store: PlatformStore
    identity: IdentityService
    jobs: LocalJobRunner
    replay_jobs: DurableJobQueue
    gate_query: GateQueryPort
    attribution_verification: AttributionVerificationPort
    product_catalog: ProductResearchCatalog
    candidate_evidence: ProductCandidateEvidence
    expert_evidence: ProductExpertEvidence
    forward_validation: ForwardValidationProductView
    data_catalog: DataIndicatorCatalog
    candidate_approvals: CandidateApprovalService
    metrics: Metrics
    app: PlatformHttpApp

    def migrate(self) -> tuple[str, ...]:
        return self.store.migrate(self.manifest.migrations)

    def start(self) -> tuple[str, ...]:
        completed: list[str] = []
        for hook in self.manifest.startup_hooks:
            if hook == "jobs.resume-pending":
                self.jobs.resume_pending()
            else:
                raise RuntimeError(f"startup hook has no implementation: {hook}")
            completed.append(hook)
        return tuple(completed)


def create_platform_kernel(
    *,
    store: PlatformStore,
    cipher_key: str | None = None,
    cipher: SecretCipher | None = None,
    manifest_path: Path | None = None,
    gate_path: Path | None = None,
    attribution_root: Path | None = None,
    product_catalog_path: Path | None = None,
    candidate_evidence_path: Path | None = None,
    candidate_run_root: Path | None = None,
    expert_evidence_path: Path | None = None,
    expert_run_root: Path | None = None,
    data_catalog_sources_path: Path | None = None,
    data_catalog_snapshot_path: Path | None = None,
) -> PlatformKernel:
    root = find_project_root()
    manifest = load_feature_manifest(manifest_path)
    validate_manifest_against_capability_gate(manifest, gate_path)
    if set(manifest.process_roles) != {
        "api",
        "migration",
        "web",
        "replay-worker",
        "event-worker",
    }:
        raise RuntimeError(
            "the Platform Kernel admits api, migration, web, replay-worker, and event-worker roles"
        )
    if manifest.dependency_groups != ("platform",):
        raise RuntimeError("the first Platform Kernel must use only the platform dependency group")
    gate_query = QuanthusGateQueryAdapter(
        gate_path or root / "config" / "platform-kernel-gates.v1.json"
    )
    attribution_verification = QuanthusAttributionVerificationAdapter(
        attribution_root or root / "artifacts" / "attribution-releases"
    )
    product_catalog = ProductResearchCatalog(
        product_catalog_path or root / "config" / "product-research-catalog.v1.json"
    )
    candidate_evidence = ProductCandidateEvidence(
        candidate_evidence_path or root / "config" / "product-candidate-evidence.v1.json",
        product_catalog,
        store=store,
        run_root=candidate_run_root,
    )
    candidate_evidence.warm_active()
    expert_evidence = ProductExpertEvidence(
        expert_evidence_path or root / "config" / "product-expert-evidence.v1.json",
        root,
        expert_run_root,
    )
    forward_validation = ForwardValidationProductView(root, store)
    data_catalog = DataIndicatorCatalog(
        data_catalog_sources_path,
        data_catalog_snapshot_path,
    )
    candidate_approvals = CandidateApprovalService(store)
    if (cipher_key is None) == (cipher is None):
        raise ValueError("provide exactly one of cipher_key or cipher")
    identity = IdentityService(
        store,
        cipher if cipher is not None else FernetSecretCipher(str(cipher_key)),
    )
    handlers = {}
    if manifest.has("finite-jobs") and manifest.has("quanthus-attribution-verification-v1"):
        handlers[ATTRIBUTION_VERIFICATION_JOB] = attribution_verification_handler(
            attribution_verification
        )
    jobs = LocalJobRunner(store, handlers)
    replay_jobs = DurableJobQueue(
        store,
        {
            CANDIDATE_REPLAY_JOB: "replay",
            EXPERT_EVALUATION_JOB: "replay",
            P6_FORWARD_CYCLE_JOB: "replay",
        },
    )
    metrics = Metrics()
    app = create_http_app(
        manifest=manifest,
        store=store,
        identity=identity,
        jobs=jobs,
        replay_jobs=replay_jobs,
        gate_query=gate_query,
        product_catalog=product_catalog,
        candidate_evidence=candidate_evidence,
        expert_evidence=expert_evidence,
        forward_validation=forward_validation,
        data_catalog=data_catalog,
        candidate_approvals=candidate_approvals,
        metrics=metrics,
    )
    return PlatformKernel(
        manifest=manifest,
        store=store,
        identity=identity,
        jobs=jobs,
        replay_jobs=replay_jobs,
        gate_query=gate_query,
        attribution_verification=attribution_verification,
        product_catalog=product_catalog,
        candidate_evidence=candidate_evidence,
        expert_evidence=expert_evidence,
        forward_validation=forward_validation,
        data_catalog=data_catalog,
        candidate_approvals=candidate_approvals,
        metrics=metrics,
        app=app,
    )


def platform_kernel_from_env() -> PlatformKernel:
    dsn = os.environ.get("QUANTHUS_PLATFORM_DATABASE_URL", "").strip()
    cipher_key = os.environ.get("QUANTHUS_PLATFORM_MFA_KEY", "").strip()
    if not dsn:
        raise RuntimeError("QUANTHUS_PLATFORM_DATABASE_URL is required")
    if not cipher_key:
        raise RuntimeError("QUANTHUS_PLATFORM_MFA_KEY is required")
    manifest_value = os.environ.get("QUANTHUS_PLATFORM_FEATURE_MANIFEST", "").strip()
    gate_value = os.environ.get("QUANTHUS_PLATFORM_GATE_PATH", "").strip()
    attribution_value = os.environ.get("QUANTHUS_ATTRIBUTION_PUBLICATION_ROOT", "").strip()
    candidate_run_value = os.environ.get("QUANTHUS_CANDIDATE_RUN_ROOT", "").strip()
    expert_run_value = os.environ.get("QUANTHUS_EXPERT_RUN_ROOT", "").strip()
    return create_platform_kernel(
        store=PostgresPlatformStore(dsn),
        cipher_key=cipher_key,
        manifest_path=Path(manifest_value) if manifest_value else None,
        gate_path=Path(gate_value) if gate_value else None,
        attribution_root=Path(attribution_value) if attribution_value else None,
        candidate_run_root=Path(candidate_run_value) if candidate_run_value else None,
        expert_run_root=Path(expert_run_value) if expert_run_value else None,
    )
