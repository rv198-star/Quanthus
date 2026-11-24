"""Persistent simulated order book + versioned continuous-auction matching.

A ``PaperAccount`` wraps the shared ``P12DesktopApplication`` account (same
SQLite fact ledger, same cash/positions) and adds a self-directed simulated
order flow on top:

- ``submit`` accepts a limit order, reserves cash or sellable quantity, stores
  it in ``sim_orders`` (same ``state_dir``, its own ``p12-paper.sqlite``), then
  tries one immediate match against the live quote.
- ``poll`` re-fetches quotes for open orders and fills any that became
  marketable inside continuous-auction windows - only quotes at or after the
  order's ``accepted_at`` may fill it, and the same snapshot never fills twice.
- ``cancel`` releases the remaining quantity's hold; ``expire`` day-closes any
  order still open at the session close.
- A simulated fill is posted to the shared ledger as an ``ExecutionReport``
  whose ``source_id`` is the rule version and ``source_sequence`` is unique per
  fill, so it replays, stays idempotent and reads back as ``sim_matched``.

The paper ledger lives beside the shared fact store so a service restart sees
the same open orders; it never writes to the adopted fact schema.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.capabilities.execution import ExecutionReport
from quanthus.capabilities.execution.facts import OrderObservation
from quanthus.p12_paper.exec_rule import ORDER_STATUS, REJECT, RULE_VERSION, OrderCommand
from quanthus.p12_paper.quotes import SHANGHAI, Quote, QuoteService
from quanthus.shared.fact_contracts import FactError

STORE_NAME = "p12-paper.sqlite"
USER_REPORTED_SOURCE = "user_reported"
SIM_MATCHED_SOURCE = "sim_matched"

# SQLite column order shared by the sim_orders table, the rebuild migration
# and the row tuple; session_date is last so older rows stay readable.
SIM_ORDER_COLUMNS = (
    "account_id", "order_id", "side", "security_code", "security_name",
    "quantity", "limit_price", "filled_qty", "status", "rule_version",
    "accepted_at", "last_quote_time", "avg_price", "reject_reason",
    "source", "receipt_id", "fill_source_seq", "updated_at", "session_date",
)

# Continuous-auction open in Asia/Shanghai, matching the exchange session.
SESSION_OPEN_ISO = "09:30:00+08:00"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).astimezone()


@dataclass(frozen=True)
class PaperOrder:
    account_id: str
    order_id: str
    side: str
    security_code: str
    security_name: str
    quantity: int
    limit_price: Decimal
    filled_qty: int
    status: str               # open | filled | cancelled | expired | rejected
    rule_version: str
    accepted_at: datetime
    last_quote_time: datetime | None
    avg_price: Decimal | None
    reject_reason: str | None
    source: str               # sim_matched | user_reported
    receipt_id: str | None
    # Issue #723 E8 r2-5: a plan-confirm order is bound to the confirming
    # package authoritative next_session - it may rest unfilled past the
    # generic close and releases only inside that session open, never
    # earlier on a disagreeing quote calendar.  None stays a same-day order.
    session_date: str | None = None

    @property
    def remaining_qty(self) -> int:
        return max(0, self.quantity - self.filled_qty)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "order_id": self.order_id,
            "side": self.side,
            "security_code": self.security_code,
            "security_name": self.security_name,
            "quantity": self.quantity,
            "limit_price": format(self.limit_price, "f"),
            "filled_qty": self.filled_qty,
            "remaining_qty": self.remaining_qty,
            "status": self.status,
            "status_text": ORDER_STATUS.get(self.status, self.status),
            "rule_version": self.rule_version,
            "accepted_at": self.accepted_at.isoformat(),
            "last_quote_time": None if self.last_quote_time is None else self.last_quote_time.isoformat(),
            "avg_price": None if self.avg_price is None else format(self.avg_price, "f"),
            "reject_reason": self.reject_reason,
            "reject_text": REJECT.get(self.reject_reason or "", None),
            "source": self.source,
            "receipt_id": self.receipt_id,
            "session_date": self.session_date,
        }


@dataclass(frozen=True)
class SubmitOutcome:
    order_id: str
    status: str               # open | filled | rejected
    reason_code: str | None
    reason_text: str | None
    filled_qty: int
    avg_price: Decimal | None
    rule_version: str
    matched_quote_time: datetime | None
    receipt_id: str | None
    # Issue #723 E8 r5 (buy): the confirm-time re-sized quantity the server
    # asks the user to accept.  Set only on a CONFIRM_RESIZED advisory.
    confirm_qty: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "order_id": self.order_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "reason_text": self.reason_text,
            "filled_qty": self.filled_qty,
            "avg_price": None if self.avg_price is None else format(self.avg_price, "f"),
            "rule_version": self.rule_version,
            "matched_quote_time": None if self.matched_quote_time is None else self.matched_quote_time.isoformat(),
            "receipt_id": self.receipt_id,
        }
        if self.confirm_qty is not None:
            out["confirm_qty"] = self.confirm_qty
        return out


class PaperOrderStore:
    """SQLite-backed simulated order book beside the shared fact store."""

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = str(path)
        self._clock = clock or _utcnow
        self._init()

    def _migrate(self, c: sqlite3.Connection) -> None:
        """Backfill ``account_id`` and move to the (account, order) key.

        Early R0 stores kept every account's orders in one table without a
        discriminator; those rows are reassigned to ``SYN-P12-A`` (the only
        account that could have written them).  R1 then keyed the table by
        ``order_id`` alone, which let one account read or cancel another's
        order when they shared a client order id.  R2 re-keys the table on
        ``(account_id, order_id)`` so the same client id can be reused by a
        different account without collision, and every read/write stays
        scoped to its own account.
        """
        cols = {row[1] for row in c.execute("PRAGMA table_info(sim_orders)")}
        pk = [r[1] for r in c.execute("PRAGMA table_info(sim_orders)") if r[5]]
        if "account_id" in cols and pk == ["account_id", "order_id"] and "session_date" in cols:
            return
        # Any older shape rebuilds once into the current columns.  Rows
        # without account_id could only have been written by the single
        # bound account, so they get the synthetic discriminator; rows
        # without session_date stay unbound (a same-day order).
        c.execute("ALTER TABLE sim_orders RENAME TO sim_orders_old")
        self._create(c)
        old_cols = {row[1] for row in c.execute("PRAGMA table_info(sim_orders_old)")}
        if "account_id" not in old_cols:
            c.execute(
                "ALTER TABLE sim_orders_old ADD COLUMN account_id TEXT NOT NULL DEFAULT 'SYN-P12-A'"
            )
            old_cols = {row[1] for row in c.execute("PRAGMA table_info(sim_orders_old)")}
        shared = [col for col in SIM_ORDER_COLUMNS if col in old_cols]
        c.execute(
            "INSERT OR IGNORE INTO sim_orders (" + ",".join(SIM_ORDER_COLUMNS) + ")"
            + " SELECT " + ",".join(shared) + ",NULL FROM sim_orders_old"
        )
        c.execute("DROP TABLE sim_orders_old")

    def _migrate_legacy(self) -> None:
        # kept for clarity; migration runs inside _init via _migrate
        return

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as c:
            self._create(c)
            c.execute("CREATE INDEX IF NOT EXISTS sim_orders_status ON sim_orders(status)")
            c.execute("CREATE INDEX IF NOT EXISTS sim_orders_account ON sim_orders(account_id, status)")
            self._migrate(c)

    @staticmethod
    def _create(c: sqlite3.Connection) -> None:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS sim_orders(
              account_id TEXT NOT NULL,
              order_id TEXT NOT NULL,
              side TEXT NOT NULL,
              security_code TEXT NOT NULL,
              security_name TEXT NOT NULL,
              quantity INTEGER NOT NULL,
              limit_price TEXT NOT NULL,
              filled_qty INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL,
              rule_version TEXT NOT NULL,
              accepted_at TEXT NOT NULL,
              last_quote_time TEXT,
              avg_price TEXT,
              reject_reason TEXT,
              source TEXT NOT NULL,
              receipt_id TEXT,
              fill_source_seq TEXT,
              updated_at TEXT NOT NULL,
              session_date TEXT,
              PRIMARY KEY (account_id, order_id)
            )
            """
        )

    def get(self, order_id: str, account_id: str) -> PaperOrder | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM sim_orders WHERE order_id=? AND account_id=?",
                (order_id, account_id),
            ).fetchone()
        return None if row is None else self._row(row)

    def insert(self, order: PaperOrder, *, fill_source_seq: str | None = None) -> None:
        vals = list(self._vals(order))
        vals[16] = fill_source_seq
        with self._connect() as c:
            c.execute(
                "INSERT INTO sim_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(vals),
            )

    def update(self, order: PaperOrder, *, fill_source_seq: str | None = None) -> None:
        with self._connect() as c:
            c.execute(
                "UPDATE sim_orders SET filled_qty=?, status=?, last_quote_time=?, avg_price=?, reject_reason=?, receipt_id=?, fill_source_seq=COALESCE(?, fill_source_seq), updated_at=?, session_date=? WHERE order_id=? AND account_id=?",
                (
                    order.filled_qty, order.status,
                    None if order.last_quote_time is None else order.last_quote_time.isoformat(),
                    None if order.avg_price is None else format(order.avg_price, "f"),
                    order.reject_reason, order.receipt_id, fill_source_seq,
                    _utcnow().isoformat(), order.session_date, order.order_id, order.account_id,
                ),
            )

    def update_if_status(
        self,
        order: PaperOrder,
        *,
        expected_status: str,
        fill_source_seq: str | None = None,
    ) -> bool:
        """Write ``order`` only while the stored row still has ``expected_status``.

        Compare-and-set for the matcher: a stale in-memory order from a
        racing read cannot overwrite a terminal state another thread just
        committed.  Returns True when the row was updated.
        """
        with self._connect() as c:
            cur = c.execute(
                "UPDATE sim_orders SET filled_qty=?, status=?, last_quote_time=?, avg_price=?, reject_reason=?, receipt_id=?, fill_source_seq=COALESCE(?, fill_source_seq), updated_at=?, session_date=? WHERE order_id=? AND account_id=? AND status=?",
                (
                    order.filled_qty, order.status,
                    None if order.last_quote_time is None else order.last_quote_time.isoformat(),
                    None if order.avg_price is None else format(order.avg_price, "f"),
                    order.reject_reason, order.receipt_id, fill_source_seq,
                    _utcnow().isoformat(), order.session_date, order.order_id, order.account_id,
                    expected_status,
                ),
            )
            return cur.rowcount > 0

    def list_orders(
        self, account_id: str, *, statuses: tuple[str, ...] | None = None
    ) -> list[PaperOrder]:
        sql = "SELECT * FROM sim_orders WHERE account_id=?"
        args: list[Any] = [account_id]
        if statuses:
            sql += " AND status IN (" + ",".join("?" * len(statuses)) + ")"
            args += list(statuses)
        sql += " ORDER BY accepted_at"
        with self._connect() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def open_for(self, account_id: str, security_code: str | None = None) -> list[PaperOrder]:
        sql = "SELECT * FROM sim_orders WHERE account_id=? AND status='open'"
        args: list[Any] = [account_id]
        if security_code:
            sql += " AND security_code=?"
            args.append(security_code)
        with self._connect() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def fill_seq_used(self, source_seq: str, account_id: str) -> bool:
        with self._connect() as c:
            row = c.execute(
                "SELECT 1 FROM sim_orders WHERE fill_source_seq=? AND account_id=?", (source_seq, account_id)
            ).fetchone()
        return row is not None

    @staticmethod
    def _row(r: sqlite3.Row) -> PaperOrder:
        return PaperOrder(
            account_id=r["account_id"],
            order_id=r["order_id"], side=r["side"], security_code=r["security_code"],
            security_name=r["security_name"], quantity=int(r["quantity"]),
            limit_price=Decimal(r["limit_price"]), filled_qty=int(r["filled_qty"]),
            status=r["status"], rule_version=r["rule_version"],
            accepted_at=datetime.fromisoformat(r["accepted_at"]),
            last_quote_time=(None if r["last_quote_time"] is None else datetime.fromisoformat(r["last_quote_time"])),
            avg_price=(None if r["avg_price"] is None else Decimal(r["avg_price"])),
            reject_reason=r["reject_reason"], source=r["source"], receipt_id=r["receipt_id"],
            session_date=r["session_date"],
        )

    @staticmethod
    def _vals(o: PaperOrder) -> tuple[Any, ...]:
        return (
            o.account_id, o.order_id, o.side, o.security_code, o.security_name,
            o.quantity,
            format(o.limit_price, "f"), o.filled_qty, o.status, o.rule_version,
            o.accepted_at.isoformat(),
            None if o.last_quote_time is None else o.last_quote_time.isoformat(),
            None if o.avg_price is None else format(o.avg_price, "f"),
            o.reject_reason, o.source, o.receipt_id, None, _utcnow().isoformat(),
            o.session_date,
        )


class PaperAccount:
    """Self-directed simulated order flow on one bound account."""

    def __init__(
        self,
        *,
        app: Any,                    # P12DesktopApplication
        store: PaperOrderStore,
        quotes: QuoteService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._app = app
        self._store = store
        self._quotes = quotes
        self._now = now or _utcnow
        self._account_id = app.scope.account_id

    # ---- queries ---------------------------------------------------------------
    def orders(self) -> list[dict[str, Any]]:
        return [o.to_dict() for o in self._store.list_orders(self._account_id)]

    def order(self, order_id: str) -> dict[str, Any] | None:
        o = self._store.get(order_id, self._account_id)
        return None if o is None else o.to_dict()

    def account_view(self) -> dict[str, Any]:
        """Paper account surface: ledger cash/positions + paper holds and T+1 sellable."""
        session = self._quotes.session()
        trading_day = datetime.fromisoformat(session["trading_date"]).date()

        projection = self._projection()
        positions = []
        for item in projection.positions:
            held = int(item.quantity)
            today = self._today_bought(item.security_code, trading_day)
            reserved = sum(
                o.remaining_qty
                for o in self._store.open_for(self._account_id, item.security_code)
                if o.side == "sell"
            )
            positions.append({
                "security_code": item.security_code,
                "quantity": held,
                "today_bought_qty": today,
                "sellable_qty": max(0, held - today - reserved),
                "sell_reserved_qty": reserved,
            })
        return {
            "cash": format(projection.cash, "f"),
            "cash_available": format(self._available_cash(), "f"),
            "cash_held_open_buys": format(self._open_buy_hold(), "f"),
            "positions": positions,
            "session": session,
        }

    def _projection(self):
        return self._app.paper_projection()

    def _history(self):
        return self._app.paper_history()

    def _cash(self) -> Decimal:
        return self._projection().cash

    def _open_buy_hold(self) -> Decimal:
        total = Decimal("0.00")
        for o in self._store.open_for(self._account_id):
            if o.side == "buy":
                total += (o.limit_price * o.remaining_qty).quantize(Decimal("0.01"))
        return total

    def _position_qty(self, code: str) -> int:
        return self._projection().quantity(code)

    def _today_bought(self, code: str, trading_day) -> int:
        """Quantity bought today (fact occurred within the trading day) -> not sellable.

        Reads the original reports: ``history.facts`` rows are current chains
        without an occurred_at, while ``history.reports`` keeps each posted
        fill's real timestamp and side.
        """
        qty = 0
        for report in self._history().reports:
            if getattr(report, "security_code", None) != code or getattr(report, "side", None) != "buy":
                continue
            try:
                d = datetime.fromisoformat(str(report.occurred_at)).astimezone(SHANGHAI).date()
            except Exception:
                continue
            if d >= trading_day:
                qty += int(report.quantity)
        return qty

    def _sellable(self, code: str, trading_day) -> int:
        held = self._position_qty(code)
        today = self._today_bought(code, trading_day)
        held_sellable = max(0, held - today)
        reserved = sum(
            o.remaining_qty for o in self._store.open_for(self._account_id, code) if o.side == "sell"
        )
        return max(0, held_sellable - reserved)

    def _available_cash(self) -> Decimal:
        return (self._cash() - self._open_buy_hold()).quantize(Decimal("0.01"))

    # ---- submit ------------------------------------------------------------------
    def submit(self, command: OrderCommand) -> SubmitOutcome:
        invalid = command.validate()
        if invalid is not None:
            return SubmitOutcome(command.order_id, "rejected", invalid, REJECT[invalid], 0, None, RULE_VERSION, None, None)
        existing = self._store.get(command.order_id, self._account_id)
        if existing is not None:
            # Idempotent re-submit returns the stored terminal/open state
            # verbatim - a cancelled or expired order must not echo "open"
            # while its row stays terminal (R2 same-id retry consistency).
            st = existing.status
            return SubmitOutcome(
                existing.order_id, st,
                existing.reject_reason, REJECT.get(existing.reject_reason or "", None),
                existing.filled_qty, existing.avg_price, existing.rule_version,
                existing.last_quote_time, existing.receipt_id,
            )

        session = self._quotes.session()
        trading_day = datetime.fromisoformat(session["trading_date"]).date()

        # --- plan-confirm admission ------------------------------------------------
        # An order id of ``plan:{plan_id}`` is a confirm-the-suggestion order:
        # bind it to the account's currently effective published plan and take
        # the server-side quantity / price cap / window as authoritative, then
        # run the same funding checks.  A plan that is no longer visible, has
        # closed its window, or disagrees with the submitted side/security is
        # refused before any cash is committed.
        #
        # Issue #723 E8 r5 (planning ruling, 8792 phase-1 execution mode):
        # an instance bound display-only refuses EVERY sell-side order at
        # the confirm entry - plan-bound confirms and self-directed sells
        # alike - with an advisory rejection that stores no order row, so a
        # later retry in a legal phase still resolves.  The refusal lives on
        # the bound application, not the shared PaperAccount path, so open
        # instances on other slices keep their sell flow unchanged.
        plan = None
        bound_session_date = None
        sell_closed = not getattr(self._app, "sell_execution_open", True)
        if sell_closed and command.side == "sell":
            return self._reject(command, "PLAN_NOT_EXECUTABLE", None, persist=False)
        if command.order_id.startswith("plan:"):
            plan_id = command.order_id.split(":", 1)[1]
            # Issue #723 E8 r5 (planning ruling): the bound session window
            # is judged from the package's authoritative next_session, and
            # an out-of-session confirm on a LIVE plan reports MARKET_CLOSED
            # (advisory, nothing stored) instead of a terminal row that
            # would lock the same id out of the legal window.  A plan that
            # no longer resolves still reports PLAN_NOT_FOUND first.
            get_decisions = getattr(self._app, "_current_decisions", None)
            _decisions = get_decisions() if callable(get_decisions) else None
            _next_session = getattr(_decisions, "next_session", None)
            _bound_date = (
                _next_session.isoformat() if _next_session is not None else None
            )
            if command.side == "buy" and _bound_date is not None:
                bound_open = (
                    session["market_state"] == "trading"
                    and session["trading_date"] == _bound_date
                )
                if not bound_open:
                    # The bound session is not open: an advisory refusal,
                    # never a stored row - the same id resolves again at
                    # the legal window instead of locking under a stale
                    # PLAN_NOT_FOUND (E8 r5 buy-3).
                    return self._reject(command, "MARKET_CLOSED", None, persist=False)
            resolver = getattr(self._app, "paper_current_plan", None)
            # E8 r2-3: a sell confirm re-sizes on the confirm-time mark -
            # the live quote last price this order will actually fill
            # against - never the stale package mark.  The resolver falls
            # back to the package mark when no live mark is available.
            live_mark = None
            live_quote = self._quotes.quote(command.security_code)
            if live_quote is not None and live_quote.last_price is not None:
                live_mark = live_quote.last_price
            try:
                plan = resolver(plan_id, live_mark) if callable(resolver) else None
            except TypeError:
                plan = resolver(plan_id) if callable(resolver) else None
            if plan is None:
                return self._reject(command, "PLAN_NOT_FOUND", None)
            if plan.side != command.side or plan.security_code != command.security_code:
                return self._reject(command, "ORDER_CONTEXT_MISMATCH", None)
            # Issue #723 E8 r5 (buy): the confirm-time mark may re-size the
            # plan.  When the resolved quantity differs from the client-
            # echoed figure the order is NOT written - the user must accept
            # the new quantity first.  A resubmit echoing the resolved
            # figure passes this gate and binds under the same id; until
            # then zero orders, zero fills, cash and position unchanged.
            # Only a real confirm-time re-size (resolver set confirm_qty)
            # demands the explicit user re-accept; a mismatch on an
            # otherwise unchanged plan still binds the authoritative
            # quantity the same way it always did.
            if getattr(plan, "confirm_qty", None) is not None and command.quantity != plan.delta_qty:
                return self._reject(
                    command,
                    "CONFIRM_RESIZED",
                    None,
                    persist=False,
                    resized_qty=plan.delta_qty,
                )
            # Authoritative parameters come from the live plan, not the client.
            command = OrderCommand(
                order_id=command.order_id,
                side=command.side,
                security_code=command.security_code,
                quantity=plan.delta_qty,
                limit_price=plan.price_cap,
            )
            # The bound session date was already resolved above for the
            # early out-of-session check; reuse it for the order row's
            # session_date so the matcher ties the fill to the bound day.
            bound_session_date = _bound_date
            # Issue #719 R12 / #723 (r2): typed unavailable gates for the
            # pre-open facts this plan's conditions declare apply ONLY to
            # buy-side confirms.  A sell or reduction confirm is allowed to
            # proceed even when suspension/limit/auction facts are missing -
            # the fact is never inferred, but the direction split means a
            # missing pre-open fact never silently blocks a position exit.
            if command.side == "buy":
                for fact in getattr(plan, "depends_on_preopen", ()) or ():
                    provider = getattr(self._app, "preopen_fact_for", None)
                    available = (
                        provider(plan.security_code, fact) if callable(provider) else None
                    )
                    if available is not True:
                        return self._reject(
                            command,
                            f"PREOPEN_{fact}_UNAVAILABLE",
                            None,
                        )

        quote = self._quotes.quote(command.security_code)

        # --- acceptance checks -------------------------------------------------
        if command.side == "buy":
            need = (command.limit_price * command.quantity).quantize(Decimal("0.01"))
            if self._available_cash() < need:
                return self._reject(command, "INSUFFICIENT_CASH", quote)
            # Fixed-bucket capacity: with no bucket able to take this security
            # the order is refused before it opens, never filled into a gap
            # the funding page cannot reconcile.
            capacity = getattr(self._app, "paper_bucket_capacity_for", None)
            if callable(capacity) and not capacity(command.security_code):
                return self._reject(command, "FUNDING_NO_FREE_BUCKET", quote)
        else:
            if self._sellable(command.security_code, trading_day) < command.quantity:
                reason = "SELL_NOT_ALLOWED_T1" if self._today_bought(command.security_code, trading_day) > 0 else "INSUFFICIENT_POSITION"
                return self._reject(command, reason, quote)

        # Daily price band: a limit outside the security's ±band is refused
        # up front instead of resting forever or filling at an impossible price.
        band = self._price_band(command.security_code, quote)
        if band is not None:
            lo, hi = band
            if not (lo <= command.limit_price <= hi):
                return self._reject(command, "PRICE_OUT_OF_BOUNDS", quote)

        # Supported-session admission: a resting limit order is only accepted
        # inside continuous auction.  Orders submitted after close are refused
        # with MARKET_CLOSED rather than parked into the next session.
        # Issue #723 E8 r4 (planning ruling): a plan-bound SELL confirms only
        # inside the bound next_session continuous auction, so a post-close,
        # pre-open, holiday or wrong-session confirm is refused with
        # MARKET_CLOSED - there is no after-hours sell registration in the
        # phase-1 slice.  The confirm then re-sizes on the live sellable,
        # in-flight, mark and locked budget at that instant.  A plan-bound
        # BUY keeps the pending registration from E8 r2-5 (publication lands
        # after the T close, so its confirm rests until the bound session
        # open).  A suspended or quoteless security still refuses up front.
        # A non-plan order keeps the plain session check: a resting limit
        # order is only accepted inside continuous auction.  Plan-bound
        # confirms already passed their bound-session gate above (buy) or
        # were refused outright (sell), so they never reach this line.
        if bound_session_date is None and session["market_state"] != "trading":
            return self._reject(command, "MARKET_CLOSED", quote)
        if quote.status == "halt":
            return self._reject(command, "SECURITY_SUSPENDED", quote)
        if quote.status == "unavailable":
            return self._reject(command, "QUOTE_UNAVAILABLE", quote)

        order = PaperOrder(
            account_id=self._account_id,
            order_id=command.order_id, side=command.side,
            security_code=command.security_code,
            security_name=quote.security_name if quote.security_name != command.security_code else command.security_code,
            quantity=command.quantity, limit_price=command.limit_price,
            filled_qty=0, status="open", rule_version=RULE_VERSION,
            accepted_at=self._now(), last_quote_time=quote.quote_time,
            avg_price=None, reject_reason=None,
            source=SIM_MATCHED_SOURCE, receipt_id=None,
            session_date=bound_session_date,
        )
        # Register the accepted order in the shared ledger first so a later
        # simulated fill posts against a real order row and the records view
        # can trace it back to this request.  The observation is idempotent:
        # a re-registered id replays its receipt instead of double-posting.
        obs_result = self._register_order(order)
        if obs_result is not None:
            object.__setattr__(order, "receipt_id", obs_result)
        # Persist the accepted (still-open) order first so the matcher's
        # compare-and-set has a stored row to flip.  A racing poll/match that
        # fills it before this insert commits is rejected by the CAS guard.
        self._store.insert(order)
        # Immediate match attempt against the live quote (accepted-at or later).
        outcome = self._try_fill(order, quote, session)
        if outcome is None:
            # Not marketable / not tradable now: persists as an open order.
            return SubmitOutcome(command.order_id, "open", "BELOW_LIMIT", REJECT["BELOW_LIMIT"], 0, None, RULE_VERSION, quote.quote_time, None)
        return outcome

    def _reject(
        self,
        command: OrderCommand,
        reason: str,
        quote: Quote | None,
        *,
        persist: bool = True,
        resized_qty: int | None = None,
    ) -> SubmitOutcome:
        order = PaperOrder(
            account_id=self._account_id,
            order_id=command.order_id, side=command.side,
            security_code=command.security_code,
            security_name=(quote.security_name if quote else command.security_code),
            quantity=command.quantity, limit_price=command.limit_price,
            filled_qty=0, status="rejected", rule_version=RULE_VERSION,
            accepted_at=self._now(),
            last_quote_time=(quote.quote_time if quote else None),
            avg_price=None, reject_reason=reason,
            source=SIM_MATCHED_SOURCE, receipt_id=None,
        )
        # Issue #723 E8 r4: an out-of-session plan-sell refusal is advisory,
        # never a terminal order row - persisting it would let the idempotent
        # resubmit path replay "rejected" forever and lock the same plan's
        # confirm out of the bound session open.  Other rejections keep
        # their stored row so a same-id retry reports its original refusal.
        if persist:
            self._store.insert(order)
        return SubmitOutcome(command.order_id, "rejected", reason, REJECT[reason], 0, None, RULE_VERSION,
                             (quote.quote_time if quote else None), None, resized_qty)

    def _register_order(self, order: PaperOrder) -> str | None:
        """Post the order's opening observation to the shared ledger.

        Returns the ledger receipt id when the observation posts (or replays),
        None when the ledger refuses it - the order then still rests in the
        simulated book and a fill remains blocked from posting (the fill
        would reference an unknown order, so ``_try_fill`` keeps it open).
        """
        observation = OrderObservation(
            observation_id=f"{order.order_id}:accepted",
            order_id=order.order_id,
            status="in_progress",
            observed_at=order.accepted_at,
            source_id=RULE_VERSION,
            source_sequence=f"{order.order_id}:accepted",
            order_revision=1,
        )
        try:
            result = self._app.paper_submit_order_observation(
                observation,
                accepted_total_qty=order.quantity,
                side=order.side,
                security_code=order.security_code,
                price_bound=order.limit_price,
                estimated_fee_upper=Decimal("0.00"),
            )
        except FactError:
            return None
        if str(result.status) in ("POSTED", "DUPLICATE"):
            return result.receipt_id
        return None

    def _observe_terminal(self, order: PaperOrder, *, filled: int, cancelled: int) -> None:
        """Record the order's terminal state in the shared ledger, best effort.

        A terminal observation must satisfy final_cumulative_fill_qty +
        cancelled_qty == accepted_total_qty.  Failure leaves the earlier
        in-progress record intact; the simulated book remains the order view.
        """
        observation = OrderObservation(
            observation_id=f"{order.order_id}:terminal",
            order_id=order.order_id,
            status="terminal",
            observed_at=self._now(),
            source_id=RULE_VERSION,
            source_sequence=f"{order.order_id}:terminal:{filled}:{cancelled}",
            final_cumulative_fill_qty=filled,
            cancelled_qty=cancelled,
            order_revision=2,
        )
        try:
            self._app.paper_submit_order_observation(
                observation,
                accepted_total_qty=order.quantity,
                side=order.side,
                security_code=order.security_code,
                price_bound=order.limit_price,
                estimated_fee_upper=Decimal("0.00"),
            )
        except FactError:
            return

    # ---- matching ------------------------------------------------------------------
    def _try_fill(self, order: PaperOrder, quote: Quote, session: dict[str, Any]) -> SubmitOutcome | None:
        """Fill ``order`` against ``quote`` if the rule makes it marketable."""
        if order.status != "open":
            return None
        if session["market_state"] != "trading":
            return None
        # Issue #723 E8 r2-5: a plan-bound order releases only inside the
        # confirming package own next_session - the quote clock session,
        # not the order accept date, gates the first fill, and a quote
        # stamped before that bound session open never releases it even
        # while the generic session reads trading.
        if order.session_date is not None:
            if order.session_date != session["trading_date"]:
                return None
            qt = quote.quote_time.astimezone(SHANGHAI) if quote.quote_time is not None else None
            if qt is not None and qt.isoformat() < order.session_date + "T" + SESSION_OPEN_ISO:
                return None
        if quote.status != "live" or quote.last_price is None or quote.quote_time is None:
            return None
        # Only quotes at or after the order's accept may fill it.
        if quote.quote_time < order.accepted_at.astimezone(SHANGHAI):
            return None
        marketable = (
            (order.side == "buy" and quote.last_price <= order.limit_price)
            or (order.side == "sell" and quote.last_price >= order.limit_price)
        )
        if not marketable:
            return None
        # Fill at the observed last price, which the limit already bounds:
        # the buyer never pays above their cap and the seller never fills
        # below their floor, while a marketable order does not overpay.
        fill_price = quote.last_price
        source_seq = f"{order.order_id}:fill:{quote.quote_time.isoformat()}"
        # A confirm-the-plan order is submitted as ``plan:{plan_id}`` so the
        # resulting fill links back to the published plan that authorized it.
        suggestion_id = (
            order.order_id.split(":", 1)[1]
            if order.order_id.startswith("plan:")
            else None
        )
        report = ExecutionReport(
            report_id=f"sim-fill-{uuid.uuid4().hex[:12]}",
            security_code=order.security_code,
            side=order.side,
            quantity=order.remaining_qty,
            price=fill_price,
            confirmed_fee=Decimal("0.00"),
            # The ledger refuses facts earlier than its takeover snapshot
            # (BASELINE_COVERAGE_UNKNOWN): the fill is dated by the server's
            # match instant; the triggering source tick stays traceable
            # through source_sequence and matched_quote_time.
            occurred_at=self._now(),
            source_id=RULE_VERSION,
            source_sequence=source_seq,
            intended_quantity=order.remaining_qty,
            intended_price_cap=order.limit_price,
            order_id=order.order_id,
            suggestion_id=suggestion_id,
        )
        # Only mark the order filled once the shared ledger actually posts the
        # fact.  A FactError (insufficient cash, unknown order, a duplicate
        # fact from a racing matcher) or a non-posted result does NOT write
        # back: another thread may already have committed the fill, and
        # re-writing this stale in-memory "open" copy would revert it (R2/N1).
        # Instead re-read the stored row and trust the ledger/book state.
        try:
            result = self._app.paper_submit_report(report)
        except FactError:
            return self._resolve_after_conflict(order, quote)
        if str(result.status) not in ("POSTED", "DUPLICATE"):
            return self._resolve_after_conflict(order, quote)
        object.__setattr__(order, "filled_qty", order.quantity)
        object.__setattr__(order, "status", "filled")
        object.__setattr__(order, "avg_price", fill_price)
        object.__setattr__(order, "last_quote_time", quote.quote_time)
        object.__setattr__(order, "receipt_id", result.receipt_id)
        # Compare-and-set: only flip while the row is still open.  When a
        # racing matcher already committed a terminal state the CAS fails and
        # we fall back to the stored row rather than overwriting it.
        if not self._store.update_if_status(
            order, expected_status="open", fill_source_seq=source_seq
        ):
            return self._resolve_after_conflict(order, quote)
        self._observe_terminal(order, filled=order.quantity, cancelled=0)
        return SubmitOutcome(
            order.order_id, "filled", None, None, order.filled_qty,
            fill_price, order.rule_version, quote.quote_time, result.receipt_id,
        )

    def _resolve_after_conflict(
        self, order: PaperOrder, quote: Quote
    ) -> SubmitOutcome | None:
        """Return the order's true state after a racing fill/duplicate.

        The shared ledger or a concurrent matcher already decided this order.
        Re-read the stored row so the caller sees the committed state (a
        filled order reports filled) instead of a stale "open" echo.
        """
        stored = self._store.get(order.order_id, self._account_id)
        if stored is None:
            return None
        if stored.status == "filled":
            return SubmitOutcome(
                stored.order_id, "filled", None, None, stored.filled_qty,
                stored.avg_price, stored.rule_version, stored.last_quote_time,
                stored.receipt_id,
            )
        return None

    @staticmethod
    def _price_band(
        security_code: str, quote: Quote | None
    ) -> tuple[Decimal, Decimal] | None:
        """Daily ±price band for the security from its previous close.

        Source: Shanghai Stock Exchange Trading Rules (2026 revision), effective
        2026-07-06.  Main-board A-shares (60xxxx/00xxxx) trade inside ±10%;
        STAR (68xxxx) and ChiNext (30xxxx) inside ±20%; BSE
        (4xxxxx/8xxxxx/920xxx) ±30%.  Under that revision main-board ST/*ST
        names trade inside ±10% (raised from ±5%); the base band already
        yields that, so the calculation is correct for them too.  The ST name
        flag is not part of the quote contract here, so the audit note records
        the limitation.

        Boundary: this is a coarse pre-trade check for the paper-trading desk,
        not the authoritative daily limit price.  The governed up/down limit
        price is supplied by the P10 named source (issue #727); P12 never
        derives the official limit from a board/ST flag or a band percentage.
        Returns None when previous_close is missing.
        """
        if quote is None or quote.previous_close is None or quote.previous_close <= 0:
            return None
        if security_code.startswith(("68", "30")):
            pct = Decimal("0.20")
        elif security_code.startswith(("4", "8", "920")):
            pct = Decimal("0.30")
        else:
            pct = Decimal("0.10")
        base = quote.previous_close
        lo = (base * (Decimal("1") - pct)).quantize(Decimal("0.01"))
        hi = (base * (Decimal("1") + pct)).quantize(Decimal("0.01"))
        return lo, hi

    def _expire_stale_orders(self, session: dict[str, Any]) -> int:
        """Day-close open orders accepted before the current trading day.

        A resting limit order is a same-day order: it does not carry into the
        next session.  Runs on every poll (read path and the background
        matcher), so an unfilled order placed before the close is released
        even when nobody opens the page.
        """
        trading_day = datetime.fromisoformat(session["trading_date"]).date()
        day_done = session["market_state"] in ("call_auction_close", "closed", "holiday")
        n = 0
        for order in self._store.open_for(self._account_id):
            # Issue #723 E8 r2-5/6: a plan-bound order rides the confirming
            # package next_session, not the generic accept-day close.  It
            # outlives a closed accept day, releases inside the bound session
            # open, and only terminates once that session continuous auction
            # has finished - the terminal observation then releases the held
            # quantity for the next-day continuation.
            if order.session_date is not None:
                bound = datetime.fromisoformat(order.session_date).date()
                if not (trading_day >= bound and day_done):
                    continue
            else:
                accepted_day = order.accepted_at.astimezone(SHANGHAI).date()
                # Cross-day orders always expire; same-day orders expire once
                # the continuous session has finished (lunch keeps them resting).
                if accepted_day >= trading_day and not day_done:
                    continue
            object.__setattr__(order, "status", "expired")
            if not self._store.update_if_status(order, expected_status="open"):
                continue
            self._observe_terminal(order, filled=order.filled_qty, cancelled=order.remaining_qty)
            n += 1
        return n

    def poll(self) -> dict[str, Any]:
        """Re-quote every open order and fill any that became marketable.

        Called on each read path (order list / workbench) and by an explicit
        tick; a snapshot never double-fills because fills persist.
        """
        session = self._quotes.session()
        self._expire_stale_orders(session)
        results: list[dict[str, Any]] = []
        if session["market_state"] == "trading":
            for order in self._store.open_for(self._account_id):
                quote = self._quotes.quote(order.security_code)
                outcome = self._try_fill(order, quote, session)
                if outcome is not None:
                    results.append(outcome.to_dict())
        return {"filled": results, "session": session}

    # ---- cancel / expire -------------------------------------------------------------
    def cancel(self, order_id: str) -> dict[str, Any]:
        order = self._store.get(order_id, self._account_id)
        if order is None:
            raise FactError("PLAN_NOT_FOUND", "simulated order not found")
        if order.status != "open":
            return {**order.to_dict(), "cancelled_qty": 0, "idempotent": True}
        cancelled = order.remaining_qty
        object.__setattr__(order, "status", "cancelled")
        # Compare-and-set: a cancel only lands while the row is still open.
        # If a racing matcher filled it first, the CAS fails and the stored
        # terminal state is returned instead of reverting it to "cancelled".
        if not self._store.update_if_status(order, expected_status="open"):
            stored = self._store.get(order_id, self._account_id)
            if stored is not None:
                return {**stored.to_dict(), "cancelled_qty": 0, "idempotent": True}
            raise FactError("PLAN_NOT_FOUND", "simulated order not found")
        self._observe_terminal(order, filled=order.filled_qty, cancelled=cancelled)
        return {**order.to_dict(), "cancelled_qty": cancelled, "idempotent": False}

    def expire_day_orders(self) -> int:
        """Close every open order once the continuous session has ended."""
        session = self._quotes.session()
        if session["market_state"] in ("trading", "lunch_break", "call_auction_close"):
            return 0
        n = 0
        bound_day = datetime.fromisoformat(session["trading_date"]).date()
        for order in self._store.open_for(self._account_id):
            # A plan-bound order ends only when its bound session ends - it
            # is not closed by the generic accept-day close (E8 r2-5).
            bound = (
                datetime.fromisoformat(order.session_date).date()
                if order.session_date else None
            )
            if bound is not None and bound_day < bound:
                continue
            object.__setattr__(order, "status", "expired")
            if not self._store.update_if_status(order, expected_status="open"):
                continue
            self._observe_terminal(order, filled=order.filled_qty, cancelled=order.remaining_qty)
            n += 1
        return n
