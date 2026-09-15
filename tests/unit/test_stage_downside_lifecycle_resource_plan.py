from __future__ import annotations

import json
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.stage_learning import downside_lifecycle_resource_plan as subject

ROOT = find_project_root()
REVISION = "a" * 40


def test_p020_resource_config_preserves_exposure_and_review_boundaries() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    assert config["work_item"] == "P3-STAGE-020-B"
    consumed = config["consumption_ceiling"]
    assert consumed["exclude_p013_exact_plan_windows"] is True
    assert consumed["exclude_all_p014_admission_read_windows"] is True
    assert consumed["whole_identity_burn_down"] is False
    assert consumed["same_identity_disjoint_window_reuse_allowed"] is True
    assert consumed["preserved_2021_identity_global_exclusion"] is True
    selection = config["selection"]
    assert selection["reserve_per_cell"] == 32
    assert selection["primary_per_cell"] == 24
    assert selection["fallback_per_cell"] == 8
    assert selection["total_rows"] == 256
    assert selection["global_identity_unique"] is True
    authority = config["authority"]
    assert authority["input_readiness_market_read"] is True
    assert authority["semantic_market_read"] is False
    assert authority["reference_opinion"] is False
    assert authority["threshold_basis"] is False
    assert authority["candidate_scoring"] is False
    assert authority["preserved_2021_market_read"] is False
    assert authority["2025_read"] is False


def test_consumed_windows_union_p013_plan_and_all_p014_admission_reads(
    tmp_path: Path, monkeypatch,
) -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)

    def fake_bound_json(root, binding, label):
        if label == "p013_result":
            return {
                "artifact": {
                    "manifest_sha256": config["bindings"]["p013_exact_plan_artifact"][
                        "manifest_sha256"
                    ]
                }
            }
        if label == "p014_result":
            return {
                "artifact": {
                    "manifest_sha256": config["bindings"]["p014_admission_artifact"][
                        "manifest_sha256"
                    ]
                }
            }
        raise AssertionError(label)

    monkeypatch.setattr(subject, "_bound_json", fake_bound_json)
    monkeypatch.setattr(
        subject,
        "verify_p013_plan_artifact",
        lambda *args, **kwargs: {
            "manifest_sha256": config["bindings"]["p013_exact_plan_artifact"][
                "manifest_sha256"
            ],
            "plan_digest": config["bindings"]["p013_exact_plan_artifact"]["plan_digest"],
        },
    )
    monkeypatch.setattr(
        subject,
        "verify_admission_artifact",
        lambda *args, **kwargs: {
            "manifest_sha256": config["bindings"]["p014_admission_artifact"][
                "manifest_sha256"
            ]
        },
    )

    def fake_object(path, label):
        if label == "p013_exact_plan":
            return {
                "rows": [
                    {"security_id": f"P013-{i}", "window_id": f"W-{i}"}
                    for i in range(256)
                ]
            }
        if label == "p014_admission_ledger":
            return {
                "rows": [
                    {"security_id": f"P014-{i}", "window_id": f"A-{i}"}
                    for i in range(5)
                ]
            }
        raise AssertionError(label)

    monkeypatch.setattr(subject, "_object", fake_object)
    consumed, receipt = subject._consumed_windows(
        root=ROOT, training_root=tmp_path, config=config
    )
    assert len(consumed) == 261
    assert ("P013-0", "W-0") in consumed
    assert ("P014-0", "A-0") in consumed
    assert receipt["p013_consumed_window_count"] == 256
    assert receipt["p014_consumed_window_count"] == 5
    assert receipt["combined_consumed_window_count"] == 261


def test_capacity_shortfall_publishes_fail_closed_artifact_before_review(
    tmp_path: Path, monkeypatch,
) -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    cells = list(config["selection"]["cell_order"])

    def fake_bound_json(root, binding, label):
        if label == "p020_concept":
            return {
                "state": (
                    "PREREGISTERED_DOWNSIDE_LIFECYCLE_SEMANTICS_BEFORE_"
                    "REFERENCE_OR_THRESHOLD_SEARCH"
                ),
                "next_resource_step": {"work_item": "P3-STAGE-020-B"},
            }
        if label == "resource_model":
            return {
                "selected_disposition": (
                    "PROCEED_TO_METADATA_ONLY_ERA_BOARD_EXPOSURE_CAPACITY_CENSUS"
                )
            }
        if label == "training_node":
            return {"node": "synthetic"}
        raise AssertionError(label)

    monkeypatch.setattr(subject, "_bound_json", fake_bound_json)
    monkeypatch.setattr(
        subject,
        "_consumed_windows",
        lambda **kwargs: ({("old", "window")}, {"combined_consumed_window_count": 1}),
    )
    monkeypatch.setattr(
        subject,
        "_reconstruct_tokens",
        lambda **kwargs: ([{"security_id": "candidate"}], [], {
            "base_manifest_sha256": "sha256:base",
            "correction_manifest_sha256": "sha256:correction",
        }, set()),
    )
    monkeypatch.setattr(
        subject,
        "freeze_candidate_surface",
        lambda **kwargs: [{"security_id": "candidate"}],
    )
    rows = []
    for index, cell in enumerate(cells):
        era, board = cell.split("/", 1)
        rows.append(
            {
                "security_id": f"S{index}",
                "security_code": f"C{index}",
                "era": era,
                "board_family": board,
                "cell": cell,
                "window_id": f"W{index}",
                "window_start": "2020-01-01",
                "window_end": "2020-03-31",
                "identity_rank": 1,
                "window_rank": 1,
                "qualified": True,
                "reason": "61_SESSION_CONTIGUOUS_INPUT_READY",
                "source_binding_digest": f"sha256:{index:064x}",
            }
        )
    monkeypatch.setattr(subject, "_screen_candidates", lambda **kwargs: (rows, []))
    cell_summary = [
        {
            "cell": cell,
            "qualified_unique_identity_count": 1,
            "required_unique_qualified_identity_count": 32,
            "sufficient_for_plan_freeze": False,
        }
        for cell in cells
    ]
    monkeypatch.setattr(subject, "_cell_summary", lambda *args, **kwargs: (cell_summary, False))
    monkeypatch.setattr(
        subject,
        "select_qualified_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not select")),
    )

    result = subject.run_plan(
        config_path=ROOT / subject.DEFAULT_CONFIG,
        training_root=tmp_path / "training",
        output_root=tmp_path / "output",
        code_revision=REVISION,
    )
    assert result["disposition"] == "CLOSED_RESOURCE_ADMISSION_CAPACITY_SHORTFALL"
    assert result["exact_plan_available"] is False
    assert result["ready_for_separate_semantic_review_freeze"] is False
    assert result["input_readiness_market_read"] is True
    assert result["preserved_2021_market_read"] is False
    verified = subject.verify_artifact(Path(result["run_root"]), code_revision=REVISION)
    assert verified["verified"] is True
    assert verified["exact_plan_available"] is False
    assert verified["ready_for_separate_semantic_review_freeze"] is False
    summary = json.loads((Path(result["run_root"]) / "summary.json").read_text())
    assert summary["semantic_label_or_reference_read"] is False
    assert summary["threshold_or_candidate_read"] is False
