"""Deterministic reconciliation of P3 opportunity-funnel evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

FUNNEL_VERSION = "1.0.0"


class OpportunityFunnelError(ValueError):
    """Raised when funnel evidence cannot be reconciled for publication."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class FunnelStage(str, Enum):
    CANDIDATE_TO_SCORE = "candidate_to_score"
    SCORE_TO_VETO = "score_to_veto"
    ENTRY_TO_ADD = "entry_to_add"
    HOLD_TO_EXIT = "hold_to_exit"


class FunnelDisposition(str, Enum):
    ADVANCED = "advanced"
    STOPPED = "stopped"
    UNAVAILABLE = "unavailable"


class FunnelOutcomeKind(str, Enum):
    REALIZED = "realized"
    DIAGNOSTIC = "diagnostic"
    PENDING = "pending"


_STAGE_ORDER = {stage: index for index, stage in enumerate(FunnelStage)}
_NEXT_STAGE = {
    FunnelStage.CANDIDATE_TO_SCORE: FunnelStage.SCORE_TO_VETO,
    FunnelStage.SCORE_TO_VETO: FunnelStage.ENTRY_TO_ADD,
    FunnelStage.ENTRY_TO_ADD: FunnelStage.HOLD_TO_EXIT,
}


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise OpportunityFunnelError("invalid_identity", f"{field_name} must not be empty")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


@dataclass(frozen=True)
class FunnelObservation:
    market_date: date
    stage: FunnelStage
    opportunity_id: str
    input_event_id: str
    disposition: FunnelDisposition
    reason_codes: tuple[str, ...]
    output_event_id: str | None = None
    decision_id: str | None = None
    outcome_kind: FunnelOutcomeKind = FunnelOutcomeKind.PENDING
    outcome_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, FunnelStage):
            raise OpportunityFunnelError("invalid_stage", "stage must be a FunnelStage")
        if not isinstance(self.disposition, FunnelDisposition):
            raise OpportunityFunnelError(
                "invalid_disposition", "disposition must be a FunnelDisposition"
            )
        if not isinstance(self.outcome_kind, FunnelOutcomeKind):
            raise OpportunityFunnelError(
                "invalid_outcome", "outcome_kind must be a FunnelOutcomeKind"
            )
        _require_text(self.opportunity_id, "opportunity_id")
        _require_text(self.input_event_id, "input_event_id")
        if self.decision_id is not None:
            _require_text(self.decision_id, "decision_id")
        if not self.reason_codes or any(not reason.strip() for reason in self.reason_codes):
            raise OpportunityFunnelError(
                "invalid_reason_codes", "reason_codes must contain non-empty values"
            )
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise OpportunityFunnelError(
                "invalid_reason_codes", "reason_codes must not contain duplicates"
            )
        if self.disposition is FunnelDisposition.ADVANCED:
            _require_text(self.output_event_id, "output_event_id")
        elif self.output_event_id is not None:
            raise OpportunityFunnelError(
                "invalid_transition", "only an advanced observation may declare output_event_id"
            )
        if self.outcome_kind is FunnelOutcomeKind.PENDING:
            if self.outcome_ref is not None:
                raise OpportunityFunnelError(
                    "invalid_outcome", "a pending outcome cannot declare outcome_ref"
                )
        else:
            if not isinstance(self.outcome_ref, str) or not self.outcome_ref.strip():
                raise OpportunityFunnelError(
                    "invalid_outcome", "a realized or diagnostic outcome requires outcome_ref"
                )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "stage": self.stage.value,
            "opportunity_id": self.opportunity_id,
            "input_event_id": self.input_event_id,
            "disposition": self.disposition.value,
            "reason_codes": sorted(self.reason_codes),
            "output_event_id": self.output_event_id,
            "decision_id": self.decision_id,
            "outcome_kind": self.outcome_kind.value,
            "outcome_ref": self.outcome_ref,
        }


@dataclass(frozen=True)
class FunnelStageSummary:
    stage: FunnelStage
    input_count: int
    advanced_count: int
    stopped_count: int
    unavailable_count: int
    realized_count: int
    diagnostic_count: int
    pending_count: int

    @property
    def reconciled(self) -> bool:
        return self.input_count == (
            self.advanced_count + self.stopped_count + self.unavailable_count
        ) and self.input_count == (self.realized_count + self.diagnostic_count + self.pending_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "input_count": self.input_count,
            "advanced_count": self.advanced_count,
            "stopped_count": self.stopped_count,
            "unavailable_count": self.unavailable_count,
            "realized_count": self.realized_count,
            "diagnostic_count": self.diagnostic_count,
            "pending_count": self.pending_count,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True)
class OpportunityFunnelReport:
    report_id: str
    funnel_version: str
    run_id: str
    period_start: date
    period_end: date
    observations: tuple[FunnelObservation, ...]
    stage_summaries: tuple[FunnelStageSummary, ...]

    @property
    def source_event_count(self) -> int:
        return len(self.observations)

    @property
    def reconciled(self) -> bool:
        return all(summary.reconciled for summary in self.stage_summaries)

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "funnel_version": self.funnel_version,
            "run_id": self.run_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "source_event_count": self.source_event_count,
            "observations": [item.to_dict() for item in self.observations],
            "stage_summaries": [item.to_dict() for item in self.stage_summaries],
            "reconciled": self.reconciled,
            "execution_authorized": self.execution_authorized,
        }


def _validate_expected_inputs(
    expected_input_event_ids: Mapping[FunnelStage, Sequence[str]],
) -> dict[FunnelStage, tuple[str, ...]]:
    if set(expected_input_event_ids) != set(FunnelStage):
        raise OpportunityFunnelError(
            "incomplete_stage_set", "expected inputs must declare every fixed funnel stage"
        )
    normalized: dict[FunnelStage, tuple[str, ...]] = {}
    for stage in FunnelStage:
        values = tuple(expected_input_event_ids[stage])
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise OpportunityFunnelError(
                "invalid_identity", f"{stage.value} expected input ids must not be empty"
            )
        if len(values) != len(set(values)):
            raise OpportunityFunnelError(
                "duplicate_source_event", f"{stage.value} expected input ids must be unique"
            )
        normalized[stage] = tuple(sorted(values))
    if not any(normalized.values()):
        raise OpportunityFunnelError(
            "empty_funnel", "a publishable funnel requires at least one source event"
        )
    return normalized


def _validate_chains(observations: Sequence[FunnelObservation]) -> None:
    outputs: dict[str, FunnelObservation] = {}
    for observation in observations:
        if observation.output_event_id is None:
            continue
        if observation.output_event_id in outputs:
            raise OpportunityFunnelError(
                "duplicate_output_event", "one output event cannot be produced more than once"
            )
        outputs[observation.output_event_id] = observation

    for observation in observations:
        predecessor = outputs.get(observation.input_event_id)
        if predecessor is None:
            continue
        if _NEXT_STAGE.get(predecessor.stage) is not observation.stage:
            raise OpportunityFunnelError(
                "invalid_stage_chain", "a linked output must enter the next fixed funnel stage"
            )
        if predecessor.opportunity_id != observation.opportunity_id:
            raise OpportunityFunnelError(
                "opportunity_identity_mismatch",
                "linked funnel stages must retain opportunity identity",
            )


def build_opportunity_funnel(
    *,
    run_id: str,
    period_start: date,
    period_end: date,
    observations: Sequence[FunnelObservation],
    expected_input_event_ids: Mapping[FunnelStage, Sequence[str]],
) -> OpportunityFunnelReport:
    """Reconcile all fixed-stage source events into a canonical, non-executing report."""

    _require_text(run_id, "run_id")
    if period_start > period_end:
        raise OpportunityFunnelError("invalid_period", "period_start must not follow period_end")
    expected = _validate_expected_inputs(expected_input_event_ids)

    observed_keys = [(item.stage, item.input_event_id) for item in observations]
    if len(observed_keys) != len(set(observed_keys)):
        raise OpportunityFunnelError(
            "duplicate_observation", "one stage may classify each input event exactly once"
        )
    if any(not period_start <= item.market_date <= period_end for item in observations):
        raise OpportunityFunnelError(
            "observation_outside_period", "every observation must fall inside the report period"
        )

    for stage in FunnelStage:
        actual = tuple(sorted(item.input_event_id for item in observations if item.stage is stage))
        if actual != expected[stage]:
            raise OpportunityFunnelError(
                "source_event_mismatch",
                f"{stage.value} observations do not match the expected source event set",
            )
    _validate_chains(observations)

    ordered = tuple(
        sorted(
            observations,
            key=lambda item: (
                item.market_date,
                _STAGE_ORDER[item.stage],
                item.opportunity_id,
                item.input_event_id,
            ),
        )
    )
    summaries = tuple(
        FunnelStageSummary(
            stage=stage,
            input_count=len(stage_items),
            advanced_count=sum(
                item.disposition is FunnelDisposition.ADVANCED for item in stage_items
            ),
            stopped_count=sum(
                item.disposition is FunnelDisposition.STOPPED for item in stage_items
            ),
            unavailable_count=sum(
                item.disposition is FunnelDisposition.UNAVAILABLE for item in stage_items
            ),
            realized_count=sum(
                item.outcome_kind is FunnelOutcomeKind.REALIZED for item in stage_items
            ),
            diagnostic_count=sum(
                item.outcome_kind is FunnelOutcomeKind.DIAGNOSTIC for item in stage_items
            ),
            pending_count=sum(
                item.outcome_kind is FunnelOutcomeKind.PENDING for item in stage_items
            ),
        )
        for stage in FunnelStage
        for stage_items in [tuple(item for item in ordered if item.stage is stage)]
    )
    report_payload = {
        "funnel_version": FUNNEL_VERSION,
        "run_id": run_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "observations": [item.to_dict() for item in ordered],
        "stage_summaries": [item.to_dict() for item in summaries],
    }
    report_hash = hashlib.sha256(_canonical_bytes(report_payload)).hexdigest()[:24]
    return OpportunityFunnelReport(
        report_id=f"funnel_{report_hash}",
        funnel_version=FUNNEL_VERSION,
        run_id=run_id,
        period_start=period_start,
        period_end=period_end,
        observations=ordered,
        stage_summaries=summaries,
    )
