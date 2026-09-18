# ruff: noqa: E501
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import quanthus.forward_validation.official_cycle as official
from quanthus.contracts import SnapshotRef
from quanthus.forward_validation.activation import (
    P6ProspectiveRuntimeConfig,
    activation_decision_from_mapping,
    load_owner_activation_mandate,
    load_prospective_runtime_config,
    make_activation_decision_payload,
)
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.data_boundary import DwdVisibilityReceipt, ProspectiveDwdRelease
from quanthus.forward_validation.official_cycle import OfficialCycleJobInput, run_official_cycle
from quanthus.forward_validation.paper_ledger import initial_paper_ledger, load_paper_policy
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
MANDATE = ROOT / "config/forward-validation/activation/p6-owner-activation-mandate.v1.json"
UTC = timezone.utc


def _visibility() -> dict[str, object]:
    semantic = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": "dwd-daily-20260921-test",
        "market_date": "2026-09-21",
        "manifest_sha256": "sha256:" + "1" * 64,
        "content_digest": "sha256:" + "2" * 64,
        "ready_observed_at": "2026-09-21T12:00:00+00:00",
        "consumer_visible_at": "2026-09-21T12:01:00+00:00",
        "source_node": "test",
    }
    return {**semantic, "receipt_digest": digest_value(semantic)}


def _activation_payload(runtime: P6ProspectiveRuntimeConfig) -> dict[str, object]:
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    return make_activation_decision_payload(
        runtime=runtime,
        mandate=mandate,
        decision_id="p6-activation-test",
        state="enabled",
        decision_at=datetime.fromisoformat("2026-09-18T09:00:00+08:00"),
        deployment_revision="a" * 40,
        disabled_rehearsal_manifest_digest="sha256:" + "4" * 64,
        rollback_drill_digest="sha256:" + "5" * 64,
    )


def _input(runtime: P6ProspectiveRuntimeConfig) -> OfficialCycleJobInput:
    return OfficialCycleJobInput.from_dict(
        {
            "schema_version": "quanthus.p6-forward-cycle-input.v1",
            "mode": "official_cycle",
            "cycle_id": "p6-cycle-20260921",
            "entry_proposal_digest": "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18",
            "runtime_sha256": runtime.config_sha256,
            "activation_decision": _activation_payload(runtime),
            "market_date": "2026-09-21",
            "package_id": "dwd-daily-20260921-test",
            "manifest_sha256": "sha256:" + "1" * 64,
            "content_digest": "sha256:" + "2" * 64,
            "visibility_receipt": _visibility(),
            "consumed_at": "2026-09-21T12:02:00+00:00",
            "prior_run_id": None,
            "prior_run_manifest_digest": None,
        }
    )


def _release(tmp_path: Path) -> ProspectiveDwdRelease:
    receipt_payload = _visibility()
    receipt = DwdVisibilityReceipt(
        package_id="dwd-daily-20260921-test",
        market_date=date(2026, 9, 21),
        manifest_sha256="sha256:" + "1" * 64,
        content_digest="sha256:" + "2" * 64,
        ready_observed_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
        consumer_visible_at=datetime(2026, 9, 21, 12, 1, tzinfo=UTC),
        source_node="test",
        receipt_digest=str(receipt_payload["receipt_digest"]),
    )
    return ProspectiveDwdRelease(
        package_root=tmp_path / "daily-package",
        package_id=receipt.package_id,
        market_date=receipt.market_date,
        manifest_sha256=receipt.manifest_sha256,
        content_digest=receipt.content_digest,
        code_revision="a" * 40,
        row_counts={"dwd_security_bar": 1},
        payload_bytes=1,
        visibility=receipt,
    )


def _activation(runtime: P6ProspectiveRuntimeConfig):
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    return activation_decision_from_mapping(
        _activation_payload(runtime),
        runtime=runtime,
        expected_mandate_digest=mandate.mandate_digest,
    )


def test_official_input_digest_is_stable_and_visibility_bound() -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    first = _input(runtime)
    second = OfficialCycleJobInput.from_dict(first.to_dict())
    assert first.input_digest == second.input_digest
    assert first.idempotency_key == second.idempotency_key
    assert first.concurrency_key == "p6-forward-cycle:official"


def test_first_official_cycle_seals_paper_only_authority_and_reuses_exact_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    job_input = _input(runtime)
    release = _release(tmp_path)
    activation = _activation(runtime)

    def fake_projection(*, config, activation, target, output_root, collect_candidates=True):
        del config, activation, target, collect_candidates
        output_root.mkdir(parents=True)
        manifest = {
            "schema_version": "quanthus.p6-prospective-projection.v1",
            "content_digest": "sha256:" + "7" * 64,
        }
        manifest_path = output_root / "manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
        digest = official._sha(manifest_path)
        return SimpleNamespace(
            root=output_root,
            manifest_path=manifest_path,
            manifest_sha256=digest,
            content_digest=str(manifest["content_digest"]),
            snapshot_id="p6-test-snapshot",
            signal_market_date=date(2026, 9, 21),
            prior_market_date=date(2026, 9, 18),
            prior_package_id="dwd-daily-20260918-test",
            prior_manifest_sha256="sha256:" + "8" * 64,
            trading_dates=(date(2026, 9, 18), date(2026, 9, 21)),
            bundle=SimpleNamespace(
                snapshot=SnapshotRef(
                    date(2026, 9, 21),
                    datetime(2026, 9, 21, 7, tzinfo=UTC),
                    release.identity_digest,
                    "p6-test-snapshot",
                )
            ),
        )

    paper_policy = load_paper_policy(runtime.bindings["paper_lane"], root=ROOT)
    ledger = initial_paper_ledger(paper_policy)
    monkeypatch.setattr(official, "_activation", lambda *_args, **_kwargs: activation)
    monkeypatch.setattr(official, "_release", lambda *_args, **_kwargs: release)
    monkeypatch.setattr(official, "build_prospective_projection", fake_projection)
    monkeypatch.setattr(official, "_settle_forecasts", lambda **_kwargs: ([], []))
    monkeypatch.setattr(
        official,
        "_settle_prior_intents",
        lambda **_kwargs: (ledger, (), {}, {}, []),
    )
    monkeypatch.setattr(
        official,
        "_review_held_positions",
        lambda **_kwargs: (ledger, {}, [], [], {}),
    )
    monkeypatch.setattr(official, "_build_entry_decisions", lambda **_kwargs: ([], []))
    monkeypatch.setattr(
        official,
        "_paper_and_shadow",
        lambda **_kwargs: (ledger, [], [], [], {}),
    )
    monkeypatch.setattr(official, "_scorecards", lambda **_kwargs: [])

    run_root = tmp_path / "runs"
    first = run_official_cycle(
        job_input=job_input,
        runtime=runtime,
        staging=tmp_path / "staging-a",
        run_root=run_root,
    )
    second = run_official_cycle(
        job_input=job_input,
        runtime=runtime,
        staging=tmp_path / "staging-b",
        run_root=run_root,
    )
    assert first.run_id == second.run_id
    assert first.manifest_digest == second.manifest_digest
    assert first.summary["mode"] == "official_cycle"
    assert first.summary["official_evidence"] is True
    assert first.summary["shared_projection_build_count"] == 1
    assert first.summary["notification_count"] == 0
    assert first.summary["broker_action_count"] == 0
    assert first.summary["live_capital_action_count"] == 0
    artifact = __import__("json").loads((first.root / "official.json").read_text(encoding="utf-8"))
    assert artifact["authority"] == {
        "official_prospective_evidence": True,
        "paper_only": True,
        "notification_authorized": False,
        "broker_authorized": False,
        "live_capital_authorized": False,
        "arbitration_2022_2024_access": False,
        "permanent_blind_2025_access": False,
    }


def test_prospective_calendar_binds_direct_dwd_successor_without_future_calendar() -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    activation = _activation(runtime)
    initial = official._initial_forward_state(runtime)
    first_input = SimpleNamespace(
        market_date=date(2026, 9, 21),
        consumed_at=datetime(2026, 9, 21, 12, 2, tzinfo=UTC),
    )
    first_projection = SimpleNamespace(
        prior_market_date=date(2026, 9, 18),
        prior_package_id="dwd-daily-20260918-test",
        prior_manifest_sha256="sha256:" + "8" * 64,
    )
    official._enforce_prospective_calendar(
        job_input=first_input,
        activation=activation,
        prior=initial,
        projection=first_projection,
    )

    prior = replace(
        initial,
        market_date=date(2026, 9, 21),
        release_package_id="dwd-daily-20260921-test",
        release_manifest_sha256="sha256:" + "1" * 64,
        collection_session_count=1,
    )
    next_input = SimpleNamespace(
        market_date=date(2026, 9, 22),
        consumed_at=datetime(2026, 9, 22, 12, 2, tzinfo=UTC),
    )
    next_projection = SimpleNamespace(
        prior_market_date=date(2026, 9, 21),
        prior_package_id="dwd-daily-20260921-test",
        prior_manifest_sha256="sha256:" + "1" * 64,
    )
    official._enforce_prospective_calendar(
        job_input=next_input,
        activation=activation,
        prior=prior,
        projection=next_projection,
    )

    wrong_projection = SimpleNamespace(
        prior_market_date=date(2026, 9, 21),
        prior_package_id="another-package",
        prior_manifest_sha256="sha256:" + "1" * 64,
    )
    with pytest.raises(ForwardValidationContractError, match="official_cycle_session_gap"):
        official._enforce_prospective_calendar(
            job_input=next_input,
            activation=activation,
            prior=prior,
            projection=wrong_projection,
        )


def test_prospective_cycle_seal_deadline_is_conservative_next_calendar_morning() -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    activation = _activation(runtime)
    initial = official._initial_forward_state(runtime)
    projection = SimpleNamespace(
        prior_market_date=date(2026, 9, 18),
        prior_package_id="dwd-daily-20260918-test",
        prior_manifest_sha256="sha256:" + "8" * 64,
    )
    late = SimpleNamespace(
        market_date=date(2026, 9, 21),
        consumed_at=datetime(2026, 9, 22, 1, 30, tzinfo=UTC),
    )
    with pytest.raises(ForwardValidationContractError, match="historical_backfill_as_official"):
        official._enforce_prospective_calendar(
            job_input=late,
            activation=activation,
            prior=initial,
            projection=projection,
        )
