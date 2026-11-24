"""E1 end-to-end seam: real P11 bindings + missing-anchor October envelope.

Issue #718 / 8792-phase1.  Exercises the FULL consumer path a real October
replay day takes: load_p11_static (registry card + fixed resources) ->
build_p11_bindings (21:30 decision cutoff, equivalent_day anchor) ->
P11HistoryRunner -> run_daily_advice_from_history -> a publishable real
ENTRY_PLAN, with each missing open_at session resolved by the local-day-bound
equivalent-day proof and NAMED on the source_closure membership_proof.

The panel_report here is a bound placeholder (real panel evaluator output is
injected by the replay driver); every other object is the fixed production
shape REFERENCE.md prescribes.  The raw-history envelope is the real October
Base shape - every is_open session lacks a stored open_at anchor.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest.baseline_research import BaselineFeaturePanelReport
from quanthus.event_library.contracts import sha256_digest
from quanthus.forward_validation.base24_runtime import (
    AfterCloseDecisionClock,
    P6Base24CandidatePolicy,
)
from quanthus.forward_validation.prospective_source import (
    P6ProspectiveSplitPolicy,
)
from quanthus.p12_daily.p11_bindings import (
    build_p11_bindings,
    load_p11_static,
    replay_decision_cutoff,
)
from quanthus.p12_daily.p11_history_runner import P11HistoryRunner
from quanthus.p12_daily.rule_runner import RunnerRequest
from quanthus.policy import find_project_root
from quanthus.product_daily.recommendation.contracts import (
    DailyFactsView,
    MemberEvidence,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
)
from quanthus.product_daily.recommendation.rule_seam import (
    RULE_DAY_PRODUCER_ID,
)
from tests.unit.test_p11_history_input import (
    _cal_null_anchor,
    _envelope,
    _listing,
    _security_entry,
)
from tests.unit.test_p11_daily_runner import (
    NEXT_OPEN,
    TARGET,
    _trend_raw,
    _weekdays_ending,
)

ROOT = find_project_root()
SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPIRES = datetime(2026, 11, 25, 15, 0, tzinfo=SHANGHAI)


@pytest.fixture(scope="module")
def static():
    return load_p11_static(ROOT)


def _bindings(static, cutoff):
    member = MemberEvidence(
        member_id="core-daily-1d",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        available_at=datetime.combine(TARGET, time(18, 1), SHANGHAI),
        required=True,
    )
    facts = DailyFactsView(
        batch_id="b1",
        market_date=TARGET,
        next_session=NEXT_OPEN.date(),
        source_id="dwd-base24",
        source_version="1.0.0",
        calendar_version="cal-v1",
        price_basis="none",
        members=(member,),
        required_member_ids=("core-daily-1d",),
        intake_receipt_id="i1",
        intake_received_at=cutoff,
        intake_first_read_at=cutoff,
        intake_verified_at=cutoff,
    )
    policy = AdvicePolicy(
        policy_id="p11-d1b",
        version="1.0.0",
        decision_cutoff=cutoff,
        published_at=cutoff,
        window_ref="w",
        eligible_at=NEXT_OPEN,
        expires_at=EXPIRES,
        candidate_rule_digest=static.card.semantic_digest,
        trusted_producer_ids=(RULE_DAY_PRODUCER_ID,),
    )
    account = AccountFactsView(
        account_id="a1",
        as_of=cutoff,
        cash_available=Decimal("500000"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        pending_commitment_total=Decimal("0"),
    )
    panel = BaselineFeaturePanelReport(
        evaluator_version="p11-d1b-seam",
        feature_registry_version="p3-baseline-features-research-v1",
        feature_registry_digest=static.feature_registry.digest,
        bundle_snapshot_id="snap",
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
    clock = AfterCloseDecisionClock(
        market_date=TARGET,
        source_available_at=datetime.combine(TARGET, time(18, 1), SHANGHAI),
        decision_at=cutoff,
        next_session_open=NEXT_OPEN,
    )
    return build_p11_bindings(
        static=static,
        panel_report=panel,
        facts=facts,
        policy=policy,
        account=account,
        split_policy=P6ProspectiveSplitPolicy(
            activation_market_date=date(2026, 10, 1), signal_market_date=TARGET
        ),
        source_policy=P6Base24CandidatePolicy(
            clock=clock,
            history_start=date(2026, 1, 1),
            inputs_manifest_sha256="sha256:" + "a" * 64,
        ),
        snapshot_id="snap-h1",
        snapshot_manifest_digest=sha256_digest({"h": 1}),
        clock=lambda: cutoff,
        anchor_mode="equivalent_day",
    )


def _october_envelope(cutoff):
    """The real October Base shape: every is_open session lacks open_at."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    env = _envelope(
        [_security_entry(raw)],
        calendar=_cal_null_anchor("SSE", days),
        listing=[
            _listing(raw.identity.security_id, effective_from=days[0].isoformat())
        ],
    )
    env["market_date"] = TARGET.isoformat()
    env["decision_cutoff"] = cutoff.isoformat()
    return env


def test_e1_real_bindings_missing_anchor_publishable(static):
    """missing open_at + equivalent_day -> publishable real ENTRY_PLAN, and the
    proof kind rides the run receipt's source_closure history_receipts."""
    cutoff = replay_decision_cutoff(TARGET)
    assert cutoff.isoformat().endswith("T21:30:00+08:00")
    bindings = _bindings(static, cutoff)
    req = RunnerRequest(
        snapshot=None,
        next_session=NEXT_OPEN.date(),
        holdings=(),
        pending_securities=(),
        published_at=cutoff,
        run_id="run-e1",
        decision_cutoff=cutoff,
        raw_history=_october_envelope(cutoff),
    )
    out = P11HistoryRunner(
        runner_ref_id="p11.h1",
        runner_version="2babce03",
        bindings=bindings,
    ).run(req)
    assert out.publishable is True
    assert out.decision_set is not None
    assert any(
        str(d.stage) == "OpportunityStage.ENTRY_PLAN"
        for d in out.decision_set.decisions
    )
    receipts = (out.source_closure or {}).get("history_receipts") or []
    assert receipts, "history receipts must ride the run receipt"
    proof = receipts[0].get("membership_proof")
    assert proof["anchor_mode"] == "equivalent_day"
    assert proof["kind"] == "mixed"
    assert receipts[0]["membership_count"] == 200
