"""P6's versioned Base 2.4 input admission, without an activation edge.

P10 qualifies native observations once. This consumer binds the exact window,
dated target Daily and real decision clock, holding only affected securities.
It never repairs sources or reuses the legacy 15:00 historical grant.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.serialization import digest_value

VERSION = "quanthus.p6-base24-runtime-inputs.v1"
CLOCK_VERSION = "quanthus.p6-after-close-decision-clock.v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        fail("p6_base24_clock", field + " requires an aware instant")
    return value


@dataclass(frozen=True)
class AfterCloseDecisionClock:
    """Observation end, actual visibility and decision are distinct instants.

    An optional next opening must come from a bound authoritative calendar.
    The conservative seal deadline alone never establishes a fill coordinate.
    """

    market_date: date
    source_available_at: datetime
    decision_at: datetime
    next_session_open: datetime | None = None

    def __post_init__(self) -> None:
        for field in ("source_available_at", "decision_at"):
            _aware(getattr(self, field), field)
        if self.next_session_open is not None:
            _aware(self.next_session_open, "next_session_open")
        if type(self.market_date) is not date or self.market_date < date(2026, 1, 1):
            fail("p6_base24_market_scope", str(self.market_date))
        if self.source_available_at > self.decision_at:
            fail("p6_base24_not_yet_visible", "source is unavailable at decision time")
        if self.decision_at < self.observation_end:
            fail("p6_base24_before_close", "daily observation has not ended")
        # This conservative seal deadline is already used by official_cycle.
        # It is a guard, never a guessed future trading session or fill time.
        if self.decision_at >= self.seal_deadline or (
            self.next_session_open is not None
            and (
                self.next_session_open.astimezone(SHANGHAI).date() <= self.market_date
                or self.next_session_open.astimezone(SHANGHAI).time() != time(9, 30)
                or self.decision_at >= self.next_session_open
            )
        ):
            fail("p6_base24_missed_next_open", "decision must precede the bound next open")

    @property
    def observation_end(self) -> datetime:
        return datetime.combine(self.market_date, time(15), SHANGHAI)

    @property
    def seal_deadline(self) -> datetime:
        return datetime.combine(self.market_date + timedelta(days=1), time(9), SHANGHAI)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CLOCK_VERSION,
            "market_date": self.market_date.isoformat(),
            "observation_end": self.observation_end.isoformat(),
            "source_available_at": self.source_available_at.isoformat(),
            "decision_at": self.decision_at.isoformat(),
            "seal_deadline": self.seal_deadline.isoformat(),
            "next_session_open": (
                None if self.next_session_open is None else self.next_session_open.isoformat()
            ),
        }

    @property
    def identity(self) -> str:
        return digest_value(self.to_dict())


@dataclass(frozen=True)
class P6Base24CandidatePolicy:
    clock: AfterCloseDecisionClock
    history_start: date
    inputs_manifest_sha256: str

    @property
    def prospective_clock_json(self) -> str:
        return json.dumps(self.clock.to_dict(), sort_keys=True)

    @property
    def semantic_digest(self) -> str:
        return digest_value(
            {
                "schema_version": VERSION,
                "clock": self.clock.to_dict(),
                "history_start": str(self.history_start),
                "input": self.inputs_manifest_sha256,
            }
        )

    def require_stage_b_authority(self) -> None:
        return None

    def require_scan_date(self, value: date) -> None:
        if (
            not max(self.history_start, date(2026, 1, 1))
            <= value
            <= self.clock.market_date + timedelta(days=14)
        ):
            fail("p6_base24_scan_scope", str(value))

    def candidate_signal_date_allowed(self, value: date) -> bool:
        return value == self.clock.market_date


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        fail("p6_base24_object", path.name)
    return value


@dataclass(frozen=True)
class VerifiedTargetDaily:
    identity_json: str
    core_root: Path
    status: Path
    release: Path
    fingerprints: Mapping[str, tuple[Any, ...]]
    core_files: frozenset[str]

    def identity(self) -> dict[str, Any]:
        return dict(json.loads(self.identity_json))

    def check_unchanged(self) -> None:
        from quanthus.pipelines.securities_warehouse.base22_sources import (
            _fingerprint,
            _tree,
            physical_path,
        )

        if _tree(self.core_root) != self.core_files:
            fail("p6_base24_target_changed", "physical namespace")
        for p, fingerprint in self.fingerprints.items():
            if _fingerprint(physical_path(Path(p))) != fingerprint:
                fail("p6_base24_target_changed", Path(p).name)


def verify_target_daily(
    context: Any,
    *,
    core_root: Path,
    core_manifest_sha256: str,
    release: Path,
    status: Path,
) -> VerifiedTargetDaily:
    """Bind a real native 1d core and its exact original dated status member.

    This checks the source's own identity and is independent of retrospective
    session-close admission. It cannot mint an old visibility receipt.
    """
    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base22_sources import (
        _fingerprint,
        _tree,
        physical_path,
    )
    from quanthus.pipelines.securities_warehouse.daily_core_domain import (
        verify_daily_domain_increment,
    )

    context.check_unchanged()
    core_root, release, status = map(physical_path, (core_root, release, status))
    core_files = frozenset(_tree(core_root))
    before = {
        str(p): _fingerprint(p)
        for p in [
            *(core_root / rel for rel in core_files),
            release,
            status,
        ]
    }
    checked = verify_daily_domain_increment(core_root)
    core = _object(core_root / "manifest.json")
    if (
        checked.manifest_sha256 != core_manifest_sha256
        or core.get("kind") != "cn01_daily_core_dwd_increment"
        or core.get("consumable_intervals") != ["1d"]
    ):
        fail("p6_base24_target_identity", "exact native daily core required")
    selection = _object(context.root / "source/source-evidence.json")["selected_input_view"]
    selected = [
        row for row in selection["Daily"] if row["market_date"] == checked.market_date.isoformat()
    ]
    if len(selected) != 1 or selected[0]["core_manifest_sha256"] != core_manifest_sha256:
        fail("p6_base24_target_revision", "target differs from qualified source selection")
    original = _object(release)
    sha = file_sha(release)
    chosen = core["selected_releases"]["market_daily_core"]
    if (
        sha != chosen["manifest_sha256"]
        or original.get("release_id") != chosen["release_id"]
        or original.get("market_date") != checked.market_date.isoformat()
    ):
        fail("p6_base24_status_release", "provider release does not bind target")
    artifacts = original.get("artifacts")
    if isinstance(artifacts, dict):
        artifacts = list(artifacts.values())
    if not isinstance(artifacts, list):
        fail("p6_base24_status_member", "original artifact inventory missing")
    members = [a for a in artifacts if a.get("name") == "daily_status_observation_delta"]
    if len(members) != 1 or members[0].get("sha256") != file_sha(status):
        fail("p6_base24_status_member", "status is not the selected original member")
    if members[0].get("size_bytes") != status.stat().st_size:
        fail("p6_base24_status_member", "status member size differs")
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    raw = pq.ParquetFile(status)
    required = {"stock_code", "market_date", "date", "acquired_at", "isST", "tradestatus"}
    if not required.issubset(raw.schema_arrow.names):
        fail("p6_base24_status_schema", "dated provider fields missing")
    for batch in raw.iter_batches(columns=["stock_code", "market_date", "date"], batch_size=8192):
        for row in batch.to_pylist():
            if (
                row["market_date"] != checked.market_date.isoformat()
                or row["date"] != row["market_date"]
                or not isinstance(row["stock_code"], str)
                or len(row["stock_code"]) != 6
                or not row["stock_code"].isdigit()
            ):
                fail("p6_base24_status_scope", "raw date/code does not bind target")
    context.check_unchanged()
    identity = {
        "market_date": checked.market_date.isoformat(),
        "core_manifest_sha256": checked.manifest_sha256,
        "core_content_digest": checked.content_digest,
        "package_id": checked.package_id,
        "release_sha256": sha,
        "status_sha256": file_sha(status),
    }
    verified = VerifiedTargetDaily(
        json.dumps(identity, sort_keys=True),
        core_root,
        status,
        release,
        MappingProxyType(before),
        core_files,
    )
    verified.check_unchanged()
    return verified


def qualify_target_state(
    context: Any, *, target: VerifiedTargetDaily, scratch: Path
) -> tuple[Any, dict[str, Any]]:
    """Qualify one actual day; don't requalify execution for every warmup day."""
    from collections import Counter

    import pyarrow as pa

    from quanthus.pipelines.securities_warehouse import base24_p6_handoff as source
    from quanthus.pipelines.securities_warehouse import base24_p6_purpose as purpose
    from quanthus.pipelines.securities_warehouse.base22 import file_sha

    context.check_unchanged()
    target.check_unchanged()
    status = target.status
    target_date = date.fromisoformat(target.identity()["market_date"])
    root = context.root
    manifest = _object(root / "source/manifest.json")
    c = source._connection(scratch)
    try:
        purpose._latest(c, root / "source", manifest["knowledge_cutoff"])
        purpose._dated_status(c, root, manifest["knowledge_cutoff"])
        c.execute("CREATE TEMP TABLE prior_dated AS SELECT * FROM dated_status")
        # Keep all conflicting latest observations; don't choose by filename.
        c.execute(f"""CREATE OR REPLACE VIEW dated_status AS
          WITH raw AS (SELECT DISTINCT stock_code,try_cast(market_date AS DATE) market_date,
            CASE isST WHEN '1' THEN 'ST' WHEN '0' THEN 'NORMAL' ELSE 'UNKNOWN' END status_value,
            CASE tradestatus WHEN '1' THEN 'PROVIDER_TRADING'
              WHEN '0' THEN 'PROVIDER_NOT_TRADING' ELSE 'UNKNOWN' END reason,
            CASE WHEN regexp_matches(acquired_at,'(Z|[+-][0-9]{{2}}:[0-9]{{2}})$')
              THEN try_cast(acquired_at AS TIMESTAMPTZ) END known,
            {source._q(file_sha(status))} evidence FROM read_parquet({source._q(status)})
            WHERE try_cast(market_date AS DATE)=DATE '{target_date}'),
          visible AS (SELECT * FROM prior_dated UNION ALL SELECT * FROM raw
            WHERE known<=TIMESTAMPTZ {source._q(manifest["knowledge_cutoff"])}),
          ranked AS (SELECT *,max(known) OVER(PARTITION BY stock_code,market_date) latest
            FROM visible)
          SELECT DISTINCT stock_code,market_date,status_value,reason,known,evidence
            FROM ranked WHERE known=latest""")
        c.execute(
            "CREATE VIEW raw_ipo AS SELECT * FROM read_parquet("
            + source._q(root / "evidence/ipo-rules.parquet")
            + ")"
        )
        prior = dict(
            c.execute(f"""SELECT dwd_listing_id,
          arg_max(dwd_close_decimal,date) FROM read_parquet(
            {source._q(root / "source/tables/daily_ohlcv_none.parquet")})
          WHERE date<DATE '{target_date}' AND is_tradable AND dwd_close_decimal>0
          GROUP BY dwd_listing_id""").fetchall()
        )
        counts = dict(
            c.execute(f"""SELECT stock_code,count(DISTINCT market_date)
          FROM dated_status WHERE market_date<=DATE '{target_date}'
            AND reason='PROVIDER_TRADING' GROUP BY stock_code""").fetchall()
        )
        policy = _object(root / "contract/price-limit.v2.json")
        rows = purpose._rows(
            c, purpose._market_query(root / "source", target_date, target_date + timedelta(days=1))
        )
        output = [
            purpose._state_row(
                row,
                ordinal=int(counts.get(row["listing_code"], 0)),
                complete=False,
                prior=prior.get(row["listing_id"]),
                policy=policy,
                raw_sha=file_sha(root / "evidence/ipo-rules.parquet"),
            )
            for row in rows
        ]
        # Native actions require an exact event-day reference; no inferred prior-close fill.
        action_securities = {
            v[0]
            for v in c.execute(f"""SELECT security_id FROM
          read_parquet({source._q(root / "qualified/actions.parquet")})
          WHERE action_status='IMPLEMENTED'
          AND coalesce(ex_date,CAST(timezone('Asia/Shanghai',effective_at) AS DATE))
            =DATE '{target_date}'""").fetchall()
        }
        for row in output:
            if row["security_id"] in action_securities:
                row.update(
                    execution_source_qualified=False,
                    qualified_entry_allowed=False,
                    reason_code="ACTION_REFERENCE_REQUIRED",
                )
        result = pa.Table.from_pylist(output, schema=purpose._schemas()[0])
        assessment = {
            "target_rows": len(output),
            "execution_source_qualified": sum(v["execution_source_qualified"] for v in output),
            "entry_allowed": sum(v["qualified_entry_allowed"] for v in output),
            "reasons": dict(sorted(Counter(v["reason_code"] for v in output).items())),
            "provider_acquisition_unavailable_not_inferred": True,
            "official_execution_authorized": False,
        }
        context.check_unchanged()
        target.check_unchanged()
        return result, assessment
    finally:
        c.close()


def materialize_runtime_inputs(
    context: Any,
    *,
    target: VerifiedTargetDaily,
    target_state: Any,
    industry_mapping: Path,
    industry_sha256: str,
    output: Path,
    code_revision: str,
) -> dict[str, Any]:
    """One bounded P6 input window; retain the full target disposition census.

    The minimum consumer bypass holds every implemented action in this feature
    window. An empty normalized-action table is justified only for the retained
    action-free scope, never for the market as a whole. Held rows stay explicit.
    """
    import pyarrow.parquet as pq

    from quanthus.backtest.base24_semantic_projection import KEYS
    from quanthus.industry_mapping import load_static_industry_mapping
    from quanthus.pipelines.securities_warehouse import base24_p6_handoff as source
    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base22_sources import disjoint_output

    context.check_unchanged()
    target.check_unchanged()
    producer_sha = file_sha(Path(__file__))
    target_identity = target.identity()
    if file_sha(industry_mapping) != industry_sha256:
        fail("binding_digest_drift", "original concentration mapping")
    load_static_industry_mapping(
        industry_mapping, expected_version="static-level1-concentration-research-v1"
    )
    output = disjoint_output(
        output,
        [
            context.root,
            industry_mapping.parent,
            target.core_root,
            target.status.parent,
            target.release.parent,
        ],
    )
    if output.exists():
        fail("p6_base24_output_exists", "sealed or failed attempt must be preserved")
    day = date.fromisoformat(target_identity["market_date"])
    output.mkdir(parents=True)
    (output / "tables").mkdir()
    c = source._connection(output)
    try:
        original = context.root / "source/tables"
        for name in KEYS:
            c.execute(
                f"CREATE VIEW s_{name} AS SELECT * FROM read_parquet("
                + source._q(original / (name + ".parquet"))
                + ")"
            )
        dates = [
            r[0]
            for r in c.execute(f"""SELECT DISTINCT trade_date FROM
          s_trading_calendar WHERE is_open=1 AND trade_date<=DATE '{day}'
          ORDER BY trade_date DESC LIMIT 61""").fetchall()
        ][::-1]
        if len(dates) != 61 or dates[-1] != day:
            fail("p6_base24_window", "61 exact published sessions ending at target required")
        start = dates[0]
        c.register("target_state", target_state)
        c.execute(
            "CREATE TABLE industry_ids(stock_code VARCHAR,exchange VARCHAR,industry_id VARCHAR)"
        )
        c.executemany(
            "INSERT INTO industry_ids VALUES (?,?,?)",
            [
                (v["stock_code"], v["exchange"], v["level1_industry_id"])
                for v in _object(industry_mapping)["memberships"]
            ],
        )
        qualifications = ",".join(
            source._q(context.root / p) for p in context.qualification_partitions
        )
        c.execute(f"CREATE VIEW q AS SELECT * FROM read_parquet([{qualifications}])")
        c.execute(f"""CREATE TABLE disposition AS WITH history AS (
          SELECT listing_id,count(*) FILTER(WHERE warmup_eligible) good,
            count(DISTINCT market_date) session_count FROM q
          WHERE market_date BETWEEN DATE '{start}' AND DATE '{day}' GROUP BY listing_id
        ), actions AS (
          SELECT DISTINCT security_id FROM read_parquet(
            {source._q(context.root / "qualified/actions.parquet")})
          WHERE action_status='IMPLEMENTED' AND (effective_at IS NULL OR
            coalesce(ex_date,CAST(timezone('Asia/Shanghai',effective_at) AS DATE))
              BETWEEN DATE '{start}' AND DATE '{day}')
        ) SELECT t.*,i.industry_id,
          coalesce(h.good,0)=61 AND coalesce(h.session_count,0)=61 AS complete_feature_window,
          a.security_id IS NULL AS action_free_window,
          i.stock_code IS NOT NULL AS concentration_identity_qualified,
          CASE WHEN coalesce(h.good,0)<>61 OR coalesce(h.session_count,0)<>61
            THEN 'FEATURE_WINDOW_INCOMPLETE'
          WHEN a.security_id IS NOT NULL THEN 'WINDOW_COMPANY_ACTION_HELD'
          WHEN i.stock_code IS NULL THEN 'CONCENTRATION_IDENTITY_UNAVAILABLE'
          WHEN NOT t.execution_source_qualified THEN t.reason_code
          WHEN NOT t.qualified_entry_allowed THEN 'TARGET_ENTRY_HELD'
          ELSE 'QUALIFIED_CURRENT_INPUT_SCOPE' END AS consumer_reason
          FROM target_state t LEFT JOIN history h USING(listing_id)
          LEFT JOIN s_daily_universe u ON u.dwd_listing_id=t.listing_id
            AND u.dwd_security_id=t.security_id AND u.market_date=DATE '{day}'
          LEFT JOIN industry_ids i ON i.stock_code=u.stock_code AND i.exchange=u.exchange
          LEFT JOIN actions a ON a.security_id=t.security_id""")
        c.execute("""CREATE TABLE selected AS SELECT listing_id,security_id FROM disposition
          WHERE complete_feature_window AND action_free_window
          AND concentration_identity_qualified""")
        if c.execute("""SELECT count(*) FROM (SELECT listing_id FROM disposition
            GROUP BY 1 HAVING count(*)<>1)""").fetchone()[0]:
            fail("p6_base24_concentration_binding", "ambiguous dated code/venue mapping")
        # Candidate age is distinct from the 61-row feature window. Preserve an
        # observed lower bound from the full admitted 2026 listing census.
        c.execute(f"""CREATE TABLE listing_age AS SELECT dwd_listing_id,
          count(DISTINCT market_date) observed_listing_sessions FROM s_daily_universe
          WHERE market_date<=DATE '{day}' GROUP BY dwd_listing_id""")
        counts = dict(
            c.execute("SELECT consumer_reason,count(*) FROM disposition GROUP BY 1").fetchall()
        )
        selected = c.execute("SELECT count(*) FROM selected").fetchone()[0]
        if selected == 0:
            fail("p6_base24_no_qualified_window", "no independent action-free context scope")
        source._copy(
            c, "SELECT * FROM disposition ORDER BY listing_id", output / "admission.parquet"
        )
        for name, date_field in [("daily_ohlcv_none", "date"), ("daily_universe", "market_date")]:
            source._copy(
                c,
                f"""SELECT s.* FROM s_{name} s JOIN selected x
              ON x.listing_id=s.dwd_listing_id WHERE {date_field}
              BETWEEN DATE '{start}' AND DATE '{day}' ORDER BY {date_field},s.dwd_listing_id""",
                output / "tables" / f"{name}.parquet",
            )
        source._copy(
            c,
            f"""SELECT * FROM s_trading_calendar
          WHERE trade_date BETWEEN DATE '{start}' AND DATE '{day}'
          ORDER BY trade_date,exchange""",
            output / "tables/trading_calendar.parquet",
        )
        cutoff = _object(context.root / "source/manifest.json")["knowledge_cutoff"]
        source._copy(
            c,
            f"""SELECT DISTINCT s.* FROM s_security_code_validity s
          JOIN selected x ON x.listing_id=s.dwd_listing_id
          WHERE knowledge_from<=TIMESTAMPTZ {source._q(cutoff)}
          AND coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>TIMESTAMPTZ {source._q(cutoff)}
          AND valid_from<=DATE '{day}' AND coalesce(valid_to,'infinity'::DATE)>=DATE '{start}'
          ORDER BY s.security_id,s.valid_from""",
            output / "tables/security_code_validity.parquet",
        )
        # All retained windows were proven to contain no implemented action.
        # No unknown known_at or old normalization authority is fabricated.
        source._copy(
            c,
            """SELECT security_code,effective_date,known_at,
          cash_dividend_per_share,bonus_share_ratio FROM s_corporate_actions_normalized
          WHERE false""",
            output / "tables/corporate_actions_normalized.parquet",
        )
        source._copy(
            c,
            """SELECT NULL::VARCHAR security_code,NULL::DATE blocked_from,
          NULL::DATE blocked_through WHERE false""",
            output / "tables/corporate_action_exceptions.parquet",
        )
        source._copy(
            c,
            f"""SELECT p.* REPLACE(
          CASE WHEN p.execution_date=DATE '{day}' THEN t.qualified_entry_allowed ELSE false END
            AS entry_policy_allows,
          CASE WHEN p.execution_date=DATE '{day}' THEN t.execution_source_qualified ELSE false END
            AS is_executable,
          CASE WHEN p.execution_date=DATE '{day}' THEN t.qualified_upper_price
            ELSE p.upper_limit_price END AS upper_limit_price,
          CASE WHEN p.execution_date=DATE '{day}' THEN t.qualified_lower_price
            ELSE p.lower_limit_price END AS lower_limit_price,
          CASE WHEN p.execution_date=DATE '{day}' THEN t.status ELSE p.st_status_label END
            AS st_status_label,
          CASE WHEN p.execution_date=DATE '{day}' THEN t.status='ST' ELSE p.is_st_class END
            AS is_st_class,
          p.execution_date=DATE '{day}' AND t.status IS NOT NULL AS status_known,
          p.execution_date=DATE '{day}' AND t.execution_source_qualified AS regime_known,
          CASE WHEN p.execution_date=DATE '{day}' THEN t.in_ipo_window
            ELSE p.in_ipo_window END AS in_ipo_window),
          p.status_known native_status_known,p.regime_known native_regime_known,
          p.upper_limit_price native_upper_limit_price,p.lower_limit_price native_lower_limit_price,
          t.evidence_digest target_qualification_evidence
          FROM s_price_limit_execution_daily p JOIN selected x ON x.listing_id=p.dwd_listing_id
          LEFT JOIN target_state t ON t.listing_id=p.dwd_listing_id
          WHERE p.execution_date BETWEEN DATE '{start}' AND DATE '{day}'
          ORDER BY p.execution_date,p.dwd_listing_id""",
            output / "tables/price_limit_execution_daily.parquet",
        )
        source._copy(
            c,
            f"""SELECT u.dwd_security_id,u.dwd_listing_id,
          u.stock_code listing_code,u.dwd_company_id,u.exchange venue_id,u.dwd_venue_id,
          u.dwd_instrument_type instrument_type,u.market_date session_date,
          a.observed_listing_sessions-((SELECT count(DISTINCT market_date)
            FROM s_daily_universe z WHERE z.dwd_listing_id=u.dwd_listing_id
            AND z.market_date>u.market_date AND z.market_date<=DATE '{day}')) listing_sessions,
          b.dwd_open_decimal open_price,b.dwd_high_decimal high_price,
          b.dwd_low_decimal low_price,b.dwd_close_decimal close_price,
          b.dwd_turnover_decimal turnover,u.is_tradable,
          false adjustment_effective,NULL::TIMESTAMPTZ factor_effective_from,
          NULL::BIGINT factor_boundary_count,b.dwd_bar_knowledge_from,
          u.dwd_security_knowledge_from,u.dwd_listing_knowledge_from,
          u.dwd_state_knowledge_from,
          NULL::TIMESTAMPTZ dwd_factor_knowledge_from,
          b.dwd_bar_source_record_id,b.dwd_bar_source_revision,b.dwd_bar_source_artifact_sha256,
          u.dwd_security_effective_from,u.dwd_listing_effective_from
          FROM read_parquet({source._q(output / "tables/daily_universe.parquet")}) u
          JOIN read_parquet({source._q(output / "tables/daily_ohlcv_none.parquet")}) b
            ON b.dwd_listing_id=u.dwd_listing_id AND b.date=u.market_date
          JOIN listing_age a ON a.dwd_listing_id=u.dwd_listing_id
          ORDER BY u.dwd_security_id,u.dwd_listing_effective_from,u.dwd_listing_id,u.market_date""",
            output / "candidate-input.parquet",
        )
        # Copy the exact original concentration mapping into the portable closure.
        import shutil

        shutil.copyfile(industry_mapping, output / "industry-source.json")
        mapped = _object(industry_mapping)
        mapped["memberships"] = [
            dict(security_id=security, stock_code=code, exchange=exchange, level1_industry_id=level)
            for security, code, exchange, level in c.execute(f"""SELECT x.security_id,u.stock_code,
                u.exchange,d.industry_id
                FROM selected x JOIN disposition d ON d.listing_id=x.listing_id
                JOIN s_daily_universe u ON u.dwd_listing_id=x.listing_id
                AND u.market_date=DATE '{day}' ORDER BY x.security_id""").fetchall()
        ]
        mapped["source"] = {
            **mapped["source"],
            "source_revision": "base24-dated-binding:" + context.identity,
        }
        mapped["artifact_digest"] = digest_value(
            {k: v for k, v in mapped.items() if k != "artifact_digest"}
        )
        source._write(output / "industry.json", mapped)
        mapped_sha = file_sha(output / "industry.json")
        pq.write_table(target_state, output / "target-state.parquet", compression="zstd")
        files = {}
        for p in sorted(output.glob("**/*.parquet")):
            meta = pq.ParquetFile(p)
            files[str(p.relative_to(output))] = {
                "sha256": file_sha(p),
                "bytes": p.stat().st_size,
                "rows": meta.metadata.num_rows,
                "schema_sha256": digest_value(str(meta.schema_arrow)),
            }
        source_manifest = file_sha(context.root / "manifest.json")
        payload = {
            "schema_version": VERSION,
            "code_revision": code_revision,
            "producer_code_sha256": producer_sha,
            "source_manifest_sha256": source_manifest,
            "source_context_identity": context.identity,
            "target": target_identity,
            "market_date": str(day),
            "window_start": str(start),
            "source_knowledge_cutoff": cutoff,
            "source_available_at": _utcnow().isoformat(),
            "minimum_feature_sessions": 61,
            "candidate_history_sessions": 22,
            "source_concentration_mapping_sha256": industry_sha256,
            "concentration_mapping_sha256": mapped_sha,
            "scope_policy": "explicit_per_listing_hold_no_implemented_action_in_consumed_window",
            "all_target_listings": target_state.num_rows,
            "context_listings": int(selected),
            "reason_counts": dict(sorted(counts.items())),
            "qualified_entry_listings": int(counts.get("QUALIFIED_CURRENT_INPUT_SCOPE", 0)),
            "held_context_is_not_forced_sale": True,
            "official_execution_authorized": False,
            "files": files,
            "industry_file": {"path": "industry.json", "sha256": mapped_sha},
        }
        context.check_unchanged()
        target.check_unchanged()
        # DuckDB owns this TMP; close it before sealing and remove only its
        # successfully consumed, job-local spill directory.
        c.close()
        spill = output / "sort-tmp"
        if spill.exists():
            shutil.rmtree(spill)
        if file_sha(Path(__file__)) != producer_sha:
            fail("p6_base24_code_changed", "producer changed during materialization")
        source._write(output / "manifest.json", payload)
        sha = file_sha(output / "manifest.json")
        (output / "READY").write_text(sha + "\n", encoding="ascii")
        return {
            "manifest_sha256": sha,
            **{
                k: payload[k]
                for k in [
                    "all_target_listings",
                    "context_listings",
                    "qualified_entry_listings",
                    "reason_counts",
                    "window_start",
                    "market_date",
                    "source_available_at",
                ]
            },
        }
    finally:
        c.close()


@dataclass(frozen=True)
class P6Base24RuntimeInputs:
    """Portable input-only consumer. Opening it does not enable an official cycle."""

    root: Path
    manifest_sha256: str
    manifest_json: str
    fingerprints: Mapping[str, tuple[Any, ...]]

    def check_unchanged(self) -> None:
        from quanthus.pipelines.securities_warehouse.base22_sources import (
            _fingerprint,
            _tree,
            physical_path,
        )

        if _tree(self.root) != set(self.fingerprints):
            fail("p6_base24_runtime_changed", "physical namespace")
        for rel, before in self.fingerprints.items():
            if _fingerprint(physical_path(self.root / rel)) != before:
                fail("p6_base24_runtime_changed", rel)

    def bundle(self, clock: AfterCloseDecisionClock) -> Any:
        """Adapt the seven physical tables to the existing feature/frame runtime."""
        self.check_unchanged()
        m = json.loads(self.manifest_json)
        if clock.market_date.isoformat() != m[
            "market_date"
        ] or clock.source_available_at != datetime.fromisoformat(m["source_available_at"]):
            fail("p6_base24_clock_binding", "decision does not bind this exact target snapshot")
        return self._bundle(clock.decision_at, clock.identity)

    def context_bundle(self) -> Any:
        """Input-only acceptance at actual availability, without a decision grant."""
        self.check_unchanged()
        m = json.loads(self.manifest_json)
        return self._bundle(
            datetime.fromisoformat(m["source_available_at"]),
            digest_value({"purpose": "read_only_input_acceptance", "input": self.manifest_sha256}),
        )

    def candidate_series(self, clock: AfterCloseDecisionClock) -> Iterator[tuple[Any, ...]]:
        """Stream the admitted current-entry scope into the unchanged numeric rule.

        Native knowledge is retained. The actual materialization availability is
        an additional lower bound, rather than a replacement historical clock.
        """
        import pyarrow.parquet as pq

        from quanthus.event_library.scan import CandidateDailyInput
        from quanthus.pipelines.securities_warehouse.base24_p6_purpose import _digest

        self.bundle(clock)
        allowed = {
            r["listing_id"]
            for r in pq.read_table(self.root / "admission.parquet").to_pylist()
            if r["consumer_reason"] == "QUALIFIED_CURRENT_INPUT_SCOPE"
        }
        members = _object(self.root / "industry.json")["memberships"]
        industry = {r["security_id"]: r["level1_industry_id"] for r in members}
        if len(industry) != len(members):
            fail("p6_base24_concentration_binding", "ambiguous retained membership")
        current = None
        rows = []
        for batch in pq.ParquetFile(self.root / "candidate-input.parquet").iter_batches(
            batch_size=8192
        ):
            for r in batch.to_pylist():
                if r["dwd_listing_id"] not in allowed:
                    continue
                key = r["dwd_security_id"]
                if current is not None and key != current:
                    yield tuple(rows)
                    rows = []
                current = key
                cutoff = max(
                    clock.source_available_at,
                    *(
                        _aware(r[n], n)
                        for n in (
                            "dwd_bar_knowledge_from",
                            "dwd_state_knowledge_from",
                            "dwd_security_knowledge_from",
                            "dwd_listing_knowledge_from",
                        )
                    ),
                )
                if cutoff > clock.decision_at:
                    fail("p6_base24_not_yet_visible", "native candidate evidence")
                day = r["session_date"]
                rows.append(
                    CandidateDailyInput(
                        security_id=key,
                        listing_id=r["dwd_listing_id"],
                        listing_code=r["listing_code"],
                        company_id=r["dwd_company_id"],
                        venue_id=r["venue_id"],
                        instrument_type=r["instrument_type"],
                        static_level1_industry_id=industry[key],
                        session_date=day,
                        close_at=datetime.combine(day, time(15), SHANGHAI),
                        knowledge_cutoff=cutoff,
                        open_price=r["open_price"],
                        high_price=r["high_price"],
                        low_price=r["low_price"],
                        close_price=r["close_price"],
                        turnover=r["turnover"],
                        is_tradable=r["is_tradable"],
                        listing_sessions=r["listing_sessions"],
                        adjustment_effective=r["adjustment_effective"],
                        source_evidence_digest=_digest(r),
                    )
                )
        if rows:
            yield tuple(rows)
        self.check_unchanged()

    def materialize_feature_panel(
        self, *, registry: Any, output: Path, clock: AfterCloseDecisionClock | None = None
    ) -> Any:
        """Use the actual P6 feature engine once; no strategy/order is evaluated."""
        from quanthus.backtest.baseline_research import BaselineFeaturePanel
        from quanthus.pipelines.securities_warehouse.base22_sources import disjoint_output

        self.check_unchanged()
        output = disjoint_output(output, [self.root])
        if output.exists():
            fail("p6_base24_output_exists", "feature TMP already exists")
        m = json.loads(self.manifest_json)
        bundle = self.context_bundle() if clock is None else self.bundle(clock)
        artifact = BaselineFeaturePanel.materialize(
            bundle,
            registry,
            scope_start=date.fromisoformat(m["window_start"]),
            scope_end=date.fromisoformat(m["market_date"]),
            output=output,
            data_content_digest=bundle.digest,
            data_manifest_sha256=self.manifest_sha256,
            output_order="security-date",
        )
        self.check_unchanged()
        return artifact

    def _bundle(self, cutoff: datetime, clock_identity: str) -> Any:
        from quanthus.backtest.base24_semantic_projection import KEYS
        from quanthus.backtest.historical_bundle import HistoricalReplayBundle
        from quanthus.contracts import SnapshotRef
        from quanthus.data_foundation import DatasetManifest

        m = json.loads(self.manifest_json)
        datasets = tuple(
            DatasetManifest(
                dataset=name,
                version=VERSION,
                source_revisions={"runtime_inputs": self.manifest_sha256, "clock": clock_identity},
                row_count=m["files"][f"tables/{name}.parquet"]["rows"],
                partitions=(f"tables/{name}.parquet",),
                schema_hash=m["files"][f"tables/{name}.parquet"]["schema_sha256"],
                content_checksums={
                    f"tables/{name}.parquet": m["files"][f"tables/{name}.parquet"]["sha256"]
                },
                created_at=datetime.fromisoformat(m["source_available_at"]),
                knowledge_cutoff=cutoff,
            )
            for name in KEYS
        )
        return HistoricalReplayBundle(
            root=self.root,
            snapshot=SnapshotRef(
                market_date=date.fromisoformat(m["market_date"]),
                knowledge_cutoff=cutoff,
                source_revision=self.manifest_sha256,
                snapshot_id="p6-base24-"
                + digest_value(
                    {"input": self.manifest_sha256, "clock": clock_identity}
                ).removeprefix("sha256:")[:24],
            ),
            universe_policy_version=VERSION,
            datasets=datasets,
        )


def open_runtime_inputs(root: Path, *, manifest_sha256: str) -> P6Base24RuntimeInputs:
    """Full bounded offline acceptance; no source root or network dereference."""
    from tempfile import TemporaryDirectory

    import pyarrow.parquet as pq

    from quanthus.backtest.base24_semantic_projection import KEYS
    from quanthus.pipelines.securities_warehouse import base24_p6_handoff as source
    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base22_sources import (
        _fingerprint,
        _tree,
        physical_path,
    )

    root = physical_path(root)
    before = {p: _fingerprint(root / p) for p in _tree(root)}
    if (
        file_sha(root / "manifest.json") != manifest_sha256
        or (root / "READY").read_text(encoding="ascii").strip() != manifest_sha256
    ):
        fail("p6_base24_runtime_identity", "manifest or READY differs")
    m = _object(root / "manifest.json")
    required = {f"tables/{n}.parquet" for n in KEYS} | {
        "candidate-input.parquet",
        "target-state.parquet",
        "admission.parquet",
    }
    if (
        m.get("schema_version") != VERSION
        or set(m.get("files", {})) != required
        or m.get("official_execution_authorized") is not False
        or m.get("minimum_feature_sessions") != 61
        or m.get("candidate_history_sessions") != 22
        or m.get("scope_policy")
        != "explicit_per_listing_hold_no_implemented_action_in_consumed_window"
        or m.get("held_context_is_not_forced_sale") is not True
        or set(before)
        != required | {"manifest.json", "READY", "industry.json", "industry-source.json"}
    ):
        fail("p6_base24_runtime_contract", "exact physical closure and scope required")
    if file_sha(root / "industry.json") != m["concentration_mapping_sha256"]:
        fail("p6_base24_runtime_identity", "concentration mapping differs")
    if file_sha(root / "industry-source.json") != m["source_concentration_mapping_sha256"]:
        fail("p6_base24_runtime_identity", "original mapping differs")
    from quanthus.industry_mapping import load_static_industry_mapping

    for name in ("industry.json", "industry-source.json"):
        load_static_industry_mapping(
            root / name, expected_version="static-level1-concentration-research-v1"
        )
    original = _object(root / "industry-source.json")
    native = _object(root / "industry.json")
    index = {
        (r["stock_code"], r["exchange"]): r["level1_industry_id"] for r in original["memberships"]
    }
    if (
        len(index) != len(original["memberships"])
        or any(
            index.get((r["stock_code"], r["exchange"])) != r["level1_industry_id"]
            for r in native["memberships"]
        )
        or native["source"]["retrieved_at"] != original["source"]["retrieved_at"]
    ):
        fail("p6_base24_concentration_binding", "original membership or source clock changed")
    for rel in sorted(required):
        p = physical_path(root / rel)
        f = m["files"][rel]
        if file_sha(p) != f["sha256"] or p.stat().st_size != f["bytes"]:
            fail("p6_base24_runtime_member", rel)
        metadata = pq.ParquetFile(p)
        if rel == "tables/daily_ohlcv_none.parquet":
            for name in ("open", "high", "low", "close", "turnover", "volume"):
                field = "dwd_" + name + "_decimal"
                if str(metadata.schema_arrow.field(field).type) != "decimal128(38, 10)":
                    fail("p6_base24_runtime_semantics", "exact Decimal schema")
        if (
            metadata.metadata.num_rows != f["rows"]
            or digest_value(str(metadata.schema_arrow)) != f["schema_sha256"]
        ):
            fail("p6_base24_runtime_member", rel)
    day = date.fromisoformat(m["market_date"])
    start = date.fromisoformat(m["window_start"])
    if start < date(2026, 1, 1) or day < start:
        fail("p6_base24_runtime_scope", "2026 window required")
    available = _aware(datetime.fromisoformat(m["source_available_at"]), "source_available_at")
    cutoff = _aware(datetime.fromisoformat(m["source_knowledge_cutoff"]), "source_knowledge_cutoff")
    if available < cutoff:
        fail("p6_base24_runtime_scope", "materialization backdates source availability")
    # Spill is outside the immutable image; no verification cache is an input.
    with TemporaryDirectory(prefix="p6-runtime-cold-") as directory:
        c = source._connection(Path(directory))
        try:
            for n in KEYS:
                c.execute(
                    f"CREATE VIEW {n} AS SELECT * FROM read_parquet("
                    + source._q(root / f"tables/{n}.parquet")
                    + ")"
                )
            for n, rel in [
                ("a", "admission.parquet"),
                ("t", "target-state.parquet"),
                ("candidate", "candidate-input.parquet"),
            ]:
                c.execute(
                    f"CREATE VIEW {n} AS SELECT * FROM read_parquet(" + source._q(root / rel) + ")"
                )
            c.execute(
                "CREATE TABLE mapped(security_id VARCHAR,stock_code VARCHAR,exchange VARCHAR)"
            )
            c.executemany(
                "INSERT INTO mapped VALUES (?,?,?)",
                [(r["security_id"], r["stock_code"], r["exchange"]) for r in native["memberships"]],
            )
            checks = {
                "target_census": f"""SELECT count(*) FROM t
                    WHERE market_date IS DISTINCT FROM DATE '{day}'
                    OR execution_authorized OR execution_authorized IS NULL
                    OR listing_id IS NULL""",
                "target_unique": """SELECT count(*) FROM (SELECT listing_id FROM t
                    GROUP BY 1 HAVING count(*)<>1)""",
                "admission_census": """SELECT count(*) FROM
                    ((SELECT listing_id FROM a EXCEPT SELECT listing_id FROM t)
                    UNION ALL (SELECT listing_id FROM t EXCEPT SELECT listing_id FROM a))""",
                "admission_unique": """SELECT count(*) FROM (SELECT listing_id FROM a
                    GROUP BY 1 HAVING count(*)<>1)""",
                "calendar": f"""SELECT CASE WHEN count(DISTINCT trade_date)=61
                    AND min(trade_date)=DATE '{start}' AND max(trade_date)=DATE '{day}'
                    THEN 0 ELSE 1 END FROM trading_calendar WHERE is_open=1""",
                "empty_actions": """SELECT (SELECT count(*) FROM corporate_actions_normalized)
                    +(SELECT count(*) FROM corporate_action_exceptions)""",
            }
            for n, field in [
                ("daily_ohlcv_none", "date"),
                ("daily_universe", "market_date"),
                ("price_limit_execution_daily", "execution_date"),
            ]:
                checks[n + "_keys"] = f"""SELECT count(*)
                    FROM (SELECT security_id,{field}
                    FROM {n} GROUP BY ALL
                    HAVING count(*)<>1)"""
                checks[n + "_scope"] = (
                    f"SELECT count(*) FROM {n} WHERE {field}<DATE '{start}' OR {field}>DATE '{day}'"
                )
                checks[n + "_window"] = f"""SELECT count(*)
                    FROM (SELECT dwd_listing_id
                    FROM {n} GROUP BY 1
                    HAVING count(*)<>61 OR count(DISTINCT {field})<>61)"""
                checks[n + "_selection"] = f"""SELECT count(*) FROM {n} b LEFT JOIN a
                  ON b.dwd_listing_id=a.listing_id WHERE a.listing_id IS NULL
                  OR NOT a.complete_feature_window OR NOT a.action_free_window
                  OR NOT a.concentration_identity_qualified"""
            checks["entry_flags"] = f"""SELECT count(*) FROM price_limit_execution_daily e
              JOIN t ON t.listing_id=e.dwd_listing_id WHERE
              (e.execution_date<>DATE '{day}' AND (e.entry_policy_allows OR e.is_executable))
              OR (e.execution_date=DATE '{day}' AND
                (e.entry_policy_allows IS DISTINCT FROM t.qualified_entry_allowed
                 OR e.is_executable IS DISTINCT FROM t.execution_source_qualified
                 OR e.upper_limit_price IS DISTINCT FROM t.qualified_upper_price
                 OR e.lower_limit_price IS DISTINCT FROM t.qualified_lower_price))"""
            checks["candidate_window"] = """SELECT count(*) FROM (SELECT dwd_listing_id
              FROM candidate GROUP BY 1 HAVING count(*)<>61 OR count(DISTINCT session_date)<>61)"""
            checks["candidate_age"] = """SELECT count(*)
                    FROM candidate
                    WHERE listing_sessions<1 OR listing_sessions IS NULL OR adjustment_effective"""
            checks["calendar_keys"] = """SELECT count(*) FROM (SELECT calendar_session_id
                FROM trading_calendar GROUP BY 1 HAVING count(*)<>1)"""
            checks["bar_calendar"] = """SELECT count(*) FROM daily_ohlcv_none b
                LEFT JOIN trading_calendar c USING(calendar_session_id)
                WHERE c.calendar_session_id IS NULL OR b.date IS DISTINCT FROM c.trade_date
                OR b.dwd_venue_id IS DISTINCT FROM c.venue_id
                OR b.identity_exchange IS DISTINCT FROM c.exchange OR c.is_open<>1"""
            checks["bar_universe"] = """SELECT count(*) FROM daily_ohlcv_none b
                FULL JOIN daily_universe u ON b.dwd_listing_id=u.dwd_listing_id
                AND b.date=u.market_date WHERE b.dwd_listing_id IS NULL
                OR u.dwd_listing_id IS NULL OR b.dwd_security_id IS DISTINCT FROM u.dwd_security_id
                OR b.dwd_venue_id IS DISTINCT FROM u.dwd_venue_id
                OR b.security_id IS DISTINCT FROM u.security_id"""
            checks["bar_execution"] = """SELECT count(*) FROM daily_ohlcv_none b
                FULL JOIN price_limit_execution_daily e ON b.dwd_listing_id=e.dwd_listing_id
                AND b.date=e.execution_date WHERE b.dwd_listing_id IS NULL
                OR e.dwd_listing_id IS NULL OR b.dwd_security_id IS DISTINCT FROM e.dwd_security_id
                OR b.dwd_venue_id IS DISTINCT FROM e.dwd_venue_id"""
            checks["candidate_values"] = """SELECT count(*) FROM candidate v
                FULL JOIN daily_ohlcv_none b ON v.dwd_listing_id=b.dwd_listing_id
                AND v.session_date=b.date WHERE v.dwd_listing_id IS NULL
                OR b.dwd_listing_id IS NULL OR v.dwd_security_id IS DISTINCT FROM b.dwd_security_id
                OR v.venue_id IS DISTINCT FROM b.identity_exchange
                OR v.dwd_venue_id IS DISTINCT FROM b.dwd_venue_id
                OR v.open_price IS DISTINCT FROM b.dwd_open_decimal
                OR v.high_price IS DISTINCT FROM b.dwd_high_decimal
                OR v.low_price IS DISTINCT FROM b.dwd_low_decimal
                OR v.close_price IS DISTINCT FROM b.dwd_close_decimal
                OR v.turnover IS DISTINCT FROM b.dwd_turnover_decimal
                OR v.dwd_bar_knowledge_from IS DISTINCT FROM b.dwd_bar_knowledge_from"""
            checks["concentration_ids"] = f"""SELECT count(*) FROM mapped m
                FULL JOIN (SELECT DISTINCT dwd_security_id,stock_code,exchange
                    FROM daily_universe WHERE market_date=DATE '{day}') u
                ON m.security_id=u.dwd_security_id AND m.stock_code=u.stock_code
                AND m.exchange=u.exchange WHERE m.security_id IS NULL
                OR u.dwd_security_id IS NULL"""
            checks["bar_knowledge"] = f"""SELECT count(*) FROM daily_ohlcv_none
                WHERE dwd_bar_knowledge_from IS NULL
                OR dwd_bar_knowledge_from>TIMESTAMPTZ {source._q(cutoff.isoformat())}
                OR coalesce(dwd_bar_knowledge_to,'infinity'::TIMESTAMPTZ)
                    <=TIMESTAMPTZ {source._q(cutoff.isoformat())}"""
            for name, query in checks.items():
                if c.execute(query).fetchone()[0]:
                    fail("p6_base24_runtime_semantics", name)
            census = c.execute(
                """SELECT count(*),count(*) FILTER(WHERE complete_feature_window
                    AND action_free_window AND concentration_identity_qualified)
                    FROM a"""
            ).fetchone()
            if census != (m["all_target_listings"], m["context_listings"]):
                fail("p6_base24_runtime_semantics", "scope counts")
            reasons = dict(
                c.execute("SELECT consumer_reason,count(*) FROM a GROUP BY 1").fetchall()
            )
            if (
                reasons != m["reason_counts"]
                or reasons.get("QUALIFIED_CURRENT_INPUT_SCOPE", 0) != m["qualified_entry_listings"]
            ):
                fail("p6_base24_runtime_semantics", "reason counts")
        finally:
            c.close()
    result = P6Base24RuntimeInputs(
        root, manifest_sha256, json.dumps(m, sort_keys=True), MappingProxyType(before)
    )
    result.check_unchanged()
    return result
