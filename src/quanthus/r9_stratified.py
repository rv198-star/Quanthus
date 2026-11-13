"""One preregistered A3 pool and fixed-R0 proxy strata for a development pilot."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.r7_native import verified_members
from quanthus.r9_qualification import check_roles
from quanthus.r9_source_first import VENUES
from quanthus.stage_learning.order_rule_screen import RULE_DIGEST, _number, load_rule
from quanthus.structure_v2_inputs import canonical, digest

SEED = "R9-R0-STRATIFIED-A3-20261113"
GROUPS = (
    ("train", "2020-03-31", 12),
    ("train", "2020-04-30", 12),
    ("guard", "2020-07-31", 8),
    ("update", "2020-08-31", 8),
    ("evaluation", "2020-11-30", 12),
    ("evaluation", "2020-12-31", 12),
)


def order(row: dict[str, Any]) -> str:
    return hashlib.sha256(
        "|".join((SEED, *(row[k] for k in ("company_id", "security_id", "listing_id")))).encode()
    ).hexdigest()


def r0_support(primary: dict[str, Any], rule: dict[str, Any]) -> bool:
    """The exact four-fact R0 Stage2 predicate, only a declared sampling proxy."""
    if digest(rule) != RULE_DIGEST:
        raise ValueError("R9_A3_R0_DRIFT")
    values: dict[str, Decimal] = {
        k: _number(primary[k], k)
        for k in ("directional_efficiency", "range_width", "width_change", "center_shift")
    }
    policy = {k: Decimal(v) for k, v in rule["rules"]["stage_2"].items()}
    return (
        values["directional_efficiency"].copy_abs() <= policy["efficiency20_abs_max"]
        and values["range_width"] <= policy["range_width20_max"]
        and values["width_change"] <= policy["width_change20_max"]
        and values["center_shift"].copy_abs() <= policy["center_shift20_abs_max"]
    )


def make_pool(project: Path, source: Path, request: Path, output: Path) -> dict[str, Any]:
    import duckdb

    if output.exists():
        raise ValueError("R9_A3_FRESH_POOL")
    q = json.loads(request.read_bytes())
    if (
        q["schema_version"] != "quanthus.r9-stratified-chain.v1"
        or q["selection"]["seed"] != SEED
        or q["selection"]["candidate_count"] != 1536
    ):
        raise ValueError("R9_A3_REQUEST")
    load_rule(project)  # Pin the unchanged proxy before any candidate values.
    excluded: set[str] = set()
    for entry in q["selection"]["prior_identities"]:
        path = Path(entry["path"])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("R9_A3_EXCLUSION_HASH")
        excluded.update(r["company_id"] for r in json.loads(raw)["rows"])
    base = json.loads((project / "config/research/research-r7/request.v2.json").read_bytes())
    members = verified_members(source, project, base)
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    db.execute("SET threads=2")
    db.execute("SET memory_limit='1GB'")
    db.execute("SET autoinstall_known_extensions=false")
    db.execute("SET autoload_known_extensions=false")
    for table in ("dwd_listing", "dwd_security", "dwd_trading_calendar"):
        paths = [
            str(source / "DWD" / m["path"])
            for m in members
            if m["path"].startswith("payload/" + table + "/")
        ]
        db.read_parquet(paths, hive_partitioning=True).create_view("raw_" + table)
        db.execute(
            f"CREATE VIEW {table} AS SELECT * FROM raw_{table} WHERE "
            f"knowledge_from<=TIMESTAMPTZ '{base['source_knowledge_cutoff']}' "
            "AND knowledge_to IS NULL"
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
    names = [n[0] for n in cursor.description]
    raw_rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    counts = {
        k: Counter(r[k] for r in raw_rows) for k in ("company_id", "security_id", "listing_id")
    }
    candidates = sorted(
        (
            r
            for r in raw_rows
            if r["venue_id"] in VENUES
            and r["company_id"] not in excluded
            and r["listing_code"] not in q["selection"]["known_historical_codes"]
            and all(counts[k][r[k]] == 1 for k in counts)
        ),
        key=order,
    )[:1536]
    if len(candidates) != 1536:
        raise ValueError("R9_A3_IDENTITY_CAPACITY")
    output.mkdir(parents=True)
    (output / "identity-pool-commit.private.json").write_bytes(
        canonical({"seed": SEED, "rows": candidates, "excluded_companies": sorted(excluded)})
        + b"\n"
    )
    rows = []
    calendar_cache = {}
    for index, (role, anchor, _) in enumerate(GROUPS):
        for entity in candidates[index * 256 : (index + 1) * 256]:
            key = (entity["venue_id"], anchor)
            if key not in calendar_cache:
                calendar_cache[key] = db.execute(
                    "SELECT trade_date,calendar_session_id,open_at,close_at "
                    "FROM dwd_trading_calendar WHERE venue_id=? AND is_open "
                    "AND trade_date BETWEEN DATE '2020-01-02' AND ?::DATE "
                    "ORDER BY trade_date DESC LIMIT 40",
                    [entity["venue_id"], anchor],
                ).fetchall()[::-1]
            calendar = calendar_cache[key]
            if (
                len(calendar) != 40
                or str(calendar[-1][0]) != anchor
                or len({r[0] for r in calendar}) != 40
                or len({r[1] for r in calendar}) != 40
                or any(r[2] is None or r[3] is None or r[2] >= r[3] for r in calendar)
            ):
                raise ValueError("R9_A3_CALENDAR")
            token = hashlib.sha256((order(entity) + "|" + anchor).encode()).hexdigest()
            rows.append(
                {
                    **entity,
                    "role": role,
                    "anchor": anchor,
                    "sample_id": "r9a3-" + token[:24],
                    "episode_id": "r9a3-episode-" + token,
                    "sessions": [{"date": str(r[0]), "session_id": r[1]} for r in calendar],
                }
            )
    verified_members(source, project, base)
    db.close()
    payload = canonical({"schema": "quanthus.r9-role-identities.v1", "rows": rows}) + b"\n"
    (output / "candidate-identities.private.json").write_bytes(payload)
    return {
        "subjects": 1536,
        "identity_sha256": hashlib.sha256(payload).hexdigest(),
        "excluded_companies": len(excluded),
        "market_values_read": 0,
    }


def select_qualified(
    project: Path,
    qualified: Path,
    output: Path,
    *,
    expected_pool_sha256: str,
    expected_identity_sha256: str,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("R9_A3_FRESH_SELECTION")
    raw = qualified.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_pool_sha256:
        raise ValueError("R9_A3_POOL_HASH")
    pool = json.loads(raw)["rows"]
    identity_raw = (
        canonical(
            {"schema": "quanthus.r9-role-identities.v1", "rows": [r["identity"] for r in pool]}
        )
        + b"\n"
    )
    if hashlib.sha256(identity_raw).hexdigest() != expected_identity_sha256:
        raise ValueError("R9_A3_IDENTITY_HASH")
    if len(pool) != 1536 or len({r["identity"]["company_id"] for r in pool}) != 1536:
        raise ValueError("R9_A3_POOL_DENOMINATOR")
    rule = load_rule(project)
    ledger = []
    selected = []
    capacity = []
    for index, (role, anchor, count) in enumerate(GROUPS):
        group = pool[index * 256 : (index + 1) * 256]
        if any(
            r["identity"]["role"] != role or r["identity"]["anchor"] != anchor for r in group
        ) or group != sorted(group, key=lambda r: order(r["identity"])):
            raise ValueError("R9_A3_GROUP_ORDER")
        strata: dict[bool, list[dict[str, Any]]] = {True: [], False: []}
        for row in group:
            proxy = r0_support(row["primary_facts"], rule) if row["available"] else None
            ledger.append(
                {
                    "identity": row["identity"],
                    "available": row["available"],
                    "reason": row["reason"],
                    "r0_support": proxy,
                    "facts_digest": row["facts_digest"],
                }
            )
            if proxy is not None:
                strata[proxy].append({**row, "sampling_r0_support": proxy})
        capacity.append(
            {
                "role": role,
                "anchor": anchor,
                "support": len(strata[True]),
                "other": len(strata[False]),
                "needed_each": count // 2,
            }
        )
        if min(len(strata[True]), len(strata[False])) >= count // 2:
            selected.extend(strata[True][: count // 2] + strata[False][: count // 2])
    output.mkdir(parents=True)
    (output / "strata-ledger.private.json").write_bytes(
        canonical({"rows": ledger, "capacity": capacity}) + b"\n"
    )
    if len(selected) != 64:
        result = {
            "status": "STRATUM_CAPACITY_STOP",
            "capacity": capacity,
            "selected": len(selected),
            "reference_calls": 0,
            "fit_runs": 0,
            "update_runs": 0,
        }
    else:
        identities = {
            "schema": "quanthus.r9-role-identities.v1",
            "rows": [r["identity"] for r in selected],
        }
        check_roles(identities)
        for name, value in [
            ("role-identities.private.json", identities),
            ("qualified-cases.private.json", {"rows": selected}),
            ("reference-packets.private.json", {"rows": [r["packet"] for r in selected]}),
        ]:
            (output / name).write_bytes(canonical(value) + b"\n")
        result = {
            "status": "SOURCE_STRATA_READY",
            "capacity": capacity,
            "selected": 64,
            "identity_sha256": hashlib.sha256(
                (output / "role-identities.private.json").read_bytes()
            ).hexdigest(),
            "qualified_cases_sha256": hashlib.sha256(
                (output / "qualified-cases.private.json").read_bytes()
            ).hexdigest(),
            "reference_calls": 0,
            "fit_runs": 0,
            "update_runs": 0,
        }
    (output / "selection.safe.json").write_bytes(canonical(result) + b"\n")
    return result
