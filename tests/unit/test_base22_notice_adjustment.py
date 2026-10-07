"""Only an explicit old-to-new relation can remove an old rate in the current scheme."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_notice_adjustment import (
    parse_adjusted_current_scheme_cash,
)


def notice(old="1.10", new="1.17", relation="变更为"):
    return (
        "证券代码：002001\n一、权益分派方案\n"
        f"本公司2025年度权益分派方案为：每10股派发现金红利{new}元(含税)。\n"
        f"分派方案由每10股派发现金红利{old}元(含税){relation}"
        f"每10股派发现金红利{new}元(含税)。\n"
        "二、股权登记日和除权除息日\n除权除息日：2026年7月10日。"
    )


def parse(text, code="002001", day=date(2026, 7, 10)):
    return parse_adjusted_current_scheme_cash(
        text,
        stock_code=code,
        ex_date=day,
        publisher_title="2025年度权益分派实施公告",
    )


@pytest.mark.parametrize("old,new", [("1.10", "1.17"), ("1.17", "1.10")])
def test_explicit_relation_preserves_current_gross_rate_and_both_raw_token_bindings(old, new):
    result = parse(notice(old, new))
    assert result["cash_per_share"] == Decimal(new) / 10
    assert len(result["explicit_adjustments"]) == 1
    assert result["historical_publication_or_acquisition_inferred"] is False
    assert result["other_action_terms_qualified"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "proposal",
        "unlinked",
        "wrong-code",
        "wrong-date",
        "mixed",
        "different-current",
        "wrong-unit",
    ],
)
def test_unrelated_old_plan_or_global_guard_failure_cannot_grant_priority(defect):
    text = notice()
    if defect == "proposal":
        text = text.replace("变更为", "拟变更为")
    elif defect == "unlinked":
        text = text.replace("分派方案由", "此前旧方案是")
    elif defect == "wrong-code":
        text = text.replace("002001", "002002")
    elif defect == "wrong-date":
        text = text.replace("7月10日", "7月11日")
    elif defect == "mixed":
        text += "B股按美元分派。"
    elif defect == "different-current":
        text = text.replace("为：每10股派发现金红利1.17", "为：每10股派发现金红利1.18")
    else:
        text = text.replace("变更为每10股", "变更为每100股")
    with pytest.raises(RuntimeError):
        parse(text)


def test_profile_is_explicit_and_the_default_still_rejects_conflicting_old_rate():
    from quanthus.pipelines.securities_warehouse.base22_notice_action import (
        select_notice_cash_candidates,
    )
    from quanthus.pipelines.securities_warehouse.base22_notice_adjustment import CONTRACT_ID

    target = {"action_id": "synthetic", "stock_code": "002001", "ex_date": "2026-07-10"}
    doc = {
        "document_id": "synthetic-doc",
        "target_action_ids": ["synthetic"],
        "publisher_title": "2025年度权益分派实施公告",
        "publisher_security_code": "002001",
        "text": notice(),
    }
    selected, _ = select_notice_cash_candidates([target], [doc])
    assert selected == {}
    selected, evidence = select_notice_cash_candidates([target], [doc], parser_profile=CONTRACT_ID)
    assert selected["synthetic"]["atom"]["cash_per_share"] == Decimal("0.117")
    assert evidence["contract_id"] == CONTRACT_ID
