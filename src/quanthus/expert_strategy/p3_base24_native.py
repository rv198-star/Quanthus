"""Bounded, input-only P3 consumption of a sealed Base 2.4 native closure.

Stages: pinned metadata -> once-only native materialization -> relation census ->
immutable publication. All consumers share that materialization. Historical
views select knowledge versions at the requested instant, never the later
seven-table governance snapshot. This format is deliberately not a legacy
replay bundle or B/D admission; it cannot invoke collection or a strategy.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value

SOURCE_SCHEMA = "quanthus.base24-consumer-source-projection.v1"
SCHEMA = "quanthus.p3-base24-native-input.v1"
REQUEST = "sha256:9de79141514aa1941d276da854d734786bb2b5bb83310a1d3c7792d60c952bde"
TZ = ZoneInfo("Asia/Shanghai")
SCOPE = {
    "decision_start": "2025-01-01",
    "decision_end": "2026-09-17",
    "execution_end": "2026-09-18",
    "warmup_sessions": 60,
}
KEYS = {
    "dwd_security": ("security_id", "effective_from"),
    "dwd_listing": ("listing_id", "effective_from"),
    "dwd_trading_venue": ("venue_id", "effective_from"),
    "dwd_trading_calendar": ("venue_id", "trade_date", "session_type"),
    "dwd_security_bar": ("listing_id", "observation_start", "interval_size", "price_basis"),
    "dwd_tradability_state": ("listing_id", "calendar_session_id", "observation_start"),
    "dwd_price_limit_regime": ("price_limit_regime_id", "effective_from"),
    "dwd_listing_status_interval": ("listing_id", "status_type", "effective_from"),
    "dwd_security_identifier": (
        "identifier_namespace",
        "identifier_value",
        "effective_from",
    ),
    "dwd_corporate_action": ("corporate_action_id", "effective_at"),
    "dwd_corporate_action_term": (
        "corporate_action_id",
        "corporate_action_term_id",
        "effective_at",
    ),
    "dwd_adjustment_factor": ("adjustment_factor_id", "effective_from"),
    "dwd_quality_exception": (
        "affected_table",
        "affected_business_key_json",
        "rule_id",
        "affected_effective_at",
    ),
    "dwd_company_industry_membership": ("company_id", "industry_node_id", "effective_from"),
    "dwd_industry_node": ("industry_node_id", "effective_from"),
    "dwd_industry_scheme": ("industry_scheme_id", "scheme_version", "effective_from"),
}
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
INTERVAL_TABLES = frozenset(t for t, keys in KEYS.items() if "effective_from" in keys)
FIELDS = {
    "dwd_security": ("company_id", "instrument_type"),
    "dwd_listing": ("security_id", "venue_id", "listing_code"),
    "dwd_trading_venue": ("mic_code",),
    "dwd_trading_calendar": (
        "calendar_session_id",
        "venue_id",
        "trade_date",
        "is_open",
        "open_at",
        "close_at",
    ),
    "dwd_security_bar": (
        "calendar_session_id",
        "interval_size",
        "price_basis",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    ),
    "dwd_tradability_state": ("is_tradable", "can_buy", "can_sell"),
    "dwd_price_limit_regime": ("venue_id", "listing_id"),
    "dwd_listing_status_interval": ("listing_id",),
    "dwd_security_identifier": ("security_id", "venue_id"),
    "dwd_corporate_action": ("security_id",),
    "dwd_corporate_action_term": ("corporate_action_id",),
    "dwd_adjustment_factor": ("security_id",),
    "dwd_quality_exception": (),
    "dwd_company_industry_membership": (),
    "dwd_industry_node": ("industry_scheme_id", "parent_node_id"),
    "dwd_industry_scheme": (),
}
REFERENCES = (
    ("dwd_listing", "security_id", "dwd_security", "security_id", False),
    ("dwd_listing", "venue_id", "dwd_trading_venue", "venue_id", False),
    ("dwd_trading_calendar", "venue_id", "dwd_trading_venue", "venue_id", False),
    ("dwd_price_limit_regime", "venue_id", "dwd_trading_venue", "venue_id", False),
    ("dwd_price_limit_regime", "listing_id", "dwd_listing", "listing_id", True),
    ("dwd_listing_status_interval", "listing_id", "dwd_listing", "listing_id", False),
    ("dwd_security_identifier", "security_id", "dwd_security", "security_id", False),
    ("dwd_security_identifier", "venue_id", "dwd_trading_venue", "venue_id", True),
    ("dwd_corporate_action", "security_id", "dwd_security", "security_id", False),
    (
        "dwd_corporate_action_term",
        "corporate_action_id",
        "dwd_corporate_action",
        "corporate_action_id",
        False,
    ),
    ("dwd_adjustment_factor", "security_id", "dwd_security", "security_id", False),
    (
        "dwd_company_industry_membership",
        "industry_node_id",
        "dwd_industry_node",
        "industry_node_id",
        False,
    ),
    ("dwd_industry_node", "industry_scheme_id", "dwd_industry_scheme", "industry_scheme_id", False),
    ("dwd_industry_node", "parent_node_id", "dwd_industry_node", "industry_node_id", True),
)
CONTRACT = {
    "schema_version": "quanthus.p3-base24-native-consumption.v1",
    "request_digest": REQUEST,
    "scope": SCOPE,
    "source_schema": SOURCE_SCHEMA,
    "native_keys": {t: list(k) for t, k in KEYS.items()},
    "knowledge_selection": ("original_half_open_intervals; unknown_excluded; ambiguity_rejected"),
    "materialization": (
        "one_job_local_native_closure; exact_values_and_versions; no_global_snapshot"
    ),
    "warmup": (
        "exact_60_authoritative_SSE_SZSE_sessions_pinned_in_run_plan; missing_or_late_rejected"
    ),
    "market_scope": "provably_excluded_markets_do_not_block; unknown_scope_fails_closed",
    "daily_state": "latest_completed_observation_start_per_listing_session_at_knowledge_cutoff",
    "disposition": "input_engineering_only; not_legacy_equivalence; not_B_or_D_admission",
    "not_proven": [
        "economic_PIT_qualification",
        "P2_execution_equivalence",
        "industry_admission",
        "candidate_common_mask",
    ],
    "network": "no_acquisition; no_extension_install_or_autoload",
    "runtime": {
        "duckdb_threads": 2,
        "duckdb_memory": "2GB",
        "spill_limit": "1GB",
        "arrow_batch_rows": 8192,
    },
}


class InputError(ValueError):
    """Safe reason code only; never include market values in diagnostics."""


def physical(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InputError("SYMLINK_NOT_ALLOWED")
    return path


def _member(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise InputError("MEMBER_PATH_INVALID")
    p = physical(root / name)
    if any(character in str(p) for character in "*?[]\\"):
        raise InputError("LITERAL_MEMBER_PATH_REQUIRED")
    if p == root or not p.is_relative_to(root) or ".." in Path(name).parts:
        raise InputError("MEMBER_PATH_ESCAPE")
    return p


def _deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise InputError("WALL_BUDGET_EXCEEDED")


def _sha(path: Path, *, deadline: float | None = None) -> str:
    h = hashlib.sha256()
    with physical(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            _deadline(deadline)
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise InputError("METADATA_TOO_LARGE")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in values:
            if k in result:
                raise InputError("DUPLICATE_JSON_KEY")
            result[k] = v
        return result

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=pairs)
    except (OSError, ValueError) as e:
        raise InputError("METADATA_INVALID") from e
    if not isinstance(value, dict):
        raise InputError("METADATA_OBJECT_REQUIRED")
    return value


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _finish(path: Path, value: Any) -> None:
    """Resume final metadata only when its exact value is unchanged."""
    if path.exists():
        if _json(path) != value:
            raise InputError("FINALIZATION_CHECKPOINT_DIFFERS")
    else:
        _write(path, value)


def _entry(path: Path) -> dict[str, Any]:
    return {"sha256": _sha(path), "bytes": path.stat().st_size}


def _check_member(path: Path, entry: Any, *, deadline: float | None = None) -> None:
    if (
        not isinstance(entry, dict)
        or type(entry.get("bytes")) is not int
        or entry["bytes"] < 0
        or not _SHA.fullmatch(str(entry.get("sha256")))
        or not path.is_file()
        or path.stat().st_size != entry["bytes"]
        or _sha(path, deadline=deadline) != entry["sha256"]
    ):
        raise InputError("MEMBER_CHANGED_OR_INVALID")


def _fingerprint(path: Path) -> tuple[int, ...]:
    s = physical(path).stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _source_structure(m: dict[str, Any], raw: dict[str, Any]) -> set[str]:
    if (
        m.get("schema_version") != SOURCE_SCHEMA
        or m.get("consumer_admission") != "NOT_GRANTED"
        or m.get("execution_authorized") is not False
        or m.get("native_versions_retained_locally") is not True
        or m.get("legacy_equivalence") is not False
    ):
        raise InputError("SOURCE_CONTRACT_DIFFERS")
    files = m.get("files")
    required = {f"native/{t}.parquet" for t in KEYS}
    if (
        not isinstance(files, dict)
        or not required <= files.keys()
        or "native-closure.json" not in files
    ):
        raise InputError("SOURCE_NATIVE_CLOSURE_INCOMPLETE")
    if raw.get("files") != {n: files[n] for n in sorted(required)}:
        raise InputError("SOURCE_NATIVE_CLOSURE_DIFFERS")
    for name in required:
        entry = files[name]
        if (
            not isinstance(entry, dict)
            or type(entry.get("bytes")) is not int
            or entry["bytes"] < 0
            or not _SHA.fullmatch(str(entry.get("sha256")))
        ):
            raise InputError("SOURCE_NATIVE_MEMBER_INVALID")
    return required


def source_metadata(root: Path, manifest_sha256: str) -> dict[str, Any]:
    """Metadata only: no Parquet footer/payload reads and no admission inference."""
    root = physical(root)
    if not _SHA.fullmatch(manifest_sha256) or _sha(root / "manifest.json") != manifest_sha256:
        raise InputError("SOURCE_MANIFEST_IDENTITY")
    if physical(root / "READY").read_text().strip() != manifest_sha256:
        raise InputError("SOURCE_READY_IDENTITY")
    m = _json(root / "manifest.json")
    files = m.get("files")
    if not isinstance(files, dict) or "native-closure.json" not in files:
        raise InputError("SOURCE_FILES_REQUIRED")
    closure = _member(root, "native-closure.json")
    _check_member(closure, files["native-closure.json"])
    raw = _json(closure)
    required = _source_structure(m, raw)
    for name in required:
        _member(root, name)
    return {
        "source_manifest_sha256": manifest_sha256,
        "source_manifest": m,
        "native_closure": raw,
        "native_bytes": sum(files[n]["bytes"] for n in required),
        "payload_verified": False,
        "consumer_admission": "NOT_GRANTED",
        "B_accepted": False,
        "D_activated": False,
    }


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _connect(root: Path, temp: Path | None = None) -> Any:
    duckdb = importlib.import_module("duckdb")
    config = {
        "threads": 2,
        "memory_limit": "2GB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "preserve_insertion_order": False,
        "max_temp_directory_size": "1GB",
    }
    # No default spill into the repository/source/current working directory.
    config["temp_directory"] = str(temp) if temp is not None else ""
    c = duckdb.connect(":memory:", config=config)
    try:
        c.execute("LOAD icu")  # Bundled extension only; installation is disabled.
        c.execute("SET TimeZone='Asia/Shanghai'")
        for table in KEYS:
            p = _member(root, f"native/{table}.parquet")
            c.execute(f"CREATE VIEW raw_{table} AS SELECT * FROM read_parquet({_q(p)})")
    except BaseException:
        c.close()
        raise
    return c


class _BoundedConnection:
    """Check every SQL boundary; interrupt only cancels an executing query."""

    def __init__(self, connection: Any, deadline: float | None) -> None:
        self.connection = connection
        self.deadline = deadline

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        _deadline(self.deadline)
        try:
            result = self.connection.execute(*args, **kwargs)
        except Exception:
            _deadline(self.deadline)
            raise
        _deadline(self.deadline)
        return result


def _zero(c: Any, sql: str, reason: str) -> None:
    if c.execute(sql).fetchone()[0]:
        raise InputError(reason)


def _visible_query(table: str, cutoff: datetime) -> str:
    clock = f"TIMESTAMPTZ {_q(cutoff.isoformat())}"
    return (
        f"SELECT DISTINCT * FROM raw_{table} WHERE knowledge_from<={clock} "
        f"AND (knowledge_to IS NULL OR knowledge_to>{clock})"
    )


def _unambiguous(c: Any, query: str, table: str, reason: str) -> None:
    _zero(
        c,
        "SELECT count(*) FROM (SELECT "
        + ",".join(KEYS[table])
        + f" FROM ({query}) GROUP BY ALL HAVING count(*)>1)",
        reason + ":" + table,
    )


def _warmup_binding(value: Any) -> list[dict[str, str]]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "quanthus.p3-warmup-calendar.v1"
        or not isinstance(value.get("authority_reference"), str)
        or not value["authority_reference"].strip()
        or not _SHA.fullmatch(str(value.get("authority_evidence_sha256")))
        or not isinstance(value.get("sessions"), list)
        or len(value["sessions"]) != SCOPE["warmup_sessions"]
    ):
        raise InputError("WARMUP_CALENDAR_AUTHORITY_REQUIRED")
    sessions: list[dict[str, str]] = value["sessions"]
    dates = []
    for session in sessions:
        if not isinstance(session, dict) or set(session) != {"trade_date", "XSHG", "XSHE"}:
            raise InputError("WARMUP_CALENDAR_BINDING_INVALID")
        try:
            day = date.fromisoformat(session["trade_date"])
        except (ValueError, TypeError):
            raise InputError("WARMUP_CALENDAR_BINDING_INVALID") from None
        if (
            day.isoformat() != session["trade_date"]
            or day >= date(2025, 1, 1)
            or any(
                not isinstance(session[m], str) or not session[m].strip() for m in ("XSHG", "XSHE")
            )
            or session["XSHG"] == session["XSHE"]
        ):
            raise InputError("WARMUP_CALENDAR_BINDING_INVALID")
        dates.append(day)
    if dates != sorted(set(dates)) or any(
        len({s[m] for s in sessions}) != 60 for m in ("XSHG", "XSHE")
    ):
        raise InputError("WARMUP_CALENDAR_BINDING_INVALID")
    return sessions


def _warmup(c: Any, binding: dict[str, Any]) -> list[str]:
    sessions = _warmup_binding(binding)
    dates = [s["trade_date"] for s in sessions]
    cutoff = datetime(2025, 1, 1, tzinfo=TZ)
    calendar = _visible_query("dwd_trading_calendar", cutoff)
    venues = _visible_query("dwd_trading_venue", cutoff)
    # The caller freezes authoritative membership before any market-row access.
    # Missing/late metadata must never cause an earlier session to be substituted.
    selected = (
        f"SELECT * FROM ({calendar}) WHERE trade_date>=DATE {_q(dates[0])} "
        "AND trade_date<DATE '2025-01-01' "
        f"AND venue_id IN (SELECT venue_id FROM ({venues}) WHERE mic_code IN ('XSHE','XSHG'))"
    )
    for table, query in (
        (
            "dwd_trading_calendar",
            f"SELECT * FROM ({calendar}) WHERE calendar_session_id IN "
            f"(SELECT calendar_session_id FROM ({selected}))",
        ),
        (
            "dwd_trading_venue",
            f"SELECT * FROM ({venues}) v WHERE EXISTS (SELECT 1 FROM ({selected}) c "
            "WHERE c.venue_id=v.venue_id AND CAST(v.effective_from AS DATE)<=c.trade_date "
            "AND (v.effective_to IS NULL OR CAST(v.effective_to AS DATE)>c.trade_date))",
        ),
    ):
        _unambiguous(c, query, table, "AMBIGUOUS_WARMUP_VERSION")
    expected = ",".join(
        f"(DATE {_q(s['trade_date'])},{_q(m)},{_q(s[m])})"
        for s in sessions
        for m in ("XSHG", "XSHE")
    )
    _zero(
        c,
        f"""WITH expected(day,mic,session_id) AS (VALUES {expected})
        SELECT count(*) FROM expected e WHERE NOT EXISTS (
            SELECT 1 FROM ({calendar}) c JOIN ({venues}) v ON c.venue_id=v.venue_id
            WHERE c.trade_date=e.day AND c.calendar_session_id=e.session_id
            AND v.mic_code=e.mic AND c.is_open
            AND CAST(v.effective_from AS DATE)<=e.day
            AND (v.effective_to IS NULL OR CAST(v.effective_to AS DATE)>e.day))""",
        "WARMUP_CALENDAR_NOT_QUALIFIED",
    )
    actual = c.execute(f"""SELECT c.trade_date FROM ({selected}) c
        JOIN ({venues}) v ON c.venue_id=v.venue_id
        WHERE v.mic_code IN ('XSHG','XSHE') AND c.is_open
        AND CAST(v.effective_from AS DATE)<=c.trade_date
        AND (v.effective_to IS NULL OR CAST(v.effective_to AS DATE)>c.trade_date)
        GROUP BY c.trade_date HAVING count(DISTINCT v.mic_code)=2
        ORDER BY c.trade_date""").fetchall()
    if [r[0].isoformat() for r in actual] != dates:
        raise InputError("WARMUP_CALENDAR_MEMBERSHIP_DIFFERS")
    return dates


def _predicate(table: str, warmup: list[str]) -> str:
    start = warmup[0]
    end = "DATE '2026-09-18'"
    if table == "dwd_trading_calendar":
        field = "trade_date"
    elif table in {"dwd_security_bar", "dwd_tradability_state"}:
        field = "observation_start"
    elif table == "dwd_quality_exception":
        field = "affected_effective_at"
    elif "effective_from" in KEYS[table]:
        return (
            f"(effective_from IS NULL OR (CAST(effective_from AS DATE)<={end} AND "
            f"(effective_to IS NULL OR effective_to > TIMESTAMPTZ '{start} 00:00:00+08')))"
        )
    else:
        field = "effective_at"
    result = f"({field} IS NULL OR CAST({field} AS DATE) BETWEEN DATE '{start}' AND {end})"
    if table in {"dwd_security_bar", "dwd_tradability_state"}:
        dates = ",".join(f"DATE {_q(day)}" for day in warmup)
        result += (
            f" AND ({field} IS NULL OR CAST({field} AS DATE)>=DATE '2025-01-01' "
            f"OR CAST({field} AS DATE) IN ({dates}))"
        )
    return result


def _schema(c: Any) -> dict[str, dict[str, str]]:
    schemas = {}
    for table, keys in KEYS.items():
        columns = {r[0]: r[1] for r in c.execute(f"DESCRIBE raw_{table}").fetchall()}
        clocks = {"knowledge_from", "knowledge_to"}
        if table in INTERVAL_TABLES:
            clocks |= {"effective_from", "effective_to"}
        elif table == "dwd_quality_exception":
            clocks.add("affected_effective_at")
        elif table in {"dwd_trading_calendar", "dwd_security_bar", "dwd_tradability_state"}:
            clocks |= {"observation_start", "observation_end"}
        else:
            clocks.add("effective_at")
        required = (
            set(keys)
            | clocks
            | set(FIELDS[table])
            | {"source_record_id", "source_revision", "source_artifact_sha256"}
        )
        if not required <= columns.keys():
            raise InputError("NATIVE_SCHEMA_REQUIRED_FIELDS:" + table)
        for field in required - clocks:
            if table == "dwd_security_bar" and field in {
                "open",
                "high",
                "low",
                "close",
                "volume",
                "turnover",
            }:
                expected = "DECIMAL("
            elif field in {"is_open", "is_tradable", "can_buy", "can_sell"}:
                expected = "BOOLEAN"
            elif table == "dwd_trading_calendar" and field == "trade_date":
                expected = "DATE"
            elif table == "dwd_trading_calendar" and field in {"open_at", "close_at"}:
                expected = "TIMESTAMP WITH TIME ZONE"
            else:
                expected = "VARCHAR"
            if (expected == "DECIMAL(" and not columns[field].startswith(expected)) or (
                expected != "DECIMAL(" and columns[field] != expected
            ):
                raise InputError("NATIVE_CONSUMER_FIELD_TYPE:" + table + ":" + field)
        if any(columns[field] != "TIMESTAMP WITH TIME ZONE" for field in clocks):
            raise InputError("AWARE_NATIVE_CLOCK_REQUIRED:" + table)
        schemas[table] = columns
    return schemas


def _semantics(c: Any, *, warmup: list[str] | None = None) -> dict[str, Any]:
    summary = {}
    schemas = _schema(c)
    _zero(
        c,
        "SELECT count(*) FROM raw_dwd_trading_calendar WHERE trade_date IS NULL",
        "CALENDAR_TRADE_DATE_UNKNOWN",
    )
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        _zero(
            c,
            f"SELECT count(*) FROM raw_{table} WHERE observation_start IS NULL",
            "CALENDAR_DATE:" + table,
        )
    for table, keys in KEYS.items():
        key = ",".join(keys)
        nulls = " OR ".join(f"{k} IS NULL" for k in keys)
        _zero(c, f"SELECT count(*) FROM raw_{table} WHERE {nulls}", "NATIVE_KEY_NULL:" + table)
        _zero(
            c,
            f"SELECT count(*) FROM raw_{table} WHERE knowledge_to<=knowledge_from",
            "KNOWLEDGE_INTERVAL_INVALID:" + table,
        )
        _zero(
            c,
            f"SELECT count(*) FROM (SELECT {key},knowledge_from FROM "
            f"(SELECT DISTINCT * FROM raw_{table}) WHERE knowledge_from IS NOT NULL "
            f"GROUP BY ALL HAVING count(*)>1)",
            "CONFLICTING_NATIVE_VERSION:" + table,
        )
        n, unknown = c.execute(
            f"SELECT count(*), count(*) FILTER (WHERE knowledge_from IS NULL) FROM raw_{table}"
        ).fetchone()
        summary[table] = {
            "rows": n,
            "unknown_knowledge": unknown,
            "schema_digest": digest_value(schemas[table]),
        }
        _zero(
            c,
            f"SELECT count(*) FROM raw_{table} WHERE source_record_id IS NULL OR "
            "source_record_id='' OR source_revision IS NULL OR source_revision='' "
            "OR source_artifact_sha256 IS NULL OR NOT regexp_full_match("
            "source_artifact_sha256,'sha256:[0-9a-f]{64}')",
            "LINEAGE_INVALID:" + table,
        )
        if warmup is not None:
            _zero(
                c,
                f"SELECT count(*) FROM raw_{table} WHERE NOT ({_predicate(table, warmup)})",
                "BUSINESS_SCOPE_DIFFERS:" + table,
            )
        if table in INTERVAL_TABLES:
            _zero(
                c,
                f"SELECT count(*) FROM raw_{table} WHERE effective_to<=effective_from",
                "EFFECTIVE_INTERVAL_INVALID:" + table,
            )
    _zero(
        c,
        "SELECT count(*) FROM raw_dwd_security_bar WHERE interval_size IS DISTINCT FROM '1d' "
        "OR price_basis IS DISTINCT FROM 'UNADJUSTED'",
        "CANONICAL_DAILY_BASIS_REQUIRED",
    )
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        _zero(
            c,
            f"SELECT count(*) FROM raw_{table} WHERE observation_end<observation_start",
            "OBSERVATION_INTERVAL_INVALID:" + table,
        )
        _zero(
            c,
            f"SELECT count(*) FROM raw_{table} b WHERE NOT EXISTS "
            "(SELECT 1 FROM raw_dwd_trading_calendar c WHERE "
            "c.calendar_session_id=b.calendar_session_id "
            "AND c.trade_date=CAST(b.observation_start AS DATE))",
            "CALENDAR_DATE:" + table,
        )
        _zero(
            c,
            f"SELECT count(*) FROM raw_{table} b WHERE NOT EXISTS "
            "(SELECT 1 FROM raw_dwd_trading_calendar c JOIN raw_dwd_listing l "
            "ON l.venue_id=c.venue_id WHERE c.calendar_session_id=b.calendar_session_id "
            "AND l.listing_id=b.listing_id AND l.effective_from<=b.observation_start "
            "AND (l.effective_to IS NULL OR l.effective_to>b.observation_start))",
            "CALENDAR_LISTING_VENUE:" + table,
        )
    for child, field, parent, target, nullable in REFERENCES:
        guard = f"x.{field} IS NOT NULL AND " if nullable else ""
        _zero(
            c,
            f"SELECT count(*) FROM raw_{child} x WHERE {guard}NOT EXISTS "
            f"(SELECT 1 FROM raw_{parent} p WHERE p.{target}=x.{field})",
            f"NATIVE_REFERENCE_MISSING:{child}:{field}",
        )
    summary["dwd_security_bar"]["value_classes"] = dict(
        zip(
            ("zero_volume", "null_volume", "null_turnover", "late_knowledge"),
            c.execute(
                "SELECT count(*) FILTER (WHERE volume=0), count(*) FILTER (WHERE volume IS NULL), "
                "count(*) FILTER (WHERE turnover IS NULL), count(*) FILTER "
                "(WHERE CAST(knowledge_from AS DATE)>CAST(observation_start AS DATE)) "
                "FROM raw_dwd_security_bar"
            ).fetchone(),
            strict=True,
        )
    )
    return summary


def _isolate(source: Path, output: Path, staging: Path) -> None:
    for path in (output, staging):
        physical(path)
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise InputError("SOURCE_OUTPUT_OR_STAGING_OVERLAP")


def _tree(root: Path) -> set[str]:
    result: set[str] = set()

    def onerror(error: OSError) -> None:
        raise InputError("DIRECTORY_SCAN_FAILED") from error

    for parent, dirs, files in os.walk(root, onerror=onerror, followlinks=False):
        for name in dirs + files:
            physical(Path(parent) / name)
        for n in files:
            p = Path(parent) / n
            s = p.stat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                raise InputError("INDEPENDENT_REGULAR_FILES_REQUIRED")
            result.add(p.relative_to(root).as_posix())
    return result


def _required_files() -> set[str]:
    return (
        {f"native/{t}.parquet" for t in KEYS}
        | {f"checkpoints/{t}.json" for t in KEYS}
        | {
            "identity.json",
            "source-metadata.json",
            "stages.json",
            "contract.json",
            "LOCK",
            "original-manifest.json",
            "original-native-closure.json",
            "run-plan.json",
        }
    )


def _input_plan(plan: dict[str, Any], *, source_sha: str, implementation_sha: str) -> None:
    if (
        plan.get("schema_version") != "quanthus.p3-base24-input-run-plan.v1"
        or plan.get("purpose") != "input_qualification_only"
        or plan.get("request_digest") != REQUEST
        or plan.get("scope") != SCOPE
        or plan.get("source_manifest_sha256") != source_sha
        or plan.get("input_read_authorized") is not True
        or plan.get("strategy_execution_authorized") is not False
        or plan.get("contract_digest") != digest_value(CONTRACT)
        or plan.get("implementation_sha256") != implementation_sha
        or not isinstance(plan.get("authority_reference"), str)
        or not plan["authority_reference"].strip()
        or not isinstance(plan.get("new_input_scope_id"), str)
        or not plan["new_input_scope_id"].startswith("P3-BASE24-INPUT-")
    ):
        raise InputError("INPUT_ONLY_RUN_PLAN_NOT_ACTIVATED")
    _warmup_binding(plan.get("warmup_calendar"))


def prepare(
    source: Path,
    output: Path,
    *,
    source_manifest_sha256: str,
    code_revision: str,
    run_plan: dict[str, Any],
    max_output_bytes: int = 4 * 1024**3,
    wall_seconds: int = 3600,
) -> dict[str, Any]:
    """Materialize once; checkpoint completed tables, never grant B/D admission.

    Callers must have separate authority for the exact real input scope. The CLI
    requires an explicit input-only run plan; the metadata command never reads
    payload. Old B attempt counters and legacy recovery functions are untouched.
    """
    source, output = physical(source), physical(output)
    staging = physical(output.parent / ("." + output.name + ".job"))
    _isolate(source, output, staging)  # Must precede even LOCK/identity/mkdir.
    if (
        not re.fullmatch(r"[a-f0-9]{40}", code_revision)
        or type(max_output_bytes) is not int
        or max_output_bytes <= 0
        or type(wall_seconds) is not int
        or not 1 <= wall_seconds <= 86400
    ):
        raise InputError("EXPLICIT_CODE_AND_BUDGET_REQUIRED")
    _input_plan(
        run_plan, source_sha=source_manifest_sha256, implementation_sha=_sha(Path(__file__))
    )
    if (
        run_plan.get("code_revision") != code_revision
        or run_plan.get("max_output_bytes") != max_output_bytes
        or run_plan.get("wall_seconds") != wall_seconds
    ):
        raise InputError("RUN_PLAN_ARGUMENTS_DIFFER")
    meta = source_metadata(source, source_manifest_sha256)
    deadline = time.monotonic() + wall_seconds
    identity = {
        "schema_version": SCHEMA,
        "request_digest": REQUEST,
        "scope": SCOPE,
        "source_manifest_sha256": source_manifest_sha256,
        "code_revision": code_revision,
        "implementation_sha256": _sha(Path(__file__)),
        "serialization_sha256": _sha(Path(canonical_json_bytes.__code__.co_filename)),
        "runtime": {
            "python": platform.python_version(),
            "duckdb": importlib.import_module("duckdb").__version__,
            "pyarrow": importlib.import_module("pyarrow").__version__,
        },
        "contract_digest": digest_value(CONTRACT),
        "run_plan_digest": digest_value(run_plan),
        "max_output_bytes": max_output_bytes,
        "wall_seconds": wall_seconds,
    }
    if output.exists():
        if _json(output / "manifest.json").get("identity") != identity:
            raise InputError("SEALED_IDENTITY_DIFFERS")
        return verify(output, manifest_sha256=_sha(output / "manifest.json"))
    if not output.parent.is_dir():
        raise InputError("EXISTING_OUTPUT_PARENT_REQUIRED")
    if shutil.disk_usage(output.parent).free < max_output_bytes + 2 * 1024**3:
        raise InputError("CAPACITY_BEFORE_WRITE_INSUFFICIENT")
    frozen: dict[str, tuple[int, ...]] = {}
    for table in KEYS:
        name = f"native/{table}.parquet"
        p = _member(source, name)
        before = _fingerprint(p)
        _check_member(p, meta["native_closure"]["files"][name], deadline=deadline)
        if _fingerprint(p) != before:
            raise InputError("SOURCE_MUTATED_DURING_VERIFY")
        frozen[table] = before
    staging.mkdir(mode=0o700, exist_ok=True)
    existing = _tree(staging)  # Before touching LOCK or any receipt.
    allowed = _required_files()
    if "manifest.json" in existing:
        allowed |= {"manifest.json", "READY"}
    if not existing <= allowed:
        raise InputError("STAGING_FILE_SET_DIFFERS")
    lock = physical(staging / "LOCK").open("a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise InputError("JOB_ALREADY_RUNNING") from e
        if output.exists():
            raise InputError("OUTPUT_APPEARED_DURING_LOCK")
        if (staging / "identity.json").exists():
            if _json(staging / "identity.json") != identity:
                raise InputError("CHECKPOINT_IDENTITY_DIFFERS")
        else:
            if _tree(staging) != {"LOCK"}:
                raise InputError("UNOWNED_STAGING_CONTENT")
            _write(staging / "identity.json", identity)
        if (staging / "manifest.json").exists():
            result = _verify(
                staging, manifest_sha256=_sha(staging / "manifest.json"), ready_required=False
            )
            _publish(staging, output)
            return result

        def check_budget() -> None:
            _deadline(deadline)
            if shutil.disk_usage(output.parent).free < 1024**3:
                raise InputError("CAPACITY_SAFETY_RESERVE_REACHED")
            if sum((staging / n).stat().st_size for n in _tree(staging)) > max_output_bytes:
                raise InputError("OUTPUT_BUDGET_EXCEEDED")

        (staging / "native").mkdir(exist_ok=True)
        (staging / "checkpoints").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix=".p3-query-", dir=output.parent)
        try:
            c = _BoundedConnection(_connect(source, Path(temp.name)), deadline)
        except BaseException:
            temp.cleanup()
            raise
        timer = threading.Timer(max(0, deadline - time.monotonic()), c.interrupt)
        timer.daemon = True
        timer.start()
        try:
            _schema(c)
            warmup = _warmup(c, run_plan["warmup_calendar"])
            # Validate the selected window, not unrelated historical/BSE gaps.
            for table in KEYS:
                c.execute(
                    f"CREATE VIEW scope_{table} AS SELECT * FROM raw_{table} "
                    f"WHERE {_predicate(table, warmup)}"
                )
            stages = []
            pa = importlib.import_module("pyarrow")
            pq = importlib.import_module("pyarrow.parquet")
            for table in KEYS:
                check_budget()
                table_started = time.monotonic()
                member = staging / "native" / (table + ".parquet")
                receipt = staging / "checkpoints" / (table + ".json")
                if receipt.exists():
                    saved = _json(receipt)
                    _check_member(member, saved["member"])
                    disposition = "reuse"
                else:
                    if member.exists():
                        raise InputError("INCOMPLETE_MEMBER_REQUIRES_NEW_RUN")
                    cursor = c.execute(f"SELECT * FROM scope_{table}")
                    batches = cursor.to_arrow_reader(8192)
                    with pq.ParquetWriter(member, batches.schema, compression="zstd") as writer:
                        for batch in batches:
                            writer.write_table(pa.Table.from_batches([batch]))
                            check_budget()
                    with member.open("rb") as completed:
                        os.fsync(completed.fileno())
                    saved = {
                        "member": _entry(member),
                        "rows": pq.ParquetFile(member).metadata.num_rows,
                    }
                    _write(receipt, saved)
                    disposition = "build"
                # Source equality is checked once per table before publication.
                _zero(
                    c,
                    f"SELECT count(*) FROM ((SELECT * FROM scope_{table} EXCEPT ALL "
                    f"SELECT * FROM read_parquet({_q(member)})) UNION ALL "
                    f"(SELECT * FROM read_parquet({_q(member)}) EXCEPT ALL "
                    f"SELECT * FROM scope_{table}))",
                    "MATERIALIZATION_CHANGED_VALUES",
                )
                stages.append(
                    {
                        "table": table,
                        "disposition": disposition,
                        "bytes": member.stat().st_size,
                        "rows": saved["rows"],
                        "elapsed_seconds": round(time.monotonic() - table_started, 6),
                    }
                )
                if _fingerprint(_member(source, f"native/{table}.parquet")) != frozen[table]:
                    raise InputError("SOURCE_CHANGED_DURING_MATERIALIZATION")
        finally:
            timer.cancel()
            timer.join()
            c.close()
            temp.cleanup()
        with NativeView(staging, temp_parent=output.parent, deadline=deadline) as view:
            summary = _semantics(view.connection, warmup=warmup)
            if _warmup(view.connection, run_plan["warmup_calendar"]) != warmup:
                raise InputError("COPIED_CALENDAR_DIFFERS")
        check_budget()
        for table in KEYS:
            if _fingerprint(_member(source, f"native/{table}.parquet")) != frozen[table]:
                raise InputError("SOURCE_CHANGED_BEFORE_SEAL")
        if _sha(source / "manifest.json") != source_manifest_sha256:
            raise InputError("SOURCE_MANIFEST_CHANGED_BEFORE_SEAL")
        _finish(staging / "source-metadata.json", meta)
        # Keep the byte-exact original metadata, so offline verification can bind
        # the embedded declaration without re-opening its historical source path.
        for name in ("manifest.json", "native-closure.json"):
            target = staging / ("original-" + name)
            if target.exists():
                if _sha(target) != _sha(source / name):
                    raise InputError("SOURCE_METADATA_CHECKPOINT_DIFFERS")
            else:
                with (source / name).open("rb") as r, target.open("xb") as w:
                    shutil.copyfileobj(r, w)
                    w.flush()
                    os.fsync(w.fileno())
        _finish(staging / "contract.json", CONTRACT)
        _finish(staging / "run-plan.json", run_plan)
        if _sha(staging / "original-manifest.json") != source_manifest_sha256:
            raise InputError("SOURCE_METADATA_CHANGED_DURING_COPY")
        _check_member(
            staging / "original-native-closure.json",
            meta["source_manifest"]["files"]["native-closure.json"],
        )
        if not (staging / "stages.json").exists():
            _write(
                staging / "stages.json", {"stages": stages, "source_materializations_per_job": 1}
            )
        if _tree(staging) != _required_files():
            raise InputError("STAGING_FILE_SET_DIFFERS")
        files = {n: _entry(staging / n) for n in sorted(_tree(staging))}
        manifest = {
            "schema_version": SCHEMA,
            "identity": identity,
            "scope": SCOPE,
            "warmup_dates": warmup,
            "semantic_summary": summary,
            "files": files,
            "consumer_admission": "NOT_GRANTED",
            "B_accepted": False,
            "D_activated": False,
            "execution_authorized": False,
            "value_preservation_checked_tables": sorted(KEYS),
            "built_at": datetime.now(TZ).isoformat(),
        }
        _write(staging / "manifest.json", manifest)
        check_budget()
        _publish(staging, output)
        return {**manifest, "manifest_sha256": _sha(output / "manifest.json")}
    finally:
        lock.close()


def _publish(staging: Path, output: Path) -> None:
    digest = _sha(staging / "manifest.json")
    ready = staging / "READY"
    expected = _required_files() | {"manifest.json"} | ({"READY"} if ready.exists() else set())
    if _tree(staging) != expected:
        raise InputError("STAGING_FILE_SET_DIFFERS")
    if ready.exists() and ready.read_text().strip() != digest:
        raise InputError("READY_IDENTITY_DIFFERS")
    final_bytes = sum((staging / name).stat().st_size for name in expected)
    if not ready.exists():
        final_bytes += len((digest + "\n").encode("utf-8"))
    if final_bytes > _json(staging / "identity.json")["max_output_bytes"]:
        raise InputError("OUTPUT_BUDGET_EXCEEDED")
    for path in (staging / "native", staging / "checkpoints", staging):
        _sync_directory(path)
    if not ready.exists():
        with ready.open("x") as f:
            f.write(digest + "\n")
            f.flush()
            os.fsync(f.fileno())
    if output.exists():
        raise InputError("SEALED_OUTPUT_ALREADY_EXISTS")
    _sync_directory(staging)
    staging.rename(output)  # LOCK stays held through READY and the atomic rename.
    _sync_directory(output.parent)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify(root: Path, *, manifest_sha256: str) -> dict[str, Any]:
    """Cold, local-only: exact files, hashes and independently recomputed relations."""
    return _verify(root, manifest_sha256=manifest_sha256, ready_required=True)


def _verify(root: Path, *, manifest_sha256: str, ready_required: bool) -> dict[str, Any]:
    root = physical(root)
    if not _SHA.fullmatch(manifest_sha256) or _sha(root / "manifest.json") != manifest_sha256:
        raise InputError("MANIFEST_IDENTITY_DIFFERS")
    if (ready_required or (root / "READY").exists()) and physical(
        root / "READY"
    ).read_text().strip() != manifest_sha256:
        raise InputError("READY_IDENTITY_DIFFERS")
    m = _json(root / "manifest.json")
    if (
        m.get("schema_version") != SCHEMA
        or m.get("scope") != SCOPE
        or m.get("consumer_admission") != "NOT_GRANTED"
        or any(m.get(k) is not False for k in ("B_accepted", "D_activated", "execution_authorized"))
        or m.get("value_preservation_checked_tables") != sorted(KEYS)
    ):
        raise InputError("PREPARED_CONTRACT_DIFFERS")
    required = _required_files()
    extra = {"manifest.json"} | ({"READY"} if (root / "READY").exists() else set())
    if set(m.get("files", {})) != required or _tree(root) != required | extra:
        raise InputError("PHYSICAL_FILE_SET_DIFFERS")
    identity = m.get("identity")
    if (
        not isinstance(identity, dict)
        or type(identity.get("wall_seconds")) is not int
        or not 1 <= identity["wall_seconds"] <= 86400
        or type(identity.get("max_output_bytes")) is not int
        or identity["max_output_bytes"] <= 0
    ):
        raise InputError("FROZEN_RESOURCE_BINDING_DIFFERS")
    if (
        sum((root / name).stat().st_size for name in required | extra)
        > identity["max_output_bytes"]
    ):
        raise InputError("OUTPUT_BUDGET_EXCEEDED")
    deadline = time.monotonic() + identity["wall_seconds"]
    fingerprints = {name: _fingerprint(root / name) for name in required | extra}
    for name, entry in m["files"].items():
        _check_member(_member(root, name), entry, deadline=deadline)
    if _json(root / "identity.json") != m["identity"]:
        raise InputError("IDENTITY_DIFFERS")
    if (
        m["identity"].get("scope") != SCOPE
        or m["identity"].get("request_digest") != REQUEST
        or m["identity"].get("schema_version") != SCHEMA
        or m["identity"].get("contract_digest") != digest_value(CONTRACT)
        or not re.fullmatch(r"[a-f0-9]{40}", str(m["identity"].get("code_revision")))
        or not _SHA.fullmatch(str(m["identity"].get("implementation_sha256")))
        or not _SHA.fullmatch(str(m["identity"].get("serialization_sha256")))
        or _json(root / "contract.json") != CONTRACT
    ):
        raise InputError("FROZEN_RESEARCH_BINDING_DIFFERS")
    plan = _json(root / "run-plan.json")
    _input_plan(
        plan,
        source_sha=m["identity"]["source_manifest_sha256"],
        implementation_sha=m["identity"]["implementation_sha256"],
    )
    if digest_value(plan) != m["identity"].get("run_plan_digest") or any(
        plan.get(k) != m["identity"].get(k)
        for k in ("code_revision", "max_output_bytes", "wall_seconds")
    ):
        raise InputError("FROZEN_RUN_PLAN_DIFFERS")
    meta = _json(root / "source-metadata.json")
    if (
        meta.get("source_manifest_sha256") != m["identity"].get("source_manifest_sha256")
        or _sha(root / "original-manifest.json") != meta["source_manifest_sha256"]
        or _json(root / "original-manifest.json") != meta.get("source_manifest")
        or _json(root / "original-native-closure.json") != meta.get("native_closure")
        or meta.get("consumer_admission") != "NOT_GRANTED"
        or any(meta.get(k) is not False for k in ("B_accepted", "D_activated", "payload_verified"))
    ):
        raise InputError("OFFLINE_SOURCE_BINDING_DIFFERS")
    _source_structure(meta["source_manifest"], meta["native_closure"])
    _check_member(
        root / "original-native-closure.json",
        meta["source_manifest"]["files"]["native-closure.json"],
    )
    for table in KEYS:
        saved = _json(root / "checkpoints" / (table + ".json"))
        if saved.get("member") != m["files"][f"native/{table}.parquet"]:
            raise InputError("MEMBER_CHECKPOINT_DIFFERS")
        if saved.get("rows") != m["semantic_summary"][table]["rows"]:
            raise InputError("ROW_CHECKPOINT_DIFFERS")
    with NativeView(root, deadline=deadline) as view:
        warmup = _warmup(view.connection, plan["warmup_calendar"])
        if _semantics(view.connection, warmup=warmup) != m["semantic_summary"]:
            raise InputError("SEMANTIC_CENSUS_DIFFERS")
        if warmup != m["warmup_dates"]:
            raise InputError("WARMUP_CENSUS_DIFFERS")
    if any(_fingerprint(root / n) != fingerprint for n, fingerprint in fingerprints.items()):
        raise InputError("PREPARED_FILES_CHANGED_DURING_VERIFY")
    _deadline(deadline)
    return {**m, "manifest_sha256": manifest_sha256}


class NativeView:
    """One job-local native reader; callers share it across input-only consumers."""

    def __init__(
        self, root: Path, *, temp_parent: Path | None = None, deadline: float | None = None
    ) -> None:
        self.root = physical(root)
        self.temp = tempfile.TemporaryDirectory(prefix="p3-native-read-", dir=temp_parent)
        self.deadline = deadline
        self.timer: threading.Timer | None = None
        self.fingerprints = {
            t: _fingerprint(_member(self.root, f"native/{t}.parquet")) for t in KEYS
        }
        try:
            connection = _connect(self.root, Path(self.temp.name))
            self.connection = _BoundedConnection(connection, deadline)
            try:
                _deadline(deadline)
            except BaseException:
                connection.close()
                raise
        except BaseException:
            self.temp.cleanup()
            raise
        if deadline is not None:
            self.timer = threading.Timer(
                max(0, deadline - time.monotonic()), self.connection.interrupt
            )
            self.timer.daemon = True
            self.timer.start()

    def __enter__(self) -> NativeView:
        return self

    def __exit__(self, *_args: object) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer.join()
        self.connection.close()
        self.temp.cleanup()

    def _unchanged(self) -> None:
        _deadline(self.deadline)
        if any(
            _fingerprint(_member(self.root, f"native/{t}.parquet")) != f
            for t, f in self.fingerprints.items()
        ):
            raise InputError("PREPARED_FILES_CHANGED_DURING_READ")

    def at(self, cutoff: datetime) -> None:
        self._at(cutoff, market_only=False)

    def _at(self, cutoff: datetime, *, market_only: bool) -> None:
        self._unchanged()
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise InputError("AWARE_CUTOFF_REQUIRED")
        day = cutoff.astimezone(TZ).date()
        if not date(2025, 1, 1) <= day <= date(2026, 9, 17):
            raise InputError("FROZEN_DECISION_WINDOW")
        self.connection.execute("BEGIN TRANSACTION")
        try:
            for table in KEYS:
                query = _visible_query(table, cutoff)
                if not market_only:
                    _unambiguous(self.connection, query, table, "AMBIGUOUS_ASOF_VERSION")
                self.connection.execute(f"CREATE OR REPLACE TEMP VIEW {table} AS {query}")
            if market_only:
                self._market_scope(cutoff)
            self._unchanged()
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _market_scope(self, cutoff: datetime) -> None:
        c = self.connection
        active_listing = (
            "l.listing_id=b.listing_id AND l.effective_from<=b.observation_start "
            "AND (l.effective_to IS NULL OR l.effective_to>b.observation_start)"
        )
        # Exclusion needs affirmative evidence at this cutoff. An unavailable
        # listing/venue/security never silently removes an in-scope candidate.
        outside = []
        for table, alias, identity, field, values in (
            ("dwd_trading_venue", "v", "venue_id", "mic_code", "('XSHE','XSHG')"),
            ("dwd_security", "s", "security_id", "instrument_type", "('A_SHARE')"),
        ):
            active = (
                f"{alias}.{identity}=l.{identity} AND {alias}.effective_from<=b.observation_start "
                f"AND ({alias}.effective_to IS NULL OR {alias}.effective_to>b.observation_start)"
            )
            outside.append(
                f"(EXISTS (SELECT 1 FROM {table} {alias} WHERE {active} "
                f"AND {alias}.{field} NOT IN {values}) AND NOT EXISTS "
                f"(SELECT 1 FROM {table} {alias} WHERE {active} "
                f"AND ({alias}.{field} IN {values} OR {alias}.{field} IS NULL)))"
            )
        excluded = (
            f"EXISTS (SELECT 1 FROM dwd_listing l WHERE {active_listing}) AND NOT EXISTS "
            f"(SELECT 1 FROM dwd_listing l WHERE {active_listing} AND NOT ({' OR '.join(outside)}))"
        )
        c.execute(
            "CREATE OR REPLACE TEMP VIEW p3_market_bars AS SELECT * FROM dwd_security_bar b "
            f"WHERE b.observation_end<=TIMESTAMPTZ {_q(cutoff.isoformat())} AND NOT ({excluded})"
        )
        queries = {
            "dwd_security_bar": "SELECT * FROM p3_market_bars",
            "dwd_listing": f"SELECT l.* FROM dwd_listing l WHERE EXISTS "
            f"(SELECT 1 FROM p3_market_bars b WHERE {active_listing})",
            "dwd_trading_calendar": "SELECT c.* FROM dwd_trading_calendar c WHERE EXISTS "
            "(SELECT 1 FROM p3_market_bars b WHERE c.calendar_session_id=b.calendar_session_id)",
            "dwd_tradability_state": "SELECT t.* FROM dwd_tradability_state t WHERE "
            f"t.observation_end<=TIMESTAMPTZ {_q(cutoff.isoformat())} AND EXISTS "
            "(SELECT 1 FROM p3_market_bars b WHERE t.listing_id=b.listing_id "
            "AND t.calendar_session_id=b.calendar_session_id)",
        }
        for table, alias, identity in (
            ("dwd_security", "s", "security_id"),
            ("dwd_trading_venue", "v", "venue_id"),
        ):
            queries[table] = (
                f"SELECT {alias}.* FROM {table} {alias} WHERE EXISTS "
                f"(SELECT 1 FROM p3_market_bars b JOIN dwd_listing l ON {active_listing} "
                f"WHERE {alias}.{identity}=l.{identity} "
                f"AND {alias}.effective_from<=b.observation_start "
                f"AND ({alias}.effective_to IS NULL OR {alias}.effective_to>b.observation_start))"
            )
        for table, query in queries.items():
            _unambiguous(c, query, table, "AMBIGUOUS_ASOF_VERSION")
        c.execute(
            "CREATE OR REPLACE TEMP VIEW p3_market_states AS "
            + queries["dwd_tradability_state"]
            + " QUALIFY row_number() OVER (PARTITION BY listing_id,calendar_session_id "
            "ORDER BY observation_start DESC)=1"
        )

    def market_batches(self, cutoff: datetime, *, batch_size: int = 8192) -> Iterator[Any]:
        """Exact native values at cutoff; no fabricated eligibility or legacy casts."""
        if type(batch_size) is not int or not 1 <= batch_size <= 65536:
            raise InputError("BATCH_SIZE_INVALID")
        self._at(cutoff, market_only=True)
        c = self.connection
        bounded = f"b.observation_end<=TIMESTAMPTZ {_q(cutoff.isoformat())}"
        _zero(
            c,
            f"SELECT count(*) FROM p3_market_bars b WHERE {bounded} AND NOT EXISTS "
            "(SELECT 1 FROM dwd_trading_calendar c "
            "WHERE c.calendar_session_id=b.calendar_session_id "
            "AND c.trade_date=CAST(b.observation_start AS DATE))",
            "ASOF_CALENDAR_UNAVAILABLE",
        )
        _zero(
            c,
            f"SELECT count(*) FROM p3_market_bars b WHERE {bounded} AND NOT EXISTS "
            "(SELECT 1 FROM dwd_listing l JOIN dwd_trading_calendar c ON c.venue_id=l.venue_id "
            "JOIN dwd_security s ON s.security_id=l.security_id "
            "JOIN dwd_trading_venue v ON v.venue_id=l.venue_id "
            "WHERE l.listing_id=b.listing_id AND c.calendar_session_id=b.calendar_session_id "
            "AND l.effective_from<=b.observation_start "
            "AND (l.effective_to IS NULL OR l.effective_to>b.observation_start) "
            "AND s.effective_from<=b.observation_start "
            "AND (s.effective_to IS NULL OR s.effective_to>b.observation_start) "
            "AND v.effective_from<=b.observation_start "
            "AND (v.effective_to IS NULL OR v.effective_to>b.observation_start))",
            "ASOF_IDENTITY_UNAVAILABLE",
        )
        state_bounded = f"t.observation_end<=TIMESTAMPTZ {_q(cutoff.isoformat())}"
        linked_state = (
            f"{state_bounded} AND EXISTS (SELECT 1 FROM p3_market_bars b WHERE {bounded} "
            "AND b.listing_id=t.listing_id AND b.calendar_session_id=t.calendar_session_id)"
        )
        _zero(
            c,
            f"SELECT count(*) FROM dwd_tradability_state t WHERE {linked_state} AND NOT EXISTS "
            "(SELECT 1 FROM dwd_trading_calendar c "
            "WHERE c.calendar_session_id=t.calendar_session_id "
            "AND c.trade_date=CAST(t.observation_start AS DATE))",
            "ASOF_STATE_CALENDAR_DATE",
        )
        _zero(
            c,
            f"SELECT count(*) FROM dwd_tradability_state t WHERE {linked_state} AND NOT EXISTS "
            "(SELECT 1 FROM dwd_trading_calendar c JOIN dwd_listing l ON l.venue_id=c.venue_id "
            "WHERE c.calendar_session_id=t.calendar_session_id "
            "AND c.trade_date=CAST(t.observation_start AS DATE) AND l.listing_id=t.listing_id "
            "AND l.effective_from<=t.observation_start "
            "AND (l.effective_to IS NULL OR l.effective_to>t.observation_start))",
            "ASOF_STATE_LISTING_VENUE",
        )
        query = f"""SELECT b.*, s.security_id, s.company_id, s.instrument_type, l.listing_code,
            l.venue_id, c.trade_date, c.open_at, c.close_at,
            b.knowledge_from AS bar_knowledge_from, t.knowledge_from AS state_knowledge_from,
            l.knowledge_from AS listing_knowledge_from, s.knowledge_from AS security_knowledge_from,
            l.effective_from AS listing_effective_from, s.effective_from AS security_effective_from,
            t.is_tradable,t.can_buy,t.can_sell
            FROM p3_market_bars b
            JOIN dwd_trading_calendar c USING(calendar_session_id)
            JOIN dwd_listing l ON l.listing_id=b.listing_id AND l.venue_id=c.venue_id
                AND l.effective_from<=b.observation_start
                AND (l.effective_to IS NULL OR l.effective_to>b.observation_start)
            JOIN dwd_security s ON s.security_id=l.security_id
                AND s.effective_from<=b.observation_start
                AND (s.effective_to IS NULL OR s.effective_to>b.observation_start)
            JOIN dwd_trading_venue v ON v.venue_id=l.venue_id
                AND v.effective_from<=b.observation_start
                AND (v.effective_to IS NULL OR v.effective_to>b.observation_start)
            LEFT JOIN p3_market_states t ON t.listing_id=b.listing_id
                AND t.calendar_session_id=b.calendar_session_id
                AND {state_bounded}
            WHERE v.mic_code IN ('XSHE','XSHG') AND s.instrument_type='A_SHARE'
                AND c.trade_date=CAST(b.observation_start AS DATE)
                AND b.observation_end<=TIMESTAMPTZ {_q(cutoff.isoformat())}
            ORDER BY b.listing_id,b.observation_start"""
        _zero(
            c,
            "SELECT count(*) FROM (SELECT listing_id,calendar_session_id FROM ("
            + query
            + ") GROUP BY ALL HAVING count(*)>1)",
            "AMBIGUOUS_MARKET_IDENTITY",
        )
        for batch in c.execute(query).to_arrow_reader(batch_size):
            self._unchanged()
            yield batch
        self._unchanged()

    def input_census(self, cutoff: datetime) -> dict[str, Any]:
        """Safe diagnostics only. Missing evidence is counted, never granted a cutoff."""
        self.at(cutoff)
        result: dict[str, Any] = {
            "knowledge_cutoff": cutoff.isoformat(),
            "tables": {},
            "consumer_admission": "NOT_GRANTED",
        }
        for table in KEYS:
            row = self.connection.execute(
                f"SELECT count(*), "
                "count(*) FILTER (WHERE knowledge_from IS NULL), "
                f"count(*) FILTER (WHERE knowledge_from>TIMESTAMPTZ {_q(cutoff.isoformat())}) "
                f"FROM raw_{table}"
            ).fetchone()
            visible = self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            result["tables"][table] = dict(
                zip(
                    ("retained_versions", "unknown_knowledge", "not_yet_known", "visible_versions"),
                    (*row, visible),
                    strict=True,
                )
            )
        self._unchanged()
        return result


def open_prepared(root: Path, *, manifest_sha256: str) -> NativeView:
    frozen = {t: _fingerprint(_member(root, f"native/{t}.parquet")) for t in KEYS}
    manifest = verify(root, manifest_sha256=manifest_sha256)
    view = NativeView(root, deadline=time.monotonic() + manifest["identity"]["wall_seconds"])
    if view.fingerprints != frozen:
        view.__exit__()
        raise InputError("PREPARED_FILES_CHANGED_DURING_OPEN")
    return view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("metadata", "prepare", "verify", "census"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--run-plan", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--knowledge-cutoff")
    args = parser.parse_args(argv)
    try:
        if args.command == "metadata":
            result = source_metadata(args.root, args.manifest_sha256)
            result.pop("source_manifest")
            result.pop("native_closure")
        elif args.command == "census":
            if args.knowledge_cutoff is None:
                raise InputError("EXPLICIT_KNOWLEDGE_CUTOFF_REQUIRED")
            cutoff = datetime.fromisoformat(args.knowledge_cutoff)
            with open_prepared(args.root, manifest_sha256=args.manifest_sha256) as view:
                result = view.input_census(cutoff)
            result.update(
                {"manifest_sha256": args.manifest_sha256, "B_accepted": False, "D_activated": False}
            )
        elif args.command == "verify":
            result = verify(args.root, manifest_sha256=args.manifest_sha256)
            result = {
                k: result[k]
                for k in (
                    "manifest_sha256",
                    "consumer_admission",
                    "B_accepted",
                    "D_activated",
                    "semantic_summary",
                )
            }
        else:
            if args.run_plan is None or args.output is None:
                raise InputError("EXPLICIT_INPUT_ONLY_RUN_PLAN_REQUIRED")
            plan = _json(physical(args.run_plan))
            result = prepare(
                args.root,
                args.output,
                source_manifest_sha256=args.manifest_sha256,
                code_revision=plan["code_revision"],
                run_plan=plan,
                max_output_bytes=plan["max_output_bytes"],
                wall_seconds=plan["wall_seconds"],
            )
            result = {
                k: result[k]
                for k in (
                    "manifest_sha256",
                    "consumer_admission",
                    "B_accepted",
                    "D_activated",
                    "semantic_summary",
                )
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as e:
        # DuckDB/Arrow may put values or raw paths in their exception messages.
        # Keep public logs safe; InputError messages are controlled reason codes.
        reason = str(e) if isinstance(e, InputError) else type(e).__name__
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": reason, "B_accepted": False, "D_activated": False}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
