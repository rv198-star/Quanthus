"""Versioned self-directed simulated execution rule: ``p12-paper-exec-v1``.

Adopted by product planning for self-directed paper limit orders only.  It is
a continuous-auction, last-price trigger model - NOT the adopted system
``close-to-next-open-v1`` path, which still governs strategy advice.

The rule is intentionally explicit so its behaviour can be reviewed:

- Instruments: A-share equities by 6-digit code; no options/funds.
- Clock: Asia/Shanghai.  Matching runs only inside continuous-auction windows
  (09:30-11:30, 13:00-14:57).  Call auctions, lunch break, non-trading days
  and quote outages do not match; a resting order stays open.
- Trigger: a buy fills only when a live quote last_price <= limit_price;
  a sell fills only when last_price >= limit_price.  Only quotes observed at
  or after the order's server-side accepted_at may fill it.
- Price: fills at the order's limit price (last-price trigger, no queue
  model).  We do not model queue depth, partial ticks or slippage; the
  matched price is the visible last price and the order is either marketable
  or it rests.
- Quantity: whole fills only in this slice (accepted orders are whole 100-share
  lots; partial fills are not modelled).
- Fees: none in this slice (``fee_model: none``) - stated, not hidden.
- Cash: a buy reserve = quantity * limit_price held from accept; a sell
  reserve = the T+1 sellable quantity (same-day buys are not sellable).
- Idempotence: the same snapshot never produces the same fill twice, and a
  re-submitted order_id returns its stored receipt without double-posting.
- Day order: unfilled orders expire at the trading-day close and must be
  re-submitted; they never carry into the next session.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

RULE_VERSION = "p12-paper-exec-v1"

LOT = 100

REJECT = {
    "INSUFFICIENT_CASH": "cash insufficient for the order hold",
    "FUNDING_NO_FREE_BUCKET": "no free bucket can take a new security; sell a held security or free a cycle before opening another",
    "NON_ROUND_LOT": "quantity must be a whole 100-share lot",
    "SELL_NOT_ALLOWED_T1": "shares bought today are not sellable until T+1",
    "INSUFFICIENT_POSITION": "sellable position is insufficient",
    "MARKET_CLOSED": "outside continuous auction; the order does not match",
    "SECURITY_SUSPENDED": "security is suspended and cannot trade",
    "PRICE_OUT_OF_BOUNDS": "price is outside the valid bound",
    "QUOTE_UNAVAILABLE": "no usable quote to judge marketability",
    "BELOW_LIMIT": "last price does not satisfy the limit; order rests",
    "DUPLICATE_ORDER": "duplicate order id",
    "INVALID_ORDER": "order field is invalid",
    "PLAN_NOT_FOUND": "the referenced plan is not currently effective",
    # Issue #723 E8 r5 (planning ruling): phase-1 sell confirms and sell
    # registrations are display-only - the order never reaches the matcher.
    "PLAN_NOT_EXECUTABLE": "the plan is display-only in this phase; sell execution is not open",
    "ORDER_CONTEXT_MISMATCH": "the order disagrees with the plan's side or security",
    # Issue #723 E8 r5 (buy): the confirm-time mark re-sized the plan, so
    # the order is not written.  The user must accept the NEW quantity - a
    # resubmit echoing that figure then binds under the same id.
    "CONFIRM_RESIZED": "the confirm-time price re-sized this plan; confirm the new quantity to proceed",
    # Issue #719 R12: typed unavailable gates for the pre-open facts a plan's
    # conditions may depend on; never inferred from a zero-volume bar, a last
    # price or the prior session state.
    "PREOPEN_SUSPENSION_UNAVAILABLE": "suspension/resumption fact for this plan has no named supply",
    "PREOPEN_LIMIT_PRICE_UNAVAILABLE": "native limit-price fact for this plan has no named supply",
    "PREOPEN_AUCTION_UNAVAILABLE": "call-auction fact for this plan has no named supply",
}

ORDER_STATUS = {
    "open": "resting",
    "filled": "filled",
    "cancelled": "cancelled",
    "expired": "expired at day close",
    "rejected": "rejected",
}


@dataclass(frozen=True)
class OrderCommand:
    order_id: str
    side: str              # buy | sell
    security_code: str
    quantity: int
    limit_price: Decimal

    def validate(self) -> str | None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            return "INVALID_ORDER"
        if self.side not in ("buy", "sell"):
            return "INVALID_ORDER"
        if not isinstance(self.quantity, int) or self.quantity <= 0:
            return "INVALID_ORDER"
        if self.quantity % LOT != 0:
            return "NON_ROUND_LOT"
        if not isinstance(self.limit_price, Decimal) or self.limit_price <= 0:
            return "PRICE_OUT_OF_BOUNDS"
        code = self.security_code
        if not (isinstance(code, str) and len(code) == 6 and code.isdigit()):
            return "INVALID_ORDER"
        return None
