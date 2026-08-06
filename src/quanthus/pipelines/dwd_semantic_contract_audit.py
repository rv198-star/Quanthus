"""Read-only model-driven semantic contract audit for sealed DWD Base packages."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from quanthus.pipelines.dwd_pit_audit import _manifest_payload_files, _parquet_source
from quanthus.pipelines.securities_warehouse.domain_base import (
    DomainBaseError,
    verify_domain_base,
)


class DwdSemanticContractAuditError(RuntimeError):
    """Raised when a complete semantic contract result cannot be established."""


@dataclass(frozen=True)
class ForeignKeyContract:
    column: str
    table: str
    reference_column: str
    nullable: bool


@dataclass(frozen=True)
class SemanticTableContract:
    name: str
    time_profile: str
    fields: frozenset[str]
    business_key: tuple[str, ...]
    row_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyContract, ...]
    constraints: tuple[str, ...]
    partition_by: tuple[str, ...]


_LINEAGE_FIELDS = (
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "knowledge_from",
    "knowledge_to",
)
_EFFECTIVE_COORDINATES = (
    "effective_from",
    "effective_at",
    "observation_start",
    "period_end",
    "affected_effective_at",
)
_MEMORY_LIMIT_PATTERN = re.compile(r"[1-9][0-9]*(?:MB|GB)")


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdSemanticContractAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdSemanticContractAuditError(f"{label} must be a JSON object: {path}")
    return value


def _names(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise DwdSemanticContractAuditError(f"{label} must be a non-empty string array")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise DwdSemanticContractAuditError(f"{label} contains duplicates")
    return result


def load_semantic_contracts(model_path: Path) -> dict[str, SemanticTableContract]:
    """Load keys, relationships, intervals, and constraints for exactly 33 tables."""

    model = _object(model_path, "DWD model")
    profiles = model.get("time_profiles")
    raw_tables = model.get("tables")
    lineage = model.get("column_sets", {}).get("lineage")
    if (
        not isinstance(profiles, Mapping)
        or not isinstance(raw_tables, list)
        or not isinstance(lineage, Mapping)
    ):
        raise DwdSemanticContractAuditError("DWD model contract sections are invalid")
    result: dict[str, SemanticTableContract] = {}
    for raw in raw_tables:
        if not isinstance(raw, Mapping):
            raise DwdSemanticContractAuditError("DWD model table is invalid")
        name = raw.get("name")
        profile_name = raw.get("time_profile")
        columns = raw.get("columns")
        raw_foreign_keys = raw.get("foreign_keys")
        raw_partition_by = raw.get("partition_by")
        if (
            not isinstance(name, str)
            or not isinstance(profile_name, str)
            or not isinstance(columns, Mapping)
            or not isinstance(raw_foreign_keys, list)
            or not isinstance(raw_partition_by, list)
            or any(not isinstance(item, str) for item in raw_partition_by)
            or name in result
        ):
            raise DwdSemanticContractAuditError(f"invalid semantic table contract: {name}")
        profile = profiles.get(profile_name)
        if not isinstance(profile, Mapping):
            raise DwdSemanticContractAuditError(f"unknown time profile: {name}.{profile_name}")
        fields = frozenset(lineage) | frozenset(profile) | frozenset(columns)
        business_key = _names(raw.get("business_key"), f"{name}.business_key")
        row_key = _names(raw.get("row_key"), f"{name}.row_key")
        if not set(business_key).issubset(fields) or not set(row_key).issubset(fields):
            raise DwdSemanticContractAuditError(f"key fields are absent for {name}")
        if row_key != (*business_key, "knowledge_from"):
            raise DwdSemanticContractAuditError(
                f"row key must extend the business key with knowledge_from: {name}"
            )
        foreign_keys: list[ForeignKeyContract] = []
        for raw_foreign_key in raw_foreign_keys:
            if not isinstance(raw_foreign_key, Mapping):
                raise DwdSemanticContractAuditError(f"invalid foreign key for {name}")
            try:
                foreign_key = ForeignKeyContract(
                    column=str(raw_foreign_key["column"]),
                    table=str(raw_foreign_key["table"]),
                    reference_column=str(raw_foreign_key["reference_column"]),
                    nullable=bool(raw_foreign_key["nullable"]),
                )
            except KeyError as exc:
                raise DwdSemanticContractAuditError(f"incomplete foreign key for {name}") from exc
            if foreign_key.column not in fields:
                raise DwdSemanticContractAuditError(
                    f"foreign-key field is absent: {name}.{foreign_key.column}"
                )
            foreign_keys.append(foreign_key)
        raw_constraints = raw.get("constraints", [])
        if not isinstance(raw_constraints, list) or any(
            not isinstance(item, str) for item in raw_constraints
        ):
            raise DwdSemanticContractAuditError(f"invalid constraints for {name}")
        result[name] = SemanticTableContract(
            name=name,
            time_profile=profile_name,
            fields=fields,
            business_key=business_key,
            row_key=row_key,
            foreign_keys=tuple(foreign_keys),
            constraints=tuple(raw_constraints),
            partition_by=tuple(raw_partition_by),
        )
    if len(result) != 33:
        raise DwdSemanticContractAuditError(
            f"DWD semantic model must contain 33 tables, found {len(result)}"
        )
    for contract in result.values():
        for foreign_key in contract.foreign_keys:
            parent = result.get(foreign_key.table)
            if parent is None or foreign_key.reference_column not in parent.fields:
                raise DwdSemanticContractAuditError(
                    f"foreign-key target is absent: {contract.name}.{foreign_key.column}"
                )
    return result


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _null_predicate(fields: Sequence[str], *, alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return " OR ".join(f"{prefix}{_identifier(field)} IS NULL" for field in fields)


def _count(connection: Any, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None:
        raise DwdSemanticContractAuditError("audit count query returned no row")
    return int(row[0])


def _duplicate_key_metrics(connection: Any, source: str, fields: Sequence[str]) -> dict[str, int]:
    keys = ", ".join(_identifier(field) for field in fields)
    row = connection.execute(
        f"""
        SELECT count(*) AS duplicate_group_count,
               coalesce(sum(row_count), 0) AS duplicate_row_count
        FROM (
          SELECT count(*) AS row_count
          FROM {source}
          GROUP BY {keys}
          HAVING count(*) > 1
        ) duplicates
        """
    ).fetchone()
    if row is None:
        raise DwdSemanticContractAuditError("duplicate-key query returned no row")
    return {"group_count": int(row[0]), "row_count": int(row[1])}


def _key_version_metrics(
    connection: Any, source: str, contract: SemanticTableContract
) -> dict[str, dict[str, int] | int]:
    business = ", ".join(_identifier(field) for field in contract.business_key)
    row = connection.execute(
        f"""
        WITH row_versions AS (
          SELECT {business}, knowledge_from,
                 max(knowledge_to) AS knowledge_to,
                 count(*) AS row_count
          FROM {source}
          GROUP BY {business}, knowledge_from
        ), ordered_versions AS (
          SELECT *, lag(knowledge_to) OVER (
            PARTITION BY {business} ORDER BY knowledge_from
          ) AS previous_to,
          row_number() OVER (
            PARTITION BY {business} ORDER BY knowledge_from
          ) AS version_number
          FROM row_versions
        ), business_versions AS (
          SELECT {business}, sum(row_count) AS row_count, count(*) AS version_count
          FROM row_versions
          GROUP BY {business}
        )
        SELECT
          (SELECT count(*) FROM row_versions WHERE row_count > 1),
          (SELECT coalesce(sum(row_count), 0) FROM row_versions WHERE row_count > 1),
          (SELECT count(*) FROM business_versions WHERE row_count > 1),
          (SELECT coalesce(sum(row_count), 0) FROM business_versions WHERE row_count > 1),
          (SELECT count(*) FROM ordered_versions
           WHERE version_number > 1
             AND (previous_to IS NULL OR previous_to > knowledge_from))
        """
    ).fetchone()
    if row is None:
        raise DwdSemanticContractAuditError("key-version query returned no row")
    return {
        "duplicate_row_keys": {"group_count": int(row[0]), "row_count": int(row[1])},
        "business_key_version_groups": {
            "group_count": int(row[2]),
            "row_count": int(row[3]),
        },
        "knowledge_interval_overlap_rows": int(row[4]),
    }


def _partition_confines_business_key(contract: SemanticTableContract) -> bool:
    for partition in contract.partition_by:
        base, separator, suffix = partition.rpartition("_")
        if separator and suffix in {"year", "month"}:
            coordinate = base
        elif partition in contract.fields:
            coordinate = partition
        else:
            return False
        if coordinate not in contract.business_key:
            return False
    return bool(contract.partition_by)


def _partitioned_key_version_metrics(
    connection: Any,
    paths: Sequence[Path],
    contract: SemanticTableContract,
) -> dict[str, dict[str, int] | int]:
    groups: dict[Path, list[Path]] = {}
    for path in paths:
        groups.setdefault(path.parent, []).append(path)
    if len(groups) <= 1 or not _partition_confines_business_key(contract):
        return _key_version_metrics(connection, _parquet_source(paths), contract)
    result: dict[str, dict[str, int] | int] = {
        "duplicate_row_keys": {"group_count": 0, "row_count": 0},
        "business_key_version_groups": {"group_count": 0, "row_count": 0},
        "knowledge_interval_overlap_rows": 0,
    }
    for group_paths in groups.values():
        group = _key_version_metrics(connection, _parquet_source(group_paths), contract)
        for metric in ("duplicate_row_keys", "business_key_version_groups"):
            target = cast(dict[str, int], result[metric])
            source = cast(dict[str, int], group[metric])
            target["group_count"] += source["group_count"]
            target["row_count"] += source["row_count"]
        result["knowledge_interval_overlap_rows"] = cast(
            int, result["knowledge_interval_overlap_rows"]
        ) + cast(int, group["knowledge_interval_overlap_rows"])
    return result


def _time_coordinate(contract: SemanticTableContract) -> str | None:
    return next((field for field in _EFFECTIVE_COORDINATES if field in contract.fields), None)


def _interval_metrics(
    connection: Any,
    source: str,
    contract: SemanticTableContract,
    *,
    include_knowledge_overlap: bool = True,
) -> dict[str, int]:
    predicates = {
        "invalid_knowledge_interval_rows": (
            "knowledge_to IS NOT NULL AND knowledge_to <= knowledge_from"
        )
    }
    interval_pairs = (
        ("effective_from", "effective_to"),
        ("observation_start", "observation_end"),
        ("period_start", "period_end"),
    )
    for start, end in interval_pairs:
        if start in contract.fields and end in contract.fields:
            predicates[f"invalid_{start}_{end}_rows"] = (
                f"{_identifier(end)} IS NOT NULL AND {_identifier(end)} <= {_identifier(start)}"
            )
    names = list(predicates)
    select = ", ".join(f"count(*) FILTER (WHERE {predicate})" for predicate in predicates.values())
    aggregate = connection.execute(f"SELECT {select} FROM {source}").fetchone()
    if aggregate is None:
        raise DwdSemanticContractAuditError("interval aggregate returned no row")
    metrics = {name: int(value) for name, value in zip(names, aggregate, strict=True)}
    if include_knowledge_overlap:
        knowledge_keys = ", ".join(_identifier(field) for field in contract.business_key)
        metrics["knowledge_interval_overlap_rows"] = _count(
            connection,
            f"""
            SELECT count(*) FROM (
              SELECT knowledge_from, lag(knowledge_to) OVER (
                PARTITION BY {knowledge_keys} ORDER BY knowledge_from
              ) AS previous_to,
              row_number() OVER (
                PARTITION BY {knowledge_keys} ORDER BY knowledge_from
              ) AS version_number
              FROM {source}
            ) versions
            WHERE version_number > 1 AND (previous_to IS NULL OR previous_to > knowledge_from)
            """,
        )
    if "effective_from" in contract.fields and "effective_to" in contract.fields:
        identity_fields = tuple(
            field for field in contract.business_key if field != "effective_from"
        )
        if identity_fields:
            identity = ", ".join(_identifier(field) for field in identity_fields)
            business = ", ".join(_identifier(field) for field in contract.business_key)
            metrics["effective_interval_overlap_rows"] = _count(
                connection,
                f"""
                WITH latest AS (
                  SELECT * EXCLUDE (version_rank) FROM (
                    SELECT *, row_number() OVER (
                      PARTITION BY {business} ORDER BY knowledge_from DESC
                    ) AS version_rank
                    FROM {source}
                  ) WHERE version_rank = 1
                ), ordered AS (
                  SELECT effective_from,
                         lag(effective_to) OVER (
                           PARTITION BY {identity} ORDER BY effective_from
                         ) AS previous_to,
                         row_number() OVER (
                           PARTITION BY {identity} ORDER BY effective_from
                         ) AS interval_number
                  FROM latest
                )
                SELECT count(*) FROM ordered
                WHERE interval_number > 1
                  AND (previous_to IS NULL OR previous_to > effective_from)
                """,
            )
    return metrics


def _lineage_metrics(connection: Any, source: str) -> dict[str, int]:
    required = _LINEAGE_FIELDS[:-1]
    predicates = {
        "null_required_lineage_rows": _null_predicate(required),
        "invalid_source_artifact_sha256_rows": (
            "NOT regexp_full_match(source_artifact_sha256, 'sha256:[0-9a-f]{64}')"
        ),
        "blank_lineage_rows": (
            "trim(record_source) = '' OR trim(source_record_id) = '' OR trim(source_revision) = ''"
        ),
    }
    row = connection.execute(
        "SELECT "
        + ", ".join(f"count(*) FILTER (WHERE {predicate})" for predicate in predicates.values())
        + f" FROM {source}"
    ).fetchone()
    if row is None:
        raise DwdSemanticContractAuditError("lineage aggregate returned no row")
    return {name: int(value) for name, value in zip(predicates, row, strict=True)}


def _numeric_metrics(
    connection: Any, source: str, contract: SemanticTableContract
) -> dict[str, int]:
    predicates = _numeric_predicates(contract)
    if not predicates:
        return {}
    row = connection.execute(
        "SELECT "
        + ", ".join(f"count(*) FILTER (WHERE {predicate})" for predicate in predicates.values())
        + f" FROM {source}"
    ).fetchone()
    if row is None:
        raise DwdSemanticContractAuditError("numeric aggregate returned no row")
    return {name: int(value) for name, value in zip(predicates, row, strict=True)}


def _numeric_predicates(contract: SemanticTableContract) -> dict[str, str]:
    result: dict[str, str] = {}
    fields = contract.fields
    if {"open", "high", "low", "close"}.issubset(fields):
        result["ohlc_envelope_rows"] = (
            "high < greatest(open, close, low) OR low > least(open, close, high)"
        )
    for field in (
        "volume",
        "turnover",
        "trade_count",
        "share_quantity",
        "holder_count",
        "average_holding",
        "holding_quantity",
        "post_holding",
    ):
        if field in fields:
            result[f"negative_{field}_rows"] = f"{_identifier(field)} < 0"
    for field in ("holding_percentage", "post_percentage"):
        if field in fields:
            result[f"out_of_range_{field}_rows"] = (
                f"{_identifier(field)} < 0 OR {_identifier(field)} > 100"
            )
    if "factor_value" in fields:
        result["nonpositive_factor_rows"] = "factor_value <= 0"
    if "confidence" in fields:
        result["out_of_range_confidence_rows"] = "confidence < 0 OR confidence > 1"
    if "price_basis_equals_unadjusted" in contract.constraints:
        result["non_unadjusted_price_basis_rows"] = "price_basis <> 'UNADJUSTED'"
    return result


def _foreign_key_metrics(
    connection: Any,
    *,
    child_source: str,
    child_contract: SemanticTableContract,
    parent_sources: Mapping[str, str],
    parent_contracts: Mapping[str, SemanticTableContract],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    child_coordinate = _time_coordinate(child_contract)
    for foreign_key in child_contract.foreign_keys:
        parent_source = parent_sources[foreign_key.table]
        parent_contract = parent_contracts[foreign_key.table]
        child_column = _identifier(foreign_key.column)
        parent_column = _identifier(foreign_key.reference_column)
        nonnull = f"c.{child_column} IS NOT NULL"
        equality = f"p.{parent_column} = c.{child_column}"
        orphan_rows = _count(
            connection,
            f"""
            SELECT count(*) FROM {child_source} c
            WHERE {nonnull} AND NOT EXISTS (
              SELECT 1 FROM {parent_source} p WHERE {equality}
            )
            """,
        )
        temporal_terms = [equality, "p.knowledge_from <= c.knowledge_from"]
        temporal_terms.append("(p.knowledge_to IS NULL OR p.knowledge_to > c.knowledge_from)")
        if (
            child_coordinate is not None
            and "effective_from" in parent_contract.fields
            and "effective_to" in parent_contract.fields
        ):
            coordinate = f"c.{_identifier(child_coordinate)}"
            temporal_terms.extend(
                (
                    f"p.effective_from <= {coordinate}",
                    f"(p.effective_to IS NULL OR p.effective_to > {coordinate})",
                )
            )
        temporal_or_future_rows = _count(
            connection,
            f"""
            SELECT count(*) FROM {child_source} c
            WHERE {nonnull} AND NOT EXISTS (
              SELECT 1 FROM {parent_source} p
              WHERE {" AND ".join(temporal_terms)}
            )
            """,
        )
        reports.append(
            {
                "column": foreign_key.column,
                "parent_table": foreign_key.table,
                "parent_column": foreign_key.reference_column,
                "nullable": foreign_key.nullable,
                "orphan_rows": orphan_rows,
                "temporal_or_future_parent_rows": temporal_or_future_rows,
                "effective_coordinate": child_coordinate,
            }
        )
    return reports


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _prepare_scratch(root: Path, scratch: Path) -> Path:
    sealed = root.resolve()
    requested = scratch.resolve()
    if requested == sealed or requested.is_relative_to(sealed) or scratch.is_symlink():
        raise DwdSemanticContractAuditError(
            f"audit scratch must remain outside sealed package: {requested}"
        )
    requested.mkdir(parents=True, exist_ok=True)
    if not requested.is_dir():
        raise DwdSemanticContractAuditError(f"audit scratch is not a directory: {requested}")
    return requested


def _validated_memory_limit(value: str) -> str:
    normalized = value.strip().upper()
    if _MEMORY_LIMIT_PATTERN.fullmatch(normalized) is None:
        raise DwdSemanticContractAuditError(
            "memory limit must be a positive whole number followed by MB or GB"
        )
    return normalized


def _outside_sealed(root: Path, path: Path, label: str) -> Path:
    sealed = root.resolve()
    resolved = path.resolve()
    if resolved == sealed or resolved.is_relative_to(sealed):
        raise DwdSemanticContractAuditError(
            f"audit {label} must remain outside sealed package: {resolved}"
        )
    return resolved


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(path)


def _checkpoint_value(
    identity: Mapping[str, Any], table_reports: Mapping[str, Any]
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_semantic_contract_audit_checkpoint",
        "identity": dict(identity),
        "completed_table_count": len(table_reports),
        "tables": dict(table_reports),
        "mutation_performed": False,
    }
    value["canonical_digest"] = _canonical_digest(value)
    return value


def _load_checkpoint(
    path: Path,
    *,
    identity: Mapping[str, Any],
    allowed_tables: set[str],
) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = _object(path, "Phase C checkpoint")
    supplied_digest = value.pop("canonical_digest", None)
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "dwd_base_v2_semantic_contract_audit_checkpoint"
        or value.get("identity") != dict(identity)
        or value.get("mutation_performed") is not False
        or supplied_digest != _canonical_digest(value)
    ):
        raise DwdSemanticContractAuditError("Phase C checkpoint identity or digest differs")
    tables = value.get("tables")
    if not isinstance(tables, dict) or not set(tables).issubset(allowed_tables):
        raise DwdSemanticContractAuditError("Phase C checkpoint table scope differs")
    if value.get("completed_table_count") != len(tables):
        raise DwdSemanticContractAuditError("Phase C checkpoint count differs")
    return tables


def audit_dwd_semantic_contracts(
    *,
    root: Path,
    model_path: Path,
    scratch: Path,
    checkpoint_path: Path,
    memory_limit: str = "1GB",
) -> dict[str, Any]:
    """Execute keys, intervals, lineage, FK, and explicit invariant checks for all tables."""

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DwdSemanticContractAuditError("duckdb is required for Phase C") from exc

    root = root.resolve()
    scratch = _prepare_scratch(root, scratch)
    memory_limit = _validated_memory_limit(memory_limit)
    checkpoint_path = _outside_sealed(root, checkpoint_path, "checkpoint")
    try:
        verified = verify_domain_base(root)
    except DomainBaseError as exc:
        raise DwdSemanticContractAuditError(f"DWD Base verification failed: {exc}") from exc
    manifest = _object(root / "manifest.json", "DWD Base manifest")
    contracts = load_semantic_contracts(model_path)
    table_paths = _manifest_payload_files(root, manifest, table_names=sorted(contracts))
    sources = {table: _parquet_source(paths) for table, paths in table_paths.items()}
    raw_counts = manifest.get("row_counts")
    if not isinstance(raw_counts, Mapping):
        raise DwdSemanticContractAuditError("DWD Base manifest row_counts are invalid")

    model_sha256 = "sha256:" + hashlib.sha256(model_path.read_bytes()).hexdigest()
    audit_identity = {
        "package_id": verified.package_id,
        "manifest_sha256": verified.manifest_sha256,
        "content_digest": verified.content_digest,
        "model_sha256": model_sha256,
        "auditor_sha256": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "memory_limit": memory_limit,
    }
    table_reports = _load_checkpoint(
        checkpoint_path,
        identity=audit_identity,
        allowed_tables=set(contracts),
    )

    connection = duckdb.connect(":memory:")
    connection.execute("SET TimeZone='UTC'")
    escaped_scratch = str(scratch).replace("'", "''")
    connection.execute(f"SET temp_directory='{escaped_scratch}'")
    connection.execute(f"SET memory_limit='{memory_limit}'")
    connection.execute("SET preserve_insertion_order=false")
    try:
        for table, contract in sorted(contracts.items()):
            if table in table_reports:
                continue
            source = sources[table]
            aggregate = connection.execute(
                f"SELECT count(*), count(*) FILTER (WHERE "
                f"{_null_predicate(contract.row_key)}) FROM {source}"
            ).fetchone()
            if aggregate is None:
                raise DwdSemanticContractAuditError(f"aggregate returned no row: {table}")
            numeric = _numeric_metrics(connection, source, contract)
            key_versions = _partitioned_key_version_metrics(
                connection, table_paths[table], contract
            )
            intervals = _interval_metrics(
                connection, source, contract, include_knowledge_overlap=False
            )
            intervals["knowledge_interval_overlap_rows"] = int(
                cast(int, key_versions["knowledge_interval_overlap_rows"])
            )
            report: dict[str, Any] = {
                "row_count": int(aggregate[0]),
                "expected_rows": int(raw_counts[table]),
                "null_row_key_rows": int(aggregate[1]),
                "row_key": list(contract.row_key),
                "business_key": list(contract.business_key),
                "duplicate_row_keys": key_versions["duplicate_row_keys"],
                "business_key_version_groups": key_versions["business_key_version_groups"],
                "intervals": intervals,
                "lineage": _lineage_metrics(connection, source),
                "numeric_invariants": numeric,
                "foreign_keys": _foreign_key_metrics(
                    connection,
                    child_source=source,
                    child_contract=contract,
                    parent_sources=sources,
                    parent_contracts=contracts,
                ),
            }
            violation_count = (
                int(report["null_row_key_rows"])
                + int(report["duplicate_row_keys"]["row_count"])
                + sum(report["intervals"].values())
                + sum(report["lineage"].values())
                + sum(numeric.values())
                + sum(
                    int(item["orphan_rows"]) + int(item["temporal_or_future_parent_rows"])
                    for item in report["foreign_keys"]
                )
            )
            if report["row_count"] != report["expected_rows"]:
                violation_count += 1
            report["violation_count"] = violation_count
            report["status"] = "verified" if violation_count == 0 else "finding"
            table_reports[table] = report
            _write_json_atomic(
                checkpoint_path,
                _checkpoint_value(audit_identity, table_reports),
            )
    except Exception as exc:
        if isinstance(exc, DwdSemanticContractAuditError):
            raise
        raise DwdSemanticContractAuditError(f"semantic audit query failed: {exc}") from exc
    finally:
        connection.close()

    finding_tables = [
        table for table, report in table_reports.items() if report["status"] == "finding"
    ]
    report = {
        "schema_version": 1,
        "kind": "dwd_base_v2_semantic_contract_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": {
            "package_id": verified.package_id,
            "manifest_sha256": verified.manifest_sha256,
            "content_digest": verified.content_digest,
            "table_count": verified.table_count,
            "row_count": verified.row_count,
        },
        "model": {
            "path": str(model_path.resolve()),
            "sha256": model_sha256,
        },
        "auditor_sha256": audit_identity["auditor_sha256"],
        "execution": {
            "memory_limit": memory_limit,
            "scratch": str(scratch),
        },
        "checkpoint_path": str(checkpoint_path),
        "summary": {
            "status": "verified" if not finding_tables else "finding",
            "audited_table_count": len(table_reports),
            "finding_table_count": len(finding_tables),
            "finding_tables": finding_tables,
            "mutation_performed": False,
        },
        "tables": table_reports,
    }
    report["semantic_digest"] = _canonical_digest(
        {
            "kind": report["kind"],
            "base_release": report["base_release"],
            "model_sha256": report["model"]["sha256"],
            "summary": report["summary"],
            "tables": report["tables"],
        }
    )
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_semantic_contract_report(
    path: Path,
    report: Mapping[str, Any],
    *,
    sealed_roots: Sequence[Path] = (),
) -> None:
    """Atomically write Phase C evidence outside the sealed package."""

    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdSemanticContractAuditError(
                f"audit output must remain outside sealed package: {resolved}"
            )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.partial")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(resolved)
