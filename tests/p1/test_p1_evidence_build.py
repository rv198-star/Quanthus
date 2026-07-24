import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

P1_STACK_AVAILABLE = all(
    __import__("importlib").util.find_spec(module) is not None for module in ("pyarrow",)
)

if P1_STACK_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.data_foundation import SnapshotCatalog
    from quanthus.p1_acceptance import validate_p1_acceptance_evidence
    from quanthus.p1_evidence_build import build_p1_acceptance_evidence
    from quanthus.snapshot_publish import load_snapshot_plan, publish_snapshot


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class P1EvidenceBuildTests(unittest.TestCase):
    def test_rebuilds_replay_evidence_but_preserves_the_c1_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / "sources"
            sources.mkdir()
            daily_none = sources / "daily_none.parquet"
            daily_qfq = sources / "daily_qfq.parquet"
            factors = sources / "factors.parquet"
            suspensions = sources / "suspensions.parquet"
            boundaries = sources / "boundaries.parquet"

            d1_dates = [date(2020, 1, 1) + timedelta(days=index) for index in range(10)]
            d1_codes = [f"{index:06d}" for index in range(10)]
            daily_rows: list[dict[str, object]] = []
            d1_cases: list[dict[str, object]] = []
            for date_index, market_date in enumerate(d1_dates):
                for code_index, stock_code in enumerate(d1_codes):
                    close = float(10 + date_index + code_index)
                    row = {
                        "stock_code": stock_code,
                        "date": market_date,
                        "open": close,
                        "high": close + 1,
                        "low": close - 1,
                        "close": close,
                        "volume": float(100 + code_index),
                        "amount": float(1000 + code_index),
                    }
                    daily_rows.append(row)
                    d1_cases.append(
                        {
                            "market_date": market_date.isoformat(),
                            "security_id": stock_code,
                            "reference_id": f"ref:{stock_code}:{market_date.isoformat()}",
                            "ref": {
                                field: row[field]
                                for field in row
                                if field not in {"stock_code", "date"}
                            },
                        }
                    )
            ex_date = date(2021, 1, 2)
            daily_rows.extend(
                [
                    {
                        "stock_code": "000001",
                        "date": date(2021, 1, 1),
                        "open": 100.0,
                        "high": 100.0,
                        "low": 100.0,
                        "close": 100.0,
                        "volume": 100.0,
                        "amount": 1000.0,
                    },
                    {
                        "stock_code": "000001",
                        "date": ex_date,
                        "open": 110.0,
                        "high": 110.0,
                        "low": 110.0,
                        "close": 110.0,
                        "volume": 100.0,
                        "amount": 1000.0,
                    },
                ]
            )
            pq.write_table(pa.Table.from_pylist(daily_rows), daily_none)
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {"stock_code": "000001", "date": date(2021, 1, 1), "close": 100.0},
                        {"stock_code": "000001", "date": ex_date, "close": 105.0},
                    ]
                ),
                daily_qfq,
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "000001",
                            "effective_date": ex_date,
                            "adjust_factor": 1.1,
                            "knowledge_cutoff": date(2021, 1, 1),
                            "visibility_origin": "corp_actions.announce_date",
                            "source_revision": "factor-v1",
                        }
                    ]
                ),
                factors,
            )
            d2_cases: list[dict[str, object]] = []
            suspension_rows: list[dict[str, object]] = []
            coverage_rows: list[dict[str, object]] = []
            for index in range(20):
                stock_code = f"1{index:05d}"
                market_date = date(2020, 2, 1) + timedelta(days=index)
                suspension_id = f"suspension-{index}"
                d2_cases.append(
                    {
                        "market_date": market_date.isoformat(),
                        "security_id": stock_code,
                        "case_type": "suspension",
                        "status": "pass",
                        "reference_id": f"suspension:{suspension_id}",
                    }
                )
                suspension_rows.append(
                    {
                        "suspension_id": suspension_id,
                        "stock_code": stock_code,
                        "suspend_start": market_date,
                        "suspend_end": market_date,
                        "knowledge_cutoff": market_date,
                    }
                )
                coverage_rows.append(
                    {
                        "stock_code": stock_code,
                        "market_date": market_date,
                        "coverage_status": "suspended",
                        "bar_present": False,
                        "is_executable": False,
                    }
                )
            pq.write_table(pa.Table.from_pylist(suspension_rows), suspensions)
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "000001",
                            "market_date": ex_date,
                            "exception_type": "terminal_boundary",
                        }
                    ]
                ),
                boundaries,
            )
            coverage = root / "coverage.parquet"
            pq.write_table(pa.Table.from_pylist(coverage_rows), coverage)

            snapshot_id = "p1-test-final-v1"
            plan_path = root / "snapshot-plan.json"
            source_files = {
                "daily_ohlcv_none": daily_none,
                "daily_ohlcv_qfq": daily_qfq,
                "adjustment_factors": factors,
                "suspensions": suspensions,
                "delist_day_boundary_exceptions": boundaries,
            }
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "snapshot": {
                            "market_date": "2021-01-02",
                            "knowledge_cutoff": "2021-01-10T00:00:00+00:00",
                            "source_revision": "test-v1",
                            "snapshot_id": snapshot_id,
                        },
                        "universe_policy_version": "test-universe-v1",
                        "datasets": [
                            {
                                "dataset": dataset,
                                "version": "v1",
                                "path": str(source),
                                "source_revisions": {"test": "v1"},
                                "knowledge_cutoff": "2021-01-10T00:00:00+00:00",
                                "created_at": "2021-01-10T00:00:00+00:00",
                            }
                            for dataset, source in source_files.items()
                        ],
                    }
                ),
                encoding="utf-8",
            )
            snapshot = publish_snapshot(
                SnapshotCatalog(root / "snapshots"), load_snapshot_plan(plan_path)
            )

            d1_pack = root / "d1-pack.json"
            d1_pack.write_text(json.dumps(d1_cases), encoding="utf-8")
            d3_seed = root / "d3-seed.json"
            d3_seed.write_text(
                json.dumps(
                    {"cases": [{"stock_code": "000001", "effective_date": ex_date.isoformat()}]}
                ),
                encoding="utf-8",
            )
            prior_evidence = root / "prior-evidence.json"
            prior_evidence.write_text(
                json.dumps(
                    {
                        "D2": {"cases": d2_cases},
                        "D3": {
                            "cases": [
                                {
                                    "ex_date": ex_date.isoformat(),
                                    "security_id": "000001",
                                    "status": "pass",
                                    "reference_id": "seed:000001:2021-01-02",
                                    "artificial_jump_detected": False,
                                    "raw_return_pct": 10.0,
                                    "qfq_return_pct": 5.0,
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            audit = root / "audit.json"
            audit.write_text(
                json.dumps(
                    {
                        "summary": {"fail": 0},
                        "checks": [
                            {
                                "check_id": "adjustment_factors.batch_dated_historical_visibility",
                                "status": "warn",
                                "count": 6910,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            coverage_metadata = root / "coverage.json"
            coverage_metadata.write_text(
                json.dumps(
                    {
                        "source_checksum": _sha256(daily_none),
                        "status_counts": {"suspended": 20},
                    }
                ),
                encoding="utf-8",
            )
            s1_report = root / "s1.json"
            s1_report.write_text(
                json.dumps(
                    {
                        "source_checksum": _sha256(daily_none),
                        "summary": {
                            "unexplained_missing_session_count": 0,
                            "invalid_observed_session_count": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            evidence = build_p1_acceptance_evidence(
                snapshot_dir=snapshot.location,
                coverage_path=coverage,
                coverage_metadata_path=coverage_metadata,
                data_audit_path=audit,
                s1_report_path=s1_report,
                prior_evidence_path=prior_evidence,
                d1_pack_path=d1_pack,
                d3_seed_path=d3_seed,
            )

            report = validate_p1_acceptance_evidence(evidence)
            checks = {check.check_id: check.passed for check in report.checks}
            self.assertEqual(report.snapshot_id, snapshot_id)
            self.assertFalse(checks["C1"])
            self.assertTrue(all(checks[check_id] for check_id in ("C2", "S1", "D1", "D2", "D3")))


if __name__ == "__main__":
    unittest.main()
