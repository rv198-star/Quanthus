from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_status import (
    project_status_day,
    resolve_status_listing,
    status_observation,
)


def case():
    day = date(2026, 8, 3)
    earlier = datetime(2026, 7, 31, tzinfo=timezone.utc)
    observed = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    raw = {
        "stock_code": "600001",
        "security_id": "supplier-id",
        "date": str(day),
        "market_date": str(day),
        "tradestatus": "1",
        "isST": "0",
        "source": "synthetic-baostock",
        "acquired_at": "2026-08-03T19:42:04+08:00",
        "built_at": "1999-01-01T00:00:00Z",
    }
    listing = {
        "listing_code": "600001",
        "listing_id": "listing-one",
        "venue_id": "venue-one",
        "knowledge_from": earlier,
        "knowledge_to": None,
        "effective_from": earlier,
        "effective_to": None,
    }
    calendar = {
        "venue_id": "venue-one",
        "calendar_session_id": "session-one",
        "trade_date": day,
        "session_type": "day",
        "is_open": True,
        "knowledge_from": earlier,
        "knowledge_to": None,
    }
    kwargs = {
        "market_date": day,
        "listing": listing,
        "calendar": calendar,
        "observed_at": observed,
        "source_sha256": "sha256:" + "a" * 64,
        "revision": "retained-synthetic",
        "input_revision_digest": "sha256:" + "b" * 64,
    }
    return raw, kwargs


@pytest.mark.parametrize(
    "flags,reason",
    [(("1", "0"), "NORMAL"), (("1", "1"), "ST_TRADABLE"), (("0", "0"), "SOURCE_NOT_TRADABLE")],
)
def test_source_flags_new_clock_unknown_limits_and_physical_exactness(tmp_path, flags, reason):
    from pathlib import Path

    raw, args = case()
    before = deepcopy(raw)
    raw["tradestatus"], raw["isST"] = flags
    out, proof = status_observation(raw, **args)
    assert out["reason_code"] == reason and out["knowledge_from"] == args["observed_at"]
    assert out["is_tradable"] is out["can_buy"] is out["can_sell"] is (flags[0] == "1")
    assert out["upper_price"] is out["lower_price"] is proof["provider_acquisition"] is None
    assert proof["acquired_at"] == before["acquired_at"]
    assert out["observation_start"] == datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
    assert out["observation_end"] == datetime(2026, 8, 3, 16, tzinfo=timezone.utc)
    c, m, e = load_contract(Path("config/pipelines"))
    schema = schemas(m, e)["dwd_tradability_state"]
    typed = pa.Table.from_pylist([out], schema=schema)
    p = tmp_path / "one.parquet"
    pq.write_table(typed, p)
    assert pq.ParquetFile(p).read().equals(typed)
    assert proof["historical_PIT_admission"] is False and proof["sealed_rows_mutated"] == 0


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("calendar-date", "DATE_CODE_VENUE"),
        ("calendar-venue", "DATE_CODE_VENUE"),
        ("listing-code", "DATE_CODE_VENUE"),
        ("calendar-session", "DATE_CODE_VENUE"),
        ("future-parent", "PARENT_KNOWLEDGE"),
        ("closed-parent", "PARENT_KNOWLEDGE"),
        ("future-acquisition", "ACQUISITION_AFTER"),
        ("listing-effective", "LISTING_BUSINESS"),
        ("closed-market", "SOURCE_CALENDAR_FLAG"),
        ("unknown-open", "OPEN_FLAG_UNKNOWN"),
        ("unknown-tradestatus", "SOURCE_FLAGS"),
        ("unknown-ST", "SOURCE_FLAGS"),
        ("raw-date", "RAW_DATE"),
        ("september", "BUSINESS_SCOPE"),
        ("source-SHA", "SOURCE_IDENTITY"),
        ("unknown-source", "RAW_SOURCE"),
        ("input-closure", "INPUT_CLOSURE"),
    ],
)
def test_wrong_dates_venues_clocks_and_unknown_flags_fail_without_mutating_input(defect, reason):
    raw, args = case()
    if defect == "calendar-date":
        args["calendar"]["trade_date"] = date(2026, 7, 31)
    elif defect == "calendar-venue":
        args["calendar"]["venue_id"] = "other"
    elif defect == "listing-code":
        args["listing"]["listing_code"] = "600002"
    elif defect == "calendar-session":
        args["calendar"]["session_type"] = "night"
    elif defect == "future-parent":
        args["calendar"]["knowledge_from"] = args["observed_at"] + timedelta(seconds=1)
    elif defect == "closed-parent":
        args["listing"]["knowledge_to"] = args["observed_at"]
    elif defect == "future-acquisition":
        raw["acquired_at"] = "2026-10-07T00:00:00+08:00"
    elif defect == "listing-effective":
        args["listing"]["effective_from"] = args["observed_at"]
    elif defect == "closed-market":
        args["calendar"]["is_open"] = False
    elif defect == "unknown-open":
        args["calendar"]["is_open"] = None
    elif defect == "unknown-tradestatus":
        raw["tradestatus"] = ""
    elif defect == "unknown-ST":
        raw["isST"] = None
    elif defect == "raw-date":
        raw["date"] = "2026-07-31"
    elif defect == "september":
        args["market_date"] = date(2026, 9, 1)
    elif defect == "source-SHA":
        args["source_sha256"] = "unknown"
    elif defect == "unknown-source":
        raw["source"] = ""
    else:
        args["input_revision_digest"] = "unknown"
    before = deepcopy((raw, args))
    with pytest.raises(BoundedError, match=reason):
        status_observation(raw, **args)
    assert (raw, args) == before


def test_exact_dated_identity_never_falls_back_to_alias_or_expired_version():
    raw, args = case()
    valid = args["listing"]
    expired = dict(valid, knowledge_to=args["observed_at"])
    assert (
        resolve_status_listing(
            raw, [expired, valid], day=args["market_date"], observed_at=args["observed_at"]
        )
        is valid
    )
    for listings in [[expired], [valid, dict(valid)]]:
        with pytest.raises(BoundedError, match="EXACT_DATED_LISTING_NOT_UNIQUE"):
            resolve_status_listing(
                raw, listings, day=args["market_date"], observed_at=args["observed_at"]
            )
    raw["stock_code"] = "600002"
    with pytest.raises(BoundedError, match="EXACT_DATED_LISTING_NOT_UNIQUE"):
        resolve_status_listing(
            raw, [valid], day=args["market_date"], observed_at=args["observed_at"]
        )


def test_invalid_row_gets_explicit_unqualified_disposition_while_independent_row_survives():
    raw, args = case()
    invalid = dict(raw, stock_code="600002")
    before = deepcopy([raw, invalid])
    out, proof, held = project_status_day(
        [raw, invalid],
        day=args["market_date"],
        listings=[args["listing"]],
        calendars=[args["calendar"]],
        observed_at=args["observed_at"],
        raw_sha256=args["source_sha256"],
        revision=args["revision"],
        input_revision_digest=args["input_revision_digest"],
    )
    assert len(out) == len(proof) == len(held) == 1
    assert held[0]["reason"] == "STATUS_EXACT_DATED_LISTING_NOT_UNIQUE"
    assert held[0]["disposition"] == "UNQUALIFIED_NO_STATE_OUTPUT_NO_INFERRED_STATUS"
    assert [raw, invalid] == before
    with pytest.raises(BoundedError, match="DUPLICATE_OR_UNKNOWN_CODE_DAY"):
        project_status_day(
            [raw, raw],
            day=args["market_date"],
            listings=[args["listing"]],
            calendars=[args["calendar"]],
            observed_at=args["observed_at"],
            raw_sha256=args["source_sha256"],
            revision=args["revision"],
            input_revision_digest=args["input_revision_digest"],
        )
