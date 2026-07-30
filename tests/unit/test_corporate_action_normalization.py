import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quanthus.corporate_action_normalization import (
    AuthoritativeCorporateActionEvidence,
    CashUnit,
    CorporateActionNormalizationError,
    CorporateActionNormalizationPolicy,
    CorporateActionSourceEvidence,
    CrossCheckBaseline,
    FactorCrossCheckStatus,
    NormalizationBasis,
    audit_corporate_action_normalization,
    load_corporate_action_normalization_policy,
    normalize_corporate_action,
)
from quanthus.policy import ConfigurationError, find_project_root

SHANGHAI = ZoneInfo("Asia/Shanghai")


def factor_after(
    prior_close: str,
    cash_per_share: str,
    share_ratio: str,
    factor_before: str = "0.8",
) -> Decimal:
    price = Decimal(prior_close)
    cash = Decimal(cash_per_share)
    shares = Decimal(share_ratio)
    return Decimal(factor_before) * (Decimal("1") + shares) * price / (price - cash)


def evidence(
    *,
    action_id: str = "action-001",
    raw_cash: str = "0.218",
    raw_bonus: str = "0",
    raw_transfer: str = "0",
    prior_close: str | None = "13.00",
    before: str | None = "0.8",
    after: Decimal | None = None,
) -> CorporateActionSourceEvidence:
    if after is None and prior_close is not None and before is not None:
        after = factor_after(prior_close, raw_cash, "0", before)
    return CorporateActionSourceEvidence(
        action_id=action_id,
        security_code="000001.SZ",
        effective_date=date(2020, 5, 28),
        known_at=datetime(2020, 5, 22, 15, tzinfo=SHANGHAI),
        raw_cash_value=Decimal(raw_cash),
        raw_bonus_shares=Decimal(raw_bonus),
        raw_transfer_shares=Decimal(raw_transfer),
        prior_close=None if prior_close is None else Decimal(prior_close),
        factor_before=None if before is None else Decimal(before),
        factor_after=after,
        cross_check_baseline=(
            None
            if prior_close is None or before is None or after is None
            else CrossCheckBaseline.PRIOR_FACTOR
        ),
        source_revision="corporate-actions-v1",
        source_row_ref="row:action-001",
        snapshot_id="p1-corporate-action-snapshot-v1",
    )


class CorporateActionNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_corporate_action_normalization_policy()

    def test_v3_changes_only_policy_and_exception_ledger_identities(self) -> None:
        root = find_project_root()
        v2 = load_corporate_action_normalization_policy(
            root / "config" / "p1-corporate-action-normalization.v2.json"
        )
        v3 = load_corporate_action_normalization_policy(
            root / "config" / "p1-corporate-action-normalization.v3.json"
        )

        self.assertEqual(
            replace(
                v3,
                policy_version=v2.policy_version,
                exception_ledger_version=v2.exception_ledger_version,
            ),
            v2,
        )
        self.assertEqual(v3.policy_version, "p1-corporate-action-normalization-20260730-v3")
        self.assertEqual(
            v3.exception_ledger_version,
            "p1-corporate-action-exceptions-20260730-v2",
        )

    def test_per_share_cash_is_selected_by_factor_evidence(self) -> None:
        result = normalize_corporate_action(evidence(), self.policy)

        self.assertEqual(result.cash_unit, CashUnit.PER_SHARE)
        self.assertEqual(result.cash_dividend_per_share, Decimal("0.21800000"))
        self.assertEqual(result.bonus_share_ratio, Decimal("0E-8"))
        self.assertEqual(result.factor_ratio_error, Decimal("0E-12"))

    def test_per_ten_cash_and_share_quantities_are_normalized(self) -> None:
        item = evidence(
            action_id="action-002",
            raw_cash="2.28",
            raw_bonus="6",
            prior_close="13.01",
            after=factor_after("13.01", "0.228", "0.6"),
        )

        result = normalize_corporate_action(item, self.policy)

        self.assertEqual(result.cash_unit, CashUnit.PER_TEN_SHARES)
        self.assertEqual(result.cash_dividend_per_share, Decimal("0.22800000"))
        self.assertEqual(result.bonus_share_ratio, Decimal("0.60000000"))

    def test_invalid_per_share_cash_does_not_hide_valid_per_ten_candidate(self) -> None:
        item = evidence(
            raw_cash="12",
            prior_close="10",
            after=factor_after("10", "1.2", "0"),
        )

        result = normalize_corporate_action(item, self.policy)

        self.assertEqual(result.cash_unit, CashUnit.PER_TEN_SHARES)
        self.assertEqual(result.cash_dividend_per_share, Decimal("1.20000000"))

    def test_transfer_and_bonus_shares_share_the_per_ten_source_unit(self) -> None:
        item = evidence(
            raw_cash="0",
            raw_bonus="1",
            raw_transfer="2",
            prior_close="20",
            after=factor_after("20", "0", "0.3"),
        )

        result = normalize_corporate_action(item, self.policy)

        self.assertEqual(result.cash_unit, CashUnit.ZERO)
        self.assertEqual(result.bonus_share_ratio, Decimal("0.30000000"))

    def test_ambiguous_cash_unit_fails_closed(self) -> None:
        raw_expected = factor_after("10", "0.1", "0")
        tenth_expected = factor_after("10", "0.01", "0")
        midpoint = (raw_expected + tenth_expected) / 2

        with self.assertRaises(CorporateActionNormalizationError) as raised:
            normalize_corporate_action(
                evidence(raw_cash="0.1", prior_close="10", after=midpoint), self.policy
            )

        self.assertEqual(raised.exception.reason_code, "cash_unit_ambiguous")

    def test_factor_mismatch_and_missing_cross_check_are_exceptions(self) -> None:
        mismatch = evidence(action_id="action-mismatch", after=Decimal("1.5"))
        missing = evidence(
            action_id="action-missing",
            prior_close=None,
            before=None,
            after=None,
        )

        report = audit_corporate_action_normalization((mismatch, missing), self.policy)

        self.assertEqual(
            [item.reason_code for item in report.exceptions],
            ["factor_ratio_mismatch", "missing_cross_check"],
        )
        self.assertFalse(report.ready_for_snapshot)
        self.assertFalse(report.execution_authorized)

    def test_authoritative_plan_normalizes_while_factor_mismatch_remains_visible(self) -> None:
        authority = AuthoritativeCorporateActionEvidence(
            security_code="000001.SZ",
            effective_date=date(2020, 5, 28),
            known_at=datetime(2020, 5, 22, 23, 59, 59, tzinfo=SHANGHAI),
            cash_per_ten_shares=Decimal("2.18"),
            bonus_per_ten_shares=Decimal("0"),
            transfer_per_ten_shares=Decimal("0"),
            source_cash_unit=CashUnit.PER_SHARE,
            assignment_progress="实施分配",
            implementation_plan="10派2.18元(含税,扣税后1.962元)",
            source_revision="eastmoney-test-v1",
            source_row_ref="em_raw/000001.json#rows[0]",
            source_file_digest=f"sha256:{'1' * 64}",
            source_row_digest=f"sha256:{'2' * 64}",
        )
        item = replace(
            evidence(after=Decimal("1.5")),
            authoritative_evidence=authority,
        )

        result = normalize_corporate_action(item, self.policy)

        self.assertEqual(
            result.normalization_basis,
            NormalizationBasis.EASTMONEY_IMPLEMENTATION_PLAN,
        )
        self.assertEqual(result.factor_cross_check_status, FactorCrossCheckStatus.MISMATCH)
        self.assertEqual(result.cash_dividend_per_share, Decimal("0.21800000"))
        self.assertIsNotNone(result.authority_evidence_digest)

    def test_input_order_cannot_change_report_or_digest(self) -> None:
        first_item = evidence(action_id="action-001")
        second_item = replace(
            evidence(action_id="action-002"),
            effective_date=date(2020, 6, 1),
            known_at=datetime(2020, 5, 25, 15, tzinfo=SHANGHAI),
        )

        first = audit_corporate_action_normalization((first_item, second_item), self.policy)
        second = audit_corporate_action_normalization((second_item, first_item), self.policy)

        self.assertTrue(first.ready_for_snapshot)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())

    def test_duplicate_action_identity_is_rejected(self) -> None:
        item = evidence()
        with self.assertRaises(CorporateActionNormalizationError) as raised:
            audit_corporate_action_normalization((item, item), self.policy)

        self.assertEqual(raised.exception.reason_code, "duplicate_action")

    def test_cross_check_policy_order_is_exact(self) -> None:
        with self.assertRaises(ConfigurationError):
            CorporateActionNormalizationPolicy(
                policy_version="invalid-order-v1",
                maximum_factor_ratio_error=self.policy.maximum_factor_ratio_error,
                minimum_candidate_error_separation=(self.policy.minimum_candidate_error_separation),
                cross_check_baselines=tuple(reversed(self.policy.cross_check_baselines)),
            )

    def test_action_must_be_known_before_effective_date(self) -> None:
        with self.assertRaises(CorporateActionNormalizationError) as raised:
            replace(
                evidence(),
                known_at=datetime(2020, 5, 28, 9, tzinfo=SHANGHAI),
            )

        self.assertEqual(raised.exception.reason_code, "late_action_evidence")

    def test_action_knowledge_date_uses_market_timezone(self) -> None:
        with self.assertRaises(CorporateActionNormalizationError) as raised:
            replace(
                evidence(),
                known_at=datetime.fromisoformat("2020-05-27T16:30:00+00:00"),
            )

        self.assertEqual(raised.exception.reason_code, "late_action_evidence")


if __name__ == "__main__":
    unittest.main()
