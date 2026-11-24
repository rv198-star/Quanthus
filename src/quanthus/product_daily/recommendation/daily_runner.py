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
from quanthus.event_library.scan import CandidateDailyInput, CandidateScanError
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
    # held, None = not declared. The combined active_or_reserved hint blocks new
    # entries but does not establish a fill. Explicit membership, qualified position
    # facts or positive held quantity establish a held review, even with bad market.
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
    # Shares from P12's distinct holdings / pending_securities records. None remains
    # unknown; a missing quantity is never silently replaced with zero.
    held_quantity: int | None = None
    pending_quantity: int | None = None


@dataclass(frozen=True)
class PendingSecurityInput:
    """P12 pending fact, independent of the availability of market rows.

    A None quantity is explicitly unavailable, never a synthetic zero. The kind is
    always named; both fields are recorded in the account closure.
    """

    security_code: str
    kind: str
    quantity: int | None

    def __post_init__(self) -> None:
        require_text(self.security_code, "pending.security_code")
        if not isinstance(self.kind, str) or self.kind not in PENDING_KINDS:
            raise ProductDailyError("invalid_pending_kind", self.security_code)
        if self.quantity is not None and (type(self.quantity) is not int or self.quantity < 0):
            raise ProductDailyError("invalid_account_quantity", self.security_code)


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
        "held_quantity": item.held_quantity,
        "pending_quantity": item.pending_quantity,
    }


PENDING_KINDS = frozenset({"open_buy_order", "reserved_bucket", "unconfirmed_fill"})


@dataclass(frozen=True)
class _NormalizedAccount:
    held_facts: dict[str, HeldPositionFacts | None]
    pending_codes: tuple[str, ...]
    unavailable_reasons: dict[str, str]
    provenance: tuple[dict[str, Any], ...]


def _normalize_account_inputs(
    raw: Sequence[RawSecurityInput],
    account: AccountFactsView,
    pending_security_codes: Sequence[str] | None,
    pending_securities: Sequence[PendingSecurityInput] | None,
    disposition: str,
) -> _NormalizedAccount:
    """Resolve account membership before any market projection can fail.

    Only explicit fill evidence establishes held membership. A combined account
    hint blocks new entry without inventing a held review or a filled quantity.
    """
    if disposition == "ACCOUNT_FACTS_UNAVAILABLE":
        raise ProductDailyError("account_facts_unavailable", "account ledger read failed")
    if disposition not in {"ok", "FRESH_NO_BASELINE"}:
        raise ProductDailyError("invalid_account_disposition", str(disposition))
    pending = set(pending_security_codes or ())
    pending_details: dict[str, list[dict[str, Any]]] = {}
    for descriptor in pending_securities or ():
        if not isinstance(descriptor, PendingSecurityInput):
            raise ProductDailyError("invalid_pending_descriptor", "typed pending fact required")
        pending.add(descriptor.security_code)
        pending_details.setdefault(descriptor.security_code, []).append(
            {"kind": descriptor.kind, "quantity": descriptor.quantity}
        )
    hints = set(account.active_or_reserved_security_codes)
    for code in pending | hints:
        require_text(code, "account.security_code")
    held: dict[str, HeldPositionFacts | None] = {}
    reasons: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}
    for item in raw:
        code = item.identity.listing_code
        require_text(code, "raw.identity.listing_code")
        if code in records:
            raise ProductDailyError("duplicate_raw_security", code)
        if item.held_membership is not None and type(item.held_membership) is not bool:
            raise ProductDailyError("invalid_held_membership", code)
        if item.pending_kind is not None and (
            not isinstance(item.pending_kind, str) or item.pending_kind not in PENDING_KINDS
        ):
            raise ProductDailyError("invalid_pending_kind", code)
        for label, quantity in (
            ("held_quantity", item.held_quantity),
            ("pending_quantity", item.pending_quantity),
        ):
            if quantity is not None and (type(quantity) is not int or quantity < 0):
                raise ProductDailyError("invalid_account_quantity", f"{code}: {label}")
        # Validate the legacy fact itself before any account hint or canonical
        # descriptor can make the security pending and mask an incomplete pair.
        if item.pending_quantity is not None and item.pending_kind is None:
            raise ProductDailyError("pending_kind_unavailable", code)
        has_facts = item.position_facts is not None
        positive_quantity = item.held_quantity is not None and item.held_quantity > 0
        if (item.held_membership is False and (has_facts or positive_quantity)) or (
            item.held_quantity == 0 and (item.held_membership is True or has_facts)
        ):
            raise ProductDailyError("contradictory_account_membership", code)
        is_held = item.held_membership is True or has_facts or positive_quantity
        is_pending = (
            item.pending_kind is not None or code in pending or (code in hints and not is_held)
        )
        if is_pending:
            pending.add(code)
        if item.pending_kind is not None:
            detail = {"kind": item.pending_kind, "quantity": item.pending_quantity}
            # When typed records exist, legacy fields are only a projection of an
            # exact canonical record, not an additional or aggregated pending order.
            if pending_details.get(code) and detail not in pending_details[code]:
                raise ProductDailyError("pending_metadata_conflict", code)
            if detail not in pending_details.setdefault(code, []):
                pending_details[code].append(detail)
        details = sorted(
            pending_details.get(code, []),
            key=lambda d: (d["kind"], -1 if d["quantity"] is None else d["quantity"]),
        )
        if is_held:
            held[code] = item.position_facts
            if item.held_unavailable_reason:
                reasons[code] = item.held_unavailable_reason
            elif not has_facts:
                reasons[code] = "holding_position_facts_unavailable"
        records[code] = {
            "security_code": code,
            "held_membership": item.held_membership,
            "held": is_held,
            "held_quantity": item.held_quantity,
            "pending": is_pending,
            "pending_kind": details[0]["kind"] if len(details) == 1 else None,
            "pending_quantity": details[0]["quantity"] if len(details) == 1 else None,
            "pending_records": details,
            "pending_quantity_state": "known"
            if details and all(d["quantity"] is not None for d in details)
            else "unavailable",
            "membership_source": "filled_position_evidence"
            if is_held
            else "pending_descriptor"
            if details
            else "combined_account_hint"
            if code in hints
            else "not_held",
            "unavailable_reason": item.held_unavailable_reason or reasons.get(code),
        }
    missing = set(pending_security_codes or ()) - pending_details.keys()
    if missing:
        raise ProductDailyError("pending_metadata_missing", ",".join(sorted(missing)))
    # A reservation or unresolved account hint does not become a filled position
    # merely because market rows are absent. Typed pending facts retain their audit.
    for code in sorted(hints | pending):
        if code in records:
            continue
        pending.add(code)
        details = sorted(
            pending_details.get(code, []),
            key=lambda d: (d["kind"], -1 if d["quantity"] is None else d["quantity"]),
        )
        records[code] = {
            "security_code": code,
            "held_membership": None,
            "held": False,
            "held_quantity": None,
            "pending": True,
            "pending_kind": details[0]["kind"] if len(details) == 1 else None,
            "pending_quantity": details[0]["quantity"] if len(details) == 1 else None,
            "pending_records": details,
            "pending_quantity_state": "known"
            if details and all(d["quantity"] is not None for d in details)
            else "unavailable",
            "membership_source": "pending_descriptor" if details else "combined_account_hint",
            "unavailable_reason": "market_input_unavailable"
            if details
            else "combined_account_hint_is_not_fill_evidence",
        }
    if disposition == "FRESH_NO_BASELINE" and held:
        raise ProductDailyError("fresh_account_has_positions", ",".join(sorted(held)))
    return _NormalizedAccount(
        held, tuple(sorted(pending)), reasons, tuple(records[code] for code in sorted(records))
    )


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
    pending_securities: Sequence[PendingSecurityInput] | None = None,
    # Account read disposition from P12: "ok" | "FRESH_NO_BASELINE" |
    # "ACCOUNT_FACTS_UNAVAILABLE".  A failed/unknown account read is a shared
    # whole-day block, never a manufactured empty-positions success. A missing ledger
    # may imply an empty account only under FRESH_NO_BASELINE (no prior baseline).
    account_disposition: str = "ok",
    shared_prereq_ok: bool = True,
    shared_prereq_reason: str | None = None,
    # H1: per-security history-gate receipts from the p11.raw-history-input.v1
    # adapter (observe-only; thresholds stay in the scan/panel/row_number).
    history_receipts: Sequence[Mapping[str, Any]] | None = None,
    # Caller-supplied exclusions (e.g. adapter-level securities[] rejections)
    # merged BEFORE the run so they are inside the issued source_closure, never
    # appended to the dict after the receipt digest was computed.
    caller_input_exclusions: Sequence[dict[str, Any]] | None = None,
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
    account_view = _normalize_account_inputs(
        raw, account, pending_security_codes, pending_securities, account_disposition
    )
    held_facts = dict(account_view.held_facts)
    held_reasons = dict(account_view.unavailable_reasons)
    if not raw and not held_facts:
        raise ProductDailyError("missing_raw_inputs", "no qualified security inputs")
    features_by_security: dict[str, SecurityFeatureFacts] = {}
    scan_rows: list[CandidateDailyInput] = []
    prior_det: dict[str, int] = {}
    input_exclusions: list[dict[str, Any]] = list(caller_input_exclusions or ())
    signal_date = facts.market_date
    all_trading_dates: set[date] = set()
    # Account and raw closures are bound even when a particular market input fails.
    raw_closures = [raw_security_input_closure(item) for item in raw]

    for item in raw:
        code = item.identity.listing_code
        identity = item.identity
        close_map = item.close_at_by_date or {}
        cutoff_map = item.knowledge_cutoff_by_date or {}
        try:
            security_dates = set(item.universe_membership)
            signal_close = close_map.get(signal_date)
            if signal_close is None:
                raise ProductDailyError("missing_close_at", f"security {code} lacks signal close")
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
            action_dates = {action.effective_date for action in item.actions}
            security_rows: list[CandidateDailyInput] = []
            for bar in sorted(item.bars, key=lambda row: row.session_date):
                day = bar.session_date
                if day > signal_date:
                    continue
                close_at, cutoff = close_map.get(day), cutoff_map.get(day)
                if close_at is None or cutoff is None:
                    raise ProductDailyError("missing_session_clock", f"{code}: {day}")
                security_rows.append(
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
        except (ProductDailyError, CandidateScanError) as exc:
            detail = f"{type(exc).__name__}: {exc}"
            input_exclusions.append(
                {
                    "security_code": code,
                    "reason": "raw_input_closure_invalid",
                    "member_or_field": detail,
                    "at": signal_date.isoformat(),
                    "scope": "per_security",
                }
            )
            if code in held_facts:
                held_facts[code] = None
                held_reasons[code] = "; ".join(
                    part for part in (held_reasons.get(code), detail) if part
                )
            continue
        # Commit both projections together, only after this security is fully valid.
        features_by_security[code] = facts_row
        scan_rows.extend(security_rows)
        all_trading_dates.update(security_dates)
        if item.prior_deterioration_sessions is not None:
            prior_det[code] = item.prior_deterioration_sessions

    if not scan_rows and not held_facts:
        raise ProductDailyError(
            "missing_raw_inputs", "every supplied market input failed validation"
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
        pending_security_codes=account_view.pending_codes,
        held_unavailable_reasons_by_security=held_reasons,
        account_membership=account_view.provenance,
        raw_security_inputs_digest=sha256_digest(raw_closures),
        account_disposition=account_disposition,
        shared_prereq_ok=shared_prereq_ok,
        shared_prereq_reason=shared_prereq_reason,
        history_receipts=history_receipts,
        clock=clock,
    )
