from __future__ import annotations

import tempfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from quanthus.aggregator import GovernedAction
from quanthus.backtest.contracts import MarketSession, OpenExecutionState
from quanthus.backtest.inputs import CoverageStatus
from quanthus.backtest.strategy_replay import PITDailyBar, StrategyReplayFrame
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root
from quanthus.structure_facts_run import (
    StructureFactsDecision,
    StructureFactsRunRequest,
    _probe_candidates,
    load_structure_facts_run_request,
    run_structure_facts,
    verify_structure_facts_result,
    write_structure_facts_result,
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


def _frames() -> tuple[StrategyReplayFrame, ...]:
    start = date(2020, 1, 2)
    values: list[tuple[str, str, str, str]] = []
    values.extend(("10.00", "9.00", "9.60", "100") for _ in range(20))
    values.extend(
        (
            ("10.30", "9.90", "10.20", "50"),
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


def _request() -> StructureFactsRunRequest:
    return StructureFactsRunRequest(
        provider_config="config/replay-data-provider.development.v2.json",
        facts_policy="config/structure-continuous-facts.research.v1.json",
        execution_policy="config/p2-execution-policy.research.v1.json",
        scan_start=date(2020, 1, 2),
        report_start=date(2020, 1, 10),
        report_end=date(2020, 1, 24),
        security_codes=("600001",),
        maximum_p2_probes=3,
        p2_probe_quantity=100,
        consumer_expert_id="expert.structure-continuous-compatibility",
        consumer_expert_version="1.0.0",
        consumer_mapping="active_breakout_episode_uses_bounded_breakout_margin_directly",
    )


def test_frozen_structure_facts_request_loads() -> None:
    request = load_structure_facts_run_request(
        ROOT / "config/structure-facts-run.development.v1.json"
    )
    assert request.security_codes == ("000001", "300750", "600519")
    assert request.maximum_p2_probes == 5
    assert request.consumer_expert_id == "expert.structure-continuous-compatibility"


def test_probe_selection_round_robins_fixed_security_scope() -> None:
    request = StructureFactsRunRequest(
        provider_config="config/replay-data-provider.development.v2.json",
        facts_policy="config/structure-continuous-facts.research.v1.json",
        execution_policy="config/p2-execution-policy.research.v1.json",
        scan_start=date(2020, 1, 1),
        report_start=date(2020, 1, 2),
        report_end=date(2020, 3, 31),
        security_codes=("000001", "300750", "600519"),
        maximum_p2_probes=3,
        p2_probe_quantity=100,
        consumer_expert_id="expert.structure-continuous-compatibility",
        consumer_expert_version="1.0.0",
        consumer_mapping="active_breakout_episode_uses_bounded_breakout_margin_directly",
    )
    decisions = tuple(
        StructureFactsDecision(
            market_date=date(2020, 1, 10 + index),
            security_code=code,
            episode_id=f"episode-{index}",
            breakout_level=Decimal("10"),
            breakout_margin=Decimal("0.01"),
            score=Decimal("0.01"),
            final_action=GovernedAction.TRIAL_ENTRY,
        )
        for index, code in enumerate(("000001", "000001", "300750", "600519"))
    )

    selected = _probe_candidates(request, decisions)

    assert tuple(item.security_code for item in selected) == ("000001", "300750", "600519")


def test_fake_provider_proves_fact_artifact_direct_consumer_and_p2_boundary() -> None:
    result = run_structure_facts(
        _request(),
        repository_root=ROOT,
        provider=_Provider(_frames()),
        identity_map={"600001": "security-001"},
    )

    assert result.to_dict()["execution_authorized"] is False
    assert result.to_dict()["economic_acceptance_claim"] is False
    assert result.to_dict()["coarse_renderer_authority"] == "explanation_only"
    assert result.fact_counts["episode_attempt_rows"] == 1
    assert result.consumer_counts["applicable"] == 3
    assert result.consumer_counts["trial_entry"] == 3
    assert len(result.probes) == 3
    assert all(item.fill_count == 1 for item in result.probes)
    attempt = next(row for row in result.adm.rows if row.episode_age_sessions == 0)
    assert attempt.attempt_volume_ratio20 == Decimal("0.5")
    assert attempt.breakout_level == Decimal("10.00")

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "published"
        code_revision = "1" * 40
        publication = write_structure_facts_result(
            result,
            output,
            code_revision=code_revision,
        )
        assert publication["result_digest"] == result.digest
        verified = verify_structure_facts_result(
            output,
            expected_code_revision=code_revision,
            expected_request_digest=_request().digest,
        )
        assert verified["p2_probe_count"] == 3
        assert verified["p2_fill_count"] == 3
        assert verified["fact_counts"]["episode_attempt_rows"] == 1
        rows_path = output / "structure-facts.jsonl"
        original = rows_path.read_text(encoding="utf-8")
        rows_path.write_text(
            original.replace(
                '"breakout_level":"10.00"',
                '"breakout_level":"9.99"',
                1,
            ),
            encoding="utf-8",
        )
        try:
            verify_structure_facts_result(
                output,
                expected_code_revision=code_revision,
                expected_request_digest=_request().digest,
            )
        except ValueError as exc:
            assert "file digest differs" in str(exc)
        else:
            raise AssertionError("tampered fact package must fail verification")
