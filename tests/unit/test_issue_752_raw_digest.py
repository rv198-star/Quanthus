"""Issue #752: a single security's malformed source_evidence_digest aborts the
whole advice batch instead of becoming a named per-security exclusion.

The per-security projection boundary in run_daily_advice_from_raw only
caught ProductDailyError/CandidateScanError; CandidateDailyInput's digest
contract raises EventContractError, so one bad neighbour collapsed the run.
The fix keeps the digest legality unchanged and isolates the failing
security: it is excluded with the field name and reason inside
source_closure, a held member still surfaces hold_alert, and a legal
neighbour publishes unchanged.  Shared/global contract failures keep their
original blocking semantics -- this never becomes a catch-Exception.
"""

from __future__ import annotations

from dataclasses import replace
from dataclasses import replace as _replace

import pytest

from quanthus.event_library.contracts import sha256_digest
from tests.unit.test_p11_daily_runner import (
    CARD,
    _held_facts,
    _identity,
    _producer_inputs,
    _prospective_source,
    _prospective_split,
    _raw_security,
    _run,
    _trend_raw,
    resources as resources,  # noqa: F401
    runner as runner,  # noqa: F401
)


def _digest_for(code):
    return sha256_digest({"security": code})


def _malformed(code):
    return _replace(_raw_security(code), identity=replace(
        _identity(code), source_evidence_digest="malformed-digest"
    ))


def _missing(code):
    return _replace(_raw_security(code), identity=replace(
        _identity(code), source_evidence_digest=""
    ))


# ---- single malformed neighbour does not abort the day ------------------------


def test_752_legal_neighbour_survives_malformed_digest(runner, resources):
    """600000 legal + 600001 malformed -> the legal one still produces a real
    entry candidate; the bad one is a named per-security exclusion carrying
    its source_evidence_digest field and reason."""
    out = _run(
        runner,
        resources,
        [_trend_raw("600000"), _malformed("600001")],
    )
    assert out.result.package is not None
    entries = [d for d in out.seam.decisions if d.entry_allowed]
    assert any(d.security_code == "600000" for d in entries)
    excl = [
        e for e in out.source_closure["exclusions"] if e["security_code"] == "600001"
    ]
    assert excl and excl[0]["reason"] == "raw_input_closure_invalid"
    assert excl[0]["scope"] == "per_security"
    assert "EventContractError" in excl[0]["member_or_field"]
    assert "source_evidence_digest" in excl[0]["member_or_field"]
    assert excl[0]["source_evidence_digest"] == "malformed-digest"


def test_752_legal_alone_matches_legal_when_mixed(runner, resources):
    """The legal security's own result is identical whether it runs alone
    or next to the malformed neighbour -- the isolation is clean."""
    solo = _run(runner, resources, [_trend_raw("600000")])
    mixed = _run(runner, resources, [_trend_raw("600000"), _malformed("600001")])
    solo_entries = {
        d.security_code for d in solo.seam.decisions if d.entry_allowed
    }
    mixed_entries = {
        d.security_code for d in mixed.seam.decisions if d.entry_allowed
    }
    assert solo_entries == mixed_entries == {"600000"}


# ---- missing digest also isolated, held member keeps its alert ----------------


def test_752_missing_digest_isolated_too(runner, resources):
    """An empty source_evidence_digest is the same contract breach and the
    same named per-security isolation."""
    out = _run(
        runner,
        resources,
        [_trend_raw("600000"), _missing("600001")],
    )
    assert out.result.package is not None
    excl = [
        e for e in out.source_closure["exclusions"] if e["security_code"] == "600001"
    ]
    assert excl and "EventContractError" in excl[0]["member_or_field"]


def test_752_held_member_keeps_hold_alert(runner, resources):
    """A held account member whose market input carries a malformed digest
    still surfaces its independent hold_alert; the exclusion does not become
    a forced exit or a lost account member."""
    raw = _replace(
        _malformed("600001"),
        held_membership=True,
        held_quantity=100,
    )
    out = _run(runner, resources, [_trend_raw("600000"), raw])
    held = [
        (d.security_code, d.action)
        for d in out.seam.held_decisions
        if d.security_code == "600001"
    ]
    assert held == [("600001", "hold_alert")]
    # The bad market row is still a named exclusion.
    assert any(
        e["security_code"] == "600001" and "EventContractError" in e["member_or_field"]
        for e in out.source_closure["exclusions"]
    )


# ---- shared/global contract failures still block ------------------------------


def test_752_all_malformed_still_blocks_day(runner, resources):
    """Every security malformed -> the day still fails (no manufactured
    qualified-empty pass); the shared block is unchanged."""
    from quanthus.product_daily.contracts import ProductDailyError

    with pytest.raises(ProductDailyError, match="missing_raw_inputs"):
        _run(runner, resources, [_malformed("600000"), _malformed("600001")])


def test_752_shared_contract_failure_still_blocks(runner, resources):
    """A non-digest contract breach outside the per-security loop (e.g. a
    malformed policy/facts object) keeps its original blocking semantics --
    never downgraded to a per-security exclusion."""
    from quanthus.product_daily.contracts import ProductDailyError

    # A shared contract breach (empty run_id) is a day-level failure, never
    # a per-security exclusion -- the isolation only covers the per-security
    # projection inside the loop.
    with pytest.raises(ProductDailyError):
        _run(
            runner,
            resources,
            [_trend_raw("600000")],
            run_id="",
        )
