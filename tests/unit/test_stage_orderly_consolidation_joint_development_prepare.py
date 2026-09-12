from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.stage_learning import orderly_consolidation_joint_development_prepare as api
from quanthus.stage_learning.orderly_consolidation_joint_development_prepare import (
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
        "schema_version": "quanthus.stage-orderly-consolidation-joint-development-selection.v1",
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
        "schema_version": "quanthus.stage-orderly-consolidation-joint-threshold-basis.v1",
        "labels_used": False,
        "reference_opinions_used": False,
    }
    grid = {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-candidate-grid.v1",
        "stage0_candidate_count": 9,
        "stage2_candidate_count": 3,
        "pair_count": 27,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }
    evidence = {"schema_version": "test-evidence.v1"}
    paths = {"schema_version": "test-path-facts.v1"}
    source = {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-d1-source-binding.v1",
        "evidence_protocol_path": EVIDENCE_SCOPE_PATH,
        "evidence_protocol_digest": evidence_scope_digest,
        "development_preserved_evaluation_overlap": 0,
        "2019_p010_development_read": False,
        "2020_p009_development_read": False,
        "preserved_2021_evaluation_market_data_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-d1-summary.v1",
        "fresh_pool_size": 512,
        "development_identity_capacity": 256,
        "development_selected_case_count": 192,
        "development_shortfall_count": 0,
        "development_selection_digest": digest(selection),
        "threshold_basis_digest": digest(basis),
        "candidate_grid_digest": digest(grid),
        "stage0_candidate_count": 9,
        "stage2_candidate_count": 3,
        "candidate_pair_count": 27,
        "path_facts_digest": digest(paths),
        "development_evidence_digest": digest(evidence),
        "preserved_evaluation_identity_count": 160,
        "preserved_evaluation_identity_digest": plan["identity_digest"],
        "preserved_evaluation_plan_digest": digest(plan),
        "preserved_evaluation_data_read": False,
        "evidence_protocol_digest": EVIDENCE_SCOPE_DIGEST,
        "labels_used_for_threshold_basis": False,
        "reference_opinions_used_for_threshold_basis": False,
        "parameter_search": False,
        "opinion_count": 0,
        "pair_frozen": False,
    }
    payloads = {
        "source-binding.json": source,
        "development-selection.json": selection,
        "preserved-evaluation-plan.json": plan,
        "threshold-basis.json": basis,
        "candidate-grid.json": grid,
        "evidence.json": evidence,
        "path-facts.json": paths,
        "summary.json": summary,
    }
    files = {}
    for name, value in payloads.items():
        raw = canonical(value)
        (root / name).write_bytes(raw)
        files[name] = api._sha(raw)
    manifest = {
        "schema_version": api.ARTIFACT_SCHEMA,
        "run_id": "stage-orderly-consolidation-joint-d1-test",
        "code_revision": REVISION,
        "preregistration_digest": api.PREREG_DIGEST,
        "execution_digest": api.EXECUTION_DIGEST,
        "files": files,
    }
    manifest_raw = canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_raw)
    (root / "READY").write_text(api._sha(manifest_raw) + "\n", encoding="ascii")


def test_verify_artifact_binds_inventory_counts_and_unread_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    _write_artifact(artifact)
    monkeypatch.setattr(api, "validate_evidence", lambda *_args, **_kwargs: None)
    result = verify_artifact(artifact, code_revision=REVISION)
    assert result["verified"] is True
    assert result["fresh_pool_size"] == 512
    assert result["development_selected_case_count"] == 192
    assert result["stage0_candidate_count"] == 9
    assert result["stage2_candidate_count"] == 3
    assert result["candidate_pair_count"] == 27
    assert result["preserved_evaluation_data_read"] is False
    assert result["pair_frozen"] is False
    assert result["review_ready"] is True


def test_verify_artifact_rejects_evidence_scope_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    _write_artifact(artifact, evidence_scope_digest="sha256:" + "f" * 64)
    monkeypatch.setattr(api, "validate_evidence", lambda *_args, **_kwargs: None)
    with pytest.raises(api.OrderlyConsolidationJointPrepareError, match="d1_authority_claim"):
        verify_artifact(artifact, code_revision=REVISION)


def test_execution_and_scope_files_are_exact_json_contracts() -> None:
    execution = json.loads(
        (api.find_project_root() / api.load_execution()["evidence_protocol"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    assert digest(execution) == EVIDENCE_SCOPE_DIGEST
    assert execution["history_start"] == "2017-09-01"
    assert execution["case_start"] == "2018-01-01"
    assert execution["development_end"] == "2018-12-31"
    assert execution["bounds"]["max_cases"] == 192
