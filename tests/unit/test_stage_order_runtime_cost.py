"""Runtime-only reuse and progress must preserve the frozen Stage-0/2 experiment."""
from __future__ import annotations

from dataclasses import asdict, replace

from quanthus import structure_v2_inputs as inputs
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.structure_v2_inputs import DailyInput, admit_daily, canonical, digest

from .test_structure_trend_evolution import sequence
from .test_structure_v2_inputs import policy, row


def uncached_member_digest(value: DailyInput) -> str:
    """The retained pre-optimization member identity formula."""
    return digest(
        {key: item for key, item in asdict(value).items() if key not in {"session_index", "cutoff"}}
    )


def test_immutable_member_digest_is_calculated_once_without_serialization_drift(monkeypatch):
    value = row()
    original_payload = asdict(value)
    original_bytes = canonical(value)
    expected = uncached_member_digest(value)
    original_digest = inputs.digest
    calls = 0

    def counted_digest(payload):
        nonlocal calls
        calls += 1
        return original_digest(payload)

    monkeypatch.setattr(inputs, "digest", counted_digest)
    for _ in range(20):
        assert value.member_digest == expected
    assert calls == 1
    assert asdict(value) == original_payload
    assert canonical(value) == original_bytes
    assert "member_digest" not in asdict(value)


def test_replacement_does_not_inherit_cached_member_identity():
    original = row()
    before = original.member_digest
    for changed in (
        replace(original, source_revision="corrected-source"),
        replace(original, action_member_digest="sha256:" + "b" * 64),
        replace(original, coordinate_epoch="changed-coordinate"),
        replace(original, listing_id="changed-listing"),
    ):
        assert "member_digest" not in vars(changed)
        assert changed.member_digest == uncached_member_digest(changed)
        assert changed.member_digest != before
    # These two caller coordinates are deliberately outside the existing member identity.
    reindexed = replace(original, session_index=original.session_index + 100)
    assert reindexed.member_digest == before
    assert original.member_digest == before


def test_complete_order_observation_output_matches_uncached_baseline(monkeypatch):
    values = sequence(160)
    admissions = tuple(admit_daily(value, policy()) for value in values)
    kwargs = {"report_start": values[0].market_date, "report_end": values[-1].market_date}
    cached = build_order_observations(admissions, **kwargs)
    monkeypatch.setattr(DailyInput, "member_digest", property(uncached_member_digest))
    uncached = build_order_observations(admissions, **kwargs)
    assert len(cached[0]) == 100
    assert cached == uncached
    assert canonical(cached) == canonical(uncached)


def test_progress_callback_is_observation_only():
    values = sequence(80)
    admissions = tuple(admit_daily(value, policy()) for value in values)
    kwargs = {"report_start": values[0].market_date, "report_end": values[-1].market_date}
    quiet = build_order_observations(admissions, **kwargs)
    progress = []
    reported = build_order_observations(
        admissions,
        **kwargs,
        progress=lambda completed, total, count: progress.append((completed, total, count)),
    )
    assert quiet == reported
    assert progress == [(1, 1, len(quiet[0]))]
