"""P11HistoryRunner seam tests (issue #718 E1, batch-A step 8792-phase1).

These cover only the adapter contract the adapter itself owns: schema marker,
missing bindings and missing P11 module all block the whole day by name
(history_inputs_unavailable).  The real envelope -> real scan/panel/expert ->
pinned decision_set round trip is covered by the P11-side H1 tests
(tests/unit/test_p11_history_input.py) and by the integration case once P11
ships its fixed construction entry.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quanthus.p12_daily.p11_history_runner import (
    HISTORY_INPUTS_UNAVAILABLE,
    RAW_HISTORY_SCHEMA,
    P11Bindings,
    P11HistoryRunner,
)
from quanthus.p12_daily.rule_runner import RunnerRequest
from quanthus.shared.fact_contracts import FactError


def _request(raw_history=None) -> RunnerRequest:
    return RunnerRequest(
        snapshot=None,
        next_session=None,
        holdings=(),
        pending_securities=(),
        published_at=datetime.now(timezone.utc),
        run_id="run-1",
        decision_cutoff=datetime.now(timezone.utc),
        raw_history=raw_history,
    )


def _runner(bindings=None) -> P11HistoryRunner:
    return P11HistoryRunner(
        runner_ref_id="p11.h1",
        runner_version="9584f70c",
        bindings=bindings,
    )


def test_missing_envelope_blocks() -> None:
    with pytest.raises(FactError) as exc:
        _runner().run(_request(raw_history=None))
    assert str(exc.value.reason_code) == HISTORY_INPUTS_UNAVAILABLE


def test_wrong_schema_marker_blocks() -> None:
    env = {"schema_version": "p11.raw-history-input.v0", "securities": []}
    with pytest.raises(FactError) as exc:
        _runner().run(_request(raw_history=env))
    assert str(exc.value.reason_code) == HISTORY_INPUTS_UNAVAILABLE


def test_envelope_without_bindings_blocks() -> None:
    env = {"schema_version": RAW_HISTORY_SCHEMA, "securities": []}
    with pytest.raises(FactError) as exc:
        _runner(bindings=None).run(_request(raw_history=env))
    assert str(exc.value.reason_code) == HISTORY_INPUTS_UNAVAILABLE


def test_runner_ref_is_versioned() -> None:
    r = _runner()
    assert r.runner_ref.ref_id == "p11.h1"
    assert r.runner_ref.version == "9584f70c"
    assert r.runner_ref.digest.startswith("sha256:")


def test_anchor_mode_forwarded_to_p11(monkeypatch: pytest.MonkeyPatch) -> None:
    """The October replay pins anchor_mode=equivalent_day; the adapter must
    forward it verbatim so P11 names the proof kind on membership_proof."""
    from types import SimpleNamespace

    import quanthus.product_daily.recommendation.history_input as hi

    captured: dict = {}

    def _fake(raw_history, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            result=SimpleNamespace(
                publishable=False,
                package=None,
                disposition="blocked",
                diagnostics=[],
            ),
            source_closure={"history_receipts": []},
            held_signals=(),
            receipt=None,
        )

    monkeypatch.setattr(hi, "run_daily_advice_from_history", _fake)
    env = {"schema_version": RAW_HISTORY_SCHEMA, "securities": []}
    bindings = P11Bindings(
        runner=object(),
        resources=object(),
        facts=object(),
        policy=object(),
        account=object(),
        card=object(),
        split_policy=object(),
        source_policy=object(),
        snapshot_id="snap",
        snapshot_manifest_digest="sha256:x",
    )
    out = P11HistoryRunner(
        runner_ref_id="p11.h1",
        runner_version="2babce03",
        bindings=bindings,
    ).run(_request(raw_history=env))
    assert captured["anchor_mode"] == "equivalent_day"
    assert out.publishable is False


def test_anchor_mode_is_caller_configurable() -> None:
    """anchor_mode is an injected evaluation config, not hardcoded; a strict
    binding forwards 'strict' so a consumer can pin the stored-anchor gate."""
    bindings = P11Bindings(
        runner=object(),
        resources=object(),
        facts=object(),
        policy=object(),
        account=object(),
        card=object(),
        split_policy=object(),
        source_policy=object(),
        snapshot_id="snap",
        snapshot_manifest_digest="sha256:x",
        anchor_mode="strict",
    )
    assert bindings.anchor_mode == "strict"
