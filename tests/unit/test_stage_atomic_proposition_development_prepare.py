from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.stage_learning import atomic_proposition_development_prepare as api
from quanthus.stage_learning.atomic_proposition_development_prepare import (
    EVIDENCE_SCOPE_DIGEST,
    EVIDENCE_SCOPE_PATH,
    _parser,
    verify_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

REVISION = "a" * 40


def test_verify_cli_matches_stage_worker_contract() -> None:
    args = _parser().parse_args(
        ["verify", "--artifact", "/tmp/existing-artifact", "--code-revision", REVISION]
    )
    assert args.command == "verify"
    assert args.artifact == Path("/tmp/existing-artifact")
    assert args.code_revision == REVISION


def _write_artifact(root: Path, *, evidence_scope_digest: str = EVIDENCE_SCOPE_DIGEST) -> None:
    root.mkdir()
    selection = {
        "schema_version": "quanthus.stage-atomic-proposition-development-selection.v1",
        "selected_case_count": 192,
        "shortfall_count": 0,
    }
    plan = {
        "schema_version": "test-preserved-plan.v1",
        "identity_count": 160,
        "identity_digest": "sha256:" + "1" * 64,
        "data_read": False,
    }
    basis = {
        "schema_version": "quanthus.stage-atomic-proposition-threshold-basis.v1",
        "labels_used": False,
        "reference_opinions_used": False,
    }
    grid = {
        "schema_version": "quanthus.stage-atomic-proposition-candidate-grid.v1",
        "instability_candidate_count": 9,
        "order_profile_count": 3,
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }
    evidence = {"schema_version": "test-evidence.v1"}
    paths = {"schema_version": "test-path-facts.v1"}
    envelopes = {
        "schema_version": "quanthus.stage-atomic-proposition-envelope-measurements.v1",
        "review_transport_visible": False,
    }
    source = {
        "schema_version": "quanthus.stage-atomic-proposition-g1-source-binding.v1",
        "evidence_protocol_path": EVIDENCE_SCOPE_PATH,
        "evidence_protocol_digest": evidence_scope_digest,
        "development_preserved_evaluation_overlap": 0,
        "2017_2021_result_bearing_market_access": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": api.SUMMARY_SCHEMA,
        "fresh_pool_size": 512,
        "development_identity_capacity": 256,
        "development_selected_case_count": 192,
        "development_shortfall_count": 0,
        "development_selection_digest": digest(selection),
        "threshold_basis_digest": digest(basis),
        "candidate_grid_digest": digest(grid),
        "envelope_measurements_digest": digest(envelopes),
        "instability_candidate_count": 9,
        "order_profile_count": 3,
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "path_facts_digest": digest(paths),
        "development_evidence_digest": digest(evidence),
        "preserved_evaluation_identity_count": 160,
        "preserved_evaluation_identity_digest": plan["identity_digest"],
        "preserved_evaluation_plan_digest": digest(plan),
        "preserved_evaluation_data_read": False,
        "evidence_protocol_digest": EVIDENCE_SCOPE_DIGEST,
        "labels_used_for_threshold_basis": False,
        "reference_opinions_used_for_threshold_basis": False,
        "envelope_measurement_visible_to_reviewer": False,
        "parameter_search": False,
        "opinion_count": 0,
        "atomic_freeze": False,
    }
    payloads = {
        "source-binding.json": source,
        "development-selection.json": selection,
        "preserved-evaluation-plan.json": plan,
        "threshold-basis.json": basis,
        "candidate-grid.json": grid,
        "evidence.json": evidence,
        "path-facts.json": paths,
        "envelope-measurements.json": envelopes,
        "summary.json": summary,
    }
    files = {}
    for name, value in payloads.items():
        raw = canonical(value)
        (root / name).write_bytes(raw)
        files[name] = api._sha(raw)
    manifest = {
        "schema_version": api.ARTIFACT_SCHEMA,
        "run_id": "stage-atomic-proposition-g1-test",
        "code_revision": REVISION,
        "preregistration_digest": api.PREREG_DIGEST,
        "execution_digest": api.EXECUTION_DIGEST,
        "files": files,
    }
    manifest_raw = canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_raw)
    (root / "READY").write_text(api._sha(manifest_raw) + "\n", encoding="ascii")


def test_verify_artifact_binds_atomic_inventory_and_hidden_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    _write_artifact(artifact)
    monkeypatch.setattr(api, "validate_evidence", lambda *_args, **_kwargs: None)
    result = verify_artifact(artifact, code_revision=REVISION)
    assert result["verified"] is True
    assert result["fresh_pool_size"] == 512
    assert result["development_selected_case_count"] == 192
    assert result["instability_candidate_count"] == 9
    assert result["order_profile_count"] == 3
    assert result["relocation_candidate_count"] == 1
    assert result["joint_pair_search_count"] == 0
    assert result["preserved_evaluation_data_read"] is False
    assert result["atomic_freeze"] is False
    assert result["review_ready"] is True


def test_verify_artifact_rejects_evidence_scope_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    _write_artifact(artifact, evidence_scope_digest="sha256:" + "f" * 64)
    monkeypatch.setattr(api, "validate_evidence", lambda *_args, **_kwargs: None)
    with pytest.raises(api.AtomicPropositionPrepareError, match="g1_authority_claim"):
        verify_artifact(artifact, code_revision=REVISION)


def test_execution_and_scope_files_are_exact_json_contracts() -> None:
    execution = api.load_execution()
    scope = json.loads(
        (api.find_project_root() / execution["evidence_protocol"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    assert digest(scope) == EVIDENCE_SCOPE_DIGEST
    assert scope["history_start"] == "2015-09-01"
    assert scope["case_start"] == "2016-01-01"
    assert scope["development_end"] == "2016-12-31"
    assert scope["bounds"]["max_cases"] == 192
    assert execution["candidate_identity"]["joint_pair_search_count"] == 0
