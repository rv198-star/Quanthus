"""Re-evaluate exact target-day SDK captures; never synthesize observations."""

from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded
from quanthus.providers.acquisition_evidence import POLICY, verify_capture

STOCK_FIELDS = (
    "date,code,open,high,low,close,volume,amount,turn,pctChg,tradestatus,isST,"
    "peTTM,pbMRQ,psTTM,pcfNcfTTM"
)
INDEX_FIELDS = "date,code,open,high,low,close,volume,amount,pctChg"


def load(
    *,
    shadow: Path,
    day: date,
    landing: Path,
    stock_codes: set[str],
    index_codes: set[str],
    universe_digest: str,
    converter_sha256: str,
) -> dict[str, dict[str, Any]]:
    root = shadow / "attempts/market-daily-core" / str(day)
    selected: dict[str, dict[str, Any]] = {}
    read_bytes = 0
    attempts = sorted(root.iterdir()) if root.exists() else []
    for attempt in attempts[-12:]:
        old = attempt / "landing"
        if old == landing or not (old / "provider_readiness.json").exists():
            continue
        ready = json.loads((old / "provider_readiness.json").read_text())
        if ready.get("target_date") != str(day) or ready.get("ready") is not True:
            continue
        ready_at = datetime.fromisoformat(ready["checked_at"])
        if ready_at.tzinfo is None:
            raise ValueError("MARKET_REUSE_READINESS_CLOCK_NAIVE")
        for path in sorted((old / "original-responses").glob("*.json.gz")):
            read_bytes += path.stat().st_size
            if read_bytes > 512 * 1024**2 or path.stat().st_size > 16 * 1024**2:
                raise ValueError("MARKET_REUSE_IO_BUDGET_EXCEEDED")
            with gzip.open(path, "rb") as handle:
                envelope = handle.read(16 * 1024**2 + 1)
            if len(envelope) > 16 * 1024**2:
                raise ValueError("MARKET_REUSE_ENVELOPE_BUDGET_EXCEEDED")
            if digest(envelope)[7:] != path.name[:-8]:
                raise ValueError("MARKET_REUSE_ENVELOPE_IDENTITY_DIFFERS")
            value = verify_capture(path)
            req = value["request"]
            code = str(req.get("code", ""))
            fields = INDEX_FIELDS if code in index_codes else STOCK_FIELDS
            if (
                value.get("policy_id") != POLICY
                or value.get("status") != "0"
                or req.get("provider") != "baostock"
                or req.get("api") != "query_history_k_data_plus"
                or req.get("frequency") != "d"
                or str(req.get("adjustflag")) != "3"
                or req.get("fields") != fields
                or code not in stock_codes | index_codes
                or req.get("start_date", req.get("start")) != str(day)
                or req.get("end_date", req.get("end")) != str(day)
            ):
                continue
            raw = json.loads(base64.b64decode(value["raw_base64"], validate=True))
            rows = raw.get("rows", [])
            stamp = datetime.fromisoformat(value["response_completed_at"])
            if (
                stamp.tzinfo is None
                or stamp > datetime.now(timezone.utc)
                or stamp.astimezone(ZoneInfo("Asia/Shanghai")).date() < day
                or str(raw.get("error_code")) != "0"
                or raw.get("fields") != fields.split(",")
                or len(rows) != 1
                or len(rows[0]) != len(fields.split(","))
                or rows[0][0] != str(day)
                or rows[0][1] != code
                or code in stock_codes
                and stamp < ready_at
            ):
                continue
            row_hash = digest(encoded(rows))
            prior = selected.get(code)
            if (
                prior
                and prior["observed_at"] == stamp.isoformat()
                and prior["row_hash"] != row_hash
            ):
                raise ValueError("MARKET_REUSE_SAME_CLOCK_CONFLICT")
            if prior and datetime.fromisoformat(prior["observed_at"]) > stamp:
                continue
            selected[code] = {
                "rows": rows,
                "observed_at": stamp.isoformat(),
                "row_hash": row_hash,
                "source_capture": str(path),
                "raw_sha256": value["raw_sha256"],
                "capture_sha256": digest(path.read_bytes()),
            }
    target = landing / "original-responses"
    target.mkdir(parents=True, exist_ok=True)
    for value in selected.values():
        source = Path(value["source_capture"])
        dest = target / source.name
        if dest.exists():
            if digest(dest.read_bytes()) != value["capture_sha256"]:
                raise ValueError("MARKET_REUSE_CAPTURE_TARGET_DIFFERS")
        else:
            try:
                os.link(source, dest)
            except OSError:
                shutil.copy2(source, dest)
        value["receipt"] = {
            "capture_file": dest.name,
            "capture_sha256": value["capture_sha256"],
            "raw_sha256": value["raw_sha256"],
            "response_completed_at": value["observed_at"],
            "source_disposition": "verified_same_date_original_response_reuse",
        }
    atomic(
        landing / "market-raw-reuse.json",
        {
            "kind": "p10_same_date_market_raw_reuse_v1",
            "market_date": str(day),
            "policy_id": POLICY,
            "universe_digest": universe_digest,
            "converter_sha256": converter_sha256,
            "reader_sha256": digest(Path(__file__).read_bytes()),
            "stock_fields": STOCK_FIELDS,
            "index_fields": INDEX_FIELDS,
            "source_attempts_considered": len(attempts[-12:]),
            "older_attempts_not_scanned": max(0, len(attempts) - 12),
            "reused_stocks": len(stock_codes & selected.keys()),
            "reused_indices": len(index_codes & selected.keys()),
            "captures": {
                code: {k: v for k, v in value.items() if k != "rows"}
                for code, value in sorted(selected.items())
            },
            "new_source_requests": 0,
            "historical_pit_authorized": False,
        },
    )
    return selected
