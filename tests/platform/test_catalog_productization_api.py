from __future__ import annotations

import urllib.parse

import pytest

from .conftest import wsgi_request
from .test_data_catalog_api import viewer_token


@pytest.mark.parametrize("query", [
    "depth=0", "depth=6", "limit=9", "limit=101", "depth=x", "depth=2&depth=3", "unknown=x",
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
