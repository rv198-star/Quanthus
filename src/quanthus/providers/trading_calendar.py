"""Refresh the A-share trading calendar before a market-dependent ODS lane.

The canonical calendar is a historical baseline, not proof that the target day is
known.  Every provider-mode market or intraday attempt fetches a bounded upstream
window, merges it into an attempt-local candidate, and requires the target date to
be present in the fresh response before session preflight may continue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

SOURCE = "baostock.query_trade_dates"
EXCHANGES = ("SSE", "SZSE", "BSE")
REQUIRED_EXCHANGES = ("SSE", "SZSE")
SCHEMA_VERSION = 1


class CalendarRefreshError(RuntimeError):
    """Raised when fresh target-date calendar evidence cannot be established."""


@dataclass(frozen=True)
class CalendarRefreshResult:
    target_date: date
    window_start: date
    window_end: date
    target_state: str
    source: str
    source_path: Path
    output_path: Path
    metadata_path: Path
    fetched_base_rows: int
    merged_rows: int
    changed_keys: int
    sha256: str
    fetched_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "target_date": self.target_date.isoformat(),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "target_state": self.target_state,
            "source": self.source,
            "source_path": str(self.source_path),
            "output_path": str(self.output_path),
            "metadata_path": str(self.metadata_path),
            "fetched_base_rows": self.fetched_base_rows,
            "merged_rows": self.merged_rows,
            "changed_keys": self.changed_keys,
            "sha256": self.sha256,
            "fetched_at": self.fetched_at.isoformat(),
            "evidence_model": "shared_a_share_calendar_mirrored_to_exchanges",
        }


CalendarFetcher = Callable[[date, date], Sequence[dict[str, Any]]]


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise CalendarRefreshError(f"invalid trade_date: {value!r}") from exc


def _as_flag(value: Any) -> int:
    try:
        flag = int(value)
    except (TypeError, ValueError) as exc:
        raise CalendarRefreshError(f"invalid is_open value: {value!r}") from exc
    if flag not in {0, 1}:
        raise CalendarRefreshError(f"is_open must be 0 or 1, got {flag!r}")
    return flag


def fetch_baostock_calendar(start: date, end: date) -> list[dict[str, Any]]:
    """Fetch one shared A-share calendar window from the locked upstream source."""

    try:
        import baostock as bs  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise CalendarRefreshError("baostock is required for calendar refresh") from exc

    try:
        login = bs.login()
    except Exception as exc:
        raise CalendarRefreshError(f"baostock login request failed: {exc}") from exc
    if str(login.error_code) != "0":
        raise CalendarRefreshError(f"baostock login failed: {login.error_msg}")
    try:
        query = bs.query_trade_dates(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        if str(query.error_code) != "0":
            raise CalendarRefreshError(
                f"baostock query_trade_dates failed: {query.error_msg}"
            )
        rows: list[dict[str, Any]] = []
        while str(query.error_code) == "0" and query.next():
            values = query.get_row_data()
            if len(values) < 2:
                raise CalendarRefreshError(
                    f"baostock calendar row has {len(values)} fields, expected at least 2"
                )
            rows.append({"trade_date": values[0], "is_open": values[1]})
        if not rows:
            raise CalendarRefreshError("baostock query_trade_dates returned no rows")
        return rows
    finally:
        bs.logout()


def _read_calendar(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CalendarRefreshError(f"calendar baseline missing: {path}")
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise CalendarRefreshError("pyarrow is required for calendar refresh") from exc
    try:
        return cast(list[dict[str, Any]], pq.read_table(path).to_pylist())
    except Exception as exc:
        raise CalendarRefreshError(f"cannot read calendar baseline {path}: {exc}") from exc


def _normalize_existing(rows: Sequence[dict[str, Any]]) -> dict[tuple[str, date], dict[str, Any]]:
    normalized: dict[tuple[str, date], dict[str, Any]] = {}
    for raw in rows:
        exchange = str(raw.get("exchange") or "").upper()
        if not exchange:
            raise CalendarRefreshError("calendar baseline contains a blank exchange")
        trade_date = _as_date(raw.get("trade_date") or raw.get("date"))
        key = (exchange, trade_date)
        if key in normalized:
            raise CalendarRefreshError(
                f"calendar baseline contains duplicate key {exchange}/{trade_date}"
            )
        normalized[key] = {
            "exchange": exchange,
            "trade_date": trade_date,
            "is_open": _as_flag(raw.get("is_open")),
            "session": str(raw.get("session") or "day"),
            "source": str(raw.get("source") or "unknown"),
            "built_at": raw.get("built_at"),
        }
    return normalized


def _normalize_fetched(
    rows: Sequence[dict[str, Any]], *, fetched_at: datetime
) -> tuple[dict[tuple[str, date], dict[str, Any]], set[date]]:
    base: dict[date, int] = {}
    for raw in rows:
        trade_date = _as_date(raw.get("trade_date") or raw.get("date"))
        if trade_date in base:
            raise CalendarRefreshError(f"upstream calendar duplicates {trade_date}")
        base[trade_date] = _as_flag(raw.get("is_open"))

    expanded: dict[tuple[str, date], dict[str, Any]] = {}
    for trade_date, is_open in sorted(base.items()):
        for exchange in EXCHANGES:
            expanded[(exchange, trade_date)] = {
                "exchange": exchange,
                "trade_date": trade_date,
                "is_open": is_open,
                "session": "day",
                "source": SOURCE,
                "built_at": fetched_at,
            }
    return expanded, set(base)


def _target_state(rows: dict[tuple[str, date], dict[str, Any]], target: date) -> str:
    states: dict[str, int] = {}
    for exchange in REQUIRED_EXCHANGES:
        row = rows.get((exchange, target))
        if row is None:
            raise CalendarRefreshError(
                f"fresh calendar has no {exchange} row for target date {target}"
            )
        states[exchange] = _as_flag(row["is_open"])
    if states["SSE"] != states["SZSE"]:
        raise CalendarRefreshError(
            f"fresh calendar disagrees for {target}: {states}"
        )
    return "both_open" if states["SSE"] == 1 else "both_closed"


def _atomic_write_calendar(rows: Sequence[dict[str, Any]], output: Path) -> str:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise CalendarRefreshError("pyarrow is required for calendar refresh") from exc

    schema = pa.schema(
        [
            ("exchange", pa.string()),
            ("trade_date", pa.date32()),
            ("is_open", pa.int8()),
            ("session", pa.string()),
            ("source", pa.string()),
            ("built_at", pa.timestamp("us", tz="UTC")),
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        pq.write_table(pa.Table.from_pylist(list(rows), schema=schema), tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, output)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return f"sha256:{hashlib.sha256(output.read_bytes()).hexdigest()}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def refresh_calendar(
    *,
    source: Path,
    output: Path,
    target_date: date,
    lookback_days: int = 7,
    fetcher: CalendarFetcher = fetch_baostock_calendar,
) -> CalendarRefreshResult:
    """Build and validate an attempt-local calendar candidate.

    The target date must come from this invocation's upstream response.  An old
    baseline row is never accepted as proof of freshness.
    """

    if lookback_days < 0 or lookback_days > 90:
        raise CalendarRefreshError("calendar lookback_days must be between 0 and 90")
    window_start = target_date - timedelta(days=lookback_days)
    window_end = target_date
    fetched_at = datetime.now(timezone.utc)
    existing = _normalize_existing(_read_calendar(source.resolve()))
    try:
        fetched_raw = list(fetcher(window_start, window_end))
    except CalendarRefreshError:
        raise
    except Exception as exc:
        raise CalendarRefreshError(f"calendar upstream request failed: {exc}") from exc
    fetched, fetched_dates = _normalize_fetched(fetched_raw, fetched_at=fetched_at)
    if target_date not in fetched_dates:
        raise CalendarRefreshError(
            f"upstream response does not contain target date {target_date}"
        )

    changed = sum(
        1
        for key, row in fetched.items()
        if key not in existing or existing[key]["is_open"] != row["is_open"]
    )
    merged = dict(existing)
    merged.update(fetched)
    state = _target_state(merged, target_date)
    ordered = [merged[key] for key in sorted(merged, key=lambda item: (item[1], item[0]))]
    try:
        digest = _atomic_write_calendar(ordered, output.resolve())
    except CalendarRefreshError:
        raise
    except Exception as exc:
        raise CalendarRefreshError(f"cannot write calendar candidate: {exc}") from exc
    metadata = output.with_suffix(output.suffix + ".meta.json").resolve()
    result = CalendarRefreshResult(
        target_date=target_date,
        window_start=window_start,
        window_end=window_end,
        target_state=state,
        source=SOURCE,
        source_path=source.resolve(),
        output_path=output.resolve(),
        metadata_path=metadata,
        fetched_base_rows=len(fetched_raw),
        merged_rows=len(ordered),
        changed_keys=changed,
        sha256=digest,
        fetched_at=fetched_at,
    )
    try:
        _atomic_write_json(metadata, result.to_dict())
    except Exception as exc:
        raise CalendarRefreshError(f"cannot write calendar metadata: {exc}") from exc
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-date", type=date.fromisoformat, required=True)
    parser.add_argument("--lookback-days", type=int, default=7)
    args = parser.parse_args()
    result = refresh_calendar(
        source=args.source,
        output=args.output,
        target_date=args.target_date,
        lookback_days=args.lookback_days,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
