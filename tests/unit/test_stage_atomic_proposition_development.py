from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from quanthus.stage_learning import atomic_proposition_development as api
from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary
from quanthus.stage_learning.orderly_consolidation_projection import OrderlyConsolidationThresholds


def test_envelope_channel_requires_resolved_both_sides_and_two_alternations() -> None:
    base = {
        "status": "AVAILABLE",
        "descriptors": {
            "resolved_escape_count": 3,
            "resolved_escape_side_alternation_count": 2,
            "resolved_escape_both_sides": True,
        },
    }
    assert api.envelope_failed_resolution_truth(base) == "TRUE"
    same_side = {
        **base,
        "descriptors": {
            **base["descriptors"],
            "resolved_escape_side_alternation_count": 0,
            "resolved_escape_both_sides": False,
        },
    }
    assert api.envelope_failed_resolution_truth(same_side) == "FALSE"
    assert api.envelope_failed_resolution_truth({"status": "UNAVAILABLE"}) == "UNKNOWN"


def test_order_atomic_core_has_no_relocation_veto(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "case_features",
        lambda _case: {
            "efficiency20_abs": Decimal("0.1"),
            "range_width20": Decimal("0.1"),
            "width_change20": Decimal("-0.5"),
        },
    )
    candidate = SimpleNamespace(
        thresholds=OrderlyConsolidationThresholds(
            efficiency20_abs_max=Decimal("0.2"),
            range_width20_max=Decimal("0.2"),
            contraction_magnitude20_min=Decimal("0.4"),
        )
    )
    assert api.order_truth({}, candidate) == "TRUE"


@dataclass(frozen=True)
class FakeInstabilityCandidate:
    candidate_id: str

    def to_dict(self) -> dict[str, str]:
        return {"candidate_id": self.candidate_id}


@dataclass(frozen=True)
class FakeOrderCandidate:
    order_profile_id: str

    def to_dict(self) -> dict[str, str]:
        return {"candidate_id": self.order_profile_id, "order_profile_id": self.order_profile_id}


def _reference_case(index: int) -> dict[str, str]:
    if index < 30:
        atomics = {
            "structural_instability_20": "TRUE",
            "coherent_directional_relocation_20": "FALSE",
            "orderly_consolidation_20": "FALSE",
        }
    elif index < 55:
        atomics = {
            "structural_instability_20": "FALSE",
            "coherent_directional_relocation_20": "FALSE",
            "orderly_consolidation_20": "TRUE",
        }
    elif index < 75:
        atomics = {
            "structural_instability_20": "TRUE",
            "coherent_directional_relocation_20": "TRUE",
            "orderly_consolidation_20": "FALSE",
        }
    else:
        atomics = {
            "structural_instability_20": "FALSE",
            "coherent_directional_relocation_20": "FALSE",
            "orderly_consolidation_20": "FALSE",
        }
    projected = project_atomic_primary(atomics, available=True)
    return {**atomics, "projected_primary_reference": projected["status"]}


def test_score_freezes_atomics_independently_before_projection(monkeypatch) -> None:
    cases = [{"case_id": f"case-{index}"} for index in range(144)]
    references = {
        f"case-{index}": _reference_case(index)
        for index in range(144)
    }
    envelopes = {
        f"case-{index}": {
            "status": "AVAILABLE",
            "descriptors": {
                "resolved_escape_count": 0,
                "resolved_escape_side_alternation_count": 0,
                "resolved_escape_both_sides": False,
            },
        }
        for index in range(144)
    }
    instability_candidates = (
        FakeInstabilityCandidate("instability-a"),
        FakeInstabilityCandidate("instability-b"),
    )
    order_candidates = (
        FakeOrderCandidate("order-a"),
        FakeOrderCandidate("order-b"),
    )
    monkeypatch.setattr(api, "instability_candidate_grid", lambda _basis: instability_candidates)
    monkeypatch.setattr(api, "stage2_candidate_grid", lambda _basis: order_candidates)

    def fake_instability(case, candidate, _envelope):
        index = int(str(case["case_id"]).split("-")[1])
        truth = references[str(case["case_id"])]["structural_instability_20"]
        enhanced = truth
        if candidate.candidate_id == "instability-b" and index in {0, 1, 80}:
            enhanced = "TRUE" if truth == "FALSE" else "FALSE"
        base = enhanced
        if candidate.candidate_id == "instability-a" and index == 0:
            base = "FALSE"
        return {"base": base, "envelope": "TRUE" if index == 0 else "FALSE", "enhanced": enhanced}

    monkeypatch.setattr(api, "instability_truth", fake_instability)
    monkeypatch.setattr(
        api,
        "relocation_truth",
        lambda case: references[str(case["case_id"])]["coherent_directional_relocation_20"],
    )

    def fake_order(case, candidate):
        index = int(str(case["case_id"]).split("-")[1])
        truth = references[str(case["case_id"])]["orderly_consolidation_20"]
        if candidate.order_profile_id == "order-b" and index in {30, 31, 100}:
            return "TRUE" if truth == "FALSE" else "FALSE"
        return truth

    monkeypatch.setattr(api, "order_truth", fake_order)
    result = api.score_and_freeze(cases, references, {}, envelopes)
    assert result["disposition"] == "EXACT_ATOMIC_AND_PROJECTION_FREEZE_FOR_PRESERVED_2021"
    assert result["exact_freeze"]["instability_candidate"]["candidate_id"] == "instability-a"
    assert result["exact_freeze"]["order_candidate"]["order_profile_id"] == "order-a"
    assert result["joint_pair_search_count"] == 0
    assert result["preserved_2021_read_authorized"] is True
    assert result["projection"]["stage0"]["positive_reference_coverage"] == Decimal("1")
    assert result["projection"]["stage2"]["positive_reference_coverage"] == Decimal("1")
