from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, timedelta

import pytest

from quanthus.stage_learning.entry_consolidation import (
    EntryOpportunity,
    diagnose_entry,
    pre_entry_window,
    summarize,
)
from quanthus.stage_learning.head2_revision import FEATURE_NAMES
from quanthus.stage_learning.local_head import HeadBindings, HeadCheckpoint, HeadPolicy, RowUse
from quanthus.stage_learning.order_rule_screen import load_rule
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import SHA, policy, row


def fixture():
    sessions = tuple(date(2020, 1, 2) + timedelta(days=i) for i in range(42))
    admissions = tuple(admit_daily(row(i), policy()) for i in range(40))
    model = HeadCheckpoint(
        HeadPolicy(FEATURE_NAMES, 200, 100, 0.05, 0.01, 0.69),
        HeadBindings(*([SHA] * 8)), (0.0,) * 7, (1.0,) * 7, (0.0,) * 7,
        0.0, 200, 0, SHA,
        (RowUse("train", "train-security", "train-episode", "2019-01-01", 0, "train"),),
    )
    event = EntryOpportunity("event", "000001", "issuer", sessions[40], True, SHA)
    return event, sessions, admissions, model


def invoke(event=None, *, admissions=None, model=None):
    e, s, a, m = fixture()
    return diagnose_entry(event or e, sessions=s, admissions=admissions or a,
                          checkpoint=model or m, rule=load_rule())


def test_signal_bar_is_excluded_and_next_open_is_only_calendar_coordinate():
    e, sessions, admissions, _ = fixture()
    result = invoke()
    assert pre_entry_window(sessions, e.signal_date) == sessions[:40]
    assert result.anchor == admissions[-1].row.market_date == sessions[39]
    assert result.earliest_calendar_execution == sessions[41]
    assert result.consumer_admission == "NOT_GRANTED"
    assert result.raw_score == 0.5  # uncalibrated diagnostic threshold, not trade probability
    assert result.c2_allowed
    assert set(asdict(result)).isdisjoint({"order", "sell", "fill"})


def test_wrong_anchor_or_security_cannot_be_relabelled_as_pre_entry():
    _, _, admissions, _ = fixture()
    with pytest.raises(ValueError, match="T_MINUS_ONE"):
        invoke(admissions=admissions[1:] + (admit_daily(row(40), policy()),))
    with pytest.raises(ValueError, match="T_MINUS_ONE"):
        invoke(admissions=(replace(admissions[0], row=replace(admissions[0].row,
                                                             security_code="600000")),)
               + admissions[1:])


@pytest.mark.parametrize("anchor", ["2020-02-10", "2020-02-11", "2021-08-31"])
def test_same_or_later_model_observation_rejects_before_scoring(anchor):
    *_, m = fixture()
    m = replace(m, uses=(replace(m.uses[0], anchor=anchor),))
    with pytest.raises(ValueError, match="MODEL_TIME_ORDER"):
        invoke(model=m)


def test_missing_required_input_blocks_new_candidate_entry_and_never_sells():
    _, _, admissions, _ = fixture()
    broken = (replace(admissions[0], admitted=False, reason="missing_raw_bar"),) + admissions[1:]
    result = invoke(admissions=broken)
    assert result.c0_allowed and not result.c1_allowed and not result.c2_allowed
    assert result.rule_positive is None and result.raw_score is None
    summary = summarize([result])
    assert summary["opportunities"] == summary["unknown_inputs"] == 1
    assert summary["known_c1_c2_differences"] == 0


def test_baseline_guardian_cannot_be_reenabled_by_positive_model():
    event, *_ = fixture()
    result = invoke(replace(event, baseline_allowed=False))
    assert result.raw_score == 0.5
    assert not result.c0_allowed and not result.c1_allowed and not result.c2_allowed


def test_action_epoch_inside_window_remains_unavailable():
    _, _, admissions, _ = fixture()
    changed = admissions[:20] + tuple(
        replace(a, row=replace(a.row, coordinate_epoch="new-action-epoch"))
        for a in admissions[20:]
    )
    result = invoke(admissions=changed)
    assert result.reason == "coordinate_change_within_fixed40"
    assert result.raw_score is None and not result.c2_allowed


def delta(event_id, issuer, day):
    result = invoke()
    return replace(result, opportunity=replace(result.opportunity, event_id=event_id,
                   issuer_id=issuer, signal_date=date.fromisoformat(day)),
                   rule_positive=False, c1_allowed=False, c2_allowed=True)


def test_concentrated_difference_is_futile_even_with_many_rows():
    values = [delta(str(i), "one-issuer", f"2021-09-{i+1:02}") for i in range(5)]
    assert summarize(values)["decision"] == "STOP_CONCENTRATED_INCREMENTAL_DECISION"
    values = [delta(str(i), str(i), "2021-09-01") for i in range(5)]
    assert summarize(values)["decision"] == "STOP_CONCENTRATED_INCREMENTAL_DECISION"


def test_dispersed_difference_only_requests_qualification_and_preserves_full_denominator():
    values = [delta("a", "A", "2021-09-01"), delta("b", "B", "2021-09-02")]
    summary = summarize(values)
    assert summary["decision"] == "PROPOSE_INDEPENDENT_QUALIFICATION_ONLY"
    assert not summary["economic_value_evaluated"]
    assert summary["consumer_admission"] == "NOT_GRANTED"
    with pytest.raises(ValueError, match="DUPLICATE_OPPORTUNITY"):
        summarize(values + [values[0]])


def test_zero_delta_and_empty_population_are_explicit_stops():
    assert summarize([])["decision"] == "STOP_NO_QUALIFIED_OPPORTUNITIES"
    r = invoke()
    r = replace(r, rule_positive=True, c1_allowed=True)
    assert summarize([r])["decision"] == "STOP_NO_KNOWN_INCREMENTAL_DECISION"


def test_calendar_cannot_omit_or_duplicate_a_session():
    e, sessions, *_ = fixture()
    with pytest.raises(ValueError, match="CALENDAR_IDENTITY"):
        pre_entry_window(sessions + (sessions[-1],), e.signal_date)
    with pytest.raises(ValueError, match="REQUIRED40"):
        pre_entry_window(sessions[1:], e.signal_date)
