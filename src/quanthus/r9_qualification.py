"""Fixed R9 native-input qualification. No model fitting or reference calls."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.r7_native import verified_members
from quanthus.stage_learning.head2_facts import build_head2_window_facts
from quanthus.stage_learning.head2_revision import encode_reference_facts
from quanthus.structure_v2_inputs import DailyInput, InputPolicy, admit_daily, canonical, digest


def quality_blocks(
    identity: dict[str, Any], table: str, key: dict[str, Any], effective: datetime | None = None
) -> bool:
    """Match required daily defects on entity AND the exact case coordinate."""
    required = {
        "dwd_security_bar",
        "dwd_listing",
        "dwd_security",
        "dwd_trading_calendar",
        "dwd_corporate_action",
        "dwd_tradability_state",
    }
    if table not in required or key.get("interval_size", "1d") != "1d":
        return False
    values = {str(v) for v in key.values()}
    entity_keys = {
        "listing_id": identity["listing_id"],
        "security_id": identity["security_id"],
        "company_id": identity["company_id"],
        "listing_code": identity["listing_code"],
        "security_code": identity["listing_code"],
    }
    if table != "dwd_trading_calendar" and any(
        k in key and str(key[k]) != expected for k, expected in entity_keys.items()
    ):
        return False
    sessions = {s["session_id"] for s in identity["sessions"]}
    days = {s["date"] for s in identity["sessions"]}

    def business_date(value: Any) -> str:
        text = str(value)
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("R9_QUALITY_TIMEZONE_UNPROVEN")
        return instant.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()

    explicit_dates = [
        business_date(v)
        for k, v in key.items()
        if k in {"trade_date", "market_date", "date", "observation_start", "effective_date"}
    ]
    relevant_day = (
        any(v in days for v in explicit_dates)
        if explicit_dates
        else (
            effective.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() in days
            if effective is not None
            else True
        )
    )
    session_match = bool(sessions & values)
    if table == "dwd_trading_calendar":
        if "venue_id" in key and str(key["venue_id"]) != identity["venue_id"]:
            return False
        return session_match or (
            relevant_day
            and (str(key.get("venue_id", "")) == identity["venue_id"] or bool(explicit_dates))
        )
    entity = bool(
        {
            identity["listing_id"],
            identity["security_id"],
            identity["listing_code"],
            identity["company_id"],
        }
        & values
    )
    return entity and (relevant_day or session_match)


def check_roles(value: dict[str, Any]) -> None:
    rows = value["rows"]
    if len(rows) != 64 or Counter(r["role"] for r in rows) != {
        "train": 24,
        "guard": 8,
        "update": 8,
        "evaluation": 24,
    }:
        raise ValueError("R9_ROLE_DENOMINATOR")
    for key in ("company_id", "security_id", "listing_id", "sample_id", "episode_id"):
        if len({r[key] for r in rows}) != 64:
            raise ValueError("R9_ROLE_IDENTITY_OVERLAP")
    for row in rows:
        sessions = row["sessions"]
        days = [s["date"] for s in sessions]
        if (
            len(days) != 40
            or len(set(days)) != 40
            or days != sorted(days)
            or days[-1] != row["anchor"]
            or len({s["session_id"] for s in sessions}) != 40
        ):
            raise ValueError("R9_FIXED40")
    anchors = {
        role: [r["anchor"] for r in rows if r["role"] == role]
        for role in ("train", "guard", "update", "evaluation")
    }
    if (
        min(anchors["guard"]) <= max(anchors["train"])
        or min(anchors["update"]) <= max(anchors["train"])
        or max(anchors["guard"]) > max(anchors["update"])
        or min(anchors["evaluation"]) <= max(anchors["update"])
    ):
        raise ValueError("R9_ROLE_TIME_ORDER")


def case_facts(
    identity: dict[str, Any],
    native: list[dict[str, Any]],
    *,
    cutoff: datetime,
    exchange: str,
    request_digest: str,
    calendar_digest: str,
    action_digest: str,
    block_reasons: list[str],
) -> dict[str, Any]:
    """Keep unavailable subjects; never substitute a different day or security."""
    wanted = identity["sessions"]
    if (
        len(native) != 40
        or [str(r["market_date"]) for r in native] != [s["date"] for s in wanted]
        or [r["calendar_session_id"] for r in native] != [s["session_id"] for s in wanted]
    ):
        raise ValueError("R9_NATIVE_SESSION_BINDING")
    policy = InputPolicy(request_digest, (identity["listing_code"],), calendar_digest)
    admissions = []
    for index, row in enumerate(native):
        reason = block_reasons[0] if block_reasons else None
        if not row["bar_ok"]:
            reason = reason or "native_bar_missing_or_invalid"
        if not row["state_ok"]:
            reason = reason or "native_state_missing_or_untradable"
        daily = DailyInput(
            security_code=identity["listing_code"],
            market_date=row["market_date"],
            session_index=index,
            cutoff=cutoff,
            security_id=identity["security_id"],
            listing_id=identity["listing_id"],
            exchange=exchange,
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            source_record_id=row["source_record_id"] or "",
            source_revision=row["source_revision"] or "",
            source_artifact_sha256=row["source_artifact_sha256"] or "",
            dwd_known_from=str(row["knowledge_from"]),
            coordinate_epoch="native-action-free-fixed40",
            finality="final",
            quality_reason=reason,
            calendar_member_digest=calendar_digest,
            action_member_digest=action_digest,
        )
        admissions.append(admit_daily(daily, policy))
    facts = build_head2_window_facts(admissions, anchor=date.fromisoformat(identity["anchor"]))
    primary, path = facts.reference_facts()
    encoded = (
        encode_reference_facts(
            primary, path, anchor=identity["anchor"], source_digest=facts.source_closure_digest
        )
        if facts.available
        else None
    )
    features = encoded.values if encoded else None
    packet: dict[str, Any] = {"case_id": identity["sample_id"], "available": features is not None}
    if features is not None:
        first_close = native[0]["close"]
        packet["HLC40"] = [
            [
                str((Decimal(v) / first_close * 100).quantize(Decimal("0.0001")))
                for v in (r["high"], r["low"], r["close"])
            ]
            for r in native
        ]
    return {
        "identity": identity,
        "available": features is not None,
        "reason": encoded.reason if encoded else facts.reason,
        "features": list(features) if features is not None else None,
        "facts_digest": facts.digest,
        "packet": packet,
        "admissions": [a.to_dict() for a in admissions],
        "block_reasons": block_reasons,
    }


def qualify(
    project: Path, source: Path, identities: Path, request: Path, output: Path
) -> dict[str, Any]:
    import duckdb
    import pyarrow as pa  # type: ignore[import-untyped]

    if output.exists():
        raise ValueError("R9_FRESH_OUTPUT_REQUIRED")
    q = json.loads(request.read_bytes())
    raw_identity = identities.read_bytes()
    if hashlib.sha256(raw_identity).hexdigest() != q["fixed_role_identity"]["sha256"]:
        raise ValueError("R9_IDENTITY_HASH")
    subjects = json.loads(raw_identity)
    check_roles(subjects)
    # Reuse only the exact native member verifier, not R7's three-stock use admission.
    base = json.loads((project / "config/research/research-r7/request.v2.json").read_bytes())
    if q["source"]["revision"] != base["authority"]["revision"]:
        raise ValueError("R9_SOURCE_AUTHORITY")
    members = verified_members(source, project, base)
    cutoff = datetime.fromisoformat(q["source"]["knowledge_cutoff"])
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    db.execute("SET threads=2")
    db.execute("SET memory_limit='1GB'")
    db.execute("SET autoinstall_known_extensions=false")
    db.execute("SET autoload_known_extensions=false")
    tables = (
        "dwd_security_bar",
        "dwd_tradability_state",
        "dwd_trading_calendar",
        "dwd_corporate_action",
        "dwd_quality_exception",
        "dwd_trading_venue",
    )
    for table in tables:
        paths = [
            str(source / "DWD" / m["path"])
            for m in members
            if m["path"].startswith("payload/" + table + "/")
        ]
        db.read_parquet(paths, hive_partitioning=True).create_view("raw_" + table)
        db.execute(
            f"CREATE VIEW {table} AS SELECT * FROM raw_{table} "
            f"WHERE knowledge_from<=TIMESTAMPTZ '{cutoff.isoformat()}' AND knowledge_to IS NULL"
        )
    selected = [
        {
            "sample_id": r["sample_id"],
            "listing_id": r["listing_id"],
            "venue_id": r["venue_id"],
            "market_date": date.fromisoformat(s["date"]),
            "calendar_session_id": s["session_id"],
        }
        for r in subjects["rows"]
        for s in r["sessions"]
    ]
    db.register("selected", pa.Table.from_pylist(selected))
    query = """SELECT s.*,c.open_at,c.close_at,b.high,b.low,b.close,b.volume,
      b.source_record_id,b.source_revision,b.source_artifact_sha256,b.knowledge_from,
      (b.currency='CNY' AND b.price_basis='UNADJUSTED' AND b.observation_start=c.open_at
       AND b.observation_end=c.close_at AND b.high>=b.close AND b.close>=b.low
       AND b.low>0 AND isfinite(b.high) AND isfinite(b.low) AND isfinite(b.close)) IS TRUE bar_ok,
      (t.is_tradable AND t.observation_start=c.open_at
       AND t.observation_end=c.close_at) IS TRUE state_ok
      FROM selected s LEFT JOIN dwd_trading_calendar c ON c.venue_id=s.venue_id
        AND c.trade_date=s.market_date AND c.calendar_session_id=s.calendar_session_id AND c.is_open
      LEFT JOIN dwd_security_bar b ON b.listing_id=s.listing_id
        AND b.calendar_session_id=s.calendar_session_id
        AND b.interval_size='1d' AND b.price_basis='UNADJUSTED'
      LEFT JOIN dwd_tradability_state t ON t.listing_id=s.listing_id
        AND t.calendar_session_id=s.calendar_session_id ORDER BY s.sample_id,s.market_date"""
    cursor = db.execute(query)
    names = [x[0] for x in cursor.description]
    all_rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    if len(all_rows) != 2560 or len({(r["sample_id"], r["market_date"]) for r in all_rows}) != 2560:
        raise ValueError("R9_NATIVE_DUPLICATE_OR_DENOMINATOR")
    venue_rows = db.execute("SELECT venue_id,mic_code FROM dwd_trading_venue").fetchall()
    venues = {a: {"XSHG": "SSE", "XSHE": "SZSE"}.get(b, "") for a, b in venue_rows}
    quality = db.execute(
        "SELECT affected_table,affected_business_key_json,"
        "quality_exception_id,affected_effective_at "
        "FROM dwd_quality_exception WHERE resolution_state NOT IN ('RESOLVED','CLOSED')"
    ).fetchall()
    calendar_digest = digest(
        [m for m in members if m["path"].startswith("payload/dwd_trading_calendar/")]
    )
    action_digest = digest([m for m in members if "/dwd_corporate_action" in m["path"]])
    result = []
    for identity in subjects["rows"]:
        first, last = identity["sessions"][0]["date"], identity["anchor"]
        actions = db.execute(
            "SELECT corporate_action_id FROM dwd_corporate_action WHERE security_id=? "
            "AND action_status='IMPLEMENTED' "
            "AND (ex_date IS NULL OR ex_date BETWEEN ?::DATE AND ?::DATE)",
            [identity["security_id"], first, last],
        ).fetchall()
        reasons = ["native_action_or_unknown_date"] if actions else []
        for table, key_json, exception, effective in quality:
            key = json.loads(key_json)
            if quality_blocks(identity, table, key, effective):
                reasons.append("unresolved_required_input:" + exception)
        native = [r for r in all_rows if r["sample_id"] == identity["sample_id"]]
        result.append(
            case_facts(
                identity,
                native,
                cutoff=cutoff,
                exchange=venues[identity["venue_id"]],
                request_digest=digest(q),
                calendar_digest=calendar_digest,
                action_digest=action_digest,
                block_reasons=sorted(set(reasons)),
            )
        )
    verified_members(source, project, base)
    db.close()
    output.mkdir(parents=True)
    (output / "qualified-cases.private.json").write_bytes(canonical({"rows": result}) + b"\n")
    (output / "reference-packets.private.json").write_bytes(
        canonical({"rows": [r["packet"] for r in result]}) + b"\n"
    )
    summary = {
        "subjects": 64,
        "admitted": sum(r["available"] for r in result),
        "role_admission": {
            role: dict(
                Counter(
                    "available" if r["available"] else r["reason"]
                    for r in result
                    if r["identity"]["role"] == role
                )
            )
            for role in ("train", "guard", "update", "evaluation")
        },
        "fixed_identity_sha256": hashlib.sha256(raw_identity).hexdigest(),
        "source_members": len(members),
        "fit_runs": 0,
        "update_runs": 0,
        "reference_calls": 0,
        "qualified_cases_sha256": hashlib.sha256(
            (output / "qualified-cases.private.json").read_bytes()
        ).hexdigest(),
        "reference_packets_sha256": hashlib.sha256(
            (output / "reference-packets.private.json").read_bytes()
        ).hexdigest(),
    }
    (output / "qualification.safe.json").write_bytes(canonical(summary) + b"\n")
    return summary
