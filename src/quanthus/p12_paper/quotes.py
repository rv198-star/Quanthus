"""Real A-share quote snapshots for the P12 paper-trading slice.

One responsibility: given a 6-digit security code, return the current quote
with its source, the exchange-side quote time, our receive time and the
trading state, or a faithful unavailable/stale/halt reason.  It never invents
a price: a closed market, a suspended security, a failed channel or a stale
snapshot is reported as such.

The default transport reuses the repository admitted Tencent ``qt.gtimg.cn``
channel (same endpoint already used by ``providers/after_hours_tdx`` for
zero-activity evidence).  An ``astock`` sources wheel, when deployed, can
feed the same adapter surface; it is not required for the slice to run.

Only the latest traded price is modelled (a paper last-price trigger).  The
snapshot does not carry queue depth, so the matching rule treats it as a
reference trigger price, not exchange-level queue evidence.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={symbol}"

QUOTE_CONTRACT_VERSION = "p12-paper-quote-v1"

# 2026 A-share public holidays (SSE/SZSE closed weekdays).  Weekends are
# covered by weekday(); this list covers the weekday closures.
CN_HOLIDAYS_2026: frozenset = frozenset(
    date.fromisoformat(d)
    for d in (
        "2026-01-01",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-04-06",
        "2026-05-01",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
    )
)

# Continuous-auction windows (Shanghai time).  09:15-09:25 open call auction
# and 14:57-15:00 closing call auction are not continuous matching.
MORNING_OPEN = (9, 30)
MORNING_CLOSE = (11, 30)
AFTERNOON_OPEN = (13, 0)
AFTERNOON_CLOSE = (14, 57)
DAY_CLOSE = (15, 0)


def _exchange(code: str) -> tuple[str, str]:
    """Return (tencent symbol, exchange label) for a 6-digit code."""
    if code.startswith(("5", "6", "9")):
        return f"sh{code}", "上交所"
    if code.startswith(("4", "8")):
        return f"bj{code}", "北交所"
    return f"sz{code}", "深交所"


@dataclass(frozen=True)
class SecurityIdentity:
    security_code: str
    security_name: str
    exchange: str
    suspended: bool
    identity_source: str
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "security_name": self.security_name,
            "exchange": self.exchange,
            "listed": True,
            "suspended": self.suspended,
            "identity_source": self.identity_source,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class Quote:
    """A real snapshot. ``status`` is the honest state, never a fabricated price."""

    security_code: str
    security_name: str
    exchange: str
    status: str          # live | closed | halt | stale | unavailable
    last_price: Decimal | None
    previous_close: Decimal | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    volume_lots: int | None
    quote_time: datetime | None   # source-side timestamp, aware (Asia/Shanghai)
    received_at: datetime          # our receive time, aware
    source: str                    # tencent | astock-* | none
    market_state: str              # trading | lunch_break | call_auction_close | closed | pre_open | holiday
    stale: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "security_name": self.security_name,
            "exchange": self.exchange,
            "status": self.status,
            "last_price": None if self.last_price is None else format(self.last_price, "f"),
            "previous_close": None if self.previous_close is None else format(self.previous_close, "f"),
            "open": None if self.open is None else format(self.open, "f"),
            "high": None if self.high is None else format(self.high, "f"),
            "low": None if self.low is None else format(self.low, "f"),
            "volume_lots": self.volume_lots,
            "quote_time": None if self.quote_time is None else self.quote_time.isoformat(),
            "received_at": self.received_at.isoformat(),
            "source": self.source,
            "market_state": self.market_state,
            "stale": self.stale,
            "reason": self.reason,
        }


# How far a quote_time may lag the wall clock before it is flagged stale.
_STALE_TOLERANCE = timedelta(seconds=120)


class TencentQuoteSource:
    """Fetch one snapshot from ``qt.gtimg.cn``; parse the ``v_symbol="~"`` payload.

    Field layout (observed): index 1 = name, 2 = code, 3 = last, 4 = prev close,
    5 = open, 6 = volume (lots), 30 = yyyymmddhhmmss quote time, 33 = high,
    34 = low.  Fields are decimal strings; a price is never re-derived.
    """

    source = "tencent"

    def __init__(self, *, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 8.0) -> None:
        self._opener = opener
        self._timeout = timeout

    def fetch_raw(self, code: str) -> tuple[list[str], datetime]:
        symbol, _ = _exchange(code)
        request = urllib.request.Request(
            TENCENT_QUOTE_URL.format(symbol=symbol),
            headers={"User-Agent": "Mozilla/5.0 Quanthus/1.0"},
        )
        with self._opener(request, timeout=self._timeout) as response:
            text = response.read().decode("gb18030", errors="replace")
        received_at = datetime.now(UTC).astimezone()
        marker = "v_" + symbol + "=\""
        start = text.find(marker)
        if start < 0:
            raise RuntimeError("Tencent quote response marker is missing")
        payload = text[start + len(marker):].split("\"", 1)[0]
        fields = payload.split("~")
        if len(fields) <= 34:
            raise RuntimeError("Tencent quote response has too few fields")
        return fields, received_at


class QuoteService:
    """Identity, market session and per-code snapshot for the paper slice.

    A ``fetcher`` may be injected (tests / replay) so the matching logic can be
    driven by labelled historical instants without a live channel; the live
    service uses ``TencentQuoteSource``.
    """

    def __init__(
        self,
        *,
        fetcher: Any | None = None,
        now: Callable[[], datetime] | None = None,
        holidays: frozenset = CN_HOLIDAYS_2026,
    ) -> None:
        self._fetcher = fetcher or TencentQuoteSource()
        self._now = now or (lambda: datetime.now(UTC).astimezone())
        self._holidays = holidays

    # ---- session -------------------------------------------------------------
    def _sh_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(SHANGHAI)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self._holidays

    def market_state(self, moment: datetime | None = None) -> str:
        now = (moment or self._sh_now()).astimezone(SHANGHAI)
        if not self.is_trading_day(now.date()):
            return "holiday"
        hm = (now.hour, now.minute, now.second)
        if hm < MORNING_OPEN + (0,):
            return "pre_open"
        if hm < MORNING_CLOSE + (0,):
            return "trading"
        if hm < AFTERNOON_OPEN + (0,):
            return "lunch_break"
        if hm < AFTERNOON_CLOSE + (0,):
            return "trading"
        if hm < DAY_CLOSE + (0,):
            return "call_auction_close"
        return "closed"

    def continuous_auction(self, moment: datetime | None = None) -> bool:
        return self.market_state(moment) == "trading"

    def session(self) -> dict[str, Any]:
        now = self._sh_now()
        state = self.market_state(now)
        trading_date = now.date() if self.is_trading_day(now.date()) else self._prev_trading_day(now.date())
        return {
            "market_state": state,
            "trading_date": trading_date.isoformat(),
            "server_time": now.isoformat(),
            "continuous_auction": state == "trading",
            "next_open": self._next_open(now).isoformat(),
        }

    def _prev_trading_day(self, day: date) -> date:
        d = day - timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def _next_open(self, now: datetime) -> datetime:
        d = now.date()
        if self.is_trading_day(d) and (now.hour, now.minute, now.second) < MORNING_OPEN + (0,):
            return datetime(d.year, d.month, d.day, 9, 30, tzinfo=SHANGHAI)
        d = d + timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return datetime(d.year, d.month, d.day, 9, 30, tzinfo=SHANGHAI)

    # ---- identity ------------------------------------------------------------
    def identity(self, code: str) -> SecurityIdentity:
        q = self.quote(code)
        return SecurityIdentity(
            security_code=code,
            security_name=q.security_name,
            exchange=q.exchange,
            suspended=q.status == "halt",
            identity_source=q.source if q.status != "unavailable" else "none",
            as_of=(q.quote_time or q.received_at).isoformat(),
        )

    # ---- snapshot ------------------------------------------------------------
    def quote(self, code: str) -> Quote:
        symbol, exchange = _exchange(code)
        now = self._sh_now()
        market_state = self.market_state(now)
        try:
            fields, received_at = self._fetcher.fetch_raw(code)
        except Exception as exc:  # noqa: BLE001 - faithful unavailable
            return Quote(
                security_code=code,
                security_name=code,
                exchange=exchange,
                status="unavailable",
                last_price=None,
                previous_close=None,
                open=None,
                high=None,
                low=None,
                volume_lots=None,
                quote_time=None,
                received_at=now,
                source="none",
                market_state=market_state,
                stale=False,
                reason=f"quote channel failed: {exc}",
            )
        returned = str(fields[2]).strip().zfill(6)
        if returned != code:
            return Quote(
                security_code=code,
                security_name=str(fields[1]).strip() or code,
                exchange=exchange,
                status="unavailable",
                last_price=None,
                previous_close=None,
                open=None,
                high=None,
                low=None,
                volume_lots=None,
                quote_time=None,
                received_at=received_at,
                source="tencent",
                market_state=market_state,
                stale=False,
                reason=f"returned security {returned!r} differs from requested {code!r}",
            )
        name = str(fields[1]).strip()

        def _d(i: int) -> Decimal | None:
            try:
                v = str(fields[i]).strip()
                return Decimal(v) if v not in ("", "--") else None
            except Exception:
                return None

        last = _d(3)
        prev_close = _d(4)
        open_ = _d(5)
        high = _d(33)
        low = _d(34)
        try:
            volume_lots = int(Decimal(str(fields[6]).strip() or "0"))
        except Exception:
            volume_lots = None
        quote_time = self._parse_quote_time(str(fields[30]).strip())
        stale = quote_time is not None and (now - quote_time) > _STALE_TOLERANCE
        status = self._status(market_state, last, volume_lots, quote_time, stale)
        return Quote(
            security_code=code,
            security_name=name or code,
            exchange=exchange,
            status=status,
            last_price=last,
            previous_close=prev_close,
            open=open_,
            high=high,
            low=low,
            volume_lots=volume_lots,
            quote_time=quote_time,
            received_at=received_at,
            source="tencent",
            market_state=market_state,
            stale=stale,
        )

    @staticmethod
    def _parse_quote_time(raw: str) -> datetime | None:
        if len(raw) < 14 or not raw[:14].isdigit():
            return None
        try:
            return datetime(
                int(raw[0:4]), int(raw[4:6]), int(raw[6:8]),
                int(raw[8:10]), int(raw[10:12]), int(raw[12:14]),
                tzinfo=SHANGHAI,
            )
        except ValueError:
            return None

    @staticmethod
    def _status(market_state: str, last: Decimal | None, volume_lots: int | None,
                quote_time: datetime | None, stale: bool) -> str:
        if last is None or quote_time is None:
            return "unavailable"
        # zero volume + a non-current timestamp means the tape stopped -> halted.
        if (volume_lots or 0) == 0 and stale:
            return "halt"
        if market_state in ("holiday", "closed"):
            return "closed"
        if stale:
            return "stale"
        if (volume_lots or 0) == 0:
            return "halt"
        return "live"


TENCENT_M1_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"


class IntradaySeries:
    """One security's current-day minute series from the admitted Tencent m1 channel.

    Only completed minutes are returned: a point is kept when its labelled
    minute has already ended by receive time.  A partial / not-yet-closed
    trailing bar is dropped rather than served as a finished point.
    """

    def __init__(self, *, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 10.0) -> None:
        self._opener = opener
        self._timeout = timeout

    def fetch(self, code: str, *, now: datetime | None = None) -> dict[str, Any]:
        symbol, _ = _exchange(code)
        request = urllib.request.Request(
            f"{TENCENT_M1_URL}?param={symbol},m1,,320",
            headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0 Quanthus-TencentM1/1.0"},
        )
        received_at = now or datetime.now(UTC).astimezone()
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = __import__("json").loads(response.read())
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "reason": f"intraday channel failed: {exc}", "points": [], "as_of": received_at.isoformat()}
        rows = payload.get("data", {}).get(symbol, {}).get("m1") or []
        today = received_at.astimezone(SHANGHAI).strftime("%Y%m%d")
        points = []
        for row in rows:
            ts = str(row[0])
            if not ts.startswith(today):
                continue
            minute_end = self._minute_end(ts)
            if minute_end is None or minute_end > received_at:
                # trailing not-yet-closed minute: drop it, keep completed only.
                continue
            points.append({"time": ts, "price": str(row[4]), "volume_lots": str(row[5])})
        trade_date = f"{today[:4]}-{today[4:6]}-{today[6:]}"
        status = "live" if points else "unavailable"
        return {
            "security_code": code,
            "trade_date": trade_date,
            "source": "tencent-m1",
            "as_of": received_at.isoformat(),
            "status": status,
            "points": points,
            "contract_version": "p12-paper-intraday-v1",
        }

    @staticmethod
    def _minute_end(ts: str) -> datetime | None:
        if len(ts) != 12 or not ts.isdigit():
            return None
        try:
            start = datetime(
                int(ts[:4]), int(ts[4:6]), int(ts[6:8]),
                int(ts[8:10]), int(ts[10:12]), tzinfo=SHANGHAI,
            )
        except ValueError:
            return None
        return start + timedelta(minutes=1)


TENCENT_DAILY_KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
DAILY_KLINE_CONTRACT_VERSION = "p12-paper-daily-kline-v1"
DAILY_KLINE_DISCLAIMER = "看盘参考日线（腾讯前复权，非策略准入数据）"


class DailyKlineSeries:
    """Reference daily candlestick bars from Tencent qfq kline (display-only)."""

    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = 10.0,
        days: int = 90,
    ) -> None:
        self._opener = opener
        self._timeout = timeout
        self._days = days

    def fetch(
        self,
        code: str,
        *,
        now: datetime | None = None,
        days: int | None = None,
    ) -> dict[str, Any]:
        symbol, _ = _exchange(code)
        limit = days if days is not None else self._days
        request = urllib.request.Request(
            f"{TENCENT_DAILY_KLINE_URL}?param={symbol},day,,,{limit},qfq",
            headers={
                "Referer": "https://gu.qq.com/",
                "User-Agent": "Mozilla/5.0 Quanthus-TencentKline/1.0",
            },
        )
        received_at = now or datetime.now(UTC).astimezone()
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = __import__("json").loads(response.read())
        except Exception as exc:  # noqa: BLE001 - graceful unavailable
            return {
                "security_code": code,
                "source": "tencent-qfq-day",
                "basis": "qfq",
                "price_basis": "tencent_qfq_reference",
                "disclaimer": DAILY_KLINE_DISCLAIMER,
                "as_of": received_at.isoformat(),
                "status": "unavailable",
                "reason": f"daily kline channel failed: {exc}",
                "bars": [],
                "contract_version": DAILY_KLINE_CONTRACT_VERSION,
            }
        node = (payload.get("data") or {}).get(symbol) if isinstance(payload, dict) else None
        node = node if isinstance(node, dict) else {}
        if isinstance(node.get("qfqday"), list) and node.get("qfqday"):
            rows = node["qfqday"]
            basis = "qfq"
        elif isinstance(node.get("day"), list) and node.get("day"):
            rows = node["day"]
            basis = "unadjusted"
        else:
            rows = []
            basis = "qfq"
        bars: list[dict[str, Any]] = []
        for row in rows:
            parsed = self._parse_bar(row)
            if parsed is not None:
                bars.append(parsed)
        status = "live" if bars else "unavailable"
        reason = None if bars else "no daily kline bars returned"
        return {
            "security_code": code,
            "source": "tencent-qfq-day",
            "basis": basis,
            "price_basis": "tencent_qfq_reference",
            "disclaimer": DAILY_KLINE_DISCLAIMER,
            "as_of": received_at.isoformat(),
            "status": status,
            "reason": reason,
            "bars": bars,
            "contract_version": DAILY_KLINE_CONTRACT_VERSION,
        }

    @staticmethod
    def _parse_bar(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            return None
        session = str(row[0]).strip()
        try:
            date.fromisoformat(session)
        except ValueError:
            return None
        try:
            open_p = Decimal(str(row[1]).strip())
            close_p = Decimal(str(row[2]).strip())
            high_p = Decimal(str(row[3]).strip())
            low_p = Decimal(str(row[4]).strip())
            vol = int(Decimal(str(row[5]).strip() or "0"))
        except (InvalidOperation, ValueError):
            return None
        if not all(p.is_finite() and p > 0 for p in (open_p, close_p, high_p, low_p)):
            return None
        if low_p > high_p or vol < 0:
            return None
        return {
            "session": session,
            "open": format(open_p, "f"),
            "high": format(high_p, "f"),
            "low": format(low_p, "f"),
            "close": format(close_p, "f"),
            "volume": vol,
        }

