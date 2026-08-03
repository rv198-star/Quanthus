from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from quanthus.pipelines.securities_warehouse.industry_classification import (
    IndustryClassificationBuildError,
    _effective_start,
    _read_reference,
    _resolve_company,
    _sha256,
    _verify_existing,
)


class IndustryClassificationTests(unittest.TestCase):
    def test_pinned_reference_covers_current_full_snapshot_hierarchy(self) -> None:
        reference, digest = _read_reference(
            Path(__file__).parents[2] / "config/pipelines/csrc-industry-hierarchy.v1.json"
        )

        self.assertEqual(
            reference["mapping_version"], "csrc-listed-company-industry-2012.snapshot-v1"
        )
        self.assertEqual(len(reference["parent_nodes"]), 19)
        self.assertEqual(len(reference["leaf_nodes"]), 83)
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(reference["leaf_nodes"]["C39"], "计算机、通信和其他电子设备制造业")

    def test_resolves_only_the_effective_stock_code_identity(self) -> None:
        identifiers = [
            {
                "identifier_namespace": "CN_STOCK_CODE:SSE",
                "identifier_value": "600001",
                "security_id": "security-old",
                "effective_from": datetime(1990, 1, 1, tzinfo=timezone.utc),
                "effective_to": datetime(2020, 1, 1, tzinfo=timezone.utc),
                "knowledge_from": datetime(2020, 1, 2, tzinfo=timezone.utc),
            },
            {
                "identifier_namespace": "CN_STOCK_CODE:SSE",
                "identifier_value": "600001",
                "security_id": "security-current",
                "effective_from": datetime(2020, 1, 1, tzinfo=timezone.utc),
                "effective_to": None,
                "knowledge_from": datetime(2020, 1, 2, tzinfo=timezone.utc),
            },
        ]
        company, knowledge_from = _resolve_company(
            identifiers,
            {"security-old": "company-1", "security-current": "company-1"},
            stock_code="600001",
            effective_at=_effective_start(datetime(2026, 7, 31, tzinfo=timezone.utc).date()),
        )

        self.assertEqual(company, "company-1")
        self.assertEqual(knowledge_from, datetime(2020, 1, 2, tzinfo=timezone.utc))

    def test_existing_candidate_rejects_a_changed_hierarchy_reference(self) -> None:
        identity = {
            "run_id": "industry-fixture-v1",
            "input_plan_digest": "sha256:" + "a" * 64,
            "model_sha256": "sha256:" + "b" * 64,
            "hierarchy_reference_sha256": "sha256:" + "c" * 64,
            "listed_company_manifest_sha256": "sha256:" + "d" * 64,
            "listed_company_semantic_digest": "sha256:" + "e" * 64,
            "source_sha256": {
                "industry_membership": "sha256:" + "f" * 64,
                "fundamentals_industry_membership_parquet": "sha256:" + "0" * 64,
            },
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "schema_version": 1,
                "kind": "dwd_industry_classification_candidate",
                **identity,
                "semantic_digest": "sha256:" + "1" * 64,
                "owned_tables": [
                    "dwd_industry_scheme",
                    "dwd_industry_node",
                    "dwd_company_industry_membership",
                ],
                "row_counts": {
                    "dwd_industry_scheme": 1,
                    "dwd_industry_node": 2,
                    "dwd_company_industry_membership": 1,
                },
                "files": [],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=True, sort_keys=True), encoding="utf-8"
            )
            (root / "READY").write_text(_sha256(manifest_path) + "\n", encoding="ascii")

            verified = _verify_existing(root, identity)
            self.assertEqual(verified.semantic_digest, manifest["semantic_digest"])

            changed = {**identity, "hierarchy_reference_sha256": "sha256:" + "2" * 64}
            with self.assertRaises(IndustryClassificationBuildError):
                _verify_existing(root, changed)


if __name__ == "__main__":
    unittest.main()
