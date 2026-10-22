"""P10's bounded effective data view for an explicitly selected repair handoff.

Only data projection lives here. Strategy, experts, windows and paper ledgers
remain with P6. Old physical versions and their knowledge clocks stay intact.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.backtest.base24_semantic_projection import NATIVE_KEYS, _queries

from .base24_execution_evidence import _require
from .base24_execution_repair import verify
from .base24_status_evidence import _instant


def _q(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def overlay(c: Any, name: str, patch: str, keys: tuple[str, ...]) -> dict[str, int]:
    """Explicit key replacement, rather than UNION of simultaneously visible versions."""
    columns = ",".join(keys)
    if c.execute(
        f"SELECT count(*) FROM (SELECT {columns} FROM {patch} GROUP BY ALL HAVING count(*)<>1)"
    ).fetchone()[0]:
        raise ValueError("REPAIR_DUPLICATE_PATCH_KEY")
    _require(
        [(d[0], d[1]) for d in c.execute(f"SELECT * FROM {name} LIMIT 0").description]
        == [(d[0], d[1]) for d in c.execute(f"SELECT * FROM {patch} LIMIT 0").description],
        "REPAIR_PROJECTION_SCHEMA",
    )
    counts = c.execute(
        f"SELECT count(*),count(*) FILTER(WHERE EXISTS "
        f"(SELECT 1 FROM {name} old WHERE "
        + " AND ".join(f"old.{k}=p.{k}" for k in keys)
        + f")) FROM {patch} p"
    ).fetchone()
    c.execute(f"ALTER VIEW {name} RENAME TO unrepaired_{name}")
    c.execute(
        f"CREATE VIEW {name} AS SELECT old.* FROM unrepaired_{name} old "
        f"WHERE NOT EXISTS (SELECT 1 FROM {patch} p WHERE "
        + " AND ".join(f"old.{k}=p.{k}" for k in keys)
        + f") UNION ALL SELECT * FROM {patch}"
    )
    return {
        "patch_rows": counts[0],
        "replaced_keys": counts[1],
        "inserted_keys": counts[0] - counts[1],
    }


def select_references(c: Any, source: Path, cutoff: datetime) -> None:
    """Only affected keys require repair admission; unrelated defects stay disclosed."""
    scope = {
        "dwd_listing": "listing_id IN (SELECT listing_id FROM dwd_tradability_state)",
        "dwd_security": "security_id IN (SELECT security_id FROM dwd_listing)",
        "dwd_trading_venue": "venue_id IN (SELECT venue_id FROM dwd_listing)",
        "dwd_trading_calendar": (
            "calendar_session_id IN (SELECT calendar_session_id FROM dwd_tradability_state)"
        ),
    }
    for table, predicate in scope.items():
        path = source / "source/native" / (table + ".parquet")
        c.execute(
            f"CREATE TEMP TABLE {table} AS SELECT DISTINCT * FROM read_parquet({_q(path)}) "
            f"WHERE ({predicate}) AND knowledge_from<=TIMESTAMPTZ {_q(cutoff.isoformat())} AND "
            f"coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>TIMESTAMPTZ {_q(cutoff.isoformat())}"
        )
        _require(
            c.execute(
                f"SELECT count(*) FROM (SELECT {NATIVE_KEYS[table]} FROM {table} "
                "GROUP BY ALL HAVING count(*)<>1)"
            ).fetchone()[0]
            == 0,
            "REPAIR_REFERENCE_VERSION_AMBIGUOUS",
        )


def attach(
    c: Any, *, source: Path, repair: Path, manifest_sha: str, cutoff: datetime
) -> dict[str, Any]:
    """Attach already frozen native repair data to shared job-local source views."""
    receipt = verify(repair, manifest_sha)
    plan = json.loads((repair / "plan.json").read_bytes())
    _require(_instant(plan["context_cutoff"]) <= cutoff, "REPAIR_NOT_YET_AVAILABLE")
    c.execute(
        "CREATE TEMP TABLE dwd_tradability_state AS SELECT * FROM read_parquet("
        + _q(repair / "bounds/dwd_tradability_state.parquet")
        + ") UNION ALL "
        "SELECT * FROM read_parquet("
        + _q(repair / "single-day/dwd_tradability_state.parquet")
        + ")"
    )
    select_references(c, source, cutoff)
    c.execute(
        "CREATE TEMP TABLE dwd_security_bar AS SELECT DISTINCT b.* FROM read_parquet("
        + _q(source / "source/native/dwd_security_bar.parquet")
        + ") b "
        "JOIN dwd_tradability_state t USING(listing_id,calendar_session_id) "
        f"WHERE b.knowledge_from<=TIMESTAMPTZ {_q(cutoff.isoformat())} AND "
        f"coalesce(b.knowledge_to,'infinity'::TIMESTAMPTZ)>TIMESTAMPTZ {_q(cutoff.isoformat())} "
        "UNION ALL SELECT * FROM read_parquet("
        + _q(repair / "single-day/dwd_security_bar.parquet")
        + ")"
    )
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        _require(
            c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 11
            and c.execute(
                f"SELECT count(*) FROM (SELECT {NATIVE_KEYS[table]} FROM {table} "
                "GROUP BY ALL HAVING count(*)<>1)"
            ).fetchone()[0]
            == 0,
            "REPAIR_NATIVE_CENSUS",
        )
    results = {}
    keys = {
        "daily_ohlcv_none": ("dwd_listing_id", "date"),
        "daily_universe": ("dwd_listing_id", "market_date"),
        "price_limit_execution_daily": ("dwd_listing_id", "execution_date"),
    }
    queries = _queries()
    for table, key in keys.items():
        patch = "repair_" + table
        c.execute(f"CREATE TEMP TABLE {patch} AS " + queries[table])
        results[table] = overlay(c, "s_" + table, patch, key)
        _require(
            results[table] == {"patch_rows": 11, "replaced_keys": 10, "inserted_keys": 1},
            "REPAIR_EXACT_ALLOWED_MARKET_DIFF",
        )
    c.execute(
        "CREATE VIEW repair_q AS SELECT * FROM read_parquet("
        + _q(repair / "execution-125.private.parquet")
        + ")"
    )
    results["qualification"] = overlay(c, "q", "repair_q", ("listing_id", "market_date"))
    _require(
        results["qualification"]["patch_rows"] == 125
        and results["qualification"]["inserted_keys"] <= 1,
        "REPAIR_TARGET_CENSUS",
    )
    return {
        "manifest_sha256": manifest_sha,
        "context_cutoff": plan["context_cutoff"],
        "new_data_at_actual_later_knowledge": True,
        "changes": results,
        "qualified_targets": receipt["qualified"],
        "source_versions_preserved": True,
        "full_base_repackaged": False,
        "production_admission": False,
    }
