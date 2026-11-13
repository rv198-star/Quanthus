"""One prospective R9 identity pool; selection never reads prices or references."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from quanthus.r7_native import verified_members
from quanthus.r9_qualification import check_roles, quality_blocks
from quanthus.structure_v2_inputs import canonical

SEED = "R9-SOURCE-FIRST-A2-20261113"
GROUPS = (
    ("train", "2020-03-31", 12, 48),
    ("train", "2020-04-30", 12, 48),
    ("guard", "2020-07-31", 8, 32),
    ("update", "2020-08-31", 8, 32),
    ("evaluation", "2020-11-30", 12, 48),
    ("evaluation", "2020-12-31", 12, 48),
)
VENUES = ("venue_bec59e31eb6bb28111a10d7e6d2c689f", "venue_8c0bd9088a0dc88f28e5e7fa8efdce87")


def identity_order(row: dict[str, Any]) -> str:
    return hashlib.sha256(
        "|".join((SEED, *(row[k] for k in ("company_id", "security_id", "listing_id")))).encode()
    ).hexdigest()


def select_roles(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose only after a fixed, fully accounted metadata eligibility screen."""
    if len(candidates) != 256 or len({r["company_id"] for r in candidates}) != 256:
        raise ValueError("R9_A2_POOL_DENOMINATOR")
    rows: list[dict[str, Any]] = []
    offset = 0
    for role, anchor, count, pool_count in GROUPS:
        group = candidates[offset : offset + pool_count]
        offset += pool_count
        if any(r["role"] != role or r["anchor"] != anchor for r in group):
            raise ValueError("R9_A2_FIXED_GROUP")
        if group != sorted(group, key=identity_order):
            raise ValueError("R9_A2_HASH_ORDER")
        eligible = [r for r in group if not r["eligibility_reasons"]]
        if len(eligible) < count:
            raise ValueError("R9_A2_SOURCE_CAPACITY:" + role + ":" + anchor)
        rows.extend(
            {k: v for k, v in r.items() if k != "eligibility_reasons"} for r in eligible[:count]
        )
    result = {"schema": "quanthus.r9-role-identities.v1", "rows": rows}
    check_roles(result)
    return result


def screen(project: Path, source: Path, prior: Path, output: Path) -> dict[str, Any]:
    import duckdb
    import pyarrow as pa  # type: ignore[import-untyped]

    if output.exists():
        raise ValueError("R9_A2_FRESH_OUTPUT")
    if hashlib.sha256(prior.read_bytes()).hexdigest() != (
        "5a63f72cc0af7a1bf0936596a9eece09ecce6b064d1ddbe10ff01562d143ec0d"
    ):
        raise ValueError("R9_A2_PRIOR_EXPOSURE_HASH")
    excluded = {r["company_id"] for r in json.loads(prior.read_bytes())["rows"]}
    base = json.loads((project / "config/research/research-r7/request.v2.json").read_bytes())
    members = verified_members(source, project, base)
    cutoff = base["source_knowledge_cutoff"]
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    db.execute("SET threads=2")
    db.execute("SET memory_limit='1GB'")
    db.execute("SET autoinstall_known_extensions=false")
    db.execute("SET autoload_known_extensions=false")
    for table in (
        "dwd_listing",
        "dwd_security",
        "dwd_trading_calendar",
        "dwd_corporate_action",
        "dwd_tradability_state",
        "dwd_quality_exception",
    ):
        paths = [
            str(source / "DWD" / m["path"])
            for m in members
            if m["path"].startswith("payload/" + table + "/")
        ]
        db.read_parquet(paths, hive_partitioning=True).create_view("raw_" + table)
        db.execute(
            f"CREATE VIEW {table} AS SELECT * FROM raw_{table} "
            f"WHERE knowledge_from<=TIMESTAMPTZ '{cutoff}' AND knowledge_to IS NULL"
        )
    cursor = db.execute("""
        SELECT s.company_id,s.security_id,l.listing_id,l.listing_code,l.venue_id
        FROM dwd_listing l JOIN dwd_security s USING(security_id)
        WHERE s.instrument_type='common_equity' AND l.listing_currency='CNY'
          AND CAST(l.effective_from AS DATE)<=DATE '2020-01-02'
          AND (l.effective_to IS NULL OR CAST(l.effective_to AS DATE)>DATE '2020-12-31')
          AND CAST(s.effective_from AS DATE)<=DATE '2020-01-02'
          AND (s.effective_to IS NULL OR CAST(s.effective_to AS DATE)>DATE '2020-12-31')
    """)
    names = [x[0] for x in cursor.description]
    raw = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    counts = {k: Counter(r[k] for r in raw) for k in ("company_id", "security_id", "listing_id")}
    pool = sorted(
        (
            r
            for r in raw
            if r["venue_id"] in VENUES
            and r["company_id"] not in excluded
            and all(counts[k][r[k]] == 1 for k in counts)
        ),
        key=identity_order,
    )
    if len(pool) < 256:
        raise ValueError("R9_A2_IDENTITY_CAPACITY")
    output.mkdir(parents=True)
    # Immutable input commitment precedes the first action/state/quality values.
    (output / "candidate-pool.private.json").write_bytes(
        canonical(
            {
                "seed": SEED,
                "groups": GROUPS,
                "rows": pool[:256],
                "prior64_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
            }
        )
        + b"\n"
    )
    quality = db.execute(
        "SELECT affected_table,affected_business_key_json,affected_effective_at "
        "FROM dwd_quality_exception WHERE resolution_state NOT IN ('RESOLVED','CLOSED')"
    ).fetchall()
    actions: dict[str, list[Any]] = {}
    for security, ex_day in db.execute(
        "SELECT security_id,ex_date FROM dwd_corporate_action WHERE action_status='IMPLEMENTED'"
    ).fetchall():
        actions.setdefault(security, []).append(ex_day)
    candidates = []
    calendars = {}
    offset = 0
    for role, anchor, _, pool_count in GROUPS:
        for entity in pool[offset : offset + pool_count]:
            calendar_key = (entity["venue_id"], anchor)
            if calendar_key not in calendars:
                calendars[calendar_key] = db.execute(
                    "SELECT trade_date,calendar_session_id,open_at,close_at "
                    "FROM dwd_trading_calendar WHERE venue_id=? AND is_open "
                    "AND trade_date BETWEEN DATE '2020-01-02' AND ?::DATE "
                    "ORDER BY trade_date DESC LIMIT 40",
                    [entity["venue_id"], anchor],
                ).fetchall()[::-1]
            calendar = calendars[calendar_key]
            if (
                len(calendar) != 40
                or str(calendar[-1][0]) != anchor
                or len({r[0] for r in calendar}) != 40
                or len({r[1] for r in calendar}) != 40
                or any(r[2] is None or r[3] is None or r[2] >= r[3] for r in calendar)
            ):
                raise ValueError("R9_A2_CALENDAR")
            token = hashlib.sha256((identity_order(entity) + "|" + anchor).encode()).hexdigest()
            identity = {
                **entity,
                "role": role,
                "anchor": anchor,
                "sample_id": "r9a2-" + token[:24],
                "episode_id": "r9a2-episode-" + token,
                "sessions": [{"date": str(r[0]), "session_id": r[1]} for r in calendar],
            }
            reasons = []
            if any(
                d is None or calendar[0][0] <= d <= calendar[-1][0]
                for d in actions.get(entity["security_id"], [])
            ):
                reasons.append("implemented_action_or_unknown_date")
            for table, key, effective in quality:
                if quality_blocks(identity, table, json.loads(key), effective):
                    reasons.append("unresolved_required_input:" + table)
            candidates.append({**identity, "eligibility_reasons": sorted(set(reasons))})
        offset += pool_count
    # Materialize this shared prerequisite once, rather than rescanning state per session.
    selected = [
        {
            "sample_id": r["sample_id"],
            "listing_id": r["listing_id"],
            "session_id": s[1],
            "open_at": s[2],
            "close_at": s[3],
        }
        for r in candidates
        for s in calendars[(r["venue_id"], r["anchor"])]
    ]
    db.register("selected", pa.Table.from_pylist(selected))
    counts_by_case = dict(
        db.execute("""
        SELECT s.sample_id, count(*)=40 AND count(t.calendar_session_id)=40
          AND bool_and((t.is_tradable AND t.observation_start=s.open_at
                        AND t.observation_end=s.close_at) IS TRUE)
        FROM selected s LEFT JOIN dwd_tradability_state t
          ON t.listing_id=s.listing_id AND t.calendar_session_id=s.session_id
        GROUP BY s.sample_id
    """).fetchall()
    )
    for identity in candidates:
        if not counts_by_case[identity["sample_id"]]:
            identity["eligibility_reasons"].append("state_missing_duplicate_or_untradable")
    verified_members(source, project, base)
    db.close()
    (output / "candidate-screen.private.json").write_bytes(canonical({"rows": candidates}) + b"\n")
    try:
        roles = select_roles(candidates)
    except ValueError as exc:
        (output / "screen.safe.json").write_bytes(
            canonical(
                {
                    "status": "SOURCE_CAPACITY_STOP",
                    "reason": str(exc),
                    "fixed_candidates": 256,
                    "eligible": sum(not r["eligibility_reasons"] for r in candidates),
                    "price_rows_read": 0,
                    "reference_calls": 0,
                    "fit_runs": 0,
                }
            )
            + b"\n"
        )
        raise
    payload = canonical(roles) + b"\n"
    (output / "role-identities.private.json").write_bytes(payload)
    summary = {
        "identity_pool": len(pool),
        "fixed_candidates": 256,
        "selected": 64,
        "eligible": sum(not r["eligibility_reasons"] for r in candidates),
        "identity_sha256": hashlib.sha256(payload).hexdigest(),
        "price_rows_read": 0,
        "reference_calls": 0,
        "fit_runs": 0,
        "selection": "source metadata only; fixed groups/hash order; no replacement after freeze",
    }
    (output / "screen.safe.json").write_bytes(canonical(summary) + b"\n")
    return summary
