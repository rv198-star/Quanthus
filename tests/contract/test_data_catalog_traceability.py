from __future__ import annotations

import json

from quanthus.data_catalog import DataIndicatorCatalog, build_catalog, file_sha256
from quanthus.policy import find_project_root

ROOT = find_project_root()


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_catalog_source_manifest_is_explicit_complete_and_digest_bound() -> None:
    sources = load("config/data-indicator-catalog-sources.v1.json")
    expected_adapters = {
        "release_identity",
        "ods_contract",
        "dwd_domain_model",
        "dwd_extension",
        "feature_registry",
        "feature_registry_extension",
        "label_registry",
        "semantic_catalog",
        "structure_blueprint",
        "catalog_annotations",
        "catalog_business",
        "catalog_lineage",
        "adm_registry",
        "asset_lineage",
    }
    observed = {item["adapter"] for item in sources["sources"]}
    assert observed == expected_adapters
    assert len(observed) == len(sources["sources"])
    for item in sources["sources"]:
        path = ROOT / item["path"]
        assert path.is_file(), item
        assert file_sha256(path).startswith("sha256:")


def test_catalog_snapshot_manifest_exactly_matches_authority_projection() -> None:
    snapshot = build_catalog(ROOT / "config/data-indicator-catalog-sources.v1.json")
    manifest = load("config/data-indicator-catalog-snapshot.v1.json")
    assert manifest == {
        "schema_version": "quanthus.data-indicator-catalog-snapshot-manifest.v1",
        "catalog_schema_version": snapshot["schema_version"],
        "builder_version": snapshot["builder_version"],
        "source_manifest_sha256": snapshot["source_manifest"]["sha256"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "entry_count": snapshot["summary"]["entry_count"],
        "gap_count": snapshot["summary"]["gap_count"],
        "counts_by_kind": snapshot["summary"]["counts_by_kind"],
    }
    DataIndicatorCatalog()


def test_catalog_platform_feature_is_read_only_and_has_no_migration() -> None:
    manifest = load("config/platform-features.v1.json")
    feature = next(
        item
        for item in manifest["features"]
        if item["id"] == "quanthus-data-catalog-v1"
    )
    assert feature["enabled"] is True
    assert feature["source_capabilities"] == []
    assert feature["startup_hooks"] == []
    assert feature["migrations"] == []
    assert feature["web_routes"] == ["/catalog"]
    assert feature["routes"] == [
        "quanthus.catalog.summary.read",
        "quanthus.catalog.search.read",
        "quanthus.catalog.item.read",
        "quanthus.catalog.relations.read",
    ]
    api = (ROOT / "src/quanthus/platform/api.py").read_text(encoding="utf-8")
    assert "/api/platform/v1/catalog/summary" in api
    assert "/api/platform/v1/catalog/search" in api
    assert "/api/platform/v1/catalog/items/<entry_id>" in api
    catalog_registration = api.split("quanthus.catalog.summary.read", 1)[1].split(
        "quanthus.product.candidate.read", 1
    )[0]
    assert '"POST",\n            self._' not in catalog_registration


def test_catalog_web_and_issue_plan_are_traced_to_one_design() -> None:
    plan = load("config/data-indicator-catalog-delivery.v1.json")
    assert plan["parent_id"] == "P3-DICT-001"
    assert plan["design"] == "docs/design/data-indicator-catalog.md"
    assert [item["id"] for item in plan["items"]] == [
        "P3-DICT-001",
        "P3-DICT-001-A",
        "P3-DICT-001-B",
        "P3-DICT-001-C",
        "P3-DICT-001-D",
        "P3-DICT-001-E",
    ]
    assert (ROOT / "web/app/catalog/page.tsx").is_file()
    assert (ROOT / "web/app/catalog-browser.tsx").is_file()
    assert (ROOT / "docs/design/data-indicator-catalog.md").is_file()
    assert (ROOT / "docs/work-items/data-indicator-dictionary.md").is_file()


def test_catalog_annotations_are_non_authoritative_and_cannot_grant_equivalence() -> None:
    annotations = load("config/data-indicator-catalog-annotations.v1.json")
    assert annotations["authority"] == "catalog_search_and_caution_annotations_only"
    expected_fields = {"entry_id", "display_name", "search_terms", "cautions"}
    assert all(set(item) == expected_fields for item in annotations["annotations"])
    semantic = load("config/structure-semantic-catalog.v1.json")
    assert all(
        item["relationship"] == "related_not_equivalent"
        for item in semantic["legacy_links"]
    )
