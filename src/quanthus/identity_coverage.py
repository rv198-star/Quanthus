"""C2 and S1 revalidation against a normalized security-identity revision."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class IdentityCoverageError(RuntimeError):
    """Raised when a normalized identity revision cannot support C2/S1 evidence."""


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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class IdentityCoverageResult:
    coverage_path: Path
    coverage_rows: int
    status_counts: dict[str, int]
    source_checksum: str
    s1_report_path: Path
    s1_summary: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_path": str(self.coverage_path),
            "coverage_rows": self.coverage_rows,
            "status_counts": dict(sorted(self.status_counts.items())),
            "source_checksum": self.source_checksum,
            "s1_report_path": str(self.s1_report_path),
            "s1_summary": dict(sorted(self.s1_summary.items())),
        }


def build_identity_coverage(
    *,
    revision_root: Path,
    reference_root: Path,
    knowledge_cutoff: date,
    coverage_output: Path,
    coverage_metadata_output: Path,
    s1_report_output: Path,
) -> IdentityCoverageResult:
    """Build C2 and S1 evidence from the exact normalized identity universe.

    ``daily_universe`` is already restricted to valid code intervals, so every row
    represents one economic security and one exchange session.  This prevents an
    alias from creating a second lifecycle row while retaining the original code for
    suspension and terminal-boundary evidence joins.
    """

    revision_root = revision_root.resolve()
    reference_root = reference_root.resolve()
    coverage_output = coverage_output.resolve()
    coverage_metadata_output = coverage_metadata_output.resolve()
    s1_report_output = s1_report_output.resolve()
    required_revision = (
        revision_root / "daily_ohlcv_none.parquet",
        revision_root / "daily_universe.parquet",
    )
    required_reference = (
        reference_root / "ref_suspensions.parquet",
        reference_root / "ref_security_master.parquet",
        reference_root / "ref_delist_day_boundary_exceptions.parquet",
    )
    missing = [
        str(path) for path in (*required_revision, *required_reference) if not path.is_file()
    ]
    if missing:
        raise IdentityCoverageError(
            "required identity coverage input is missing: " + ", ".join(missing)
        )
    if coverage_output.exists():
        raise FileExistsError(f"refusing to overwrite identity coverage output: {coverage_output}")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise IdentityCoverageError("P1 dependencies are missing; run make bootstrap-p1") from exc

    none_path, universe_path = required_revision
    suspensions_path, master_path, terminal_path = required_reference
    source_checksum = _sha256(none_path).removeprefix("sha256:")
    connection = duckdb.connect(":memory:")
    try:
        sources = {
            "bars": none_path,
            "universe": universe_path,
            "suspensions": suspensions_path,
            "master": master_path,
            "terminal": terminal_path,
        }
        for name, path in sources.items():
            connection.execute(f"CREATE VIEW {name} AS SELECT * FROM {_parquet_source(path)}")
        cutoff = _sql_string(knowledge_cutoff.isoformat())
        classified = f"""
            SELECT
                u.security_id,
                u.stock_code,
                u.exchange,
                u.market_date,
                b.stock_code IS NOT NULL AS bar_present,
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
                b.volume,
                b.amount,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM suspensions sp
                        WHERE sp.stock_code = u.stock_code
                          AND CAST(sp.suspend_start AS DATE) <= u.market_date
                          AND (
                               sp.suspend_end IS NULL
                               OR CAST(sp.suspend_end AS DATE) >= u.market_date
                          )
                          AND CAST(sp.knowledge_cutoff AS DATE) <= CAST({cutoff} AS DATE)
                    ) THEN 'suspended'
                    WHEN b.stock_code IS NOT NULL THEN 'trading'
                    ELSE 'missing'
                END AS coverage_status
            FROM universe u
            LEFT JOIN bars b
              ON b.security_id = u.security_id
             AND b.stock_code = u.stock_code
             AND CAST(b.date AS DATE) = u.market_date
        """
        connection.execute(f"CREATE VIEW identity_coverage AS {classified}")
        duplicate_rows = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT security_id, market_date
                    FROM identity_coverage
                    GROUP BY security_id, market_date
                    HAVING count(*) > 1
                )
                """
            ).fetchone()[0]
        )
        if duplicate_rows:
            raise IdentityCoverageError("identity coverage contains duplicate security/date rows")
        coverage_query = """
            SELECT
                security_id,
                stock_code,
                exchange,
                market_date,
                coverage_status,
                bar_present,
                coalesce(data_valid, FALSE) AS data_valid,
                CASE
                    WHEN coverage_status = 'trading'
                     AND data_valid
                     AND volume > 0
                     AND amount > 0 THEN TRUE
                    ELSE FALSE
                END AS is_executable,
                CASE
                    WHEN coverage_status = 'suspended' THEN 'suspension_record'
                    WHEN coverage_status = 'trading' AND NOT data_valid THEN 'invalid_bar'
                    WHEN coverage_status = 'trading' AND (volume = 0 OR amount = 0)
                    THEN 'zero_liquidity'
                    WHEN coverage_status = 'trading' THEN 'bar_present'
                    ELSE 'bar_missing_without_suspension'
                END AS reason_code,
                CAST(? AS DATE) AS knowledge_cutoff
            FROM identity_coverage
        """
        count_row = connection.execute(
            f"SELECT count(*) FROM ({coverage_query})", [knowledge_cutoff]
        ).fetchone()
        if count_row is None:
            raise IdentityCoverageError("identity coverage row-count query returned no row")
        coverage_rows = int(count_row[0])
        status_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                (
                    "SELECT coverage_status, count(*) "
                    f"FROM ({coverage_query}) GROUP BY coverage_status"
                ),
                [knowledge_cutoff],
            ).fetchall()
        }
        coverage_output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{coverage_output.stem}.", dir=coverage_output.parent)
        )
        staging_output = staging / coverage_output.name
        try:
            connection.execute(
                f"COPY ({coverage_query}) TO {_sql_string(str(staging_output))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)",
                [knowledge_cutoff],
            )
            os.replace(staging_output, coverage_output)
            os.rmdir(staging)
        except BaseException:
            raise

        s1_query = f"""
            WITH scope AS (
                SELECT u.security_id, u.stock_code, u.exchange,
                       CAST(m.list_date AS DATE) AS list_date,
                       CAST(m.delist_date AS DATE) AS delist_date
                FROM universe u
                JOIN master m USING (stock_code)
                WHERE m.delist_date IS NOT NULL AND u.exchange IN ('SSE', 'SZSE')
                GROUP BY u.security_id, u.stock_code, u.exchange, m.list_date, m.delist_date
            ), expected AS (
                SELECT u.security_id, u.stock_code, u.exchange, u.market_date
                FROM universe u JOIN scope s USING (security_id, stock_code, exchange)
            ), classified_s1 AS (
                SELECT e.*, b.stock_code IS NOT NULL AS bar_present,
                    coalesce(c.data_valid, FALSE) AS data_valid,
                    EXISTS (
                        SELECT 1 FROM suspensions sp
                        WHERE sp.stock_code=e.stock_code
                          AND CAST(sp.suspend_start AS DATE) <= e.market_date
                          AND (
                               sp.suspend_end IS NULL
                               OR CAST(sp.suspend_end AS DATE) >= e.market_date
                          )
                          AND CAST(sp.knowledge_cutoff AS DATE) <= CAST({cutoff} AS DATE)
                    ) AS suspension_known,
                    EXISTS (
                        SELECT 1 FROM terminal tb
                        WHERE tb.stock_code=e.stock_code AND tb.exchange=e.exchange
                          AND CAST(tb.market_date AS DATE)=e.market_date
                          AND tb.exception_type='terminal_boundary'
                          AND CAST(tb.knowledge_cutoff AS DATE) <= CAST({cutoff} AS DATE)
                    ) AS terminal_known
                FROM expected e
                LEFT JOIN bars b ON b.security_id=e.security_id AND b.stock_code=e.stock_code
                    AND CAST(b.date AS DATE)=e.market_date
                LEFT JOIN identity_coverage c
                    ON c.security_id=e.security_id AND c.stock_code=e.stock_code
                    AND c.market_date=e.market_date
            )
            SELECT s.security_id, s.stock_code, s.exchange, s.list_date, s.delist_date,
                count(c.market_date) AS expected_session_count,
                coalesce(
                    sum(CASE WHEN c.bar_present THEN 1 ELSE 0 END), 0
                ) AS observed_session_count,
                coalesce(
                    sum(CASE WHEN NOT c.bar_present AND c.suspension_known THEN 1 ELSE 0 END), 0
                ) AS suspended_session_count,
                coalesce(
                    sum(CASE
                        WHEN NOT c.bar_present AND NOT c.suspension_known AND c.terminal_known
                        THEN 1 ELSE 0 END
                    ), 0
                ) AS terminal_boundary_session_count,
                coalesce(
                    sum(CASE
                        WHEN NOT c.bar_present AND NOT c.suspension_known AND NOT c.terminal_known
                        THEN 1 ELSE 0 END
                    ), 0
                ) AS unexplained_missing_session_count,
                coalesce(
                    sum(CASE WHEN c.bar_present AND NOT c.data_valid THEN 1 ELSE 0 END), 0
                ) AS invalid_observed_session_count
            FROM scope s LEFT JOIN classified_s1 c USING (security_id, stock_code, exchange)
            GROUP BY s.security_id, s.stock_code, s.exchange, s.list_date, s.delist_date
            ORDER BY s.security_id, s.stock_code
        """
        s1_rows = connection.execute(s1_query).fetchall()
    finally:
        connection.close()

    records: list[dict[str, Any]] = []
    states: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for row in s1_rows:
        expected, observed, suspended, terminal, unexplained, invalid = (
            int(value) for value in row[5:]
        )
        if unexplained:
            state = "incomplete"
        elif invalid:
            state = "complete_with_invalid_bars"
        elif terminal:
            state = "complete_with_terminal_boundary"
        elif observed == 0 and suspended:
            state = "suspension_only"
        else:
            state = "complete"
        states[state] += 1
        totals.update(
            expected_session_count=expected,
            observed_session_count=observed,
            suspended_session_count=suspended,
            terminal_boundary_session_count=terminal,
            unexplained_missing_session_count=unexplained,
            invalid_observed_session_count=invalid,
        )
        records.append(
            {
                "security_id": str(row[0]),
                "stock_code": str(row[1]),
                "exchange": str(row[2]),
                "list_date": row[3].isoformat(),
                "delist_date": row[4].isoformat(),
                "expected_session_count": expected,
                "observed_session_count": observed,
                "suspended_session_count": suspended,
                "terminal_boundary_session_count": terminal,
                "unexplained_missing_session_count": unexplained,
                "invalid_observed_session_count": invalid,
                "coverage_state": state,
            }
        )
    s1_summary = dict(totals)
    s1_summary["reference_delisted_security_count"] = len(records)
    s1_summary["in_history_window_security_count"] = sum(
        record["expected_session_count"] > 0 for record in records
    )
    s1_summary["coverage_states"] = dict(sorted(states.items()))
    s1_payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(revision_root),
        "source_dataset_version": revision_root.name,
        "source_checksum": source_checksum,
        "knowledge_cutoff": knowledge_cutoff.isoformat(),
        "summary": s1_summary,
        "records": records,
    }
    _atomic_json(s1_report_output, s1_payload)
    _atomic_json(
        coverage_metadata_output,
        {
            "output": str(coverage_output),
            "row_count": coverage_rows,
            "status_counts": dict(sorted(status_counts.items())),
            "source_checksum": source_checksum,
            "source_dataset_version": revision_root.name,
            "knowledge_cutoff": knowledge_cutoff.isoformat(),
            "output_sha256": _sha256(coverage_output),
        },
    )
    return IdentityCoverageResult(
        coverage_path=coverage_output,
        coverage_rows=coverage_rows,
        status_counts=status_counts,
        source_checksum=source_checksum,
        s1_report_path=s1_report_output,
        s1_summary=s1_summary,
    )
