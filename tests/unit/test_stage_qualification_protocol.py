import json

from quanthus.policy import find_project_root

ROOT = find_project_root()
PROTOCOL = ROOT / "config/stage-learning/qualification-protocol.v2.json"


def _protocol() -> dict:
    return json.loads(PROTOCOL.read_text(encoding="utf-8"))


def test_successor_protocol_has_no_runtime_or_holdout_authority() -> None:
    raw = _protocol()
    assert raw["schema_version"] == "quanthus.stage-qualification-protocol.v2"
    assert raw["work_item"] == "P3-STAGE-001-I"
    assert raw["status"] == "preregistered_protocol_only_no_partition_authority"

    authority = raw["authority"]
    assert authority["protocol_design_authorized"] is True
    for key, value in authority.items():
        if key == "protocol_design_authorized":
            continue
        assert value is False, key

    partitions = raw["partition_policy"]
    assert partitions["consumed_partitions"] == ["2022-2024-arbitration"]
    assert partitions["fresh_confirmatory_reuse_forbidden"] == ["2022-2024-arbitration"]
    assert partitions["permanent_blind_2025_state"] == "unopened_unselected"
    assert partitions["next_holdout_partition"] is None
    assert partitions["partition_selection_requires_explicit_authorization"] is True


def test_sampling_keeps_original_targets_but_does_not_require_exact_quota() -> None:
    raw = _protocol()
    sampling = raw["sampling"]
    assert sampling["selection_order"] == [
        "started_activity",
        "early_held",
        "near_anchor_loss_negative",
        "reclaimed_ambiguity",
        "mature_held",
    ]
    assert sampling["target_caps"] == {
        "started_activity": 200,
        "early_held": 180,
        "near_anchor_loss_negative": 100,
        "reclaimed_ambiguity": 80,
        "mature_held": 80,
    }
    assert sampling["started_activity_target_caps"] == {
        "missing_or_lt_1": 25,
        "1_to_1_25": 60,
        "1_25_to_1_5": 60,
        "gte_1_5": 55,
    }
    assert sampling["early_held_target_caps"] == {"1": 60, "2": 60, "3": 60}
    assert sampling["one_context_per_episode"] is True
    assert sampling["exact_stratum_quota_required"] is False
    assert sampling["target_cap_shortfall_is_automatic_process_failure"] is False
    assert sampling["cross_stratum_backfill_authorized"] is False
    assert sampling["result_dependent_resampling_authorized"] is False
    assert sampling["realized_counts_must_be_published"] is True
    assert sampling["sampling_shortfall_reason_codes_required"] is True


def test_semantic_hard_gates_are_not_relaxed_after_f_failure() -> None:
    protocol = _protocol()["semantic_sufficiency"]
    original = json.loads(
        (ROOT / "config/stage-learning/qualification.v1.json").read_text(encoding="utf-8")
    )["hard_gates"]
    for key in (
        "minimum_definite_references_per_stage",
        "minimum_ambiguous_references_for_overassertion_audit",
        "maximum_unjudgeable_reference_rate",
        "minimum_positive_reference_coverage",
        "maximum_ambiguous_unique_assertion_rate",
        "maximum_definite_negative_false_positive_rate",
        "minimum_stage_precision_one_sided_95_lower",
        "macro_f1_noninferiority_tolerance_vs_r0",
        "require_zero_quantitative_audit_vetoes",
        "require_unique_episode_per_sampled_context",
    ):
        assert protocol[key] == original[key]
    assert protocol["when_any_gate_fails"] == "INCONCLUSIVE_OR_REJECTED_RETAIN_RESULT"


def test_protocol_cannot_mutate_candidate_or_upgrade_synthetic_reference_claim() -> None:
    raw = _protocol()
    candidate = raw["frozen_candidate"]
    assert candidate["digest"] == (
        "sha256:d492d12686be5f5ac84f87ba278afd873d9999aadc5fa5dc79a6f5f5985f176c"
    )
    assert candidate["mutation_authorized"] is False

    evaluation = raw["evaluation"]
    assert evaluation["candidate_and_r0_only"] is True
    assert evaluation["r1_search_trial_count"] == 0
    assert evaluation["r2_executed"] is False
    assert evaluation["future_settlement_read"] is False
    assert evaluation["strategy_result_read"] is False
    assert evaluation["reference_evidence_tier"] == "SYNTHETIC_EXPERT_CONSENSUS"
    assert evaluation["synthetic_reference_pass_claim_ceiling"] == (
        "bounded_shadow_external_semantic_only"
    )
    assert evaluation["qualified_label_version_allowed"] is False
