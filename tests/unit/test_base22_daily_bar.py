from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_daily_bar import (
    bar_observation,
    project_bar_day,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from tests.unit.test_base22_status import case


def bar_case():
    raw, args = case()
    raw.update(
        adj_mode="none",
        adj_version="baostock.adjustflag=3.v1",
        source="baostock.query_history_k_data_plus.adjustflag=3",
        open=2.25,
        high=2.5,
        low=2.0,
        close=2.4,
        volume=0.0,
        amount=None,
    )
    args.pop("input_revision_digest")
    args["listing"]["listing_currency"] = "CNY"
    _, model, extension = load_contract(Path("config/pipelines"))
    args["schema"] = schemas(model, extension)["dwd_security_bar"]
    return raw, args


def test_retained_binary_decimal_clock_and_zero_null_survive_real_parquet(tmp_path):
    raw, args = bar_case()
    before = deepcopy((raw, args))
    out, proof = bar_observation(raw, **args)
    assert out["price_basis"] == "UNADJUSTED" and out["interval_size"] == "1d"
    assert out["volume"] == Decimal(0) and out["turnover"] is None
    assert out["observation_start"] == datetime(2026, 8, 2, 16, tzinfo=timezone.utc)
    assert out["knowledge_from"] == args["observed_at"]
    assert proof["acquired_at"] == raw["acquired_at"] and proof["provider_acquisition"] is None
    assert proof["numeric_classifications"]["ZERO"] == 1
    assert proof["numeric_classifications"]["NULL"] == 1
    assert proof["source_precision_recovered"] is False and proof["sealed_rows_mutated"] == 0
    expected = pa.Table.from_pylist([out], schema=args["schema"])
    p = tmp_path / "scope.parquet"
    pq.write_table(expected, p)
    assert pq.ParquetFile(p).read().equals(expected)
    assert (raw, args) == before


@pytest.mark.parametrize(
    "bad",
    [
        "basis",
        "provider",
        "adj_version",
        "date",
        "venue",
        "listing",
        "session",
        "parent_clock",
        "acquisition",
        "september",
        "SHA",
        "precision",
        "nan",
        "missing",
    ],
)
def test_unknown_basis_precision_dates_and_clocks_fail_without_input_mutation(bad):
    raw, args = bar_case()
    if bad == "basis":
        raw["adj_mode"] = "hfq"
    elif bad == "provider":
        raw["source"] = "unqualified"
    elif bad == "adj_version":
        raw["adj_version"] = "guessed"
    elif bad == "date":
        args["calendar"]["trade_date"] += timedelta(days=1)
    elif bad == "venue":
        args["calendar"]["venue_id"] = "another"
    elif bad == "listing":
        args["listing"]["listing_code"] = "600002"
    elif bad == "session":
        args["calendar"]["session_type"] = "unknown"
    elif bad == "parent_clock":
        args["listing"]["knowledge_from"] = args["observed_at"] + timedelta(days=1)
    elif bad == "acquisition":
        raw["acquired_at"] = "2026-10-08T00:00:00+08:00"
    elif bad == "september":
        args["market_date"] = date(2026, 9, 1)
    elif bad == "SHA":
        args["source_sha256"] = "sha256:" + "z" * 64
    elif bad == "precision":
        raw["close"] = Decimal("1.12345678901")
    elif bad == "nan":
        raw["close"] = float("nan")
    elif bad == "missing":
        raw["close"] = None
    before = deepcopy((raw, args))
    with pytest.raises((BoundedError, SourceAssessmentError)):
        bar_observation(raw, **args)
    assert raw.keys() == before[0].keys() and args == before[1]
    assert raw["acquired_at"] == before[0]["acquired_at"]


def test_bad_row_is_held_not_rounded_or_invented_and_duplicates_block():
    raw, args = bar_case()
    bad = dict(raw, stock_code="600002", close=Decimal("1.12345678901"))
    other = dict(args["listing"], listing_code="600002", listing_id="listing-two")
    kw = dict(
        day=args["market_date"],
        listings=[args["listing"], other],
        calendars=[args["calendar"]],
        observed_at=args["observed_at"],
        raw_sha256=args["source_sha256"],
        revision=args["revision"],
        schema=args["schema"],
    )
    out, proof, held = project_bar_day([raw, bad], **kw)
    assert len(out) == len(proof) == len(held) == 1
    assert held[0]["reason"] == "NUMERIC_OBSERVATION_PRECISION_UNSUPPORTED"
    assert "close" not in held[0] and "price" not in held[0]
    with pytest.raises(BoundedError, match="DUPLICATE"):
        project_bar_day([raw, raw], **kw)
