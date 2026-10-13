from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_holder_notice import project_notice
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity

BODY = """信息披露义务人合成甲 住所合成地址
权益变动时间2026年6月4日 股票代码002001
2.本次权益变动情况 变动股数（万股）变动比例
A股合成甲 −100.50 −1.00% 合计−100.50−1.00% 询价转让
3.本次变动前后 股数（万股）
合成甲 合计持有股份1,200.00 12.00% 1,099.50 11.00%
"""


def inputs():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    return dict(
        body=BODY,
        code="002001",
        expected_event_date=date(2026, 6, 4),
        announcement_id="123",
        publication_ms=1780617600000,
        pdf_sha256="sha256:" + "a" * 64,
        received_at=now,
        observed_at=now,
        parent=FinancialCompanyIdentity(
            stock_code="002001",
            security_id="security",
            company_id="company",
            effective_from=datetime(2010, 1, 1, tzinfo=timezone.utc),
            effective_to=None,
        ),
        document_uri="https://static.cninfo.com.cn/finalpage/synthetic/123.PDF",
    )


def test_official_notice_keeps_real_identity_balance_units_and_new_knowledge():
    rows, proof = project_notice(**inputs())
    row = rows["dwd_shareholding_change"][0]
    assert row["share_change"] == Decimal("-1005000")
    assert row["post_holding"] == Decimal("10995000")
    assert row["percentage_change"] == -1
    assert row["disclosure_id"] == rows["dwd_disclosure"][0]["disclosure_id"]
    assert proof["original_event_id"] == "123:A_SHARE_TRANSFER_ROW"
    assert not proof["existing_F10_atom_equivalence_granted"]


def test_explicit_rowspanned_holder_binds_its_own_aggregate():
    kw = inputs()
    kw["body"] = BODY.replace(
        "合成甲 合计持有股份1,200.00 12.00% 1,099.50 11.00%",
        "合计持有股份 1,200.00 12.00% 1,099.50 11.00%\n\n"
        "合成甲 其中：无限售条件股份 1 1% 1 1%\n\n"
        "合计持有股份 99 1.00% 99 1.00%\n\n"
        "另一人 其中：无限售条件股份 1 1% 1 1%",
    )
    rows, _ = project_notice(**kw)
    assert rows["dwd_shareholding_change"][0]["post_holding"] == Decimal("10995000")


@pytest.mark.parametrize(
    "fault", ["unit", "date", "before-after", "ratio", "actor", "code", "numeric-boundary"]
)
def test_unsupported_or_conflicting_notice_does_not_supply_guessed_terms(fault):
    kw = inputs()
    changes = {
        "unit": ("股数（万股）", "股数"),
        "date": ("2026年6月4日", "2026年6月5日"),
        "before-after": ("1,099.50", "1,099.51"),
        "ratio": ("11.00%", "10.99%"),
        "actor": ("A股合成甲", "A股合成乙"),
        "code": ("股票代码002001", "股票代码002002"),
        "numeric-boundary": ("1,099.50 11.00%", "1,099.5011.00%"),
    }
    kw["body"] = kw["body"].replace(*changes[fault])
    with pytest.raises(RuntimeError):
        project_notice(**kw)
