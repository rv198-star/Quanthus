from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from quanthus.stage_learning.local_head import (
    HeadBindings,
    HeadPolicy,
    HeadRow,
    diagnose_head,
    dump_checkpoint,
    dump_diagnostic,
    evaluation_summary,
    fit_head,
    load_checkpoint,
    load_diagnostic,
    predict_head,
    resume_head,
    update_head,
)
from quanthus.stage_learning.p5_assessment import DecisionState, P5AssessmentError


def bindings() -> HeadBindings:
    return HeadBindings(*("sha256:" + str(n) * 64 for n in range(8)))


def policy(**changes) -> HeadPolicy:
    return replace(HeadPolicy(("x", "constant"), 40, 20, 0.05, 0.01, 0.0), **changes)


def rows(prefix="train", day="2020-06-30", labels=(0, 0, 1, 1)) -> tuple[HeadRow, ...]:
    return tuple(
        HeadRow(
            f"{prefix}-{i}",
            f"security-{prefix}-{i}",
            f"episode-{prefix}-{i}",
            day,
            (float(i - 2), 7.0),
            label,
        )
        for i, label in enumerate(labels)
    )


def checkpoint(**changes):
    return fit_head(rows(), policy=policy(**changes), bindings=bindings())


def byte_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_fit_is_order_invariant_and_learns_nonconstant_order():
    first = checkpoint()
    reversed_fit = fit_head(tuple(reversed(rows())), policy=policy(), bindings=bindings())
    assert dump_checkpoint(first) == dump_checkpoint(reversed_fit)
    assert predict_head(first, (-2.0, 7.0)).raw_score < predict_head(first, (1.0, 7.0)).raw_score
    assert first.means == (-0.5, 7.0)
    assert first.scales[1] == 1.0
    assert first.weights[1] == 0.0


def test_pause_json_reload_and_resume_equal_uninterrupted_fit():
    partial = fit_head(rows(), policy=policy(), bindings=bindings(), stop_after=13)
    raw = dump_checkpoint(partial)
    loaded = load_checkpoint(raw, expected_sha256=byte_digest(raw), bindings=bindings())
    resumed = resume_head(loaded, rows())
    assert resumed == checkpoint()
    assert resume_head(resumed, rows()) == resumed
    assert resumed.means == partial.means


def test_resume_rejects_changed_input_and_step_rewind():
    partial = fit_head(rows(), policy=policy(), bindings=bindings(), stop_after=13)
    changed = (replace(rows()[0], label=1), *rows()[1:])
    with pytest.raises(P5AssessmentError, match="input_changed"):
        resume_head(partial, changed)
    with pytest.raises(P5AssessmentError, match="step_boundary"):
        resume_head(partial, rows(), stop_after=12)


def test_json_requires_pinned_bytes_and_complete_semantic_bindings():
    raw = dump_checkpoint(checkpoint())
    with pytest.raises(P5AssessmentError, match="checkpoint_bytes"):
        load_checkpoint(raw + b" ", expected_sha256=byte_digest(raw), bindings=bindings())
    with pytest.raises(P5AssessmentError):
        load_checkpoint(
            raw,
            expected_sha256=byte_digest(raw),
            bindings=replace(bindings(), eligibility="sha256:" + "f" * 64),
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(schema="unknown"),
        lambda value: value.update(cursor=41),
        lambda value: value.update(generation=True),
        lambda value: value.update(parent_digest="sha256:" + "f" * 64),
        lambda value: value.update(scales=[0.0, 1.0]),
        lambda value: value["uses"][0].update(anchor="2025-01-01"),
        lambda value: value["uses"][0].update(generation=True),
        lambda value: value["uses"][0].update(role="evaluation"),
        lambda value: value["uses"].append(value["uses"][0]),
        lambda value: value.update(uses=[]),
    ],
)
def test_repacked_invalid_checkpoint_fails_semantics(mutator):
    value = json.loads(dump_checkpoint(checkpoint()))
    mutator(value)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(P5AssessmentError):
        load_checkpoint(raw, expected_sha256=byte_digest(raw), bindings=bindings())


def test_duplicate_keys_and_noncanonical_json_are_rejected_even_with_new_hash():
    raw = dump_checkpoint(checkpoint())
    duplicate = b'{"schema":"quanthus.local-head2.checkpoint.v1",' + raw[1:]
    for changed in (duplicate, json.dumps(json.loads(raw), indent=2).encode()):
        with pytest.raises(P5AssessmentError):
            load_checkpoint(changed, expected_sha256=byte_digest(changed), bindings=bindings())


@pytest.mark.parametrize(
    "changed",
    [
        lambda r: replace(r, label=None),
        lambda r: replace(r, label=True),
        lambda r: replace(r, anchor="2022-01-01"),
        lambda r: replace(r, anchor="2011-12-31"),
        lambda r: replace(r, anchor="20200101"),
        lambda r: replace(r, features=(float("nan"), 7.0)),
        lambda r: replace(r, features=(True, 7.0)),
        lambda r: replace(r, features=(1.0,)),
        lambda r: replace(r, security_id=""),
    ],
)
def test_invalid_unknown_and_protected_training_inputs_fail_closed(changed):
    selected = (changed(rows()[0]), *rows()[1:])
    with pytest.raises(P5AssessmentError):
        fit_head(selected, policy=policy(), bindings=bindings())


def test_empty_duplicate_and_one_class_train_cannot_be_silently_accepted():
    for selected in ((), (rows()[0], rows()[0]), rows(labels=(0, 0, 0, 0))):
        with pytest.raises(P5AssessmentError):
            fit_head(selected, policy=policy(), bindings=bindings())


@pytest.mark.parametrize(
    "changes",
    [
        {"feature_names": ("x", "x")},
        {"fit_steps": 1001},
        {"fit_steps": True},
        {"learning_rate": 0},
        {"l2": -1},
        {"update_loss_trigger": float("nan")},
    ],
)
def test_optimizer_and_work_limits_are_explicit(changes):
    with pytest.raises(P5AssessmentError):
        checkpoint(**changes)


def test_no_trigger_means_no_local_update():
    first = checkpoint(update_loss_trigger=100.0)
    dev = rows("dev", "2020-09-30", (0, 0, 0, 0))
    diagnostic = diagnose_head(first, dev)
    assert diagnostic.classes == (0,)
    assert update_head(first, dev, diagnostic=diagnostic) is None
    with pytest.raises(P5AssessmentError, match="consumption_overlap"):
        evaluation_summary(first, dev, diagnostic=diagnostic)
    raw = dump_diagnostic(diagnostic)
    cold = load_diagnostic(raw, expected_sha256=byte_digest(raw))
    with pytest.raises(P5AssessmentError, match="consumption_overlap"):
        evaluation_summary(first, dev, diagnostic=cold)


def test_one_real_gradient_update_keeps_scaler_and_versioned_consumption():
    first = checkpoint()
    dev = rows("dev", "2020-09-30", (0, 0, 0, 0))
    diagnostic = diagnose_head(first, dev)
    second = update_head(first, dev, diagnostic=diagnostic)
    assert second is not None and second.generation == 1 and second.complete
    assert second.parent_digest == first.digest
    assert second.means == first.means and second.scales == first.scales
    assert (second.weights, second.bias) != (first.weights, first.bias)
    assert {(use.generation, use.role) for use in second.uses} == {
        (0, "train"),
        (0, "development_validation"),
        (1, "incremental_train"),
    }
    with pytest.raises(P5AssessmentError, match="complete_v0"):
        update_head(second, dev, diagnostic=diagnostic)


def test_paused_update_replays_exactly_without_reusing_development_as_validation():
    first = checkpoint()
    dev = rows("dev", "2020-09-30", (0, 0, 0, 0))
    diagnostic = diagnose_head(first, dev)
    partial = update_head(first, dev, diagnostic=diagnostic, stop_after=7)
    complete = update_head(first, dev, diagnostic=diagnostic)
    assert partial is not None and complete is not None
    raw = dump_checkpoint(partial)
    loaded = load_checkpoint(raw, expected_sha256=byte_digest(raw), bindings=bindings())
    assert resume_head(loaded, dev) == complete
    with pytest.raises(P5AssessmentError, match="consumption_overlap"):
        evaluation_summary(complete, dev, diagnostic=diagnostic)
    with pytest.raises(P5AssessmentError, match="consumption_overlap"):
        evaluation_summary(first, dev, diagnostic=diagnostic)


def test_forged_diagnostic_cannot_trigger_update():
    first = checkpoint()
    dev = rows("dev", "2020-09-30")
    diagnostic = diagnose_head(first, dev)
    with pytest.raises(P5AssessmentError, match="diagnostic_binding"):
        update_head(first, dev, diagnostic=replace(diagnostic, raw_log_loss=100.0))


@pytest.mark.parametrize("field", ["sample_id", "security_id", "episode_id"])
def test_cross_role_consumption_overlap_is_rejected(field):
    first = checkpoint()
    dev = rows("dev", "2020-09-30")
    contaminated = (replace(dev[0], **{field: getattr(rows()[0], field)}), *dev[1:])
    with pytest.raises(P5AssessmentError, match="consumption_overlap"):
        diagnose_head(first, contaminated)


def test_no_future_or_same_time_validation_and_no_incomplete_output():
    with pytest.raises(P5AssessmentError, match="time_order"):
        diagnose_head(checkpoint(), rows("dev", "2020-06-30"))
    partial = fit_head(rows(), policy=policy(), bindings=bindings(), stop_after=0)
    with pytest.raises(P5AssessmentError, match="incomplete"):
        predict_head(partial, (1.0, 7.0))


def test_raw_score_never_grants_calibrated_assertion_or_missing_data_sale():
    first = checkpoint()
    observed = predict_head(first, (1.0, 7.0))
    assert observed.raw_score is not None and 0 <= observed.raw_score <= 1
    assert observed.calibrated_probability is None
    assert observed.decision_state is DecisionState.ABSTAIN
    assert predict_head(first, (None, 7.0)).decision_state is DecisionState.UNAVAILABLE


def test_unseen_two_class_evaluation_has_rank_diagnostic_only():
    first = checkpoint()
    diagnostic = diagnose_head(first, rows("dev", "2020-09-30"))
    result = evaluation_summary(first, rows("eval", "2021-09-30"), diagnostic=diagnostic)
    assert result["raw_score_auc"] == 1.0
    assert result["calibrated_probability_metrics"] is None
    assert result["consumer_admission"] == "NOT_GRANTED"


def test_missing_evaluation_class_is_inconclusive_and_does_not_substitute_accuracy():
    first = checkpoint()
    diagnostic = diagnose_head(first, rows("dev", "2020-09-30"))
    result = evaluation_summary(
        first, rows("eval", "2021-09-30", (0, 0, 0, 0)), diagnostic=diagnostic
    )
    assert result["raw_score_auc"] is None
    assert result["reason"] == "REQUIRED_CLASS_MISSING_INCONCLUSIVE"


def test_repacked_unpaired_incremental_use_is_rejected():
    first = checkpoint()
    dev = rows("dev", "2020-09-30")
    second = update_head(first, dev, diagnostic=diagnose_head(first, dev))
    assert second is not None
    broken = replace(
        second, uses=tuple(u for u in second.uses if u.role != "development_validation")
    )
    with pytest.raises(P5AssessmentError, match="consumption_pair"):
        broken.validate()


def test_resume_rejects_repacked_consumption_identity_even_if_input_hash_unchanged():
    first = checkpoint()
    changed = replace(first.uses[0], security_id="unrelated-security")
    broken = replace(first, uses=(changed, *first.uses[1:]))
    with pytest.raises(P5AssessmentError, match="consumption_changed"):
        resume_head(broken, rows())


def test_evaluation_requires_bound_diagnostic_even_when_model_has_not_updated():
    first = checkpoint()
    diagnostic = diagnose_head(first, rows("dev", "2020-09-30"))
    with pytest.raises(TypeError):
        evaluation_summary(first, rows("eval", "2021-09-30"))
    with pytest.raises(P5AssessmentError, match="diagnostic_binding"):
        evaluation_summary(
            first,
            rows("eval", "2021-09-30"),
            diagnostic=replace(diagnostic, parent_digest="sha256:" + "f" * 64),
        )


def test_diagnostic_bytes_and_semantic_consumption_are_verified_on_cold_read():
    diagnostic = diagnose_head(checkpoint(), rows("dev", "2020-09-30"))
    raw = dump_diagnostic(diagnostic)
    with pytest.raises(P5AssessmentError, match="diagnostic_bytes"):
        load_diagnostic(raw + b" ", expected_sha256=byte_digest(raw))
    for uses in [
        [],
        [
            dict(
                sample_id="bad",
                security_id="s",
                episode_id="e",
                anchor="2025-01-01",
                generation=0,
                role="development_validation",
            )
        ],
    ]:
        value = json.loads(raw)
        value["uses"] = uses
        changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(P5AssessmentError):
            load_diagnostic(changed, expected_sha256=byte_digest(changed))


def test_v1_rejects_diagnostic_with_replaced_consumption_even_with_same_parent():
    first = checkpoint()
    dev = rows("dev", "2020-09-30")
    diagnostic = diagnose_head(first, dev)
    second = update_head(first, dev, diagnostic=diagnostic)
    assert second is not None
    changed = replace(
        diagnostic, uses=(replace(diagnostic.uses[0], security_id="changed"), *diagnostic.uses[1:])
    )
    with pytest.raises(P5AssessmentError, match="diagnostic_consumption"):
        evaluation_summary(second, rows("eval", "2021-09-30"), diagnostic=changed)


def test_finite_inputs_cannot_return_nonfinite_diagnostic_or_prediction():
    selected = (
        HeadRow("t0", "s0", "e0", "2020-06-30", (-1.0,), 0),
        HeadRow("t1", "s1", "e1", "2020-06-30", (1.0,), 1),
    )
    finite_policy = HeadPolicy(("x",), 2, 1, 1.0, 1e308, 0.0)
    first = fit_head(selected, policy=finite_policy, bindings=bindings())
    first.validate()
    dev = (
        HeadRow("d0", "s2", "e2", "2020-09-30", (-10.0,), 0),
        HeadRow("d1", "s3", "e3", "2020-09-30", (10.0,), 1),
    )
    with pytest.raises(P5AssessmentError):
        diagnose_head(first, dev)
    with pytest.raises(P5AssessmentError):
        predict_head(first, (10.0,))


def test_finite_values_that_overflow_scaler_fail_with_contract_error():
    selected = rows()
    changed = (replace(selected[0], features=(1e308, 7.0)), *selected[1:])
    with pytest.raises(P5AssessmentError, match="scaler_overflow"):
        fit_head(changed, policy=policy(), bindings=bindings())
