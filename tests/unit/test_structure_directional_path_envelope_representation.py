from __future__ import annotations

from quanthus.structure_directional_path_envelope import assess_close_path_envelope
from quanthus.structure_directional_path_envelope_representation import (
    CONCEPT_VERSION,
    CONTRACT_DIGEST,
    RESULT_KIND,
    as_catalog_measurement,
    load_contract,
    measurement_value,
)
from quanthus.structure_v2_inputs import digest

FAILED_RESOLUTION = [
    10.00, 10.10, 9.90, 10.05, 9.95, 10.08, 9.92, 10.04, 9.96, 10.02,
    10.40, 10.00, 9.60, 10.00, 10.50, 10.00, 9.50, 10.00, 10.45, 10.00, 9.55,
]


def test_representation_contract_is_measurement_only_and_unregistered() -> None:
    contract = load_contract()
    assert digest(contract) == CONTRACT_DIGEST
    assert contract["concept"]["result_kind"] == "measurement"
    assert contract["concept"]["registration_status"] == "PROPOSED_NOT_REGISTERED"
    assert contract["authority"]["catalog_registration"] is False
    assert contract["authority"]["catalog_type_system_change"] is False
    assert contract["authority"]["algorithm_change"] is False


def test_catalog_measurement_wraps_e5_semantics_without_loss() -> None:
    trace = assess_close_path_envelope(FAILED_RESOLUTION)
    source = trace.to_dict()
    value = measurement_value(trace)
    assert value == {
        "status": source["status"],
        "reason": source["reason"],
        "profile": source["profile"],
        "events": source["events"],
        "descriptors": source["descriptors"],
    }
    wrapped = as_catalog_measurement(trace)
    assert wrapped["concept_id"] == "structure.geometry.close-envelope-resolution-trace"
    assert wrapped["concept_version"] == CONCEPT_VERSION
    assert wrapped["result_kind"] == RESULT_KIND == "measurement"
    assert wrapped["value"] == value
    assert wrapped["value_digest"] == digest(value)
    assert wrapped["registration_status"] == "PROPOSED_NOT_REGISTERED"


def test_trace_parts_remain_explanation_not_independent_registered_facts() -> None:
    wrapped = as_catalog_measurement(assess_close_path_envelope(FAILED_RESOLUTION))
    claims = wrapped["claims"]
    assert claims["events_are_independent_registered_facts"] is False
    assert claims["descriptors_are_independent_registered_facts"] is False
    assert claims["stage_projection"] is None
    assert claims["compact_disorder_truth"] is None
    assert claims["relocation_truth"] is None
    assert claims["registered_fact"] is False
