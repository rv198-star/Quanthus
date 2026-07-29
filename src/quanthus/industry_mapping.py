"""Checksum-bound static industry mapping for P3 concentration research only."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.concentration import IndustryMappingUsage, StaticIndustryMembership

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class IndustryMappingError(ValueError):
    """Raised when static concentration membership is incomplete or ambiguous."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise IndustryMappingError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise IndustryMappingError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndustryMappingError(f"{path} must be a non-empty string")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise IndustryMappingError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


@dataclass(frozen=True)
class IndustryMappingSource:
    provider: str
    endpoint: str
    retrieved_at: datetime
    source_revision: str

    def __post_init__(self) -> None:
        for value in (self.provider, self.endpoint, self.source_revision):
            if not value.strip():
                raise IndustryMappingError("industry mapping source fields must not be empty")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise IndustryMappingError("industry mapping retrieved_at must be timezone-aware")

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_revision": self.source_revision,
        }


@dataclass(frozen=True)
class StaticIndustryMapping:
    path: Path
    schema_version: str
    mapping_version: str
    source: IndustryMappingSource
    memberships: tuple[StaticIndustryMembership, ...]
    artifact_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise IndustryMappingError("industry mapping schema must be 1.0.0")
        if not self.mapping_version.strip():
            raise IndustryMappingError("mapping_version must not be empty")
        if not _SHA256.fullmatch(self.artifact_digest):
            raise IndustryMappingError("artifact_digest must use sha256")
        if not self.memberships:
            raise IndustryMappingError("industry mapping must contain memberships")
        security_ids = [item.security_code for item in self.memberships]
        if len(security_ids) != len(set(security_ids)):
            raise IndustryMappingError("industry mapping contains ambiguous security_id rows")
        if any(item.mapping_version != self.mapping_version for item in self.memberships):
            raise IndustryMappingError("membership mapping version does not match artifact")

    @property
    def by_security_id(self) -> Mapping[str, StaticIndustryMembership]:
        return {item.security_code: item for item in self.memberships}

    def require_coverage(self, security_ids: Sequence[str]) -> None:
        requested = tuple(security_ids)
        if len(requested) != len(set(requested)):
            raise IndustryMappingError("requested mapping coverage contains duplicate security_id")
        missing = sorted(set(requested).difference(self.by_security_id))
        if missing:
            preview = missing[:10]
            raise IndustryMappingError(
                f"industry mapping is missing {len(missing)} required security_id values: {preview}"
            )


def load_static_industry_mapping(
    path: Path,
    *,
    expected_version: str,
    required_security_ids: Sequence[str] = (),
) -> StaticIndustryMapping:
    """Load an external map without defaults, identity inference, or partial coverage."""

    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IndustryMappingError(f"cannot read industry mapping: {path}") from exc
    except json.JSONDecodeError as exc:
        raise IndustryMappingError(f"invalid industry mapping JSON: {exc}") from exc
    raw = _mapping(raw_value, "industry_mapping")
    _exact_keys(
        raw,
        {
            "schema_version",
            "mapping_version",
            "usage",
            "source",
            "memberships",
            "artifact_digest",
        },
        "industry_mapping",
    )
    if raw["schema_version"] != "1.0.0":
        raise IndustryMappingError("industry mapping schema must be 1.0.0")
    mapping_version = _text(raw["mapping_version"], "mapping_version")
    if mapping_version != expected_version:
        raise IndustryMappingError("industry mapping version does not match strategy policy")
    if raw["usage"] != IndustryMappingUsage.STATIC_CONCENTRATION_ONLY.value:
        raise IndustryMappingError("static industry mapping may only be used for concentration")
    artifact_digest = _text(raw["artifact_digest"], "artifact_digest")
    unsigned = {key: value for key, value in raw.items() if key != "artifact_digest"}
    if artifact_digest != _digest(unsigned):
        raise IndustryMappingError("industry mapping artifact digest mismatch")

    source_raw = _mapping(raw["source"], "source")
    _exact_keys(
        source_raw,
        {"provider", "endpoint", "retrieved_at", "source_revision"},
        "source",
    )
    try:
        retrieved_at = datetime.fromisoformat(_text(source_raw["retrieved_at"], "retrieved_at"))
    except ValueError as exc:
        raise IndustryMappingError("retrieved_at must be an ISO timestamp") from exc
    source = IndustryMappingSource(
        provider=_text(source_raw["provider"], "source.provider"),
        endpoint=_text(source_raw["endpoint"], "source.endpoint"),
        retrieved_at=retrieved_at,
        source_revision=_text(source_raw["source_revision"], "source.source_revision"),
    )

    memberships: list[StaticIndustryMembership] = []
    for index, item_value in enumerate(_sequence(raw["memberships"], "memberships")):
        item = _mapping(item_value, f"memberships[{index}]")
        _exact_keys(
            item,
            {"security_id", "stock_code", "exchange", "level1_industry_id"},
            f"memberships[{index}]",
        )
        security_id = _text(item["security_id"], f"memberships[{index}].security_id")
        stock_code = _text(item["stock_code"], f"memberships[{index}].stock_code")
        exchange = _text(item["exchange"], f"memberships[{index}].exchange")
        industry = _text(item["level1_industry_id"], f"memberships[{index}].level1_industry_id")
        memberships.append(
            StaticIndustryMembership(
                security_code=security_id,
                level1_industry_id=industry,
                mapping_version=mapping_version,
                source_revision=source.source_revision,
                lineage_ref=(f"{source.provider}:{stock_code}:{exchange}:{artifact_digest}"),
            )
        )
    result = StaticIndustryMapping(
        path=path.resolve(),
        schema_version="1.0.0",
        mapping_version=mapping_version,
        source=source,
        memberships=tuple(sorted(memberships, key=lambda item: item.security_code)),
        artifact_digest=artifact_digest,
    )
    result.require_coverage(required_security_ids)
    return result
