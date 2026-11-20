"""P12 desktop application use cases: one facade for HTTP, CLI and future daily jobs.

Rules stay in capabilities: accounting values the ledger, risk solves quantities, funding
describes the authoritative partition and decision access validates PIT.  This module only
orchestrates them, checks the trusted scope and adoption for new risk actions, and builds
read models.  Fact registration is never blocked by an expired or unadopted suggestion.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

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
from quanthus.product_daily.application.history import ConfigurationBinding, FactHistory
from quanthus.product_daily.application.models import Baseline
from quanthus.product_daily.application.service import SyntheticFactService
from quanthus.product_daily.funding import (
    CycleSettlementEvidence,
    FundingDecision,
    FundingState,
    SettlementRequest,
    BucketPhase,
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


def _valuation_ref(decisions: MarketDecisionSet) -> str:
    return f"{decisions.source_ref.ref_id}@{decisions.market_date.isoformat()}:close"


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
        decisions = published[0] if published else self._current_decisions()
        first = (
            security_code
            or (decisions.decisions[0].security_code if decisions.decisions else "CASH")
        )
        return self._facts.establish_baseline(
            Baseline(self._context, request_id, cash, quantity, first, 0, plan_ids(decisions))
        )

    def bind_configuration(self) -> ConfigurationBinding:
        """Bind this account store to one composition and decision source, once.

        Published plans are derived from (composition, decisions, baseline).  Reopening the
        same store with a different policy would silently re-size historical plans, so the
        first configuration is recorded and any other one is refused with guidance; a new
        account store may use a new policy.  Stores created before bindings existed are
        adopted only if every plan-linked report matches the plan this configuration
        publishes.
        """

        decisions = self._current_decisions()
        # The binding covers the full content of every package the source has
        # released, not only the source ref: re-opening with the same version
        # whose package content was edited is refused, while a brand-new dated
        # release (a new package, not an edited one) still opens the store.
        source_ref = decisions.source_ref
        published = self._decisions.history(self._scope)
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
            plans = {p.plan_id: p for p in self._publish(decisions, snapshot.history).plans}
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
        return PublicationStatus(
            status="PUBLISHED" if self._visible(decisions, now) else "NOT_YET_PUBLISHED",
            market_date=decisions.market_date,
            published_at=decisions.published_at,
        )

    # ---- package publication ----------------------------------------------------------
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
                    fraction=item.entry.fraction,
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
                            price=item.get("price"),
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
        decisions: MarketDecisionSet,
        funding: FundingView,
        now: datetime,
    ) -> AccountSummary:
        # Closing prices are market facts known at the cutoff; plan links wait for publication.
        market = decisions if now >= decisions.knowledge_cutoff else None
        linked = decisions if self._visible(decisions, now) else None
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
        valuation = value_account(
            projection,
            marks=_marks(market) if market else {},
            valuation_ref=_valuation_ref(market) if market else None,
            cost_principal=principal,
        )
        names = {item.security_code: item.security_name for item in (market or decisions).decisions}
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
            action, status = "SELL", "READY"
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
            next_observation=item.next_observation,
            chart=item.chart,
            cost_price=cost,
            plan=plan,
            applicability=applicability,
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
        decisions, history, projection, package, funding, now = self._state()
        account = self._account(projection, history, decisions, funding, now)
        blockers = self._policy.missing_for_action(scope.mode)
        visible = self._visible(decisions, now)
        items = [
            self._opportunity(item, decisions, package, projection, history, account, blockers, now)
            for item in (decisions.decisions if visible else ())
        ]
        known = {item.security_code for item in decisions.decisions}
        for line in account.positions if visible else ():
            if line.security_code in known:
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
        )

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
        exposed = replace(
            history,
            plans=history.plans if visible else (),
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
        _, history, projection, _, funding, _ = self._state()
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
                f"security_code {form_code} does not match order {command.order_id} security {order_code}",
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

    def paper_projection(self):
        return self._facts.projection(self._context)

    def paper_history(self):
        return self._facts.history(self._context)

    def paper_current_plan(self, plan_id: str):
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
        snapshot = self._facts.snapshot(self._context)
        package = self._publish(decisions, snapshot.history)
        plan = next((p for p in package.plans if p.plan_id == plan_id), None)
        if plan is None or plan.delta_qty <= 0:
            return None
        if now < plan.eligible_at or now > plan.expires_at:
            return None
        return plan

    def rebuild_matches(self, scope: AccountScope) -> bool:
        """Deterministic rebuild from baseline + events equals the stored projection."""

        self._require(scope)
        return self._facts.rebuild(self._context) == self._facts.projection(self._context)
