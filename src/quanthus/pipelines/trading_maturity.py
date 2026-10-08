"""Fail-closed next-exchange-session maturity for historical Tencent 1m.

The source is BaoStock's explicit SSE/SZSE trade-date service, *not* a weekday
guess or a prior day's mutable ODS publication. Failure to obtain a complete
calendar is an unknown maturity state; it does not authorize a source request
or an overdue alarm. The per-process digest binds the observed date evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
FIRST_PROBE_AT = time(10, 10)
MAX_CALENDAR_DAYS = 45


@dataclass(frozen=True)
class NextSessionEvidence:
    next_session: date
    source: str
    calendar_sha256: str


def resolve_next_session(
    market_date: date, rows: Iterable[tuple[str, str] | list[str]]
) -> NextSessionEvidence:
    """Require every intervening calendar day, including all holiday closures."""
    expected = market_date
    verified: list[tuple[str, str]] = []
    next_session: date | None = None
    for raw in rows:
        if len(raw) != 2 or raw[1] not in ("0", "1"):
            raise ValueError("invalid trading calendar row")
        actual = date.fromisoformat(raw[0])
        if actual != expected:
            raise ValueError("trading calendar has missing, duplicate or unordered dates")
        if actual == market_date and raw[1] != "1":
            raise ValueError("requested market date is not an exchange trading session")
        verified.append((actual.isoformat(), raw[1]))
        if actual > market_date and raw[1] == "1":
            next_session = actual
            break
        expected += timedelta(days=1)
    if next_session is None:
        raise ValueError("future exchange trading date is unavailable")
    digest = hashlib.sha256(
        json.dumps(verified, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return NextSessionEvidence(next_session, "baostock.query_trade_dates", digest)


def verified_next_session(market_date: date) -> NextSessionEvidence:
    """Read a bounded live exchange calendar; never default to weekdays."""
    import baostock as bs

    # BaoStock prints login/logout banners to stdout; orchestration stdout must
    # remain machine-readable JSON, not a mixture of provider banners and runs.
    with redirect_stdout(StringIO()):
        result = bs.login()
        if str(result.error_code) != "0":
            raise RuntimeError("BaoStock trading calendar login unavailable")
        try:
            query = bs.query_trade_dates(
                start_date=market_date.isoformat(),
                end_date=(market_date + timedelta(days=MAX_CALENDAR_DAYS)).isoformat(),
            )
            if str(query.error_code) != "0":
                raise RuntimeError("BaoStock trading calendar query unavailable")
            rows = []
            while query.next():
                rows.append(query.get_row_data())
            return resolve_next_session(market_date, rows)
        finally:
            bs.logout()


def minute_maturity_eligible(
    market_date: date, now: datetime, evidence: NextSessionEvidence
) -> bool:
    """Never request same-day m1; allow next-session probes after 10:10 CST.

    Reaching this timestamp is *not* data qualification; exact daily-volume,
    actual source minute-grid and whole-security admission remain mandatory.
    """
    if now.tzinfo is None:
        raise ValueError("maturity clock needs a timezone")
    if evidence.next_session <= market_date:
        raise ValueError("maturity trading calendar did not advance")
    return now.astimezone(SHANGHAI) >= datetime.combine(
        evidence.next_session, FIRST_PROBE_AT, SHANGHAI
    )


def overdue_on_next_session(
    market_date: date, now: datetime, cutoff: time, evidence: NextSessionEvidence
) -> bool:
    if evidence.next_session <= market_date:
        raise ValueError("maturity trading calendar did not advance")
    if now.tzinfo is None:
        raise ValueError("overdue clock needs a timezone")
    return now.astimezone(SHANGHAI) >= datetime.combine(evidence.next_session, cutoff, SHANGHAI)
