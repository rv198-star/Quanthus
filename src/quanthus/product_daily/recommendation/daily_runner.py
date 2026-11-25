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
from quanthus.product_daily.recommendation.contracts import (
    DailyFactsView,
    NoQualifiedRawInputs,
    OctoberReplayContext,
)
from quanthus.product_daily.recommendation.contracts import (
    _new_october_context as _new_october_context,
)
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

# The private OctoberReplayContext token now lives in the leaf contracts module
# so the rule seam and the producer can type-check it without importing this
# top-level module.  _new_october_context is re-exported here for the
# history_input as-observed projection and the focused tests.


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
    # Explicit null entries are declared absences (the bound calendar kept the
    # session date/is_open but no native instant), NOT a stored clock.  A
    # date-context warm-up row joins the numeric window only under the
    # versioned as-observed replay context; every other entry still fails
    # closed on any missing instant.
    close_at_by_date: Mapping[date, datetime | None] | None = None
    knowledge_cutoff_by_date: Mapping[date, datetime | None] | None = None
    # Versioned #749 role: "calendar_source_knowledge_from" declares that a
    # warm-up row's decision coordinate is the source calendar's own
    # knowledge_from (the instant the row became known to the source), not its
    # native close instant.  Only admitted inside the versioned as-observed
    # replay context under the OctoberReplayContext token; close_at stays the
    # ORIGINAL null (never borrowed) while the verified source knowledge_from
    # supplies an independent decision coordinate so the row evaluates as a
    # real clocked row, not a date-context row and not a fail-closed partial
    # pair.  Any other value / None keeps the per-bar close instant required.
    knowledge_clock_role: str | None = None
    # Versioned as-observed projection: the session dates whose envelope
    # knowledge_cutoff matched the SAME venue+session_date source calendar
    # knowledge_from under the calendar_source_knowledge_from role.  Only
    # populated by the versioned as-observed entry; for those rows the scan's
    # PIT decision coordinate is the business fact instant (the native close,
    # or the business session date when the close is absent), never the
    # archive version stamp -- which stays bound verbatim on the closure.
    source_calendar_matched_dates: frozenset[date] | None = None
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
    # False when the adapter has already classified this input out of the market
    # scan (a held/pending account member that is provably not listed on T).
    # The row still supplies held/pending facts to the account normalisation and
    # stays inside the bound raw closure, but the feature/scan construction is
    # skipped -- the named census receipt is its only classification, never a
    # downstream missing_signal_membership / raw_input_closure_invalid.
    scan_eligible: bool = True


@dataclass(frozen=True)
class PendingSecurityInput:
    """P12 pending fact, independent of the availability of market rows.

    A None quantity is explicitly unavailable, never a synthetic zero. The kind is
    always named; both fields are recorded in the account closure.
    """

    security_code: str
    kind: str
    quantity: int | None
    # Per-order evidence: order_key and source_receipt bind the same-read
    # order identity into the issued closure; direction keeps sell verbatim.
    order_key: str | None = None
    source_receipt: str | None = None
    direction: str | None = None

    def __post_init__(self) -> None:
        require_text(self.security_code, "pending.security_code")
        if not isinstance(self.kind, str) or self.kind not in PENDING_KINDS:
            raise ProductDailyError("invalid_pending_kind", self.security_code)
        # Strict int quantity: bool (True==1), float (1.2) and Decimal are all
        # named rejections, never silently truncated or coerced.
        if self.quantity is not None and (
            type(self.quantity) is not int
            or isinstance(self.quantity, bool)
            or self.quantity < 0
        ):
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

    def mapping_dates(
        table: Mapping[date, datetime | None] | None,
    ) -> dict[str, str | None] | None:
        if table is None:
            return None
        return {
            day.isoformat(): (None if instant is None else instant.isoformat())
            for day, instant in sorted(table.items())
        }

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
        "scan_eligible": item.scan_eligible,
        # The declared clock role and the proven source-calendar-matched dates
        # both change the PIT decision coordinate, so both are bound into the
        # accepted closure identity (a role/match change must drift the
        # digest, never silently reuse a pass).
        "knowledge_clock_role": item.knowledge_clock_role,
        "source_calendar_matched_dates": (
            None
            if item.source_calendar_matched_dates is None
            else sorted(d.isoformat() for d in item.source_calendar_matched_dates)
        ),
    }


PENDING_KINDS = frozenset({"open_buy_order", "reserved_bucket", "unconfirmed_fill"})


@dataclass(frozen=True)
class _NormalizedAccount:
    held_facts: dict[str, HeldPositionFacts | None]
    pending_codes: tuple[str, ...]
    unavailable_reasons: dict[str, str]
    provenance: tuple[dict[str, Any], ...]
    # Per-order pending evidence bound into the issued closure: kind/quantity
    # plus order_key/source_receipt/direction verbatim, never collapsed.
    pending_evidence: tuple[dict[str, Any], ...] = ()


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
    pending_evidence: dict[str, list[dict[str, Any]]] = {}
    for descriptor in pending_securities or ():
        if not isinstance(descriptor, PendingSecurityInput):
            raise ProductDailyError("invalid_pending_descriptor", "typed pending fact required")
        pending.add(descriptor.security_code)
        detail = {"kind": descriptor.kind, "quantity": descriptor.quantity}
        pending_details.setdefault(descriptor.security_code, []).append(detail)
        # Per-order evidence (order_key/source_receipt/direction) is bound into
        # the issued closure so the same-read order identity recomputes the
        # digest; it never silently collapses to a bare kind/quantity pair.
        ev = {
            "kind": descriptor.kind,
            "quantity": descriptor.quantity,
            "order_key": descriptor.order_key,
            "source_receipt": descriptor.source_receipt,
            "direction": descriptor.direction,
        }
        pending_evidence.setdefault(descriptor.security_code, []).append(ev)
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
    flat_evidence = [
        ev
        for code in sorted(pending_evidence)
        for ev in pending_evidence[code]
    ]
    return _NormalizedAccount(
        held,
        tuple(sorted(pending)),
        reasons,
        tuple(records[code] for code in sorted(records)),
        tuple(flat_evidence),
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
    # Pool-external unresolved account records (B-segment): a held role
    # surfaces its own hold review, a pending role stays pending-isolated;
    # neither admits any new entry or exit decision for that key.
    account_unresolved: Sequence[Mapping[str, Any]] | None = None,
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
    # Optional factory: after this job computes features_by_security ONCE, it
    # is invoked with that mapping and must return the BaselineFeaturePanelReport
    # to bind into the resources for the receipt.  This keeps the panel the
    # actual same-job computation (no second market pass, no caller label);
    # when omitted the supplied resources.panel_report is used unchanged.
    panel_report_factory: Callable[[Mapping[str, SecurityFeatureFacts]], Any] | None = None,
    # Versioned October as-observed consumption evidence: bound into the
    # issued source_closure before its digest so the job identity recomputes
    # from receipt + closure together (original envelope identity, selected
    # member/row versions, original clocks, this run's actual open/verify/
    # observe instants, exclusions and the member catalogue).  Strict/daily
    # callers omit it and see no field.
    consumption_evidence: Mapping[str, Any] | None = None,
    # Versioned single-T numeric context for the October replay.  Only an
    # OctoberReplayContext token - minted by the versioned as-observed replay
    # entry, never a caller-supplied string/anchor/boolean - admits a
    # date-context warm-up row: a bar strictly before T on a named open session
    # whose close/cutoff entries are BOTH absent joins the numeric window with
    # its own business date as the knowledge coordinate -- original nulls
    # preserved, never a borrowed T clock or a fabricated 15:00/midnight.  Each
    # admitted row is named inside the signed closure.  None keeps the original
    # per-bar instant requirement on every caller.
    numeric_context: OctoberReplayContext | None = None,
    # Versioned as-observed scan calendar: the bounded open-session set the scan
    # window actually runs over - the named bar_context_window(<=61 contiguous
    # open sessions ending at T) plus facts.next_session, projected by the
    # versioned replay entry from the venue calendar (never narrowed by which
    # bars exist).  Only admitted alongside the private OctoberReplayContext
    # token; without the token it is a contract breach.  When supplied it
    # REPLACES the full universe_membership union as the scan's trading_dates -
    # listing age stays complete inside features/universe_membership (the
    # listing_sessions row_number), but a security listed since 1991 no longer
    # drags every pre-2026 session into require_scan_date scope.  Strict/forward
    # callers omit it and keep the original membership-union semantics.
    scan_trading_dates: Sequence[date] | None = None,
    # Versioned as-observed actual-read instant: the wall-clock moment THIS run
    # actually opened/verified the sealed replay input.  A source calendar
    # version knowledge_from is an ARCHIVE clock, not the trading-fact
    # availability instant -- under the as-observed replay it is admitted only
    # when it was already obtainable by this read (knowledge_from <=
    # as_observed_at); a version that only became readable later still blocks.
    # Required (non-None) alongside the private OctoberReplayContext token;
    # without the token it is a contract breach.
    as_observed_at: datetime | None = None,
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
    # Pool-external held unresolved records: register the account key as held
    # evidence with its named missing reason so the held-review loop produces
    # a hold_alert (never a silent drop and never a fabricated market
    # identity).  quantity=None stays explicitly unavailable.
    for record in account_unresolved or ():
        if record.get("role") == "held":
            key = str(record.get("account_key") or "")
            if not key:
                raise ProductDailyError(
                    "invalid_account_unresolved", "held unresolved record needs account_key"
                )
            held_facts.setdefault(key, None)
            held_reasons.setdefault(
                key,
                record.get("missing_reason")
                or "pool_external_listing_identity_unresolved",
            )
    if not raw and not held_facts:
        raise NoQualifiedRawInputs("no qualified security inputs", caller_input_exclusions or ())
    features_by_security: dict[str, SecurityFeatureFacts] = {}
    scan_rows: list[CandidateDailyInput] = []
    prior_det: dict[str, int] = {}
    input_exclusions: list[dict[str, Any]] = list(caller_input_exclusions or ())
    signal_date = facts.market_date
    all_trading_dates: set[date] = set()
    date_context_rows: list[dict[str, Any]] = []
    source_clock_rows: list[dict[str, Any]] = []
    # The typed replay token is the ONLY thing that admits a date-context row.
    # A bare string/anchor/boolean can never satisfy the type check, so a strict
    # /forward/H1/M2 caller cannot substitute a label for the versioned entry's
    # projection; an unrecognized context id fails loudly.
    if numeric_context is not None and (
        type(numeric_context) is not OctoberReplayContext
        or numeric_context.context_id != "october_replay_as_observed"
    ):
        raise ProductDailyError("unknown_numeric_context", f"numeric_context {numeric_context!r}")
    _date_context_ok = numeric_context is not None
    if scan_trading_dates is not None and not _date_context_ok:
        # A bounded scan calendar is an as-observed projection: only the
        # versioned replay entry (which mints the private token) may supply it.
        raise ProductDailyError(
            "scan_calendar_requires_replay_context",
            "scan_trading_dates requires the versioned as-observed replay context",
        )
    if as_observed_at is not None and not _date_context_ok:
        raise ProductDailyError(
            "as_observed_clock_requires_replay_context",
            "as_observed_at requires the versioned as-observed replay context",
        )
    if _date_context_ok:
        if as_observed_at is None:
            # An as-observed run must carry its actual-read instant so a sealed
            # version's readability can be judged; a missing independent clock
            # never silently passes.
            raise ProductDailyError(
                "missing_as_observed_clock",
                "the as-observed replay requires the actual-read instant",
            )
        if as_observed_at.tzinfo is None or as_observed_at.utcoffset() is None:
            raise ProductDailyError("invalid_as_observed_clock", "as_observed_at must be tz-aware")
    # Account and raw closures are bound even when a particular market input fails.
    raw_closures = [raw_security_input_closure(item) for item in raw]

    for item in raw:
        code = item.identity.listing_code
        identity = item.identity
        if not item.scan_eligible:
            # Already classified by the adapter (held/pending member not listed
            # on T): the row keeps its held/pending facts for the account view
            # and its bytes stay inside the bound raw closure, but it never
            # enters feature/scan construction, so it cannot degrade into a
            # contradictory missing_signal_membership exclusion.
            continue
        close_map = item.close_at_by_date or {}
        cutoff_map = item.knowledge_cutoff_by_date or {}
        try:
            security_dates = set(item.universe_membership)
            signal_close = close_map.get(signal_date)
            if signal_close is None:
                raise ProductDailyError("missing_close_at", f"security {code} lacks signal close")
            # The #749 calendar_source_knowledge_from role admits a warm-up row
            # whose native close is absent while the source calendar's own
            # knowledge_from supplies the decision coordinate.  It is effective
            # ONLY inside the versioned as-observed replay context (the private
            # token) - a strict/forward caller keeps the per-bar instant
            # requirement.  close_at stays the ORIGINAL null, never borrowed.
            _role_source_clock = (
                _date_context_ok and item.knowledge_clock_role == "calendar_source_knowledge_from"
            )
            # The dates whose envelope cutoff matched the same venue+session
            # source calendar knowledge_from (computed by the versioned
            # as-observed entry).  Only those rows move the PIT coordinate to
            # the business fact instant; every other row keeps its original
            # cutoff/identity.
            _matched_days = item.source_calendar_matched_dates if _date_context_ok else None
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
            security_date_rows: list[dict[str, Any]] = []
            security_source_clock_rows: list[dict[str, Any]] = []
            for bar in sorted(item.bars, key=lambda row: row.session_date):
                day = bar.session_date
                if day > signal_date:
                    continue
                close_at, source_cutoff = close_map.get(day), cutoff_map.get(day)
                matched = _matched_days is not None and day in _matched_days
                if _date_context_ok and matched:
                    # Source-calendar-matched row under the verified
                    # calendar_source_knowledge_from role: the envelope's
                    # knowledge_cutoff equals the SAME venue+session_date source
                    # calendar knowledge_from -- an ARCHIVE VERSION clock, not
                    # the trading-fact availability instant.  The trading fact
                    # (this bar's OHLC) is determined at the session's own
                    # close, so the scan's PIT decision coordinate is the
                    # business fact instant: native close when present, else the
                    # business session date (date-context).  The version stamp
                    # is bound verbatim below (source_knowledge_clock_rows), the
                    # member-level readability verdict is unchanged.
                    if source_cutoff is not None:
                        # Readability: a USED source version must already have
                        # been obtainable by THIS run's real read.  A version
                        # whose archive knowledge_from is later than the actual
                        # read instant was not yet acquired -- isolate it by
                        # dependency, never let a later rerun flatten the gap.
                        if source_cutoff > as_observed_at:
                            raise ProductDailyError(
                                "source_version_after_actual_read",
                                f"{code}: {day} source knowledge_from "
                                f"{source_cutoff.isoformat()} is later than the "
                                f"actual read {as_observed_at.isoformat()}",
                            )
                        kind = (
                            "source_knowledge_clock_row"
                            if close_at is None
                            else "source_archive_version_clock"
                        )
                        security_source_clock_rows.append(
                            {
                                "security_code": code,
                                "session_date": day.isoformat(),
                                "kind": kind,
                                "source_knowledge_from": source_cutoff.isoformat(),
                                "actual_read_at": as_observed_at.isoformat(),
                                "knowledge_clock_role": item.knowledge_clock_role,
                            }
                        )
                    if close_at is None:
                        if day < signal_date:
                            if source_cutoff is None:
                                security_date_rows.append(
                                    {
                                        "security_code": code,
                                        "session_date": day.isoformat(),
                                        "kind": "date_context_warmup_row",
                                    }
                                )
                        else:
                            raise ProductDailyError(
                                "missing_close_at",
                                f"{code}: signal {day} lacks a native close",
                            )
                    pit_cutoff = close_at
                elif _date_context_ok:
                    # Un-matched as-observed rows keep the original r3/r4
                    # semantics verbatim: a close-missing + knowledge-role row
                    # is a real source-clock row (knowledge bound = its source
                    # knowledge_from); a bare double-null warm-up row is a
                    # date-context row; a partial pair stays fail-closed.  The
                    # PIT coordinate is the envelope cutoff unchanged.
                    if (
                        close_at is None
                        and source_cutoff is not None
                        and day < signal_date
                        and _role_source_clock
                    ):
                        security_source_clock_rows.append(
                            {
                                "security_code": code,
                                "session_date": day.isoformat(),
                                "kind": "source_knowledge_clock_row",
                                "knowledge_clock_role": item.knowledge_clock_role,
                                "source_knowledge_from": source_cutoff.isoformat(),
                            }
                        )
                    elif close_at is None and source_cutoff is None and day < signal_date:
                        security_date_rows.append(
                            {
                                "security_code": code,
                                "session_date": day.isoformat(),
                                "kind": "date_context_warmup_row",
                            }
                        )
                    elif close_at is None or source_cutoff is None:
                        raise ProductDailyError("missing_session_clock", f"{code}: {day}")
                    pit_cutoff = source_cutoff
                else:
                    # Strict/forward: the per-bar source knowledge cutoff is the
                    # real trading-fact availability instant and keeps the
                    # original per-bar instant requirement unchanged.
                    pit_cutoff = source_cutoff
                    if close_at is None or source_cutoff is None:
                        raise ProductDailyError("missing_session_clock", f"{code}: {day}")
                security_rows.append(
                    daily_input_from_bar(
                        bar=bar,
                        identity=identity,
                        listing_sessions=facts_row.listing_sessions,
                        close_at=close_at,
                        knowledge_cutoff=pit_cutoff,
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
        date_context_rows.extend(security_date_rows)
        source_clock_rows.extend(security_source_clock_rows)
        all_trading_dates.update(security_dates)
        if item.prior_deterioration_sessions is not None:
            prior_det[code] = item.prior_deterioration_sessions

    if not scan_rows and not held_facts:
        raise NoQualifiedRawInputs(
            "every supplied market input failed validation", input_exclusions
        )

    # The scan requires the T+1 session in trading_dates; take it from the bound
    # facts.next_session (the calendar's next open), not invented here.
    all_trading_dates.add(facts.next_session)
    # Under the versioned as-observed replay the scan runs over the bounded
    # named calendar supplied by the entry (bar_context_window + next_session),
    # NOT the full universe_membership union - a 1991 listing keeps its complete
    # membership inside features/listing_sessions but must not drag pre-scope
    # sessions into require_scan_date.  The supplied set already carries the
    # next session; without the token the original union semantics stand.
    effective_trading_dates = (
        sorted(set(scan_trading_dates))
        if scan_trading_dates is not None
        else sorted(all_trading_dates)
    )

    if panel_report_factory is not None:
        # Rebind only the panel_report field; feature_registry/trend_* stay the
        # caller's real objects, so the report is this job's actual computation.
        import dataclasses as _dc

        resources = _dc.replace(resources, panel_report=panel_report_factory(features_by_security))

    if date_context_rows:
        # Every date-context warm-up row admitted under the named context is
        # named inside the signed closure (code, session_date, kind) -- the
        # original nulls stay original, never a clock borrowed from T.  The
        # evidence dict is caller-owned; copy before attaching.
        consumption_evidence = {
            **(dict(consumption_evidence) if consumption_evidence else {}),
            "date_context_rows": tuple(date_context_rows),
            "numeric_context": numeric_context.context_id,
        }
    if source_clock_rows:
        # Every source-knowledge clock row admitted under the verified #749 role
        # is named inside the signed closure with its role and the source
        # knowledge_from that supplied the decision coordinate -- close_at stays
        # the original null, bound verbatim.  The evidence dict is caller-owned.
        consumption_evidence = {
            **(dict(consumption_evidence) if consumption_evidence else {}),
            "source_knowledge_clock_rows": tuple(source_clock_rows),
        }

    return run_daily_advice(
        runner=runner,
        resources=resources,
        rows=scan_rows,
        trading_dates=effective_trading_dates,
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
        pending_evidence=account_view.pending_evidence,
        account_unresolved=account_unresolved,
        raw_security_inputs_digest=sha256_digest(raw_closures),
        account_disposition=account_disposition,
        shared_prereq_ok=shared_prereq_ok,
        shared_prereq_reason=shared_prereq_reason,
        history_receipts=history_receipts,
        consumption_evidence=consumption_evidence,
        replay_context=numeric_context,
        clock=clock,
    )
