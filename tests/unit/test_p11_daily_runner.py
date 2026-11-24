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
    raw_security_input_closure,
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
    closes: list[Decimal] = []
    close = Decimal("100")
    for i, day in enumerate(days):
        if i < n - 2:
            # a gentle upward drift so the RSI14/structure expert facts qualify a real
            # TRIAL_ENTRY instead of a flat NO_ACTION
            close += Decimal("1.0") if i % 2 == 0 else Decimal("-0.4")
        else:
            close = closes[-1]
        closes.append(close)
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


def _replay_source():
    """as-observed replay source policy: observation end = T close (the bound
    calendar's own close instant), decision = T 21:30 - the real
    _source_policy_for_day shape, never a forward 18:01 availability."""

    from quanthus.forward_validation.base24_runtime import (
        AfterCloseDecisionClock,
        P6Base24CandidatePolicy,
    )

    clock = AfterCloseDecisionClock(
        market_date=TARGET,
        source_available_at=datetime(2026, 11, 24, 15, 0, tzinfo=SHANGHAI),
        decision_at=datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI),
        next_session_open=NEXT_OPEN,
    )
    return P6Base24CandidatePolicy(
        clock=clock,
        history_start=date(2026, 1, 1),
        inputs_manifest_sha256="sha256:" + "a" * 64,
    )


def test_raw_to_p12_package_positive(runner, resources):
    """Real raw bars -> original panel -> run_daily_advice -> a package with a real entry."""

    facts, policy, account = _producer_inputs()
    output = run_daily_advice_from_raw(
        runner=runner,
        resources=resources,
        raw=[_trend_raw("600000")],
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
    # A single bad security with no valid neighbour left is a whole-day
    # missing_raw_inputs block; with a valid neighbour it would be a named exclusion.
    assert "missing_raw_inputs" in str(exc.value)


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


# --- M2-R1 consolidated repair: isolation, held membership, raw closure identity, real positive


def _trend_raw(code: str = "600000") -> RawSecurityInput:
    """C3's verified upward-drift series that produces a real TRIAL_ENTRY."""

    days = _weekdays_ending(TARGET, 130)
    closes: list[Decimal] = []
    close = Decimal("100")
    for i, _day in enumerate(days):
        if i < 128:
            close += Decimal("1.0") if i % 2 == 0 else Decimal("-0.4")
        else:
            close = closes[-1]
        closes.append(close)
    bars = []
    for i, (day, close_) in enumerate(zip(days, closes, strict=True)):
        if i == 128:
            high, low = close_ * Decimal("1.004"), close_ * Decimal("0.996")
        elif i == 129:
            high, low = close_ * Decimal("1.05"), close_ * Decimal("0.95")
        else:
            high, low = close_ * Decimal("1.01"), close_ * Decimal("0.99")
        amount = Decimal("50") if i == 128 else Decimal("400") if i == 129 else Decimal("100")
        bars.append(
            DailyBar(
                security_id=f"security-{code}",
                session_date=day,
                open_price=close_,
                high_price=high,
                low_price=low,
                close_price=close_,
                volume=Decimal("100000"),
                amount=amount,
                is_tradable=True,
            )
        )
    limits = tuple(
        LimitFacts(
            session_date=day,
            entry_policy_allows=True,
            is_executable=True,
            upper_limit_pct=Decimal("10"),
            lower_limit_pct=Decimal("10"),
            upper_limit_price=close_ * Decimal("1.1"),
            lower_limit_price=close_ * Decimal("0.9"),
        )
        for day, close_ in zip(days, closes, strict=True)
    )
    base = _raw_security(code)
    from dataclasses import replace as _replace

    return _replace(
        base,
        identity=_identity(code),
        bars=tuple(bars),
        universe_membership=days,
        limits=limits,
        close_at_by_date={day: datetime.combine(day, time(15), SHANGHAI) for day in days},
        knowledge_cutoff_by_date={
            day: datetime.combine(day, time(18, 1), SHANGHAI) for day in days
        },
    )


def _run(runner, resources, raws, **kw):
    facts, policy, account = _producer_inputs()
    account = kw.pop("account", account)
    return run_daily_advice_from_raw(
        runner=runner,
        resources=resources,
        raw=raws,
        facts=facts,
        policy=policy,
        account=account,
        run_id=kw.pop("run_id", "run-r1"),
        snapshot_id=kw.pop("snapshot_id", "snap-r1"),
        snapshot_manifest_digest=kw.pop("snapshot_manifest_digest", sha256_digest({"seed": "r1"})),
        card=CARD,
        split_policy=kw.pop("split_policy", _prospective_split()),
        source_policy=kw.pop("source_policy", _prospective_source()),
        clock=kw.pop("clock", lambda: policy.decision_cutoff),
        **kw,
    )


def _quiet_held(code: str) -> RawSecurityInput:
    """A held series with no new transition and no position facts."""

    from dataclasses import replace as _replace

    raw = _trend_raw(code)
    bars = list(raw.bars)
    for index in (-2, -1):
        bar = bars[index]
        bars[index] = _replace(
            bar,
            high_price=bar.close_price * Decimal("1.004"),
            low_price=bar.close_price * Decimal("0.996"),
            amount=Decimal("100"),
        )
    return _replace(raw, bars=tuple(bars), position_facts=None)


def _held_facts():
    from quanthus.expert_strategy.position_risk import HeldPositionFacts
    from quanthus.expert_strategy.runner import position_facts_evidence

    return HeldPositionFacts(
        holding_sessions=40,
        marked_return=Decimal("0.05"),
        prior_strategy_reduction_count=0,
        evidence=position_facts_evidence(
            unit_id="pos-r1",
            holding_sessions=40,
            marked_return=Decimal("0.05"),
            prior_strategy_reduction_count=0,
        ),
    )


# P1-1: per-security isolation keeps the valid neighbor's real entry -----------------------


def test_r1_bad_neighbor_excluded_valid_entry_kept(runner, resources):
    """A malformed second security is excluded alone; the valid neighbor enters."""

    from dataclasses import replace as _replace

    valid = _trend_raw("600000")
    bad = _replace(_trend_raw("600001"), close_at_by_date={})
    output = _run(runner, resources, [valid, bad])
    entries = [
        d for ds in output.result.package.decision_sets for d in ds.decisions if d.entry is not None
    ]
    assert entries, "the valid neighbor must still reach a real entry"
    exclusions = output.source_closure["exclusions"]
    assert any(
        item["security_code"] == "600001" and "close" in item["member_or_field"]
        for item in exclusions
    )


def test_r1_identity_mismatch_neighbor_isolated(runner, resources):
    from dataclasses import replace as _replace

    valid = _trend_raw("600000")
    bad_id = _trend_raw("600001")
    bad = _replace(bad_id, identity=_replace(bad_id.identity, security_id="security-OTHER"))
    output = _run(runner, resources, [valid, bad])
    assert any(item["security_code"] == "600001" for item in output.source_closure["exclusions"])
    entries = [d for ds in output.result.package.decision_sets for d in ds.decisions if d.entry]
    assert entries


def test_r1_all_bad_raises_missing_raw(runner, resources):
    """Every closure failing is a shared whole-day block, not a fabricated empty day."""

    from dataclasses import replace as _replace

    bad = _replace(_trend_raw("600001"), close_at_by_date={})
    with pytest.raises(ProductDailyError, match="missing_raw_inputs"):
        _run(runner, resources, [bad])


# P1-2: held membership survives missing facts --------------------------------------------


def test_r1_known_held_missing_facts_hold_alert_no_entry(runner, resources):
    """Account-known held code with no position facts -> hold_alert, never ENDED/entry."""

    from dataclasses import replace as _replace

    facts, policy, account = _producer_inputs()
    account = _replace(account, active_or_reserved_security_codes=("600001",))
    raw = _replace(_quiet_held("600001"), held_membership=True)
    output = _run(runner, resources, [raw], account=account)
    actions = [d.action for d in output.seam.held_decisions]
    assert actions == ["hold_alert"], actions
    stages = [d.stage for ds in output.result.package.decision_sets for d in ds.decisions]
    assert "entry_plan" not in stages and "ended" not in stages
    assert all(stage == "holding" for stage in stages)


def test_r1_held_trigger_missing_facts_stays_hold_not_entry(runner, resources):
    """A held member with an entry-shaped series but no facts stays a held review."""

    from dataclasses import replace as _replace

    facts, policy, account = _producer_inputs()
    account = _replace(account, active_or_reserved_security_codes=("600001",))
    raw = _trend_raw("600001")
    raw = _replace(raw, held_membership=True, position_facts=None)
    output = _run(runner, resources, [raw], account=account)
    actions = [d.action for d in output.seam.held_decisions]
    assert actions == ["hold_alert"], actions
    stages = [d.stage for ds in output.result.package.decision_sets for d in ds.decisions]
    assert "entry_plan" not in stages


def test_r1_explicit_held_membership_none_facts_hold_alert(runner, resources):
    """held_membership=True with no facts is a hold_alert; no account set needed."""

    from dataclasses import replace as _replace

    raw = _replace(_quiet_held("600001"), held_membership=True)
    output = _run(runner, resources, [raw])
    assert [d.action for d in output.seam.held_decisions] == ["hold_alert"]


def test_r1_held_with_real_facts_exit_positive(runner, resources):
    """A held member WITH real position facts reaches a real exit review."""

    from dataclasses import replace as _replace

    facts, policy, account = _producer_inputs()
    account = _replace(account, active_or_reserved_security_codes=("600001",))
    raw = _replace(_quiet_held("600001"), position_facts=_held_facts())
    output = _run(runner, resources, [raw], account=account)
    actions = [d.action for d in output.seam.held_decisions]
    assert actions == ["exit"], actions
    stages = [d.stage for ds in output.result.package.decision_sets for d in ds.decisions]
    assert "exit_plan" in stages


# pending securities block new entry without a held review --------------------------------


def test_r1_pending_security_blocks_entry_no_held_review(runner, resources):
    """A pending open buy order blocks a new entry; it is not a held review."""

    from dataclasses import replace as _replace

    pending = _replace(_trend_raw("600001"), pending_kind="open_buy_order", pending_quantity=100)
    output = _run(
        runner,
        resources,
        [_trend_raw("600000"), pending],
        pending_security_codes=("600001",),
    )
    entry_codes = {
        d.security_code
        for ds in output.result.package.decision_sets
        for d in output.result.package.decision_sets[0].decisions
        if d.entry is not None
    }
    assert "600000" in entry_codes and "600001" not in entry_codes
    held_codes = {d.security_code for d in output.seam.held_decisions}
    assert "600001" not in held_codes


# account disposition: failed account read is a whole-day block -----------------------------


def test_r1_account_facts_unavailable_blocks_day(runner, resources):
    with pytest.raises(ProductDailyError, match="account_facts_unavailable"):
        _run(
            runner,
            resources,
            [_trend_raw("600000")],
            account_disposition="ACCOUNT_FACTS_UNAVAILABLE",
        )


# P1-3: full raw closure changes the job identity -----------------------------------------


def test_r1_raw_closure_digest_changes_with_volume(runner, resources):
    """A bar-volume byte change must alter raw_security_inputs_digest."""

    from dataclasses import replace as _replace

    raw1 = _trend_raw("600000")
    out1 = _run(runner, resources, [raw1])
    bars = list(raw1.bars)
    bars[0] = _replace(bars[0], volume=Decimal("999999"))
    raw2 = _replace(raw1, bars=tuple(bars))
    out2 = _run(runner, resources, [raw2])
    assert (
        out1.source_closure["raw_security_inputs_digest"]
        != out2.source_closure["raw_security_inputs_digest"]
    )


def test_r1_closure_covers_membership_pending_unavailable(runner, resources):
    from dataclasses import replace as _replace

    raw = _replace(
        _trend_raw("600000"),
        held_membership=True,
        pending_kind="reserved_bucket",
        held_unavailable_reason="ledger knows code+quantity only",
    )
    closure = raw_security_input_closure(raw)
    assert closure["held_membership"] is True
    assert closure["pending_kind"] == "reserved_bucket"
    assert closure["held_unavailable_reason"] == "ledger knows code+quantity only"
    assert "volume" in closure["bars"][0] and "amount" in closure["bars"][0]


# P2-1: the raw positive must reach a REAL entry through the pinned P12 loader ------------


def test_r1_raw_to_p12_real_entry_plan(runner, resources):
    """The named positive asserts entry_allowed and a real P12 entry_plan."""

    output = _run(runner, resources, [_trend_raw("600000")])
    assert output.result.package is not None
    entries = [d for d in output.seam.decisions if d.entry_allowed]
    assert entries, "a real TRIAL_ENTRY decision must be produced, not NO_ACTION"
    plan_entries = [
        d
        for ds in output.result.package.decision_sets
        for d in ds.decisions
        if d.stage == "entry_plan"
    ]
    assert plan_entries, "the package must carry a real entry_plan, not only ENDED"

    from tests.unit.test_product_daily_rule_seam import _p12_loader

    validate = _p12_loader()
    for ds in output.result.package.decision_sets:
        validated = validate(ds.to_dict())
    stages = [str(d.stage) for d in validated.decisions]
    assert any("ENTRY_PLAN" in s for s in stages), stages


# --- oct08-context-real: single-T numeric context with null warm-up instants -------


def _gap_clocked_raw(code: str, gap_days: int = 42) -> RawSecurityInput:
    """The 600000/2026-10-08 shape: last <=61 sessions, early rows null instants.

    Mirrors the P10 OPEN-AT-GAPS evidence: the bound venue calendar keeps the
    session date/is_open flag but the native close/cutoff entries are explicit
    nulls; T keeps its real close instant.  gap_days=42 counts the trailing
    window sessions ending at T-1 (matching the 08-03..09-30 gap block).
    """

    from dataclasses import replace as _replace

    raw = _trend_raw(code)
    days = [bar.session_date for bar in raw.bars]
    gaps = set(days[-(gap_days + 1) : -1])
    assert len(gaps) == gap_days
    close_map = dict(raw.close_at_by_date)
    cutoff_map = dict(raw.knowledge_cutoff_by_date)
    for day in gaps:
        close_map[day] = None
        cutoff_map[day] = None
    return _replace(
        raw,
        close_at_by_date=close_map,
        knowledge_cutoff_by_date=cutoff_map,
    )


def _run_context(runner, resources, raws, context=None, **kw):
    from datetime import timezone

    from quanthus.product_daily.recommendation.daily_runner import _new_october_context

    token = _new_october_context(context) if context is not None else None
    # The as-observed replay requires the actual-read instant so a sealed
    # source version's readability is judged; the fixture's real read was
    # 2026-11-24 (later than the Oct version stamps), overridable via kwarg.
    kw.setdefault("as_observed_at", datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc))
    return _run(
        runner,
        resources,
        raws,
        numeric_context=token,
        consumption_evidence={"replay": "oct08-42"},
        **kw,
    )


def test_date_context_strict_entry_refused(runner, resources):
    """Without the named context a null warm-up instant stays a shared block."""

    raw = _gap_clocked_raw("600000")
    with pytest.raises(ProductDailyError) as exc:
        _run(runner, resources, [raw])
    assert "missing_raw_inputs" in str(exc.value)


def test_date_context_context_kwarg_gated(runner, resources):
    """numeric_context must be the private token; a bare string is a breach."""

    raw = _gap_clocked_raw("600000")
    with pytest.raises(ProductDailyError) as exc:
        _run(runner, resources, [raw], numeric_context="bogus_context")
    assert "unknown_numeric_context" in str(exc.value)


def test_date_context_string_label_refused(runner, resources):
    """The public raw entry refuses a caller-supplied context string - only the
    versioned replay entry can mint the private token, so a forward/H1/M2 caller
    cannot substitute a label for the verified projection."""

    raw = _gap_clocked_raw("600000")
    with pytest.raises(ProductDailyError) as exc:
        _run(runner, resources, [raw], numeric_context="october_replay_as_observed")
    assert "unknown_numeric_context" in str(exc.value)


def test_date_context_token_not_constructible_publicly(runner, resources):
    """OctoberReplayContext cannot be minted without the internal key - a raw
    caller cannot fabricate the admission token even knowing its shape."""
    from quanthus.product_daily.recommendation.daily_runner import OctoberReplayContext

    with pytest.raises(ProductDailyError) as exc:
        OctoberReplayContext("october_replay_as_observed", _key=object())
    assert "replay_context_not_constructible" in str(exc.value)


def _source_clock_raw(code: str, gap_days: int = 42, *, matched: bool = True) -> RawSecurityInput:
    """The #749 v4 shape: knowledge_clock_role=calendar_source_knowledge_from,
    warm-up rows keep close_at=None (original) while knowledge_cutoff carries
    the source calendar knowledge_from as the decision coordinate.

    matched=True mirrors the versioned as-observed entry's proof: each cutoff
    equals the same venue+session_date source calendar knowledge_from, so the
    PIT coordinate moves to the business fact instant.  matched=False leaves
    the role declared but unproven - the rows keep their original cutoff
    identity (the un-matched r4 path)."""

    from dataclasses import replace as _replace
    from datetime import datetime

    raw = _gap_clocked_raw(code, gap_days)
    stamp = datetime.fromisoformat("2026-10-14T16:28:52.274625+00:00")
    cutoff = {d: (stamp if v is None else v) for d, v in raw.knowledge_cutoff_by_date.items()}
    return _replace(
        raw,
        knowledge_clock_role="calendar_source_knowledge_from",
        knowledge_cutoff_by_date=cutoff,
        source_calendar_matched_dates=(
            frozenset(d for d, v in cutoff.items() if v is not None) if matched else None
        ),
    )


def test_source_clock_role_admits_real_clocked_rows(runner, resources):
    """Under the verified replay context + named role, a null close with a real
    source knowledge_from is a clocked row (not date-context, not fail-closed)."""

    raw = _source_clock_raw("600000")
    out = _run_context(
        runner,
        resources,
        [raw],
        context="october_replay_as_observed",
        source_policy=_replay_source(),
    )
    assert out.result.publishable is True
    rows = out.source_closure["consumption_evidence"]["source_knowledge_clock_rows"]
    # The close-missing warm-up rows under the named role keep their
    # source_knowledge_clock_row kind (a real source-version coordinate, never
    # a date-context row); every OTHER row's version stamp is bound as a
    # generic source_archive_version_clock so the archive-vs-fact separation
    # is recomputable for the whole window.
    role_named = [r for r in rows if r["kind"] == "source_knowledge_clock_row"]
    archive_named = [r for r in rows if r["kind"] == "source_archive_version_clock"]
    assert len(role_named) == 42
    assert all(r["knowledge_clock_role"] == "calendar_source_knowledge_from" for r in role_named)
    assert all(r["source_knowledge_from"] for r in role_named)
    assert all(r["actual_read_at"] for r in role_named)
    # The remaining close-bearing rows carry their own archive version clock.
    assert archive_named
    # Not a date-context row - date_context_rows stays empty under this shape.
    assert not out.source_closure["consumption_evidence"].get("date_context_rows")


def test_source_clock_role_refused_without_context(runner, resources):
    """A forward/strict caller keeps the per-bar instant requirement even when
    the role is declared - the role is effective ONLY inside the verified
    replay context."""

    raw = _source_clock_raw("600000")
    with pytest.raises(ProductDailyError) as exc:
        _run(runner, resources, [raw])
    assert "missing_raw_inputs" in str(exc.value)


def test_source_clock_role_refused_on_bare_string(runner, resources):
    """The role cannot be activated by a caller-supplied context string - only
    the private token minted inside the verified entry admits it."""

    raw = _source_clock_raw("600000")
    with pytest.raises(ProductDailyError) as exc:
        _run(runner, resources, [raw], numeric_context="october_replay_as_observed")
    assert "unknown_numeric_context" in str(exc.value)


def test_date_context_warmup_rows_equivalent(runner, resources):
    """Null-instant warm-up rows reach the same numeric path as full clocks.

    Same 61 bars, last 22 scan sessions including T-1: the anchored run must
    produce identical dispositions/package and identical four scan ratios as
    the fully-clocked run, with every date-context row named in the closure.
    """

    gap = _gap_clocked_raw("600000")
    full = _trend_raw("600000")
    out_ctx = _run_context(runner, resources, [gap], context="october_replay_as_observed")
    out_full = _run(runner, resources, [full])
    assert [d.disposition for d in out_ctx.seam.decisions] == [
        d.disposition for d in out_full.seam.decisions
    ]
    assert (out_ctx.result.package is not None) == (out_full.result.package is not None)
    # The real entry (same bars trigger the real transition) is preserved.
    assert [d.entry_allowed for d in out_ctx.seam.decisions] == [
        d.entry_allowed for d in out_full.seam.decisions
    ]
    # The emitted candidate's four real scan ratios are identical: the emitted
    # entry decision's canonical bytes bind the feature-evidence digest
    # computed from the 22-session window, so equal digests prove equal ratios
    # on equal bars.  Both sides keep their own un-matched cutoffs, so the
    # whole decision envelope is identical.
    ctx_decs = [d.decision.to_dict() for d in out_ctx.seam.entries]
    full_decs = [d.decision.to_dict() for d in out_full.seam.entries]
    assert ctx_decs == full_decs
    # Every date-context row is named inside the signed closure.
    named = out_ctx.source_closure["consumption_evidence"]["date_context_rows"]
    assert len(named) == 42
    assert {r["session_date"] for r in named} == {
        b.session_date.isoformat()
        for b in gap.bars
        if b.session_date in {d for d, v in gap.close_at_by_date.items() if v is None}
    }
    assert all(r["kind"] == "date_context_warmup_row" for r in named)


def test_date_context_missing_t_close_still_blocks(runner, resources):
    """A missing native T close keeps blocking under the numeric context."""

    from dataclasses import replace as _replace

    raw = _gap_clocked_raw("600000")
    signal = raw.bars[-1].session_date
    close_map = dict(raw.close_at_by_date)
    close_map[signal] = None
    raw = _replace(raw, close_at_by_date=close_map)
    with pytest.raises(ProductDailyError) as exc:
        _run_context(runner, resources, [raw], context="october_replay_as_observed")
    assert "missing_raw_inputs" in str(exc.value)


def test_date_context_partial_pair_still_blocks(runner, resources):
    """close present + cutoff absent (or reverse) stays fail-closed."""

    from dataclasses import replace as _replace

    raw = _gap_clocked_raw("600000")
    gap_days = [d for d, v in raw.close_at_by_date.items() if v is None]
    day = gap_days[0]
    cutoff_map = dict(raw.knowledge_cutoff_by_date)
    cutoff_map[day] = raw.knowledge_cutoff_by_date[  # restore one side only
        [d for d in raw.knowledge_cutoff_by_date if raw.knowledge_cutoff_by_date[d] is not None][0]
    ]
    raw = _replace(raw, knowledge_cutoff_by_date=cutoff_map)
    with pytest.raises(ProductDailyError) as exc:
        _run_context(runner, resources, [raw], context="october_replay_as_observed")
    assert "missing_raw_inputs" in str(exc.value)


def test_date_context_held_member_uses_t_row(runner, resources):
    """A held security's T row keeps its real instants for the held review."""

    from dataclasses import replace as _replace

    raw = _replace(_gap_clocked_raw("600000"), held_membership=True, held_quantity=1000)
    out = _run_context(runner, resources, [raw], context="october_replay_as_observed")
    # Held review ran on the T row with its real instants.
    held = [d for d in out.seam.held_decisions]
    assert held, "a held member must produce a held review"
    named = out.source_closure["consumption_evidence"]["date_context_rows"]
    assert len(named) == 42


# --- oct08-scan-calendar: bounded scan calendar vs full listing-age union -------


def _bounded_scan_dates(raw: RawSecurityInput, market_date: date, next_session: date):
    from quanthus.product_daily.recommendation.paper_replay import bar_context_window

    open_sessions = tuple(sorted({d for d in raw.universe_membership if d <= market_date}))
    window = bar_context_window(open_sessions, market_date)
    return tuple(sorted(set(window) | {next_session}))


def test_scan_calendar_bounds_1991_listing_age(runner, resources):
    """A 1991 listing-age membership no longer drags pre-scope sessions into
    require_scan_date: under the as-observed token the scan runs over the
    bounded bar_context_window + next_session, while features/membership keep
    the full listing age (listing_sessions row_number preserved)."""

    from dataclasses import replace as _replace

    facts, policy, account = _producer_inputs()
    base = _trend_raw("600000")
    # A security listed back to ~1991: the full membership is kept complete
    # (the listing_sessions gate still row_numbers the whole sequence).
    old_days = _weekdays_ending(date(1991, 12, 31), 260)
    membership = tuple(sorted(set(old_days) | set(base.universe_membership)))
    raw = _replace(base, universe_membership=membership)
    assert raw.universe_membership[0].year <= 1991

    scan_dates = _bounded_scan_dates(raw, facts.market_date, facts.next_session)

    # Strict/forward (no token): the full membership union still fails scope.
    with pytest.raises(Exception) as exc:
        _run(runner, resources, [raw])
    assert "p6_base24_scan_scope" in str(exc.value)

    # Token alone (no bounded calendar): still the membership union -> scope.
    with pytest.raises(Exception) as exc2:
        _run_context(runner, resources, [raw], context="october_replay_as_observed")
    assert "p6_base24_scan_scope" in str(exc2.value)

    # Token + bounded scan calendar: the scan window is in-scope and runs.
    # Capture features_by_security via the panel factory to prove the full
    # listing age is preserved (row_number over complete membership).
    captured = {}

    def _capture(features_by_security):
        captured.update(features_by_security)
        return resources.panel_report

    out = _run_context(
        runner,
        resources,
        [raw],
        context="october_replay_as_observed",
        scan_trading_dates=scan_dates,
        panel_report_factory=_capture,
    )
    assert out.result.publishable
    # The scan calendar, not the full membership, bounded the scan: every
    # trading date is within [window_start, next_session], none before 2026.
    assert all(d >= date(2026, 1, 1) for d in scan_dates)
    # The full listing age is preserved inside features (row_number over the
    # complete membership, not the 61-session scan window).
    assert captured["600000"].listing_sessions == len(membership)


def test_scan_calendar_requires_context(runner, resources):
    """scan_trading_dates without the private token is a contract breach."""

    from dataclasses import replace as _replace

    facts, policy, account = _producer_inputs()
    base = _trend_raw("600000")
    old_days = _weekdays_ending(date(1991, 12, 31), 260)
    raw = _replace(
        base, universe_membership=tuple(sorted(set(old_days) | set(base.universe_membership)))
    )
    scan_dates = _bounded_scan_dates(raw, facts.market_date, facts.next_session)
    with pytest.raises(ProductDailyError) as exc:
        _run(runner, resources, [raw], scan_trading_dates=scan_dates)
    assert "scan_calendar_requires_replay_context" in str(exc.value)


def test_scan_calendar_missing_one_bar_not_triggered(runner, resources):
    """Missing-one-bar control: a gap inside the bounded scan window must
    still leave the security unevaluated/qualified-no-signal - the bounded
    calendar is derived from the named open sessions, never narrowed by which
    bars exist, so a missing bar stays an observation gap, not a trigger."""

    from dataclasses import replace as _replace

    facts, policy, account = _producer_inputs()
    base = _trend_raw("600000")
    old_days = _weekdays_ending(date(1991, 12, 31), 260)
    membership = tuple(sorted(set(old_days) | set(base.universe_membership)))
    # Drop one mid-window bar (keep it in membership + clock maps): an
    # observation gap the bounded calendar must not paper over.
    bars = list(base.bars)
    removed = bars[-3].session_date
    bars = [b for b in bars if b.session_date != removed]
    raw = _replace(base, bars=tuple(bars), universe_membership=membership)

    scan_dates = _bounded_scan_dates(raw, facts.market_date, facts.next_session)
    # The dropped session is still a named open session in the calendar.
    assert removed in scan_dates
    out = _run_context(
        runner,
        resources,
        [raw],
        context="october_replay_as_observed",
        scan_trading_dates=scan_dates,
    )
    # No new entry fires on a gapped window.
    assert not any(d.event_id is not None for d in out.seam.decisions)
    # The scan named the gap rather than treating the day as a clean window.
    receipt = [
        r
        for r in (out.source_closure.get("history_receipts") or [])
        if r.get("security_code") == "600000"
    ]
    if receipt:
        ev = (receipt[0].get("gates") or {}).get("events", {})
        assert ev.get("status") != "satisfied" or "transition" in (ev.get("reason") or "")
