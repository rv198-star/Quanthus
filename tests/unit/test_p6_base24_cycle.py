from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from quanthus.forward_validation import base24_cycle as cycle
from quanthus.forward_validation.activation import (
    activation_decision_from_mapping,
    load_prospective_runtime_config,
)
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.data_boundary import load_visibility_receipt
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from tests.unit.test_forward_validation_official_cycle import _activation
from tests.unit.test_p6_base24_runtime import (
    build_runtime,
)
from tests.unit.test_p6_base24_runtime import (
    runtime_source as runtime_source,
)
from tests.unit.test_p6_base24_runtime import (
    view as view,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/forward-validation/activation/p6-base24-cycle-runtime.v1.json"


def runtime():
    return cycle.load_runtime(CONFIG, root=ROOT)


def activation():
    old = load_prospective_runtime_config(
        ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json", root=ROOT
    )
    value = replace(
        _activation(old),
        runtime_sha256=runtime().config_sha256,
        decision_at=datetime.fromisoformat("2026-09-29T12:00:00+08:00"),
        activation_market_date=date(2026, 9, 29),
    )
    payload = value.semantic_payload()
    return activation_decision_from_mapping(
        {**payload, "decision_digest": digest_value(payload)}, runtime=runtime()
    )


def seal(root, m):
    m["content_digest"] = digest_value({k: v for k, v in m.items() if k != "content_digest"})
    (root / "manifest.json").write_bytes(canonical_json_bytes(m) + b"\n")
    (root / "READY").write_text(cycle._sha(root / "manifest.json") + "\n")
    receipt = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": m["package_id"],
        "market_date": m["market_date"],
        "manifest_sha256": cycle._sha(root / "manifest.json"),
        "content_digest": m["content_digest"],
        "ready_observed_at": "2026-10-01T00:00:02+00:00",
        "consumer_visible_at": "2026-10-01T00:00:03+00:00",
        "source_node": "synthetic",
    }
    return load_visibility_receipt({**receipt, "receipt_digest": digest_value(receipt)})


@pytest.fixture
def release(runtime_source, tmp_path):
    inner, receipt = build_runtime(runtime_source, tmp_path)
    root = tmp_path / "package"
    root.mkdir()
    shutil.move(inner, root / "input")
    m = {
        "schema_version": cycle.RELEASE_SCHEMA,
        "package_id": "p6-base24-20260930-test",
        "market_date": "2026-09-30",
        "code_revision": "a" * 40,
        "input_manifest_sha256": receipt["manifest_sha256"],
        "built_at": "2026-10-01T00:00:02+00:00",
        "prior": {"market_date": "2026-09-29", "package_id": None, "manifest_sha256": None},
    }
    return root, m, seal(root, m)


def test_successor_binds_frozen_policies_without_legacy_source_paths(monkeypatch):
    monkeypatch.setenv("QUANTHUS_P6_BASE24_DAILY_ROOT", "/nonexistent/new-catalog")
    r = runtime()
    assert len(r.bindings) == 11
    assert r.daily_root == Path("/nonexistent/new-catalog")
    assert not hasattr(r, "historical_base_root") and not hasattr(r, "correction_root")
    assert cycle.load_cycle_runtime(CONFIG, root=ROOT) == r
    legacy = ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
    assert cycle.load_cycle_runtime(legacy, root=ROOT) == load_prospective_runtime_config(
        legacy, root=ROOT
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("clock_version", "legacy-close"),
        ("source_scope", "whole_market"),
        ("authority", {"official_execution_authorized": True}),
        ("extra", True),
    ],
)
def test_runtime_rejects_unreviewed_scope_clock_or_authority(tmp_path, field, value):
    raw = json.loads(CONFIG.read_text())
    raw[field] = value
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ForwardValidationContractError, match="runtime_drift"):
        cycle.load_runtime(path, root=ROOT)


@pytest.mark.parametrize(
    "mutation", ["wrong_prior", "partial_prior", "extra", "corrupt", "visibility"]
)
def test_repacked_or_physical_release_failure(release, mutation):
    root, m, receipt = release
    if mutation == "wrong_prior":
        m["prior"]["market_date"] = "2026-09-28"
    elif mutation == "partial_prior":
        m["prior"]["package_id"] = "unbound"
    elif mutation == "extra":
        (root / "unexpected").write_text("")
    elif mutation == "corrupt":
        (root / "input/candidate-input.parquet").write_bytes(b"bad")
    else:
        m["market_date"] = "2026-10-01"
    changed = seal(root, m)
    with pytest.raises((ForwardValidationContractError, ValueError)):
        cycle.verify_release(root, changed)


def test_real_successor_projection_uses_one_panel_real_clock_and_offline_input(release, tmp_path):
    root, _, receipt = release
    target = cycle.verify_release(root, receipt)
    decision = datetime.fromisoformat("2026-10-01T08:01:00+08:00")
    p = cycle.build_projection(
        config=runtime(),
        activation=activation(),
        target=target,
        output_root=tmp_path / "projection",
        decision_at=decision,
    )
    assert p.bundle.snapshot.knowledge_cutoff == decision
    assert p.prior_market_date == date(2026, 9, 29)
    m = json.loads(p.manifest_path.read_text())
    assert m["decision_clock"]["schema_version"] == cycle.CLOCK_VERSION
    assert m["candidate_scan"]["scanner_internal_t_plus_1_sentinel_persisted"] is False
    # The sealed projection contains independent input files, not old source paths.
    shutil.rmtree(root)
    assert p.bundle.partition_paths("daily_ohlcv_none")[0].is_file()
    assert not p.bundle.execution_authorized
    assert (p.root / "READY").read_text().strip() == cycle._sha(p.manifest_path)


@pytest.mark.parametrize(
    "decision,reason",
    [
        ("2026-10-01T07:59:59+08:00", "not_yet_visible"),
        ("2026-10-01T09:00:00+08:00", "missed_next_open"),
    ],
)
def test_projection_rejects_future_visibility_or_late_historical_replay(
    release, tmp_path, decision, reason
):
    root, _, receipt = release
    with pytest.raises(ForwardValidationContractError, match=reason):
        cycle.build_projection(
            config=runtime(),
            activation=activation(),
            target=cycle.verify_release(root, receipt),
            output_root=tmp_path / "projection",
            decision_at=datetime.fromisoformat(decision),
        )
    assert not (tmp_path / "projection").exists()


def test_no_current_or_earlier_session_becomes_official(release, tmp_path):
    root, _, receipt = release
    act = replace(activation(), activation_market_date=date(2026, 9, 30))
    with pytest.raises(ForwardValidationContractError, match="strictly_after_activation"):
        cycle.build_projection(
            config=runtime(),
            activation=act,
            target=cycle.verify_release(root, receipt),
            output_root=tmp_path / "projection",
            decision_at=datetime.fromisoformat("2026-10-01T08:01:00+08:00"),
        )
    assert not (tmp_path / "projection").exists()


def test_complete_paper_cycle_and_independent_reproduction_use_successor_adapter(
    release, tmp_path, monkeypatch
):
    from quanthus.forward_validation.official_cycle import OfficialCycleJobInput, run_official_cycle
    from quanthus.forward_validation.reproduction import (
        OfficialCycleReproductionJobInput,
        run_official_reproduction,
    )

    monkeypatch.delenv("QUANTHUS_CODE_REVISION", raising=False)
    root, m, receipt = release
    r = replace(runtime(), daily_root=tmp_path / "catalog")
    package = r.daily_root / "packages" / m["market_date"] / m["package_id"]
    package.parent.mkdir(parents=True)
    shutil.move(root, package)
    act = activation()
    job = OfficialCycleJobInput(
        "synthetic-base24-first",
        r.config_sha256,
        {**act.semantic_payload(), "decision_digest": act.decision_digest},
        receipt.market_date,
        receipt.package_id,
        receipt.manifest_sha256,
        receipt.content_digest,
        {**receipt.semantic_payload, "receipt_digest": receipt.receipt_digest},
        datetime.fromisoformat("2026-10-01T08:01:00+08:00"),
    )
    first = run_official_cycle(
        job_input=job, runtime=r, staging=tmp_path / "build", run_root=tmp_path / "runs"
    )
    assert first.summary["collection_session_count"] == 1
    assert first.summary["shared_projection_build_count"] == 1
    for name in ("notification_count", "broker_action_count", "live_capital_action_count"):
        assert first.summary[name] == 0
    artifact = json.loads((first.root / "official.json").read_text())
    assert artifact["access_ledger"]["purpose"] == "p6_base24_after_close_cycle_input"
    assert (
        datetime.fromisoformat(artifact["access_ledger"]["decision_knowledge_cutoff"])
        == job.consumed_at
    )
    again = run_official_cycle(
        job_input=job, runtime=r, staging=tmp_path / "unused", run_root=tmp_path / "runs"
    )
    assert again.manifest_digest == first.manifest_digest
    assert not (tmp_path / "unused").exists()
    repro = run_official_reproduction(
        job_input=OfficialCycleReproductionJobInput(job, first.run_id, first.manifest_digest),
        runtime=r,
        staging=tmp_path / "independent",
        primary_run_root=tmp_path / "runs",
        reproduction_root=tmp_path / "reproductions",
    )
    assert repro.summary["semantic_match"] is True
    assert repro.summary["official_evidence"] is False


def test_publication_validates_prior_before_writing_and_preserves_inputs(release, tmp_path):
    root, m, receipt = release
    inputs = cycle.verify_release(root, receipt).inputs
    wrong = {**m["prior"], "market_date": "2026-09-28"}
    with pytest.raises(ForwardValidationContractError, match="session_gap"):
        cycle.seal_release(
            inputs=inputs,
            output=tmp_path / "bad-publish",
            package_id="new",
            code_revision="a" * 40,
            prior=wrong,
        )
    assert not (tmp_path / "bad-publish").exists()
    inputs.check_unchanged()
    out = tmp_path / "publish"
    sealed = cycle.seal_release(
        inputs=inputs,
        output=out,
        package_id="new",
        code_revision="a" * 40,
        prior=m["prior"],
    )
    assert (out / "READY").read_text().strip() == sealed["manifest_sha256"]
    before = (out / "manifest.json").read_bytes()
    with pytest.raises(ForwardValidationContractError, match="output_exists"):
        cycle.seal_release(
            inputs=inputs,
            output=out,
            package_id="new",
            code_revision="a" * 40,
            prior=m["prior"],
        )
    assert (out / "manifest.json").read_bytes() == before
    inputs.check_unchanged()
