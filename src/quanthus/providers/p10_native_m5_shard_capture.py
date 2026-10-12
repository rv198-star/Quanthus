"""Bounded unadjusted native Baostock 5m originals, retaining provider strings."""

from __future__ import annotations

import gzip
import hashlib
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_delivery_common import atomic, encoded, object_at
from quanthus.providers.baostock_transport import set_socket_timeout
from quanthus.providers.p10_native_m1_shard_capture import STOCK

KIND = "p10_baostock_native_m5_raw_capture_shard"
PROVIDER = "baostock-native-5m"
FIELDS = "date,time,code,open,high,low,close,volume,amount"


def fetch_native(client: Any, code: str, day: date) -> bytes:
    vendor_code = ("sh." if code.startswith("6") else "sz.") + code
    result = client.query_history_k_data_plus(
        vendor_code,
        FIELDS,
        start_date=day.isoformat(),
        end_date=day.isoformat(),
        frequency="5",
        adjustflag="3",
    )
    rows = []
    while str(result.error_code) == "0" and result.next():
        rows.append(result.get_row_data())
        if len(rows) > 60:
            raise ValueError("native 5m response exceeds bounded single session")
    if str(result.error_code) != "0":
        raise ValueError("native 5m provider reported failure")
    return encoded(
        {
            "provider": PROVIDER,
            "code": vendor_code,
            "market_date": day.isoformat(),
            "fields": FIELDS.split(","),
            "frequency": "5",
            "adjustflag": "3",
            "raw_representation": "lossless_provider_sdk_string_fields_v1",
            "rows": rows,
        }
    )


def capture_shard(
    *,
    codes: list[str],
    market_date: date,
    market_manifest_sha: str,
    output: Path,
    shard_index: int,
    shard_size: int = 50,
    client: Any = None,
) -> dict[str, Any]:
    if (
        codes != sorted(set(codes))
        or any(not STOCK.fullmatch(code) for code in codes)
        or shard_index < 0
        or not 1 <= shard_size <= 50
        or len(market_manifest_sha) != 71
        or not market_manifest_sha.startswith("sha256:")
    ):
        raise ValueError("unqualified source universe or shard")
    selected = codes[shard_index * shard_size : (shard_index + 1) * shard_size]
    if not selected:
        raise ValueError("empty capture shard")
    identity = {
        "schema_version": 1,
        "kind": KIND,
        "market_date": market_date.isoformat(),
        "market_ods_manifest_sha256": market_manifest_sha,
        "shard_index": shard_index,
        "selected_symbols": selected,
        "provider": PROVIDER,
        "frequency": "5",
        "adjustflag": "3",
        "formal_warehouse_ready": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    if (output / "identity.json").exists():
        if object_at(output / "identity.json") != identity:
            raise ValueError("immutable 5m capture identity differs")
    else:
        atomic(output / "identity.json", identity)
    (output / "raw").mkdir(exist_ok=True)
    (output / "receipts").mkdir(exist_ok=True)
    if client is None:
        import baostock

        client = baostock
    login = client.login()
    if str(login.error_code) != "0":
        raise ValueError("native 5m login failed")
    saved = errors = 0
    try:
        if not set_socket_timeout(12):
            raise ValueError("native 5m transport lacks required finite socket timeout")
        for code in selected:
            raw_path = output / "raw" / f"{code}.json.gz"
            receipt_path = output / "receipts" / f"{code}.json"
            if receipt_path.exists():
                prior = object_at(receipt_path)
                raw = gzip.decompress(raw_path.read_bytes())
                if hashlib.sha256(raw).hexdigest() != prior["raw_sha256"]:
                    raise ValueError("immutable 5m original changed")
                saved += 1
                continue
            if raw_path.exists() or (output / "failures" / f"{code}.json").exists():
                raise ValueError("failed original is immutable; use a new attempt")
            try:
                raw = fetch_native(client, code, market_date)
                with raw_path.open("xb") as handle:
                    handle.write(gzip.compress(raw, mtime=0))
                    handle.flush()
                    os.fsync(handle.fileno())
                atomic(
                    receipt_path,
                    {
                        "stock_code": code,
                        "market_date": market_date.isoformat(),
                        "status": "raw_saved",
                        "provider": PROVIDER,
                        "sdk_version": str(getattr(client, "__version__", "unreported")),
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "source_market_ods_sha256": market_manifest_sha,
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    },
                )
                saved += 1
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                errors += 1
                atomic(
                    output / "failures" / f"{code}.json",
                    {
                        "stock_code": code,
                        "status": "source_failed",
                        "error_class": type(exc).__name__,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            time.sleep(0.1)
    finally:
        client.logout()
    result = {**identity, "saved": saved, "errors": errors, "published": False}
    atomic(output / "summary.json", result)
    return result
