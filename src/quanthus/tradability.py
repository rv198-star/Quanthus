"""Build the P1 security-calendar coverage matrix without mutating raw data."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.universe import UniversePolicy


class CoverageError(RuntimeError):
    """Raised when an input cannot produce a deterministic coverage matrix."""


@dataclass(frozen=True)
class TradabilityCoverageResult:
    """Metadata for an immutable foundational tradability coverage artifact."""

    output: Path
    row_count: int
    status_counts: Mapping[str, int]
    valid_bar_count: int
    source_dataset_version: str
    source_checksum: str
    output_sha256: str
    knowledge_cutoff: date
    universe_policy_version: str
    universe_policy_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "row_count": self.row_count,
            "status_counts": dict(sorted(self.status_counts.items())),
            "valid_bar_count": self.valid_bar_count,
            "source_dataset_version": self.source_dataset_version,
            "source_checksum": self.source_checksum,
            "output_sha256": self.output_sha256,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "universe_policy_version": self.universe_policy_version,
            "universe_policy_digest": self.universe_policy_digest,
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


def _manifest_details(connection: Any) -> tuple[str, str]:
    row = connection.execute(
        """
        SELECT version, checksum, adj_mode
        FROM manifest
        WHERE dataset = 'daily_ohlcv_none'
        ORDER BY built_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise CoverageError("dataset_manifest has no daily_ohlcv_none entry")
    version, checksum, adj_mode = (str(value) for value in row)
    if adj_mode != "none":
        raise CoverageError(f"daily_ohlcv_none manifest has adj_mode={adj_mode!r}, expected 'none'")
    if not checksum.strip():
        raise CoverageError("daily_ohlcv_none manifest checksum must not be empty")
    return version, checksum


def _validate_paths(root: Path, bars_file: str, output: Path) -> tuple[Path, ...]:
    if Path(bars_file).name != bars_file or bars_file != "daily_ohlcv_none.parquet":
        raise CoverageError("P1 coverage input must be daily_ohlcv_none.parquet")
    paths = (
        root / bars_file,
        root / "dataset_manifest.parquet",
        root / "ref_security_master.parquet",
        root / "ref_suspensions.parquet",
        root / "ref_trading_calendar.parquet",
    )
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise CoverageError(f"required raw files are missing: {', '.join(sorted(missing))}")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite coverage output: {output}")
    return paths


def _validate_source_keys(connection: Any) -> None:
    checks = {
        "daily bars": """
            SELECT count(*) FROM (
                SELECT stock_code, CAST(date AS DATE) AS market_date
                FROM bars
                GROUP BY stock_code, CAST(date AS DATE)
                HAVING count(*) > 1
            )
        """,
        "security master": """
            SELECT count(*) FROM (
                SELECT stock_code
                FROM master
                GROUP BY stock_code
                HAVING count(*) > 1
            )
        """,
        "trading calendar": """
            SELECT count(*) FROM (
                SELECT exchange, CAST(trade_date AS DATE) AS market_date
                FROM calendar
                GROUP BY exchange, CAST(trade_date AS DATE)
                HAVING count(*) > 1
            )
        """,
    }
    duplicate_sources = [
        name for name, query in checks.items() if int(connection.execute(query).fetchone()[0]) > 0
    ]
    if duplicate_sources:
        raise CoverageError(
            "coverage requires unique source keys; duplicates in "
            + ", ".join(sorted(duplicate_sources))
        )


def _coverage_query(
    universe_policy: UniversePolicy,
    knowledge_cutoff: date,
) -> str:
    exchanges = ", ".join(_sql_string(exchange) for exchange in universe_policy.allowed_exchanges)
    exclusions = ", ".join(
        _sql_string(security_id) for security_id in sorted(universe_policy.excluded_security_ids)
    )
    exclusion_clause = f"AND security_id NOT IN ({exclusions})" if exclusions else ""
    cutoff = _sql_string(knowledge_cutoff.isoformat())
    return f"""
        WITH universe AS (
            SELECT
                security_id,
                stock_code,
                exchange,
                CAST(list_date AS DATE) AS list_date,
                CAST(delist_date AS DATE) AS delist_date
            FROM master
            WHERE exchange IN ({exchanges})
            {exclusion_clause}
        ), sessions AS (
            SELECT exchange, CAST(trade_date AS DATE) AS market_date
            FROM calendar
            WHERE is_open = 1
        ), bar_rows AS (
            SELECT
                stock_code,
                CAST(date AS DATE) AS market_date,
                CASE
                    WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                      OR volume IS NULL OR amount IS NULL
                      OR open < 0 OR high < 0 OR low < 0 OR close < 0
                      OR volume < 0 OR amount < 0
                      OR low > high OR open < low OR open > high
                      OR close < low OR close > high
                    THEN FALSE
                    ELSE TRUE
                END AS data_valid
            FROM bars
        ), classified AS (
            SELECT
                u.security_id,
                u.stock_code,
                u.exchange,
                s.market_date,
                b.stock_code IS NOT NULL AS bar_present,
                coalesce(b.data_valid, FALSE) AS data_valid,
                CASE
                    WHEN s.market_date < u.list_date THEN 'not_listed'
                    WHEN u.delist_date IS NOT NULL AND s.market_date > u.delist_date THEN 'delisted'
                    WHEN b.stock_code IS NOT NULL THEN 'trading'
                    WHEN EXISTS (
                        SELECT 1
                        FROM suspensions sp
                        WHERE sp.stock_code = u.stock_code
                          AND CAST(sp.suspend_start AS DATE) <= s.market_date
                          AND (
                              sp.suspend_end IS NULL
                              OR CAST(sp.suspend_end AS DATE) >= s.market_date
                          )
                          AND CAST(sp.knowledge_cutoff AS DATE) <= CAST({cutoff} AS DATE)
                    ) THEN 'suspended'
                    ELSE 'missing'
                END AS coverage_status,
                CASE
                    WHEN s.market_date < u.list_date THEN 'before_list_date'
                    WHEN u.delist_date IS NOT NULL
                     AND s.market_date > u.delist_date THEN 'after_delist_date'
                    WHEN b.stock_code IS NOT NULL AND NOT b.data_valid THEN 'invalid_bar'
                    WHEN b.stock_code IS NOT NULL THEN 'bar_present'
                    WHEN EXISTS (
                        SELECT 1
                        FROM suspensions sp
                        WHERE sp.stock_code = u.stock_code
                          AND CAST(sp.suspend_start AS DATE) <= s.market_date
                          AND (
                              sp.suspend_end IS NULL
                              OR CAST(sp.suspend_end AS DATE) >= s.market_date
                          )
                          AND CAST(sp.knowledge_cutoff AS DATE) <= CAST({cutoff} AS DATE)
                    ) THEN 'suspension_record'
                    ELSE 'bar_missing_without_suspension'
                END AS reason_code,
                u.list_date,
                u.delist_date
            FROM universe u
            JOIN sessions s USING (exchange)
            LEFT JOIN bar_rows b
              ON b.stock_code = u.stock_code
             AND b.market_date = s.market_date
        )
        SELECT
            security_id,
            stock_code,
            exchange,
            market_date,
            coverage_status,
            bar_present,
            data_valid,
            reason_code,
            sum(
                CASE
                    WHEN market_date >= list_date
                     AND (delist_date IS NULL OR market_date <= delist_date) THEN 1
                    ELSE 0
                END
            )
                OVER (
                    PARTITION BY security_id
                    ORDER BY market_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS list_days,
            CAST({cutoff} AS DATE) AS knowledge_cutoff,
            {_sql_string(universe_policy.version)} AS universe_policy_version
        FROM classified
    """


def build_tradability_coverage(
    root: Path,
    output: Path,
    universe_policy: UniversePolicy,
    knowledge_cutoff: date,
    bars_file: str = "daily_ohlcv_none.parquet",
) -> TradabilityCoverageResult:
    """Materialize P1 lifecycle coverage using only input known by ``knowledge_cutoff``.

    This foundational artifact does not assert historical ST state or price limits;
    those remain S2/S3 enrichments.  It does classify every in-scope security/open-session
    pair and retains an invalid observed bar with ``data_valid=false``.
    """

    root = root.resolve()
    output = output.resolve()
    bars_path, manifest_path, master_path, suspensions_path, calendar_path = _validate_paths(
        root, bars_file, output
    )
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise CoverageError("P1 dependencies are missing; run `make bootstrap-p1`") from exc

    connection = duckdb.connect(":memory:")
    try:
        views = {
            "bars": bars_path,
            "manifest": manifest_path,
            "master": master_path,
            "suspensions": suspensions_path,
            "calendar": calendar_path,
        }
        for name, path in views.items():
            connection.execute(f"CREATE VIEW {name} AS SELECT * FROM {_parquet_source(path)}")
        _validate_source_keys(connection)
        source_dataset_version, source_checksum = _manifest_details(connection)
        query = _coverage_query(universe_policy, knowledge_cutoff)
        status_rows = connection.execute(
            f"SELECT coverage_status, count(*) FROM ({query}) GROUP BY coverage_status"
        ).fetchall()
        status_counts = {str(status): int(count) for status, count in status_rows}
        row_count = sum(status_counts.values())
        valid_count_row = connection.execute(
            f"SELECT count(*) FROM ({query}) WHERE data_valid"
        ).fetchone()
        if valid_count_row is None:
            raise CoverageError("coverage validity query returned no row")
        valid_bar_count = int(valid_count_row[0])
        output.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{output.stem}.", dir=output.parent))
        staging_path = staging_dir / output.name
        try:
            connection.execute(
                f"COPY ({query}) TO {_sql_string(str(staging_path))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            os.replace(staging_path, output)
            os.rmdir(staging_dir)
        except BaseException:
            # Preserve failed staging output for diagnosis; never replace a published artifact.
            raise
    finally:
        connection.close()

    return TradabilityCoverageResult(
        output=output,
        row_count=row_count,
        status_counts=status_counts,
        valid_bar_count=valid_bar_count,
        source_dataset_version=source_dataset_version,
        source_checksum=source_checksum,
        output_sha256=_sha256(output),
        knowledge_cutoff=knowledge_cutoff,
        universe_policy_version=universe_policy.version,
        universe_policy_digest=universe_policy.digest,
    )
