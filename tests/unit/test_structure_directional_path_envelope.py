from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from quanthus.stage_learning.range_envelope_prototype import assess_close_envelope
from quanthus.structure_directional_path_envelope import (
    CONTRACT_DIGEST,
    DirectionalPathEnvelopeError,
    assess_close_path_envelope,
    assess_daily_segment,
    load_contract,
)
from quanthus.structure_v2_inputs import digest

from .test_structure_v2_inputs import row

ORDERLY = [
    10.00, 10.10, 9.90, 10.08, 9.92, 10.06, 9.94, 10.04, 9.96, 10.02,
    9.98, 10.01, 9.99, 10.00, 9.98, 10.01, 9.99, 10.00, 9.99, 10.00, 10.00,
]
FAILED = [
    10.00, 10.10, 9.90, 10.05, 9.95, 10.08, 9.92, 10.04, 9.96, 10.02,
    10.40, 10.00, 9.60, 10.00, 10.50, 10.00, 9.50, 10.00, 10.45, 10.00, 9.55,
]


def _rows(values):
    return [
        row(
            index,
            close=None if value is None else str(value),
            high=None if value is None else Decimal(str(value)) + Decimal("1"),
            low=None if value is None else Decimal(str(value)) - Decimal("1"),
        )
        for index, value in enumerate(values)
    ]


def test_contract_is_structure_owned_but_not_registered():
    contract = load_contract()
    assert digest(contract) == CONTRACT_DIGEST
    assert contract["owner"]["blueprint_id"] == "B6-PATH"
    assert contract["concept_proposal"]["registration_status"] == "PROPOSED_NOT_REGISTERED"
    assert contract["authority"]["fact_registration"] is False
    assert contract["authority"]["semantic_catalog_registration"] is False
    assert contract["authority"]["existing_b6_path_contract_change"] is False
    assert contract["authority"]["market_data_read"] is False
    assert contract["authority"]["stage_projection"] is False


def test_failed_resolution_trace_is_two_sided_and_alternating():
    observed = assess_close_path_envelope(FAILED).to_dict()
    assert observed["status"] == "AVAILABLE"
    assert observed["descriptors"]["escape_count"] == 4
    assert observed["descriptors"]["resolved_escape_count"] == 4
    assert observed["descriptors"]["resolved_escape_side_sequence"] == [
        "UPPER", "LOWER", "UPPER", "LOWER"
    ]
    assert observed["descriptors"]["resolved_escape_side_alternation_count"] == 3
    assert observed["descriptors"]["resolved_escape_both_sides"] is True
    assert observed["claims"]["compact_disorder_truth"] is None
    assert observed["claims"]["stage_projection"] is None


def test_high_churn_inside_envelope_is_not_escape_by_construction():
    observed = assess_close_path_envelope(ORDERLY).to_dict()
    assert observed["status"] == "AVAILABLE"
    assert observed["descriptors"]["escape_count"] == 0


def test_positive_scale_preserves_geometry():
    base = assess_close_path_envelope(FAILED).to_dict()["descriptors"]
    scaled = assess_close_path_envelope(
        [Decimal(str(value)) * Decimal("100") for value in FAILED]
    ).to_dict()["descriptors"]
    for key in (
        "escape_count",
        "resolved_escape_count",
        "unresolved_escape_count",
        "resolved_escape_side_sequence",
        "resolved_escape_side_alternation_count",
        "resolved_escape_both_sides",
        "unresolved_escape_sides",
    ):
        assert base[key] == scaled[key]


def test_equality_is_inside_and_t_escape_remains_unresolved():
    equality = [
        10.00, 10.10, 9.90, 10.05, 9.95, 10.08, 9.92, 10.04, 9.96, 10.02,
        10.10, 10.00, 10.05, 9.95, 10.00, 10.02, 9.98, 10.01, 9.99, 10.00, 10.00,
    ]
    assert assess_close_path_envelope(equality).to_dict()["descriptors"]["escape_count"] == 0

    t_escape = [
        10.00, 10.10, 9.90, 10.05, 9.95, 10.08, 9.92, 10.04, 9.96, 10.02,
        10.00, 10.01, 9.99, 10.00, 10.02, 9.98, 10.01, 9.99, 10.00, 10.00, 10.40,
    ]
    observed = assess_close_path_envelope(t_escape).to_dict()
    assert observed["descriptors"]["resolved_escape_count"] == 0
    assert observed["descriptors"]["unresolved_escape_sides"] == ["UPPER"]


def test_missing_and_short_history_fail_closed():
    missing = list(ORDERLY)
    missing[10] = None
    observed = assess_close_path_envelope(missing).to_dict()
    assert observed["status"] == "UNAVAILABLE"
    assert observed["reason"] == "close_input_unavailable"

    short = assess_close_path_envelope(ORDERLY[:-1]).to_dict()
    assert short["status"] == "INSUFFICIENT_HISTORY"
    assert short["reason"] == "primary_history_requires_21_sessions"

    with pytest.raises(DirectionalPathEnvelopeError, match="exact_21_sessions"):
        assess_close_path_envelope(ORDERLY + [10.0])


def test_daily_segment_reuses_b6_path_reset_semantics():
    rows = _rows(ORDERLY)
    assert assess_daily_segment(rows).status == "AVAILABLE"

    changed = list(rows)
    changed[-1] = replace(changed[-1], listing_id="listing-B", coordinate_epoch="coord-B")
    result = assess_daily_segment(changed)
    assert result.status == "INSUFFICIENT_HISTORY"
    assert result.reason == "listing_or_coordinate_change_requires_rewarm"

    gap = list(rows)
    gap[-1] = replace(gap[-1], session_index=99)
    result = assess_daily_segment(gap)
    assert result.status == "INSUFFICIENT_HISTORY"
    assert result.reason == "hidden_session_gap_requires_rewarm"


def test_stage_e2_compatibility_adapter_preserves_frozen_descriptors():
    stage = assess_close_envelope(FAILED).to_dict()
    structure = assess_close_path_envelope(FAILED).to_dict()
    for key in (
        "escape_count",
        "resolved_escape_count",
        "unresolved_escape_count",
        "resolved_escape_side_sequence",
        "resolved_escape_side_alternation_count",
        "resolved_escape_both_sides",
        "unresolved_escape_sides",
    ):
        assert stage["descriptors"][key] == structure["descriptors"][key]
