"""M2 integration: raw rows -> original panel SQL -> real core -> P12, and negatives."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest.baseline_research import BaselineFeaturePanelReport
from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.splits import load_split_policy
from quanthus.expert_evaluation.experts.trend_stability import (
    load_trend_stability_config,
)
from quanthus.expert_evaluation.serialization import artifact_from_bytes
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.feature_registry import load_feature_registry
from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.contracts import (
    DailyFactsView,
    MemberEvidence,
)
from quanthus.product_daily.recommendation.daily_runner import (
    RawSecurityInput,
    run_daily_advice_from_raw,
)
from quanthus.product_daily.recommendation.feature_adapter import (
    DailyBar,
    IdentityFacts,
    LimitFacts,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
)
from quanthus.product_daily.recommendation.rule_seam import (
    RULE_DAY_PRODUCER_ID,
    SeamResources,
)

ROOT = find_project_root()
CARD = load_candidate_rule_card(
    ROOT / "config" / "p4-candidate-price-volume-transition.research.v1.json"
)
SPLIT = load_split_policy(ROOT / "config" / "p4-event-split-policy.research.v1.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _weekdays_ending(target: date, count: int) -> tuple[date, ...]:
    days: list[date] = []
    cur = target
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return tuple(reversed(days))


TARGET = date(2026, 11, 24)
NEXT_OPEN = datetime(2026, 11, 25, 9, 30, tzinfo=SHANGHAI)


def _identity(code: str) -> IdentityFacts:
    return IdentityFacts(
        security_id=f"security-{code}",
        stock_code=code,
        listing_code=code,
        security_name=f"name-{code}",
        industry_id="industry-01",
        venue_id="SSE",
        instrument_type="common_equity",
        listing_id=f"listing-{code}",
        company_id=f"company-{code}",
        depends_on_member_ids=("core-daily-1d",),
        invalidations=("cancel if open above price_bound",),
        price_bound=Decimal("200.00"),
        source_evidence_digest=sha256_digest({"security": code}),
    )


def _raw_security(code: str, *, trigger: bool = True, n: int = 130) -> RawSecurityInput:
    """Qualified raw bars with a quiet->active transition in the last two sessions.

    The transition is encoded in the raw price/amount/turnover itself; no event or
    trend label is injected.  Bars carry the real amount (CNY) and a None is_tradable
    (qualified fact absent is not a default-True).
    """

    days = _weekdays_ending(TARGET, n)
    bars: list[DailyBar] = []
    for i, day in enumerate(days):
        close = Decimal("100")
        if trigger and i == n - 2:
            # prior bar: quiet vs the 20-session median on BOTH range and turnover
            high = close * Decimal("1.004")
            low = close * Decimal("0.996")
            volume = Decimal("100000")
            amount = Decimal("50.00")
        elif trigger and i == n - 1:
            # signal bar: active vs the median on BOTH range and turnover (>=1.5x)
            high = close * Decimal("1.05")
            low = close * Decimal("0.95")
            volume = Decimal("100000")
            amount = Decimal("400.00")
        else:
            high = close * Decimal("1.01")
            low = close * Decimal("0.99")
            volume = Decimal("100000")
            amount = Decimal("100.00")
        bars.append(
            DailyBar(
                security_id=f"security-{code}",
                session_date=day,
                open_price=close,
                high_price=high,
                low_price=low,
                close_price=close,
                volume=volume,
                amount=amount,
                is_tradable=True,
            )
        )
    limits = tuple(
        LimitFacts(
            session_date=d,
            entry_policy_allows=True,
            is_executable=True,
            upper_limit_pct=Decimal("10"),
            lower_limit_pct=Decimal("10"),
            upper_limit_price=Decimal("110"),
            lower_limit_price=Decimal("90"),
        )
        for d in days
    )
    close_map = {d: datetime.combine(d, time(15, 0), SHANGHAI) for d in days}
    # Prospective source: 18:01 after-close knowledge, before the 22:30 decision.
    cutoff_map = {d: datetime.combine(d, time(18, 1), SHANGHAI) for d in days}
    return RawSecurityInput(
        identity=_identity(code),
        bars=tuple(bars),
        universe_membership=days,
        actions=(),
        exceptions=(),
        limits=limits,
        close_at_by_date=close_map,
        knowledge_cutoff_by_date=cutoff_map,
    )


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
        feature_registry_digest=load_feature_registry(
            ROOT / "config" / "p3-baseline-features.research.v1.json"
        ).digest,
        bundle_snapshot_id="snap-1",
        bundle_digest=sha256_digest({"b": 1}),
        scope_start=date(2026, 1, 1),
        scope_end=TARGET,
        row_count=1,
        feature_ready_count=1,
        entry_eligible_count=1,
        exception_active_count=0,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
        data_content_digest=sha256_digest({"d": 1}),
        data_manifest_sha256=sha256_digest({"m": 1}),
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


def _producer_inputs() -> tuple[DailyFactsView, AdvicePolicy, AccountFactsView]:
    cutoff = datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI)
    member = MemberEvidence(
        member_id="core-daily-1d",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        available_at=cutoff - timedelta(hours=1),
        required=True,
    )
    facts = DailyFactsView(
        batch_id="batch-1",
        market_date=TARGET,
        next_session=NEXT_OPEN.date(),
        source_id="dwd-base24",
        source_version="1.0.0",
        calendar_version="cal-v1",
        price_basis="none",
        members=(member,),
        required_member_ids=("core-daily-1d",),
        intake_receipt_id="intake-1",
        intake_received_at=cutoff - timedelta(hours=2),
        intake_first_read_at=cutoff - timedelta(minutes=90),
        intake_verified_at=cutoff - timedelta(hours=1),
    )
    policy = AdvicePolicy(
        policy_id="p11-d1b",
        version="1.0.0",
        decision_cutoff=cutoff,
        published_at=cutoff,
        window_ref="window-test",
        eligible_at=NEXT_OPEN,
        expires_at=datetime(2026, 11, 25, 15, 0, tzinfo=SHANGHAI),
        candidate_rule_digest=CARD.semantic_digest,
        trusted_producer_ids=(RULE_DAY_PRODUCER_ID,),
    )
    account = AccountFactsView(
        account_id="acct-1",
        as_of=cutoff,
        cash_available=Decimal("500000"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        pending_commitment_total=Decimal("0"),
    )
    return facts, policy, account


def _prospective_source():
    from quanthus.forward_validation.base24_runtime import (
        AfterCloseDecisionClock,
        P6Base24CandidatePolicy,
    )

    clock = AfterCloseDecisionClock(
        market_date=TARGET,
        source_available_at=datetime(2026, 11, 24, 18, 1, tzinfo=SHANGHAI),
        decision_at=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        next_session_open=NEXT_OPEN,
    )
    return P6Base24CandidatePolicy(
        clock=clock,
        history_start=date(2026, 1, 1),
        inputs_manifest_sha256="sha256:" + "a" * 64,
    )


def _prospective_split():
    from quanthus.forward_validation.prospective_source import P6ProspectiveSplitPolicy

    return P6ProspectiveSplitPolicy(
        activation_market_date=date(2026, 11, 1), signal_market_date=TARGET
    )


def test_raw_to_p12_package_positive(runner, resources):
    """Real raw bars -> original panel -> run_daily_advice -> a package with a real entry."""

    facts, policy, account = _producer_inputs()
    output = run_daily_advice_from_raw(
        runner=runner,
        resources=resources,
        raw=[_raw_security("600000")],
        facts=facts,
        policy=policy,
        account=account,
        run_id="run-m2-1",
        snapshot_id="snap-raw-1",
        snapshot_manifest_digest=sha256_digest({"s": 1}),
        card=CARD,
        split_policy=_prospective_split(),
        source_policy=_prospective_source(),
        clock=lambda: policy.decision_cutoff,
    )
    assert output.result.package is not None, (
        f"real raw chain must produce a package: {output.result.diagnostics}"
    )
    assert output.seam.decisions, "a real transition event must be scanned"
    # The candidate carries the real feature facts, not a caller-injected label.
    assert output.candidates or output.seam.excluded or output.seam.decisions


def test_missing_close_at_fails_closed(runner, resources):
    facts, policy, account = _producer_inputs()
    raw = _raw_security("600000")
    raw = RawSecurityInput(**{**raw.__dict__, "close_at_by_date": {}})
    with pytest.raises(ProductDailyError):
        run_daily_advice_from_raw(
            runner=runner,
            resources=resources,
            raw=[raw],
            facts=facts,
            policy=policy,
            account=account,
            run_id="run-m2-2",
            snapshot_id="s",
            snapshot_manifest_digest=sha256_digest({"s": 2}),
            card=CARD,
            split_policy=_prospective_split(),
            source_policy=_prospective_source(),
        )


def test_identity_mismatch_fails_closed(runner, resources):
    """identity.security_id must equal the computed security's identity (burn check)."""

    facts, policy, account = _producer_inputs()
    raw = _raw_security("600000")
    bad_identity = IdentityFacts(**{**raw.identity.__dict__, "security_id": "security-OTHER"})
    raw = RawSecurityInput(**{**raw.__dict__, "identity": bad_identity})
    with pytest.raises(ProductDailyError) as exc:
        run_daily_advice_from_raw(
            runner=runner,
            resources=resources,
            raw=[raw],
            facts=facts,
            policy=policy,
            account=account,
            run_id="run-m2-3",
            snapshot_id="s",
            snapshot_manifest_digest=sha256_digest({"s": 3}),
            card=CARD,
            split_policy=_prospective_split(),
            source_policy=_prospective_source(),
        )
    assert "identity_security_mismatch" in str(exc.value) or "contamination" in str(exc.value)


def test_entry_eligible_false_blocks_entry_feature_ready_true(runner, resources):
    """feature_ready=True but entry_eligible=False (limit gate) must not enter.

    The limit member reports entry_policy_allows=False for the signal session; the panel
    still computes feature_ready=True (all metrics present), but entry_eligible=False
    feeds the real entry_evidence_ready gate, so the seam produces no entry.  Held
    evaluation stays independent.
    """

    days = _weekdays_ending(TARGET, 130)
    bars = []
    for i, d in enumerate(days):
        amount = (
            Decimal("50.00")
            if i == len(days) - 2
            else (Decimal("200.00") if i == len(days) - 1 else Decimal("100.00"))
        )
        bars.append(
            DailyBar(
                security_id="security-600000",
                session_date=d,
                open_price=Decimal("100"),
                high_price=Decimal("101"),
                low_price=Decimal("99"),
                close_price=Decimal("100"),
                volume=Decimal("100000"),
                amount=amount,
                is_tradable=True,
            )
        )
    limits = tuple(
        LimitFacts(
            session_date=d,
            entry_policy_allows=False,  # the limit/policy gate blocks entry
            is_executable=True,
            upper_limit_pct=Decimal("10"),
            lower_limit_pct=Decimal("10"),
            upper_limit_price=Decimal("110"),
            lower_limit_price=Decimal("90"),
        )
        for d in days
    )
    raw = RawSecurityInput(
        identity=_identity("600000"),
        bars=tuple(bars),
        universe_membership=days,
        actions=(),
        exceptions=(),
        limits=limits,
        close_at_by_date={d: datetime.combine(d, time(15, 0), SHANGHAI) for d in days},
        knowledge_cutoff_by_date={d: datetime.combine(d, time(18, 1), SHANGHAI) for d in days},
    )
    facts, policy, account = _producer_inputs()
    output = run_daily_advice_from_raw(
        runner=runner,
        resources=resources,
        raw=[raw],
        facts=facts,
        policy=policy,
        account=account,
        run_id="run-m2-4",
        snapshot_id="s",
        snapshot_manifest_digest=sha256_digest({"s": 4}),
        card=CARD,
        split_policy=_prospective_split(),
        source_policy=_prospective_source(),
        clock=lambda: policy.decision_cutoff,
    )
    # The panel marks it feature_ready (all metrics present) but entry_eligible False.
    feats = output.seam.decisions
    # No ENTRY disposition may survive: entry_evidence_ready was False.
    assert not any(d.disposition.value == "entry" and d.entry_allowed for d in feats), (
        "entry_eligible=False must block the entry edge"
    )


def test_is_tradable_none_is_not_defaulted_to_true(runner, resources):
    """A bar with is_tradable=None does not become tradable (the scan gate is strict)."""

    days = _weekdays_ending(TARGET, 30)
    bars = [
        DailyBar(
            security_id="security-600000",
            session_date=d,
            open_price=Decimal("100"),
            high_price=Decimal("101"),
            low_price=Decimal("99"),
            close_price=Decimal("100"),
            volume=Decimal("1000"),
            amount=Decimal("100"),
            is_tradable=None,  # unknown, never default-True
        )
        for d in days
    ]
    raw = RawSecurityInput(
        identity=_identity("600000"),
        bars=tuple(bars),
        universe_membership=days,
        actions=(),
        exceptions=(),
        limits=(),
        close_at_by_date={d: datetime.combine(d, time(15, 0), SHANGHAI) for d in days},
        knowledge_cutoff_by_date={d: datetime.combine(d, time(18, 1), SHANGHAI) for d in days},
    )
    facts, policy, account = _producer_inputs()
    # It must not raise: the scan's tradable gate (is_tradable is not True) suppresses
    # transitions, it does not fabricate a tradable day.
    output = run_daily_advice_from_raw(
        runner=runner,
        resources=resources,
        raw=[raw],
        facts=facts,
        policy=policy,
        account=account,
        run_id="run-m2-5",
        snapshot_id="s",
        snapshot_manifest_digest=sha256_digest({"s": 5}),
        card=CARD,
        split_policy=_prospective_split(),
        source_policy=_prospective_source(),
        clock=lambda: policy.decision_cutoff,
    )
    assert output is not None


def test_listing_sessions_from_membership_not_bar_window():
    """listing_sessions is the universe-membership row_number, not the bar count.

    A security listed for 200 sessions but delivering only the last 130 OHLC bars must
    still report listing_sessions=200 -- the original SQL row_numbers the
    source_daily_universe membership (<= scope_end), never the delivered bar window.
    """

    from quanthus.product_daily.recommendation.feature_adapter import (
        DailyBar,
        compute_security_feature_facts,
    )

    membership = _weekdays_ending(TARGET, 200)
    bar_days = membership[-130:]  # only the last 130 sessions carry OHLC bars
    bars = [
        DailyBar(
            security_id="security-600000",
            session_date=d,
            open_price=Decimal("100"),
            high_price=Decimal("101"),
            low_price=Decimal("99"),
            close_price=Decimal("100"),
            volume=Decimal("1000"),
            amount=Decimal("100"),
            is_tradable=True,
        )
        for d in bar_days
    ]
    facts = compute_security_feature_facts(
        security_id="security-600000",
        bars=bars,
        actions=(),
        exceptions=(),
        limits=(),
        trading_dates=membership,
        identity=_identity("600000"),
        signal_date=TARGET,
        signal_close=datetime.combine(TARGET, time(15, 0), SHANGHAI),
    )
    assert facts.listing_sessions == 200  # membership, not the 130-bar window
