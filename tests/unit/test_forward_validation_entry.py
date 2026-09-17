from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import quanthus.forward_validation.validation as entry_validation
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.serialization import proposal_digest
from quanthus.forward_validation.validation import load_entry_proposal

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL_PATH = ROOT / "config/forward-validation/entry/p6-prospective-mvp-entry.v1.json"


def _raw() -> dict[str, object]:
    return json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))


def test_frozen_p6_entry_proposal_loads_and_grants_no_activation() -> None:
    proposal = load_entry_proposal(PROPOSAL_PATH, root=ROOT)

    assert proposal.proposal_id == "p6-prospective-mechanism-mvp-entry-v1"
    assert proposal.state == "FROZEN_ENTRY_ONLY_NO_ACTIVATION"
    assert proposal.official_cycle_authorized is False
    assert proposal.proposal_digest == _raw()["proposal_digest"]


def test_declared_entry_digest_is_canonical_and_complete() -> None:
    raw = _raw()

    assert raw["proposal_digest"] == proposal_digest(raw)


def test_cross_section_cannot_inflate_the_independent_budget() -> None:
    raw = _raw()
    mutated = copy.deepcopy(raw)
    mutated["observation_budget"]["cross_section_increases_independent_progress"] = True

    with pytest.raises(ForwardValidationContractError, match="budget_drift"):
        entry_validation._validate_budget_and_metrics(mutated)


def test_budget_block_count_and_maturity_tail_are_frozen() -> None:
    raw = _raw()
    mutated = copy.deepcopy(raw)
    mutated["observation_budget"]["collection_block_count"] = 2

    with pytest.raises(ForwardValidationContractError, match="budget_drift"):
        entry_validation._validate_budget_and_metrics(mutated)

    mutated = copy.deepcopy(raw)
    mutated["observation_budget"]["target_maturity_tail_trading_sessions"] = 10
    with pytest.raises(ForwardValidationContractError, match="budget_drift"):
        entry_validation._validate_budget_and_metrics(mutated)


def test_profitability_cannot_become_the_primary_mvp_gate() -> None:
    raw = _raw()
    mutated = copy.deepcopy(raw)
    mutated["metric_precommitment"]["economic_performance_is_primary_gate"] = True

    with pytest.raises(ForwardValidationContractError, match="metric_drift"):
        entry_validation._validate_budget_and_metrics(mutated)


def test_stage_or_downstream_authority_cannot_be_smuggled_into_entry() -> None:
    raw = _raw()
    for key in (
        "stage_action_authority",
        "new_expert_authorized",
        "aggregator_change_authorized",
        "p6_data_implementation_authorized",
        "official_prospective_cycle_authorized",
        "notification_authorized",
        "broker_authorized",
        "live_capital_authorized",
    ):
        mutated = copy.deepcopy(raw)
        mutated["authority"][key] = True
        with pytest.raises(ForwardValidationContractError, match="authority_drift"):
            entry_validation._validate_expected_stops_rollback_authority(mutated)


def test_p3_inconclusive_disabled_lifecycle_remains_immutable() -> None:
    raw = _raw()
    mutated = copy.deepcopy(raw)
    mutated["formal_strategy"]["prior_shadow_status"] = "enabled"
    bound = entry_validation._validate_bindings(mutated, ROOT)

    with pytest.raises(ForwardValidationContractError, match="lifecycle_incompatible"):
        entry_validation._validate_formal_strategy(mutated, bound)


def test_repository_binding_drift_fails_closed() -> None:
    raw = _raw()
    mutated = copy.deepcopy(raw)
    mutated["bindings"]["strategy_card"]["sha256"] = "sha256:" + "1" * 64

    with pytest.raises(ForwardValidationContractError, match="binding_digest_drift"):
        entry_validation._validate_bindings(mutated, ROOT)


def test_automatic_successor_is_forbidden() -> None:
    raw = _raw()
    mutated = copy.deepcopy(raw)
    mutated["governance"]["automatic_successor"] = "P6-DATA-001"

    with pytest.raises(ForwardValidationContractError, match="governance_drift"):
        entry_validation._validate_expected_stops_rollback_authority(mutated)
