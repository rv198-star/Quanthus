"""Immutable Hermes-authoritative ODS Base packages and mirror publication."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class OdsBaseError(RuntimeError):
    """Raised when an ODS Base source, package, or publication is invalid."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELEASE_ATTEMPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXCLUDED_RELEASE_ARTIFACTS = frozenset({"acquisition_log", "lane_status"})


@dataclass(frozen=True)
class OdsFileSpec:
    dataset_id: str
    source: str
    target: str
    role: str
    file_format: str
    key_columns: tuple[str, ...]
    date_columns: tuple[str, ...]


@dataclass(frozen=True)
class OdsTreeSpec:
    dataset_id: str
    source: str
    target: str
    role: str
    pattern: str
    minimum_parts: int
    key_columns: tuple[str, ...]
    date_columns: tuple[str, ...]


@dataclass(frozen=True)
class OdsArchiveSpec:
    dataset_id: str
    source: str
    target: str
    role: str
    include_patterns: tuple[str, ...]
    minimum_files: int


@dataclass(frozen=True)
class OdsDailySpec:
    source: str
    increment_start: date
    market_required_artifacts: tuple[str, ...]
    event_required_artifacts: tuple[str, ...]


@dataclass(frozen=True)
class OdsIntradayException:
    market_date: date
    source_node: str
    source: str
    reason: str
    manifest_sha256: str


@dataclass(frozen=True)
class OdsIntradaySpec:
    frequency: str
    source: str
    increment_start: date
    required_artifacts: tuple[str, ...]
    source_exceptions: tuple[OdsIntradayException, ...]


@dataclass(frozen=True)
class OdsExclusion:
    pattern: str
    classification: str
    reason: str


@dataclass(frozen=True)
class OdsBasePolicy:
    contract_id: str
    source_node: str
    mirror_nodes: tuple[str, ...]
    oci_ods_enabled: bool
    files: tuple[OdsFileSpec, ...]
    trees: tuple[OdsTreeSpec, ...]
    archives: tuple[OdsArchiveSpec, ...]
    daily: OdsDailySpec
    intraday: tuple[OdsIntradaySpec, ...]
    exclusions: tuple[OdsExclusion, ...]
    path: Path
    digest: str


@dataclass(frozen=True)
class OdsBaseBundle:
    root: Path
    bundle_id: str
    manifest_sha256: str
    content_digest: str
    through_date: date
    increment_start: date
    source_node: str
    size_bytes: int
    file_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsBaseError(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise OdsBaseError(f"JSON value must be an object: {path}")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise OdsBaseError(f"{key} must be a non-empty string")
    return item


def _parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise OdsBaseError(f"{field} must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OdsBaseError(f"{field} must use YYYY-MM-DD") from exc


def _safe_relative(value: str, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise OdsBaseError(f"{field} must be a safe relative path")
    return path


def _inside(root: Path, value: str, field: str) -> Path:
    root = root.resolve()
    relative = _safe_relative(value, field)
    result = root.joinpath(*relative.parts).resolve()
    if result != root and root not in result.parents:
        raise OdsBaseError(f"{field} escapes its package root")
    return result


def _string_list(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise OdsBaseError(f"{field} must be a non-empty array")
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise OdsBaseError(f"{field} contains an invalid string")
    if len(set(result)) != len(result):
        raise OdsBaseError(f"{field} contains duplicates")
    return result


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o640)
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def load_ods_base_policy(path: Path) -> OdsBasePolicy:
    path = path.resolve()
    value = _object(path)
    authority = value.get("authority")
    source = value.get("source")
    retention = value.get("retention")
    if value.get("schema_version") != 1 or not all(
        isinstance(item, Mapping) for item in (authority, source, retention)
    ):
        raise OdsBaseError("unsupported ODS Base policy")
    assert isinstance(authority, Mapping)
    assert isinstance(source, Mapping)
    assert isinstance(retention, Mapping)
    mirror_nodes = _string_list(authority.get("mirror_nodes"), "authority.mirror_nodes")
    if (
        authority.get("source_node") != "hermes"
        or mirror_nodes != ("hermes", "cn01")
        or authority.get("oci_ods_enabled") is not False
        or retention.get("migration_mode") != "copy_only"
        or retention.get("delete_enabled") is not False
        or retention.get("minimum_identical_copies") != 2
    ):
        raise OdsBaseError("ODS Base authority or retention invariants are invalid")

    file_values = source.get("files")
    tree_values = source.get("trees")
    archive_values = source.get("archive_trees")
    daily_value = source.get("daily")
    intraday_values = source.get("intraday_releases")
    exclusion_values = source.get("exclusions")
    if (
        not isinstance(file_values, list)
        or not file_values
        or not isinstance(tree_values, list)
        or not tree_values
        or not isinstance(archive_values, list)
        or not archive_values
        or not isinstance(daily_value, Mapping)
        or not isinstance(intraday_values, list)
        or not intraday_values
        or not isinstance(exclusion_values, list)
        or not exclusion_values
    ):
        raise OdsBaseError("ODS Base source policy is incomplete")

    files: list[OdsFileSpec] = []
    for item in file_values:
        if not isinstance(item, Mapping):
            raise OdsBaseError("source.files entries must be objects")
        file_format = _text(item, "format")
        if file_format not in {"json", "parquet", "sqlite"}:
            raise OdsBaseError("source file format is unsupported")
        files.append(
            OdsFileSpec(
                dataset_id=_text(item, "dataset_id"),
                source=_safe_relative(_text(item, "source"), "files.source").as_posix(),
                target=_safe_relative(_text(item, "target"), "files.target").as_posix(),
                role=_text(item, "role"),
                file_format=file_format,
                key_columns=_string_list(
                    item.get("key_columns", []), "files.key_columns", allow_empty=True
                ),
                date_columns=_string_list(
                    item.get("date_columns", []), "files.date_columns", allow_empty=True
                ),
            )
        )

    archives: list[OdsArchiveSpec] = []
    for item in archive_values:
        if not isinstance(item, Mapping):
            raise OdsBaseError("source.archive_trees entries must be objects")
        minimum_files = item.get("minimum_files")
        if (
            not isinstance(minimum_files, int)
            or isinstance(minimum_files, bool)
            or minimum_files < 1
        ):
            raise OdsBaseError("archive_trees.minimum_files must be positive")
        patterns = _string_list(
            item.get("include_patterns"), "archive_trees.include_patterns"
        )
        for pattern in patterns:
            _safe_relative(pattern, "archive_trees.include_patterns")
        archives.append(
            OdsArchiveSpec(
                dataset_id=_text(item, "dataset_id"),
                source=_safe_relative(
                    _text(item, "source"), "archive_trees.source"
                ).as_posix(),
                target=_safe_relative(
                    _text(item, "target"), "archive_trees.target"
                ).as_posix(),
                role=_text(item, "role"),
                include_patterns=patterns,
                minimum_files=minimum_files,
            )
        )

    trees: list[OdsTreeSpec] = []
    for item in tree_values:
        if not isinstance(item, Mapping):
            raise OdsBaseError("source.trees entries must be objects")
        minimum_parts = item.get("minimum_parts")
        if (
            not isinstance(minimum_parts, int)
            or isinstance(minimum_parts, bool)
            or minimum_parts < 1
        ):
            raise OdsBaseError("trees.minimum_parts must be positive")
        pattern = _text(item, "pattern")
        if "/" in pattern or pattern in {"*", "**"}:
            raise OdsBaseError("trees.pattern must be a bounded filename pattern")
        trees.append(
            OdsTreeSpec(
                dataset_id=_text(item, "dataset_id"),
                source=_safe_relative(_text(item, "source"), "trees.source").as_posix(),
                target=_safe_relative(_text(item, "target"), "trees.target").as_posix(),
                role=_text(item, "role"),
                pattern=pattern,
                minimum_parts=minimum_parts,
                key_columns=_string_list(item.get("key_columns"), "trees.key_columns"),
                date_columns=_string_list(item.get("date_columns"), "trees.date_columns"),
            )
        )

    exclusions: list[OdsExclusion] = []
    for item in exclusion_values:
        if not isinstance(item, Mapping):
            raise OdsBaseError("source.exclusions entries must be objects")
        pattern = _safe_relative(_text(item, "pattern"), "exclusions.pattern").as_posix()
        exclusions.append(
            OdsExclusion(
                pattern=pattern,
                classification=_text(item, "classification"),
                reason=_text(item, "reason"),
            )
        )

    daily = OdsDailySpec(
        source=_safe_relative(_text(daily_value, "source"), "daily.source").as_posix(),
        increment_start=_parse_date(daily_value.get("increment_start"), "daily.increment_start"),
        market_required_artifacts=_string_list(
            daily_value.get("market_required_artifacts"), "daily.market_required_artifacts"
        ),
        event_required_artifacts=_string_list(
            daily_value.get("event_required_artifacts"), "daily.event_required_artifacts"
        ),
    )
    intraday: list[OdsIntradaySpec] = []
    for item in intraday_values:
        if not isinstance(item, Mapping):
            raise OdsBaseError("source.intraday_releases entries must be objects")
        frequency = _text(item, "frequency")
        if frequency not in {"1m", "5m"}:
            raise OdsBaseError("intraday release frequency must be 1m or 5m")
        exception_values = item.get("source_exceptions", [])
        if not isinstance(exception_values, list):
            raise OdsBaseError("intraday source_exceptions must be an array")
        exceptions: list[OdsIntradayException] = []
        for exception in exception_values:
            if not isinstance(exception, Mapping):
                raise OdsBaseError("intraday source exception must be an object")
            market_date = _parse_date(
                exception.get("market_date"), "source_exceptions.market_date"
            )
            source_node = _text(exception, "source_node")
            manifest_sha256 = _text(exception, "manifest_sha256")
            if source_node != "cn01" or _SHA256.fullmatch(manifest_sha256) is None:
                raise OdsBaseError("intraday source exception authority is invalid")
            exceptions.append(
                OdsIntradayException(
                    market_date=market_date,
                    source_node=source_node,
                    source=_safe_relative(
                        _text(exception, "source"), "source_exceptions.source"
                    ).as_posix(),
                    reason=_text(exception, "reason"),
                    manifest_sha256=manifest_sha256,
                )
            )
        if len({exception.market_date for exception in exceptions}) != len(exceptions):
            raise OdsBaseError("intraday source exception dates must be unique")
        intraday.append(
            OdsIntradaySpec(
                frequency=frequency,
                source=_safe_relative(
                    _text(item, "source"), "intraday_releases.source"
                ).as_posix(),
                increment_start=_parse_date(
                    item.get("increment_start"), "intraday_releases.increment_start"
                ),
                required_artifacts=_string_list(
                    item.get("required_artifacts"),
                    "intraday_releases.required_artifacts",
                ),
                source_exceptions=tuple(exceptions),
            )
        )
    if tuple(sorted(item.frequency for item in intraday)) != ("1m", "5m"):
        raise OdsBaseError("intraday release policy must define exactly 1m and 5m")
    if any(
        exception.market_date < item.increment_start
        for item in intraday
        for exception in item.source_exceptions
    ):
        raise OdsBaseError("intraday source exception predates its increment start")

    dataset_ids = (
        [item.dataset_id for item in files]
        + [item.dataset_id for item in trees]
        + [item.dataset_id for item in archives]
    )
    targets = (
        [item.target for item in files]
        + [item.target for item in trees]
        + [item.target for item in archives]
    )
    sources = (
        [item.source for item in files]
        + [item.source for item in trees]
        + [item.source for item in archives]
    )
    if (
        len(set(dataset_ids)) != len(dataset_ids)
        or len(set(targets)) != len(targets)
        or len(set(sources)) != len(sources)
        or any(target == "payload" or not target.startswith("payload/") for target in targets)
    ):
        raise OdsBaseError("ODS Base dataset ids, sources, or targets are ambiguous")
    return OdsBasePolicy(
        contract_id=_text(value, "contract_id"),
        source_node="hermes",
        mirror_nodes=mirror_nodes,
        oci_ods_enabled=False,
        files=tuple(files),
        trees=tuple(trees),
        archives=tuple(archives),
        daily=daily,
        intraday=tuple(intraday),
        exclusions=tuple(exclusions),
        path=path,
        digest=_sha256(path),
    )


def _classify_source_root(astock_root: Path, policy: OdsBasePolicy) -> list[dict[str, str]]:
    included = (
        {item.source for item in policy.files}
        | {item.source for item in policy.trees}
        | {item.source for item in policy.archives}
        | {item.source for item in policy.intraday}
    )
    included.add(policy.daily.source)
    included.update(
        exception.source
        for item in policy.intraday
        for exception in item.source_exceptions
    )
    inventory: list[dict[str, str]] = []
    unresolved: list[str] = []
    for root_name in ("data", "minute/data"):
        root = astock_root / root_name
        if not root.is_dir() or root.is_symlink():
            raise OdsBaseError(f"required source inventory root is invalid: {root_name}")
        for child in sorted(root.iterdir()):
            relative = child.relative_to(astock_root).as_posix()
            if child.is_symlink():
                raise OdsBaseError(f"symbolic links are forbidden in ODS sources: {relative}")
            direct = relative in included
            ancestor = any(item.startswith(relative + "/") for item in included)
            exclusions = [
                item for item in policy.exclusions if fnmatch.fnmatchcase(relative, item.pattern)
            ]
            if direct:
                classification = "included"
                reason = "explicit policy member"
            elif ancestor:
                classification = "mixed"
                reason = "only explicit policy descendants are included"
            elif exclusions:
                classification = exclusions[0].classification
                reason = exclusions[0].reason
            else:
                unresolved.append(relative)
                continue
            inventory.append(
                {
                    "path": relative,
                    "classification": classification,
                    "reason": reason,
                }
            )
    if unresolved:
        raise OdsBaseError(f"unclassified top-level ODS sources: {', '.join(unresolved)}")
    return inventory


def _copy_file(source: Path, target: Path) -> tuple[str, int]:
    if not source.is_file() or source.is_symlink():
        raise OdsBaseError(f"source member is missing, unreadable, or a symlink: {source}")
    before = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        target.unlink(missing_ok=True)
        raise OdsBaseError(f"source changed while copied: {source}")
    target.chmod(0o640)
    return f"sha256:{digest.hexdigest()}", before.st_size


def _parent_parts(parent: OdsBaseBundle | None) -> dict[str, tuple[Path, str, int]]:
    if parent is None:
        return {}
    manifest = _object(parent.root / "manifest.json")
    content = manifest.get("content")
    files = content.get("files") if isinstance(content, Mapping) else None
    if not isinstance(files, list):
        raise OdsBaseError("parent ODS Base has no reusable file inventory")
    result: dict[str, tuple[Path, str, int]] = {}
    for item in files:
        if not isinstance(item, Mapping):
            raise OdsBaseError("parent ODS Base file inventory is invalid")
        source = _text(item, "source_path")
        path = _inside(parent.root, _text(item, "path"), "parent files.path")
        digest = _text(item, "sha256")
        size = item.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or source in result:
            raise OdsBaseError("parent ODS Base source inventory is ambiguous")
        result[source] = (path, digest, size)
    return result


def _copy_or_reuse_file(
    *,
    source: Path,
    source_relative: str,
    target: Path,
    reusable: Mapping[str, tuple[Path, str, int]],
) -> tuple[str, int, bool]:
    prior = reusable.get(source_relative)
    if prior is not None and source.is_file() and not source.is_symlink():
        prior_path, prior_digest, prior_size = prior
        stat = source.stat()
        if stat.st_size == prior_size and _sha256(source) == prior_digest:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(prior_path, target)
            except OSError:
                pass
            else:
                return prior_digest, prior_size, True
    digest, size = _copy_file(source, target)
    return digest, size, False


def _json_scalar(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _parquet_metadata(path: Path, date_columns: Sequence[str]) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise OdsBaseError("pyarrow is required to build or audit an ODS Base") from exc
    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow
    schema_value = [{"name": field.name, "type": str(field.type)} for field in schema]
    null_counts: dict[str, int | None] = {}
    bounds: dict[str, dict[str, object | None]] = {}
    parquet_names = parquet.schema.names
    for name in schema.names:
        index = parquet_names.index(name) if name in parquet_names else -1
        total: int | None = 0
        if index >= 0:
            for row_group in range(parquet.metadata.num_row_groups):
                stats = parquet.metadata.row_group(row_group).column(index).statistics
                if stats is None or stats.null_count is None:
                    total = None
                    break
                assert total is not None
                total += int(stats.null_count)
        else:
            total = None
        null_counts[name] = total
    for name in date_columns:
        if name not in parquet_names:
            raise OdsBaseError(f"declared date column {name} is missing from {path}")
        index = parquet_names.index(name)
        minimum: object | None = None
        maximum: object | None = None
        for row_group in range(parquet.metadata.num_row_groups):
            stats = parquet.metadata.row_group(row_group).column(index).statistics
            if stats is None or not stats.has_min_max:
                minimum = None
                maximum = None
                break
            candidate_min = _json_scalar(stats.min)
            candidate_max = _json_scalar(stats.max)
            if minimum is None or str(candidate_min) < str(minimum):
                minimum = candidate_min
            if maximum is None or str(candidate_max) > str(maximum):
                maximum = candidate_max
        bounds[name] = {"min": minimum, "max": maximum}
    return {
        "row_count": parquet.metadata.num_rows,
        "schema": schema_value,
        "schema_sha256": _canonical_digest(schema_value),
        "null_counts": null_counts,
        "date_bounds": bounds,
    }


def _sqlite_metadata(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise OdsBaseError("SQLite ODS source failed integrity_check")
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables: list[dict[str, Any]] = []
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [
                {"name": str(row[1]), "type": str(row[2]), "primary_key": bool(row[5])}
                for row in connection.execute(f"PRAGMA table_info({quoted})")
            ]
            tables.append(
                {
                    "name": name,
                    "row_count": int(
                        connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
                    ),
                    "schema": columns,
                    "schema_sha256": _canonical_digest(columns),
                }
            )
    finally:
        connection.close()
    return {"integrity_check": "ok", "tables": tables}


def _part_metadata(
    *,
    source_relative: str,
    target_relative: str,
    dataset_id: str,
    role: str,
    file_format: str,
    digest: str,
    size_bytes: int,
    target: Path,
    date_columns: Sequence[str] = (),
    knowledge_cutoff: str | None = None,
    source_node: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "role": role,
        "format": file_format,
        "source_path": source_relative,
        "path": target_relative,
        "sha256": digest,
        "size_bytes": size_bytes,
    }
    if knowledge_cutoff is not None:
        result["knowledge_cutoff"] = knowledge_cutoff
    if source_node is not None:
        result["source_node"] = source_node
    if file_format == "parquet":
        result.update(_parquet_metadata(target, date_columns))
    elif file_format == "sqlite":
        result.update(_sqlite_metadata(target))
    return result


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parquet_quality(
    paths: Sequence[Path], key_columns: Sequence[str], date_columns: Sequence[str]
) -> dict[str, Any]:
    if not paths:
        raise OdsBaseError("cannot audit an empty Parquet dataset")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise OdsBaseError("duckdb is required for ODS Base key and date audits") from exc
    sources = "[" + ",".join(_sql_string(str(path)) for path in paths) + "]"
    relation = f"read_parquet({sources}, union_by_name=true)"
    keys = ", ".join(_sql_identifier(item) for item in key_columns)
    distinct = (
        f"count(DISTINCT {_sql_identifier(key_columns[0])})"
        if len(key_columns) == 1
        else f"count(DISTINCT ({keys}))"
    )
    key_null = " OR ".join(f"{_sql_identifier(item)} IS NULL" for item in key_columns)
    selects = [
        "count(*)",
        f"count(*) - {distinct}",
        f"count(*) FILTER (WHERE {key_null})",
    ]
    for item in date_columns:
        quoted = _sql_identifier(item)
        selects.extend((f"min({quoted})", f"max({quoted})"))
    connection = duckdb.connect(":memory:")
    try:
        row = connection.execute(f"SELECT {', '.join(selects)} FROM {relation}").fetchone()
    finally:
        connection.close()
    if row is None:
        raise OdsBaseError("Parquet quality query returned no result")
    bounds = {
        name: {"min": _json_scalar(row[3 + index * 2]), "max": _json_scalar(row[4 + index * 2])}
        for index, name in enumerate(date_columns)
    }
    return {
        "row_count": int(row[0]),
        "duplicate_key_count": int(row[1]),
        "null_key_count": int(row[2]),
        "date_bounds": bounds,
    }


def _ready_release(root: Path, market_date: date) -> dict[str, Any]:
    if root.is_symlink() or _RELEASE_ATTEMPT.fullmatch(root.name) is None:
        raise OdsBaseError(f"daily ODS attempt path is unsafe: {root}")
    release_path = root / "release.json"
    ready_path = root / "READY"
    value = _object(release_path)
    release_digest = _sha256(release_path)
    try:
        ready_value = ready_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise OdsBaseError(f"daily ODS attempt has no READY: {root}") from exc
    if ready_value != release_digest:
        raise OdsBaseError(f"daily ODS READY differs from release.json: {root}")
    if (
        value.get("schema_version") != 1
        or value.get("schedule") != "daily"
        or value.get("status") != "ready"
        or value.get("market_date") != market_date.isoformat()
    ):
        raise OdsBaseError(f"daily ODS release envelope is invalid: {root}")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise OdsBaseError(f"daily ODS release has no artifacts: {root}")
    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise OdsBaseError("daily ODS artifact must be an object")
        name = _text(artifact, "name")
        relative = _safe_relative(_text(artifact, "relative_path"), "artifact.relative_path")
        digest = _text(artifact, "sha256")
        size = artifact.get("size_bytes")
        cutoff = _text(artifact, "knowledge_cutoff")
        if (
            name in names
            or _SHA256.fullmatch(digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise OdsBaseError(f"daily ODS artifact metadata is invalid: {root}")
        names.add(name)
        path = _inside(root, relative.as_posix(), "artifact.relative_path")
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != size
            or _sha256(path) != digest
        ):
            raise OdsBaseError(f"daily ODS artifact failed integrity: {path}")
        normalized.append(
            {
                "name": name,
                "relative_path": relative.as_posix(),
                "sha256": digest,
                "size_bytes": size,
                "knowledge_cutoff": cutoff,
                "source_revision": _text(artifact, "source_revision"),
            }
        )
    return {
        "root": root,
        "attempt_id": root.name,
        "release_id": _text(value, "release_id"),
        "created_at": _text(value, "created_at"),
        "producer": _text(value, "producer"),
        "manifest_sha256": release_digest,
        "artifacts": normalized,
        "artifact_names": names,
    }


def _open_dates(calendar_path: Path, start: date, end: date) -> tuple[date, ...]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise OdsBaseError("pyarrow is required to read the ODS trading calendar") from exc
    table = pq.read_table(calendar_path, columns=["trade_date", "is_open"])
    values = table.to_pylist()
    result = sorted(
        {
            row["trade_date"]
            for row in values
            if row["is_open"] == 1 and start <= row["trade_date"] <= end
        }
    )
    return tuple(result)


def _trading_calendar_path(astock_root: Path, policy: OdsBasePolicy) -> Path:
    matches = [
        item
        for item in policy.files
        if item.dataset_id == "trading_calendar" and item.file_format == "parquet"
    ]
    if len(matches) != 1:
        raise OdsBaseError("policy must define one Parquet trading_calendar dataset")
    return _inside(astock_root, matches[0].source, "trading_calendar.source")


def _selected_daily_releases(
    astock_root: Path, policy: OdsBasePolicy, through_date: date
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    daily_root = _inside(astock_root, policy.daily.source, "daily.source")
    dates = _open_dates(
        _trading_calendar_path(astock_root, policy),
        policy.daily.increment_start,
        through_date,
    )
    if not dates or dates[-1] != through_date:
        raise OdsBaseError("through_date must be an open session present in the source calendar")
    selected: list[dict[str, Any]] = []
    attempt_inventory: list[dict[str, str]] = []
    for market_date in dates:
        date_root = daily_root / market_date.isoformat()
        pointer = _object(date_root / "CURRENT.json")
        attempt_id = _text(pointer, "attempt_id")
        if _RELEASE_ATTEMPT.fullmatch(attempt_id) is None:
            raise OdsBaseError("daily ODS CURRENT attempt id is unsafe")
        market = _ready_release(date_root / attempt_id, market_date)
        if (
            pointer.get("market_date") != market_date.isoformat()
            or pointer.get("schedule") != "daily"
            or pointer.get("manifest_sha256") != market["manifest_sha256"]
            or not set(policy.daily.market_required_artifacts).issubset(market["artifact_names"])
        ):
            raise OdsBaseError(f"daily market CURRENT is incomplete or inconsistent: {market_date}")
        selected.append(
            {
                **market,
                "release_role": "market",
                "source_node": "hermes",
                "source_path": date_root.relative_to(astock_root).as_posix(),
                "gap_fill": None,
            }
        )

        event_candidates: list[dict[str, Any]] = []
        for attempt_root in sorted(date_root.iterdir()):
            if not attempt_root.is_dir() or attempt_root.name == attempt_id:
                continue
            try:
                candidate = _ready_release(attempt_root, market_date)
            except OdsBaseError:
                attempt_inventory.append(
                    {
                        "path": attempt_root.relative_to(astock_root).as_posix(),
                        "classification": "excluded_failed_or_incomplete_attempt",
                        "reason": "attempt did not pass immutable READY and artifact verification",
                    }
                )
                continue
            if set(policy.daily.event_required_artifacts).issubset(candidate["artifact_names"]):
                event_candidates.append(candidate)
            else:
                attempt_inventory.append(
                    {
                        "path": attempt_root.relative_to(astock_root).as_posix(),
                        "classification": "excluded_superseded_market_attempt",
                        "reason": "market authority is the date-local CURRENT descriptor",
                    }
                )
        if not event_candidates:
            raise OdsBaseError(f"no complete daily event release for {market_date}")
        event_candidates.sort(key=lambda item: (item["created_at"], item["attempt_id"]))
        event = event_candidates[-1]
        selected.append(
            {
                **event,
                "release_role": "events",
                "source_node": "hermes",
                "source_path": date_root.relative_to(astock_root).as_posix(),
                "gap_fill": None,
            }
        )
        for candidate in event_candidates[:-1]:
            attempt_inventory.append(
                {
                    "path": candidate["root"].relative_to(astock_root).as_posix(),
                    "classification": "excluded_superseded_event_attempt",
                    "reason": "latest complete READY event release wins under policy",
                }
            )
        attempt_inventory.extend(
            {
                "path": item["root"].relative_to(astock_root).as_posix(),
                "classification": "included_selected_daily_release",
                "reason": f"selected {item['release_role']} release",
            }
            for item in (selected[-2], selected[-1])
        )
    return selected, attempt_inventory


def _selected_intraday_releases(
    astock_root: Path, policy: OdsBasePolicy, through_date: date
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    selected: list[dict[str, Any]] = []
    attempt_inventory: list[dict[str, str]] = []
    calendar_path = _trading_calendar_path(astock_root, policy)
    for spec in policy.intraday:
        release_root = _inside(astock_root, spec.source, "intraday_releases.source")
        exceptions = {item.market_date: item for item in spec.source_exceptions}
        dates = _open_dates(calendar_path, spec.increment_start, through_date)
        if not dates or dates[-1] != through_date:
            raise OdsBaseError(
                f"intraday {spec.frequency} through_date is absent from the source calendar"
            )
        for market_date in dates:
            exception = exceptions.get(market_date)
            date_root = (
                release_root / market_date.isoformat()
                if exception is None
                else _inside(astock_root, exception.source, "source_exceptions.source")
            )
            if not date_root.is_dir() or date_root.is_symlink():
                raise OdsBaseError(
                    f"intraday {spec.frequency} has no release root for {market_date}"
                )
            candidates: list[dict[str, Any]] = []
            for attempt_root in sorted(date_root.iterdir()):
                if not attempt_root.is_dir():
                    continue
                try:
                    candidate = _ready_release(attempt_root, market_date)
                except OdsBaseError:
                    attempt_inventory.append(
                        {
                            "path": attempt_root.relative_to(astock_root).as_posix(),
                            "classification": "excluded_failed_or_incomplete_intraday_attempt",
                            "reason": "attempt did not pass READY and artifact verification",
                        }
                    )
                    continue
                if set(spec.required_artifacts).issubset(candidate["artifact_names"]):
                    candidates.append(candidate)
                else:
                    attempt_inventory.append(
                        {
                            "path": attempt_root.relative_to(astock_root).as_posix(),
                            "classification": "excluded_incomplete_intraday_release",
                            "reason": f"missing required {spec.frequency} artifacts",
                        }
                    )
            if not candidates:
                raise OdsBaseError(
                    f"no complete intraday {spec.frequency} release for {market_date}"
                )
            candidates.sort(key=lambda item: (item["created_at"], item["attempt_id"]))
            if (
                exception is not None
                and candidates[-1]["manifest_sha256"] != exception.manifest_sha256
            ):
                raise OdsBaseError(
                    f"intraday {spec.frequency} gap-fill digest differs for {market_date}"
                )
            winner = {
                **candidates[-1],
                "release_role": f"intraday_{spec.frequency}",
                "source_node": "hermes" if exception is None else exception.source_node,
                "source_path": date_root.relative_to(astock_root).as_posix(),
                "gap_fill": (
                    None
                    if exception is None
                    else {
                        "reason": exception.reason,
                        "expected_manifest_sha256": exception.manifest_sha256,
                    }
                ),
            }
            selected.append(winner)
            attempt_inventory.append(
                {
                    "path": winner["root"].relative_to(astock_root).as_posix(),
                    "classification": "included_selected_intraday_release",
                    "reason": (
                        f"latest complete READY {spec.frequency} release"
                        if exception is None
                        else f"explicit CN01 gap-fill for {spec.frequency}"
                    ),
                }
            )
            for candidate in candidates[:-1]:
                attempt_inventory.append(
                    {
                        "path": candidate["root"].relative_to(astock_root).as_posix(),
                        "classification": "excluded_superseded_intraday_attempt",
                        "reason": f"superseded by latest complete {spec.frequency} release",
                    }
                )
    return selected, attempt_inventory


def _selected_source_state(
    astock_root: Path, policy: OdsBasePolicy, through_date: date
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    source_inventory = _classify_source_root(astock_root, policy)
    releases, attempt_inventory = _selected_daily_releases(
        astock_root, policy, through_date
    )
    intraday_releases, intraday_inventory = _selected_intraday_releases(
        astock_root, policy, through_date
    )
    releases.extend(intraday_releases)
    source_inventory.extend(attempt_inventory)
    source_inventory.extend(intraday_inventory)
    source_inventory.sort(key=lambda item: item["path"])
    return source_inventory, releases


def _tree_paths(astock_root: Path, spec: OdsTreeSpec) -> list[Path]:
    source_root = _inside(astock_root, spec.source, "trees.source")
    if not source_root.is_dir() or source_root.is_symlink():
        raise OdsBaseError(f"ODS Base tree root is invalid: {spec.dataset_id}")
    paths = sorted(source_root.glob(spec.pattern))
    if (
        len(paths) < spec.minimum_parts
        or any(not item.is_file() or item.is_symlink() for item in paths)
    ):
        raise OdsBaseError(f"ODS Base tree is incomplete: {spec.dataset_id}")
    return paths


def _archive_paths(astock_root: Path, spec: OdsArchiveSpec) -> list[Path]:
    source_root = _inside(astock_root, spec.source, "archive_trees.source")
    if not source_root.is_dir() or source_root.is_symlink():
        raise OdsBaseError(f"ODS source evidence archive is invalid: {spec.dataset_id}")
    paths: list[Path] = []
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise OdsBaseError(f"symbolic link in source evidence archive: {source}")
        if not source.is_file():
            continue
        relative_name = source.relative_to(source_root).as_posix()
        if any(
            fnmatch.fnmatchcase(relative_name, pattern)
            for pattern in spec.include_patterns
        ):
            paths.append(source)
    if len(paths) < spec.minimum_files:
        raise OdsBaseError(f"ODS source evidence archive is incomplete: {spec.dataset_id}")
    return paths


def _archive_format(path: Path) -> str:
    return {
        ".csv": "csv",
        ".json": "json",
        ".md": "text",
        ".parquet": "parquet",
        ".sha256": "text",
        ".txt": "text",
    }.get(path.suffix.lower(), "binary")


def _release_market_date(release: Mapping[str, Any]) -> date:
    root = release.get("root")
    if not isinstance(root, Path):
        raise OdsBaseError("selected release root is invalid")
    return _parse_date(root.parent.name, "selected release market_date")


def _release_lineage_entry(release: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, list):
        raise OdsBaseError("selected release artifacts are invalid")
    return {
        "market_date": _release_market_date(release).isoformat(),
        "release_role": str(release["release_role"]),
        "source_node": str(release["source_node"]),
        "source_path": str(release["source_path"]),
        "gap_fill": release["gap_fill"],
        "attempt_id": str(release["attempt_id"]),
        "release_id": str(release["release_id"]),
        "manifest_sha256": str(release["manifest_sha256"]),
        "producer": str(release["producer"]),
        "created_at": str(release["created_at"]),
        "artifacts": [
            {
                **artifact,
                "included_in_base": artifact["name"] not in _EXCLUDED_RELEASE_ARTIFACTS,
            }
            for artifact in artifacts
        ],
    }


def _selection_digest(
    *,
    source_inventory: Sequence[Mapping[str, Any]],
    release_lineage: Sequence[Mapping[str, Any]],
    files: Sequence[Mapping[str, Any]],
) -> str:
    return _canonical_digest(
        {
            "source_inventory": list(source_inventory),
            "daily_releases": list(release_lineage),
            "files": list(files),
        }
    )


def _dataset_summary(
    *,
    dataset_id: str,
    role: str,
    parts: Sequence[dict[str, Any]],
    quality: Mapping[str, Any] | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "dataset_id": dataset_id,
        "role": role,
        "part_count": len(parts),
        "size_bytes": sum(int(item["size_bytes"]) for item in parts),
        "parts": list(parts),
    }
    if quality is not None:
        value["quality"] = dict(quality)
    return value


def audit_ods_base_sources(
    *, astock_root: Path, through_date: date, policy: OdsBasePolicy
) -> dict[str, Any]:
    """Deep-audit the exact source selection without copying package payload."""
    if through_date < policy.daily.increment_start:
        raise OdsBaseError("through_date predates the configured daily increment start")
    astock_root = astock_root.resolve()
    source_inventory, releases = _selected_source_state(
        astock_root, policy, through_date
    )
    datasets: list[dict[str, Any]] = []
    all_parts: list[dict[str, Any]] = []

    for spec in policy.files:
        source = _inside(astock_root, spec.source, "files.source")
        if not source.is_file() or source.is_symlink():
            raise OdsBaseError(f"ODS Base source file is invalid: {spec.dataset_id}")
        part = _part_metadata(
            source_relative=spec.source,
            target_relative=spec.target,
            dataset_id=spec.dataset_id,
            role=spec.role,
            file_format=spec.file_format,
            digest=_sha256(source),
            size_bytes=source.stat().st_size,
            target=source,
            date_columns=spec.date_columns,
        )
        quality = None
        if spec.file_format == "parquet" and spec.key_columns:
            quality = _parquet_quality([source], spec.key_columns, spec.date_columns)
            if quality["duplicate_key_count"] or quality["null_key_count"]:
                raise OdsBaseError(f"ODS Base key gate failed for {spec.dataset_id}")
        datasets.append(
            _dataset_summary(
                dataset_id=spec.dataset_id,
                role=spec.role,
                parts=[part],
                quality=quality,
            )
        )
        all_parts.append(part)

    for spec in policy.trees:
        source_root = _inside(astock_root, spec.source, "trees.source")
        paths = _tree_paths(astock_root, spec)
        parts: list[dict[str, Any]] = []
        for source in paths:
            relative_name = source.relative_to(source_root).as_posix()
            part = _part_metadata(
                source_relative=source.relative_to(astock_root).as_posix(),
                target_relative=f"{spec.target}/{relative_name}",
                dataset_id=spec.dataset_id,
                role=spec.role,
                file_format="parquet",
                digest=_sha256(source),
                size_bytes=source.stat().st_size,
                target=source,
                date_columns=spec.date_columns,
            )
            parts.append(part)
            all_parts.append(part)
        quality = _parquet_quality(paths, spec.key_columns, spec.date_columns)
        if quality["duplicate_key_count"] or quality["null_key_count"]:
            raise OdsBaseError(f"ODS Base key gate failed for {spec.dataset_id}")
        datasets.append(
            _dataset_summary(
                dataset_id=spec.dataset_id,
                role=spec.role,
                parts=parts,
                quality=quality,
            )
        )

    for spec in policy.archives:
        source_root = _inside(astock_root, spec.source, "archive_trees.source")
        parts = []
        parquet_rows = 0
        for source in _archive_paths(astock_root, spec):
            relative_name = source.relative_to(source_root).as_posix()
            file_format = _archive_format(source)
            part = _part_metadata(
                source_relative=source.relative_to(astock_root).as_posix(),
                target_relative=f"{spec.target}/{relative_name}",
                dataset_id=spec.dataset_id,
                role=spec.role,
                file_format=file_format,
                digest=_sha256(source),
                size_bytes=source.stat().st_size,
                target=source,
            )
            if file_format == "parquet":
                parquet_rows += int(part["row_count"])
            parts.append(part)
            all_parts.append(part)
        datasets.append(
            _dataset_summary(
                dataset_id=spec.dataset_id,
                role=spec.role,
                parts=parts,
                quality={
                    "quality_source": "accepted source-evidence archive",
                    "parquet_row_count": parquet_rows,
                },
            )
        )

    daily_parts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    release_lineage: list[dict[str, Any]] = []
    for release in releases:
        release_role = str(release["release_role"])
        market_date = _release_market_date(release)
        release_root = release["root"]
        if not isinstance(release_root, Path):
            raise OdsBaseError("selected release root is invalid")
        base_target = f"payload/daily/{market_date.isoformat()}/{release_role}"
        for name, file_format in (("release.json", "json"), ("READY", "text")):
            source = release_root / name
            part = _part_metadata(
                source_relative=source.relative_to(astock_root).as_posix(),
                target_relative=f"{base_target}/{name}",
                dataset_id="daily_release_metadata",
                role="lineage",
                file_format=file_format,
                digest=_sha256(source),
                size_bytes=source.stat().st_size,
                target=source,
                source_node=str(release["source_node"]),
            )
            daily_parts[("daily_release_metadata", "lineage")].append(part)
            all_parts.append(part)
        for artifact in release["artifacts"]:
            if artifact["name"] in _EXCLUDED_RELEASE_ARTIFACTS:
                continue
            source = _inside(
                release_root, artifact["relative_path"], "daily artifact path"
            )
            file_format = "parquet" if source.suffix.lower() == ".parquet" else "json"
            dataset_id = f"daily_{release_role}_{artifact['name']}"
            part = _part_metadata(
                source_relative=source.relative_to(astock_root).as_posix(),
                target_relative=(
                    f"{base_target}/artifacts/{Path(artifact['relative_path']).name}"
                ),
                dataset_id=dataset_id,
                role="increment",
                file_format=file_format,
                digest=str(artifact["sha256"]),
                size_bytes=int(artifact["size_bytes"]),
                target=source,
                knowledge_cutoff=str(artifact["knowledge_cutoff"]),
                source_node=str(release["source_node"]),
            )
            daily_parts[(dataset_id, "increment")].append(part)
            all_parts.append(part)
        release_lineage.append(_release_lineage_entry(release))
    for (dataset_id, role), parts in sorted(daily_parts.items()):
        datasets.append(
            _dataset_summary(
                dataset_id=dataset_id,
                role=role,
                parts=parts,
                quality={"quality_source": "verified immutable source release"},
            )
        )

    all_parts.sort(key=lambda item: str(item["path"]))
    datasets.sort(key=lambda item: str(item["dataset_id"]))
    release_lineage.sort(key=lambda item: (item["market_date"], item["release_role"]))
    selection_digest = _selection_digest(
        source_inventory=source_inventory,
        release_lineage=release_lineage,
        files=all_parts,
    )
    return {
        "schema_version": 1,
        "kind": "quanthus_ods_base_source_inventory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract_id": policy.contract_id,
        "policy_sha256": policy.digest,
        "source_node": policy.source_node,
        "source_root": str(astock_root),
        "through_date": through_date.isoformat(),
        "increment_start": policy.daily.increment_start.isoformat(),
        "selection_digest": selection_digest,
        "estimated_package": {
            "file_count": len(all_parts),
            "size_bytes": sum(int(item["size_bytes"]) for item in all_parts),
        },
        "gates": {
            "classified_source_inventory": "pass",
            "immutable_release_integrity": "pass",
            "parquet_schema_and_key_quality": "pass",
            "sqlite_integrity": "pass",
        },
        "source_inventory": source_inventory,
        "daily_releases": release_lineage,
        "datasets": datasets,
        "files": all_parts,
    }


def write_ods_base_source_inventory(path: Path, inventory: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(path)
    _atomic_json(path.resolve(), inventory)


def _inventory_selection_digest(
    path: Path, *, policy: OdsBasePolicy, through_date: date
) -> str:
    value = _object(path.resolve())
    digest = _text(value, "selection_digest")
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "quanthus_ods_base_source_inventory"
        or value.get("contract_id") != policy.contract_id
        or value.get("policy_sha256") != policy.digest
        or value.get("source_node") != policy.source_node
        or value.get("through_date") != through_date.isoformat()
        or _SHA256.fullmatch(digest) is None
    ):
        raise OdsBaseError("ODS Base source inventory binding is invalid")
    calculated = _selection_digest(
        source_inventory=value.get("source_inventory", []),
        release_lineage=value.get("daily_releases", []),
        files=value.get("files", []),
    )
    if calculated != digest:
        raise OdsBaseError("ODS Base source inventory selection digest is invalid")
    return digest


def build_ods_base_bundle(
    *,
    astock_root: Path,
    output: Path,
    bundle_id: str,
    through_date: date,
    policy: OdsBasePolicy,
    code_revision: str,
    parent_base: Path | None = None,
    source_inventory: Path | None = None,
) -> OdsBaseBundle:
    if _SAFE_ID.fullmatch(bundle_id) is None:
        raise OdsBaseError("ODS Base bundle id contains unsupported characters")
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise OdsBaseError("code_revision must be a full Git object id")
    if through_date < policy.daily.increment_start:
        raise OdsBaseError("through_date predates the configured daily increment start")
    expected_selection_digest = (
        None
        if source_inventory is None
        else _inventory_selection_digest(
            source_inventory, policy=policy, through_date=through_date
        )
    )
    astock_root = astock_root.resolve()
    output = output.resolve()
    if output.name != bundle_id:
        raise OdsBaseError("ODS Base output path must end with bundle_id")
    if output.exists():
        existing = verify_ods_base_bundle(output, policy=policy)
        if existing.through_date != through_date:
            raise OdsBaseError("existing ODS Base has a different through_date")
        manifest = _object(output / "manifest.json")
        content = manifest.get("content")
        if (
            expected_selection_digest is not None
            and (
                not isinstance(content, Mapping)
                or content.get("source_inventory_selection_digest")
                != expected_selection_digest
            )
        ):
            raise OdsBaseError("existing ODS Base differs from the bound source inventory")
        return existing

    parent = (
        None
        if parent_base is None
        else verify_ods_base_bundle(parent_base, policy=policy, deep=True)
    )
    if parent is not None and parent.through_date >= through_date:
        raise OdsBaseError("parent ODS Base must end before the requested through_date")
    reusable = _parent_parts(parent)
    source_classification, releases = _selected_source_state(
        astock_root, policy, through_date
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=output.parent))
    datasets: list[dict[str, Any]] = []
    all_parts: list[dict[str, Any]] = []
    reused_bytes = 0
    reused_files = 0
    try:
        for spec in policy.files:
            source = _inside(astock_root, spec.source, "files.source")
            target = _inside(staging, spec.target, "files.target")
            digest, size, reused = _copy_or_reuse_file(
                source=source,
                source_relative=spec.source,
                target=target,
                reusable=reusable,
            )
            if reused:
                reused_bytes += size
                reused_files += 1
            part = _part_metadata(
                source_relative=spec.source,
                target_relative=spec.target,
                dataset_id=spec.dataset_id,
                role=spec.role,
                file_format=spec.file_format,
                digest=digest,
                size_bytes=size,
                target=target,
                date_columns=spec.date_columns,
            )
            quality = None
            if spec.file_format == "parquet" and spec.key_columns:
                quality = _parquet_quality([target], spec.key_columns, spec.date_columns)
                if quality["duplicate_key_count"] or quality["null_key_count"]:
                    raise OdsBaseError(f"ODS Base key gate failed for {spec.dataset_id}")
            datasets.append(
                _dataset_summary(
                    dataset_id=spec.dataset_id,
                    role=spec.role,
                    parts=[part],
                    quality=quality,
                )
            )
            all_parts.append(part)

        for spec in policy.trees:
            source_root = _inside(astock_root, spec.source, "trees.source")
            paths = _tree_paths(astock_root, spec)
            parts: list[dict[str, Any]] = []
            targets: list[Path] = []
            for source in paths:
                relative_name = source.relative_to(source_root).as_posix()
                target_relative = f"{spec.target}/{relative_name}"
                target = _inside(staging, target_relative, "trees.target member")
                source_relative = source.relative_to(astock_root).as_posix()
                digest, size, reused = _copy_or_reuse_file(
                    source=source,
                    source_relative=source_relative,
                    target=target,
                    reusable=reusable,
                )
                if reused:
                    reused_bytes += size
                    reused_files += 1
                part = _part_metadata(
                    source_relative=source_relative,
                    target_relative=target_relative,
                    dataset_id=spec.dataset_id,
                    role=spec.role,
                    file_format="parquet",
                    digest=digest,
                    size_bytes=size,
                    target=target,
                    date_columns=spec.date_columns,
                )
                parts.append(part)
                targets.append(target)
            quality = _parquet_quality(targets, spec.key_columns, spec.date_columns)
            if quality["duplicate_key_count"] or quality["null_key_count"]:
                raise OdsBaseError(f"ODS Base key gate failed for {spec.dataset_id}")
            datasets.append(
                _dataset_summary(
                    dataset_id=spec.dataset_id,
                    role=spec.role,
                    parts=parts,
                    quality=quality,
                )
            )
            all_parts.extend(parts)

        for spec in policy.archives:
            source_root = _inside(astock_root, spec.source, "archive_trees.source")
            paths = _archive_paths(astock_root, spec)
            parts = []
            parquet_rows = 0
            for source in paths:
                relative_name = source.relative_to(source_root).as_posix()
                source_relative = source.relative_to(astock_root).as_posix()
                target_relative = f"{spec.target}/{relative_name}"
                target = _inside(staging, target_relative, "archive_trees.target member")
                digest, size, reused = _copy_or_reuse_file(
                    source=source,
                    source_relative=source_relative,
                    target=target,
                    reusable=reusable,
                )
                if reused:
                    reused_bytes += size
                    reused_files += 1
                file_format = _archive_format(source)
                part = _part_metadata(
                    source_relative=source_relative,
                    target_relative=target_relative,
                    dataset_id=spec.dataset_id,
                    role=spec.role,
                    file_format=file_format,
                    digest=digest,
                    size_bytes=size,
                    target=target,
                )
                if file_format == "parquet":
                    parquet_rows += int(part["row_count"])
                parts.append(part)
            datasets.append(
                _dataset_summary(
                    dataset_id=spec.dataset_id,
                    role=spec.role,
                    parts=parts,
                    quality={
                        "quality_source": "accepted source-evidence archive",
                        "parquet_row_count": parquet_rows,
                    },
                )
            )
            all_parts.extend(parts)

        daily_parts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        release_lineage: list[dict[str, Any]] = []
        for release in releases:
            release_role = str(release["release_role"])
            market_date = _release_market_date(release)
            base_target = f"payload/daily/{market_date.isoformat()}/{release_role}"
            metadata_members = (("release.json", "json"), ("READY", "text"))
            for name, file_format in metadata_members:
                source = release["root"] / name
                target_relative = f"{base_target}/{name}"
                target = _inside(staging, target_relative, "daily metadata target")
                source_relative = source.relative_to(astock_root).as_posix()
                digest, size, reused = _copy_or_reuse_file(
                    source=source,
                    source_relative=source_relative,
                    target=target,
                    reusable=reusable,
                )
                if reused:
                    reused_bytes += size
                    reused_files += 1
                part = _part_metadata(
                    source_relative=source_relative,
                    target_relative=target_relative,
                    dataset_id="daily_release_metadata",
                    role="lineage",
                    file_format=file_format,
                    digest=digest,
                    size_bytes=size,
                    target=target,
                    source_node=str(release["source_node"]),
                )
                daily_parts[("daily_release_metadata", "lineage")].append(part)
                all_parts.append(part)

            for artifact in release["artifacts"]:
                included = artifact["name"] not in _EXCLUDED_RELEASE_ARTIFACTS
                if not included:
                    continue
                source = _inside(
                    release["root"], artifact["relative_path"], "daily artifact path"
                )
                suffix = Path(artifact["relative_path"]).suffix.lower()
                file_format = "parquet" if suffix == ".parquet" else "json"
                target_relative = f"{base_target}/artifacts/{Path(artifact['relative_path']).name}"
                target = _inside(staging, target_relative, "daily artifact target")
                source_relative = source.relative_to(astock_root).as_posix()
                digest, size, reused = _copy_or_reuse_file(
                    source=source,
                    source_relative=source_relative,
                    target=target,
                    reusable=reusable,
                )
                if reused:
                    reused_bytes += size
                    reused_files += 1
                if digest != artifact["sha256"] or size != artifact["size_bytes"]:
                    raise OdsBaseError("copied daily artifact differs from release manifest")
                dataset_id = f"daily_{release_role}_{artifact['name']}"
                part = _part_metadata(
                    source_relative=source_relative,
                    target_relative=target_relative,
                    dataset_id=dataset_id,
                    role="increment",
                    file_format=file_format,
                    digest=digest,
                    size_bytes=size,
                    target=target,
                    knowledge_cutoff=artifact["knowledge_cutoff"],
                    source_node=str(release["source_node"]),
                )
                daily_parts[(dataset_id, "increment")].append(part)
                all_parts.append(part)
            release_lineage.append(_release_lineage_entry(release))
        for (dataset_id, role), parts in sorted(daily_parts.items()):
            datasets.append(
                _dataset_summary(
                    dataset_id=dataset_id,
                    role=role,
                    parts=parts,
                    quality={"quality_source": "verified immutable source release"},
                )
            )

        all_parts.sort(key=lambda item: str(item["path"]))
        datasets.sort(key=lambda item: str(item["dataset_id"]))
        release_lineage.sort(key=lambda item: (item["market_date"], item["release_role"]))
        selection_digest = _selection_digest(
            source_inventory=source_classification,
            release_lineage=release_lineage,
            files=all_parts,
        )
        if (
            expected_selection_digest is not None
            and selection_digest != expected_selection_digest
        ):
            raise OdsBaseError("ODS sources changed after the bound inventory was frozen")
        content = {
            "contract_id": policy.contract_id,
            "policy_sha256": policy.digest,
            "source_node": policy.source_node,
            "mirror_nodes": list(policy.mirror_nodes),
            "through_date": through_date.isoformat(),
            "increment_start": policy.daily.increment_start.isoformat(),
            "authority": {
                "historical_source": "hermes",
                "oci_ods_enabled": False,
                "reconstruction_seed": True,
                "execution_authorized": False,
            },
            "parent_base": (
                None
                if parent is None
                else {
                    "bundle_id": parent.bundle_id,
                    "manifest_sha256": parent.manifest_sha256,
                    "content_digest": parent.content_digest,
                    "through_date": parent.through_date.isoformat(),
                }
            ),
            "source_inventory_selection_digest": selection_digest,
            "source_inventory": source_classification,
            "daily_releases": release_lineage,
            "datasets": datasets,
            "files": all_parts,
        }
        manifest = {
            "schema_version": 1,
            "kind": "quanthus_ods_base_bundle",
            "bundle_id": bundle_id,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "code_revision": code_revision,
            "content_digest": _canonical_digest(content),
            "content": content,
            "storage_reuse": {
                "file_count": reused_files,
                "size_bytes": reused_bytes,
            },
        }
        _write_json(staging / "manifest.json", manifest)
        (staging / "READY").write_text(
            f"{_sha256(staging / 'manifest.json')}\n", encoding="ascii"
        )
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise OdsBaseError("symbolic links are forbidden in ODS Base packages")
            path.chmod(0o750 if path.is_dir() else 0o640)
        staging.chmod(0o750)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_ods_base_bundle(output, policy=policy)


def verify_ods_base_bundle(
    root: Path, *, policy: OdsBasePolicy | None = None, deep: bool = False
) -> OdsBaseBundle:
    root = root.resolve()
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    manifest = _object(manifest_path)
    try:
        ready = ready_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise OdsBaseError("ODS Base package has no READY") from exc
    if ready != _sha256(manifest_path):
        raise OdsBaseError("ODS Base READY differs from manifest.json")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "quanthus_ods_base_bundle"
        or re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("code_revision", ""))) is None
    ):
        raise OdsBaseError("unsupported ODS Base manifest")
    bundle_id = _text(manifest, "bundle_id")
    if _SAFE_ID.fullmatch(bundle_id) is None or root.name != bundle_id:
        raise OdsBaseError("ODS Base directory and bundle identity differ")
    content = manifest.get("content")
    if (
        not isinstance(content, Mapping)
        or _canonical_digest(content) != manifest.get("content_digest")
    ):
        raise OdsBaseError("ODS Base content digest is invalid")
    if policy is not None and (
        content.get("contract_id") != policy.contract_id
        or content.get("policy_sha256") != policy.digest
    ):
        raise OdsBaseError("ODS Base differs from the active policy")
    if content.get("authority") != {
        "execution_authorized": False,
        "historical_source": "hermes",
        "oci_ods_enabled": False,
        "reconstruction_seed": True,
    }:
        raise OdsBaseError("ODS Base authority boundary is invalid")
    files = content.get("files")
    if not isinstance(files, list) or not files:
        raise OdsBaseError("ODS Base manifest contains no files")
    seen: set[str] = set()
    size_bytes = 0
    for item in files:
        if not isinstance(item, Mapping):
            raise OdsBaseError("ODS Base file entry must be an object")
        relative = _safe_relative(_text(item, "path"), "files.path").as_posix()
        if relative in seen or not relative.startswith("payload/"):
            raise OdsBaseError(f"ODS Base file path is duplicate or outside payload: {relative}")
        seen.add(relative)
        path = _inside(root, relative, "files.path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != size
            or _sha256(path) != digest
        ):
            raise OdsBaseError(f"ODS Base file integrity failed: {relative}")
        if deep and item.get("format") == "parquet":
            metadata = _parquet_metadata(path, tuple(item.get("date_bounds", {}).keys()))
            if (
                metadata["row_count"] != item.get("row_count")
                or metadata["schema_sha256"] != item.get("schema_sha256")
            ):
                raise OdsBaseError(f"ODS Base Parquet metadata drift: {relative}")
        if deep and item.get("format") == "sqlite":
            _sqlite_metadata(path)
        size_bytes += size
    envelope_files = {manifest_path, ready_path}
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path not in envelope_files
    }
    if actual != seen:
        raise OdsBaseError("ODS Base manifest file set differs from disk")
    return OdsBaseBundle(
        root=root,
        bundle_id=bundle_id,
        manifest_sha256=_sha256(manifest_path),
        content_digest=_text(manifest, "content_digest"),
        through_date=_parse_date(content.get("through_date"), "content.through_date"),
        increment_start=_parse_date(
            content.get("increment_start"), "content.increment_start"
        ),
        source_node=_text(content, "source_node"),
        size_bytes=size_bytes,
        file_count=len(seen),
    )


def _apply_permissions(root: Path, owner_uid: int, group_gid: int) -> None:
    for path in (root, *sorted(root.rglob("*"))):
        if path.is_symlink():
            raise OdsBaseError("symbolic links are forbidden in sealed ODS Base roots")
        os.chown(path, owner_uid, group_gid)
        path.chmod(0o750 if path.is_dir() else 0o640)


def promote_ods_base_bundle(
    *,
    ingress_bundle: Path,
    base_root: Path,
    current_pointer: Path,
    policy: OdsBasePolicy,
    owner_uid: int | None = None,
    group_gid: int | None = None,
    move_ingress: bool = False,
) -> OdsBaseBundle:
    source = verify_ods_base_bundle(ingress_bundle, policy=policy, deep=True)
    base_root = base_root.resolve()
    base_root.mkdir(parents=True, exist_ok=True)
    destination = base_root / source.bundle_id
    if destination.exists():
        promoted = verify_ods_base_bundle(destination, policy=policy, deep=True)
        if (
            promoted.manifest_sha256 != source.manifest_sha256
            or promoted.content_digest != source.content_digest
        ):
            raise OdsBaseError("sealed ODS Base differs from ingress")
    elif move_ingress:
        ingress = ingress_bundle.resolve()
        if ingress.parent != base_root or not ingress.name.startswith(".incoming-"):
            raise OdsBaseError("move_ingress requires a hidden staging child of base_root")
        if owner_uid is not None and group_gid is not None:
            _apply_permissions(ingress, owner_uid, group_gid)
        os.replace(ingress, destination)
        promoted = verify_ods_base_bundle(destination, policy=policy, deep=True)
    else:
        staging_parent = Path(tempfile.mkdtemp(prefix=f".{source.bundle_id}.", dir=base_root))
        staging = staging_parent / source.bundle_id
        try:
            shutil.copytree(source.root, staging)
            copied = verify_ods_base_bundle(staging, policy=policy, deep=True)
            if copied.manifest_sha256 != source.manifest_sha256:
                raise OdsBaseError("copied ODS Base differs from ingress")
            if owner_uid is not None and group_gid is not None:
                _apply_permissions(staging, owner_uid, group_gid)
            os.replace(staging, destination)
            staging_parent.rmdir()
        except BaseException:
            shutil.rmtree(staging_parent, ignore_errors=True)
            raise
        promoted = verify_ods_base_bundle(destination, policy=policy, deep=True)
    relative = os.path.relpath(promoted.root, start=current_pointer.resolve().parent)
    _atomic_json(
        current_pointer.resolve(),
        {
            "schema_version": 1,
            "bundle_id": promoted.bundle_id,
            "bundle_path": Path(relative).as_posix(),
            "manifest_sha256": promoted.manifest_sha256,
            "content_digest": promoted.content_digest,
            "through_date": promoted.through_date.isoformat(),
        },
    )
    if owner_uid is not None and group_gid is not None:
        os.chown(current_pointer.resolve(), owner_uid, group_gid)
    return promoted


def load_ods_base_current(
    pointer: Path, *, policy: OdsBasePolicy | None = None, deep: bool = False
) -> OdsBaseBundle:
    pointer = pointer.resolve()
    value = _object(pointer)
    path = Path(_text(value, "bundle_path"))
    if not path.is_absolute():
        path = pointer.parent / path
    bundle = verify_ods_base_bundle(path, policy=policy, deep=deep)
    if (
        value.get("schema_version") != 1
        or bundle.bundle_id != value.get("bundle_id")
        or bundle.manifest_sha256 != value.get("manifest_sha256")
        or bundle.content_digest != value.get("content_digest")
        or bundle.through_date.isoformat() != value.get("through_date")
    ):
        raise OdsBaseError("ODS BASE_CURRENT differs from its package")
    return bundle
