"""Build an immutable 2026-09-22 lane seed from verified Tencent m1 evidence.

This does not create an ODS release. The source has no minute turnover: every
amount remains null and that limitation is made explicit in the seed receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.providers.tencent_m1_normalize import TARGET_DATE, expected_times


def _number(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite numeric value")
    return result


def validate_symbol_rows(
    code: str, rows: list[dict[str, Any]], *, daily_volume: Any, daily_close: Any
) -> None:
    expected = [datetime.strptime(ts, "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M:00") for ts in expected_times()]
    if len(rows) != 240 or [str(row.get("ts")) for row in rows] != expected:
        raise ValueError(f"invalid 240-bar session grid for {code}")
    total = Decimal(0)
    for row in rows:
        if row.get("stock_code") != code or row.get("amount") is not None:
            raise ValueError(f"source identity or nullable amount violation for {code}")
        low, high = _number(row["low"]), _number(row["high"])
        opening, closing = _number(row["open"]), _number(row["close"])
        volume = _number(row["volume"])
        if volume < 0 or low > min(opening, closing, high) or high < max(opening, closing, low):
            raise ValueError(f"invalid OHLCV row for {code}")
        total += volume
    if abs(total - _number(daily_volume)) > Decimal("0.01"):
        raise ValueError(f"daily volume mismatch for {code}")
    if abs(_number(rows[-1]["close"]) - _number(daily_close)) > Decimal("0.011"):
        raise ValueError(f"daily close mismatch for {code}")


def build_seed(
    *, candidate: Path, status_path: Path, daily_path: Path, output: Path
) -> dict[str, Any]:
    import pyarrow.parquet as pq

    if output.exists():
        raise ValueError(f"seed output already exists: {output}")
    summary = json.loads((candidate / "candidate-summary.json").read_text())
    if (
        summary.get("status") != "candidate_only_no_ods_ready_no_dwd"
        or summary.get("market_date") != TARGET_DATE
        or summary.get("universe_count") != 4992
        or summary.get("traded_normalized") != 4983
        or summary.get("suspended_zero") != 9
        or summary.get("normalized_rows") != 4983 * 240
        or summary.get("minute_amount_status") != "source_absent_null_not_estimated"
    ):
        raise ValueError("candidate summary does not match the bounded recovery")
    statuses = pq.read_table(status_path, columns=["stock_code", "market_date", "tradestatus"]).to_pylist()
    dailies = pq.read_table(daily_path, columns=["stock_code", "market_date", "volume", "close"]).to_pylist()
    status = {row["stock_code"]: row for row in statuses}
    daily = {row["stock_code"]: row for row in dailies}
    codes = sorted(status)
    digest = hashlib.sha256("\n".join(codes).encode()).hexdigest()
    if (
        len(codes) != 4992
        or len(statuses) != 4992
        or len(dailies) != 4992
        or set(status) != set(daily)
        or summary.get("universe_sha256") != digest
        or any(row["market_date"] != TARGET_DATE for row in statuses + dailies)
    ):
        raise ValueError("sealed universe/date mismatch")

    output.mkdir(parents=True)
    checkpoint: dict[str, dict[str, Any]] = {}
    traded = suspended = 0
    for index, code in enumerate(codes, 1):
        receipt = json.loads((candidate / "receipts" / f"{code}.json").read_text())
        if (
            receipt.get("stock_code") != code
            or receipt.get("market_date") != TARGET_DATE
            or receipt.get("candidate_only_no_ready") is not True
        ):
            raise ValueError(f"candidate receipt mismatch for {code}")
        raw_receipt = Path(str(receipt["source_receipt"]))
        source = json.loads(raw_receipt.read_text())
        raw_path = raw_receipt.parent.parent / "raw" / f"{code}.json.gz"
        import gzip

        raw_sha = hashlib.sha256(gzip.decompress(raw_path.read_bytes())).hexdigest()
        if (
            source.get("code") != code
            or source.get("raw_sha256") != raw_sha
            or receipt.get("source_raw_sha256") != raw_sha
        ):
            raise ValueError(f"raw source hash mismatch for {code}")
        candidate_file = candidate / "research_1m" / f"{code}_1m.parquet"
        if str(status[code]["tradestatus"]) == "0":
            if candidate_file.exists() or receipt.get("normalized_rows") != 0:
                raise ValueError(f"suspended stock has candidate bars: {code}")
            row_count = 0
            suspended += 1
        else:
            rows = pq.read_table(candidate_file).to_pylist()
            validate_symbol_rows(code, rows, daily_volume=daily[code]["volume"], daily_close=daily[code]["close"])
            destination = output / f"{code}_1m.parquet"
            shutil.copyfile(candidate_file, destination)
            if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(candidate_file.read_bytes()).digest():
                raise ValueError(f"candidate copy hash mismatch for {code}")
            row_count = 240
            traded += 1
        checkpoint[code] = {
            "status": "done",
            "path": str(output / f"{code}_1m.parquet"),
            "rows_file": row_count,
            "provider": "tencent-mkline-raw-normalized",
            "native_frequency": "1m",
            "derived_from": None,
            "market_date": TARGET_DATE,
            "source_raw_sha256": raw_sha,
            "minute_amount_status": "source_absent_null_not_estimated",
        }
        if index % 500 == 0 or index == len(codes):
            print(json.dumps({"validated": index, "total": len(codes)}), flush=True)
    if (traded, suspended) != (4983, 9):
        raise ValueError("seed trade-status count mismatch")
    now = datetime.now(timezone.utc).isoformat()
    (output / "checkpoint.json").write_text(json.dumps({"symbols": checkpoint, "updated_at": now}, sort_keys=True))
    status_receipt = {
        "state": "done",
        "phase": "finished",
        "layer": "research",
        "freq": "1m",
        "mode": "incr",
        "server": "tencent-mkline-quarantine-normalized",
        "market_date": TARGET_DATE,
        "total": 4992,
        "done": 4992,
        "error": 0,
        "traded": traded,
        "justified_no_data": suspended,
        "rows": traded * 240,
        "minute_amount_status": "source_absent_null_not_estimated",
        "candidate_summary_sha256": hashlib.sha256((candidate / "candidate-summary.json").read_bytes()).hexdigest(),
        "updated_at": now,
    }
    (output / "status.json").write_text(json.dumps(status_receipt, sort_keys=True))
    return status_receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--daily-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_seed(candidate=args.candidate, status_path=args.status_path, daily_path=args.daily_path, output=args.output), sort_keys=True))


if __name__ == "__main__":
    main()
