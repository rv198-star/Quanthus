from __future__ import annotations

from quanthus.stage_learning import atomic_primary_reference as api
from quanthus.stage_learning.order_reference_protocol_v2 import (
    PRIMARY_COMMIT_SCHEMA,
    PRIMARY_OPINION_SCHEMA,
    PRIMARY_PROPOSITIONS,
    PROTOCOL_DIGEST,
)
from quanthus.structure_v2_inputs import digest


def commit(case: str, role: str, values: tuple[str, str, str]):
    primary = dict(zip(PRIMARY_PROPOSITIONS, values, strict=True))
    opinion = {
        "schema_version": PRIMARY_OPINION_SCHEMA,
        "case_id": digest({"case": case}),
        "protocol_digest": PROTOCOL_DIGEST,
        "packet_digest": digest({"packet": case}),
        "reviewer_id": f"reviewer-{role}",
        "role": role,
        "provenance": {
            "kind": "deterministic_policy",
            "origin_id": "p012-e1-unit",
            "session_id": f"{case}-{role}",
        },
        "primary": primary,
        "support_refs": [digest({"support": case, "role": role})] if "TRUE" in values else [],
        "contradiction_refs": [],
        "reason": "Synthetic P012-E1 atomic reference unit case.",
        "unknowns": ["synthetic_unknown"] if "UNKNOWN" in values else [],
    }
    return {
        "schema_version": PRIMARY_COMMIT_SCHEMA,
        "opinion": opinion,
        "primary_opinion_digest": digest(opinion),
    }


def pair(case: str, short: tuple[str, str, str], fund: tuple[str, str, str]):
    return [commit(case, "short_term", short), commit(case, "fund", fund)]


def test_mixed_directional_instability_is_preserved_before_projection() -> None:
    resolved = api.resolve_atomic_primary_roles(
        pair("mixed", ("TRUE", "TRUE", "FALSE"), ("TRUE", "TRUE", "FALSE"))
    )
    assert resolved["projected_primary_reference"] == "NEITHER"
    assert resolved["mixed_directional_instability_roles"] == ["fund", "short_term"]
    assert resolved["role_atomic_primary"]["short_term"] == {
        "structural_instability_20": "TRUE",
        "coherent_directional_relocation_20": "TRUE",
        "orderly_consolidation_20": "FALSE",
    }
    assert resolved["atomic_commit_precedes_projection"] is True


def test_atomic_disagreement_is_reported_separately_from_projection_disagreement() -> None:
    resolved = api.resolve_atomic_primary_roles(
        pair("disagree", ("TRUE", "FALSE", "FALSE"), ("FALSE", "TRUE", "FALSE"))
    )
    assert resolved["projected_primary_reference"] == "AMBIGUOUS"
    assert resolved["atomic_disagreement"] == {
        "structural_instability_20": True,
        "coherent_directional_relocation_20": True,
        "orderly_consolidation_20": False,
    }
    summary = api.summarize_atomic_reference_batch([resolved])
    assert summary["projection_disagreement_case_count"] == 1
    assert summary["atomic_disagreement_case_count_by_proposition"] == {
        "structural_instability_20": 1,
        "coherent_directional_relocation_20": 1,
        "orderly_consolidation_20": 0,
    }


def test_batch_summary_keeps_stage0_distinct_from_atomic_instability() -> None:
    stage0 = api.resolve_atomic_primary_roles(
        pair("stage0", ("TRUE", "FALSE", "FALSE"), ("TRUE", "FALSE", "FALSE"))
    )
    mixed = api.resolve_atomic_primary_roles(
        pair("mixed", ("TRUE", "TRUE", "FALSE"), ("TRUE", "TRUE", "FALSE"))
    )
    stage2 = api.resolve_atomic_primary_roles(
        pair("stage2", ("FALSE", "FALSE", "TRUE"), ("FALSE", "FALSE", "TRUE"))
    )
    summary = api.summarize_atomic_reference_batch([stage0, mixed, stage2])
    assert summary["projected_reference_counts"] == {"NEITHER": 1, "STAGE_0": 1, "STAGE_2": 1}
    assert summary["mixed_directional_instability_case_count"] == 1
    assert summary["stage0_is_not_atomic_instability"] is True


def test_unavailable_primary_fails_closed_and_preserves_unknown_atomics() -> None:
    resolved = api.resolve_atomic_primary_roles(
        pair(
            "unavailable",
            ("UNKNOWN", "UNKNOWN", "UNKNOWN"),
            ("UNKNOWN", "UNKNOWN", "UNKNOWN"),
        ),
        primary_available=False,
    )
    assert resolved["projected_primary_reference"] == "INPUT_UNAVAILABLE"
    assert set(resolved["role_atomic_primary"]["fund"].values()) == {"UNKNOWN"}
