"""R2 ports keep information time distinct from historical business time."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.contracts import BacktestSignal, OrderSide
from quanthus.backtest.inputs import market_session_from_p1
from quanthus.contracts import SnapshotRef
from quanthus.event_library.contracts import (
    EventContractError,
    EventIdentity,
    EventRecord,
    SplitAssignment,
)
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_strategy import p3_base24_bridge as strict
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_bridge as retro
from tests.unit.test_p3_base24_bridge import clock, row, support

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "sha256:" + "a" * 64
INFO = datetime(2026, 10, 20, 23, tzinfo=native.TZ)
DAYS = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(22))


def observation(day, *, execution=False):
    r = row(day)
    r.update(
        instrument_type="common_equity",
        instrument_currency="CNY",
        observation_start=clock(day, 0),
        observation_end=clock(day + timedelta(days=1), 0),
        r2_information_cutoff=INFO,
        r2_business_read_through=date(2026, 9, 18),
        r2_observation_role="EXECUTION_ONLY" if execution else "DECISION_HISTORY",
        r2_calendar_status="OPEN",
        bar_knowledge_from=INFO - timedelta(days=1),
    )
    return r


def port(r, *, supported=True):
    s = replace(support(r), execution_known_at=INFO) if supported else None
    return retro.decision_port(
        r,
        information_cutoff=INFO,
        source_manifest_sha256=SOURCE,
        support=s,
        static_industry_id="static-L1",
    )


def feature(day):
    return BaselineFeatureRow(
        day,
        "security-native",
        Decimal("1"),
        *([Decimal("1")] * 11),
        150,
        True,
        True,
    )


def test_late_source_clock_and_next_midnight_interval_are_not_rewritten():
    r = observation(DAYS[-1])
    original = dict(r)
    p = port(r)
    assert r == original
    assert p.selected.candidate.knowledge_cutoff == r["bar_knowledge_from"]
    assert p.selected.selected_at == INFO
    assert p.selected.close_at == r["close_at"]
    assert p.selected.bar.close_price.as_tuple() == r["close"].as_tuple()
    assert p.business_date == DAYS[-1] and p.information_cutoff == INFO
    with pytest.raises(native.InputError, match="BRIDGE_NOT_VISIBLE"):
        strict.decision_port(
            r,
            cutoff=clock(DAYS[-1]),
            source_manifest_sha256=SOURCE,
            support=None,
            static_industry_id="static-L1",
        )


@pytest.mark.parametrize("kind", ["missing", "closed", "future", "venue", "currency"])
def test_invalid_or_unavailable_session_is_not_filled(kind):
    r = observation(DAYS[-1])
    if kind == "missing":
        r.update(open_at=None, close_at=None)
    elif kind == "closed":
        r["r2_calendar_status"] = "CLOSED"
    elif kind == "future":
        r["bar_knowledge_from"] = INFO + timedelta(seconds=1)
    elif kind == "venue":
        r["mic_code"] = "BJSE"
    else:
        r["instrument_currency"] = None
    if kind in {"missing", "closed"}:
        p = port(r, supported=False)
        assert p.selected is None and p.unavailable_reasons
        assert r["open_at"] is None if kind == "missing" else r["r2_calendar_status"] == "CLOSED"
    else:
        with pytest.raises(native.InputError):
            port(r, supported=False)


def test_complete_case_mask_uses_snapshot_clock_and_is_identical_for_all_variants():
    ports = tuple(port(observation(d)) for d in DAYS)
    card = load_candidate_rule_card(
        ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    mask = retro.common_entry_mask(
        ports,
        expected_dates=DAYS,
        card=card,
        feature=feature(DAYS[-1]),
        feature_known_at=INFO,
        feature_evidence_digest=SOURCE,
    )
    assert mask.eligible
    assert [mask.for_variant(v) for v in strict.VARIANTS] == [True] * 4
    with pytest.raises(native.InputError, match="BRIDGE_T_CLOSE_REQUIRED"):
        strict.common_entry_mask(
            [p.selected for p in ports],
            expected_dates=DAYS,
            card=card,
            feature=feature(DAYS[-1]),
            feature_known_at=INFO,
            feature_evidence_digest=SOURCE,
        )


def test_missing_support_or_observation_blocks_affected_entry():
    ports = tuple(port(observation(d)) for d in DAYS)
    card = load_candidate_rule_card(
        ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    for selected in (ports[:-1], (*ports[:-1], port(observation(DAYS[-1]), supported=False))):
        mask = retro.common_entry_mask(
            selected,
            expected_dates=DAYS,
            card=card,
            feature=feature(DAYS[-1]),
            feature_known_at=INFO,
            feature_evidence_digest=SOURCE,
        )
        assert not mask.eligible
        assert all(not mask.for_variant(v) for v in strict.VARIANTS)


def test_execution_tail_only_enters_P1_P2_execution_interface():
    day = date(2026, 9, 18)
    p = port(observation(day, execution=True))
    assert p.selected.candidate is None
    snapshot = SnapshotRef(
        market_date=day, knowledge_cutoff=INFO, source_revision=SOURCE, snapshot_id="r2-execution"
    )
    evidence = retro.execution_evidence(
        p,
        snapshot=snapshot,
        decision_date=date(2026, 9, 17),
        next_session_date=day,
        trading_dates=(date(2026, 9, 17), day),
    )
    assert evidence.snapshot.knowledge_cutoff == INFO
    assert evidence.open_at == p.selected.open_at
    assert evidence.open_price == p.selected.bar.open_price
    with pytest.raises(native.InputError, match="RETRO_EXECUTION_ROLE"):
        retro.execution_evidence(
            port(observation(DAYS[-1])),
            snapshot=snapshot,
            decision_date=date(2026, 9, 17),
            next_session_date=day,
            trading_dates=(date(2026, 9, 17), day),
        )


def test_support_binding_and_snapshot_cannot_be_swapped():
    r = observation(DAYS[-1])
    with pytest.raises(native.InputError, match="BRIDGE_SUPPORT_BINDING"):
        retro.decision_port(
            r,
            information_cutoff=INFO,
            source_manifest_sha256=SOURCE,
            support=replace(support(r), row_sha256="sha256:" + "b" * 64),
            static_industry_id="static-L1",
        )
    r["r2_information_cutoff"] = INFO - timedelta(days=1)
    with pytest.raises(native.InputError, match="RETRO_PORT_BOUNDARY"):
        port(r, supported=False)


def test_historical_execution_role_is_separate_and_uses_the_exact_next_session():
    day, before = date(2025, 2, 10), date(2025, 2, 7)
    p = port(observation(day))
    snapshot = SnapshotRef(
        market_date=day, knowledge_cutoff=INFO, source_revision=SOURCE, snapshot_id="r2-execution"
    )
    evidence = retro.execution_evidence(
        p,
        snapshot=snapshot,
        decision_date=before,
        next_session_date=day,
        trading_dates=(before, day),
    )
    session = market_session_from_p1(evidence)
    assert session.security_code == p.security_id and session.session_date == day
    assert p.selected.candidate is not None and not p.selected.execution_only
    with pytest.raises(native.InputError, match="RETRO_EXECUTION_NEXT_SESSION"):
        retro.execution_evidence(
            p,
            snapshot=snapshot,
            decision_date=before,
            next_session_date=day,
            trading_dates=(before, date(2025, 2, 8), day),
        )


def test_unknown_support_is_not_promoted_by_native_tradability_flags():
    day, before = date(2025, 2, 10), date(2025, 2, 7)
    r = observation(day)
    assert r["can_buy"] is r["can_sell"] is True
    p = port(r, supported=False)
    snapshot = SnapshotRef(
        market_date=day, knowledge_cutoff=INFO, source_revision=SOURCE, snapshot_id="r2-execution"
    )
    evidence = retro.execution_evidence(
        p,
        snapshot=snapshot,
        decision_date=before,
        next_session_date=day,
        trading_dates=(before, day),
    )
    assert not evidence.is_executable and not evidence.entry_policy_allows
    assert "EXECUTION_QUALIFICATION_UNAVAILABLE" in p.unavailable_reasons


def test_feature_information_after_snapshot_does_not_pass_shared_mask():
    ports = tuple(port(observation(d)) for d in DAYS)
    card = load_candidate_rule_card(
        ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    mask = retro.common_entry_mask(
        ports,
        expected_dates=DAYS,
        card=card,
        feature=feature(DAYS[-1]),
        feature_known_at=INFO + timedelta(seconds=1),
        feature_evidence_digest=SOURCE,
    )
    assert not mask.eligible and "FEATURE_CLOCK_UNAVAILABLE" in mask.reasons


def test_utc_source_instants_retain_the_Shanghai_business_date():
    r = observation(DAYS[-1])
    for field in ("observation_start", "observation_end", "open_at", "close_at"):
        r[field] = r[field].astimezone(timezone.utc)
    assert r["observation_start"].date() != r["trade_date"]
    p = port(r)
    assert p.business_date == DAYS[-1]
    assert p.selected.open_at == r["open_at"] and p.selected.close_at == r["close_at"]


@pytest.mark.parametrize("volume", [Decimal("0.0000000000"), None])
def test_zero_and_null_liquidity_are_retained_without_qualification(volume):
    r = observation(DAYS[-1])
    r["volume"] = volume
    p = port(r)
    assert p.selected.bar.volume == volume
    assert not p.selected.bar.entry_policy_allows and not p.selected.bar.is_executable


def test_R2_port_cannot_be_relabelled_as_strict_event_or_historical_signal():
    p = port(observation(DAYS[-1]))
    c = p.selected.candidate
    with pytest.raises(EventContractError, match="must not be after T close"):
        EventRecord(
            identity=EventIdentity(p.security_id, p.business_date, "rule", "1", "r2"),
            candidate_rule_digest=SOURCE,
            snapshot_manifest_digest=SOURCE,
            knowledge_cutoff=c.knowledge_cutoff,
            signal_close=c.close_at,
            eligibility_evidence_digest=SOURCE,
            feature_window_manifest_digest=SOURCE,
            isolation_group_id="group",
            split_policy_id="policy",
            split_policy_version="1",
            split_policy_digest=SOURCE,
            split_assignment=SplitAssignment.PERMANENT_BLIND,
        )
    snapshot = SnapshotRef(
        market_date=p.business_date, knowledge_cutoff=INFO, source_revision=SOURCE, snapshot_id="r2"
    )
    with pytest.raises(ValueError, match="signal decision cannot precede snapshot"):
        BacktestSignal(
            signal_id="r2-test",
            snapshot=snapshot,
            decision_at=c.close_at,
            profile_id="close-to-next-open-v1",
            window_id=None,
            security_code=p.security_id,
            side=OrderSide.BUY,
            unit_id="unit",
            quantity=100,
            aggregate_score=Decimal("1"),
            reason_code="synthetic",
            decision_version="v1",
        )
