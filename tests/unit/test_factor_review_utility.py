"""Frozen five-session diagnostic, unknown bounds and dependent-observation handling."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.stage_learning.factor_review_utility import _paired, evaluate, load_contract
from quanthus.stage_learning.factor_slice import (
    FactorSliceError,
    PanelIdentity,
    consume_queue,
    project_facts,
)
from quanthus.structure_v2_inputs import admit_daily, digest
from tests.unit.test_factor_slice import DEFINITION, values
from tests.unit.test_structure_v2_inputs import SHA, policy

REPO = Path(__file__).resolve().parents[2]


def diagnostic(rows):
    admissions = tuple(admit_daily(row, policy()) for row in rows)
    anchors = {}
    facts = project_facts(admissions, spec_digest=SHA, calendar_ref=SHA,
                          definition_digest=DEFINITION, anchors=anchors)
    identity = PanelIdentity(digest(facts), DEFINITION, tuple(
        (a.row.security_id, a.row.market_date.isoformat(), a.row.member_digest)
        for a in admissions))
    base = consume_queue(facts, volume_gate=False, identity=identity)
    candidate = consume_queue(facts, volume_gate=True, identity=identity)
    return evaluate(admissions, facts, anchors, base, candidate,
                    identity=identity, spec=load_contract(REPO))


def breakout_rows(count=26):
    rows = values(count)
    for i in range(20, count):
        rows[i] = replace(rows[i], high=Decimal(115), close=Decimal(110))
    return rows


@pytest.mark.parametrize("future_close,expected", [("99", "quick_failure"),
                                                  ("100", "no_quick_failure"),
                                                  ("110", "no_quick_failure")])
def test_target_uses_strict_below_T_anchor_and_exact_five_future_sessions(future_close, expected):
    rows = breakout_rows()
    rows[23] = replace(rows[23], close=Decimal(future_close))
    result = diagnostic(rows)
    assert result["safe"]["full_price_pool"] == 1
    assert result["private_rows"][0]["outcome"] == expected
    assert result["private_rows"][0]["future_rows"] == 5


def test_sixth_future_session_never_enters_five_session_outcome():
    rows = breakout_rows(27)
    rows[26] = replace(rows[26], close=Decimal(1))
    assert diagnostic(rows)["private_rows"][0]["outcome"] == "no_quick_failure"


@pytest.mark.parametrize("kind,reason", [
    ("tail", "scope_end_incomplete_five_sessions"),
    ("action", "coordinate_or_listing_boundary"),
    ("gap", "session_gap"),
    ("nontrading", "unavailable_future:suspended"),
])
def test_incomplete_or_incomparable_windows_stay_unknown(kind, reason):
    rows = breakout_rows(25 if kind == "tail" else 26)
    if kind == "action":
        for i in range(22, len(rows)):
            rows[i] = replace(rows[i], coordinate_epoch="action-boundary")
    elif kind == "gap":
        for i in range(22, len(rows)):
            rows[i] = replace(rows[i], session_index=i+1)
    elif kind == "nontrading":
        rows[22] = replace(rows[22], quality_reason="suspended")
    outcome = diagnostic(rows)["private_rows"][0]
    assert outcome["outcome"] == "unknown"
    assert reason in outcome["reasons"]
    assert outcome["historical_knowledge_cutoff"] is None


def test_common_unknowns_cancel_but_remain_in_both_arm_denominators():
    common = ("shared", "2021-01-01")
    removed, added = ("r", "2021-01-02"), ("a", "2021-01-02")
    outcomes = {common: {"outcome": "unknown"}, removed: {"outcome": "quick_failure"},
                added: {"outcome": "no_quick_failure"}}
    result = _paired({common, removed}, {common, added}, outcomes)
    assert result["slots_per_arm"] == 2 and result["common_slots"] == 1
    assert result["worst_case_net_fewer_quick_failures"] == 1
    assert result["best_case_net_fewer_quick_failures"] == 1
    assert result["changed_unknown_control"] == result["changed_unknown_candidate"] == 0


def test_changed_unknowns_get_worst_and_best_bounds_not_zero_filling():
    removed, added = ("r", "2021-01-02"), ("a", "2021-01-02")
    result = _paired({removed}, {added}, {
        removed: {"outcome": "unknown"}, added: {"outcome": "quick_failure"}})
    assert result["worst_case_net_fewer_quick_failures"] == -1
    assert result["best_case_net_fewer_quick_failures"] == 0
    assert result["bootstrap"] is None
    assert result["disposition"] == "PARK_VOLUME_GATE_INCONCLUSIVE_OR_UNFAVOURABLE"


def test_repeated_days_of_one_issuer_cannot_manufacture_independent_acceptance():
    control = {("one_issuer", f"2021-01-{i:02d}") for i in range(1, 6)}
    candidate = {("other_issuer", f"2021-01-{i:02d}") for i in range(1, 6)}
    outcomes = {key: {"outcome": "quick_failure"} for key in control}
    outcomes.update({key: {"outcome": "no_quick_failure"} for key in candidate})
    result = _paired(control, candidate, outcomes)
    assert result["affected_securities"] == 2
    assert result["bootstrap"]["clusters"] == 2
    assert result["minimum_net_after_removing_one_security"] == 0
    assert result["disposition"] == "PARK_VOLUME_GATE_INCONCLUSIVE_OR_UNFAVOURABLE"


def test_target_contract_cannot_change_after_freeze():
    spec = deepcopy(load_contract(REPO))
    spec["target"]["horizon_exchange_sessions"] = 10
    with pytest.raises(FactorSliceError, match="FROZEN_CONTRACT"):
        evaluate((), [], {}, {}, {}, identity=None, spec=spec)
