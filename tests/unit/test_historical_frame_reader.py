import importlib.util
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    BacktestEngine,
    BacktestInputError,
    BacktestRunSpec,
    HistoricalFrameReaderError,
    HistoricalReplayBundle,
    HistoricalReplayFrameReader,
    OpenExecutionState,
    ReplayAction,
    ReplayDecision,
    StrategyReplayContext,
    load_execution_policy,
    run_strategy_replay,
)
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.policy import find_project_root

P1_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = find_project_root()
POLICY_PATH = ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
ECONOMIC = "SZSE-000043"
EXCEPTION_SECURITY = "SZSE-000099"


def at(market_date: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(market_date, datetime.min.time(), SHANGHAI).replace(
        hour=hour,
        minute=minute,
    )


class MappingStrategy:
    def __init__(
        self,
        strategy_version: str,
        decisions: dict[date, ReplayDecision],
    ) -> None:
        self.strategy_id = "historical-frame-reader-test"
        self.strategy_version = strategy_version
        self.decisions = decisions
        self.contexts: list[StrategyReplayContext] = []

    def decide(self, context: StrategyReplayContext) -> ReplayDecision:
        self.contexts.append(context)
        return self.decisions[context.snapshot.market_date]


@unittest.skipUnless(P1_DEPENDENCIES_AVAILABLE, "P1 dependencies are not installed")
class HistoricalReplayFrameReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dates = (
            date(2026, 1, 5),
            date(2026, 1, 6),
            date(2026, 1, 7),
            date(2026, 1, 8),
        )
        self.securities = {
            ECONOMIC: "001914",
            "SZSE-000010": "000010",
            "SZSE-000011": "000011",
            "SZSE-000012": "000012",
            "SZSE-000013": "000013",
            EXCEPTION_SECURITY: "000099",
        }
        self.bundle = self.build_bundle()
        self.policy = load_execution_policy(POLICY_PATH)

    def build_bundle(self) -> HistoricalReplayBundle:
        import pyarrow as pa
        import pyarrow.parquet as pq

        d1, d2, d3, d4 = self.dates
        calendar = [
            {
                "exchange": exchange,
                "trade_date": market_date,
                "is_open": 1,
            }
            for market_date in self.dates
            for exchange in ("SSE", "SZSE")
        ]
        validity = [
            {
                "security_id": ECONOMIC,
                "stock_code": "000043",
                "exchange": "SZSE",
                "valid_from": "1995-03-20",
                "valid_to": "2019-12-31",
            },
            {
                "security_id": ECONOMIC,
                "stock_code": "001914",
                "exchange": "SZSE",
                "valid_from": "2020-01-01",
                "valid_to": None,
            },
            *(
                {
                    "security_id": security_id,
                    "stock_code": stock_code,
                    "exchange": "SZSE",
                    "valid_from": "2000-01-01",
                    "valid_to": None,
                }
                for security_id, stock_code in self.securities.items()
                if security_id != ECONOMIC
            ),
        ]
        universe = [
            {
                "security_id": security_id,
                "stock_code": stock_code,
                "exchange": "SZSE",
                "market_date": market_date,
            }
            for market_date in self.dates
            for security_id, stock_code in self.securities.items()
        ]
        prices = {
            d1: ("10.00", "10.20", "9.90", "10.00"),
            d2: ("10.00", "10.30", "9.90", "10.20"),
            d3: ("10.10", "10.50", "10.00", "10.40"),
            d4: ("10.50", "10.60", "10.20", "10.30"),
        }
        bars = []
        limits = []
        for market_date in self.dates:
            for security_id, _stock_code in self.securities.items():
                if not (security_id == "SZSE-000011" and market_date == d2):
                    open_price, high, low, close = prices[market_date]
                    if security_id == "SZSE-000012" and market_date == d2:
                        open_price = high = close = "11.00"
                    if security_id == "SZSE-000013" and market_date == d2:
                        open_price = low = close = "9.00"
                    bars.append(
                        {
                            "security_id": security_id,
                            "date": market_date,
                            "open": Decimal(open_price),
                            "high": Decimal(high),
                            "low": Decimal(low),
                            "close": Decimal(close),
                            "volume": Decimal("100000"),
                            "amount": Decimal("1000000"),
                        }
                    )
                if security_id == "SZSE-000011" and market_date == d2:
                    continue
                suspended = security_id == "SZSE-000010" and market_date == d2
                limits.append(
                    {
                        "security_id": security_id,
                        "exchange": "SZSE",
                        "execution_date": market_date,
                        "observed_tradestatus": "0" if suspended else "1",
                        "entry_policy_allows": True,
                        "is_executable": not suspended,
                        "reason_code": (
                            "session_not_tradable" if suspended else "price_limit_resolved"
                        ),
                        "upper_limit_pct": Decimal("10"),
                        "lower_limit_pct": Decimal("10"),
                        "upper_limit_price": Decimal("11.00"),
                        "lower_limit_price": Decimal("9.00"),
                    }
                )
        actions = [
            {
                "action_id": "action-alias-001914-20260107",
                "security_code": "001914",
                "effective_date": d3,
                "known_at": at(d2, 10),
                "cash_dividend_per_share": Decimal("0.10"),
                "bonus_share_ratio": Decimal("0.00"),
                "source_revision": "normalized-actions-test-v1",
                "source_snapshot_id": "normalized-action-source-snapshot-v1",
                "evidence_digest": f"sha256:{'1' * 64}",
                "execution_authorized": False,
            }
        ]
        exceptions = [
            {
                "exception_id": "exception-000099-20260107",
                "security_code": "000099",
                "blocked_from": d3,
                "blocked_through": d3,
                "reason_code": "normalization_unresolved",
                "replay_disposition": "security_fail_closed",
                "execution_authorized": False,
            }
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
                    version="test-v1",
                    source_revisions={"source": "historical-frame-test-v1"},
                    row_count=len(rows),
                    partitions=(str(relative),),
                    schema_hash="test-schema-v1",
                    content_checksums={str(relative): f"sha256:{'0' * 64}"},
                    created_at=at(d4, 18),
                    knowledge_cutoff=at(d4, 18),
                )
            )
        return HistoricalReplayBundle(
            root=self.root,
            snapshot=SnapshotRef(
                market_date=d4,
                knowledge_cutoff=at(d4, 18),
                source_revision="historical-frame-test-v1",
                snapshot_id="historical-frame-test-bundle-v1",
            ),
            universe_policy_version="test-universe-v1",
            datasets=tuple(manifests),
        )

    def run_spec(
        self,
        reader: HistoricalReplayFrameReader,
        *,
        rule_version: str,
        scope_start: date,
        scope_end: date,
    ):
        return reader.build_replay_spec(
            run=BacktestRunSpec(
                code_version="test-code-v1",
                parameter_hash="sha256:historical-frame-test",
                data_snapshot_id=self.bundle.snapshot.snapshot_id,
                feature_version="test-features-v1",
                rule_version=rule_version,
                run_id=f"historical-frame-{rule_version}",
                execution_policy_version=self.policy.policy_version,
                execution_profile_id="close-to-next-open-v1",
                schedule_version="close-to-next-open-synthetic-v1",
                official=False,
            ),
            unit_id="unit-001",
            initial_cash=Decimal("10000.00"),
            scope_start=scope_start,
            scope_end=scope_end,
        )

    def test_frames_are_consecutive_and_decision_view_is_bounded_at_t_close(self) -> None:
        d1, d2, d3, d4 = self.dates
        with HistoricalReplayFrameReader(self.bundle) as reader:
            frames = reader.frames(d1, d3)
            self.assertEqual(len(frames), 3)
            self.assertEqual(
                [(item.snapshot.market_date, item.execution_date) for item in frames],
                [(d1, d2), (d2, d3), (d3, d4)],
            )
            first = frames[0]
            self.assertEqual(first.execution_sessions, ())
            self.assertIsNotNone(first.decision_view)
            assert first.decision_view is not None
            self.assertTrue(
                all(item.market_date == d1 for item in first.decision_view.current_bars())
            )
            self.assertEqual(
                [item.market_date for item in first.decision_view.history(ECONOMIC, 10)],
                [d1],
            )
            self.assertFalse(hasattr(first.decision_view, "session"))

    def test_execution_states_alias_action_and_exception_are_fail_closed(self) -> None:
        d1, d2, d3, _ = self.dates
        with HistoricalReplayFrameReader(self.bundle) as reader:
            frames = reader.frames(d1, d3)
            execution = frames[0]
            self.assertEqual(
                execution.session(ECONOMIC).open_execution_state,
                OpenExecutionState.EXECUTABLE,
            )
            self.assertEqual(
                execution.session("SZSE-000010").open_execution_state,
                OpenExecutionState.SUSPENDED,
            )
            self.assertEqual(
                execution.session("SZSE-000011").open_execution_state,
                OpenExecutionState.MISSING_EVIDENCE,
            )
            self.assertEqual(
                execution.session("SZSE-000012").open_execution_state,
                OpenExecutionState.BUY_BLOCKED_LIMIT_UP,
            )
            self.assertEqual(
                execution.session("SZSE-000013").open_execution_state,
                OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
            )
            action = frames[1].actions(ECONOMIC, Decimal("10.20"))[0]
            self.assertEqual(action.action.security_code, ECONOMIC)
            self.assertEqual(action.post_action_mark_price, Decimal("10.10"))
            self.assertEqual(
                action.action.snapshot_id,
                "normalized-action-source-snapshot-v1",
            )
            self.assertEqual(
                frames[1].block_reason(EXCEPTION_SECURITY),
                "normalization_unresolved",
            )
            self.assertEqual(
                frames[1].session(EXCEPTION_SECURITY).open_execution_state,
                OpenExecutionState.MISSING_EVIDENCE,
            )
            self.assertEqual(frames[0].snapshot.market_date, d1)
            self.assertEqual(frames[1].snapshot.market_date, d2)

    def test_audit_counts_missing_execution_without_dropping_the_universe_row(self) -> None:
        d1, _, d3, _ = self.dates
        with HistoricalReplayFrameReader(self.bundle) as reader:
            report = reader.audit(d1, d3)
        self.assertEqual(report.frame_count, 3)
        self.assertEqual(report.decision_observation_count, 18)
        self.assertEqual(report.execution_evidence_count, 17)
        self.assertEqual(report.missing_execution_evidence_count, 1)
        self.assertEqual(report.missing_bar_count, 1)
        self.assertEqual(report.corporate_action_count, 1)
        self.assertEqual(report.corporate_action_blocked_pair_count, 1)
        self.assertFalse(report.execution_authorized)

    def test_lazy_reader_runs_entry_action_mark_and_exit_end_to_end(self) -> None:
        d1, d2, d3, _ = self.dates
        strategy = MappingStrategy(
            "reader-round-trip-v1",
            {
                d1: ReplayDecision(
                    ReplayAction.ENTER,
                    "enter_alias_identity",
                    Decimal("0.80"),
                    ECONOMIC,
                    100,
                ),
                d2: ReplayDecision(ReplayAction.NO_ACTION, "hold_through_action"),
                d3: ReplayDecision(ReplayAction.EXIT, "exit_after_action"),
            },
        )
        with HistoricalReplayFrameReader(self.bundle) as reader:
            result = run_strategy_replay(
                BacktestEngine(self.policy),
                strategy,
                self.run_spec(
                    reader,
                    rule_version=strategy.strategy_version,
                    scope_start=d1,
                    scope_end=d3,
                ),
            )
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].dividends, Decimal("10.00"))
        self.assertIsNone(result.final_state.security_code)
        self.assertTrue(all(context.market_view is not None for context in strategy.contexts))
        self.assertTrue(all(not hasattr(context, "source") for context in strategy.contexts))

    def test_exception_blocks_new_entry_and_aborts_an_existing_position(self) -> None:
        d1, d2, _, _ = self.dates
        entry = MappingStrategy(
            "blocked-entry-v1",
            {
                d2: ReplayDecision(
                    ReplayAction.ENTER,
                    "candidate_before_execution_block",
                    Decimal("0.50"),
                    EXCEPTION_SECURITY,
                    100,
                )
            },
        )
        held = MappingStrategy(
            "held-block-v1",
            {
                d1: ReplayDecision(
                    ReplayAction.ENTER,
                    "enter_before_block",
                    Decimal("0.50"),
                    EXCEPTION_SECURITY,
                    100,
                ),
                d2: ReplayDecision(ReplayAction.NO_ACTION, "hold_into_block"),
            },
        )
        with HistoricalReplayFrameReader(self.bundle) as reader:
            entry_result = run_strategy_replay(
                BacktestEngine(self.policy),
                entry,
                self.run_spec(
                    reader,
                    rule_version=entry.strategy_version,
                    scope_start=d2,
                    scope_end=d2,
                ),
            )
            self.assertEqual(entry_result.backtest.result_summary["fills"], 0)
            with self.assertRaisesRegex(
                BacktestInputError,
                "held security is blocked by replay evidence",
            ):
                run_strategy_replay(
                    BacktestEngine(self.policy),
                    held,
                    self.run_spec(
                        reader,
                        rule_version=held.strategy_version,
                        scope_start=d1,
                        scope_end=d2,
                    ),
                )

    def test_duplicate_daily_universe_identity_fails_before_frame_read(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        universe_path = (
            self.root / "datasets" / "daily_universe" / "daily_universe.parquet"
        )
        universe = pq.read_table(universe_path)
        pq.write_table(pa.concat_tables((universe, universe.slice(0, 1))), universe_path)

        with self.assertRaisesRegex(
            HistoricalFrameReaderError,
            "violate identity bounds",
        ):
            HistoricalReplayFrameReader(self.bundle)

    def test_unexplained_price_transition_is_fail_closed_execution_evidence(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        d1, d2, _, _ = self.dates
        bars_path = self.root / "datasets" / "daily_ohlcv_none" / "daily_ohlcv_none.parquet"
        rows = pq.read_table(bars_path).to_pylist()
        for row in rows:
            if row["security_id"] == ECONOMIC and row["date"] == d2:
                row.update(
                    {
                        "open": Decimal("20.00"),
                        "high": Decimal("20.00"),
                        "low": Decimal("20.00"),
                        "close": Decimal("20.00"),
                    }
                )
        pq.write_table(pa.Table.from_pylist(rows), bars_path)

        with HistoricalReplayFrameReader(self.bundle) as reader:
            frame = reader.frames(d1, d1)[0]
            evidence = reader.execution_evidence(ECONOMIC, d2)
            batched = reader.execution_evidence_batch(((ECONOMIC, d2),))
            self.assertEqual(batched[(ECONOMIC, d2)], evidence)
            self.assertEqual(evidence.security_code, ECONOMIC)
            self.assertFalse(evidence.is_executable)
            self.assertEqual(
                frame.session(ECONOMIC).open_execution_state,
                OpenExecutionState.MISSING_EVIDENCE,
            )
            self.assertEqual(
                frame.block_reason(ECONOMIC),
                "unexplained_price_transition",
            )
            report = reader.audit(d1, d1)
            self.assertEqual(report.unexplained_price_transition_count, 1)

    def test_execution_evidence_batch_is_one_set_join_equivalent_to_scalar_reads(self) -> None:
        d1, d2, d3, _ = self.dates
        requests = (
            (ECONOMIC, d2),
            ("SZSE-000010", d2),
            ("SZSE-000011", d2),
            (EXCEPTION_SECURITY, d3),
            ("SZSE-000010", date(1990, 1, 1)),
            (ECONOMIC, date(2030, 1, 1)),
            ("UNKNOWN-000001", d1),
        )
        with HistoricalReplayFrameReader(self.bundle) as reader:
            expected = {
                request: reader.execution_evidence(*request) for request in requests
            }
            with (
                mock.patch.object(
                    reader,
                    "_bars_for_date",
                    side_effect=AssertionError("batch must not scan by date"),
                ),
                mock.patch.object(
                    reader,
                    "_missing_coverage",
                    side_effect=AssertionError("batch must not query missing rows individually"),
                ),
                mock.patch.object(reader, "_rows", wraps=reader._rows) as rows,
            ):
                observed = reader.execution_evidence_batch(requests)

        self.assertEqual(observed, expected)
        self.assertEqual(rows.call_count, 1)
        self.assertEqual(
            observed[("SZSE-000010", date(1990, 1, 1))].coverage_status.value,
            "not_listed",
        )
        self.assertEqual(
            observed[(ECONOMIC, date(2030, 1, 1))].coverage_status.value,
            "delisted",
        )
        self.assertEqual(
            observed[("UNKNOWN-000001", d1)].coverage_status.value,
            "missing",
        )

    def test_execution_evidence_batch_rejects_duplicates_and_accepts_empty_input(self) -> None:
        _, d2, _, _ = self.dates
        with HistoricalReplayFrameReader(self.bundle) as reader:
            self.assertEqual(reader.execution_evidence_batch(()), {})
            with self.assertRaisesRegex(
                HistoricalFrameReaderError,
                "execution evidence requests must be unique",
            ):
                reader.execution_evidence_batch(((ECONOMIC, d2), (ECONOMIC, d2)))

    def test_missing_whole_session_universe_fails_continuous_frame_construction(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        d1, d2, d3, _ = self.dates
        universe_path = (
            self.root / "datasets" / "daily_universe" / "daily_universe.parquet"
        )
        rows = pq.read_table(universe_path).to_pylist()
        pq.write_table(
            pa.Table.from_pylist([row for row in rows if row["market_date"] != d2]),
            universe_path,
        )
        limits_path = (
            self.root
            / "datasets"
            / "price_limit_execution_daily"
            / "price_limit_execution_daily.parquet"
        )
        limit_rows = pq.read_table(limits_path).to_pylist()
        pq.write_table(
            pa.Table.from_pylist(
                [row for row in limit_rows if row["execution_date"] != d2]
            ),
            limits_path,
        )

        with HistoricalReplayFrameReader(self.bundle) as reader, self.assertRaisesRegex(
            HistoricalFrameReaderError,
            "daily universe has no rows for required sessions: 2026-01-06",
        ):
            reader.frames(d1, d3)


if __name__ == "__main__":
    unittest.main()
