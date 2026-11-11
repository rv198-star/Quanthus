"""Bounded native source census for capital-consumer qualification, never PnL."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

TABLES = {
    "dwd_security",
    "dwd_listing",
    "dwd_trading_calendar",
    "dwd_security_bar",
    "dwd_tradability_state",
    "dwd_trading_venue",
    "dwd_price_limit_regime",
    "dwd_listing_status_interval",
    "dwd_security_identifier",
    "dwd_corporate_action",
    "dwd_corporate_action_term",
    "dwd_quality_exception",
}
KEYS = {
    "dwd_security": ["security_id", "effective_from"],
    "dwd_listing": ["listing_id", "effective_from"],
    "dwd_trading_calendar": ["calendar_session_id"],
    "dwd_security_bar": ["listing_id", "calendar_session_id"],
    "dwd_tradability_state": ["listing_id", "calendar_session_id"],
    "dwd_trading_venue": ["venue_id", "effective_from"],
    "dwd_price_limit_regime": ["price_limit_regime_id", "effective_from"],
    "dwd_listing_status_interval": ["listing_status_id", "effective_from"],
    "dwd_security_identifier": [
        "security_id",
        "identifier_namespace",
        "identifier_value",
        "effective_from",
    ],
    "dwd_corporate_action": ["corporate_action_id"],
    "dwd_corporate_action_term": ["corporate_action_term_id"],
    "dwd_quality_exception": ["quality_exception_id"],
}


def member_table(name: str) -> str | None:
    if name == "contract/base-build-delivery.v1.json":
        return None
    parts = Path(name).parts
    if len(parts) < 3 or parts[0] != "payload" or parts[1] not in TABLES:
        raise ValueError("undeclared native table/member")
    if Path(name).is_absolute() or ".." in parts or not name.endswith(".parquet"):
        raise ValueError("unsafe native member")
    for part in parts:
        if part.startswith("detected_at_year=") and parts[1] == "dwd_quality_exception":
            continue
        if "_year=" in part:
            year = part.rsplit("=", 1)[1]
            allowed = (
                {"2019", "2020", "2021"} if parts[1] == "dwd_trading_calendar" else {"2020", "2021"}
            )
            if year not in allowed:
                raise ValueError("protected native member partition")
    return parts[1]


def business_predicate(table: str, names: set[str], scope: dict[str, Any]) -> tuple[str, str]:
    if table not in TABLES:
        raise ValueError("unknown native table")
    start, end = scope["market_start"], scope["execution_end"]
    if table == "dwd_trading_calendar":
        column, start = "trade_date", scope["calendar_start"]
    elif table in {"dwd_security_bar", "dwd_tradability_state"}:
        column = "observation_start"
    elif table == "dwd_quality_exception":
        column = "affected_effective_at"
    elif table in {"dwd_corporate_action", "dwd_corporate_action_term"}:
        column = "effective_at"
    else:
        column = "effective_from"
    if column not in names:
        raise ValueError("required native business clock missing: " + table)
    if column == "effective_from":
        if "effective_to" not in names:
            raise ValueError("native interval end missing: " + table)
        return column, (
            f"({column} IS NULL OR (CAST({column} AS DATE)<=DATE '{end}' "
            f"AND (effective_to IS NULL OR CAST(effective_to AS DATE)>DATE '{start}')))"
        )
    return (
        column,
        f"({column} IS NULL OR CAST({column} AS DATE) BETWEEN DATE '{start}' AND DATE '{end}')",
    )


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def census(
    source: Path, members: list[dict[str, Any]], request: dict[str, Any], scratch: Path
) -> dict[str, Any]:
    import duckdb

    began = time.monotonic()
    c = duckdb.connect()
    c.execute("SET TimeZone='Asia/Shanghai'")
    c.execute(f"SET threads={request['budget']['threads']}")
    c.execute(f"SET memory_limit='{request['budget']['duckdb_memory_bytes']}B'")
    c.execute("SET temp_directory=?", [str(scratch)])
    c.execute(f"SET max_temp_directory_size='{request['budget']['max_scratch_bytes']}B'")
    c.execute("SET autoinstall_known_extensions=false")
    c.execute("SET autoload_known_extensions=false")
    grouped: dict[str, list[str]] = {}
    for item in members:
        table = member_table(item["path"])
        if table:
            grouped.setdefault(table, []).append(str(source / "DWD" / item["path"]))
    if set(grouped) != TABLES:
        raise ValueError("native table closure differs")
    tables: dict[str, Any] = {}
    stages = []
    for table, paths in sorted(grouped.items()):
        started = time.monotonic()
        relation = c.read_parquet(paths, hive_partitioning=True)
        names = set(relation.columns)
        columns = list(zip(relation.columns, map(str, relation.types), strict=True))
        entry: dict[str, Any] = {"schema": columns, "files": len(paths), "missing_required": []}
        tables[table] = entry
        if not {"knowledge_from", "knowledge_to"} <= names:
            entry["missing_required"] = sorted({"knowledge_from", "knowledge_to"} - names)
            continue
        try:
            clock, predicate = business_predicate(table, names, request["scope"])
        except ValueError as exc:
            entry["missing_required"] = [str(exc)]
            continue
        relation.create_view("source_" + table)
        c.execute(
            f"CREATE TEMP TABLE q_{table} AS SELECT * FROM source_{table} "
            f"WHERE knowledge_to IS NULL AND {predicate}"
        )
        query = c.execute(
            f"SELECT count(*),count(*) FILTER(WHERE {clock} IS NULL),"
            f"count(*) FILTER(WHERE knowledge_from IS NULL),"
            f"count(*) FILTER(WHERE CAST(knowledge_from AS DATE)>CAST({clock} AS DATE)),"
            f"min({clock}),max({clock}),min(knowledge_from),max(knowledge_from) FROM q_{table}"
        )
        count_row = query.fetchone()
        assert count_row is not None
        n, missing_clock, missing_known, later, first, last, kfirst, klast = count_row
        entry.update(
            rows=n,
            missing_business_clock=missing_clock,
            missing_source_knowledge_clock=missing_known,
            source_knowledge_after_business_date=later,
            business_min=first,
            business_max=last,
            source_knowledge_min=kfirst,
            source_knowledge_max=klast,
        )
        missing_keys = set(KEYS[table]) - names
        if table == "dwd_tradability_state":
            entry["missing_required"].extend(
                sorted({"is_tradable", "can_buy", "can_sell", "reason_code"} - names)
            )
        if table == "dwd_corporate_action_term":
            entry["missing_required"].extend(sorted({"corporate_action_id", "term_type"} - names))
        entry["missing_required"].extend(sorted(missing_keys))
        if not missing_keys:
            keys = ",".join('"' + name + '"' for name in KEYS[table])
            duplicate_row = c.execute(
                f"SELECT count(*) FROM (SELECT {keys} FROM q_{table} "
                f"GROUP BY {keys} HAVING count(*)>1)"
            ).fetchone()
            assert duplicate_row is not None
            entry["duplicate_keys"] = duplicate_row[0]
        profiles = {}
        for name in sorted(
            names
            & {
                "action_type",
                "action_status",
                "term_type",
                "unit",
                "value_unit",
                "currency",
                "quote_currency",
                "trading_currency",
                "listing_currency",
                "lot_size",
                "instrument_type",
                "listing_type",
            }
        ):
            profiles[name] = c.execute(
                f'SELECT "{name}",count(*) FROM q_{table} GROUP BY 1 ORDER BY 2 DESC,1 LIMIT 32'
            ).fetchall()
        entry["profiles"] = profiles
        if table in {"dwd_corporate_action", "dwd_corporate_action_term"}:
            entry["examples"] = [
                dict(zip(relation.columns, row, strict=True))
                for row in c.execute(
                    f"SELECT * FROM q_{table} ORDER BY 1 "
                    f"LIMIT {request['budget']['max_action_examples']}"
                ).fetchall()
            ]
        stages.append(
            {
                "stage": table,
                "disposition": "build_once_TMP",
                "rows": n,
                "wall_seconds": time.monotonic() - started,
                "source_bytes": sum(
                    x["size_bytes"] for x in members if member_table(x["path"]) == table
                ),
            }
        )
    extra: dict[str, Any] = {}
    state = tables["dwd_tradability_state"]
    if not state["missing_required"]:
        extra["directional_states"] = c.execute(
            "SELECT is_tradable,can_buy,can_sell,count(*) FROM q_dwd_tradability_state "
            "GROUP BY 1,2,3 ORDER BY 1,2,3"
        ).fetchall()
        extra["state_reasons"] = c.execute(
            "SELECT reason_code,count(*) FROM q_dwd_tradability_state "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 32"
        ).fetchall()
    if (
        not tables["dwd_corporate_action"]["missing_required"]
        and not tables["dwd_corporate_action_term"]["missing_required"]
    ):
        orphan_row = c.execute(
            "SELECT count(*) FROM q_dwd_corporate_action_term t WHERE NOT EXISTS "
            "(SELECT 1 FROM q_dwd_corporate_action a "
            "WHERE a.corporate_action_id=t.corporate_action_id)"
        ).fetchone()
        assert orphan_row is not None
        extra["terms_without_header_in_same_scope"] = orphan_row[0]
    c.close()
    result = clean(
        {
            "schema": "quanthus.native-capital-source-census.v1",
            "qualification_only": True,
            "source_clock_policy": request["scope"]["source_clock"],
            "tables": tables,
            "relations": extra,
            "stages": stages,
            "wall_seconds": time.monotonic() - began,
            "strategy_or_pnl_evaluated": False,
        }
    )
    assert isinstance(result, dict)
    return result
