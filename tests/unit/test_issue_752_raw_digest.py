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

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.product_daily.contracts import ProductDailyError
from tests.unit.test_p11_daily_runner import (
    _identity,
    _raw_security,
    _run,
    _trend_raw,
)
from tests.unit.test_p11_daily_runner import (
    resources as resources,  # noqa: F401
)
from tests.unit.test_p11_daily_runner import (
    runner as runner,  # noqa: F401
)


def _digest_for(code):
    return sha256_digest({"security": code})


def _malformed(code):
    return replace(
        _raw_security(code),
        identity=replace(_identity(code), source_evidence_digest="malformed-digest"),
    )


def _missing(code):
    return replace(
        _raw_security(code), identity=replace(_identity(code), source_evidence_digest="")
    )


def _entry_fields(decision):
    """The full business fields of one published entry/opportunity -- every
    consumer-visible field a P12 decision set carries, not just the code."""
    base = {
        "security_code": decision.security_code,
        "disposition": str(decision.disposition),
        "event_id": decision.event_id,
        "entry_allowed": decision.entry_allowed,
        "target_maximum_position": (
            None
            if decision.target_maximum_position is None
            else str(decision.target_maximum_position)
        ),
        "reason": decision.reason,
        "expert_decision_digest": decision.expert_decision_digest,
        "signal_close": None
        if decision.signal_close is None
        else decision.signal_close.isoformat(),
        "knowledge_cutoff": None
        if decision.knowledge_cutoff is None
        else decision.knowledge_cutoff.isoformat(),
        "decision_at": None if decision.decision_at is None else decision.decision_at.isoformat(),
    }
    if decision.decision is not None:
        base["formal_decision"] = decision.decision.to_dict()
    if decision.features is not None:
        base["feature_listing_sessions"] = decision.features.listing_sessions
    return base


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
    excl = [e for e in out.source_closure["exclusions"] if e["security_code"] == "600001"]
    assert excl and excl[0]["reason"] == "raw_input_closure_invalid"
    assert excl[0]["scope"] == "per_security"
    assert "EventContractError" in excl[0]["member_or_field"]
    assert "source_evidence_digest" in excl[0]["member_or_field"]
    # The new field appears only on an actual digest-contract breach.
    assert excl[0]["source_evidence_digest"] == "malformed-digest"


def test_752_legal_alone_matches_legal_when_mixed(runner, resources):
    """The legal security's complete published entry is identical whether it
    runs alone or next to the malformed neighbour -- isolation is clean.

    Compared per business field on the real seam decision (disposition,
    event, entry flag, target position, reason, expert digest, the three
    clocks, the formal decision body, and the consumed feature projection),
    not just the security-code set.  Closure/receipt identities (run_id,
    digests) are recorded independently per run and are expected to differ.
    """
    solo = _run(runner, resources, [_trend_raw("600000")])
    mixed = _run(runner, resources, [_trend_raw("600000"), _malformed("600001")])

    solo_entry = [d for d in solo.seam.decisions if d.entry_allowed]
    mixed_entry = [d for d in mixed.seam.decisions if d.entry_allowed]
    assert [d.security_code for d in solo_entry] == ["600000"]
    assert [d.security_code for d in mixed_entry] == ["600000"]
    # Complete business-field comparison on the published entry.
    assert _entry_fields(solo_entry[0]) == _entry_fields(mixed_entry[0])

    # The produced candidate/opportunity carries the same consumer fields.
    solo_cand = [c for c in solo.candidates if c.security_code == "600000"]
    mixed_cand = [c for c in mixed.candidates if c.security_code == "600000"]
    assert len(solo_cand) == len(mixed_cand) == 1
    for field in (
        "opportunity_id",
        "security_name",
        "industry_id",
        "fraction",
        "price_bound",
        "mark_price",
        "component_ref_id",
        "component_version",
        "reason_codes",
        "explanations",
        "invalidations",
        "next_observation",
        "depends_on_member_ids",
        "expert_decision_digest",
        "risk_decision_digest",
    ):
        assert getattr(solo_cand[0], field) == getattr(mixed_cand[0], field), field

    # Per-run closure/receipt identities are independently bound and differ
    # (different raw input sets -> different digests); record both, compare
    # their stable identities, not their digests.
    assert solo.receipt is not None and mixed.receipt is not None
    assert solo.receipt.producer_id == mixed.receipt.producer_id
    assert solo.receipt.producer_version == mixed.receipt.producer_version
    assert solo.source_closure["schema_version"] == mixed.source_closure["schema_version"]
    assert solo.source_closure["code_provenance"] == mixed.source_closure["code_provenance"]
    # The mixed run's closure additionally names the excluded neighbour.
    assert any(e["security_code"] == "600001" for e in mixed.source_closure["exclusions"])
    assert not any(e["security_code"] == "600001" for e in solo.source_closure["exclusions"])


# ---- missing and malformed digests each isolated in a mixed positive ---------


def test_752_missing_digest_isolated_too(runner, resources):
    """An empty source_evidence_digest is the same contract breach and the
    same named per-security isolation."""
    out = _run(
        runner,
        resources,
        [_trend_raw("600000"), _missing("600001")],
    )
    assert out.result.package is not None
    excl = [e for e in out.source_closure["exclusions"] if e["security_code"] == "600001"]
    assert excl and "EventContractError" in excl[0]["member_or_field"]
    assert excl[0]["source_evidence_digest"] == ""


def test_752_missing_digest_neighbour_also_isolated(runner, resources):
    """A missing digest enters the same mixed positive case: the legal
    neighbour publishes and the bad member is a named exclusion with the
    empty digest field recorded."""
    out = _run(
        runner,
        resources,
        [_trend_raw("600000"), _missing("600001")],
    )
    entries = [d for d in out.seam.decisions if d.entry_allowed]
    assert [d.security_code for d in entries] == ["600000"]
    excl = [e for e in out.source_closure["exclusions"] if e["security_code"] == "600001"]
    assert excl and excl[0]["reason"] == "raw_input_closure_invalid"
    assert excl[0]["source_evidence_digest"] == ""


# ---- held member keeps its alert --------------------------------------------


def test_752_held_member_keeps_hold_alert(runner, resources):
    """A held account member whose market input carries a malformed digest
    still surfaces its independent hold_alert; the exclusion does not become
    a forced exit or a lost account member."""
    raw = replace(
        _malformed("600001"),
        held_membership=True,
        held_quantity=100,
    )
    out = _run(runner, resources, [_trend_raw("600000"), raw])
    held = [
        (d.security_code, d.action) for d in out.seam.held_decisions if d.security_code == "600001"
    ]
    assert held == [("600001", "hold_alert")]
    # The bad market row is still a named exclusion.
    assert any(
        e["security_code"] == "600001" and "EventContractError" in e["member_or_field"]
        for e in out.source_closure["exclusions"]
    )


# ---- original exclusion shape is unchanged for non-digest failures -----------


def test_752_product_error_exclusion_keeps_original_shape(runner, resources):
    """A ProductDailyError exclusion (not a digest breach) keeps its original
    keys -- the new source_evidence_digest field is only added on an actual
    EventContractError, so the existing audit shape is preserved."""
    bad = replace(_trend_raw("600001"), close_at_by_date={})
    out = _run(runner, resources, [_trend_raw("600000"), bad])
    excl = [e for e in out.source_closure["exclusions"] if e["security_code"] == "600001"]
    assert excl and excl[0]["reason"] == "raw_input_closure_invalid"
    assert excl[0]["scope"] == "per_security"
    assert "ProductDailyError" in excl[0]["member_or_field"]
    assert "source_evidence_digest" not in excl[0]


# ---- shared/global contract failures still block ------------------------------


def test_752_all_malformed_still_blocks_day(runner, resources):
    """Every security malformed -> the day still fails (no manufactured
    qualified-empty pass); the shared block is unchanged."""
    with pytest.raises(ProductDailyError, match="missing_raw_inputs"):
        _run(runner, resources, [_malformed("600000"), _malformed("600001")])


def test_752_shared_contract_failure_still_blocks(runner, resources):
    """A non-digest contract breach outside the per-security loop (e.g. an
    empty run_id) keeps its original blocking semantics -- it is a day-level
    failure, never downgraded to a per-security exclusion."""
    with pytest.raises(ProductDailyError):
        _run(
            runner,
            resources,
            [_trend_raw("600000")],
            run_id="",
        )


def test_752_shared_prereq_failure_blocks_day(runner, resources):
    """An actual shared-prerequisite failure is a whole-day block with the
    declared reason and no per-security fallback -- the isolation only
    covers the per-security projection, not a global source gate."""
    out = _run(
        runner,
        resources,
        [_trend_raw("600000")],
        shared_prereq_ok=False,
        shared_prereq_reason="shared_source_gate_failed",
    )
    assert out.seam.day_blocked
    assert out.seam.day_block_reason == "shared_source_gate_failed"
    assert out.seam.decisions == ()
    # A blocked day is not a publishable package and names no exclusions.
    assert not out.result.publishable
