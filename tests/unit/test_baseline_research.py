import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    BacktestEngine,
    BaselineFeaturePanel,
    BaselineResearchError,
    BenchmarkCurve,
    BenchmarkObservation,
    EventType,
    HistoricalReplayFrameReader,
    load_baseline_strategy_catalogue,
    load_execution_policy,
    render_baseline_scoreboard,
    run_baseline_strategy_suite,
)
from quanthus.backtest.baseline_research import BaselineHistoricalStrategy
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.feature_registry import load_feature_registry
from quanthus.policy import find_project_root

P1_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = find_project_root()


def at(market_date: date, hour: int) -> datetime:
    return datetime.combine(market_date, datetime.min.time(), SHANGHAI).replace(hour=hour)


def weekdays(start: date, count: int) -> tuple[date, ...]:
    values: list[date] = []
    cursor = start
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor += timedelta(days=1)
    return tuple(values)


@unittest.skipUnless(P1_DEPENDENCIES_AVAILABLE, "P1 dependencies are not installed")
class BaselineResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dates = weekdays(date(2012, 1, 2), 132)
        self.bundle = self._build_bundle()
        self.registry = load_feature_registry(
            ROOT / "config" / "p3-baseline-features.research.v1.json"
        )
        self.v1_catalogue = load_baseline_strategy_catalogue(
            ROOT / "config" / "p3-baseline-strategies.research.v1.json"
        )
        self.catalogue = load_baseline_strategy_catalogue(
            ROOT / "config" / "p3-baseline-strategies.research.v2.json"
        )
        self.benchmark = BenchmarkCurve(
            benchmark_id="csi300",
            instrument_id="sh000300",
            display_name="CSI 300",
            currency="CNY",
            price_field="close",
            source="synthetic",
            source_revision="synthetic-csi300-v1",
            acquired_at=at(self.dates[-1], 18),
            observations=tuple(
                BenchmarkObservation(market_date=value, close=Decimal(3000 + index))
                for index, value in enumerate(self.dates)
            ),
        )

    def _build_bundle(self) -> HistoricalReplayBundle:
        import pyarrow as pa
        import pyarrow.parquet as pq

        securities = {"SSE-600001": "600001", "SZSE-000002": "000002"}
        split_index = 30
        universe = []
        bars = []
        limits = []
        calendar = []
        prior_prices: dict[str, Decimal] = {}
        for market_date in self.dates:
            for exchange in ("SSE", "SZSE"):
                calendar.append(
                    {"exchange": exchange, "trade_date": market_date, "is_open": 1}
                )
        for index, market_date in enumerate(self.dates):
            economic_price = Decimal("100") + Decimal("2") * index
            split_price = (
                economic_price if index < split_index else economic_price / Decimal("2")
            )
            for security_id, stock_code in securities.items():
                exchange = "SSE" if security_id.startswith("SSE") else "SZSE"
                close = split_price if security_id == "SSE-600001" else Decimal("10")
                price_reference = prior_prices.get(security_id, close)
                volume = Decimal("2000000") if index == 121 else Decimal("1000000")
                universe.append(
                    {
                        "security_id": security_id,
                        "stock_code": stock_code,
                        "exchange": exchange,
                        "market_date": market_date,
                    }
                )
                bars.append(
                    {
                        "security_id": security_id,
                        "stock_code": stock_code,
                        "date": market_date,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": volume,
                        "amount": volume * close,
                    }
                )
                limits.append(
                    {
                        "security_id": security_id,
                        "exchange": exchange,
                        "execution_date": market_date,
                        "observed_tradestatus": "1",
                        "entry_policy_allows": True,
                        "is_executable": True,
                        "reason_code": "price_limit_resolved",
                        "upper_limit_pct": Decimal("10"),
                        "lower_limit_pct": Decimal("10"),
                        "upper_limit_price": price_reference * Decimal("1.1"),
                        "lower_limit_price": price_reference * Decimal("0.9"),
                    }
                )
                prior_prices[security_id] = close
        split_date = self.dates[split_index]
        actions = [
            {
                "action_id": "split-600001-v1",
                "security_code": "600001",
                "effective_date": split_date,
                "known_at": at(self.dates[split_index - 5], 15),
                "cash_dividend_per_share": Decimal("0"),
                "bonus_share_ratio": Decimal("1"),
                "source_revision": "synthetic-action-v1",
                "source_snapshot_id": "synthetic-action-snapshot-v1",
                "evidence_digest": f"sha256:{'1' * 64}",
                "execution_authorized": False,
            }
        ]
        exceptions = [
            {
                "exception_id": "future-exception-v1",
                "security_code": "000002",
                "blocked_from": self.dates[-1] + timedelta(days=1),
                "blocked_through": None,
                "reason_code": "missing_cross_check",
                "replay_disposition": "security_fail_closed",
                "execution_authorized": False,
            }
        ]
        validity = [
            {
                "security_id": security_id,
                "stock_code": stock_code,
                "exchange": "SSE" if security_id.startswith("SSE") else "SZSE",
                "valid_from": date(2000, 1, 1),
                "valid_to": None,
            }
            for security_id, stock_code in securities.items()
        ]
        datasets = {
            "corporate_action_exceptions": exceptions,
            "corporate_actions_normalized": actions,
            "daily_ohlcv_none": bars,
            "daily_universe": universe,
            "price_limit_execution_daily": limits,
            "security_code_validity": validity,
            "trading_calendar": calendar,
        }
        manifests = []
        for name, rows in datasets.items():
            relative = Path("datasets") / name / f"{name}.parquet"
            output = self.root / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows), output)
            manifests.append(
                DatasetManifest(
                    dataset=name,
                    version="synthetic-v1",
                    source_revisions={"source": "synthetic-v1"},
                    row_count=len(rows),
                    partitions=(str(relative),),
                    schema_hash="synthetic-schema-v1",
                    content_checksums={str(relative): f"sha256:{'0' * 64}"},
                    created_at=at(self.dates[-1], 18),
                    knowledge_cutoff=at(self.dates[-1], 18),
                )
            )
        return HistoricalReplayBundle(
            root=self.root,
            snapshot=SnapshotRef(
                market_date=self.dates[-1],
                knowledge_cutoff=at(self.dates[-1], 18),
                source_revision="synthetic-baseline-v1",
                snapshot_id="synthetic-baseline-bundle-v1",
            ),
            universe_policy_version="synthetic-universe-v1",
            datasets=tuple(manifests),
        )

    def _panel(self) -> BaselineFeaturePanel:
        return BaselineFeaturePanel.build(
            self.bundle,
            self.registry,
            scope_start=self.dates[0],
            scope_end=self.dates[-2],
            output=self.root / "baseline-features.parquet",
        )

    def test_total_return_index_is_continuous_across_a_split(self) -> None:
        split_index = 30
        with self._panel() as panel:
            before = panel.feature(self.dates[split_index - 1], "SSE-600001")
            after = panel.feature(self.dates[split_index], "SSE-600001")
            self.assertIsNotNone(before)
            self.assertIsNotNone(after)
            assert before is not None and before.total_return_index is not None
            assert after is not None and after.total_return_index is not None
            observed_return = after.total_return_index / before.total_return_index - Decimal("1")
            expected_return = Decimal("160") / Decimal("158") - Decimal("1")
            self.assertAlmostEqual(float(observed_return), float(expected_return), places=10)
            raw_before = Decimal("158")
            raw_after = Decimal("80")
            self.assertLess(raw_after / raw_before - Decimal("1"), Decimal("-0.49"))

    def test_v1_remains_replayable_and_v2_freezes_moderation_bounds(self) -> None:
        self.assertEqual(
            self.v1_catalogue.digest,
            "sha256:573d53b0046823af0f613e2efbf2a51b4393686de63dc4ae67a6af35f7210c4c",
        )
        self.assertFalse(self.v1_catalogue.shared.moderated)
        self.assertTrue(self.catalogue.shared.moderated)
        self.assertEqual(self.catalogue.shared.position_fraction, Decimal("0.30"))
        self.assertEqual(
            self.catalogue.shared.maximum_realized_volatility20,
            Decimal("0.50"),
        )
        self.assertTrue(all(item.moderated for item in self.catalogue.strategies))

        raw = json.loads(self.catalogue.path.read_text(encoding="utf-8"))
        raw["shared"]["position_fraction"] = "1.01"
        invalid_path = self.root / "invalid-baseline-v2.json"
        invalid_path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(BaselineResearchError, "position_fraction"):
            load_baseline_strategy_catalogue(invalid_path)

    def test_suite_runs_all_baselines_and_rejects_non_development_scope(self) -> None:
        execution_policy = load_execution_policy(
            ROOT / "config" / "p2-execution-policy.research.v1.json"
        )
        scope_start = self.dates[121]
        scope_end = self.dates[128]
        with HistoricalReplayFrameReader(self.bundle) as reader, self._panel() as panel:
            result = run_baseline_strategy_suite(
                reader,
                BacktestEngine(execution_policy),
                self.catalogue,
                self.registry,
                panel,
                self.benchmark,
                scope_start=scope_start,
                scope_end=scope_end,
            )
            self.assertEqual(result.runner_version, "2.1.0")
            self.assertEqual(
                {item.strategy_id for item in result.strategies},
                {
                    "baseline.ma-trend",
                    "baseline.breakout20",
                    "baseline.oversold-rebound",
                },
            )
            self.assertTrue(all(not item.backtest.run.official for item in result.strategies))
            result_payload = result.to_dict()
            self.assertEqual(result_payload["benchmark"]["instrument_id"], "sh000300")
            for strategy_payload in result_payload["strategies"]:
                self.assertEqual(
                    strategy_payload["operation_count"],
                    strategy_payload["buy_operation_count"]
                    + strategy_payload["sell_operation_count"],
                )
                self.assertEqual(
                    strategy_payload["closed_trade_count"],
                    strategy_payload["trade_count"],
                )
            buy_fills = [
                event
                for replay in result.strategies
                for event in replay.backtest.events
                if event.event_type is EventType.FILL and event.payload.get("side") == "buy"
            ]
            self.assertTrue(buy_fills)
            self.assertTrue(
                all(
                    Decimal(str(event.payload["gross_amount"])) <= Decimal("30000")
                    for event in buy_fills
                )
            )
            scoreboard = render_baseline_scoreboard(result)
            self.assertIn("Operations (B/S)", scoreboard)
            self.assertIn("Win rate", scoreboard)
            self.assertIn("Max drawdown", scoreboard)
            self.assertIn("Profit factor", scoreboard)
            self.assertIn("Ann. volatility", scoreboard)
            self.assertIn("Payoff ratio", scoreboard)
            self.assertIn("Turnover", scoreboard)
            self.assertIn("Time in market", scoreboard)
            self.assertIn("Avg invested", scoreboard)
            self.assertIn("Benchmark return", scoreboard)
            self.assertIn("Excess return", scoreboard)
            self.assertIn("Return ex top 3", scoreboard)
            with self.assertRaisesRegex(BaselineResearchError, "explicit sh000300"):
                run_baseline_strategy_suite(
                    reader,
                    BacktestEngine(execution_policy),
                    self.catalogue,
                    self.registry,
                    panel,
                    replace(self.benchmark, instrument_id="000300"),
                    scope_start=scope_start,
                    scope_end=scope_end,
                )
            with BaselineFeaturePanel.build(
                self.bundle,
                self.registry,
                scope_start=self.dates[0],
                scope_end=scope_end,
                output=self.root / "under-scoped-baseline-features.parquet",
            ) as under_scoped_panel, self.assertRaisesRegex(
                BaselineResearchError,
                "continuity scope must include the final execution date",
            ):
                run_baseline_strategy_suite(
                    reader,
                    BacktestEngine(execution_policy),
                    self.catalogue,
                    self.registry,
                    under_scoped_panel,
                    self.benchmark,
                    scope_start=scope_start,
                    scope_end=scope_end,
                )
            with self.assertRaisesRegex(BaselineResearchError, "development period"):
                run_baseline_strategy_suite(
                    reader,
                    BacktestEngine(execution_policy),
                    self.catalogue,
                    self.registry,
                    panel,
                    self.benchmark,
                    scope_start=date(2022, 1, 4),
                    scope_end=date(2022, 1, 5),
                )

    def test_v2_sizing_keeps_20_percent_t_plus_one_gap_below_budget(self) -> None:
        with self._panel() as panel:
            strategy = BaselineHistoricalStrategy(
                self.catalogue.strategies[0],
                self.catalogue.shared,
                panel,
            )
            cash = Decimal("100000")
            decision_close = Decimal("10")
            limit_up_open = decision_close * Decimal("1.20")
            quantity = strategy._entry_quantity(cash, decision_close)

            assert self.catalogue.shared.position_fraction is not None
            entry_budget = cash * self.catalogue.shared.position_fraction
            self.assertEqual(quantity, 2400)
            self.assertLess(quantity * limit_up_open, entry_budget)
            self.assertGreaterEqual(
                entry_budget - quantity * limit_up_open,
                Decimal("1000"),
            )

    def test_invalid_future_known_action_fails_closed(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        action_path = (
            self.root
            / "datasets"
            / "corporate_actions_normalized"
            / "corporate_actions_normalized.parquet"
        )
        rows = pq.read_table(action_path).to_pylist()
        rows[0]["known_at"] = at(rows[0]["effective_date"], 18)
        pq.write_table(pa.Table.from_pylist(rows), action_path)

        with self.assertRaisesRegex(BaselineResearchError, "invalid normalized action"):
            self._panel()

    def test_scope_continuity_policy_excludes_an_incomplete_security_from_start(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        action_path = (
            self.root
            / "datasets"
            / "corporate_actions_normalized"
            / "corporate_actions_normalized.parquet"
        )
        rows = pq.read_table(action_path).to_pylist()
        rows[0]["effective_date"] = self.dates[-1]
        rows[0]["known_at"] = at(self.dates[-2], 15)
        pq.write_table(pa.Table.from_pylist(rows), action_path)

        with self._panel() as panel:
            self.assertEqual(panel.report.continuity_excluded_security_count, 1)
            before_break = panel.feature(self.dates[20], "SSE-600001")
            self.assertIsNotNone(before_break)
            assert before_break is not None
            self.assertFalse(before_break.entry_eligible)


if __name__ == "__main__":
    unittest.main()
