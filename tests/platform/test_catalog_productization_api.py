from __future__ import annotations

import urllib.parse

import pytest

from .conftest import wsgi_request
from .test_data_catalog_api import viewer_token


@pytest.mark.parametrize("query", [
    "depth=0", "depth=6", "limit=9", "limit=101", "depth=x", "depth=2&depth=3", "asset_scope=x", "unknown=x",
])
def test_lineage_rejects_invalid_query(kernel, query):
    token = viewer_token(kernel)
    encoded = urllib.parse.quote("dwd_field:dwd_security_bar.close", safe="")
    path = f"/api/platform/v1/catalog/items/{encoded}/relations?{query}"
    status, _, payload = wsgi_request(
        kernel, "GET", path, headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 400
    assert payload["error"]["reason"].startswith("catalog_")


def test_adjustment_table_api_exposes_distinct_exact_source_identities(kernel):
    token = viewer_token(kernel)
    headers = {"Authorization": f"Bearer {token}"}
    encoded = urllib.parse.quote("dwd_table:dwd_adjustment_factor", safe="")
    status, _, payload = wsgi_request(
        kernel, "GET", f"/api/platform/v1/catalog/items/{encoded}/relations?depth=2&limit=60",
        headers=headers,
    )
    assert status == 200
    lineage = payload["lineage"]
    identities = {node["technical_id"] for node in lineage["nodes"]}
    assert {"adjustment_factors_pit", "adjustment_evidence_delta",
            "factor_evidence_delta", "adjustment_evidence"} <= identities
    assert not any(node["process"] for node in lineage["nodes"])


def test_adjustment_effective_from_api_exposes_field_proof_and_table_fallback(kernel):
    token = viewer_token(kernel)
    headers = {"Authorization": f"Bearer {token}"}
    proven = urllib.parse.quote("dwd_field:dwd_adjustment_factor.effective_from", safe="")
    status, _, payload = wsgi_request(
        kernel, "GET",
        f"/api/platform/v1/catalog/items/{proven}/relations?depth=5&limit=100",
        headers=headers,
    )
    assert status == 200
    lineage = payload["lineage"]
    assert lineage["coverage_level"] == "field_proven"
    assert lineage["table_context"] is None
    assert any(node["id"] == "source-field:adjustment_factors_pit.effective_date"
               for node in lineage["nodes"])
    assert any(node["id"] == "projection:p4-event.factor_effective_from"
               for node in lineage["nodes"])
    asset = payload["asset_lineage"]
    assert asset["coverage_level"] == "field_proven"
    assert asset["boundary_edges"] == 0
    assert {node["asset_kind"] for node in asset["nodes"]} == {"ods_dataset", "dwd_table", "adm_dataset"}
    assert {node["technical_id"] for node in asset["nodes"]} == {
        "adjustment_factors_pit",
        "dwd_adjustment_factor",
        "p4-event-research-adm.candidate_daily",
    }

    fallback = urllib.parse.quote("dwd_field:dwd_adjustment_factor.factor_value", safe="")
    status, _, payload = wsgi_request(
        kernel, "GET", f"/api/platform/v1/catalog/items/{fallback}/relations",
        headers=headers,
    )
    assert status == 200
    lineage = payload["lineage"]
    assert lineage["coverage_level"] == "table_context_only"
    assert lineage["edges"] == []
    assert lineage["table_context"]["edges"]
    asset = payload["asset_lineage"]
    assert asset["coverage_level"] == "asset_context_only"
    assert asset["boundary_edges"] == 0
    assert all(edge["field_mappings"] == [] for edge in asset["edges"])


def test_asset_lineage_defaults_to_full_chain_and_separates_definition_usage(kernel):
    token = viewer_token(kernel)
    headers = {"Authorization": f"Bearer {token}"}
    encoded = urllib.parse.quote("dwd_field:dwd_security_bar.close", safe="")
    status, _, payload = wsgi_request(
        kernel, "GET", f"/api/platform/v1/catalog/items/{encoded}/relations?limit=100",
        headers=headers,
    )
    assert status == 200
    asset = payload["asset_lineage"]
    assert asset["scope_mode"] == "full"
    assert asset["boundary_edges"] == 0
    assert not any(node["id"].startswith(("process:", "projection:", "consumer:")) for node in asset["nodes"])
    assert payload["definition_dependency"] is not None

    adm = urllib.parse.quote("adm_dataset:p4-event-research-adm.candidate_daily", safe="")
    status, _, payload = wsgi_request(kernel, "GET", f"/api/platform/v1/catalog/items/{adm}/relations", headers=headers)
    assert status == 200
    assert "consumer:p4-event-library" in {node["id"] for node in payload["usage_dependency"]["nodes"]}
    assert "consumer:p4-event-library" not in {node["id"] for node in payload["asset_lineage"]["nodes"]}


def test_graph_is_authenticated_readonly_and_supports_declared_projection(kernel):
    encoded = urllib.parse.quote("projection:daily-return", safe="")
    path = f"/api/platform/v1/catalog/items/{encoded}/relations?depth=3&limit=20"
    assert wsgi_request(kernel, "GET", path)[0] == 401
    token = viewer_token(kernel)
    headers = {"Authorization": f"Bearer {token}"}
    status, _, payload = wsgi_request(kernel, "GET", path, headers=headers)
    assert status == 200 and len(payload["lineage"]["nodes"]) <= 20
    assert payload["lineage"]["center"] == "projection:daily-return"
    assert wsgi_request(kernel, "POST", path, headers=headers, payload={})[0] == 404
    missing = "/api/platform/v1/catalog/items/projection:invented/relations"
    assert wsgi_request(kernel, "GET", missing, headers=headers)[0] == 404
