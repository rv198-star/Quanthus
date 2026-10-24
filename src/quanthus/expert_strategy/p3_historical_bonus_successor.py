"""Versioned P3 integration of P2's existing proportional carried-sell rule.

Corporate actions remain P2 events, applied once before the open. A lifecycle
carry compares the post-action, pre-trade ledger with the session result; it
must not mistake an evidenced bonus for a sell fill or a strategy add. Original
formal replay and lifecycle contracts are unchanged and still fail closed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.backtest.contracts import MARKET_TIMEZONE, EventType, OpenExecutionState, OrderSide
from quanthus.backtest.engine import BacktestInputError
from quanthus.backtest.portfolio_replay import _child_run, _state_from_snapshot
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_historical_rights_successor as parent
from quanthus.expert_strategy import p3_historical_robustness as h
from quanthus.expert_strategy.formal_p2_replay import _SellSchedule
from quanthus.expert_strategy.p3_historical_point_panel import PointPanel

VERSION = "p3-historical-bonus-integration-v3"
REQUEST = "config/research/p3-strat-003/historical-bonus-integration.v3.json"


def load_execution_request(repository: Path) -> dict[str, Any]:
    scientific = parent.load_request(repository)
    request: dict[str, Any] = json.loads((repository / REQUEST).read_bytes())
    if (
        request.get("schema_version") != VERSION
        or request.get("scientific_request_sha256") != native._sha(repository / parent.REQUEST)
        or request.get("source_manifest_sha256") != scientific["source_manifest_sha256"]
        or request.get("code_sha256")
        != native._sha(repository / "src/quanthus/expert_strategy/p3_historical_bonus_successor.py")
        or request.get("point_panel_code_sha256")
        != native._sha(repository / "src/quanthus/expert_strategy/p3_historical_point_panel.py")
        or request.get("point_panel_rule") != "EXACT_KEY_UNAVAILABLE_STAYS_UNAVAILABLE"
        or request.get("rule") != "EXISTING_P2_PROPORTIONAL_PENDING_SELL"
        or request.get("action_identity_rule")
        != (
            "SELL_EFFECTIVE_VIEW_AND_ORIGINAL_ACTION_SOURCE_"
            "VALIDATED_SEPARATELY_WITH_UNCHANGED_CUTOFF"
        )
        or request.get("scope") != scientific["scope"]
        or request.get("scientific_policy_changed") is not False
        or request.get("deadline_reset") is not False
        or request.get("production_authorized") is not False
        or request.get("preparation_sha256")
        != "sha256:e15ab17698de4616ea0e11af7746f0e56c4134f8392557575fe7f5f817d2e570"
        or request.get("feature_sha256")
        != "sha256:29523b3a16dff12e722f35939063f9b0778e7216ea650668deb7e0b6711a2b5f"
    ):
        raise native.InputError("BONUS_EXECUTION_REQUEST")
    return request


class HistoricalBonusEngine(h.HistoricalEngine):
    """Bind sell effective-view and original action identities separately."""

    def __init__(self, policy: Any, cutoff: datetime, snapshot: str, action_snapshot: str) -> None:
        super().__init__(policy, cutoff, snapshot, action_snapshot)
        self._bound_sell_run: Any = None

    def run_sell_sequence(self, *, run: Any, **kwargs: Any) -> Any:
        if self._bound_sell_run is not None:
            raise BacktestInputError("HISTORY_NESTED_SELL_SEQUENCE")
        self._bound_sell_run = run
        try:
            return super().run_sell_sequence(run=run, **kwargs)
        finally:
            self._bound_sell_run = None

    def _validate_action_clock(self, run: Any, action: Any, applied_at: datetime) -> None:
        if run is self._bound_sell_run:
            if run.data_snapshot_id != self.snapshot:
                raise BacktestInputError("HISTORY_ACTION_BINDING")
            # The signal guard still binds the effective input to this exact
            # sell run. The unchanged action guard separately binds the original
            # source and actual cutoff; no action time or identity is rewritten.
            run = replace(run, data_snapshot_id=self.action_snapshot)
        super()._validate_action_clock(run, action, applied_at)


class HistoricalBonusRuntime(parent.HistoricalRightsRuntime):
    def _open_data_inputs(self) -> None:
        super()._open_data_inputs()
        # The stack retains the original owner. Borrow its sole immutable table;
        # no second materialization or cross-role feature sharing is introduced.
        if not isinstance(self.panel, h.DayPanel):
            raise native.InputError("HISTORY_POINT_OWNER_TYPE")
        self.panel = PointPanel(self.panel)

    def __init__(
        self, repository: Path, source: Path, prepared: Path, admission: dict[str, Any]
    ) -> None:
        self.execution_request = load_execution_request(repository)
        self.execution_digest = digest_value(self.execution_request)
        if (
            admission.get("execution_digest") != self.execution_digest
            or admission.get("preparation_sha256") != self.execution_request["preparation_sha256"]
            or admission.get("feature_sha256") != self.execution_request["feature_sha256"]
        ):
            raise native.InputError("BONUS_EXECUTION_ADMISSION")
        super().__init__(repository, source, prepared, admission)
        self.engine = HistoricalBonusEngine(
            self.engine.policy, self.info, self.snapshot_id, h.ACTION_SOURCE_SNAPSHOT
        )

    def _code_version(self) -> str:
        return super()._code_version() + ":" + VERSION + ":" + self.execution_digest

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._parameter_payload(intervention_id),
            "execution_digest": self.execution_digest,
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "execution_adapter": VERSION,
            "execution_digest": self.execution_digest,
        }

    def _sell_schedule(
        self,
        frame_index: int,
        frame: Any,
        runtime: Any,
        run: Any,
        quantity: int,
        full_exit: bool,
        decision_digest: str,
    ) -> _SellSchedule:
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
        sessions, actions = [], []
        mark = runtime.ledger.last_mark_price
        for candidate in self.frames[frame_index:]:
            if mark is None:
                raise native.InputError("BONUS_POSITION_MARK_MISSING")
            candidate_actions = candidate.actions(runtime.panel_security_id, mark)
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
        states, fills, carries = {}, {}, {}
        for event in result.events:
            market_date = event.event_time.astimezone(MARKET_TIMEZONE).date()
            if event.event_type is EventType.DAILY_SNAPSHOT:
                states[date.fromisoformat(str(event.payload["session_date"]))] = (
                    _state_from_snapshot(event, target_notional=runtime.ledger.target_notional)
                )
            elif event.event_type is EventType.FILL:
                fills[market_date] = event
            elif event.event_type is EventType.ORDER_STATUS and str(
                event.payload.get("transition", "")
            ).endswith("to_carried"):
                carries[market_date] = event
        return _SellSchedule(full_exit, states, fills, carries, result.events)

    def _apply_sell_schedule(
        self, frame: Any, runtime: Any, transitions: Any, schedule: _SellSchedule, events: Any
    ) -> None:
        if frame.execution_date not in schedule.states_by_date:
            return
        original = runtime.ledger
        adjusted = original
        for event in schedule.events:
            if (
                event.event_type is not EventType.CORPORATE_ACTION
                or event.event_time.astimezone(MARKET_TIMEZONE).date() != frame.execution_date
            ):
                continue
            p = event.payload
            if (
                p["security_code"] != adjusted.security_code
                or p["quantity_before"] != adjusted.settled_quantity
                or Decimal(p["cash_before"]) != adjusted.cash
                or Decimal(p["position_cost"]) != adjusted.position_cost
            ):
                raise native.InputError("BONUS_ACTION_BEFORE_IMAGE")
            if (
                Decimal(p["quantity_before"]) * (1 + Decimal(p["bonus_share_ratio"]))
                != p["quantity_after"]
                or p["quantity_after"] - p["quantity_before"] != p["quantity_delta"]
                or Decimal(p["cash_after"]) - adjusted.cash != Decimal(p["cash_dividend"])
            ):
                raise native.InputError("BONUS_ACTION_DELTA")
            adjusted = replace(
                adjusted,
                settled_quantity=p["quantity_after"],
                cash=Decimal(p["cash_after"]),
                dividend_income=adjusted.dividend_income + Decimal(p["cash_dividend"]),
                last_mark_price=Decimal(p["mark_price"]),
                last_mark_snapshot_id=p["mark_snapshot_id"],
            )
        # Evidence is already present in the immutable P2 event stream. This is
        # the pre-trade ledger boundary, not a second corporate-action execution.
        runtime.ledger = adjusted
        try:
            super()._apply_sell_schedule(frame, runtime, transitions, schedule, events)
        except BaseException:
            runtime.ledger = original
            raise
