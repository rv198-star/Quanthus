from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUEST = ROOT / "config/research/p5-stage-001/request.v1.json"
TARGETS = ROOT / "config/research/p5-stage-001/stage-targets.v1.json"
STAGE_BUNDLE = ROOT / "config/stage-learning/stage-proposition-bundle.v1.json"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _semantic_digest(value: dict[str, object]) -> str:
    payload = {key: item for key, item in value.items() if key != "semantic_digest"}
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_p5_stage_001_a_requests_are_frozen_and_digest_bound() -> None:
    request = _load(REQUEST)
    targets = _load(TARGETS)

    assert request["schema_version"] == "quanthus.p5-stage-001-research-request.v1"
    assert request["work_item"] == "P5-STAGE-001-A"
    assert request["state"] == "FROZEN_A_NO_MARKET_READ_NO_TRAINING"
    assert request["semantic_digest"] == _semantic_digest(request)
    assert request["semantic_digest"] == (
        "sha256:89c729c8b206f8d051d681736f2a58d5d2f301e78c4a7977330263c7dcf2d8ec"
    )

    assert targets["schema_version"] == "quanthus.p5-stage-001-stage-targets.v1"
    assert targets["work_item"] == "P5-STAGE-001-A"
    assert targets["state"] == "FROZEN_A_NO_MARKET_READ_NO_TRAINING"
    assert targets["semantic_digest"] == _semantic_digest(targets)
    assert targets["semantic_digest"] == (
        "sha256:2e175cefbc7a68ecf1e7a2856bb01ef849baee10260e4a2cfe4957bcb4abddc3"
    )


def test_p5_stage_001_a_binds_existing_authority_and_new_target_set() -> None:
    request = _load(REQUEST)

    for binding in request["bindings"].values():
        path = ROOT / binding["path"]
        observed = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert observed == binding["sha256"], binding["path"]

    target_binding = request["bindings"]["stage_targets"]
    assert target_binding["semantic_digest"] == _load(TARGETS)["semantic_digest"]


def test_p5_stage_001_a_is_narrow_and_does_not_activate_data_or_training() -> None:
    request = _load(REQUEST)
    authority = request["authority"]
    data = request["data_use"]

    assert authority["narrow_p5_research_line_authorized"] is True
    assert authority["A_docs_config_tests_and_synthetic_contracts_authorized"] is True
    assert authority["P3_stage_phase1_official_qualification_reopened"] is False
    assert authority["market_data_row_read_in_A_authorized"] is False
    assert authority["model_training_in_A_authorized"] is False
    assert authority["successor_issue_execution_auto_authorized"] is False
    assert authority["B_requires_explicit_issue_activation"] is True
    assert authority["C_requires_explicit_issue_activation"] is True
    assert authority["D_requires_explicit_issue_activation"] is True
    assert authority["P6_mutation_authorized"] is False
    assert authority["trading_authorized"] is False

    assert data["A"]["market_row_read"] is False
    assert data["A"]["training"] is False
    assert data["B_candidate_scope_after_explicit_activation"]["market_date_start"] == "2012-01-01"
    assert data["B_candidate_scope_after_explicit_activation"]["market_date_end"] == "2021-12-31"
    assert data["B_candidate_scope_after_explicit_activation"][
        "row_read_before_B_freezes_and_verifies_manifest"
    ] is False
    assert data["warmup_2011"]["authorized_by_A"] is False
    assert data["protected_2022_2024"]["authorized"] is False
    assert data["blind_2025"]["authorized"] is False
    assert data["simulation_2026"]["authorized"] is False
    assert data["p3_strat_003_data_authority_inheritance"] is False


def test_p5_stage_001_targets_match_layered_vocabulary_without_top1_compression() -> None:
    targets = _load(TARGETS)
    bundle = _load(STAGE_BUNDLE)

    cards = {item["historical_stage_code"]: item for item in targets["cards"]}
    catalogue = {
        item["historical_stage_code"]: item
        for item in bundle["proposition_catalog"]
        if item["historical_stage_code"] in range(1, 13)
    }

    assert set(cards) == set(range(1, 13))
    assert set(catalogue) == set(range(1, 13))
    for code in range(1, 13):
        assert cards[code]["proposition_id"] == catalogue[code]["proposition_id"]
        assert cards[code]["layer"] == catalogue[code]["layer"]
        assert cards[code]["evidence_grade"] == catalogue[code]["evidence_grade"]
        assert cards[code]["anchor_time"] == "T_after_close"
        assert cards[code]["feature_cutoff"] == "T_close"
        assert cards[code]["label_observation_end"] == "T_close"
        assert cards[code]["reference_eligibility"].startswith(
            ("unresolved_until_B", "ineligible_until_governed_")
        )

    output = targets["global_output_contract"]
    assert output["global_softmax_forbidden"] is True
    assert output["multiple_stage_scores_may_coexist"] is True
    assert output["primary_stage_output"] is None
    assert output["ineligible_or_untrained_head_raw_score"] is None
    assert output["ineligible_or_uncalibrated_probability"] is None

    controls = targets["epistemic_and_control_outputs"]
    assert controls["0_DIRECTIONLESS"]["is_part_of_1_to_12_model_heads"] is False
    assert controls["ABSTAIN"]["is_market_state"] is False
    assert controls["X"]["is_market_state"] is False
    assert controls["X"]["may_not_imply_structural_damage"] is True


def test_p5_stage_001_stage11_is_conditioned_on_qualified_stage10_context() -> None:
    request = _load(REQUEST)
    targets = _load(TARGETS)
    cards = {item["historical_stage_code"]: item for item in targets["cards"]}

    stage10 = cards[10]
    stage11 = cards[11]
    assert stage10["proposition_id"] == "stock_stage.context.structural_failure"
    assert stage11["proposition_id"] == "stock_stage.condition.post_break_rebound"
    assert stage11["probability_semantics"] == "conditional"
    assert stage11["conditioning_population"] == (
        "stage_10_structural_failure_context_qualified_at_T"
    )
    assert stage11["parent_proposition_id"] == stage10["proposition_id"]
    assert stage11["parent_not_applicable_handling"] == (
        "applicability_false_probability_null"
    )
    assert request["target_contract"]["stage_11_probability"] == (
        "P(Stage11 | Stage10 context qualified at T)"
    )
    assert request["target_contract"]["stage_11_parent_not_applicable"] == (
        "applicability_false_probability_null"
    )


def test_p5_stage_001_reference_and_capacity_rules_fail_closed() -> None:
    request = _load(REQUEST)
    targets = _load(TARGETS)

    reference = targets["reference_rules"]
    assert reference["unknown_is_not_negative"] is True
    assert reference["confirmatory_primary_requires_blind_to_tested_outputs"] is True
    assert reference["confirmatory_primary_may_not_be_derived_only_from_tested_rule"] is True
    assert reference["rule_silver_allowed_for_training_or_exploration"] is True

    qualification = request["sample_qualification"]
    assert qualification["maximum_unique_episode_anchors"] == 1500
    assert qualification["maximum_self_supervised_windows"] == 200000
    assert qualification["minimum_per_head_episode_counts"] == {
        "train": {"positive": 50, "negative": 50},
        "development_validation": {"positive": 20, "negative": 20},
        "calibration": {"positive": 20, "negative": 20},
        "evaluation": {"positive": 50, "negative": 50},
    }
    assert qualification["B_must_publish_readiness_matrix_before_formal_labeling"] is True
    assert qualification["insufficient_head_disposition"] == (
        "head_not_evaluable_keep_output_null"
    )
    assert qualification["no_budget_expansion_to_complete_all_12"] is True
    assert qualification["no_result_driven_gate_lowering"] is True


def test_p5_stage_001_freezes_attribution_seeds_and_calibration_before_results() -> None:
    request = _load(REQUEST)

    ladder = request["model_ladder"]
    assert set(key for key in ladder if key in {"R", "L", "G", "T0", "T1"}) == {
        "R",
        "L",
        "G",
        "T0",
        "T1",
    }
    assert ladder["sequence_architecture"] == {
        "window_sessions": 120,
        "maximum_input_channels": 24,
        "patch_sessions": 5,
        "encoder_layers": 4,
        "d_model": 128,
        "attention_heads": 4,
        "ffn_dimension": 512,
        "maximum_parameters": 2000000,
        "text_BERT_weights_used": False,
    }
    assert ladder["automatic_hyperparameter_search"] is False
    assert ladder["fusion_model_in_A_to_H"] is False

    comparisons = request["comparison_contract"]
    assert comparisons["T0_minus_B_star"] == (
        "component_diagnostic_sequence_architecture_increment"
    )
    assert comparisons["T1_minus_T0"] == (
        "component_diagnostic_self_supervised_pretraining_increment"
    )
    assert comparisons["T1_minus_B_star"] == (
        "sole_confirmatory_complete_learned_route_increment"
    )
    assert comparisons["confirmatory_claim_hierarchy"][
        "sole_line_level_confirmatory_contrast"
    ] == "T1_minus_B_star"
    assert comparisons["confirmatory_claim_hierarchy"][
        "component_diagnostics_may_not_create_SUPPORTED_INCREMENT_line_disposition"
    ] is True
    assert comparisons["rule_model_agreement"] == "diagnostic_only_not_confidence_multiplier"

    seeds = request["seed_policy"]
    assert seeds["downstream_seeds"] == [17, 29, 43]
    assert seeds["confirmatory_raw_primary"].startswith("arithmetic_mean")
    assert seeds["single_seed_results"] == "stability_secondary_only"
    assert seeds["best_seed_selection_forbidden"] is True
    assert "no_replacement_seed" in seeds["missing_required_seed_after_pre_registered_recovery"]

    calibration = request["calibration"]
    assert calibration["method"] == "platt_scaling"
    assert calibration["fit_partition"] == "calibration_only"
    assert calibration["model_inputs"] == {
        "L": "L_raw_score",
        "G": "G_raw_score",
        "T0": "three_seed_mean_raw_score",
        "T1": "three_seed_mean_raw_score",
    }
    assert calibration["B_star_calibrator"] == (
        "calibrator_of_selected_underlying_L_or_G_model"
    )
    assert calibration["insufficient_evidence"] == (
        "calibrated_probability_null_raw_score_may_remain"
    )


def test_p5_stage_001_statistics_and_compute_have_pre_result_stop_rules() -> None:
    request = _load(REQUEST)
    evaluation = request["primary_evaluation"]

    assert evaluation["primary_metric"] == (
        "macro_average_precision_over_confirmatory_evaluable_heads"
    )
    assert evaluation["uncertainty"] == {
        "method": "paired_calendar_block_bootstrap_v1",
        "protocol_ref": "primary_evaluation.bootstrap_protocol",
        "block_sessions": 22,
        "effective_time_blocks_minimum": 8,
        "target_valid_resamples": 2000,
        "maximum_candidate_resamples": 4000,
        "confidence_level": 0.95,
    }
    assert evaluation["AP_increment_rule"] == (
        "paired_difference_95pct_interval_lower_bound_gt_0"
    )
    assert evaluation["Brier_noninferiority"]["tolerance"] == 0.01
    assert evaluation["Brier_noninferiority"]["rule"] == (
        "paired_difference_95pct_interval_upper_bound_lte_0.01"
    )
    assert evaluation["Brier_noninferiority"]["point_estimate_only_pass_forbidden"] is True
    assert evaluation["severe_error"]["point_tolerance"] == 0.02
    assert evaluation["severe_error"]["slice_id"] == (
        "ALL_REFERENCE_ELIGIBLE_DEFINITE_APPLICABLE_CASES"
    )
    assert evaluation["severe_error"]["cross_head_aggregation"].startswith(
        "equal_weight_macro_mean_of_per_head_paired_rate_differences"
    )
    assert evaluation["severe_error"]["insufficient_resolution_or_blocks"] == "INCONCLUSIVE"
    multiplicity = evaluation["multiple_comparison_policy"]
    assert multiplicity["line_level_confirmatory_family"] == {
        "contrasts": ["T1_minus_B_star"],
        "family_size": 1,
        "multiplicity_adjustment": "none_required_single_confirmatory_claim",
    }
    assert multiplicity["component_contrasts"]["contrasts"] == [
        "T0_minus_B_star",
        "T1_minus_T0",
    ]
    assert multiplicity["per_head_results"] == {
        "claim_ceiling": "descriptive_diagnostic_only",
        "confirmatory_hypotheses": False,
        "p_values": "not_computed",
        "multiplicity_adjustment": (
            "not_applicable_because_no_per_head_confirmatory_claims"
        ),
        "may_not_create_or_upgrade_line_level_SUPPORTED_INCREMENT": True,
    }

    budget = request["resource_budget"]
    assert budget["maximum_total_node_hours"] == 24
    assert budget["worker_concurrency"] == 1
    assert budget["GPU_two_x_median_speedup"] == (
        "recommended_preference_not_absolute_gate"
    )
    assert budget["C_must_freeze_sub_2x_GPU_rule_before_benchmark_results"] is True
    assert budget["T1_over_budget_disposition"] == (
        "COMPUTE_LIMITED_without_blocking_L_G_T0"
    )


def test_p5_stage_001_repository_routing_is_compact_and_parallel() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    design = (ROOT / "docs/design/lld/07-research-scoring.md").read_text(encoding="utf-8")
    work_item = (ROOT / "docs/work-items/p5-stage-001.md").read_text(encoding="utf-8")
    index = (ROOT / "docs/work-items/index.md").read_text(encoding="utf-8")

    assert "## Current active task routing — P3-STRAT-003" in agents
    assert "## Parallel research routing — P5-STAGE-001" in agents
    assert "A permits docs/config/tests and synthetic contract cases only" in agents
    assert "A completion does not auto-start B/C/D" in agents

    assert "### 3.1 Bounded P5-STAGE-001 sequence-model exception" in design
    assert "does not authorize architecture search" in design
    assert "P5-STAGE-001-A / #401" in work_item
    assert "Current recovery checkpoint" in work_item
    assert "P5-STAGE-001 | Learned Stage assessment dual-track research" in index


def test_p5_stage_001_target_cards_are_semantically_complete() -> None:
    targets = _load(TARGETS)
    cards = {item["historical_stage_code"]: item for item in targets["cards"]}

    for code in range(1, 13):
        card = cards[code]
        examples = card["semantic_examples"]
        assert set(examples) == {"positive", "negative", "ambiguous", "unjudgeable"}
        assert all(examples[key] for key in examples)
        assert card["semantic_intent"]
        assert card["t_visible_evidence_requirements"]
        assert card["rule_model_distinction"][
            "tested_rule_alone_may_not_be_confirmatory_truth"
        ] is True
        assert card["relationships"]["same_layer_mutual_exclusion_codes"] == []
        assert card["relationships"]["same_layer_ambiguity_preserved"] is True
        assert card["severe_error_slice"] == (
            "ALL_REFERENCE_ELIGIBLE_DEFINITE_APPLICABLE_CASES"
        )
        assert card["severe_error_slice_membership_rule"] == (
            "stage-targets.v1.json:severe_error_defaults.reference_slice_membership_rule"
        )

    assert cards[7]["probability_semantics"] == (
        "unavailable_until_governed_repair_context"
    )
    assert cards[7]["initial_trainability"] == (
        "ineligible_head_output_null_until_governed_repair_context"
    )
    assert cards[8]["probability_semantics"] == (
        "unavailable_until_governed_high_side_context_predicate"
    )
    assert cards[8]["initial_trainability"] == (
        "ineligible_head_output_null_until_governed_high_side_context_predicate"
    )
    assert cards[8]["relationships"]["required_parent_code"] == 5
    assert cards[11]["semantic_examples"]["negative"] == [
        "Within qualified Stage10, the same failed support remains unreclaimed at T "
        "but no qualifying post-break rebound is present."
    ]
    assert (
        "not applicable"
        not in cards[11]["semantic_examples"]["unjudgeable"][0]
    )
    assert cards[11]["known_parent_not_applicable_reference_handling"] == (
        "no_stage11_reference_label; applicability_false; calibrated_probability_null"
    )
    assert cards[4]["known_parent_not_applicable_reference_handling"] == (
        "no_stage4_reference_label; applicability_false; calibrated_probability_null"
    )
    assert cards[6]["known_parent_not_applicable_reference_handling"] == (
        "no_stage6_reference_label; applicability_false; calibrated_probability_null"
    )
    assert cards[1]["known_parent_not_applicable_reference_handling"] == (
        "no_stage1_reference_label; applicability_false; calibrated_probability_null"
    )
    assert cards[9]["probability_semantics"] == (
        "unavailable_until_governed_activity_relation"
    )
    assert cards[9]["initial_trainability"] == (
        "ineligible_head_output_null_until_governed_activity_relation"
    )


def test_p5_stage_001_marginal_population_is_frozen_outcome_blind() -> None:
    targets = _load(TARGETS)
    population = targets["marginal_population_contract"]
    cards = {item["historical_stage_code"]: item for item in targets["cards"]}

    assert population["population_id"] == "BASE_ACTIVE_RESEARCH_ANCHOR_V1"
    membership = population["base_membership"]
    assert membership["anchor_unit"] == "unique (security_id, T_market_date) at T_after_close"
    assert "labels" not in membership["semantic_applicability"].lower()
    assert set(membership["prohibited_membership_inputs"]) == {
        "stage reference label",
        "tested rule output",
        "model score or prediction",
        "future price/return/event outcome",
        "post-T correction not known at T",
        "current sector membership used as historical proxy",
    }
    assert population["B_authority"]["may_resolve_exact_source_manifest_and_counts"] is True
    assert population["B_authority"]["may_not_redefine_population_semantics"] is True
    assert population["B_authority"][
        "may_not_filter_base_population_by_label_rule_model_or_future_outcome"
    ] is True
    assert population["sampling_roles"]["calibration"].startswith(
        "outcome-blind sample from the frozen base population"
    )
    assert population["sampling_roles"]["evaluation"].startswith(
        "outcome-blind sample from the frozen base population"
    )

    for code in (2, 3, 5, 10, 12):
        assert cards[code]["probability_semantics"] == (
            "marginal_over_BASE_ACTIVE_RESEARCH_ANCHOR_V1"
        )
        assert cards[code]["applicability_population_id"] == (
            "BASE_ACTIVE_RESEARCH_ANCHOR_V1"
        )
        assert cards[code]["applicability_rule_ref"] == (
            "stage-targets.v1.json:marginal_population_contract"
        )
        assert cards[code]["missing_required_T_facts_handling"] == (
            "UNAVAILABLE_not_NOT_APPLICABLE"
        )


def test_p5_stage_001_output_abstention_and_coverage_are_operational() -> None:
    targets = _load(TARGETS)
    output = targets["model_output_schema"]
    coverage = targets["coverage_contract"]

    assert output["positive_assertion_probability_minimum"] == 0.70
    assert output["negative_assertion_probability_maximum"] == 0.30
    assert "ABSTAIN_otherwise" in output["precedence"]
    assert output["uncalibrated_score_may_not_create_assertion"] is True
    assert "false when parent/context is known not applicable" in output["fields"][
        "applicability"
    ]
    assert "null when required parent/context is unavailable or unknown" in output["fields"][
        "applicability"
    ]
    assert set(output["fields"]["decision_state"]) == {
        "ASSERT_POSITIVE",
        "ASSERT_NEGATIVE",
        "ABSTAIN",
        "NOT_APPLICABLE",
        "UNAVAILABLE",
        "X",
    }

    assert coverage["primary_coverage_gate_metric"] == "raw_score_coverage"
    assert coverage["primary_coverage_minimum"] == 0.80
    assert coverage["raw_score_coverage"][
        "model_abstain_with_finite_raw_score_remains_in_numerator"
    ] is True
    assert coverage[
        "abstention_never_removes_case_from_AP_or_Brier_when_probability_exists"
    ] is True
    assert coverage["head_ineligible_handling"] == (
        "exclude_from_confirmatory_macro_head_set_and_report_in_k_of_12"
    )


def test_p5_stage_001_baseline_and_decision_table_are_frozen() -> None:
    request = _load(REQUEST)
    comparison = request["comparison_contract"]
    b_star = comparison["simple_baseline_B_star"]

    assert b_star["candidate_models"] == ["L", "G"]
    assert b_star["selection_partition"] == "development_validation_only"
    assert b_star["metric"] == "macro_average_precision"
    assert b_star["direction"] == "maximize"
    assert b_star["tie_breaker"] == "L_preferred_for_simplicity"
    assert b_star["neither_valid"] == "INCONCLUSIVE_NO_B_STAR"

    evaluation = request["primary_evaluation"]
    assert evaluation["coverage_metric"] == (
        "raw_score_coverage_on_reference_eligible_applicable_definite_cases"
    )
    assert evaluation["Brier_noninferiority"]["signed_difference"] == (
        "candidate_minus_comparator"
    )
    assert evaluation["severe_error"]["signed_difference"] == (
        "candidate_severe_error_rate_minus_comparator_severe_error_rate"
    )
    assert evaluation["severe_error"]["rule"] == (
        "paired_difference_95pct_interval_upper_bound_lte_0.02"
    )

    table = evaluation["contrast_decision_table"]
    assert set(table["registered_contrasts"]) == {
        "T0_minus_B_star",
        "T1_minus_T0",
        "T1_minus_B_star",
    }
    assert table["sole_confirmatory_contrast"] == "T1_minus_B_star"
    assert table["registered_contrasts"]["T0_minus_B_star"]["role"] == (
        "component_diagnostic"
    )
    assert table["registered_contrasts"]["T1_minus_T0"]["role"] == (
        "component_diagnostic"
    )
    assert table["registered_contrasts"]["T1_minus_B_star"]["role"] == (
        "sole_line_level_confirmatory"
    )
    rules = table["confirmatory_decision_precedence"]
    assert [rule["order"] for rule in rules] == list(range(1, 8))
    assert [rule["disposition"] for rule in rules] == [
        "INVALID_EVIDENCE",
        "COMPUTE_LIMITED",
        "INCONCLUSIVE",
        "NO_INCREMENT",
        "NO_INCREMENT",
        "NO_INCREMENT",
        "SUPPORTED_INCREMENT",
    ]
    assert "B_star_unavailable_or_invalid" in rules[2]["when_any"]
    assert "candidate_raw_score_coverage_lt_0.80" in rules[3]["when_any"]
    assert (
        "Brier_candidate_minus_comparator_95pct_upper_bound_gt_0.01"
        in rules[4]["when_any"]
    )
    assert "AP_paired_difference_95pct_lower_bound_lte_0" in rules[5]["when_any"]
    assert "AP_paired_difference_95pct_lower_bound_gt_0" in rules[6]["when_all"]
    assert table["component_diagnostic_disposition"][
        "line_level_SUPPORTED_INCREMENT_authority"
    ] is False


def test_p5_stage_001_line_level_metric_construction_is_deterministic() -> None:
    request = _load(REQUEST)
    evaluation = request["primary_evaluation"]
    heads = evaluation["confirmatory_head_set"]
    metric = evaluation["line_level_metric_construction"]

    assert heads["frozen_by"] == "B_before_E_and_before_any_model_result"
    assert heads["same_head_set_for"] == [
        "macro_average_precision",
        "macro_Brier_difference",
        "macro_severe_error_difference",
    ]
    assert heads["post_result_head_drop_forbidden"] is True
    assert heads["any_frozen_head_metric_unavailable"] == "INCONCLUSIVE"

    assert metric["head_weighting"] == (
        "equal_weight_macro_over_the_exact_B_frozen_confirmatory_head_set"
    )
    assert metric["per_head_Brier_difference"].startswith(
        "for each frozen head and paired calibrated-probability case"
    )
    assert metric["per_head_severe_error_difference"].startswith(
        "for each frozen head and paired calibrated-probability case"
    )
    blocks = metric["temporal_blocks"]
    assert blocks["protocol_ref"] == "primary_evaluation.bootstrap_protocol"
    assert blocks["per_resample_aggregation"].startswith(
        "compute each head metric/difference on cases in sampled blocks"
    )
    assert metric["Brier_line_level_statistic"].startswith(
        "macro equal-weight mean of per-head paired Brier differences"
    )
    assert metric["severe_error_line_level_statistic"].startswith(
        "macro equal-weight mean of per-head paired severe-error-rate differences"
    )

    rules = evaluation["contrast_decision_table"]["confirmatory_decision_precedence"]
    inconclusive = next(rule for rule in rules if rule["disposition"] == "INCONCLUSIVE")
    assert "confirmatory_head_metric_unavailable" in inconclusive["when_any"]
    assert "constructed_block_count_lt_8" in inconclusive["when_any"]
    assert "any_constructed_block_not_jointly_effective" in inconclusive["when_any"]
    assert (
        "fewer_than_2000_valid_shared_replicates_after_4000_candidates"
        in inconclusive["when_any"]
    )


def test_p5_stage_001_bootstrap_protocol_is_fully_reproducible() -> None:
    request = _load(REQUEST)
    protocol = request["primary_evaluation"]["bootstrap_protocol"]

    assert protocol["protocol_id"] == "paired-calendar-block-bootstrap-v1"
    partition = protocol["block_partition"]
    assert partition["base_block_sessions"] == 22
    assert partition["all_evaluation_dates_used_when_at_least_one_full_block_exists"] is True
    assert partition["block_boundaries_frozen_by"] == "B evaluation manifest before E"
    assert "append it to the immediately preceding full block" in partition["algorithm"]
    assert "EVERY constructed block" in protocol["effective_block_requirement"]
    assert "No constructed block is dropped" in protocol["effective_block_requirement"]

    draws = protocol["bootstrap_draws"]
    assert draws["target_valid_replicates"] == 2000
    assert draws["maximum_candidate_replicates"] == 4000
    assert draws["seed_material"] == (
        "request_id + ':' + contrast_id + ':paired-calendar-block-bootstrap-v1'"
    )
    assert "SHA256" in draws["deterministic_index_rule"]
    assert draws["shared_indices"].startswith("the exact same block-index draws")

    validity = protocol["replicate_validity"]
    assert "at least one positive and one negative" in validity["AP"]
    assert validity["replacement_policy"].startswith(
        "scan deterministic candidate replicate ids r=0..3999"
    )

    interval = protocol["confidence_interval"]
    assert interval == {
        "method": "empirical_nearest_rank_percentile",
        "confidence_level": 0.95,
        "valid_replicate_count": 2000,
        "sorting": "ascending numeric statistic",
        "lower": "50th smallest value (one-based rank ceil(0.025*2000)=50; zero-based index 49)",
        "upper": (
            "1950th smallest value (one-based rank ceil(0.975*2000)=1950; "
            "zero-based index 1949)"
        ),
        "interpolation": "none",
    }


def test_p5_stage_001_owner_and_design_review_evidence_are_bound() -> None:
    request = _load(REQUEST)
    authorization = _load(
        ROOT / "config/research/p5-stage-001/authorization.v1.json"
    )
    review = _load(
        ROOT / "config/research/p5-stage-001/design-review-evidence.v1.json"
    )
    validation = _load(
        ROOT / "config/research/p5-stage-001/validation-evidence.v1.json"
    )

    consumer = authorization["concrete_downstream_consumer"]
    assert consumer["consumer_id"] == (
        "p5.research-scoring.proposal-to-rule-shadow-review"
    )
    assert consumer["does_not_change_policy_effect"] is True
    assert consumer["does_not_issue_orders"] is True
    assert authorization["successor_activation"] == {
        "B_requires_explicit_owner_activation": True,
        "C_requires_explicit_owner_activation": True,
        "D_requires_explicit_owner_activation": True,
    }

    assert request["downstream_value_card"]["consumer_kind"] == (
        "existing_p5_research_scoring_lifecycle_review"
    )
    assert review["human_experts"] is False
    assert review["artifact"]["artifact_id"] == "42737764950ac91851a23f2c81331e85"
    assert review["artifact"]["archive_sha256"] == (
        "sha256:82faeddf1573631d2915d8578ab491cb86e6d742cad58d0c19545587e532807e"
    )
    assert set(review["roles"]) == {"quant", "ml", "data", "engineering"}
    assert all(review["roles"][role]["exit_code"] == 0 for role in review["roles"])

    assert validation["artifact"]["artifact_id"] == "82680dff61dffb4af983bc6b85e59f4e"
    assert validation["artifact"]["sha256"] == (
        "sha256:3c871be21087ceb7cb6b14165b3121d2de78c4cada8cbf5ce9428744bb2edfa8"
    )
    assert validation["focused_contract"]["result"] == "16_passed"
    assert validation["equivalent_repository_check"]["result"] == (
        "2759_passed_7_skipped_3_deselected"
    )
    assert validation["baseline_reproduction"]["disposition"] == (
        "pre_existing_main_baseline_failure_not_caused_by_A"
    )
