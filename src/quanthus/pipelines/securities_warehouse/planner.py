"""Freeze exact ODS inputs for one DWD/DWS v2 construction run."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import resource
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.contracts import OdsReleaseError, verify_ods_release
from quanthus.pipelines.ods_base_rollup import (
    OdsBaseRollupError,
    verify_ods_base_rollup,
)


class WarehousePlanningError(RuntimeError):
    """Raised when an input package cannot produce a closed construction plan."""


@dataclass(frozen=True)
class InputPlanResult:
    root: Path
    plan_digest: str
    input_kind: str
    input_id: str
    input_manifest_sha256: str
    file_count: int
    source_record_count: int
    required_free_bytes: int
    available_free_bytes: int
    capacity_sufficient: bool


_MODES = {"base", "daily", "weekly", "correction", "backfill"}
_MODEL_STATUSES = {"approved_for_scripted_build_implementation"}
_MAPPING_GROUPS = (
    "static_sources",
    "release_artifacts",
    "weekly_artifacts",
    "hotfix_artifacts",
    "release_auxiliary_artifacts",
)
_DAILY_DATASET_PREFIXES = (
    "daily_intraday_1m_",
    "daily_intraday_5m_",
    "daily_market_",
    "daily_events_",
)
_MARGIN_PPM = 200_000
_DWS_TO_DWD_PPM = 1_000_000
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WarehousePlanningError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise WarehousePlanningError(f"JSON root must be an object: {path}")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WarehousePlanningError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise WarehousePlanningError(f"{field} must be an array")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise WarehousePlanningError(f"{key} must be a non-empty string")
    return result


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _safe_relative(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise WarehousePlanningError(f"{field} must be a relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise WarehousePlanningError(f"{field} must be a safe relative path")
    return path.as_posix()


def _utc_datetime(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WarehousePlanningError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WarehousePlanningError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _date(value: str, field: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise WarehousePlanningError(f"{field} must use YYYY-MM-DD") from exc


def _validate_mapping(
    raw: object,
    *,
    identity_field: str,
    allowed_dispositions: set[str],
    table_names: set[str],
    field: str,
) -> dict[str, Any]:
    item = dict(_mapping(raw, field))
    identity = _text(item, identity_field)
    disposition = _text(item, "disposition")
    if disposition not in allowed_dispositions:
        raise WarehousePlanningError(f"{field} has unsupported disposition: {disposition}")
    targets = list(_sequence(item.get("target_tables"), f"{field}.target_tables"))
    if not all(isinstance(target, str) and target in table_names for target in targets):
        raise WarehousePlanningError(f"{field} references an unknown DWD table")
    if disposition in {"ods_only", "deferred"} and targets:
        raise WarehousePlanningError(f"{field} {disposition} mapping cannot target DWD")
    if disposition not in {"ods_only", "deferred", "source_gap"} and not targets:
        raise WarehousePlanningError(f"{field} {disposition} mapping requires a target")
    aliases = item.get("aliases", [])
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and alias for alias in aliases
    ):
        raise WarehousePlanningError(f"{field}.aliases must be strings")
    item[identity_field] = identity
    item["target_tables"] = targets
    item["aliases"] = aliases
    item.setdefault("reason", f"declared {field} disposition")
    return item


def _dependency_edges(value: object, field: str) -> set[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    for index, raw in enumerate(_sequence(value, field)):
        edge = _sequence(raw, f"{field}[{index}]")
        if (
            len(edge) != 2
            or not all(isinstance(issue, int) and not isinstance(issue, bool) for issue in edge)
            or edge[0] == edge[1]
        ):
            raise WarehousePlanningError(
                f"{field}[{index}] must be a directed pair of distinct issue numbers"
            )
        pair = (edge[0], edge[1])
        if pair in edges:
            raise WarehousePlanningError(f"{field} contains a duplicate edge")
        edges.add(pair)
    return edges


def _validate_build_execution_contract(
    model: Mapping[str, Any],
    tables: Sequence[Any],
    table_names: set[str],
) -> None:
    execution = _mapping(
        model.get("build_execution_contract"), "model.build_execution_contract"
    )
    raw_ownership = _mapping(
        execution.get("dwd_script_ownership"),
        "build_execution_contract.dwd_script_ownership",
    )
    owner_by_table: dict[str, int] = {}
    for raw_issue, raw_tables in raw_ownership.items():
        if not isinstance(raw_issue, str) or not raw_issue.isdigit():
            raise WarehousePlanningError("DWD script owner must be an issue number")
        issue = int(raw_issue)
        for table_name in _sequence(raw_tables, f"dwd_script_ownership.{raw_issue}"):
            if not isinstance(table_name, str) or table_name not in table_names:
                raise WarehousePlanningError("DWD script ownership references an unknown table")
            if table_name in owner_by_table:
                raise WarehousePlanningError("DWD table has more than one script owner")
            owner_by_table[table_name] = issue
    if set(owner_by_table) != table_names:
        raise WarehousePlanningError("every DWD table must have exactly one script owner")

    derived_cross_owner_edges: set[tuple[int, int]] = set()
    for raw in tables:
        table = _mapping(raw, "model.tables[]")
        table_name = _text(table, "name")
        for index, raw_foreign_key in enumerate(
            _sequence(table.get("foreign_keys"), f"{table_name}.foreign_keys")
        ):
            foreign_key = _mapping(
                raw_foreign_key, f"{table_name}.foreign_keys[{index}]"
            )
            referenced_table = _text(foreign_key, "table")
            if referenced_table not in table_names:
                raise WarehousePlanningError(
                    f"DWD foreign key references an unknown table: {referenced_table}"
                )
            source_owner = owner_by_table[referenced_table]
            target_owner = owner_by_table[table_name]
            if source_owner != target_owner:
                derived_cross_owner_edges.add((source_owner, target_owner))

    declared_cross_owner_edges = _dependency_edges(
        execution.get("cross_owner_foreign_key_edges"),
        "build_execution_contract.cross_owner_foreign_key_edges",
    )
    if declared_cross_owner_edges != derived_cross_owner_edges:
        raise WarehousePlanningError(
            "cross-owner foreign-key edges do not match DWD ownership and table contracts"
        )

    required_edges = _dependency_edges(
        execution.get("required_dependency_edges"),
        "build_execution_contract.required_dependency_edges",
    )
    if not declared_cross_owner_edges.issubset(required_edges):
        raise WarehousePlanningError(
            "required dependency edges omit a cross-owner foreign-key edge"
        )

    remaining_edges = set(required_edges)
    remaining_nodes = {issue for edge in required_edges for issue in edge}
    while remaining_edges:
        roots = remaining_nodes.difference(target for _, target in remaining_edges)
        if not roots:
            raise WarehousePlanningError("required dependency edges contain a cycle")
        remaining_edges = {
            edge for edge in remaining_edges if edge[0] not in roots
        }
        remaining_nodes.difference_update(roots)


def _model_contract(path: Path) -> tuple[dict[str, Any], str]:
    model = _object(path)
    if model.get("schema_version") != 1 or model.get("status") not in _MODEL_STATUSES:
        raise WarehousePlanningError(
            "DWD/DWS model is not approved for scripted build implementation"
        )
    tables = list(_sequence(model.get("tables"), "model.tables"))
    table_names = {_text(_mapping(table, "model.tables[]"), "name") for table in tables}
    domains = list(_sequence(model.get("domain_catalog"), "model.domain_catalog"))
    dimensions = list(
        _sequence(
            _mapping(model.get("dws_model"), "model.dws_model").get("dimensions"),
            "dimensions",
        )
    )
    facts = list(
        _sequence(_mapping(model.get("dws_model"), "model.dws_model").get("facts"), "facts")
    )
    domain_ids = {
        _text(_mapping(item, "model.domain_catalog[]"), "id") for item in domains
    }
    dimension_names = {
        _text(_mapping(item, "model.dws_model.dimensions[]"), "name")
        for item in dimensions
    }
    fact_names = {
        _text(_mapping(item, "model.dws_model.facts[]"), "name") for item in facts
    }
    if (
        len(tables) != 33
        or len(table_names) != 33
        or len(domains) != 7
        or len(domain_ids) != 7
        or len(dimensions) != 10
        or len(dimension_names) != 10
        or len(facts) != 13
        or len(fact_names) != 13
    ):
        raise WarehousePlanningError(
            "model must contain exactly 7 domains, 33 DWD objects, "
            "10 dimensions, and 13 facts"
        )
    roles: dict[str, int] = defaultdict(int)
    declared_core: set[str] = set()
    for raw in domains:
        domain = _mapping(raw, "model.domain_catalog[]")
        for table_name in _sequence(domain.get("core_objects"), "domain.core_objects"):
            if not isinstance(table_name, str) or table_name not in table_names:
                raise WarehousePlanningError("domain core_objects references an unknown DWD table")
            declared_core.add(table_name)
    actual_core: set[str] = set()
    for raw in tables:
        table = _mapping(raw, "model.tables[]")
        name = _text(table, "name")
        role = _text(table, "object_role")
        domain_name = _text(table, "domain")
        if role not in {"core_object", "supporting_structure", "governance_sidecar"}:
            raise WarehousePlanningError(f"unsupported object role: {role}")
        if role == "governance_sidecar":
            if domain_name != "governance_sidecar":
                raise WarehousePlanningError("governance sidecar must use its reserved domain")
        elif domain_name not in domain_ids:
            raise WarehousePlanningError(f"DWD table has an unknown business domain: {name}")
        roles[role] += 1
        if role == "core_object":
            actual_core.add(name)
    if roles != {"core_object": 14, "supporting_structure": 18, "governance_sidecar": 1}:
        raise WarehousePlanningError(
            "model must contain exactly 14 core objects, 18 supporting structures, "
            "and 1 governance sidecar"
        )
    if declared_core != actual_core:
        raise WarehousePlanningError("domain core object declarations do not match DWD table roles")
    _validate_build_execution_contract(model, tables, table_names)

    delivery_states = set(_sequence(model.get("delivery_states"), "delivery_states"))
    for raw in dimensions:
        dimension = _mapping(raw, "model.dws_model.dimensions[]")
        _text(dimension, "grain")
        _text(dimension, "key")
        if dimension.get("delivery_state") not in delivery_states:
            raise WarehousePlanningError("DWS dimension has an unsupported delivery state")
        sources = _sequence(dimension.get("source_tables"), "dimension.source_tables")
        if any(not isinstance(source, str) or source not in table_names for source in sources):
            raise WarehousePlanningError("DWS dimension references an unknown DWD source table")
    for raw in facts:
        fact = _mapping(raw, "model.dws_model.facts[]")
        name = _text(fact, "name")
        _text(fact, "grain")
        sources = _sequence(fact.get("source_tables"), "fact.source_tables")
        required_dimensions = _sequence(fact.get("dimensions"), "fact.dimensions")
        optional_dimensions = _sequence(
            fact.get("optional_dimensions"), "fact.optional_dimensions"
        )
        if not sources or any(
            not isinstance(source, str) or source not in table_names for source in sources
        ):
            raise WarehousePlanningError(f"DWS fact has invalid source tables: {name}")
        all_dimensions = [*required_dimensions, *optional_dimensions]
        if (
            any(
                not isinstance(dimension, str) or dimension not in dimension_names
                for dimension in all_dimensions
            )
            or len(all_dimensions) != len(set(all_dimensions))
            or "dws_dim_snapshot" not in required_dimensions
        ):
            raise WarehousePlanningError(f"DWS fact has invalid dimension bindings: {name}")
        if fact.get("delivery_state") not in delivery_states:
            raise WarehousePlanningError(f"DWS fact has an unsupported delivery state: {name}")
        _mapping(fact.get("measures"), f"{name}.measures")
    source_contract = _mapping(model.get("ods_source_contract"), "model.ods_source_contract")
    allowed = set(_sequence(source_contract.get("allowed_dispositions"), "allowed_dispositions"))
    if not allowed or not all(isinstance(item, str) for item in allowed):
        raise WarehousePlanningError("allowed_dispositions is invalid")
    for group in _MAPPING_GROUPS:
        identities: set[str] = set()
        field_name = "source_id" if group == "static_sources" else "artifact"
        for index, raw in enumerate(_sequence(source_contract.get(group, []), group)):
            item = _validate_mapping(
                raw,
                identity_field=field_name,
                allowed_dispositions=allowed,
                table_names=table_names,
                field=f"{group}[{index}]",
            )
            identities_to_add = [item[field_name], *item["aliases"]]
            if identities.intersection(identities_to_add):
                raise WarehousePlanningError(f"duplicate mapping identity in {group}")
            identities.update(identities_to_add)
    container_members = _sequence(source_contract.get("container_members", []), "container_members")
    seen_members: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(container_members):
        item = dict(_mapping(raw, f"container_members[{index}]"))
        key = (
            _text(item, "container_source_id"),
            _text(item, "member_type"),
            _text(item, "member"),
        )
        if key in seen_members:
            raise WarehousePlanningError("duplicate container member mapping")
        seen_members.add(key)
        _validate_mapping(
            {**item, "member_identity": "|".join(key)},
            identity_field="member_identity",
            allowed_dispositions=allowed,
            table_names=table_names,
            field=f"container_members[{index}]",
        )
    return model, _sha256(path)


def _input_identity_override(
    path: Path,
    *,
    model_sha256: str,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    value = _object(path)
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "dwd_dws_input_identity_override"
        or value.get("base_model_sha256") != model_sha256
    ):
        raise WarehousePlanningError(
            "input identity override does not bind the exact base model"
        )
    override_id = _text(value, "override_id")
    minimum_knowledge_cutoff = _utc_datetime(
        _text(value, "minimum_knowledge_cutoff"),
        "minimum_knowledge_cutoff",
    )
    contract = _mapping(value.get("build_input_contract"), "build_input_contract")
    required = {"bundle_id", "content_digest", "manifest_sha256", "through_date"}
    if set(contract) != required or any(
        not isinstance(contract.get(field), str) or not contract.get(field)
        for field in required
    ):
        raise WarehousePlanningError(
            "input identity override must contain only the exact ODS Base identity"
        )
    authority = {
        "schema_version": 1,
        "kind": "dwd_dws_input_identity_override",
        "override_id": override_id,
        "base_model_sha256": model_sha256,
        "minimum_knowledge_cutoff": minimum_knowledge_cutoff,
        "build_input_contract": dict(contract),
    }
    return contract, {
        **authority,
        "canonical_digest": _canonical_digest(authority),
        "sha256": _sha256(path),
    }


def _lookups(model: Mapping[str, Any]) -> dict[str, Any]:
    contract = _mapping(model.get("ods_source_contract"), "model.ods_source_contract")

    def indexed(group: str, identity_field: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for raw in _sequence(contract.get(group, []), group):
            item = dict(_mapping(raw, f"{group}[]"))
            names = [_text(item, identity_field), *item.get("aliases", [])]
            for name in names:
                result[str(name)] = item
        return result

    members: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in _sequence(contract.get("container_members", []), "container_members"):
        item = dict(_mapping(raw, "container_members[]"))
        key = (
            str(item["container_source_id"]),
            str(item["member_type"]),
            str(item["member"]),
        )
        members[key] = item
    return {
        "static": indexed("static_sources", "source_id"),
        "release": indexed("release_artifacts", "artifact"),
        "weekly": indexed("weekly_artifacts", "artifact"),
        "hotfix": indexed("hotfix_artifacts", "artifact"),
        "auxiliary": indexed("release_auxiliary_artifacts", "artifact"),
        "members": members,
    }


def _normalized_mapping(
    mapping: Mapping[str, Any],
    *,
    mapping_group: str,
    mapping_identity: str,
) -> dict[str, Any]:
    return {
        "disposition": str(mapping["disposition"]),
        "mapping_group": mapping_group,
        "mapping_identity": mapping_identity,
        "reason": str(mapping.get("reason", "declared source disposition")),
        "target_tables": sorted(str(item) for item in mapping.get("target_tables", [])),
    }


def _release_candidate(dataset_id: str) -> str | None:
    for prefix in _DAILY_DATASET_PREFIXES:
        if dataset_id.startswith(prefix):
            return dataset_id.removeprefix(prefix)
    return None


def _weekly_artifact_name(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if "artifacts" not in parts:
        return None
    return PurePosixPath(path).stem


def _mapping_for_base_file(
    item: Mapping[str, Any], lookups: Mapping[str, Any]
) -> tuple[dict[str, Any], str, str]:
    dataset_id = _text(item, "dataset_id")
    path = _safe_relative(item.get("path"), "content.files.path")
    if dataset_id in lookups["hotfix"]:
        return lookups["hotfix"][dataset_id], "hotfix_artifacts", dataset_id
    if dataset_id.startswith("source_evidence_weekly_"):
        artifact = _weekly_artifact_name(path)
        if artifact and artifact in lookups["weekly"]:
            return lookups["weekly"][artifact], "weekly_artifacts", artifact
        if artifact and artifact in lookups["auxiliary"]:
            return lookups["auxiliary"][artifact], "release_auxiliary_artifacts", artifact
    release_name = _release_candidate(dataset_id)
    if release_name and release_name in lookups["release"]:
        return lookups["release"][release_name], "release_artifacts", release_name
    if release_name and release_name in lookups["auxiliary"]:
        return lookups["auxiliary"][release_name], "release_auxiliary_artifacts", release_name
    if dataset_id == "daily_release_metadata" and "release_metadata" in lookups["auxiliary"]:
        return (
            lookups["auxiliary"]["release_metadata"],
            "release_auxiliary_artifacts",
            "release_metadata",
        )
    if dataset_id in lookups["static"]:
        return lookups["static"][dataset_id], "static_sources", dataset_id
    raise WarehousePlanningError(f"unclassified ODS Base dataset: {dataset_id} ({path})")


def _file_record(
    item: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    mapping_group: str,
    mapping_identity: str,
) -> dict[str, Any]:
    size = item.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise WarehousePlanningError("content.files.size_bytes must be a non-negative integer")
    digest = _text(item, "sha256")
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise WarehousePlanningError("content.files.sha256 is invalid")
    result = {
        "dataset_id": _text(item, "dataset_id"),
        "format": _text(item, "format"),
        "path": _safe_relative(item.get("path"), "content.files.path"),
        "record_type": "file",
        "role": _text(item, "role"),
        "sha256": digest,
        "size_bytes": size,
        **_normalized_mapping(
            mapping,
            mapping_group=mapping_group,
            mapping_identity=mapping_identity,
        ),
    }
    for field in ("row_count", "rollup_source", "schema_sha256"):
        if field in item:
            result[field] = item[field]
    return result


def _base_records(manifest: Mapping[str, Any], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = _mapping(manifest.get("content"), "manifest.content")
    lookups = _lookups(model)
    records: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw in _sequence(content.get("files"), "manifest.content.files"):
        item = _mapping(raw, "manifest.content.files[]")
        mapping, group, identity = _mapping_for_base_file(item, lookups)
        record = _file_record(item, mapping, mapping_group=group, mapping_identity=identity)
        path = str(record["path"])
        if path in seen_paths:
            raise WarehousePlanningError(f"duplicate ODS Base file path: {path}")
        seen_paths.add(path)
        records.append(record)
        tables = _sequence(item.get("tables", []), f"{path}.tables")
        expected_members = {
            member
            for container_source_id, member_type, member in lookups["members"]
            if container_source_id == record["dataset_id"] and member_type == "sqlite_table"
        }
        seen_members: set[str] = set()
        if tables:
            for table_raw in tables:
                table = _mapping(table_raw, f"{path}.tables[]")
                name = _text(table, "name")
                if name in seen_members:
                    raise WarehousePlanningError(
                        f"duplicate container member: {record['dataset_id']}#{name}"
                    )
                seen_members.add(name)
                member_key = (str(record["dataset_id"]), "sqlite_table", name)
                member_mapping = lookups["members"].get(member_key)
                if member_mapping is None:
                    raise WarehousePlanningError(
                        f"unclassified container member: {member_key[0]}#{name}"
                    )
                row_count = table.get("row_count")
                if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
                    raise WarehousePlanningError(
                        f"invalid SQLite row count: {member_key[0]}#{name}"
                    )
                records.append(
                    {
                        "container_path": path,
                        "container_source_id": member_key[0],
                        "member": name,
                        "member_type": "sqlite_table",
                        "path": f"{path}#table={name}",
                        "record_type": "container_member",
                        "row_count": row_count,
                        "schema_sha256": _text(table, "schema_sha256"),
                        **_normalized_mapping(
                            member_mapping,
                            mapping_group="container_members",
                            mapping_identity="|".join(member_key),
                        ),
                    }
                )
        if seen_members != expected_members:
            missing = sorted(expected_members.difference(seen_members))
            unexpected = sorted(seen_members.difference(expected_members))
            raise WarehousePlanningError(
                f"container member inventory mismatch: {record['dataset_id']} "
                f"missing={missing} unexpected={unexpected}"
            )
    return sorted(records, key=lambda item: (str(item["path"]), str(item["record_type"])))


def _release_records(
    release_dir: Path, model: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    verified = verify_ods_release(release_dir)
    release = verified.release
    lookups = _lookups(model)
    group_name = "weekly" if release.schedule == "weekly" else "release"
    mapping_group = "weekly_artifacts" if release.schedule == "weekly" else "release_artifacts"
    records: list[dict[str, Any]] = []
    for artifact in release.artifacts:
        mapping = lookups[group_name].get(artifact.name)
        selected_group = mapping_group
        if mapping is None:
            mapping = lookups["auxiliary"].get(artifact.name)
            selected_group = "release_auxiliary_artifacts"
        if mapping is None:
            raise WarehousePlanningError(
                f"unclassified {release.schedule} release artifact: {artifact.name}"
            )
        records.append(
            {
                "artifact": artifact.name,
                "dataset_id": artifact.name,
                "format": PurePosixPath(artifact.relative_path).suffix.lstrip(".") or "binary",
                "knowledge_cutoff": artifact.knowledge_cutoff.astimezone(timezone.utc).isoformat(),
                "path": artifact.relative_path.as_posix(),
                "record_type": "file",
                "role": f"ods_{release.schedule}_artifact",
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "source_revision": artifact.source_revision,
                **_normalized_mapping(
                    mapping,
                    mapping_group=selected_group,
                    mapping_identity=artifact.name,
                ),
            }
        )
    identity = {
        "artifact_count": len(release.artifacts),
        "created_at": release.created_at.astimezone(timezone.utc).isoformat(),
        "input_id": release.release_id,
        "kind": "ods_release",
        "manifest_sha256": verified.manifest_sha256,
        "market_date": release.market_date.isoformat(),
        "producer": release.producer,
        "schedule": release.schedule,
        "verified_bytes": verified.verified_bytes,
    }
    return sorted(records, key=lambda item: str(item["path"])), identity


def _table_domains(model: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, list[str]]]:
    by_table: dict[str, str] = {}
    by_domain: dict[str, list[str]] = defaultdict(list)
    for raw in _sequence(model.get("tables"), "model.tables"):
        table = _mapping(raw, "model.tables[]")
        name = _text(table, "name")
        domain = _text(table, "domain")
        by_table[name] = domain
        by_domain[domain].append(name)
    return by_table, {key: sorted(value) for key, value in by_domain.items()}


def _coverage(
    records: Sequence[Mapping[str, Any]], model: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_table, expected_by_domain = _table_domains(model)
    table_sources: dict[str, set[str]] = defaultdict(set)
    domain_records: dict[str, set[str]] = defaultdict(set)
    domain_file_bytes: dict[tuple[str, str], int] = {}
    source_gaps: dict[str, list[str]] = defaultdict(list)
    for record in records:
        record_path = str(record["path"])
        for target in record.get("target_tables", []):
            target_name = str(target)
            domain = by_table[target_name]
            table_sources[target_name].add(record_path)
            domain_records[domain].add(record_path)
            if record.get("record_type") == "file":
                domain_file_bytes[(domain, record_path)] = int(record.get("size_bytes", 0))
            if record.get("disposition") == "source_gap":
                source_gaps[domain].append(record_path)
    domains: list[dict[str, Any]] = []
    for domain, expected_tables in sorted(expected_by_domain.items()):
        missing = sorted(table for table in expected_tables if not table_sources.get(table))
        gaps = sorted(set(source_gaps.get(domain, [])))
        domains.append(
            {
                "domain": domain,
                "expected_tables": expected_tables,
                "missing_source_tables": missing,
                "source_bytes": sum(
                    size
                    for (source_domain, _), size in domain_file_bytes.items()
                    if source_domain == domain
                ),
                "source_gaps": gaps,
                "source_record_count": len(domain_records.get(domain, set())),
                "status": "complete" if not missing and not gaps else "incomplete",
                "table_source_counts": {
                    table: len(table_sources.get(table, set())) for table in expected_tables
                },
            }
        )
    business_order = [
        _text(_mapping(item, "domain_catalog[]"), "id")
        for item in _sequence(model.get("domain_catalog"), "domain_catalog")
    ]
    affected = [
        domain
        for domain in business_order
        if any(item["domain"] == domain and item["source_record_count"] for item in domains)
    ]
    governance = any(
        item["domain"] == "governance_sidecar" and item["source_record_count"]
        for item in domains
    )
    coverage = {
        "schema_version": 1,
        "bse_policy": model["build_input_contract"]["overlay_precedence"]["bse_policy"],
        "domains": domains,
        "scope_statement": "maximum defensible coverage of the exact selected ODS package",
    }
    domain_plan = {
        "affected_dwd_domains": affected,
        "build_governance_sidecar": governance,
        "build_dws": bool(affected),
        "dwd_domain_order": business_order,
    }
    return coverage, domain_plan


def _validate_base_coverage(
    coverage: Mapping[str, Any], domain_plan: Mapping[str, Any]
) -> None:
    domains = _sequence(coverage.get("domains"), "coverage.domains")
    missing = {
        str(item["domain"]): list(item["missing_source_tables"])
        for raw in domains
        for item in [_mapping(raw, "coverage.domains[]")]
        if item.get("missing_source_tables")
    }
    expected_order = list(
        _sequence(domain_plan.get("dwd_domain_order"), "domain_plan.dwd_domain_order")
    )
    affected = list(
        _sequence(domain_plan.get("affected_dwd_domains"), "domain_plan.affected_dwd_domains")
    )
    if missing or affected != expected_order or not domain_plan.get("build_governance_sidecar"):
        raise WarehousePlanningError(
            "ODS Base does not cover every declared DWD object: "
            f"missing={json.dumps(missing, ensure_ascii=True, sort_keys=True)}"
        )


def _financial_uncompressed_bytes(
    package_root: Path, records: Sequence[Mapping[str, Any]]
) -> int | None:
    paths = [
        package_root.joinpath(*PurePosixPath(str(record["path"])).parts)
        for record in records
        if record.get("record_type") == "file"
        and PurePosixPath(str(record["path"])).name == "financial_statements.parquet"
    ]
    if not paths:
        return None
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        return None
    total = 0
    for path in paths:
        try:
            values = pq.read_table(path, columns=["raw_payload_bytes"])["raw_payload_bytes"]
            total += sum(int(value.as_py() or 0) for value in values)
        except (OSError, KeyError, TypeError, ValueError, pa.ArrowException) as exc:
            raise WarehousePlanningError(f"cannot read financial capacity hint: {path}") from exc
    return total


def _capacity(
    package_root: Path,
    records: Sequence[Mapping[str, Any]],
    *,
    available_bytes: int,
) -> dict[str, Any]:
    modeled_files = {
        str(record["path"]): record
        for record in records
        if record.get("record_type") == "file" and record.get("disposition") == "modeled"
    }
    modeled_input = sum(int(record["size_bytes"]) for record in modeled_files.values())
    by_dataset: dict[str, int] = defaultdict(int)
    for record in modeled_files.values():
        by_dataset[str(record["dataset_id"])] += int(record["size_bytes"])
    largest_group = max(by_dataset.values(), default=0)
    financial_compressed = sum(
        int(record["size_bytes"])
        for record in modeled_files.values()
        if PurePosixPath(str(record["path"])).name == "financial_statements.parquet"
    )
    financial_uncompressed = _financial_uncompressed_bytes(
        package_root, tuple(modeled_files.values())
    )
    financial_basis = max(financial_compressed, financial_uncompressed or 0)
    estimated_dwd = max(modeled_input, modeled_input - financial_compressed + financial_basis)
    estimated_dws = math.ceil(estimated_dwd * _DWS_TO_DWD_PPM / 1_000_000)
    largest_stage = max(largest_group, financial_basis)
    subtotal = estimated_dwd + estimated_dws + largest_stage
    safety_margin = math.ceil(subtotal * _MARGIN_PPM / 1_000_000)
    required = subtotal + safety_margin
    requirement = {
        "estimate_class": "uncalibrated_baseline_requires_partition_pilot",
        "estimated_final_dwd_bytes": estimated_dwd,
        "estimated_final_dws_bytes": estimated_dws,
        "financial_compressed_input_bytes": financial_compressed,
        "financial_uncompressed_payload_bytes": financial_uncompressed,
        "largest_modeled_dataset_bytes": largest_group,
        "largest_stage_or_spill_bytes": largest_stage,
        "modeled_input_bytes": modeled_input,
        "required_free_bytes": required,
        "safety_margin_bytes": safety_margin,
        "safety_margin_ppm": _MARGIN_PPM,
        "dws_to_dwd_ratio_ppm": _DWS_TO_DWD_PPM,
    }
    return {
        "schema_version": 1,
        "requirement": requirement,
        "requirement_digest": _canonical_digest(requirement),
        "environment": {
            "available_free_bytes": available_bytes,
            "capacity_sufficient": available_bytes >= required,
        },
    }


def _write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o640)
        json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _verify_existing_plan(output: Path, *, run_id: str, plan_digest: str) -> dict[str, Any]:
    if not output.is_dir():
        raise WarehousePlanningError(f"existing plan output is not a directory: {output}")
    manifest_path = output / "manifest.json"
    manifest = _object(manifest_path)
    ready_path = output / "READY"
    try:
        ready = ready_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise WarehousePlanningError(f"cannot read existing plan READY: {ready_path}") from exc
    if ready != _sha256(manifest_path):
        raise WarehousePlanningError("existing plan READY does not match manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dwd_dws_input_plan_package"
        or manifest.get("plan_digest") != plan_digest
    ):
        raise WarehousePlanningError("existing plan manifest differs from requested plan")
    expected_files = {
        "capacity-estimate.json",
        "coverage-matrix.json",
        "input-plan.json",
        "run-receipt.json",
        "source-disposition-ledger.json",
    }
    declared_files: set[str] = set()
    for raw in _sequence(manifest.get("files"), "manifest.files"):
        item = _mapping(raw, "manifest.files[]")
        relative = _safe_relative(item.get("path"), "manifest.files[].path")
        if "/" in relative or relative in declared_files:
            raise WarehousePlanningError("existing plan manifest has invalid file inventory")
        declared_files.add(relative)
        path = output / relative
        size = item.get("size_bytes")
        if not path.is_file() or path.stat().st_size != size or _sha256(path) != item.get("sha256"):
            raise WarehousePlanningError(f"existing plan file failed verification: {relative}")
    if declared_files != expected_files:
        raise WarehousePlanningError("existing plan manifest file inventory is incomplete")
    input_plan = _object(output / "input-plan.json")
    if input_plan.get("run_id") != run_id or input_plan.get("plan_digest") != plan_digest:
        raise WarehousePlanningError("existing output belongs to a different run or plan")
    return _object(output / "capacity-estimate.json")


def _publish_plan(
    output: Path,
    *,
    input_plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    coverage: Mapping[str, Any],
    capacity: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"plan output already exists: {output}")
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    staging = staging_parent / output.name
    staging.mkdir()
    try:
        values = {
            "input-plan.json": input_plan,
            "source-disposition-ledger.json": ledger,
            "coverage-matrix.json": coverage,
            "capacity-estimate.json": capacity,
            "run-receipt.json": receipt,
        }
        for name, value in values.items():
            _write_json_new(staging / name, value)
        files = [
            {
                "path": name,
                "sha256": _sha256(staging / name),
                "size_bytes": (staging / name).stat().st_size,
            }
            for name in sorted(values)
        ]
        manifest = {
            "schema_version": 1,
            "kind": "dwd_dws_input_plan_package",
            "plan_digest": input_plan["plan_digest"],
            "files": files,
        }
        _write_json_new(staging / "manifest.json", manifest)
        with (staging / "READY").open("x", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(f"{_sha256(staging / 'manifest.json')}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
    shutil.rmtree(staging_parent, ignore_errors=True)


def build_input_plan(
    *,
    ods_package: Path,
    model_path: Path,
    output: Path,
    mode: str,
    run_id: str,
    knowledge_cutoff: str,
    code_revision: str,
    runtime_id: str,
    market_date: str | None = None,
    through_date: str | None = None,
    capacity_root: Path | None = None,
    input_identity_override: Path | None = None,
) -> InputPlanResult:
    """Verify one immutable ODS package and publish its deterministic build plan."""

    started_at = datetime.now(timezone.utc)
    started_ns = time.perf_counter_ns()
    if mode not in _MODES:
        raise WarehousePlanningError(f"unsupported build mode: {mode}")
    if _SAFE_ID.fullmatch(run_id) is None or _SAFE_ID.fullmatch(runtime_id) is None:
        raise WarehousePlanningError("run_id and runtime_id must be safe identifiers")
    if _REVISION.fullmatch(code_revision) is None:
        raise WarehousePlanningError("code_revision must be a lowercase 40-character Git revision")
    cutoff = _utc_datetime(knowledge_cutoff, "knowledge_cutoff")
    selected_market_date = _date(market_date, "market_date") if market_date else None
    selected_through_date = _date(through_date, "through_date") if through_date else None
    model, model_sha = _model_contract(model_path.resolve())
    expected_input = _mapping(model.get("build_input_contract"), "build_input_contract")
    override_identity: dict[str, Any] | None = None
    if input_identity_override is not None:
        expected_input, override_identity = _input_identity_override(
            input_identity_override.resolve(),
            model_sha256=model_sha,
        )
        if cutoff < override_identity["minimum_knowledge_cutoff"]:
            raise WarehousePlanningError(
                "knowledge_cutoff precedes the frozen input identity authority"
            )
    package_root = ods_package.resolve()
    base_manifest_path = package_root / "manifest.json"
    release_manifest_path = package_root / "release.json"

    try:
        if base_manifest_path.is_file():
            if mode != "base":
                raise WarehousePlanningError(f"{mode} mode requires an ODS release, not a Base")
            verified = verify_ods_base_rollup(package_root, deep=True)
            manifest = _object(base_manifest_path)
            actual_identity = {
                "bundle_id": verified.bundle_id,
                "content_digest": verified.content_digest,
                "manifest_sha256": verified.manifest_sha256,
                "through_date": verified.through_date.isoformat(),
            }
            for field, actual in actual_identity.items():
                if expected_input.get(field) != actual:
                    raise WarehousePlanningError(
                        f"ODS Base {field} differs from the model contract"
                    )
            if selected_through_date and selected_through_date != actual_identity["through_date"]:
                raise WarehousePlanningError("requested through_date differs from the ODS Base")
            records = _base_records(manifest, model)
            input_identity = {
                **actual_identity,
                "file_count": verified.file_count,
                "kind": "ods_base_rollup",
                "verified_bytes": verified.size_bytes,
                "verification_mode": "deep",
            }
        elif release_manifest_path.is_file():
            if mode == "base":
                raise WarehousePlanningError("base mode requires an ODS Base rollup")
            records, input_identity = _release_records(package_root, model)
            if mode == "daily" and input_identity["schedule"] != "daily":
                raise WarehousePlanningError("daily mode requires a daily ODS release")
            if mode == "weekly" and input_identity["schedule"] != "weekly":
                raise WarehousePlanningError("weekly mode requires a weekly ODS release")
            if selected_market_date and selected_market_date != input_identity["market_date"]:
                raise WarehousePlanningError("requested market_date differs from the ODS release")
            late = [
                str(record["path"])
                for record in records
                if record.get("knowledge_cutoff") and str(record["knowledge_cutoff"]) > cutoff
            ]
            if late:
                raise WarehousePlanningError("ODS artifacts exceed the requested knowledge cutoff")
        else:
            raise WarehousePlanningError("ODS package has neither manifest.json nor release.json")
    except (OdsBaseRollupError, OdsReleaseError) as exc:
        raise WarehousePlanningError(str(exc)) from exc

    coverage, domain_plan = _coverage(records, model)
    if mode == "base":
        _validate_base_coverage(coverage, domain_plan)
    output_parent = output.resolve().parent
    output_parent.mkdir(parents=True, exist_ok=True)
    selected_capacity_root = (capacity_root or output_parent).resolve()
    if not selected_capacity_root.is_dir():
        raise WarehousePlanningError(
            f"capacity root must be an existing directory: {selected_capacity_root}"
        )
    available_bytes = shutil.disk_usage(selected_capacity_root).free
    capacity = _capacity(package_root, records, available_bytes=available_bytes)
    capacity["environment"]["capacity_root"] = str(selected_capacity_root)
    ledger = {
        "schema_version": 1,
        "input_id": input_identity["bundle_id"]
        if "bundle_id" in input_identity
        else input_identity["input_id"],
        "records": records,
        "summary": {
            "by_disposition": {
                disposition: sum(1 for record in records if record["disposition"] == disposition)
                for disposition in sorted({str(record["disposition"]) for record in records})
            },
            "container_member_count": sum(
                record["record_type"] == "container_member" for record in records
            ),
            "file_count": sum(record["record_type"] == "file" for record in records),
            "record_count": len(records),
        },
    }
    semantic_plan = {
        "schema_version": 1,
        "code_revision": code_revision,
        "runtime_id": runtime_id,
        "mode": mode,
        "knowledge_cutoff": cutoff,
        "market_date": selected_market_date or input_identity.get("market_date"),
        "through_date": selected_through_date or input_identity.get("through_date"),
        "model": {
            "contract_id": model["contract_id"],
            "sha256": model_sha,
        },
        "input": input_identity,
        "domain_plan": domain_plan,
        "source_precedence": {
            **_mapping(
                model["build_input_contract"]["overlay_precedence"],
                "build_input_contract.overlay_precedence",
            ),
            "calendar_history": _mapping(
                model["build_input_contract"]["calendar_history_precedence"],
                "build_input_contract.calendar_history_precedence",
            ),
        },
        "source_ledger_digest": _canonical_digest(ledger),
        "coverage_matrix_digest": _canonical_digest(coverage),
        "capacity_requirement_digest": capacity["requirement_digest"],
    }
    if override_identity is not None:
        semantic_plan["input_identity_override"] = override_identity
    plan_digest = _canonical_digest(semantic_plan)
    input_plan = {
        "schema_version": 1,
        "kind": "dwd_dws_input_plan",
        "run_id": run_id,
        "plan_digest": plan_digest,
        "plan": semantic_plan,
    }
    declared_row_counts = [
        int(record["row_count"])
        for record in records
        if isinstance(record.get("row_count"), int)
        and not isinstance(record.get("row_count"), bool)
    ]
    receipt = {
        "schema_version": 1,
        "kind": "dwd_dws_input_plan_receipt",
        "run_id": run_id,
        "status": "planned",
        "started_at": started_at.isoformat(),
        "planning_elapsed_ms": max(0, (time.perf_counter_ns() - started_ns) // 1_000_000),
        "input_manifest_sha256": input_identity["manifest_sha256"],
        "schema_digest": model_sha,
        "semantic_digest": plan_digest,
        "row_counts": {
            "source_records": len(records),
            "records_with_declared_rows": len(declared_row_counts),
            "declared_source_rows": sum(declared_row_counts),
        },
        "key_counts": {
            "status": "not_applicable",
            "reason": "planning_stage_does_not_decode_business_rows",
        },
        "resource_use": {
            "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "available_free_bytes": capacity["environment"]["available_free_bytes"],
            "required_free_bytes": capacity["requirement"]["required_free_bytes"],
        },
        "quality_exceptions": [],
    }
    result_capacity = capacity
    try:
        _publish_plan(
            output,
            input_plan=input_plan,
            ledger=ledger,
            coverage=coverage,
            capacity=capacity,
            receipt=receipt,
        )
    except FileExistsError:
        result_capacity = _verify_existing_plan(
            output.resolve(), run_id=run_id, plan_digest=plan_digest
        )
    summary = _mapping(ledger["summary"], "ledger.summary")
    requirement = _mapping(result_capacity["requirement"], "capacity.requirement")
    environment = _mapping(result_capacity["environment"], "capacity.environment")
    return InputPlanResult(
        root=output.resolve(),
        plan_digest=plan_digest,
        input_kind=str(input_identity["kind"]),
        input_id=str(input_identity.get("bundle_id", input_identity.get("input_id"))),
        input_manifest_sha256=str(input_identity["manifest_sha256"]),
        file_count=int(summary["file_count"]),
        source_record_count=len(records),
        required_free_bytes=int(requirement["required_free_bytes"]),
        available_free_bytes=int(environment["available_free_bytes"]),
        capacity_sufficient=bool(environment["capacity_sufficient"]),
    )
