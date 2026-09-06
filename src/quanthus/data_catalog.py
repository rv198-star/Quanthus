# ruff: noqa: E501
"""Deterministic read-only Data & Indicator Catalog projection and query service."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root

CATALOG_SCHEMA = "quanthus.data-indicator-catalog.v1"
BUILDER_VERSION = "data-indicator-catalog-builder-v1"
SOURCE_MANIFEST = "config/data-indicator-catalog-sources.v1.json"
SNAPSHOT_MANIFEST = "config/data-indicator-catalog-snapshot.v1.json"
ENTRY_KINDS = frozenset(
    {
        "ods_dataset",
        "dwd_table",
        "dwd_field",
        "feature_spec",
        "label_card",
        "semantic_family",
        "semantic_concept",
    }
)
LIFECYCLES = frozenset({"active", "reserved", "deprecated", "source_available", "source_gap"})
IMPLEMENTATION_STATUSES = frozenset({"implemented", "declared_only", "planned", "unknown"})
VERIFICATION_STATUSES = frozenset({"accepted", "registry_validated", "contract_only", "unverified"})
RELATION_TYPES = frozenset(
    {
        "belongs_to_family",
        "contains",
        "field_of",
        "references",
        "requires_feature",
        "related_not_equivalent",
        "exact_equivalent",
        "replacement",
    }
)
ENTRY_FIELDS = {
    "entry_id",
    "kind",
    "canonical_id",
    "display_name",
    "description",
    "domain",
    "family",
    "lifecycle",
    "implementation_status",
    "verification_status",
    "accepted_scope",
    "authority",
    "aliases",
    "keywords",
    "relations",
    "details",
}
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENTRY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$")


class DataCatalogError(ValueError):
    """Raised when a Catalog source, snapshot, query or relation violates the contract."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(value)).hexdigest()}"


def file_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataCatalogError(f"cannot read catalog source: {path}") from exc
    if not isinstance(value, dict):
        raise DataCatalogError(f"catalog source must be an object: {path}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataCatalogError(f"{name} must be nonempty text")
    return value


def _authority(path: str, pointer: str, source_sha: str) -> dict[str, str]:
    return {"path": path, "pointer": pointer, "sha256": source_sha}


def _display(identifier: str) -> str:
    return identifier.replace("_", " ").replace("-", " ")


def _entry(
    *,
    entry_id: str,
    kind: str,
    canonical_id: str,
    display_name: str,
    description: str,
    domain: str,
    family: str,
    lifecycle: str,
    implementation_status: str,
    verification_status: str,
    accepted_scope: str | None,
    authority: dict[str, str],
    aliases: Iterable[str] = (),
    keywords: Iterable[str] = (),
    relations: Iterable[dict[str, str]] = (),
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "entry_id": entry_id,
        "kind": kind,
        "canonical_id": canonical_id,
        "display_name": display_name,
        "description": description,
        "domain": domain,
        "family": family,
        "lifecycle": lifecycle,
        "implementation_status": implementation_status,
        "verification_status": verification_status,
        "accepted_scope": accepted_scope,
        "authority": authority,
        "aliases": sorted(set(aliases)),
        "keywords": sorted(set(keywords)),
        "relations": sorted(
            (dict(item) for item in relations),
            key=lambda item: (item["type"], item["target_entry_id"]),
        ),
        "details": dict(details or {}),
    }
    validate_entry(result)
    return result


def validate_entry(entry: Mapping[str, Any]) -> None:
    if set(entry) != ENTRY_FIELDS:
        raise DataCatalogError("catalog entry fields differ")
    if not isinstance(entry["entry_id"], str) or not _ENTRY_ID.fullmatch(entry["entry_id"]):
        raise DataCatalogError("catalog entry_id is invalid")
    for field in ("canonical_id", "display_name", "description", "domain", "family"):
        _text(entry[field], f"entry.{field}")
    if entry["kind"] not in ENTRY_KINDS:
        raise DataCatalogError("catalog entry kind is invalid")
    if entry["lifecycle"] not in LIFECYCLES:
        raise DataCatalogError("catalog lifecycle is invalid")
    if entry["implementation_status"] not in IMPLEMENTATION_STATUSES:
        raise DataCatalogError("catalog implementation status is invalid")
    if entry["verification_status"] not in VERIFICATION_STATUSES:
        raise DataCatalogError("catalog verification status is invalid")
    if entry["accepted_scope"] is not None:
        _text(entry["accepted_scope"], "entry.accepted_scope")
    authority = entry["authority"]
    if not isinstance(authority, Mapping) or set(authority) != {"path", "pointer", "sha256"}:
        raise DataCatalogError("catalog authority is invalid")
    _text(authority["path"], "authority.path")
    _text(authority["pointer"], "authority.pointer")
    if not isinstance(authority["sha256"], str) or not _SHA256.fullmatch(authority["sha256"]):
        raise DataCatalogError("catalog authority sha256 is invalid")
    for field in ("aliases", "keywords"):
        value = entry[field]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise DataCatalogError(f"catalog {field} must be text arrays")
        if value != sorted(set(value)):
            raise DataCatalogError(f"catalog {field} must be sorted unique")
    relations = entry["relations"]
    if not isinstance(relations, list):
        raise DataCatalogError("catalog relations must be an array")
    seen: set[tuple[str, str]] = set()
    for relation in relations:
        if not isinstance(relation, Mapping) or set(relation) != {"type", "target_entry_id"}:
            raise DataCatalogError("catalog relation fields differ")
        if relation["type"] not in RELATION_TYPES:
            raise DataCatalogError("catalog relation type is invalid")
        if not isinstance(relation["target_entry_id"], str) or not _ENTRY_ID.fullmatch(
            relation["target_entry_id"]
        ):
            raise DataCatalogError("catalog relation target is invalid")
        key = (str(relation["type"]), str(relation["target_entry_id"]))
        if key in seen:
            raise DataCatalogError("duplicate catalog relation")
        seen.add(key)
    if not isinstance(entry["details"], Mapping):
        raise DataCatalogError("catalog details must be an object")


def _load_sources(root: Path, manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, dict[str, Any]]]:
    manifest = _object(manifest_path)
    if set(manifest) != {"schema_version", "catalog_id", "sources"} or manifest["schema_version"] != "1.0.0":
        raise DataCatalogError("catalog source manifest schema differs")
    values = manifest["sources"]
    if not isinstance(values, list) or not values:
        raise DataCatalogError("catalog source manifest is empty")
    allowed = {
        "release_identity",
        "ods_contract",
        "dwd_domain_model",
        "dwd_extension",
        "feature_registry",
        "feature_registry_extension",
        "label_registry",
        "semantic_catalog",
        "structure_blueprint",
        "catalog_annotations",
    }
    records: list[dict[str, str]] = []
    payloads: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, Mapping) or set(raw) != {"adapter", "path"}:
            raise DataCatalogError("catalog source record fields differ")
        adapter = _text(raw["adapter"], "source.adapter")
        relative = _text(raw["path"], "source.path")
        if adapter not in allowed or adapter in seen:
            raise DataCatalogError("catalog source adapter is invalid or duplicated")
        path = (root / relative).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise DataCatalogError(f"catalog source path is unavailable: {relative}")
        source_sha = file_sha256(path)
        records.append({"adapter": adapter, "path": relative, "sha256": source_sha})
        payloads[adapter] = _object(path)
        seen.add(adapter)
    required = allowed
    if seen != required:
        raise DataCatalogError(f"catalog source adapters differ: missing={sorted(required - seen)}")
    return manifest, sorted(records, key=lambda item: item["adapter"]), payloads


def _walk_dataset_records(value: object, pointer: str = "") -> list[tuple[str, dict[str, Any]]]:
    output: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        if isinstance(value.get("dataset_id"), str):
            output.append((pointer or "/", value))
        for key, child in value.items():
            output.extend(_walk_dataset_records(child, f"{pointer}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            output.extend(_walk_dataset_records(child, f"{pointer}/{index}"))
    return output


def _source_sha(sources: list[dict[str, str]], adapter: str) -> str:
    return next(item["sha256"] for item in sources if item["adapter"] == adapter)


def _source_path(sources: list[dict[str, str]], adapter: str) -> str:
    return next(item["path"] for item in sources if item["adapter"] == adapter)


def build_catalog(source_manifest_path: Path | None = None) -> dict[str, Any]:
    root = find_project_root(source_manifest_path)
    manifest_path = (source_manifest_path or root / SOURCE_MANIFEST).resolve()
    manifest, sources, payloads = _load_sources(root, manifest_path)
    entries: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []

    def add(item: dict[str, Any]) -> None:
        key = item["entry_id"]
        if key in entries:
            raise DataCatalogError(f"duplicate catalog entry: {key}")
        entries[key] = item

    release = payloads["release_identity"]
    layers = release.get("layers")
    if not isinstance(layers, Mapping):
        raise DataCatalogError("release identity layers are invalid")
    ods_release = layers.get("ods")
    dwd_release = layers.get("dwd")
    if not isinstance(ods_release, Mapping) or not isinstance(dwd_release, Mapping):
        raise DataCatalogError("release identity must bind ODS and DWD")
    release_identity = {
        "release_id": _text(release.get("release_id"), "release.release_id"),
        "through_date": _text(release.get("through_date"), "release.through_date"),
        "ods_package_id": _text(ods_release.get("package_id"), "release.ods.package_id"),
        "ods_manifest_sha256": _text(ods_release.get("manifest_sha256"), "release.ods.manifest_sha256"),
        "dwd_package_id": _text(dwd_release.get("package_id"), "release.dwd.package_id"),
        "dwd_manifest_sha256": _text(dwd_release.get("manifest_sha256"), "release.dwd.manifest_sha256"),
    }

    # ODS: dataset-level only. The accepted package manifest is external to this checkout, so field
    # schemas are deliberately not synthesized from prose or physical files.
    ods_path = _source_path(sources, "ods_contract")
    ods_sha = _source_sha(sources, "ods_contract")
    dataset_records = _walk_dataset_records(payloads["ods_contract"])
    seen_datasets: set[str] = set()
    for pointer, raw in dataset_records:
        dataset_id = _text(raw.get("dataset_id"), "ods.dataset_id")
        if dataset_id in seen_datasets:
            raise DataCatalogError(f"duplicate ODS dataset declaration: {dataset_id}")
        seen_datasets.add(dataset_id)
        role = str(raw.get("role") or "unclassified")
        format_name = str(raw.get("format") or ("tree" if "pattern" in raw else "archive_tree"))
        add(
            _entry(
                entry_id=f"ods_dataset:{dataset_id}",
                kind="ods_dataset",
                canonical_id=dataset_id,
                display_name=dataset_id,
                description=f"ODS dataset declared by the authoritative Base source contract; role={role}.",
                domain="ods",
                family=role,
                lifecycle="source_available",
                implementation_status="implemented",
                verification_status="contract_only",
                accepted_scope=None,
                authority=_authority(ods_path, pointer, ods_sha),
                keywords=(role, format_name, *(raw.get("key_columns") or []), *(raw.get("date_columns") or [])),
                details={
                    "role": role,
                    "format": format_name,
                    "source": raw.get("source"),
                    "target": raw.get("target"),
                    "key_columns": list(raw.get("key_columns") or []),
                    "date_columns": list(raw.get("date_columns") or []),
                    "package_membership_proven_in_checkout": False,
                },
            )
        )
    gaps.append(
        {
            "gap_id": "gap:ods-package-manifest-not-in-checkout",
            "category": "source_manifest_unavailable",
            "description": "The accepted ODS package manifest is checksum-bound by the release identity but is not a repository source, so v1 does not claim full package membership or field schemas for ODS datasets.",
            "affected_entry_ids": sorted(f"ods_dataset:{item}" for item in seen_datasets),
            "authority_refs": [ods_path, _source_path(sources, "release_identity")],
        }
    )

    # DWD: merge approved v1.1 extensions into the base machine model before projection.
    dwd = payloads["dwd_domain_model"]
    extension = payloads["dwd_extension"]
    tables = dwd.get("tables")
    column_sets = dwd.get("column_sets")
    time_profiles = dwd.get("time_profiles")
    domains = dwd.get("domain_catalog")
    extensions = extension.get("table_extensions")
    if not all(isinstance(value, (list, dict)) for value in (tables, column_sets, time_profiles, domains, extensions)):
        raise DataCatalogError("DWD machine model sections are invalid")
    domain_map = {item["id"]: item for item in domains if isinstance(item, Mapping) and isinstance(item.get("id"), str)}
    dwd_path = _source_path(sources, "dwd_domain_model")
    dwd_sha = _source_sha(sources, "dwd_domain_model")
    extension_path = _source_path(sources, "dwd_extension")
    extension_sha = _source_sha(sources, "dwd_extension")
    for index, raw in enumerate(tables):
        if not isinstance(raw, Mapping):
            raise DataCatalogError("DWD table entry must be an object")
        name = _text(raw.get("name"), "dwd.table.name")
        table_domain = _text(raw.get("domain"), "dwd.table.domain")
        time_profile = _text(raw.get("time_profile"), "dwd.table.time_profile")
        columns: dict[str, str] = {}
        field_authority: dict[str, tuple[str, str, str]] = {}
        for inherited in raw.get("inherits", []):
            inherited_columns = column_sets.get(inherited)
            if not isinstance(inherited_columns, Mapping):
                raise DataCatalogError(f"unknown DWD inherited column set: {inherited}")
            for field, type_name in inherited_columns.items():
                columns[str(field)] = str(type_name)
                field_authority[str(field)] = (dwd_path, f"/column_sets/{inherited}/{field}", dwd_sha)
        profile_columns = time_profiles.get(time_profile)
        if not isinstance(profile_columns, Mapping):
            raise DataCatalogError(f"unknown DWD time profile: {time_profile}")
        for field, type_name in profile_columns.items():
            columns[str(field)] = str(type_name)
            field_authority[str(field)] = (dwd_path, f"/time_profiles/{time_profile}/{field}", dwd_sha)
        own_columns = raw.get("columns")
        if not isinstance(own_columns, Mapping):
            raise DataCatalogError("DWD table columns are invalid")
        for field, type_name in own_columns.items():
            if str(field) in columns:
                raise DataCatalogError(f"DWD field collision: {name}.{field}")
            columns[str(field)] = str(type_name)
            field_authority[str(field)] = (dwd_path, f"/tables/{index}/columns/{field}", dwd_sha)
        extension_raw = extensions.get(name, {})
        if not isinstance(extension_raw, Mapping):
            raise DataCatalogError("DWD extension entry is invalid")
        extension_columns = extension_raw.get("columns", {})
        if not isinstance(extension_columns, Mapping):
            raise DataCatalogError("DWD extension columns are invalid")
        for field, type_name in extension_columns.items():
            if str(field) in columns:
                raise DataCatalogError(f"DWD extension field collision: {name}.{field}")
            columns[str(field)] = str(type_name)
            field_authority[str(field)] = (
                extension_path,
                f"/table_extensions/{name}/columns/{field}",
                extension_sha,
            )
        business_key = list(raw.get("business_key") or []) + list(extension_raw.get("business_key_append") or [])
        table_id = f"dwd_table:{name}"
        field_relations = [
            {"type": "contains", "target_entry_id": f"dwd_field:{name}.{field}"}
            for field in sorted(columns)
        ]
        foreign_relations = [
            {"type": "references", "target_entry_id": f"dwd_table:{foreign['table']}"}
            for foreign in raw.get("foreign_keys", [])
            if isinstance(foreign, Mapping) and isinstance(foreign.get("table"), str)
        ]
        domain_info = domain_map.get(table_domain, {})
        add(
            _entry(
                entry_id=table_id,
                kind="dwd_table",
                canonical_id=name,
                display_name=name,
                description=str(raw.get("grain") or domain_info.get("question") or name),
                domain=f"dwd.{table_domain}",
                family="dwd.table",
                lifecycle="source_gap" if raw.get("delivery_state") == "source_gap" else "source_available",
                implementation_status="implemented",
                verification_status="accepted",
                accepted_scope=release_identity["dwd_package_id"],
                authority=_authority(dwd_path, f"/tables/{index}", dwd_sha),
                keywords=(table_domain, time_profile, str(raw.get("kind") or ""), str(raw.get("object_role") or "")),
                relations=(*field_relations, *foreign_relations),
                details={
                    "grain": raw.get("grain"),
                    "kind": raw.get("kind"),
                    "object_role": raw.get("object_role"),
                    "delivery_state": raw.get("delivery_state"),
                    "time_profile": time_profile,
                    "business_key": business_key,
                    "row_key": list(raw.get("row_key") or []),
                    "partition_by": list(raw.get("partition_by") or []),
                    "foreign_keys": list(raw.get("foreign_keys") or []),
                    "extension_authority": extension_path if extension_raw else None,
                },
            )
        )
        for field in sorted(columns):
            authority_path, pointer, authority_sha = field_authority[field]
            type_name = columns[field]
            add(
                _entry(
                    entry_id=f"dwd_field:{name}.{field}",
                    kind="dwd_field",
                    canonical_id=f"{name}.{field}",
                    display_name=f"{name}.{field}",
                    description=f"Field `{field}` of `{name}` under the {time_profile} DWD profile.",
                    domain=f"dwd.{table_domain}",
                    family="dwd.field",
                    lifecycle="source_gap" if raw.get("delivery_state") == "source_gap" else "source_available",
                    implementation_status="implemented",
                    verification_status="accepted",
                    accepted_scope=release_identity["dwd_package_id"],
                    authority=_authority(authority_path, pointer, authority_sha),
                    keywords=(field, type_name, table_domain, time_profile),
                    relations=({"type": "field_of", "target_entry_id": table_id},),
                    details={
                        "table": name,
                        "field": field,
                        "type": type_name,
                        "nullable": type_name.endswith("?"),
                        "time_profile": time_profile,
                        "in_business_key": field in business_key,
                        "in_row_key": field in list(raw.get("row_key") or []),
                    },
                )
            )

    # Semantic vocabulary.
    semantic = payloads["semantic_catalog"]
    semantic_path = _source_path(sources, "semantic_catalog")
    semantic_sha = _source_sha(sources, "semantic_catalog")
    families = semantic.get("families")
    concepts = semantic.get("concepts")
    if not isinstance(families, list) or not isinstance(concepts, list):
        raise DataCatalogError("semantic catalog sections are invalid")
    family_prefixes: set[str] = set()
    for index, raw in enumerate(families):
        if not isinstance(raw, Mapping):
            raise DataCatalogError("semantic family is invalid")
        prefix = _text(raw.get("prefix"), "semantic.family.prefix")
        family_prefixes.add(prefix)
        add(
            _entry(
                entry_id=f"semantic_family:{prefix}",
                kind="semantic_family",
                canonical_id=prefix,
                display_name=prefix,
                description=_text(raw.get("meaning"), "semantic.family.meaning"),
                domain=prefix.split(".", 1)[0],
                family=prefix,
                lifecycle="active",
                implementation_status="declared_only",
                verification_status="contract_only",
                accepted_scope="naming_and_boundary_contract_only",
                authority=_authority(semantic_path, f"/families/{index}", semantic_sha),
                keywords=prefix.split("."),
                details={"boundary": raw.get("boundary")},
            )
        )
    for index, raw in enumerate(concepts):
        if not isinstance(raw, Mapping):
            raise DataCatalogError("semantic concept is invalid")
        concept_id = _text(raw.get("concept_id"), "semantic.concept_id")
        matches = sorted(prefix for prefix in family_prefixes if concept_id.startswith(prefix + "."))
        if len(matches) != 1:
            raise DataCatalogError(f"semantic concept family is ambiguous: {concept_id}")
        prefix = matches[0]
        registration = str(raw.get("registration") or "reserved")
        add(
            _entry(
                entry_id=f"semantic_concept:{concept_id}",
                kind="semantic_concept",
                canonical_id=concept_id,
                display_name=concept_id,
                description=_text(raw.get("meaning"), "semantic.concept.meaning"),
                domain=concept_id.split(".", 1)[0],
                family=prefix,
                lifecycle="reserved" if registration == "reserved" else "active",
                implementation_status="declared_only",
                verification_status="contract_only",
                accepted_scope="semantic_registration_only",
                authority=_authority(semantic_path, f"/concepts/{index}", semantic_sha),
                keywords=concept_id.replace("-", ".").split("."),
                relations=({"type": "belongs_to_family", "target_entry_id": f"semantic_family:{prefix}"},),
                details={"result_kind": raw.get("result_kind"), "registration": registration},
            )
        )

    # Features: project exact registered formulas, never re-implement them here.
    feature_paths = {
        "feature_registry": _source_path(sources, "feature_registry"),
        "feature_registry_extension": _source_path(sources, "feature_registry_extension"),
    }
    feature_shas = {key: _source_sha(sources, key) for key in feature_paths}
    for adapter in ("feature_registry", "feature_registry_extension"):
        registry = payloads[adapter]
        definitions = registry.get("definitions")
        if not isinstance(definitions, list):
            raise DataCatalogError("feature registry definitions are invalid")
        for index, raw in enumerate(definitions):
            if not isinstance(raw, Mapping):
                raise DataCatalogError("feature definition is invalid")
            feature_id = _text(raw.get("feature_id"), "feature.feature_id")
            version = _text(raw.get("version"), "feature.version")
            canonical_id = f"{feature_id}@{version}"
            lifecycle = str(raw.get("lifecycle"))
            lifecycle_value = lifecycle if lifecycle in {"active", "deprecated"} else "active"
            add(
                _entry(
                    entry_id=f"feature_spec:{canonical_id}",
                    kind="feature_spec",
                    canonical_id=canonical_id,
                    display_name=canonical_id,
                    description=_text(raw.get("formula"), "feature.formula"),
                    domain="feature",
                    family=feature_id.split(".", 1)[0],
                    lifecycle=lifecycle_value,
                    implementation_status="implemented",
                    verification_status="registry_validated",
                    accepted_scope=str(registry.get("registry_version") or "feature_registry"),
                    authority=_authority(feature_paths[adapter], f"/definitions/{index}", feature_shas[adapter]),
                    aliases=(feature_id,),
                    keywords=(feature_id, str(raw.get("unit") or ""), str(raw.get("output_type") or "")),
                    details={
                        "formula": raw.get("formula"),
                        "inputs": list(raw.get("inputs") or []),
                        "unit": raw.get("unit"),
                        "null_policy": raw.get("null_policy"),
                        "lookback_sessions": raw.get("lookback_sessions"),
                        "minimum_history_sessions": raw.get("minimum_history_sessions"),
                        "cutoff_rule": raw.get("cutoff_rule"),
                        "output_type": raw.get("output_type"),
                        "owner": raw.get("owner"),
                        "registry_version": registry.get("registry_version"),
                    },
                )
            )

    # Legacy relationships are discovery-only. Only create a relation when both sides exist.
    unresolved_legacy: list[str] = []
    for raw in semantic.get("legacy_links", []):
        if not isinstance(raw, Mapping):
            raise DataCatalogError("semantic legacy link is invalid")
        legacy_id = _text(raw.get("legacy_id"), "legacy.legacy_id")
        legacy_version = _text(raw.get("legacy_version"), "legacy.legacy_version")
        target_concept = _text(raw.get("related_concept"), "legacy.related_concept")
        relationship = str(raw.get("relationship"))
        if relationship != "related_not_equivalent":
            raise DataCatalogError("v1 legacy link cannot silently grant executable equivalence")
        source_id = f"feature_spec:{legacy_id}@{legacy_version}"
        target_id = f"semantic_concept:{target_concept}"
        if source_id in entries and target_id in entries:
            relations = list(entries[source_id]["relations"])
            relations.append({"type": "related_not_equivalent", "target_entry_id": target_id})
            entries[source_id] = {**entries[source_id], "relations": sorted(relations, key=lambda item: (item["type"], item["target_entry_id"]))}
            validate_entry(entries[source_id])
        else:
            unresolved_legacy.append(f"{legacy_id}@{legacy_version}->{target_concept}")
    if unresolved_legacy:
        gaps.append(
            {
                "gap_id": "gap:legacy-link-source-not-catalogued",
                "category": "unresolved_relation_source",
                "description": "Some legacy semantic links refer to identities outside the v1 executable Feature catalogs; they remain discovery notes and are not fabricated as aliases.",
                "affected_entry_ids": [],
                "authority_refs": [semantic_path, *sorted(unresolved_legacy)],
            }
        )

    # Labels and exact feature requirements.
    label_registry = payloads["label_registry"]
    label_path = _source_path(sources, "label_registry")
    label_sha = _source_sha(sources, "label_registry")
    cards = label_registry.get("cards")
    if not isinstance(cards, list):
        raise DataCatalogError("label registry cards are invalid")
    unresolved_requirements: set[str] = set()
    for index, raw in enumerate(cards):
        if not isinstance(raw, Mapping):
            raise DataCatalogError("label card is invalid")
        label_id = _text(raw.get("label_id"), "label.label_id")
        version = _text(raw.get("version"), "label.version")
        canonical_id = f"{label_id}@{version}"
        relations: list[dict[str, str]] = []
        for requirement in raw.get("feature_requirements", []):
            if not isinstance(requirement, Mapping):
                raise DataCatalogError("label feature requirement is invalid")
            target = f"feature_spec:{requirement.get('feature_id')}@{requirement.get('version')}"
            if target in entries:
                relations.append({"type": "requires_feature", "target_entry_id": target})
            else:
                unresolved_requirements.add(target)
        add(
            _entry(
                entry_id=f"label_card:{canonical_id}",
                kind="label_card",
                canonical_id=canonical_id,
                display_name=str(raw.get("display_name") or canonical_id),
                description=_text(raw.get("rule"), "label.rule"),
                domain="label",
                family=str(raw.get("dimension") or "unspecified"),
                lifecycle="active" if raw.get("lifecycle") == "active" else "deprecated",
                implementation_status="implemented",
                verification_status="registry_validated",
                accepted_scope=str(label_registry.get("registry_version") or "label_registry"),
                authority=_authority(label_path, f"/cards/{index}", label_sha),
                aliases=(label_id,),
                keywords=(label_id, str(raw.get("dimension") or "")),
                relations=relations,
                details={
                    "dimension": raw.get("dimension"),
                    "rule": raw.get("rule"),
                    "parameters": raw.get("parameters"),
                    "lookback_sessions": raw.get("lookback_sessions"),
                    "minimum_history_sessions": raw.get("minimum_history_sessions"),
                    "source_fields": list(raw.get("source_fields") or []),
                    "feature_requirements": list(raw.get("feature_requirements") or []),
                    "output_values": list(raw.get("output_values") or []),
                    "reason_codes": list(raw.get("reason_codes") or []),
                },
            )
        )
    if unresolved_requirements:
        gaps.append(
            {
                "gap_id": "gap:label-feature-requirements-not-catalogued",
                "category": "unresolved_relation_target",
                "description": "Label requirements point to Feature specs not present in the explicit v1 Feature source manifest.",
                "affected_entry_ids": [],
                "authority_refs": sorted(unresolved_requirements),
            }
        )

    # Reserved indicator concepts without an executable feature spec stay visibly incomplete.
    related_targets = {
        relation["target_entry_id"]
        for item in entries.values()
        for relation in item["relations"]
        if relation["type"] in {"related_not_equivalent", "exact_equivalent", "replacement"}
    }
    for item in sorted(entries.values(), key=lambda value: value["entry_id"]):
        if (
            item["kind"] == "semantic_concept"
            and item["canonical_id"].startswith("indicator.")
            and item["entry_id"] not in related_targets
        ):
            gaps.append(
                {
                    "gap_id": f"gap:no-executable-spec:{item['canonical_id']}",
                    "category": "executable_spec_missing",
                    "description": "Semantic indicator concept is registered for discovery but no executable Feature spec is linked in the v1 Catalog sources.",
                    "affected_entry_ids": [item["entry_id"]],
                    "authority_refs": [semantic_path],
                }
            )

    # Feature input datasets outside the catalog are explicit gaps rather than guessed DWD/ODS links.
    catalog_dataset_ids = {
        item["canonical_id"]
        for item in entries.values()
        if item["kind"] in {"ods_dataset", "dwd_table"}
    }
    unresolved_inputs = sorted(
        {
            str(source.get("dataset"))
            for item in entries.values()
            if item["kind"] == "feature_spec"
            for source in item["details"].get("inputs", [])
            if isinstance(source, Mapping)
            and isinstance(source.get("dataset"), str)
            and source.get("dataset") not in catalog_dataset_ids
        }
    )
    if unresolved_inputs:
        gaps.append(
            {
                "gap_id": "gap:feature-input-projection-datasets",
                "category": "projection_dataset_not_catalogued",
                "description": "Some registered research Features depend on replay/projection datasets that are not ODS/DWD canonical table identities in this v1 Catalog; their source strings remain visible in the Feature details without guessed equivalence.",
                "affected_entry_ids": [],
                "authority_refs": unresolved_inputs,
            }
        )

    # Catalog-only search/caution annotations. These cannot create entries or relations.
    annotations = payloads["catalog_annotations"]
    if annotations.get("authority") != "catalog_search_and_caution_annotations_only":
        raise DataCatalogError("catalog annotation authority differs")
    for raw in annotations.get("annotations", []):
        if not isinstance(raw, Mapping) or set(raw) != {"entry_id", "display_name", "search_terms", "cautions"}:
            raise DataCatalogError("catalog annotation fields differ")
        entry_id = _text(raw.get("entry_id"), "annotation.entry_id")
        if entry_id not in entries:
            raise DataCatalogError(f"catalog annotation target is missing: {entry_id}")
        search_terms = raw.get("search_terms")
        cautions = raw.get("cautions")
        if not isinstance(search_terms, list) or not isinstance(cautions, list) or any(
            not isinstance(item, str) or not item.strip() for item in [*search_terms, *cautions]
        ):
            raise DataCatalogError("catalog annotation text arrays are invalid")
        item = entries[entry_id]
        item = {
            **item,
            "display_name": _text(raw.get("display_name"), "annotation.display_name"),
            "keywords": sorted(set([*item["keywords"], *search_terms])),
            "details": {**item["details"], "cautions": cautions},
        }
        validate_entry(item)
        entries[entry_id] = item

    ordered_entries = sorted(entries.values(), key=lambda item: item["entry_id"])
    entry_ids = {item["entry_id"] for item in ordered_entries}
    for item in ordered_entries:
        for relation in item["relations"]:
            if relation["target_entry_id"] not in entry_ids:
                raise DataCatalogError(
                    f"dangling catalog relation: {item['entry_id']} -> {relation['target_entry_id']}"
                )
    canonical_kind_keys = [(item["kind"], item["canonical_id"]) for item in ordered_entries]
    if len(canonical_kind_keys) != len(set(canonical_kind_keys)):
        raise DataCatalogError("duplicate canonical identity within a Catalog kind")

    ordered_gaps = sorted(gaps, key=lambda item: item["gap_id"])
    if len({item["gap_id"] for item in ordered_gaps}) != len(ordered_gaps):
        raise DataCatalogError("duplicate catalog gap id")
    counts = Counter(item["kind"] for item in ordered_entries)
    source_manifest_sha = file_sha256(manifest_path)
    snapshot: dict[str, Any] = {
        "schema_version": CATALOG_SCHEMA,
        "catalog_id": _text(manifest.get("catalog_id"), "catalog_id"),
        "builder_version": BUILDER_VERSION,
        "source_manifest": {
            "path": str(manifest_path.relative_to(root)),
            "sha256": source_manifest_sha,
        },
        "release_identity": release_identity,
        "sources": sources,
        "summary": {
            "entry_count": len(ordered_entries),
            "gap_count": len(ordered_gaps),
            "counts_by_kind": dict(sorted(counts.items())),
        },
        "entries": ordered_entries,
        "gaps": ordered_gaps,
    }
    snapshot["snapshot_digest"] = digest(snapshot)
    validate_catalog(snapshot)
    return snapshot


def validate_catalog(snapshot: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "catalog_id",
        "builder_version",
        "source_manifest",
        "release_identity",
        "sources",
        "summary",
        "entries",
        "gaps",
        "snapshot_digest",
    }
    if set(snapshot) != expected or snapshot["schema_version"] != CATALOG_SCHEMA:
        raise DataCatalogError("catalog snapshot schema differs")
    if snapshot["builder_version"] != BUILDER_VERSION:
        raise DataCatalogError("catalog builder version differs")
    stored_digest = snapshot["snapshot_digest"]
    if not isinstance(stored_digest, str) or not _SHA256.fullmatch(stored_digest):
        raise DataCatalogError("catalog snapshot digest is invalid")
    without_digest = {key: value for key, value in snapshot.items() if key != "snapshot_digest"}
    if digest(without_digest) != stored_digest:
        raise DataCatalogError("catalog snapshot digest differs")
    entries = snapshot["entries"]
    if not isinstance(entries, list):
        raise DataCatalogError("catalog entries must be an array")
    entry_ids: set[str] = set()
    canonical_kind: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise DataCatalogError("catalog entry must be an object")
        validate_entry(entry)
        entry_id = str(entry["entry_id"])
        key = (str(entry["kind"]), str(entry["canonical_id"]))
        if entry_id in entry_ids or key in canonical_kind:
            raise DataCatalogError("catalog contains duplicate identity")
        entry_ids.add(entry_id)
        canonical_kind.add(key)
    for entry in entries:
        for relation in entry["relations"]:
            if relation["target_entry_id"] not in entry_ids:
                raise DataCatalogError("catalog contains dangling relation")
    summary = snapshot["summary"]
    if not isinstance(summary, Mapping):
        raise DataCatalogError("catalog summary is invalid")
    if summary.get("entry_count") != len(entries):
        raise DataCatalogError("catalog summary entry count differs")
    if summary.get("gap_count") != len(snapshot["gaps"]):
        raise DataCatalogError("catalog summary gap count differs")
    observed_counts = dict(sorted(Counter(item["kind"] for item in entries).items()))
    if summary.get("counts_by_kind") != observed_counts:
        raise DataCatalogError("catalog summary kind counts differ")


def catalog_bytes(source_manifest_path: Path | None = None) -> bytes:
    return canonical_bytes(build_catalog(source_manifest_path)) + b"\n"


class DataIndicatorCatalog:
    """Validated in-memory query view over one deterministic authority projection."""

    def __init__(
        self,
        source_manifest_path: Path | None = None,
        snapshot_manifest_path: Path | None = None,
    ) -> None:
        root = find_project_root(source_manifest_path or snapshot_manifest_path)
        source_path = (source_manifest_path or root / SOURCE_MANIFEST).resolve()
        manifest_path = (snapshot_manifest_path or root / SNAPSHOT_MANIFEST).resolve()
        snapshot = build_catalog(source_path)
        expected = _object(manifest_path)
        expected_fields = {
            "schema_version",
            "catalog_schema_version",
            "builder_version",
            "source_manifest_sha256",
            "snapshot_digest",
            "entry_count",
            "gap_count",
            "counts_by_kind",
        }
        if set(expected) != expected_fields or expected.get("schema_version") != (
            "quanthus.data-indicator-catalog-snapshot-manifest.v1"
        ):
            raise DataCatalogError("catalog snapshot manifest schema differs")
        if (
            expected.get("catalog_schema_version") != CATALOG_SCHEMA
            or expected.get("builder_version") != BUILDER_VERSION
            or expected.get("source_manifest_sha256") != snapshot["source_manifest"]["sha256"]
            or expected.get("snapshot_digest") != snapshot["snapshot_digest"]
            or expected.get("entry_count") != snapshot["summary"]["entry_count"]
            or expected.get("gap_count") != snapshot["summary"]["gap_count"]
            or expected.get("counts_by_kind") != snapshot["summary"]["counts_by_kind"]
        ):
            raise DataCatalogError("catalog snapshot manifest differs from authoritative projection")
        self.path = manifest_path
        self.source_manifest_path = source_path
        self.snapshot = snapshot
        self._entries = {item["entry_id"]: item for item in snapshot["entries"]}
        reverse: dict[str, list[dict[str, str]]] = defaultdict(list)
        for item in snapshot["entries"]:
            for relation in item["relations"]:
                reverse[relation["target_entry_id"]].append(
                    {"type": relation["type"], "source_entry_id": item["entry_id"]}
                )
        self._reverse = {
            key: sorted(value, key=lambda item: (item["type"], item["source_entry_id"]))
            for key, value in reverse.items()
        }

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": CATALOG_SCHEMA,
            "catalog_id": self.snapshot["catalog_id"],
            "builder_version": self.snapshot["builder_version"],
            "snapshot_digest": self.snapshot["snapshot_digest"],
            "release_identity": self.snapshot["release_identity"],
            "summary": self.snapshot["summary"],
            "gaps": self.snapshot["gaps"],
        }

    def item(self, entry_id: str) -> dict[str, Any]:
        if not isinstance(entry_id, str) or entry_id not in self._entries:
            raise DataCatalogError("catalog_entry_not_found")
        return dict(self._entries[entry_id])

    def relations(self, entry_id: str) -> dict[str, Any]:
        item = self.item(entry_id)
        outgoing = [
            {
                **relation,
                "target": self._summary_entry(self._entries[relation["target_entry_id"]]),
            }
            for relation in item["relations"]
        ]
        incoming = [
            {
                **relation,
                "source": self._summary_entry(self._entries[relation["source_entry_id"]]),
            }
            for relation in self._reverse.get(entry_id, [])
        ]
        return {"entry_id": entry_id, "outgoing": outgoing, "incoming": incoming}

    @staticmethod
    def _summary_entry(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: item[key]
            for key in (
                "entry_id",
                "kind",
                "canonical_id",
                "display_name",
                "domain",
                "family",
                "lifecycle",
                "implementation_status",
                "verification_status",
                "accepted_scope",
            )
        }

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        domain: str | None = None,
        family: str | None = None,
        status: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or len(query) > 120:
            raise DataCatalogError("catalog_query_invalid")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataCatalogError("catalog_limit_invalid")
        if kind is not None and kind not in ENTRY_KINDS:
            raise DataCatalogError("catalog_kind_invalid")
        filters = {"domain": domain, "family": family}
        for name, value in filters.items():
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 128):
                raise DataCatalogError(f"catalog_{name}_invalid")
        if status is not None and status not in LIFECYCLES | IMPLEMENTATION_STATUSES | VERIFICATION_STATUSES:
            raise DataCatalogError("catalog_status_invalid")
        needle = query.strip().casefold()
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for item in self.snapshot["entries"]:
            if kind is not None and item["kind"] != kind:
                continue
            if domain is not None and item["domain"] != domain:
                continue
            if family is not None and item["family"] != family:
                continue
            if status is not None and status not in {
                item["lifecycle"], item["implementation_status"], item["verification_status"]
            }:
                continue
            score = self._score(item, needle)
            if needle and score <= 0:
                continue
            ranked.append((score, item["entry_id"], self._summary_entry(item)))
        ranked.sort(key=lambda value: (-value[0], value[1]))
        return {
            "query": query,
            "filters": {"kind": kind, "domain": domain, "family": family, "status": status},
            "total": len(ranked),
            "limit": limit,
            "items": [item for _score, _key, item in ranked[:limit]],
        }

    @staticmethod
    def _score(item: Mapping[str, Any], needle: str) -> int:
        if not needle:
            return 1
        canonical = str(item["canonical_id"]).casefold()
        entry_id = str(item["entry_id"]).casefold()
        aliases = [str(value).casefold() for value in item["aliases"]]
        if needle in {canonical, entry_id, *aliases}:
            return 100
        if canonical.startswith(needle) or entry_id.startswith(needle):
            return 80
        if any(alias.startswith(needle) for alias in aliases):
            return 70
        searchable = " ".join(
            [
                canonical,
                entry_id,
                str(item["display_name"]).casefold(),
                str(item["description"]).casefold(),
                *(str(value).casefold() for value in item["aliases"]),
                *(str(value).casefold() for value in item["keywords"]),
                json.dumps(item["details"], ensure_ascii=False, sort_keys=True).casefold(),
            ]
        )
        return 40 if needle in searchable else 0
