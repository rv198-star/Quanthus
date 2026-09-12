"""P3-STAGE-011-C consumed-evidence topology conformance.

This runner reads only frozen repository contracts and the already-aggregated P010 diagnostic. It
performs no market-data read, no threshold selection, no candidate scoring, and no runtime Job.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

DIAGNOSTIC_PATH = "config/stage-learning/orderly-consolidation-boundary-diagnostic.v1.json"
DIAGNOSTIC_DIGEST = "sha256:d34e9f7ad8a32969e28d2a1d5245caa089a52fb8a7891a8b8f03e69ce3fc3d1c"
DESIGN_PATH = "config/stage-learning/orderly-consolidation-boundary-repair-design.v1.json"
DESIGN_DIGEST = "sha256:5d1e1ab3298df676829aff34a9c3b6117fa56053fb684f2c630ca04fbd380353"
PROJECTION_PATH = "config/stage-learning/orderly-consolidation-projection.v1.json"
PROJECTION_DIGEST = "sha256:11569752a0a3ae7c5a6c3f2ecf07b51710442c0f2445a848800203df3cda146c"


def _load(root: Path, path: str, expected: str) -> dict[str, Any]:
    value = json.loads((root / path).read_text(encoding="utf-8"))
    if digest(value) != expected:
        raise ValueError(f"digest_drift:{path}")
    return value


def run(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    diagnostic = _load(root, DIAGNOSTIC_PATH, DIAGNOSTIC_DIGEST)
    design = _load(root, DESIGN_PATH, DESIGN_DIGEST)
    projection = _load(root, PROJECTION_PATH, PROJECTION_DIGEST)

    stage2_failures = diagnostic["stage2_reference_gate_failure_counts"]
    checks = {
        "legacy_stage2_center_gate_is_the_only_observed_stage2_false_negative_gate": (
            stage2_failures["center_shift20_abs_gt_0_04"] == 10
            and stage2_failures["efficiency20_abs_gt_0_33"] == 0
            and stage2_failures["range_width20_gt_0_18"] == 0
            and stage2_failures["width_change20_gt_minus_0_20"] == 0
        ),
        "legacy_stage2_has_definite_cross_class_conflict": (
            diagnostic["legacy_stage2"]["false_positive_reference_types"]
            == {"STAGE_0": 1, "NEITHER": 1}
        ),
        "center_shift_removed_from_stage2_truth_inputs": (
            "center_shift20_abs" not in projection["inputs"]
            and projection["authority"]["center_shift_truth_authority"] is False
            and design["truth_authority"]["center_shift20_abs"] is False
        ),
        "path_churn_not_promoted_to_stage2_truth": (
            "path_turnover20" not in projection["inputs"]
            and projection["non_authoritative_context"]["path_turnover20"] == "not_an_input"
            and design["truth_authority"]["path_turnover20"] is False
        ),
        "directional_path_geometry_remains_relocation_only": (
            design["truth_authority"]["range_traversal20"] == "relocation_guard_only"
            and design["truth_authority"]["counter_direction_excursion_share20"]
            == "relocation_guard_only"
            and projection["non_authoritative_context"]["range_traversal20"]
            == "relocation_guard_only"
            and projection["non_authoritative_context"]["counter_direction_excursion_share20"]
            == "relocation_guard_only"
        ),
        "positive_stage2_still_requires_narrowness_and_contraction": (
            "materially_narrow_current_range"
            in projection["truth_function"]["orderly_consolidation_20"]
            and "material_range_contraction"
            in projection["truth_function"]["orderly_consolidation_20"]
        ),
        "coherent_relocation_remains_hard_negative_stage2_condition": (
            "negative_coherent_relocation"
            in projection["truth_function"]["orderly_consolidation_20"]
        ),
        "no_numeric_threshold_selected_from_consumed_evidence": (
            design["future_joint_development_shape"]["numeric_thresholds_selected_now"] is False
            and design["future_joint_development_shape"]["candidate_pair_selected_now"] is False
            and diagnostic["authority"]["threshold_selection"] is False
        ),
        "future_search_surface_is_bounded_before_fresh_data": (
            len(design["orderly_consolidation_profiles"]) == 3
            and design["future_joint_development_shape"]["maximum_joint_pairs"] == 27
            and design["future_joint_development_shape"]["grid_expansion_after_result"] is False
        ),
        "preserved_2021_remains_unread": (
            diagnostic["source"]["preserved_2021_evaluation_read"] is False
            and design["authority"]["preserved_2021_evaluation_read"] is False
            and projection["authority"]["preserved_2021_evaluation_read"] is False
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "schema_version": "quanthus.stage-orderly-consolidation-boundary-conformance-result.v1",
        "work_item": "P3-STAGE-011-C",
        "status": "PASS_TOPOLOGY_CONFORMANCE_ONLY" if not failed else "FAIL",
        "diagnostic_digest": DIAGNOSTIC_DIGEST,
        "design_digest": DESIGN_DIGEST,
        "projection_contract_digest": PROJECTION_DIGEST,
        "checks": checks,
        "failed_checks": failed,
        "numeric_thresholds_selected": False,
        "candidate_pairs_scored": 0,
        "parameter_trials": 0,
        "new_market_data_reads": 0,
        "runtime_jobs": 0,
        "preserved_2021_evaluation_read": False,
        "qualification_claim": False,
    }


def main() -> int:
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
