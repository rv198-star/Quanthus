"""Build an immutable OCI none-history child from checksummed raw shards."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class NoneBackfillError(RuntimeError):
    """Raised when a raw none backfill cannot be promoted safely."""


@dataclass(frozen=True)
class NoneBackfillResult:
    revision: str
    output: Path
    affected_securities: int
    affected_execution_rows: int
    parent_rows: int
    raw_rows: int
    overlap_rows: int
    inserted_rows: int
    output_rows: int
    checksums: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "affected_securities": self.affected_securities,
            "affected_execution_rows": self.affected_execution_rows,
            "parent_rows": self.parent_rows,
            "raw_rows": self.raw_rows,
            "overlap_rows": self.overlap_rows,
            "inserted_rows": self.inserted_rows,
            "output_rows": self.output_rows,
            "checksums": dict(sorted(self.checksums.items())),
        }


def write_none_current(*, revision_dir: Path, pointer: Path) -> None:
    """Atomically promote a reviewed OCI none revision."""

    meta = revision_dir / "META.json"
    checksums = revision_dir / "CHECKSUMS.sha256"
    if not meta.is_file() or not checksums.is_file():
        raise NoneBackfillError(f"revision has no complete metadata: {revision_dir}")
    metadata = _load_object(meta)
    if metadata.get("revision") != revision_dir.name:
        raise NoneBackfillError("none revision metadata does not match its directory")
    payload = {
        "dataset": "daily_ohlcv_none",
        "revision": revision_dir.name,
        "revision_path": str(revision_dir.resolve()),
        "meta_sha256": f"sha256:{_sha256(meta)}",
        "checksums_sha256": f"sha256:{_sha256(checksums)}",
        "daily_sha256": metadata.get("checksums", {}).get("daily_ohlcv_none.parquet"),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    if not payload["daily_sha256"]:
        raise NoneBackfillError("none revision metadata has no daily dataset checksum")
    pointer.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{pointer.name}.", dir=pointer.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, pointer)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NoneBackfillError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise NoneBackfillError(f"JSON payload must be an object: {path}")
    return value


def _verify_checksum_ledger(root: Path) -> int:
    ledger = root / "CHECKSUMS.sha256"
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise NoneBackfillError(f"cannot read checksum ledger: {ledger}") from exc
    verified = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            expected, relative = line.split(None, 1)
        except ValueError as exc:
            raise NoneBackfillError(f"invalid checksum line: {line!r}") from exc
        relative = relative.strip().lstrip("*")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise NoneBackfillError(f"unsafe checksum path: {relative}")
        target = root / path
        if not target.is_file():
            raise NoneBackfillError(f"checksummed file is missing: {target}")
        if _sha256(target) != expected.lower():
            raise NoneBackfillError(f"checksum mismatch: {target}")
        verified += 1
    if not verified:
        raise NoneBackfillError(f"checksum ledger is empty: {ledger}")
    return verified


def _require_inputs(parent: Path, raw: Path, detection: Path, output: Path) -> None:
    parent_names = (
        "daily_ohlcv_none.parquet",
        "daily_universe.parquet",
        "daily_isST.parquet",
        "st_status_pit.parquet",
        "ref_security_code_validity.parquet",
        "META.json",
        "CHECKSUMS.sha256",
    )
    raw_names = (
        "scope_ledger_276.parquet",
        "META.json",
        "manifest.json",
        "CHECKSUMS.sha256",
    )
    required = [*(parent / name for name in parent_names), *(raw / name for name in raw_names)]
    required.append(detection)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise NoneBackfillError("required backfill input is missing: " + ", ".join(missing))
    shards = tuple((raw / "raw_shards").glob("*.json"))
    if not shards:
        raise NoneBackfillError("raw revision contains no JSON shards")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite none backfill revision: {output}")


def _single_int(connection: Any, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None:
        raise NoneBackfillError("validation query returned no row")
    return int(row[0])


def build_none_backfill_revision(
    *,
    parent: Path,
    raw_revision: Path,
    detection_file: Path,
    output: Path,
    revision: str,
) -> NoneBackfillResult:
    """Fill absent none keys from raw evidence without replacing parent observations."""

    parent = parent.resolve()
    raw = raw_revision.resolve()
    detection = detection_file.resolve()
    output = output.resolve()
    if not revision.strip():
        raise NoneBackfillError("revision must not be empty")
    _require_inputs(parent, raw, detection, output)
    parent_verified = _verify_checksum_ledger(parent)
    raw_verified = _verify_checksum_ledger(raw)
    raw_meta = _load_object(raw / "META.json")
    if raw_meta.get("promoted") is not False:
        raise NoneBackfillError("raw acquisition input must be an unpromoted revision")
    if raw_meta.get("kind") != "raw_none_acquisition_candidate":
        raise NoneBackfillError("raw acquisition input has an unexpected kind")
    declared_detection = str(raw_meta.get("detection_parquet_sha256", ""))
    if _sha256(detection) != declared_detection:
        raise NoneBackfillError("detection parquet does not match raw revision lineage")

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise NoneBackfillError("P1 dependencies are missing; run make bootstrap-p1") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        parent_none = _source(parent / "daily_ohlcv_none.parquet")
        scope = _source(raw / "scope_ledger_276.parquet")
        detection_source = _source(detection)
        shard_glob = _sql_string(str(raw / "raw_shards" / "*.json"))
        connection.execute(f"CREATE VIEW parent_none AS SELECT * FROM {parent_none}")
        connection.execute(f"CREATE VIEW scope AS SELECT * FROM {scope}")
        connection.execute(
            f"CREATE VIEW raw_shards AS SELECT * FROM read_json_auto({shard_glob})"
        )
        connection.execute(
            f"""
            CREATE VIEW detected AS
            SELECT security_id, count(*) AS affected_rows
            FROM {detection_source}
            WHERE reason_code = 'prior_close_missing_fail_closed'
            GROUP BY security_id
            """
        )

        affected_securities = _single_int(connection, "SELECT count(*) FROM detected")
        affected_execution_rows = _single_int(
            connection, "SELECT coalesce(sum(affected_rows), 0) FROM detected"
        )
        scope_rows = _single_int(connection, "SELECT count(*) FROM scope")
        scope_mismatch = _single_int(
            connection,
            """
            SELECT count(*) FROM (
                (SELECT security_id FROM detected EXCEPT SELECT security_id FROM scope)
                UNION ALL
                (SELECT security_id FROM scope EXCEPT SELECT security_id FROM detected)
            )
            """,
        )
        if scope_rows != affected_securities or scope_mismatch:
            raise NoneBackfillError("raw scope does not match the immutable detection set")
        if affected_securities != 276 or affected_execution_rows != 639009:
            raise NoneBackfillError("detection counts do not match the frozen #38 contract")
        nonterminal = _single_int(
            connection,
            "SELECT count(*) FROM scope WHERE terminal <> 'fetched' OR provider_error_code <> '0'",
        )
        if nonterminal:
            raise NoneBackfillError("raw scope includes unfinished acquisition results")
        shard_count = _single_int(connection, "SELECT count(*) FROM raw_shards")
        declared_raw_rows = _single_int(
            connection, "SELECT coalesce(sum(n_rows), 0) FROM raw_shards"
        )
        if shard_count != affected_securities:
            raise NoneBackfillError("raw shard count does not match affected securities")
        shard_scope_mismatch = _single_int(
            connection,
            """
            SELECT count(*) FROM (
                (SELECT security_id FROM raw_shards EXCEPT SELECT security_id FROM scope)
                UNION ALL
                (SELECT security_id FROM scope EXCEPT SELECT security_id FROM raw_shards)
            )
            """,
        )
        if shard_scope_mismatch:
            raise NoneBackfillError("raw shards do not match the frozen scope")
        bad_contracts = _single_int(
            connection,
            """
            SELECT count(*) FROM raw_shards
            WHERE provider <> 'baostock' OR frequency <> 'd' OR adjustflag <> '3'
               OR meta.error_code <> '0' OR meta.request.adjustflag <> '3'
               OR meta.request.code <> bs_code
            """,
        )
        if bad_contracts:
            raise NoneBackfillError("raw shard source contract is invalid")

        raw_rows_query = f"""
            SELECT
                security_id,
                stock_code,
                exchange AS identity_exchange,
                try_cast(row[1] AS DATE) AS date,
                try_cast(nullif(row[3], '') AS DOUBLE) AS open,
                try_cast(nullif(row[4], '') AS DOUBLE) AS high,
                try_cast(nullif(row[5], '') AS DOUBLE) AS low,
                try_cast(nullif(row[6], '') AS DOUBLE) AS close,
                CASE WHEN row[7] = '' AND row[12] = '0' THEN 0.0
                     ELSE try_cast(nullif(row[7], '') AS DOUBLE) END AS volume,
                CASE WHEN row[8] = '' AND row[12] = '0' THEN 0.0
                     ELSE try_cast(nullif(row[8], '') AS DOUBLE) END AS amount,
                try_cast(nullif(row[10], '') AS DOUBLE) AS turnover_rate,
                try_cast(nullif(row[11], '') AS DOUBLE) AS pct_change,
                'none' AS adj_mode,
                {_sql_string(revision)} AS adj_version,
                'baostock.query_history_k_data_plus.adjustflag=3' AS source,
                CAST(meta.request.fetched_at_utc AS TIMESTAMPTZ) AS built_at,
                row[2] AS provider_code,
                row[9] AS row_adjustflag,
                row[12] AS tradestatus,
                CAST(request_window.start AS DATE) AS request_start,
                CAST(request_window.end AS DATE) AS request_end
            FROM raw_shards, unnest(rows) AS expanded(row)
        """
        connection.execute(f"CREATE VIEW normalized_raw_all AS {raw_rows_query}")
        raw_rows = _single_int(connection, "SELECT count(*) FROM normalized_raw_all")
        if raw_rows != declared_raw_rows:
            raise NoneBackfillError("raw row count does not match shard declarations")
        invalid_raw = _single_int(
            connection,
            """
            SELECT count(*) FROM normalized_raw_all
            WHERE date IS NULL OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
               OR volume IS NULL OR amount IS NULL OR open < 0 OR high < 0 OR low < 0
               OR close < 0 OR volume < 0 OR amount < 0 OR low > high
               OR open < low OR open > high OR close < low OR close > high
               OR row_adjustflag <> '3'
               OR provider_code <> CASE WHEN identity_exchange = 'SSE'
                                        THEN 'sh.' || stock_code ELSE 'sz.' || stock_code END
               OR date < request_start OR date > request_end
            """,
        )
        if invalid_raw:
            raise NoneBackfillError(f"raw acquisition contains {invalid_raw} invalid rows")
        raw_duplicates = _single_int(
            connection,
            """
            SELECT count(*) FROM (
                SELECT security_id, date FROM normalized_raw_all
                GROUP BY security_id, date HAVING count(*) > 1
            )
            """,
        )
        if raw_duplicates:
            raise NoneBackfillError("raw acquisition contains duplicate security/date keys")
        connection.execute(
            """
            CREATE VIEW normalized_raw AS
            SELECT * EXCLUDE (
                provider_code, row_adjustflag, tradestatus, request_start, request_end
            )
            FROM normalized_raw_all
            """
        )
        parent_rows = _single_int(connection, "SELECT count(*) FROM parent_none")
        overlap_rows = _single_int(
            connection,
            """
            SELECT count(*) FROM normalized_raw r
            JOIN parent_none p USING (security_id, date)
            """,
        )
        inserted_rows = raw_rows - overlap_rows
        merged_query = """
            SELECT * FROM parent_none
            UNION ALL
            SELECT r.* FROM normalized_raw r
            WHERE NOT EXISTS (
                SELECT 1 FROM parent_none p
                WHERE p.security_id = r.security_id AND p.date = r.date
            )
        """
        output_rows = _single_int(connection, f"SELECT count(*) FROM ({merged_query})")
        if output_rows != parent_rows + inserted_rows:
            raise NoneBackfillError("merged none row count does not reconcile")
        merged_duplicates = _single_int(
            connection,
            f"""
            SELECT count(*) FROM (
                SELECT security_id, date FROM ({merged_query})
                GROUP BY security_id, date HAVING count(*) > 1
            )
            """,
        )
        if merged_duplicates:
            raise NoneBackfillError("merged none revision contains duplicate keys")
        connection.execute(
            f"COPY (SELECT * FROM ({merged_query}) ORDER BY security_id, date, stock_code) "
            f"TO {_sql_string(str(staging / 'daily_ohlcv_none.parquet'))} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        connection.close()

    linked_names = (
        "daily_universe.parquet",
        "daily_isST.parquet",
        "st_status_pit.parquet",
        "ref_security_code_validity.parquet",
    )
    try:
        for name in linked_names:
            os.link(parent / name, staging / name)
        data_names = ("daily_ohlcv_none.parquet", *linked_names)
        checksums = {name: f"sha256:{_sha256(staging / name)}" for name in data_names}
        meta = {
            "revision": revision,
            "kind": "oci_none_backfill_child",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "parent_revision": parent.name,
            "raw_revision": raw.name,
            "detection_revision": raw_meta.get("detection_revision"),
            "lineage": {
                "parent": str(parent),
                "raw_revision": str(raw),
                "detection_file": str(detection),
                "detection_sha256": f"sha256:{_sha256(detection)}",
            },
            "merge_policy": "retain_parent_and_fill_absent_security_date_keys",
            "normalization": {
                "blank_volume_amount_on_nontrading_rows": "zero",
                "source_adjustflag": "3",
                "identity_contract": "parent_versioned_code_validity",
            },
            "counts": {
                "affected_securities": affected_securities,
                "affected_execution_rows": affected_execution_rows,
                "parent_rows": parent_rows,
                "raw_rows": raw_rows,
                "overlap_rows_retained_from_parent": overlap_rows,
                "inserted_rows": inserted_rows,
                "output_rows": output_rows,
                "parent_files_verified": parent_verified,
                "raw_files_verified": raw_verified,
            },
            "constraints": {
                "duplicate_security_date": 0,
                "invalid_inserted_rows": 0,
                "no_parent_overwrite": True,
                "no_current_switch": True,
                "no_pg_publication": True,
            },
            "checksums": checksums,
        }
        (staging / "META.json").write_text(
            json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "CHECKSUMS.sha256").write_text(
            "".join(
                f"{checksum.removeprefix('sha256:')}  {name}\n"
                for name, checksum in sorted(checksums.items())
            ),
            encoding="utf-8",
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return NoneBackfillResult(
        revision=revision,
        output=output,
        affected_securities=affected_securities,
        affected_execution_rows=affected_execution_rows,
        parent_rows=parent_rows,
        raw_rows=raw_rows,
        overlap_rows=overlap_rows,
        inserted_rows=inserted_rows,
        output_rows=output_rows,
        checksums=checksums,
    )
