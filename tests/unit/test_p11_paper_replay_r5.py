# --- R5 action-join: verified event terms must reach the feature row ---
from __future__ import annotations

from datetime import date, timedelta

from quanthus.product_daily.recommendation.paper_replay import (
    build_replay_envelope,
    load_replay_request,
)
from tests.unit.test_p11_paper_replay import (
    SESSION_DAYS,
    T1,
    _listing,
    _request,
    _security,
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


def _bind_artifacts(req, authority=None):
    # For every returns_61_session verified_event, register the event rows as
    # the verified-artifact catalogue of the bound coverage register: the
    # P10-delivered list of (register_row -> full original clause) the upstream
    # credential actually verified.  The catalogue carries a digest + producer
    # + authority/receipt identity (B02/B03/B10 lineage) so a caller-assembled
    # minimal or self-copied list is not an authority.
    arts = []
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] != "returns_61_session" or ev["event_state"] != "verified_event":
            continue
        cid = ev["coverage_id"]
        rows = []
        for e in ev.get("events") or []:
            rows.append(
                {
                    "register_row": e.get("register_row"),
                    "ex_date": e.get("ex_date"),
                    "implementation_sha256": e.get("implementation_sha256"),
                    "notice": e.get("notice"),
                    "notice_id": e.get("notice_id") or e.get("notice"),
                    "cash_gross_per_share": e.get("cash_gross_per_share"),
                    "bonus_share_ratio": e.get("bonus_share_ratio"),
                    "transfer_share_ratio": e.get("transfer_share_ratio"),
                    "currency": e.get("currency"),
                    "security_id": e.get("security_id"),
                    "listing_id": e.get("listing_id"),
                    "venue_id": e.get("venue_id"),
                }
            )
        arts.append(
            {
                "coverage_id": cid,
                "digest": "sha256:" + "9" * 64,
                "producer": "p10.b02-b03-b10-register.v1",
                "authority": authority
                or {
                    "batch_id": "p10-b02-b03-b10",
                    "manifest_sha256": "sha256:" + "b" * 64,
                    "result_sha256": "sha256:" + "c" * 64,
                },
                "rows": rows,
            }
        )
    if arts:
        req["action_coverage"]["verified_artifacts"] = arts
    return req


def _r5_req(events, actions, market_date=T1):
    req = _request(market_dates=(market_date,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session":
            ev["event_state"] = "verified_event"
            ev["events"] = events
    if actions:
        req["securities"][0]["actions"] = actions
    return _bind_artifacts(req)


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


def test_r5_scan_verified_event_unrelated_date_isolated():
    # R7/C6: a scan verified_event whose rows only carry an unrelated ex_date
    # (not one of the two scanned sessions) is evidence bound to nothing -
    # named isolation, never a free clean scan.  This replaces the old test
    # that expected admission and masked the defect.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "scan_two_session":
            ev["event_state"] = "verified_event"
            ev["events"] = [{"ex_date": "2026-07-28"}]
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


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


# --- R7: code-shape identity, provenance binding, decimal-normal dedupe ----------


def _r7_sse_security(days, stock="sh.600000"):
    sec = _security("600000", days)
    sec["identity"]["stock_code"] = stock
    return sec


def _r7_req(sec, events, actions, market_date=T1, artifacts=None):
    req = _request(market_dates=(market_date,), coverage="verified", securities=[sec])
    code = sec["identity"]["listing_code"]
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session" and ev["security_code"] == code:
            ev["event_state"] = "verified_event"
            ev["events"] = events
    if actions:
        sec["actions"] = actions
    if artifacts is not None:
        req["action_coverage"]["verified_artifacts"] = artifacts
    elif events:
        # Bind the declared events to the register's verified-artifact
        # catalogue so a tampered provenance fails (unless a test overrides
        # with its own catalogue via artifacts=).
        _bind_artifacts(req)
    return req


def test_r7_sse_stock_code_positive_admitted():
    # Real SPDB shape: listing_code=600000, stock_code=sh.600000, action row
    # keyed by stock_code.  Per original-share 0.42 counts once.
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _r7_sse_security(days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _r7_req(
        sec,
        [_r5_event(ex, cash="0.42")],
        [_r5_action("sh.600000", ex, cash="0.42", known_at=known)],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0
    assert window_info["delivered"]["600000"]["entry_eligible"] is True


def test_r7_sse_verified_none_but_action_isolated():
    # Reverse: declared window proves no event yet a same-security action row
    # keyed by stock_code exists -> conflict isolation (R6 reverse join now
    # binds the stock_code key).
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _r7_sse_security(days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _r7_req(sec, [], [_r5_action("sh.600000", ex, cash="0.42", known_at=known)])
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r7_action_keyed_to_wrong_venue_isolated():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _r7_sse_security(days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _r7_req(
        sec,
        [_r5_event(ex, cash="0.42")],
        [_r5_action("sz.600000", ex, cash="0.42", known_at=known)],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r7_trailing_zero_amount_dedupes_to_one():
    # 0.420 vs 0.42: identical terms under different decimal renderings
    # collapse to a single panel row, not a multiplicity conflict.
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _r7_sse_security(days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _r7_req(
        sec,
        [_r5_event(ex, cash="0.42")],
        [
            _r5_action("sh.600000", ex, cash="0.42", known_at=known),
            _r5_action("sh.600000", ex, cash="0.420", known_at=known),
        ],
    )
    request = load_replay_request(req)
    env, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0
    rows = next(s for s in env["securities"] if s["identity"]["listing_code"] == "600000")
    assert len(rows["actions"]) == 1


def test_r7_same_date_distinct_version_conflict_isolated():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _r7_sse_security(days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _r7_req(
        sec,
        [_r5_event(ex, cash="0.42")],
        [
            _r5_action("sh.600000", ex, cash="0.42", known_at=known),
            _r5_action("sh.600000", ex, cash="0.52", known_at=known),
        ],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r7_event_identity_mismatch_isolated():
    # The projected action row names a different security_id -> per-security
    # isolation, not a silent same-code match.
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _r7_sse_security(days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    bad = _r5_action("sh.600000", ex, cash="0.42", known_at=known)
    bad["security_id"] = "security-other"
    req = _r7_req(sec, [_r5_event(ex, cash="0.42")], [bad])
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def _r7_artifacts(coverage_id, register_row, event=None, **row):
    # Default the catalogue row's original clause to the declared event's own
    # terms (the upstream credential verified this same clause); explicit
    # kwargs override for mismatch cases.
    e = event or {}
    base = {
        "register_row": register_row,
        "ex_date": row.pop("ex_date", e.get("ex_date", "2026-07-28")),
        "cash_gross_per_share": row.pop(
            "cash_gross_per_share", e.get("cash_gross_per_share", "0.116")
        ),
        "bonus_share_ratio": row.pop("bonus_share_ratio", e.get("bonus_share_ratio", "0")),
        "transfer_share_ratio": row.pop("transfer_share_ratio", e.get("transfer_share_ratio", "0")),
        "currency": row.pop("currency", e.get("currency", "CNY")),
        "implementation_sha256": row.pop(
            "implementation_sha256", e.get("implementation_sha256", "a" * 64)
        ),
        "notice": row.pop("notice", e.get("notice", "r5")),
    }
    base.update(row)
    return [
        {
            "coverage_id": coverage_id,
            "digest": "sha256:" + "9" * 64,
            "producer": "p10.b02-b03-b10-register.v1",
            "authority": {
                "batch_id": "p10-b02-b03-b10",
                "manifest_sha256": "sha256:" + "b" * 64,
                "result_sha256": "sha256:" + "c" * 64,
            },
            "rows": [base],
        }
    ]


def test_r7_bound_artifact_admits_matching_event():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    ev = _r5_event(ex, cash="0.42")
    req = _r7_req(sec, [ev], [_r5_action("600000", ex, cash="0.42", known_at=known)])
    cid = req["action_coverage"]["registers"][0]["coverage_id"]
    req["action_coverage"]["verified_artifacts"] = _r7_artifacts(
        cid, 302, event=ev, implementation_sha256="a" * 64, notice="r5"
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0


def test_r7_bound_artifact_forged_implementation_isolated():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    ev = _r5_event(ex, cash="0.42")
    ev["implementation_sha256"] = "f" * 64
    req = _r7_req(sec, [ev], [_r5_action("600000", ex, cash="0.42", known_at=known)])
    cid = req["action_coverage"]["registers"][0]["coverage_id"]
    req["action_coverage"]["verified_artifacts"] = _r7_artifacts(
        cid, 302, event=_r5_event(ex, cash="0.42"), implementation_sha256="a" * 64, notice="r5"
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r7_bound_artifact_wrong_register_row_isolated():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    ev = _r5_event(ex, cash="0.42")
    ev["register_row"] = 999
    req = _r7_req(sec, [ev], [_r5_action("600000", ex, cash="0.42", known_at=known)])
    cid = req["action_coverage"]["registers"][0]["coverage_id"]
    req["action_coverage"]["verified_artifacts"] = _r7_artifacts(
        cid, 302, event=_r5_event(ex, cash="0.42"), implementation_sha256="a" * 64, notice="r5"
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r7_malformed_event_date_isolated():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    ev = _r5_event("not-a-date", cash="0.42")
    req = _r7_req(sec, [ev], [_r5_action("600000", ex, cash="0.42", known_at=known)])
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r7_neighbour_retained_when_one_isolated():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    good = _security("600001", days)
    bad = _r7_sse_security(days)
    bad["identity"]["security_id"] = "security-600000"
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _request(market_dates=(T1,), coverage="verified", securities=[good, bad])
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session" and ev["security_code"] == "600000":
            ev["event_state"] = "verified_event"
            ev["events"] = [_r5_event(ex, cash="0.42")]
    bad["actions"] = [_r5_action("sz.600000", ex, cash="0.42", known_at=known)]
    req["listing_rows"].append(_listing("security-600001"))
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1
    assert window_info["delivered"]["600001"]["entry_eligible"] is True
    assert window_info["delivered"]["600000"]["entry_eligible"] is False


# --- R8: source-authority catalogue + full-term binding + designated key ------


def test_r8_minimal_or_self_copied_catalogue_refused():
    # C6-R7-P1-1: a catalogue that is only register_row, or self-copied from
    # the event, has no producer/digest/authority and no complete original
    # clause - it is caller-controlled, not an authority, so it must fail
    # closed even when the event looks complete.
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    ev = _r5_event(ex, cash="0.42")
    req = _r7_req(sec, [ev], [_r5_action("600000", ex, cash="0.42", known_at=known)])
    cid = req["action_coverage"]["registers"][0]["coverage_id"]
    # A minimal/self-copied catalogue: only register_row, no authority.
    req["action_coverage"]["verified_artifacts"] = [
        {"coverage_id": cid, "rows": [{"register_row": 302}]}
    ]
    import pytest

    from quanthus.product_daily.contracts import ProductDailyError

    with pytest.raises(ProductDailyError):
        load_replay_request(req)


def test_r8_catalogue_missing_authority_refused():
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    ev = _r5_event(ex, cash="0.42")
    req = _r7_req(sec, [ev], [_r5_action("600000", ex, cash="0.42", known_at=known)])
    cid = req["action_coverage"]["registers"][0]["coverage_id"]
    art = _r7_artifacts(cid, 302, event=ev)[0]
    art.pop("authority")
    req["action_coverage"]["verified_artifacts"] = [art]
    import pytest

    from quanthus.product_daily.contracts import ProductDailyError

    with pytest.raises(ProductDailyError):
        load_replay_request(req)


def test_r8_source_terms_mismatch_isolated():
    # C6-R7-P1-2: the catalogue row keeps the TRUE clause while event/action
    # carry a different economic term (real impl sha, wrong cash/ex_date).
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    ev = _r5_event(ex, cash="0.52")
    req = _r7_req(sec, [ev], [_r5_action("600000", ex, cash="0.52", known_at=known)])
    cid = req["action_coverage"]["registers"][0]["coverage_id"]
    # Catalogue keeps the true 0.42 clause on the true ex_date.
    true_ev = _r5_event(ex, cash="0.42")
    req["action_coverage"]["verified_artifacts"] = _r7_artifacts(cid, 302, event=true_ev)
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r8_bare_listing_code_action_rejected_when_codes_differ():
    # C6-R7-P2-1: stock_code=sh.600000, listing_code=600000; an action row
    # keyed by bare 600000 is a foreign source key and must NOT be reported
    # entry_eligible at the coverage gate.
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _r7_sse_security(days)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _r7_req(
        sec,
        [_r5_event(ex, cash="0.42")],
        [_r5_action("600000", ex, cash="0.42", known_at=known)],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    # The bare listing_code action is not this security's source row, so the
    # declared verified event has no matching action -> named isolation.
    assert window_info["coverage_isolated"] == 1
    assert window_info["delivered"]["600000"]["entry_eligible"] is False


def test_r8_listing_code_action_ok_when_codes_same():
    # When stock_code == listing_code the single value is the source key.
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    sec = _security("600000", days)  # stock_code == listing_code == "600000"
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=3)).isoformat()
    req = _r7_req(
        sec,
        [_r5_event(ex, cash="0.42")],
        [_r5_action("600000", ex, cash="0.42", known_at=known)],
    )
    request = load_replay_request(req)
    _, _, window_info, _ = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0
    assert window_info["delivered"]["600000"]["entry_eligible"] is True
