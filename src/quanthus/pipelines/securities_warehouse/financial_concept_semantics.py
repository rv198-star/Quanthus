"""Validate the versioned Issue #78 financial-concept remediation policy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class FinancialConceptSemanticsPolicyError(RuntimeError):
    """Raised when the financial-concept policy is incomplete or unbound."""


@dataclass(frozen=True)
class FinancialConceptSemanticsPolicy:
    sha256: str
    builder_contract: str
    knowledge_interval_identity: tuple[str, ...]


def _sha256(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise FinancialConceptSemanticsPolicyError(
            f"cannot read financial-concept policy evidence: {path}"
        ) from exc


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FinancialConceptSemanticsPolicyError(f"{label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise FinancialConceptSemanticsPolicyError(f"{label} must be non-empty text")
    return value


def _evidence_path(repository: Path, value: object, label: str) -> Path:
    relative = PurePosixPath(_text(value, f"{label}.path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise FinancialConceptSemanticsPolicyError(f"{label} path is unsafe")
    path = repository.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(repository):
        raise FinancialConceptSemanticsPolicyError(f"{label} is not a repository file")
    return path


def load_financial_concept_semantics_policy(
    policy_path: Path, *, model_path: Path
) -> FinancialConceptSemanticsPolicy:
    """Return one policy only after all frozen authority bindings verify."""

    try:
        policy_bytes = policy_path.read_bytes()
        raw = json.loads(policy_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FinancialConceptSemanticsPolicyError(
            f"cannot read financial-concept semantics policy: {policy_path}"
        ) from exc
    policy = _mapping(raw, "financial-concept semantics policy")
    if (
        policy.get("schema_version") != 1
        or policy.get("kind") != "dwd_financial_concept_semantics_policy"
        or policy.get("policy_version") != "1.0.0"
        or policy.get("status") != "approved_remediation_contract"
        or policy.get("governing_issue") != 78
        or policy.get("table") != "dwd_financial_concept"
    ):
        raise FinancialConceptSemanticsPolicyError(
            "financial-concept semantics policy identity is invalid"
        )
    builder_contract = _text(policy.get("builder_contract"), "builder_contract")
    if builder_contract != "financial-reporting-six-source-v3":
        raise FinancialConceptSemanticsPolicyError("financial-concept builder contract differs")
    if policy.get("base_model_sha256") != _sha256(model_path):
        raise FinancialConceptSemanticsPolicyError("financial-concept base model differs")

    repository = model_path.resolve().parents[2]
    supersedes = _mapping(policy.get("supersedes"), "supersedes")
    if supersedes.get("table") != "dwd_financial_concept":
        raise FinancialConceptSemanticsPolicyError("superseded table differs")
    superseded_path = _evidence_path(repository, supersedes.get("policy"), "supersedes")
    if supersedes.get("policy_sha256") != _sha256(superseded_path):
        raise FinancialConceptSemanticsPolicyError("superseded policy digest differs")

    decision = _mapping(policy.get("decision_evidence"), "decision_evidence")
    decision_path = _evidence_path(repository, decision.get("path"), "decision_evidence")
    if (
        decision.get("sha256") != _sha256(decision_path)
        or decision.get("section") != "B. Financial concept effective interval"
    ):
        raise FinancialConceptSemanticsPolicyError("decision evidence binding differs")

    raw_identity = policy.get("knowledge_interval_identity")
    if raw_identity != ["financial_concept_id", "taxonomy_version"]:
        raise FinancialConceptSemanticsPolicyError("knowledge interval identity differs")
    for field in ("knowledge_rule", "effective_rule", "revision_rule", "authority_boundary"):
        _text(policy.get(field), field)
    required_evidence = policy.get("required_evidence")
    if (
        not isinstance(required_evidence, list)
        or len(required_evidence) < 5
        or any(not isinstance(item, str) or not item for item in required_evidence)
    ):
        raise FinancialConceptSemanticsPolicyError("required evidence is incomplete")
    return FinancialConceptSemanticsPolicy(
        sha256="sha256:" + hashlib.sha256(policy_bytes).hexdigest(),
        builder_contract=builder_contract,
        knowledge_interval_identity=tuple(raw_identity),
    )
