import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest, PublishedSnapshot
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureDefinition,
    FeatureLifecycle,
    FeatureNullPolicy,
    FeatureOutputType,
    FeatureRegistry,
    FeatureSource,
    build_feature_lineage,
)
from quanthus.label_registry import (
    ExpectedFrequency,
    FeatureFact,
    FeatureRequirement,
    LabelCard,
    LabelCounterexample,
    LabelDecision,
    LabelDimension,
    LabelEvaluationContext,
    LabelEvaluationError,
    LabelEvidence,
    LabelLifecycle,
    LabelParameter,
    LabelRegistry,
    LabelRegistryError,
    LabelResultStatus,
    evaluate_label,
    load_label_registry,
)


def checksum(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def dataset_manifest(cutoff: datetime) -> DatasetManifest:
    return DatasetManifest(
        dataset="bars_daily",
        version="bars-daily-v1",
        source_revisions={"source": "revision-v1"},
        row_count=20,
        partitions=("market_date=2025-01-02",),
        schema_hash=checksum("bars-schema"),
        content_checksums={"part-000.parquet": checksum("bars")},
        created_at=cutoff,
        knowledge_cutoff=cutoff,
    )


def feature_definition(
    *,
    feature_id: str = "bar.close_location",
    lifecycle: FeatureLifecycle = FeatureLifecycle.ACTIVE,
    formula: str = "(close - low) / nullif(high - low, 0)",
    output_type: FeatureOutputType = FeatureOutputType.DECIMAL,
    inputs: tuple[FeatureSource, ...] | None = None,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=feature_id,
        version="1.0.0",
        formula=formula,
        inputs=inputs
        or (
            FeatureSource("bars_daily", "close"),
            FeatureSource("bars_daily", "high"),
            FeatureSource("bars_daily", "low"),
        ),
        unit="ratio",
        null_policy=FeatureNullPolicy.EXPLICIT_UNKNOWN,
        lookback_sessions=20,
        minimum_history_sessions=1,
        cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
        output_type=output_type,
        owner="p3-label-feature-system",
        lifecycle=lifecycle,
    )


def label_card() -> LabelCard:
    definition = feature_definition()
    return LabelCard(
        label_id="stage.close_strength",
        display_name="Close strength",
        dimension=LabelDimension.PRICE_VOLUME_ACCEPTANCE,
        version="1.0.0",
        lifecycle=LabelLifecycle.ACTIVE,
        source_fields=definition.inputs,
        feature_requirements=(FeatureRequirement(definition.feature_id, definition.version),),
        rule="bar.close_location >= threshold",
        parameters=(LabelParameter("threshold", "0.70"),),
        lookback_sessions=20,
        minimum_history_sessions=1,
        mutually_exclusive_with=(),
        coexists_with=(),
        output_values=("accepted", "rejected"),
        evidence_fields=("comparison",),
        reason_codes=("matched", "not_matched", "feature_unavailable"),
        counterexamples=(
            LabelCounterexample(
                "flat_range",
                "Flat range cannot meet the declared threshold.",
                "rejected",
                "fixture:flat-range",
            ),
            LabelCounterexample(
                "weak_close",
                "A close near the low remains rejected.",
                "rejected",
                "fixture:weak-close",
            ),
        ),
        expected_frequency=ExpectedFrequency(Decimal("0.10"), Decimal("0.90"), "synthetic"),
        validation_sample_refs=("fixture:label-contract",),
    )


class CloseStrengthEvaluator:
    @property
    def evaluator_id(self) -> str:
        return "synthetic.close_strength"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, card: LabelCard, context: LabelEvaluationContext) -> LabelDecision:
        fact = context.fact(card.feature_requirements[0])
        if fact.value is None:
            return LabelDecision(
                LabelResultStatus.UNKNOWN,
                None,
                "feature_unavailable",
                (LabelEvidence("comparison", "feature unavailable"),),
            )
        threshold = Decimal(str(card.parameters[0].value))
        matched = isinstance(fact.value, Decimal) and fact.value >= threshold
        return LabelDecision(
            LabelResultStatus.APPLIED,
            "accepted" if matched else "rejected",
            "matched" if matched else "not_matched",
            (LabelEvidence("comparison", f"{fact.value}>={threshold}"),),
        )


class FixedEvaluator:
    def __init__(self, decision: LabelDecision) -> None:
        self.decision = decision

    @property
    def evaluator_id(self) -> str:
        return "synthetic.fixed"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, card: LabelCard, context: LabelEvaluationContext) -> LabelDecision:
        return self.decision


class LabelRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.snapshot = PublishedSnapshot(
            SnapshotRef(
                market_date=date(2025, 1, 2),
                knowledge_cutoff=self.cutoff,
                source_revision="p1-daily-v1",
                snapshot_id="snapshot-2025-01-02-v1",
            ),
            (dataset_manifest(self.cutoff),),
            "p1-universe-v2",
        )
        self.definition = feature_definition()
        self.feature_registry = FeatureRegistry(
            Path("features"),
            "1.0.0",
            "feature-registry-v1",
            (self.definition,),
        )
        self.lineage = build_feature_lineage(
            self.definition,
            self.snapshot,
            history_sessions=20,
        )
        self.fact = FeatureFact(
            "600000",
            self.definition.feature_id,
            self.definition.version,
            self.lineage,
            Decimal("0.80"),
        )
        self.context = LabelEvaluationContext(
            "600000",
            self.snapshot.reference.snapshot_id,
            self.cutoff,
            (self.fact,),
        )

    def test_synthetic_evaluator_is_deterministic_and_evidence_bound(self) -> None:
        first = evaluate_label(
            label_card(), self.feature_registry, self.context, CloseStrengthEvaluator()
        )
        second = evaluate_label(
            label_card(), self.feature_registry, self.context, CloseStrengthEvaluator()
        )

        self.assertEqual(first.status, LabelResultStatus.APPLIED)
        self.assertEqual(first.value, "accepted")
        self.assertEqual(first.card_digest, label_card().digest)
        self.assertEqual(first.feature_evidence[0].lineage_digest, self.lineage.digest)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_unavailable_feature_produces_explicit_unknown(self) -> None:
        unavailable = replace(self.fact, value=None, unavailable_reason="zero_range")
        context = replace(self.context, facts=(unavailable,))

        result = evaluate_label(
            label_card(), self.feature_registry, context, CloseStrengthEvaluator()
        )

        self.assertEqual(result.status, LabelResultStatus.UNKNOWN)
        self.assertIsNone(result.value)
        self.assertEqual(result.reason_code, "feature_unavailable")

    def test_unavailable_feature_cannot_be_converted_to_an_applied_label(self) -> None:
        unavailable = replace(self.fact, value=None, unavailable_reason="zero_range")
        context = replace(self.context, facts=(unavailable,))
        evaluator = FixedEvaluator(
            LabelDecision(
                LabelResultStatus.APPLIED,
                "accepted",
                "matched",
                (LabelEvidence("comparison", "forced"),),
            )
        )

        with self.assertRaises(LabelEvaluationError) as raised:
            evaluate_label(label_card(), self.feature_registry, context, evaluator)

        self.assertEqual(raised.exception.reason_code, "unavailable_feature_applied")

    def test_hidden_extra_feature_input_is_rejected(self) -> None:
        extra_definition = feature_definition(
            feature_id="bar.volume_ratio",
            inputs=(FeatureSource("bars_daily", "volume"),),
        )
        registry = replace(
            self.feature_registry,
            definitions=(self.definition, extra_definition),
        )
        extra_lineage = build_feature_lineage(
            extra_definition,
            self.snapshot,
            history_sessions=20,
        )
        extra_fact = FeatureFact(
            "600000",
            extra_definition.feature_id,
            extra_definition.version,
            extra_lineage,
            Decimal("1.20"),
        )
        context = replace(self.context, facts=(self.fact, extra_fact))

        with self.assertRaises(LabelEvaluationError) as raised:
            evaluate_label(label_card(), registry, context, CloseStrengthEvaluator())

        self.assertEqual(raised.exception.reason_code, "feature_input_contract_mismatch")

    def test_feature_definition_digest_cannot_drift_under_a_card(self) -> None:
        changed = replace(self.definition, formula="close / open")
        registry = replace(self.feature_registry, definitions=(changed,))

        with self.assertRaises(LabelEvaluationError) as raised:
            evaluate_label(label_card(), registry, self.context, CloseStrengthEvaluator())

        self.assertEqual(raised.exception.reason_code, "feature_definition_mismatch")

    def test_active_label_cannot_depend_on_a_shadow_feature(self) -> None:
        shadow = replace(self.definition, lifecycle=FeatureLifecycle.SHADOW)
        registry = replace(self.feature_registry, definitions=(shadow,))
        shadow_lineage = build_feature_lineage(shadow, self.snapshot, history_sessions=20)
        context = replace(self.context, facts=(replace(self.fact, lineage=shadow_lineage),))

        with self.assertRaises(LabelEvaluationError) as raised:
            evaluate_label(label_card(), registry, context, CloseStrengthEvaluator())

        self.assertEqual(raised.exception.reason_code, "inactive_feature_requirement")

    def test_card_requires_two_counterexamples_and_consistent_relationships(self) -> None:
        card = label_card()
        with self.assertRaisesRegex(LabelRegistryError, "at least two counterexamples"):
            replace(card, counterexamples=card.counterexamples[:1])
        with self.assertRaisesRegex(LabelRegistryError, "both exclude and coexist"):
            replace(
                card,
                mutually_exclusive_with=("stage.other",),
                coexists_with=("stage.other",),
            )

    def test_registry_rejects_two_active_versions_of_one_label(self) -> None:
        with self.assertRaisesRegex(LabelRegistryError, "at most one active"):
            LabelRegistry(
                Path("labels"),
                "1.0.0",
                "label-registry-v1",
                (label_card(), replace(label_card(), version="2.0.0")),
            )

    def test_evaluator_cannot_return_undeclared_value_reason_or_evidence(self) -> None:
        cases = (
            (
                LabelDecision(
                    LabelResultStatus.APPLIED,
                    "undeclared",
                    "matched",
                    (LabelEvidence("comparison", "x"),),
                ),
                "undeclared_label_value",
            ),
            (
                LabelDecision(
                    LabelResultStatus.APPLIED,
                    "accepted",
                    "undeclared_reason",
                    (LabelEvidence("comparison", "x"),),
                ),
                "undeclared_reason_code",
            ),
            (
                LabelDecision(
                    LabelResultStatus.APPLIED,
                    "accepted",
                    "matched",
                    (LabelEvidence("hidden", "x"),),
                ),
                "evidence_contract_mismatch",
            ),
        )

        for decision, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                with self.assertRaises(LabelEvaluationError) as raised:
                    evaluate_label(
                        label_card(),
                        self.feature_registry,
                        self.context,
                        FixedEvaluator(decision),
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_feature_value_type_must_match_registered_output_type(self) -> None:
        wrong_type = replace(self.fact, value="0.80")
        context = replace(self.context, facts=(wrong_type,))

        with self.assertRaises(LabelEvaluationError) as raised:
            evaluate_label(label_card(), self.feature_registry, context, CloseStrengthEvaluator())

        self.assertEqual(raised.exception.reason_code, "feature_type_mismatch")

    def test_label_registry_loader_is_strict_and_deterministic(self) -> None:
        payload = {
            "schema_version": "1.0.0",
            "registry_version": "label-registry-v1",
            "cards": [label_card().to_dict()],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_label_registry(path)

            payload["implicit_default"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(LabelRegistryError, "unexpected"):
                load_label_registry(path)

        self.assertEqual(loaded.get("stage.close_strength", "1.0.0"), label_card())
        self.assertEqual(loaded.active("stage.close_strength"), label_card())
        self.assertTrue(loaded.digest.startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
