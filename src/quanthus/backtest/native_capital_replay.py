"""Bounded native capital consumer. Unknown source coordinates remain explicit holds."""

from __future__ import annotations

import collections
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from quanthus.backtest.capital_clock import CapitalReplayClock
from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    BacktestResult,
    BacktestRunSpec,
    BacktestSignal,
    CorporateActionStep,
    EventType,
    MarketSession,
    OpenExecutionState,
    UnitState,
)
from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.backtest.native_capital_adapter import NativeAdapterError, qualify_action
from quanthus.backtest.policy import ExecutionPolicy
from quanthus.backtest.strategy_replay import (
    PITDailyBar,
    ReplayAction,
    ReplayDecision,
    StrategyReplayContext,
    StrategyReplayFrame,
    StrategyReplaySpec,
    run_strategy_replay,
)
from quanthus.contracts import SnapshotRef
from quanthus.hf_dwd_research import digest

VERSION = "native-capital-fixed-minimum-holding-v1"
CASH = Decimal("100000.00")
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class CapitalInputHold(BacktestInputError):
    def __init__(self, reason: str, day: date, listing: str, actions: tuple[str, ...] = ()):
        self.detail = {
            "reason": reason,
            "date": str(day),
            "listing_id": listing,
            "action_ids": list(actions),
            "owner": "P10 #556",
        }
        super().__init__(json.dumps(self.detail, sort_keys=True))


def rows(db: Any, query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = db.execute(query, params or [])
    return [
        dict(zip([v[0] for v in cursor.description], row, strict=True)) for row in cursor.fetchall()
    ]


class CapitalAuditEngine(BacktestEngine):
    """Retain committed P2 steps even when a later source guard stops the arm."""

    def __init__(self, policy: ExecutionPolicy) -> None:
        super().__init__(policy)
        self.receipts: list[dict[str, Any]] = []

    def record(self, signal: BacktestSignal, result: BacktestResult) -> None:
        fills = [dict(e.payload) for e in result.events if e.event_type is EventType.FILL]
        snapshots = [e.payload for e in result.events if e.event_type is EventType.DAILY_SNAPSHOT]
        for event in result.events:
            if event.event_type is EventType.LEDGER_ENTRY:
                assert sum(Decimal(p["amount"]) for p in event.payload["postings"]) == 0
        assert snapshots
        snapshot = snapshots[-1]
        self.receipts.append(
            {
                "side": signal.side.value,
                "decision_date": str(signal.snapshot.market_date),
                "listing_id": signal.security_code,
                "quantity": signal.quantity,
                "fills": [
                    {
                        k: fill[k]
                        for k in (
                            "filled_at",
                            "side",
                            "price",
                            "quantity",
                            "gross_amount",
                            "commission",
                            "stamp_duty",
                            "transfer_fee",
                            "total_fees",
                        )
                    }
                    for fill in fills
                ],
                "ending_cash": snapshot["cash"],
                "external_pool": snapshot["external_pool"],
                "position_quantity": snapshot["position_quantity"],
                "system_wealth": str(
                    money(
                        Decimal(snapshot["cash"])
                        + Decimal(snapshot["external_pool"])
                        + Decimal(snapshot["market_value"])
                    )
                ),
                "order_count": sum(e.event_type is EventType.ORDER_CREATED for e in result.events),
                "carried": sum(
                    e.event_type is EventType.ORDER_STATUS
                    and e.payload.get("transition", "").endswith("_carried")
                    for e in result.events
                ),
            }
        )

    def run_buy_session(
        self,
        *,
        run: BacktestRunSpec,
        signal: BacktestSignal,
        session: MarketSession,
        opening_state: UnitState,
    ) -> BacktestResult:
        result = super().run_buy_session(
            run=run, signal=signal, session=session, opening_state=opening_state
        )
        self.record(signal, result)
        return result

    def run_sell_sequence(
        self,
        *,
        run: BacktestRunSpec,
        signal: BacktestSignal,
        sessions: Sequence[MarketSession],
        opening_state: UnitState,
        corporate_actions: Sequence[CorporateActionStep] = (),
    ) -> BacktestResult:
        result = super().run_sell_sequence(
            run=run,
            signal=signal,
            sessions=sessions,
            opening_state=opening_state,
            corporate_actions=corporate_actions,
        )
        self.record(signal, result)
        return result


@dataclass(frozen=True)
class NativeDecisionView:
    snapshot: SnapshotRef
    choice: dict[str, Any] | None

    def securities(self) -> tuple[str, ...]:
        return () if self.choice is None else (self.choice["listing_id"],)

    def current_bars(self) -> tuple[PITDailyBar, ...]:
        raise BacktestInputError("capital strategy receives only the frozen T nominal choice")

    def bar(self, security_code: str) -> PITDailyBar:
        raise BacktestInputError("arbitrary price queries are outside the decision capability")

    def history(self, security_code: str, sessions: int) -> tuple[PITDailyBar, ...]:
        raise BacktestInputError("history is already frozen in the T-only features")


class FixedCapitalStrategy:
    strategy_id = "fixed-native-capital"
    strategy_version = VERSION

    def __init__(self) -> None:
        self.nominal_attempts: list[dict[str, Any]] = []

    def decide(self, context: StrategyReplayContext) -> ReplayDecision:
        if context.unit_state.settled_quantity:
            return ReplayDecision(ReplayAction.EXIT, "fixed_next_close_exit")
        view = context.market_view
        if not isinstance(view, NativeDecisionView):
            raise BacktestInputError("native T-only decision view required")
        choice = view.choice
        if choice is None:
            return ReplayDecision(ReplayAction.NO_ACTION, "no_nominal_candidate")
        attempt = {
            "date": str(context.snapshot.market_date),
            "listing_id": choice["listing_id"],
            "ranking_vol12": choice["ranking_vol12"],
        }
        self.nominal_attempts.append(attempt)
        if choice["decision_hold"]:
            attempt["decision"] = "blocked_at_T"
            return ReplayDecision(ReplayAction.NO_ACTION, "T_input_unqualified")
        price = Decimal(choice["decision_close"])
        quantity = (
            int(context.unit_state.cash * Decimal("0.30") / (price * Decimal("1.21")) / 100) * 100
        )
        attempt["quantity"] = quantity
        if quantity <= 0:
            attempt["decision"] = "below_one_lot"
            return ReplayDecision(ReplayAction.NO_ACTION, "T_sizing_below_lot")
        attempt["decision"] = "original_nominal_order"
        return ReplayDecision(
            ReplayAction.ENTER,
            "fixed_band_nominal",
            security_code=choice["listing_id"],
            quantity=quantity,
        )


class NativeCapitalStore:
    """Read verified native facts. No repair, source priority, or inferred action date."""

    def __init__(
        self, db: Any, request: dict[str, Any], purpose: dict[str, Any] | None = None
    ) -> None:
        self.db, self.request = db, request
        self.cutoff = datetime.fromisoformat(request["source_knowledge_cutoff"])
        self.revision = request["authority"]["revision"]
        self.content = request["authority"]["content_digest"]
        self.native_revision = self.revision
        self.run_prefix = (
            request.get("task_id", "RESEARCH-CAPITAL-001-T1")
            + "-"
            + request.get("attempt_id", "A1")
        )
        self.clock_identity: dict[str, Any] = {}
        self.purpose_application: dict[str, Any] | None = None
        if "effective_input" in request:
            self.content = request["effective_input"]["content_digest"]
            self.revision = request["effective_input"]["source_revision"]
            self.clock_identity = request["effective_input"]["clock_identity"]
        self.accesses: list[dict[str, Any]] = []
        self.native_cache: dict[tuple[str, date], dict[str, Any] | None] = {}
        self.arm = ""
        self.last_access: dict[str, Any] = {}
        self.actions: dict[tuple[str, date], list[dict[str, Any]]] = collections.defaultdict(list)
        self.unknown_action_dates: dict[str, list[str]] = collections.defaultdict(list)
        self.terms: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for t in rows(db, "SELECT * FROM capital_terms ORDER BY corporate_action_term_id"):
            self.terms[t["corporate_action_id"]].append(t)
        for a in rows(db, "SELECT * FROM capital_actions ORDER BY corporate_action_id"):
            if a["action_status"] == "IMPLEMENTED":
                if a["ex_date"] is None:
                    self.unknown_action_dates[a["security_id"]].append(a["corporate_action_id"])
                # A missing ex_date remains a hold at its observed business coordinate.
                day = a["ex_date"] or a["effective_at"].astimezone(MARKET_TIMEZONE).date()
                self.actions[a["security_id"], day].append(a)
        self.exceptions: dict[tuple[str, date], list[str]] = collections.defaultdict(list)
        for q in rows(db, "SELECT * FROM capital_quality"):
            if q["affected_table"] not in {"dwd_corporate_action", "dwd_corporate_action_term"}:
                continue
            if q["resolution_state"] in {"RESOLVED", "CLOSED"}:
                continue
            key = json.loads(q["affected_business_key_json"])
            if key.get("stock_code"):
                day = q["affected_effective_at"].astimezone(MARKET_TIMEZONE).date()
                self.exceptions[key["stock_code"], day].append(q["quality_exception_id"])
        self.identities = {
            r["listing_id"]: r
            for r in rows(
                db,
                "SELECT listing_id,min(security_id) security_id,min(venue_id) venue_id,"
                "min(listing_code) listing_code FROM dwd_listing WHERE knowledge_to IS NULL "
                "GROUP BY listing_id HAVING count(DISTINCT security_id)=1 "
                "AND count(DISTINCT venue_id)=1 AND count(DISTINCT listing_code)=1",
            )
        }
        if "purpose" in request:
            from quanthus.backtest.capital_purpose import effective_event, require

            require(purpose is not None, "EXPLICIT_INPUT_REQUIRED")
            assert purpose is not None
            scope = purpose["manifest"]["scope"]
            identity = self.identities.get(scope["listing_id"])
            require(
                identity is not None
                and identity["security_id"] == scope["security_id"]
                and identity["listing_code"] == scope["stock_code"],
                "LISTING_BINDING",
            )
            key = scope["security_id"], date.fromisoformat(scope["ex_date"])
            header, terms, self.purpose_application = effective_event(
                request, purpose, self.actions.get(key, []), self.terms
            )
            self.actions[key] = [header]
            self.terms[header["corporate_action_id"]] = terms
        self.calendars = {
            (r["venue_id"], r["trade_date"]): r
            for r in rows(
                db,
                "SELECT venue_id,CAST(trade_date AS DATE) trade_date,open_at,close_at "
                "FROM dwd_trading_calendar "
                "WHERE knowledge_to IS NULL AND is_open AND trade_date BETWEEN "
                "DATE '2020-01-01' AND DATE '2021-12-31'",
            )
        }

    def clock(self, day: date) -> CapitalReplayClock:
        if self.clock_identity:
            return CapitalReplayClock(
                day,
                self.cutoff,
                self.content,
                self.request["request_id"],
                self.request["task_id"],
                self.request["attempt_id"],
                json.dumps(self.clock_identity, sort_keys=True, separators=(",", ":")),
            )
        return CapitalReplayClock(day, self.cutoff, self.content)

    def native(self, listing: str, day: date) -> dict[str, Any] | None:
        key = listing, day
        if key not in self.native_cache:
            found = rows(
                self.db,
                "SELECT * FROM capital_execution WHERE listing_id=? AND trade_date=?",
                [listing, day],
            )
            if len(found) > 1:
                raise CapitalInputHold("NATIVE_IDENTITY_VERSION_AMBIGUOUS", day, listing)
            self.native_cache[key] = found[0] if found else None
        return self.native_cache[key]

    def record(self, listing: str, day: date, kind: str, **fields: Any) -> None:
        self.last_access = {
            "arm": self.arm,
            "listing_id": listing,
            "date": str(day),
            "kind": kind,
            **fields,
        }
        self.accesses.append(self.last_access)

    def session(self, listing: str, day: date) -> MarketSession:
        self.record(listing, day, "execution")
        identity = self.identities.get(listing)
        if identity is None:
            raise CapitalInputHold("NATIVE_IDENTITY_UNKNOWN", day, listing)
        cal = self.calendars.get((identity["venue_id"], day))
        if cal is None or cal["open_at"] is None or cal["close_at"] is None:
            raise CapitalInputHold("CALENDAR_CLOCK_UNKNOWN", day, listing)
        row = self.native(listing, day)
        state = OpenExecutionState.MISSING_EVIDENCE
        op = close = None
        can_buy = can_sell = None
        if row is not None:
            if row["listing_currency"] != "CNY" or row["lot_size"] != 100:
                raise CapitalInputHold("CURRENCY_OR_LOT_UNQUALIFIED", day, listing)
            known = [
                row[k] for k in ("bar_known", "state_known", "listing_known", "security_known")
            ]
            if any(k is None for k in known):
                raise CapitalInputHold("SOURCE_KNOWLEDGE_UNKNOWN", day, listing)
            if any(k is not None and k > self.cutoff for k in known):
                raise CapitalInputHold("SOURCE_KNOWLEDGE_AFTER_CUTOFF", day, listing)
            flags = [row[k] for k in ("is_tradable", "can_buy", "can_sell")]
            if all(type(v) is bool for v in flags[1:]):
                can_buy, can_sell = row["can_buy"], row["can_sell"]
            if row["reason_code"] == "SUSPENDED" and flags == [False, False, False]:
                state = OpenExecutionState.SUSPENDED
            elif (
                all(type(v) is bool for v in flags)
                and flags[0]
                and row["reason_code"] == "TRADABLE_OBSERVED"
            ):
                prices = [row[k] for k in ("open", "close", "upper_price", "lower_price")]
                if all(isinstance(v, Decimal) and v.is_finite() and v > 0 for v in prices):
                    op, close, upper, lower = prices
                    if lower < upper and lower <= op <= upper:
                        state = (
                            OpenExecutionState.BUY_BLOCKED_LIMIT_UP
                            if op == upper
                            else OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN
                            if op == lower
                            else OpenExecutionState.EXECUTABLE
                        )
                    else:
                        op = close = None
        return MarketSession(
            day,
            cal["open_at"],
            cal["close_at"],
            listing,
            op,
            close,
            state,
            self.native_revision,
            self.run_prefix + "-native-execution-" + listing + "-" + str(day),
            can_buy,
            can_sell,
        )

    def action_hold(self, listing: str, day: date) -> str | None:
        self.record(listing, day, "held_action_gate")
        identity = self.identities.get(listing)
        if identity is None or self.native(listing, day) is None:
            raise CapitalInputHold("HELD_IDENTITY_UNKNOWN", day, listing)
        unknown = self.unknown_action_dates.get(identity["security_id"], [])
        if unknown:
            raise CapitalInputHold("ACTION_BUSINESS_DATE_UNKNOWN", day, listing, tuple(unknown))
        ids = self.exceptions.get((identity["listing_code"], day), [])
        if ids:
            raise CapitalInputHold("UNRESOLVED_SOURCE_ACTION_EXCEPTION", day, listing, tuple(ids))
        events = self.actions.get((identity["security_id"], day), [])
        names = tuple(a["corporate_action_id"] for a in events)
        if len(events) > 1:
            raise CapitalInputHold("ACTION_EVENT_AMBIGUOUS", day, listing, names)
        for action in events:
            try:
                qualify_action(action, self.terms[action["corporate_action_id"]])
            except NativeAdapterError as exc:
                raise CapitalInputHold(str(exc), day, listing, names) from exc
        return None

    def action_steps(self, listing: str, day: date) -> tuple[CorporateActionStep, ...]:
        self.action_hold(listing, day)
        identity = self.identities[listing]
        events = self.actions.get((identity["security_id"], day), [])
        if not events:
            # Only absence in this exact sealed observation set is consumed.
            self.record(listing, day, "no_recorded_action_in_fixed_snapshot")
            return ()
        action = events[0]
        item = qualify_action(action, self.terms[action["corporate_action_id"]])
        session = self.session(listing, day)
        if session.open_price is None:
            raise CapitalInputHold(
                "ACTION_OBSERVED_OPEN_MARK_UNAVAILABLE", day, listing, (item.action_id,)
            )
        from dataclasses import replace

        native_action = item.source_pit_action(
            cutoff=self.cutoff,
            source_revision=action["source_revision"] if self.clock_identity else self.revision,
            snapshot_id=session.snapshot_id,
        )
        native_action = replace(native_action, security_code=listing)
        return (
            CorporateActionStep(
                native_action,
                session.open_at,
                session.open_price,
                session.snapshot_id,
                self.clock(day).to_json(),
            ),
        )


@dataclass
class NativeFrameSource:
    store: NativeCapitalStore
    execution_date: date

    def session(self, security_code: str) -> MarketSession:
        return self.store.session(security_code, self.execution_date)

    def block_reason(self, security_code: str) -> str | None:
        return self.store.action_hold(security_code, self.execution_date)

    def corporate_actions(
        self, security_code: str, prior_mark_price: Decimal
    ) -> tuple[CorporateActionStep, ...]:
        return self.store.action_steps(security_code, self.execution_date)


def nominal_choices(db: Any, q: dict[str, Any], store: NativeCapitalStore) -> list[dict[str, Any]]:
    f = q["screen"]
    found = rows(
        db,
        f"""
        SELECT * FROM (
          SELECT listing_id,security_id,trade_date,px,
          CAST(round(return_vol20,12) AS DECIMAL(20,12)) AS ranking_vol12,
          CASE WHEN momentum20 BETWEEN 0.02 AND 0.12 THEN 'candidate' ELSE 'control' END AS arm,
          row_number() OVER(PARTITION BY trade_date,(momentum20 BETWEEN 0.02 AND 0.12)
            ORDER BY round(return_vol20,12),listing_id) AS rank
          FROM features WHERE security_ok IS TRUE AND is_tradable IS TRUE AND px>0
          AND positive_closes21=21 AND returns20=20 AND amounts20=20
          AND listing_age_floor_sessions>={f["min_listing_sessions"]}
          AND average_amount20>={f["min_avg_turnover_prior20"]}
          AND return_vol20<={f["max_unadjusted_close_return_vol20"]}
          AND abs(unadjusted_return)<={f["max_abs_unadjusted_close_return"]}
          AND momentum20 IS NOT NULL AND isfinite(momentum20)
          AND trade_date BETWEEN DATE '2020-03-02' AND DATE '2021-12-30'
        ) WHERE rank=1 ORDER BY trade_date,arm
    """,
    )
    result = []
    for choice in found:
        row = store.native(choice["listing_id"], choice["trade_date"])
        decision_hold = (
            row is None
            or row["reason_code"] != "TRADABLE_OBSERVED"
            or row["is_tradable"] is not True
            or row["bar_known"] is None
            or row["state_known"] is None
            or bool(store.unknown_action_dates.get(choice["security_id"], []))
        )
        result.append(
            {
                "date": str(choice["trade_date"]),
                "arm": choice["arm"],
                "listing_id": choice["listing_id"],
                "ranking_vol12": str(choice["ranking_vol12"]),
                "decision_close": str(row["close"]) if row and row["close"] else "0",
                "decision_hold": decision_hold,
            }
        )
    return result


def replay_arms(
    db: Any,
    q: dict[str, Any],
    policy: ExecutionPolicy,
    code_git: str,
    purpose: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = NativeCapitalStore(db, q, purpose)
    nominal = nominal_choices(db, q, store)
    choices = {(r["arm"], r["date"]): r for r in nominal}
    days_by_venue = rows(
        db,
        "SELECT venue_id,list(trade_date ORDER BY trade_date) AS calendar_days "
        "FROM calendar WHERE trade_date>=DATE '2020-03-02' GROUP BY venue_id",
    )
    if (
        len(days_by_venue) != 2
        or days_by_venue[0]["calendar_days"] != days_by_venue[1]["calendar_days"]
    ):
        raise BacktestInputError("venue execution calendars differ")
    days = days_by_venue[0]["calendar_days"]
    arms: dict[str, Any] = {}
    for arm in ("candidate", "control"):
        store.arm = arm
        strategy = FixedCapitalStrategy()
        engine = CapitalAuditEngine(policy)
        frames = []
        for t, nxt in zip(days, days[1:], strict=False):
            clock = store.clock(t)
            snap = SnapshotRef(
                t, store.cutoff, store.revision, store.run_prefix + "-capital-" + arm + "-" + str(t)
            )
            frames.append(
                StrategyReplayFrame(
                    snap,
                    clock.simulated_close,
                    nxt,
                    (),
                    decision_view=NativeDecisionView(snap, choices.get((arm, str(t)))),
                    source=NativeFrameSource(store, nxt),
                    snapshot_replay_clock_json=clock.to_json(),
                )
            )
        run = BacktestRunSpec(
            code_version=code_git,
            parameter_hash=digest(q),
            data_snapshot_id=frames[0].snapshot.snapshot_id,
            feature_version="R2-raw20-v1",
            rule_version=VERSION,
            run_id=store.run_prefix + "-" + arm,
            execution_policy_version=policy.policy_version,
            execution_profile_id="close-to-next-open-v1",
            schedule_version=policy.profile("close-to-next-open-v1").schedule_version,
            data_content_digest=store.content,
            data_manifest_sha256=q.get("effective_input", {}).get(
                "manifest_sha256", q["authority"]["manifest_sha256"]
            ),
        )
        try:
            result = run_strategy_replay(
                engine,
                strategy,
                StrategyReplaySpec(run, "capital-" + arm, CASH, tuple(frames)),
            )
            if result.final_state.settled_quantity:
                terminal = store.session(str(result.final_state.security_code), days[-1])
                if terminal.close_price is None:
                    raise CapitalInputHold(
                        "TERMINAL_MARK_UNQUALIFIED", days[-1], str(result.final_state.security_code)
                    )
            for event in result.backtest.events:
                if event.event_type is EventType.LEDGER_ENTRY:
                    assert sum(Decimal(p["amount"]) for p in event.payload["postings"]) == 0
            arms[arm] = {
                "status": "completed",
                "result": result.to_dict(),
                "nominal_attempts": strategy.nominal_attempts,
            }
        except CapitalInputHold as exc:
            arms[arm] = {
                "status": "blocked",
                "hold": exc.detail,
                "nominal_attempts": strategy.nominal_attempts,
                "capital_metrics": None,
            }
        except BacktestInputError as exc:
            observed = store.last_access
            when = date.fromisoformat(observed["date"])
            asset = observed["listing_id"]
            security = store.identities[asset]["security_id"]
            action_ids = [a["corporate_action_id"] for a in store.actions.get((security, when), [])]
            arms[arm] = {
                "status": "blocked",
                "hold": {
                    "reason": str(exc),
                    "date": str(when),
                    "listing_id": asset,
                    "action_ids": action_ids,
                    "owner": "P2 consumer",
                },
                "nominal_attempts": strategy.nominal_attempts,
                "capital_metrics": None,
            }
        arms[arm]["p2_step_receipts"] = engine.receipts
    return {
        "arms": arms,
        "nominal_choices": nominal,
        "native_accesses": store.accesses,
        "decision": capital_decision(arms),
        "historical_pit_claim": False,
        "source_knowledge_cutoff": q["source_knowledge_cutoff"],
        "purpose_application": store.purpose_application,
        "native_authority": q["authority"],
        "effective_input": q.get("effective_input"),
        "no_action_semantics": q["no_action_semantics"],
    }


def capital_decision(arms: dict[str, Any]) -> dict[str, Any]:
    if any(a["status"] != "completed" for a in arms.values()):
        return {"branch": "blocked", "verdict": "not_evaluated", "primary_increment": None}
    data = {k: v["result"] for k, v in arms.items()}
    metrics = {k: v["performance"] for k, v in data.items()}
    years: dict[str, dict[str, Any]] = {}
    for name, arm in data.items():
        wealth = {r["market_date"]: Decimal(r["system_equity"]) for r in arm["equity_curve"]}
        prior = CASH
        years[name] = {}
        for year in (2020, 2021):
            last = wealth[max(d for d in wealth if d.startswith(str(year)))]
            trades = sum(t["exit_date"].startswith(str(year)) for t in arm["trades"])
            years[name][str(year)] = {"return": str((last - prior) / CASH), "closed_trades": trades}
            prior = last
    increment = Decimal(metrics["candidate"]["total_return"]) - Decimal(
        metrics["control"]["total_return"]
    )
    per_year = {
        y: Decimal(years["candidate"][y]["return"]) - Decimal(years["control"][y]["return"])
        for y in ("2020", "2021")
    }
    enough = all(m["closed_trades"] >= 100 for m in metrics.values()) and all(
        y["closed_trades"] >= 20 for a in years.values() for y in a.values()
    )
    top3 = sum(
        sorted(
            [max(Decimal(t["pnl"]), Decimal(0)) for t in data["candidate"]["trades"]], reverse=True
        )[:3],
        Decimal(0),
    )
    robust = increment - top3 / CASH
    candidate_return = Decimal(metrics["candidate"]["total_return"])
    supported = (
        candidate_return > 0
        and increment >= Decimal("0.01")
        and all(v > 0 for v in per_year.values())
        and robust > 0
        and Decimal(metrics["candidate"]["maximum_drawdown"])
        <= Decimal(metrics["control"]["maximum_drawdown"]) + Decimal("0.02")
    )
    branch = (
        "insufficient"
        if not enough
        else "advance"
        if supported
        else "not_met"
        if candidate_return <= 0 or increment <= 0
        else "uncertain"
    )
    return {
        "branch": branch,
        "verdict": "supported"
        if branch == "advance"
        else "not_met"
        if branch == "not_met"
        else "inconclusive",
        "primary_increment": str(increment),
        "by_year": years,
        "year_increment": {k: str(v) for k, v in per_year.items()},
        "after_static_top3_increment": str(robust),
        "sample_sufficient": enough,
    }
