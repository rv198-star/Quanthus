"""Source row ordinals, wrapped groups and literal denominator components."""

from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22_holder_components_v2 import (
    TABLE,
    component_key,
    load_component_contract,
    project_holder_components,
)
from tests.unit.test_base22_f10_ownership import raw, resolve
from tests.unit.test_base22_holder_components import BODY
from tests.unit.test_base22_holder_components import project as frozen_project


def project(text):
    return project_holder_components(
        raw(text, "股东研究"),
        source_sha256="sha256:" + "a" * 64,
        resolver=resolve(),
        contract=load_component_contract(Path("config/pipelines")),
    )


def test_name_wrap_keeps_exact_component_adjacency_without_fabricating_new_rank():
    text = BODY.replace("合成股东｜", "合成股东长｜", 1).replace(
        "｜｜2.345678万", "｜名称｜｜｜｜｜\n｜｜2.345678万", 1
    )
    rows, proof = project(text)
    assert [r["holder_rank"] for r in rows[TABLE]] == [1, 1, 2]
    assert (
        rows[TABLE][0]["shareholder_source_name"]
        == rows[TABLE][1]["shareholder_source_name"]
        == "合成股东长名称"
    )
    assert rows[TABLE][1]["holding_quantity"] == Decimal("23456.78")
    assert proof["frames"][0]["status"] == "QUALIFIED_COMPLETE_MATRIX"
    _, original = frozen_project(text)
    assert original["frames"][0]["status"] == "HELD_NOT_EMPTY_COVERAGE"


FLOAT = (
    "截至日期：2026-06-30 十大流通股东情况\n"
    "｜股东名称｜持股数(股)｜占流通股比(%)｜股东性质｜增减情况(股)｜\n"
    "｜合成名称长｜1.234567万｜1.1 占A股｜专项账户｜未变｜\n"
    "｜名称｜｜｜｜｜\n"
    "｜｜2.345678万｜2.2 占B股｜专项账户｜-｜\n└"
)


def test_floating_denominator_components_use_literal_units_no_share_nature_inference():
    rows, proof = project(FLOAT)
    values = rows[TABLE]
    assert len(values) == 2 and len({component_key(r) for r in values}) == 2
    assert [r["holding_percentage_unit"] for r in values] == [
        "PERCENT_OF_LITERAL_A_SHARES",
        "PERCENT_OF_LITERAL_B_SHARES",
    ]
    assert all(r["share_nature"] is None and r["holder_category"] == "专项账户" for r in values)
    assert [r["holder_rank"] for r in values] == [1, 1]
    assert proof["consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "change", ["same_basis", "unknown_basis", "classification", "separator", "truncated"]
)
def test_unproved_floating_adjacency_or_basis_is_held(change):
    text = FLOAT
    if change == "same_basis":
        text = text.replace("占B股", "占A股")
    elif change == "unknown_basis":
        text = text.replace(" 占B股", "%")
    elif change == "classification":
        text = text.replace("2.2 占B股｜专项账户", "2.2 占B股｜个人")
    elif change == "separator":
        text = text.replace("｜｜2.345678万", "├\n｜｜2.345678万")
    else:
        text = text.removesuffix("└")
    rows, proof = project(text)
    assert not rows[TABLE]
    assert proof["frames"][0]["status"] == "HELD_NOT_EMPTY_COVERAGE"


def test_source_can_print_twelve_named_rows_without_inferred_top_ten_or_row_loss():
    text = (
        "截至日期：2026-06-30 十大股东情况\n"
        "｜股东名称｜持股数(股)｜占总股本比(%)｜股份性质｜增减情况(股)｜\n"
    )
    text += "\n".join(f"｜合成名称{i}｜1万｜1%｜流通A股｜未变｜" for i in range(12)) + "\n└"
    rows, proof = project(text)
    assert len(rows[TABLE]) == 12
    assert [r["holder_rank"] for r in rows[TABLE]] == list(range(1, 13))
    assert proof["frames"][0]["census"]["source_named_rows_exceed_ten"] is True
    assert not frozen_project(text)[0][TABLE]


def test_classification_wrap_keeps_literal_plus_without_splitting_aggregated_quantity():
    text = BODY.replace("无限售A股｜未变", "限售股+流通A｜未变", 1)
    text = text.replace("｜｜2.345678万｜2.234567890123%｜限售A股｜未变｜", "｜｜｜｜股+H股｜｜")
    rows, _ = project(text)
    assert rows[TABLE][0]["share_nature"] == "限售股+流通A股+H股"
    assert rows[TABLE][0]["holding_quantity"] == Decimal("12345.67")
    assert len(rows[TABLE]) == 2


def test_source_percent_over_one_hundred_is_still_held():
    rows, proof = project(BODY.replace("1.123456789012%", "101%"))
    assert not rows[TABLE]
    assert proof["frames"][0]["reason"] == "HOLDER_COMPONENT_EXACT_PRECISION_OR_RANGE_INVALID"


def test_versioned_named_group_bound_is_enforced_without_losing_excess_rows():
    text = (
        "截至日期：2026-06-30 十大股东情况\n"
        "｜股东名称｜持股数(股)｜占总股本比(%)｜股份性质｜增减情况(股)｜\n"
    )
    text += "\n".join(f"｜合成名称{i}｜1万｜1%｜流通A股｜未变｜" for i in range(65)) + "\n└"
    rows, proof = project(text)
    assert not rows[TABLE]
    assert proof["frames"][0]["reason"] == "HOLDER_COMPONENT_COMPLETE_HEADER_OR_RANK_BOUND_INVALID"
