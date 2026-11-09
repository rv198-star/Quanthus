from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.daily_base_alignment import compare_bars


def write(root: Path, *, close: int):
    (root / "payload").mkdir(parents=True)
    for interval in ("1d", "5m"):
        pq.write_table(
            pa.table(
                {
                    "listing_id": ["id1"],
                    "close": [close],
                    "volume": [100],
                    "knowledge_from": ["actual-source-time"],
                }
            ),
            root / "payload" / (interval + ".parquet"),
        )


def test_rebase_business_values_are_independently_compared(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    write(old, close=10)
    write(new, close=10)
    result = compare_bars(old, new)
    assert result["1d"] == {"business_rows": 1, "differences": 0}
    assert result["5m"] == {"business_rows": 1, "differences": 0}


def test_rebase_audit_refuses_changed_business_values(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    write(old, close=10)
    write(new, close=11)
    with pytest.raises(ValueError, match="BUSINESS_VALUES_CHANGED"):
        compare_bars(old, new)


def test_closed_only_runtime_can_align_again_without_market_index(tmp_path, monkeypatch):
    import json
    from datetime import date

    from quanthus.pipelines.securities_warehouse import daily_base_alignment as align
    from quanthus.pipelines.securities_warehouse import observed_daily_calendar as calendar
    from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha

    base, runtime = tmp_path / "base", tmp_path / "runtime"
    base.mkdir()
    runtime.mkdir()
    (base / "manifest.json").write_text("{}")
    delivered = runtime / "deliveries/2026-11-07"
    reference = delivered / "sources/calendar_reference"
    events = delivered / "sources/calendar_daily_events"
    for source in [reference, events]:
        source.mkdir(parents=True)
        (source / "release.json").write_text("{}")
    (delivered / "manifest.json").write_text("{}")
    captured = runtime / "2026-11-07-aligned/calendar"
    captured.mkdir(parents=True)
    (captured / "calendar.json").write_text("{}")
    entry = {
        "status": "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY",
        "event_source": str(events),
        "event_manifest_sha256": file_sha(events / "release.json"),
        "calendar_sha256": file_sha(captured / "calendar.json"),
        "standard_delivery": str(delivered),
        "standard_delivery_manifest_sha256": file_sha(delivered / "manifest.json"),
    }
    (runtime / "DATES.json").write_text(json.dumps({"dates": {"2026-11-07": entry}}))
    retained = {"path": str(reference), "manifest_sha256": file_sha(reference / "release.json")}
    (runtime / "RUNTIME-READY.json").write_text(json.dumps({"calendar_reference": retained}))
    monkeypatch.setattr(
        align,
        "governance_config",
        lambda *a, **kw: {
            "through_date": "2026-11-06",
            "layers": {"dwd": {"manifest_sha256": "new-base"}},
        },
    )
    monkeypatch.setattr(align, "read_native_parent_references", lambda *a, **kw: None)
    monkeypatch.setattr(calendar, "validate", lambda *a, **kw: None)
    monkeypatch.setattr(
        align,
        "inspect_delivery",
        lambda *a, **kw: {
            "market_date": "2026-11-07",
            "sources": {
                "calendar_reference": {
                    "path": "sources/calendar_reference/release.json",
                    "manifest_sha256": retained["manifest_sha256"],
                }
            },
        },
    )
    plan = align.prepare(base, runtime, date(2026, 11, 7), "a" * 40)
    assert plan["entries"][0]["sources"]["calendar_reference"] == str(reference)
    assert plan["calendar_reference"] == retained
