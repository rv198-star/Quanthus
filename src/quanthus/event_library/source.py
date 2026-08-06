"""Strict source authority for the P4 forward event scan."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import require_sha256_digest, sha256_digest
from quanthus.pipelines.securities_warehouse.domain_base import (
    DomainBaseError,
    DomainBaseResult,
    verify_domain_base,
)


class EventScanSourceError(ValueError):
    """Raised when a P4 scan source is mutable, legacy, incomplete, or unbound."""


_SEMVER_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_EXPECTED_AUTHORITY = "new_chain_cn01_dwd_mirror"
_EXPECTED_INCREMENT_MODE = "disabled_for_initial_development_scan"
_EXPECTED_FORBIDDEN_SOURCES = (
    "legacy_dual_lane_dwd",
    "legacy_dual_lane_dws",
    "legacy_operational_ads",
    "legacy_strategy_or_portable_ads",
    "legacy_release_set",
    "mutable_current_pointer",
)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise EventScanSourceError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise EventScanSourceError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventScanSourceError(f"{path} must be a non-empty string")
    return value


def _date(value: object, path: str) -> date:
    try:
        return date.fromisoformat(_text(value, path))
    except ValueError as exc:
        raise EventScanSourceError(f"{path} must be an ISO date") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise EventScanSourceError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _unique_text_tuple(value: object, path: str) -> tuple[str, ...]:
    items = tuple(
        _text(item, f"{path}[{index}]")
        for index, item in enumerate(_sequence(value, path))
    )
    if not items or len(items) != len(set(items)):
        raise EventScanSourceError(f"{path} must contain unique non-empty values")
    return items


@dataclass(frozen=True)
class DwdBaseBinding:
    package_id: str
    manifest_sha256: str
    content_digest: str
    kind: str
    through_date: date

    def __post_init__(self) -> None:
        require_sha256_digest(self.manifest_sha256, "base_release.manifest_sha256")
        require_sha256_digest(self.content_digest, "base_release.content_digest")
        if self.kind != "dwd_domain_base_package":
            raise EventScanSourceError("P4 source must be a new-chain DWD domain Base package")

    def to_dict(self) -> dict[str, str]:
        return {
            "package_id": self.package_id,
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
            "kind": self.kind,
            "through_date": self.through_date.isoformat(),
        }


@dataclass(frozen=True)
class DevelopmentPartition:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise EventScanSourceError("development partition start must not follow end")
        if self.start != date(2012, 1, 1) or self.end != date(2021, 12, 31):
            raise EventScanSourceError("P4 initial scan must remain inside 2012-2021 development")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class HistoricalVisibilityPolicy:
    status: str
    required_projection: str
    forbidden_shortcuts: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"blocked", "approved"}:
            raise EventScanSourceError("historical visibility status must be blocked or approved")
        if self.required_projection != "versioned_economic_availability_projection":
            raise EventScanSourceError("historical visibility requires the governed PIT projection")
        expected = (
            "use_dwd_build_cutoff_as_historical_knowledge",
            "replace_knowledge_from_with_observation_time_without_versioned_approval",
            "reuse_legacy_ads_or_historical_bundle",
        )
        if self.forbidden_shortcuts != expected:
            raise EventScanSourceError("historical visibility forbidden shortcuts differ")

    def require_full_scan_authority(self) -> None:
        if self.status != "approved":
            raise EventScanSourceError(
                "full DWD scan is blocked pending a versioned historical PIT projection"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "required_projection": self.required_projection,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
        }


@dataclass(frozen=True)
class EventScanSourcePolicy:
    path: Path
    schema_version: str
    policy_id: str
    policy_version: str
    status: str
    authority: str
    runtime_root_env: str
    base_release: DwdBaseBinding
    development_partition: DevelopmentPartition
    historical_visibility: HistoricalVisibilityPolicy
    daily_increment_mode: str
    forbidden_sources: tuple[str, ...]
    required_tables: tuple[str, ...]
    decision_reference: str
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise EventScanSourceError("event scan source schema_version must be 1.0.0")
        if _SEMVER_PATTERN.fullmatch(self.policy_version) is None:
            raise EventScanSourceError(
                "event scan source policy_version must use semantic versioning"
            )
        if self.status != "approved":
            raise EventScanSourceError("event scan source policy must be approved")
        if self.authority != _EXPECTED_AUTHORITY:
            raise EventScanSourceError("P4 scan authority must be the new-chain CN01 DWD mirror")
        if self.daily_increment_mode != _EXPECTED_INCREMENT_MODE:
            raise EventScanSourceError(
                "initial development scan must not depend on daily increments"
            )
        if self.forbidden_sources != _EXPECTED_FORBIDDEN_SOURCES:
            raise EventScanSourceError(
                "forbidden source list does not match the P4 authority decision"
            )
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", self.runtime_root_env):
            raise EventScanSourceError("runtime_root_env must be an uppercase environment variable")
        required = {
            "dwd_security",
            "dwd_listing",
            "dwd_trading_calendar",
            "dwd_security_bar",
            "dwd_tradability_state",
            "dwd_price_limit_regime",
            "dwd_adjustment_factor",
        }
        if not required.issubset(self.required_tables):
            missing = sorted(required.difference(self.required_tables))
            raise EventScanSourceError(f"required DWD tables are missing: {missing}")
        require_sha256_digest(self.semantic_digest, "semantic_digest")

    @property
    def computed_semantic_digest(self) -> str:
        return sha256_digest(self.semantic_payload())

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "status": self.status,
            "authority": self.authority,
            "runtime_root_env": self.runtime_root_env,
            "base_release": self.base_release.to_dict(),
            "development_partition": self.development_partition.to_dict(),
            "historical_visibility": self.historical_visibility.to_dict(),
            "daily_increment_mode": self.daily_increment_mode,
            "forbidden_sources": list(self.forbidden_sources),
            "required_tables": list(self.required_tables),
        }

    def require_development_date(self, value: date) -> None:
        if not self.development_partition.contains(value):
            raise EventScanSourceError(
                f"date {value.isoformat()} is outside the authorized development partition"
            )

    def resolve_runtime_root(self, environment: Mapping[str, str] | None = None) -> Path:
        values = os.environ if environment is None else environment
        raw = values.get(self.runtime_root_env)
        if raw is None or not raw.strip():
            raise EventScanSourceError(
                f"set {self.runtime_root_env} to the physical new-chain DWD Base package"
            )
        root = Path(raw)
        if not root.is_absolute():
            raise EventScanSourceError("DWD Base runtime root must be absolute")
        if root.name != self.base_release.package_id:
            raise EventScanSourceError("DWD Base runtime root package id differs from policy")
        if root.is_symlink() or not root.is_dir():
            raise EventScanSourceError("DWD Base runtime root must be a physical directory")
        return root.resolve()


def _validate_release_config(policy: EventScanSourcePolicy, release_path: Path) -> None:
    try:
        value = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventScanSourceError(f"cannot read release identity: {release_path}") from exc
    release = _mapping(value, "release")
    layers = _mapping(release.get("layers"), "release.layers")
    dwd = _mapping(layers.get("dwd"), "release.layers.dwd")
    expected = policy.base_release
    observed = (
        dwd.get("package_id"),
        dwd.get("manifest_sha256"),
        dwd.get("content_digest"),
        release.get("through_date"),
    )
    required = (
        expected.package_id,
        expected.manifest_sha256,
        expected.content_digest,
        expected.through_date.isoformat(),
    )
    if observed != required:
        raise EventScanSourceError("P4 source policy disagrees with the V2 release authority")


def load_event_scan_source_policy(
    path: Path,
    *,
    release_path: Path | None = None,
) -> EventScanSourcePolicy:
    """Load the approved P4 source policy without defaults or mutable pointers."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EventScanSourceError(f"cannot read event scan source policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EventScanSourceError(f"invalid event scan source policy JSON: {exc}") from exc
    raw = _mapping(value, "event_scan_source")
    _exact_keys(
        raw,
        {
            "schema_version",
            "policy_id",
            "policy_version",
            "status",
            "authority",
            "runtime_root_env",
            "base_release",
            "development_partition",
            "historical_visibility",
            "daily_increment_mode",
            "forbidden_sources",
            "required_tables",
            "decision_reference",
            "semantic_digest",
        },
        "event_scan_source",
    )
    base = _mapping(raw["base_release"], "base_release")
    _exact_keys(
        base,
        {"package_id", "manifest_sha256", "content_digest", "kind", "through_date"},
        "base_release",
    )
    development = _mapping(raw["development_partition"], "development_partition")
    _exact_keys(development, {"start", "end"}, "development_partition")
    historical_visibility = _mapping(raw["historical_visibility"], "historical_visibility")
    _exact_keys(
        historical_visibility,
        {"status", "required_projection", "forbidden_shortcuts"},
        "historical_visibility",
    )
    result = EventScanSourcePolicy(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        policy_id=_text(raw["policy_id"], "policy_id"),
        policy_version=_text(raw["policy_version"], "policy_version"),
        status=_text(raw["status"], "status"),
        authority=_text(raw["authority"], "authority"),
        runtime_root_env=_text(raw["runtime_root_env"], "runtime_root_env"),
        base_release=DwdBaseBinding(
            package_id=_text(base["package_id"], "base_release.package_id"),
            manifest_sha256=_text(
                base["manifest_sha256"], "base_release.manifest_sha256"
            ),
            content_digest=_text(base["content_digest"], "base_release.content_digest"),
            kind=_text(base["kind"], "base_release.kind"),
            through_date=_date(base["through_date"], "base_release.through_date"),
        ),
        development_partition=DevelopmentPartition(
            start=_date(development["start"], "development_partition.start"),
            end=_date(development["end"], "development_partition.end"),
        ),
        historical_visibility=HistoricalVisibilityPolicy(
            status=_text(historical_visibility["status"], "historical_visibility.status"),
            required_projection=_text(
                historical_visibility["required_projection"],
                "historical_visibility.required_projection",
            ),
            forbidden_shortcuts=_unique_text_tuple(
                historical_visibility["forbidden_shortcuts"],
                "historical_visibility.forbidden_shortcuts",
            ),
        ),
        daily_increment_mode=_text(raw["daily_increment_mode"], "daily_increment_mode"),
        forbidden_sources=_unique_text_tuple(raw["forbidden_sources"], "forbidden_sources"),
        required_tables=_unique_text_tuple(raw["required_tables"], "required_tables"),
        decision_reference=_text(raw["decision_reference"], "decision_reference"),
        semantic_digest=_text(raw["semantic_digest"], "semantic_digest"),
    )
    if result.semantic_digest != result.computed_semantic_digest:
        raise EventScanSourceError("event scan source semantic digest mismatch")
    if release_path is not None:
        _validate_release_config(result, release_path)
    return result


def verify_event_scan_base(
    policy: EventScanSourcePolicy,
    *,
    environment: Mapping[str, str] | None = None,
) -> DomainBaseResult:
    """Deeply verify the exact physical new-chain DWD Base selected for the scan."""

    root = policy.resolve_runtime_root(environment)
    try:
        result = verify_domain_base(root)
    except (OSError, DomainBaseError) as exc:
        raise EventScanSourceError("new-chain DWD Base verification failed") from exc
    expected = policy.base_release
    if (
        result.package_id != expected.package_id
        or result.manifest_sha256 != expected.manifest_sha256
        or result.content_digest != expected.content_digest
    ):
        raise EventScanSourceError("physical DWD Base identity differs from approved source policy")
    try:
        manifest_value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventScanSourceError("verified DWD Base manifest became unreadable") from exc
    manifest = _mapping(manifest_value, "dwd_base_manifest")
    row_counts = _mapping(manifest.get("row_counts"), "dwd_base_manifest.row_counts")
    missing = sorted(set(policy.required_tables).difference(row_counts))
    if missing:
        raise EventScanSourceError(f"physical DWD Base lacks required tables: {missing}")
    return result
