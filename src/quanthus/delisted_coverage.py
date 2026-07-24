"""Read-only S1 reconciliation for delisted-security daily-history coverage."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.universe import UniversePolicy


class DelistedCoverageError(RuntimeError):
    """Raised when local inputs cannot support a deterministic S1 reconciliation."""


@dataclass(frozen=True)
class DelistedSecurityCoverage:
    security_id: str
    stock_code: str
    exchange: str
    list_date: date
    delist_date: date
    expected_session_count: int
    observed_session_count: int
    suspended_session_count: int
    terminal_boundary_session_count: int
    unexplained_missing_session_count: int
    invalid_observed_session_count: int
    coverage_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "stock_code": self.stock_code,
            "exchange": self.exchange,
            "list_date": self.list_date.isoformat(),
            "delist_date": self.delist_date.isoformat(),
            "expected_session_count": self.expected_session_count,
            "observed_session_count": self.observed_session_count,
            "suspended_session_count": self.suspended_session_count,
            "terminal_boundary_session_count": self.terminal_boundary_session_count,
            "unexplained_missing_session_count": self.unexplained_missing_session_count,
            "invalid_observed_session_count": self.invalid_observed_session_count,
            "coverage_state": self.coverage_state,
        }


@dataclass(frozen=True)
class DelistedCoverageReport:
    generated_at: datetime
    source_root: Path
    bars_file: str
    source_dataset_version: str
    source_checksum: str
    history_start: date
    history_end: date
    knowledge_cutoff: date
    universe_policy_version: str
    universe_policy_digest: str
    records: tuple[DelistedSecurityCoverage, ...]

    def to_dict(self) -> dict[str, Any]:
        states: dict[str, int] = {}
        for record in self.records:
            states[record.coverage_state] = states.get(record.coverage_state, 0) + 1
        return {
            "schema_version": 1,
            "generated_at": self.generated_at.isoformat(),
            "source_root": str(self.source_root),
            "bars_file": self.bars_file,
            "source_dataset_version": self.source_dataset_version,
            "source_checksum": self.source_checksum,
            "history_window": {
                "start": self.history_start.isoformat(),
                "end": self.history_end.isoformat(),
            },
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "universe_policy_version": self.universe_policy_version,
            "universe_policy_digest": self.universe_policy_digest,
            "summary": {
                "reference_delisted_security_count": len(self.records),
                "in_history_window_security_count": sum(
                    record.expected_session_count > 0 for record in self.records
                ),
                "expected_session_count": sum(
                    record.expected_session_count for record in self.records
                ),
                "observed_session_count": sum(
                    record.observed_session_count for record in self.records
                ),
                "suspended_session_count": sum(
                    record.suspended_session_count for record in self.records
                ),
                "terminal_boundary_session_count": sum(
                    record.terminal_boundary_session_count for record in self.records
                ),
                "unexplained_missing_session_count": sum(
                    record.unexplained_missing_session_count for record in self.records
                ),
                "invalid_observed_session_count": sum(
                    record.invalid_observed_session_count for record in self.records
                ),
                "coverage_states": dict(sorted(states.items())),
            },
            "records": [record.to_dict() for record in self.records],
        }

    def write_json(self, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                payload = json.dumps(self.to_dict(), ensure_ascii=True, indent=2, sort_keys=True)
                stream.write(f"{payload}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, output)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _parquet_source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _validate_paths(root: Path, bars_file: str) -> tuple[Path, Path, Path, Path, Path, Path | None]:
    if Path(bars_file).name != bars_file or bars_file != "daily_ohlcv_none.parquet":
        raise DelistedCoverageError("S1 coverage input must be daily_ohlcv_none.parquet")
    paths = (
        root / bars_file,
        root / "dataset_manifest.parquet",
        root / "ref_security_master.parquet",
        root / "ref_suspensions.parquet",
        root / "ref_trading_calendar.parquet",
    )
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise DelistedCoverageError(
            f"required data files are missing: {', '.join(sorted(missing))}"
        )
    boundaries = root / "ref_delist_day_boundary_exceptions.parquet"
    return (*paths, boundaries if boundaries.is_file() else None)


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
        raise DelistedCoverageError("dataset_manifest has no daily_ohlcv_none entry")
    version, checksum, adj_mode = (str(value) for value in row)
    if adj_mode != "none":
        raise DelistedCoverageError(
            f"daily_ohlcv_none manifest has adj_mode={adj_mode!r}, expected 'none'"
        )
    if not checksum.strip():
        raise DelistedCoverageError("daily_ohlcv_none manifest checksum must not be empty")
    return version, checksum


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
                SELECT security_id
                FROM master
                GROUP BY security_id
                HAVING count(*) > 1
            )
        """,
        "security master codes": """
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
    duplicates = [
        name for name, query in checks.items() if int(connection.execute(query).fetchone()[0]) > 0
    ]
    if duplicates:
        raise DelistedCoverageError(
            "S1 coverage requires unique source keys; duplicates in "
            + ", ".join(sorted(duplicates))
        )


def _validate_terminal_boundaries(connection: Any) -> None:
    duplicate_count = int(
        connection.execute(
            """
            SELECT count(*) FROM (
                SELECT stock_code, CAST(market_date AS DATE) AS market_date
                FROM terminal_boundaries
                GROUP BY stock_code, CAST(market_date AS DATE)
                HAVING count(*) > 1
            )
            """
        ).fetchone()[0]
    )
    if duplicate_count:
        raise DelistedCoverageError(
            "terminal boundary exceptions contain duplicate (stock_code, market_date) keys"
        )

    invalid_count = int(
        connection.execute(
            """
            SELECT count(*)
            FROM terminal_boundaries e
            LEFT JOIN master m USING (stock_code)
            WHERE e.stock_code IS NULL
               OR e.exchange IS NULL
               OR e.market_date IS NULL
               OR e.knowledge_cutoff IS NULL
               OR e.source_revision IS NULL
               OR e.evidence_path IS NULL
               OR e.exception_type <> 'terminal_boundary'
               OR m.security_id IS NULL
               OR e.exchange <> m.exchange
               OR m.delist_date IS NULL
               OR CAST(e.market_date AS DATE) <> CAST(m.delist_date AS DATE)
               OR e.delist_date IS NULL
               OR CAST(e.delist_date AS DATE) <> CAST(m.delist_date AS DATE)
            """
        ).fetchone()[0]
    )
    if invalid_count:
        raise DelistedCoverageError(
            "terminal boundary exceptions must be evidenced exact delist-date "
            "terminal_boundary rows"
        )


def _coverage_query(
    universe_policy: UniversePolicy,
    history_start: date,
    history_end: date,
    knowledge_cutoff: date,
    *,
    has_terminal_boundaries: bool,
) -> str:
    exchanges = ", ".join(_sql_string(exchange) for exchange in universe_policy.allowed_exchanges)
    exclusions = ", ".join(
        _sql_string(security_id) for security_id in sorted(universe_policy.excluded_security_ids)
    )
    exclusion_clause = f"AND security_id NOT IN ({exclusions})" if exclusions else ""
    start = _sql_string(history_start.isoformat())
    end = _sql_string(history_end.isoformat())
    cutoff = _sql_string(knowledge_cutoff.isoformat())
    terminal_boundary_predicate = "FALSE"
    if has_terminal_boundaries:
        terminal_boundary_predicate = f"""
            EXISTS (
                SELECT 1
                FROM terminal_boundaries tb
                WHERE tb.stock_code = e.stock_code
                  AND tb.exchange = e.exchange
                  AND CAST(tb.market_date AS DATE) = e.market_date
                  AND tb.exception_type = 'terminal_boundary'
                  AND CAST(tb.knowledge_cutoff AS DATE) <= CAST({cutoff} AS DATE)
            )
        """
    return f"""
        WITH scope AS (
            SELECT
                security_id,
                stock_code,
                exchange,
                CAST(list_date AS DATE) AS list_date,
                CAST(delist_date AS DATE) AS delist_date
            FROM master
            WHERE exchange IN ({exchanges})
              AND delist_date IS NOT NULL
              {exclusion_clause}
        ), expected AS (
            SELECT
                s.security_id,
                s.stock_code,
                s.exchange,
                s.list_date,
                s.delist_date,
                CAST(c.trade_date AS DATE) AS market_date
            FROM scope s
            JOIN calendar c USING (exchange)
            WHERE c.is_open = 1
              AND CAST(c.trade_date AS DATE) >= CAST({start} AS DATE)
              AND CAST(c.trade_date AS DATE) <= CAST({end} AS DATE)
              AND CAST(c.trade_date AS DATE) >= s.list_date
              AND CAST(c.trade_date AS DATE) <= s.delist_date
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
                e.security_id,
                e.stock_code,
                e.exchange,
                e.list_date,
                e.delist_date,
                e.market_date,
                b.stock_code IS NOT NULL AS bar_present,
                coalesce(b.data_valid, FALSE) AS data_valid,
                EXISTS (
                    SELECT 1
                    FROM suspensions sp
                    WHERE sp.stock_code = e.stock_code
                      AND CAST(sp.suspend_start AS DATE) <= e.market_date
                      AND (
                          sp.suspend_end IS NULL
                          OR CAST(sp.suspend_end AS DATE) >= e.market_date
                      )
                      AND CAST(sp.knowledge_cutoff AS DATE) <= CAST({cutoff} AS DATE)
                ) AS suspension_known,
                {terminal_boundary_predicate} AS terminal_boundary_known
            FROM expected e
            LEFT JOIN bar_rows b
              ON b.stock_code = e.stock_code
             AND b.market_date = e.market_date
        )
        SELECT
            s.security_id,
            s.stock_code,
            s.exchange,
            s.list_date,
            s.delist_date,
            count(c.market_date) AS expected_session_count,
            coalesce(sum(CASE WHEN c.bar_present THEN 1 ELSE 0 END), 0) AS observed_session_count,
            coalesce(
                sum(CASE WHEN NOT c.bar_present AND c.suspension_known THEN 1 ELSE 0 END),
                0
            ) AS suspended_session_count,
            coalesce(
                sum(
                    CASE
                        WHEN NOT c.bar_present
                         AND NOT c.suspension_known
                         AND c.terminal_boundary_known
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS terminal_boundary_session_count,
            coalesce(
                sum(
                    CASE
                        WHEN NOT c.bar_present
                         AND NOT c.suspension_known
                         AND NOT c.terminal_boundary_known
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS unexplained_missing_session_count,
            coalesce(
                sum(CASE WHEN c.bar_present AND NOT c.data_valid THEN 1 ELSE 0 END),
                0
            ) AS invalid_observed_session_count
        FROM scope s
        LEFT JOIN classified c USING (security_id, stock_code, exchange, list_date, delist_date)
        GROUP BY s.security_id, s.stock_code, s.exchange, s.list_date, s.delist_date
        ORDER BY s.security_id
    """


def _coverage_state(
    expected_session_count: int,
    observed_session_count: int,
    suspended_session_count: int,
    terminal_boundary_session_count: int,
    unexplained_missing_session_count: int,
    invalid_observed_session_count: int,
) -> str:
    if expected_session_count == 0:
        return "outside_history_window"
    if unexplained_missing_session_count > 0:
        return "incomplete"
    if invalid_observed_session_count > 0:
        return "complete_with_invalid_bars"
    if terminal_boundary_session_count > 0:
        return "complete_with_terminal_boundary"
    if observed_session_count == 0 and suspended_session_count > 0:
        return "suspension_only"
    return "complete"


def run_delisted_coverage(
    root: Path,
    knowledge_cutoff: date,
    *,
    bars_file: str = "daily_ohlcv_none.parquet",
    universe_policy: UniversePolicy,
) -> DelistedCoverageReport:
    """Measure P1 delisted-history coverage while separating known suspensions from gaps."""

    root = root.resolve()
    (
        bars_path,
        manifest_path,
        master_path,
        suspensions_path,
        calendar_path,
        terminal_boundaries_path,
    ) = _validate_paths(root, bars_file)
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DelistedCoverageError("P1 dependencies are missing; run `make bootstrap-p1`") from exc

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
        if terminal_boundaries_path is not None:
            connection.execute(
                "CREATE VIEW terminal_boundaries AS SELECT * FROM "
                f"{_parquet_source(terminal_boundaries_path)}"
            )
        _validate_source_keys(connection)
        if terminal_boundaries_path is not None:
            _validate_terminal_boundaries(connection)
        source_dataset_version, source_checksum = _manifest_details(connection)
        history_row = connection.execute(
            "SELECT min(CAST(date AS DATE)), max(CAST(date AS DATE)) FROM bars"
        ).fetchone()
        if history_row is None or history_row[0] is None or history_row[1] is None:
            raise DelistedCoverageError("daily_ohlcv_none has no history window")
        history_start, history_end = history_row
        rows = connection.execute(
            _coverage_query(
                universe_policy,
                history_start,
                history_end,
                knowledge_cutoff,
                has_terminal_boundaries=terminal_boundaries_path is not None,
            )
        ).fetchall()
    finally:
        connection.close()

    records = tuple(
        DelistedSecurityCoverage(
            security_id=str(row[0]),
            stock_code=str(row[1]),
            exchange=str(row[2]),
            list_date=row[3],
            delist_date=row[4],
            expected_session_count=int(row[5]),
            observed_session_count=int(row[6]),
            suspended_session_count=int(row[7]),
            terminal_boundary_session_count=int(row[8]),
            unexplained_missing_session_count=int(row[9]),
            invalid_observed_session_count=int(row[10]),
            coverage_state=_coverage_state(
                int(row[5]),
                int(row[6]),
                int(row[7]),
                int(row[8]),
                int(row[9]),
                int(row[10]),
            ),
        )
        for row in rows
    )
    return DelistedCoverageReport(
        generated_at=datetime.now(timezone.utc),
        source_root=root,
        bars_file=bars_file,
        source_dataset_version=source_dataset_version,
        source_checksum=source_checksum,
        history_start=history_start,
        history_end=history_end,
        knowledge_cutoff=knowledge_cutoff,
        universe_policy_version=universe_policy.version,
        universe_policy_digest=universe_policy.digest,
        records=records,
    )
