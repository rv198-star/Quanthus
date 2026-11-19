"""Complete capital-constrained P2 replay for the first formal expert strategy."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from quanthus.aggregator import GovernedAction
from quanthus.backtest.baseline_research import BaselineFeaturePanel, BaselineFeatureRow
from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    BacktestRunSpec,
    BacktestSignal,
    EngineEvent,
    EventType,
    OpenExecutionState,
    OrderSide,
    UnitState,
)
from quanthus.backtest.data_provider import ReplayDataProvider, load_replay_data_provider_config
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.portfolio_replay import (
    _child_run,
    _market_value,
    _state_from_snapshot,
    _wealth,
)
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.capital_allocation import (
    AllocationDisposition,
    AvailableCapitalUnit,
    EntryAllocationCandidate,
    allocate_entry_reservations,
)
from quanthus.concentration import ActiveUnitExposure
from quanthus.event_library.contracts import EventIdentity, EventRecord
from quanthus.expert_evaluation import ExpertEvaluationSpec, artifact_from_bytes
from quanthus.expert_evaluation.experts.trend_stability import (
    evaluate_trend_stability,
    load_trend_stability_config,
)
from quanthus.expert_evaluation.production import _EventInput, _find_parent_package, _load_events
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_protocols import RequiredAction
from quanthus.expert_strategy.formal_attribution import FormalReplayEvidence
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import (
    FormalStrategyRunner,
    build_formal_expert_context,
    position_facts_evidence,
)
from quanthus.feature_registry import load_feature_registry
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping
from quanthus.policy import load_policy
from quanthus.position_lifecycle import (
    POSITION_STATE_MACHINE_TRIAL_REDUCTION_VERSION,
    PositionEvidenceKind,
    PositionEvidenceRef,
    PositionLifecycleState,
    PositionStage,
    PositionTransition,
    PositionTransitionKind,
    apply_position_transition,
    initialize_position_lifecycle,
)

DECISION_START = date(2020, 1, 1)
DECISION_END = date(2021, 12, 30)
EXECUTION_END = date(2021, 12, 31)
PARENT_EVENT_RUN_DIGEST = "sha256:8b9d184c2947b82d1a98440e7e6313e3659735fa352ea5dd72ec4b90fc80050c"
JANUARY_SENTINEL_DECISION_DIGEST = (
    "sha256:431c9c9376daf327cd1385e837c62772b72631e11b128806be98084bc7c071d8"
)
MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.00000001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _stable_id(prefix: str, *values: object) -> str:
    normalized = tuple(
        value.isoformat() if isinstance(value, (date, datetime)) else value for value in values
    )
    return f"{prefix}_{hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()[:24]}"


def _decision_digest_in_window(decisions: list[tuple[date, str]], start: date, end: date) -> str:
    return digest_value(
        sorted(item for market_date, item in decisions if start <= market_date <= end)
    )


def _load_parent_industry_mapping(
    event_library: Path, events: tuple[_EventInput, ...]
) -> StaticIndustryMapping:
    industry = load_static_industry_mapping(
        event_library / "adm" / "static_industry_mapping.json",
        expected_version="static-level1-concentration-research-v1",
        required_security_ids=tuple(sorted({item.record.identity.security_id for item in events})),
    )
    if any(
        item.static_industry_id
        != industry.by_security_id[item.record.identity.security_id].level1_industry_id
        for item in events
    ):
        raise ExpertEvaluationError(
            "formal_attribution_industry_mismatch",
            "event industry differs from its verified parent mapping",
        )
    return industry


def _ledger(state: UnitState) -> dict[str, Any]:
    return {
        "unit_id": state.unit_id,
        "target_notional": str(state.target_notional),
        "cash": str(state.cash),
        "security_code": state.security_code,
        "settled_quantity": state.settled_quantity,
        "position_cost": str(state.position_cost),
        "commission_expense": str(state.commission_expense),
        "stamp_duty_expense": str(state.stamp_duty_expense),
        "transfer_fee_expense": str(state.transfer_fee_expense),
        "realized_pnl": str(state.realized_pnl),
        "dividend_income": str(state.dividend_income),
        "external_pool": str(state.external_pool),
        "last_mark_price": (None if state.last_mark_price is None else str(state.last_mark_price)),
        "last_mark_snapshot_id": state.last_mark_snapshot_id,
    }


def _transition(
    runtime: _UnitRuntime,
    kind: PositionTransitionKind,
    occurred_at: datetime,
    reason_codes: tuple[str, ...],
    evidence: PositionEvidenceRef | tuple[PositionEvidenceRef, ...],
) -> PositionTransition:
    if runtime.opportunity_id is None or runtime.formal_security_id is None:
        raise ExpertEvaluationError(
            "formal_position_identity_missing", "position transition lacks identity"
        )
    evidence_items = evidence if isinstance(evidence, tuple) else (evidence,)
    evidence_identity: object = (
        evidence.evidence_id
        if isinstance(evidence, PositionEvidenceRef)
        else tuple(item.evidence_id for item in evidence_items)
    )
    return PositionTransition(
        transition_id=_stable_id(
            "transition",
            runtime.lifecycle.unit_id,
            kind.value,
            occurred_at.isoformat(),
            evidence_identity,
        ),
        kind=kind,
        unit_id=runtime.lifecycle.unit_id,
        opportunity_id=runtime.opportunity_id,
        security_code=str(runtime.ledger.security_code or runtime.panel_security_id),
        occurred_at=occurred_at,
        reason_codes=reason_codes,
        evidence=evidence_items,
    )


@dataclass
class _SellSchedule:
    full_exit: bool
    states_by_date: Mapping[date, UnitState]
    fill_by_date: Mapping[date, EngineEvent]
    carry_by_date: Mapping[date, EngineEvent]
    events: tuple[EngineEvent, ...]


@dataclass
class _UnitRuntime:
    ledger: UnitState
    lifecycle: PositionLifecycleState
    panel_security_id: str | None = None
    formal_security_id: str | None = None
    opportunity_id: str | None = None
    source_event: EventRecord | None = None
    entry_gross: Decimal = Decimal("0")
    entry_fees: Decimal = Decimal("0")
    holding_sessions: int = 0
    deterioration_streak: int = 0
    strategy_reduction_count: int = 0
    cooldown_sessions: int = 0
    sell_schedule: _SellSchedule | None = None


@dataclass(frozen=True)
class _EntryCandidate:
    item: _EventInput
    feature: BaselineFeatureRow | None
    decision: Any
    opportunity_id: str


class FormalP2ReplayRuntime:
    """Keep one expensive data projection open across strategy variants."""

    def __init__(
        self,
        repository_root: Path,
        *,
        decision_start: date = DECISION_START,
        decision_end: date = DECISION_END,
        execution_end: date = EXECUTION_END,
        parent_event_run_digest: str | None = PARENT_EVENT_RUN_DIGEST,
        provider_config: str = "config/replay-data-provider.development.v2.json",
        allowed_provider_partitions: frozenset[str] = frozenset({"development"}),
    ) -> None:
        if not decision_start <= decision_end < execution_end:
            raise ExpertEvaluationError(
                "formal_attribution_scope_mismatch",
                "decision scope must end before the final execution date",
            )
        self.repository_root = repository_root.resolve()
        self.decision_start = decision_start
        self.decision_end = decision_end
        self.execution_end = execution_end
        self.parent_event_run_digest = parent_event_run_digest
        self.provider_config = provider_config
        self.allowed_provider_partitions = allowed_provider_partitions
        self.registry = ExpertStrategyRegistry(self.repository_root)
        self.stack = ExitStack()
        self.temporary = tempfile.TemporaryDirectory(prefix="quanthus-formal-p2-")
        self.stack.callback(self.temporary.cleanup)
        event_root = Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))
        self.events, self.industry = self._load_event_population(event_root)
        if not self.events:
            raise ExpertEvaluationError(
                "formal_attribution_population_empty", "formal event population is empty"
            )
        self.events_by_date: dict[date, list[_EventInput]] = defaultdict(list)
        for item in self.events:
            self.events_by_date[item.record.identity.signal_date].append(item)
        if any(
            len({item.panel_security_id for item in values}) != len(values)
            for values in self.events_by_date.values()
        ):
            raise ExpertEvaluationError(
                "formal_attribution_population_duplicate",
                "formal event population duplicates a security-date",
            )
        self.session = self.stack.enter_context(self._open_data_session())
        self.frames = self.session.reader.frames(self.decision_start, self.decision_end)
        if self.frames[-1].execution_date != self.execution_end:
            raise ExpertEvaluationError(
                "formal_attribution_scope_mismatch",
                f"replay does not end at {self.execution_end.isoformat()}",
            )
        self.feature_registry = load_feature_registry(
            self.repository_root / "config/p3-baseline-features.research.v1.json"
        )
        selected = tuple(sorted({item.panel_security_id for item in self.events}))
        panel_path = Path(self.temporary.name) / "formal-feature-panel.parquet"
        self.panel = self.stack.enter_context(self._build_feature_panel(selected, panel_path))
        self.p0_policy = load_policy(self.repository_root / "config/p0-policy.v1.json")
        self.engine = BacktestEngine(
            load_execution_policy(
                self.repository_root / "config/p2-execution-policy.research.v1.json"
            )
        )
        trend_path = (
            self.repository_root
            / "config/expert-evaluations/specs/"
            / "trend-stability-p4-first-passage.evaluation.v1.json"
        )
        trend_spec = artifact_from_bytes(trend_path.read_bytes())
        if not isinstance(trend_spec, ExpertEvaluationSpec):
            raise ExpertEvaluationError(
                "formal_trend_spec_mismatch", "trend evaluation spec is invalid"
            )
        self.trend_spec = trend_spec
        self.trend_config = load_trend_stability_config(
            self.repository_root / "config/expert-evaluations/experts/trend-stability.v1.json"
        )

    def _open_data_session(self) -> Any:
        provider_config = load_replay_data_provider_config(
            self.repository_root / self.provider_config,
            allowed_partitions=self.allowed_provider_partitions,
        )
        provider = ReplayDataProvider(provider_config)
        return provider.open(
            scope_start=self._provider_scope_start(),
            scope_end=self.decision_end,
        )

    def _build_feature_panel(self, selected: tuple[str, ...], panel_path: Path) -> Any:
        return BaselineFeaturePanel.build(
            self.session.reader.bundle,
            self.feature_registry,
            scope_start=self.decision_start,
            scope_end=self.execution_end,
            output=panel_path,
            data_content_digest=self.session.reader.data_content_digest,
            data_manifest_sha256=self.session.reader.data_manifest_sha256,
            allowed_security_ids=selected,
        )

    def _load_event_population(
        self,
        event_root: Path,
    ) -> tuple[tuple[_EventInput, ...], StaticIndustryMapping]:
        if self.parent_event_run_digest is None:
            raise ExpertEvaluationError(
                "parent_run_binding_invalid",
                "formal replay requires one parent P4 Run unless a bounded subclass "
                "overrides the population source",
            )
        event_library, _ = _find_parent_package(event_root, (self.parent_event_run_digest,))
        events = _load_events(event_library, self.decision_start, self.decision_end)
        return events, _load_parent_industry_mapping(event_library, events)

    def _population_manifest_fields(self) -> dict[str, Any]:
        return {"parent_event_run_digest": self.parent_event_run_digest}

    def _provider_scope_start(self) -> date:
        return self.decision_start

    def close(self) -> None:
        self.stack.close()

    def __enter__(self) -> FormalP2ReplayRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _validate_intervention_id(self, intervention_id: str | None) -> None:
        if intervention_id is not None and intervention_id not in self.registry.intervention_ids:
            raise ExpertEvaluationError(
                "formal_intervention_not_admitted", "formal intervention is not admitted"
            )

    def _code_version(self) -> str:
        return "formal-p2-attribution-replay-1.0.0"

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            "strategy": self.registry.card.semantic_digest,
            "intervention": intervention_id,
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        del intervention_id
        return {}

    def _held_intervention_id(self, intervention_id: str | None) -> str | None:
        return intervention_id

    def _run_id(self, intervention_id: str | None) -> str:
        return (
            f"{self.registry.card.strategy_id}.{self.registry.card.strategy_version}."
            f"{intervention_id or 'native'}"
        )

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        return {
            None: "variant.native-baseline",
            "intervention.trend-stability-abstain": "variant.trend-fallback",
            "intervention.position-risk-base-policy": "variant.position-risk-fallback",
            "intervention.both-experts-fallback": "variant.joint-fallback",
        }[intervention_id]

    def run_variant(
        self,
        intervention_id: str | None,
        output_root: Path,
        *,
        allocation_audit: bool = False,
    ) -> tuple[FormalReplayEvidence, dict[str, bytes]]:
        if type(allocation_audit) is not bool:
            raise ValueError("allocation_audit must be boolean")
        self._validate_intervention_id(intervention_id)
        topology = self.registry.topology_manifest
        held_intervention_id = self._held_intervention_id(intervention_id)
        runner = FormalStrategyRunner(self.registry.card, topology.digest)
        position = self.registry.card.semantic_sections["position_and_execution"]
        unit_notional = Decimal(str(position["unit_notional"]))
        unit_count = int(position["unit_count"])
        initial_equity = unit_notional * unit_count
        initial_as_of = self.frames[0].decision_at.replace(microsecond=0)
        runtimes: dict[str, _UnitRuntime] = {}
        for number in range(1, unit_count + 1):
            unit_id = f"capital-unit-{number:02d}"
            ledger = UnitState.empty(unit_id=unit_id, cash=unit_notional)
            runtimes[unit_id] = _UnitRuntime(
                ledger=ledger,
                lifecycle=initialize_position_lifecycle(
                    ledger_state=ledger,
                    as_of=initial_as_of,
                    state_machine_version=POSITION_STATE_MACHINE_TRIAL_REDUCTION_VERSION,
                ),
            )
        run = BacktestRunSpec(
            code_version=self._code_version(),
            parameter_hash=digest_value(self._parameter_payload(intervention_id)),
            data_snapshot_id=self.session.reader.bundle.snapshot.snapshot_id,
            feature_version=self.panel.report.digest,
            rule_version=self.registry.card.strategy_version,
            run_id=self._run_id(intervention_id),
            execution_policy_version=self.engine.policy.policy_version,
            execution_profile_id=self.engine.policy.active_profile.profile_id,
            schedule_version=self.engine.policy.active_profile.schedule_version,
            data_content_digest=self.session.reader.data_content_digest,
            data_manifest_sha256=self.session.reader.data_manifest_sha256,
        )
        decisions: list[dict[str, Any]] = []
        allocation_frames: list[dict[str, Any]] | None = [] if allocation_audit else None
        transitions: list[PositionTransition] = []
        events: list[EngineEvent] = []
        equity: list[dict[str, Any]] = [
            {
                "market_date": self.frames[0].snapshot.market_date.isoformat(),
                "system_equity": str(initial_equity),
                "market_value": "0.00",
                "invested_capital_ratio": "0",
                "active_unit_count": 0,
            }
        ]

        for frame_index, frame in enumerate(self.frames):
            self._review_positions(
                frame_index,
                frame,
                runtimes,
                runner,
                held_intervention_id,
                run,
                decisions,
                transitions,
                events,
            )
            candidates = self._entry_candidates(frame, runner, intervention_id)
            audit_options = (
                {} if allocation_frames is None else {"allocation_audit": allocation_frames}
            )
            self._execute_frame(
                frame_index,
                frame,
                runtimes,
                candidates,
                unit_notional,
                position,
                run,
                decisions,
                transitions,
                events,
                **audit_options,
            )
            market_value = _money(
                sum((_market_value(item.ledger) for item in runtimes.values()), Decimal("0"))
            )
            system_equity = self._portfolio_equity(runtimes)
            equity.append(
                {
                    "market_date": frame.execution_date.isoformat(),
                    "system_equity": str(system_equity),
                    "market_value": str(market_value),
                    "invested_capital_ratio": str(_rate(market_value / system_equity)),
                    "active_unit_count": sum(
                        item.ledger.settled_quantity > 0 for item in runtimes.values()
                    ),
                }
            )

        metrics, daily_returns = self._metrics(
            initial_equity,
            equity,
            events,
            decisions,
            output_root,
            self._metrics_intervention_id(intervention_id),
            baseline_variant_directory=self._baseline_variant_directory(),
        )
        ordered_events = tuple(sorted(events, key=lambda item: (item.event_time, item.event_id)))
        ordered_decisions = tuple(
            sorted(decisions, key=lambda item: (item["market_date"], item["decision_key"]))
        )
        final_ledgers = tuple(_ledger(runtimes[key].ledger) for key in sorted(runtimes))
        event_payload = [item.to_dict() for item in ordered_events]
        decision_payload = list(ordered_decisions)
        reconciliation = {
            "initial_equity": str(initial_equity),
            "ending_equity": equity[-1]["system_equity"],
            "unit_wealth": str(self._portfolio_equity(runtimes)),
            "decision_count": len(decision_payload),
            "engine_event_count": len(event_payload),
            "fill_count": sum(item.event_type is EventType.FILL for item in ordered_events),
            "ledger_count": sum(
                item.event_type is EventType.LEDGER_ENTRY for item in ordered_events
            ),
            "sealed_partition_access": False,
            "reconciled": equity[-1]["system_equity"] == str(self._portfolio_equity(runtimes)),
        }
        if not reconciliation["reconciled"]:
            raise ExpertEvaluationError(
                "incomplete_p2_ledger", "formal replay equity does not reconcile"
            )
        artifacts = {
            "decisions.json": canonical_json_bytes(decision_payload),
            "engine-events.json": canonical_json_bytes(event_payload),
            "equity.json": canonical_json_bytes(equity),
            "daily-returns.json": canonical_json_bytes(daily_returns),
            "final-ledgers.json": canonical_json_bytes(final_ledgers),
            "reconciliation.json": canonical_json_bytes(reconciliation),
            **self._portfolio_artifacts(),
        }
        replay_digest = digest_value(
            {name: digest_value(json.loads(payload)) for name, payload in sorted(artifacts.items())}
        )
        evidence = FormalReplayEvidence(
            variant_id=self._variant_id(intervention_id),
            intervention_id=intervention_id,
            metrics=metrics,
            replay_digest=replay_digest,
            reconciliation_digest=digest_value(reconciliation),
            decision_trace_digest=digest_value(decision_payload),
            opportunity_trace_digest=digest_value(
                [item for item in decision_payload if item["surface"] == "entry"]
            ),
            daily_return_artifact_digest=digest_value(daily_returns),
        )
        artifacts["manifest.json"] = canonical_json_bytes(
            {
                "schema_version": "quanthus.formal-p2-attribution-replay.v1",
                "variant_id": evidence.variant_id,
                "intervention_id": intervention_id,
                "strategy_card_digest": self.registry.card.semantic_digest,
                "topology_manifest_digest": topology.digest,
                **self._population_manifest_fields(),
                "scope": {
                    "decision_start": self.decision_start.isoformat(),
                    "decision_end": self.decision_end.isoformat(),
                    "execution_end": self.execution_end.isoformat(),
                },
                "metrics": {key: str(value) for key, value in sorted(metrics.items())},
                "replay_digest": replay_digest,
                "sealed_partition_access": False,
                "execution_authorized": False,
                **self._manifest_metadata(intervention_id),
            }
        )
        if allocation_frames is not None:
            # Operational observation is separate from the sealed legacy replay identity.
            # No allocator/evaluator is called here, and no original byte is rewritten.
            identity = {
                "run": run.manifest({}),
                "execution_policy": json.loads(json.dumps(asdict(self.engine.policy), default=str)),
                "strategy_card_digest": self.registry.card.semantic_digest,
                "topology_manifest_digest": topology.digest,
                "industry_artifact_digest": self.industry.artifact_digest,
                "population": self._population_manifest_fields(),
                "source_metadata": self._manifest_metadata(intervention_id),
                "scope": {
                    "decision_start": self.decision_start.isoformat(),
                    "decision_end": self.decision_end.isoformat(),
                    "execution_end": self.execution_end.isoformat(),
                },
                "code_and_config_sha256": {
                    str(path.relative_to(self.repository_root)): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for folder, pattern in (("src/quanthus", "*.py"), ("config", "*.json"))
                    for path in sorted((self.repository_root / folder).rglob(pattern))
                },
            }
            audit = {
                "schema_version": "quanthus.entry-allocation-audit.v1",
                "purpose": "ALREADY_COMPUTED_ALLOCATION_OBSERVATION_ONLY",
                "execution_authorized": False,
                "historical_PIT_claim": False,
                "identity": identity,
                "legacy_replay_digest": replay_digest,
                "legacy_artifacts_sha256": {
                    name: hashlib.sha256(payload).hexdigest()
                    for name, payload in sorted(artifacts.items())
                },
                "frames": allocation_frames,
            }
            artifacts["entry-allocation-audit.json"] = canonical_json_bytes(
                {**audit, "audit_digest": digest_value(audit)}
            )
        return evidence, artifacts

    def _entry_candidates(
        self,
        frame: StrategyReplayFrame,
        runner: FormalStrategyRunner,
        intervention_id: str | None,
    ) -> tuple[_EntryCandidate, ...]:
        output: list[_EntryCandidate] = []
        for item in sorted(
            self.events_by_date.get(frame.snapshot.market_date, ()),
            key=lambda value: value.record.event_id,
        ):
            feature = self.panel.feature(item.record.identity.signal_date, item.panel_security_id)
            formal_feature = (
                None
                if feature is None
                else replace(feature, security_id=item.record.identity.security_id)
            )
            observation = self._evaluate_trend_observation(item.record, formal_feature)
            context = build_formal_expert_context(
                event=item.record,
                feature=formal_feature,
                feature_registry=self.feature_registry,
                panel_report=self.panel.report,
                position_state="empty",
                trend_observation_digest=observation.digest,
            )
            decision = self._evaluate_entry_decision(
                runner,
                event_id=item.record.event_id,
                context=context,
                trend_state=(None if observation.raw_value is None else str(observation.raw_value)),
                entry_evidence_ready=formal_feature is not None and formal_feature.entry_eligible,
                intervention_id=intervention_id,
            )
            output.append(
                _EntryCandidate(
                    item,
                    feature,
                    decision,
                    _stable_id("opportunity", item.record.event_id),
                )
            )
        return tuple(output)

    def _evaluate_trend_observation(self, event: EventRecord, feature: Any) -> Any:
        return evaluate_trend_stability(
            event=event,
            feature=feature,
            config=self.trend_config,
            spec=self.trend_spec,
        )

    def _evaluate_entry_decision(
        self,
        runner: FormalStrategyRunner,
        *,
        event_id: str,
        context: Any,
        trend_state: str | None,
        entry_evidence_ready: bool,
        intervention_id: str | None,
    ) -> Any:
        return runner.evaluate_entry(
            event_id=event_id,
            context=context,
            trend_state=trend_state,
            entry_evidence_ready=entry_evidence_ready,
            intervention_id=intervention_id,
        )

    def _review_positions(
        self,
        frame_index: int,
        frame: StrategyReplayFrame,
        runtimes: Mapping[str, _UnitRuntime],
        runner: FormalStrategyRunner,
        intervention_id: str | None,
        run: BacktestRunSpec,
        decisions: list[dict[str, Any]],
        transitions: list[PositionTransition],
        events: list[EngineEvent],
    ) -> None:
        base = self.registry.card.semantic_sections["position_and_execution"][
            "base_position_management"
        ]
        for unit_id in sorted(runtimes):
            runtime = runtimes[unit_id]
            if (
                runtime.lifecycle.stage not in {PositionStage.TRIAL, PositionStage.DYNAMIC}
                or runtime.sell_schedule is not None
            ):
                continue
            if (
                runtime.panel_security_id is None
                or runtime.formal_security_id is None
                or runtime.source_event is None
            ):
                raise ExpertEvaluationError(
                    "formal_position_identity_missing", "held position lacks source identity"
                )
            feature = self.panel.feature(frame.snapshot.market_date, runtime.panel_security_id)
            marked_return = (
                None
                if runtime.ledger.last_mark_price is None or runtime.entry_gross <= 0
                else _rate(
                    (_market_value(runtime.ledger) - runtime.entry_gross - runtime.entry_fees)
                    / (runtime.entry_gross + runtime.entry_fees)
                )
            )
            structural = bool(
                feature is not None
                and feature.total_return_index is not None
                and feature.sma60 is not None
                and feature.sma20 is not None
                and feature.total_return_index < feature.sma60
                and feature.sma20 <= feature.sma60
            )
            runtime.deterioration_streak = runtime.deterioration_streak + 1 if structural else 0
            base_required = RequiredAction.NONE
            base_reason = "base_position_management_clear"
            if runtime.holding_sessions >= int(base["maximum_holding_sessions"]):
                base_required = RequiredAction.FORCE_LIQUIDATE
                base_reason = "base_maximum_holding_exit"
            elif marked_return is not None and marked_return <= -Decimal(
                str(base["constraint_stop_loss"])
            ):
                base_required = RequiredAction.FORCE_LIQUIDATE
                base_reason = "base_constraint_stop_exit"
            elif runtime.holding_sessions >= int(
                base["minimum_holding_sessions"]
            ) and runtime.deterioration_streak >= int(base["deterioration_sessions"]):
                base_required = RequiredAction.FORCE_LIQUIDATE
                base_reason = "base_structural_deterioration_exit"
            facts = HeldPositionFacts(
                holding_sessions=max(1, runtime.holding_sessions),
                marked_return=marked_return,
                prior_strategy_reduction_count=runtime.strategy_reduction_count,
                evidence=position_facts_evidence(
                    unit_id=unit_id,
                    holding_sessions=max(1, runtime.holding_sessions),
                    marked_return=marked_return,
                    prior_strategy_reduction_count=runtime.strategy_reduction_count,
                ),
            )
            review_event = self._held_event(runtime.source_event, frame)
            formal_feature = (
                None
                if feature is None
                else replace(feature, security_id=runtime.formal_security_id)
            )
            context = build_formal_expert_context(
                event=review_event,
                feature=formal_feature,
                feature_registry=self.feature_registry,
                panel_report=self.panel.report,
                position_state=runtime.lifecycle.stage.value,
                trend_observation_digest=digest_value(
                    {"held_review": review_event.event_id, "trend": "not_requested"}
                ),
                position_facts=facts,
            )
            decision = runner.evaluate_held(
                event_id=review_event.event_id,
                context=context,
                position_facts=facts,
                base_required_action=base_required,
                base_reason_code=base_reason,
                intervention_id=intervention_id,
            )
            decisions.append(
                self._decision_row(
                    frame.snapshot.market_date,
                    f"held:{unit_id}",
                    "held",
                    decision,
                    unit_id,
                    False,
                    False,
                )
            )
            if decision.action.final_action not in {
                GovernedAction.FULL_EXIT,
                GovernedAction.REDUCE,
            }:
                continue
            full_exit = decision.action.final_action is GovernedAction.FULL_EXIT
            quantity = runtime.ledger.settled_quantity
            if not full_exit:
                target = decision.target_maximum_position
                if target is None or runtime.ledger.last_mark_price is None:
                    raise ExpertEvaluationError(
                        "formal_reduction_target_missing", "reduction lacks a target"
                    )
                target_quantity = (
                    int(
                        (
                            runtime.ledger.target_notional
                            * target
                            / runtime.ledger.last_mark_price
                            / Decimal("100")
                        ).to_integral_value(rounding=ROUND_DOWN)
                    )
                    * 100
                )
                quantity = runtime.ledger.settled_quantity - target_quantity
                quantity = min(quantity, runtime.ledger.settled_quantity - 100)
                if quantity <= 0:
                    continue
            schedule = self._sell_schedule(
                frame_index, frame, runtime, run, quantity, full_exit, decision.digest
            )
            runtime.sell_schedule = schedule
            events.extend(schedule.events)
            if full_exit:
                transition = _transition(
                    runtime,
                    PositionTransitionKind.EXIT_REQUESTED,
                    frame.decision_at,
                    tuple(decision.action.reason_codes),
                    PositionEvidenceRef(
                        PositionEvidenceKind.DECISION,
                        decision.digest,
                        self.registry.card.strategy_version,
                    ),
                )
                runtime.lifecycle = apply_position_transition(
                    runtime.lifecycle,
                    transition,
                    ledger_before=runtime.ledger,
                    ledger_after=runtime.ledger,
                )
                transitions.append(transition)

    @staticmethod
    def _held_event(source: EventRecord, frame: StrategyReplayFrame) -> EventRecord:
        identity = EventIdentity(
            source.identity.security_id,
            frame.snapshot.market_date,
            source.identity.candidate_rule_id,
            source.identity.candidate_rule_version,
            frame.snapshot.snapshot_id,
        )
        return EventRecord(
            identity,
            source.candidate_rule_digest,
            source.snapshot_manifest_digest,
            frame.decision_at,
            frame.decision_at,
            source.eligibility_evidence_digest,
            source.feature_window_manifest_digest,
            source.isolation_group_id,
            source.split_policy_id,
            source.split_policy_version,
            source.split_policy_digest,
            source.split_assignment,
        )

    def _sell_schedule(
        self,
        frame_index: int,
        frame: StrategyReplayFrame,
        runtime: _UnitRuntime,
        run: BacktestRunSpec,
        quantity: int,
        full_exit: bool,
        decision_digest: str,
    ) -> _SellSchedule:
        assert runtime.panel_security_id is not None
        signal = self._signal(
            run,
            frame,
            runtime.lifecycle.unit_id,
            runtime.panel_security_id,
            OrderSide.SELL,
            quantity,
            Decimal("-1"),
            "formal_full_exit" if full_exit else "formal_reduce",
            decision_digest,
        )
        sessions = []
        actions = []
        mark = runtime.ledger.last_mark_price
        for candidate in self.frames[frame_index:]:
            if mark is None:
                raise ExpertEvaluationError(
                    "formal_position_mark_missing", "sell sequence lacks prior mark"
                )
            candidate_actions = candidate.actions(runtime.panel_security_id, mark)
            self._validate_carried_sell_actions(candidate_actions)
            actions.extend(candidate_actions)
            if candidate_actions:
                mark = candidate_actions[-1].post_action_mark_price
            session = candidate.session(runtime.panel_security_id)
            sessions.append(session)
            if session.close_price is not None:
                mark = session.close_price
            if session.open_execution_state not in {
                OpenExecutionState.SUSPENDED,
                OpenExecutionState.NON_TRADING_OBSERVED,
                OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
                OpenExecutionState.MISSING_EVIDENCE,
            }:
                break
        result = self.engine.run_sell_sequence(
            run=_child_run(
                run,
                suffix=f"{runtime.lifecycle.unit_id}.sell.{frame.snapshot.market_date}",
                data_snapshot_id=frame.snapshot.snapshot_id,
            ),
            signal=signal,
            sessions=tuple(sessions),
            opening_state=runtime.ledger,
            corporate_actions=tuple(actions),
        )
        states = {}
        fills = {}
        carries = {}
        for event in result.events:
            if event.event_type is EventType.DAILY_SNAPSHOT:
                market_date = date.fromisoformat(str(event.payload["session_date"]))
                states[market_date] = _state_from_snapshot(
                    event, target_notional=runtime.ledger.target_notional
                )
            elif event.event_type is EventType.FILL:
                fills[event.event_time.date()] = event
            elif event.event_type is EventType.ORDER_STATUS and str(
                event.payload.get("transition", "")
            ).endswith("to_carried"):
                carries[event.event_time.date()] = event
        return _SellSchedule(full_exit, states, fills, carries, result.events)

    def _validate_carried_sell_actions(self, actions: Any) -> None:
        if any(item.action.bonus_share_ratio != 0 for item in actions):
            raise ExpertEvaluationError(
                "formal_sell_bonus_share_unsupported",
                "carried sell encountered a quantity-changing action",
            )

    def _execute_frame(
        self,
        frame_index: int,
        frame: StrategyReplayFrame,
        runtimes: Mapping[str, _UnitRuntime],
        candidates: tuple[_EntryCandidate, ...],
        unit_notional: Decimal,
        position: Mapping[str, Any],
        run: BacktestRunSpec,
        decisions: list[dict[str, Any]],
        transitions: list[PositionTransition],
        events: list[EngineEvent],
        *,
        allocation_audit: list[dict[str, Any]] | None = None,
    ) -> None:
        active = tuple(
            ActiveUnitExposure(
                item.lifecycle.unit_id,
                str(item.opportunity_id),
                item.lifecycle.stage,
                item.lifecycle.digest,
                self.industry.by_security_id[str(item.formal_security_id)],
            )
            for item in runtimes.values()
            if item.lifecycle.stage
            in {PositionStage.TRIAL, PositionStage.DYNAMIC, PositionStage.EXITING}
        )
        available = tuple(
            AvailableCapitalUnit(item.lifecycle, item.ledger)
            for item in sorted(runtimes.values(), key=lambda value: value.lifecycle.unit_id)
            if item.lifecycle.stage is PositionStage.EMPTY
        )[: max(0, int(position["maximum_active_units"]) - len(active))]
        requested = tuple(
            EntryAllocationCandidate(
                _stable_id("allocation", item.opportunity_id),
                item.opportunity_id,
                item.decision.action.aggregate_score,
                self.industry.by_security_id[item.item.record.identity.security_id],
            )
            for item in candidates
            if item.decision.action.final_action is GovernedAction.TRIAL_ENTRY
        )
        allocation = allocate_entry_reservations(
            self.p0_policy,
            market_date=frame.snapshot.market_date,
            candidates=requested,
            available_units=available,
            active_exposures=active,
            accepted_entries=(),
        )
        if allocation_audit is not None:
            allocation_payload = allocation.to_dict()
            allocation_audit.append(
                {
                    "market_date": frame.snapshot.market_date.isoformat(),
                    "decision_at": frame.decision_at.isoformat(),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "snapshot_id": frame.snapshot.snapshot_id,
                    "snapshot_source_revision": frame.snapshot.source_revision,
                    "execution_date": frame.execution_date.isoformat(),
                    "knowledge_cutoff": frame.snapshot.knowledge_cutoff.isoformat(),
                    "available_units": [
                        {
                            "unit_id": item.lifecycle.unit_id,
                            "lifecycle_digest": item.lifecycle.digest,
                            "ledger_digest": digest_value(_ledger(item.ledger)),
                        }
                        for item in available
                    ],
                    "active_lifecycle_digests": [item.lifecycle_digest for item in active],
                    "entries": [
                        {
                            "entry_key": f"entry:{item.item.record.event_id}",
                            "opportunity_id": item.opportunity_id,
                            "formal_decision_digest": item.decision.digest,
                            "security_id": item.item.record.identity.security_id,
                            "panel_security_id": item.item.panel_security_id,
                            "final_action": item.decision.action.final_action.value,
                            "allocation_decision_id": (
                                _stable_id("allocation", item.opportunity_id)
                                if item.decision.action.final_action is GovernedAction.TRIAL_ENTRY
                                else None
                            ),
                        }
                        for item in candidates
                    ],
                    "allocation": allocation_payload,
                    "allocation_digest": digest_value(allocation_payload),
                }
            )
        by_opportunity = {item.opportunity_id: item for item in allocation.decisions}
        by_unit = {
            str(item.considered_unit_id): next(
                candidate
                for candidate in candidates
                if candidate.opportunity_id == item.opportunity_id
            )
            for item in allocation.decisions
            if item.disposition is AllocationDisposition.RESERVED
            and item.considered_unit_id is not None
        }
        self._prepare_entry_sizing(frame, runtimes)
        for unit_id in sorted(runtimes):
            runtime = runtimes[unit_id]
            if runtime.sell_schedule is not None:
                self._apply_sell_schedule(
                    frame, runtime, transitions, runtime.sell_schedule, events
                )
                continue
            if runtime.lifecycle.stage in {PositionStage.TRIAL, PositionStage.DYNAMIC}:
                self._mark(frame_index, frame, runtime, run, events)
                continue
            candidate = by_unit.get(unit_id)
            if runtime.lifecycle.stage is PositionStage.EMPTY and candidate is not None:
                quantity = self._size_entry(frame, runtime, candidate, unit_notional, position)
                filled = False
                if quantity > 0:
                    signal = self._signal(
                        run,
                        frame,
                        unit_id,
                        candidate.item.panel_security_id,
                        OrderSide.BUY,
                        quantity,
                        candidate.decision.action.aggregate_score,
                        "formal_trial_entry",
                        candidate.decision.digest,
                    )
                    result = self.engine.run_buy_session(
                        run=_child_run(
                            run,
                            suffix=f"{unit_id}.buy.{frame_index}",
                            data_snapshot_id=frame.snapshot.snapshot_id,
                        ),
                        signal=signal,
                        session=frame.session(candidate.item.panel_security_id),
                        opening_state=runtime.ledger,
                    )
                    events.extend(result.events)
                    snapshot = next(
                        item
                        for item in reversed(result.events)
                        if item.event_type is EventType.DAILY_SNAPSHOT
                    )
                    before = runtime.ledger
                    runtime.ledger = _state_from_snapshot(snapshot, target_notional=unit_notional)
                    fill = next(
                        (item for item in result.events if item.event_type is EventType.FILL), None
                    )
                    if fill is not None:
                        self._validate_entry_fill(candidate, fill)
                        runtime.panel_security_id = candidate.item.panel_security_id
                        runtime.formal_security_id = candidate.item.record.identity.security_id
                        runtime.opportunity_id = candidate.opportunity_id
                        runtime.source_event = candidate.item.record
                        runtime.entry_gross = Decimal(str(fill.payload["gross_amount"]))
                        runtime.entry_fees = Decimal(str(fill.payload["total_fees"]))
                        runtime.holding_sessions = 1
                        transition = _transition(
                            runtime,
                            PositionTransitionKind.ENTRY_FILLED,
                            fill.event_time,
                            ("formal_trial_entry_filled",),
                            PositionEvidenceRef(
                                PositionEvidenceKind.FILL,
                                str(fill.payload["fill_id"]),
                                self.engine.policy.policy_version,
                            ),
                        )
                        runtime.lifecycle = apply_position_transition(
                            runtime.lifecycle,
                            transition,
                            ledger_before=before,
                            ledger_after=runtime.ledger,
                        )
                        transitions.append(transition)
                        filled = True
                decisions.append(
                    self._decision_row(
                        frame.snapshot.market_date,
                        f"entry:{candidate.item.record.event_id}",
                        "entry",
                        candidate.decision,
                        unit_id,
                        True,
                        filled,
                    )
                )

                continue
            if runtime.lifecycle.stage is PositionStage.CLOSED:
                runtime.cooldown_sessions += 1
                if runtime.cooldown_sessions >= int(position["cooldown_sessions"]):
                    settlement = next(
                        (
                            item
                            for item in reversed(events)
                            if item.event_type is EventType.UNIT_SETTLEMENT
                            and item.payload.get("unit_id") == unit_id
                        ),
                        None,
                    )
                    if settlement is None:
                        raise ExpertEvaluationError(
                            "incomplete_p2_ledger", "closed unit lacks settlement"
                        )
                    transition = _transition(
                        runtime,
                        PositionTransitionKind.COOLDOWN_COMPLETED,
                        datetime.combine(
                            frame.execution_date,
                            time(15, 0),
                            tzinfo=MARKET_TIMEZONE,
                        ),
                        ("cooldown_completed",),
                        (
                            PositionEvidenceRef(
                                PositionEvidenceKind.UNIT_SETTLEMENT,
                                settlement.event_id,
                                self.engine.policy.policy_version,
                            ),
                            PositionEvidenceRef(
                                PositionEvidenceKind.CALENDAR,
                                _stable_id("calendar", unit_id, frame.execution_date),
                                run.schedule_version,
                            ),
                        ),
                    )
                    runtime.lifecycle = apply_position_transition(
                        runtime.lifecycle,
                        transition,
                        ledger_before=runtime.ledger,
                        ledger_after=runtime.ledger,
                    )
                    transitions.append(transition)
                    runtime.cooldown_sessions = 0
                    runtime.panel_security_id = None
                    runtime.formal_security_id = None
                    runtime.opportunity_id = None
                    runtime.source_event = None
        recorded = {item["decision_key"] for item in decisions}
        for candidate in candidates:
            if f"entry:{candidate.item.record.event_id}" in recorded:
                continue
            decision = by_opportunity.get(candidate.opportunity_id)
            decisions.append(
                self._decision_row(
                    frame.snapshot.market_date,
                    f"entry:{candidate.item.record.event_id}",
                    "entry",
                    candidate.decision,
                    (
                        "capital-pool"
                        if decision is None or decision.considered_unit_id is None
                        else str(decision.considered_unit_id)
                    ),
                    decision is not None,
                    False,
                )
            )

        self._settle_portfolio_frame(frame, runtimes)

    def _prepare_entry_sizing(
        self, frame: StrategyReplayFrame, runtimes: Mapping[str, _UnitRuntime]
    ) -> None:
        pass

    def _size_entry(
        self,
        frame: StrategyReplayFrame,
        runtime: _UnitRuntime,
        candidate: _EntryCandidate,
        unit_notional: Decimal,
        position: Mapping[str, Any],
    ) -> int:
        target = candidate.decision.target_maximum_position or Decimal(
            str(position["base_trial_position_fraction"])
        )
        close = None if candidate.feature is None else candidate.feature.close_price
        if close is None:
            return 0
        return (
            int(
                (unit_notional * target / close / Decimal("100")).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )
            * 100
        )

    def _validate_entry_fill(self, candidate: _EntryCandidate, fill: EngineEvent) -> None:
        pass

    def _settle_portfolio_frame(
        self, frame: StrategyReplayFrame, runtimes: Mapping[str, _UnitRuntime]
    ) -> None:
        pass

    def _portfolio_equity(self, runtimes: Mapping[str, _UnitRuntime]) -> Decimal:
        return _money(sum((_wealth(item.ledger) for item in runtimes.values()), Decimal("0")))

    def _portfolio_artifacts(self) -> dict[str, bytes]:
        return {}

    def _observe_full_exit(
        self, runtime: _UnitRuntime, schedule: _SellSchedule, frame: StrategyReplayFrame
    ) -> None:
        pass

    def _apply_sell_schedule(
        self,
        frame: StrategyReplayFrame,
        runtime: _UnitRuntime,
        transitions: list[PositionTransition],
        schedule: _SellSchedule,
        _events: list[EngineEvent],
    ) -> None:
        execution_date = frame.execution_date
        if execution_date not in schedule.states_by_date:
            return
        before = runtime.ledger
        runtime.ledger = schedule.states_by_date[execution_date]
        runtime.holding_sessions += 1
        fill = schedule.fill_by_date.get(execution_date)
        if fill is None:
            if schedule.full_exit:
                carry = schedule.carry_by_date.get(execution_date)
                if carry is None:
                    raise ExpertEvaluationError(
                        "incomplete_p2_ledger", "carried full exit lacks status evidence"
                    )
                transition = _transition(
                    runtime,
                    PositionTransitionKind.EXIT_CARRIED,
                    carry.event_time,
                    (carry.reason_code,),
                    PositionEvidenceRef(
                        PositionEvidenceKind.ORDER_STATUS,
                        carry.event_id,
                        self.engine.policy.policy_version,
                    ),
                )
                runtime.lifecycle = apply_position_transition(
                    runtime.lifecycle,
                    transition,
                    ledger_before=before,
                    ledger_after=runtime.ledger,
                )
                transitions.append(transition)
            return
        kind = (
            PositionTransitionKind.EXIT_FILLED
            if schedule.full_exit
            else PositionTransitionKind.REDUCE_FILLED
        )
        transition = _transition(
            runtime,
            kind,
            fill.event_time,
            ("formal_full_exit_filled" if schedule.full_exit else "formal_reduce_filled",),
            PositionEvidenceRef(
                PositionEvidenceKind.FILL,
                str(fill.payload["fill_id"]),
                self.engine.policy.policy_version,
            ),
        )
        runtime.lifecycle = apply_position_transition(
            runtime.lifecycle,
            transition,
            ledger_before=before,
            ledger_after=runtime.ledger,
        )
        transitions.append(transition)
        runtime.sell_schedule = None
        if schedule.full_exit:
            self._observe_full_exit(runtime, schedule, frame)
            runtime.cooldown_sessions = 0
        else:
            runtime.strategy_reduction_count += 1

    def _mark(
        self,
        frame_index: int,
        frame: StrategyReplayFrame,
        runtime: _UnitRuntime,
        run: BacktestRunSpec,
        events: list[EngineEvent],
    ) -> None:
        assert runtime.panel_security_id is not None
        mark = runtime.ledger.last_mark_price
        if mark is None:
            raise ExpertEvaluationError("formal_position_mark_missing", "position lacks mark")
        for action_index, step in enumerate(
            frame.actions(runtime.panel_security_id, mark), start=1
        ):
            application = self.engine.apply_corporate_action(
                run=_child_run(
                    run,
                    suffix=(f"{runtime.lifecycle.unit_id}.action.{frame_index}.{action_index}"),
                    data_snapshot_id=step.action.snapshot_id,
                ),
                opening_state=runtime.ledger,
                action=step.action,
                applied_at=step.applied_at,
                post_action_mark_price=step.post_action_mark_price,
                mark_snapshot_id=step.mark_snapshot_id,
                snapshot_replay_clock_json=step.snapshot_replay_clock_json,
            )
            runtime.ledger = application.state
            events.extend(application.events)
            mark = step.post_action_mark_price
        session = frame.session(runtime.panel_security_id)
        result = self.engine.mark_position_session(
            run=_child_run(
                run,
                suffix=f"{runtime.lifecycle.unit_id}.mark.{frame_index}",
                data_snapshot_id=session.snapshot_id,
            ),
            session=session,
            opening_state=runtime.ledger,
        )
        runtime.ledger = result.state
        events.extend(result.events)
        runtime.holding_sessions += 1

    def _signal(
        self,
        run: BacktestRunSpec,
        frame: StrategyReplayFrame,
        unit_id: str,
        security_id: str,
        side: OrderSide,
        quantity: int,
        score: Decimal,
        reason: str,
        decision_digest: str,
    ) -> BacktestSignal:
        return BacktestSignal(
            signal_id=_stable_id(
                "signal", run.run_id, frame.snapshot.snapshot_id, unit_id, side.value, reason
            ),
            snapshot=frame.snapshot,
            decision_at=frame.decision_at,
            profile_id=run.execution_profile_id,
            window_id=None,
            security_code=security_id,
            side=side,
            unit_id=unit_id,
            quantity=quantity,
            aggregate_score=score,
            reason_code=reason,
            decision_version=f"{self.registry.card.strategy_version}|{decision_digest}",
            snapshot_replay_clock_json=frame.snapshot_replay_clock_json,
        )

    @staticmethod
    def _decision_row(
        market_date: date,
        decision_key: str,
        surface: str,
        decision: Any,
        unit_id: str,
        allocated: bool,
        filled: bool,
    ) -> dict[str, Any]:
        return {
            "market_date": market_date.isoformat(),
            "decision_key": decision_key,
            "surface": surface,
            "unit_id": unit_id,
            "formal_decision_digest": decision.digest,
            "final_action": decision.action.final_action.value,
            "aggregate_score": str(decision.action.aggregate_score),
            "target_maximum_position": (
                None
                if decision.target_maximum_position is None
                else str(decision.target_maximum_position)
            ),
            "reason_codes": list(decision.action.reason_codes),
            "allocated": allocated,
            "filled": filled,
        }

    def _baseline_variant_directory(self) -> str:
        return "variant.native-baseline"

    def _metrics_intervention_id(self, intervention_id: str | None) -> str | None:
        return intervention_id

    @staticmethod
    def _metrics(
        initial_equity: Decimal,
        equity: list[dict[str, Any]],
        events: list[EngineEvent],
        decisions: list[dict[str, Any]],
        output_root: Path,
        intervention_id: str | None,
        baseline_variant_directory: str = "variant.native-baseline",
    ) -> tuple[dict[str, Decimal], list[dict[str, str]]]:
        values = [Decimal(item["system_equity"]) for item in equity]
        returns = [
            _rate(current / prior - Decimal("1"))
            for prior, current in zip(values, values[1:], strict=False)
        ]
        peak = values[0]
        maximum_drawdown = Decimal("0")
        for value in values[1:]:
            peak = max(peak, value)
            maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)
        ordered_returns = sorted(returns)
        fifth = ordered_returns[max(0, math.floor((len(ordered_returns) - 1) * 0.05))]
        fills = [item for item in events if item.event_type is EventType.FILL]
        gross = sum((Decimal(str(item.payload["gross_amount"])) for item in fills), Decimal("0"))
        fees = sum((Decimal(str(item.payload["total_fees"])) for item in fills), Decimal("0"))
        replay_points = equity[1:]
        native: dict[tuple[str, str], str] = {}
        if intervention_id is not None:
            native_path = output_root.parent / baseline_variant_directory / "decisions.json"
            if not native_path.is_file():
                raise ExpertEvaluationError(
                    "formal_baseline_missing", "intervention requires native decisions"
                )
            raw = json.loads(native_path.read_bytes())
            native = {
                (item["market_date"], item["decision_key"]): item["final_action"] for item in raw
            }
        observed = {
            (item["market_date"], item["decision_key"]): item["final_action"] for item in decisions
        }
        change_count = (
            sum(observed.get(key) != action for key, action in native.items()) if native else 0
        )
        native_entries = sum(
            action == GovernedAction.TRIAL_ENTRY.value for action in native.values()
        )
        observed_entries = sum(
            item["final_action"] == GovernedAction.TRIAL_ENTRY.value for item in decisions
        )
        daily_returns = [
            {"market_date": equity[index + 1]["market_date"], "return": str(value)}
            for index, value in enumerate(returns)
        ]
        return (
            {
                "capital_constrained_net_total_return_delta": _rate(
                    values[-1] / initial_equity - Decimal("1")
                ),
                "maximum_drawdown_delta": _rate(maximum_drawdown),
                "tail_loss_delta": _rate(max(Decimal("0"), -fifth)),
                "average_invested_capital_delta": _rate(
                    sum(
                        (Decimal(item["invested_capital_ratio"]) for item in replay_points),
                        Decimal("0"),
                    )
                    / Decimal(len(replay_points))
                ),
                "time_in_market_delta": _rate(
                    Decimal(sum(Decimal(item["market_value"]) > 0 for item in replay_points))
                    / Decimal(len(replay_points))
                ),
                "turnover_delta": _rate(gross / initial_equity),
                "fee_delta": _money(fees),
                "missed_opportunity_count": Decimal(max(0, native_entries - observed_entries)),
                "decision_change_counts": Decimal(change_count),
                "action_change_rate": (
                    Decimal("0")
                    if not native
                    else _rate(Decimal(change_count) / Decimal(len(native)))
                ),
            },
            daily_returns,
        )


def run_formal_p2_variant(
    *, repository_root: Path, output_root: Path, intervention_id: str | None
) -> tuple[FormalReplayEvidence, dict[str, bytes]]:
    with FormalP2ReplayRuntime(repository_root) as runtime:
        return runtime.run_variant(intervention_id, output_root)


def run_paired_block_bootstrap(
    *,
    baseline_root: Path,
    intervention_root: Path,
    question_id: str,
    method: str,
    strategy_card_digest: str,
) -> dict[str, Any]:
    baseline = json.loads((baseline_root / "daily-returns.json").read_bytes())
    intervention = json.loads((intervention_root / "daily-returns.json").read_bytes())
    if [item["market_date"] for item in baseline] != [item["market_date"] for item in intervention]:
        raise ExpertEvaluationError("formal_bootstrap_pair_mismatch", "paired return dates differ")
    differences = [
        Decimal(left["return"]) - Decimal(right["return"])
        for left, right in zip(baseline, intervention, strict=True)
    ]
    blocks = [differences[index : index + 22] for index in range(0, len(differences), 22)]
    blocks = [item for item in blocks if len(item) == 22]
    if not blocks:
        raise ExpertEvaluationError(
            "formal_bootstrap_sample_missing", "bootstrap has no complete block"
        )
    seed = int(
        hashlib.sha256(
            canonical_json_bytes(
                {
                    "strategy_card_digest": strategy_card_digest,
                    "question_id": question_id,
                }
            )
        ).hexdigest()[:16],
        16,
    )
    generator = random.Random(seed)
    estimates = []
    for _ in range(2000):
        sampled = [generator.choice(blocks) for _ in range(len(blocks))]
        flat = [value for block in sampled for value in block]
        estimates.append(sum(flat, Decimal("0")) / Decimal(len(flat)))
    estimates.sort()
    lower = estimates[math.floor(0.025 * (len(estimates) - 1))]
    upper = estimates[math.floor(0.975 * (len(estimates) - 1))]
    return {
        "schema_version": "quanthus.formal-attribution-bootstrap-evidence.v1",
        "question_id": question_id,
        "method": method,
        "block_sessions": 22,
        "complete_block_count": len(blocks),
        "resamples": 2000,
        "seed": seed,
        "lower": str(lower),
        "upper": str(upper),
        "completed": True,
        "passed": True,
        "dependence_disclosure": (
            "Rows sharing market dates, securities, event windows, or capital units are dependent."
        ),
    }
