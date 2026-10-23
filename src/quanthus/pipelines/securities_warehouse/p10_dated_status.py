"""P10 pure dated-status parsing, factored from the accepted #538 implementation.

Source: base24_status_evidence.py at c1faad291cce16cc153de1780ba5a5a58bbaae61.
Same request, identity and true acquisition checks. This bounded successor keeps
unknown selected flags as unavailable observations instead of rejecting all other
dates in a source response. No normal-status fallback or source priority vote.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

FLAGS = {"0", "1"}
PROVIDER = "baostock.query_history_k_data_plus"


class StatusEvidenceError(ValueError):
    """Unqualified evidence or immutable publication failure."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise StatusEvidenceError(reason)


def _instant(value: Any) -> datetime:
    _require(isinstance(value, str), "CAPTURE_MISSING")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StatusEvidenceError("CAPTURE_INVALID") from exc
    _require(parsed.utcoffset() is not None, "CAPTURE_ZONE_MISSING")
    return parsed.astimezone(timezone.utc)


def _day(value: Any) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    _require(isinstance(value, str), "DATE_INVALID")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise StatusEvidenceError("DATE_INVALID") from exc


def _observation(
    code: Any, day: Any, st: Any, trading: Any, acquired: Any, sha: str, index: int
) -> dict[str, Any]:
    _require(isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) is not None, "CODE_INVALID")
    _require(
        isinstance(st, str) and st in FLAGS and isinstance(trading, str) and trading in FLAGS,
        "STATUS_UNKNOWN",
    )
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", sha) is not None, "SOURCE_HASH_INVALID")
    known = _instant(acquired)
    business_day = _day(day)
    _require(
        date.fromisoformat(business_day) <= known.astimezone(ZoneInfo("Asia/Shanghai")).date(),
        "CAPTURE_BEFORE_OBSERVATION",
    )
    return dict(
        stock_code=code,
        market_date=business_day,
        status_value="ST" if st == "1" else "NORMAL",
        reason="PROVIDER_TRADING" if trading == "1" else "PROVIDER_NOT_TRADING",
        acquired_at=acquired,
        source_sha256=sha,
        source_row=index,
    )


def raw_observations(
    raw: dict[str, Any], keys: set[tuple[str, str]], sha: str
) -> list[dict[str, Any]]:
    req = raw.get("request") or {}
    code, exchange = raw.get("stock_code"), raw.get("exchange")
    _require(exchange in {"SSE", "SZSE"}, "EXCHANGE_UNKNOWN")
    expected = ("sh." if exchange == "SSE" else "sz.") + str(code)
    _require(raw.get("bs_code") == req.get("code") == expected, "SOURCE_IDENTITY")
    _require(
        req.get("fields") == "date,code,isST,tradestatus"
        and req.get("frequency") == "d"
        and req.get("adjustflag") == "3",
        "REQUEST_SCHEMA",
    )
    _require(raw.get("error_code") == "0", "SOURCE_REQUEST_FAILED")
    acquired = req.get("fetched_at")
    _instant(acquired)  # Never substitute built_at, mtime or market-date + 1.
    rows = raw.get("rows")
    if not isinstance(rows, list):
        raise StatusEvidenceError("SOURCE_ROW_COUNT")
    _require(
        isinstance(rows, list) and type(raw.get("n_rows")) is int and raw["n_rows"] == len(rows),
        "SOURCE_ROW_COUNT",
    )
    start, end = _day(req.get("start_date")), _day(req.get("end_date"))
    window = raw.get("window") or {}
    _require(
        window.get("start") == start and window.get("end") == end and start <= end, "REQUEST_WINDOW"
    )
    seen: set[str] = set()
    result = []
    for index, row in enumerate(rows):
        _require(
            isinstance(row, dict) and set(row) == {"date", "code", "isST", "tradestatus"},
            "RAW_ROW_SCHEMA",
        )
        day = _day(row["date"])
        _require(row["code"] == expected and start <= day <= end, "ROW_IDENTITY_OR_WINDOW")
        _require(day not in seen, "DUPLICATE_SOURCE_DATE")
        seen.add(day)
        if (code, day) in keys:
            try:
                observation = _observation(
                    code, day, row["isST"], row["tradestatus"], acquired, sha, index
                )
            except StatusEvidenceError as exc:
                if str(exc) != "STATUS_UNKNOWN":
                    raise
                observation = dict(
                    stock_code=code,
                    market_date=day,
                    status_value=None,
                    reason="STATUS_UNKNOWN",
                    acquired_at=acquired,
                    source_sha256=sha,
                    source_row=index,
                )
            result.append(observation)
    return result


def hotfix_observation(row: dict[str, Any], sha: str, index: int) -> dict[str, Any]:
    _require(row.get("source") == PROVIDER, "PROVIDER_UNKNOWN")
    _require(row.get("stock_code") == row.get("security_id"), "HOTFIX_IDENTITY")
    _require(_day(row.get("market_date")) == _day(row.get("date")), "HOTFIX_DATE_ALIAS")
    return _observation(
        row["stock_code"],
        row["market_date"],
        row.get("isST"),
        row.get("tradestatus"),
        row.get("acquired_at"),
        sha,
        index,
    )


def effective_query(relation: str, cutoff: datetime) -> str:
    """Same provider's real acquisition revisions; equal-time conflicts stay unknown.

    Original observations remain in their immutable file. NULL is a version too:
    do not search backwards for a convenient known status. This selection grants
    no precedence over a conflicting canonical interval or listing lifecycle.
    """
    _require(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", relation) is not None, "RELATION_IDENTIFIER")
    _require(cutoff.utcoffset() is not None, "STATUS_CUTOFF_ZONE")
    stamp = cutoff.isoformat()
    return f"""WITH ranked AS (
        SELECT *, dense_rank() OVER (PARTITION BY stock_code,market_date
            ORDER BY acquired_at DESC) AS latest
        FROM {relation} WHERE acquired_at<=TIMESTAMPTZ '{stamp}'
    ), top AS (SELECT * FROM ranked WHERE latest=1)
    SELECT stock_code,market_date,max(acquired_at) AS acquired_at,
        CASE WHEN count(DISTINCT coalesce(status_value,'UNAVAILABLE'))=1
             AND count(DISTINCT reason)=1 THEN min(status_value) END AS status_value,
        CASE WHEN count(DISTINCT coalesce(status_value,'UNAVAILABLE'))=1
             AND count(DISTINCT reason)=1 THEN min(reason)
             ELSE 'EQUAL_ACQUISITION_CONFLICT' END AS reason,
        count(*) AS observations_at_latest,
        list(struct_pack(source_sha256:=source_sha256,source_row:=source_row)
             ORDER BY source_sha256,source_row) AS source_refs
    FROM top GROUP BY stock_code,market_date"""
