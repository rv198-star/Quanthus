"""Synthetic receipt-integrity tests; these do not qualify a legacy lane runtime."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines import dual_lane
from quanthus.pipelines.contracts import publish_ods_release


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    source = tmp_path / "synthetic-source.txt"
    source.write_text("synthetic recovery fixture\n", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write(
        spec,
        {
            "schema_version": 1,
            "release_id": "synthetic-release",
            "schedule": "daily",
            "market_date": now.date().isoformat(),
            "created_at": now.isoformat(),
            "producer": "synthetic-test",
            "artifacts": [
                {
                    "name": "synthetic",
                    "source_path": str(source),
                    "relative_path": "artifacts/synthetic.txt",
                    "source_revision": "synthetic-v1",
                    "knowledge_cutoff": now.isoformat(),
                }
            ],
        },
    )
    release_path = tmp_path / "ods-release"
    release = publish_ods_release(spec_path=spec, output=release_path)
    candidate = SimpleNamespace(
        root=tmp_path / "candidate",
        market_date=date(2026, 7, 24),
        ods_manifest_sha256=release.manifest_sha256,
        manifest_sha256=f"sha256:{'a' * 64}",
    )
    # Isolate receipt assessment from the frozen legacy DWD builder/runtime.
    # The ODS package and its READY/member hashes are verified without mocks.
    monkeypatch.setattr(dual_lane, "verify_lane_candidate", lambda _: candidate)
    policy = SimpleNamespace(
        timezone="Asia/Shanghai",
        correction_horizon_days=90,
        recovery_minimum_copies=2,
        delete_enabled=False,
    )
    root = tmp_path / "governance"
    receipts = []
    for copy_id, location_class, failure_domain in (
        ("source-copy", "source_lane", "source-node"),
        ("recovery-copy", "independent_recovery", "recovery-store"),
    ):
        copy_path = tmp_path / copy_id
        shutil.copytree(release_path, copy_path)
        receipts.append(
            dual_lane.record_accepted_ods_copy(
                root=root,
                policy=policy,
                accepted_candidate_path=candidate.root,
                ods_release_copy_path=copy_path,
                copy_id=copy_id,
                location_class=location_class,
                failure_domain=failure_domain,
                retention_until=now + timedelta(days=100),
                clock=lambda: now,
            )
        )
    return SimpleNamespace(
        root=root,
        policy=policy,
        candidate=candidate,
        now=now,
        receipts=receipts,
        package_root=receipts[0].parent.parent,
    )


def _assess(recovery: SimpleNamespace):
    return dual_lane.assess_accepted_ods_recovery(
        root=recovery.root,
        policy=recovery.policy,
        accepted_candidate_path=recovery.candidate.root,
        clock=lambda: recovery.now,
    )


def _index_path(receipt: Path) -> Path:
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    return receipt.parent.parent / "copy-index" / f"{digest}.json"


def test_verified_copy_indexes_pass_idempotently_without_authorizing_purge(recovery) -> None:
    first = _assess(recovery)
    assert first.passed
    before = first.path.read_bytes()
    assert _assess(recovery) == first
    assert first.path.read_bytes() == before
    purge = dual_lane.record_accepted_ods_purge_gate(
        root=recovery.root,
        policy=recovery.policy,
        accepted_candidate_path=recovery.candidate.root,
        actor="synthetic-auditor",
        clock=lambda: recovery.now,
    )
    assert json.loads(purge.read_text())["eligible"] is False


@pytest.mark.parametrize("field", ["failure_domain", "retention_until", "copy_id"])
def test_changed_receipt_cannot_reuse_its_original_index(recovery, field) -> None:
    receipt = recovery.receipts[0]
    value = json.loads(receipt.read_text())
    value[field] = (
        (recovery.now + timedelta(days=200)).isoformat()
        if field == "retention_until"
        else "changed-identity"
    )
    _write(receipt, value)
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*index"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


def test_missing_copy_index_fails_closed(recovery) -> None:
    _index_path(recovery.receipts[0]).unlink()
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*index"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


def test_missing_all_copy_indexes_fails_closed(recovery) -> None:
    shutil.rmtree(recovery.package_root / "copy-index")
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*index"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


def test_orphan_copy_index_cannot_be_ignored_when_other_copies_pass(recovery) -> None:
    index = _index_path(recovery.receipts[0])
    shutil.copyfile(index, index.with_name(f"{'f' * 64}.json"))
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*index"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


def test_missing_receipt_with_retained_index_fails_closed(recovery) -> None:
    recovery.receipts[0].unlink()
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*index"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


def test_renamed_receipt_cannot_rebind_its_copy_id_through_index_path(recovery) -> None:
    receipt = recovery.receipts[0]
    index = _index_path(receipt)
    renamed = receipt.with_name("renamed-copy.json")
    receipt.rename(renamed)
    payload = json.loads(index.read_text())
    payload["receipt_path"] = renamed.relative_to(recovery.root).as_posix()
    _write(index, payload)
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*index"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


@pytest.mark.parametrize("payload", [bytes([255]), b"{", b"[]", b"null"])
def test_malformed_index_raises_domain_error_without_assessment(recovery, payload) -> None:
    _index_path(recovery.receipts[0]).write_bytes(payload)
    with pytest.raises(dual_lane.DualLaneError):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("kind", "unrelated_record"),
        ("copy_id", "different-copy"),
        ("receipt_sha256", f"sha256:{'f' * 64}"),
        ("receipt_path", "../../outside.json"),
        ("receipt_path", "/tmp/outside.json"),
        ("reason_code", "unverified"),
    ],
)
def test_copy_index_fields_are_bound_to_exact_receipt(recovery, field, value) -> None:
    index = _index_path(recovery.receipts[0])
    payload = json.loads(index.read_text())
    payload[field] = value
    _write(index, payload)
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*index"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


@pytest.mark.parametrize(
    "target",
    ["receipt", "index", "copies", "copy-index", "package", "accepted-ods", "recovery"],
)
def test_recovery_evidence_symlinks_fail_closed(recovery, target) -> None:
    paths = {
        "receipt": recovery.receipts[0],
        "index": _index_path(recovery.receipts[0]),
        "copies": recovery.package_root / "copies",
        "copy-index": recovery.package_root / "copy-index",
        "package": recovery.package_root,
        "accepted-ods": recovery.root / "recovery" / "accepted-ods",
        "recovery": recovery.root / "recovery",
    }
    path = paths[target]
    moved = recovery.root.parent / f"moved-{target}"
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(dual_lane.DualLaneError, match="recovery.*symbolic"):
        _assess(recovery)
    assert not (recovery.package_root / "assessments").exists()


def test_expired_indexed_receipt_still_fails_closed(recovery) -> None:
    recovery.now += timedelta(days=101)
    with pytest.raises(dual_lane.DualLaneError, match="invalid or expired"):
        _assess(recovery)


def test_no_recorded_copies_is_insufficient_without_creating_copy_evidence(recovery) -> None:
    shutil.rmtree(recovery.package_root)
    result = _assess(recovery)
    assert not result.passed
    assert result.failure_domains == ()
    assert not (recovery.package_root / "copies").exists()
    assert not (recovery.package_root / "copy-index").exists()
