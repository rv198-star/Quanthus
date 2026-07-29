import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest, PublishedSnapshot
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureDefinition,
    FeatureLifecycle,
    FeatureLineageError,
    FeatureNullPolicy,
    FeatureOutputType,
    FeatureRegistry,
    FeatureRegistryError,
    FeatureSource,
    build_feature_lineage,
    load_feature_registry,
)


def checksum(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def manifest(dataset: str, cutoff: datetime) -> DatasetManifest:
    return DatasetManifest(
        dataset=dataset,
        version=f"{dataset}-v1",
        source_revisions={"source": "revision-v1"},
        row_count=20,
        partitions=("market_date=2025-01-02",),
        schema_hash=checksum(f"{dataset}-schema"),
        content_checksums={"part-000.parquet": checksum(dataset)},
        created_at=cutoff,
        knowledge_cutoff=cutoff,
    )


def definition(
    *,
    feature_id: str = "bar.close_location",
    version: str = "1.0.0",
    lifecycle: FeatureLifecycle = FeatureLifecycle.ACTIVE,
    cutoff_rule: FeatureCutoffRule = FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
    inputs: tuple[FeatureSource, ...] | None = None,
    minimum_history_sessions: int = 1,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=feature_id,
        version=version,
        formula="(close - low) / nullif(high - low, 0)",
        inputs=inputs
        or (
            FeatureSource("bars_daily", "close"),
            FeatureSource("bars_daily", "high"),
            FeatureSource("bars_daily", "low"),
        ),
        unit="ratio",
        null_policy=FeatureNullPolicy.EXPLICIT_UNKNOWN,
        lookback_sessions=20,
        minimum_history_sessions=minimum_history_sessions,
        cutoff_rule=cutoff_rule,
        output_type=FeatureOutputType.DECIMAL,
        owner="p3-label-feature-system",
        lifecycle=lifecycle,
    )


def registry_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "registry_version": "p3-feature-registry-test-v1",
        "definitions": [definition().to_dict()],
    }


class FeatureRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.snapshot = PublishedSnapshot(
            reference=SnapshotRef(
                market_date=date(2025, 1, 2),
                knowledge_cutoff=self.cutoff,
                source_revision="p1-daily-v1",
                snapshot_id="snapshot-2025-01-02-v1",
            ),
            datasets=(
                manifest("bars_daily", self.cutoff),
                manifest("tradability_daily", self.cutoff),
            ),
            universe_policy_version="p1-universe-v2",
        )

    def test_strict_registry_loads_and_resolves_the_active_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "features.json"
            path.write_text(json.dumps(registry_payload()), encoding="utf-8")

            registry = load_feature_registry(path)

        self.assertEqual(registry.active("bar.close_location"), definition())
        self.assertEqual(registry.get("bar.close_location", "1.0.0"), definition())
        self.assertTrue(registry.digest.startswith("sha256:"))

    def test_registry_serialization_is_independent_of_definition_order(self) -> None:
        active = definition()
        shadow = definition(
            feature_id="market.turnover_percentile",
            lifecycle=FeatureLifecycle.SHADOW,
            cutoff_rule=FeatureCutoffRule.COMPLETE_T_CLOSE_CROSS_SECTION,
            inputs=(FeatureSource("bars_daily", "turnover"),),
            minimum_history_sessions=20,
        )
        first = FeatureRegistry(Path("first"), "1.0.0", "registry-v1", (active, shadow))
        second = FeatureRegistry(Path("second"), "1.0.0", "registry-v1", (shadow, active))

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_same_feature_version_cannot_hide_a_definition_change(self) -> None:
        original = definition()
        changed = replace(original, formula="close / open")

        with self.assertRaisesRegex(FeatureRegistryError, "duplicate feature/version"):
            FeatureRegistry(Path("registry"), "1.0.0", "registry-v1", (original, changed))

    def test_only_one_version_of_a_feature_may_be_active(self) -> None:
        with self.assertRaisesRegex(FeatureRegistryError, "at most one active"):
            FeatureRegistry(
                Path("registry"),
                "1.0.0",
                "registry-v1",
                (definition(), definition(version="2.0.0")),
            )

    def test_lineage_binds_definition_snapshot_and_manifest_evidence(self) -> None:
        lineage = build_feature_lineage(definition(), self.snapshot, history_sessions=20)
        repeated = build_feature_lineage(definition(), self.snapshot, history_sessions=20)

        self.assertEqual(lineage.snapshot_id, self.snapshot.reference.snapshot_id)
        self.assertEqual(lineage.source_revision, self.snapshot.reference.source_revision)
        self.assertEqual(lineage.universe_policy_version, "p1-universe-v2")
        self.assertEqual({source.dataset for source in lineage.sources}, {"bars_daily"})
        self.assertTrue(all(source.knowledge_cutoff <= self.cutoff for source in lineage.sources))
        self.assertEqual(lineage.digest, repeated.digest)

    def test_lineage_rejects_source_evidence_after_the_snapshot_cutoff(self) -> None:
        lineage = build_feature_lineage(definition(), self.snapshot, history_sessions=20)
        future_source = replace(
            lineage.sources[0], knowledge_cutoff=self.cutoff + timedelta(seconds=1)
        )

        with self.assertRaises(FeatureLineageError) as raised:
            replace(lineage, sources=(future_source, *lineage.sources[1:]))

        self.assertEqual(raised.exception.reason_code, "future_source_evidence")

    def test_insufficient_history_is_an_explicit_failure(self) -> None:
        with self.assertRaises(FeatureLineageError) as raised:
            build_feature_lineage(
                definition(minimum_history_sessions=20),
                self.snapshot,
                history_sessions=19,
            )

        self.assertEqual(raised.exception.reason_code, "insufficient_history")

    def test_cross_sectional_feature_requires_complete_t_close_evidence(self) -> None:
        cross_sectional = definition(
            feature_id="market.turnover_percentile",
            cutoff_rule=FeatureCutoffRule.COMPLETE_T_CLOSE_CROSS_SECTION,
            inputs=(FeatureSource("bars_daily", "turnover"),),
            minimum_history_sessions=20,
        )

        with self.assertRaises(FeatureLineageError) as raised:
            build_feature_lineage(
                cross_sectional,
                self.snapshot,
                history_sessions=20,
                cross_section_complete=False,
            )

        self.assertEqual(raised.exception.reason_code, "incomplete_cross_section")
        accepted = build_feature_lineage(
            cross_sectional,
            self.snapshot,
            history_sessions=20,
            cross_section_complete=True,
        )
        self.assertTrue(accepted.cross_section_complete)

    def test_missing_source_dataset_fails_closed(self) -> None:
        missing_source = definition(inputs=(FeatureSource("sector_membership_history", "sector"),))

        with self.assertRaises(FeatureLineageError) as raised:
            build_feature_lineage(missing_source, self.snapshot, history_sessions=20)

        self.assertEqual(raised.exception.reason_code, "missing_source_dataset")

    def test_loader_rejects_unknown_fields_instead_of_ignoring_them(self) -> None:
        payload = registry_payload()
        payload["implicit_default"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "features.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(FeatureRegistryError, "unexpected"):
                load_feature_registry(path)


if __name__ == "__main__":
    unittest.main()
