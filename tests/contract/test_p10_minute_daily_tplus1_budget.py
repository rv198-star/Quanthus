"""P10 accepted minute T / T+1 time-budget contract, not an activated service."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from quanthus.pipelines.daily_core_cycle import pending_market_dates

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/pipelines/minute-daily-tplus1-budget.v1.json"


def _policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def test_t_and_tplus1_are_independent_cumulative_budgets() -> None:
    policy = _policy()
    assert policy["schema_version"] == 1
    assert policy["policy_id"] == "p10-1m-daily-and-tplus1-bounded-ops-v1"
    scopes = policy["per_target_market_date"]
    assert scopes["T"]["routine_target_wall_seconds"] == 1800
    assert scopes["T"]["cumulative_hard_wall_seconds"] == 3600
    assert scopes["T_plus_1"]["cumulative_hard_wall_seconds"] == 3600
    assert scopes["combined_cumulative_hard_wall_seconds"] == 7200
    assert scopes["T_plus_1"]["window_not_reset_by_retry_or_midnight"] is True
    assert policy["immutable_accounting"]["shared_attempt_budget_not_reinitialized_by_restart"]
    assert policy["review_after_completed_market_sessions"] == 10


def test_tplus1_uses_exchange_calendar_not_weekdays() -> None:
    start = date(2026, 9, 30)
    final = date(2026, 10, 8)
    days = []
    current = start + timedelta(days=1)
    while current <= final:
        days.append(
            {"trade_date": current.isoformat(), "is_open": "1" if current == final else "0"}
        )
        current += timedelta(days=1)
    assert pending_market_dates(start, final, days) == [final]
    with pytest.raises(ValueError, match="missing daily-core trading date evidence"):
        pending_market_dates(start, final, days[:-1])


def test_tplus1_revision_probe_inspects_target_day_only() -> None:
    policy = _policy()
    phase = policy["per_target_market_date"]["T_plus_1"]
    assert 12 <= phase["source_maturity_probe_min_symbols"]
    assert phase["source_maturity_probe_max_symbols"] == 24
    assert "target_market_date_native_1m_observation_digest" in phase[
        "source_revision_comparison"
    ]
    assert phase["no_new_evidence_action"] == "stop_without_spending_remaining_budget"
    assert phase["next_trade_day_reference"] == (
        "authoritative_trading_calendar_not_weekday_arithmetic"
    )


def test_quality_route_never_promotes_r3_or_rejects_stored_r2() -> None:
    policy = _policy()
    grades = policy["grade_admission"]
    assert "formal_ODS_DWD" in grades["R2"]
    assert "quality_reasons" in grades["R2"]
    assert "no_formal_ODS_DWD" in grades["R3"]
    assert policy["independence"]["daily_core_unblocked_by_1m"]
    assert policy["independence"]["legacy_four_lane_unchanged"]
    assert policy["rollout_gate"].endswith("separate_production_cutover")
