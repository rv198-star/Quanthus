from __future__ import annotations

from decimal import Decimal

from quanthus.stage_learning import directional_boundary_development as api


def case(case_id: str, closes: list[str], *, efficiency="0.30", width="0.30",
         width_change="0.30", center="0.10"):
    assert len(closes) == 21
    history = [
        [f"sha256:{index:064x}", f"2020-01-{index + 1:02d}", value, value, value, "epoch", None]
        for index, value in enumerate(closes)
    ]
    return {
        "case_id": case_id,
        "primary_available": True,
        "primary": {
            "directional_efficiency": {"reason": "available", "value": efficiency},
            "range_width": {"reason": "available", "value": width},
            "width_change": {"reason": "available", "value": width_change},
            "center_shift": {"reason": "available", "value": center},
        },
        "history": history,
    }


def monotonic(start: int, step: int) -> list[str]:
    return [str(Decimal(start) + Decimal(step) * index) for index in range(21)]


def oscillating() -> list[str]:
    return [str(100 + (6 if index % 2 else -6)) for index in range(20)] + ["100"]


def test_preregistered_grid_is_exactly_27_and_has_no_stage2_search() -> None:
    config = api.load_config()
    grid = api.candidate_grid()
    assert len(grid) == 27
    assert len({item.candidate_id for item in grid}) == 27
    assert config["authority"]["maximum_parameter_trials"] == 27
    assert config["authority"]["stage_2_threshold_research"] is False
    assert config["authority"]["2022_2024_access"] is False
    assert config["authority"]["2025_access"] is False


def test_relocation_guard_removes_coherent_repricing_but_not_oscillation() -> None:
    candidate = api.RelocationCandidate(Decimal("0.10"), Decimal("0.50"), Decimal("0.40"))
    directional = case("directional", monotonic(100, 2))
    disorder = case("disorder", oscillating(), efficiency="0.05")
    assert api.old_stage0(directional)
    assert api.relocation_guard(directional, candidate)
    assert not api.candidate_stage0(directional, candidate)
    assert api.old_stage0(disorder)
    assert not api.relocation_guard(disorder, candidate)
    assert api.candidate_stage0(disorder, candidate)


def test_zero_net_path_never_manufactures_relocation_guard() -> None:
    candidate = api.RelocationCandidate(Decimal("0.10"), Decimal("0.50"), Decimal("0.60"))
    zero_net_closes = ["100", "106", "94"] * 6 + ["100", "106", "100"]
    zero_net = case("zero", zero_net_closes, efficiency="0.05")
    features = api.case_features(zero_net)
    assert features["counter_direction_excursion_share20"] is None
    assert not api.relocation_guard(zero_net, candidate)


def test_stage2_rule_is_unchanged_and_separate_from_stage0_repair() -> None:
    orderly = case(
        "orderly", monotonic(100, 0), efficiency="0.10", width="0.10",
        width_change="-0.30", center="0.01",
    )
    assert api.unchanged_stage2(orderly)
    assert not api.old_stage0(orderly)
    for candidate in api.candidate_grid():
        assert not api.candidate_stage0(orderly, candidate)


def test_metrics_ignore_ambiguous_for_definite_precision_and_fpr() -> None:
    candidate = api.RelocationCandidate(Decimal("0.10"), Decimal("0.50"), Decimal("0.40"))
    cases = [
        case("tp", oscillating(), efficiency="0.05"),
        case("fp", monotonic(100, 1)),
        case("amb", oscillating(), efficiency="0.05"),
    ]
    refs = {"tp": "STAGE_0", "fp": "NEITHER", "amb": "AMBIGUOUS"}
    metrics = api.stage0_metrics(cases, refs, candidate=candidate)
    assert metrics["definite_positive_references"] == 1
    assert metrics["definite_negative_references"] == 1
    assert metrics["ambiguous_references"] == 1
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 0
    assert metrics["precision"] == Decimal(1)
    assert metrics["definite_negative_false_positive_rate"] == Decimal(0)
