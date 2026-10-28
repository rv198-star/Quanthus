from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.backtest import (
    BacktestEngine,
    BacktestRunSpec,
    BacktestSignal,
    EventType,
    MarketSession,
    OpenExecutionState,
    OrderSide,
    load_execution_policy,
)
from quanthus.contracts import SnapshotRef
from quanthus.expert_strategy.entry_march_capital import filtered_decision
from quanthus.policy import find_project_root


@dataclass(frozen=True)
class Action:
    final_action: GovernedAction = GovernedAction.TRIAL_ENTRY
    proposed_action: GovernedAction = GovernedAction.TRIAL_ENTRY
    action_policy_version: str = "parent"
    action_policy_digest: str = "sha256:parent"
    reason_codes: tuple[str, ...] = ("expert_passed",)


@dataclass(frozen=True)
class Decision:
    action: Action = Action()
    intervention_id: str = "B"
    governance: str = "unchanged-expert-veto"


def test_model_cannot_enable_old_rejection_or_change_expert_governance():
    old = Decision(action=Action(final_action=GovernedAction.NO_ACTION))
    prediction = {"c0_allowed": False, "c1_allowed": False, "c2_allowed": True}
    with pytest.raises(ValueError, match="CANNOT_PROMOTE_REJECTION"):
        filtered_decision(old, arm="C2", prediction=prediction, request_digest="new")
    prediction["c2_allowed"] = False
    result = filtered_decision(old, arm="C2", prediction=prediction, request_digest="new")
    assert result.governance == old.governance
    assert result.action.final_action is GovernedAction.NO_ACTION
    assert result.action.proposed_action == old.action.proposed_action


def test_entry_filter_changes_actual_cash_position_and_fee_path():
    """A withheld order remains cash; an allowed order enters the real P2 ledger."""
    root = find_project_root()
    engine = BacktestEngine(
        load_execution_policy(root / "tests/fixtures/p2/execution-policy.synthetic.v1.json")
    )
    tz = ZoneInfo("Asia/Shanghai")
    snapshot = SnapshotRef(
        date(2021, 4, 2), datetime(2021, 4, 2, 15, tzinfo=tz), "synthetic", "synthetic20210402"
    )
    signal = BacktestSignal(
        signal_id="synthetic-entry",
        snapshot=snapshot,
        decision_at=datetime(2021, 4, 2, 15, 10, tzinfo=tz),
        profile_id="close-to-next-open-v1",
        window_id=None,
        security_code="000001",
        side=OrderSide.BUY,
        unit_id="unit-001",
        quantity=100,
        aggregate_score=Decimal("0.75"),
        reason_code="synthetic_entry",
        decision_version="synthetic",
    )
    session = MarketSession(
        session_date=date(2021, 4, 6),
        open_at=datetime(2021, 4, 6, 9, 30, tzinfo=tz),
        close_at=datetime(2021, 4, 6, 15, tzinfo=tz),
        security_code="000001",
        open_price=Decimal("10"),
        close_price=Decimal("11"),
        open_execution_state=OpenExecutionState.EXECUTABLE,
        source_revision="synthetic",
        snapshot_id="synthetic20210406",
    )
    run = BacktestRunSpec(
        code_version="synthetic",
        parameter_hash="sha256:synthetic",
        data_snapshot_id=snapshot.snapshot_id,
        feature_version="synthetic",
        rule_version="synthetic",
        run_id="synthetic-entry-march",
        execution_policy_version=engine.policy.policy_version,
        execution_profile_id="close-to-next-open-v1",
        schedule_version=engine.policy.active_profile.schedule_version,
        official=False,
    )
    prediction = {"c0_allowed": True, "c1_allowed": False, "c2_allowed": True}
    outcomes = {}
    for arm in ("C0", "C1", "C2"):
        decision = filtered_decision(
            Decision(), arm=arm, prediction=prediction, request_digest="synthetic"
        )
        if decision.action.final_action is GovernedAction.TRIAL_ENTRY:
            result = engine.run_session(
                run=replace(run, run_id=run.run_id + arm),
                signal=signal,
                session=session,
                initial_cash=Decimal("10000"),
            )
            fills = [e for e in result.events if e.event_type is EventType.FILL]
            snapshots = [e for e in result.events if e.event_type is EventType.DAILY_SNAPSHOT]
            outcomes[arm] = snapshots[-1].payload
            assert len(fills) == 1 and Decimal(fills[0].payload["total_fees"]) > 0
            assert fills[0].event_time.date() > snapshot.market_date
        else:
            outcomes[arm] = {"cash": "10000", "position_quantity": 0}
    assert {k: v for k, v in outcomes["C0"].items() if k != "snapshot_id"} == {
        k: v for k, v in outcomes["C2"].items() if k != "snapshot_id"
    }
    assert outcomes["C1"]["position_quantity"] == 0
    assert outcomes["C2"]["position_quantity"] == 100
    assert Decimal(outcomes["C2"]["cash"]) < Decimal(outcomes["C1"]["cash"])


def test_held_management_is_unchanged_and_unknown_only_blocks_entry():
    from quanthus.expert_strategy.entry_march_capital import runtime_type

    parent = type("Parent", (), {})
    runtime = runtime_type(parent).__new__(runtime_type(parent))
    for arm in ("C0", "C1", "C2"):
        assert runtime._held_intervention_id(arm) == "intervention.position-risk-base-policy"
    prediction = {"c0_allowed": True, "c1_allowed": False, "c2_allowed": False}
    result = filtered_decision(Decision(), arm="C2", prediction=prediction, request_digest="new")
    assert result.action.final_action is GovernedAction.NO_ACTION
    assert result.action.final_action is not GovernedAction.FULL_EXIT


def test_changed_baseline_is_rejected_before_allocation():
    with pytest.raises(ValueError, match="ORIGINAL_B_ELIGIBILITY_CHANGED"):
        filtered_decision(
            Decision(),
            arm="C0",
            prediction={"c0_allowed": False, "c1_allowed": False, "c2_allowed": False},
            request_digest="new",
        )


def test_scoped_native_panel_equals_exact_parent_patch_projection(tmp_path):
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.expert_strategy.entry_march_capital import scoped_panel_type

    rows = [
        {"security_id": code, "market_date": date(2021, month, 2), "value": number}
        for number, code in enumerate(("000001", "000792", "999999"))
        for month in (3, 4, 5)
    ]
    original, patch = tmp_path / "original.parquet", tmp_path / "patch.parquet"
    pq.write_table(pa.Table.from_pylist(rows), original)
    pq.write_table(
        pa.Table.from_pylist(
            [{**r, "value": r["value"] + 100} for r in rows if r["security_id"] == "000792"]
        ),
        patch,
    )

    class Parent:
        def close(self):
            self._connection.close()
            self._spill.cleanup()

    panel = scoped_panel_type(Parent)(
        original,
        None,
        patch=patch,
        selected=("000001", "000792"),
        start=date(2021, 4, 1),
        end=date(2021, 5, 31),
    )
    try:
        actual = panel._connection.execute(
            "SELECT * FROM feature_panel ORDER BY security_id,market_date"
        ).fetchall()
        with duckdb.connect() as connection:
            expected = connection.execute(
                "SELECT * FROM (SELECT * FROM read_parquet(?) WHERE security_id<>'000792' "
                "UNION ALL SELECT * FROM read_parquet(?)) "
                "WHERE security_id IN ('000001','000792') AND market_date BETWEEN '2021-04-01' "
                "AND '2021-05-31' ORDER BY security_id,market_date",
                [str(original), str(patch)],
            ).fetchall()
        assert actual == expected and len(actual) == 4
        assert all(row[0] != "999999" and row[1] >= date(2021, 4, 1) for row in actual)
    finally:
        panel.close()
