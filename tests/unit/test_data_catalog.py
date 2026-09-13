from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.data_catalog import (
    DataCatalogError,
    DataIndicatorCatalog,
    build_catalog,
    canonical_bytes,
    validate_catalog,
)
from quanthus.policy import find_project_root

ROOT = find_project_root()


def test_catalog_build_is_deterministic_and_matches_snapshot_manifest() -> None:
    first = build_catalog(ROOT / "config/data-indicator-catalog-sources.v1.json")
    second = build_catalog(ROOT / "config/data-indicator-catalog-sources.v1.json")
    assert canonical_bytes(first) == canonical_bytes(second)
    catalog = DataIndicatorCatalog()
    assert catalog.snapshot["snapshot_digest"] == first["snapshot_digest"]
    assert first["summary"] == {
        "entry_count": 761,
        "gap_count": 10,
        "counts_by_kind": {
            "adm_dataset": 4,
            "adm_field": 76,
            "dwd_field": 524,
            "dwd_table": 33,
            "feature_spec": 9,
            "label_card": 4,
            "ods_dataset": 57,
            "semantic_concept": 34,
            "semantic_family": 20,
        },
    }


def test_dwd_tables_include_inherited_time_and_extension_fields() -> None:
    catalog = DataIndicatorCatalog()
    for entry_id in (
        "dwd_field:dwd_security_bar.knowledge_from",
        "dwd_field:dwd_security_bar.observation_start",
        "dwd_field:dwd_shareholding_observation.holder_rank",
    ):
        item = catalog.item(entry_id)
        assert item["kind"] == "dwd_field"
        assert item["verification_status"] == "accepted"
    table = catalog.item("dwd_table:dwd_security_bar")
    assert table["details"]["time_profile"] == "market_observation"
    assert table["accepted_scope"] == "dwd-base-202607-v2.1"


def test_ods_catalog_does_not_fabricate_missing_package_field_schema() -> None:
    catalog = DataIndicatorCatalog()
    item = catalog.item("ods_dataset:daily_ohlcv_unadjusted")
    assert item["verification_status"] == "contract_only"
    assert item["details"]["package_membership_proven_in_checkout"] is False
    gaps = {item["gap_id"]: item for item in catalog.diagnostics()["gaps"]}
    assert "gap:ods-package-manifest-not-in-checkout" in gaps


def test_sma20_is_total_return_index_spec_not_raw_price_alias() -> None:
    catalog = DataIndicatorCatalog()
    item = catalog.item("feature_spec:price.sma20@1.0.0")
    assert item["details"]["unit"] == "index"
    assert "price.total_return_index" in item["details"]["formula"]
    assert any("不得当作原始收盘价" in note for note in item["details"]["cautions"])
    assert item["relations"] == [
        {
            "type": "related_not_equivalent",
            "target_entry_id": "semantic_concept:indicator.trend.sma",
        }
    ]
    assert catalog.search("MA20")["items"][0]["entry_id"] == item["entry_id"]


def test_rsi14_retains_existing_formula_and_indicator_concepts_do_not_gain_false_specs() -> None:
    catalog = DataIndicatorCatalog()
    rsi = catalog.item("feature_spec:price.rsi14@1.0.0")
    assert "mean(max(total_return, 0), 14)" in rsi["details"]["formula"]
    assert any("Wilder RSI" in note for note in rsi["details"]["cautions"])
    gaps = {gap["gap_id"] for gap in catalog.diagnostics()["gaps"]}
    for concept_id in (
        "indicator.trend.sma",
        "indicator.oscillator.rsi",
        "indicator.momentum.macd",
    ):
        concept = catalog.item(f"semantic_concept:{concept_id}")
        assert concept["lifecycle"] == "reserved"
        assert concept["implementation_status"] == "declared_only"
        assert concept["verification_status"] == "contract_only"
        assert f"gap:no-executable-spec:{concept_id}" in gaps


def test_structure_runtime_bindings_project_only_blueprint_accepted_concepts() -> None:
    catalog = DataIndicatorCatalog()
    accepted = {
        "structure.anchor.rolling-high": ("B3", "bounded_retrospective_b1_b5"),
        "structure.anchor.rolling-low": ("B3", "bounded_retrospective_b1_b5"),
        "structure.geometry.signed-distance": ("B3", "bounded_retrospective_b1_b5"),
        "structure.interaction.close-cross-above": ("B4", "bounded_retrospective_b1_b5"),
        "structure.interaction.close-cross-below": ("B4", "bounded_retrospective_b1_b5"),
        "structure.interaction.close-reclaim": ("B4", "bounded_retrospective_b1_b5"),
        "structure.episode.reclaim-sequence": ("B5", "bounded_retrospective_b1_b5"),
        "structure.geometry.post-break-rebound": (
            "B5-F9",
            "bounded_retrospective_f9_support_process",
        ),
        "structure.episode.support-failure-sequence": (
            "B5-F9",
            "bounded_retrospective_f9_support_process",
        ),
        "structure.geometry.window-return": (
            "B6-TREND",
            "bounded_retrospective_trend_evolution",
        ),
        "structure.geometry.directional-efficiency": (
            "B6-TREND",
            "bounded_retrospective_trend_evolution",
        ),
        "structure.geometry.return-velocity": (
            "B6-TREND",
            "bounded_retrospective_trend_evolution",
        ),
        "structure.geometry.velocity-delta": (
            "B6-TREND",
            "bounded_retrospective_trend_evolution",
        ),
        "structure.geometry.range-position": (
            "B6-TREND",
            "bounded_retrospective_trend_evolution",
        ),
        "structure.geometry.drawdown-from-window-high": (
            "B6-TREND",
            "bounded_retrospective_trend_evolution",
        ),
        "structure.geometry.rebound-from-window-low": (
            "B6-TREND",
            "bounded_retrospective_trend_evolution",
        ),
        "structure.geometry.range-width": (
            "B6-ORDER",
            "bounded_retrospective_structure_order",
        ),
        "structure.geometry.width-change": (
            "B6-ORDER",
            "bounded_retrospective_structure_order",
        ),
        "structure.geometry.range-center-shift": (
            "B6-ORDER",
            "bounded_retrospective_structure_order",
        ),
    }
    for concept_id, (capability_id, accepted_scope) in accepted.items():
        item = catalog.item(f"semantic_concept:{concept_id}")
        assert item["implementation_status"] == "implemented"
        assert item["verification_status"] == "accepted"
        assert item["accepted_scope"] == accepted_scope
        assert item["details"]["runtime_binding"]["capability_id"] == capability_id
    for concept_id in (
        "structure.anchor.confirmed-pivot",
        "structure.geometry.close-envelope-resolution-trace",
    ):
        planned = catalog.item(f"semantic_concept:{concept_id}")
        assert planned["implementation_status"] == "declared_only"
        assert planned["verification_status"] == "contract_only"
        assert "runtime_binding" not in planned["details"]


def test_turnover_field_is_not_silently_promoted_to_turnover_rate() -> None:
    catalog = DataIndicatorCatalog()
    item = catalog.item("dwd_field:dwd_security_bar.turnover")
    assert item["details"]["type"] == "decimal_38_10?"
    assert any("不是换手率" in note for note in item["details"]["cautions"])
    result = catalog.search("换手率", limit=10)
    assert {value["entry_id"] for value in result["items"]} == {
        item["entry_id"],
        "dwd_field:dwd_index_bar.turnover",
        "adm_field:p4-event-research-adm.candidate_daily.turnover",
    }
    turnover = catalog.item("adm_field:p4-event-research-adm.candidate_daily.turnover")
    assert "成交金额" in turnover["description"]


def test_relations_return_outgoing_and_incoming_without_dangling_targets() -> None:
    catalog = DataIndicatorCatalog()
    relation = catalog.relations("semantic_concept:indicator.trend.sma")
    assert any(
        item["source"]["entry_id"] == "feature_spec:price.sma20@1.0.0"
        and item["type"] == "related_not_equivalent"
        for item in relation["incoming"]
    )
    table_relation = catalog.relations("dwd_table:dwd_security_bar")
    assert any(item["type"] == "contains" for item in table_relation["outgoing"])
    assert any(item["type"] == "field_of" for item in table_relation["incoming"])


def test_search_filters_and_bounds_fail_closed() -> None:
    catalog = DataIndicatorCatalog()
    result = catalog.search("", kind="semantic_concept", domain="indicator", status="reserved")
    assert result["total"] >= 1
    assert all(item["kind"] == "semantic_concept" for item in result["items"])
    with pytest.raises(DataCatalogError, match="catalog_query_invalid"):
        catalog.search("x" * 121)
    with pytest.raises(DataCatalogError, match="catalog_limit_invalid"):
        catalog.search("sma", limit=101)
    with pytest.raises(DataCatalogError, match="catalog_kind_invalid"):
        catalog.search("sma", kind="indicator")


def test_catalog_rejects_dangling_relation_even_when_snapshot_digest_is_recomputed() -> None:
    snapshot = build_catalog(ROOT / "config/data-indicator-catalog-sources.v1.json")
    mutated = json.loads(json.dumps(snapshot))
    mutated["entries"][0]["relations"].append(
        {"type": "references", "target_entry_id": "dwd_table:not-real"}
    )
    from quanthus.data_catalog import digest

    mutated["snapshot_digest"] = digest(
        {key: value for key, value in mutated.items() if key != "snapshot_digest"}
    )
    with pytest.raises(DataCatalogError, match="dangling"):
        validate_catalog(mutated)


def test_catalog_annotation_cannot_create_unknown_entry(tmp_path: Path) -> None:
    source_manifest = json.loads(
        (ROOT / "config/data-indicator-catalog-sources.v1.json").read_text()
    )
    annotation_source = ROOT / "config/data-indicator-catalog-annotations.v1.json"
    annotations = json.loads(annotation_source.read_text())
    annotations["annotations"].append(
        {
            "entry_id": "feature_spec:not.real@1.0.0",
            "display_name": "not real",
            "search_terms": ["not real"],
            "cautions": ["not real"],
        }
    )
    replacement = tmp_path / "annotations.json"
    replacement.write_text(json.dumps(annotations), encoding="utf-8")
    rewritten = []
    for source in source_manifest["sources"]:
        if source["adapter"] == "catalog_annotations":
            rewritten.append({"adapter": "catalog_annotations", "path": str(replacement)})
        else:
            rewritten.append(source)
    source_manifest["sources"] = rewritten
    manifest_path = tmp_path / "sources.json"
    manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    # Absolute paths are intentionally refused by the production source manifest contract.
    with pytest.raises(DataCatalogError):
        build_catalog(manifest_path)
