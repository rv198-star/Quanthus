import hashlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.backtest.benchmark import BenchmarkCurve, BenchmarkObservation
from quanthus.backtest.historical_bundle import load_historical_replay_bundle
from quanthus.backtest.historical_frames import HistoricalFrameReaderError
from quanthus.cli import _parser, main
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import (
    load_corporate_action_normalization_policy,
)
from quanthus.data_foundation import SnapshotCatalog
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.snapshot_publish import (
    SnapshotDatasetPlan,
    SnapshotPublicationPlan,
    publish_snapshot,
)
from quanthus.strategy_replay_ads import (
    StrategyReplayADSError,
    StrategyReplayADSPolicy,
    StrategyReplayADSScope,
    build_strategy_replay_ads,
    load_strategy_replay_ads,
    load_strategy_replay_ads_policy,
    load_strategy_replay_ads_reader,
)

P1_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _schema_sha256(path: Path) -> str:
    import pyarrow.parquet as pq

    schema = pq.ParquetFile(path).schema_arrow
    return f"sha256:{hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()}"


def _digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@unittest.skipUnless(P1_DEPENDENCIES_AVAILABLE, "P1 dependencies are not installed")
class StrategyReplayADSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.corporate_action_policy = load_corporate_action_normalization_policy()
        self.source = self._publish_source()
        self.benchmark_path, benchmark = self._publish_benchmark()
        self.industry_path = self._publish_industry_mapping()
        industry = load_static_industry_mapping(
            self.industry_path,
            expected_version="static-level1-concentration-research-test-v1",
        )
        source_bundle = load_historical_replay_bundle(self.source, self.corporate_action_policy)
        self.policy = StrategyReplayADSPolicy(
            contract_id="strategy-replay-ads-test-v1",
            context_policy="all_available_history_through_execution_end",
            bootstrap_source_snapshot_id=source_bundle.snapshot.snapshot_id,
            bootstrap_source_bundle_digest=source_bundle.digest,
            bootstrap_source_manifest_sha256=_sha256(self.source / "snapshot.json"),
            benchmark_instrument_id=benchmark.instrument_id,
            benchmark_logical_digest=benchmark.digest,
            benchmark_file_sha256=_sha256(self.benchmark_path),
            industry_mapping_version=industry.mapping_version,
            industry_mapping_logical_digest=industry.artifact_digest,
            industry_mapping_file_sha256=_sha256(self.industry_path),
            scopes={
                "development": StrategyReplayADSScope(
                    "development",
                    date(2026, 1, 5),
                    date(2026, 1, 8),
                    "development_research",
                ),
                "arbitration": StrategyReplayADSScope(
                    "arbitration",
                    date(2026, 1, 9),
                    date(2026, 1, 10),
                    "arbitration_operator",
                ),
                "permanent_blind": StrategyReplayADSScope(
                    "permanent_blind",
                    date(2026, 1, 11),
                    date(2026, 1, 12),
                    "blind_evaluation_operator",
                ),
            },
            digest=f"sha256:{'9' * 64}",
        )

    def _publish_benchmark(self) -> tuple[Path, BenchmarkCurve]:
        curve = BenchmarkCurve(
            benchmark_id="test-csi300",
            instrument_id="sh000300",
            display_name="CSI 300",
            currency="CNY",
            price_field="close",
            source="test",
            source_revision="test-csi300-v1",
            acquired_at=datetime(2026, 1, 13, tzinfo=SHANGHAI),
            observations=tuple(
                BenchmarkObservation(date(2026, 1, day), Decimal(str(3000 + day)))
                for day in range(5, 13)
            ),
        )
        path = self.root / "profiles" / "benchmark.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(curve.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path, curve

    def _publish_industry_mapping(self) -> Path:
        payload: dict[str, object] = {
            "schema_version": "1.0.0",
            "mapping_version": "static-level1-concentration-research-test-v1",
            "usage": "static_non_pit_concentration_only",
            "source": {
                "provider": "test",
                "endpoint": "test://industry",
                "retrieved_at": "2026-01-13T00:00:00+00:00",
                "source_revision": "test-industry-v1",
            },
            "memberships": [
                {
                    "security_id": "SZSE-000001",
                    "stock_code": "000001",
                    "exchange": "SZSE",
                    "level1_industry_id": "test-industry",
                }
            ],
        }
        payload["artifact_digest"] = _digest(payload)
        path = self.root / "profiles" / "industry.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def _source_revisions(self, dataset: str) -> dict[str, str]:
        if dataset not in {"corporate_actions_normalized", "corporate_action_exceptions"}:
            return {"source": f"test-{dataset}-v1"}
        lineage = {
            "normalization_policy_version": self.corporate_action_policy.policy_version,
            "normalization_report_digest": f"sha256:{'1' * 64}",
            "corporate_action_source_digest": f"sha256:{'2' * 64}",
            "authority_source_digest": f"sha256:{'3' * 64}",
            "exception_ledger_digest": f"sha256:{'4' * 64}",
            "exception_ledger_version": str(self.corporate_action_policy.exception_ledger_version),
        }
        if dataset == "corporate_action_exceptions":
            return {**lineage, "replay_disposition": "security_fail_closed"}
        return {**lineage, "normalization_exception_count": "1"}

    def _publish_source(self) -> Path:
        import pyarrow as pa
        import pyarrow.parquet as pq

        dates = tuple(date(2026, 1, day) for day in range(5, 10))
        security_id = "SZSE-000001"
        rows = {
            "corporate_action_exceptions": [
                {
                    "exception_id": "exception-1",
                    "security_code": "000001",
                    "blocked_from": dates[-1],
                    "blocked_through": dates[-1],
                    "reason_code": "normalization_unresolved",
                    "replay_disposition": "security_fail_closed",
                    "execution_authorized": False,
                }
            ],
            "corporate_actions_normalized": [
                {
                    "action_id": "action-1",
                    "security_code": "000001",
                    "effective_date": dates[2],
                    "known_at": datetime(2026, 1, 6, 10, tzinfo=SHANGHAI),
                    "cash_dividend_per_share": Decimal("0.10"),
                    "bonus_share_ratio": Decimal("0.00"),
                    "source_revision": "action-source-v1",
                    "source_snapshot_id": "action-source-snapshot-v1",
                    "evidence_digest": f"sha256:{'5' * 64}",
                    "execution_authorized": False,
                }
            ],
            "daily_ohlcv_none": [
                {
                    "security_id": security_id,
                    "date": market_date,
                    "open": Decimal("10.00"),
                    "high": Decimal("10.20"),
                    "low": Decimal("9.90"),
                    "close": Decimal("10.10"),
                    "volume": Decimal("100000"),
                    "amount": Decimal("1000000"),
                }
                for market_date in dates
            ],
            "daily_universe": [
                {
                    "security_id": security_id,
                    "stock_code": "000001",
                    "exchange": "SZSE",
                    "market_date": market_date,
                }
                for market_date in dates
            ],
            "price_limit_execution_daily": [
                {
                    "security_id": security_id,
                    "exchange": "SZSE",
                    "execution_date": market_date,
                    "observed_tradestatus": "1",
                    "entry_policy_allows": True,
                    "is_executable": True,
                    "reason_code": "price_limit_resolved",
                    "upper_limit_pct": Decimal("10"),
                    "lower_limit_pct": Decimal("10"),
                    "upper_limit_price": Decimal("11.11"),
                    "lower_limit_price": Decimal("9.09"),
                }
                for market_date in dates
            ],
            "security_code_validity": [
                {
                    "security_id": security_id,
                    "stock_code": "000001",
                    "exchange": "SZSE",
                    "valid_from": "1991-01-01",
                    "valid_to": "2026-01-12",
                }
            ],
            "trading_calendar": [
                {
                    "exchange": exchange,
                    "trade_date": market_date,
                    "is_open": 1,
                }
                for market_date in dates
                for exchange in ("SSE", "SZSE")
            ],
        }
        cutoff = datetime(2026, 1, 9, 18, tzinfo=SHANGHAI)
        plans = []
        for dataset, dataset_rows in rows.items():
            source = self.root / "source" / f"{dataset}.parquet"
            source.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(dataset_rows), source)
            plans.append(
                SnapshotDatasetPlan(
                    dataset=dataset,
                    version="test-v1",
                    source_path=source,
                    source_revisions=self._source_revisions(dataset),
                    knowledge_cutoff=cutoff,
                    created_at=cutoff,
                )
            )
        publication = publish_snapshot(
            SnapshotCatalog(self.root / "snapshots"),
            SnapshotPublicationPlan(
                reference=SnapshotRef(
                    market_date=dates[-1],
                    knowledge_cutoff=cutoff,
                    source_revision="strategy-replay-ads-source-v1",
                    snapshot_id="strategy-replay-ads-source-v1",
                ),
                universe_policy_version="test-universe-v1",
                datasets=tuple(plans),
            ),
        )
        return publication.location

    def _build(self) -> Path:
        output = self.root / "ads" / "strategy-replay-development-test-v1"
        result = build_strategy_replay_ads(
            source_snapshot_dir=self.source,
            output=output,
            bundle_id=output.name,
            scope_id="development",
            code_revision="0" * 40,
            benchmark_path=self.benchmark_path,
            industry_mapping_path=self.industry_path,
            ads_policy=self.policy,
            corporate_action_policy=self.corporate_action_policy,
        )
        self.assertEqual(result.root, output)
        return output

    def test_default_policy_freezes_three_physically_separate_scopes(self) -> None:
        policy = load_strategy_replay_ads_policy()

        self.assertEqual(tuple(policy.scopes), ("development", "arbitration", "permanent_blind"))
        self.assertEqual(policy.scopes["development"].reader_role, "development_research")
        self.assertLess(
            policy.scopes["development"].partition_end,
            policy.scopes["arbitration"].partition_start,
        )

    def test_build_filters_future_rows_and_opens_only_through_typed_role_reader(self) -> None:
        output = self._build()

        bundle = load_strategy_replay_ads(
            output,
            ads_policy=self.policy,
            reader_role="development_research",
            corporate_action_policy=self.corporate_action_policy,
        )
        deep_verified = load_strategy_replay_ads(
            output,
            ads_policy=self.policy,
            reader_role="development_research",
            corporate_action_policy=self.corporate_action_policy,
            verify_logical_content=True,
        )
        self.assertEqual(deep_verified.manifest_sha256, bundle.manifest_sha256)
        self.assertEqual(bundle.decision_start, date(2026, 1, 5))
        self.assertEqual(bundle.decision_end, date(2026, 1, 7))
        self.assertEqual(bundle.execution_end, date(2026, 1, 8))
        self.assertFalse(bundle.execution_authorized)
        self.assertRegex(bundle.bundle_content_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(bundle.manifest_sha256, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(bundle.bundle_content_digest, bundle.manifest_sha256)
        self.assertEqual(tuple(bundle.pit_bindings), tuple(date(2026, 1, day) for day in (5, 6, 7)))
        with load_strategy_replay_ads_reader(
            output,
            ads_policy=self.policy,
            reader_role="development_research",
            corporate_action_policy=self.corporate_action_policy,
        ) as reader:
            frames = reader.frames(date(2026, 1, 5), date(2026, 1, 7))
            self.assertEqual(len(frames), 3)
            self.assertEqual(frames[-1].execution_date, date(2026, 1, 8))
            final_session = frames[-1].source.session("SZSE-000001")
            self.assertEqual(final_session.session_date, date(2026, 1, 8))
            self.assertEqual(
                final_session.close_at,
                datetime(2026, 1, 8, 15, 0, tzinfo=SHANGHAI),
            )
            self.assertIn(".execution.20260108", final_session.snapshot_id)
            self.assertIn(
                "authorized-by-decision=2026-01-07",
                final_session.source_revision,
            )
            with self.assertRaisesRegex(
                StrategyReplayADSError,
                "next session of an authorized ADS decision",
            ):
                reader._session("SZSE-000001", date(2026, 1, 9))
            scoped_benchmark = reader.load_benchmark()
            self.assertEqual(scoped_benchmark.instrument_id, "sh000300")
            self.assertEqual(scoped_benchmark.observations[-1].market_date, date(2026, 1, 8))
            self.assertEqual(
                reader.load_industry_mapping(
                    expected_version=self.policy.industry_mapping_version
                ).mapping_version,
                self.policy.industry_mapping_version,
            )
            self.assertEqual(reader.data_content_digest, bundle.bundle_content_digest)
            self.assertEqual(reader.data_manifest_sha256, bundle.manifest_sha256)

        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        validity = next(
            item for item in manifest["tables"] if item["name"] == "security_code_validity"
        )
        self.assertEqual(validity["right_censor"]["counts"], {"valid_to": 1})

    def test_wrong_scope_role_fails_before_any_frame_is_opened(self) -> None:
        output = self._build()

        with self.assertRaisesRegex(StrategyReplayADSError, "not authorized"):
            load_strategy_replay_ads_reader(
                output,
                ads_policy=self.policy,
                reader_role="arbitration_operator",
                corporate_action_policy=self.corporate_action_policy,
            )

    def test_context_rows_cannot_be_promoted_to_decision_samples(self) -> None:
        output = self._build()

        with (
            load_strategy_replay_ads_reader(
                output,
                ads_policy=self.policy,
                reader_role="development_research",
                corporate_action_policy=self.corporate_action_policy,
            ) as reader,
            self.assertRaisesRegex(StrategyReplayADSError, "authorized ADS scope"),
        ):
            reader.frames(date(2026, 1, 5), date(2026, 1, 8))

    def test_strategy_decision_capability_detects_runner_only_access(self) -> None:
        output = self._build()

        with load_strategy_replay_ads_reader(
            output,
            ads_policy=self.policy,
            reader_role="development_research",
            corporate_action_policy=self.corporate_action_policy,
        ) as reader:
            view = reader.frames(date(2026, 1, 5), date(2026, 1, 5))[0].decision_view
            assert view is not None
            reader._session("SZSE-000001", date(2026, 1, 6))
            with self.assertRaisesRegex(HistoricalFrameReaderError, "runner-only"):
                view.assert_decision_capability()

    def test_manifest_driven_reader_accepts_multiple_parts(self) -> None:
        import duckdb

        output = self._build()
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        table = next(item for item in manifest["tables"] if item["name"] == "daily_ohlcv_none")
        original = output / table["parts"][0]["path"]
        first = original.with_name("daily_ohlcv_none.part-000.parquet")
        second = original.with_name("daily_ohlcv_none.part-001.parquet")
        connection = duckdb.connect(":memory:")
        try:
            for target, predicate in (
                (first, "CAST(date AS DATE) <= DATE '2026-01-06'"),
                (second, "CAST(date AS DATE) > DATE '2026-01-06'"),
            ):
                source_sql = str(original).replace("'", "''")
                target_sql = str(target).replace("'", "''")
                connection.execute(
                    f"COPY (SELECT * FROM read_parquet('{source_sql}') WHERE {predicate} "
                    f"ORDER BY date, security_id) TO '{target_sql}' "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
        finally:
            connection.close()
        original.unlink()
        table["parts"] = [
            {
                "path": path.relative_to(output).as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "row_count": 2,
                "schema_sha256": _schema_sha256(path),
                "min_date": minimum,
                "max_date": maximum,
                "sort_keys": ["date", "security_id"],
            }
            for path, minimum, maximum in (
                (first, "2026-01-05", "2026-01-06"),
                (second, "2026-01-07", "2026-01-08"),
            )
        ]
        manifest["bundle_content_digest"] = _digest(
            {
                key: value
                for key, value in manifest.items()
                if key
                not in {
                    "schema_version",
                    "kind",
                    "built_at",
                    "research_only",
                    "execution_authorized",
                    "bundle_content_digest",
                }
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (output / "READY").write_text(_sha256(manifest_path) + "\n", encoding="ascii")

        with load_strategy_replay_ads_reader(
            output,
            ads_policy=self.policy,
            reader_role="development_research",
            corporate_action_policy=self.corporate_action_policy,
        ) as reader:
            self.assertEqual(len(reader.frames(date(2026, 1, 5), date(2026, 1, 7))), 3)

    def test_bootstrap_source_and_publisher_revision_are_frozen(self) -> None:
        output = self._build()

        with self.assertRaisesRegex(StrategyReplayADSError, "different code revision"):
            build_strategy_replay_ads(
                source_snapshot_dir=self.source,
                output=output,
                bundle_id=output.name,
                scope_id="development",
                code_revision="1" * 40,
                benchmark_path=self.benchmark_path,
                industry_mapping_path=self.industry_path,
                ads_policy=self.policy,
                corporate_action_policy=self.corporate_action_policy,
            )

        rejected_policy = StrategyReplayADSPolicy(
            contract_id=self.policy.contract_id,
            context_policy=self.policy.context_policy,
            bootstrap_source_snapshot_id="different-source",
            bootstrap_source_bundle_digest=self.policy.bootstrap_source_bundle_digest,
            bootstrap_source_manifest_sha256=self.policy.bootstrap_source_manifest_sha256,
            benchmark_instrument_id=self.policy.benchmark_instrument_id,
            benchmark_logical_digest=self.policy.benchmark_logical_digest,
            benchmark_file_sha256=self.policy.benchmark_file_sha256,
            industry_mapping_version=self.policy.industry_mapping_version,
            industry_mapping_logical_digest=self.policy.industry_mapping_logical_digest,
            industry_mapping_file_sha256=self.policy.industry_mapping_file_sha256,
            scopes=self.policy.scopes,
            digest=self.policy.digest,
        )
        rejected = self.root / "ads" / "strategy-replay-development-rejected-v1"
        with self.assertRaisesRegex(StrategyReplayADSError, "frozen accepted snapshot"):
            build_strategy_replay_ads(
                source_snapshot_dir=self.source,
                output=rejected,
                bundle_id=rejected.name,
                scope_id="development",
                code_revision="0" * 40,
                benchmark_path=self.benchmark_path,
                industry_mapping_path=self.industry_path,
                ads_policy=rejected_policy,
                corporate_action_policy=self.corporate_action_policy,
            )

    def test_tampered_part_and_manifest_path_fail_closed(self) -> None:
        output = self._build()
        bars = output / "tables" / "daily_ohlcv_none.parquet"
        bars.write_bytes(bars.read_bytes() + b"tampered")
        with self.assertRaisesRegex(StrategyReplayADSError, "integrity failed"):
            load_strategy_replay_ads(
                output,
                ads_policy=self.policy,
                reader_role="development_research",
                corporate_action_policy=self.corporate_action_policy,
            )

        second = self.root / "ads" / "strategy-replay-development-test-v2"
        build_strategy_replay_ads(
            source_snapshot_dir=self.source,
            output=second,
            bundle_id=second.name,
            scope_id="development",
            code_revision="0" * 40,
            benchmark_path=self.benchmark_path,
            industry_mapping_path=self.industry_path,
            ads_policy=self.policy,
            corporate_action_policy=self.corporate_action_policy,
        )
        manifest_path = second / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["tables"][0]["parts"][0]["path"] = "../outside.parquet"
        manifest["bundle_content_digest"] = _digest(
            {
                key: value
                for key, value in manifest.items()
                if key
                not in {
                    "schema_version",
                    "kind",
                    "built_at",
                    "research_only",
                    "execution_authorized",
                    "bundle_content_digest",
                }
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (second / "READY").write_text(_sha256(manifest_path) + "\n", encoding="ascii")
        with self.assertRaisesRegex(StrategyReplayADSError, "safe relative path"):
            load_strategy_replay_ads(
                second,
                ads_policy=self.policy,
                reader_role="development_research",
                corporate_action_policy=self.corporate_action_policy,
            )

    def test_active_cli_inputs_are_closed_inside_the_ads_profile(self) -> None:
        parser = _parser()
        baseline = parser.parse_args(
            [
                "run-baseline-strategy-suite",
                "--ads-bundle",
                str(self.root / "ads"),
                "--ads-role",
                "development_research",
                "--scope-start",
                "2026-01-05",
                "--scope-end",
                "2026-01-08",
                "--feature-panel",
                str(self.root / "features.parquet"),
                "--output",
                str(self.root / "baseline.json"),
            ]
        )
        governed = parser.parse_args(
            [
                "run-governed-portfolio-replay",
                "--ads-bundle",
                str(self.root / "ads"),
                "--ads-role",
                "development_research",
                "--scope-start",
                "2026-01-05",
                "--scope-end",
                "2026-01-08",
                "--feature-panel",
                str(self.root / "features.parquet"),
                "--output",
                str(self.root / "governed.json"),
            ]
        )

        self.assertIsNone(baseline.benchmark)
        self.assertIsNone(governed.benchmark)
        self.assertIsNone(governed.industry_mapping)

    def test_ads_cli_requires_explicit_reader_role(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            exit_code = main(
                [
                    "audit-historical-replay-frames",
                    "--ads-bundle",
                    str(self.root / "ads"),
                    "--scope-start",
                    "2026-01-05",
                    "--scope-end",
                    "2026-01-08",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("--ads-role is required with --ads-bundle", stderr.getvalue())

    def test_legacy_snapshot_cli_is_warning_bound_compatibility_path(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                [
                    "audit-historical-replay-frames",
                    "--snapshot-dir",
                    str(self.source),
                    "--scope-start",
                    "2026-01-05",
                    "--scope-end",
                    "2026-01-08",
                ]
            )

        self.assertIn(exit_code, {0, 1})
        self.assertIn("frozen P3 acceptance-only compatibility path", stderr.getvalue())
        self.assertIn("ready_for_strategy_replay", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
