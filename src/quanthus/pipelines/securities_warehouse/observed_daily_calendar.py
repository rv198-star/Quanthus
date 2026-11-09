"""Replay the exact retained BaoStock calendar response for closed-day delivery."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .base22 import file_sha
from .base_build_inputs import member

CONTRACT = "p10-observed-calendar-replay-v1"


def validate(root: Path, *, day: str) -> tuple[dict[str, Any], datetime]:
    body = json.loads(member(root, "calendar.json").read_bytes())
    if body.get("kind") != "cn02_actual_provider_calendar_v1":
        raise ValueError("DAILY_OBSERVED_CALENDAR_KIND_CHANGED")
    observed = datetime.fromisoformat(body["observed_at"])
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("DAILY_OBSERVED_CALENDAR_CLOCK_REQUIRED")
    dates = {}
    files = body.get("original_files")
    if not isinstance(files, dict) or not files:
        raise ValueError("DAILY_OBSERVED_CALENDAR_RAW_REQUIRED")
    for name, expected in files.items():
        p = member(root, name)
        if p.parent != root or file_sha(p) != expected:
            raise ValueError("DAILY_OBSERVED_CALENDAR_RAW_CHANGED")
        receipt = json.loads(gzip.decompress(p.read_bytes()))
        raw = base64.b64decode(receipt["raw_base64"], validate=True)
        if (
            len(raw) != receipt["raw_bytes"]
            or str(receipt["raw_sha256"]).removeprefix("sha256:") != hashlib.sha256(raw).hexdigest()
            or receipt["policy_id"] != "p10-acquisition-prevention-v1"
        ):
            raise ValueError("DAILY_OBSERVED_CALENDAR_RAW_IDENTITY_CHANGED")
        acquired = datetime.fromisoformat(receipt["warehouse_observed_at"])
        if acquired.tzinfo is None or acquired.utcoffset() is None or observed < acquired:
            raise ValueError("DAILY_OBSERVED_CALENDAR_RAW_CLOCK_CHANGED")
        sdk = json.loads(raw)
        if sdk.get("error_code") != "0" or sdk.get("fields") != ["calendar_date", "is_trading_day"]:
            raise ValueError("DAILY_OBSERVED_CALENDAR_RESPONSE_CHANGED")
        for ds, flag in sdk["rows"]:
            date.fromisoformat(ds)
            if flag not in {"0", "1"} or ds in dates and dates[ds] != (flag == "1"):
                raise ValueError("DAILY_OBSERVED_CALENDAR_CELL_CONFLICT")
            dates[ds] = flag == "1"
    if dates != body["dates"] or day not in dates or dates[day] is not False:
        raise ValueError("DAILY_OBSERVED_CALENDAR_SUMMARY_DIFFERS_FROM_RAW")
    return body, observed.astimezone(timezone.utc)


def project(root: Path, proof: dict[str, Any], output: Path | None, day: str):
    import pyarrow as pa
    import pyarrow.parquet as pq

    if proof.get("contract") != CONTRACT:
        raise ValueError("DAILY_OBSERVED_CALENDAR_CONTRACT_CHANGED")
    calendar = member(root, proof["path"])
    if file_sha(calendar) != proof["sha256"]:
        raise ValueError("DAILY_OBSERVED_CALENDAR_BINDING_CHANGED")
    body, observed = validate(calendar.parent, day=day)
    path = (
        member(root, proof["projection_path"])
        if proof.get("projection_path")
        else output / "observed-calendar.parquet"
    )
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
    rows = [
        {
            "exchange": exchange,
            "trade_date": date.fromisoformat(day),
            "is_open": 0,
            "session": "day",
            "source": "baostock.query_trade_dates",
            "built_at": observed,
        }
        for exchange in ("SSE", "SZSE")
    ]
    expected_table = pa.Table.from_pylist(rows, schema=schema)
    if proof.get("projection_path"):
        if file_sha(path) != proof["projection_sha256"] or not pq.ParquetFile(path).read().equals(
            expected_table
        ):
            raise ValueError("DAILY_OBSERVED_CALENDAR_TYPED_PROJECTION_CHANGED")
    else:
        pq.write_table(expected_table, path)
    artifact = SimpleNamespace(
        source_revision="cn02-observed-calendar@" + proof["sha256"][7:],
        sha256=file_sha(path),
        relative_path=Path("observed-calendar.parquet"),
        knowledge_cutoff=observed,
    )
    return path, artifact
