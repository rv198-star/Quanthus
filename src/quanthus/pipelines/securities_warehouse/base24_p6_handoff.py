"""P10's bounded, physically scoped source handoff to the P6 consumer.

This module copies qualified source observations, never invents knowledge or
execution authority. One materialization serves the purpose census and readers.
An integrity PASS and a purpose NO_PASS can coexist in a complete handoff.
"""

from __future__ import annotations

import importlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.backtest.base24_semantic_projection import (
    KEYS,
    NATIVE_KEYS,
    _market,
    _queries,
    revision_transitions,
)
from quanthus.backtest.base24_semantic_projection import (
    VERSION as SOURCE_VERSION,
)

from .base22 import file_sha
from .base22_sources import _fingerprint, _tree, disjoint_output, physical_path

VERSION = "quanthus.p10-base24-p6-source-handoff.v1"
NATIVE = (
    "dwd_security",
    "dwd_listing",
    "dwd_trading_venue",
    "dwd_trading_calendar",
    "dwd_security_bar",
    "dwd_tradability_state",
    "dwd_price_limit_regime",
    "dwd_listing_status_interval",
    "dwd_security_status_interval",
    "dwd_corporate_action",
    "dwd_corporate_action_term",
    "dwd_adjustment_factor",
)
DATE_COLUMNS = {
    "daily_ohlcv_none": "date",
    "daily_universe": "market_date",
    "price_limit_execution_daily": "execution_date",
    "trading_calendar": "trade_date",
    "corporate_actions_normalized": "effective_date",
    "corporate_action_exceptions": "effective_date",
}
CONFIG: dict[str, Any] = {
    "schema_version": VERSION,
    "scope": {"business_start": "2026-01-01", "venues": ["SSE", "SZSE"]},
    "historical_close_local_time": "15:00:00",
    "minimum_history_sessions": 120,
    "minimum_feature_sessions": 61,
    "candidate_history_sessions": 22,
    "clock_rule": "knowledge_from<=cutoff<knowledge_to; unknown fails",
    "official_execution_authorized": False,
    "legacy_runtime_changed": False,
}


class HandoffError(ValueError):
    """Stable fail-closed source-handoff failure."""


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise HandoffError("OBJECT_REQUIRED")
    return value


def _connection(root: Path) -> Any:
    c = importlib.import_module("duckdb").connect()
    c.execute("SET memory_limit='2GB'")
    c.execute("SET threads=2")
    c.execute(f"SET temp_directory={_q(root / 'sort-tmp')}")
    c.execute("SET max_temp_directory_size='2GB'")
    c.execute("SET TimeZone='Asia/Shanghai'")
    c.execute("SET autoinstall_known_extensions=false")
    c.execute("SET autoload_known_extensions=false")
    return c


def _copy(c: Any, query: str, path: Path) -> None:
    c.execute(f"COPY ({query}) TO {_q(path)} (FORMAT PARQUET,COMPRESSION ZSTD)")


def _member(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != relative:
        raise HandoffError("MEMBER_PATH_ESCAPE")
    return physical_path(root / rel)


def _views(c: Any, root: Path) -> None:
    for name in KEYS:
        path = root / "tables" / (name + ".parquet")
        c.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({_q(path)})")
    for name in NATIVE:
        path = root / "native" / (name + ".parquet")
        c.execute(f"CREATE VIEW n_{name} AS SELECT * FROM read_parquet({_q(path)})")


def _count(c: Any, query: str) -> int:
    return int(c.execute(query).fetchone()[0])


def _reconstruct(c: Any, root: Path, cutoff: str, business_end: str) -> None:
    """Rebuild seven meanings with one shared, bounded market TMP per month.

    Year-wide, wide-row EXCEPT can exhaust the declared memory before spilling.
    References are shared once; the three market consumers share one monthly
    join. No semantic row or knowledge version is skipped to meet a budget.
    """
    visibility = (
        f"knowledge_from<=TIMESTAMPTZ {_q(cutoff)} AND "
        f"coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>TIMESTAMPTZ {_q(cutoff)}"
    )
    market = {"dwd_security_bar", "dwd_tradability_state"}

    def select(name: str, window: str = "true") -> None:
        keys = NATIVE_KEYS[name]
        raw = f"(SELECT DISTINCT * FROM n_{name} WHERE {window})"
        if _count(
            c,
            (
                f"SELECT count(*) FROM (SELECT {keys},knowledge_from "
                f"FROM {raw} WHERE {visibility} GROUP BY ALL "
                f"HAVING count(*)>1)"
            ),
        ):
            raise HandoffError("AMBIGUOUS_NATIVE_VERSION:" + name)
        c.execute(
            f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT "
            f"* FROM {raw} WHERE {visibility} QUALIFY "
            f"row_number() OVER (PARTITION BY {keys} ORDER "
            f"BY knowledge_from DESC)=1"
        )

    def compare(name: str, query: str, actual: str) -> None:
        actual_schema = c.execute(f"SELECT * FROM ({actual}) LIMIT 0").description
        expected_schema = c.execute(f"SELECT * FROM ({query}) LIMIT 0").description
        if [(v[0], str(v[1])) for v in actual_schema] != [
            (v[0], str(v[1])) for v in expected_schema
        ]:
            raise HandoffError("SEMANTIC_SCHEMA:" + name)
        if _count(
            c,
            (
                f"SELECT count(*) FROM ((({query}) EXCEPT "
                f"ALL ({actual})) UNION ALL (({actual}) EXCEPT "
                f"ALL ({query})))"
            ),
        ):
            raise HandoffError("SEMANTIC_VALUES:" + name)

    for name in NATIVE:
        if name in NATIVE_KEYS and name not in market:
            select(name)
    cursor, through = date(2026, 1, 1), date.fromisoformat(business_end)
    while cursor <= through:
        following = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        window = (
            f"observation_start>=TIMESTAMPTZ '{cursor}T00:00:00+08:00' AND "
            f"observation_start<TIMESTAMPTZ '{following}T00:00:00+08:00'"
        )
        for name in sorted(market):
            select(name, window)
        for name, query in _market(c, cursor.isoformat(), following.isoformat()).items():
            column = DATE_COLUMNS[name]
            compare(
                name,
                query,
                (
                    f"SELECT * FROM {name} WHERE {column}>=DATE "
                    f"'{cursor}' AND {column}<DATE '{following}'"
                ),
            )
        cursor = following
    for name, query in _queries().items():
        if name not in {"daily_ohlcv_none", "daily_universe", "price_limit_execution_daily"}:
            compare(name, query, f"SELECT * FROM {name}")


def assess(root: Path, *, business_end: str, knowledge_cutoff: str) -> dict[str, Any]:
    """Recompute safe purpose counts from the local closure, all native versions.

    Strict historical visibility uses EXISTS over all original versions, not the
    latest snapshot alone. Later warm-up availability is a distinct measurement.
    This is a P10 source assessment, not candidate scanning or activation.
    """
    c = _connection(root)
    try:
        _views(c, root)
        _reconstruct(c, root, knowledge_cutoff, business_end)
        counts = {name: _count(c, f"SELECT count(*) FROM {name}") for name in KEYS}
        for name, keys in KEYS.items():
            if _count(
                c,
                (
                    f"SELECT count(*) FROM (SELECT {','.join(keys)} "
                    f"FROM {name} GROUP BY ALL HAVING count(*)>1)"
                ),
            ):
                raise HandoffError("DUPLICATE_KEY:" + name)
            if _count(
                c, f"SELECT count(*) FROM {name} WHERE " + " OR ".join(k + " IS NULL" for k in keys)
            ):
                raise HandoffError("NULL_KEY:" + name)
        for name, column in DATE_COLUMNS.items():
            if _count(
                c,
                (
                    f"SELECT count(*) FROM {name} WHERE {column} "
                    f"IS NULL OR {column}<DATE '2026-01-01' OR "
                    f"{column}>DATE {_q(business_end)}"
                ),
            ):
                raise HandoffError("BUSINESS_SCOPE:" + name)
        for name, column in [
            ("daily_ohlcv_none", "identity_exchange"),
            ("daily_universe", "exchange"),
            ("price_limit_execution_daily", "exchange"),
            ("trading_calendar", "exchange"),
            ("security_code_validity", "exchange"),
        ]:
            if _count(
                c,
                (
                    f"SELECT count(*) FROM {name} WHERE {column} "
                    f"IS NULL OR {column} NOT IN ('SSE','SZSE')"
                ),
            ):
                raise HandoffError("VENUE_SCOPE:" + name)
        c.execute(
            "CREATE TEMP TABLE identities AS SELECT DISTINCT "
            "listing_id,security_id,venue_id FROM n_dwd_listing"
        )
        if _count(
            c,
            (
                "SELECT count(*) FROM (SELECT listing_id "
                "FROM identities GROUP BY ALL HAVING count(*)<>1)"
            ),
        ):
            raise HandoffError("AMBIGUOUS_LISTING_IDENTITY")
        c.execute("""CREATE TEMP TABLE sessions AS
            SELECT DISTINCT calendar_session_id,trade_date,venue_id
            FROM n_dwd_trading_calendar""")
        if _count(
            c,
            (
                "SELECT count(*) FROM (SELECT calendar_session_id "
                "FROM sessions GROUP BY ALL HAVING count(*)<>1)"
            ),
        ):
            raise HandoffError("AMBIGUOUS_CALENDAR_IDENTITY")
        for table in ("dwd_security_bar", "dwd_tradability_state"):
            if _count(
                c,
                f"""SELECT count(*) FROM n_{table} b LEFT JOIN sessions s USING(calendar_session_id)
                LEFT JOIN identities l USING(listing_id) WHERE s.calendar_session_id IS NULL
                OR l.listing_id IS NULL OR s.venue_id<>l.venue_id
                OR s.trade_date<>CAST(b.observation_start AS DATE)
                OR s.trade_date<DATE '2026-01-01' OR s.trade_date>DATE {_q(business_end)}""",
            ):
                raise HandoffError("CALENDAR_LISTING_DATE:" + table)
        if _count(
            c, "SELECT count(*) FROM daily_ohlcv_none WHERE adj_mode IS DISTINCT FROM 'none'"
        ):
            raise HandoffError("PRICE_BASIS")
        if _count(
            c,
            (
                "SELECT count(*) FROM n_dwd_security_bar "
                "WHERE interval_size IS DISTINCT FROM '1d' "
                "OR price_basis IS DISTINCT FROM 'UNADJUSTED'"
            ),
        ):
            raise HandoffError("NATIVE_PRICE_BASIS")
        c.execute("""CREATE TEMP TABLE denominator AS
            SELECT DISTINCT t.listing_id,t.calendar_session_id,l.security_id,
              s.trade_date,s.venue_id,
              (CAST(s.trade_date AS TIMESTAMP)+INTERVAL 15 HOUR)
                  AT TIME ZONE 'Asia/Shanghai' AS session_cutoff
            FROM n_dwd_tradability_state t JOIN sessions s USING(calendar_session_id)
            JOIN identities l USING(listing_id)""")
        core = {
            "venue": (
                "dwd_trading_venue",
                "x.venue_id=d.venue_id AND "
                "x.effective_from<=d.session_cutoff AND "
                "coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>d.session_cutoff",
            ),
            "bar": (
                "dwd_security_bar",
                "x.listing_id=d.listing_id AND x.calendar_session_id=d.calendar_session_id",
            ),
            "state": (
                "dwd_tradability_state",
                "x.listing_id=d.listing_id AND x.calendar_session_id=d.calendar_session_id",
            ),
            "calendar": ("dwd_trading_calendar", "x.calendar_session_id=d.calendar_session_id"),
            "listing": (
                "dwd_listing",
                (
                    "x.listing_id=d.listing_id AND x.effective_from<=d.session_cutoff "
                    "AND coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>d.session_cutoff"
                ),
            ),
            "security": (
                "dwd_security",
                (
                    "x.security_id=d.security_id AND x.effective_from<=d.session_cutoff "
                    "AND coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>d.session_cutoff"
                ),
            ),
        }
        # One immutable, exact native-version TMP per family is shared by its
        # two clock checks. Avoid a twelve-way correlated plan retaining all
        # wide native tables simultaneously under the fixed memory budget.
        c.execute("CREATE TEMP TABLE visibility AS SELECT * FROM denominator")
        for family, (table, relation) in core.items():
            c.execute(
                f"CREATE OR REPLACE TEMP TABLE core_versions AS SELECT DISTINCT * FROM n_{table}"
            )
            columns = []
            for mode, cutoff in [
                ("historical", "d.session_cutoff"),
                ("later", "TIMESTAMPTZ " + _q(knowledge_cutoff)),
            ]:
                column = mode + "_" + family
                c.execute(f"ALTER TABLE visibility ADD COLUMN {column} BOOLEAN")
                columns.append(
                    f"{column}=(SELECT count(*)=1 FROM core_versions "
                    f"x WHERE {relation} AND x.knowledge_from<={cutoff} "
                    f"AND coalesce(x.knowledge_to,'infinity'::TIMESTAMPTZ)>{cutoff})"
                )
            c.execute("UPDATE visibility d SET " + ",".join(columns))
        c.execute("DROP TABLE core_versions")
        total = _count(c, "SELECT count(*) FROM denominator")
        clocks = {}
        for mode in ("historical", "later"):
            clocks[mode] = {
                family: _count(c, f"SELECT count(*) FROM visibility WHERE NOT {mode}_{family}")
                for family in core
            }
            clocks[mode]["all_core_visible"] = _count(
                c,
                "SELECT count(*) FROM visibility WHERE "
                + " AND ".join(mode + "_" + k for k in core),
            )
        unknown_execution = _count(
            c,
            """SELECT count(*) FROM price_limit_execution_daily
            WHERE status_known IS DISTINCT FROM true OR regime_known IS DISTINCT FROM true
            OR in_ipo_window IS NULL OR selected_rule_basis IS NULL""",
        )
        action_unqualified = _count(
            c,
            """SELECT count(*) FROM corporate_actions_normalized
            WHERE execution_authorized IS DISTINCT FROM true OR authority_evidence_digest IS NULL
            OR factor_cross_check_status IS DISTINCT FROM 'PASS'""",
        )
        action_orphans = _count(
            c,
            (
                "SELECT count(*) FROM n_dwd_corporate_action_term "
                "t WHERE NOT EXISTS (SELECT 1 FROM n_dwd_corporate_action "
                "a WHERE a.corporate_action_id=t.corporate_action_id)"
            ),
        )
        if action_orphans:
            raise HandoffError("ACTION_TERM_ORPHAN")
        sessions_count = _count(
            c,
            "SELECT count(DISTINCT d.trade_date) FROM denominator d WHERE EXISTS "
            "(SELECT 1 FROM dwd_trading_calendar k WHERE "
            "k.calendar_session_id=d.calendar_session_id AND k.is_open IS TRUE)",
        )
        historical = (
            total > 0
            and clocks["historical"]["all_core_visible"] == total
            and sessions_count >= CONFIG["minimum_history_sessions"]
        )
        later = (
            total > 0
            and clocks["later"]["all_core_visible"] == total
            and sessions_count >= CONFIG["minimum_history_sessions"]
        )
        return {
            "denominator_listing_sessions": total,
            "history_sessions": sessions_count,
            "rows": counts,
            "clocks": clocks,
            "execution_rows_unqualified": unknown_execution,
            "action_rows_unqualified": action_unqualified,
            "native_status_rows": {
                name: _count(c, f"SELECT count(*) FROM n_{name}")
                for name in (
                    "dwd_security_status_interval",
                    "dwd_listing_status_interval",
                    "dwd_price_limit_regime",
                )
            },
            "reference_evidence": {
                "status_unknown_clock_rows": _count(
                    c,
                    (
                        "SELECT count(*) FROM n_dwd_security_status_interval "
                        "WHERE knowledge_from IS NULL"
                    ),
                ),
                "status_unknown_value_rows": _count(
                    c,
                    (
                        "SELECT count(*) FROM n_dwd_security_status_interval "
                        "WHERE status_value IS NULL OR status_value='UNKNOWN'"
                    ),
                ),
                "action_unknown_clock_rows": _count(
                    c, "SELECT count(*) FROM n_dwd_corporate_action WHERE knowledge_from IS NULL"
                ),
                "action_terms_without_currency": _count(
                    c,
                    (
                        "SELECT count(*) FROM n_dwd_corporate_action_term "
                        "WHERE amount_per_share IS NOT NULL AND currency "
                        "IS NULL"
                    ),
                ),
                "adjustment_factor_rows": _count(c, "SELECT count(*) FROM n_dwd_adjustment_factor"),
            },
            "missing_qualifications": [
                "P10_PURPOSE_ATTESTATION_FOR_EXECUTION_STATUS_AND_IPO_REGIME",
                "ACTION_AUTHORITY_NORMALIZATION_AND_FACTOR_CROSSCHECK",
                "P6_CONSUMER_ACCEPTANCE_OF_DECLARED_SNAPSHOT_SEPARATE_FROM_ACTIVATION",
            ],
            "purposes": {
                "source_diagnostics": True,
                "later_market_context_available": later,
                "historical_market_context": historical,
                # Diagnostic counts cannot substitute for a signed/versioned purpose
                # qualification. This v1 interface does not contain that grant.
                "historical_replay": False,
                "paper_entry_source": False,
            },
            "knowledge_time_reinterpreted": False,
            "official_execution_authorized": False,
            "status_authority_inferred_from_source_row_count": False,
        }
    finally:
        c.close()


def build(
    source: Path,
    output: Path,
    *,
    expected_source_sha256: str,
    status_source: Path,
    expected_status_sha256: str,
    business_end: str,
    code_revision: str,
) -> dict[str, Any]:
    """P10 scopes the already-sealed snapshot; never mutates old inputs.

    The one additional status member is explicitly selected by the caller from
    the locked Base inventory, not searched for or inferred from a path name.
    """
    source, status_source = physical_path(source), physical_path(status_source)
    output = disjoint_output(output, [source, status_source.parent])
    if output.exists():
        raise HandoffError("OUTPUT_EXISTS")
    if date.fromisoformat(business_end).year != 2026:
        raise HandoffError("P6_YEAR_SCOPE")
    sm = _read(source / "manifest.json")
    if (
        file_sha(source / "manifest.json") != expected_source_sha256
        or (source / "READY").read_text().strip() != expected_source_sha256
    ):
        raise HandoffError("SOURCE_IDENTITY")
    if sm.get("schema_version") != SOURCE_VERSION or business_end > sm["scope"]["business_end"]:
        raise HandoffError("SOURCE_VERSION_OR_SCOPE")
    required = ["tables/" + n + ".parquet" for n in KEYS]
    required += ["native/" + n + ".parquet" for n in NATIVE if n != "dwd_security_status_interval"]
    fingerprints = {
        p: _fingerprint(p)
        for p in (source / "manifest.json", source / "READY", source / "input-view.json")
    }
    if file_sha(source / "input-view.json") != sm["files"]["input-view.json"]["sha256"]:
        raise HandoffError("SOURCE_INPUT_VIEW_IDENTITY")
    input_view = _read(source / "input-view.json")
    transitions = revision_transitions(input_view)
    closure = {}
    for relative in required:
        p = _member(source, relative)
        fingerprints[p] = _fingerprint(p)
        expected = sm["files"][relative]
        if p.stat().st_size != expected["bytes"] or file_sha(p) != expected["sha256"]:
            raise HandoffError("SOURCE_MEMBER:" + relative)
        closure[relative] = expected
    fingerprints[status_source] = _fingerprint(status_source)
    if file_sha(status_source) != expected_status_sha256:
        raise HandoffError("STATUS_SOURCE_IDENTITY")
    output.mkdir()
    (output / "tables").mkdir()
    (output / "native").mkdir()
    c = _connection(output)
    try:
        for name in KEYS:
            path = source / "tables" / (name + ".parquet")
            c.execute(f"CREATE VIEW s_{name} AS SELECT * FROM read_parquet({_q(path)})")
        for name in NATIVE:
            p = (
                status_source
                if name == "dwd_security_status_interval"
                else source / "native" / (name + ".parquet")
            )
            c.execute(f"CREATE VIEW s_{name} AS SELECT * FROM read_parquet({_q(p)})")
        c.execute(f"""CREATE TEMP TABLE ids AS SELECT DISTINCT listing_id,security_id,venue_id
            FROM s_dwd_listing l WHERE venue_id IN
            (SELECT venue_id FROM s_dwd_trading_venue WHERE mic_code IN ('XSHG','XSHE'))
            AND effective_from<TIMESTAMPTZ {_q(business_end + "T23:59:59.999999+08:00")}
            AND coalesce(effective_to,'infinity'::TIMESTAMPTZ)
              >TIMESTAMPTZ '2026-01-01T00:00:00+08:00'""")
        for name in KEYS:
            if name == "security_code_validity":
                where = "dwd_listing_id IN (SELECT listing_id FROM ids)"
            elif name in ("corporate_actions_normalized", "corporate_action_exceptions"):
                where = (
                    f"dwd_security_id IN (SELECT security_id FROM "
                    f"ids) AND {DATE_COLUMNS[name]} BETWEEN DATE "
                    f"'2026-01-01' AND DATE {_q(business_end)}"
                )
            else:
                venue = "identity_exchange" if name == "daily_ohlcv_none" else "exchange"
                where = (
                    f"{venue} IN ('SSE','SZSE') AND {DATE_COLUMNS[name]} "
                    f"BETWEEN DATE '2026-01-01' AND DATE {_q(business_end)}"
                )
            _copy(
                c, f"SELECT * FROM s_{name} WHERE {where}", output / "tables" / (name + ".parquet")
            )
        native_filters = {
            "dwd_security": "security_id IN (SELECT security_id FROM ids)",
            "dwd_listing": "listing_id IN (SELECT listing_id FROM ids)",
            "dwd_trading_venue": "mic_code IN ('XSHG','XSHE')",
            "dwd_trading_calendar": (
                f"venue_id IN (SELECT venue_id FROM ids) AND "
                f"trade_date BETWEEN DATE '2026-01-01' AND "
                f"DATE {_q(business_end)}"
            ),
            "dwd_security_bar": (
                f"listing_id IN (SELECT listing_id FROM ids) "
                f"AND CAST(observation_start AS DATE) BETWEEN "
                f"DATE '2026-01-01' AND DATE {_q(business_end)}"
            ),
            "dwd_tradability_state": (
                f"listing_id IN (SELECT listing_id FROM ids) "
                f"AND CAST(observation_start AS DATE) BETWEEN "
                f"DATE '2026-01-01' AND DATE {_q(business_end)}"
            ),
            "dwd_price_limit_regime": "venue_id IN (SELECT venue_id FROM ids)",
            "dwd_listing_status_interval": "listing_id IN (SELECT listing_id FROM ids)",
            "dwd_security_status_interval": "security_id IN (SELECT security_id FROM ids)",
            "dwd_corporate_action": (
                f"security_id IN (SELECT security_id FROM "
                f"ids) AND CAST(effective_at AS DATE) BETWEEN "
                f"DATE '2026-01-01' AND DATE {_q(business_end)}"
            ),
            "dwd_corporate_action_term": (
                "corporate_action_id IN (SELECT corporate_action_id FROM read_parquet("
            )
            + _q(output / "native/dwd_corporate_action.parquet")
            + "))",
            "dwd_adjustment_factor": "security_id IN (SELECT security_id FROM ids)",
        }
        temporal = (
            f"effective_from<=TIMESTAMPTZ {_q(business_end + 'T23:59:59.999999+08:00')} "
            f"AND coalesce(effective_to,'infinity'::TIMESTAMPTZ)>TIMESTAMPTZ "
            f"'2026-01-01T00:00:00+08:00'"
        )
        for name in (
            "dwd_security",
            "dwd_listing",
            "dwd_trading_venue",
            "dwd_price_limit_regime",
            "dwd_listing_status_interval",
            "dwd_security_status_interval",
            "dwd_adjustment_factor",
        ):
            native_filters[name] = "(" + native_filters[name] + ") AND " + temporal
        for name in NATIVE:
            _copy(
                c,
                f"SELECT * FROM s_{name} WHERE {native_filters[name]}",
                output / "native" / (name + ".parquet"),
            )
    finally:
        c.close()
    if any(_fingerprint(p) != value for p, value in fingerprints.items()):
        raise HandoffError("SOURCE_CHANGED_DURING_BUILD")
    assessment = assess(output, business_end=business_end, knowledge_cutoff=sm["knowledge_cutoff"])
    _write(
        output / "source-evidence.json",
        {
            "source_manifest_sha256": expected_source_sha256,
            "source_input_view": sm["input_view_digest"],
            "source_members": closure,
            "additional_status_sha256": expected_status_sha256,
            "source_scope": sm["scope"],
            "selected_input_view": input_view,
            "daily_revision_transitions": transitions,
            "source_paths_runtime_dependencies": False,
        },
    )
    _write(output / "assessment.json", assessment)
    _write(output / "contract.json", CONFIG)
    pq = importlib.import_module("pyarrow.parquet")
    members = {}
    for relative in sorted(_tree(output)):
        p = output / relative
        record: dict[str, Any] = {"sha256": file_sha(p), "bytes": p.stat().st_size}
        if p.suffix == ".parquet":
            meta = pq.ParquetFile(p)
            record.update(
                rows=meta.metadata.num_rows,
                schema=[(f.name, str(f.type)) for f in meta.schema_arrow],
            )
        members[relative] = record
    result = {
        "schema_version": VERSION,
        "code_revision": code_revision,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "business_start": "2026-01-01",
        "business_end": business_end,
        "venues": ["SSE", "SZSE"],
        "knowledge_cutoff": sm["knowledge_cutoff"],
        "source_manifest_sha256": expected_source_sha256,
        "files": members,
        "consumer_admission": "SOURCE_DIAGNOSTICS_ONLY",
        "execution_authorized": False,
    }
    _write(output / "manifest.json", result)
    (output / "READY").write_text(file_sha(output / "manifest.json") + "\n", encoding="ascii")
    return result


def verify(root: Path, expected_sha256: str) -> dict[str, Any]:
    """Local-only cold validation, including recomputation of purpose findings."""
    root = physical_path(root)
    if (
        file_sha(root / "manifest.json") != expected_sha256
        or (root / "READY").read_text().strip() != expected_sha256
    ):
        raise HandoffError("HANDOFF_IDENTITY")
    m = _read(root / "manifest.json")
    if (
        m.get("schema_version") != VERSION
        or m.get("venues") != ["SSE", "SZSE"]
        or m.get("business_start") != "2026-01-01"
        or date.fromisoformat(m["business_end"]).year != 2026
        or m.get("execution_authorized") is not False
        or m.get("consumer_admission") != "SOURCE_DIAGNOSTICS_ONLY"
    ):
        raise HandoffError("HANDOFF_CONTRACT")
    required = (
        {"tables/" + n + ".parquet" for n in KEYS}
        | {"native/" + n + ".parquet" for n in NATIVE}
        | {"contract.json", "source-evidence.json", "assessment.json"}
    )
    if set(m["files"]) != required or _tree(root) != required | {"manifest.json", "READY"}:
        raise HandoffError("HANDOFF_PHYSICAL_SET")
    pq = importlib.import_module("pyarrow.parquet")
    for relative, expected in m["files"].items():
        p = _member(root, relative)
        if p.stat().st_size != expected["bytes"] or file_sha(p) != expected["sha256"]:
            raise HandoffError("HANDOFF_MEMBER:" + relative)
        if p.suffix == ".parquet":
            parquet = pq.ParquetFile(p)
            if parquet.metadata.num_rows != expected["rows"] or [
                [f.name, str(f.type)] for f in parquet.schema_arrow
            ] != [list(v) for v in expected["schema"]]:
                raise HandoffError("HANDOFF_SCHEMA_OR_ROWS:" + relative)
    evidence = _read(root / "source-evidence.json")
    if (
        evidence["source_manifest_sha256"] != m["source_manifest_sha256"]
        or evidence["daily_revision_transitions"]
        != revision_transitions(evidence["selected_input_view"])
        or evidence["source_paths_runtime_dependencies"] is not False
    ):
        raise HandoffError("SOURCE_BINDING_DRIFT")
    if _read(root / "contract.json") != CONFIG:
        raise HandoffError("POLICY_DRIFT")
    actual = assess(root, business_end=m["business_end"], knowledge_cutoff=m["knowledge_cutoff"])
    if actual != _read(root / "assessment.json"):
        raise HandoffError("ASSESSMENT_RECONSTRUCTION")
    return actual
