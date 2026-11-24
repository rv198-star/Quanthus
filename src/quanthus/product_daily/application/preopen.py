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
    return FactVerdict(
        security_code=code,
        fact=FACT_AUCTION,
        status="verified",
        reason=None,
        source=getattr(quote, "source", "tencent") or "tencent",
        observed_at=_iso(getattr(quote, "quote_time", None)),
        received_at=_iso(getattr(quote, "received_at", None)) or now.isoformat(),
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
        missing = [f["fact"] for f in facts if f["status"] != "verified"]

        recheck_qty = None
        executable = True
        reasons: list[str] = []
        if plan.side == "buy":
            current = projection.quantity(code)
            remaining = plan.target_qty - current
            need = (plan.price_cap * remaining).quantize(Decimal("0.01"))
            if remaining <= 0:
                executable = False
                reasons.append("already_at_target")
            elif cash is not None and cash < need:
                executable = False
                affordable = int(cash / plan.price_cap) // _ROUND_LOT * _ROUND_LOT
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

        auction_price = getattr(quote, "open", None) if quote is not None else None
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