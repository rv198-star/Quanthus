"""Literal populations and units never become a guessed uniform entitlement."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_nonuniform_revision import (
    CONTRACT_ID,
    PRIORITY_ID,
    REPLAY_MODULES,
    parse_native_claims,
    revise_nonuniform_terms,
)
from tests.unit.test_base22_notice_nonuniform_facts import TARGET, TEXT, doc
from tests.unit.test_base22_notice_restatement import case


def source(text=TEXT):
    # The native adapter also requires the two final literal declarations in the
    # current scheme, rather than trusting a summary or performing its formula.
    return doc(text)


def fixture(text=TEXT, mixed=False):
    raw, before, terms, _, _ = case()
    raw.update(stock_code=TARGET["stock_code"], ex_date=date(2019, 6, 3))
    if mixed:
        raw["transfer_shares"] = Decimal("10")
        before["action_type"] = "MIXED_DISTRIBUTION"
    document = source(
        text.replace("2026/7/31", "2019/5/31")
        .replace("2026/8/3", "2019/6/3")
        .replace("2026年8月3日", "2019年6月3日")
    )
    target = {**TARGET, "action_id": raw["action_id"], "ex_date": "2019-06-03"}
    proof = parse_native_claims(document, target)
    binding = {
        "contract_id": CONTRACT_ID,
        "priority_id": PRIORITY_ID,
        "original_raw_row_sha256": digest(raw),
        "before_action_sha256": digest(before),
        "before_terms_sha256": sorted(digest(t) for t in terms),
        "claim_proof_sha256": digest(proof),
        "PDF_sha256": "sha256:" + "a" * 64,
        "publisher_detail_sha256": "sha256:" + "b" * 64,
        "source_scope_identity": "sha256:" + "c" * 64,
        "document_id": "123",
        "actual_new_acquisition": "2026-10-07T06:38:20+00:00",
        "old_normalization_manifest_restored": False,
        "historical_PIT_admission": False,
    }
    return raw, before, terms, proof, binding


def project(c, clock="2026-10-08T00:00:00+00:00"):
    return revise_nonuniform_terms(
        *c[:4], binding=c[4], observed_at=clock, revision=CONTRACT_ID + ":sha256:" + "d" * 64
    )


def test_named_original_claims_keep_unknown_tax_no_uniform_cash_or_membership():
    c = fixture()
    frozen = deepcopy(c)
    actions, terms, proof = project(c)
    assert c == frozen
    assert actions[0] == {**c[1], "knowledge_to": actions[1]["knowledge_from"]}
    assert terms[0] == {**c[2][0], "knowledge_to": actions[1]["knowledge_from"]}
    assert all(t["term_type"].startswith("SOURCE_DECLARED_NONUNIFORM_") for t in terms[1:])
    assert all("TAX_UNAVAILABLE" in t["term_type"] for t in terms[1:])
    assert {t["amount_per_share"] for t in terms[1:]} == {Decimal("0.175"), Decimal("0.12675")}
    assert proof["source_claim_representation_qualified"] is True
    assert proof["general_gross_cash_qualified"] is False
    assert proof["consumer_admission"] == "NOT_GRANTED"
    assert not proof["actual_holder_membership_inferred"]
    assert actions[1]["announced_at"] is None
    assert actions[1]["effective_at"] == c[1]["effective_at"]
    _, model, extension = load_contract(Path("config/pipelines"))
    ss = schemas(model, extension)
    for table, rows in zip(
        ("dwd_corporate_action", "dwd_corporate_action_term"), (actions, terms), strict=True
    ):
        assert pa.Table.from_pylist(rows, schema=ss[table]).to_pylist() == rows


MIXED = (
    "证券代码:600001\n本公司2011年半年度权益分派方案为:\n"
    "1、原有股份扣除非公开发行新增股份,每10股派0.125元(含税)。\n"
    "2、以公司现有总股本为基数,向全体股东每10股派1元(含税),"
    "同时,以资本公积金向全体股东每10股转增10股。"
    "注:以上1、2项合计共向原文具名范围每10股转增10股派发现金0.5625元(含税)。\n"
    "二、权益登记日与除权除息日\n除权除息日为2026年8月3日。"
)


def test_mixed_uses_literal_money_per_ten_and_share_ratios_without_sum_or_average():
    c = fixture(MIXED, mixed=True)
    actions, terms, proof = project(c)
    new = terms[len(c[2]) :]
    assert len(new) == 4
    assert all(t["amount_per_share"] is None for t in new)
    assert {t["ratio_numerator"] for t in new} == {
        Decimal("0.125"),
        Decimal("1"),
        Decimal("10"),
        Decimal("0.5625"),
    }
    assert all(t["ratio_denominator"] == Decimal(10) for t in new)
    assert proof["tax_basis_unavailable_terms"] == 0
    assert not proof["general_gross_cash_qualified"]  # No one generic entitlement.
    assert not proof["aggregate_computed"]
    assert actions[1]["action_type"] == "MIXED_DISTRIBUTION"
    assert len({t["corporate_action_term_id"] for t in new}) == 4
    assert all(t["option_id"] is None for t in new)


@pytest.mark.parametrize(
    "defect",
    [
        "raw",
        "before",
        "terms",
        "claims",
        "priority",
        "time",
        "restored",
        "pit",
        "document",
        "missing",
        "gross",
        "negative",
        "denominator",
        "source_scope",
    ],
)
def test_unbound_or_resealed_inferred_changes_fail_closed(defect):
    c = fixture()
    if defect in {"raw", "before", "terms", "claims"}:
        field = {
            "raw": "original_raw_row_sha256",
            "before": "before_action_sha256",
            "terms": "before_terms_sha256",
            "claims": "claim_proof_sha256",
        }[defect]
        c[4][field] = None
    elif defect == "priority":
        c[4]["priority_id"] = "last_amount_wins"
    elif defect == "time":
        c[4]["actual_new_acquisition"] = "2026-10-09T00:00:00+00:00"
    elif defect in {"restored", "pit"}:
        c[4][
            "old_normalization_manifest_restored"
            if defect == "restored"
            else "historical_PIT_admission"
        ] = True
    elif defect == "document":
        c[4]["document_id"] = "124"
    elif defect == "source_scope":
        c[4]["source_scope_identity"] = None
    else:
        f = c[3]["facts"]
        if defect == "missing":
            f.pop()
        elif defect == "gross":
            f[0]["tax_basis"] = "EXPLICIT_GROSS"
        elif defect == "negative":
            f[0]["literal_numeric_token"] = "-1"
        else:
            f[0]["literal_denominator"] = 10
        c[3]["identity"] = digest({k: v for k, v in c[3].items() if k != "identity"})
        c[4]["claim_proof_sha256"] = digest(c[3])
    with pytest.raises(BoundedError, match="NONUNIFORM"):
        project(c)


def test_current_scheme_conflict_cannot_hide_behind_correct_summary():
    broken = TEXT.replace("实际每股分配金额为0.175元", "实际每股分配金额为0.176元")
    with pytest.raises(BoundedError, match="NONUNIFORM"):
        parse_native_claims(source(broken), TARGET)
    with pytest.raises(BoundedError, match="NONUNIFORM"):
        parse_native_claims(
            source(TEXT.replace("实际每股分配金额为0.12675元", "实际金额未知")), TARGET
        )


def test_replay_closure_includes_all_recursive_local_imports():
    import ast

    directory = Path("src/quanthus/pipelines/securities_warehouse")
    queue, seen = ["base22_nonuniform_revision"], set()
    while queue:
        name = queue.pop()
        path = directory / (name + ".py")
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                queue.append(node.module.split(".")[0])
    assert {n + ".py" for n in seen} == set(REPLAY_MODULES)
