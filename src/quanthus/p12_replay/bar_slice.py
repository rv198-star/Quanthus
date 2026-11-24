"""Bounded 1d-bar slicing from the pinned Base month partitions (issue #748).

Reads the REAL rows out of one verified Base member
(``dwd_security_bar/interval_size=1d`` month partition), projects them by
session date and security code onto plain ``DayBar`` objects, and writes
the per-session parquet the replay day's core member declares.  Every read
records the source member path and its pinned sha256 on the receipt - the
slice never repairs, re-dates or invents a bar; a session with no row for
a security simply has no DayBar (the quote source then honestly reads
unavailable, which is replay evidence, not a fill).

A session is expressed by ``observation_end``'s UTC-midnight close: a row
whose observation_end falls inside the session's [start, end) window
belongs to that session.  Only ``price_basis`` rows in the declared basis
(UNADJUSTED / none) and CNY are admitted; anything else is a named defect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.p12_replay.replay_quotes import DayBar

ADMITTED_PRICE_BASIS = {"UNADJUSTED", "none", "NONE"}
ADMITTED_CURRENCY = {"CNY"}


@dataclass(frozen=True)
class SliceRead:
    """Provenance of one partition read, carried on the replay receipt."""

    member_path: str
    member_sha256: str
    rows_scanned: int
    rows_admitted: int
    sessions_found: int


def _session_day(value) -> date | None:
    """Map a bar row's observation_end to its trading date.

    The P10 1d rows close at 16:00 UTC = 00:00+08 the next day, so the
    session date is observation_end's UTC date - matching the source's own
    ``date=`` in source_record_id.  A naive timestamp is treated as UTC;
    an unparseable value yields None (the row is skipped, not guessed).
    """

    if value is None:
        return None
    try:
        end = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end.astimezone(timezone.utc).date()


def read_month_bars(
    member_path: Path,
    *,
    member_sha256: str | None = None,
    columns: tuple[str, ...] = (
        "source_record_id",
        "observation_end",
        "listing_id",
        "interval_size",
        "price_basis",
        "currency",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    ),
) -> tuple[dict[date, dict[str, DayBar]], SliceRead]:
    """Project one Base month partition into {session_date: {code: DayBar}}.

    The code is read from ``source_record_id``'s ``code=XXXXXX`` segment -
    the payload's own security identity (the same segment the daily-core
    members carry).  Rows that fail the admitted basis/currency/parse
    checks are skipped, not coerced.
    """

    import re

    import pyarrow.parquet as pq

    table = pq.ParquetFile(str(member_path)).read(columns=list(columns))
    code_re = re.compile(r"code=(\d{6})")
    out: dict[date, dict[str, DayBar]] = {}
    rows_scanned = table.num_rows
    rows_admitted = 0
    for row in table.to_pylist():
        basis = str(row.get("price_basis") or "")
        if basis not in ADMITTED_PRICE_BASIS:
            continue
        if str(row.get("currency") or "") not in ADMITTED_CURRENCY:
            continue
        if str(row.get("interval_size") or "") != "1d":
            continue
        match = code_re.search(str(row.get("source_record_id") or ""))
        if match is None:
            continue
        code = match.group(1)
        session = _session_day(row.get("observation_end"))
        if session is None:
            continue
        try:
            bar = DayBar(
                security_code=code,
                trade_date=session,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=(
                    int(Decimal(str(row["volume"])))
                    if row.get("volume") is not None
                    else None
                ),
                amount=(
                    Decimal(str(row["turnover"]))
                    if row.get("turnover") is not None
                    else None
                ),
            )
        except Exception:  # noqa: BLE001 - an unparseable row is a named gap
            continue
        out.setdefault(session, {})[code] = bar
        rows_admitted += 1
    read = SliceRead(
        member_path=str(member_path),
        member_sha256=member_sha256 or "",
        rows_scanned=rows_scanned,
        rows_admitted=rows_admitted,
        sessions_found=len(out),
    )
    return out, read


def write_session_parquet(bars: dict[str, DayBar], path: Path) -> None:
    """Materialize one session's bars as the core member's 1d.parquet.

    Carries the security_code column the input reader names, plus the OHLC
    the replay's fill rule and the runner's chart evidence consume.
    """

    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = sorted(bars.values(), key=lambda b: b.security_code)
    table = pa.table(
        {
            "security_code": [b.security_code for b in rows],
            "security_name": [b.security_name or b.security_code for b in rows],
            "trade_date": [b.trade_date.isoformat() for b in rows],
            "open": [str(b.open) for b in rows],
            "high": [str(b.high) for b in rows],
            "low": [str(b.low) for b in rows],
            "close": [str(b.close) for b in rows],
            "volume": [b.volume for b in rows],
            "turnover": [str(b.amount) if b.amount is not None else None for b in rows],
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(path))
