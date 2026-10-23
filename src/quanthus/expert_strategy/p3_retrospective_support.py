"""Read a P10 handoff through the distinct R2 purpose; never repair warehouse rows.

Original native clocks and nullable fields are retained beside explicitly named
P10 schedule/support metadata. The strict NativeView and historical P3 gates are
not an alternate entrypoint to this adapter.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.expert_strategy import p3_base24_bridge as strict
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_bridge as bridge
from quanthus.expert_strategy import p3_retrospective_preparation as prep
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.pipelines.securities_warehouse import p10_session_schedule as schedule

VERSION = "p3-p10-retrospective-support-consumption.v1"


def static_memberships(
    securities: Sequence[Mapping[str, Any]],
    listings: Sequence[Mapping[str, Any]],
    memberships: Sequence[Mapping[str, Any]],
    nodes: Sequence[Mapping[str, Any]],
    venues: Sequence[Mapping[str, Any]],
    *,
    business_end: date,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Static concentration projection only, with no historical membership claim.

    The input already passed fixed-snapshot row admission. Conflicting groups and
    unresolved ancestry remain missing; never create an 'unknown' industry bucket.
    """
    node = {n["industry_node_id"]: n for n in nodes}
    if len(node) != len(nodes):
        raise native.InputError("R2_STATIC_NODE_VERSION_AMBIGUOUS")
    by_company: dict[str, set[str]] = defaultdict(set)
    bad_companies = set()
    # One declared static snapshot per company, fixed before any comparison.
    # This is deliberately not an effective-at-historical-T industry join.
    declared: dict[str, date] = {}
    for m in memberships:
        stamp = m.get("source_declared_as_of")
        if type(stamp) is date and stamp <= business_end:
            declared[m["company_id"]] = max(declared.get(m["company_id"], date.min), stamp)
    memberships = [
        m
        for m in memberships
        if m.get("source_declared_as_of") is not None
        and m["source_declared_as_of"] == declared.get(m["company_id"])
        and m.get("membership_type") == "PRIMARY_CLASSIFICATION"
    ]
    for m in memberships:
        n = node.get(m["industry_node_id"])
        seen = set()
        while n is not None and n["node_level"] != 1:
            ident = n["industry_node_id"]
            if ident in seen:
                raise native.InputError("R2_STATIC_HIERARCHY_CYCLE")
            seen.add(ident)
            parent = node.get(n["parent_node_id"])
            if parent is not None and parent["industry_scheme_id"] != n["industry_scheme_id"]:
                raise native.InputError("R2_STATIC_CROSS_SCHEME_PARENT")
            n = parent
        if n is None:
            bad_companies.add(m["company_id"])
        else:
            by_company[m["company_id"]].add(n["industry_node_id"])
    by_security: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for listing in listings:
        if listing["effective_from"].astimezone(native.TZ).date() <= business_end:
            by_security[listing["security_id"]].append(listing)
    mic = {v["venue_id"]: v["mic_code"] for v in venues}
    selected = []
    unavailable = []
    companies: dict[str, set[str]] = defaultdict(set)
    for security in securities:
        companies[security["security_id"]].add(security["company_id"])
    for sid, company_ids in sorted(companies.items()):
        if len(company_ids) != 1:
            unavailable.append({"security_id": sid, "reason": "STATIC_COMPANY_AMBIGUOUS"})
            continue
        company = next(iter(company_ids))
        ls = by_security.get(sid, [])
        ids = by_company.get(company, set())
        if not ls or len(ids) != 1 or company in bad_companies:
            unavailable.append(
                {"security_id": sid, "reason": "STATIC_SNAPSHOT_MEMBERSHIP_UNAVAILABLE"}
            )
            continue
        latest = max(listing["effective_from"] for listing in ls)
        active = [listing for listing in ls if listing["effective_from"] == latest]
        identities = {(listing["listing_code"], mic.get(listing["venue_id"])) for listing in active}
        if len(identities) != 1:
            unavailable.append({"security_id": sid, "reason": "STATIC_LISTING_IDENTITY_AMBIGUOUS"})
            continue
        code, venue = next(iter(identities))
        if venue not in {"XSHE", "XSHG"}:
            continue
        selected.append(
            {
                "security_id": sid,
                "stock_code": code,
                "exchange": {"XSHE": "SZSE", "XSHG": "SSE"}[venue],
                "level1_industry_id": next(iter(ids)),
            }
        )
    return sorted(selected, key=lambda x: x["security_id"]), sorted(
        unavailable, key=lambda x: x["security_id"]
    )


def validate_relations(c: Any, *, cutoff: datetime, expected_rows: int) -> dict[str, Any]:
    """Full key/time census, bounded in DuckDB; never print market values."""
    for rel in ("j", "s"):
        day = "trade_date" if rel == "j" else "market_date"
        native._zero(
            c,
            f"SELECT count(*) FROM (SELECT listing_id,{day} FROM {rel} "
            "GROUP BY ALL HAVING count(*)<>1)",
            "P10_SUPPORT_DUPLICATE_KEY",
        )
    native._zero(
        c,
        """SELECT count(*) FROM j FULL JOIN s ON j.listing_id=s.listing_id
        AND j.trade_date=s.market_date WHERE j.listing_id IS NULL OR s.listing_id IS NULL
        OR j.security_id IS DISTINCT FROM s.security_id""",
        "P10_SUPPORT_EXACT_CENSUS",
    )
    bad = c.execute(
        """SELECT count(*) FROM s WHERE qualified IS NULL OR entry IS NULL
        OR qualified_at IS NULL OR qualified_at > ?
        OR listing_sessions_lower_bound IS NULL OR listing_sessions_lower_bound < 0
        OR evidence_sha256 IS NULL OR NOT regexp_full_match(evidence_sha256,'sha256:[0-9a-f]{64}')
        OR market_date NOT BETWEEN DATE '2024-10-09' AND DATE '2026-09-18'
        OR (entry AND NOT qualified)
        OR (qualified AND ((upper IS NULL) <> (lower IS NULL)))
        OR (upper IS NOT NULL AND (upper<=0 OR lower<=0 OR upper<lower))""",
        [cutoff],
    ).fetchone()[0]
    rows = c.execute("SELECT count(*) FROM j").fetchone()[0]
    if bad or rows != expected_rows or rows == 0:
        raise native.InputError("P10_SUPPORT_SEMANTIC_CENSUS")
    return {
        "rows": rows,
        "qualified": c.execute("SELECT count(*) FROM s WHERE qualified").fetchone()[0],
        "reasons": dict(c.execute("SELECT reason,count(*) FROM s GROUP BY reason").fetchall()),
    }


def verify(root: Path, *, manifest_sha256: str, ready: bool = True) -> dict[str, Any]:
    root = native.physical(root)
    if native._sha(root / "manifest.json") != manifest_sha256:
        raise native.InputError("P10_SUPPORT_EXTERNAL_IDENTITY")
    manifest = native._json(root / "manifest.json")
    plan = native._json(root / "plan.json")
    if (
        manifest.get("schema_version") != VERSION
        or native._tree(root)
        != set(manifest["files"]) | {"manifest.json"} | ({"READY"} if ready else set())
        or (ready and native._json(root / "READY") != {"manifest_sha256": manifest_sha256})
        or plan.get("original_source_replaced") is not False
        or plan.get("historical_pit_granted") is not False
        or plan.get("original_B_accepted") is not False
        or not {"plan.json", "joined.parquet", "execution.parquet", "actions.json",
                "action-exceptions.json", "industry.json"}.issubset(manifest["files"])
    ):
        raise native.InputError("P10_SUPPORT_PURPOSE_OR_FILES")
    for name, entry in manifest["files"].items():
        native._check_member(native._member(root, name), entry)
    info = datetime.fromisoformat(plan["information_cutoff"])
    if not strict._aware(info):
        raise native.InputError("P10_SUPPORT_CUTOFF")
    actions = prep.load_actions(root / "actions.json", info)
    if any(not date(2024, 10, 9) <= a["effective_date"] <= date(2026, 9, 18)
           for a in actions):
        raise native.InputError("P10_SUPPORT_ACTION_SCOPE")
    exceptions = json.loads((root / "action-exceptions.json").read_bytes())
    if not isinstance(exceptions, list) or any(
        not isinstance(e, dict) or not e.get("security_id") or not e.get("reason")
        for e in exceptions
    ):
        raise native.InputError("P10_SUPPORT_EXCEPTION_SCHEMA")
    schedule.verify(root / "calendar", manifest_sha256=plan["calendar_manifest_sha256"])
    load_static_industry_mapping(
        root / "industry.json", expected_version="static-level1-concentration-research-v1"
    )
    import duckdb

    with duckdb.connect(":memory:") as c:
        c.execute("SET memory_limit='2GB';SET threads=2;SET TimeZone='Asia/Shanghai'")
        for rel, name in (("j", "joined.parquet"), ("s", "execution.parquet")):
            c.execute(f"CREATE VIEW {rel} AS SELECT * FROM read_parquet({native._q(root / name)})")
        census = validate_relations(c, cutoff=info, expected_rows=plan["expected_rows"])
    if census != manifest.get("census"):
        raise native.InputError("P10_SUPPORT_RECOMPUTED_CENSUS")
    return {"manifest_sha256": manifest_sha256, "plan": plan, "files": manifest["files"]}


def ports(root: Path, *, manifest_sha256: str) -> Iterator[bridge.RetrospectivePort]:
    """A bounded security-ordered stream; one source join shared by all variants."""
    import duckdb

    checked = verify(root, manifest_sha256=manifest_sha256)
    plan = checked["plan"]
    info = datetime.fromisoformat(plan["information_cutoff"])
    source = plan["source_manifest_sha256"]
    before = {n: native._fingerprint(root / n) for n in checked["files"]}
    mapping = load_static_industry_mapping(
        root / "industry.json", expected_version="static-level1-concentration-research-v1"
    ).by_security_id
    declared = {
        s["calendar_session_id"]: s
        for s in json.loads((root / "calendar/declared-sessions.json").read_bytes())
    }
    actions = {
        (a["security_id"], a["effective_date"])
        for a in prep.load_actions(root / "actions.json", info)
    }
    excluded = {
        e["security_id"] for e in json.loads((root / "action-exceptions.json").read_bytes())
    }
    warmup = tuple(date.fromisoformat(d) for d in plan["trading_dates"] if d < "2025-01-01")
    with duckdb.connect(":memory:") as c:
        c.execute("SET memory_limit='2GB';SET threads=2;SET TimeZone='Asia/Shanghai'")
        for rel, path in [("j", root / "joined.parquet"), ("s", root / "execution.parquet")]:
            c.execute(f"CREATE VIEW {rel} AS SELECT * FROM read_parquet({native._q(str(path))})")
        query = """SELECT j.*,s.listing_sessions_lower_bound AS p10_listing_sessions,
            s.qualified AS p10_qualified,s.entry AS p10_entry,s.upper AS p10_upper,
            s.lower AS p10_lower,s.reason AS p10_reason,s.evidence_sha256 AS p10_evidence,
            s.qualified_at AS p10_qualified_at
            FROM j JOIN s ON j.listing_id=s.listing_id AND j.trade_date=s.market_date
            ORDER BY j.security_id,j.trade_date"""
        for block in c.execute(query).to_arrow_reader(2048):
            for row in block.to_pylist():
                if row["p10_qualified_at"] > info:
                    raise native.InputError("P10_SUPPORT_AFTER_CUTOFF")
                row["r2_source_snapshot_cutoff"] = row["r2_information_cutoff"]
                row["r2_information_cutoff"] = info
                d = declared.get(row["calendar_session_id"])
                if d is not None:
                    if (
                        row["open_at"] is not None
                        or row["close_at"] is not None
                        or d["trade_date"] != str(row["trade_date"])
                        or d["mic"] != row["mic_code"]
                    ):
                        raise native.InputError("P10_DECLARED_SCHEDULE_BINDING")
                    row["p10_original_open_at"] = row["open_at"]
                    row["p10_original_close_at"] = row["close_at"]
                    row["open_at"] = datetime.fromisoformat(d["declared_regular_open_at"])
                    row["close_at"] = datetime.fromisoformat(d["declared_regular_close_at"])
                    row["p10_declared_schedule_sha256"] = plan["calendar_manifest_sha256"]
                sid, day = row["security_id"], row["trade_date"]
                support = strict.DailySupport(
                    source,
                    strict.row_digest(row),
                    row["p10_evidence"],
                    row["listing_id"],
                    day,
                    listing_sessions=row["p10_listing_sessions"],
                    listing_sessions_known_at=info,
                    adjustment_effective=None if sid in excluded else (sid, day) in actions,
                    adjustment_known_at=info,
                    execution_known_at=info,
                    entry_policy_allows=row["p10_entry"] if row["p10_qualified"] else None,
                    is_executable=(row["is_tradable"] is True) if row["p10_qualified"] else None,
                    upper_limit_price=row["p10_upper"],
                    lower_limit_price=row["p10_lower"],
                )
                industry = mapping.get(sid)
                yield bridge.decision_port(
                    row,
                    information_cutoff=info,
                    source_manifest_sha256=source,
                    support=support,
                    static_industry_id=None if industry is None else industry.level1_industry_id,
                    warmup_dates=warmup,
                )
    if before != {n: native._fingerprint(root / n) for n in before}:
        raise native.InputError("P10_SUPPORT_CHANGED_DURING_CONSUMPTION")
