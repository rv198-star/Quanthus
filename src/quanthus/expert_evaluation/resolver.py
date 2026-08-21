"""Independent adapter from the accepted P4 label corpus to v2 settlements."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from quanthus.event_library.label_corpus import verify_physical_label_corpus
from quanthus.expert_evaluation.contracts import (
    ArtifactRef,
    ExpertObservation,
    OutcomeSettlement,
    RawValueType,
    SettlementState,
)
from quanthus.expert_evaluation.serialization import ExpertEvaluationError

_OUTCOME_ORDER = {"failure": 0, "neutral": 1, "positive": 2}
_ASSESSMENT_ORDER = {"fragile": 0, "neutral": 1, "stable": 2}


@dataclass(frozen=True)
class P4LabelRow:
    event_id: str
    entry_date: date
    settlement_status: str
    outcome: str | None
    reason_code: str
    terminal_date: date | None
    outcome_evidence_digest: str


@dataclass(frozen=True)
class Resolution:
    observation_digest: str
    ledger_state: str
    settlement: OutcomeSettlement | None


def load_verified_p4_labels(root: Path) -> tuple[dict[str, P4LabelRow], ArtifactRef]:
    """Verify the sealed package before exposing any outcome row."""

    verified = verify_physical_label_corpus(root)
    try:
        raw_rows = [json.loads(line) for line in (root / "labels.jsonl").read_text().splitlines()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError("label_corpus_unreadable", "P4 labels are unreadable") from exc
    rows: dict[str, P4LabelRow] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ExpertEvaluationError("field_mismatch", "P4 label row must be an object")
        try:
            event_id = str(raw["event_id"])
            terminal = raw["terminal_date"]
            row = P4LabelRow(
                event_id=event_id,
                entry_date=date.fromisoformat(str(raw["entry_date"])),
                settlement_status=str(raw["settlement_status"]),
                outcome=None if raw["outcome"] is None else str(raw["outcome"]),
                reason_code=str(raw["reason_code"]),
                terminal_date=None if terminal is None else date.fromisoformat(str(terminal)),
                outcome_evidence_digest=str(raw["outcome_evidence_digest"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExpertEvaluationError("field_mismatch", "P4 label row is incomplete") from exc
        if not event_id or event_id in rows:
            raise ExpertEvaluationError("duplicate_identity", "P4 label event ids differ")
        rows[event_id] = row
    if len(rows) != verified.total_events:
        raise ExpertEvaluationError("label_corpus_drift", "P4 label row count differs")
    return rows, ArtifactRef(
        "p4-label-corpus.accepted",
        "1.0.0",
        verified.content_digest,
    )


def resolve_observation(
    observation: ExpertObservation,
    label: P4LabelRow | None,
    *,
    resolver: ArtifactRef,
    label_corpus: ArtifactRef,
) -> Resolution:
    """Resolve without importing or invoking any expert implementation."""

    if observation.raw_value_type is not RawValueType.ORDINAL:
        raise ExpertEvaluationError("value_type_mismatch", "P4 adapter requires ordinal input")
    if observation.applicability.value != "applicable":
        raise ExpertEvaluationError("settlement_state_conflict", "abstentions are not resolved")
    if label is None:
        anchor = observation.observation_anchor.date()
        settlement = OutcomeSettlement(
            "producer.p4-label-resolver",
            "1.0.0",
            observation.digest,
            SettlementState.UNAVAILABLE,
            anchor,
            anchor,
            None,
            resolver,
            RawValueType.ORDINAL,
            None,
            None,
            "missing_verified_label_row",
        )
        return Resolution(observation.digest, "unavailable", settlement)
    if label.event_id != observation.subject_id:
        raise ExpertEvaluationError("subject_mismatch", "label and observation subjects differ")
    if label.settlement_status == "settled" and label.outcome == "not_executable":
        return Resolution(observation.digest, "not_executable", None)
    if label.settlement_status == "settled":
        raw_value = observation.raw_value
        if (
            label.outcome not in _OUTCOME_ORDER
            or not isinstance(raw_value, str)
            or raw_value not in _ASSESSMENT_ORDER
        ):
            raise ExpertEvaluationError("value_type_mismatch", "ordinal state is not registered")
        target_score = Decimal(
            abs(_ASSESSMENT_ORDER[raw_value] - _OUTCOME_ORDER[label.outcome])
        )
        settlement = OutcomeSettlement(
            "producer.p4-label-resolver",
            "1.0.0",
            observation.digest,
            SettlementState.SETTLED,
            label.entry_date,
            label.terminal_date or label.entry_date,
            label_corpus,
            resolver,
            RawValueType.ORDINAL,
            label.outcome,
            target_score,
            None,
        )
        return Resolution(observation.digest, "settled", settlement)
    states = {
        "censored": SettlementState.CENSORED,
        "unavailable": SettlementState.UNAVAILABLE,
    }
    try:
        state = states[label.settlement_status]
    except KeyError as exc:
        raise ExpertEvaluationError(
            "settlement_state_conflict", "P4 status is unsupported"
        ) from exc
    settlement = OutcomeSettlement(
        "producer.p4-label-resolver",
        "1.0.0",
        observation.digest,
        state,
        label.entry_date,
        label.terminal_date or label.entry_date,
        None,
        resolver,
        RawValueType.ORDINAL,
        None,
        None,
        label.reason_code,
    )
    return Resolution(observation.digest, label.settlement_status, settlement)
