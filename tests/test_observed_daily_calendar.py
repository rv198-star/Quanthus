import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.observed_daily_calendar import validate


def observation(root: Path, day="2026-10-09"):
    root.mkdir()
    raw = json.dumps(
        {"error_code": "0", "fields": ["calendar_date", "is_trading_day"], "rows": [[day, "0"]]}
    ).encode()
    receipt = {
        "raw_base64": base64.b64encode(raw).decode(),
        "raw_bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "policy_id": "p10-acquisition-prevention-v1",
        "warehouse_observed_at": day + "T09:30:00+00:00",
    }
    payload = gzip.compress(json.dumps(receipt).encode())
    (root / "raw.json.gz").write_bytes(payload)
    body = {
        "kind": "cn02_actual_provider_calendar_v1",
        "observed_at": day + "T09:30:01+00:00",
        "dates": {day: False},
        "start": day,
        "end": day,
        "original_files": {"raw.json.gz": "sha256:" + hashlib.sha256(payload).hexdigest()},
    }
    (root / "calendar.json").write_text(json.dumps(body))
    return root / "calendar.json"


def test_calendar_replay_uses_recorded_raw_and_real_observation_clock(tmp_path):
    p = observation(tmp_path / "calendar")
    body, when = validate(p.parent, day="2026-10-09")
    assert body["dates"]["2026-10-09"] is False
    assert when.isoformat() == "2026-10-09T09:30:01+00:00"


def test_calendar_replay_rejects_summary_that_disagrees_with_raw(tmp_path):
    p = observation(tmp_path / "calendar")
    d = json.loads(p.read_bytes())
    d["dates"]["2026-10-09"] = True
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="SUMMARY_DIFFERS_FROM_RAW"):
        validate(p.parent, day="2026-10-09")
