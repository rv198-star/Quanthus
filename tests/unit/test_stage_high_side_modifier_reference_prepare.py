from __future__ import annotations

from quanthus.policy import find_project_root
from quanthus.stage_learning import high_side_modifier_reference_prepare as subject
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_trend_evolution import sequence
from .test_structure_v2_inputs import policy

ROOT = find_project_root()


def _candidate(
    *,
    cell: str,
    cell_rank: int,
    identity: str,
    identity_rank: int,
    window_id: str,
    start: str,
    end: str,
    window_rank: int = 1,
) -> dict[str, object]:
    era, board = cell.split("/")
    return {
        "security_id": identity,
        "security_code": identity,
        "listing_id": f"listing-{identity}",
        "era": era,
        "board_family": board,
        "board_code": board,
        "venue_id": "venue",
        "cell": cell,
        "window_id": window_id,
        "window_start": start,
        "window_end": end,
        "prior_exposure_class": "L0_METADATA_DISCOVERED",
        "cell_rank": cell_rank,
        "identity_rank": identity_rank,
        "window_rank": window_rank,
    }


def _ready(row: dict[str, object]) -> dict[str, object]:
    return {
        "security_id": row["security_id"],
        "window_id": row["window_id"],
        "qualified": True,
    }


def test_p026b_config_freezes_bounded_stage8_resource_and_reference_route() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    selection = config["resource_selection"]
    assert selection["target_admission_ready_rows_per_cell"] == 20
    assert selection["minimum_admission_ready_rows_per_cell"] == 16
    assert selection["maximum_candidate_identities_per_cell"] == 64
    assert selection["maximum_precommitted_windows_per_identity"] == 2
    assert selection["minimum_selected_rows"] == 144
    assert selection["minimum_unique_economic_identity_clusters"] == 128
    assert selection["no_post_result_replacement"] is True
    exposure = config["exposure_contract"]
    assert exposure["p014_admission_only_l2a_exact_window_reuse_allowed"] is True
    assert exposure["l2b_l3_l4_same_exact_window_reuse"] is False
    assert exposure["p020_exact_row_or_identity_window_reuse_as_fresh_stage8_evidence"] is False
    packet = config["packet_contract"]
    assert packet["security_identity_visible_to_reviewer"] is False
    assert packet["identity_cluster_visible_to_reviewer"] is False
    assert packet["stage9_activity_or_opinion_visible"] is False
    assert config["review_protocol"]["stage9_opinion_collection"] is False


def test_resource_selection_prefers_unique_identity_then_nonoverlap_cluster_repeat() -> None:
    cell_a = "ERA_A/MAIN"
    cell_b = "ERA_B/MAIN"
    shared_a = _candidate(
        cell=cell_a,
        cell_rank=1,
        identity="shared",
        identity_rank=1,
        window_id="shared-a",
        start="2019-01-01",
        end="2019-03-31",
    )
    unique_a = _candidate(
        cell=cell_a,
        cell_rank=1,
        identity="a-only",
        identity_rank=2,
        window_id="a-only",
        start="2019-04-01",
        end="2019-06-30",
    )
    shared_b = _candidate(
        cell=cell_b,
        cell_rank=2,
        identity="shared",
        identity_rank=1,
        window_id="shared-b",
        start="2020-01-01",
        end="2020-03-31",
    )
    unique_b = _candidate(
        cell=cell_b,
        cell_rank=2,
        identity="b-only",
        identity_rank=2,
        window_id="b-only",
        start="2020-04-01",
        end="2020-06-30",
    )
    reservoir = [shared_a, unique_a, shared_b, unique_b]
    selected = subject._select_resource_plan(
        reservoir=reservoir,
        readiness=[_ready(row) for row in reservoir],
        contract={
            "cell_order": [cell_a, cell_b],
            "target_admission_ready_rows_per_cell": 2,
        },
    )
    assert len(selected) == 4
    assert subject._selected_overlap_count(selected) == 0
    assert len({row["cluster_id"] for row in selected}) == 3
    roles = {str(row["window_id"]): str(row["selection_role"]) for row in selected}
    assert roles["shared-a"] == "GLOBAL_UNIQUE_FIRST"
    assert roles["b-only"] == "GLOBAL_UNIQUE_FIRST"
    assert roles["shared-b"] == "CROSS_CELL_NONOVERLAP_CLUSTER_REPEAT"


def test_resource_gate_counts_clusters_and_rejects_consumed_or_overlapping_windows() -> None:
    rows = [
        {
            **_candidate(
                cell="A/MAIN",
                cell_rank=1,
                identity="x",
                identity_rank=1,
                window_id="x-a",
                start="2019-01-01",
                end="2019-03-31",
            ),
            "cluster_id": subject._cluster_id("x"),
        },
        {
            **_candidate(
                cell="B/MAIN",
                cell_rank=2,
                identity="y",
                identity_rank=1,
                window_id="y-b",
                start="2020-01-01",
                end="2020-03-31",
            ),
            "cluster_id": subject._cluster_id("y"),
        },
    ]
    config = {
        "resource_selection": {"cell_order": ["A/MAIN", "B/MAIN"]},
        "resource_gate": {
            "minimum_rows_per_cell": 1,
            "minimum_total_rows": 2,
            "minimum_unique_identity_clusters": 2,
            "on_fail": "FAIL",
        },
    }
    passed = subject._resource_gate(
        selected=rows,
        consumed=set(),
        config=config,
        p015_subset_verified=True,
    )
    assert passed["passed"] is True
    failed = subject._resource_gate(
        selected=rows,
        consumed={("x", "x-a")},
        config=config,
        p015_subset_verified=True,
    )
    assert failed["passed"] is False
    assert failed["consumed_exact_window_overlap_count"] == 1


def test_stage8_packet_builder_exposes_only_opaque_t_only_trend_facts() -> None:
    values = sequence(61)
    admissions = tuple(admit_daily(item, policy()) for item in values)
    first, last = values[0], values[-1]
    row = {
        "security_id": first.security_id,
        "security_code": first.security_code,
        "listing_id": first.listing_id,
        "era": "TEST_ERA",
        "board_family": "MAIN",
        "cell": "TEST_ERA/MAIN",
        "window_id": "test-window",
        "window_start": first.market_date.isoformat(),
        "window_end": last.market_date.isoformat(),
        "prior_exposure_class": "L0_METADATA_DISCOVERED",
        "selection_role": "GLOBAL_UNIQUE_FIRST",
        "cluster_id": subject._cluster_id(first.security_id),
    }
    packet, mapping = subject._build_packet(
        row=row,
        admissions=admissions,
        plan_digest="sha256:" + "1" * 64,
    )
    assert packet["status"] == "AVAILABLE"
    assert len(packet["trend_fact_values"]) == 11
    assert {item["concept_id"] for item in packet["trend_fact_values"]} == set(subject.CONCEPTS)
    assert {
        "security_id",
        "security_code",
        "listing_id",
        "cluster_id",
        "window_id",
        "activity",
        "stage9",
        "candidate",
        "threshold",
        "future",
    }.isdisjoint(packet)
    assert mapping["case_id"] == packet["case_id"]
    assert mapping["cluster_id"].startswith("cluster_")
    assert mapping["selection_role"] == "GLOBAL_UNIQUE_FIRST"
