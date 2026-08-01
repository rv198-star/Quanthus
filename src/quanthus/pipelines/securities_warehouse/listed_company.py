"""Build the listed-company identity backbone from one sealed ODS input plan."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.code_validity import (
    CodeValidityError,
    CodeValidityInterval,
    load_code_validity_mapping,
)


class ListedCompanyBuildError(RuntimeError):
    """Raised when the listed-company candidate cannot be built without ambiguity."""


@dataclass(frozen=True)
class ListedCompanyBuildResult:
    root: Path
    semantic_digest: str
    row_counts: dict[str, int]
    primary_listed_count: int
    bse_listed_count: int


@dataclass(frozen=True)
class _IdentityMember:
    key: tuple[str, str]
    row: Mapping[str, Any]
    security_group: str
    company_group: tuple[str, str]
    effective_from: datetime
    effective_to: datetime | None
    knowledge_from: datetime
    validity: CodeValidityInterval | None


_OWNED_TABLES = (
    "dwd_listed_company",
    "dwd_company_identifier",
    "dwd_security",
    "dwd_security_identifier",
    "dwd_listing",
    "dwd_trading_venue",
)
_SHA256 = "sha256:"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PLAN_FILES = {
    "capacity-estimate.json",
    "coverage-matrix.json",
    "input-plan.json",
    "run-receipt.json",
    "source-disposition-ledger.json",
}
_OUTPUT_FILES = {
    "checkpoint.json",
    "reconciliation/source-row-reconciliation.json",
    "receipts/listed-company-build.json",
    *{f"payload/listed_company/{table}.parquet" for table in _OWNED_TABLES},
}
_OUTPUT_DIRECTORIES = {
    "payload",
    "payload/listed_company",
    "receipts",
    "reconciliation",
}


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ListedCompanyBuildError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ListedCompanyBuildError(f"JSON root must be an object: {path}")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ListedCompanyBuildError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ListedCompanyBuildError(f"{field} must be an array")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ListedCompanyBuildError(f"{key} must be a non-empty string")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ListedCompanyBuildError(f"cannot hash file: {path}") from exc
    return f"sha256:{digest.hexdigest()}"


def _normalize(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    return value


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        _normalize(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ListedCompanyBuildError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ListedCompanyBuildError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListedCompanyBuildError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date_value(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ListedCompanyBuildError(f"{field} must be a date") from exc
    raise ListedCompanyBuildError(f"{field} must be a date")


def _effective_start(value: date) -> datetime:
    return datetime.combine(value, datetime_time.min, _SHANGHAI).astimezone(timezone.utc)


def _effective_end(value: date | None) -> datetime | None:
    return _effective_start(value + timedelta(days=1)) if value is not None else None


def _safe_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise ListedCompanyBuildError("source path must be a relative string")
    path = PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise ListedCompanyBuildError(f"unsafe source path: {relative}")
    resolved = root.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ListedCompanyBuildError(f"source path escapes ODS package: {relative}")
    return resolved


def _verify_plan_package(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if root.is_symlink():
        raise ListedCompanyBuildError("input plan package must not be a symbolic link")
    root = root.resolve()
    if not root.is_dir():
        raise ListedCompanyBuildError("input plan package must be a directory")
    expected_physical = _PLAN_FILES | {"manifest.json", "READY"}
    entries = list(root.iterdir())
    if {entry.name for entry in entries} != expected_physical or any(
        entry.is_symlink() or not entry.is_file() for entry in entries
    ):
        raise ListedCompanyBuildError("input plan package physical inventory is not exact")
    manifest_path = root / "manifest.json"
    manifest = _object(manifest_path)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ListedCompanyBuildError("input plan READY is unreadable") from exc
    if ready != _sha256(manifest_path):
        raise ListedCompanyBuildError("input plan READY does not match manifest")
    if manifest.get("schema_version") != 1 or manifest.get("kind") != (
        "dwd_dws_input_plan_package"
    ):
        raise ListedCompanyBuildError("input plan manifest has an unsupported contract")
    declared: set[str] = set()
    for raw in _sequence(manifest.get("files"), "plan manifest files"):
        item = _mapping(raw, "plan manifest file")
        relative = _text(item, "path")
        if "/" in relative or relative in declared:
            raise ListedCompanyBuildError("input plan has an invalid file inventory")
        declared.add(relative)
        path = root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise ListedCompanyBuildError(f"input plan file failed verification: {relative}")
    if declared != _PLAN_FILES:
        raise ListedCompanyBuildError("input plan package must declare the exact five plan files")
    plan = _object(root / "input-plan.json")
    ledger = _object(root / "source-disposition-ledger.json")
    if (
        plan.get("schema_version") != 1
        or plan.get("kind") != "dwd_dws_input_plan"
        or ledger.get("schema_version") != 1
    ):
        raise ListedCompanyBuildError("input plan or source ledger has an unsupported contract")
    semantic_plan = _mapping(plan.get("plan"), "input plan")
    recalculated_plan_digest = _canonical_digest(semantic_plan)
    if (
        manifest.get("plan_digest") != plan.get("plan_digest")
        or plan.get("plan_digest") != recalculated_plan_digest
    ):
        raise ListedCompanyBuildError("input plan digest differs from package manifest")
    if semantic_plan.get("source_ledger_digest") != _canonical_digest(ledger):
        raise ListedCompanyBuildError("source ledger digest differs from the sealed input plan")
    return plan, ledger


def _verify_source(record: Mapping[str, Any], ods_root: Path) -> Path:
    path = _safe_file(ods_root, record.get("path"))
    size = record.get("size_bytes")
    digest = record.get("sha256")
    if (
        not path.is_file()
        or not isinstance(size, int)
        or path.stat().st_size != size
        or not isinstance(digest, str)
        or _sha256(path) != digest
    ):
        raise ListedCompanyBuildError(f"ODS source failed verification: {record.get('path')}")
    return path


def _seed_contract(path: Path) -> tuple[dict[str, Any], str, datetime]:
    seed = _object(path)
    if (
        seed.get("schema_version") != 1
        or seed.get("status") != "frozen"
        or len(_sequence(seed.get("venues"), "seed venues")) != 3
    ):
        raise ListedCompanyBuildError("reference seed is not the frozen v1 venue contract")
    exchanges = {
        _text(_mapping(raw, "seed venue"), "exchange_code")
        for raw in _sequence(seed.get("venues"), "seed venues")
    }
    if exchanges != {"SSE", "SZSE", "BSE"}:
        raise ListedCompanyBuildError("reference seed must own SSE, SZSE, and BSE")
    snapshot = _mapping(seed.get("source_snapshot"), "reference seed source snapshot")
    retrieved_at = _timestamp(snapshot.get("retrieved_at"), "reference seed retrieved_at")
    return seed, _sha256(path), retrieved_at


def _board(seed: Mapping[str, Any], exchange: str, code: str) -> str:
    for raw in _sequence(seed.get("board_rules"), "board rules"):
        rule = _mapping(raw, "board rule")
        if rule.get("exchange_code") != exchange:
            continue
        prefixes = _sequence(rule.get("prefixes"), "board prefixes")
        if "*" in prefixes or any(
            isinstance(prefix, str) and code.startswith(prefix) for prefix in prefixes
        ):
            return _text(rule, "board_code")
    raise ListedCompanyBuildError(f"no board rule for {exchange}:{code}")


def _table_schemas() -> dict[str, Any]:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise ListedCompanyBuildError("pyarrow is required for DWD construction") from exc
    common = [
        pa.field("record_source", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("effective_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("effective_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    return {
        "dwd_listed_company": pa.schema(
            common
            + [
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("display_name", pa.string(), nullable=False),
                pa.field("company_type", pa.string(), nullable=False),
                pa.field("resolution_scope", pa.string(), nullable=False),
            ]
        ),
        "dwd_company_identifier": pa.schema(
            common
            + [
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("identifier_namespace", pa.string(), nullable=False),
                pa.field("identifier_value", pa.string(), nullable=False),
                pa.field("identifier_authority", pa.string(), nullable=False),
            ]
        ),
        "dwd_security": pa.schema(
            common
            + [
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("company_id", pa.string(), nullable=False),
                pa.field("instrument_type", pa.string(), nullable=False),
                pa.field("instrument_currency", pa.string(), nullable=False),
            ]
        ),
        "dwd_security_identifier": pa.schema(
            common
            + [
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("identifier_namespace", pa.string(), nullable=False),
                pa.field("identifier_value", pa.string(), nullable=False),
                pa.field("venue_id", pa.string(), nullable=True),
            ]
        ),
        "dwd_listing": pa.schema(
            common
            + [
                pa.field("listing_id", pa.string(), nullable=False),
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("venue_id", pa.string(), nullable=False),
                pa.field("listing_code", pa.string(), nullable=False),
                pa.field("board_code", pa.string(), nullable=False),
                pa.field("listing_currency", pa.string(), nullable=False),
                pa.field("lot_size", pa.int64(), nullable=True),
            ]
        ),
        "dwd_trading_venue": pa.schema(
            common
            + [
                pa.field("venue_id", pa.string(), nullable=False),
                pa.field("mic_code", pa.string(), nullable=False),
                pa.field("venue_name", pa.string(), nullable=False),
                pa.field("country_code", pa.string(), nullable=False),
                pa.field("timezone", pa.string(), nullable=False),
            ]
        ),
    }


def _row_key(table: str, row: Mapping[str, Any]) -> tuple[object, ...]:
    keys = {
        "dwd_listed_company": ("company_id", "effective_from", "knowledge_from"),
        "dwd_company_identifier": (
            "identifier_namespace",
            "identifier_value",
            "effective_from",
            "knowledge_from",
        ),
        "dwd_security": ("security_id", "effective_from", "knowledge_from"),
        "dwd_security_identifier": (
            "identifier_namespace",
            "identifier_value",
            "effective_from",
            "knowledge_from",
        ),
        "dwd_listing": ("listing_id", "effective_from", "knowledge_from"),
        "dwd_trading_venue": ("venue_id", "effective_from", "knowledge_from"),
    }[table]
    return tuple(row[key] for key in keys)


def _identity_key(table: str, row: Mapping[str, Any]) -> tuple[object, ...]:
    keys = {
        "dwd_listed_company": ("company_id",),
        "dwd_company_identifier": ("identifier_namespace", "identifier_value"),
        "dwd_security": ("security_id",),
        "dwd_security_identifier": ("identifier_namespace", "identifier_value"),
        "dwd_listing": ("listing_id",),
        "dwd_trading_venue": ("venue_id",),
    }[table]
    return tuple(row[key] for key in keys)


def _interval_overlaps(
    left_start: datetime,
    left_end: datetime | None,
    right_start: datetime,
    right_end: datetime | None,
) -> bool:
    return (right_end is None or left_start < right_end) and (
        left_end is None or right_start < left_end
    )


def _interval_contains(
    parent_start: datetime,
    parent_end: datetime | None,
    child_start: datetime,
    child_end: datetime | None,
) -> bool:
    if parent_start > child_start:
        return False
    if parent_end is None:
        return True
    return child_end is not None and child_end <= parent_end


def _temporally_contains(parent: Mapping[str, Any], child: Mapping[str, Any]) -> bool:
    return _interval_contains(
        parent["effective_from"],
        parent.get("effective_to"),
        child["effective_from"],
        child.get("effective_to"),
    ) and _interval_contains(
        parent["knowledge_from"],
        parent.get("knowledge_to"),
        child["knowledge_from"],
        child.get("knowledge_to"),
    )


def _active_segments(
    members: Sequence[_IdentityMember],
    *,
    additional_boundaries: Sequence[datetime] = (),
) -> list[tuple[datetime, datetime | None, tuple[_IdentityMember, ...]]]:
    boundaries = sorted(
        {*additional_boundaries}
        | {
            boundary
            for member in members
            for boundary in (member.effective_from, member.effective_to)
            if boundary is not None
        }
    )
    segments: list[tuple[datetime, datetime | None, tuple[_IdentityMember, ...]]] = []
    for index, start in enumerate(boundaries):
        active = tuple(
            sorted(
                (
                    member
                    for member in members
                    if member.effective_from <= start
                    and (member.effective_to is None or start < member.effective_to)
                ),
                key=lambda member: (
                    member.key,
                    member.security_group,
                    member.knowledge_from,
                ),
            )
        )
        if not active:
            continue
        end = boundaries[index + 1] if index + 1 < len(boundaries) else None
        segments.append((start, end, active))
    return segments


def _validate_rows(rows: Mapping[str, list[dict[str, Any]]]) -> None:
    for table, values in rows.items():
        keys = [_row_key(table, row) for row in values]
        if len(keys) != len(set(keys)):
            raise ListedCompanyBuildError(f"duplicate row key in {table}")
        for row in values:
            effective_end = row.get("effective_to")
            if effective_end is not None and effective_end <= row["effective_from"]:
                raise ListedCompanyBuildError(f"invalid effective interval in {table}")
            knowledge_end = row.get("knowledge_to")
            if knowledge_end is not None and knowledge_end <= row["knowledge_from"]:
                raise ListedCompanyBuildError(f"invalid knowledge interval in {table}")
        grouped: dict[tuple[object, ...], list[Mapping[str, Any]]] = defaultdict(list)
        for row in values:
            grouped[_identity_key(table, row)].append(row)
        for identity, versions in grouped.items():
            for index, left in enumerate(versions):
                for right in versions[index + 1 :]:
                    if _interval_overlaps(
                        left["effective_from"],
                        left.get("effective_to"),
                        right["effective_from"],
                        right.get("effective_to"),
                    ) and _interval_overlaps(
                        left["knowledge_from"],
                        left.get("knowledge_to"),
                        right["knowledge_from"],
                        right.get("knowledge_to"),
                    ):
                        raise ListedCompanyBuildError(
                            f"bitemporal versions overlap in {table}: {identity}"
                        )

    foreign_keys = (
        ("dwd_company_identifier", "company_id", "dwd_listed_company", "company_id", False),
        ("dwd_security", "company_id", "dwd_listed_company", "company_id", False),
        (
            "dwd_security_identifier",
            "security_id",
            "dwd_security",
            "security_id",
            False,
        ),
        ("dwd_security_identifier", "venue_id", "dwd_trading_venue", "venue_id", True),
        ("dwd_listing", "security_id", "dwd_security", "security_id", False),
        ("dwd_listing", "venue_id", "dwd_trading_venue", "venue_id", False),
    )
    for source_table, source_column, target_table, target_column, nullable in foreign_keys:
        for row in rows[source_table]:
            value = row.get(source_column)
            if value is None and nullable:
                continue
            matches = [
                target
                for target in rows[target_table]
                if target.get(target_column) == value and _temporally_contains(target, row)
            ]
            if len(matches) != 1:
                raise ListedCompanyBuildError(
                    "temporal foreign key must resolve exactly once: "
                    f"{source_table}.{source_column}->{target_table}.{target_column}"
                )


def _write_json_atomic(path: Path, value: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o640)
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_text_atomic(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_orphaned_temporaries(work: Path) -> None:
    final_paths = {work / relative for relative in _OUTPUT_FILES | {"manifest.json", "READY"}}
    for final_path in sorted(final_paths):
        for candidate in final_path.parent.glob(f".{final_path.name}.*"):
            if candidate.is_dir() and not candidate.is_symlink():
                raise ListedCompanyBuildError(f"orphaned temporary path is not a file: {candidate}")
            candidate.unlink(missing_ok=True)
    for table in _OWNED_TABLES:
        partial = work / "payload" / "listed_company" / f"{table}.parquet.partial"
        if partial.is_dir() and not partial.is_symlink():
            raise ListedCompanyBuildError(f"partial table path is not a file: {partial}")
        partial.unlink(missing_ok=True)


def _work_directory(output: Path, run_id: str, semantic_digest: str) -> Path:
    identity_digest = _canonical_digest(
        {"run_id": run_id, "semantic_digest": semantic_digest}
    ).removeprefix(_SHA256)
    return output.parent / f".{output.name}.{identity_digest}.work"


def _initialize_unowned_work(
    work: Path, checkpoint_path: Path, checkpoint_identity: Mapping[str, Any]
) -> None:
    allowed_directories = {
        Path("payload"),
        Path("payload/listed_company"),
        Path("receipts"),
        Path("reconciliation"),
    }
    if work.exists():
        if work.is_symlink() or not work.is_dir():
            raise ListedCompanyBuildError("build work path must be a physical directory")
        for path in work.rglob("*"):
            relative = path.relative_to(work)
            if path.is_symlink():
                raise ListedCompanyBuildError("uninitialized work contains a symbolic link")
            if path.is_dir() and relative in allowed_directories:
                continue
            if path.is_file() and path.parent == work and path.name.startswith(".checkpoint.json."):
                continue
            raise ListedCompanyBuildError(
                f"uninitialized work contains an unexpected path: {relative}"
            )
        _remove_orphaned_temporaries(work)
    else:
        work.mkdir(mode=0o750)
    for relative in sorted(allowed_directories, key=lambda value: len(value.parts)):
        (work / relative).mkdir(exist_ok=True)
    _write_json_atomic(checkpoint_path, checkpoint_identity)


def _verify_existing_output(
    output: Path,
    run_id: str,
    semantic_digest: str,
    row_counts: Mapping[str, int],
) -> None:
    if output.is_symlink() or not output.is_dir():
        raise ListedCompanyBuildError("existing output must be a physical directory")
    manifest_path = output / "manifest.json"
    manifest = _object(manifest_path)
    try:
        ready = (output / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ListedCompanyBuildError("existing output READY is unreadable") from exc
    if ready != _sha256(manifest_path):
        raise ListedCompanyBuildError("existing output READY does not match manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "dwd_listed_company_candidate"
        or manifest.get("run_id") != run_id
        or manifest.get("semantic_digest") != semantic_digest
        or manifest.get("owned_tables") != list(_OWNED_TABLES)
        or manifest.get("row_counts") != dict(row_counts)
    ):
        raise ListedCompanyBuildError("existing output belongs to another run or semantic build")
    declared: set[str] = set()
    for raw in _sequence(manifest.get("files"), "output manifest files"):
        item = _mapping(raw, "output manifest file")
        relative = _text(item, "path")
        if relative in declared:
            raise ListedCompanyBuildError("existing output manifest has duplicate files")
        declared.add(relative)
        path = _safe_file(output, relative)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item.get("size_bytes")
            or _sha256(path) != item.get("sha256")
        ):
            raise ListedCompanyBuildError(f"existing output file failed verification: {relative}")
    if declared != _OUTPUT_FILES:
        raise ListedCompanyBuildError("existing output manifest file inventory is not exact")
    physical_files: set[str] = set()
    physical_directories: set[str] = set()
    for path in output.rglob("*"):
        relative = path.relative_to(output).as_posix()
        if path.is_symlink():
            raise ListedCompanyBuildError("existing output contains a symbolic link")
        if path.is_file():
            physical_files.add(relative)
        elif path.is_dir():
            physical_directories.add(relative)
        else:
            raise ListedCompanyBuildError("existing output contains an unsupported file type")
    if physical_files != _OUTPUT_FILES | {"manifest.json", "READY"}:
        raise ListedCompanyBuildError("existing output physical file inventory is not exact")
    if physical_directories != _OUTPUT_DIRECTORIES:
        raise ListedCompanyBuildError("existing output directory inventory is not exact")

    checkpoint = _object(output / "checkpoint.json")
    receipt = _object(output / "receipts" / "listed-company-build.json")
    completed = _mapping(checkpoint.get("completed_tables"), "completed tables")
    declared_table_digests = _mapping(receipt.get("table_digests"), "table digests")
    if (
        checkpoint.get("schema_version") != 1
        or checkpoint.get("run_id") != run_id
        or checkpoint.get("semantic_digest") != semantic_digest
        or set(completed) != set(_OWNED_TABLES)
        or receipt.get("schema_version") != 1
        or receipt.get("kind") != "dwd_listed_company_build_receipt"
        or receipt.get("run_id") != run_id
        or receipt.get("semantic_digest") != semantic_digest
        or receipt.get("row_counts") != dict(row_counts)
        or set(declared_table_digests) != set(_OWNED_TABLES)
    ):
        raise ListedCompanyBuildError("output checkpoint or receipt identity is inconsistent")
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ListedCompanyBuildError("pyarrow is required for output verification") from exc
    actual_table_digests: dict[str, str] = {}
    schemas = _table_schemas()
    for table in _OWNED_TABLES:
        relative = f"payload/listed_company/{table}.parquet"
        path = output / relative
        table_value = pq.read_table(path)
        actual_digest = _canonical_digest(table_value.to_pylist())
        actual_table_digests[table] = actual_digest
        table_checkpoint = _mapping(completed.get(table), f"checkpoint {table}")
        if (
            table_value.num_rows != row_counts[table]
            or actual_digest != declared_table_digests[table]
            or table_checkpoint.get("path") != relative
            or table_checkpoint.get("row_count") != row_counts[table]
            or table_checkpoint.get("schema_digest") != _canonical_digest(str(schemas[table]))
            or table_checkpoint.get("semantic_digest") != actual_digest
            or table_checkpoint.get("sha256") != _sha256(path)
        ):
            raise ListedCompanyBuildError(f"output table semantic verification failed: {table}")
    recomputed_semantic_digest = _canonical_digest(
        {
            "input_plan_digest": receipt.get("input_plan_digest"),
            "model_sha256": receipt.get("model_sha256"),
            "reference_seed_sha256": receipt.get("reference_seed_sha256"),
            "code_validity_sha256": receipt.get("code_validity_sha256"),
            "table_digests": actual_table_digests,
        }
    )
    if recomputed_semantic_digest != semantic_digest:
        raise ListedCompanyBuildError("output semantic digest does not match its payload")


def _build_listed_company_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    reference_seed_path: Path,
    code_validity_path: Path,
    output: Path,
    run_id: str,
) -> ListedCompanyBuildResult:
    """Build six deterministic listed-company DWD objects from an exact Base plan."""

    plan, ledger = _verify_plan_package(input_plan)
    semantic_plan = _mapping(plan.get("plan"), "input plan")
    if semantic_plan.get("mode") != "base":
        raise ListedCompanyBuildError("listed-company v1 currently requires a Base input plan")
    if plan.get("run_id") != run_id:
        raise ListedCompanyBuildError("builder run id must match the sealed input plan")
    model = _object(model_path)
    model_identity = _mapping(semantic_plan.get("model"), "input plan model")
    model_sha = _sha256(model_path)
    if model_identity.get("sha256") != model_sha or model_identity.get("contract_id") != model.get(
        "contract_id"
    ):
        raise ListedCompanyBuildError("model contract differs from the sealed input plan")
    knowledge_cutoff = _timestamp(semantic_plan.get("knowledge_cutoff"), "knowledge_cutoff")
    seed, seed_sha, seed_knowledge_from = _seed_contract(reference_seed_path)
    if seed_knowledge_from > knowledge_cutoff:
        raise ListedCompanyBuildError("reference seed exceeds the plan knowledge cutoff")
    code_validity_sha = _sha256(code_validity_path)
    try:
        code_validity = load_code_validity_mapping(code_validity_path)
    except CodeValidityError as exc:
        raise ListedCompanyBuildError(f"invalid security-code validity mapping: {exc}") from exc
    late_validity = [
        interval
        for interval in code_validity.intervals
        if interval.knowledge_cutoff.astimezone(timezone.utc) > knowledge_cutoff
    ]
    if late_validity:
        raise ListedCompanyBuildError("code-validity evidence exceeds the plan knowledge cutoff")

    ledger_records = [
        _mapping(raw, "source ledger record")
        for raw in _sequence(ledger.get("records"), "source ledger records")
    ]
    modeled = [
        record
        for record in ledger_records
        if record.get("record_type") == "file"
        and record.get("disposition") == "modeled"
        and set(record.get("target_tables", [])).intersection(_OWNED_TABLES)
    ]
    stock_list_evidence = [
        record
        for record in ledger_records
        if record.get("record_type") == "file"
        and record.get("disposition") == "superseded"
        and record.get("mapping_identity") == "stock_list"
        and record.get("path") == "payload/canonical/stock_list.parquet"
    ]
    if len(stock_list_evidence) != 1:
        raise ListedCompanyBuildError(
            "Base plan must declare one superseded canonical stock-list evidence file"
        )
    relevant = [*modeled, *stock_list_evidence]
    verified_paths = {
        str(record["path"]): _verify_source(record, ods_package) for record in relevant
    }
    by_path = {str(record["path"]): record for record in relevant}
    required_paths = {
        "payload/canonical/ref_security_master.parquet",
        "payload/canonical/stock_list.parquet",
        "payload/canonical/ref_trading_calendar.parquet",
        "payload/canonical/cninfo_orgid_map.json",
    }
    if not required_paths.issubset(verified_paths):
        raise ListedCompanyBuildError("Base plan is missing a canonical identity source")

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise ListedCompanyBuildError("pyarrow is required for DWD construction") from exc

    master_relative = "payload/canonical/ref_security_master.parquet"
    master_rows = pq.read_table(verified_paths[master_relative]).to_pylist()
    master_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    business_fields = (
        "security_id",
        "stock_code",
        "exchange",
        "name",
        "list_date",
        "delist_date",
        "status",
        "bs_code",
        "source",
    )
    for raw in master_rows:
        row = dict(raw)
        key = (str(row.get("exchange")), str(row.get("stock_code")))
        if key in master_by_key:
            raise ListedCompanyBuildError(f"duplicate canonical security-master key: {key}")
        for field in business_fields:
            if field not in row:
                raise ListedCompanyBuildError(f"security master is missing {field}")
        master_by_key[key] = row

    reconciliation: list[dict[str, Any]] = []
    quality_exceptions: list[dict[str, Any]] = []
    for relative, path in sorted(verified_paths.items()):
        record = by_path[relative]
        name = PurePosixPath(relative).name
        if name not in {"ref_security_master.parquet", "security_lifecycle.parquet"}:
            continue
        snapshot_rows = pq.read_table(path).to_pylist()
        bse_conflicts: list[str] = []
        for raw in snapshot_rows:
            row = dict(raw)
            key = (str(row.get("exchange")), str(row.get("stock_code")))
            canonical = master_by_key.get(key)
            if canonical is None or any(
                canonical.get(field) != row.get(field) for field in business_fields
            ):
                if key[0] == "BSE":
                    bse_conflicts.append(key[1])
                    continue
                raise ListedCompanyBuildError(
                    f"security-master snapshot conflicts with canonical row: {key}"
                )
        if bse_conflicts:
            quality_exceptions.append(
                {
                    "code": "BSE_SECURITY_MASTER_SNAPSHOT_DIFFERENCE",
                    "policy": "BSE_PARTIAL_NONBLOCKING",
                    "path": relative,
                    "affected_rows": len(bse_conflicts),
                    "sample_stock_codes": sorted(bse_conflicts)[:20],
                    "resolution": "canonical_base_authority_retained",
                }
            )
        reconciliation.append(
            {
                "dataset_id": record.get("dataset_id"),
                "path": relative,
                "source_rows": len(snapshot_rows),
                "outcome": (
                    "selected_authority"
                    if relative == master_relative
                    else (
                        "corroborated_snapshot_with_bse_exception"
                        if bse_conflicts
                        else "corroborated_snapshot"
                    )
                ),
                "unmatched_rows": len(bse_conflicts),
            }
        )

    org_relative = "payload/canonical/cninfo_orgid_map.json"
    org_map_raw = json.loads(verified_paths[org_relative].read_text(encoding="utf-8"))
    if not isinstance(org_map_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in org_map_raw.items()
    ):
        raise ListedCompanyBuildError("CNINFO organization map is invalid")
    org_map = {str(key): str(value) for key, value in org_map_raw.items()}
    missing_primary_org = sorted(
        str(row["stock_code"])
        for row in master_rows
        if row.get("exchange") in {"SSE", "SZSE"} and not org_map.get(str(row["stock_code"]))
    )
    if missing_primary_org:
        raise ListedCompanyBuildError(
            "SSE/SZSE security master has rows without CNINFO organization id"
        )
    missing_bse_org = sorted(
        str(row["stock_code"])
        for row in master_rows
        if row.get("exchange") == "BSE" and not org_map.get(str(row["stock_code"]))
    )
    if missing_bse_org:
        quality_exceptions.append(
            {
                "code": "BSE_COMPANY_IDENTITY_PARTIAL",
                "policy": "BSE_PARTIAL_NONBLOCKING",
                "affected_rows": len(missing_bse_org),
                "resolution": "provider_security_identity_retained",
            }
        )
    reconciliation.append(
        {
            "dataset_id": by_path[org_relative].get("dataset_id"),
            "path": org_relative,
            "source_rows": len(org_map),
            "outcome": "selected_company_identity_map",
            "matched_rows": sum(bool(org_map.get(str(row["stock_code"]))) for row in master_rows),
            "unmatched_bse_rows": len(missing_bse_org),
            "filtered_non_security_master_rows": len(
                set(org_map) - {str(row["stock_code"]) for row in master_rows}
            ),
        }
    )

    for relative, path in sorted(verified_paths.items()):
        if PurePosixPath(relative).name != "stock_list.parquet":
            continue
        values = pq.read_table(path, columns=["stock_code"]).to_pylist()
        unknown = sorted(
            str(row["stock_code"])
            for row in values
            if str(row["stock_code"]) not in {str(item["stock_code"]) for item in master_rows}
        )
        if unknown:
            raise ListedCompanyBuildError(
                "stock-list row is absent from the canonical security master"
            )
        reconciliation.append(
            {
                "dataset_id": by_path[relative].get("dataset_id"),
                "path": relative,
                "source_rows": len(values),
                "outcome": "corroborated_collection_scope",
                "unmatched_rows": 0,
            }
        )

    venue_seed = {
        _text(_mapping(raw, "seed venue"), "exchange_code"): dict(_mapping(raw, "seed venue"))
        for raw in _sequence(seed.get("venues"), "seed venues")
    }
    for relative, path in sorted(verified_paths.items()):
        if PurePosixPath(relative).name != "ref_trading_calendar.parquet":
            continue
        values = pq.read_table(path, columns=["exchange"]).to_pylist()
        unknown = sorted({str(row["exchange"]) for row in values}.difference(venue_seed))
        if unknown:
            raise ListedCompanyBuildError(f"calendar references unknown venues: {unknown}")
        reconciliation.append(
            {
                "dataset_id": by_path[relative].get("dataset_id"),
                "path": relative,
                "source_rows": len(values),
                "outcome": "folded_venue_reference_evidence",
                "unmatched_rows": 0,
            }
        )

    validity_by_code: dict[tuple[str, str], list[CodeValidityInterval]] = defaultdict(list)
    for interval in code_validity.intervals:
        key = (interval.exchange, interval.stock_code)
        if key not in master_by_key:
            raise ListedCompanyBuildError(
                f"code-validity interval references unknown master row: {key}"
            )
        validity_by_code[key].append(interval)

    members: list[_IdentityMember] = []
    for key, row in sorted(master_by_key.items()):
        intervals: Sequence[CodeValidityInterval | None] = validity_by_code.get(key, ())
        if not intervals:
            intervals = (None,)
        for validity in intervals:
            raw_security_id = _text(row, "security_id")
            security_group = (
                validity.security_id if validity is not None else f"{key[0]}:{raw_security_id}"
            )
            start = _effective_start(
                validity.valid_from
                if validity is not None
                else _date_value(row.get("list_date"), "list_date")
            )
            end = _effective_end(
                validity.valid_to
                if validity is not None
                else (
                    _date_value(row.get("delist_date"), "delist_date")
                    if row.get("delist_date") is not None
                    else None
                )
            )
            org_id = org_map.get(key[1])
            if org_id:
                company_group = ("CNINFO_ORG_ID", org_id)
            elif key[0] == "BSE":
                company_group = ("BSE_SOURCE_SECURITY_ID", security_group)
            else:  # guarded by missing_primary_org above
                raise ListedCompanyBuildError(f"unresolved primary company identity: {key}")
            member = _IdentityMember(
                key=key,
                row=row,
                security_group=security_group,
                company_group=company_group,
                effective_from=start,
                effective_to=end,
                knowledge_from=max(
                    knowledge_cutoff,
                    validity.knowledge_cutoff.astimezone(timezone.utc)
                    if validity is not None
                    else knowledge_cutoff,
                ),
                validity=validity,
            )
            members.append(member)

    security_groups: dict[str, list[_IdentityMember]] = defaultdict(list)
    company_groups: dict[tuple[str, str], list[_IdentityMember]] = defaultdict(list)
    for member in members:
        security_groups[member.security_group].append(member)
        company_groups[member.company_group].append(member)

    company_ids = {group: _stable_id("company", group[0], group[1]) for group in company_groups}
    security_ids = {
        group: _stable_id("security", "ECONOMIC_SECURITY", group) for group in security_groups
    }
    venue_ids = {
        exchange: _stable_id("venue", str(venue["mic_code"]))
        for exchange, venue in venue_seed.items()
    }
    venue_knowledge_from = {
        exchange: max(
            seed_knowledge_from,
            max(
                (member.knowledge_from for member in members if member.key[0] == exchange),
                default=seed_knowledge_from,
            ),
        )
        for exchange in venue_seed
    }
    master_record = by_path[master_relative]
    master_sha = _text(master_record, "sha256")
    master_revision = "security-master@" + str(master_rows[0].get("as_of"))
    org_sha = _text(by_path[org_relative], "sha256")
    seed_revision = _text(seed, "seed_id")

    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in _OWNED_TABLES}
    company_segments: dict[
        tuple[str, str], list[tuple[datetime, datetime | None, tuple[_IdentityMember, ...]]]
    ] = {}
    for company_group, group_members in sorted(company_groups.items()):
        segments = _active_segments(group_members)
        company_segments[company_group] = segments
        for start, end, active in segments:
            names = {str(member.row["name"]).strip() for member in active}
            if len(names) != 1:
                raise ListedCompanyBuildError(
                    f"concurrent company names are ambiguous: {company_group}"
                )
            exchanges = {member.key[0] for member in active}
            namespace, identifier = company_group
            scope = (
                "CNINFO_ORG_ID:BSE_PARTIAL_NONBLOCKING"
                if namespace == "CNINFO_ORG_ID" and exchanges == {"BSE"}
                else (
                    "CNINFO_ORG_ID:PRIMARY"
                    if namespace == "CNINFO_ORG_ID"
                    else "SOURCE_SECURITY_ID:BSE_PARTIAL_NONBLOCKING"
                )
            )
            active_keys = ",".join(f"{member.key[0]}:{member.key[1]}" for member in active)
            common = {
                "record_source": "cninfo_orgid_map+ref_security_master+security_code_validity",
                "source_record_id": (
                    f"{org_relative}#{namespace}={identifier}&members={active_keys}"
                ),
                "source_revision": f"{seed_revision}:{code_validity.version}",
                "source_artifact_sha256": org_sha,
                "knowledge_from": max(member.knowledge_from for member in active),
                "knowledge_to": None,
                "effective_from": start,
                "effective_to": end,
            }
            rows["dwd_listed_company"].append(
                {
                    **common,
                    "company_id": company_ids[company_group],
                    "display_name": next(iter(names)),
                    "company_type": "provider_scoped_listed_company",
                    "resolution_scope": scope,
                }
            )
            rows["dwd_company_identifier"].append(
                {
                    **common,
                    "company_id": company_ids[company_group],
                    "identifier_namespace": namespace,
                    "identifier_value": identifier,
                    "identifier_authority": "CNINFO"
                    if namespace == "CNINFO_ORG_ID"
                    else "SOURCE_PROVIDER",
                }
            )

    for group, group_members in sorted(security_groups.items()):
        resolved_companies = {member.company_group for member in group_members}
        if len(resolved_companies) != 1:
            raise ListedCompanyBuildError(
                f"one economic security resolves to multiple companies: {group}"
            )
        company_group = next(iter(resolved_companies))
        company_boundaries = [
            boundary
            for start, end, _active in company_segments[company_group]
            for boundary in (start, end)
            if boundary is not None
        ]
        for start, end, active in _active_segments(
            group_members, additional_boundaries=company_boundaries
        ):
            source_member = active[0]
            mapped = any(member.validity is not None for member in active)
            company_matches = [
                company_active
                for company_start, company_end, company_active in company_segments[company_group]
                if _interval_contains(company_start, company_end, start, end)
            ]
            if len(company_matches) != 1:
                raise ListedCompanyBuildError(
                    f"security interval must resolve one company segment: {group}"
                )
            security_knowledge_from = max(
                max(member.knowledge_from for member in active),
                max(member.knowledge_from for member in company_matches[0]),
            )
            rows["dwd_security"].append(
                {
                    "record_source": "ref_security_master+security_code_validity",
                    "source_record_id": (
                        f"{master_relative}#exchange={source_member.key[0]}"
                        f"&stock_code={source_member.key[1]}"
                    ),
                    "source_revision": code_validity.version if mapped else master_revision,
                    "source_artifact_sha256": code_validity_sha if mapped else master_sha,
                    "knowledge_from": security_knowledge_from,
                    "knowledge_to": None,
                    "effective_from": start,
                    "effective_to": end,
                    "security_id": security_ids[group],
                    "company_id": company_ids[company_group],
                    "instrument_type": "common_equity",
                    "instrument_currency": "CNY",
                }
            )
            for member in active:
                source_row = member.row
                key = member.key
                venue_id = venue_ids[key[0]]
                member_validity = member.validity
                lineage = {
                    "record_source": (
                        "security_code_validity"
                        if member_validity is not None
                        else "ref_security_master"
                    ),
                    "source_record_id": (
                        f"{code_validity_path.name}#exchange={key[0]}&stock_code={key[1]}"
                        if member_validity is not None
                        else f"{master_relative}#exchange={key[0]}&stock_code={key[1]}"
                    ),
                    "source_revision": (
                        member_validity.source_revision
                        if member_validity is not None
                        else master_revision
                    ),
                    "source_artifact_sha256": (
                        member_validity.evidence_sha256
                        if member_validity is not None
                        else master_sha
                    ),
                    "knowledge_from": max(
                        member.knowledge_from,
                        security_knowledge_from,
                        venue_knowledge_from[key[0]],
                    ),
                    "knowledge_to": None,
                    "effective_from": start,
                    "effective_to": end,
                }
                listing_id = _stable_id(
                    "listing",
                    security_ids[group],
                    venue_id,
                    key[1],
                    member.effective_from.isoformat(),
                )
                rows["dwd_listing"].append(
                    {
                        **lineage,
                        "listing_id": listing_id,
                        "security_id": security_ids[group],
                        "venue_id": venue_id,
                        "listing_code": key[1],
                        "board_code": _board(seed, key[0], key[1]),
                        "listing_currency": "CNY",
                        "lot_size": 100,
                    }
                )
                identifiers = [
                    (f"CN_STOCK_CODE:{key[0]}", key[1]),
                    (
                        "AKSHARE_BSE_SECURITY_ID" if key[0] == "BSE" else "BAOSTOCK_SECURITY_ID",
                        str(source_row["security_id"]),
                    ),
                ]
                if source_row.get("bs_code"):
                    identifiers.append(("BAOSTOCK_CODE", str(source_row["bs_code"])))
                for namespace, value in identifiers:
                    rows["dwd_security_identifier"].append(
                        {
                            **lineage,
                            "security_id": security_ids[group],
                            "identifier_namespace": namespace,
                            "identifier_value": value,
                            "venue_id": venue_id,
                        }
                    )

    for exchange, venue in sorted(venue_seed.items()):
        exchange_members = [member for member in members if member.key[0] == exchange]
        if not exchange_members and exchange != "BSE":
            raise ListedCompanyBuildError(
                f"security master has no rows for primary venue {exchange}"
            )
        if not exchange_members:
            quality_exceptions.append(
                {
                    "code": "BSE_BUSINESS_COVERAGE_EMPTY",
                    "policy": "BSE_PARTIAL_NONBLOCKING",
                    "affected_rows": 0,
                    "resolution": "venue_seed_retained_without_business_coverage_claim",
                }
            )
        rows["dwd_trading_venue"].append(
            {
                "record_source": "ISO_10383_reference_seed",
                "source_record_id": f"{reference_seed_path.name}#exchange_code={exchange}",
                "source_revision": seed_revision,
                "source_artifact_sha256": seed_sha,
                "knowledge_from": venue_knowledge_from[exchange],
                "knowledge_to": None,
                "effective_from": min(
                    (member.effective_from for member in exchange_members),
                    default=knowledge_cutoff,
                ),
                "effective_to": None,
                "venue_id": venue_ids[exchange],
                "mic_code": str(venue["mic_code"]),
                "venue_name": str(venue["venue_name"]),
                "country_code": str(venue["country_code"]),
                "timezone": str(venue["timezone"]),
            }
        )

    for table, table_rows in rows.items():
        table_rows.sort(key=lambda row: _row_key(table, row))
    _validate_rows(rows)
    listed_counts = {
        exchange: sum(
            row["exchange"] == exchange and row["status"] == "listed" for row in master_rows
        )
        for exchange in ("SSE", "SZSE", "BSE")
    }
    expected_primary = int(
        _mapping(semantic_plan.get("source_precedence"), "source precedence").get(
            "expected_sse_szse_listed_scope_after_overlay", -1
        )
    )
    primary_count = listed_counts["SSE"] + listed_counts["SZSE"]
    if primary_count != expected_primary:
        raise ListedCompanyBuildError(
            f"SSE/SZSE listed coverage differs from the plan: {primary_count} != {expected_primary}"
        )
    coverage_policy = _mapping(seed.get("coverage_policy"), "coverage policy")
    if coverage_policy.get("BSE") != "BSE_PARTIAL_NONBLOCKING":
        raise ListedCompanyBuildError("BSE partial coverage policy is not explicit")

    table_digests = {table: _canonical_digest(values) for table, values in rows.items()}
    row_counts = {table: len(values) for table, values in rows.items()}
    semantic_digest = _canonical_digest(
        {
            "input_plan_digest": plan["plan_digest"],
            "model_sha256": model_sha,
            "reference_seed_sha256": seed_sha,
            "code_validity_sha256": code_validity_sha,
            "table_digests": table_digests,
        }
    )
    if output.is_symlink():
        raise ListedCompanyBuildError("candidate output path must not be a symbolic link")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        _verify_existing_output(output, run_id, semantic_digest, row_counts)
        return ListedCompanyBuildResult(
            root=output,
            semantic_digest=semantic_digest,
            row_counts=row_counts,
            primary_listed_count=primary_count,
            bse_listed_count=listed_counts["BSE"],
        )

    work = _work_directory(output, run_id, semantic_digest)
    checkpoint_identity = {
        "schema_version": 1,
        "run_id": run_id,
        "semantic_digest": semantic_digest,
        "completed_tables": {},
    }
    checkpoint_path = work / "checkpoint.json"
    if work.exists():
        if checkpoint_path.is_file() and not checkpoint_path.is_symlink():
            checkpoint = _object(checkpoint_path)
            if any(
                checkpoint.get(key) != checkpoint_identity[key]
                for key in ("run_id", "semantic_digest")
            ):
                raise ListedCompanyBuildError(f"checkpoint belongs to another build: {work}")
            _remove_orphaned_temporaries(work)
        elif checkpoint_path.exists():
            raise ListedCompanyBuildError("checkpoint must be a physical file")
        else:
            _initialize_unowned_work(work, checkpoint_path, checkpoint_identity)
    else:
        _initialize_unowned_work(work, checkpoint_path, checkpoint_identity)

    checkpoint = _object(checkpoint_path)
    if any(
        checkpoint.get(key) != checkpoint_identity[key] for key in ("run_id", "semantic_digest")
    ):
        raise ListedCompanyBuildError(f"checkpoint belongs to another build: {work}")
    completed = dict(_mapping(checkpoint.get("completed_tables"), "completed tables"))
    schemas = _table_schemas()
    for table in _OWNED_TABLES:
        relative = f"payload/listed_company/{table}.parquet"
        target = work / relative
        expected = {
            "row_count": row_counts[table],
            "schema_digest": _canonical_digest(str(schemas[table])),
            "semantic_digest": table_digests[table],
        }
        prior = completed.get(table)
        if prior is not None:
            prior_mapping = _mapping(prior, f"checkpoint {table}")
            if any(prior_mapping.get(key) != value for key, value in expected.items()):
                raise ListedCompanyBuildError(f"checkpoint table metadata differs: {table}")
            if not target.is_file() or _sha256(target) != prior_mapping.get("sha256"):
                raise ListedCompanyBuildError(f"checkpoint table file failed verification: {table}")
            continue
        table_value = pa.Table.from_pylist(rows[table], schema=schemas[table])
        temporary = target.with_suffix(".parquet.partial")
        pq.write_table(
            table_value,
            temporary,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        completed[table] = {**expected, "path": relative, "sha256": _sha256(target)}
        checkpoint["completed_tables"] = completed
        _write_json_atomic(checkpoint_path, checkpoint)

    reconciliation_value = {
        "schema_version": 1,
        "input_plan_digest": plan["plan_digest"],
        "sources": reconciliation,
        "summary": {
            "source_file_count": len(reconciliation),
            "source_row_count": sum(int(item["source_rows"]) for item in reconciliation),
            "unmatched_rows": sum(int(item.get("unmatched_rows", 0)) for item in reconciliation),
        },
    }
    _write_json_atomic(
        work / "reconciliation" / "source-row-reconciliation.json", reconciliation_value
    )
    receipt = {
        "schema_version": 1,
        "kind": "dwd_listed_company_build_receipt",
        "run_id": run_id,
        "status": "built",
        "input_plan_digest": plan["plan_digest"],
        "model_sha256": model_sha,
        "reference_seed_sha256": seed_sha,
        "code_validity_sha256": code_validity_sha,
        "semantic_digest": semantic_digest,
        "table_digests": table_digests,
        "row_counts": row_counts,
        "coverage": {
            "listed_by_exchange": listed_counts,
            "primary_sse_szse_listed": primary_count,
            "bse_policy": coverage_policy["BSE"],
        },
        "quality_exceptions": quality_exceptions,
    }
    _write_json_atomic(work / "receipts" / "listed-company-build.json", receipt)
    files = []
    for relative in sorted(_OUTPUT_FILES):
        path = work / relative
        if path.is_symlink() or not path.is_file():
            raise ListedCompanyBuildError(f"candidate output file is missing: {relative}")
        files.append({"path": relative, "sha256": _sha256(path), "size_bytes": path.stat().st_size})
    manifest = {
        "schema_version": 1,
        "kind": "dwd_listed_company_candidate",
        "run_id": run_id,
        "input_plan_digest": plan["plan_digest"],
        "semantic_digest": semantic_digest,
        "owned_tables": list(_OWNED_TABLES),
        "row_counts": row_counts,
        "files": files,
    }
    _write_json_atomic(work / "manifest.json", manifest)
    _write_text_atomic(work / "READY", f"{_sha256(work / 'manifest.json')}\n")
    _verify_existing_output(work, run_id, semantic_digest, row_counts)
    for relative in sorted(_OUTPUT_DIRECTORIES, key=lambda value: value.count("/"), reverse=True):
        _fsync_directory(work / relative)
    _fsync_directory(work)
    try:
        os.replace(work, output)
    except FileExistsError:
        _verify_existing_output(output, run_id, semantic_digest, row_counts)
    _fsync_directory(output.parent)

    return ListedCompanyBuildResult(
        root=output,
        semantic_digest=semantic_digest,
        row_counts=row_counts,
        primary_listed_count=primary_count,
        bse_listed_count=listed_counts["BSE"],
    )


def build_listed_company(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    reference_seed_path: Path,
    code_validity_path: Path,
    output: Path,
    run_id: str,
) -> ListedCompanyBuildResult:
    """Build six deterministic listed-company DWD objects from one sealed Base plan."""

    if output.is_symlink():
        raise ListedCompanyBuildError("candidate output path must not be a symbolic link")
    normalized_output = output.resolve()
    normalized_output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = normalized_output.parent / f".{normalized_output.name}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o640)
    except OSError as exc:
        raise ListedCompanyBuildError(f"cannot open build lock: {lock_path}") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ListedCompanyBuildError("build lock must be a physical file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        return _build_listed_company_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            reference_seed_path=reference_seed_path,
            code_validity_path=code_validity_path,
            output=normalized_output,
            run_id=run_id,
        )
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
