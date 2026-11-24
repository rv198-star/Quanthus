"""E6 pre-open check for the P12 paper slice (#723).

Two deliverables in one module:

* ``stock_pool_snapshot`` - the named pre-open stock pool written before 09:00
  each trading day.  It is the union of the day's published buy candidates and
  every simulated account's held positions, each tagged with its source
  (package id or the ledger snapshot digest).  Both the P12 pre-open check and
  P10 #727 field verification read this one snapshot so neither side takes a
  separate pool.

* ``run_check`` - the 09:00-09:25 Asia/Shanghai verification pass.  It reads
  the real-time quote surface and the account and applies only the failure
  conditions already carried inside the published package; it never emits a
  new signal, new security, new threshold or a relaxed veto.  Its verdict is
  recorded into the run receipt.

Fact supply contract (P10 #727 + the batch-A reservation): T+1
suspension/resumption, native limit prices and the opening auction have no
named supply yet, so each keeps a typed ``unavailable`` state and reason.  The
checker never substitutes a zero-volume bar for suspension, a ``last`` price
for the auction print, or a previous/derived value for a native limit price -
an unresolved fact stays ``unavailable`` and only the buy confirmation that
depends on it is gated (sell/reduce continues with an alert, per the v2
machine-fact contract).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("Asia/Shanghai")

SNAPSHOT_SCHEMA = "quanthus.p12.preopen-pool.v1"
CHECK_SCHEMA = "quanthus.p12.preopen-check.v1"

# Canonical P12 pre-open fact names (the PREOPEN_* markers strip this prefix).
FACT_SUSPENSION = "SUSPENSION"
FACT_LIMIT_PRICE = "LIMIT_PRICE"
FACT_AUCTION = "AUCTION"
PREOPEN_FACTS = (FACT_SUSPENSION, FACT_LIMIT_PRICE, FACT_AUCTION)

# The open call auction is 09:15-09:25; at 09:00 it cannot have printed yet.
AUCTION_OPEN = dtime(9, 15)
AUCTION_CLOSE = dtime(9, 25)
CHECK_OPEN = dtime(9, 0)
CHECK_CLOSE = dtime(9, 25)

_ROUND_LOT = 100


def _iso(value):
    return None if value is None else value.isoformat()


def _decimal_text(value):
    return None if value is None else format(value, "f")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Stock pool snapshot
# ---------------------------------------------------------------------------
def stock_pool_snapshot(*, package, positions: tuple, as_of: datetime) -> dict:
    """Build the named pre-open pool for one account.

    ``package`` is the day's :class:`PublishedPackage` (``None`` when no
    package is visible); ``positions`` is the live account position tuple.  A
    security appearing both as a buy candidate and a held position keeps every
    applicable source.
    """
    securities: dict[str, dict] = {}

    def _entry(code: str, name) -> dict:
        return securities.setdefault(
            code, {"security_code": code, "security_name": name, "sources": []}
        )

    package_ref = None
    if package is not None:
        package_ref = getattr(package, "package_id", None) or getattr(
            package, "digest", None
        )
        for plan in package.plans:
            if plan.side != "buy" or plan.delta_qty <= 0:
                continue
            entry = _entry(plan.security_code, None)
            entry["sources"].append(
                {"kind": "buy_candidate", "ref": package_ref, "plan_id": plan.plan_id}
            )
        for plan in package.plans:
            if plan.side == "buy":
                continue
            entry = _entry(plan.security_code, None)
            entry["sources"].append(
                {"kind": "exit_intent", "ref": package_ref, "plan_id": plan.plan_id}
            )

    for pos in positions:
        qty = getattr(pos, "quantity", 0) or 0
        if qty <= 0:
            continue
        code = getattr(pos, "security_code", "")
        entry = _entry(code, getattr(pos, "security_name", None))
        entry["sources"].append({"kind": "held_position", "ref": "ledger-snapshot"})
        entry["quantity"] = qty

    rows = [securities[code] for code in sorted(securities)]
    for row in rows:
        seen = []
        for src in row["sources"]:
            if src not in seen:
                seen.append(src)
        row["sources"] = seen

    body = {
        "schema_version": SNAPSHOT_SCHEMA,
        "trading_date": as_of.date().isoformat(),
        "generated_at": as_of.isoformat(),
        "securities": rows,
    }
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    body["sha256"] = _sha256_bytes(payload)
    return body


def write_stock_pool_snapshot(state_dir: Path, snapshot: dict) -> Path:
    """Persist the snapshot at its fixed dated path; returns the path."""
    path = Path(state_dir) / "preopen-pool" / (str(snapshot["trading_date"]) + ".json")
    _write_atomic(path, json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2))
    return path


def merge_stock_pool_snapshots(snapshots: tuple, as_of: datetime) -> dict:
    """Merge every simulated account's pool into the day's shared snapshot.

    Each account contributes the same buy-candidate set (one published
    package) plus its own held positions.  The single named pool is the
    union of all securities, so a security held only by account B still
    appears after account A's write.  A security sourced from several
    accounts keeps every applicable source and the per-account quantity.
    """
    securities: dict[str, dict] = {}
    trading_date = None
    for snap in snapshots:
        if snap is None:
            continue
        trading_date = trading_date or snap.get("trading_date")
        for row in snap.get("securities", ()):
            code = row.get("security_code", "")
            entry = securities.setdefault(
                code,
                {
                    "security_code": code,
                    "security_name": row.get("security_name"),
                    "sources": [],
                    "quantities": {},
                },
            )
            if entry["security_name"] is None:
                entry["security_name"] = row.get("security_name")
            for src in row.get("sources", ()):
                if src not in entry["sources"]:
                    entry["sources"].append(src)
            qty = row.get("quantity")
            if qty:
                src = row.get("account_ref") or "ledger-snapshot"
                entry["quantities"][src] = qty
                entry["quantity"] = entry.get("quantity", 0) + qty

    rows = []
    for code in sorted(securities):
        row = securities[code]
        # Total across accounts when more than one held the security.
        if row.get("quantities"):
            row["quantity"] = sum(row["quantities"].values())
        rows.append(row)

    body = {
        "schema_version": SNAPSHOT_SCHEMA,
        "trading_date": trading_date or as_of.date().isoformat(),
        "generated_at": as_of.isoformat(),
        "securities": rows,
    }
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    body["sha256"] = _sha256_bytes(payload)
    return body


class PoolError(Exception):
    """A named pool was missing, stale or corrupted at consumption."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_stock_pool_snapshot(state_dir: Path, trading_date) -> dict:
    """Read the day's named pool and verify its date and embedded SHA.

    The pre-open check and P10 #727 consume the *same* snapshot file.  A
    missing, wrong-day or hash-mismatched pool is a typed error - the
    caller keeps the affected facts unavailable rather than treating a
    broken pool as verified input.  Returns the verified body.
    """
    day = trading_date if hasattr(trading_date, "isoformat") else None
    day = day.isoformat() if day is not None else str(trading_date)
    path = Path(state_dir) / "preopen-pool" / (day + ".json")
    if not path.exists():
        raise PoolError("POOL_MISSING", f"no named pre-open pool for {day}")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PoolError("POOL_CORRUPT", f"unreadable pre-open pool: {exc}") from exc
    sig = body.pop("sha256", None)
    if sig is None:
        raise PoolError("POOL_CORRUPT", "pre-open pool has no embedded sha256")
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if _sha256_bytes(payload) != sig:
        raise PoolError("POOL_CORRUPT", "pre-open pool sha256 mismatch")
    if body.get("trading_date") != day:
        raise PoolError(
            "POOL_STALE",
            f"pre-open pool is for {body.get('trading_date')}, not {day}",
        )
    body["sha256"] = sig
    return body


# ---------------------------------------------------------------------------
# Pre-open fact resolution
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FactVerdict:
    """One security's verdict on one named pre-open fact."""

    security_code: str
    fact: str
    status: str          # verified | unavailable
    reason: str | None
    source: str
    observed_at: str | None
    received_at: str | None

    def to_dict(self) -> dict:
        return {
            "security_code": self.security_code,
            "fact": self.fact,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "observed_at": self.observed_at,
            "received_at": self.received_at,
        }


def _unavailable(code, fact, reason, source, now) -> FactVerdict:
    return FactVerdict(
        security_code=code,
        fact=fact,
        status="unavailable",
        reason=reason,
        source=source,
        observed_at=None,
        received_at=now.isoformat(),
    )


def resolve_suspension(quote, code, now) -> FactVerdict:
    """Suspension/resumption is a named T+1 fact with no supply yet (#727).

    A zero-volume quote or a halted tape is NOT a formal suspension notice,
    so the fact stays ``unavailable`` until a #727-named supply is bound; the
    checker never infers it from the last price or a stale feed.
    """
    return _unavailable(
        code,
        FACT_SUSPENSION,
        "T+1 suspension/resumption has no named supply (P10 #727 pending)",
        "none",
        now,
    )


def resolve_limit_price(quote, code, now) -> FactVerdict:
    """Native limit price is NULL in core and never derived here (#727)."""
    return _unavailable(
        code,
        FACT_LIMIT_PRICE,
        "native up/down limit price has no named supply (P10 #727 pending)",
        "none",
        now,
    )


def resolve_auction(quote, code, now) -> FactVerdict:
    """Opening-auction print.

    A genuine ``open`` price observed inside 09:15-09:25 is admitted as the
    auction print; before the window or with no usable print the fact is
    ``unavailable``.  An ordinary ``last`` tick is never relabelled as the
    auction price.
    """
    if now.time() < AUCTION_OPEN:
        return _unavailable(
            code,
            FACT_AUCTION,
            "open call auction has not printed yet (window 09:15-09:25)",
            "none",
            now,
        )
    if quote is None or getattr(quote, "open", None) is None:
        return _unavailable(
            code,
            FACT_AUCTION,
            "no usable opening-auction print from the quote surface",
            "none",
            now,
        )
    # Identity and session: the snapshot must belong to the queried
    # security and to the same T+1 trading day as the check instant.
    if getattr(quote, "security_code", code) != code:
        return _unavailable(
            code, FACT_AUCTION, "quote belongs to a different security",
            getattr(quote, "source", "tencent") or "tencent", now,
        )
    quote_status = getattr(quote, "status", "live")
    if quote_status in ("unavailable", "halt", "closed") or getattr(
        quote, "stale", False
    ):
        return _unavailable(
            code, FACT_AUCTION,
            f"quote surface is {quote_status}; no usable auction print",
            getattr(quote, "source", "tencent") or "tencent", now,
        )
    # Point-in-time: a print must already be observed AND received by the
    # check instant; a later tick is never back-read into an earlier check.
    quote_time = getattr(quote, "quote_time", None)
    received_at = getattr(quote, "received_at", None)
    if quote_time is not None:
        qt = quote_time.astimezone(EXCHANGE_TZ)
        if qt > now:
            return _unavailable(
                code, FACT_AUCTION, "auction print observed after the check instant",
                getattr(quote, "source", "tencent") or "tencent", now,
            )
        if qt.date() != now.date():
            return _unavailable(
                code, FACT_AUCTION, "auction print belongs to a different session",
                getattr(quote, "source", "tencent") or "tencent", now,
            )
    if received_at is not None:
        ra = received_at.astimezone(EXCHANGE_TZ)
        if ra > now:
            return _unavailable(
                code, FACT_AUCTION, "auction print received after the check instant",
                getattr(quote, "source", "tencent") or "tencent", now,
            )
        if ra.date() != now.date():
            return _unavailable(
                code, FACT_AUCTION, "auction print received on a different session",
                getattr(quote, "source", "tencent") or "tencent", now,
            )
    return FactVerdict(
        security_code=code,
        fact=FACT_AUCTION,
        status="verified",
        reason=None,
        source=getattr(quote, "source", "tencent") or "tencent",
        observed_at=_iso(quote_time),
        received_at=_iso(received_at) or now.isoformat(),
    )


def resolve_fact(quote, code, fact, now) -> FactVerdict:
    if fact == FACT_SUSPENSION:
        return resolve_suspension(quote, code, now)
    if fact == FACT_LIMIT_PRICE:
        return resolve_limit_price(quote, code, now)
    if fact == FACT_AUCTION:
        return resolve_auction(quote, code, now)
    return _unavailable(code, fact, f"unknown pre-open fact {fact!r}", "none", now)


# ---------------------------------------------------------------------------
# Pre-open check run
# ---------------------------------------------------------------------------
def run_check(
    *,
    package,
    projection,
    history,
    quotes,
    now: datetime,
    sellable_for=None,
    account_mode: str = "real",
    pool_dir=None,
) -> dict:
    """Run the 09:00-09:25 verification over the day's package and account.

    Only the failure conditions already inside the package are applied.  For
    each plan the checker resolves its declared pre-open facts, re-derives the
    still-executable quantity against the *current* position and cash, and
    keeps sells/reduce unaffected by a missing fact (they proceed with an
    alert).  The verdict never mints a new signal.
    """
    now = now.astimezone(EXCHANGE_TZ)
    in_window = CHECK_OPEN <= now.time() <= CHECK_CLOSE
    cash = getattr(projection, "cash", None)
    _sim = account_mode in ("synthetic", "simulation")
    results = []
    blocked = 0
    alerted = 0

    # E6-01: the check consumes the day's named pool by the same path, date
    # and SHA the writer produced.  A missing, stale or corrupt pool is a
    # typed defect - every quote-dependent fact then stays unverified, so a
    # buy depending on it cannot silently pass on a broken pool.
    pool = None
    pool_error = None
    if pool_dir is not None:
        try:
            pool = load_stock_pool_snapshot(pool_dir, now.date())
        except PoolError as exc:
            pool_error = exc.code
    pool_members = (
        {row.get("security_code") for row in pool.get("securities", ())}
        if pool is not None
        else None
    )

    def _sellable(code: str) -> int:
        if callable(sellable_for):
            return sellable_for(code)
        return getattr(projection, "quantity", lambda c: 0)(code)

    plans = tuple(getattr(package, "plans", ()) or ()) if package is not None else ()
    for plan in plans:
        code = plan.security_code
        quote = None
        try:
            quote = quotes.quote(code)
        except Exception:  # noqa: BLE001 - a failed channel is recorded, not fatal
            quote = None

        depends = tuple(getattr(plan, "depends_on_preopen", ()) or ())
        facts = [resolve_fact(quote, code, fact, now).to_dict() for fact in depends]

        # A verified pool confines the check to the named securities; a
        # pool that is missing/stale/corrupt or that does not list this
        # security keeps every declared fact unverified (never inferred).
        if pool_error is not None:
            for f in facts:
                if f["status"] == "verified":
                    f["status"] = "unavailable"
                    f["reason"] = f"named pre-open pool defect: {pool_error}"
                    f["observed_at"] = None
        elif pool is not None and code not in pool_members:
            for f in facts:
                if f["status"] == "verified":
                    f["status"] = "unavailable"
                    f["reason"] = "security absent from the named pre-open pool"
                    f["observed_at"] = None
        missing = [f["fact"] for f in facts if f["status"] != "verified"]

        recheck_qty = None
        executable = True
        reasons: list[str] = []
        if pool_error is not None:
            reasons.append(pool_error.lower())
        elif pool is not None and code not in pool_members:
            reasons.append("pool_not_listed")
        if plan.side == "buy":
            current = projection.quantity(code)
            remaining = plan.target_qty - current
            # E6-04: the buy need is the bounded cost INCLUDING the plan's
            # declared fee provision, and the spendable cash is what is left
            # after every still-open buy order's reserved amount.  A check
            # that ignores either lets an account confirm above its real
            # headroom (the audit's bare-cash counterexample).
            fee = getattr(plan, "estimated_fee", None) or Decimal("0.00")
            need = (plan.price_cap * remaining + fee).quantize(Decimal("0.01"))
            reserved = sum(
                (
                    getattr(o, "hold_amount", None)
                    if getattr(o, "hold_amount", None) is not None
                    else getattr(o, "hold_cash", Decimal("0.00"))
                )
                for o in getattr(history, "orders", ())
                if getattr(o, "side", "buy") == "buy"
            ) or Decimal("0.00")
            spendable = None if cash is None else (cash - reserved).quantize(
                Decimal("0.01")
            )
            if remaining <= 0:
                executable = False
                reasons.append("already_at_target")
            elif spendable is not None and spendable < need:
                executable = False
                # The still-affordable quantity is what the free cash buys
                # after the fee provision, rounded down to a whole lot.
                headroom = spendable - fee
                affordable = (
                    int(headroom / plan.price_cap) // _ROUND_LOT * _ROUND_LOT
                    if headroom > 0
                    else 0
                )
                recheck_qty = max(0, affordable)
                reasons.append("cash_reduced")
            if missing:
                # P11 v0.2 sec.5: a simulated buy may still confirm as a
                # pending intent under the typed unavailable alert; a real
                # account fails closed on the same missing fact.
                if _sim:
                    for fact in missing:
                        reasons.append(f"PREOPEN_{fact}_UNAVAILABLE")
                    reasons.append("preopen_unverified_alert")
                else:
                    executable = False
                    for fact in missing:
                        reasons.append(f"PREOPEN_{fact}_UNAVAILABLE")
        else:
            sellable = _sellable(code)
            if sellable <= 0:
                executable = False
                reasons.append("nothing_sellable")
            elif plan.delta_qty > sellable:
                recheck_qty = sellable
                reasons.append("position_reduced")
            if missing:
                alerted += 1
                reasons.append("preopen_unverified_alert")

        # The auction comparison uses only a PIT-qualified print: the same
        # verdict gate that admits an observed-and-received in-session open.
        # A future, stale, wrong-security or unqualified print never feeds
        # the price-cap failure (it stays a typed unavailable fact instead).
        auction_verdict = resolve_fact(quote, code, FACT_AUCTION, now)
        auction_price = (
            getattr(quote, "open", None)
            if (quote is not None and auction_verdict.status == "verified")
            else None
        )
        if (
            plan.side == "buy"
            and auction_price is not None
            and auction_price > plan.price_cap
        ):
            executable = False
            reasons.append("auction_above_price_cap")

        if not executable:
            blocked += 1
        results.append(
            {
                "plan_id": plan.plan_id,
                "security_code": code,
                "side": plan.side,
                "price_cap": _decimal_text(plan.price_cap),
                "depends_on_preopen": list(depends),
                "facts": facts,
                "executable": executable,
                "recheck_quantity": recheck_qty,
                "reason_codes": reasons,
            }
        )

    receipt = {
        "schema_version": CHECK_SCHEMA,
        "check": "preopen",
        "trading_date": now.date().isoformat(),
        "checked_at": now.isoformat(),
        "in_window": in_window,
        "window": str(CHECK_OPEN) + "-" + str(CHECK_CLOSE) + " " + str(EXCHANGE_TZ),
        "package_id": getattr(package, "package_id", None),
        "account_revision": getattr(projection, "revision", None),
        "cash": _decimal_text(cash),
        "pool_sha256": pool.get("sha256") if pool is not None else None,
        "pool_status": (
            "verified"
            if pool is not None
            else ("unavailable:" + str(pool_error) if pool_error else "unbound")
        ),
        "plans": results,
        "summary": {
            "plans_checked": len(results),
            "blocked": blocked,
            "sell_alerts": alerted,
        },
        "note": (
            "verify-only pass over the day's package; no new signal is produced. "
            "Missing T+1 suspension / native-limit / auction facts stay typed "
            "unavailable until P10 #727 names a supply."
        ),
    }
    payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
    receipt["sha256"] = _sha256_bytes(payload)
    return receipt


def attach_check_to_receipt(receipt: dict | None, check: dict) -> dict:
    """Embed the pre-open check into a run receipt without replacing it."""
    base = dict(receipt or {})
    base.setdefault("schema_version", "quanthus.p12.daily-run.v1")
    base["preopen_check"] = check
    return base


def write_check_receipt(state_dir: Path, check: dict) -> Path:
    """Persist a pre-open check receipt beside the named pool.

    E6-06: the verify pass is durable evidence - the package, account
    revision, policy mode, pool SHA, per-plan facts/alerts/recheck quantity
    and refusal reasons are recoverable across a restart, keyed by trading
    day and check SHA so a later audit reads the identical record."""
    path = (
        Path(state_dir)
        / "preopen-check"
        / (str(check["trading_date"]) + ".json")
    )
    _write_atomic(path, json.dumps(check, ensure_ascii=False, sort_keys=True, indent=2))
    return path
