"""OCI daily DWD partition build and fail-closed promotion."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.code_validity import build_code_validity_table, load_code_validity_mapping
from quanthus.pipelines.contracts import OdsReleaseError, verify_ods_release
from quanthus.universe import load_universe_policy


class DailyWarehouseError(RuntimeError):
    """Raised when a daily ODS release cannot produce an accepted DWD partition."""


def market_session_is_open(*, calendar: Path, market_date: date) -> bool:
    """Resolve one SSE/SZSE date; reject absent or contradictory calendar evidence."""

    calendar = calendar.resolve()
    if not calendar.is_file():
        raise DailyWarehouseError(f"trading calendar does not exist: {calendar}")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DailyWarehouseError("P1 dependencies are missing; run make bootstrap-p1") from exc
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            f"""
            SELECT exchange, CAST(is_open AS INTEGER)
            FROM {_source(calendar)}
            WHERE exchange IN ('SSE', 'SZSE') AND CAST(trade_date AS DATE) = ?
            ORDER BY exchange
            """,
            [market_date],
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != 2 or {str(row[0]) for row in rows} != {"SSE", "SZSE"}:
        raise DailyWarehouseError(
            f"trading calendar has no complete SSE/SZSE evidence for {market_date}"
        )
    states = {int(row[1]) for row in rows}
    if not states <= {0, 1} or len(states) != 1:
        raise DailyWarehouseError(f"trading calendar disagrees for {market_date}")
    return states == {1}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _scalar(connection: Any, query: str, parameters: list[Any] | None = None) -> int:
    row = connection.execute(query, parameters or []).fetchone()
    if row is None:
        raise DailyWarehouseError("daily DWD scalar query returned no row")
    return int(row[0])


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyWarehouseError(f"cannot load JSON object: {path}") from exc
    if not isinstance(payload, dict):
        raise DailyWarehouseError(f"JSON root must be an object: {path}")
    return payload


def _checksum_ledger(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DailyWarehouseError(f"cannot read checksum ledger: {path}") from exc
    for line in lines:
        fields = line.strip().split()
        if len(fields) == 2 and len(fields[0]) == 64:
            expected[fields[1].lstrip("*")] = f"sha256:{fields[0]}"
    return expected


@dataclass(frozen=True)
class DailyDwdResult:
    revision: str
    output: Path
    market_date: date
    row_count: int
    security_count: int
    status_row_count: int
    excluded_row_count: int
    checksums: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "market_date": self.market_date.isoformat(),
            "row_count": self.row_count,
            "security_count": self.security_count,
            "status_row_count": self.status_row_count,
            "excluded_row_count": self.excluded_row_count,
            "checksums": dict(sorted(self.checksums.items())),
        }


@dataclass(frozen=True)
class DailyDwsResult:
    """One immutable OCI service-layer summary derived from a daily DWD revision."""

    revision: str
    output: Path
    market_date: date
    metrics: Mapping[str, int | float]
    checksums: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "market_date": self.market_date.isoformat(),
            "metrics": dict(sorted(self.metrics.items())),
            "checksums": dict(sorted(self.checksums.items())),
        }


def build_daily_dwd_revision(
    *,
    release_dir: Path,
    output: Path,
    revision: str,
    market_date: date,
    universe_policy_path: Path,
    code_validity_mapping_path: Path,
    code_revision: str,
    parent_pointer: Path | None = None,
    base_pointer: Path | None = None,
) -> DailyDwdResult:
    """Normalize one T-day ODS delta into one immutable, identity-keyed DWD partition."""

    if not revision.strip():
        raise DailyWarehouseError("revision must not be empty")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", code_revision) is None:
        raise DailyWarehouseError("code_revision must be a full lowercase Git object id")
    release_dir = release_dir.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite daily DWD revision: {output}")
    try:
        verified = verify_ods_release(release_dir)
    except OdsReleaseError as exc:
        raise DailyWarehouseError(str(exc)) from exc
    if verified.release.schedule != "daily" or verified.release.market_date != market_date:
        raise DailyWarehouseError("daily ODS release schedule or market_date does not match build")
    artifact_by_name = {artifact.name: artifact for artifact in verified.release.artifacts}
    required_artifacts = {
        "daily_ohlcv_none_delta",
        "daily_status_observation_delta",
        "security_master",
        "stock_list",
        "trading_calendar",
    }
    missing_artifacts = sorted(required_artifacts - artifact_by_name.keys())
    if missing_artifacts:
        raise DailyWarehouseError(
            "daily ODS release is missing artifacts: " + ", ".join(missing_artifacts)
        )
    bars_path = release_dir.joinpath(
        *artifact_by_name["daily_ohlcv_none_delta"].relative_path.parts
    )
    master_path = release_dir.joinpath(*artifact_by_name["security_master"].relative_path.parts)
    status_path = release_dir.joinpath(
        *artifact_by_name["daily_status_observation_delta"].relative_path.parts
    )
    calendar_path = release_dir.joinpath(*artifact_by_name["trading_calendar"].relative_path.parts)
    stock_list_path = release_dir.joinpath(*artifact_by_name["stock_list"].relative_path.parts)
    if (
        len(
            {
                bars_path.parent,
                master_path.parent,
                status_path.parent,
                calendar_path.parent,
                stock_list_path.parent,
            }
        )
        != 1
    ):
        raise DailyWarehouseError("daily artifacts must share one ODS artifact root")
    raw_root = bars_path.parent
    if bars_path.name != "daily_ohlcv_none_delta.parquet":
        raise DailyWarehouseError("daily bar artifact must use daily_ohlcv_none_delta.parquet")
    if master_path.name != "ref_security_master.parquet":
        raise DailyWarehouseError("security master artifact must use ref_security_master.parquet")
    if status_path.name != "daily_status_observation_delta.parquet":
        raise DailyWarehouseError(
            "daily status artifact must use daily_status_observation_delta.parquet"
        )
    if calendar_path.name != "ref_trading_calendar.parquet":
        raise DailyWarehouseError("calendar artifact must use ref_trading_calendar.parquet")
    if stock_list_path.name != "stock_list.parquet":
        raise DailyWarehouseError("stock-list artifact must use stock_list.parquet")
    if not market_session_is_open(calendar=calendar_path, market_date=market_date):
        raise DailyWarehouseError("refusing to build a daily partition for a closed session")

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DailyWarehouseError("P1 dependencies are missing; run make bootstrap-p1") from exc

    policy = load_universe_policy(universe_policy_path)
    mapping = load_code_validity_mapping(code_validity_mapping_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        validity_path = staging / "ref_security_code_validity.parquet"
        validity_rows, validity_checksum = build_code_validity_table(
            root=raw_root,
            mapping=mapping,
            output=validity_path,
        )
        bars = _source(bars_path)
        statuses = _source(status_path)
        stock_list = _source(stock_list_path)
        validity = _source(validity_path)
        available_columns = {
            str(row[0]) for row in connection.execute(f"DESCRIBE SELECT * FROM {bars}").fetchall()
        }
        required_columns = {
            "stock_code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover_rate",
            "pct_change",
            "adj_mode",
            "adj_version",
            "source",
            "built_at",
        }
        missing_columns = sorted(required_columns - available_columns)
        if missing_columns:
            raise DailyWarehouseError(
                "daily bar delta is missing columns: " + ", ".join(missing_columns)
            )
        status_columns = {
            str(row[0])
            for row in connection.execute(f"DESCRIBE SELECT * FROM {statuses}").fetchall()
        }
        missing_status_columns = sorted(
            {"stock_code", "date", "tradestatus", "isST", "source", "built_at"} - status_columns
        )
        if missing_status_columns:
            raise DailyWarehouseError(
                "daily status delta is missing columns: " + ", ".join(missing_status_columns)
            )
        wrong_date = _scalar(
            connection,
            f"SELECT count(*) FROM {bars} WHERE CAST(date AS DATE) <> ? OR date IS NULL",
            [market_date],
        )
        if wrong_date:
            raise DailyWarehouseError(f"daily bar delta contains {wrong_date} rows outside T")
        wrong_status_date = _scalar(
            connection,
            f"SELECT count(*) FROM {statuses} WHERE CAST(date AS DATE) <> ? OR date IS NULL",
            [market_date],
        )
        if wrong_status_date:
            raise DailyWarehouseError(
                f"daily status delta contains {wrong_status_date} rows outside T"
            )
        duplicates = _scalar(
            connection,
            f"SELECT count(*) FROM (SELECT stock_code, date FROM {bars} "
            "GROUP BY stock_code, date HAVING count(*) > 1)",
        )
        if duplicates:
            raise DailyWarehouseError(f"daily bar delta contains {duplicates} duplicate keys")
        status_duplicates = _scalar(
            connection,
            f"SELECT count(*) FROM (SELECT stock_code, date FROM {statuses} "
            "GROUP BY stock_code, date HAVING count(*) > 1)",
        )
        if status_duplicates:
            raise DailyWarehouseError(
                f"daily status delta contains {status_duplicates} duplicate keys"
            )
        invalid_status = _scalar(
            connection,
            f"SELECT count(*) FROM {statuses} "
            "WHERE CAST(tradestatus AS VARCHAR) NOT IN ('0', '1') "
            "OR CAST(isST AS VARCHAR) NOT IN ('0', '1')",
        )
        if invalid_status:
            raise DailyWarehouseError(
                f"daily status delta contains {invalid_status} non-binary observations"
            )
        unmatched = _scalar(
            connection,
            f"""
                SELECT count(*)
                FROM {bars} b
                LEFT JOIN {validity} v
                  ON v.stock_code = b.stock_code
                 AND CAST(b.date AS DATE) >= CAST(v.valid_from AS DATE)
                 AND (v.valid_to IS NULL OR CAST(b.date AS DATE) <= CAST(v.valid_to AS DATE))
                WHERE v.security_id IS NULL
                """,
        )
        if unmatched:
            raise DailyWarehouseError(
                f"daily bar delta has {unmatched} rows without point-in-time identity"
            )
        exchanges = ", ".join(_sql_string(item) for item in policy.allowed_exchanges)
        exclusions = ", ".join(_sql_string(item) for item in sorted(policy.excluded_security_ids))
        exclusion_clause = f"AND v.security_id NOT IN ({exclusions})" if exclusions else ""
        expected_query = f"""
            SELECT v.*
            FROM {validity} v
            JOIN {stock_list} listed USING (stock_code)
            WHERE v.exchange IN ({exchanges})
              AND CAST(v.valid_from AS DATE) <= CAST({_sql_string(market_date.isoformat())} AS DATE)
              AND (
                    v.valid_to IS NULL
                 OR CAST(v.valid_to AS DATE) >= CAST({_sql_string(market_date.isoformat())} AS DATE)
              )
              {exclusion_clause}
        """
        coverage = connection.execute(
            f"""
            WITH expected AS ({expected_query})
            SELECT
                count(*),
                count(*) FILTER (WHERE s.stock_code IS NULL),
                count(*) FILTER (
                    WHERE CAST(s.tradestatus AS VARCHAR) = '1' AND b.stock_code IS NULL
                )
            FROM expected v
            LEFT JOIN {statuses} s
              ON s.stock_code = v.stock_code AND CAST(s.date AS DATE) = ?
            LEFT JOIN {bars} b
              ON b.stock_code = v.stock_code AND CAST(b.date AS DATE) = ?
            """,
            [market_date, market_date],
        ).fetchone()
        if coverage is None:
            raise DailyWarehouseError("daily status coverage query returned no row")
        expected_count, missing_status_count, tradable_missing_bar_count = (
            int(value) for value in coverage
        )
        if missing_status_count:
            raise DailyWarehouseError(
                f"daily status coverage is missing {missing_status_count} in-scope securities"
            )
        if tradable_missing_bar_count:
            raise DailyWarehouseError(
                f"daily bars are missing {tradable_missing_bar_count} tradable securities"
            )
        normalized_status_query = f"""
            SELECT
                v.security_id,
                v.stock_code,
                v.exchange,
                CAST(s.date AS DATE) AS market_date,
                CAST(s.tradestatus AS VARCHAR) AS observed_tradestatus,
                CAST(s.isST AS VARCHAR) AS observed_is_st,
                CAST(s.source AS VARCHAR) AS source,
                CAST(s.built_at AS TIMESTAMPTZ) AS source_built_at,
                {_sql_string(verified.release.release_id)} AS ods_release_id,
                {_sql_string(revision)} AS dwd_revision
            FROM ({expected_query}) v
            JOIN {statuses} s
              ON s.stock_code = v.stock_code AND CAST(s.date AS DATE) = ?
        """
        query = f"""
            SELECT
                v.security_id,
                b.stock_code,
                v.exchange,
                CAST(b.date AS DATE) AS market_date,
                CAST(b.open AS DOUBLE) AS open,
                CAST(b.high AS DOUBLE) AS high,
                CAST(b.low AS DOUBLE) AS low,
                CAST(b.close AS DOUBLE) AS close,
                CAST(b.volume AS DOUBLE) AS volume,
                CAST(b.amount AS DOUBLE) AS amount,
                CAST(b.turnover_rate AS DOUBLE) AS turnover_rate,
                CAST(b.pct_change AS DOUBLE) AS pct_change,
                CAST(b.adj_mode AS VARCHAR) AS adj_mode,
                CAST(b.adj_version AS VARCHAR) AS adj_version,
                CAST(b.source AS VARCHAR) AS source,
                CAST(b.built_at AS TIMESTAMPTZ) AS source_built_at,
                CAST(s.tradestatus AS VARCHAR) AS observed_tradestatus,
                CAST(s.isST AS VARCHAR) AS observed_is_st,
                {_sql_string(verified.release.release_id)} AS ods_release_id,
                {_sql_string(revision)} AS dwd_revision
            FROM {bars} b
            JOIN {validity} v
              ON v.stock_code = b.stock_code
             AND CAST(b.date AS DATE) >= CAST(v.valid_from AS DATE)
             AND (v.valid_to IS NULL OR CAST(b.date AS DATE) <= CAST(v.valid_to AS DATE))
            JOIN {statuses} s
              ON s.stock_code = b.stock_code AND CAST(s.date AS DATE) = CAST(b.date AS DATE)
            WHERE v.exchange IN ({exchanges})
              {exclusion_clause}
        """
        counts = connection.execute(
            f"""
            SELECT
                count(*),
                count(DISTINCT security_id),
                count(*) FILTER (
                    WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                       OR volume IS NULL OR amount IS NULL
                       OR open < 0 OR high < 0 OR low < 0 OR close < 0
                       OR volume < 0 OR amount < 0 OR low > high
                       OR open < low OR open > high OR close < low OR close > high
                       OR adj_mode <> 'none'
                )
            FROM ({query})
            """
        ).fetchone()
        if counts is None:
            raise DailyWarehouseError("daily DWD count query returned no row")
        row_count, security_count, invalid_count = (int(value) for value in counts)
        if row_count == 0:
            raise DailyWarehouseError("daily DWD partition would be empty")
        if invalid_count:
            raise DailyWarehouseError(f"daily DWD partition has {invalid_count} invalid rows")
        input_count = _scalar(connection, f"SELECT count(*) FROM {bars}")
        excluded_count = input_count - row_count
        partition_dir = staging / f"market_date={market_date.isoformat()}"
        partition_dir.mkdir()
        daily_output = partition_dir / "part-00000.parquet"
        connection.execute(
            f"COPY (SELECT * FROM ({query}) ORDER BY security_id, stock_code) "
            f"TO {_sql_string(str(daily_output))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        status_output = partition_dir / "daily_status_observation.parquet"
        connection.execute(
            f"COPY (SELECT * FROM ({normalized_status_query}) ORDER BY security_id, stock_code) "
            f"TO {_sql_string(str(status_output))} (FORMAT PARQUET, COMPRESSION ZSTD)",
            [market_date],
        )
        status_row_count = _scalar(
            connection,
            f"SELECT count(*) FROM ({normalized_status_query})",
            [market_date],
        )
        if status_row_count != expected_count:
            raise DailyWarehouseError(
                "normalized daily status count does not match expected universe"
            )
        checksums = {
            f"market_date={market_date.isoformat()}/daily_status_observation.parquet": _sha256(
                status_output
            ),
            f"market_date={market_date.isoformat()}/part-00000.parquet": _sha256(daily_output),
            "ref_security_code_validity.parquet": validity_checksum,
        }
        parent: dict[str, Any] | None = None
        if parent_pointer is not None and parent_pointer.is_file():
            parent_payload = _load_object(parent_pointer)
            try:
                parent_market_date = date.fromisoformat(str(parent_payload["market_date"]))
            except (KeyError, ValueError) as exc:
                raise DailyWarehouseError(
                    "prior daily DWD pointer has an invalid market_date"
                ) from exc
            if parent_market_date > market_date:
                raise DailyWarehouseError("refusing to build behind daily DWD CURRENT")
            parent_revision_path = Path(str(parent_payload.get("revision_path", "")))
            checked_parent = check_daily_dwd_revision(parent_revision_path)
            parent_manifest_path = checked_parent.output / "manifest.json"
            if _sha256(parent_manifest_path) != parent_payload.get("manifest_sha256"):
                raise DailyWarehouseError("prior daily DWD pointer manifest checksum mismatch")
            if parent_market_date == market_date:
                parent_manifest = _load_object(parent_manifest_path)
                lineage = parent_manifest.get("lineage")
                if not isinstance(lineage, Mapping) or not isinstance(
                    lineage.get("parent"), Mapping
                ):
                    raise DailyWarehouseError("superseded T revision has no valid parent lineage")
                parent = dict(lineage["parent"])
            else:
                parent = {
                    "kind": "daily_partition",
                    "revision": checked_parent.revision,
                    "manifest_sha256": parent_payload.get("manifest_sha256"),
                }
        elif base_pointer is not None and base_pointer.is_file():
            base_payload = _load_object(base_pointer)
            parent = {
                "kind": "historical_base",
                "revision": base_payload.get("revision"),
                "manifest_sha256": base_payload.get("manifest_sha256")
                or base_payload.get("meta_sha256"),
            }
        if parent is None or not parent.get("revision") or not parent.get("manifest_sha256"):
            raise DailyWarehouseError(
                "daily DWD requires a valid prior daily pointer or historical base pointer"
            )
        manifest = {
            "schema_version": 1,
            "kind": "oci_daily_dwd_partition",
            "dataset": "daily_market_dwd",
            "revision": revision,
            "market_date": market_date.isoformat(),
            "knowledge_cutoff": verified.release.created_at.isoformat(),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "transformation": {
                "code_revision": code_revision,
                "contract": "t-close-plus-5h-v1",
            },
            "lineage": {
                "ods_release_id": verified.release.release_id,
                "ods_manifest_sha256": verified.manifest_sha256,
                "parent": parent,
            },
            "policy": {
                "universe_version": policy.version,
                "universe_digest": policy.digest,
                "code_validity_version": mapping.version,
                "code_validity_digest": mapping.digest,
            },
            "counts": {
                "input_rows": input_count,
                "output_rows": row_count,
                "security_count": security_count,
                "status_rows": status_row_count,
                "expected_universe_rows": expected_count,
                "missing_status_rows": missing_status_count,
                "tradable_missing_bar_rows": tradable_missing_bar_count,
                "excluded_rows": excluded_count,
                "invalid_rows": invalid_count,
                "validity_rows": validity_rows,
            },
            "checksums": checksums,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
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
    finally:
        connection.close()
    return DailyDwdResult(
        revision=revision,
        output=output,
        market_date=market_date,
        row_count=row_count,
        security_count=security_count,
        status_row_count=status_row_count,
        excluded_row_count=excluded_count,
        checksums=checksums,
    )


def check_daily_dwd_revision(revision_dir: Path) -> DailyDwdResult:
    """Recheck a complete immutable daily partition before CURRENT publication."""

    revision_dir = revision_dir.resolve()
    manifest_path = revision_dir / "manifest.json"
    checksum_path = revision_dir / "CHECKSUMS.sha256"
    manifest = _load_object(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "oci_daily_dwd_partition":
        raise DailyWarehouseError("unsupported daily DWD manifest")
    revision = str(manifest.get("revision", ""))
    if revision != revision_dir.name:
        raise DailyWarehouseError("daily DWD revision does not match its directory")
    try:
        market_date = date.fromisoformat(str(manifest["market_date"]))
    except (KeyError, ValueError) as exc:
        raise DailyWarehouseError("daily DWD manifest has an invalid market_date") from exc
    expected = _checksum_ledger(checksum_path)
    declared = manifest.get("checksums")
    if not isinstance(declared, Mapping) or dict(declared) != expected:
        raise DailyWarehouseError("daily DWD manifest and checksum ledger disagree")
    for relative, checksum in expected.items():
        path = revision_dir / relative
        if not path.is_file() or _sha256(path) != checksum:
            raise DailyWarehouseError(f"daily DWD checksum mismatch: {relative}")
    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        raise DailyWarehouseError("daily DWD manifest has no counts")
    if (
        int(counts.get("output_rows", 0)) <= 0
        or int(counts.get("invalid_rows", -1)) != 0
        or int(counts.get("status_rows", 0)) != int(counts.get("expected_universe_rows", -1))
        or int(counts.get("missing_status_rows", -1)) != 0
        or int(counts.get("tradable_missing_bar_rows", -1)) != 0
    ):
        raise DailyWarehouseError("daily DWD row-count or validity gate failed")
    return DailyDwdResult(
        revision=revision,
        output=revision_dir,
        market_date=market_date,
        row_count=int(counts["output_rows"]),
        security_count=int(counts["security_count"]),
        status_row_count=int(counts["status_rows"]),
        excluded_row_count=int(counts["excluded_rows"]),
        checksums=expected,
    )


def promote_daily_dwd_revision(*, revision_dir: Path, pointer: Path) -> DailyDwdResult:
    """Atomically advance one logical daily-partition chain after every gate has passed."""

    result = check_daily_dwd_revision(revision_dir)
    pointer = pointer.resolve()
    if pointer.is_file():
        current = _load_object(pointer)
        try:
            current_date = date.fromisoformat(str(current["market_date"]))
        except (KeyError, ValueError) as exc:
            raise DailyWarehouseError(
                "current daily DWD pointer has an invalid market_date"
            ) from exc
        if result.market_date < current_date:
            raise DailyWarehouseError("refusing to move daily DWD CURRENT backward")
    manifest_path = result.output / "manifest.json"
    payload = {
        "schema_version": 1,
        "dataset": "daily_market_dwd",
        "revision": result.revision,
        "revision_path": str(result.output),
        "market_date": result.market_date.isoformat(),
        "manifest_sha256": _sha256(manifest_path),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    pointer.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{pointer.name}.", dir=pointer.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, pointer)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return result


_DWS_METRIC_COLUMNS = (
    "security_count",
    "tradable_security_count",
    "suspended_security_count",
    "st_security_count",
    "bar_security_count",
    "advanced_security_count",
    "declined_security_count",
    "flat_security_count",
    "missing_pct_change_security_count",
    "total_volume",
    "total_amount",
)


def _daily_dws_summary_query(
    *, market_date: date, dwd_revision: str, bars: Path, statuses: Path
) -> str:
    target = _sql_string(market_date.isoformat())
    revision = _sql_string(dwd_revision)
    return f"""
        WITH status_rows AS (
            SELECT *
            FROM {_source(statuses)}
            WHERE CAST(market_date AS DATE) = CAST({target} AS DATE)
        ),
        bar_rows AS (
            SELECT *
            FROM {_source(bars)}
            WHERE CAST(market_date AS DATE) = CAST({target} AS DATE)
        )
        SELECT
            CAST({target} AS DATE) AS market_date,
            {revision} AS dwd_revision,
            count(*)::BIGINT AS security_count,
            count(*) FILTER (WHERE observed_tradestatus = '1')::BIGINT
                AS tradable_security_count,
            count(*) FILTER (WHERE observed_tradestatus = '0')::BIGINT
                AS suspended_security_count,
            count(*) FILTER (WHERE observed_is_st = '1')::BIGINT AS st_security_count,
            (SELECT count(DISTINCT security_id)::BIGINT FROM bar_rows) AS bar_security_count,
            (SELECT count(DISTINCT security_id)::BIGINT
             FROM bar_rows WHERE pct_change > 0) AS advanced_security_count,
            (SELECT count(DISTINCT security_id)::BIGINT
             FROM bar_rows WHERE pct_change < 0) AS declined_security_count,
            (SELECT count(DISTINCT security_id)::BIGINT
             FROM bar_rows WHERE pct_change = 0) AS flat_security_count,
            (SELECT count(DISTINCT security_id)::BIGINT
             FROM bar_rows WHERE pct_change IS NULL) AS missing_pct_change_security_count,
            (SELECT coalesce(
                sum(CAST(volume AS DECIMAL(38, 6))), CAST(0 AS DECIMAL(38, 6))
             )::DOUBLE FROM bar_rows) AS total_volume,
            (SELECT coalesce(
                sum(CAST(amount AS DECIMAL(38, 6))), CAST(0 AS DECIMAL(38, 6))
             )::DOUBLE FROM bar_rows) AS total_amount
        FROM status_rows
    """


def _daily_dws_metrics(row: tuple[Any, ...]) -> dict[str, int | float]:
    values = row[2:]
    metrics: dict[str, int | float] = {}
    for column, value in zip(_DWS_METRIC_COLUMNS, values, strict=True):
        metrics[column] = float(value) if column.startswith("total_") else int(value)
    return metrics


def _validate_daily_dws_metrics(metrics: Mapping[str, Any]) -> dict[str, int | float]:
    missing = sorted(set(_DWS_METRIC_COLUMNS) - set(metrics))
    if missing:
        raise DailyWarehouseError("daily DWS metrics are missing: " + ", ".join(missing))
    normalized: dict[str, int | float] = {}
    for column in _DWS_METRIC_COLUMNS:
        value = metrics[column]
        normalized[column] = float(value) if column.startswith("total_") else int(value)
    security_count = int(normalized["security_count"])
    tradable_count = int(normalized["tradable_security_count"])
    suspended_count = int(normalized["suspended_security_count"])
    bar_count = int(normalized["bar_security_count"])
    direction_count = sum(
        int(normalized[column])
        for column in (
            "advanced_security_count",
            "declined_security_count",
            "flat_security_count",
            "missing_pct_change_security_count",
        )
    )
    if (
        security_count <= 0
        or tradable_count < 0
        or suspended_count < 0
        or tradable_count + suspended_count != security_count
        or int(normalized["st_security_count"]) < 0
        or int(normalized["st_security_count"]) > security_count
        or bar_count < 0
        or bar_count > security_count
        or direction_count != bar_count
        or float(normalized["total_volume"]) < 0
        or float(normalized["total_amount"]) < 0
    ):
        raise DailyWarehouseError("daily DWS summary metrics violate coverage invariants")
    return normalized


def build_daily_dws_revision(
    *,
    dwd_revision_dir: Path,
    output: Path,
    revision: str,
    market_date: date,
) -> DailyDwsResult:
    """Build one immutable market-summary DWS projection from an accepted DWD partition."""

    if not revision.strip():
        raise DailyWarehouseError("revision must not be empty")
    dwd = check_daily_dwd_revision(dwd_revision_dir)
    if dwd.market_date != market_date:
        raise DailyWarehouseError("daily DWD market_date does not match DWS build")
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite daily DWS revision: {output}")
    bars = dwd.output / f"market_date={market_date.isoformat()}" / "part-00000.parquet"
    statuses = (
        dwd.output / f"market_date={market_date.isoformat()}" / "daily_status_observation.parquet"
    )
    if not bars.is_file() or not statuses.is_file():
        raise DailyWarehouseError("daily DWD partition is missing summary inputs")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DailyWarehouseError("P1 dependencies are missing; run make bootstrap-p1") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        query = _daily_dws_summary_query(
            market_date=market_date,
            dwd_revision=dwd.revision,
            bars=bars,
            statuses=statuses,
        )
        partition_dir = staging / f"market_date={market_date.isoformat()}"
        partition_dir.mkdir()
        summary_path = partition_dir / "daily_market_summary.parquet"
        connection.execute(
            f"COPY ({query}) TO {_sql_string(str(summary_path))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        persisted = connection.execute(f"SELECT * FROM {_source(summary_path)}").fetchone()
        if persisted is None:
            raise DailyWarehouseError("daily DWS summary query returned no row")
        metrics = _validate_daily_dws_metrics(_daily_dws_metrics(tuple(persisted)))
        checksums = {
            f"market_date={market_date.isoformat()}/daily_market_summary.parquet": _sha256(
                summary_path
            )
        }
        dwd_manifest = dwd.output / "manifest.json"
        manifest = {
            "schema_version": 1,
            "kind": "oci_daily_dws_market_summary",
            "dataset": "daily_market_summary_dws",
            "revision": revision,
            "market_date": market_date.isoformat(),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "lineage": {
                "dwd_revision": dwd.revision,
                "dwd_revision_path": str(dwd.output),
                "dwd_manifest_sha256": _sha256(dwd_manifest),
            },
            "metrics": metrics,
            "checksums": checksums,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
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
    finally:
        connection.close()
    return DailyDwsResult(
        revision=revision,
        output=output,
        market_date=market_date,
        metrics=metrics,
        checksums=checksums,
    )


def check_daily_dws_revision(revision_dir: Path) -> DailyDwsResult:
    """Recheck an immutable daily DWS market-summary revision before publication."""

    revision_dir = revision_dir.resolve()
    manifest_path = revision_dir / "manifest.json"
    checksum_path = revision_dir / "CHECKSUMS.sha256"
    manifest = _load_object(manifest_path)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "oci_daily_dws_market_summary"
    ):
        raise DailyWarehouseError("unsupported daily DWS manifest")
    revision = str(manifest.get("revision", ""))
    if revision != revision_dir.name:
        raise DailyWarehouseError("daily DWS revision does not match its directory")
    try:
        market_date = date.fromisoformat(str(manifest["market_date"]))
    except (KeyError, ValueError) as exc:
        raise DailyWarehouseError("daily DWS manifest has an invalid market_date") from exc
    lineage = manifest.get("lineage")
    if not isinstance(lineage, Mapping):
        raise DailyWarehouseError("daily DWS manifest has no DWD lineage")
    dwd_path = Path(str(lineage.get("dwd_revision_path", "")))
    dwd = check_daily_dwd_revision(dwd_path)
    if dwd.market_date != market_date or dwd.revision != lineage.get("dwd_revision"):
        raise DailyWarehouseError("daily DWS DWD lineage does not match its market_date")
    if _sha256(dwd.output / "manifest.json") != lineage.get("dwd_manifest_sha256"):
        raise DailyWarehouseError("daily DWS DWD manifest checksum mismatch")
    expected = _checksum_ledger(checksum_path)
    declared = manifest.get("checksums")
    if not isinstance(declared, Mapping) or dict(declared) != expected:
        raise DailyWarehouseError("daily DWS manifest and checksum ledger disagree")
    for relative, checksum in expected.items():
        path = revision_dir / relative
        if not path.is_file() or _sha256(path) != checksum:
            raise DailyWarehouseError(f"daily DWS checksum mismatch: {relative}")
    metrics = _validate_daily_dws_metrics(manifest.get("metrics", {}))
    summary_path = (
        revision_dir / f"market_date={market_date.isoformat()}" / "daily_market_summary.parquet"
    )
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DailyWarehouseError("P1 dependencies are missing; run make bootstrap-p1") from exc
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(f"SELECT * FROM {_source(summary_path)}").fetchall()
        columns = [str(column[0]) for column in connection.description]
    finally:
        connection.close()
    expected_columns = ["market_date", "dwd_revision", *_DWS_METRIC_COLUMNS]
    if columns != expected_columns or len(rows) != 1:
        raise DailyWarehouseError("daily DWS summary must contain one canonical summary row")
    row = rows[0]
    if date.fromisoformat(str(row[0])) != market_date or str(row[1]) != dwd.revision:
        raise DailyWarehouseError("daily DWS summary lineage row does not match manifest")
    persisted = _validate_daily_dws_metrics(_daily_dws_metrics(tuple(row)))
    if persisted != metrics:
        raise DailyWarehouseError("daily DWS summary row does not match manifest metrics")
    return DailyDwsResult(
        revision=revision,
        output=revision_dir,
        market_date=market_date,
        metrics=metrics,
        checksums=expected,
    )


def promote_daily_dws_revision(*, revision_dir: Path, pointer: Path) -> DailyDwsResult:
    """Atomically advance the daily DWS summary pointer after full verification."""

    result = check_daily_dws_revision(revision_dir)
    pointer = pointer.resolve()
    if pointer.is_file():
        current = _load_object(pointer)
        try:
            current_date = date.fromisoformat(str(current["market_date"]))
        except (KeyError, ValueError) as exc:
            raise DailyWarehouseError(
                "current daily DWS pointer has an invalid market_date"
            ) from exc
        if result.market_date < current_date:
            raise DailyWarehouseError("refusing to move daily DWS CURRENT backward")
    payload = {
        "schema_version": 1,
        "dataset": "daily_market_summary_dws",
        "revision": result.revision,
        "revision_path": str(result.output),
        "market_date": result.market_date.isoformat(),
        "manifest_sha256": _sha256(result.output / "manifest.json"),
        "dwd_revision": _load_object(result.output / "manifest.json")["lineage"]["dwd_revision"],
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    pointer.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{pointer.name}.", dir=pointer.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, pointer)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return result
