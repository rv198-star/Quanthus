"""Reproducible Hist-1D overlap and listing/session coverage census."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256

BASE251_MANIFEST = "8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef"
VALUE_FIELDS = ("open", "high", "low", "close", "volume", "turnover", "currency")


def overlap_query() -> str:
    differences = " OR ".join(f'o."{f}" IS DISTINCT FROM n."{f}"' for f in VALUE_FIELDS)
    columns = ", ".join(f'o."{f}" AS old_{f}, n."{f}" AS new_{f}' for f in VALUE_FIELDS)
    return f"""
        SELECT coalesce(o.listing_id,n.listing_id) AS listing_id,
               coalesce(o.observation_start,n.observation_start) AS observation_start,
               CASE WHEN o.listing_id IS NULL THEN 'ABSENT_IN_BASE23'
                    WHEN n.listing_id IS NULL THEN 'ABSENT_IN_BASE251'
                    ELSE 'VALUE_DIFFERENCE' END AS difference_type,
               {columns}
        FROM (SELECT * FROM old_daily WHERE trade_date BETWEEN DATE '2020-01-02'
              AND DATE '2021-12-31') o
        FULL JOIN new_daily n USING (listing_id,observation_start,interval_size,price_basis)
        WHERE o.listing_id IS NULL OR n.listing_id IS NULL OR ({differences})
    """


def coverage_query() -> str:
    return """
        SELECT e.*,
          CASE WHEN b.rows > 0 THEN
                    CASE WHEN b.zero_rows = b.rows THEN 'SOURCE_ZERO_VOLUME_BAR'
                         ELSE 'OBSERVED_BAR' END
               WHEN EXISTS (SELECT 1 FROM lifecycle s
                    WHERE s.listing_id=e.listing_id AND s.knowledge_to IS NULL
                    AND s.status_type='SUSPENSION'
                    AND s.status_value='SUSPENDED' AND s.effective_from<=e.open_at
                    AND (s.effective_to IS NULL OR e.open_at<s.effective_to))
                    THEN 'EVIDENCED_SUSPENSION'
               ELSE 'MISSING' END AS coverage_status
        FROM expected e LEFT JOIN bar_presence b USING (listing_id,trade_date)
    """


def _rows(connection: Any, sql: str) -> list[dict[str, Any]]:
    result = connection.execute(sql)
    names = [str(item[0]) for item in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def _export(connection: Any, sql: str, path: Path) -> None:
    escaped = str(path).replace("'", "''")
    connection.execute(f"COPY ({sql}) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")


def audit(
    workspace: Path, base251: Path, output: Path, hist_root: Path | None = None
) -> dict[str, Any]:
    if file_sha256(base251 / "manifest.json") != BASE251_MANIFEST:
        raise ValueError("BASE251_REFERENCE_IDENTITY_MISMATCH")
    receipt = json.loads((workspace / "RECOVERY-RECEIPT.json").read_bytes())
    if receipt["status"] != "PASS_SOURCE_RECOVERY":
        raise ValueError("SOURCE_RECOVERY_NOT_VERIFIED")
    source = workspace / "raw/base23/extracted"
    for name, size, digest in receipt["members"]:
        path = source / name
        if path.stat().st_size != size or file_sha256(path) != digest:
            raise ValueError("RESTORED_SOURCE_CHANGED")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    duckdb = importlib.import_module("duckdb")
    scratch = output / "scratch"
    scratch.mkdir()
    connection = duckdb.connect(
        str(scratch / "audit.duckdb"), config={"threads": 2, "memory_limit": "2GB"}
    )
    connection.execute("SET TimeZone='Asia/Shanghai'")
    connection.execute("SET preserve_insertion_order=false")

    def bind(name: str, table_root: Path, years: set[int] | None = None) -> None:
        files = sorted(table_root.rglob("*.parquet"))
        if years is not None:
            files = [
                p for p in files if any(f"observation_start_year={y}/" in str(p) for y in years)
            ]
        if not files:
            raise ValueError("AUDIT_TABLE_UNAVAILABLE:" + name)
        connection.read_parquet([str(p) for p in files], hive_partitioning=False).create_view(name)

    if hist_root is None:
        bind("old_raw", source / "DWD/payload/dwd_security_bar")
    else:
        bind("hist_bars", hist_root / "payload/dwd_security_bar")
        bind("archive_overlap", source / "DWD/payload/dwd_security_bar", {2020, 2021})
        connection.execute(
            "CREATE TEMP VIEW old_raw AS SELECT * FROM hist_bars "
            "UNION ALL SELECT * FROM archive_overlap"
        )
    bind("new_raw", base251 / "payload/dwd_security_bar/interval_size=1d", {2020, 2021})
    bind("listing_raw", base251 / "payload/dwd_listing")
    if hist_root is None:
        bind("lifecycle", base251 / "payload/dwd_listing_status_interval")
    else:
        bind("base_lifecycle", base251 / "payload/dwd_listing_status_interval")
        bind("supp_lifecycle", hist_root / "payload/dwd_listing_status_interval")
        connection.execute("""CREATE TEMP VIEW lifecycle AS SELECT * FROM base_lifecycle
            UNION ALL SELECT * FROM supp_lifecycle
            WHERE record_source='baostock.query_history_k_data_plus'
            AND status_type='SUSPENSION'""")
    bind("calendar", base251 / "payload/dwd_trading_calendar")
    connection.execute("""CREATE TABLE old_daily AS SELECT listing_id,calendar_session_id,
        observation_start,interval_size,price_basis,currency,open,high,low,close,volume,turnover,
        CAST(observation_start AS DATE) AS trade_date FROM old_raw
        WHERE knowledge_to IS NULL AND interval_size='1d' AND price_basis='UNADJUSTED'""")
    connection.execute("""CREATE TEMP VIEW new_daily AS SELECT * FROM new_raw
        WHERE knowledge_to IS NULL AND interval_size='1d' AND price_basis='UNADJUSTED'""")
    connection.execute("""CREATE TABLE listings AS SELECT DISTINCT
        listing_id,security_id,venue_id,listing_code,board_code
        FROM listing_raw WHERE knowledge_to IS NULL AND listing_currency='CNY'
        AND board_code IN ('SSE_main','SZ_main','ChiNext','CHINEXT','STAR')""")
    connection.execute("""CREATE TABLE expected AS SELECT DISTINCT
        l.listing_id,l.security_id,l.listing_code,l.board_code,c.trade_date,c.open_at
        FROM listings l JOIN lifecycle s ON s.listing_id=l.listing_id
        JOIN calendar c ON c.venue_id=l.venue_id
        WHERE s.status_type='LISTING_LIFECYCLE' AND s.status_value='LISTED'
        AND s.knowledge_to IS NULL AND c.knowledge_to IS NULL
        AND c.is_open AND c.session_type='REGULAR_DAY'
        AND c.trade_date BETWEEN DATE '1990-12-19' AND DATE '2019-12-31'
        AND s.effective_from<=c.open_at AND (s.effective_to IS NULL OR c.open_at<s.effective_to)""")
    connection.execute("""CREATE TABLE bar_presence AS SELECT listing_id,trade_date,
        count(*) AS rows,count(*) FILTER (WHERE volume=0) AS zero_rows
        FROM old_daily GROUP BY ALL""")
    connection.execute("CREATE TABLE coverage AS " + coverage_query())
    connection.execute("CREATE TABLE overlap_differences AS " + overlap_query())
    summary: dict[str, Any] = {
        "status": "AUDIT_COMPLETED",
        "base251_manifest_sha256": BASE251_MANIFEST,
        "daily_census": _rows(
            connection,
            """SELECT year(trade_date) AS year,count(*) AS rows,
            count(DISTINCT listing_id) AS listings,
            count(*) FILTER (WHERE volume=0) AS zero_volume_rows
            FROM old_daily GROUP BY ALL ORDER BY year""",
        ),
        "duplicates": _rows(
            connection,
            """SELECT 'base23' AS source,count(*) AS duplicate_keys FROM
            (SELECT listing_id,observation_start,interval_size,price_basis,count(*) AS n
             FROM old_daily GROUP BY ALL HAVING n>1)
            UNION ALL SELECT 'base251',count(*) FROM
            (SELECT listing_id,observation_start,interval_size,price_basis,count(*) AS n
             FROM new_daily GROUP BY ALL HAVING n>1)""",
        ),
        "overlap_differences": _rows(
            connection,
            """SELECT difference_type,count(*) AS rows
            FROM overlap_differences GROUP BY ALL ORDER BY difference_type""",
        ),
        "coverage": _rows(
            connection,
            """SELECT
            CASE WHEN trade_date>=DATE '2005-01-01' THEN 'RESEARCH' ELSE 'REFERENCE' END AS grade,
            coverage_status,count(*) AS listing_sessions FROM coverage GROUP BY ALL ORDER BY ALL""",
        ),
        "source_universe_differences": _rows(
            connection,
            """SELECT count(DISTINCT b.listing_id) AS
            listings,count(*) AS rows FROM old_daily b LEFT JOIN listings l USING(listing_id)
            WHERE b.trade_date<=DATE '2019-12-31' AND l.listing_id IS NULL""",
        ),
        "delisted_coverage": _rows(
            connection,
            """SELECT count(*) AS expected_delisted,
            count(*) FILTER(WHERE bar_rows>0) AS with_bars FROM
            (SELECT l.listing_id,count(b.listing_id) AS bar_rows
             FROM listings l JOIN lifecycle s ON s.listing_id=l.listing_id
             LEFT JOIN bar_presence b ON b.listing_id=l.listing_id
             AND b.trade_date<=DATE '2019-12-31'
             WHERE s.knowledge_to IS NULL AND s.status_type='LISTING_LIFECYCLE'
             AND s.status_value='LISTED' AND s.effective_to>=TIMESTAMPTZ '1990-12-19 00:00:00+08'
             AND s.effective_to<=TIMESTAMPTZ '2020-01-01 00:00:00+08' GROUP BY l.listing_id)""",
        ),
    }
    _export(connection, "SELECT * FROM overlap_differences", output / "overlap-differences.parquet")
    _export(
        connection,
        "SELECT * FROM coverage WHERE coverage_status='MISSING'",
        output / "missing-listing-sessions.parquet",
    )
    _export(
        connection,
        """SELECT year(trade_date) AS year,board_code,coverage_status,count(*) AS
        listing_sessions FROM coverage GROUP BY ALL ORDER BY ALL""",
        output / "coverage-by-year-board.parquet",
    )
    _export(
        connection,
        """SELECT b.* FROM old_daily b LEFT JOIN listings l USING(listing_id)
        WHERE b.trade_date<=DATE '2019-12-31' AND l.listing_id IS NULL""",
        output / "source-universe-differences.parquet",
    )
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if hist_root is not None:
        bind("product_status", hist_root / "payload/dwd_security_status_interval")
        summary["historical_status"] = _rows(
            connection,
            """SELECT status_type,status_value,
            reason_code,count(*) AS intervals,count(DISTINCT security_id) AS securities
            FROM product_status WHERE effective_from<TIMESTAMPTZ '2011-01-01 00:00:00+08'
            GROUP BY ALL ORDER BY ALL""",
        )
    summary["ledgers"] = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": file_sha256(p)}
        for p in sorted(output.glob("*.parquet"))
    ]
    (output / "AUDIT.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    connection.close()
    shutil.rmtree(scratch)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--base251", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--hist-root", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(audit(args.workspace, args.base251, args.output, args.hist_root), default=str),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
