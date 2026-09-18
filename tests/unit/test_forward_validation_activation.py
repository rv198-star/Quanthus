# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import quanthus.forward_validation.prospective_source as prospective
from quanthus.event_library.contracts import EventIdentity, EventRecord, SplitAssignment
from quanthus.forward_validation.activation import (
    load_activation_decision,
    load_owner_activation_mandate,
    load_prospective_runtime_config,
    make_activation_decision_payload,
)
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.data_boundary import DwdVisibilityReceipt, ProspectiveDwdRelease
from quanthus.forward_validation.prospective_source import (
    P6ProspectiveCandidate,
    P6ProspectiveSourcePolicy,
    P6ProspectiveSplitPolicy,
    load_daily_chain,
)
from quanthus.forward_validation.serialization import digest_value

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
MANDATE = ROOT / "config/forward-validation/activation/p6-owner-activation-mandate.v1.json"
UTC = timezone.utc


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )


def _write_daily_package(
    daily_root: Path,
    *,
    market_date: date,
    package_id: str,
    covered_after: date,
    prior: dict[str, object] | None,
) -> tuple[Path, dict[str, object]]:
    root = daily_root / "packages" / market_date.isoformat() / package_id
    root.mkdir(parents=True)
    files: list[dict[str, object]] = []
    content_digest = "sha256:" + hashlib.sha256(_canonical(files)).hexdigest()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_domain_increment",
        "package_id": package_id,
        "market_date": market_date.isoformat(),
        "covered_after_date": covered_after.isoformat(),
        "code_revision": "a" * 40,
        "base": {
            "package_id": "dwd-base-202607-v2.1",
            "manifest_sha256": "sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b",
            "content_digest": "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9",
        },
        "prior": prior,
        "row_counts": {},
        "files": files,
        "content_digest": content_digest,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    manifest_sha = _sha(manifest_path)
    (root / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return root, {
        "package_id": package_id,
        "market_date": market_date.isoformat(),
        "manifest_sha256": manifest_sha,
        "content_digest": content_digest,
    }


def _release(root: Path, binding: dict[str, object]) -> ProspectiveDwdRelease:
    market_date = date.fromisoformat(str(binding["market_date"]))
    ready = datetime.combine(market_date, datetime.min.time(), tzinfo=UTC)
    visibility_semantic = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": str(binding["package_id"]),
        "market_date": market_date.isoformat(),
        "manifest_sha256": str(binding["manifest_sha256"]),
        "content_digest": str(binding["content_digest"]),
        "ready_observed_at": ready.isoformat(),
        "consumer_visible_at": ready.isoformat(),
        "source_node": "test",
    }
    visibility = DwdVisibilityReceipt(
        package_id=str(binding["package_id"]),
        market_date=market_date,
        manifest_sha256=str(binding["manifest_sha256"]),
        content_digest=str(binding["content_digest"]),
        ready_observed_at=ready,
        consumer_visible_at=ready,
        source_node="test",
        receipt_digest=digest_value(visibility_semantic),
    )
    return ProspectiveDwdRelease(
        package_root=root,
        package_id=visibility.package_id,
        market_date=market_date,
        manifest_sha256=visibility.manifest_sha256,
        content_digest=visibility.content_digest,
        code_revision="a" * 40,
        row_counts={},
        payload_bytes=0,
        visibility=visibility,
    )


def test_prospective_runtime_binds_frozen_predecessors_without_activation() -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    assert runtime.historical_base_through_date == date(2026, 7, 31)
    assert runtime.history_calendar_days == 300
    assert runtime.candidate_history_sessions == 22
    assert runtime.minimum_feature_sessions == 61
    assert runtime.bindings["candidate_rule"].name == "p4-candidate-price-volume-transition.research.v1.json"


def test_activation_decision_is_human_digest_bound_and_paper_only(tmp_path: Path) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    payload = make_activation_decision_payload(
        runtime=runtime,
        mandate=mandate,
        decision_id="p6-activation-test",
        state="enabled",
        decision_at=datetime.fromisoformat("2026-09-18T09:30:00+08:00"),
        deployment_revision="b" * 40,
        disabled_rehearsal_manifest_digest="sha256:" + "d" * 64,
        rollback_drill_digest="sha256:" + "e" * 64,
    )
    path = tmp_path / "activation.json"
    path.write_bytes(_canonical(payload) + b"\n")
    loaded = load_activation_decision(
        path,
        runtime=runtime,
        expected_mandate_digest=mandate.mandate_digest,
    )
    assert loaded.enabled is True
    assert loaded.decision_digest == payload["decision_digest"]
    assert loaded.activation_mandate_digest == mandate.mandate_digest
    assert payload["broker_authorized"] is False
    assert payload["live_capital_authorized"] is False
    assert payload["permanent_blind_2025_access_authorized"] is False


def test_prospective_source_uses_2026_context_but_only_current_target_can_signal() -> None:
    source = P6ProspectiveSourcePolicy(
        activation_market_date=date(2026, 9, 18),
        signal_market_date=date(2027, 1, 5),
        history_start=date(2026, 3, 11),
        semantic_digest="sha256:" + "1" * 64,
    )
    source.require_scan_date(date(2026, 3, 11))
    source.require_scan_date(date(2027, 1, 5))
    assert source.candidate_signal_date_allowed(date(2026, 12, 31)) is False
    assert source.candidate_signal_date_allowed(date(2027, 1, 5)) is True
    with pytest.raises(ForwardValidationContractError, match="sealed_partition_access_attempt"):
        source.require_scan_date(date(2025, 12, 31))


def test_prospective_split_never_relabels_historical_partitions() -> None:
    split = P6ProspectiveSplitPolicy(
        activation_market_date=date(2026, 9, 18),
        signal_market_date=date(2026, 9, 21),
    )
    calendar = (date(2026, 8, 20), date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22))
    assert split.assign_event_split(
        signal_date=date(2026, 9, 21),
        feature_start=date(2026, 8, 20),
        outcome_end=date(2026, 9, 22),
        trading_dates=calendar,
    ) is SplitAssignment.PROSPECTIVE
    with pytest.raises(ForwardValidationContractError, match="prospective_signal_before_activation"):
        split.assign_event_split(
            signal_date=date(2026, 9, 18),
            feature_start=date(2026, 8, 20),
            outcome_end=date(2026, 9, 21),
            trading_dates=calendar,
        )
    with pytest.raises(ForwardValidationContractError, match="sealed_partition_access_attempt"):
        split.assign_event_split(
            signal_date=date(2026, 9, 21),
            feature_start=date(2025, 12, 31),
            outcome_end=date(2026, 9, 22),
            trading_dates=(date(2025, 12, 31), *calendar),
        )


def test_published_prospective_candidate_never_persists_synthetic_entry_date() -> None:
    signal_date = date(2026, 9, 21)
    identity = EventIdentity(
        security_id="security_test",
        signal_date=signal_date,
        candidate_rule_id="p4-event.price-volume-state-transition",
        candidate_rule_version="1.0.0",
        snapshot_id="p6-snapshot-test",
    )
    event = EventRecord(
        identity=identity,
        candidate_rule_digest="sha256:" + "1" * 64,
        snapshot_manifest_digest="sha256:" + "2" * 64,
        knowledge_cutoff=datetime(2026, 9, 21, 7, tzinfo=UTC),
        signal_close=datetime(2026, 9, 21, 7, tzinfo=UTC),
        eligibility_evidence_digest="sha256:" + "3" * 64,
        feature_window_manifest_digest="sha256:" + "4" * 64,
        isolation_group_id="isolation:test",
        split_policy_id="p6-prospective-activation-split",
        split_policy_version="1.0.0",
        split_policy_digest="sha256:" + "5" * 64,
        split_assignment=SplitAssignment.PROSPECTIVE,
    )
    candidate = P6ProspectiveCandidate(
        record=event,
        listing_id="listing_test",
        listing_code="000001",
        company_id="company_test",
        static_level1_industry_id="industry_test",
        feature_start=date(2026, 8, 20),
        feature_evidence={"signal_date": signal_date.isoformat()},
    )
    payload = candidate.to_dict()
    assert payload["entry_state"] == "pending_next_ready_session"
    assert payload["entry_date"] is None
    restored = P6ProspectiveCandidate.from_dict(payload)
    assert restored.record.event_id == candidate.record.event_id
    tampered = {**payload, "entry_date": "2026-09-22"}
    with pytest.raises(ForwardValidationContractError, match="prospective_candidate_invalid"):
        P6ProspectiveCandidate.from_dict(tampered)


def test_daily_chain_must_bind_exact_prior_manifest_back_to_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    daily_root = tmp_path / "daily"
    runtime = replace(runtime, daily_root=daily_root)
    monkeypatch.setattr(prospective, "_verify_seed", lambda _config: None)

    first_root, first = _write_daily_package(
        daily_root,
        market_date=date(2026, 8, 3),
        package_id="dwd-daily-20260803-a",
        covered_after=date(2026, 7, 31),
        prior=None,
    )
    second_root, second = _write_daily_package(
        daily_root,
        market_date=date(2026, 8, 4),
        package_id="dwd-daily-20260804-b",
        covered_after=date(2026, 8, 3),
        prior={
            "package_id": first["package_id"],
            "market_date": first["market_date"],
            "manifest_sha256": first["manifest_sha256"],
        },
    )
    chain = load_daily_chain(config=runtime, target=_release(second_root, second))
    assert [item.package_id for item in chain] == [first["package_id"], second["package_id"]]
    assert chain[0].root == first_root.resolve()

    manifest_path = second_root / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(raw["prior"], dict)
    raw["prior"]["manifest_sha256"] = "sha256:" + "f" * 64
    manifest_path.write_bytes(_canonical(raw) + b"\n")
    (second_root / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")
    broken = _release(
        second_root,
        {
            **second,
            "manifest_sha256": _sha(manifest_path),
            "content_digest": raw["content_digest"],
        },
    )
    with pytest.raises(ForwardValidationContractError, match="prospective_daily_chain_gap"):
        load_daily_chain(config=runtime, target=broken)
