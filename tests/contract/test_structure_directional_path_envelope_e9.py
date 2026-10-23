from __future__ import annotations

import json
from pathlib import Path

from quanthus.structure_semantics import load_semantic_catalog
from quanthus.structure_v2_inputs import digest

ROOT = Path(__file__).resolve().parents[2]
E9 = ROOT / "config/structure-directional-path-envelope-e9-registration-result.v1.json"
E9_DIGEST = "sha256:3fadb9fa2c41d39951fdf3385f5d676623d806baeae6265af17bbd45105aa79c"
DELEGATED = ROOT / "config/stage-learning/stage-atomic-proposition-p012-delegated-authority.v1.json"
DELEGATED_DIGEST = "sha256:77b0d77aee4892dea3771ffec5544cdecc9984967297d0db1cb4f7fec627b90f"
CATALOG = ROOT / "config/structure-semantic-catalog.v1.json"
BLUEPRINT = ROOT / "config/structure-facts-v2-blueprint.v1.json"
SNAPSHOT = ROOT / "config/data-indicator-catalog-snapshot.v1.json"
CONCEPT = "structure.geometry.close-envelope-resolution-trace"


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_e9_registers_reserved_measurement_without_runtime_binding() -> None:
    result = load(E9)
    assert digest(result) == E9_DIGEST
    assert result["state"] == "REGISTERED_RESERVED_MEASUREMENT_NO_RUNTIME_BINDING"
    assert result["registration"]["concept_id"] == CONCEPT
    assert result["registration"]["result_kind"] == "measurement"
    assert result["registration"]["registration"] == "reserved"
    assert result["blueprint"]["semantic_runtime_binding_added"] is False
    assert result["authority"]["runtime_binding"] is False
    assert result["authority"]["market_data_read"] is False

    catalog = load_semantic_catalog(CATALOG)
    concept = catalog.resolve(CONCEPT)
    assert concept.result_kind == "measurement"
    assert concept.registration == "reserved"

    blueprint = load(BLUEPRINT)
    bindings = {
        item["concept_id"]: item for item in blueprint["semantic_runtime_bindings"]
    }
    assert CONCEPT not in bindings
    row = next(item for item in blueprint["capabilities"] if item["id"] == "B6-PATH")
    assert row["implementation_status"] == "implemented_unqualified"
    assert "registered" in row["v1_coverage"]
    assert "runtime accepted binding" in row["missing"]


def test_e9_catalog_snapshot_is_deterministic_additive_only() -> None:
    result = load(E9)
    snapshot = load(SNAPSHOT)
    assert snapshot["entry_count"] == 761
    assert snapshot["counts_by_kind"]["semantic_concept"] == 34
    assert snapshot["gap_count"] == 10
    # Active code-evidence SHA bindings changed with the prefix-safe P3 feature SQL.
    # The sealed E9 registration and semantic population remain unchanged.
    assert snapshot["snapshot_digest"] == (
        "sha256:8fd06da6f201b7f8d5ceb6553e7355fd4585c11e5d386ef298c314288ed70241"
    )
    assert result["registration"]["catalog_entry_count"] == 761
    assert result["registration"]["semantic_concept_count"] == 34
    assert result["registration"]["catalog_gap_count"] == 10


def test_p012_delegation_opens_f_without_waiving_hard_gates() -> None:
    authority = load(DELEGATED)
    assert digest(authority) == DELEGATED_DIGEST
    assert authority["state"] == "OWNER_DELEGATED_THROUGH_P012_CLOSEOUT"
    assert authority["delegated"]["open_next_subwork_after_current_gate_passes"] is True
    assert authority["delegated"]["freshness_inventory_and_preregistration"] is True
    assert authority["delegated"]["bounded_fresh_development_after_preregistration"] is True
    assert authority["delegated"]["expert_review_and_route_selection"] is True
    assert authority["hard_boundaries"]["do_not_rerun_completed_or_consumed_stages"] is True
    assert authority["hard_boundaries"]["do_not_rewrite_sealed_ledgers"] is True
    assert authority["hard_boundaries"]["do_not_relax_frozen_gates_after_results"] is True
    assert authority["hard_boundaries"]["preserved_2021_before_exact_freeze"] is False
    assert authority["hard_boundaries"]["permanent_blind_2025_access"] is False
    assert authority["hard_boundaries"]["daily_strategy_trading_activation"] is False

    result = load(E9)
    assert result["next_gate"]["work_item"] == "P3-STAGE-012-F"
    assert result["next_gate"]["state"] == "OPEN_UNDER_P012_DELEGATED_AUTHORITY"
