from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict, deque

import pytest

from quanthus.catalog_lineage import focus, validate_lineage
from quanthus.catalog_presentation import apply_presentation
from quanthus.data_catalog import DataCatalogError, DataIndicatorCatalog, build_catalog
from quanthus.policy import find_project_root

ROOT = find_project_root()
ZH = re.compile(r"[\u3400-\u9fff]")


@pytest.fixture(scope="module")
def catalog():
    return DataIndicatorCatalog()


def test_every_existing_entry_has_reviewed_chinese_reading(catalog):
    assert len(catalog.snapshot["entries"]) == 760
    for entry in catalog.snapshot["entries"]:
        assert ZH.search(entry["display_name"]), entry["entry_id"]
        assert ZH.search(entry["description"]), entry["entry_id"]
        assert not entry["description"].startswith("Field `")
        reading = entry["details"]["reading"]
        assert reading["facts"]
        assert all(ZH.search(item["label"]) for item in reading["facts"])
        expected_authority = (
            "config/catalog-adm-assets.v1.json"
            if entry["kind"] in {"adm_dataset", "adm_field"}
            else "config/catalog-business-metadata.v1.json"
        )
        assert reading["metadata_authority"] == expected_authority


def test_reading_never_grants_new_identity_formula_or_status(catalog):
    metadata = json.loads((ROOT / "config/catalog-business-metadata.v1.json").read_text())
    entries = {item["entry_id"]: copy.deepcopy(item) for item in catalog.snapshot["entries"]}
    metadata["objects"]["not.real@1"] = ["虚构名称", "这一项不能创建新条目。"]
    original = copy.deepcopy(entries)
    apply_presentation(entries, metadata)
    assert entries.keys() == original.keys()
    protected = ("entry_id", "canonical_id", "authority", "relations", "lifecycle",
                 "implementation_status", "verification_status", "accepted_scope")
    for key, entry in entries.items():
        for field in protected:
            assert entry[field] == original[key][field]
        for field in ("formula", "unit", "lookback_sessions", "minimum_history_sessions"):
            assert entry["details"].get(field) == original[key]["details"].get(field)


def test_unreviewed_field_cannot_fall_back_to_english(catalog):
    metadata = json.loads((ROOT / "config/catalog-business-metadata.v1.json").read_text())
    del metadata["fields"]["close"]
    entries = {"x": copy.deepcopy(catalog.item("dwd_field:dwd_security_bar.close"))}
    with pytest.raises(ValueError, match="metadata missing"):
        apply_presentation(entries, metadata)


def test_business_summary_is_not_a_maintenance_dashboard(catalog):
    summary = catalog.summary()
    assert "gaps" not in summary and "gap_count" not in summary["summary"]
    assert catalog.diagnostics()["gap_count"] == 10
    macd = catalog.item("semantic_concept:indicator.momentum.macd")
    assert macd["implementation_status"] == "declared_only"
    assert "暂无" in json.dumps(macd["details"]["reading"], ensure_ascii=False)


def test_directional_path_geometry_concepts_have_reviewed_chinese_readings(catalog):
    traversal = catalog.item("semantic_concept:structure.geometry.range-traversal")
    counter = catalog.item(
        "semantic_concept:structure.geometry.counter-direction-excursion-share"
    )
    assert "区间穿越" in traversal["display_name"]
    assert "价格重定位" in traversal["description"]
    assert "反向偏离" in counter["display_name"]
    assert "未来反转" in counter["description"]


def test_price_index_volume_and_time_units_are_not_guessed(catalog):
    price = catalog.item("dwd_field:dwd_security_bar.close")
    index = catalog.item("dwd_field:dwd_index_bar.close")
    assert "未复权" in price["description"]
    assert "指数点位" in index["description"]
    volume = catalog.item("dwd_field:dwd_security_bar.volume")
    assert "不能擅自认定" in volume["description"]
    knowledge = catalog.item("dwd_field:dwd_security_bar.knowledge_from")
    assert "不因此证明" in knowledge["description"]
    sma = catalog.item("feature_spec:price.sma20@1.0.0")
    assert any("完整研究管道" in text for text in sma["details"]["reading"]["cautions"])


def reachable(graph, source, target):
    adjacency = defaultdict(set)
    for edge in graph["edges"]:
        adjacency[edge["source"]].add(edge["target"])
    queue, seen = deque([source]), {source}
    while queue:
        current = queue.popleft()
        if current == target:
            return True
        for item in adjacency[current] - seen:
            seen.add(item)
            queue.append(item)
    return False


def test_real_field_flow_is_directed_and_not_membership(catalog):
    graph = catalog.snapshot["lineage"]
    source = "source-field:daily_ohlcv_unadjusted.close"
    field = "dwd_field:dwd_security_bar.close"
    sma = "feature_spec:price.sma20@1.0.0"
    assert reachable(graph, source, field)
    assert reachable(graph, field, sma)
    assert not reachable(graph, sma, field)
    assert not reachable(graph, "dwd_table:dwd_security_bar", field)
    assert not reachable(graph, sma, "semantic_concept:indicator.trend.sma")
    assert not {"contains", "field_of", "related_not_equivalent"} & {
        edge["role"] for edge in graph["edges"]
    }


def test_adjustment_factor_table_focus_shows_exact_source_identities_without_generic_boxes(catalog):
    lineage = catalog.relations("dwd_table:dwd_adjustment_factor", depth=2, limit=60)["lineage"]
    identities = {node["technical_id"] for node in lineage["nodes"]}
    assert {
        "dwd_adjustment_factor",
        "adjustment_factors_pit",
        "adjustment_evidence_delta",
        "factor_evidence_delta",
        "adjustment_evidence",
    } <= identities
    assert not any(node["process"] for node in lineage["nodes"])
    source_labels = [node["label"] for node in lineage["nodes"] if node["layer"] == "source"]
    assert len(source_labels) == len(set(source_labels))
    assert all("复权因子来源 · 日发布工件" not in label for label in source_labels)
    table = "dwd_table:dwd_adjustment_factor"
    assert all(edge["target"] == table for edge in lineage["edges"])


def test_adjustment_factor_effective_from_has_exact_field_lineage(catalog):
    graph = catalog.snapshot["lineage"]
    field = "dwd_field:dwd_adjustment_factor.effective_from"
    source = "source-field:adjustment_factors_pit.effective_date"
    assert reachable(graph, source, field)
    assert reachable(graph, field, "dwd_field:dwd_adjustment_factor.effective_to")
    assert reachable(graph, field, "dwd_field:dwd_adjustment_factor_input.effective_to")
    assert reachable(graph, field, "projection:p4-event.factor_effective_from")
    assert reachable(graph, field, "projection:p4-event.adjustment_effective")
    lineage = catalog.relations(field, depth=5, limit=100)["lineage"]
    assert lineage["coverage_level"] == "field_proven"
    assert lineage["table_context"] is None
    assert len(lineage["edges"]) >= 5


def test_every_dwd_field_is_explicitly_field_proven_or_gets_table_context(catalog):
    summary = catalog.summary()["lineage_overview"]["field_coverage"]
    assert summary == {
        "total": 524,
        "field_proven": 10,
        "table_context_only": 514,
        "unregistered": 0,
    }
    for entry in catalog.snapshot["entries"]:
        if entry["kind"] != "dwd_field":
            continue
        lineage = catalog.relations(entry["entry_id"], depth=1, limit=20)["lineage"]
        assert lineage["coverage_level"] in {"field_proven", "table_context_only"}
        if lineage["coverage_level"] == "field_proven":
            assert lineage["edges"], entry["entry_id"]
            assert lineage["table_context"] is None
        else:
            assert lineage["edges"] == [], entry["entry_id"]
            assert lineage["table_context"] is not None, entry["entry_id"]
            assert lineage["table_context"]["edges"], entry["entry_id"]
            assert "不代表该字段" in lineage["table_context"]["message"]


def test_table_context_fallback_never_becomes_a_fake_field_edge(catalog):
    field = "dwd_field:dwd_adjustment_factor.factor_value"
    lineage = catalog.relations(field, depth=2, limit=60)["lineage"]
    assert lineage["coverage_level"] == "table_context_only"
    assert lineage["nodes"][0]["id"] == field and lineage["edges"] == []
    context = lineage["table_context"]
    assert context is not None
    assert context["center"] == "dwd_table:dwd_adjustment_factor"
    assert context["edges"]
    assert all(edge["source"] != field and edge["target"] != field for edge in context["edges"])


def test_dwd_coverage_uses_only_explicit_modeled_contracts(catalog):
    graph = catalog.snapshot["lineage"]
    targets = {edge["target"] for edge in graph["edges"]}
    assert len({key for key in targets if key.startswith("dwd_table:")}) == 33
    sources = {edge["source"] for edge in graph["edges"]}
    assert "ods_dataset:stock_list" not in sources
    assert "ods_dataset:daily_ohlcv_adjusted" not in sources
    assert "不是某次运行" in graph["coverage"]


def test_v2_uses_exact_decimal_members_and_declared_consumer(catalog):
    graph = catalog.snapshot["lineage"]
    consumer = "consumer:structure-v2-compatibility"
    assert reachable(graph, "projection:daily_ohlcv_none.dwd_close_decimal", consumer)
    assert not reachable(graph, "projection:daily_ohlcv_none.close", consumer)
    assert "非运行审计" in graph["scope"]


def test_lineage_evidence_files_and_model_pointers_match(catalog):
    definition = json.loads((ROOT / "config/catalog-lineage.v1.json").read_text())
    for evidence in definition["evidence"].values():
        path = ROOT / evidence["path"]
        assert "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == evidence["sha256"]
        assert evidence["locator"] in path.read_text()
    for edge in catalog.snapshot["lineage"]["edges"]:
        evidence = edge["evidence"]
        path = ROOT / evidence["path"]
        assert "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == evidence["sha256"]
        if evidence["locator"].startswith("/"):
            value = json.loads(path.read_text())
            for segment in evidence["locator"].split("/")[1:]:
                segment = segment.replace("~1", "/").replace("~0", "~")
                value = value[int(segment)] if isinstance(value, list) else value[segment]
            assert value is not None


@pytest.mark.parametrize("depth,limit", [(0, 60), (6, 60), (True, 60), (2, 9), (2, 101), (2, True)])
def test_focus_bounds_are_not_silently_ignored(catalog, depth, limit):
    with pytest.raises(DataCatalogError, match="catalog_lineage"):
        catalog.relations("dwd_field:dwd_security_bar.close", depth=depth, limit=limit)


def test_focus_is_immutable_bounded_and_recenterable(catalog):
    graph = catalog.snapshot["lineage"]
    original = copy.deepcopy(graph)
    result = focus(graph, "dwd_field:dwd_security_bar.close", depth=5, limit=10)
    assert len(result["nodes"]) <= 10
    assert result["nodes"][0]["id"] == result["center"]
    assert result["boundary_edges"] > 0 and result["omitted_nodes"] > 0
    external = catalog.relations("projection:daily-return")["lineage"]
    assert external["center"] == "projection:daily-return"
    assert catalog.relations("process:total-return")["lineage"]["edges"]
    assert graph == original
    with pytest.raises(DataCatalogError, match="catalog_entry_not_found"):
        catalog.relations("projection:invented")


@pytest.mark.parametrize("mutation", ["cycle", "dangling", "membership"])
def test_invalid_graphs_are_rejected(catalog, mutation):
    graph = copy.deepcopy(catalog.snapshot["lineage"])
    edge = graph["edges"][0]
    if mutation == "cycle":
        graph["edges"].append({**edge, "source": edge["target"], "target": edge["source"]})
    elif mutation == "dangling":
        edge["target"] = "not-real"
    else:
        edge["role"] = "field_of"
    with pytest.raises(ValueError):
        validate_lineage(graph)


def test_packaging_contains_presentation_and_lineage_closure():
    script = (ROOT / "ops/bin/build-platform-image").read_text()
    dockerfile = (ROOT / "ops/platform/Dockerfile").read_text()
    for path in (
        "config/catalog-business-metadata.v1.json",
        "config/catalog-lineage.v1.json",
        "config/catalog-adm-assets.v1.json",
        "config/catalog-asset-lineage.v2.json",
        "config/p4-research-adm.v1.json",
        "config/structure-research-adm.mvp.v1.json",
        "config/structure-continuous-facts.research.v1.json",
        "src/quanthus/catalog_presentation.py",
        "src/quanthus/catalog_lineage.py",
        "src/quanthus/catalog_asset_lineage.py",
        "src/quanthus/catalog_dependency.py",
    ):
        assert path in script and path in dockerfile
    for restricted in ("src/quanthus/backtest", "src/quanthus/event_library"):
        assert restricted not in script and restricted not in dockerfile
    adm = json.loads((ROOT / "config/catalog-adm-assets.v1.json").read_text())
    asset = json.loads((ROOT / "config/catalog-asset-lineage.v2.json").read_text())
    assert all(str(value).startswith("sha256:") for value in adm["evidence_sha256"].values())
    assert all(str(value).startswith("sha256:") for value in asset["evidence_sha256"].values())


def test_source_inventory_and_semantic_ids_stay_exact(catalog):
    built = build_catalog()
    assert built["snapshot_digest"] == catalog.snapshot["snapshot_digest"]
    assert built["summary"]["entry_count"] == 760
    assert built["summary"]["counts_by_kind"]["semantic_concept"] == 33
    assert built["summary"]["counts_by_kind"]["adm_dataset"] == 4
    assert built["summary"]["counts_by_kind"]["adm_field"] == 76
    assert built["summary"]["counts_by_kind"]["dwd_field"] == 524
    plan = json.loads((ROOT / "config/data-indicator-catalog-productization.v1.json").read_text())
    expected = ["P3-DICT-002", *[f"P3-DICT-002-{c}" for c in "ABCD"]]
    assert [item["id"] for item in plan["items"]] == expected
    assert (ROOT / plan["design"]).is_file()
