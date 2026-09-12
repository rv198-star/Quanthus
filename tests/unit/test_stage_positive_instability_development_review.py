from __future__ import annotations

from quanthus.stage_learning.positive_instability_development_review import (
    load_ledger,
    resolve_references,
)


def _cases() -> list[dict]:
    return [
        {
            "case_id": "sha256:" + f"{index + 1:064x}",
            "primary_available": True,
        }
        for index in range(122)
    ]


def test_sealed_development_ledger_has_expected_primary_denominators() -> None:
    ledger = load_ledger()
    assert ledger["metadata"]["candidate_output_seen_during_review"] == "false"
    assert ledger["metadata"]["threshold_basis_seen_during_review"] == "false"
    assert ledger["metadata"]["explicit_recent_derived_facts_seen"] == "false"
    assert len(ledger["rows"]) == 122
    resolved = resolve_references(_cases(), ledger)
    assert resolved["primary_reference_counts"] == {
        "AMBIGUOUS": 15,
        "NEITHER": 59,
        "STAGE_0": 37,
        "STAGE_2": 11,
    }
    assert resolved["primary_disagreement_count"] == 15
