from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quanthus.r9_qualification import case_facts, check_roles, quality_blocks


def roles():
    rows = []
    for role, anchor, count in (("train", "2020-03-31", 12), ("train", "2020-04-30", 12),
                               ("guard", "2020-07-31", 8), ("update", "2020-08-31", 8),
                               ("evaluation", "2020-11-30", 12), ("evaluation", "2020-12-31", 12)):
        for _ in range(count):
            i = len(rows)
            end = date.fromisoformat(anchor)
            sessions = [{"date": str(end - timedelta(days=39-j)), "session_id": f"{i}/{j}"}
                        for j in range(40)]
            rows.append({key: f"{key}-{i}" for key in ("company_id", "security_id", "listing_id",
                                                       "sample_id", "episode_id")})
            rows[-1].update(role=role, anchor=anchor, sessions=sessions, listing_code=f"{i:06d}")
    return {"rows": rows}


def test_role_denominator_and_identity_separation():
    value = roles()
    check_roles(value)
    value["rows"][24]["company_id"] = value["rows"][0]["company_id"]
    with pytest.raises(ValueError, match="IDENTITY_OVERLAP"):
        check_roles(value)


def test_fixed_role_and_window_no_drop_or_padding():
    value = roles()
    value["rows"].pop()
    with pytest.raises(ValueError, match="DENOMINATOR"):
        check_roles(value)
    value = roles()
    value["rows"][0]["sessions"].pop()
    with pytest.raises(ValueError, match="FIXED40"):
        check_roles(value)


def test_role_time_order_rejects_future_training():
    value = roles()
    value["rows"][0]["anchor"] = "2020-08-31"
    value["rows"][0]["sessions"] = deepcopy(value["rows"][32]["sessions"])
    with pytest.raises(ValueError, match="TIME_ORDER"):
        check_roles(value)


def sample():
    identity = roles()["rows"][0]
    identity["venue_id"] = "synthetic-venue"
    native = [{"market_date": date.fromisoformat(s["date"]), "calendar_session_id": s["session_id"],
               "bar_ok": True, "state_ok": True, "high": Decimal(105+i % 3), "low": Decimal(95),
               "close": Decimal(100+i % 2), "volume": Decimal(1000),
               "source_record_id": f"row-{i}", "source_revision": "synthetic", "source_artifact_sha256": "sha256:"+"1"*64,
               "knowledge_from": "2026-01-01T00:00:00+00:00"}
              for i, s in enumerate(identity["sessions"])]
    kwargs = {"cutoff": datetime(2026, 11, 11, tzinfo=timezone.utc), "exchange": "SSE",
              "request_digest": "sha256:"+"2"*64, "calendar_digest": "sha256:"+"3"*64,
              "action_digest": "sha256:"+"4"*64, "block_reasons": []}
    return identity, native, kwargs


def test_valid_facts_and_blind_packet_exclude_roles_features_and_dates():
    identity, native, kwargs = sample()
    result = case_facts(identity, native, **kwargs)
    assert result["available"] and len(result["features"]) == 7
    assert set(result["packet"]) == {"case_id", "available", "HLC40"}
    assert len(result["packet"]["HLC40"]) == 40


@pytest.mark.parametrize("invalid", ["bar_ok", "state_ok"])
def test_missing_or_untradable_source_stays_unavailable(invalid):
    identity, native, kwargs = sample()
    native[7][invalid] = False
    result = case_facts(identity, native, **kwargs)
    assert not result["available"] and result["features"] is None
    assert "HLC40" not in result["packet"]


def test_action_hold_is_not_a_negative_reference():
    identity, native, kwargs = sample()
    kwargs["block_reasons"] = ["native_action_or_unknown_date"]
    result = case_facts(identity, native, **kwargs)
    assert not result["available"] and result["features"] is None
    assert "label" not in result


def test_wrong_session_is_rejected_before_facts():
    identity, native, kwargs = sample()
    native[0]["calendar_session_id"] = "wrong-session"
    with pytest.raises(ValueError, match="SESSION_BINDING"):
        case_facts(identity, native, **kwargs)


def test_required_calendar_and_state_quality_follow_exact_coordinate():
    identity, _, _ = sample()
    session, day = identity["sessions"][5]["session_id"], identity["sessions"][5]["date"]
    assert quality_blocks(identity, "dwd_trading_calendar", {"calendar_session_id": session})
    assert quality_blocks(identity, "dwd_trading_calendar", {"venue_id": identity["venue_id"], "trade_date": day})
    assert quality_blocks(identity, "dwd_tradability_state", {"listing_id": identity["listing_id"], "trade_date": day})
    assert not quality_blocks(identity, "dwd_trading_calendar", {"venue_id": "other", "trade_date": day})
    assert not quality_blocks(identity, "dwd_tradability_state", {"listing_id": "other", "trade_date": day})
    assert not quality_blocks(identity, "dwd_tradability_state", {"listing_id": identity["listing_id"], "trade_date": "2021-01-01"})
    assert not quality_blocks(identity, "dwd_security_bar", {"listing_id": identity["listing_id"], "trade_date": day, "interval_size": "1m"})


def test_quality_timestamp_boundary_and_other_issuer_same_session():
    identity, _, _ = sample()
    first = identity["sessions"][0]
    local_midnight = datetime.fromisoformat(first["date"]+"T00:00:00+08:00")
    utc_time = local_midnight.astimezone(timezone.utc)
    assert quality_blocks(identity, "dwd_tradability_state", {
        "listing_id": identity["listing_id"], "observation_start": utc_time.isoformat()}, utc_time)
    assert not quality_blocks(identity, "dwd_tradability_state", {
        "security_id": "OTHER", "calendar_session_id": first["session_id"]})
