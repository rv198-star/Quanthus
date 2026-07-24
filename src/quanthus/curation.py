"""Atomic construction of the P1 curated daily dataset from stable raw inputs."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.universe import UniversePolicy


class CurationError(RuntimeError):
    """Raised when a raw input cannot safely produce a curated dataset."""


@dataclass(frozen=True)
class CuratedDailyResult:
    output: Path
    row_count: int
    valid_row_count: int
    source_dataset_version: str
    source_checksum: str
    source_sha256: str
    output_sha256: str
    universe_policy_version: str
    universe_policy_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "row_count": self.row_count,
            "valid_row_count": self.valid_row_count,
            "source_dataset_version": self.source_dataset_version,
            "source_checksum": self.source_checksum,
            "source_sha256": self.source_sha256,
            "output_sha256": self.output_sha256,
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
        raise CurationError("dataset_manifest has no daily_ohlcv_none entry")
    version, checksum, adj_mode = (str(value) for value in row)
    if adj_mode != "none":
        raise CurationError(f"daily_ohlcv_none manifest has adj_mode={adj_mode!r}, expected 'none'")
    if not checksum.strip():
        raise CurationError("daily_ohlcv_none manifest checksum must not be empty")
    return version, checksum


def _validate_paths(root: Path, bars_file: str, output: Path) -> tuple[Path, Path, Path]:
    if Path(bars_file).name != bars_file or bars_file != "daily_ohlcv_none.parquet":
        raise CurationError("P1 curated daily input must be daily_ohlcv_none.parquet")
    bars_path = root / bars_file
    master_path = root / "ref_security_master.parquet"
    manifest_path = root / "dataset_manifest.parquet"
    missing = [path.name for path in (bars_path, master_path, manifest_path) if not path.is_file()]
    if missing:
        raise CurationError(f"required raw files are missing: {', '.join(sorted(missing))}")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite curated output: {output}")
    return bars_path, master_path, manifest_path


def build_curated_daily(
    root: Path,
    output: Path,
    universe_policy: UniversePolicy,
    bars_file: str = "daily_ohlcv_none.parquet",
) -> CuratedDailyResult:
    """Build a filtered daily view without correcting or dropping raw invalid bars."""

    root = root.resolve()
    output = output.resolve()
    bars_path, master_path, manifest_path = _validate_paths(root, bars_file, output)
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise CurationError("P1 dependencies are missing; run `make bootstrap-p1`") from exc

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(f"CREATE VIEW bars AS SELECT * FROM {_parquet_source(bars_path)}")
        connection.execute(f"CREATE VIEW master AS SELECT * FROM {_parquet_source(master_path)}")
        connection.execute(
            f"CREATE VIEW manifest AS SELECT * FROM {_parquet_source(manifest_path)}"
        )
        source_version, source_checksum = _manifest_details(connection)
        exchange_values = ", ".join(
            _sql_string(exchange) for exchange in universe_policy.allowed_exchanges
        )
        exclusions = ", ".join(
            _sql_string(security_id)
            for security_id in sorted(universe_policy.excluded_security_ids)
        )
        exclusion_clause = f"AND m.security_id NOT IN ({exclusions})" if exclusions else ""
        query = f"""
            SELECT
                m.security_id,
                b.stock_code,
                m.exchange,
                CAST(b.date AS DATE) AS market_date,
                b.open,
                b.high,
                b.low,
                b.close,
                b.volume,
                b.amount,
                CASE
                    WHEN b.open IS NULL OR b.high IS NULL OR b.low IS NULL OR b.close IS NULL
                      OR b.volume IS NULL OR b.amount IS NULL
                      OR b.open < 0 OR b.high < 0 OR b.low < 0 OR b.close < 0
                      OR b.volume < 0 OR b.amount < 0
                      OR b.low > b.high OR b.open < b.low OR b.open > b.high
                      OR b.close < b.low OR b.close > b.high
                    THEN FALSE
                    ELSE TRUE
                END AS data_valid,
                {_sql_string(source_version)} AS source_dataset_version,
                {_sql_string(universe_policy.version)} AS universe_policy_version
            FROM bars b
            JOIN master m USING (stock_code)
            WHERE m.exchange IN ({exchange_values})
            {exclusion_clause}
        """
        row = connection.execute(
            f"SELECT count(*), sum(CASE WHEN data_valid THEN 1 ELSE 0 END) FROM ({query})"
        ).fetchone()
        if row is None:
            raise CurationError("curated row-count query returned no row")
        row_count, valid_row_count = int(row[0]), int(row[1])
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
            # Preserve the staging directory for diagnosis after a failed materialization.
            raise
    finally:
        connection.close()

    return CuratedDailyResult(
        output=output,
        row_count=row_count,
        valid_row_count=valid_row_count,
        source_dataset_version=source_version,
        source_checksum=source_checksum,
        source_sha256=_sha256(bars_path),
        output_sha256=_sha256(output),
        universe_policy_version=universe_policy.version,
        universe_policy_digest=universe_policy.digest,
    )
