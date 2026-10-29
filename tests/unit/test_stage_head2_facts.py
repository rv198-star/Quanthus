"""The new forty-session entry point must retain existing fact identities."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from quanthus.stage_learning.head2_facts import build_head2_window_facts
from quanthus.stage_learning.head2_revision import encode_reference_facts
from quanthus.structure_directional_path_geometry import build_directional_path_geometry_facts
from quanthus.structure_order_facts import CONCEPTS, build_structure_order_facts
from quanthus.structure_trend_evolution import DIRECTIONAL_EFFICIENCY, build_trend_evolution_facts
from quanthus.structure_v2_inputs import Admission, admit_daily

from .test_structure_order_facts import sequence
from .test_structure_v2_inputs import policy, row


def admissions(count=40):
    return tuple(admit_daily(value, policy()) for value in sequence(count))


def build(values=None):
    values = admissions() if values is None else values
    return build_head2_window_facts(values, anchor=values[-1].row.market_date)


def test_exact40_matches_legacy_measurements_including_revisions():
    full = admissions(70)
    anchor = full[-1].row.market_date
    result = build(full[-40:])
    expected = []
    for builder in (build_structure_order_facts, build_trend_evolution_facts):
        facts = builder(full, report_start=anchor, report_end=anchor)
        expected.extend(
            item for item in facts.measurements
            if item.window_sessions == 20 and (
                item.concept_id == DIRECTIONAL_EFFICIENCY
                or item.concept_id in CONCEPTS
            )
        )
    # Match by concepts, not ordering or count alone.
    by_concept = {item.concept_id: item for item in expected}
    assert len(result.primary) == 4
    assert {m.concept_id for m in result.primary} == set(by_concept)
    assert all(m.to_dict() == by_concept[m.concept_id].to_dict() for m in result.primary)
    paths = build_directional_path_geometry_facts(full, report_start=anchor, report_end=anchor)
    assert {m.concept_id: m.to_dict() for m in result.path} == {
        m.concept_id: m.to_dict() for m in paths.measurements
    }
    assert result.available


def test_no_sixty_session_warmup_and_no_source_mutation():
    selected = admissions()
    before = [asdict(v) for v in selected]
    result = build(selected)
    assert result.available and result.reason == "available"
    primary, path = result.reference_facts()
    encoded = encode_reference_facts(
        primary, path, anchor=selected[-1].row.market_date.isoformat(),
        source_digest=result.digest,
    )
    assert encoded.values is not None
    assert before == [asdict(v) for v in selected]
    assert build(selected).digest == result.digest


@pytest.mark.parametrize("count", [1, 20, 39, 41, 60])
def test_exact_frozen_count_required(count):
    with pytest.raises(ValueError, match="EXACT40"):
        build(admissions(count))


@pytest.mark.parametrize("field,value", [
    ("security_code", "000002"), ("security_id", "other-security"),
    ("listing_id", "other-listing"), ("exchange", "SSE"),
    ("session_index", 55), ("market_date", date(2020, 1, 1)),
])
def test_identity_or_session_failure_is_rejected(field, value):
    selected = list(admissions())
    selected[20] = replace(selected[20], row=replace(selected[20].row, **{field: value}))
    with pytest.raises(ValueError, match="IDENTITY|SEQUENCE"):
        build(selected)


def test_duplicate_or_reordered_days_are_not_silently_sorted():
    selected = list(admissions())
    selected[20], selected[21] = selected[21], selected[20]
    with pytest.raises(ValueError, match="SEQUENCE"):
        build(selected)


def test_anchor_and_protected_future_scope_rejected():
    selected = admissions()
    with pytest.raises(ValueError, match="ANCHOR"):
        build_head2_window_facts(selected, anchor=selected[-1].row.market_date + timedelta(days=1))
    with pytest.raises(ValueError, match="DATE_SCOPE"):
        build_head2_window_facts(selected, anchor=date(2022, 1, 1))


def test_action_reset_inside_prior20_is_unavailable_not_label_or_adjustment():
    selected = list(admissions())
    for i in range(10, 40):
        selected[i] = replace(selected[i], row=replace(selected[i].row, coordinate_epoch="ex-date"))
    result = build(selected)
    assert not result.available and result.reason == "coordinate_change_within_fixed40"
    assert result.primary == result.path == ()


def test_missing_admission_preserves_original_reason():
    selected = list(admissions())
    selected[10] = Admission(selected[10].row, False, "missing_bar", False)
    result = build(selected)
    assert not result.available and result.reason == "nonadmitted:missing_bar"
    assert result.primary == ()


def test_flat_path_preserves_undefined_direction_mask():
    selected = tuple(admit_daily(row(i, "95"), policy()) for i in range(40))
    result = build(selected)
    primary, path = result.reference_facts()
    encoded = encode_reference_facts(
        primary, path, anchor=selected[-1].row.market_date.isoformat(),
        source_digest=result.digest,
    )
    assert result.available and encoded.values is not None
    assert encoded.values[-2:] == (0.0, 1.0)
    assert result.path[-1].value is None
    assert result.path[-1].availability_reason == "zero_net_direction"


def test_zero_width_prior_is_not_imputed_as_zero_change():
    selected = list(admissions())
    for i in range(20):
        value = row(i, "100", high=Decimal("100"), low=Decimal("100"))
        selected[i] = admit_daily(value, policy())
    result = build(selected)
    primary, path = result.reference_facts()
    assert not result.available and result.reason == "required_fact_unavailable"
    assert primary["width_change"]["value"] is None
    assert encode_reference_facts(
        primary, path, anchor=selected[-1].row.market_date.isoformat(),
        source_digest=result.digest,
    ).values is None


def test_member_identity_changes_fact_closure_not_numeric_values():
    selected = list(admissions())
    original = build(selected)
    selected[0] = replace(selected[0], row=replace(selected[0].row, source_revision="new-revision"))
    changed = build(selected)
    assert [m.value for m in original.primary] == [m.value for m in changed.primary]
    assert original.digest != changed.digest
