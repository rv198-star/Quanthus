"""Read-only ODS source sufficiency audit for the DWD V2.1 remediation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.ods_base import OdsBaseError, verify_ods_base_bundle


class OdsPitAuditError(RuntimeError):
    """Raised when an ODS PIT audit request or policy violates its contract."""


_ALLOWED_FORMATS = {"json", "parquet", "sqlite"}
_ALLOWED_MODES = {"full", "manifest-only"}


@dataclass(frozen=True)
class OdsPhysicalCheck:
    """One source dataset/member and the fields needed for PIT sufficiency evidence."""

    check_id: str
    dataset_id: str
    file_format: str
    member: str | None
    path_suffix: str | None
    required_columns: tuple[str, ...]
    business_time_fields: tuple[str, ...]
    knowledge_fields: tuple[str, ...]


@dataclass(frozen=True)
class OdsPitAuditPolicy:
    """Validated machine-readable policy for one ODS Base sufficiency audit."""

    path: Path
    policy_sha256: str
    package_id: str
    manifest_sha256: str
    content_digest: str
    through_date: date
    required_mirror_labels: tuple[str, ...]
    successor_package_id: str
    successor_through_date: date
    daily_after: date
    checks: tuple[OdsPhysicalCheck, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OdsPitAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise OdsPitAuditError(f"{label} must be a JSON object: {path}")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OdsPitAuditError(f"{field} must be non-empty text")
    return value.strip()


def _date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(_text(value, field))
    except ValueError as exc:
        raise OdsPitAuditError(f"{field} must be an ISO date") from exc


def _strings(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise OdsPitAuditError(f"{field} must be an array")
    result = tuple(_text(item, field) for item in value)
    if (not allow_empty and not result) or len(result) != len(set(result)):
        raise OdsPitAuditError(f"{field} must contain unique values")
    return result


def load_ods_pit_audit_policy(path: Path) -> OdsPitAuditPolicy:
    """Load and validate the ODS source sufficiency audit policy."""

    raw = _object(path, "ODS PIT sufficiency policy")
    if raw.get("schema_version") != 1 or raw.get("kind") != "ods_pit_sufficiency_audit":
        raise OdsPitAuditError("ODS PIT sufficiency policy identity is invalid")
    base = raw.get("ods_base")
    successor = raw.get("successor_dwd_boundary")
    raw_checks = raw.get("physical_checks")
    if not isinstance(base, Mapping) or not isinstance(successor, Mapping):
        raise OdsPitAuditError("ODS PIT sufficiency policy release boundary is invalid")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise OdsPitAuditError("ODS PIT sufficiency policy has no physical checks")

    checks: list[OdsPhysicalCheck] = []
    observed: set[str] = set()
    for raw_check in raw_checks:
        if not isinstance(raw_check, Mapping):
            raise OdsPitAuditError("ODS physical check row is invalid")
        check_id = _text(raw_check.get("check_id"), "physical_checks.check_id")
        dataset_id = _text(raw_check.get("dataset_id"), f"{check_id}.dataset_id")
        file_format = _text(raw_check.get("format"), f"{check_id}.format")
        if check_id in observed or file_format not in _ALLOWED_FORMATS:
            raise OdsPitAuditError(f"duplicate or unsupported physical check: {check_id}")
        member_raw = raw_check.get("member")
        member = _text(member_raw, f"{check_id}.member") if member_raw is not None else None
        if file_format == "sqlite" and member is None:
            raise OdsPitAuditError(f"SQLite check requires member: {check_id}")
        if file_format != "sqlite" and member is not None:
            raise OdsPitAuditError(f"non-SQLite check cannot declare member: {check_id}")
        suffix_raw = raw_check.get("path_suffix")
        path_suffix = (
            _text(suffix_raw, f"{check_id}.path_suffix")
            if suffix_raw is not None
            else None
        )
        if path_suffix is not None and (
            path_suffix.startswith("/") or ".." in PurePosixPath(path_suffix).parts
        ):
            raise OdsPitAuditError(f"unsafe path suffix: {check_id}")
        checks.append(
            OdsPhysicalCheck(
                check_id=check_id,
                dataset_id=dataset_id,
                file_format=file_format,
                member=member,
                path_suffix=path_suffix,
                required_columns=_strings(
                    raw_check.get("required_columns"),
                    f"{check_id}.required_columns",
                    allow_empty=True,
                ),
                business_time_fields=_strings(
                    raw_check.get("business_time_fields"),
                    f"{check_id}.business_time_fields",
                    allow_empty=True,
                ),
                knowledge_fields=_strings(
                    raw_check.get("knowledge_fields"),
                    f"{check_id}.knowledge_fields",
                    allow_empty=True,
                ),
            )
        )
        observed.add(check_id)

    required_labels = _strings(base.get("physical_holders"), "ods_base.physical_holders")
    if required_labels != ("hermes", "cn01") or bool(base.get("oci_ods_enabled")):
        raise OdsPitAuditError("ODS holder boundary must remain Hermes/CN01 only")
    through_date = _date(base.get("through_date"), "ods_base.through_date")
    successor_through = _date(
        successor.get("through_date"), "successor_dwd_boundary.through_date"
    )
    daily_after = _date(successor.get("daily_after"), "successor_dwd_boundary.daily_after")
    if successor_through != through_date or daily_after != through_date:
        raise OdsPitAuditError("successor Base and Daily boundary must equal the ODS through-date")
    if successor.get("daily_disposition") != (
        "excluded_from_v2_1_base_and_cleaned_rebuilt_rebased_separately"
    ):
        raise OdsPitAuditError("successor Daily disposition is invalid")

    return OdsPitAuditPolicy(
        path=path.resolve(),
        policy_sha256=_sha256(path),
        package_id=_text(base.get("package_id"), "ods_base.package_id"),
        manifest_sha256=_text(base.get("manifest_sha256"), "ods_base.manifest_sha256"),
        content_digest=_text(base.get("content_digest"), "ods_base.content_digest"),
        through_date=through_date,
        required_mirror_labels=required_labels,
        successor_package_id=_text(
            successor.get("package_id"), "successor_dwd_boundary.package_id"
        ),
        successor_through_date=successor_through,
        daily_after=daily_after,
        checks=tuple(checks),
    )


def _safe_part(root: Path, value: object) -> Path:
    relative_text = _text(value, "dataset part path")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or ".." in relative.parts or not relative_text.startswith("payload/"):
        raise OdsPitAuditError(f"unsafe ODS dataset part path: {relative_text}")
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file():
        raise OdsPitAuditError(f"ODS dataset part is absent or unsafe: {relative_text}")
    return path


def _dataset_parts(root: Path, manifest: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    content = manifest.get("content")
    if not isinstance(content, Mapping):
        raise OdsPitAuditError("ODS manifest content is invalid")
    raw_datasets = content.get("datasets")
    if not isinstance(raw_datasets, list):
        raise OdsPitAuditError("ODS manifest datasets are invalid")
    result: dict[str, list[dict[str, Any]]] = {}
    for raw_dataset in raw_datasets:
        if not isinstance(raw_dataset, Mapping):
            raise OdsPitAuditError("ODS manifest dataset row is invalid")
        dataset_id = _text(raw_dataset.get("dataset_id"), "content.datasets.dataset_id")
        raw_parts = raw_dataset.get("parts")
        if dataset_id in result or not isinstance(raw_parts, list) or not raw_parts:
            raise OdsPitAuditError(f"ODS dataset is duplicate or empty: {dataset_id}")
        parts: list[dict[str, Any]] = []
        for raw_part in raw_parts:
            if not isinstance(raw_part, Mapping):
                raise OdsPitAuditError(f"ODS dataset part row is invalid: {dataset_id}")
            path = _safe_part(root, raw_part.get("path"))
            parts.append(
                {
                    "path": path,
                    "relative_path": path.relative_to(root).as_posix(),
                    "format": _text(raw_part.get("format"), f"{dataset_id}.parts.format"),
                    "sha256": _text(raw_part.get("sha256"), f"{dataset_id}.parts.sha256"),
                    "size_bytes": int(raw_part.get("size_bytes", -1)),
                }
            )
        result[dataset_id] = parts
    return result


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _field_statistics_sql(fields: Sequence[str]) -> str:
    expressions = ["count(*) AS row_count"]
    for index, field in enumerate(fields):
        quoted = _quoted_identifier(field)
        expressions.extend(
            [
                f"count(*) FILTER (WHERE {quoted} IS NULL) AS null_{index}",
                f"min({quoted}) AS min_{index}",
                f"max({quoted}) AS max_{index}",
            ]
        )
    return ", ".join(expressions)


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise OdsPitAuditError(f"{field} must be an integer")
    return value


def _statistics_from_row(row: Sequence[object], fields: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"row_count": _integer(row[0], "row_count"), "fields": {}}
    offset = 1
    field_stats = result["fields"]
    if not isinstance(field_stats, dict):
        raise OdsPitAuditError("internal field statistics state is invalid")
    for field in fields:
        field_stats[field] = {
            "null_rows": _integer(row[offset], f"{field}.null_rows"),
            "minimum": _json_value(row[offset + 1]),
            "maximum": _json_value(row[offset + 2]),
        }
        offset += 3
    return result


def _scan_parquet(paths: Sequence[Path], check: OdsPhysicalCheck) -> dict[str, Any]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise OdsPitAuditError("duckdb is required for a full ODS PIT audit") from exc
    connection = duckdb.connect(":memory:")
    try:
        rendered = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
        source = f"read_parquet([{rendered}], union_by_name=true, hive_partitioning=true)"
        schema = connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
        columns = {str(row[0]) for row in schema}
        missing = sorted(set(check.required_columns).difference(columns))
        if missing:
            raise OdsPitAuditError(f"{check.check_id} lacks required columns: {missing}")
        fields = tuple(dict.fromkeys((*check.business_time_fields, *check.knowledge_fields)))
        row = connection.execute(
            f"SELECT {_field_statistics_sql(fields)} FROM {source}"
        ).fetchone()
        if row is None:
            raise OdsPitAuditError(f"{check.check_id} returned no aggregate row")
        return {
            "format": "parquet",
            "part_count": len(paths),
            "columns": sorted(columns),
            **_statistics_from_row(row, fields),
        }
    finally:
        connection.close()


def _scan_sqlite(path: Path, check: OdsPhysicalCheck) -> dict[str, Any]:
    if check.member is None:
        raise OdsPitAuditError(f"SQLite check has no member: {check.check_id}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({check.member})")}
        missing = sorted(set(check.required_columns).difference(columns))
        if missing:
            raise OdsPitAuditError(f"{check.check_id} lacks required columns: {missing}")
        fields = tuple(dict.fromkeys((*check.business_time_fields, *check.knowledge_fields)))
        row = connection.execute(
            f"SELECT {_field_statistics_sql(fields)} FROM {_quoted_identifier(check.member)}"
        ).fetchone()
        if row is None:
            raise OdsPitAuditError(f"{check.check_id} returned no aggregate row")
        return {
            "format": "sqlite",
            "member": check.member,
            "part_count": 1,
            "columns": sorted(columns),
            **_statistics_from_row(row, fields),
        }
    except sqlite3.Error as exc:
        raise OdsPitAuditError(f"cannot audit SQLite member: {check.check_id}") from exc
    finally:
        connection.close()


def _scan_json(path: Path, check: OdsPhysicalCheck) -> dict[str, Any]:
    value = _object(path, check.check_id)
    return {
        "format": "json",
        "part_count": 1,
        "top_level_kind": "object",
        "top_level_entries": len(value),
        "row_count": len(value),
        "fields": {},
    }


def _scan_check(parts: Sequence[dict[str, Any]], check: OdsPhysicalCheck) -> dict[str, Any]:
    formats = {str(part["format"]) for part in parts}
    if formats != {check.file_format}:
        raise OdsPitAuditError(
            f"{check.check_id} format differs: {sorted(formats)} != {check.file_format}"
        )
    paths = [part["path"] for part in parts]
    if not all(isinstance(path, Path) for path in paths):
        raise OdsPitAuditError(f"{check.check_id} has invalid part paths")
    typed_paths = [path for path in paths if isinstance(path, Path)]
    if check.file_format == "parquet":
        return _scan_parquet(typed_paths, check)
    if len(typed_paths) != 1:
        raise OdsPitAuditError(f"{check.check_id} requires exactly one package part")
    if check.file_format == "sqlite":
        return _scan_sqlite(typed_paths[0], check)
    return _scan_json(typed_paths[0], check)


def _validate_output_paths(paths: Sequence[Path], *, mirrors: Mapping[str, Path]) -> None:
    mirror_roots = [root.resolve() for root in mirrors.values()]
    for raw_path in paths:
        path = raw_path.resolve()
        for root in mirror_roots:
            if path == root or root in path.parents:
                raise OdsPitAuditError(
                    "audit output must not be written inside a sealed ODS mirror"
                )


def audit_ods_pit_sufficiency(
    *,
    mirrors: Mapping[str, Path],
    policy_path: Path,
    mode: str = "full",
) -> dict[str, Any]:
    """Deep-verify ODS mirrors and scan the required PIT source fields read-only."""

    if mode not in _ALLOWED_MODES:
        raise OdsPitAuditError(f"unsupported audit mode: {mode}")
    if not mirrors:
        raise OdsPitAuditError("at least one ODS mirror is required")
    resolved_roots = [root.resolve() for root in mirrors.values()]
    if len(resolved_roots) != len(set(resolved_roots)):
        raise OdsPitAuditError("mirror labels must identify distinct physical roots")
    policy = load_ods_pit_audit_policy(policy_path)

    reports: list[dict[str, Any]] = []
    for label, raw_root in sorted(mirrors.items()):
        root = raw_root.resolve()
        report: dict[str, Any] = {
            "label": label,
            "root": str(root),
            "verification_status": "failed",
            "identity_matches_policy": False,
            "scan_status": "not_started",
            "checks": {},
            "accessed_parts": [],
        }
        try:
            verified = verify_ods_base_bundle(root, deep=(mode == "full"))
            report.update(
                {
                    "verification_status": "verified",
                    "package_id": verified.bundle_id,
                    "manifest_sha256": verified.manifest_sha256,
                    "content_digest": verified.content_digest,
                    "through_date": verified.through_date.isoformat(),
                    "file_count": verified.file_count,
                    "size_bytes": verified.size_bytes,
                }
            )
            identity_matches = (
                verified.bundle_id == policy.package_id
                and verified.manifest_sha256 == policy.manifest_sha256
                and verified.content_digest == policy.content_digest
                and verified.through_date == policy.through_date
            )
            report["identity_matches_policy"] = identity_matches
            if not identity_matches:
                raise OdsPitAuditError("ODS package identity differs from the audit policy")
            if mode == "manifest-only":
                report["scan_status"] = "manifest_only"
            else:
                manifest = _object(root / "manifest.json", "ODS Base manifest")
                datasets = _dataset_parts(root, manifest)
                check_reports: dict[str, Any] = {}
                accessed: list[str] = []
                for check in policy.checks:
                    parts = datasets.get(check.dataset_id)
                    if parts is None:
                        raise OdsPitAuditError(
                            f"ODS package lacks required dataset: {check.dataset_id}"
                        )
                    selected_parts = (
                        [
                            part
                            for part in parts
                            if str(part["relative_path"]).endswith(check.path_suffix)
                        ]
                        if check.path_suffix is not None
                        else parts
                    )
                    if not selected_parts:
                        raise OdsPitAuditError(
                            f"ODS dataset has no part matching check: {check.check_id}"
                        )
                    check_reports[check.check_id] = _scan_check(selected_parts, check)
                    accessed.extend(
                        str(part["relative_path"]) for part in selected_parts
                    )
                report["checks"] = check_reports
                report["accessed_parts"] = sorted(set(accessed))
                report["scan_status"] = "full"
        except (OdsPitAuditError, OdsBaseError, OSError, PermissionError) as exc:
            report["error"] = str(exc)
        reports.append(report)

    supplied_labels = {str(report["label"]) for report in reports}
    missing_labels = sorted(set(policy.required_mirror_labels).difference(supplied_labels))
    unexpected_labels = sorted(supplied_labels.difference(policy.required_mirror_labels))
    failed_labels = [
        str(report["label"])
        for report in reports
        if report["verification_status"] != "verified"
    ]
    identity_mismatches = [
        str(report["label"])
        for report in reports
        if report["verification_status"] == "verified"
        and not bool(report["identity_matches_policy"])
    ]
    expected_scan = "full" if mode == "full" else "manifest_only"
    incomplete_scans = [
        str(report["label"])
        for report in reports
        if report["verification_status"] == "verified"
        and report["scan_status"] != expected_scan
    ]
    identities = {
        (
            report.get("package_id"),
            report.get("manifest_sha256"),
            report.get("content_digest"),
        )
        for report in reports
        if report["verification_status"] == "verified"
    }
    mirror_divergence = len(identities) > 1

    reason_codes: list[str] = []
    if missing_labels:
        reason_codes.append("required_mirror_not_supplied")
    if unexpected_labels:
        reason_codes.append("unexpected_mirror_label")
    if failed_labels:
        reason_codes.append("mirror_verification_failed")
    if identity_mismatches:
        reason_codes.append("mirror_identity_mismatch")
    if incomplete_scans:
        reason_codes.append("source_scan_incomplete")
    if mirror_divergence:
        reason_codes.append("mirror_identity_divergence")
    execution_status = "passed" if not reason_codes else "blocked"

    report_body: dict[str, Any] = {
        "schema_version": 1,
        "kind": "ods_pit_sufficiency_audit_report",
        "policy": {
            "path": str(policy.path),
            "sha256": policy.policy_sha256,
        },
        "ods_base": {
            "package_id": policy.package_id,
            "manifest_sha256": policy.manifest_sha256,
            "content_digest": policy.content_digest,
            "through_date": policy.through_date.isoformat(),
        },
        "successor_boundary": {
            "package_id": policy.successor_package_id,
            "through_date": policy.successor_through_date.isoformat(),
            "daily_after": policy.daily_after.isoformat(),
            "daily_disposition": (
                "excluded_from_v2_1_base_and_cleaned_rebuilt_rebased_separately"
            ),
        },
        "mode": mode,
        "mirrors": reports,
        "execution_gate": {
            "status": execution_status,
            "reason_codes": reason_codes,
            "missing_labels": missing_labels,
            "failed_labels": failed_labels,
            "identity_mismatches": identity_mismatches,
            "incomplete_scans": incomplete_scans,
            "mirror_divergence": mirror_divergence,
        },
        "remediation_gate": {
            "status": "blocked_pending_source_semantic_approval",
            "reason": (
                "Physical ODS success confirms package facts but does not by itself approve "
                "identity, valuation, financial, or holder-change reconstruction rules."
            ),
        },
        "accessed_artifacts": {
            "mutation_performed": False,
            "package_roots": {label: str(root.resolve()) for label, root in mirrors.items()},
            "output_inside_package": False,
        },
    }
    report_body["report_digest"] = _digest(report_body)
    return report_body


def write_ods_pit_audit_report(path: Path, report: Mapping[str, Any]) -> None:
    """Atomically write an audit report outside all sealed package roots."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(dict(report)) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
