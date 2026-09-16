"""Layered stock-stage proposition representation governed by P3-STAGE-028.

This module serializes already-derived propositions. It does not read market data or infer stage
truth, qualify labels, choose a primary stage, or grant production/strategy/trading authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONTRACT = "config/stage-learning/stage-proposition-bundle.v1.json"
CONTRACT_SCHEMA = "quanthus.stage-proposition-bundle-contract.v1"
BUNDLE_SCHEMA = "quanthus.stage-proposition-bundle.v1"
CONSUMER_VIEW_SCHEMA = "quanthus.stage-proposition-consumer-view.v1"


class StagePropositionBundleError(ValueError):
    """Raised when the frozen P028 representation contract is violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise StagePropositionBundleError(f"cannot_hash:{path}") from exc


def _nonempty_strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise StagePropositionBundleError(f"{label}_required")
    output = tuple(str(item).strip() for item in value)
    if any(not item for item in output) or len(set(output)) != len(output):
        raise StagePropositionBundleError(f"{label}_invalid")
    return output


def _knowledge_cutoff(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise StagePropositionBundleError("knowledge_cutoff_required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StagePropositionBundleError("knowledge_cutoff_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StagePropositionBundleError("knowledge_cutoff_timezone_required")
    return parsed.isoformat()


@dataclass(frozen=True)
class PropositionDefinition:
    proposition_id: str
    historical_stage_code: int
    layer: str
    proposition_version: str
    evidence_grade: str
    use_authority: str
    parent_proposition_id: str | None
    required_provenance_prefix: str | None


@dataclass(frozen=True)
class StagePropositionContract:
    version: str
    contract_digest: str
    output_schema_version: str
    control_states: tuple[str, ...]
    epistemic_statuses: tuple[str, ...]
    verdicts: tuple[str, ...]
    layers: tuple[str, ...]
    propositions: Mapping[str, PropositionDefinition]
    evidence_grades: Mapping[str, Mapping[str, Any]]
    surfaces: Mapping[str, Mapping[str, Any]]

    @property
    def by_code(self) -> dict[int, PropositionDefinition]:
        return {item.historical_stage_code: item for item in self.propositions.values()}


@dataclass(frozen=True)
class StagePropositionAssertion:
    proposition_id: str
    historical_stage_code: int
    layer: str
    verdict: str
    proposition_version: str
    evidence_grade: str
    use_authority: str
    reason_codes: tuple[str, ...]
    fact_refs: tuple[str, ...]
    provenance_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        value["fact_refs"] = list(self.fact_refs)
        value["provenance_refs"] = list(self.provenance_refs)
        return value


@dataclass(frozen=True)
class StagePropositionBundle:
    contract_version: str
    security_id: str
    knowledge_cutoff: str
    control_state: str
    epistemic_status: str
    status_reasons: tuple[str, ...]
    context_assertions: tuple[StagePropositionAssertion, ...]
    condition_assertions: tuple[StagePropositionAssertion, ...]
    modifier_assertions: tuple[StagePropositionAssertion, ...]
    fact_refs: tuple[str, ...]
    primary_stage: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BUNDLE_SCHEMA,
            "contract_version": self.contract_version,
            "security_id": self.security_id,
            "knowledge_cutoff": self.knowledge_cutoff,
            "control_state": self.control_state,
            "epistemic_status": self.epistemic_status,
            "status_reasons": list(self.status_reasons),
            "context_assertions": [item.to_dict() for item in self.context_assertions],
            "condition_assertions": [item.to_dict() for item in self.condition_assertions],
            "modifier_assertions": [item.to_dict() for item in self.modifier_assertions],
            "fact_refs": list(self.fact_refs),
            "primary_stage": None,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical(self.to_dict())

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class StagePropositionConsumerView:
    surface: str
    security_id: str
    knowledge_cutoff: str
    control_state: str
    source_epistemic_status: str
    context_assertions: tuple[StagePropositionAssertion, ...]
    condition_assertions: tuple[StagePropositionAssertion, ...]
    modifier_assertions: tuple[StagePropositionAssertion, ...]
    fact_refs: tuple[str, ...]
    hidden_assertion_count: int
    primary_stage: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONSUMER_VIEW_SCHEMA,
            "surface": self.surface,
            "security_id": self.security_id,
            "knowledge_cutoff": self.knowledge_cutoff,
            "control_state": self.control_state,
            "source_epistemic_status": self.source_epistemic_status,
            "context_assertions": [item.to_dict() for item in self.context_assertions],
            "condition_assertions": [item.to_dict() for item in self.condition_assertions],
            "modifier_assertions": [item.to_dict() for item in self.modifier_assertions],
            "fact_refs": list(self.fact_refs),
            "hidden_assertion_count": self.hidden_assertion_count,
            "primary_stage": None,
            "production_expert_authority": False,
            "strategy_or_trading_authority": False,
        }


def load_stage_proposition_contract(path: Path | None = None) -> StagePropositionContract:
    root = find_project_root()
    contract_path = path or root / DEFAULT_CONTRACT
    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagePropositionBundleError("contract_unreadable") from exc
    if not isinstance(raw, dict):
        raise StagePropositionBundleError("contract_object_required")
    if (
        raw.get("schema_version") != CONTRACT_SCHEMA
        or raw.get("work_item") != "P3-STAGE-028"
        or raw.get("version") != "1.0.0"
        or raw.get("state") != "FROZEN_LAYERED_BUNDLE_SCHEMA_GOVERNANCE_NO_NEW_RESULT_READ"
    ):
        raise StagePropositionBundleError("contract_identity")

    binding = raw.get("bindings", {}).get("p027_portfolio_synthesis")
    if not isinstance(binding, Mapping):
        raise StagePropositionBundleError("p027_binding_missing")
    bound_path = root / str(binding.get("path") or "")
    if _file_sha(bound_path) != binding.get("sha256"):
        raise StagePropositionBundleError("p027_binding_digest")

    schema = raw.get("bundle_schema")
    if not isinstance(schema, Mapping):
        raise StagePropositionBundleError("bundle_schema_missing")
    if (
        schema.get("output_schema_version") != BUNDLE_SCHEMA
        or schema.get("primary_stage_policy") != "REQUIRED_NULL_V1"
    ):
        raise StagePropositionBundleError("bundle_schema_identity")
    control_states = tuple(schema.get("control_states") or ())
    epistemic_statuses = tuple(schema.get("epistemic_statuses") or ())
    verdicts = tuple(schema.get("verdicts") or ())
    layers = tuple(schema.get("layers") or ())
    if control_states != ("ACTIVE", "X"):
        raise StagePropositionBundleError("control_state_contract")
    if set(epistemic_statuses) != {
        "ASSERTED",
        "AMBIGUOUS",
        "ABSTAIN",
        "RULE_CONFLICT",
        "NOT_EVALUATED",
    }:
        raise StagePropositionBundleError("epistemic_status_contract")
    if set(verdicts) != {"TRUE", "FALSE", "AMBIGUOUS", "UNKNOWN", "NOT_APPLICABLE"}:
        raise StagePropositionBundleError("verdict_contract")
    if layers != ("context_or_structural_state", "event_or_nested_condition", "modifier"):
        raise StagePropositionBundleError("layer_contract")

    grades = raw.get("evidence_grades")
    surfaces = raw.get("surface_policy")
    if not isinstance(grades, Mapping) or not isinstance(surfaces, Mapping):
        raise StagePropositionBundleError("authority_tables_missing")
    if set(surfaces) != {"audit", "development_shadow"}:
        raise StagePropositionBundleError("surface_contract")
    if surfaces["development_shadow"].get("allowed_evidence_grades") != [
        "accepted_development_projection"
    ]:
        raise StagePropositionBundleError("development_shadow_grade_contract")
    if surfaces["development_shadow"].get("allowed_historical_stage_codes") != [10, 11]:
        raise StagePropositionBundleError("development_shadow_code_contract")

    catalog = raw.get("proposition_catalog")
    if not isinstance(catalog, list) or len(catalog) != 13:
        raise StagePropositionBundleError("proposition_catalog_count")
    propositions: dict[str, PropositionDefinition] = {}
    codes: set[int] = set()
    expected_fields = {
        "proposition_id",
        "historical_stage_code",
        "layer",
        "proposition_version",
        "evidence_grade",
        "use_authority",
        "parent_proposition_id",
        "required_provenance_prefix",
    }
    for entry in catalog:
        if not isinstance(entry, Mapping) or set(entry) != expected_fields:
            raise StagePropositionBundleError("proposition_catalog_shape")
        proposition_id = str(entry["proposition_id"])
        code = entry["historical_stage_code"]
        layer = str(entry["layer"])
        grade = str(entry["evidence_grade"])
        use_authority = str(entry["use_authority"])
        if (
            not proposition_id
            or proposition_id in propositions
            or type(code) is not int
            or code in codes
            or layer not in layers
            or grade not in grades
            or use_authority not in surfaces
            or grades[grade].get("max_surface") != use_authority
            or grades[grade].get("official_label") is not False
        ):
            raise StagePropositionBundleError("proposition_catalog_authority")
        parent = entry["parent_proposition_id"]
        prefix = entry["required_provenance_prefix"]
        propositions[proposition_id] = PropositionDefinition(
            proposition_id=proposition_id,
            historical_stage_code=code,
            layer=layer,
            proposition_version=str(entry["proposition_version"]),
            evidence_grade=grade,
            use_authority=use_authority,
            parent_proposition_id=None if parent is None else str(parent),
            required_provenance_prefix=None if prefix is None else str(prefix),
        )
        codes.add(code)
    if codes != set(range(13)):
        raise StagePropositionBundleError("historical_code_coverage")
    if any(
        definition.parent_proposition_id not in propositions
        for definition in propositions.values()
        if definition.parent_proposition_id is not None
    ):
        raise StagePropositionBundleError("parent_proposition_unresolved")

    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StagePropositionBundleError("authority_missing")
    for key in (
        "new_market_read",
        "new_reference_opinion",
        "holdout_read",
        "candidate_or_threshold_search",
        "primary_stage_compression",
        "labelcard_publication",
        "production_expert_consumption",
        "daily_inference",
        "strategy_consumption",
        "trading",
    ):
        if authority.get(key) is not False:
            raise StagePropositionBundleError(f"authority_open:{key}")

    return StagePropositionContract(
        version=str(raw["version"]),
        contract_digest=digest(raw),
        output_schema_version=str(schema["output_schema_version"]),
        control_states=control_states,
        epistemic_statuses=epistemic_statuses,
        verdicts=verdicts,
        layers=layers,
        propositions=propositions,
        evidence_grades=dict(grades),
        surfaces=dict(surfaces),
    )


def make_assertion(
    contract: StagePropositionContract,
    *,
    proposition_id: str,
    verdict: str,
    reason_codes: Sequence[str],
    fact_refs: Sequence[str],
    provenance_refs: Sequence[str],
) -> StagePropositionAssertion:
    definition = contract.propositions.get(proposition_id)
    if definition is None:
        raise StagePropositionBundleError("unknown_proposition")
    if verdict not in contract.verdicts:
        raise StagePropositionBundleError("invalid_verdict")
    reasons = tuple(sorted(_nonempty_strings(reason_codes, "reason_codes")))
    facts = tuple(sorted(_nonempty_strings(fact_refs, "fact_refs")))
    provenance = tuple(sorted(_nonempty_strings(provenance_refs, "provenance_refs")))
    if (
        verdict == "TRUE"
        and definition.required_provenance_prefix is not None
        and not any(ref.startswith(definition.required_provenance_prefix) for ref in provenance)
    ):
        raise StagePropositionBundleError("required_provenance_missing")
    return StagePropositionAssertion(
        proposition_id=definition.proposition_id,
        historical_stage_code=definition.historical_stage_code,
        layer=definition.layer,
        verdict=verdict,
        proposition_version=definition.proposition_version,
        evidence_grade=definition.evidence_grade,
        use_authority=definition.use_authority,
        reason_codes=reasons,
        fact_refs=facts,
        provenance_refs=provenance,
    )


def _assertion_from_raw(
    contract: StagePropositionContract, raw: object
) -> StagePropositionAssertion:
    if not isinstance(raw, Mapping):
        raise StagePropositionBundleError("assertion_object_required")
    expected = {
        "proposition_id",
        "historical_stage_code",
        "layer",
        "verdict",
        "proposition_version",
        "evidence_grade",
        "use_authority",
        "reason_codes",
        "fact_refs",
        "provenance_refs",
    }
    if set(raw) != expected:
        raise StagePropositionBundleError("assertion_fields")
    assertion = make_assertion(
        contract,
        proposition_id=str(raw["proposition_id"]),
        verdict=str(raw["verdict"]),
        reason_codes=raw["reason_codes"],
        fact_refs=raw["fact_refs"],
        provenance_refs=raw["provenance_refs"],
    )
    if assertion.to_dict() != dict(raw):
        raise StagePropositionBundleError("assertion_catalog_metadata_mutated")
    return assertion


def _layered(assertions: Sequence[StagePropositionAssertion]) -> tuple[
    tuple[StagePropositionAssertion, ...],
    tuple[StagePropositionAssertion, ...],
    tuple[StagePropositionAssertion, ...],
]:
    ordered = tuple(sorted(assertions, key=lambda item: item.proposition_id))
    return (
        tuple(item for item in ordered if item.layer == "context_or_structural_state"),
        tuple(item for item in ordered if item.layer == "event_or_nested_condition"),
        tuple(item for item in ordered if item.layer == "modifier"),
    )


def _validate_relationships(
    contract: StagePropositionContract,
    *,
    control_state: str,
    epistemic_status: str,
    status_reasons: tuple[str, ...],
    assertions: tuple[StagePropositionAssertion, ...],
) -> None:
    if len({item.proposition_id for item in assertions}) != len(assertions):
        raise StagePropositionBundleError("duplicate_proposition")
    true_items = tuple(item for item in assertions if item.verdict == "TRUE")

    if control_state == "X":
        if epistemic_status != "NOT_EVALUATED" or assertions:
            raise StagePropositionBundleError("control_x_freeze_contract")
        if not status_reasons:
            raise StagePropositionBundleError("control_x_reason_required")
        return
    if control_state != "ACTIVE":
        raise StagePropositionBundleError("control_state_invalid")
    if epistemic_status == "NOT_EVALUATED" and not status_reasons:
        raise StagePropositionBundleError("active_not_evaluated_reason_required")
    if epistemic_status in {"ABSTAIN", "RULE_CONFLICT"} and true_items:
        raise StagePropositionBundleError("epistemic_status_forbids_true_assertion")
    if epistemic_status in {"ABSTAIN", "RULE_CONFLICT", "AMBIGUOUS"} and not status_reasons:
        raise StagePropositionBundleError("epistemic_status_reason_required")
    if epistemic_status == "ASSERTED" and not true_items:
        raise StagePropositionBundleError("asserted_requires_true_assertion")

    by_id = {item.proposition_id: item for item in assertions}
    for item in true_items:
        definition = contract.propositions[item.proposition_id]
        parent_id = definition.parent_proposition_id
        if parent_id is not None:
            parent = by_id.get(parent_id)
            if parent is None or parent.verdict != "TRUE":
                raise StagePropositionBundleError("true_parent_required")

    true_contexts = [
        item for item in true_items if item.layer == "context_or_structural_state"
    ]
    if any(item.historical_stage_code == 0 for item in true_contexts) and len(true_contexts) > 1:
        raise StagePropositionBundleError("neutral_context_conflict")
    for layer in contract.layers:
        same_layer_true = [item for item in true_items if item.layer == layer]
        if len(same_layer_true) > 1 and epistemic_status != "AMBIGUOUS":
            raise StagePropositionBundleError("same_layer_true_requires_ambiguity")


def build_bundle(
    contract: StagePropositionContract,
    *,
    security_id: str,
    knowledge_cutoff: str,
    control_state: str,
    epistemic_status: str,
    status_reasons: Sequence[str] = (),
    assertions: Sequence[StagePropositionAssertion] = (),
) -> StagePropositionBundle:
    security = str(security_id).strip()
    if not security:
        raise StagePropositionBundleError("security_id_required")
    cutoff = _knowledge_cutoff(knowledge_cutoff)
    if control_state not in contract.control_states:
        raise StagePropositionBundleError("control_state_invalid")
    if epistemic_status not in contract.epistemic_statuses:
        raise StagePropositionBundleError("epistemic_status_invalid")
    reasons = tuple(sorted(str(item).strip() for item in status_reasons if str(item).strip()))
    if len(set(reasons)) != len(reasons):
        raise StagePropositionBundleError("status_reasons_duplicate")
    assertion_tuple = tuple(assertions)
    for assertion in assertion_tuple:
        definition = contract.propositions.get(assertion.proposition_id)
        if definition is None:
            raise StagePropositionBundleError("unknown_proposition")
        expected = make_assertion(
            contract,
            proposition_id=assertion.proposition_id,
            verdict=assertion.verdict,
            reason_codes=assertion.reason_codes,
            fact_refs=assertion.fact_refs,
            provenance_refs=assertion.provenance_refs,
        )
        if assertion != expected:
            raise StagePropositionBundleError("assertion_catalog_metadata_mutated")
    _validate_relationships(
        contract,
        control_state=control_state,
        epistemic_status=epistemic_status,
        status_reasons=reasons,
        assertions=assertion_tuple,
    )
    contexts, conditions, modifiers = _layered(assertion_tuple)
    fact_refs = tuple(sorted({ref for item in assertion_tuple for ref in item.fact_refs}))
    bundle = StagePropositionBundle(
        contract_version=contract.version,
        security_id=security,
        knowledge_cutoff=cutoff,
        control_state=control_state,
        epistemic_status=epistemic_status,
        status_reasons=reasons,
        context_assertions=contexts,
        condition_assertions=conditions,
        modifier_assertions=modifiers,
        fact_refs=fact_refs,
    )
    validate_bundle(contract, bundle.to_dict())
    return bundle


def validate_bundle(
    contract: StagePropositionContract, raw: Mapping[str, Any]
) -> StagePropositionBundle:
    required = {
        "schema_version",
        "contract_version",
        "security_id",
        "knowledge_cutoff",
        "control_state",
        "epistemic_status",
        "status_reasons",
        "context_assertions",
        "condition_assertions",
        "modifier_assertions",
        "fact_refs",
        "primary_stage",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise StagePropositionBundleError("bundle_fields")
    if raw["schema_version"] != BUNDLE_SCHEMA or raw["contract_version"] != contract.version:
        raise StagePropositionBundleError("bundle_identity")
    if raw["primary_stage"] is not None:
        raise StagePropositionBundleError("primary_stage_must_be_null")
    security_id = str(raw["security_id"]).strip()
    if not security_id:
        raise StagePropositionBundleError("security_id_required")
    cutoff = _knowledge_cutoff(raw["knowledge_cutoff"])
    control_state = str(raw["control_state"])
    epistemic_status = str(raw["epistemic_status"])
    if (
        control_state not in contract.control_states
        or epistemic_status not in contract.epistemic_statuses
    ):
        raise StagePropositionBundleError("bundle_status_invalid")
    reasons_raw = raw["status_reasons"]
    if not isinstance(reasons_raw, list) or any(
        not isinstance(item, str) or not item for item in reasons_raw
    ):
        raise StagePropositionBundleError("status_reasons_invalid")
    reasons = tuple(reasons_raw)
    if list(reasons) != sorted(set(reasons)):
        raise StagePropositionBundleError("status_reasons_order")

    parsed_layers: list[tuple[str, tuple[StagePropositionAssertion, ...]]] = []
    layer_fields = (
        ("context_assertions", "context_or_structural_state"),
        ("condition_assertions", "event_or_nested_condition"),
        ("modifier_assertions", "modifier"),
    )
    all_assertions: list[StagePropositionAssertion] = []
    for field, layer in layer_fields:
        values = raw[field]
        if not isinstance(values, list):
            raise StagePropositionBundleError("assertion_list_required")
        parsed = tuple(_assertion_from_raw(contract, item) for item in values)
        if any(item.layer != layer for item in parsed):
            raise StagePropositionBundleError("assertion_layer_mismatch")
        if list(item.proposition_id for item in parsed) != sorted(
            item.proposition_id for item in parsed
        ):
            raise StagePropositionBundleError("assertion_order")
        parsed_layers.append((field, parsed))
        all_assertions.extend(parsed)

    top_fact_refs = raw["fact_refs"]
    if not isinstance(top_fact_refs, list) or any(
        not isinstance(item, str) or not item for item in top_fact_refs
    ):
        raise StagePropositionBundleError("fact_refs_invalid")
    expected_fact_refs = sorted({ref for item in all_assertions for ref in item.fact_refs})
    if top_fact_refs != expected_fact_refs:
        raise StagePropositionBundleError("fact_reference_closure")

    assertion_tuple = tuple(all_assertions)
    _validate_relationships(
        contract,
        control_state=control_state,
        epistemic_status=epistemic_status,
        status_reasons=reasons,
        assertions=assertion_tuple,
    )
    parsed = dict(parsed_layers)
    return StagePropositionBundle(
        contract_version=contract.version,
        security_id=security_id,
        knowledge_cutoff=cutoff,
        control_state=control_state,
        epistemic_status=epistemic_status,
        status_reasons=reasons,
        context_assertions=parsed["context_assertions"],
        condition_assertions=parsed["condition_assertions"],
        modifier_assertions=parsed["modifier_assertions"],
        fact_refs=tuple(top_fact_refs),
    )


def consumer_view(
    contract: StagePropositionContract,
    bundle: StagePropositionBundle,
    *,
    surface: str,
) -> StagePropositionConsumerView:
    validated = validate_bundle(contract, bundle.to_dict())
    policy = contract.surfaces.get(surface)
    if policy is None:
        raise StagePropositionBundleError("unknown_surface")
    all_assertions = (
        *validated.context_assertions,
        *validated.condition_assertions,
        *validated.modifier_assertions,
    )
    if surface == "audit":
        visible = tuple(all_assertions)
    else:
        allowed_grades = set(policy.get("allowed_evidence_grades") or ())
        allowed_codes = set(policy.get("allowed_historical_stage_codes") or ())
        visible = tuple(
            item
            for item in all_assertions
            if item.evidence_grade in allowed_grades
            and item.historical_stage_code in allowed_codes
            and item.use_authority == surface
        )
    contexts, conditions, modifiers = _layered(visible)
    fact_refs = tuple(sorted({ref for item in visible for ref in item.fact_refs}))
    return StagePropositionConsumerView(
        surface=surface,
        security_id=validated.security_id,
        knowledge_cutoff=validated.knowledge_cutoff,
        control_state=validated.control_state,
        source_epistemic_status=validated.epistemic_status,
        context_assertions=contexts,
        condition_assertions=conditions,
        modifier_assertions=modifiers,
        fact_refs=fact_refs,
        hidden_assertion_count=len(all_assertions) - len(visible),
    )
