"""Separate R2 execution boundary reusing the frozen four-variant/P2 arithmetic.

Neither the prepared journal nor this runtime qualifies a warehouse source. A real
run requires the separate consumer admission; synthetic runs exercise the same
executor without opening protected results. No strict signal/event is backdated.
"""

from __future__ import annotations

import json
import resource
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeaturePanelReport, _sql_string
from quanthus.backtest.contracts import (
    BacktestRunSpec,
    MarketSession,
    OpenExecutionState,
    OrderSide,
)
from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.backtest.inputs import market_session_from_p1
from quanthus.contracts import SnapshotRef
from quanthus.event_library.contracts import EventIdentity
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.expert_evaluation.experts.trend_stability import (
    _feature_payload,
    classify_trend_stability,
)
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_bridge as bridge
from quanthus.expert_strategy import p3_retrospective_preparation as prep
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime, _stable_id
from quanthus.expert_strategy.p3_base24_batch import _NativePanel, _session, _session_dict
from quanthus.expert_strategy.p3_strat_003 import P3Strat003ReplayRuntime, P3Strat003Request
from quanthus.feature_registry import load_feature_registry
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping

VERSION = "p3-retrospective-replay-v1"


@dataclass(frozen=True)
class RetrospectiveSignal:
    """Simulation business time and actual snapshot knowledge are distinct."""

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
    purpose_id: str = prep.PURPOSE

    def __post_init__(self) -> None:
        if (
            self.purpose_id != prep.PURPOSE
            or self.profile_id != "close-to-next-open-v1"
            or self.window_id is not None
            or self.decision_at.tzinfo is None
            or self.decision_at.utcoffset() is None
            or self.snapshot.knowledge_cutoff < self.decision_at
            or self.decision_at.astimezone(native.TZ).date() != self.snapshot.market_date
            or not isinstance(self.side, OrderSide)
            or type(self.quantity) is not int
            or self.quantity <= 0
            or not self.aggregate_score.is_finite()
            or any(
                not isinstance(x, str) or not x.strip()
                for x in (
                    self.signal_id,
                    self.security_code,
                    self.unit_id,
                    self.reason_code,
                    self.decision_version,
                )
            )
        ):
            raise native.InputError("RETRO_SIGNAL_CONTRACT")


class RetrospectiveEngine(BacktestEngine):
    def __init__(self, policy: Any, *, information_cutoff: datetime, snapshot_id: str) -> None:
        super().__init__(policy)
        self.information_cutoff, self.snapshot_id = information_cutoff, snapshot_id

    def _validate_signal_clock(self, run: BacktestRunSpec, signal: Any) -> None:
        if (
            type(signal) is not RetrospectiveSignal
            or signal.purpose_id != prep.PURPOSE
            or signal.snapshot.knowledge_cutoff != self.information_cutoff
            or signal.snapshot.snapshot_id != self.snapshot_id
            or run.official
            or not run.code_version.startswith(VERSION + ":")
        ):
            raise BacktestInputError("R2 purpose/snapshot/run binding required")
        # Remaining matching, funding, tick, cost and T+1 checks stay in P2.


@dataclass(frozen=True)
class RetrospectiveEvent:
    identity: EventIdentity
    knowledge_cutoff: datetime
    signal_close: datetime
    candidate_rule_digest: str
    snapshot_manifest_digest: str
    eligibility_evidence_digest: str
    feature_window_manifest_digest: str
    candidate_source_known_at: datetime

    def __post_init__(self) -> None:
        if (
            self.knowledge_cutoff.tzinfo is None
            or self.signal_close.tzinfo is None
            or self.candidate_source_known_at.tzinfo is None
            or self.candidate_source_known_at > self.knowledge_cutoff
            or self.signal_close > self.knowledge_cutoff
            or self.signal_close.astimezone(native.TZ).date() != self.identity.signal_date
            or any(
                not native._SHA.fullmatch(d)
                for d in (
                    self.candidate_rule_digest,
                    self.snapshot_manifest_digest,
                    self.eligibility_evidence_digest,
                    self.feature_window_manifest_digest,
                )
            )
        ):
            raise native.InputError("RETRO_EVENT_CONTRACT")

    @property
    def event_id(self) -> str:
        return "r2-event:" + digest_value(self.to_dict()).split(":")[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "purpose_id": prep.PURPOSE,
            **self.identity.to_dict(),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "business_close": self.signal_close.isoformat(),
            "candidate_source_known_at": self.candidate_source_known_at.isoformat(),
            "candidate_rule_digest": self.candidate_rule_digest,
            "snapshot_manifest_digest": self.snapshot_manifest_digest,
            "eligibility_evidence_digest": self.eligibility_evidence_digest,
            "feature_window_manifest_digest": self.feature_window_manifest_digest,
            "historical_availability_claim": False,
        }


@dataclass(frozen=True)
class RetrospectiveFrame:
    snapshot: SnapshotRef
    decision_at: datetime
    execution_date: date
    source: _DaySource

    def __post_init__(self) -> None:
        if (
            self.decision_at.tzinfo is None
            or self.snapshot.knowledge_cutoff < self.decision_at
            or self.decision_at.astimezone(native.TZ).date() != self.snapshot.market_date
            or self.execution_date <= self.snapshot.market_date
        ):
            raise native.InputError("RETRO_FRAME_CLOCK")

    def session(self, security: str) -> MarketSession:
        return self.source.session(security)

    def block_reason(self, security: str) -> str | None:
        return self.source.block_reason(security)

    def actions(self, security: str, prior_mark_price: Decimal) -> tuple[Any, ...]:
        # Only explicit qualified no-action support can return (). Known and
        # unknown action boundaries block the affected held step, never fabricate
        # a zero action or force an exit. Normalized action replay is not inferred.
        if self.source.block_reason(security) is not None:
            raise native.InputError("RETRO_HELD_ACTION_UNQUALIFIED")
        return ()


class _SessionIndex:
    """One bounded, job-local index shared by every day and all four variants."""

    def __init__(self, path: Path) -> None:
        import duckdb

        self.connection = duckdb.connect(":memory:")
        self.connection.execute("SET memory_limit='2GB'; SET threads=2")
        self.connection.execute(
            "CREATE VIEW sessions AS SELECT * FROM read_parquet(" + _sql_string(str(path)) + ")"
        )
        self.day: date | None = None
        self.rows: dict[str, dict[str, Any]] = {}

    def get(self, day: date, security: str) -> dict[str, Any] | None:
        if self.day != day:
            self.rows = {
                sid: json.loads(payload)
                for sid, payload in self.connection.execute(
                    "SELECT security_id,payload FROM sessions WHERE market_date=?", [day]
                ).fetchall()
            }
            self.day = day
        return self.rows.get(security)

    def close(self) -> None:
        self.connection.close()


@dataclass(frozen=True)
class _DaySource:
    index: _SessionIndex
    empty: MarketSession

    def session(self, security: str) -> MarketSession:
        row = self.index.get(self.empty.session_date, security)
        return (
            replace(self.empty, security_code=security) if row is None else _session(row["session"])
        )

    def block_reason(self, security: str) -> str | None:
        row = self.index.get(self.empty.session_date, security)
        return (
            "RETRO_ACTION_OR_SESSION_UNQUALIFIED" if row is None or row["blocked_action"] else None
        )


def _index(
    prepared: Path, output: Path, plan: Mapping[str, Any], identity: str, guard: Callable[[], None]
) -> dict[date, tuple[datetime, datetime]]:
    import duckdb
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    schema = pa.schema(
        [("market_date", pa.date32()), ("security_id", pa.string()), ("payload", pa.string())]
    )
    clocks: dict[date, tuple[datetime, datetime]] = {}
    days = tuple(map(date.fromisoformat, plan["trading_dates"]))
    positions = {d: i for i, d in enumerate(days)}
    batch = []
    unsorted = output / "sessions-unsorted.parquet"
    with pq.ParquetWriter(unsorted, schema, compression="zstd") as writer:
        for port in prep._checked(prep._ports(prepared), plan):
            p = port.selected
            if p is None:
                continue
            clock = p.open_at, p.close_at
            if clocks.setdefault(port.business_date, clock) != clock:
                raise native.InputError("RETRO_SESSION_CLOCK_CONFLICT")
            i = positions[port.business_date]
            if i == 0:
                continue
            snap = SnapshotRef(
                port.business_date, port.information_cutoff, port.source_manifest_sha256, identity
            )
            evidence = bridge.execution_evidence(
                port,
                snapshot=snap,
                decision_date=days[i - 1],
                next_session_date=port.business_date,
                trading_dates=days,
            )
            session = market_session_from_p1(evidence)
            blocked = p.adjustment_effective is not False
            if blocked:
                session = replace(
                    session,
                    open_price=None,
                    close_price=None,
                    open_execution_state=OpenExecutionState.MISSING_EVIDENCE,
                )
            batch.append(
                {
                    "market_date": port.business_date,
                    "security_id": port.security_id,
                    "payload": json.dumps(
                        {"session": _session_dict(session), "blocked_action": blocked},
                        sort_keys=True,
                    ),
                }
            )
            if len(batch) == 4096:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                batch.clear()
                guard()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    guard()
    with duckdb.connect(":memory:") as c:
        c.execute("SET memory_limit='2GB'; SET threads=2")
        c.execute("SET temp_directory=?", [str(output / "sort-TMP")])
        c.execute("SET max_temp_directory_size='1GB'")
        c.execute(
            "CREATE VIEW input AS SELECT * FROM read_parquet(" + _sql_string(str(unsorted)) + ")"
        )
        native._zero(
            c,
            "SELECT count(*) FROM (SELECT market_date,security_id FROM input "
            "GROUP BY ALL HAVING count(*)<>1)",
            "RETRO_SESSION_DUPLICATE",
        )
        c.execute(
            "COPY (SELECT * FROM input ORDER BY market_date,security_id) TO ? "
            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 4096)",
            [str(output / "sessions.parquet")],
        )
    guard()
    unsorted.unlink()  # only this successful job-owned sort input
    return clocks


class _MaskedPanel(_NativePanel):
    def __init__(
        self, path: Path, report: BaselineFeaturePanelReport, masks: Mapping[Any, Any]
    ) -> None:
        super().__init__(path, report)
        self.masks = masks

    def feature(self, market_date: date, security_id: str) -> Any:
        value = super().feature(market_date, security_id)
        mask = self.masks.get((security_id, market_date.isoformat()))
        if value is not None and mask is not None and not mask["eligible"]:
            return replace(value, entry_eligible=False)
        return value


class RetrospectiveRuntime(P3Strat003ReplayRuntime):
    """Actual accepted allocator/experts/ledger, with explicit R2 time contracts."""

    def __init__(
        self,
        repository: Path,
        prepared: Path,
        workspace: Path,
        *,
        manifest_sha256: str,
        industry: StaticIndustryMapping,
        consumer_admission: Mapping[str, Any],
    ) -> None:
        self.prepared, self.workspace = native.physical(prepared), native.physical(workspace)
        self.preparation_sha = manifest_sha256
        self.r2_plan = native._json(self.prepared / "plan.json")
        # Real execution is possible only with an exact consumer receipt already
        # admitted in ordinary versioned configuration. A caller flag, source
        # READY, or passing synthetic tests cannot activate protected results.
        self.synthetic = self.r2_plan["synthetic"]
        if self.synthetic:
            if consumer_admission != {
                "purpose_id": prep.PURPOSE,
                "mode": "SYNTHETIC_ONLY",
                "preparation_sha256": manifest_sha256,
            }:
                raise native.InputError("RETRO_CONSUMER_ADMISSION_BINDING")
        else:
            activation = native._json(
                repository / "config/research/p3-strat-003/retrospective-execution.v1.json"
            )
            if (
                digest_value(dict(consumer_admission))
                not in activation["admitted_consumer_receipt_sha256"]
                or activation["purpose_id"] != prep.PURPOSE
                or consumer_admission.get("purpose_id") != prep.PURPOSE
                or consumer_admission.get("preparation_sha256") != manifest_sha256
                or consumer_admission.get("input_manifest_sha256")
                != self.r2_plan["input_manifest_sha256"]
                or consumer_admission.get("support_manifest_sha256")
                != self.r2_plan["support_manifest_sha256"]
                or consumer_admission.get("information_cutoff")
                != self.r2_plan["information_cutoff"]
                or consumer_admission.get("scope") != self.r2_plan["scope"]
                or consumer_admission.get("result_role")
                not in {"primary", "independent_cold_reproduction"}
            ):
                raise native.InputError("RETRO_REAL_SOURCE_ADMISSION_NOT_CLOSED")
        self.consumer_admission_digest = digest_value(dict(consumer_admission))
        prep.verify(repository, self.prepared, manifest_sha256=manifest_sha256)
        industry_path = native.physical(industry.path)
        if native._sha(industry_path) != self.r2_plan["industry_manifest_sha256"]:
            raise native.InputError("RETRO_INDUSTRY_MANIFEST_BINDING")
        industry = load_static_industry_mapping(
            industry_path, expected_version="static-level1-concentration-research-v1"
        )
        self.input_fingerprints = {
            p: (p.stat().st_dev, p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
            for p in [
                industry_path,
                *(self.prepared / name for name in prep.MEMBERS),
                self.prepared / "manifest.json",
                self.prepared / "PREPARATION_COMPLETE.json",
            ]
        }
        if (
            workspace.exists()
            or workspace.is_symlink()
            or workspace.is_relative_to(prepared)
            or prepared.is_relative_to(workspace)
        ):
            raise native.InputError("RETRO_RUNTIME_OUTPUT_ISOLATION")
        self.started = time.monotonic()
        self._guard()
        workspace.mkdir()
        self.r2_industry = industry
        self.request = P3Strat003Request.load(repository)
        self.candidate_input = self.candidate_input_manifest = None
        start, end, execution = map(date.fromisoformat, self.r2_plan["scope"])
        self.info = datetime.fromisoformat(self.r2_plan["information_cutoff"])
        self.snapshot_id = prep.PURPOSE + "/" + manifest_sha256.split(":")[1]
        self.clocks = _index(prepared, workspace, self.r2_plan, self.snapshot_id, self._guard)
        try:
            FormalP2ReplayRuntime.__init__(
                self,
                repository,
                decision_start=start,
                decision_end=end,
                execution_end=execution,
                parent_event_run_digest=None,
                provider_config=prep.PURPOSE,
            )
            self.engine = RetrospectiveEngine(
                self.engine.policy, information_cutoff=self.info, snapshot_id=self.snapshot_id
            )
        except BaseException:
            if hasattr(self, "stack"):
                self.stack.close()
            raise

    def _load_event_population(self, event_root: Path) -> Any:
        masks = {
            (x["security_id"], x["business_date"]): x
            for x in map(json.loads, (self.prepared / "common-mask.jsonl").read_text().splitlines())
        }
        card = load_candidate_rule_card(
            self.repository_root / "config/p4-candidate-price-volume-transition.research.v1.json"
        )
        self.common_masks = masks
        events = []
        for r in native._json(self.prepared / "candidates.json")["candidates"]:
            membership = self.r2_industry.by_security_id.get(r["security_id"])
            if membership is None or membership.level1_industry_id != r["static_industry_id"]:
                raise native.InputError("RETRO_INDUSTRY_CONFLICT")
            event = RetrospectiveEvent(
                EventIdentity(
                    r["security_id"],
                    date.fromisoformat(r["signal_date"]),
                    card.rule_id,
                    card.rule_version,
                    self.snapshot_id,
                ),
                self.info,
                datetime.fromisoformat(r["business_close"]),
                card.semantic_digest,
                self.preparation_sha,
                r["eligibility_evidence_digest"],
                r["feature_evidence_digest"],
                datetime.fromisoformat(r["source_knowledge_cutoff"]),
            )
            events.append(
                SimpleNamespace(
                    record=event,
                    static_industry_id=r["static_industry_id"],
                    panel_security_id=r["security_id"],
                )
            )
        return tuple(events), self.r2_industry

    def _open_data_inputs(self) -> None:
        import duckdb

        self.data_snapshot_id = self.snapshot_id
        self.data_content_digest = self.data_manifest_sha256 = self.preparation_sha
        index = _SessionIndex(self.workspace / "sessions.parquet")
        self.stack.callback(index.close)
        days = tuple(map(date.fromisoformat, self.r2_plan["trading_dates"]))
        self.frames: Any = []
        for day, following in zip(days, days[1:], strict=False):
            if not self.decision_start <= day <= self.decision_end:
                continue
            if day not in self.clocks or following not in self.clocks:
                raise native.InputError("RETRO_SESSION_CLOCK_UNAVAILABLE")
            opened, closed = self.clocks[following]
            empty = MarketSession(
                following,
                opened,
                closed,
                "missing",
                None,
                None,
                OpenExecutionState.MISSING_EVIDENCE,
                self.r2_plan["source_manifest_sha256"],
                self.snapshot_id,
            )
            self.frames.append(
                RetrospectiveFrame(
                    SnapshotRef(
                        day, self.info, self.r2_plan["source_manifest_sha256"], self.snapshot_id
                    ),
                    self.clocks[day][1],
                    following,
                    _DaySource(index, empty),
                )
            )
        with duckdb.connect(":memory:") as c:
            c.execute(
                "CREATE VIEW f AS SELECT * FROM read_parquet("
                + _sql_string(str(self.prepared / "features.parquet"))
                + ")"
            )
            counts = c.execute(
                "SELECT count(*),count(*) FILTER(WHERE feature_ready),"
                "count(*) FILTER(WHERE entry_eligible),count(*) FILTER(WHERE exception_active),"
                "count(*) FILTER(WHERE unexplained_price_transition) FROM f"
            ).fetchone()
        assert counts is not None
        registry = load_feature_registry(
            self.repository_root / "config/p3-baseline-features.research.v1.json"
        )
        report = BaselineFeaturePanelReport(
            VERSION,
            registry.registry_version,
            registry.digest,
            self.snapshot_id,
            self.preparation_sha,
            self.decision_start,
            self.decision_end,
            counts[0],
            counts[1],
            counts[2],
            counts[3],
            counts[4],
            0,
            data_content_digest=self.preparation_sha,
            data_manifest_sha256=self.preparation_sha,
        )
        self.panel = self.stack.enter_context(
            _MaskedPanel(self.prepared / "features.parquet", report, self.common_masks)
        )

    def _guard(self) -> None:
        for path, expected in self.input_fingerprints.items():
            st = path.stat()
            if expected != (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns):
                raise native.InputError("RETRO_RUNTIME_INPUT_CHANGED")
        if (
            time.monotonic() - self.started > 3600
            or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > 4 * 1024**3
            or shutil.disk_usage(self.workspace.parent).free < 8 * 1024**3
            or (
                self.workspace.exists()
                and sum(p.stat().st_size for p in self.workspace.rglob("*") if p.is_file())
                > 2 * 1024**3
            )
        ):
            raise native.InputError("RETRO_RUNTIME_BUDGET")

    def _protected_partition_access(self) -> bool:
        return not self.synthetic

    def _trend_observation(self, event: Any, feature: Any) -> Any:
        state, reason = classify_trend_stability(feature, self.trend_config)
        return SimpleNamespace(
            raw_value=state,
            digest=digest_value(
                {
                    "purpose_id": prep.PURPOSE,
                    "event": event.to_dict(),
                    "state": state,
                    "reason": reason,
                    "feature": None if feature is None else _feature_payload(feature),
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
        return RetrospectiveSignal(
            _stable_id(
                "r2-signal",
                run.run_id,
                frame.snapshot.snapshot_id,
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
        return VERSION + ":" + self.preparation_sha

    def _population_manifest_fields(self) -> dict[str, Any]:
        return {
            "retrospective_preparation_sha256": self.preparation_sha,
            "historical_availability_claim": False,
        }

    def batch_input_identity(self) -> dict[str, Any]:
        return {
            "purpose_id": prep.PURPOSE,
            "preparation_sha256": self.preparation_sha,
            "request_digest": self.request.digest,
            "information_cutoff": self.info.isoformat(),
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            "schema_version": VERSION,
            "purpose_id": prep.PURPOSE,
            "research_variant_id": self.variant_for(intervention_id),
            "information_cutoff": self.info.isoformat(),
            "claim_ceiling": "SYNTHETIC_R2_EXECUTOR_ONLY"
            if self.synthetic
            else "EXPLORATORY_RETROSPECTIVE_ONLY",
            "consumer_admission_digest": self.consumer_admission_digest,
            "original_B_accepted": False,
            "D_activated": False,
            "historical_availability_claim": False,
            "production_authorized": False,
        }

    def execute_all(self) -> dict[str, Any]:
        results: dict[str, str] = {}
        for variant in ("B", "T", "R", "TR"):
            self._guard()
            target = self.workspace / ("variant." + variant)
            target.mkdir()
            try:
                evidence, artifacts = self.run_research_variant(variant, target)
            except Exception as exc:
                (self.workspace / "FAILED.json").write_bytes(
                    canonical_json_bytes(
                        {
                            "purpose_id": prep.PURPOSE,
                            "failed_variant": variant,
                            "failure_type": type(exc).__name__,
                            "completed_variants": list(results),
                            "complete": False,
                            "outputs_are_partial": True,
                        }
                    )
                )
                raise
            for name, payload in artifacts.items():
                (target / name).write_bytes(payload)
            results[variant] = evidence.replay_digest
        report = {
            "purpose_id": prep.PURPOSE,
            "variants": results,
            "mode": "SYNTHETIC_ONLY" if self.synthetic else "ADMITTED_RETROSPECTIVE",
            "provider_builds": 1,
            "feature_panel_builds": 0,
            "session_index_builds": 1,
            "real_comparisons_executed": 0 if self.synthetic else len(results),
            "information_cutoff": self.info.isoformat(),
        }
        self._guard()
        (self.workspace / "COMPLETE.json").write_bytes(canonical_json_bytes(report))
        return report
