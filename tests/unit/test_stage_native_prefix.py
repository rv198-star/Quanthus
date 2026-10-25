from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from quanthus.stage_learning.native_prefix import (
    SHANGHAI,
    VENUES,
    NativeAction,
    NativePrefixScope,
    ordinary_control_rows,
    project_native_prefix,
)
from quanthus.structure_order_facts import build_structure_order_facts
from quanthus.structure_v2_inputs import InputPolicy, digest

SHA = "sha256:" + "a" * 64


def fixture(n=120):
    first = date(2021, 1, 1)
    scope = NativePrefixScope("600001", "security-1", "listing-1", "SSE", first,
                              first + timedelta(days=n - 1), SHA, SHA, digest([]))
    policy = InputPolicy(SHA, ("600001",), SHA)
    listing = {"listing_id": "listing-1", "security_id": "security-1",
               "listing_code": "600001", "venue_id": VENUES["SSE"],
               "listing_currency": "CNY", "effective_from": datetime.combine(
                   first, time.min, SHANGHAI), "effective_to": None}
    cal, bars, states = [], [], []
    for i in range(n):
        day = first + timedelta(days=i)
        cal.append({"calendar_session_id": f"cal-{i}", "venue_id": VENUES["SSE"],
                    "trade_date": day, "is_open": True, "session_type": "REGULAR_DAY"})
        bar = {"listing_id": "listing-1", "calendar_session_id": f"cal-{i}",
               "observation_start": datetime.combine(day, time(15), SHANGHAI),
               "interval_size": "1d", "price_basis": "UNADJUSTED", "currency": "CNY",
               "high": Decimal("11.123456789"), "low": Decimal("9.001"),
               "close": Decimal("10.000"), "volume": Decimal("100"),
               "source_record_id": f"bar-{i}", "source_revision": "native-r1",
               "source_artifact_sha256": SHA,
               "knowledge_from": datetime(2026, 10, 20, tzinfo=SHANGHAI)}
        bars.append(bar)
        states.append({k: bar[k] for k in (
            "listing_id", "calendar_session_id", "observation_start",
        )} | {"is_tradable": True})  # Deliberately no interval_size field.
    return scope, policy, dict(listings=[listing], calendar=cal, bars=bars, states=states,
                               gaps=[], exceptions=[], actions=[])


def test_raw_decimals_and_observed_clocks_survive_without_as_known_claim():
    scope, policy, tables = fixture()
    admissions = project_native_prefix(scope, policy, **tables)
    assert all(a.admitted for a in admissions)
    assert admissions[0].row.high is tables["bars"][0]["high"]
    assert admissions[0].row.close.as_tuple().exponent == -3
    assert admissions[0].row.dwd_known_from.startswith("2026-10-20")
    controls = ordinary_control_rows(admissions)
    assert len(controls) == 120 and all(c["control_state"] == "ACTIVE" for c in controls)
    assert not any(c["historical_public_as_known_claim"] for c in controls)


def test_full_action_identity_resets_coordinate_and_sixty_session_window():
    scope, policy, tables = fixture()
    ex = scope.first_listing + timedelta(days=60)
    tables["actions"] = [NativeAction("cash-1", ex, "CASH_DIVIDEND", "official-r1", SHA)]
    scope = replace(scope, qualified_actions_digest=digest([
        a.identity() for a in tables["actions"]
    ]))
    admitted = project_native_prefix(scope, policy, **tables)
    assert admitted[59].row.coordinate_ref != admitted[60].row.coordinate_ref
    assert admitted[60].row.reset_reason == "corporate_action_coordinate_change"
    assert admitted[61].row.coordinate_ref == admitted[60].row.coordinate_ref
    facts = build_structure_order_facts(admitted, report_start=scope.first_listing,
                                        report_end=scope.through)
    available = {x.market_date for x in facts.availability if x.available}
    assert available == {scope.first_listing + timedelta(days=59), scope.through}
    # Lookback availability does not determine or remove ordinary ACTIVE members.
    assert sum(c["control_state"] == "ACTIVE" for c in ordinary_control_rows(admitted)) == 120
    changed = dict(tables, actions=[replace(tables["actions"][0], source_revision="official-r2")])
    with pytest.raises(ValueError, match="ACTION_INVENTORY"):
        project_native_prefix(scope, policy, **changed)
    changed_scope = replace(scope, qualified_actions_digest=digest([
        a.identity() for a in changed["actions"]
    ]))
    second = project_native_prefix(changed_scope, policy, **changed)
    assert admitted[59].row.coordinate_ref == second[59].row.coordinate_ref
    assert admitted[60].row.coordinate_ref != second[60].row.coordinate_ref


@pytest.mark.parametrize("kind", ["bar", "state"])
@pytest.mark.parametrize("wrong", ["date", "venue"])
def test_calendar_closure_checks_date_and_venue_even_for_state_without_interval(kind, wrong):
    scope, policy, tables = fixture(3)
    rows = tables["bars" if kind == "bar" else "states"]
    if wrong == "date":
        rows[0]["calendar_session_id"] = "cal-1"
    else:
        tables["calendar"][0]["venue_id"] = VENUES["SZSE"]
    with pytest.raises(ValueError, match="CALENDAR"):
        project_native_prefix(scope, policy, **tables)


def test_missing_and_nontrading_attempts_remain_visible_and_break_warmup():
    scope, policy, tables = fixture()
    tables["bars"].pop(1)
    tables["states"][2]["is_tradable"] = False
    admissions = project_native_prefix(scope, policy, **tables)
    controls = ordinary_control_rows(admissions)
    assert len(controls) == 120 and sum(c["control_state"] == "X" for c in controls) == 2
    assert admissions[1].reason == "missing_daily_bar"
    assert admissions[2].reason == "nontrading_state"
    facts = build_structure_order_facts(admissions, report_start=scope.first_listing,
                                        report_end=scope.through)
    assert next(x.market_date for x in facts.availability if x.available) == (
        scope.first_listing + timedelta(days=62)
    )


@pytest.mark.parametrize("kind", ["bar", "state"])
def test_same_day_closed_other_session_cannot_supply_regular_day_observation(kind):
    scope, policy, tables = fixture(2)
    tables["calendar"].append(dict(tables["calendar"][0], calendar_session_id="other",
                                  is_open=False, session_type="OTHER"))
    tables["bars" if kind == "bar" else "states"][0]["calendar_session_id"] = "other"
    with pytest.raises(ValueError, match="CALENDAR_CLOSURE"):
        project_native_prefix(scope, policy, **tables)


def test_entire_qualified_action_cannot_be_dropped_from_adapter_input():
    scope, policy, tables = fixture(2)
    action = NativeAction("cash-1", scope.first_listing, "CASH_DIVIDEND", "official-r1", SHA)
    scope = replace(scope, qualified_actions_digest=digest([action.identity()]))
    with pytest.raises(ValueError, match="ACTION_INVENTORY"):
        project_native_prefix(scope, policy, **tables)


def test_exact_exception_key_match_does_not_match_substring_and_unknown_json_fails():
    scope, policy, tables = fixture(2)
    exception = {"affected_business_key_json": '{"listing_id":"listing-10"}',
                 "resolution_state": "OPEN", "affected_effective_at":
                     datetime.combine(scope.first_listing, time(15), SHANGHAI)}
    tables["exceptions"] = [exception]
    assert project_native_prefix(scope, policy, **tables)[0].admitted
    exception["affected_business_key_json"] = '{"listing_id":"listing-1"}'
    assert project_native_prefix(scope, policy, **tables)[0].reason == (
        "unresolved_quality_exception"
    )
    exception["affected_business_key_json"] = "not-json"
    with pytest.raises(ValueError):
        project_native_prefix(scope, policy, **tables)


@pytest.mark.parametrize("wrong", ["protected", "duplicate", "basis", "action"])
def test_unqualified_scope_or_conflicting_input_fails_closed(wrong):
    scope, policy, tables = fixture(2)
    if wrong == "protected":
        scope = replace(scope, through=date(2022, 1, 1))
    elif wrong == "duplicate":
        tables["bars"].append(dict(tables["bars"][0]))
    elif wrong == "basis":
        tables["bars"][0]["price_basis"] = "QFQ"
    else:
        tables["actions"] = [NativeAction("x", scope.first_listing, "CASH", "r1", "unknown")]
    with pytest.raises(ValueError, match="NATIVE_PREFIX"):
        project_native_prefix(scope, policy, **tables)
