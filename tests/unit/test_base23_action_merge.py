from datetime import datetime, timedelta, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_action_merge import (
    classify_actions,
    term_closures,
)

T = datetime(2026, 7, 10, tzinfo=timezone.utc)


def row(**changes):
    return {"corporate_action_id": "a", "security_id": "s", "effective_at": T,
            "action_type": "cash", "knowledge_from": T, "knowledge_to": None,
            "record_source": "retained", "path": "old.parquet", "ordinal": 0,
            **changes}


def test_new_observation_closes_only_old_clock_and_keeps_unknown_acquisition():
    old = row()
    result = classify_actions([old], [row(knowledge_from=T+timedelta(days=3))])
    assert len(result["accepted"]) == len(result["closures"]) == 1
    assert not result["held"]
    assert old == row()
    assert result["provider_acquisition_inferred"] is False


def test_different_ids_at_same_coordinate_hold_without_value_arbitration():
    result = classify_actions([row()], [row(corporate_action_id="b", action_type="mixed")])
    assert not result["accepted"] and not result["closures"]
    assert result["held"][0]["reason"] == (
        "DIFFERENT_ACTION_ID_SAME_ECONOMIC_COORDINATE_NO_PRIORITY")


def test_incoming_economic_duplicates_all_held():
    result = classify_actions([], [row(), row(corporate_action_id="b")])
    assert not result["accepted"] and len(result["held"]) == 2


@pytest.mark.parametrize("changes,reason", [
    ({"record_source": "other"}, "SAME_ACTION_ID_DIFFERENT_PROVIDER_NO_PRIORITY"),
    ({"knowledge_from": T+timedelta(days=4)}, "INCOMING_KNOWLEDGE_NOT_STRICTLY_NEW"),
    ({"knowledge_to": T+timedelta(days=5)},
     "IMMUTABLE_CLOSED_HISTORY_OVERLAPS_NEW_OBSERVATION"),
])
def test_unqualified_replacement_is_scoped_hold(changes, reason):
    result = classify_actions([row(**changes)], [row(knowledge_from=T+timedelta(days=3))])
    assert result["held"][0]["reason"] == reason
    assert not result["accepted"] and not result["closures"]


def test_missing_event_can_be_added_with_real_new_clock():
    result = classify_actions([row()], [row(security_id="another", corporate_action_id="b")])
    assert len(result["accepted"]) == 1 and not result["closures"]


def test_closed_prior_version_is_preserved():
    result = classify_actions([row(knowledge_to=T+timedelta(days=1))],
                              [row(knowledge_from=T+timedelta(days=3))])
    assert len(result["accepted"]) == 1 and not result["closures"]


def test_duplicate_incoming_versions_fail_closed():
    with pytest.raises(BoundedError, match="MULTIPLE_INCOMING"):
        classify_actions([], [row(), row()])


def test_all_old_term_roles_close_even_if_new_plan_removed_role():
    admission = classify_actions([row()], [row(knowledge_from=T+timedelta(days=3))])
    terms = [row(ordinal=0), row(ordinal=1)]
    result = term_closures(terms, admission)
    assert [r["ordinal"] for r in result] == [0, 1]
    assert terms == [row(ordinal=0), row(ordinal=1)]


def test_held_action_terms_are_unchanged():
    admission = classify_actions([row()], [row(corporate_action_id="b")])
    assert term_closures([row()], admission) == []


def test_conflicting_term_provider_fails_closed():
    admission = classify_actions([], [row(knowledge_from=T+timedelta(days=3))])
    with pytest.raises(BoundedError, match="TERM_SOURCE_OR_INTERVAL"):
        term_closures([row(record_source="other")], admission)
