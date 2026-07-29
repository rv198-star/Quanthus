import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.feature_registry import FeatureSource
from quanthus.label_audit import LabelAuditError, LabelConflictReason, audit_label_results
from quanthus.label_registry import (
    ExpectedFrequency,
    FeatureEvidenceRef,
    FeatureRequirement,
    LabelCard,
    LabelCounterexample,
    LabelDimension,
    LabelEvidence,
    LabelLifecycle,
    LabelRegistry,
    LabelResult,
    LabelResultStatus,
)

BASE_TIME = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)


def digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def card(
    label_id: str,
    *,
    dimension: LabelDimension = LabelDimension.PRICE_VOLUME_ACCEPTANCE,
    lifecycle: LabelLifecycle = LabelLifecycle.ACTIVE,
    minimum_rate: str = "0.00",
    maximum_rate: str = "1.00",
    mutually_exclusive_with: tuple[str, ...] = (),
) -> LabelCard:
    return LabelCard(
        label_id=label_id,
        display_name=label_id,
        dimension=dimension,
        version="1.0.0",
        lifecycle=lifecycle,
        source_fields=(FeatureSource("bars_daily", "close"),),
        feature_requirements=(FeatureRequirement("feature.close", "1.0.0"),),
        rule="synthetic audit rule",
        parameters=(),
        lookback_sessions=1,
        minimum_history_sessions=1,
        mutually_exclusive_with=mutually_exclusive_with,
        coexists_with=(),
        output_values=("present", "absent"),
        evidence_fields=("comparison",),
        reason_codes=("matched", "unknown", "not_applicable"),
        counterexamples=(
            LabelCounterexample("case_one", "first", "absent", "fixture:one"),
            LabelCounterexample("case_two", "second", "absent", "fixture:two"),
        ),
        expected_frequency=ExpectedFrequency(
            Decimal(minimum_rate), Decimal(maximum_rate), "synthetic-audit"
        ),
        validation_sample_refs=("fixture:label-audit",),
    )


def registry(*cards: LabelCard) -> LabelRegistry:
    return LabelRegistry(Path("labels"), "1.0.0", "label-audit-registry-v1", tuple(cards))


def result(
    definition: LabelCard,
    sequence: int,
    *,
    status: LabelResultStatus = LabelResultStatus.APPLIED,
    value: str | None = "present",
) -> LabelResult:
    if status is not LabelResultStatus.APPLIED:
        value = None
    reason = {
        LabelResultStatus.APPLIED: "matched",
        LabelResultStatus.UNKNOWN: "unknown",
        LabelResultStatus.NOT_APPLICABLE: "not_applicable",
    }[status]
    return LabelResult(
        label_id=definition.label_id,
        label_version=definition.version,
        card_digest=definition.digest,
        dimension=definition.dimension,
        security_code=f"6000{sequence:02d}",
        snapshot_id=f"snapshot-{sequence}",
        knowledge_cutoff=BASE_TIME + timedelta(days=sequence),
        status=status,
        value=value,
        reason_code=reason,
        evidence=(LabelEvidence("comparison", f"synthetic-{sequence}"),),
        feature_evidence=(FeatureEvidenceRef("feature.close", "1.0.0", digest("lineage")),),
        evaluator_id="synthetic.audit",
        evaluator_version="1.0.0",
    )


class LabelAuditTests(unittest.TestCase):
    def test_frequency_status_and_value_counts_reconcile(self) -> None:
        definition = card("stage.alpha", minimum_rate="0.40", maximum_rate="0.60")
        results = (
            result(definition, 1, value="present"),
            result(definition, 2, value="absent"),
            result(definition, 3, status=LabelResultStatus.UNKNOWN),
            result(definition, 4, status=LabelResultStatus.NOT_APPLICABLE),
        )

        report = audit_label_results(registry(definition), results, sample_scope="synthetic-audit")

        frequency = report.frequencies[0]
        self.assertEqual(frequency.sample_count, 4)
        self.assertEqual(frequency.applied_count, 2)
        self.assertEqual(frequency.unknown_count, 1)
        self.assertEqual(frequency.not_applicable_count, 1)
        self.assertEqual(frequency.applied_rate, Decimal("0.500000000000"))
        self.assertEqual(
            [(item.value, item.count) for item in frequency.value_counts],
            [("absent", 1), ("present", 1)],
        )
        self.assertTrue(report.passed)

    def test_active_frequency_failure_blocks_but_shadow_failure_only_warns(self) -> None:
        active = card("stage.active", minimum_rate="0.50")
        shadow = card(
            "stage.shadow",
            lifecycle=LabelLifecycle.SHADOW,
            minimum_rate="0.50",
        )
        results = (
            result(active, 1, status=LabelResultStatus.UNKNOWN),
            result(shadow, 2, status=LabelResultStatus.UNKNOWN),
        )

        report = audit_label_results(
            registry(active, shadow), results, sample_scope="synthetic-audit"
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.blocking_issue_count, 1)
        self.assertEqual(report.shadow_warning_count, 1)

    def test_declared_active_mutual_exclusion_blocks_publication(self) -> None:
        alpha = card("stage.alpha", mutually_exclusive_with=("stage.beta",))
        beta = card("stage.beta")
        left = result(alpha, 1)
        right = replace(
            result(beta, 1),
            security_code=left.security_code,
            snapshot_id=left.snapshot_id,
            knowledge_cutoff=left.knowledge_cutoff,
        )

        report = audit_label_results(
            registry(alpha, beta), (left, right), sample_scope="synthetic-audit"
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.conflicts[0].reason, LabelConflictReason.MUTUALLY_EXCLUSIVE)
        self.assertTrue(report.conflicts[0].blocks_publication)

    def test_shadow_conflict_is_visible_without_blocking_active_slice(self) -> None:
        active = card("stage.active", mutually_exclusive_with=("stage.shadow",))
        shadow = card("stage.shadow", lifecycle=LabelLifecycle.SHADOW)
        left = result(active, 1)
        right = replace(
            result(shadow, 1),
            security_code=left.security_code,
            snapshot_id=left.snapshot_id,
            knowledge_cutoff=left.knowledge_cutoff,
        )

        report = audit_label_results(
            registry(active, shadow), (left, right), sample_scope="synthetic-audit"
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.blocking_issue_count, 0)
        self.assertEqual(report.shadow_warning_count, 1)

    def test_two_applied_primary_stage_cards_conflict_without_extra_declaration(self) -> None:
        alpha = card("stage.alpha", dimension=LabelDimension.PRIMARY_STAGE)
        beta = card("stage.beta", dimension=LabelDimension.PRIMARY_STAGE)
        left = result(alpha, 1)
        right = replace(
            result(beta, 1),
            security_code=left.security_code,
            snapshot_id=left.snapshot_id,
            knowledge_cutoff=left.knowledge_cutoff,
        )

        report = audit_label_results(
            registry(alpha, beta), (left, right), sample_scope="synthetic-audit"
        )

        self.assertEqual(report.conflicts[0].reason, LabelConflictReason.PRIMARY_STAGE_MULTIPLE)
        self.assertFalse(report.passed)

    def test_result_must_match_registered_card_contract(self) -> None:
        definition = card("stage.alpha")
        base = result(definition, 1)
        cases = (
            (replace(base, card_digest=digest("wrong")), "card_digest_mismatch"),
            (
                replace(base, dimension=LabelDimension.TREND_ENERGY),
                "dimension_mismatch",
            ),
            (replace(base, value="undeclared"), "undeclared_label_value"),
            (replace(base, reason_code="undeclared"), "undeclared_reason_code"),
        )

        for changed, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                with self.assertRaises(LabelAuditError) as raised:
                    audit_label_results(
                        registry(definition),
                        (changed,),
                        sample_scope="synthetic-audit",
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_duplicate_result_and_snapshot_cutoff_drift_fail_closed(self) -> None:
        definition = card("stage.alpha")
        base = result(definition, 1)
        with self.assertRaises(LabelAuditError) as duplicate:
            audit_label_results(registry(definition), (base, base), sample_scope="synthetic-audit")
        self.assertEqual(duplicate.exception.reason_code, "duplicate_label_result")

        second = replace(
            result(definition, 2),
            security_code=base.security_code,
            snapshot_id=base.snapshot_id,
        )
        with self.assertRaises(LabelAuditError) as cutoff:
            audit_label_results(
                registry(definition), (base, second), sample_scope="synthetic-audit"
            )
        self.assertEqual(cutoff.exception.reason_code, "context_cutoff_mismatch")

    def test_unknown_relationship_target_and_wrong_scope_fail_closed(self) -> None:
        unknown_target = card("stage.alpha", mutually_exclusive_with=("stage.not_registered",))
        with self.assertRaises(LabelAuditError) as relationship:
            audit_label_results(
                registry(unknown_target),
                (result(unknown_target, 1),),
                sample_scope="synthetic-audit",
            )
        self.assertEqual(relationship.exception.reason_code, "unknown_relationship_target")

        definition = card("stage.beta")
        with self.assertRaises(LabelAuditError) as scope:
            audit_label_results(
                registry(definition), (result(definition, 1),), sample_scope="wrong-scope"
            )
        self.assertEqual(scope.exception.reason_code, "sample_scope_mismatch")

    def test_result_input_order_cannot_change_report_or_digest(self) -> None:
        alpha = card("stage.alpha")
        beta = card("stage.beta")
        results = (result(beta, 2), result(alpha, 1), result(alpha, 3, value="absent"))

        first = audit_label_results(registry(alpha, beta), results, sample_scope="synthetic-audit")
        second = audit_label_results(
            registry(alpha, beta), tuple(reversed(results)), sample_scope="synthetic-audit"
        )

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_empty_audit_fails_closed(self) -> None:
        definition = card("stage.alpha")

        with self.assertRaises(LabelAuditError) as raised:
            audit_label_results(registry(definition), (), sample_scope="synthetic-audit")

        self.assertEqual(raised.exception.reason_code, "missing_label_results")


if __name__ == "__main__":
    unittest.main()
