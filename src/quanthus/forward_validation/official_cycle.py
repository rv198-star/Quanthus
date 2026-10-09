# ruff: noqa: E501
"""P6-ACTIVATE-001 official prospective cycle orchestration.

The module reuses accepted candidate, Trend/Position-Risk, aggregator, and P2 paper semantics while
keeping prospective population/authority identities separate from the historical P4 development
assessment.  It never grants notification, broker, or live-capital authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

from quanthus.aggregator import GovernedAction
from quanthus.backtest.contracts import MARKET_TIMEZONE, EventType, OrderSide
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.backtest.inputs import (
    CoverageStatus,
    buy_open_rejection_reason,
    market_session_from_p1,
)
from quanthus.event_library.contracts import EventIdentity, EventRecord, event_record_from_dict
from quanthus.expert_evaluation import ExpertEvaluationSpec, artifact_from_bytes
from quanthus.expert_evaluation.experts.trend_stability import (
    classify_trend_stability,
    load_trend_stability_config,
)
from quanthus.expert_protocols import RequiredAction
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import (
    FormalStrategyRunner,
    build_formal_expert_context,
    position_facts_evidence,
)
from quanthus.feature_registry import load_feature_registry
from quanthus.forward_validation.activation import (
    P6ActivationDecision,
    P6ProspectiveRuntimeConfig,
    activation_decision_from_mapping,
    runtime_code_revision,
)
from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.data_boundary import (
    P6ActivationBoundary,
    ProspectiveDwdRelease,
    classify_release_admission,
    load_visibility_receipt,
    make_access_ledger_entry,
    verify_prospective_dwd_release,
)
from quanthus.forward_validation.package_locator import resolve_daily_package
from quanthus.forward_validation.paper_ledger import (
    PaperLedgerState,
    initial_paper_ledger,
    load_paper_policy,
    paper_ledger_from_dict,
    reconcile_paper_ledger,
    replace_paper_unit,
)
from quanthus.forward_validation.paper_projection import (
    PaperPendingIntent,
    SealedAggregateDecision,
    paper_pending_intent_from_dict,
    project_paper_intent,
    settle_paper_intent,
)
from quanthus.forward_validation.prospective_source import (
    P6ProspectiveProjection,
    build_prospective_projection,
    scan_projection_candidates,
)
from quanthus.forward_validation.scorecards import build_role_scorecard, load_scorecard_policy
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.platform.jobs import input_digest

_INPUT_SCHEMA = "quanthus.p6-forward-cycle-input.v1"
_OFFICIAL_ARTIFACT_SCHEMA = "quanthus.p6-official-cycle.v1"
_RUN_SCHEMA = "quanthus.p6-forward-run.v1"
_JOB_KIND = "quanthus.p6-forward-cycle.v1"
_ENTRY_DIGEST = "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        fail("invalid_digest", field)
    return value


@dataclass(frozen=True)
class OfficialCycleJobInput:
    cycle_id: str
    runtime_sha256: str
    activation_decision: Mapping[str, object]
    market_date: date
    package_id: str
    manifest_sha256: str
    content_digest: str
    visibility_receipt: Mapping[str, object]
    consumed_at: datetime
    prior_run_id: str | None = None
    prior_run_manifest_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.cycle_id or self.cycle_id.strip() != self.cycle_id:
            fail("field_mismatch", "cycle_id must be canonical text")
        _digest(self.runtime_sha256, "runtime_sha256")
        decision_digest = self.activation_decision.get("decision_digest")
        _digest(decision_digest, "activation_decision.decision_digest")
        if not self.package_id or self.package_id.strip() != self.package_id:
            fail("field_mismatch", "package_id must be canonical text")
        _digest(self.manifest_sha256, "manifest_sha256")
        _digest(self.content_digest, "content_digest")
        if (self.prior_run_id is None) != (self.prior_run_manifest_digest is None):
            fail("field_mismatch", "prior run id/digest must be supplied together")
        if self.prior_run_id is not None:
            if not self.prior_run_id.startswith("p6_run_"):
                fail("field_mismatch", "prior_run_id is invalid")
            _digest(self.prior_run_manifest_digest, "prior_run_manifest_digest")
        if self.consumed_at.tzinfo is None or self.consumed_at.utcoffset() is None:
            fail("invalid_instant", "consumed_at")
        receipt = load_visibility_receipt(self.visibility_receipt)
        if (
            receipt.package_id != self.package_id
            or receipt.market_date != self.market_date
            or receipt.manifest_sha256 != self.manifest_sha256
            or receipt.content_digest != self.content_digest
        ):
            fail("visibility_binding_mismatch", "official input visibility differs from release")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _INPUT_SCHEMA,
            "mode": "official_cycle",
            "cycle_id": self.cycle_id,
            "entry_proposal_digest": _ENTRY_DIGEST,
            "runtime_sha256": self.runtime_sha256,
            "activation_decision": dict(self.activation_decision),
            "market_date": self.market_date.isoformat(),
            "package_id": self.package_id,
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
            "visibility_receipt": dict(self.visibility_receipt),
            "consumed_at": self.consumed_at.astimezone(timezone.utc).isoformat(),
            "prior_run_id": self.prior_run_id,
            "prior_run_manifest_digest": self.prior_run_manifest_digest,
        }

    @property
    def input_digest(self) -> str:
        return input_digest(self.to_dict())

    @property
    def idempotency_key(self) -> str:
        return f"p6-official:{self.input_digest[7:39]}"

    @property
    def concurrency_key(self) -> str:
        return "p6-forward-cycle:official"

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> OfficialCycleJobInput:
        expected = {
            "schema_version",
            "mode",
            "cycle_id",
            "entry_proposal_digest",
            "runtime_sha256",
            "activation_decision",
            "market_date",
            "package_id",
            "manifest_sha256",
            "content_digest",
            "visibility_receipt",
            "consumed_at",
            "prior_run_id",
            "prior_run_manifest_digest",
        }
        if set(value) != expected or value.get("schema_version") != _INPUT_SCHEMA or value.get("mode") != "official_cycle":
            fail("field_mismatch", "official forward input fields differ")
        if value.get("entry_proposal_digest") != _ENTRY_DIGEST:
            fail("binding_digest_drift", "official input binds another entry proposal")
        receipt = value.get("visibility_receipt")
        activation_decision = value.get("activation_decision")
        if not isinstance(receipt, Mapping):
            fail("field_mismatch", "visibility_receipt must be object")
        if not isinstance(activation_decision, Mapping):
            fail("field_mismatch", "activation_decision must be object")
        try:
            market_date = date.fromisoformat(str(value.get("market_date")))
        except ValueError as exc:
            raise ForwardValidationContractError("invalid_date", "market_date") from exc
        try:
            consumed_at = datetime.fromisoformat(str(value.get("consumed_at")))
        except ValueError as exc:
            raise ForwardValidationContractError("invalid_instant", "consumed_at") from exc
        return cls(
            cycle_id=str(value.get("cycle_id")),
            runtime_sha256=str(value.get("runtime_sha256")),
            activation_decision=MappingProxyType(dict(activation_decision)),
            market_date=market_date,
            package_id=str(value.get("package_id")),
            manifest_sha256=str(value.get("manifest_sha256")),
            content_digest=str(value.get("content_digest")),
            visibility_receipt=MappingProxyType(dict(receipt)),
            consumed_at=consumed_at,
            prior_run_id=(None if value.get("prior_run_id") is None else str(value.get("prior_run_id"))),
            prior_run_manifest_digest=(
                None
                if value.get("prior_run_manifest_digest") is None
                else str(value.get("prior_run_manifest_digest"))
            ),
        )


@dataclass(frozen=True)
class ProspectiveTrendObservation:
    event_id: str
    market_date: date
    expert_id: str
    expert_version: str
    reference_evaluation_spec_digest: str
    reference_assessment_binding_digest: str
    expert_semantic_digest: str
    applicability: str
    applicability_reason: str
    raw_value: str | None
    evidence_digest: str
    knowledge_cutoff: datetime

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "quanthus.p6-prospective-trend-observation.v1",
            "population": "p6_prospective_daily_candidates",
            "partition": "prospective",
            "event_id": self.event_id,
            "market_date": self.market_date.isoformat(),
            "expert_id": self.expert_id,
            "expert_version": self.expert_version,
            "reference_evaluation_spec_digest": self.reference_evaluation_spec_digest,
            "reference_assessment_binding_digest": self.reference_assessment_binding_digest,
            "expert_semantic_digest": self.expert_semantic_digest,
            "applicability": self.applicability,
            "applicability_reason": self.applicability_reason,
            "raw_value": self.raw_value,
            "evidence_digest": self.evidence_digest,
            "knowledge_cutoff": self.knowledge_cutoff.astimezone(timezone.utc).isoformat(),
            "historical_development_observation_claim": False,
        }


@dataclass(frozen=True)
class OfficialCycleResult:
    run_id: str
    root: Path
    input_digest: str
    manifest_digest: str
    summary: Mapping[str, Any]


@dataclass(frozen=True)
class P6ForwardState:
    market_date: date | None
    release_package_id: str | None
    release_manifest_sha256: str | None
    collection_session_count: int
    tail_session_count: int
    ledger: PaperLedgerState
    pending_intents: tuple[PaperPendingIntent, ...]
    pending_contexts: Mapping[str, Mapping[str, object]]
    positions: Mapping[str, Mapping[str, object]]
    forecasts: tuple[Mapping[str, object], ...]
    paper_equity_history: tuple[Mapping[str, object], ...]


def _initial_forward_state(runtime: P6ProspectiveRuntimeConfig) -> P6ForwardState:
    policy = load_paper_policy(runtime.bindings["paper_lane"], root=runtime.root)
    return P6ForwardState(
        market_date=None,
        release_package_id=None,
        release_manifest_sha256=None,
        collection_session_count=0,
        tail_session_count=0,
        ledger=initial_paper_ledger(policy),
        pending_intents=(),
        pending_contexts=MappingProxyType({}),
        positions=MappingProxyType({}),
        forecasts=(),
        paper_equity_history=(),
    )


def _forward_state_to_dict(state: P6ForwardState) -> dict[str, object]:
    return {
        "schema_version": "quanthus.p6-forward-state.v1",
        "market_date": None if state.market_date is None else state.market_date.isoformat(),
        "release_package_id": state.release_package_id,
        "release_manifest_sha256": state.release_manifest_sha256,
        "collection_session_count": state.collection_session_count,
        "tail_session_count": state.tail_session_count,
        "paper_ledger": state.ledger.to_dict(),
        "pending_paper_intents": [item.to_dict() for item in state.pending_intents],
        "pending_intent_contexts": {
            key: dict(value) for key, value in sorted(state.pending_contexts.items())
        },
        "positions": {key: dict(value) for key, value in sorted(state.positions.items())},
        "forecasts": [dict(value) for value in state.forecasts],
        "paper_equity_history": [dict(value) for value in state.paper_equity_history],
    }


def _load_prior_forward_state(
    *,
    job_input: OfficialCycleJobInput,
    runtime: P6ProspectiveRuntimeConfig,
    run_root: Path,
) -> P6ForwardState:
    if job_input.prior_run_id is None:
        return _initial_forward_state(runtime)
    prior = (run_root.resolve() / job_input.prior_run_id).resolve()
    if prior.parent != run_root.resolve():
        fail("path_escape", "prior P6 run")
    from quanthus.forward_validation.runner import verify_forward_run

    verified = verify_forward_run(prior)
    if verified.manifest_digest != job_input.prior_run_manifest_digest:
        fail("binding_digest_drift", "prior P6 run manifest")
    if verified.summary.get("mode") != "official_cycle":
        fail("prior_run_mode_mismatch", "prior P6 run is not official")
    try:
        artifact = json.loads((prior / "official.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("prior_run_unreadable", str(prior)) from exc
    raw = artifact.get("forward_state") if isinstance(artifact, dict) else None
    if not isinstance(raw, dict) or raw.get("schema_version") != "quanthus.p6-forward-state.v1":
        fail("prior_state_missing", "prior official run has no forward state")
    try:
        market_date = date.fromisoformat(str(raw["market_date"]))
        release_package_id = str(raw["release_package_id"])
        release_manifest_sha256 = str(raw["release_manifest_sha256"])
        count = int(raw["collection_session_count"])
        tail_count = int(raw["tail_session_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ForwardValidationContractError("prior_state_invalid", "market date/count") from exc
    if (
        count <= 0
        or count > 66
        or tail_count < 0
        or tail_count > 20
        or market_date >= job_input.market_date
        or not release_package_id
        or not release_manifest_sha256.startswith("sha256:")
        or len(release_manifest_sha256) != 71
    ):
        fail("prior_state_invalid", "prior state identity/count does not precede current cycle")
    ledger_raw = raw.get("paper_ledger")
    intents_raw = raw.get("pending_paper_intents")
    contexts_raw = raw.get("pending_intent_contexts")
    positions_raw = raw.get("positions")
    forecasts_raw = raw.get("forecasts")
    equity_raw = raw.get("paper_equity_history")
    if (
        not isinstance(ledger_raw, dict)
        or not isinstance(intents_raw, list)
        or not isinstance(contexts_raw, dict)
        or not isinstance(positions_raw, dict)
        or not isinstance(forecasts_raw, list)
        or not isinstance(equity_raw, list)
    ):
        fail("prior_state_invalid", "forward state sections differ")
    intents = tuple(
        paper_pending_intent_from_dict(item)
        for item in intents_raw
        if isinstance(item, dict)
    )
    if len(intents) != len(intents_raw):
        fail("prior_state_invalid", "pending paper intents are malformed")
    contexts = {
        str(key): MappingProxyType(dict(value))
        for key, value in contexts_raw.items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    positions = {
        str(key): MappingProxyType(dict(value))
        for key, value in positions_raw.items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    forecasts = tuple(MappingProxyType(dict(item)) for item in forecasts_raw if isinstance(item, dict))
    equity_history = tuple(MappingProxyType(dict(item)) for item in equity_raw if isinstance(item, dict))
    if (
        len(contexts) != len(contexts_raw)
        or len(positions) != len(positions_raw)
        or len(forecasts) != len(forecasts_raw)
        or len(equity_history) != len(equity_raw)
        or set(contexts) != {item.paper_intent_id for item in intents}
    ):
        fail("prior_state_invalid", "forward state identities differ")
    ledger = paper_ledger_from_dict(ledger_raw)
    policy = load_paper_policy(runtime.bindings["paper_lane"], root=runtime.root)
    reconciliation = reconcile_paper_ledger(ledger, policy=policy)
    if not reconciliation.balanced:
        fail("paper_ledger_reconciliation_break", "prior paper ledger is unbalanced")
    return P6ForwardState(
        market_date=market_date,
        release_package_id=release_package_id,
        release_manifest_sha256=release_manifest_sha256,
        collection_session_count=count,
        tail_session_count=tail_count,
        ledger=ledger,
        pending_intents=intents,
        pending_contexts=MappingProxyType(contexts),
        positions=MappingProxyType(positions),
        forecasts=forecasts,
        paper_equity_history=equity_history,
    )


def _activation(
    job_input: OfficialCycleJobInput, runtime: P6ProspectiveRuntimeConfig
) -> P6ActivationDecision:
    decision = activation_decision_from_mapping(
        job_input.activation_decision,
        runtime=runtime,
    )
    if not decision.enabled:
        fail("official_cycle_not_authorized", "activation decision is not enabled")
    observed_revision = runtime_code_revision()
    if observed_revision is not None and observed_revision != decision.deployment_revision:
        fail("deployment_revision_drift", "worker revision differs from activation decision")
    return decision


def _release(
    job_input: OfficialCycleJobInput, runtime: P6ProspectiveRuntimeConfig
) -> ProspectiveDwdRelease:
    package = resolve_daily_package(
        runtime.daily_root,
        job_input.market_date,
        job_input.package_id,
        manifest_sha256=job_input.manifest_sha256,
    )
    receipt = load_visibility_receipt(job_input.visibility_receipt)
    release = verify_prospective_dwd_release(
        package, receipt, product_scope=runtime.daily_product_scope
    )
    if (
        release.manifest_sha256 != job_input.manifest_sha256
        or release.content_digest != job_input.content_digest
        or release.market_date != job_input.market_date
        or release.package_id != job_input.package_id
    ):
        fail("binding_digest_drift", "official DWD release")
    return release


def _trend_spec(runtime: P6ProspectiveRuntimeConfig) -> ExpertEvaluationSpec:
    artifact = artifact_from_bytes(runtime.bindings["trend_evaluation_spec"].read_bytes())
    if not isinstance(artifact, ExpertEvaluationSpec):
        fail("binding_type_mismatch", "trend evaluation spec")
    if artifact.assessment_id != "expert.trend-stability" or artifact.assessment_version != "1.0.0":
        fail("binding_identity_mismatch", "trend evaluation spec")
    return artifact


def _feature_evidence(feature: object | None, event_id: str) -> str:
    if feature is None:
        return digest_value({"event_id": event_id, "feature": None})
    fields = (
        "market_date",
        "security_id",
        "total_return_index",
        "sma20",
        "sma60",
        "momentum20",
        "rsi14",
        "realized_volatility20",
        "feature_ready",
        "entry_eligible",
    )
    payload: dict[str, object] = {"event_id": event_id}
    for field in fields:
        value = getattr(feature, field)
        if isinstance(value, date):
            payload[field] = value.isoformat()
        elif isinstance(value, Decimal):
            payload[field] = str(value)
        else:
            payload[field] = value
    return digest_value(payload)


def _build_entry_decisions(
    *,
    runtime: P6ProspectiveRuntimeConfig,
    projection: P6ProspectiveProjection,
) -> tuple[list[dict[str, Any]], list[tuple[Any, Any, ProspectiveTrendObservation]]]:
    candidates = scan_projection_candidates(
        projection, candidate_rule=runtime.bindings["candidate_rule"]
    )
    registry = ExpertStrategyRegistry(runtime.root)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    trend_config = load_trend_stability_config(runtime.bindings["trend_expert"])
    trend_spec = _trend_spec(runtime)
    feature_registry = load_feature_registry(runtime.bindings["feature_registry"])
    rows: list[dict[str, Any]] = []
    result: list[tuple[Any, Any, ProspectiveTrendObservation]] = []
    with projection.feature_panel() as panel:
        for candidate in candidates.candidates:
            event = candidate.record
            panel_feature = panel.feature(event.identity.signal_date, candidate.listing_code)
            feature = (
                None
                if panel_feature is None
                else replace(panel_feature, security_id=event.identity.security_id)
            )
            raw_value, reason = classify_trend_stability(feature, trend_config)
            observation = ProspectiveTrendObservation(
                event_id=event.event_id,
                market_date=event.identity.signal_date,
                expert_id=trend_config.expert_id,
                expert_version=trend_config.expert_version,
                reference_evaluation_spec_digest=trend_spec.digest,
                reference_assessment_binding_digest=trend_spec.assessment_binding_digest,
                expert_semantic_digest=trend_config.semantic_digest,
                applicability="abstained" if raw_value is None else "applicable",
                applicability_reason=reason,
                raw_value=raw_value,
                evidence_digest=_feature_evidence(feature, event.event_id),
                knowledge_cutoff=event.knowledge_cutoff,
            )
            context = build_formal_expert_context(
                event=event,
                feature=feature,
                feature_registry=feature_registry,
                panel_report=panel.report,
                position_state="empty",
                trend_observation_digest=observation.digest,
            )
            decision = runner.evaluate_entry(
                event_id=event.event_id,
                context=context,
                trend_state=raw_value,
                entry_evidence_ready=feature is not None and feature.entry_eligible,
            )
            row = {
                "event": event.to_dict(),
                "trend_observation": observation.to_dict(),
                "trend_observation_digest": observation.digest,
                "formal_decision": decision.to_dict(),
                "formal_decision_digest": decision.digest,
                "listing_code": candidate.listing_code,
                "decision_close": None if feature is None else str(feature.close_price),
            }
            rows.append(row)
            result.append((candidate, decision, observation))
    return rows, result


def _paper_and_shadow(
    *,
    runtime: P6ProspectiveRuntimeConfig,
    projection: P6ProspectiveProjection,
    decisions: list[tuple[Any, Any, ProspectiveTrendObservation]],
    ledger: PaperLedgerState,
    unavailable_unit_ids: set[str],
) -> tuple[
    PaperLedgerState,
    list[dict[str, object]],
    list[PaperPendingIntent],
    list[dict[str, object]],
    dict[str, dict[str, object]],
]:
    paper_policy = load_paper_policy(runtime.bindings["paper_lane"], root=runtime.root)
    empty_units = [
        item.unit_id
        for item in ledger.units
        if item.settled_quantity == 0 and item.unit_id not in unavailable_unit_ids
    ]
    shadow: list[dict[str, object]] = []
    pending: list[PaperPendingIntent] = []
    allocation: list[dict[str, object]] = []
    contexts: dict[str, dict[str, object]] = {}
    ordered = sorted(
        decisions,
        key=lambda item: (
            -item[1].action.aggregate_score,
            item[0].record.identity.security_id,
        ),
    )
    with projection.feature_panel() as panel:
        for candidate, decision, observation in ordered:
            shadow.append(
                {
                    "schema_version": "quanthus.p6-shadow-decision.v1",
                    "authority": "shadow",
                    "actual_execution_authorized": False,
                    "event_id": candidate.record.event_id,
                    "trend_observation_digest": observation.digest,
                    "formal_decision_digest": decision.digest,
                    "final_action": decision.action.final_action.value,
                    "aggregate_score": str(decision.action.aggregate_score),
                    "reason_codes": list(decision.action.reason_codes),
                }
            )
            if decision.action.final_action is not GovernedAction.TRIAL_ENTRY:
                allocation.append(
                    {
                        "event_id": candidate.record.event_id,
                        "formal_decision_digest": decision.digest,
                        "paper_disposition": "no_new_intent",
                        "unit_id": None,
                    }
                )
                continue
            if not empty_units:
                allocation.append(
                    {
                        "event_id": candidate.record.event_id,
                        "formal_decision_digest": decision.digest,
                        "paper_disposition": "funding_units_exhausted",
                        "unit_id": None,
                    }
                )
                continue
            unit_id = empty_units.pop(0)
            feature = panel.feature(candidate.record.identity.signal_date, candidate.listing_code)
            if feature is None:
                fail("paper_decision_close_missing", candidate.record.event_id)
            sealed = SealedAggregateDecision(
                decision_digest=decision.digest,
                snapshot=projection.bundle.snapshot,
                decided_at=candidate.record.signal_close,
                security_code=candidate.listing_code,
                unit_id=unit_id,
                final_action=decision.action.final_action.value,
                aggregate_score=decision.action.aggregate_score,
                decision_version="candidate.p4-trend-risk@1.0.1",
                decision_close=feature.close_price,
                target_maximum_position=decision.target_maximum_position,
                reason_codes=decision.action.reason_codes,
            )
            intent = project_paper_intent(policy=paper_policy, ledger=ledger, decision=sealed)
            if intent is None:
                fail("paper_intent_missing", candidate.record.event_id)
            pending.append(intent)
            contexts[intent.paper_intent_id] = {
                "purpose": "entry",
                "source_event": candidate.record.to_dict(),
                "formal_security_id": candidate.record.identity.security_id,
                "listing_code": candidate.listing_code,
                "decision_digest": decision.digest,
            }
            allocation.append(
                {
                    "event_id": candidate.record.event_id,
                    "formal_decision_digest": decision.digest,
                    "paper_disposition": "pending_next_open",
                    "unit_id": unit_id,
                    "paper_intent_id": intent.paper_intent_id,
                }
            )
    return ledger, shadow, pending, allocation, contexts


def _next_open(trading_dates: tuple[date, ...], value: date) -> date | None:
    return next((item for item in trading_dates if item > value), None)


def _enforce_prospective_calendar(
    *,
    job_input: OfficialCycleJobInput,
    activation: P6ActivationDecision,
    prior: P6ForwardState,
    projection: P6ProspectiveProjection,
) -> None:
    if prior.market_date is None:
        # The target package must be the first immutable DWD successor whose direct predecessor is
        # not later than the activation date.  This proves no already-published post-activation DWD
        # package was skipped without consulting a future trading calendar.
        if not (
            projection.prior_market_date <= activation.activation_market_date
            < job_input.market_date
        ):
            fail(
                "official_cycle_session_gap",
                "first official DWD successor does not straddle the activation market date",
            )
    else:
        if (
            projection.prior_market_date != prior.market_date
            or projection.prior_package_id != prior.release_package_id
            or projection.prior_manifest_sha256 != prior.release_manifest_sha256
        ):
            fail(
                "official_cycle_session_gap",
                "current DWD package is not the direct immutable successor of the prior official run",
            )

    # We deliberately do not guess the future exchange calendar.  Sealing by the next *calendar*
    # day's 09:30 Asia/Shanghai is stricter than sealing before any possible later T+1 open,
    # including weekends and exchange holidays.
    conservative_deadline = datetime.combine(
        job_input.market_date + timedelta(days=1), time(9, 30), tzinfo=MARKET_TIMEZONE
    )
    if job_input.consumed_at >= conservative_deadline:
        fail(
            "historical_backfill_as_official",
            "official cycle was not sealed before the conservative next-open deadline",
        )


def _fill_payload(events: tuple[Any, ...]) -> Mapping[str, Any] | None:
    fills = [item.payload for item in events if item.event_type is EventType.FILL]
    if len(fills) > 1:
        fail("paper_fill_multiplicity", "daily P2 paper intent produced multiple fills")
    return None if not fills else fills[0]


def _settle_prior_intents(
    *,
    runtime: P6ProspectiveRuntimeConfig,
    projection: P6ProspectiveProjection,
    prior: P6ForwardState,
) -> tuple[
    PaperLedgerState,
    tuple[PaperPendingIntent, ...],
    dict[str, Mapping[str, object]],
    dict[str, dict[str, object]],
    list[dict[str, object]],
]:
    policy = load_paper_policy(runtime.bindings["paper_lane"], root=runtime.root)
    ledger = prior.ledger
    positions = {key: dict(value) for key, value in prior.positions.items()}
    retained: list[PaperPendingIntent] = []
    retained_contexts: dict[str, Mapping[str, object]] = {}
    settlements: list[dict[str, object]] = []
    current = projection.signal_market_date
    with HistoricalReplayFrameReader(projection.bundle) as reader:
        for intent in sorted(prior.pending_intents, key=lambda item: item.paper_intent_id):
            context = prior.pending_contexts.get(intent.paper_intent_id)
            if context is None:
                fail("prior_state_invalid", f"missing context for {intent.paper_intent_id}")
            eligible = _next_open(projection.trading_dates, intent.signal.snapshot.market_date)
            if eligible is None:
                fail("prospective_next_open_unknown", intent.paper_intent_id)
            if intent.signal.side is OrderSide.BUY:
                if current < eligible:
                    retained.append(intent)
                    retained_contexts[intent.paper_intent_id] = context
                    continue
                if current != eligible:
                    fail("missed_paper_buy_execution_session", intent.paper_intent_id)
            elif current < eligible:
                retained.append(intent)
                retained_contexts[intent.paper_intent_id] = context
                continue
            evidence = reader.execution_evidence(intent.signal.security_code, current)
            settlement = settle_paper_intent(
                policy=policy,
                ledger=ledger,
                intent=intent,
                sessions=(market_session_from_p1(evidence),),
            )
            ledger = settlement.ledger
            fill = _fill_payload(settlement.events)
            settlements.append(
                {
                    **settlement.to_dict(),
                    "market_date": current.isoformat(),
                    "events": [item.to_dict() for item in settlement.events],
                }
            )
            if intent.signal.side is OrderSide.BUY:
                if fill is not None:
                    source_event = context.get("source_event")
                    if not isinstance(source_event, dict):
                        fail("prior_state_invalid", "entry intent lost source event")
                    positions[intent.signal.unit_id] = {
                        "unit_id": intent.signal.unit_id,
                        "formal_security_id": str(context.get("formal_security_id")),
                        "listing_code": intent.signal.security_code,
                        "source_event": source_event,
                        "entry_date": current.isoformat(),
                        "entry_gross": str(fill["gross_amount"]),
                        "entry_fees": str(fill["total_fees"]),
                        "holding_sessions": 0,
                        "deterioration_streak": 0,
                        "strategy_reduction_count": 0,
                    }
                # Daily paper buys have one open attempt; filled or rejected, the intent is terminal.
                continue
            if fill is None:
                retained.append(intent)
                retained_contexts[intent.paper_intent_id] = context
                continue
            unit = ledger.unit(intent.signal.unit_id)
            if unit.settled_quantity == 0:
                positions.pop(intent.signal.unit_id, None)
            else:
                metadata = positions.get(intent.signal.unit_id)
                if metadata is None:
                    fail("prior_state_invalid", "filled reduction lost position metadata")
                if context.get("purpose") == "held_reduce":
                    metadata["strategy_reduction_count"] = int(
                        metadata.get("strategy_reduction_count", 0)
                    ) + 1
    return ledger, tuple(retained), retained_contexts, positions, settlements


def _held_event(source: EventRecord, projection: P6ProspectiveProjection) -> EventRecord:
    identity = EventIdentity(
        source.identity.security_id,
        projection.signal_market_date,
        source.identity.candidate_rule_id,
        source.identity.candidate_rule_version,
        projection.snapshot_id,
    )
    cutoff = projection.bundle.snapshot.knowledge_cutoff
    return EventRecord(
        identity,
        source.candidate_rule_digest,
        source.snapshot_manifest_digest,
        cutoff,
        cutoff,
        source.eligibility_evidence_digest,
        source.feature_window_manifest_digest,
        source.isolation_group_id,
        source.split_policy_id,
        source.split_policy_version,
        source.split_policy_digest,
        source.split_assignment,
    )


def _review_held_positions(
    *,
    runtime: P6ProspectiveRuntimeConfig,
    projection: P6ProspectiveProjection,
    ledger: PaperLedgerState,
    positions: dict[str, dict[str, object]],
    pending_intents: tuple[PaperPendingIntent, ...],
) -> tuple[
    PaperLedgerState,
    dict[str, dict[str, object]],
    list[dict[str, object]],
    list[PaperPendingIntent],
    dict[str, dict[str, object]],
]:
    policy = load_paper_policy(runtime.bindings["paper_lane"], root=runtime.root)
    registry = ExpertStrategyRegistry(runtime.root)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    feature_registry = load_feature_registry(runtime.bindings["feature_registry"])
    base = registry.card.semantic_sections["position_and_execution"]["base_position_management"]
    pending_units = {item.signal.unit_id for item in pending_intents if item.signal.side is OrderSide.SELL}
    decisions: list[dict[str, object]] = []
    new_intents: list[PaperPendingIntent] = []
    contexts: dict[str, dict[str, object]] = {}
    current = projection.signal_market_date
    with HistoricalReplayFrameReader(projection.bundle) as reader, projection.feature_panel() as panel:
        for unit_id in sorted(positions):
            metadata = positions[unit_id]
            unit = ledger.unit(unit_id)
            if unit.settled_quantity <= 0 or unit.security_code is None:
                fail("prior_state_invalid", f"position metadata exists for empty {unit_id}")
            listing_code = str(metadata.get("listing_code"))
            evidence = reader.execution_evidence(listing_code, current)
            if evidence.close_price is not None:
                marked = replace(
                    unit,
                    last_mark_price=evidence.close_price,
                    last_mark_snapshot_id=evidence.snapshot.snapshot_id,
                )
                ledger = replace_paper_unit(ledger, marked, policy=policy)
                unit = marked
            feature = panel.feature(current, listing_code)
            source_raw = metadata.get("source_event")
            if not isinstance(source_raw, dict):
                fail("prior_state_invalid", f"{unit_id} source event missing")
            source_event = event_record_from_dict(source_raw)
            formal_security_id = str(metadata.get("formal_security_id"))
            formal_feature = (
                None if feature is None else replace(feature, security_id=formal_security_id)
            )
            entry_date = date.fromisoformat(str(metadata.get("entry_date")))
            holding_sessions = sum(
                entry_date <= item <= current for item in projection.trading_dates
            )
            structural = bool(
                feature is not None
                and feature.total_return_index is not None
                and feature.sma20 is not None
                and feature.sma60 is not None
                and feature.total_return_index < feature.sma60
                and feature.sma20 <= feature.sma60
            )
            streak = int(metadata.get("deterioration_streak", 0)) + 1 if structural else 0
            metadata["holding_sessions"] = holding_sessions
            metadata["deterioration_streak"] = streak
            entry_gross = Decimal(str(metadata.get("entry_gross")))
            entry_fees = Decimal(str(metadata.get("entry_fees")))
            marked_return = (
                None
                if unit.last_mark_price is None or entry_gross <= 0
                else (unit.last_mark_price * unit.settled_quantity - entry_gross - entry_fees)
                / (entry_gross + entry_fees)
            )
            base_required = RequiredAction.NONE
            base_reason = "base_position_management_clear"
            if holding_sessions >= int(base["maximum_holding_sessions"]):
                base_required = RequiredAction.FORCE_LIQUIDATE
                base_reason = "base_maximum_holding_exit"
            elif marked_return is not None and marked_return <= -Decimal(str(base["constraint_stop_loss"])):
                base_required = RequiredAction.FORCE_LIQUIDATE
                base_reason = "base_constraint_stop_exit"
            elif holding_sessions >= int(base["minimum_holding_sessions"]) and streak >= int(base["deterioration_sessions"]):
                base_required = RequiredAction.FORCE_LIQUIDATE
                base_reason = "base_structural_deterioration_exit"
            facts = HeldPositionFacts(
                holding_sessions=max(1, holding_sessions),
                marked_return=marked_return,
                prior_strategy_reduction_count=int(metadata.get("strategy_reduction_count", 0)),
                evidence=position_facts_evidence(
                    unit_id=unit_id,
                    holding_sessions=max(1, holding_sessions),
                    marked_return=marked_return,
                    prior_strategy_reduction_count=int(metadata.get("strategy_reduction_count", 0)),
                ),
            )
            review_event = _held_event(source_event, projection)
            context = build_formal_expert_context(
                event=review_event,
                feature=formal_feature,
                feature_registry=feature_registry,
                panel_report=panel.report,
                position_state="trial",
                trend_observation_digest=digest_value(
                    {"held_review": review_event.event_id, "trend": "not_requested"}
                ),
                position_facts=facts,
            )
            decision = runner.evaluate_held(
                event_id=review_event.event_id,
                context=context,
                position_facts=facts,
                base_required_action=base_required,
                base_reason_code=base_reason,
            )
            decisions.append(
                {
                    "surface": "held",
                    "unit_id": unit_id,
                    "event": review_event.to_dict(),
                    "formal_decision": decision.to_dict(),
                    "formal_decision_digest": decision.digest,
                }
            )
            if decision.action.final_action not in {GovernedAction.FULL_EXIT, GovernedAction.REDUCE}:
                continue
            if unit_id in pending_units:
                decisions[-1]["paper_disposition"] = "existing_pending_sell_preserved"
                continue
            sealed = SealedAggregateDecision(
                decision_digest=decision.digest,
                snapshot=projection.bundle.snapshot,
                decided_at=projection.bundle.snapshot.knowledge_cutoff,
                security_code=listing_code,
                unit_id=unit_id,
                final_action=decision.action.final_action.value,
                aggregate_score=decision.action.aggregate_score,
                decision_version="candidate.p4-trend-risk@1.0.1",
                decision_close=unit.last_mark_price,
                target_maximum_position=decision.target_maximum_position,
                reason_codes=decision.action.reason_codes,
            )
            intent = project_paper_intent(policy=policy, ledger=ledger, decision=sealed)
            if intent is None:
                decisions[-1]["paper_disposition"] = "no_reduction_quantity"
                continue
            new_intents.append(intent)
            purpose = (
                "held_exit"
                if decision.action.final_action is GovernedAction.FULL_EXIT
                else "held_reduce"
            )
            contexts[intent.paper_intent_id] = {
                "purpose": purpose,
                "unit_id": unit_id,
                "decision_digest": decision.digest,
            }
            decisions[-1]["paper_disposition"] = "pending_next_open"
            decisions[-1]["paper_intent_id"] = intent.paper_intent_id
    return ledger, positions, decisions, new_intents, contexts


def _new_forecasts(
    decisions: list[tuple[Any, Any, ProspectiveTrendObservation]],
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for candidate, _decision, observation in decisions:
        output.append(
            {
                "forecast_id": digest_value(
                    {
                        "namespace": "p6-prospective-trend-target-v1",
                        "event_id": candidate.record.event_id,
                        "observation_digest": observation.digest,
                    }
                ),
                "event_id": candidate.record.event_id,
                "listing_code": candidate.listing_code,
                "signal_date": candidate.record.identity.signal_date.isoformat(),
                "entry_date": None,
                "trend_observation_digest": observation.digest,
                "trend_state": observation.raw_value,
                "applicability": observation.applicability,
                "status": (
                    "abstained" if observation.applicability == "abstained" else "awaiting_entry"
                ),
                "entry_open_price": None,
                "session_count": 0,
                "outcome": None,
                "reason_code": observation.applicability_reason,
                "terminal_date": None,
                "target_policy": "p4-event.outcome-first-passage@1.0.0",
                "corporate_action_daily_evidence": "governed_empty_or_DWD_build_would_fail",
            }
        )
    return output


def _settle_forecasts(
    *,
    projection: P6ProspectiveProjection,
    forecasts: tuple[Mapping[str, object], ...],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    updated = [dict(item) for item in forecasts]
    settlements: list[dict[str, object]] = []
    current = projection.signal_market_date
    with HistoricalReplayFrameReader(projection.bundle) as reader, projection.feature_panel() as panel:
        for item in updated:
            status = str(item.get("status"))
            if status in {"abstained", "settled", "not_executable", "unavailable"}:
                continue
            signal_date = date.fromisoformat(str(item.get("signal_date")))
            if current <= signal_date:
                continue
            listing_code = str(item.get("listing_code"))
            evidence = reader.execution_evidence(listing_code, current)
            session = market_session_from_p1(evidence)
            if status == "awaiting_entry":
                reject = buy_open_rejection_reason(session.open_execution_state)
                if reject is not None:
                    item.update(
                        {
                            "status": "not_executable",
                            "outcome": "not_executable",
                            "reason_code": reject,
                            "terminal_date": current.isoformat(),
                        }
                    )
                    settlements.append(
                        {
                            "forecast_id": item["forecast_id"],
                            "event_id": item["event_id"],
                            "state": "not_executable",
                            "outcome": "not_executable",
                            "reason_code": reject,
                            "terminal_date": current.isoformat(),
                        }
                    )
                    continue
                if session.open_price is None:
                    fail("target_entry_price_missing", str(item.get("forecast_id")))
                item["entry_open_price"] = str(session.open_price)
                item["entry_date"] = current.isoformat()
                item["status"] = "active"
            entry_open = Decimal(str(item.get("entry_open_price")))
            if evidence.coverage_status is CoverageStatus.SUSPENDED:
                item["session_count"] = int(item.get("session_count", 0)) + 1
                if int(item["session_count"]) >= 20:
                    item.update(
                        {
                            "status": "settled",
                            "outcome": "neutral",
                            "reason_code": "observation_window_ended_without_terminal_condition",
                            "terminal_date": current.isoformat(),
                        }
                    )
                if item["status"] == "settled":
                    settlements.append(
                        {
                            "forecast_id": item["forecast_id"],
                            "event_id": item["event_id"],
                            "state": "settled",
                            "outcome": item["outcome"],
                            "reason_code": item["reason_code"],
                            "terminal_date": item["terminal_date"],
                        }
                    )
                continue
            if (
                evidence.coverage_status is not CoverageStatus.TRADING
                or not evidence.data_valid
                or evidence.high_price is None
                or evidence.low_price is None
                or evidence.close_price is None
            ):
                item.update(
                    {
                        "status": "unavailable",
                        "outcome": None,
                        "reason_code": "price_path_evidence_unavailable",
                        "terminal_date": current.isoformat(),
                    }
                )
                settlements.append(
                    {
                        "forecast_id": item["forecast_id"],
                        "event_id": item["event_id"],
                        "state": "unavailable",
                        "outcome": None,
                        "reason_code": item["reason_code"],
                        "terminal_date": item["terminal_date"],
                    }
                )
                continue
            feature = panel.feature(current, listing_code)
            if (
                feature is None
                or not feature.feature_ready
                or feature.total_return_index is None
                or feature.sma20 is None
                or feature.sma60 is None
            ):
                item.update(
                    {
                        "status": "unavailable",
                        "outcome": None,
                        "reason_code": "structural_break_evidence_unavailable",
                        "terminal_date": current.isoformat(),
                    }
                )
                settlements.append(
                    {
                        "forecast_id": item["forecast_id"],
                        "event_id": item["event_id"],
                        "state": "unavailable",
                        "outcome": None,
                        "reason_code": item["reason_code"],
                        "terminal_date": item["terminal_date"],
                    }
                )
                continue
            item["session_count"] = int(item.get("session_count", 0)) + 1
            favorable = evidence.high_price / entry_open - Decimal("1")
            adverse = Decimal("1") - evidence.low_price / entry_open
            structural = (
                feature.total_return_index < feature.sma60 and feature.sma20 <= feature.sma60
            )
            failure = structural or adverse >= Decimal("0.07")
            positive = favorable >= Decimal("0.15")
            if failure:
                if structural and adverse >= Decimal("0.07"):
                    reason = "structural_break_and_adverse_threshold"
                elif structural:
                    reason = "structural_break"
                else:
                    reason = "adverse_threshold"
                item.update(
                    {
                        "status": "settled",
                        "outcome": "failure",
                        "reason_code": reason,
                        "terminal_date": current.isoformat(),
                    }
                )
            elif positive:
                item.update(
                    {
                        "status": "settled",
                        "outcome": "positive",
                        "reason_code": "positive_threshold",
                        "terminal_date": current.isoformat(),
                    }
                )
            elif int(item["session_count"]) >= 20:
                item.update(
                    {
                        "status": "settled",
                        "outcome": "neutral",
                        "reason_code": "observation_window_ended_without_terminal_condition",
                        "terminal_date": current.isoformat(),
                    }
                )
            if item["status"] == "settled":
                settlements.append(
                    {
                        "forecast_id": item["forecast_id"],
                        "event_id": item["event_id"],
                        "state": "settled",
                        "outcome": item["outcome"],
                        "reason_code": item["reason_code"],
                        "terminal_date": item["terminal_date"],
                        "session_count": item["session_count"],
                    }
                )
    return updated, settlements


def _paper_equity_record(
    *,
    runtime: P6ProspectiveRuntimeConfig,
    ledger: PaperLedgerState,
    market_date: date,
) -> dict[str, object]:
    policy = load_paper_policy(runtime.bindings["paper_lane"], root=runtime.root)
    reconciliation = reconcile_paper_ledger(ledger, policy=policy)
    return {
        "market_date": market_date.isoformat(),
        "ledger_digest": ledger.digest,
        "total_cash": str(reconciliation.total_cash),
        "total_market_value": str(reconciliation.total_market_value),
        "total_external_pool": str(reconciliation.total_external_pool),
        "total_equity": str(reconciliation.total_equity),
    }


def _scorecards(
    *,
    runtime: P6ProspectiveRuntimeConfig,
    decision_rows: list[dict[str, Any]],
    held_decisions: list[dict[str, object]],
    pending: tuple[PaperPendingIntent, ...],
    ledger: PaperLedgerState,
    forecasts: list[dict[str, object]],
    equity_history: list[dict[str, object]],
) -> list[dict[str, object]]:
    policy = load_scorecard_policy(runtime.bindings["scorecards"])
    applicable = sum(item.get("applicability") == "applicable" for item in forecasts)
    abstained = sum(item.get("applicability") == "abstained" for item in forecasts)
    settled = [
        item
        for item in forecasts
        if item.get("status") == "settled"
        and item.get("trend_state") in {"fragile", "neutral", "stable"}
        and item.get("outcome") in {"failure", "neutral", "positive"}
    ]
    state_order = {"fragile": 0, "neutral": 1, "stable": 2}
    outcome_order = {"failure": 0, "neutral": 1, "positive": 2}
    if settled:
        maoe = sum(
            Decimal(abs(state_order[str(item["trend_state"])] - outcome_order[str(item["outcome"])]))
            for item in settled
        ) / Decimal(len(settled))
        neutral_maoe = sum(
            Decimal(abs(1 - outcome_order[str(item["outcome"])])) for item in settled
        ) / Decimal(len(settled))
        trend_maoe: str | Decimal = maoe
        trend_baseline: str | Decimal = maoe - neutral_maoe
        trend_limits = ("first-passage settlements exclude non-executable/unavailable rows",)
    else:
        trend_maoe = "pending_maturity"
        trend_baseline = "pending_maturity"
        trend_limits = ("no eligible first-passage target is mature yet",)
    all_decisions = [*decision_rows, *held_decisions]
    actions: dict[str, int] = {}
    position_activations = 0
    for row in all_decisions:
        decision = row.get("formal_decision")
        if not isinstance(decision, dict) or not isinstance(decision.get("action"), dict):
            continue
        action = str(decision["action"].get("final_action"))
        actions[action] = actions.get(action, 0) + 1
        reasons = tuple(str(item) for item in decision["action"].get("reason_codes", ()))
        position_activations += int(
            action in {"reduce", "full_exit"}
            or any("position" in item or "risk" in item for item in reasons)
        )
    source = digest_value(all_decisions)
    paper_policy = load_paper_policy(runtime.bindings["paper_lane"], root=runtime.root)
    reconciliation = reconcile_paper_ledger(ledger, policy=paper_policy)
    if equity_history:
        values = [Decimal(str(item["total_equity"])) for item in equity_history]
        initial = paper_policy.unit_notional * Decimal(paper_policy.unit_count)
        net_return: str | Decimal = values[-1] / initial - Decimal("1")
        peak = values[0]
        maximum_drawdown = Decimal("0")
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                maximum_drawdown = max(maximum_drawdown, Decimal("1") - value / peak)
        drawdown: str | Decimal = maximum_drawdown
        strategy_limits = ("paper return uses fixed-unit non-compounding equity",)
    else:
        net_return = "pending_no_settled_fill"
        drawdown = "pending_no_settled_fill"
        strategy_limits = ("paper equity has not reached its first official close",)
    cards = [
        build_role_scorecard(
            policy=policy,
            role="trend_assessment",
            source_artifact_digest=digest_value(forecasts),
            metric_values={
                "trend_maoe": trend_maoe,
                "trend_maoe_vs_always_neutral": trend_baseline,
                "coverage": "0" if not forecasts else str(Decimal(applicable) / Decimal(len(forecasts))),
                "abstention_rate": "0" if not forecasts else str(Decimal(abstained) / Decimal(len(forecasts))),
            },
            sample_count=len(settled),
            slices={},
            limitations=trend_limits,
        ),
        build_role_scorecard(
            policy=policy,
            role="position_risk",
            source_artifact_digest=source,
            metric_values={
                "policy_activation_rate": "0" if not all_decisions else str(Decimal(position_activations) / Decimal(len(all_decisions))),
                "decision_change_rate": "pending_counterfactual",
            },
            sample_count=len(all_decisions),
            slices={},
            limitations=("policy-neutralized forward counterfactual remains separately attributable",),
        ),
        build_role_scorecard(
            policy=policy,
            role="aggregator",
            source_artifact_digest=source,
            metric_values={
                "agreement_state_distribution": "single_scoring_expert_not_applicable",
                "final_action_distribution": json.dumps(actions, sort_keys=True, separators=(",", ":")),
            },
            sample_count=len(all_decisions),
            slices={},
            limitations=("the frozen v1 topology has one positive scoring expert",),
        ),
        build_role_scorecard(
            policy=policy,
            role="complete_strategy",
            source_artifact_digest=digest_value(equity_history),
            metric_values={"net_total_return": net_return, "maximum_drawdown": drawdown},
            sample_count=len(equity_history),
            slices={},
            limitations=strategy_limits,
        ),
        build_role_scorecard(
            policy=policy,
            role="paper_operation",
            source_artifact_digest=ledger.digest,
            metric_values={
                "cash_position_reconciliation_break_count": 0 if reconciliation.balanced else 1,
                "pending_intent_count": len(pending),
            },
            sample_count=len(pending),
            slices={},
            limitations=("paper authority never implies broker/live execution authority",),
        ),
    ]
    return [item.to_dict() | {"digest": item.digest} for item in cards]


def _run_id(input_digest_value: str) -> str:
    return f"p6_run_{input_digest_value[7:31]}"


def run_official_cycle(
    *,
    job_input: OfficialCycleJobInput,
    runtime: P6ProspectiveRuntimeConfig,
    staging: Path,
    run_root: Path,
) -> OfficialCycleResult:
    if job_input.runtime_sha256 != runtime.config_sha256:
        fail("binding_digest_drift", "official input binds another runtime")
    activation = _activation(job_input, runtime)
    release = _release(job_input, runtime)
    boundary = P6ActivationBoundary(
        activation.decision_digest,
        activation.decision_at,
        activation.activation_market_date,
        activation.enabled,
    )
    admission = classify_release_admission(release, boundary)
    if not admission.official_evidence:
        fail("official_cycle_not_authorized", admission.reason_code)
    input_digest_value = job_input.input_digest
    run_id = _run_id(input_digest_value)
    final = run_root.resolve() / run_id
    if final.exists():
        from quanthus.forward_validation.runner import verify_forward_run

        existing = verify_forward_run(final, expected_input_digest=input_digest_value)
        return OfficialCycleResult(
            existing.run_id,
            existing.root,
            existing.input_digest,
            existing.manifest_digest,
            existing.summary,
        )
    prior_state = _load_prior_forward_state(
        job_input=job_input, runtime=runtime, run_root=run_root
    )
    if prior_state.collection_session_count >= 66 and prior_state.tail_session_count >= 20:
        fail("forward_budget_closed", "66-session collection plus 20-session tail is complete")
    collection_active = prior_state.collection_session_count < 66
    work = staging.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    projection = build_prospective_projection(
        config=runtime,
        activation=activation,
        target=release,
        output_root=work / "projection",
        collect_candidates=collection_active,
    )
    _enforce_prospective_calendar(
        job_input=job_input,
        activation=activation,
        prior=prior_state,
        projection=projection,
    )
    settled_forecasts, target_settlements = _settle_forecasts(
        projection=projection, forecasts=prior_state.forecasts
    )
    (
        ledger,
        retained_pending,
        retained_contexts,
        positions,
        paper_settlements,
    ) = _settle_prior_intents(
        runtime=runtime, projection=projection, prior=prior_state
    )
    (
        ledger,
        positions,
        held_decisions,
        held_pending,
        held_contexts,
    ) = _review_held_positions(
        runtime=runtime,
        projection=projection,
        ledger=ledger,
        positions=positions,
        pending_intents=retained_pending,
    )
    if collection_active:
        decision_rows, decisions = _build_entry_decisions(
            runtime=runtime, projection=projection
        )
    else:
        decision_rows, decisions = [], []
    unavailable_units = {
        item.signal.unit_id for item in (*retained_pending, *held_pending)
    }
    (
        ledger,
        entry_shadow,
        entry_pending,
        allocation,
        entry_contexts,
    ) = _paper_and_shadow(
        runtime=runtime,
        projection=projection,
        decisions=decisions,
        ledger=ledger,
        unavailable_unit_ids=unavailable_units,
    )
    pending = tuple((*retained_pending, *held_pending, *entry_pending))
    pending_contexts: dict[str, Mapping[str, object]] = {
        **retained_contexts,
        **held_contexts,
        **entry_contexts,
    }
    if set(pending_contexts) != {item.paper_intent_id for item in pending}:
        fail("forward_state_identity_mismatch", "pending intent contexts differ")
    forecasts = [*settled_forecasts, *_new_forecasts(decisions)]
    forecast_ids = [str(item.get("forecast_id")) for item in forecasts]
    if len(forecast_ids) != len(set(forecast_ids)):
        fail("forward_state_identity_mismatch", "duplicate forecast identity")
    collection_count = prior_state.collection_session_count + (1 if collection_active else 0)
    tail_count = prior_state.tail_session_count + (0 if collection_active else 1)
    equity_history = [dict(item) for item in prior_state.paper_equity_history]
    equity_history.append(
        _paper_equity_record(
            runtime=runtime, ledger=ledger, market_date=projection.signal_market_date
        )
    )
    forward_state = P6ForwardState(
        market_date=projection.signal_market_date,
        release_package_id=release.package_id,
        release_manifest_sha256=release.manifest_sha256,
        collection_session_count=collection_count,
        tail_session_count=tail_count,
        ledger=ledger,
        pending_intents=pending,
        pending_contexts=MappingProxyType(dict(pending_contexts)),
        positions=MappingProxyType(
            {key: MappingProxyType(dict(value)) for key, value in positions.items()}
        ),
        forecasts=tuple(MappingProxyType(dict(item)) for item in forecasts),
        paper_equity_history=tuple(
            MappingProxyType(dict(item)) for item in equity_history
        ),
    )
    held_shadow = [
        {
            "schema_version": "quanthus.p6-shadow-decision.v1",
            "authority": "shadow",
            "actual_execution_authorized": False,
            "surface": "held",
            "unit_id": row["unit_id"],
            "formal_decision_digest": row["formal_decision_digest"],
            "final_action": row["formal_decision"]["action"]["final_action"],
            "reason_codes": row["formal_decision"]["action"]["reason_codes"],
        }
        for row in held_decisions
    ]
    shadow = [*entry_shadow, *held_shadow]
    if job_input.consumed_at < release.visibility.consumer_visible_at:
        fail("consumption_before_visibility", "official input consumed_at predates visibility")
    access = make_access_ledger_entry(
        release=release,
        admission=admission,
        purpose="p6_forward_cycle_input",
        decision_knowledge_cutoff=projection.bundle.snapshot.knowledge_cutoff.astimezone(timezone.utc),
        consumed_at=job_input.consumed_at,
    ).to_dict()
    scorecards = _scorecards(
        runtime=runtime,
        decision_rows=decision_rows,
        held_decisions=held_decisions,
        pending=pending,
        ledger=ledger,
        forecasts=forecasts,
        equity_history=equity_history,
    )
    official = {
        "schema_version": _OFFICIAL_ARTIFACT_SCHEMA,
        "job_kind": _JOB_KIND,
        "input": job_input.to_dict(),
        "activation_decision_digest": activation.decision_digest,
        "runtime_sha256": runtime.config_sha256,
        "access_ledger": access,
        "projection": {
            "artifact_ref": "projection",
            "manifest_sha256": projection.manifest_sha256,
            "content_digest": projection.content_digest,
            "snapshot_id": projection.snapshot_id,
        },
        "decisions": decision_rows,
        "held_decisions": held_decisions,
        "shadow_records": shadow,
        "paper_allocation": allocation,
        "paper_settlements": paper_settlements,
        "paper_ledger": ledger.to_dict(),
        "paper_ledger_digest": ledger.digest,
        "pending_paper_intents": [item.to_dict() for item in pending],
        "target_settlements": target_settlements,
        "scorecards": scorecards,
        "forward_state": _forward_state_to_dict(forward_state),
        "authority": {
            "official_prospective_evidence": True,
            "paper_only": True,
            "notification_authorized": False,
            "broker_authorized": False,
            "live_capital_authorized": False,
            "arbitration_2022_2024_access": False,
            "permanent_blind_2025_access": False,
        },
    }
    official_path = work / "official.json"
    official_path.write_bytes(canonical_json_bytes(official) + b"\n")
    stage_status = {
        "verify_frozen_bindings_and_activation": "PASS",
        "verify_and_bind_one_dwd_release": "PASS",
        "build_one_shared_effective_view_projection": "PASS",
        "settle_eligible_prior_paper_intents": (
            f"PASS_SETTLEMENTS_{len(paper_settlements)}"
        ),
        "produce_outcome_blind_expert_observations_and_aggregate_decision": (
            "PASS" if collection_active else "PASS_COLLECTION_CLOSED_TAIL_ONLY"
        ),
        "append_shadow_record_without_execution_edge": "PASS",
        "project_aggregate_decision_to_isolated_paper_intent": "PASS",
        "settle_matured_forecast_targets": f"PASS_SETTLEMENTS_{len(target_settlements)}",
        "build_role_specific_scorecards_and_loss_attribution": "PASS",
        "seal_immutable_forward_run": "PASS",
    }
    summary = {
        "mode": "official_cycle",
        "official_evidence": True,
        "market_date": release.market_date.isoformat(),
        "release_identity_digest": release.identity_digest,
        "dwd_read_count": 1,
        "shared_projection_build_count": 1,
        "candidate_count": len(decision_rows),
        "held_review_count": len(held_decisions),
        "collection_session_count": collection_count,
        "tail_session_count": tail_count,
        "shadow_write_count": len(shadow),
        "paper_write_count": len(held_pending) + len(entry_pending),
        "settlement_write_count": len(paper_settlements) + len(target_settlements),
        "scorecard_write_count": len(scorecards),
        "notification_count": 0,
        "broker_action_count": 0,
        "live_capital_action_count": 0,
        "paper_ledger_digest": ledger.digest,
        "pending_paper_intent_count": len(pending),
        "position_count": len(positions),
        "forecast_count": len(forecasts),
        "stage_status": stage_status,
    }
    manifest = {
        "schema_version": _RUN_SCHEMA,
        "run_id": run_id,
        "input_digest": input_digest_value,
        "mode": "official_cycle",
        "runtime_sha256": runtime.config_sha256,
        "official_sha256": _sha(official_path),
        "projection_manifest_sha256": projection.manifest_sha256,
        "summary": summary,
    }
    manifest_path = work / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    (work / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")
    run_root.resolve().mkdir(parents=True, exist_ok=True)
    if final.exists():
        fail("immutable_forward_run_exists", run_id)
    os.replace(work, final)
    from quanthus.forward_validation.runner import verify_forward_run

    verified = verify_forward_run(final, expected_input_digest=input_digest_value)
    return OfficialCycleResult(
        verified.run_id,
        verified.root,
        verified.input_digest,
        verified.manifest_digest,
        verified.summary,
    )
