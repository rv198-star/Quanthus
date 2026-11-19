"""P12 desktop application use cases: one facade for HTTP, CLI and future daily jobs.

Rules stay in capabilities: accounting values the ledger, risk solves quantities, funding
describes the authoritative partition and decision access validates PIT.  This module only
orchestrates them, checks the trusted scope and adoption for new risk actions, and builds
read models.  Fact registration is never blocked by an expired or unadopted suggestion.
"""

from __future__ import annotations

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
    FUNDING_LEDGER_MISMATCH,
    FundingView,
    describe_funding,
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
        """Create the trusted baseline; a held position joins with unknown cost."""

        decisions = self._current_decisions()
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
        binding: dict[str, object] = {
            "mode": self._scope.mode,
            "composition": self._policy.composition_ref.to_dict(),
            "decision_source": decisions.source_ref.to_dict(),
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
            if accountable_total != account_cash:
                return describe_unbound_funding(
                    self._policy.funding, FUNDING_LEDGER_MISMATCH
                )
            return describe_funding(
                self._policy.funding,
                persisted,
                state_account_revision=bound_revision,
                current_account_revision=revision,
            )
        try:
            state = initial_funding_state(self._policy.funding, history.baseline_cash)
        except FactError as exc:
            if exc.reason_code != BUCKET_ASSIGNMENT_CONFLICT:
                raise
            return describe_unbound_funding(self._policy.funding, BUCKET_ASSIGNMENT_CONFLICT)
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
                "NEEDS_RECONCILIATION" if (pending or order_pending) else "CLEAN"
            ),
            pending_reconciliations=pending + sum(
                1 for o in history.orders if o.needs_reconciliation
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
        if plan.plan_id not in statuses:
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
    def workbench(self, scope: AccountScope) -> WorkbenchView:
        self._require(scope)
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
        return RecordsView(
            scope=scope,
            package=package if visible else None,
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
        result = self._facts.submit_report(self._context, report)
        deviations = tuple(
            dict.fromkeys((*result.deviations, *self._window_deviation(plan, command.occurred_at)))
        )
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
            result = self._facts.submit_order_observation(
                self._context,
                observation,
                accepted_total_qty=accepted,
                side=existing.side if existing is not None else command.side,
                security_code=(
                    existing.security_code
                    if existing is not None
                    else command.security_code
                ),
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
            result = self._facts.submit_fee_assessment(self._context, fee)
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
            self._facts.submit_order_observation(
                self._context,
                opening,
                accepted_total_qty=accepted,
                side=command.side,
                security_code=command.security_code,
                price_bound=command.price_bound,
                estimated_fee_upper=command.fee_upper_bound or Decimal("0.00"),
            )
        report = ExecutionReport(
            report_id=command.report_id,
            security_code=command.security_code
            or (existing.security_code if existing is not None else ""),
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

    def rebuild_matches(self, scope: AccountScope) -> bool:
        """Deterministic rebuild from baseline + events equals the stored projection."""

        self._require(scope)
        return self._facts.rebuild(self._context) == self._facts.projection(self._context)
