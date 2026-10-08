"""Bounded, resumable same-vendor Tencent native 5m witnesses for sparse m1 only.

Only candidates that fail R0 solely for 1..5 noncritical missing SOURCE minutes
may be queried. This is evidence collection, never 1m bar manufacture.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from quanthus.pipelines.loss_aware_m1_research import (
    _archived_source_ledger,
    _native_raw,
    _reference,
)
from quanthus.providers.loss_aware_m1 import _source_rows, assess, verify_native_m5

ENDPOINT = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    with tmp.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def collect(
    *,
    day: str,
    candidates: Path,
    source_root: Path,
    source_mode: str,
    market_release: Path,
    output: Path,
    reuse_root: Path | None,
    min_interval: float = 0.18,
    budget_seconds: float = 900.0,
) -> dict[str, object]:
    import requests

    obj = json.loads(candidates.read_text())
    codes = obj.get("codes")
    if (
        obj.get("market_date") != day
        or not isinstance(codes, list)
        or len(codes) > 500
        or len(set(codes)) != len(codes)
        or any(not isinstance(c, str) or len(c) != 6 or not c.isdigit() for c in codes)
    ):
        raise ValueError("source-native missing-minute witness candidate identity differs")
    if min_interval < 0.18 or budget_seconds <= 0:
        raise ValueError("invalid bounded witness request budget")
    dailies, daily_sha, states = _reference(market_release, day)
    if not set(codes) <= set(dailies):
        raise ValueError("witness candidates outside verified daily universe")
    source_ledger = _archived_source_ledger() if source_mode == "archive" else None
    identity = {
        "kind": "quanthus_source_native_5m_witness_quarantine",
        "schema_version": 1,
        "market_date": day,
        "source_provider": "tencent_mkline_m5",
        "candidate_file_sha256": hashlib.sha256(candidates.read_bytes()).hexdigest(),
        "market_source_manifest_sha256": daily_sha,
        "codes": codes,
        "purpose": "only_1m_sparse_cross_frequency_witness_no_1m_ohlc_inference",
    }
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".writer.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output / "identity.json").exists():
            if json.loads((output / "identity.json").read_text()) != identity:
                raise ValueError("reused directory changed source/date/contract identity")
        else:
            _write(output / "identity.json", identity)
        (output / "receipts").mkdir(exist_ok=True)
        begin = time.monotonic()
        session = requests.Session()
        last = 0.0
        verified = 0
        failed = 0
        reused = 0
        deadline = False
        for code in codes:
            raw, expected_sha, comparison, acquired = _native_raw(
                source_root, code, day=day, mode=source_mode, ledger=source_ledger
            )
            if raw is None:
                failed += 1
                continue
            grade = assess(
                symbol=code,
                day=day,
                raw=raw,
                shares=dailies[code]["volume"],
                close=dailies[code]["close"],
                traded=states[code] != "0",
                cross_source_status=comparison,
            )
            if grade.reason != "same_vendor_native_5m_witness_pending":
                raise ValueError(f"not a bounded R1 candidate: {code}/{grade.reason}")
            m1, _ = _source_rows(raw, code, day, period="m1")
            path = output / f"{code}.json.gz"
            receipt_path = output / "receipts" / f"{code}.json"
            existing = None
            if path.is_file():
                existing = path
            elif reuse_root is not None:
                hits = list(reuse_root.rglob(f"{code}-raw-0.json.gz"))
                if hits:
                    existing = hits[0]
            tries = 0
            if existing is None and time.monotonic() - begin <= budget_seconds:
                name = ("sh" if code.startswith("6") else "sz") + code
                request = f"{name},m5,,320"
                for tries in (1, 2):
                    try:
                        elapsed = time.monotonic() - last
                        if last and elapsed < min_interval:
                            time.sleep(min_interval - elapsed)
                        last = time.monotonic()
                        response = session.get(ENDPOINT, params={"param": request}, timeout=12)
                        response.raise_for_status()
                        response_raw = response.content
                        verify_native_m5(
                            m1=m1,
                            m5_raw=response_raw,
                            symbol=code,
                            day=day,
                            daily_shares=__import__("decimal").Decimal(
                                str(dailies[code]["volume"])
                            ),
                            daily_close=__import__("decimal").Decimal(str(dailies[code]["close"])),
                        )
                        temp = path.with_name(f".{code}.{os.getpid()}.tmp")
                        with temp.open("xb") as stream:
                            with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped:
                                zipped.write(response_raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temp, path)
                        existing = path
                        break
                    except (requests.RequestException, ValueError, KeyError):
                        if tries == 1:
                            time.sleep(1)
            if existing is not None:
                try:
                    m5 = gzip.decompress(existing.read_bytes())
                    sha = verify_native_m5(
                        m1=m1,
                        m5_raw=m5,
                        symbol=code,
                        day=day,
                        daily_shares=__import__("decimal").Decimal(str(dailies[code]["volume"])),
                        daily_close=__import__("decimal").Decimal(str(dailies[code]["close"])),
                    )
                    if existing != path:
                        with path.open("xb") as f:
                            f.write(existing.read_bytes())
                            f.flush()
                            os.fsync(f.fileno())
                        reused += 1
                    _write(
                        receipt_path,
                        {
                            "code": code,
                            "status": "48_bucket_verified",
                            "market_date": day,
                            "m1_source_sha256": expected_sha,
                            "m5_raw_sha256": sha,
                            "source": "reused_existing_same_vendor_m5"
                            if existing != path
                            else "fresh_or_resumed_native_m5",
                            "source_acquired_after": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    verified += 1
                except (OSError, ValueError, KeyError) as exc:
                    _write(
                        receipt_path,
                        {"code": code, "status": "unverified_native_m5", "reason": str(exc)[:180]},
                    )
                    failed += 1
            else:
                _write(
                    receipt_path,
                    {"code": code, "status": "not_acquired_or_not_conserved", "attempts": tries},
                )
                failed += 1
            if (verified + failed) % 25 == 0:
                print(
                    json.dumps(
                        {"verified": verified, "failed": failed, "reused": reused}, sort_keys=True
                    ),
                    flush=True,
                )
            if time.monotonic() - begin > budget_seconds:
                deadline = True
                break
            if failed >= 20:
                break
        session.close()
        summary = {
            **identity,
            "verified": verified,
            "failed": failed,
            "reused": reused,
            "budget_exhausted": deadline,
            "total": len(codes),
            "dwd_1m_published": False,
        }
        _write(output / "summary.json", summary)
        return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--day", required=True)
    p.add_argument("--candidate-file", type=Path, required=True)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--source-mode", choices=("archive", "capture"), required=True)
    p.add_argument("--market-release", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reuse-root", type=Path)
    p.add_argument("--budget-seconds", type=float, default=900)
    x = p.parse_args(argv)
    result = collect(
        day=x.day,
        candidates=x.candidate_file,
        source_root=x.source_root,
        source_mode=x.source_mode,
        market_release=x.market_release,
        output=x.output,
        reuse_root=x.reuse_root,
        budget_seconds=x.budget_seconds,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["verified"] == result["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
