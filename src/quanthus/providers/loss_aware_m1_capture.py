"""Bounded capture of original Tencent 1m responses into immutable, non-publishing quarantine.

A capture is NOT a qualified 1m dataset, ODS READY or DWD handoff. In particular,
Tencent m1 may be sparse or contain unfinalized current-session whole-lot volumes.
GitHub-tracked tool source; source bytes, SHA, dates, requests and failures are retained.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import verify_ods_release

ENDPOINT = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"
SHA = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_DATES = ("2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08")


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("xb") as handle:
        handle.write(_canonical(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def read_universe(release_root: Path) -> tuple[list[str], dict[str, str]]:
    """Verify a real market ODS READY and its date-bound physical source artifacts."""
    import pyarrow.parquet as pq

    v = verify_ods_release(release_root)
    if v.release.market_date.isoformat() != "2026-09-24" or v.release.schedule != "daily":
        raise ValueError("capture universe must bind the immutable 2026-09-24 market ODS")
    arts = {x.name: x for x in v.release.artifacts}
    daily = arts["daily_ohlcv_none_delta"]
    path = release_root / daily.relative_path
    names = [str(row["stock_code"]) for row in pq.read_table(path, columns=["stock_code"]).to_pylist()]
    if len(names) != 4992 or len(set(names)) != 4992:
        raise ValueError("unexpected market-universe size or duplicated securities")
    if any(re.fullmatch(r"[0-9]{6}", x) is None for x in names):
        raise ValueError("non-security identity in sealed universe")
    return sorted(names), {
        "market_date": v.release.market_date.isoformat(),
        "release_manifest_sha256": v.manifest_sha256,
        "market_source_sha256": daily.sha256,
        "universe_sha256": hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest(),
    }


def parse_source(raw: bytes, code: str, required_dates: tuple[str, ...]) -> dict[str, int]:
    doc = json.loads(raw)
    symbol = ("sh" if code.startswith("6") else "sz") + code
    if doc.get("code") != 0:
        raise ValueError("Tencent provider returned a nonzero error code")
    rows = doc.get("data", {}).get(symbol, {}).get("m1")
    if not isinstance(rows, list):
        raise ValueError("Tencent m1 list absent")
    wanted = {d.replace("-", "") for d in required_dates}
    counts = {d: 0 for d in required_dates}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("malformed source row")
        ts = str(row[0])
        if ts in seen:
            raise ValueError("duplicated raw minute stamp")
        seen.add(ts)
        if ts[:8] in wanted:
            counts[f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"] += 1
    return counts


def capture(
    *,
    release_root: Path,
    output: Path,
    window: str,
    anchor: str,
    dates: tuple[str, ...],
    max_symbols: int,
    min_interval: float,
    budget_seconds: float,
    max_errors: int = 20,
) -> dict[str, Any]:
    import requests

    if not set(dates) <= set(EXPECTED_DATES) or not dates:
        raise ValueError("out-of-scope date set")
    if anchor != "latest" and not re.fullmatch(r"20260928[0-9]{4}", anchor):
        raise ValueError("anchor must be latest or bounded 2026-09-28 timestamp")
    if not 1 <= max_symbols <= 4992 or not 0.18 <= min_interval <= 3:
        raise ValueError("invalid limits")
    if budget_seconds <= 0 or max_errors < 1:
        raise ValueError("invalid budget")
    codes, source = read_universe(release_root)
    codes = codes[:max_symbols]
    identity = {
        "schema_version": 1,
        "kind": "quanthus_tencent_native_m1_raw_quarantine",
        "purpose": "non_publishing_original_source_only",
        "window": window,
        "anchor": anchor,
        "dates": list(dates),
        "provider": "tencent_mkline_m1",
        "source_universe": source,
        "codes_count": len(codes),
        "codes_sha256": hashlib.sha256("\n".join(codes).encode()).hexdigest(),
        "api_endpoint": ENDPOINT,
        "max_provider_bars": 800,
    }
    output.mkdir(parents=True, exist_ok=True)
    lockfile = output / ".writer.lock"
    with lockfile.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = output / "identity.json"
        if path.exists():
            if json.loads(path.read_text()) != identity:
                raise ValueError("different capture identity at existing path")
        else:
            _write(path, identity)
        (output / "raw").mkdir(exist_ok=True)
        (output / "receipts").mkdir(exist_ok=True)
        session = requests.Session()
        last_request = 0.0
        started = time.monotonic()
        errors = 0
        finished = 0
        per_date = {d: 0 for d in dates}
        stopped = "complete"
        for code in codes:
            receipt_path = output / "receipts" / f"{code}.json"
            raw_path = output / "raw" / f"{code}.json.gz"
            if receipt_path.exists():
                prior = json.loads(receipt_path.read_text())
                if prior.get("status") == "raw_saved":
                    if not raw_path.is_file():
                        raise ValueError("success receipt lacks original source raw")
                    raw = gzip.decompress(raw_path.read_bytes())
                    if hashlib.sha256(raw).hexdigest() != prior["raw_sha256"]:
                        raise ValueError("source raw changed after receipt")
                    counts = parse_source(raw, code, dates)
                    if counts != prior["native_day_row_counts"]:
                        raise ValueError("replayed raw no longer agrees with receipt")
                    finished += 1
                    for day, count in counts.items():
                        per_date[day] += int(count > 0)
                    continue
            if time.monotonic() - started > budget_seconds:
                stopped = "bounded_wall_budget_exhausted"
                break
            symbol = ("sh" if code.startswith("6") else "sz") + code
            query = f"{symbol},m1,{'' if anchor == 'latest' else anchor},800"
            receipt: dict[str, Any] = {
                "stock_code": code, "provider": "tencent_mkline_m1",
                "request_param": query, "dates": list(dates),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "status": "source_error",
            }
            for trial in (1, 2):
                try:
                    elapsed = time.monotonic() - last_request
                    if last_request and elapsed < min_interval:
                        time.sleep(min_interval - elapsed)
                    last_request = time.monotonic()
                    resp = session.get(ENDPOINT, params={"param": query}, timeout=12)
                    resp.raise_for_status()
                    raw = resp.content
                    counts = parse_source(raw, code, dates)
                    receipt.update({
                        "status": "raw_saved",
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "raw_bytes": len(raw),
                        "native_day_row_counts": counts,
                        "attempts": trial,
                    })
                    temp = raw_path.with_name(f".{code}.{os.getpid()}.tmp")
                    with temp.open("xb") as stream:
                        with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped:
                            zipped.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp, raw_path)
                    finished += 1
                    for day, count in counts.items():
                        per_date[day] += int(count > 0)
                    break
                except (requests.RequestException, ValueError, KeyError) as exc:
                    receipt["error"] = f"{type(exc).__name__}: {str(exc)[:250]}"
                    receipt["attempts"] = trial
                    if trial == 1:
                        time.sleep(1.0)
            _write(receipt_path, receipt)
            if receipt["status"] != "raw_saved":
                errors += 1
            if (finished + errors) % 100 == 0:
                print(json.dumps({"captured": finished, "errors": errors, "per_date_rows_present": per_date, "elapsed_seconds": round(time.monotonic() - started, 1)}, sort_keys=True), flush=True)
            if errors >= max_errors:
                stopped = "provider_error_circuit_breaker"
                break
        session.close()
        summary = {
            **identity,
            "status": stopped,
            "saved_raw_symbols": finished,
            "source_errors": errors,
            "dates_with_native_rows": per_date,
            "total_requested": len(codes),
            "remaining_without_success": len(codes) - finished,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "publisher_authorized": False,
            "formal_strict_1m_qualified": False,
        }
        _write(output / "capture-summary.json", summary)
        return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--date", action="append", dest="dates", required=True)
    parser.add_argument("--max-symbols", type=int, default=4992)
    parser.add_argument("--min-interval", type=float, default=0.18)
    parser.add_argument("--budget-seconds", type=float, default=4000)
    parser.add_argument("--max-errors", type=int, default=20)
    args = parser.parse_args(argv)
    result = capture(
        release_root=args.release_root, output=args.output, window=args.window,
        anchor=args.anchor, dates=tuple(args.dates),
        max_symbols=args.max_symbols, min_interval=args.min_interval,
        budget_seconds=args.budget_seconds, max_errors=args.max_errors,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "complete" and result["source_errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
