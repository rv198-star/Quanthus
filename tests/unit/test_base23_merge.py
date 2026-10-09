from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_merge import (
    close_sparse_batch,
    prior_closure,
    scope_outputs,
)

T0 = datetime(2026, 8, 31, 19, 59, 59, 123456, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 9, 9, 35, 17, 876543, tzinfo=timezone.utc)


def table():
    return pa.table({"average_holding_exact": pa.array([
        Decimal("1234567890123456789012345678.1234567890123"),
        None, Decimal("0.0000000000000"),
    ], type=pa.decimal256(41, 13)),
        "knowledge_from": pa.array([T0]*3, type=pa.timestamp("us", tz="UTC")),
        "knowledge_to": pa.array([None]*3, type=pa.timestamp("us", tz="UTC")),
        "original": pa.array(["zero/null/exact", None, "晚到"]),
    })


def test_sparse_closure_preserves_decimal256_null_zero_clock_and_all_other_atoms():
    before = table()
    after = close_sparse_batch(before, row_offset=10, closures=[(10, T1), (12, T1)])
    assert before.column("knowledge_to").to_pylist() == [None]*3
    assert after.column("knowledge_to").to_pylist() == [T1, None, T1]
    for name in ("average_holding_exact", "original", "knowledge_from"):
        assert before.column(name).equals(after.column(name))
    assert close_sparse_batch(before, row_offset=10, closures=[]).equals(before)


@pytest.mark.parametrize("closures", [[(10, T1), (10, T1)], [(12, T1), (10, T1)],
                                      [(9, T1)], [(13, T1)], [(10, T0)]])
def test_sparse_invalid_positions_and_nonnew_time_fail(closures):
    with pytest.raises(BoundedError):
        close_sparse_batch(table(), row_offset=10, closures=closures)


def test_existing_closed_interval_is_immutable():
    assert prior_closure(T0, None, T1) == T1
    old_end = T0+timedelta(seconds=1)
    assert prior_closure(T0, old_end, T1) == old_end
    with pytest.raises(BoundedError, match="CLOSED_HISTORY_INTERVAL_CONFLICT"):
        prior_closure(T0, T1+timedelta(seconds=1), T1)
    with pytest.raises(BoundedError, match="NOT_STRICTLY_NEW"):
        prior_closure(T1, None, T0)
    t = table().set_column(2, "knowledge_to", pa.array([T1, None, None],
                         type=pa.timestamp("us", tz="UTC")))
    with pytest.raises(BoundedError, match="CLOSED_HISTORY_ROW_TARGETED"):
        close_sparse_batch(t, row_offset=0, closures=[(0, T1)])


def scope():
    outputs = {t: {"path": f"payload/{t}/all/part.parquet", "partition": "all",
                   "semantic_scan": {"rows": 3}} for t in
               ("dwd_security_valuation", "dwd_index_bar")}
    body = {"contract_id": "p10-base23-retained-daily-measures-v1", "READY": False,
            "complete_Base23": False, "consumer_admission": "NOT_GRANTED",
            "days": [{"date": "2026-09-01", "outputs": outputs}],
            "physical_members": {i["path"]: {"sha256": "sha256:"+"a"*64,
                                             "size_bytes": 100} for i in outputs.values()}}
    body["identity"] = digest(body)
    return body


def test_explicit_existing_daily_layout_and_exact_physical_members():
    body = scope()
    out = scope_outputs(body)
    assert set(out) == {"dwd_security_valuation", "dwd_index_bar"}
    assert out["dwd_index_bar"][0]["rows"] == 3


@pytest.mark.parametrize("damage", ["member", "table", "purpose", "count", "path"])
def test_repacked_unqualified_or_conflicting_scope_refused(damage):
    body = scope()
    out = body["days"][0]["outputs"]
    if damage == "member":
        body["physical_members"] = {}
    elif damage == "table":
        del out["dwd_index_bar"]
    elif damage == "purpose":
        body["consumer_admission"] = "GRANTED"
    elif damage == "count":
        out["dwd_index_bar"]["rows"] = 0
    else:
        out["dwd_index_bar"]["path"] = "payload/dwd_index_bar/../other.parquet"
    body["identity"] = digest({k: v for k, v in body.items() if k != "identity"})
    with pytest.raises(BoundedError):
        scope_outputs(body)
