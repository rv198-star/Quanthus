"""P12 desktop application use cases: one facade for HTTP, CLI and future daily jobs.

Rules stay in capabilities: accounting values the ledger, risk solves quantities, funding
describes the authoritative partition and decision access validates PIT.  This module only
orchestrates them, checks the trusted scope and adoption for new risk actions, and builds
read models.  Fact registration is never blocked by an expired or unadopted suggestion.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from quanthus.capabilities.accounting import AccountProjection, Position, value_account
from quanthus.capabilities.decision_access import (
    MarketDecision,
    MarketDecisionSet,
    OpportunityStage,
    execution_window_issue,
    validate_decision_set,
)
from quanthus.capabilities.execution import (
    BASELINE_COVERAGE_UNKNOWN,
    Correction,
    ExecutionReport,
    FeeAssessment,
    OrderObservation,
    SyntheticContext,
    coverage_gap_at,
)
from quanthus.capabilities.funding import (
    BUCKET_ASSIGNMENT_CONFLICT,
    FUNDING_CYCLE_NOT_BOUND,
    FUNDING_LEDGER_MISMATCH,
    FundingView,
    PendingAttribution,
    describe_funding,
    describe_partial_funding,
    describe_unbound_funding,
    initial_funding_state,
)
from quanthus.capabilities.risk import EntrySizingInput, HeldExposure, size_entry
from quanthus.product_daily.application.desktop.policy import DesktopPolicyBundle
from quanthus.product_daily.application.desktop.ports import DecisionPackageSource
from quanthus.product_daily.application.desktop.views import (
    AccountSummary,
    CorrectFill,
    FactOutcome,
    FactRecord,
    FundingRulesView,
    OpportunityView,
    PlanApplicability,
    PositionLine,
    PublicationStatus,
    PublishedPackage,
    PublishedPlan,
    RecordsView,
    ReportRecord,
    SubmitFill,
    SubmitOrderEvent,
    WorkbenchView,
)
from quanthus.product_daily.application.exit_planner import size_exit
from quanthus.product_daily.application.history import ConfigurationBinding, FactHistory
from quanthus.product_daily.application.models import Baseline
from quanthus.product_daily.application.service import SyntheticFactService
from quanthus.product_daily.funding import (
    BucketPhase,
    CycleSettlementEvidence,
    FundingDecision,
    FundingState,
    SettlementRequest,
)
from quanthus.shared.fact_contracts import FactError, digest
from quanthus.shared.values import (
    CNY_QUANTUM,
    RATIO_QUANTUM,
    AccountScope,
    MaybeDecimal,
    decimal_text,
)

PACKAGE_SCHEMA_VERSION = "p12.daily-decision-package.v1"
FACT_SOURCE_ID = "desktop-user-confirmation"
OUTSIDE_PLAN_WINDOW = "OUTSIDE_PLAN_WINDOW"
RECONCILIATION_PENDING = "RECONCILIATION_PENDING"
CASH_ONLY = "CASH"
EXCHANGE_TZ_E8 = timezone(timedelta(hours=8))
CONFIGURATION_BINDING_MISMATCH = "CONFIGURATION_BINDING_MISMATCH"
_GROUP_ORDER = {
    "reconcile": 0,
    "exit": 1,
    "entry": 2,
    "holding": 3,
    "unassigned": 4,
    "watch": 5,
    "ended": 6,
}


def plan_id_for(decisions: MarketDecisionSet, decision: MarketDecision) -> str:
    return f"plan-{decision.opportunity_id}-{decisions.market_date.isoformat()}"


def plan_ids(decisions: MarketDecisionSet) -> tuple[str, ...]:
    return tuple(
        plan_id_for(decisions, item)
        for item in decisions.decisions
        if item.stage is OpportunityStage.ENTRY_PLAN
    )


def _package_content(decisions: MarketDecisionSet) -> dict[str, object]:
    """JSON-stable dump of one package's full validated content for binding."""
    import dataclasses

    return json.loads(
        json.dumps(dataclasses.asdict(decisions), default=str, sort_keys=True)
    )


def _marks(decisions: MarketDecisionSet) -> dict[str, Decimal]:
    marks: dict[str, Decimal] = {}
    for item in decisions.decisions:
        if item.chart is not None and item.chart.bars:
            marks[item.security_code] = item.chart.bars[-1].close
    return marks


def _session_close(day) -> datetime:
    # Exchange 15:00 close on a trading date, Asia/Shanghai-aware.
    return datetime.combine(day, time(15, 0), tzinfo=EXCHANGE_TZ_E8)


def _sell_reserved_by_security(history):
    # Open sell-order quantity already reserved per security: any in-flight
    # (non-terminal) sell order holds its remaining quantity so a new plan
    # can never oversell the same shares.
    reserved = {}
    for order in getattr(history, "orders", ()) or ():
        if order.side != "sell" or order.status == "terminal":
            continue
        remaining = max(0, int(order.hold_qty or 0))
        reserved[order.security_code] = reserved.get(order.security_code, 0) + remaining
    return reserved


def _sellable_for(security_code, projection, history, reserved, trading_day):
    # T+1-sellable shares: held minus today buys minus open sell reserves.
    held = int(projection.quantity(security_code))
    today = 0
    for report in getattr(history, "reports", ()) or ():
        if report.security_code != security_code or report.side != "buy":
            continue
        try:
            day = datetime.fromisoformat(str(report.occurred_at)).date()
        except Exception:
            continue
        if day >= trading_day:
            today += int(report.quantity)
    return max(0, held - today - int(reserved.get(security_code, 0)))


def _carried_exit_orders(history) -> dict[str, list]:
    """plan:* sell orders that ended terminal with unfilled quantity.

    The exit intent they confirmed survives their own bound-session close:
    a sell order that ended with quantity still held leaves a residual,
    and this registry is what the next package reads to re-offer that
    same intent under a re-sized plan.  An open order still reserves its
    quantity, so only terminal rows carry.
    """
    carried: dict[str, list] = {}
    for order in getattr(history, "orders", ()) or ():
        if not str(order.order_id).startswith("plan:") or order.side != "sell":
            continue
        residual = int(order.accepted_total_qty) - int(order.known_fill_qty)
        if order.status != "terminal" or residual <= 0:
            continue
        carried.setdefault(order.security_code, []).append(order)
    return carried


def _carried_plan_id(decisions, security_code: str) -> str:
    """Deterministic successor plan id for a carried exit intent."""
    return f"plan-carry-{security_code}-{decisions.market_date.isoformat()}"


def _active_bucket_and_budget(app, history):
    # security -> (bucket_id, locked_cycle_budget) for RESERVED/HELD cycles.
    # The reduce fraction denominator is that cycle locked_cycle_budget.
    bucket_by_sec = {}
    locked_by_sec = {}
    try:
        state = app._facts.funding_state(app._context)
    except FactError:
        return bucket_by_sec, locked_by_sec
    if state is None:
        return bucket_by_sec, locked_by_sec
    funding, _ = state
    try:
        binding = app._facts.funding_binding(app._context) or {}
    except FactError:
        binding = {}
    bucket_secs = dict(binding.get("bucket_securities") or {})
    for item in funding.buckets:
        phase = str(getattr(item.phase, "value", item.phase)).lower()
        if phase not in ("reserved", "held"):
            continue
        sec = bucket_secs.get(item.bucket_id)
        if not sec:
            continue
        locked = getattr(item, "locked_cycle_budget", None)
        bucket_by_sec[sec] = item.bucket_id
        locked_by_sec[sec] = locked
    return bucket_by_sec, locked_by_sec


def _valuation_ref(decisions: MarketDecisionSet) -> str:
    return f"{decisions.source_ref.ref_id}@{decisions.market_date.isoformat()}:close"


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _humanize_bucket_id(bucket_id: str) -> str:
    if bucket_id.lower().startswith("bucket-"):
        suffix = bucket_id.split("-", 1)[1].lstrip("0") or "0"
        if suffix.isdigit():
            return f"{suffix} 号资金桶"
    return bucket_id


class P12DesktopApplication:
    """Use-case facade bound to exactly one trusted account scope."""

    def __init__(
        self,
        *,
        scope: AccountScope,
        context: SyntheticContext,
        facts: SyntheticFactService,
        decisions: DecisionPackageSource,
        policy: DesktopPolicyBundle,
        clock: Callable[[], datetime],
        clock_kind: str,
        sizing_trial_cap: bool = False,
        live_marks: Callable[[Iterable[str]], Mapping[str, Decimal]] | None = None,
        live_valuation_ref: str = "paper-live-quote",
    ) -> None:
        if (context.account_id, context.mode) != (scope.account_id, scope.mode):
            raise FactError("UNAUTHORIZED_CONTEXT", "facts context and account scope differ")
        self._scope = scope
        self._context = context
        self._facts = facts
        self._decisions = decisions
        self._policy = policy
        self._clock = clock
        self._clock_kind = clock_kind
        # When True the published suggestion's sizing is additionally bound
        # by the account's trial-fraction cap, so the suggested quantity and
        # the confirm-time hold share one effective cash ceiling.
        self._sizing_trial_cap = sizing_trial_cap
        self._live_marks = live_marks
        self._live_valuation_ref = live_valuation_ref

    @property
    def scope(self) -> AccountScope:
        return self._scope

    @property
    def policy(self) -> DesktopPolicyBundle:
        return self._policy

    def _require(self, scope: AccountScope) -> None:
        if scope != self._scope:
            raise FactError("UNAUTHORIZED_CONTEXT", "requested account scope is not bound here")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise FactError("INVALID_REPORT_SHAPE", "application clock must be aware")
        return value

    def _current_decisions(self) -> MarketDecisionSet:
        decisions = validate_decision_set(self._decisions.current(self._scope))
        if decisions.mode != self._scope.mode:
            raise FactError("POLICY_NOT_ADOPTED", "decision package mode differs from account")
        return decisions

    def _skipped_plans(self) -> dict:
        '''plan_id -> skip record from the package store's skip log (R14).'''

        store = getattr(self._decisions, "_store", None)
        skipped = getattr(store, "skipped_plans", None)
        return skipped() if callable(skipped) else {}

    def skip_plan(
        self,
        plan_id: str,
        *,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict:
        '''Record that the user skipped an actionable plan (issue #721 R14).

        The skip appends one record to the package store's skip log; the plan
        then reads SKIPPED and can no longer confirm.  It reserves no cash and
        writes no ledger posting; the record is auditable on the records page.'''

        store = getattr(self._decisions, "_store", None)
        skip = getattr(store, "skip_plan", None)
        if not callable(skip):
            raise FactError(
                "POLICY_NOT_ADOPTED",
                "the bound decision source does not support plan skips",
            )
        return skip(
            plan_id=plan_id,
            skipped_at=occurred_at or self._now(),
            reason=reason,
            actor=f"desktop:{self._scope.account_id}",
        )

    # ---- baseline -------------------------------------------------------------------
    def establish_baseline(
        self,
        *,
        request_id: str,
        cash: Decimal,
        quantity: int = 0,
        security_code: str | None = None,
    ) -> AccountProjection:
        """Create the trusted baseline; a held position joins with unknown cost.

        The baseline input is stable across restarts: it names the account's
        opening cash and the plan ids published for the *earliest* dated
        package, not whichever package happens to be current today.  Reopening
        the same store after a newer package is released therefore replays the
        same payload instead of failing the idempotency check.
        """

        try:
            existing = self._facts.snapshot(self._context)
        except FactError:
            existing = None
        if existing is not None:
            return existing.projection
        # The earliest dated package owns the baseline: its entry plans are the
        # dependent plans the store opened with, and they never change on a
        # later publication.
        published = self._decisions.history(self._scope)
        if published:
            decisions = published[0]
            first = (
                security_code
                or (decisions.decisions[0].security_code if decisions.decisions else "CASH")
            )
            baseline_plan_ids = plan_ids(decisions)
        else:
            try:
                decisions = self._current_decisions()
            except FactError as exc:
                # A source whose not-yet-published placeholder cannot be
                # served (no authoritative next_session on this host) must
                # not keep the account store from opening: bind the baseline
                # to an empty plan set; the first real package still owns
                # the recorded binding.
                if exc.reason_code != "INPUT_UNAVAILABLE" or not self._pending_blocked():
                    raise
                decisions = None
            if decisions is not None:
                first = (
                    security_code
                    or (decisions.decisions[0].security_code if decisions.decisions else "CASH")
                )
                baseline_plan_ids = plan_ids(decisions)
            else:
                first = security_code or "CASH"
                baseline_plan_ids = ()
        return self._facts.establish_baseline(
            Baseline(self._context, request_id, cash, quantity, first, 0, baseline_plan_ids)
        )

    def _pending_blocked(self) -> bool:
        '''True when the bound source serves no published package and its
        placeholder is blocked (INPUT_UNAVAILABLE) - only meaningful for the
        daily PublishedPackageSource; other sources return False.'''
        current_stored = getattr(self._decisions, "current_stored", None)
        if not callable(current_stored):
            return False
        try:
            return current_stored() is None
        except Exception:  # noqa: BLE001 - a store read failure is not pending-blocked
            return False

    def bind_configuration(self) -> ConfigurationBinding:
        """Bind this account store to one composition and decision source, once.

        Published plans are derived from (composition, decisions, baseline).  Reopening the
        same store with a different policy would silently re-size historical plans, so the
        first configuration is recorded and any other one is refused with guidance; a new
        account store may use a new policy.  Stores created before bindings existed are
        adopted only if every plan-linked report matches the plan this configuration
        publishes.
        """

        try:
            decisions = self._current_decisions()
        except FactError as exc:
            # The not-yet-published placeholder of the daily package source
            # can be unservable on a host without an authoritative calendar
            # (INPUT_UNAVAILABLE).  The store still binds - to the pending
            # source identity with an empty package set - so the account can
            # open and the workbench surfaces the blocked run receipt.
            if exc.reason_code != "INPUT_UNAVAILABLE" or not self._pending_blocked():
                raise
            decisions = None
        # The binding covers the full content of every package the source has
        # released, not only the source ref: re-opening with the same version
        # whose package content was edited is refused, while a brand-new dated
        # release (a new package, not an edited one) still opens the store.
        published = self._decisions.history(self._scope)
        if decisions is not None:
            source_ref = decisions.source_ref
        else:
            pending_ref = getattr(self._decisions, "pending_source_ref", None)
            if not callable(pending_ref):
                raise FactError(
                    "INPUT_UNAVAILABLE",
                    "decision source blocked and does not expose a pending identity",
                )
            source_ref = pending_ref()
        # Each bound package is fingerprinted over its full validated
        # content (entry bounds, charts, reasons), so editing any field under
        # the same version changes the digest and is refused on reopen.
        package_digests = {
            item.market_date.isoformat(): digest(_package_content(item))
            for item in published
        }
        binding: dict[str, object] = {
            "mode": self._scope.mode,
            "composition": self._policy.composition_ref.to_dict(),
            "decision_source": {
                "ref_id": source_ref.ref_id,
                "version": source_ref.version,
                "package_digests": package_digests,
            },
            "execution_profile": self._policy.execution_profile,
        }
        binding_digest = digest(binding)
        self._refuse_uncovered_legacy_postings()
        existing = self._facts.configuration_binding(self._context)
        if existing is None:
            snapshot = self._facts.snapshot(self._context)
            plans = (
                {p.plan_id: p for p in self._publish(decisions, snapshot.history).plans}
                if decisions is not None
                else {}
            )
            for report in snapshot.history.reports:
                if report.suggestion_id is None:
                    continue
                plan = plans.get(report.suggestion_id)
                if (
                    plan is None
                    or plan.delta_qty != report.intended_quantity
                    or plan.price_cap != report.intended_price_cap
                ):
                    raise FactError(
                        CONFIGURATION_BINDING_MISMATCH,
                        f"report {report.report_id} was filed against a different plan; "
                        "reopen this store with its original configuration",
                    )
            existing = self._facts.bind_configuration(self._context, binding_digest, binding)
        if existing.binding_digest != binding_digest:
            stored = existing.binding
            if self._binding_is_append_only(stored, binding):
                # A new dated release extends the bound package set while every
                # already-bound package keeps its content, so the recorded
                # binding still holds; no re-bind is needed.
                return existing
            composition = stored.get("composition", {})
            source = stored.get("decision_source", {})
            raise FactError(
                CONFIGURATION_BINDING_MISMATCH,
                "account store is bound to composition "
                f"{composition.get('ref_id')}@{composition.get('version')} "
                f"({composition.get('digest')}) and decisions {source.get('ref_id')}@"
                f"{source.get('version')} ({source.get('digest')}); reopen it with that "
                "configuration or start a new account store for the new policy",
            )
        return existing

    def migrate_decision_source(self) -> ConfigurationBinding:
        '''One-time decision-source migration for a bound account store.

        Records the currently bound source under the binding's migrations list
        and rebinds the account to the source this application was assembled
        with (same mode, composition and execution profile).  Ledger history,
        reports and receipts are untouched; the migration is auditable through
        the stored record.  Only a store already bound to a *different* source
        takes this path - a same-source call is a plain rebind.
        '''

        try:
            decisions = self._current_decisions()
        except FactError as exc:
            if exc.reason_code != "INPUT_UNAVAILABLE" or not self._pending_blocked():
                raise
            decisions = None
        published = self._decisions.history(self._scope)
        if decisions is not None:
            source_ref = decisions.source_ref
        else:
            pending_ref = getattr(self._decisions, "pending_source_ref", None)
            if not callable(pending_ref):
                raise FactError(
                    "INPUT_UNAVAILABLE",
                    "decision source blocked and does not expose a pending identity",
                )
            source_ref = pending_ref()
        package_digests = {
            item.market_date.isoformat(): digest(_package_content(item))
            for item in published
        }
        binding: dict[str, object] = {
            "mode": self._scope.mode,
            "composition": self._policy.composition_ref.to_dict(),
            "decision_source": {
                "ref_id": source_ref.ref_id,
                "version": source_ref.version,
                "package_digests": package_digests,
            },
            "execution_profile": self._policy.execution_profile,
        }
        existing = self._facts.configuration_binding(self._context)
        if existing is None:
            raise FactError(
                CONFIGURATION_BINDING_MISMATCH,
                "no bound configuration to migrate; use bind_configuration on a fresh store",
            )
        stored_source = existing.binding.get("decision_source", {})
        if (stored_source.get("ref_id"), stored_source.get("version")) == (
            source_ref.ref_id,
            source_ref.version,
        ):
            return existing
        record = {
            "kind": "decision_source_migration",
            "from": stored_source,
            "to": binding["decision_source"],
            "migrated_at": self._now().isoformat(),
        }
        new_digest = digest(binding)
        return self._facts.migrate_configuration(
            self._context, new_digest, binding, record
        )

    @staticmethod
    def _binding_is_append_only(stored: dict, new: dict) -> bool:
        """True when ``new`` keeps every bound package digest and only adds releases."""
        if stored.get("mode") != new.get("mode"):
            return False
        if stored.get("composition") != new.get("composition"):
            return False
        if stored.get("execution_profile") != new.get("execution_profile"):
            return False
        old_src = stored.get("decision_source", {})
        new_src = new.get("decision_source", {})
        if (old_src.get("ref_id"), old_src.get("version")) != (
            new_src.get("ref_id"),
            new_src.get("version"),
        ):
            return False
        old_digests = old_src.get("package_digests", {})
        new_digests = new_src.get("package_digests", {})
        # Every previously bound package digest must still be present and equal;
        # only additional market dates may be added.
        return all(new_digests.get(day) == dg for day, dg in old_digests.items())

    @staticmethod
    def _visible(decisions: MarketDecisionSet, now: datetime) -> bool:
        """A package and its plans exist for the user only from its publication time."""
        return now >= decisions.published_at

    def _refuse_uncovered_legacy_postings(self) -> None:
        """Refuse to take over a store that already posted a fact dated before its baseline.

        Builds up to 66ddf73d posted such facts without coverage evidence.  Taking the store
        over would present that debit as reconciled, so the store is left untouched (ledger,
        receipts and evidence stay readable with the version that wrote them) and the
        affected reports are named.  Current code never posts such a fact.
        """

        history = self._facts.snapshot(self._context).history
        baseline_as_of = datetime.fromisoformat(history.baseline_as_of)
        affected = [
            f"{report.report_id} ({report.receipt_id}, occurred {report.occurred_at})"
            for report in history.reports
            if report.status == "POSTED"
            and coverage_gap_at(datetime.fromisoformat(report.occurred_at), baseline_as_of)
        ]
        if affected:
            raise FactError(
                BASELINE_COVERAGE_UNKNOWN,
                "takeover refused: posted reports predate the baseline snapshot "
                f"{history.baseline_as_of} without coverage evidence: {', '.join(affected)}; "
                "the store is unchanged; reconcile it with the version that wrote it or "
                "start a new account store",
            )

    def _publication(self, decisions: MarketDecisionSet, now: datetime) -> PublicationStatus:
        retraction = None
        read = getattr(self._decisions, "retraction_for", None)
        if callable(read):
            retraction = read(decisions.market_date)
        next_check_at = None
        next_session = getattr(decisions, "next_session", None)
        if next_session is not None:
            # Issue #721 R15 + #722 r3-3: the next check is the publish point
            # of the next authoritative session at the CONFIGURED publish
            # time-of-day (the source's publish_time), never this package's
            # actual published_at - a same-day late publish (22:35) does not
            # shift tomorrow's configured 22:30.
            pub_time = None
            source_publish = getattr(self._decisions, "publish_time", None)
            if callable(source_publish):
                pub_time = source_publish()
            if pub_time is None:
                pub = decisions.published_at
                pub_time = pub.timetz() if hasattr(pub, "timetz") else pub.time()
            next_check_at = datetime.combine(next_session, pub_time)
        published = self._visible(decisions, now)
        # Issue #722 r6-4: overdue is judged for the CURRENT expected market
        # date - a prior day's package or receipt never masks a present-day
        # overdue.  A stale package stays readable AND still flags the day it
        # failed to refresh; the package-store source answers this directly
        # while a fixture without it keeps the older market_date check.
        overdue = False
        expected_overdue = getattr(self._decisions, "expected_overdue", None)
        if callable(expected_overdue):
            overdue = bool(expected_overdue(now))
        elif not published:
            publish_deadline = getattr(self._decisions, "publish_deadline", None)
            if callable(publish_deadline):
                deadline = publish_deadline(decisions.market_date)
                if deadline is not None and now > deadline:
                    latest_run = getattr(self._decisions, "latest_run", None)
                    run_doc = latest_run() if callable(latest_run) else None
                    ok = (run_doc or {}).get("status") in (
                        "PUBLISHED",
                        "ALREADY_PUBLISHED",
                    )
                    overdue = not ok
        # Issue #721 R16 (r2): published_at is the REAL publish moment only -
        # null while the package is not yet visible to the reader (never the
        # request's own clock passed off as a publish).  observed_at carries
        # the reader's observation moment instead.
        return PublicationStatus(
            status="PUBLISHED" if published else "NOT_YET_PUBLISHED",
            market_date=decisions.market_date,
            published_at=decisions.published_at if published else None,
            retracted=retraction is not None,
            retraction_reason=None if retraction is None else str(retraction.get("reason")),
            next_check_at=next_check_at,
            observed_at=now,
            overdue=overdue,
        )

    # ---- package publication ----------------------------------------------------------
    def _exit_types_unverified(self, decisions: MarketDecisionSet) -> bool:
        # True when no bound receipt proves this package's exit types.
        # Issue #722 r4-1 / #723 E8 r2-1: the package-store reader binds a
        # published package to the run that produced it.  When that binding
        # is absent the reduce/full types cannot be verified - the workbench
        # shows HOLD/MISSING_INPUT and NO sell plan is generated, so package,
        # workbench and confirm all agree the exit stays held.  A fixture
        # without published_run_for has no receipt concept and stays verifiable.
        published_run = getattr(self._decisions, "published_run_for", None)
        if not callable(published_run):
            return False
        run_doc = published_run(decisions.market_date)
        return run_doc is None and any(
            item.stage is OpportunityStage.EXIT_PLAN
            for item in decisions.decisions
        )

    def _publish(self, decisions: MarketDecisionSet, history: FactHistory) -> PublishedPackage:
        """Size each entry intent against the package's account revision (the baseline)."""

        baseline = AccountProjection(
            0,
            0,
            history.baseline_cash,
            (
                (Position(history.baseline_security_code, history.baseline_quantity),)
                if history.baseline_quantity
                else ()
            ),
            0,
        )
        marks = _marks(decisions)
        valuation = value_account(
            baseline,
            marks=marks,
            valuation_ref=_valuation_ref(decisions),
            cost_principal={},
        )
        snapshot_id = f"{decisions.source_ref.digest}:rev0"
        plans = []
        for item in decisions.decisions:
            if item.entry is None:
                continue
            if execution_window_issue(self._policy.execution_profile, decisions, item.entry):
                # No plan is published for a window the execution profile does not admit.
                continue
            # The suggested quantity and the confirm-time hold share one
            # effective cash ceiling: the smaller of the decision's entry
            # fraction and the account's trial-fraction cap, times the bucket
            # budget.  When the trial cap binds below the strategy's intent,
            # the published plan already carries the lot-rounded quantity the
            # account can actually take - the order path then holds the same
            # number instead of accepting an over-cap request.
            trial_fraction = (
                getattr(self._policy.funding, "trial_fraction", None)
                if self._sizing_trial_cap
                else None
            )
            effective_fraction = item.entry.fraction
            if trial_fraction is not None:
                effective_fraction = min(item.entry.fraction, trial_fraction)
            sizing = size_entry(
                self._policy.risk,
                EntrySizingInput(
                    decision_snapshot_id=snapshot_id,
                    as_of=decisions.knowledge_cutoff,
                    opportunity_id=item.opportunity_id,
                    security_code=item.security_code,
                    industry_id=item.entry.industry_id,
                    mark_price=item.entry.mark_price,
                    price_bound=item.entry.price_bound,
                    bucket_budget=self._policy.funding.bucket_budget,
                    fraction=effective_fraction,
                    equity=valuation.economic_equity,
                    cash_available=MaybeDecimal.known(baseline.cash),
                    held=tuple(
                        HeldExposure(p.security_code, None, p.quantity, marks.get(p.security_code))
                        for p in baseline.positions
                    ),
                ),
            )
            current = baseline.quantity(item.security_code)
            target = current + sizing.quantity
            plans.append(
                PublishedPlan(
                    plan_id=plan_id_for(decisions, item),
                    opportunity_id=item.opportunity_id,
                    security_code=item.security_code,
                    side="buy",
                    current_qty=current,
                    target_qty=target,
                    delta_qty=target - current,
                    price_cap=item.entry.price_bound,
                    cash_bound=sizing.total_cost,
                    estimated_fee=sizing.fees,
                    eligible_at=item.entry.eligible_at,
                    expires_at=item.entry.expires_at,
                    window_ref=item.entry.window_ref,
                    invalidations=item.entry.invalidations,
                    reason_codes=sizing.reason_codes,
                    primary_constraint=sizing.primary_reason,
                    risk_check_ref=sizing.decision_digest,
                    depends_on_preopen=tuple(
                        marker.split(":", 1)[1]
                        for marker in item.entry.invalidations
                        if str(marker).startswith("requires:PREOPEN_")
                    ),
                )
            )
        # E8 (issue #723): exit_plan decisions produce sell plans.  A full
        # exit sells the T+1-sellable quantity; a reduce sells only down to
        # the fraction the decision declared, sized against the holding
        # locked_cycle_budget by size_exit.  A decision with no executable
        # quantity still publishes a display-only row; the confirm gate
        # reads the same live sizing and refuses when nothing is sellable.
        marks_map = _marks(decisions)
        sell_reserved = _sell_reserved_by_security(history)
        bucket_by_sec, locked_by_sec = _active_bucket_and_budget(self, history)
        trading_day = self._now().date()
        # E8 r2-1: an exit whose reduce/full type cannot be verified is
        # held, never sold - the package emits no sell plan for it, matching
        # the workbench HOLD/MISSING_INPUT and the confirm-path refusal.
        exit_unverified = self._exit_types_unverified(decisions)
        # E8 r2-6: an unfilled plan sell order ended terminal at the bound
        # session close with its quantity still held.  The next package emits
        # a carry plan for that same intent (even when the package itself
        # adds no new exit signal), re-sized on the live holding and the
        # in-flight quantity.  A position that already went flat keeps no
        # residual intent, so it emits none.
        carried = _carried_exit_orders(history)
        for item in decisions.decisions:
            if item.stage is not OpportunityStage.EXIT_PLAN:
                continue
            if item.exit_kind is None or exit_unverified:
                continue
            # A sell sizes against the LIVE position, not the package's
            # baseline account revision: the quantity that may leave is the
            # holding the account actually carries now.
            current = self._facts.projection(self._context).quantity(item.security_code)
            if current <= 0:
                continue
            mark = marks_map.get(item.security_code)
            bucket_id = bucket_by_sec.get(item.security_code)
            locked = locked_by_sec.get(item.security_code) if bucket_id else None
            sizing = size_exit(
                security_code=item.security_code,
                exit_kind=item.exit_kind,
                target_position_fraction=item.target_position_fraction,
                held_qty=current,
                sellable_qty=_sellable_for(
                    item.security_code,
                    self._facts.projection(self._context),
                    history, sell_reserved, trading_day,
                ),
                mark_price=mark,
                locked_cycle_budget=locked,
                bucket_id=bucket_id,
                lot_size=self._policy.risk.lot_size,
            )
            target = current - sizing.sell_qty
            plans.append(
                PublishedPlan(
                    plan_id=plan_id_for(decisions, item),
                    opportunity_id=item.opportunity_id,
                    security_code=item.security_code,
                    side="sell",
                    current_qty=current,
                    target_qty=target,
                    delta_qty=target - current,
                    price_cap=mark if mark is not None else Decimal("0.00"),
                    cash_bound=None,
                    estimated_fee=None,
                    eligible_at=decisions.published_at,
                    expires_at=_session_close(decisions.next_session),
                    window_ref=f"close-to-next-open:{decisions.next_session.isoformat()}",
                    invalidations=(() if sizing.reason_code is None else (sizing.reason_code,)),
                    # E8 r2-4: an internal sizing reason is projected through
                    # the registered HOLD_AND_ALERT code on the wire, never a
                    # new closed-enum value; the specific Chinese hint rides in
                    # the plan's invalidations/explanation text instead.
                    reason_codes=(
                        () if sizing.reason_code is None else ("HOLD_AND_ALERT",)
                    ),
                    primary_constraint=None if sizing.reason_code is None else "HOLD_AND_ALERT",
                    risk_check_ref=digest({
                        "plan_id": plan_id_for(decisions, item),
                        "exit_kind": item.exit_kind,
                        "target": str(target),
                        "locked": str(locked),
                        "mark": str(mark),
                    }),
                    depends_on_preopen=(),
                )
            )
        # Carried exit intents: securities whose plan sell order ended
        # terminal-unfilled and that this package did NOT just re-decide
        # (a fresh exit_plan for the same security already emits the
        # re-sized plan above; emitting both would double-count the
        # intent).
        fresh_exit_secs = {
            item.security_code
            for item in decisions.decisions
            if item.stage is OpportunityStage.EXIT_PLAN and item.exit_kind is not None
        }
        for security_code, prior_orders in carried.items():
            if security_code in fresh_exit_secs or exit_unverified:
                continue
            current = self._facts.projection(self._context).quantity(security_code)
            if current <= 0:
                continue
            mark = marks_map.get(security_code)
            bucket_id = bucket_by_sec.get(security_code)
            locked = locked_by_sec.get(security_code) if bucket_id else None
            sizing = size_exit(
                security_code=security_code,
                exit_kind="full",
                target_position_fraction=None,
                held_qty=current,
                sellable_qty=_sellable_for(
                    security_code,
                    self._facts.projection(self._context),
                    history, sell_reserved, trading_day,
                ),
                mark_price=mark,
                locked_cycle_budget=locked,
                bucket_id=bucket_id,
                lot_size=self._policy.risk.lot_size,
            )
            target = current - sizing.sell_qty
            plans.append(
                PublishedPlan(
                    plan_id=_carried_plan_id(decisions, security_code),
                    opportunity_id=f"OPP-CARRY-{security_code}",
                    security_code=security_code,
                    side="sell",
                    current_qty=current,
                    target_qty=target,
                    delta_qty=target - current,
                    price_cap=mark if mark is not None else Decimal("0.00"),
                    cash_bound=None,
                    estimated_fee=None,
                    eligible_at=decisions.published_at,
                    expires_at=_session_close(decisions.next_session),
                    window_ref=f"close-to-next-open:{decisions.next_session.isoformat()}",
                    invalidations=(() if sizing.reason_code is None else (sizing.reason_code,)),
                    reason_codes=(
                        () if sizing.reason_code is None else ("HOLD_AND_ALERT",)
                    ),
                    primary_constraint=None if sizing.reason_code is None else "HOLD_AND_ALERT",
                    risk_check_ref=digest({
                        "plan_id": _carried_plan_id(decisions, security_code),
                        "carry_of": [o.order_id for o in prior_orders],
                        "target": str(target),
                        "locked": str(locked),
                        "mark": str(mark),
                    }),
                    depends_on_preopen=(),
                )
            )
        body = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "mode": decisions.mode,
            "market_date": decisions.market_date.isoformat(),
            "knowledge_cutoff": decisions.knowledge_cutoff.isoformat(),
            "source": decisions.source_ref.to_dict(),
            "composition": self._policy.composition_ref.to_dict(),
            "account_revision": 0,
            "plans": [
                {
                    "plan_id": plan.plan_id,
                    "target_qty": plan.target_qty,
                    "price_cap": decimal_text(plan.price_cap),
                    "risk_check_ref": plan.risk_check_ref,
                }
                for plan in plans
            ],
        }
        package_digest = digest(body)
        return PublishedPackage(
            package_id=f"pkg-{decisions.market_date.isoformat()}-{package_digest[7:19]}",
            schema_version=PACKAGE_SCHEMA_VERSION,
            mode=decisions.mode,
            synthetic=decisions.synthetic,
            market_date=decisions.market_date,
            knowledge_cutoff=decisions.knowledge_cutoff,
            published_at=decisions.published_at,
            account_revision=0,
            composition_ref=self._policy.composition_ref,
            source_ref=decisions.source_ref,
            plans=tuple(plans),
            digest=package_digest,
        )

    # ---- account --------------------------------------------------------------------
    def _funding(self, history: FactHistory, revision: int) -> FundingView:
        # Prefer the persisted funding partition (settled cycles, reservations,
        # top-ups) when one is bound; only fall back to a fresh partition from
        # the baseline cash when no funding state has been recorded yet.
        binding = None
        read = getattr(self._facts, "funding_state", None)
        if read is not None:
            try:
                binding = read(self._context)
            except FactError:
                binding = None
        if binding is not None:
            persisted, bound_revision = binding
            # The page reconciles the persisted partition with the account
            # ledger the same way the write path does: bucket cash plus
            # proceeds still awaiting attribution must equal recorded cash.
            # Movements already explainable by a bound bucket's security are
            # pending settlement, not a mismatch; only an unexplained gap
            # marks the account for manual review.
            account_cash = history.baseline_cash + sum(
                (item.cash_delta for item in history.events),
                Decimal("0.00"),
            )
            accountable = getattr(
                self._facts, "funding_accountable_cash", None
            )
            accountable_total = (
                accountable(self._context)
                if callable(accountable)
                else persisted.cash_total
            )
            pending_read = getattr(
                self._facts, "funding_pending_by_bucket", None
            )
            pending = (
                pending_read(self._context)
                if callable(pending_read)
                else None
            )
            held_read = getattr(
                self._facts, "funding_held_by_bucket", None
            )
            held = (
                held_read(self._context)
                if callable(held_read)
                else None
            )
            if accountable_total != account_cash:
                # A gap the partition cannot explain is listed, not hidden:
                # fills with no owning bucket are surfaced as pending
                # attribution so the known buckets and reserve still
                # reconcile against the account (gap = unattributed cash).
                unattributed_read = getattr(
                    self._facts, "funding_unattributed_fills", None
                )
                unattributed = (
                    unattributed_read(self._context)
                    if callable(unattributed_read)
                    else None
                )
                if unattributed:
                    pending_attrs = tuple(
                        PendingAttribution(
                            order_id=item.get("order_id"),
                            security_code=item["security_code"],
                            cash_delta=item["cash_delta"],
                            side=item.get("side"),
                            quantity=item.get("quantity"),
                            price=_optional_decimal(item.get("price")),
                        )
                        for item in unattributed
                    )
                    # Partial view only when the listed fills explain the
                    # whole gap; otherwise the partition is genuinely short
                    # and the page keeps every amount marked for review.
                    if sum(
                        (p.cash_delta for p in pending_attrs), Decimal("0.00")
                    ) == (account_cash - accountable_total):
                        return describe_partial_funding(
                            self._policy.funding,
                            persisted,
                            state_account_revision=bound_revision,
                            current_account_revision=revision,
                            pending_by_bucket=pending,
                            held_by_bucket=held,
                            pending_attributions=pending_attrs,
                        )
                return describe_unbound_funding(
                    self._policy.funding, FUNDING_LEDGER_MISMATCH
                )
            return describe_funding(
                self._policy.funding,
                persisted,
                state_account_revision=bound_revision,
                current_account_revision=revision,
                pending_by_bucket=pending,
                held_by_bucket=held,
            )
        try:
            state = initial_funding_state(self._policy.funding, history.baseline_cash)
        except FactError as exc:
            if exc.reason_code != BUCKET_ASSIGNMENT_CONFLICT:
                raise
            return describe_unbound_funding(self._policy.funding, BUCKET_ASSIGNMENT_CONFLICT)
        # The synthetic partition is only the opening snapshot.  Once the
        # ledger has moved without a bound partition the snapshot no longer
        # describes the account, so the page marks the amounts for review
        # instead of presenting stale balances as current.
        account_cash = history.baseline_cash + sum(
            (item.cash_delta for item in history.events),
            Decimal("0.00"),
        )
        if state.cash_total != account_cash:
            return describe_unbound_funding(
                self._policy.funding, FUNDING_CYCLE_NOT_BOUND
            )
        return describe_funding(
            self._policy.funding,
            state,
            state_account_revision=0,
            current_account_revision=revision,
        )

    def _pending(self, history: FactHistory) -> int:
        return sum(1 for item in history.reports if item.status == "NEEDS_RECONCILIATION") + sum(
            1 for item in history.corrections if item.status == "NEEDS_RECONCILIATION"
        )

    def _account(
        self,
        projection: AccountProjection,
        history: FactHistory,
        decisions: MarketDecisionSet | None,
        funding: FundingView,
        now: datetime,
    ) -> AccountSummary:
        # Closing prices are market facts known at the cutoff; plan links wait for publication.
        market = (
            decisions
            if (decisions is not None and now >= decisions.knowledge_cutoff)
            else None
        )
        linked = (
            decisions
            if (decisions is not None and self._visible(decisions, now))
            else None
        )
        principal: dict[str, MaybeDecimal] = {}
        for fact in history.facts:
            known = principal.get(fact.security_code, MaybeDecimal.known(Decimal("0")))
            if known.value is not None:
                principal[fact.security_code] = MaybeDecimal.known(
                    known.value + fact.price * fact.quantity
                )
        if history.baseline_quantity:
            principal[history.baseline_security_code] = MaybeDecimal.unknown(
                "BASELINE_COVERAGE_UNKNOWN"
            )
        marks = dict(_marks(market)) if market else {}
        valuation_ref = _valuation_ref(market) if market else None
        missing_codes = [
            pos.security_code
            for pos in projection.positions
            if pos.quantity > 0 and pos.security_code not in marks
        ]
        if missing_codes and self._live_marks is not None:
            try:
                extra = self._live_marks(missing_codes) or {}
            except Exception:  # noqa: BLE001 - fail closed to VALUATION_UNAVAILABLE
                extra = {}
            used_live = False
            for code in missing_codes:
                price = _optional_decimal(extra.get(code))
                if price is not None and price > 0:
                    marks[code] = price
                    used_live = True
            if used_live:
                live_ref = (
                    f"{self._live_valuation_ref}@{now.isoformat(timespec='seconds')}"
                )
                valuation_ref = (
                    f"{valuation_ref}+{live_ref}" if valuation_ref is not None else live_ref
                )
        valuation = value_account(
            projection,
            marks=marks,
            valuation_ref=valuation_ref,
            cost_principal=principal,
        )
        names_source = market if market is not None else decisions
        names = {
            item.security_code: item.security_name
            for item in (names_source.decisions if names_source is not None else ())
        }
        opportunities = {
            item.security_code: item.opportunity_id for item in (linked.decisions if linked else ())
        }
        equity = valuation.economic_equity.value
        lines = []
        top: tuple[Decimal, str] | None = None
        for row in valuation.positions:
            if equity is None or equity <= 0 or row.market_value.value is None:
                weight = MaybeDecimal.unknown(
                    valuation.economic_equity.reason_code or "VALUATION_UNAVAILABLE"
                )
            else:
                ratio = (row.market_value.value / equity).quantize(RATIO_QUANTUM)
                weight = MaybeDecimal.known(ratio)
                if top is None or ratio > top[0]:
                    top = (ratio, row.security_code)
            lines.append(
                PositionLine(
                    security_code=row.security_code,
                    security_name=names.get(row.security_code),
                    quantity=row.quantity,
                    mark_price=row.mark_price,
                    market_value=row.market_value,
                    average_cost=row.average_cost,
                    weight=weight,
                    opportunity_id=opportunities.get(row.security_code),
                )
            )
        pending = self._pending(history)
        occupied = sum(
            (order.hold_amount for order in history.orders),
            Decimal("0.00"),
        ).quantize(CNY_QUANTUM)
        order_pending = any(o.needs_reconciliation for o in history.orders)
        # A fill with no owning bucket is a real reconciliation item: the
        # workbench must not claim the account is fully aligned while the
        # funding page lists cash awaiting attribution.
        attribution_pending = len(getattr(funding, "pending_attributions", ()) or ())
        if occupied > 0:
            # A bounded order hold keeps a known residual even while it needs
            # reconciliation (EX08: 605 held, 18,195 still usable).
            available = MaybeDecimal.known(
                max(Decimal("0.00"), projection.cash - occupied)
            )
        elif pending or order_pending:
            available = MaybeDecimal.unknown(RECONCILIATION_PENDING)
        else:
            available = MaybeDecimal.known(projection.cash)
        if top is not None:
            concentration = MaybeDecimal.known(top[0])
        elif projection.positions:
            concentration = MaybeDecimal.unknown(
                valuation.economic_equity.reason_code or "VALUATION_UNAVAILABLE"
            )
        else:
            concentration = MaybeDecimal.known(Decimal("0.0000"))
        return AccountSummary(
            scope=self._scope,
            account_revision=projection.revision,
            event_sequence=projection.event_sequence,
            as_of=now,
            valuation_ref=valuation.valuation_ref,
            economic_equity=valuation.economic_equity,
            cash_total=projection.cash.quantize(CNY_QUANTUM),
            cash_available_for_new=available,
            cash_occupied=occupied,
            positions_value=valuation.positions_value,
            reserve=funding.reserve,
            top_concentration=concentration,
            top_concentration_security=top[1] if top else None,
            reconciliation_status=(
                "NEEDS_RECONCILIATION"
                if (pending or order_pending or attribution_pending)
                else "CLEAN"
            ),
            pending_reconciliations=(
                pending
                + sum(1 for o in history.orders if o.needs_reconciliation)
                + attribution_pending
            ),
            ledger_digest=projection.digest,
            positions=tuple(lines),
        )

    # ---- applicability --------------------------------------------------------------
    @staticmethod
    def _window_deviation(plan: PublishedPlan, occurred_at: datetime) -> tuple[str, ...]:
        inside = plan.eligible_at <= occurred_at <= plan.expires_at
        return () if inside else (OUTSIDE_PLAN_WINDOW,)

    def _applicability(
        self,
        plan: PublishedPlan,
        projection: AccountProjection,
        history: FactHistory,
        blockers: tuple[str, ...],
        now: datetime,
    ) -> PlanApplicability:
        statuses = {item.plan_id: item.status for item in history.plans}
        current = projection.quantity(plan.security_code)
        reasons: list[str] = []
        linked = [r for r in history.reports if r.suggestion_id == plan.plan_id]
        # Issue #721 R14: a skipped plan can no longer confirm.  The skip is
        # recorded in the package store's append-only log; it reserves no cash
        # and writes no ledger fact.
        skipped = self._skipped_plans()
        if plan.plan_id in skipped:
            return PlanApplicability(
                plan_id=plan.plan_id,
                status="SKIPPED",
                current_qty=current,
                delta_qty=plan.target_qty - current,
                account_revision=projection.revision,
                reason_codes=(),
                next_step="NONE",
            )
        has_deviation = any(r.deviations for r in linked) or any(
            self._window_deviation(plan, datetime.fromisoformat(r.occurred_at))
            for r in linked
        )
        # A plan reported in full and without deviation reads as executed, not
        # as pending review: a clean buy is held, a clean exit is complete.  The
        # stored NEEDS_REASSESSMENT mark is left for audit but does not reopen
        # the user-facing status when the position already matches the plan.
        buy_done = plan.side == "buy" and current >= plan.target_qty and bool(linked)
        sell_done = plan.side == "sell" and current == 0 and bool(linked)
        clean_done = (buy_done or sell_done) and not has_deviation
        if clean_done:
            status, step = "ACTIONABLE" if buy_done else "EXPIRED", "NONE"
        elif plan.plan_id not in statuses:
            status, step = "NOT_BOUND", "RECONCILE_FACTS"
            reasons.append("BASELINE_COVERAGE_UNKNOWN")
        elif blockers:
            status, step = "POLICY_NOT_ADOPTED", "WAIT_FOR_ADOPTION"
            reasons.append("POLICY_NOT_ADOPTED")
        elif statuses[plan.plan_id] == "NEEDS_REASSESSMENT":
            status, step = "NEEDS_REASSESSMENT", "REASSESS_PLAN"
            for report in history.reports:
                if report.suggestion_id == plan.plan_id:
                    reasons.extend(report.deviations)
                    occurred = datetime.fromisoformat(report.occurred_at)
                    reasons.extend(self._window_deviation(plan, occurred))
        elif now > plan.expires_at:
            status, step = "EXPIRED", "NONE"
            reasons.append(OUTSIDE_PLAN_WINDOW)
        elif now < plan.eligible_at:
            status, step = "SCHEDULED", "WAIT_FOR_WINDOW"
        else:
            status, step = "ACTIONABLE", "EXECUTE_AND_REPORT"
        unique = tuple(dict.fromkeys(reasons))
        return PlanApplicability(
            plan_id=plan.plan_id,
            status=status,
            current_qty=current,
            delta_qty=plan.target_qty - current,
            account_revision=projection.revision,
            reason_codes=unique,
            next_step=step,
        )

    def _opportunity(
        self,
        item: MarketDecision,
        decisions: MarketDecisionSet,
        package: PublishedPackage,
        projection: AccountProjection,
        history: FactHistory,
        account: AccountSummary,
        blockers: tuple[str, ...],
        now: datetime,
        reduce_exit_ids: frozenset[str] = frozenset(),
        exit_types_unverified: bool = False,
    ) -> OpportunityView:
        plan = next((p for p in package.plans if p.opportunity_id == item.opportunity_id), None)
        applicability = (
            self._applicability(plan, projection, history, blockers, now) if plan else None
        )
        reconcile = any(
            r.security_code == item.security_code and r.status == "NEEDS_RECONCILIATION"
            for r in history.reports
        )
        changes: list[str] = []
        if item.stage is OpportunityStage.ENTRY_PLAN:
            changes.append("NEW_ENTRY_PLAN")
        if any(r.security_code == item.security_code for r in history.reports):
            changes.append("FACT_RECEIVED")
        if any(
            c.status == "POSTED"
            and any(
                f.fact_id == c.fact_id and f.security_code == item.security_code
                for f in history.facts
            )
            for c in history.corrections
        ):
            changes.append("FACT_CORRECTED")
        stage = item.stage
        reasons = list(item.reason_codes)
        primary = plan.primary_constraint if plan else None
        next_observation_override: str | None = None
        window_issue = (
            execution_window_issue(self._policy.execution_profile, decisions, item.entry)
            if item.entry is not None
            else None
        )
        if window_issue is not None:
            action, status = "PAUSED", "MISSING_INPUT"
            primary = window_issue
            reasons.append(window_issue)
        elif stage is OpportunityStage.ENTRY_PLAN and (plan is None or plan.delta_qty <= 0):
            # Published without an executable quantity (for example INSUFFICIENT_CAPACITY).
            action, status = "WAIT", "WAITING"
            if plan is not None:
                reasons.extend(plan.reason_codes)
        elif stage is OpportunityStage.ENTRY_PLAN and applicability is not None:
            status = {
                "ACTIONABLE": "READY",
                "SCHEDULED": "READY",
                "SKIPPED": "ENDED",
                "NEEDS_REASSESSMENT": "NEEDS_REASSESSMENT",
                "EXPIRED": "EXPIRED",
                "POLICY_NOT_ADOPTED": "POLICY_NOT_ADOPTED",
                "NOT_BOUND": "NEEDS_RECONCILIATION",
            }[applicability.status]
            if status == "READY" and applicability.delta_qty > 0:
                action = "BUY"
            elif status == "READY":
                action = "HOLD"
            elif status in {"NEEDS_REASSESSMENT", "NEEDS_RECONCILIATION"}:
                action = "REVIEW"
            elif status == "POLICY_NOT_ADOPTED":
                action = "PAUSED"
            elif status == "ENDED":
                action = "NONE"
            else:
                action = "NONE"
            # A cleanly executed plan reads as held/completed, never as pending
            # review: a filled buy is held, a fully sold exit is done.
            current_qty = projection.quantity(item.security_code)
            linked = [r for r in history.reports if r.suggestion_id == plan.plan_id]
            clean = not any(r.deviations for r in linked) and not any(
                self._window_deviation(plan, datetime.fromisoformat(r.occurred_at))
                for r in linked
            )
            if clean and plan.side == "buy" and current_qty >= plan.target_qty:
                status, action = "READY", "HOLD"
            elif clean and plan.side == "sell" and current_qty == 0 and linked:
                status, action = "ENDED", "NONE"
            reasons.extend(applicability.reason_codes)
            if status != "READY" and applicability.reason_codes:
                # The account-side reason now explains the state better than the sizing limit.
                primary = applicability.reason_codes[0]
        elif stage is OpportunityStage.WAITING:
            action, status = "WAIT", "WAITING"
        elif stage is OpportunityStage.HOLDING:
            action, status = "HOLD", "READY"
        elif stage is OpportunityStage.EXIT_PLAN:
            # Issue #723 comment-7117663809: an exit_plan whose held_signal
            # action is "reduce" is a 减仓建议, never a full liquidation
            # task in batch A - the position stays held and the page shows
            # the reduction suggestion; only action "exit" produces the
            # sell task.
            if exit_types_unverified:
                # Issue #722 r4-1: no receipt binds this package's digest
                # and run identity - the reduce/full type cannot be
                # verified.  The plan stays visible but is not confirmable:
                # HOLD + MISSING_INPUT ("退出类型无法核实") rather than a
                # guessed full sell.
                # Issue #722 r7-1: the plan reads as HELD, not as an exit -
                # the backend no longer hands the desktop a sell action,
                # quantity or sell explanation, so neither the new nor the
                # R4 desktop can show "卖出 N 股" / "触发卖出计划" / "卖出
                # 确认照常放行".  The stage rewrites to a held row and the
                # reason text explains the unverifiable exit.
                stage = OpportunityStage.HOLDING
                action, status = "HOLD", "MISSING_INPUT"
                # Issue #722 r6-2: reuse the registered INPUT_UNAVAILABLE
                # reason (no new closed enum) - the unverifiable-exit note is
                # carried in the explanation text, not a new reason value.
                if "INPUT_UNAVAILABLE" not in reasons:
                    reasons.append("INPUT_UNAVAILABLE")
                # Issue #722 r7-1: the sell-signal reason and the producer's
                # sell explanation never reach the page for an unverifiable
                # exit - no desktop generation may render a guessed sell
                # action, quantity or "达到退出条件，按计划卖出" for it.
                reasons = [r for r in reasons if r != "EXIT_SIGNAL"]
                primary = "INPUT_UNAVAILABLE"
                next_observation_override = (
                    "退出类型无法核实，暂不确认；持仓继续持有。"
                )
            else:
                is_reduce = (
                    item.exit_kind == "reduce"
                    or item.opportunity_id in reduce_exit_ids
                )
                # E8 (#723 comment-7117737408): a v2 exit_plan carries its own
                # exit_kind, and _publish sized a sell plan for it.  An
                # executable plan (delta_qty < 0) is a confirmable SELL or
                # REDUCE task; a plan that sized to zero stays a display row
                # with the sizing reason (below target / residue-zero / budget
                # unknown / T+1 lock), never an implicit order.  A legacy v1
                # exit_plan without exit_kind keeps the batch-A behaviour:
                # reduce shows 减仓建议 and a full exit still offers
                # the whole-quantity sell from the plan-less display path.
                executable = plan is not None and plan.delta_qty < 0
                if is_reduce:
                    action, status = (
                        ("SELL", "READY")
                        if executable
                        else ("HOLD", "READY")
                    )
                else:
                    action, status = (
                        ("SELL", "READY")
                        if (executable or item.exit_kind is None)
                        else ("HOLD", "READY")
                    )
                if plan is not None and plan.primary_constraint:
                    reasons.append(plan.primary_constraint)
                    primary = plan.primary_constraint
            # Once the position is flat the exit is complete: it no longer
            # prompts a sell.  An exit plan is sized for the whole held
            # quantity, so quantity 0 after its report marks it done — but only
            # after every sell order is terminal and fee-confirmed.  A flat
            # position whose whole-order fee or terminal close is still pending
            # stays a live sell task, not an ended one.
            if projection.quantity(item.security_code) == 0:
                # The exit reported the whole position sold; no sell remains.
                sold = any(
                    r.security_code == item.security_code and r.side == "sell"
                    for r in history.reports
                )
                sell_orders = [
                    o
                    for o in history.orders
                    if o.security_code == item.security_code and o.side == "sell"
                ]
                complete = bool(sell_orders) and all(
                    o.status == "terminal" and o.settlement_ready for o in sell_orders
                )
                if sold and complete:
                    action, status = "NONE", "ENDED"
        elif stage is OpportunityStage.INPUT_BLOCKED:
            action, status = "PAUSED", "MISSING_INPUT"
            primary = next(iter(item.reason_codes), None)
        else:
            action, status = "NONE", "ENDED"
        if reconcile:
            status, action = "NEEDS_RECONCILIATION", "REVIEW"
            reasons.append(RECONCILIATION_PENDING)
        group = {
            OpportunityStage.ENTRY_PLAN: "entry",
            OpportunityStage.EXIT_PLAN: "exit",
            OpportunityStage.HOLDING: "holding",
            OpportunityStage.WAITING: "watch",
            OpportunityStage.INPUT_BLOCKED: "watch",
            OpportunityStage.ENDED: "ended",
        }[stage]
        if reconcile:
            group = "reconcile"
        cost = next(
            (p.average_cost for p in account.positions if p.security_code == item.security_code),
            MaybeDecimal.unknown("NO_POSITION"),
        )
        exit_kind = None
        if stage is OpportunityStage.EXIT_PLAN and not exit_types_unverified:
            # v2 decisions self-describe their exit kind; v1 falls back to the
            # published run's held_signals for the same distinction.
            exit_kind = (
                item.exit_kind
                if item.exit_kind is not None
                else ("reduce" if item.opportunity_id in reduce_exit_ids else "full")
            )
        return OpportunityView(
            opportunity_id=item.opportunity_id,
            security_code=item.security_code,
            security_name=item.security_name,
            stage=stage.value,
            queue_group=group,
            action_code=action,
            status_code=status,
            reason_codes=tuple(dict.fromkeys(reasons)),
            primary_constraint=primary,
            changes=tuple(changes),
            explanations=item.explanations,
            next_observation=(
                next_observation_override
                if next_observation_override is not None
                else item.next_observation
            ),
            chart=item.chart,
            cost_price=cost,
            plan=plan,
            applicability=applicability,
            exit_kind=exit_kind,
        )

    def _state(
        self,
    ) -> tuple[
        MarketDecisionSet, FactHistory, AccountProjection, PublishedPackage, FundingView, datetime
    ]:
        now = self._now()  # one clock reading per read model
        decisions = self._current_decisions()
        snapshot = self._facts.snapshot(self._context)
        history, projection = snapshot.history, snapshot.projection
        package = self._publish(decisions, history)
        funding = self._funding(history, projection.revision)
        return decisions, history, projection, package, funding, now

    # ---- queries --------------------------------------------------------------------
    def _maintain(self) -> None:
        """Idempotent write-through on the pages a user opens.

        A published executable entry plan reserves an empty bucket and a flat
        security settles its open cycle, so the page reflects the automatic
        funding lifecycle without the user touching a control.  These stay on
        the user-facing reads, never inside the snapshot pair, so a concurrent
        reader still observes one consistent old state.
        """
        try:
            decisions = self._current_decisions()
            now = self._now()
            if not self._visible(decisions, now):
                return
            snapshot = self._facts.snapshot(self._context)
            package = self._publish(decisions, snapshot.history)
            for plan in package.plans:
                if plan.delta_qty > 0:
                    self._auto_reserve(plan.security_code)
            self._upgrade_pre_binding_fills(snapshot)
            self._auto_settle(None)
        except FactError:
            return

    def workbench(self, scope: AccountScope) -> WorkbenchView:
        self._require(scope)
        self._maintain()
        try:
            decisions, history, projection, package, funding, now = self._state()
        except FactError as exc:
            if exc.reason_code != "INPUT_UNAVAILABLE" or not self._pending_blocked():
                raise
            return self._blocked_workbench(scope, exc)
        account = self._account(projection, history, decisions, funding, now)
        blockers = self._policy.missing_for_action(scope.mode)
        visible = self._visible(decisions, now)
        # Issue #723 comment-7117663809 + #722 r3-1: the run receipt's
        # held_signals carry the producer action; a "reduce" action marks the
        # matching exit_plan as a 减仓建议 on the workbench.  The binding
        # reads the receipt of the run that PUBLISHED this package
        # (decisions.market_date, status PUBLISHED/ALREADY_PUBLISHED) - a
        # later INPUT_BLOCKED or precheck receipt on the same date never
        # rewrites the published package's actions.
        published_run = getattr(self._decisions, "published_run_for", None)
        run_doc = (
            published_run(decisions.market_date)
            if callable(published_run)
            else None
        )
        # Issue #722 r4-1: when no receipt binds THIS package's digest and
        # run identity, the exit plans' reduce/full types cannot be
        # verified - the workbench shows "退出类型无法核实" and the plan is
        # not confirmable, never a guessed full sell.  This only applies to
        # sources that implement published_run_for (the package-store
        # reader); a replay fixture without the method has no receipt
        # concept and its exit plans are already self-describing.
        exit_types_unverified = (
            callable(published_run)
            and run_doc is None
            and any(
                item.stage is OpportunityStage.EXIT_PLAN
                for item in decisions.decisions
            )
        )
        reduce_exit_ids = frozenset(
            str(sig.get("opportunity_id"))
            for sig in (run_doc or {}).get("held_signals") or ()
            if str(sig.get("action")).lower() == "reduce"
        )
        items = [
            self._opportunity(
                item, decisions, package, projection, history, account,
                blockers, now, reduce_exit_ids, exit_types_unverified,
            )
            for item in (decisions.decisions if visible else ())
            # The no-qualified-candidate no-op marks a published "nothing to
            # do" day: it is evidence, never a queue row, a position or an
            # order surface.
            if item.opportunity_id != "no-qualified-candidate"
        ]
        known = {item.security_code for item in decisions.decisions}
        active_buckets = self._active_bucket_by_security(funding)
        pending_attr_secs = {
            item.security_code
            for item in (getattr(funding, "pending_attributions", ()) or ())
        }
        reconcile_secs = self._reconciliation_securities(history)
        for line in account.positions if visible else ():
            if line.security_code in known:
                continue
            bucket_id = active_buckets.get(line.security_code)
            if (
                bucket_id is not None
                and line.security_code not in pending_attr_secs
                and line.security_code not in reconcile_secs
            ):
                bucket_label = _humanize_bucket_id(bucket_id)
                items.append(
                    OpportunityView(
                        opportunity_id=f"paper-holding:{line.security_code}",
                        security_code=line.security_code,
                        security_name=line.security_name or line.security_code,
                        stage=OpportunityStage.HOLDING.value,
                        queue_group="holding",
                        action_code="HOLD",
                        status_code="READY",
                        reason_codes=(),
                        primary_constraint=None,
                        changes=(),
                        explanations=(),
                        next_observation=f"自主模拟持仓，已归入 {bucket_label}，继续持有",
                        chart=None,
                        cost_price=line.average_cost,
                        plan=None,
                        applicability=None,
                    )
                )
                continue
            items.append(
                OpportunityView(
                    opportunity_id=f"unassigned:{line.security_code}",
                    security_code=line.security_code,
                    security_name=line.security_code,
                    stage=OpportunityStage.HOLDING.value,
                    queue_group="unassigned",
                    action_code="REVIEW",
                    status_code="NEEDS_RECONCILIATION",
                    reason_codes=("EXTERNAL_TRADE",),
                    primary_constraint="EXTERNAL_TRADE",
                    changes=(),
                    explanations=(),
                    next_observation=None,
                    chart=None,
                    cost_price=line.average_cost,
                    plan=None,
                    applicability=None,
                )
            )
        items.sort(key=lambda x: (_GROUP_ORDER[x.queue_group], x.opportunity_id))
        run_receipt = None
        latest_run = getattr(self._decisions, "latest_run", None)
        if callable(latest_run):
            run_receipt = latest_run()
        return WorkbenchView(
            scope=scope,
            package=package if visible else None,
            publication=self._publication(decisions, now),
            clock=now,
            clock_kind=self._clock_kind,
            account=account,
            opportunities=tuple(items),
            orders=history.orders,
            action_blockers=blockers,
            run_receipt=run_receipt,
        )

    def _blocked_workbench(self, scope: AccountScope, exc: FactError) -> WorkbenchView:
        '''Workbench rendered when the daily source has no published package
        and its placeholder is unservable (no authoritative next_session).

        The page surfaces the run receipt (INPUT_BLOCKED / FAILED) plus a
        NOT_YET_PUBLISHED status instead of failing the whole request, so a
        blocked day reads as "advice not generated" - never as a fake
        package.  Account facts (positions, cash, ledger) stay readable.'''
        now = self._now()
        snapshot = self._facts.snapshot(self._context)
        history, projection = snapshot.history, snapshot.projection
        funding = self._funding(history, projection.revision)
        run_receipt = None
        latest_run = getattr(self._decisions, "latest_run", None)
        if callable(latest_run):
            run_receipt = latest_run()
        market_date = now.date()
        if run_receipt and run_receipt.get("market_date"):
            with contextlib.suppress(ValueError):
                market_date = type(market_date).fromisoformat(str(run_receipt["market_date"]))
        blockers = self._policy.missing_for_action(scope.mode)
        account = self._account(projection, history, None, funding, now)
        blockers = tuple(dict.fromkeys((*blockers, "INPUT_UNAVAILABLE")))
        # Issue #721 R15 (r2): the blocked page still names the next check -
        # the publish point of the next authoritative session when the
        # forward calendar resolves one (None when it does not).  The
        # publish time-of-day comes from the run receipt's configured
        # decision cutoff; a receipt without it falls back to 22:30+08.
        next_check_at = None
        provider = getattr(self._decisions, "_next_session_provider", None)
        if callable(provider):
            nxt = provider(now.date())
            if nxt is not None and nxt > now.date():
                # The publish time-of-day is the source's configured publish
                # point (audit R15).  A pending placeholder publishes at the
                # same moment, so the blocked page and the normal pending page
                # agree on the next check.  A receipt's decision_cutoff is the
                # run's configured cutoff, not the publish display.
                cutoff_time = time(22, 30)
                source_publish = getattr(self._decisions, "publish_time", None)
                if callable(source_publish):
                    candidate = source_publish()
                    if candidate is not None:
                        cutoff_time = (
                            candidate.timetz()
                            if hasattr(candidate, "timetz")
                            else candidate
                        )
                next_check_at = datetime.combine(nxt, cutoff_time)
        # Issue #722 r3-3: the blocked page carries the same overdue judgment -
        # a blocked INPUT_UNAVAILABLE day is a run receipt, so a package absent
        # past the deadline with no successful receipt is overdue.
        overdue = False
        # Issue #722 r6-4: the blocked page also judges the CURRENT expected
        # market date, not the displayed receipt's date - a prior day's
        # package or receipt never masks a present-day overdue.
        expected_overdue = getattr(self._decisions, "expected_overdue", None)
        if callable(expected_overdue):
            overdue = bool(expected_overdue(now))
        else:
            publish_deadline = getattr(self._decisions, "publish_deadline", None)
            if callable(publish_deadline):
                deadline = publish_deadline(market_date)
                if deadline is not None and now > deadline:
                    ok = (run_receipt or {}).get("status") in (
                        "PUBLISHED",
                        "ALREADY_PUBLISHED",
                    )
                    overdue = not ok
        return WorkbenchView(
            scope=scope,
            package=None,
            publication=PublicationStatus(
                status="NOT_YET_PUBLISHED",
                market_date=market_date,
                published_at=None,
                retracted=False,
                retraction_reason=None,
                next_check_at=next_check_at,
                observed_at=now,
                overdue=overdue,
            ),
            clock=now,
            clock_kind=self._clock_kind,
            account=account,
            opportunities=(),
            orders=history.orders,
            action_blockers=blockers,
            run_receipt=run_receipt,
        )

    def _active_bucket_by_security(self, funding: FundingView) -> dict[str, str]:
        """Map security_code -> active bucket_id for clean bound buckets only."""
        if funding.status_reason in (
            FUNDING_LEDGER_MISMATCH,
            BUCKET_ASSIGNMENT_CONFLICT,
            FUNDING_CYCLE_NOT_BOUND,
        ):
            return {}
        read_binding = getattr(self._facts, "funding_binding", None)
        if not callable(read_binding):
            return {}
        binding = read_binding(self._context) or {}
        bucket_securities = dict(binding.get("bucket_securities") or {})
        if not bucket_securities:
            return {}
        result: dict[str, str] = {}
        for bucket in funding.buckets:
            if str(bucket.phase).lower() not in ("reserved", "held"):
                continue
            if not bucket.cycle_id:
                continue
            sec = bucket_securities.get(bucket.bucket_id)
            if sec:
                result[sec] = bucket.bucket_id
        return result

    @staticmethod
    def _reconciliation_securities(history: FactHistory) -> set[str]:
        """Securities currently carrying a reconciliation conflict or warning."""
        flagged: set[str] = set()
        fact_sec = {f.fact_id: f.security_code for f in history.facts}
        for report in history.reports:
            if report.status == "NEEDS_RECONCILIATION" or report.conflicts:
                flagged.add(report.security_code)
        for corr in history.corrections:
            if corr.status == "NEEDS_RECONCILIATION":
                sec = fact_sec.get(corr.fact_id)
                if sec:
                    flagged.add(sec)
        for order in history.orders:
            if order.needs_reconciliation:
                flagged.add(order.security_code)
        return flagged

    def records(self, scope: AccountScope) -> RecordsView:
        self._require(scope)
        decisions, history, projection, package, funding, now = self._state()
        visible = self._visible(decisions, now)
        plans = {plan.plan_id: plan for plan in package.plans} if visible else {}
        reports = []
        for report in history.reports:
            deviations = list(report.deviations)
            plan = plans.get(report.suggestion_id or "")
            if plan is not None:
                deviations.extend(
                    self._window_deviation(plan, datetime.fromisoformat(report.occurred_at))
                )
            reports.append(ReportRecord(report, report.suggestion_id, tuple(deviations)))
        links = {r.report_id: r.suggestion_id for r in history.reports}
        facts = tuple(
            FactRecord(
                fact,
                links.get(fact.fact_id),
                tuple(c for c in history.corrections if c.fact_id == fact.fact_id),
            )
            for fact in history.facts
        )
        # Before publication only facts known then are exposed: dependent-plan states are hidden
        # and a cash-only baseline names no security (older stores recorded a candidate code).
        # Issue #721 R14: a skipped plan reads SKIPPED on the records page; the
        # skip reserved no cash and wrote no ledger posting, but it is
        # auditable alongside the other plan states.
        skipped = self._skipped_plans()
        exposed_plans = tuple(
            replace(p, status="SKIPPED") if p.plan_id in skipped else p
            for p in history.plans
        )
        exposed = replace(
            history,
            plans=exposed_plans if visible else (),
            baseline_security_code=(
                history.baseline_security_code if history.baseline_quantity else CASH_ONLY
            ),
        )
        # Keep earlier published packages readable, each still sized against the
        # baseline and marked superseded by the current release.
        prior = tuple(
            self._publish(item, history)
            for item in self._decisions.history(self._scope)
            if item is not decisions and self._visible(item, now)
        )
        return RecordsView(
            scope=scope,
            package=package if visible else None,
            prior_packages=prior,
            publication=self._publication(decisions, now),
            account=self._account(projection, history, decisions, funding, now),
            history=exposed,
            reports=tuple(reports),
            facts=facts,
            events=history.events,
            plans=exposed.plans,
            orders=history.orders,
        )

    def funding_rules(self, scope: AccountScope) -> FundingRulesView:
        self._require(scope)
        self._maintain()
        try:
            _, history, projection, _, funding, _ = self._state()
        except FactError as exc:
            # Issue #721 R14 (r2): a host without an authoritative calendar
            # serves a blocked placeholder (INPUT_UNAVAILABLE); the funding
            # and rules read must still answer from the account facts - the
            # money never depends on the daily advice calendar.  Re-raise
            # anything that is not the pending-blocked placeholder.
            if exc.reason_code != "INPUT_UNAVAILABLE" or not self._pending_blocked():
                raise
            snapshot = self._facts.snapshot(self._context)
            history, projection = snapshot.history, snapshot.projection
            funding = self._funding(history, projection.revision)
        return FundingRulesView(
            scope=scope,
            composition_ref=self._policy.composition_ref,
            execution_profile=self._policy.execution_profile,
            funding=funding,
            risk=self._policy.risk,
            adoption=self._policy.adoption,
            action_blockers=self._policy.missing_for_action(scope.mode),
            account_revision=projection.revision,
        )

    # ---- fixed-bucket funding (CP01..CP04) --------------------------------------

    def settle_funding_cycle(
        self,
        scope: AccountScope,
        *,
        settlement_id: str,
        requests: tuple[SettlementRequest, ...],
        close_bucket_ids: tuple[str, ...] | None,
    ) -> FundingDecision:
        """Settle closed buckets once their facts prove completeness.

        The application owns the transaction: it verifies the admitted order
        facts satisfy settlement evidence, then commits the funding partition
        in the same store the desktop reads.  A duplicate settlement replays
        the recorded result without moving cash again.
        """
        self._require(scope)
        return self._facts.settle_funding(
            self._context,
            settlement_id=settlement_id,
            requests=requests,
            close_bucket_ids=close_bucket_ids,
        )

    def prepare_funding_cycle(
        self, scope: AccountScope, *, bucket_id: str, cycle_id: str
    ) -> FundingDecision:
        """Lock the next cycle budget for a settled or empty bucket.

        A cold account whose baseline already equals the configured partition
        binds that partition on this first command instead of forcing a page
        read first, so a headless reservation works without warm-up.
        """
        self._require(scope)
        try:
            return self._facts.prepare_funding_cycle(
                self._context, bucket_id=bucket_id, cycle_id=cycle_id
            )
        except FactError as exc:
            if exc.reason_code != "FUNDING_NOT_BOUND":
                raise
        history = self._facts.history(self._context)
        if history.baseline_quantity:
            raise FactError(
                "FUNDING_NOT_BOUND", "baseline holdings keep funding unbound"
            )
        state = initial_funding_state(self._policy.funding, history.baseline_cash)
        self._facts.bind_funding_state(self._context, state)
        return self._facts.prepare_funding_cycle(
            self._context, bucket_id=bucket_id, cycle_id=cycle_id
        )

    def funding_settlement_evidence(
        self, scope: AccountScope, security_codes: set[str] | None = None
    ) -> CycleSettlementEvidence:
        """Readiness derived from the same admitted facts the user sees."""
        self._require(scope)
        return self._facts.funding_settlement_evidence(
            self._context, security_codes=security_codes
        )

    def bind_funding(
        self,
        scope: AccountScope,
        state: FundingState,
        bucket_securities: dict[str, str] | None = None,
    ) -> FundingState:
        """Persist the account's initial fixed-bucket partition once.

        ``bucket_securities`` maps each settled bucket to the single security
        whose fills it funds, letting settlement attribute orders to a cycle.
        """
        self._require(scope)
        return self._facts.bind_funding_state(
            self._context, state, bucket_securities=bucket_securities
        )

    def funding_state(self, scope: AccountScope) -> tuple[FundingState, int] | None:
        """Current persisted funding partition plus its bound account revision."""
        self._require(scope)
        return self._facts.funding_state(self._context)

    # ---- commands -------------------------------------------------------------------
    def submit_fill(self, scope: AccountScope, command: SubmitFill) -> FactOutcome:
        """Register a user-confirmed buy fill; deviations are explained, never rejected."""

        self._require(scope)
        if not isinstance(command.occurred_at, datetime) or command.occurred_at.tzinfo is None:
            raise FactError("INVALID_REPORT_SHAPE", "occurred_at must be aware")
        if command.occurred_at > self._now():
            raise FactError("INVALID_REPORT_SHAPE", "occurred_at cannot be in the future")
        decisions = self._current_decisions()
        history = self._facts.history(self._context)
        package = self._publish(decisions, history)
        plans = package.plans if self._visible(decisions, self._now()) else ()
        plan = next((p for p in plans if p.plan_id == command.plan_id), None)
        if plan is None:
            raise FactError("PLAN_NOT_FOUND", "fill must reference a published plan in this slice")
        if plan.delta_qty <= 0:
            raise FactError("PLAN_NOT_EXECUTABLE", "the published plan has no executable quantity")
        report = ExecutionReport(
            report_id=command.report_id,
            security_code=plan.security_code,
            side=plan.side,
            quantity=command.quantity,
            price=command.price,
            confirmed_fee=command.confirmed_fee,
            occurred_at=command.occurred_at,
            source_id=FACT_SOURCE_ID,
            source_sequence=command.report_id,
            intended_quantity=plan.delta_qty,
            intended_price_cap=plan.price_cap,
            suggestion_id=plan.plan_id,
        )
        self._auto_reserve(plan.security_code)
        result = self._facts.submit_report(self._context, report)
        deviations = tuple(
            dict.fromkeys((*result.deviations, *self._window_deviation(plan, command.occurred_at)))
        )
        self._auto_settle(plan.security_code)
        return self._outcome(
            "fill",
            result.receipt_id,
            result.status,
            deviations,
            result.conflicts,
            result.affected_plans,
            result.next_action,
            None,
            None,
            command.report_id if result.status == "POSTED" else None,
        )

    @staticmethod
    def _order_security(command: SubmitOrderEvent, existing) -> str:
        """The order record owns the security code once the order exists.

        A form returning from the records page may submit a blank code; the
        stored order still knows its security.  A non-blank form code that
        disagrees with the order is a field error, never a silent override.
        """
        form_code = command.security_code or ""
        if existing is None:
            return form_code
        order_code = existing.security_code or ""
        if form_code and order_code and form_code != order_code:
            raise FactError(
                "INVALID_REPORT_SHAPE",
                f"security_code {form_code} does not match order "
                f"{command.order_id} security {order_code}",
            )
        return order_code or form_code

    def submit_order_event(
        self, scope: AccountScope, command: SubmitOrderEvent
    ) -> FactOutcome:
        """Register one fill, order observation or order fee against an actual order.

        The first fact seen for an order establishes it; an ``in_progress``
        observation may also carry the order's accepted quantity and bounds.
        """

        self._require(scope)
        if not isinstance(command.occurred_at, datetime) or command.occurred_at.tzinfo is None:
            raise FactError("INVALID_REPORT_SHAPE", "occurred_at must be aware")
        if command.occurred_at > self._now():
            raise FactError("INVALID_REPORT_SHAPE", "occurred_at cannot be in the future")
        if command.kind not in ("fill", "observation", "fee"):
            raise FactError("INVALID_REPORT_SHAPE", f"unknown order event kind {command.kind}")

        history = self._facts.history(self._context)
        existing = next((o for o in history.orders if o.order_id == command.order_id), None)
        order_security = self._order_security(command, existing)

        if command.kind == "observation":
            observation = OrderObservation(
                observation_id=command.report_id,
                order_id=command.order_id,
                status=command.order_status or "in_progress",
                observed_at=command.occurred_at,
                source_id=FACT_SOURCE_ID,
                source_sequence=command.report_id,
                final_cumulative_fill_qty=command.final_cumulative_fill_qty,
                cancelled_qty=command.cancelled_qty,
                order_revision=command.order_revision,
            )
            accepted = command.accepted_total_qty or (
                existing.accepted_total_qty if existing is not None else None
            )
            if accepted is None:
                raise FactError(
                    "INVALID_REPORT_SHAPE",
                    "first observation must declare accepted_total_qty",
                )
            self._auto_reserve(order_security)
            result = self._facts.submit_order_observation(
                self._context,
                observation,
                accepted_total_qty=accepted,
                side=existing.side if existing is not None else command.side,
                security_code=order_security,
                price_bound=(
                    command.price_bound
                    if command.price_bound is not None
                    else (
                        existing.price_bound if existing is not None else Decimal("0.01")
                    )
                ),
                estimated_fee_upper=(
                    command.fee_upper_bound
                    if command.fee_upper_bound is not None
                    else Decimal("0.00")
                ),
            )
            self._auto_settle(order_security)
            return self._outcome(
                "observation",
                result.receipt_id,
                result.status,
                (),
                result.conflicts,
                (),
                result.next_action,
                None,
                None,
                command.report_id if result.status == "POSTED" else None,
            )

        if command.kind == "fee":
            if existing is None:
                raise FactError("UNKNOWN_ORDER_EXPOSURE", "fee references an unknown order")
            fee = FeeAssessment(
                fee_id=command.report_id,
                order_id=command.order_id,
                amount=command.confirmed_fee if command.confirmed_fee is not None else Decimal("0"),
                source_id=FACT_SOURCE_ID,
                source_sequence=command.report_id,
                occurred_at=command.occurred_at,
            )
            self._auto_reserve(order_security)
            result = self._facts.submit_fee_assessment(self._context, fee)
            self._auto_settle(order_security)
            return self._outcome(
                "fee",
                result.receipt_id,
                result.status,
                (),
                result.conflicts,
                (),
                result.next_action,
                None,
                None,
                command.report_id if result.status == "POSTED" else None,
            )

        # kind == "fill": a fill is an ExecutionReport linked to the order.
        if command.quantity is None or command.price is None:
            raise FactError("INVALID_REPORT_SHAPE", "fill needs quantity and price")
        if existing is None:
            # First fact for this order: register it in progress with the declared
            # accepted quantity, then post the fill against it.
            accepted = command.accepted_total_qty
            if accepted is None or command.price_bound is None:
                raise FactError(
                    "INVALID_REPORT_SHAPE",
                    "first fill must declare accepted_total_qty and price_bound",
                )
            opening = OrderObservation(
                observation_id=f"{command.report_id}:open",
                order_id=command.order_id,
                status="in_progress",
                observed_at=command.occurred_at,
                source_id=FACT_SOURCE_ID,
                source_sequence=f"{command.report_id}:open",
                order_revision=command.order_revision,
            )
            self._auto_reserve(order_security)
            self._facts.submit_order_observation(
                self._context,
                opening,
                accepted_total_qty=accepted,
                side=command.side,
                security_code=order_security,
                price_bound=command.price_bound,
                estimated_fee_upper=command.fee_upper_bound or Decimal("0.00"),
            )
        report = ExecutionReport(
            report_id=command.report_id,
            security_code=order_security,
            side=command.side if existing is None else existing.side,
            quantity=command.quantity,
            price=command.price,
            confirmed_fee=command.confirmed_fee or Decimal("0.00"),
            occurred_at=command.occurred_at,
            source_id=FACT_SOURCE_ID,
            source_sequence=command.report_id,
            intended_quantity=(
                existing.accepted_total_qty
                if existing is not None
                else (command.accepted_total_qty or command.quantity)
            ),
            intended_price_cap=(
                command.price_bound
                if command.price_bound is not None
                else command.price
            ),
            order_id=command.order_id,
        )
        self._auto_reserve(order_security)
        result = self._facts.submit_report(self._context, report)
        return self._outcome(
            "fill",
            result.receipt_id,
            result.status,
            result.deviations,
            result.conflicts,
            result.affected_plans,
            result.next_action,
            None,
            None,
            command.report_id if result.status == "POSTED" else None,
        )

    def correct_fill(self, scope: AccountScope, command: CorrectFill) -> FactOutcome:
        self._require(scope)
        result = self._facts.correct_fact(
            self._context,
            Correction(
                correction_id=command.correction_id,
                fact_id=command.fact_id,
                expected_fact_revision=command.expected_fact_revision,
                quantity=command.quantity,
                price=command.price,
                reason=command.reason,
            ),
        )
        return self._outcome(
            "correction",
            result.receipt_id,
            result.status,
            (),
            result.conflicts,
            result.affected_plans,
            result.next_action,
            result.cash_delta.quantize(CNY_QUANTUM),
            result.quantity_delta,
            command.fact_id,
        )

    def _auto_reserve(self, security_code: str | None) -> None:
        """Bind the partition, reserve a free bucket and pin the security.

        Called before the user's fill is posted: a live entry plan locks its
        budget into one empty bucket and attributes that bucket to the
        security, so the fill lands inside an already-reserved cycle.  Every
        step is retried-safe and returns its recorded result on a repeat.
        """
        if security_code is None:
            return
        try:
            snapshot = self._facts.snapshot(self._context)
            decisions = self._current_decisions()
            package = self._publish(decisions, snapshot.history)
            entry = next(
                (
                    plan
                    for plan in package.plans
                    if plan.security_code == security_code and plan.delta_qty > 0
                ),
                None,
            )
            if entry is None:
                return
            state = self._facts.funding_state(self._context)
            if state is None:
                if snapshot.history.baseline_quantity:
                    return
                self._facts.bind_funding_state(
                    self._context,
                    initial_funding_state(
                        self._policy.funding, snapshot.history.baseline_cash
                    ),
                )
                state = self._facts.funding_state(self._context)
                if state is None:
                    return
            funding, _ = state
            if any(
                item.phase in (BucketPhase.RESERVED, BucketPhase.HELD)
                for item in funding.buckets
            ):
                return
            empty = next(
                (item for item in funding.buckets if item.phase is BucketPhase.EMPTY),
                None,
            )
            if empty is None:
                return
            cycle_id = f"{entry.opportunity_id}:{entry.plan_id}"
            self._facts.prepare_funding_cycle(
                self._context, bucket_id=empty.bucket_id, cycle_id=cycle_id
            )
            self._facts.bind_funding_state(
                self._context,
                self._facts.funding_state(self._context)[0],
                bucket_securities={empty.bucket_id: security_code},
            )
        except FactError:
            # A failed reserve never blocks the user's fill; the page shows
            # the unbound reason on the next read instead.
            return

    def _upgrade_pre_binding_fills(self, snapshot) -> None:
        """Attribute ledger fills that predate the funding partition.

        A ledger bound *after* its first fills (the previous version opened
        the account, posted a fill, then bound the flat baseline partition)
        leaves those fills with no owning bucket.  The floor inside each
        open cycle only reaches back to the binding anchor, so the cash the
        fill moved is unaccountable and the page reports a mismatch.  When
        the realized cash is uniquely attributable to open-position
        securities whose count fits the empty buckets, each security claims
        one empty bucket: the cycle floor is set to the partition's binding
        anchor so the pre-binding cash becomes accountable inside it.  An
        account that does not uniquely attribute keeps its facts unchanged
        and is left for the funding page's manual-review view.
        """
        try:
            state = self._facts.funding_state(self._context)
            if state is None:
                return
            funding, _ = state
            binding = self._facts.funding_binding(self._context) or {}
            securities = dict(binding.get("bucket_securities") or {})
            anchor = int(binding.get("initial_bound_account_revision", 0))
            empties = [
                item for item in funding.buckets if item.phase is BucketPhase.EMPTY
            ]
            if not empties:
                return
            # Candidate securities are the fills the partition cannot yet
            # explain: still-held positions not already attributed to a live
            # bucket.  Realized cash must match the still-missing account
            # delta or another flow owns it and we do not guess.
            open_secs = {
                p.security_code
                for p in snapshot.projection.positions
                if p.quantity and p.security_code not in securities.values()
            }
            # Restrict to securities the ledger actually admitted fills for:
            # a position that arrived only through a baseline or another
            # non-fill source has no realized cash to attribute, and letting
            # it claim a bucket would mask a different ownership question.
            filled = self._facts.funding_realized_cash_by_security(
                self._context, security_codes=None, since_sequence=anchor
            ) or {}
            open_secs &= set(filled)
            if not open_secs:
                return
            account_cash = snapshot.projection.cash
            accountable = self._facts.funding_accountable_cash(self._context)
            if accountable is None or accountable == account_cash:
                return
            missing = account_cash - accountable
            realized = self._facts.funding_realized_cash_by_security(
                self._context, security_codes=open_secs, since_sequence=anchor
            )
            if not realized or len(realized) > len(empties):
                return
            if sum(realized.values(), Decimal("0.00")) != missing:
                return
            for (bucket, (sec, _cash)) in zip(empties, sorted(realized.items())):
                cycle_id = f"paper:{sec}:{bucket.bucket_id}"
                # One atomic upgrade: open the cycle on the empty bucket,
                # attribute it to the security, and anchor the floor at the
                # binding revision so the already-moved fill cash is the
                # pending amount the open cycle explains.  This is what
                # restores the reconcile the normal write gate requires.
                self._facts.attribute_prebinding_cycle(
                    self._context,
                    bucket_id=bucket.bucket_id,
                    cycle_id=cycle_id,
                    security_code=sec,
                )
        except FactError:
            return

    def _auto_settle(self, security_code: str | None) -> None:
        """Settle an open bucket cycle once the security is flat.

        Called after the user's fact is posted: when the order closes the
        position, the owning bucket's cycle settles inside the same store,
        idempotent by its settlement id.

        A ``None`` code sweeps every open bucket whose attributed security is
        no longer held, so a read-path ``_maintain`` repairs a bucket that an
        earlier blank-coded submission left stuck.
        """
        try:
            state = self._facts.funding_state(self._context)
            if state is None:
                return
            funding, _ = state
            snapshot = self._facts.snapshot(self._context)
            if security_code is not None and any(
                item.security_code == security_code and item.quantity
                for item in snapshot.projection.positions
            ):
                return
            # bucket_id -> its attributed security is still held (open position).
            held = self._facts.funding_held_by_bucket(self._context) or {}
            for item in funding.buckets:
                if item.phase not in (BucketPhase.RESERVED, BucketPhase.HELD):
                    continue
                # A specific security settles only its owning bucket once it is
                # flat; a None sweep (read-path) settles every open bucket whose
                # attributed security is no longer held.
                if security_code is None:
                    if held.get(item.bucket_id, True):
                        continue
                    codes = None
                else:
                    if held.get(item.bucket_id, True):
                        continue
                    codes = {security_code}
                evidence = self._facts.funding_settlement_evidence(
                    self._context, security_codes=codes,
                )
                if not evidence.settlement_ready:
                    continue
                try:
                    self._facts.settle_funding(
                        self._context,
                        settlement_id=f"auto:{item.bucket_id}:{item.cycle_id}",
                        requests=(
                            SettlementRequest(
                                f"auto-{item.bucket_id}-{item.cycle_id}",
                                item.bucket_id,
                                item.cycle_id,
                                evidence,
                            ),
                        ),
                        close_bucket_ids=(item.bucket_id,),
                    )
                except FactError:
                    continue
        except FactError:
            return

    def _outcome(
        self,
        kind: str,
        receipt_id: str,
        status: str,
        deviations: tuple[str, ...],
        conflicts: tuple[str, ...],
        affected: tuple[str, ...],
        next_action: str,
        cash_delta: Decimal | None,
        quantity_delta: int | None,
        fact_id: str | None,
    ) -> FactOutcome:
        # Account and fact revision come from the same committed snapshot.
        decisions, history, projection, _, funding, now = self._state()
        fact_revision = next(
            (f.fact_revision for f in history.facts if fact_id and f.fact_id == fact_id), None
        )
        return FactOutcome(
            kind=kind,
            receipt_id=receipt_id,
            status=status,
            account=self._account(projection, history, decisions, funding, now),
            deviations=deviations,
            conflicts=conflicts,
            affected_plans=affected,
            next_action=next_action,
            cash_delta=cash_delta,
            quantity_delta=quantity_delta,
            current_fact_revision=fact_revision,
        )

    # ---- paper-account fact port ---------------------------------------------------
    # A simulated order flow posts its own fills and order observations into
    # the same ledger through this narrow port instead of reaching private
    # members.  The port stays inside the application layer: the caller is
    # still bound to this application's account scope.
    def paper_submit_report(self, report: ExecutionReport):
        # A self-directed simulated buy must land inside an attributable
        # bucket cycle the same way a plan-linked fill does, or the funding
        # page can no longer reconcile the partition against account cash
        # (R2 ledger-mismatch).  Reserve an empty bucket for the security
        # before posting so the cycle floor precedes the cash event; after a
        # sell posts, settle the owning bucket once the position is flat.
        # With no usable bucket a new security is refused *before* its fill
        # posts - the ledger must never admit cash it cannot attribute.
        side = getattr(report, "side", None)
        if side == "buy":
            security = getattr(report, "security_code", None)
            if security is not None and not self._bucket_capacity_for(security):
                raise FactError(
                    "FUNDING_NO_FREE_BUCKET",
                    f"no free bucket can take {security}; sell a held security "
                    "or free a cycle before opening a new one",
                )
            self._auto_reserve_self_directed(security)
        result = self._facts.submit_report(self._context, report)
        if side == "sell":
            self._auto_settle(getattr(report, "security_code", None))
        return result

    def paper_bucket_capacity_for(self, security_code: str) -> bool:
        """Order-side probe: can a bucket take this security's next cycle.

        The paper order book asks this before accepting a new-security buy so
        the fixed-bucket account never fills cash it cannot attribute.
        """
        return self._bucket_capacity_for(security_code)

    def _bucket_capacity_for(self, security_code: str) -> bool:
        """Whether some bucket can take this security's next cycle.

        A bucket is usable when it already serves this security, or is EMPTY,
        or closed its last cycle while still attributed to this security (a
        repeat trade).  With none usable the fixed-bucket policy refuses the
        new security rather than admit a fill it cannot reconcile.
        """
        try:
            state = self._facts.funding_state(self._context)
        except FactError:
            return False
        if state is None:
            return True  # the reserve path binds a fresh partition
        funding, _ = state
        binding = self._facts.funding_binding(self._context) or {}
        securities = dict(binding.get("bucket_securities") or {})
        for item in funding.buckets:
            if item.phase in (BucketPhase.RESERVED, BucketPhase.HELD):
                if securities.get(item.bucket_id) == security_code:
                    return True
                continue
            if item.phase is BucketPhase.EMPTY:
                return True
            if (
                item.phase is BucketPhase.CLOSED
                and securities.get(item.bucket_id) == security_code
            ):
                return True
        return False

    def _auto_reserve_self_directed(self, security_code: str | None) -> None:
        """Attribute a self-directed buy to one bucket cycle, by security.

        Unlike ``_auto_reserve`` this does not need a published entry plan:
        the user decided to buy, so the fill belongs to a bucket either way.
        An already-open bucket that is serving *this* security is enough;
        another security's open bucket never blocks a new one.  The buy first
        claims a free bucket, then falls back to a closed bucket whose cycle
        ended while it still held this security (a repeat trade after a
        settle).  With no usable bucket the buy stays unattributed and the
        funding page reports the gap instead of pretending the partition
        reconciles.
        """
        if security_code is None:
            return
        try:
            state = self._facts.funding_state(self._context)
            if state is None:
                snapshot = self._facts.snapshot(self._context)
                if snapshot.history.baseline_quantity:
                    return
                self._facts.bind_funding_state(
                    self._context,
                    initial_funding_state(
                        self._policy.funding, snapshot.history.baseline_cash
                    ),
                )
                state = self._facts.funding_state(self._context)
                if state is None:
                    return
            funding, _ = state
            # Already attributed: one open bucket is serving this security's cycle.
            binding = self._facts.funding_binding(self._context) or {}
            securities = dict(binding.get("bucket_securities") or {})
            served = {
                item.bucket_id
                for item in funding.buckets
                if item.phase in (BucketPhase.RESERVED, BucketPhase.HELD)
            }
            if any(securities.get(bid) == security_code for bid in served):
                return
            candidate = next(
                (item for item in funding.buckets if item.phase is BucketPhase.EMPTY),
                None,
            )
            if candidate is None:
                # Reuse a closed bucket that last served this same security:
                # its cycle ended while still attributed, so a repeat trade
                # reopens it rather than inventing a second owner.
                candidate = next(
                    (
                        item
                        for item in funding.buckets
                        if item.phase is BucketPhase.CLOSED
                        and securities.get(item.bucket_id) == security_code
                    ),
                    None,
                )
            if candidate is None:
                return
            cycle_id = f"paper:{security_code}:{candidate.bucket_id}"
            self._facts.prepare_funding_cycle(
                self._context, bucket_id=candidate.bucket_id, cycle_id=cycle_id
            )
            self._facts.bind_funding_state(
                self._context,
                self._facts.funding_state(self._context)[0],
                bucket_securities={candidate.bucket_id: security_code},
            )
        except FactError:
            return


    def paper_submit_order_observation(
        self,
        observation,
        *,
        accepted_total_qty: int,
        side: str,
        security_code: str,
        price_bound,
        estimated_fee_upper,
    ):
        return self._facts.submit_order_observation(
            self._context,
            observation,
            accepted_total_qty=accepted_total_qty,
            side=side,
            security_code=security_code,
            price_bound=price_bound,
            estimated_fee_upper=estimated_fee_upper,
        )

    def facts_binding(self):
        """The configuration binding recorded on this account store, if any."""
        return self._facts.configuration_binding(self._context)

    def paper_projection(self):
        return self._facts.projection(self._context)

    def paper_history(self):
        return self._facts.history(self._context)

    def _confirm_mark(self, security_code: str, marks_map: dict):
        """Confirm-time effective mark: the bound live-mark supply, then
        the bound quote service live last price, else the package mark.

        Issue #723 E8 r2-3: sizing at confirm must not reuse the stale
        package-time mark when a live mark exists - the reduce keep target
        is fixed by the mark that will actually price the fill.
        """
        if self._live_marks is not None:
            try:
                live = self._live_marks([security_code]) or {}
            except Exception:  # noqa: BLE001 - fall back to quote/package
                live = {}
            mark = _optional_decimal(live.get(security_code))
            if mark is not None and mark > 0:
                return mark
        quote_svc = getattr(self, "_quote_service", None) or getattr(self, "_quotes", None)
        if quote_svc is not None:
            try:
                quote = quote_svc.quote(security_code)
            except Exception:  # noqa: BLE001
                quote = None
            mark = _optional_decimal(getattr(quote, "last_price", None))
            if mark is not None and mark > 0:
                return mark
        return marks_map.get(security_code)

    def paper_current_plan(self, plan_id: str, live_mark=None):
        """The account's currently effective published plan for ``plan_id``.

        Read at order-acceptance time so a confirm-the-suggestion order is
        bound to the live plan's server-side quantity, price cap and window,
        not to values the client echoed back.  Returns ``None`` when the
        package is not yet visible, the plan is absent, or its execution
        window has already closed - the order path then refuses rather than
        fill against a stale suggestion.
        """
        decisions = self._current_decisions()
        now = self._now()
        if not self._visible(decisions, now):
            return None
        # A retracted package keeps its filled facts but its unconfirmed plans
        # can no longer confirm: the order path then refuses with PLAN_NOT_FOUND.
        is_retracted = getattr(self._decisions, "is_retracted", None)
        if callable(is_retracted) and is_retracted(decisions.market_date):
            return None
        snapshot = self._facts.snapshot(self._context)
        package = self._publish(decisions, snapshot.history)
        plan = next((p for p in package.plans if p.plan_id == plan_id), None)
        if plan is None:
            # E8 r2-6: a carried exit intent resolves under the current
            # package as a sell for the still-held quantity, so its own
            # confirm id keeps working after the successor package lands.
            # The original id plan-OPP-EXIT-<code>-<pkg-date> maps to the
            # carry plan id for that same security.
            stem = (
                plan_id.removeprefix("plan-OPP-EXIT-")
                if plan_id.startswith("plan-OPP-EXIT-") else None
            )
            if stem is None:
                return None
            security = stem.rsplit("-", 3)[0] if "-" in stem else stem
            carried_plan_id = _carried_plan_id(decisions, security)
            plan = next((p for p in package.plans if p.plan_id == carried_plan_id), None)
            if plan is None:
                return None
        # E8 r2-1: an unverified exit never confirms a sell order.
        if plan.side == "sell" and self._exit_types_unverified(decisions):
            return None
        if now < plan.eligible_at or now > plan.expires_at:
            return None
        current = snapshot.projection.quantity(plan.security_code)
        if plan.side == "sell":
            # E8 r2-2/3: confirm re-sizes against the live account, not the
            # package-time quantity.  Live marks + the bucket's current
            # locked_cycle_budget fix the keep target at raw precision; the
            # still-sellable quantity nets out T+1 holds and the in-flight
            # sell orders already covering it, so a second confirm can only
            # sell the uncovered difference - never double-sell into a flat.
            marks_map = dict(_marks(decisions))
            # The caller (paper order path) may pass the live quote mark it
            # will fill against; that confirm-time mark fixes the keep
            # target before any package/mark fallback.
            injected = _optional_decimal(live_mark)
            confirm_mark = (
                injected if injected is not None and injected > 0
                else self._confirm_mark(plan.security_code, marks_map)
            )
            if confirm_mark is not None:
                marks_map[plan.security_code] = confirm_mark
            bucket_by_sec, locked_by_sec = _active_bucket_and_budget(self, snapshot.history)
            bucket_id = bucket_by_sec.get(plan.security_code)
            # Recover the reduce fraction from the decision that produced
            # this plan; full exits keep their flat target.
            decision = next(
                (d for d in decisions.decisions if d.opportunity_id == plan.opportunity_id),
                None,
            )
            exit_kind = getattr(decision, "exit_kind", None) or "full"
            fraction = getattr(decision, "target_position_fraction", None)
            sellable = _sellable_for(
                plan.security_code, snapshot.projection, snapshot.history,
                _sell_reserved_by_security(snapshot.history), now.date(),
            )
            sizing = size_exit(
                security_code=plan.security_code,
                exit_kind=exit_kind,
                target_position_fraction=fraction,
                held_qty=current,
                sellable_qty=sellable,
                mark_price=marks_map.get(plan.security_code),
                locked_cycle_budget=locked_by_sec.get(plan.security_code) if bucket_id else None,
                bucket_id=bucket_id,
                lot_size=self._policy.risk.lot_size,
            )
            if sizing.sell_qty <= 0:
                return None
            return dataclasses.replace(plan, delta_qty=sizing.sell_qty)
        if plan.delta_qty <= 0:
            return None
        # Re-evaluate the still-open delta against the *current* position:
        # after a fill the account already holds the security, so the
        # remaining executable quantity is target - current, which may now be
        # zero.  The order path then refuses instead of reopening a spent
        # suggestion above the live trial headroom.
        remaining = plan.target_qty - current
        if remaining <= 0:
            return None
        return dataclasses.replace(plan, delta_qty=remaining)

    def preopen_fact_for(self, security_code: str, fact: str):
        """Resolve one pre-open fact for a confirm-the-suggestion check (R12).

        Returns True only when a named supply attests the fact for this
        security and trading day; returns None/False when no named supply is
        bound, which makes any confirm that depends on it fail closed with
        the typed unavailable reason.  The desktop never infers a fact from a
        zero-volume bar, a last price or the prior session state.
        """
        provider = getattr(self, "_preopen_facts", None)
        if callable(provider):
            return provider(security_code, fact)
        return None

    def bind_preopen_facts(self, provider) -> None:
        """Bind the named pre-open fact supply used by the R12 confirm gates.

        The provider maps (security_code, fact) -> True when a named supply
        attests the fact; any other result means the fact is unavailable and
        a dependent confirm is refused.  Absent a bound supply every declared
        fact reads unavailable."""
        self._preopen_facts = provider

    def rebuild_matches(self, scope: AccountScope) -> bool:
        """Deterministic rebuild from baseline + events equals the stored projection."""

        self._require(scope)
        return self._facts.rebuild(self._context) == self._facts.projection(self._context)
