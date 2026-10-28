from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.stage_learning.entry_baseline import alias_exclusion, recover_baseline
from quanthus.structure_v2_inputs import digest

SHA = "sha256:" + "1" * 64
ROOT = Path(__file__).resolve().parents[2]


def fixture():
    card = load_candidate_rule_card(
        ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    row = {"security_id": "000001", "signal_date": "2021-09-02",
           "business_close": "2021-09-02T15:00:00+08:00",
           "eligibility_evidence_digest": SHA, "feature_evidence_digest": SHA}
    cutoff = "2026-10-25T08:51:06.934678+00:00"
    prep = {"source_information_cutoff": cutoff, "effective_source_manifest_sha256": SHA,
            "snapshot_id": "frozen", "candidates": [row]}
    event = {"purpose_id": "p3-historical-development-robustness-v1",
             "security_id": "000001", "signal_date": "2021-09-02",
             "candidate_rule_id": card.rule_id, "candidate_rule_version": card.rule_version,
             "snapshot_id": "frozen", "knowledge_cutoff": cutoff,
             "candidate_source_known_at": cutoff, "business_close": row["business_close"],
             "candidate_rule_digest": card.semantic_digest, "snapshot_manifest_digest": SHA,
             "eligibility_evidence_digest": SHA, "feature_window_manifest_digest": SHA,
             "historical_availability_claim": False}
    key = "entry:r2-event:" + digest(event).removeprefix("sha256:")
    decisions = [{"surface": "entry", "market_date": row["signal_date"],
                  "decision_key": key, "final_action": "no_action",
                  "formal_decision_digest": SHA, "allocated": "NOT_READ",
                  "filled": "NOT_READ"}]
    return prep, decisions, card


def test_exact_original_decision_is_preserved_without_fill_projection():
    prep, decisions, card = fixture()
    result, = recover_baseline(prep, decisions, card, runtime_snapshot_id="frozen")
    assert not result.baseline_allowed
    assert result.baseline_decision_digest == SHA
    decisions[0]["final_action"] = "trial_entry"
    assert recover_baseline(prep, decisions, card, runtime_snapshot_id="frozen")[0].baseline_allowed


@pytest.mark.parametrize("mutation", ["date", "candidate", "duplicate", "missing", "source"])
def test_incomplete_or_misaligned_original_population_fails(mutation):
    prep, decisions, card = fixture()
    if mutation == "date":
        decisions[0]["market_date"] = "2021-09-03"
    elif mutation == "candidate":
        prep["candidates"][0]["feature_evidence_digest"] = "sha256:" + "2" * 64
    elif mutation == "duplicate":
        decisions.append(deepcopy(decisions[0]))
    elif mutation == "missing":
        decisions.clear()
    else:
        prep["effective_source_manifest_sha256"] = "unknown"
    with pytest.raises(ValueError, match="ENTRY_BASELINE"):
        recover_baseline(prep, decisions, card, runtime_snapshot_id="frozen")


def test_all_company_aliases_excluded_and_ambiguous_code_not_qualified():
    security = [{"security_id": "s1", "company_id": "issuer"},
                {"security_id": "s2", "company_id": "issuer"},
                {"security_id": "s3", "company_id": "other"}]
    listing = [{"security_id": "s1", "listing_code": "old"},
               {"security_id": "s2", "listing_code": "new"},
               {"security_id": "s2", "listing_code": "ambiguous"},
               {"security_id": "s3", "listing_code": "ambiguous"}]
    identities, blocked = alias_exclusion(listing, security, {"s1"}, set())
    assert identities == {"old": "issuer", "new": "issuer"}
    assert blocked == {"old", "new", "ambiguous"}
