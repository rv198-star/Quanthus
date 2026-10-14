from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_dated_distribution import project_terms
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity

BODY = """证券代码：003018 2026权益分派实施公告
二、本次实施的利润分配及资本公积金转增股本方案
向全体股东每10股派2.50元人民币现金（含税），每10股转增2.25股，不送红股
三、股权登记日与除权除息日
股权登记日为：2026年9月14日，除权除息日为：2026年9月15日
"""


def sample():
    clock = datetime(2026, 10, 14, tzinfo=timezone.utc)
    pub = datetime(2026, 9, 10, 7, 30, tzinfo=timezone.utc)
    interval = dict(
        effective_from=datetime(1990, 1, 1, tzinfo=timezone.utc),
        effective_to=None,
        knowledge_from=datetime(2026, 10, 13, tzinfo=timezone.utc),
        knowledge_to=None,
    )
    actual = dict(
        secCode="003018",
        announcementId="123",
        announcementTitle="合成实施公告",
        announcementTime=int(pub.timestamp() * 1000),
        adjunctUrl="finalpage/2026-09-10/123.PDF",
    )
    uri = "https://static.cninfo.com.cn/" + actual["adjunctUrl"]
    return dict(
        selection=dict(
            actual_index_row=actual,
            original_disclosure=dict(
                source_document_id="123",
                company_id="c",
                disclosure_id="d",
                business_date="2026-09-10",
                explicit_references=[dict(security_id="s")],
            ),
            actual_document_URI=uri,
            index_sha256="sha256:" + "a" * 64,
        ),
        receipt=dict(
            completed_at=clock.isoformat(),
            URI=uri,
            body_sha256="sha256:" + "b" * 64,
            curl_exit=0,
            http_status="200",
        ),
        index=dict(announcements=[actual]),
        parent=FinancialCompanyIdentity("003018", "s", "c", interval["effective_from"], None),
        venue="SZSE",
        expected_ex_date=date(2026, 9, 15),
        listings=[dict(interval, security_id="s", venue_id="source-venue")],
        calendars=[
            dict(
                venue_id="source-venue",
                trade_date=date(2026, 9, 15),
                session_type="day",
                is_open=True,
                knowledge_from=interval["knowledge_from"],
                knowledge_to=None,
            )
        ],
        venues=[dict(interval, mic_code="XSHE", venue_id="source-venue", timezone="Asia/Shanghai")],
        observed_at=clock,
        vocabulary=dict(
            action_types=dict(mixed="MIXED_DISTRIBUTION"),
            action_statuses={"实施": "IMPLEMENTED"},
            term_types=dict(
                cash_per_share="CASH_AMOUNT_PER_SHARE",
                bonus_shares="BONUS_SHARES_PER_SHARE",
                transfer_shares="TRANSFER_SHARES_PER_SHARE",
            ),
        ),
    )


def test_actual_publication_instant_exact_terms_and_new_knowledge_are_distinct():
    args = sample()
    before = deepcopy(args)
    rows, proof = project_terms(BODY, **args)
    action = rows["dwd_corporate_action"][0]
    assert action["announced_at"] == datetime(2026, 9, 10, 7, 30, tzinfo=timezone.utc)
    assert action["knowledge_from"] == args["observed_at"]
    assert rows["dwd_corporate_action_term"][0]["amount_per_share"] == Decimal("0.25")
    assert proof["cumulative_adjustment_qualified"] is False
    assert args == before


@pytest.mark.parametrize(
    "fault", ["venue", "listing", "date", "closed", "clock", "duplicate", "session"]
)
def test_terms_require_exact_listing_market_date_and_clock(fault):
    args = sample()
    if fault == "venue":
        args["venues"][0]["mic_code"] = "XSHG"
    elif fault == "listing":
        args["listings"][0]["security_id"] = "other"
    elif fault == "date":
        args["calendars"][0]["trade_date"] = date(2026, 8, 3)
    elif fault == "closed":
        args["calendars"][0]["is_open"] = False
    elif fault == "clock":
        args["calendars"][0]["knowledge_from"] = datetime(2026, 10, 15, tzinfo=timezone.utc)
    elif fault == "duplicate":
        args["calendars"] *= 2
    else:
        args["calendars"][0]["session_type"] = "other"
    with pytest.raises(RuntimeError):
        project_terms(BODY, **args)
