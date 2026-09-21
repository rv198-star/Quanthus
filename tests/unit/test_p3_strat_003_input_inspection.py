"""Synthetic boundaries for the fixed input metadata inspection."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy import p3_strat_003_input_inspection as inspection
from quanthus.platform.event_worker import _structure_input, _structure_request_digest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p3-strat-003/input-inspection.v1.json"


def test_frozen_inspection_config_is_admitted_without_arbitrary_configs() -> None:
    raw = json.loads(CONFIG.read_text())
    assert digest_value(raw) == inspection.REQUEST_DIGEST
    job = {"code_revision": "a" * 40, "run_config": str(CONFIG.relative_to(ROOT))}
    assert _structure_input(job) == job
    assert _structure_request_digest(job["run_config"]) == inspection.REQUEST_DIGEST
    with pytest.raises(RuntimeError, match="run_config_not_governed"):
        _structure_input({**job, "run_config": "config/research/arbitrary.json"})


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    project = tmp_path / "project"
    request = project / CONFIG.relative_to(ROOT)
    request.parent.mkdir(parents=True)
    raw = json.loads(CONFIG.read_text())
    qualification = json.loads((ROOT / raw["qualification"]["path"]).read_text())
    event = tmp_path / "event"
    probe = event / "structure-runs/run_probe"
    probe.mkdir(parents=True)
    qualification["semantic_equivalence_prerequisite"]["artifact_ref"] = str(probe)
    qualification_path = project / raw["qualification"]["path"]
    qualification_data = json.dumps(qualification).encode()
    qualification_path.write_bytes(qualification_data)
    raw["qualification"]["file_sha256"] = "sha256:" + hashlib.sha256(qualification_data).hexdigest()
    request.write_text(json.dumps(raw))
    (probe / "manifest.json").write_text("{}")
    (probe / "summary.json").write_text(json.dumps({
        "missing_boundary": {"anchor_market_date": "2026-08-26", "anchor_package_id": "anchor"},
    }))
    prerequisite = SimpleNamespace(
        artifact_root=probe, receipt=lambda: {"result_digest": "sha256:" + "a" * 64},
    )
    daily = tmp_path / "daily"
    daily.mkdir()
    monkeypatch.setattr(inspection, "REQUEST_DIGEST", digest_value(raw))
    monkeypatch.setattr(inspection, "find_project_root", lambda _path: project)
    monkeypatch.setattr(inspection, "_request", lambda _path: qualification)
    monkeypatch.setattr(inspection, "_semantic_equivalence_prerequisite", lambda _raw: prerequisite)
    monkeypatch.setattr(inspection, "load_prospective_runtime_config", lambda *_args, **_kwargs:
                        SimpleNamespace(daily_root=daily, historical_base_root=tmp_path / "base",
                                        correction_root=tmp_path / "correction"))
    package = daily / "packages/2026-08-28/short-directory-f8f8330"
    package.mkdir(parents=True)
    manifest = {
        "schema_version": 1, "kind": "cn01_daily_dwd_domain_increment",
        "market_date": "2026-08-28", "package_id": "dwd-daily-20260828-retained-f8f8330",
        "covered_after_date": "2026-08-27", "code_revision": "b" * 40,
        "prior": {"market_date": "2026-08-27", "package_id": "original-boundary",
                  "manifest_sha256": raw["failed_job"]["expected_manifest_sha256"]},
    }
    (package / "manifest.json").write_text(json.dumps(manifest))
    research = probe / "missing-boundary/package"
    research.mkdir(parents=True)
    research_data = json.dumps({
        **manifest, "market_date": "2026-08-27", "package_id": "research-boundary",
        "prior": {"market_date": "2026-08-26", "package_id": "anchor",
                  "manifest_sha256": "sha256:" + "d" * 64},
    }).encode()
    (research / "manifest.json").write_bytes(research_data)
    prerequisite.research_package_root = research
    prerequisite.research_manifest_sha256 = "sha256:" + hashlib.sha256(research_data).hexdigest()
    prerequisite.research_package_id = "research-boundary"
    prerequisite.boundary_market_date = date(2026, 8, 27)
    return SimpleNamespace(raw=raw, request=request, daily=daily, package=package,
                           output=tmp_path / "output", event=event, research=research,
                           prerequisite=prerequisite)


def _build(fixture: SimpleNamespace) -> dict[str, object]:
    return inspection.build_input_inspection(
        request_path=fixture.request, output=fixture.output, code_revision="c" * 40,
    )


def test_identity_mapping_keeps_all_versions_and_filters_stderr(fixture: SimpleNamespace) -> None:
    before = (fixture.package / "manifest.json").read_bytes()
    another = fixture.package.parent / "second-retained-version"
    another.mkdir()
    (another / "manifest.json").write_bytes(before)
    stderr = fixture.event / ".staging" / fixture.raw["failed_job"]["job_id"] / "1/stderr.log"
    stderr.parent.mkdir(parents=True)
    stderr.write_text(
        '  File "/app/p3_strat_003_input_qualification.py", line 999, '
        'in _fetch_bound_prior_package\n'
        '  File "/app/unrelated.py", line 1, in forbidden_secret_function\n'
        "SECRET_TOKEN=do-not-emit\n"
        + fixture.raw["failed_job"]["error_code"] + ": expected "
        + fixture.raw["failed_job"]["expected_manifest_sha256"] + "; observed "
        + fixture.raw["failed_job"]["observed_manifest_sha256_prefixes"][0]
        + "\nsha256:" + "e" * 64
    )
    result = _build(fixture)
    manifest = json.loads((fixture.output / "manifest.json").read_text())
    core = manifest["inspection"]
    assert result["disposition"] == "INPUT_IDENTITY_INSPECTION_COMPLETE"
    assert result["row_count"] == 0 and result["execution_authorized"] is False
    assert core["observed_manifest_count"] == 2
    assert core["match_disposition"] == "multiple"
    assert core["match_occurrence_count"] == 2 and core["distinct_identity_count"] == 1
    assert {item["relation"] for item in core["expected_hash_matches"]} == {"prior"}
    assert len({item["relative_path"] for item in core["expected_hash_matches"]}) == 2
    assert core["claim_failed_job_inventory_reproduced"] is False
    assert core["stderr"]["frame_names"] == ["_fetch_bound_prior_package"]
    assert core["stderr"]["observed_full_hashes_matching_declared_prefixes"] == [
        fixture.raw["failed_job"]["observed_manifest_sha256_prefixes"][0],
    ]
    rendered = (fixture.output / "manifest.json").read_text()
    assert "SECRET_TOKEN" not in rendered and "forbidden_secret_function" not in rendered
    assert (fixture.package / "manifest.json").read_bytes() == before
    assert inspection.verify_input_inspection_artifact(
        fixture.output, code_revision="c" * 40, request_digest=inspection.REQUEST_DIGEST,
    ) == result
    (fixture.output / "summary.json").write_text("{}")
    with pytest.raises(ExpertEvaluationError, match="artifact identity"):
        inspection.verify_input_inspection_artifact(
            fixture.output, code_revision="c" * 40, request_digest=inspection.REQUEST_DIGEST,
        )


def test_expected_identity_only_in_sealed_research_prior_is_reported(
    fixture: SimpleNamespace,
) -> None:
    daily_path = fixture.package / "manifest.json"
    daily = json.loads(daily_path.read_text())
    daily["prior"]["manifest_sha256"] = "sha256:" + "d" * 64
    daily_path.write_text(json.dumps(daily))
    research_path = fixture.research / "manifest.json"
    research = json.loads(research_path.read_text())
    research["prior"]["manifest_sha256"] = fixture.raw["failed_job"]["expected_manifest_sha256"]
    data = json.dumps(research).encode()
    research_path.write_bytes(data)
    fixture.prerequisite.research_manifest_sha256 = "sha256:" + hashlib.sha256(data).hexdigest()
    _build(fixture)
    core = json.loads((fixture.output / "manifest.json").read_text())["inspection"]
    assert core["match_disposition"] == "unique" and core["distinct_identity_count"] == 1
    match = core["expected_hash_matches"][0]
    assert match["source"] == "sealed_probe_research_package" and match["relation"] == "prior"
    assert match["prior"]["market_date"] == "2026-08-26"
    assert core["sealed_probe_research_package"]["prior"] == match["prior"]


@pytest.mark.parametrize("section,key,value", [
    ("authority", "b_acceptance", True),
    ("failed_job", "job_id", "job_other"),
    ("limits", "maximum_packages", 129),
    ("scope", "read_current", True),
])
def test_frozen_request_rejects_authority_and_target_drift(
    fixture: SimpleNamespace, section: str, key: str, value: object,
) -> None:
    fixture.raw[section][key] = value
    fixture.request.write_text(json.dumps(fixture.raw))
    with pytest.raises(ExpertEvaluationError, match="frozen metadata-only"):
        _build(fixture)
    assert not fixture.output.exists()


@pytest.mark.parametrize("failure", ["symlink", "size", "count"])
def test_metadata_read_limits_fail_before_output(
    fixture: SimpleNamespace, tmp_path: Path, failure: str,
) -> None:
    path = fixture.package / "manifest.json"
    if failure == "symlink":
        outside = tmp_path / "outside-secret"
        outside.write_text("unrelated secret")
        path.unlink()
        path.symlink_to(outside)
    elif failure == "size":
        path.write_bytes(b" " * 262145)
    else:
        for index in range(128):
            (fixture.package.parent / f"another-{index}").mkdir()
    with pytest.raises(ExpertEvaluationError):
        _build(fixture)
    assert not fixture.output.exists()


def test_total_output_limit_fails_without_partial_artifact(
    fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = inspection._scan(
        fixture.daily, limits=fixture.raw["limits"], deadline=time.monotonic() + 60,
    )[0]
    monkeypatch.setattr(inspection, "_scan", lambda *_args, **_kwargs:
                        [{**record, "relative_path": f"packages/2026-08-28/{index}/manifest.json"}
                         for index in range(128)])
    with pytest.raises(ExpertEvaluationError, match="output limit"):
        _build(fixture)
    assert not fixture.output.exists()
