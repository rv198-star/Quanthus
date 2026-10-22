from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_industry import snapshot_observation


def fixture():
    early = datetime(2026, 7, 31, tzinfo=timezone.utc)
    raw = dict(stock_code="600001", security_id="600001", stock_name="synthetic company",
               market_date="2026-09-01", industry_code="C01", industry_name="synthetic-sector",
               classification="证监会行业分类", acquired_at="2026-09-02T01:42:04+08:00",
               provider_update_date="2026-07-27", source="baostock.query_stock_industry",
               source_revision="fixture-source-v1")
    node = dict(industry_node_id="node-one", node_code="C01", node_name="synthetic-sector",
                effective_from=early, effective_to=None, knowledge_from=early, knowledge_to=None)
    kwargs = dict(day=date(2026, 9, 1), node=node,
                  parent=SimpleNamespace(company_id="company-one", knowledge_from=early),
                  observed_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
                  source_sha256="sha256:" + "a" * 64, revision="retained-weekly-v2")
    return raw, kwargs


def test_declared_date_actual_governance_clock_and_original_capture_are_separate():
    raw, kwargs = fixture()
    before = deepcopy(raw)
    row, proof = snapshot_observation(raw, **kwargs)
    assert raw == before
    assert row["source_declared_as_of"] == date(2026, 9, 1)
    assert row["effective_from"] == datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
    assert row["knowledge_from"] == kwargs["observed_at"]
    assert proof["actual_acquisition_instant"] == datetime(2026, 9, 1, 17, 42, 4,
                                                          tzinfo=timezone.utc)
    assert proof["original_provider_update_date"] == "2026-07-27"
    assert not proof["historical_PIT_qualified"] and not proof["native_history_rewritten"]


@pytest.mark.parametrize("defect", [
    "extra-field", "different-code", "date", "october", "classification", "source",
    "node-name", "node-start", "node-knowledge", "parent", "future-capture", "precision",
])
def test_unproven_source_identity_node_or_clocks_fail_closed_without_mutating_input(defect):
    raw, kw = fixture()
    if defect == "extra-field":
        raw["extra"] = "not ignored"
    elif defect == "different-code":
        raw["security_id"] = "600002"
    elif defect == "date":
        raw["market_date"] = "2026-09-05"
    elif defect == "october":
        raw["market_date"] = "2026-10-01"
        kw["day"] = date(2026, 10, 1)
    elif defect == "classification":
        raw["classification"] = "not the reviewed scheme"
    elif defect == "source":
        raw["source"] = "other-provider"
    elif defect == "node-name":
        kw["node"]["node_name"] = "other"
    elif defect == "node-start":
        kw["node"]["effective_from"] = kw["observed_at"]
    elif defect == "node-knowledge":
        kw["node"]["knowledge_to"] = kw["observed_at"]
    elif defect == "parent":
        kw["parent"] = None
    elif defect == "future-capture":
        raw["acquired_at"] = "2026-10-10T00:00:00+08:00"
    else:
        raw["acquired_at"] = "2026-09-02T01:42:04.1234567+08:00"
    before = deepcopy(raw)
    with pytest.raises(BoundedError):
        snapshot_observation(raw, **kw)
    assert raw == before
