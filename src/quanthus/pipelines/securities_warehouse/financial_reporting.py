"""Decode provider-native financial-statement envelopes for Issue #61."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


class FinancialReportingBuildError(RuntimeError):
    """Raised when a provider financial-statement envelope is not lossless."""


_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DecodedFinancialEnvelope:
    """Verified source payload plus its provider report periods."""

    raw_payload: bytes
    report_list: Mapping[str, Any]


@dataclass(frozen=True)
class StatementRowAccounting:
    """Explicit disposition counts for one provider statement envelope."""

    heading_rows: int
    null_placeholders: int
    valid_facts: int
    invalid_rows: int


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FinancialReportingBuildError(f"{field} must be a non-empty string")
    return value


def _unpack_byte_literal(value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise FinancialReportingBuildError("raw_payload_gzip must be bytes")
    try:
        decoded = ast.literal_eval(bytes(value).decode("ascii"))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise FinancialReportingBuildError(
            "raw_payload_gzip is not an ASCII bytes literal"
        ) from exc
    if not isinstance(decoded, bytes):
        raise FinancialReportingBuildError("raw_payload_gzip literal must decode to bytes")
    return decoded


def decode_financial_envelope(row: Mapping[str, Any]) -> DecodedFinancialEnvelope:
    """Losslessly unpack, decompress, hash-check, and parse one ODS envelope."""

    packed = _unpack_byte_literal(row.get("raw_payload_gzip"))
    try:
        raw = gzip.decompress(packed)
    except OSError as exc:
        raise FinancialReportingBuildError("raw_payload_gzip is not a valid gzip stream") from exc
    declared_bytes = row.get("raw_payload_bytes")
    if not isinstance(declared_bytes, int) or isinstance(declared_bytes, bool):
        raise FinancialReportingBuildError("raw_payload_bytes must be an integer")
    if len(raw) != declared_bytes:
        raise FinancialReportingBuildError("raw payload length differs from its envelope")
    declared_sha256 = _text(row.get("raw_payload_sha256"), "raw_payload_sha256")
    actual_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual_sha256 != declared_sha256:
        raise FinancialReportingBuildError("raw payload SHA-256 differs from its envelope")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FinancialReportingBuildError("raw financial payload is not JSON") from exc
    if not isinstance(payload, Mapping):
        raise FinancialReportingBuildError("raw financial payload root must be an object")
    result = payload.get("result")
    data = result.get("data") if isinstance(result, Mapping) else None
    report_list = data.get("report_list") if isinstance(data, Mapping) else None
    if not isinstance(report_list, Mapping):
        raise FinancialReportingBuildError("financial payload has no object report_list")
    return DecodedFinancialEnvelope(raw_payload=raw, report_list=report_list)


def conservative_provider_timestamp(value: object) -> datetime | None:
    """Convert a provider date or local timestamp to a UTC availability timestamp.

    A date-only value represents no more than a calendar-day disclosure, so its
    availability is conservatively set to the end of that Shanghai calendar day.
    """

    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise FinancialReportingBuildError("provider timestamp must be a string")
    text = value.strip()
    if not text:
        return None
    try:
        if len(text) == 8 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d").replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=_SHANGHAI
            )
        elif len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=_SHANGHAI
            )
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=_SHANGHAI)
    except ValueError as exc:
        raise FinancialReportingBuildError("provider timestamp is invalid") from exc
    return parsed.astimezone(timezone.utc)


def classify_statement_rows(report: Mapping[str, Any]) -> StatementRowAccounting:
    """Classify every provider item without silently dropping malformed rows."""

    items = report.get("data")
    if not isinstance(items, list):
        raise FinancialReportingBuildError("provider report data must be an array")
    headings = 0
    nulls = 0
    facts = 0
    invalid = 0
    for item in items:
        if not isinstance(item, Mapping):
            invalid += 1
            continue
        field = item.get("item_field")
        display_type = item.get("item_display_type")
        if display_type == 1 or not isinstance(field, str) or not field.strip():
            headings += 1
            continue
        value = item.get("item_value")
        if value is None or (isinstance(value, str) and not value.strip()):
            nulls += 1
        else:
            facts += 1
    return StatementRowAccounting(headings, nulls, facts, invalid)


def iter_statement_reports(
    envelope: DecodedFinancialEnvelope,
) -> Iterator[tuple[str, Mapping[str, Any], StatementRowAccounting]]:
    """Yield provider reports in deterministic reporting-period order."""

    for period, value in sorted(envelope.report_list.items(), key=lambda item: str(item[0])):
        if not isinstance(period, str) or not period:
            raise FinancialReportingBuildError("financial report period must be a non-empty string")
        if not isinstance(value, Mapping):
            raise FinancialReportingBuildError(f"financial report {period} must be an object")
        yield period, value, classify_statement_rows(value)
