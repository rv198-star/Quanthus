"""P3-STAGE-012-E1 atomic-first Primary reference projection.

This module does not replace the historical P3-STAGE-008 Primary protocol. It composes its
validated immutable role commits into an explicit atomic ledger before applying the existing
Stage0/Stage2 projection, so mixed instability + relocation is retained rather than hidden by
a single projected label.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from quanthus.stage_learning.order_reference_protocol_v2 import (
    PRIMARY_PROPOSITIONS,
    ROLES,
    project_atomic_primary,
    resolve_primary_roles,
    validate_primary_commit,
)

ATOMIC_REFERENCE_SCHEMA = "quanthus.stage-atomic-primary-reference.v1"
ATOMIC_BATCH_SCHEMA = "quanthus.stage-atomic-primary-reference-batch.v1"


class AtomicPrimaryReferenceError(ValueError):
    """The P3-STAGE-012-E1 atomic-first composition contract was violated."""


def resolve_atomic_primary_roles(
    primary_commits: Sequence[Mapping[str, Any]], *, primary_available: bool = True
) -> dict[str, Any]:
    """Preserve validated role atomics and then reuse the historical downstream projection."""
    projected = resolve_primary_roles(primary_commits, primary_available=primary_available)
    commits = [validate_primary_commit(item) for item in primary_commits]
    opinions = [item["opinion"] for item in commits]
    role_atomic = {
        item["role"]: dict(item["primary"])
        for item in opinions
    }
    if set(role_atomic) != set(ROLES):
        raise AtomicPrimaryReferenceError("atomic_roles_differ")
    role_projection = {
        role: project_atomic_primary(values, available=primary_available)
        for role, values in role_atomic.items()
    }
    disagreements = {
        proposition: len({values[proposition] for values in role_atomic.values()}) > 1
        for proposition in PRIMARY_PROPOSITIONS
    }
    mixed_roles = sorted(
        role
        for role, values in role_atomic.items()
        if (
            values["structural_instability_20"] == "TRUE"
            and values["coherent_directional_relocation_20"] == "TRUE"
            and values["orderly_consolidation_20"] == "FALSE"
        )
    )
    return {
        "schema_version": ATOMIC_REFERENCE_SCHEMA,
        "case_id": projected["case_id"],
        "role_atomic_primary": role_atomic,
        "atomic_disagreement": disagreements,
        "role_projection": role_projection,
        "mixed_directional_instability_roles": mixed_roles,
        "projected_primary_reference": projected["primary_reference"],
        "projected_reason_codes": list(projected["reason_codes"]),
        "primary_opinion_digests": list(projected["primary_opinion_digests"]),
        "atomic_commit_precedes_projection": True,
    }


def summarize_atomic_reference_batch(
    resolved_cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize atomic and projected denominators without converting mixed states to Stage0."""
    if not resolved_cases:
        raise AtomicPrimaryReferenceError("atomic_batch_empty")
    case_ids = [str(item.get("case_id") or "") for item in resolved_cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise AtomicPrimaryReferenceError("atomic_batch_case_identity")
    projected = Counter()
    atomic_disagreement = Counter({key: 0 for key in PRIMARY_PROPOSITIONS})
    mixed_case_count = 0
    projection_disagreement_count = 0
    for item in resolved_cases:
        if item.get("schema_version") != ATOMIC_REFERENCE_SCHEMA:
            raise AtomicPrimaryReferenceError("atomic_reference_schema")
        reference = str(item.get("projected_primary_reference") or "")
        if not reference:
            raise AtomicPrimaryReferenceError("atomic_reference_projection_missing")
        projected[reference] += 1
        disagreement = item.get("atomic_disagreement")
        if not isinstance(disagreement, Mapping) or set(disagreement) != set(PRIMARY_PROPOSITIONS):
            raise AtomicPrimaryReferenceError("atomic_reference_disagreement_shape")
        for proposition in PRIMARY_PROPOSITIONS:
            atomic_disagreement[proposition] += int(disagreement[proposition] is True)
        mixed_case_count += int(bool(item.get("mixed_directional_instability_roles")))
        projection_disagreement_count += int(
            "cross_role_primary_disagreement" in item.get("projected_reason_codes", [])
        )
    return {
        "schema_version": ATOMIC_BATCH_SCHEMA,
        "case_count": len(resolved_cases),
        "projected_reference_counts": dict(sorted(projected.items())),
        "mixed_directional_instability_case_count": mixed_case_count,
        "projection_disagreement_case_count": projection_disagreement_count,
        "atomic_disagreement_case_count_by_proposition": dict(atomic_disagreement),
        "stage0_is_not_atomic_instability": True,
    }
