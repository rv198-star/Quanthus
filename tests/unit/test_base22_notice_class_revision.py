"""An ambiguous uniform term becomes explicit source classes, never an inferred average."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_class_revision import (
    CONTRACT_ID,
    PRIORITY_ID,
    TABLES,
    revise_class_terms,
)
from quanthus.pipelines.securities_warehouse.base22_notice_class_terms import parse_class_cash_terms
from tests.unit.test_base22_notice_class_terms import BODY
from tests.unit.test_base22_notice_restatement import case


def fixture():
    raw, old, terms, _, _ = case()
    raw.update(stock_code="600001", ex_date=date(2019, 6, 3))
    notice = parse_class_cash_terms(
        BODY.replace("2026年8月3日", "2019年6月3日"),
        stock_code=raw["stock_code"],
        ex_date=raw["ex_date"],
        publisher_title="年度利润分配实施公告",
    )
    binding = {
        "contract_id": CONTRACT_ID,
        "priority_id": PRIORITY_ID,
        "disposition": "EXACT_PRIMARY_LITERAL_CLASS_TERMS",
        "old_normalization_manifest_restored": False,
        "historical_PIT_admission": False,
        "original_raw_row_sha256": digest(raw),
        "before_action_sha256": digest(old),
        "before_terms_sha256": sorted(digest(t) for t in terms),
        "notice_proof_sha256": digest(notice),
        "PDF_sha256": "sha256:" + "a" * 64,
        "publisher_detail_sha256": "sha256:" + "b" * 64,
        "source_scope_identity": "sha256:" + "c" * 64,
        "document_id": "123456",
        "actual_new_acquisition": "2026-10-07T06:38:20+00:00",
    }
    return raw, old, terms, notice, binding


def project(c, clock="2026-10-08T00:00:00+00:00"):
    return revise_class_terms(
        *c[:4], binding=c[4], observed_at=clock, revision=CONTRACT_ID + ":sha256:" + "d" * 64
    )


def test_close_ambiguous_scalar_open_two_actual_classes_keep_every_old_atom_and_zero():
    from pathlib import Path

    import pyarrow as pa

    c = fixture()
    frozen = deepcopy(c)
    actions, terms, proof = project(c)
    assert c == frozen
    assert actions[0] == {**c[1], "knowledge_to": actions[1]["knowledge_from"]}
    assert terms[0] == {**c[2][0], "knowledge_to": actions[1]["knowledge_from"]}
    assert terms[0]["amount_per_share"].as_tuple() == c[2][0]["amount_per_share"].as_tuple()
    assert {t["amount_per_share"] for t in terms[1:]} == {Decimal(0), Decimal("1.2345678901")}
    assert all(t["term_type"].startswith("SOURCE_CLASS_GROSS_CASH_") for t in terms[1:])
    assert all(t["option_id"] is None for t in terms)
    assert actions[1]["announced_at"] is None
    assert actions[1]["effective_at"] == c[1]["effective_at"]
    assert not proof["uniform_cash_emitted"] and not proof["historical_PIT_admission"]
    _, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    for table, rows in zip(TABLES, [actions, terms], strict=True):
        assert pa.Table.from_pylist(rows, schema=ss[table]).to_pylist() == rows


@pytest.mark.parametrize(
    "defect",
    [
        "raw_hash",
        "old_hash",
        "term_hash",
        "notice_hash",
        "priority",
        "pit",
        "restore",
        "code",
        "date",
        "nonzero_bonus",
        "missing_class",
        "old_term",
        "unknown_role",
        "membership",
        "net",
        "negative",
        "precision",
        "duplicate_class",
        "future_source",
    ],
)
def test_missing_proof_inferred_scope_or_other_unqualified_change_is_rejected(defect):
    c = fixture()
    if defect in {"raw_hash", "old_hash", "term_hash", "notice_hash"}:
        field = {
            "raw_hash": "original_raw_row_sha256",
            "old_hash": "before_action_sha256",
            "term_hash": "before_terms_sha256",
            "notice_hash": "notice_proof_sha256",
        }[defect]
        c[4][field] = None
    elif defect == "priority":
        c[4]["priority_id"] = "take_latest"
    elif defect == "pit":
        c[4]["historical_PIT_admission"] = True
    elif defect == "restore":
        c[4]["old_normalization_manifest_restored"] = True
    elif defect == "code":
        c[3]["stock_code"] = "600002"
    elif defect == "date":
        c[3]["ex_date"] = date(2019, 6, 4)
    elif defect == "nonzero_bonus":
        c[0]["bonus_shares"] = 1
        c[4]["original_raw_row_sha256"] = digest(c[0])
    elif defect == "missing_class":
        c[3]["scheme"]["terms"].pop()
    elif defect == "old_term":
        c[2][0]["amount_per_share"] += 1
    elif defect == "unknown_role":
        c[3]["scheme"]["terms"][0]["class_role"] = "GUESSED"
    elif defect == "membership":
        c[3]["actual_holder_membership_inferred"] = True
    elif defect == "net":
        c[3]["scheme"]["terms"][0]["tax_basis"] = "NET"
    elif defect == "negative":
        c[3]["scheme"]["terms"][0]["amount_per_share"] = Decimal(-1)
    elif defect == "precision":
        c[3]["scheme"]["terms"][0]["amount_per_share"] = Decimal("0.123456789012")
    elif defect == "duplicate_class":
        c[3]["scheme"]["terms"][1] = deepcopy(c[3]["scheme"]["terms"][0])
    else:
        c[4]["actual_new_acquisition"] = "2026-10-09T00:00:00+00:00"
    if defect in {
        "missing_class",
        "unknown_role",
        "net",
        "negative",
        "precision",
        "duplicate_class",
    }:
        c[3]["identity"] = digest({k: v for k, v in c[3].items() if k != "identity"})
        c[4]["notice_proof_sha256"] = digest(c[3])
    with pytest.raises(BoundedError, match="CLASS_REVISION"):
        project(c)
