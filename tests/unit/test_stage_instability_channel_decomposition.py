from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from quanthus.policy import find_project_root
from quanthus.stage_learning import instability_channel_decomposition as subject

ROOT = find_project_root()


@dataclass(frozen=True)
class _Candidate:
    expansion_profile_id: str
    broad_profile_id: str
    marker: str

    @property
    def thresholds(self) -> str:
        return f"{self.expansion_profile_id}|{self.broad_profile_id}|{self.marker}"

    def to_dict(self) -> dict[str, str]:
        return {
            "candidate_id": self.thresholds,
            "expansion_profile_id": self.expansion_profile_id,
            "broad_profile_id": self.broad_profile_id,
            "marker": self.marker,
        }


def test_p018_config_is_diagnostic_only_and_preserves_partitions() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    assert config["work_item"] == "P3-STAGE-018-A"
    surface = config["diagnostic_surface"]
    assert surface["reference_case_count"] == 252
    assert surface["cell_count"] == 8
    assert surface["profile_count"] == 9
    assert surface["new_profile_coordinates"] is False
    assert surface["new_thresholds"] is False
    assert surface["threshold_recompute"] is False
    assert surface["new_semantic_labels"] is False
    metrics = config["metrics"]
    assert metrics["candidate_selection"] is False
    assert metrics["eligibility_gate"] is False
    assert metrics["successor_qualification"] is False
    authority = config["authority"]
    assert authority["artifact_only_diagnostic"] is True
    assert authority["channel_decomposition"] is True
    assert authority["candidate_scoring_for_selection"] is False
    assert authority["threshold_change"] is False
    assert authority["dwd_market_read"] is False
    assert authority["preserved_2021_read"] is False
    assert authority["2022_2024_read"] is False
    assert authority["2025_read"] is False


def test_attribution_counts_separates_expansion_broad_overlap_and_neither() -> None:
    case_ids = ["a", "b", "c", "d", "e"]
    references = {
        "a": {"structural_instability_20": "TRUE"},
        "b": {"structural_instability_20": "FALSE"},
        "c": {"structural_instability_20": "TRUE"},
        "d": {"structural_instability_20": "FALSE"},
        "e": {"structural_instability_20": "AMBIGUOUS"},
    }
    expansion = {"a": "TRUE", "b": "TRUE", "c": "FALSE", "d": "TRUE", "e": "FALSE"}
    broad = {"a": "FALSE", "b": "TRUE", "c": "TRUE", "d": "FALSE", "e": "FALSE"}
    counts = subject._attribution_counts(
        case_ids=case_ids,
        references=references,
        expansion=expansion,
        broad=broad,
    )
    assert counts["expansion_true_count"] == 3
    assert counts["broad_true_count"] == 2
    assert counts["overlap_true_count"] == 1
    assert counts["expansion_only_count"] == 2
    assert counts["broad_only_count"] == 1
    assert counts["neither_count"] == 1
    assert counts["expansion_only_true_positive"] == 1
    assert counts["expansion_only_false_positive"] == 1
    assert counts["broad_only_true_positive"] == 1
    assert counts["broad_only_false_positive"] == 0
    assert counts["overlap_true_positive"] == 0
    assert counts["overlap_false_positive"] == 1


def test_decomposition_reports_all_three_views_without_selecting(monkeypatch) -> None:
    cases = {"case-a": {"case_id": "case-a"}, "case-b": {"case_id": "case-b"}}
    strata = {
        "case-a": {"cell": "ERA/BOARD", "era": "ERA", "board_family": "BOARD"},
        "case-b": {"cell": "ERA/BOARD", "era": "ERA", "board_family": "BOARD"},
    }
    references = {
        "case-a": {"structural_instability_20": "TRUE"},
        "case-b": {"structural_instability_20": "FALSE"},
    }
    bases = {"ERA/BOARD": {"marker": "cell"}}
    profiles = [
        {
            "profile_id": f"p-{i}-{j}",
            "expansion_profile_id": f"exp-{i}",
            "broad_profile_id": f"broad-{j}",
            "cell_candidates": {
                "ERA/BOARD": _Candidate(f"exp-{i}", f"broad-{j}", "cell").to_dict()
            },
        }
        for i in range(3)
        for j in range(3)
    ]

    monkeypatch.setattr(
        subject,
        "instability_candidate_grid",
        lambda basis: tuple(
            _Candidate(f"exp-{i}", f"broad-{j}", str(basis["marker"]))
            for i in range(3)
            for j in range(3)
        ),
    )
    monkeypatch.setattr(subject, "_measurements", lambda case: case["case_id"])

    def fake_project(measurement, thresholds):
        expansion = "TRUE" if measurement == "case-a" else "FALSE"
        broad = "TRUE" if "broad-0" in thresholds else "FALSE"
        status = "TRUE" if "TRUE" in {expansion, broad} else "FALSE"
        return SimpleNamespace(
            expanding_disorder_20=expansion,
            broad_reversal_disorder_20=broad,
            status=status,
        )

    monkeypatch.setattr(subject, "project_positive_instability", fake_project)
    metric_calls = []

    def fake_metrics(**kwargs):
        predictions = dict(kwargs["predictions"])
        metric_calls.append(predictions)
        return {
            "pooled": {"predictions": sum(v == "TRUE" for v in predictions.values())},
            "macro": {"precision": "diagnostic"},
            "per_cell": {"ERA/BOARD": {"predictions": 1}},
        }

    monkeypatch.setattr(subject, "_macro_atomic_metrics", fake_metrics)
    output = subject.decompose_profiles(
        cases=cases,
        strata=strata,
        references=references,
        bases=bases,
        profile_grid={"profiles": profiles},
    )
    assert len(output) == 9
    assert len(metric_calls) == 27
    assert all(
        set(row)
        == {
            "profile_id",
            "expansion_profile_id",
            "broad_profile_id",
            "expanding_disorder_20",
            "broad_reversal_disorder_20",
            "parent_or",
            "attribution",
        }
        for row in output
    )
    assert output[0]["attribution"]["expansion_true_count"] == 1
    assert output[0]["attribution"]["broad_true_count"] == 2
    assert output[0]["attribution"]["overlap_true_count"] == 1
