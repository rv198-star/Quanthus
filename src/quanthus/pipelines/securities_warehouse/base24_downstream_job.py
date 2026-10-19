"""Bounded monthly ownership transfer from Base 2.4 native 5m projection to DWD."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .base22 import canonical, digest, file_sha
from .base22_sources import physical_path
from .base24_native5m_acquire import BUCKETS
from .base24_native5m_bounded import (
    Base24BoundedLimitationError,
    validate_bounded_ledger_package,
)
from .base24_native5m_projection import audit_projection
from .market_observation import _schemas

LEDGER_CONTRACT = "p10-base24-native5m-coverage-ledger-package-v1"
PROJECTED_SOURCE_FAMILIES = (
    "new_native_5m",
    "tushare_partition_gated_native_5m",
    "tushare_bounded_continuous_session_native_5m",
)
MONTH_CONTRACT = "p10-base24-native5m-monthly-transfer-v1"
MONTH_RECEIPT_CONTRACT = "p10-base24-native5m-monthly-transfer-receipt-v1"
RESOLUTION_CONTRACT = "p10-base24-native5m-monthly-resolution-v1"
GLOBAL_RECEIPT_CONTRACT = "p10-base24-native5m-global-transfer-receipt-v1"
PACKAGE_CONTRACT = "p10-base24-native5m-monthly-dwd-package-v1"
TRANSFER_MARKER = "TRANSFERRED"
SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
MONTH = re.compile(r"(?:19[89][0-9]|20(?:[01][0-9]|2[0-6]))-(?:0[1-9]|1[0-2])")


class Base24DownstreamError(RuntimeError):
    """A monthly ownership-transfer contract failed closed."""


def _object(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise Base24DownstreamError(reason) from exc
    if not isinstance(value, dict):
        raise Base24DownstreamError(reason)
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise Base24DownstreamError("BASE24_MONTHLY_SHA256_INVALID")
    return value if value.startswith("sha256:") else "sha256:" + value


def _month(value: object) -> str:
    if not isinstance(value, str) or MONTH.fullmatch(value) is None:
        raise Base24DownstreamError("BASE24_MONTHLY_SCOPE_INVALID")
    return value


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.open("xb").write(canonical(value) + b"\n")


def _member(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise Base24DownstreamError("BASE24_MONTHLY_MEMBER_PATH_INVALID")
    part = PurePosixPath(relative)
    if part.is_absolute() or ".." in part.parts or part.as_posix() != relative:
        raise Base24DownstreamError("BASE24_MONTHLY_MEMBER_PATH_INVALID")
    path = physical_path(root / relative)
    if root not in path.parents:
        raise Base24DownstreamError("BASE24_MONTHLY_MEMBER_PATH_INVALID")
    return path


def _tree(root: Path) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        raise Base24DownstreamError("BASE24_MONTHLY_ROOT_INVALID")
    result: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise Base24DownstreamError("BASE24_MONTHLY_SYMLINK_FORBIDDEN")
        if path.is_file():
            result.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise Base24DownstreamError("BASE24_MONTHLY_MEMBER_TYPE_INVALID")
    return result


def validate_successor_ledger(root: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    """Require one exact immutable PASS/R3=0 ledger before any downstream write."""
    root = physical_path(root)
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_NOT_READY")
    manifest_sha256 = file_sha(manifest_path)
    if (
        manifest_sha256 != _sha(expected_manifest_sha256)
        or ready_path.read_text(encoding="ascii").strip() != manifest_sha256.removeprefix("sha256:")
    ):
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_IDENTITY_CHANGED")
    manifest = _object(manifest_path, "BASE24_SUCCESSOR_LEDGER_INVALID")
    if manifest.get("contract") != LEDGER_CONTRACT:
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_NOT_PASS")
    declared: set[str] = set()
    files = manifest.get("files")
    if not isinstance(files, list):
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_INVALID")
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_INVALID")
        relative = row["path"]
        if relative in declared:
            raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_INVALID")
        declared.add(relative)
        member = _member(root, relative)
        if (
            member.is_symlink()
            or not member.is_file()
            or member.stat().st_size != row.get("size_bytes")
            or file_sha(member) != _sha(row.get("sha256"))
        ):
            raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_MEMBER_CHANGED")
    if _tree(root) != {"manifest.json", "READY", *declared}:
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_FILE_SET_CHANGED")
    result = _object(root / "result.safe.json", "BASE24_SUCCESSOR_LEDGER_INVALID")
    try:
        bounded = validate_bounded_ledger_package(root=root, manifest=manifest, result=result)
    except Base24BoundedLimitationError as exc:
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_NOT_PASS") from exc
    ledger_path = root / "coverage-ledger.parquet"
    if ledger_path.relative_to(root).as_posix() not in declared:
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_INVALID")

    connection = duckdb.connect()
    try:
        ledger_summary = connection.execute(
            """
            SELECT count_if(
                     source_family IN ? AND rating IS DISTINCT FROM 'R2'
                     OR source_family IN ? AND disposition IS DISTINCT FROM 'OBSERVED'
                   ),
                   count(*),count_if(source_family IN ?),
                   count(DISTINCT (venue,stock_code,trade_date))
            FROM read_parquet(?)
            """,
            [list(PROJECTED_SOURCE_FAMILIES)] * 3 + [str(ledger_path)],
        ).fetchone()
        if ledger_summary is None:
            raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_CENSUS_INVALID")
        invalid, total, projected, version_keys = ledger_summary
        grouped = connection.execute(
            """
            SELECT strftime(trade_date,'%Y-%m') business_month,
                   count_if(source_family IN ?) projected_security_days,
                   count(*) denominator_security_days
            FROM read_parquet(?) GROUP BY ALL ORDER BY ALL
            """,
            [list(PROJECTED_SOURCE_FAMILIES), str(ledger_path)],
        ).fetchall()
    finally:
        connection.close()
    if invalid or total != version_keys or total != result.get("required_security_days"):
        raise Base24DownstreamError("BASE24_SUCCESSOR_LEDGER_CENSUS_INVALID")
    months = [
        {
            "month": _month(row[0]),
            "projected_security_days": int(row[1]),
            "projected_rows": int(row[1]) * 48,
            "denominator_security_days": int(row[2]),
        }
        for row in grouped
    ]
    return {
        "root": str(root),
        "manifest_sha256": manifest_sha256,
        "result_sha256": file_sha(root / "result.safe.json"),
        "coverage_ledger_sha256": file_sha(ledger_path),
        "required_security_days": int(total),
        "projected_security_days": int(projected),
        "projected_rows": int(projected) * 48,
        "status": bounded["status"],
        "bounded_unavailable_keys": bounded["bounded_unavailable_keys"],
        "limitation_policy_sha256": bounded["policy_sha256"],
        "months": months,
    }


def select_month_source_members(
    members: Iterable[Mapping[str, Any]], business_month: str
) -> list[dict[str, Any]]:
    """Select exact immutable source members whose declared days intersect a month."""
    month = _month(business_month)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in members:
        required_dates = source.get("required_dates")
        if (
            not isinstance(required_dates, list)
            or not all(isinstance(value, str) for value in required_dates)
            or required_dates != sorted(set(required_dates))
        ):
            raise Base24DownstreamError("BASE24_MONTHLY_SOURCE_DATES_INVALID")
        try:
            parsed_dates = [date.fromisoformat(value) for value in required_dates]
        except ValueError as exc:
            raise Base24DownstreamError("BASE24_MONTHLY_SOURCE_DATES_INVALID") from exc
        if [value.isoformat() for value in parsed_dates] != required_dates:
            raise Base24DownstreamError("BASE24_MONTHLY_SOURCE_DATES_INVALID")
        scoped_dates = [
            value for value in required_dates if isinstance(value, str) and value[:7] == month
        ]
        if not scoped_dates:
            continue
        path_value = source.get("source_path")
        partition_id = source.get("partition_id")
        acquired_at = source.get("acquired_at")
        if (
            not isinstance(path_value, str)
            or not isinstance(partition_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", partition_id) is None
            or not isinstance(acquired_at, str)
            or not acquired_at
        ):
            raise Base24DownstreamError("BASE24_MONTHLY_SOURCE_IDENTITY_INVALID")
        key = (path_value, partition_id)
        if key in seen:
            raise Base24DownstreamError("BASE24_MONTHLY_SOURCE_DUPLICATE")
        seen.add(key)
        selected.append(
            {
                "source_path": path_value,
                "partition_id": partition_id,
                "source_artifact_sha256": _sha(source.get("source_artifact_sha256")),
                "acquired_at": acquired_at,
                "required_dates": scoped_dates,
            }
        )
    return sorted(selected, key=lambda row: (row["partition_id"], row["source_path"]))


def freeze_month_identity(
    *,
    business_month: str,
    projected_security_days: int,
    ledger: Mapping[str, Any],
    acquisition: Mapping[str, Any],
    denominator: Mapping[str, Any],
    exact_members: Iterable[Mapping[str, Any]],
    predecessor_members: Iterable[Mapping[str, Any]],
    reference_members: Iterable[Mapping[str, Any]],
    code: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the complete semantic closure of one monthly transfer."""
    month = _month(business_month)
    if not isinstance(projected_security_days, int) or projected_security_days < 0:
        raise Base24DownstreamError("BASE24_MONTHLY_PROJECTED_DAYS_INVALID")

    def identities(values: Iterable[Mapping[str, Any]], label: str) -> list[dict[str, Any]]:
        result = []
        paths: set[str] = set()
        for value in values:
            path = value.get("path", value.get("source_path"))
            if not isinstance(path, str) or not path or path in paths:
                raise Base24DownstreamError(f"BASE24_MONTHLY_{label}_INVALID")
            paths.add(path)
            record = {
                "path": path,
                "sha256": _sha(value.get("sha256", value.get("source_artifact_sha256"))),
            }
            for key in ("partition_id", "acquired_at", "required_dates", "rows", "size_bytes"):
                if key in value:
                    record[key] = value[key]
            result.append(record)
        return sorted(result, key=lambda row: row["path"])

    value: dict[str, Any] = {
        "contract": MONTH_CONTRACT,
        "business_month": month,
        "projected_security_days": projected_security_days,
        "projected_rows": projected_security_days * 48,
        "ledger": dict(ledger),
        "acquisition": dict(acquisition),
        "denominator": dict(denominator),
        "exact_members": identities(exact_members, "EXACT_MEMBERS"),
        "predecessor_members": identities(predecessor_members, "PREDECESSOR_MEMBERS"),
        "reference_members": identities(reference_members, "REFERENCE_MEMBERS"),
        "code": dict(code),
    }
    closures: tuple[dict[str, Any], ...] = (
        value["ledger"],
        value["acquisition"],
        value["denominator"],
        value["code"],
    )
    for closure in closures:
        invalid_sha = any(
            key.endswith("sha256") and SHA256.fullmatch(str(item)) is None
            for key, item in closure.items()
        )
        if not closure or invalid_sha:
            raise Base24DownstreamError("BASE24_MONTHLY_SEMANTIC_CLOSURE_INVALID")
    value["identity"] = digest(value)
    return value


def _relation(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]*", value) is None:
        raise Base24DownstreamError("BASE24_MONTHLY_RELATION_INVALID")
    return value


def _resolution_result(month: str, expected_rows: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "contract": RESOLUTION_CONTRACT,
        "business_month": month,
        "required_security_days": expected_rows // 48,
        "bucket_count_per_security_day": 48,
        "bucket_set_sha256": digest(list(BUCKETS)),
        "projected_rows": expected_rows,
        "resolved_rows": expected_rows,
        "venue_calendar_rows": expected_rows,
    }
    result["identity"] = digest(result)
    return result


def empty_month_resolution(business_month: str) -> dict[str, Any]:
    """Return the sealed zero-projection resolution for a predecessor-only month."""
    return _resolution_result(_month(business_month), 0)


def audit_month_resolution(
    connection: duckdb.DuckDBPyConnection,
    *,
    business_month: str,
    expected_rows: int,
    projected_relation: str = "projected_native5m",
    supplement_relation: str = "native5m_supplement",
    listing_relation: str = "dwd_listing",
    venue_relation: str = "dwd_trading_venue",
    calendar_relation: str = "dwd_trading_calendar",
    required_relation: str = "required_native5m",
) -> dict[str, Any]:
    """Recompute exact-bucket, source, listing, venue and calendar resolution."""
    month = _month(business_month)
    if not isinstance(expected_rows, int) or expected_rows < 0 or expected_rows % 48:
        raise Base24DownstreamError("BASE24_MONTHLY_PROJECTED_ROWS_INVALID")
    projected = _relation(projected_relation)
    supplement = _relation(supplement_relation)
    listing = _relation(listing_relation)
    venue = _relation(venue_relation)
    calendar = _relation(calendar_relation)
    required = _relation(required_relation)
    buckets = ",".join("'" + value + "'" for value in BUCKETS)
    projected_summary = connection.execute(
        f"""
        SELECT count(*),count(DISTINCT (stock_code,ts,bar_size)),
               count_if(bar_size<>'5m' OR strftime(market_date,'%Y-%m')<>?
                 OR strftime(ts,'%H:%M:%S') NOT IN ({buckets})
                 OR input_partition_id !~ '^[0-9a-f]{{64}}$'
                 OR input_artifact_sha256 !~ '^sha256:[0-9a-f]{{64}}$'
                 OR acquired_at IS NULL)
        FROM {projected}
        """,
        [month],
    ).fetchone()
    bad_days = connection.execute(
        f"""
        SELECT count(*) FROM (
          SELECT stock_code,market_date,count(*) row_count,
                 count(DISTINCT ts) bucket_count,
                 count(DISTINCT strftime(ts,'%H:%M:%S')) time_count
          FROM {projected} GROUP BY ALL
          HAVING row_count<>48 OR bucket_count<>48 OR time_count<>48
        )
        """
    ).fetchone()
    resolved_summary = connection.execute(
        f"""
        SELECT count(*),count(DISTINCT (
                 s.listing_id,s.observation_start,s.interval_size,
                 s.price_basis,s.knowledge_from
               )),
               count_if(
                 s.record_source<>'base24_native5m_exact'
                 OR s.interval_size<>'5m' OR s.price_basis<>'UNADJUSTED'
                 OR s.source_artifact_sha256<>p.input_artifact_sha256
                 OR s.knowledge_from<>p.acquired_at
                 OR l.listing_code<>p.stock_code
                 OR l.venue_id<>c.venue_id
                 OR v.mic_code<>
                    CASE r.venue WHEN 'SSE' THEN 'XSHG' WHEN 'SZSE' THEN 'XSHE' END
                 OR c.trade_date<>p.market_date
                 OR c.session_type<>'REGULAR_DAY' OR NOT c.is_open
                 OR strftime(c.trade_date,'%Y-%m')<>?
                 OR s.source_record_id<>
                    'partition='||p.input_partition_id||'&stock_code='||p.stock_code||'&ts='||
                    strftime(p.ts,'%Y-%m-%dT%H:%M:%S')
               )
        FROM {projected} p
        JOIN {supplement} s ON s.source_record_id=
             'partition='||p.input_partition_id||'&stock_code='||p.stock_code||'&ts='||
             strftime(p.ts,'%Y-%m-%dT%H:%M:%S')
        JOIN {listing} l ON l.listing_id=s.listing_id
        JOIN {venue} v ON v.venue_id=l.venue_id
        JOIN {calendar} c ON c.calendar_session_id=s.calendar_session_id
        JOIN {required} r ON r.stock_code=p.stock_code AND r.trade_date=p.market_date
        """,
        [month],
    ).fetchone()
    if projected_summary is None or bad_days is None or resolved_summary is None:
        raise Base24DownstreamError("BASE24_MONTHLY_RESOLUTION_INVALID")
    projected_rows, projected_keys, projected_invalid = map(int, projected_summary)
    resolved_rows, resolved_keys, resolution_invalid = map(int, resolved_summary)
    if (
        projected_rows != expected_rows
        or projected_keys != expected_rows
        or projected_invalid
        or int(bad_days[0])
        or resolved_rows != expected_rows
        or resolved_keys != expected_rows
        or resolution_invalid
    ):
        raise Base24DownstreamError("BASE24_MONTHLY_RESOLUTION_INVALID")
    return _resolution_result(month, expected_rows)


def _parquet_records(root: Path, relatives: Iterable[str]) -> list[dict[str, Any]]:
    records = []
    supplied = list(relatives)
    if len(supplied) != len(set(supplied)):
        raise Base24DownstreamError("BASE24_MONTHLY_DWD_MEMBER_INVALID")
    for relative in sorted(supplied):
        path = _member(root, relative)
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise Base24DownstreamError("BASE24_MONTHLY_DWD_MEMBER_INVALID")
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(_schemas()["dwd_security_bar"], check_metadata=False):
            raise Base24DownstreamError("BASE24_MONTHLY_DWD_SCHEMA_INVALID")
        records.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "rows": parquet.metadata.num_rows,
                "sha256": file_sha(path),
            }
        )
    if not records:
        raise Base24DownstreamError("BASE24_MONTHLY_DWD_MEMBER_INVALID")
    return records


def audit_month_dwd(
    root: Path, relatives: Iterable[str], business_month: str, expected_native_rows: int
) -> dict[str, Any]:
    """Recompute month containment, 48-bucket, lineage and version-key evidence."""
    root = physical_path(root)
    month = _month(business_month)
    records = _parquet_records(root, relatives)
    paths = [str(root / row["path"]) for row in records]
    connection = duckdb.connect()
    try:
        summary = connection.execute(
            """
            SELECT count(*),
                   count(DISTINCT (listing_id,observation_start,interval_size,
                                   price_basis,knowledge_from)),
                   count_if(interval_size='1m'),
                   count_if(record_source='base24_native5m_exact'),
                   count_if(interval_size NOT IN ('1d','5m') OR price_basis<>'UNADJUSTED'
                     OR listing_id='' OR calendar_session_id=''
                     OR source_artifact_sha256 !~ '^sha256:[0-9a-f]{64}$'
                     OR strftime(timezone('Asia/Shanghai',observation_start),'%Y-%m')<>?)
            FROM read_parquet(?,union_by_name=true,hive_partitioning=false)
            """,
            [month, paths],
        ).fetchone()
        bad_native_summary = connection.execute(
            """
            SELECT count(*) FROM (
              SELECT listing_id,cast(timezone('Asia/Shanghai',observation_start) AS DATE),
                     count(*) row_count,count(DISTINCT observation_start) bucket_count
              FROM read_parquet(?,union_by_name=true,hive_partitioning=false)
              WHERE record_source='base24_native5m_exact'
              GROUP BY ALL HAVING row_count<>48 OR bucket_count<>48
            )
            """,
            [paths],
        ).fetchone()
        intervals = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT interval_size FROM "
                "read_parquet(?,union_by_name=true,hive_partitioning=false) ORDER BY 1",
                [paths],
            ).fetchall()
        ]
    finally:
        connection.close()
    if summary is None or bad_native_summary is None:
        raise Base24DownstreamError("BASE24_MONTHLY_DWD_CENSUS_INVALID")
    bad_native_days = int(bad_native_summary[0])
    rows, keys, one_minute, native_rows, invalid = (int(value) for value in summary)
    if (
        rows != keys
        or one_minute
        or native_rows != expected_native_rows
        or invalid
        or bad_native_days
        or not set(intervals) <= {"1d", "5m"}
    ):
        raise Base24DownstreamError("BASE24_MONTHLY_DWD_CENSUS_INVALID")
    return {
        "business_month": month,
        "rows": rows,
        "version_keys": keys,
        "native5m_rows": native_rows,
        "native5m_security_days": native_rows // 48,
        "intervals": intervals,
        "files": records,
    }


def seal_month_transfer(
    *,
    month_root: Path,
    final_root: Path,
    month_identity: Mapping[str, Any],
    projection_root: Path | None,
    final_files: Iterable[str],
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal ownership only after projected TMP and final-owned DWD bytes validate."""
    month_root = physical_path(month_root)
    final_root = physical_path(final_root)
    if (month_root / "receipt.json").exists() or (month_root / TRANSFER_MARKER).exists():
        raise Base24DownstreamError("BASE24_MONTHLY_TRANSFER_ALREADY_SEALED")
    frozen = {key: value for key, value in month_identity.items() if key != "identity"}
    if (
        month_identity.get("contract") != MONTH_CONTRACT
        or digest(frozen) != month_identity.get("identity")
    ):
        raise Base24DownstreamError("BASE24_MONTHLY_IDENTITY_INVALID")
    expected_rows = int(month_identity["projected_rows"])
    projection_sha256 = None
    projection_summary_sha256 = None
    if expected_rows:
        if projection_root is None:
            raise Base24DownstreamError("BASE24_MONTHLY_PROJECTION_REQUIRED")
        projection = audit_projection(physical_path(projection_root))
        if (
            projection["summary"]["rows"] != expected_rows
            or projection["summary"]["security_days"] * 48 != expected_rows
            or {
                f"{row['year']:04d}-{row['month']:02d}"
                for row in projection["summary"]["partitions"]
            }
            != {month_identity["business_month"]}
        ):
            raise Base24DownstreamError("BASE24_MONTHLY_PROJECTION_CENSUS_INVALID")
        projection_sha256 = file_sha(Path(projection_root) / "manifest.json")
        projection_summary_sha256 = digest(projection["summary"])
    elif projection_root is not None:
        raise Base24DownstreamError("BASE24_MONTHLY_UNEXPECTED_PROJECTION")
    resolution_frozen = {key: value for key, value in resolution.items() if key != "identity"}
    resolution_keys = ("projected_rows", "resolved_rows", "venue_calendar_rows")
    if (
        resolution.get("contract") != RESOLUTION_CONTRACT
        or resolution.get("business_month") != month_identity["business_month"]
        or resolution.get("required_security_days") != expected_rows // 48
        or resolution.get("bucket_count_per_security_day") != 48
        or resolution.get("bucket_set_sha256") != digest(list(BUCKETS))
        or digest(resolution_frozen) != resolution.get("identity")
        or any(int(resolution.get(key, -1)) != expected_rows for key in resolution_keys)
    ):
        raise Base24DownstreamError("BASE24_MONTHLY_RESOLUTION_INVALID")
    dwd = audit_month_dwd(
        final_root,
        final_files,
        str(month_identity["business_month"]),
        expected_rows,
    )
    receipt = {
        "contract": MONTH_RECEIPT_CONTRACT,
        "month_identity": month_identity["identity"],
        "business_month": month_identity["business_month"],
        "projected_rows": expected_rows,
        "projection_manifest_sha256": projection_sha256,
        "projection_summary_sha256": projection_summary_sha256,
        "resolution": dict(resolution),
        "dwd": dwd,
        "final_root_archival": str(final_root),
        "ownership": "FINAL_DWD",
        "projection_tmp_retirement": "AUTHORIZED_AFTER_THIS_RECEIPT",
    }
    receipt["identity"] = digest(receipt)
    _write_once(month_root / "month-identity.json", dict(month_identity))
    _write_once(month_root / "receipt.json", receipt)
    (month_root / TRANSFER_MARKER).open("x", encoding="ascii").write(
        file_sha(month_root / "receipt.json").removeprefix("sha256:") + "\n"
    )
    return receipt


def _verify_receipt(month_root: Path, final_root: Path) -> dict[str, Any]:
    receipt_path = month_root / "receipt.json"
    marker = month_root / TRANSFER_MARKER
    if (
        not receipt_path.is_file()
        or not marker.is_file()
        or marker.read_text(encoding="ascii").strip()
        != file_sha(receipt_path).removeprefix("sha256:")
    ):
        raise Base24DownstreamError("BASE24_MONTHLY_TRANSFER_NOT_SEALED")
    receipt = _object(receipt_path, "BASE24_MONTHLY_TRANSFER_RECEIPT_INVALID")
    frozen = {key: value for key, value in receipt.items() if key != "identity"}
    if (
        receipt.get("contract") != MONTH_RECEIPT_CONTRACT
        or digest(frozen) != receipt.get("identity")
        or receipt.get("final_root_archival") != str(final_root)
    ):
        raise Base24DownstreamError("BASE24_MONTHLY_TRANSFER_RECEIPT_INVALID")
    observed = audit_month_dwd(
        final_root,
        [row["path"] for row in receipt["dwd"]["files"]],
        receipt["business_month"],
        int(receipt["projected_rows"]),
    )
    if observed != receipt["dwd"]:
        raise Base24DownstreamError("BASE24_MONTHLY_FINAL_BYTES_CHANGED")
    return receipt


def retire_projection_tmp(
    *, month_root: Path, projection_root: Path, final_root: Path
) -> dict[str, Any]:
    """Release only a month-owned projection TMP after final DWD ownership is proven."""
    month_root = physical_path(month_root)
    projection_root = physical_path(projection_root)
    final_root = physical_path(final_root)
    if projection_root.parent != month_root / "tmp" or projection_root.name != "projection":
        raise Base24DownstreamError("BASE24_MONTHLY_TMP_SCOPE_INVALID")
    receipt = _verify_receipt(month_root, final_root)
    if receipt["projected_rows"] == 0 or not projection_root.is_dir():
        raise Base24DownstreamError("BASE24_MONTHLY_TMP_SCOPE_INVALID")
    if file_sha(projection_root / "manifest.json") != receipt["projection_manifest_sha256"]:
        raise Base24DownstreamError("BASE24_MONTHLY_PROJECTION_CHANGED")
    before = {
        "contract": "p10-base24-native5m-monthly-tmp-retirement-v1",
        "business_month": receipt["business_month"],
        "transfer_receipt_sha256": file_sha(month_root / "receipt.json"),
        "projection_root": str(projection_root),
        "files": [
            {
                "path": relative,
                "size_bytes": (projection_root / relative).stat().st_size,
                "sha256": file_sha(projection_root / relative),
            }
            for relative in sorted(_tree(projection_root))
        ],
        "source_roots_modified": False,
    }
    before["identity"] = digest(before)
    _write_once(month_root / "tmp-retirement-before.json", before)
    shutil.rmtree(projection_root)
    if projection_root.exists():
        raise Base24DownstreamError("BASE24_MONTHLY_TMP_RETIREMENT_FAILED")
    after = {
        **before,
        "status": "PASS",
        "projection_tmp_absent": True,
        "before_sha256": file_sha(month_root / "tmp-retirement-before.json"),
    }
    _write_once(month_root / "tmp-retirement-after.json", after)
    return after


def record_month_failure(
    *,
    month_root: Path,
    business_month: str,
    stage: str,
    reason: str,
    month_identity: str | None,
) -> dict[str, Any]:
    """Write bounded non-price failure evidence without authorizing cleanup or READY."""
    month_root = physical_path(month_root)
    month = _month(business_month)
    if re.fullmatch(r"[a-z][a-z0-9_]*", stage) is None:
        raise Base24DownstreamError("BASE24_MONTHLY_FAILURE_STAGE_INVALID")
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", reason) is None:
        raise Base24DownstreamError("BASE24_MONTHLY_FAILURE_REASON_INVALID")
    if month_identity is not None:
        month_identity = _sha(month_identity)
    failure = {
        "contract": "p10-base24-native5m-monthly-failure-v1",
        "status": "FAILED",
        "business_month": month,
        "stage": stage,
        "reason": reason,
        "month_identity": month_identity,
        "evidence_retained": True,
        "projection_tmp_retained": (month_root / "tmp/projection").exists(),
        "final_dwd_bytes_retained": True,
        "transfer_marker_written": (month_root / TRANSFER_MARKER).is_file(),
        "ready_written": False,
    }
    failure["identity"] = digest(failure)
    _write_once(month_root / "FAILURE.safe.json", failure)
    return failure


def seal_global_transfer(
    *,
    output_root: Path,
    month_roots: Iterable[Path],
    expected_months: Iterable[str],
    registration: Mapping[str, Any],
    code_revision: str,
) -> dict[str, Any]:
    """Recompute the whole bar census and write READY last."""
    output_root = physical_path(output_root)
    if (output_root / "manifest.json").exists() or (output_root / "READY").exists():
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_ALREADY_SEALED")
    months = sorted({_month(value) for value in expected_months})
    roots = sorted((physical_path(path) for path in month_roots), key=lambda path: path.name)
    receipts = [_verify_receipt(path, output_root) for path in roots]
    if [receipt["business_month"] for receipt in receipts] != months:
        raise Base24DownstreamError("BASE24_GLOBAL_MONTH_SET_INVALID")
    for root in roots:
        tmp_root = root / "tmp"
        if tmp_root.exists() and (not tmp_root.is_dir() or any(tmp_root.iterdir())):
            raise Base24DownstreamError("BASE24_GLOBAL_MONTH_TMP_REMAINS")
    expected_files = {
        row["path"] for receipt in receipts for row in receipt["dwd"]["files"]
    }
    if len(expected_files) != sum(len(receipt["dwd"]["files"]) for receipt in receipts):
        raise Base24DownstreamError("BASE24_GLOBAL_FILE_COLLISION")
    payload = output_root / "payload/dwd_security_bar"
    actual_files = {
        path.relative_to(output_root).as_posix() for path in payload.rglob("*.parquet")
    }
    if actual_files != expected_files:
        raise Base24DownstreamError("BASE24_GLOBAL_FILE_SET_INVALID")
    paths = [str(output_root / relative) for relative in sorted(expected_files)]
    connection = duckdb.connect()
    try:
        global_summary = connection.execute(
            """
            SELECT count(*),count(DISTINCT (
              listing_id,observation_start,interval_size,price_basis,knowledge_from
            )),count_if(record_source='base24_native5m_exact'),count_if(interval_size='1m')
            FROM read_parquet(?,union_by_name=true,hive_partitioning=false)
            """,
            [paths],
        ).fetchone()
        intervals = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT interval_size FROM "
                "read_parquet(?,union_by_name=true,hive_partitioning=false)",
                [paths],
            ).fetchall()
        }
    finally:
        connection.close()
    if global_summary is None:
        raise Base24DownstreamError("BASE24_GLOBAL_CENSUS_INVALID")
    rows, keys, native_rows, one_minute = global_summary
    expected_rows = sum(int(receipt["dwd"]["rows"]) for receipt in receipts)
    expected_native = sum(int(receipt["projected_rows"]) for receipt in receipts)
    if (
        rows != keys
        or rows != expected_rows
        or native_rows != expected_native
        or one_minute
        or intervals != {"1d", "5m"}
    ):
        raise Base24DownstreamError("BASE24_GLOBAL_CENSUS_INVALID")

    _write_once(output_root / "REGISTRATION.json", dict(registration))
    for receipt in receipts:
        _write_once(output_root / f"evidence/months/{receipt['business_month']}.json", receipt)
    global_receipt = {
        "contract": GLOBAL_RECEIPT_CONTRACT,
        "months": months,
        "month_receipt_identities": [receipt["identity"] for receipt in receipts],
        "rows": int(rows),
        "version_keys": int(keys),
        "native5m_rows": int(native_rows),
        "intervals": sorted(intervals),
        "files": [row for receipt in receipts for row in receipt["dwd"]["files"]],
        "projection_tmp_retirements": [
            file_sha(root / "tmp-retirement-after.json")
            if int(receipt["projected_rows"])
            else None
            for root, receipt in zip(roots, receipts, strict=True)
        ],
        "monthly_tmp_roots_empty": True,
    }
    global_receipt["identity"] = digest(global_receipt)
    _write_once(output_root / "evidence/global-transfer-receipt.json", global_receipt)
    files = [
        {
            "path": relative,
            "size_bytes": (output_root / relative).stat().st_size,
            "sha256": file_sha(output_root / relative),
        }
        for relative in sorted(_tree(output_root))
    ]
    manifest = {
        "contract": PACKAGE_CONTRACT,
        "code_revision": code_revision,
        "business_through": "2026-09-30",
        "default_intervals": ["1d", "5m"],
        "one_minute_disposition": "NOT_INCLUDED_RETAINED_SOURCE_NOT_DELETED",
        "global_transfer_identity": global_receipt["identity"],
        "rows": int(rows),
        "new_native5m_rows": int(native_rows),
        "files": files,
        "consumer_admission": "NOT_GRANTED",
    }
    _write_once(output_root / "manifest.json", manifest)
    (output_root / "READY").open("x", encoding="ascii").write(
        file_sha(output_root / "manifest.json").removeprefix("sha256:") + "\n"
    )
    return manifest


def audit_global_transfer(output_root: Path) -> dict[str, Any]:
    """Cold-recompute the sealed file set, hashes and global DWD uniqueness census."""
    output_root = physical_path(output_root)
    manifest_path = output_root / "manifest.json"
    ready_path = output_root / "READY"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_NOT_READY")
    if ready_path.read_text(encoding="ascii").strip() != file_sha(manifest_path).removeprefix(
        "sha256:"
    ):
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_SEAL_INVALID")
    manifest = _object(manifest_path, "BASE24_GLOBAL_TRANSFER_SEAL_INVALID")
    if (
        manifest.get("contract") != PACKAGE_CONTRACT
        or manifest.get("default_intervals") != ["1d", "5m"]
        or manifest.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_SEAL_INVALID")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_SEAL_INVALID")
    declared: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_SEAL_INVALID")
        relative = row["path"]
        if relative in declared:
            raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_SEAL_INVALID")
        declared.add(relative)
        member = _member(output_root, relative)
        if (
            member.is_symlink()
            or not member.is_file()
            or member.stat().st_size != row.get("size_bytes")
            or file_sha(member) != _sha(row.get("sha256"))
        ):
            raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_MEMBER_CHANGED")
    if _tree(output_root) != {"manifest.json", "READY", *declared}:
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_FILE_SET_CHANGED")
    receipt = _object(
        output_root / "evidence/global-transfer-receipt.json",
        "BASE24_GLOBAL_TRANSFER_RECEIPT_INVALID",
    )
    frozen = {key: value for key, value in receipt.items() if key != "identity"}
    if (
        receipt.get("contract") != GLOBAL_RECEIPT_CONTRACT
        or digest(frozen) != receipt.get("identity")
        or receipt.get("identity") != manifest.get("global_transfer_identity")
    ):
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_RECEIPT_INVALID")
    receipt_files = receipt.get("files")
    if not isinstance(receipt_files, list) or not all(
        isinstance(row, dict) and isinstance(row.get("path"), str) for row in receipt_files
    ):
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_RECEIPT_INVALID")
    receipt_paths: set[str] = set()
    for row in receipt_files:
        relative = row["path"]
        if relative in receipt_paths:
            raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_RECEIPT_INVALID")
        receipt_paths.add(relative)
        member = _member(output_root, relative)
        if (
            member.stat().st_size != row.get("size_bytes")
            or file_sha(member) != _sha(row.get("sha256"))
        ):
            raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_MEMBER_CHANGED")
    paths = [str(_member(output_root, relative)) for relative in sorted(receipt_paths)]
    if not paths:
        raise Base24DownstreamError("BASE24_GLOBAL_TRANSFER_RECEIPT_INVALID")
    connection = duckdb.connect()
    try:
        global_summary = connection.execute(
            """
            SELECT count(*),count(DISTINCT (
              listing_id,observation_start,interval_size,price_basis,knowledge_from
            )),count_if(record_source='base24_native5m_exact'),count_if(interval_size='1m')
            FROM read_parquet(?,union_by_name=true,hive_partitioning=false)
            """,
            [paths],
        ).fetchone()
        intervals = sorted(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT interval_size FROM "
                "read_parquet(?,union_by_name=true,hive_partitioning=false)",
                [paths],
            ).fetchall()
        )
    finally:
        connection.close()
    if global_summary is None:
        raise Base24DownstreamError("BASE24_GLOBAL_CENSUS_INVALID")
    rows, keys, native_rows, one_minute = global_summary
    if (
        rows != keys
        or one_minute
        or int(rows) != receipt.get("rows")
        or int(keys) != receipt.get("version_keys")
        or int(native_rows) != receipt.get("native5m_rows")
        or intervals != receipt.get("intervals")
        or int(rows) != manifest.get("rows")
        or int(native_rows) != manifest.get("new_native5m_rows")
    ):
        raise Base24DownstreamError("BASE24_GLOBAL_CENSUS_INVALID")
    return {
        "status": "PASS",
        "manifest_sha256": file_sha(manifest_path),
        "global_transfer_identity": receipt["identity"],
        "rows": int(rows),
        "version_keys": int(keys),
        "native5m_rows": int(native_rows),
        "intervals": intervals,
        "source_roots_dereferenced": False,
    }
