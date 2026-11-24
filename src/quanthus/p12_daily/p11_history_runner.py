"""E1 real-history runner: P11 raw-history-input.v1 -> P12 RunnerOutput.

Issue #718 batch-A step (E1 seam). This adapter owns ONLY the seam translation
between the P11 run_daily_advice_from_history entry point and the P12
StrategyRuleRunner protocol. It never computes listing age, never rebuilds
bars/calendar/facts, and never constructs a P11 object (runner / SeamResources /
card / policy / facts / account / split / source): every one is injected by the
caller, so the adapter stays a thin, source-faithful carrier.

P11 owns the algorithms (event scan, feature panel, listing sessions, expert +
veto). P12 owns the orchestration, the publish gate and the run receipt. The
P11 recommendation modules are merged into this integration tree; the import
still happens inside run so a missing module is a named whole-day block
(history_inputs_unavailable), never a silent fallback to a synthetic decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from quanthus.shared.fact_contracts import FactError, digest

from .rule_runner import RunnerOutput, RunnerRequest

RAW_HISTORY_SCHEMA = "p11.raw-history-input.v1"
HISTORY_INPUTS_UNAVAILABLE = "history_inputs_unavailable"
DECISION_SET_SCHEMA_V2 = "p12.decision-set.v2"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class P11Bindings:
    """The injected P11 objects run_daily_advice_from_history needs."""

    runner: Any
    resources: Any
    facts: Any
    policy: Any
    account: Any
    card: Any
    split_policy: Any
    source_policy: Any
    snapshot_id: str
    snapshot_manifest_digest: str
    clock: Callable[[], datetime] | None = None
    # Versioned anchor policy forwarded to run_daily_advice_from_history.
    # "equivalent_day" resolves an is_open session whose stored open_at anchor
    # is absent when the local day bound is provably inside the listing; each
    # resolved session is named on the receipt membership_proof.  The P12
    # replay fixes this mode for the October acceptance run.
    anchor_mode: str = "equivalent_day"


@dataclass(frozen=True)
class _RunnerRef:
    ref_id: str
    version: str
    digest: str


class P11HistoryRunner:
    """StrategyRuleRunner backed by P11 run_daily_advice_from_history."""

    def __init__(
        self,
        *,
        runner_ref_id: str,
        runner_version: str,
        bindings: P11Bindings | None,
        runner_digest: str | None = None,
    ) -> None:
        self._ref = _RunnerRef(
            runner_ref_id,
            runner_version,
            runner_digest or digest({"runner": runner_ref_id, "v": runner_version}),
        )
        self._bindings = bindings

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
                "raw_history envelope missing or wrong schema_version; "
                "the job blocks the day rather than fabricating history",
            )
        if self._bindings is None:
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "P11 runner bindings (runner/resources/card/policy/facts/account) "
                "were not supplied; the fixed P11 modules are required for E1",
            )
        try:
            from quanthus.product_daily.recommendation.history_input import (
                run_daily_advice_from_history,
            )
        except Exception as exc:
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "P11 history_input module unavailable: " + repr(exc),
            ) from exc

        b = self._bindings
        try:
            output = run_daily_advice_from_history(
                raw_history,
                runner=b.runner,
                resources=b.resources,
                facts=b.facts,
                policy=b.policy,
                account=b.account,
                run_id=request.run_id or "",
                snapshot_id=b.snapshot_id,
                snapshot_manifest_digest=b.snapshot_manifest_digest,
                card=b.card,
                split_policy=b.split_policy,
                source_policy=b.source_policy,
                anchor_mode=b.anchor_mode,
                pending_security_codes=[
                    p.security_code for p in (request.pending_securities or ())
                ],
                clock=b.clock or _utc_now,
            )
        except FactError:
            raise
        except Exception as exc:
            raise FactError(
                HISTORY_INPUTS_UNAVAILABLE,
                "P11 history run failed: " + repr(exc),
            ) from exc
        return self._project(output)

    def _project(self, output: Any) -> RunnerOutput:
        from quanthus.p12_daily.decision_loader import (
            load_decision_set,
            load_decision_set_dict,
        )

        result = output.result
        publishable = bool(getattr(result, "publishable", False))
        package = getattr(result, "package", None)
        source_closure = getattr(output, "source_closure", None)
        held_signals = tuple(getattr(output, "held_signals", ()) or ())
        receipt = getattr(output, "receipt", None)
        if package is None or not publishable:
            return RunnerOutput(
                decision_set=None,
                exclusions=(),
                publishable=False,
                disposition=str(getattr(result, "disposition", "blocked")),
                diagnostics={
                    "diagnostics": list(getattr(result, "diagnostics", ()) or ()),
                },
                evaluation_receipt=(
                    receipt.to_dict() if receipt is not None else None
                ),
                source_closure=source_closure,
                held_signals=held_signals,
            )
        wire_sets = [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in package.decision_sets
        ]
        if len(wire_sets) != 1:
            raise FactError(
                "INVALID_PACKAGE",
                "expected exactly one decision_set, got " + str(len(wire_sets)),
            )
        wire = wire_sets[0]
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
            evaluation_receipt=(
                receipt.to_dict() if receipt is not None else None
            ),
            source_closure=source_closure,
            held_signals=held_signals,
        )
