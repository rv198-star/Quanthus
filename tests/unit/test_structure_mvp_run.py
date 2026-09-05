from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from quanthus.aggregator import GovernedAction
from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.contracts import MarketSession, OpenExecutionState
from quanthus.backtest.inputs import CoverageStatus
from quanthus.backtest.strategy_replay import PITDailyBar, StrategyReplayFrame
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root
from quanthus.structure_intelligence import StructureMvpState
from quanthus.structure_mvp_run import (
    StructureMvpDecision,
    StructureMvpRunRequest,
    _probe_candidates,
    load_structure_mvp_run_request,
    run_structure_mvp,
    verify_structure_mvp_result,
    write_structure_mvp_result,
)

ROOT = find_project_root()
SHANGHAI = ZoneInfo("Asia/Shanghai")
SHA = "sha256:" + "b" * 64


class _View:
    def __init__(self, snapshot: SnapshotRef, bar: PITDailyBar) -> None:
        self.snapshot = snapshot
        self._bar = bar

    def bar(self, security_code: str) -> PITDailyBar:
        if security_code != self._bar.security_code:
            raise KeyError(security_code)
        return self._bar


class _Reader:
    def __init__(self, frames: tuple[StrategyReplayFrame, ...]) -> None:
        self._frames = frames
        self.bundle = SimpleNamespace(root=Path("/unused"))

    def frames(self, start: date, end: date) -> tuple[StrategyReplayFrame, ...]:
        return tuple(
            frame for frame in self._frames if start <= frame.snapshot.market_date <= end
        )


class _Session:
    def __init__(self, frames: tuple[StrategyReplayFrame, ...]) -> None:
        self.reader = _Reader(frames)
        self.identity = {
            "provider_id": "fake-direct-dwd",
            "provider_version": "2.0.0",
            "provider_config_digest": SHA,
            "provider_lineage_digest": SHA,
            "materialized_content_digest": SHA,
            "point_in_time": {
                "strategy_cutoff_enforced": True,
                "record_view": "retrospective_dwd_record_view",
                "per_decision_publication_proven": False,
            },
        }

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Provider:
    def __init__(self, frames: tuple[StrategyReplayFrame, ...]) -> None:
        self._frames = frames

    def open(self, *, scope_start: date, scope_end: date) -> _Session:
        assert scope_start <= scope_end
        return _Session(self._frames)


def _trend_features(
    frames: tuple[StrategyReplayFrame, ...],
) -> dict[tuple[date, str], BaselineFeatureRow]:
    return {
        (frame.snapshot.market_date, "600001"): BaselineFeatureRow(
            market_date=frame.snapshot.market_date,
            security_id="600001",
            close_price=Decimal("10"),
            total_return_index=Decimal("100"),
            sma20=Decimal("100"),
            sma60=Decimal("100"),
            momentum20=Decimal("0"),
            prior_high20=Decimal("100"),
            rsi14=Decimal("50"),
            rsi14_previous=Decimal("50"),
            volume_ratio20=Decimal("1"),
            realized_volatility20=Decimal("0.20"),
            daily_total_return=Decimal("0"),
            average_amount20=Decimal("1000000"),
            listing_sessions=300,
            feature_ready=True,
            entry_eligible=True,
        )
        for frame in frames
    }


def _frames() -> tuple[StrategyReplayFrame, ...]:
    start = date(2020, 1, 2)
    values: list[tuple[str, str, str, str]] = []
    values.extend(("10.00", "9.00", "9.60", "100") for _ in range(20))
    values.extend(
        (
            ("10.30", "9.90", "10.20", "150"),
            ("10.25", "10.00", "10.10", "105"),
            ("10.30", "10.05", "10.15", "110"),
        )
    )
    output: list[StrategyReplayFrame] = []
    for index, (high, low, close, volume) in enumerate(values):
        market_date = start + timedelta(days=index)
        execution_date = market_date + timedelta(days=1)
        cutoff = datetime.combine(market_date, time(15, 0), SHANGHAI)
        snapshot = SnapshotRef(
            market_date=market_date,
            knowledge_cutoff=cutoff,
            source_revision="fake-dwd",
            snapshot_id=f"fake-{market_date.isoformat()}",
        )
        bar = PITDailyBar(
            security_code="600001",
            exchange="SSE",
            market_date=market_date,
            coverage_status=CoverageStatus.TRADING,
            data_valid=True,
            is_executable=True,
            entry_policy_allows=True,
            reason_code="TRADABLE_OBSERVED",
            open_price=Decimal(close),
            high_price=Decimal(high),
            low_price=Decimal(low),
            close_price=Decimal(close),
            volume=Decimal(volume),
            amount=Decimal("1000000"),
            upper_limit_price=Decimal("20"),
            lower_limit_price=Decimal("5"),
        )
        session = MarketSession(
            session_date=execution_date,
            open_at=datetime.combine(execution_date, time(9, 30), SHANGHAI),
            close_at=datetime.combine(execution_date, time(15, 0), SHANGHAI),
            security_code="600001",
            open_price=Decimal(close),
            close_price=Decimal(close),
            open_execution_state=OpenExecutionState.EXECUTABLE,
            source_revision="fake-execution",
            snapshot_id=f"execution-{execution_date.isoformat()}",
        )
        output.append(
            StrategyReplayFrame(
                snapshot=snapshot,
                decision_at=cutoff,
                execution_date=execution_date,
                execution_sessions=(session,),
                decision_view=_View(snapshot, bar),
            )
        )
    return tuple(output)


class StructureMvpRunTests(unittest.TestCase):
    def test_probe_selection_round_robins_across_fixed_security_scope(self) -> None:
        request = StructureMvpRunRequest(
            provider_config="config/replay-data-provider.development.v2.json",
            adm_policy="config/structure-research-adm.mvp.v1.json",
            execution_policy="config/p2-execution-policy.research.v1.json",
            scan_start=date(2020, 1, 1),
            report_start=date(2020, 1, 2),
            report_end=date(2020, 3, 31),
            security_codes=("000001", "300750", "600519"),
            maximum_p2_probes=3,
            p2_probe_quantity=100,
        )
        decisions = tuple(
            StructureMvpDecision(
                market_date=date(2020, 1, 10 + index),
                security_code=code,
                state=StructureMvpState.BREAKOUT_ACCEPTED,
                trend_state="neutral",
                baseline_score=Decimal("0"),
                baseline_action=GovernedAction.NO_ACTION,
                assisted_score=Decimal("0.4"),
                assisted_action=GovernedAction.TRIAL_ENTRY,
            )
            for index, code in enumerate(("000001", "000001", "300750", "600519"))
        )
        selected = _probe_candidates(request, decisions)
        self.assertEqual(
            tuple(item.security_code for item in selected),
            ("000001", "300750", "600519"),
        )

    def test_frozen_development_request_loads(self) -> None:
        request = load_structure_mvp_run_request(
            ROOT / "config/structure-mvp-run.development.v1.json"
        )
        self.assertEqual(request.security_codes, ("000001", "300750", "600519"))
        self.assertEqual(request.maximum_p2_probes, 5)

    def test_fake_provider_proves_run_artifact_and_p2_probe_boundary(self) -> None:
        request = StructureMvpRunRequest(
            provider_config="config/replay-data-provider.development.v2.json",
            adm_policy="config/structure-research-adm.mvp.v1.json",
            execution_policy="config/p2-execution-policy.research.v1.json",
            scan_start=date(2020, 1, 2),
            report_start=date(2020, 1, 10),
            report_end=date(2020, 1, 24),
            security_codes=("600001",),
            maximum_p2_probes=3,
            p2_probe_quantity=100,
        )
        frames = _frames()
        result = run_structure_mvp(
            request,
            repository_root=ROOT,
            provider=_Provider(frames),
            identity_map={"600001": "security-001"},
            trend_features=_trend_features(frames),
        )
        self.assertFalse(result.to_dict()["execution_authorized"])
        self.assertFalse(result.to_dict()["economic_acceptance_claim"])
        self.assertEqual(result.state_counts.get("breakout_accepted"), 1)
        self.assertEqual(result.decision_counts["changed"], 1)
        self.assertEqual(result.decision_counts["assisted_new_entry"], 1)
        self.assertEqual(len(result.probes), 1)
        self.assertEqual(result.probes[0].trend_state, "neutral")
        self.assertEqual(result.probes[0].baseline_action.value, "no_action")
        self.assertEqual(result.probes[0].fill_count, 1)
        self.assertEqual(result.probes[0].final_action.value, "trial_entry")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "published"
            code_revision = "1" * 40
            manifest = write_structure_mvp_result(
                result,
                output,
                code_revision=code_revision,
            )
            self.assertTrue((output / "READY").is_file())
            self.assertTrue((output / "manifest.json").is_file())
            self.assertEqual(manifest["result_digest"], result.digest)
            verified = verify_structure_mvp_result(
                output,
                expected_code_revision=code_revision,
                expected_request_digest=request.digest,
            )
            self.assertEqual(verified["p2_probe_count"], 1)
            self.assertEqual(verified["p2_fill_count"], 1)
            rows_path = output / "structure-adm.jsonl"
            original_rows = rows_path.read_text(encoding="utf-8")
            rows_path.write_text(
                original_rows.replace("breakout_accepted", "range_intact", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "file digest differs"):
                verify_structure_mvp_result(
                    output,
                    expected_code_revision=code_revision,
                    expected_request_digest=request.digest,
                )


if __name__ == "__main__":
    unittest.main()
