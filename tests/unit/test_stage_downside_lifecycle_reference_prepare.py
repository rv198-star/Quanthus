from __future__ import annotations

from quanthus.policy import find_project_root
from quanthus.stage_learning import downside_lifecycle_reference_prepare as subject
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_trend_evolution import sequence
from .test_structure_v2_inputs import policy

ROOT = find_project_root()


def _panel_row(values):
    first = values[0]
    last = values[-1]
    return {
        "security_id": first.security_id,
        "security_code": first.security_code,
        "listing_id": first.listing_id,
        "era": "TEST_ERA",
        "board_family": "MAIN",
        "cell": "TEST_ERA/MAIN",
        "window_id": "test-window",
        "window_start": first.market_date.isoformat(),
        "window_end": last.market_date.isoformat(),
    }


def test_p020c_config_freezes_cluster_aware_blind_packet_contract() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    panel = config["resource_panel"]
    assert panel["row_count"] == 187
    assert panel["unique_identity_cluster_count"] == 179
    assert panel["repeated_identity_cluster_count"] == 8
    assert panel["repeated_rows_not_independent"] is True
    assert panel["replacement_or_resource_expansion"] is False
    assert panel["additional_admission_read"] is False
    packet = config["packet_contract"]
    assert packet["attempt_every_panel_row"] is True
    assert packet["security_identity_visible_to_reviewer"] is False
    assert packet["identity_cluster_visible_to_reviewer"] is False
    assert packet["candidate_output_visible"] is False
    assert packet["future_rows_visible"] is False
    review = config["review_protocol"]
    assert review["roles"] == ["short_term", "fund"]
    assert review["roles_isolated_from_each_other"] is True
    assert review["opinion_collection_before_packet_freeze"] is False
    gate = config["reference_sufficiency_gate"]
    assert gate["independence_unit"] == "economic_identity_cluster"
    assert gate["minimum_stage12_definite_positive_clusters"] == 20
    assert gate["minimum_stage12_definite_negative_clusters"] == 20
    assert gate["minimum_stage1_definite_positive_clusters"] == 20
    assert gate["minimum_stage1_definite_negative_clusters"] == 20


def test_packet_builder_exposes_only_opaque_t_only_trend_facts() -> None:
    values = sequence(61)
    admissions = tuple(admit_daily(item, policy()) for item in values)
    row = _panel_row(values)
    packet, mapping = subject._build_packet(
        row=row,
        admissions=admissions,
        plan_digest="sha256:" + "1" * 64,
    )
    assert packet["status"] == "AVAILABLE"
    assert packet["knowledge_cutoff"] == values[-1].market_date.isoformat()
    assert packet["era"] == "TEST_ERA"
    assert packet["board_family"] == "MAIN"
    assert len(packet["trend_fact_values"]) == 11
    assert {item["concept_id"] for item in packet["trend_fact_values"]} == set(subject.CONCEPTS)
    assert packet["fact_availability_reasons"] == []
    assert {
        "security_id",
        "security_code",
        "listing_id",
        "cluster_id",
        "window_id",
        "window_start",
        "candidate",
        "threshold",
        "future",
    }.isdisjoint(packet)
    assert mapping["case_id"] == packet["case_id"]
    assert mapping["security_id"] == values[0].security_id
    assert mapping["security_code"] == values[0].security_code
    assert mapping["cluster_id"].startswith("cluster_")
    assert mapping["packet_status"] == "AVAILABLE"


def test_packet_builder_retains_unavailable_case_without_replacement() -> None:
    values = sequence(20)
    admissions = tuple(admit_daily(item, policy()) for item in values)
    row = _panel_row(values)
    packet, mapping = subject._build_packet(
        row=row,
        admissions=admissions,
        plan_digest="sha256:" + "2" * 64,
    )
    assert packet["status"] == "UNAVAILABLE"
    assert packet["trend_fact_values"] == []
    assert packet["fact_availability_reasons"]
    assert mapping["packet_status"] == "UNAVAILABLE"
    assert mapping["packet_reason"] != "available"
