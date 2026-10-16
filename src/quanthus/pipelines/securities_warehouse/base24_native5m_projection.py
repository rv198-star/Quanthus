"""Bounded, self-verifying ODS projection for exact Base 2.4 native five-minute data."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

CONTRACT = "p10-base24-native5m-projection-v1"
PACKAGE_CONTRACT = "p10-base24-native5m-projection-package-v1"
EXPECTED_COLUMNS = {
    "stock_code": "VARCHAR",
    "ts": "TIMESTAMP",
    "market_date": "DATE",
    "bar_size": "VARCHAR",
    "source_values": "VARCHAR[]",
    "open": "DECIMAL(38,10)",
    "high": "DECIMAL(38,10)",
    "low": "DECIMAL(38,10)",
    "close": "DECIMAL(38,10)",
    "volume": "DECIMAL(38,10)",
    "amount": "DECIMAL(38,10)",
    "acquired_at": "TIMESTAMP WITH TIME ZONE",
    "input_partition_id": "VARCHAR",
    "input_artifact_sha256": "VARCHAR",
}


class Native5mProjectionError(ValueError):
    """Projection contract error without source prices."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode()


def _physical_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise Native5mProjectionError("PROJECTION_ROOT_INVALID")
    files = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise Native5mProjectionError("PROJECTION_SYMLINK_FORBIDDEN")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise Native5mProjectionError("PROJECTION_MEMBER_TYPE_INVALID")
    return sorted(files)


def inspect_projection_payload(root: Path) -> dict[str, Any]:
    """Fully scan a bounded projection without exposing market values."""
    payload = root / "payload"
    parquet = sorted(payload.rglob("*.parquet")) if payload.is_dir() else []
    if not parquet or any(path.is_symlink() for path in parquet):
        raise Native5mProjectionError("PROJECTION_PAYLOAD_MISSING")
    expected_relatives = set()
    grouped: dict[tuple[int, int], list[Path]] = {}
    for path in parquet:
        relative = path.relative_to(root).as_posix()
        match = re.fullmatch(
            r"payload/observation_year=20(?:2[0-6])/observation_month=(?:[1-9]|1[0-2])/.+\.parquet",
            relative,
        )
        if match is None:
            raise Native5mProjectionError("PROJECTION_PARTITION_LAYOUT_INVALID")
        expected_relatives.add(relative)
        year = int(relative.split("/", 2)[1].split("=", 1)[1])
        month = int(relative.split("/", 3)[2].split("=", 1)[1])
        grouped.setdefault((year, month), []).append(path)
    physical_payload = {path.relative_to(root).as_posix() for path in _physical_files(payload)}
    if physical_payload != expected_relatives:
        raise Native5mProjectionError("PROJECTION_PHYSICAL_FILE_SET_INVALID")

    connection = duckdb.connect()
    connection.execute("SET threads=1")
    connection.execute("SET memory_limit='2GB'")
    rows = 0
    days = 0
    first_date = None
    last_date = None
    partitions = []
    try:
        for (year, month), partition_files in sorted(grouped.items()):
            paths = [str(path) for path in partition_files]
            description = {
                row[0]: row[1]
                for row in connection.execute(
                    "DESCRIBE SELECT * EXCLUDE(observation_year,observation_month) "
                    "FROM read_parquet(?,union_by_name=true,hive_partitioning=true)",
                    [paths],
                ).fetchall()
            }
            if description != EXPECTED_COLUMNS:
                raise Native5mProjectionError("PROJECTION_SCHEMA_INVALID")
            query = "read_parquet(?,union_by_name=true,hive_partitioning=true)"
            invalid_row = connection.execute(
                f"""
                SELECT count(*) FROM {query}
                WHERE bar_size<>'5m' OR stock_code !~ '^[0-9]{{6}}$'
                  OR market_date<>cast(ts AS DATE)
                  OR market_date<DATE '2020-01-01' OR market_date>DATE '2026-09-30'
                  OR observation_year<>{year} OR observation_month<>{month}
                  OR observation_year<>year(market_date)
                  OR observation_month<>month(market_date)
                  OR acquired_at IS NULL OR len(source_values)<>6
                  OR input_partition_id !~ '^[0-9a-f]{{64}}$'
                  OR input_artifact_sha256 !~ '^sha256:[0-9a-f]{{64}}$'
                """,
                [paths],
            ).fetchone()
            census = connection.execute(
                f"""
                SELECT count(*),count(DISTINCT (stock_code,ts,bar_size)),
                       count(DISTINCT (stock_code,market_date)),
                       min(market_date),max(market_date)
                FROM {query}
                """,
                [paths],
            ).fetchone()
            bad_days_row = connection.execute(
                f"""
                SELECT count(*) FROM (
                  SELECT stock_code,market_date,count(*) n,count(DISTINCT ts) buckets
                  FROM {query} GROUP BY ALL HAVING n<>48 OR buckets<>48
                )
                """,
                [paths],
            ).fetchone()
            if invalid_row is None or census is None or bad_days_row is None:
                raise Native5mProjectionError("PROJECTION_SCAN_FAILED")
            if invalid_row[0]:
                raise Native5mProjectionError("PROJECTION_ROW_CONTRACT_INVALID")
            part_rows, keys, part_days, part_first, part_last = census
            if part_rows != keys or bad_days_row[0] or part_rows != part_days * 48:
                raise Native5mProjectionError("PROJECTION_KEY_OR_BUCKET_CENSUS_INVALID")
            rows += part_rows
            days += part_days
            first_date = part_first if first_date is None else min(first_date, part_first)
            last_date = part_last if last_date is None else max(last_date, part_last)
            partitions.append(
                {"year": year, "month": month, "rows": part_rows, "security_days": part_days}
            )
    finally:
        connection.close()
    if first_date is None or last_date is None:
        raise Native5mProjectionError("PROJECTION_PAYLOAD_MISSING")
    file_members = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in parquet
    ]
    return {
        "rows": rows,
        "security_days": days,
        "first_business_date": first_date.isoformat(),
        "last_business_date": last_date.isoformat(),
        "partitions": partitions,
        "files": file_members,
    }


def seal_projection(
    root: Path,
    *,
    input_identity: dict[str, Any],
    code_revision: str,
) -> dict[str, Any]:
    """Seal a completed payload; READY is written only after the full scan."""
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise Native5mProjectionError("PROJECTION_ALREADY_SEALED")
    registration = root / "REGISTRATION.json"
    if registration.is_symlink() or not registration.is_file():
        raise Native5mProjectionError("PROJECTION_REGISTRATION_MISSING")
    summary = inspect_projection_payload(root)
    manifest = {
        "contract": PACKAGE_CONTRACT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "frequency": "5m",
        "price_basis": "unadjusted",
        "consumer_admission": "NOT_GRANTED",
        "code_revision": code_revision,
        "input_identity": input_identity,
        "registration_SHA256": file_sha256(registration),
        "summary": summary,
    }
    (root / "manifest.json").open("xb").write(_canonical(manifest))
    (root / "READY").open("x", encoding="ascii").write(file_sha256(root / "manifest.json") + "\n")
    return manifest


def audit_projection(root: Path) -> dict[str, Any]:
    """Cold audit a sealed package using contained evidence and physical payload."""
    try:
        loaded = json.loads((root / "manifest.json").read_bytes())
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except (OSError, ValueError, TypeError) as exc:
        raise Native5mProjectionError("PROJECTION_SEAL_INVALID") from exc
    if not isinstance(loaded, dict):
        raise Native5mProjectionError("PROJECTION_SEAL_INVALID")
    manifest: dict[str, Any] = loaded
    if (
        manifest.get("contract") != PACKAGE_CONTRACT
        or manifest.get("business_through") != "2026-09-30"
        or manifest.get("frequency") != "5m"
        or manifest.get("price_basis") != "unadjusted"
        or manifest.get("consumer_admission") != "NOT_GRANTED"
        or ready != file_sha256(root / "manifest.json")
        or manifest.get("registration_SHA256") != file_sha256(root / "REGISTRATION.json")
    ):
        raise Native5mProjectionError("PROJECTION_SEAL_INVALID")
    if manifest.get("summary") != inspect_projection_payload(root):
        raise Native5mProjectionError("PROJECTION_SUMMARY_CHANGED")
    allowed = {
        "REGISTRATION.json",
        "manifest.json",
        "READY",
        *(row["path"] for row in manifest["summary"]["files"]),
    }
    actual = {path.relative_to(root).as_posix() for path in _physical_files(root)}
    if actual != allowed:
        raise Native5mProjectionError("PROJECTION_PHYSICAL_FILE_SET_INVALID")
    return manifest


def _relation(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise Native5mProjectionError("RELATION_NAME_INVALID")
    return value


def projection_query(
    *,
    exact_relation: str = "exact_rows",
    identity_relation: str = "source_identity",
    required_relation: str = "required_missing",
) -> str:
    """Return one scan/join projection; required gaps exclude incidental responses.

    ``exact_relation`` is the immutable acquired Parquet scan with a ``filename``
    column. ``identity_relation`` binds each file to its partition/hash/acquisition
    clock. ``required_relation`` is the frozen listing-session gap denominator.
    """
    exact = _relation(exact_relation)
    identity = _relation(identity_relation)
    required = _relation(required_relation)
    return f"""
WITH source AS (
  SELECT e.*,substr(e.code,4) stock_code,
         timezone('Asia/Shanghai',e.timestamp) local_timestamp,
         try_cast(i.acquired_at AS TIMESTAMPTZ) acquired_clock,
         i.partition_id,i.source_artifact_sha256
  FROM {exact} e JOIN {identity} i USING(filename)
), bounded AS (
  SELECT s.* FROM source s JOIN {required} r
    ON r.stock_code=s.stock_code AND r.trade_date=cast(s.local_timestamp AS DATE)
)
SELECT stock_code,local_timestamp AS ts,cast(local_timestamp AS DATE) market_date,
       '5m' bar_size,source_values,
       cast(open AS DECIMAL(38,10)) open,cast(high AS DECIMAL(38,10)) high,
       cast(low AS DECIMAL(38,10)) low,cast(close AS DECIMAL(38,10)) AS "close",
       cast(volume AS DECIMAL(38,10)) volume,cast(amount AS DECIMAL(38,10)) amount,
       CASE WHEN acquired_clock IS NULL
              THEN error('BASE24_NATIVE5M_ACQUIRED_AT_INVALID')
            ELSE acquired_clock END acquired_at,
       partition_id input_partition_id,
       'sha256:' || source_artifact_sha256 input_artifact_sha256,
       year(local_timestamp) observation_year,month(local_timestamp) observation_month
FROM bounded
"""
