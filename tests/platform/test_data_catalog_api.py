from __future__ import annotations

import urllib.parse

from .conftest import login, wsgi_request


def viewer_token(kernel) -> str:
    kernel.identity.create_user(
        username="catalog-viewer",
        password="Catalog-viewer-123",
        role="viewer",
    )
    return login(
        kernel,
        username="catalog-viewer",
        password="Catalog-viewer-123",
    )


def test_catalog_routes_require_authentication(kernel) -> None:
    status, _, payload = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/catalog/summary",
    )
    assert status == 401
    assert payload["error"]["reason"] == "authentication_required"


def test_viewer_can_read_catalog_summary_and_search(kernel) -> None:
    token = viewer_token(kernel)
    status, _, summary = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/catalog/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200
    assert summary["summary"]["entry_count"] == 760
    assert summary["summary"]["counts_by_kind"]["adm_dataset"] == 4
    assert summary["summary"]["counts_by_kind"]["adm_field"] == 76
    assert summary["summary"]["counts_by_kind"]["dwd_table"] == 33
    assert "gap_count" not in summary["summary"]
    assert "gaps" not in summary
    assert summary["lineage_overview"]["registered_edges"] > 0
    assert summary["asset_lineage_overview"]["registered_asset_count"] == 94
    assert summary["asset_lineage_overview"]["participating_asset_count"] == 70
    assert summary["asset_lineage_overview"]["edge_count"] == 80

    status, _, result = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/catalog/search?q=MA20&kind=feature_spec&limit=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200
    assert result["total"] == 1
    assert result["items"][0]["entry_id"] == "feature_spec:price.sma20@1.0.0"


def test_catalog_detail_and_relations_are_read_only_and_exact(kernel) -> None:
    token = login(kernel)
    entry_id = "feature_spec:price.sma20@1.0.0"
    encoded = urllib.parse.quote(entry_id, safe="")
    status, _, item = wsgi_request(
        kernel,
        "GET",
        f"/api/platform/v1/catalog/items/{encoded}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200
    assert item["canonical_id"] == "price.sma20@1.0.0"
    assert item["details"]["unit"] == "index"
    assert item["relations"] == [
        {
            "target_entry_id": "semantic_concept:indicator.trend.sma",
            "type": "related_not_equivalent",
        }
    ]

    status, _, relations = wsgi_request(
        kernel,
        "GET",
        f"/api/platform/v1/catalog/items/{encoded}/relations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200
    assert relations["entry_id"] == entry_id
    assert relations["outgoing"][0]["target"]["canonical_id"] == "indicator.trend.sma"

    status, _, _ = wsgi_request(
        kernel,
        "POST",
        f"/api/platform/v1/catalog/items/{encoded}",
        payload={"display_name": "mutate"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 404


def test_catalog_query_bounds_and_not_found_fail_closed(kernel) -> None:
    token = login(kernel)
    headers = {"Authorization": f"Bearer {token}"}
    for query in (
        "q=sma&q=macd",
        "unknown=x",
        "q=sma&limit=101",
        "q=sma&kind=indicator",
    ):
        status, _, payload = wsgi_request(
            kernel,
            "GET",
            f"/api/platform/v1/catalog/search?{query}",
            headers=headers,
        )
        assert status == 400
        assert payload["error"]["reason"].startswith("catalog_")

    missing = urllib.parse.quote("semantic_concept:not.real", safe="")
    status, _, payload = wsgi_request(
        kernel,
        "GET",
        f"/api/platform/v1/catalog/items/{missing}",
        headers=headers,
    )
    assert status == 404
    assert payload["error"]["reason"] == "catalog_entry_not_found"


def test_openapi_contains_only_catalog_get_routes(kernel) -> None:
    status, _, document = wsgi_request(kernel, "GET", "/openapi.json")
    assert status == 200
    catalog_paths = {
        path: methods
        for path, methods in document["paths"].items()
        if "/catalog/" in path
    }
    assert set(catalog_paths) == {
        "/api/platform/v1/catalog/summary",
        "/api/platform/v1/catalog/search",
        "/api/platform/v1/catalog/items/{entry_id}",
        "/api/platform/v1/catalog/items/{entry_id}/relations",
    }
    assert all(set(methods) == {"get"} for methods in catalog_paths.values())
