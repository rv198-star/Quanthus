"""Canonical serialization for additive expert-evaluation artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

MAX_DECIMAL_EXPONENT = 10_000


class ExpertEvaluationError(ValueError):
    """Raised when an expert-evaluation artifact violates a frozen contract."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def decimal_text(value: Decimal, field_name: str = "decimal") -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ExpertEvaluationError("invalid_decimal", f"{field_name} must be a finite Decimal")
    exponent = value.as_tuple().exponent
    if abs(exponent) > MAX_DECIMAL_EXPONENT or abs(value.adjusted()) > MAX_DECIMAL_EXPONENT:
        raise ExpertEvaluationError(
            "decimal_out_of_range",
            f"{field_name} exceeds the supported decimal exponent range",
        )
    if value.is_zero():
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise ExpertEvaluationError("invalid_decimal", f"{field_name} must be a decimal string")
    try:
        parsed = Decimal(value)
    except Exception as exc:  # Decimal raises several context-dependent subclasses.
        raise ExpertEvaluationError(
            "invalid_decimal", f"{field_name} must be a decimal string"
        ) from exc
    if decimal_text(parsed, field_name) != value:
        raise ExpertEvaluationError(
            "non_canonical_decimal", f"{field_name} is not normalized"
        )
    return parsed


def datetime_text(value: datetime, field_name: str = "datetime") -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExpertEvaluationError(
            "invalid_timestamp", f"{field_name} must be timezone-aware UTC"
        )
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ExpertEvaluationError(
            "invalid_timestamp", f"{field_name} must already be expressed in UTC"
        )
    return value.isoformat().replace("+00:00", "Z")


def parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ExpertEvaluationError(
            "invalid_timestamp", f"{field_name} must be an explicit UTC timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ExpertEvaluationError(
            "invalid_timestamp", f"{field_name} is not a valid timestamp"
        ) from exc
    if datetime_text(parsed, field_name) != value:
        raise ExpertEvaluationError(
            "non_canonical_timestamp", f"{field_name} is not canonical"
        )
    return parsed


def date_text(value: date, field_name: str = "date") -> str:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ExpertEvaluationError("invalid_date", f"{field_name} must be a date")
    return value.isoformat()


def parse_date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise ExpertEvaluationError("invalid_date", f"{field_name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ExpertEvaluationError(
            "invalid_date", f"{field_name} must be an ISO date"
        ) from exc
    if parsed.isoformat() != value:
        raise ExpertEvaluationError("invalid_date", f"{field_name} is not canonical")
    return parsed


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExpertEvaluationError(
            "unsupported_canonical_value", "artifact contains a non-canonical JSON value"
        ) from exc


def digest_value(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExpertEvaluationError("field_mismatch", f"duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_canonical_object(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes):
        raise ExpertEvaluationError("field_mismatch", "artifact payload must be bytes")
    try:
        value = json.loads(payload, object_pairs_hook=_reject_duplicate_pairs)
    except ExpertEvaluationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError("field_mismatch", "artifact is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ExpertEvaluationError("field_mismatch", "artifact root must be an object")
    if canonical_json_bytes(value) != payload:
        raise ExpertEvaluationError(
            "non_canonical_encoding", "artifact bytes are not canonical JSON"
        )
    return value


def require_fields(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        raise ExpertEvaluationError(
            "field_mismatch",
            f"{context} fields differ; missing={missing}, unexpected={unexpected}",
        )


def require_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExpertEvaluationError("field_mismatch", f"{field_name} must be an object")
    return value


def require_array(value: object, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExpertEvaluationError("field_mismatch", f"{field_name} must be an array")
    return value


ArtifactT = TypeVar("ArtifactT")


def decode_artifact(payload: bytes, artifact_type: type[ArtifactT]) -> ArtifactT:
    raw = parse_canonical_object(payload)
    parser = getattr(artifact_type, "_from_dict", None)
    if parser is None:
        raise TypeError(f"{artifact_type.__name__} does not implement _from_dict")
    artifact = parser(raw)
    if artifact.canonical_bytes() != payload:
        raise ExpertEvaluationError(
            "digest_mismatch", "decoded artifact does not reproduce its canonical bytes"
        )
    return artifact


def artifact_from_bytes(payload: bytes) -> object:
    raw = parse_canonical_object(payload)
    schema_version = raw.get("schema_version")
    from quanthus.expert_evaluation.contracts import ARTIFACT_TYPES_BY_SCHEMA

    artifact_type = ARTIFACT_TYPES_BY_SCHEMA.get(schema_version)
    if artifact_type is None:
        raise ExpertEvaluationError(
            "unsupported_schema", f"unsupported schema_version: {schema_version!r}"
        )
    return decode_artifact(payload, artifact_type)
