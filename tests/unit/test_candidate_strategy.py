from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    BacktestEngine,
    CoverageStatus,
    PITDailyBar,
    ReplayAction,
    ReplayDecision,
    StrategyReplayContext,
    UnitState,
    load_execution_policy,
)
from quanthus.candidate_strategy import (
    CandidateContractError,
    QuantDingerBullishThreeLinesAdapter,
    QuantDingerCandidateAdapterError,
    load_candidate_manifest,
    run_candidate_replay,
)
from quanthus.candidate_strategy.dwd_conformance import (
    DirectDwdProjectionIdentity,
    DirectDwdRow,
    _pit_bar,
)
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root
from tests.unit.test_historical_frame_reader import (
    P1_DEPENDENCIES_AVAILABLE,
    HistoricalReplayFrameReaderTests,
)

ROOT = find_project_root()
MANIFEST = ROOT / "config" / "quantdinger-candidates" / "bullish-three-lines.v1.json"
POLICY = ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"


class CandidateContractTests(unittest.TestCase):
    def test_packaged_candidate_is_research_only_and_digest_is_deterministic(self) -> None:
        first = load_candidate_manifest(MANIFEST)
        second = load_candidate_manifest(MANIFEST)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.source_template_key, "strategy_v2_bullish_three_lines")
        self.assertFalse(first.execution_authorized)

    def test_manifest_cannot_self_authorize_execution(self) -> None:
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        raw["governance"]["execution_authorized"] = True
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidate.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(CandidateContractError, "cannot grant execution"):
                load_candidate_manifest(path)

    def test_cli_ads_bundle_branch_uses_typed_ads_loader(self) -> None:
        from quanthus import cli

        evidence = mock.Mock()
        evidence.to_dict.return_value = {"pipeline": {"state": "replayed"}}
        reader = mock.MagicMock()
        reader.__enter__.return_value = reader
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            argv = [
                "run-candidate-strategy-replay",
                "--ads-bundle",
                "/authorized/development-bundle",
                "--ads-role",
                "development_research",
                "--candidate",
                str(MANIFEST),
                "--scope-start",
                "2012-01-04",
                "--scope-end",
                "2012-01-04",
                "--output",
                str(output),
            ]
            with (
                mock.patch.object(
                    cli, "load_strategy_replay_ads_reader", return_value=reader
                ) as load,
                mock.patch.object(cli, "run_candidate_replay", return_value=evidence),
            ):
                self.assertEqual(cli.main(argv), 0)
            load.assert_called_once()
            self.assertEqual(load.call_args.kwargs["reader_role"], "development_research")
            self.assertTrue(output.is_file())


@unittest.skipUnless(P1_DEPENDENCIES_AVAILABLE, "P1 dependencies are not installed")
class CandidateReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = HistoricalReplayFrameReaderTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _pilot_manifest(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        raw["candidate_version"] = "candidate-test-v1"
        raw["universe"]["source_symbols"] = ["CNStock:000043.SZ"]
        raw["universe"]["symbols"] = ["SZSE-000043"]
        raw["parameters"].update(
            {
                "short_period": 1,
                "mid_period": 2,
                "long_period": 3,
                "min_body_pct": "0.02",
                "target_pct": "0.50",
            }
        )
        path = self.fixture.root / "candidate.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return load_candidate_manifest(path)

    def test_quantdinger_signal_uses_only_t_close_history(self) -> None:
        manifest = self._pilot_manifest()
        dates = tuple(date(2026, 1, day) for day in (2, 5, 6, 7))
        prices = (
            ("10.00", "10.00"),
            ("10.00", "10.00"),
            ("10.00", "10.00"),
            ("9.90", "10.30"),
        )
        bars = tuple(
            PITDailyBar(
                security_code=manifest.security_code,
                exchange="SZSE",
                market_date=market_date,
                coverage_status=CoverageStatus.TRADING,
                data_valid=True,
                is_executable=True,
                entry_policy_allows=True,
                reason_code="price_limit_resolved",
                open_price=Decimal(open_price),
                high_price=Decimal("10.40"),
                low_price=Decimal("9.80"),
                close_price=Decimal(close_price),
                volume=Decimal("100000"),
                amount=Decimal("1000000"),
                upper_limit_price=Decimal("11.00"),
                lower_limit_price=Decimal("9.00"),
            )
            for market_date, (open_price, close_price) in zip(dates, prices, strict=True)
        )

        class Market:
            snapshot = SnapshotRef(
                market_date=dates[-1],
                knowledge_cutoff=datetime(2026, 1, 7, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
                source_revision="candidate-test",
                snapshot_id="candidate-test-snapshot",
            )

            def securities(self):
                return (manifest.security_code,)

            def history(self, security_code, sessions):
                self.request = (security_code, sessions)
                return bars

        market = Market()
        decision = QuantDingerBullishThreeLinesAdapter(manifest).decide(
            StrategyReplayContext(
                snapshot=market.snapshot,
                decision_at=market.snapshot.knowledge_cutoff,
                unit_state=UnitState.empty(unit_id="candidate-unit", cash=Decimal("100000.00")),
                market_view=market,
            )
        )
        self.assertEqual(decision.action, ReplayAction.ENTER)
        self.assertEqual(decision.security_code, manifest.security_code)
        self.assertIsNone(decision.quantity)
        self.assertEqual(decision.target_percent, Decimal("0.50"))
        self.assertEqual(market.request, (manifest.security_code, 4))

    def test_zero_activity_observation_is_price_bearing_but_not_executable(self) -> None:
        identity = DirectDwdProjectionIdentity(
            package_id="dwd-base-202607-v2.1",
            content_digest=(
                "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9"
            ),
            manifest_sha256=f"sha256:{'1' * 64}",
            projection_sha256=f"sha256:{'2' * 64}",
            listing_id="listing-test",
            security_id="security-test",
            venue_id="venue-test",
            listing_code="600519",
            source_symbol="CNStock:600519.SH",
            lot_size=100,
            price_basis="UNADJUSTED",
            scope_start=date(2012, 1, 4),
            scope_end=date(2021, 12, 31),
            row_count=1,
            historical_knowledge_from="2026-08-03T23:59:00+00:00",
            corporate_action_status="blocked",
            corporate_action_duplicate_groups=11,
        )
        bar = _pit_bar(
            DirectDwdRow(
                market_date=date(2012, 5, 29),
                open_price=Decimal("225.49"),
                high_price=Decimal("225.49"),
                low_price=Decimal("225.49"),
                close_price=Decimal("225.49"),
                volume=Decimal("0"),
                turnover=Decimal("0"),
                tradable=False,
                can_buy=False,
                can_sell=False,
                reason_code="ZERO_ACTIVITY_OBSERVATION",
                upper_price=None,
                lower_price=None,
                source_revision="dwd-test",
            ),
            identity,
        )
        self.assertTrue(bar.data_valid)
        self.assertFalse(bar.is_executable)
        self.assertFalse(bar.entry_policy_allows)

    def test_candidate_decision_runs_through_p2_next_open(self) -> None:
        from quanthus.backtest import HistoricalReplayFrameReader

        manifest = self._pilot_manifest()
        d1 = self.fixture.dates[0]

        class FixedDecision:
            strategy_id = manifest.candidate_id
            strategy_version = manifest.candidate_version

            def decide(self, _context):
                return ReplayDecision(
                    ReplayAction.ENTER,
                    "quantdinger_bullish_three_lines_entry",
                    Decimal("1"),
                    manifest.security_code,
                    100,
                )

        with (
            mock.patch(
                "quanthus.candidate_strategy.runner.build_quantdinger_adapter",
                return_value=FixedDecision(),
            ),
            HistoricalReplayFrameReader(self.fixture.bundle) as reader,
        ):
            evidence = run_candidate_replay(
                reader=reader,
                engine=BacktestEngine(load_execution_policy(POLICY)),
                manifest=manifest,
                scope_start=d1,
                scope_end=d1,
                initial_cash=Decimal("100000.00"),
                code_version="candidate-test-code-v1",
            ).to_dict()
        self.assertEqual(evidence["pipeline"]["state"], "replayed")
        self.assertTrue(evidence["pipeline"]["quanthus_p2_referee_used"])
        self.assertFalse(evidence["pipeline"]["quantdinger_fill_model_used"])
        self.assertEqual(evidence["result"]["decision_counts"]["enter"], 1)
        self.assertEqual(evidence["result"]["fills"], 1)
        self.assertFalse(evidence["pipeline"]["execution_authorized"])

    def test_invalid_dwd_history_fails_closed(self) -> None:
        from quanthus.backtest import HistoricalReplayFrameReader

        manifest = self._pilot_manifest()
        with HistoricalReplayFrameReader(self.fixture.bundle) as reader:
            context = self.fixture.run_spec(
                reader,
                rule_version=manifest.candidate_version,
                scope_start=self.fixture.dates[0],
                scope_end=self.fixture.dates[0],
            ).frames[0]
            replay_context = __import__(
                "quanthus.backtest.strategy_replay", fromlist=["StrategyReplayContext"]
            ).StrategyReplayContext(
                snapshot=context.snapshot,
                decision_at=context.decision_at,
                unit_state=__import__(
                    "quanthus.backtest.contracts", fromlist=["UnitState"]
                ).UnitState.empty(unit_id="candidate-unit", cash=Decimal("100000.00")),
                market_view=None,
            )
            with self.assertRaisesRegex(
                QuantDingerCandidateAdapterError, "requires a PIT market view"
            ):
                QuantDingerBullishThreeLinesAdapter(manifest).decide(replay_context)

    def test_wrong_security_identity_fails_closed(self) -> None:
        from quanthus.backtest import HistoricalReplayFrameReader

        manifest = self._pilot_manifest()
        raw = dict(manifest.canonical_payload)
        raw["universe"] = dict(raw["universe"])
        raw["universe"]["symbols"] = ["wrong-security-id"]
        path = self.fixture.root / "wrong-identity.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        wrong = load_candidate_manifest(path)
        with HistoricalReplayFrameReader(self.fixture.bundle) as reader:
            frame = self.fixture.run_spec(
                reader,
                rule_version=wrong.candidate_version,
                scope_start=self.fixture.dates[0],
                scope_end=self.fixture.dates[0],
            ).frames[0]
            context = StrategyReplayContext(
                snapshot=frame.snapshot,
                decision_at=frame.decision_at,
                unit_state=UnitState.empty(unit_id="candidate-unit", cash=Decimal("100000.00")),
                market_view=frame.decision_view,
            )
            with self.assertRaisesRegex(
                QuantDingerCandidateAdapterError, "absent from the current PIT universe"
            ):
                QuantDingerBullishThreeLinesAdapter(wrong).decide(context)

    def test_invalid_dwd_bar_blocks_entry_without_aborting_replay(self) -> None:
        manifest = self._pilot_manifest()
        from quanthus.backtest import HistoricalReplayFrameReader

        with HistoricalReplayFrameReader(self.fixture.bundle) as reader:
            frame = self.fixture.run_spec(
                reader,
                rule_version=manifest.candidate_version,
                scope_start=self.fixture.dates[2],
                scope_end=self.fixture.dates[2],
            ).frames[0]
            market = mock.Mock(wraps=frame.decision_view)
            history = list(frame.decision_view.history(manifest.security_code, 4))
            history.insert(0, replace(history[0], market_date=date(2026, 1, 2)))
            history[-1] = replace(history[-1], data_valid=False)
            market.securities.return_value = frame.decision_view.securities()
            market.history.return_value = tuple(history)
            decision = QuantDingerBullishThreeLinesAdapter(manifest).decide(
                StrategyReplayContext(
                    snapshot=frame.snapshot,
                    decision_at=frame.decision_at,
                    unit_state=UnitState.empty(unit_id="candidate-unit", cash=Decimal("100000.00")),
                    market_view=market,
                )
            )
            self.assertEqual(decision.action, ReplayAction.NO_ACTION)
            self.assertEqual(decision.reason_code, "candidate_data_blocked")
