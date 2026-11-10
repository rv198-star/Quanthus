from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import canonical, digest
from quanthus.pipelines.securities_warehouse.base231_official_profile import SCOPE
from quanthus.pipelines.securities_warehouse.base231_origin_statement import (
    CONTRACT_ID,
    financial_origin_identity,
    origin_statement,
    replay_origin_statements,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity
from tests.unit.test_base231_current_statement import current


def fixture():
    raw, source, _, observed = current()
    parent = FinancialCompanyIdentity(
        stock_code=raw["stock_code"],
        company_id="company-1",
        security_id="security-1",
        effective_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
        effective_to=None,
    )
    company = dict(
        resolution_scope=SCOPE,
        company_type="official_source_scoped_financial_issuer",
        record_source="cninfo.company_profile.reported_origin",
        company_id=parent.company_id,
        effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        knowledge_from=datetime(2026, 10, 14, 1, tzinfo=timezone.utc),
    )
    return raw, source, observed, parent, company


def test_reuses_financial_origin_without_extending_security_or_known_time():
    raw, source, observed, parent, company = fixture()
    original = deepcopy((raw, company))
    output, proof = origin_statement(
        raw, source=source, company=company, parent=parent, observed_at=observed
    )
    report = output["dwd_financial_report"][0]
    assert report["company_id"] == parent.company_id
    assert report["knowledge_from"] == observed
    assert output["dwd_financial_fact"][0]["value_text"] == "1.00000000001"
    assert proof["security_or_listing_extended"] is False
    assert proof["historical_PIT_qualified"] is False
    assert (raw, company) == original
    identity = financial_origin_identity(company, parent)
    assert identity.effective_to == parent.effective_from
    assert identity.knowledge_from == company["knowledge_from"]


@pytest.mark.parametrize("fault", ["scope", "type", "source", "company", "date", "clock", "code"])
def test_unqualified_parent_or_alias_cannot_extend_report_identity(fault):
    raw, source, observed, parent, company = fixture()
    if fault == "scope":
        company["resolution_scope"] = "TRADING_HISTORY"
    elif fault == "type":
        company["company_type"] = "historical_legal_person"
    elif fault == "source":
        company["record_source"] = "unknown"
    elif fault == "company":
        company["company_id"] = "another-company"
    elif fault == "date":
        company["effective_from"] = parent.effective_from
    elif fault == "clock":
        company["knowledge_from"] = datetime(2026, 10, 15, tzinfo=timezone.utc)
    else:
        raw["stock_code"] = "000002"
    with pytest.raises(RuntimeError):
        origin_statement(raw, source=source, company=company, parent=parent, observed_at=observed)


@pytest.mark.parametrize("fault", ["purpose", "pit", "through", "extra"])
def test_repackaged_offline_plan_rejects_purpose_or_physical_set(tmp_path: Path, fault):
    plan = dict(
        contract_id=CONTRACT_ID,
        through_date="2026-09-30",
        complete_Base=False,
        historical_PIT_qualified=False,
        consumer_admission="NOT_GRANTED",
        members={},
    )
    if fault == "purpose":
        plan["consumer_admission"] = "GRANTED"
    elif fault == "pit":
        plan["historical_PIT_qualified"] = True
    elif fault == "through":
        plan["through_date"] = "2026-10-14"
    else:
        (tmp_path / "extra").write_bytes(b"unregistered")
    plan["identity"] = digest(plan)
    (tmp_path / "INPUTS.json").write_bytes(canonical(plan))
    with pytest.raises(RuntimeError):
        replay_origin_statements(tmp_path, tmp_path.parent / "unused-scratch")
