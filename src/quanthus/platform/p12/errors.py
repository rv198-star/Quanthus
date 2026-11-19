"""Contract error bodies; importable without the P12 application closure."""

from __future__ import annotations

from typing import Any

from quanthus.platform.p12.contract_generated import REASON_TEXTS, SCHEMA_VERSION
from quanthus.platform.p12.validation import validate

UNCLASSIFIED = "UNCLASSIFIED_FACT_ERROR"


def error_dto(code: str, detail: str, request_id: str) -> dict[str, Any]:
    known = code if code in REASON_TEXTS else UNCLASSIFIED
    message = detail if known == code else f"{code}: {detail}"
    return validate(
        "Error",
        {
            "schema_version": SCHEMA_VERSION,
            "error": {"reason_code": known, "detail": message[:500], "request_id": request_id},
        },
    )
