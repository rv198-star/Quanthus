"""Exact scoped August index observations, without consumer or calendar admission."""

from __future__ import annotations

import struct
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_outputs import acquisition, decimal_observation

TABLE = "dwd_index_bar"
CONTRACT_ID = "p10-base22-index-observation-v1"


def index_observation(
    raw: dict[str, Any],
    *,
    index: dict[str, Any],
    market_date: date,
    source_sha256: str,
    revision: str,
    schema: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep exact native identity, source clocks and retained numeric information.

    The existing index model permits an unavailable calendar_session_id; this
    projection does not invent an index venue or apply listing/open-close rules.
    Float input retains a bit-exact round trip, while its unavailable original
    provider Decimal remains explicitly unavailable. No quantity is rounded to
    make a schema fit, and every raw member is retained separately.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    day = date.fromisoformat(raw["date"])
    start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai"))
    clock = _clock(raw.get("acquired_at"))
    if day != market_date or day > THROUGH or raw["index_code"] != index["index_code"]:
        raise BoundedError("INDEX_SOURCE_DATE_OR_IDENTITY_MISMATCH")
    if clock < start:
        raise BoundedError("INDEX_SOURCE_ACQUISITION_BEFORE_OBSERVATION")
    knowledge = acquisition(raw, index["knowledge_from"])
    if (
        index["effective_from"] > start
        or index["effective_to"] is not None
        and start >= index["effective_to"]
        or index["knowledge_to"] is not None
        and knowledge >= index["knowledge_to"]
    ):
        raise BoundedError("INDEX_DATED_PARENT_CLOSURE_FAILED")
    row = {
        "record_source": "base22_august/index_daily_delta",
        "source_record_id": f"index_code={raw['index_code']}&date={day}",
        "source_revision": revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": knowledge,
        "knowledge_to": None,
        "observation_start": start,
        "observation_end": start + timedelta(days=1),
        "index_id": index["index_id"],
        "calendar_session_id": None,
        "interval_size": "1d",
        "currency": index["base_currency"],
        **{f: decimal_observation(raw[f]) for f in ("open", "high", "low", "close")},
        "volume": decimal_observation(raw.get("volume"), nullable=True),
        "turnover": decimal_observation(raw.get("amount"), nullable=True),
    }
    restored = pa.Table.from_pylist([row], schema=schema).to_pylist()[0]
    if restored != row:
        raise BoundedError("INDEX_TYPED_VALUE_CHANGED")
    classes: Counter[str] = Counter()
    for original, target in [(f, f) for f in ("open", "high", "low", "close", "volume")] + [
        ("amount", "turnover")
    ]:
        value = raw.get(original)
        if isinstance(value, float):
            if struct.pack("!d", value) != struct.pack("!d", float(restored[target])):
                raise BoundedError("INDEX_RETAINED_FLOAT_INFORMATION_CHANGED")
            classes["FLOAT_ORIGINAL_PROVIDER_DECIMAL_UNAVAILABLE"] += 1
        else:
            classes["NULL" if value is None else "EXACT_" + type(value).__name__] += 1
        if restored[target] is not None and restored[target] == 0:
            classes["ZERO"] += 1
    return restored, {
        "contract_id": CONTRACT_ID,
        "raw_row_sha256": digest(raw),
        "typed_row_sha256": digest(restored),
        "parent_row_sha256": digest(index),
        "source_acquisition_instant": clock,
        "knowledge_from": knowledge,
        "numeric_classifications": dict(classes),
        "publication_available": False,
        "provider_acquisition_qualified": False,
        "calendar_link": "UNAVAILABLE_AS_PER_NATIVE_INDEX_MODEL",
        "consumer_admission": "NOT_GRANTED",
    }
