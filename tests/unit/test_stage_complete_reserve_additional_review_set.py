from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.complete_reserve_additional_review_set import (
    CompleteReserveAdditionalReviewSetError,
    load_config,
    verify_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/stage-complete-reserve-additional-review-set.v1.json"


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_config_freezes_all_sixty_without_semantic_selection() -> None:
    value = load_config(CONFIG)
    selection = value["selection"]
    assert selection["source_row_count"] == 256
    assert selection["ready_row_count"] == 252
    assert selection["already_reviewed_ready_count"] == 192
    assert selection["expected_additional_review_count"] == 60
    assert selection["include_every_matching_row"] is True
    assert selection["positive_count_stop"] is False
    assert selection["semantic_value_sort"] is False
    assert selection["reference_value_sort"] is False
    assert selection["random_sample"] is False
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 600,
    }
    assert all(item is False for item in value["authority"].values())


def _artifact(tmp_path: Path, *, extra_field: bool = False) -> Path:
    rows = []
    for index in range(60):
        row = {
            "security_id": f"security_{index:04d}",
            "security_code": f"{index:06d}",
            "era": "PRE_STAR",
            "board_family": "MAIN",
            "cell": "PRE_STAR/MAIN",
            "window_id": f"PRE_STAR|MAIN|{index:02d}",
            "window_start": "2014-01-01",
            "window_end": "2014-04-01",
            "plan_slot": index + 1,
            "plan_role": "fallback",
            "readiness_source_member_closure_digest": "sha256:" + f"{index:064x}"[-64:],
        }
        if extra_field:
            row["range_width"] = "0.1"
        rows.append(row)
    frozen = {
        "schema_version": (
            "quanthus.stage-complete-reserve-additional-review-set-artifact-payload.v1"
        ),
        "rows": rows,
        "row_count": 60,
        "cell_counts": {"PRE_STAR/MAIN": 60},
        "identity_digest": digest(sorted(row["security_id"] for row in rows)),
        "set_digest": digest(rows),
        "selection_predicate": (
            "review_packet_ready=true AND already_reviewed_in_p014_ledger=false AND "
            "new_review_required=true"
        ),
        "all_matching_rows_included": True,
        "semantic_selection": False,
    }
    summary = {
        "schema_version": "quanthus.stage-complete-reserve-additional-review-set-summary.v1",
        "row_count": 60,
        "cell_counts": frozen["cell_counts"],
        "identity_digest": frozen["identity_digest"],
        "set_digest": frozen["set_digest"],
        "all_matching_rows_included": True,
        "positive_count_stop": False,
        "semantic_values_read": False,
        "reference_opinions_read": False,
        "candidate_output_read": False,
        "threshold_basis_read": False,
        "selection_transport_read": False,
        "preserved_2021_market_read": False,
        "2025_read": False,
    }
    source = {"metadata_only": True}
    root = tmp_path / "artifact"
    root.mkdir()
    payloads = {
        "additional-review-set.json": canonical(frozen),
        "source-binding.json": canonical(source),
        "summary.json": canonical(summary),
    }
    for name, raw in payloads.items():
        (root / name).write_bytes(raw)
    manifest = {
        "schema_version": "quanthus.stage-complete-reserve-additional-review-set-artifact.v1",
        "run_id": "test-run",
        "code_revision": "a" * 40,
        "files": {name: _sha(raw) for name, raw in payloads.items()},
    }
    manifest_raw = canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_raw)
    (root / "READY").write_text(_sha(manifest_raw) + "\n", encoding="ascii")
    return root


def test_verify_artifact_accepts_only_metadata_rows(tmp_path: Path) -> None:
    result = verify_artifact(_artifact(tmp_path), code_revision="a" * 40)
    assert result["verified"] is True
    assert result["row_count"] == 60
    assert result["semantic_values_read"] is False
    assert result["reference_opinions_read"] is False
    assert result["candidate_output_read"] is False


def test_verify_artifact_rejects_semantic_value_retention(tmp_path: Path) -> None:
    with pytest.raises(CompleteReserveAdditionalReviewSetError, match="artifact_set_binding"):
        verify_artifact(_artifact(tmp_path, extra_field=True), code_revision="a" * 40)
