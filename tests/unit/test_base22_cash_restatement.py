from copy import deepcopy
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_cash_restatement import cash_restatement_binding
from quanthus.pipelines.securities_warehouse.disclosure_event import _stable_id
from tests.unit.test_base22_canonical_action_revision import case


def fixture():
    raw, old, donor, terms, binding, clock, revision = case()
    raw.update(
        source="md.dividend_history",
        progress="实施",
        cash_per_share=0.125,
        bonus_shares=0,
        transfer_shares=0,
    )
    donor["record_source"] = "base22_f10/dividend_implemented"
    terms[0]["record_source"] = donor["record_source"]
    terms[0]["amount_per_share"] = Decimal("0.1250000000")
    before = {
        **terms[0],
        "corporate_action_id": old["corporate_action_id"],
        "corporate_action_term_id": _stable_id(
            "corporate_action_term", old["corporate_action_id"], "CASH_AMOUNT_PER_SHARE"
        ),
        "knowledge_from": old["knowledge_from"],
        "announced_at": old["announced_at"],
        "record_source": old["record_source"],
        "source_record_id": old["source_record_id"],
        "source_revision": old["source_revision"],
        "source_artifact_sha256": old["source_artifact_sha256"],
    }
    return raw, old, [before], donor, terms, binding


def bind(c):
    raw, old, before, donor, terms, binding = c
    return cash_restatement_binding(
        raw,
        old,
        before,
        donor,
        terms,
        source_scope_identity=binding["source_scope_identity"],
        raw_replay_receipt_identity=binding["raw_replay_receipt_sha256"],
    )


def test_exact_same_cash_is_new_source_evidence_without_priority():
    c = fixture()
    before = deepcopy(c)
    binding = bind(c)
    assert c == before
    assert binding["disposition"] == "UNCONTESTED_RAW_SOURCE_BOUND_SUCCESSOR"
    assert binding["original_raw_row_sha256"] == digest(c[0])
    assert binding["cash_source_priority_granted"] is False
    assert binding["historical_PIT_admission"] is False


def test_unknown_original_share_components_remain_unknown_in_cash_only_successor():
    c = fixture()
    c[0]["bonus_shares"] = c[0]["transfer_shares"] = None
    before = deepcopy(c)
    binding = bind(c)
    assert c == before
    assert binding["original_unavailable_share_fields"] == ["bonus_shares", "transfer_shares"]
    assert len(c[4]) == 1 and c[4][0]["term_type"] == "CASH_AMOUNT_PER_SHARE"


@pytest.mark.parametrize(
    "change",
    [
        "raw_cash",
        "old_cash",
        "donor_cash",
        "currency",
        "status",
        "source",
        "multiple_terms",
        "nonzero_bonus",
    ],
)
def test_conflict_or_incomplete_term_set_cannot_be_admitted(change):
    c = fixture()
    if change == "raw_cash":
        c[0]["cash_per_share"] = 0.126
    elif change == "old_cash":
        c[2][0]["amount_per_share"] += 1
    elif change == "donor_cash":
        c[4][0]["amount_per_share"] += 1
    elif change == "currency":
        c[4][0]["currency"] = "USD"
    elif change == "status":
        c[0]["progress"] = "预案"
    elif change == "source":
        c[0]["source"] = "opaque-normalization"
    elif change == "multiple_terms":
        c[4].append(deepcopy(c[4][0]))
    else:
        c[2].append(
            {
                **c[2][0],
                "term_type": "BONUS_SHARES_PER_SHARE",
                "amount_per_share": None,
                "ratio_numerator": Decimal(1),
                "ratio_denominator": Decimal(1),
            }
        )
    with pytest.raises(BoundedError, match="CASH_RESTATEMENT"):
        bind(c)
