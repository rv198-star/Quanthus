"""Build and resolve the formal OCI S2 point-in-time ST-status dataset."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class StStatusBuildError(RuntimeError):
    """Raised when an S2 revision cannot be published without weakening PIT semantics."""


@dataclass(frozen=True)
class StStatusBuildResult:
    revision: str
    output: Path
    observed_interval_rows: int
    execution_daily_rows: int
    unknown_rows: int
    st_class_rows: int
    non_st_class_rows: int
    checksums: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "output": str(self.output),
            "observed_interval_rows": self.observed_interval_rows,
            "execution_daily_rows": self.execution_daily_rows,
            "unknown_rows": self.unknown_rows,
            "st_class_rows": self.st_class_rows,
            "non_st_class_rows": self.non_st_class_rows,
            "checksums": dict(sorted(self.checksums.items())),
        }


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _schema_hash(connection: Any, parquet: Path) -> str:
    rows = connection.execute(f"DESCRIBE SELECT * FROM {_source(parquet)}").fetchall()
    payload = json.dumps(
        [(str(row[0]), str(row[1]), str(row[2])) for row in rows],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _verify_candidate_checksums(candidate: Path, required_names: tuple[str, ...]) -> None:
    expected: dict[str, str] = {}
    for line in (candidate / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        fields = line.strip().split()
        if len(fields) == 2 and len(fields[0]) == 64:
            expected[fields[1].lstrip("*")] = f"sha256:{fields[0]}"
    missing = sorted(set(required_names) - expected.keys())
    if missing:
        raise StStatusBuildError(
            "candidate checksum ledger is missing required inputs: " + ", ".join(missing)
        )
    mismatches = [name for name in required_names if _sha256(candidate / name) != expected[name]]
    if mismatches:
        raise StStatusBuildError(
            "candidate input checksum mismatch: " + ", ".join(sorted(mismatches))
        )


def _copy(connection: Any, query: str, path: Path) -> int:
    connection.execute(
        f"COPY ({query}) TO {_sql_string(str(path))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    row = connection.execute(f"SELECT count(*) FROM ({query})").fetchone()
    if row is None:
        raise StStatusBuildError("row-count query returned no row")
    return int(row[0])


def _load_policy(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StStatusBuildError(f"cannot load ST-status policy: {path}") from exc
    incremental = payload.get("incremental")
    if not isinstance(incremental, dict) or incremental.get("lookback_calendar_days") != 90:
        raise StStatusBuildError("ST-status policy must freeze a 90-day incremental lookback")
    if payload.get("unknown_behavior") != "block_new_positions":
        raise StStatusBuildError("unknown ST status must block new positions")
    excluded = payload.get("excluded_security_ids", [])
    if not isinstance(excluded, list) or any(not str(item).strip() for item in excluded):
        raise StStatusBuildError("excluded_security_ids must be a list of non-empty ids")
    missing_observation = payload.get("missing_observation")
    if missing_observation is not None and missing_observation != {
        "allow_when_source_bar_missing": True,
        "result": "unknown_fail_closed",
    }:
        raise StStatusBuildError("unsupported missing-observation policy")
    return payload


def build_st_status_revision(
    *,
    candidate: Path,
    output: Path,
    revision: str,
    policy_path: Path,
    parent: Path | None = None,
) -> StStatusBuildResult:
    """Publish an immutable S2 revision from an identity-normalized candidate.

    Provider state observed on session T is mapped to the next session in the security's
    actual universe. This naturally skips weekends and suspensions and prevents T state from
    influencing T execution. The first session has no prior observation and is explicit
    ``unknown`` rather than silently normal.
    """

    candidate = candidate.resolve()
    output = output.resolve()
    policy_path = policy_path.resolve()
    parent = parent.resolve() if parent is not None else None
    if not revision.strip():
        raise StStatusBuildError("revision must not be empty")
    policy = _load_policy(policy_path)
    explain_missing_with_bar = "missing_observation" in policy
    required = (
        candidate / "daily_isST.parquet",
        candidate / "daily_universe.parquet",
        candidate / "st_status_pit.parquet",
        candidate / "META.json",
        candidate / "CHECKSUMS.sha256",
    )
    if explain_missing_with_bar:
        required += (candidate / "daily_ohlcv_none.parquet",)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise StStatusBuildError("required candidate inputs are missing: " + ", ".join(missing))
    if output.exists():
        raise FileExistsError(f"refusing to overwrite S2 revision: {output}")
    excluded_ids = tuple(str(item) for item in policy.get("excluded_security_ids", []))
    excluded_predicate = (
        "security_id NOT IN (" + ", ".join(_sql_string(item) for item in excluded_ids) + ")"
        if excluded_ids
        else "TRUE"
    )
    checksum_inputs = ["daily_isST.parquet", "daily_universe.parquet", "st_status_pit.parquet"]
    if explain_missing_with_bar:
        checksum_inputs.append("daily_ohlcv_none.parquet")
    _verify_candidate_checksums(candidate, tuple(checksum_inputs))

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise StStatusBuildError("P1 dependencies are missing; run make bootstrap-p1") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    connection = duckdb.connect(":memory:")
    try:
        daily = _source(candidate / "daily_isST.parquet")
        universe = _source(candidate / "daily_universe.parquet")
        intervals = _source(candidate / "st_status_pit.parquet")
        bars = _source(candidate / "daily_ohlcv_none.parquet") if explain_missing_with_bar else None
        invalid_state_row = connection.execute(
            f"SELECT count(*) FROM {daily} WHERE isST NOT IN ('0', '1')"
        ).fetchone()
        invalid_state = int(invalid_state_row[0])
        if invalid_state:
            raise StStatusBuildError(f"candidate contains {invalid_state} non-binary isST rows")
        duplicate_daily = int(
            connection.execute(
                f"SELECT count(*) FROM (SELECT security_id, date FROM {daily} "
                "GROUP BY security_id, date HAVING count(*) > 1)"
            ).fetchone()[0]
        )
        if duplicate_daily:
            raise StStatusBuildError("candidate has duplicate (security_id, date) rows")

        observed_query = f"""
            SELECT
                security_id,
                stock_code,
                exchange,
                status_label,
                is_st_class,
                CAST(effective_start AS DATE) AS observed_start,
                CAST(effective_end AS DATE) AS observed_end,
                CAST(knowledge_cutoff AS DATE) AS source_knowledge_cutoff,
                source,
                source_revision AS parent_source_revision,
                raw_shard,
                {_sql_string(revision)} AS revision
            FROM {intervals}
            WHERE {excluded_predicate}
            ORDER BY security_id, observed_start, stock_code
        """
        observed_count = _copy(
            connection, observed_query, staging / "st_status_observed_pit.parquet"
        )

        bar_join = (
            f"""
            LEFT JOIN {bars} b
              ON b.security_id = u.security_id
             AND CAST(b.date AS DATE) = CAST(u.source_market_date AS DATE)
            """
            if bars is not None
            else ""
        )
        unknown_reason = (
            """
                CASE
                    WHEN u.source_market_date IS NULL THEN 'st_status_unknown_fail_closed'
                    WHEN b.security_id IS NULL THEN 'source_bar_and_status_missing_fail_closed'
                    ELSE 'st_status_unexplained_missing'
                END
            """
            if bars is not None
            else "'st_status_unknown_fail_closed'"
        )
        execution_query = f"""
            WITH ordered_universe AS (
                SELECT
                    *,
                    lag(market_date) OVER (
                        PARTITION BY security_id ORDER BY market_date
                    ) AS source_market_date
                FROM {universe}
                WHERE exchange IN ('SSE', 'SZSE')
                  AND stock_code NOT LIKE '920%'
                  AND {excluded_predicate}
            )
            SELECT
                u.security_id,
                u.stock_code,
                u.exchange,
                CAST(u.market_date AS DATE) AS execution_date,
                CAST(u.source_market_date AS DATE) AS source_market_date,
                CASE
                    WHEN s.isST = '1' THEN 'st_class'
                    WHEN s.isST = '0' THEN 'non_st_class'
                    ELSE 'unknown'
                END AS status_label,
                CASE
                    WHEN s.isST = '1' THEN true
                    WHEN s.isST = '0' THEN false
                    ELSE NULL
                END AS is_st_class,
                s.tradestatus AS source_tradestatus,
                CASE WHEN s.isST IN ('0', '1') THEN 'baostock.isST' ELSE NULL END AS source,
                s.raw_shard,
                CAST(u.market_date AS DATE) AS knowledge_cutoff,
                CASE WHEN s.isST IN ('0', '1') THEN true ELSE false END AS status_known,
                CASE WHEN s.isST IN ('0', '1') THEN true ELSE false END AS can_open_position,
                CASE
                    WHEN s.isST IN ('0', '1') THEN 'status_known_from_prior_session'
                    ELSE {unknown_reason}
                END AS reason_code,
                {_sql_string(revision)} AS revision
            FROM ordered_universe u
            LEFT JOIN {daily} s
              ON s.security_id = u.security_id
             AND CAST(s.date AS DATE) = CAST(u.source_market_date AS DATE)
            {bar_join}
            ORDER BY u.security_id, u.market_date, u.stock_code
        """
        execution_count = _copy(
            connection, execution_query, staging / "st_status_execution_daily.parquet"
        )
        counts = connection.execute(
            f"SELECT status_label, count(*) FROM ({execution_query}) GROUP BY status_label"
        ).fetchall()
        status_counts = {str(label): int(count) for label, count in counts}
        content_max_row = connection.execute(f"SELECT max(date) FROM {daily}").fetchone()
        if content_max_row is None or content_max_row[0] is None:
            raise StStatusBuildError("candidate daily ST evidence is empty")
        content_max = content_max_row[0]
        future_leaks = int(
            connection.execute(
                f"SELECT count(*) FROM ({execution_query}) "
                "WHERE source_market_date >= execution_date"
            ).fetchone()[0]
        )
        if future_leaks:
            raise StStatusBuildError(
                "execution mapping contains same-day or future ST observations"
            )
        unexpected_unknown = int(
            connection.execute(
                f"SELECT count(*) FROM ({execution_query}) WHERE "
                + (
                    "reason_code = 'st_status_unexplained_missing'"
                    if explain_missing_with_bar
                    else "status_label = 'unknown' AND source_market_date IS NOT NULL"
                )
            ).fetchone()[0]
        )
        if unexpected_unknown:
            raise StStatusBuildError(
                f"candidate has {unexpected_unknown} unexplained prior-session ST gaps"
            )
        explained_missing = (
            int(
                connection.execute(
                    f"SELECT count(*) FROM ({execution_query}) "
                    "WHERE reason_code = 'source_bar_and_status_missing_fail_closed'"
                ).fetchone()[0]
            )
            if explain_missing_with_bar
            else 0
        )
    finally:
        connection.close()

    try:
        files = ("st_status_observed_pit.parquet", "st_status_execution_daily.parquet")
        checksums = {name: _sha256(staging / name) for name in files}
        connection = duckdb.connect(":memory:")
        try:
            schema_hashes = {name: _schema_hash(connection, staging / name) for name in files}
        finally:
            connection.close()
        built_at = datetime.now(timezone.utc)
        knowledge_cutoff = datetime.combine(
            content_max, time(23, 59, 59), tzinfo=ZoneInfo("Asia/Shanghai")
        )
        parent_meta = None
        if parent is not None:
            parent_manifest = parent / "manifest.json"
            if not parent_manifest.is_file():
                raise StStatusBuildError("incremental parent has no manifest.json")
            parent_meta = {
                "revision": parent.name,
                "manifest_sha256": _sha256(parent_manifest),
            }
        manifest = {
            "schema_version": "1.0.0",
            "dataset": "st_status_pit",
            "revision": revision,
            "created_at": built_at.isoformat(),
            "knowledge_cutoff": knowledge_cutoff.isoformat(),
            "policy_version": policy["policy_version"],
            "source_revisions": {
                "identity_normalized_candidate": candidate.name,
                "candidate_meta_sha256": _sha256(candidate / "META.json"),
                "candidate_checksums_sha256": _sha256(candidate / "CHECKSUMS.sha256"),
            },
            "parent": parent_meta,
            "datasets": {
                "st_status_observed_pit": {
                    "path": "st_status_observed_pit.parquet",
                    "rows": observed_count,
                    "schema_hash": schema_hashes["st_status_observed_pit.parquet"],
                    "sha256": checksums["st_status_observed_pit.parquet"],
                },
                "st_status_execution_daily": {
                    "path": "st_status_execution_daily.parquet",
                    "rows": execution_count,
                    "schema_hash": schema_hashes["st_status_execution_daily.parquet"],
                    "sha256": checksums["st_status_execution_daily.parquet"],
                    "status_counts": dict(sorted(status_counts.items())),
                },
            },
            "semantics": {
                "binary_authority": policy["authoritative_binary_source"],
                "observation_to_execution": policy["execution_visibility"],
                "unknown_behavior": policy["unknown_behavior"],
                "detail_behavior": policy["detail_behavior"],
                "incremental": policy["incremental"],
                **(
                    {"missing_observation": policy["missing_observation"]}
                    if explain_missing_with_bar
                    else {}
                ),
            },
            "constraints": {
                "immutable_revision": True,
                "parent_overwritten": False,
                "same_day_or_future_observation_count": 0,
                "unexplained_unknown_count": 0,
                "oos_920_count": 0,
                "explicitly_excluded_security_count": 0,
                **(
                    {"source_bar_and_status_missing_count": explained_missing}
                    if explain_missing_with_bar
                    else {}
                ),
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
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

    return StStatusBuildResult(
        revision=revision,
        output=output,
        observed_interval_rows=observed_count,
        execution_daily_rows=execution_count,
        unknown_rows=status_counts.get("unknown", 0),
        st_class_rows=status_counts.get("st_class", 0),
        non_st_class_rows=status_counts.get("non_st_class", 0),
        checksums=checksums,
    )


def write_current_pointer(*, revision_dir: Path, pointer: Path) -> None:
    """Atomically promote a reviewed OCI S2 revision without changing Hermes pointers."""

    manifest = revision_dir / "manifest.json"
    if not manifest.is_file():
        raise StStatusBuildError(f"revision has no manifest: {revision_dir}")
    payload = {
        "dataset": "st_status_pit",
        "revision": revision_dir.name,
        "revision_path": str(revision_dir.resolve()),
        "manifest_sha256": _sha256(manifest),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    pointer.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{pointer.name}.", dir=pointer.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, pointer)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
