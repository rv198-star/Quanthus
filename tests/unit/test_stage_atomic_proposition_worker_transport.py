from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

from quanthus.stage_learning.worker import (
    STAGE_ATOMIC_PROPOSITION_G1_JOB,
    StageWorkerPaths,
    _digest,
    read_status,
)

REVISION = "a" * 40


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _paths(tmp_path: Path) -> StageWorkerPaths:
    training = tmp_path / "training"
    (training / "sources" / "dwd-v2.1-full-20260908").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    python = repo / ".venv-stage-training" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    key = tmp_path / "github-ro"
    key.write_text("test-only")
    paths = StageWorkerPaths(training, repo, key)
    paths.ensure()
    return paths


def test_atomic_g1_review_hides_envelope_but_selection_exposes_it(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    job_id = "stage_" + "8" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "market_date": f"2016-04-{index + 1:02d}",
            "high": str(201 + index),
            "low": str(199 + index),
            "close": str(200 + index),
            "coordinate_epoch": "original-price-quantity-epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    primary = {
        "directional_efficiency": {"value": "0.12", "reason": "available"},
        "range_width": {"value": "0.08", "reason": "available"},
        "width_change": {"value": "-0.40", "reason": "available"},
        "center_shift": {"value": "0.08", "reason": "available"},
    }
    case_body = {
        "security_code": "000001",
        "security_id": "security-fresh-2016-A",
        "market_date": "2016-04-21",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "fresh-2016-A"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": primary,
        "recent": {"width_change": {"value": "9.9"}},
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    paths_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "0.2", "reason": "available", "revision_id": _digest({"p": 1})
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.9", "reason": "available", "revision_id": _digest({"p": 2})
                },
            }
        }
    }
    basis = {"labels_used": False, "reference_opinions_used": False}
    grid = {
        "instability_candidate_count": 9,
        "order_profile_count": 3,
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }
    envelopes = {
        "review_transport_visible": False,
        "cases": {
            case["case_id"]: {
                "status": "AVAILABLE",
                "descriptors": {
                    "resolved_escape_count": 3,
                    "resolved_escape_side_alternation_count": 2,
                    "resolved_escape_both_sides": True,
                },
            }
        },
    }
    plan = {"identity_count": 160, "identity_digest": "sha256:" + "7" * 64, "data_read": False}
    values = {
        "evidence.json": evidence,
        "path-facts.json": paths_facts,
        "threshold-basis.json": basis,
        "candidate-grid.json": grid,
        "envelope-measurements.json": envelopes,
        "preserved-evaluation-plan.json": plan,
    }
    artifact = paths.runs / job_id / "stage-atomic-proposition-g1-test"
    artifact.mkdir(parents=True)
    files = {}
    for name, value in values.items():
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        (artifact / name).write_bytes(raw)
        files[name] = _sha(raw)
    manifest_raw = json.dumps({"files": files}, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = _sha(manifest_raw)
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_ATOMIC_PROPOSITION_G1_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-14T00:00:00+00:00",
        "started_at": "2026-09-14T00:00:01+00:00",
        "completed_at": "2026-09-14T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "atomic_proposition_g1": {
                "development_selected_case_count": 1,
                "threshold_basis_digest": _digest(basis),
                "candidate_grid_digest": _digest(grid),
                "envelope_measurements_digest": _digest(envelopes),
                "preserved_evaluation_plan_digest": _digest(plan),
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-14T00:00:02+00:00",
    }
    (paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    observed = read_status(paths, job_id)
    index = observed["review_transport_index"]
    assert index["envelope_measurement_included"] is False
    page_status = read_status(paths, index["pages"][0]["job_id"])
    review = json.loads(
        gzip.decompress(base64.b64decode(page_status["review_transport"]["payload"]))
    )
    assert review["cases"][0]["primary"] == primary
    assert "envelope_measurements" not in review
    assert "recent" not in review["cases"][0]

    selection = json.loads(
        gzip.decompress(base64.b64decode(observed["selection_transport"]["payload"]))
    )
    assert selection["threshold_basis"] == basis
    assert selection["candidate_grid"] == grid
    assert selection["envelope_measurements"] == envelopes
    assert selection["review_ledger_must_be_sealed_before_use"] is True
    assert selection["review_transport_exposed_envelope_measurement"] is False
    assert selection["joint_pair_search_count"] == 0
