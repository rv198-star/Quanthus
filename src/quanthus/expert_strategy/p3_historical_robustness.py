"""One historical-development robustness experiment, never a PIT/production grant.

Consume the retained 2012--2021 portable bundle without repairing or relabelling
it. Share one prefix-correct feature/candidate prerequisite inside each role;
the cold role reconstructs its own prerequisite from an independent source copy.
All matching, costs, allocation and held rules remain the reviewed P2 code.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quanthus.backtest.baseline_research import (
    BaselineFeaturePanel,
    BaselineFeaturePanelReport,
    BaselineFeatureRow,
    _create_feature_source_views,
    _feature_panel_query,
    _sql_string,
    _validate_feature_sources,
)
from quanthus.backtest.contracts import MARKET_TIMEZONE, CorporateAction, OrderSide
from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.backtest.historical_frames import _HistoricalFrameSource
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import load_corporate_action_normalization_policy
from quanthus.event_library.contracts import EventIdentity
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import (
    CandidateDailyInput,
    _retain_raw_candidates,
    _scan_security_series,
)
from quanthus.expert_evaluation.experts.trend_stability import classify_trend_stability
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_risk_components as components
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime, _stable_id
from quanthus.expert_strategy.p3_retrospective_analysis import classify_headline
from quanthus.expert_strategy.p3_retrospective_replay import (
    RetrospectiveEvent,
)
from quanthus.expert_strategy.p3_strat_003_input_qualification import _RecentVisibilityPolicy
from quanthus.feature_registry import load_feature_registry
from quanthus.strategy_replay_ads import (
    StrategyReplayADSBundle,
    StrategyReplayADSFrameReader,
    load_strategy_replay_ads,
    load_strategy_replay_ads_policy,
)

VERSION = "p3-historical-development-robustness-v1"
REQUEST = "config/research/p3-strat-003/historical-robustness.v1.json"
SCOPE = ["2012-01-04", "2021-12-30", "2021-12-31"]
ARMS = {
    "B": {"trend": False, "entry_cap": False, "held_risk": False},
    "T": {"trend": True, "entry_cap": False, "held_risk": False},
    "TCH": {"trend": True, "entry_cap": True, "held_risk": True},
}
SOURCE_SHA = "sha256:9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628"
ACTION_SOURCE_SNAPSHOT = "p1-s2-s3-dwd-20260725-v1"
# The input-only producer was frozen before the capital-count prose correction.
# Retain its exact population/ranking namespace; the new execution request binds
# this digest plus the full current code/config closure and physical input hashes.
POPULATION_REQUEST_DIGEST = (
    "sha256:f6bbbd8a700acbf967895ce5c553f938d1e8d33db79825c51f3582319c64c86c"
)


def load_request(repository: Path) -> dict[str, Any]:
    raw = native._json(repository / REQUEST)
    if (
        raw.get("schema_version") != VERSION
        or raw.get("scope") != SCOPE
        or raw.get("arms") != ARMS
        or raw.get("execution_order") != list(ARMS)
        or raw.get("source_manifest_sha256") != SOURCE_SHA
        or raw.get("action_source_snapshot_id") != ACTION_SOURCE_SNAPSHOT
        or raw.get("input_preparation_request_digest") != POPULATION_REQUEST_DIGEST
        or raw.get("source_information_cutoff") != "2026-07-29T03:19:57+00:00"
        or raw.get("time_blocks") != [[y, y + 1] for y in range(2012, 2022, 2)]
        or raw.get("annual_diagnostics") != list(range(2012, 2022))
        or raw.get("analysis", {}).get("materiality") != "0.01"
        or raw.get("analysis", {}).get("paired_block_sessions") != 22
        or raw.get("analysis", {}).get("resamples") != 2000
        or any(
            raw.get(k) is not False
            for k in (
                "historical_PIT_claim",
                "independent_holdout_claim",
                "production_authorized",
                "original_B_accepted",
                "original_D_activated",
                "P6_mutation_authorized",
                "source_repair_authorized",
                "parameter_search",
            )
        )
        or any(type(v) is not bool for arm in raw["arms"].values() for v in arm.values())
    ):
        raise native.InputError("HISTORY_REQUEST_BINDING")
    for name, sha in raw["code_config_members"].items():
        path = native.physical(repository / name)
        if native._sha(path) != sha:
            raise native.InputError("HISTORY_CODE_CONFIG_CHANGED")
    return raw


def open_source(root: Path) -> StrategyReplayADSBundle:
    if native._sha(native.physical(root / "manifest.json")) != SOURCE_SHA:
        raise native.InputError("HISTORY_SOURCE_BINDING")
    # Physical hashes, schema, complete table/key/range contract are checked by
    # the existing reader. The separate qualification also recomputes every
    # legacy logical digest in the publisher's UTC process timezone.
    return load_strategy_replay_ads(
        root,
        ads_policy=load_strategy_replay_ads_policy(),
        reader_role="development_research",
        corporate_action_policy=load_corporate_action_normalization_policy(),
    )


def read_preparation(path: Path) -> dict[str, Any]:
    # Ten years can exceed the older 8,016-candidate JSON reader's small cap.
    # This is a separately bounded input contract, not a global cap relaxation.
    if native.physical(path).stat().st_size > 64 * 1024**2:
        raise native.InputError("HISTORY_PREPARATION_SIZE")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or len(value.get("candidates", [])) > 200_000:
        raise native.InputError("HISTORY_PREPARATION_SHAPE")
    return value


class BoundedHistoricalReader(StrategyReplayADSFrameReader):
    def _create_views(self) -> None:
        connection = self._ensure_open()
        connection.execute("SET memory_limit='4GB'")
        connection.execute("SET max_temp_directory_size='6GB'")
        connection.execute("SET timezone='UTC'")
        super()._create_views()


class DayPanel(BaselineFeaturePanel):
    """Bound point-lookup work to one date, without changing any feature values."""

    def __init__(self, path: Path, report: BaselineFeaturePanelReport) -> None:
        import duckdb

        self.path, self.report = path.resolve(), report
        self._spill = tempfile.TemporaryDirectory(prefix="quanthus-historical-panel-")
        self._connection = duckdb.connect(
            config={
                "memory_limit": "4GB",
                "threads": "2",
                "max_temp_directory_size": "6GB",
                "temp_directory": self._spill.name,
            }
        )
        try:
            # Configure the timezone after initialization: DuckDB's startup
            # setting otherwise attempts ICU autoload without a user home in
            # the deliberately sanitized, offline replay environment.
            self._connection.execute("SET timezone='UTC'")
            self._connection.execute(
                "CREATE TABLE feature_panel AS SELECT * FROM read_parquet("
                + _sql_string(str(self.path))
                + ")"
            )
            self._connection.execute(
                "CREATE UNIQUE INDEX feature_panel_key ON feature_panel (market_date,security_id)"
            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        super().close()
        self._spill.cleanup()

    def feature(self, market_date: date, security_id: str) -> BaselineFeatureRow | None:
        if getattr(self, "_day", None) != market_date:
            rows = self._rows(
                "SELECT * EXCLUDE (exception_active, unexplained_price_transition, "
                "scope_continuity_excluded) FROM feature_panel WHERE market_date=?",
                (market_date,),
            )
            if len({r["security_id"] for r in rows}) != len(rows):
                raise native.InputError("HISTORY_FEATURE_DUPLICATE")
            self._day, self._day_rows = (
                market_date,
                {r["security_id"]: BaselineFeatureRow.from_mapping(r) for r in rows},
            )
        row = self._day_rows.get(security_id)
        if row is not None and any(
            getattr(row, key) is None
            for key in (
                "total_return_index",
                "sma20",
                "sma60",
                "momentum20",
                "rsi14",
                "realized_volatility20",
            )
        ):
            row = replace(row, entry_eligible=False)
        return row


def prepare(
    repository: Path,
    bundle: StrategyReplayADSBundle,
    output: Path,
    request: dict[str, Any],
    *,
    retained_feature: tuple[Path, str, str] | None = None,
) -> dict[str, Any]:
    """Build the entire outcome-blind prerequisite once per independently built role."""
    import duckdb

    if output.exists() or output.is_symlink():
        raise native.InputError("HISTORY_PREPARATION_EXISTS")
    output.mkdir()
    c = duckdb.connect(":memory:")
    c.execute("SET threads=2")
    c.execute("SET memory_limit='4GB'")
    c.execute("SET timezone='UTC'")
    c.execute("SET max_temp_directory_size='6GB'")
    (output / "spill").mkdir()
    c.execute("SET temp_directory=" + _sql_string(str(output / "spill")))
    try:
        paths = {
            d.dataset: bundle.historical_bundle.partition_paths(d.dataset)
            for d in bundle.historical_bundle.datasets
        }
        _create_feature_source_views(c, paths, None, scope_end=date(2021, 12, 31))
        # The retained portable tables already contain their accepted dated
        # security identity. Do not invent a DWD listing/company source.
        for name in ("corporate_actions_normalized", "corporate_action_exceptions"):
            c.execute(f"CREATE VIEW source_{name}_mapped AS SELECT * FROM source_{name}")
        _validate_feature_sources(c)
        cutoff = datetime.fromisoformat(request["source_information_cutoff"])
        query = _feature_panel_query(
            date(2011, 1, 4),
            date(2021, 12, 31),
            output_order="security-date",
            continuity_asof_each_row=True,
            retrospective_action_cutoff=cutoff,
        )
        feature = output / "features.parquet"
        feature_identity = digest_value(
            {
                "schema": "historical-prefix-feature-v1",
                "source": bundle.bundle_content_digest,
                "source_manifest": bundle.manifest_sha256,
                "query": query,
                "baseline_evaluator_code": native._sha(
                    repository / "src/quanthus/backtest/baseline_research.py"
                ),
                "registry_config": native._sha(
                    repository / "config/p3-baseline-features.research.v1.json"
                ),
            }
        )
        if retained_feature is None:
            c.execute(
                f"COPY ({query}) TO {_sql_string(str(feature))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)"
            )
        else:
            prior, sha, identity = retained_feature
            if identity != feature_identity or native._sha(native.physical(prior)) != sha:
                raise native.InputError("HISTORY_RETAINED_FEATURE_IDENTITY")
            shutil.copyfile(prior, feature)
            if native._sha(feature) != sha:
                raise native.InputError("HISTORY_RETAINED_FEATURE_COPY")
        c.execute(
            f"CREATE VIEW feature_panel AS SELECT * FROM read_parquet({_sql_string(str(feature))})"
        )
        counts = c.execute(
            "SELECT count(*), count(*) FILTER(WHERE feature_ready), "
            "count(*) FILTER(WHERE entry_eligible), "
            "count(*) FILTER(WHERE exception_active), "
            "count(*) FILTER(WHERE unexplained_price_transition), "
            "count(DISTINCT security_id) FILTER(WHERE scope_continuity_excluded) "
            "FROM feature_panel"
        ).fetchone()
        registry = load_feature_registry(
            repository / "config/p3-baseline-features.research.v1.json"
        )
        assert counts is not None
        report = BaselineFeaturePanelReport(
            VERSION,
            registry.registry_version,
            registry.digest,
            bundle.historical_bundle.snapshot.snapshot_id,
            bundle.bundle_content_digest,
            date(2011, 1, 4),
            date(2021, 12, 31),
            row_count=int(counts[0]),
            feature_ready_count=int(counts[1]),
            entry_eligible_count=int(counts[2]),
            exception_active_count=int(counts[3]),
            unexplained_transition_count=int(counts[4]),
            continuity_excluded_security_count=int(counts[5]),
            data_content_digest=bundle.bundle_content_digest,
            data_manifest_sha256=bundle.manifest_sha256,
        )
        industry = BoundedHistoricalReader(bundle)
        try:
            mapping = industry.load_industry_mapping(
                expected_version="static-level1-concentration-research-v1"
            )
            dates = tuple(
                r[0]
                for r in c.execute(
                    "SELECT trade_date FROM read_parquet(?) WHERE exchange IN ('SSE','SZSE') "
                    "AND is_open=1 GROUP BY trade_date HAVING count(DISTINCT exchange)=2 "
                    "ORDER BY trade_date",
                    [str(paths["trading_calendar"][0])],
                ).fetchall()
            )
        finally:
            industry.close()
        card = load_candidate_rule_card(
            repository / "config/p4-candidate-price-volume-transition.research.v1.json"
        )
        policy: Any = _RecentVisibilityPolicy(
            dates[0],
            date(2012, 1, 4),
            date(2021, 12, 30),
            date(2021, 12, 31),
            request["input_preparation_request_digest"],
        )
        index = {d: i for i, d in enumerate(dates)}
        snapshot = (
            VERSION + "/" + request["input_preparation_request_digest"].removeprefix("sha256:")
        )
        cursor = c.execute("""SELECT b.*, f.listing_sessions, f.exception_active,
            v.stock_code,v.exchange,v.valid_from,
            l.observed_tradestatus,
            EXISTS(SELECT 1 FROM source_corporate_actions_normalized a
                WHERE a.security_id=b.security_id AND a.effective_date=b.date) AS action_boundary
            FROM source_daily_ohlcv_none b
            JOIN feature_panel f ON f.security_id=b.security_id AND f.market_date=b.date
            JOIN source_security_code_validity v ON v.security_id=b.security_id
              AND b.date>=CAST(v.valid_from AS DATE)
              AND (v.valid_to IS NULL OR b.date<=CAST(v.valid_to AS DATE))
            LEFT JOIN source_price_limit_execution_daily l
              ON l.security_id=b.security_id AND l.execution_date=b.date
            ORDER BY b.security_id,v.valid_from,b.date""")
        columns = [r[0] for r in cursor.description]

        def rows() -> Any:
            while batch := cursor.fetchmany(8192):
                for values in batch:
                    yield dict(zip(columns, values, strict=True))

        raw = []
        reasons: Counter[str] = Counter()
        evaluated = 0
        for _, group in groupby(
            rows(), lambda r: (r["security_id"], r["stock_code"], r["valid_from"])
        ):
            series = []
            for row in group:
                sid, day = row["security_id"], row["date"]
                membership = mapping.by_security_id.get(sid)
                series.append(
                    CandidateDailyInput(
                        sid,
                        "legacy-listing:"
                        + digest_value([sid, row["stock_code"], str(row["valid_from"])]),
                        row["stock_code"],
                        "legacy-security-reference:" + sid,
                        row["exchange"],
                        "common_equity",
                        None if membership is None else membership.level1_industry_id,
                        day,
                        datetime.combine(day, time(15), tzinfo=MARKET_TIMEZONE),
                        cutoff,
                        **{
                            n + "_price": None if row[n] is None else Decimal(str(row[n]))
                            for n in ("open", "high", "low", "close")
                        },
                        turnover=None if row["amount"] is None else Decimal(str(row["amount"])),
                        is_tradable=row["observed_tradestatus"] == "1"
                        and not row["exception_active"],
                        listing_sessions=row["listing_sessions"],
                        adjustment_effective=row["action_boundary"],
                        source_evidence_digest=digest_value(
                            {
                                "source_manifest": SOURCE_SHA,
                                "values": [str(v) for v in row.values()],
                            }
                        ),
                    )
                )
            found, blocked, n = _scan_security_series(
                series,
                ordered_dates=dates,
                date_index=index,
                history_sessions=22,
                snapshot_id=snapshot,
                snapshot_manifest_digest=SOURCE_SHA,
                card=card,
                source_policy=policy,
                information_cutoff=cutoff,
            )
            raw.extend(found)
            reasons.update(blocked)
            evaluated += n
        retained, raw_count, isolated, gaps = _retain_raw_candidates(
            raw, reasons=reasons, date_index=index, card=card
        )
        candidates = [
            {
                "security_id": r.security_id,
                "signal_date": r.signal_date.isoformat(),
                "business_close": r.signal_close.isoformat(),
                "static_industry_id": r.industry_id,
                "eligibility_evidence_digest": r.eligibility_evidence_digest,
                "feature_evidence_digest": digest_value(r.feature_evidence.to_dict()),
            }
            for r in retained
        ]
        result = {
            "request_digest": request["input_preparation_request_digest"],
            "snapshot_id": snapshot,
            "feature_report": {
                **asdict(report),
                "scope_start": report.scope_start.isoformat(),
                "scope_end": report.scope_end.isoformat(),
            },
            "candidates": candidates,
            "evaluated_rows": evaluated,
            "raw_triggers": raw_count,
            "isolated": isolated,
            "retained_candidates": len(candidates),
            "industry_gap_identities": len(gaps),
            "reason_counts": dict(sorted(reasons.items())),
            "table_files": {},
            "feature_identity": feature_identity,
            "feature_disposition": "BUILT"
            if retained_feature is None
            else "REUSED_EXACT_JOB_LOCAL_FEATURE",
        }
        (output / "preparation.json").write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n"
        )
        return result
    finally:
        c.close()


@dataclass(frozen=True)
class HistoricalEvent(RetrospectiveEvent):
    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "purpose_id": VERSION}


@dataclass(frozen=True)
class HistoricalSignal:
    signal_id: str
    snapshot: SnapshotRef
    decision_at: datetime
    profile_id: str
    window_id: str | None
    security_code: str
    side: OrderSide
    unit_id: str
    quantity: int
    aggregate_score: Decimal
    reason_code: str
    decision_version: str
    purpose_id: str = VERSION

    def __post_init__(self) -> None:
        if (
            self.purpose_id != VERSION
            or self.profile_id != "close-to-next-open-v1"
            or self.window_id is not None
            or self.decision_at.utcoffset() is None
            or self.snapshot.knowledge_cutoff < self.decision_at
            or self.decision_at.astimezone(MARKET_TIMEZONE).date() != self.snapshot.market_date
            or not date(2012, 1, 4) <= self.snapshot.market_date <= date(2021, 12, 30)
            or type(self.quantity) is not int
            or self.quantity <= 0
            or not self.aggregate_score.is_finite()
            or not isinstance(self.side, OrderSide)
            or any(
                not isinstance(v, str) or not v.strip()
                for v in (
                    self.signal_id,
                    self.security_code,
                    self.unit_id,
                    self.reason_code,
                    self.decision_version,
                )
            )
        ):
            raise native.InputError("HISTORY_SIGNAL_CONTRACT")


@dataclass(frozen=True)
class HistoricalFrame:
    snapshot: SnapshotRef
    decision_at: datetime
    execution_date: date
    source: _HistoricalFrameSource

    def __post_init__(self) -> None:
        if (
            self.decision_at.utcoffset() is None
            or self.snapshot.knowledge_cutoff < self.decision_at
            or self.decision_at.astimezone(MARKET_TIMEZONE).date() != self.snapshot.market_date
            or self.execution_date <= self.snapshot.market_date
        ):
            raise native.InputError("HISTORY_FRAME_CLOCK")

    def session(self, security: str) -> Any:
        return self.source.session(security)

    def block_reason(self, security: str) -> str | None:
        return self.source.block_reason(security)

    def actions(self, security: str, prior_mark_price: Decimal) -> Any:
        return self.source.corporate_actions(security, prior_mark_price)


class HistoricalEngine(BacktestEngine):
    def __init__(self, policy: Any, cutoff: datetime, snapshot: str, action_snapshot: str) -> None:
        super().__init__(policy)
        self.cutoff, self.snapshot, self.action_snapshot = cutoff, snapshot, action_snapshot

    def _validate_signal_clock(self, run: Any, signal: Any) -> None:
        if (
            type(signal) is not HistoricalSignal
            or signal.purpose_id != VERSION
            or signal.snapshot.knowledge_cutoff != self.cutoff
            or signal.snapshot.snapshot_id != self.snapshot
            or run.official
            or not run.code_version.startswith(VERSION + ":")
        ):
            raise BacktestInputError("HISTORY_SIGNAL_BINDING")

    def _validate_action_clock(
        self, run: Any, action: CorporateAction, applied_at: datetime
    ) -> None:
        if (
            action.known_at > self.cutoff
            or action.snapshot_id != self.action_snapshot
            or run.data_snapshot_id != self.action_snapshot
            or run.official
            or not run.code_version.startswith(VERSION + ":")
        ):
            raise BacktestInputError("HISTORY_ACTION_BINDING")


class HistoricalRuntime(accounting.AccountingCorrectedReplayRuntime):
    def __init__(
        self, repository: Path, source: Path, prepared: Path, admission: dict[str, Any]
    ) -> None:
        self.historical_request = load_request(repository)
        self.request_digest = digest_value(self.historical_request)
        if (
            admission.get("request_digest") != self.request_digest
            or admission.get("role") not in {"primary", "independent_cold_reproduction"}
            or admission.get("source_manifest_sha256") != SOURCE_SHA
            or admission.get("preparation_sha256") != native._sha(prepared / "preparation.json")
            or admission.get("feature_sha256") != native._sha(prepared / "features.parquet")
        ):
            raise native.InputError("HISTORY_EXACT_ADMISSION")
        self.bundle = open_source(source)
        self.prepared = prepared
        self.preparation = read_preparation(prepared / "preparation.json")
        if self.preparation["request_digest"] != self.historical_request[
            "input_preparation_request_digest"
        ] or self.preparation[
            "snapshot_id"
        ] != VERSION + "/" + POPULATION_REQUEST_DIGEST.removeprefix("sha256:"):
            raise native.InputError("HISTORY_PREREQUISITE_IDENTITY")
        self.prerequisite_digest = digest_value(
            {k: v for k, v in self.preparation.items() if k != "feature_disposition"}
        )
        self.snapshot_id = self.preparation["snapshot_id"]
        self.info = datetime.fromisoformat(self.historical_request["source_information_cutoff"])
        self.reader = BoundedHistoricalReader(self.bundle)
        self.entry_runners: dict[str, Any] = {}
        try:
            FormalP2ReplayRuntime.__init__(
                self,
                repository,
                decision_start=date(2012, 1, 4),
                decision_end=date(2021, 12, 30),
                execution_end=date(2021, 12, 31),
                parent_event_run_digest=None,
                provider_config=VERSION,
            )
            self.stack.callback(self.reader.close)
            action_sources = self.reader._rows(
                "SELECT DISTINCT source_snapshot_id FROM replay_actions ORDER BY source_snapshot_id"
            )
            if action_sources != [{"source_snapshot_id": ACTION_SOURCE_SNAPSHOT}]:
                raise native.InputError("HISTORY_ACTION_SOURCE_CLOSURE")
            self.engine = HistoricalEngine(
                self.engine.policy,
                self.info,
                self.snapshot_id,
                self.historical_request["action_source_snapshot_id"],
            )
        except BaseException:
            self.reader.close()
            if hasattr(self, "stack"):
                self.stack.close()
            raise

    def _load_event_population(self, event_root: Path) -> Any:
        mapping = self.reader.load_industry_mapping(
            expected_version="static-level1-concentration-research-v1"
        )
        card = load_candidate_rule_card(
            self.repository_root / "config/p4-candidate-price-volume-transition.research.v1.json"
        )
        events = []
        for r in self.preparation["candidates"]:
            sid = r["security_id"]
            if mapping.by_security_id[sid].level1_industry_id != r["static_industry_id"]:
                raise native.InputError("HISTORY_INDUSTRY_BINDING")
            events.append(
                SimpleNamespace(
                    record=HistoricalEvent(
                        EventIdentity(
                            sid,
                            date.fromisoformat(r["signal_date"]),
                            card.rule_id,
                            card.rule_version,
                            self.snapshot_id,
                        ),
                        self.info,
                        datetime.fromisoformat(r["business_close"]),
                        card.semantic_digest,
                        SOURCE_SHA,
                        r["eligibility_evidence_digest"],
                        r["feature_evidence_digest"],
                        self.info,
                    ),
                    panel_security_id=sid,
                    static_industry_id=r["static_industry_id"],
                )
            )
        return tuple(events), mapping

    def _open_data_inputs(self) -> None:
        self.data_snapshot_id = self.snapshot_id
        self.data_content_digest = self.prerequisite_digest
        self.data_manifest_sha256 = SOURCE_SHA
        self.frames: Any = [
            HistoricalFrame(
                SnapshotRef(day, self.info, SOURCE_SHA, self.snapshot_id),
                datetime.combine(day, time(15), tzinfo=MARKET_TIMEZONE),
                following,
                _HistoricalFrameSource(self.reader, following),
            )
            for day, following in self.reader._date_pairs(self.decision_start, self.decision_end)
        ]
        report = BaselineFeaturePanelReport(
            **{
                **self.preparation["feature_report"],
                "scope_start": date.fromisoformat(
                    self.preparation["feature_report"]["scope_start"]
                ),
                "scope_end": date.fromisoformat(self.preparation["feature_report"]["scope_end"]),
            }
        )
        self.panel = self.stack.enter_context(DayPanel(self.prepared / "features.parquet", report))

    def _validate_intervention_id(self, intervention_id: str | None) -> None:
        if intervention_id not in ARMS:
            raise native.InputError("HISTORY_VARIANT")

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        if intervention_id not in ARMS:
            raise native.InputError("HISTORY_VARIANT")
        return "variant." + str(intervention_id)

    def _held_intervention_id(self, intervention_id: str | None) -> str | None:
        self._validate_intervention_id(intervention_id)
        return None if intervention_id == "TCH" else "intervention.position-risk-base-policy"

    def _evaluate_entry_decision(
        self, runner: Any, *, intervention_id: str | None, **kwargs: Any
    ) -> Any:
        if intervention_id == "B":
            kwargs.pop("trend_state")
            return runner.evaluate_entry_control(**kwargs, position_risk_enabled=False)
        if intervention_id == "T":
            return runner.evaluate_entry(
                **kwargs, intervention_id="intervention.position-risk-base-policy"
            )
        if intervention_id == "TCH":
            if "TCH" not in self.entry_runners:
                self.entry_runners["TCH"] = components.ComponentEntryRunner(runner, "TC")
            return self.entry_runners["TCH"].evaluate_component(**kwargs)
        raise native.InputError("HISTORY_VARIANT")

    def _trend_observation(self, event: Any, feature: Any) -> Any:
        state, reason = classify_trend_stability(feature, self.trend_config)
        return SimpleNamespace(
            raw_value=state,
            digest=digest_value(
                {
                    "purpose": VERSION,
                    "event": event.to_dict(),
                    "state": state,
                    "reason": reason,
                    "config": self.trend_config.semantic_digest,
                }
            ),
        )

    @staticmethod
    def _held_event(source: Any, frame: Any) -> Any:
        return replace(
            source,
            identity=replace(source.identity, signal_date=frame.snapshot.market_date),
            signal_close=frame.decision_at,
        )

    def _signal(
        self,
        run: Any,
        frame: Any,
        unit_id: str,
        security_id: str,
        side: Any,
        quantity: int,
        score: Decimal,
        reason: str,
        decision_digest: str,
    ) -> Any:
        return HistoricalSignal(
            _stable_id(
                "historical-signal",
                run.run_id,
                frame.snapshot.market_date,
                unit_id,
                side.value,
                reason,
            ),
            frame.snapshot,
            frame.decision_at,
            run.execution_profile_id,
            None,
            security_id,
            side,
            unit_id,
            quantity,
            score,
            reason,
            self.registry.card.strategy_version + "|" + decision_digest,
        )

    def _code_version(self) -> str:
        return VERSION + ":" + self.request_digest

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            "request_digest": self.request_digest,
            "variant": intervention_id,
            "source_manifest_sha256": SOURCE_SHA,
            "accounting_version": accounting.VERSION,
        }

    def _population_manifest_fields(self) -> dict[str, Any]:
        return {
            "historical_prerequisite_digest": self.prerequisite_digest,
            "source_manifest_sha256": SOURCE_SHA,
            "historical_availability_claim": False,
        }

    def _baseline_variant_directory(self) -> str:
        return "variant.B"

    def _metrics_intervention_id(self, intervention_id: str | None) -> str | None:
        return None if intervention_id == "B" else intervention_id

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            "purpose": VERSION,
            "variant": intervention_id,
            "historical_availability_claim": False,
            "independent_holdout_claim": False,
            "production_authorized": False,
        }


def analyze_history(
    request: dict[str, Any], artifacts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Fixed exact-equity point estimates and descriptive paired block intervals."""
    import numpy as np

    if set(artifacts) != set(ARMS):
        raise native.InputError("HISTORY_ANALYSIS_ARMS")
    request_digest = digest_value(request)
    dates = [r["market_date"] for r in artifacts["B"]["daily-returns.json"]]
    if (
        dates != sorted(set(dates))
        or not dates
        or dates[0] < "2012-01-04"
        or dates[-1] != "2021-12-31"
    ):
        raise native.InputError("HISTORY_ANALYSIS_DATES")
    metrics: dict[str, Any] = {}
    rounded_returns: dict[str, Any] = {}
    for arm in ARMS:
        data = artifacts[arm]
        equity, daily = data["equity.json"], data["daily-returns.json"]
        if (
            [r["market_date"] for r in daily] != dates
            or len(equity) != len(dates) + 1
            or [r["market_date"] for r in equity[1:]] != dates
            or equity[0]["market_date"] != "2012-01-04"
            or Decimal(equity[0]["system_equity"]) != Decimal(500000)
        ):
            raise native.InputError("HISTORY_ANALYSIS_PAIRING")
        amounts = [Decimal(r["system_equity"]) for r in equity]
        if any(not x.is_finite() or x <= 0 for x in amounts):
            raise native.InputError("HISTORY_ANALYSIS_EQUITY")
        values = [Decimal(r["return"]) for r in daily]
        if any(
            (now / before - 1).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP) != observed
            for before, now, observed in zip(amounts[:-1], amounts[1:], values, strict=True)
        ):
            raise native.InputError("HISTORY_ANALYSIS_ROUNDED_RETURN")
        rounded_returns[arm] = np.asarray([float(x) for x in values])
        fills = [e for e in data["engine-events.json"] if e["event_type"] == "fill"]

        def summarize(
            indices: list[int],
            amounts: list[Decimal] = amounts,
            equity: list[dict[str, Any]] = equity,
            fills: list[dict[str, Any]] = fills,
        ) -> dict[str, Any]:
            if not indices:
                raise native.InputError("HISTORY_ANALYSIS_EMPTY_BLOCK")
            initial = amounts[indices[0]]
            peak, drawdown = initial, Decimal(0)
            for i in indices:
                peak = max(peak, amounts[i + 1])
                drawdown = max(drawdown, 1 - amounts[i + 1] / peak)
            selected_dates = {dates[i] for i in indices}
            selected_fills = [
                e["payload"]
                for e in fills
                if datetime.fromisoformat(e["event_time"])
                .astimezone(MARKET_TIMEZONE)
                .date()
                .isoformat()
                in selected_dates
            ]
            return {
                "sessions": len(indices),
                "net_total_return": str(amounts[indices[-1] + 1] / initial - 1),
                "maximum_drawdown": str(drawdown),
                "average_invested_capital": str(
                    sum(
                        (Decimal(equity[i + 1]["market_value"]) / amounts[i + 1] for i in indices),
                        Decimal(0),
                    )
                    / len(indices)
                ),
                "fees": str(sum((Decimal(f["total_fees"]) for f in selected_fills), Decimal(0))),
                "turnover_on_block_start_capital": str(
                    sum((Decimal(f["gross_amount"]) for f in selected_fills), Decimal(0)) / initial
                ),
                "fill_count": len(selected_fills),
            }

        metrics[arm] = {
            **summarize(list(range(len(dates)))),
            "annual": {
                str(y): summarize([i for i, d in enumerate(dates) if d.startswith(str(y))])
                for y in request["annual_diagnostics"]
            },
            "two_year": {
                f"{a}-{b}": summarize([i for i, d in enumerate(dates) if a <= int(d[:4]) <= b])
                for a, b in request["time_blocks"]
            },
        }
    count = len(dates) // 22
    if count < 2:
        raise native.InputError("HISTORY_ANALYSIS_BLOCK_COUNT")
    contrasts = {}
    for contrast in request["analysis"]["contrasts"]:
        left, right = contrast.split("-")
        seed = int(hashlib.sha256((request_digest + ":" + contrast).encode()).hexdigest(), 16)
        rng = random.Random(seed)
        indices = np.asarray([[rng.randrange(count) for _ in range(count)] for _ in range(2000)])
        paths = {}
        for arm in (left, right):
            returns = (
                rounded_returns[arm][: count * 22]
                .reshape(count, 22)[indices]
                .reshape(2000, count * 22)
            )
            wealth = np.concatenate([np.ones((2000, 1)), np.cumprod(1 + returns, axis=1)], axis=1)
            paths[arm] = {
                "net_total_return": wealth[:, -1] - 1,
                "maximum_drawdown": np.max(
                    1 - wealth / np.maximum.accumulate(wealth, axis=1), axis=1
                ),
            }
        delta = {k: Decimal(metrics[left][k]) - Decimal(metrics[right][k]) for k in paths[left]}
        intervals = {}
        for k in delta:
            sorted_samples = np.sort(paths[left][k] - paths[right][k])
            intervals[k] = {
                "lower": Decimal(str(sorted_samples[49])),
                "upper": Decimal(str(sorted_samples[1949])),
            }
        contrasts[contrast] = {
            "seed": str(seed),
            "point_delta": {k: str(v) for k, v in delta.items()},
            "descriptive_unadjusted_95_interval": {
                k: {bound: str(v) for bound, v in limits.items()} for k, limits in intervals.items()
            },
            "screen": classify_headline(delta, intervals),
        }
    return {
        "purpose": VERSION,
        "request_digest": request_digest,
        "variants": metrics,
        "contrasts": contrasts,
        "complete_22_session_blocks": count,
        "incomplete_dates_excluded_only_from_resampling": dates[count * 22 :],
        "window": [dates[0], dates[-1]],
        "historical_PIT_claim": False,
        "independent_holdout_claim": False,
        "strategy_accepted": False,
        "production_authorized": False,
    }
