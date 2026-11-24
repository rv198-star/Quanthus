"""Explicit mapping between P12 application read models and transport DTOs (p12.desktop.v1).

Every outbound payload is validated against the generated contract before it leaves the
adapter; every inbound payload is validated before it becomes an application command.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from quanthus.platform.p12 import errors
from quanthus.platform.p12.contract_generated import REASON_TEXTS, SCHEMA_VERSION

# Issue #723 E8 r5 (compat): the frozen pre-field contract.  A request with
# no schema_version, or one that explicitly pins p12.desktop.v2, reads the
# v2 projection (post-frozen fields stripped, envelope and package version
# rewritten to v2).  Only a client that explicitly selects the newer
# contract reads next_session / confirm_qty.
LEGACY_V2 = "p12.desktop.v2"

def _effective_version(schema_version: str | None) -> str:
    return SCHEMA_VERSION if schema_version == SCHEMA_VERSION else LEGACY_V2

from quanthus.platform.p12.validation import ContractViolation, validate
from quanthus.product_daily.application.desktop import (
    CorrectFill,
    FactOutcome,
    FundingRulesView,
    RecordsView,
    SubmitFill,
    SubmitOrderEvent,
    WorkbenchView,
)
from quanthus.product_daily.application.desktop.views import (
    AccountSummary,
    OpportunityView,
    PublicationStatus,
    PublishedPackage,
    PublishedPlan,
)
from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import (
    RATIO_QUANTUM,
    AccountScope,
    MaybeDecimal,
    VersionRef,
    decimal_text,
    parse_decimal_text,
)

UNCLASSIFIED = errors.UNCLASSIFIED


class OutboundContractError(RuntimeError):
    """The server produced a payload outside the contract; never reported as client error."""


def _out(component: str, payload: dict[str, Any], schema_version: str | None = None) -> dict[str, Any]:
    # A negotiated v2 projection rewrote the SchemaVersion const; validate it
    # against the frozen reader's expectation, never the newer wire const.
    override = LEGACY_V2 if _effective_version(schema_version) == LEGACY_V2 else None
    try:
        return validate(component, payload, const_override=override)
    except ContractViolation as exc:
        raise OutboundContractError(str(exc)) from exc


def _money(value: Decimal) -> str:
    return decimal_text(value)


def _money_or_none(value: Decimal | None) -> str | None:
    return None if value is None else decimal_text(value)


def _ratio(value: Decimal) -> str:
    return decimal_text(value, RATIO_QUANTUM)


def _policy_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text


def _maybe(value: MaybeDecimal | None, *, ratio: bool = False) -> dict[str, Any] | None:
    if value is None:
        return None
    if value.value is None:
        return {"value": None, "reason_code": reason(value.reason_code)}
    return {"value": _ratio(value.value) if ratio else _money(value.value), "reason_code": None}


def _ts(value: datetime) -> str:
    return value.isoformat()


def reason(code: str | None) -> str:
    if code is None or code not in REASON_TEXTS:
        raise OutboundContractError(f"reason code {code!r} is not in the contract")
    return code


def _reasons(codes: tuple[str, ...]) -> list[str]:
    return [reason(code) for code in codes]


def _ref(value: VersionRef) -> dict[str, str]:
    return value.to_dict()


def _scope(value: AccountScope) -> dict[str, str]:
    return {"account_id": value.account_id, "mode": value.mode}


def _account(value: AccountSummary) -> dict[str, Any]:
    return {
        "scope": _scope(value.scope),
        "account_revision": value.account_revision,
        "event_sequence": value.event_sequence,
        "as_of": _ts(value.as_of),
        "valuation_ref": value.valuation_ref,
        "economic_equity": _maybe(value.economic_equity),
        "cash_total": _money(value.cash_total),
        "cash_available_for_new": _maybe(value.cash_available_for_new),
        "cash_occupied": _money(value.cash_occupied),
        "positions_value": _maybe(value.positions_value),
        "reserve": _maybe(value.reserve),
        "top_concentration": _maybe(value.top_concentration, ratio=True),
        "top_concentration_security": value.top_concentration_security,
        "reconciliation_status": value.reconciliation_status,
        "pending_reconciliations": value.pending_reconciliations,
        "ledger_digest": value.ledger_digest,
        "positions": [
            {
                "security_code": line.security_code,
                "security_name": line.security_name,
                "quantity": line.quantity,
                "mark_price": _maybe(line.mark_price),
                "market_value": _maybe(line.market_value),
                "average_cost": _maybe(line.average_cost, ratio=True),
                "weight": _maybe(line.weight, ratio=True),
                "opportunity_id": line.opportunity_id,
            }
            for line in value.positions
        ],
    }


def _plan(value: PublishedPlan, schema_version: str | None = None) -> dict[str, Any]:
    plan = {
        "plan_id": value.plan_id,
        "opportunity_id": value.opportunity_id,
        "security_code": value.security_code,
        "side": value.side,
        "current_qty": value.current_qty,
        "target_qty": value.target_qty,
        "delta_qty": value.delta_qty,
        "price_cap": _money(value.price_cap),
        "cash_bound": _money_or_none(value.cash_bound),
        "estimated_fee": _money_or_none(value.estimated_fee),
        "eligible_at": _ts(value.eligible_at),
        "expires_at": _ts(value.expires_at),
        "window_ref": value.window_ref,
        "invalidations": list(value.invalidations),
        "reason_codes": _reasons(value.reason_codes),
        "primary_constraint": None
        if value.primary_constraint is None
        else reason(value.primary_constraint),
        "risk_check_ref": value.risk_check_ref,
    }
    # Issue #723 E8 r5 (buy): confirm_qty is the confirm-time re-sized
    # figure (optional contract field, only in the v3 projection).  A
    # p12.desktop.v2 reader predates it, so it is stripped for v2.
    confirm = getattr(value, "confirm_qty", None)
    if _effective_version(schema_version) != LEGACY_V2 and confirm is not None:
        plan["confirm_qty"] = confirm
    return plan


def _package(value: PublishedPackage, schema_version: str | None = None) -> dict[str, Any]:
    # Issue #723 E8 r5 (compat): the p12.desktop.v2 projection predates
    # next_session/confirm_qty - those post-frozen fields are stripped and
    # the package.schema_version is rewritten to v2 for an old reader.
    # A client that explicitly selects the newer contract keeps the field.
    v2 = _effective_version(schema_version) == LEGACY_V2
    pkg = {
        "package_id": value.package_id,
        "schema_version": (
            "p12.desktop.v2" if v2 else value.schema_version
        ),
        "mode": value.mode,
        "synthetic": value.synthetic,
        "market_date": value.market_date.isoformat(),
        "knowledge_cutoff": _ts(value.knowledge_cutoff),
    }
    if not v2:
        pkg["next_session"] = (
            value.next_session.isoformat()
            if getattr(value, "next_session", None) is not None
            else None
        )
    pkg.update({
        "published_at": _ts(value.published_at),
        "account_revision": value.account_revision,
        "composition_ref": _ref(value.composition_ref),
        "source_ref": _ref(value.source_ref),
        "plans": [_plan(item) for item in value.plans],
        "digest": value.digest,
        "superseded": False,
    })
    return pkg


def _publication(value: PublicationStatus) -> dict[str, Any]:
    # Optional-by-contract fields (audit R18): retracted/retraction_reason are
    # only emitted when a retraction is actually recorded, so the pre-
    # publication response keeps its earlier shape.
    out = {
        "status": value.status,
        "market_date": value.market_date.isoformat(),
        # Issue #721 R16 (r2): null until the package is actually visible -
        # never the request's clock dressed as a publish time.
        "published_at": (
            _ts(value.published_at) if value.published_at is not None else None
        ),
    }
    # observed_at carries the reader's observation moment (always present).
    observed = getattr(value, "observed_at", None)
    if observed is not None:
        out["observed_at"] = _ts(observed)
    # Issue #721 R15: next-check time, an optional contract field emitted
    # only when an authoritative next session is known.
    next_check = getattr(value, "next_check_at", None)
    if next_check is not None:
        out["next_check_at"] = _ts(next_check)
    # Issue #722 r3-3: overdue flag - emitted only when the publish deadline
    # actually passed with no package and no successful receipt, so a normal
    # pending day keeps the older shape.
    if getattr(value, "overdue", False):
        out["overdue"] = True
    if value.retracted:
        out["retracted"] = True
        out["retraction_reason"] = value.retraction_reason
    return out


def _opportunity(value: OpportunityView, schema_version: str | None = None) -> dict[str, Any]:
    chart = value.chart
    applicability = value.applicability
    return {
        "opportunity_id": value.opportunity_id,
        "security_code": value.security_code,
        "security_name": value.security_name,
        "stage": value.stage,
        "queue_group": value.queue_group,
        "action_code": value.action_code,
        "status_code": value.status_code,
        "exit_kind": value.exit_kind,
        "reason_codes": _reasons(value.reason_codes),
        "primary_constraint": None
        if value.primary_constraint is None
        else reason(value.primary_constraint),
        "changes": list(value.changes),
        "explanations": [
            {
                "code": item.code,
                "use": item.use.value,
                "component_ref": item.component_ref,
                "text": item.text,
            }
            for item in value.explanations
        ],
        "next_observation": value.next_observation,
        "chart": None
        if chart is None
        else {
            "price_basis": chart.price_basis,
            "as_of": _ts(chart.as_of),
            "support": _money_or_none(chart.support),
            "volume_baseline": chart.volume_baseline,
            "volume_baseline_window": chart.volume_baseline_window,
            "bars": [
                {
                    "session": bar.session.isoformat(),
                    "open": _money(bar.open),
                    "high": _money(bar.high),
                    "low": _money(bar.low),
                    "close": _money(bar.close),
                    "volume": bar.volume,
                }
                for bar in chart.bars
            ],
        },
        "cost_price": _maybe(value.cost_price, ratio=True),
        "plan": None if value.plan is None else _plan(value.plan, schema_version),
        "applicability": None
        if applicability is None
        else {
            "plan_id": applicability.plan_id,
            "status": applicability.status,
            "current_qty": applicability.current_qty,
            "delta_qty": applicability.delta_qty,
            "account_revision": applicability.account_revision,
            "reason_codes": _reasons(applicability.reason_codes),
            "next_step": applicability.next_step,
        },
    }


def _run_receipt(value: dict | None) -> dict | None:
    """Project the stored run receipt onto the closed contract shape."""
    if value is None:
        return None
    block = value.get("input_block")
    package = value.get("package")
    return {
        "schema_version": str(value.get("schema_version") or ""),
        "run_id": str(value.get("run_id") or ""),
        "market_date": str(value.get("market_date") or ""),
        "status": str(value.get("status") or ""),
        "started_at": _ts(datetime.fromisoformat(str(value["started_at"])))
        if value.get("started_at")
        else "",
        "elapsed_ms": int(value.get("elapsed_ms") or 0),
        "runner": {
            "ref_id": str((value.get("runner") or {}).get("ref_id") or ""),
            "version": str((value.get("runner") or {}).get("version") or ""),
            "digest": str((value.get("runner") or {}).get("digest") or ""),
        },
        "input_block": None
        if block is None
        else {
            "reason_code": str(block.get("reason_code") or ""),
            "detail": str(block.get("detail") or ""),
            "affected_scope": str(block.get("affected_scope") or ""),
            "missing_members": [str(x) for x in block.get("missing_members") or ()],
            "recovery_condition": str(block.get("recovery_condition") or ""),
        },
        "package": None
        if package is None
        else {
            "package_id": str(package.get("package_id") or ""),
            "file_sha256": str(package.get("file_sha256") or ""),
            "decision_count": int(package.get("decision_count") or 0),
            "entry_plan_count": int(package.get("entry_plan_count") or 0),
            "admission_disposition": str(package.get("admission_disposition") or ""),
            "decision_set_digest": str(package.get("decision_set_digest") or ""),
        },
        "exclusions": [
            {
                "security_code": str(item.get("security_code") or ""),
                "reason_code": str(item.get("reason_code") or ""),
                "detail": str(item.get("detail") or ""),
                "metric": item.get("metric"),
                "window_start": item.get("window_start"),
                "window_end": item.get("window_end"),
                "regime_version": item.get("regime_version"),
            }
            for item in value.get("exclusions") or ()
        ],
    }


def workbench_dto(value: WorkbenchView, schema_version: str | None = None) -> dict[str, Any]:
    payload = {
            "schema_version": _effective_version(schema_version),
            "scope": _scope(value.scope),
            "package": (
                None if value.package is None else _package(value.package, schema_version)
            ),
            "publication": _publication(value.publication),
            "clock": _ts(value.clock),
            "clock_kind": value.clock_kind,
            "account": _account(value.account),
            "opportunities": [_opportunity(item, schema_version) for item in value.opportunities],
            "orders": [
                {
                    "order_id": o.order_id,
                    "security_code": o.security_code,
                    "side": o.side,
                    "price_bound": _money(o.price_bound),
                    "accepted_total_qty": o.accepted_total_qty,
                    "known_fill_qty": o.known_fill_qty,
                    "unreported_fill_qty": o.unreported_fill_qty,
                    "hold_qty": o.hold_qty,
                    "hold_amount": _money(o.hold_amount),
                    "status": o.status,
                    "needs_reconciliation": o.needs_reconciliation,
                    "reason_codes": _reasons(o.reason_codes),
                    "fee_confirmed_total": _money(o.fee_confirmed_total),
                    "settlement_ready": o.settlement_ready,
                }
                for o in value.orders
            ],
            "action_blockers": list(value.action_blockers),
        }
    # Optional-by-contract (audit R18): run_receipt is emitted only when a run
    # receipt exists, so the pre-run response keeps its earlier shape.
    if value.run_receipt is not None:
        payload["run_receipt"] = _run_receipt(value.run_receipt)
    return _out("Workbench", payload, schema_version)


def records_dto(value: RecordsView, schema_version: str | None = None) -> dict[str, Any]:
    history = value.history
    return _out(
        "Records",
        {
            "schema_version": _effective_version(schema_version),
            "scope": _scope(value.scope),
            "package": (
                None if value.package is None else _package(value.package, schema_version)
            ),
            "prior_packages": [
                {**_package(item, schema_version), "superseded": True}
                for item in value.prior_packages
            ],
            "publication": _publication(value.publication),
            "account": _account(value.account),
            "baseline": {
                "request_id": history.baseline_request_id,
                "cash": _money(history.baseline_cash),
                "quantity": history.baseline_quantity,
                "security_code": history.baseline_security_code,
                "as_of": history.baseline_as_of,
                "source_id": history.baseline_source_id,
                "coverage_id": history.baseline_coverage_id,
            },
            "reports": [
                {
                    "report_id": item.report.report_id,
                    "receipt_id": item.report.receipt_id,
                    "status": item.report.status,
                    "received_at": item.report.received_at,
                    "plan_id": item.plan_id,
                    "security_code": item.report.security_code,
                    "side": item.report.side,
                    "quantity": item.report.quantity,
                    "price": _money(item.report.price),
                    "confirmed_fee": _money(item.report.confirmed_fee),
                    "occurred_at": item.report.occurred_at,
                    "intended_quantity": item.report.intended_quantity,
                    "intended_price_cap": _money(item.report.intended_price_cap),
                    "deviations": _reasons(item.deviations),
                    "conflicts": _reasons(item.report.conflicts),
                    "order_id": item.report.order_id,
                }
                for item in value.reports
            ],
            "facts": [
                {
                    "fact_id": item.fact.fact_id,
                    "plan_id": item.plan_id,
                    "security_code": item.fact.security_code,
                    "quantity": item.fact.quantity,
                    "price": _money(item.fact.price),
                    "confirmed_fee": _money(item.fact.confirmed_fee),
                    "fact_revision": item.fact.fact_revision,
                    "corrections": [
                        {
                            "correction_id": c.correction_id,
                            "receipt_id": c.receipt_id,
                            "fact_id": c.fact_id,
                            "expected_fact_revision": c.expected_fact_revision,
                            "quantity": c.quantity,
                            "price": _money(c.price),
                            "reason": c.reason,
                            "status": c.status,
                            "error_code": None
                            if c.error_code is None
                            else (c.error_code if c.error_code in REASON_TEXTS else UNCLASSIFIED),
                            "received_at": c.received_at,
                        }
                        for c in item.corrections
                    ],
                }
                for item in value.facts
            ],
            "events": [
                {
                    "event_id": event.event_id,
                    "event_sequence": event.event_sequence,
                    "revision": event.revision,
                    "kind": event.kind,
                    "fact_id": event.fact_id,
                    "cash_delta": _money(event.cash_delta),
                    "position_delta": _money(event.position_delta),
                    "quantity_delta": event.quantity_delta,
                    "recorded_at": event.recorded_at,
                }
                for event in value.events
            ],
            "plans": [
                {
                    "plan_id": item.plan_id,
                    "status": item.status,
                    "based_on_revision": item.based_on_revision,
                }
                for item in value.plans
            ],
            "orders": [
                {
                    "order_id": o.order_id,
                    "security_code": o.security_code,
                    "side": o.side,
                    "price_bound": _money(o.price_bound),
                    "accepted_total_qty": o.accepted_total_qty,
                    "known_fill_qty": o.known_fill_qty,
                    "unreported_fill_qty": o.unreported_fill_qty,
                    "hold_qty": o.hold_qty,
                    "hold_amount": _money(o.hold_amount),
                    "status": o.status,
                    "needs_reconciliation": o.needs_reconciliation,
                    "reason_codes": _reasons(o.reason_codes),
                    "fee_confirmed_total": _money(o.fee_confirmed_total),
                    "settlement_ready": o.settlement_ready,
                }
                for o in value.orders
            ],
            "observations": [
                {
                    "observation_id": o.observation_id,
                    "receipt_id": o.receipt_id,
                    "order_id": o.order_id,
                    "status": o.status,
                    "observed_at": o.observed_at,
                    "final_cumulative_fill_qty": o.final_cumulative_fill_qty,
                    "cancelled_qty": o.cancelled_qty,
                    "order_revision": o.order_revision,
                    "store_status": o.store_status,
                }
                for o in history.observations
            ],
            "fees": [
                {
                    "fee_id": f.fee_id,
                    "receipt_id": f.receipt_id,
                    "order_id": f.order_id,
                    "amount": _money(f.amount),
                    "occurred_at": f.occurred_at,
                    "store_status": f.store_status,
                }
                for f in history.fees
            ],
            "outbox_pending": history.outbox_pending,
        },
        schema_version,
    )


def funding_rules_dto(value: FundingRulesView) -> dict[str, Any]:
    funding = value.funding
    risk = value.risk
    return _out(
        "FundingRules",
        {
            "schema_version": SCHEMA_VERSION,
            "scope": _scope(value.scope),
            "composition_ref": _ref(value.composition_ref),
            "execution_profile": value.execution_profile,
            "funding": {
                "policy_ref": _ref(funding.policy_ref),
                "mode": funding.mode,
                "funding_revision": funding.funding_revision,
                "buckets": [
                    {
                        "bucket_id": item.bucket_id,
                        "phase": item.phase,
                        "target_notional": _money(item.target_notional),
                        "cash": _maybe(item.cash),
                        "cycle_id": item.cycle_id,
                        "locked_cycle_budget": _maybe(item.locked_cycle_budget),
                        "underfunded": _maybe(item.underfunded),
                        "trial_notional_cap": _maybe(item.trial_notional_cap),
                        "next_available": _maybe(item.next_available),
                    }
                    for item in funding.buckets
                ],
                "reserve": _maybe(funding.reserve),
                "unallocated_adjustment": _maybe(funding.unallocated_adjustment),
                "bound_account_revision": funding.bound_account_revision,
                "status_reason": None
                if funding.status_reason is None
                else reason(funding.status_reason),
                "pending_attributions": [
                    {
                        "order_id": item.order_id,
                        "security_code": item.security_code,
                        "cash_delta": _money(item.cash_delta),
                        "side": item.side,
                        "quantity": item.quantity,
                        "price": _money_or_none(item.price),
                    }
                    for item in getattr(funding, "pending_attributions", ()) or ()
                ],
            },
            "risk": {
                "policy_ref": _ref(risk.policy_ref),
                "stock_limit": _policy_decimal(risk.stock_limit),
                "industry_limit": _policy_decimal(risk.industry_limit),
                "total_limit": _policy_decimal(risk.total_limit),
                "lot_size": risk.lot_size,
                "commission_rate": _policy_decimal(risk.commission_rate),
                "minimum_commission": _money(risk.minimum_commission),
            },
            "adoption": [
                {
                    "binding_id": item.binding_id,
                    "title": item.title,
                    "consumer": item.consumer,
                    "mode": item.mode,
                    "version": item.version,
                    "digest": item.digest,
                    "source_ref": item.source_ref,
                    "allowed_uses": list(item.allowed_uses),
                    "dependencies": list(item.dependencies),
                    "status": item.status,
                    "missing_fields": list(item.missing_fields),
                    "owner": item.owner,
                    "acceptance_ref": item.acceptance_ref,
                }
                for item in value.adoption
            ],
            "action_blockers": list(value.action_blockers),
            "account_revision": value.account_revision,
            "retained_bucket_mode_available": False,
        },
    )


def outcome_dto(value: FactOutcome) -> dict[str, Any]:
    return _out(
        "FactOutcome",
        {
            "schema_version": SCHEMA_VERSION,
            "kind": value.kind,
            "receipt_id": value.receipt_id,
            "status": value.status,
            "account": _account(value.account),
            "deviations": _reasons(value.deviations),
            "conflicts": _reasons(value.conflicts),
            "affected_plans": list(value.affected_plans),
            "next_action": value.next_action,
            "cash_delta": _money_or_none(value.cash_delta),
            "quantity_delta": value.quantity_delta,
            "current_fact_revision": value.current_fact_revision,
        },
    )


def skip_plan_outcome_dto(record: dict[str, Any]) -> dict[str, Any]:
    return _out(
        "SkipPlanOutcome",
        {
            "schema_version": SCHEMA_VERSION,
            "skipped": True,
            "record": {
                "plan_id": str(record.get("plan_id") or ""),
                "skipped_at": str(record.get("skipped_at") or ""),
                "reason": record.get("reason"),
                "actor": str(record.get("actor") or ""),
            },
        },
    )


def accounts_dto(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return _out("AccountList", {"schema_version": SCHEMA_VERSION, "accounts": entries})


error_dto = errors.error_dto


def _require_version(body: dict[str, Any]) -> None:
    if body.get("schema_version") != SCHEMA_VERSION:
        raise FactError("SCHEMA_VERSION_MISMATCH", f"expected {SCHEMA_VERSION}")


def parse_submit_fill(body: dict[str, Any]) -> SubmitFill:
    _require_version(body)
    validate("SubmitFillRequest", body)
    return SubmitFill(
        report_id=body["report_id"],
        plan_id=body["plan_id"],
        quantity=body["quantity"],
        price=parse_decimal_text(body["price"], "price"),
        confirmed_fee=parse_decimal_text(body["confirmed_fee"], "confirmed_fee"),
        occurred_at=datetime.fromisoformat(body["occurred_at"]),
    )


def parse_correct_fill(body: dict[str, Any]) -> CorrectFill:
    _require_version(body)
    validate("CorrectFillRequest", body)
    return CorrectFill(
        correction_id=body["correction_id"],
        fact_id=body["fact_id"],
        expected_fact_revision=body["expected_fact_revision"],
        quantity=body["quantity"],
        price=parse_decimal_text(body["price"], "price"),
        reason=body["reason"],
    )


def parse_order_event(body: dict[str, Any]) -> SubmitOrderEvent:
    _require_version(body)
    validate("SubmitOrderEventRequest", body)

    def money(name: str) -> Decimal | None:
        value = body.get(name)
        return None if value is None else parse_decimal_text(value, name)

    def quantity(name: str) -> int | None:
        return body.get(name)

    return SubmitOrderEvent(
        report_id=body["report_id"],
        kind=body["kind"],
        order_id=body["order_id"],
        occurred_at=datetime.fromisoformat(body["occurred_at"]),
        side=body.get("side") or "buy",
        security_code=body.get("security_code") or "",
        quantity=quantity("quantity"),
        price=money("price"),
        confirmed_fee=money("confirmed_fee"),
        accepted_total_qty=quantity("accepted_total_qty"),
        order_status=body.get("order_status"),
        final_cumulative_fill_qty=quantity("final_cumulative_fill_qty"),
        cancelled_qty=quantity("cancelled_qty"),
        price_bound=money("price_bound"),
        fee_upper_bound=money("fee_upper_bound"),
        order_revision=body.get("order_revision") or 1,
    )
