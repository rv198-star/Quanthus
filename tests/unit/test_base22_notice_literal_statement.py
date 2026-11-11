"""Current company sentences and expressly combined schemes are bounded source claims."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_literal_statement import (
    parse_literal_current_cash,
)

BODY = (
    "证券代码:600001\n一、权益分派方案\n本公司2025年年度权益分派方案为:"
    "以现有股本为基数，向全体股东每10股派1.234567元人民币现金(含税;扣税后另行办理)。"
    "公司于2026年3月董事会审议通过的利润分配方案为每10股派2元人民币现金(含税)。"
    "\n二、日期\n除息日:2026年8月3日"
)


def parse(body=BODY):
    return parse_literal_current_cash(
        body, stock_code="600001", ex_date=date(2026, 8, 3), publisher_title="年度权益分派实施公告"
    )


def test_literal_current_sentence_does_not_select_an_old_board_proposal():
    result = parse()
    assert result["cash_per_share"] == Decimal("0.1234567")
    assert result["aggregate_computed"] is False


def test_expressly_combined_implementation_is_never_the_computed_component_sum():
    body = BODY.replace(
        "本公司2025年年度权益分派方案为:",
        "本公司2024年年度权益分派方案为:每10股派9元人民币现金(含税)。"
        "本公司2024年年度权益分派及2025年中期权益分派合并实施方案为:",
    )
    body = body.replace("每10股派1.234567", "每10股合计派1.234567")
    result = parse(body)
    assert result["cash_per_share"] == Decimal("0.1234567")
    assert result["scopes"][0]["grammar"] == "EXPLICIT_COMBINED_IMPLEMENTED"


@pytest.mark.parametrize(
    "defect",
    [
        "no_end",
        "net",
        "no_unit",
        "foreign",
        "wrong_code",
        "wrong_date",
        "different_current",
        "unqualified_adjustment",
        "precision",
        "nonuniform",
        "no_declaration",
    ],
)
def test_incomplete_ambiguous_or_other_scope_stays_unqualified(defect):
    body = BODY
    if defect == "no_end":
        body = body.replace("。", "")
    elif defect == "net":
        body = body.replace("含税", "税后")
    elif defect == "no_unit":
        body = body.replace("人民币", "")
    elif defect == "foreign":
        body += "\nB股美元另行办理"
    elif defect == "wrong_code":
        body = body.replace("600001", "600002")
    elif defect == "wrong_date":
        body = body.replace("8月3日", "8月4日")
    elif defect == "different_current":
        body = body.replace(
            "\n二、日期", "本公司2025年年度权益分派方案为:每10股派4元人民币现金(含税)。\n二、日期"
        )
    elif defect == "unqualified_adjustment":
        body = body.replace("\n二、日期", "调整后实际每10股派4元人民币现金(含税)。\n二、日期")
    elif defect == "precision":
        body = body.replace("1.234567", "1.23456789012345")
    elif defect == "nonuniform":
        body = body.replace("以现有股本", "差异化分红，以现有股本")
    else:
        body = body.replace("本公司2025年年度权益分派方案为:", "原计划如下:")
    with pytest.raises(BoundedError):
        parse(body)


def test_truthful_new_cash_version_binds_this_profile_and_keeps_original_versions():
    from quanthus.pipelines.securities_warehouse.base22 import digest
    from quanthus.pipelines.securities_warehouse.base22_notice_restatement import (
        CONTRACT_ID,
        restate_notice_cash,
    )
    from tests.unit.test_base22_notice_restatement import case

    raw, old, terms, _, binding = case()
    raw["stock_code"] = "600001"
    atom = parse_literal_current_cash(
        BODY.replace("2026年8月3日", "2019年6月3日"),
        stock_code="600001",
        ex_date=raw["ex_date"],
        publisher_title="年度权益分派实施公告",
    )
    binding.update(original_raw_row_sha256=digest(raw), notice_atom_sha256=digest(atom))
    actions, output, proof = restate_notice_cash(
        raw,
        old,
        terms,
        atom,
        binding=binding,
        observed_at="2026-10-08T00:00:00+00:00",
        revision=CONTRACT_ID + ":sha256:" + "d" * 64,
    )
    assert output[-1]["amount_per_share"] == Decimal("0.1234567")
    assert actions[0] == {**old, "knowledge_to": actions[1]["knowledge_from"]}
    assert proof["historical_PIT_admission"] is False
