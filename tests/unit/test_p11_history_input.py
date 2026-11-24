"""H1: p11.raw-history-input.v1 -> RawSecurityInput -> real chain -> P12.

The raw-history envelope carries original facts only (bars/calendar/listing); P11
derives the FULL universe membership from listing intervals + the venue calendar
(row_number semantics preserved -- never a 120-session truncation passed off as the
complete age), projects each qualified security, runs the real scan/panel/expert,
and binds the per-security history-gate receipts into source_closure.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
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


def _member(member_id: str = "core-daily-1d") -> dict:
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


def _calendar(venue: str = "SSE", days=None, member_id: str = "core-daily-1d") -> list:
    days = _weekdays_ending(TARGET, 260) if days is None else days
    return [
        {
            "venue_id": venue,
            "session_date": day.isoformat(),
            "is_open": True,
            "open_at": f"{day.isoformat()}T09:30:00+08:00",
            "close_at": f"{day.isoformat()}T15:00:00+08:00",
            "knowledge_from": f"{day.isoformat()}T15:30:00+08:00",
            "source_member_id": member_id,
        }
        for day in days
    ]


def _listing(
    security_id: str,
    venue: str = "SSE",
    effective_from: str = "2026-01-01",
    member_id: str = "core-daily-1d",
    listing_id: str | None = None,
    effective_to: str | None = None,
) -> dict:
    # The identity carries listing-<code> (see _identity in test_p11_daily_runner);
    # the listing row must bind that exact listing_id, not a derived default.
    code = security_id.removeprefix("security-")
    return {
        "security_id": security_id,
        "listing_id": listing_id or f"listing-{code}",
        "venue_id": venue,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "knowledge_from": "2026-01-01T00:00:00+08:00",
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
        "close_at_by_date": {
            d.isoformat(): (None if t is None else t.isoformat())
            for d, t in raw.close_at_by_date.items()
        },
        "knowledge_cutoff_by_date": {
            d.isoformat(): (None if t is None else t.isoformat())
            for d, t in raw.knowledge_cutoff_by_date.items()
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
    # SSE listing + a calendar that only carries SZSE sessions cannot prove the
    # membership -> a shared coverage failure (the venue's calendar is wrong), not
    # a per-security isolation.
    with pytest.raises(ProductDailyError, match="calendar_coverage_incomplete"):
        raw_security_inputs_from_history(history)


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
            listing_id="listing-1",
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
    # The receipts live inside source_closure (the frozen 7-field output is
    # unchanged); they carry the enriched satisfied/failed verdicts.
    assert receipts[0]["gates"]["features"]["status"] == "satisfied"
    assert receipts[0]["raw_history_digest"].startswith("sha256:")


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


# --- H1-R1 root-intake consolidated repair -------------------------------------------


def test_r1_source_member_sha_changes_job_identity(runner, resources):
    """A source member sha change must drift the closure digest, not reuse a pass."""
    from copy import deepcopy

    env = _trend_envelope()
    out1 = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    env2 = deepcopy(env)
    env2["source_members"][0]["sha256"] = "sha256:" + "c" * 64
    out2 = run_daily_advice_from_history(env2, runner=runner, resources=resources, **_run_kwargs())
    assert out1.source_closure != out2.source_closure
    assert out1.receipt.source_closure_digest != out2.receipt.source_closure_digest


def test_r1_source_knowledge_after_cutoff_rejected(runner, resources):
    env = _trend_envelope()
    env["source_members"][0]["knowledge_at"] = "2027-01-01T18:01:00+08:00"
    with pytest.raises(ProductDailyError, match="source_knowledge_after_cutoff"):
        run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())


def test_r1_listing_knowledge_after_cutoff_rejected(runner, resources):
    env = _trend_envelope()
    env["listing_rows"][0]["knowledge_from"] = "2027-01-01T00:00:00+08:00"
    with pytest.raises(ProductDailyError, match="listing_knowledge_after_cutoff"):
        run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())


def test_r1_envelope_cutoff_must_match_policy(runner, resources):
    """The envelope cannot declare its own later deadline to admit a late member."""
    env = _trend_envelope()
    env["decision_cutoff"] = "2026-11-25T23:30:00+08:00"
    env["source_members"][0]["visible_at"] = "2026-11-24T23:00:00+08:00"
    with pytest.raises(ProductDailyError, match="envelope_decision_cutoff_mismatch"):
        run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())


def test_r1_only_21_bars_is_failed_not_qualified_empty(runner, resources):
    """21 bars -> features gate 'failed' / not_qualified, never a qualified empty."""
    env = _trend_envelope()
    env["securities"][0]["bars"] = env["securities"][0]["bars"][-21:]
    output = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    receipt = output.source_closure["history_receipts"][0]
    assert receipt["gates"]["features"]["status"] == "failed"
    assert receipt["gates"]["features"]["observed"] == 21
    assert receipt["status"] == "not_qualified"


def test_r1_malformed_neighbor_isolated_valid_entry_kept(runner, resources):
    good = _security_entry(_trend_raw("600000"))
    bad = _security_entry(_trend_raw("600001"))
    bad["bars"][0]["close_price"] = "bad-close"
    cal_days = _weekdays_ending(TARGET, 200)
    env = _envelope(
        [good, bad],
        calendar=_calendar(days=cal_days),
        listing=[
            _listing("security-600000", effective_from=cal_days[0].isoformat()),
            _listing("security-600001", effective_from=cal_days[0].isoformat()),
        ],
    )
    output = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    assert output.result.package is not None
    assert any(e["security_code"] == "600001" for e in output.source_closure["exclusions"])
    entries = [d for d in output.seam.decisions if d.entry_allowed]
    assert entries


def test_r1_string_false_calendar_rejected():
    env = _trend_envelope()
    env["calendar_rows"][0]["is_open"] = "false"
    with pytest.raises(ProductDailyError, match="invalid_raw_history"):
        load_raw_history(env)


def test_r1_conflicting_calendar_row_rejected():
    env = _trend_envelope()
    dup = dict(env["calendar_rows"][0])
    dup["is_open"] = not dup["is_open"]
    env["calendar_rows"].append(dup)
    with pytest.raises(ProductDailyError, match="conflicting_calendar_row"):
        load_raw_history(env)


def test_r1_known_held_missing_listing_hold_alert(runner, resources):
    """held_membership=True + missing listing -> hold_alert, never dropped/entry."""
    env = _trend_envelope()
    env["securities"][0]["held_membership"] = True
    env["securities"][0]["held_quantity"] = 100
    env["listing_rows"] = []
    output = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    actions = [d.action for d in output.seam.held_decisions]
    assert actions == ["hold_alert"]
    stages = [d.stage for ds in output.result.package.decision_sets for d in ds.decisions]
    assert "entry_plan" not in stages


def test_r1_ruledayoutput_keeps_frozen_7_fields():
    from dataclasses import fields

    from quanthus.product_daily.recommendation.rule_seam import RuleDayOutput

    assert [f.name for f in fields(RuleDayOutput)] == [
        "seam",
        "candidates",
        "held_signals",
        "receipt",
        "result",
        "source_closure",
        "stage_metrics",
    ]


def test_r1_truncated_calendar_coverage_rejected(runner, resources):
    """A calendar starting after the listing start cannot prove the full age."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 130)  # only 130 sessions
    env = _envelope(
        [_security_entry(raw)],
        calendar=_calendar(days=cal_days),
        # listing claims to start 200 sessions ago but calendar only covers 130
        listing=[
            _listing(
                raw.identity.security_id,
                effective_from=_weekdays_ending(TARGET, 200)[0].isoformat(),
            )
        ],
    )
    with pytest.raises(ProductDailyError, match="calendar_coverage_incomplete"):
        run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())


def test_r1_119_membership_below_gate_not_qualified(runner, resources):
    """119 real membership sessions -> listing gate 'failed', not a qualified empty."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 119)
    env = _envelope(
        [_security_entry(raw)],
        calendar=_calendar(days=cal_days),
        listing=[_listing(raw.identity.security_id, effective_from=cal_days[0].isoformat())],
    )
    # bars also exceed available history; the run must not publish a fake entry.
    try:
        output = run_daily_advice_from_history(
            env, runner=runner, resources=resources, **_run_kwargs()
        )
    except ProductDailyError:
        return  # a whole-day block is also a valid fail-closed outcome
    receipt = output.source_closure["history_receipts"][0]
    assert receipt["gates"]["listing"]["status"] == "failed"
    assert receipt["status"] == "not_qualified"


# --- H1-R2 C6 consolidated repair ----------------------------------------------------


def _envelope_bars(code: str, n_bars: int, n_cal: int = 200, n_membership: int = 200) -> dict:
    """A trend security truncated to n_bars inside a full listing envelope."""
    raw = _trend_raw(code)
    entry = _security_entry(raw)
    entry["bars"] = entry["bars"][-n_bars:]
    cal_days = _weekdays_ending(TARGET, n_cal)
    listing_start = cal_days[-n_membership] if n_membership < n_cal else cal_days[0]
    return _envelope(
        [entry],
        calendar=_calendar(days=cal_days),
        listing=[_listing(raw.identity.security_id, effective_from=listing_start.isoformat())],
    )


@pytest.mark.parametrize("n_bars", [21, 22, 60])
def test_r2_insufficient_history_blocks_publication(runner, resources, n_bars):
    """C6 P1-1: 21/22/60 bars -> every market input not_qualified AND no held
    evaluation -> the result is a blocked day, never a publishable ended."""
    output = run_daily_advice_from_history(
        _envelope_bars("600000", n_bars), runner=runner, resources=resources, **_run_kwargs()
    )
    assert output.result.publishable is False
    assert output.seam.day_blocked is True
    stages = (
        [d.stage for ds in output.result.package.decision_sets for d in ds.decisions]
        if output.result.package is not None
        else []
    )
    assert "entry_plan" not in stages
    receipt = output.source_closure["history_receipts"][0]
    assert receipt["status"] == "not_qualified"


def test_r2_119_membership_blocks_publication(runner, resources):
    """C6 P1-1: a real 119-session membership fails the listing gate -> blocked."""
    output = run_daily_advice_from_history(
        _envelope_bars("600000", 130, n_cal=200, n_membership=119),
        runner=runner,
        resources=resources,
        **_run_kwargs(),
    )
    assert output.result.publishable is False
    assert output.seam.day_blocked is True
    receipt = output.source_closure["history_receipts"][0]
    assert receipt["gates"]["listing"]["status"] == "failed"
    assert receipt["status"] == "not_qualified"


def test_r2_qualified_no_signal_stays_publishable(runner, resources):
    """61+ bars AND >=120 membership with a genuine no-signal -> a publishable
    empty/ended day is still allowed (qualified-no-signal, not a blocked day)."""
    output = run_daily_advice_from_history(
        _trend_envelope(), runner=runner, resources=resources, **_run_kwargs()
    )
    assert output.result.publishable is True
    assert output.seam.day_blocked is False


def test_r2_held_still_evaluates_when_market_fails(runner, resources):
    """A known-held security whose market parse failed still gets its held review
    (account_member_market_unavailable), and the day is not falsely blocked."""
    env = _trend_envelope()
    env["securities"][0]["held_membership"] = True
    env["securities"][0]["held_quantity"] = 100
    env["securities"][0]["bars"] = []  # market member broken
    output = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    actions = [d.action for d in output.seam.held_decisions]
    assert actions == ["hold_alert"]
    assert output.seam.day_blocked is False


def test_r2_exclusion_bound_into_issued_closure(runner, resources):
    """C6 P1-2: a malformed neighbour's exclusion is inside the SAME closure the
    receipt digests - never appended after issuance (digest must match)."""
    good = _security_entry(_trend_raw("600000"))
    bad = _security_entry(_trend_raw("600001"))
    bad["bars"][0]["close_price"] = "bad-close"
    cal_days = _weekdays_ending(TARGET, 200)
    env = _envelope(
        [good, bad],
        calendar=_calendar(days=cal_days),
        listing=[
            _listing("security-600000", effective_from=cal_days[0].isoformat()),
            _listing("security-600001", effective_from=cal_days[0].isoformat()),
        ],
    )
    output = run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())
    closure = output.source_closure
    exclusions = closure["exclusions"]
    assert any(e["security_code"] == "600001" for e in exclusions)
    # The issued receipt's digest must equal the digest of the closure actually
    # carried on the output - the exclusion is inside the same bound object.
    from quanthus.event_library.contracts import sha256_digest

    assert output.receipt.source_closure_digest == sha256_digest(closure)
    # The good neighbour still produces a real entry through the package.
    assert output.result.publishable is True
    entries = [d for d in output.seam.decisions if d.entry_allowed]
    assert entries


def test_r2_listing_effective_timestamp_accepted_and_projected():
    """C6 P1-3: P10 effective_from as a UTC timestamp projects onto the venue's
    session date - not rejected, not truncated to a UTC date."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    # 16:00 UTC on the first cal day == 00:00+08 next day; still the SAME SSE day
    # once anchored to Asia/Shanghai.  effective_from is a UTC instant.
    from datetime import timezone as _tz

    utc_from = datetime.combine(cal_days[0], datetime.min.time(), tzinfo=_tz.utc).isoformat()
    env = _envelope(
        [_security_entry(raw)],
        calendar=_calendar(days=cal_days),
        listing=[
            _listing(raw.identity.security_id, effective_from=utc_from),
        ],
    )
    history = load_raw_history(env)  # must not reject the timestamp
    inputs, _ = raw_security_inputs_from_history(history)
    assert inputs[0].universe_membership[0] == cal_days[0]


def test_r3_listing_timestamp_effective_after_session_open_excluded():
    """effective_from=23:00+08 on day X is AFTER that session's 09:30 open ->
    the half-open interval excludes day X (the listing was not yet active at
    its observation start).  This is the baseline instant comparison, not a
    date projection."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    late = datetime.combine(cal_days[0], datetime.min.time()).replace(hour=23, tzinfo=SHANGHAI)
    env = _envelope(
        [_security_entry(raw)],
        calendar=_calendar(days=cal_days),
        listing=[_listing(raw.identity.security_id, effective_from=late.isoformat())],
    )
    history = load_raw_history(env)
    inputs, _ = raw_security_inputs_from_history(history)
    # The listing only becomes active at 23:00 - after day0's 09:30 anchor, so
    # day0 is excluded and membership starts on day1.
    assert inputs[0].universe_membership[0] == cal_days[1]
    assert len(inputs[0].universe_membership) == 199


def test_r3_listing_timestamp_effective_at_session_open_included():
    """effective_from exactly == session open_at satisfies the closed lower
    bound effective_from <= observation_start -> that session IS a member."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    at_open = datetime.combine(cal_days[0], datetime.min.time()).replace(
        hour=9, minute=30, tzinfo=SHANGHAI
    )
    env = _envelope(
        [_security_entry(raw)],
        calendar=_calendar(days=cal_days),
        listing=[_listing(raw.identity.security_id, effective_from=at_open.isoformat())],
    )
    inputs, _ = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs[0].universe_membership[0] == cal_days[0]
    assert len(inputs[0].universe_membership) == 200


def test_r2_wrong_listing_id_rejected_not_matched():
    """C6 P1-3: a listing row for a different listing_id must NOT produce a
    membership - identity binds security_id AND listing_id AND venue."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    env = _envelope(
        [_security_entry(raw)],
        calendar=_calendar(days=cal_days),
        listing=[
            _listing(
                raw.identity.security_id,
                effective_from=cal_days[0].isoformat(),
                listing_id="wrong-listing",
            )
        ],
    )
    inputs, receipts = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs == []
    assert receipts[0]["status"] == "excluded"
    assert "listing" in receipts[0]["reason"].lower()


def test_r3_listing_effective_to_at_session_start_excludes_T():
    """effective_to=2026-11-24T00:00+08 ends BEFORE T's 09:30 open -> the
    half-open interval excludes T (open < effective_to fails), so T is not a
    member even though the date matches.  Baseline: anchor < effective_to."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    listing = _listing(raw.identity.security_id, effective_from=cal_days[0].isoformat())
    listing["effective_to"] = f"{TARGET.isoformat()}T00:00:00+08:00"
    env = _envelope([_security_entry(raw)], calendar=_calendar(days=cal_days), listing=[listing])
    inputs, _ = raw_security_inputs_from_history(load_raw_history(env))
    membership = inputs[0].universe_membership
    assert TARGET not in membership
    assert len(membership) == 199


def test_r3_listing_effective_from_after_close_excludes_T():
    """effective_from=2026-11-24T20:00+08 (after T's 09:30 open) -> T is not
    yet a member; membership would begin on the next session's open."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    listing = _listing(raw.identity.security_id, effective_from=cal_days[0].isoformat())
    listing["effective_from"] = f"{TARGET.isoformat()}T20:00:00+08:00"
    env = _envelope([_security_entry(raw)], calendar=_calendar(days=cal_days), listing=[listing])
    inputs, _ = raw_security_inputs_from_history(load_raw_history(env))
    membership = inputs[0].universe_membership
    assert membership == ()  # no session's anchor is at/after 20:00 yet


def test_r3_listing_z_and_offset_equivalent():
    """ISO 'Z' and '+00:00' spell the same instant; Python 3.10 needs the
    normalisation.  Both must produce the identical membership."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)

    def _build(suffix: str):
        listing = _listing(
            raw.identity.security_id,
            effective_from=cal_days[0].isoformat() + suffix,
        )
        return _envelope(
            [_security_entry(raw)], calendar=_calendar(days=cal_days), listing=[listing]
        )

    # A bare date is a legacy contract; use a timestamp with Z vs +08:00 at the
    # same instant so the comparison is meaningful. 2026-02-18T01:30Z == 09:30+08.
    z = _listing(raw.identity.security_id, effective_from="2026-02-18T01:30:00Z")
    off = _listing(raw.identity.security_id, effective_from="2026-02-18T09:30:00+08:00")
    env_z = _envelope([_security_entry(raw)], calendar=_calendar(), listing=[z])
    env_off = _envelope([_security_entry(raw)], calendar=_calendar(), listing=[off])
    iz, _ = raw_security_inputs_from_history(load_raw_history(env_z))
    io, _ = raw_security_inputs_from_history(load_raw_history(env_off))
    assert iz[0].universe_membership == io[0].universe_membership


def test_r4_missing_anchor_before_listing_does_not_block():
    """C6 P1-2: an anchor missing on a session BEFORE the listing starts is
    irrelevant to that listing's age and must not exclude it."""
    raw = _trend_raw("600000")
    cal = _calendar()
    cal[0].pop("open_at")  # earliest session, long before the listing
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from="2026-01-01")],
    )
    inputs, receipts = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs  # the irrelevant missing anchor must not exclude the security
    assert inputs[0].universe_membership


def test_r4_missing_anchor_inside_listing_isolated():
    """C6 P1-2: a missing anchor on a session that could intersect the listing
    interval IS named (listing_anchor_unavailable), never guessed."""
    raw = _trend_raw("600000")
    cal_days = _weekdays_ending(TARGET, 200)
    cal = _calendar(days=cal_days)
    listing_start = cal_days[100]
    # Strip the anchor on a session inside the listing window (after the start).
    victim = cal_days[150]
    for row in cal:
        if row["session_date"] == victim.isoformat():
            row.pop("open_at")
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from=listing_start.isoformat())],
    )
    inputs, receipts = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs == []
    assert receipts[0]["status"] == "excluded"
    assert "anchor" in receipts[0]["reason"].lower()


def test_r4_conflicting_open_at_duplicate_rejected():
    """C6 P1-1: two rows for the same (venue, session) with DIFFERENT open_at
    are a named conflict, never 'first row wins'."""
    raw = _trend_raw("600000")
    cal = _calendar()
    # Second row: same session, a DIFFERENT open_at anchor.
    dup = dict(cal[0])
    dup["open_at"] = "2026-02-18T10:00:00+08:00"
    cal.append(dup)
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from="2026-01-01")],
    )
    with pytest.raises(ProductDailyError, match="conflicting_calendar_row"):
        raw_security_inputs_from_history(load_raw_history(env))


def test_r4_conflicting_open_at_threshold_not_first_wins():
    """C6 P1-1: at a listing ending 09:45, duplicate anchors 09:30 vs 10:00
    would change the membership boundary - the conflict is rejected, not
    resolved by taking the first row."""
    raw = _trend_raw("600000")
    cal = _calendar()
    target_day = cal[0]["session_date"]
    # Make the first session's listing end between the two candidate anchors.
    listing = _listing(
        raw.identity.security_id,
        effective_from="2026-01-01",
        effective_to=f"{target_day}T09:45:00+08:00",
    )
    dup = dict(cal[0])
    dup["open_at"] = f"{target_day}T10:00:00+08:00"  # would EXCLUDE vs 09:30 include
    cal.append(dup)
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[listing],
    )
    with pytest.raises(ProductDailyError, match="conflicting_calendar_row"):
        raw_security_inputs_from_history(load_raw_history(env))


def test_r4_identical_duplicate_normalised():
    """An exact-duplicate calendar row (same instant for every clock field)
    is normalised to one row, not a conflict; membership is unchanged."""
    raw = _trend_raw("600000")
    cal = _calendar()
    dup = dict(cal[0])  # byte-identical duplicate
    cal.append(dup)
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from="2026-01-01")],
    )
    inputs, _ = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs[0].universe_membership
    # Order-insensitive: reversing the duplicate keeps the same membership.
    cal2 = _calendar()
    dup2 = dict(cal2[0])
    cal2.append(dup2)
    cal2.reverse()
    env2 = _envelope(
        [_security_entry(raw)],
        calendar=cal2,
        listing=[_listing(raw.identity.security_id, effective_from="2026-01-01")],
    )
    inputs2, _ = raw_security_inputs_from_history(load_raw_history(env2))
    assert inputs2[0].universe_membership == inputs[0].universe_membership


def test_r4_equivalent_instant_open_at_normalised():
    """open_at spelled '09:30+08:00' vs '01:30Z' is the SAME instant ->
    normalised to one row (equivalent-timezone instants compare equal)."""
    raw = _trend_raw("600000")
    cal = _calendar()
    dup = dict(cal[0])
    # cal[0]'s 09:30+08 == the same instant spelled with Z: -8h -> 01:30Z.
    dup["open_at"] = cal[0]["session_date"] + "T01:30:00Z"
    cal.append(dup)
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from="2026-01-01")],
    )
    inputs, _ = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs[0].universe_membership


# --- h1-exact-day-membership: equivalent-day anchor proof ---------------------------
#
# Real October evidence (terms-740-897-v2): both venues carry is_open=True rows
# whose open_at is null for 42 sessions 2026-08-03..2026-09-30.  The named venue
# contract (SSE order-acceptance 09:30 / SZSE 09:15, Asia/Shanghai) bounds every
# possible open instant inside [local_midnight, next_midnight), so a session whose
# whole local day is strictly inside one listing interval still counts exactly.
# anchor_mode is an explicit consumer config: "strict" keeps R4 isolation; the
# "equivalent_day" mode resolves only provable days and names each on the receipt.


def _cal_null_anchor(venue: str, days, member_id: str = "core-daily-1d") -> list:
    """is_open rows with open_at ABSENT (the real October Base shape)."""
    return [
        {
            "venue_id": venue,
            "session_date": day.isoformat(),
            "is_open": True,
            "open_at": None,
            "close_at": f"{day.isoformat()}T15:00:00+08:00",
            "knowledge_from": f"{day.isoformat()}T15:30:00+08:00",
            "source_member_id": member_id,
        }
        for day in days
    ]


def test_eqday_resolves_sessions_wholly_inside_listing():
    """Missing anchor + whole local day strictly inside the listing interval
    -> counted exactly (equivalent_day), never isolated."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    cal = _cal_null_anchor("SSE", days)  # EVERY session lacks open_at
    # Listing is active from the FIRST delivered session's midnight so every
    # open day bound is strictly inside AND coverage covers the start day.
    eff_from = days[0].isoformat()
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from=eff_from)],
    )
    inputs, receipts = raw_security_inputs_from_history(
        load_raw_history(env), anchor_mode="equivalent_day"
    )
    assert inputs  # the day-bound proof carried the membership, not an exclusion
    assert len(inputs[0].universe_membership) == len(days)
    receipt = receipts[0]
    assert receipt["membership_proof"]["kind"] == "mixed"
    assert receipt["membership_proof"]["anchor_mode"] == "equivalent_day"
    assert receipt["membership_proof"]["equivalent_day_sessions"]


def test_eqday_strict_mode_still_isolates_missing_anchor():
    """Default anchor_mode='strict' (R4) keeps the missing-anchor isolation -
    equivalent_day must be opted into, never a silent default."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    cal = _cal_null_anchor("SSE", days)
    listing_start = days[50]
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from=listing_start.isoformat())],
    )
    inputs, receipts = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs == []
    assert "anchor" in receipts[0]["reason"].lower()


def test_eqday_listing_start_straddle_is_unresolved():
    """The session whose local day CONTAINS the listing's effective_from
    (eff_from.date == session) straddles the interval start -> unresolved even
    in equivalent_day (the open could fall before the interval begins)."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    cal = _cal_null_anchor("SSE", days)
    start_day = days[50]
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[
            _listing(
                raw.identity.security_id,
                effective_from=f"{start_day.isoformat()}T10:00:00+08:00",
            )
        ],
    )
    inputs, receipts = raw_security_inputs_from_history(
        load_raw_history(env), anchor_mode="equivalent_day"
    )
    assert inputs == []
    assert "anchor" in receipts[0]["reason"].lower()


def test_eqday_delist_day_straddle_is_unresolved():
    """A session whose local day contains effective_to straddles the interval
    end -> unresolved (the open could be at/after the listing ended)."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    cal = _cal_null_anchor("SSE", days)
    end_day = days[150]
    eff_from = days[0].isoformat()
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[
            _listing(
                raw.identity.security_id,
                effective_from=eff_from,
                effective_to=f"{end_day.isoformat()}T11:00:00+08:00",
            )
        ],
    )
    inputs, receipts = raw_security_inputs_from_history(
        load_raw_history(env), anchor_mode="equivalent_day"
    )
    assert inputs == []
    assert "anchor" in receipts[0]["reason"].lower()


def test_eqday_session_outside_all_intervals_not_counted():
    """Missing anchor on a session wholly OUTSIDE every listing interval is not
    counted and not isolated (it provably cannot change the age)."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 200)
    cal = _cal_null_anchor("SSE", days)
    # listing window covers only the LAST 30 sessions; the first 170 missing
    # anchors are wholly outside every interval -> count 0, no isolation.
    listing_start = days[170]
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[
            _listing(
                raw.identity.security_id,
                effective_from=f"{listing_start.isoformat()}T00:00:00+08:00",
            )
        ],
    )
    inputs, _ = raw_security_inputs_from_history(
        load_raw_history(env), anchor_mode="equivalent_day"
    )
    assert inputs
    assert len(inputs[0].universe_membership) == len(days) - 170


def test_eqday_real_october_intervals_count_exactly():
    """Real listing intervals (600000 XSHG / 002040 XSHE / 000002 XSHE / 002238
    XSHE) all span the 42 missing-anchor sessions 2026-08-03..2026-09-30 with
    effective_to=None -> every missing-anchor session resolves wholly inside
    (verdict True) and none straddles a boundary.  This exercises the real
    interval shapes directly; coverage is a separate gate."""
    from quanthus.product_daily.recommendation.history_input import (
        ListingRow,
        _equivalent_day_membership,
        _listing_instant,
    )

    # The 42 sessions that lack open_at in the real October/November Base.
    missing = [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
        "2026-08-10",
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
        "2026-08-14",
        "2026-08-17",
        "2026-08-18",
        "2026-08-19",
        "2026-08-20",
        "2026-08-21",
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-04",
        "2026-09-07",
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
        "2026-09-11",
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
        "2026-09-28",
        "2026-09-29",
        "2026-09-30",
    ]
    cases = [
        (
            "600000",
            "XSHG",
            "security_2e02245cf8b28319ec8af0e9554cdc24",
            "listing_5edd7b7cdd4b6445afae0d2cca6b85ce",
            "1999-11-09T16:00:00+00:00",
        ),
        (
            "002040",
            "XSHE",
            "security_72946bcaf22e26f7028e5ffe79c54424",
            "listing_e30521d51b0e506341403f5a70da7a1d",
            "2005-03-24T16:00:00+00:00",
        ),
        (
            "000002",
            "XSHE",
            "security_011cff7c2146007a3e51037c8476d8ab",
            "listing_c49a60eac39b48d4e349a1cc6dab0e56",
            "1991-01-28T16:00:00+00:00",
        ),
        (
            "002238",
            "XSHE",
            "security_00051d650c0deebabb68fc4367ce2df4",
            "listing_204f88502b2f60311ddbc730878af434",
            "2008-05-25T16:00:00+00:00",
        ),
    ]
    for code, venue, sec_id, list_id, eff_from in cases:
        listing_row = ListingRow(
            security_id=sec_id,
            listing_id=list_id,
            venue_id=venue,
            effective_from=_listing_instant(eff_from, "effective_from", venue),
            effective_to=None,
            knowledge_from=datetime(2026, 8, 3, 23, 59, tzinfo=SHANGHAI),
            source_member_id="core-daily-1d",
        )
        resolved = 0
        for sess in missing:
            verdict = _equivalent_day_membership(
                session=date.fromisoformat(sess),
                intervals=[listing_row],
                venue_id=venue,
            )
            # Each session's whole local day is strictly inside the open-ended
            # listing interval -> provably a member (count 1).
            assert verdict is True, (code, sess, verdict)
            resolved += 1
        assert resolved == 42


def test_eqday_receipt_marks_proof_source_and_kind():
    """The per-security receipt names the proof kind + source so an
    equivalent-day count is never passed off as a stored-anchor instant."""
    raw = _trend_raw("600000")
    days = _weekdays_ending(TARGET, 130)
    cal = _cal_null_anchor("SSE", days)
    eff_from = days[0].isoformat()
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from=eff_from)],
    )
    sinks: dict[str, dict[str, str]] = {}
    _, receipts = raw_security_inputs_from_history(
        load_raw_history(env), anchor_mode="equivalent_day", proof_sinks=sinks
    )
    assert "600000" in sinks
    assert all(v == "equivalent_day" for v in sinks["600000"].values())
    proof = receipts[0]["membership_proof"]
    assert proof["anchor_mode"] == "equivalent_day"
    assert "open_at" in proof["source"]
    # exact_instant kind when nothing needed the day bound
    _, receipts2 = raw_security_inputs_from_history(load_raw_history(_trend_envelope()))
    assert receipts2[0]["membership_proof"]["kind"] == "exact_instant"


# --- oct08-context-real: date-context warm-up rows inside the as-observed envelope ---


def _gap_clocked_entry(code="600000", gap_days=42):
    """Security entry whose trailing gap window has null close/cutoff instants."""

    from tests.unit.test_p11_daily_runner import _gap_clocked_raw

    return _security_entry(_gap_clocked_raw(code, gap_days))


def _gap_envelope(code="600000", venue="SSE", gap_days=42, days=260):
    raw = _trend_raw(code)
    sec = _gap_clocked_entry(code, gap_days)
    cal_days = _weekdays_ending(TARGET, days)
    return _envelope(
        [sec],
        calendar=_calendar(venue=venue, days=cal_days),
        listing=[_listing(raw.identity.security_id, venue=venue, effective_from="2026-01-01")],
    )


def test_date_context_envelope_loads_and_projects():
    """Explicit null close/cutoff entries load and project as declared absences."""

    env = _gap_envelope()
    history = load_raw_history(env)
    inputs, _receipts = raw_security_inputs_from_history(history)
    assert inputs
    signal = date.fromisoformat(env["market_date"])
    assert inputs[0].close_at_by_date[signal] is not None
    gaps = [d for d, v in inputs[0].close_at_by_date.items() if v is None]
    assert len(gaps) == 42


def test_date_context_strict_envelope_still_blocks(runner, resources):
    """Strict envelope: null warm-up instants stay a shared block."""

    env = _gap_envelope()
    with pytest.raises(ProductDailyError, match="missing_raw_inputs"):
        run_daily_advice_from_history(env, runner=runner, resources=resources, **_run_kwargs())


def test_date_context_numeric_context_gated(runner, resources):
    """numeric_context without as_observed is a contract breach."""

    env = _gap_envelope()
    with pytest.raises(ProductDailyError, match="numeric_context"):
        run_daily_advice_from_history(
            env,
            runner=runner,
            resources=resources,
            numeric_context="october_replay_as_observed",
            **_run_kwargs(),
        )


def test_date_context_as_observed_equivalent(runner, resources):
    """as_observed + named context reaches the same numeric path as full clocks."""

    env = _gap_envelope()
    out_ctx = run_daily_advice_from_history(
        env,
        runner=runner,
        resources=resources,
        as_observed=True,
        numeric_context="october_replay_as_observed",
        consumption_evidence={"replay": "oct08-42"},
        **_run_kwargs(),
    )
    out_full = run_daily_advice_from_history(
        _trend_envelope(), runner=runner, resources=resources, **_run_kwargs()
    )
    assert [d.disposition for d in out_ctx.seam.decisions] == [
        d.disposition for d in out_full.seam.decisions
    ]
    assert [d.decision.to_dict() for d in out_ctx.seam.entries] == [
        d.decision.to_dict() for d in out_full.seam.entries
    ]
    named = out_ctx.source_closure["consumption_evidence"]["date_context_rows"]
    assert len(named) == 42


def test_date_context_neighbor_isolated(runner, resources):
    """A malformed neighbour is isolated while the date-context entry stands."""

    good = _gap_clocked_entry("600000")
    bad_raw = _trend_raw("600001")
    bad = _security_entry(bad_raw)
    bad["bars"] = []  # malformed market member -> per-security exclusion
    cal_days = _weekdays_ending(TARGET, 200)
    listing = [
        _listing("security-600000", effective_from=cal_days[0].isoformat()),
        _listing("security-600001", effective_from=cal_days[0].isoformat()),
    ]
    env = _envelope(
        [good, bad],
        calendar=_calendar(days=cal_days),
        listing=listing,
    )
    out = run_daily_advice_from_history(
        env,
        runner=runner,
        resources=resources,
        as_observed=True,
        numeric_context="october_replay_as_observed",
        consumption_evidence={"replay": "oct08-42"},
        **_run_kwargs(),
    )
    assert any(e["security_code"] == "600001" for e in out.source_closure["exclusions"])
    named = out.source_closure["consumption_evidence"]["date_context_rows"]
    assert len(named) == 42
