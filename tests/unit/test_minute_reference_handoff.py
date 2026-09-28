"""Synthetic READY/lineage/PIT reference selection, not source-semantic acceptance."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from quanthus.pipelines import contracts, lane_runner
from quanthus.pipelines.contracts import publish_ods_release
from quanthus.pipelines.lane_runner import (
    LanePaths,
    LaneRunError,
    _sealed_minute_reference_artifacts,
)

DAY = date(2026, 9, 23)
CLOCK = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)
LINEAGE = {
    "schedule_contract_id": "synthetic-node",
    "schedule_contract_revision": "same-v1",
    "schedule_config_sha256": "a" * 64,
}


@pytest.fixture
def paths(tmp_path):
    return LanePaths(
        tmp_path / "home", tmp_path / "shadow", tmp_path / "calendar", tmp_path / "locks"
    )


def release(
    paths,
    tmp_path,
    monkeypatch,
    name,
    *,
    five=False,
    names=None,
    lineage=LINEAGE,
    created_at=CLOCK,
    knowledge_cutoff=CLOCK,
    day=DAY,
    prefix=None,
):
    # A valid release cannot be created before its input cutoff. A late input
    # therefore also makes the resulting generation unavailable at our cutoff.
    created_at = max(created_at, knowledge_cutoff)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return created_at.astimezone(tz or timezone.utc)

    monkeypatch.setattr(contracts, "datetime", FixedClock)
    root = tmp_path / ("source-" + name)
    root.mkdir()
    names = names or (
        ["intraday_bars"] if five else ["daily_ohlcv_none_delta", "daily_status_observation_delta"]
    )
    artifacts = []
    for artifact_name in names:
        source = root / (artifact_name + ".json")
        source.write_text(json.dumps({"synthetic_structure_only": artifact_name}))
        artifacts.append(
            {
                "name": artifact_name,
                "source_path": str(source),
                "relative_path": "artifacts/" + source.name,
                "source_revision": "synthetic-only",
                "knowledge_cutoff": knowledge_cutoff.isoformat(),
            }
        )
    spec = {
        "schema_version": 1,
        "release_id": (
            prefix or ("hermes-intraday-archive-5m-" if five else "hermes-market-daily-core-")
        )
        + name,
        "schedule": "daily",
        "market_date": day.isoformat(),
        "producer": "synthetic-only",
        "artifacts": artifacts,
        **lineage,
    }
    spec_path = root / "spec.json"
    spec_path.write_text(json.dumps(spec))
    release_root = paths.shadow_root / "releases" / "daily"
    if five:
        release_root /= "5m"
    output = release_root / DAY.isoformat() / name
    publish_ods_release(spec_path=spec_path, output=output)
    return output


def select(paths, *, five=False):
    return _sealed_minute_reference_artifacts(
        paths=paths, market_date=DAY, available_at=CLOCK + timedelta(hours=2), require_five=five
    )


def test_pair_is_frozen_from_one_verified_market_release(paths, tmp_path, monkeypatch):
    paired = release(paths, tmp_path, monkeypatch, "paired")
    release(
        paths,
        tmp_path,
        monkeypatch,
        "daily-only",
        names=["daily_ohlcv_none_delta"],
        created_at=CLOCK + timedelta(minutes=1),
    )
    release(
        paths,
        tmp_path,
        monkeypatch,
        "status-only",
        names=["daily_status_observation_delta"],
        created_at=CLOCK + timedelta(minutes=2),
    )
    refs = select(paths)
    assert set(refs) == {"sealed_daily", "sealed_status"}
    assert all(str(paired) == item[1]["release_dir"] for item in refs.values())
    assert len({item[1]["manifest_sha256"] for item in refs.values()}) == 1
    assert all(item[1]["artifact_sha256"].startswith("sha256:") for item in refs.values())


def test_explicit_five_requires_same_contract_and_keeps_paired_market(paths, tmp_path, monkeypatch):
    release(paths, tmp_path, monkeypatch, "market")
    five = release(paths, tmp_path, monkeypatch, "five", five=True)
    release(
        paths,
        tmp_path,
        monkeypatch,
        "newer-incompatible",
        five=True,
        lineage={**LINEAGE, "schedule_contract_revision": "other-v2"},
        created_at=CLOCK + timedelta(minutes=1),
    )
    refs = select(paths, five=True)
    assert refs["sealed_native_5m"][1]["release_dir"] == str(five)
    assert refs["sealed_native_5m"][1]["schedule_config_sha256"] == "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "fault",
    ["wrong_date", "future_creation", "future_cutoff", "legacy_lineage", "wrong_lane", "tamper"],
)
def test_ineligible_market_references_do_not_create_a_selection(
    paths, tmp_path, monkeypatch, fault
):
    kwargs = {}
    if fault == "wrong_date":
        kwargs["day"] = date(2026, 9, 22)
    elif fault == "future_creation":
        kwargs["created_at"] = CLOCK + timedelta(hours=3)
    elif fault == "future_cutoff":
        kwargs["knowledge_cutoff"] = CLOCK + timedelta(hours=3)
    elif fault == "legacy_lineage":
        kwargs["lineage"] = {}
    elif fault == "wrong_lane":
        kwargs["prefix"] = "hermes-calendar-daily-events-"
    published = release(paths, tmp_path, monkeypatch, "ineligible", **kwargs)
    if fault == "tamper":
        (published / "artifacts/daily_ohlcv_none_delta.json").write_text("changed bytes")
    with pytest.raises(LaneRunError, match="same-date READY market"):
        select(paths)


@pytest.mark.parametrize("fault", ["missing", "different_contract", "future_cutoff", "wrong_lane"])
def test_missing_or_ineligible_five_never_becomes_an_auction_reference(
    paths, tmp_path, monkeypatch, fault
):
    release(paths, tmp_path, monkeypatch, "market")
    if fault != "missing":
        kwargs = {}
        if fault == "different_contract":
            kwargs["lineage"] = {**LINEAGE, "schedule_contract_revision": "different"}
        elif fault == "future_cutoff":
            kwargs["knowledge_cutoff"] = CLOCK + timedelta(hours=3)
        else:
            kwargs["prefix"] = "hermes-intraday-archive-1m-"
        release(paths, tmp_path, monkeypatch, "five-ineligible", five=True, **kwargs)
    with pytest.raises(LaneRunError, match="native-5m.*same schedule contract"):
        select(paths, five=True)


@pytest.mark.parametrize("five", [False, True])
def test_latest_tie_is_ambiguous_not_a_directory_order_policy(paths, tmp_path, monkeypatch, five):
    release(paths, tmp_path, monkeypatch, "market")
    release(paths, tmp_path, monkeypatch, "tie-1", five=five)
    if five:
        release(paths, tmp_path, monkeypatch, "tie-2", five=True)
    with pytest.raises(LaneRunError, match="ambiguous"):
        select(paths, five=five)


def test_naive_cutoff_is_not_inferred_from_the_node_timezone(paths):
    with pytest.raises(LaneRunError, match="timezone-aware"):
        _sealed_minute_reference_artifacts(
            paths=paths,
            market_date=DAY,
            available_at=CLOCK.replace(tzinfo=None),
            require_five=False,
        )


def test_missing_references_stop_before_collector_and_attempt_creation(
    paths, tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_ODS_MINUTE_TENCENT_FALLBACK", "1")
    monkeypatch.setenv("HERMES_ODS_MINUTE_MODE", "incr")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no collector may launch")

    monkeypatch.setattr("subprocess.run", forbidden)
    home = tmp_path / "new-attempt"
    with pytest.raises(LaneRunError, match="same-date READY market"):
        lane_runner._run_minute_collector(
            paths=paths, market_date=DAY, bar_size="1m", symbols=["000001"], raw_home=home
        )
    assert not home.exists()
