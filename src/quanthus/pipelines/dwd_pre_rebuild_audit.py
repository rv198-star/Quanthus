"""Read-only physical schema and partition audit for DWD Base V2."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, cast

from quanthus.pipelines.dwd_pit_audit import _manifest_payload_files
from quanthus.pipelines.securities_warehouse.domain_base import (
    DomainBaseError,
    verify_domain_base,
)


class DwdPreRebuildAuditError(RuntimeError):
    """Raised when the Phase B audit cannot establish a complete result."""


@dataclass(frozen=True)
class ExpectedField:
    name: str
    arrow_type: str
    nullable: bool


@dataclass(frozen=True)
class TableContract:
    name: str
    partition_by: tuple[str, ...]
    fields: tuple[ExpectedField, ...]


_TYPE_MAP = {
    "bool": "bool",
    "date32": "date32[day]",
    "decimal_18_12": "decimal128(18, 12)",
    "decimal_38_10": "decimal128(38, 10)",
    "decimal_38_18": "decimal128(38, 18)",
    "decimal_9_6": "decimal128(9, 6)",
    "int32": "int32",
    "int64": "int64",
    "string": "string",
    "timestamp_us_utc": "timestamp[us, tz=UTC]",
}


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdPreRebuildAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdPreRebuildAuditError(f"{label} must be a JSON object: {path}")
    return value


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _field(name: str, raw_type: object) -> ExpectedField:
    if not isinstance(raw_type, str) or not raw_type:
        raise DwdPreRebuildAuditError(f"invalid model type for {name}")
    nullable = raw_type.endswith("?")
    base = raw_type.removesuffix("?")
    arrow_type = _TYPE_MAP.get(base)
    if arrow_type is None:
        raise DwdPreRebuildAuditError(f"unsupported model type for {name}: {raw_type}")
    return ExpectedField(name=name, arrow_type=arrow_type, nullable=nullable)


def load_table_contracts(model_path: Path, extension_path: Path) -> dict[str, TableContract]:
    """Load the exact 33-table schema contract, including the accepted v1.1 extension."""

    model = _object(model_path, "DWD model")
    extension = _object(extension_path, "DWD model extension")
    column_sets = model.get("column_sets")
    time_profiles = model.get("time_profiles")
    raw_tables = model.get("tables")
    raw_extensions = extension.get("table_extensions")
    if (
        not isinstance(column_sets, Mapping)
        or not isinstance(time_profiles, Mapping)
        or not isinstance(raw_extensions, Mapping)
        or not isinstance(raw_tables, list)
    ):
        raise DwdPreRebuildAuditError("DWD model schema sections are invalid")
    column_sets = cast(Mapping[str, object], column_sets)
    time_profiles = cast(Mapping[str, object], time_profiles)
    raw_extensions = cast(Mapping[str, object], raw_extensions)
    lineage = column_sets.get("lineage")
    if not isinstance(lineage, Mapping):
        raise DwdPreRebuildAuditError("DWD model lineage column set is invalid")

    result: dict[str, TableContract] = {}
    for raw_table in raw_tables:
        if not isinstance(raw_table, Mapping):
            raise DwdPreRebuildAuditError("DWD model table is invalid")
        name = raw_table.get("name")
        profile_name = raw_table.get("time_profile")
        raw_columns = raw_table.get("columns")
        partition_by = raw_table.get("partition_by")
        inherits = raw_table.get("inherits")
        if (
            not isinstance(name, str)
            or not isinstance(profile_name, str)
            or not isinstance(raw_columns, Mapping)
            or not isinstance(partition_by, list)
            or any(not isinstance(item, str) for item in partition_by)
            or inherits != ["lineage"]
        ):
            raise DwdPreRebuildAuditError(f"invalid table schema contract: {name}")
        profile = time_profiles.get(profile_name)
        if not isinstance(profile, Mapping):
            raise DwdPreRebuildAuditError(f"unknown time profile for {name}: {profile_name}")
        merged: dict[str, object] = dict(lineage)
        merged.update(profile)
        merged.update(raw_columns)
        raw_extension = raw_extensions.get(name, {})
        if not isinstance(raw_extension, Mapping):
            raise DwdPreRebuildAuditError(f"invalid model extension for {name}")
        additions = raw_extension.get("columns", {})
        if not isinstance(additions, Mapping) or set(merged).intersection(additions):
            raise DwdPreRebuildAuditError(f"invalid additive columns for {name}")
        merged.update(additions)
        result[name] = TableContract(
            name=name,
            partition_by=tuple(partition_by),
            fields=tuple(_field(field_name, raw_type) for field_name, raw_type in merged.items()),
        )
    if len(result) != 33 or set(raw_extensions).difference(result):
        raise DwdPreRebuildAuditError("DWD schema contract must resolve exactly 33 tables")
    return result


def _partition_values(relative_path: str, table: str) -> dict[str, str]:
    relative = PurePosixPath(relative_path)
    parts = relative.parts
    if relative.stem == table:
        return {}
    try:
        table_index = parts.index(table)
    except ValueError as exc:
        raise DwdPreRebuildAuditError(f"payload path lacks table segment: {relative_path}") from exc
    values: dict[str, str] = {}
    for part in parts[table_index + 1 : -1]:
        key, separator, value = part.partition("=")
        if not separator or not key or not value or key in values:
            raise DwdPreRebuildAuditError(f"invalid partition segment: {relative_path}")
        values[key] = value
    return values


def _schema_fields(schema: Any) -> list[dict[str, Any]]:
    return [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in schema
    ]


def _schema_mismatches(schema: Any, contract: TableContract) -> list[str]:
    actual = _schema_fields(schema)
    expected = [
        {"name": field.name, "type": field.arrow_type, "nullable": field.nullable}
        for field in contract.fields
    ]
    mismatches: list[str] = []
    actual_names = [str(item["name"]) for item in actual]
    expected_names = [str(item["name"]) for item in expected]
    missing = sorted(set(expected_names).difference(actual_names))
    extra = sorted(set(actual_names).difference(expected_names))
    if missing:
        mismatches.append("missing_fields:" + ",".join(missing))
    if extra:
        mismatches.append("extra_fields:" + ",".join(extra))
    if not missing and not extra and actual_names != expected_names:
        mismatches.append("field_order_mismatch")
    expected_by_name = {item["name"]: item for item in expected}
    for item in actual:
        expected_item = expected_by_name.get(item["name"])
        if expected_item is None:
            continue
        if item["type"] != expected_item["type"]:
            mismatches.append(
                f"type_mismatch:{item['name']}:{expected_item['type']}:{item['type']}"
            )
        if item["nullable"] != expected_item["nullable"]:
            mismatches.append(
                f"nullable_mismatch:{item['name']}:{expected_item['nullable']}:{item['nullable']}"
            )
    return mismatches


def _part_schema_classification(
    schema: Any, contract: TableContract, *, row_count: int
) -> tuple[list[str], str | None]:
    if row_count == 0 and len(schema) == 0:
        return [], "zero_row_zero_column"
    return _schema_mismatches(schema, contract), None


def _statistics(metadata: Any, column_name: str) -> list[Any] | None:
    column_index = None
    for index in range(metadata.num_columns):
        if metadata.schema.column(index).path == column_name:
            column_index = index
            break
    if column_index is None:
        return None
    result: list[Any] = []
    for group_index in range(metadata.num_row_groups):
        statistics = metadata.row_group(group_index).column(column_index).statistics
        if statistics is None or not statistics.has_min_max:
            return None
        result.extend((statistics.min, statistics.max))
    return result


def _coordinate_values(values: Sequence[Any], suffix: str) -> set[str]:
    result: set[str] = set()
    for value in values:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            coordinate: date | datetime = value.astimezone(timezone.utc)
        elif isinstance(value, date):
            coordinate = value
        else:
            return {"<unsupported-statistics-type>"}
        if suffix == "year":
            result.add(f"{coordinate.year:04d}")
        else:
            result.add(f"{coordinate.month:02d}")
    return result


def _partition_mismatches(
    parquet: Any,
    contract: TableContract,
    partition_values: Mapping[str, str],
    *,
    row_count: int,
) -> list[str]:
    mismatches: list[str] = []
    if set(partition_values) != set(contract.partition_by):
        mismatches.append(
            "partition_keys_mismatch:"
            + ",".join(sorted(partition_values))
            + ":"
            + ",".join(contract.partition_by)
        )
        return mismatches
    if row_count == 0:
        return mismatches
    schema_names = set(parquet.schema_arrow.names)
    for key, expected_value in partition_values.items():
        if key in schema_names:
            values = _statistics(parquet.metadata, key)
            if values is None:
                mismatches.append(f"partition_statistics_missing:{key}")
            elif {str(value) for value in values} != {expected_value}:
                mismatches.append(f"partition_value_mismatch:{key}")
            continue
        base, separator, suffix = key.rpartition("_")
        if separator and suffix in {"year", "month"} and base in schema_names:
            values = _statistics(parquet.metadata, base)
            if values is None:
                mismatches.append(f"partition_statistics_missing:{base}")
            elif _coordinate_values(values, suffix) != {expected_value}:
                mismatches.append(f"partition_value_mismatch:{key}")
        else:
            mismatches.append(f"partition_coordinate_unresolved:{key}")
    return mismatches


def audit_dwd_schema_and_partitions(
    *,
    root: Path,
    model_path: Path,
    extension_path: Path,
) -> dict[str, Any]:
    """Audit every manifest-declared Parquet part against the physical model contract."""

    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DwdPreRebuildAuditError("pyarrow is required for the Phase B audit") from exc

    root = root.resolve()
    try:
        verified = verify_domain_base(root)
    except DomainBaseError as exc:
        raise DwdPreRebuildAuditError(f"DWD Base verification failed: {exc}") from exc
    manifest = _object(root / "manifest.json", "DWD Base manifest")
    contracts = load_table_contracts(model_path, extension_path)
    table_paths = _manifest_payload_files(root, manifest, table_names=sorted(contracts))
    raw_counts = manifest.get("row_counts")
    if not isinstance(raw_counts, Mapping):
        raise DwdPreRebuildAuditError("DWD Base manifest row_counts are invalid")

    table_reports: dict[str, Any] = {}
    package_reason_counts: Counter[str] = Counter()
    for table, paths in sorted(table_paths.items()):
        contract = contracts[table]
        parts: list[dict[str, Any]] = []
        schema_counts: Counter[str] = Counter()
        partition_counts: Counter[tuple[tuple[str, str], ...]] = Counter()
        actual_rows = 0
        for path in paths:
            relative = path.relative_to(root).as_posix()
            parquet = pq.ParquetFile(path)
            schema = parquet.schema_arrow
            schema_fields = _schema_fields(schema)
            schema_digest = _canonical_digest(schema_fields)
            schema_counts[schema_digest] += 1
            row_count = parquet.metadata.num_rows
            actual_rows += row_count
            try:
                partition_values = _partition_values(relative, table)
                partition_key = tuple(sorted(partition_values.items()))
                partition_counts[partition_key] += 1
                reasons, empty_classification = _part_schema_classification(
                    schema, contract, row_count=row_count
                )
                reasons.extend(
                    _partition_mismatches(
                        parquet,
                        contract,
                        partition_values,
                        row_count=row_count,
                    )
                )
            except DwdPreRebuildAuditError as exc:
                partition_values = {}
                empty_classification = None
                reasons = ["invalid_partition_path:" + str(exc)]
            disposition = (
                "finding"
                if reasons
                else "classified_empty"
                if empty_classification is not None
                else "verified"
            )
            package_reason_counts.update(reasons)
            parts.append(
                {
                    "path": relative,
                    "row_count": row_count,
                    "row_group_count": parquet.metadata.num_row_groups,
                    "schema_digest": schema_digest,
                    "partition_values": dict(sorted(partition_values.items())),
                    "empty_classification": empty_classification,
                    "disposition": disposition,
                    "reason_codes": sorted(reasons),
                }
            )
        multipart_partitions = [
            {"partition_values": dict(key), "part_count": count}
            for key, count in partition_counts.items()
            if count > 1
        ]
        expected_rows = int(raw_counts[table])
        row_count_matches = actual_rows == expected_rows
        if not row_count_matches:
            package_reason_counts["manifest_row_count_mismatch"] += 1
        finding_parts = sum(item["disposition"] == "finding" for item in parts)
        classified_empty_parts = sum(item["disposition"] == "classified_empty" for item in parts)
        table_reports[table] = {
            "expected_schema": [
                {"name": field.name, "type": field.arrow_type, "nullable": field.nullable}
                for field in contract.fields
            ],
            "partition_by": list(contract.partition_by),
            "part_count": len(parts),
            "row_count": actual_rows,
            "expected_rows": expected_rows,
            "row_count_matches_manifest": row_count_matches,
            "schema_variant_count": len(schema_counts),
            "schema_digest_counts": dict(sorted(schema_counts.items())),
            "finding_part_count": finding_parts,
            "classified_empty_part_count": classified_empty_parts,
            "multipart_partitions": multipart_partitions,
            "parts": parts,
            "status": ("verified" if not finding_parts and row_count_matches else "finding"),
        }

    finding_tables = [
        table for table, report in table_reports.items() if report["status"] != "verified"
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_physical_schema_partition_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": {
            "package_id": verified.package_id,
            "manifest_sha256": verified.manifest_sha256,
            "content_digest": verified.content_digest,
            "table_count": verified.table_count,
            "row_count": verified.row_count,
            "payload_bytes": verified.payload_bytes,
            "payload_file_count": verified.payload_file_count,
        },
        "model": {
            "path": str(model_path.resolve()),
            "sha256": "sha256:" + hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "extension_path": str(extension_path.resolve()),
            "extension_sha256": "sha256:" + hashlib.sha256(extension_path.read_bytes()).hexdigest(),
        },
        "summary": {
            "status": "verified" if not finding_tables else "finding",
            "audited_table_count": len(table_reports),
            "audited_part_count": sum(item["part_count"] for item in table_reports.values()),
            "classified_empty_part_count": sum(
                item["classified_empty_part_count"] for item in table_reports.values()
            ),
            "finding_table_count": len(finding_tables),
            "finding_tables": finding_tables,
            "reason_counts": dict(sorted(package_reason_counts.items())),
            "mutation_performed": False,
        },
        "tables": table_reports,
    }
    report["semantic_digest"] = _canonical_digest(
        {
            "kind": report["kind"],
            "base_release": report["base_release"],
            "model_sha256": report["model"]["sha256"],
            "extension_sha256": report["model"]["extension_sha256"],
            "summary": report["summary"],
            "tables": report["tables"],
        }
    )
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_phase_b_report(
    path: Path,
    report: Mapping[str, Any],
    *,
    sealed_roots: Sequence[Path] = (),
) -> None:
    """Atomically write one canonical Phase B JSON report outside the package."""

    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdPreRebuildAuditError(
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
