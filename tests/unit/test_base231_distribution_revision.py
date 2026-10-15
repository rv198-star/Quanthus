from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base231_distribution_revision import (
    qualify_payment_revision,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity


def sample():
    before = (
        "本公司此次委托中国结算深圳分公司代派的A股股东现金红利将于2026年9月14日"
        "通过股东托管证券公司（或其他托管机构）直接划入其资金账户。"
    )
    after = before.replace("9月14日", "9月15日")
    base = (
        "证券代码：003018 公告编号：2026-066\n2026年中期权益分派实施公告\n"
        "二、本次实施的利润分配及资本公积金转增股本方案\n"
        "向全体股东每10股派2.50元人民币现金（含税），每10股转增2.25股，不送红股\n"
        "三、股权登记日与除权除息日\n"
        "股权登记日为：2026年9月14日，除权除息日为：2026年9月15日\n"
        "五、权益分派方法\n" + before + "\n董事会\n2026年9月10日"
    )
    bodies = [
        base,
        base.replace("2026-066", "2026-068")
        .replace("实施公告\n", "实施公告（更正后）\n")
        .replace(before, after)
        .replace("董事会\n2026年9月10日", "董事会\n2026年9月12日"),
        "证券代码：003018 公告编号：2026-067\n关于2026年中期权益分派实施公告的更正公告\n"
        "于2026年9月10日在巨潮资讯网刊登了《2026年中期权益分派实施公告》"
        "（公告编号：2026-066），现将相关内容更正如下：更正前：" + before
        + "更正后：" + after
        + "除上述更正内容外，公司2026年中期权益分派实施的其他内容均未发生变化。",
    ]
    docs = []
    for n, (body, day) in enumerate(zip(bodies, [10, 12, 12], strict=True)):
        aid = str(123 + n)
        actual = dict(
            secCode="003018", orgId="same-original-org", announcementId=aid,
            announcementTitle=["2026年中期权益分派实施公告",
                               "2026年中期权益分派实施公告（更正后）",
                               "关于2026年中期权益分派实施公告的更正公告"][n],
            announcementTime=int(datetime(2026, 9, day, tzinfo=timezone.utc).timestamp() * 1000),
            adjunctUrl=f"finalpage/2026-09-{day}/" + aid + ".PDF",
        )
        uri = "https://static.cninfo.com.cn/" + actual["adjunctUrl"]
        sha = "sha256:" + str(n + 1) * 64
        docs.append(dict(
            body=body, PDF_sha256=sha, index=dict(announcements=[actual]),
            selection=dict(actual_index_row=actual, index_sha256="sha256:" + "a" * 64,
                           actual_document_URI=uri, original_disclosure=dict(
                               company_id="c", disclosure_id="d" + aid,
                               source_document_id=aid, business_date=f"2026-09-{day}",
                               explicit_references=[dict(security_id="s")],
                           )),
            receipt=dict(URI=uri, completed_at="2026-10-15T00:00:00+00:00",
                         curl_exit=0, http_status="200", body_sha256=sha),
        ))
    return docs, dict(
        parent=FinancialCompanyIdentity("003018", "s", "c",
                                        datetime(1990, 1, 1, tzinfo=timezone.utc), None),
        observed_at=datetime(2026, 10, 15, 1, tzinfo=timezone.utc),
        expected_ex_date=date(2026, 9, 15), currency_scheme_body=None,
    )


def test_exact_three_document_revision_is_one_event_with_real_new_knowledge():
    docs, args = sample()
    before = deepcopy((docs, args))
    result = qualify_payment_revision(*docs, **args)
    assert result["payment_date"] == date(2026, 9, 15)
    assert result["selected_announcement_id"] == "124"
    assert result["superseded_announcement_id"] == "123"
    assert result["event_count"] == 1
    assert result["observed_at"] == args["observed_at"]
    assert not result["old_values_overwritten"] and not result["historical_PIT_restored"]
    assert (docs, args) == before


def test_native_payment_is_corrected_once_without_changing_exact_cash_terms():
    from quanthus.pipelines.securities_warehouse.base231_dated_distribution import project_terms

    docs, args = sample()
    interval = dict(effective_from=args["parent"].effective_from, effective_to=None,
                    knowledge_from=datetime(2026, 10, 14, tzinfo=timezone.utc), knowledge_to=None)
    common = dict(
        parent=args["parent"], venue="SZSE", expected_ex_date=args["expected_ex_date"],
        observed_at=args["observed_at"],
        listings=[dict(interval, security_id="s", venue_id="v")],
        calendars=[dict(venue_id="v", trade_date=args["expected_ex_date"], session_type="day",
                        is_open=True, knowledge_from=interval["knowledge_from"],
                        knowledge_to=None)],
        venues=[dict(interval, venue_id="v", mic_code="XSHE", timezone="Asia/Shanghai")],
        vocabulary=dict(action_types=dict(mixed="MIXED_DISTRIBUTION"),
                        action_statuses={"实施": "IMPLEMENTED"}, term_types=dict(
                            cash_per_share="CASH_AMOUNT_PER_SHARE",
                            bonus_shares="BONUS_SHARES_PER_SHARE",
                            transfer_shares="TRANSFER_SHARES_PER_SHARE")),
    )
    d = docs[1]
    plain, _ = project_terms(d["body"], selection=d["selection"], receipt=d["receipt"],
                             index=d["index"], **common)
    revised, proof = project_terms(
        d["body"], selection=d["selection"], receipt=d["receipt"], index=d["index"],
        payment_revision=dict(previous=docs[0], notice=docs[2]), **common,
    )
    assert plain["dwd_corporate_action"][0]["payment_date"] is None
    assert len(revised["dwd_corporate_action"]) == 1
    assert revised["dwd_corporate_action"][0]["payment_date"] == date(2026, 9, 15)
    assert proof["payment_revision_proof"]["event_count"] == 1
    ignored = {"source_revision", "source_artifact_sha256", "payment_date"}
    for table in plain:
        assert [{k: v for k, v in r.items() if k not in ignored} for r in plain[table]] == [
            {k: v for k, v in r.items() if k not in ignored} for r in revised[table]
        ]


def test_reference_parenthesis_may_precede_quoted_title_in_pdf_column_order():
    docs, args = sample()
    docs[2]["body"] = docs[2]["body"].replace(
        "在巨潮资讯网刊登了《2026年中期权益分派实施公告》（公告编号：2026-066）",
        "（公告编号：2026-066）在巨潮资讯网刊登了《2026年中期权益分派实施公告》",
    )
    assert qualify_payment_revision(*docs, **args)["event_count"] == 1
    docs[2]["body"] = docs[2]["body"].replace("2026-066", "2026-069")
    with pytest.raises(RuntimeError):
        qualify_payment_revision(*docs, **args)


@pytest.mark.parametrize("fault", [
    "code", "org", "missing_index", "future_capture", "notice_number", "notice_title",
    "notice_before", "notice_after", "other_change", "amount_change", "no_unchanged",
    "unchanged_payment", "post_cutoff_payment", "wrong_selected_id", "publication_order",
])
def test_revision_requires_exact_complete_sources_and_only_disclosed_change(fault):
    docs, args = sample()
    if fault in {"code", "org"}:
        docs[2]["selection"]["actual_index_row"][
            "secCode" if fault == "code" else "orgId"
        ] = "other"
    elif fault == "missing_index":
        docs[2]["index"]["announcements"] = []
    elif fault == "future_capture":
        docs[2]["receipt"]["completed_at"] = "2026-10-16T00:00:00+00:00"
    elif fault == "notice_number":
        docs[2]["body"] = docs[2]["body"].replace("2026-066", "2026-065")
    elif fault == "notice_title":
        docs[2]["body"] = docs[2]["body"].replace("2026年中期", "2025年年度")
    elif fault in {"notice_before", "notice_after"}:
        token = "9月14日" if fault == "notice_before" else "9月15日"
        docs[2]["body"] = docs[2]["body"].replace(token, "9月16日")
    elif fault == "other_change":
        docs[1]["body"] = docs[1]["body"].replace("五、", "五、其他未说明的业务变化。")
    elif fault == "amount_change":
        docs[1]["body"] = docs[1]["body"].replace("2.50元", "2.60元")
    elif fault == "no_unchanged":
        docs[2]["body"] = docs[2]["body"].replace("其他内容均未发生变化", "其他内容待定")
    elif fault == "unchanged_payment":
        docs[1]["body"] = docs[1]["body"].replace("9月15日通过", "9月14日通过")
    elif fault == "post_cutoff_payment":
        for i in [1, 2]:
            docs[i]["body"] = docs[i]["body"].replace("9月15日通过", "10月1日通过")
    elif fault == "wrong_selected_id":
        docs[1]["selection"]["actual_index_row"]["announcementId"] = "123"
    else:
        docs[0]["selection"]["actual_index_row"]["announcementTime"] += 4 * 86400000
    with pytest.raises(RuntimeError):
        qualify_payment_revision(*docs, **args)
