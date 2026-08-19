from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from quanthus.aggregator import (
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.backtest.contracts import DecisionDisposition
from quanthus.expert_protocols import RequiredAction
from quanthus.governed_strategy import governed_position_decision_event
from tests.unit.test_aggregator import constraint, context, score, veto
from tests.unit.test_backtest_decisions import BacktestDecisionTests
from tests.unit.test_governed_strategy import GovernedStrategyTests

ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = (
    ROOT / "tests" / "fixtures" / "golden" / "expert_evaluation_v2" / "v1-digest-inventory.json"
)


def semantic_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def test_v1_source_config_and_golden_bytes_remain_frozen() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))

    observed = {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in inventory["files"]
    }

    assert observed == inventory["files"]


def test_v1_expert_aggregator_and_decision_semantics_remain_frozen() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))["semantic"]
    current_context = context()
    veto_output = veto("veto.system", RequiredAction.NONE)
    constraint_output = constraint("constraint.position", "0", "0.30")
    scoring_output = score("score.trend", "0.80", "0.75")
    governance = aggregate_governance(
        current_context,
        (veto_output,),
        (constraint_output,),
    )
    action = aggregate_score_and_map_action(
        current_context,
        governance,
        (scoring_output,),
        ScoreActionPolicy("candidate-action-v1", Decimal("0.35")),
    )
    decision_case = BacktestDecisionTests()
    decision_case.setUp()
    decision = decision_case.decision(DecisionDisposition.ACCEPTED)
    governed_case = GovernedStrategyTests()
    governed_case.setUp()
    frame = governed_case.replay_frame()
    review = governed_case.position_review(
        governed_case.feature_row(price="90", sma20="99", sma60="100")
    )
    governed_decision = governed_position_decision_event(
        review=review,
        frame=frame,
        policy=governed_case.policy,
        run_id="governed-run-001",
        profile_id="close-to-next-open-v1",
        disposition=DecisionDisposition.ACCEPTED,
        signal_id="signal-exit-001",
        linked_order_ids=("order-exit-001",),
    )
    observed = {
        "scoring_output": scoring_output.digest,
        "constraint_output": constraint_output.digest,
        "veto_output": veto_output.digest,
        "governance_result": governance.digest,
        "score_action_result": action.digest,
        "decision_event": semantic_digest(decision.to_dict()),
        "governed_decision_event": semantic_digest(governed_decision.to_dict()),
    }

    assert observed == inventory
