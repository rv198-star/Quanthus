"""Immutable OCI-authoritative operational ADS historical Base packages."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.backtest.historical_bundle import (
    REQUIRED_HISTORICAL_REPLAY_DATASETS,
    HistoricalReplayBundle,
    HistoricalReplayBundleError,
    validate_historical_replay_bundle_contract,
)
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import CorporateActionNormalizationPolicy
from quanthus.data_foundation import DatasetManifest
from quanthus.policy import ConfigurationError, find_project_root
from quanthus.strategy_replay_ads import (
    StrategyReplayADSBundle,
    StrategyReplayADSError,
    StrategyReplayADSFrameReader,
    StrategyReplayADSPITBinding,
)

BASE_SCHEMA_VERSION = 1
BASE_KIND = "operational_ads_base"
CONTENT_DIGEST_METHOD = "canonical_logical_row_stream_sha256_v1"
JSON_CONTENT_DIGEST_METHOD = "canonical_json_rows_sha256_v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_OBJECT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_PHASES = {"provisional", "final"}
_TABLE_ROLES = {
    "decision_visible",
    "runner_only",
    "runner_policy_reference",
    "outcome_only",
    "label_only",
}
_CLASSIFICATIONS = {
    "authoritative_operational_daily",
    "reusable_verified_semantic_content",
    "derived_offline_export",
    "duplicate",
    "superseded",
    "unverifiable",
    "out_of_scope",
}
_REUSE_DECISIONS = {"reuse", "rebuild", "exclude", "evidence_only"}


class OperationalADSBaseError(RuntimeError):
    """Raised when an operational ADS Base violates its contract."""


@dataclass(frozen=True)
class OperationalADSTableContract:
    name: str
    role: str
    format: str
    row_path: str | None
    date_column: str | None
    business_key: tuple[str, ...]
    sort_keys: tuple[str, ...]
    allow_empty: bool
    require_per_decision_pit: bool


@dataclass(frozen=True)
class OperationalADSBasePolicy:
    contract_id: str
    profile: str
    authority: Mapping[str, object]
    packages: Mapping[str, tuple[str, date]]
    required_upstream_roles: tuple[str, ...]
    generation_ledger_calendar_table: str
    tables: Mapping[str, OperationalADSTableContract]
    consumer_roles: Mapping[str, frozenset[str]]
    digest: str


@dataclass(frozen=True)
class OperationalADSBasePlan:
    path: Path
    raw: Mapping[str, Any]
    package_id: str
    phase: str
    through_date: date
    ranges: Mapping[str, date]
    digest: str
    identity: Mapping[str, object]


@dataclass(frozen=True)
class OperationalADSBaseBundle:
    root: Path
    package_id: str
    phase: str
    through_date: date
    content_digest: str
    manifest_sha256: str
    plan_digest: str
    table_parts: Mapping[str, tuple[str, ...]]
    table_roles: Mapping[str, str]

    @property
    def execution_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "phase": self.phase,
            "through_date": self.through_date.isoformat(),
            "content_digest": self.content_digest,
            "manifest_sha256": self.manifest_sha256,
            "plan_digest": self.plan_digest,
            "table_count": len(self.table_parts),
            "execution_authorized": self.execution_authorized,
            "root": str(self.root),
        }


@dataclass(frozen=True)
class OperationalADSBaseAccess:
    """Role-filtered path capability; callers never receive the package root."""

    _bundle: OperationalADSBaseBundle
    _allowed_roles: frozenset[str]

    @property
    def identity(self) -> Mapping[str, str]:
        return {
            "package_id": self._bundle.package_id,
            "content_digest": self._bundle.content_digest,
            "manifest_sha256": self._bundle.manifest_sha256,
        }

    def table_paths(self, name: str) -> tuple[Path, ...]:
        try:
            role = self._bundle.table_roles[name]
            parts = self._bundle.table_parts[name]
        except KeyError as exc:
            raise OperationalADSBaseError(f"operational ADS table is not declared: {name}") from exc
        if role not in self._allowed_roles:
            raise OperationalADSBaseError(
                f"reader capability cannot access operational ADS table role: {role}"
            )
        return tuple((self._bundle.root / part).resolve() for part in parts)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _canonical_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationalADSBaseError(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise OperationalADSBaseError(f"JSON payload must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _required_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise OperationalADSBaseError(f"{key} must be non-empty text")
    return item


def _parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise OperationalADSBaseError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OperationalADSBaseError(f"{field} must be an ISO date") from exc


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise OperationalADSBaseError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OperationalADSBaseError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise OperationalADSBaseError(f"{field} must include a timezone")
    return parsed


def _safe_relative(value: str, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise OperationalADSBaseError(f"{field} must be a safe relative path")
    return path


def _inside(root: Path, value: str, field: str) -> Path:
    relative = _safe_relative(value, field)
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise OperationalADSBaseError(f"{field} escapes its package root") from exc
    return resolved


def _source_manifest_path(value: object, field: str) -> Path:
    if not isinstance(value, str):
        raise OperationalADSBaseError(f"{field} must be an absolute manifest path")
    path = Path(value)
    if not path.is_absolute() or any("CURRENT" in part.upper() for part in path.parts):
        raise OperationalADSBaseError(f"{field} must be exact and must not resolve CURRENT")
    return path


def _string_list(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise OperationalADSBaseError(f"{field} must be a unique non-empty text list")
    return tuple(value)


def load_operational_ads_base_policy(path: Path | None = None) -> OperationalADSBasePolicy:
    policy_path = path or find_project_root() / "config" / "operational-ads-base.v1.json"
    try:
        raw = _load_object(policy_path)
    except OperationalADSBaseError as exc:
        raise ConfigurationError(str(exc)) from exc
    try:
        if raw.get("schema_version") != BASE_SCHEMA_VERSION:
            raise OperationalADSBaseError("operational ADS Base policy schema is unsupported")
        contract_id = _required_text(raw, "contract_id")
        profile = _required_text(raw, "profile")
        authority = raw.get("authority")
        if authority != {
            "holder": "oci",
            "research_only": True,
            "execution_authorized": False,
        }:
            raise OperationalADSBaseError(
                "operational ADS Base authority must remain OCI research-only"
            )
        packages_raw = raw.get("packages")
        if not isinstance(packages_raw, dict) or set(packages_raw) != _PHASES:
            raise OperationalADSBaseError("operational ADS Base package phases are incomplete")
        packages: dict[str, tuple[str, date]] = {}
        for phase in sorted(_PHASES):
            item = packages_raw[phase]
            if not isinstance(item, dict):
                raise OperationalADSBaseError(f"packages.{phase} must be an object")
            package_id = _required_text(item, "package_id")
            if not _SAFE_ID.fullmatch(package_id):
                raise OperationalADSBaseError(f"packages.{phase}.package_id is unsafe")
            packages[phase] = (
                package_id,
                _parse_date(item.get("through_date"), f"packages.{phase}.through_date"),
            )
        required_upstreams = _string_list(
            raw.get("required_upstream_roles"), "required_upstream_roles"
        )
        table_values = raw.get("tables")
        if not isinstance(table_values, dict) or not table_values:
            raise OperationalADSBaseError("operational ADS Base policy needs semantic tables")
        tables: dict[str, OperationalADSTableContract] = {}
        for name, item in sorted(table_values.items()):
            if (
                not isinstance(name, str)
                or not _SAFE_ID.fullmatch(name)
                or not isinstance(item, dict)
            ):
                raise OperationalADSBaseError("operational ADS Base table declaration is invalid")
            role = _required_text(item, "role")
            table_format = _required_text(item, "format")
            date_column = item.get("date_column")
            row_path = item.get("row_path")
            if (
                role not in _TABLE_ROLES
                or table_format not in {"parquet", "json"}
                or (date_column is not None and not isinstance(date_column, str))
                or (table_format == "json" and not isinstance(row_path, str))
                or (table_format == "parquet" and row_path is not None)
                or not isinstance(item.get("allow_empty"), bool)
                or not isinstance(item.get("require_per_decision_pit"), bool)
            ):
                raise OperationalADSBaseError(
                    f"operational ADS Base table contract is invalid: {name}"
                )
            business_key = _string_list(item.get("business_key"), f"tables.{name}.business_key")
            sort_keys = _string_list(item.get("sort_keys"), f"tables.{name}.sort_keys")
            if not set(business_key).issubset(sort_keys):
                raise OperationalADSBaseError(
                    f"table sort keys must contain its business key: {name}"
                )
            tables[name] = OperationalADSTableContract(
                name=name,
                role=role,
                format=table_format,
                row_path=row_path,
                date_column=date_column,
                business_key=business_key,
                sort_keys=sort_keys,
                allow_empty=bool(item["allow_empty"]),
                require_per_decision_pit=bool(item["require_per_decision_pit"]),
            )
        consumer_values = raw.get("consumer_roles")
        if not isinstance(consumer_values, dict) or not consumer_values:
            raise OperationalADSBaseError("operational ADS Base consumer roles are missing")
        consumer_roles: dict[str, frozenset[str]] = {}
        for role, allowed in sorted(consumer_values.items()):
            allowed_roles = frozenset(_string_list(allowed, f"consumer_roles.{role}"))
            if not allowed_roles.issubset(_TABLE_ROLES) or "label_only" in allowed_roles:
                raise OperationalADSBaseError(f"consumer role grants an unsafe table role: {role}")
            consumer_roles[str(role)] = allowed_roles
        publication = raw.get("publication")
        if publication != {
            "artifact_discovery": "manifest_tables_parts",
            "mandatory_year_partitioning": False,
            "ready_written_last": True,
            "descriptor_compare_and_swap": True,
            "migration_mode": "copy_only",
            "delete_enabled": False,
        }:
            raise OperationalADSBaseError("operational ADS Base publication safety changed")
        calendar_table = _required_text(raw, "generation_ledger_calendar_table")
        if calendar_table not in tables:
            raise OperationalADSBaseError("generation ledger calendar table is not declared")
    except OperationalADSBaseError as exc:
        raise ConfigurationError(str(exc)) from exc
    return OperationalADSBasePolicy(
        contract_id=contract_id,
        profile=profile,
        authority=authority,
        packages=packages,
        required_upstream_roles=required_upstreams,
        generation_ledger_calendar_table=calendar_table,
        tables=tables,
        consumer_roles=consumer_roles,
        digest=_canonical_digest(raw),
    )


def _sanitize_bound_object(value: Mapping[str, Any]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key not in {"manifest_path"}}


def _validate_generation_ledger(
    value: object, *, ranges: Mapping[str, date], through_date: date
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not value:
        raise OperationalADSBaseError("generation_ledger must contain per-date lineage")
    rows: list[dict[str, object]] = []
    dates: list[date] = []
    required = {
        "market_date",
        "source_generation_id",
        "source_lane",
        "knowledge_cutoff",
        "decision_cutoff",
        "catalog_manifest_sha256",
        "dwd_manifest_sha256",
        "dws_manifest_sha256",
    }
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != required:
            raise OperationalADSBaseError(f"generation_ledger[{index}] has an invalid shape")
        market_date = _parse_date(item["market_date"], f"generation_ledger[{index}].market_date")
        knowledge = _parse_datetime(
            item["knowledge_cutoff"], f"generation_ledger[{index}].knowledge_cutoff"
        )
        decision = _parse_datetime(
            item["decision_cutoff"], f"generation_ledger[{index}].decision_cutoff"
        )
        if (
            market_date > through_date
            or not (ranges["decision_start"] <= market_date <= ranges["decision_end"])
            or knowledge > decision
            or decision.date() != market_date
            or not _SAFE_ID.fullmatch(_required_text(item, "source_generation_id"))
            or _required_text(item, "source_lane") not in {"cn01", "hermes", "legacy_accepted"}
            or any(
                not _SHA256.fullmatch(_required_text(item, field))
                for field in required
                if field.endswith("_sha256")
            )
        ):
            raise OperationalADSBaseError(f"generation_ledger[{index}] violates PIT or lineage")
        dates.append(market_date)
        rows.append(dict(item))
    if dates != sorted(set(dates)):
        raise OperationalADSBaseError("generation_ledger dates must be unique and sorted")
    return tuple(rows)


def load_operational_ads_base_plan(
    path: Path, *, policy: OperationalADSBasePolicy
) -> OperationalADSBasePlan:
    raw = _load_object(path)
    if (
        raw.get("schema_version") != BASE_SCHEMA_VERSION
        or raw.get("contract_id") != policy.contract_id
    ):
        raise OperationalADSBaseError("operational ADS Base build plan contract mismatch")
    package = raw.get("package")
    ranges_raw = raw.get("ranges")
    if not isinstance(package, dict) or not isinstance(ranges_raw, dict):
        raise OperationalADSBaseError("operational ADS Base package/ranges are missing")
    phase = _required_text(package, "phase")
    if phase not in _PHASES:
        raise OperationalADSBaseError("operational ADS Base phase is unsupported")
    package_id = _required_text(package, "package_id")
    through_date = _parse_date(package.get("through_date"), "package.through_date")
    if policy.packages[phase] != (package_id, through_date):
        raise OperationalADSBaseError("operational ADS Base package identity differs from policy")
    if raw.get("authority") != policy.authority or raw.get("profile") != policy.profile:
        raise OperationalADSBaseError("operational ADS Base authority/profile mismatch")
    range_fields = {
        "context_start",
        "context_end",
        "decision_start",
        "decision_end",
        "execution_start",
        "execution_end",
        "outcome_start",
        "outcome_end",
    }
    if set(ranges_raw) != range_fields:
        raise OperationalADSBaseError("operational ADS Base ranges are incomplete")
    ranges = {field: _parse_date(ranges_raw[field], f"ranges.{field}") for field in range_fields}
    if any(ranges[field] > through_date for field in range_fields) or not (
        ranges["context_start"] <= ranges["decision_start"] <= ranges["decision_end"]
        and ranges["decision_end"] < ranges["execution_end"]
        and ranges["execution_start"] <= ranges["execution_end"]
        and ranges["outcome_start"] <= ranges["outcome_end"]
    ):
        raise OperationalADSBaseError("operational ADS Base ranges violate package coverage")
    replay_contract = raw.get("replay_contract")
    if (
        not isinstance(replay_contract, dict)
        or set(replay_contract)
        != {
            "universe_policy_version",
            "corporate_action_policy_version",
        }
        or not _required_text(replay_contract, "universe_policy_version")
        or not _required_text(replay_contract, "corporate_action_policy_version")
    ):
        raise OperationalADSBaseError("operational ADS Base replay contract is incomplete")
    upstreams = raw.get("upstreams")
    inventory = raw.get("inventory")
    tables = raw.get("tables")
    if (
        raw.get("inventory_complete") is not True
        or not isinstance(upstreams, list)
        or not isinstance(inventory, list)
        or not isinstance(tables, list)
    ):
        raise OperationalADSBaseError("operational ADS Base inventory or inputs are incomplete")
    upstream_roles: list[str] = []
    upstream_ids: list[str] = []
    for index, item in enumerate(upstreams):
        if not isinstance(item, dict) or set(item) != {
            "role",
            "object_id",
            "manifest_path",
            "manifest_sha256",
            "content_digest",
        }:
            raise OperationalADSBaseError(f"upstreams[{index}] has an invalid shape")
        upstream_roles.append(_required_text(item, "role"))
        upstream_ids.append(_required_text(item, "object_id"))
        _source_manifest_path(item.get("manifest_path"), f"upstreams[{index}].manifest_path")
        if not _SHA256.fullmatch(_required_text(item, "manifest_sha256")) or not _SHA256.fullmatch(
            _required_text(item, "content_digest")
        ):
            raise OperationalADSBaseError(f"upstreams[{index}] has an invalid digest")
    if tuple(sorted(upstream_roles)) != tuple(sorted(policy.required_upstream_roles)):
        raise OperationalADSBaseError(
            "operational ADS Base must bind exactly one required upstream role"
        )
    if len(set(upstream_roles)) != len(upstream_roles):
        raise OperationalADSBaseError("operational ADS Base upstream roles must be unique")
    inventory_by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(inventory):
        if not isinstance(item, dict) or set(item) != {
            "object_id",
            "kind",
            "manifest_path",
            "manifest_sha256",
            "content_digest",
            "classification",
            "authority_state",
            "reuse_decision",
            "coverage",
            "upstream_generation_digest",
            "reason",
        }:
            raise OperationalADSBaseError(f"inventory[{index}] has an invalid shape")
        object_id = _required_text(item, "object_id")
        if object_id in inventory_by_id or not _SAFE_ID.fullmatch(object_id):
            raise OperationalADSBaseError(f"inventory[{index}] object id is unsafe or duplicated")
        _source_manifest_path(item.get("manifest_path"), f"inventory[{index}].manifest_path")
        if (
            _required_text(item, "classification") not in _CLASSIFICATIONS
            or _required_text(item, "reuse_decision") not in _REUSE_DECISIONS
            or not _SHA256.fullmatch(_required_text(item, "manifest_sha256"))
            or not _SHA256.fullmatch(_required_text(item, "content_digest"))
            or not _SHA256.fullmatch(_required_text(item, "upstream_generation_digest"))
            or not isinstance(item.get("coverage"), dict)
            or set(item["coverage"]) != {"date_start", "date_end"}
        ):
            raise OperationalADSBaseError(f"inventory[{index}] classification or digest is invalid")
        start = _parse_date(
            item["coverage"]["date_start"], f"inventory[{index}].coverage.date_start"
        )
        end = _parse_date(item["coverage"]["date_end"], f"inventory[{index}].coverage.date_end")
        if (
            start > end
            or not _required_text(item, "authority_state")
            or not _required_text(item, "reason")
        ):
            raise OperationalADSBaseError(f"inventory[{index}] coverage or rationale is invalid")
        inventory_by_id[object_id] = item
    if not set(upstream_ids).issubset(inventory_by_id):
        raise OperationalADSBaseError(
            "every upstream object must be present in the frozen inventory"
        )
    upstream_by_role = {str(item["role"]): item for item in upstreams}
    for index, item in enumerate(upstreams):
        bound_inventory = inventory_by_id[str(item["object_id"])]
        if any(
            bound_inventory.get(field) != item.get(field)
            for field in ("manifest_path", "manifest_sha256", "content_digest")
        ):
            raise OperationalADSBaseError(
                f"upstreams[{index}] differs from its frozen inventory binding"
            )
    table_by_name: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(tables):
        if not isinstance(item, dict) or set(item) != {"name", "sources"}:
            raise OperationalADSBaseError(f"tables[{index}] has an invalid shape")
        name = _required_text(item, "name")
        sources = item.get("sources")
        if (
            name in table_by_name
            or name not in policy.tables
            or not isinstance(sources, list)
            or not sources
        ):
            raise OperationalADSBaseError(
                f"tables[{index}] is unknown, duplicated, or has no sources"
            )
        contract = policy.tables[name]
        for source_index, source in enumerate(sources):
            if not isinstance(source, dict) or set(source) != {
                "object_id",
                "source_table",
                "source_role",
                "table_content_digest",
                "lineage_digest",
                "pit_evidence_digest",
            }:
                raise OperationalADSBaseError(f"tables[{index}].sources[{source_index}] is invalid")
            object_id = _required_text(source, "object_id")
            source_inventory = inventory_by_id.get(object_id)
            if (
                source_inventory is None
                or source_inventory.get("classification") != "reusable_verified_semantic_content"
                or source_inventory.get("reuse_decision") != "reuse"
                or _required_text(source, "source_role") != contract.role
                or any(
                    not _SHA256.fullmatch(_required_text(source, field))
                    for field in ("table_content_digest", "lineage_digest")
                )
            ):
                raise OperationalADSBaseError(f"table source is not verified and reusable: {name}")
            pit_digest = source.get("pit_evidence_digest")
            if contract.require_per_decision_pit:
                if not isinstance(pit_digest, str) or not _SHA256.fullmatch(pit_digest):
                    raise OperationalADSBaseError(
                        f"decision-visible table lacks PIT evidence: {name}"
                    )
            elif pit_digest is not None:
                raise OperationalADSBaseError(f"non-PIT table must not invent PIT evidence: {name}")
        table_by_name[name] = item
    if set(table_by_name) != set(policy.tables):
        raise OperationalADSBaseError("operational ADS Base table source set is incomplete")
    ledger = _validate_generation_ledger(
        raw.get("generation_ledger"), ranges=ranges, through_date=through_date
    )
    ledger_bindings = {
        "canonical_catalog": "catalog_manifest_sha256",
        "dwd_base": "dwd_manifest_sha256",
        "dws_base": "dws_manifest_sha256",
    }
    for role, ledger_field in ledger_bindings.items():
        upstream = upstream_by_role.get(role)
        if upstream is None:
            continue
        if any(row[ledger_field] != upstream["manifest_sha256"] for row in ledger):
            raise OperationalADSBaseError(
                f"generation_ledger {ledger_field} differs from the bound {role}"
            )
    absorbed = raw.get("absorbed_daily")
    prior_base = raw.get("prior_base")
    if not isinstance(absorbed, list) or any(not isinstance(item, dict) for item in absorbed):
        raise OperationalADSBaseError("absorbed_daily must be a list of exact bindings")
    for index, item in enumerate(absorbed):
        if set(item) != {"object_id", "market_date", "manifest_sha256", "content_digest"}:
            raise OperationalADSBaseError(f"absorbed_daily[{index}] has an invalid shape")
        absorbed_date = _parse_date(item["market_date"], f"absorbed_daily[{index}].market_date")
        absorbed_inventory = inventory_by_id.get(_required_text(item, "object_id"))
        if not _SHA256.fullmatch(_required_text(item, "manifest_sha256")) or not _SHA256.fullmatch(
            _required_text(item, "content_digest")
        ):
            raise OperationalADSBaseError(f"absorbed_daily[{index}] digest is invalid")
        if (
            absorbed_date > through_date
            or absorbed_inventory is None
            or absorbed_inventory.get("classification") != "authoritative_operational_daily"
            or absorbed_inventory.get("manifest_sha256") != item["manifest_sha256"]
            or absorbed_inventory.get("content_digest") != item["content_digest"]
        ):
            raise OperationalADSBaseError(
                f"absorbed_daily[{index}] differs from authoritative frozen inventory"
            )
    absorbed_dates = [str(item["market_date"]) for item in absorbed]
    if absorbed_dates != sorted(set(absorbed_dates)):
        raise OperationalADSBaseError("absorbed_daily dates must be unique and sorted")
    if phase == "provisional" and (absorbed or prior_base is not None):
        raise OperationalADSBaseError(
            "provisional ADS Base cannot claim a prior Base or absorbed daily"
        )
    if phase == "final":
        if (
            len(absorbed) != 1
            or absorbed[0].get("market_date") != "2026-07-31"
            or not isinstance(prior_base, dict)
        ):
            raise OperationalADSBaseError(
                "final ADS Base must bind the provisional Base and July 31 daily"
            )
        if (
            set(prior_base) != {"package_id", "manifest_sha256", "content_digest"}
            or prior_base.get("package_id") != policy.packages["provisional"][0]
        ):
            raise OperationalADSBaseError("final ADS Base prior package binding is invalid")
        if not _SHA256.fullmatch(
            _required_text(prior_base, "manifest_sha256")
        ) or not _SHA256.fullmatch(_required_text(prior_base, "content_digest")):
            raise OperationalADSBaseError("final ADS Base prior package digest is invalid")
        prior_inventory = inventory_by_id.get(str(prior_base["package_id"]))
        if (
            prior_inventory is None
            or prior_inventory.get("classification") != "reusable_verified_semantic_content"
            or prior_inventory.get("reuse_decision") != "reuse"
            or prior_inventory.get("manifest_sha256") != prior_base["manifest_sha256"]
            or prior_inventory.get("content_digest") != prior_base["content_digest"]
        ):
            raise OperationalADSBaseError(
                "final ADS Base prior package differs from frozen reusable inventory"
            )
    identity = {
        "contract_id": policy.contract_id,
        "profile": policy.profile,
        "package": dict(package),
        "authority": dict(policy.authority),
        "ranges": {key: value.isoformat() for key, value in sorted(ranges.items())},
        "replay_contract": dict(replay_contract),
        "upstreams": [_sanitize_bound_object(item) for item in upstreams],
        "inventory": [_sanitize_bound_object(item) for item in inventory],
        "inventory_complete": True,
        "tables": tables,
        "generation_ledger": list(ledger),
        "absorbed_daily": absorbed,
        "prior_base": prior_base,
    }
    return OperationalADSBasePlan(
        path=path.resolve(),
        raw=raw,
        package_id=package_id,
        phase=phase,
        through_date=through_date,
        ranges=ranges,
        digest=_canonical_digest(identity),
        identity=identity,
    )


def _manifest_content_digest(value: Mapping[str, Any]) -> str | None:
    for key in (
        "content_digest",
        "bundle_digest",
        "bundle_content_digest",
        "candidate_content_digest",
        "snapshot_digest",
    ):
        item = value.get(key)
        if isinstance(item, str) and _SHA256.fullmatch(item):
            return item
    return None


def _verify_bound_manifest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    path = _source_manifest_path(value.get("manifest_path"), f"{field}.manifest_path")
    manifest_sha256 = _sha256(path) if path.is_file() else None
    if (
        path.is_symlink()
        or not path.is_file()
        or path.resolve() != path
        or manifest_sha256 != value.get("manifest_sha256")
    ):
        raise OperationalADSBaseError(f"{field} manifest integrity failed")
    manifest = _load_object(path)
    embedded_content_digest = _manifest_content_digest(manifest)
    content_identity = embedded_content_digest or manifest_sha256
    if content_identity != value.get("content_digest"):
        raise OperationalADSBaseError(f"{field} content digest differs from its manifest")
    object_id = _required_text(value, "object_id")
    declared_ids = {
        item
        for key in (
            "bundle_id",
            "catalog_id",
            "manifest_id",
            "package_id",
            "snapshot_id",
        )
        if isinstance((item := manifest.get(key)), str) and item
    }
    if object_id != path.parent.name and object_id not in declared_ids:
        raise OperationalADSBaseError(f"{field} logical id differs from its manifest")
    return manifest


def _source_table(manifest: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    values: list[object] = []
    for field in ("tables", "profile_tables"):
        item = manifest.get(field)
        if isinstance(item, list):
            values.extend(item)
    matches = [item for item in values if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise OperationalADSBaseError(f"source manifest must declare exactly one table: {name}")
    return matches[0]


def _nested_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {item for child in value.values() for item in _nested_strings(child)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return {item for child in value for item in _nested_strings(child)}
    return set()


def _path_free_source_lineage(value: object, field: str) -> object:
    if isinstance(value, str):
        if value.startswith("/") or "file://" in value:
            raise OperationalADSBaseError(f"{field} contains an absolute producer path")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _path_free_source_lineage(item, f"{field}.{key}")
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if key != "parts"
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            _path_free_source_lineage(item, f"{field}[{index}]") for index, item in enumerate(value)
        ]
    return value


def _source_parts(
    *, manifest_path: Path, table: Mapping[str, Any], table_name: str
) -> tuple[Path, ...]:
    parts = table.get("parts")
    if (
        not isinstance(parts, list)
        or not parts
        or any(not isinstance(item, dict) for item in parts)
    ):
        raise OperationalADSBaseError(f"source table has no manifest parts: {table_name}")
    paths: list[Path] = []
    for index, part in enumerate(parts):
        assert isinstance(part, dict)
        relative = _required_text(part, "path")
        path = _inside(manifest_path.parent, relative, f"{table_name}.parts[{index}].path")
        size = part.get("size_bytes", part.get("bytes"))
        if (
            path.is_symlink()
            or not path.is_file()
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or path.stat().st_size != size
            or _sha256(path) != part.get("sha256")
        ):
            raise OperationalADSBaseError(f"source table part integrity failed: {table_name}")
        paths.append(path)
    return tuple(paths)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_parquet_expression(paths: Sequence[Path]) -> str:
    values = ",".join(_sql_string(str(path)) for path in paths)
    return f"read_parquet([{values}])"


def _logical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return {"decimal": format(value, "f")}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, float):
        return {"float_hex": value.hex()}
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, Mapping):
        return {"mapping": {str(key): _logical_value(item) for key, item in sorted(value.items())}}
    if isinstance(value, Sequence):
        return {"sequence": [_logical_value(item) for item in value]}
    raise OperationalADSBaseError(
        f"operational ADS logical digest does not support: {type(value).__name__}"
    )


def _logical_schema(description: Sequence[Sequence[object]]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "name": str(item[0]),
            "type": str(item[1]).upper(),
            "nullable": "unknown" if len(item) < 3 or item[2] is None else str(item[2]).lower(),
        }
        for item in description
    )


def _row_content_digest(
    rows: Sequence[Sequence[object]],
    *,
    columns: Sequence[str],
    schema: Sequence[Mapping[str, str]],
    sort_keys: Sequence[str],
    method: str,
) -> tuple[str, dict[str, object]]:
    digest = hashlib.sha256()
    header = {
        "method": method,
        "columns": list(columns),
        "logical_schema": [dict(item) for item in schema],
        "sort_keys": list(sort_keys),
    }
    header_bytes = _canonical_bytes(header)
    digest.update(len(header_bytes).to_bytes(8, "big"))
    digest.update(header_bytes)
    for row in rows:
        row_bytes = _canonical_bytes([_logical_value(value) for value in row])
        digest.update(len(row_bytes).to_bytes(8, "big"))
        digest.update(row_bytes)
    value = f"sha256:{digest.hexdigest()}"
    return value, {
        "method": method,
        "row_count": len(rows),
        "logical_row_stream_sha256": value,
    }


def _parquet_content_digest(
    connection: Any,
    relation: str,
    *,
    columns: Sequence[str],
    schema: Sequence[Mapping[str, str]],
    sort_keys: Sequence[str],
) -> tuple[str, dict[str, object]]:
    digest = hashlib.sha256()
    header = {
        "method": CONTENT_DIGEST_METHOD,
        "columns": list(columns),
        "logical_schema": [dict(item) for item in schema],
        "sort_keys": list(sort_keys),
    }
    header_bytes = _canonical_bytes(header)
    digest.update(len(header_bytes).to_bytes(8, "big"))
    digest.update(header_bytes)
    projection = ", ".join(f'"{column}"' for column in columns)
    ordering = ", ".join(f'"{column}"' for column in sort_keys)
    cursor = connection.execute(f"SELECT {projection} FROM {relation} ORDER BY {ordering}")
    row_count = 0
    while True:
        rows = cursor.fetchmany(10_000)
        if not rows:
            break
        for row in rows:
            row_bytes = _canonical_bytes([_logical_value(value) for value in row])
            digest.update(len(row_bytes).to_bytes(8, "big"))
            digest.update(row_bytes)
            row_count += 1
    value = f"sha256:{digest.hexdigest()}"
    return value, {
        "method": CONTENT_DIGEST_METHOD,
        "row_count": row_count,
        "logical_row_stream_sha256": value,
    }


def _runtime_identity() -> dict[str, object]:
    packages: dict[str, str] = {}
    for package in ("duckdb", "pyarrow", "quanthus"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "source-tree"
    return {
        "python_series": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": dict(sorted(packages.items())),
    }


def _package_content_identity(value: Mapping[str, Any]) -> dict[str, object]:
    """Return the logical package identity without Parquet encoding metadata."""

    identity: dict[str, object] = dict(value)
    raw_tables = value.get("tables")
    if not isinstance(raw_tables, list):
        raise OperationalADSBaseError("operational ADS Base content tables are invalid")
    tables: list[dict[str, object]] = []
    for raw_table in raw_tables:
        if not isinstance(raw_table, dict):
            raise OperationalADSBaseError("operational ADS Base content table is invalid")
        table: dict[str, object] = dict(raw_table)
        raw_parts = raw_table.get("parts")
        if not isinstance(raw_parts, list) or any(
            not isinstance(part, dict) for part in raw_parts
        ):
            raise OperationalADSBaseError("operational ADS Base content parts are invalid")
        table["parts"] = [
            {
                key: item
                for key, item in part.items()
                if key not in {"sha256", "size_bytes"}
            }
            for part in raw_parts
        ]
        tables.append(table)
    identity["tables"] = tables
    return identity


def _schema_sha256(path: Path) -> str:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise OperationalADSBaseError("P1 dependencies are required for ADS Base") from exc
    schema_bytes = pq.ParquetFile(path).schema_arrow.serialize().to_pybytes()
    return f"sha256:{hashlib.sha256(schema_bytes).hexdigest()}"


def _null_counts(connection: Any, relation: str, columns: Sequence[str]) -> dict[str, int]:
    projections = ", ".join(f'count(*) FILTER (WHERE "{column}" IS NULL)' for column in columns)
    row = connection.execute(f"SELECT {projections} FROM {relation}").fetchone()
    if row is None:
        raise OperationalADSBaseError("table null-count query returned no row")
    return {column: int(value) for column, value in zip(columns, row, strict=True)}


def _build_parquet_table(
    connection: Any,
    *,
    staging: Path,
    contract: OperationalADSTableContract,
    sources: Sequence[tuple[Mapping[str, Any], Mapping[str, Any], Path]],
) -> dict[str, object]:
    expressions: list[str] = []
    expected_columns: tuple[str, ...] | None = None
    expected_schema: tuple[dict[str, str], ...] | None = None
    for binding, table, manifest_path in sources:
        paths = _source_parts(manifest_path=manifest_path, table=table, table_name=contract.name)
        relation = _read_parquet_expression(paths)
        description = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
        columns = tuple(str(item[0]) for item in description)
        schema = _logical_schema(description)
        missing = set(contract.business_key + contract.sort_keys) - set(columns)
        if contract.date_column is not None:
            missing.add(contract.date_column) if contract.date_column not in columns else None
        if missing:
            raise OperationalADSBaseError(
                f"source table is missing contract columns {sorted(missing)}: {contract.name}"
            )
        if expected_columns is None:
            expected_columns, expected_schema = columns, schema
        elif columns != expected_columns or schema != expected_schema:
            raise OperationalADSBaseError(f"source table schemas conflict: {contract.name}")
        source_content, _source_fingerprint = _parquet_content_digest(
            connection,
            relation,
            columns=columns,
            schema=schema,
            sort_keys=contract.sort_keys,
        )
        if source_content != binding.get("table_content_digest"):
            raise OperationalADSBaseError(
                f"source table logical content digest mismatch: {contract.name}"
            )
        primary = ", ".join(f'"{column}"' for column in contract.business_key)
        duplicate = connection.execute(
            f"SELECT count(*) FROM (SELECT {primary}, count(*) AS n FROM {relation} "
            f"GROUP BY {primary} HAVING n <> 1)"
        ).fetchone()
        if duplicate is None or int(duplicate[0]):
            raise OperationalADSBaseError(
                f"source table has duplicate business keys: {contract.name}"
            )
        generation = _required_text(binding, "lineage_digest")
        expressions.append(
            f"SELECT *, {_sql_string(generation)} AS __source_generation FROM {relation}"
        )
    assert expected_columns is not None
    assert expected_schema is not None
    union = " UNION ALL ".join(expressions)
    primary = ", ".join(f'"{column}"' for column in contract.business_key)
    row_hash_columns = ", ".join(f'"{column}"' for column in expected_columns)
    conflict = connection.execute(
        f"SELECT count(*) FROM (SELECT {primary}, "
        "count(DISTINCT __source_generation) AS generations, "
        f"count(DISTINCT hash({row_hash_columns})) AS versions "
        f"FROM ({union}) source GROUP BY {primary} HAVING generations <> 1 OR versions <> 1)"
    ).fetchone()
    if conflict is None or int(conflict[0]):
        raise OperationalADSBaseError(
            f"overlapping source generations conflict on business keys: {contract.name}"
        )
    projection = ", ".join(f'"{column}"' for column in expected_columns)
    ordering = ", ".join(f'"{column}"' for column in contract.sort_keys)
    target = staging / "tables" / f"{contract.name}.parquet"
    connection.execute(
        f"COPY (SELECT DISTINCT {projection} FROM ({union}) source ORDER BY {ordering}) "
        f"TO {_sql_string(str(target))} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
    )
    relation = f"read_parquet({_sql_string(str(target))})"
    stats_projection = "count(*)"
    if contract.date_column is not None:
        stats_projection += (
            f', min(CAST("{contract.date_column}" AS DATE)), '
            f'max(CAST("{contract.date_column}" AS DATE))'
        )
    stats = connection.execute(f"SELECT {stats_projection} FROM {relation}").fetchone()
    if stats is None or (int(stats[0]) == 0 and not contract.allow_empty):
        raise OperationalADSBaseError(f"operational ADS Base table is empty: {contract.name}")
    nulls = _null_counts(connection, relation, expected_columns)
    if any(nulls[column] for column in contract.business_key):
        raise OperationalADSBaseError(
            f"operational ADS Base business key contains nulls: {contract.name}"
        )
    content_digest, fingerprint = _parquet_content_digest(
        connection,
        relation,
        columns=expected_columns,
        schema=expected_schema,
        sort_keys=contract.sort_keys,
    )
    relative = target.relative_to(staging).as_posix()
    return {
        "name": contract.name,
        "role": contract.role,
        "format": contract.format,
        "date_column": contract.date_column,
        "business_key": list(contract.business_key),
        "sort_keys": list(contract.sort_keys),
        "logical_schema": [dict(item) for item in expected_schema],
        "table_content_digest": content_digest,
        "content_fingerprint": fingerprint,
        "quality": {
            "row_count": int(stats[0]),
            "null_counts": nulls,
            "duplicate_business_key_count": 0,
            "conflicting_business_key_count": 0,
        },
        "source_bindings": [dict(binding) for binding, _table, _path in sources],
        "source_lineage": [
            _path_free_source_lineage(table, f"tables.{contract.name}.source_lineage")
            for _binding, table, _path in sources
        ],
        "parts": [
            {
                "path": relative,
                "sha256": _sha256(target),
                "size_bytes": target.stat().st_size,
                "row_count": int(stats[0]),
                "schema_sha256": _schema_sha256(target),
                "min_date": stats[1].isoformat() if len(stats) > 1 and stats[1] else None,
                "max_date": stats[2].isoformat() if len(stats) > 2 and stats[2] else None,
                "sort_keys": list(contract.sort_keys),
            }
        ],
    }


def _json_rows(payload: Mapping[str, Any], row_path: str) -> list[dict[str, Any]]:
    value: object = payload
    for part in row_path.split("."):
        if not isinstance(value, dict):
            raise OperationalADSBaseError(f"JSON row path is not an object path: {row_path}")
        value = value.get(part)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise OperationalADSBaseError(f"JSON row path must resolve to object rows: {row_path}")
    return [dict(item) for item in value]


def _build_json_table(
    *,
    staging: Path,
    contract: OperationalADSTableContract,
    sources: Sequence[tuple[Mapping[str, Any], Mapping[str, Any], Path]],
) -> dict[str, object]:
    assert contract.row_path is not None
    if len(sources) != 1:
        raise OperationalADSBaseError(
            f"JSON reference table must bind exactly one semantic source: {contract.name}"
        )
    rows_by_key: dict[tuple[object, ...], tuple[dict[str, Any], str]] = {}
    template: dict[str, Any] | None = None
    for binding, table, manifest_path in sources:
        parts = _source_parts(manifest_path=manifest_path, table=table, table_name=contract.name)
        if len(parts) != 1:
            raise OperationalADSBaseError(
                f"JSON semantic table requires exactly one part: {contract.name}"
            )
        payload = _load_object(parts[0])
        source_digest = payload.get("digest", payload.get("artifact_digest"))
        if source_digest != binding.get("table_content_digest"):
            raise OperationalADSBaseError(
                f"JSON source logical content digest mismatch: {contract.name}"
            )
        if template is None:
            template = payload
        generation = _required_text(binding, "lineage_digest")
        for row in _json_rows(payload, contract.row_path):
            try:
                key = tuple(row[column] for column in contract.business_key)
            except KeyError as exc:
                raise OperationalADSBaseError(
                    f"JSON semantic table is missing a business key: {contract.name}"
                ) from exc
            if any(value is None for value in key):
                raise OperationalADSBaseError(
                    f"JSON semantic table business key contains nulls: {contract.name}"
                )
            existing = rows_by_key.get(key)
            if existing is not None and (existing[0] != row or existing[1] != generation):
                raise OperationalADSBaseError(
                    f"JSON semantic table has a conflicting business key: {contract.name}"
                )
            rows_by_key[key] = (row, generation)
    assert template is not None
    ordered = [
        rows_by_key[key][0]
        for key in sorted(rows_by_key, key=lambda item: tuple(str(value) for value in item))
    ]
    if not ordered and not contract.allow_empty:
        raise OperationalADSBaseError(f"operational ADS Base table is empty: {contract.name}")
    target_value = dict(template)
    cursor: dict[str, Any] = target_value
    row_path_parts = contract.row_path.split(".")
    for part in row_path_parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            raise OperationalADSBaseError(
                f"JSON row path cannot be materialized: {contract.row_path}"
            )
        cursor = child
    cursor[row_path_parts[-1]] = ordered
    target = staging / "tables" / f"{contract.name}.json"
    _write_json(target, target_value)
    columns = sorted({column for row in ordered for column in row})
    schema = tuple(
        {
            "name": column,
            "type": sorted({type(row.get(column)).__name__ for row in ordered}),
            "nullable": any(row.get(column) is None for row in ordered),
        }
        for column in columns
    )
    row_values = [tuple(row.get(column) for column in columns) for row in ordered]
    content_digest, fingerprint = _row_content_digest(
        row_values,
        columns=columns,
        schema=[{key: str(value) for key, value in item.items()} for item in schema],
        sort_keys=contract.sort_keys,
        method=JSON_CONTENT_DIGEST_METHOD,
    )
    date_values = (
        [
            _parse_date(row[contract.date_column], f"{contract.name}.{contract.date_column}")
            for row in ordered
        ]
        if contract.date_column is not None
        else []
    )
    nulls = {column: sum(row.get(column) is None for row in ordered) for column in columns}
    relative = target.relative_to(staging).as_posix()
    schema_digest = _canonical_digest(schema)
    return {
        "name": contract.name,
        "role": contract.role,
        "format": contract.format,
        "row_path": contract.row_path,
        "date_column": contract.date_column,
        "business_key": list(contract.business_key),
        "sort_keys": list(contract.sort_keys),
        "logical_schema": list(schema),
        "table_content_digest": content_digest,
        "content_fingerprint": fingerprint,
        "quality": {
            "row_count": len(ordered),
            "null_counts": nulls,
            "duplicate_business_key_count": 0,
            "conflicting_business_key_count": 0,
        },
        "source_bindings": [dict(binding) for binding, _table, _path in sources],
        "source_lineage": [
            _path_free_source_lineage(table, f"tables.{contract.name}.source_lineage")
            for _binding, table, _path in sources
        ],
        "parts": [
            {
                "path": relative,
                "sha256": _sha256(target),
                "size_bytes": target.stat().st_size,
                "row_count": len(ordered),
                "schema_sha256": schema_digest,
                "min_date": min(date_values).isoformat() if date_values else None,
                "max_date": max(date_values).isoformat() if date_values else None,
                "sort_keys": list(contract.sort_keys),
            }
        ],
    }


def _validate_calendar_ledger(
    connection: Any,
    *,
    staging: Path,
    policy: OperationalADSBasePolicy,
    plan: OperationalADSBasePlan,
) -> None:
    contract = policy.tables[policy.generation_ledger_calendar_table]
    if contract.format != "parquet" or contract.date_column is None:
        raise OperationalADSBaseError("generation ledger calendar contract must be dated Parquet")
    path = staging / "tables" / f"{contract.name}.parquet"
    relation = f"read_parquet({_sql_string(str(path))})"
    columns = {
        str(item[0]) for item in connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    }
    where = f'CAST("{contract.date_column}" AS DATE) BETWEEN ? AND ?'
    if {"exchange", "is_open"}.issubset(columns):
        rows = connection.execute(
            f'SELECT CAST("{contract.date_column}" AS DATE) FROM {relation} WHERE {where} '
            "AND exchange IN ('SSE', 'SZSE') AND is_open = 1 GROUP BY 1 "
            "HAVING count(DISTINCT exchange) = 2 ORDER BY 1",
            [plan.ranges["decision_start"], plan.ranges["decision_end"]],
        ).fetchall()
    else:
        rows = connection.execute(
            f'SELECT DISTINCT CAST("{contract.date_column}" AS DATE) FROM {relation} '
            f"WHERE {where} ORDER BY 1",
            [plan.ranges["decision_start"], plan.ranges["decision_end"]],
        ).fetchall()
    actual = tuple(row[0].isoformat() for row in rows)
    ledger_value = plan.identity.get("generation_ledger")
    if not isinstance(ledger_value, list) or any(
        not isinstance(item, dict) for item in ledger_value
    ):
        raise OperationalADSBaseError("generation ledger identity is invalid")
    declared = tuple(item["market_date"] for item in ledger_value)
    if actual != declared:
        raise OperationalADSBaseError(
            "generation ledger does not cover the decision calendar exactly"
        )


def build_operational_ads_base(
    *,
    plan: OperationalADSBasePlan,
    policy: OperationalADSBasePolicy,
    output: Path,
    code_revision: str,
) -> OperationalADSBaseBundle:
    """Build one deterministic immutable Base from exact declared semantic inputs."""

    if not _HEX_OBJECT.fullmatch(code_revision):
        raise OperationalADSBaseError("code_revision must be a full lowercase Git object id")
    target = output.resolve()
    if target.name != plan.package_id:
        raise OperationalADSBaseError("operational ADS Base output directory must equal package id")
    if target.exists():
        existing = verify_operational_ads_base(target, policy=policy, verify_logical_content=True)
        if existing.plan_digest != plan.digest:
            raise OperationalADSBaseError(
                "existing operational ADS Base used a different build plan"
            )
        return existing
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise OperationalADSBaseError("P1 dependencies are required for ADS Base") from exc
    upstreams = plan.raw["upstreams"]
    inventory = plan.raw["inventory"]
    assert isinstance(upstreams, list)
    assert isinstance(inventory, list)
    for index, item in enumerate(upstreams):
        assert isinstance(item, dict)
        _verify_bound_manifest(item, f"upstreams[{index}]")
    manifests: dict[str, tuple[Mapping[str, Any], Path]] = {}
    for index, item in enumerate(inventory):
        assert isinstance(item, dict)
        source_manifest = _verify_bound_manifest(item, f"inventory[{index}]")
        manifests[_required_text(item, "object_id")] = (
            source_manifest,
            _source_manifest_path(item["manifest_path"], f"inventory[{index}].manifest_path"),
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{plan.package_id}.", dir=target.parent))
    connection = duckdb.connect(":memory:")
    try:
        (staging / "tables").mkdir()
        table_manifests: list[dict[str, object]] = []
        table_plan = {item["name"]: item for item in plan.raw["tables"] if isinstance(item, dict)}
        for name in sorted(policy.tables):
            contract = policy.tables[name]
            bindings = table_plan[name].get("sources")
            if not isinstance(bindings, list):
                raise OperationalADSBaseError(f"table source bindings are invalid: {name}")
            resolved_sources: list[tuple[Mapping[str, Any], Mapping[str, Any], Path]] = []
            for binding in bindings:
                if not isinstance(binding, dict):
                    raise OperationalADSBaseError(f"table source binding is invalid: {name}")
                object_id = _required_text(binding, "object_id")
                package_manifest, manifest_path = manifests[object_id]
                source_name = _required_text(binding, "source_table")
                source_table = _source_table(package_manifest, source_name)
                source_digest = source_table.get(
                    "table_content_digest", source_table.get("logical_digest")
                )
                strings = _nested_strings(source_table)
                required_digests = {
                    _required_text(binding, "lineage_digest"),
                    _required_text(binding, "table_content_digest"),
                }
                pit_digest = binding.get("pit_evidence_digest")
                if isinstance(pit_digest, str):
                    required_digests.add(pit_digest)
                if (
                    source_table.get("role") != binding.get("source_role")
                    or source_digest != binding.get("table_content_digest")
                    or not required_digests.issubset(strings)
                ):
                    raise OperationalADSBaseError(
                        f"source table lineage/content/role mismatch: {name}"
                    )
                sanitized_binding = dict(binding)
                sanitized_binding["object_id"] = object_id
                resolved_sources.append((sanitized_binding, source_table, manifest_path))
            if contract.format == "parquet":
                table_manifest = _build_parquet_table(
                    connection,
                    staging=staging,
                    contract=contract,
                    sources=resolved_sources,
                )
            else:
                table_manifest = _build_json_table(
                    staging=staging,
                    contract=contract,
                    sources=resolved_sources,
                )
            built_parts = table_manifest.get("parts")
            if (
                not isinstance(built_parts, list)
                or len(built_parts) != 1
                or not isinstance(built_parts[0], dict)
            ):
                raise OperationalADSBaseError(f"built table parts are invalid: {name}")
            part = built_parts[0]
            if (
                part["max_date"] is not None
                and _parse_date(part["max_date"], f"tables.{name}.max_date") > plan.through_date
            ):
                raise OperationalADSBaseError(f"table crosses the Base through_date: {name}")
            table_manifests.append(table_manifest)
        _validate_calendar_ledger(connection, staging=staging, policy=policy, plan=plan)
        content_value = {
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "plan_digest": plan.digest,
            "profile": policy.profile,
            "package_id": plan.package_id,
            "phase": plan.phase,
            "through_date": plan.through_date.isoformat(),
            "ranges": {key: value.isoformat() for key, value in sorted(plan.ranges.items())},
            "replay_contract": plan.identity["replay_contract"],
            "authority": dict(policy.authority),
            "publisher": {
                "code_revision": code_revision,
                "runtime": _runtime_identity(),
            },
            "upstreams": plan.identity["upstreams"],
            "inventory": plan.identity["inventory"],
            "inventory_complete": True,
            "generation_ledger": plan.identity["generation_ledger"],
            "absorbed_daily": plan.identity["absorbed_daily"],
            "prior_base": plan.identity["prior_base"],
            "tables": table_manifests,
        }
        manifest = {
            "schema_version": BASE_SCHEMA_VERSION,
            "kind": BASE_KIND,
            "manifest_id": f"{plan.package_id}-manifest-v1",
            "manifest_digest_method": "file_sha256_bound_by_READY_and_BASE_CURRENT",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
            "execution_authorized": False,
            **content_value,
            "content_digest": _canonical_digest(_package_content_identity(content_value)),
        }
        _write_json(staging / "manifest.json", manifest)
        (staging / "READY").write_text(_sha256(staging / "manifest.json") + "\n", encoding="ascii")
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise OperationalADSBaseError(
                    "symbolic links are forbidden in operational ADS Base"
                )
            path.chmod(0o750 if path.is_dir() else 0o640)
        staging.chmod(0o750)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        connection.close()
    return verify_operational_ads_base(target, policy=policy, verify_logical_content=True)


def _verify_table_part(
    root: Path,
    *,
    raw: Mapping[str, Any],
    contract: OperationalADSTableContract,
) -> tuple[Path, Mapping[str, Any]]:
    if (
        raw.get("name") != contract.name
        or raw.get("role") != contract.role
        or raw.get("format") != contract.format
        or raw.get("date_column") != contract.date_column
        or raw.get("business_key") != list(contract.business_key)
        or raw.get("sort_keys") != list(contract.sort_keys)
    ):
        raise OperationalADSBaseError(
            f"operational ADS Base table contract mismatch: {contract.name}"
        )
    parts = raw.get("parts")
    source_bindings = raw.get("source_bindings")
    source_lineage = raw.get("source_lineage")
    if (
        not isinstance(parts, list)
        or len(parts) != 1
        or not isinstance(parts[0], dict)
        or not isinstance(source_bindings, list)
        or not source_bindings
        or any(not isinstance(item, dict) for item in source_bindings)
        or not isinstance(source_lineage, list)
        or len(source_lineage) != len(source_bindings)
        or any(not isinstance(item, dict) for item in source_lineage)
    ):
        raise OperationalADSBaseError(
            f"operational ADS Base table parts or lineage are invalid: {contract.name}"
        )
    part = parts[0]
    path = _inside(root, _required_text(part, "path"), f"tables.{contract.name}.part.path")
    size = part.get("size_bytes")
    rows = part.get("row_count")
    if (
        path.is_symlink()
        or not path.is_file()
        or not isinstance(size, int)
        or isinstance(size, bool)
        or path.stat().st_size != size
        or not isinstance(rows, int)
        or isinstance(rows, bool)
        or rows < 0
        or _sha256(path) != part.get("sha256")
        or part.get("sort_keys") != list(contract.sort_keys)
    ):
        raise OperationalADSBaseError(
            f"operational ADS Base table part integrity failed: {contract.name}"
        )
    return path, part


def _verify_parquet_table(
    connection: Any,
    *,
    path: Path,
    raw: Mapping[str, Any],
    part: Mapping[str, Any],
    contract: OperationalADSTableContract,
    verify_logical_content: bool,
) -> None:
    relation = f"read_parquet({_sql_string(str(path))})"
    description = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    columns = tuple(str(item[0]) for item in description)
    schema = _logical_schema(description)
    if raw.get("logical_schema") != [dict(item) for item in schema] or _schema_sha256(
        path
    ) != part.get("schema_sha256"):
        raise OperationalADSBaseError(
            f"operational ADS Base table schema mismatch: {contract.name}"
        )
    primary = ", ".join(f'"{column}"' for column in contract.business_key)
    stats_projection = "count(*)"
    if contract.date_column is not None:
        stats_projection += (
            f', min(CAST("{contract.date_column}" AS DATE)), '
            f'max(CAST("{contract.date_column}" AS DATE))'
        )
    stats = connection.execute(f"SELECT {stats_projection} FROM {relation}").fetchone()
    duplicate = connection.execute(
        f"SELECT count(*) FROM (SELECT {primary}, count(*) AS n FROM {relation} "
        f"GROUP BY {primary} HAVING n <> 1)"
    ).fetchone()
    quality = raw.get("quality")
    if (
        stats is None
        or duplicate is None
        or int(duplicate[0])
        or not isinstance(quality, dict)
        or int(stats[0]) != part.get("row_count")
        or int(stats[0]) != quality.get("row_count")
        or quality.get("duplicate_business_key_count") != 0
        or quality.get("conflicting_business_key_count") != 0
        or quality.get("null_counts") != _null_counts(connection, relation, columns)
    ):
        raise OperationalADSBaseError(
            f"operational ADS Base table quality mismatch: {contract.name}"
        )
    min_date = stats[1].isoformat() if len(stats) > 1 and stats[1] else None
    max_date = stats[2].isoformat() if len(stats) > 2 and stats[2] else None
    if part.get("min_date") != min_date or part.get("max_date") != max_date:
        raise OperationalADSBaseError(
            f"operational ADS Base table date bounds mismatch: {contract.name}"
        )
    if verify_logical_content:
        content, fingerprint = _parquet_content_digest(
            connection,
            relation,
            columns=columns,
            schema=schema,
            sort_keys=contract.sort_keys,
        )
        if content != raw.get("table_content_digest") or fingerprint != raw.get(
            "content_fingerprint"
        ):
            raise OperationalADSBaseError(
                f"operational ADS Base table logical content mismatch: {contract.name}"
            )


def _verify_json_table(
    *,
    path: Path,
    raw: Mapping[str, Any],
    part: Mapping[str, Any],
    contract: OperationalADSTableContract,
) -> None:
    assert contract.row_path is not None
    payload = _load_object(path)
    rows = _json_rows(payload, contract.row_path)
    columns = sorted({column for row in rows for column in row})
    keys = [tuple(row.get(column) for column in contract.business_key) for row in rows]
    schema = tuple(
        {
            "name": column,
            "type": sorted({type(row.get(column)).__name__ for row in rows}),
            "nullable": any(row.get(column) is None for row in rows),
        }
        for column in columns
    )
    ordered = sorted(
        rows, key=lambda row: tuple(str(row.get(column)) for column in contract.sort_keys)
    )
    content, fingerprint = _row_content_digest(
        [tuple(row.get(column) for column in columns) for row in ordered],
        columns=columns,
        schema=[{key: str(value) for key, value in item.items()} for item in schema],
        sort_keys=contract.sort_keys,
        method=JSON_CONTENT_DIGEST_METHOD,
    )
    quality = raw.get("quality")
    date_values = (
        [
            _parse_date(row[contract.date_column], f"{contract.name}.{contract.date_column}")
            for row in rows
        ]
        if contract.date_column is not None
        else []
    )
    if (
        raw.get("row_path") != contract.row_path
        or raw.get("logical_schema") != list(schema)
        or part.get("schema_sha256") != _canonical_digest(schema)
        or len(rows) != part.get("row_count")
        or len(keys) != len(set(keys))
        or not isinstance(quality, dict)
        or quality.get("row_count") != len(rows)
        or quality.get("null_counts")
        != {column: sum(row.get(column) is None for row in rows) for column in columns}
        or content != raw.get("table_content_digest")
        or fingerprint != raw.get("content_fingerprint")
        or part.get("min_date") != (min(date_values).isoformat() if date_values else None)
        or part.get("max_date") != (max(date_values).isoformat() if date_values else None)
    ):
        raise OperationalADSBaseError(f"operational ADS Base JSON table mismatch: {contract.name}")


def verify_operational_ads_base(
    root: Path,
    *,
    policy: OperationalADSBasePolicy,
    verify_logical_content: bool = False,
) -> OperationalADSBaseBundle:
    """Verify one Base without resolving or reading any producer path."""

    package_root = root.resolve()
    manifest_path = package_root / "manifest.json"
    ready_path = package_root / "READY"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise OperationalADSBaseError("operational ADS Base is missing manifest.json or READY")
    manifest_sha256 = _sha256(manifest_path)
    if ready_path.read_text(encoding="ascii").strip() != manifest_sha256:
        raise OperationalADSBaseError("operational ADS Base READY does not bind manifest.json")
    manifest = _load_object(manifest_path)
    phase = _required_text(manifest, "phase")
    package_id = _required_text(manifest, "package_id")
    through_date = _parse_date(manifest.get("through_date"), "through_date")
    if (
        manifest.get("schema_version") != BASE_SCHEMA_VERSION
        or manifest.get("kind") != BASE_KIND
        or manifest.get("contract_id") != policy.contract_id
        or manifest.get("policy_sha256") != policy.digest
        or manifest.get("profile") != policy.profile
        or manifest.get("authority") != policy.authority
        or manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
        or package_root.name != package_id
        or phase not in _PHASES
        or policy.packages[phase] != (package_id, through_date)
    ):
        raise OperationalADSBaseError("operational ADS Base envelope contract mismatch")
    content_value = {
        key: value
        for key, value in manifest.items()
        if key
        not in {
            "schema_version",
            "kind",
            "manifest_id",
            "manifest_digest_method",
            "built_at",
            "research_only",
            "execution_authorized",
            "content_digest",
        }
    }
    content_digest = _required_text(manifest, "content_digest")
    if content_digest != _canonical_digest(_package_content_identity(content_value)):
        raise OperationalADSBaseError("operational ADS Base content digest mismatch")
    raw_tables = manifest.get("tables")
    if not isinstance(raw_tables, list):
        raise OperationalADSBaseError("operational ADS Base tables are missing")
    by_name: dict[str, Mapping[str, Any]] = {}
    for item in raw_tables:
        if not isinstance(item, dict):
            raise OperationalADSBaseError("operational ADS Base table manifest is invalid")
        name = _required_text(item, "name")
        if name in by_name:
            raise OperationalADSBaseError(f"duplicate operational ADS Base table: {name}")
        by_name[name] = item
    if set(by_name) != set(policy.tables):
        raise OperationalADSBaseError("operational ADS Base table set is incomplete")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise OperationalADSBaseError("P1 dependencies are required for ADS Base") from exc
    connection = duckdb.connect(":memory:")
    table_parts: dict[str, tuple[str, ...]] = {}
    table_roles: dict[str, str] = {}
    try:
        for name in sorted(policy.tables):
            contract = policy.tables[name]
            raw = by_name[name]
            path, part = _verify_table_part(package_root, raw=raw, contract=contract)
            if contract.format == "parquet":
                _verify_parquet_table(
                    connection,
                    path=path,
                    raw=raw,
                    part=part,
                    contract=contract,
                    verify_logical_content=verify_logical_content,
                )
            else:
                _verify_json_table(path=path, raw=raw, part=part, contract=contract)
            if (
                part.get("max_date") is not None
                and _parse_date(part["max_date"], f"tables.{name}.max_date") > through_date
            ):
                raise OperationalADSBaseError(f"table crosses the Base through_date: {name}")
            table_parts[name] = (_required_text(part, "path"),)
            table_roles[name] = contract.role
    finally:
        connection.close()
    declared = {part for parts in table_parts.values() for part in parts}
    actual = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "READY"}
    }
    if declared != actual:
        raise OperationalADSBaseError("operational ADS Base file set differs from its manifest")
    plan_digest = _required_text(manifest, "plan_digest")
    if not _SHA256.fullmatch(plan_digest):
        raise OperationalADSBaseError("operational ADS Base plan digest is invalid")
    return OperationalADSBaseBundle(
        root=package_root,
        package_id=package_id,
        phase=phase,
        through_date=through_date,
        content_digest=content_digest,
        manifest_sha256=manifest_sha256,
        plan_digest=plan_digest,
        table_parts=table_parts,
        table_roles=table_roles,
    )


def open_operational_ads_base(
    root: Path,
    *,
    policy: OperationalADSBasePolicy,
    reader_role: str,
) -> OperationalADSBaseAccess:
    try:
        allowed = policy.consumer_roles[reader_role]
    except KeyError as exc:
        raise OperationalADSBaseError(
            f"unknown operational ADS Base reader role: {reader_role}"
        ) from exc
    return OperationalADSBaseAccess(
        _bundle=verify_operational_ads_base(root, policy=policy),
        _allowed_roles=allowed,
    )


def _merged_source_revisions(
    raw: Mapping[str, Any], table_name: str
) -> tuple[dict[str, str], tuple[str, ...], tuple[datetime, ...]]:
    lineage = raw.get("source_lineage")
    if not isinstance(lineage, list) or not lineage:
        raise OperationalADSBaseError(f"replay table has no retained source lineage: {table_name}")
    revisions: dict[str, str] = {}
    parent_ids: set[str] = set()
    cutoffs: list[datetime] = []
    for index, item in enumerate(lineage):
        if not isinstance(item, dict):
            raise OperationalADSBaseError(f"replay source lineage is invalid: {table_name}")
        candidate = item.get("source_manifest", item)
        if not isinstance(candidate, dict):
            raise OperationalADSBaseError(f"replay source manifest is invalid: {table_name}")
        source_revisions = candidate.get("source_revisions")
        if isinstance(source_revisions, dict):
            for key, value in source_revisions.items():
                key_text = str(key)
                value_text = str(value)
                previous = revisions.get(key_text)
                if previous is not None and previous != value_text:
                    raise OperationalADSBaseError(
                        f"replay source revisions conflict: {table_name}.{key_text}"
                    )
                revisions[key_text] = value_text
        manifest_id = candidate.get("manifest_id")
        if isinstance(manifest_id, str) and manifest_id:
            parent_ids.add(manifest_id)
        cutoff = candidate.get("knowledge_cutoff")
        if isinstance(cutoff, str):
            cutoffs.append(_parse_datetime(cutoff, f"{table_name}.knowledge_cutoff[{index}]"))
    return revisions, tuple(sorted(parent_ids)), tuple(cutoffs)


def load_operational_ads_replay_reader(
    root: Path,
    *,
    policy: OperationalADSBasePolicy,
    corporate_action_policy: CorporateActionNormalizationPolicy,
) -> StrategyReplayADSFrameReader:
    """Open the existing typed replay reader from a clean verified Base package."""

    bundle = verify_operational_ads_base(root, policy=policy, verify_logical_content=True)
    manifest = _load_object(bundle.root / "manifest.json")
    replay_contract = manifest.get("replay_contract")
    ranges = manifest.get("ranges")
    ledger = manifest.get("generation_ledger")
    table_values = manifest.get("tables")
    publisher = manifest.get("publisher")
    upstreams = manifest.get("upstreams")
    if (
        not isinstance(replay_contract, dict)
        or replay_contract.get("corporate_action_policy_version")
        != corporate_action_policy.policy_version
        or not isinstance(ranges, dict)
        or not isinstance(ledger, list)
        or not ledger
        or any(not isinstance(item, dict) for item in ledger)
        or not isinstance(table_values, list)
        or not isinstance(publisher, dict)
        or not isinstance(upstreams, list)
    ):
        raise OperationalADSBaseError("operational ADS Base replay envelope is invalid")
    by_name = {
        item["name"]: item
        for item in table_values
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    missing = REQUIRED_HISTORICAL_REPLAY_DATASETS - set(by_name)
    if missing:
        raise OperationalADSBaseError(
            "operational ADS Base is missing replay datasets: " + ", ".join(sorted(missing))
        )
    built_at = _parse_datetime(manifest.get("built_at"), "built_at")
    ledger_cutoffs = tuple(
        _parse_datetime(item.get("knowledge_cutoff"), "generation_ledger.knowledge_cutoff")
        for item in ledger
    )
    datasets: list[DatasetManifest] = []
    for name in sorted(REQUIRED_HISTORICAL_REPLAY_DATASETS):
        raw = by_name[name]
        quality = raw.get("quality")
        parts = raw.get("parts")
        if (
            not isinstance(quality, dict)
            or not isinstance(parts, list)
            or len(parts) != 1
            or not isinstance(parts[0], dict)
        ):
            raise OperationalADSBaseError(f"replay table manifest is invalid: {name}")
        part = parts[0]
        revisions, parent_ids, source_cutoffs = _merged_source_revisions(raw, name)
        revisions["operational_ads_base_content_digest"] = bundle.content_digest
        revisions["operational_ads_base_table_content_digest"] = _required_text(
            raw, "table_content_digest"
        )
        if name == "corporate_actions_normalized":
            exception_quality = by_name["corporate_action_exceptions"].get("quality")
            if not isinstance(exception_quality, dict) or not isinstance(
                exception_quality.get("row_count"), int
            ):
                raise OperationalADSBaseError("corporate-action exception quality is invalid")
            revisions["normalization_exception_count"] = str(exception_quality["row_count"])
        path = _required_text(part, "path")
        checksum = _required_text(part, "sha256")
        datasets.append(
            DatasetManifest(
                dataset=name,
                version=policy.contract_id,
                source_revisions=revisions,
                row_count=int(quality["row_count"]),
                partitions=(path,),
                schema_hash=_required_text(part, "schema_sha256"),
                content_checksums={path: checksum},
                created_at=built_at,
                knowledge_cutoff=max((*source_cutoffs, *ledger_cutoffs)),
                parent_manifest_ids=parent_ids,
            )
        )
    decision_start = _parse_date(ranges.get("decision_start"), "ranges.decision_start")
    decision_end = _parse_date(ranges.get("decision_end"), "ranges.decision_end")
    execution_end = _parse_date(ranges.get("execution_end"), "ranges.execution_end")
    source_cutoff = max(dataset.knowledge_cutoff for dataset in datasets)
    historical = HistoricalReplayBundle(
        root=bundle.root,
        snapshot=SnapshotRef(
            market_date=execution_end,
            knowledge_cutoff=source_cutoff,
            source_revision=(
                f"operational-ads-base={bundle.package_id}|content={bundle.content_digest}"
                f"|manifest={bundle.manifest_sha256}"
            ),
            snapshot_id=bundle.package_id,
        ),
        universe_policy_version=_required_text(replay_contract, "universe_policy_version"),
        datasets=tuple(datasets),
    )
    try:
        historical = validate_historical_replay_bundle_contract(
            historical,
            corporate_action_policy,
        )
    except HistoricalReplayBundleError as exc:
        raise OperationalADSBaseError("operational ADS Base replay semantics failed") from exc
    pit_bindings: dict[date, StrategyReplayADSPITBinding] = {}
    for item in ledger:
        market_date = _parse_date(item.get("market_date"), "generation_ledger.market_date")
        binding = StrategyReplayADSPITBinding(
            decision_date=market_date,
            decision_cutoff=_parse_datetime(
                item.get("decision_cutoff"), "generation_ledger.decision_cutoff"
            ),
            source_snapshot_id=bundle.package_id,
            source_knowledge_cutoff=_parse_datetime(
                item.get("knowledge_cutoff"), "generation_ledger.knowledge_cutoff"
            ),
            evidence_mode="operational_ads_base_generation_ledger_v1",
            evidence_status="per_decision_publication_proven",
        )
        pit_bindings[market_date] = binding
    profile_parts = {
        name: bundle.table_parts[name]
        for name in ("benchmark_curve", "static_industry_mapping")
        if name in bundle.table_parts
    }
    if set(profile_parts) != {"benchmark_curve", "static_industry_mapping"}:
        raise OperationalADSBaseError("operational ADS Base replay profile references are missing")
    catalog_binding = next(
        (
            item
            for item in upstreams
            if isinstance(item, dict) and item.get("role") == "canonical_catalog"
        ),
        None,
    )
    if not isinstance(catalog_binding, dict):
        raise OperationalADSBaseError("operational ADS Base canonical catalog binding is missing")
    runtime = publisher.get("runtime")
    if not isinstance(runtime, dict):
        raise OperationalADSBaseError("operational ADS Base publisher runtime is invalid")
    replay_bundle = StrategyReplayADSBundle(
        root=bundle.root,
        bundle_id=bundle.package_id,
        bundle_content_digest=bundle.content_digest,
        manifest_sha256=bundle.manifest_sha256,
        scope_id="operational_base",
        reader_role="replay_runner",
        decision_start=decision_start,
        decision_end=decision_end,
        execution_end=execution_end,
        publisher_code_revision=_required_text(publisher, "code_revision"),
        publisher_runtime=runtime,
        source_snapshot_id=bundle.package_id,
        source_manifest_sha256=_required_text(catalog_binding, "manifest_sha256"),
        pit_mapping_digest=_canonical_digest(ledger),
        pit_bindings=pit_bindings,
        profile_parts=profile_parts,
        historical_bundle=historical,
    )
    try:
        return StrategyReplayADSFrameReader(replay_bundle)
    except StrategyReplayADSError as exc:
        raise OperationalADSBaseError("operational ADS Base replay reader failed") from exc


def smoke_operational_ads_replay(
    root: Path,
    *,
    policy: OperationalADSBasePolicy,
    corporate_action_policy: CorporateActionNormalizationPolicy,
    scope_start: date,
    scope_end: date,
) -> Mapping[str, object]:
    """Run a bounded existing-reader audit without exposing the Base root to callbacks."""

    reader = load_operational_ads_replay_reader(
        root,
        policy=policy,
        corporate_action_policy=corporate_action_policy,
    )
    report = reader.audit(scope_start, scope_end)
    return {
        "base": dict(reader.ads_identity),
        "scope_start": scope_start.isoformat(),
        "scope_end": scope_end.isoformat(),
        "frame_audit": report.to_dict(),
        "runner_only_access_count": reader.runner_only_access_count,
        "access_ledger_digest": reader.access_ledger_digest,
        "research_only": True,
        "execution_authorized": False,
    }


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as descriptor:
            temporary = Path(descriptor.name)
            json.dump(value, descriptor, ensure_ascii=True, indent=2, sort_keys=True)
            descriptor.write("\n")
            descriptor.flush()
            os.fsync(descriptor.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)


@contextmanager
def _base_current_lock(root: Path) -> Iterator[None]:
    try:
        import fcntl
    except ModuleNotFoundError as exc:  # pragma: no cover - OCI publication is Linux-only
        raise OperationalADSBaseError("BASE_CURRENT locking requires fcntl") from exc
    lock_path = root / ".BASE_CURRENT.lock"
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o640,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def promote_operational_ads_base(
    *,
    source: Path,
    base_root: Path,
    policy: OperationalADSBasePolicy,
    expected_current_manifest_sha256: str,
) -> OperationalADSBaseBundle:
    """Copy one verified Base immutably and atomically CAS only BASE_CURRENT.json."""

    if expected_current_manifest_sha256 != "absent" and not _SHA256.fullmatch(
        expected_current_manifest_sha256
    ):
        raise OperationalADSBaseError("expected current digest must be sha256 or absent")
    verified = verify_operational_ads_base(source, policy=policy, verify_logical_content=True)
    root = base_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / verified.package_id
    if destination.exists():
        published = verify_operational_ads_base(
            destination, policy=policy, verify_logical_content=True
        )
        if (
            published.content_digest != verified.content_digest
            or published.manifest_sha256 != verified.manifest_sha256
        ):
            raise OperationalADSBaseError("published package id already binds different content")
    else:
        staging_parent = Path(tempfile.mkdtemp(prefix=f".{verified.package_id}.", dir=root))
        staging = staging_parent / verified.package_id
        try:
            shutil.copytree(verified.root, staging)
            copied = verify_operational_ads_base(
                staging, policy=policy, verify_logical_content=True
            )
            if copied.content_digest != verified.content_digest:
                raise OperationalADSBaseError("copied operational ADS Base content differs")
            os.replace(staging, destination)
            staging_parent.rmdir()
        except BaseException:
            shutil.rmtree(staging_parent, ignore_errors=True)
            raise
        published = verify_operational_ads_base(destination, policy=policy)
    descriptor = {
        "schema_version": BASE_SCHEMA_VERSION,
        "kind": "operational_ads_base_current",
        "package_id": published.package_id,
        "phase": published.phase,
        "through_date": published.through_date.isoformat(),
        "content_digest": published.content_digest,
        "manifest_sha256": published.manifest_sha256,
        "manifest_path": f"{published.package_id}/manifest.json",
    }
    with _base_current_lock(root):
        current = root / "BASE_CURRENT.json"
        current_value = _load_object(current) if current.exists() else None
        current_digest = (
            current_value.get("manifest_sha256") if isinstance(current_value, dict) else "absent"
        )
        if current_value == descriptor:
            return published
        if current_digest != expected_current_manifest_sha256:
            raise OperationalADSBaseError("BASE_CURRENT compare-and-swap precondition failed")
        _atomic_json(current, descriptor)
    return published


def load_operational_ads_base_current(
    descriptor: Path, *, policy: OperationalADSBasePolicy
) -> OperationalADSBaseBundle:
    value = _load_object(descriptor)
    if value.get("kind") != "operational_ads_base_current":
        raise OperationalADSBaseError("BASE_CURRENT descriptor kind is invalid")
    relative = _required_text(value, "manifest_path")
    manifest_path = _inside(descriptor.parent, relative, "BASE_CURRENT.manifest_path")
    bundle = verify_operational_ads_base(manifest_path.parent, policy=policy)
    if (
        bundle.package_id != value.get("package_id")
        or bundle.phase != value.get("phase")
        or bundle.through_date.isoformat() != value.get("through_date")
        or bundle.content_digest != value.get("content_digest")
        or bundle.manifest_sha256 != value.get("manifest_sha256")
    ):
        raise OperationalADSBaseError("BASE_CURRENT descriptor differs from its package")
    return bundle
