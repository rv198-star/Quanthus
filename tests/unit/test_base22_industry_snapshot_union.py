from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22_industry_snapshot_union import project_snapshot


def case():
    early = datetime(2026, 7, 31, tzinfo=timezone.utc)
    raw = dict(stock_code="600001", market_date="2026-08-01", industry_code="C01",
               industry_name="synthetic-sector", classification="证监会行业分类",
               acquired_at="2026-08-01T10:41:25+08:00", provider_update_date="2026-07-27")
    node = dict(industry_node_id="node-one", node_code="C01", node_name="synthetic-sector",
                effective_from=early, effective_to=None, knowledge_from=early, knowledge_to=None)
    parent = SimpleNamespace(company_id="company-one", knowledge_from=early)
    resolver = SimpleNamespace(resolve_for_report=lambda code, period_end: (parent, None))
    kwargs = dict(day=date(2026, 8, 1), resolver=resolver, nodes=[node],
                  source_sha256="sha256:" + "a" * 64, revision="synthetic-weekly-v2")
    return [raw], kwargs


def test_only_source_declared_snapshot_date_and_retained_acquisition_are_projected():
    raw, kwargs = case()
    before = deepcopy(raw)
    rows = project_snapshot(raw, **kwargs)
    assert raw == before and len(rows) == 1
    row = rows[0]
    assert row["source_declared_as_of"] == date(2026, 8, 1)
    assert row["effective_from"] == datetime(2026, 7, 31, 16, tzinfo=timezone.utc)
    assert row["knowledge_from"] == datetime(2026, 8, 1, 2, 41, 25, tzinfo=timezone.utc)
    assert row["effective_to"] is None and row["confidence"] is None
    assert row["company_id"] == "company-one" and row["industry_node_id"] == "node-one"


@pytest.mark.parametrize("defect,reason", [
    ("empty", "EXACT_SOURCE_KEY_CENSUS"),
    ("duplicate", "EXACT_SOURCE_KEY_CENSUS"),
    ("september", "BUSINESS_SCOPE"),
    ("raw-date", "DATE_OR_CLASSIFICATION"),
    ("classification", "DATE_OR_CLASSIFICATION"),
    ("missing-node", "NODE_CODE_NAME"),
    ("node-name", "NODE_CODE_NAME"),
    ("duplicate-node", "NODE_VERSION_NOT_UNIQUE"),
    ("missing-identity", "DATED_COMPANY_UNAVAILABLE"),
    ("closed-node", "NODE_TIME_CONFLICT"),
    ("unknown-acquisition", "OBSERVATION_CLOCK_UNKNOWN"),
])
def test_date_identity_classification_clock_or_node_gaps_fail_closed(defect, reason):
    raw, kwargs = case()
    if defect == "empty":
        raw = []
    elif defect == "duplicate":
        raw *= 2
    elif defect == "september":
        kwargs["day"] = date(2026, 9, 1)
    elif defect == "raw-date":
        raw[0]["market_date"] = "2026-07-31"
    elif defect == "classification":
        raw[0]["classification"] = "other"
    elif defect == "missing-node":
        kwargs["nodes"] = []
    elif defect == "node-name":
        kwargs["nodes"][0]["node_name"] = "other"
    elif defect == "duplicate-node":
        kwargs["nodes"] *= 2
    elif defect == "missing-identity":
        kwargs["resolver"] = SimpleNamespace(
            resolve_for_report=lambda code, period_end: (None, "gap")
        )
    elif defect == "closed-node":
        kwargs["nodes"][0]["effective_to"] = datetime(2026, 7, 31, tzinfo=timezone.utc)
    else:
        raw[0]["acquired_at"] = None
    before = deepcopy(raw)
    with pytest.raises(RuntimeError, match=reason):
        project_snapshot(raw, **kwargs)
    assert raw == before


def test_business_date_never_defaults_to_the_provider_update_date():
    raw, kwargs = case()
    raw[0]["market_date"] = "2026-08-08"
    kwargs["day"] = date(2026, 8, 8)
    row = project_snapshot(raw, **kwargs)[0]
    assert row["effective_from"] == datetime(2026, 8, 7, 16, tzinfo=timezone.utc)
    assert row["source_declared_as_of"] == date(2026, 8, 8)
    assert raw[0]["provider_update_date"] == "2026-07-27"
