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
) -> dict:
    # The identity carries listing-<code> (see _identity in test_p11_daily_runner);
    # the listing row must bind that exact listing_id, not a derived default.
    code = security_id.removeprefix("security-")
    return {
        "security_id": security_id,
        "listing_id": listing_id or f"listing-{code}",
        "venue_id": venue,
        "effective_from": effective_from,
        "effective_to": None,
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


def test_r3_listing_missing_anchor_isolated_not_guessed():
    """A session without open_at cannot be half-open-compared; it is named
    (listing_anchor_unavailable) rather than guessed from the date/close."""
    raw = _trend_raw("600000")
    cal = _calendar()
    cal[0].pop("open_at")  # strip the anchor on the first session
    env = _envelope(
        [_security_entry(raw)],
        calendar=cal,
        listing=[_listing(raw.identity.security_id, effective_from="2026-01-01")],
    )
    inputs, receipts = raw_security_inputs_from_history(load_raw_history(env))
    assert inputs == []
    assert receipts[0]["status"] == "excluded"
    assert "anchor" in receipts[0]["reason"].lower()
