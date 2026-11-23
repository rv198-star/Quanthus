"""Synthetic contract tests for the R11 daily advice producer."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation import (
    AccountFactsView,
    AdmissionDisposition,
    AdvicePolicy,
    CandidateSignal,
    DailyAdviceProducer,
    DailyFactsView,
    ExpertBundle,
    MemberEvidence,
    package_digests,
    publish_package,
)
from quanthus.product_daily.recommendation.contracts import (
    ENDED,
    ENTRY_PLAN,
    INPUT_BLOCKED,
    SCHEMA,
)

TZ = timezone(timedelta(hours=8))
CUTOFF = datetime(2026, 11, 20, 22, 30, tzinfo=TZ)
PUBLISHED = datetime(2026, 11, 20, 22, 45, tzinfo=TZ)


def _member(mid: str, available_at: datetime, required: bool = True) -> MemberEvidence:
    return MemberEvidence(mid, "1.0.0", "sha256:" + "a" * 64, available_at, required)


def _facts(members) -> DailyFactsView:
    return DailyFactsView(
        batch_id="batch-20261120",
        market_date=date(2026, 11, 20),
        next_session=date(2026, 11, 23),
        source_id="p11-daily",
        source_version="1.0.0",
        calendar_version="cal-1",
        price_basis="unadjusted",
        members=tuple(members),
    )


def _policy() -> AdvicePolicy:
    return AdvicePolicy(
        policy_id="r11-daily-advice",
        version="0.1.0",
        decision_cutoff=CUTOFF,
        published_at=PUBLISHED,
        window_ref="replay-manual-window:2026-11-23",
        eligible_at=datetime(2026, 11, 23, 9, 30, tzinfo=TZ),
        expires_at=datetime(2026, 11, 23, 15, 0, tzinfo=TZ),
        candidate_rule_digest="sha256:" + "b" * 64,
    )


def _account() -> AccountFactsView:
    return AccountFactsView(
        account_id="SYN-PAPER-A",
        as_of=datetime(2026, 11, 20, 15, 0, tzinfo=TZ),
        cash_available=Decimal("100000.00"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        pending_commitment_total=Decimal("0"),
    )


def _experts() -> ExpertBundle:
    return ExpertBundle("replay.trend-position-risk", "1.0.0", "sha256:" + "c" * 64)


def _candidate(member_ids=("daily-bars",)) -> CandidateSignal:
    return CandidateSignal(
        opportunity_id="OPP-600000",
        security_code="600000",
        security_name="浦发银行",
        industry_id="IND-BANK",
        fraction=Decimal("0.5"),
        price_bound=Decimal("10.75"),
        mark_price=Decimal("10.60"),
        component_ref_id="replay.trend-position-risk",
        component_version="1.0.0",
        reason_codes=("NEW_ENTRY_SIGNAL",),
        explanations=(
            {
                "code": "REPLAY_SIZING",
                "use": "explanation",
                "component_ref": "replay.trend-position-risk@1.0.0",
                "text": "sizing is bounded by account cash, bucket budget and concentration caps",
            },
        ),
        invalidations=("cancel if open above price_bound",),
        next_observation="2026-11-23 close",
        depends_on_member_ids=tuple(member_ids),
    )


def producer() -> DailyAdviceProducer:
    return DailyAdviceProducer()


def test_entry_plan_package_is_publishable(tmp_path: Path):
    facts = _facts([_member("daily-bars", datetime(2026, 11, 20, 18, 0, tzinfo=TZ))])
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
    )
    assert result.publishable
    pkg = result.package
    assert pkg is not None and pkg.schema == SCHEMA
    ds = pkg.decision_sets[0]
    assert ds.decisions[0].stage == ENTRY_PLAN
    assert ds.decisions[0].entry is not None
    assert ds.decisions[0].entry.eligible_at == datetime(2026, 11, 23, 9, 30, tzinfo=TZ)
    assert (
        dict(ds.audit.per_member_available_at)["daily-bars"]
        == datetime(2026, 11, 20, 18, 0, tzinfo=TZ).isoformat()
    )
    assert ds.audit.admission_disposition == "publishable"
    digests = package_digests(pkg)
    assert digests["2026-11-20"].startswith("sha256:")
    receipt = publish_package(pkg, directory=tmp_path, published_at=PUBLISHED)
    assert receipt.entry_plan_count == 1
    assert dict(receipt.package_digests)["2026-11-20"] == digests["2026-11-20"]


def test_required_member_after_cutoff_fails_closed():
    facts = _facts([_member("daily-bars", datetime(2026, 11, 20, 23, 0, tzinfo=TZ))])
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
    )
    assert not result.publishable
    assert result.package is None
    assert result.disposition is AdmissionDisposition.INPUT_NOT_READY
    assert result.diagnostics[0]["reason"] == "member_clock_blocked"


def test_blocked_optional_member_blocks_only_its_decision():
    facts = _facts(
        [
            _member("daily-bars", datetime(2026, 11, 20, 18, 0, tzinfo=TZ)),
            _member("industry-map", datetime(2026, 11, 20, 23, 0, tzinfo=TZ), required=False),
        ]
    )
    both = (
        _candidate(("daily-bars",)),
        _candidate(("industry-map",)),
    )
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=both,
    )
    assert result.publishable
    stages = [d.stage for d in result.package.decision_sets[0].decisions]
    assert stages == [ENTRY_PLAN, INPUT_BLOCKED]


def test_empty_advice_emits_single_ended_decision():
    facts = _facts([_member("daily-bars", datetime(2026, 11, 20, 18, 0, tzinfo=TZ))])
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(),
    )
    assert result.publishable
    ds = result.package.decision_sets[0]
    assert len(ds.decisions) == 1
    assert ds.decisions[0].stage == ENDED
    assert ds.audit.admission_disposition == "empty_advice"


def test_publish_refuses_same_date_rewrite(tmp_path: Path):
    facts = _facts([_member("daily-bars", datetime(2026, 11, 20, 18, 0, tzinfo=TZ))])
    result = producer().produce(
        facts=facts,
        account=_account(),
        policy=_policy(),
        experts=_experts(),
        candidates=(_candidate(),),
    )
    publish_package(result.package, directory=tmp_path, published_at=PUBLISHED)
    with pytest.raises(ProductDailyError):
        publish_package(result.package, directory=tmp_path, published_at=PUBLISHED)


def test_published_at_must_not_precede_cutoff():
    with pytest.raises(ProductDailyError):
        AdvicePolicy(
            policy_id="p",
            version="0.1.0",
            decision_cutoff=CUTOFF,
            published_at=CUTOFF - timedelta(hours=1),
            window_ref="w",
            eligible_at=datetime(2026, 11, 23, 9, 30, tzinfo=TZ),
            expires_at=datetime(2026, 11, 23, 15, 0, tzinfo=TZ),
            candidate_rule_digest="sha256:" + "b" * 64,
        )
