"""H1: p11.raw-history-input.v1 -> RawSecurityInput -> real chain -> P12.

The raw-history envelope carries original facts only (bars/calendar/listing); P11
derives the FULL universe membership from listing intervals + the venue calendar
(row_number semantics preserved -- never a 120-session truncation passed off as the
complete age), projects each qualified security, runs the real scan/panel/expert,
and binds the per-security history-gate receipts into source_closure.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.daily_runner import RawSecurityInput
from quanthus.product_daily.recommendation.history_input import (
    derive_universe_membership,
    load_raw_history,
    raw_security_inputs_from_history,
    run_daily_advice_from_history,
)
from tests.unit.test_p11_daily_runner import (
    CARD,
    TARGET,
    _producer_inputs,
    _prospective_source,
    _prospective_split,
    _trend_raw,
    _weekdays_ending,
)
from tests.unit.test_p11_daily_runner import (
    resources as resources,  # noqa: F401
)
from tests.unit.test_p11_daily_runner import (
    runner as runner,  # noqa: F401  (module-scoped fixture reuse)
)

ROOT = find_project_root()
SHANGHAI = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI)


# --- envelope builders ------------------------------------------------------------


def _member(member_id: str = "m-cal-1") -> dict:
    return {
        "member_id": member_id,
        "package_id": "pkg-cal",
        "version": "1.0.0",
        "member_path": "calendar/sse.parquet",
        "sha256": "sha256:" + "b" * 64,
        "date_from": "2026-01-01",
        "date_to": "2026-11-24",
        "knowledge_at": "2026-11-24T18:01:00+08:00",
        "visible_at": "2026-11-24T18:01:00+08:00",
    }


def _calendar(venue: str = "SSE", days=None, member_id: str = "m-cal-1") -> list:
    days = _weekdays_ending(TARGET, 260) if days is None else days
    return [
        {
            "venue_id": venue,
            "session_date": day.isoformat(),
            "is_open": True,
            "close_at": f"{day.isoformat()}T15:00:00+08:00",
            "knowledge_from": day.isoformat(),
            "source_member_id": member_id,
        }
        for day in days
    ]


def _listing(
    security_id: str,
    venue: str = "SSE",
    effective_from: str = "2026-01-01",
    member_id: str = "m-cal-1",
    listing_id: str | None = None,
) -> dict:
    return {
        "security_id": security_id,
        "listing_id": listing_id or f"listing-{security_id}",
        "venue_id": venue,
        "effective_from": effective_from,
        "effective_to": None,
        "knowledge_from": "2026-01-01",
        "source_member_id": member_id,
    }


def _security_entry(raw: RawSecurityInput, **extra) -> dict:
    """Serialize a RawSecurityInput into the envelope securities[] shape."""
    identity = raw.identity
    return {
        "identity": {
            "security_id": identity.security_id,
            "stock_code": identity.stock_code,
            "listing_code": identity.listing_code,
            "security_name": identity.security_name,
            "industry_id": identity.industry_id,
            "venue_id": identity.venue_id,
            "instrument_type": identity.instrument_type,
            "listing_id": identity.listing_id,
            "company_id": identity.company_id,
            "depends_on_member_ids": list(identity.depends_on_member_ids),
            "invalidations": list(identity.invalidations),
            "price_bound": str(identity.price_bound),
            "source_evidence_digest": identity.source_evidence_digest,
        },
        "bars": [
            {
                "security_id": b.security_id,
                "session_date": b.session_date.isoformat(),
                "open_price": str(b.open_price),
                "high_price": str(b.high_price),
                "low_price": str(b.low_price),
                "close_price": str(b.close_price),
                "volume": str(b.volume),
                "amount": str(b.amount),
                "is_tradable": b.is_tradable,
            }
            for b in raw.bars
        ],
        "actions": [],
        "exceptions": [],
        "limits": [
            {
                "session_date": lf.session_date.isoformat(),
                "entry_policy_allows": lf.entry_policy_allows,
                "is_executable": lf.is_executable,
                "upper_limit_pct": str(lf.upper_limit_pct),
                "lower_limit_pct": str(lf.lower_limit_pct),
                "upper_limit_price": str(lf.upper_limit_price),
                "lower_limit_price": str(lf.lower_limit_price),
            }
            for lf in raw.limits
        ],
        "close_at_by_date": {d.isoformat(): t.isoformat() for d, t in raw.close_at_by_date.items()},
        "knowledge_cutoff_by_date": {
            d.isoformat(): t.isoformat() for d, t in raw.knowledge_cutoff_by_date.items()
        },
        **extra,
    }


def _envelope(securities, calendar=None, listing=None, members=None, market_date=TARGET):
    return {
        "schema_version": "p11.raw-history-input.v1",
        "market_date": market_date.isoformat(),
        "decision_cutoff": CUTOFF.isoformat(),
        "source_members": [_member()] if members is None else members,
        "calendar_rows": _calendar() if calendar is None else calendar,
        "listing_rows": [] if listing is None else listing,
        "securities": securities,
    }


def _trend_envelope(
    code: str = "600000", venue: str = "SSE", n_cal: int = 260, listing_from: str = "2026-01-01"
) -> dict:
    """A trend-raw security wrapped in a raw-history envelope covering listing."""
    raw = _trend_raw(code)
    cal_days = _weekdays_ending(TARGET, n_cal)
    return _envelope(
        [_security_entry(raw)],
        calendar=_calendar(venue=venue, days=cal_days),
        listing=[_listing(raw.identity.security_id, venue=venue, effective_from=listing_from)],
    )


# --- strict loader ----------------------------------------------------------------


def test_loader_accepts_valid_envelope():
    history = load_raw_history(_trend_envelope())
    assert history.market_date == TARGET
    assert len(history.securities) == 1
    assert history.calendar_rows[0].is_open


def test_loader_rejects_bad_schema():
    env = _trend_envelope()
    env["schema_version"] = "p11.raw-history-input.v9"
    with pytest.raises(ProductDailyError, match="invalid_raw_history"):
        load_raw_history(env)


def test_loader_rejects_duplicate_member():
    env = _trend_envelope()
    env["source_members"] = [_member("m-1"), _member("m-1")]
    with pytest.raises(ProductDailyError, match="duplicate_member"):
        load_raw_history(env)


def test_loader_rejects_unknown_member_reference():
    env = _trend_envelope()
    env["calendar_rows"] = _calendar(member_id="m-missing")
    with pytest.raises(ProductDailyError, match="unknown_source_member"):
        load_raw_history(env)


def test_loader_rejects_visibility_after_cutoff():
    member = _member()
    member["visible_at"] = "2026-11-24T23:00:00+08:00"  # after the 22:30 cutoff
    env = _trend_envelope()
    env["source_members"] = [member]
    with pytest.raises(ProductDailyError, match="source_visibility_after_cutoff"):
        load_raw_history(env)


def test_loader_rejects_bad_listing_interval():
    listing = _listing("security-600000", effective_from="2026-06-01")
    listing["effective_to"] = "2026-05-01"
    env = _trend_envelope()
    env["listing_rows"] = [listing]
    with pytest.raises(ProductDailyError, match="invalid_listing_interval"):
        load_raw_history(env)


# --- membership derivation: full sequence, never a truncated proxy -----------------


def test_membership_is_full_listing_age_not_bar_count():
    """200-member listing with 130 bars reports 200, not 130 or a capped window."""
    raw = _trend_raw("600000")  # 130 bars
    cal_days = _weekdays_ending(TARGET, 200)
    listing = [_listing(raw.identity.security_id, effective_from=cal_days[0].isoformat())]
    history = load_raw_history(
        _envelope([_security_entry(raw)], calendar=_calendar(days=cal_days), listing=listing)
    )
    inputs, receipts = raw_security_inputs_from_history(history)
    assert len(inputs[0].universe_membership) == 200
    receipt = receipts[0]
    assert receipt["membership_count"] == 200
    assert receipt["bar_count"] == 130
    assert receipt["gates"]["listing"]["observed"] == 200
    assert receipt["listing_floor_proof"]["kind"] == "AT_LEAST_120"
    assert receipt["listing_floor_proof"]["floor_proven"] is True


def test_membership_exact_when_listing_inside_window():
    """Listing starting inside the delivered window -> EXACT observed age."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    listing_start = cal_days[-119]  # exactly 119 sessions of membership
    listing = [_listing(raw.identity.security_id, effective_from=listing_start.isoformat())]
    history = load_raw_history(
        _envelope([_security_entry(raw)], calendar=_calendar(days=cal_days), listing=listing)
    )
    inputs, receipts = raw_security_inputs_from_history(history)
    assert len(inputs[0].universe_membership) == 119
    receipt = receipts[0]
    assert receipt["listing_floor_proof"]["kind"] == "EXACT"
    assert receipt["listing_floor_proof"]["observed"] == 119
    assert receipt["listing_floor_proof"]["floor_proven"] is False


def test_119_membership_below_listing_gate():
    """119 sessions is below the 120 floor; the receipt reports it, not a pass."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 119)
    listing = [_listing(raw.identity.security_id, effective_from=cal_days[0].isoformat())]
    history = load_raw_history(
        _envelope([_security_entry(raw)], calendar=_calendar(days=cal_days), listing=listing)
    )
    _, receipts = raw_security_inputs_from_history(history)
    assert receipts[0]["membership_count"] == 119
    assert receipts[0]["listing_floor_proof"]["floor_proven"] is False


def test_missing_listing_fact_excludes_security():
    """No listing interval -> the security is isolated; age is never inferred."""
    raw = _trend_raw("600000")
    history = load_raw_history(_envelope([_security_entry(raw)], listing=[]))
    inputs, receipts = raw_security_inputs_from_history(history)
    assert inputs == []
    assert receipts[0]["status"] == "excluded"
    assert "listing" in receipts[0]["reason"].lower()


def test_wrong_exchange_calendar_excluded():
    """Membership keys on venue_id; a different venue's calendar does not count."""
    raw = _trend_raw("600000")  # identity.venue_id = SSE
    cal_days = _weekdays_ending(TARGET, 200)
    history = load_raw_history(
        _envelope(
            [_security_entry(raw)],
            calendar=_calendar(venue="SZSE", days=cal_days),
            listing=[
                _listing(
                    raw.identity.security_id, venue="SSE", effective_from=cal_days[0].isoformat()
                )
            ],
        )
    )
    inputs, receipts = raw_security_inputs_from_history(history)
    # SSE venue has no open sessions on the delivered SZSE calendar -> empty membership.
    assert inputs[0].universe_membership == ()


def test_calendar_after_market_date_excluded():
    """Calendar sessions after T never inflate the listing age (no T+1 backflow)."""
    raw = _trend_raw("600000")
    cal_days = list(_weekdays_ending(TARGET, 200))
    cal_days.append(TARGET + timedelta(days=3))  # a future session
    listing = [_listing(raw.identity.security_id, effective_from=cal_days[0].isoformat())]
    history = load_raw_history(
        _envelope([_security_entry(raw)], calendar=_calendar(days=cal_days), listing=listing)
    )
    inputs, _ = raw_security_inputs_from_history(history)
    assert TARGET + timedelta(days=3) not in inputs[0].universe_membership
    assert inputs[0].universe_membership[-1] <= TARGET


def test_derive_membership_fail_closed_without_listing():
    with pytest.raises(ProductDailyError, match="listing_fact_missing"):
        derive_universe_membership(
            listing_rows=[],
            calendar_rows=(),
            security_id="security-1",
            venue_id="SSE",
            market_date=TARGET,
        )


# --- per-security isolation --------------------------------------------------------


def test_bad_security_isolated_valid_neighbor_kept():
    good = _trend_raw("600000")
    bad = _trend_raw("600001")
    cal_days = _weekdays_ending(TARGET, 200)
    listing = [
        _listing(good.identity.security_id, effective_from=cal_days[0].isoformat()),
        # bad security has no listing interval -> isolated
    ]
    history = load_raw_history(
        _envelope(
            [_security_entry(good), _security_entry(bad)],
            calendar=_calendar(days=cal_days),
            listing=listing,
        )
    )
    inputs, receipts = raw_security_inputs_from_history(history)
    assert len(inputs) == 1
    assert inputs[0].identity.listing_code == "600000"
    assert receipts[1]["status"] == "excluded"
    assert receipts[1]["security_code"] == "600001"


def test_all_fail_is_block_not_empty_pass():
    """Every security failing yields zero inputs + explicit reasons, never a pass."""
    a, b = _trend_raw("600000"), _trend_raw("600001")
    history = load_raw_history(_envelope([_security_entry(a), _security_entry(b)], listing=[]))
    inputs, receipts = raw_security_inputs_from_history(history)
    assert inputs == []
    assert all(r["status"] == "excluded" for r in receipts)


# --- end-to-end: envelope -> real scan/panel/expert -> pinned P12 -------------------


def _run_kwargs():
    facts, policy, account = _producer_inputs()
    return {
        "facts": facts,
        "policy": policy,
        "account": account,
        "card": CARD,
        "split_policy": _prospective_split(),
        "source_policy": _prospective_source(),
        "run_id": "run-h1-1",
        "snapshot_id": "snap-h1",
        "snapshot_manifest_digest": sha256_digest({"h": 1}),
        "clock": lambda: policy.decision_cutoff,
    }


def test_h1_raw_history_to_p12_real_entry(runner, resources):
    """61+ bars and >=120 membership -> a real entry through the full chain."""
    output = run_daily_advice_from_history(
        _trend_envelope(), runner=runner, resources=resources, **_run_kwargs()
    )
    assert output.result.package is not None
    entries = [d for d in output.seam.decisions if d.entry_allowed]
    assert entries, "a real TRIAL_ENTRY must be produced through the H1 envelope"
    plans = [
        d
        for ds in output.result.package.decision_sets
        for d in ds.decisions
        if d.stage == "entry_plan"
    ]
    assert plans, "the package must carry a real entry_plan"

    from tests.unit.test_product_daily_rule_seam import _p12_loader

    validate = _p12_loader()
    for ds in output.result.package.decision_sets:
        validated = validate(ds.to_dict())
    assert any("ENTRY_PLAN" in str(d.stage) for d in validated.decisions)


def test_h1_receipts_bound_into_source_closure(runner, resources):
    output = run_daily_advice_from_history(
        _trend_envelope(), runner=runner, resources=resources, **_run_kwargs()
    )
    receipts = output.source_closure["history_receipts"]
    assert receipts and receipts[0]["listing_floor_proof"]["kind"] == "AT_LEAST_120"
    assert output.source_closure["history_receipts_digest"].startswith("sha256:")
    assert output.history_receipts == tuple(receipts)


def test_h1_neighbour_failure_isolated_not_day_block(runner, resources):
    good_env_sec = _security_entry(_trend_raw("600000"))
    bad = _trend_raw("600001")
    bad_sec = _security_entry(bad)
    bad_sec["bars"] = []  # malformed market member -> per-security exclusion
    cal_days = _weekdays_ending(TARGET, 200)
    listing = [
        _listing("security-600000", effective_from=cal_days[0].isoformat()),
        _listing("security-600001", effective_from=cal_days[0].isoformat()),
    ]
    env = _envelope(
        [good_env_sec, bad_sec],
        calendar=_calendar(days=cal_days),
        listing=listing,
    )
    output = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    assert output.result.package is not None
    assert any(e["security_code"] == "600001" for e in output.source_closure["exclusions"])


def test_h1_all_fail_raises_missing_raw_not_empty_pass(runner, resources):
    bad_sec = _security_entry(_trend_raw("600000"))
    bad_sec["bars"] = []
    env = _envelope(
        [bad_sec],
        listing=[_listing("security-600000", effective_from="2026-01-01")],
    )
    # bars=[] still projects (empty bar tuple) -> run_daily_advice_from_raw fails the
    # shared-prereq / missing_bars path rather than manufacturing an empty pass.
    with pytest.raises(ProductDailyError):
        run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())


def test_h1_no_qualified_inputs_raises_missing_raw(runner, resources):
    """Zero qualified RawSecurityInput -> shared block, not a qualified-empty."""
    env = _envelope([_security_entry(_trend_raw("600000"))], listing=[])
    with pytest.raises(ProductDailyError, match="missing_raw_inputs"):
        run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
