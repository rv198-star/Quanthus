from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse import base231_joint_scope as adapter


def member(day="2026-09-30"):
    return {"path": "payload/security.parquet", "sha256": "sha256:" + "a" * 64,
            "source_business_date": day}


def stock(code="000001"):
    return {"security_id": code, "stock_code": code, "exchange": "SZSE", "name": "合成公司",
            "list_date": date(2020, 1, 1), "delist_date": None, "status": "listed",
            "bs_code": "sz." + code, "as_of": date(2026, 7, 25),
            "source": "original.provider",
            "built_at": datetime(2026, 7, 25, tzinfo=timezone.utc)}


def test_release_date_never_rolls_snapshot_lifecycle_or_provider_clock_forward():
    rows, proof = adapter.lifecycle_observations([stock()], member())
    assert rows[0]["as_of"] == date(2026, 7, 25)
    assert rows[0]["provider_acquired_at"] is None
    assert rows[0]["built_at"] == stock()["built_at"]
    assert not proof["all_market_denominator_qualified"]
    assert not proof["historical_PIT_qualified"]


@pytest.mark.parametrize("rows", [
    [stock(), stock()],
    [{**stock(), "list_date": date(2026, 8, 1), "delist_date": date(2026, 7, 1)}],
    [{**stock(), "as_of": date(2026, 10, 1)}],
    [{**stock(), "exchange": "UNKNOWN"}],
    [{**stock(), "status": "UNKNOWN"}],
])
def test_invalid_lifecycle_atoms_fail_without_fabricated_coverage(rows):
    with pytest.raises(RuntimeError, match="LIFECYCLE"):
        adapter.lifecycle_observations(rows, member())


def minute():
    return {"market_date": "2026-09-24", "bar_size": "1m", "scope_mode": "full_universe",
            "session_policy_version": "cn-a-share-trading-session.v1", "coverage_ok": True,
            "row_count": 240, "collector": {"provider": "quanthus.providers.minute_tdx",
            "api": "mootdx/tdx.incr", "fetch_mode": "incr", "exit_code": 0}}


def test_ready_and_full_universe_do_not_qualify_minute_basis_or_historical_code():
    proof = adapter.minute_evidence(minute(), member("2026-09-24"))
    assert proof["state"] == "HELD_ORIGINAL_MINUTE_SEMANTIC_EVIDENCE"
    assert "original_provider_adjustment_mode" in proof["missing"]
    assert "original_deployed_collector_code_identity" in proof["missing"]
    assert proof["consumer_admission"] == "NOT_GRANTED"


def test_provider_fetch_mode_is_not_adjustment_mode_and_is_not_ignored():
    raw = minute()
    raw["collector"]["fetch_mode"] = "unrecognized"
    assert "unqualified_fetch_mode" in adapter.minute_evidence(
        raw, member("2026-09-24"))["missing"]


def test_provider_mode_string_alone_cannot_qualify_intraday():
    raw = minute()
    raw.update(provider_adjustment_mode="none", deployed_collector_sha256="sha256:" + "b" * 64)
    assert adapter.minute_evidence(raw, member("2026-09-24"))["state"].startswith("HELD")


@pytest.mark.parametrize("update", [{"market_date": "2026-10-01"}, {"bar_size": "1h"}])
def test_minute_scope_mismatch_fails_closed(update):
    raw = {**minute(), **update}
    with pytest.raises(RuntimeError, match="MINUTE"):
        adapter.minute_evidence(raw, member("2026-09-24"))
