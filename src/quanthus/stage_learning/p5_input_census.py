"""Bounded, outcome-blind P5 three-table supply census; never qualifies ACTIVE."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from typing import Any

from quanthus.stage_learning.p5_contracts import REQUEST_DIGEST, TARGET_DIGEST

TABLES = ("dwd_security_bar", "dwd_tradability_state", "dwd_trading_calendar")
VENUES = ("venue_bec59e31eb6bb28111a10d7e6d2c689f", "venue_8c0bd9088a0dc88f28e5e7fa8efdce87")
COMMON = {
    "observation_start": "TIMESTAMP WITH TIME ZONE",
    "observation_end": "TIMESTAMP WITH TIME ZONE",
    "knowledge_from": "TIMESTAMP WITH TIME ZONE",
    "knowledge_to": "TIMESTAMP WITH TIME ZONE",
    "record_source": "VARCHAR",
    "source_record_id": "VARCHAR",
    "source_revision": "VARCHAR",
    "source_artifact_sha256": "VARCHAR",
    "calendar_session_id": "VARCHAR",
}
SCHEMAS = {
    TABLES[0]: {
        **COMMON,
        "listing_id": "VARCHAR",
        "interval_size": "VARCHAR",
        "price_basis": "VARCHAR",
        "currency": "VARCHAR",
        **{c: "DECIMAL(38,10)" for c in ("open", "high", "low", "close", "volume", "turnover")},
    },
    TABLES[1]: {
        **COMMON,
        "listing_id": "VARCHAR",
        "is_tradable": "BOOLEAN",
        "can_buy": "BOOLEAN",
        "can_sell": "BOOLEAN",
        "reason_code": "VARCHAR",
        "input_revision_digest": "VARCHAR",
        "upper_price": "DECIMAL(38,10)",
        "lower_price": "DECIMAL(38,10)",
    },
    TABLES[2]: {
        **COMMON,
        "venue_id": "VARCHAR",
        "trade_date": "DATE",
        "session_type": "VARCHAR",
        "is_open": "BOOLEAN",
        "open_at": "TIMESTAMP WITH TIME ZONE",
        "close_at": "TIMESTAMP WITH TIME ZONE",
    },
}
KEYS = {
    TABLES[0]: "listing_id, observation_start, interval_size, price_basis",
    TABLES[1]: "listing_id, calendar_session_id, observation_start",
    TABLES[2]: "venue_id, trade_date, session_type",
}
BUDGET = {
    "threads": 2,
    "memory_limit_mib": 1024,
    "maximum_temp_bytes": 4294967296,
    "timeout_seconds": 600,
}


class CensusError(ValueError):
    """A selected input or census contract failed closed."""


def digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


@contextmanager
def bounded_tmp(connection: Any, root: Path, *, deadline: float, limit: int) -> Iterator[None]:
    """Interrupt DuckDB on time or aggregate DB/WAL/spill budget excess; never seal it."""
    stopped = Event()
    failure: list[str] = []

    def check() -> None:
        if time.monotonic() > deadline:
            failure.append("census elapsed-time budget exceeded")
        total = 0
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass  # A completed spill may disappear while the counter walks TMP.
        if total > limit:
            failure.append("census aggregate TMP budget exceeded")
        if failure:
            connection.interrupt()

    def watch() -> None:
        while not stopped.wait(0.2):
            try:
                check()
            except OSError:
                failure.append("census TMP budget inspection failed")
                connection.interrupt()
            if failure:
                return

    thread = Thread(target=watch, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()
        check()
        if failure:
            raise CensusError(failure[0])


def checked_child(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not relative:
        raise CensusError("unsafe input member")
    path = root / rel
    if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
        raise CensusError("symlink input member")
    if not path.is_file():
        raise CensusError("input member unavailable")
    return path


def validate_request(request: dict[str, Any]) -> None:
    if (
        request.get("schema_version") != "quanthus.p5-stage-001-input-census-request.v1"
        or request.get("activation_issue") != 402
        or request.get("request_sha256") != REQUEST_DIGEST
        or request.get("target_set_sha256") != TARGET_DIGEST
        or request.get("market_start") != "2012-01-01"
        or request.get("market_end") != "2021-12-31"
        or request.get("venues") != dict(zip(("SSE", "SZSE"), VENUES, strict=True))
        or request.get("resource_budget") != BUDGET
    ):
        raise CensusError("frozen P5 census boundary differs")
    files = request.get("files", [])
    if not files or digest(files) != request.get("selection_digest"):
        raise CensusError("selected file identity differs")
    paths = [entry["path"] for entry in files]
    if paths != sorted(set(paths)):
        raise CensusError("selection must be ordered and unique")
    if {entry["table"] for entry in files} != set(TABLES):
        raise CensusError("only the three frozen DWD families are permitted")
    for entry in files:
        table = entry["table"]
        domain = "security_market_observation" if table == TABLES[0] else "tradability_state"
        candidate_domain = "trading_calendar" if table == TABLES[2] else domain
        partition = (
            r"trade_date_year=(201[2-9]|202[01])"
            if table == TABLES[2]
            else (
                r"observation_start_year=(201[2-9]|202[01])/"
                r"observation_start_month=(0[1-9]|1[0-2])"
            )
        )
        interval = "interval_size=1d/" if table == TABLES[0] else ""
        pattern = rf"payload/{domain}/{table}/{interval}{partition}/part-[0-9]+\.parquet"
        if (
            entry.get("candidate_domain") != candidate_domain
            or re.fullmatch(pattern, entry["path"]) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", entry.get("sha256", "")) is None
            or type(entry.get("size_bytes")) is not int
            or entry["size_bytes"] <= 0
        ):
            raise CensusError("selected member outside the bounded source contract")


def verify_inputs(root: Path, request: dict[str, Any]) -> dict[str, list[Path]]:
    """Verify the entire selected closure before opening any Parquet schema/row."""
    validate_request(request)
    if root.is_symlink():
        raise CensusError("symlink source root")
    manifest_path = checked_child(root, "manifest.json")
    expected = request["base_manifest_sha256"]
    if file_digest(manifest_path) != expected:
        raise CensusError("Base manifest checksum differs")
    if checked_child(root, "READY").read_text().strip() != expected:
        raise CensusError("Base READY differs")
    inventory = json.loads(manifest_path.read_text())["files"]
    paths = [entry["path"] for entry in inventory]
    if len(paths) != len(set(paths)):
        raise CensusError("Base inventory repeats a member")
    inventory = {entry["path"]: entry for entry in inventory}
    result: dict[str, list[Path]] = {table: [] for table in TABLES}
    for entry in request["files"]:
        original = inventory.get(entry["path"], {})
        if any(
            original.get(key) != entry[key]
            for key in ("path", "candidate_domain", "sha256", "size_bytes")
        ):
            raise CensusError("selection differs from Base inventory")
        path = checked_child(root, entry["path"])
        if path.stat().st_size != entry["size_bytes"] or file_digest(path) != entry["sha256"]:
            raise CensusError("selected payload checksum/size differs")
        result[entry["table"]].append(path)
    return result


def _one(connection: Any, query: str) -> dict[str, Any]:
    result = connection.execute(query)
    # Integer aggregates here are counts. Empty/all-null predicates mean known zero;
    # missing date extrema and downstream qualification remain null.
    return {
        column[0]: (0 if value is None and str(column[1]) in {"BIGINT", "HUGEINT"} else value)
        for column, value in zip(result.description, result.fetchone(), strict=True)
    }


def census_tables(connection: Any, paths: dict[str, list[Path]]) -> dict[str, Any]:
    """Each source is projected once, then all accounting reuses job-local TMP tables."""
    schemas: dict[str, Any] = {}
    stages = []
    counts = {}
    for table in TABLES:
        started = time.monotonic()
        expected = SCHEMAS[table]
        # Check every file: union_by_name must not conceal a member schema drift.
        for path in paths[table]:
            observed = {
                row[0]: row[1]
                for row in connection.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning=false)",
                    [str(path)],
                ).fetchall()
            }
            if any(observed.get(key) != kind for key, kind in expected.items()):
                raise CensusError(f"source schema differs: {table}")
            if table in schemas and schemas[table] != observed:
                raise CensusError(f"source member schemas differ: {table}")
            schemas[table] = observed
        # No price/volume values, labels, security master or correction source are read.
        selected = [c for c in expected if expected[c] != "DECIMAL(38,10)"]
        connection.execute(
            f"CREATE TABLE {table} AS SELECT {', '.join(selected)} "
            "FROM read_parquet(?, hive_partitioning=false)",
            [[str(path) for path in paths[table]]],
        )
        counts[table] = _one(
            connection,
            f"""
            SELECT count(*) AS rows, count_if(calendar_session_id IS NULL) AS null_session_rows,
              count_if(knowledge_from IS NULL) AS null_knowledge_from_rows,
              count_if(knowledge_to IS NULL) AS open_knowledge_interval_rows,
              count_if(knowledge_to <= knowledge_from) AS invalid_knowledge_interval_rows,
              min(knowledge_from) AS earliest_knowledge_from,
              max(knowledge_from) AS latest_knowledge_from,
              count_if(observation_start IS NULL OR observation_end IS NULL
                       OR observation_end <= observation_start)
                       AS invalid_observation_interval_rows,
              count_if(CAST(observation_start AT TIME ZONE 'Asia/Shanghai' AS DATE)
                       NOT BETWEEN DATE '2012-01-01' AND DATE '2021-12-31') AS outside_scope_rows
            FROM {table}
        """,
        )
        if counts[table]["outside_scope_rows"]:
            raise CensusError("payload contains rows outside the frozen partition scope")
        if table == TABLES[2]:
            calendar_dates = _one(
                connection,
                """
                SELECT count_if(trade_date IS NULL OR trade_date NOT BETWEEN
                    DATE '2012-01-01' AND DATE '2021-12-31') invalid_trade_date_rows,
                  count_if(trade_date <> CAST(observation_start AT TIME ZONE
                    'Asia/Shanghai' AS DATE)) observation_date_mismatch_rows,
                  count_if(is_open AND (close_at IS NULL OR open_at IS NULL
                    OR close_at <= open_at OR trade_date <> CAST(close_at AT TIME ZONE
                    'Asia/Shanghai' AS DATE))) invalid_open_session_time_rows
                FROM dwd_trading_calendar
            """,
            )
            counts[table].update(calendar_dates)
            if any(calendar_dates.values()):
                raise CensusError("calendar trade date/session time outside frozen boundary")
        keys = KEYS[table]
        counts[table].update(
            _one(
                connection,
                f"""
            SELECT coalesce(sum(n-1), 0)::BIGINT AS duplicate_row_key_excess,
                   count_if(n>1) AS duplicated_row_keys
            FROM (SELECT count(*) n FROM {table} GROUP BY {keys}, knowledge_from)
        """,
            )
        )
        counts[table].update(
            _one(
                connection,
                f"""
            SELECT count_if(n>1) AS multiversion_business_slots
            FROM (SELECT count(DISTINCT knowledge_from) n FROM {table} GROUP BY {keys})
        """,
            )
        )
        stages.append(
            {
                "stage": "project",
                "table": table,
                "lifecycle": "TMP",
                "disposition": "built_once",
                "rows": counts[table]["rows"],
                "source_bytes": sum(path.stat().st_size for path in paths[table]),
                "elapsed_seconds": round(time.monotonic() - started, 6),
            }
        )

    started = time.monotonic()
    venue_sql = ",".join("'" + venue + "'" for venue in VENUES)
    connection.execute("""
        CREATE TABLE calendar_slots AS
        SELECT calendar_session_id, count(*) calendar_versions,
          min(trade_date) trade_date, min(venue_id) venue_id, min(session_type) session_type,
          bool_and(is_open) is_open, min(close_at) close_at
        FROM dwd_trading_calendar GROUP BY calendar_session_id
    """)
    counts[TABLES[2]].update(
        _one(
            connection,
            f"""
        SELECT count(*) session_ids, count_if(calendar_versions<>1) ambiguous_session_ids,
          count_if(calendar_versions=1 AND venue_id IN ({venue_sql})
            AND session_type='REGULAR_DAY' AND is_open) admitted_open_session_ids
        FROM calendar_slots
    """,
        )
    )
    for table, slot in ((TABLES[0], "bar_slots"), (TABLES[1], "state_slots")):
        keys_valid = "listing_id IS NOT NULL AND calendar_session_id IS NOT NULL"
        scope = "interval_size='1d' AND price_basis='UNADJUSTED'" if table == TABLES[0] else "true"
        scope_null = (
            "interval_size IS NULL OR price_basis IS NULL" if table == TABLES[0] else "false"
        )
        counts[table].update(
            _one(
                connection,
                f"""
            SELECT count_if(NOT ({keys_valid})) AS null_identity_key_rows,
              count_if(NOT ({scope}) OR {scope_null}) AS invalid_business_scope_rows
            FROM {table}
        """,
            )
        )
        connection.execute(f"""
            CREATE TABLE {slot} AS
            SELECT listing_id, calendar_session_id, count(*) versions,
              min(CAST(observation_start AT TIME ZONE 'Asia/Shanghai' AS DATE)) first_date,
              max(CAST(observation_start AT TIME ZONE 'Asia/Shanghai' AS DATE)) last_date
            FROM {table} WHERE {keys_valid} AND {scope}
            GROUP BY listing_id, calendar_session_id
        """)
        counts[table].update(
            _one(
                connection,
                f"""
            SELECT count(*) listing_session_slots,
              count_if(versions<>1) ambiguous_listing_session_slots,
              count_if(c.calendar_session_id IS NULL) orphan_calendar_slots,
              count_if(c.calendar_versions<>1) ambiguous_calendar_slots,
              count_if(s.first_date<>c.trade_date OR s.last_date<>c.trade_date)
                session_date_mismatch_slots
            FROM {slot} s LEFT JOIN calendar_slots c USING(calendar_session_id)
        """,
            )
        )
        counts[table].update(
            _one(
                connection,
                f"""
            SELECT count_if(s.knowledge_from <= c.close_at
                AND (s.knowledge_to IS NULL OR c.close_at < s.knowledge_to))
                AS warehouse_version_interval_covers_session_close_rows,
              count_if(s.knowledge_from > c.close_at) AS warehouse_version_after_session_close_rows
            FROM {table} s JOIN calendar_slots c USING(calendar_session_id)
            WHERE c.calendar_versions=1 AND c.venue_id IN ({venue_sql})
              AND c.session_type='REGULAR_DAY' AND c.is_open
        """,
            )
        )
    joined = _one(
        connection,
        f"""
        WITH pairs AS (
          SELECT coalesce(b.listing_id,s.listing_id) listing_id,
            coalesce(b.calendar_session_id,s.calendar_session_id) calendar_session_id,
            b.versions bar_versions, s.versions state_versions,
            b.first_date bar_first_date, b.last_date bar_last_date,
            s.first_date state_first_date, s.last_date state_last_date
          FROM bar_slots b FULL OUTER JOIN state_slots s USING(listing_id,calendar_session_id)
        )
        SELECT count(*) listing_session_union_slots,
          count_if(bar_versions IS NOT NULL AND state_versions IS NOT NULL) matched_slots,
          count_if(state_versions IS NULL) bar_only_slots,
          count_if(bar_versions IS NULL) state_only_slots,
          count_if(c.calendar_session_id IS NULL) orphan_calendar_slots,
          count_if(c.calendar_versions<>1) ambiguous_calendar_slots,
          count_if(c.calendar_versions=1 AND c.venue_id IN ({venue_sql})
            AND c.session_type='REGULAR_DAY' AND c.is_open) authorized_open_calendar_slots,
          count_if(bar_versions=1 AND state_versions=1 AND c.calendar_versions=1
            AND c.venue_id IN ({venue_sql}) AND c.session_type='REGULAR_DAY' AND c.is_open
            AND bar_first_date=c.trade_date AND bar_last_date=c.trade_date
            AND state_first_date=c.trade_date AND state_last_date=c.trade_date)
            AS unique_date_aligned_supply_slots
        FROM pairs LEFT JOIN calendar_slots c USING(calendar_session_id)
    """,
    )
    stages.append(
        {
            "stage": "account",
            "depends_on": list(TABLES),
            "disposition": "reused_TMP",
            "rows": joined["listing_session_union_slots"],
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
    )
    return {
        "source_schemas": schemas,
        "projection_schema_contract": SCHEMAS,
        "schema_digest": digest(schemas),
        "table_counts": counts,
        "joins": joined,
        "stages": stages,
        "security_population_count": None,
        "active_population_count": None,
        "qualified_120_session_windows": None,
        "independent_reference_capacity": None,
        "qualification_reason": "IDENTITY_COORDINATE_ACTIVE_AND_REFERENCE_NOT_ESTABLISHED",
        "b_acceptance": False,
        "model_training": False,
        "strategy_result_read": False,
        "pit_claim": "WAREHOUSE_VERSION_DIAGNOSTICS_ONLY_NOT_PUBLIC_AS_KNOWN_QUALIFICATION",
    }


def run_census(
    *, root: Path, request_path: Path, output: Path, code_revision: str
) -> dict[str, Any]:
    import duckdb

    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise CensusError("exact code revision required")
    if output.exists():
        raise CensusError("output already exists")
    request = json.loads(request_path.read_text())
    started = time.monotonic()
    paths = verify_inputs(root, request)
    verified_seconds = time.monotonic() - started
    if verified_seconds >= BUDGET["timeout_seconds"]:
        raise CensusError("census elapsed-time budget exceeded during verification")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="p5-census-", dir=output) as temporary:
        connection = duckdb.connect(str(Path(temporary) / "census.duckdb"))
        try:
            connection.execute("SET threads=2")
            connection.execute("SET memory_limit='1GB'")
            connection.execute("SET max_temp_directory_size='4GB'")
            connection.execute("SET TimeZone='UTC'")
            with bounded_tmp(
                connection,
                Path(temporary),
                deadline=started + BUDGET["timeout_seconds"],
                limit=BUDGET["maximum_temp_bytes"],
            ):
                result = census_tables(connection, paths)
                connection.execute("CHECKPOINT")
            result["temporary_database_bytes"] = sum(
                p.stat().st_size for p in Path(temporary).rglob("*") if p.is_file()
            )
        finally:
            connection.close()
    result.update(
        {
            "schema_version": "quanthus.p5-stage-001-input-census-result.v1",
            "code_revision": code_revision,
            "request_sha256": file_digest(request_path),
            "source_selection_digest": request["selection_digest"],
            "base_manifest_sha256": request["base_manifest_sha256"],
            "selected_file_count": len(request["files"]),
            "selected_bytes": sum(e["size_bytes"] for e in request["files"]),
            "verification_seconds": round(verified_seconds, 6),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "temporary_intermediates_removed": True,
            "enforced_resource_budget": BUDGET,
        }
    )
    summary = output / "summary.json"
    summary.write_text(json.dumps(result, sort_keys=True, indent=2, default=str) + "\n")
    manifest = {
        "schema_version": "quanthus.p5-stage-001-input-census-manifest.v1",
        "code_revision": code_revision,
        "request_sha256": file_digest(request_path),
        "files": [
            {
                "path": "summary.json",
                "sha256": file_digest(summary),
                "size_bytes": summary.stat().st_size,
            }
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (output / "READY").write_text(file_digest(output / "manifest.json") + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    result = run_census(
        root=args.root,
        request_path=args.request,
        output=args.output,
        code_revision=args.code_revision,
    )
    print(
        json.dumps(
            {"output": str(args.output), "joins": result["joins"], "b_acceptance": False},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
