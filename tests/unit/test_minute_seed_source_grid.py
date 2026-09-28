"""A recovery seed must not bypass rejection of unobserved minute bars."""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from quanthus.pipelines import lane_runner


@pytest.mark.parametrize("inferred", [None, [], ["202609241342"]])
def test_seed_without_current_source_identity_is_withheld_before_copy(
    tmp_path, monkeypatch, inferred
):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "000404_1m.parquet").touch()
    (seed / "status.json").write_text(
        json.dumps(
            {
                "state": "done",
                "freq": "1m",
                "total": 1,
                "done": 1,
                "error": 0,
            }
        )
    )
    (seed / "checkpoint.json").write_text(
        json.dumps(
            {
                "symbols": {
                    "000404": {
                        "status": "done",
                        "rows_file": 240,
                        "inferred_zero_volume_minutes": inferred,
                    }
                }
            }
        )
    )
    monkeypatch.setenv("HERMES_ODS_MINUTE_SEED_HOME", str(seed))
    monkeypatch.delenv("HERMES_ODS_MINUTE_1M_RECOVERY_POLICY", raising=False)
    monkeypatch.setattr(lane_runner, "_minute_collector_fetch_mode", lambda **_: ("incr", 1))
    raw = tmp_path / "raw"
    kwargs = dict(
        paths=SimpleNamespace(home=tmp_path / "home"),
        market_date=date(2026, 9, 24),
        bar_size="1m",
        symbols=["000404"],
        raw_home=raw,
    )
    before = {p.name: p.read_bytes() for p in seed.iterdir()}
    with pytest.raises(lane_runner.LaneRunError, match="1m seed reuse withheld"):
        lane_runner._run_minute_collector(**kwargs)
    assert not raw.exists()
    assert before == {p.name: p.read_bytes() for p in seed.iterdir()}


def test_5m_legacy_seed_behavior_is_unchanged(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "000404_5m.parquet").write_bytes(b"synthetic-copy-only-not-qualified")
    (seed / "status.json").write_text(
        json.dumps(
            {
                "state": "done",
                "freq": "5m",
                "total": 1,
                "done": 1,
                "error": 0,
            }
        )
    )
    (seed / "checkpoint.json").write_text(
        json.dumps(
            {
                "symbols": {
                    "000404": {
                        "status": "done",
                        "rows_file": 48,
                    }
                }
            }
        )
    )
    monkeypatch.setenv("HERMES_ODS_MINUTE_SEED_HOME", str(seed))
    monkeypatch.setattr(lane_runner, "_minute_collector_fetch_mode", lambda **_: ("incr", 1))
    raw = tmp_path / "raw"
    result = lane_runner._run_minute_collector(
        paths=SimpleNamespace(home=tmp_path / "home"),
        market_date=date(2026, 9, 24),
        bar_size="5m",
        symbols=["000404"],
        raw_home=raw,
    )
    assert result["isolation"] == "attempt-local-seed-copy"
    assert (raw / "000404_5m.parquet").read_bytes() == b"synthetic-copy-only-not-qualified"
