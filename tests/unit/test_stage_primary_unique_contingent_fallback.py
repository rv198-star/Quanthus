from __future__ import annotations

import json
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.stage_learning import primary_unique_contingent_fallback as subject

ROOT = find_project_root()


def test_p022_contract_keeps_primary_identity_independence_and_contingent_fallbacks() -> None:
    contract = subject._object(
        ROOT / "config/stage-learning/stage-primary-unique-contingent-fallback.v1.json",
        "contract",
    )
    primary = contract["primary_contract"]
    fallback = contract["fallback_contract"]
    gate = contract["feasibility_gate"]
    assert primary["primary_per_cell"] == 24
    assert primary["total_primary"] == 192
    assert primary["global_identity_unique"] is True
    assert primary["cross_cell_fill"] is False
    assert fallback["fallback_ceiling_per_cell"] == 8
    assert fallback["total_fallback_ceiling"] == 64
    assert fallback["fallback_pool_is_dormant_before_activation"] is True
    assert fallback["same_cell_only"] is True
    assert fallback["exclude_all_primary_identities"] is True
    assert fallback["cross_cell_fallback_identity_overlap_may_exist_before_activation"] is True
    assert (
        fallback[
            "activation_requires_global_identity_uniqueness_against_realized_primaries_and_previously_activated_fallbacks"
        ]
        is True
    )
    assert fallback["simultaneous_64_fallback_global_uniqueness_is_diagnostic_only"] is True
    assert gate["primary_global_unique_192_required"] is True
    assert gate["per_cell_dormant_non_primary_fallback_candidates_required"] == 8
    assert gate["worst_case_64_fallback_simultaneous_activation_required"] is False


def test_slot_matching_can_reassign_earlier_cell_instead_of_false_shortfall() -> None:
    candidates = {"A": ["shared", "a-only"], "B": ["shared"]}
    matched, feasible = subject._slot_matching(candidates, cell_order=("A", "B"), per_cell=1)
    assert feasible is True
    assert matched[("B", 1)] == "shared"
    assert matched[("A", 1)] == "a-only"


def _token(identity: str, cell: str, window: str) -> dict[str, object]:
    era, board = cell.split("/", 1)
    return {
        "security_id": identity,
        "security_code": identity,
        "listing_id": f"listing-{identity}",
        "era": era,
        "board_family": board,
        "board_code": board,
        "venue_id": "venue",
        "window_id": window,
        "window_start": "2020-01-01",
        "window_end": "2020-03-31",
        "exposure_level": "L1_POOL_RESERVED_NOT_READ",
    }


def test_fallback_pool_excludes_primaries_but_may_overlap_across_cells() -> None:
    cells = ("ERA/A", "ERA/B")
    by_cell = {
        "ERA/A": {
            "p-a": [_token("p-a", "ERA/A", "a-p")],
            "shared-fallback": [_token("shared-fallback", "ERA/A", "a-f")],
        },
        "ERA/B": {
            "p-b": [_token("p-b", "ERA/B", "b-p")],
            "shared-fallback": [_token("shared-fallback", "ERA/B", "b-f")],
        },
    }
    fallback_rows, feasible, receipts = subject._freeze_fallback_pools(
        by_cell,
        cell_order=cells,
        primary_identities={"p-a", "p-b"},
        per_cell=1,
        identity_seed="fallback",
        window_seed="window",
    )
    assert feasible is True
    assert len(fallback_rows) == 2
    assert {row["security_id"] for row in fallback_rows} == {"shared-fallback"}
    assert all(row["security_id"] not in {"p-a", "p-b"} for row in fallback_rows)
    assert all(receipt["sufficient"] is True for receipt in receipts)
    worst_case = subject._max_simultaneous_fallback_activation(
        fallback_rows,
        cell_order=cells,
        per_cell=1,
    )
    assert worst_case["all_64_simultaneously_feasible"] is False
    assert worst_case["max_simultaneously_activatable_count"] == 1


def test_artifact_verifier_exposes_only_bounded_cell_diagnostics(tmp_path: Path) -> None:
    primary_rows = [{"security_id": "PRIMARY-SECRET", "cell": "ERA/A"}]
    fallback_rows = [{"security_id": "FALLBACK-SECRET", "cell": "ERA/A"}]
    primary = {
        "rows": primary_rows,
        "plan_digest": subject.digest(primary_rows),
        "cell_counts": {"ERA/A": 1},
    }
    fallback = {
        "rows": fallback_rows,
        "pool_digest": subject.digest(fallback_rows),
        "cell_counts": {"ERA/A": 1},
        "cell_receipts": [
            {
                "cell": "ERA/A",
                "available_non_primary_identity_count": 3,
                "frozen_fallback_candidate_count": 1,
                "fallback_ceiling": 1,
                "sufficient": True,
                "selected_identity_digest": "sha256:" + "1" * 64,
            }
        ],
        "worst_case_simultaneous_activation": {
            "cell_activation_counts": {"ERA/A": 1}
        },
    }
    source = {"schema_version": "source"}
    summary = {
        "disposition": "TEST",
        "primary_row_count": 1,
        "primary_unique_identity_count": 1,
        "primary_all_cells_24": False,
        "primary_global_identity_unique": True,
        "fallback_row_count": 1,
        "fallback_all_cells_8": False,
        "fallback_primary_identity_overlap_count": 0,
        "worst_case_simultaneous_fallback_activation_count": 1,
        "worst_case_all_64_fallbacks_feasible": False,
        "primary_plan_digest": primary["plan_digest"],
        "fallback_pool_digest": fallback["pool_digest"],
        "source_binding_digest": subject.digest(source),
        "market_bar_read": False,
        "admission_market_read": False,
        "stage1_12_semantic_read": False,
        "threshold_or_candidate_read": False,
        "reference_opinion_read": False,
        "fallback_activation": False,
        "p020_reopen_authorized": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    payloads = {
        "primary-plan.json": subject.canonical(primary),
        "fallback-pools.json": subject.canonical(fallback),
        "source-binding.json": subject.canonical(source),
        "summary.json": subject.canonical(summary),
    }
    for name, raw in payloads.items():
        (tmp_path / name).write_bytes(raw)
    manifest = {
        "schema_version": "quanthus.stage-primary-unique-contingent-fallback-artifact.v1",
        "run_id": "test",
        "code_revision": "a" * 40,
        "config_digest": "sha256:" + "2" * 64,
        "files": {name: subject._sha(raw) for name, raw in payloads.items()},
    }
    manifest_raw = subject.canonical(manifest)
    (tmp_path / "manifest.json").write_bytes(manifest_raw)
    (tmp_path / "READY").write_text(subject._sha(manifest_raw) + "\n", encoding="ascii")

    verified = subject.verify_artifact(tmp_path, code_revision="a" * 40)
    assert verified["primary_cell_counts"] == {"ERA/A": 1}
    assert verified["fallback_cell_counts"] == {"ERA/A": 1}
    assert verified["fallback_cell_capacity"] == [
        {
            "cell": "ERA/A",
            "available_non_primary_identity_count": 3,
            "frozen_fallback_candidate_count": 1,
            "fallback_ceiling": 1,
            "sufficient": True,
        }
    ]
    assert verified["worst_case_fallback_cell_activation_counts"] == {"ERA/A": 1}
    rendered = json.dumps(verified, sort_keys=True)
    assert "PRIMARY-SECRET" not in rendered
    assert "FALLBACK-SECRET" not in rendered
