"""Complete literal ownership-body comparison against qualified local archives.

Query dates and real capture clocks are separate observations. Identical bodies
reuse their prior qualified/held dated-matrix scope; they do not repair a held
matrix, establish no-change in other F10 categories, or infer provider/PIT times.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock

CONTRACT_ID = "p10-base23-complete-ownership-literal-reconfirmation-v1"
CATEGORIES = {"股本结构", "股东研究"}
RAW_FIELDS = frozenset({
    "security_id", "stock_code", "category", "source_filename", "source_start", "source_length",
    "content", "content_sha256", "provider_updated_date", "market_date", "source",
    "source_revision", "acquired_at",
})


def literal_identity(raw: dict[str, Any]) -> dict[str, Any]:
    content = raw.get("content")
    if (
        set(raw) != RAW_FIELDS or raw.get("category") not in CATEGORIES
        or raw.get("source") != "tdx.get_company_info_content"
        or raw.get("source_revision") != "quanthus-fundamentals-provider-v1"
        or raw.get("security_id") != raw.get("stock_code")
        or not re.fullmatch(r"\d{6}", str(raw.get("stock_code")))
        or not isinstance(content, str) or not 0 < len(content.encode()) <= 1048576
        or raw.get("content_sha256") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        or type(raw.get("source_start")) is not int or raw["source_start"] < 0
        or type(raw.get("source_length")) is not int or raw["source_length"] < 0
        or not isinstance(raw.get("source_filename"), str) or not raw["source_filename"]
        or raw.get("provider_updated_date") is not None
        and not isinstance(raw["provider_updated_date"], str)
    ):
        raise BoundedError("OWNERSHIP_LITERAL_FULL_ROW_UNQUALIFIED")
    wrapper_date = date.fromisoformat(raw["market_date"])
    acquired = _clock(raw["acquired_at"])
    # Transport coordinates have no ownership-field meaning. Hash every literal
    # field as well, so neither their changes nor original clocks disappear.
    meaningful = {k: raw[k] for k in (
        "security_id", "stock_code", "category", "source", "source_revision",
        "content_sha256", "provider_updated_date",
    )}
    return {"identity_key": (raw["source"], raw["stock_code"], raw["category"]),
            "literal_scope_fingerprint": digest(meaningful),
            "complete_raw_row_sha256": digest(raw), "content_sha256": raw["content_sha256"],
            "wrapper_market_date": wrapper_date, "original_raw_acquired_at": raw["acquired_at"],
            "actual_acquisition_instant": acquired, "provider_PIT_qualified": False,
            "other_categories_no_change_asserted": False,
            "body_match_means": "PRIOR_QUALIFIED_OR_HELD_DATED_MATRIX_SCOPE_ONLY"}
