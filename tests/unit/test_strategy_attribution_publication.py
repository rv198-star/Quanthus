import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import quanthus.strategy_attribution_publication as publication_module
from quanthus.strategy_attribution import AttributionError, canonical_bytes, digest
from quanthus.strategy_attribution_demo import (
    SyntheticAttributionExecutor,
    synthetic_run_request,
)
from quanthus.strategy_attribution_pipeline import AttributionPipeline
from quanthus.strategy_attribution_publication import (
    PUBLICATION_CATALOG_SCHEMA,
    AttributionPublisher,
    discover_attribution_releases,
    load_attribution_run_request,
    verify_attribution_release,
)


@pytest.fixture(scope="module")
def evaluated_runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("attribution-publication-runs")
    request = synthetic_run_request()
    first = AttributionPipeline(root / "first").run(
        request, SyntheticAttributionExecutor()
    )
    second = AttributionPipeline(root / "second").run(
        request, SyntheticAttributionExecutor()
    )
    return request, first, second


def _release_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _reseal(release: Path) -> None:
    manifest_path = release / "release.json"
    manifest = json.loads(manifest_path.read_bytes())
    workspace = json.loads((release / "workspace.json").read_bytes())
    manifest["workspace_digest"] = workspace["digest"]
    for record in manifest["semantic_files"]:
        payload = (release / record["path"]).read_bytes()
        record["sha256"] = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        record["size"] = len(payload)
    manifest["digest"] = digest(
        {key: value for key, value in manifest.items() if key != "digest"}
    )
    manifest_path.write_bytes(canonical_bytes(manifest))

    transport_path = release / "transport.json"
    transport = json.loads(transport_path.read_bytes())
    transport["release_digest"] = manifest["digest"]
    transport["digest"] = digest(
        {key: value for key, value in transport.items() if key != "digest"}
    )
    transport_path.write_bytes(canonical_bytes(transport))

    checksummed = {
        path.relative_to(release).as_posix(): path.read_bytes()
        for path in sorted(release.rglob("*"))
        if path.is_file() and path.name not in {"CHECKSUMS.sha256", "READY"}
    }
    checksums = "".join(
        f"{hashlib.sha256(payload).hexdigest()}  {path}\n"
        for path, payload in sorted(checksummed.items())
    ).encode("ascii")
    (release / "CHECKSUMS.sha256").write_bytes(checksums)
    (release / "READY").write_text(
        f"{hashlib.sha256(checksums).hexdigest()}\n", encoding="ascii"
    )


def test_publish_builds_deep_verified_release_and_ready_catalog(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    published = AttributionPublisher(tmp_path).publish(
        request,
        result,
        published_at=datetime(2026, 7, 31, 8, 30, tzinfo=timezone.utc),
    )

    verified = verify_attribution_release(published.root)
    files = _release_files(published.root)
    catalog = json.loads((tmp_path / "catalog.json").read_bytes())

    assert verified.manifest.digest == published.manifest.digest
    assert verified.manifest.synthetic_fixture is True
    assert verified.manifest.formal_strategy_evidence is False
    assert verified.manifest.publishable_claim_digests == ()
    assert len(verified.manifest.display_claim_digests) == 5
    assert verified.workspace["unavailable_component_ids"] == ["allocator.capital"]
    assert "strategy.root" not in verified.workspace["unavailable_component_ids"]
    assert {
        "request.json",
        "plan.json",
        "executor-binding.json",
    }.issubset(
        {
            path.removeprefix("lineage/")
            for path in files
            if path.startswith("lineage/")
        }
    )
    assert any(path.startswith("lineage/work/") for path in files)
    assert any(path.startswith("lineage/raw/") for path in files)
    assert any(path.startswith("lineage/verification/") for path in files)
    assert any(path.startswith("lineage/artifacts/") for path in files)
    assert any(path.startswith("lineage/evidence/") for path in files)
    assert any(path.startswith("lineage/evaluation/claims/") for path in files)
    assert any(path.startswith("lineage/evaluation/kernel-receipts/") for path in files)
    assert any(path.startswith("lineage/receipts/") for path in files)
    assert catalog["schema_version"] == PUBLICATION_CATALOG_SCHEMA
    assert catalog["releases"] == [
        {
            "release_id": published.release_id,
            "release_path": f"./{published.release_id}/release.json",
            "workspace_path": f"./{published.release_id}/workspace.json",
            "checksum_path": f"./{published.release_id}/CHECKSUMS.sha256",
            "ready_path": f"./{published.release_id}/READY",
        }
    ]
    ready_mtime = (published.root / "READY").stat().st_mtime_ns
    assert all(
        ready_mtime >= path.stat().st_mtime_ns
        for path in published.root.rglob("*")
        if path.is_file() and path.name != "READY"
    )


def test_double_run_and_double_publish_have_identical_semantic_bytes(
    tmp_path, evaluated_runs
) -> None:
    request, first_result, second_result = evaluated_runs
    first = AttributionPublisher(tmp_path / "first").publish(
        request,
        first_result,
        published_at=datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc),
    )
    second = AttributionPublisher(tmp_path / "second").publish(
        request,
        second_result,
        published_at=datetime(2026, 7, 31, 9, 5, tzinfo=timezone.utc),
    )

    assert first.release_id == second.release_id
    assert first.manifest.canonical_bytes() == second.manifest.canonical_bytes()
    for record in first.manifest.semantic_files:
        assert (first.root / record.path).read_bytes() == (
            second.root / record.path
        ).read_bytes()
    assert (first.root / "transport.json").read_bytes() != (
        second.root / "transport.json"
    ).read_bytes()
    assert b"published_at" not in (first.root / "release.json").read_bytes()
    assert b"published_at" not in (first.root / "workspace.json").read_bytes()


def test_same_release_is_idempotent_and_different_semantics_conflict(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    publisher = AttributionPublisher(tmp_path)
    first = publisher.publish(
        request,
        result,
        published_at=datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc),
    )
    before = _release_files(first.root)
    mtimes = {
        path: path.stat().st_mtime_ns
        for path in first.root.rglob("*")
        if path.is_file()
    }

    repeated = publisher.publish(
        request,
        result,
        published_at=datetime(2026, 7, 31, 11, 0, tzinfo=timezone.utc),
    )

    assert repeated.transport.published_at == "2026-07-31T10:00:00Z"
    assert _release_files(first.root) == before
    assert all(path.stat().st_mtime_ns == value for path, value in mtimes.items())

    with pytest.raises(AttributionError) as captured:
        publisher.publish(request, result, title="Different semantic title")
    assert captured.value.reason_code == "release_id_conflict"


def test_catalog_filename_is_reserved_from_release_identity(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs

    with pytest.raises(AttributionError) as captured:
        AttributionPublisher(tmp_path).publish(
            request,
            result,
            release_id="catalog.json",
        )
    assert captured.value.reason_code == "invalid_release_id"
    assert not (tmp_path / "catalog.json").exists()


def test_only_complete_evaluated_pipeline_can_publish(tmp_path, evaluated_runs) -> None:
    request, result, _ = evaluated_runs
    copied_root = tmp_path / "runs"
    copied_run = copied_root / result.run_id
    shutil.copytree(result.run_dir, copied_run)
    (copied_run / "receipts" / "evaluated.json").unlink()
    incomplete = replace(result, run_dir=copied_run)

    with pytest.raises(AttributionError):
        AttributionPublisher(tmp_path / "published").publish(request, incomplete)
    assert not (tmp_path / "published" / result.run_id / "READY").exists()


def test_interrupted_staging_is_not_discoverable(
    tmp_path, evaluated_runs, monkeypatch
) -> None:
    request, result, _ = evaluated_runs
    original = publication_module._write_file

    def fail_before_ready(root, relative, payload):
        if relative == "READY":
            raise OSError("injected interruption")
        original(root, relative, payload)

    monkeypatch.setattr(publication_module, "_write_file", fail_before_ready)
    with pytest.raises(OSError, match="injected interruption"):
        AttributionPublisher(tmp_path).publish(request, result)

    assert discover_attribution_releases(tmp_path) == ()
    assert not any(path.name == "READY" for path in tmp_path.rglob("*"))
    assert not any(path.is_dir() for path in tmp_path.iterdir())


def test_checksum_tampering_fails_closed(tmp_path, evaluated_runs) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    workspace = release / "workspace.json"
    workspace.write_bytes(workspace.read_bytes() + b" ")

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "publication_checksum_mismatch"


def test_forged_claim_fails_after_attacker_reseals_outer_envelopes(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    workspace_path = release / "workspace.json"
    workspace = json.loads(workspace_path.read_bytes())
    workspace["claims"][0]["classification"] = "harmful"
    workspace["digest"] = digest(
        {key: value for key, value in workspace.items() if key != "digest"}
    )
    workspace_path.write_bytes(canonical_bytes(workspace))
    _reseal(release)

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "workspace_evaluation_mismatch"


def test_synthetic_source_cannot_be_resealed_as_formal_evidence(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    manifest_path = release / "release.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["synthetic_fixture"] = False
    manifest["formal_strategy_evidence"] = True
    manifest["publishable_claim_digests"] = manifest["display_claim_digests"]
    manifest_path.write_bytes(canonical_bytes(manifest))
    _reseal(release)

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "invalid_publication_source"


def test_synthetic_source_kind_cannot_be_resealed_as_formal_evidence(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    manifest_path = release / "release.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["source_kind"] = "attacker_claimed_formal_strategy_evidence"
    manifest["synthetic_fixture"] = False
    manifest["formal_strategy_evidence"] = True
    manifest["publishable_claim_digests"] = manifest["display_claim_digests"]
    manifest_path.write_bytes(canonical_bytes(manifest))
    workspace_path = release / "workspace.json"
    workspace = json.loads(workspace_path.read_bytes())
    workspace["source_kind"] = manifest["source_kind"]
    workspace["digest"] = digest(
        {key: value for key, value in workspace.items() if key != "digest"}
    )
    workspace_path.write_bytes(canonical_bytes(workspace))
    _reseal(release)

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "publication_source_mismatch"


def test_workspace_schema_drift_fails_after_outer_reseal(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    workspace_path = release / "workspace.json"
    workspace = json.loads(workspace_path.read_bytes())
    workspace["schema"] = "attacker.workspace-semantics.v999"
    workspace["digest"] = digest(
        {key: value for key, value in workspace.items() if key != "digest"}
    )
    workspace_path.write_bytes(canonical_bytes(workspace))
    _reseal(release)

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "workspace_schema_mismatch"


@pytest.mark.parametrize("field,value", (("title", {}), ("subtitle", [])))
def test_workspace_text_metadata_cannot_change_type_after_outer_reseal(
    tmp_path, evaluated_runs, field, value
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    workspace_path = release / "workspace.json"
    workspace = json.loads(workspace_path.read_bytes())
    workspace[field] = value
    workspace["digest"] = digest(
        {key: item for key, item in workspace.items() if key != "digest"}
    )
    workspace_path.write_bytes(canonical_bytes(workspace))
    _reseal(release)

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "invalid_publication"


def test_broken_parent_fails_after_attacker_reseals_outer_envelopes(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    verification_paths = sorted((release / "lineage" / "verification").glob("*.json"))
    target = next(
        path
        for path in verification_paths
        if json.loads(path.read_bytes())["dependency_verification_digests"]
    )
    record = json.loads(target.read_bytes())
    parent_id = next(iter(record["dependency_verification_digests"]))
    record["dependency_verification_digests"][parent_id] = digest("foreign-parent")
    record["digest"] = digest(
        {key: value for key, value in record.items() if key != "digest"}
    )
    target.write_bytes(canonical_bytes(record))
    _reseal(release)

    with pytest.raises(AttributionError):
        verify_attribution_release(release)


def test_context_drift_fails_after_attacker_reseals_outer_envelopes(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    raw_path = sorted((release / "lineage" / "raw").glob("*.json"))[0]
    raw = json.loads(raw_path.read_bytes())
    raw["pit_context_digest"] = digest("foreign-context")
    raw["digest"] = digest(
        {
            key: value
            for key, value in raw.items()
            if key not in {"digest", "result_id"}
        }
    )
    raw["result_id"] = f"raw-execution:{raw['digest'].removeprefix('sha256:')}"
    raw_path.write_bytes(canonical_bytes(raw))
    _reseal(release)

    with pytest.raises(AttributionError):
        verify_attribution_release(release)


def test_workspace_availability_is_deterministically_rebuilt(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    workspace_path = release / "workspace.json"
    workspace = json.loads(workspace_path.read_bytes())
    workspace["unavailable_component_ids"] = ["strategy.root"]
    workspace["digest"] = digest(
        {key: value for key, value in workspace.items() if key != "digest"}
    )
    workspace_path.write_bytes(canonical_bytes(workspace))
    _reseal(release)

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "workspace_availability_mismatch"


def test_workspace_baseline_is_deterministically_rebuilt(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    release = AttributionPublisher(tmp_path).publish(request, result).root
    workspace_path = release / "workspace.json"
    workspace = json.loads(workspace_path.read_bytes())
    alternative = next(
        item
        for item in workspace["experiments"]
        if item["role"] == "baseline"
        and item["digest"] != workspace["baseline"]["digest"]
    )
    workspace["baseline"] = alternative
    workspace["digest"] = digest(
        {key: value for key, value in workspace.items() if key != "digest"}
    )
    workspace_path.write_bytes(canonical_bytes(workspace))
    _reseal(release)

    with pytest.raises(AttributionError) as captured:
        verify_attribution_release(release)
    assert captured.value.reason_code == "workspace_baseline_mismatch"


def test_incomplete_directories_are_absent_from_discovery(
    tmp_path, evaluated_runs
) -> None:
    request, result, _ = evaluated_runs
    published = AttributionPublisher(tmp_path).publish(request, result)
    incomplete = tmp_path / "incomplete-release"
    incomplete.mkdir()
    (incomplete / "workspace.json").write_text("{}", encoding="ascii")
    hidden = tmp_path / ".interrupted.staging"
    hidden.mkdir()
    (hidden / "READY").write_text("not-ready", encoding="ascii")

    assert tuple(item.release_id for item in discover_attribution_releases(tmp_path)) == (
        published.release_id,
    )


def test_embedded_request_round_trips_and_fake_digest_is_rejected(
    evaluated_runs,
) -> None:
    request, _, _ = evaluated_runs
    restored = load_attribution_run_request(request.canonical_bytes())
    assert restored.canonical_bytes() == request.canonical_bytes()
    raw = json.loads(request.canonical_bytes())
    raw["digest"] = digest("fake-request")

    with pytest.raises(AttributionError) as captured:
        load_attribution_run_request(canonical_bytes(raw))
    assert captured.value.reason_code == "run_request_digest_mismatch"
