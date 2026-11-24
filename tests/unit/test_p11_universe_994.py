"""v4-universe-994: pre-age listing eligibility + member_use three-way split.

Fix 1 (universe): a security whose qualified listing interval does not cover
T (delisted before the signal day, e.g. 000005) is isolated PER-SECURITY via
a named not_listed_on_signal_date reason -- never the shared
calendar_coverage_incomplete that a still-listed security's truncated
calendar correctly triggers.  Held/pending account members keep their held
path; still-listed securities keep the strict coverage gate.

Fix 2 (member_use): a member opened by the reader but marked
relevant_for_T=False (member_evidence required=False) is evidence, not a
same-T shared content dependency.  The use analysis separates used_for_T /
opened_not_used / registered_not_read; an opened member sealed after this
run's read is named, never a block.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from quanthus.event_library.contracts import sha256_digest
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.history_input import (
    listing_active_at_market_day,
    load_raw_history,
    raw_security_inputs_from_history,
    run_daily_advice_from_history,
)
from quanthus.product_daily.recommendation.e1_runtime import (
    _analyze_member_use,
    _used_member_ids,
)
from tests.unit.test_p11_daily_runner import (  # noqa: F401
    resources as resources,
    runner as runner,
)
from tests.unit.test_p11_history_input import (
    TARGET,
    _calendar,
    _envelope,
    _listing,
    _member,
    _run_kwargs,
    _security_entry,
    _trend_raw,
    _weekdays_ending,
)


def _szse_entry(raw, **extra):
    """A securities[] entry whose identity.venue_id is SZSE."""
    entry = _security_entry(raw, **extra)
    entry["identity"]["venue_id"] = "SZSE"
    return entry

# ---- listing_active_at_market_day ---------------------------------------------


def _history(securities, calendar, listing, market_date=TARGET):
    return load_raw_history(
        _envelope(securities, calendar=calendar, listing=listing, market_date=market_date)
    )


def test_t_active_listed_security_true():
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    hist = _history(
        [_security_entry(raw)],
        _calendar(days=days),
        [_listing(raw.identity.security_id, effective_from=days[0].isoformat())],
    )
    sec = hist.securities[0]
    assert (
        listing_active_at_market_day(
            listing_rows=hist.listing_rows,
            calendar_rows=hist.calendar_rows,
            security_id=sec["identity"]["security_id"],
            listing_id=sec["identity"]["listing_id"],
            venue_id=sec["identity"]["venue_id"],
            market_date=TARGET,
        )
        is True
    )


def test_t_active_delisted_security_false():
    """000005 shape: listing ended 2024-04-26, T=2026-10-08 -> outside interval."""
    raw = _trend_raw("000005")
    days = _weekdays_ending(TARGET, 200)
    hist = _history(
        [_szse_entry(raw)],
        _calendar(venue="SZSE", days=days),
        [
            _listing(
                raw.identity.security_id,
                venue="SZSE",
                effective_from="1990-12-09T16:00:00+08:00",
                effective_to="2024-04-26T16:00:00+08:00",
            )
        ],
    )
    sec = hist.securities[0]
    assert (
        listing_active_at_market_day(
            listing_rows=hist.listing_rows,
            calendar_rows=hist.calendar_rows,
            security_id=sec["identity"]["security_id"],
            listing_id=sec["identity"]["listing_id"],
            venue_id=sec["identity"]["venue_id"],
            market_date=TARGET,
        )
        is False
    )


def test_t_active_relist_boundary_true():
    """T = the listing effective_from day boundary -> active."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    hist = _history(
        [_security_entry(raw)],
        _calendar(days=days),
        [_listing(raw.identity.security_id, effective_from=TARGET.isoformat())],
    )
    sec = hist.securities[0]
    assert (
        listing_active_at_market_day(
            listing_rows=hist.listing_rows,
            calendar_rows=hist.calendar_rows,
            security_id=sec["identity"]["security_id"],
            listing_id=sec["identity"]["listing_id"],
            venue_id=sec["identity"]["venue_id"],
            market_date=TARGET,
        )
        is True
    )


# ---- raw_security_inputs_from_history: delisted -> per-security isolation -----


def test_delisted_neighbour_isolated_shared_gate_kept():
    """Two-venue shape: SZSE 000005 delisted before T is isolated per-security;
    SSE 600000 keeps evaluating, and the strict coverage gate for a still-
    listed security is untouched."""
    good = _security_entry(_trend_raw("600000"))
    good["identity"]["venue_id"] = "SSE"
    # trend raw is SSE by default; 000005 built on SZSE venue.
    d5 = _szse_entry(_trend_raw("000005"))
    days_sse = _weekdays_ending(TARGET, 200)
    days_szse = _weekdays_ending(TARGET, 200)
    calendar = _calendar(venue="SSE", days=days_sse) + _calendar(
        venue="SZSE", days=days_szse, member_id="szse-cal"
    )
    listing = [
        _listing("security-600000", venue="SSE", effective_from=days_sse[0].isoformat()),
        _listing(
            "security-000005",
            venue="SZSE",
            effective_from="1990-12-09T16:00:00+08:00",
            effective_to="2024-04-26T16:00:00+08:00",
            member_id="szse-cal",
            listing_id="listing-000005",
        ),
    ]
    members = [_member(), _member("szse-cal")]
    hist = load_raw_history(
        _envelope([good, d5], calendar=calendar, listing=listing, members=members)
    )
    inputs, receipts = raw_security_inputs_from_history(hist)
    codes = {i.identity.listing_code for i in inputs}
    assert "600000" in codes
    assert "000005" not in codes
    r5 = [r for r in receipts if r.get("security_code") == "000005"]
    assert r5 and r5[0]["status"] == "excluded"
    assert "not_listed_on_signal_date" in r5[0]["reason"]


def test_delisted_with_held_facts_still_member():
    """A delisted security that is an account member keeps its held path:
    it is preserved into RawSecurityInput with empty market facts, never
    dropped and never allowed to force a shared block."""
    d5 = _szse_entry(
        _trend_raw("000005"),
        held_membership=True,
        held_quantity=100,
    )
    days = _weekdays_ending(TARGET, 200)
    hist = load_raw_history(
        _envelope(
            [d5],
            calendar=_calendar(venue="SZSE", days=days, member_id="szse-cal"),
            listing=[
                _listing(
                    "security-000005",
                    venue="SZSE",
                    effective_from="1990-12-09T16:00:00+08:00",
                    effective_to="2024-04-26T16:00:00+08:00",
                    member_id="szse-cal",
                    listing_id="listing-000005",
                )
            ],
            members=[_member("szse-cal")],
        )
    )
    inputs, receipts = raw_security_inputs_from_history(hist)
    held = [i for i in inputs if i.identity.listing_code == "000005"]
    assert held and held[0].held_membership is True and held[0].held_quantity == 100
    # No membership is fabricated for a delisted security; its held/pending
    # facts ride through with the market rows it actually delivered.
    assert held[0].universe_membership == ()
    r5 = [r for r in receipts if r.get("security_code") == "000005"]
    assert r5 and r5[0]["status"] == "account_member_market_unavailable"


def test_still_listed_truncated_calendar_still_shared_block():
    """The strict gate is preserved: a STILL-LISTED security whose calendar
    starts after its listing start keeps calendar_coverage_incomplete
    (shared), not a fabricated early age."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 130)
    hist = load_raw_history(
        _envelope(
            [_security_entry(raw)],
            calendar=_calendar(days=days),
            listing=[
                _listing(
                    raw.identity.security_id,
                    effective_from=_weekdays_ending(TARGET, 200)[0].isoformat(),
                )
            ],
        )
    )
    with pytest.raises(ProductDailyError, match="calendar_coverage_incomplete"):
        raw_security_inputs_from_history(hist)


def test_end_to_end_delisted_neighbour_does_not_block_day(runner, resources):
    """Full chain: 600000 publishes an entry while the delisted 000005 is a
    named per-security isolation inside source_closure, not a day block."""
    good = _security_entry(_trend_raw("600000"))
    d5 = _szse_entry(_trend_raw("000005"))
    days_sse = _weekdays_ending(TARGET, 200)
    days_szse = _weekdays_ending(TARGET, 200)
    calendar = _calendar(venue="SSE", days=days_sse) + _calendar(
        venue="SZSE", days=days_szse, member_id="szse-cal"
    )
    listing = [
        _listing("security-600000", venue="SSE", effective_from=days_sse[0].isoformat()),
        _listing(
            "security-000005",
            venue="SZSE",
            effective_from="1990-12-09T16:00:00+08:00",
            effective_to="2024-04-26T16:00:00+08:00",
            member_id="szse-cal",
            listing_id="listing-000005",
        ),
    ]
    env = _envelope(
        [good, d5],
        calendar=calendar,
        listing=listing,
        members=[_member(), _member("szse-cal")],
    )
    output = run_daily_advice_from_history(
        env, runner=runner, resources=resources, **_run_kwargs()
    )
    assert output.result.package is not None
    assert any(e["security_code"] == "000005" for e in output.source_closure["exclusions"])
    assert any(e["security_code"] == "600000" for e in output.source_closure.get("exclusions", ())) is False




def test_relisted_same_security_id_two_listings_not_duplicate(runner, resources):
    """One corporate entity with two listing_ids (delisted 000043 + relisted
    001914 sharing security_id) is a legal relisted shape, not a duplicate.
    The duplicate guard keys on listing_id; each row is aged by its own
    listing interval."""
    days = _weekdays_ending(TARGET, 200)
    live = _szse_entry(_trend_raw("001914"))
    live["identity"]["listing_id"] = "listing-live-001914"
    dead = _szse_entry(_trend_raw("000043"))
    dead["identity"]["security_id"] = live["identity"]["security_id"]
    dead["identity"]["listing_id"] = "listing-dead-000043"
    dead["bars"] = []
    calendar = _calendar(venue="SZSE", days=days)
    listing = [
        _listing(
            live["identity"]["security_id"],
            venue="SZSE",
            effective_from=days[0].isoformat(),
            listing_id="listing-live-001914",
        ),
        _listing(
            dead["identity"]["security_id"],
            venue="SZSE",
            effective_from="1990-12-09T16:00:00+08:00",
            effective_to="2024-04-26T16:00:00+08:00",
            listing_id="listing-dead-000043",
        ),
    ]
    env = _envelope([live, dead], calendar=calendar, listing=listing)
    output = run_daily_advice_from_history(
        env, runner=runner, resources=resources, **_run_kwargs()
    )
    assert output.result is not None
    ex = [e for e in output.source_closure["exclusions"] if e["security_code"] == "000043"]
    assert ex and ex[0]["scope"] == "per_security"
    assert "not_listed" in (ex[0]["member_or_field"] + ex[0]["reason"])


def test_same_listing_id_twice_is_duplicate(runner, resources):
    """The same listing_id delivered twice stays a hard duplicate error."""
    days = _weekdays_ending(TARGET, 200)
    a = _szse_entry(_trend_raw("001914"))
    b = _szse_entry(_trend_raw("001914"))
    calendar = _calendar(venue="SZSE", days=days)
    listing = [
        _listing(
            a["identity"]["security_id"],
            venue="SZSE",
            effective_from=days[0].isoformat(),
        )
    ]
    env = _envelope([a, b], calendar=calendar, listing=listing)
    with pytest.raises(ProductDailyError, match="duplicate_security"):
        run_daily_advice_from_history(
            env, runner=runner, resources=resources, **_run_kwargs()
        )


# ---- member_use three-way split -------------------------------------------------


def _request_with_members():
    from tests.unit import test_p11_paper_replay as f

    days = tuple(d for d in f.SESSION_DAYS if d <= f.T1)
    req = f._request(market_dates=(f.T1,), securities=[f._security("600000", days)])
    return req


def test_member_evidence_required_false_is_opened_not_used():
    """A member bound only through member_evidence with required=False (the
    reader opened it but marked relevant_for_T=False) is opened_not_used,
    not a shared blocking member."""
    req = _request_with_members()
    # The fixture's single member is already bound via calendar/listing/etc.
    # Give it a second member opened-not-used by evidence alone.
    req["source_members"].append(
        {
            "member_id": "opened-extra",
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "version": "synthetic.v1",
            "member_path": "extra.parquet",
            "sha256": "sha256:" + "3" * 64,
            "date_from": "2026-01-01",
            "date_to": "2026-10-31",
            "knowledge_at": "2026-11-01T00:00:00+08:00",
            "visible_at": "2026-11-01T00:00:00+08:00",
        }
    )
    req["member_evidence"].append(
        {
            "member_id": "opened-extra",
            "version": "synthetic.v1",
            "digest": "sha256:" + "3" * 64,
            "available_at": "2026-11-01T00:00:00+08:00",
            "required": False,
        }
    )
    use = _analyze_member_use(
        type("R", (), req)(), date(2026, 10, 8), observed_at=datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)
    )
    rec = [m for m in use["members"] if m["member_id"] == "opened-extra"]
    assert rec and rec[0]["dependency_tier"] == "opened_not_used"
    assert rec[0]["is_used"] is False
    assert use["blocking_shared_members"] == []
    assert use["opened_not_used_count"] == 1


def test_member_evidence_required_true_stays_shared():
    """required=True member_evidence still binds the member as shared."""
    req = _request_with_members()
    req["source_members"].append(
        {
            "member_id": "ev-req",
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "version": "synthetic.v1",
            "member_path": "ev.parquet",
            "sha256": "sha256:" + "4" * 64,
            "date_from": "2026-01-01",
            "date_to": "2026-10-31",
            "knowledge_at": "2026-11-01T00:00:00+08:00",
            "visible_at": "2026-11-01T00:00:00+08:00",
        }
    )
    req["member_evidence"].append(
        {
            "member_id": "ev-req",
            "version": "synthetic.v1",
            "digest": "sha256:" + "4" * 64,
            "available_at": "2026-11-01T00:00:00+08:00",
            "required": True,
        }
    )
    use = _analyze_member_use(
        type("R", (), req)(), date(2026, 10, 8), observed_at=datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)
    )
    rec = [m for m in use["members"] if m["member_id"] == "ev-req"]
    assert rec and rec[0]["dependency_tier"] == "shared"
    assert rec[0]["is_used"] is True


def test_opened_member_sealed_after_read_named_not_block():
    """An opened-not-used member sealed later than this run's read instant is
    named on the evidence list, never a shared block."""
    req = _request_with_members()
    req["source_members"].append(
        {
            "member_id": "opened-late",
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "version": "synthetic.v1",
            "member_path": "late.parquet",
            "sha256": "sha256:" + "5" * 64,
            "date_from": "2026-01-01",
            "date_to": "2026-10-31",
            "knowledge_at": "2026-12-01T00:00:00+08:00",
            "visible_at": "2026-12-01T00:00:00+08:00",
        }
    )
    req["member_evidence"].append(
        {
            "member_id": "opened-late",
            "version": "synthetic.v1",
            "digest": "sha256:" + "5" * 64,
            "available_at": "2026-12-01T00:00:00+08:00",
            "required": False,
        }
    )
    use = _analyze_member_use(
        type("R", (), req)(), date(2026, 10, 8), observed_at=datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)
    )
    rec = [m for m in use["members"] if m["member_id"] == "opened-late"]
    assert rec and rec[0]["dependency_tier"] == "opened_not_used"
    assert rec[0].get("obtained_after_actual_read") is True
    assert use["blocking_shared_members"] == []
    assert use["unavailable_at_read"] == []


def test_registered_not_read_member_unused():
    """A member with no row/evidence binding is unused / registered-not-read."""
    req = _request_with_members()
    req["source_members"].append(
        {
            "member_id": "registered-unread",
            "package_id": "standard-base-2.5.1-8cd966be87fc",
            "version": "synthetic.v1",
            "member_path": "unread.parquet",
            "sha256": "sha256:" + "6" * 64,
            "date_from": "2026-01-01",
            "date_to": "2026-10-31",
            "knowledge_at": "2026-11-01T00:00:00+08:00",
            "visible_at": "2026-11-01T00:00:00+08:00",
        }
    )
    use = _analyze_member_use(
        type("R", (), req)(), date(2026, 10, 8), observed_at=datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)
    )
    rec = [m for m in use["members"] if m["member_id"] == "registered-unread"]
    assert rec and rec[0]["disposition"] == "unused"
    assert use["registered_not_read_count"] == 1

