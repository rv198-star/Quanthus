import json
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    BacktestEngine,
    BacktestInputError,
    BacktestRunSpec,
    BacktestSignal,
    CorporateAction,
    EventType,
    MarketSession,
    OpenExecutionState,
    OrderSide,
    UnitState,
    UnsupportedModeError,
    load_execution_policy,
    rank_signals,
)
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root

ROOT = find_project_root()
POLICY_PATH = ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
RESEARCH_POLICY_PATH = ROOT / "config" / "p2-execution-policy.research.v1.json"
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_normal_buy.v1.json"
REJECTION_GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_buy_no_fill.v1.json"
SELL_GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_carried_sell.v1.json"
CANDIDATE_GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_candidate_tie.v1.json"
ACTION_GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_corporate_action.v1.json"
MAXIMUM_HOLD_GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_maximum_hold_sell.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("fixture datetime must be timezone-aware")
    return parsed


class BacktestEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.inputs = self.fixture["inputs"]
        self.expected = self.fixture["expected"]
        self.rejection_fixture = json.loads(REJECTION_GOLDEN_PATH.read_text(encoding="utf-8"))
        self.sell_fixture = json.loads(SELL_GOLDEN_PATH.read_text(encoding="utf-8"))
        self.candidate_fixture = json.loads(CANDIDATE_GOLDEN_PATH.read_text(encoding="utf-8"))
        self.action_fixture = json.loads(ACTION_GOLDEN_PATH.read_text(encoding="utf-8"))
        self.maximum_hold_fixture = json.loads(MAXIMUM_HOLD_GOLDEN_PATH.read_text(encoding="utf-8"))
        self.policy = load_execution_policy(POLICY_PATH)
        self.engine = BacktestEngine(self.policy)

    def signal(
        self,
        *,
        quantity: int | None = None,
        profile_id: str = "close-to-next-open-v1",
        window_id: str | None = None,
        aggregate_score: Decimal = Decimal("0.75"),
        security_code: str | None = None,
        signal_id: str = "signal-normal-buy-001",
    ) -> BacktestSignal:
        decision_at = dt(self.inputs["decision_at"])
        snapshot = SnapshotRef(
            market_date=date.fromisoformat(self.inputs["signal_market_date"]),
            knowledge_cutoff=datetime(2026, 7, 27, 15, 0, tzinfo=SHANGHAI),
            source_revision="p1-synthetic-decision-v1",
            snapshot_id="p1-synthetic-2026-07-27-v1",
        )
        return BacktestSignal(
            signal_id=signal_id,
            snapshot=snapshot,
            decision_at=decision_at,
            profile_id=profile_id,
            window_id=window_id,
            security_code=security_code or self.inputs["security_code"],
            side=OrderSide.BUY,
            unit_id="unit-001",
            quantity=quantity if quantity is not None else self.inputs["quantity"],
            aggregate_score=aggregate_score,
            reason_code="synthetic_candidate_accepted",
            decision_version="synthetic-decision-v1",
        )

    def session(self, *, session_date: date | None = None) -> MarketSession:
        selected_date = session_date or date.fromisoformat(self.inputs["next_session_date"])
        if session_date is None:
            open_at = dt(self.inputs["open_at"])
            close_at = dt(self.inputs["close_at"])
        else:
            open_at = datetime.combine(selected_date, datetime.min.time(), SHANGHAI).replace(
                hour=15, minute=10
            )
            close_at = open_at.replace(minute=30)
        return MarketSession(
            session_date=selected_date,
            open_at=open_at,
            close_at=close_at,
            security_code=self.inputs["security_code"],
            open_price=Decimal(self.inputs["open_price"]),
            close_price=Decimal(self.inputs["close_price"]),
            open_execution_state=OpenExecutionState.EXECUTABLE,
            source_revision="p1-synthetic-execution-v1",
            snapshot_id="p1-synthetic-2026-07-28-v1",
        )

    def run_spec(
        self,
        *,
        profile_id: str = "close-to-next-open-v1",
        schedule_version: str = "close-to-next-open-synthetic-v1",
    ) -> BacktestRunSpec:
        return BacktestRunSpec(
            code_version="test-code-v1",
            parameter_hash="sha256:synthetic-parameters",
            data_snapshot_id="p1-synthetic-2026-07-27-v1",
            feature_version="not-applicable",
            rule_version="synthetic-decision-v1",
            run_id="p2-normal-buy-001",
            execution_policy_version=self.policy.policy_version,
            execution_profile_id=profile_id,
            schedule_version=schedule_version,
            official=False,
        )

    def sell_signal(self) -> BacktestSignal:
        inputs = self.sell_fixture["inputs"]
        decision_at = dt(inputs["decision_at"])
        return BacktestSignal(
            signal_id="signal-carried-sell-001",
            snapshot=SnapshotRef(
                market_date=date.fromisoformat(inputs["signal_market_date"]),
                knowledge_cutoff=decision_at.replace(minute=0),
                source_revision="p2-synthetic-sell-decision-v1",
                snapshot_id="p2-synthetic-sell-2026-07-27-v1",
            ),
            decision_at=decision_at,
            profile_id="close-to-next-open-v1",
            window_id=None,
            security_code=inputs["security_code"],
            side=OrderSide.SELL,
            unit_id="unit-001",
            quantity=inputs["quantity"],
            aggregate_score=Decimal("0.80"),
            reason_code="synthetic_full_exit",
            decision_version="synthetic-decision-v1",
        )

    def sell_run_spec(self) -> BacktestRunSpec:
        return replace(
            self.run_spec(),
            data_snapshot_id="p2-synthetic-sell-2026-07-27-v1",
            run_id="p2-carried-sell-001",
        )

    def sell_opening_state(self, *, external_pool: str | None = None) -> UnitState:
        inputs = self.sell_fixture["inputs"]
        raw = inputs["opening_state"]
        return UnitState(
            unit_id="unit-001",
            target_notional=Decimal(raw["target_notional"]),
            cash=Decimal(raw["cash"]),
            security_code=inputs["security_code"],
            settled_quantity=inputs["quantity"],
            position_cost=Decimal(raw["position_cost"]),
            commission_expense=Decimal(raw["commission_expense"]),
            stamp_duty_expense=Decimal(raw["stamp_duty_expense"]),
            transfer_fee_expense=Decimal(raw["transfer_fee_expense"]),
            realized_pnl=Decimal(raw["realized_pnl"]),
            dividend_income=Decimal(raw["dividend_income"]),
            external_pool=Decimal(external_pool or raw["external_pool"]),
            last_mark_price=Decimal(raw["last_mark_price"]),
            last_mark_snapshot_id=raw["last_mark_snapshot_id"],
        )

    def sell_sessions(self) -> tuple[MarketSession, ...]:
        sessions: list[MarketSession] = []
        security_code = self.sell_fixture["inputs"]["security_code"]
        for raw in self.sell_fixture["inputs"]["sessions"]:
            sessions.append(
                MarketSession(
                    session_date=date.fromisoformat(raw["session_date"]),
                    open_at=dt(raw["open_at"]),
                    close_at=dt(raw["close_at"]),
                    security_code=security_code,
                    open_price=(None if raw["open_price"] is None else Decimal(raw["open_price"])),
                    close_price=(
                        None if raw["close_price"] is None else Decimal(raw["close_price"])
                    ),
                    open_execution_state=OpenExecutionState(raw["open_execution_state"]),
                    source_revision="p2-synthetic-sell-execution-v1",
                    snapshot_id=raw["snapshot_id"],
                )
            )
        return tuple(sessions)

    def test_hand_calculated_normal_buy_closes_the_first_vertical_slice(self) -> None:
        result = self.engine.run_session(
            run=self.run_spec(),
            signal=self.signal(),
            session=self.session(),
            initial_cash=Decimal(self.inputs["initial_cash"]),
        )
        event_types = [event.event_type.value for event in result.events]
        fill = next(event.payload for event in result.events if event.event_type is EventType.FILL)
        ledger = next(
            event.payload for event in result.events if event.event_type is EventType.LEDGER_ENTRY
        )
        intent = next(
            event.payload
            for event in result.events
            if event.event_type is EventType.ORDER_INTENT_CREATED
        )
        order = next(
            event.payload for event in result.events if event.event_type is EventType.ORDER_CREATED
        )
        snapshot = next(
            event.payload for event in result.events if event.event_type is EventType.DAILY_SNAPSHOT
        )

        self.assertEqual(event_types, self.expected["event_types"])
        self.assertEqual(fill["price"], self.expected["fill_price"])
        self.assertEqual(fill["open_execution_state"], self.expected["open_execution_state"])
        self.assertEqual(fill["gross_amount"], self.expected["gross_amount"])
        self.assertEqual(fill["commission"], self.expected["commission"])
        self.assertEqual(intent["eligible_at"], self.inputs["open_at"])
        self.assertEqual(intent["expires_at"], self.inputs["close_at"])
        self.assertEqual(intent["unfilled_policy"], "cancel_after_open_attempt")
        self.assertEqual(order["intent_id"], intent["intent_id"])
        self.assertEqual(
            sum(Decimal(posting["amount"]) for posting in ledger["postings"]), Decimal("0")
        )
        self.assertEqual(snapshot["cash"], self.expected["ending_cash"])
        self.assertEqual(snapshot["position_quantity"], self.expected["position_quantity"])
        self.assertEqual(snapshot["position_cost"], self.expected["position_cost"])
        self.assertEqual(snapshot["market_value"], self.expected["market_value"])
        self.assertEqual(snapshot["total_equity"], self.expected["total_equity"])

    def test_repeated_run_is_byte_identical_with_stable_unique_event_ids(self) -> None:
        arguments = {
            "run": self.run_spec(),
            "signal": self.signal(),
            "session": self.session(),
            "initial_cash": Decimal(self.inputs["initial_cash"]),
        }

        first = self.engine.run_session(**arguments)
        second = self.engine.run_session(**arguments)

        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        event_ids = [event.event_id for event in first.events]
        self.assertEqual(len(event_ids), len(set(event_ids)))

    def test_daily_close_signal_cannot_fill_on_the_same_market_date(self) -> None:
        signal = self.signal()
        with self.assertRaisesRegex(BacktestInputError, "later market session"):
            self.engine.run_session(
                run=self.run_spec(),
                signal=signal,
                session=self.session(session_date=signal.snapshot.market_date),
                initial_cash=Decimal(self.inputs["initial_cash"]),
            )

    def test_non_lot_entry_is_an_explicit_rejection_without_cash_change(self) -> None:
        result = self.engine.run_session(
            run=self.run_spec(),
            signal=self.signal(quantity=50),
            session=self.session(),
            initial_cash=Decimal(self.inputs["initial_cash"]),
        )

        self.assertEqual(result.result_summary["status"], "completed_with_rejection")
        self.assertEqual(result.result_summary["fills"], 0)
        self.assertEqual(result.result_summary["ending_cash"], self.inputs["initial_cash"])
        statuses = [event for event in result.events if event.event_type is EventType.ORDER_STATUS]
        self.assertEqual(statuses[0].reason_code, "entry_quantity_not_whole_lot")

    def test_hand_calculated_buy_no_fill_cases_fail_closed(self) -> None:
        for case in self.rejection_fixture["cases"]:
            with self.subTest(case_id=case["case_id"]):
                state = OpenExecutionState(case["open_execution_state"])
                session = self.session()
                if state in {
                    OpenExecutionState.SUSPENDED,
                    OpenExecutionState.MISSING_EVIDENCE,
                }:
                    session = replace(
                        session,
                        open_execution_state=state,
                        open_price=None,
                        close_price=None,
                    )
                else:
                    session = replace(session, open_execution_state=state)

                result = self.engine.run_session(
                    run=self.run_spec(),
                    signal=self.signal(),
                    session=session,
                    initial_cash=Decimal(case["initial_cash"]),
                )
                status = next(
                    event for event in result.events if event.event_type is EventType.ORDER_STATUS
                )

                self.assertEqual(status.reason_code, case["expected_reason_code"])
                self.assertEqual(status.payload["transition"], case["expected_transition"])
                self.assertEqual(result.result_summary["status"], case["expected_status"])
                self.assertEqual(result.result_summary["ending_cash"], case["expected_ending_cash"])
                self.assertEqual(result.result_summary["rejections"], case["rejections"])
                self.assertEqual(result.result_summary["cancellations"], case["cancellations"])
                self.assertFalse(
                    any(
                        event.event_type in {EventType.FILL, EventType.LEDGER_ENTRY}
                        for event in result.events
                    )
                )

    def test_suspended_session_cannot_carry_invented_prices(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not invent"):
            replace(
                self.session(),
                open_execution_state=OpenExecutionState.SUSPENDED,
            )

    def test_sell_blocked_limit_down_does_not_block_a_buy(self) -> None:
        result = self.engine.run_session(
            run=self.run_spec(),
            signal=self.signal(),
            session=replace(
                self.session(),
                open_execution_state=OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
            ),
            initial_cash=Decimal(self.inputs["initial_cash"]),
        )

        self.assertEqual(result.result_summary["fills"], 1)
        fill = next(event for event in result.events if event.event_type is EventType.FILL)
        self.assertEqual(
            fill.payload["open_execution_state"],
            OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN.value,
        )

    def test_synthetic_policy_cannot_label_a_run_official(self) -> None:
        with self.assertRaisesRegex(BacktestInputError, "requires a frozen execution policy"):
            self.engine.run_session(
                run=replace(self.run_spec(), official=True),
                signal=self.signal(),
                session=self.session(),
                initial_cash=Decimal(self.inputs["initial_cash"]),
            )

    def test_frozen_daily_research_policy_allows_official_replay(self) -> None:
        policy = load_execution_policy(RESEARCH_POLICY_PATH)
        engine = BacktestEngine(policy)
        run = replace(
            self.run_spec(),
            execution_policy_version=policy.policy_version,
            schedule_version=policy.active_profile.schedule_version,
            official=True,
        )

        result = engine.run_session(
            run=run,
            signal=self.signal(),
            session=self.session(),
            initial_cash=Decimal(self.inputs["initial_cash"]),
        )

        self.assertTrue(result.run.official)
        self.assertEqual(result.result_summary["status"], "completed")
        self.assertEqual(result.result_summary["total_equity"], self.expected["total_equity"])

    def test_two_window_execution_fails_explicitly_until_implemented(self) -> None:
        profile_id = "synthetic-two-window-fixture-v1"
        with self.assertRaisesRegex(UnsupportedModeError, "not implemented"):
            self.engine.run_session(
                run=self.run_spec(
                    profile_id=profile_id,
                    schedule_version="synthetic-two-window-schedule-v1",
                ),
                signal=self.signal(profile_id=profile_id, window_id="window_1"),
                session=self.session(),
                initial_cash=Decimal(self.inputs["initial_cash"]),
            )

    def test_hand_calculated_sell_carries_once_per_session_then_fills_and_settles(self) -> None:
        expected = self.sell_fixture["expected"]
        result = self.engine.run_sell_sequence(
            run=self.sell_run_spec(),
            signal=self.sell_signal(),
            sessions=self.sell_sessions(),
            opening_state=self.sell_opening_state(),
        )

        self.assertEqual(
            [event.event_type.value for event in result.events], expected["event_types"]
        )
        statuses = [event for event in result.events if event.event_type is EventType.ORDER_STATUS]
        intent = next(
            event.payload
            for event in result.events
            if event.event_type is EventType.ORDER_INTENT_CREATED
        )
        order = next(
            event.payload for event in result.events if event.event_type is EventType.ORDER_CREATED
        )
        self.assertEqual(
            [event.payload["transition"] for event in statuses],
            expected["status_transitions"],
        )
        self.assertEqual(
            len([event for event in result.events if event.event_type is EventType.ORDER_CREATED]),
            1,
        )
        self.assertIsNone(intent["expires_at"])
        self.assertEqual(intent["unfilled_policy"], "carry_until_tradable")
        self.assertEqual(order["intent_id"], intent["intent_id"])
        carry_snapshots = [
            event.payload for event in result.events if event.event_type is EventType.DAILY_SNAPSHOT
        ][:-1]
        self.assertEqual(
            [snapshot["mark_status"] for snapshot in carry_snapshots],
            expected["carry_mark_statuses"],
        )
        self.assertEqual(
            [snapshot["market_value"] for snapshot in carry_snapshots],
            expected["carry_market_values"],
        )
        fill = next(event.payload for event in result.events if event.event_type is EventType.FILL)
        for field in (
            "price",
            "gross_amount",
            "commission",
            "stamp_duty",
            "transfer_fee",
            "total_fees",
        ):
            expected_field = "fill_price" if field == "price" else field
            self.assertEqual(fill[field], expected[expected_field])
        ledgers = [
            event.payload for event in result.events if event.event_type is EventType.LEDGER_ENTRY
        ]
        for ledger in ledgers:
            self.assertEqual(
                sum(Decimal(posting["amount"]) for posting in ledger["postings"]),
                Decimal("0"),
            )
        settlement = next(
            event.payload
            for event in result.events
            if event.event_type is EventType.UNIT_SETTLEMENT
        )
        final_snapshot = next(
            event.payload
            for event in reversed(result.events)
            if event.event_type is EventType.DAILY_SNAPSHOT
        )
        self.assertEqual(settlement["profit_transfer"], expected["profit_transfer"])
        self.assertEqual(final_snapshot["cash"], expected["ending_cash"])
        self.assertEqual(final_snapshot["external_pool"], expected["ending_external_pool"])
        self.assertEqual(final_snapshot["realized_pnl"], expected["realized_pnl"])
        self.assertEqual(result.result_summary["underfunded_amount"], "0.00")

    def test_carried_sell_remains_pending_without_duplicate_order(self) -> None:
        result = self.engine.run_sell_sequence(
            run=self.sell_run_spec(),
            signal=self.sell_signal(),
            sessions=self.sell_sessions()[:2],
            opening_state=self.sell_opening_state(),
        )

        self.assertEqual(result.result_summary["status"], "pending_sell")
        self.assertEqual(result.result_summary["carries"], 2)
        self.assertEqual(
            len([event for event in result.events if event.event_type is EventType.ORDER_CREATED]),
            1,
        )
        self.assertFalse(any(event.event_type is EventType.FILL for event in result.events))

    def test_empty_external_pool_leaves_losing_unit_underfunded(self) -> None:
        executable_session = replace(
            self.sell_sessions()[-1],
            open_price=Decimal("9.00"),
            close_price=Decimal("9.00"),
        )
        result = self.engine.run_sell_sequence(
            run=self.sell_run_spec(),
            signal=self.sell_signal(),
            sessions=(executable_session,),
            opening_state=self.sell_opening_state(external_pool="0.00"),
        )

        settlement = next(
            event for event in result.events if event.event_type is EventType.UNIT_SETTLEMENT
        )
        self.assertEqual(settlement.reason_code, "unit_underfunded_pool_empty")
        self.assertEqual(result.result_summary["ending_cash"], "9888.54")
        self.assertEqual(result.result_summary["underfunded_amount"], "111.46")
        self.assertEqual(result.result_summary["external_pool"], "0.00")

    def test_upstream_maximum_hold_sell_uses_the_generic_execution_path(self) -> None:
        inputs = self.maximum_hold_fixture["inputs"]
        expected = self.maximum_hold_fixture["expected"]
        session_date = date.fromisoformat(inputs["execution_session"])
        session = replace(
            self.sell_sessions()[-1],
            session_date=session_date,
            open_at=datetime(2026, 7, 28, 9, 30, tzinfo=SHANGHAI),
            close_at=datetime(2026, 7, 28, 15, 0, tzinfo=SHANGHAI),
            open_price=Decimal(inputs["open_price"]),
            close_price=Decimal(inputs["close_price"]),
            snapshot_id="p2-maximum-hold-execution-2026-07-28-v1",
        )
        signal = replace(
            self.sell_signal(),
            reason_code=inputs["decision_reason_code"],
            decision_version=inputs["decision_version"],
        )

        result = self.engine.run_sell_sequence(
            run=replace(self.sell_run_spec(), run_id="p2-maximum-hold-sell-001"),
            signal=signal,
            sessions=(session,),
            opening_state=self.sell_opening_state(),
        )

        self.assertEqual(
            [event.event_type.value for event in result.events], expected["event_types"]
        )
        persisted_signal = result.events[0].payload
        status = next(
            event for event in result.events if event.event_type is EventType.ORDER_STATUS
        )
        fill = next(event for event in result.events if event.event_type is EventType.FILL)
        self.assertEqual(
            persisted_signal["decision_reason_code"],
            expected["strategy_decision_reason_preserved"],
        )
        self.assertEqual(status.payload["transition"], expected["status_transition"])
        self.assertEqual(fill.payload["price"], expected["fill_price"])
        self.assertEqual(result.result_summary["realized_pnl"], expected["realized_pnl"])

    def test_same_day_candidates_rank_by_score_then_security_code(self) -> None:
        candidates = tuple(
            self.signal(
                signal_id=raw["signal_id"],
                security_code=raw["security_code"],
                aggregate_score=Decimal(raw["aggregate_score"]),
            )
            for raw in self.candidate_fixture["candidates"]
        )

        expected_codes = self.candidate_fixture["expected"]["ordered_security_codes"]
        expected_ids = self.candidate_fixture["expected"]["ordered_signal_ids"]
        ranked = rank_signals(candidates)
        self.assertEqual([signal.security_code for signal in ranked], expected_codes)
        self.assertEqual([signal.signal_id for signal in ranked], expected_ids)
        self.assertEqual(
            [signal.security_code for signal in rank_signals(tuple(reversed(candidates)))],
            expected_codes,
        )

    def test_candidate_batch_rejects_duplicate_security(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate candidate security_code"):
            rank_signals(
                (
                    self.signal(signal_id="signal-1"),
                    self.signal(signal_id="signal-2"),
                )
            )

    def test_hand_calculated_cash_dividend_preserves_ex_right_equity(self) -> None:
        inputs = self.action_fixture["inputs"]
        expected = self.action_fixture["expected"]
        application = self.engine.apply_corporate_action(
            run=self.sell_run_spec(),
            opening_state=self.sell_opening_state(),
            action=CorporateAction(
                action_id=inputs["action_id"],
                security_code=inputs["security_code"],
                effective_date=date.fromisoformat(inputs["effective_date"]),
                known_at=dt(inputs["known_at"]),
                cash_dividend_per_share=Decimal(inputs["cash_dividend_per_share"]),
                bonus_share_ratio=Decimal(inputs["bonus_share_ratio"]),
                source_revision="p1-synthetic-corporate-action-v1",
                snapshot_id=inputs["action_snapshot_id"],
            ),
            applied_at=dt(inputs["applied_at"]),
            post_action_mark_price=Decimal(inputs["post_action_mark_price"]),
            mark_snapshot_id=inputs["mark_snapshot_id"],
        )

        self.assertEqual(
            [event.event_type.value for event in application.events], expected["event_types"]
        )
        action_payload = application.events[0].payload
        for field in (
            "cash_dividend",
            "quantity_before",
            "quantity_after",
            "position_cost",
            "cash_before",
            "cash_after",
            "equity_before",
            "equity_after",
        ):
            self.assertEqual(action_payload[field], expected[field])
        self.assertEqual(application.state.dividend_income, Decimal(expected["dividend_income"]))
        ledger = application.events[1].payload
        self.assertEqual(
            sum(Decimal(posting["amount"]) for posting in ledger["postings"]), Decimal("0")
        )

    def test_future_known_corporate_action_fails_point_in_time(self) -> None:
        inputs = self.action_fixture["inputs"]
        with self.assertRaisesRegex(BacktestInputError, "not known"):
            self.engine.apply_corporate_action(
                run=self.sell_run_spec(),
                opening_state=self.sell_opening_state(),
                action=CorporateAction(
                    action_id=inputs["action_id"],
                    security_code=inputs["security_code"],
                    effective_date=date.fromisoformat(inputs["effective_date"]),
                    known_at=dt("2026-07-28T10:00:00+08:00"),
                    cash_dividend_per_share=Decimal(inputs["cash_dividend_per_share"]),
                    bonus_share_ratio=Decimal("0.00"),
                    source_revision="p1-synthetic-corporate-action-v1",
                    snapshot_id=inputs["action_snapshot_id"],
                ),
                applied_at=dt(inputs["applied_at"]),
                post_action_mark_price=Decimal(inputs["post_action_mark_price"]),
                mark_snapshot_id=inputs["mark_snapshot_id"],
            )

    def test_bonus_shares_change_quantity_without_rewriting_total_cost(self) -> None:
        inputs = self.action_fixture["inputs"]
        application = self.engine.apply_corporate_action(
            run=self.sell_run_spec(),
            opening_state=self.sell_opening_state(),
            action=CorporateAction(
                action_id="action-bonus-shares-001",
                security_code=inputs["security_code"],
                effective_date=date.fromisoformat(inputs["effective_date"]),
                known_at=dt(inputs["known_at"]),
                cash_dividend_per_share=Decimal("0.00"),
                bonus_share_ratio=Decimal("0.02"),
                source_revision="p1-synthetic-corporate-action-v1",
                snapshot_id=inputs["action_snapshot_id"],
            ),
            applied_at=dt(inputs["applied_at"]),
            post_action_mark_price=Decimal("10.00"),
            mark_snapshot_id=inputs["mark_snapshot_id"],
        )

        self.assertEqual(application.state.settled_quantity, 102)
        self.assertEqual(application.state.position_cost, Decimal("1001.00"))
        self.assertEqual(application.events[0].payload["equity_before"], "10014.00")
        self.assertEqual(application.events[0].payload["equity_after"], "10014.00")
        self.assertEqual(len(application.events), 1)


if __name__ == "__main__":
    unittest.main()
