from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_gross_layout import (
    parse_gross_layout_cash,
    select_gross_layout_candidates,
)

BODY = (
    "证券代码:600001\n重要内容提示:\n每股分配比例\n"
    "每股合计派发现金红利人民币0.6元(含税)。\n一、通过分配方案的股东大会\n"
    "二、分配方案\n分派对象:本公司全体 A 股股东。本公司 H 股股东的现金红利"
    "发放的具体操作不适用本公告。\n本次分红派息以方案实施前总股本为基数:"
    "派发年度股息每股派发现金红利人民币0.1元(含税);"
    "派发特别股息每股派发现金人民币0.4元(含税)。"
    "两项合计派发现金红利0.6元(含税)。\n三、相关日期\n除息日:2026年8月3日"
)


def parse(body=BODY):
    return parse_gross_layout_cash(body, stock_code="600001", ex_date=date(2026, 8, 3),
                                   publisher_title="分红派息实施公告")


def test_printed_total_is_not_computed_sum_and_original_body_is_bound():
    atom = parse()
    assert atom["cash_per_share"] == Decimal("0.6")
    assert atom["original_text_sha256"] == digest(BODY)
    assert atom["gross_layout_evidence"]["aggregate_computed"] is False


def test_literal_gross_label_comma_across_lines_does_not_admit_net_cash():
    from tests.unit.test_base22_notice_literal_statement import BODY as CURRENT

    body = CURRENT.replace("元人民币现金(含税", "元人民币现\n金, (含税")
    atom = parse(body)
    assert atom["cash_per_share"] == Decimal("0.1234567")
    assert atom["gross_layout_evidence"]["literal_gross_punctuation_reflows"] == 2
    with pytest.raises(BoundedError):
        parse(body.replace("含税", "税后"))


@pytest.mark.parametrize("defect", ["total", "no_summary_unit", "no_A", "no_exclusion",
                                    "date", "code", "adjustment", "truncated"])
def test_unproved_scope_conflicts_and_identity_stay_closed(defect):
    body = BODY
    if defect == "total":
        body = body.replace("两项合计派发现金红利0.6", "两项合计派发现金红利0.7")
    elif defect == "no_summary_unit":
        body = body.replace("每股合计派发现金红利人民币", "每股合计派发现金红利")
    elif defect == "no_A":
        body = body.replace("全体 A 股股东", "全体股东")
    elif defect == "no_exclusion":
        body = body.replace("不适用本公告", "另有不同现金条款")
    elif defect == "date":
        body = body.replace("8月3日", "8月4日")
    elif defect == "code":
        body = body.replace("600001", "600002")
    elif defect == "adjustment":
        body = body.replace("两项合计", "调整后两项合计")
    else:
        body = body.replace("两项合计派发现金红利0.6元(含税)", "两项合计未能读取")
    with pytest.raises(BoundedError):
        parse(body)


def test_all_primary_documents_must_agree_without_mutating_frozen_documents():
    targets = [{"action_id": "a", "stock_code": "600001", "ex_date": "2026-08-03"}]
    docs = [{"document_id": "1", "target_action_ids": ["a"],
             "publisher_security_code": "600001", "publisher_title": "分红派息实施公告",
             "text": BODY}]
    original = deepcopy(docs)
    selected, _ = select_gross_layout_candidates(targets, docs)
    assert set(selected) == {"a"} and docs == original
    docs.append({**docs[0], "document_id": "2", "text": BODY.replace("0.6", "0.7")})
    selected, _ = select_gross_layout_candidates(targets, docs)
    assert not selected


def test_truthful_new_version_keeps_original_terms_and_unknown_announcement_clock():
    from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
        CONTRACT_ID,
        restate_notice_cash,
    )
    from tests.unit.test_base22_notice_restatement import case

    raw, old, terms, _, binding = case()
    raw["stock_code"] = "600001"
    atom = parse_gross_layout_cash(
        BODY.replace("2026年8月3日", "2019年6月3日"), stock_code="600001",
        ex_date=raw["ex_date"], publisher_title="分红派息实施公告",
    )
    binding.update(original_raw_row_sha256=digest(raw), notice_atom_sha256=digest(atom))
    actions, output, proof = restate_notice_cash(
        raw, old, terms, atom, binding=binding,
        observed_at="2026-10-08T00:30:00+00:00",
        revision=CONTRACT_ID + ":sha256:" + "d" * 64,
    )
    assert actions[0] == {**old, "knowledge_to": actions[1]["knowledge_from"]}
    assert output[0] == {**terms[0], "knowledge_to": output[1]["knowledge_from"]}
    assert output[1]["amount_per_share"] == Decimal("0.6")
    assert actions[1]["announced_at"] is None and proof["historical_PIT_admission"] is False
