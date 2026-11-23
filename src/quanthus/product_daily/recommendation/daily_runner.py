"""P11 M2 same-job runner: raw in-memory rows -> real panel -> run_daily_advice.

This is the orchestration seam between P12's already-read canonical inputs and the
fixed-rule core.  It owns the P11-side transformation only:

  P12-supplied raw rows (bars/actions/exceptions/limits/calendar/identity, plus
      held raw rows and position facts)
      -> ONE job-local :memory: DuckDB relation set + the ORIGINAL _feature_panel_query
      -> real BaselineFeatureRow per security (a SecurityFeatureFacts)
      -> the real per-session CandidateDailyInput series for the event scan
      -> run_daily_advice (real scan + trend + entry/held + internal coverage receipt
         + source closure)  [M1 core, unchanged]
      -> the producer package for P12's loader / publish gate

The panel relation set is registered and the query executed per security, but each
security's closure is registered once and the result feeds BOTH the entry scan and the
held review; nothing re-reads a store or caches across jobs.

Held inputs are supplied independently of entry events: a held security contributes its
own bars/calendar/facts even when it has no new entry transition.  Held position facts
(HeldPositionFacts) are P12's account-state read and are passed through unchanged; this
runner does not synthesize them.

This is a synthetic engineering boundary, not a real-market admission: it does not read
market rows, does not touch accounts/P12 binding/publication, and does not adopt U06/U07.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from quanthus.event_library.scan import CandidateDailyInput
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.product_daily.contracts import (
    ProductDailyError,
    require_text,
)
from quanthus.product_daily.recommendation.contracts import DailyFactsView
from quanthus.product_daily.recommendation.feature_adapter import (
    CorporateAction,
    DailyBar,
    ExceptionSpan,
    IdentityFacts,
    LimitFacts,
    compute_security_feature_facts,
    daily_input_from_bar,
)
from quanthus.product_daily.recommendation.producer import (
    AccountFactsView,
    AdvicePolicy,
)
from quanthus.product_daily.recommendation.rule_seam import (
    RuleDayOutput,
    SeamResources,
    SecurityFeatureFacts,
    run_daily_advice,
)


@dataclass(frozen=True)
class RawSecurityInput:
    """The complete qualified raw closure for one security in this job.

    bars is the PIT-trimmed daily history ending at the signal date; each bar carries
    its own session close instant (`close_at`) and source knowledge cutoff
    (`knowledge_cutoff`) so the prospective/legacy clocks stay separated.  trading_dates
    is the universe membership the listing-session count runs over; actions/exceptions/
    limits are the normalized members already qualified by P10.  identity carries the
    P10 identity/dependency facts.  Held securities supply the same shape plus
    position_facts / prior_deterioration_sessions.
    """

    identity: IdentityFacts
    bars: tuple[DailyBar, ...]
    trading_dates: tuple[date, ...]
    actions: tuple[CorporateAction, ...] = ()
    exceptions: tuple[ExceptionSpan, ...] = ()
    limits: tuple[LimitFacts, ...] = ()
    close_at_by_date: Mapping[date, datetime] | None = None
    knowledge_cutoff_by_date: Mapping[date, datetime] | None = None
    position_facts: HeldPositionFacts | None = None
    prior_deterioration_sessions: int | None = None


def run_daily_advice_from_raw(
    *,
    runner: FormalStrategyRunner,
    resources: SeamResources,
    raw: Sequence[RawSecurityInput],
    facts: DailyFactsView,
    policy: AdvicePolicy,
    account: AccountFactsView,
    run_id: str,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: Any,
    split_policy: Any,
    source_policy: Any,
    shared_prereq_ok: bool = True,
    shared_prereq_reason: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RuleDayOutput:
    """Build real panel facts + the scan series from raw inputs, then run the core.

    Each security's qualified closure is registered once into the job-local relations;
    its resulting BaselineFeatureRow/SecurityFeatureFacts feeds the entry evidence gate
    AND its per-session CandidateDailyInput series feeds the shared transition scan, so
    a held security with no new event is still evaluated.  The caller supplies the
    predeclared facts/policy/account/run_id; this function does not issue a receipt
    itself (run_daily_advice does) and does not write anything.
    """

    require_text(run_id, "run_id")
    if not raw:
        raise ProductDailyError("missing_raw_inputs", "no qualified security inputs")

    features_by_security: dict[str, SecurityFeatureFacts] = {}
    scan_rows: list[CandidateDailyInput] = []
    held_facts: dict[str, HeldPositionFacts] = {}
    prior_det: dict[str, int] = {}
    signal_date = facts.market_date
    all_trading_dates: set[date] = set()

    for item in raw:
        code = item.identity.listing_code
        identity = item.identity
        close_map = item.close_at_by_date or {}
        cutoff_map = item.knowledge_cutoff_by_date or {}

        signal_close = close_map.get(signal_date)
        if signal_close is None:
            raise ProductDailyError(
                "missing_close_at",
                f"security {code} has no close instant on the signal date",
            )
        facts_row = compute_security_feature_facts(
            security_id=identity.security_id,
            bars=item.bars,
            actions=item.actions,
            exceptions=item.exceptions,
            limits=item.limits,
            trading_dates=item.trading_dates,
            identity=identity,
            signal_date=signal_date,
            signal_close=signal_close,
        )
        features_by_security[code] = facts_row

        # Emit the full per-session series so the transition scan sees the quiet->active
        # window ending at the signal date; every bar carries its own close/cutoff and
        # the qualified adjustment flag for that session.
        action_dates = {a.effective_date for a in item.actions}
        for bar in sorted(item.bars, key=lambda b: b.session_date):
            day = bar.session_date
            if day > signal_date:
                continue
            close_at = close_map.get(day)
            cutoff = cutoff_map.get(day)
            if close_at is None or cutoff is None:
                raise ProductDailyError(
                    "missing_session_clock",
                    f"security {code} bar {day.isoformat()} lacks a close/cutoff instant",
                )
            scan_rows.append(
                daily_input_from_bar(
                    bar=bar,
                    identity=identity,
                    listing_sessions=facts_row.listing_sessions,
                    close_at=close_at,
                    knowledge_cutoff=cutoff,
                    source_evidence_digest=identity.source_evidence_digest,
                    adjustment_effective=day in action_dates,
                )
            )
        all_trading_dates.update(item.trading_dates)

        if item.position_facts is not None:
            held_facts[code] = item.position_facts
        if item.prior_deterioration_sessions is not None:
            prior_det[code] = item.prior_deterioration_sessions

    # The scan requires the T+1 session in trading_dates; take it from the bound
    # facts.next_session (the calendar's next open), not invented here.
    all_trading_dates.add(facts.next_session)

    return run_daily_advice(
        runner=runner,
        resources=resources,
        rows=scan_rows,
        trading_dates=sorted(all_trading_dates),
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
        features_by_security=features_by_security,
        facts=facts,
        policy=policy,
        account=account,
        run_id=run_id,
        held_facts_by_security=held_facts or None,
        prior_deterioration_sessions_by_security=prior_det or None,
        shared_prereq_ok=shared_prereq_ok,
        shared_prereq_reason=shared_prereq_reason,
        clock=clock,
    )
