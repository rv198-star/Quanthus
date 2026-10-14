from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base231_name_change import (
    parse_name_change,
    project_name_change,
    reconcile_name_intervals,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity


def body():
    return (
        "证券代码：600001 证券简称：合成旧名\n经交易所办理，"
        "公司证券简称将于2026年8月17日起由“合成旧名”变更为“合成新名”，证券代码保持不变。"
    )


def test_exact_implemented_name_and_date_without_corporate_name_inference():
    parsed = parse_name_change(body(), code="600001")
    assert parsed["effective_date"] == date(2026, 8, 17)
    assert parsed["new_name"] == "合成新名"
    assert parsed["field"] == "STOCK_SHORT_NAME_ONLY"


def test_risk_warning_start_has_exact_alias_date_without_tradability_change():
    raw = (
        "证券代码：600001 证券简称：合成旧名；股票简称由“合成旧名”变更为“ST合成”；"
        "实施其他风险警示的起始日：2026年8月3日。"
    )
    assert parse_name_change(raw, code="600001")["effective_date"] == date(2026, 8, 3)


@pytest.mark.parametrize("fault", ["code", "proposal", "date", "pair", "future", "same"])
def test_undated_proposals_and_conflicting_aliases_are_held(fault):
    raw = body()
    if fault == "code":
        raw = raw.replace("600001", "600002")
    elif fault == "proposal":
        raw = raw.replace("将于2026年8月17日起由", "拟由")
    elif fault == "date":
        raw += raw.replace("8月17日", "8月18日")
    elif fault == "pair":
        raw += raw.replace("合成新名", "另一新名")
    elif fault == "future":
        raw = raw.replace("8月17日", "10月17日")
    else:
        raw = raw.replace("合成新名", "合成旧名")
    with pytest.raises(RuntimeError):
        parse_name_change(raw, code="600001")


def test_typed_alias_preserves_current_knowledge_and_ordinary_identity():
    parent = FinancialCompanyIdentity(
        "600001", "security-1", "company-1", datetime(2000, 1, 1, tzinfo=timezone.utc), None
    )
    observed = datetime(2026, 10, 14, 11, tzinfo=timezone.utc)
    row, proof = project_name_change(
        body(),
        code="600001",
        parent=parent,
        venue_id="venue-SSE",
        PDF_sha256="sha256:" + "a" * 64,
        announcement_id="100001",
        received_at=observed,
        observed_at=observed,
    )
    assert row["security_id"] == parent.security_id
    assert row["knowledge_from"] == observed
    assert row["effective_from"].isoformat() == "2026-08-16T16:00:00+00:00"
    assert proof["code_identity_changed"] is False
    assert proof["listing_or_trading_state_changed"] is False


def test_alias_end_uses_next_explicit_transition_without_mutating_inputs():
    first = dict(
        security_id="s",
        knowledge_from=datetime(2026, 10, 14, tzinfo=timezone.utc),
        effective_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
        effective_to=None,
        identifier_value="合成甲",
    )
    second = dict(
        first, effective_from=datetime(2026, 8, 1, tzinfo=timezone.utc), identifier_value="合成乙"
    )
    rows = reconcile_name_intervals(
        [second, first],
        [dict(original_pair=dict(old_name="合成甲")), dict(original_pair=dict(old_name="旧名"))],
    )
    assert rows[1]["effective_to"] == second["effective_from"]
    assert first["effective_to"] is None
    with pytest.raises(RuntimeError):
        reconcile_name_intervals(
            [first, second],
            [
                dict(original_pair=dict(old_name="旧名")),
                dict(original_pair=dict(old_name="不相同")),
            ],
        )


def test_implementation_date_accepts_explicit_start_word_without_proposal():
    assert parse_name_change(body().replace("将于", "将自"), code="600001")[
        "effective_date"
    ] == date(2026, 8, 17)


def test_exact_pair_with_its_explicit_activation_instant_date():
    raw = (
        "证券代码：600001；公司证券简称由“合成旧名”变更为“合成新名”，"
        "启用时间为2026年8月11日（星期二）。"
    )
    assert parse_name_change(raw, code="600001")["effective_date"] == date(2026, 8, 11)


def test_explicit_before_after_labelled_short_names_and_activation_date():
    raw = (
        "证券代码：600001；证券简称启用日期：2026年8月13日；"
        "\n一、变更前证券简称：合成旧名\n二、变更后证券简称：合成新名\n"
        "三、公司证券代码：600001（保持不变）"
    )
    assert parse_name_change(raw, code="600001")["new_name"] == "合成新名"


@pytest.mark.parametrize(
    "fault", ["proposed", "unrelated_date", "ambiguous_pair", "conflicting_date"]
)
def test_extra_activation_families_cannot_borrow_proposal_or_unrelated_dates(fault):
    raw = "证券代码：600001；公司证券简称由“合成旧名”变更为“合成新名”，启用时间为2026年8月11日。"
    if fault == "proposed":
        raw = raw.replace("简称由", "简称拟由")
    elif fault == "unrelated_date":
        raw = raw.replace("启用时间为", "董事会日期为")
    elif fault == "ambiguous_pair":
        raw += raw.replace("合成新名", "另一新名")
    else:
        raw += "证券简称启用日期：2026年8月12日。"
    with pytest.raises(RuntimeError):
        parse_name_change(raw, code="600001")


def test_implemented_direct_since_pair_preserves_wrapped_original_short_name():
    raw = body().replace("将于", "自").replace("合成旧名”变更", "合成旧\n名”变更")
    parsed = parse_name_change(raw, code="600001")
    assert parsed["old_name"] == "合成旧名"
    assert parsed["effective_date"] == date(2026, 8, 17)


@pytest.mark.parametrize("start_word", ["", "自"])
def test_labelled_risk_warning_pair_and_explicit_start_date(start_word):
    raw = (
        "证券代码：600001；\n（二）股票简称：由“*ST合成”变更为“合成新名”；"
        "\n（四）撤销退市风险警示及其他风险警示的起始日："
        + start_word
        + "2026年8月7日（星期五）开市起撤销。"
    )
    parsed = parse_name_change(raw, code="600001")
    assert parsed["old_name"] == "*ST合成"
    assert parsed["effective_date"] == date(2026, 8, 7)


@pytest.mark.parametrize("fault", ["proposal", "unrelated_date", "conflicting_date"])
def test_new_direct_and_labelled_families_reject_unproved_or_conflicting_dates(fault):
    raw = (
        "证券代码：600001；股票简称：由“合成旧名”变更为“ST合成”；"
        "实施其他风险警示的起始日：自2026年8月3日。"
    )
    if fault == "proposal":
        raw = raw.replace("简称：由", "简称拟由")
    elif fault == "unrelated_date":
        raw = raw.replace("实施其他风险警示的起始日", "董事会会议日期")
    else:
        raw += "证券简称启用日期：2026年8月4日。"
    with pytest.raises(RuntimeError):
        parse_name_change(raw, code="600001")


def test_risk_warning_start_date_label_with_qi_is_not_a_different_clock():
    raw = (
        "证券代码：600001；股票简称：由“*ST合成”变更为“合成新名”；"
        "撤销退市风险警示的起始日期：自2026年9月29日开市起撤销。"
    )
    assert parse_name_change(raw, code="600001")["effective_date"] == date(2026, 9, 29)


def resume_alias_body():
    return (
        "证券代码：600001；公司股票将于2026年9月2日停牌一天，"
        "并于2026年9月3日开市起复牌。自复牌之日起，深圳证券交易所"
        "撤销对公司股票交易的退市风险警示。"
        "公司股票交易本次撤销退市风险警示后，公司股票交易继续被实施"
        "其他风险警示，公司股票简称将由“*ST合成”变更为“ST合成”。"
    )


def test_explicit_resume_warning_and_alias_effect_chain_only_projects_short_name():
    parsed = parse_name_change(resume_alias_body(), code="600001")
    assert parsed["effective_date"] == date(2026, 9, 3)
    assert parsed["field"] == "STOCK_SHORT_NAME_ONLY"


@pytest.mark.parametrize("fault", ["unrelated_resume", "different_change", "proposal", "date"])
def test_resume_date_cannot_be_borrowed_without_its_exact_warning_alias_chain(fault):
    raw = resume_alias_body()
    if fault == "unrelated_resume":
        raw = raw.replace("自复牌之日起", "另行公告后")
    elif fault == "different_change":
        raw = raw.replace("本次撤销退市风险警示后", "董事会会议结束后")
    elif fault == "proposal":
        raw = raw.replace("简称将由", "简称拟由")
    else:
        raw += "证券简称启用日期：2026年9月4日。"
    with pytest.raises(RuntimeError):
        parse_name_change(raw, code="600001")


def numbered_alias_body():
    return (
        "证券代码：600001；2、股票简称：由“*ST合成”变更为“合成新名”；"
        "3、股票代码：600001；4、撤销退市风险警示的起始日期：自2026年9月29日。"
    )


def test_numbered_current_fields_do_not_mix_a_reverse_historical_narrative_pair():
    raw = numbered_alias_body() + "去年，公司股票简称由“合成新名”变更为“*ST合成”。"
    parsed = parse_name_change(raw, code="600001")
    assert parsed["new_name"] == "合成新名"
    assert parsed["effective_date"] == date(2026, 9, 29)


def test_conflicting_numbered_current_field_blocks_are_rejected():
    raw = numbered_alias_body() + numbered_alias_body().replace("9月29日", "9月30日")
    with pytest.raises(RuntimeError):
        parse_name_change(raw, code="600001")


def delisting_alias_body():
    return (
        "证券代码：600001 证券简称：*ST合成 公告编号：2026-100；"
        "一、公司股票在退市整理期间的证券代码、证券简称及涨跌幅限制"
        "1、证券代码：600001 2、证券简称：合成退 3、涨跌幅限制：另行规定。"
        "公司股票进入退市整理期的起始日为2026年9月30日，预计最后交易日期为2026年10月27日。"
    )


def test_delisting_period_arrangement_names_bind_only_its_explicit_start():
    parsed = parse_name_change(delisting_alias_body(), code="600001")
    assert parsed["old_name"] == "*ST合成"
    assert parsed["new_name"] == "合成退"
    assert parsed["effective_date"] == date(2026, 9, 30)
    assert parsed["field"] == "STOCK_SHORT_NAME_ONLY"


@pytest.mark.parametrize("fault", ["section", "start", "code", "conflicting_start", "future"])
def test_delisting_alias_requires_its_complete_explicit_arrangement(fault):
    raw = delisting_alias_body()
    if fault == "section":
        raw = raw.replace("公司股票在退市整理期间", "公司收到终止上市决定后")
    elif fault == "start":
        raw = raw.replace("公司股票进入退市整理期的起始日为", "预计最后交易日期为")
    elif fault == "code":
        raw = raw.replace("1、证券代码：600001", "1、证券代码：600002")
    elif fault == "conflicting_start":
        raw += "公司股票进入退市整理期的起始日为2026年9月29日。"
    else:
        raw = raw.replace("9月30日", "10月1日")
    with pytest.raises(RuntimeError):
        parse_name_change(raw, code="600001")
