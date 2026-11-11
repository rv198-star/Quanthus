import hashlib
from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base231_prelisting_financial import qualify_profile
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity
from quanthus.pipelines.securities_warehouse.listed_company import _stable_id
from tests.unit.test_base23_ownership_reconfirmation import fixture


def inputs():
    body = (
        "｜公司名称｜合成股份有限公司｜证券代码｜002001｜\n"
        "｜股份公司设立日期｜2018-01-01｜上市日期｜2022-01-04｜"
    )
    raw = fixture()
    raw.update(
        stock_code="002001",
        security_id="002001",
        category="公司概况",
        content=body,
        content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
    )
    parent = FinancialCompanyIdentity(
        stock_code="002001",
        security_id="security",
        company_id=_stable_id("company", "CNINFO_ORG_ID", "org"),
        effective_from=datetime(2022, 1, 3, 16, tzinfo=timezone.utc),
        effective_to=None,
    )
    return dict(
        raw=raw,
        raw_row_sha256=digest(raw),
        source_sha256="sha256:" + "a" * 64,
        issuer=dict(code="002001", orgId="org", category="A股"),
        parent=parent,
        observed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )


def test_explicit_profile_qualifies_only_financial_scope_without_inventing_listing():
    identity, company, proof = qualify_profile(**inputs())
    assert identity.effective_to == datetime(2022, 1, 3, 16, tzinfo=timezone.utc)
    assert company["effective_to"] is None
    assert identity.knowledge_from == datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert not proof["security_or_listing_extended"]
    assert not proof["historical_names_asserted"]


@pytest.mark.parametrize("fault", ["body", "date-missing", "org", "IPO", "future", "code"])
def test_static_code_or_company_name_cannot_replace_dated_legal_evidence(fault):
    kw = inputs()
    if fault in {"body", "date-missing", "IPO", "code"}:
        raw = kw["raw"]
        if fault == "body":
            raw["content"] += " changed"
        elif fault == "date-missing":
            raw["content"] = raw["content"].replace("股份公司设立日期", "成立年份")
        elif fault == "IPO":
            raw["content"] = raw["content"].replace("2022-01-04", "2022-01-05")
        else:
            raw["content"] = raw["content"].replace("002001", "002002")
        if fault != "body":
            raw["content_sha256"] = "sha256:" + hashlib.sha256(raw["content"].encode()).hexdigest()
            kw["raw_row_sha256"] = digest(raw)
    elif fault == "org":
        kw["issuer"]["orgId"] = "another"
    else:
        kw["observed_at"] = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with pytest.raises((RuntimeError, ValueError)):
        qualify_profile(**kw)


def test_only_reports_inside_explicit_prelisting_legal_interval_are_attributed():
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base231_financial_summary import load_contract
    from quanthus.pipelines.securities_warehouse.base231_prelisting_financial import (
        project_financial,
    )
    from tests.unit.test_base231_financial_summary import BODY

    kw = inputs()
    identity, _, _ = qualify_profile(**kw)
    raw = fixture()
    body = BODY.replace("2026-06-30", "2021-06-30").replace("2026-03-31", "2017-03-31")
    raw.update(
        stock_code="002001",
        security_id="002001",
        category="财务分析",
        content=body,
        content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
    )
    output, proof = project_financial(
        raw,
        identity=identity,
        source_sha256=kw["source_sha256"],
        source_revision="selected-source",
        observed_at=kw["observed_at"],
        contract=load_contract(Path("config/pipelines/base231-domains.v1.json")),
    )
    assert len(output["dwd_financial_report"]) == 1
    assert output["dwd_financial_report"][0]["period_end"].isoformat() == "2021-06-30"
    assert all(v["company_id"] == identity.company_id for v in output["dwd_financial_report"])
    assert not proof["historical_PIT_qualified"]
    assert (
        proof["classification_counts"]["held_parent_cells:BEFORE_FIRST_LISTED_COMPANY_IDENTITY"]
        == 3
    )
