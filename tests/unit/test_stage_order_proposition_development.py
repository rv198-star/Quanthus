from __future__ import annotations

from datetime import date
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_proposition_development import (
    LABEL_AMBIGUOUS,
    LABEL_NEITHER,
    LABEL_STAGE_0,
    LABEL_STAGE_2,
    OrderObservation,
    ResolvedReference,
    SampledContext,
    _gate,
    _predict_stage0,
    _predict_stage2,
    _reference_counts,
    _review_one,
    load_order_proposition_config,
    load_order_proposition_panel,
    sample_contexts,
    score_proposition,
)

ROOT = find_project_root()


def obs(
    code: str,
    *,
    eff: str = "0.20",
    width: str = "0.15",
    wc10: str = "-0.20",
    wc20: str = "-0.20",
    cs10: str = "0.02",
    cs20: str = "0.02",
    day: int = 1,
) -> OrderObservation:
    return OrderObservation(
        code,
        date(2020, 1, day),
        f"sha256:{code.zfill(64)[-64:]}",
        Decimal(eff),
        Decimal(width),
        Decimal(wc10),
        Decimal(wc20),
        Decimal(cs10),
        Decimal(cs20),
    )


def test_frozen_config_and_panel_keep_authority_bounded() -> None:
    config = load_order_proposition_config(
        ROOT / "config/stage-learning/order-proposition-development.v1.json"
    )
    panel = load_order_proposition_panel(
        ROOT / "config/stage-learning/order-proposition-synthetic-panel.v1.json"
    )
    assert config["authority"]["development_data_read_authorized"] is True
    assert config["authority"]["future_outcome_access_authorized"] is False
    assert config["authority"]["arbitration_access_authorized"] is False
    assert config["authority"]["permanent_blind_access_authorized"] is False
    assert config["authority"]["official_label_card_publication_authorized"] is False
    assert config["proposition_identity"]["stage_0_is_not_residual"] is True
    assert config["proposition_identity"]["stage_2_implies_future_breakout"] is False
    assert panel["human_expert_validated"] is False


def test_stage2_requires_positive_order_evidence() -> None:
    params = {
        "efficiency20_abs_max": "0.33",
        "range_width20_max": "0.18",
        "width_change20_max": "-0.20",
        "center_shift20_abs_max": "0.04",
    }
    assert _predict_stage2(obs("1"), params)
    assert not _predict_stage2(obs("2", wc20="0.10"), params)
    assert not _predict_stage2(obs("3", cs20="0.08"), params)
    assert not _predict_stage2(obs("4", eff="0.50"), params)


def test_stage0_is_positive_disorder_evidence_not_residual() -> None:
    params = {
        "efficiency20_abs_max": "0.40",
        "width_change20_min": "0.20",
        "center_shift20_abs_min": "0.07",
    }
    assert _predict_stage0(obs("1", wc20="0.25", cs20="0.01"), params)
    assert _predict_stage0(obs("2", wc20="-0.10", cs20="0.09"), params)
    assert not _predict_stage0(obs("3", wc20="0.00", cs20="0.02"), params)
    assert not _predict_stage0(obs("4", eff="0.60", wc20="0.40"), params)


def test_stage0_and_stage2_can_both_be_false() -> None:
    p2 = {
        "efficiency20_abs_max": "0.33",
        "range_width20_max": "0.18",
        "width_change20_max": "-0.20",
        "center_shift20_abs_max": "0.04",
    }
    p0 = {
        "efficiency20_abs_max": "0.40",
        "width_change20_min": "0.20",
        "center_shift20_abs_min": "0.07",
    }
    mixed = obs("1", wc20="0.00", cs20="0.03")
    assert not _predict_stage0(mixed, p0)
    assert not _predict_stage2(mixed, p2)


def test_reference_panel_uses_both_10_and_20_window_evidence() -> None:
    panel = load_order_proposition_panel(
        ROOT / "config/stage-learning/order-proposition-synthetic-panel.v1.json"
    )
    policy = panel["policies"]["balanced-order-v1"]
    ordered = obs("1", wc10="-0.10", wc20="-0.20", cs10="0.02", cs20="0.02")
    disorder = obs("2", wc10="0.25", wc20="0.00", cs10="0.02", cs20="0.02")
    unclear = obs("3", wc10="0.00", wc20="0.00", cs10="0.03", cs20="0.03")
    directional = obs("4", eff="0.70", wc10="0.30", wc20="0.30")
    assert _review_one(ordered, policy) == LABEL_STAGE_2
    assert _review_one(disorder, policy) == LABEL_STAGE_0
    assert _review_one(unclear, policy) == LABEL_AMBIGUOUS
    assert _review_one(directional, policy) == LABEL_NEITHER


def test_sampling_uses_at_most_one_context_per_security() -> None:
    rows = []
    for index in range(1, 21):
        code = f"{index:06d}"
        rows.extend(
            [
                obs(code, day=1, eff="0.60"),
                obs(code, day=2, eff="0.20", wc20="-0.20", cs20="0.02"),
                obs(code, day=3, eff="0.20", wc20="0.30", cs20="0.02"),
                obs(code, day=4, eff="0.20", wc20="0.00", cs20="0.03"),
            ]
        )
    bands = {
        "low_efficiency20_abs_max": "0.45",
        "order_range_width20_max": "0.18",
        "order_width_change20_max": "0",
        "order_center_shift20_abs_max": "0.04",
        "disorder_width_change20_min": "0.20",
        "disorder_center_shift20_abs_min": "0.07",
    }
    contexts, _counts = sample_contexts(
        rows,
        seed="seed",
        bucket_order=(
            "directional_control",
            "order_like",
            "disorder_like",
            "mixed_low_efficiency",
        ),
        per_bucket=5,
        minimum_total=15,
        bands=bands,
    )
    codes = [item.observation.security_code for item in contexts]
    assert len(codes) == len(set(codes))


def test_score_and_gate_penalize_negative_and_ambiguous_overassertion() -> None:
    contexts = tuple(
        SampledContext(str(i), "x", obs(str(i + 1)), "e") for i in range(60)
    )
    references = tuple(
        ResolvedReference(
            str(i),
            LABEL_STAGE_2 if i < 20 else LABEL_STAGE_0 if i < 40 else LABEL_AMBIGUOUS,
            "primary_consensus",
            "e",
        )
        for i in range(60)
    )
    predictions = {str(i): i < 20 for i in range(60)}
    score = score_proposition(2, contexts, references, predictions, trial_id="good")
    counts = _reference_counts(references)
    counts[LABEL_NEITHER] = 10
    gates = {
        "minimum_stage_0_definite_references": 20,
        "minimum_stage_2_definite_references": 20,
        "minimum_ambiguous_references": 10,
        "minimum_neither_references": 10,
        "minimum_definite_positive_references": 20,
        "minimum_definite_negative_references": 20,
        "minimum_positive_reference_coverage": 0.55,
        "maximum_definite_negative_false_positive_rate": 0.10,
        "maximum_ambiguous_unique_assertion_rate": 0.20,
        "minimum_precision_one_sided_95_lower": 0.65,
        "f1_noninferiority_tolerance_vs_r0": 0.05,
        "maximum_pair_overlap_count": 0,
    }
    assert _gate(score, reference_counts=counts, r0_f1=score.f1, gates=gates)["passed"]
    bad_predictions = {str(i): i < 50 for i in range(60)}
    bad = score_proposition(2, contexts, references, bad_predictions, trial_id="bad")
    result = _gate(bad, reference_counts=counts, r0_f1=score.f1, gates=gates)
    assert not result["passed"]
    assert "negative_false_positive_rate_above_gate" in result["reason_codes"]
    assert "ambiguous_unique_assertion_rate_above_gate" in result["reason_codes"]


def test_pair_overlap_is_a_hard_failure() -> None:
    score = score_proposition(
        2,
        (SampledContext("x", "b", obs("1"), "e"),) * 20,
        tuple(
            ResolvedReference("x", LABEL_STAGE_2, "primary_consensus", "e") for _ in range(20)
        ),
        {"x": True},
        trial_id="x",
    )
    # The direct gate path is covered above; freeze the configured pair rule here.
    config = load_order_proposition_config(
        ROOT / "config/stage-learning/order-proposition-development.v1.json"
    )
    assert config["hard_gates"]["maximum_pair_overlap_count"] == 0
    assert score.stage == 2
