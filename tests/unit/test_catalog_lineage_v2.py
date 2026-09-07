from __future__ import annotations

import copy

import pytest

from quanthus.catalog_asset_lineage import focus, validate_asset_lineage
from quanthus.catalog_dependency import validate_dependency_graph
from quanthus.data_catalog import DataIndicatorCatalog


@pytest.fixture(scope="module")
def catalog() -> DataIndicatorCatalog:
    return DataIndicatorCatalog()


def test_data_lineage_contains_only_governed_ods_dwd_adm_assets(catalog: DataIndicatorCatalog) -> None:
    graph = catalog.snapshot["asset_lineage"]
    assert len(graph["nodes"]) == 94
    assert len(graph["edges"]) == 80
    kinds = {node["asset_kind"] for node in graph["nodes"]}
    assert kinds == {"ods_dataset", "dwd_table", "adm_dataset"}
    assert sum(node["asset_kind"] == "ods_dataset" for node in graph["nodes"]) == 57
    assert sum(node["asset_kind"] == "dwd_table" for node in graph["nodes"]) == 33
    assert sum(node["asset_kind"] == "adm_dataset" for node in graph["nodes"]) == 4
    assert not any(
        node["id"].startswith(
            ("process:", "projection:", "source-field:", "feature_spec:", "label_card:", "semantic_", "consumer:")
        )
        for node in graph["nodes"]
    )
    participating = {edge[side] for edge in graph["edges"] for side in ("source", "target")}
    assert all(
        node["id"] in participating
        for node in graph["nodes"]
        if node["asset_kind"] in {"dwd_table", "adm_dataset"}
    )


def test_adm_registry_promotes_only_explicit_stable_assets(catalog: DataIndicatorCatalog) -> None:
    datasets = {
        entry["canonical_id"]
        for entry in catalog.snapshot["entries"]
        if entry["kind"] == "adm_dataset"
    }
    assert datasets == {
        "p4-event-research-adm.candidate_daily",
        "p4-event-research-adm.static_industry_mapping",
        "structure-research-adm.rows",
        "structure-continuous-facts.rows",
    }
    assert "quanthus.structure-v2-adm.v1" not in datasets
    assert catalog.item("adm_dataset:p4-event-research-adm.candidate_daily")["details"]["member"] == "candidate_daily.parquet"
    assert catalog.item("adm_field:p4-event-research-adm.candidate_daily.close_price")["details"]["dataset_id"] == "p4-event-research-adm.candidate_daily"


def test_adjustment_effective_from_is_exact_ods_dwd_adm_field_chain(catalog: DataIndicatorCatalog) -> None:
    graph = catalog.relations(
        "dwd_field:dwd_adjustment_factor.effective_from",
        asset_scope="full",
        limit=100,
    )["asset_lineage"]
    assert graph is not None
    assert graph["coverage_level"] == "field_proven"
    assert graph["boundary_edges"] == 0 and graph["omitted_nodes"] == 0
    assert [node["layer"] for node in sorted(graph["nodes"], key=lambda item: item["distance"])] == ["ods", "dwd", "adm"]
    by_id = {node["id"]: node for node in graph["nodes"]}
    assert by_id["ods_dataset:adjustment_factors_pit"]["focus_fields"] == ["effective_date"]
    assert by_id["dwd_table:dwd_adjustment_factor"]["focus_fields"] == ["effective_from"]
    assert by_id["adm_dataset:p4-event-research-adm.candidate_daily"]["focus_fields"] == [
        "adjustment_effective",
        "factor_effective_from",
    ]
    assert {edge["source"] for edge in graph["edges"]} == {
        "ods_dataset:adjustment_factors_pit",
        "dwd_table:dwd_adjustment_factor",
    }
    assert not any(node["id"].startswith(("process:", "projection:", "source-field:")) for node in graph["nodes"])


def test_field_without_exact_mapping_falls_back_to_full_asset_context(catalog: DataIndicatorCatalog) -> None:
    graph = catalog.relations(
        "dwd_field:dwd_adjustment_factor.factor_value",
        asset_scope="full",
        limit=100,
    )["asset_lineage"]
    assert graph is not None
    assert graph["coverage_level"] == "asset_context_only"
    assert graph["boundary_edges"] == 0
    assert {node["id"] for node in graph["nodes"]} == {
        "ods_dataset:adjustment_factors_pit",
        "dwd_table:dwd_adjustment_factor",
        "adm_dataset:p4-event-research-adm.candidate_daily",
        "adm_dataset:p4-event-research-adm.static_industry_mapping",
    }
    assert next(node for node in graph["nodes"] if node["id"] == graph["center"])["focus_fields"] == ["factor_value"]
    assert all(edge["field_mappings"] == [] for edge in graph["edges"])


def test_security_bar_close_full_chain_reaches_all_registered_adm_consuming_assets(catalog: DataIndicatorCatalog) -> None:
    graph = catalog.relations("dwd_field:dwd_security_bar.close", asset_scope="full", limit=100)["asset_lineage"]
    assert graph is not None
    assert graph["coverage_level"] == "field_proven"
    assert graph["boundary_edges"] == 0
    assert {node["id"] for node in graph["nodes"]} == {
        "ods_dataset:daily_ohlcv_unadjusted",
        "dwd_table:dwd_security_bar",
        "adm_dataset:p4-event-research-adm.candidate_daily",
        "adm_dataset:structure-research-adm.rows",
        "adm_dataset:structure-continuous-facts.rows",
    }
    fields = {node["id"]: node["focus_fields"] for node in graph["nodes"]}
    assert fields["ods_dataset:daily_ohlcv_unadjusted"] == ["close"]
    assert fields["dwd_table:dwd_security_bar"] == ["close"]
    assert fields["adm_dataset:p4-event-research-adm.candidate_daily"] == ["close_price"]


def test_full_asset_chain_is_default_and_focus_hops_are_optional(catalog: DataIndicatorCatalog) -> None:
    full = catalog.relations("dwd_table:dwd_security_bar")["asset_lineage"]
    focused = catalog.relations("dwd_table:dwd_security_bar", asset_scope="focus", depth=1)["asset_lineage"]
    assert full is not None and focused is not None
    assert full["scope_mode"] == "full"
    assert focused["scope_mode"] == "focus"
    assert focused["depth"] == 1
    assert len(focused["nodes"]) <= len(full["nodes"])
    assert full["boundary_edges"] == 0


def test_indicator_and_consumers_are_separated_from_data_lineage(catalog: DataIndicatorCatalog) -> None:
    sma = catalog.relations("feature_spec:price.sma20@1.0.0")
    assert sma["asset_lineage"] is None
    definition = sma["definition_dependency"]
    assert definition is not None
    ids = {node["id"] for node in definition["nodes"]}
    assert "feature_spec:price.sma20@1.0.0" in ids
    assert "feature_spec:price.total_return_index@1.0.0" in ids
    assert not any(node_id.startswith(("process:", "projection:", "source-field:")) for node_id in ids)

    p4 = catalog.relations("adm_dataset:p4-event-research-adm.candidate_daily")
    asset_ids = {node["id"] for node in p4["asset_lineage"]["nodes"]}
    assert "consumer:p4-event-library" not in asset_ids
    usage = p4["usage_dependency"]
    assert usage is not None
    assert "consumer:p4-event-library" in {node["id"] for node in usage["nodes"]}


def test_dependency_graphs_have_no_engineering_process_nodes(catalog: DataIndicatorCatalog) -> None:
    for key in ("definition_dependency", "usage_dependency"):
        graph = catalog.snapshot[key]
        validate_dependency_graph(graph)
        assert not any(node["id"].startswith(("process:", "projection:", "source-field:")) for node in graph["nodes"])


def test_asset_graph_rejects_non_asset_node(catalog: DataIndicatorCatalog) -> None:
    graph = copy.deepcopy(catalog.snapshot["asset_lineage"])
    graph["nodes"].append({
        "id": "process:not-an-asset",
        "entry_id": None,
        "asset_kind": "process",
        "layer": "dwd",
        "label": "wrong",
        "technical_id": "wrong",
        "description": "wrong",
    })
    with pytest.raises(ValueError, match="non-asset"):
        validate_asset_lineage(graph)


def test_asset_focus_budget_and_bounds_fail_closed(catalog: DataIndicatorCatalog) -> None:
    entries = {entry["entry_id"]: entry for entry in catalog.snapshot["entries"]}
    with pytest.raises(ValueError, match="scope_invalid"):
        focus(catalog.snapshot["asset_lineage"], entries, "dwd_table:dwd_security_bar", scope="bad")
    with pytest.raises(ValueError, match="depth_invalid"):
        focus(catalog.snapshot["asset_lineage"], entries, "dwd_table:dwd_security_bar", depth=0)
    with pytest.raises(ValueError, match="limit_invalid"):
        focus(catalog.snapshot["asset_lineage"], entries, "dwd_table:dwd_security_bar", limit=9)
