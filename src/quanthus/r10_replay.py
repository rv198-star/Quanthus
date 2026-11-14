"""Controlled native source adapter for the unchanged complete two-expert replay."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from quanthus.aggregator import GovernedAction
from quanthus.backtest.baseline_research import (
    BaselineFeaturePanelReport,
    BaselineFeatureRow,
    _feature_panel_query,
)
from quanthus.backtest.inputs import CoverageStatus
from quanthus.backtest.native_capital_replay import NativeCapitalStore, NativeFrameSource
from quanthus.backtest.strategy_replay import PITDailyBar, StrategyReplayFrame
from quanthus.contracts import SnapshotRef
from quanthus.event_library.contracts import (
    EventIdentity,
    EventRecord,
    SplitAssignment,
    event_record_from_dict,
)
from quanthus.expert_evaluation.experts.trend_stability import evaluate_trend_stability
from quanthus.expert_evaluation.production import _EventInput
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
from quanthus.feature_registry import load_feature_registry
from quanthus.r10_inputs import MANIFEST_SHA, NativeInputs, digest, read, rows


class NativeStore(NativeCapitalStore):
    def __init__(self, native: NativeInputs):
        self.native_inputs = native
        super().__init__(
            native.db,
            {
                "source_knowledge_cutoff": native.cutoff.isoformat(),
                "authority": {
                    "revision": native.c["source"]["revision"],
                    "content_digest": native.content_digest,
                },
                "request_id": "RESEARCH-R10-001",
                "task_id": "RESEARCH-R10-001-T1",
                "attempt_id": "A1",
            },
        )

    def clock(self, day: date):
        return self.native_inputs.clock(day)


class MarketView:
    def __init__(self, store: NativeStore, snapshot: SnapshotRef):
        self.store, self.snapshot = store, snapshot

    def bar(self, code: str) -> PITDailyBar:
        r = self.store.native(code, self.snapshot.market_date)
        if r is None:
            return PITDailyBar(
                code,
                "UNKNOWN",
                self.snapshot.market_date,
                CoverageStatus.MISSING,
                False,
                False,
                False,
                "NATIVE_MISSING",
                *(None for _ in range(8)),
            )
        valid = all(
            isinstance(r[k], Decimal) and r[k].is_finite() and r[k] > 0
            for k in ["open", "high", "low", "close"]
        )
        exchange = self.store.native_inputs.venues[r["venue_id"]]
        executable = r["is_tradable"] is True and r["reason_code"] == "TRADABLE_OBSERVED"
        return PITDailyBar(
            code,
            exchange,
            self.snapshot.market_date,
            CoverageStatus.TRADING,
            valid,
            executable,
            executable and r["can_buy"] is True,
            r["reason_code"] or "NATIVE_MISSING",
            r["open"],
            r["high"],
            r["low"],
            r["close"],
            r["volume"],
            r["turnover"],
            r["upper_price"],
            r["lower_price"],
        )

    def securities(self) -> tuple[str, ...]:
        return tuple(sorted(self.store.identities))

    def current_bars(self) -> tuple[PITDailyBar, ...]:
        return tuple(self.bar(code) for code in self.securities())

    def history(self, code: str, sessions: int) -> tuple[PITDailyBar, ...]:
        if type(sessions) is not int or sessions <= 0:
            raise ValueError("R10_HISTORY_LENGTH")
        dates = [d for d in self.store.native_inputs.dates if d <= self.snapshot.market_date][
            -sessions:
        ]
        return tuple(
            MarketView(self.store, replace(self.snapshot, market_date=d)).bar(code) for d in dates
        )


class NativeReader:
    def __init__(self, native: NativeInputs):
        self.native, self.store = native, NativeStore(native)
        self.data_content_digest = native.content_digest
        self.data_manifest_sha256 = "sha256:" + MANIFEST_SHA
        self.bundle = SimpleNamespace(
            snapshot=SnapshotRef(
                date(2021, 12, 31),
                native.cutoff,
                native.c["source"]["revision"],
                native.snapshot_id,
            )
        )

    def frames(self, start: date, end: date) -> tuple[StrategyReplayFrame, ...]:
        output = []
        for index, day in enumerate(self.native.dates[:-1]):
            if start <= day <= end:
                execution = self.native.dates[index + 1]
                clock = self.native.clock(day)
                snapshot = SnapshotRef(
                    day,
                    self.native.cutoff,
                    self.native.c["source"]["revision"],
                    self.native.snapshot_id + "-" + str(day),
                )
                output.append(
                    StrategyReplayFrame(
                        snapshot,
                        clock.simulated_close,
                        execution,
                        (),
                        decision_view=MarketView(self.store, snapshot),
                        source=NativeFrameSource(self.store, execution),
                        snapshot_replay_clock_json=clock.to_json(),
                    )
                )
        return tuple(output)


class NativeSession:
    def __init__(self, native: NativeInputs):
        self.reader = NativeReader(native)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def build_feature_panel(native: NativeInputs) -> None:
    db = native.db
    db.execute("""CREATE VIEW source_daily_universe AS SELECT listing_id security_id,
        trade_date market_date FROM daily;
      CREATE VIEW source_daily_ohlcv_none AS SELECT listing_id security_id,trade_date date,
        open,high,low,close,volume,turnover amount FROM daily;
      CREATE VIEW source_price_limit_execution_daily AS SELECT listing_id security_id,
        trade_date execution_date,
        (is_tradable IS TRUE AND can_buy IS TRUE) entry_policy_allows,
        (is_tradable IS TRUE AND reason_code='TRADABLE_OBSERVED' AND upper_price>lower_price
         AND open BETWEEN lower_price AND upper_price) IS TRUE is_executable,
        NULL::DOUBLE upper_limit_pct,NULL::DOUBLE lower_limit_pct,
        upper_price upper_limit_price,lower_price lower_limit_price FROM daily;""")
    query = controlled_feature_query(native)
    db.execute("CREATE TABLE r10_features AS " + query)
    db.execute("CREATE INDEX feature_key ON r10_features(security_id,market_date)")
    db.execute("CHECKPOINT")


def controlled_feature_query(native: NativeInputs) -> str:
    # The original R7 module is byte-bound by its sealed request. Extend its
    # generated SQL at this explicit controlled boundary without changing it.
    query = _feature_panel_query(date(2021, 6, 2), date(2021, 12, 31))
    clause = "CAST(actions.known_at AS DATE) < CAST(actions.effective_date AS DATE)"
    if query.count(clause) != 1 or native.cutoff.tzinfo is None:
        raise ValueError("R10_NATIVE_FEATURE_QUERY_BINDING")
    return query.replace(
        clause, "actions.known_at <= TIMESTAMPTZ '" + native.cutoff.isoformat() + "'"
    )


class NativePanel:
    def __init__(self, native: NativeInputs):
        self.native = native
        self.cache = {}
        registry = load_feature_registry(
            native.project / "config/p3-baseline-features.research.v1.json"
        )
        n, ready, entry, exceptions, unexplained, excluded = native.db.execute("""SELECT count(*),
            count(*) FILTER(WHERE feature_ready),count(*) FILTER(WHERE entry_eligible),
            count(*) FILTER(WHERE exception_active),
            count(*) FILTER(WHERE unexplained_price_transition),
            count(DISTINCT security_id) FILTER(WHERE scope_continuity_excluded)
            FROM r10_features""").fetchone()
        self.report = BaselineFeaturePanelReport(
            "r10-native-controlled-v1",
            registry.registry_version,
            registry.digest,
            native.snapshot_id,
            native.content_digest,
            date(2021, 6, 2),
            date(2021, 12, 31),
            n,
            ready,
            entry,
            exceptions,
            unexplained,
            excluded,
            native.content_digest,
            "sha256:" + MANIFEST_SHA,
        )

    def feature(self, day: date, code: str) -> BaselineFeatureRow | None:
        key = day, code
        if key not in self.cache:
            found = rows(
                self.native.db,
                "SELECT * FROM r10_features WHERE security_id=? AND market_date=?",
                [code, day],
            )
            close = None if not found else found[0]["close_price"]
            valid = close is not None and Decimal(str(close)).is_finite() and close > 0
            self.cache[key] = BaselineFeatureRow.from_mapping(found[0]) if valid else None
        return self.cache[key]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class ControlledReplay(FormalP2ReplayRuntime):
    def __init__(
        self, project: Path, native: NativeInputs, events: Path, predictions: Path | None = None
    ):
        self.native = native
        self.frozen_events = read(events)
        self.predictions = (
            {} if predictions is None else {r["event_id"]: r for r in read(predictions)["rows"]}
        )
        self.event_rows = {r["event"]["event_id"]: r for r in self.frozen_events["rows"]}
        super().__init__(
            project,
            decision_start=date(2021, 6, 2),
            decision_end=date(2021, 12, 30),
            execution_end=date(2021, 12, 31),
            parent_event_run_digest=None,
        )

    def _load_event_population(self, event_root):
        del event_root
        return tuple(
            _EventInput(event_record_from_dict(r["event"]), r["industry_id"], r["listing_id"])
            for r in self.frozen_events["rows"]
        ), self.native.industry

    def _open_data_session(self):
        return NativeSession(self.native)

    def _evaluate_trend_observation(self, event, feature):
        # The original event retains its simulated T-close. The observation is
        # produced now from a fixed native snapshot, so its anchor is source time.
        # This adapter is only an in-memory observation subject, never an EventRecord.
        subject = SimpleNamespace(
            identity=event.identity,
            event_id=event.event_id,
            knowledge_cutoff=event.knowledge_cutoff,
            signal_close=event.knowledge_cutoff,
        )
        return evaluate_trend_stability(
            event=subject,
            feature=feature,
            config=self.trend_config,
            spec=self.trend_spec,
        )

    def _build_feature_panel(self, selected, panel_path):
        del selected, panel_path
        return NativePanel(self.native)

    def _run_id(self, intervention_id):
        return "RESEARCH-R10-001-T1-A1-" + self._variant_id(intervention_id)

    def _validate_intervention_id(self, intervention_id):
        if intervention_id not in (None, "r10-v0", "r10-v1"):
            raise ValueError("R10_VARIANT")

    @staticmethod
    def _variant_id(intervention_id):
        return {None: "C0", "r10-v0": "C1", "r10-v1": "C2"}[intervention_id]

    def _baseline_variant_directory(self):
        return "C0"

    def _held_intervention_id(self, intervention_id):
        return None

    def _code_version(self):
        return "r10-native-controlled-replay-1.0.0"

    def _parameter_payload(self, intervention_id):
        return {
            "strategy": self.registry.card.semantic_digest,
            "intervention": intervention_id,
            "purpose": self.native.contract_digest,
            "events": digest(self.frozen_events),
            "prediction_commitment": digest(self.predictions),
        }

    def _manifest_metadata(self, intervention_id):
        return self._parameter_payload(intervention_id) | {
            "legacy_exposure": "UNKNOWN_DIAGNOSTIC_ONLY",
            "consumer_admission": "NOT_GRANTED",
            "source_members": len(self.native.members),
            "source_clock": self.native.cutoff.isoformat(),
        }

    def _evaluate_entry_decision(
        self, runner, *, event_id, context, trend_state, entry_evidence_ready, intervention_id
    ):
        original = super()._evaluate_entry_decision(
            runner,
            event_id=event_id,
            context=context,
            trend_state=trend_state,
            entry_evidence_ready=entry_evidence_ready,
            intervention_id=None,
        )
        if (
            intervention_id is None
            or original.action.final_action is not GovernedAction.TRIAL_ENTRY
        ):
            return original
        key = "v0" if intervention_id == "r10-v0" else "v1"
        score = self.predictions.get(event_id, {}).get(key)
        passes = score is not None and score >= 0.5
        action = replace(
            original.action,
            final_action=original.action.final_action if passes else GovernedAction.NO_ACTION,
            action_policy_version=original.action.action_policy_version + "|r10-confirmation-v1",
            action_policy_digest=digest(
                {
                    "parent": original.action.action_policy_digest,
                    "contract": self.native.contract_digest,
                    "prediction": self.predictions.get(event_id),
                    "version": key,
                    "threshold": 0.5,
                }
            ),
            reason_codes=original.action.reason_codes
            + (
                "R10_CONFIRM_PASS"
                if passes
                else "R10_CONFIRM_UNAVAILABLE"
                if score is None
                else "R10_CONFIRM_BLOCK",
            ),
        )
        return replace(
            original,
            intervention_id=intervention_id,
            action=action,
            target_maximum_position=original.target_maximum_position if passes else None,
        )

    def _held_event(self, source, frame):
        return EventRecord(
            EventIdentity(
                source.identity.security_id,
                frame.snapshot.market_date,
                source.identity.candidate_rule_id,
                source.identity.candidate_rule_version,
                frame.snapshot.snapshot_id,
            ),
            source.candidate_rule_digest,
            source.snapshot_manifest_digest,
            self.native.cutoff,
            frame.decision_at,
            source.eligibility_evidence_digest,
            source.feature_window_manifest_digest,
            source.isolation_group_id,
            source.split_policy_id,
            source.split_policy_version,
            source.split_policy_digest,
            SplitAssignment.SNAPSHOT_REPLAY,
            snapshot_replay_clock_json=frame.snapshot_replay_clock_json,
        )
