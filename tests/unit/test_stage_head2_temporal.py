"""Prospective temporal facts, support abstention and launch-order regressions."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quanthus.stage_learning.head2_revision import encode_reference_facts, revision_policy
from quanthus.stage_learning.head2_temporal import (
    build_temporal_facts,
    dispatch_temporal_stage,
    encode_temporal_features,
    feature_contract_digest,
    fit_matched_heads,
    mask_support,
    predict_supported,
)
from quanthus.stage_learning.local_head import HeadBindings, HeadRow, fit_head
from quanthus.stage_learning.p5_assessment import P5AssessmentError
from quanthus.structure_v2_inputs import Admission, admit_daily

from .test_structure_v2_inputs import SHA, policy, row


def window(current=None):
    values = [Decimal("100")] * 20 + [Decimal(str(v)) for v in (
        current if current is not None else [100, 101] * 10
    )]
    return tuple(admit_daily(row(i, str(v), high=v + Decimal('.01'),
                                low=v - Decimal('.01')), policy())
                 for i, v in enumerate(values))


def facts(values):
    return build_temporal_facts(values, anchor=values[-1].row.market_date)


def test_alias_is_separated_without_altering_original_seven_or_sources():
    late = [100.1,100,100.2,100.1,100,100.2,100.1,100,100.2,100.1,
            100,100.2,100.1,100,100.2,99,102,101.5,101.2,101]
    full = [Decimal('100')] + [Decimal(str(x)) for x in late]
    early = [Decimal('201') - x for x in reversed(full)][1:]
    a, b = window(late), window(early)
    before = [asdict(v) for v in a]
    fa, fb = facts(a), facts(b)
    ea = encode_temporal_features(fa, source_digest=SHA)
    eb = encode_temporal_features(fb, source_digest=SHA)
    assert ea.values is not None and eb.values is not None
    assert ea.values[:7] == eb.values[:7]
    assert ea.values[7:] != eb.values[7:]
    primary, path = fa.base.reference_facts()
    old = encode_reference_facts(primary, path, anchor=a[-1].row.market_date.isoformat(),
                                 source_digest=SHA)
    assert ea.values[:7] == old.values
    assert before == [asdict(v) for v in a]
    assert fa.digest == facts(a).digest and fa.digest != fb.digest


@pytest.mark.parametrize('scale,offset', [('3','17'), ('1','35'), ('-1','250')])
def test_new_facts_reflection_scale_translation_invariant(scale, offset):
    original = window()
    multiplier, shift = Decimal(scale), Decimal(offset)
    changed = []
    for a in original:
        r = a.row
        lo, hi = sorted((r.low * multiplier + shift, r.high * multiplier + shift))
        changed.append(replace(a, row=replace(r, high=hi, low=lo,
                                              close=r.close * multiplier + shift)))
    assert facts(original).values == facts(changed).values


def test_boundary_equality_counts_inside_and_exact_decimal_context():
    selected = list(window([100] * 15 + ['100.01', '99.99', '100.010000000001',
                                         '99.989999999999', '100']))
    result = facts(selected)
    assert result.values[0] == Decimal('0.4')
    assert all(Decimal(0) <= x <= Decimal(1) for x in result.values)


@pytest.mark.parametrize('current,expected,reason', [
    ([100] * 20, Decimal(0), 'no-observed-transition'),
    ([100] * 19 + [101], Decimal(1) / Decimal(19), 'available'),
    ([99, 101] * 10, Decimal(1), 'available'),
])
def test_exact_zero_sign_handling(current, expected, reason):
    result = facts(window(current))
    assert result.values[1] == expected
    assert result.reasons[1] == reason


def test_midpoint_zero_signs_removed_before_transitions():
    # Extrema99/101 give midpoint100. Zero signs are removed, not a transition.
    values = window([99, 100, 101, 100, 99] + [100] * 15)
    assert facts(values).values[1] == Decimal(1)


@pytest.mark.parametrize('kind', ['missing', 'reset', 'nonadmitted'])
def test_unavailable_is_not_imputed(kind):
    values = list(window())
    a = values[10]
    if kind == 'missing':
        values[10] = replace(a, row=replace(a.row, close=None))
    elif kind == 'reset':
        values[10] = replace(a, row=replace(a.row, coordinate_epoch='other'))
    else:
        values[10] = Admission(a.row, False, 'original_missing', False)
    result = facts(values)
    assert not result.available and result.values is None
    assert encode_temporal_features(result, source_digest=SHA).values is None


@pytest.mark.parametrize('kind', ['future', 'identity', 'session', 'count'])
def test_invalid_window_rejected(kind):
    values = list(window())
    if kind == 'future':
        values[-1] = replace(values[-1], row=replace(values[-1].row,
                              market_date=values[-1].row.market_date + timedelta(days=1)))
        with pytest.raises(ValueError, match='ANCHOR'):
            build_temporal_facts(values, anchor=window()[-1].row.market_date)
        return
    if kind == 'identity':
        values[10] = replace(values[10], row=replace(values[10].row, listing_id='other'))
    elif kind == 'session':
        values[10] = replace(values[10], row=replace(values[10].row, session_index=100))
    else:
        values.pop()
    with pytest.raises(ValueError):
        facts(values)


def test_source_revision_changes_digest_without_changing_decimal_or_times():
    values = list(window())
    before = facts(values)
    values[0] = replace(values[0], row=replace(values[0].row, source_revision='different'))
    after = facts(values)
    assert before.values == after.values and before.digest != after.digest
    assert before.base.anchor == after.base.anchor


def training():
    return tuple(HeadRow(f's{i}', f'sec{i}', f'ep{i}', '2021-03-31',
                        (.1 + i/100, .1, -.2, .01, .3, .2, 0.), i % 2)
                 for i in range(4))


def test_unseen_mask_abstains_and_seen_mask_retains_raw_research_score():
    from quanthus.stage_learning.head2_revision import feature_contract_digest as original_digest
    train = training()
    support = mask_support(train)
    bindings = replace(HeadBindings(*([SHA] * 8)), features=original_digest())
    model = fit_head(train, policy=revision_policy(), bindings=bindings)
    unseen = predict_supported(model, (0., .1, -.2, .01, 0., 0., 1.), support=support)
    assert unseen.raw_score is None
    assert unseen.reason == 'ABSTAIN_UNSEEN_ZERO_NET_GEOMETRY'
    seen = predict_supported(model, train[0].features, support=support)
    assert seen.raw_score is not None and seen.calibrated_probability is None
    other = mask_support([replace(train[0], sample_id='other'), *train[1:]])
    with pytest.raises(P5AssessmentError, match='support_binding'):
        predict_supported(model, train[0].features, support=other)
    with pytest.raises(P5AssessmentError, match='support_binding'):
        predict_supported(model, train[0].features, support=replace(support, observed=(0, 1)))
    with pytest.raises(P5AssessmentError, match='model_feature_binding'):
        predict_supported(replace(model, bindings=replace(bindings, features=SHA)),
                          train[0].features, support=support)


def test_matched_heads_same40_identity_labels_optimizer_and_support():
    from quanthus.stage_learning.head2_revision import feature_contract_digest as original_digest
    train = tuple(HeadRow(f's{i:02}', f'sec{i}', f'ep{i}', '2021-08-31',
                         (*training()[i % 4].features, .2, .4), int(i < 12))
                  for i in range(40))
    b7 = replace(HeadBindings(*([SHA] * 8)), features=original_digest())
    b9 = replace(b7, features=feature_contract_digest())
    seven, nine = fit_matched_heads(train, seven_bindings=b7, nine_bindings=b9)
    assert seven.uses == nine.uses and seven.cursor == nine.cursor == 200
    assert seven.means == nine.means[:7] and seven.scales == nine.scales[:7]
    assert seven.policy.learning_rate == nine.policy.learning_rate == .05
    support7 = mask_support([replace(r, features=r.features[:7]) for r in train])
    support9 = mask_support(train)
    assert support7.observed == support9.observed == (0,)
    assert predict_supported(seven, (0., .1, -.2, .01, 0., 0., 1.),
                             support=support7).raw_score is None
    assert predict_supported(nine, (0., .1, -.2, .01, 0., 0., 1., .2, .4),
                             support=support9).raw_score is None
    for bad in (train[:-1], [replace(train[0], anchor='2021-09-01'), *train[1:]],
                [replace(train[0], label=0), *train[1:]],
                [replace(train[0], security_id=train[1].security_id), *train[1:]]):
        with pytest.raises(P5AssessmentError, match='exact_train40'):
            fit_matched_heads(bad, seven_bindings=b7, nine_bindings=b9)


@pytest.mark.parametrize('features', [(0.,)*6, (0.,)*6 + (.5,), (float('nan'),)*7])
def test_mask_support_requires_valid_original_encoding(features):
    with pytest.raises(P5AssessmentError):
        mask_support([replace(training()[0], features=features)])


def dispatch_kwargs(tmp_path):
    source = tmp_path / 'input.json'
    source.write_text('{}')
    import hashlib
    return dict(output=tmp_path / 'new-output', stage='fit-seven',
                command=[sys.executable, '-c', 'pass'],
                inputs={source: hashlib.sha256(source.read_bytes()).hexdigest()},
                deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
                free_floor_bytes=0, reserved_bytes=1024)


@pytest.mark.parametrize('failure', ['deadline', 'digest', 'space', 'symlink', 'stage'])
def test_dispatch_preflight_rejection_creates_no_output(tmp_path, failure):
    kw = dispatch_kwargs(tmp_path)
    if failure == 'deadline':
        kw['deadline'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif failure == 'digest':
        kw['inputs'] = {next(iter(kw['inputs'])): '0' * 64}
    elif failure == 'space':
        kw['reserved_bytes'] = 2**100
    elif failure == 'symlink':
        target = tmp_path / 'target'
        target.mkdir()
        link = tmp_path / 'link'
        link.symlink_to(target, target_is_directory=True)
        kw['output'] = link / 'new-output'
    else:
        kw['stage'] = '../bad'
    with pytest.raises((ValueError, OSError)):
        dispatch_temporal_stage(**kw)
    assert not kw['output'].exists()
    assert not (tmp_path / 'new-output').exists()
    assert next(iter(kw['inputs'])).read_text() == '{}'


def test_dispatch_identity_is_durable_before_child_invocation(tmp_path):
    kw = dispatch_kwargs(tmp_path)
    kw['command'] = [sys.executable, '-c',
        "import json,pathlib; p=pathlib.Path('launch.json'); "
        "x=json.loads(p.read_text()); assert x['status']=='PREPARED_NOT_EXECUTED'; "
        "assert x['inputs']; pathlib.Path('observed-receipt').write_text('yes')"]
    dispatch_temporal_stage(**kw)
    out = kw['output']
    assert (out / 'observed-receipt').read_text() == 'yes'
    assert json.loads((out / 'complete.json').read_text())['returncode'] == 0


def test_dispatch_failure_preserves_launch_without_complete_or_retry(tmp_path):
    import subprocess
    kw = dispatch_kwargs(tmp_path)
    kw['command'] = [sys.executable, '-c', 'raise SystemExit(7)']
    with pytest.raises(subprocess.CalledProcessError):
        dispatch_temporal_stage(**kw)
    assert (kw['output'] / 'launch.json').is_file()
    assert not (kw['output'] / 'complete.json').exists()
    with pytest.raises(FileExistsError):
        dispatch_temporal_stage(**kw)
