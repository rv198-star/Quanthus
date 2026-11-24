# --- R5 action-join: verified event terms must reach the feature row ---
from __future__ import annotations

from quanthus.product_daily.recommendation.paper_replay import (
    build_replay_envelope,
    load_replay_request,
)
from tests.unit.test_p11_paper_replay import (
    SESSION_DAYS,
    T1,
    _request,
)


def _returns_window(market_date=T1):
    opens = list(SESSION_DAYS)
    idx = opens.index(market_date)
    return [d.isoformat() for d in opens[idx - 60 : idx + 1]]


def _r5_event(ex_date, cash="0.116", bonus="0", transfer="0", currency="CNY"):
    return {
        "ex_date": ex_date,
        "notice": "r5",
        "implementation_sha256": "a" * 64,
        "currency": currency,
        "cash_gross_per_share": cash,
        "bonus_share_ratio": bonus,
        "transfer_share_ratio": transfer,
        "register_row": 302,
    }


def _r5_action(code, effective_date, cash="0.116", bonus="0", known_at="2026-09-01"):
    return {
        "security_code": code,
        "effective_date": effective_date,
        "cash_dividend_per_share": cash,
        "bonus_share_ratio": bonus,
        "known_at": known_at,
    }


def _r5_req(events, actions, market_date=T1):
    req = _request(market_dates=(market_date,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session":
            ev["event_state"] = "verified_event"
            ev["events"] = events
    if actions:
        req["securities"][0]["actions"] = actions
    return req


def test_r5_verified_event_matching_action_admitted():
    win = _returns_window()
    ex_day = win[30]
    req = _r5_req(
        [_r5_event(ex_day, cash="0.116", bonus="0", transfer="0")],
        [_r5_action("600000", ex_day, cash="0.116", bonus="0")],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0
    assert window_info["delivered"]["600000"]["entry_eligible"] is True


def test_r5_verified_event_missing_action_isolated():
    win = _returns_window()
    ex_day = win[30]
    req = _r5_req([_r5_event(ex_day, cash="0.116")], [])
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r5_amount_mismatch_isolated():
    win = _returns_window()
    ex_day = win[30]
    req = _r5_req(
        [_r5_event(ex_day, cash="0.116")],
        [_r5_action("600000", ex_day, cash="0.0116")],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r5_bonus_transfer_combined_once():
    win = _returns_window()
    ex_day = win[30]
    req = _r5_req(
        [_r5_event(ex_day, cash="0", bonus="0.10", transfer="0.05")],
        [_r5_action("600000", ex_day, cash="0", bonus="0.15")],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0
    req2 = _r5_req(
        [_r5_event(ex_day, cash="0", bonus="0.10", transfer="0.05")],
        [_r5_action("600000", ex_day, cash="0", bonus="0.10")],
    )
    request2 = load_replay_request(req2)
    _, _, window_info2, _ = build_replay_envelope(request2, T1)
    assert window_info2["coverage_isolated"] == 1


def test_r5_first_close_ex_date_not_counted():
    win = _returns_window()
    first_close = win[0]
    req = _r5_req([_r5_event(first_close, cash="0.116")], [])
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0


def test_r5_verified_none_with_action_conflict_isolated():
    win = _returns_window()
    ex_day = win[30]
    req = _request(market_dates=(T1,), coverage="verified")
    req["securities"][0]["actions"] = [_r5_action("600000", ex_day)]
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r5_orphan_action_isolated():
    win = _returns_window()
    ex_day = win[30]
    other_day = win[35]
    req = _r5_req(
        [_r5_event(ex_day, cash="0.116")],
        [_r5_action("600000", ex_day, cash="0.116"), _r5_action("600000", other_day)],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r5_non_cny_currency_isolated():
    win = _returns_window()
    ex_day = win[30]
    req = _r5_req(
        [_r5_event(ex_day, cash="0.116", currency="HKD")],
        [_r5_action("600000", ex_day, cash="0.116")],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r5_known_at_not_before_effective_isolated():
    win = _returns_window()
    ex_day = win[30]
    req = _r5_req(
        [_r5_event(ex_day, cash="0.116")],
        [_r5_action("600000", ex_day, cash="0.116", known_at=ex_day)],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r5_scan_verified_event_no_resolvable_row_isolated():
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "scan_two_session":
            ev["event_state"] = "verified_event"
            ev["events"] = [{"unavailable_subtype": "unclassified"}]
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r5_scan_verified_event_resolvable_row_admitted():
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "scan_two_session":
            ev["event_state"] = "verified_event"
            ev["events"] = [{"ex_date": "2026-07-28"}]
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0


# --- R6: scan ex-date effectiveness + same-date action one-to-one -------------------


def test_r6_scan_ex_date_on_T_blocks_entry():
    # The R6 probe: scan verified_event with ex_date == T (the signal day) is
    # an effective adjustment on a scanned session -> blocks new entry even
    # though the row spells it as a date, not an adjustment_effective bool.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "scan_two_session":
            ev["event_state"] = "verified_event"
            ev["events"] = [{"ex_date": T1.isoformat()}]
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r6_scan_ex_date_on_T_minus_1_blocks_entry():
    # Same gate on T-1: an ex-date landing on the prior scanned session is an
    # effective adjustment -> blocked.
    opens = list(SESSION_DAYS)
    tm1 = opens[opens.index(T1) - 1]
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "scan_two_session":
            ev["event_state"] = "verified_event"
            ev["events"] = [{"ex_date": tm1.isoformat()}]
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r6_same_date_identical_actions_dedupe_to_one():
    # Byte-identical re-statements of the same action row collapse to one by
    # version -> the single proven row feeds the panel, the entry proceeds.
    win = _returns_window()
    ex_day = win[30]
    row = _r5_action("600000", ex_day, cash="0.116")
    req = _r5_req([_r5_event(ex_day, cash="0.116")], [row, dict(row)])
    request = load_replay_request(req)
    env, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0
    # One deduped row reaches the feature envelope.
    assert len(env["securities"][0]["actions"]) == 1


def test_r6_same_date_different_actions_isolated():
    # A proven 0.116 plus an unproven 0.232 on the same ex_date -> two DISTINCT
    # same-date rows -> multiplicity conflict -> isolated, not passed through.
    win = _returns_window()
    ex_day = win[30]
    good = _r5_action("600000", ex_day, cash="0.116")
    extra = _r5_action("600000", ex_day, cash="0.232")
    req = _r5_req([_r5_event(ex_day, cash="0.116")], [good, extra])
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r6_two_declared_events_same_date_conflict_isolated():
    # Two DECLARED events on the same ex_date with different terms are a
    # declaration conflict -> isolated (the register cannot pick one).
    win = _returns_window()
    ex_day = win[30]
    req = _r5_req(
        [_r5_event(ex_day, cash="0.116"), _r5_event(ex_day, cash="0.232")],
        [_r5_action("600000", ex_day, cash="0.116")],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1
