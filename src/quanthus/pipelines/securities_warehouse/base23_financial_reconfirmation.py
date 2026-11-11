"""Complete retained statement comparison before a September rewrite.

This proves literal equivalence, not provider authority or warehouse admission.
Only the wrapper capture date/clock and verified compression representation are
excluded. Changed metadata, source identity, precision and report bodies remain
changes. Every real clock and original member is retained by the scope owner.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import MAX_RAW_BYTES, _clock
from .base23_sources import THROUGH
from .financial_reporting import decode_financial_envelope, iter_statement_reports

CONTRACT_ID = "p10-base23-complete-statement-reconfirmation-v1"
FIELDS = {
    "security_id", "stock_code", "statement_type", "provider_source",
    "requested_periods", "period_count", "earliest_report_date", "latest_report_date",
    "latest_publish_date", "latest_update_time", "raw_payload_gzip",
    "raw_payload_sha256", "raw_payload_bytes", "market_date", "source",
    "source_revision", "acquired_at",
}


def declared_statement_identity(raw: dict[str, Any]) -> dict[str, Any]:
    """Hash the declaration for an already hash-qualified baseline member.

    This function does not verify compressed bytes. The caller must bind the
    exact complete member to its prior raw qualification before baseline reuse.
    Incoming members still require ``statement_fingerprint`` below.
    """
    if set(raw) != FIELDS:
        raise BoundedError("STATEMENT_RECONFIRMATION_EXACT_SCHEMA_REQUIRED")
    code = raw["stock_code"]
    if (
        not isinstance(code, str) or re.fullmatch(r"\d{6}", code) is None
        or raw["security_id"] != code
        or raw["statement_type"] not in {"balance_sheet", "income_statement", "cash_flow"}
        or any(not isinstance(raw[f], str) or not raw[f]
               for f in ("provider_source", "source", "source_revision"))
    ):
        raise BoundedError("STATEMENT_RECONFIRMATION_SOURCE_IDENTITY_INVALID")
    clock = _clock(raw["acquired_at"])
    try:
        day = date.fromisoformat(raw["market_date"])
    except (TypeError, ValueError) as exc:
        raise BoundedError("STATEMENT_RECONFIRMATION_CAPTURE_DATE_INVALID") from exc
    if day > THROUGH:
        raise BoundedError("STATEMENT_RECONFIRMATION_CAPTURE_AFTER_THROUGH")
    if (
        type(raw["requested_periods"]) is not int or raw["requested_periods"] <= 0
        or type(raw["period_count"]) is not int or raw["period_count"] < 0
    ):
        raise BoundedError("STATEMENT_RECONFIRMATION_PERIOD_CENSUS_INVALID")
    if (
        type(raw["raw_payload_bytes"]) is not int
        or not 0 < raw["raw_payload_bytes"] <= MAX_RAW_BYTES
        or not isinstance(raw["raw_payload_sha256"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", raw["raw_payload_sha256"]) is None
    ):
        raise BoundedError("STATEMENT_RECONFIRMATION_RAW_DECLARATION_INVALID")
    packed = raw["raw_payload_gzip"]
    if not isinstance(packed, bytes) or not packed:
        raise BoundedError("STATEMENT_RECONFIRMATION_COMPRESSION_REPRESENTATION_INVALID")
    metadata = {k: v for k, v in raw.items()
                if k not in {"acquired_at", "market_date", "raw_payload_gzip"}}
    return {
        "contract_id": CONTRACT_ID,
        "identity_key": tuple(raw[f] for f in
                              ("stock_code", "statement_type", "provider_source",
                               "source", "source_revision")),
        "complete_envelope_fingerprint": digest(metadata),
        "declared_raw_payload_sha256": raw["raw_payload_sha256"],
        "compressed_representation_sha256": "sha256:" + hashlib.sha256(packed).hexdigest(),
        "raw_acquired_at": raw["acquired_at"],
        "acquisition_instant": clock,
        "wrapper_capture_date": raw["market_date"],
        "provider_availability_asserted": False,
        "native_admission_asserted": False,
        "complete_envelope_bytes_verified_here": False,
    }


def statement_fingerprint(raw: dict[str, Any]) -> dict[str, Any]:
    """Decode one incoming bounded body once and return value-free hashes.

    Equality is usable only against separately qualified native raw provenance.
    No native fact is rewritten or claimed accepted by this function.
    """
    proof = declared_statement_identity(raw)
    envelope = decode_financial_envelope(raw, max_raw_bytes=MAX_RAW_BYTES)
    periods = list(iter_statement_reports(envelope))
    if len(periods) != raw["period_count"]:
        raise BoundedError("STATEMENT_RECONFIRMATION_PERIOD_CENSUS_INVALID")
    return dict(
        proof,
        verified_raw_payload_sha256=raw["raw_payload_sha256"],
        report_body_sha256s={period: digest(body) for period, body, _ in periods},
        complete_envelope_bytes_verified_here=True,
    )
