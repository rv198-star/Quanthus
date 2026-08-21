from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.contracts import EventOutcome
from quanthus.event_library.label_cards import (
    EventLabelCardError,
    LabelSettlementStatus,
    SyntheticOutcomeObservation,
    calculate_outcome_label_card_digest,
    calculate_structural_break_card_digest,
    load_outcome_label_card,
    load_structural_break_card,
    resolve_synthetic_outcome,
)
from quanthus.policy import find_project_root

ROOT = find_project_root()
STRUCTURAL_PATH = ROOT / "config/p4-event-structural-break.research.v1.json"
OUTCOME_PATH = ROOT / "config/p4-event-outcome-label.research.v1.json"


def _observations(
    *,
    positive_session: int | None = None,
    adverse_session: int | None = None,
    structural_session: int | None = None,
    missing_session: int | None = None,
) -> tuple[SyntheticOutcomeObservation, ...]:
    start = date(2020, 1, 2)
    rows: list[SyntheticOutcomeObservation] = []
    for index in range(20):
        session = index + 1
        rows.append(
            SyntheticOutcomeObservation(
                session_date=start + timedelta(days=index),
                favorable_excursion=(
                    None
                    if missing_session == session
                    else Decimal("0.15")
                    if positive_session == session
                    else Decimal("0.05")
                ),
                adverse_excursion=(
                    None
                    if missing_session == session
                    else Decimal("0.07")
                    if adverse_session == session
                    else Decimal("0.02")
                ),
                structural_break=(
                    None
                    if missing_session == session
                    else structural_session == session
                ),
            )
        )
    return tuple(rows)


def test_repository_p4_label_proposals_are_digest_bound_and_outcome_blind() -> None:
    structural = load_structural_break_card(STRUCTURAL_PATH)
    outcome = load_outcome_label_card(OUTCOME_PATH)

    assert structural.lifecycle == "approved"
    assert outcome.lifecycle == "approved"
    assert structural.governance.approval_state == "approved"
    assert outcome.governance.approval_state == "approved"
    assert structural.governance.decision_reference.endswith("#issuecomment-5366610770")
    assert outcome.governance.decision_reference.endswith("#issuecomment-5366610770")
    assert structural.semantic_digest == calculate_structural_break_card_digest(STRUCTURAL_PATH)
    assert outcome.semantic_digest == calculate_outcome_label_card_digest(OUTCOME_PATH)
    assert outcome.structural_break.semantic_digest == structural.semantic_digest
    assert outcome.observation_sessions == 20
    assert outcome.positive_threshold == Decimal("0.15")
    assert outcome.adverse_threshold == Decimal("0.07")
    assert outcome.bindings.maximum_overlap_sessions == 22
    assert outcome.output_values == ("positive", "neutral", "failure", "not_executable")
    structural.require_labeling_authority()
    outcome.require_labeling_authority()


def test_label_card_digest_detects_semantic_tampering(tmp_path: Path) -> None:
    raw = json.loads(OUTCOME_PATH.read_text(encoding="utf-8"))
    raw["positive_threshold"] = "0.14"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(EventLabelCardError):
        load_outcome_label_card(path)


def test_first_passage_positive_and_failure_ordering() -> None:
    card = load_outcome_label_card(OUTCOME_PATH)

    positive = resolve_synthetic_outcome(
        card,
        _observations(positive_session=3),
        executable=True,
        full_horizon_available=True,
    )
    assert positive.status is LabelSettlementStatus.SETTLED
    assert positive.outcome is EventOutcome.POSITIVE
    assert positive.reason_code == "positive_threshold"

    adverse_first = resolve_synthetic_outcome(
        card,
        _observations(positive_session=5, adverse_session=3),
        executable=True,
        full_horizon_available=True,
    )
    assert adverse_first.outcome is EventOutcome.FAILURE
    assert adverse_first.reason_code == "adverse_threshold"

    structural_first = resolve_synthetic_outcome(
        card,
        _observations(positive_session=5, structural_session=2),
        executable=True,
        full_horizon_available=True,
    )
    assert structural_first.outcome is EventOutcome.FAILURE
    assert structural_first.reason_code == "structural_break"


def test_same_session_ambiguous_thresholds_fail_conservatively() -> None:
    card = load_outcome_label_card(OUTCOME_PATH)

    overlap = resolve_synthetic_outcome(
        card,
        _observations(positive_session=4, adverse_session=4),
        executable=True,
        full_horizon_available=True,
    )
    assert overlap.outcome is EventOutcome.FAILURE
    assert overlap.reason_code == "adverse_threshold"

    structural_overlap = resolve_synthetic_outcome(
        card,
        _observations(positive_session=4, structural_session=4),
        executable=True,
        full_horizon_available=True,
    )
    assert structural_overlap.outcome is EventOutcome.FAILURE
    assert structural_overlap.reason_code == "structural_break"


def test_neutral_not_executable_censored_and_unavailable_are_distinct() -> None:
    card = load_outcome_label_card(OUTCOME_PATH)

    neutral = resolve_synthetic_outcome(
        card,
        _observations(),
        executable=True,
        full_horizon_available=True,
    )
    assert neutral.status is LabelSettlementStatus.SETTLED
    assert neutral.outcome is EventOutcome.NEUTRAL
    assert neutral.reason_code == "observation_window_ended_without_terminal_condition"

    not_executable = resolve_synthetic_outcome(
        card,
        (),
        executable=False,
        full_horizon_available=True,
    )
    assert not_executable.status is LabelSettlementStatus.SETTLED
    assert not_executable.outcome is EventOutcome.NOT_EXECUTABLE

    censored = resolve_synthetic_outcome(
        card,
        (),
        executable=True,
        full_horizon_available=False,
    )
    assert censored.status is LabelSettlementStatus.CENSORED
    assert censored.outcome is None

    unavailable = resolve_synthetic_outcome(
        card,
        _observations(missing_session=7),
        executable=True,
        full_horizon_available=True,
    )
    assert unavailable.status is LabelSettlementStatus.UNAVAILABLE
    assert unavailable.outcome is None
    assert unavailable.reason_code == "structural_break_evidence_unavailable"
