import hashlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_f10_ownership import ownership_observation
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
)

CAPITAL = "【1.股本结构】\n｜单位(股)｜2026-07-31｜\n｜总股本｜1.234567万｜\n【2.股本变动】"
HOLDERS = (
    "截至日期：2026-06-30 十大股东情况\n｜股东名称｜持股数量｜占总股本｜股份性质｜\n"
    "｜合成股东｜1.234567万｜1.123456789012%｜普通股｜\n└"
)


def raw(content=CAPITAL, category="股本结构"):
    return {
        "content": content, "category": category, "stock_code": "002001",
        "content_sha256": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        "acquired_at": "2026-08-01T08:00:03.123456+08:00",
        "source_revision": "weekly-f10-v2", "source": "tdx_f10",
        "provider_updated_date": "2026-07-31",
    }


def resolve(start=datetime(2010, 1, 1, tzinfo=timezone.utc)):
    return FinancialCompanyResolver([FinancialCompanyIdentity(
        "002001", "security", "company", start, None,
        datetime(2026, 7, 31, tzinfo=timezone.utc),
    )])


def project(r, resolver=None):
    return ownership_observation(
        r, source_sha256="sha256:" + "a" * 64,
        resolver=resolve() if resolver is None else resolver,
    )


def test_exact_capital_unit_and_real_acquisition_are_preserved():
    rows, proof = project(raw())
    row = rows["dwd_share_capital_observation"][0]
    assert row["share_quantity"] == Decimal("12345.67")
    assert row["raw_value"] == "1.234567万"
    assert row["observation_start"] == datetime(2026, 7, 30, 16, tzinfo=timezone.utc)
    assert row["knowledge_from"] == datetime(2026, 8, 1, 0, 0, 3, 123456, tzinfo=timezone.utc)
    assert proof["shared_parses_built"] == 1


def test_exact_holder_percentage_and_identity():
    rows, _ = project(raw(HOLDERS, "股东研究"))
    row = rows["dwd_shareholding_observation"][0]
    assert row["holding_percentage"] == Decimal("1.123456789012")
    assert row["holding_quantity"] == Decimal("12345.67")
    assert row["company_id"] == "company" and row["holder_rank"] == 1


def test_prelisting_and_september_are_disclosed_without_alias_or_empty_pass():
    rows, proof = project(raw(), resolve(datetime(2026, 8, 2, tzinfo=timezone.utc)))
    assert rows["dwd_share_capital_observation"] == []
    assert proof["held_observations"][0]["reason"] == "BEFORE_FIRST_LISTED_COMPANY_IDENTITY"
    rows, proof = project(raw(CAPITAL.replace("2026-07-31", "2026-09-01")))
    assert rows["dwd_share_capital_observation"] == []
    assert proof["held_observations"][0]["reason"] == "AFTER_IMAGE_CUTOFF"


@pytest.mark.parametrize("content", ["非空未知格式", CAPITAL.replace("1.234567万", "1.2美元")])
def test_unparsed_nonempty_or_unknown_unit_never_becomes_qualified_empty(content):
    with pytest.raises(BoundedError, match="OWNERSHIP"):
        project(raw(content))


@pytest.mark.parametrize("change", ["hash", "clock", "sub_microsecond", "revision"])
def test_source_bytes_and_real_clock_are_mandatory(change):
    r = raw()
    if change == "hash":
        r["content_sha256"] = "sha256:" + "b" * 64
    elif change == "clock":
        r["acquired_at"] = None
    elif change == "sub_microsecond":
        r["acquired_at"] = "2026-08-01T08:00:03.1234567+08:00"
    else:
        r["source_revision"] = ""
    with pytest.raises((BoundedError, RuntimeError)):
        project(r)


def test_invalid_percentage_is_blocked_not_clipped():
    with pytest.raises(BoundedError, match="SCALAR_UNQUALIFIED"):
        project(raw(HOLDERS.replace("1.123456789012%", "101%"), "股东研究"))


@pytest.mark.parametrize("token", ["不可解释", "1.2美元", "2026-07-31"])
def test_a_valid_cell_cannot_hide_another_unparsed_nonempty_cell(token):
    content = CAPITAL.replace("【2.股本变动】", f"｜流通股本｜{token}｜\n【2.股本变动】")
    with pytest.raises(BoundedError, match="UNACCOUNTED"):
        project(raw(content))


def test_named_holder_with_missing_quantity_is_held_not_silently_dropped():
    content = HOLDERS.replace("└", "｜另一个股东｜-｜1%｜普通股｜\n└")
    with pytest.raises(BoundedError, match="UNACCOUNTED"):
        project(raw(content, "股东研究"))
