"""Prospective DWD, access-ledger, and correction boundary for P6-DATA-001.

This module is implementation-only.  It never activates P6 and never turns an
already-known release into official prospective evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.serialization import digest_value
from quanthus.pipelines.securities_warehouse.daily_domain import (
    DailyDomainBuildError,
    verify_daily_domain_increment,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_POLICY_SCHEMA = "quanthus.p6-prospective-data-boundary.v1"
_VALUE_SCHEMA = "quanthus.p6-owner-value-decision.v1"
_VISIBILITY_SCHEMA = "quanthus.p6-dwd-visibility-receipt.v1"
_ACCESS_SCHEMA = "quanthus.p6-forward-access-ledger-entry.v1"
_CORRECTION_SCHEMA = "quanthus.p6-correction-disposition.v1"
_ENTRY_PROPOSAL_DIGEST = "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("invalid_json", str(path)) from exc
    if not isinstance(value, dict):
        fail("field_mismatch", f"{path} must contain one object")
    return value


def _require_digest(value: object, path: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        fail("invalid_digest", f"{path} must be lowercase sha256")
    return value


def _require_sha40(value: object, path: str) -> str:
    if not isinstance(value, str) or _SHA40.fullmatch(value) is None:
        fail("invalid_revision", f"{path} must be exact lowercase git sha")
    return value


def _require_aware(value: datetime, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        fail("invalid_time", f"{path} must be timezone aware")
    return value.astimezone(timezone.utc)


def _parse_instant(value: object, path: str) -> datetime:
    if not isinstance(value, str) or not value:
        fail("invalid_time", f"{path} must be ISO datetime text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForwardValidationContractError("invalid_time", path) from exc
    return _require_aware(parsed, path)


@dataclass(frozen=True)
class P6DataBoundaryPolicy:
    path: Path
    payload: Mapping[str, Any]
    policy_sha256: str


@dataclass(frozen=True)
class P6OwnerValueDecision:
    path: Path
    decision_id: str
    parent_entry_proposal_digest: str
    decision: str
    decision_sha256: str


@dataclass(frozen=True)
class DwdVisibilityReceipt:
    package_id: str
    market_date: date
    manifest_sha256: str
    content_digest: str
    ready_observed_at: datetime
    consumer_visible_at: datetime
    source_node: str
    receipt_digest: str

    @property
    def semantic_payload(self) -> dict[str, str]:
        return {
            "schema_version": _VISIBILITY_SCHEMA,
            "package_id": self.package_id,
            "market_date": self.market_date.isoformat(),
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
            "ready_observed_at": self.ready_observed_at.astimezone(timezone.utc).isoformat(),
            "consumer_visible_at": self.consumer_visible_at.astimezone(timezone.utc).isoformat(),
            "source_node": self.source_node,
        }


@dataclass(frozen=True)
class ProspectiveDwdRelease:
    package_root: Path
    package_id: str
    market_date: date
    manifest_sha256: str
    content_digest: str
    code_revision: str
    row_counts: Mapping[str, int]
    payload_bytes: int
    visibility: DwdVisibilityReceipt

    @property
    def identity_digest(self) -> str:
        return digest_value(
            {
                "package_id": self.package_id,
                "market_date": self.market_date.isoformat(),
                "manifest_sha256": self.manifest_sha256,
                "content_digest": self.content_digest,
                "code_revision": self.code_revision,
            }
        )


@dataclass(frozen=True)
class P6ActivationBoundary:
    decision_digest: str
    decision_at: datetime
    activation_market_date: date
    enabled: bool


@dataclass(frozen=True)
class P6ReleaseAdmission:
    release_identity_digest: str
    disposition: str
    official_evidence: bool
    reason_code: str
    activation_decision_digest: str | None


@dataclass(frozen=True)
class P6AccessLedgerEntry:
    access_id: str
    purpose: str
    package_id: str
    market_date: date
    manifest_sha256: str
    content_digest: str
    decision_knowledge_cutoff: datetime
    consumed_at: datetime
    entry_proposal_digest: str
    activation_decision_digest: str | None
    official_evidence: bool
    partition: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _ACCESS_SCHEMA,
            "access_id": self.access_id,
            "purpose": self.purpose,
            "package_id": self.package_id,
            "market_date": self.market_date.isoformat(),
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
            "decision_knowledge_cutoff": self.decision_knowledge_cutoff.astimezone(timezone.utc).isoformat(),
            "consumed_at": self.consumed_at.astimezone(timezone.utc).isoformat(),
            "entry_proposal_digest": self.entry_proposal_digest,
            "activation_decision_digest": self.activation_decision_digest,
            "official_evidence": self.official_evidence,
            "partition": self.partition,
        }


@dataclass(frozen=True)
class P6CorrectionDisposition:
    disposition_id: str
    prior_package_id: str
    prior_manifest_sha256: str
    correction_package_id: str
    correction_manifest_sha256: str
    affected_evidence_ids: tuple[str, ...]
    disposition: str
    rationale: str
    recorded_at: datetime
    rewrite_prior_evidence: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _CORRECTION_SCHEMA,
            "disposition_id": self.disposition_id,
            "prior_package_id": self.prior_package_id,
            "prior_manifest_sha256": self.prior_manifest_sha256,
            "correction_package_id": self.correction_package_id,
            "correction_manifest_sha256": self.correction_manifest_sha256,
            "affected_evidence_ids": list(self.affected_evidence_ids),
            "disposition": self.disposition,
            "rationale": self.rationale,
            "recorded_at": self.recorded_at.astimezone(timezone.utc).isoformat(),
            "rewrite_prior_evidence": self.rewrite_prior_evidence,
        }


def load_owner_value_decision(path: Path) -> P6OwnerValueDecision:
    raw = _load_json(path)
    if raw.get("schema_version") != _VALUE_SCHEMA or raw.get("work_item") != "P6-DATA-001":
        fail("value_decision_drift", "owner value decision identity differs")
    if raw.get("decision") != "OPEN_P6_DATA_001_ONLY":
        fail("value_decision_blocked", "P6-DATA-001 was not explicitly opened")
    parent = _require_digest(raw.get("parent_entry_proposal_digest"), "parent_entry_proposal_digest")
    if parent != _ENTRY_PROPOSAL_DIGEST:
        fail("value_decision_drift", "owner decision binds a different entry proposal")
    authority = raw.get("authority")
    if not isinstance(authority, dict):
        fail("field_mismatch", "value decision authority must be object")
    if authority.get("p6_data_implementation_authorized") is not True:
        fail("value_decision_blocked", "P6 data implementation authority is false")
    forbidden = (
        "p6_score_implementation_authorized",
        "forward_worker_authorized",
        "paper_runtime_authorized",
        "web_projection_authorized",
        "official_prospective_cycle_authorized",
        "notification_authorized",
        "broker_authorized",
        "live_capital_authorized",
        "p5_authorized",
        "new_expert_authorized",
        "stage_action_authority",
        "aggregator_change_authorized",
        "arbitration_2022_2024_access_authorized",
        "permanent_blind_2025_access_authorized",
    )
    if any(authority.get(name) is not False for name in forbidden):
        fail("authority_drift", "owner value decision grants downstream or sealed authority")
    return P6OwnerValueDecision(
        path=path,
        decision_id=str(raw.get("decision_id")),
        parent_entry_proposal_digest=parent,
        decision="OPEN_P6_DATA_001_ONLY",
        decision_sha256=_sha256(path),
    )


def load_data_boundary_policy(path: Path, *, root: Path) -> P6DataBoundaryPolicy:
    raw = _load_json(path)
    if (
        raw.get("schema_version") != _POLICY_SCHEMA
        or raw.get("work_item") != "P6-DATA-001"
        or raw.get("state") != "FROZEN_IMPLEMENTATION_ONLY_NO_ACTIVATION"
    ):
        fail("data_policy_drift", "P6 data-boundary identity differs")
    entry = raw.get("entry_proposal")
    owner = raw.get("owner_value_decision")
    if not isinstance(entry, dict) or not isinstance(owner, dict):
        fail("field_mismatch", "P6 data-boundary bindings are incomplete")
    if entry.get("proposal_digest") != _ENTRY_PROPOSAL_DIGEST:
        fail("data_policy_drift", "data policy binds another entry proposal")
    owner_path = root / str(owner.get("path"))
    decision = load_owner_value_decision(owner_path)
    if owner.get("sha256") != decision.decision_sha256 or owner.get("decision") != decision.decision:
        fail("binding_digest_drift", "owner value decision binding differs")
    activation = raw.get("activation_rule")
    if not isinstance(activation, dict):
        fail("field_mismatch", "activation rule is missing")
    if (
        activation.get("activation_owner") != "P6-ACTIVATE-001"
        or activation.get("official_requires_enabled_activation") is not True
        or activation.get("historical_2026_backfill_as_official") is not False
        or activation.get("first_official_market_date_rule")
        != "market_date_strictly_after_activation_market_date"
    ):
        fail("activation_authority_drift", "P6 data policy weakens activation boundary")
    authority = raw.get("authority")
    if not isinstance(authority, dict) or authority.get("p6_data_implementation_authorized") is not True:
        fail("authority_drift", "P6 data policy lacks implementation authority")
    for name, value in authority.items():
        if name != "p6_data_implementation_authorized" and value is not False:
            fail("authority_drift", f"P6 data policy unexpectedly enables {name}")
    return P6DataBoundaryPolicy(path=path, payload=raw, policy_sha256=_sha256(path))


def load_visibility_receipt(payload: Mapping[str, object]) -> DwdVisibilityReceipt:
    if payload.get("schema_version") != _VISIBILITY_SCHEMA:
        fail("visibility_receipt_drift", "visibility receipt schema differs")
    try:
        market_date = date.fromisoformat(str(payload["market_date"]))
    except (KeyError, ValueError) as exc:
        raise ForwardValidationContractError("invalid_date", "visibility market_date") from exc
    ready = _parse_instant(payload.get("ready_observed_at"), "ready_observed_at")
    visible = _parse_instant(payload.get("consumer_visible_at"), "consumer_visible_at")
    if visible < ready:
        fail("visibility_time_reversed", "consumer visibility predates READY observation")
    semantic = {
        "schema_version": _VISIBILITY_SCHEMA,
        "package_id": str(payload.get("package_id")),
        "market_date": market_date.isoformat(),
        "manifest_sha256": _require_digest(payload.get("manifest_sha256"), "manifest_sha256"),
        "content_digest": _require_digest(payload.get("content_digest"), "content_digest"),
        "ready_observed_at": ready.isoformat(),
        "consumer_visible_at": visible.isoformat(),
        "source_node": str(payload.get("source_node")),
    }
    receipt_digest = _require_digest(payload.get("receipt_digest"), "receipt_digest")
    if digest_value(semantic) != receipt_digest:
        fail("digest_mismatch", "visibility receipt digest differs")
    if not semantic["package_id"] or not semantic["source_node"]:
        fail("field_mismatch", "visibility receipt identity is empty")
    return DwdVisibilityReceipt(
        package_id=semantic["package_id"],
        market_date=market_date,
        manifest_sha256=semantic["manifest_sha256"],
        content_digest=semantic["content_digest"],
        ready_observed_at=ready,
        consumer_visible_at=visible,
        source_node=semantic["source_node"],
        receipt_digest=receipt_digest,
    )


def verify_prospective_dwd_release(
    package_root: Path,
    visibility: DwdVisibilityReceipt,
    *,
    product_scope: str = "legacy-four-lane",
) -> ProspectiveDwdRelease:
    manifest = _load_json(package_root / "manifest.json")
    if product_scope == "daily-core-v1":
        if (
            manifest.get("schema_version") != 2
            or manifest.get("kind") != "cn01_daily_core_dwd_increment"
            or manifest.get("product_scope") != "daily-core-v1"
            or manifest.get("consumable_intervals") != ["1d"]
        ):
            fail("p6_daily_core_product_drift", "non-core or non-daily DWD is not eligible")
        from quanthus.pipelines.securities_warehouse.daily_core_domain import (
            verify_daily_domain_increment as verify_core_increment,
        )
        verifier = verify_core_increment
    elif product_scope == "legacy-four-lane":
        if (
            manifest.get("kind") != "cn01_daily_dwd_domain_increment"
            or manifest.get("schema_version") != 1
        ):
            fail("p6_legacy_product_drift", "legacy P6 cannot silently consume daily-core")
        verifier = verify_daily_domain_increment
    else:
        fail("p6_daily_product_unknown", product_scope)
    try:
        verified = verifier(package_root)
    except DailyDomainBuildError as exc:
        raise ForwardValidationContractError("dwd_release_verification_failed", str(exc)) from exc
    code_revision = _require_sha40(manifest.get("code_revision"), "manifest.code_revision")
    observed = (
        verified.package_id,
        verified.market_date,
        verified.manifest_sha256,
        verified.content_digest,
    )
    bound = (
        visibility.package_id,
        visibility.market_date,
        visibility.manifest_sha256,
        visibility.content_digest,
    )
    if observed != bound:
        fail("visibility_binding_mismatch", "visibility receipt does not bind verified DWD package")
    return ProspectiveDwdRelease(
        package_root=package_root.resolve(),
        package_id=verified.package_id,
        market_date=verified.market_date,
        manifest_sha256=verified.manifest_sha256,
        content_digest=verified.content_digest,
        code_revision=code_revision,
        row_counts=verified.row_counts,
        payload_bytes=verified.payload_bytes,
        visibility=visibility,
    )


def classify_release_admission(
    release: ProspectiveDwdRelease,
    activation: P6ActivationBoundary | None,
) -> P6ReleaseAdmission:
    if activation is None or not activation.enabled:
        return P6ReleaseAdmission(
            release.identity_digest,
            "engineering_rehearsal_only_not_budget",
            False,
            "activation_not_enabled",
            None if activation is None else activation.decision_digest,
        )
    _require_digest(activation.decision_digest, "activation.decision_digest")
    _require_aware(activation.decision_at, "activation.decision_at")
    if release.market_date <= activation.activation_market_date:
        return P6ReleaseAdmission(
            release.identity_digest,
            "engineering_rehearsal_only_not_budget",
            False,
            "market_date_not_strictly_after_activation",
            activation.decision_digest,
        )
    if release.visibility.consumer_visible_at < activation.decision_at.astimezone(timezone.utc):
        fail("visibility_before_activation", "official release visibility predates activation decision")
    return P6ReleaseAdmission(
        release.identity_digest,
        "official_prospective_candidate",
        True,
        "post_activation_ready_visible_release",
        activation.decision_digest,
    )


def make_access_ledger_entry(
    *,
    release: ProspectiveDwdRelease,
    admission: P6ReleaseAdmission,
    purpose: str,
    decision_knowledge_cutoff: datetime,
    consumed_at: datetime,
    partition: str = "prospective_daily_dwd",
) -> P6AccessLedgerEntry:
    if partition != "prospective_daily_dwd":
        fail("sealed_partition_access_attempt", f"P6 data boundary forbids partition {partition}")
    cutoff = _require_aware(decision_knowledge_cutoff, "decision_knowledge_cutoff")
    consumed = _require_aware(consumed_at, "consumed_at")
    if consumed < release.visibility.consumer_visible_at:
        fail("pre_visibility_read", "P6 read predates consumer visibility evidence")
    if cutoff.date() != release.market_date:
        fail("cutoff_market_date_mismatch", "decision cutoff date differs from DWD market date")
    if not purpose or purpose.strip() != purpose:
        fail("field_mismatch", "access purpose must be canonical non-empty text")
    activation_digest = admission.activation_decision_digest
    semantic = {
        "purpose": purpose,
        "package_id": release.package_id,
        "market_date": release.market_date.isoformat(),
        "manifest_sha256": release.manifest_sha256,
        "content_digest": release.content_digest,
        "decision_knowledge_cutoff": cutoff.isoformat(),
        "consumed_at": consumed.isoformat(),
        "entry_proposal_digest": _ENTRY_PROPOSAL_DIGEST,
        "activation_decision_digest": activation_digest,
        "official_evidence": admission.official_evidence,
        "partition": partition,
    }
    access_id = digest_value(semantic)
    return P6AccessLedgerEntry(
        access_id=access_id,
        purpose=purpose,
        package_id=release.package_id,
        market_date=release.market_date,
        manifest_sha256=release.manifest_sha256,
        content_digest=release.content_digest,
        decision_knowledge_cutoff=cutoff,
        consumed_at=consumed,
        entry_proposal_digest=_ENTRY_PROPOSAL_DIGEST,
        activation_decision_digest=activation_digest,
        official_evidence=admission.official_evidence,
        partition=partition,
    )


def make_correction_disposition(
    *,
    prior_package_id: str,
    prior_manifest_sha256: str,
    correction_package_id: str,
    correction_manifest_sha256: str,
    affected_evidence_ids: tuple[str, ...],
    disposition: str,
    rationale: str,
    recorded_at: datetime,
) -> P6CorrectionDisposition:
    if disposition not in {"preserve", "degrade", "censor"}:
        fail("correction_disposition_invalid", "correction disposition must be preserve/degrade/censor")
    prior_digest = _require_digest(prior_manifest_sha256, "prior_manifest_sha256")
    correction_digest = _require_digest(correction_manifest_sha256, "correction_manifest_sha256")
    if prior_package_id == correction_package_id or prior_digest == correction_digest:
        fail("correction_identity_reuse", "correction must have a new immutable release identity")
    if not affected_evidence_ids or len(affected_evidence_ids) != len(set(affected_evidence_ids)):
        fail("field_mismatch", "affected evidence ids must be non-empty and unique")
    if not rationale or rationale.strip() != rationale:
        fail("field_mismatch", "correction rationale must be canonical non-empty text")
    instant = _require_aware(recorded_at, "recorded_at")
    semantic = {
        "prior_package_id": prior_package_id,
        "prior_manifest_sha256": prior_digest,
        "correction_package_id": correction_package_id,
        "correction_manifest_sha256": correction_digest,
        "affected_evidence_ids": list(affected_evidence_ids),
        "disposition": disposition,
        "rationale": rationale,
        "recorded_at": instant.isoformat(),
        "rewrite_prior_evidence": False,
    }
    return P6CorrectionDisposition(
        disposition_id=digest_value(semantic),
        prior_package_id=prior_package_id,
        prior_manifest_sha256=prior_digest,
        correction_package_id=correction_package_id,
        correction_manifest_sha256=correction_digest,
        affected_evidence_ids=affected_evidence_ids,
        disposition=disposition,
        rationale=rationale,
        recorded_at=instant,
        rewrite_prior_evidence=False,
    )
