"""E1 real-history replay runner: #749 members -> P11 run_e1_replay_day -> RunnerOutput.

Issue #718 / 8792-phase1.  Per the coordinator ruling the 8792 E1 path uses the
P11 PUBLIC replay entry run_e1_replay_day (never the low-level
run_e1_from_history).  P12's job in this adapter is ONLY assembly and
projection:

* build_replay_request projects the bounded members the #749 reader
  delivered (p11.raw-history-input.v1 envelope fields: source_members /
  calendar_rows / listing_rows / securities) plus the 8792 scenario account,
  the fixed October purpose window, #737 exclusions, action coverage, member
  evidence and the run identity into a p11.paper-replay-request.v1 raw
  mapping.  P12 never builds a P11 strategy object, never computes a floor,
  never synthesizes a panel or a clock.
* E1ReplayRunner hands that mapping to load_replay_request +
  run_e1_replay_day (P11 selects the single day's business rows, builds
  the H1 input, judges coverage and isolation internally) and projects the
  returned day receipt into a P12 RunnerOutput for the publish gate.

The publish gate and the E5 per-action admission keep working on the returned
publishable flag, the actual wire package and the receipt / source_closure /
stage_metrics - P12 only transports them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from quanthus.shared.fact_contracts import FactError, digest

from .rule_runner import HeldSignal, RunnerOutput, RunnerRequest

REPLAY_REQUEST_SCHEMA = "p11.paper-replay-request.v1"
RAW_HISTORY_SCHEMA = "p11.raw-history-input.v1"
REPLAY_PURPOSE_MODE = "historical_replay"
REPLAY_ACCOUNT_USE = "personal_simulation"
DECISION_SET_SCHEMA_V2 = "p12.decision-set.v2"
HISTORY_INPUTS_UNAVAILABLE = "history_inputs_unavailable"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_replay_request(
    raw_history: Mapping[str, Any],
    *,
    scenario_id: str,
    market_dates: tuple[str, ...],
    open_sessions: tuple[str, ...],
    package_id: str,
    manifest_sha256: str,
    account: Mapping[str, Any],
    policy: Mapping[str, Any],
    facts_template: Mapping[str, Any],
    run_id: str,
    exclusions: tuple[Mapping[str, Any], ...] = (),
    member_evidence: tuple[Mapping[str, Any], ...] = (),
    required_member_ids: tuple[str, ...] = (),
    signal_window: Mapping[str, Any] | None = None,
    final_day_policy: Mapping[str, Any] | None = None,
    action_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project #749 members + 8792 scenario parameters into a v1 replay request.

    raw_history supplies the member payload verbatim (source_members /
    calendar_rows / listing_rows / securities).  P12 contributes only the
    scenario/binding metadata; the strategy objects, clocks and floors stay on
    the P11 side of the seam.
    """
    if (
        not isinstance(raw_history, Mapping)
        or raw_history.get("schema_version") != RAW_HISTORY_SCHEMA
    ):
        raise FactError(
            HISTORY_INPUTS_UNAVAILABLE,
            "raw_history envelope missing or wrong schema_version; the #749 "
            "reader's p11.raw-history-input.v1 members are required for E1",
        )
    request: dict[str, Any] = {
        "schema_version": REPLAY_REQUEST_SCHEMA,
        "purpose_mode": REPLAY_PURPOSE_MODE,
        "permitted_account_use": REPLAY_ACCOUNT_USE,
        "scenario_id": scenario_id,
        "market_dates": list(market_dates),
        "open_sessions": list(open_sessions),
        "package_id": package_id,
        "manifest_sha256": manifest_sha256,
        "source_members": list(raw_history.get("source_members") or ()),
        "calendar_rows": list(raw_history.get("calendar_rows") or ()),
        "listing_rows": list(raw_history.get("listing_rows") or ()),
        "securities": list(raw_history.get("securities") or ()),
        "exclusions": list(exclusions),
        "member_evidence": list(member_evidence),
        "required_member_ids": list(required_member_ids),
        "account": dict(account),
        "policy": dict(policy),
        "facts_template": dict(facts_template),
        "run_id": run_id,
    }
    if signal_window is not None:
        request["signal_window"] = dict(signal_window)
    if final_day_policy is not None:
        request["final_day_policy"] = dict(final_day_policy)
    if action_coverage is not None:
        request["action_coverage"] = dict(action_coverage)
    return request


@dataclass(frozen=True)
class E1ReplayContext:
    """The run-bound replay fields P12 injects; members come from raw_history."""

    scenario_id: str
    market_dates: tuple[str, ...]
    open_sessions: tuple[str, ...]
    package_id: str
    manifest_sha256: str
    account: Mapping[str, Any]
    policy: Mapping[str, Any]
    facts_template: Mapping[str, Any]
    snapshot_id: str
    snapshot_manifest_digest: str
    exclusions: tuple[Mapping[str, Any], ...] = ()
    member_evidence: tuple[Mapping[str, Any], ...] = ()
    required_member_ids: tuple[str, ...] = ()
    signal_window: Mapping[str, Any] | None = None
    final_day_policy: Mapping[str, Any] | None = None
    action_coverage: Mapping[str, Any] | None = None
    clock: Callable[[], datetime] | None = None
    observed_clock: Callable[[], datetime] | None = None
    p12_evidence: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class _RunnerRef:
    ref_id: str
    version: str
    digest: str


class E1ReplayRunner:
    """StrategyRuleRunner backed by P11 run_e1_replay_day.

    assets is the verified E1RuleAssets from load_e1_rule_assets (the frozen
    runner/card/feature_registry/trend bundle); the run context carries the
    8792 scenario account, October window, #737 exclusions, coverage evidence
    and snapshot identity.  market_date comes from the day's raw_history
    envelope (the T the #749 reader bound).
    """

    def __init__(
        self,
        *,
        runner_ref_id: str,
        runner_version: str,
        assets: Any,
        context: E1ReplayContext | None,
        runner_digest: str | None = None,
    ) -> None:
        self._ref = _RunnerRef(
            runner_ref_id,
            runner_version,
            runner_digest or digest({"runner": runner_ref_id, "v": runner_version}),
        )
        self._assets = assets
        self._context = context

    @property
    def runner_ref(self) -> _RunnerRef:
        return self._ref

    def run(self, request: RunnerRequest) -> RunnerOutput:
        raw_history = request.raw_history
        if (
            not isinstance(raw_history, dict)
            or raw_history.get("schema_version") != RAW_HISTORY_SCHEMA
        ):
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "raw_history envelope missing or wrong schema_version; the job "
                "blocks the day rather than fabricating history",
            )
        if self._context is None or self._assets is None:
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "E1 replay context (scenario account / October window / coverage) "
                "and the verified E1 assets were not supplied; the fixed P11 "
                "modules are required for E1",
            )
        try:
            from quanthus.product_daily.recommendation.e1_runtime import (
                run_e1_replay_day,
            )
            from quanthus.product_daily.recommendation.paper_replay import (
                load_replay_request,
            )
        except Exception as exc:
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "P11 e1_runtime / paper_replay module unavailable: " + repr(exc),
            ) from exc

        c = self._context
        market_date_raw = raw_history.get("market_date")
        if market_date_raw is None:
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "raw_history envelope lacks market_date; the #749 reader must "
                "bind the signal day",
            )
        market_date = (
            market_date_raw
            if isinstance(market_date_raw, date)
            else date.fromisoformat(str(market_date_raw))
        )
        request_dict = build_replay_request(
            raw_history,
            scenario_id=c.scenario_id,
            market_dates=c.market_dates,
            open_sessions=c.open_sessions,
            package_id=c.package_id,
            manifest_sha256=c.manifest_sha256,
            account=c.account,
            policy=c.policy,
            facts_template=c.facts_template,
            run_id=request.run_id or "",
            exclusions=c.exclusions,
            member_evidence=c.member_evidence,
            required_member_ids=c.required_member_ids,
            signal_window=c.signal_window,
            final_day_policy=c.final_day_policy,
            action_coverage=c.action_coverage,
        )
        try:
            replay_request = load_replay_request(request_dict)
            receipt = run_e1_replay_day(
                replay_request,
                market_date,
                assets=self._assets,
                snapshot_id=c.snapshot_id,
                snapshot_manifest_digest=c.snapshot_manifest_digest,
                clock=c.clock,
                observed_clock=c.observed_clock or _utc_now,
                p12_evidence=c.p12_evidence,
            )
        except FactError:
            raise
        except Exception as exc:
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "P11 e1 replay day failed: " + repr(exc),
            ) from exc
        return self._project(receipt)

    def _project(self, receipt: Mapping[str, Any]) -> RunnerOutput:
        from quanthus.p12_daily.decision_loader import (
            load_decision_set,
            load_decision_set_dict,
        )

        publishable = bool(receipt.get("publishable"))
        disposition = receipt.get("disposition")
        package = receipt.get("package")
        source_closure = receipt.get("source_closure")
        day_block = receipt.get("day_block")
        diagnostics = {
            "diagnostics": list(receipt.get("diagnostics") or ()),
            **({"day_block": day_block} if day_block else {}),
        }
        held_signals = tuple(
            HeldSignal(
                security_code=str(h.get("security_code") or ""),
                opportunity_id=str(h.get("opportunity_id") or ""),
                action=str(h.get("action") or ""),
                target_maximum_position=(
                    None
                    if h.get("target_position_fraction") is None
                    else str(h.get("target_position_fraction"))
                ),
            )
            for h in (receipt.get("held_signals") or ())
            if isinstance(h, Mapping)
        )
        if package is None or not publishable:
            return RunnerOutput(
                decision_set=None,
                exclusions=(),
                publishable=False,
                disposition=str(disposition or "blocked"),
                diagnostics=diagnostics,
                evaluation_receipt=receipt.get("receipt"),
                source_closure=source_closure,
                held_signals=held_signals,
            )
        wire_sets = list(package.get("decision_sets") or ())
        if len(wire_sets) != 1:
            raise FactError(
                "INVALID_PACKAGE",
                "expected exactly one decision_set, got " + str(len(wire_sets)),
            )
        wire = dict(wire_sets[0])
        if wire.get("decision_set_schema") == DECISION_SET_SCHEMA_V2:
            decision_set, _ = load_decision_set(
                wire, expected_schema=DECISION_SET_SCHEMA_V2
            )
        else:
            decision_set = load_decision_set_dict(wire, mode="synthetic")
        return RunnerOutput(
            decision_set=decision_set,
            exclusions=(),
            publishable=True,
            disposition=None,
            diagnostics=None,
            evaluation_receipt=receipt.get("receipt"),
            source_closure=source_closure,
            held_signals=held_signals,
        )

