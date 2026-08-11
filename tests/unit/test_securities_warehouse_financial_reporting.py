from __future__ import annotations

import gzip
import hashlib
import inspect
import itertools
import json
import os
import shutil
import sqlite3
import unittest
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.dwd_semantic_contract_audit import (
    _foreign_key_metrics,
    _interval_metrics,
    load_semantic_contracts,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    _ACCOUNTING_FIELDS,
    _FINANCIAL_BUILDER_CONTRACT,
    _FINANCIAL_CANDIDATE_CONTROL_FILES,
    _FINANCIAL_CONCEPT_EVIDENCE,
    _FINANCIAL_CONCEPT_EVIDENCE_STORAGE,
    _FINANCIAL_CONCEPT_SEMANTICS_POLICY,
    _FINANCIAL_PARTITION_BY,
    _FINANCIAL_RESOURCE_POLICY,
    _FINANCIAL_SEMANTIC_VERIFICATION_CACHE,
    _FINANCIAL_SOURCE_ORDER,
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
    FinancialReportingBuildError,
    FinancialReportingSource,
    FinancialReportingSourceInventory,
    FinancialStatementSource,
    _audit_financial_candidate,
    _batch_rows,
    _build_financial_reporting_locked,
    _checkpoint_batch_digest,
    _checkpoint_batch_files_valid,
    _concept_revision_evidence_metrics,
    _digest_value,
    _discard_uncheckpointed_batch_files,
    _finalize_concepts,
    _financial_concept_evidence_paths,
    _load_or_initialize_checkpoint,
    _merge_concept_row,
    _plan_date,
    _reporting_batch_rows,
    _retire_concept_staging,
    _reverify_financial_build_inputs,
    _verify_financial_candidate_semantics_once,
    _verify_financial_checkpoint_evidence_binding,
    _verify_financial_concept_evidence,
    _verify_financial_manifest_inventory,
    _verify_financial_source_audit,
    _write_batch,
    audit_financial_native_sources,
    audit_financial_statement_sources,
    classify_statement_rows,
    combine_financial_reporting_source_audits,
    conservative_provider_timestamp,
    decode_financial_envelope,
    expand_financial_envelope,
    expand_financial_native_row,
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
    iter_statement_reports,
    select_financial_reporting_sources,
    select_financial_statement_sources,
    verify_financial_reporting_candidate,
    write_financial_reporting_source_audit,
)

ROOT = Path(__file__).resolve().parents[2]


def _concept_source_record_id(label: str, *, period_end: str, acquired_at: datetime) -> str:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = acquired_at.astimezone(timezone.utc) - epoch
    acquired_at_us = ((delta.days * 86_400) + delta.seconds) * 1_000_000 + delta.microseconds
    return f"fixture#{label}&period_end={period_end}&source_acquired_at_us={acquired_at_us}"


def _envelope(payload: dict[str, object]) -> dict[str, object]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = gzip.compress(raw, mtime=0)
    return {
        "raw_payload_gzip": repr(packed).encode("ascii"),
        "raw_payload_bytes": len(raw),
        "raw_payload_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }


def _financial_source_audit_fixture(*, include_second_statement: bool = False) -> dict[str, Any]:
    source_sha256 = {
        source: "sha256:" + f"{index + 1:x}" * 64
        for index, source in enumerate(_FINANCIAL_SOURCE_ORDER)
    }
    zero_accounting = {field: 0 for field in _ACCOUNTING_FIELDS}
    selected_accounting = {**zero_accounting, "envelope_rows": 1}
    second_input_accounting = (
        {**zero_accounting, "envelope_rows": 1, "report_periods": 1}
        if include_second_statement
        else zero_accounting
    )
    all_source_accounting = {
        field: selected_accounting[field] + second_input_accounting[field]
        for field in _ACCOUNTING_FIELDS
    }
    statement_quality_exceptions = (
        {
            "AFTER_THROUGH_DATE": 1,
            "BEFORE_FIRST": 2,
        }
        if include_second_statement
        else {"BEFORE_FIRST": 2}
    )
    statement = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_source_audit",
        "decoder_contract": "byte_literal_gzip_sha256_json_v1",
        "source_order": [
            "financial_statements",
            "fundamentals_financial_statements_parquet",
        ],
        "business_key": ["stock_code", "statement_type"],
        "source_rows": {
            "financial_statements": {
                "path": "payload/full/financial_statements.parquet",
                "source_sha256": source_sha256[_FINANCIAL_SOURCE_ORDER[0]],
                "source_revision": "fixture-r1",
                "precedence": "parent",
                "input_accounting": selected_accounting,
                "selected_accounting": selected_accounting,
                "input_business_key_count": 1,
                "selected_business_key_count": 1,
                "unique_provider_concept_count": 0,
                "quality_exceptions": {"BEFORE_FIRST": 2},
            },
            "fundamentals_financial_statements_parquet": {
                "path": "payload/hotfix/financial_statements.parquet",
                "source_sha256": source_sha256[_FINANCIAL_SOURCE_ORDER[1]],
                "source_revision": "fixture-r1",
                "precedence": "hotfix",
                "input_accounting": second_input_accounting,
                "selected_accounting": zero_accounting,
                "input_business_key_count": int(include_second_statement),
                "selected_business_key_count": int(include_second_statement),
                "unique_provider_concept_count": 0,
                "quality_exceptions": (
                    {"AFTER_THROUGH_DATE": 1} if include_second_statement else {}
                ),
            },
        },
        "overlay": {
            "policy": "parent_base_wins_on_overlapping_business_keys_hotfix_fills_absent_keys_only",
            "overlap_mismatch_policy": "fail_build",
            "parent_overlap_rows": 0,
            "hotfix_fill_rows": int(include_second_statement),
            "selected_envelope_rows": 1 + int(include_second_statement),
        },
        "all_source_accounting": all_source_accounting,
        "selected_source_accounting": selected_accounting,
        "candidate_size_calibration": {
            "dwd_financial_report_rows": 0,
            "dwd_financial_fact_upper_bound": 0,
            "provider_native_concept_coordinate_count": 0,
            "selected_uncompressed_bytes": 0,
        },
        "quality_exceptions": statement_quality_exceptions,
    }
    bindings = {
        "run_id": "run-1",
        "input_plan_digest": "plan-1",
        "model_sha256": "sha256:" + "a" * 64,
        "listed_company_manifest_sha256": "sha256:" + "b" * 64,
        "listed_company_semantic_digest": "sha256:" + "c" * 64,
        "source_sha256": source_sha256,
    }
    native_rows = {
        source: {
            "source_kind": (
                "financial_abstract"
                if source == "announcement_archive_fin_abstract"
                else "quarterly_projection"
            ),
            "path": f"payload/{source}",
            "member": None,
            "source_sha256": source_sha256[source],
            "input_rows": 0,
            "selected_rows": 0,
            "output_reports": 0,
            "output_facts": 0,
            "quality_exceptions": {},
        }
        for source in _FINANCIAL_SOURCE_ORDER[2:]
    }
    native = {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_native_source_audit",
        **bindings,
        "source_order": list(_FINANCIAL_SOURCE_ORDER[2:]),
        "source_rows": native_rows,
        "quarterly_overlay": {
            "policy": "weekly_parent_then_hotfix_fill_only",
            "parent_overlap_rows": 0,
            "hotfix_fill_rows": 0,
        },
        "empty_modeled_sources": [],
        "candidate_size_calibration": {
            "dwd_financial_report_rows": 0,
            "dwd_financial_fact_rows": 0,
            "dwd_financial_concept_count": 0,
            "quality_exception_rows": 0,
        },
        "quality_exceptions": {},
    }

    def canonical_sha(value: object) -> str:
        payload = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
            "ascii"
        )
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    return {
        "schema_version": 1,
        "kind": "dwd_financial_reporting_combined_source_audit",
        **bindings,
        "source_order": list(_FINANCIAL_SOURCE_ORDER),
        "component_audits": {
            "statement": {
                "path": "/fixture/statement.json",
                "sha256": canonical_sha(statement),
                "audit": statement,
            },
            "native": {
                "path": "/fixture/native.json",
                "sha256": canonical_sha(native),
                "audit": native,
            },
        },
        "candidate_size_calibration": {
            "dwd_financial_report_rows": 0,
            "dwd_financial_fact_rows": 0,
            "statement_report_rows": 0,
            "statement_fact_rows": 0,
            "native_report_rows": 0,
            "native_fact_rows": 0,
        },
        "quality_exceptions": statement_quality_exceptions,
    }


class FinancialEnvelopeTests(unittest.TestCase):
    def test_finalizer_never_materializes_the_full_concept_evidence_table(self) -> None:
        source = inspect.getsource(_finalize_concepts)

        self.assertNotIn("evidence_table", source)
        self.assertEqual(source.count(".to_arrow_table()"), 1)
        self.assertIn("_retire_concept_staging(work)", source)

    def test_final_input_reverification_occurs_after_deep_verify_before_rename(self) -> None:
        source = inspect.getsource(_build_financial_reporting_locked)
        deep_verify = source.rfind("_verify_existing_financial_candidate(work")
        final_input_verify = source.rfind("_reverify_financial_build_inputs(")
        rename = source.rfind("os.replace(work, output)")
        manifest_ready = source.rfind('_write_text_atomic(work / "READY"')

        self.assertGreater(deep_verify, 0)
        self.assertLess(manifest_ready, deep_verify)
        self.assertNotIn(
            "_FINANCIAL_SEMANTIC_VERIFICATION_CACHE.add",
            source[manifest_ready:deep_verify],
        )
        self.assertLess(deep_verify, final_input_verify)
        self.assertLess(final_input_verify, rename)

    def test_concept_retirement_persists_parent_before_directory_rename(self) -> None:
        from quanthus.pipelines.securities_warehouse import financial_reporting

        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            staging = work / "staging/concepts"
            staging.mkdir(parents=True)
            (staging / "part-00-000000.parquet").write_bytes(b"fixture")
            events: list[tuple[str, Path, Path | None]] = []
            original_fsync = financial_reporting._fsync_directory
            original_replace = financial_reporting.os.replace

            def recorded_fsync(path: Path) -> None:
                events.append(("fsync", path, None))
                original_fsync(path)

            def recorded_replace(source: Path, target: Path) -> None:
                events.append(("replace", source, target))
                original_replace(source, target)

            with (
                patch.object(
                    financial_reporting,
                    "_fsync_directory",
                    side_effect=recorded_fsync,
                ),
                patch.object(
                    financial_reporting.os,
                    "replace",
                    side_effect=recorded_replace,
                ),
            ):
                paths = _retire_concept_staging(work)

            parent_fsync = events.index(("fsync", work, None))
            rename = next(
                index
                for index, event in enumerate(events)
                if event[0] == "replace" and event[1] == staging
            )
            self.assertLess(parent_fsync, rename)
            self.assertEqual([path.name for path in paths], ["part-00-000000.parquet"])

    def test_manifest_inventory_reads_physical_schema_and_row_counts(self) -> None:
        schemas = financial_reporting_schemas()

        def fixture_value(field: pa.Field, coordinate_year: int) -> object:
            data_type = field.type
            if field.name == "period_end_year":
                return coordinate_year
            if field.name == "period_end":
                return datetime(coordinate_year, 1, 1).date()
            if pa.types.is_string(data_type):
                return "fixture"
            if pa.types.is_timestamp(data_type):
                return datetime(2026, 1, 1, tzinfo=timezone.utc)
            if pa.types.is_date32(data_type):
                return datetime(2026, 1, 1).date()
            if pa.types.is_boolean(data_type):
                return False
            if pa.types.is_integer(data_type):
                return 1
            if pa.types.is_decimal(data_type):
                return Decimal("1")
            self.fail(f"unsupported financial fixture type: {data_type}")

        def fixture_table(
            schema: pa.Schema, row_count: int = 1, coordinate_year: int = 2026
        ) -> pa.Table:
            return pa.Table.from_arrays(
                [
                    pa.array(
                        [fixture_value(field, coordinate_year)] * row_count,
                        type=field.type,
                    )
                    for field in schema
                ],
                schema=schema,
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "dwd_financial_report": (
                    root / "payload/financial_reporting/dwd_financial_report/"
                    "period_end_year=2026/part-00-000000.parquet"
                ),
                "dwd_financial_concept": (
                    root / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
                ),
                "dwd_financial_fact": (
                    root / "payload/financial_reporting/dwd_financial_fact/"
                    "period_end_year=2026/part-00-000000.parquet"
                ),
                "evidence": root / _FINANCIAL_CONCEPT_EVIDENCE / "part-00-000000.parquet",
            }
            for table, path in paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                schema = schemas["dwd_financial_concept" if table == "evidence" else table]
                pq.write_table(fixture_table(schema), path)
            control_paths = [root / relative for relative in _FINANCIAL_CANDIDATE_CONTROL_FILES]
            for path in control_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="ascii")

            def file_record(path: Path) -> dict[str, object]:
                return {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size_bytes": path.stat().st_size,
                }

            manifest = {
                "partition_by": _FINANCIAL_PARTITION_BY,
                "row_counts": {
                    "dwd_financial_report": 1,
                    "dwd_financial_concept": 1,
                    "dwd_financial_fact": 1,
                },
                "files": [file_record(path) for path in sorted([*paths.values(), *control_paths])],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (root / "READY").write_text("fixture", encoding="ascii")

            _manifest_files, rows, _table_digests, physical_rows = (
                _verify_financial_manifest_inventory(root, manifest)
            )
            self.assertEqual(physical_rows, manifest["row_counts"])
            self.assertEqual(rows[paths["dwd_financial_fact"].relative_to(root).as_posix()], 1)

            report_schema = schemas["dwd_financial_report"]
            pq.write_table(
                fixture_table(report_schema, coordinate_year=2025),
                paths["dwd_financial_report"],
                write_statistics=True,
            )
            manifest["files"] = [
                file_record(path) for path in sorted([*paths.values(), *control_paths])
            ]
            with self.assertRaisesRegex(
                FinancialReportingBuildError, "differs from Parquet coordinates"
            ):
                _verify_financial_manifest_inventory(root, manifest)
            pq.write_table(
                fixture_table(report_schema),
                paths["dwd_financial_report"],
                write_statistics=True,
            )
            manifest["files"] = [
                file_record(path) for path in sorted([*paths.values(), *control_paths])
            ]

            empty_directory = root / "audits/empty-other"
            empty_directory.mkdir()
            with self.assertRaisesRegex(
                FinancialReportingBuildError, "directory inventory is inconsistent"
            ):
                _verify_financial_manifest_inventory(root, manifest)
            empty_directory.rmdir()

            rogue_control = root / "audits/rogue-other.bin"
            rogue_control.write_bytes(b"rogue")
            manifest["files"] = [
                *manifest["files"],
                file_record(rogue_control),
            ]
            with self.assertRaisesRegex(FinancialReportingBuildError, "control inventory"):
                _verify_financial_manifest_inventory(root, manifest)
            manifest["files"] = manifest["files"][:-1]
            rogue_control.unlink()

            manifest["partition_by"] = {}
            with self.assertRaisesRegex(FinancialReportingBuildError, "manifest files are invalid"):
                _verify_financial_manifest_inventory(root, manifest)
            manifest["partition_by"] = _FINANCIAL_PARTITION_BY

            pq.write_table(pa.table({"wrong": [1]}), paths["dwd_financial_fact"])
            manifest["files"] = [
                file_record(path) for path in sorted([*paths.values(), *control_paths])
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(FinancialReportingBuildError, "schema is invalid"):
                _verify_financial_manifest_inventory(root, manifest)

            fact_schema = schemas["dwd_financial_fact"]
            pq.write_table(fixture_table(fact_schema, 2), paths["dwd_financial_fact"])
            manifest["files"] = [
                file_record(path) for path in sorted([*paths.values(), *control_paths])
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(FinancialReportingBuildError, "row counts differ"):
                _verify_financial_manifest_inventory(root, manifest)

    def test_source_audit_requires_complete_nested_contract(self) -> None:
        audit = _financial_source_audit_fixture()

        def write_audit(path: Path, value: object) -> None:
            path.write_text(json.dumps(value, ensure_ascii=True), encoding="ascii")

        def component_sha(value: object) -> str:
            payload = (
                json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
            ).encode("ascii")
            return "sha256:" + hashlib.sha256(payload).hexdigest()

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "source-audit.json"
            write_audit(path, audit)
            _verify_financial_source_audit(path)

            statement_component = audit["component_audits"]["statement"]
            del statement_component["audit"]["quality_exceptions"]
            statement_component["sha256"] = component_sha(statement_component["audit"])
            write_audit(path, audit)
            with self.assertRaisesRegex(FinancialReportingBuildError, "statement source audit"):
                _verify_financial_source_audit(path)

    def test_semantic_verification_cache_is_manifest_and_policy_bound(self) -> None:
        target = (
            "quanthus.pipelines.securities_warehouse.financial_reporting."
            "_verify_financial_concept_evidence"
        )
        _FINANCIAL_SEMANTIC_VERIFICATION_CACHE.clear()
        try:
            with patch(target) as verify:
                for _ in range(2):
                    _verify_financial_candidate_semantics_once(
                        Path("/fixture/candidate"),
                        manifest_sha256="sha256:manifest-a",
                        policy_sha256="sha256:policy-a",
                        expected_evidence_rows=7,
                    )
                _verify_financial_candidate_semantics_once(
                    Path("/fixture/candidate"),
                    manifest_sha256="sha256:manifest-b",
                    policy_sha256="sha256:policy-a",
                    expected_evidence_rows=7,
                )
            self.assertEqual(verify.call_count, 2)
            verify.assert_any_call(
                Path("/fixture/candidate"),
                expected_evidence_rows=7,
                verify_semantics=True,
            )
        finally:
            _FINANCIAL_SEMANTIC_VERIFICATION_CACHE.clear()

        with (
            patch(target, side_effect=FinancialReportingBuildError("semantic failure")),
            self.assertRaisesRegex(FinancialReportingBuildError, "semantic failure"),
        ):
            _verify_financial_candidate_semantics_once(
                Path("/fixture/candidate"),
                manifest_sha256="sha256:failed-manifest",
                policy_sha256="sha256:policy-a",
                expected_evidence_rows=7,
            )
        self.assertNotIn(
            ("sha256:failed-manifest", "sha256:policy-a"),
            _FINANCIAL_SEMANTIC_VERIFICATION_CACHE,
        )

    def test_concept_merge_is_input_order_independent(self) -> None:
        first_knowledge = datetime(2026, 8, 1, tzinfo=timezone.utc)
        second_knowledge = datetime(2026, 8, 2, tzinfo=timezone.utc)
        rows = [
            {
                "financial_concept_id": "concept-1",
                "effective_from": datetime(1998, 12, 30, 16, tzinfo=timezone.utc),
                "knowledge_from": first_knowledge,
                "source_record_id": _concept_source_record_id(
                    "record-b", period_end="1998-12-31", acquired_at=first_knowledge
                ),
                "source_artifact_sha256": "sha256:" + "b" * 64,
                "record_source": "source-b",
                "source_revision": "revision-b",
            },
            {
                "financial_concept_id": "concept-1",
                "effective_from": datetime(1990, 12, 30, 16, tzinfo=timezone.utc),
                "knowledge_from": second_knowledge,
                "source_record_id": _concept_source_record_id(
                    "record-c", period_end="1990-12-31", acquired_at=second_knowledge
                ),
                "source_artifact_sha256": "sha256:" + "c" * 64,
                "record_source": "source-c",
                "source_revision": "revision-c",
            },
            {
                "financial_concept_id": "concept-1",
                "effective_from": datetime(2000, 12, 30, 16, tzinfo=timezone.utc),
                "knowledge_from": first_knowledge,
                "source_record_id": _concept_source_record_id(
                    "record-a", period_end="2000-12-31", acquired_at=first_knowledge
                ),
                "source_artifact_sha256": "sha256:" + "a" * 64,
                "record_source": "source-a",
                "source_revision": "revision-a",
            },
            {
                "financial_concept_id": "concept-1",
                "effective_from": datetime(2001, 12, 30, 16, tzinfo=timezone.utc),
                "knowledge_from": first_knowledge,
                "source_record_id": _concept_source_record_id(
                    "record-a", period_end="2000-12-31", acquired_at=first_knowledge
                ),
                "source_artifact_sha256": "sha256:" + "a" * 64,
                "record_source": "source-0",
                "source_revision": "revision-0",
            },
        ]

        results = []
        for order in itertools.permutations(rows):
            merged: dict[tuple[str, datetime, str], dict[str, object]] = {}
            for row in order:
                _merge_concept_row(merged, row)
            results.append([merged[key] for key in sorted(merged)])

        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(results[0]), 3)
        self.assertEqual(
            results[0][0]["effective_from"],
            datetime(2000, 12, 30, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(results[0][0]["record_source"], "source-a")
        self.assertEqual(
            results[0][1]["effective_from"],
            datetime(1998, 12, 30, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(results[0][1]["record_source"], "source-b")
        self.assertEqual(
            results[0][2]["effective_from"],
            datetime(1990, 12, 30, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(results[0][2]["record_source"], "source-c")

    def test_concept_finalize_uses_complete_deterministic_tie_break(self) -> None:
        schemas = financial_reporting_schemas()
        knowledge_from = datetime(2026, 8, 1, tzinfo=timezone.utc)
        base = {
            "record_source": "source-z",
            "source_record_id": _concept_source_record_id(
                "record-1", period_end="2000-12-31", acquired_at=knowledge_from
            ),
            "source_revision": "revision-z",
            "source_artifact_sha256": "sha256:" + "a" * 64,
            "knowledge_from": knowledge_from,
            "knowledge_to": None,
            "effective_from": datetime(2000, 12, 30, 16, tzinfo=timezone.utc),
            "effective_to": None,
            "financial_concept_id": "concept-1",
            "taxonomy_namespace": "fixture",
            "taxonomy_version": "fixture-v1",
            "concept_code": "CASH",
            "concept_name": "Cash",
            "statement_type": "balance_sheet",
            "data_type": "provider_native_scalar",
            "period_type": "instant",
            "balance_type": None,
        }
        tied = {
            **base,
            "record_source": "source-a",
            "source_revision": "revision-a",
        }
        retained_rows = []
        for rows in ([base, tied], [tied, base]):
            with TemporaryDirectory() as temporary:
                work = Path(temporary)
                staging = work / "staging/concepts"
                staging.mkdir(parents=True)
                for index, row in enumerate(rows):
                    pq.write_table(
                        pa.Table.from_pylist([row], schema=schemas["dwd_financial_concept"]),
                        staging / f"part-00-{index:06d}.parquet",
                    )
                staged_digests = {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in staging.glob("*.parquet")
                }

                self.assertEqual(_finalize_concepts(work, {"concept_finalized": False}, schemas), 1)
                evidence_digests = {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (work / _FINANCIAL_CONCEPT_EVIDENCE).glob("*.parquet")
                }
                self.assertEqual(evidence_digests, staged_digests)
                output_path = (
                    work / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
                )
                retained_rows.append(pq.read_table(output_path).to_pylist()[0])

        self.assertEqual(retained_rows[0], retained_rows[1])
        self.assertEqual(retained_rows[0]["record_source"], "source-a")
        self.assertEqual(retained_rows[0]["source_revision"], "revision-a")
        self.assertEqual(
            retained_rows[0]["effective_from"],
            datetime(2000, 12, 30, 16, tzinfo=timezone.utc),
        )

    def test_persisted_concept_evidence_recomputes_revisions_without_staging(self) -> None:
        schemas = financial_reporting_schemas()
        first_knowledge = datetime(2026, 8, 1, tzinfo=timezone.utc)
        second_knowledge = datetime(2026, 8, 2, tzinfo=timezone.utc)
        third_knowledge = datetime(2026, 8, 3, tzinfo=timezone.utc)
        base = {
            "record_source": "fixture",
            "source_record_id": _concept_source_record_id(
                "record-1", period_end="2000-01-01", acquired_at=first_knowledge
            ),
            "source_revision": "revision-1",
            "source_artifact_sha256": "sha256:" + "a" * 64,
            "knowledge_from": first_knowledge,
            "knowledge_to": None,
            "effective_from": datetime(1999, 12, 31, 16, tzinfo=timezone.utc),
            "effective_to": None,
            "financial_concept_id": "concept-1",
            "taxonomy_namespace": "fixture",
            "taxonomy_version": "fixture-v1",
            "concept_code": "CASH",
            "concept_name": "Cash",
            "statement_type": "balance_sheet",
            "data_type": "provider_native_scalar",
            "period_type": "instant",
            "balance_type": None,
        }
        evidence_rows = [
            base,
            {
                **base,
                "source_record_id": _concept_source_record_id(
                    "record-2", period_end="1990-01-01", acquired_at=second_knowledge
                ),
                "knowledge_from": second_knowledge,
                "effective_from": datetime(1989, 12, 31, 16, tzinfo=timezone.utc),
            },
            {
                **base,
                "source_record_id": _concept_source_record_id(
                    "record-3", period_end="1995-01-01", acquired_at=third_knowledge
                ),
                "knowledge_from": third_knowledge,
                "effective_from": datetime(1994, 12, 31, 16, tzinfo=timezone.utc),
            },
        ]
        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            staging = work / "staging/concepts"
            staging.mkdir(parents=True)
            pq.write_table(
                pa.Table.from_pylist(evidence_rows, schema=schemas["dwd_financial_concept"]),
                staging / "part-00-000000.parquet",
            )

            self.assertEqual(_finalize_concepts(work, {"concept_finalized": False}, schemas), 2)
            self.assertEqual(pq.read_table(work / _FINANCIAL_CONCEPT_EVIDENCE).num_rows, 3)
            concept_path = (
                work / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
            )
            concept_bytes = concept_path.read_bytes()
            concept_path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "evidence is unreadable",
            ):
                _finalize_concepts(
                    work,
                    {"concept_finalized": True, "concept_staging_retired": True},
                    schemas,
                )
            concept_path.write_bytes(concept_bytes)
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "evidence verification failed",
            ):
                _verify_financial_concept_evidence(work, expected_evidence_rows=2)
            shutil.rmtree(work / "staging")

            self.assertEqual(
                _verify_financial_concept_evidence(work),
                {
                    "unsupported_rows": 0,
                    "missing_revision_rows": 0,
                    "invalid_evidence_rows": 0,
                    "source_coordinate_violations": 0,
                    "semantic_conflict_rows": 0,
                    "parallel_state_rows": 0,
                    "boundary_violations": 0,
                },
            )
            self.assertEqual(_finalize_concepts(work, {"concept_finalized": True}, schemas), 2)

    def test_concept_checkpoint_rejects_missing_persisted_evidence(self) -> None:
        schemas = financial_reporting_schemas()
        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            concept_path = (
                work / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
            )
            concept_path.parent.mkdir(parents=True)
            pq.write_table(
                pa.Table.from_pylist([], schema=schemas["dwd_financial_concept"]), concept_path
            )
            with self.assertRaisesRegex(FinancialReportingBuildError, "incomplete output"):
                _finalize_concepts(work, {"concept_finalized": True}, schemas)

    def test_concept_evidence_metrics_detect_fault_injections(self) -> None:
        schemas = financial_reporting_schemas()
        first_knowledge = datetime(2026, 8, 1, tzinfo=timezone.utc)
        second_knowledge = datetime(2026, 8, 2, tzinfo=timezone.utc)
        base = {
            "record_source": "fixture",
            "source_record_id": _concept_source_record_id(
                "record-1", period_end="2000-01-01", acquired_at=first_knowledge
            ),
            "source_revision": "revision-1",
            "source_artifact_sha256": "sha256:" + "a" * 64,
            "knowledge_from": first_knowledge,
            "knowledge_to": None,
            "effective_from": datetime(1999, 12, 31, 16, tzinfo=timezone.utc),
            "effective_to": None,
            "financial_concept_id": "concept-1",
            "taxonomy_namespace": "fixture",
            "taxonomy_version": "fixture-v1",
            "concept_code": "CASH",
            "concept_name": "Cash",
            "statement_type": "balance_sheet",
            "data_type": "provider_native_scalar",
            "period_type": "instant",
            "balance_type": None,
        }
        evidence_rows = [
            base,
            {
                **base,
                "source_record_id": _concept_source_record_id(
                    "record-2", period_end="1990-01-01", acquired_at=second_knowledge
                ),
                "knowledge_from": second_knowledge,
                "effective_from": datetime(1989, 12, 31, 16, tzinfo=timezone.utc),
            },
        ]
        output_rows = [
            {**evidence_rows[0], "knowledge_to": evidence_rows[1]["knowledge_from"]},
            evidence_rows[1],
        ]

        def metrics(
            concepts: list[dict[str, object]], evidence: list[dict[str, object]]
        ) -> dict[str, int]:
            import duckdb

            connection = duckdb.connect(":memory:")
            try:
                connection.register(
                    "dwd_financial_concept",
                    pa.Table.from_pylist(concepts, schema=schemas["dwd_financial_concept"]),
                )
                connection.register(
                    "financial_concept_evidence",
                    pa.Table.from_pylist(evidence, schema=schemas["dwd_financial_concept"]),
                )
                return _concept_revision_evidence_metrics(connection)
            finally:
                connection.close()

        self.assertFalse(any(metrics(output_rows, evidence_rows).values()))
        self.assertEqual(metrics(output_rows[:1], evidence_rows)["missing_revision_rows"], 1)
        tampered_output = [{**output_rows[0], "source_record_id": "tampered"}, output_rows[1]]
        self.assertEqual(metrics(tampered_output, evidence_rows)["unsupported_rows"], 1)
        invalid_evidence = [
            {**evidence_rows[0], "knowledge_to": datetime(2026, 8, 2, tzinfo=timezone.utc)},
            evidence_rows[1],
        ]
        self.assertEqual(metrics(output_rows, invalid_evidence)["invalid_evidence_rows"], 1)
        false_coordinate = datetime(1880, 1, 1, tzinfo=timezone.utc)
        forged_evidence = [
            evidence_rows[0],
            {**evidence_rows[1], "effective_from": false_coordinate},
        ]
        forged_output = [
            output_rows[0],
            {**output_rows[1], "effective_from": false_coordinate},
        ]
        forged_metrics = metrics(forged_output, forged_evidence)
        self.assertEqual(forged_metrics["unsupported_rows"], 0)
        self.assertEqual(forged_metrics["missing_revision_rows"], 0)
        self.assertEqual(forged_metrics["source_coordinate_violations"], 1)
        conflicting_evidence = [
            *evidence_rows,
            {**evidence_rows[0], "concept_name": "Conflicting cash"},
        ]
        self.assertEqual(metrics(output_rows, conflicting_evidence)["semantic_conflict_rows"], 1)
        self.assertGreater(
            metrics([*output_rows, output_rows[0]], evidence_rows)["parallel_state_rows"], 0
        )

    def test_v3_builder_contract_rejects_v2_checkpoint_and_candidate(self) -> None:
        policy = json.loads(_FINANCIAL_CONCEPT_SEMANTICS_POLICY.read_text(encoding="utf-8"))
        self.assertEqual(policy["builder_contract"], _FINANCIAL_BUILDER_CONTRACT)
        self.assertEqual(
            policy["knowledge_interval_identity"],
            ["financial_concept_id", "taxonomy_version"],
        )
        identity = {
            "schema_version": 1,
            "run_id": "run-1",
            "builder_contract": _FINANCIAL_BUILDER_CONTRACT,
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = {
                "schema_version": 1,
                "identity": {**identity, "builder_contract": "financial-reporting-six-source-v2"},
                "completed_batches": {},
                "concept_finalized": False,
            }
            (root / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
            with self.assertRaisesRegex(FinancialReportingBuildError, "different identity"):
                _load_or_initialize_checkpoint(root, identity)
            checkpoint = {
                "schema_version": 1,
                "identity": identity,
                "completed_batches": {},
                "concept_finalized": True,
                "concept_staging_retired": "false",
            }
            (root / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
            with self.assertRaisesRegex(FinancialReportingBuildError, "different identity"):
                _load_or_initialize_checkpoint(root, identity)

            candidate = root / "candidate"
            candidate.mkdir()
            manifest = {
                "schema_version": 1,
                "kind": "dwd_financial_reporting_candidate",
                "run_id": "run-1",
                "input_plan_digest": "plan-1",
                "model_sha256": "sha256:model",
                "builder_contract": "financial-reporting-six-source-v2",
            }
            manifest_path = candidate / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (candidate / "READY").write_text(
                "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                encoding="ascii",
            )
            with self.assertRaisesRegex(FinancialReportingBuildError, "binding differs"):
                verify_financial_reporting_candidate(
                    candidate,
                    run_id="run-1",
                    input_plan_digest="plan-1",
                    model_sha256="sha256:model",
                )

    def test_checkpoint_files_are_verified_and_orphan_batch_is_replayable(self) -> None:
        schemas = financial_reporting_schemas()
        knowledge_from = datetime(2026, 8, 1, tzinfo=timezone.utc)
        concept = {
            "record_source": "fixture",
            "source_record_id": _concept_source_record_id(
                "record-1", period_end="2000-01-01", acquired_at=knowledge_from
            ),
            "source_revision": "revision-1",
            "source_artifact_sha256": "sha256:" + "a" * 64,
            "knowledge_from": knowledge_from,
            "knowledge_to": None,
            "effective_from": datetime(1999, 12, 31, 16, tzinfo=timezone.utc),
            "effective_to": None,
            "financial_concept_id": "concept-1",
            "taxonomy_namespace": "fixture",
            "taxonomy_version": "fixture-v1",
            "concept_code": "CASH",
            "concept_name": "Cash",
            "statement_type": "balance_sheet",
            "data_type": "provider_native_scalar",
            "period_type": "instant",
            "balance_type": None,
        }
        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            files = _write_batch(
                work=work,
                batch_id="00-000000",
                rows_by_year={
                    table: {} for table in ("dwd_financial_report", "dwd_financial_fact")
                },
                concepts=[concept],
                schemas=schemas,
            )
            completed = {
                "source": "fixture-source",
                "selected_source_rows": 1,
                "row_counts": {
                    "dwd_financial_report": 0,
                    "dwd_financial_concept": 1,
                    "dwd_financial_fact": 0,
                },
                "quality_exceptions": {"BEFORE_FIRST": 2},
                "files": files,
            }
            completed["batch_digest"] = _checkpoint_batch_digest(completed)
            relative_work = Path(os.path.relpath(work, Path.cwd()))
            self.assertTrue(
                _checkpoint_batch_files_valid(
                    relative_work,
                    "00-000000",
                    completed,
                    source_id="fixture-source",
                    selected_source_rows=1,
                )
            )
            self.assertFalse(
                _checkpoint_batch_files_valid(
                    work,
                    "00-000000",
                    {
                        "source": "fixture-source",
                        "selected_source_rows": 0,
                        "row_counts": {
                            table: 0
                            for table in (
                                "dwd_financial_report",
                                "dwd_financial_concept",
                                "dwd_financial_fact",
                            )
                        },
                        "quality_exceptions": {},
                        "files": [],
                        "batch_digest": "sha256:" + "0" * 64,
                    },
                    source_id="fixture-source",
                    selected_source_rows=0,
                )
            )
            tampered_exceptions = {
                **completed,
                "quality_exceptions": {"BEFORE_FIRST": 3},
            }
            self.assertFalse(
                _checkpoint_batch_files_valid(
                    work,
                    "00-000000",
                    tampered_exceptions,
                    source_id="fixture-source",
                    selected_source_rows=1,
                )
            )
            self.assertFalse(
                _checkpoint_batch_files_valid(
                    work,
                    "00-000000",
                    completed,
                    source_id="fixture-source",
                    selected_source_rows=2,
                )
            )
            with self.assertRaisesRegex(FinancialReportingBuildError, "path is unsafe"):
                _checkpoint_batch_files_valid(
                    work,
                    "00-000001",
                    completed,
                    source_id="fixture-source",
                    selected_source_rows=1,
                )
            (work / str(files[0]["path"])).write_bytes(b"corrupt")
            self.assertFalse(
                _checkpoint_batch_files_valid(
                    work,
                    "00-000000",
                    completed,
                    source_id="fixture-source",
                    selected_source_rows=1,
                )
            )
            corrupt_record = {
                **files[0],
                "size_bytes": len(b"corrupt"),
                "sha256": "sha256:" + hashlib.sha256(b"corrupt").hexdigest(),
            }
            corrupt_checkpoint = {**completed, "files": [corrupt_record]}
            corrupt_checkpoint["batch_digest"] = _checkpoint_batch_digest(corrupt_checkpoint)
            self.assertFalse(
                _checkpoint_batch_files_valid(
                    work,
                    "00-000000",
                    corrupt_checkpoint,
                    source_id="fixture-source",
                    selected_source_rows=1,
                )
            )

            _discard_uncheckpointed_batch_files(work, "00-000000")
            self.assertFalse((work / str(files[0]["path"])).exists())
            replayed = _write_batch(
                work=work,
                batch_id="00-000000",
                rows_by_year={
                    table: {} for table in ("dwd_financial_report", "dwd_financial_fact")
                },
                concepts=[concept],
                schemas=schemas,
            )
            replayed_checkpoint = {**completed, "files": replayed}
            replayed_checkpoint["batch_digest"] = _checkpoint_batch_digest(replayed_checkpoint)
            self.assertTrue(
                _checkpoint_batch_files_valid(
                    work,
                    "00-000000",
                    replayed_checkpoint,
                    source_id="fixture-source",
                    selected_source_rows=1,
                )
            )
            finalization_checkpoint = {
                "concept_finalized": False,
                "concept_staging_retired": False,
            }
            retire_target = (
                "quanthus.pipelines.securities_warehouse.financial_reporting."
                "_retire_concept_staging"
            )
            with (
                patch(
                    retire_target,
                    side_effect=FinancialReportingBuildError("injected retirement failure"),
                ),
                self.assertRaisesRegex(FinancialReportingBuildError, "injected retirement"),
            ):
                _finalize_concepts(work, finalization_checkpoint, schemas)
            self.assertTrue(finalization_checkpoint["concept_finalized"])
            self.assertTrue(finalization_checkpoint["concept_staging_retired"])
            self.assertTrue((work / "staging/concepts").is_dir())
            self.assertFalse((work / _FINANCIAL_CONCEPT_EVIDENCE).exists())
            self.assertEqual(_finalize_concepts(work, finalization_checkpoint, schemas), 1)
            evidence_path = next((work / _FINANCIAL_CONCEPT_EVIDENCE).glob("*.parquet"))
            evidence_relative = evidence_path.relative_to(work).as_posix()
            manifest_file = {
                "path": evidence_relative,
                "size_bytes": evidence_path.stat().st_size,
                "sha256": "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            }
            identity_fields = (
                "run_id",
                "input_plan_digest",
                "model_sha256",
                "source_audit_sha256",
                "builder_contract",
                "financial_concept_semantics_policy_sha256",
                "listed_company_manifest_sha256",
                "listed_company_semantic_digest",
                "source_sha256",
            )
            sealed_source_audit = _financial_source_audit_fixture()
            sealed_manifest = {
                "source_audit_sha256": "sha256:audit",
                "run_id": sealed_source_audit["run_id"],
                "input_plan_digest": sealed_source_audit["input_plan_digest"],
                "model_sha256": sealed_source_audit["model_sha256"],
                "builder_contract": _FINANCIAL_BUILDER_CONTRACT,
                "financial_concept_semantics_policy_sha256": "sha256:policy",
                "listed_company_manifest_sha256": sealed_source_audit[
                    "listed_company_manifest_sha256"
                ],
                "listed_company_semantic_digest": sealed_source_audit[
                    "listed_company_semantic_digest"
                ],
                "source_sha256": sealed_source_audit["source_sha256"],
                "row_counts": {
                    "dwd_financial_report": 0,
                    "dwd_financial_concept": 1,
                    "dwd_financial_fact": 0,
                },
            }
            table_digests = {
                table: _digest_value([])
                for table in (
                    "dwd_financial_report",
                    "dwd_financial_concept",
                    "dwd_financial_fact",
                )
            }
            semantic_digest = _digest_value(
                {
                    "schema_version": 1,
                    **{field: sealed_manifest.get(field) for field in identity_fields},
                    "table_digests": table_digests,
                }
            )
            sealed_manifest["semantic_digest"] = semantic_digest
            sealed_replayed_checkpoint = {
                **replayed_checkpoint,
                "source": _FINANCIAL_SOURCE_ORDER[0],
            }
            sealed_replayed_checkpoint["batch_digest"] = _checkpoint_batch_digest(
                sealed_replayed_checkpoint
            )
            sealed_checkpoint = {
                **finalization_checkpoint,
                "identity": {
                    "schema_version": 1,
                    **{field: sealed_manifest.get(field) for field in identity_fields},
                    "source_batch_size": 1,
                    "native_batch_size": 1,
                },
                "completed_batches": {"00-000000": sealed_replayed_checkpoint},
                "completed": True,
                "semantic_digest": semantic_digest,
            }
            sealed_receipt = {
                "schema_version": 1,
                "kind": "dwd_financial_reporting_build_receipt",
                "status": "built",
                **{field: sealed_manifest.get(field) for field in identity_fields},
                "semantic_digest": semantic_digest,
                "row_counts": sealed_manifest["row_counts"],
                "table_digests": table_digests,
                "concept_evidence_storage": _FINANCIAL_CONCEPT_EVIDENCE_STORAGE,
                "quality_exceptions": {"BEFORE_FIRST": 2},
                "resource_policy": _FINANCIAL_RESOURCE_POLICY,
            }
            sealed_reconciliation = {
                "schema_version": 1,
                "kind": "dwd_financial_reporting_reconciliation",
                "row_counts": sealed_manifest["row_counts"],
                "expected_reports": 0,
                "expected_facts": 0,
                "concept_evidence_rows": 1,
                "concept_staging_rows": 1,
                "selected_source_rows": 1,
                "source_audit_sha256": "sha256:audit",
                "concept_evidence_storage": _FINANCIAL_CONCEPT_EVIDENCE_STORAGE,
                "quality_exceptions": {"BEFORE_FIRST": 2},
                "audit_policy": "duckdb_year_partitioned_external_audit_v4",
                "year_partitions": {},
                "null_row_keys": {
                    "dwd_financial_report": 0,
                    "dwd_financial_concept": 0,
                    "dwd_financial_fact": 0,
                },
                "duplicate_business_keys": {
                    "dwd_financial_report": 0,
                    "dwd_financial_concept": 0,
                    "dwd_financial_fact": 0,
                },
                "orphan_foreign_keys": {
                    "financial_report_id": 0,
                    "financial_concept_id": 0,
                },
                "temporal_or_future_foreign_keys": {
                    "financial_report_id": 0,
                    "financial_concept_id": 0,
                },
                "concept_revision_evidence": {
                    "unsupported_rows": 0,
                    "missing_revision_rows": 0,
                    "invalid_evidence_rows": 0,
                    "source_coordinate_violations": 0,
                    "semantic_conflict_rows": 0,
                    "parallel_state_rows": 0,
                    "boundary_violations": 0,
                },
            }
            verification_arguments = {
                "checkpoint": sealed_checkpoint,
                "manifest": sealed_manifest,
                "manifest_files": {evidence_relative: manifest_file},
                "receipt": sealed_receipt,
                "reconciliation": sealed_reconciliation,
                "source_audit": sealed_source_audit,
                "source_audit_sha256": "sha256:audit",
                "parquet_rows": {evidence_relative: 1},
                "table_digests": table_digests,
                "physical_row_counts": {
                    "dwd_financial_report": 0,
                    "dwd_financial_concept": 1,
                    "dwd_financial_fact": 0,
                },
                "expected_evidence_rows": 1,
            }
            _verify_financial_checkpoint_evidence_binding(
                work,
                **verification_arguments,
            )
            two_source_audit = _financial_source_audit_fixture(include_second_statement=True)
            second_batch = {
                "source": _FINANCIAL_SOURCE_ORDER[1],
                "selected_source_rows": 1,
                "row_counts": {table: 0 for table in sealed_manifest["row_counts"]},
                "quality_exceptions": {"AFTER_THROUGH_DATE": 1},
                "files": [],
            }
            second_batch["batch_digest"] = _checkpoint_batch_digest(second_batch)
            two_source_checkpoint = {
                **sealed_checkpoint,
                "completed_batches": {
                    "00-000000": sealed_replayed_checkpoint,
                    "01-000000": second_batch,
                },
            }
            two_source_receipt = {
                **sealed_receipt,
                "quality_exceptions": {
                    "AFTER_THROUGH_DATE": 1,
                    "BEFORE_FIRST": 2,
                },
            }
            two_source_reconciliation = {
                **sealed_reconciliation,
                "selected_source_rows": 2,
                "quality_exceptions": two_source_receipt["quality_exceptions"],
            }
            two_source_arguments = {
                **verification_arguments,
                "checkpoint": two_source_checkpoint,
                "receipt": two_source_receipt,
                "reconciliation": two_source_reconciliation,
                "source_audit": two_source_audit,
            }
            _verify_financial_checkpoint_evidence_binding(work, **two_source_arguments)
            swapped_first = {
                **sealed_replayed_checkpoint,
                "quality_exceptions": {"AFTER_THROUGH_DATE": 1},
            }
            swapped_first["batch_digest"] = _checkpoint_batch_digest(swapped_first)
            swapped_second = {
                **second_batch,
                "quality_exceptions": {"BEFORE_FIRST": 2},
            }
            swapped_second["batch_digest"] = _checkpoint_batch_digest(swapped_second)
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "checkpoint reconciliation differs",
            ):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **two_source_arguments,
                        "checkpoint": {
                            **two_source_checkpoint,
                            "completed_batches": {
                                "00-000000": swapped_first,
                                "01-000000": swapped_second,
                            },
                        },
                    },
                )
            rogue = work / _FINANCIAL_CONCEPT_EVIDENCE / "ignored-control.bin"
            rogue.write_bytes(b"rogue")
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "evidence inventory is invalid",
            ):
                _financial_concept_evidence_paths(work)
            rogue.unlink()
            rogue_directory = work / _FINANCIAL_CONCEPT_EVIDENCE / "ignored-subdir"
            rogue_directory.mkdir()
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "evidence inventory is invalid",
            ):
                _financial_concept_evidence_paths(work)
            rogue_directory.rmdir()

            wrong_name_record = {
                **sealed_replayed_checkpoint["files"][0],
                "path": "staging/concepts/part-00-000001.parquet",
            }
            wrong_name_batch = {
                **sealed_replayed_checkpoint,
                "files": [wrong_name_record],
            }
            wrong_name_batch["batch_digest"] = _checkpoint_batch_digest(wrong_name_batch)
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "concept path is not canonical",
            ):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **verification_arguments,
                        "checkpoint": {
                            **sealed_checkpoint,
                            "completed_batches": {"00-000000": wrong_name_batch},
                        },
                    },
                )
            wrong_source_batch = {
                **sealed_replayed_checkpoint,
                "source": _FINANCIAL_SOURCE_ORDER[1],
            }
            wrong_source_batch["batch_digest"] = _checkpoint_batch_digest(wrong_source_batch)
            with self.assertRaisesRegex(FinancialReportingBuildError, "batch shape is invalid"):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **verification_arguments,
                        "checkpoint": {
                            **sealed_checkpoint,
                            "completed_batches": {"00-000000": wrong_source_batch},
                        },
                    },
                )
            rogue_fact = (
                "payload/financial_reporting/dwd_financial_fact/"
                "period_end_year=2026/part-00-999999.parquet"
            )
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "reconciliation differs",
            ):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **verification_arguments,
                        "manifest_files": {
                            **verification_arguments["manifest_files"],
                            rogue_fact: {
                                "path": rogue_fact,
                                "sha256": "sha256:rogue",
                                "size_bytes": 1,
                            },
                        },
                        "parquet_rows": {
                            **verification_arguments["parquet_rows"],
                            rogue_fact: 1,
                        },
                    },
                )
            with self.assertRaisesRegex(FinancialReportingBuildError, "seal state is invalid"):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **verification_arguments,
                        "receipt": {
                            "semantic_digest": semantic_digest,
                            "concept_evidence_storage": _FINANCIAL_CONCEPT_EVIDENCE_STORAGE,
                            "quality_exceptions": {"BEFORE_FIRST": 2},
                        },
                    },
                )
            with self.assertRaisesRegex(FinancialReportingBuildError, "source audit"):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **verification_arguments,
                        "source_audit": {
                            "candidate_size_calibration": {
                                "dwd_financial_report_rows": 9,
                                "dwd_financial_fact_rows": 0,
                            }
                        },
                    },
                )
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "reconciliation differs",
            ):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **verification_arguments,
                        "receipt": {**sealed_receipt, "quality_exceptions": {}},
                    },
                )
            with self.assertRaisesRegex(
                FinancialReportingBuildError,
                "reconciliation seal binding differs",
            ):
                _verify_financial_checkpoint_evidence_binding(
                    work,
                    **{
                        **verification_arguments,
                        "reconciliation": {
                            **sealed_reconciliation,
                            "concept_revision_evidence": {},
                        },
                    },
                )
            shutil.rmtree(work / "staging")
            self.assertTrue(
                _checkpoint_batch_files_valid(
                    work,
                    "00-000000",
                    replayed_checkpoint,
                    source_id="fixture-source",
                    selected_source_rows=1,
                    concept_staging_retired=True,
                )
            )
            self.assertEqual(
                _finalize_concepts(work, finalization_checkpoint, schemas),
                1,
            )

    def test_frozen_inputs_are_reverified_before_sealing(self) -> None:
        with TemporaryDirectory() as temporary:
            source_audit = Path(temporary) / "source-audit.json"
            source_audit.write_bytes(b"{}")
            identity = {
                "run_id": "run-1",
                "source_audit_sha256": "sha256:"
                + hashlib.sha256(source_audit.read_bytes()).hexdigest(),
            }
            arguments = {
                "initial_identity": identity,
                "ods_package": Path("/fixture/ods"),
                "input_plan": Path("/fixture/plan"),
                "model_path": Path("/fixture/model"),
                "listed_company_candidate": Path("/fixture/listed"),
                "source_audit_path": source_audit,
                "run_id": "run-1",
                "initial_source_audit_bytes": b"{}",
            }
            target = (
                "quanthus.pipelines.securities_warehouse.financial_reporting._candidate_identity"
            )
            with patch(target, return_value=(dict(identity), None, None, None, None, None, b"{}")):
                self.assertEqual(_reverify_financial_build_inputs(**arguments), b"{}")
            with (
                patch(
                    target,
                    return_value=(
                        {**identity, "run_id": "changed"},
                        None,
                        None,
                        None,
                        None,
                        None,
                        b"{}",
                    ),
                ),
                self.assertRaisesRegex(FinancialReportingBuildError, "frozen inputs changed"),
            ):
                _reverify_financial_build_inputs(**arguments)
            source_audit.write_bytes(b'{"changed":true}')
            with (
                patch(
                    target,
                    return_value=(
                        dict(identity),
                        None,
                        None,
                        None,
                        None,
                        None,
                        b'{"changed":true}',
                    ),
                ),
                self.assertRaisesRegex(FinancialReportingBuildError, "source audit changed"),
            ):
                _reverify_financial_build_inputs(**arguments)

    def test_plan_date_accepts_the_sealed_plan_format(self) -> None:
        self.assertEqual(_plan_date("2026-07-31", "through_date").isoformat(), "2026-07-31")
        with self.assertRaisesRegex(FinancialReportingBuildError, "YYYY-MM-DD"):
            _plan_date("20260731", "through_date")

    def test_decodes_byte_literal_gzip_and_hash_checks_payload(self) -> None:
        row = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20251231": {"data": []},
                            "20260331": {"data": []},
                        }
                    }
                }
            }
        )
        value = decode_financial_envelope(row)
        self.assertEqual(list(value.report_list), ["20251231", "20260331"])
        self.assertGreater(len(value.raw_payload), 0)

    def test_rejects_tampered_payload_digest(self) -> None:
        row = _envelope({"result": {"data": {"report_list": {}}}})
        row["raw_payload_sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(FinancialReportingBuildError, "SHA-256"):
            decode_financial_envelope(row)

    def test_classifies_every_provider_item(self) -> None:
        report = {
            "data": [
                {"item_field": "", "item_display_type": 1, "item_value": ""},
                {"item_field": "CASH", "item_display_type": 2, "item_value": "12.50"},
                {"item_field": "DEBT", "item_display_type": 2, "item_value": ""},
                {"item_field": "", "item_display_type": 2, "item_value": "12.50"},
                "malformed",
            ]
        }
        self.assertEqual(classify_statement_rows(report).heading_rows, 1)
        self.assertEqual(classify_statement_rows(report).valid_facts, 1)
        self.assertEqual(classify_statement_rows(report).null_placeholders, 1)
        self.assertEqual(classify_statement_rows(report).invalid_rows, 2)

    def test_uses_conservative_end_of_day_for_date_only_availability(self) -> None:
        self.assertEqual(
            conservative_provider_timestamp("2026-08-02"),
            datetime(2026, 8, 2, 15, 59, 59, 999999, tzinfo=timezone.utc),
        )

    def test_iterates_reports_in_period_order(self) -> None:
        row = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20260331": {"data": [{"item_field": "A", "item_value": "1"}]},
                            "20251231": {"data": [{"item_field": "B", "item_value": "2"}]},
                        }
                    }
                }
            }
        )
        reports = iter_statement_reports(decode_financial_envelope(row))
        periods = [period for period, _report, _accounting in reports]
        self.assertEqual(periods, ["20251231", "20260331"])

    def test_stream_audit_keeps_matching_parent_overlap_and_hotfix_fill(self) -> None:
        parent_envelope = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20251231": {
                                "data": [
                                    {"item_display_type": 1, "item_field": "", "item_value": ""},
                                    {"item_field": "CASH", "item_value": "12.5"},
                                    {"item_field": "DEBT", "item_value": ""},
                                ]
                            }
                        }
                    }
                }
            }
        )
        fill_envelope = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20251231": {"data": [{"item_field": "PROFIT", "item_value": "4"}]}
                        }
                    }
                }
            }
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent.parquet"
            hotfix = root / "hotfix.parquet"
            parent_row = {
                **parent_envelope,
                "security_id": "000001",
                "stock_code": "000001",
                "statement_type": "balance_sheet",
            }
            pq.write_table(pa.Table.from_pylist([parent_row]), parent)
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        parent_row,
                        {
                            **fill_envelope,
                            "security_id": "000002",
                            "stock_code": "000002",
                            "statement_type": "income_statement",
                        },
                    ]
                ),
                hotfix,
            )
            sources = (
                FinancialStatementSource(
                    "financial_statements",
                    parent,
                    "payload/full.parquet",
                    "sha256:parent",
                    "parent-r1",
                    "parent",
                ),
                FinancialStatementSource(
                    "fundamentals_financial_statements_parquet",
                    hotfix,
                    "payload/hotfix.parquet",
                    "sha256:hotfix",
                    "hotfix-r1",
                    "hotfix",
                ),
            )
            audit = audit_financial_statement_sources(sources, batch_size=1)
            self.assertEqual(audit.report["overlay"]["parent_overlap_rows"], 1)
            self.assertEqual(audit.report["overlay"]["hotfix_fill_rows"], 1)
            self.assertEqual(audit.report["overlay"]["selected_envelope_rows"], 2)
            self.assertEqual(
                audit.report["candidate_size_calibration"]["dwd_financial_fact_upper_bound"],
                3,
            )
            output = write_financial_reporting_source_audit(root / "audit.json", audit)
            self.assertEqual(output.read_text(encoding="ascii"), output.read_text(encoding="ascii"))

    def test_stream_audit_rejects_different_parent_hotfix_overlap(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent.parquet"
            hotfix = root / "hotfix.parquet"
            base = {"security_id": "000001", "stock_code": "000001", "statement_type": "cash_flow"}
            pq.write_table(
                pa.Table.from_pylist(
                    [{**base, **_envelope({"result": {"data": {"report_list": {}}}})}]
                ),
                parent,
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            **base,
                            **_envelope(
                                {"result": {"data": {"report_list": {"20251231": {"data": []}}}}}
                            ),
                        }
                    ]
                ),
                hotfix,
            )
            sources = (
                FinancialStatementSource("parent", parent, "parent", "sha256:parent", "", "parent"),
                FinancialStatementSource("hotfix", hotfix, "hotfix", "sha256:hotfix", "", "hotfix"),
            )
            with self.assertRaisesRegex(FinancialReportingBuildError, "overlap has different"):
                audit_financial_statement_sources(sources)

    def test_combines_statement_and_native_audits_with_build_bindings(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            statement_path = root / "statement.json"
            native_path = root / "native.json"
            output = root / "combined.json"
            fixture = _financial_source_audit_fixture()
            for path, component in (
                (statement_path, fixture["component_audits"]["statement"]["audit"]),
                (native_path, fixture["component_audits"]["native"]["audit"]),
            ):
                path.write_text(
                    json.dumps(component, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="ascii",
                )

            audit = combine_financial_reporting_source_audits(
                statement_audit_path=statement_path,
                native_audit_path=native_path,
                output=output,
            )

            self.assertEqual(
                audit.report["candidate_size_calibration"],
                {
                    "dwd_financial_report_rows": 0,
                    "dwd_financial_fact_rows": 0,
                    "statement_report_rows": 0,
                    "statement_fact_rows": 0,
                    "native_report_rows": 0,
                    "native_fact_rows": 0,
                },
            )
            self.assertTrue(output.is_file())
            _verify_financial_source_audit(output)

    def test_selects_exact_parent_and_fill_only_hotfix_from_ledger(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            full = root / "payload" / "source-evidence" / "financial_statements.parquet"
            hotfix = root / "payload" / "fundamentals" / "financial_statements.parquet"
            full.parent.mkdir(parents=True)
            hotfix.parent.mkdir(parents=True)
            full.write_bytes(b"full")
            hotfix.write_bytes(b"hotfix")
            records = [
                {
                    "record_type": "file",
                    "disposition": "modeled",
                    "mapping_identity": "financial_statements",
                    "role": "retained_immutable_source_gap_fill",
                    "path": full.relative_to(root).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(b"full").hexdigest(),
                },
                {
                    "record_type": "file",
                    "disposition": "modeled",
                    "mapping_identity": "fundamentals_financial_statements_parquet",
                    "role": "provider_source_evidence",
                    "path": hotfix.relative_to(root).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(b"hotfix").hexdigest(),
                },
            ]
            parent, selected_hotfix = select_financial_statement_sources(records, root)
            self.assertEqual(parent.precedence, "parent")
            self.assertEqual(selected_hotfix.precedence, "hotfix")
            self.assertEqual(parent.path, full)
            self.assertEqual(selected_hotfix.path, hotfix)

    def test_selects_all_non_empty_financial_sources_without_identity_collapse(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            def write(relative: str, payload: bytes) -> tuple[str, int]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                return "sha256:" + hashlib.sha256(payload).hexdigest(), len(payload)

            files = {
                "payload/full/financial_statements.parquet": b"statements-full",
                "payload/hotfix/financial_statements.parquet": b"statements-hotfix",
                "payload/canonical/financial_quarterly.parquet": b"quarterly-canonical",
                "payload/full/financial_quarterly.parquet": b"quarterly-full",
                "payload/hotfix/financial_quarterly.parquet": b"quarterly-hotfix",
                "payload/canonical/info_archive.db": b"archive",
                "payload/daily/financial_disclosures_delta.parquet": b"empty",
            }
            identities = {relative: write(relative, payload) for relative, payload in files.items()}

            def file_record(
                *,
                identity: str,
                relative: str,
                role: str | None,
                rows: int,
                targets: list[str] | None = None,
            ) -> dict[str, object]:
                sha256, size = identities[relative]
                record: dict[str, object] = {
                    "record_type": "file",
                    "disposition": "modeled",
                    "mapping_identity": identity,
                    "path": relative,
                    "sha256": sha256,
                    "size_bytes": size,
                    "row_count": rows,
                    "target_tables": targets
                    or [
                        "dwd_financial_report",
                        "dwd_financial_concept",
                        "dwd_financial_fact",
                    ],
                }
                if role is not None:
                    record["role"] = role
                return record

            records = [
                file_record(
                    identity="financial_statements",
                    relative="payload/full/financial_statements.parquet",
                    role="retained_immutable_source_gap_fill",
                    rows=10,
                ),
                file_record(
                    identity="fundamentals_financial_statements_parquet",
                    relative="payload/hotfix/financial_statements.parquet",
                    role="provider_source_evidence",
                    rows=2,
                ),
                file_record(
                    identity="financial_quarterly",
                    relative="payload/canonical/financial_quarterly.parquet",
                    role="source_evidence",
                    rows=8,
                ),
                file_record(
                    identity="financial_quarterly",
                    relative="payload/full/financial_quarterly.parquet",
                    role="retained_immutable_source_gap_fill",
                    rows=9,
                ),
                file_record(
                    identity="fundamentals_financial_quarterly_parquet",
                    relative="payload/hotfix/financial_quarterly.parquet",
                    role="provider_source_evidence",
                    rows=3,
                ),
                file_record(
                    identity="announcement_archive",
                    relative="payload/canonical/info_archive.db",
                    role=None,
                    rows=20,
                    targets=["dwd_disclosure"],
                ),
                {
                    "record_type": "container_member",
                    "disposition": "modeled",
                    "mapping_identity": "announcement_archive|sqlite_table|fin_abstract",
                    "container_source_id": "announcement_archive",
                    "container_path": "payload/canonical/info_archive.db",
                    "path": "payload/canonical/info_archive.db#table=fin_abstract",
                    "member": "fin_abstract",
                    "row_count": 20,
                    "target_tables": [
                        "dwd_financial_report",
                        "dwd_financial_concept",
                        "dwd_financial_fact",
                    ],
                },
                file_record(
                    identity="financial_disclosures_delta",
                    relative="payload/daily/financial_disclosures_delta.parquet",
                    role=None,
                    rows=0,
                ),
            ]

            inventory = select_financial_reporting_sources(records, root)

            self.assertEqual(
                [source.source_id for source in inventory.sources],
                [
                    "weekly_full_financial_statements",
                    "hotfix_financial_statements",
                    "canonical_financial_quarterly",
                    "weekly_full_financial_quarterly",
                    "hotfix_financial_quarterly",
                    "announcement_archive_fin_abstract",
                ],
            )
            self.assertEqual(inventory.sources[-1].member, "fin_abstract")
            self.assertEqual(
                inventory.empty_modeled_sources,
                ("payload/daily/financial_disclosures_delta.parquet",),
            )

            extra_path = "payload/unknown/non_empty.parquet"
            identities[extra_path] = write(extra_path, b"unknown")
            records.append(
                file_record(
                    identity="unknown_financial_source",
                    relative=extra_path,
                    role=None,
                    rows=1,
                )
            )
            with self.assertRaisesRegex(
                FinancialReportingBuildError, "unclassified non-empty financial source"
            ):
                select_financial_reporting_sources(records, root)

    def test_expands_lossless_envelope_into_three_typed_dwd_row_sets(self) -> None:
        row = {
            **_envelope(
                {
                    "result": {
                        "data": {
                            "report_list": {
                                "20251231": {
                                    "rType": "consolidated",
                                    "rCurrency": "CNY",
                                    "data_source": "provider-native",
                                    "is_audit": "audited",
                                    "publish_date": "2026-04-30",
                                    "update_time": 123,
                                    "data": [
                                        {"item_display_type": 1, "item_field": ""},
                                        {
                                            "item_field": "CASH",
                                            "item_title": "Cash",
                                            "item_value": "12.500000",
                                            "item_precision": "f2",
                                            "item_group_no": 1,
                                        },
                                        {
                                            "item_field": "CASH",
                                            "item_title": "Cash and equivalents",
                                            "item_value": "12.500000",
                                            "item_precision": "f2",
                                            "item_group_no": 1,
                                        },
                                        {
                                            "item_field": "NOTE",
                                            "item_title": "Note",
                                            "item_value": "not-numeric",
                                        },
                                        {
                                            "item_field": "DEBT",
                                            "item_title": "Debt",
                                            "item_value": "",
                                        },
                                        {
                                            "item_field": "CASH",
                                            "item_title": "Cash",
                                            "item_value": "12.500000",
                                            "item_precision": "f2",
                                            "item_group_no": 1,
                                        },
                                    ],
                                }
                            }
                        }
                    }
                }
            ),
            "security_id": "000001",
            "stock_code": "000001",
            "statement_type": "balance_sheet",
            "source": "sina.CompanyFinanceService.getFinanceReport2022",
            "source_revision": "provider-v1",
            "acquired_at": "2026-07-31T23:12:41+08:00",
        }
        source = FinancialStatementSource(
            "financial_statements",
            Path("/fixture/full.parquet"),
            "payload/full.parquet",
            "sha256:" + "a" * 64,
            "",
            "parent",
        )
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1991, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                )
            ]
        )
        expanded = expand_financial_envelope(row, source=source, company_resolver=resolver)
        self.assertEqual(len(expanded.reports), 1)
        self.assertEqual(len(expanded.concepts), 4)
        self.assertEqual(len(expanded.facts), 5)
        self.assertEqual(expanded.reports[0]["company_id"], "company-1")
        self.assertEqual(expanded.facts[0]["value_decimal"], Decimal("12.500000"))
        self.assertIsNone(expanded.facts[0]["value_text"])
        self.assertIsNone(expanded.facts[2]["value_decimal"])
        self.assertTrue(expanded.facts[3]["is_nil"])
        self.assertNotEqual(expanded.facts[0]["context_hash"], expanded.facts[4]["context_hash"])
        cash_concepts = {
            concept["financial_concept_id"]
            for concept in expanded.concepts
            if concept["concept_code"] == "CASH"
        }
        self.assertEqual(len(cash_concepts), 2)
        schemas = financial_reporting_schemas()
        for table, rows in (
            ("dwd_financial_report", expanded.reports),
            ("dwd_financial_concept", expanded.concepts),
            ("dwd_financial_fact", expanded.facts),
        ):
            self.assertTrue(
                pa.Table.from_pylist(list(rows), schema=schemas[table]).schema.equals(
                    schemas[table], check_metadata=True
                )
            )

    def test_statement_envelope_retains_each_report_period_as_concept_evidence(self) -> None:
        row = {
            **_envelope(
                {
                    "result": {
                        "data": {
                            "report_list": {
                                "20051231": {
                                    "rType": "consolidated",
                                    "rCurrency": "CNY",
                                    "data": [
                                        {
                                            "item_field": "CASH",
                                            "item_title": "Cash",
                                            "item_value": "2",
                                        }
                                    ],
                                },
                                "19901231": {
                                    "rType": "consolidated",
                                    "rCurrency": "CNY",
                                    "data": [
                                        {
                                            "item_field": "CASH",
                                            "item_title": "Cash",
                                            "item_value": "1",
                                        }
                                    ],
                                },
                            }
                        }
                    }
                }
            ),
            "security_id": "000001",
            "stock_code": "000001",
            "statement_type": "balance_sheet",
            "source": "test.finance",
            "source_revision": "test-r1",
            "acquired_at": "2026-07-31T23:12:41+08:00",
        }
        source = FinancialStatementSource(
            "financial_statements",
            Path("/fixture/full.parquet"),
            "payload/full.parquet",
            "sha256:" + "a" * 64,
            "",
            "parent",
        )
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1980, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                    knowledge_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                )
            ]
        )

        expanded = expand_financial_envelope(row, source=source, company_resolver=resolver)

        self.assertEqual(len(expanded.reports), 2)
        self.assertEqual(len(expanded.concepts), 2)
        self.assertEqual(len(expanded.facts), 2)
        self.assertTrue(
            all(
                concept["knowledge_from"] == datetime(2026, 7, 31, 15, 12, 41, tzinfo=timezone.utc)
                for concept in expanded.concepts
            )
        )
        self.assertTrue(
            all(
                report["knowledge_from"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
                for report in expanded.reports
            )
        )
        self.assertEqual(
            min(concept["effective_from"] for concept in expanded.concepts),
            datetime(1990, 12, 30, 16, 0, tzinfo=timezone.utc),
        )
        self.assertIn(
            "#stock_code=000001&statement_type=balance_sheet&period_end=1990-12-31"
            "&source_acquired_at_us=",
            "\n".join(str(concept["source_record_id"]) for concept in expanded.concepts),
        )
        schemas = financial_reporting_schemas()
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.register(
                "financial_facts",
                pa.Table.from_pylist(list(expanded.facts), schema=schemas["dwd_financial_fact"]),
            )
            connection.register(
                "financial_reports",
                pa.Table.from_pylist(
                    list(expanded.reports), schema=schemas["dwd_financial_report"]
                ),
            )
            connection.register(
                "financial_concepts",
                pa.Table.from_pylist(
                    list(expanded.concepts), schema=schemas["dwd_financial_concept"]
                ),
            )
            contracts = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")
            foreign_keys = _foreign_key_metrics(
                connection,
                child_source="financial_facts",
                child_contract=contracts["dwd_financial_fact"],
                parent_sources={
                    "dwd_financial_report": "financial_reports",
                    "dwd_financial_concept": "financial_concepts",
                },
                parent_contracts=contracts,
            )
            concept_intervals = _interval_metrics(
                connection,
                "financial_concepts",
                contracts["dwd_financial_concept"],
            )
        finally:
            connection.close()
        self.assertEqual(
            [
                (item["parent_table"], item["temporal_or_future_parent_rows"])
                for item in foreign_keys
            ],
            [("dwd_financial_report", 0), ("dwd_financial_concept", 0)],
        )
        self.assertEqual(
            concept_intervals,
            {
                "invalid_knowledge_interval_rows": 0,
                "invalid_effective_from_effective_to_rows": 0,
                "knowledge_interval_overlap_rows": 0,
                # Staging evidence retains both periods; finalization emits one revision.
                "effective_interval_overlap_rows": 1,
            },
        )

    def test_rejects_non_heading_item_without_concept_code(self) -> None:
        row = {
            **_envelope(
                {
                    "result": {
                        "data": {
                            "report_list": {
                                "20251231": {
                                    "data": [
                                        {
                                            "item_display_type": 2,
                                            "item_field": "",
                                            "item_value": "12.5",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            ),
            "security_id": "000001",
            "stock_code": "000001",
            "statement_type": "balance_sheet",
            "source": "fixture.finance",
            "source_revision": "fixture-v1",
            "acquired_at": "2026-07-31T23:12:41+08:00",
        }
        source = FinancialStatementSource(
            "financial_statements",
            Path("/fixture/full.parquet"),
            "payload/full.parquet",
            "sha256:" + "a" * 64,
            "",
            "parent",
        )
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1991, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                )
            ]
        )
        with self.assertRaisesRegex(FinancialReportingBuildError, "no item_field"):
            expand_financial_envelope(row, source=source, company_resolver=resolver)

    def test_expands_source_isolated_quarterly_and_abstract_rows(self) -> None:
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1991, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                )
            ]
        )
        cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
        canonical_source = FinancialReportingSource(
            source_id="canonical_financial_quarterly",
            source_kind="quarterly_projection",
            ledger_identity="financial_quarterly",
            path=Path("/fixture/canonical.parquet"),
            relative_path="payload/canonical/financial_quarterly.parquet",
            source_sha256="sha256:" + "a" * 64,
            source_revision="ods-base-202607-v1.2",
            precedence="independent",
        )
        canonical = expand_financial_native_row(
            {
                "stock_code": "000001",
                "report_date": datetime(2025, 12, 31),
                "publish_deadline": datetime(2026, 4, 30),
                "roe": 8.5,
                "revenue": 100.0,
                "net_profit": 20.0,
                "revenue_growth_yoy": None,
                "profit_growth_yoy": 5.0,
            },
            source=canonical_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(canonical.reports), 1)
        self.assertEqual(len(canonical.concepts), 5)
        self.assertEqual(len(canonical.facts), 5)
        self.assertEqual(
            {row["taxonomy_namespace"] for row in canonical.concepts},
            {"legacy.ods.financial_quarterly"},
        )
        units = {
            concept["concept_code"]: fact["unit_code"]
            for concept, fact in zip(canonical.concepts, canonical.facts, strict=True)
        }
        self.assertEqual(units["revenue"], "CNY")
        self.assertEqual(units["roe"], "PERCENT")
        self.assertTrue(
            next(
                fact["is_nil"]
                for concept, fact in zip(canonical.concepts, canonical.facts, strict=True)
                if concept["concept_code"] == "revenue_growth_yoy"
            )
        )

        sina_source = FinancialReportingSource(
            source_id="weekly_full_financial_quarterly",
            source_kind="quarterly_projection",
            ledger_identity="financial_quarterly",
            path=Path("/fixture/full.parquet"),
            relative_path="payload/full/financial_quarterly.parquet",
            source_sha256="sha256:" + "b" * 64,
            source_revision="quanthus-fundamentals-provider-v1",
            precedence="parent",
        )
        sina = expand_financial_native_row(
            {
                "security_id": "000001",
                "stock_code": "000001",
                "report_date": "20251231",
                "publish_date": "20260430",
                "revenue": "100.000000",
                "net_profit": "21.000000",
                "parent_net_profit": "20.000000",
                "currency": "CNY",
                "is_audit": "yes",
                "source": "sina.full_income_statement_projection",
                "source_revision": "provider-v1",
                "acquired_at": "2026-07-31T23:00:00+08:00",
            },
            source=sina_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(sina.facts), 3)
        self.assertNotEqual(
            canonical.reports[0]["financial_report_id"],
            sina.reports[0]["financial_report_id"],
        )
        self.assertEqual(
            {row["taxonomy_namespace"] for row in sina.concepts},
            {"sina.full_income_statement_projection"},
        )

        abstract_source = FinancialReportingSource(
            source_id="announcement_archive_fin_abstract",
            source_kind="financial_abstract",
            ledger_identity="announcement_archive|sqlite_table|fin_abstract",
            path=Path("/fixture/archive.db"),
            relative_path="payload/canonical/info_archive.db",
            source_sha256="sha256:" + "c" * 64,
            source_revision="ods-base-202607-v1.2",
            precedence="independent",
            member="fin_abstract",
        )
        abstract = expand_financial_native_row(
            {
                "code": "000001",
                "report_date": "2025-12-31",
                "revenue": 100.0,
                "eps_basic": 1.2,
                "ar_days": 20.0,
            },
            source=abstract_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(abstract.facts), 28)
        abstract_units = {
            concept["concept_code"]: fact["unit_code"]
            for concept, fact in zip(abstract.concepts, abstract.facts, strict=True)
        }
        self.assertEqual(abstract_units["eps_basic"], "CNY_PER_SHARE")
        self.assertEqual(abstract_units["ar_days"], "DAY")

        before_listing = expand_financial_native_row(
            {"stock_code": "000001", "report_date": "1990-12-31"},
            source=canonical_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(before_listing.reports), 0)
        self.assertEqual(
            before_listing.quality_exceptions[0]["code"],
            "BEFORE_FIRST_LISTED_COMPANY_IDENTITY",
        )

    def test_audits_native_sources_and_calibrates_selected_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical_path = root / "canonical.parquet"
            full_path = root / "full.parquet"
            hotfix_path = root / "hotfix.parquet"
            archive_path = root / "archive.db"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "000001",
                            "report_date": datetime(2025, 12, 31),
                            "publish_deadline": datetime(2026, 4, 30),
                            "roe": 8.5,
                            "revenue": 100.0,
                            "net_profit": 20.0,
                            "revenue_growth_yoy": 5.0,
                            "profit_growth_yoy": 4.0,
                        },
                        {
                            "stock_code": "000001",
                            "report_date": datetime(1990, 12, 31),
                            "publish_deadline": datetime(1991, 4, 30),
                            "roe": 1.0,
                            "revenue": 1.0,
                            "net_profit": 1.0,
                            "revenue_growth_yoy": 1.0,
                            "profit_growth_yoy": 1.0,
                        },
                    ]
                ),
                canonical_path,
            )

            def sina_row(stock_code: str) -> dict[str, object]:
                return {
                    "security_id": stock_code,
                    "stock_code": stock_code,
                    "report_date": "20251231",
                    "publish_date": "20260430",
                    "update_time": "20260430",
                    "revenue": "100.000000",
                    "net_profit": "21.000000",
                    "parent_net_profit": "20.000000",
                    "currency": "CNY",
                    "is_audit": "yes",
                    "audit_opinion": "clean",
                    "source": "sina.full_income_statement_projection",
                    "source_revision": "provider-v1",
                    "acquired_at": "2026-07-31T23:00:00+08:00",
                }

            pq.write_table(pa.Table.from_pylist([sina_row("000001")]), full_path)
            pq.write_table(pa.Table.from_pylist([sina_row("000002")]), hotfix_path)
            abstract_columns = ["code TEXT", "report_date TEXT"] + [
                f"{field} REAL"
                for field in (
                    "net_profit_attr",
                    "revenue",
                    "op_cost",
                    "net_profit",
                    "net_profit_deducted",
                    "equity",
                    "goodwill",
                    "ocf",
                    "eps_basic",
                    "bps",
                    "cfps",
                    "roe",
                    "roa",
                    "gross_margin",
                    "net_margin",
                    "expense_ratio",
                    "debt_ratio",
                    "revenue_yoy",
                    "np_attr_yoy",
                    "ocf_to_np",
                    "current_ratio",
                    "quick_ratio",
                    "equity_multiplier",
                    "inventory_turnover",
                    "ar_days",
                    "asset_turnover",
                    "retained_eps",
                    "ocf_ps",
                )
            ]
            with sqlite3.connect(archive_path) as connection:
                connection.execute(f"CREATE TABLE fin_abstract ({', '.join(abstract_columns)})")
                connection.execute(
                    "INSERT INTO fin_abstract (code, report_date, revenue) VALUES (?, ?, ?)",
                    ("000001", "2025-12-31", 100.0),
                )

            sources = (
                FinancialReportingSource(
                    "canonical_financial_quarterly",
                    "quarterly_projection",
                    "financial_quarterly",
                    canonical_path,
                    "payload/canonical/financial_quarterly.parquet",
                    "sha256:canonical",
                    "ods-base-v1.2",
                    "independent",
                    2,
                ),
                FinancialReportingSource(
                    "weekly_full_financial_quarterly",
                    "quarterly_projection",
                    "financial_quarterly",
                    full_path,
                    "payload/full/financial_quarterly.parquet",
                    "sha256:full",
                    "provider-v1",
                    "parent",
                    1,
                ),
                FinancialReportingSource(
                    "hotfix_financial_quarterly",
                    "quarterly_projection",
                    "fundamentals_financial_quarterly_parquet",
                    hotfix_path,
                    "payload/hotfix/financial_quarterly.parquet",
                    "sha256:hotfix",
                    "provider-v1",
                    "hotfix",
                    1,
                ),
                FinancialReportingSource(
                    "announcement_archive_fin_abstract",
                    "financial_abstract",
                    "announcement_archive|sqlite_table|fin_abstract",
                    archive_path,
                    "payload/canonical/info_archive.db",
                    "sha256:archive",
                    "ods-base-v1.2",
                    "independent",
                    1,
                    "fin_abstract",
                ),
            )
            inventory = FinancialReportingSourceInventory(sources=sources, empty_modeled_sources=())
            resolver = FinancialCompanyResolver(
                [
                    FinancialCompanyIdentity(
                        "000001",
                        "security-1",
                        "company-1",
                        datetime(1991, 1, 1, tzinfo=timezone.utc),
                        None,
                    ),
                    FinancialCompanyIdentity(
                        "000002",
                        "security-2",
                        "company-2",
                        datetime(1991, 1, 1, tzinfo=timezone.utc),
                        None,
                    ),
                ]
            )

            audit = audit_financial_native_sources(
                inventory,
                company_resolver=resolver,
                knowledge_cutoff=datetime(2026, 8, 1, tzinfo=timezone.utc),
                batch_size=1,
            )

            self.assertEqual(
                audit.report["candidate_size_calibration"],
                {
                    "dwd_financial_report_rows": 4,
                    "dwd_financial_fact_rows": 39,
                    "dwd_financial_concept_count": 36,
                    "quality_exception_rows": 1,
                },
            )
            self.assertEqual(
                audit.report["quality_exceptions"],
                {"BEFORE_FIRST_LISTED_COMPANY_IDENTITY": 1},
            )
            self.assertEqual(audit.report["quarterly_overlay"]["hotfix_fill_rows"], 1)

    def test_company_resolver_accepts_multiple_same_company_security_versions(self) -> None:
        resolver = financial_company_resolver_from_listed_company_rows(
            [
                {
                    "security_id": "security-1",
                    "company_id": "company-1",
                    "effective_from": datetime(1999, 1, 1, tzinfo=timezone.utc),
                    "effective_to": datetime(2010, 1, 1, tzinfo=timezone.utc),
                    "knowledge_from": datetime(2026, 7, 31, tzinfo=timezone.utc),
                },
                {
                    "security_id": "security-1",
                    "company_id": "company-1",
                    "effective_from": datetime(2010, 1, 1, tzinfo=timezone.utc),
                    "effective_to": None,
                    "knowledge_from": datetime(2026, 8, 1, tzinfo=timezone.utc),
                },
            ],
            [
                {
                    "security_id": "security-1",
                    "identifier_namespace": "CN_STOCK_CODE:SSE",
                    "identifier_value": "600000",
                    "effective_from": datetime(1999, 1, 1, tzinfo=timezone.utc),
                    "effective_to": None,
                    "knowledge_from": datetime(2026, 7, 30, tzinfo=timezone.utc),
                }
            ],
        )
        resolved = resolver.resolve(
            "600000",
            period_end=datetime(2025, 12, 31, tzinfo=timezone.utc).date(),
            published_at=None,
        )
        self.assertEqual(resolved.company_id, "company-1")

    def test_company_resolver_does_not_bridge_a_later_code_backward(self) -> None:
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000022",
                    security_id="security-old",
                    company_id="company-1",
                    effective_from=datetime(1993, 5, 5, tzinfo=timezone.utc),
                    effective_to=datetime(2018, 12, 26, tzinfo=timezone.utc),
                ),
                FinancialCompanyIdentity(
                    stock_code="001872",
                    security_id="security-new",
                    company_id="company-1",
                    effective_from=datetime(2018, 12, 26, tzinfo=timezone.utc),
                    effective_to=None,
                ),
            ]
        )

        resolved, reason = resolver.resolve_for_report(
            "001872", period_end=datetime(2010, 12, 31, tzinfo=timezone.utc).date()
        )

        self.assertIsNone(resolved)
        self.assertEqual(reason, "BEFORE_FIRST_LISTED_COMPANY_IDENTITY")
        outside, reason = resolver.resolve_for_report(
            "001872", period_end=datetime(1990, 12, 31, tzinfo=timezone.utc).date()
        )
        self.assertIsNone(outside)
        self.assertEqual(reason, "BEFORE_FIRST_LISTED_COMPANY_IDENTITY")

    def test_company_resolver_classifies_report_between_listing_intervals(self) -> None:
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000765",
                    security_id="security-old",
                    company_id="company-1",
                    effective_from=datetime(1997, 11, 3, tzinfo=timezone.utc),
                    effective_to=datetime(2005, 7, 5, tzinfo=timezone.utc),
                ),
                FinancialCompanyIdentity(
                    stock_code="001267",
                    security_id="security-new",
                    company_id="company-1",
                    effective_from=datetime(2021, 11, 17, tzinfo=timezone.utc),
                    effective_to=None,
                ),
            ]
        )

        resolved, reason = resolver.resolve_for_report(
            "001267", period_end=datetime(2011, 3, 31, tzinfo=timezone.utc).date()
        )

        self.assertIsNone(resolved)
        self.assertEqual(reason, "BEFORE_FIRST_LISTED_COMPANY_IDENTITY")

    def test_company_resolver_classifies_an_uncovered_code(self) -> None:
        resolver = FinancialCompanyResolver([])

        resolved, reason = resolver.resolve_for_report(
            "920258", period_end=datetime(2026, 7, 31, tzinfo=timezone.utc).date()
        )

        self.assertIsNone(resolved)
        self.assertEqual(reason, "NO_LISTED_COMPANY_IDENTITY")

    def test_reporting_batch_materializes_native_rows_and_quality_counts(self) -> None:
        source = FinancialReportingSource(
            source_id="canonical_financial_quarterly",
            source_kind="quarterly_projection",
            ledger_identity="financial_quarterly",
            path=Path("/fixture/canonical.parquet"),
            relative_path="payload/canonical/financial_quarterly.parquet",
            source_sha256="sha256:" + "a" * 64,
            source_revision="ods-base-v1.2",
            precedence="independent",
        )
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1991, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                )
            ]
        )

        rows_by_year, concepts, counts, exceptions = _reporting_batch_rows(
            [
                {
                    "stock_code": "000001",
                    "report_date": datetime(1990, 12, 31),
                    "roe": 1.0,
                    "revenue": 2.0,
                    "net_profit": 3.0,
                    "revenue_growth_yoy": 4.0,
                    "profit_growth_yoy": 5.0,
                }
            ],
            source=source,
            resolver=resolver,
            knowledge_cutoff=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(counts["dwd_financial_report"], 0)
        self.assertEqual(counts["dwd_financial_fact"], 0)
        self.assertEqual(counts["dwd_financial_concept"], 0)
        self.assertEqual(len(concepts), 0)
        self.assertTrue(all(not partitions for partitions in rows_by_year.values()))
        self.assertEqual(exceptions, {"BEFORE_FIRST_LISTED_COMPANY_IDENTITY": 1})

    def test_reporting_batches_finalize_evidence_backed_concept_revisions(self) -> None:
        source = FinancialReportingSource(
            source_id="canonical_financial_quarterly",
            source_kind="quarterly_projection",
            ledger_identity="financial_quarterly",
            path=Path("/fixture/canonical.parquet"),
            relative_path="payload/canonical/financial_quarterly.parquet",
            source_sha256="sha256:" + "a" * 64,
            source_revision="ods-base-v1.2",
            precedence="independent",
        )
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1980, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                    knowledge_from=datetime(2026, 8, 3, tzinfo=timezone.utc),
                )
            ]
        )

        rows_by_year, concepts, _counts, exceptions = _reporting_batch_rows(
            [
                {
                    "stock_code": "000001",
                    "report_date": datetime(1998, 12, 31),
                    "acquired_at": "2026-08-01T00:00:00+00:00",
                    "revenue": 2.0,
                },
                {
                    "stock_code": "000001",
                    "report_date": datetime(1990, 12, 31),
                    "acquired_at": "2026-08-02T00:00:00+00:00",
                    "revenue": 1.0,
                },
            ],
            source=source,
            resolver=resolver,
            knowledge_cutoff=datetime(2026, 8, 3, tzinfo=timezone.utc),
        )

        self.assertEqual(exceptions, {})
        self.assertEqual(len(concepts), 10)
        self.assertEqual(
            {concept["knowledge_from"] for concept in concepts},
            {
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                datetime(2026, 8, 2, tzinfo=timezone.utc),
            },
        )
        concepts_by_id: dict[str, list[dict[str, object]]] = defaultdict(list)
        for concept in concepts:
            concepts_by_id[concept["financial_concept_id"]].append(concept)
        self.assertEqual(len(concepts_by_id), 5)
        for revisions in concepts_by_id.values():
            self.assertEqual(
                sorted(
                    [
                        (revision["effective_from"], revision["knowledge_from"])
                        for revision in revisions
                    ],
                    key=lambda coordinate: coordinate[1],
                ),
                [
                    (
                        datetime(1998, 12, 30, 16, 0, tzinfo=timezone.utc),
                        datetime(2026, 8, 1, tzinfo=timezone.utc),
                    ),
                    (
                        datetime(1990, 12, 30, 16, 0, tzinfo=timezone.utc),
                        datetime(2026, 8, 2, tzinfo=timezone.utc),
                    ),
                ],
            )
        facts = [
            fact
            for partitions in rows_by_year["dwd_financial_fact"].values()
            for fact in partitions
        ]
        reports = [
            report
            for partitions in rows_by_year["dwd_financial_report"].values()
            for report in partitions
        ]
        schemas = financial_reporting_schemas()
        second_rows_by_year, second_concepts, _second_counts, second_exceptions = (
            _reporting_batch_rows(
                [
                    {
                        "stock_code": "000001",
                        "report_date": datetime(2005, 12, 31),
                        "acquired_at": "2026-07-31T00:00:00+00:00",
                        "revenue": 3.0,
                    }
                ],
                source=source,
                resolver=resolver,
                knowledge_cutoff=datetime(2026, 8, 3, tzinfo=timezone.utc),
            )
        )
        self.assertEqual(second_exceptions, {})
        second_facts = [
            fact
            for partitions in second_rows_by_year["dwd_financial_fact"].values()
            for fact in partitions
        ]
        second_reports = [
            report
            for partitions in second_rows_by_year["dwd_financial_report"].values()
            for report in partitions
        ]
        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            _write_batch(
                work=work,
                batch_id="00-000000",
                rows_by_year=rows_by_year,
                concepts=concepts,
                schemas=schemas,
            )
            _write_batch(
                work=work,
                batch_id="00-000001",
                rows_by_year=second_rows_by_year,
                concepts=second_concepts,
                schemas=schemas,
            )
            self.assertEqual(_finalize_concepts(work, {"concept_finalized": False}, schemas), 15)
            finalized_concepts = pq.read_table(
                work / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
            ).to_pylist()

        evidence_coordinates = {
            (
                concept["financial_concept_id"],
                concept["effective_from"],
                concept["knowledge_from"],
                concept["record_source"],
                concept["source_record_id"],
                concept["source_revision"],
                concept["source_artifact_sha256"],
            )
            for concept in [*concepts, *second_concepts]
        }
        finalized_by_id: dict[str, list[dict[str, object]]] = defaultdict(list)
        for concept in finalized_concepts:
            finalized_by_id[concept["financial_concept_id"]].append(concept)
            self.assertIn(
                (
                    concept["financial_concept_id"],
                    concept["effective_from"],
                    concept["knowledge_from"],
                    concept["record_source"],
                    concept["source_record_id"],
                    concept["source_revision"],
                    concept["source_artifact_sha256"],
                ),
                evidence_coordinates,
            )
        self.assertEqual(len(finalized_by_id), 5)
        for revisions in finalized_by_id.values():
            revisions.sort(key=lambda row: row["knowledge_from"])
            self.assertEqual(
                [
                    (
                        revision["effective_from"],
                        revision["knowledge_from"],
                        revision["knowledge_to"],
                    )
                    for revision in revisions
                ],
                [
                    (
                        datetime(2005, 12, 30, 16, 0, tzinfo=timezone.utc),
                        datetime(2026, 7, 31, tzinfo=timezone.utc),
                        datetime(2026, 8, 1, tzinfo=timezone.utc),
                    ),
                    (
                        datetime(1998, 12, 30, 16, 0, tzinfo=timezone.utc),
                        datetime(2026, 8, 1, tzinfo=timezone.utc),
                        datetime(2026, 8, 2, tzinfo=timezone.utc),
                    ),
                    (
                        datetime(1990, 12, 30, 16, 0, tzinfo=timezone.utc),
                        datetime(2026, 8, 2, tzinfo=timezone.utc),
                        None,
                    ),
                ],
            )
        facts.extend(second_facts)
        reports.extend(second_reports)
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.register(
                "financial_facts",
                pa.Table.from_pylist(facts, schema=schemas["dwd_financial_fact"]),
            )
            connection.register(
                "financial_reports",
                pa.Table.from_pylist(reports, schema=schemas["dwd_financial_report"]),
            )
            connection.register(
                "financial_concepts",
                pa.Table.from_pylist(finalized_concepts, schema=schemas["dwd_financial_concept"]),
            )
            contracts = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")
            foreign_keys = _foreign_key_metrics(
                connection,
                child_source="financial_facts",
                child_contract=contracts["dwd_financial_fact"],
                parent_sources={
                    "dwd_financial_report": "financial_reports",
                    "dwd_financial_concept": "financial_concepts",
                },
                parent_contracts=contracts,
            )
            concept_id = finalized_concepts[0]["financial_concept_id"]
            before_revision = connection.execute(
                """
                SELECT effective_from FROM financial_concepts
                WHERE financial_concept_id = ?
                  AND knowledge_from <= TIMESTAMPTZ '2026-08-01 12:00:00+00:00'
                  AND (knowledge_to IS NULL
                    OR knowledge_to > TIMESTAMPTZ '2026-08-01 12:00:00+00:00')
                """,
                [concept_id],
            ).fetchone()
            after_revision = connection.execute(
                """
                SELECT effective_from FROM financial_concepts
                WHERE financial_concept_id = ?
                  AND knowledge_from <= TIMESTAMPTZ '2026-08-02 00:00:00+00:00'
                  AND (knowledge_to IS NULL
                    OR knowledge_to > TIMESTAMPTZ '2026-08-02 00:00:00+00:00')
                """,
                [concept_id],
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            [
                (item["parent_table"], item["temporal_or_future_parent_rows"])
                for item in foreign_keys
            ],
            [("dwd_financial_report", 0), ("dwd_financial_concept", 0)],
        )
        self.assertEqual(
            before_revision,
            (datetime(1998, 12, 30, 16, 0, tzinfo=timezone.utc),),
        )
        self.assertEqual(
            after_revision,
            (datetime(1990, 12, 30, 16, 0, tzinfo=timezone.utc),),
        )

    def test_writes_partitioned_rows_and_runs_external_key_audit(self) -> None:
        row = {
            **_envelope(
                {
                    "result": {
                        "data": {
                            "report_list": {
                                "20251231": {
                                    "rType": "consolidated",
                                    "rCurrency": "CNY",
                                    "data": [
                                        {"item_field": "CASH", "item_value": "12.5"},
                                        {"item_field": "DEBT", "item_value": ""},
                                    ],
                                }
                            }
                        }
                    }
                }
            ),
            "security_id": "000001",
            "stock_code": "000001",
            "statement_type": "balance_sheet",
            "source": "test.finance",
            "source_revision": "test-r1",
            "acquired_at": "2026-07-31T23:12:41+08:00",
        }
        source = FinancialStatementSource(
            "financial_statements",
            Path("/fixture/full.parquet"),
            "payload/full.parquet",
            "sha256:" + "a" * 64,
            "",
            "parent",
        )
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1991, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                )
            ]
        )
        rows_by_year, concepts, counts = _batch_rows([row], source=source, resolver=resolver)
        schemas = financial_reporting_schemas()
        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            _write_batch(
                work=work,
                batch_id="00-000000",
                rows_by_year=rows_by_year,
                concepts=concepts,
                schemas=schemas,
            )
            concept_count = _finalize_concepts(work, {"concept_finalized": False}, schemas)
            row_counts, audit = _audit_financial_candidate(
                work=work,
                schemas=schemas,
                expected_reports=counts["dwd_financial_report"],
                expected_facts=counts["dwd_financial_fact"],
            )
            concept_path = (
                work / "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
            )
            invalid_concepts = pq.read_table(concept_path).to_pylist()
            original_effective_from = invalid_concepts[0]["effective_from"]
            invalid_concepts[0]["effective_from"] = datetime(2026, 12, 30, 16, tzinfo=timezone.utc)
            pq.write_table(
                pa.Table.from_pylist(invalid_concepts, schema=schemas["dwd_financial_concept"]),
                concept_path,
            )
            with self.assertRaisesRegex(FinancialReportingBuildError, "temporal FK"):
                _audit_financial_candidate(
                    work=work,
                    schemas=schemas,
                    expected_reports=counts["dwd_financial_report"],
                    expected_facts=counts["dwd_financial_fact"],
                )
            invalid_concepts[0]["effective_from"] = original_effective_from
            invalid_concepts[0]["knowledge_to"] = datetime(2026, 8, 2, tzinfo=timezone.utc)
            pq.write_table(
                pa.Table.from_pylist(invalid_concepts, schema=schemas["dwd_financial_concept"]),
                concept_path,
            )
            with self.assertRaisesRegex(FinancialReportingBuildError, "interval"):
                _audit_financial_candidate(
                    work=work,
                    schemas=schemas,
                    expected_reports=counts["dwd_financial_report"],
                    expected_facts=counts["dwd_financial_fact"],
                )
        self.assertEqual(concept_count, 2)
        self.assertEqual(
            row_counts,
            {
                "dwd_financial_report": 1,
                "dwd_financial_concept": 2,
                "dwd_financial_fact": 2,
            },
        )
        self.assertEqual(
            audit["duplicate_business_keys"],
            {
                "dwd_financial_report": 0,
                "dwd_financial_concept": 0,
                "dwd_financial_fact": 0,
            },
        )
        self.assertEqual(
            audit["temporal_or_future_foreign_keys"],
            {"financial_report_id": 0, "financial_concept_id": 0},
        )
        self.assertEqual(
            audit["concept_revision_evidence"],
            {
                "unsupported_rows": 0,
                "missing_revision_rows": 0,
                "invalid_evidence_rows": 0,
                "source_coordinate_violations": 0,
                "semantic_conflict_rows": 0,
                "parallel_state_rows": 0,
                "boundary_violations": 0,
            },
        )
        self.assertEqual(audit["audit_policy"], "duckdb_year_partitioned_external_audit_v4")
        self.assertEqual(audit["year_partitions"]["2025"]["report_rows"], 1)
        self.assertEqual(audit["year_partitions"]["2025"]["fact_rows"], 2)


if __name__ == "__main__":
    unittest.main()
