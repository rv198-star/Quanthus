from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import quanthus.forward_validation.data_boundary as boundary
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.data_boundary import (
    P6ActivationBoundary,
    classify_release_admission,
    load_data_boundary_policy,
    load_owner_value_decision,
    load_visibility_receipt,
    make_access_ledger_entry,
    make_correction_disposition,
    verify_prospective_dwd_release,
)
from quanthus.forward_validation.serialization import digest_value
from quanthus.pipelines.securities_warehouse.daily_domain import DailyDomainResult

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/forward-validation/data/p6-prospective-data-boundary.v1.json"
OWNER = ROOT / "config/forward-validation/entry/p6-data-001-owner-value-decision.v1.json"
UTC = timezone.utc


def _visibility_payload(
    *,
    package_id: str = "dwd-daily-20260918-test-abcdef0",
    market_date: str = "2026-09-18",
    manifest_sha256: str = "sha256:" + "1" * 64,
    content_digest: str = "sha256:" + "2" * 64,
    ready: datetime = datetime(2026, 9, 18, 11, 31, tzinfo=UTC),
    visible: datetime = datetime(2026, 9, 18, 11, 32, tzinfo=UTC),
) -> dict[str, object]:
    semantic = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": package_id,
        "market_date": market_date,
        "manifest_sha256": manifest_sha256,
        "content_digest": content_digest,
        "ready_observed_at": ready.isoformat(),
        "consumer_visible_at": visible.isoformat(),
        "source_node": "oci-mirror",
    }
    return {**semantic, "receipt_digest": digest_value(semantic)}


def _fake_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    package = tmp_path / "daily"
    package.mkdir()
    (package / "manifest.json").write_text(
        json.dumps({"code_revision": "a" * 40}), encoding="utf-8"
    )
    receipt = load_visibility_receipt(_visibility_payload())
    result = DailyDomainResult(
        root=package,
        package_id=receipt.package_id,
        market_date=receipt.market_date,
        manifest_sha256=receipt.manifest_sha256,
        content_digest=receipt.content_digest,
        row_counts={"dwd_security_bar": 1},
        payload_bytes=123,
    )
    monkeypatch.setattr(boundary, "verify_daily_domain_increment", lambda _root: result)
    return verify_prospective_dwd_release(package, receipt)


def test_owner_value_decision_opens_only_p6_data() -> None:
    decision = load_owner_value_decision(OWNER)
    assert decision.decision == "OPEN_P6_DATA_001_ONLY"
    assert decision.parent_entry_proposal_digest.endswith("2aebaa18")

    policy = load_data_boundary_policy(POLICY, root=ROOT)
    assert policy.payload["authority"]["p6_data_implementation_authorized"] is True
    assert policy.payload["authority"]["official_prospective_cycle_authorized"] is False


def test_visibility_receipt_is_separate_operational_evidence() -> None:
    receipt = load_visibility_receipt(_visibility_payload())
    assert receipt.consumer_visible_at > receipt.ready_observed_at

    bad = _visibility_payload()
    bad["consumer_visible_at"] = "2026-09-18T11:30:00+00:00"
    semantic = {key: value for key, value in bad.items() if key != "receipt_digest"}
    bad["receipt_digest"] = digest_value(semantic)
    with pytest.raises(ForwardValidationContractError, match="visibility_time_reversed"):
        load_visibility_receipt(bad)


def test_deep_verified_release_must_match_visibility_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _fake_release(tmp_path, monkeypatch)
    assert release.code_revision == "a" * 40
    assert release.visibility.package_id == release.package_id

    wrong = load_visibility_receipt(
        _visibility_payload(content_digest="sha256:" + "3" * 64)
    )
    with pytest.raises(ForwardValidationContractError, match="visibility_binding_mismatch"):
        verify_prospective_dwd_release(release.package_root, wrong)


def test_pre_activation_and_same_day_release_never_count_as_official(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _fake_release(tmp_path, monkeypatch)
    assert classify_release_admission(release, None).official_evidence is False

    activation = P6ActivationBoundary(
        decision_digest="sha256:" + "4" * 64,
        decision_at=datetime(2026, 9, 18, 8, tzinfo=UTC),
        activation_market_date=date(2026, 9, 18),
        enabled=True,
    )
    admitted = classify_release_admission(release, activation)
    assert admitted.official_evidence is False
    assert admitted.reason_code == "market_date_not_strictly_after_activation"


def test_first_strictly_post_activation_release_can_be_official(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _fake_release(tmp_path, monkeypatch)
    activation = P6ActivationBoundary(
        decision_digest="sha256:" + "4" * 64,
        decision_at=datetime(2026, 9, 17, 12, tzinfo=UTC),
        activation_market_date=date(2026, 9, 17),
        enabled=True,
    )
    admitted = classify_release_admission(release, activation)
    assert admitted.official_evidence is True
    assert admitted.disposition == "official_prospective_candidate"


def test_access_ledger_is_append_only_identity_and_sealed_partitions_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _fake_release(tmp_path, monkeypatch)
    activation = P6ActivationBoundary(
        decision_digest="sha256:" + "4" * 64,
        decision_at=datetime(2026, 9, 17, 12, tzinfo=UTC),
        activation_market_date=date(2026, 9, 17),
        enabled=True,
    )
    admitted = classify_release_admission(release, activation)
    cutoff = datetime(2026, 9, 18, 7, tzinfo=UTC)
    consumed = release.visibility.consumer_visible_at + timedelta(minutes=1)
    first = make_access_ledger_entry(
        release=release,
        admission=admitted,
        purpose="p6_forward_cycle_input",
        decision_knowledge_cutoff=cutoff,
        consumed_at=consumed,
    )
    second = make_access_ledger_entry(
        release=release,
        admission=admitted,
        purpose="p6_forward_cycle_input",
        decision_knowledge_cutoff=cutoff,
        consumed_at=consumed,
    )
    assert first.access_id == second.access_id
    assert first.official_evidence is True

    with pytest.raises(ForwardValidationContractError, match="sealed_partition_access_attempt"):
        make_access_ledger_entry(
            release=release,
            admission=admitted,
            purpose="forbidden",
            decision_knowledge_cutoff=cutoff,
            consumed_at=consumed,
            partition="permanent_blind_2025",
        )


def test_correction_is_new_identity_and_never_rewrites_prior_evidence() -> None:
    item = make_correction_disposition(
        prior_package_id="dwd-daily-20260918-a",
        prior_manifest_sha256="sha256:" + "1" * 64,
        correction_package_id="dwd-correction-20260919-b",
        correction_manifest_sha256="sha256:" + "2" * 64,
        affected_evidence_ids=("evidence-1",),
        disposition="censor",
        rationale="corrected input changes the decision-time evidence closure",
        recorded_at=datetime(2026, 9, 19, 12, tzinfo=UTC),
    )
    assert item.rewrite_prior_evidence is False
    assert item.disposition == "censor"

    with pytest.raises(ForwardValidationContractError, match="correction_identity_reuse"):
        make_correction_disposition(
            prior_package_id="same",
            prior_manifest_sha256="sha256:" + "1" * 64,
            correction_package_id="same",
            correction_manifest_sha256="sha256:" + "2" * 64,
            affected_evidence_ids=("evidence-1",),
            disposition="preserve",
            rationale="no semantic effect",
            recorded_at=datetime(2026, 9, 19, 12, tzinfo=UTC),
        )
