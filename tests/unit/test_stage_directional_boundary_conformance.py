from __future__ import annotations

from quanthus.stage_learning import directional_boundary_conformance as api
from quanthus.structure_v2_inputs import digest


def test_conformance_contract_has_no_threshold_or_runtime_authority() -> None:
    config = api.load_config()
    assert digest(config) == (
        "sha256:defec6a0ec09f209e5608274febdf3a8c76f7907db4bc44c8c2a9061dff022bb"
    )
    assert config["authority"] == {
        "synthetic_conformance": True,
        "read_existing_stage_007_sealed_evidence": True,
        "write_new_market_data": False,
        "market_data_acquisition": False,
        "runtime_job_submission": False,
        "parameter_search": False,
        "stage_007_threshold_learning": False,
        "stage_007_qualification": False,
        "stage_2_change": False,
        "2022_2024_access": False,
        "2025_access": False,
        "official_labels": False,
        "daily_or_strategy": False,
        "trading": False,
    }
    assert config["pass_conditions"]["threshold_search_count"] == 0
    assert config["pass_conditions"]["parameter_trials"] == 0
    assert config["pass_conditions"]["new_market_data_reads"] == 0
    assert config["pass_conditions"]["runtime_jobs"] == 0


def test_frozen_exposed_ledger_and_design_regression_are_exact() -> None:
    ledger = api.load_frozen_ledger()
    assert len(ledger) == 160
    config = api.load_config()
    assert config["frozen_group_definition"]["expected_counts"] == {
        "old_stage_0_true_positive": 28,
        "old_stage_0_false_positive_neither": 12,
    }
    frozen = config["frozen_design_regression"]
    assert frozen["range_traversal_20_median"] == {
        "old_stage_0_true_positive": "0.3261",
        "old_stage_0_false_positive_neither": "0.7695",
    }
    assert frozen["counter_direction_excursion_share_20_median"] == {
        "old_stage_0_true_positive": "0.6286",
        "old_stage_0_false_positive_neither": "0.3272",
    }
