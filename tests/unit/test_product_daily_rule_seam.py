"""Synthetic seam tests: real scan + real expert + eligibility, with exclusion/block gates."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import CandidateDailyInput, scan_forward_candidates
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.policy import find_project_root
from quanthus.product_daily.recommendation.rule_seam import (
    SecurityFeatureFacts,
    evaluate_day,
)

ROOT = find_project_root()
CARD = load_candidate_rule_card(
    ROOT / "config" / "p4-candidate-price-volume-transition.research.v1.json"
)
SPLIT = load_split_policy(ROOT / "config" / "p4-event-split-policy.research.v1.json")
SOURCE = load_event_scan_source_policy(
    ROOT / "config" / "p4-event-scan-source.research.v1.json",
    release_path=ROOT / "config" / "releases" / "v2-build202607.json",
    provider_path=ROOT / "config" / "replay-data-provider.development.v2.json",
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SNAPSHOT_ID = "dwd-base-202607-v2.1+correction-20260813-001"
SNAPSHOT_MANIFEST = SOURCE.base_release.manifest_sha256


def _weekdays(start: date, end: date) -> tuple[date, ...]:
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return tuple(days)


TRADING_DATES = _weekdays(date(2012, 1, 1), date(2021, 12, 31))
CUTOFF = datetime(2020, 1, 6, 14, 59, tzinfo=SHANGHAI)


def _row(
    *, security: str, session_date: date, range_fraction: Decimal, turnover: Decimal
) -> CandidateDailyInput:
    open_price = Decimal("100.00")
    return CandidateDailyInput(
        security_id=f"security-{security}",
        listing_id=f"listing-{security}",
        listing_code=security,
        company_id=f"company-{security}",
        venue_id="SSE",
        instrument_type="common_equity",
        static_level1_industry_id="industry-01",
        session_date=session_date,
        close_at=datetime.combine(session_date, time(15, 0), SHANGHAI),
        knowledge_cutoff=datetime.combine(session_date, time(14, 59), SHANGHAI),
        open_price=open_price,
        high_price=open_price * (Decimal("1") + range_fraction),
        low_price=open_price,
        close_price=open_price,
        turnover=turnover,
        is_tradable=True,
        listing_sessions=200,
        adjustment_effective=False,
        source_evidence_digest=sha256_digest(
            {"security": security, "date": session_date.isoformat()}
        ),
    )


def _series(security: str, signal: int, *, trigger: bool) -> list[CandidateDailyInput]:
    """A quiet->active transition at index signal when trigger else uniformly quiet."""

    rows = []
    for index in range(signal - 21, signal + 1):
        if trigger and index == signal - 1:
            rf, tv = Decimal("0.005"), Decimal("50.00")
        elif trigger and index == signal:
            rf, tv = Decimal("0.02"), Decimal("200.00")
        else:
            rf, tv = Decimal("0.01"), Decimal("100.00")
        rows.append(
            _row(
                security=security, session_date=TRADING_DATES[index], range_fraction=rf, turnover=tv
            )
        )
    return rows


@pytest.fixture(scope="module")
def runner() -> FormalStrategyRunner:
    return FormalStrategyRunner(
        ExpertStrategyRegistry(ROOT).card, ExpertStrategyRegistry(ROOT).topology_manifest.digest
    )


def _features(code: str, rsi: Decimal | None, vol: Decimal | None) -> SecurityFeatureFacts:
    return SecurityFeatureFacts(
        security_code=code,
        signal_date=TRADING_DATES[100],
        rsi14=rsi,
        realized_volatility20=vol,
        listing_sessions=200,
    )


def test_bar_change_changes_real_event_output(runner):
    """The real scan emits an event on a quiet->active transition, none when uniformly quiet."""

    sig = 100
    trig = scan_forward_candidates(
        _series("600000", sig, trigger=True),
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
    )
    flat = scan_forward_candidates(
        _series("600000", sig, trigger=False),
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
    )
    assert trig.counters.candidate_events >= 1
    assert flat.counters.candidate_events == 0
    assert trig.candidates[0].event_id != ""  # a real EventRecord identity, not an injected string


def test_real_expert_veto_blocks_entry(runner):
    """rsi above the card threshold produces NO_ACTION -> no executable entry."""

    sig = 100
    rows = _series("600000", sig, trigger=True)
    feats = {"600000": _features("600000", Decimal("76"), Decimal("0.20"))}  # rsi above max
    result = evaluate_day(
        runner=runner,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
        trend_state="stable",
    )
    assert not result.day_blocked
    assert result.entries == ()  # veto -> no executable entry


def test_real_expert_entry_allowed_on_in_range_features(runner):
    sig = 100
    rows = _series("600000", sig, trigger=True)
    feats = {"600000": _features("600000", Decimal("60"), Decimal("0.20"))}
    result = evaluate_day(
        runner=runner,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
        trend_state="stable",
    )
    assert not result.day_blocked
    assert len(result.entries) >= 1
    assert result.entries[0].entry_allowed
    assert result.entries[0].target_maximum_position is not None
    assert result.entries[0].expert_decision_digest is not None


def test_single_security_exclusion(runner):
    """A security missing feature facts is excluded alone; others still produce entries."""

    sig = 100
    rows = _series("600000", sig, trigger=True) + _series("600001", sig, trigger=True)
    feats = {
        "600000": _features("600000", Decimal("60"), Decimal("0.20")),
        # 600001 missing entirely -> excluded
    }
    result = evaluate_day(
        runner=runner,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
        trend_state="stable",
    )
    assert not result.day_blocked
    assert {e["security_code"] for e in result.excluded} == {"600001"}
    assert {d.security_code for d in result.entries} == {"600000"}


def test_whole_day_block(runner):
    """A shared hard prerequisite failure produces a whole-day block, no package."""

    result = evaluate_day(
        runner=runner,
        rows=_series("600000", 100, trigger=True),
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security={"600000": _features("600000", Decimal("60"), Decimal("0.20"))},
        knowledge_cutoff=CUTOFF,
        trend_state="stable",
        shared_prereq_ok=False,
        shared_prereq_reason="calendar_or_identity_missing",
    )
    assert result.day_blocked
    assert result.day_block_reason == "calendar_or_identity_missing"
    assert result.entries == ()
