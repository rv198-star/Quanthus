import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    AuditEventStore,
    BacktestEngine,
    BacktestRunSpec,
    BacktestSignal,
    CorporateAction,
    CorporateActionStep,
    DiagnosticDisposition,
    DiagnosticReplaySpec,
    MarketSession,
    OpenExecutionState,
    OrderSide,
    RoundTripReplaySpec,
    load_execution_policy,
    run_diagnostic_path,
    run_round_trip,
)
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root

ROOT = find_project_root()
POLICY_PATH = ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_round_trip_replay.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class BacktestReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.inputs = self.fixture["inputs"]
        self.expected = self.fixture["expected"]
        self.policy = load_execution_policy(POLICY_PATH)
        self.engine = BacktestEngine(self.policy)

    def signal(self, *, side: OrderSide) -> BacktestSignal:
        is_buy = side is OrderSide.BUY
        market_date = date.fromisoformat(
            self.inputs["entry_signal_date"] if is_buy else self.inputs["exit_signal_date"]
        )
        decision_at = datetime.fromisoformat(
            self.inputs["entry_decision_at"] if is_buy else self.inputs["exit_decision_at"]
        )
        return BacktestSignal(
            signal_id="signal-round-trip-entry" if is_buy else "signal-round-trip-exit",
            snapshot=SnapshotRef(
                market_date=market_date,
                knowledge_cutoff=decision_at.replace(minute=0),
                source_revision="p2-synthetic-round-trip-decision-v1",
                snapshot_id=(
                    "p2-round-trip-entry-snapshot-v1"
                    if is_buy
                    else "p2-round-trip-exit-snapshot-v1"
                ),
            ),
            decision_at=decision_at,
            profile_id="close-to-next-open-v1",
            window_id=None,
            security_code=self.inputs["security_code"],
            side=side,
            unit_id="unit-001",
            quantity=self.inputs["quantity"],
            aggregate_score=Decimal("0.80"),
            reason_code="round_trip_entry" if is_buy else "upstream_exit",
            decision_version="synthetic-round-trip-decision-v1",
        )

    def session(self, *, entry: bool) -> MarketSession:
        session_date = date.fromisoformat(
            self.inputs["entry_session"] if entry else self.inputs["exit_session"]
        )
        return MarketSession(
            session_date=session_date,
            open_at=datetime.combine(session_date, datetime.min.time(), SHANGHAI).replace(
                hour=9, minute=30
            ),
            close_at=datetime.combine(session_date, datetime.min.time(), SHANGHAI).replace(hour=15),
            security_code=self.inputs["security_code"],
            open_price=Decimal("10.00" if entry else "10.20"),
            close_price=Decimal("10.20" if entry else "10.10"),
            open_execution_state=OpenExecutionState.EXECUTABLE,
            source_revision="p2-synthetic-round-trip-execution-v1",
            snapshot_id=(
                "p2-round-trip-entry-execution-v1" if entry else "p2-round-trip-exit-execution-v1"
            ),
        )

    def scenario(self) -> RoundTripReplaySpec:
        action_at = datetime.fromisoformat(self.inputs["corporate_action_at"])
        return RoundTripReplaySpec(
            run=BacktestRunSpec(
                code_version="test-code-v1",
                parameter_hash="sha256:synthetic-round-trip",
                data_snapshot_id="p2-round-trip-snapshot-bundle-v1",
                feature_version="not-applicable",
                rule_version="synthetic-round-trip-decision-v1",
                run_id="p2-round-trip-replay-001",
                execution_policy_version=self.policy.policy_version,
                execution_profile_id="close-to-next-open-v1",
                schedule_version="close-to-next-open-synthetic-v1",
                official=False,
            ),
            entry_signal=self.signal(side=OrderSide.BUY),
            entry_session=self.session(entry=True),
            exit_signal=self.signal(side=OrderSide.SELL),
            exit_sessions=(self.session(entry=False),),
            initial_cash=Decimal(self.inputs["initial_cash"]),
            capital_days=self.inputs["capital_days"],
            corporate_actions=(
                CorporateActionStep(
                    action=CorporateAction(
                        action_id="round-trip-cash-dividend-001",
                        security_code=self.inputs["security_code"],
                        effective_date=action_at.date(),
                        known_at=datetime(2026, 7, 20, 10, 0, tzinfo=SHANGHAI),
                        cash_dividend_per_share=Decimal(self.inputs["cash_dividend_per_share"]),
                        bonus_share_ratio=Decimal("0.00"),
                        source_revision="p1-synthetic-corporate-action-v1",
                        snapshot_id="p1-round-trip-action-snapshot-v1",
                    ),
                    applied_at=action_at,
                    post_action_mark_price=Decimal(self.inputs["post_action_mark_price"]),
                    mark_snapshot_id="p1-round-trip-ex-mark-v1",
                ),
            ),
        )

    def test_round_trip_composes_events_accounting_metrics_and_audit(self) -> None:
        replay = run_round_trip(self.engine, self.scenario())

        self.assertEqual(
            [event.event_type.value for event in replay.backtest.events],
            self.expected["event_types"],
        )
        for field in ("ending_cash", "external_pool", "realized_pnl", "total_pnl"):
            self.assertEqual(replay.backtest.result_summary[field], self.expected[field])
        self.assertEqual(replay.performance.to_dict(), self.expected["performance"])
        self.assertEqual(
            [event.sequence for event in replay.backtest.events],
            list(range(1, len(replay.backtest.events) + 1)),
        )

        with tempfile.TemporaryDirectory() as directory:
            stored = AuditEventStore(Path(directory)).persist_run(replay.backtest)
            self.assertEqual(
                stored.storage_manifest["event_count"], len(self.expected["event_types"])
            )

    def test_diagnostic_path_is_bounded_isolated_and_not_official(self) -> None:
        replay = run_diagnostic_path(
            self.engine,
            DiagnosticReplaySpec(
                source_decision_id="decision-rejected-001",
                source_disposition=DiagnosticDisposition.REJECTED,
                baseline_policy_version="synthetic-baseline-v1",
                scenario=replace(
                    self.scenario(),
                    run=replace(self.scenario().run, run_id="p2-diagnostic-path-001"),
                ),
            ),
        )

        for field, expected in self.expected["diagnostic"].items():
            self.assertEqual(replay.backtest.result_summary[field], expected)
        self.assertFalse(replay.backtest.run.official)

        too_long = replace(self.scenario(), capital_days=21)
        with self.assertRaisesRegex(ValueError, "limited to 20"):
            DiagnosticReplaySpec(
                source_decision_id="decision-rejected-002",
                source_disposition=DiagnosticDisposition.REJECTED,
                baseline_policy_version="synthetic-baseline-v1",
                scenario=too_long,
            )

    def test_round_trip_rejects_incomplete_exit(self) -> None:
        blocked = replace(
            self.session(entry=False),
            open_execution_state=OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
        )
        with self.assertRaisesRegex(ValueError, "did not complete"):
            run_round_trip(
                self.engine,
                replace(self.scenario(), exit_sessions=(blocked,)),
            )


if __name__ == "__main__":
    unittest.main()
