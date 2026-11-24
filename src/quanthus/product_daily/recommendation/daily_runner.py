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
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from quanthus.event_library.contracts import sha256_digest
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
    (`knowledge_cutoff`) so the prospective/legacy clocks stay separated.
    universe_membership is the FULL source_daily_universe/calendar membership sequence
    the listing-session count runs over -- it is NOT the OHLC bar window; a security
    listed for 200 sessions with 130 delivered bars must still report
    listing_sessions=200 (the membership row_number), matching the original SQL which
    row_numbers universe rows <= scope_end, never the bar count.  actions/exceptions/
    limits are the normalized members already qualified by P10.  identity carries the
    P10 identity/dependency facts.  Held securities supply the same shape plus
    position_facts / prior_deterioration_sessions.
    """

    identity: IdentityFacts
    bars: tuple[DailyBar, ...]
    universe_membership: tuple[date, ...]
    actions: tuple[CorporateAction, ...] = ()
    exceptions: tuple[ExceptionSpan, ...] = ()
    limits: tuple[LimitFacts, ...] = ()
    close_at_by_date: Mapping[date, datetime] | None = None
    knowledge_cutoff_by_date: Mapping[date, datetime] | None = None
    position_facts: HeldPositionFacts | None = None
    prior_deterioration_sessions: int | None = None
    # Explicit held membership: True = a confirmed open position, False = confirmed not
    # held, None = not declared.  A security listed in the account's
    # active_or_reserved set with no explicit False is treated as held-with-unknown
    # facts (hold+alert), never silently empty and never a fresh entry; missing
    # position_facts can never convert it to an entry or drop it as ENDED.
    held_membership: bool | None = None
    # Pending kind from the P12 RunnerRequest.pending_securities projection
    # (open_buy_order | reserved_bucket | unconfirmed_fill): it blocks a new entry for
    # the security but does not itself receive a held review.  A code that is also a
    # real held member stays held (real membership wins).
    pending_kind: str | None = None
    # P12 HeldPositionFact.unavailable_reason: the ledger knows code+quantity only, so
    # sessions/return/reduction counts are None and this reason travels with the
    # membership; P11 never invents HeldPositionFacts counts.
    held_unavailable_reason: str | None = None


def raw_security_input_closure(item: RawSecurityInput) -> dict[str, Any]:
    """The complete accepted semantic closure of one RawSecurityInput.

    Binds every field the job consumes -- identity, all bar fields (OHLC, volume,
    CNY amount, tradable), the membership sequence, actions, exceptions, limits, the
    per-session close/cutoff instants, held membership/facts and prior deterioration --
    not merely the projected scan rows.  A change to any accepted byte (for example a
    bar volume while the caller evidence label is unchanged) must change this identity
    or fail at the digest comparison, exactly as the original panel's source closure
    requires.
    """

    def norm(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value

    def mapping_dates(table: Mapping[date, datetime] | None) -> dict[str, str] | None:
        if table is None:
            return None
        return {day.isoformat(): instant.isoformat() for day, instant in sorted(table.items())}

    return {
        "identity": {k: norm(v) for k, v in asdict(item.identity).items()},
        "bars": [
            {k: norm(v) for k, v in asdict(bar).items()}
            for bar in sorted(item.bars, key=lambda bar: bar.session_date)
        ],
        "universe_membership": [day.isoformat() for day in item.universe_membership],
        "actions": [{k: norm(v) for k, v in asdict(action).items()} for action in item.actions],
        "exceptions": [{k: norm(v) for k, v in asdict(span).items()} for span in item.exceptions],
        "limits": [{k: norm(v) for k, v in asdict(limit).items()} for limit in item.limits],
        "close_at_by_date": mapping_dates(item.close_at_by_date),
        "knowledge_cutoff_by_date": mapping_dates(item.knowledge_cutoff_by_date),
        "position_facts": (
            None
            if item.position_facts is None
            else {k: norm(v) for k, v in asdict(item.position_facts).items()}
        ),
        "prior_deterioration_sessions": item.prior_deterioration_sessions,
        "held_membership": item.held_membership,
        "pending_kind": item.pending_kind,
        "held_unavailable_reason": item.held_unavailable_reason,
    }


PENDING_KINDS = frozenset({"open_buy_order", "reserved_bucket", "unconfirmed_fill"})


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
    # P12 RunnerRequest.pending_securities projection: codes that must not receive a
    # new entry (open buy order / reserved bucket / unconfirmed fill).  They are not
    # held memberships and get no held review on that basis alone; a code that is also
    # a real held member stays held.
    pending_security_codes: Sequence[str] | None = None,
    # Account read disposition from P12: "ok" | "FRESH_NO_BASELINE" |
    # "ACCOUNT_FACTS_UNAVAILABLE".  A failed/unknown account read is a shared
    # whole-day block, never a manufactured empty-positions success; empty holdings is
    # allowed only under FRESH_NO_BASELINE (no ledger file and no baseline).
    account_disposition: str = "ok",
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
    if account_disposition == "ACCOUNT_FACTS_UNAVAILABLE":
        raise ProductDailyError(
            "account_facts_unavailable",
            "the account read failed; a failed/unknown account read is a whole-day "
            "block, never a manufactured empty-positions success",
        )
    if account_disposition not in {"ok", "FRESH_NO_BASELINE"}:
        raise ProductDailyError(
            "invalid_account_disposition",
            f"account_disposition must be ok|FRESH_NO_BASELINE|ACCOUNT_FACTS_UNAVAILABLE, "
            f"got {account_disposition}",
        )

    features_by_security: dict[str, SecurityFeatureFacts] = {}
    scan_rows: list[CandidateDailyInput] = []
    # Mapping held code -> its qualified position facts; a None value means the security
    # is an explicit held member whose position facts are unavailable, which the core
    # turns into a hold+alert review (never an omission, never a forced sale).
    held_facts: dict[str, HeldPositionFacts | None] = {}
    prior_det: dict[str, int] = {}
    input_exclusions: list[dict[str, Any]] = []
    signal_date = facts.market_date
    all_trading_dates: set[date] = set()
    raw_closures: list[dict[str, Any]] = []

    for item in raw:
        code = item.identity.listing_code
        identity = item.identity
        close_map = item.close_at_by_date or {}
        cutoff_map = item.knowledge_cutoff_by_date or {}

        # A malformed or incomplete closure for one locatable security excludes only
        # that security; a valid neighbour still reaches its real entry review.  Only
        # shared or unlocatable prerequisites may block the whole day.
        try:
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
                trading_dates=item.universe_membership,
                identity=identity,
                signal_date=signal_date,
                signal_close=signal_close,
            )
        except ProductDailyError as exc:
            input_exclusions.append(
                {
                    "security_code": code,
                    "reason": "raw_input_closure_invalid",
                    "member_or_field": str(exc),
                    "at": signal_date.isoformat(),
                    "scope": "per_security",
                }
            )
            continue
        features_by_security[code] = facts_row

        # Emit the full per-session series so the transition scan sees the quiet->active
        # window ending at the signal date; every bar carries its own close/cutoff and
        # the qualified adjustment flag for that session.
        action_dates = {a.effective_date for a in item.actions}
        try:
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
        except ProductDailyError as exc:
            # The feature row was built but the session series is incomplete: exclude the
            # security entirely so its partial rows never reach the scan.
            scan_rows = [row for row in scan_rows if row.listing_code != code]
            features_by_security.pop(code, None)
            input_exclusions.append(
                {
                    "security_code": code,
                    "reason": "raw_input_closure_invalid",
                    "member_or_field": str(exc),
                    "at": signal_date.isoformat(),
                    "scope": "per_security",
                }
            )
            continue
        all_trading_dates.update(item.universe_membership)
        raw_closures.append(raw_security_input_closure(item))

        # Explicit held membership: a confirmed position carries its facts; a member of
        # the account's active_or_reserved set without an explicit not-held declaration
        # is a held member with unavailable facts (hold+alert), never an entry and never
        # dropped.  held_membership=False is the only explicit "not held" escape.
        if item.pending_kind is not None and item.pending_kind not in PENDING_KINDS:
            raise ProductDailyError(
                "invalid_pending_kind",
                f"security {code} pending_kind must be one of {sorted(PENDING_KINDS)}",
            )
        if item.position_facts is not None:
            held_facts[code] = item.position_facts
        elif item.held_membership is True or (
            item.held_membership is not False and code in account.active_or_reserved_security_codes
        ):
            held_facts[code] = None
        if item.prior_deterioration_sessions is not None:
            prior_det[code] = item.prior_deterioration_sessions

    if not raw_closures:
        raise ProductDailyError(
            "missing_raw_inputs",
            "every supplied security closure failed per-security validation",
        )

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
        input_exclusions=input_exclusions,
        pending_security_codes=pending_security_codes,
        raw_security_inputs_digest=sha256_digest(raw_closures),
        account_disposition=account_disposition,
        shared_prereq_ok=shared_prereq_ok,
        shared_prereq_reason=shared_prereq_reason,
        clock=clock,
    )
