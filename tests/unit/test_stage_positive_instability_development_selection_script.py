from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from quanthus.structure_v2_inputs import digest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_stage_positive_instability_development_selection.py"


def _module():
    spec = importlib.util.spec_from_file_location("stage009_d2_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_local_d1_loader_reads_sealed_artifact_without_worker_restart(tmp_path: Path) -> None:
    module = _module()
    module.D1_JOB = "stage_" + "1" * 32
    training = tmp_path / "training"
    artifact = (
        training
        / "runs"
        / "stage-worker"
        / module.D1_JOB
        / "stage-positive-instability-d1-test"
    )
    artifact.mkdir(parents=True)
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "market_date": f"2020-01-{index + 1:02d}",
            "high": str(101 + index),
            "low": str(99 + index),
            "close": str(100 + index),
            "coordinate_epoch": "epoch",
            "reset_reason": None,
        }
        ref = digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    case_body = {
        "security_code": "000001",
        "security_id": "security-test",
        "market_date": "2020-01-21",
        "primary_available": True,
        "primary": {
            "directional_efficiency": {"value": "0.1", "reason": "available"},
            "range_width": {"value": "0.2", "reason": "available"},
            "width_change": {"value": "0.3", "reason": "available"},
            "center_shift": {"value": "0.1", "reason": "available"},
        },
        "history_refs": history_refs,
    }
    case = {"case_id": digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {"value": "0.2", "reason": "available"},
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.8",
                    "reason": "available",
                },
            }
        }
    }
    threshold_basis = {"labels_used": False, "example": "basis"}
    candidate_grid = {
        "candidate_count": 27,
        "parameter_search_performed": False,
        "candidates": [{"candidate_id": str(index)} for index in range(27)],
    }
    evaluation_plan = {
        "identity_count": 160,
        "identity_digest": digest({"evaluation": 160}),
        "data_read": False,
    }
    payloads = {
        "evidence.json": evidence,
        "path-facts.json": path_facts,
        "threshold-basis.json": threshold_basis,
        "candidate-grid.json": candidate_grid,
        "evaluation-plan.json": evaluation_plan,
    }
    files = {}
    for name, value in payloads.items():
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        (artifact / name).write_bytes(raw)
        files[name] = _sha(raw)
    manifest = {"files": files}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    module.D1_MANIFEST = _sha(manifest_raw)
    module.D1_EVIDENCE = digest(evidence)
    module.D1_CASES = 1
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(module.D1_MANIFEST + "\n", encoding="ascii")

    cases, selection = module._local_cases_and_selection(training)
    assert len(cases) == 1
    assert len(cases[0]["history"]) == 21
    assert selection["threshold_basis"] == threshold_basis
    assert selection["candidate_grid"] == candidate_grid
    assert selection["evaluation_plan_binding"]["identity_count"] == 160
    assert selection["review_ledger_must_be_sealed_before_use"] is True
    assert selection["evaluation_data_read"] is False
