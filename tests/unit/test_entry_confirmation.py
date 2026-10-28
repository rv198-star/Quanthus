from copy import deepcopy

import pytest

from quanthus.stage_learning.entry_confirmation import (
    reference_support,
    require_reference_admission,
    training_support,
)


def fixture():
    population = [
        {
            "blind_id": str(i),
            "company_id": str(i),
            "available": True,
            "time_block": "2021-04" if i < 6 else "2021-05",
        }
        for i in range(12)
    ]
    panel = [
        {
            "blind_id": str(i),
            "opinion": "POSITIVE" if i < 6 else "NEGATIVE",
            "evidence": "Current20 evidence",
            "counterconsideration": "Prior20 context",
        }
        for i in range(12)
    ]
    return population, panel, deepcopy(panel)


def test_support_is_not_a_fit_or_consumer_grant():
    receipt, consensus = reference_support(*fixture())
    require_reference_admission(receipt)
    assert len(consensus) == receipt["opportunities"] == 12
    assert receipt["companies_by_class"] == {"POSITIVE": 6, "NEGATIVE": 6}
    assert not receipt["individual_opinions_revealed"]
    assert receipt["consumer_admission"] == "NOT_GRANTED"


def test_disagreement_is_unknown_and_failed_support_blocks_fit():
    population, a, b = fixture()
    b[0]["opinion"] = "NEGATIVE"
    receipt, consensus = reference_support(population, a, b)
    assert consensus["0"] == "UNKNOWN" and receipt["unknown"] == 1
    with pytest.raises(ValueError, match="NO_FIT"):
        require_reference_admission(receipt)


def test_many_events_of_one_issuer_do_not_supply_class_support():
    population, a, b = fixture()
    for row in population[:6]:
        row["company_id"] = "one"
    receipt, _ = reference_support(population, a, b)
    assert "insufficient_distinct_companies_per_class" in receipt["failures"]


def test_missing_or_duplicate_opinion_cannot_shrink_denominator():
    population, a, b = fixture()
    for broken in (b[:-1], b[:-1] + [b[0]]):
        with pytest.raises(ValueError, match="EXACT_SET"):
            reference_support(population, a, broken)


def test_unavailable_cannot_become_negative_or_disappear():
    population, a, b = fixture()
    population[0]["available"] = False
    with pytest.raises(ValueError, match="UNAVAILABLE_RELABELED"):
        reference_support(population, a, b)
    a[0]["opinion"] = b[0]["opinion"] = "UNAVAILABLE"
    receipt, consensus = reference_support(population, a, b)
    assert receipt["opportunities"] == 12 and receipt["unavailable"] == 1
    assert consensus["0"] == "UNAVAILABLE"


def test_nonempty_evidence_and_counterconsideration_required():
    population, a, b = fixture()
    a[0]["evidence"] = ""
    with pytest.raises(ValueError, match="RATIONALE"):
        reference_support(population, a, b)


def test_training_exact_identity_and_cutoff_preserved():
    rows = [
        {
            "sample_id": str(i),
            "company_id": str(i),
            "anchor": "2020-09-30" if i < 6 else "2021-03-31",
            "label": int(i < 4),
            "features": [0.0] * 7,
        }
        for i in range(12)
    ]
    ids = [{k: r[k] for k in ("sample_id", "company_id", "anchor")} for r in rows]
    assert training_support(rows, ids, cutoff="2021-03-31")["mask_support"] == [0.0]
    with pytest.raises(ValueError, match="IDENTITY_SET"):
        training_support(rows[:-1], ids, cutoff="2021-03-31")
    with pytest.raises(ValueError, match="CUTOFF"):
        training_support(rows, ids, cutoff="2020-12-31")
