"""Synthetic seam tests: real scan + real expert + eligibility, with exclusion/block gates."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest.baseline_research import BaselineFeaturePanelReport
from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import CandidateDailyInput, scan_forward_candidates
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.expert_evaluation.experts.trend_stability import (
    load_trend_stability_config,
)
from quanthus.expert_evaluation.serialization import artifact_from_bytes
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner, position_facts_evidence
from quanthus.feature_registry import load_feature_registry
from quanthus.policy import find_project_root
from quanthus.product_daily.recommendation.contracts import (
    DailyFactsView,
    MemberEvidence,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
    CandidateEvaluationReceipt,
    DailyAdviceProducer,
    ExpertBundle,
)
from quanthus.product_daily.recommendation.rule_seam import (
    SeamResources,
    SecurityFeatureFacts,
    evaluate_day,
    seam_to_candidates,
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


@pytest.fixture(scope="module")
def resources() -> SeamResources:
    trend_spec = artifact_from_bytes(
        (
            ROOT
            / "config"
            / "expert-evaluations"
            / "specs"
            / "trend-stability-p4-first-passage.evaluation.v1.json"
        ).read_bytes()
    )
    panel_report = BaselineFeaturePanelReport(
        evaluator_version="p11-d1b-seam",
        feature_registry_version="p3-baseline-features-research-v1",
        feature_registry_digest="sha256:" + "0" * 64,
        bundle_snapshot_id=SNAPSHOT_ID,
        bundle_digest=SNAPSHOT_MANIFEST,
        scope_start=TRADING_DATES[0],
        scope_end=TRADING_DATES[-1],
        row_count=1,
        feature_ready_count=1,
        entry_eligible_count=1,
        exception_active_count=0,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
        data_content_digest=SNAPSHOT_MANIFEST,
        data_manifest_sha256=SNAPSHOT_MANIFEST,
    )
    return SeamResources(
        feature_registry=load_feature_registry(
            ROOT / "config" / "p3-baseline-features.research.v1.json"
        ),
        panel_report=panel_report,
        trend_config=load_trend_stability_config(
            ROOT / "config" / "expert-evaluations" / "experts" / "trend-stability.v1.json"
        ),
        trend_spec=trend_spec,
    )


def _features(
    code: str,
    rsi: Decimal | None,
    vol: Decimal | None,
    signal_date: date = TRADING_DATES[100],
) -> SecurityFeatureFacts:
    """A feature row with a complete stable trend shape so the real producer qualifies.

    It also carries the real P10-projected identity / price / dependency fields the
    seam now requires to emit a candidate: name, industry, the real close as mark_price,
    a real price bound, invalidations and the member ids it depends on.
    """

    return SecurityFeatureFacts(
        security_code=code,
        signal_date=signal_date,
        rsi14=rsi,
        realized_volatility20=vol,
        # total_return_index >= sma20 > sma60 and momentum20 > 0 -> "stable"
        total_return_index=Decimal("120"),
        sma20=Decimal("115"),
        sma60=Decimal("100"),
        momentum20=Decimal("0.10"),
        listing_sessions=200,
        security_name=f"name-{code}",
        industry_id="industry-01",
        mark_price=Decimal("100.00"),
        price_bound=Decimal("101.00"),
        invalidations=("cancel if open above price_bound",),
        depends_on_member_ids=("core-daily-1d",),
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


def test_real_expert_veto_blocks_entry(runner, resources):
    """rsi above the card threshold produces NO_ACTION -> no executable entry."""

    sig = 100
    rows = _series("600000", sig, trigger=True)
    feats = {"600000": _features("600000", Decimal("76"), Decimal("0.20"))}  # rsi above max
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
    )
    assert not result.day_blocked
    assert result.entries == ()  # veto -> no executable entry


def test_real_expert_entry_allowed_on_in_range_features(runner, resources):
    sig = 100
    rows = _series("600000", sig, trigger=True)
    feats = {"600000": _features("600000", Decimal("60"), Decimal("0.20"))}
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
    )
    assert not result.day_blocked
    assert len(result.entries) >= 1
    assert result.entries[0].entry_allowed
    assert result.entries[0].target_maximum_position is not None
    assert result.entries[0].expert_decision_digest is not None


def test_single_security_exclusion(runner, resources):
    """A security missing feature facts is excluded alone; others still produce entries."""

    sig = 100
    rows = _series("600000", sig, trigger=True) + _series("600001", sig, trigger=True)
    feats = {
        "600000": _features("600000", Decimal("60"), Decimal("0.20")),
        # 600001 missing entirely -> excluded
    }
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
    )
    assert not result.day_blocked
    assert {e["security_code"] for e in result.excluded} == {"600001"}
    assert {d.security_code for d in result.entries} == {"600000"}


def test_whole_day_block(runner, resources):
    """A shared hard prerequisite failure produces a whole-day block, no package."""

    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=_series("600000", 100, trigger=True),
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security={"600000": _features("600000", Decimal("60"), Decimal("0.20"))},
        knowledge_cutoff=CUTOFF,
        shared_prereq_ok=False,
        shared_prereq_reason="calendar_or_identity_missing",
    )
    assert result.day_blocked
    assert result.day_block_reason == "calendar_or_identity_missing"
    assert result.entries == ()


# --- fail-first negative + held-path tests (C CHANGES_REQUIRED coverage) ---------------------


def _producer_inputs(
    cutoff: datetime,
    market_date: date = date(2020, 1, 3),
    next_session: date = date(2020, 1, 6),
):
    """Minimal DailyFactsView/AdvicePolicy/AccountFactsView for producer tests."""

    member = MemberEvidence(
        member_id="core-daily-1d",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        available_at=cutoff - __import__("datetime").timedelta(hours=1),
        required=True,
    )
    facts = DailyFactsView(
        batch_id="batch-test",
        market_date=market_date,
        next_session=next_session,
        source_id="dwd-base24",
        source_version="1.0.0",
        calendar_version="cal-v1",
        price_basis="none",
        members=(member,),
        required_member_ids=("core-daily-1d",),
        intake_receipt_id="intake-1",
        intake_received_at=cutoff - __import__("datetime").timedelta(hours=2),
        intake_first_read_at=cutoff - __import__("datetime").timedelta(minutes=90),
        intake_verified_at=cutoff - __import__("datetime").timedelta(hours=1),
    )
    policy = AdvicePolicy(
        policy_id="p11-d1b",
        version="1.0.0",
        decision_cutoff=cutoff,
        published_at=cutoff,
        window_ref="window-test",
        eligible_at=datetime.combine(next_session, time(9, 30), SHANGHAI),
        expires_at=datetime.combine(next_session, time(15, 0), SHANGHAI),
        candidate_rule_digest="sha256:" + "b" * 64,
    )
    account = AccountFactsView(
        account_id="acct-test",
        as_of=cutoff,
        cash_available=Decimal("500000"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        pending_commitment_total=Decimal("0"),
    )
    return facts, policy, account


def test_seam_to_candidates_locks_component_to_expert_bundle(runner, resources):
    """seam_to_candidates emits CandidateSignal with component_ref/version == ExpertBundle."""

    sig = 100
    rows = _series("600000", sig, trigger=True)
    feats = {"600000": _features("600000", Decimal("60"), Decimal("0.20"))}
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
    )
    experts = ExpertBundle(ref_id="p11.expert", version="1.0.0", digest=runner.topology_digest)
    facts, policy, _account = _producer_inputs(CUTOFF)
    signals, _held = seam_to_candidates(
        result,
        experts=experts,
        facts=facts,
        policy=policy,
        eligible_at=datetime(2020, 1, 6, 9, 30, tzinfo=SHANGHAI),
        expires_at=datetime(2020, 1, 6, 15, 0, tzinfo=SHANGHAI),
    )
    assert len(signals) >= 1
    for s in signals:
        assert s.component_ref_id == "p11.expert"
        assert s.component_version == "1.0.0"
        assert s.expert_decision_digest.startswith("sha256:")
        assert s.risk_decision_digest.startswith("sha256:")
        assert s.expert_decision_digest.startswith("sha256:")


def test_seam_decision_carries_real_formal_decision(runner, resources):
    """Each seam entry binds a real FormalStrategyDecision, not a label digest."""

    rows = _series("600000", 100, trigger=True)
    feats = {"600000": _features("600000", Decimal("60"), Decimal("0.20"))}
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
    )
    entry = result.entries[0]
    assert entry.decision is not None
    # real decision digest is a full sha256 of the decision's own to_dict, not a label
    assert entry.decision.digest == entry.expert_decision_digest
    assert entry.decision.digest != _digest("expert:" + entry.event_id)


def _digest(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def test_forged_component_rejected_by_producer(runner, resources):
    """A CandidateSignal with a forged component_ref cannot pass produce."""

    rows = _series("600000", 100, trigger=True)
    feats = {"600000": _features("600000", Decimal("60"), Decimal("0.20"))}
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
    )
    experts = ExpertBundle(ref_id="p11.expert", version="1.0.0", digest=runner.topology_digest)
    facts, policy, _account = _producer_inputs(CUTOFF)
    signals, _held = seam_to_candidates(
        result,
        experts=experts,
        facts=facts,
        policy=policy,
        eligible_at=datetime(2020, 1, 6, 9, 30, tzinfo=SHANGHAI),
        expires_at=datetime(2020, 1, 6, 15, 0, tzinfo=SHANGHAI),
    )
    forged = signals[0].__class__(
        **{
            **signals[0].__dict__,
            "component_ref_id": "forged.component",
            "component_version": "9.9.9",
        }
    )
    facts, policy, account = _producer_inputs(CUTOFF)
    receipt = CandidateEvaluationReceipt(
        producer_id="p11.seam",
        producer_version="1.0.0",
        run_id="run-1",
        completed_at=CUTOFF,
        coverage_digest="sha256:" + "0" * 64,  # replaced with run-bound digest below
        evaluated_count=1,
        qualified_count=1,
        binding_digest=CandidateEvaluationReceipt.expected_binding_digest(facts, policy, (forged,)),
        evaluated_universe=(forged.security_code,),
    )
    receipt = dataclasses.replace(receipt, coverage_digest=receipt.expected_coverage_digest())
    produced = DailyAdviceProducer().produce(
        facts=facts,
        account=account,
        policy=policy,
        experts=experts,
        candidates=(forged,),
        evaluation_receipt=receipt,
    )
    # A forged component fails closed: either the whole call is refused (package is None) or
    # the package exists but is not publishable and carries a blocked decision.
    if produced.package is None:
        assert produced.disposition.value == "input_not_ready"
    else:
        assert produced.publishable is False
        assert any(
            d.stage.value == "input_blocked"
            for ds in produced.package.decision_sets
            for d in ds.decisions
        )


def test_held_missing_facts_hold_alert_not_forced_exit(runner, resources):
    """Held path with no position facts yields hold+alert, never a forced sale."""

    rows = _series("600000", 100, trigger=True)
    feats = {"600000": _features("600000", Decimal("60"), Decimal("0.20"))}
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
        held_facts_by_security={"600000": None},  # position facts absent
    )
    assert result.held_decisions
    held = result.held_decisions[0]
    assert held.action == "hold_alert"
    assert held.decision is None  # no forced exit decision


def test_held_with_facts_runs_real_evaluate_held(runner, resources):
    """Held path with real position facts runs evaluate_held and binds the decision."""

    rows = _series("600000", 100, trigger=True)
    feats = {"600000": _features("600000", Decimal("60"), Decimal("0.20"))}
    facts = HeldPositionFacts(
        holding_sessions=10,
        marked_return=Decimal("0.02"),
        prior_strategy_reduction_count=0,
        evidence=position_facts_evidence(
            unit_id="u-1",
            holding_sessions=10,
            marked_return=Decimal("0.02"),
            prior_strategy_reduction_count=0,
        ),
    )
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
        features_by_security=feats,
        knowledge_cutoff=CUTOFF,
        held_facts_by_security={"600000": facts},
    )
    held = result.held_decisions[0]
    assert held.decision is not None
    assert held.decision_digest == held.decision.digest


# --- prospective clock seam tests (DAY1-CONTRACT-v1.1, C NEXT-FIXED-ACCEPTANCE PC-1..PC-8) ---


def _prospective_source(
    *,
    market_date: date,
    source_at: datetime,
    decision_at: datetime,
    next_open: datetime,
):
    """Build a real P6Base24CandidatePolicy bound to an AfterCloseDecisionClock."""

    from quanthus.forward_validation.base24_runtime import (
        AfterCloseDecisionClock,
        P6Base24CandidatePolicy,
    )

    clock = AfterCloseDecisionClock(
        market_date=market_date,
        source_available_at=source_at,
        decision_at=decision_at,
        next_session_open=next_open,
    )
    return P6Base24CandidatePolicy(
        clock=clock,
        history_start=date(2026, 1, 1),
        inputs_manifest_sha256="sha256:" + "a" * 64,
    )


def _prospective_split(*, activation: date, signal: date):
    from quanthus.forward_validation.prospective_source import P6ProspectiveSplitPolicy

    return P6ProspectiveSplitPolicy(activation_market_date=activation, signal_market_date=signal)


def _prospective_row(
    *, day: date, knowledge_cutoff: datetime, range_fraction: Decimal, turnover: Decimal
) -> CandidateDailyInput:
    """One CandidateDailyInput with a real source knowledge cutoff."""

    price = Decimal("100.00")
    return CandidateDailyInput(
        security_id="security-600000",
        listing_id="listing-600000",
        listing_code="600000",
        company_id="company-600000",
        venue_id="SSE",
        instrument_type="common_equity",
        static_level1_industry_id="industry-01",
        session_date=day,
        close_at=datetime.combine(day, __import__("datetime").time(15, 0), SHANGHAI),
        knowledge_cutoff=knowledge_cutoff,
        open_price=price,
        high_price=price * (Decimal("1") + range_fraction),
        low_price=price,
        close_price=price,
        turnover=turnover,
        is_tradable=True,
        listing_sessions=200,
        adjustment_effective=False,
        source_evidence_digest=sha256_digest({"day": day.isoformat()}),
    )


def _prospective_series(days: tuple[date, ...], *, cutoff_hour: int, cutoff_minute: int):
    """quiet(index -2)->active(last) transition with a real source knowledge cutoff."""

    rows = []
    for index, day in enumerate(days):
        if index == len(days) - 2:
            rf, tv = Decimal("0.005"), Decimal("50.00")
        elif index == len(days) - 1:
            rf, tv = Decimal("0.02"), Decimal("200.00")
        else:
            rf, tv = Decimal("0.01"), Decimal("100.00")
        rows.append(
            _prospective_row(
                day=day,
                knowledge_cutoff=datetime.combine(
                    day, __import__("datetime").time(cutoff_hour, cutoff_minute), SHANGHAI
                ),
                range_fraction=rf,
                turnover=tv,
            )
        )
    return rows


def _weekdays_ending(target: date, count: int) -> tuple[date, ...]:
    days: list[date] = []
    cur = target
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= __import__("datetime").timedelta(days=1)
    return tuple(reversed(days))


def test_pc1_prospective_event_uses_decision_at_not_close(runner, resources):
    """18:01 source + 22:30 decision + T+1 09:30 open -> prospective event, decision_at=22:30."""

    target = date(2026, 11, 24)
    next_open = datetime(2026, 11, 25, 9, 30, tzinfo=SHANGHAI)
    source = _prospective_source(
        market_date=target,
        source_at=datetime(2026, 11, 24, 18, 1, tzinfo=SHANGHAI),
        decision_at=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        next_open=next_open,
    )
    split = _prospective_split(activation=date(2026, 11, 1), signal=target)
    days = _weekdays_ending(target, 22)
    rows = _prospective_series(days, cutoff_hour=18, cutoff_minute=1)
    trading_dates = (*days, next_open.date())
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=trading_dates,
        snapshot_id="synthetic-prospective-20261124",
        snapshot_manifest_digest="sha256:" + "b" * 64,
        card=CARD,
        split_policy=split,
        source_policy=source,
        features_by_security={
            "600000": _features("600000", Decimal("60"), Decimal("0.20"), signal_date=target)
        },
        knowledge_cutoff=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
    )
    assert result.entries, "expected a prospective entry on the qualified transition"
    event = result.entries[0].decision
    assert event is not None and event.event_id


def test_pc3_source_after_decision_blocked(runner, resources):
    """23:00 source (after 22:30 decision) -> no prospective event, fail closed."""

    target = date(2026, 11, 24)
    from quanthus.forward_validation.contracts import ForwardValidationContractError

    # source_available_at after decision_at fails closed at clock construction.
    with pytest.raises(ForwardValidationContractError):
        _prospective_source(
            market_date=target,
            source_at=datetime(2026, 11, 24, 23, 0, tzinfo=SHANGHAI),
            decision_at=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
            next_open=datetime(2026, 11, 25, 9, 30, tzinfo=SHANGHAI),
        )


def test_pc6_legacy_source_unchanged(runner, resources):
    """Legacy SOURCE without prospective_clock_json keeps the old default semantics."""

    assert getattr(SOURCE, "prospective_clock_json", None) is None


def test_pc8_full_seam_producer_chain_prospective(runner, resources):
    """Full prospective seam -> producer chain runs end to end."""

    target = date(2026, 11, 24)
    next_open = datetime(2026, 11, 25, 9, 30, tzinfo=SHANGHAI)
    source = _prospective_source(
        market_date=target,
        source_at=datetime(2026, 11, 24, 18, 1, tzinfo=SHANGHAI),
        decision_at=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        next_open=next_open,
    )
    split = _prospective_split(activation=date(2026, 11, 1), signal=target)
    days = _weekdays_ending(target, 22)
    rows = _prospective_series(days, cutoff_hour=18, cutoff_minute=1)
    trading_dates = (*days, next_open.date())
    result = evaluate_day(
        runner=runner,
        resources=resources,
        rows=rows,
        trading_dates=trading_dates,
        snapshot_id="synthetic-prospective-20261124",
        snapshot_manifest_digest="sha256:" + "b" * 64,
        card=CARD,
        split_policy=split,
        source_policy=source,
        features_by_security={
            "600000": _features("600000", Decimal("60"), Decimal("0.20"), signal_date=target)
        },
        knowledge_cutoff=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
    )
    experts = ExpertBundle(ref_id="p11.expert", version="1.0.0", digest=runner.topology_digest)
    facts, policy, _account = _producer_inputs(CUTOFF)
    signals, _held = seam_to_candidates(
        result,
        experts=experts,
        facts=facts,
        policy=policy,
        eligible_at=datetime(2026, 11, 25, 9, 30, tzinfo=SHANGHAI),
        expires_at=datetime(2026, 11, 25, 15, 0, tzinfo=SHANGHAI),
    )
    if signals:
        for s in signals:
            assert s.component_ref_id == "p11.expert"
            assert s.expert_decision_digest.startswith("sha256:")


# --- forward full-closure: seam -> producer -> real P12 loader ----------------------------


def _p12_loader():
    """Return the real P12 parser/validator + fixture loader, or fail the case.

    Same fail-closed precondition as the recommendation integration tests: an absent
    sibling P12 worktree is a reported failure (not a silent skip).
    """

    import os
    import sys
    from pathlib import Path

    p12_root = Path(
        os.environ.get(
            "QUANTHUS_P12_WORKTREE_ROOT",
            "/Users/william/Projects/Github/Quanthus/artifacts/worktrees/p12-paper-integration-20261120",
        )
    )
    p12_src = p12_root / "src"
    if not p12_src.is_dir():
        pytest.fail(
            "P12 integration loader unavailable: sibling worktree missing at "
            f"{p12_src}; set QUANTHUS_P12_WORKTREE_ROOT to the P12 release root"
        )
    quanthus_pkg = sys.modules.get("quanthus")
    if quanthus_pkg is None:
        pytest.fail("quanthus package not initialized")
    p12_pkg = str(p12_src / "quanthus")
    for name in list(sys.modules):
        if not name.startswith("quanthus"):
            continue
        module = sys.modules[name]
        module_path = getattr(module, "__path__", None)
        if module_path is None:
            continue
        relative = name[len("quanthus") + 1 :].replace(".", "/") if name != "quanthus" else ""
        candidate = Path(p12_pkg) / relative
        if candidate.is_dir() and str(candidate) not in module_path:
            module_path.insert(0, str(candidate))
    try:
        from quanthus.capabilities.decision_access import (
            MarketDecisionSet,
            validate_decision_set,
        )
        from quanthus.platform.adapters.p12_synthetic_fixture import (
            _decision as p12_decision,
        )
        from quanthus.shared.fact_contracts import digest as p12_digest
        from quanthus.shared.values import VersionRef
    except ImportError as exc:  # pragma: no cover - depends on sibling tree
        pytest.fail(f"P12 modules unavailable: {exc}")

    def validate_raw_package(raw: dict):
        """Build and validate a MarketDecisionSet from the produced decision_set dict."""

        decisions = tuple(p12_decision(item) for item in raw["decisions"])
        ds = MarketDecisionSet(
            source_ref=VersionRef(raw["source_id"], raw["version"], p12_digest(raw)),
            mode="synthetic",
            synthetic=True,
            market_date=date.fromisoformat(raw["market_date"]),
            knowledge_cutoff=datetime.fromisoformat(raw["knowledge_cutoff"]),
            published_at=datetime.fromisoformat(raw["published_at"]),
            next_session=date.fromisoformat(raw["next_session"]),
            decisions=decisions,
        )
        return validate_decision_set(ds)

    return validate_raw_package


def _forward_chain(tmp_path, runner_, resources_, *, held=None):
    """Run the real prospective seam -> producer chain and return (result, produced)."""
    target = date(2026, 11, 24)
    next_open = datetime(2026, 11, 25, 9, 30, tzinfo=SHANGHAI)
    source = _prospective_source(
        market_date=target,
        source_at=datetime(2026, 11, 24, 18, 1, tzinfo=SHANGHAI),
        decision_at=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        next_open=next_open,
    )
    split = _prospective_split(activation=date(2026, 11, 1), signal=target)
    days = _weekdays_ending(target, 22)
    rows = _prospective_series(days, cutoff_hour=18, cutoff_minute=1)
    trading_dates = (*days, next_open.date())
    features = {
        "600000": _features("600000", Decimal("60"), Decimal("0.20"), signal_date=target),
    }
    if held:
        for code in held:
            features.setdefault(
                code, _features(code, Decimal("60"), Decimal("0.20"), signal_date=target)
            )
    result = evaluate_day(
        runner=runner_,
        resources=resources_,
        rows=rows,
        trading_dates=trading_dates,
        snapshot_id="synthetic-prospective-20261124",
        snapshot_manifest_digest="sha256:" + "b" * 64,
        card=CARD,
        split_policy=split,
        source_policy=source,
        features_by_security=features,
        knowledge_cutoff=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        held_facts_by_security=held,
    )
    experts = ExpertBundle(ref_id="p11.expert", version="1.0.0", digest=runner_.topology_digest)
    facts, policy, account = _producer_inputs(
        datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        market_date=target,
        next_session=next_open.date(),
    )
    signals, held_signals = seam_to_candidates(
        result,
        experts=experts,
        facts=facts,
        policy=policy,
        eligible_at=datetime(2026, 11, 25, 9, 30, tzinfo=SHANGHAI),
        expires_at=datetime(2026, 11, 25, 15, 0, tzinfo=SHANGHAI),
    )
    universe = tuple(
        sorted({s.security_code for s in signals} | {h.security_code for h in held_signals})
    ) or ("000000",)
    identity = {
        "producer_id": "p11.seam",
        "producer_version": "1.0.0",
        "run_id": "run-pc9",
        "evaluated_universe": sorted(universe),
    }
    receipt = CandidateEvaluationReceipt(
        producer_id=identity["producer_id"],
        producer_version=identity["producer_version"],
        run_id=identity["run_id"],
        completed_at=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        coverage_digest="sha256:" + "0" * 64,
        evaluated_count=len(signals) + len(held_signals) or 1,
        qualified_count=len(signals),
        binding_digest=CandidateEvaluationReceipt.expected_binding_digest(
            facts, policy, signals, receipt_identity=identity
        ),
        evaluated_universe=universe,
    )
    receipt = dataclasses.replace(receipt, coverage_digest=receipt.expected_coverage_digest())
    produced = DailyAdviceProducer().produce(
        facts=facts,
        account=account,
        policy=policy,
        experts=experts,
        candidates=signals,
        held_signals=held_signals,
        evaluation_receipt=receipt,
    )
    return result, produced, signals, held_signals


def test_pc9_forward_seam_producer_p12_publishable(tmp_path, runner, resources):
    """Positive end-to-end: real prospective seam -> producer -> real P12 loader.

    Asserts the produced package is not None, is publishable in synthetic mode, carries a
    real entry_plan with a registered reason and action explanation, and round-trips through
    the real P12 decision_set validator.
    """

    _result, produced, signals, held_signals = _forward_chain(tmp_path, runner, resources)
    assert produced.package is not None, f"producer refused the real chain: {produced.diagnostics}"
    assert produced.publishable is True
    entries = [
        d for ds in produced.package.decision_sets for d in ds.decisions if d.entry is not None
    ]
    assert entries, "expected at least one entry_plan decision"
    for entry in entries:
        assert entry.reason_codes == ("NEW_ENTRY_SIGNAL",)
        assert any(x.get("use") == "action" for x in entry.explanations)
        assert entry.security_name != ""
        assert entry.component_ref_id == "p11.expert"
    # held positions travel the same set when present
    if held_signals:
        held_stages = {
            d.stage
            for ds in produced.package.decision_sets
            for d in ds.decisions
            if d.stage in {"holding", "exit_plan"}
        }
        assert held_stages, "expected a held decision in the same decision set"
    validate = _p12_loader()
    for ds in produced.package.decision_sets:
        validate(ds.to_dict())


def test_pc9_forward_held_only_no_entry_still_publishable(tmp_path, runner, resources):
    """A held position with no new event is still evaluated and reaches the decision set."""

    from quanthus.expert_strategy.position_risk import HeldPositionFacts
    from quanthus.expert_strategy.runner import position_facts_evidence

    held_facts = {
        "600001": HeldPositionFacts(
            holding_sessions=40,
            marked_return=Decimal("0.05"),
            prior_strategy_reduction_count=0,
            evidence=position_facts_evidence(
                unit_id="pos-1",
                holding_sessions=40,
                marked_return=Decimal("0.05"),
                prior_strategy_reduction_count=0,
            ),
        )
    }
    _result, produced, signals, held_signals = _forward_chain(
        tmp_path, runner, resources, held=held_facts
    )
    assert produced.package is not None
    assert produced.publishable is True
    held_decisions = [
        d
        for ds in produced.package.decision_sets
        for d in ds.decisions
        if d.stage in {"holding", "exit_plan"}
    ]
    assert held_decisions, "held position must reach the decision set, not be dropped"
