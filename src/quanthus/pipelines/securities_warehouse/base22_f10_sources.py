"""Bind retained F10 categories to the original collector's exact receipt protocol.

No provider request is made. A closed selected response is separate from historical
universe coverage, old normalization restoration and consumer admission.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock

CONTRACT_ID = "p10-base22-retained-f10-receipt-closure-v1"
MAX_CATEGORIES = 128
MAX_RESPONSE_BYTES = 16 * 1024**2
_CODE = re.compile(r"\d{6}")


def bind_f10_receipt(
    rows: list[dict[str, Any]],
    receipt: dict[str, Any],
    *,
    market_date: date,
    observed_at: Any,
) -> dict[str, Any]:
    """Recompute ordered concatenation, count and byte size used by collect_f10.

    The serialized category acquired_at is preserved as an original observation.
    It is not substituted for the collector's later completion receipt. New
    qualification also cannot claim knowledge before this actual observation.
    """
    code = receipt.get("security_id")
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise BoundedError("F10_RECEIPT_ID_INVALID")
    if receipt.get("dataset") != "raw_f10":
        raise BoundedError("F10_RECEIPT_DATASET_INVALID")
    try:
        request = json.loads(receipt["request_json"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BoundedError("F10_RECEIPT_REQUEST_INVALID") from exc
    if request != {"operation": "F10C+F10", "symbol": code}:
        raise BoundedError("F10_RECEIPT_REQUEST_INVALID")
    if len(rows) > MAX_CATEGORIES:
        raise BoundedError("F10_RESPONSE_CATEGORY_BOUND_EXCEEDED")
    completion = _clock(receipt.get("acquired_at"))
    observed = _clock(observed_at)
    if observed < completion:
        raise BoundedError("F10_OBSERVATION_PRECEDES_RECEIPT")
    sha = hashlib.sha256()
    size = 0
    categories: set[str] = set()
    originals = []
    for row in rows:
        if (
            row.get("security_id") != code
            or row.get("stock_code") != code
            or row.get("market_date") != market_date.isoformat()
            or row.get("source") != "tdx.get_company_info_content"
            or row.get("source_revision") != "quanthus-fundamentals-provider-v1"
        ):
            raise BoundedError("F10_RAW_ID_DATE_SOURCE_REVISION_MISMATCH")
        category = row.get("category")
        content = row.get("content")
        if not isinstance(category, str) or not category or category in categories:
            raise BoundedError("F10_RAW_CATEGORY_INVALID_OR_DUPLICATE")
        if not isinstance(content, str) or not content.strip():
            raise BoundedError("F10_RAW_CONTENT_INVALID")
        data = content.encode("utf-8")
        if hashlib.sha256(data).hexdigest() != str(row.get("content_sha256")).removeprefix(
            "sha256:"
        ):
            raise BoundedError("F10_RAW_CONTENT_HASH_MISMATCH")
        size += len(data)
        if size > MAX_RESPONSE_BYTES:
            raise BoundedError("F10_RESPONSE_BYTE_BOUND_EXCEEDED")
        clock = _clock(row.get("acquired_at"))
        if clock > completion:
            raise BoundedError("F10_CATEGORY_OBSERVATION_AFTER_RECEIPT")
        sha.update(data)
        categories.add(category)
        originals.append({"category": category, "raw_row_sha256": digest(row),
                          "content_sha256": hashlib.sha256(data).hexdigest(),
                          "bytes": len(data), "original_acquired_at": clock})
    if (
        type(receipt.get("response_rows")) is not int
        or receipt["response_rows"] != len(rows)
        or type(receipt.get("response_bytes")) is not int
        or receipt["response_bytes"] != size
    ):
        raise BoundedError("F10_RECEIPT_ROW_OR_BYTE_COUNT_MISMATCH")
    if rows:
        if receipt.get("status") != "success_nonempty" or receipt.get("reason_code") != "ok":
            raise BoundedError("F10_RECEIPT_NOT_SUCCESS_NONEMPTY")
        if sha.hexdigest() != str(receipt.get("response_sha256")).removeprefix("sha256:"):
            raise BoundedError("F10_RECEIPT_ORDERED_RESPONSE_HASH_MISMATCH")
    elif (
        receipt.get("status") != "justified_empty"
        or receipt.get("reason_code") != "provider_has_no_f10"
        or receipt.get("response_sha256") is not None
    ):
        raise BoundedError("F10_EMPTY_RESPONSE_NOT_JUSTIFIED")
    return {"contract_id": CONTRACT_ID, "stock_code": code,
            "original_receipt_sha256": digest(receipt), "response_rows": len(rows),
            "response_bytes": size, "ordered_response_sha256": sha.hexdigest() if rows else None,
            "original_category_observations": originals,
            "receipt_completion_observation": completion, "knowledge_floor": observed,
            "provider_acquisition": "UNAVAILABLE", "complete_selected_response": True,
            "historical_action_coverage_or_no_change": False,
            "legacy_normalization_restored": False, "consumer_admission": "NOT_GRANTED"}
