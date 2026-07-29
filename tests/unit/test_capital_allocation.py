import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from quanthus.backtest.contracts import UnitState
from quanthus.capital_allocation import (
    AllocationDisposition,
    AvailableCapitalUnit,
    CapitalAllocationError,
    EntryAllocationCandidate,
    allocate_entry_reservations,
)
from quanthus.concentration import (
    ActiveUnitExposure,
    DailyEntryReservation,
    StaticIndustryMembership,
)
from quanthus.policy import load_policy
from quanthus.position_lifecycle import (
    PositionStage,
    initialize_position_lifecycle,
)

MARKET_DATE = date(2025, 1, 2)
AS_OF = datetime(2025, 1, 2, 7, 0, tzinfo=timezone.utc)


def membership(security_code: str, industry_id: str) -> StaticIndustryMembership:
    return StaticIndustryMembership(
        security_code=security_code,
        level1_industry_id=industry_id,
        mapping_version="static-level1-v1",
        source_revision="industry-source-v1",
        lineage_ref=f"lineage:{security_code}",
    )


def candidate(
    sequence: int,
    score: str,
    security_code: str,
    industry_id: str,
) -> EntryAllocationCandidate:
    return EntryAllocationCandidate(
        decision_id=f"decision-{sequence}",
        opportunity_id=f"opportunity-{sequence}",
        aggregate_score=Decimal(score),
        membership=membership(security_code, industry_id),
    )


def available(unit_id: str) -> AvailableCapitalUnit:
    ledger = UnitState.empty(unit_id=unit_id, cash=Decimal("10000.00"))
    return AvailableCapitalUnit(
        lifecycle=initialize_position_lifecycle(ledger_state=ledger, as_of=AS_OF),
        ledger=ledger,
    )


def active(sequence: int, industry_id: str) -> ActiveUnitExposure:
    lifecycle = replace(
        available(f"active-{sequence}").lifecycle,
        stage=PositionStage.TRIAL,
        security_code=f"6000{sequence:02d}",
        opportunity_id=f"active-opportunity-{sequence}",
    )
    return ActiveUnitExposure(
        unit_id=lifecycle.unit_id,
        opportunity_id=str(lifecycle.opportunity_id),
        lifecycle_stage=lifecycle.stage,
        lifecycle_digest=lifecycle.digest,
        membership=membership(str(lifecycle.security_code), industry_id),
    )


class CapitalAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_candidates_compete_by_frozen_priority_and_units_by_identity(self) -> None:
        candidates = (
            candidate(1, "0.40", "600003", "industry-c"),
            candidate(2, "0.90", "600002", "industry-b"),
            candidate(3, "0.90", "600001", "industry-a"),
        )

        result = allocate_entry_reservations(
            self.policy,
            market_date=MARKET_DATE,
            candidates=candidates,
            available_units=(available("unit-b"), available("unit-a"), available("unit-c")),
            active_exposures=(),
            accepted_entries=(),
        )

        self.assertEqual(
            result.candidate_order,
            ("decision-3", "decision-2", "decision-1"),
        )
        self.assertEqual(
            [decision.disposition for decision in result.decisions],
            [
                AllocationDisposition.RESERVED,
                AllocationDisposition.RESERVED,
                AllocationDisposition.REJECTED,
            ],
        )
        self.assertEqual(
            [item.unit_id for item in result.new_reservations],
            ["unit-a", "unit-b"],
        )
        self.assertEqual(result.decisions[-1].reason_codes, ("daily_open_cap",))
        self.assertTrue(all(not item.execution_authorized for item in result.decisions))
        self.assertFalse(result.execution_authorized)

    def test_industry_rejection_does_not_consume_the_available_unit(self) -> None:
        result = allocate_entry_reservations(
            self.policy,
            market_date=MARKET_DATE,
            candidates=(
                candidate(1, "0.90", "600010", "industry-a"),
                candidate(2, "0.80", "600020", "industry-b"),
            ),
            available_units=(available("unit-a"),),
            active_exposures=(active(1, "industry-a"), active(2, "industry-a")),
            accepted_entries=(),
        )

        self.assertEqual(result.decisions[0].reason_codes, ("industry_cap",))
        self.assertEqual(result.decisions[1].disposition, AllocationDisposition.RESERVED)
        self.assertEqual(result.new_reservations[0].unit_id, "unit-a")

    def test_input_permutation_produces_the_same_canonical_batch(self) -> None:
        candidates = (
            candidate(1, "0.70", "600001", "industry-a"),
            candidate(2, "0.60", "600002", "industry-b"),
        )
        units = (available("unit-b"), available("unit-a"))

        first = allocate_entry_reservations(
            self.policy,
            market_date=MARKET_DATE,
            candidates=candidates,
            available_units=units,
            active_exposures=(),
            accepted_entries=(),
        )
        second = allocate_entry_reservations(
            self.policy,
            market_date=MARKET_DATE,
            candidates=tuple(reversed(candidates)),
            available_units=tuple(reversed(units)),
            active_exposures=(),
            accepted_entries=(),
        )

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_nonempty_or_duplicate_unit_evidence_fails_closed(self) -> None:
        empty = available("unit-a")
        positioned_ledger = replace(
            empty.ledger,
            cash=Decimal("9000.00"),
            security_code="600001",
            settled_quantity=100,
            position_cost=Decimal("1000.00"),
            last_mark_price=Decimal("10.00"),
            last_mark_snapshot_id="mark-1",
        )
        with self.assertRaisesRegex(CapitalAllocationError, "unit_not_available"):
            AvailableCapitalUnit(lifecycle=empty.lifecycle, ledger=positioned_ledger)

        with self.assertRaisesRegex(CapitalAllocationError, "duplicate available unit"):
            allocate_entry_reservations(
                self.policy,
                market_date=MARKET_DATE,
                candidates=(candidate(1, "0.50", "600001", "industry-a"),),
                available_units=(empty, empty),
                active_exposures=(),
                accepted_entries=(),
            )

    def test_active_and_reserved_identity_conflict_fails_closed(self) -> None:
        exposure = active(1, "industry-a")
        reservation = DailyEntryReservation(
            reservation_id="reservation-existing",
            decision_id="decision-existing",
            market_date=MARKET_DATE,
            unit_id=exposure.unit_id,
            opportunity_id="opportunity-existing",
            security_code="600099",
            level1_industry_id="industry-b",
            industry_mapping_version="static-level1-v1",
        )

        with self.assertRaisesRegex(CapitalAllocationError, "unit_evidence_conflict"):
            allocate_entry_reservations(
                self.policy,
                market_date=MARKET_DATE,
                candidates=(candidate(1, "0.50", "600001", "industry-a"),),
                available_units=(available("unit-a"),),
                active_exposures=(exposure,),
                accepted_entries=(reservation,),
            )


if __name__ == "__main__":
    unittest.main()
