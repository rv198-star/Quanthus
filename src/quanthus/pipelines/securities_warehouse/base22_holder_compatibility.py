"""Qualify a retained exact capture against a legacy binary holder observation.

Matching binary values proves compatibility at the old representation's resolution.
It never recovers the vendor's original decimal token, publication or acquisition.
A caller can use the qualified capture in a separately frozen new governance revision;
this function cannot select an old overwrite, backdate knowledge or grant admission.
"""

from __future__ import annotations

import math
import struct
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .ownership_capital import _raw_number

CONTRACT_ID = "p10-base22-legacy-holder-binary-compatibility-v1"


def _binary(value: Any, *, legacy: bool) -> bytes | None:
    if legacy:
        if value is None or isinstance(value, float) and math.isnan(value):
            return None
        if not isinstance(value, float) or not math.isfinite(value):
            raise BoundedError("HOLDER_COMPATIBILITY_LEGACY_BINARY_REQUIRED")
        number = value
    else:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise BoundedError("HOLDER_COMPATIBILITY_EXACT_TOKEN_REQUIRED")
        try:
            exact = Decimal(value)
            number = float(exact)
        except (ValueError, InvalidOperation, OverflowError):
            raise BoundedError("HOLDER_COMPATIBILITY_EXACT_TOKEN_INVALID") from None
        if not exact.is_finite() or not math.isfinite(number):
            raise BoundedError("HOLDER_COMPATIBILITY_EXACT_TOKEN_INVALID")
    return struct.pack("!d", number)


def qualify_legacy_capture(
    before_raw: dict[str, Any], before: dict[str, Any], capture: dict[str, Any]
) -> dict[str, Any]:
    """Exact source key/body linkage plus same-provider binary compatibility.

    Sources and full raw file hashes are independently frozen/verified by the owner.
    Do not interpret fetched_at, binary equality or this receipt as historical PIT.
    """
    try:
        day = date.fromisoformat(before_raw["end_date"][:10])
        incoming_day = date.fromisoformat(capture["end_date"][:10])
        key = f"stock_code={before_raw['code']}&period_end={day.isoformat()}"
        count = before_raw["shareholder_count"]
        if (
            not isinstance(count, float)
            or not math.isfinite(count)
            or not count.is_integer()
            or not isinstance(capture["shareholder_count"], str)
            or Decimal(capture["shareholder_count"]) != Decimal(str(count))
            or before["holder_count"] != int(count)
            or before["raw_holder_count"] != _raw_number(count)
            or before["raw_average_holding"] != _raw_number(before_raw.get("avg_hold_num"))
        ):
            raise BoundedError("HOLDER_COMPATIBILITY_BEFORE_RAW_OR_COUNT_MISMATCH")
        if (
            before_raw["code"] != capture["code"]
            or day != incoming_day
            or before["period_end"] != day
            or before["source_record_id"] != key
            or before_raw["source"] != capture["source"]
            or before["record_source"] != before_raw["source"]
        ):
            raise BoundedError("HOLDER_COMPATIBILITY_IDENTITY_OR_PROVIDER_MISMATCH")
        if _binary(before_raw.get("avg_hold_num"), legacy=True) != _binary(
            capture.get("avg_hold_num"), legacy=False
        ):
            raise BoundedError("HOLDER_COMPATIBILITY_DIFFERENT_BINARY_VALUE")
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise BoundedError("HOLDER_COMPATIBILITY_INPUT_INVALID") from None
    result = {
        "contract_id": CONTRACT_ID,
        "before_raw_sha256": digest(before_raw),
        "before_row_sha256": digest(before),
        "capture_raw_sha256": digest(capture),
        "source_record_key_sha256": digest(key),
        "same_provider": True,
        "same_holder_integer": True,
        "same_average_binary_bits": True,
        "old_provider_original_precision": "UNKNOWN",
        "old_provider_acquisition": "UNKNOWN",
        "historical_publication_or_PIT": "NOT_QUALIFIED",
        "overwrite_old_body": False,
        "knowledge_time_assigned": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
