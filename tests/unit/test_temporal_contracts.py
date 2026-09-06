from __future__ import annotations

from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta, timezone

import pytest

from quanthus.evidence_consumption import (
    ConsumedRevision,
    FactConsumptionEvent,
    GenerationReceipt,
    PublicationReceipt,
    validate_consumption_binding,
)
from quanthus.structure_semantics import ClockView
from quanthus.temporal_contracts import (
    BusinessTime,
    ObservationRevision,
    ObservationWindow,
    QueryPurpose,
    SourceVersionClock,
    TemporalQuery,
    TimeEvidence,
    dependency_ready_at,
    select_source_version,
)

TZ = timezone(timedelta(hours=8))


def at(day=3, hour=15, second=0, year=2020):
    return datetime(year, 1, day, hour, 0, second, tzinfo=TZ)


def sha(character):
    return "sha256:" + character * 64


def mark(day=3, hour=16):
    instant = at(day, hour)
    return TimeEvidence(instant, instant, "instant", f"receipt-{day}-{hour}")


def query(day=5, view=ClockView.PUBLIC_RECONSTRUCTION, purpose=QueryPurpose.RECOMPUTATION):
    return TemporalQuery(at(day), view, purpose, "unique-evidenced-intervals-v1")


def fact():
    window = ObservationWindow(at(1), at(3), True, True, "calendar-v1", 3, 3)
    return ObservationRevision(
        "security-A", "listing-A", "indicator.trend.sma", "1.0.0", sha("1"),
        "price-coordinate-v1", BusinessTime("measurement", at(3), window), sha("2"), sha("3"),
    )


def receipts(observation, year=2020):
    generation = GenerationReceipt(
        f"generation-{year}", f"run-{year}", "a" * 40, observation.spec_digest,
        (observation.revision_id,), at(5, 16, year=year),
    )
    publication = PublicationReceipt(
        f"publication-{year}", generation.receipt_id, sha("4"), at(5, 17, year=year),
    )
    return generation, publication


def consumption(observation, generation, publication, *, year=2020, name="A", purpose=None):
    context = query(
        view=ClockView.PLATFORM_AS_KNOWN,
        purpose=purpose or QueryPurpose.ACTUAL_CONSUMPTION_AUDIT,
    )
    return FactConsumptionEvent(
        f"event-{name}", f"run-{name}", f"expert-{name}", f"decision-{name}", context,
        at(5, 18, year=year), at(5, 19, year=year), at(5, 20, year=year),
        (ConsumedRevision(
            observation.observation_key, observation.revision_id, observation.spec_digest,
            generation.receipt_id, publication.receipt_id, publication.artifact_digest,
        ),),
    )


def test_observation_has_business_time_but_no_usage_or_generation_fields():
    forbidden = {
        "consumed_at", "used_at", "last_used_at", "consumer_id", "decision_id",
        "decision_at", "knowledge_cutoff", "recorded_at", "computed_at", "created_at",
        "publication_ref", "servable_at", "run_id",
    }
    for contract in (ObservationRevision, BusinessTime):
        assert forbidden.isdisjoint({field.name for field in fields(contract)})
    with pytest.raises(TypeError):
        replace(fact(), consumed_at=at(5))


def test_multiple_uses_change_only_log_identity_not_observation():
    observation = fact()
    before = asdict(observation)
    generation, publication = receipts(observation)
    first = consumption(observation, generation, publication)
    second = consumption(observation, generation, publication, name="B")
    for event in (first, second):
        validate_consumption_binding(event, observation, generation, publication)
    assert first.digest != second.digest
    assert first.inputs[0].revision_id == second.inputs[0].revision_id == observation.revision_id
    assert asdict(observation) == before


def test_later_rebuild_and_larger_package_do_not_change_semantic_revision():
    observation = fact()
    generation, publication = receipts(observation)
    later_generation, later_publication = receipts(observation, year=2026)
    later_generation = replace(
        later_generation, output_revision_ids=(observation.revision_id, sha("5")),
    )
    later_publication = replace(later_publication, artifact_digest=sha("6"))
    event = consumption(
        observation, later_generation, later_publication, year=2026,
        purpose=QueryPurpose.RECOMPUTATION,
    )
    validate_consumption_binding(event, observation, later_generation, later_publication)
    assert later_generation != generation and later_publication != publication
    assert event.inputs[0].revision_id == observation.revision_id
    assert event.query.knowledge_cutoff.year == 2020 and event.consumed_at.year == 2026


def test_new_computation_cannot_prove_old_actual_use():
    observation = fact()
    generation, publication = receipts(observation, year=2026)
    event = consumption(observation, generation, publication, year=2020)
    with pytest.raises(ValueError, match="delivery-before-consumption"):
        validate_consumption_binding(event, observation, generation, publication)


def test_delayed_log_recording_does_not_rewrite_old_operation():
    observation = fact()
    generation, publication = receipts(observation)
    old_event = consumption(observation, generation, publication)
    recorded_later = replace(old_event, recorded_at=at(5, 20, year=2026))
    validate_consumption_binding(recorded_later, observation, generation, publication)
    assert recorded_later.consumed_at == old_event.consumed_at
    assert recorded_later.digest != old_event.digest


@pytest.mark.parametrize("field,value", [
    ("spec_digest", sha("8")), ("artifact_digest", sha("8")),
    ("generation_ref", "another-generation"), ("publication_ref", "another-publication"),
    ("observation_key", sha("8")), ("revision_id", sha("8")),
])
def test_audit_rejects_reference_substitution(field, value):
    observation = fact()
    generation, publication = receipts(observation)
    event = consumption(observation, generation, publication)
    event = replace(event, inputs=(replace(event.inputs[0], **{field: value}),))
    with pytest.raises(ValueError):
        validate_consumption_binding(event, observation, generation, publication)


def test_same_second_delivery_requires_ordering_evidence_not_a_guessed_epsilon():
    observation = fact()
    generation, publication = receipts(observation)
    event = consumption(observation, generation, publication)
    publication = replace(publication, servable_at=event.consumed_at)
    with pytest.raises(ValueError, match="order is unproven"):
        validate_consumption_binding(event, observation, generation, publication)


def test_publication_cannot_precede_generation():
    observation = fact()
    generation, publication = receipts(observation)
    event = consumption(observation, generation, publication)
    with pytest.raises(ValueError, match="publication predates generation"):
        validate_consumption_binding(
            event, observation, generation, replace(publication, servable_at=at(5, 15)),
        )


def test_direct_handoff_is_a_receipt_not_a_fake_fact_used_at():
    observation = fact()
    generation, publication = receipts(observation)
    publication = replace(publication, delivery_mode="direct_handoff")
    event = consumption(observation, generation, publication)
    validate_consumption_binding(event, observation, generation, publication)
    assert "consumed_at" not in asdict(observation)


@pytest.mark.parametrize("view", [ClockView.PUBLIC_RECONSTRUCTION, ClockView.RETROSPECTIVE])
def test_knowledge_view_and_actual_use_are_distinct_axes(view):
    with pytest.raises(ValueError, match="platform knowledge"):
        query(view=view, purpose=QueryPurpose.ACTUAL_CONSUMPTION_AUDIT)
    assert query(view=view).purpose == QueryPurpose.RECOMPUTATION


def test_result_window_and_effective_interval_are_not_interchangeable():
    business = fact().business_time
    assert business.result_as_of == at(3) and business.window.start == at(1)
    future_rule = BusinessTime("reference", at(3), valid_from=at(10), valid_to=at(12))
    assert future_rule.confirmed_by(at(5))
    assert future_rule.valid_from > at(5)  # Known is not yet business-effective.


def test_delayed_pivot_retains_event_date_without_backdating_confirmation():
    pivot = BusinessTime("reference", at(3), event_at=at(3), confirmation_event_at=at(7))
    assert not pivot.confirmed_by(at(6)) and pivot.confirmed_by(at(7))
    observation = replace(fact(), business_time=pivot)
    generation, publication = receipts(observation)
    event = consumption(observation, generation, publication)
    with pytest.raises(ValueError, match="not confirmed"):
        validate_consumption_binding(event, observation, generation, publication)


def test_market_confirmation_is_not_evidence_readiness():
    market_confirmation = BusinessTime("process_observation", at(3), event_at=at(3))
    ready = dependency_ready_at((mark(3, 16), mark(3, 17)), declared_complete=True)
    assert market_confirmation.confirmed_by(at(3))
    assert ready == at(3, 17) and ready > at(3)
    assert dependency_ready_at(
        (mark(3, 16),), declared_complete=True, confirmation_ready=mark(7, 17),
    ) == at(7, 17)


def test_missing_dependency_is_not_silently_ignored():
    with pytest.raises(ValueError):
        dependency_ready_at((), declared_complete=True)
    with pytest.raises(ValueError):
        dependency_ready_at((mark(),), declared_complete=False)
    with pytest.raises(ValueError):
        dependency_ready_at((mark(), None), declared_complete=True)


def test_date_only_availability_is_not_midnight_imputation():
    day = TimeEvidence(at(3, 0), at(4, 0), "date", "date-only-publisher-record")
    assert not day.definitely_before(at(3, 12))
    assert not day.definitely_before(at(4, 0))
    assert day.definitely_before(at(4, 1))
    with pytest.raises(ValueError, match="precision"):
        TimeEvidence(at(3, 0), at(3, 0), "date", "guessed-midnight")


def test_public_version_selection_refuses_overlapping_candidates():
    first = SourceVersionClock("bar-A", "v1", public_from=mark(3))
    second = SourceVersionClock("bar-A", "v2", public_from=mark(10))
    with pytest.raises(ValueError, match="unique"):
        select_source_version((first, second), query(11))
    first = replace(first, public_to=mark(10))
    assert select_source_version((second, first), query(5)).revision_id == "v1"
    assert select_source_version((first, second), query(11)).revision_id == "v2"
    with pytest.raises(ValueError, match="order"):
        select_source_version((first, second), replace(query(11), knowledge_cutoff=at(10, 16)))


def test_unknown_revision_does_not_fall_back_to_stale_known_value():
    first = SourceVersionClock("bar-A", "v1", public_from=mark(3))
    unknown = SourceVersionClock("bar-A", "unknown-version")
    with pytest.raises(ValueError, match="unproven"):
        select_source_version((first, unknown), query(5))


def test_platform_receipt_differs_from_publication_and_retro_requires_pin():
    version = SourceVersionClock("bar-A", "v1", public_from=mark(3), platform_from=mark(4))
    assert select_source_version((version,), query(4)).revision_id == "v1"
    with pytest.raises(ValueError):
        select_source_version((version,), query(4, view=ClockView.PLATFORM_AS_KNOWN))
    retrospective = query(view=ClockView.RETROSPECTIVE)
    with pytest.raises(ValueError, match="pinned"):
        select_source_version((version,), retrospective)
    assert select_source_version((version,), retrospective, retrospective_pin="v1") == version


def test_selection_never_mixes_subject_keys_or_duplicate_versions():
    version = SourceVersionClock("bar-A", "v1", public_from=mark(3))
    with pytest.raises(ValueError):
        select_source_version((version, version), query())
    with pytest.raises(ValueError):
        select_source_version((version, replace(version, source_key="bar-B")), query())


def test_ending_episode_is_a_later_observation_not_mutation_of_earlier_status():
    prior = replace(
        fact(), business_time=BusinessTime("process_observation", at(7), event_at=at(3)),
    )
    ended = replace(
        prior, business_time=BusinessTime("process_observation", at(8), event_at=at(8)),
        value_digest=sha("7"),
    )
    assert prior.business_time.confirmed_by(at(7))
    assert not ended.business_time.confirmed_by(at(7))
    assert prior.revision_id != ended.revision_id


def test_partial_final_and_session_counts_are_explicit_not_pattern_confirmation():
    window = replace(fact().business_time.window, exchange_sessions=4, observed_bars=3)
    partial = replace(
        fact(), business_time=BusinessTime("measurement", at(3), window, finality="partial"),
    )
    final = replace(partial, business_time=replace(partial.business_time, finality="final"))
    assert partial.observation_key == final.observation_key
    assert partial.revision_id != final.revision_id
    assert final.business_time.confirmation_event_at is None
    assert window.observed_bars != window.exchange_sessions


def test_source_correction_and_spec_change_are_not_wall_clock_rebuilds():
    observation = fact()
    correction = replace(observation, source_closure_digest=sha("9"))
    assert correction.observation_key == observation.observation_key
    assert correction.revision_id != observation.revision_id
    other_spec = replace(observation, spec_digest=sha("9"))
    assert other_spec.observation_key != observation.observation_key


def test_mutable_collections_cannot_mutate_frozen_audit_contracts():
    observation = fact()
    generation, publication = receipts(observation)
    event = consumption(observation, generation, publication)
    with pytest.raises(ValueError):
        replace(generation, output_revision_ids=list(generation.output_revision_ids))
    with pytest.raises(ValueError):
        replace(event, inputs=list(event.inputs))


def test_utc_equivalence_has_identical_semantic_identity():
    observation = fact()
    business = replace(observation.business_time, result_as_of=at(3).astimezone(timezone.utc))
    equivalent = replace(observation, business_time=business)
    assert equivalent.observation_key == observation.observation_key
    assert equivalent.revision_id == observation.revision_id


@pytest.mark.parametrize("changes", [
    {"result_as_of": datetime(2020, 1, 3)},
    {"confirmation_event_at": at(2)},
    {"event_at": at(4)},
    {"valid_to": at(5)},
    {"finality": "probably-final"},
    {"profile": "forecast-guessed-as-structure"},
])
def test_business_time_rejects_invalid_local_contracts(changes):
    with pytest.raises(ValueError):
        replace(fact().business_time, **changes)
