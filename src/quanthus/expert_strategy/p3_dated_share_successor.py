"""Source-bound, separately approved dated-share retrospective P3 consumer."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    CorporateAction,
    EventType,
    OpenExecutionState,
)
from quanthus.backtest.portfolio_replay import _child_run, _state_from_snapshot
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_historical_action_successor as parent
from quanthus.expert_strategy import p3_historical_robustness as h
from quanthus.expert_strategy.formal_p2_replay import (
    FormalP2ReplayRuntime,
    _SellSchedule,
    _transition,
)
from quanthus.expert_strategy.p3_dated_share_engine import AVAILABILITY, DatedShareEngine
from quanthus.expert_strategy.p3_optional_rights import no_subscription_disposition
from quanthus.pipelines.securities_warehouse import p10_dated_distribution_input as p10
from quanthus.pipelines.securities_warehouse import p10_rights_replay_input as rights_input
from quanthus.pipelines.securities_warehouse.p10_rights_issue_source import load_rights_source
from quanthus.position_lifecycle import (
    PositionEvidenceKind,
    PositionEvidenceRef,
    PositionStage,
    PositionTransitionKind,
    apply_position_transition,
)

VERSION = "p3-historical-dated-share-successor-v1"
REQUEST = "config/research/p3-strat-003/historical-dated-share-successor.v1.json"
MEMBERS = {
    "src/quanthus/expert_strategy/p3_dated_share_engine.py",
    "src/quanthus/expert_strategy/p3_dated_share_successor.py",
    "src/quanthus/expert_strategy/p3_dated_share_preparation.py",
    "src/quanthus/pipelines/securities_warehouse/p10_dated_distribution_input.py",
    "src/quanthus/pipelines/securities_warehouse/p10_historical_distribution_source.py",
    "src/quanthus/backtest/engine.py",
    "src/quanthus/position_lifecycle.py",
}


def load_request(repository: Path) -> dict[str, Any]:
    old = parent.load_request(repository)
    r: dict[str, Any] = json.loads((repository / REQUEST).read_bytes())
    if (
        r.get("schema_version") != VERSION
        or r.get("parent_request_sha256") != native._sha(repository / parent.REQUEST)
        or r.get("share_availability_model") != AVAILABILITY
        or r.get("dated_source_manifest_sha256")
        != "sha256:08bbaa5dfea033178763a78a60f645b767eef2b02a4bd80cc5615244a3623a1f"
        or set(r.get("code_config_members", {})) != MEMBERS
        or any(
            r.get(k) != old[k]
            for k in (
                "scope",
                "arms",
                "execution_order",
                "time_blocks",
                "annual_diagnostics",
                "analysis",
                "input_preparation_request_digest",
                "optional_rights_policy",
                "optional_rights_policy_sha256",
                "reference_model",
            )
        )
        or any(
            r.get(k) is not False
            for k in (
                "historical_PIT_claim",
                "production_authorized",
                "P6_mutation_authorized",
                "original_B_accepted",
                "original_D_activated",
                "parameter_search",
                "source_guards_waived",
                "new_collection_authorized",
            )
        )
        or r.get("maximum_additional_wall_seconds") != 7200
        or r.get("maximum_seconds_per_role") != 7200
        or r.get("previous_failures_costs_and_deadlines_retained") is not True
    ):
        raise ValueError("DATED_SHARE_REQUEST")
    for name, sha in r["code_config_members"].items():
        if native._sha(native.physical(repository / name)) != sha:
            raise ValueError("DATED_SHARE_CODE_CHANGED")
    return r


class DatedShareReader(parent.SupplementedReader):
    def _install_late_actions(self, bundle: Any, facts: Any) -> None:
        import pyarrow as pa

        same_day = tuple(f for f in facts if f.share_listing_date == f.ex_date)
        if not same_day:
            raise ValueError("DATED_READER_QUALIFIED_PARENT_REQUIRED")
        super()._install_late_actions(bundle, same_day)
        c = self._ensure_open()
        for f in facts:
            if f in same_day:
                continue
            if (
                f.share_credit_date is not None
                or f.share_listing_date is None
                or f.share_listing_date <= f.ex_date
                or f.cash_payment_date != f.ex_date
            ):
                raise ValueError("DATED_READER_UNQUALIFIED_AVAILABILITY")
            if self._scalar(
                "SELECT count(*) FROM replay_actions_mapped "
                "WHERE security_id=? AND effective_date=?",
                (f.security_code, f.ex_date),
            ):
                raise ValueError("DATED_READER_CONFLICT")
            valid = self._scalar(
                "SELECT count(*) FROM replay_validity WHERE security_id=? "
                "AND CAST(valid_from AS DATE)<=? "
                "AND (valid_to IS NULL OR CAST(valid_to AS DATE)>=?)",
                (f.security_code, f.record_date, f.share_listing_date),
            )
            days = sorted({f.record_date, f.ex_date, f.share_listing_date})
            sessions = self._scalar(
                "SELECT count(*) FROM replay_calendar c JOIN replay_universe u "
                "ON c.exchange=u.exchange AND c.trade_date=u.market_date "
                "WHERE u.security_id=? AND u.market_date IN (?,?,?) AND c.is_open=1",
                (f.security_code, *days),
            )
            if valid != 1 or sessions != len(days):
                raise ValueError("DATED_READER_IDENTITY_CALENDAR")
            row = self._rows(
                "SELECT b.close,(SELECT close FROM replay_bars p "
                "WHERE p.security_id=b.security_id AND p.date<b.date AND p.close>0 "
                "ORDER BY p.date DESC LIMIT 1) AS prior_close,l.upper_limit_pct,l.lower_limit_pct "
                "FROM replay_bars b JOIN replay_limits l ON l.security_id=b.security_id "
                "AND l.execution_date=b.date WHERE b.security_id=? AND b.date=?",
                (f.security_code, f.ex_date),
            )
            if len(row) != 1 or not row[0]["prior_close"]:
                raise ValueError("DATED_READER_REFERENCE_MISSING")
            r = row[0]
            gross = (
                Decimal(str(r["close"])) * (1 + f.bonus_share_ratio) + f.cash_per_share
            ) / Decimal(str(r["prior_close"]))
            if not (
                1 - Decimal(str(r["lower_limit_pct"])) / 100 - Decimal("0.005")
                <= gross
                <= 1 + Decimal(str(r["upper_limit_pct"])) / 100 + Decimal("0.005")
            ):
                raise ValueError("DATED_READER_RESIDUAL")
            c.register(
                "dated_added_arrow",
                pa.Table.from_pylist(parent._action_rows((f,), bundle.manifest_sha256)),
            )
            c.execute("INSERT INTO qualified_late_actions SELECT * FROM dated_added_arrow")
            c.unregister("dated_added_arrow")


class DatedShareRuntime(parent.HistoricalActionRuntime):
    def __init__(self, repository: Path, source: Path, prepared: Path, admission: Any) -> None:
        self.historical_request = load_request(repository)
        self.request_digest = digest_value(self.historical_request)
        self.execution_request = self.historical_request
        self.execution_digest = self.request_digest
        self.effective_source_sha = self.historical_request["source_manifest_sha256"]
        if (
            admission.get("request_digest") != self.request_digest
            or admission.get("execution_digest") != self.request_digest
            or admission.get("role") not in {"primary", "independent_cold_reproduction"}
            or admission.get("source_manifest_sha256") != self.effective_source_sha
            or admission.get("preparation_sha256") != native._sha(prepared / "preparation.json")
            or admission.get("feature_sha256") != native._sha(prepared / "features.parquet")
        ):
            raise ValueError("DATED_SHARE_EXACT_ADMISSION")
        self.bundle, self.official_facts = p10.load_input(
            source, self.effective_source_sha.removeprefix("sha256:")
        )
        self.prepared = prepared
        self.preparation = h.read_preparation(prepared / "preparation.json")
        if (
            self.preparation.get("action_successor_request_digest") != self.request_digest
            or self.preparation.get("effective_source_manifest_sha256") != self.effective_source_sha
            or self.preparation.get("source_information_cutoff")
            != self.historical_request["source_information_cutoff"]
        ):
            raise ValueError("DATED_SHARE_PREREQUISITE")
        self.prerequisite_digest = digest_value(
            {k: v for k, v in self.preparation.items() if k != "feature_disposition"}
        )
        self.snapshot_id = VERSION + "/" + self.request_digest.removeprefix("sha256:")
        self.info = datetime.fromisoformat(self.historical_request["source_information_cutoff"])
        if self.info != self.bundle.historical_bundle.snapshot.knowledge_cutoff:
            raise ValueError("DATED_SHARE_SOURCE_CLOCK")
        original = load_rights_source(
            source / "parent/parent/evidence/issuer", rights_input.ISSUER_SHA
        )
        self.rights_dispositions = [
            no_subscription_disposition(
                repository,
                original,
                arm=arm,
                market_date=original.ex_right_date,
                source_information_cutoff=self.info,
            )
            for arm in h.ARMS
        ]
        self.reader = DatedShareReader(self.bundle, self.official_facts, self.info)
        self.entry_runners = {}
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
            added = {
                "official-action:" + f.evidence_sha256: CorporateAction(
                    "official-action:" + f.evidence_sha256,
                    f.security_code,
                    f.ex_date,
                    f.acquired_at,
                    f.cash_per_share,
                    f.bonus_share_ratio,
                    f.source_revision + "|evidence=sha256:" + f.evidence_sha256,
                    self.effective_source_sha,
                )
                for f in self.official_facts
            }
            self.engine = DatedShareEngine(
                self.engine.policy, self.info, self.snapshot_id, added, self.official_facts
            )
        except BaseException:
            self.reader.close()
            if hasattr(self, "stack"):
                self.stack.close()
            raise

    def _code_version(self) -> str:
        return super()._code_version() + ":" + VERSION

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "purpose": VERSION,
            "execution_adapter": VERSION,
            "share_availability_model": AVAILABILITY,
            "actual_share_credit_known": False,
        }

    def run_variant(self, *args: Any, **kwargs: Any) -> Any:
        self.engine.reset_arm()
        return super().run_variant(*args, **kwargs)

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
        from quanthus.backtest.contracts import OrderSide

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
        horizon = max(
            [frame.execution_date]
            + [
                listing
                for _, _, listing in self.engine.lots.get(
                    (runtime.ledger.unit_id, runtime.ledger.security_code), []
                )
            ]
        )
        sessions, actions = [], []
        mark = runtime.ledger.last_mark_price
        for candidate in self.frames[frame_index:]:
            if mark is None:
                raise ValueError("DATED_SHARE_POSITION_MARK")
            steps = candidate.actions(runtime.panel_security_id, mark)
            actions.extend(steps)
            for step in steps:
                if step.action.action_id in self.engine.dated:
                    horizon = max(
                        horizon, self.engine.dated[step.action.action_id].share_listing_date
                    )
            if steps:
                mark = steps[-1].post_action_mark_price
            session = candidate.session(runtime.panel_security_id)
            sessions.append(session)
            if session.close_price is not None:
                mark = session.close_price
            if session.session_date >= horizon and session.open_execution_state not in {
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
            day = event.event_time.astimezone(MARKET_TIMEZONE).date()
            if event.event_type is EventType.DAILY_SNAPSHOT:
                states[date.fromisoformat(event.payload["session_date"])] = _state_from_snapshot(
                    event, target_notional=runtime.ledger.target_notional
                )
            elif event.event_type is EventType.FILL:
                if day in fills:
                    raise ValueError("DATED_SHARE_DUPLICATE_SESSION_FILL")
                fills[day] = event
            elif event.event_type is EventType.ORDER_STATUS and str(
                event.payload.get("transition", "")
            ).endswith("to_carried"):
                carries[day] = event
        return _SellSchedule(full_exit, states, fills, carries, result.events)

    def _apply_sell_schedule(
        self, frame: Any, runtime: Any, transitions: Any, schedule: _SellSchedule, events: Any
    ) -> None:
        after = schedule.states_by_date.get(frame.execution_date)
        fill = schedule.fill_by_date.get(frame.execution_date)
        if after is None or fill is None or not schedule.full_exit or not after.settled_quantity:
            return super()._apply_sell_schedule(frame, runtime, transitions, schedule, events)
        before = runtime.ledger
        for event in schedule.events:
            if (
                event.event_type is EventType.CORPORATE_ACTION
                and event.event_time.astimezone(MARKET_TIMEZONE).date() == frame.execution_date
            ):
                p = event.payload
                action = self.engine.added_actions.get(p["action_id"])
                if (
                    action is None
                    or Decimal(p["quantity_after"])
                    != before.settled_quantity * (1 + action.bonus_share_ratio)
                    or Decimal(p["cash_dividend"])
                    != self.engine._quantize_cash(
                        before.settled_quantity * action.cash_dividend_per_share
                    )
                    or p["quantity_after"] - p["quantity_before"] != p["quantity_delta"]
                    or Decimal(p["cash_after"]) - Decimal(p["cash_before"])
                    != Decimal(p["cash_dividend"])
                    or p["quantity_before"] != before.settled_quantity
                    or Decimal(p["cash_before"]) != before.cash
                    or p["security_code"] != before.security_code
                    or Decimal(p["position_cost"]) != before.position_cost
                ):
                    raise ValueError("DATED_PARTIAL_ACTION_BEFORE_IMAGE")
                before = replace(
                    before,
                    settled_quantity=p["quantity_after"],
                    cash=Decimal(p["cash_after"]),
                    dividend_income=before.dividend_income + Decimal(p["cash_dividend"]),
                )
        p = fill.payload
        carry = schedule.carry_by_date.get(frame.execution_date)
        sold_cost = self.engine._quantize_cash(
            before.position_cost * p["quantity"] / before.settled_quantity
        )
        fees = sum(Decimal(p[k]) for k in ("commission", "stamp_duty", "transfer_fee"))
        if (
            runtime.lifecycle.stage is not PositionStage.EXITING
            or carry is None
            or carry.payload.get("partial_fill_ids") != [p["fill_id"]]
            or before.settled_quantity - after.settled_quantity != p["quantity"]
            or after.cash != before.cash + Decimal(p["gross_amount"]) - Decimal(p["total_fees"])
            or after.dividend_income != before.dividend_income
            or Decimal(p["total_fees"]) != fees
            or after.position_cost != before.position_cost - sold_cost
            or after.external_pool != before.external_pool
            or after.realized_pnl
            != before.realized_pnl + Decimal(p["gross_amount"]) - sold_cost - fees
            or any(
                getattr(after, k + "_expense") != getattr(before, k + "_expense") + Decimal(p[k])
                for k in ("commission", "stamp_duty", "transfer_fee")
            )
            or not 0 < carry.payload["remaining_quantity"] <= after.settled_quantity
        ):
            raise ValueError("DATED_PARTIAL_EXIT_RECONCILIATION")
        # This is a post-fill carry transition. The actual fill and its before/
        # after ledger remain in the engine stream; EXIT_CARRIED does no trade.
        transition = _transition(
            runtime,
            PositionTransitionKind.EXIT_CARRIED,
            carry.event_time,
            ("dated_share_partial_fill_remainder_carried",),
            PositionEvidenceRef(
                PositionEvidenceKind.ORDER_STATUS, carry.event_id, self.engine.policy.policy_version
            ),
        )
        runtime.lifecycle = apply_position_transition(
            runtime.lifecycle, transition, ledger_before=after, ledger_after=after
        )
        runtime.ledger = after
        runtime.holding_sessions += 1
        transitions.append(transition)
