"""paper_replay: bounded historical replay over the real H1/M2/M1 chain.

Synthetic deterministic fixtures only -- this test never reads the physical
Base, never touches #737's content, and never asserts strategy performance.
Bound sessions are a named set of venue open days, not a weekday proxy; the
envelope slices each security's rows to the last <=61 bound sessions <=T.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.paper_replay import (
    INPUT_GRADE,
    _facts_for_day,
    build_replay_envelope,
    load_replay_request,
    preview_replay_plan,
    replay_decision_at,
    run_replay_day,
    run_replay_request,
)
from tests.unit.test_p11_daily_runner import (
    CARD,
    _identity,
)
from tests.unit.test_p11_daily_runner import (
    resources as resources,  # noqa: F401
)
from tests.unit.test_p11_daily_runner import (
    runner as runner,  # noqa: F401
)

ROOT = find_project_root()
SHANGHAI = ZoneInfo("Asia/Shanghai")

# Bound open sessions: every weekday 2026-04-01..2026-10-30 (the synthetic
# venue calendar).  A request outside this bound set is rejected, never a
# fabricated Monday.  >=120 sessions precede the first October signal day so
# the chain's listing-age floor is provable from the bound calendar alone.
SESSION_DAYS = tuple(
    d
    for d in (
        date(2026, 4, 1) + timedelta(days=i)
        for i in range((date(2026, 10, 30) - date(2026, 4, 1)).days + 1)
    )
    if d.weekday() < 5
)
assert len([d for d in SESSION_DAYS if d < date(2026, 10, 8)]) >= 120
OCT_SIGNAL_DAYS = tuple(d for d in SESSION_DAYS if date(2026, 10, 8) <= d <= date(2026, 10, 29))
assert len(OCT_SIGNAL_DAYS) == 16
T1 = date(2026, 10, 29)
T2 = date(2026, 10, 30)  # last bound session: final display day (no next open)


def _oct_bars(code: str, days: tuple[date, ...]) -> list[dict]:
    """Deterministic synthetic daily bars; a real quiet->active ramp on T1."""
    bars: list[dict] = []
    closes: list[Decimal] = []
    close = Decimal("100")
    n = len(days)
    for i, day in enumerate(days):
        if i < n - 2:
            close += Decimal("1.0") if i % 2 == 0 else Decimal("-0.4")
        else:
            close = closes[-1]
        closes.append(close)
        if i == n - 2:
            high, low, amount = close * Decimal("1.004"), close * Decimal("0.996"), Decimal("50")
        elif i == n - 1:
            high, low, amount = close * Decimal("1.05"), close * Decimal("0.95"), Decimal("400")
        else:
            high, low, amount = close * Decimal("1.01"), close * Decimal("0.99"), Decimal("100")
        bars.append(
            {
                "security_id": f"security-{code}",
                "session_date": day.isoformat(),
                "open_price": str(close),
                "high_price": str(high),
                "low_price": str(low),
                "close_price": str(close),
                "volume": "100000",
                "amount": str(amount),
                "is_tradable": True,
            }
        )
    return bars


def _security(code: str, days: tuple[date, ...], *, held: bool = False) -> dict:
    ident = _identity(code)
    bars = _oct_bars(code, days)
    close_by_day = {b["session_date"]: Decimal(b["close_price"]) for b in bars}
    limits = [
        {
            "session_date": d.isoformat(),
            "entry_policy_allows": True,
            "is_executable": True,
            "upper_limit_pct": "10",
            "lower_limit_pct": "10",
            "upper_limit_price": str(close_by_day[d.isoformat()] * Decimal("1.1")),
            "lower_limit_price": str(close_by_day[d.isoformat()] * Decimal("0.9")),
        }
        for d in days
        if d.isoformat() in close_by_day
    ]
    sec = {
        "identity": {
            "security_id": ident.security_id,
            "stock_code": ident.stock_code,
            "listing_code": ident.listing_code,
            "security_name": ident.security_name,
            "industry_id": ident.industry_id,
            "venue_id": ident.venue_id,
            "instrument_type": ident.instrument_type,
            "listing_id": ident.listing_id,
            "company_id": ident.company_id,
            "depends_on_member_ids": ["synthetic-daily-1d"],
            "invalidations": list(ident.invalidations),
            "price_bound": str(ident.price_bound),
            "source_evidence_digest": ident.source_evidence_digest,
        },
        "bars": bars,
        "actions": [],
        "exceptions": [],
        "limits": limits,
        "close_at_by_date": {
            d.isoformat(): datetime.combine(d, time(15), SHANGHAI).isoformat() for d in days
        },
        "knowledge_cutoff_by_date": {
            d.isoformat(): datetime.combine(d, time(18, 1), SHANGHAI).isoformat()
            for d in days
        },
    }
    if held:
        sec["held_membership"] = True
        sec["held_quantity"] = 1000
        # The request is a JSON-bound contract: held membership is declared by
        # the markers, and the scenario account's typed position_facts is
        # injected at run time by the account projection, not serialized here.
    return sec


def _calendar(days: tuple[date, ...], venue: str = "SSE") -> list[dict]:
    return [
        {
            "venue_id": venue,
            "session_date": d.isoformat(),
            "is_open": True,
            # H1 R4 anchors each open session's observation instant on
            # dwd_trading_calendar.open_at; the replay calendar supplies it.
            "open_at": datetime.combine(d, time(9, 30), SHANGHAI).isoformat(),
            "close_at": datetime.combine(d, time(15), SHANGHAI).isoformat(),
            "knowledge_from": datetime.combine(d, time(15, 30), SHANGHAI).isoformat(),
            "source_member_id": "synthetic-daily-1d",
        }
        for d in days
    ]


def _listing(security_id: str, venue: str = "SSE") -> dict:
    return {
        "security_id": security_id,
        "listing_id": security_id.replace("security-", "listing-"),
        "venue_id": venue,
        # The synthetic listing starts with the bound calendar so the chain's
        # full-membership proof is satisfiable; a real older listing would use
        # the named source_daily_universe passthrough, never a truncated count.
        "effective_from": SESSION_DAYS[0].isoformat(),
        "effective_to": None,
        "knowledge_from": "2026-01-01T00:00:00+08:00",
            "source_member_id": "synthetic-daily-1d",
    }


def _coverage(mode, securities, market_dates):
    """Build an action_coverage block for the requested admission mode.

    verified = each security gets verified_none window_evidence records bound
    to its identity + each signal date, each carrying the verbatim venue-
    calendar session set (scan = [T-1,T], returns = the 61 sessions ending at
    T), a hashed register, a member_map digest and an upstream credential.
    unknown  = default unknown, no window_evidence (fail-closed on entry).
    empty    = default 'covered' caller label, no evidence -- must be refused.
    partial  = only the FIRST security is evidenced; the rest stay unknown.
    """
    base = {
        "default_state": "unknown",
        "registers": [],
        "securities": [],
        "window_evidence": [],
        "note": "synthetic coverage fixture",
    }
    if mode == "empty":
        base["default_state"] = "covered"
        return base
    if mode == "unknown":
        return base
    reg = {
        "coverage_id": "register-synthetic-sse-2026",
        "version": "reg.v1",
        "sha256": "sha256:" + "5" * 64,
        "venue_id": "SSE",
        "event_types": ["cash_dividend", "bonus_share", "transfer"],
        "source_member_id": "synthetic-daily-1d",
    }
    base["registers"] = [reg]
    target = securities if mode == "verified" else securities[:1]
    opens = list(SESSION_DAYS)
    for sec in target:
        ident = sec["identity"]
        code = ident["listing_code"]
        base["securities"].append(
            {
                "security_code": code,
                "security_id": ident["security_id"],
                "listing_id": ident["listing_id"],
                "venue_id": ident["venue_id"],
            }
        )
        for t in market_dates:
            if t not in opens:
                # Non-open signal days are rejected by the request validator
                # before coverage is consulted; skip building records for them.
                continue
            idx = opens.index(t)
            scan_sessions = [
                {"date": opens[idx - 1].isoformat(), "adjustment_effective": False},
                {"date": t.isoformat(), "adjustment_effective": False},
            ]
            returns_sessions = [
                d.isoformat() for d in opens[idx - 60 : idx + 1]
            ]
            cred = {
                "batch_id": "p10-synthetic-b02",
                "manifest_sha256": "sha256:" + "7" * 64,
                "result_sha256": "sha256:" + "8" * 64,
                "reader": "p10.register-reader.v1",
            }
            mmap = "sha256:" + "6" * 64
            base["window_evidence"].append(
                {
                    "security_code": code,
                    "signal_date": t.isoformat(),
                    "purpose": "scan_two_session",
                    "event_state": "verified_none",
                    "coverage_id": reg["coverage_id"],
                    "member_sha256": mmap,
                    "member_map_sha256": mmap,
                    "credential": cred,
                    "scan_sessions": scan_sessions,
                    "sessions": [s["date"] for s in scan_sessions],
                    "events": [],
                    "source_published_at": "2026-07-10",
                    "verified_at": "2026-11-24T00:00:00+08:00",
                }
            )
            base["window_evidence"].append(
                {
                    "security_code": code,
                    "signal_date": t.isoformat(),
                    "purpose": "returns_61_session",
                    "event_state": "verified_none",
                    "coverage_id": reg["coverage_id"],
                    "member_sha256": mmap,
                    "member_map_sha256": mmap,
                    "credential": cred,
                    "sessions": returns_sessions,
                    "events": [],
                    "source_published_at": "2026-07-10",
                    "verified_at": "2026-11-24T00:00:00+08:00",
                }
            )
    return base


def _request(
    *,
    market_dates=(T1, T2),
    securities=None,
    exclusions=(),
    open_sessions=SESSION_DAYS,
    account_id="replay-paper-202610",
    member_available_at=None,
    coverage="verified",
    mutate=None,
) -> dict:
    if securities is None:
        securities = [_security("600000", SESSION_DAYS)]
    avail = member_available_at or datetime(2026, 1, 1, 18, 1, tzinfo=SHANGHAI)
    req = {
        "schema_version": "p11.paper-replay-request.v1",
        "purpose_mode": "historical_replay",
        "permitted_account_use": "personal_simulation",
        "scenario_id": "paper-replay-202610",
        "package_id": "standard-base-2.5.1-8cd966be87fc",
        "manifest_sha256": (
            "sha256:8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef"
        ),
        "market_dates": [d.isoformat() for d in market_dates],
        "open_sessions": [d.isoformat() for d in open_sessions],
        "signal_window": {"from": "2026-10-08", "through": "2026-10-29"},
        "final_day_policy": {
            "market_date": T2.isoformat(),
            "next_session": None,
            "disposition": "final_display_only",
        },
        "action_coverage": _coverage(coverage, securities, market_dates),
        "member_evidence": [
            {
                "member_id": "synthetic-daily-1d",
                "version": "synthetic.v1",
                "digest": "sha256:" + "1" * 64,
                "available_at": avail.isoformat(),
                "required": True,
            }
        ],
        "required_member_ids": ["synthetic-daily-1d"],
        "source_members": [
            {
                "member_id": "synthetic-daily-1d",
                "package_id": "standard-base-2.5.1-8cd966be87fc",
                "version": "synthetic.v1",
                "member_path": "synthetic/daily.parquet",
                "sha256": "sha256:" + "1" * 64,
                "date_from": "2026-01-01",
                "date_to": "2026-10-30",
                "knowledge_at": avail.isoformat(),
                "visible_at": avail.isoformat(),
            }
        ],
        "calendar_rows": _calendar(SESSION_DAYS),
        "listing_rows": [_listing("security-600000")],
        "securities": securities,
        "exclusions": list(exclusions),
        "account": {
            "account_id": account_id,
            "as_of": datetime(2026, 10, 30, 21, 30, tzinfo=SHANGHAI).isoformat(),
            # Per-signal-day scenario-ledger snapshots: the T view, never a
            # later day's read.
            "as_of_overrides": {
                T1.isoformat(): datetime(2026, 10, 29, 21, 30, tzinfo=SHANGHAI).isoformat(),
            },
            "cash_available": "500000",
            "active_or_reserved_security_codes": [],
            "active_industry_counts": [],
            "new_buckets_today": 0,
            "pending_commitment_total": "0",
        },
        "policy": {
            "policy_id": "p11-d1b-paper-replay",
            "version": "1.0.0",
            "published_at": datetime(2026, 10, 29, 21, 30, tzinfo=SHANGHAI).isoformat(),
            "window_ref": "paper-replay-202610",
            "candidate_rule_digest": CARD.semantic_digest,
            "trusted_producer_ids": ["p11.fixed-rule-day"],
        },
        "facts_template": {
            "batch_id": "replay-202610",
            "source_id": "REPLAY-standard-base-2.5.1",
            "source_version": "synthetic.v1",
            "calendar_version": "replay-cal-v1",
            "price_basis": "none",
            "intake_receipt_id": "replay-intake-202610",
            "intake_received_at": "2026-10-29T18:01:00+08:00",
            "intake_first_read_at": "2026-10-29T18:30:00+08:00",
            "intake_verified_at": "2026-10-29T19:00:00+08:00",
        },
        "run_id": "paper-replay-202610-r1",
    }
    if mutate:
        mutate(req)
    return req


def _oct_split(signal_day=T2):
    from quanthus.forward_validation.prospective_source import P6ProspectiveSplitPolicy

    return P6ProspectiveSplitPolicy(
        activation_market_date=date(2026, 1, 1), signal_market_date=signal_day
    )


def _chain_kwargs():
    from quanthus.product_daily.recommendation.history_input import (
        run_daily_advice_from_history,
    )

    return {
        "run_daily_advice_from_history": run_daily_advice_from_history,
        "snapshot_id": "snap-replay-1",
        "snapshot_manifest_digest": sha256_digest({"snap": "replay"}),
    }


def _run(request, market_date, runner, resources, **over):
    kw = dict(
        runner=runner,
        resources=resources,
        card=CARD,
        split_policy=_oct_split(market_date),
        clock=lambda: replay_decision_at(market_date),
        observed_clock=lambda: datetime(2026, 11, 25, 1, 0, tzinfo=SHANGHAI),
        **_chain_kwargs(),
    )
    kw.update(over)
    return run_replay_day(request, market_date, **kw)


# --- request validation ------------------------------------------------------------


def test_request_roundtrip():
    request = load_replay_request(_request())
    assert request.scenario_id == "paper-replay-202610"
    assert request.ledger_namespace == "historical-replay:paper-replay-202610"
    assert request.request_digest.startswith("sha256:")
    assert request.market_dates == (T1, T2)
    assert request.signal_window == (date(2026, 10, 8), date(2026, 10, 29))


def test_native_utc_intake_clocks_keep_source_precision():
    raw = _request()
    keys = ("intake_received_at", "intake_first_read_at", "intake_verified_at")
    for key in keys:
        raw["facts_template"][key] = "2026-11-24T22:31:16.606519Z"
    facts = _facts_for_day(load_replay_request(raw), T1)
    assert all(
        getattr(facts, key).isoformat() == "2026-11-24T22:31:16.606519+00:00" for key in keys
    )


def test_request_rejects_bad_schema():
    with pytest.raises(ProductDailyError):
        load_replay_request({"schema_version": "other"})


def test_request_rejects_non_historical_purpose():
    with pytest.raises(ProductDailyError):
        load_replay_request(_request(mutate=lambda r: r.update({"purpose_mode": "daily_live"})))


def test_request_rejects_closed_market_date():
    closed = date(2026, 10, 31)  # Saturday, not in open_sessions
    with pytest.raises(ProductDailyError):
        load_replay_request(_request(market_dates=(closed,)))


def test_request_rejects_out_of_window_signal_day():
    # 2026-09-30 is a bound open session but outside the declared October window.
    with pytest.raises(ProductDailyError):
        load_replay_request(_request(market_dates=(date(2026, 9, 30),)))


def test_request_rejects_missing_required_member():
    with pytest.raises(ProductDailyError):
        load_replay_request(_request(mutate=lambda r: r["source_members"].pop()))


def test_request_rejects_final_day_inside_window():
    req = _request(
        market_dates=(T1, T2),
        mutate=lambda r: r["final_day_policy"].update({"market_date": "2026-10-29"}),
    )
    with pytest.raises(ProductDailyError):
        load_replay_request(req)


# --- envelope projection -------------------------------------------------------------


def test_envelope_bounds_bars_to_le_t_and_61_window():
    request = load_replay_request(_request(market_dates=(T1,)))
    envelope, excluded, window_info, cov = build_replay_envelope(request, T1)
    assert envelope["schema_version"] == "p11.raw-history-input.v1"
    assert envelope["decision_cutoff"] == replay_decision_at(T1).isoformat()
    sec = envelope["securities"][0]
    # Window is the bound sessions, not the delivered row count.
    assert window_info["bar_context_sessions"] == 61
    assert window_info["window_last"] == T1.isoformat()
    assert len(sec["bars"]) == 61
    assert max(b["session_date"] for b in sec["bars"]) <= T1.isoformat()
    assert min(b["session_date"] for b in sec["bars"]) == window_info["window_first"]
    assert all(d <= T1.isoformat() for d in sec["knowledge_cutoff_by_date"])
    # Source member times are verbatim, never rewritten to look "as of T".
    assert envelope["source_members"][0]["visible_at"] == "2026-01-01T18:01:00+08:00"
    assert excluded == () and cov == ()
    assert window_info["delivered"]["600000"]["bar_rows"] == 61
    assert window_info["delivered"]["600000"]["gap_sessions"] == 0


def test_envelope_keeps_missing_bar_as_gap():
    days = tuple(d for d in SESSION_DAYS if d != date(2026, 10, 28))
    request = load_replay_request(
        _request(market_dates=(T1,), securities=[_security("600000", days)])
    )
    envelope, _, window_info, _ = build_replay_envelope(request, T1)
    sec = envelope["securities"][0]
    assert len(sec["bars"]) == 60
    assert window_info["delivered"]["600000"]["gap_sessions"] == 1
    assert date(2026, 10, 28).isoformat() not in {b["session_date"] for b in sec["bars"]}


def test_envelope_applies_exclusions():
    req = _request(
        securities=[
            _security("600000", SESSION_DAYS),
            _security("600001", SESSION_DAYS),
        ],
        exclusions=[{"security_code": "600001", "session_date": T1.isoformat()}],
        mutate=lambda r: r["listing_rows"].append(_listing("security-600001")),
    )
    request = load_replay_request(req)
    env_t1, excluded_t1, _, _ = build_replay_envelope(request, T1)
    assert excluded_t1 == ("600001",)
    assert {s["identity"]["listing_code"] for s in env_t1["securities"]} == {"600000"}
    env_t2, excluded_t2, _, _ = build_replay_envelope(request, T2)
    assert excluded_t2 == ()
    assert {s["identity"]["listing_code"] for s in env_t2["securities"]} == {
        "600000",
        "600001",
    }


def test_envelope_rejects_empty_universe():
    req = _request(exclusions=[{"security_code": "600000"}])
    request = load_replay_request(req)
    with pytest.raises(ProductDailyError):
        build_replay_envelope(request, T1)


# --- corporate-action coverage gate ------------------------------------------


def test_coverage_unknown_isolates_new_entry():
    request = load_replay_request(_request(market_dates=(T1,), coverage="unknown"))
    envelope, _, window_info, cov = build_replay_envelope(request, T1)
    sec = envelope["securities"][0]
    assert sec["bars"] == []
    assert cov[0]["reason"] == "replay_action_coverage_unknown"
    assert window_info["delivered"]["600000"]["entry_eligible"] is False
    assert window_info["coverage_isolated"] == 1


def test_coverage_empty_credential_refused_at_load():
    # The R2 probe's core defect: default_state='covered' with an empty
    # evidence table admitted entry.  It must now fail closed at the loader.
    with pytest.raises(ProductDailyError):
        load_replay_request(_request(market_dates=(T1,), coverage="empty"))


def test_coverage_unverified_state_isolated():
    # A window_evidence record that is only 'unresolved' never admits entry.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        ev["event_state"] = "unresolved"
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert cov and cov[0]["reason"] == "replay_action_coverage_unknown"
    assert window_info["coverage_isolated"] == 1


def test_coverage_unregistered_coverage_id_refused():
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        ev["coverage_id"] = "register-not-declared"
    with pytest.raises(ProductDailyError):
        load_replay_request(req)


def test_coverage_wrong_listing_identity_isolated():
    # A record binding a different listing_id must not admit this security.
    req = _request(market_dates=(T1,), coverage="verified")
    for s in req["action_coverage"]["securities"]:
        s["listing_id"] = "listing-someone-else"
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_coverage_partial_only_evidenced_security_enters():
    sec2 = _security("600001", SESSION_DAYS)
    req = _request(
        market_dates=(T1,),
        securities=[_security("600000", SESSION_DAYS), sec2],
        coverage="partial",
        mutate=lambda r: r["listing_rows"].append(_listing("security-600001")),
    )
    request = load_replay_request(req)
    envelope, _, window_info, cov = build_replay_envelope(request, T1)
    codes = {s["identity"]["listing_code"] for s in envelope["securities"]}
    assert codes == {"600000", "600001"}
    barred = {e["security_code"] for e in cov}
    assert barred == {"600001"}
    assert window_info["coverage_isolated"] == 1


def test_coverage_unknown_keeps_held_member():
    request = load_replay_request(
        _request(
            market_dates=(T1,),
            coverage="unknown",
            securities=[_security("600000", SESSION_DAYS, held=True)],
        )
    )
    envelope, _, window_info, cov = build_replay_envelope(request, T1)
    sec = envelope["securities"][0]
    # A held security keeps its bars for the held expert; coverage isolation
    # only gates NEW entry, never the held decision's own evidence path.
    assert sec["held_membership"] is True
    assert len(sec["bars"]) == 61
    assert cov == ()


def test_coverage_scan_only_missing_returns_isolates():
    # Root's NORMAL-ENTRY case: verified scan alone with zero returns evidence
    # must NOT admit entry - both branches are required for a new position.
    req = _request(market_dates=(T1,), coverage="verified")
    req["action_coverage"]["window_evidence"] = [
        ev
        for ev in req["action_coverage"]["window_evidence"]
        if ev["purpose"] != "returns_61_session"
    ]
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1
    assert cov[0]["reason"] == "replay_action_coverage_unknown"


def test_coverage_missing_upstream_credential_isolates():
    # A verified state with no bound upstream read is a caller summary, not
    # proof; entry stays isolated.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        ev["credential"] = None
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_coverage_wrong_identity_on_returns_isolates():
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session":
            ev["security_code"] = "600999"
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


# --- R4: verbatim venue-calendar session binding + full event terms ------------


def test_r4_single_date_scan_and_returns_isolated():
    # Root R3 probe: both purposes given only the single T still produced an
    # entry.  Scan must be exactly [T-1,T] and returns the 61-session set.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        ev["sessions"] = [T1.isoformat()]
        ev["scan_sessions"] = [{"date": T1.isoformat(), "adjustment_effective": False}]
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1
    assert cov[0]["reason"] == "replay_action_coverage_unknown"


def test_r4_scan_effective_action_blocks_new_entry():
    # A verified scan hit on [T-1,T] with an adjustment_effective event must
    # block the new entry outright.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "scan_two_session":
            ev["event_state"] = "verified_event"
            ev["scan_sessions"][1]["adjustment_effective"] = True
            ev["events"] = [{"adjustment_effective": True, "ex_date": T1.isoformat()}]
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r4_returns_event_missing_raw_terms_isolated():
    # verified_event on returns without the full original terms (per-share /
    # cash / register_row / implementation_sha256) is not proof the series is
    # clean -- the security stays out.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session":
            ev["event_state"] = "verified_event"
            ev["events"] = [{"ex_date": "2026-07-28"}]  # terms missing
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r4_returns_event_full_terms_admitted():
    # The named Nanjing-Gang-style case: a verified_event with complete raw
    # terms bound to the member map admits the new entry.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session":
            ev["event_state"] = "verified_event"
            ev["events"] = [
                {
                    "ex_date": "2026-07-28",
                    "notice": "1225435299",
                    "implementation_sha256": "a" * 64,
                    "currency": "CNY",
                    "cash_gross_per_share": "0.116",
                    "bonus_share_ratio": "0",
                    "transfer_share_ratio": "0",
                    "register_row": 302,
                }
            ]
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 0


def test_r4_no_security_identity_isolated():
    # R3 probe admitted a record bound to NO security identity.  A bare code
    # without security_id/listing_id/venue_id is not admitted.
    req = _request(market_dates=(T1,), coverage="verified")
    req["action_coverage"]["securities"] = []
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r4_missing_member_map_isolated():
    # A verified state without member_map_sha256 cannot reach named raw rows.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        ev["member_map_sha256"] = None
    with pytest.raises(ProductDailyError):
        load_replay_request(req)


def test_r4_skipped_open_session_isolated():
    # returns sessions missing one real open day must not pass the verbatim
    # 61-session check.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "returns_61_session":
            ev["sessions"] = ev["sessions"][:-2] + ev["sessions"][-1:]  # drop one
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


def test_r4_wrong_session_day_isolated():
    # A scan session list that lands on a non-open day fails verbatim check.
    req = _request(market_dates=(T1,), coverage="verified")
    for ev in req["action_coverage"]["window_evidence"]:
        if ev["purpose"] == "scan_two_session":
            ev["scan_sessions"] = [
                {"date": "2026-10-26", "adjustment_effective": False},  # Sunday
                {"date": T1.isoformat(), "adjustment_effective": False},
            ]
            ev["sessions"] = [s["date"] for s in ev["scan_sessions"]]
    request = load_replay_request(req)
    _, _, window_info, cov = build_replay_envelope(request, T1)
    assert window_info["coverage_isolated"] == 1


# --- real-chain replay ---------------------------------------------------------------


def test_replay_day_runs_real_chain_positive(runner, resources):
    """Synthetic bars -> real H1 -> real scan/expert -> a real entry on T1.

    The ramp's volume event lands on the signal day itself (bars end at T1),
    so the real chain produces a publishable package, not a blocked day.
    """
    days_upto_T1 = tuple(d for d in SESSION_DAYS if d <= T1)
    request = load_replay_request(
        _request(market_dates=(T1,), securities=[_security("600000", days_upto_T1)])
    )
    receipt = _run(request, T1, runner, resources)
    assert receipt["schema_version"] == "p11.paper-replay-day.v1"
    assert receipt["input_grade"] == INPUT_GRADE
    assert receipt["ledger_namespace"] == "historical-replay:paper-replay-202610"
    assert receipt["replay_observed_at"] == "2026-11-25T01:00:00+08:00"
    assert receipt["logical_decision_at"] == "2026-10-29T21:30:00+08:00"
    assert receipt["next_session"] == T2.isoformat()
    assert receipt["daily_live_use"] is False
    assert receipt["real_account_use"] is False
    assert receipt["automatic_trading_use"] is False
    # The P12 projection carries the real RuleDayOutput fields.
    for key in (
        "disposition",
        "publishable",
        "package",
        "diagnostics",
        "seam",
        "receipt",
        "source_closure",
        "stage_metrics",
        "held_signals",
        "candidates",
        "entry_decisions",
    ):
        assert key in receipt, key
    # A real publishable package and a real entry decision on 10-29 -> 10-30.
    assert receipt["publishable"] is True
    assert receipt["package_present"] is True
    assert any(d["status"] == "entry" for d in receipt["entry_decisions"])
    assert "replay_no_action" not in receipt["package"]["decision_sets"][0]["audit"]


def test_qualified_quiet_package_explains_each_scanned_security(runner, resources):
    days = tuple(d for d in SESSION_DAYS if d <= T1)
    securities = [_security(code, days) for code in ("600000", "600001")]
    for security in securities:
        bar = security["bars"][-1]
        bar.update(high_price=bar["close_price"], low_price=bar["close_price"], amount="100")
    raw = _request(market_dates=(T1,), securities=securities)
    raw["listing_rows"].append(_listing("security-600001"))
    day = _run(load_replay_request(raw), T1, runner, resources)
    assert day["publishable"] and not day["candidates"] and not day["held_signals"]
    ds = day["package"]["decision_sets"][0]
    assert ds["audit"]["admission_disposition"] == "empty_advice"
    summary = ds["audit"]["replay_no_action"]
    assert summary["market_date"] == T1.isoformat()
    assert summary["source_closure_digest"] == sha256_digest(day["source_closure"])
    assert summary["candidate_count"] == 0
    assert summary["message"] == ds["decisions"][0]["explanations"][0]["text"] == "今日无操作"
    assert {s["security_code"] for s in summary["scans"]} == {"600000", "600001"}
    for scan in summary["scans"]:
        assert scan["verdict"] == "not_triggered"
        assert scan["gates"]["events"]["required"] == 22
        assert scan["gates"]["features"]["observed"] == 61
        assert scan["gates"]["listing"]["status"] == "satisfied"


def test_replay_day_blocks_on_late_source_visibility(runner, resources):
    """A member whose declared visible_at is after T 21:30 fails closed, honestly."""
    late = datetime(2026, 10, 29, 23, 0, tzinfo=SHANGHAI)
    request = load_replay_request(
        _request(market_dates=(T1,), member_available_at=late)
    )
    receipt = _run(request, T1, runner, resources)
    assert receipt["disposition"] == "blocked"
    assert receipt["input_grade"] == INPUT_GRADE


def test_replay_request_run_collects_days(runner, resources):
    out = run_replay_request(
        _request(),
        runner=runner,
        resources=resources,
        card=CARD,
        split_policy=_oct_split(),
        observed_clock=lambda: datetime(2026, 11, 25, 1, 0, tzinfo=SHANGHAI),
        **_chain_kwargs(),
    )
    assert out["schema_version"] == "p11.paper-replay-run.v1"
    assert len(out["days"]) == 2
    assert {d["market_date"] for d in out["days"]} == {
        T1.isoformat(),
        T2.isoformat(),
    }
    assert all(d["input_grade"] == INPUT_GRADE for d in out["days"])


# --- final-day sealed-scope policy -----------------------------------------------------


def test_final_display_day_t_only(runner, resources):
    """The sealed calendar's last day replays T-only; no T+1 quote is fabricated."""
    request = load_replay_request(_request())
    receipt = _run(request, T2, runner, resources)
    assert receipt["disposition"] == "final_display_only"
    assert receipt["next_session"] is None
    assert receipt["package_present"] is False
    assert receipt["input_grade"] == INPUT_GRADE


def test_final_day_without_policy_refuses():
    req = _request(mutate=lambda r: r.pop("final_day_policy"))
    with pytest.raises(ProductDailyError):
        load_replay_request(req)


# --- P12 metadata preview ---------------------------------------------------------


def test_preview_replay_plan_is_metadata_only():
    plan = preview_replay_plan(_request())
    assert plan["schema_version"] == "p11.paper-replay-plan.v1"
    assert plan["request_digest"].startswith("sha256:")
    kinds = {d["market_date"]: d["kind"] for d in plan["days"]}
    assert kinds[T1.isoformat()] == "signal_day"
    assert kinds[T2.isoformat()] == "final_display_only"
    day29 = next(d for d in plan["days"] if d["market_date"] == T1.isoformat())
    assert day29["bar_context_sessions"] == 61
    assert day29["next_session"] == T2.isoformat()
    assert "300308" not in {c for d in plan["days"] for c in d.get("excluded_security_codes", ())}
