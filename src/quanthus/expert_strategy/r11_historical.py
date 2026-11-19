"""R11 historical capital composition for the original T+1 expert and P2 paths."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.backtest.contracts import EngineEvent, EventType
from quanthus.backtest.data_provider import (
    ReplayDataProvider,
    load_replay_data_provider_config,
)
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.expert_evaluation.production import _EventInput
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, canonical_json_bytes
from quanthus.expert_strategy.formal_attribution import FormalReplayEvidence
from quanthus.expert_strategy.formal_p2_replay import (
    DECISION_END,
    DECISION_START,
    EXECUTION_END,
    FormalP2ReplayRuntime,
    _EntryCandidate,
    _money,
    _SellSchedule,
    _UnitRuntime,
)
from quanthus.industry_mapping import StaticIndustryMapping
from quanthus.product_daily.contracts import CommandEnvelope
from quanthus.product_daily.funding import (
    BucketPhase,
    BucketState,
    CycleSettlementEvidence,
    FundingState,
    SettlementRequest,
    prepare_cycle_core,
    settle_batch,
    settle_invocation_payload,
)
from quanthus.product_daily.risk import (
    BuyFeeRule,
    MarkedExposure,
    PendingBuy,
    QuantityLimits,
    QuantityRequest,
    RiskSnapshot,
    evaluate_quantity,
)

POLICY_VERSION = "r11-hist-fixed-reserve-equitycaps-v1"
CAPS = QuantityLimits(Decimal("0.04"), Decimal("0.08"), Decimal("0.20"))
ZERO = Decimal("0.00")
ReplayResult = tuple[FormalReplayEvidence, dict[str, bytes]]


@dataclass(frozen=True)
class HistoricalBuyBound:
    """A qualified bound for the slipped and tick-rounded buy price, known at T."""

    price: Decimal
    available_at: datetime
    evidence_id: str

    def __post_init__(self) -> None:
        if (
            self.available_at.tzinfo is None
            or self.available_at.utcoffset() is None
            or not self.price.is_finite()
            or self.price <= 0
            or not self.evidence_id.strip()
        ):
            raise ValueError("invalid historical buy-bound evidence")


class R11HistoricalReplayRuntime(FormalP2ReplayRuntime):
    """Retain formal strategy and P2 matching, using the adopted R11 capital policy."""

    _candidate_active = False

    def __init__(
        self,
        repository_root: Path,
        *,
        provider_config: str,
        parent_event_run_digest: str,
        buy_bounds: Mapping[tuple[date, str], HistoricalBuyBound],
        admitted_data_manifest_sha256: str,
        consumer_receipt_id: str,
        excluded_security_ids: frozenset[str],
    ) -> None:
        if not consumer_receipt_id.strip() or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", admitted_data_manifest_sha256
        ):
            raise ValueError("R11 requires a qualified P10 consumer receipt and manifest hash")
        if not excluded_security_ids or any(not value.strip() for value in excluded_security_ids):
            raise ValueError("R11 requires the qualified formal security IDs for known exposure")
        self._excluded_security_ids = excluded_security_ids
        self._buy_bounds = dict(buy_bounds)
        self._consumer_receipt_id = consumer_receipt_id
        self._admitted_data_manifest_sha256 = admitted_data_manifest_sha256
        # Fail closed before any event or market-row read when the provider mode
        # cannot prove its reader manifest from pinned metadata alone.
        provable = ReplayDataProvider(
            load_replay_data_provider_config(
                repository_root.resolve() / provider_config
            )
        ).admitted_manifest()
        if provable != admitted_data_manifest_sha256:
            raise ValueError(
                "R11 provider manifest does not match the admitted data"
            )
        super().__init__(
            repository_root,
            provider_config=provider_config,
            parent_event_run_digest=parent_event_run_digest,
            decision_start=DECISION_START,
            decision_end=DECISION_END,
            execution_end=EXECUTION_END,
        )
        if self.frames[0].snapshot.market_date < DECISION_START:
            self.close()
            raise ValueError("R11 replay starts before the admitted decision window")

    def _open_data_session(self) -> Any:
        provider_config = load_replay_data_provider_config(
            self.repository_root / self.provider_config,
            allowed_partitions=self.allowed_provider_partitions,
        )
        provider = ReplayDataProvider(provider_config)
        session = provider.open(
            scope_start=self._provider_scope_start(),
            scope_end=self.decision_end,
            admitted_reader_manifest_sha256=self._admitted_data_manifest_sha256,
        )
        if session.reader.data_manifest_sha256 != self._admitted_data_manifest_sha256:
            session.__exit__(None, None, None)
            raise ValueError("R11 provider manifest does not match the admitted data")
        return session

    def _load_event_population(
        self, event_root: Path
    ) -> tuple[tuple[_EventInput, ...], StaticIndustryMapping]:
        events, industry = super()._load_event_population(event_root)
        return tuple(
            item
            for item in events
            if item.record.identity.security_id not in self._excluded_security_ids
        ), industry

    def run_pair(self, output_root: Path) -> tuple[ReplayResult, ReplayResult]:
        """Use the same admitted event/feature/frame construction for both arms."""
        control = self.run_variant(None, output_root / "control")
        candidate = self.run_candidate(output_root / "candidate")
        return control, candidate

    def run_candidate(self, output_root: Path) -> ReplayResult:
        self._reserve = ZERO
        self._capital_trace: list[dict[str, Any]] = []
        self._full_exits: dict[str, tuple[str, Decimal]] = {}
        self._locked_budgets: dict[str, Decimal] = {}
        settings = self.registry.card.semantic_sections["position_and_execution"]
        if (
            Decimal(str(settings["unit_notional"])) != Decimal("100000")
            or int(settings["unit_count"]) != 5
            or int(settings["maximum_active_units"]) != 5
            or Decimal(str(settings["base_trial_position_fraction"])) != Decimal("0.30")
            or self.engine.policy.policy_version != "p2-daily-research-v1"
            or self.engine.policy.active_profile.profile_id != "close-to-next-open-v1"
            or self.engine.policy.entry_lot_size != 100
        ):
            raise ValueError("R11 candidate does not bind the adopted account and P2 policy")
        self._candidate_active = True
        try:
            return self.run_variant(None, output_root)
        finally:
            self._candidate_active = False

    def _variant_id(self, intervention_id: str | None) -> str:  # type: ignore[override]
        if not self._candidate_active:
            return super()._variant_id(intervention_id)
        if intervention_id is not None:
            raise ValueError("R11 candidate uses the original expert configuration")
        return "variant.r11-historical-funding"

    def _validate_intervention_id(self, intervention_id: str | None) -> None:
        if intervention_id is not None:
            raise ValueError("R11 candidate uses the original expert configuration")

    def _code_version(self) -> str:
        return POLICY_VERSION if self._candidate_active else super()._code_version()

    def _run_id(self, intervention_id: str | None) -> str:
        legacy = super()._run_id(intervention_id)
        return f"{legacy}.{POLICY_VERSION}" if self._candidate_active else legacy

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        if not self._candidate_active:
            return super()._parameter_payload(intervention_id)
        return {
            **super()._parameter_payload(intervention_id),
            "historical_capital_policy": POLICY_VERSION,
            "consumer_receipt": self._consumer_receipt_id,
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        if not self._candidate_active:
            return super()._manifest_metadata(intervention_id)
        return {
            "historical_capital_policy": POLICY_VERSION,
            "consumer_receipt": self._consumer_receipt_id,
            "candidate_ledger": "r11-capital-trace.json",
        }

    def _prepare_entry_sizing(
        self, frame: StrategyReplayFrame, runtimes: Mapping[str, _UnitRuntime]
    ) -> None:
        if not self._candidate_active:
            return
        exposure: list[MarkedExposure] = []
        for unit_id, unit in sorted(runtimes.items()):
            if not unit.ledger.settled_quantity:
                continue
            if unit.formal_security_id is None or unit.opportunity_id is None:
                raise ValueError("R11 held exposure lacks formal identity")
            membership = self.industry.by_security_id[unit.formal_security_id]
            exposure.append(
                MarkedExposure(
                    unit_id,
                    unit.opportunity_id,
                    unit.formal_security_id,
                    membership.level1_industry_id,
                    unit.ledger.settled_quantity,
                    unit.ledger.last_mark_price,
                    None,
                )
            )
        marks_complete = all(item.mark_price is not None for item in exposure)
        self._risk_snapshot = RiskSnapshot(
            frame.snapshot.snapshot_id,
            frame.decision_at,
            self._portfolio_equity(runtimes) if marks_complete else None,
            sum((unit.ledger.cash for unit in runtimes.values()), self._reserve),
            tuple(exposure),
        )
        self._sized: dict[str, tuple[Decimal, Decimal]] = {}

    def _size_entry(
        self,
        frame: StrategyReplayFrame,
        runtime: _UnitRuntime,
        candidate: _EntryCandidate,
        unit_notional: Decimal,
        position: Mapping[str, Any],
    ) -> int:
        if not self._candidate_active:
            return super()._size_entry(frame, runtime, candidate, unit_notional, position)
        security_id = candidate.item.record.identity.security_id
        bound = self._buy_bounds.get((frame.snapshot.market_date, security_id))
        bound_visible = bound is not None and bound.available_at <= frame.snapshot.knowledge_cutoff
        mark = candidate.feature.close_price if candidate.feature is not None else None
        target = candidate.decision.target_maximum_position
        membership = self.industry.by_security_id[security_id]
        costs = self.engine.policy.buy_cost
        unit_id = runtime.ledger.unit_id
        funding = FundingState(
            (
                BucketState(
                    unit_id, runtime.ledger.cash, unit_notional, ZERO, None, BucketPhase.EMPTY
                ),
            ),
            self._reserve,
            ZERO,
            0,
        )
        prepared = prepare_cycle_core(
            funding,
            bucket_id=unit_id,
            cycle_id=candidate.opportunity_id,
            reference_id=candidate.decision.digest,
        )
        quantity = evaluate_quantity(
            QuantityRequest(
                self._risk_snapshot.snapshot_id,
                prepared.state.bucket(unit_id).locked_cycle_budget,
                min(Decimal("0.20"), target) if target is not None else None,
                security_id,
                membership.level1_industry_id,
                mark,
                bound.price if bound is not None and bound_visible else None,
                self.engine.policy.entry_lot_size,
                CAPS,
                BuyFeeRule(
                    costs.commission_rate,
                    costs.minimum_commission,
                    self.engine.policy.money.cash_quantum,
                ),
            ),
            self._risk_snapshot,
        )
        self._capital_trace.append(
            {
                "kind": "entry_sizing",
                "decision_at": frame.decision_at.isoformat(),
                "opportunity_id": candidate.opportunity_id,
                "expert_decision_digest": candidate.decision.digest,
                "unit_id": runtime.ledger.unit_id,
                "bound_evidence": bound.evidence_id
                if bound is not None and bound_visible
                else None,
                "decision": quantity.to_dict(),
                "funding_entries": [
                    entry.to_dict() for entry in prepared.ledger_entries if quantity.quantity
                ],
            }
        )
        if quantity.quantity:
            assert (
                bound is not None
                and bound_visible
                and mark is not None
                and quantity.fees is not None
            )
            pending = PendingBuy(
                candidate.opportunity_id,
                security_id,
                membership.level1_industry_id,
                quantity.quantity,
                mark,
                bound.price,
                quantity.fees,
            )
            self._sized[candidate.opportunity_id] = (bound.price, quantity.fees)
            self._locked_budgets[unit_id] = prepared.state.bucket(unit_id).locked_cycle_budget
            self._reserve = prepared.state.reserve
            runtime.ledger = replace(runtime.ledger, cash=prepared.state.bucket(unit_id).cash)
            self._risk_snapshot = replace(
                self._risk_snapshot, pending_buys=(*self._risk_snapshot.pending_buys, pending)
            )
        return quantity.quantity

    def _validate_entry_fill(self, candidate: _EntryCandidate, fill: EngineEvent) -> None:
        if not self._candidate_active:
            return
        price, fees = self._sized[candidate.opportunity_id]
        if (
            Decimal(str(fill.payload["price"])) > price
            or Decimal(str(fill.payload["total_fees"])) > fees
        ):
            raise ExpertEvaluationError(
                "r11_fill_exceeds_pit_bound", "realized P2 fill exceeds the decision-time bound"
            )

    def _observe_full_exit(
        self, runtime: _UnitRuntime, schedule: _SellSchedule, frame: StrategyReplayFrame
    ) -> None:
        if not self._candidate_active:
            return
        unit_id = runtime.ledger.unit_id
        matching = [
            event
            for event in schedule.events
            if event.event_type is EventType.UNIT_SETTLEMENT
            and event.payload.get("unit_id") == unit_id
            and event.event_time.date() == frame.execution_date
        ]
        if len(matching) != 1 or runtime.ledger.settled_quantity != 0:
            raise ValueError("R11 settlement requires one matched terminal P2 exit")
        receipt = matching[0]
        raw_cash = Decimal(str(receipt.payload["cash_before_settlement"]))
        if raw_cash != runtime.ledger.cash + runtime.ledger.external_pool:
            raise ValueError("R11 terminal P2 cash does not reconcile")
        self._full_exits[unit_id] = (receipt.event_id, raw_cash)

    def _settle_portfolio_frame(
        self, frame: StrategyReplayFrame, runtimes: Mapping[str, _UnitRuntime]
    ) -> None:
        if not self._candidate_active or not self._full_exits:
            return
        requests = []
        buckets = []
        for unit_id, unit in sorted(runtimes.items()):
            closed = self._full_exits.get(unit_id)
            if closed is None:
                buckets.append(
                    BucketState(
                        unit_id,
                        unit.ledger.cash,
                        unit.ledger.target_notional,
                        ZERO,
                        None,
                        BucketPhase.EMPTY,
                    )
                )
                continue
            settlement_id, raw_cash = closed
            cycle_id = unit.opportunity_id
            if cycle_id is None:
                raise ValueError("R11 exit lacks a cycle identity")
            buckets.append(
                BucketState(
                    unit_id,
                    raw_cash,
                    unit.ledger.target_notional,
                    self._locked_budgets[unit_id],
                    cycle_id,
                    BucketPhase.CLOSED,
                )
            )
            requests.append(
                SettlementRequest(
                    settlement_id,
                    unit_id,
                    cycle_id,
                    CycleSettlementEvidence(True, True, True, True, True),
                )
            )
        previous = self._portfolio_equity(runtimes)
        funding = FundingState(tuple(buckets), self._reserve, ZERO, 0)
        payload = settle_invocation_payload(requests)
        result = settle_batch(
            funding,
            CommandEnvelope.create("r11-settle:" + frame.execution_date.isoformat(), payload, 0),
            requests,
        )
        if not result.accepted or result.state.cash_total != funding.cash_total:
            raise ValueError("R11 shared reserve settlement did not reconcile")
        for unit_id in sorted(self._full_exits):
            unit = runtimes[unit_id]
            unit.ledger = replace(
                unit.ledger, cash=result.state.bucket(unit_id).cash, external_pool=ZERO
            )
            del self._locked_budgets[unit_id]
        self._reserve = result.state.reserve
        if self._portfolio_equity(runtimes) != previous:
            raise ValueError("R11 shared reserve transfer changed account equity")
        self._capital_trace.append(
            {
                "kind": "shared_reserve_settlement",
                "session_date": frame.execution_date.isoformat(),
                "p2_settlement_sources": {
                    key: item[0] for key, item in sorted(self._full_exits.items())
                },
                "reserve": str(self._reserve),
                "entries": [entry.to_dict() for entry in result.ledger_entries],
            }
        )
        self._full_exits.clear()

    def _portfolio_equity(self, runtimes: Mapping[str, _UnitRuntime]) -> Decimal:
        equity = super()._portfolio_equity(runtimes)
        return _money(equity + self._reserve) if self._candidate_active else equity

    def _portfolio_artifacts(self) -> dict[str, bytes]:
        if not self._candidate_active:
            return {}
        return {
            "r11-capital-trace.json": canonical_json_bytes(
                {
                    "policy": POLICY_VERSION,
                    "ending_reserve": str(self._reserve),
                    "events": self._capital_trace,
                }
            )
        }
