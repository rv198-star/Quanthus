import copy
import hashlib
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from quanthus.concentration import (
    ActiveUnitExposure,
    ConcentrationError,
    DailyEntryReservation,
    EntryCapacityCandidate,
    StaticIndustryMembership,
    evaluate_entry_capacity,
)
from quanthus.policy import P0Policy, load_policy
from quanthus.position_lifecycle import PositionStage

MARKET_DATE = date(2025, 1, 2)


def digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def membership(
    security_code: str,
    industry_id: str,
    *,
    mapping_version: str = "static-level1-v1",
) -> StaticIndustryMembership:
    return StaticIndustryMembership(
        security_code=security_code,
        level1_industry_id=industry_id,
        mapping_version=mapping_version,
        source_revision="industry-source-v1",
        lineage_ref=f"lineage:{security_code}",
    )


def exposure(sequence: int, industry_id: str = "industry-a") -> ActiveUnitExposure:
    return ActiveUnitExposure(
        unit_id=f"unit-{sequence}",
        opportunity_id=f"opportunity-{sequence}",
        lifecycle_stage=PositionStage.TRIAL,
        lifecycle_digest=digest(f"lifecycle-{sequence}"),
        membership=membership(f"6000{sequence:02d}", industry_id),
    )


def reservation(sequence: int, industry_id: str = "industry-b") -> DailyEntryReservation:
    return DailyEntryReservation(
        reservation_id=f"reservation-{sequence}",
        decision_id=f"decision-{sequence}",
        market_date=MARKET_DATE,
        unit_id=f"reserved-unit-{sequence}",
        opportunity_id=f"reserved-opportunity-{sequence}",
        security_code=f"0000{sequence:02d}",
        level1_industry_id=industry_id,
        industry_mapping_version="static-level1-v1",
    )


def candidate() -> EntryCapacityCandidate:
    return EntryCapacityCandidate(
        unit_id="candidate-unit",
        opportunity_id="candidate-opportunity",
        membership=membership("600999", "industry-a"),
    )


class ConcentrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_capacity_below_both_frozen_caps_is_allowed_without_execution_authority(self) -> None:
        result = evaluate_entry_capacity(
            self.policy,
            candidate(),
            market_date=MARKET_DATE,
            active_exposures=(exposure(1),),
            accepted_entries=(reservation(1),),
        )

        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_codes, ("entry_capacity_available",))
        self.assertEqual(result.max_units_per_level1_industry, 2)
        self.assertEqual(result.max_new_units_per_day, 2)
        self.assertFalse(result.execution_authorized)

    def test_two_existing_industry_units_reject_with_industry_cap(self) -> None:
        result = evaluate_entry_capacity(
            self.policy,
            candidate(),
            market_date=MARKET_DATE,
            active_exposures=(exposure(1), exposure(2)),
            accepted_entries=(),
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_codes, ("industry_cap",))
        self.assertEqual(result.active_industry_units_before, 2)

    def test_same_day_industry_reservation_consumes_capacity_before_fill(self) -> None:
        result = evaluate_entry_capacity(
            self.policy,
            candidate(),
            market_date=MARKET_DATE,
            active_exposures=(exposure(1),),
            accepted_entries=(reservation(1, "industry-a"),),
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_codes, ("industry_cap",))
        self.assertEqual(result.active_industry_units_before, 1)
        self.assertEqual(result.reserved_industry_units_before, 1)

    def test_two_daily_reservations_reject_with_daily_open_cap(self) -> None:
        result = evaluate_entry_capacity(
            self.policy,
            candidate(),
            market_date=MARKET_DATE,
            active_exposures=(),
            accepted_entries=(reservation(1), reservation(2)),
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_codes, ("daily_open_cap",))

    def test_simultaneous_cap_failures_have_fixed_reason_order(self) -> None:
        result = evaluate_entry_capacity(
            self.policy,
            candidate(),
            market_date=MARKET_DATE,
            active_exposures=(exposure(1), exposure(2)),
            accepted_entries=(reservation(1), reservation(2)),
        )

        self.assertEqual(result.reason_codes, ("industry_cap", "daily_open_cap"))

    def test_input_permutation_cannot_change_decision_or_digest(self) -> None:
        exposures = (exposure(2), exposure(1, "industry-b"))
        entries = (reservation(2), reservation(1))

        first = evaluate_entry_capacity(
            self.policy,
            candidate(),
            market_date=MARKET_DATE,
            active_exposures=exposures,
            accepted_entries=entries,
        )
        second = evaluate_entry_capacity(
            self.policy,
            candidate(),
            market_date=MARKET_DATE,
            active_exposures=tuple(reversed(exposures)),
            accepted_entries=tuple(reversed(entries)),
        )

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_static_industry_usage_and_mapping_version_fail_closed(self) -> None:
        with self.assertRaises(ConcentrationError) as usage:
            replace(
                membership("600001", "industry-a"),
                usage="sector_expert",  # type: ignore[arg-type]
            )
        self.assertEqual(usage.exception.reason_code, "invalid_industry_usage")

        changed = replace(
            exposure(1),
            membership=membership("600001", "industry-a", mapping_version="static-level1-v2"),
        )
        with self.assertRaises(ConcentrationError) as version:
            evaluate_entry_capacity(
                self.policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(changed,),
                accepted_entries=(),
            )
        self.assertEqual(version.exception.reason_code, "industry_mapping_mismatch")

    def test_non_positioned_lifecycle_cannot_count_as_active_exposure(self) -> None:
        with self.assertRaises(ConcentrationError) as raised:
            replace(exposure(1), lifecycle_stage=PositionStage.EMPTY)

        self.assertEqual(raised.exception.reason_code, "invalid_active_exposure")

    def test_candidate_unit_must_be_empty_and_unreserved(self) -> None:
        active_candidate = replace(exposure(1), unit_id=candidate().unit_id)
        with self.assertRaises(ConcentrationError) as active:
            evaluate_entry_capacity(
                self.policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(active_candidate,),
                accepted_entries=(),
            )
        self.assertEqual(active.exception.reason_code, "unit_not_empty")

        reserved_candidate = replace(reservation(1), unit_id=candidate().unit_id)
        with self.assertRaises(ConcentrationError) as reserved:
            evaluate_entry_capacity(
                self.policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(),
                accepted_entries=(reserved_candidate,),
            )
        self.assertEqual(reserved.exception.reason_code, "unit_already_reserved")

    def test_duplicate_and_wrong_date_evidence_fail_closed(self) -> None:
        with self.assertRaises(ConcentrationError) as duplicate:
            evaluate_entry_capacity(
                self.policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(exposure(1), exposure(1)),
                accepted_entries=(),
            )
        self.assertEqual(duplicate.exception.reason_code, "duplicate_capacity_evidence")

        conflicting_reservation = replace(reservation(1), unit_id=exposure(1).unit_id)
        with self.assertRaises(ConcentrationError) as conflict:
            evaluate_entry_capacity(
                self.policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(exposure(1),),
                accepted_entries=(conflicting_reservation,),
            )
        self.assertEqual(conflict.exception.reason_code, "unit_evidence_conflict")

        wrong_date = replace(reservation(1), market_date=date(2025, 1, 3))
        with self.assertRaises(ConcentrationError) as market_date_error:
            evaluate_entry_capacity(
                self.policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(),
                accepted_entries=(wrong_date,),
            )
        self.assertEqual(market_date_error.exception.reason_code, "market_date_mismatch")

    def test_policy_must_preserve_concentration_only_mapping_and_positive_limits(self) -> None:
        raw = copy.deepcopy(dict(self.policy.raw))
        raw["data"]["level1_industry_mapping"] = "sector_expert_allowed"
        invalid_usage_policy = P0Policy(Path("invalid"), raw)
        with self.assertRaises(ConcentrationError) as usage:
            evaluate_entry_capacity(
                invalid_usage_policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(),
                accepted_entries=(),
            )
        self.assertEqual(usage.exception.reason_code, "invalid_industry_policy")

        raw = copy.deepcopy(dict(self.policy.raw))
        raw["risk"]["max_new_units_per_day"] = 0
        invalid_limit_policy = P0Policy(Path("invalid"), raw)
        with self.assertRaises(ConcentrationError) as limit:
            evaluate_entry_capacity(
                invalid_limit_policy,
                candidate(),
                market_date=MARKET_DATE,
                active_exposures=(),
                accepted_entries=(),
            )
        self.assertEqual(limit.exception.reason_code, "invalid_risk_policy")


if __name__ == "__main__":
    unittest.main()
