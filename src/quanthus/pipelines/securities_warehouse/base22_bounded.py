"""Partition-bounded P10 primitives, not a qualified real-image build entry.

Parquet remains the row representation. Only typed keys/references are sorted by
DuckDB, with explicit memory/spill limits. No full-row JSON/SQLite stage is built.
A job owns one immutable repaired-history intermediate; downstream projections
must bind that intermediate's file identity, never the final August image.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import importlib
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import THROUGH, file_sha
from .base22_sources import (
    SourceAssessmentError,
    _fingerprint,
    _tree,
    disjoint_output,
    physical_path,
)

FICLONE = 0x40049409
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BoundedError(RuntimeError):
    """Closed resource, physical file or global semantic constraint."""


@dataclass(frozen=True)
class Limits:
    batch_rows: int = 16384
    memory_bytes: int = 2147483648
    temp_bytes: int = 2147483648
    allocated_bytes: int = 6442450944
    free_floor_bytes: int = 32212254720
    rss_bytes: int = 4294967296
    wall_seconds: int = 1800

    def __post_init__(self) -> None:
        if any(
            not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in vars(self).values()
        ):
            raise BoundedError("RESOURCE_LIMIT_INVALID")


def storage_census(root: Path) -> dict[str, int]:
    """Include temp/spill/partial files; scanning failures never mean zero usage."""
    logical = allocated = files = 0

    def scan_failed(error: OSError) -> None:
        if isinstance(error, FileNotFoundError):
            return  # Only a mutable job-owned spill/index directory can vanish.
        raise BoundedError("RESOURCE_SCAN_FAILED") from error

    for directory, dirs, names in os.walk(root, onerror=scan_failed, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(directory) / name
            if path.is_symlink():
                raise BoundedError("RESOURCE_SYMLINK_FORBIDDEN")
            if name in dirs:
                continue
            try:
                info = path.stat()
            except FileNotFoundError:
                # A job-owned spill file may disappear while its writer runs.
                # Source inventory uses the stricter, fail-closed _tree instead.
                continue
            if not stat.S_ISREG(info.st_mode):
                raise BoundedError("RESOURCE_SPECIAL_FILE_FORBIDDEN")
            logical += info.st_size
            allocated += info.st_blocks * 512
            files += 1
    return {"logical_bytes": logical, "allocated_bytes": allocated, "files": files}


class Budget:
    """Unique isolated job directory, monitored even during blocking sort queries.

    Limits apply to the entire retained job root, not just its final Parquet.
    A breach interrupts active DuckDB work and is raised at the next batch boundary.
    Partial files and evidence remain; this component never writes READY or cleans
    an input, older run or unknown job root.
    """

    def __init__(self, root: Path, sources: list[Path], limits: Limits):
        self.root = disjoint_output(root, sources)
        self.limits = limits
        self.started = time.monotonic()
        self.stage = "initialization"
        self.peak = {"allocated_bytes": 0, "logical_bytes": 0, "rss_bytes": 0}
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connections: list[Any] = []

    def __enter__(self) -> Budget:
        self.check()
        self.root.mkdir(parents=True, exist_ok=False)
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if args[0] is None:
            self.check()

    def _monitor(self) -> None:
        while not self._stop.wait(0.1):
            try:
                self.check()
            except Exception as exc:
                self.error = str(exc) if isinstance(exc, BoundedError) else "RESOURCE_SCAN_FAILED"
                for connection in list(self._connections):
                    connection.interrupt()
                return

    def destination(self, path: Path) -> Path:
        path = physical_path(path)
        if self.root not in path.parents:
            raise BoundedError("DESTINATION_OUTSIDE_JOB")
        return path

    def begin_stage(self, name: str) -> None:
        """Reset only the per-stage clock; never reset resource peaks or a breach."""
        self.check()
        self.stage = name
        self.started = time.monotonic()

    def check(self, *, reserve: int = 0, extra_rss: int = 0) -> None:
        if self.error:
            raise BoundedError(self.error)
        usage = (
            storage_census(self.root)
            if self.root.exists()
            else {"allocated_bytes": 0, "logical_bytes": 0}
        )
        parent = self.root
        while not parent.exists():
            parent = parent.parent
        fs = os.statvfs(parent)
        rss = (
            int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            + extra_rss
        )
        for field in ["allocated_bytes", "logical_bytes"]:
            self.peak[field] = max(self.peak[field], usage[field])
        self.peak["rss_bytes"] = max(self.peak["rss_bytes"], rss)
        if usage["allocated_bytes"] + reserve > self.limits.allocated_bytes:
            raise BoundedError("ALLOCATED_WRITE_BUDGET_EXCEEDED")
        if (self.root / "temp").exists() and storage_census(self.root / "temp")[
            "allocated_bytes"
        ] > self.limits.temp_bytes:
            raise BoundedError("TEMP_BUDGET_EXCEEDED")
        if fs.f_bavail * fs.f_frsize - reserve < self.limits.free_floor_bytes:
            raise BoundedError("FREE_SPACE_FLOOR_REACHED")
        if rss > self.limits.rss_bytes:
            raise BoundedError("RSS_LIMIT_EXCEEDED")
        if time.monotonic() - self.started > self.limits.wall_seconds:
            raise BoundedError("STAGE_WALL_LIMIT_EXCEEDED")


@dataclass(frozen=True)
class Member:
    path: Path
    sha256: str
    size_bytes: int
    rows: int

    def verify(self) -> tuple[int, int, int, int, int]:
        path = physical_path(self.path)
        before = _fingerprint(path)
        if before[2] != self.size_bytes or file_sha(path) != self.sha256:
            raise BoundedError("FROZEN_MEMBER_MISMATCH")
        if _fingerprint(path) != before:
            raise BoundedError("SOURCE_CHANGED")
        return before


def independent_copy(member: Member, output: Path, budget: Budget) -> dict[str, Any]:
    """FICLONE if supported; otherwise copy bytes into a distinct exclusive inode."""
    before = member.verify()
    output = budget.destination(output)
    budget.check(reserve=member.size_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "reflink"
    fallback_errno = None
    with member.path.open("rb") as source, output.open("xb") as target:
        try:
            fcntl.ioctl(target.fileno(), FICLONE, source.fileno())
        except OSError as exc:
            if exc.errno not in {
                errno.EXDEV,
                errno.ENOTTY,
                errno.EOPNOTSUPP,
                errno.EINVAL,
                errno.ENOSYS,
            }:
                raise BoundedError("REFLINK_UNEXPECTED_FAILURE") from exc
            mode, fallback_errno = "independent_copy", exc.errno
            target.truncate(0)
            for block in iter(lambda: source.read(1024 * 1024), b""):
                budget.check(reserve=len(block))
                target.write(block)
        target.flush()
        os.fsync(target.fileno())
    after = output.stat()
    if (
        _fingerprint(member.path) != before
        or after.st_nlink != 1
        or (after.st_dev, after.st_ino) == before[:2]
        or after.st_size != member.size_bytes
        or file_sha(output) != member.sha256
    ):
        raise BoundedError("INDEPENDENT_COPY_MISMATCH")
    budget.check()
    return {
        "mode": mode,
        "fallback_errno": fallback_errno,
        "sha256": member.sha256,
        "logical_bytes": after.st_size,
        "allocated_bytes": after.st_blocks * 512,
        "source_device": before[0],
        "output_device": after.st_dev,
        "different_inode": True,
        "nlink": after.st_nlink,
    }


def _batches(paths: list[Path], schema: Any, batch_rows: int) -> Iterator[Any]:
    pq = importlib.import_module("pyarrow.parquet")

    for path in paths:
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(schema, check_metadata=False):
            raise BoundedError("PHYSICAL_SCHEMA_MISMATCH")
        yield from parquet.iter_batches(batch_size=batch_rows)


def semantic_scan(
    paths: list[Path], schema: Any, batch_rows: int = 16384, check: Callable[[], None] | None = None
) -> dict[str, Any]:
    """Order-sensitive typed column streams, independent of file/row-group boundaries.

    Fixed-width values retain exact Arrow little-endian integers (including Decimal
    unscaled integers and UTC timestamp microseconds), preceded by a validity byte.
    Strings use signed 64-bit lengths (-1 for null). Frames cross neither file nor
    batch identity. Null payload bytes are canonical zero. Only one batch is decoded.
    Nothing is stored in JSON/SQLite or returned as values.
    """
    import numpy as np

    pa = importlib.import_module("pyarrow")
    pc = importlib.import_module("pyarrow.compute")

    from .base22 import digest

    hashes = [hashlib.sha256() for _ in schema]
    rows = 0
    for batch in _batches(paths, schema, batch_rows):
        if check:
            check()
        for i, column in enumerate(batch.columns):
            if (
                pa.types.is_string(column.type)
                or pa.types.is_large_string(column.type)
                or pa.types.is_binary(column.type)
                or pa.types.is_large_binary(column.type)
            ):
                hashes[i].update(
                    b"".join(
                        (-1).to_bytes(8, "little", signed=True)
                        if value is None
                        else len(
                            encoded := value if isinstance(value, bytes) else value.encode("utf-8")
                        ).to_bytes(8, "little", signed=True)
                        + encoded
                        for value in column.to_pylist()
                    )
                )
            elif pa.types.is_null(column.type):
                hashes[i].update((-1).to_bytes(8, "little", signed=True) * len(column))
            else:
                valid = np.array(column.is_valid(), dtype=np.uint8)
                if pa.types.is_boolean(column.type):
                    normalized = pc.fill_null(column, False).cast(pa.uint8())
                else:
                    zero = (
                        pa.scalar(0, type=column.type)
                        if not pa.types.is_decimal(column.type)
                        else pa.scalar(Decimal(0), type=column.type)
                    )
                    normalized = pc.fill_null(column, zero)
                width = normalized.type.bit_width // 8
                data = np.frombuffer(
                    normalized.buffers()[1],
                    dtype=np.uint8,
                    count=len(column) * width,
                    offset=normalized.offset * width,
                ).reshape(-1, width)
                framed = np.empty((len(column), width + 1), dtype=np.uint8)
                framed[:, 0] = valid
                framed[:, 1:] = data
                hashes[i].update(framed.tobytes())
        rows += batch.num_rows
    columns = {
        field.name: "sha256:" + h.hexdigest() for field, h in zip(schema, hashes, strict=True)
    }
    return {
        "rows": rows,
        "schema_sha256": digest(str(schema.remove_metadata())),
        "columns_sha256": columns,
        "semantic_sha256": digest(columns),
    }


def rewrite_partition(
    members: list[Member],
    output: Path,
    budget: Budget,
    transform: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """One affected partition, deterministic source order; retained partial on failure.

    Arbitrary merge/repair authority is deliberately outside this primitive. A caller
    must freeze exact replacement keys and qualification before using a transform.
    """
    pq = importlib.import_module("pyarrow.parquet")

    if not members:
        raise BoundedError("PARTITION_INPUT_REQUIRED")
    fingerprints = [member.verify() for member in members]
    schema = pq.ParquetFile(members[0].path).schema_arrow
    for member in members:
        file = pq.ParquetFile(member.path)
        if file.metadata.num_rows != member.rows or not file.schema_arrow.equals(schema):
            raise BoundedError("FROZEN_PARTITION_CENSUS_MISMATCH")
    output = budget.destination(output)
    partial = budget.destination(output.with_name(output.name + ".partial"))
    if output.exists() or partial.exists():
        raise BoundedError("PARTITION_OUTPUT_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    start = time.monotonic()
    with (
        partial.open("xb") as stream,
        pq.ParquetWriter(stream, schema, compression="zstd") as writer,
    ):
        for batch in _batches(
            [member.path for member in members], schema, budget.limits.batch_rows
        ):
            budget.check(reserve=batch.nbytes * 2)
            result = transform(batch) if transform else batch
            if result.num_rows != batch.num_rows or not result.schema.equals(schema):
                raise BoundedError("REWRITE_SCHEMA_OR_ROW_COUNT_CHANGED")
            writer.write_batch(result, row_group_size=budget.limits.batch_rows)
            count += result.num_rows
    if count != sum(m.rows for m in members):
        raise BoundedError("REWRITE_ROW_CENSUS_MISMATCH")
    if any(_fingerprint(m.path) != f for m, f in zip(members, fingerprints, strict=True)):
        raise BoundedError("SOURCE_CHANGED")
    # link is only an atomic no-overwrite local rename mechanism; unlink immediately
    # leaves the new output with nlink=1. No input is ever linked.
    os.link(partial, output)
    partial.unlink()
    budget.check()
    return {
        "rows": count,
        "sha256": file_sha(output),
        "logical_bytes": output.stat().st_size,
        "allocated_bytes": output.stat().st_blocks * 512,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "disposition": "partition_bounded_rewrite",
    }


def identifier(value: str) -> str:
    if not _IDENT.fullmatch(value):
        raise BoundedError("SQL_IDENTIFIER_INVALID")
    return '"' + value + '"'


class GlobalScan:
    """Typed global key/version/FK/lineage checks over local Parquet, bounded spill.

    This shared scan is suitable for build and offline verification. It cannot grant
    qualification: source admission and the exact physical partition inventory are
    supplied by the offline image contract and remain mandatory.
    """

    def __init__(self, budget: Budget, *, read_only_roots: list[Path] | None = None):
        import duckdb

        self.budget = budget
        # Cold images are existing immutable inputs, not newly allocated scratch.
        # Keep the write destination guard strict. An explicitly supplied reader
        # root must be a real kernel-readonly mount, disjoint from mutable TMP.
        self.read_only_roots: list[Path] = []
        for raw in read_only_roots or []:
            root = physical_path(raw)
            if (
                not root.is_dir()
                or root == budget.root
                or root in budget.root.parents
                or budget.root in root.parents
                or root in self.read_only_roots
                or not os.statvfs(root).f_flag & os.ST_RDONLY
            ):
                raise BoundedError("GLOBAL_READER_ROOT_NOT_ISOLATED_READONLY")
            self.read_only_roots.append(root)
        temp = budget.destination(budget.root / "temp" / ("scan-" + str(time.time_ns())))
        temp.mkdir(parents=True, exist_ok=False)
        self.temp = temp
        self.connection = duckdb.connect(
            config={
                "memory_limit": str(budget.limits.memory_bytes) + "B",
                "threads": "1",
                "temp_directory": str(temp),
                "max_temp_directory_size": str(budget.limits.temp_bytes) + "B",
                "autoload_known_extensions": "false",
                "autoinstall_known_extensions": "false",
                "preserve_insertion_order": "false",
            }
        )
        # Local Parquet/spill needs filesystem access. Paths are restricted to this
        # job; extension autoload/install is disabled and no URL or user SQL entry
        # is provided. Cold acceptance additionally denies sources and network.
        budget._connections.append(self.connection)
        self.tables: set[str] = set()
        self.source_paths: dict[str, list[Path]] = {}
        self.active_reference_batch: tuple[str, tuple[Path, ...]] | None = None
        self.key_metrics: dict[str, Any] = {}
        self._reference_maps: dict[tuple[str, str], str] = {}
        self._reference_snapshots: dict[str, list[tuple[Path, tuple[int, int, int, int, int]]]] = {}
        self._reference_batch_cache: dict[str, tuple[tuple[Path, ...], ...]] = {}

    def __enter__(self) -> GlobalScan:
        return self

    def __exit__(self, *args: object) -> None:
        self.budget._connections.remove(self.connection)
        self.connection.close()

    def add(self, name: str, paths: list[Path]) -> None:
        identifier(name)
        if not paths or name in self.tables:
            raise BoundedError("SCAN_TABLE_INVALID")
        for path in paths:
            path = physical_path(path)
            if not any(root in path.parents for root in self.read_only_roots):
                self.budget.destination(path)
            if path.stat().st_nlink != 1 or not path.is_file():
                raise BoundedError("SCAN_LOCAL_INDEPENDENT_FILE_REQUIRED")
        self.connection.read_parquet([str(p) for p in paths], hive_partitioning=False).create_view(
            name
        )
        self.tables.add(name)
        self.source_paths[name] = list(paths)

    def reject(self, sql: str, reason: str) -> None:
        self.budget.check()
        try:
            failed = self.connection.execute(sql).fetchone() is not None
        except Exception as exc:
            self.budget.check()
            raise BoundedError("GLOBAL_SCAN_EXECUTION_FAILED") from exc
        self.budget.check()
        if failed:
            raise BoundedError(reason)

    def keys(self, name: str, business_key: list[str], version_key: list[str]) -> None:
        pa = importlib.import_module("pyarrow")
        pc = importlib.import_module("pyarrow.compute")
        pq = importlib.import_module("pyarrow.parquet")

        table = identifier(name)
        keys = ",".join(identifier(k) for k in version_key)
        nulls = " OR ".join(identifier(k) + " IS NULL" for k in version_key)
        self.reject(f"SELECT 1 FROM {table} WHERE {nulls} LIMIT 1", "GLOBAL_KEY_NULL")
        bk = ",".join(identifier(k) for k in business_key)
        # One global typed key projection per table. Equal business keys always
        # enter the same bucket across ALL physical partitions and files. Hash
        # collisions only co-locate keys: every check still compares full keys.
        # Prices/full row documents are never stored in this job-local TMP index.
        index_root = self.budget.destination(self.temp / ("keys-" + name))
        index_root.mkdir(exist_ok=False)
        writers: dict[int, Any] = {}
        counts: dict[int, int] = {}
        pending: dict[int, list[Any]] = {}
        pending_rows: dict[int, int] = {}
        fields = list(dict.fromkeys([*version_key, "knowledge_to"]))
        projection = ",".join(identifier(field) for field in fields)
        projection_result = self.connection.execute(
            f"SELECT {projection}, hash({bk})%64 AS __bucket FROM {table}"
        )
        reader = (
            projection_result.to_arrow_reader(self.budget.limits.batch_rows)
            if hasattr(projection_result, "to_arrow_reader")
            else projection_result.fetch_record_batch(self.budget.limits.batch_rows)
        )

        def flush(bucket: int) -> None:
            # Hash fan-out makes each incoming batch tiny in an individual bucket.
            # Writing every intersection as a row group retains unbounded footer
            # metadata in the 64 writers. Coalesce at most two bounded batches,
            # keeping the key projection and full global comparisons unchanged.
            chunks = pending[bucket]
            self.budget.check(extra_rss=2 * sum(chunk.nbytes for chunk in chunks))
            table = pa.Table.from_batches(chunks).combine_chunks()
            self.budget.check(reserve=table.nbytes * 2)
            writers[bucket].write_table(table, row_group_size=self.budget.limits.batch_rows)
            pending[bucket] = []
            pending_rows[bucket] = 0

        try:
            for batch in reader:
                self.budget.check(reserve=batch.nbytes * 2)
                buckets = batch.column("__bucket")
                for bucket in sorted(set(buckets.to_pylist())):
                    selected = batch.filter(pc.equal(buckets, bucket)).select(fields)
                    if bucket not in writers:
                        writers[bucket] = pq.ParquetWriter(
                            index_root / (str(bucket) + ".parquet"),
                            selected.schema,
                            compression="zstd",
                        )
                        pending[bucket] = []
                        pending_rows[bucket] = 0
                    pending[bucket].append(selected)
                    pending_rows[bucket] += selected.num_rows
                    counts[bucket] = counts.get(bucket, 0) + selected.num_rows
                    if pending_rows[bucket] >= self.budget.limits.batch_rows:
                        flush(bucket)
            for bucket in sorted(writers):
                if pending_rows[bucket]:
                    flush(bucket)
        finally:
            for writer in writers.values():
                writer.close()
        self.key_metrics[name] = {
            "rows": sum(counts.values()),
            "buckets": len(counts),
            "largest_bucket_rows": max(counts.values(), default=0),
            "index_storage": storage_census(index_root),
            "materializations": 1,
            "lifecycle": "TMP",
        }
        query = f"""SELECT 1 FROM (SELECT knowledge_from,
            lag(knowledge_to) OVER w AS previous_end,
            row_number() OVER w AS n FROM bounded_keys
            WINDOW w AS (PARTITION BY {bk} ORDER BY knowledge_from))
            WHERE n>1 AND (previous_end IS NULL OR previous_end>knowledge_from) LIMIT 1"""
        for bucket in sorted(writers):
            self.connection.read_parquet(
                str(index_root / (str(bucket) + ".parquet")), hive_partitioning=False
            ).create_view("bounded_keys", replace=True)
            self.reject(
                f"SELECT 1 FROM bounded_keys GROUP BY {keys} HAVING count(*)>1 LIMIT 1",
                "GLOBAL_DUPLICATE_VERSION_KEY",
            )
            self.reject(query, "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS")
        # Only this exact successful, job-owned index is removed. Failed index
        # evidence is retained. Downstream proofs bind the frozen file identities.
        if writers:
            self.connection.execute("DROP VIEW bounded_keys")
        for bucket in sorted(writers):
            (index_root / (str(bucket) + ".parquet")).unlink()
        index_root.rmdir()

    def lineage(self, name: str, admitted: list[tuple[str, str, str]]) -> None:
        pa = importlib.import_module("pyarrow")

        if not admitted:
            raise BoundedError("TABLE_SOURCE_CLOSURE_EMPTY")
        registry = "admitted_" + name
        self.connection.register(
            registry,
            pa.table(
                {
                    "record_source": [x[0] for x in admitted],
                    "source_revision": [x[1] for x in admitted],
                    "source_artifact_sha256": [x[2] for x in admitted],
                }
            ),
        )
        self.reject(
            f"""SELECT 1 FROM {identifier(name)} r WHERE
            r.source_record_id IS NULL OR r.source_record_id='' OR NOT EXISTS
            (SELECT 1 FROM {identifier(registry)} a WHERE a.record_source=r.record_source
             AND a.source_revision=r.source_revision
             AND a.source_artifact_sha256=r.source_artifact_sha256) LIMIT 1""",
            "TABLE_ROW_LINEAGE_CLOSURE_FAILED",
        )

    def _reference_map(self, target: str, reference_column: str) -> str:
        """One compact temporal parent per key, shared by every in-job child.

        A join condition containing nullable interval ends can make DuckDB
        choose a blockwise nested loop even when an equality key is present.
        Joining the exact key alone, then filtering its complete version list,
        keeps the original existential semantics without a child delimiter or
        a child-row materialization. No protected financial fields are copied.
        """
        identity = (target, reference_column)
        if identity not in self._reference_maps:
            columns = {
                r[0] for r in self.connection.execute("DESCRIBE " + identifier(target)).fetchall()
            }
            fields = sorted(
                columns
                & {
                    "knowledge_from",
                    "knowledge_to",
                    "effective_from",
                    "effective_to",
                    "venue_id",
                    "trade_date",
                }
            )
            record = ",".join(identifier(f) + ":=" + identifier(f) for f in fields)
            name = identifier("reference_versions_" + target + "_" + reference_column)
            self.budget.check()
            self.connection.execute(
                f"CREATE TEMP TABLE {name} AS SELECT {identifier(reference_column)} "
                f"AS parent_key,list(struct_pack({record})) AS versions FROM {identifier(target)} "
                f"GROUP BY {identifier(reference_column)}"
            )
            self.budget.check()
            self._reference_maps[identity] = name
        return self._reference_maps[identity]

    def _reference_batches(self, name: str) -> list[list[Path]]:
        """Bound only the child scan; parents remain globally complete.

        These physical batches create views, not another row materialization.
        Foreign-key existence is universal over children and independent of
        other children. Key/version uniqueness continues to use the entire
        original table in `keys`, never these per-reference views.
        Prepare each immutable table once inside this scan. Reuse checks the
        ordered physical inventory and file fingerprints, never an old job.
        """
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        def snapshot() -> list[tuple[Path, tuple[int, int, int, int, int]]]:
            try:
                return [(p, _fingerprint(physical_path(p))) for p in self.source_paths[name]]
            except (OSError, SourceAssessmentError) as exc:
                raise BoundedError("REFERENCE_BATCH_INPUT_CHANGED") from exc

        before = snapshot()
        if name in self._reference_batch_cache:
            if before != self._reference_snapshots[name]:
                raise BoundedError("REFERENCE_BATCH_INPUT_CHANGED")
            self.budget.check()
            return [list(paths) for paths in self._reference_batch_cache[name]]
        batches: list[list[Path]] = []
        current: list[Path] = []
        rows = size = 0
        max_rows = 64 * self.budget.limits.batch_rows
        max_bytes = self.budget.limits.memory_bytes // 8
        for path in self.source_paths[name]:
            count, length = pq.ParquetFile(path).metadata.num_rows, path.stat().st_size
            # A larger existing file forms its own lazy native scan; its
            # columnar reader streams batches under the same memory/RSS gates.
            if current and (
                len(current) >= 256 or rows + count > max_rows or size + length > max_bytes
            ):
                batches.append(current)
                current = []
                rows = size = 0
            current.append(path)
            rows += count
            size += length
            self.budget.check()
        if current:
            batches.append(current)
        if snapshot() != before:
            raise BoundedError("REFERENCE_BATCH_INPUT_CHANGED")
        self._reference_snapshots[name] = before
        self._reference_batch_cache[name] = tuple(tuple(paths) for paths in batches)
        return batches

    def structural_reference(self, name: str, column: str, target: str, reference: str) -> None:
        """Existence-only typed relocation rule, without adding temporal authority."""
        parent = self._reference_map(target, reference)
        for paths in self._reference_batches(name):
            self.active_reference_batch = (name, tuple(paths))
            child = "reference_child_" + name
            self.connection.read_parquet(
                [str(p) for p in paths], hive_partitioning=False
            ).create_view(child, replace=True)
            self.reject(
                f"SELECT 1 FROM {identifier(child)} r LEFT JOIN {parent} p "
                f"ON p.parent_key=r.{identifier(column)} WHERE "
                f"r.{identifier(column)} IS NOT NULL AND p.parent_key IS NULL LIMIT 1",
                "RELOCATION_MODEL_FOREIGN_KEY_MISSING",
            )

    def references(self, model: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
        tables = {t["name"]: t for t in model["tables"]}
        if self.tables != set(tables) or self.tables != set(contract["tables"]):
            raise BoundedError("EXACT_GLOBAL_TABLE_SET_MISMATCH")
        for name, table in tables.items():
            field = contract["tables"][name]["cutoff_field"]
            coordinate = "r." + identifier(field)
            source_types = dict(
                (row[0], row[1])
                for row in self.connection.execute("DESCRIBE " + identifier(name)).fetchall()
            )
            if source_types[field] == "DATE":
                coordinate = f"timezone('Asia/Shanghai',CAST({coordinate} AS TIMESTAMP))"
            batches = self._reference_batches(name) if table["foreign_keys"] else []
            for fk in table["foreign_keys"]:
                column = "r." + identifier(fk["column"])
                target = identifier(fk["table"])
                temporal = (
                    "t.knowledge_from<=r.knowledge_from AND "
                    "(t.knowledge_to IS NULL OR r.knowledge_from<t.knowledge_to)"
                )
                target_cols = {
                    row[0] for row in self.connection.execute("DESCRIBE " + target).fetchall()
                }
                if "effective_from" in target_cols:
                    temporal += (
                        f" AND t.effective_from<={coordinate} AND "
                        f"(t.effective_to IS NULL OR {coordinate}<t.effective_to)"
                    )
                join = ""
                if fk["table"] == "dwd_trading_calendar":
                    if table["time_profile"] == "market_observation":
                        daily = (
                            "TRUE" if name == "dwd_tradability_state" else "r.interval_size='1d'"
                        )
                        temporal += (
                            f" AND (NOT ({daily}) OR t.trade_date="
                            "CAST(timezone('Asia/Shanghai',r.observation_start) AS DATE))"
                        )
                    listing = next(
                        (f for f in table["foreign_keys"] if f["table"] == "dwd_listing"), None
                    )
                    if listing:
                        parent = self._reference_map("dwd_listing", listing["reference_column"])
                        join = (
                            f"LEFT JOIN {parent} lv ON lv.parent_key="
                            f"r.{identifier(listing['column'])} "
                        )
                        # Includes every listing version and its venue, including
                        # mixed venues for one ID; no stable-venue assumption or
                        # uniform open/close rule is added to the model.
                        temporal += (
                            " AND coalesce(length(list_filter(lv.versions,l -> "
                            "l.venue_id=t.venue_id AND l.knowledge_from<=r.knowledge_from "
                            "AND (l.knowledge_to IS NULL OR r.knowledge_from<l.knowledge_to) "
                            f"AND l.effective_from<={coordinate} AND "
                            f"(l.effective_to IS NULL OR {coordinate}<l.effective_to)))>0,FALSE)"
                        )
                parent = self._reference_map(fk["table"], fk["reference_column"])
                predicate = f"{column} IS NOT NULL AND " if fk["nullable"] else ""
                for paths in batches:
                    self.active_reference_batch = (name, tuple(paths))
                    child = identifier("reference_child_" + name)
                    self.connection.read_parquet(
                        [str(p) for p in paths], hive_partitioning=False
                    ).create_view("reference_child_" + name, replace=True)
                    self.reject(
                        f"SELECT 1 FROM {child} r {join}LEFT JOIN {parent} p "
                        f"ON p.parent_key={column} WHERE {predicate}(p.parent_key IS NULL OR "
                        f"length(list_filter(p.versions,t -> {temporal}))=0) LIMIT 1",
                        "GLOBAL_FOREIGN_KEY_DATE_VENUE_CLOSURE_FAILED:" + name,
                    )

    def _cutoff_predicate(self, cutoff: str, field: Any) -> str:
        pa = importlib.import_module("pyarrow")
        expr = (
            f"CAST(timezone('Asia/Shanghai',{cutoff}) AS DATE)"
            if pa.types.is_timestamp(field.type)
            else cutoff
        )
        return f"{expr}>DATE '{THROUGH.isoformat()}'"

    def row_rules(self, name: str, schema: Any, rule: Mapping[str, Any]) -> None:
        table = identifier(name)
        required = " OR ".join(identifier(f.name) + " IS NULL" for f in schema if not f.nullable)
        if required:
            self.reject(f"SELECT 1 FROM {table} WHERE {required} LIMIT 1", "REQUIRED_FIELD_UNKNOWN")
        cutoff = identifier(rule["cutoff_field"])
        field = schema.field(rule["cutoff_field"])
        self.reject(
            f"SELECT 1 FROM {table} WHERE {self._cutoff_predicate(cutoff, field)} LIMIT 1",
            "BUSINESS_DATE_AFTER_CUTOFF",
        )
        for start, end in [
            ("effective_from", "effective_to"),
            ("knowledge_from", "knowledge_to"),
            ("observation_start", "observation_end"),
            ("period_start", "period_end"),
        ]:
            if start in schema.names and end in schema.names:
                self.reject(
                    f"SELECT 1 FROM {table} WHERE {identifier(end)}<={identifier(start)} LIMIT 1",
                    "INVALID_TIME_INTERVAL",
                )


def audit_local_partitions(
    budget: Budget,
    layout: Mapping[str, Any],
    schemas: Mapping[str, Any],
    contract: Mapping[str, Any],
    model: Mapping[str, Any],
    admitted: Mapping[str, list[tuple[str, str, str]]],
) -> dict[str, Any]:
    """Offline bounded scan of an exact frozen local physical file/partition census.

    The registry is table-specific and must come from image-local admitted evidence.
    This primitive does not replace the admission contract or historical repair gates.
    Zero-row partitions are compared physically, not inferred from nonempty rows.
    """
    pq = importlib.import_module("pyarrow.parquet")

    from .base22 import partition

    tables = set(contract["tables"])
    if set(layout) != tables or set(schemas) != tables or set(admitted) != tables:
        raise BoundedError("EXACT_PHYSICAL_TABLE_SET_MISMATCH")
    summaries: dict[str, Any] = {}
    declared: set[str] = set()
    with GlobalScan(budget) as scan:
        for name in sorted(tables):
            paths = []
            rule, schema = contract["tables"][name], schemas[name]
            physical_partitions = layout[name]["partitions"]
            if not physical_partitions:
                raise BoundedError("PHYSICAL_PARTITION_REQUIRED")
            counts: dict[str, int] = {}
            for part, files in physical_partitions.items():
                if not files:
                    raise BoundedError("PHYSICAL_PARTITION_FILE_REQUIRED")
                counts[part] = 0
                for item in files:
                    path = budget.destination(budget.root / item["path"])
                    if item["path"] in declared:
                        raise BoundedError("DUPLICATE_PHYSICAL_FILE")
                    declared.add(item["path"])
                    if path.stat().st_nlink != 1:
                        raise BoundedError("INDEPENDENT_PHYSICAL_FILE_REQUIRED")
                    Member(path, item["sha256"], item["size_bytes"], item["rows"]).verify()
                    parquet = pq.ParquetFile(path)
                    if not parquet.schema_arrow.equals(schema, check_metadata=False):
                        raise BoundedError("PHYSICAL_SCHEMA_MISMATCH")
                    if parquet.metadata.num_rows != item["rows"]:
                        raise BoundedError("PHYSICAL_ROW_CENSUS_MISMATCH")
                    counts[part] += item["rows"]
                    for batch in parquet.iter_batches(batch_size=budget.limits.batch_rows):
                        budget.check()
                        # Only coordinate columns are decoded; no full history documents.
                        fields = []
                        for field in rule["partition_by"]:
                            fields.append(
                                field.rsplit("_", 1)[0]
                                if field.endswith(("_year", "_month"))
                                else field
                            )
                        fields = list(dict.fromkeys(fields))
                        for row in batch.select(fields).to_pylist() if fields else [{}]:
                            if batch.num_rows and partition(row, rule) != part:
                                raise BoundedError("PHYSICAL_PARTITION_COORDINATE_MISMATCH")
                    paths.append(path)
            if counts != layout[name]["expected_partition_rows"]:
                raise BoundedError("EXACT_PHYSICAL_PARTITION_CENSUS_MISMATCH")
            if not sum(counts.values()) and set(counts) != {"empty"}:
                raise BoundedError("UNIQUE_EMPTY_PARTITION_REQUIRED")
            if sum(counts.values()) and any(count == 0 for count in counts.values()):
                raise BoundedError("EXTRA_ZERO_ROW_PARTITION")
            scan.add(name, paths)
            scan.keys(name, rule["business_key"], rule["version_key"])
            scan.lineage(name, admitted[name])
            scan.row_rules(name, schema, rule)
            summaries[name] = {"rows": sum(counts.values()), "partitions": counts}
        actual = {p for p in _tree(budget.root / "payload")}
        expected = {str(Path(p).relative_to("payload")) for p in declared}
        if actual != expected:
            raise BoundedError("EXACT_PHYSICAL_FILE_SET_MISMATCH")
        scan.references(model, contract)
    return summaries
