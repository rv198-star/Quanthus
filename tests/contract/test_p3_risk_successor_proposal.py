"""A successor proposal cannot silently extend an accepted experiment or its authority."""

import hashlib
import json
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_risk_components as components
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = ROOT / "config/research/p3-strat-003/risk-successor-proposal.v1.json"


def test_successor_binds_frozen_inputs_and_changes_one_strategy_effect_only() -> None:
    proposal = json.loads(PROPOSAL.read_text())
    for key in ("original_request", "original_card"):
        binding = proposal["frozen_bindings"][key]
        assert (
            binding["sha256"]
            == "sha256:" + hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest()
        )
    previous = components.load_request(ROOT)
    candidate = proposal["proposed_candidate"]
    assert candidate["reference_effects"] == previous["variants"]["TR"]
    assert {
        key
        for key, value in candidate["effects"].items()
        if value != candidate["reference_effects"][key]
    } == {"entry_filter"}
    card = json.loads((ROOT / proposal["frozen_bindings"]["original_card"]["path"]).read_text())
    risk = next(expert for expert in card["experts"] if expert["route"] == "policy_only")
    assert candidate["unit_entry_cap"] == risk["entry_policy"]["allowed_maximum_position"]
    assert (
        candidate["unit_held_reduction_target"]
        == risk["held_policy"]["reduce_target_maximum_position"]
    )
    assert candidate["cell"] not in previous["variants"]
    assert candidate["registry_admitted"] is False
    assert proposal["current_authority"]["step2_registry_extended"] is False


def test_successor_proposal_cannot_masquerade_as_old_executable_request(tmp_path: Path) -> None:
    request_path = tmp_path / components.REQUEST_PATH
    request_path.parent.mkdir(parents=True)
    request_path.write_bytes(PROPOSAL.read_bytes())
    with pytest.raises(native.InputError, match="COMPONENT_REQUEST_BINDING"):
        components.load_request(tmp_path)


def test_new_cell_is_rejected_by_the_sealed_five_cell_runner() -> None:
    registry = ExpertStrategyRegistry(ROOT)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    with pytest.raises(native.InputError, match="COMPONENT_VARIANT"):
        components.ComponentEntryRunner(runner, "TCH")


def test_future_scope_cannot_inherit_exposed_window_or_execution_admission() -> None:
    proposal = json.loads(PROPOSAL.read_text())
    historical = proposal["validation_stages"]["A_exposed_window_mechanism"]
    previous = components.load_request(ROOT)
    assert historical["decision_scope"] == previous["scope"][:2]
    assert historical["execution_end"] == previous["scope"][2]
    assert historical["information_cutoff"] == previous["information_cutoff"]
    assert historical["already_observed_window"] is True
    assert historical["new_cells"] == ["TCH"]
    assert historical["budget"]["new_research_cells"] == 1
    future = proposal["validation_stages"]["B_fresh_evidence_pilot"]
    assert future["state"] == "NOT_QUALIFIED_NOT_STARTED"
    assert future["maximum_decision_sessions"] == future["maximum_22_session_blocks"] * 22
    assert all(
        proposal["current_authority"][key] is False
        for key in (
            "new_result_bearing_execution_authorized",
            "new_market_row_access_authorized",
            "original_strict_B_accepted",
            "original_strict_D_activated",
            "historical_PIT_qualified",
            "P6_mutation_authorized",
            "production_authorized",
            "original_request_replaced",
        )
    )
