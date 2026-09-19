from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import quanthus.forward_validation.reproduction as reproduction
from quanthus.forward_validation.activation import load_prospective_runtime_config
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.official_cycle import OfficialCycleJobInput
from quanthus.forward_validation.runner import verify_forward_run
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.platform.jobs import p6_forward_submission

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json"


def _sha(path: Path) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _source_input(runtime_sha256: str) -> OfficialCycleJobInput:
    visibility = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": "dwd-daily-20260921-test",
        "market_date": "2026-09-21",
        "manifest_sha256": "sha256:" + "1" * 64,
        "content_digest": "sha256:" + "2" * 64,
        "ready_observed_at": "2026-09-21T12:00:00+00:00",
        "consumer_visible_at": "2026-09-21T12:01:00+00:00",
        "source_node": "test",
    }
    visibility["receipt_digest"] = digest_value(visibility)
    return OfficialCycleJobInput(
        cycle_id="p6-official-2026-09-21",
        runtime_sha256=runtime_sha256,
        activation_decision={"decision_digest": "sha256:" + "3" * 64},
        market_date=date(2026, 9, 21),
        package_id="dwd-daily-20260921-test",
        manifest_sha256="sha256:" + "1" * 64,
        content_digest="sha256:" + "2" * 64,
        visibility_receipt=visibility,
        consumed_at=datetime(2026, 9, 21, 12, 2, tzinfo=timezone.utc),
    )


def _write_official_run(root: Path, source: OfficialCycleJobInput, *, variant: int = 0):
    run_id = f"p6_run_{source.input_digest[7:31]}"
    run = root / run_id
    projection = run / "projection"
    projection.mkdir(parents=True)
    projection_manifest = {
        "schema_version": "quanthus.p6-prospective-projection.v1",
        "content_digest": "sha256:" + "4" * 64,
    }
    (projection / "manifest.json").write_bytes(
        canonical_json_bytes(projection_manifest) + b"\n"
    )
    projection_sha = _sha(projection / "manifest.json")
    summary = {
        "mode": "official_cycle",
        "official_evidence": True,
        "shared_projection_build_count": 1,
        "notification_count": 0,
        "broker_action_count": 0,
        "live_capital_action_count": 0,
        "candidate_count": variant,
    }
    official = {
        "schema_version": "quanthus.p6-official-cycle.v1",
        "input": source.to_dict(),
        "projection": {
            "artifact_ref": "projection",
            "manifest_sha256": projection_sha,
        },
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
    (run / "official.json").write_bytes(canonical_json_bytes(official) + b"\n")
    manifest = {
        "schema_version": "quanthus.p6-forward-run.v1",
        "run_id": run_id,
        "input_digest": source.input_digest,
        "mode": "official_cycle",
        "official_sha256": _sha(run / "official.json"),
        "projection_manifest_sha256": projection_sha,
        "summary": summary,
    }
    (run / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    (run / "READY").write_text(_sha(run / "manifest.json") + "\n", encoding="ascii")
    return verify_forward_run(run, expected_input_digest=source.input_digest)


def test_reproduction_submission_is_same_kind_but_never_official_evidence(tmp_path: Path) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    source = _source_input(runtime.config_sha256)
    primary = _write_official_run(tmp_path / "primary", source)
    value = reproduction.OfficialCycleReproductionJobInput(
        source_input=source,
        primary_run_id=primary.run_id,
        primary_manifest_digest=primary.manifest_digest,
    )

    canonical, idempotency, concurrency = p6_forward_submission(value.to_dict())

    assert canonical == value.to_dict()
    assert idempotency == value.idempotency_key
    assert concurrency == "p6-forward-cycle:official-reproduction"


def test_independent_reproduction_seals_non_evidence_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    source = _source_input(runtime.config_sha256)
    primary_root = tmp_path / "primary"
    primary = _write_official_run(primary_root, source)
    value = reproduction.OfficialCycleReproductionJobInput(
        source_input=source,
        primary_run_id=primary.run_id,
        primary_manifest_digest=primary.manifest_digest,
    )

    def fake_run_official_cycle(*, job_input, runtime, staging, run_root):
        del runtime, staging
        target = run_root / primary.run_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(primary.root, target)
        return verify_forward_run(target, expected_input_digest=job_input.input_digest)

    monkeypatch.setattr(reproduction, "run_official_cycle", fake_run_official_cycle)
    observed = reproduction.run_official_reproduction(
        job_input=value,
        runtime=runtime,
        staging=tmp_path / "staging",
        primary_run_root=primary_root,
        reproduction_root=tmp_path / "reproductions",
    )

    assert observed.summary["mode"] == "official_reproduction"
    assert observed.summary["official_evidence"] is False
    assert observed.summary["semantic_match"] is True
    assert observed.summary["primary_manifest_digest"] == primary.manifest_digest
    nested = verify_forward_run(observed.root / "reproduced-run")
    assert nested.manifest_digest == primary.manifest_digest


def test_independent_reproduction_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    source = _source_input(runtime.config_sha256)
    primary_root = tmp_path / "primary"
    primary = _write_official_run(primary_root, source)
    value = reproduction.OfficialCycleReproductionJobInput(
        source_input=source,
        primary_run_id=primary.run_id,
        primary_manifest_digest=primary.manifest_digest,
    )

    def fake_mismatch(*, job_input, runtime, staging, run_root):
        del runtime, staging
        return _write_official_run(run_root, job_input, variant=1)

    monkeypatch.setattr(reproduction, "run_official_cycle", fake_mismatch)
    with pytest.raises(
        ForwardValidationContractError,
        match="same_release_reproduction_semantic_mismatch",
    ):
        reproduction.run_official_reproduction(
            job_input=value,
            runtime=runtime,
            staging=tmp_path / "staging",
            primary_run_root=primary_root,
            reproduction_root=tmp_path / "reproductions",
        )
