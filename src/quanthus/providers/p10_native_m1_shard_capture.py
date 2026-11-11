"""P10 bounded source-native Tencent 1m T/T+1 *shard* capture.

The caller must supply a verified same-market-date daily ODS release. Each
invocation captures at most 50 symbols into an immutable attempt namespace.
A separate P10 operation runner holds the persisted phase deadline. Data
capture never makes a source ODS, research DWD or legacy READY by itself.
No synthetic trades, volumes, minute prices or provider stitching.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

from quanthus.pipelines.contracts import verify_ods_release

ENDPOINT = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"
STOCK = re.compile(r"[0-9]{6}\Z")
NATIVE_TS = re.compile(r"[0-9]{12}\Z")
Fetch = Callable[[str, str], bytes]


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("xb") as handle:
        handle.write(
            (json.dumps(data, sort_keys=True, ensure_ascii=True) + "\n").encode()
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _raw_gzip(path: Path, raw: bytes) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("xb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as zipped:
            zipped.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def target_date_records(raw: bytes, symbol: str, market_date: date) -> tuple[list[list], str]:
    """Hash *only the target day*, not Tencent's changing 800-bar rolling envelope."""
    if not STOCK.fullmatch(symbol):
        raise ValueError("invalid six-digit A-stock identity")
    alias = ("sh" if symbol.startswith("6") else "sz") + symbol
    try:
        doc = json.loads(raw)
        if doc.get("code") != 0:
            raise ValueError("source declared failure")
        items = doc["data"][alias]["m1"]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("missing exact Tencent source symbol/data") from exc
    if not isinstance(items, list):
        raise ValueError("native 1m rows are not a list")
    prefix = market_date.strftime("%Y%m%d")
    relevant: list[list] = []
    seen: set[str] = set()
    for row in items:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("malformed original native 1m row")
        stamp = str(row[0])
        if not NATIVE_TS.fullmatch(stamp) or stamp in seen:
            raise ValueError("invalid or duplicated native 1m timestamp")
        seen.add(stamp)
        if stamp.startswith(prefix):
            relevant.append(row)
    if relevant != sorted(relevant, key=lambda x: x[0]):
        raise ValueError("native original target-date rows not ordered")
    raw_days = json.dumps(
        relevant, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    return relevant, _digest(raw_days)


def raw_source_fetch(code: str, anchor: str) -> bytes:
    if not STOCK.fullmatch(code) or not re.fullmatch(r"[0-9]{12}", anchor):
        raise ValueError("invalid source symbol or historical anchor")
    alias = ("sh" if code.startswith("6") else "sz") + code
    params = parse.urlencode({"param": f"{alias},m1,{anchor},800"})
    req = request.Request(
        f"{ENDPOINT}?{params}",
        headers={"User-Agent": "Mozilla/5.0 Quanthus-P10-1m/2.0"},
    )
    with request.urlopen(req, timeout=12) as stream:
        if stream.status != 200:
            raise ValueError(f"provider HTTP status {stream.status}")
        result = stream.read(200000)
    if not result or len(result) >= 200000:
        raise ValueError("empty or excessive provider 1m response size")
    return result


def verified_daily_universe(market_release: Path, market_date: date) -> tuple[list[str], str]:
    import pyarrow.parquet as pq

    release = verify_ods_release(market_release)
    if (
        release.release.market_date != market_date
        or release.release.schedule != "daily"
    ):
        raise ValueError("source universe not bound to verified actual trading date")
    items = {a.name: a for a in release.release.artifacts}
    bar = items["daily_ohlcv_none_delta"]
    status = items["daily_status_observation_delta"]
    bars = pq.read_table(market_release / bar.relative_path, columns=["stock_code"])
    statuses = pq.read_table(market_release / status.relative_path, columns=["stock_code"])
    codes = [str(c) for c in bars.column("stock_code").to_pylist()]
    states = [str(c) for c in statuses.column("stock_code").to_pylist()]
    if (
        len(codes) < 100
        or len(codes) != len(states)
        or len(codes) != len(set(codes))
        or set(codes) != set(states)
        or any(not STOCK.fullmatch(code) for code in codes)
    ):
        raise ValueError("duplicate/invalid daily-core security population")
    return sorted(codes), release.manifest_sha256


def capture_shard(
    *,
    codes: list[str],
    market_date: date,
    market_manifest_sha: str,
    output: Path,
    shard_index: int,
    shard_size: int = 50,
    fetcher: Fetch = raw_source_fetch,
) -> dict[str, Any]:
    if (
        not market_manifest_sha.startswith("sha256:")
        or len(market_manifest_sha) != 71
        or shard_index < 0
        or not 1 <= shard_size <= 50
        or any(not STOCK.fullmatch(code) for code in codes)
        or codes != sorted(set(codes))
    ):
        raise ValueError("unverified source universe or unsafe shard parameters")
    selections = codes[shard_index * shard_size:(shard_index + 1) * shard_size]
    if not selections:
        raise ValueError("shard is outside daily-core source universe")
    identity = {
        "schema_version": 2,
        "kind": "p10_tencent_native_m1_raw_capture_shard",
        "market_date": market_date.isoformat(),
        "market_ods_manifest_sha256": market_manifest_sha,
        "shard_index": shard_index,
        "selected_symbols": selections,
        "expected_rows_are_native_only": True,
        "formal_warehouse_ready": False,
        "provider": "tencent-mkline-m1",
        "anchor": market_date.strftime("%Y%m%d") + "2359",
    }
    output.mkdir(parents=True, exist_ok=True)
    if (output / "identity.json").exists():
        if json.loads((output / "identity.json").read_text()) != identity:
            raise ValueError("cannot append to a different original shard identity")
    else:
        _write(output / "identity.json", identity)
    (output / "raw").mkdir(exist_ok=True)
    (output / "receipts").mkdir(exist_ok=True)
    saved = 0
    failures = 0
    unchanged = 0
    for code in selections:
        receipt_file = output / "receipts" / f"{code}.json"
        raw_file = output / "raw" / f"{code}.json.gz"
        if receipt_file.exists():
            if not raw_file.is_file():
                raise ValueError("source receipt missing its original raw response")
            old = json.loads(receipt_file.read_text())
            prior_raw = gzip.decompress(raw_file.read_bytes())
            if old["raw_sha256"] != _digest(prior_raw):
                raise ValueError("immutable source evidence SHA differs")
            entries, dated_sha = target_date_records(prior_raw, code, market_date)
            if old["target_date_observation_sha256"] != dated_sha:
                raise ValueError("target-day original source digest differs")
            saved += 1
            unchanged += 1
            continue
        if raw_file.exists() or (output / "failures" / f"{code}.json").exists():
            raise ValueError(
                "unfinished original source/failure evidence exists; "
                "use a new versioned retry attempt, never overwrite history"
            )
        try:
            raw = fetcher(code, identity["anchor"])
            observed, observed_sha = target_date_records(raw, code, market_date)
            if not observed:
                raise ValueError("source has no real rows for specified trading date")
            _raw_gzip(raw_file, raw)
            _write(
                receipt_file,
                {
                    "stock_code": code,
                    "market_date": market_date.isoformat(),
                    "status": "raw_saved",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "source_market_ods_sha256": market_manifest_sha,
                    "raw_sha256": _digest(raw),
                    "native_target_rows": len(observed),
                    "target_date_observation_sha256": observed_sha,
                    "provider": identity["provider"],
                },
            )
            saved += 1
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            failures += 1
            # A failed request never writes unqualified source bars to formal storage.
            _write(
                output / "failures" / f"{code}.json",
                {"stock_code": code, "status": "source_failed",
                 "error_class": type(exc).__name__,
                 "observed_at": datetime.now(timezone.utc).isoformat()},
            )
        time.sleep(0.18)
    summary = {
        **identity,
        "saved": saved,
        "errors": failures,
        "already_captured": unchanged,
        "source_response_count": len(selections),
        "published": False,
    }
    _write(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--market-ods-release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-size", type=int, default=50)
    parser.add_argument("--codes-file", type=Path)
    args = parser.parse_args()
    codes, manifest_sha = verified_daily_universe(
        args.market_ods_release, args.market_date
    )
    if args.codes_file:
        subset = json.loads(args.codes_file.read_text())
        if (
            not isinstance(subset, list)
            or len(subset) < 1
            or len(subset) > len(codes)
            or len(set(subset)) != len(subset)
            or not set(subset) <= set(codes)
        ):
            raise ValueError("bounded canary code selection outside verified daily source")
        codes = sorted(subset)
    outcome = capture_shard(
        codes=codes, market_date=args.market_date,
        market_manifest_sha=manifest_sha, output=args.output,
        shard_index=args.shard_index, shard_size=args.shard_size,
    )
    print(json.dumps(outcome, sort_keys=True))
    return 0 if outcome["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
