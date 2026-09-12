#!/usr/bin/env python3
"""P3-STAGE-010-C consumed-evidence topology conformance; no threshold learning."""
from __future__ import annotations

import json
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
DESIGN = ROOT / (
    "config/stage-learning/positive-instability-quantitative-projection-design.v1.json"
)
CONTRACT = ROOT / "config/stage-learning/positive-instability-quantitative-projection.v1.json"
DIAGNOSTIC = ROOT / (
    "config/stage-learning/positive-instability-development-failure-diagnostic.v1.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    design = _load(DESIGN)
    contract = _load(CONTRACT)
    old = _load(DIAGNOSTIC)
    projection = design["projection_model"]
    expanding = projection["expanding_disorder_20"]
    broad = projection["broad_reversal_disorder_20"]
    contract_channels = contract["channels"]

    checks = {
        "old_failure_is_shared_core_bottleneck": (
            old["false_negative_reason_counts"]["range_width_core_false"] == 8
            and old["false_negative_reason_counts"]["low_directional_efficiency_core_false"] == 8
            and old["nearest_candidate_false_negative_count"] == 15
        ),
        "shared_core_is_explicitly_forbidden": projection["shared_core_forbidden"] is True,
        "expanding_channel_has_no_broad_width_hard_dependency": (
            "broad_reversal_range_width_floor" in expanding["does_not_require"]
            and "broad_range_width_material"
            not in contract_channels["expanding_disorder_20"]["inputs"]
        ),
        "expanding_channel_has_no_mandatory_low_efficiency_dependency": (
            "low_directional_efficiency_as_mandatory_gate" in expanding["does_not_require"]
            and set(contract_channels["expanding_disorder_20"]["any_path_disorder"]["inputs"])
            == {
                "expansion_low_directional_efficiency",
                "expansion_low_range_traversal",
                "expansion_high_counter_excursion",
            }
        ),
        "broad_channel_uses_two_of_three_not_mandatory_efficiency": (
            "low_directional_efficiency_as_single_mandatory_gate" in broad["does_not_require"]
            and contract_channels["broad_reversal_disorder_20"][
                "broad_path_disorder_two_of_three"
            ]["operator"]
            == "AT_LEAST_TWO_OF_THREE"
        ),
        "center_shift_remains_outside_truth_inputs": (
            "structure.geometry.range-center-shift@20_vs_prior20" in contract["forbidden_inputs"]
        ),
        "no_numeric_threshold_values_frozen": (
            contract["authority"]["numeric_threshold_values"] is False
        ),
        "consumed_2020_cannot_select_thresholds": (
            design["consumed_evidence_boundary"]["may_select_new_thresholds"] is False
        ),
        "preserved_2021_evaluation_still_unread": (
            design["preserved_evaluation"]["market_data_read"] is False
            and design["authority"]["2021_preserved_evaluation_read"] is False
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    result = {
        "schema_version": "quanthus.stage-positive-instability-projection-conformance-result.v1",
        "work_item": "P3-STAGE-010-C",
        "status": "PASS_TOPOLOGY_CONFORMANCE_ONLY" if not failed else "FAIL_TOPOLOGY_CONFORMANCE",
        "source_consumed_failure_diagnostic_digest": digest(old),
        "old_nearest_candidate_false_negative_count": old["nearest_candidate_false_negative_count"],
        "old_shared_core_fail_counts": old["false_negative_core_fail_counts"],
        "checks": checks,
        "failed_checks": failed,
        "numeric_thresholds_selected": False,
        "parameter_trials": 0,
        "new_market_data_reads": 0,
        "runtime_jobs": 0,
        "preserved_2021_evaluation_read": False,
        "qualification_claim": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
