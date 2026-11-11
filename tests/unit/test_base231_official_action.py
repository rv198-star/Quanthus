from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_official_action import parse_distribution

BODY = """证券代码003018 2026权益分派实施公告
特别提示：除权参考价格每10股转增1.5股
一、原预案每10股转增9股
二、本次实施的利润分配及资本公积金转增股本方案
向全体股东每10股派2.50元人民币现金（含税），每10股转增2.25股，不送红股
三、股权登记日与除权除息日
股权登记日为：2026年7月14日，除权除息日为：2026年7月15日
"""


@pytest.mark.parametrize(
    "wording",
    ["派发2.50元人民币现金", "派发人民币2.50元现金"],
)
def test_szse_plain_distribute_cash_requires_explicit_rmb_and_pretax(wording):
    body = BODY.replace("派2.50元人民币现金", wording)
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.250")
    assert proof["classes"]["cash_per_share"] == "EXACT"
    for invalid in [
        body.replace("人民币", "美元"),
        body.replace("人民币", ""),
        body.replace("含税", "税后"),
        body.replace("三、股权", "每10股派发人民币3元现金（含税）三、股权"),
    ]:
        with pytest.raises(RuntimeError, match="EXPLICIT_TERM"):
            parse_distribution(
                invalid, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
            )


def test_distribution_uses_implemented_section_and_preserves_distinct_adjustment_basis():
    row, proof = parse_distribution(
        BODY, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["transfer_shares"] == Decimal("0.225")
    assert row["cash_per_share"] == Decimal("0.25")
    assert row["bonus_shares"] == 0
    assert row["announce_date"] is None
    assert proof["derived_price_factor_admission"] == "NOT_GRANTED"


def approved_cash_body():
    return (
        BODY.replace(
            "一、原预案每10股转增9股",
            "一、股东会审议通过权益分派情况：公司2026年中期利润分配方案经股东会审议通过，"
            "向全体股东每10股派发现金股利人民币2.50元（含税）。"
            "本次实施的权益分派方案与公司股东会审议通过的分配方案一致。",
        )
        .replace("派2.50元人民币现金", "派发现金股利2.500000元")
        .replace(
            "向全体股东每10股派发现金股利2.500000元",
            "本公司2026年中期方案为：向全体股东每10股派发现金股利2.500000元",
        )
    )


def test_szse_same_current_approved_scheme_can_supply_currency_only():
    row, proof = parse_distribution(
        approved_cash_body(), code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    assert row["transfer_shares"] == Decimal("0.225")
    assert proof["currency_binding_contract"] == "same-current-approved-and-consistent-scheme-v1"
    assert proof["derived_price_factor_admission"] == "NOT_GRANTED"


def report_currency_body():
    return (
        "证券代码003018 2026年半年度报告全文\n第一节 重要提示、目录和释义\n"
        "释义\n元、万元、亿元 指 人民币元、人民币万元、人民币亿元\n"
        "第二节 公司简介和主要财务指标\n一、公司简介\n股票代码003018\n"
        "二、联系人和联系方式\n第四节 公司治理、环境和社会\n"
        "二、本报告期利润分配及资本公积金转增股本情况\n"
        "每10股派息数（元）（含税）2.50\n每10股转增数（股）2.25\n"
        "利润分配或资本公积金转增预案的详细情况说明\n"
        "向全体股东每10股派发现金股利2.50元（含税）。\n"
        "三、公司股权激励计划\n"
    )


def test_complete_physical_page_census_can_split_cash_amount_from_unit():
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元")
    body = body.replace(
        "现金股利2.500000元", "现金股利2.500000\n\n            1/3\n\f元"
    ) + "\n            2/3\n\f其他说明\n            3/3\n\f"
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=report_currency_body(),
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    assert proof["layout_normalization_contract"] == (
        "pdf-complete-page-census-cash-unit-boundary-v1"
    )
    for bad in [
        body.replace("2/3", "1/3"),
        body.replace("3/3", "3/4"),
        body.replace("            1/3\n\f", "            1/3\n"),
        body.replace("            3/3\n\f", ""),
        body.replace("2.500000\n", "2.50\n").replace("\f元", "\f0000元"),
    ]:
        with pytest.raises(RuntimeError, match="EXPLICIT_TERM"):
            parse_distribution(
                bad, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
                currency_scheme_body=report_currency_body(),
            )


@pytest.mark.parametrize("definition", [
    "元、万元、亿元 指 人民币元、人民币万元、人民币亿元",
    "元，万元，亿元 指 人民币元，人民币万元，人民币亿元",
    "元、万元、亿元 指 人民币元、万元、亿元",
    "元、万元、亿元 指 元人民币、万元人民币、亿元人民币",
])
def test_report_own_explicit_currency_definition_and_dividend_table_agree(definition):
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元")
    report = report_currency_body().replace(
        "元、万元、亿元 指 人民币元、人民币万元、人民币亿元", definition
    )
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=report,
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    assert proof["currency_binding_contract"] == (
        "same-issuer-current-approved-scheme-corroboration-v1"
    )
    for invalid in [
        report.replace(definition, "元、万元、亿元 指 美元、万美元、亿美元"),
        report.replace(definition, "附注：元、万元、亿元 人民币元、人民币万元、人民币亿元"),
        report.replace("（含税）2.50", "（含税）3.00"),
        report.replace("股利2.50", "股利3.00"),
        report.replace("释义", "财务报表附注"),
        report.replace("二、本报告期利润分配", "二、上年度利润分配"),
        report.replace("003018", "000001"),
        report.replace("2.50元（含税）", "2.50元（税后）"),
        report.replace("第二节", "元 指 美元\n第二节"),
    ]:
        with pytest.raises(RuntimeError):
            parse_distribution(
                body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
                currency_scheme_body=invalid,
            )


def test_report_identity_uses_its_own_company_table_before_financial_sections():
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元")
    report = report_currency_body().replace("证券代码003018 ", "")
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=report,
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    for invalid in [
        report.replace("股票代码003018", "股票代码000001"),
        report.replace("股票代码003018", "股票代码003018 股票代码000001"),
        report.replace("一、公司简介", "一、子公司简介"),
    ]:
        with pytest.raises(RuntimeError):
            parse_distribution(
                body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
                currency_scheme_body=invalid,
            )


def test_report_explicit_rmb_prose_agrees_with_its_own_current_table():
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元")
    report = report_currency_body().replace("现金股利2.50元", "现金股利人民币2.50元")
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=report,
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    with pytest.raises(RuntimeError):
        parse_distribution(
            body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
            currency_scheme_body=report.replace("人民币", "美元"),
        )


@pytest.mark.parametrize("clause", [
    "每10股派发现金2.50元（含税）",
    "每10股派现金红利2.50元（含税）",
    "每１０股派发现金红利２．５０元（含税）",
    "按10股向全体股东派发现金红利2.50元（含税）",
    "每10.00股派发现金红利2.50元（含税）",
])
def test_report_explicit_cash_clauses_keep_per_ten_units_and_table_agreement(clause):
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元")
    report = report_currency_body().replace("每10股派发现金股利2.50元（含税）", clause)
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=report,
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    for bad in [clause.replace("10股", "20股").replace("１０股", "２０股").replace(
                    "10.00股", "10.01股"),
                clause.replace("含税", "税后")]:
        with pytest.raises(RuntimeError):
            parse_distribution(
                body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
                currency_scheme_body=report.replace(clause, bad),
            )


def test_explicit_current_year_midyear_wording_never_admits_other_year():
    body = approved_cash_body().replace("2026年中期", "2026年度中期")
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=report_currency_body(),
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    with pytest.raises(RuntimeError):
        parse_distribution(
            body.replace("2026年度中期", "2025年度中期"), code="003018", venue="SZSE",
            expected_ex_date=date(2026, 7, 15), currency_scheme_body=report_currency_body(),
        )


def test_current_implemented_section_can_bind_period_to_exact_cover_title():
    body = approved_cash_body().replace("2026权益分派实施公告", "2026年半年度权益分派实施公告")
    body = body.replace("本公司2026年中期方案为：", "本公司方案为：")
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    assert proof["current_approved_scheme_sha256"]
    for invalid in [
        body.replace("2026年半年度权益分派实施公告", "2025年半年度权益分派实施公告"),
        body.replace("2026年半年度权益分派实施公告", "2026年年度权益分派实施公告"),
        body.replace("本公司方案为：", "本公司2025年年度方案为："),
        body.replace("2026年半年度权益分派实施公告", "2026年半年度权益分派实施公告拟稿"),
    ]:
        with pytest.raises(RuntimeError, match="EXPLICIT_TERM"):
            parse_distribution(
                invalid, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
            )


@pytest.mark.parametrize("clause", [
    "每10股派发现金人民币2.50（含税）",
    "每10股派发现\n\n                          1\n\f金股利2.50元人民币（含税）",
])
def test_current_approved_scheme_explicit_currency_atom_and_exact_page_footer(clause):
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元")
    scheme = "证券代码003018 2026年半年度利润分配预案：" + clause
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=scheme,
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    assert proof["currency_binding_contract"] == (
        "same-issuer-current-approved-scheme-corroboration-v1"
    )
    for invalid in [
        scheme.replace("人民币", "美元"),
        scheme.replace("2.50", "2.60"),
        scheme.replace("含税", "税后"),
        scheme.replace("每10股", "合计"),
        scheme.replace("003018", "003019"),
    ]:
        with pytest.raises(RuntimeError):
            parse_distribution(
                body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
                currency_scheme_body=invalid,
            )
    if "\f" in scheme:
        for invalid in [scheme.replace("\f", ""), scheme.replace("1\n\f", "11 12\n\f")]:
            with pytest.raises(RuntimeError):
                parse_distribution(
                    body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
                    currency_scheme_body=invalid,
                )


@pytest.mark.parametrize(
    "fault",
    [
        "not_approved",
        "wrong_year",
        "wrong_period",
        "no_consistency",
        "changed",
        "wrong_value",
        "currency",
        "tax",
        "total_only",
        "foreign_class",
        "other_section",
    ],
)
def test_approved_currency_requires_same_event_explicit_value_and_implementation(fault):
    body = approved_cash_body()
    if fault == "not_approved":
        body = body.replace("审议通过", "待审议")
    elif fault == "wrong_year":
        body = body.replace("2026年中期", "2025年中期", 1)
    elif fault == "wrong_period":
        body = body.replace("2026年中期", "2026年年度", 1)
    elif fault == "no_consistency":
        body = body.replace("本次实施的权益分派方案与公司股东会审议通过的分配方案一致。", "")
    elif fault == "changed":
        body = body.replace("方案一致", "方案不一致")
    elif fault == "wrong_value":
        body = body.replace("人民币2.50", "人民币2.60")
    elif fault == "currency":
        body = body.replace("人民币", "美元")
    elif fault == "tax":
        body = body.replace("人民币2.50元（含税）", "人民币2.50元（扣税后）")
    elif fault == "total_only":
        body = body.replace("每10股派发现金股利人民币2.50元", "合计派发现金股利人民币2.50元")
    elif fault == "foreign_class":
        body = body.replace(
            "向全体股东每10股派发现金股利人民币", "向B股股东每10股派发现金股利人民币"
        )
    else:
        body = body.replace(
            "一、股东会",
            "一、公司历史方案：2025年年度每10股派发现金股利人民币2.50元（含税）。二点、股东会",
        )
        body = body.replace("公司2026年中期利润分配方案", "公司利润分配方案")
    with pytest.raises(RuntimeError, match="EXPLICIT_TERM"):
        parse_distribution(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))


def test_corresponding_official_scheme_supplies_currency_without_editing_implementation():
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元")
    scheme = "证券代码003018 2026年半年度利润分配方案：每10股派发现金股利人民币2.50元（含税）"
    row, proof = parse_distribution(
        body,
        code="003018",
        venue="SZSE",
        expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=scheme,
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    assert proof["currency_binding_contract"] == (
        "same-issuer-current-approved-scheme-corroboration-v1"
    )
    with pytest.raises(RuntimeError):
        parse_distribution(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))
    for invalid in [
        scheme.replace("003018", "003019"),
        scheme.replace("2026年", "2025年"),
        scheme.replace("人民币", "美元"),
        scheme.replace("2.50元", "2.51元"),
        scheme.replace("含税", "扣税后"),
    ]:
        with pytest.raises(RuntimeError):
            parse_distribution(
                body,
                code="003018",
                venue="SZSE",
                expected_ex_date=date(2026, 7, 15),
                currency_scheme_body=invalid,
            )


@pytest.mark.parametrize("prefix", ["派1.0元人民币", "派发现金股利人民币1.00元"])
def test_szse_currency_can_bind_to_matching_current_implementation_summary(prefix):
    body = BODY.replace(
        "特别提示：除权参考价格每10股转增1.5股",
        "特别提示：本次权益分派向全体股东每10股" + prefix + "（含税）。",
    ).replace("派2.50元人民币现金", "派发现金股利1.000000元")
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.1000000")
    assert proof["currency_binding_contract"] == "same-current-implementation-summary-v1"
    for invalid in [
        body.replace(prefix, prefix.replace("人民币", "美元")),
        body.replace("特别提示：本次权益分派", "特别提示：拟进行权益分派"),
        body.replace("特别提示：本次权益分派", "特别提示：上次权益分派"),
        body.replace(prefix + "（含税）", prefix + "（扣税后）"),
        body.replace("派发现金股利1.000000元", "派发现金股利2.000000元"),
        body.replace("一、原预案", "每10股派发现金股利人民币9元（含税）。一、原预案"),
    ]:
        with pytest.raises(RuntimeError, match="EXPLICIT_TERM"):
            parse_distribution(
                invalid, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
            )


@pytest.mark.parametrize("fault", ["section", "tax", "date", "conflict", "code", "precision"])
def test_unsupported_official_body_is_not_a_partial_success(fault):
    body = BODY
    if fault == "section":
        body = body.replace("二、本次实施", "二、拟实施")
    elif fault == "tax":
        body = body.replace("现金（含税", "现金（扣税后")
    elif fault == "date":
        body = body.replace("除权除息日为：2026年7月15日", "除权除息日为：2026年7月16日")
    elif fault == "conflict":
        body = body.replace("不送红股", "每10股转增3股，不送红股")
    elif fault == "code":
        body = body.replace("003018", "003019")
    else:
        body = body.replace("2.25股", "2.12345678901股")
    with pytest.raises(RuntimeError):
        parse_distribution(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))


def test_sse_per_share_terms_are_not_divided_by_ten_again():
    body = """688115上海证券交易所年度权益分派实施公告
    每股分配比例每股现金红利0.067元 每股派送红股0.4股 相关日期
    股权登记日 除权（息）日 现金红利发放日 新股上市日
    2026/7/14 2026/7/15 2026/7/15 2026/7/15 一、股东会
    """
    row, _ = parse_distribution(
        body, code="688115", venue="SSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.067")
    assert row["bonus_shares"] == Decimal("0.4")


def test_per_ten_conversion_keeps_digits_beyond_ambient_precision():
    from quanthus.pipelines.securities_warehouse.base231_official_action import _per_ten

    value = _per_ten("1234567890123456789012345678.123456789")
    assert value == Decimal("123456789012345678901234567.8123456789")


def test_materialization_has_actual_new_knowledge_and_date_only_publication():
    from quanthus.pipelines.securities_warehouse.base231_official_action import (
        materialize_distribution,
    )

    row, _ = parse_distribution(
        BODY, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    args = dict(
        announcement_id="doc",
        observed_at=now,
        pdf_received_at=now,
        pdf_sha256="sha256:" + "a" * 64,
        company_id="company",
        security_id="security",
        vocabulary={
            "action_types": {"mixed": "MIXED"},
            "action_statuses": {"实施": "IMPLEMENTED"},
            "term_types": {
                "cash_per_share": "CASH_AMOUNT_PER_SHARE",
                "bonus_shares": "BONUS_SHARES_PER_SHARE",
                "transfer_shares": "TRANSFER_SHARES_PER_SHARE",
            },
        },
    )
    output = materialize_distribution(row, **args)
    assert output["dwd_corporate_action"][0]["knowledge_from"] == now
    assert output["dwd_corporate_action"][0]["announced_at"] is None
    assert len(output["dwd_corporate_action_term"]) == 2


def test_cash_only_szse_does_not_require_invented_transfer_or_bonus():
    body = """证券代码003018 2026年半年度权益分派实施公告
    一、股东会原预案每10股转增9股，不送红股，不以公积金转增股本
    二、本次权益分派方案
    向全体股东每10股派3元人民币现金【含税；扣税后每10股派2.7元】
    三、股权登记日及除权除息日
    股权登记日：2026年9月29日；除权日（除息日）：2026年9月30日
    """
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 9, 30)
    )
    assert row["cash_per_share"] == Decimal("0.3")
    assert row["transfer_shares"] is None
    assert row["bonus_shares"] is None
    assert proof["classes"]["transfer_shares"] == "NULL"


@pytest.mark.parametrize("wording", ["派", "派发现金红利", "派现金红利", "派发现金股利"])
def test_szse_explicit_pretax_cash_wordings(wording):
    body = BODY.replace("派2.50元人民币现金（含税", wording + "2.50元人民币现金（含税")
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.25")


def test_szse_cash_tax_disagreement_and_ab_date_ambiguity_remain_held():
    for extra in [
        "每10股派3元人民币现金（含税）",
        "每10股转增3股",
    ]:
        body = BODY.replace("三、股权", extra + "三、股权")
        with pytest.raises(RuntimeError):
            parse_distribution(
                body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
            )
    body = BODY + "B股股权登记日为：2026年7月17日"
    with pytest.raises(RuntimeError):
        parse_distribution(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))


def test_szse_disclosed_non_distribution_is_zero_only_inside_implemented_section():
    body = BODY.replace("每10股转增2.25股，不送红股", "不送红股，不以资本公积金转增股本")
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["bonus_shares"] == row["transfer_shares"] == 0


def test_szse_current_year_profit_heading_is_an_implementation_scope():
    body = BODY.replace(
        "二、本次实施的利润分配及资本公积金转增股本方案",
        "二、本次实施的2026年中期利润分配方案",
    )
    assert parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )[0]["cash_per_share"] == Decimal("0.25")


def test_szse_repeated_dates_compare_business_dates_not_zero_padding():
    from quanthus.pipelines.securities_warehouse.base231_official_action import (
        szse_distribution_dates,
    )

    body = BODY + "股权登记日为：2026年07月14日，除权除息日为：2026年07月15日"
    assert szse_distribution_dates(body) == (date(2026, 7, 14), date(2026, 7, 15))
    with pytest.raises(RuntimeError, match="DATE_LABEL"):
        szse_distribution_dates(body + "除权除息日为：2026年07月16日")


SSE_CURRENT = """600001 上海证券交易所2026年半年度权益分派实施公告
二、分配方案
以实施前总股本为基数，每股派发现金红利0.02元（含税）。
三、相关日期
股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日
Ａ股 2026/9/28 － 2026/9/29 2026/9/29
四、分配实施办法
"""


def test_sse_explicit_single_class_dates_without_class_column():
    from quanthus.pipelines.securities_warehouse.base231_official_action import (
        sse_distribution_dates,
    )

    body = "三、相关日期 股权登记日 除权（息）日 现金红利发放日 2026/9/30 2026/10/8 2026/10/8 四、"
    assert sse_distribution_dates(body) == (date(2026, 9, 30), date(2026, 10, 8))
    for invalid in [
        body.replace("现金红利发放日", "现金红利发放日 B股"),
        body.replace("四、", "2026/10/9 四、"),
        body.replace("股权登记日 除权（息）日", "除权（息）日 股权登记日"),
    ]:
        with pytest.raises(RuntimeError, match="DATE_LABEL"):
            sse_distribution_dates(invalid)


def test_sse_wrapped_header_keeps_the_explicit_single_a_share_date_row():
    body = SSE_CURRENT.replace(
        "股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日",
        "                               除权（息）  现金红利发\n"
        "股份类别 股权登记日 最后交易日\n"
        "                                  日         放日",
    )
    row, _ = parse_distribution(
        body, code="600001", venue="SSE", expected_ex_date=date(2026, 9, 29)
    )
    assert row["record_date"] == date(2026, 9, 28)
    for invalid in [
        body.replace("Ａ股", "Ｂ股"),
        body.replace("四、", "Ｂ股 2026/10/9 － 2026/10/12 2026/10/12 四、"),
        body.replace("股权登记日 最后交易日", "最后交易日 股权登记日"),
    ]:
        with pytest.raises(RuntimeError, match="DATE_LABEL"):
            parse_distribution(
                invalid, code="600001", venue="SSE", expected_ex_date=date(2026, 9, 29)
            )


def test_sse_cash_first_wrapped_header_keeps_single_a_share_dates():
    body = SSE_CURRENT.replace(
        "股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日",
        "现金红利发放\n股份类别 股权登记日 最后交易日 除权（息）日\n日",
    )
    row, _ = parse_distribution(
        body, code="600001", venue="SSE", expected_ex_date=date(2026, 9, 29)
    )
    assert row["record_date"] == date(2026, 9, 28)
    assert row["cash_per_share"].as_tuple() == Decimal("0.02").as_tuple()
    for invalid in [
        body.replace("Ａ股", "Ｂ股"),
        body.replace("四、", "Ｂ股 2026/9/28 － 2026/9/29 2026/9/29 四、"),
        body.replace("股权登记日 最后交易日", "最后交易日 股权登记日"),
        body.replace("四、", "2026/9/30 四、"),
        body.replace("现金红利发放\n", "现金红利发放日\n"),
    ]:
        with pytest.raises(RuntimeError, match="DATE_LABEL"):
            parse_distribution(
                invalid, code="600001", venue="SSE", expected_ex_date=date(2026, 9, 29)
            )


SSE_TRANSFER = """688808 上海证券交易所2026年半年度资本公积金转增股本实施公告
二、转增股本方案
本次转增股本以方案实施前的公司总股本为基数，
以资本公积金向全体股东每股转增0.25股。
相关日期
                  新增无限售条件流通股份
股权登记日 除权（息）日
                         上市日
2026/9/28 2026/9/29 2026/9/29
三、转增股本实施办法
"""


def test_sse_transfer_only_keeps_undisclosed_cash_and_bonus_unknown():
    row, proof = parse_distribution(
        SSE_TRANSFER, code="688808", venue="SSE", expected_ex_date=date(2026, 9, 29)
    )
    assert row["transfer_shares"] == Decimal("0.25")
    assert row["cash_per_share"] is row["bonus_shares"] is None
    assert row["action_type"] == "transfer"
    assert proof["classes"]["cash_per_share"] == "NULL"


@pytest.mark.parametrize(
    "fault", ["proposal", "ratio", "extra_date", "foreign_class", "date", "cash", "bonus"]
)
def test_sse_transfer_only_requires_exact_implemented_ratio_and_date_table(fault):
    body = SSE_TRANSFER
    if fault == "proposal":
        body = body.replace("二、转增股本方案", "二、拟转增股本方案")
    elif fault == "ratio":
        body = body.replace("相关日期", "以资本公积金向全体股东每股转增0.5股。相关日期")
    elif fault == "extra_date":
        body = body.replace("三、", "2026/9/30 三、")
    elif fault == "foreign_class":
        body = body.replace("相关日期", "相关日期 B股")
    elif fault == "cash":
        body = body.replace("相关日期", "每股派发现金红利人民币0.1元（含税）。相关日期")
    elif fault == "bonus":
        body = body.replace("相关日期", "每股派送红股0.2股。相关日期")
    else:
        body = body.replace("2026/9/29", "2026/10/9")
    with pytest.raises(RuntimeError):
        parse_distribution(body, code="688808", venue="SSE", expected_ex_date=date(2026, 9, 29))


def test_sse_current_implementation_table_binds_per_share_cash_and_exact_dates():
    row, _ = parse_distribution(
        SSE_CURRENT, code="600001", venue="SSE", expected_ex_date=date(2026, 9, 29)
    )
    assert row["record_date"] == date(2026, 9, 28)
    assert row["cash_per_share"] == Decimal("0.02")
    assert row["transfer_shares"] is row["bonus_shares"] is None


@pytest.mark.parametrize("fault", ["two_classes", "pretax", "proposal", "cash_conflict", "date"])
def test_sse_current_table_conflicts_do_not_become_success(fault):
    body = SSE_CURRENT
    if fault == "two_classes":
        body = body.replace("四、", "Ｂ股 2026/10/8 2026/9/28 2026/10/20 四、")
    elif fault == "pretax":
        body = body.replace("含税", "扣税后")
    elif fault == "proposal":
        body = body.replace("二、分配方案", "二、拟定方案")
    elif fault == "cash_conflict":
        body = body.replace("三、", "每股派发现金红利0.03元（含税）。三、")
    else:
        body = body.replace("2026/9/29", "2026/10/8")
    with pytest.raises(RuntimeError):
        parse_distribution(body, code="600001", venue="SSE", expected_ex_date=date(2026, 9, 29))


@pytest.mark.parametrize(
    "wording", ["派发现金红利人民币", "派发现金股利人民币", "派现金红利人民币"]
)
def test_szse_explicit_rmb_before_amount_preserves_original_decimal(wording):
    body = BODY.replace("派2.50元人民币现金", wording + "2.50元")
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.250")
    with pytest.raises(RuntimeError):
        parse_distribution(
            body.replace("人民币", "美元"),
            code="003018",
            venue="SZSE",
            expected_ex_date=date(2026, 7, 15),
        )
    with pytest.raises(RuntimeError):
        parse_distribution(
            body.replace("人民币", ""),
            code="003018",
            venue="SZSE",
            expected_ex_date=date(2026, 7, 15),
        )


def test_explicit_rmb_word_order_does_not_hide_conflicting_implemented_amount():
    body = BODY.replace("三、股权", "每10股派发现金红利人民币3元（含税）三、股权")
    with pytest.raises(RuntimeError, match="EXPLICIT_TERM"):
        parse_distribution(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))


def test_sse_current_explicit_rmb_prefix_uses_per_share_amount():
    body = SSE_CURRENT.replace("每股派发现金红利", "每股派发现金红利人民币")
    row, _ = parse_distribution(
        body, code="600001", venue="SSE", expected_ex_date=date(2026, 9, 29)
    )
    assert row["cash_per_share"] == Decimal("0.02")


def test_legacy_sse_explicit_rmb_prefix_does_not_change_per_share_scale():
    body = """688115上海证券交易所年度权益分派实施公告
    每股分配比例A股每股现金红利人民币0.067元（含税） 相关日期
    股权登记日 除权（息）日 现金红利发放日
    2026/7/14 2026/7/15 2026/7/15 一、股东会
    """
    row, _ = parse_distribution(
        body, code="688115", venue="SSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.067")
    with pytest.raises(RuntimeError):
        parse_distribution(
            body.replace("人民币", "美元"),
            code="688115",
            venue="SSE",
            expected_ex_date=date(2026, 7, 15),
        )


def test_physical_pdf_page_footer_can_split_explicit_currency_word():
    from quanthus.pipelines.securities_warehouse.base22 import digest

    body = BODY.replace("元人民币现金", "元人民币\n\n\n            1\n\f现金")
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.25")
    assert proof["source_body_sha256"] == digest(body)
    assert proof["layout_normalization_contract"] == "pdf-explicit-rmb-cash-page-footer-v1"


@pytest.mark.parametrize(
    "separator", ["\n1\n\f", "\n            1\n", "1", "\n            12345\n\f"]
)
def test_unproven_numeric_fragments_are_not_silently_removed(separator):
    body = BODY.replace("元人民币现金", "元人民币" + separator + "现金")
    with pytest.raises(RuntimeError):
        parse_distribution(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))


@pytest.mark.parametrize(
    "heading", ["二、本次实施的权益分配方案", "二、本次权益分派实施方案", "二、A股权益分派方案"]
)
@pytest.mark.parametrize(
    "date_heading", ["三、分红派息日", "三、权益分派日期", "三、A股分红派息日期"]
)
def test_exact_implemented_section_heading_variants(heading, date_heading):
    body = BODY.replace("二、本次实施的利润分配及资本公积金转增股本方案", heading)
    body = body.replace("三、股权登记日与除权除息日", date_heading)
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.25")
    with pytest.raises(RuntimeError):
        parse_distribution(
            body.replace("人民币", "美元"),
            code="003018",
            venue="SZSE",
            expected_ex_date=date(2026, 7, 15),
        )


def test_page_footer_rule_never_removes_a_fragment_inside_numeric_amount():
    body = BODY.replace("2.50元", "2.\n            5\n\f50元")
    row, proof = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.2550")
    assert "layout_normalization_contract" not in proof


@pytest.mark.parametrize("heading,consistent", [
    ("利润分配方案审议等情况",
     "本次实施的分配方案与公司董事会审议通过的利润分配方案及其调整原则是一致的"),
    ("股东会审议通过权益分派情况",
     "本次权益分派方案内容与股东会审议通过的2026年半年度利润分配方案及其调整原则一致"),
    ("股东会审议通过权益分派情况",
     "本次实施的权益分派方案与股东会审议通过的方案及调整原则一致"),
])
def test_exact_current_approved_consistency_wording_keeps_report_currency_checks(
    heading, consistent
):
    body = approved_cash_body().replace("现金股利人民币2.50元", "现金股利2.50元").replace(
        "股东会审议通过权益分派情况", heading
    ).replace("本次实施的权益分派方案与公司股东会审议通过的分配方案一致", consistent)
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        currency_scheme_body=report_currency_body(),
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    with pytest.raises(RuntimeError):
        parse_distribution(
            body.replace("一致", "存在差异"), code="003018", venue="SZSE",
            expected_ex_date=date(2026, 7, 15), currency_scheme_body=report_currency_body(),
        )


def test_exact_implemented_halfyear_section_without_repeated_heading_scheme_word():
    body = approved_cash_body().replace(
        "二、本次实施的利润分配及资本公积金转增股本方案", "二、本次实施的利润分配"
    ).replace("本公司2026年中期方案为", "本次实施的2026年半年度权益分配方案为")
    row, _ = parse_distribution(
        body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"].as_tuple() == Decimal("0.2500000").as_tuple()
    with pytest.raises(RuntimeError):
        parse_distribution(
            body.replace("本次实施的2026年半年度权益分配方案为", "拟定2026年半年度权益分配方案为"),
            code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15),
        )
