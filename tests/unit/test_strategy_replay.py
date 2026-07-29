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
    AuditIntegrityError,
    BacktestEngine,
    BacktestRunSpec,
    CorporateAction,
    CorporateActionStep,
    MarketSession,
    OpenExecutionState,
    P1DailyExecutionEvidence,
    P1StrategyReplayFrameEvidence,
    ReplayAction,
    ReplayDecision,
    StrategyReplayContext,
    StrategyReplayFrame,
    StrategyReplaySpec,
    load_execution_policy,
    run_strategy_replay,
    strategy_replay_frame_from_p1,
)
from quanthus.backtest.inputs import CoverageStatus
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root

ROOT = find_project_root()
POLICY_PATH = ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p3_strategy_replay.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class ScriptedStrategy:
    strategy_id = "scripted-two-trade-baseline"
    strategy_version = "scripted-two-trade-v1"

    def __init__(self, decisions: dict[date, ReplayDecision]) -> None:
        self.decisions = decisions
        self.contexts: list[StrategyReplayContext] = []

    def decide(self, context: StrategyReplayContext) -> ReplayDecision:
        self.contexts.append(context)
        return self.decisions[context.snapshot.market_date]


def at(market_date: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(market_date, datetime.min.time(), SHANGHAI).replace(
        hour=hour, minute=minute
    )


def snapshot(market_date: date) -> SnapshotRef:
    return SnapshotRef(
        market_date=market_date,
        knowledge_cutoff=at(market_date, 15),
        source_revision="strategy-replay-pit-source-v1",
        snapshot_id=f"strategy-snapshot-{market_date.isoformat()}",
    )


def session(
    market_date: date,
    security_code: str,
    open_price: str | None,
    close_price: str | None,
    state: OpenExecutionState = OpenExecutionState.EXECUTABLE,
) -> MarketSession:
    return MarketSession(
        session_date=market_date,
        open_at=at(market_date, 9, 30),
        close_at=at(market_date, 15),
        security_code=security_code,
        open_price=None if open_price is None else Decimal(open_price),
        close_price=None if close_price is None else Decimal(close_price),
        open_execution_state=state,
        source_revision="strategy-replay-execution-v1",
        snapshot_id=f"execution-{security_code}-{market_date.isoformat()}",
    )


def frame(
    decision_date: date,
    execution_date: date,
    *sessions: MarketSession,
    corporate_actions: tuple[CorporateActionStep, ...] = (),
) -> StrategyReplayFrame:
    return StrategyReplayFrame(
        snapshot=snapshot(decision_date),
        decision_at=at(decision_date, 15),
        execution_date=execution_date,
        execution_sessions=tuple(sessions),
        corporate_actions=corporate_actions,
    )


class StrategyReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_execution_policy(POLICY_PATH)
        self.engine = BacktestEngine(self.policy)

    def run_spec(
        self, frames: tuple[StrategyReplayFrame, ...], *, run_id: str
    ) -> StrategyReplaySpec:
        return StrategyReplaySpec(
            run=BacktestRunSpec(
                code_version="test-code-v1",
                parameter_hash="sha256:strategy-replay",
                data_snapshot_id="strategy-replay-bundle-v1",
                feature_version="scripted-features-v1",
                rule_version="scripted-two-trade-v1",
                run_id=run_id,
                execution_policy_version=self.policy.policy_version,
                execution_profile_id="close-to-next-open-v1",
                schedule_version="close-to-next-open-synthetic-v1",
                official=False,
            ),
            unit_id="unit-001",
            initial_cash=Decimal("10000.00"),
            frames=frames,
        )

    def two_trade_frames(self) -> tuple[StrategyReplayFrame, ...]:
        dividend_date = date(2026, 1, 7)
        dividend = CorporateActionStep(
            action=CorporateAction(
                action_id="cash-dividend-000001-20260107",
                security_code="000001.SZ",
                effective_date=dividend_date,
                known_at=at(date(2026, 1, 6), 10),
                cash_dividend_per_share=Decimal("0.10"),
                bonus_share_ratio=Decimal("0.00"),
                source_revision="strategy-replay-action-v1",
                snapshot_id="action-snapshot-000001-20260107",
            ),
            applied_at=at(dividend_date, 9),
            post_action_mark_price=Decimal("10.10"),
            mark_snapshot_id="ex-mark-000001-20260107",
        )
        return (
            frame(
                date(2026, 1, 5),
                date(2026, 1, 6),
                session(date(2026, 1, 6), "000001.SZ", "10.00", "10.20"),
            ),
            frame(
                date(2026, 1, 6),
                dividend_date,
                session(dividend_date, "000001.SZ", "10.10", "10.40"),
                corporate_actions=(dividend,),
            ),
            frame(
                date(2026, 1, 7),
                date(2026, 1, 8),
                session(date(2026, 1, 8), "000001.SZ", "10.50", "10.30"),
            ),
            frame(
                date(2026, 1, 8),
                date(2026, 1, 9),
                session(date(2026, 1, 9), "000002.SZ", "20.00", "19.50"),
            ),
            frame(
                date(2026, 1, 9),
                date(2026, 1, 12),
                session(date(2026, 1, 12), "000002.SZ", "19.20", "19.00"),
            ),
            frame(
                date(2026, 1, 12),
                date(2026, 1, 13),
                session(date(2026, 1, 13), "000002.SZ", "18.50", "18.80"),
            ),
        )

    def two_trade_strategy(self) -> ScriptedStrategy:
        return ScriptedStrategy(
            {
                date(2026, 1, 5): ReplayDecision(
                    ReplayAction.ENTER,
                    "first_entry",
                    Decimal("0.80"),
                    "000001.SZ",
                    100,
                ),
                date(2026, 1, 6): ReplayDecision(
                    ReplayAction.NO_ACTION, "first_hold", Decimal("0.40")
                ),
                date(2026, 1, 7): ReplayDecision(ReplayAction.EXIT, "first_exit", Decimal("-0.30")),
                date(2026, 1, 8): ReplayDecision(
                    ReplayAction.ENTER,
                    "second_entry",
                    Decimal("0.70"),
                    "000002.SZ",
                    100,
                ),
                date(2026, 1, 9): ReplayDecision(
                    ReplayAction.NO_ACTION, "second_hold", Decimal("0.10")
                ),
                date(2026, 1, 12): ReplayDecision(
                    ReplayAction.EXIT, "second_exit", Decimal("-0.70")
                ),
            }
        )

    def test_two_trades_reconcile_equity_metrics_and_audit(self) -> None:
        strategy = self.two_trade_strategy()
        spec = self.run_spec(self.two_trade_frames(), run_id="p3-strategy-replay-001")

        replay = run_strategy_replay(self.engine, strategy, spec)

        expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(replay.performance.to_dict(), expected["performance"])
        self.assertEqual([trade.to_dict() for trade in replay.trades], expected["trades"])
        self.assertEqual(
            [point.to_dict() for point in replay.equity_curve], expected["equity_curve"]
        )
        self.assertEqual(replay.backtest.result_summary, expected["result_summary"])
        self.assertEqual(len(replay.decisions), 6)
        self.assertEqual(replay.performance.winning_trades, 1)
        self.assertEqual(replay.performance.losing_trades, 1)
        self.assertFalse(replay.performance.compound_nav)
        self.assertFalse(replay.pending_exit)
        self.assertEqual(replay.final_state.settled_quantity, 0)
        self.assertTrue(
            all(not hasattr(context, "execution_sessions") for context in strategy.contexts)
        )

        repeated = run_strategy_replay(
            self.engine,
            self.two_trade_strategy(),
            self.run_spec(self.two_trade_frames(), run_id="p3-strategy-replay-001"),
        )
        self.assertEqual(replay.canonical_bytes(), repeated.canonical_bytes())

        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            stored = store.persist_strategy_replay(replay)
            self.assertEqual(
                stored.run.storage_manifest["event_count"], len(replay.backtest.events)
            )
            self.assertEqual(stored.storage_manifest["decision_count"], len(replay.decisions))
            self.assertEqual(stored.replay, replay.audit_dict())
            repeated_store = store.persist_strategy_replay(replay)
            self.assertEqual(repeated_store.path, stored.path)

            (stored.path / "replay.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(AuditIntegrityError, "payload checksum mismatch"):
                store.load_strategy_replay(replay.backtest.run.run_id)

    def test_carried_exit_skips_new_strategy_decisions_and_keeps_one_order(self) -> None:
        frames = (
            frame(
                date(2026, 2, 2),
                date(2026, 2, 3),
                session(date(2026, 2, 3), "000001.SZ", "10.00", "10.10"),
            ),
            frame(
                date(2026, 2, 3),
                date(2026, 2, 4),
                session(
                    date(2026, 2, 4),
                    "000001.SZ",
                    "9.50",
                    "9.50",
                    OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
                ),
            ),
            frame(
                date(2026, 2, 4),
                date(2026, 2, 5),
                session(date(2026, 2, 5), "000001.SZ", "9.80", "9.90"),
            ),
        )
        strategy = ScriptedStrategy(
            {
                date(2026, 2, 2): ReplayDecision(
                    ReplayAction.ENTER,
                    "entry",
                    Decimal("0.50"),
                    "000001.SZ",
                    100,
                ),
                date(2026, 2, 3): ReplayDecision(
                    ReplayAction.EXIT, "structural_exit", Decimal("-0.80")
                ),
            }
        )

        replay = run_strategy_replay(
            self.engine,
            strategy,
            self.run_spec(frames, run_id="p3-strategy-carried-exit-001"),
        )

        transitions = [
            event.payload["transition"]
            for event in replay.backtest.events
            if event.event_type.value == "order_status"
        ]
        self.assertEqual(
            transitions,
            ["created_to_filled", "created_to_carried", "carried_to_filled"],
        )
        self.assertEqual(replay.backtest.result_summary["orders"], 2)
        self.assertEqual(len(strategy.contexts), 2)
        self.assertEqual(len(replay.decisions), 2)
        self.assertEqual(replay.trades[0].holding_sessions, 3)

    def test_add_attempt_fails_before_creating_a_second_order(self) -> None:
        frames = self.two_trade_frames()[:2]
        strategy = ScriptedStrategy(
            {
                date(2026, 1, 5): ReplayDecision(
                    ReplayAction.ENTER,
                    "entry",
                    Decimal("0.50"),
                    "000001.SZ",
                    100,
                ),
                date(2026, 1, 6): ReplayDecision(
                    ReplayAction.ENTER,
                    "unfrozen_add",
                    Decimal("0.90"),
                    "000001.SZ",
                    100,
                ),
            }
        )

        with self.assertRaisesRegex(ValueError, "separately frozen P3 position policy"):
            run_strategy_replay(
                self.engine,
                strategy,
                self.run_spec(frames, run_id="p3-strategy-add-blocked-001"),
            )

    def test_missing_hold_data_carries_the_last_mark_without_forcing_an_exit(self) -> None:
        frames = (
            frame(
                date(2026, 3, 2),
                date(2026, 3, 3),
                session(date(2026, 3, 3), "000001.SZ", "10.00", "10.10"),
            ),
            frame(
                date(2026, 3, 3),
                date(2026, 3, 4),
                session(
                    date(2026, 3, 4),
                    "000001.SZ",
                    None,
                    None,
                    OpenExecutionState.MISSING_EVIDENCE,
                ),
            ),
        )
        strategy = ScriptedStrategy(
            {
                date(2026, 3, 2): ReplayDecision(
                    ReplayAction.ENTER,
                    "entry",
                    Decimal("0.50"),
                    "000001.SZ",
                    100,
                ),
                date(2026, 3, 3): ReplayDecision(ReplayAction.NO_ACTION, "missing_hold_evidence"),
            }
        )

        replay = run_strategy_replay(
            self.engine,
            strategy,
            self.run_spec(frames, run_id="p3-strategy-missing-mark-001"),
        )

        marks = [
            event.payload
            for event in replay.backtest.events
            if event.event_type.value == "daily_snapshot"
        ]
        self.assertEqual(marks[-1]["mark_status"], "carried_forward")
        self.assertEqual(replay.final_state.last_mark_price, Decimal("10.10"))
        self.assertEqual(replay.final_state.settled_quantity, 100)
        self.assertTrue(replay.backtest.result_summary["open_position"])
        self.assertIsNone(replay.performance.win_rate)
        self.assertFalse(replay.pending_exit)

    def test_corporate_action_during_a_carried_sell_adjusts_the_same_order(self) -> None:
        action_date = date(2026, 4, 6)
        action = CorporateActionStep(
            action=CorporateAction(
                action_id="carried-sell-action-001",
                security_code="000001.SZ",
                effective_date=action_date,
                known_at=at(date(2026, 4, 2), 10),
                cash_dividend_per_share=Decimal("0.05"),
                bonus_share_ratio=Decimal("0.10"),
                source_revision="strategy-replay-action-v1",
                snapshot_id="carried-sell-action-snapshot-001",
            ),
            applied_at=at(action_date, 9),
            post_action_mark_price=Decimal("9.40"),
            mark_snapshot_id="carried-sell-action-mark-001",
        )
        frames = (
            frame(
                date(2026, 4, 1),
                date(2026, 4, 2),
                session(date(2026, 4, 2), "000001.SZ", "10.00", "10.10"),
            ),
            frame(
                date(2026, 4, 2),
                date(2026, 4, 3),
                session(
                    date(2026, 4, 3),
                    "000001.SZ",
                    "9.50",
                    "9.50",
                    OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
                ),
            ),
            frame(
                date(2026, 4, 3),
                action_date,
                session(action_date, "000001.SZ", "9.60", "9.70"),
                corporate_actions=(action,),
            ),
        )
        strategy = ScriptedStrategy(
            {
                date(2026, 4, 1): ReplayDecision(
                    ReplayAction.ENTER,
                    "entry",
                    Decimal("0.50"),
                    "000001.SZ",
                    100,
                ),
                date(2026, 4, 2): ReplayDecision(ReplayAction.EXIT, "exit", Decimal("-0.80")),
            }
        )

        replay = run_strategy_replay(
            self.engine,
            strategy,
            self.run_spec(frames, run_id="p3-strategy-carried-action-001"),
        )

        transitions = [
            event.payload["transition"]
            for event in replay.backtest.events
            if event.event_type.value == "order_status"
        ]
        fills = [
            event.payload for event in replay.backtest.events if event.event_type.value == "fill"
        ]
        actions = [
            event.payload
            for event in replay.backtest.events
            if event.event_type.value == "corporate_action"
        ]
        self.assertEqual(
            transitions,
            [
                "created_to_filled",
                "created_to_carried",
                "carried_to_carried_adjusted",
                "carried_to_filled",
            ],
        )
        self.assertEqual(replay.backtest.result_summary["orders"], 2)
        self.assertEqual(fills[-1]["quantity"], 110)
        self.assertEqual(actions[0]["quantity_before"], 100)
        self.assertEqual(actions[0]["quantity_after"], 110)
        self.assertEqual(replay.trades[0].exit_quantity, 110)
        self.assertEqual(replay.trades[0].dividends, Decimal("5.00"))
        self.assertFalse(replay.pending_exit)

    def test_no_trade_replay_still_validates_the_execution_manifest(self) -> None:
        frames = (frame(date(2026, 5, 6), date(2026, 5, 7)),)
        strategy = ScriptedStrategy(
            {date(2026, 5, 6): ReplayDecision(ReplayAction.NO_ACTION, "no_candidate")}
        )
        spec = self.run_spec(frames, run_id="p3-strategy-no-trade-manifest-001")

        with self.assertRaisesRegex(ValueError, "schedule_version"):
            run_strategy_replay(
                self.engine,
                strategy,
                replace(spec, run=replace(spec.run, schedule_version="wrong-schedule")),
            )

    def test_p1_frame_adapter_is_deterministic_and_keeps_future_evidence_out_of_context(
        self,
    ) -> None:
        decision_date = date(2026, 5, 11)
        execution_date = date(2026, 5, 12)

        def evidence(security_code: str, open_price: str) -> P1DailyExecutionEvidence:
            return P1DailyExecutionEvidence(
                snapshot=SnapshotRef(
                    market_date=execution_date,
                    knowledge_cutoff=at(execution_date, 18),
                    source_revision="p1-replay-execution-v1",
                    snapshot_id=f"p1-{security_code}-{execution_date.isoformat()}",
                ),
                security_code=security_code,
                open_at=at(execution_date, 9, 30),
                close_at=at(execution_date, 15),
                coverage_status=CoverageStatus.TRADING,
                data_valid=True,
                is_executable=True,
                entry_policy_allows=True,
                reason_code="tradable",
                open_price=Decimal(open_price),
                high_price=Decimal("21.00"),
                low_price=Decimal("9.00"),
                close_price=Decimal("20.50" if security_code == "000002.SZ" else "10.20"),
                upper_limit_price=Decimal("22.00"),
                lower_limit_price=Decimal("8.00"),
            )

        adapted = strategy_replay_frame_from_p1(
            P1StrategyReplayFrameEvidence(
                decision_snapshot=snapshot(decision_date),
                decision_at=at(decision_date, 15),
                execution_date=execution_date,
                execution_evidence=(
                    evidence("000002.SZ", "20.00"),
                    evidence("000001.SZ", "10.00"),
                ),
            )
        )

        self.assertEqual(
            [item.security_code for item in adapted.execution_sessions],
            ["000001.SZ", "000002.SZ"],
        )
        strategy = ScriptedStrategy(
            {
                decision_date: ReplayDecision(
                    ReplayAction.ENTER,
                    "p1_adapted_entry",
                    Decimal("0.50"),
                    "000001.SZ",
                    100,
                )
            }
        )
        replay = run_strategy_replay(
            self.engine,
            strategy,
            self.run_spec((adapted,), run_id="p3-p1-frame-adapter-001"),
        )

        self.assertEqual(replay.backtest.result_summary["fills"], 1)
        self.assertFalse(hasattr(strategy.contexts[0], "execution_evidence"))
        self.assertFalse(hasattr(strategy.contexts[0], "execution_sessions"))

    def test_p1_frame_adapter_rejects_misaligned_execution_evidence(self) -> None:
        decision_date = date(2026, 5, 11)
        with self.assertRaisesRegex(ValueError, "must match execution_date"):
            P1StrategyReplayFrameEvidence(
                decision_snapshot=snapshot(decision_date),
                decision_at=at(decision_date, 15),
                execution_date=date(2026, 5, 12),
                execution_evidence=(
                    P1DailyExecutionEvidence(
                        snapshot=snapshot(date(2026, 5, 13)),
                        security_code="000001.SZ",
                        open_at=at(date(2026, 5, 13), 9, 30),
                        close_at=at(date(2026, 5, 13), 15),
                        coverage_status=CoverageStatus.MISSING,
                        data_valid=False,
                        is_executable=False,
                        entry_policy_allows=False,
                        reason_code="missing",
                        open_price=None,
                        high_price=None,
                        low_price=None,
                        close_price=None,
                        upper_limit_price=None,
                        lower_limit_price=None,
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
