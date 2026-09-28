"""A recovery seed must not bypass rejection of unobserved minute bars."""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from quanthus.pipelines import lane_runner


@pytest.mark.parametrize("inferred", [[], ["202609241342"]])
def test_seed_rejects_inferred_minutes_before_copy(tmp_path, monkeypatch, inferred):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "000404_1m.parquet").touch()
    (seed / "status.json").write_text(json.dumps({
        "state": "done", "freq": "1m", "total": 1, "done": 1, "error": 0,
    }))
    (seed / "checkpoint.json").write_text(json.dumps({"symbols": {"000404": {
        "status": "done", "rows_file": 240,
        "inferred_zero_volume_minutes": inferred,
    }}}))
    monkeypatch.setenv("HERMES_ODS_MINUTE_SEED_HOME", str(seed))
    monkeypatch.delenv("HERMES_ODS_MINUTE_1M_RECOVERY_POLICY", raising=False)
    monkeypatch.setattr(lane_runner, "_minute_collector_fetch_mode", lambda **_: ("incr", 1))
    raw = tmp_path / "raw"
    kwargs = dict(
        paths=SimpleNamespace(home=tmp_path / "home"), market_date=date(2026, 9, 24),
        bar_size="1m", symbols=["000404"], raw_home=raw,
    )
    if inferred:
        with pytest.raises(lane_runner.LaneRunError, match="invalid=.*000404"):
            lane_runner._run_minute_collector(**kwargs)
        assert not raw.exists()
    else:
        result = lane_runner._run_minute_collector(**kwargs)
        assert result["isolation"] == "attempt-local-seed-copy"
        assert (raw / "000404_1m.parquet").exists()
