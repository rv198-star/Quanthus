"""Versioned, opt-in Daily 1d basis repair; never a consumer/PIT admission."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

POLICY = "p10-daily-unadjusted-v1"
CANDIDATE_KIND = "cn01_daily_dwd_price_basis_candidate"
SOURCE_FAMILY = "market_daily_core/daily_ohlcv_none_delta"
ORDER = "listing_id,observation_start,interval_size,price_basis,knowledge_from"


def contract(policy: str | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    if policy != POLICY:
        raise ValueError("P10_PRICE_BASIS_POLICY_UNKNOWN")
    return {
        "policy_version": POLICY,
        "schema_version": "quanthus.daily-price-basis.v1",
        "source_family": SOURCE_FAMILY,
        "interval_size": "1d",
        "source_price_basis": "none",
        "canonical_price_basis": "UNADJUSTED",
        "physical_schema": "dwd_security_bar:20-fields:decimal38_10:utc-us:v1",
        "other_intervals": "preserved_without_mapping",
        "decision_admissible": False,
        "unavailable_reason": "P10_CONSUMER_AND_PIT_ADMISSION_REQUIRED",
    }


def prepare(connection: Any, source_query: str, artifact: Any) -> str:
    """Materialize the unchanged producer projection once for mapping and equality."""
    connection.execute("CREATE TEMP TABLE p10_basis_source AS " + source_query)
    validate_rows(connection, artifact, "none")
    return (
        """SELECT * REPLACE (CASE WHEN interval_size='1d' THEN 'UNADJUSTED'
        ELSE price_basis END AS price_basis) FROM p10_basis_source ORDER BY """
        + ORDER
    )


def validate_rows(connection: Any, artifact: Any, expected_basis: str) -> None:
    schema = {row[0]: row[1] for row in connection.execute("DESCRIBE p10_basis_source").fetchall()}
    expected = dict.fromkeys(
        (
            "record_source",
            "source_record_id",
            "source_revision",
            "source_artifact_sha256",
            "listing_id",
            "calendar_session_id",
            "interval_size",
            "price_basis",
            "currency",
        ),
        "VARCHAR",
    )
    expected.update(
        dict.fromkeys(
            ("knowledge_from", "knowledge_to", "observation_start", "observation_end"),
            "TIMESTAMP WITH TIME ZONE",
        )
    )
    expected.update(
        dict.fromkeys(("open", "high", "low", "close", "volume", "turnover"), "DECIMAL(38,10)")
    )
    expected["trade_count"] = "BIGINT"
    if schema != expected:
        raise ValueError("P10_PRICE_BASIS_SOURCE_SCHEMA_UNKNOWN")
    bad = connection.execute(
        """SELECT count(*) FROM p10_basis_source WHERE interval_size='1d'
        AND (price_basis IS DISTINCT FROM ? OR source_revision IS DISTINCT FROM ?
        OR source_artifact_sha256 IS DISTINCT FROM ? OR listing_id IS NULL
        OR calendar_session_id IS NULL OR knowledge_from IS NULL
        OR observation_start IS NULL OR observation_end IS NULL
        OR observation_end<=observation_start
        OR (knowledge_to IS NOT NULL AND knowledge_to<=knowledge_from))""",
        [expected_basis, artifact.source_revision, artifact.sha256],
    ).fetchone()[0]
    if bad:
        raise ValueError("P10_PRICE_BASIS_SOURCE_IDENTITY_BASIS_OR_TIME_INVALID")
    if connection.execute("""SELECT count(*) FROM p10_basis_source WHERE interval_size='1d'
        AND ("open" IS NULL OR "high" IS NULL OR "low" IS NULL OR "close" IS NULL
        OR "open"<=0 OR "high"<=0 OR "low"<=0 OR "close"<=0
        OR "high"<greatest("open","low","close")
        OR "low">least("open","high","close") OR volume<0 OR turnover<0)""").fetchone()[0]:
        raise ValueError("P10_PRICE_BASIS_PRICE_INVALID")


def verify_preservation(connection: Any, output: Path) -> dict[str, Any]:
    """Independent Arrow readback, exact typed equality of all 20 source columns.

    Mapping uses DuckDB SQL. Verification restores only the declared 1d basis with
    Arrow and compares streamed batches against the unchanged TMP, without floats,
    rounding, string serialization or a second expensive source construction.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    validate_physical_schema(output)
    source = connection.execute("SELECT * FROM p10_basis_source ORDER BY " + ORDER)
    batches = source.fetch_record_batch(65536)
    parquet = pq.ParquetFile(output)
    count = 0
    sentinel = object()
    actual = iter(parquet.iter_batches(batch_size=65536))
    for expected in batches:
        observed = next(actual, sentinel)
        if observed is sentinel or not expected.schema.equals(observed.schema):
            raise ValueError("P10_PRICE_BASIS_SERIALIZED_SCHEMA_OR_ROWS_DIFFER")
        index = observed.schema.get_field_index("price_basis")
        daily = pc.equal(observed.column("interval_size"), "1d")
        if pc.any(
            pc.and_(daily, pc.fill_null(pc.not_equal(observed.column(index), "UNADJUSTED"), True))
        ).as_py():
            raise ValueError("P10_PRICE_BASIS_CANONICAL_VALUE_DIFFERS")
        restored = observed.set_column(
            index,
            observed.schema.field(index),
            pc.if_else(daily, pa.scalar("none"), observed.column(index)),
        )
        if not expected.equals(restored):
            raise ValueError("P10_PRICE_BASIS_SERIALIZED_SOURCE_DRIFT")
        count += expected.num_rows
    if next(actual, sentinel) is not sentinel:
        raise ValueError("P10_PRICE_BASIS_SERIALIZED_EXTRA_ROWS")
    return {
        "compared_rows_all_intervals": count,
        "compared_source_fields": len(parquet.schema_arrow),
        "source_differences": 0,
        "method": "independent_arrow_exact_typed_readback",
    }


def coverage(connection: Any, calendar: Path, status: Path, market_date: str) -> dict[str, Any]:
    """Full union denominator; non-tradable/zero volume never proves suspension."""
    bad = connection.execute(
        """SELECT count(*) FROM p10_basis_source b
        LEFT JOIN read_parquet(?) c USING(calendar_session_id) WHERE b.interval_size='1d'
        AND (c.calendar_session_id IS NULL OR c.trade_date IS DISTINCT FROM ?::DATE
        OR c.is_open IS DISTINCT FROM true OR c.close_at IS NULL
        OR b.observation_start IS DISTINCT FROM c.observation_start
        OR b.observation_end IS DISTINCT FROM c.observation_end)""",
        [str(calendar), market_date],
    ).fetchone()[0]
    if bad:
        raise ValueError("P10_PRICE_BASIS_CALENDAR_SCOPE_INVALID")
    counts = connection.execute(
        """WITH b AS (
        SELECT * FROM p10_basis_source WHERE interval_size='1d'), s AS (
        SELECT * FROM read_parquet(?)), u AS (
        SELECT b.listing_id bar_id,s.listing_id status_id,s.is_tradable,
          b.volume,b.turnover,b.knowledge_from,b.calendar_session_id
        FROM b FULL OUTER JOIN s USING(listing_id,calendar_session_id))
        SELECT count(*),count(*) FILTER(WHERE bar_id IS NULL),
          count(*) FILTER(WHERE status_id IS NULL),
          count(*) FILTER(WHERE is_tradable IS false),
          count(*) FILTER(WHERE volume=0 OR turnover=0),
          count(*) FILTER(WHERE volume IS NULL AND bar_id IS NOT NULL),
          count(*) FILTER(WHERE turnover IS NULL AND bar_id IS NOT NULL),
          count(*) FILTER(WHERE u.knowledge_from>c.close_at),
          count(*) FILTER(WHERE bar_id IS NOT NULL)
        FROM u LEFT JOIN read_parquet(?) c USING(calendar_session_id)""",
        [str(status), str(calendar)],
    ).fetchone()
    names = (
        "status_or_bar_pairs",
        "status_without_bar",
        "bar_without_status",
        "source_nontradable_status",
        "zero_activity_rows",
        "null_volume_rows",
        "null_turnover_rows",
        "raw_known_after_close_rows",
        "canonical_daily_rows",
    )
    return {
        "market_date": market_date,
        **dict(zip(names, counts, strict=True)),
        "mapping_lost_rows": 0,
        "suspension_classification": "NOT_INFERRED",
        "decision_admissible": False,
    }


def validate_manifest(manifest: Mapping[str, Any], policy: str | None) -> None:
    expected = contract(policy)
    if expected is None:
        if manifest.get("kind") == CANDIDATE_KIND or "price_basis_contract" in manifest:
            raise ValueError("P10_PRICE_BASIS_EXPLICIT_ADMISSION_REQUIRED")
    elif (
        not str(manifest.get("package_id", "")).endswith("-" + POLICY)
        or manifest.get("kind") != CANDIDATE_KIND
        or manifest.get("schema_version") != 2
        or manifest.get("price_basis_contract") != expected
    ):
        raise ValueError("P10_PRICE_BASIS_REQUESTED_CONTRACT_DIFFERS")


def verify_candidate_payload(root: Path, manifest: Mapping[str, Any]) -> None:
    """Verify candidate readback and receipt bindings after inventory SHA verification.

    This does not rerun source construction or claim a new independent source comparison.
    """
    from types import SimpleNamespace

    import duckdb

    audit = json.loads((root / "audit.json").read_text())
    attestation = audit.get("price_basis", {})
    equality = attestation.get("equality", {})
    if (
        attestation.get("contract") != contract(POLICY)
        or attestation.get("source_family") != SOURCE_FAMILY
        or equality.get("source_differences") != 0
        or equality.get("compared_source_fields") != 20
        or equality.get("method") != "independent_arrow_exact_typed_readback"
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", attestation.get("source_artifact_sha256", ""))
        or not attestation.get("source_revision")
        or attestation.get("independent_cold_construction") != "NOT_PERFORMED"
        or attestation.get("historical_replacement_comparison") != "NOT_PERFORMED"
        or attestation.get("consumer_acceptance") != "NOT_REQUESTED"
    ):
        raise ValueError("P10_PRICE_BASIS_ATTESTATION_INVALID")
    selected = {}
    for table in ("dwd_security_bar", "dwd_trading_calendar", "dwd_tradability_state"):
        matches = [root / f["path"] for f in manifest["files"] if f"/{table}/" in f["path"]]
        if len(matches) != 1:
            raise ValueError("P10_PRICE_BASIS_MEMBER_SCOPE_INVALID")
        selected[table] = matches[0]
    validate_physical_schema(selected["dwd_security_bar"])
    with duckdb.connect() as connection:
        connection.execute("SET threads=1")
        connection.execute("SET memory_limit='2GB'")
        connection.execute(
            "CREATE TEMP TABLE p10_basis_source AS "
            "SELECT * FROM read_parquet(?,hive_partitioning=false)",
            [str(selected["dwd_security_bar"])],
        )
        validate_rows(
            connection,
            SimpleNamespace(
                source_revision=attestation["source_revision"],
                sha256=attestation["source_artifact_sha256"],
            ),
            "UNADJUSTED",
        )
        count, unique = connection.execute(
            "SELECT count(*),count(DISTINCT (" + ORDER + ")) FROM p10_basis_source"
        ).fetchone()
        if (
            count != unique
            or count != equality.get("compared_rows_all_intervals")
            or count != manifest["row_counts"].get("dwd_security_bar")
        ):
            raise ValueError("P10_PRICE_BASIS_ROW_COUNT_OR_IDENTITY_DIFFERS")
        observed = coverage(
            connection,
            selected["dwd_trading_calendar"],
            selected["dwd_tradability_state"],
            manifest["market_date"],
        )
        if observed != attestation.get("coverage"):
            raise ValueError("P10_PRICE_BASIS_COVERAGE_DIFFERS")


def validate_physical_schema(path: Path) -> None:
    """Check Parquet types before DuckDB can normalize timestamp precision."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    names = (
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
        "knowledge_to",
        "observation_start",
        "observation_end",
        "listing_id",
        "calendar_session_id",
        "interval_size",
        "price_basis",
        "currency",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "trade_count",
    )
    schema = pq.ParquetFile(path).schema_arrow
    if tuple(schema.names) != names:
        raise ValueError("P10_PRICE_BASIS_PHYSICAL_SCHEMA_DIFFERS")
    for index, field in enumerate(schema):
        if 4 <= index <= 7:
            valid = (
                pa.types.is_timestamp(field.type)
                and field.type.unit == "us"
                and field.type.tz in {"UTC", "Etc/UTC"}
            )
        elif 13 <= index <= 18:
            valid = field.type == pa.decimal128(38, 10)
        elif index == 19:
            valid = field.type == pa.int64()
        else:
            valid = field.type == pa.string()
        if not valid:
            raise ValueError("P10_PRICE_BASIS_PHYSICAL_SCHEMA_DIFFERS")
