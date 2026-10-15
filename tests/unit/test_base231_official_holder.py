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


def split_numeric_sample():
    body, hints = sample()
    hints[0]["HOLDER_NAME"] = "合成公司全称"
    body = body.replace(
        "合成股东1  其他  1.00%  1001  0  不适用  0",
        "合成公司  境内非国有法\n                    1.00%  1001  0  不适用  0\n全称         人",
    )
    body = body.replace("合成股东2", "合成公司2025年第三季度报告\n合成股东2", 1)
    return body, hints


def test_explicit_column_order_binds_split_nature_and_numeric_row_without_inventing_category():
    body, hints = split_numeric_sample()
    rows, proof = parse_top_matrix(body, hints)
    assert len(rows) == proof["rows"] == 10
    assert rows[0]["name"] == "合成公司全称"
    assert rows[0]["quantity"] == 1001
    assert rows[0]["raw_percentage"] == "1.00%"
    assert rows[0]["category"] is None


@pytest.mark.parametrize("fault", ["order", "header", "quantity", "name", "extent"])
def test_split_numeric_rows_do_not_bypass_explicit_header_or_full_frame(fault):
    body, hints = split_numeric_sample()
    if fault == "order":
        body = body.replace("持股比例  持股数量", "持股数量  持股比例")
    elif fault == "header":
        body = body.replace("持股比例  持股数量", "未确认列甲  未确认列乙")
    elif fault == "quantity":
        body = body.replace("1.00%  1001", "1.00%  1002", 1)
    elif fault == "name":
        body = body.replace("全称         人", "多余全称         人")
    else:
        body = body.replace("                    1.00%  1001  0  不适用  0", "")
    with pytest.raises(RuntimeError):
        parse_top_matrix(body, hints)


@pytest.mark.parametrize("wrapped", [False, True])
def test_top_matrix_ends_at_explicit_strategic_investor_note_section(wrapped):
    body, hints = sample()
    body = body.replace(
        "上述股东关联关系的说明：未知", "战略投资者或一般法人因配售新股成为前10名股东的情况\n不适用"
    )
    if wrapped:
        body = body.replace("一般法人因配售", "一般法人因\n配售")
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("战略投资者或", "额外未说明股东\n战略投资者或"), hints)


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


@pytest.mark.parametrize("wrapped", [False, True])
def test_floating_matrix_ends_at_explicit_cross_holder_relation_note(wrapped):
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = floating_sample()
    body = body.replace(
        "上述股东关联关系的说明",
        "前10名无限售流通股股东之间，以及前10名无限售流通股股东和前10名股东之间的关联关系\n未知",
    )
    if wrapped:
        body = body.replace("前10名无限售流通股", "前10名无限售流通\n股")
    rows, _ = parse_floating_matrix(body, hints)
    assert len(rows) == 10
    with pytest.raises(RuntimeError):
        parse_floating_matrix(
            body.replace("前10名无限售流通", "额外未说明股东\n前10名无限售流通", 1),
            hints,
        )


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


def test_wrapped_name_left_cell_is_not_discarded_for_right_hand_pledge():
    body, hints = split_numeric_sample()
    body = body.replace(
        "合成公司  境内非国有法", "合成公司  境内非国有法                  质押  1001"
    )
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["name"] == "合成公司全称"
    assert rows[0]["quantity"] == 1001
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("合成公司  境内", "额外合成公司  境内"), hints)


def test_quantity_first_header_is_ordered_by_physical_columns_across_lines():
    body, hints = quantity_first_sample()
    body = body.replace(
        "股东名称  股东性质  持股数量  持股比例（%）  有限售股份  股份状态",
        "                                    持股比\n"
        "股东名称      股东性质  持股数量\n"
        "                                    例(%)",
    )
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10
    assert rows[0]["quantity"] == 1001
    assert rows[0]["raw_percentage"] == "1.00"
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("例(%)", "例"), hints)


def test_split_quantity_first_uses_explicit_header_without_guessing_nature():
    body, hints = quantity_first_sample()
    body = body.replace(
        "合成股东1  其他  1001  1.00", "合成股东1\n                    1001  1.00", 1
    )
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["quantity"] == 1001
    assert rows[0]["category"] is None
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("1001  1.00", "1002  1.00", 1), hints)


def test_wrapped_strategic_note_ends_before_its_continuation_rows():
    body, hints = sample()
    body = body.replace(
        "上述股东关联关系的说明：未知",
        "战略投资者或一般法人\n因配售新股成为前10名\n股东的情况（如有）\n不适用\n上述股东关联关系的说明：未知",
    )
    assert len(parse_top_matrix(body, hints)[0]) == 10


def test_explicit_unknown_nature_is_retained_as_source_unknown():
    body, hints = sample()
    body = body.replace("合成股东1  其他", "合成股东1  未知", 1)
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["category"] == "未知"


def test_share_unit_can_precede_wrapped_shareholder_census_but_nearest_unit_wins():
    body, hints = sample()
    body = body.replace("单位：股\n", "单位：股\n" + "股东总数说明\n" * 20)
    assert len(parse_top_matrix(body, hints)[0]) == 10
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("前10名股东持股", "单位：万元\n前10名股东持股"), hints)
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("单位：股", "单位：份"), hints)


def test_nature_last_wrapped_name_cell_does_not_absorb_right_hand_change_value():
    body, hints = nature_last_sample()
    body = body.replace("股东名称  报告期内增减", "股东名称         报告期内增减")
    hints[0]["HOLDER_NAME"] = "合成股东四号名称续名"
    body = body.replace("合成股东1  -2", "合成股东四号名称 -7\n续名  -2", 1)
    assert parse_top_matrix(body, hints)[0][0]["name"] == hints[0]["HOLDER_NAME"]
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("四号名称 -7", "错误名称 -7"), hints)


def test_nearest_unsupported_source_unit_is_not_skipped_for_an_older_share_unit():
    body, hints = sample()
    body = body.replace("单位：股", "单位：股\n单位：份")
    with pytest.raises(RuntimeError, match="SHARE_UNIT"):
        parse_top_matrix(body, hints)


def test_existing_strategic_heading_ends_even_with_interposed_right_hand_note():
    body, hints = sample()
    body = body.replace(
        "上述股东关联关系的说明：未知",
        "战略投资者或一般法人因配售新股成为前 10\n"
        "                         无。\n"
        "名股东的情况（如有）\n上述股东关联关系的说明：未知",
    )
    assert len(parse_top_matrix(body, hints)[0]) == 10


def lending_column_sample():
    hints = [
        dict(HOLDER_RANK=n, HOLDER_NAME=f"合成股东{n}", HOLD_NUM=str(1000 + n))
        for n in range(1, 11)
    ]
    header = [
        "单位：股",
        "前十名股东持股情况（不含通过转融通出借股份）",
        "                                                 持有   包含转   质押、标记或冻结",
        "                                                 有限   融通借      情况",
        "  股东名称      报告期                       比例         售条   出股份"
        "                        股东",
        "                        期末持股数量",
        "  （全称）      内增减                       (%)        件股   的限售"
        "                        性质",
        "                                                            股份",
        "                                                 份数   股份数         数量",
        "                                                            状态",
        "                                                  量    量",
    ]
    data = [f"{h['HOLDER_NAME']}  0  {h['HOLD_NUM']}  1.25  0  0  无  0  其他" for h in hints]
    return "\n".join(header + data + ["前十名无限售条件股东持股情况"]), hints


def test_wrapped_explicit_lending_header_preserves_complete_native_matrix():
    body, hints = lending_column_sample()
    rows, proof = parse_top_matrix(body, hints)
    assert len(rows) == proof["rows"] == 10
    assert rows[0]["quantity"] == 1001
    assert rows[0]["raw_percentage"] == "1.25"


def split_delta_lending_sample():
    body, hints = lending_column_sample()
    body = body.replace("报告期                       ", "报告期内增                   ")
    body = body.replace("      内增减", "        减")
    body = body.replace(
        "合成股东1  0  1001  1.25  0  0  无  0  其他",
        "合成股东1           0                      国有\n"
        "                        1001  1.25  0  0  无  0\n"
        "                                                        法人",
    )
    return body, hints


def test_lending_explicit_delta_and_quantity_on_adjacent_physical_lines():
    body, hints = split_delta_lending_sample()
    rows, proof = parse_top_matrix(body, hints)
    assert len(rows) == 10 and rows[0]["quantity"] == 1001
    assert rows[0]["raw_percentage"] == "1.25" and rows[0]["category"] is None
    assert proof["layout_normalization_contract"] == "adjacent-explicit-lending-delta-row-v1"


@pytest.mark.parametrize(
    "fault", ["no_delta", "unknown_right", "wrong_quantity", "name", "wrong_header", "far_header"]
)
def test_split_lending_delta_cannot_be_inferred_or_join_unrelated_columns(fault):
    body, hints = split_delta_lending_sample()
    if fault == "no_delta":
        body = body.replace("合成股东1           0", "合成股东1            ")
    elif fault == "unknown_right":
        body = body.replace("0                      国有", "0                      未审核")
    elif fault == "wrong_quantity":
        body = body.replace("1001  1.25", "1002  1.25")
    elif fault == "name":
        body = body.replace("合成股东1           ", "额外名称合成股东1           ")
    elif fault == "wrong_header":
        body = body.replace("报告期内增", "未知字段")
    else:
        body = body.replace("        减", "                      减")
    with pytest.raises(RuntimeError):
        parse_top_matrix(body, hints)


def vertical_lending_sample():
    body, hints = split_delta_lending_sample()

    def line(cells):
        result = " " * 80
        for at, text in cells:
            result = result[:at] + text + result[at + len(text) :]
        return result.rstrip()

    header = [
        "单位：股",
        "前十名股东持股情况（不含通过转融通出借股份）",
        line([(51, "包含")]),
        line([(46, "持有"), (51, "转融")]),
        line([(46, "有限"), (51, "通借")]),
        line(
            [
                (3, "股东名称"),
                (13, "报告期内增"),
                (22, "期末持股数"),
                (39, "比例"),
                (51, "出股"),
                (66, "股东"),
            ]
        ),
        line([(3, "（全称）"), (15, "减"), (24, "量"), (39, "(%)"), (51, "份的"), (66, "性质")]),
        line([(46, "售条"), (51, "限售")]),
        line([(46, "件股"), (51, "股份")]),
    ]
    data = body.splitlines()[
        next(i for i, s in enumerate(body.splitlines()) if s.startswith("合成股东1 ")) :
    ]
    return "\n".join(header + data), hints


def test_vertical_lending_fragments_bind_same_physical_column_with_split_name_cells():
    body, hints = vertical_lending_sample()
    hints[1]["HOLDER_NAME"] = "合成股东二全称"
    body = body.replace("合成股东2  0", "合成股东二  0").replace(
        "1002  1.25  0  0  无  0  其他",
        "1002  1.25  0  0  无  0\n全称"
        + " " * 55
        + "非国\n"
        + " " * 60
        + "有法\n"
        + " " * 60
        + "人",
    )
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10 and rows[1]["name"] == hints[1]["HOLDER_NAME"]
    assert rows[1]["category"] is None
    for invalid in [body.replace("通借", "通贷"), body.replace("非国", "未审核")]:
        with pytest.raises(RuntimeError):
            parse_top_matrix(invalid, hints)


def test_lending_column_wrapped_nature_stays_unknown_not_inferred():
    body, hints = lending_column_sample()
    body = body.replace(
        "无  0  其他",
        "无  0\n                              境内非国有法\n                              人",
        1,
    )
    rows, _ = parse_top_matrix(body, hints)
    assert rows[0]["category"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "header",
        "order",
        "unit",
        "quantity",
        "name",
        "extra",
        "missing",
        "rank",
        "unknown_right_cell",
        "missing_column",
    ],
)
def test_lending_column_family_fails_closed(fault):
    body, hints = lending_column_sample()
    if fault == "header":
        body = body.replace("包含转", "未知列")
    elif fault == "order":
        body = body.replace("期末持股数量", "期末其他数量")
    elif fault == "unit":
        body = body.replace("单位：股", "单位：万股")
    elif fault == "quantity":
        body = body.replace("  1001  ", "  1002  ", 1)
    elif fault == "name":
        body = body.replace("合成股东1  ", "未知股东1  ", 1)
    elif fault == "extra":
        body = body.replace(
            "前十名无限售", "额外股东  0  999  1.25  0  0  无  0  其他\n前十名无限售"
        )
    elif fault == "missing":
        body = body.replace("合成股东1  0  1001  1.25  0  0  无  0  其他", "")
    elif fault == "rank":
        hints[7]["HOLDER_RANK"] = 7
    elif fault == "unknown_right_cell":
        body = body.replace("合成股东1  ", "              隐藏名称\n合成股东1  ", 1)
    else:
        body = body.replace("  0  0  无", "  0  无")
    with pytest.raises(RuntimeError):
        parse_top_matrix(body, hints)


def split_floating_sample():
    hints = [
        dict(HOLDER_RANK=n, HOLDER_NAME=f"合成股东{n}续", HOLD_NUM=str(1000 + n))
        for n in range(1, 11)
    ]
    lines = [
        "(二) 前十名股东、前十名无限售条件股东持股情况表",
        "单位：股",
        "前十名无限售条件股东持股情况（不含通过转融通出借股份）",
        " " * 37 + "持有无限售条件     股份种类及数量",
        f"{'股东名称':<37}{'流通股的数量':<16}种类      数量",
    ]
    for n in range(1, 11):
        lines.extend(
            [
                f"{'合成股东':<52}人民币普",
                f"{str(n):<37}{str(1000 + n):>12}    {str(1000 + n):>12}",
                f"{'续':<53}通股",
            ]
        )
    return "\n".join(lines + ["前十名股东中回购专户情况说明 无"]), hints


def test_floating_split_share_class_has_all_ten_exact_paired_quantities():
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = split_floating_sample()
    rows, proof = parse_floating_matrix(body, hints)
    assert [r["name"] for r in rows] == [h["HOLDER_NAME"] for h in hints]
    assert [r["quantity"] for r in rows] == list(range(1001, 1011))
    assert all(r["percentage"] is None and r["share_nature"] == "人民币普通股" for r in rows)
    assert proof["layout_contract"] == "physical-split-rmb-share-class-paired-quantities-v1"


@pytest.mark.parametrize(
    "fault",
    [
        "class",
        "suffix",
        "quantity",
        "name",
        "extra",
        "missing",
        "rank",
        "unit",
        "column",
        "order",
        "percent",
        "quantity_header",
    ],
)
def test_split_floating_family_rejects_unproved_extent_columns_and_share_class(fault):
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = split_floating_sample()
    if fault == "class":
        body = body.replace("人民币普", "境外普通", 1)
    elif fault == "suffix":
        body = body.replace(f"{'续':<53}通股", f"{'续':<53}未知", 1)
    elif fault == "quantity":
        body = body.replace("1001", "1002", 1)
    elif fault == "name":
        body = body.replace("合成股东", "错误股东", 1)
    elif fault == "extra":
        body = body.replace("前十名股东中回购", "隐藏股东\n前十名股东中回购")
    elif fault == "missing":
        body = body.replace(f"{'续':<53}通股", "", 1)
    elif fault == "rank":
        hints[7]["HOLDER_RANK"] = 7
    elif fault == "unit":
        body = body.replace("单位：股", "单位：股\n单位：万股")
    elif fault == "column":
        body = body.replace("持有无限售条件", "未知持股条件")
    elif fault == "order":
        body = body.replace("种类      数量", "            种类 数量")
    elif fault == "quantity_header":
        body = body.replace("流通股的数量", "未知股的数量")
    else:
        body = body.replace(f"{str(1001):>12}    {str(1001):>12}", "1001  12%  1001")
    with pytest.raises(RuntimeError):
        parse_floating_matrix(body, hints)


def column_floating_sample():
    hints = [
        dict(HOLDER_RANK=n, HOLDER_NAME=f"合成股东{n}公司", HOLD_NUM=str(1000 + n))
        for n in range(1, 11)
    ]
    lines = [
        "单位：股",
        "前10名无限售条件股东持股情况（不含通过转融通出借股份、高管锁定股）",
        "股东名称          报告期末持有无限售条件股份数量        股份种类",
        " " * 55 + "股份种类     数量",
    ]
    for n in range(1, 11):
        lines += [
            f"{'#合成股东' if n == 1 else '合成股东':<20}",
            f"{str(n):<20} {str(1000 + n):>25}   人民币普通股     {1000 + n}",
            "公司",
        ]
        if n == 5:
            lines += ["\f", "       合成公司2025年半年度报告全文", "12"]
    lines += ["前10名无限售流通股股东之间关联关系说明"]
    return "\n".join(lines), hints


def test_explicit_column_floating_preserves_all_names_quantities_and_annotation():
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = column_floating_sample()
    rows, proof = parse_floating_matrix(body, hints)
    assert [r["quantity"] for r in rows] == list(range(1001, 1011))
    assert [r["name"] for r in rows] == [h["HOLDER_NAME"] for h in hints]
    assert all(r["percentage"] is None for r in rows)
    assert proof["layout_contract"] == "explicit-column-wrapped-floating-matrix-v1"
    assert proof["name_prefix_annotations"] == [{"rank": 1, "marker": "#"}]


@pytest.mark.parametrize(
    "fault",
    [
        "name",
        "interior_marker",
        "extra",
        "missing",
        "quantity",
        "repeat",
        "class",
        "unit",
        "unknown_header",
        "rank",
    ],
)
def test_column_floating_never_infers_missing_atoms_or_rows(fault):
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = column_floating_sample()
    if fault == "name":
        body = body.replace("#合成股东", "#错误股东", 1)
    elif fault == "interior_marker":
        body = body.replace("#合成股东", "合#成股东", 1)
    elif fault == "extra":
        body = body.replace("前10名无限售流通股", "额外股东\n前10名无限售流通股", 1)
    elif fault == "missing":
        body = body.replace("1001", "", 1)
    elif fault == "quantity":
        hints[0]["HOLD_NUM"] = "1002"
    elif fault == "repeat":
        body = body.replace("1001", "1002", 1)
    elif fault == "class":
        body = body.replace("人民币普通股", "境外普通股", 1)
    elif fault == "unit":
        body = body.replace("单位：股", "单位：份", 1)
    elif fault == "unknown_header":
        body = body.replace("报告期末持有无限售条件股份数量", "未知条件的数量", 1)
    else:
        hints[0]["HOLDER_RANK"] = 2
    with pytest.raises(RuntimeError):
        parse_floating_matrix(body, hints)


@pytest.mark.parametrize("variant", ["joined_header", "wrapped_header"])
def test_column_floating_explicit_alternative_header_and_annual_page(variant):
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = column_floating_sample()
    body = body.replace(
        "报告期末持有无限售条件股份数量",
        "持有无限售条件流通股的数量"
        if variant == "joined_header"
        else "持有无限售条件\n流通股的数量",
    )
    body = (
        body.replace("股份种类     数量", "种类     数量")
        .replace("合成公司2025年半年度报告全文", "合成公司2025年年度报告")
        .replace("\n12\n", "\n12 / 200\n")
    )
    body = body.replace("前10名无限售流通股股东之间关联关系说明", "前十名股东中回购专户情况说明")
    rows, proof = parse_floating_matrix(body, hints)
    assert len(rows) == 10 and rows[0]["quantity"] == 1001
    assert proof["name_prefix_annotations"] == [dict(rank=1, marker="#")]


def test_floating_quantity_header_may_wrap_only_in_same_physical_column():
    from quanthus.pipelines.securities_warehouse.base231_official_holder import (
        parse_floating_matrix,
    )

    body, hints = column_floating_sample()
    body = body.replace(
        "股东名称          报告期末持有无限售条件股份数量        股份种类",
        " " * 30 + "持有无限售条件       股份种类及数量\n股东名称\n" + " " * 31 + "流通股的数量",
    )
    body = body.replace("股份种类     数量", "种类     数量")
    rows, _ = parse_floating_matrix(body, hints)
    assert len(rows) == 10
    with pytest.raises(RuntimeError):
        parse_floating_matrix(
            body.replace(" " * 31 + "流通股的数量", " " * 2 + "流通股的数量"), hints
        )


@pytest.mark.parametrize("state_quantity", ["", "/", "未知"])
def test_top_matrix_does_not_require_unrelated_pledge_quantity(state_quantity):
    body, hints = nature_last_sample()
    body = body.replace("无  0  其他", "无  " + state_quantity + "  其他")
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10 and rows[0]["quantity"] == 1001 and rows[0]["percentage"] == 1
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("1001", "1002", 1), hints)


def test_top_split_header_columns_keep_explicit_order_and_unknown_nature():
    body, hints = nature_last_sample()

    def physical(cells):
        value = ""
        for col, text in cells:
            width = sum(2 if ord(c) > 127 else 1 for c in value)
            value += " " * max(0, col - width) + text
        return value

    header = "\n".join(
        [
            physical(
                [(0, "股东名称"), (14, "报告期内"), (30, "期末持股"), (48, "比例"), (65, "股东性")]
            ),
            physical([(15, "增减"), (31, "数量"), (48, "(%)")]),
            physical([(66, "质")]),
        ]
    )
    body = body.replace(
        "股东名称  报告期内增减  期末持股数量  比例（%）  有限售股份  股份状态  数量  股东性质",
        header,
    )
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10 and rows[0]["quantity"] == 1001
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("期末持股", "未知数量"), hints)


def test_top_physical_raw_name_column_does_not_clip_wide_characters():
    body, hints = nature_last_sample()
    old = next(x for x in body.splitlines() if "报告期内增减" in x)
    header = "股东名称     报告期内增减      期末持股数量        比例（%）           股东性质"
    body = body.replace(old, header)
    for n, h in enumerate(hints, 1):
        name = f"合成股东全称{n}甲乙丙"
        body = body.replace(h["HOLDER_NAME"] + "  -2", name + "  -2", 1)
        h["HOLDER_NAME"] = name
    rows, _ = parse_top_matrix(body, hints)
    assert [r["name"] for r in rows] == [h["HOLDER_NAME"] for h in hints]
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("甲乙丙", "甲乙丁", 1), hints)


def test_top_missing_wrapped_nature_remains_unknown_without_changing_numeric_atoms():
    body, hints = nature_last_sample()
    body = body.replace("无  0  其他", "无  /  ", 1)
    rows, _ = parse_top_matrix(body, hints)
    assert len(rows) == 10 and rows[0]["category"] is None
    assert rows[0]["quantity"] == 1001 and rows[0]["raw_percentage"] == "1.00"
    with pytest.raises(RuntimeError):
        parse_top_matrix(body.replace("无  /  ", "不明状态  /  ", 1), hints)
