from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_action import (
    parse_implemented_notice_cash,
)
from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
    CONTRACT_ID,
    PRIORITY_ID,
    restate_notice_cash,
)
from tests.unit.test_base22_cash_restatement import fixture


def case():
    raw, old, terms, _, _, _ = fixture()
    raw.update(
        stock_code="002001", ex_date=date(2019, 6, 3), bonus_shares=None, transfer_shares=None
    )
    atom = parse_implemented_notice_cash(
        "证券代码：002001 每10股派发现金红利1.5元（含税） 除权除息日：2019年6月3日",
        stock_code="002001", ex_date=raw["ex_date"], publisher_title="权益分派实施公告",
    )
    binding = {
        "contract_id": CONTRACT_ID,
        "priority_id": PRIORITY_ID,
        "disposition": "EXACT_PRIMARY_ISSUER_CASH_ONLY",
        "old_normalization_manifest_restored": False,
        "historical_PIT_admission": False,
        "original_raw_row_sha256": digest(raw),
        "before_action_sha256": digest(old),
        "before_terms_sha256": sorted(digest(t) for t in terms),
        "notice_atom_sha256": digest(atom),
        "PDF_sha256": "sha256:" + "a" * 64,
        "publisher_detail_sha256": "sha256:" + "b" * 64,
        "source_scope_identity": "sha256:" + "c" * 64,
        "document_id": "123456",
        "actual_new_acquisition": "2026-10-07T06:38:20+00:00",
    }
    return raw, old, terms, atom, binding


def project(c, clock="2026-10-07T07:00:00+00:00"):
    return restate_notice_cash(
        *c[:4], binding=c[4], observed_at=clock, revision=CONTRACT_ID + ":sha256:" + "d" * 64
    )


def test_primary_cash_new_version_preserves_old_and_unknowns():
    c = case()
    before = deepcopy(c)
    actions, terms, proof = project(c)
    assert c == before
    assert actions[0] == {**c[1], "knowledge_to": actions[1]["knowledge_from"]}
    assert terms[-1]["amount_per_share"] == Decimal("0.15")
    assert actions[1]["announced_at"] is None
    assert actions[1]["effective_at"] == c[1]["effective_at"]
    assert proof["cash_changed"] is True
    assert proof["raw_unknown_share_fields"] == ["bonus_shares", "transfer_shares"]
    assert proof["historical_PIT_admission"] is False


def test_preexisting_raw_typed_difference_is_disclosed_and_old_atoms_preserved():
    c = case()
    c[0]["cash_per_share"] = 0.12500000000000003
    c[4]["original_raw_row_sha256"] = digest(c[0])
    actions, terms, proof = project(c)
    assert proof["retained_raw_vs_before_cash_equal"] is False
    assert actions[0] == {**c[1], "knowledge_to": actions[1]["knowledge_from"]}
    assert terms[0]["amount_per_share"].as_tuple() == c[2][0]["amount_per_share"].as_tuple()


@pytest.mark.parametrize(
    "change",
    ["old_hash", "term_hash", "raw_hash", "notice_hash", "priority", "old_restored", "pit",
     "wrong_security", "wrong_date", "currency", "nonzero_shares", "old_cash", "nonexact_cash"],
)
def test_missing_closure_or_unqualified_change_fails(change):
    c = case()
    if change in {"old_hash", "term_hash", "raw_hash", "notice_hash"}:
        field = {"old_hash": "before_action_sha256", "term_hash": "before_terms_sha256",
                 "raw_hash": "original_raw_row_sha256", "notice_hash": "notice_atom_sha256"}[change]
        c[4][field] = None
    elif change == "priority":
        c[4]["priority_id"] = "take_latest"
    elif change == "old_restored":
        c[4]["old_normalization_manifest_restored"] = True
    elif change == "pit":
        c[4]["historical_PIT_admission"] = True
    elif change == "wrong_security":
        c[3]["stock_code"] = "002002"
    elif change == "wrong_date":
        c[3]["ex_date"] = date(2019, 6, 4)
    elif change == "currency":
        c[3]["currency"] = "USD"
    elif change == "nonzero_shares":
        c[0]["transfer_shares"] = 1
    elif change == "old_cash":
        c[2][0]["amount_per_share"] = Decimal("0.1")
    else:
        c[3]["cash_per_share"] = Decimal("0.12345678901")
    with pytest.raises(BoundedError, match="NOTICE_RESTATEMENT"):
        project(c)


@pytest.mark.parametrize("clock", ["2026-10-07T06:38:19+00:00", "2026-07-01T00:00:00+00:00"])
def test_new_clock_never_predates_actual_acquisition_or_old_version(clock):
    with pytest.raises(BoundedError, match="REAL_NEW_KNOWLEDGE"):
        project(case(), clock)
