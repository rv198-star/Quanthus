"""Synthetic regressions for the separately versioned Head2 correction."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from decimal import Decimal

import pytest

from quanthus.stage_learning.head2_revision import (
    FEATURE_NAMES,
    FactValue,
    begin_update,
    dump_update,
    encode_features,
    encode_reference_facts,
    feature_contract_digest,
    load_update,
    resume_update,
    retention_report,
    revision_policy,
    update_score,
    validate_roles,
)
from quanthus.stage_learning.local_head import (
    HeadBindings,
    HeadRow,
    diagnose_head,
    dump_checkpoint,
    fit_head,
    predict_head,
)
from quanthus.stage_learning.p5_assessment import DecisionState, P5AssessmentError


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def bindings() -> HeadBindings:
    result = HeadBindings(*("sha256:" + str(n) * 64 for n in range(8)))
    return replace(result, features=feature_contract_digest())


def facts(**changes) -> tuple[FactValue, ...]:
    values = {
        "directional_efficiency": "0.4",
        "range_width": "0.12",
        "width_change": "-0.2",
        "center_shift": "0.03",
        "range_traversal": "0.6",
        "counter_direction_excursion_share": "0.5",
    }
    values.update(changes)
    return tuple(
        FactValue(
            name,
            None if value is None else Decimal(value),
            "available" if value is not None else "unavailable",
            "sha256:" + str(i) * 64,
            "2020-06-30",
        )
        for i, (name, value) in enumerate(values.items())
    )


def encode(selected=None, **changes):
    return encode_features(
        facts(**changes) if selected is None else selected,
        anchor="2020-06-30",
        source_digest="sha256:" + "a" * 64,
    )


def rows(prefix, day, labels=(0, 0, 1, 1), *, hard=False):
    return tuple(
        HeadRow(
            f"{prefix}-{i}",
            f"issuer-{prefix}-{i}",
            f"episode-{prefix}-{i}",
            day,
            (0.1 if hard or label else 0.9, 0.12, -0.2, 0.03, 0.6, 0.5, 0.0),
            label,
        )
        for i, label in enumerate(labels)
    )


@pytest.fixture
def inputs():
    train = rows("train", "2020-06-30")
    new = rows("update", "2020-09-30", (0, 0, 0), hard=True)
    guard = rows("guard", "2020-07-31")
    parent = fit_head(train, policy=revision_policy(0.0), bindings=bindings())
    return parent, train, new, guard


def start(inputs, **kwargs):
    parent, train, new, guard = inputs
    result = begin_update(
        parent, train, new, guard, diagnostic=diagnose_head(parent, new), **kwargs
    )
    assert result is not None
    return result


def test_signed_geometry_has_symmetric_encoding_and_central_separation():
    encodings = [encode(directional_efficiency=str(v)) for v in (-0.6, 0, 0.6)]
    first, middle, last = [value.values for value in encodings]
    assert first is not None and middle is not None and last is not None
    assert first == last and first[0] > middle[0]
    # A concrete separator witnesses representability, not market-label truth.
    assert [0.3 - value[0] > 0 for value in (first, middle, last)] == [False, True, False]
    assert encode(center_shift="-0.03").values == encode(center_shift="0.03").values


def test_path_facts_and_mask_are_actual_columns():
    original = encode()
    changed = encode(range_traversal="0.2")
    assert len(FEATURE_NAMES) == 7
    assert original.values is not None and changed.values is not None
    assert original.values[4] != changed.values[4]
    assert original.digest != changed.digest


def test_zero_net_direction_is_masked_but_missing_payload_is_unavailable():
    selected = list(
        facts(
            directional_efficiency="0", range_traversal="0", counter_direction_excursion_share=None
        )
    )
    selected[-1] = replace(selected[-1], reason="zero_net_direction")
    neutral = encode(selected)
    missing = encode()
    assert neutral.values is not None and neutral.values[-2:] == (0.0, 1.0)
    assert encode(facts(counter_direction_excursion_share=None)).values is None
    assert missing.values is not None and missing.values[-1] == 0.0
    assert neutral.digest != encode(facts(counter_direction_excursion_share="0")).digest


@pytest.mark.parametrize("index", range(6))
def test_actual_missing_fact_never_becomes_a_zero_feature(index):
    selected = list(facts())
    selected[index] = replace(selected[index], value=None, reason="source_unavailable")
    result = encode(selected)
    assert result.values is None and result.reason == "REQUIRED_FACT_UNAVAILABLE"


@pytest.mark.parametrize(
    "change",
    [
        {"value": Decimal("NaN")},
        {"value": Decimal("Infinity")},
        {"value": 0.3},
        {"revision_digest": "unknown"},
        {"market_date": "2020-07-01"},
        {"name": "unknown"},
        {"value": Decimal("0"), "reason": "zero_net_direction"},
    ],
)
def test_invalid_or_future_facts_rejected(change):
    selected = list(facts())
    selected[0] = replace(selected[0], **change)
    with pytest.raises(P5AssessmentError):
        encode(selected)


def test_encoding_preserves_original_decimal_values_and_revision_identity():
    selected = facts(center_shift="0.03000000000000000000000000001")
    before = tuple((v.value, v.value.as_tuple()) for v in selected if v.value is not None)
    encoded = encode(selected)
    after = tuple((v.value, v.value.as_tuple()) for v in selected if v.value is not None)
    assert before == after
    altered = list(selected)
    altered[0] = replace(altered[0], revision_digest="sha256:" + "f" * 64)
    assert encoded.digest != encode(altered).digest
    assert encode(tuple(reversed(selected))) == encoded


def test_protected_date_duplicate_and_incomplete_fact_sets_rejected():
    with pytest.raises(P5AssessmentError):
        encode_features(facts(), anchor="2022-01-01", source_digest="sha256:" + "a" * 64)
    for selected in (facts()[:-1], (*facts(), facts()[0])):
        with pytest.raises(P5AssessmentError):
            encode(selected)


def test_first_update_gradient_weights_each_group_equally(inputs):
    parent, train, new, _ = inputs
    first = start(inputs, stop_after=1)
    matrix = [
        [
            tuple(
                (x - m) / s
                for x, m, s in zip(row.features, parent.means, parent.scales, strict=True)
            )
            for row in group
        ]
        for group in (train, new)
    ]
    errors = [
        [predict_head(parent, row.features).raw_score - row.label for row in group]
        for group in (train, new)
    ]
    for i, weight in enumerate(parent.weights):
        gradient = math.fsum(
            0.5 * math.fsum(e * v[i] for e, v in zip(es, vs, strict=True)) / len(es)
            for es, vs in zip(errors, matrix, strict=True)
        )
        expected = weight - 0.05 * (gradient + 0.01 * weight)
        assert first.weights[i] == pytest.approx(expected, abs=1e-14)
    assert first.parent.means == parent.means and first.parent.scales == parent.scales


def test_guard_never_participates_in_gradient(inputs):
    first = start(inputs)
    parent, train, new, guard = inputs
    changed_guard = tuple(replace(row, label=1 - row.label) for row in guard)
    second = start((parent, train, new, changed_guard))
    assert first.weights == second.weights and first.bias == second.bias
    assert first.guard_digest != second.guard_digest


def test_pause_reload_resume_is_exact_and_idempotent(inputs):
    parent, train, new, guard = inputs
    partial = start(inputs, stop_after=17)
    raw = dump_update(partial)
    cold = load_update(
        raw, expected_sha256=digest(raw), bindings=bindings(), parent_digest=parent.digest
    )
    resumed = resume_update(cold, train, new, guard)
    assert resumed == start(inputs)
    assert resume_update(resumed, train, new, guard) == resumed
    assert dump_checkpoint(parent) == dump_checkpoint(resumed.parent)


@pytest.mark.parametrize("part", (1, 2, 3))
def test_changed_train_update_or_guard_refuses_resume(inputs, part):
    partial = start(inputs, stop_after=17)
    changed = list(inputs)
    changed[part] = (replace(inputs[part][0], label=1 - inputs[part][0].label), *inputs[part][1:])
    with pytest.raises(P5AssessmentError):
        resume_update(partial, *changed[1:])


@pytest.mark.parametrize("field", ("sample_id", "security_id", "episode_id"))
def test_roles_reject_any_identity_overlap(inputs, field):
    _, train, new, guard = inputs
    bad = (replace(new[0], **{field: getattr(train[0], field)}), *new[1:])
    with pytest.raises(P5AssessmentError, match="overlap"):
        validate_roles(train, bad, guard)


def test_evaluation_overlap_time_leak_and_one_class_guard_rejected(inputs):
    _, train, new, guard = inputs
    with pytest.raises(P5AssessmentError, match="overlap"):
        validate_roles(train, new, guard, guard)
    with pytest.raises(P5AssessmentError):
        validate_roles(train, rows("bad", "2020-05-31"), guard)
    with pytest.raises(P5AssessmentError, match="guard"):
        validate_roles(train, new, rows("guard", "2020-07-31", (0, 0, 0, 0)))
    validate_roles(train, new, guard, rows("eval", "2020-12-31"))


def test_policy_feature_and_source_bindings_cannot_be_mixed(inputs):
    parent, train, new, guard = inputs
    changed = replace(parent, bindings=replace(parent.bindings, features="sha256:" + "f" * 64))
    with pytest.raises(P5AssessmentError, match="feature"):
        begin_update(changed, train, new, guard, diagnostic=diagnose_head(changed, new))
    with pytest.raises(P5AssessmentError):
        begin_update(start(inputs), train, new, guard, diagnostic=diagnose_head(parent, new))


def test_no_trigger_preserves_no_update_disposition(inputs):
    _, train, new, guard = inputs
    parent = fit_head(train, policy=revision_policy(100.0), bindings=bindings())
    assert begin_update(parent, train, new, guard, diagnostic=diagnose_head(parent, new)) is None


def test_update_guard_rejects_lost_old_positive_ability(inputs):
    parent, train, new, guard = inputs
    updated = start(inputs)
    broken = replace(updated, weights=tuple(-w for w in parent.weights), bias=-parent.bias)
    report = retention_report(broken, train, new, guard)
    assert not report.accepted and report.positive_errors_after > report.positive_errors_before
    assert "POSITIVE_RETENTION_REGRESSION" in report.reasons


def test_update_report_and_scores_are_diagnostics_not_calibrated_admission(inputs):
    _, train, new, guard = inputs
    updated = start(inputs)
    report = retention_report(updated, train, new, guard)
    assert report.consumer_admission == "NOT_GRANTED"
    assert report.update_loss_after <= report.update_loss_before
    output = update_score(updated, guard[0].features)
    assert output.calibrated_probability is None and output.decision_state is DecisionState.ABSTAIN
    assert update_score(updated, (None, *guard[0].features[1:])).raw_score is None
    with pytest.raises(P5AssessmentError, match="incomplete"):
        update_score(start(inputs, stop_after=1), guard[0].features)


@pytest.mark.parametrize(
    "change",
    (
        {"cursor": True},
        {"cursor": 101},
        {"bias": float("inf")},
        {"train_digest": "unknown"},
        {"weights": [1.0]},
    ),
)
def test_repacked_invalid_update_is_rejected(inputs, change):
    parent = inputs[0]
    data = json.loads(dump_update(start(inputs)))
    data.update(change)
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(P5AssessmentError):
        load_update(
            raw, expected_sha256=digest(raw), bindings=bindings(), parent_digest=parent.digest
        )


def test_duplicate_json_wrong_parent_and_noncanonical_update_rejected(inputs):
    parent = inputs[0]
    raw = dump_update(start(inputs))
    duplicate = b'{"schema":"quanthus.head2.guarded-update.v2",' + raw[1:]
    for data in (duplicate, json.dumps(json.loads(raw), indent=2).encode()):
        with pytest.raises(P5AssessmentError):
            load_update(
                data, expected_sha256=digest(data), bindings=bindings(), parent_digest=parent.digest
            )
    with pytest.raises(P5AssessmentError):
        load_update(
            raw,
            expected_sha256=digest(raw),
            bindings=bindings(),
            parent_digest="sha256:" + "f" * 64,
        )


def test_versioned_engineering_policy_matches_code_without_granting_a_real_run():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2] / "config/research/learning-mvp/head2-revision.v2.json"
    )
    config = json.loads(path.read_text())
    policy = revision_policy()
    assert tuple(config["feature_names"]) == policy.feature_names
    for name in ("fit_steps", "update_steps", "learning_rate", "l2", "update_loss_trigger"):
        assert config["optimizer"][name] == getattr(policy, name)
    assert config["optimizer"]["original_train_weight"] == 0.5
    assert config["optimizer"]["new_update_weight"] == 0.5
    assert config["consumer_admission"] == "NOT_GRANTED"
    assert config["source_and_real_experiment_admission"] == "NOT_GRANTED_BY_THIS_CONFIG"
    assert config["P6_activated"] is config["production_authorized"] is False


def packet_fields():
    items = [
        (f.name, {"value": str(f.value), "reason": f.reason, "revision_id": f.revision_digest})
        for f in facts()
    ]
    return dict(items[:4]), {
        "structure.geometry.range-traversal": items[4][1],
        "structure.geometry.counter-direction-excursion-share": items[5][1],
    }


def test_existing_reference_packet_fields_project_exact_revisions():
    primary, path = packet_fields()
    result = encode_reference_facts(
        primary, path, anchor="2020-06-30", source_digest="sha256:" + "a" * 64
    )
    assert result == encode()


@pytest.mark.parametrize("change", (0.4, "NaN", None))
def test_reference_adapter_rejects_lossy_or_invalid_available_values(change):
    primary, path = packet_fields()
    primary["directional_efficiency"]["value"] = change
    with pytest.raises(P5AssessmentError):
        encode_reference_facts(
            primary, path, anchor="2020-06-30", source_digest="sha256:" + "a" * 64
        )
