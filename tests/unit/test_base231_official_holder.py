from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base231_official_holder import parse_top_matrix


def sample():
    hints = [
        dict(HOLDER_RANK=n, HOLDER_NAME="合成股东" + str(n), HOLD_NUM=str(1000 + n))
        for n in range(1, 11)
    ]
    lines = [
        "单位：股",
        "前10名股东持股情况（不含通过转融通出借股份）",
        "股东名称  股东性质  持股比例  持股数量  有限售股份  股份状态",
    ]
    lines += [f"{r['HOLDER_NAME']}  其他  1.00%  {r['HOLD_NUM']}  0  不适用  0" for r in hints]
    lines += ["上述股东关联关系的说明：未知"]
    return "\n".join(lines), hints


def test_exact_complete_names_ranks_quantities_and_original_percent_tokens():
    body, hints = sample()
    before = deepcopy(hints)
    rows, proof = parse_top_matrix(body, hints)
    assert len(rows) == proof["rows"] == 10
    assert rows[0]["raw_percentage"] == "1.00%"
    assert hints == before
    assert proof["rank_authority"] == "EXPLICIT_PROVIDER_RANK_RECONCILED_TO_COMPLETE_OFFICIAL_ORDER"


def test_wrapped_name_before_and_after_its_numeric_row_is_bound_exactly():
    body, hints = sample()
    hints[0]["HOLDER_NAME"] = "合成股东长名字续行"
    body = body.replace("合成股东1  其他", "合成股东\n长名字  其他", 1).replace(
        "1001  0  不适用  0", "1001  0  不适用  0\n续行", 1
    )
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["name"] == hints[0]["HOLDER_NAME"]


def test_wrapped_nature_does_not_invent_category_or_hide_name_bytes():
    body, hints = sample()
    hints[0]["HOLDER_NAME"] = "合成公司名称"
    body = body.replace(
        "合成股东1  其他  1.00%  1001  0  不适用  0",
        "合成公司  境内非国有法\n名称  人  1.00%  1001  0  不适用  0",
    )
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["name"] == "合成公司名称"
    assert rows[0]["category"] is None
    assert rows[0]["quantity"] == 1001
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("名称  人", "额外名称  人"), hints)


def test_wrapped_nature_after_numeric_row_preserves_complete_name_chain():
    body, hints = sample()
    body = body.replace("合成股东1  其他", "合成股东1  境内非国有法", 1).replace(
        "1001  0  不适用  0", "1001  0  不适用  0\n          人", 1
    )
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["category"] is None


def floating_sample():
    _, hints = sample()
    lines = [
        "单位：股",
        "前10名无限售条件股东持股情况",
        "股东名称  持有无限售条件股份数量  股份种类及数量",
    ]
    lines += [f"{r['HOLDER_NAME']}  {r['HOLD_NUM']}  人民币普通股  {r['HOLD_NUM']}" for r in hints]
    return "\n".join(lines + ["上述股东关联关系的说明"]), hints


def test_complete_floating_table_does_not_infer_undisclosed_percentage():
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = floating_sample()
    rows, proof = parse_floating_matrix(body, hints)
    assert len(rows) == 10
    assert rows[0]["percentage"] is None
    assert rows[0]["share_nature"] == "人民币普通股"
    assert proof["percentage_authority"] == "NOT_DISCLOSED_IN_OFFICIAL_FLOATING_TABLE"


@pytest.mark.parametrize("fault", ["name", "quantity", "shares", "extra", "unit", "rank"])
def test_floating_table_requires_whole_name_and_both_quantity_columns(fault):
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = floating_sample()
    if fault == "name":
        hints[0]["HOLDER_NAME"] = "另一股东"
    elif fault == "quantity":
        body = body.replace("人民币普通股  1001", "人民币普通股  1002", 1)
    elif fault == "shares":
        body = body.replace("人民币普通股", "未审定股份", 1)
    elif fault == "extra":
        body = body.replace("上述股东", "额外股东\n上述股东")
    elif fault == "unit":
        body = body.replace("单位：股", "单位：万股")
    else:
        hints[0]["HOLDER_RANK"] = 2
    with pytest.raises(RuntimeError):
        parse_floating_matrix(body, hints)


@pytest.mark.parametrize(
    "fault", ["quantity", "rank", "missing", "name", "extra", "unit", "nature"]
)
def test_incomplete_or_misbound_matrix_is_held(fault):
    body, hints = sample()
    if fault == "quantity":
        body = body.replace("1001", "1002", 1)
    elif fault == "rank":
        hints[0]["HOLDER_RANK"] = 2
    elif fault == "missing":
        hints.pop()
    elif fault == "name":
        hints[0]["HOLDER_NAME"] = "另一个股东"
    elif fault == "extra":
        body = body.replace("上述股东关联关系", "额外名字\n上述股东关联关系")
    elif fault == "unit":
        body = body.replace("单位：股", "单位：万股")
    else:
        body = body.replace("其他", "未定义类别", 1)
    with pytest.raises(RuntimeError):
        parse_top_matrix(body, hints)


def test_projected_matrix_preserves_official_tokens_and_current_knowledge():
    from datetime import date, datetime, timezone

    from quanthus.pipelines.securities_warehouse.base231_official_holder import project_top_matrix
    from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity

    body, hints = sample()
    body = "证券代码：000400 2025年第三季度报告\n" + body
    parent = FinancialCompanyIdentity(
        "000400", "s", "c", datetime(1990, 1, 1, tzinfo=timezone.utc), None
    )
    args = dict(
        parent=parent,
        business_date=date(2025, 9, 30),
        announcement_id="123",
        official_sha256="sha256:" + "a" * 64,
        hint_sha256="sha256:" + "b" * 64,
        source_known_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        observed_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc),
    )
    rows, proof = project_top_matrix(body, hints, **args)
    assert len(rows) == 10 and rows[0]["knowledge_from"] == args["observed_at"]
    assert rows[0]["raw_holding_percentage"] == "1.00%"
    assert proof["old_values_overwritten"] is False
    with pytest.raises(RuntimeError, match="HEADER_CODE_PERIOD"):
        project_top_matrix(body.replace("000400", "000401"), hints, **args)


def test_native_floating_projection_preserves_unknown_percentage_and_share_class():
    from datetime import date, datetime, timezone

    from quanthus.pipelines.securities_warehouse.base231_official_holder import project_top_matrix
    from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity

    body, hints = floating_sample()
    parent = FinancialCompanyIdentity(
        "000400", "s", "c", datetime(1990, 1, 1, tzinfo=timezone.utc), None
    )
    clock = datetime(2026, 10, 14, tzinfo=timezone.utc)
    rows, _ = project_top_matrix(
        "证券代码：000400 2025年第三季度报告\n" + body,
        hints,
        parent=parent,
        business_date=date(2025, 9, 30),
        announcement_id="123",
        official_sha256="sha256:" + "a" * 64,
        hint_sha256="sha256:" + "b" * 64,
        source_known_at=clock,
        observed_at=clock,
        holding_type="TOP_FLOATING_SHAREHOLDERS",
    )
    assert rows[0]["table_kind"] == "TOP_FLOATING_SHAREHOLDERS"
    assert rows[0]["holding_percentage"] is rows[0]["holding_percentage_unit"] is None
    assert rows[0]["share_nature"] == "人民币普通股"
    assert rows[0]["knowledge_from"] == clock


def test_missing_pdf_code_requires_exact_original_index_company_identity():
    from datetime import date, datetime, timezone

    from quanthus.pipelines.securities_warehouse.base231_official_holder import project_top_matrix
    from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity
    from quanthus.pipelines.securities_warehouse.listed_company import _stable_id

    body, hints = sample()
    body = "2025年第三季度报告\n" + body
    parent = FinancialCompanyIdentity(
        "000400",
        "s",
        _stable_id("company", "CNINFO_ORG_ID", "original-org"),
        datetime(1990, 1, 1, tzinfo=timezone.utc),
        None,
    )
    args = dict(
        parent=parent,
        business_date=date(2025, 9, 30),
        announcement_id="123",
        official_sha256="sha256:" + "a" * 64,
        hint_sha256="sha256:" + "b" * 64,
        source_known_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        observed_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(RuntimeError, match="HEADER_CODE_PERIOD"):
        project_top_matrix(body, hints, **args)
    index = dict(secCode="000400", announcementId="123", orgId="original-org")
    assert len(project_top_matrix(body, hints, original_index=index, **args)[0]) == 10
    index["orgId"] = "another-org"
    with pytest.raises(RuntimeError, match="HEADER_CODE_PERIOD"):
        project_top_matrix(body, hints, original_index=index, **args)


def test_exact_existing_company_identifier_binding_has_dated_and_knowledge_gates():
    from datetime import date, datetime, timezone

    from quanthus.pipelines.securities_warehouse.base231_official_holder import project_top_matrix
    from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity

    body, hints = sample()
    body = "2025年第三季度报告\n" + body
    clock = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    parent = FinancialCompanyIdentity(
        "000400", "s", "legacy-company", datetime(1990, 1, 1, tzinfo=timezone.utc), None
    )
    binding = dict(
        company_id="legacy-company",
        identifier_namespace="CNINFO_ORG_ID",
        identifier_authority="CNINFO",
        identifier_value="original-org",
        effective_from=parent.effective_from,
        effective_to=None,
        knowledge_from=clock,
        knowledge_to=None,
    )
    args = dict(
        parent=parent,
        business_date=date(2025, 9, 30),
        announcement_id="123",
        official_sha256="sha256:" + "a" * 64,
        hint_sha256="sha256:" + "b" * 64,
        source_known_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        observed_at=clock,
        original_index=dict(secCode="000400", announcementId="123", orgId="original-org"),
    )
    assert len(project_top_matrix(body, hints, company_org_bindings=[binding], **args)[0]) == 10
    binding["effective_from"] = clock
    with pytest.raises(RuntimeError, match="HEADER_CODE_PERIOD"):
        project_top_matrix(body, hints, company_org_bindings=[binding], **args)


def quantity_first_sample():
    _, hints = sample()
    lines = [
        "单位：股",
        "前10名股东持股情况（不含通过转融通出借股份）",
        "股东名称  股东性质  持股数量  持股比例（%）  有限售股份  股份状态",
    ]
    lines += [f"{r['HOLDER_NAME']}  其他  {r['HOLD_NUM']}  1.00  0  无  0" for r in hints]
    return "\n".join(lines + ["上述股东关联关系的说明"]), hints


def test_explicit_quantity_first_percentage_header_keeps_original_bare_atom():
    body, hints = quantity_first_sample()
    rows, proof = parse_top_matrix(body, hints)
    assert len(rows) == 10
    assert rows[0]["quantity"] == 1001
    assert rows[0]["percentage"] == 1
    assert rows[0]["raw_percentage"] == "1.00"
    assert (
        proof["percentage_authority"] == "ORIGINAL_OFFICIAL_PERCENT_TOKEN_NOT_PROVIDER_EXTRA_DIGITS"
    )


@pytest.mark.parametrize("fault", ["unit", "header_order", "value_order", "extra_name"])
def test_quantity_first_requires_explicit_header_and_exact_quantity(fault):
    body, hints = quantity_first_sample()
    if fault == "unit":
        body = body.replace("持股比例（%）", "持股比例")
    elif fault == "header_order":
        body = body.replace("持股数量  持股比例（%）", "持股比例（%）  持股数量")
    elif fault == "value_order":
        body = body.replace("1001  1.00", "1.00  1001", 1)
    else:
        body = body.replace("合成股东1  其他", "额外名合成股东1  其他", 1)
    with pytest.raises(RuntimeError):
        parse_top_matrix(body, hints)


def nature_last_sample():
    _, hints = sample()
    lines = [
        "单位：股",
        "前十名股东持股情况（不含通过转融通出借股份）",
        "股东名称  报告期内增减  期末持股数量  比例（%）  有限售股份  股份状态  数量  股东性质",
    ]
    lines += [f"{r['HOLDER_NAME']}  -2  {r['HOLD_NUM']}  1.00  0  无  0  其他" for r in hints]
    return "\n".join(lines + ["上述股东关联关系说明"]), hints


def test_explicit_nature_last_complete_matrix_preserves_name_chain():
    body, hints = nature_last_sample()
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10
    assert rows[0]["raw_percentage"] == "1.00"
    assert rows[0]["category"] == "其他"
    hints[0]["HOLDER_NAME"] = "合成股东一号长名字"
    body = body.replace("合成股东1  -2", "合成股东\n一号长名字  -2")
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["name"] == hints[0]["HOLDER_NAME"]


@pytest.mark.parametrize("fault", ["header", "unit", "quantity", "extra", "nature", "column"])
def test_nature_last_rejects_unqualified_columns_or_incomplete_extent(fault):
    body, hints = nature_last_sample()
    if fault == "header":
        body = body.replace("报告期内增减", "未审核字段")
    elif fault == "unit":
        body = body.replace("比例（%）", "比例")
    elif fault == "quantity":
        body = body.replace("1001", "1002", 1)
    elif fault == "extra":
        body = body.replace("上述股东", "多余股东\n上述股东")
    elif fault == "nature":
        body = body.replace("其他", "未审核类别", 1)
    else:
        body = body.replace("-2  1001  1.00", "1001  -2  1.00", 1)
    with pytest.raises(RuntimeError):
        parse_top_matrix(body, hints)


def test_nature_last_wrapped_header_columns_use_physical_order():
    body, hints = nature_last_sample()
    header = "股东名称  报告期内增减  期末持股数量  比例（%）  有限售股份  股份状态  数量  股东性质"
    body = body.replace(
        header,
        "股东名称                    期末持股数          比例                 股东性质\n"
        "          报告期内增减       量               (%)",
    )
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10
