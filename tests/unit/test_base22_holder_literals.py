"""Explicit source percentage bases survive typed serialization unchanged."""

from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_literals import holder_observation
from tests.unit.test_base22_f10_ownership import HOLDERS, project, raw, resolve


def literal(body):
    return holder_observation(
        raw(body, "股东研究"), source_sha256="sha256:" + "a" * 64, resolver=resolve()
    )


@pytest.mark.parametrize("token", ["1.123456789012 占A股", "1.123456789012% 占A股"])
def test_literal_A_share_denominator_is_preserved_with_exact_source_numeral(token):
    body = HOLDERS.replace("1.123456789012%", token)
    with pytest.raises(BoundedError, match="UNIT_OR_SCALAR"):
        project(raw(body, "股东研究"))
    rows, proof = literal(body)
    row = rows["dwd_shareholding_observation"][0]
    assert row["holding_percentage"] == Decimal("1.123456789012")
    assert row["raw_holding_percentage"] == token
    assert proof["percentage_basis_counts"] == {"LITERAL_A_SHARE_DENOMINATOR": 1}
    assert proof["denominators_converted_or_erased"] is False
    assert proof["whole_category_and_history_union_admission"] == "SEPARATE_GATE"


@pytest.mark.parametrize(
    "token",
    [
        "1 占B股",
        "1 占H股",
        "1 占不明股份",
        "101 占A股",
        "NaN 占A股",
        "-1 占A股",
        "1.0000000000001 占A股",
    ],
)
def test_unknown_denominator_invalid_scalar_and_precision_stay_blocked(token):
    with pytest.raises(BoundedError, match="HOLDER_LITERAL"):
        literal(HOLDERS.replace("1.123456789012%", token))


@pytest.mark.parametrize(
    "header",
    [
        "｜日期｜股东名称｜持股数｜变动股数｜变动后｜变动比例｜备注｜",
        "｜变动日期｜股东名称｜持股数｜变动股数｜变动后｜变动后占｜变动类型｜",
    ],
)
def test_exact_change_table_header_has_separate_unqualified_domain_accounting(header):
    other = header + "\n｜2026-08-01｜合成法人｜1万｜-1万｜0｜0%｜变动｜\n└"
    body = HOLDERS.removesuffix("└") + other
    with pytest.raises(BoundedError, match="UNACCOUNTED"):
        project(raw(body, "股东研究"))
    rows, proof = literal(body)
    assert len(rows["dwd_shareholding_observation"]) == 1
    assert proof["separate_nonempty_domains"][0]["table"] == "dwd_shareholding_change"
    assert proof["separate_nonempty_domains"][0]["qualification"] == "NOT_GRANTED"
    assert proof["separate_nonempty_domains"][0]["nonempty_data_rows"] == 1


def test_an_unknown_header_inside_a_holder_matrix_cannot_hide_missing_cells():
    body = HOLDERS.removesuffix("└") + "｜日期｜未知列｜不明数据｜另一列｜\n└"
    with pytest.raises(BoundedError, match="HOLDER_LITERAL"):
        literal(body)


def test_a_partial_change_header_with_unknown_columns_does_not_close_the_matrix():
    body = (
        HOLDERS.removesuffix("└") + "｜日期｜股东名称｜持股数｜变动股数｜变动后｜未知列｜其他｜\n└"
    )
    with pytest.raises(BoundedError, match="HOLDER_LITERAL"):
        literal(body)


def test_original_provider_content_and_acquisition_stay_mandatory():
    for defect in ["content_sha256", "acquired_at"]:
        source = raw(HOLDERS, "股东研究")
        source[defect] = None
        with pytest.raises(BoundedError, match="HOLDER_LITERAL"):
            holder_observation(source, source_sha256="sha256:" + "a" * 64, resolver=resolve())


@pytest.mark.parametrize("token", ["", "-"])
def test_explicit_missing_percentage_stays_null_without_becoming_zero(token):
    rows, proof = literal(HOLDERS.replace("1.123456789012%", token))
    row = rows["dwd_shareholding_observation"][0]
    assert row["holding_percentage"] is None
    assert row["raw_holding_percentage"] == token
    assert proof["percentage_basis_counts"] == {"EXPLICIT_NULL": 1}


@pytest.mark.parametrize("border", ["｜续名｜｜", "｜续名｜｜｜"])
def test_empty_extra_borders_in_a_name_continuation_preserve_the_complete_name(border):
    body = HOLDERS.replace("└", border + "\n└")
    with pytest.raises(BoundedError, match="UNACCOUNTED"):
        project(raw(body, "股东研究"))
    rows, _ = literal(body)
    row = rows["dwd_shareholding_observation"][0]
    assert row["shareholder_source_name"] == "合成股东续名"
    assert row["holding_quantity"] == Decimal("12345.67")
    assert row["holding_percentage"] == Decimal("1.123456789012")


def test_an_orphan_continuation_and_a_real_cell_never_become_empty_border_layout():
    for line in ["｜孤立名称｜｜", "｜另一个股东｜｜1%｜"]:
        body = HOLDERS.replace("｜合成股东｜", line + "\n｜合成股东｜")
        with pytest.raises(BoundedError, match="HOLDER_LITERAL"):
            literal(body)
