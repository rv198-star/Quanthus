from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.event_library.contracts import (
    EventIdentity,
    EventRecord,
    SplitAssignment,
)
from quanthus.expert_evaluation.contracts import ExpertEvaluationSpec, ObservationApplicability
from quanthus.expert_evaluation.experts.trend_stability import (
    classify_trend_stability,
    evaluate_trend_stability,
    load_trend_stability_config,
)
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, decode_artifact

ROOT = Path(__file__).resolve().parents[2]


def _spec() -> ExpertEvaluationSpec:
    path = (
        ROOT
        / "config/expert-evaluations/specs/"
        "trend-stability-p4-first-passage.evaluation.v1.json"
    )
    return decode_artifact(path.read_bytes(), ExpertEvaluationSpec)


def _event() -> EventRecord:
    digest = "sha256:" + "a" * 64
    shanghai = ZoneInfo("Asia/Shanghai")
    return EventRecord(
        EventIdentity(
            "security-cn-000001",
            date(2020, 6, 30),
            "p4-event.price-volume-state-transition",
            "1.0.0",
            "snapshot-v1",
        ),
        digest,
        digest,
        datetime(2020, 6, 30, 15, tzinfo=shanghai),
        datetime(2020, 6, 30, 15, tzinfo=shanghai),
        digest,
        digest,
        "security-cn-000001:2020-06",
        "p4-event-date-split",
        "1.0.0",
        digest,
        SplitAssignment.DEVELOPMENT,
    )


def _feature(**changes: object) -> BaselineFeatureRow:
    values: dict[str, object] = {
        "market_date": date(2020, 6, 30),
        "security_id": "security-cn-000001",
        "close_price": Decimal("12"),
        "total_return_index": Decimal("120"),
        "sma20": Decimal("110"),
        "sma60": Decimal("100"),
        "momentum20": Decimal("0.1"),
        "prior_high20": Decimal("118"),
        "rsi14": Decimal("60"),
        "rsi14_previous": Decimal("58"),
        "volume_ratio20": Decimal("1.1"),
        "realized_volatility20": Decimal("0.2"),
        "daily_total_return": Decimal("0.01"),
        "average_amount20": Decimal("1000000"),
        "listing_sessions": 300,
        "feature_ready": True,
        "entry_eligible": True,
    }
    values.update(changes)
    return BaselineFeatureRow(**values)  # type: ignore[arg-type]


def _config() -> object:
    return load_trend_stability_config(
        ROOT / "config/expert-evaluations/experts/trend-stability.v1.json"
    )


def test_trend_expert_is_outcome_blind_ordinal_and_normalizes_utc() -> None:
    observation = evaluate_trend_stability(
        event=_event(), feature=_feature(), config=_config(), spec=_spec()  # type: ignore[arg-type]
    )

    assert observation.raw_value == "stable"
    assert observation.applicability is ObservationApplicability.APPLICABLE
    assert observation.knowledge_cutoff.isoformat() == "2020-06-30T07:00:00+00:00"
    assert "outcome" not in observation.canonical_bytes().decode("utf-8")


def test_trend_state_helper_reuses_exact_accepted_semantics() -> None:
    config = _config()
    assert classify_trend_stability(_feature(), config) == ("stable", "eligible")  # type: ignore[arg-type]
    assert classify_trend_stability(
        _feature(
            total_return_index=Decimal("90"),
            sma20=Decimal("95"),
            sma60=Decimal("100"),
        ),
        config,  # type: ignore[arg-type]
    ) == ("fragile", "eligible")
    assert classify_trend_stability(
        _feature(momentum20=Decimal("0")), config  # type: ignore[arg-type]
    ) == ("neutral", "eligible")
    assert classify_trend_stability(None, config) == (  # type: ignore[arg-type]
        None,
        "missing_required_t_close_trend_feature",
    )


def test_trend_expert_fragile_neutral_and_missing_are_explicit() -> None:
    event, config, spec = _event(), _config(), _spec()
    fragile = evaluate_trend_stability(
        event=event,
        feature=_feature(
            total_return_index=Decimal("90"),
            sma20=Decimal("95"),
            sma60=Decimal("100"),
        ),
        config=config,  # type: ignore[arg-type]
        spec=spec,
    )
    neutral = evaluate_trend_stability(
        event=event,
        feature=_feature(momentum20=Decimal("0")),
        config=config,  # type: ignore[arg-type]
        spec=spec,
    )
    abstained = evaluate_trend_stability(
        event=event, feature=None, config=config, spec=spec  # type: ignore[arg-type]
    )

    assert (fragile.raw_value, neutral.raw_value) == ("fragile", "neutral")
    assert abstained.applicability is ObservationApplicability.ABSTAINED
    assert abstained.raw_value is None


def test_trend_expert_rejects_feature_row_from_another_subject_or_date() -> None:
    with pytest.raises(ExpertEvaluationError, match="observation_feature_mismatch"):
        evaluate_trend_stability(
            event=_event(),
            feature=replace(_feature(), security_id="security-cn-000002"),
            config=_config(),  # type: ignore[arg-type]
            spec=_spec(),
        )
