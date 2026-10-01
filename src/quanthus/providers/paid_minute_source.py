"""Quarantined qualification for the owner's paid A-share minute proxy.

This module never writes ODS READY or DWD. A successful HTTP response is only
source evidence; every symbol must match the sealed daily and native 5m inputs.
The API key is read from stdin by the audit command and is never persisted.
"""

from __future__ import annotations

import argparse
import getpass
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.providers.minute_quality_policy import (
    STRICT,
    MinuteQualityPolicy,
    load_policy,
    require_supported_policy,
)
from quanthus.providers.minute_references import load_minute_references, parse_reference_sha256
from quanthus.providers.minute_source_selection import (
    MinuteCandidate,
    numeric_deviations,
    qualify_candidate,
)
from quanthus.providers.tencent_m1_normalize import expected_times

ENDPOINT = "https://tl.kaixin8.top/tushare/pro/stk_mins"
FIELDS = {"ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"}
MATERIAL_FIELDS = ("open", "high", "low", "close", "vol")


class PaidMinuteIdentityError(ValueError):
    """Captured rows belong to another security, date, or frequency."""


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid minute number") from exc
    if not number.is_finite():
        raise ValueError("non-finite minute number")
    return number


def _material_complete(row: dict[str, Any]) -> bool:
    return all(row[name] is not None for name in MATERIAL_FIELDS)


def _source_start_times(market_date: str) -> dict[str, str]:
    """Map the governed minute-end grid to an interval-start source grid."""

    result: dict[str, str] = {}
    for stamp in expected_times(market_date):
        canonical = datetime.strptime(stamp, "%Y%m%d%H%M")
        result[canonical.strftime("%H:%M")] = (canonical - timedelta(minutes=1)).strftime("%H:%M")
    return result


def _parse_rows(
    payload: dict[str, Any], *, symbol: str, market_date: str
) -> tuple[list[str], list[list[Any]], dict[str, dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("proxy response is not a data mapping")
    if payload.get("code") != 0 or payload.get("api_name") not in (None, "stk_mins"):
        raise ValueError("proxy response is not successful stk_mins")
    meta = payload.get("meta") or {}
    if meta.get("partial") is True or meta.get("coverage") not in (None, "complete"):
        raise ValueError("proxy minute coverage is not complete")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("proxy response has no data mapping")
    fields, items = data.get("fields"), data.get("items")
    if not isinstance(fields, list) or not FIELDS.issubset(fields) or not isinstance(items, list):
        raise ValueError("proxy minute fields/items contract changed")
    if payload.get("count") != len(items):
        raise ValueError("proxy minute count disagrees with items")

    canonical = {stamp[-4:-2] + ":" + stamp[-2:] for stamp in expected_times(market_date)}
    source_starts = set(_source_start_times(market_date).values())
    date_prefix = market_date + " "
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise ValueError("malformed proxy minute item")
        row = dict(zip(fields, item, strict=True))
        stamp = str(row["trade_time"])
        if (
            row["ts_code"] != symbol
            or row["freq"] != "1MIN"
            or not stamp.startswith(date_prefix)
            or len(stamp) != 19
            or not stamp.endswith(":00")
        ):
            raise PaidMinuteIdentityError("proxy minute symbol/frequency/date mismatch")
        hhmm = stamp[11:16]
        if hhmm in seen:
            raise ValueError("duplicate proxy minute timestamp")
        # The relay may expose call-auction observations and repeated quote
        # snapshots during the lunch break or after the close. They are retained
        # in the immutable raw response but are never admitted to the candidate.
        admitted_observation = (
            hhmm in canonical
            or hhmm in source_starts
            or "09:25" <= hhmm <= "09:30"
            or "11:30" <= hhmm <= "12:59"
            or "15:00" <= hhmm <= "15:59"
        )
        if not admitted_observation:
            raise ValueError("out-of-session proxy minute timestamp")
        for name in (*MATERIAL_FIELDS, "amount"):
            row[name] = None if row[name] is None else _decimal(row[name])
        if _material_complete(row) and (
            row["vol"] < 0
            or (row["amount"] is not None and row["amount"] < 0)
            or row["high"] < max(row["open"], row["close"], row["low"])
            or row["low"] > min(row["open"], row["close"], row["high"])
        ):
            raise ValueError("invalid proxy minute OHLCVA")
        seen[hhmm] = row
    return fields, items, seen


def _qualify_start_labeled(
    seen: dict[str, dict[str, Any]],
    *,
    market_date: str,
    daily_volume: Decimal,
    daily_close: Decimal,
    first_5m_volume: Any | None,
    quality_policy: MinuteQualityPolicy = STRICT,
) -> dict[str, Any]:
    mapping = _source_start_times(market_date)
    source_times = set(mapping.values())
    missing = source_times - set(seen)
    if missing:
        raise ValueError(f"proxy interval-start grid missing {len(missing)} minutes")
    incomplete = [t for t in source_times if not _material_complete(seen[t])]
    if incomplete:
        raise ValueError(
            f"proxy interval-start grid has {len(incomplete)} incomplete material minutes"
        )
    source_volume = sum((seen[t]["vol"] for t in source_times), Decimal(0))
    if not quality_policy.volume_matches(source_volume, daily_volume):
        raise ValueError(
            f"proxy interval-start daily volume mismatch: delta={source_volume - daily_volume}"
        )
    close = seen[mapping["15:00"]]["close"]
    if not (
        abs(close - daily_close) <= Decimal("0.011")
        if quality_policy == STRICT else quality_policy.price_matches(close, daily_close)
    ):
        raise ValueError("proxy interval-start/daily close mismatch")
    first = sum(
        (seen[mapping[t]]["vol"] for t in mapping if "09:31" <= t <= "09:35"),
        Decimal(0),
    )
    reference_first = _decimal(first_5m_volume) if first_5m_volume is not None else None
    return {
        "time_label_mode": "interval_start_shifted_plus_1m",
        "auction_mode": "source_interval_start_shifted_plus_1m",
        "first_5m_volume_delta": (
            str(first - reference_first) if reference_first is not None else None
        ),
        "source_close": str(close),
        "excluded_observation_rows": len(seen) - len(source_times),
    }


def qualify_payload(
    payload: dict[str, Any],
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    first_5m_volume: Any | None,
    quality_policy: MinuteQualityPolicy = STRICT,
) -> dict[str, Any]:
    """Interpret source-faithful rows without adjusting any reported shares.

    The proxy supports both the historical minute-end convention and an
    interval-start convention observed on the replacement relay. Only a complete
    fixed 240-row session can be selected. Other admitted observations remain in
    immutable raw evidence but are excluded before normalization; sealed daily
    inputs constrain the selected interpretation. No residual
    is spread over minute bars. This adapter-level result is provisional: only
    qualify_raw's shared exact whole-security gate licenses qualification.
    """

    require_supported_policy(quality_policy)
    _, items, seen = _parse_rows(payload, symbol=symbol, market_date=market_date)
    code = symbol.split(".")[0]
    grid = {
        date_stamp[-4:-2] + ":" + date_stamp[-2:]: date_stamp
        for date_stamp in expected_times(market_date)
    }
    preopen = [seen[t] for t in ("09:25", "09:26", "09:27", "09:28", "09:29") if t in seen]
    if preopen and (
        len(preopen) != 5
        or any(
            any(
                row[name] != preopen[0][name]
                for name in ("open", "high", "low", "close", "vol", "amount")
            )
            for row in preopen[1:]
        )
    ):
        raise ValueError("unexpected preopen observations are not identical snapshots")
    daily = _decimal(daily_volume)
    close = _decimal(daily_close)
    missing = set(grid) - set(seen)
    end_error: ValueError | None = None
    end_result: dict[str, Any] | None = None
    if not missing:
        auction = seen.get("09:30")
        incomplete = [t for t in grid if not _material_complete(seen[t])]
        if incomplete:
            end_error = ValueError(
                f"proxy session grid has {len(incomplete)} incomplete material minutes"
            )
        else:
            first = sum((seen[t]["vol"] for t in grid if "09:31" <= t <= "09:35"), Decimal(0))
            normal = sum((seen[t]["vol"] for t in grid), Decimal(0))
            reference_first = _decimal(first_5m_volume) if first_5m_volume is not None else None
            usable_auction = auction if auction and _material_complete(auction) else None
            auction_volume = usable_auction["vol"] if usable_auction else Decimal(0)
            include = quality_policy.volume_matches(normal + auction_volume, daily)
            exclude = (
                quality_policy.volume_matches(normal, daily)
                and reference_first is not None and first == reference_first
            )
            if quality_policy != STRICT and include and exclude and auction_volume:
                raise ValueError("ambiguous proxy auction interpretation under bounded policy")
        if not incomplete and (include or exclude):
            auction_mode = (
                "merged_into_0931"
                if include and usable_auction and auction_volume
                else "zero_or_absent_0930"
                if include
                else "duplicate_0930_excluded"
            )
            first_5m_delta = (
                first + auction_volume - reference_first
                if include and reference_first is not None
                else Decimal(0)
                if exclude
                else None
            )
            if (
                abs(seen["15:00"]["close"] - close) <= Decimal("0.011")
                if quality_policy == STRICT
                else quality_policy.price_matches(seen["15:00"]["close"], close)
            ):
                end_result = {
                    "time_label_mode": "interval_end_native",
                    "auction_mode": auction_mode,
                    "first_5m_volume_delta": (
                        str(first_5m_delta) if first_5m_delta is not None else None
                    ),
                    "source_close": str(seen["15:00"]["close"]),
                    "excluded_observation_rows": len(seen)
                    - len(grid)
                    - (1 if usable_auction and auction_mode == "merged_into_0931" else 0),
                }
            else:
                end_error = ValueError("proxy/daily close mismatch")
        elif not incomplete:
            first_with_delta = (
                first + auction_volume - reference_first
                if reference_first is not None
                else "unavailable"
            )
            first_without_delta = (
                first - reference_first if reference_first is not None else "unavailable"
            )
            end_error = ValueError(
                "proxy daily/native-5m volume mismatch: "
                f"with_auction_delta={normal + auction_volume - daily}, "
                f"without_auction_delta={normal - daily}, "
                f"first_5m_with_delta={first_with_delta}, "
                f"first_5m_without_delta={first_without_delta}"
            )
    else:
        end_error = ValueError(f"proxy session grid missing {len(missing)} minutes")

    start_result: dict[str, Any] | None = None
    start_error: ValueError | None = None
    try:
        start_result = _qualify_start_labeled(
            seen,
            market_date=market_date,
            daily_volume=daily,
            daily_close=close,
            first_5m_volume=first_5m_volume,
            quality_policy=quality_policy,
        )
    except ValueError as exc:
        start_error = exc

    if end_result is not None and start_result is not None:
        start_mapping = _source_start_times(market_date)
        if any(
            any(
                seen[canonical][name] != seen[start_mapping[canonical]][name]
                for name in MATERIAL_FIELDS
            )
            for canonical in grid
        ):
            raise ValueError("ambiguous proxy minute time-label interpretation")
        result = end_result
    elif end_result is not None:
        result = end_result
    elif start_result is not None:
        result = start_result
    else:
        assert end_error is not None and start_error is not None
        if set(_source_start_times(market_date).values()).issubset(seen):
            raise start_error from None
        raise end_error from None
    return {
        "status": "interpreted_payload_pending_strict_check",
        "provider": "paid-tushare-style-proxy-stk_mins",
        "stock_code": code,
        "market_date": market_date,
        "source_rows": len(items),
        "session_rows": len(grid),
        "preopen_duplicate_snapshots_excluded": len(preopen),
        **result,
        "source_volume": str(daily),
        "amount_status": "source_present_unpublished_pending_separate_quality_review",
    }


def _interpreted_candidate(
    raw: bytes,
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    first_5m_volume: Any | None,
    raw_ref: str,
    quality_policy: MinuteQualityPolicy = STRICT,
) -> tuple[MinuteCandidate, dict[str, Any]]:
    """Build one provisional whole security and adapter detail from captured bytes.

    The 09:30 auction is merged only when the daily/5m qualification above
    licenses it. Minute amount remains null until its own quality contract is
    accepted, despite the relay exposing an amount field in its raw response.
    The default retains this debt. The explicit bounded candidate instead keeps
    observed amount and rejects null at its shared final gate. This builder does
    not grant final qualification or monetary/source truth.
    """
    payload = json.loads(raw)
    result = qualify_payload(
        payload,
        symbol=symbol,
        market_date=market_date,
        daily_volume=daily_volume,
        daily_close=daily_close,
        first_5m_volume=first_5m_volume,
        quality_policy=quality_policy,
    )
    fields = payload["data"]["fields"]
    by_time = {
        str(row[fields.index("trade_time")])[11:16]: dict(zip(fields, row, strict=True))
        for row in payload["data"]["items"]
    }
    auction = by_time.get("09:30")
    start_mapping = _source_start_times(market_date)
    rows: list[dict[str, Any]] = []
    for stamp in expected_times(market_date):
        hhmm = stamp[-4:-2] + ":" + stamp[-2:]
        source = by_time[
            start_mapping[hhmm]
            if result["time_label_mode"] == "interval_start_shifted_plus_1m"
            else hhmm
        ]
        values = {name: _decimal(source[name]) for name in ("open", "high", "low", "close", "vol")}
        amount = (
            None if source["amount"] is None else _decimal(source["amount"])
        ) if quality_policy.require_amount else None
        if hhmm == "09:31" and auction and result["auction_mode"] == "merged_into_0931":
            values["open"] = _decimal(auction["open"])
            values["high"] = max(values["high"], _decimal(auction["high"]))
            values["low"] = min(values["low"], _decimal(auction["low"]))
            values["vol"] += _decimal(auction["vol"])
            if quality_policy.require_amount:
                amount = (
                    amount + _decimal(auction["amount"])
                    if amount is not None and auction["amount"] is not None else None
                )
        rows.append(
            {
                "stock_code": symbol.split(".")[0],
                "ts": f"{market_date} {hhmm}:00",
                "open": values["open"],
                "high": values["high"],
                "low": values["low"],
                "close": values["close"],
                "volume": values["vol"],
                "amount": amount,
            }
        )
    candidate = MinuteCandidate(
        provider="paid-tushare-style-proxy-stk_mins",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_ref=raw_ref,
        rows=tuple(rows),
        auction_provenance=result["auction_mode"],
        amount_status=(
            "source_observed_cny_no_estimation_candidate_only"
            if quality_policy.require_amount
            else "source_present_unpublished_pending_separate_quality_review"
        ),
    )
    return candidate, result


def qualify_raw(
    raw: bytes,
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    first_5m_volume: Any | None,
    raw_ref: str,
    quality_policy: MinuteQualityPolicy = STRICT,
) -> tuple[MinuteCandidate, dict[str, Any]]:
    """Apply the same explicitly selected final gate in audit and raw replay."""
    candidate, result = _interpreted_candidate(
        raw,
        symbol=symbol,
        market_date=market_date,
        daily_volume=daily_volume,
        daily_close=daily_close,
        first_5m_volume=first_5m_volume,
        raw_ref=raw_ref,
        quality_policy=quality_policy,
    )
    semantic_hash = qualify_candidate(
        candidate,
        symbol=symbol.split(".")[0],
        market_date=market_date,
        daily_volume=daily_volume,
        daily_close=daily_close,
        traded=True,
        quality_policy=quality_policy,
    )
    return candidate, {
        **result,
        "status": "qualified_candidate_not_published",
        "source_semantic_sha256": semantic_hash,
        "quality_policy": quality_policy.evidence(),
        "amount_status": candidate.amount_status,
        "numeric_deviations": numeric_deviations(
            candidate, daily_volume=daily_volume, daily_close=daily_close,
        ),
    }


def normalized_candidate(
    raw: bytes,
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    first_5m_volume: Any | None,
    raw_ref: str,
    quality_policy: MinuteQualityPolicy = STRICT,
) -> MinuteCandidate:
    """Build a provisional candidate; whole-security selection owns its final gate.

    Keep quality rejection in source selection: preemptively raising here would
    turn a rejected candidate into a hard evidence error and block a valid peer.
    """
    candidate, _ = _interpreted_candidate(
        raw,
        symbol=symbol,
        market_date=market_date,
        daily_volume=daily_volume,
        daily_close=daily_close,
        first_5m_volume=first_5m_volume,
        raw_ref=raw_ref,
        quality_policy=quality_policy,
    )
    return candidate


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _resume_state(
    output_dir: Path, *, codes: list[str], market_date: str, defer_pending: bool = False
) -> tuple[list[str], Counter[str]]:
    """Verify HTTP-200 evidence and optionally defer recorded HTTP-202 symbols."""

    selected = set(codes)
    summary_path = output_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("market_date") != market_date or summary.get(
            "selected_traded_symbols"
        ) != len(codes):
            raise ValueError("resume summary date or selected-universe mismatch")
    counts: Counter[str] = Counter()
    complete: set[str] = set()
    for receipt_path in sorted((output_dir / "receipts").glob("*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        code = receipt.get("stock_code")
        if code not in selected or receipt.get("market_date") != market_date:
            raise ValueError("resume receipt date or selected-universe mismatch")
        if defer_pending and receipt.get("status") == "source_pending":
            if receipt.get("http_status") != 202:
                raise ValueError("deferred pending receipt is not HTTP 202")
            complete.add(code)
            counts["source_pending"] += 1
            continue
        if receipt.get("status") not in {"qualified_candidate_not_published", "unqualified"}:
            continue
        if receipt.get("http_status") != 200:
            raise ValueError("terminal resume receipt is not HTTP 200")
        raw_path = output_dir / "raw" / f"{code}.json.gz"
        if not raw_path.is_file():
            raise ValueError("terminal resume receipt has no immutable raw response")
        with gzip.open(raw_path, "rb") as source:
            raw_sha256 = hashlib.sha256(source.read()).hexdigest()
        if raw_sha256 != receipt.get("source_raw_sha256"):
            raise ValueError("terminal resume raw response hash mismatch")
        complete.add(code)
        counts[receipt["status"]] += 1
    return [code for code in codes if code not in complete], counts


def select_canary_symbols(raw: str, traded_codes: list[str]) -> list[str]:
    requested = [item.strip() for item in raw.split(",")]
    if (
        len(requested) not in {1, 2, 3}
        or len(requested) != len(set(requested))
        or any(code not in traded_codes for code in requested)
    ):
        raise ValueError("canary requires one to three unique, traded sealed-universe codes")
    return requested


def _audit_symbol(
    code: str,
    *,
    day: str,
    key: str,
    daily: dict[str, Any],
    first_5m_volume: Any,
    output_dir: Path,
    reference_view_sha256: str | None = None,
    quality_policy: MinuteQualityPolicy = STRICT,
) -> dict[str, Any]:
    import requests

    require_supported_policy(quality_policy)
    symbol = code + (".SH" if code.startswith("6") else ".SZ")
    receipt: dict[str, Any] = {
        "stock_code": code, "market_date": day, "quality_policy": quality_policy.evidence(),
    }
    if reference_view_sha256 is not None:
        receipt["reference_effective_view_sha256"] = reference_view_sha256
    try:
        response = requests.get(
            ENDPOINT,
            params={
                "ts_code": symbol,
                "freq": "1min",
                "start_date": day + " 09:00:00",
                "end_date": day + " 16:00:00",
            },
            headers={"X-API-Key": key},
            timeout=25,
            verify=True,
            # Custom authentication headers survive requests' default redirects.
            # Only the explicitly authorized HTTPS endpoint may receive this key.
            allow_redirects=False,
        )
        receipt["http_status"] = response.status_code
        receipt["request_id"] = response.headers.get("x-request-id")
        if response.status_code == 202:
            receipt.update(status="source_pending", retry_after=response.headers.get("retry-after"))
        elif response.status_code == 429:
            receipt.update(status="rate_limited", retry_after=response.headers.get("retry-after"))
        elif response.status_code != 200:
            receipt.update(status="http_error")
        else:
            raw = response.content
            receipt["source_raw_sha256"] = hashlib.sha256(raw).hexdigest()
            raw_path = output_dir / "raw" / f"{code}.json.gz"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with (
                raw_path.open("wb") as sink,
                gzip.GzipFile(fileobj=sink, mode="wb", mtime=0) as zipped,
            ):
                zipped.write(raw)
            try:
                _, qualification = qualify_raw(
                    raw,
                    symbol=symbol,
                    market_date=day,
                    daily_volume=daily["volume"],
                    daily_close=daily["close"],
                    first_5m_volume=first_5m_volume,
                    quality_policy=quality_policy,
                    raw_ref=str(raw_path),
                )
                receipt.update(qualification)
            except (ValueError, KeyError) as exc:
                receipt.update(status="unqualified", reason=str(exc)[:350])
    except requests.RequestException as exc:
        receipt.update(status="transport_error", reason=type(exc).__name__)
    _write_json(output_dir / "receipts" / f"{code}.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Quarantine-only paid 1m source coverage audit")
    parser.add_argument("--market-date", required=True, type=date.fromisoformat)
    parser.add_argument("--daily-path", required=True, type=Path)
    parser.add_argument("--status-path", required=True, type=Path)
    parser.add_argument("--five-path", required=True, type=Path)
    parser.add_argument("--reference-sha256-json", type=parse_reference_sha256)
    parser.add_argument("--quality-policy", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--rate-per-second", type=float, default=1)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--wall-budget-seconds", type=int, default=240)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a hash-verified audit directory without re-requesting terminal HTTP-200 rows",
    )
    parser.add_argument(
        "--defer-pending",
        action="store_true",
        help="With --resume, leave recorded HTTP-202 symbols pending while auditing unseen symbols",
    )
    parser.add_argument(
        "--full-market",
        action="store_true",
        help="Explicitly authorize a bounded full-market audit",
    )
    parser.add_argument(
        "--symbols",
        help="Comma-separated, at most three traded codes for a bounded canary audit",
    )
    args = parser.parse_args()
    quality_policy = load_policy(args.quality_policy)
    if (
        args.rate_per_second <= 0
        or args.rate_per_second > 5
        or args.workers < 1
        or args.workers > 8
        or args.wall_budget_seconds < 60
        or (args.limit is not None and args.limit < 1)
        or (args.defer_pending and not (args.resume and args.full_market))
        or (args.symbols is not None and args.limit is not None)
        or (args.full_market and (args.symbols is not None or args.limit is not None))
        or (not args.full_market and args.symbols is None and args.limit is None)
    ):
        parser.error("choose exactly one of --symbols, --limit, or --full-market")
    day = args.market_date.isoformat()
    references = load_minute_references(
        daily_path=args.daily_path,
        status_path=args.status_path,
        five_path=args.five_path,
        market_date=day,
        expected_sha256=args.reference_sha256_json,
    )
    codes = [code for code in references if references[code]["tradestatus"] == "1"]
    if args.symbols is not None:
        codes = select_canary_symbols(args.symbols, codes)
    elif args.limit is not None:
        codes = codes[: args.limit]
    references = references.select(codes, require_first_five=True)
    reference_evidence = references.evidence()
    reference_scope = (
        "new_requests_only_resumed_receipts_not_requalified"
        if args.resume
        else "this_audit_requests"
    )
    selected_count = len(codes)
    output_exists = args.output_dir.exists() and any(args.output_dir.iterdir())
    if output_exists and not args.resume:
        raise ValueError("audit output directory must be empty")
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.resume:
        codes, counts = _resume_state(
            args.output_dir, codes=codes, market_date=day, defer_pending=args.defer_pending
        )
    else:
        counts = Counter()
    resumed_terminal = selected_count - len(codes)
    if not codes:
        _write_json(
            args.output_dir / "summary.json",
            {
                "market_date": day,
                "selected_traded_symbols": selected_count,
                "processed": resumed_terminal,
                "counts": counts,
                "stop_reason": (
                    "completed_scan_with_source_pending"
                    if counts["source_pending"]
                    else "completed"
                ),
                "elapsed_seconds": 0,
                "quarantine_only": True,
                "production_publication": False,
                "reference_inputs": reference_evidence,
                "qualification_reference_scope": reference_scope,
                "quality_policy": quality_policy.evidence(),
            },
        )
        return 3 if counts["source_pending"] else 0
    print("READY_FOR_KEY", flush=True)
    key = getpass.getpass("") if sys.stdin.isatty() else sys.stdin.readline().strip()
    if not key:
        raise ValueError("empty API key")
    started = time.monotonic()
    next_call = time.monotonic()
    processed = resumed_terminal
    stop_reason = "completed"

    def checkpoint() -> None:
        _write_json(
            args.output_dir / "summary.json",
            {
                "market_date": day,
                "selected_traded_symbols": selected_count,
                "processed": processed,
                "counts": counts,
                "stop_reason": stop_reason,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "quarantine_only": True,
                "production_publication": False,
                "reference_inputs": reference_evidence,
                "qualification_reference_scope": reference_scope,
                "quality_policy": quality_policy.evidence(),
            },
        )

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for start in range(0, len(codes), 10):
                if time.monotonic() - started >= args.wall_budget_seconds:
                    stop_reason = "stopped_on_wall_budget"
                    break
                batch = codes[start : start + 10]
                futures = []
                for code in batch:
                    time.sleep(max(0.0, next_call - time.monotonic()))
                    next_call = time.monotonic() + 1 / args.rate_per_second
                    futures.append(
                        pool.submit(
                            _audit_symbol,
                            code,
                            day=day,
                            key=key,
                            daily=references[code],
                            first_5m_volume=references[code]["first_5m_volume"],
                            output_dir=args.output_dir,
                            reference_view_sha256=references.effective_view_sha256,
                            quality_policy=quality_policy,
                        )
                    )
                batch_counts: Counter[str] = Counter()
                for future in as_completed(futures):
                    receipt = future.result()
                    counts[receipt["status"]] += 1
                    batch_counts[receipt["status"]] += 1
                processed = resumed_terminal + start + len(batch)
                checkpoint()
                print(
                    json.dumps(
                        {"processed": processed, "total": selected_count, "counts": counts},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if counts["rate_limited"]:
                    stop_reason = "stopped_on_provider_rate_limit"
                    break
                if batch_counts["source_pending"] >= max(3, len(batch) // 2):
                    stop_reason = "stopped_on_provider_pending_batch"
                    break
                if batch_counts["http_error"]:
                    stop_reason = "stopped_on_provider_http_error_batch"
                    break
                if time.monotonic() - started >= args.wall_budget_seconds:
                    stop_reason = "stopped_on_wall_budget"
                    break
    except BaseException:
        stop_reason = "interrupted_before_terminal_batch"
        raise
    finally:
        key = ""
        if stop_reason == "completed" and counts["source_pending"]:
            stop_reason = "completed_scan_with_source_pending"
        checkpoint()
    return 0 if stop_reason == "completed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
