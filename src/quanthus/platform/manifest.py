"""Versioned admission manifest for every Platform Kernel surface."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.policy import ConfigurationError, find_project_root

MANIFEST_SCHEMA = "quanthus.platform.features.v1"
_FEATURE_FIELDS = {
    "id",
    "enabled",
    "source_capabilities",
    "routes",
    "startup_hooks",
    "migrations",
    "web_routes",
}


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ConfigurationError(f"{field_name} must be an array of non-empty strings")
    normalized = tuple(value)
    if len(normalized) != len(set(normalized)):
        raise ConfigurationError(f"{field_name} must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class Feature:
    id: str
    enabled: bool
    source_capabilities: tuple[str, ...]
    routes: tuple[str, ...]
    startup_hooks: tuple[str, ...]
    migrations: tuple[str, ...]
    web_routes: tuple[str, ...]


@dataclass(frozen=True)
class FeatureManifest:
    version: str
    process_roles: tuple[str, ...]
    dependency_groups: tuple[str, ...]
    features: tuple[Feature, ...]
    excluded_capabilities: tuple[str, ...]
    path: Path

    def __post_init__(self) -> None:
        feature_ids = [feature.id for feature in self.features]
        if len(feature_ids) != len(set(feature_ids)):
            raise ConfigurationError("feature ids must be unique")
        source_capabilities = [
            capability for feature in self.features for capability in feature.source_capabilities
        ]
        if len(source_capabilities) != len(set(source_capabilities)):
            raise ConfigurationError("source capabilities must map to only one feature")
        if set(source_capabilities).intersection(self.excluded_capabilities):
            raise ConfigurationError("a source capability cannot be both admitted and excluded")

    @property
    def enabled_features(self) -> tuple[str, ...]:
        return tuple(feature.id for feature in self.features if feature.enabled)

    def has(self, feature_id: str) -> bool:
        return feature_id in self.enabled_features

    @property
    def admitted_capabilities(self) -> tuple[str, ...]:
        return tuple(
            capability
            for feature in self.features
            if feature.enabled
            for capability in feature.source_capabilities
        )

    def _enabled_surface(self, field_name: str) -> tuple[str, ...]:
        values: list[str] = []
        for feature in self.features:
            if feature.enabled:
                values.extend(getattr(feature, field_name))
        if len(values) != len(set(values)):
            raise ConfigurationError(f"enabled {field_name} entries must be globally unique")
        return tuple(values)

    @property
    def routes(self) -> tuple[str, ...]:
        return self._enabled_surface("routes")

    @property
    def startup_hooks(self) -> tuple[str, ...]:
        return self._enabled_surface("startup_hooks")

    @property
    def migrations(self) -> tuple[str, ...]:
        return self._enabled_surface("migrations")

    @property
    def web_routes(self) -> tuple[str, ...]:
        return self._enabled_surface("web_routes")


def _parse_feature(raw_value: object) -> Feature:
    if not isinstance(raw_value, Mapping) or set(raw_value) != _FEATURE_FIELDS:
        raise ConfigurationError("each feature must contain the exact manifest fields")
    feature_id = raw_value["id"]
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise ConfigurationError("feature.id must be a non-empty string")
    enabled = raw_value["enabled"]
    if not isinstance(enabled, bool):
        raise ConfigurationError(f"feature {feature_id}.enabled must be boolean")
    return Feature(
        id=feature_id,
        enabled=enabled,
        source_capabilities=_strings(
            raw_value["source_capabilities"], f"feature {feature_id}.source_capabilities"
        ),
        routes=_strings(raw_value["routes"], f"feature {feature_id}.routes"),
        startup_hooks=_strings(raw_value["startup_hooks"], f"feature {feature_id}.startup_hooks"),
        migrations=_strings(raw_value["migrations"], f"feature {feature_id}.migrations"),
        web_routes=_strings(raw_value["web_routes"], f"feature {feature_id}.web_routes"),
    )


def load_feature_manifest(path: Path | None = None) -> FeatureManifest:
    resolved = (path or find_project_root() / "config" / "platform-features.v1.json").resolve()
    try:
        raw: Any = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"feature manifest does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid feature manifest JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigurationError("feature manifest root must be an object")
    expected = {
        "schema_version",
        "manifest_version",
        "process_roles",
        "dependency_groups",
        "features",
        "excluded_capabilities",
    }
    if set(raw) != expected or raw["schema_version"] != MANIFEST_SCHEMA:
        raise ConfigurationError("feature manifest schema or fields are invalid")
    version = raw["manifest_version"]
    if not isinstance(version, str) or not version.strip():
        raise ConfigurationError("manifest_version must be a non-empty string")
    feature_values = raw["features"]
    if not isinstance(feature_values, list) or not feature_values:
        raise ConfigurationError("features must be a non-empty array")
    manifest = FeatureManifest(
        version=version,
        process_roles=_strings(raw["process_roles"], "process_roles"),
        dependency_groups=_strings(raw["dependency_groups"], "dependency_groups"),
        features=tuple(_parse_feature(item) for item in feature_values),
        excluded_capabilities=_strings(raw["excluded_capabilities"], "excluded_capabilities"),
        path=resolved,
    )
    # Evaluate all enabled surfaces during load so duplicate admission fails before boot.
    _ = (manifest.routes, manifest.startup_hooks, manifest.migrations, manifest.web_routes)
    return manifest


def validate_manifest_against_capability_gate(
    manifest: FeatureManifest,
    gate_path: Path | None = None,
) -> None:
    resolved = (
        gate_path or find_project_root() / "config" / "platform-kernel-gates.v1.json"
    ).resolve()
    try:
        raw: Any = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"cannot read Platform Kernel capability gate: {resolved}"
        ) from exc
    if not isinstance(raw, Mapping) or not isinstance(raw.get("capabilities"), list):
        raise ConfigurationError("Platform Kernel capability gate is invalid")
    inventory: set[str] = set()
    dispositions: dict[str, str] = {}
    for item in raw["capabilities"]:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("id"), str)
            or item.get("disposition") not in {"keep", "replace", "defer", "remove"}
        ):
            raise ConfigurationError("Platform Kernel capability inventory entry is invalid")
        capability_id = item["id"]
        inventory.add(capability_id)
        dispositions[capability_id] = str(item["disposition"])
    classified = set(manifest.admitted_capabilities).union(manifest.excluded_capabilities)
    if classified != inventory:
        raise ConfigurationError(
            "feature manifest capability coverage differs from the frozen gate: "
            f"missing={sorted(inventory.difference(classified))}, "
            f"unknown={sorted(classified.difference(inventory))}"
        )
    forbidden_admissions = sorted(
        capability
        for capability in manifest.admitted_capabilities
        if dispositions[capability] in {"defer", "remove"}
    )
    if forbidden_admissions:
        raise ConfigurationError(
            f"feature manifest admits deferred or removed capabilities: {forbidden_admissions}"
        )
