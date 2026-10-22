"""P6 fixed-snapshot mechanism replay, isolated from the official controller.

One immutable 2026 history, panel, candidate scan and frame reader serve every
date. The actual knowledge clock stays later than the simulated market clock.
This is a counterfactual engineering fixture, never historical PIT/economics.
"""

from __future__ import annotations

import importlib
import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from quanthus.backtest.baseline_research import BaselineFeaturePanel
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.backtest.snapshot_clock import SnapshotReplayClock
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.event_library.contracts import SplitAssignment
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import CandidateDailyInput, scan_forward_candidate_series
from quanthus.feature_registry import load_feature_registry
from quanthus.forward_validation import official_cycle as engine
from quanthus.forward_validation.base24_cycle import load_runtime
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.historical_admission import (
    bind_calendar,
    check_unchanged,
    verify_source,
)
from quanthus.forward_validation.paper_ledger import load_paper_policy, reconcile_paper_ledger
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    _fingerprint,
    _tree,
    disjoint_output,
    physical_path,
)

VERSION = "quanthus.p6-fixed-snapshot-replay.v1"
TABLES = (
    "daily_ohlcv_none",
    "daily_universe",
    "price_limit_execution_daily",
    "security_code_validity",
    "trading_calendar",
    "corporate_actions_normalized",
    "corporate_action_exceptions",
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        fail("p6_snapshot_object", path.name)
    return value


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")


def _q(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def load_plan(repository: Path, config: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = _read(config)
    # This explicit successor does not edit the frozen strict-PIT plan or any
    # prospective source-purpose/activation flag.
    reference = repository / "config/forward-validation/runtime/p6-fixed-snapshot-replay.v1.json"
    if (
        raw != _read(reference)
        or raw["schema_version"] != VERSION
        or raw["mode"] != "fixed_snapshot_mechanism_replay"
        or raw["claims"]
        != {
            "mechanism_test": True,
            "historical_pit": False,
            "economic_validation": False,
            "official_prospective_observation": False,
            "production_admission": False,
        }
        or any(raw["authority"].values())
    ):
        fail("p6_snapshot_plan", "versioned mechanism contract differs")
    plan_path = repository / raw["window_plan"]["path"]
    if file_sha(plan_path) != raw["window_plan"]["sha256"]:
        fail("p6_snapshot_plan", "frozen dates differ")
    return raw, _read(plan_path)


@dataclass(frozen=True)
class SnapshotScanPolicy:
    history_start: date
    decision_start: date
    decision_end: date
    history_end: date
    snapshot_replay_cutoff: datetime
    semantic_digest: str
    policy_id: str = "p6-fixed-snapshot-mechanism-split"
    policy_version: str = "1.0.0"

    def require_stage_b_authority(self) -> None:
        SnapshotReplayClock(self.decision_start, self.snapshot_replay_cutoff)

    def require_scan_date(self, value: date) -> None:
        if not date(2026, 1, 1) <= self.history_start <= value <= self.history_end:
            fail("p6_snapshot_scope", str(value))

    def candidate_signal_date_allowed(self, value: date) -> bool:
        return self.decision_start <= value <= self.decision_end

    def assign_event_split(
        self,
        *,
        signal_date: date,
        feature_start: date,
        outcome_end: date,
        trading_dates: Sequence[date],
    ) -> SplitAssignment:
        for day in (signal_date, feature_start, outcome_end):
            self.require_scan_date(day)
            if day not in trading_dates:
                fail("p6_snapshot_scope", "event not in published sessions")
        if (
            not self.candidate_signal_date_allowed(signal_date)
            or not feature_start <= signal_date < outcome_end
        ):
            fail("p6_snapshot_scope", "signal outside fixed decisions")
        return SplitAssignment.SNAPSHOT_REPLAY


class SnapshotFrameReader(HistoricalReplayFrameReader):
    """Keep the real knowledge clock on every P1 evidence snapshot.

    The inherited regular-session coordinates are simulation assumptions. They
    are not inserted into the native calendar or claimed as recovered evidence.
    """

    def _create_views(self) -> None:
        self._ensure_open().execute("SET memory_limit='2GB'")
        super()._create_views()

    def _snapshot(self, market_date: date, *, role: str) -> SnapshotRef:
        SnapshotReplayClock(market_date, self.bundle.snapshot.knowledge_cutoff)
        return replace(
            self.bundle.snapshot,
            market_date=market_date,
            snapshot_id=self.bundle.snapshot.snapshot_id + f".{role}.{market_date}",
        )

    def prefetch(self, codes: Sequence[str], day: date) -> None:
        # One bounded daily join supplies pending, position and target consumers.
        self._day_evidence = self.execution_evidence_batch([(c, day) for c in sorted(set(codes))])

    def execution_evidence(self, security_code: str, execution_date: date) -> Any:
        cache = getattr(self, "_day_evidence", {})
        key = (security_code, execution_date)
        if key not in cache:
            cache[key] = super().execution_evidence(security_code, execution_date)
        return cache[key]


@dataclass
class SharedSnapshotReplay:
    """In-Job reuse only; independent reproduction constructs a fresh instance."""

    binding: str | None = None
    reader: Any = None
    panel: Any = None
    bundle: Any = None
    artifact: Any = None
    by_day: Any = None
    counters: Any = None
    fingerprints: Any = None
    owner_root: Path | None = None

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
        if self.panel is not None:
            self.panel.close()

    @contextmanager
    def lease(self, *, owned: bool) -> Iterator[tuple[Any, Any]]:
        try:
            yield self.reader, self.panel
        finally:
            if owned:
                self.close()


def _bundle(root: Path, manifest: Mapping[str, Any], market_date: date) -> HistoricalReplayBundle:
    cutoff = datetime.fromisoformat(manifest["knowledge_cutoff"])
    datasets = tuple(
        DatasetManifest(
            dataset=name,
            version=VERSION,
            source_revisions={"snapshot": manifest["input_identity"]},
            row_count=manifest["files"][f"tables/{name}.parquet"]["rows"],
            partitions=(f"tables/{name}.parquet",),
            schema_hash=manifest["files"][f"tables/{name}.parquet"]["schema_sha256"],
            content_checksums={
                f"tables/{name}.parquet": manifest["files"][f"tables/{name}.parquet"]["sha256"]
            },
            created_at=cutoff,
            knowledge_cutoff=cutoff,
        )
        for name in TABLES
    )
    return HistoricalReplayBundle(
        root,
        SnapshotRef(
            market_date,
            cutoff,
            manifest["input_identity"],
            "p6-snapshot-" + manifest["input_identity"][7:31],
        ),
        VERSION,
        datasets,
    )


def prepare_history(
    *,
    source: Path,
    output: Path,
    repository: Path,
    config: Path,
    industry: Path,
    industry_sha256: str,
    knowledge_cutoff: datetime,
    code_revision: str,
) -> dict[str, Any]:
    """Materialize one job-local action-free fixture, preserving native fields."""
    import duckdb

    pq = importlib.import_module("pyarrow.parquet")

    from quanthus.industry_mapping import load_static_industry_mapping

    raw, plan = load_plan(repository, config)
    if re.fullmatch("[0-9a-f]{40}", code_revision) is None:
        fail("p6_snapshot_code", "full local revision required")
    SnapshotReplayClock(date.fromisoformat(plan["scope"]["end"]), knowledge_cutoff)
    source, industry = physical_path(source), physical_path(industry)
    output = disjoint_output(output, [source, repository, industry.parent])
    if output.exists():
        fail("p6_snapshot_output_exists", "preserve every sealed/failed attempt")
    if file_sha(industry) != industry_sha256:
        fail("p6_snapshot_industry", "frozen static concentration mapping differs")
    load_static_industry_mapping(
        industry, expected_version="static-level1-concentration-research-v1"
    )
    m, fingerprints = verify_source(source, plan)
    if knowledge_cutoff < max(
        datetime.fromisoformat(m["built_at"]),
        datetime.fromisoformat(_read(source / "source/manifest.json")["knowledge_cutoff"]),
    ):
        fail("p6_snapshot_clock", "input not yet available at actual knowledge cutoff")
    windows = bind_calendar(
        plan, pq.read_table(source / plan["source"]["calendar_member"]).to_pylist()
    )
    policy_path = repository / plan["policy_reference"]["path"]
    if file_sha(policy_path) != plan["policy_reference"]["sha256"]:
        fail("p6_snapshot_policy", "frozen runtime binding differs")
    runtime = load_runtime(policy_path, root=repository)
    policy_hashes = {name: file_sha(path) for name, path in runtime.bindings.items()}
    closure = {
        "source_manifest": plan["source"]["manifest_sha256"],
        "mode_config": file_sha(config),
        "window_plan": raw["window_plan"]["sha256"],
        "industry": industry_sha256,
        "knowledge_cutoff": knowledge_cutoff.isoformat(),
        "code_revision": code_revision,
        "policy_hashes": policy_hashes,
    }
    identity = digest_value(closure)
    output.mkdir(parents=True)
    (output / "tables").mkdir()
    c: Any = duckdb.connect()
    c.execute("SET memory_limit='2GB'")
    c.execute("SET threads=2")
    c.execute("SET TimeZone='Asia/Shanghai'")
    c.execute("SET autoinstall_known_extensions=false")
    c.execute("SET autoload_known_extensions=false")
    c.execute("SET temp_directory=" + _q(output / "sort-tmp"))
    c.execute("SET max_temp_directory_size='2GB'")
    start, end = plan["scope"]["start"], plan["scope"]["end"]
    started = time.monotonic()
    try:
        for name in TABLES:
            c.execute(
                f"CREATE VIEW s_{name} AS SELECT * FROM read_parquet("
                f"{_q(source / 'source/tables' / (name + '.parquet'))})"
            )
        qpaths = [
            source / rel
            for rel in m["files"]
            if rel.startswith("qualified/") and rel.endswith("/execution.parquet")
        ]
        c.execute(
            "CREATE VIEW q AS SELECT * FROM read_parquet([" + ",".join(_q(p) for p in qpaths) + "])"
        )
        if c.execute(
            "SELECT count(*) FROM (SELECT listing_id,market_date FROM q "
            "GROUP BY 1,2 HAVING count(*)<>1)"
        ).fetchone()[0]:
            fail("p6_snapshot_duplicate", "qualification listing/date")
        c.execute("CREATE TABLE industry(stock_code VARCHAR,exchange VARCHAR,industry_id VARCHAR)")
        c.executemany(
            "INSERT INTO industry VALUES (?,?,?)",
            [
                (v["stock_code"], v["exchange"], v["level1_industry_id"])
                for v in _read(industry)["memberships"]
            ],
        )
        c.execute(f"""CREATE TABLE selected AS SELECT DISTINCT u.dwd_listing_id,u.dwd_security_id,
          u.stock_code,u.exchange,i.industry_id
          FROM s_daily_universe u JOIN industry i USING(stock_code,exchange)
          WHERE u.market_date BETWEEN DATE '{start}' AND DATE '{end}' AND NOT EXISTS (
            SELECT 1 FROM read_parquet({_q(source / "qualified/actions.parquet")}) a
            WHERE a.security_id=u.dwd_security_id AND a.action_status='IMPLEMENTED'
              AND (a.effective_at IS NULL OR coalesce(a.ex_date,
                CAST(timezone('Asia/Shanghai',a.effective_at) AS DATE))
                BETWEEN DATE '{start}' AND DATE '{end}'))""")
        if c.execute(
            "SELECT count(*) FROM (SELECT dwd_listing_id FROM selected "
            "GROUP BY 1 HAVING count(*)<>1)"
        ).fetchone()[0]:
            fail("p6_snapshot_identity", "ambiguous dated concentration/listing")

        def copy(query: str, relative: str) -> None:
            c.execute(
                f"COPY ({query}) TO {_q(output / relative)} "
                "(FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 50000)"
            )

        for name, field in (("daily_ohlcv_none", "date"), ("daily_universe", "market_date")):
            copy(
                f"SELECT s.* FROM s_{name} s JOIN selected x USING(dwd_listing_id) "
                f"WHERE {field} BETWEEN DATE '{start}' AND DATE '{end}' "
                f"ORDER BY {field},s.dwd_listing_id",
                f"tables/{name}.parquet",
            )
        copy(
            f"SELECT * FROM s_trading_calendar WHERE trade_date "
            f"BETWEEN DATE '{start}' AND DATE '{end}' ORDER BY trade_date,exchange",
            "tables/trading_calendar.parquet",
        )
        copy(
            "SELECT s.* FROM s_security_code_validity s WHERE EXISTS "
            "(SELECT 1 FROM selected x WHERE x.dwd_listing_id=s.dwd_listing_id) "
            "ORDER BY s.security_id,s.valid_from",
            "tables/security_code_validity.parquet",
        )
        copy(
            """SELECT NULL::VARCHAR action_id,NULL::VARCHAR security_code,NULL::DATE effective_date,
             NULL::TIMESTAMPTZ known_at,NULL::DECIMAL(38,10) cash_dividend_per_share,
             NULL::DECIMAL(38,18) bonus_share_ratio,NULL::VARCHAR source_revision,
             NULL::VARCHAR source_snapshot_id,NULL::VARCHAR evidence_digest,
             false execution_authorized WHERE false""",
            "tables/corporate_actions_normalized.parquet",
        )
        copy(
            """SELECT NULL::VARCHAR exception_id,NULL::VARCHAR security_code,
             NULL::DATE blocked_from,
             NULL::DATE blocked_through,NULL::VARCHAR reason_code,NULL::VARCHAR replay_disposition,
             false execution_authorized WHERE false""",
            "tables/corporate_action_exceptions.parquet",
        )
        copy(
            f"""SELECT p.* REPLACE(
          coalesce(q.qualified_entry_allowed,false) AS entry_policy_allows,
          coalesce(q.execution_source_qualified,false) AS is_executable,
          q.qualified_upper_price AS upper_limit_price,q.qualified_lower_price AS lower_limit_price,
          q.status AS st_status_label,q.status='ST' AS is_st_class,
          q.status IS NOT NULL AS status_known,
          coalesce(q.execution_source_qualified,false) AS regime_known,
          coalesce(q.in_ipo_window,false) AS in_ipo_window),
          p.upper_limit_price native_upper_limit_price,p.lower_limit_price native_lower_limit_price,
          p.status_known native_status_known,p.regime_known native_regime_known,
          q.reason_code qualification_reason,q.evidence_digest qualification_evidence
          FROM s_price_limit_execution_daily p JOIN selected x USING(dwd_listing_id)
          LEFT JOIN q ON q.listing_id=p.dwd_listing_id AND q.market_date=p.execution_date
          WHERE p.execution_date BETWEEN DATE '{start}' AND DATE '{end}'
          ORDER BY p.execution_date,p.dwd_listing_id""",
            "tables/price_limit_execution_daily.parquet",
        )
        # Native numbers/times remain intact; original unknowns explicitly hold
        # affected candidate rows. Counterfactual intake is an additional clock.
        copy(
            f"""SELECT b.*,
          row_number() OVER(PARTITION BY u.dwd_listing_id ORDER BY u.market_date) listing_sessions,
          x.industry_id,coalesce(q.warmup_eligible,false) warmup_eligible,
          b.dwd_bar_knowledge_from IS NOT NULL AND b.dwd_bar_knowledge_from
            <=TIMESTAMPTZ {_q(knowledge_cutoff.isoformat())}
          AND (b.dwd_bar_knowledge_to IS NULL OR b.dwd_bar_knowledge_to
            >TIMESTAMPTZ {_q(knowledge_cutoff.isoformat())})
          AND u.dwd_security_knowledge_from IS NOT NULL AND u.dwd_security_knowledge_from
            <=TIMESTAMPTZ {_q(knowledge_cutoff.isoformat())}
          AND u.dwd_listing_knowledge_from IS NOT NULL AND u.dwd_listing_knowledge_from
            <=TIMESTAMPTZ {_q(knowledge_cutoff.isoformat())}
          AND u.dwd_state_knowledge_from IS NOT NULL AND u.dwd_state_knowledge_from
            <=TIMESTAMPTZ {_q(knowledge_cutoff.isoformat())}
          AS native_knowledge_qualified
          FROM s_daily_ohlcv_none b JOIN selected x USING(dwd_listing_id)
          JOIN s_daily_universe u ON u.dwd_listing_id=b.dwd_listing_id AND u.market_date=b.date
          LEFT JOIN q ON q.listing_id=b.dwd_listing_id AND q.market_date=b.date
          WHERE b.date BETWEEN DATE '{start}' AND DATE '{end}' ORDER BY b.dwd_security_id,b.date""",
            "candidate-input.parquet",
        )
        census = {
            "selected_listings": c.execute("SELECT count(*) FROM selected").fetchone()[0],
            "all_listings": c.execute(
                "SELECT count(DISTINCT dwd_listing_id) FROM s_daily_universe"
            ).fetchone()[0],
        }
        census["candidate_native_clock_held_rows"] = c.execute(
            f"SELECT count(*) FROM read_parquet({_q(output / 'candidate-input.parquet')}) "
            "WHERE NOT native_knowledge_qualified"
        ).fetchone()[0]
        _write(
            output / "selected-cohort.json",
            {
                "members": c.execute("SELECT * FROM selected ORDER BY dwd_listing_id").fetchall(),
                "purpose": "hindsight_engineering_fixture_not_market_representative",
            },
        )
    finally:
        c.close()
    check_unchanged(source, fingerprints)
    if file_sha(industry) != industry_sha256:
        fail("p6_snapshot_changed", "industry source during materialization")
    files: dict[str, Any] = {}
    for relative in sorted(_tree(output)):
        p = output / relative
        member: dict[str, Any] = {"sha256": file_sha(p), "bytes": p.stat().st_size}
        if p.suffix == ".parquet":
            f = pq.ParquetFile(p)
            member.update(rows=f.metadata.num_rows, schema_sha256=digest_value(str(f.schema_arrow)))
        files[relative] = member
    result = {
        "schema_version": VERSION,
        "input_identity": identity,
        "closure": closure,
        "knowledge_cutoff": knowledge_cutoff.isoformat(),
        "windows": windows,
        "claims": raw["claims"],
        "census": census,
        "files": files,
        "elapsed_seconds": str(time.monotonic() - started),
        "source_unchanged": True,
        "physical_history_build_count": 1,
    }
    _write(output / "manifest.json", result)
    (output / "READY").write_text(file_sha(output / "manifest.json") + "\n")
    return result


def open_history(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = physical_path(root)
    m = _read(root / "manifest.json")
    if (
        m["schema_version"] != VERSION
        or (root / "READY").read_text().strip() != file_sha(root / "manifest.json")
        or set(_tree(root)) != set(m["files"]) | {"manifest.json", "READY"}
    ):
        fail("p6_snapshot_physical", "history manifest/namespace")
    before = {rel: _fingerprint(root / rel) for rel in _tree(root)}
    for rel, member in m["files"].items():
        if file_sha(root / rel) != member["sha256"]:
            fail("p6_snapshot_physical", rel)
    check_unchanged(root, before)
    return m, before


def candidate_series(root: Path, cutoff: datetime) -> Iterator[tuple[CandidateDailyInput, ...]]:
    pq = importlib.import_module("pyarrow.parquet")
    current, rows = None, []
    for batch in pq.ParquetFile(root / "candidate-input.parquet").iter_batches(batch_size=4096):
        for r in batch.to_pylist():
            key = r["dwd_security_id"]
            if current is not None and current != key:
                yield tuple(rows)
                rows = []
            current = key
            if not r["native_knowledge_qualified"]:
                continue
            rows.append(
                CandidateDailyInput(
                    security_id=key,
                    listing_id=r["dwd_listing_id"],
                    listing_code=r["stock_code"],
                    company_id=r["dwd_company_id"],
                    venue_id=r["identity_exchange"],
                    instrument_type=(
                        "common_equity"
                        if r["dwd_instrument_type"] == "A_SHARE"
                        else r["dwd_instrument_type"]
                    ),
                    static_level1_industry_id=r["industry_id"],
                    session_date=r["date"],
                    close_at=SnapshotReplayClock(r["date"], cutoff).simulated_close,
                    knowledge_cutoff=cutoff,
                    open_price=r["dwd_open_decimal"],
                    high_price=r["dwd_high_decimal"],
                    low_price=r["dwd_low_decimal"],
                    close_price=r["dwd_close_decimal"],
                    turnover=r["dwd_turnover_decimal"],
                    is_tradable=r["is_tradable"] and r["warmup_eligible"],
                    listing_sessions=r["listing_sessions"],
                    adjustment_effective=False,
                    source_evidence_digest=digest_value(
                        {
                            "record": r["dwd_bar_source_record_id"],
                            "artifact": r["dwd_bar_source_artifact_sha256"],
                            "native_from": r["dwd_bar_knowledge_from"].isoformat(),
                        }
                    ),
                )
            )
    if rows:
        yield tuple(rows)


def run_replay(
    *,
    history: Path,
    output: Path,
    repository: Path,
    config: Path,
    smoke: bool = False,
    shared: SharedSnapshotReplay | None = None,
) -> dict[str, Any]:
    """Actual candidate -> experts/veto -> pending -> P2 -> continuous ledger."""
    raw, plan = load_plan(repository, config)
    history = physical_path(history)
    output = disjoint_output(output, [history, repository])
    if output.exists():
        fail("p6_snapshot_output_exists", "do not overwrite an earlier replay")
    m, before = open_history(history)
    owned = shared is None
    shared = SharedSnapshotReplay() if shared is None else shared
    binding = digest_value(
        {"history": file_sha(history / "manifest.json"), "mode_config": file_sha(config)}
    )
    if shared.binding is not None and shared.binding != binding:
        fail("p6_snapshot_shared_identity", "in-Job prerequisites belong to another closure")
    if (
        m["closure"]["mode_config"] != file_sha(config)
        or m["closure"]["window_plan"] != raw["window_plan"]["sha256"]
    ):
        fail("p6_snapshot_plan", "history belongs to another configuration")
    runtime = load_runtime(repository / plan["policy_reference"]["path"], root=repository)
    if m["closure"]["policy_hashes"] != {n: file_sha(p) for n, p in runtime.bindings.items()}:
        fail("p6_snapshot_policy", "policy changed since history freeze")
    cutoff = datetime.fromisoformat(m["knowledge_cutoff"])
    windows = m["windows"]
    dates = tuple(date.fromisoformat(d) for d in windows["all_sessions"])
    decisions = windows["smoke"] if smoke else windows["decisions"]
    run_dates = decisions if smoke else decisions + windows["settlement_tail"]
    bundle = _bundle(history, m, date.fromisoformat(plan["scope"]["end"]))
    policy = SnapshotScanPolicy(
        dates[0],
        date.fromisoformat(windows["decisions"][0]),
        date.fromisoformat(windows["decisions"][-1]),
        dates[-1],
        cutoff,
        digest_value(raw),
    )
    output.mkdir(parents=True)
    (output / "cycles").mkdir()
    started = time.monotonic()
    disposition = "reuse"
    if shared.binding is None:
        disposition = "build"
        scan = scan_forward_candidate_series(
            candidate_series(history, cutoff),
            trading_dates=dates,
            snapshot_id=bundle.snapshot.snapshot_id,
            snapshot_manifest_digest=file_sha(history / "manifest.json"),
            card=load_candidate_rule_card(runtime.bindings["candidate_rule"]),
            split_policy=cast(Any, policy),
            source_policy=cast(Any, policy),
        )
        shared.by_day = defaultdict(list)
        for candidate in scan.candidates:
            shared.by_day[candidate.record.identity.signal_date].append(candidate)
        shared.counters = {
            "candidate_events": len(scan.candidates),
            "reason_counts": dict(scan.counters.reason_counts),
        }
        _write(output / "candidates.private.json", [c.record.to_dict() for c in scan.candidates])
        shared.artifact = BaselineFeaturePanel.materialize(
            bundle,
            load_feature_registry(runtime.bindings["feature_registry"]),
            scope_start=dates[0],
            scope_end=dates[-1],
            output=output / "feature-panel.parquet",
            data_content_digest=bundle.digest,
            data_manifest_sha256=file_sha(history / "manifest.json"),
            duckdb_memory_limit="1500MB",
        )
        try:
            shared.reader = SnapshotFrameReader(bundle)
            shared.panel = BaselineFeaturePanel(shared.artifact.path, shared.artifact.report)
        except BaseException:
            shared.close()
            raise
        shared.binding, shared.bundle, shared.owner_root = binding, bundle, output
        shared.fingerprints = {
            p: _fingerprint(p) for p in [shared.artifact.path, output / "candidates.private.json"]
        }
    if any(_fingerprint(p) != fp for p, fp in shared.fingerprints.items()):
        fail("p6_snapshot_shared_changed", "immutable prerequisite changed")
    by_day = shared.by_day
    _write(output / "candidate-scan.safe.json", shared.counters)
    state = engine._initial_forward_state(runtime)
    policy_paper = load_paper_policy(runtime.bindings["paper_lane"], root=repository)
    summaries = []
    counters: Counter[str] = Counter()
    with shared.lease(owned=owned) as (reader, panel):
        for text in run_dates:
            day = date.fromisoformat(text)
            clock = SnapshotReplayClock(day, cutoff)
            daily_bundle = replace(bundle, snapshot=replace(bundle.snapshot, market_date=day))
            projection: Any = SimpleNamespace(
                bundle=daily_bundle,
                snapshot_id=bundle.snapshot.snapshot_id,
                signal_market_date=day,
                trading_dates=dates,
                feature_panel=lambda: nullcontext(panel),
            )
            ledger_before = state.ledger.digest
            needed = [i.signal.security_code for i in state.pending_intents]
            needed.extend(str(p["listing_code"]) for p in state.positions.values())
            needed.extend(
                str(f["listing_code"])
                for f in state.forecasts
                if f["status"] not in {"abstained", "settled", "not_executable", "unavailable"}
            )
            reader.prefetch(needed, day)
            ledger, pending, contexts, positions, settlements = engine._settle_prior_intents(
                runtime=runtime, projection=projection, prior=state, reader=reader
            )
            ledger, positions, held, sells, sell_contexts = engine._review_held_positions(
                runtime=runtime,
                projection=projection,
                ledger=ledger,
                positions=positions,
                pending_intents=pending,
                reader=reader,
                snapshot_replay_clock=clock,
            )
            if text in decisions:
                rows, evaluations = engine._build_entry_decisions(
                    runtime=runtime,
                    projection=projection,
                    candidates=SimpleNamespace(candidates=tuple(by_day[day])),
                )
                occupied = {i.signal.unit_id for i in (*pending, *sells)}
                ledger, shadow, buys, allocations, buy_contexts = engine._paper_and_shadow(
                    runtime=runtime,
                    projection=projection,
                    decisions=evaluations,
                    ledger=ledger,
                    unavailable_unit_ids=occupied,
                    snapshot_replay_clock=clock,
                )
            else:
                rows, evaluations, shadow, buys, allocations, buy_contexts = [], [], [], [], [], {}
            forecasts, forecast_settlements = engine._settle_forecasts(
                projection=projection, forecasts=state.forecasts, reader=reader
            )
            forecasts.extend(engine._new_forecasts(evaluations))
            reconciled = reconcile_paper_ledger(ledger, policy=policy_paper)
            if not reconciled.balanced:
                fail("p6_snapshot_ledger", "cash/position accounting is not balanced")
            state = replace(
                state,
                market_date=day,
                ledger=ledger,
                pending_intents=tuple((*pending, *sells, *buys)),
                pending_contexts={**contexts, **sell_contexts, **buy_contexts},
                positions=positions,
                forecasts=tuple(forecasts),
            )
            fills = sum(cast(int, s["fill_count"]) for s in settlements)
            counts: Counter[str] = Counter(
                str(r["formal_decision"]["action"]["final_action"]) for r in (*rows, *held)
            )
            counters.update(counts)
            counters.update(
                candidates=len(rows),
                held_reviews=len(held),
                simulated_fills=fills,
                new_intents=len(buys) + len(sells),
                settled_intents=len(settlements),
            )
            safe = {
                "market_date": text,
                "decision_phase": text in decisions,
                "candidate_decisions": len(rows),
                "held_reviews": len(held),
                "actions": dict(counts),
                "new_intents": len(buys) + len(sells),
                "simulated_fills": fills,
                "ledger_before": ledger_before,
                "ledger_after": ledger.digest,
                "ledger_balanced": True,
                "held_positions": len(positions),
                "pending_intents": len(state.pending_intents),
            }
            summaries.append(safe)
            _write(
                output / "cycles" / (text + ".private.json"),
                {
                    "clock": clock.to_dict(),
                    "claims": raw["claims"],
                    "summary": safe,
                    "entry_decisions": rows,
                    "held_decisions": held,
                    "shadow": shadow,
                    "allocations": allocations,
                    "settlements": settlements,
                    "forecast_settlements": forecast_settlements,
                    "state": engine._forward_state_to_dict(state),
                },
            )
            _write(output / "cycles" / (text + ".safe.json"), safe)
    check_unchanged(history, before)
    _write(output / "final-state.private.json", engine._forward_state_to_dict(state))
    result = {
        "schema_version": VERSION,
        "claims": raw["claims"],
        "smoke": smoke,
        "history_identity": m["input_identity"],
        "knowledge_cutoff": cutoff.isoformat(),
        "run_start": run_dates[0],
        "run_end": run_dates[-1],
        "processed_sessions": len(summaries),
        "decision_sessions": len(decisions),
        "tail_sessions": 0 if smoke else len(windows["settlement_tail"]),
        "counters": dict(sorted(counters.items())),
        "all_ledgers_balanced": True,
        "real_path_coverage": {
            "nonempty_candidate_and_expert_path": counters["candidates"] > 0,
            "simulated_fill_path": counters["simulated_fills"] > 0,
            "held_review_path": counters["held_reviews"] > 0,
        },
        "pending_intents_at_end": len(state.pending_intents),
        "held_positions_at_end": len(state.positions),
        "shared_builds": {
            "feature_panel": int(disposition == "build"),
            "candidate_scan": int(disposition == "build"),
            "frame_reader": int(disposition == "build"),
        },
        "shared_prerequisite_binding": binding,
        "shared_disposition": disposition,
        "physical_history_disposition": "reuse_one_immutable_job_local_materialization",
        "slices": [] if smoke else [{**s, "ledger_reset": False} for s in plan["report_slices"]],
        "elapsed_seconds": str(time.monotonic() - started),
        "source_unchanged": True,
        "result": "MECHANISM_REPLAY_COMPLETED_NOT_PIT_OR_ECONOMIC_ACCEPTANCE",
    }
    _write(output / "result.safe.json", result)
    files = {rel: file_sha(output / rel) for rel in sorted(_tree(output))}
    _write(
        output / "manifest.json",
        {
            "schema_version": VERSION,
            "history_manifest": file_sha(history / "manifest.json"),
            "files": files,
        },
    )
    (output / "READY").write_text(file_sha(output / "manifest.json") + "\n")
    return result
