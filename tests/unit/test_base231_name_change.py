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
