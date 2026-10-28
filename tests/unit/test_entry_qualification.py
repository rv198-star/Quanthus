from dataclasses import replace
from datetime import date, timedelta

import pytest

from quanthus.stage_learning.entry_consolidation import EntryOpportunity
from quanthus.stage_learning.entry_qualification import (
    ListingScope,
    qualification_summary,
    qualify_population,
)


def fixture():
    # A synthetic actual-session list: qualification never manufactures weekdays.
    calendar = [date(2021, 7, 1) + timedelta(days=i) for i in range(100)]
    event = EntryOpportunity("event", "code", "issuer", calendar[70], True, "sha256:" + "1" * 64)
    args = {"model_last_observation": calendar[60], "identities": {"code": "issuer"},
            "diagnostic_blocked_aliases": set(), "all_exposed_aliases": set(),
            "consumed": (), "sessions": {"venue": calendar},
            "listings": {"code": ListingScope("venue", calendar[0], None)}}
    return event, args


def test_metadata_eligible_never_grants_reference_capital_or_admission():
    event, args = fixture()
    rows = qualify_population([event], **args)
    summary = qualification_summary(rows)
    assert summary["metadata_eligible_events"] == 1
    assert summary["independent_reference"] == "NOT_CHECKED"
    assert summary["whole_capital_comparison"] == "NOT_RUN_QUALIFICATION_REQUIRED"
    assert summary["consumer_admission"] == "NOT_GRANTED"


def test_used_event_cannot_be_renamed_fresh_and_empty_does_not_pass_vacuously():
    event, args = fixture()
    args["consumed"] = (event,)
    rows = qualify_population([event], **args)
    assert rows[0].confirmation_disposition == "consumed_development_event"
    assert qualification_summary(rows)["cohort_gate"] == "EMPTY_FIXED_OPPORTUNITY_POPULATION"


def test_different_event_on_used_issuer_is_still_not_independent():
    event, args = fixture()
    later = replace(event, event_id="later", signal_date=args["sessions"]["venue"][75])
    args["consumed"] = (event,)
    rows = qualify_population([event, later], **args)
    assert rows[1].confirmation_disposition == "consumed_development_issuer"


def test_different_security_code_with_same_company_cannot_escape_used_issuer():
    event, args = fixture()
    alias = replace(event, event_id="alias_event", security_code="alias")
    args["identities"]["alias"] = "issuer"
    args["listings"]["alias"] = args["listings"]["code"]
    args["consumed"] = (event,)
    rows = qualify_population([event, alias], **args)
    assert rows[1].confirmation_disposition == "consumed_development_issuer"


def test_older_development_allowed_in_diagnostic_is_excluded_from_confirmation():
    event, args = fixture()
    args["all_exposed_aliases"] = {"code"}
    row, = qualify_population([event], **args)
    assert row.diagnostic_disposition == "INCLUDE"
    assert row.confirmation_disposition == "prior_development_or_evaluation_issuer_alias"


@pytest.mark.parametrize("offset,reason", [
    (60, "signal_not_after_training"), (61, "anchor_not_after_training"),
])
def test_training_time_must_precede_t_minus_one_anchor(offset, reason):
    event, args = fixture()
    event = replace(event, signal_date=args["sessions"]["venue"][offset])
    row, = qualify_population([event], **args)
    assert row.confirmation_disposition == reason


@pytest.mark.parametrize("mutation", ["unknown_event", "duplicate", "digest", "issuer", "B_action"])
def test_changed_consumed_identity_fails_instead_of_increasing_capacity(mutation):
    event, args = fixture()
    used = event
    if mutation == "unknown_event":
        used = replace(event, event_id="unknown")
    elif mutation == "digest":
        used = replace(event, baseline_decision_digest="sha256:" + "2" * 64)
    elif mutation == "issuer":
        used = replace(event, issuer_id="other")
    elif mutation == "B_action":
        used = replace(event, baseline_allowed=False)
    args["consumed"] = (used, used) if mutation == "duplicate" else (used,)
    with pytest.raises(ValueError, match="CONSUMED_IDENTITY"):
        qualify_population([event], **args)


def test_terminal_entry_uses_existing_dec31_session_without_opening2022():
    event, args = fixture()
    calendar = [date(2021, 10, 1) + timedelta(days=i) for i in range(92)]
    args["sessions"] = {"venue": calendar}
    args["model_last_observation"] = date(2021, 8, 31)
    event = replace(event, signal_date=date(2021, 12, 30))
    row, = qualify_population([event], **args)
    assert row.anchor == date(2021, 12, 29)
    assert row.next_open_session == date(2021, 12, 31)
    assert row.confirmation_disposition == "METADATA_ELIGIBLE_NOT_YET_QUALIFIED"
    args["sessions"]["venue"] = calendar[:-1]
    row, = qualify_population([event], **args)
    assert row.confirmation_disposition == "next_open_calendar_unavailable"


def test_absent_issuer_is_unqualified_and_cannot_form_a_new_cluster():
    event, args = fixture()
    args["identities"] = {}
    row, = qualify_population([event], **args)
    assert row.confirmation_disposition == "missing_or_ambiguous_issuer"


@pytest.mark.parametrize("mutation,error", [
    ("protected", "PROTECTED_SCOPE"), ("duplicate_event", "POPULATION"),
    ("duplicate_coordinate", "EVENT_COORDINATE"), ("calendar", "CALENDAR"),
    ("venue", "VENUE"),
])
def test_invalid_population_calendar_or_scope_fails(mutation, error):
    event, args = fixture()
    events = [event]
    if mutation == "protected":
        events = [replace(event, signal_date=date(2022, 1, 1))]
    elif mutation == "duplicate_event":
        events.append(event)
    elif mutation == "duplicate_coordinate":
        events.append(replace(event, event_id="other"))
    elif mutation == "calendar":
        args["sessions"]["venue"].reverse()
    else:
        args["sessions"] = {"wrong_venue": args["sessions"]["venue"]}
    with pytest.raises(ValueError, match=error):
        qualify_population(events, **args)


def test_consumed_selection_with_changed_listing_scope_fails():
    event, args = fixture()
    args["consumed"] = (event,)
    args["listings"]["code"] = replace(args["listings"]["code"], last_session=date(2021, 7, 3))
    with pytest.raises(ValueError, match="PRIOR_SCOPE_CHANGED"):
        qualify_population([event], **args)
