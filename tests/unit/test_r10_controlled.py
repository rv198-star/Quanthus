import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from quanthus.backtest.capital_clock import CapitalReplayClock, bind_capital_action_context
from quanthus.backtest.snapshot_clock import SnapshotReplayClock
from quanthus.r10_prepare import source_select
from quanthus.r10_replay import NativePanel


def clock(day=date(2021, 6, 2), **changes):
    source = "sha256:" + "1" * 64
    identity = {
        "native_content_digest": source,
        "purpose_sha256": "sha256:" + "2" * 64,
        "consumer_closure_sha256": "sha256:" + "2" * 64,
        "native_knowledge_cutoff": "2026-11-11T20:19:54+00:00",
        "purpose_qualified_at": "2026-11-14T08:07:52+00:00",
    }
    identity.update(changes)
    return CapitalReplayClock(
        day,
        datetime(2026, 11, 11, 20, 19, 54, tzinfo=timezone.utc),
        source,
        "RESEARCH-R10-001",
        "RESEARCH-R10-001-T1",
        "A1",
        json.dumps(identity, sort_keys=True, separators=(",", ":")),
    )


def test_controlled_clock_binds_actual_source_and_simulated_run():
    c = clock()
    assert c.to_dict()["schema_version"] == "quanthus.r10-controlled-clock.v1"
    assert c.simulated_close.year == 2021 and c.knowledge_cutoff.year == 2026
    assert CapitalReplayClock.from_json(c.to_json()) == c
    c.validate_run("RESEARCH-R10-001-T1-A1-C0")
    with pytest.raises(ValueError):
        c.validate_run("RESEARCH-R10-001-T2-A1-C0")
    bind_capital_action_context(clock(date(2021, 6, 3)).to_json(), c.to_json())
    with pytest.raises(ValueError):
        bind_capital_action_context(c.to_json(), None)
    with pytest.raises(ValueError):
        clock(native_content_digest="sha256:" + "3" * 64)
    with pytest.raises(ValueError):
        clock(native_knowledge_cutoff="2026-11-10T20:19:54+00:00")
    with pytest.raises(ValueError):
        clock(consumer_closure_sha256="sha256:" + "3" * 64)
    with pytest.raises(ValueError):
        SnapshotReplayClock.from_json(c.to_json())


def sample(company, listing, width):
    return {
        "available": True,
        "identity": {"company_id": company, "listing_id": listing, "anchor": "2021-05-31"},
        "primary_facts": {
            k: {"value": v}
            for k, v in {
                "directional_efficiency": ".1",
                "width_change": "-.3",
                "range_width": width,
            }.items()
        },
    }


def test_selection_company_unique_and_source_order_deterministic():
    pool = [
        sample("C1", "L1", ".1"),
        sample("C1", "L2", ".11"),
        sample("C2", "L3", ".2"),
        sample("C3", "L4", ".3"),
    ]
    a = source_select(pool, {"narrow": 2}, set())
    assert [r["source_row"]["identity"]["company_id"] for r in a] == ["C1", "C2"]
    assert source_select(list(reversed(pool)), {"narrow": 2}, set()) == a
    with pytest.raises(ValueError, match="SOURCE_QUOTA"):
        source_select(pool, {"narrow": 4}, set())


@pytest.mark.parametrize("close", [None, 0, float("nan"), float("inf")])
def test_missing_or_invalid_close_is_unavailable(monkeypatch, close):
    monkeypatch.setattr("quanthus.r10_replay.rows", lambda *args: [{"close_price": close}])
    panel = object.__new__(NativePanel)
    panel.native, panel.cache = SimpleNamespace(db=None), {}
    assert panel.feature(date(2021, 6, 2), "L1") is None
