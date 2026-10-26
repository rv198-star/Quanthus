"""The census successor keeps every qualified case and never widens frozen V2."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from quanthus.stage_learning import head2_census as census
from quanthus.stage_learning import head2_revision as v2
from quanthus.stage_learning.local_head import (
    HeadBindings,
    diagnose_head,
    dump_checkpoint,
    fit_head,
)
from quanthus.stage_learning.p5_assessment import P5AssessmentError

SHA = "sha256:" + "a" * 64


def bindings():
    return HeadBindings(
        SHA, SHA, SHA, census.eligibility_binding(SHA), SHA, v2.feature_contract_digest(), SHA, SHA
    )


def slots():
    result = []
    for role, blocks in (
        ("TRAIN", [("2020-12", 10), ("2021-03", 10)]),
        ("GUARD", [("2021-05", 16), ("2021-06", 16)]),
        ("UPDATE", [("2021-08", 16)]),
        ("EVALUATE", [("2021-11", 40), ("2021-12", 40)]),
    ):
        for block, count in blocks:
            for _ in range(count):
                k = str(len(result))
                result.append(
                    census.CensusSlot(
                        k,
                        "company" + k,
                        "security" + k,
                        "listing" + k,
                        "episode" + k,
                        block + "-20",
                        block,
                        role,
                    )
                )
    return tuple(result)


def opinions(selected, role):
    return tuple(
        census.CensusOpinion(
            s,
            True,
            (0.1 if i % 2 else 0.9, 0.12, -0.2, 0.03, 0.6, 0.5, 0.0),
            "POSITIVE" if i % 2 else "NEGATIVE",
            "POSITIVE" if i % 2 else "NEGATIVE",
        )
        for i, s in enumerate(s for s in selected if s.role == role)
    )


def groups():
    selected = slots()
    return tuple(
        census.admit_role(selected, opinions(selected, r), r)
        for r in ("TRAIN", "UPDATE", "GUARD", "EVALUATE")
    )


def test_all_qualified_cases_used_in_canonical_order():
    selected = slots()
    for role, maximum in census.ROLE_CAPS.items():
        refs = opinions(selected, role)
        original = census.admit_role(selected, refs, role)
        assert len(original) == maximum
        assert census.admit_role(selected[::-1], refs[::-1], role) == original


def test_unknown_disagreement_and_unavailable_keep_denominator_without_negative_imputation():
    selected = slots()
    refs = list(opinions(selected, "EVALUATE"))
    refs[0] = replace(refs[0], opinion_a="UNKNOWN", opinion_b="UNKNOWN")
    refs[1] = replace(refs[1], opinion_b="NEGATIVE")
    refs[2] = replace(
        refs[2], source_available=False, features=None, opinion_a=None, opinion_b=None
    )
    admitted = census.admit_role(selected, refs, "EVALUATE")
    assert len(admitted) == 77
    assert not ({r.sample_id for r in admitted} & {r.slot.sample_id for r in refs[:3]})
    with pytest.raises(P5AssessmentError, match="reference_slot_set"):
        census.admit_role(selected, refs[1:], "EVALUATE")
    with pytest.raises(P5AssessmentError, match="unavailable_has_evidence"):
        census.admit_role(
            selected, [replace(refs[2], opinion_a="NEGATIVE"), *refs[:2], *refs[3:]], "EVALUATE"
        )


@pytest.mark.parametrize("field", ["company_id", "security_id", "listing_id", "episode_id"])
def test_alias_or_duplicate_identity_across_roles_rejects(field):
    selected = list(slots())
    selected[-1] = replace(selected[-1], **{field: getattr(selected[0], field)})
    with pytest.raises(P5AssessmentError, match="identity_overlap"):
        census.validate_slots(selected)


@pytest.mark.parametrize("role", ["TRAIN", "GUARD", "UPDATE", "EVALUATE"])
def test_extra_frozen_slot_rejects(role):
    selected = slots()
    extra = next(s for s in selected if s.role == role)
    with pytest.raises(P5AssessmentError, match="exact_role_slots"):
        census.validate_slots((*selected, extra))


def test_low_support_and_missing_time_block_fail_without_replacing_cases():
    selected = slots()
    refs = opinions(selected, "EVALUATE")
    with pytest.raises(P5AssessmentError, match="role_support"):
        census.admit_role(
            selected,
            [replace(r, opinion_a="NEGATIVE", opinion_b="NEGATIVE") for r in refs],
            "EVALUATE",
        )
    with pytest.raises(P5AssessmentError, match="time_support"):
        census.admit_role(
            selected,
            [
                replace(r, opinion_a="UNKNOWN", opinion_b="UNKNOWN")
                if r.slot.time_block == "2021-11"
                else r
                for r in refs
            ],
            "EVALUATE",
        )


def test_fixed_new_role_order_and_anchor_months_are_checked():
    selected = list(slots())
    selected[0] = replace(selected[0], anchor="2022-01-01")
    with pytest.raises(P5AssessmentError, match="date_scope"):
        census.validate_slots(selected)
    selected = list(slots())
    selected[-1] = replace(selected[-1], time_block="2021-11")
    with pytest.raises(P5AssessmentError, match="fixed_time_blocks"):
        census.validate_slots(selected)


def test_old_v2_caps_reject_census_sizes_and_keep_original_semantics():
    train, update, guard, evaluation = groups()
    v2.validate_roles(train, update[:8], guard[:8], evaluation[:24])
    for new, protection, final in (
        (update, guard[:8], evaluation[:24]),
        (update[:8], guard, evaluation[:24]),
        (update[:8], guard[:8], evaluation),
    ):
        with pytest.raises(P5AssessmentError, match="row_budget"):
            v2.validate_roles(train, new, protection, final)


def test_fixed_fit_kernel_exactly_matches_legacy_and_checks_protocol_binding():
    train, *_ = groups()
    parent = census.fit_census(train, bindings=bindings())
    expected = fit_head(train, policy=v2.revision_policy(), bindings=bindings())
    assert dump_checkpoint(parent) == dump_checkpoint(expected)
    assert census.development_spend_gate(parent, train)["pass"]
    with pytest.raises(P5AssessmentError, match="protocol_binding"):
        census.fit_census(train, bindings=replace(bindings(), eligibility=SHA))
    with pytest.raises(P5AssessmentError, match="train_binding"):
        census.development_spend_gate(parent, [replace(r, label=1 - r.label) for r in train])


def test_development_non_discrimination_does_not_open_any_source_or_reference():
    train, *_ = groups()
    train = tuple(
        replace(r, features=train[0].features, label=int(i < 4)) for i, r in enumerate(train)
    )
    parent = census.fit_census(train, bindings=bindings())
    report = census.development_spend_gate(parent, train)
    assert report["reason"] == "DEVELOPMENT_NONDISCRIMINATION_STOP"
    assert report["true_positive"] == 0 and report["true_negative"] == 16
    # These primitives accept in-memory admitted rows and have no source/reference callback.
    assert not report["independent_evaluation"]


def test_no_update_trigger_is_recorded_without_forced_training():
    train, update, guard, _ = groups()
    parent = census.fit_census(train, bindings=bindings())
    assert (
        census.begin_update(parent, train, update, guard, diagnostic=diagnose_head(parent, update))
        is None
    )


def test_actual_update_retention_resume_and_legacy_numeric_equivalence():
    train, update, guard, _ = groups()
    update = tuple(replace(r, label=1 - r.label) for r in update)
    parent = census.fit_census(train, bindings=bindings())
    revised = census.begin_update(
        parent, train, update[:8], guard[:8], diagnostic=diagnose_head(parent, update[:8])
    )
    legacy = v2.begin_update(
        parent, train, update[:8], guard[:8], diagnostic=diagnose_head(parent, update[:8])
    )
    assert revised is not None and legacy is not None
    assert (revised.weights, revised.bias) == (legacy.weights, legacy.bias)
    assert census.retention_report(revised, train, update[:8], guard[:8]) == (
        v2.retention_report(legacy, train, update[:8], guard[:8])
    )
    expanded = census.begin_update(
        parent, train, update, guard, diagnostic=diagnose_head(parent, update)
    )
    assert expanded is not None and expanded.complete
    report = census.retention_report(expanded, train, update, guard)
    assert report.accepted and report.consumer_admission == "NOT_GRANTED"
    bad = replace(expanded, weights=tuple(-w for w in parent.weights), bias=-parent.bias)
    rejected = census.retention_report(bad, train, update, guard)
    assert not rejected.accepted and "POSITIVE_RETENTION_REGRESSION" in rejected.reasons
    raw = census.dump_update(expanded)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert census.load_update(raw, expected_sha256=digest, bindings=bindings()) == expanded
    with pytest.raises(P5AssessmentError, match="checkpoint_invalid"):
        v2.load_update(
            raw, expected_sha256=digest, bindings=bindings(), parent_digest=parent.digest
        )
    with pytest.raises(P5AssessmentError, match="resume_input_changed"):
        census.resume_update(
            expanded, train, tuple(replace(r, label=1 - r.label) for r in update), guard
        )


def test_prediction_commitment_pins_all_evaluation_slots_model_code_and_predictions():
    selected = slots()
    values = {s.sample_id: 0.2 for s in selected if s.role == "EVALUATE"}
    inputs = dict(
        request=SHA, code=SHA, cohort=SHA, candidate=SHA, predictions=values, slots=selected
    )
    expected = census.prediction_commitment(**inputs)
    census.verify_prediction_commitment(expected, **inputs)
    for field in ("code", "candidate", "cohort", "request"):
        with pytest.raises(P5AssessmentError, match="commitment_changed"):
            census.verify_prediction_commitment(expected, **{**inputs, field: "sha256:" + "b" * 64})
    changed = dict(values)
    changed[next(iter(changed))] = 0.8
    with pytest.raises(P5AssessmentError, match="commitment_changed"):
        census.verify_prediction_commitment(expected, **{**inputs, "predictions": changed})
    with pytest.raises(P5AssessmentError, match="prediction_set"):
        census.prediction_commitment(**{**inputs, "predictions": {}})
