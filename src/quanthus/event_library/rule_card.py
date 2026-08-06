"""Strict candidate rule-card contract for P4-EVENT-001."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import require_sha256_digest, sha256_digest


class CandidateRuleCardError(ValueError):
    """Raised when a candidate rule card is incomplete, mutable, or outcome-bearing."""


_SEMVER_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_ALLOWED_LIFECYCLES = {"draft", "approved", "deprecated"}
_ALLOWED_RATIONALE_CLASSES = {
    "market_mechanics",
    "data_quality",
    "unit_consistency",
    "development_only_calibration",
}
_ALLOWED_OPERATORS = {"<", "<=", "==", ">=", ">"}
_ALLOWED_INPUT_USAGES = {
    "candidate_signal_input",
    "eligibility_input",
    "static_non_pit_concentration_only",
}
_FORBIDDEN_CANDIDATE_FIELDS = {
    "entry_price",
    "exit_price",
    "future_label",
    "future_labels",
    "future_return",
    "future_returns",
    "label",
    "labels",
    "outcome",
    "outcomes",
    "post_t_feature",
    "post_t_features",
    "realized_fill",
    "realized_fills",
}


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CandidateRuleCardError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise CandidateRuleCardError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateRuleCardError(f"{path} must be a non-empty string")
    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise CandidateRuleCardError(f"{path} must be boolean")
    return value


def _positive_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CandidateRuleCardError(f"{path} must be a positive integer")
    return value


def _decimal(value: object, path: str) -> Decimal:
    if not isinstance(value, str):
        raise CandidateRuleCardError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CandidateRuleCardError(f"{path} must be a decimal string") from exc
    if not parsed.is_finite():
        raise CandidateRuleCardError(f"{path} must be finite")
    return parsed


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise CandidateRuleCardError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _text_tuple(value: object, path: str) -> tuple[str, ...]:
    items = tuple(
        _text(item, f"{path}[{index}]") for index, item in enumerate(_sequence(value, path))
    )
    if not items or len(items) != len(set(items)):
        raise CandidateRuleCardError(f"{path} must contain unique non-empty values")
    return items


def _reject_outcome_fields(values: Sequence[str], path: str) -> None:
    normalized = {value.lower() for value in values}
    forbidden_prefixes = ("future_", "post_t_", "realized_", "outcome_", "label_")
    forbidden = sorted(
        value
        for value in normalized
        if value in _FORBIDDEN_CANDIDATE_FIELDS
        or value.startswith(forbidden_prefixes)
    )
    if forbidden:
        raise CandidateRuleCardError(f"{path} contains outcome-bearing fields: {forbidden}")


@dataclass(frozen=True)
class InputBinding:
    table: str
    usage: str
    fields: tuple[str, ...]
    row_filter: str
    knowledge_cutoff: str
    unit_contract: tuple[str, ...]

    def __post_init__(self) -> None:
        _reject_outcome_fields(self.fields, f"inputs.{self.table}.fields")
        if self.usage not in _ALLOWED_INPUT_USAGES:
            raise CandidateRuleCardError(f"unsupported input usage: {self.usage}")
        if self.usage == "static_non_pit_concentration_only":
            if self.table != "static_industry_mapping":
                raise CandidateRuleCardError(
                    "static concentration input must use the governed mapping artifact"
                )
            if self.knowledge_cutoff != "not_applicable_static_non_pit_concentration_only":
                raise CandidateRuleCardError(
                    "static concentration mapping must declare its non-PIT exception"
                )
        elif self.knowledge_cutoff != "knowledge_from <= T close":
            raise CandidateRuleCardError(
                "candidate and eligibility inputs must be cutoff at T close"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "table": self.table,
            "usage": self.usage,
            "fields": list(self.fields),
            "row_filter": self.row_filter,
            "knowledge_cutoff": self.knowledge_cutoff,
            "unit_contract": list(self.unit_contract),
        }


@dataclass(frozen=True)
class TransformSpec:
    transform_id: str
    formula: str
    required_fields: tuple[str, ...]
    lookback_sessions: int
    includes_current_session: bool
    output_unit: str
    missing_data_policy: str

    def __post_init__(self) -> None:
        _reject_outcome_fields(
            self.required_fields,
            f"transforms.{self.transform_id}.required_fields",
        )
        if self.missing_data_policy != "reject_candidate_and_emit_reason_code":
            raise CandidateRuleCardError("candidate transforms must fail closed on missing data")

    def to_dict(self) -> dict[str, object]:
        return {
            "transform_id": self.transform_id,
            "formula": self.formula,
            "required_fields": list(self.required_fields),
            "lookback_sessions": self.lookback_sessions,
            "includes_current_session": self.includes_current_session,
            "output_unit": self.output_unit,
            "missing_data_policy": self.missing_data_policy,
        }


@dataclass(frozen=True)
class StateTransitionSpec:
    quiet_state_definition: str
    active_state_definition: str
    transition_edge_definition: str

    def to_dict(self) -> dict[str, str]:
        return {
            "quiet_state_definition": self.quiet_state_definition,
            "active_state_definition": self.active_state_definition,
            "transition_edge_definition": self.transition_edge_definition,
        }


@dataclass(frozen=True)
class ThresholdSpec:
    threshold_id: str
    operator: str
    value: Decimal
    unit: str
    rationale_class: str
    rationale: str
    calibration_procedure: str | None

    def __post_init__(self) -> None:
        if self.operator not in _ALLOWED_OPERATORS:
            raise CandidateRuleCardError(f"unsupported threshold operator: {self.operator}")
        if self.rationale_class not in _ALLOWED_RATIONALE_CLASSES:
            raise CandidateRuleCardError(
                f"unsupported threshold rationale class: {self.rationale_class}"
            )
        if self.rationale_class == "development_only_calibration":
            if self.calibration_procedure is None:
                raise CandidateRuleCardError(
                    "development-only calibration thresholds require an explicit procedure"
                )
        elif self.calibration_procedure is not None:
            raise CandidateRuleCardError(
                "calibration_procedure is allowed only for development-only calibration"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold_id": self.threshold_id,
            "operator": self.operator,
            "value": str(self.value),
            "unit": self.unit,
            "rationale_class": self.rationale_class,
            "rationale": self.rationale,
            "calibration_procedure": self.calibration_procedure,
        }


@dataclass(frozen=True)
class EligibilityPolicy:
    allowed_venues: tuple[str, ...]
    instrument_types: tuple[str, ...]
    minimum_listing_sessions: int
    minimum_history_sessions: int
    required_t_close_conditions: tuple[str, ...]
    rationale: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed_venues": list(self.allowed_venues),
            "instrument_types": list(self.instrument_types),
            "minimum_listing_sessions": self.minimum_listing_sessions,
            "minimum_history_sessions": self.minimum_history_sessions,
            "required_t_close_conditions": list(self.required_t_close_conditions),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class IsolationPolicy:
    cooldown_sessions: int
    same_security_minimum_separation_sessions: int
    deduplication_key: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        expected = (
            "security_id",
            "signal_date",
            "candidate_rule_id",
            "candidate_rule_version",
            "snapshot_id",
        )
        if self.deduplication_key != expected:
            raise CandidateRuleCardError("deduplication_key must match the event identity contract")

    def to_dict(self) -> dict[str, object]:
        return {
            "cooldown_sessions": self.cooldown_sessions,
            "same_security_minimum_separation_sessions": (
                self.same_security_minimum_separation_sessions
            ),
            "deduplication_key": list(self.deduplication_key),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ConcentrationPolicy:
    grouping_field: str
    wave_sessions: int
    maximum_events_per_group_per_wave: int
    maximum_events_per_signal_date: int
    deterministic_ranking_key: str
    rationale: str

    def to_dict(self) -> dict[str, object]:
        return {
            "grouping_field": self.grouping_field,
            "wave_sessions": self.wave_sessions,
            "maximum_events_per_group_per_wave": self.maximum_events_per_group_per_wave,
            "maximum_events_per_signal_date": self.maximum_events_per_signal_date,
            "deterministic_ranking_key": self.deterministic_ranking_key,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class RuleBindings:
    p0_policy_version: str
    p3_gate_version: str
    dwd_model_version: str
    industry_mapping_version: str
    industry_mapping_usage: str
    accepted_profile: str
    snapshot_binding: str

    def __post_init__(self) -> None:
        if self.accepted_profile != "close-to-next-open-v1":
            raise CandidateRuleCardError("P4 candidate card must use close-to-next-open-v1")
        if self.snapshot_binding != "exact_manifest_bound_dwd_snapshot":
            raise CandidateRuleCardError("candidate card must require an exact DWD snapshot")
        if self.industry_mapping_usage != "static_non_pit_concentration_only":
            raise CandidateRuleCardError("static industry mapping may only control concentration")

    def to_dict(self) -> dict[str, str]:
        return {
            "p0_policy_version": self.p0_policy_version,
            "p3_gate_version": self.p3_gate_version,
            "dwd_model_version": self.dwd_model_version,
            "industry_mapping_version": self.industry_mapping_version,
            "industry_mapping_usage": self.industry_mapping_usage,
            "accepted_profile": self.accepted_profile,
            "snapshot_binding": self.snapshot_binding,
        }


@dataclass(frozen=True)
class RuleGovernance:
    approval_state: str
    approval_authority: str
    decision_reference: str

    def __post_init__(self) -> None:
        if self.approval_state not in {"pending_human_approval", "approved", "deprecated"}:
            raise CandidateRuleCardError("unsupported rule-card approval state")
        if self.approval_authority != "human":
            raise CandidateRuleCardError("candidate research truth requires human approval")

    def to_dict(self) -> dict[str, str]:
        return {
            "approval_state": self.approval_state,
            "approval_authority": self.approval_authority,
            "decision_reference": self.decision_reference,
        }


@dataclass(frozen=True)
class CandidateRuleCard:
    path: Path
    schema_version: str
    rule_id: str
    rule_version: str
    lifecycle: str
    owner: str
    hypothesis: str
    intended_research_use: str
    falsification_conditions: tuple[str, ...]
    non_claims: tuple[str, ...]
    inputs: tuple[InputBinding, ...]
    transforms: tuple[TransformSpec, ...]
    states: StateTransitionSpec
    thresholds: tuple[ThresholdSpec, ...]
    eligibility: EligibilityPolicy
    exclusions: tuple[str, ...]
    missing_data_policy: str
    suspension_policy: str
    listing_policy: str
    corporate_action_policy: str
    isolation: IsolationPolicy
    concentration: ConcentrationPolicy
    bindings: RuleBindings
    governance: RuleGovernance
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise CandidateRuleCardError("candidate rule card schema_version must be 1.0.0")
        if _SEMVER_PATTERN.fullmatch(self.rule_version) is None:
            raise CandidateRuleCardError("rule_version must be semantic versioning")
        if self.lifecycle not in _ALLOWED_LIFECYCLES:
            raise CandidateRuleCardError("unsupported candidate rule-card lifecycle")
        if self.lifecycle == "approved" and self.governance.approval_state != "approved":
            raise CandidateRuleCardError("approved lifecycle requires approved governance")
        if self.lifecycle == "draft" and self.governance.approval_state != "pending_human_approval":
            raise CandidateRuleCardError("draft lifecycle requires pending human approval")
        if self.lifecycle == "deprecated" and self.governance.approval_state != "deprecated":
            raise CandidateRuleCardError("deprecated lifecycle requires deprecated governance")
        require_sha256_digest(self.semantic_digest, "semantic_digest")
        input_tables = [item.table for item in self.inputs]
        transform_ids = [item.transform_id for item in self.transforms]
        threshold_ids = [item.threshold_id for item in self.thresholds]
        if len(input_tables) != len(set(input_tables)):
            raise CandidateRuleCardError("input tables must be unique")
        if len(transform_ids) != len(set(transform_ids)):
            raise CandidateRuleCardError("transform ids must be unique")
        if len(threshold_ids) != len(set(threshold_ids)):
            raise CandidateRuleCardError("threshold ids must be unique")
        if self.missing_data_policy != "reject_candidate_and_emit_reason_code":
            raise CandidateRuleCardError("candidate rule card must fail closed on missing data")

    @property
    def computed_semantic_digest(self) -> str:
        return sha256_digest(self.semantic_payload())

    def require_stage_b_authority(self) -> None:
        if self.lifecycle != "approved" or self.governance.approval_state != "approved":
            raise CandidateRuleCardError(
                "Stage B is blocked until the exact semantic digest receives human approval"
            )

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "hypothesis": self.hypothesis,
            "intended_research_use": self.intended_research_use,
            "falsification_conditions": list(self.falsification_conditions),
            "non_claims": list(self.non_claims),
            "inputs": [item.to_dict() for item in self.inputs],
            "transforms": [item.to_dict() for item in self.transforms],
            "states": self.states.to_dict(),
            "thresholds": [item.to_dict() for item in self.thresholds],
            "eligibility": self.eligibility.to_dict(),
            "exclusions": list(self.exclusions),
            "missing_data_policy": self.missing_data_policy,
            "suspension_policy": self.suspension_policy,
            "listing_policy": self.listing_policy,
            "corporate_action_policy": self.corporate_action_policy,
            "isolation": self.isolation.to_dict(),
            "concentration": self.concentration.to_dict(),
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


def _parse_candidate_rule_card(path: Path) -> CandidateRuleCard:
    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CandidateRuleCardError(f"cannot read candidate rule card: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CandidateRuleCardError(f"invalid candidate rule-card JSON: {exc}") from exc
    raw = _mapping(raw_value, "candidate_rule_card")
    _exact_keys(
        raw,
        {
            "schema_version",
            "rule_id",
            "rule_version",
            "lifecycle",
            "owner",
            "hypothesis",
            "intended_research_use",
            "falsification_conditions",
            "non_claims",
            "inputs",
            "transforms",
            "states",
            "thresholds",
            "eligibility",
            "exclusions",
            "missing_data_policy",
            "suspension_policy",
            "listing_policy",
            "corporate_action_policy",
            "isolation",
            "concentration",
            "bindings",
            "governance",
            "semantic_digest",
        },
        "candidate_rule_card",
    )

    inputs: list[InputBinding] = []
    for index, raw_input_value in enumerate(_sequence(raw["inputs"], "inputs")):
        raw_input = _mapping(raw_input_value, f"inputs[{index}]")
        _exact_keys(
            raw_input,
            {"table", "usage", "fields", "row_filter", "knowledge_cutoff", "unit_contract"},
            f"inputs[{index}]",
        )
        inputs.append(
            InputBinding(
                table=_text(raw_input["table"], f"inputs[{index}].table"),
                usage=_text(raw_input["usage"], f"inputs[{index}].usage"),
                fields=_text_tuple(raw_input["fields"], f"inputs[{index}].fields"),
                row_filter=_text(raw_input["row_filter"], f"inputs[{index}].row_filter"),
                knowledge_cutoff=_text(
                    raw_input["knowledge_cutoff"], f"inputs[{index}].knowledge_cutoff"
                ),
                unit_contract=_text_tuple(
                    raw_input["unit_contract"], f"inputs[{index}].unit_contract"
                ),
            )
        )

    transforms: list[TransformSpec] = []
    for index, raw_transform_value in enumerate(_sequence(raw["transforms"], "transforms")):
        raw_transform = _mapping(raw_transform_value, f"transforms[{index}]")
        _exact_keys(
            raw_transform,
            {
                "transform_id",
                "formula",
                "required_fields",
                "lookback_sessions",
                "includes_current_session",
                "output_unit",
                "missing_data_policy",
            },
            f"transforms[{index}]",
        )
        transforms.append(
            TransformSpec(
                transform_id=_text(
                    raw_transform["transform_id"], f"transforms[{index}].transform_id"
                ),
                formula=_text(raw_transform["formula"], f"transforms[{index}].formula"),
                required_fields=_text_tuple(
                    raw_transform["required_fields"],
                    f"transforms[{index}].required_fields",
                ),
                lookback_sessions=_positive_int(
                    raw_transform["lookback_sessions"],
                    f"transforms[{index}].lookback_sessions",
                ),
                includes_current_session=_boolean(
                    raw_transform["includes_current_session"],
                    f"transforms[{index}].includes_current_session",
                ),
                output_unit=_text(
                    raw_transform["output_unit"], f"transforms[{index}].output_unit"
                ),
                missing_data_policy=_text(
                    raw_transform["missing_data_policy"],
                    f"transforms[{index}].missing_data_policy",
                ),
            )
        )

    states = _mapping(raw["states"], "states")
    _exact_keys(
        states,
        {
            "quiet_state_definition",
            "active_state_definition",
            "transition_edge_definition",
        },
        "states",
    )

    thresholds: list[ThresholdSpec] = []
    for index, raw_threshold_value in enumerate(_sequence(raw["thresholds"], "thresholds")):
        raw_threshold = _mapping(raw_threshold_value, f"thresholds[{index}]")
        _exact_keys(
            raw_threshold,
            {
                "threshold_id",
                "operator",
                "value",
                "unit",
                "rationale_class",
                "rationale",
                "calibration_procedure",
            },
            f"thresholds[{index}]",
        )
        thresholds.append(
            ThresholdSpec(
                threshold_id=_text(
                    raw_threshold["threshold_id"], f"thresholds[{index}].threshold_id"
                ),
                operator=_text(raw_threshold["operator"], f"thresholds[{index}].operator"),
                value=_decimal(raw_threshold["value"], f"thresholds[{index}].value"),
                unit=_text(raw_threshold["unit"], f"thresholds[{index}].unit"),
                rationale_class=_text(
                    raw_threshold["rationale_class"],
                    f"thresholds[{index}].rationale_class",
                ),
                rationale=_text(
                    raw_threshold["rationale"], f"thresholds[{index}].rationale"
                ),
                calibration_procedure=_optional_text(
                    raw_threshold["calibration_procedure"],
                    f"thresholds[{index}].calibration_procedure",
                ),
            )
        )

    eligibility = _mapping(raw["eligibility"], "eligibility")
    _exact_keys(
        eligibility,
        {
            "allowed_venues",
            "instrument_types",
            "minimum_listing_sessions",
            "minimum_history_sessions",
            "required_t_close_conditions",
            "rationale",
        },
        "eligibility",
    )
    isolation = _mapping(raw["isolation"], "isolation")
    _exact_keys(
        isolation,
        {
            "cooldown_sessions",
            "same_security_minimum_separation_sessions",
            "deduplication_key",
            "rationale",
        },
        "isolation",
    )
    concentration = _mapping(raw["concentration"], "concentration")
    _exact_keys(
        concentration,
        {
            "grouping_field",
            "wave_sessions",
            "maximum_events_per_group_per_wave",
            "maximum_events_per_signal_date",
            "deterministic_ranking_key",
            "rationale",
        },
        "concentration",
    )
    bindings = _mapping(raw["bindings"], "bindings")
    _exact_keys(
        bindings,
        {
            "p0_policy_version",
            "p3_gate_version",
            "dwd_model_version",
            "industry_mapping_version",
            "industry_mapping_usage",
            "accepted_profile",
            "snapshot_binding",
        },
        "bindings",
    )
    governance = _mapping(raw["governance"], "governance")
    _exact_keys(
        governance,
        {"approval_state", "approval_authority", "decision_reference"},
        "governance",
    )

    return CandidateRuleCard(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        rule_id=_text(raw["rule_id"], "rule_id"),
        rule_version=_text(raw["rule_version"], "rule_version"),
        lifecycle=_text(raw["lifecycle"], "lifecycle"),
        owner=_text(raw["owner"], "owner"),
        hypothesis=_text(raw["hypothesis"], "hypothesis"),
        intended_research_use=_text(
            raw["intended_research_use"], "intended_research_use"
        ),
        falsification_conditions=_text_tuple(
            raw["falsification_conditions"], "falsification_conditions"
        ),
        non_claims=_text_tuple(raw["non_claims"], "non_claims"),
        inputs=tuple(inputs),
        transforms=tuple(transforms),
        states=StateTransitionSpec(
            quiet_state_definition=_text(
                states["quiet_state_definition"], "states.quiet_state_definition"
            ),
            active_state_definition=_text(
                states["active_state_definition"], "states.active_state_definition"
            ),
            transition_edge_definition=_text(
                states["transition_edge_definition"], "states.transition_edge_definition"
            ),
        ),
        thresholds=tuple(thresholds),
        eligibility=EligibilityPolicy(
            allowed_venues=_text_tuple(
                eligibility["allowed_venues"], "eligibility.allowed_venues"
            ),
            instrument_types=_text_tuple(
                eligibility["instrument_types"], "eligibility.instrument_types"
            ),
            minimum_listing_sessions=_positive_int(
                eligibility["minimum_listing_sessions"],
                "eligibility.minimum_listing_sessions",
            ),
            minimum_history_sessions=_positive_int(
                eligibility["minimum_history_sessions"],
                "eligibility.minimum_history_sessions",
            ),
            required_t_close_conditions=_text_tuple(
                eligibility["required_t_close_conditions"],
                "eligibility.required_t_close_conditions",
            ),
            rationale=_text(eligibility["rationale"], "eligibility.rationale"),
        ),
        exclusions=_text_tuple(raw["exclusions"], "exclusions"),
        missing_data_policy=_text(raw["missing_data_policy"], "missing_data_policy"),
        suspension_policy=_text(raw["suspension_policy"], "suspension_policy"),
        listing_policy=_text(raw["listing_policy"], "listing_policy"),
        corporate_action_policy=_text(
            raw["corporate_action_policy"], "corporate_action_policy"
        ),
        isolation=IsolationPolicy(
            cooldown_sessions=_positive_int(
                isolation["cooldown_sessions"], "isolation.cooldown_sessions"
            ),
            same_security_minimum_separation_sessions=_positive_int(
                isolation["same_security_minimum_separation_sessions"],
                "isolation.same_security_minimum_separation_sessions",
            ),
            deduplication_key=_text_tuple(
                isolation["deduplication_key"], "isolation.deduplication_key"
            ),
            rationale=_text(isolation["rationale"], "isolation.rationale"),
        ),
        concentration=ConcentrationPolicy(
            grouping_field=_text(
                concentration["grouping_field"], "concentration.grouping_field"
            ),
            wave_sessions=_positive_int(
                concentration["wave_sessions"], "concentration.wave_sessions"
            ),
            maximum_events_per_group_per_wave=_positive_int(
                concentration["maximum_events_per_group_per_wave"],
                "concentration.maximum_events_per_group_per_wave",
            ),
            maximum_events_per_signal_date=_positive_int(
                concentration["maximum_events_per_signal_date"],
                "concentration.maximum_events_per_signal_date",
            ),
            deterministic_ranking_key=_text(
                concentration["deterministic_ranking_key"],
                "concentration.deterministic_ranking_key",
            ),
            rationale=_text(concentration["rationale"], "concentration.rationale"),
        ),
        bindings=RuleBindings(
            p0_policy_version=_text(
                bindings["p0_policy_version"], "bindings.p0_policy_version"
            ),
            p3_gate_version=_text(bindings["p3_gate_version"], "bindings.p3_gate_version"),
            dwd_model_version=_text(
                bindings["dwd_model_version"], "bindings.dwd_model_version"
            ),
            industry_mapping_version=_text(
                bindings["industry_mapping_version"],
                "bindings.industry_mapping_version",
            ),
            industry_mapping_usage=_text(
                bindings["industry_mapping_usage"],
                "bindings.industry_mapping_usage",
            ),
            accepted_profile=_text(
                bindings["accepted_profile"], "bindings.accepted_profile"
            ),
            snapshot_binding=_text(
                bindings["snapshot_binding"], "bindings.snapshot_binding"
            ),
        ),
        governance=RuleGovernance(
            approval_state=_text(
                governance["approval_state"], "governance.approval_state"
            ),
            approval_authority=_text(
                governance["approval_authority"], "governance.approval_authority"
            ),
            decision_reference=_text(
                governance["decision_reference"], "governance.decision_reference"
            ),
        ),
        semantic_digest=require_sha256_digest(
            _text(raw["semantic_digest"], "semantic_digest"), "semantic_digest"
        ),
    )


def calculate_candidate_rule_card_digest(path: Path) -> str:
    """Calculate the semantic digest without granting Stage B authority."""

    return _parse_candidate_rule_card(path).computed_semantic_digest


def load_candidate_rule_card(path: Path) -> CandidateRuleCard:
    """Load a candidate rule card with exact-key and semantic-digest validation."""

    card = _parse_candidate_rule_card(path)
    if card.semantic_digest != card.computed_semantic_digest:
        raise CandidateRuleCardError("candidate rule-card semantic_digest does not match content")
    return card
