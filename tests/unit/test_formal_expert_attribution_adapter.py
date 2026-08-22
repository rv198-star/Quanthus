from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from quanthus.expert_strategy.formal_attribution import (
    FormalAttributionExecutor,
    FormalP2ReplayBackend,
    FormalReplayEvidence,
)
from quanthus.expert_strategy.formal_attribution_semantics import (
    FormalStrategyAttributionAdapter,
)
from quanthus.strategy_attribution_pipeline import AttributionPipeline, PipelineRunState
from quanthus.strategy_attribution_planning import PlanNodeKind, compile_attribution_plan

ROOT = Path(__file__).resolve().parents[2]


def _evidence(intervention_id: str | None) -> FormalReplayEvidence:
    value = Decimal("0.10") if intervention_id is None else Decimal("0.08")
    metrics = {
        "capital_constrained_net_total_return_delta": value,
        "maximum_drawdown_delta": Decimal("0.05"),
        "tail_loss_delta": Decimal("0.01"),
        "average_invested_capital_delta": Decimal("0.20"),
        "time_in_market_delta": Decimal("0.30"),
        "turnover_delta": Decimal("0.40"),
        "fee_delta": Decimal("100"),
        "missed_opportunity_count": Decimal("2"),
        "decision_change_counts": Decimal("3"),
        "action_change_rate": Decimal("0.02"),
    }
    digest = "sha256:" + ("1" if intervention_id is None else "2") * 64
    return FormalReplayEvidence(
        variant_id=FormalP2ReplayBackend.variant_id(intervention_id),
        intervention_id=intervention_id,
        metrics=metrics,
        replay_digest=digest,
        reconciliation_digest=digest,
        decision_trace_digest=digest,
        opportunity_trace_digest=digest,
        daily_return_artifact_digest=digest,
    )


def test_formal_semantic_bridge_compiles_four_unique_physical_replays() -> None:
    adapter = FormalStrategyAttributionAdapter(ROOT)
    plan = compile_attribution_plan(adapter.run_request())
    physical = tuple(
        item
        for item in plan.experiments
        if item.kind in {PlanNodeKind.BASELINE_REPLAY, PlanNodeKind.SINGLE_INTERVENTION}
    )

    assert len(physical) == 4
    assert len({item.digest for item in physical}) == 4
    assert {item.intervention_ids for item in physical} == {
        (),
        ("intervention.trend-stability-abstain",),
        ("intervention.position-risk-base-policy",),
        ("intervention.both-experts-fallback",),
    }
    assert (
        len([item for item in plan.experiments if item.kind is PlanNodeKind.UNCERTAINTY_CHECK]) == 3
    )


def test_formal_pipeline_accepts_only_registered_executor_and_verifies_checks(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str | None] = []

    def execute_variant(
        _backend: FormalP2ReplayBackend, intervention_id: str | None
    ) -> FormalReplayEvidence:
        calls.append(intervention_id)
        return _evidence(intervention_id)

    monkeypatch.setattr(FormalP2ReplayBackend, "execute_variant", execute_variant)
    monkeypatch.setattr(
        FormalP2ReplayBackend,
        "run_uncertainty",
        lambda *_args: (True, "sha256:" + "3" * 64),
    )
    adapter = FormalStrategyAttributionAdapter(ROOT)
    request = adapter.run_request()
    result = AttributionPipeline(tmp_path / "pipeline").run(
        request,
        FormalAttributionExecutor(ROOT, tmp_path / "replays"),
    )

    assert result.state is PipelineRunState.EVALUATED
    assert calls == [
        None,
        "intervention.position-risk-base-policy",
        "intervention.both-experts-fallback",
        "intervention.trend-stability-abstain",
    ]
    assert len(result.claims) == 3
    assert all(item.status.value == "valid" for item in result.claims)
