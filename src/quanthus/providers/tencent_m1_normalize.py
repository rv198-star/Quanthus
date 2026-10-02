"""Date-bounded, non-publishing normalization of quarantined Tencent m1 bytes.

This module deliberately does not create ODS READY. Tencent m1 has no exact
minute amount; its output is candidate evidence, not an accepted source.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any


TARGET_DATE = "2026-09-22"
SOURCE_COMMIT = "26fb13de28f4c79d01db15ce0ddd49d39e39abc5"


def expected_times(market_date: str = TARGET_DATE) -> tuple[str, ...]:
    intervals = (("09:31", "11:30"), ("13:01", "15:00"))
    result: list[str] = []
    for first, last in intervals:
        current = datetime.fromisoformat(f"{market_date}T{first}:00")
        end = datetime.fromisoformat(f"{market_date}T{last}:00")
        while current <= end:
            result.append(current.strftime("%Y%m%d%H%M"))
            current += timedelta(minutes=1)
    if len(result) != 240 or len(set(result)) != 240:
        raise AssertionError("invalid governed session grid")
    return tuple(result)


def _number(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite Tencent numeric value")
    return result


def normalize_symbol(
    code: str, raw: bytes, *, daily_volume: Any, daily_close: Any,
    market_date: str = TARGET_DATE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Produce 240 OHLCV observations; preserve absent amount as null.

    Empty provider minutes are zero-volume *candidates* only when the complete
    source volume equals the sealed daily volume. A separate acceptance gate
    must decide whether this inference is acceptable for formal publication.
    """
    symbol = ("sh" if code.startswith("6") else "sz") + code
    payload = json.loads(raw)
    if payload.get("code") != 0:
        raise ValueError(f"Tencent response code for {code}: {payload.get('code')}")
    source = payload["data"][symbol]["m1"]
    day = market_date.replace("-", "")
    grid = expected_times(market_date)
    allowed = set(grid) | {day + "0930"}
    observed: dict[str, dict[str, Decimal]] = {}
    multiplier = Decimal(1 if code.startswith("68") else 100)
    for item in source:
        if not isinstance(item, list) or len(item) < 7:
            raise ValueError(f"malformed Tencent m1 row for {code}")
        stamp = str(item[0])
        if not stamp.startswith(day):
            continue
        if stamp not in allowed or stamp in observed:
            raise ValueError(f"unexpected or duplicate Tencent timestamp {code}/{stamp}")
        if item[6] not in ({}, None):
            raise ValueError(f"unexpected Tencent field-7 contract for {code}/{stamp}")
        row = {
            "open": _number(item[1]),
            "close": _number(item[2]),
            "high": _number(item[3]),
            "low": _number(item[4]),
            "volume": _number(item[5]) * multiplier,
        }
        if (
            row["volume"] < 0
            or row["high"] < max(row["open"], row["close"], row["low"])
            or row["low"] > min(row["open"], row["close"], row["high"])
        ):
            raise ValueError(f"invalid Tencent m1 OHLCV for {code}/{stamp}")
        observed[stamp] = row
    if not observed or min(observed) > day + "0931" or max(observed) != day + "1500":
        raise ValueError(f"incomplete Tencent session boundary for {code}")
    source_volume = sum((row["volume"] for row in observed.values()), Decimal(0))
    if abs(source_volume - _number(daily_volume)) > Decimal("0.01"):
        raise ValueError(f"Tencent/daily volume mismatch for {code}")

    raw_day_rows = len(observed)
    auction = observed.pop(day + "0930", None)
    first = day + "0931"
    if auction is not None:
        minute = observed.get(first)
        observed[first] = (
            {
                "open": auction["open"],
                "high": max(auction["high"], minute["high"]),
                "low": min(auction["low"], minute["low"]),
                "close": minute["close"],
                "volume": auction["volume"] + minute["volume"],
            }
            if minute is not None
            else auction
        )
    if first not in observed:
        raise ValueError(f"no opening 1m or auction observation for {code}")

    previous_close: Decimal | None = None
    filled: list[str] = []
    rows: list[dict[str, Any]] = []
    for stamp in grid:
        item = observed.get(stamp)
        if item is None:
            if previous_close is None:
                raise ValueError(f"cannot infer first-minute no-trade bar for {code}")
            item = {
                "open": previous_close,
                "high": previous_close,
                "low": previous_close,
                "close": previous_close,
                "volume": Decimal(0),
            }
            filled.append(stamp)
        previous_close = item["close"]
        rows.append(
            {
                "stock_code": code,
                "ts": datetime.strptime(stamp, "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M:00"),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item["volume"]),
                "amount": None,
            }
        )
    if abs(_number(rows[-1]["close"]) - _number(daily_close)) > Decimal("0.011"):
        raise ValueError(f"Tencent/daily close mismatch for {code}")
    if abs(sum((_number(row["volume"]) for row in rows), Decimal(0)) - source_volume) > Decimal("0.01"):
        raise ValueError(f"normalization lost source volume for {code}")
    return rows, {
        "source_rows": raw_day_rows,
        "normalized_rows": len(rows),
        "opening_auction_merged": auction is not None,
        "inferred_zero_volume_minutes": filled,
        "volume_multiplier": int(multiplier),
        "minute_amount_status": "source_absent_null_not_estimated",
    }


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)


def build_candidate(
    *, capture_roots: tuple[Path, Path], status_path: Path, daily_path: Path, output: Path
) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if output.exists():
        raise ValueError(f"candidate output already exists: {output}")
    statuses = pq.read_table(status_path, columns=["stock_code", "market_date", "tradestatus"]).to_pylist()
    dailies = pq.read_table(daily_path, columns=["stock_code", "market_date", "volume", "close"]).to_pylist()
    status = {row["stock_code"]: row for row in statuses}
    daily = {row["stock_code"]: row for row in dailies}
    codes = sorted(status)
    if (
        len(codes) != 4992
        or len(statuses) != 4992
        or len(daily) != 4992
        or set(status) != set(daily)
        or any(row["market_date"] != TARGET_DATE for row in statuses + dailies)
        or any(str(row["tradestatus"]) not in {"0", "1"} for row in statuses)
    ):
        raise ValueError("sealed universe/date mismatch")
    digest = hashlib.sha256("\n".join(codes).encode()).hexdigest()
    source_files: dict[str, tuple[Path, Path]] = {}
    for root in capture_roots:
        manifest = json.loads((root / "manifest.json").read_text())
        if (
            manifest.get("source_commit") != SOURCE_COMMIT
            or manifest.get("market_date") != TARGET_DATE
            or manifest.get("universe_sha256") != digest
            or manifest.get("shard_count") != 2
            or manifest.get("purpose") != "raw-quarantine-only-no-ods-ready-no-dwd"
        ):
            raise ValueError(f"invalid quarantine manifest: {root}")
        for receipt in (root / "receipts").glob("*.json"):
            code = receipt.stem
            if code in source_files:
                raise ValueError(f"duplicate quarantine code: {code}")
            source_files[code] = (root / "raw" / f"{code}.json.gz", receipt)
    if set(source_files) != set(codes):
        raise ValueError("quarantine roots do not cover exact sealed universe")

    output.mkdir(parents=True)
    bar_dir = output / "research_1m"
    receipt_dir = output / "receipts"
    bar_dir.mkdir()
    receipt_dir.mkdir()
    schema = pa.schema(
        [
            pa.field("stock_code", pa.string()),
            pa.field("ts", pa.string()),
            pa.field("open", pa.float64()),
            pa.field("high", pa.float64()),
            pa.field("low", pa.float64()),
            pa.field("close", pa.float64()),
            pa.field("volume", pa.float64()),
            pa.field("amount", pa.float64(), nullable=True),
        ]
    )
    counts: Counter[str] = Counter()
    filled_total = 0
    for index, code in enumerate(codes, 1):
        raw_path, receipt_path = source_files[code]
        receipt = json.loads(receipt_path.read_text())
        raw = gzip.decompress(raw_path.read_bytes())
        sha = hashlib.sha256(raw).hexdigest()
        if receipt.get("code") != code or receipt.get("raw_sha256") != sha:
            raise ValueError(f"quarantine raw/receipt mismatch: {code}")
        if str(status[code]["tradestatus"]) == "0":
            payload = json.loads(raw)
            symbol = ("sh" if code.startswith("6") else "sz") + code
            if any(str(item[0]).startswith("20260922") for item in payload["data"][symbol]["m1"]):
                raise ValueError(f"suspended stock has Tencent m1 rows: {code}")
            counts["suspended_zero"] += 1
            detail = {"normalized_rows": 0, "minute_amount_status": "not_applicable_suspended"}
        else:
            rows, detail = normalize_symbol(
                code, raw, daily_volume=daily[code]["volume"], daily_close=daily[code]["close"]
            )
            temporary = bar_dir / f"{code}_1m.parquet.tmp"
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary)
            os.replace(temporary, bar_dir / f"{code}_1m.parquet")
            counts["traded_normalized"] += 1
            filled_total += len(detail["inferred_zero_volume_minutes"])
        _atomic_json(
            receipt_dir / f"{code}.json",
            {
                "stock_code": code,
                "market_date": TARGET_DATE,
                "source_raw_sha256": sha,
                "source_receipt": str(receipt_path),
                "daily_volume": daily[code]["volume"],
                "candidate_only_no_ready": True,
                **detail,
            },
        )
        if index % 500 == 0 or index == len(codes):
            print(json.dumps({"processed": index, "total": len(codes)}), flush=True)
    summary = {
        "status": "candidate_only_no_ods_ready_no_dwd",
        "market_date": TARGET_DATE,
        "universe_sha256": digest,
        "universe_count": len(codes),
        "traded_normalized": counts["traded_normalized"],
        "suspended_zero": counts["suspended_zero"],
        "normalized_rows": counts["traded_normalized"] * 240,
        "inferred_zero_volume_minutes": filled_total,
        "minute_amount_status": "source_absent_null_not_estimated",
        "source_roots": [str(root) for root in capture_roots],
    }
    _atomic_json(output / "candidate-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, action="append", required=True)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--daily-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.capture_root) != 2:
        parser.error("exactly two quarantine shard roots are required")
    print(
        json.dumps(
            build_candidate(
                capture_roots=tuple(args.capture_root),
                status_path=args.status_path,
                daily_path=args.daily_path,
                output=args.output,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
