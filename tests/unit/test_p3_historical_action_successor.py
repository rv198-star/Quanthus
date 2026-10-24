"""Late source is explicit retrospective evidence, never a general PIT waiver."""

from dataclasses import replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.backtest.contracts import MARKET_TIMEZONE, CorporateAction
from quanthus.backtest.engine import BacktestInputError
from quanthus.expert_strategy import p3_historical_action_successor as p


@pytest.fixture
def bound():
    info = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
    action = CorporateAction(
        "exact-official-action",
        "synthetic",
        date(2012, 5, 31),
        info,
        Decimal("0.25"),
        Decimal("0.5"),
        "official-source|evidence=sha256:" + "a" * 64,
        "new-source",
    )
    engine = p.SupplementedEngine(None, info, "effective-view", {action.action_id: action})
    run = SimpleNamespace(
        official=False, data_snapshot_id="new-source", code_version=p.h.VERSION + ":test"
    )
    applied = datetime.combine(action.effective_date, time(9), tzinfo=MARKET_TIMEZONE)
    return engine, run, action, applied


def test_exact_late_observation_allowed_only_in_bound_retrospective_engine(bound):
    engine, run, action, applied = bound
    engine._validate_action_clock(run, action, applied)
    assert action.known_at > applied
    assert action.known_at.year == 2026


@pytest.mark.parametrize(
    "mutation",
    [
        "snapshot",
        "cash",
        "ratio",
        "date",
        "security",
        "clock",
        "revision",
        "run",
        "official",
        "applied",
        "code",
    ],
)
def test_same_action_id_cannot_hide_changed_terms_clock_or_production(bound, mutation):
    engine, run, action, applied = bound
    if mutation == "snapshot":
        action = replace(action, snapshot_id="old")
    if mutation == "cash":
        action = replace(action, cash_dividend_per_share=Decimal("0.26"))
    if mutation == "ratio":
        action = replace(action, bonus_share_ratio=Decimal("1.5"))
    if mutation == "date":
        action = replace(action, effective_date=date(2012, 6, 1))
    if mutation == "security":
        action = replace(action, security_code="other")
    if mutation == "clock":
        action = replace(action, known_at=datetime(2012, 5, 30, tzinfo=timezone.utc))
    if mutation == "revision":
        action = replace(action, source_revision="forged")
    if mutation == "run":
        run.data_snapshot_id = "foreign"
    if mutation == "official":
        run.official = True
    if mutation == "applied":
        applied = applied.replace(hour=10)
    if mutation == "code":
        run.code_version = "other"
    with pytest.raises(BacktestInputError, match="EXACT_ACTION_BINDING"):
        engine._validate_action_clock(run, action, applied)


def test_unknown_action_still_uses_original_binding_gate(bound):
    engine, run, action, applied = bound
    with pytest.raises(BacktestInputError, match="HISTORY_ACTION_BINDING"):
        engine._validate_action_clock(run, replace(action, action_id="unknown"), applied)


def test_reader_rejects_cutoff_before_real_acquisition_before_opening_any_source(bound):
    engine, _, _, _ = bound
    from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts

    fact = ActionFacts(
        "synthetic",
        date(2012, 5, 30),
        date(2012, 5, 31),
        None,
        None,
        None,
        Decimal("0.25"),
        Decimal("0.5"),
        engine.cutoff,
        "revision",
        "a" * 64,
        "b" * 64,
    )
    with pytest.raises(ValueError, match="SOURCE_CLOCK"):
        p.SupplementedReader(object(), (fact,), datetime(2012, 5, 30, tzinfo=timezone.utc))


def test_original_modules_and_requests_remain_unchanged_and_new_request_bound():
    repository = Path(__file__).resolve().parents[2]
    original = p.prior.load_request(repository)
    request = p.load_request(repository)
    assert request["scope"] == original["scope"]
    assert request["arms"] == original["arms"]
    assert request["analysis"] == original["analysis"]
    assert request["optional_rights_policy"] == original["optional_rights_policy"]
    assert request["previous_failures_costs_and_deadlines_retained"]
    assert request["maximum_additional_wall_seconds"] == 7200
    assert not request["historical_PIT_claim"]
    assert not request["source_guards_waived"]
