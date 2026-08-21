"""Frozen P4 outcome and structural-break card contracts.

The contracts are deliberately outcome-blind. Loading and validating draft cards is allowed before
human approval; resolving a real development corpus is not. Pure synthetic fixtures may exercise
terminal-condition ordering without reading production outcomes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from quanthus.contracts import EventOutcome
from quanthus.event_library.contracts import require_sha256_digest, sha256_digest


class EventLabelCardError(ValueError):
    """Raised when a P4 label card is incomplete or violates the frozen boundary."""


class LabelSettlementStatus(str, Enum):
    SETTLED = "settled"
    CENSORED = "censored"
    UNAVAILABLE = "unavailable"


_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_ALLOWED_LIFECYCLES = {"draft", "approved", "deprecated"}
_EXPECTED_PROFILE = "close-to-next-open-v1"
_EXPECTED_STRUCTURAL_FORMULA = (
    "(total_return_index < sma60 and sma20 <= sma60) or authoritative_listing_termination"
)
_EXPECTED_EVALUATION_ORDER = (
    "not_executable",
    "failure",
    "positive",
    "neutral",
)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise EventLabelCardError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise EventLabelCardError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventLabelCardError(f"{path} must be a non-empty string")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise EventLabelCardError(f"{path} must be boolean")
    return value


def _positive_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EventLabelCardError(f"{path} must be a positive integer")
    return value


def _decimal(value: object, path: str) -> Decimal:
    if not isinstance(value, str):
        raise EventLabelCardError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise EventLabelCardError(f"{path} must be a decimal string") from exc
    if not parsed.is_finite():
        raise EventLabelCardError(f"{path} must be finite")
    return parsed


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise EventLabelCardError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _text_tuple(value: object, path: str) -> tuple[str, ...]:
    items = tuple(
        _text(item, f"{path}[{index}]") for index, item in enumerate(_sequence(value, path))
    )
    if not items or len(items) != len(set(items)):
        raise EventLabelCardError(f"{path} must contain unique non-empty values")
    return items


@dataclass(frozen=True)
class CardGovernance:
    approval_state: str
    approval_authority: str
    decision_reference: str

    def __post_init__(self) -> None:
        if self.approval_state not in {"pending_human_approval", "approved", "deprecated"}:
            raise EventLabelCardError("unsupported label-card approval state")
        if self.approval_authority != "human":
            raise EventLabelCardError("label-card approval authority must remain human")

    def to_dict(self) -> dict[str, str]:
        return {
            "approval_state": self.approval_state,
            "approval_authority": self.approval_authority,
            "decision_reference": self.decision_reference,
        }


@dataclass(frozen=True)
class StructuralBreakBindings:
    accepted_profile: str
    p0_policy_version: str
    normalized_action_authority: str
    price_feature_semantics: str

    def __post_init__(self) -> None:
        if self.accepted_profile != _EXPECTED_PROFILE:
            raise EventLabelCardError("structural-break card profile differs")
        if self.normalized_action_authority != "accepted_p1_normalized_corporate_actions":
            raise EventLabelCardError("structural-break card must use accepted P1 actions")
        if self.price_feature_semantics != "p3_total_return_index_v1":
            raise EventLabelCardError("structural-break price-feature semantics differ")

    def to_dict(self) -> dict[str, str]:
        return {
            "accepted_profile": self.accepted_profile,
            "p0_policy_version": self.p0_policy_version,
            "normalized_action_authority": self.normalized_action_authority,
            "price_feature_semantics": self.price_feature_semantics,
        }


@dataclass(frozen=True)
class StructuralBreakCard:
    path: Path
    schema_version: str
    card_id: str
    card_version: str
    lifecycle: str
    owner: str
    purpose: str
    evaluation_anchor: str
    formula: str
    short_sma_sessions: int
    long_sma_sessions: int
    minimum_history_sessions: int
    listing_termination_policy: str
    suspension_policy: str
    missing_data_policy: str
    corporate_action_policy: str
    non_claims: tuple[str, ...]
    bindings: StructuralBreakBindings
    governance: CardGovernance
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise EventLabelCardError("structural-break schema must be 1.0.0")
        if self.card_id != "p4-event.structural-break-trend":
            raise EventLabelCardError("unexpected P4 structural-break card id")
        if _SEMVER.fullmatch(self.card_version) is None:
            raise EventLabelCardError("structural-break version must use semantic versioning")
        if self.lifecycle not in _ALLOWED_LIFECYCLES:
            raise EventLabelCardError("unsupported structural-break lifecycle")
        if self.lifecycle == "approved" and self.governance.approval_state != "approved":
            raise EventLabelCardError("approved structural-break card requires human approval")
        if self.lifecycle == "draft" and self.governance.approval_state != "pending_human_approval":
            raise EventLabelCardError("draft structural-break card must await human approval")
        if self.evaluation_anchor != "each_market_session_close_after_executable_entry":
            raise EventLabelCardError("structural-break evaluation anchor differs")
        if self.formula != _EXPECTED_STRUCTURAL_FORMULA:
            raise EventLabelCardError("structural-break formula differs")
        if (self.short_sma_sessions, self.long_sma_sessions) != (20, 60):
            raise EventLabelCardError("structural-break moving-average windows differ")
        if self.minimum_history_sessions != 60:
            raise EventLabelCardError("structural-break minimum history differs")
        if self.listing_termination_policy != "authoritative_termination_is_structural_break":
            raise EventLabelCardError("listing-termination policy differs")
        if self.suspension_policy != "suspension_counts_session_but_does_not_trigger_break":
            raise EventLabelCardError("structural-break suspension policy differs")
        if self.missing_data_policy != "unavailable_not_failure_or_neutral":
            raise EventLabelCardError("structural-break missing-data policy must fail closed")
        if self.corporate_action_policy != (
            "use_corporate_action_normalized_total_return_close_path"
        ):
            raise EventLabelCardError("structural-break corporate-action policy differs")
        require_sha256_digest(self.semantic_digest, "semantic_digest")

    @property
    def computed_semantic_digest(self) -> str:
        return sha256_digest(self.semantic_payload())

    def require_labeling_authority(self) -> None:
        if self.lifecycle != "approved" or self.governance.approval_state != "approved":
            raise EventLabelCardError(
                "real outcome reads are blocked until the exact structural-break digest is approved"
            )

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "card_id": self.card_id,
            "card_version": self.card_version,
            "purpose": self.purpose,
            "evaluation_anchor": self.evaluation_anchor,
            "formula": self.formula,
            "short_sma_sessions": self.short_sma_sessions,
            "long_sma_sessions": self.long_sma_sessions,
            "minimum_history_sessions": self.minimum_history_sessions,
            "listing_termination_policy": self.listing_termination_policy,
            "suspension_policy": self.suspension_policy,
            "missing_data_policy": self.missing_data_policy,
            "corporate_action_policy": self.corporate_action_policy,
            "non_claims": list(self.non_claims),
            "bindings": self.bindings.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.semantic_payload(),
            "lifecycle": self.lifecycle,
            "owner": self.owner,
            "governance": self.governance.to_dict(),
            "semantic_digest": self.semantic_digest,
        }


@dataclass(frozen=True)
class StructuralBreakReference:
    card_id: str
    card_version: str
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.card_id != "p4-event.structural-break-trend":
            raise EventLabelCardError("outcome card structural-break id differs")
        require_sha256_digest(self.semantic_digest, "structural_break.semantic_digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "card_id": self.card_id,
            "card_version": self.card_version,
            "semantic_digest": self.semantic_digest,
        }


@dataclass(frozen=True)
class OutcomeBindings:
    accepted_profile: str
    p0_policy_version: str
    candidate_rule_id: str
    candidate_rule_version: str
    candidate_rule_digest: str
    split_policy_id: str
    split_policy_version: str
    split_policy_digest: str
    maximum_overlap_sessions: int

    def __post_init__(self) -> None:
        if self.accepted_profile != _EXPECTED_PROFILE:
            raise EventLabelCardError("outcome card profile differs")
        if self.candidate_rule_id != "p4-event.price-volume-state-transition":
            raise EventLabelCardError("outcome card candidate rule differs")
        require_sha256_digest(self.candidate_rule_digest, "candidate_rule_digest")
        if self.split_policy_id != "p4-event-date-split":
            raise EventLabelCardError("outcome card split policy differs")
        require_sha256_digest(self.split_policy_digest, "split_policy_digest")
        if self.maximum_overlap_sessions != 22:
            raise EventLabelCardError("outcome card split overlap binding differs")

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted_profile": self.accepted_profile,
            "p0_policy_version": self.p0_policy_version,
            "candidate_rule_id": self.candidate_rule_id,
            "candidate_rule_version": self.candidate_rule_version,
            "candidate_rule_digest": self.candidate_rule_digest,
            "split_policy_id": self.split_policy_id,
            "split_policy_version": self.split_policy_version,
            "split_policy_digest": self.split_policy_digest,
            "maximum_overlap_sessions": self.maximum_overlap_sessions,
        }


@dataclass(frozen=True)
class OutcomeLabelCard:
    path: Path
    schema_version: str
    card_id: str
    card_version: str
    lifecycle: str
    owner: str
    purpose: str
    entry_anchor: str
    observation_sessions: int
    entry_session_is_session_one: bool
    positive_threshold: Decimal
    adverse_threshold: Decimal
    price_path_basis: str
    corporate_action_policy: str
    transaction_cost_policy: str
    evaluation_order: tuple[str, ...]
    same_session_overlap_policy: str
    structural_break_same_session_policy: str
    end_of_window_policy: str
    incomplete_tail_policy: str
    missing_future_evidence_policy: str
    suspension_policy: str
    output_values: tuple[str, ...]
    non_claims: tuple[str, ...]
    structural_break: StructuralBreakReference
    bindings: OutcomeBindings
    governance: CardGovernance
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise EventLabelCardError("outcome-card schema must be 1.0.0")
        if self.card_id != "p4-event.outcome-first-passage":
            raise EventLabelCardError("unexpected P4 outcome card id")
        if _SEMVER.fullmatch(self.card_version) is None:
            raise EventLabelCardError("outcome-card version must use semantic versioning")
        if self.lifecycle not in _ALLOWED_LIFECYCLES:
            raise EventLabelCardError("unsupported outcome-card lifecycle")
        if self.lifecycle == "approved" and self.governance.approval_state != "approved":
            raise EventLabelCardError("approved outcome card requires human approval")
        if self.lifecycle == "draft" and self.governance.approval_state != "pending_human_approval":
            raise EventLabelCardError("draft outcome card must await human approval")
        if self.entry_anchor != "T_plus_1_actual_executable_open":
            raise EventLabelCardError("outcome-card entry anchor differs")
        if self.observation_sessions != 20 or self.entry_session_is_session_one is not True:
            raise EventLabelCardError("outcome-card observation horizon differs")
        if self.observation_sessions > self.bindings.maximum_overlap_sessions:
            raise EventLabelCardError("outcome horizon exceeds approved split overlap")
        if self.positive_threshold != Decimal("0.15"):
            raise EventLabelCardError("positive threshold differs from P4 proposal")
        if self.adverse_threshold != Decimal("0.07"):
            raise EventLabelCardError("adverse threshold differs from P4 proposal")
        if self.price_path_basis != "hypothetical_one_share_economic_value_using_daily_high_low":
            raise EventLabelCardError("outcome price-path basis differs")
        if self.corporate_action_policy != (
            "apply_accepted_cash_dividend_and_bonus_share_actions_before_session_thresholds"
        ):
            raise EventLabelCardError("outcome corporate-action policy differs")
        if self.transaction_cost_policy != "exclude_costs_label_price_path_not_trade_pnl":
            raise EventLabelCardError("outcome transaction-cost policy differs")
        if self.evaluation_order != _EXPECTED_EVALUATION_ORDER:
            raise EventLabelCardError("outcome evaluation order differs")
        if self.same_session_overlap_policy != "failure_wins_when_positive_and_adverse_both_cross":
            raise EventLabelCardError("same-session overlap policy differs")
        if self.structural_break_same_session_policy != "failure_wins_over_positive":
            raise EventLabelCardError("structural-break overlap policy differs")
        if self.end_of_window_policy != "neutral_after_session_20_when_no_terminal_condition":
            raise EventLabelCardError("end-of-window policy differs")
        if self.incomplete_tail_policy != "censored_excluded_from_three_class_corpus":
            raise EventLabelCardError("incomplete-tail policy differs")
        if self.missing_future_evidence_policy != "unavailable_not_neutral_or_failure":
            raise EventLabelCardError("missing future evidence must fail closed")
        if self.suspension_policy != "market_session_counts_no_threshold_without_valid_bar":
            raise EventLabelCardError("outcome suspension policy differs")
        if self.output_values != ("positive", "neutral", "failure", "not_executable"):
            raise EventLabelCardError("outcome values differ")
        require_sha256_digest(self.semantic_digest, "semantic_digest")

    @property
    def computed_semantic_digest(self) -> str:
        return sha256_digest(self.semantic_payload())

    def require_labeling_authority(self) -> None:
        if self.lifecycle != "approved" or self.governance.approval_state != "approved":
            raise EventLabelCardError(
                "real outcome reads are blocked until the exact outcome-card digest is approved"
            )

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "card_id": self.card_id,
            "card_version": self.card_version,
            "purpose": self.purpose,
            "entry_anchor": self.entry_anchor,
            "observation_sessions": self.observation_sessions,
            "entry_session_is_session_one": self.entry_session_is_session_one,
            "positive_threshold": str(self.positive_threshold),
            "adverse_threshold": str(self.adverse_threshold),
            "price_path_basis": self.price_path_basis,
            "corporate_action_policy": self.corporate_action_policy,
            "transaction_cost_policy": self.transaction_cost_policy,
            "evaluation_order": list(self.evaluation_order),
            "same_session_overlap_policy": self.same_session_overlap_policy,
            "structural_break_same_session_policy": self.structural_break_same_session_policy,
            "end_of_window_policy": self.end_of_window_policy,
            "incomplete_tail_policy": self.incomplete_tail_policy,
            "missing_future_evidence_policy": self.missing_future_evidence_policy,
            "suspension_policy": self.suspension_policy,
            "output_values": list(self.output_values),
            "non_claims": list(self.non_claims),
            "structural_break": self.structural_break.to_dict(),
            "bindings": self.bindings.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.semantic_payload(),
            "lifecycle": self.lifecycle,
            "owner": self.owner,
            "governance": self.governance.to_dict(),
            "semantic_digest": self.semantic_digest,
        }


@dataclass(frozen=True)
class SyntheticOutcomeObservation:
    session_date: date
    favorable_excursion: Decimal | None
    adverse_excursion: Decimal | None
    structural_break: bool | None

    def __post_init__(self) -> None:
        for field_name in ("favorable_excursion", "adverse_excursion"):
            value = getattr(self, field_name)
            if value is not None and not value.is_finite():
                raise EventLabelCardError(f"{field_name} must be finite when present")


@dataclass(frozen=True)
class SyntheticOutcomeResolution:
    status: LabelSettlementStatus
    outcome: EventOutcome | None
    reason_code: str
    terminal_date: date | None


def resolve_synthetic_outcome(
    card: OutcomeLabelCard,
    observations: Sequence[SyntheticOutcomeObservation],
    *,
    executable: bool,
    full_horizon_available: bool,
) -> SyntheticOutcomeResolution:
    """Exercise only the frozen terminal-condition ordering on synthetic observations."""

    if not executable:
        return SyntheticOutcomeResolution(
            status=LabelSettlementStatus.SETTLED,
            outcome=EventOutcome.NOT_EXECUTABLE,
            reason_code="not_executable_at_t_plus_1_open",
            terminal_date=None,
        )
    if not full_horizon_available:
        return SyntheticOutcomeResolution(
            status=LabelSettlementStatus.CENSORED,
            outcome=None,
            reason_code="incomplete_partition_tail",
            terminal_date=None,
        )
    ordered = tuple(sorted(observations, key=lambda item: item.session_date))
    if len(ordered) != card.observation_sessions:
        raise EventLabelCardError("synthetic settled horizon must contain exactly 20 sessions")
    if len({item.session_date for item in ordered}) != len(ordered):
        raise EventLabelCardError("synthetic outcome observations contain duplicate sessions")
    for item in ordered:
        if item.structural_break is None:
            return SyntheticOutcomeResolution(
                status=LabelSettlementStatus.UNAVAILABLE,
                outcome=None,
                reason_code="structural_break_evidence_unavailable",
                terminal_date=item.session_date,
            )
        if item.favorable_excursion is None or item.adverse_excursion is None:
            return SyntheticOutcomeResolution(
                status=LabelSettlementStatus.UNAVAILABLE,
                outcome=None,
                reason_code="price_path_evidence_unavailable",
                terminal_date=item.session_date,
            )
        adverse = item.adverse_excursion >= card.adverse_threshold
        positive = item.favorable_excursion >= card.positive_threshold
        if item.structural_break or adverse:
            reason = (
                "structural_break_and_adverse_threshold"
                if item.structural_break and adverse
                else "structural_break"
                if item.structural_break
                else "adverse_threshold"
            )
            return SyntheticOutcomeResolution(
                status=LabelSettlementStatus.SETTLED,
                outcome=EventOutcome.FAILURE,
                reason_code=reason,
                terminal_date=item.session_date,
            )
        if positive:
            return SyntheticOutcomeResolution(
                status=LabelSettlementStatus.SETTLED,
                outcome=EventOutcome.POSITIVE,
                reason_code="positive_threshold",
                terminal_date=item.session_date,
            )
    return SyntheticOutcomeResolution(
        status=LabelSettlementStatus.SETTLED,
        outcome=EventOutcome.NEUTRAL,
        reason_code="observation_window_ended_without_terminal_condition",
        terminal_date=ordered[-1].session_date,
    )


def _governance(raw: object, path: str) -> CardGovernance:
    value = _mapping(raw, path)
    _exact_keys(value, {"approval_state", "approval_authority", "decision_reference"}, path)
    return CardGovernance(
        approval_state=_text(value["approval_state"], f"{path}.approval_state"),
        approval_authority=_text(value["approval_authority"], f"{path}.approval_authority"),
        decision_reference=_text(value["decision_reference"], f"{path}.decision_reference"),
    )


def _load_raw(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EventLabelCardError(f"cannot read P4 label card: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EventLabelCardError(f"invalid P4 label-card JSON: {exc}") from exc
    return _mapping(raw, "card")


def _parse_structural_break_card(path: Path) -> StructuralBreakCard:
    raw = _load_raw(path)
    _exact_keys(
        raw,
        {
            "schema_version",
            "card_id",
            "card_version",
            "lifecycle",
            "owner",
            "purpose",
            "evaluation_anchor",
            "formula",
            "short_sma_sessions",
            "long_sma_sessions",
            "minimum_history_sessions",
            "listing_termination_policy",
            "suspension_policy",
            "missing_data_policy",
            "corporate_action_policy",
            "non_claims",
            "bindings",
            "governance",
            "semantic_digest",
        },
        "card",
    )
    bindings = _mapping(raw["bindings"], "bindings")
    _exact_keys(
        bindings,
        {
            "accepted_profile",
            "p0_policy_version",
            "normalized_action_authority",
            "price_feature_semantics",
        },
        "bindings",
    )
    return StructuralBreakCard(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        card_id=_text(raw["card_id"], "card_id"),
        card_version=_text(raw["card_version"], "card_version"),
        lifecycle=_text(raw["lifecycle"], "lifecycle"),
        owner=_text(raw["owner"], "owner"),
        purpose=_text(raw["purpose"], "purpose"),
        evaluation_anchor=_text(raw["evaluation_anchor"], "evaluation_anchor"),
        formula=_text(raw["formula"], "formula"),
        short_sma_sessions=_positive_int(
            raw["short_sma_sessions"], "short_sma_sessions"
        ),
        long_sma_sessions=_positive_int(
            raw["long_sma_sessions"], "long_sma_sessions"
        ),
        minimum_history_sessions=_positive_int(
            raw["minimum_history_sessions"], "minimum_history_sessions"
        ),
        listing_termination_policy=_text(
            raw["listing_termination_policy"], "listing_termination_policy"
        ),
        suspension_policy=_text(raw["suspension_policy"], "suspension_policy"),
        missing_data_policy=_text(raw["missing_data_policy"], "missing_data_policy"),
        corporate_action_policy=_text(
            raw["corporate_action_policy"], "corporate_action_policy"
        ),
        non_claims=_text_tuple(raw["non_claims"], "non_claims"),
        bindings=StructuralBreakBindings(
            accepted_profile=_text(
                bindings["accepted_profile"], "bindings.accepted_profile"
            ),
            p0_policy_version=_text(
                bindings["p0_policy_version"], "bindings.p0_policy_version"
            ),
            normalized_action_authority=_text(
                bindings["normalized_action_authority"],
                "bindings.normalized_action_authority",
            ),
            price_feature_semantics=_text(
                bindings["price_feature_semantics"],
                "bindings.price_feature_semantics",
            ),
        ),
        governance=_governance(raw["governance"], "governance"),
        semantic_digest=require_sha256_digest(
            _text(raw["semantic_digest"], "semantic_digest"), "semantic_digest"
        ),
    )


def load_structural_break_card(path: Path) -> StructuralBreakCard:
    card = _parse_structural_break_card(path)
    if card.semantic_digest != card.computed_semantic_digest:
        raise EventLabelCardError("structural-break semantic_digest does not match content")
    return card


def calculate_structural_break_card_digest(path: Path) -> str:
    return _parse_structural_break_card(path).computed_semantic_digest


def _parse_outcome_label_card(path: Path) -> OutcomeLabelCard:
    raw = _load_raw(path)
    _exact_keys(
        raw,
        {
            "schema_version",
            "card_id",
            "card_version",
            "lifecycle",
            "owner",
            "purpose",
            "entry_anchor",
            "observation_sessions",
            "entry_session_is_session_one",
            "positive_threshold",
            "adverse_threshold",
            "price_path_basis",
            "corporate_action_policy",
            "transaction_cost_policy",
            "evaluation_order",
            "same_session_overlap_policy",
            "structural_break_same_session_policy",
            "end_of_window_policy",
            "incomplete_tail_policy",
            "missing_future_evidence_policy",
            "suspension_policy",
            "output_values",
            "non_claims",
            "structural_break",
            "bindings",
            "governance",
            "semantic_digest",
        },
        "card",
    )
    structural = _mapping(raw["structural_break"], "structural_break")
    _exact_keys(
        structural,
        {"card_id", "card_version", "semantic_digest"},
        "structural_break",
    )
    bindings = _mapping(raw["bindings"], "bindings")
    _exact_keys(
        bindings,
        {
            "accepted_profile",
            "p0_policy_version",
            "candidate_rule_id",
            "candidate_rule_version",
            "candidate_rule_digest",
            "split_policy_id",
            "split_policy_version",
            "split_policy_digest",
            "maximum_overlap_sessions",
        },
        "bindings",
    )
    return OutcomeLabelCard(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        card_id=_text(raw["card_id"], "card_id"),
        card_version=_text(raw["card_version"], "card_version"),
        lifecycle=_text(raw["lifecycle"], "lifecycle"),
        owner=_text(raw["owner"], "owner"),
        purpose=_text(raw["purpose"], "purpose"),
        entry_anchor=_text(raw["entry_anchor"], "entry_anchor"),
        observation_sessions=_positive_int(
            raw["observation_sessions"], "observation_sessions"
        ),
        entry_session_is_session_one=_boolean(
            raw["entry_session_is_session_one"], "entry_session_is_session_one"
        ),
        positive_threshold=_decimal(raw["positive_threshold"], "positive_threshold"),
        adverse_threshold=_decimal(raw["adverse_threshold"], "adverse_threshold"),
        price_path_basis=_text(raw["price_path_basis"], "price_path_basis"),
        corporate_action_policy=_text(
            raw["corporate_action_policy"], "corporate_action_policy"
        ),
        transaction_cost_policy=_text(
            raw["transaction_cost_policy"], "transaction_cost_policy"
        ),
        evaluation_order=_text_tuple(raw["evaluation_order"], "evaluation_order"),
        same_session_overlap_policy=_text(
            raw["same_session_overlap_policy"], "same_session_overlap_policy"
        ),
        structural_break_same_session_policy=_text(
            raw["structural_break_same_session_policy"],
            "structural_break_same_session_policy",
        ),
        end_of_window_policy=_text(
            raw["end_of_window_policy"], "end_of_window_policy"
        ),
        incomplete_tail_policy=_text(
            raw["incomplete_tail_policy"], "incomplete_tail_policy"
        ),
        missing_future_evidence_policy=_text(
            raw["missing_future_evidence_policy"],
            "missing_future_evidence_policy",
        ),
        suspension_policy=_text(raw["suspension_policy"], "suspension_policy"),
        output_values=_text_tuple(raw["output_values"], "output_values"),
        non_claims=_text_tuple(raw["non_claims"], "non_claims"),
        structural_break=StructuralBreakReference(
            card_id=_text(structural["card_id"], "structural_break.card_id"),
            card_version=_text(
                structural["card_version"], "structural_break.card_version"
            ),
            semantic_digest=require_sha256_digest(
                _text(
                    structural["semantic_digest"],
                    "structural_break.semantic_digest",
                ),
                "structural_break.semantic_digest",
            ),
        ),
        bindings=OutcomeBindings(
            accepted_profile=_text(
                bindings["accepted_profile"], "bindings.accepted_profile"
            ),
            p0_policy_version=_text(
                bindings["p0_policy_version"], "bindings.p0_policy_version"
            ),
            candidate_rule_id=_text(
                bindings["candidate_rule_id"], "bindings.candidate_rule_id"
            ),
            candidate_rule_version=_text(
                bindings["candidate_rule_version"],
                "bindings.candidate_rule_version",
            ),
            candidate_rule_digest=require_sha256_digest(
                _text(
                    bindings["candidate_rule_digest"],
                    "bindings.candidate_rule_digest",
                ),
                "bindings.candidate_rule_digest",
            ),
            split_policy_id=_text(
                bindings["split_policy_id"], "bindings.split_policy_id"
            ),
            split_policy_version=_text(
                bindings["split_policy_version"], "bindings.split_policy_version"
            ),
            split_policy_digest=require_sha256_digest(
                _text(
                    bindings["split_policy_digest"],
                    "bindings.split_policy_digest",
                ),
                "bindings.split_policy_digest",
            ),
            maximum_overlap_sessions=_positive_int(
                bindings["maximum_overlap_sessions"],
                "bindings.maximum_overlap_sessions",
            ),
        ),
        governance=_governance(raw["governance"], "governance"),
        semantic_digest=require_sha256_digest(
            _text(raw["semantic_digest"], "semantic_digest"), "semantic_digest"
        ),
    )


def load_outcome_label_card(path: Path) -> OutcomeLabelCard:
    card = _parse_outcome_label_card(path)
    if card.semantic_digest != card.computed_semantic_digest:
        raise EventLabelCardError("outcome-card semantic_digest does not match content")
    return card


def calculate_outcome_label_card_digest(path: Path) -> str:
    return _parse_outcome_label_card(path).computed_semantic_digest
