import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path

from quanthus.cli import main
from quanthus.corporate_action_corpus import (
    CorporateActionCorpusError,
    CorporateActionCorpusPaths,
    audit_corporate_action_corpus,
)
from quanthus.corporate_action_normalization import (
    CashUnit,
    FactorCrossCheckStatus,
    NormalizationBasis,
    load_corporate_action_normalization_policy,
)

P1_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)

if P1_DEPENDENCIES_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq


def observed_factor(
    *, prior_factor: float, prior_close: float, cash_per_share: float, share_ratio: float
) -> float:
    return prior_factor * (1 + share_ratio) * prior_close / (prior_close - cash_per_share)


@unittest.skipUnless(P1_DEPENDENCIES_AVAILABLE, "P1 dependencies are not installed")
class CorporateActionCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()
        self.project_root = Path(__file__).parents[2]
        self.policy = load_corporate_action_normalization_policy(
            self.project_root / "config" / "p1-corporate-action-normalization.v1.json"
        )
        self.v2_policy = load_corporate_action_normalization_policy()

    def write_snapshot(
        self,
        *,
        raw_rows: list[dict[str, object]],
        qfq_rows: list[dict[str, object]],
        factor_rows: list[dict[str, object]],
    ) -> None:
        datasets = {
            "daily_ohlcv_none": ("datasets/raw.parquet", raw_rows),
            "daily_ohlcv_qfq": ("datasets/qfq.parquet", qfq_rows),
            "adjustment_factors": ("datasets/factors.parquet", factor_rows),
        }
        manifest_datasets = []
        for dataset, (relative_path, rows) in datasets.items():
            path = self.snapshot / relative_path
            path.parent.mkdir(exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows), path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_datasets.append(
                {
                    "dataset": dataset,
                    "partitions": [relative_path],
                    "content_checksums": {relative_path: f"sha256:{digest}"},
                }
            )
        (self.snapshot / "snapshot.json").write_text(
            json.dumps(
                {
                    "snapshot": {"snapshot_id": "p1-test-corporate-actions-v1"},
                    "datasets": manifest_datasets,
                }
            ),
            encoding="utf-8",
        )

    def write_actions(self, rows: list[dict[str, object]]) -> Path:
        path = self.root / "corporate-actions.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        return path

    def write_eastmoney_raw(
        self,
        rows: list[dict[str, object]],
        *,
        security_code: str = "000001",
    ) -> Path:
        revision = self.root / "eastmoney-revision"
        raw_dir = revision / "em_raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (revision / "META.json").write_text(
            json.dumps({"revision": "test-eastmoney-implementation-v1"}),
            encoding="utf-8",
        )
        (raw_dir / f"{security_code}.json").write_text(
            json.dumps(
                {
                    "code": security_code,
                    "rows": rows,
                    "error": None,
                    "fetched_at": "2026-07-29T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        return raw_dir

    def eastmoney_action(self, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "SECURITY_CODE": "000001",
            "EX_DIVIDEND_DATE": "2020-05-28 00:00:00",
            "NOTICE_DATE": "2020-05-22 00:00:00",
            "PRETAX_BONUS_RMB": 2.18,
            "BONUS_RATIO": None,
            "IT_RATIO": None,
            "ASSIGN_PROGRESS": "实施分配",
            "IMPL_PLAN_PROFILE": "10派2.18元(含税,扣税后1.962元)",
        }
        row.update(overrides)
        return row

    def base_action(self, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "action_id": "action-001",
            "stock_code": "000001",
            "ex_date": date(2020, 5, 28),
            "announce_date": date(2020, 5, 22),
            "cash_per_share": 0.218,
            "bonus_shares": None,
            "transfer_shares": None,
            "progress": "实施",
            "source": "test.corporate_actions",
            "source_row_key": "row:action-001",
        }
        row.update(overrides)
        return row

    def audit(self, actions: list[dict[str, object]]):
        before = 0.8
        self.write_snapshot(
            raw_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            qfq_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 10.4}],
            factor_rows=[
                {
                    "stock_code": "000001",
                    "effective_date": date(2019, 5, 28),
                    "fore_adjust_factor": before,
                    "knowledge_cutoff": date(2019, 5, 20),
                },
                {
                    "stock_code": "000001",
                    "effective_date": date(2020, 5, 28),
                    "fore_adjust_factor": observed_factor(
                        prior_factor=before,
                        prior_close=13.0,
                        cash_per_share=0.218,
                        share_ratio=0,
                    ),
                    "knowledge_cutoff": date(2020, 5, 22),
                },
            ],
        )
        return audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions(actions),
            ),
            self.policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

    def test_real_shape_rows_reconcile_without_execution_authority(self) -> None:
        report = self.audit(
            [
                self.base_action(),
                self.base_action(
                    action_id="action-proposal",
                    progress="预案",
                    source_row_key="row:proposal",
                ),
                self.base_action(
                    action_id="action-outside",
                    ex_date=date(2018, 5, 28),
                    announce_date=date(2018, 5, 22),
                    source_row_key="row:outside",
                ),
                self.base_action(
                    action_id="action-missing-date",
                    ex_date=None,
                    source_row_key="row:missing-date",
                ),
            ]
        )

        self.assertEqual(report.source_row_count, 4)
        self.assertEqual(report.implemented_row_count, 3)
        self.assertEqual(report.excluded_not_implemented_count, 1)
        self.assertEqual(report.excluded_out_of_scope_count, 1)
        self.assertEqual(len(report.normalized), 1)
        self.assertEqual(report.normalized[0].cash_unit, CashUnit.PER_SHARE)
        self.assertEqual(report.source_exceptions[0].reason_code, "missing_effective_date")
        self.assertFalse(report.ready_for_snapshot)
        self.assertFalse(report.execution_authorized)

    def test_clean_corpus_is_ready_for_snapshot(self) -> None:
        first = self.audit([self.base_action()])
        second = self.audit([self.base_action()])

        self.assertTrue(first.ready_for_snapshot)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.digest, second.digest)

    def test_first_factor_uses_prior_qfq_to_raw_ratio(self) -> None:
        before = 0.8
        self.write_snapshot(
            raw_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            qfq_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 10.4}],
            factor_rows=[
                {
                    "stock_code": "000001",
                    "effective_date": date(2020, 5, 28),
                    "fore_adjust_factor": observed_factor(
                        prior_factor=before,
                        prior_close=13.0,
                        cash_per_share=0.228,
                        share_ratio=0.6,
                    ),
                    "knowledge_cutoff": date(2020, 5, 22),
                }
            ],
        )
        action = self.base_action(cash_per_share=2.28, bonus_shares=6.0)
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([action]),
            ),
            self.policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertTrue(report.ready_for_snapshot)
        self.assertEqual(report.normalized[0].cash_unit, CashUnit.PER_TEN_SHARES)
        self.assertEqual(report.normalized[0].cross_check_baseline.value, "prior_qfq_ratio")

    def test_missing_current_factor_is_an_explicit_normalization_exception(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            qfq_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 10.4}],
            factor_rows=[
                {
                    "stock_code": "000001",
                    "effective_date": date(2019, 5, 28),
                    "fore_adjust_factor": 0.8,
                    "knowledge_cutoff": date(2019, 5, 20),
                }
            ],
        )
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action()]),
            ),
            self.policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertEqual(
            [item.reason_code for item in report.normalization_exceptions],
            ["missing_cross_check"],
        )
        self.assertFalse(report.ready_for_snapshot)

    def test_tampered_snapshot_partition_fails_before_audit(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            qfq_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 10.4}],
            factor_rows=[
                {
                    "stock_code": "000001",
                    "effective_date": date(2020, 5, 28),
                    "fore_adjust_factor": 0.8,
                    "knowledge_cutoff": date(2020, 5, 22),
                }
            ],
        )
        factors = self.snapshot / "datasets" / "factors.parquet"
        factors.write_bytes(factors.read_bytes() + b"tampered")

        with self.assertRaisesRegex(CorporateActionCorpusError, "checksum mismatch"):
            audit_corporate_action_corpus(
                CorporateActionCorpusPaths(
                    snapshot_dir=self.snapshot,
                    corporate_actions=self.write_actions([self.base_action()]),
                ),
                self.policy,
                scope_start=date(2020, 1, 1),
                scope_end=date(2020, 12, 31),
            )

    def test_cli_writes_full_report_and_prints_stable_summary(self) -> None:
        expected = self.audit([self.base_action()])
        report_output = self.root / "audit" / "corporate-actions.json"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "audit-corporate-actions",
                    "--snapshot-dir",
                    str(self.snapshot),
                    "--corporate-actions",
                    str(self.root / "corporate-actions.parquet"),
                    "--policy",
                    str(self.project_root / "config" / "p1-corporate-action-normalization.v1.json"),
                    "--scope-start",
                    "2020-01-01",
                    "--scope-end",
                    "2020-12-31",
                    "--report-output",
                    str(report_output),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(report_output.read_text()), expected.to_dict())
        self.assertEqual(json.loads(stdout.getvalue()), expected.summary_dict())

    def test_v2_authority_normalizes_without_factor_and_versions_exceptions(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw([self.eastmoney_action()])
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action()]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertTrue(report.ready_for_snapshot)
        self.assertEqual(len(report.normalized), 1)
        self.assertEqual(
            report.normalized[0].normalization_basis,
            NormalizationBasis.EASTMONEY_IMPLEMENTATION_PLAN,
        )
        self.assertEqual(
            report.normalized[0].factor_cross_check_status,
            FactorCrossCheckStatus.UNAVAILABLE,
        )
        self.assertEqual(str(report.normalized[0].cash_dividend_per_share), "0.21800000")
        self.assertEqual(report.normalized[0].cash_unit, CashUnit.PER_SHARE)
        self.assertIsNotNone(report.exception_ledger)
        self.assertEqual(report.exception_ledger.entries, ())

    def test_v2_exact_candidates_require_one_source_field_match(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw(
            [
                self.eastmoney_action(
                    PRETAX_BONUS_RMB=5.6,
                    IMPL_PLAN_PROFILE="10派5.60元(含税)",
                ),
                self.eastmoney_action(),
            ]
        )
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action()]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertEqual(len(report.normalized), 1)
        self.assertTrue(report.normalized[0].authority_source_row_ref.endswith("#rows[1]"))

    def test_v2_exact_source_match_outranks_precision_boundary_match(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw(
            [
                self.eastmoney_action(
                    PRETAX_BONUS_RMB=0.5,
                    IMPL_PLAN_PROFILE="10派0.50元(含税)",
                ),
                self.eastmoney_action(
                    PRETAX_BONUS_RMB=1.0,
                    IMPL_PLAN_PROFILE="10派1.00元(含税)",
                ),
            ]
        )
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action(cash_per_share=0.1)]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertTrue(report.normalized[0].authority_source_row_ref.endswith("#rows[1]"))
        self.assertEqual(report.normalized[0].cash_dividend_per_share, Decimal("0.10000000"))

    def test_v2_ambiguous_or_inconsistent_authority_is_versioned_fail_closed(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw([self.eastmoney_action(), self.eastmoney_action()])
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action()]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertEqual(
            [item.reason_code for item in report.source_exceptions],
            ["authoritative_evidence_ambiguous"],
        )
        self.assertEqual(len(report.exception_ledger.entries), 1)
        self.assertTrue(report.exception_ledger.entries[0].blocks("000001", date(2020, 5, 28)))
        self.assertFalse(report.exception_ledger.entries[0].blocks("000001", date(2020, 5, 27)))
        self.assertFalse(report.ready_for_snapshot, "a corpus with no normalized row stays blocked")

        raw_dir = self.write_eastmoney_raw(
            [
                self.eastmoney_action(
                    PRETAX_BONUS_RMB=2.18,
                    IMPL_PLAN_PROFILE="10派1.80元(含税)",
                )
            ]
        )
        inconsistent = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action()]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )
        self.assertEqual(
            [item.reason_code for item in inconsistent.source_exceptions],
            ["authoritative_profile_mismatch"],
        )
        self.assertEqual(len(inconsistent.source_exceptions[0].authority_source_row_refs), 1)
        self.assertEqual(
            len(inconsistent.exception_ledger.entries[0].authority_evidence_digests), 1
        )

    def test_v2_source_precision_rule_accepts_authority_rounding_only(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw(
            [
                self.eastmoney_action(
                    PRETAX_BONUS_RMB=None,
                    IT_RATIO=9.940741,
                    IMPL_PLAN_PROFILE="10转9.940741",
                )
            ]
        )
        action = self.base_action(cash_per_share=None, transfer_shares=9.94074)
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([action]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertEqual(str(report.normalized[0].bonus_share_ratio), "0.99407410")

    def test_v2_business_tolerance_absorbs_immaterial_cash_difference(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw(
            [
                self.eastmoney_action(
                    PRETAX_BONUS_RMB=0.9544,
                    IMPL_PLAN_PROFILE="10派0.9544元(含税)",
                )
            ]
        )
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action(cash_per_share=0.0954445)]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertEqual(report.normalized[0].cash_dividend_per_share, Decimal("0.09544000"))

    def test_v2_notice_must_precede_effective_date(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw(
            [self.eastmoney_action(NOTICE_DATE="2020-05-28 00:00:00")]
        )
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action()]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertEqual(
            [item.reason_code for item in report.source_exceptions],
            ["authoritative_notice_not_point_in_time"],
        )

    def test_v2_missing_effective_date_blocks_from_announcement(self) -> None:
        self.write_snapshot(
            raw_rows=[{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            qfq_rows=[{"stock_code": "999999", "date": date(2020, 1, 1), "close": 1.0}],
            factor_rows=[
                {
                    "stock_code": "999999",
                    "effective_date": date(2020, 1, 2),
                    "fore_adjust_factor": 1.0,
                    "knowledge_cutoff": date(2020, 1, 1),
                }
            ],
        )
        raw_dir = self.write_eastmoney_raw([self.eastmoney_action()])
        missing = self.base_action(
            action_id="action-missing-date",
            ex_date=None,
            source_row_key="row:missing-date",
        )
        report = audit_corporate_action_corpus(
            CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.write_actions([self.base_action(), missing]),
                eastmoney_raw_dir=raw_dir,
            ),
            self.v2_policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
        )

        self.assertTrue(report.ready_for_snapshot)
        self.assertEqual(len(report.exception_ledger.entries), 1)
        entry = report.exception_ledger.entries[0]
        self.assertEqual(entry.blocked_from, date(2020, 5, 22))
        self.assertTrue(entry.blocks("000001", date(2020, 5, 22)))


if __name__ == "__main__":
    unittest.main()
