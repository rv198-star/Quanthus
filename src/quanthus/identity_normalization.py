"""OCI DWD build for point-in-time security identity normalization."""

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


class IdentityNormalizationError(RuntimeError):
    """Raised when a code-validity child revision cannot be built safely."""


@dataclass(frozen=True)
class IdentityNormalizationResult:
    revision: str
    output: Path
    none_row_count: int
    universe_row_count: int
    s2_daily_row_count: int
    s2_interval_row_count: int
    checksums: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "none_row_count": self.none_row_count,
            "universe_row_count": self.universe_row_count,
            "s2_daily_row_count": self.s2_daily_row_count,
            "s2_interval_row_count": self.s2_interval_row_count,
            "checksums": dict(sorted(self.checksums.items())),
        }


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _parquet_source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _copy_query(connection: Any, query: str, output: Path) -> int:
    connection.execute(
        f"COPY ({query}) TO {_sql_string(str(output))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    row = connection.execute(f"SELECT count(*) FROM ({query})").fetchone()
    if row is None:
        raise IdentityNormalizationError("row-count query returned no row")
    return int(row[0])


def _duplicate_count(connection: Any, source: str, key: str) -> int:
    row = connection.execute(
        f"SELECT count(*) FROM (SELECT {key} FROM {source} GROUP BY {key} HAVING count(*) > 1)"
    ).fetchone()
    if row is None:
        raise IdentityNormalizationError("duplicate-key query returned no row")
    return int(row[0])


def _validate_inputs(root: Path, validity: Path, candidate: Path, output: Path) -> None:
    required = (
        root / "daily_ohlcv_none.parquet",
        root / "ref_trading_calendar.parquet",
        validity,
        candidate / "daily_isST.parquet",
        candidate / "st_status_pit_candidate.parquet",
        candidate / "META.json",
        candidate / "CHECKSUMS.sha256",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise IdentityNormalizationError("required input files are missing: " + ", ".join(missing))
    if output.exists():
        raise FileExistsError(f"refusing to overwrite identity-normalization output: {output}")


def build_identity_normalized_revision(
    *,
    root: Path,
    code_validity: Path,
    s2_candidate: Path,
    output: Path,
    revision: str,
) -> IdentityNormalizationResult:
    """Create an immutable OCI child revision without refetching provider data.

    ``daily_ohlcv_none`` and the S2 daily candidate are cropped to the exact code
    intervals.  Every resulting daily dataset carries an economic ``security_id``;
    the S2 interval output is recompressed from the cropped daily rows so an alias
    boundary cannot retain an invalid pre-change segment.
    """

    root = root.resolve()
    validity = code_validity.resolve()
    candidate = s2_candidate.resolve()
    output = output.resolve()
    if not revision.strip():
        raise IdentityNormalizationError("revision must not be empty")
    _validate_inputs(root, validity, candidate, output)
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise IdentityNormalizationError(
            "P1 dependencies are missing; run make bootstrap-p1"
        ) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(f"CREATE VIEW validity AS SELECT * FROM {_parquet_source(validity)}")
        bars_source = _parquet_source(root / "daily_ohlcv_none.parquet")
        calendar_source = _parquet_source(root / "ref_trading_calendar.parquet")
        s2_daily_source = _parquet_source(candidate / "daily_isST.parquet")
        s2_interval_source = _parquet_source(candidate / "st_status_pit_candidate.parquet")
        connection.execute(f"CREATE VIEW bars AS SELECT * FROM {bars_source}")
        connection.execute(f"CREATE VIEW calendar AS SELECT * FROM {calendar_source}")
        connection.execute(f"CREATE VIEW s2_daily_source AS SELECT * FROM {s2_daily_source}")
        connection.execute(f"CREATE VIEW s2_interval_source AS SELECT * FROM {s2_interval_source}")

        required_columns = {
            "security_id",
            "stock_code",
            "exchange",
            "valid_from",
            "valid_to",
            "mapping_version",
            "mapping_digest",
        }
        available_columns = {
            str(row[0]) for row in connection.execute("DESCRIBE validity").fetchall()
        }
        missing_columns = sorted(required_columns - available_columns)
        if missing_columns:
            raise IdentityNormalizationError(
                "code validity table is missing required columns: " + ", ".join(missing_columns)
            )

        validity_code_duplicates = _duplicate_count(
            connection,
            "validity",
            "stock_code, exchange, valid_from",
        )
        if validity_code_duplicates:
            raise IdentityNormalizationError("code validity has duplicate code/start keys")
        interval_overlaps = int(
            connection.execute(
                """
                SELECT count(*)
                FROM validity left_interval
                JOIN validity right_interval
                  ON left_interval.security_id = right_interval.security_id
                 AND left_interval.exchange = right_interval.exchange
                 AND (
                       CAST(left_interval.valid_from AS DATE)
                           < CAST(right_interval.valid_from AS DATE)
                    OR (
                           CAST(left_interval.valid_from AS DATE)
                               = CAST(right_interval.valid_from AS DATE)
                       AND left_interval.stock_code < right_interval.stock_code
                    )
                 )
                 AND CAST(left_interval.valid_from AS DATE) <= coalesce(
                       CAST(right_interval.valid_to AS DATE), DATE '9999-12-31'
                 )
                 AND CAST(right_interval.valid_from AS DATE) <= coalesce(
                       CAST(left_interval.valid_to AS DATE), DATE '9999-12-31'
                 )
                """
            ).fetchone()[0]
        )
        if interval_overlaps:
            raise IdentityNormalizationError("code validity has overlapping alias intervals")

        normalized_none = """
            SELECT
                validity.security_id,
                bars.stock_code,
                validity.exchange AS identity_exchange,
                bars.* EXCLUDE (stock_code)
            FROM bars
            JOIN validity
              ON validity.stock_code = bars.stock_code
             AND CAST(bars.date AS DATE) >= CAST(validity.valid_from AS DATE)
             AND (
                    validity.valid_to IS NULL
                 OR CAST(bars.date AS DATE) <= CAST(validity.valid_to AS DATE)
             )
        """
        connection.execute(f"CREATE VIEW normalized_none AS {normalized_none}")
        duplicate_none = _duplicate_count(connection, "normalized_none", "security_id, date")
        if duplicate_none:
            raise IdentityNormalizationError(
                "normalization would create duplicate (security_id, date) none bars"
            )
        none_row_count = _copy_query(
            connection,
            "SELECT * FROM normalized_none ORDER BY security_id, date, stock_code",
            staging / "daily_ohlcv_none.parquet",
        )

        daily_universe = """
            SELECT
                validity.security_id,
                validity.stock_code,
                validity.exchange,
                CAST(calendar.trade_date AS DATE) AS market_date,
                CAST(validity.valid_from AS DATE) AS valid_from,
                CAST(validity.valid_to AS DATE) AS valid_to,
                validity.mapping_version,
                validity.mapping_digest
            FROM validity
            JOIN calendar
              ON calendar.exchange = validity.exchange
             AND calendar.is_open = 1
             AND CAST(calendar.trade_date AS DATE) >= CAST(validity.valid_from AS DATE)
             AND (
                    validity.valid_to IS NULL
                 OR CAST(calendar.trade_date AS DATE) <= CAST(validity.valid_to AS DATE)
             )
            WHERE validity.exchange IN ('SSE', 'SZSE')
        """
        connection.execute(f"CREATE VIEW normalized_universe AS {daily_universe}")
        duplicate_universe = _duplicate_count(
            connection,
            "normalized_universe",
            "security_id, market_date",
        )
        if duplicate_universe:
            raise IdentityNormalizationError(
                "normalization would create duplicate (security_id, market_date) universe rows"
            )
        universe_row_count = _copy_query(
            connection,
            "SELECT * FROM normalized_universe ORDER BY security_id, market_date, stock_code",
            staging / "daily_universe.parquet",
        )

        normalized_s2_daily = """
            SELECT
                validity.security_id,
                s2_daily_source.*
            FROM s2_daily_source
            JOIN validity
              ON validity.stock_code = s2_daily_source.stock_code
             AND validity.exchange = s2_daily_source.exchange
             AND CAST(s2_daily_source.date AS DATE) >= CAST(validity.valid_from AS DATE)
             AND (
                    validity.valid_to IS NULL
                 OR CAST(s2_daily_source.date AS DATE) <= CAST(validity.valid_to AS DATE)
             )
        """
        connection.execute(f"CREATE VIEW normalized_s2_daily AS {normalized_s2_daily}")
        duplicate_s2_daily = _duplicate_count(
            connection,
            "normalized_s2_daily",
            "security_id, date",
        )
        if duplicate_s2_daily:
            raise IdentityNormalizationError(
                "normalization would create duplicate (security_id, date) S2 rows"
            )
        s2_daily_row_count = _copy_query(
            connection,
            "SELECT * FROM normalized_s2_daily ORDER BY security_id, date, stock_code",
            staging / "daily_isST.parquet",
        )

        cropped_intervals = f"""
            WITH cropped AS (
                SELECT
                    validity.security_id,
                    s2_interval_source.stock_code,
                    s2_interval_source.exchange,
                    s2_interval_source.status_label,
                    s2_interval_source.is_st_class,
                    greatest(
                        CAST(s2_interval_source.effective_start AS DATE),
                        CAST(validity.valid_from AS DATE)
                    ) AS effective_start,
                    least(
                        CAST(s2_interval_source.effective_end AS DATE),
                        coalesce(CAST(validity.valid_to AS DATE), DATE '9999-12-31')
                    ) AS effective_end,
                    CAST(s2_interval_source.knowledge_cutoff AS DATE) AS parent_knowledge_cutoff,
                    s2_interval_source.source,
                    s2_interval_source.raw_shard
                FROM s2_interval_source
                JOIN validity
                  ON validity.stock_code = s2_interval_source.stock_code
                 AND validity.exchange = s2_interval_source.exchange
                 AND CAST(s2_interval_source.effective_start AS DATE) <= coalesce(
                       CAST(validity.valid_to AS DATE), DATE '9999-12-31'
                 )
                 AND CAST(s2_interval_source.effective_end AS DATE)
                       >= CAST(validity.valid_from AS DATE)
            )
            SELECT
                security_id,
                stock_code,
                exchange,
                status_label,
                is_st_class,
                effective_start,
                effective_end,
                greatest(
                    parent_knowledge_cutoff,
                    CAST(effective_start + INTERVAL 1 DAY AS DATE)
                ) AS knowledge_cutoff,
                source,
                {_sql_string(revision)} AS source_revision,
                raw_shard,
                effective_start AS source_market_date_start
            FROM cropped
            WHERE effective_start <= effective_end
        """
        connection.execute(f"CREATE VIEW normalized_s2_intervals AS {cropped_intervals}")
        duplicate_s2_intervals = _duplicate_count(
            connection,
            "normalized_s2_intervals",
            "security_id, effective_start",
        )
        if duplicate_s2_intervals:
            raise IdentityNormalizationError(
                "normalization would create duplicate (security_id, effective_start) S2 intervals"
            )
        s2_interval_row_count = _copy_query(
            connection,
            (
                "SELECT * FROM normalized_s2_intervals "
                "ORDER BY security_id, effective_start, stock_code"
            ),
            staging / "st_status_pit.parquet",
        )
        _copy_query(
            connection,
            "SELECT * FROM validity ORDER BY security_id, valid_from, stock_code",
            staging / "ref_security_code_validity.parquet",
        )
    finally:
        connection.close()

    try:
        checksums = {
            name: _sha256(staging / name)
            for name in (
                "daily_ohlcv_none.parquet",
                "daily_universe.parquet",
                "daily_isST.parquet",
                "st_status_pit.parquet",
                "ref_security_code_validity.parquet",
            )
        }
        meta = {
            "revision": revision,
            "kind": "oci_security_identity_code_validity_child",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "parent_inputs": {
                "daily_ohlcv_none": str(root / "daily_ohlcv_none.parquet"),
                "s2_candidate": str(s2_candidate),
                "s2_raw_shards": str(s2_candidate / "raw_shards"),
                "s2_checksums": str(s2_candidate / "CHECKSUMS.sha256"),
                "code_validity": str(code_validity),
            },
            "constraints": {
                "no_raw_refetch": True,
                "no_parent_overwrite": True,
                "no_current_switch": True,
                "no_pg_publication": True,
                "duplicate_security_market_date": 0,
            },
            "rows": {
                "daily_ohlcv_none": none_row_count,
                "daily_universe": universe_row_count,
                "daily_isST": s2_daily_row_count,
                "st_status_pit": s2_interval_row_count,
            },
            "checksums": checksums,
        }
        (staging / "META.json").write_text(
            json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksum_lines = [
            f"{checksum.removeprefix('sha256:')}  {name}"
            for name, checksum in sorted(checksums.items())
        ]
        (staging / "CHECKSUMS.sha256").write_text(
            "\n".join(checksum_lines) + "\n", encoding="utf-8"
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return IdentityNormalizationResult(
        revision=revision,
        output=output,
        none_row_count=none_row_count,
        universe_row_count=universe_row_count,
        s2_daily_row_count=s2_daily_row_count,
        s2_interval_row_count=s2_interval_row_count,
        checksums=checksums,
    )
