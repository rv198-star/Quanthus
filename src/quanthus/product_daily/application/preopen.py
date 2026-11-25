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

# Purpose qualification for the opening auction: the ordinary intraday
# quote surface (production Quote always stamps a ``market_state`` session
# value) carries the day's open price as a regular post-hoc tape field, not
# as an auction-purpose record visible inside 09:15-09:25.  An ordinary
# snapshot therefore never qualifies as the auction print no matter which
# clocks it carries; only a record shape that does not declare an ordinary
# session state (the pre-qualified auction record, e.g. a future #727-named
# supply) can be admitted, and only after the same-security/same-session/
# in-window/point-in-time gates in resolve_auction.

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
    expected_day = as_of.date().isoformat()
    trading_date = None
    rejected: list[dict] = []
    for snap in snapshots:
        if snap is None:
            continue
        snap_day = snap.get("trading_date")
        if snap_day != expected_day:
            # An account pool written for a DIFFERENT trading day never
            # merges into today's named pool: its held quantities would
            # silently re-enter the current session under a valid SHA.
            # The wrong-day account is recorded as a typed rejection and
            # contributes nothing - per-account quantities and ledger
            # source refs stay separated by day.
            rejected.append({
                'account_ref': snap.get('account_ref'),
                'trading_date': snap_day,
                'error': 'POOL_WRONG_DAY',
            })
            continue
        trading_date = trading_date or snap_day
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
    if rejected:
        body["rejected_accounts"] = rejected
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
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PoolError("POOL_CORRUPT", f"unreadable pre-open pool: {exc}") from exc
    # A syntactically valid JSON value that is not an object (``[]``, a
    # scalar, ``null``) is still a corrupt named pool - refuse it with the
    # same typed defect instead of crashing on the dict access below.
    if not isinstance(body, dict):
        raise PoolError(
            "POOL_CORRUPT", "pre-open pool is not a JSON object"
        )
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


def _unavailable(code, fact, reason, source, now, quote=None) -> FactVerdict:
    # E6-02 source-clock fidelity: an unavailable verdict keeps the
    # source-side observation/receipt times at their ORIGINAL values.
    # A missing source clock stays null - the check instant belongs only
    # to ``checked_at`` and is never copied into the fact's source fields,
    # so a late/early/missing receipt is auditable as it actually was.
    observed = getattr(quote, 'quote_time', None) if quote is not None else None
    received = getattr(quote, 'received_at', None) if quote is not None else None
    return FactVerdict(
        security_code=code,
        fact=fact,
        status="unavailable",
        reason=reason,
        source=source,
        observed_at=_iso(observed),
        received_at=_iso(received),
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
            now, quote=quote,
        )
    if quote is None or getattr(quote, "open", None) is None:
        return _unavailable(
            code,
            FACT_AUCTION,
            "no usable opening-auction print from the quote surface",
            "none",
            now, quote=quote,
        )
    # Identity and session: the snapshot must belong to the queried
    # security and to the same T+1 trading day as the check instant.
    if getattr(quote, "security_code", code) != code:
        return _unavailable(
            code, FACT_AUCTION, "quote belongs to a different security",
            getattr(quote, "source", "tencent") or "tencent", now, quote=quote,
        )
    quote_status = getattr(quote, "status", "live")
    if quote_status in ("unavailable", "halt", "closed") or getattr(
        quote, "stale", False
    ):
        return _unavailable(
            code, FACT_AUCTION,
            f"quote surface is {quote_status}; no usable auction print",
            getattr(quote, "source", "tencent") or "tencent", now, quote=quote,
        )
    # Purpose qualification: an auction verdict is only available from a
    # record qualified for THIS purpose.  An ordinary intraday snapshot
    # whose source stamps a continuous/post-auction session state
    # (trading/lunch/closing-auction/closed, or the pre/holiday states that
    # never carry a print) reports the day's open price as a post-hoc
    # field of the regular tape; it is not the opening-auction print that
    # was visible inside 09:15-09:25.  Only a quote surface that does not
    # declare such a session state (the pre-qualified auction record
    # shape) can qualify, and the time-window gates below still apply.
    market_state = getattr(quote, 'market_state', None)
    if market_state is not None:
        return _unavailable(
            code, FACT_AUCTION,
            'ordinary intraday snapshot open is not an auction-purpose print (market_state='+str(market_state)+')',
            getattr(quote, 'source', 'tencent') or 'tencent', now, quote=quote,
        )

    # Point-in-time AND window qualification: a genuine auction print must
    # carry BOTH a source observation time and a source receipt time, each
    # on the same trading day and each at or before the check instant, and
    # the observation must land inside the 09:15-09:25 auction window.  An
    # ordinary ``open`` tick without source times, a print observed before
    # the window, or one stamped outside the window never qualifies - the
    # fact stays ``unavailable`` and never borrows the check instant as a
    # stand-in receipt.
    quote_time = getattr(quote, "quote_time", None)
    received_at = getattr(quote, "received_at", None)
    if quote_time is None or received_at is None:
        return _unavailable(
            code, FACT_AUCTION,
            "auction print lacks a source observation/receipt time",
            getattr(quote, "source", "tencent") or "tencent", now, quote=quote,
        )
    qt = quote_time.astimezone(EXCHANGE_TZ)
    ra = received_at.astimezone(EXCHANGE_TZ)
    if qt.date() != now.date() or ra.date() != now.date():
        return _unavailable(
            code, FACT_AUCTION, "auction print belongs to a different session",
            getattr(quote, "source", "tencent") or "tencent", now, quote=quote,
        )
    if qt > now or ra > now:
        return _unavailable(
            code, FACT_AUCTION, "auction print observed/received after the check instant",
            getattr(quote, "source", "tencent") or "tencent", now, quote=quote,
        )
    if not (AUCTION_OPEN <= qt.time() <= AUCTION_CLOSE):
        return _unavailable(
            code, FACT_AUCTION,
            "auction print observed outside the 09:15-09:25 window",
            getattr(quote, "source", "tencent") or "tencent", now, quote=quote,
        )
    if ra.time() > AUCTION_CLOSE:
        # A print first received after 09:25 was never visible inside the
        # auction window, so it cannot serve as the 09:15-09:25-visible
        # auction record even when the check itself runs late.
        return _unavailable(
            code, FACT_AUCTION,
            "auction print received after the 09:15-09:25 visibility window",
            getattr(quote, "source", "tencent") or "tencent", now, quote=quote,
        )
    return FactVerdict(
        security_code=code,
        fact=FACT_AUCTION,
        status="verified",
        reason=None,
        source=getattr(quote, "source", "tencent") or "tencent",
        observed_at=_iso(quote_time),
        received_at=_iso(received_at),
    )


def _canonical_fact(fact) -> str:
    """Bare fact category for a declared dependency name.

    A package projects ``requires:PREOPEN_AUCTION`` onto the plan as the
    scoped name ``PREOPEN_AUCTION``; the resolver keys on the bare category
    ``AUCTION``.  Strip the PREOPEN_ scope prefix so a scoped declaration
    resolves to the same category instead of an unknown fact (and never
    yields a doubled PREOPEN_PREOPEN_* reason downstream).
    """
    name = str(fact or "")
    return name[len("PREOPEN_"):] if name.startswith("PREOPEN_") else name


def resolve_fact(quote, code, fact, now) -> FactVerdict:
    category = _canonical_fact(fact)
    if category == FACT_SUSPENSION:
        return resolve_suspension(quote, code, now)
    if category == FACT_LIMIT_PRICE:
        return resolve_limit_price(quote, code, now)
    if category == FACT_AUCTION:
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
    account_id: str | None = None,
    policy_ref: str | None = None,
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

        # E6-01/E6-02 unified qualification+pool verdict: ONE gate decides
        # whether quote-derived facts may be admitted for this security, and
        # the same verdict governs both the declared-fact list and the auction
        # price comparison below - a bad/missing/unlisted pool never leaves an
        # independent ungated verdict feeding the price-cap failure.
        pool_blocks_facts = False
        pool_block_reason = None
        if pool_error is not None:
            pool_blocks_facts = True
            pool_block_reason = f"named pre-open pool defect: {pool_error}"
        elif pool is not None and code not in pool_members:
            pool_blocks_facts = True
            pool_block_reason = "security absent from the named pre-open pool"
        if pool_blocks_facts:
            for f in facts:
                if f["status"] == "verified":
                    f["status"] = "unavailable"
                    f["reason"] = pool_block_reason
                    # The pool defect denies admission but never rewrites the
                    # source clocks: observed/received keep their original
                    # source values (a missing source clock stays null).
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

        # The auction comparison is one more consumer of the SAME
        # unified qualification+pool verdict as the declared facts: it is
        # skipped entirely unless the plan actually depends on the auction
        # fact, and a defective/missing/unlisted pool exits the price
        # judgment instead of feeding the cap failure from a fresh ungated
        # resolve_fact.  Never minted for a plan without the dependency.
        auction_declared = any(
            _canonical_fact(dep) == FACT_AUCTION for dep in depends
        )
        auction_price = None
        if auction_declared and not pool_blocks_facts and quote is not None:
            auction_verdict = resolve_fact(quote, code, FACT_AUCTION, now)
            if auction_verdict.status == "verified":
                auction_price = getattr(quote, "open", None)
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
        "account_id": account_id,
        "account_mode": account_mode,
        "policy_ref": policy_ref,
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


def write_check_receipt(state_dir: Path, check: dict, *, account_id=None) -> Path:
    """Persist a pre-open check receipt beside the named pool.

    E6-06: the verify pass is durable evidence - the package, account
    revision, policy mode, pool SHA, per-plan facts/alerts/recheck quantity
    and refusal reasons are recoverable across a restart, keyed by trading
    day and check SHA so a later audit reads the identical record.  The day
    file ``<date>.json`` holds every account's receipt under its own key in
    ``accounts``, so a second account's same-day check merges in without
    overwriting the first's record."""
    path = (
        Path(state_dir)
        / "preopen-check"
        / (str(check["trading_date"]) + ".json")
    )
    account = account_id or check.get("account_id") or "account"
    prior_accounts: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            if isinstance(existing.get("accounts"), dict):
                prior_accounts = dict(existing["accounts"])
            elif existing.get("account_id"):
                # Fold a single-account receipt from an earlier write into
                # the shared accounts map so it is preserved, never lost.
                prior_accounts[str(existing["account_id"])] = existing
    prior_accounts[str(account)] = check
    # The day file keeps the just-written receipt at the top level (so a
    # single-account read still finds its own sha/fields) AND embeds every
    # account's receipt under ``accounts`` - a second account's check
    # merges in without overwriting or hiding the first's record.
    merged = {**check, "accounts": prior_accounts}
    _write_atomic(path, json.dumps(merged, ensure_ascii=False, sort_keys=True, indent=2))
    return path
