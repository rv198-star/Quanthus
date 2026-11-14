"""Independent arithmetic and lineage checks for the scheduled R10 mode."""

import hashlib
import math
from dataclasses import replace

import pytest

from quanthus.r10_learning import (
    begin_scheduled_correction,
    dump_scheduled_correction,
    load_scheduled_correction,
    resume_scheduled_correction,
    scheduled_score,
)
from quanthus.stage_learning import head2_revision as hr
from quanthus.stage_learning.local_head import (
    HeadBindings,
    HeadRow,
    diagnose_head,
    dump_checkpoint,
    fit_head,
)
from quanthus.stage_learning.p5_assessment import P5AssessmentError


def fixture():
    def group(prefix, count, anchor, positives):
        return tuple(
            HeadRow(
                f"{prefix}{i:02}",
                f"sec-{prefix}{i}",
                f"episode-{prefix}{i}",
                anchor,
                (i / 25, 0.1 + i / 500, -0.4 + i / 100, 0.01, 0.8, 0.4, 0.0),
                int(i < positives),
            )
            for i in range(count)
        )

    train = group("t", 24, "2020-12-31", 5)
    update = group("u", 7, "2021-05-31", 4)
    guard = group("g", 4, "2021-05-31", 2)
    bindings = replace(
        HeadBindings(*("sha256:" + str(i) * 64 for i in range(8))),
        features=hr.feature_contract_digest(),
    )
    parent = fit_head(train, policy=hr.revision_policy(), bindings=bindings)
    sha = "sha256:" + hashlib.sha256(dump_checkpoint(parent)).hexdigest()
    return parent, train, update, guard, sha


def start(stop_after=None):
    parent, train, update, guard, sha = fixture()
    return begin_scheduled_correction(
        parent,
        train,
        update,
        guard,
        contract_digest="sha256:" + "a" * 64,
        parent_file_sha256=sha,
        stop_after=stop_after,
    ), (parent, train, update, guard, sha)


def test_unequal_groups_independent_one_and_100_steps():
    child, (parent, train, update, _, _) = start(1)
    weights, bias = list(parent.weights), parent.bias
    for step in range(100):
        grad = [0.0] * 7
        intercept = 0.0
        for group in [train, update]:
            for row in group:
                x = [
                    (v - m) / s
                    for v, m, s in zip(row.features, parent.means, parent.scales, strict=True)
                ]
                raw = sum(a * b for a, b in zip(weights, x, strict=True)) + bias
                error = 1 / (1 + math.exp(-raw)) - row.label
                intercept += 0.5 * error / len(group)
                for j in range(7):
                    grad[j] += 0.5 * error * x[j] / len(group)
        weights = [w - 0.05 * (g + 0.01 * w) for w, g in zip(weights, grad, strict=True)]
        bias -= 0.05 * intercept
        if step == 0:
            assert child.update.weights == pytest.approx(weights, abs=1e-14)
            assert child.update.bias == pytest.approx(bias, abs=1e-14)
    complete, _ = start()
    assert complete.update.weights == pytest.approx(weights, abs=1e-14)
    assert complete.update.bias == pytest.approx(bias, abs=1e-14)
    assert complete.update.parent.means == parent.means
    assert complete.update.parent.scales == parent.scales


def test_resume_canonical_parent_and_changed_inputs():
    child, (parent, train, update, guard, sha) = start(17)
    raw = dump_scheduled_correction(child)
    loaded = load_scheduled_correction(
        raw,
        expected_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
        contract_digest=child.contract_digest,
        parent_file_sha256=sha,
        parent_digest=parent.digest,
        parent_bindings=parent.bindings,
    )
    complete = resume_scheduled_correction(loaded, train, update, guard)
    assert dump_scheduled_correction(complete) == dump_scheduled_correction(start()[0])
    assert dump_scheduled_correction(
        resume_scheduled_correction(complete, train, update, guard)
    ) == dump_scheduled_correction(complete)
    with pytest.raises(P5AssessmentError):
        resume_scheduled_correction(
            loaded, train, [replace(update[0], label=0), *update[1:]], guard
        )
    with pytest.raises(P5AssessmentError):
        scheduled_score(child, train[0].features)


def test_guard_not_in_gradient_and_legacy_trigger_stays_closed():
    child, (parent, train, update, guard, sha) = start()
    changed_guard = [replace(r, label=1 - r.label) for r in guard]
    other = begin_scheduled_correction(
        parent,
        train,
        update,
        changed_guard,
        contract_digest=child.contract_digest,
        parent_file_sha256=sha,
    )
    assert other.update.weights == child.update.weights and other.update.bias == child.update.bias
    # A diagnostic from exact parent predictions is below the old trigger.
    easy = [
        replace(r, label=int(hr.legacy.predict_head(parent, r.features).raw_score >= 0.5))
        for r in update
    ]
    diagnostic = diagnose_head(parent, easy)
    assert diagnostic.raw_log_loss < parent.policy.update_loss_trigger
    assert hr.begin_update(parent, train, easy, guard, diagnostic=diagnostic) is None
    assert child.update.cursor == 100
    assert hashlib.sha256(dump_checkpoint(parent)).hexdigest() == sha.removeprefix("sha256:")


def test_overlap_unknown_wrong_parent_and_wrong_mode_reject():
    child, (parent, train, update, guard, sha) = start()
    for changed in [
        replace(child, mode="loss_trigger"),
        replace(child, parent_file_sha256="sha256:" + "b" * 64),
    ]:
        with pytest.raises(P5AssessmentError):
            changed.validate()
    for bad in [
        [replace(update[0], label=2), *update[1:]],
        [replace(update[0], security_id=train[0].security_id), *update[1:]],
    ]:
        with pytest.raises(P5AssessmentError):
            begin_scheduled_correction(
                parent,
                train,
                bad,
                guard,
                contract_digest=child.contract_digest,
                parent_file_sha256=sha,
            )
