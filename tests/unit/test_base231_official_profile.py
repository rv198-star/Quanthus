from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base231_official_profile import (
    project_origin_financial,
    qualify_origin,
)
from tests.unit.test_base231_prelisting_financial import inputs


def source():
    original = inputs()
    return dict(
        body=("合成股份有限公司          交易信息\n代码：002001 ISIN：\n"
              "成立日期 2010/01/02 邮箱 office@example.invalid\n"
              "上市日期 2022/01/04 电话 -\n业务概况 (更新日期：2026/09/30)"),
        code="002001", expected_name="合成股份有限公司", issuer=original["issuer"],
        parent=original["parent"], pdf_sha256="sha256:" + "a" * 64,
        document_uri="https://static.cninfo.com.cn/finalpage/enpage/002001_1.pdf",
        received_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        observed_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc),
    )


def test_reported_origin_bounds_current_financial_scope_without_legal_or_listing_assertion():
    identity, company, proof = qualify_origin(**source())
    assert identity.effective_from.isoformat() == "2010-01-01T16:00:00+00:00"
    assert identity.effective_to == source()["parent"].effective_from
    assert company["effective_to"] is None
    assert company["resolution_scope"] == "CURRENT_FINANCIAL_LITERAL_SOURCE_REPORTED_ORIGIN_ONLY"
    assert proof["original_provider_acquisition"] is None
    assert proof["legal_form_date_qualified"] is False
    assert proof["historical_PIT_qualified"] is False
    assert proof["security_or_listing_extended"] is False


@pytest.mark.parametrize("fault", ["code", "name", "IPO", "origin", "duplicate", "URI",
                                    "org", "future", "receipt", "hash"])
def test_origin_requires_exact_source_issuer_dates_and_new_observation(fault):
    kw = source()
    if fault == "code":
        kw["body"] = kw["body"].replace("代码：002001", "代码：002002")
    elif fault == "name":
        kw["expected_name"] = "另一股份有限公司"
    elif fault == "IPO":
        kw["body"] = kw["body"].replace("2022/01/04", "2022/01/05")
    elif fault == "origin":
        kw["body"] = kw["body"].replace("成立日期", "业务年份")
    elif fault == "duplicate":
        kw["body"] += "\n成立日期 2011/01/02 邮箱 -"
    elif fault == "URI":
        kw["document_uri"] = "https://example.invalid/002001_1.pdf"
    elif fault == "org":
        kw["issuer"]["orgId"] = "different"
    elif fault == "future":
        kw["body"] = kw["body"].replace("2010/01/02", "2027/01/02")
    elif fault == "receipt":
        kw["received_at"] = datetime(2027, 1, 1, tzinfo=timezone.utc)
    else:
        kw["pdf_sha256"] = "sha256:bad"
    with pytest.raises((RuntimeError, ValueError)):
        qualify_origin(**kw)


def test_current_origin_financial_view_excludes_periods_before_reported_origin():
    import hashlib
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base231_financial_summary import load_contract
    from tests.unit.test_base23_ownership_reconfirmation import fixture
    from tests.unit.test_base231_financial_summary import BODY

    kw = source()
    identity, _, qualification = qualify_origin(**kw)
    raw = fixture()
    body = BODY.replace("2026-06-30", "2021-06-30").replace("2026-03-31", "2009-03-31")
    raw.update(stock_code="002001", security_id="002001", category="财务分析", content=body,
               content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest())
    out, proof = project_origin_financial(
        raw, identity=identity, qualification=qualification, source_sha256=kw["pdf_sha256"],
        source_revision="original-financial-source", observed_at=kw["observed_at"],
        contract=load_contract(Path("config/pipelines/base231-domains.v1.json")),
    )
    assert len(out["dwd_financial_report"]) == 1
    assert out["dwd_financial_report"][0]["period_end"].isoformat() == "2021-06-30"
    assert proof["origin_scope"] == "CURRENT_FINANCIAL_LITERAL_SOURCE_REPORTED_ORIGIN_ONLY"
    assert proof["legal_form_date_qualified"] is False
    assert proof["historical_PIT_qualified"] is False
    qualification["company_id"] = "different"
    with pytest.raises(RuntimeError):
        project_origin_financial(
            raw, identity=identity, qualification=qualification,
            source_sha256=kw["pdf_sha256"], source_revision="original-financial-source",
            observed_at=kw["observed_at"],
            contract=load_contract(Path("config/pipelines/base231-domains.v1.json")),
        )


@pytest.mark.parametrize("industry", ["", "所属行业:       制造业\n"])
@pytest.mark.parametrize("parentheses", [False, True])
def test_explicit_profile_header_industry_and_parenthesis_typography(industry, parentheses):
    kw = source()
    name = "合成(上海)股份有限公司" if parentheses else kw["expected_name"]
    native = name.replace("(", "（").replace(")", "）")
    kw.update(expected_name=name, body=kw["body"].replace(
        "合成股份有限公司          交易信息\n", native + "          交易信息\n" + industry))
    _, _, proof = qualify_origin(**kw)
    if industry or parentheses:
        assert proof["header_layout_contract"] == "explicit-profile-header-typography-v1"
        assert proof["header_name_typography_only"] is parentheses
    assert proof["historical_PIT_qualified"] is False


@pytest.mark.parametrize("fault", ["subsidiary", "different", "duplicate", "unlabelled",
                                    "two_industry"])
def test_profile_header_fallback_cannot_borrow_an_unrelated_name(fault):
    kw = source()
    name = kw["expected_name"]
    if fault == "subsidiary":
        prefix = name + "          交易信息\n子公司：另一股份有限公司\n"
    elif fault == "different":
        prefix = "另一股份有限公司          交易信息\n所属行业: 制造业\n"
    elif fault == "duplicate":
        prefix = name + "          交易信息\n" + name + "          交易信息\n所属行业: 制造业\n"
    elif fault == "unlabelled":
        prefix = name + "          交易信息\n制造业\n"
    else:
        prefix = name + "          交易信息\n所属行业: 制造业\n所属行业: 金融业\n"
    kw["body"] = kw["body"].replace(name + "          交易信息\n", prefix)
    with pytest.raises(RuntimeError, match="EXACT_ORIGINAL_NAME"):
        qualify_origin(**kw)
