"""Deterministic typed Daily composition for the standard Base builder.

Only the seven declared Daily table families advance here. Other table families
retain their predecessor coverage; original inputs and knowledge_from clocks
are never rewritten. Same-instant conflicting business values fail closed.
"""
from __future__ import annotations

import importlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from .base22 import file_sha
from .base22_bounded import BoundedError, Budget, identifier
from .base22_sources import physical_path
from .base_build_inputs import canonical, identity, member
from .base_build_profile import REFERENCE_TABLES, UPDATED_TABLES

LINEAGE = frozenset({"record_source", "source_record_id", "source_revision",
                     "source_artifact_sha256", "knowledge_from", "knowledge_to"})
TEMPORAL = frozenset({"dwd_disclosure", "dwd_disclosure_subject"})


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def relation(paths: list[Path]) -> str:
    if not paths:
        raise BoundedError("BUILD_DELTA_INPUT_FILES_REQUIRED")
    return ("read_parquet([" + ",".join(literal(str(p)) for p in paths)
            + "],hive_partitioning=false,union_by_name=false)")


def normalize_versions(connection: Any, source: str, schema: Any,
                       business_key: list[str], version_key: list[str], *,
                       allow_closed_identity: bool = False) -> dict[str, int]:
    """Retain first identical knowledge; conflicting facts require a separate repair.

    Typed struct equality decides value equality, never a hash collision or
    float conversion. Source copies are retained outside the normalized view.
    """
    keys = ",".join(identifier(k) for k in version_key)
    bk = ",".join(identifier(k) for k in business_key)
    values = [name for name in schema.names if name not in LINEAGE]
    packed = "struct_pack(" + ",".join(
        identifier(name) + ":=" + identifier(name) for name in values
    ) + ")"
    connection.execute(f"CREATE OR REPLACE TEMP VIEW incoming AS SELECT *,{packed} __value "
                       f"FROM {source}")
    closed = connection.execute(
        "SELECT 1 FROM incoming WHERE knowledge_to IS NOT NULL LIMIT 1",
    ).fetchone() is not None
    if closed and not allow_closed_identity:
        # This adapter consumes the current append-only Daily representation.
        # A closed/reopened temporal interval needs an explicit version adapter;
        # deduplicating it as a fresh observation would erase an availability gap.
        raise BoundedError("BUILD_DELTA_CLOSED_KNOWLEDGE_VERSION_REQUIRES_ADAPTER")
    if allow_closed_identity and connection.execute(
        "SELECT 1 FROM incoming WHERE knowledge_to IS NOT NULL "
        "AND knowledge_to<=knowledge_from LIMIT 1",
    ).fetchone() is not None:
        raise BoundedError("BUILD_DELTA_INVALID_CLOSED_KNOWLEDGE_INTERVAL")
    if connection.execute(
        f"SELECT 1 FROM incoming GROUP BY {keys},record_source,source_revision "
        "HAVING count(*)>1 LIMIT 1",
    ).fetchone() is not None:
        raise BoundedError("BUILD_DELTA_SAME_SOURCE_DUPLICATE")
    duplicate = connection.execute(
        f"SELECT 1 FROM incoming GROUP BY {keys} HAVING count(DISTINCT __value)>1 LIMIT 1",
    ).fetchone()
    if duplicate is not None:
        raise BoundedError("BUILD_DELTA_SAME_INSTANT_BUSINESS_CONFLICT")
    revised = connection.execute(
        f"SELECT 1 FROM incoming GROUP BY {bk} HAVING count(DISTINCT __value)>1 LIMIT 1",
    ).fetchone()
    if revised is not None:
        # The frozen warehouse rule requires an exact authorized replacement;
        # later arrival alone does not establish authority to change a fact.
        raise BoundedError("BUILD_DELTA_UNAUTHORIZED_BUSINESS_REVISION")
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW versions AS SELECT * FROM incoming QUALIFY "
        f"row_number() OVER (PARTITION BY {keys} ORDER BY record_source,source_record_id,"
        "source_revision,source_artifact_sha256,knowledge_to NULLS LAST)=1",
    )
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW ordered_versions AS SELECT *,"
        f"lag(__value) OVER w __prior_value,lag(knowledge_to) OVER w __prior_end,"
        f"row_number() OVER w __n FROM versions "
        f"WINDOW w AS (PARTITION BY {bk} ORDER BY knowledge_from)",
    )
    reopen_rule = " OR __prior_end IS NOT NULL" if allow_closed_identity else ""
    connection.execute(
        "CREATE OR REPLACE TEMP VIEW changed_versions AS SELECT * FROM ordered_versions "
        "WHERE __n=1 OR __value IS DISTINCT FROM __prior_value" + reopen_rule,
    )
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW bounded_versions AS SELECT *,"
        f"lead(knowledge_from) OVER (PARTITION BY {bk} ORDER BY knowledge_from) __next "
        "FROM changed_versions",
    )
    columns = [
        ("CASE WHEN __next IS NULL THEN knowledge_to "
         "WHEN knowledge_to IS NULL OR knowledge_to>__next THEN __next "
         "ELSE knowledge_to END AS knowledge_to")
        if name == "knowledge_to" else identifier(name) for name in schema.names
    ]
    connection.execute("CREATE OR REPLACE TEMP VIEW normalized AS SELECT "
                       + ",".join(columns) + " FROM bounded_versions")
    raw = connection.execute("SELECT count(*) FROM incoming").fetchone()[0]
    normalized = connection.execute("SELECT count(*) FROM normalized").fetchone()[0]
    return {"input_rows": raw, "output_rows": normalized, "identical_repeats": raw-normalized}


def partition_expressions(rule: dict[str, Any], schema: Any) -> list[tuple[str, str]]:
    import pyarrow as pa

    result = []
    for key in rule["partition_by"]:
        if key in schema.names:
            result.append((key, identifier(key)))
            continue
        suffix = next((s for s in ("_year", "_month") if key.endswith(s)), None)
        if suffix is None:
            raise BoundedError("BUILD_DELTA_PARTITION_RULE_UNSUPPORTED")
        name = key[:-len(suffix)]
        field = schema.field(name)
        expr = identifier(name)
        if pa.types.is_timestamp(field.type) and field.type.tz is not None:
            expr = "timezone('Asia/Shanghai'," + expr + ")"
        result.append((key, "CAST(EXTRACT(" + suffix[1:] + " FROM " + expr + ") AS INTEGER)"))
    return result


def write_typed_query(connection: Any, query: str, schema: Any, target: Path,
                      budget: Budget) -> int:
    """Stream Arrow to fixed-schema Parquet, without a pandas/float round-trip."""
    pq = importlib.import_module("pyarrow.parquet")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    if target.exists() or temporary.exists():
        raise BoundedError("BUILD_DELTA_OUTPUT_EXISTS_PRESERVED")
    rows = 0
    with pq.ParquetWriter(temporary, schema, compression="zstd", version="2.6") as writer:
        for batch in connection.execute(query).to_arrow_reader(batch_size=16384):
            budget.check()
            table = importlib.import_module("pyarrow").Table.from_batches([batch])
            # Safe cast preserves schema/Decimal scale and rejects lossy changes.
            table = table.cast(schema, safe=True)
            writer.write_table(table, row_group_size=16384)
            rows += table.num_rows
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    temporary.rename(target)
    return rows


@contextmanager
def _connection(budget: Budget) -> Iterator[Any]:
    with importlib.import_module("duckdb").connect() as connection:
        connection.execute("SET threads=2")
        connection.execute("SET preserve_insertion_order=true")
        connection.execute("SET TimeZone='Asia/Shanghai'")
        connection.execute("SET memory_limit='2GB'")
        temp = budget.destination(budget.root / "temp")
        temp.mkdir(exist_ok=True)
        connection.execute("SET temp_directory=" + literal(str(temp)))
        budget._connections.append(connection)
        try:
            yield connection
        finally:
            budget._connections.remove(connection)


def build_delta(*, tables: dict[str, list[Path]], rules: dict[str, Any],
                schemas: dict[str, Any], binding: dict[str, Any], output: Path,
                budget: Budget) -> dict[str, Any]:
    """Materialize one immutable period intermediate shared by all consumers."""
    if set(tables) not in (UPDATED_TABLES, UPDATED_TABLES | REFERENCE_TABLES):
        raise BoundedError("BUILD_DELTA_EXACT_TABLE_SET_REQUIRED")
    output = budget.destination(output)
    if output.exists():
        raise BoundedError("BUILD_DELTA_OUTPUT_EXISTS_PRESERVED")
    output.mkdir()
    pq = importlib.import_module("pyarrow.parquet")
    files = []
    metrics = {}
    with _connection(budget) as connection:
        for table in sorted(tables):
            budget.begin_stage("period-delta-" + table)
            paths = [physical_path(p) for p in tables[table]]
            schema = schemas[table]
            for path in paths:
                observed_schema = pq.ParquetFile(path).schema_arrow
                if (observed_schema.names != schema.names or any(
                    left.type != right.type for left, right in zip(observed_schema, schema,
                                                                   strict=True)
                )):
                    raise BoundedError("BUILD_DELTA_SOURCE_SCHEMA_CHANGED")
            rule = rules["tables"][table]
            source = relation(paths)
            required = [identifier(f.name) + " IS NULL" for f in schema if not f.nullable]
            if required and connection.execute(
                "SELECT 1 FROM " + source + " WHERE " + " OR ".join(required) + " LIMIT 1",
            ).fetchone() is not None:
                raise BoundedError("BUILD_DELTA_REQUIRED_VALUE_NULL")
            if table in TEMPORAL | REFERENCE_TABLES:
                metrics[table] = normalize_versions(connection, source, schema,
                                                     rule["business_key"], rule["version_key"],
                                                     allow_closed_identity=(
                                                         table in REFERENCE_TABLES))
            else:
                connection.execute("CREATE OR REPLACE TEMP VIEW normalized AS SELECT * FROM "
                                   + source)
                count = connection.execute("SELECT count(*) FROM normalized").fetchone()[0]
                metrics[table] = {"input_rows": count, "output_rows": count,
                                  "identical_repeats": 0}
            # The period must never rewrite an earlier business observation.
            # An identity extension may add an earlier knowledge version of an
            # inherited fact, so the period reference clock is the capture
            # instant this build observed, never a rewritten business date.
            cutoff = ("knowledge_from" if table in REFERENCE_TABLES
                      else rule.get("cutoff_field"))
            if cutoff is None:
                candidates = [n for n in ("observation_start", "effective_at", "effective_from",
                                          "trade_date")
                              if n in schema.names]
                cutoff = candidates[0]
            field = schema.field(cutoff)
            clock = identifier(cutoff)
            pa = importlib.import_module("pyarrow")
            if pa.types.is_timestamp(field.type) and field.type.tz is not None:
                clock = "timezone('Asia/Shanghai'," + clock + ")"
            lower, upper = binding["predecessor_through"], binding["through_date"]
            date.fromisoformat(lower), date.fromisoformat(upper)
            if connection.execute(
                f"SELECT 1 FROM normalized WHERE CAST({clock} AS DATE)<={literal(lower)}::DATE "
                f"OR CAST({clock} AS DATE)>{literal(upper)}::DATE LIMIT 1",
            ).fetchone() is not None:
                raise BoundedError("BUILD_DELTA_BUSINESS_DATE_OUTSIDE_INTERVAL")
            expressions = partition_expressions(rule, schema)
            selects = ",".join(expr for _, expr in expressions)
            parts = (connection.execute("SELECT DISTINCT " + selects
                                        + " FROM normalized ORDER BY ALL").fetchall()
                     if expressions else [()] if metrics[table]["output_rows"] else [])
            if not parts:
                parts = [None]
            observed = 0
            for values in parts:
                part = "empty" if values is None else "/".join(
                    key + "=" + str(value)
                    for (key, _), value in zip(expressions, values, strict=True)
                ) or "all"
                condition = "false" if values is None else " AND ".join(
                    expr + " IS NOT DISTINCT FROM "
                    + (literal(value) if isinstance(value, str) else str(value))
                    for (_, expr), value in zip(expressions, values, strict=True)
                ) or "true"
                order = ",".join(identifier(k) for k in rule["version_key"])
                query = f"SELECT * FROM normalized WHERE {condition} ORDER BY {order}"
                relative = "tables/" + table + "/" + part + "/part-000000.parquet"
                target = member(output, relative)
                count = write_typed_query(connection, query, schema, target, budget)
                files.append({"path": relative, "table": table, "partition": part,
                              "rows": count, "size_bytes": target.stat().st_size,
                              "sha256": file_sha(target)})
                observed += count
            if observed != metrics[table]["output_rows"]:
                raise BoundedError("BUILD_DELTA_PARTITION_CENSUS_CHANGED")
            print(json.dumps({"stage": "period-delta", "table": table,
                              **metrics[table]}), flush=True)
    descriptor = {"kind": "p10-standard-base-period-dwd-v1", "binding": binding,
                  "files": files, "tables": metrics, "lifecycle": "TMP",
                  "quality": "SOURCE_REPLAYED_FULL_IMAGE_AUDIT_STILL_REQUIRED",
                  "READY": False}
    descriptor["identity"] = identity(descriptor)
    with (output / "DELTA.json").open("xb") as stream:
        stream.write(canonical(descriptor) + b"\n")
    return descriptor
