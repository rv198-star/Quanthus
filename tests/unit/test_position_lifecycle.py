import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quanthus.backtest.contracts import UnitState
from quanthus.position_lifecycle import (
    PositionEvidenceKind,
    PositionEvidenceRef,
    PositionLifecycleError,
    PositionStage,
    PositionTransition,
    PositionTransitionKind,
    apply_position_transition,
    initialize_position_lifecycle,
)

BASE_TIME = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)

EVIDENCE_KIND = {
    PositionTransitionKind.ENTRY_FILLED: (PositionEvidenceKind.FILL,),
    PositionTransitionKind.ADD_FILLED: (PositionEvidenceKind.FILL,),
    PositionTransitionKind.REDUCE_FILLED: (PositionEvidenceKind.FILL,),
    PositionTransitionKind.EXIT_REQUESTED: (PositionEvidenceKind.DECISION,),
    PositionTransitionKind.EXIT_CARRIED: (PositionEvidenceKind.ORDER_STATUS,),
    PositionTransitionKind.EXIT_FILLED: (PositionEvidenceKind.FILL,),
    PositionTransitionKind.COOLDOWN_COMPLETED: (
        PositionEvidenceKind.UNIT_SETTLEMENT,
        PositionEvidenceKind.CALENDAR,
    ),
}


def ledger(quantity: int, *, mark: str = "10.00") -> UnitState:
    if quantity == 0:
        return UnitState.empty(unit_id="unit-01", cash=Decimal("10000.00"))
    return UnitState(
        unit_id="unit-01",
        target_notional=Decimal("10000.00"),
        cash=Decimal("5000.00"),
        security_code="600000",
        settled_quantity=quantity,
        position_cost=Decimal("5000.00"),
        commission_expense=Decimal("5.00"),
        stamp_duty_expense=Decimal("0.00"),
        transfer_fee_expense=Decimal("0.00"),
        realized_pnl=Decimal("0.00"),
        dividend_income=Decimal("0.00"),
        external_pool=Decimal("0.00"),
        last_mark_price=Decimal(mark),
        last_mark_snapshot_id=f"snapshot-{mark}",
    )


def transition(
    kind: PositionTransitionKind,
    sequence: int,
    *,
    evidence_id: str | None = None,
    evidence_order_reversed: bool = False,
) -> PositionTransition:
    evidence = tuple(
        PositionEvidenceRef(
            kind=evidence_kind,
            evidence_id=evidence_id or f"evidence-{sequence}-{evidence_kind.value}",
            source_version="1.0.0",
        )
        for evidence_kind in EVIDENCE_KIND[kind]
    )
    if evidence_order_reversed:
        evidence = tuple(reversed(evidence))
    return PositionTransition(
        transition_id=f"transition-{sequence}",
        kind=kind,
        unit_id="unit-01",
        opportunity_id="opportunity-01",
        security_code="600000",
        occurred_at=BASE_TIME + timedelta(hours=sequence),
        reason_codes=(f"reason_{sequence}",),
        evidence=evidence,
    )


class PositionLifecycleTests(unittest.TestCase):
    def test_complete_legal_lifecycle_reconciles_each_p2_ledger_delta(self) -> None:
        empty = ledger(0)
        state = initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME)

        trial_ledger = ledger(100)
        state = apply_position_transition(
            state,
            transition(PositionTransitionKind.ENTRY_FILLED, 1),
            ledger_before=empty,
            ledger_after=trial_ledger,
        )
        self.assertEqual(state.stage, PositionStage.TRIAL)

        dynamic_ledger = ledger(200)
        state = apply_position_transition(
            state,
            transition(PositionTransitionKind.ADD_FILLED, 2),
            ledger_before=trial_ledger,
            ledger_after=dynamic_ledger,
        )
        self.assertEqual(state.stage, PositionStage.DYNAMIC)

        reduced_ledger = ledger(150)
        state = apply_position_transition(
            state,
            transition(PositionTransitionKind.REDUCE_FILLED, 3),
            ledger_before=dynamic_ledger,
            ledger_after=reduced_ledger,
        )
        state = apply_position_transition(
            state,
            transition(PositionTransitionKind.EXIT_REQUESTED, 4),
            ledger_before=reduced_ledger,
            ledger_after=reduced_ledger,
        )
        self.assertEqual(state.stage, PositionStage.EXITING)

        carried_ledger = ledger(150, mark="9.50")
        state = apply_position_transition(
            state,
            transition(PositionTransitionKind.EXIT_CARRIED, 5),
            ledger_before=reduced_ledger,
            ledger_after=carried_ledger,
        )
        closed_ledger = ledger(0)
        state = apply_position_transition(
            state,
            transition(PositionTransitionKind.EXIT_FILLED, 6),
            ledger_before=carried_ledger,
            ledger_after=closed_ledger,
        )
        self.assertEqual(state.stage, PositionStage.CLOSED)
        self.assertEqual(state.security_code, "600000")

        state = apply_position_transition(
            state,
            transition(PositionTransitionKind.COOLDOWN_COMPLETED, 7),
            ledger_before=closed_ledger,
            ledger_after=closed_ledger,
        )
        self.assertEqual(state.stage, PositionStage.EMPTY)
        self.assertIsNone(state.security_code)
        self.assertIsNone(state.opportunity_id)
        self.assertEqual(len(state.transition_ids), 7)

    def test_empty_unit_can_only_become_trial_after_an_entry_fill(self) -> None:
        empty = ledger(0)
        state = initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME)

        with self.assertRaises(PositionLifecycleError) as raised:
            apply_position_transition(
                state,
                transition(PositionTransitionKind.ADD_FILLED, 1),
                ledger_before=empty,
                ledger_after=ledger(100),
            )

        self.assertEqual(raised.exception.reason_code, "illegal_transition")

    def test_trial_cannot_reduce_or_return_directly_to_empty(self) -> None:
        empty = ledger(0)
        trial_ledger = ledger(100)
        state = apply_position_transition(
            initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME),
            transition(PositionTransitionKind.ENTRY_FILLED, 1),
            ledger_before=empty,
            ledger_after=trial_ledger,
        )

        with self.assertRaises(PositionLifecycleError) as raised:
            apply_position_transition(
                state,
                transition(PositionTransitionKind.REDUCE_FILLED, 2),
                ledger_before=trial_ledger,
                ledger_after=ledger(50),
            )

        self.assertEqual(raised.exception.reason_code, "illegal_transition")

    def test_add_and_reduce_require_proven_quantity_direction(self) -> None:
        empty = ledger(0)
        trial_ledger = ledger(100)
        trial = apply_position_transition(
            initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME),
            transition(PositionTransitionKind.ENTRY_FILLED, 1),
            ledger_before=empty,
            ledger_after=trial_ledger,
        )

        with self.assertRaises(PositionLifecycleError) as raised:
            apply_position_transition(
                trial,
                transition(PositionTransitionKind.ADD_FILLED, 2),
                ledger_before=trial_ledger,
                ledger_after=ledger(100),
            )

        self.assertEqual(raised.exception.reason_code, "invalid_ledger_delta")

    def test_exit_request_and_carry_cannot_mutate_position_quantity(self) -> None:
        empty = ledger(0)
        trial_ledger = ledger(100)
        trial = apply_position_transition(
            initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME),
            transition(PositionTransitionKind.ENTRY_FILLED, 1),
            ledger_before=empty,
            ledger_after=trial_ledger,
        )

        with self.assertRaises(PositionLifecycleError) as raised:
            apply_position_transition(
                trial,
                transition(PositionTransitionKind.EXIT_REQUESTED, 2),
                ledger_before=trial_ledger,
                ledger_after=ledger(50),
            )

        self.assertEqual(raised.exception.reason_code, "invalid_ledger_delta")

    def test_closed_state_requires_empty_p2_ledger_before_cooldown(self) -> None:
        empty = ledger(0)
        trial_ledger = ledger(100)
        trial = apply_position_transition(
            initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME),
            transition(PositionTransitionKind.ENTRY_FILLED, 1),
            ledger_before=empty,
            ledger_after=trial_ledger,
        )
        exiting = apply_position_transition(
            trial,
            transition(PositionTransitionKind.EXIT_REQUESTED, 2),
            ledger_before=trial_ledger,
            ledger_after=trial_ledger,
        )

        with self.assertRaises(PositionLifecycleError) as raised:
            apply_position_transition(
                exiting,
                transition(PositionTransitionKind.EXIT_FILLED, 3),
                ledger_before=trial_ledger,
                ledger_after=ledger(1),
            )

        self.assertEqual(raised.exception.reason_code, "invalid_ledger_delta")

    def test_cooldown_requires_settlement_and_calendar_evidence(self) -> None:
        with self.assertRaises(PositionLifecycleError) as raised:
            PositionTransition(
                transition_id="transition-1",
                kind=PositionTransitionKind.COOLDOWN_COMPLETED,
                unit_id="unit-01",
                opportunity_id="opportunity-01",
                security_code="600000",
                occurred_at=BASE_TIME + timedelta(hours=1),
                reason_codes=("cooldown_complete",),
                evidence=(
                    PositionEvidenceRef(
                        PositionEvidenceKind.UNIT_SETTLEMENT,
                        "settlement-01",
                        "1.0.0",
                    ),
                ),
            )

        self.assertEqual(raised.exception.reason_code, "invalid_transition_evidence")

    def test_reused_transition_or_evidence_fails_closed(self) -> None:
        empty = ledger(0)
        trial_ledger = ledger(100)
        trial = apply_position_transition(
            initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME),
            transition(PositionTransitionKind.ENTRY_FILLED, 1),
            ledger_before=empty,
            ledger_after=trial_ledger,
        )
        dynamic_ledger = ledger(200)
        reused = transition(
            PositionTransitionKind.ADD_FILLED,
            2,
            evidence_id="shared-fill",
        )
        dynamic = apply_position_transition(
            trial,
            reused,
            ledger_before=trial_ledger,
            ledger_after=dynamic_ledger,
        )

        with self.assertRaises(PositionLifecycleError) as transition_error:
            apply_position_transition(
                dynamic,
                replace(reused, occurred_at=BASE_TIME + timedelta(hours=3)),
                ledger_before=dynamic_ledger,
                ledger_after=ledger(300),
            )
        self.assertEqual(transition_error.exception.reason_code, "duplicate_transition")

        with self.assertRaises(PositionLifecycleError) as evidence_error:
            apply_position_transition(
                dynamic,
                transition(
                    PositionTransitionKind.ADD_FILLED,
                    3,
                    evidence_id="shared-fill",
                ),
                ledger_before=dynamic_ledger,
                ledger_after=ledger(300),
            )
        self.assertEqual(evidence_error.exception.reason_code, "duplicate_evidence")

    def test_position_identity_cannot_change_inside_one_lifecycle(self) -> None:
        empty = ledger(0)
        trial_ledger = ledger(100)
        trial = apply_position_transition(
            initialize_position_lifecycle(ledger_state=empty, as_of=BASE_TIME),
            transition(PositionTransitionKind.ENTRY_FILLED, 1),
            ledger_before=empty,
            ledger_after=trial_ledger,
        )
        changed = replace(
            transition(PositionTransitionKind.EXIT_REQUESTED, 2),
            opportunity_id="opportunity-02",
        )

        with self.assertRaises(PositionLifecycleError) as raised:
            apply_position_transition(
                trial,
                changed,
                ledger_before=trial_ledger,
                ledger_after=trial_ledger,
            )

        self.assertEqual(raised.exception.reason_code, "transition_identity_mismatch")

    def test_transition_serialization_is_evidence_order_independent(self) -> None:
        first = transition(PositionTransitionKind.COOLDOWN_COMPLETED, 1)
        second = transition(
            PositionTransitionKind.COOLDOWN_COMPLETED,
            1,
            evidence_order_reversed=True,
        )

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_initial_state_rejects_a_positioned_p2_unit(self) -> None:
        with self.assertRaises(PositionLifecycleError) as raised:
            initialize_position_lifecycle(ledger_state=ledger(100), as_of=BASE_TIME)

        self.assertEqual(raised.exception.reason_code, "ledger_state_mismatch")


if __name__ == "__main__":
    unittest.main()
