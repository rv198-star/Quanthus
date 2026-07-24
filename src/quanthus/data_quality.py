"""Read-only quality checks for the local P1 data foundation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class AuditError(RuntimeError):
    """Raised when an audit cannot run against the supplied data root."""


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    count: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "count": self.count,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DataAuditReport:
    generated_at: datetime
    source_root: Path
    bars_file: str
    dataset_stats: Mapping[str, Mapping[str, Any]]
    checks: tuple[CheckResult, ...]

    @property
    def has_failures(self) -> bool:
        return any(check.status is CheckStatus.FAIL for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        summary = {
            status.value: sum(check.status is status for check in self.checks)
            for status in CheckStatus
        }
        return {
            "schema_version": 1,
            "generated_at": self.generated_at.isoformat(),
            "source_root": str(self.source_root),
            "bars_file": self.bars_file,
            "summary": summary,
            "dataset_stats": {name: dict(stats) for name, stats in self.dataset_stats.items()},
            "checks": [check.to_dict() for check in self.checks],
        }

    def write_json(self, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=True, indent=2, sort_keys=True)
        output.write_text(f"{payload}\n", encoding="utf-8")


_REQUIRED_FILES = (
    "dataset_manifest.parquet",
    "qa_data_gaps.parquet",
    "ref_corporate_actions.parquet",
    "ref_security_master.parquet",
    "ref_trading_calendar.parquet",
)


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _parquet_source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _scalar(connection: Any, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise AuditError("audit query returned no row")
    return int(row[0])


def _status(count: int, *, warning: bool = False) -> CheckStatus:
    if count == 0:
        return CheckStatus.PASS
    return CheckStatus.WARN if warning else CheckStatus.FAIL


def _check(check_id: str, count: int, detail: str, *, warning: bool = False) -> CheckResult:
    return CheckResult(check_id, _status(count, warning=warning), count, detail)


def _validate_files(root: Path, bars_file: str) -> None:
    if Path(bars_file).name != bars_file:
        raise AuditError("bars_file must be a file name, not a path")
    required = (bars_file, *_REQUIRED_FILES)
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise AuditError(f"required data files are missing: {', '.join(sorted(missing))}")


def _register_views(connection: Any, root: Path, bars_file: str) -> None:
    views = {
        "bars": root / bars_file,
        "manifest": root / "dataset_manifest.parquet",
        "gaps": root / "qa_data_gaps.parquet",
        "actions": root / "ref_corporate_actions.parquet",
        "master": root / "ref_security_master.parquet",
        "calendar": root / "ref_trading_calendar.parquet",
    }
    for view, path in views.items():
        connection.execute(f"CREATE VIEW {view} AS SELECT * FROM {_parquet_source(path)}")


def _dataset_stats(connection: Any) -> dict[str, Mapping[str, Any]]:
    bars = connection.execute(
        """
        SELECT count(*), count(DISTINCT stock_code), min(CAST(date AS DATE)),
               max(CAST(date AS DATE))
        FROM bars
        """
    ).fetchone()
    master = connection.execute(
        "SELECT count(*), count(DISTINCT security_id), count(DISTINCT stock_code) FROM master"
    ).fetchone()
    calendar = connection.execute(
        """
        SELECT count(*), sum(CASE WHEN is_open = 1 THEN 1 ELSE 0 END),
               min(trade_date), max(trade_date)
        FROM calendar
        """
    ).fetchone()
    if bars is None or master is None or calendar is None:
        raise AuditError("dataset statistics query returned no row")
    return {
        "bars": {
            "row_count": int(bars[0]),
            "security_count": int(bars[1]),
            "date_min": str(bars[2]),
            "date_max": str(bars[3]),
        },
        "security_master": {
            "row_count": int(master[0]),
            "security_id_count": int(master[1]),
            "stock_code_count": int(master[2]),
        },
        "trading_calendar": {
            "row_count": int(calendar[0]),
            "open_session_count": int(calendar[1]),
            "date_min": str(calendar[2]),
            "date_max": str(calendar[3]),
        },
    }


def _run_checks(
    connection: Any,
    bars_file: str,
    stats: Mapping[str, Mapping[str, Any]],
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    bars_rows = int(stats["bars"]["row_count"])
    manifest_dataset = "daily_ohlcv_none" if "none" in bars_file else "daily_ohlcv"
    manifest_row = connection.execute(
        "SELECT row_count FROM manifest WHERE dataset = ? ORDER BY built_at DESC LIMIT 1",
        [manifest_dataset],
    ).fetchone()
    if manifest_row is None:
        checks.append(
            CheckResult(
                "manifest.bars_present",
                CheckStatus.WARN,
                1,
                f"manifest has no {manifest_dataset} entry",
            )
        )
    else:
        difference = abs(bars_rows - int(manifest_row[0]))
        checks.append(
            _check(
                "manifest.bars_row_count",
                difference,
                f"actual={bars_rows}, manifest={int(manifest_row[0])}",
            )
        )

    checks.extend(
        [
            _check(
                "bars.primary_key_not_null",
                _scalar(
                    connection,
                    "SELECT count(*) FROM bars WHERE stock_code IS NULL OR date IS NULL",
                ),
                "stock_code and date must both be present",
            ),
            _check(
                "bars.primary_key_unique",
                _scalar(
                    connection,
                    """
                    SELECT coalesce(sum(duplicates), 0)
                    FROM (
                        SELECT count(*) - 1 AS duplicates
                        FROM bars
                        GROUP BY stock_code, date
                        HAVING count(*) > 1
                    )
                    """,
                ),
                "duplicate rows beyond the first row for each (stock_code, date)",
            ),
            _check(
                "bars.ohlc_invariant",
                _scalar(
                    connection,
                    """
                    SELECT count(*) FROM bars
                    WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                       OR open < 0 OR high < 0 OR low < 0 OR close < 0
                       OR low > high OR open < low OR open > high
                       OR close < low OR close > high
                    """,
                ),
                "rows violating non-negative low <= open/close <= high",
            ),
            _check(
                "bars.volume_not_null",
                _scalar(connection, "SELECT count(*) FROM bars WHERE volume IS NULL"),
                "rows with null volume",
            ),
            _check(
                "bars.amount_not_null",
                _scalar(connection, "SELECT count(*) FROM bars WHERE amount IS NULL"),
                "rows with null amount",
            ),
            _check(
                "bars.volume_non_negative",
                _scalar(connection, "SELECT count(*) FROM bars WHERE volume < 0"),
                "rows with negative volume",
            ),
            _check(
                "bars.amount_non_negative",
                _scalar(connection, "SELECT count(*) FROM bars WHERE amount < 0"),
                "rows with negative amount",
            ),
            _check(
                "bars.security_known",
                _scalar(
                    connection,
                    """
                    SELECT count(*) FROM bars b
                    LEFT JOIN master m USING (stock_code)
                    WHERE m.security_id IS NULL
                    """,
                ),
                "bar rows whose stock_code is absent from security_master",
            ),
            _check(
                "bars.inside_security_lifecycle",
                _scalar(
                    connection,
                    """
                    SELECT count(*) FROM bars b
                    JOIN master m USING (stock_code)
                    WHERE m.exchange IN ('SSE', 'SZSE')
                      AND (
                          CAST(b.date AS DATE) < m.list_date
                          OR (
                              m.delist_date IS NOT NULL
                              AND CAST(b.date AS DATE) > m.delist_date
                          )
                      )
                    """,
                ),
                "SSE/SZSE bar rows before list_date or after delist_date",
            ),
            _check(
                "bars.on_open_exchange_session",
                _scalar(
                    connection,
                    """
                    SELECT count(*) FROM bars b
                    JOIN master m USING (stock_code)
                    LEFT JOIN calendar c
                      ON c.exchange = m.exchange
                     AND c.trade_date = CAST(b.date AS DATE)
                    WHERE m.exchange IN ('SSE', 'SZSE')
                      AND (c.trade_date IS NULL OR c.is_open <> 1)
                    """,
                ),
                "SSE/SZSE bar rows not mapped to an open exchange session",
            ),
            _check(
                "scope.bse_rows_excluded_from_curated",
                _scalar(
                    connection,
                    """
                    SELECT count(*) FROM bars b
                    JOIN master m USING (stock_code)
                    WHERE m.exchange = 'BSE'
                    """,
                ),
                "raw BSE rows must be filtered because Quanthus P1 covers SSE/SZSE only",
                warning=True,
            ),
            _check(
                "security_master.primary_key_unique",
                _scalar(
                    connection,
                    """
                    SELECT coalesce(sum(duplicates), 0)
                    FROM (
                        SELECT count(*) - 1 AS duplicates
                        FROM master
                        GROUP BY security_id
                        HAVING count(*) > 1
                    )
                    """,
                ),
                "duplicate security_id rows beyond the first row",
            ),
            _check(
                "trading_calendar.primary_key_unique",
                _scalar(
                    connection,
                    """
                    SELECT coalesce(sum(duplicates), 0)
                    FROM (
                        SELECT count(*) - 1 AS duplicates
                        FROM calendar
                        GROUP BY exchange, trade_date
                        HAVING count(*) > 1
                    )
                    """,
                ),
                "duplicate (exchange, trade_date) rows beyond the first row",
            ),
            _check(
                "trading_calendar.valid_session_flag",
                _scalar(connection, "SELECT count(*) FROM calendar WHERE is_open NOT IN (0, 1)"),
                "calendar rows whose is_open value is not 0 or 1",
            ),
            _check(
                "corporate_actions.primary_key_unique",
                _scalar(
                    connection,
                    """
                    SELECT coalesce(sum(duplicates), 0)
                    FROM (
                        SELECT count(*) - 1 AS duplicates
                        FROM actions
                        GROUP BY action_id
                        HAVING count(*) > 1
                    )
                    """,
                ),
                "duplicate action_id rows beyond the first row",
            ),
            _check(
                "data_gaps.invalid_ohlc_reconciled",
                _scalar(
                    connection,
                    """
                    WITH invalid AS (
                        SELECT stock_code, CAST(date AS DATE) AS trade_date
                        FROM bars
                        WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                           OR open < 0 OR high < 0 OR low < 0 OR close < 0
                           OR low > high OR open < low OR open > high
                           OR close < low OR close > high
                    ), recorded AS (
                        SELECT stock_code, trade_date
                        FROM gaps
                        WHERE dataset = 'daily_ohlcv' AND gap_type = 'invalid_ohlc'
                    ), difference AS (
                        (SELECT * FROM invalid EXCEPT SELECT * FROM recorded)
                        UNION ALL
                        (SELECT * FROM recorded EXCEPT SELECT * FROM invalid)
                    )
                    SELECT count(*) FROM difference
                    """,
                ),
                "symmetric difference between invalid OHLC rows and qa_data_gaps",
            ),
        ]
    )
    return checks


def run_p1_data_audit(root: Path, bars_file: str = "daily_ohlcv.parquet") -> DataAuditReport:
    """Audit stable local files without mutating the source snapshot."""

    root = root.resolve()
    _validate_files(root, bars_file)
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise AuditError("P1 dependencies are missing; run `make bootstrap-p1`") from exc

    connection = duckdb.connect(":memory:")
    try:
        _register_views(connection, root, bars_file)
        stats = _dataset_stats(connection)
        checks = _run_checks(connection, bars_file, stats)
    finally:
        connection.close()
    return DataAuditReport(
        generated_at=datetime.now(timezone.utc),
        source_root=root,
        bars_file=bars_file,
        dataset_stats=stats,
        checks=tuple(checks),
    )
