"""Real September adapters retain August gates and exact observation evidence."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_daily_bar import bar_observation
from quanthus.pipelines.securities_warehouse.base23_core import (
    calendar_union,
    project_calendar,
    project_day,
    revision_union,
)
from tests.unit.test_base22_daily_bar import bar_case
from tests.unit.test_base22_status import case


def september_case():
    raw, args = bar_case()
    day = date(2026, 9, 3)
    raw.update(date=str(day), market_date=str(day), acquired_at="2026-09-03T19:30:00+08:00")
    args["market_date"] = day
    args["calendar"]["trade_date"] = day
    args["observed_at"] = datetime(2026, 10, 9, tzinfo=timezone.utc)
    status, _ = case()
    status.update(
        date=str(day),
        market_date=str(day),
        acquired_at=raw["acquired_at"],
        source="baostock.query_history_k_data_plus",
    )
    return raw, status, args


def run_day(raw, status, args):
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas

    _, model, extension = load_contract(Path("config/pipelines"))
    return project_day(
        {"daily_ohlcv_none_delta": [raw], "daily_status_observation_delta": [status]},
        day=args["market_date"],
        listings=[args["listing"]],
        calendars=[args["calendar"]],
        observed_at=args["observed_at"],
        sources={
            name: (args["source_sha256"], args["revision"])
            for name in ["daily_ohlcv_none_delta", "daily_status_observation_delta"]
        },
        input_digest=digest(["synthetic-complete-input"]),
        schemas=schemas(model, extension),
    )


def test_september_exact_values_clocks_roundtrip_and_august_still_rejects(tmp_path):
    raw, status, args = september_case()
    before = deepcopy((raw, status, args))
    with pytest.raises(BoundedError, match="BUSINESS_SCOPE"):
        bar_observation(raw, **args)
    outputs, proof, held = run_day(raw, status, args)
    assert not held and len(proof) == 2
    for name, rows in outputs.items():
        p = tmp_path / (name + ".parquet")
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, p)
        assert pq.read_table(p).equals(table)
        assert rows[0]["knowledge_from"] == args["observed_at"]
        assert rows[0]["observation_start"] == datetime(2026, 9, 2, 16, tzinfo=timezone.utc)
    assert proof[0]["provider_acquisition"] is None
    assert (raw, status, args) == before


@pytest.mark.parametrize("fault", ["date", "venue", "closed", "clock", "basis", "precision"])
def test_affected_observations_held_without_inference_or_raw_mutation(fault):
    raw, status, args = september_case()
    if fault == "date":
        args["calendar"]["trade_date"] += timedelta(days=1)
    elif fault == "venue":
        args["calendar"]["venue_id"] = "wrong"
    elif fault == "closed":
        args["calendar"]["is_open"] = False
    elif fault == "clock":
        raw["acquired_at"] = "2027-01-01T00:00:00+00:00"
    elif fault == "basis":
        raw["adj_mode"] = "hfq"
    else:
        from decimal import Decimal

        raw["close"] = Decimal("1.12345678901")
    before = deepcopy((raw, status, args))
    outputs, _, held = run_day(raw, status, args)
    assert held and all("price" not in h and "close" not in h for h in held)
    if fault in {"clock", "basis", "precision"}:
        assert not outputs["dwd_security_bar"] and outputs["dwd_tradability_state"]
    elif fault == "closed":
        assert not outputs["dwd_tradability_state"]
    else:
        assert not any(outputs.values())
    assert (raw, status, args) == before


@pytest.mark.parametrize("day", [date(2026, 8, 31), date(2026, 10, 1)])
def test_only_fixed_september_business_window(day):
    raw, status, args = september_case()
    args["market_date"] = day
    with pytest.raises(BoundedError, match="SEPTEMBER"):
        run_day(raw, status, args)


def test_duplicate_code_day_fail_closed():
    raw, status, args = september_case()
    with pytest.raises(BoundedError, match="DUPLICATE"):
        revision_union([("sha256:" + "1" * 64, "r1", [raw, raw])])


def test_unrepresentable_scalar_holds_only_its_family_without_exception_value():
    from decimal import Decimal

    raw, status, args = september_case()
    raw["close"] = Decimal("1" * 39)
    out, _, held = run_day(raw, status, args)
    assert not out["dwd_security_bar"] and out["dwd_tradability_state"]
    assert held[0]["reason"] == "SEPTEMBER_SCHEMA_PRECISION_OR_TYPE_UNSUPPORTED"
    assert "1" * 39 not in str(held)


def test_revisions_keep_true_acquisition_and_hold_conflicting_values():
    raw, _, _ = september_case()
    later = {**raw, "acquired_at": "2026-09-03T20:30:00+08:00"}
    source1 = ("sha256:" + "1" * 64, "r1", [raw])
    source2 = ("sha256:" + "2" * 64, "r2", [later])
    selected, held, proof = revision_union([source2, source1])
    assert len(selected) == 1 and not held and proof["identical_revision_overlap"] == 1
    assert selected[0]["raw"]["acquired_at"] == raw["acquired_at"]
    assert len(selected[0]["observations"]) == 2
    assert revision_union([source1, source2]) == (selected, held, proof)
    changed = {**later, "amount": 123.25}
    selected, held, _ = revision_union([source1, (source2[0], source2[1], [changed])])
    assert not selected and len(held) == 1 and "amount" not in held[0]


def calendar_case():
    built = datetime(2026, 9, 23, tzinfo=timezone.utc)
    raw = [
        {
            "exchange": ex,
            "trade_date": date(2026, 9, 1) + timedelta(days=n),
            "session": "day",
            "is_open": int(n % 7 < 5),
            "source": "baostock.query_trade_dates",
            "built_at": built,
        }
        for ex in ["SSE", "SZSE", "BSE"]
        for n in range(23)
    ]
    venues = [
        {"mic_code": mic, "venue_id": mic, "knowledge_from": built, "knowledge_to": None}
        for mic in ["XSHG", "XSHE", "BJSE"]
    ]
    return raw, venues


def test_calendar_available_69_keys_never_claims_monthly_90_or_exchange_pit():
    raw, venues = calendar_case()
    union, proof = calendar_union([("sha256:" + "1" * 64, raw)])
    assert len(union) == 69 and proof["missing_month_keys"] == 21
    output, summary = project_calendar(
        union,
        venues,
        observed_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        source_sha256="sha256:" + "2" * 64,
        revision="new-observation",
    )
    assert len(output) == 69 and summary["full_month_coverage"] is False
    assert all(r["open_at"] is r["close_at"] is None for r in output)
    assert summary["independent_exchange_original_responses"] == "NOT_ASSERTED"


@pytest.mark.parametrize("fault", ["flag", "duplicate", "sha", "venue"])
def test_calendar_conflicts_and_unknown_sources_do_not_materialize(fault):
    raw, venues = calendar_case()
    sha = "sha256:" + "1" * 64
    sources = [(sha, raw)]
    if fault == "flag":
        sources.append(("sha256:" + "2" * 64, [{**raw[0], "is_open": 1 - raw[0]["is_open"]}]))
    elif fault == "duplicate":
        sources = [(sha, raw + [raw[0]])]
    elif fault == "sha":
        sources = [("bad", raw)]
    else:
        venues.pop()
    with pytest.raises(BoundedError):
        union, _ = calendar_union(sources)
        project_calendar(
            union,
            venues,
            observed_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
            source_sha256=sha,
            revision="synthetic",
        )
