from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning import stratum_normalized_instability as subject

ROOT = find_project_root()


@dataclass(frozen=True)
class _Candidate:
    expansion_profile_id: str
    broad_profile_id: str
    basis_marker: str

    def to_dict(self) -> dict[str, str]:
        return {
            "candidate_id": (
                f"{self.expansion_profile_id}-{self.broad_profile_id}-{self.basis_marker}"
            ),
            "expansion_profile_id": self.expansion_profile_id,
            "broad_profile_id": self.broad_profile_id,
            "basis_marker": self.basis_marker,
        }


def test_p017_config_retires_envelope_without_relaxing_absolute_gates() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    assert config["work_item"] == "P3-STAGE-017-A"
    assert config["development_surface"]["case_count"] == 252
    assert config["normalization"]["kind"] == "cell_local_unlabeled_quantile_basis"
    assert config["normalization"]["label_or_reference_visibility_during_basis_build"] is False
    assert config["candidate_surface"]["candidate_count"] == 9
    envelope = config["envelope_successor_disposition"]
    assert envelope["p012_fixed_additive_channel"] == "RETIRED_FROM_P017_ELIGIBILITY"
    assert envelope["post_result_threshold_tuning"] is False
    assert envelope["new_envelope_candidate_search"] is False
    assert envelope["p012_envelope_specific_incremental_gate"].startswith("NOT_APPLICABLE")
    gate = config["quality_gate"]
    assert gate["minimum_predictions"] == 18
    assert gate["minimum_macro_precision"] == "0.78"
    assert gate["minimum_macro_positive_reference_coverage"] == "0.70"
    assert gate["maximum_macro_definite_negative_false_positive_rate"] == "0.10"
    assert gate["numeric_threshold_relaxation"] is False
    authority = config["authority"]
    assert authority["fixed_relocation_scoring"] is False
    assert authority["order_scoring"] is False
    assert authority["projection_compatibility"] is False
    assert authority["preserved_2021_read"] is False
    assert authority["2025_read"] is False


def test_cell_basis_build_is_unlabeled_and_keeps_shared_profile_topology(monkeypatch) -> None:
    cases = {}
    strata = {}
    for cell_index in range(8):
        cell = f"ERA{cell_index}/BOARD{cell_index}"
        for member in range(24):
            case_id = f"case-{cell_index}-{member}"
            cases[case_id] = {"case_id": case_id}
            strata[case_id] = {
                "era": f"ERA{cell_index}",
                "board_family": f"BOARD{cell_index}",
                "cell": cell,
                "window_id": f"W{cell_index}",
            }

    monkeypatch.setattr(
        subject,
        "case_features",
        lambda case: {
            "range_width20": Decimal("0.2"),
            "width_change20": Decimal("0.1"),
            "efficiency20_abs": Decimal("0.3"),
            "range_traversal20": Decimal("0.4"),
            "counter_direction_excursion_share20": Decimal("0.5"),
        },
    )
    basis_calls: list[list[dict[str, str | None]]] = []

    def fake_basis(rows):
        copied = [dict(row) for row in rows]
        basis_calls.append(copied)
        return {
            "schema_version": "fake-basis",
            "marker": f"b{len(basis_calls)}",
            "observation_count": len(copied),
            "labels_used": False,
        }

    def fake_grid(basis):
        return tuple(
            _Candidate(f"exp-{i}", f"broad-{j}", str(basis["marker"]))
            for i in range(3)
            for j in range(3)
        )

    monkeypatch.setattr(subject, "instability_threshold_basis", fake_basis)
    monkeypatch.setattr(subject, "instability_candidate_grid", fake_grid)
    bases, grid = subject._cell_basis_and_grid(
        cases, strata, minimum_cases_per_cell=24
    )
    assert len(bases) == 8
    assert len(basis_calls) == 8
    assert all(len(rows) == 24 for rows in basis_calls)
    assert all(
        "reference" not in key and "label" not in key
        for rows in basis_calls
        for row in rows
        for key in row
    )
    assert grid["candidate_count"] == 9
    assert grid["labels_used"] is False
    assert grid["reference_opinions_used"] is False
    assert grid["envelope_additive_channel_included"] is False
    assert len({(p["expansion_profile_id"], p["broad_profile_id"]) for p in grid["profiles"]}) == 9
    assert all(len(profile["cell_candidates"]) == 8 for profile in grid["profiles"])


def test_score_profiles_freezes_instability_only_and_never_uses_envelope(monkeypatch) -> None:
    case_ids = [f"case-{index}" for index in range(8)]
    cases = {case_id: {"case_id": case_id} for case_id in case_ids}
    strata = {
        case_id: {
            "era": f"ERA{index}",
            "board_family": f"BOARD{index}",
            "cell": f"ERA{index}/BOARD{index}",
        }
        for index, case_id in enumerate(case_ids)
    }
    references = {
        case_id: {"structural_instability_20": "TRUE" if index % 2 == 0 else "FALSE"}
        for index, case_id in enumerate(case_ids)
    }
    bases = {
        f"ERA{index}/BOARD{index}": {"marker": f"m{index}"}
        for index in range(8)
    }
    profiles = [
        {
            "profile_id": f"p{i}{j}",
            "expansion_profile_id": f"exp-{i}",
            "broad_profile_id": f"broad-{j}",
            "cell_candidates": {
                cell: _Candidate(f"exp-{i}", f"broad-{j}", str(basis["marker"])).to_dict()
                for cell, basis in bases.items()
            },
        }
        for i in range(3)
        for j in range(3)
    ]

    def fake_grid(basis):
        return tuple(
            _Candidate(f"exp-{i}", f"broad-{j}", str(basis["marker"]))
            for i in range(3)
            for j in range(3)
        )

    monkeypatch.setattr(subject, "instability_candidate_grid", fake_grid)
    monkeypatch.setattr(
        subject,
        "candidate_instability",
        lambda case, candidate: "TRUE" if candidate.expansion_profile_id == "exp-0" else "FALSE",
    )

    def fake_metrics(**kwargs):
        true_predictions = sum(value == "TRUE" for value in kwargs["predictions"].values())
        macro_precision = Decimal("0.90") if true_predictions else Decimal("0.20")
        return {
            "pooled": {
                "complete_reference_count": 252,
                "definite_positive_references": 40,
                "definite_negative_references": 147,
                "predictions": max(true_predictions, 18),
                "precision": macro_precision,
                "positive_reference_coverage": Decimal("0.80"),
                "definite_negative_false_positive_rate": Decimal("0.05"),
            },
            "macro": {
                "precision": macro_precision,
                "positive_reference_coverage": Decimal("0.80"),
                "definite_negative_false_positive_rate": Decimal("0.05"),
            },
            "per_cell": {},
        }

    monkeypatch.setattr(subject, "_macro_atomic_metrics", fake_metrics)
    monkeypatch.setattr(
        subject,
        "_passes_atomic_macro_gate",
        lambda **kwargs: kwargs["metrics"]["macro"]["precision"] >= Decimal("0.78"),
    )
    prereg = {"atomic_quality_gates": {"instability": {}}, "development_sufficiency": {}}
    result = subject._score_profiles(
        cases=cases,
        strata=strata,
        references=references,
        profile_grid={"profiles": profiles},
        bases=bases,
        prereg=prereg,
    )
    assert result["disposition"] == "PASS_STRATUM_NORMALIZED_INSTABILITY_FREEZE"
    assert result["selected"] is not None
    assert result["selected"]["expansion_profile_id"] == "exp-0"
    assert set(result["selected_predictions"]) == set(case_ids)
    assert all(
        set(value) == {"structural_instability_20", "era", "board_family", "cell"}
        for value in result["selected_predictions"].values()
    )
