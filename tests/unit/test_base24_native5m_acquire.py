"""Checkpointed Base 2.4 acquisition seals only complete exact partitions."""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
    Native5mAcquireError,
    run_acquisition,
)
from quanthus.providers.native_5m_exact import SHANGHAI, ExactBar


def _plan(windows=2):
    rows = []
    for index in range(windows):
        code = f"sz.{index + 1:06d}"
        start = "2020-01-02"
        end = "2020-01-03"
        import hashlib

        identity = hashlib.sha256(f"SZSE\t{code[3:]}\t{start}\t{end}".encode()).hexdigest()
        rows.append(
            {
                "partition_id": identity,
                "code": code,
                "venue": "SZSE",
                "start_date": start,
                "end_date": end,
                "required_dates": ["2020-01-02", "2020-01-03"],
                "expected_session_dates": ["2020-01-02", "2020-01-03"],
                "required_security_days": 2,
                "expected_source_days": 2,
                "maximum_rows": 96,
            }
        )
    return json.dumps(
        {
            "contract": "p10-base24-native5m-missing-window-plan-v1",
            "business_through": "2026-09-30",
            "frequency": "5",
            "adjustflag": "3",
            "source": "baostock.query_history_k_data_plus",
            "required_security_days": windows * 2,
            "missing_security_days": windows * 2,
            "windows": rows,
        },
        sort_keys=True,
    ).encode()


def _bars(code):
    values = ("1.00", "1.00", "1.00", "1.00", "0", "0.0")
    rows = []
    for day in (2, 3):
        morning = datetime(2020, 1, day, 9, 35, tzinfo=SHANGHAI)
        afternoon = datetime(2020, 1, day, 13, 5, tzinfo=SHANGHAI)
        rows.extend(ExactBar(code, morning + timedelta(minutes=5 * i), values) for i in range(24))
        rows.extend(ExactBar(code, afternoon + timedelta(minutes=5 * i), values) for i in range(24))
    assert all(value.is_finite() for row in rows for value in row.values)
    assert Decimal("0.0") in rows[0].values
    return tuple(rows)


def test_complete_partitions_seal_and_resume_without_fetch(tmp_path):
    calls = []

    def fetch(staging, request):
        calls.append(request.code)
        (staging / "wire").mkdir()
        (staging / "wire/source.wire").write_bytes(request.code.encode())
        return _bars(request.code), {"actual_requests": 1, "actual_bytes": 9}

    plan = _plan()
    first = run_acquisition(plan, tmp_path / "out", fetch, source_roots=(tmp_path / "source",))
    assert first["completed"] == 2
    assert first["reused"] == 0
    assert len(calls) == 2
    second = run_acquisition(
        plan,
        tmp_path / "out",
        lambda *_: pytest.fail("completed partitions must be reused"),
        source_roots=(tmp_path / "source",),
    )
    assert second["completed"] == 2
    assert second["reused"] == 2
    assert len(list((tmp_path / "out/partitions").glob("*/READY"))) == 2


def test_failure_retained_without_ready_and_other_partition_continues(tmp_path):
    def fetch(staging, request):
        if request.code.endswith("000001"):
            raise RuntimeError("bounded failure")
        (staging / "wire").mkdir()
        (staging / "wire/source.wire").write_bytes(b"ok")
        return _bars(request.code), {"actual_requests": 1, "actual_bytes": 2}

    result = run_acquisition(_plan(), tmp_path / "out", fetch, source_roots=())
    assert result["completed"] == 1
    assert result["failed"] == 1
    assert len(list((tmp_path / "out/failures").glob("*/FAILURE.json"))) == 1
    assert len(list((tmp_path / "out/partitions").glob("*/READY"))) == 1


@pytest.mark.parametrize("defect,reason", [("partial", "BUCKET"), ("plan", "PARTITION")])
def test_partial_day_or_changed_plan_fails_closed(tmp_path, defect, reason):
    plan = json.loads(_plan(1))
    if defect == "plan":
        plan["windows"][0]["end_date"] = "2020-01-04"

    def fetch(staging, request):
        (staging / "wire").mkdir()
        (staging / "wire/source.wire").write_bytes(b"source")
        return _bars(request.code)[:-1], {"actual_requests": 1, "actual_bytes": 6}

    if defect == "plan":
        with pytest.raises(Native5mAcquireError, match=reason):
            run_acquisition(json.dumps(plan).encode(), tmp_path / "out", fetch, source_roots=())
    else:
        result = run_acquisition(
            json.dumps(plan).encode(), tmp_path / "out", fetch, source_roots=()
        )
        assert result["failed"] == 1
        failure = next((tmp_path / "out/failures").glob("*/FAILURE.json"))
        assert reason in json.loads(failure.read_text())["reason_code"]


def test_source_overlap_and_symlink_output_rejected_before_creation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    before = list(source.iterdir())
    with pytest.raises(Native5mAcquireError, match="OVERLAP"):
        run_acquisition(_plan(1), source / "child", lambda *_: None, source_roots=(source,))
    assert list(source.iterdir()) == before
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(Native5mAcquireError, match="SYMLINK"):
        run_acquisition(_plan(1), link, lambda *_: None, source_roots=())


def test_corrupt_sealed_partition_blocks_resume(tmp_path):
    def fetch(staging, request):
        (staging / "wire").mkdir()
        (staging / "wire/source.wire").write_bytes(b"ok")
        return _bars(request.code), {"actual_requests": 1, "actual_bytes": 2}

    plan = _plan(1)
    run_acquisition(plan, tmp_path / "out", fetch, source_roots=())
    parquet = next((tmp_path / "out/partitions").glob("*/exact.parquet"))
    with parquet.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(Native5mAcquireError, match="SEALED_PARTITION_CHANGED"):
        run_acquisition(plan, tmp_path / "out", fetch, source_roots=())


def test_consistently_resealed_semantic_change_blocks_resume(tmp_path):
    def fetch(staging, request):
        (staging / "wire").mkdir()
        (staging / "wire/source.wire").write_bytes(b"ok")
        return _bars(request.code), {"actual_requests": 1, "actual_bytes": 2}

    plan = _plan(1)
    output = tmp_path / "out"
    run_acquisition(plan, output, fetch, source_roots=())
    partition = next((output / "partitions").iterdir())
    parquet = partition / "exact.parquet"
    table = pq.read_table(parquet)
    changed = table.set_column(
        table.schema.get_field_index("code"),
        "code",
        pa.array(["sz.999999"] * table.num_rows),
    )
    pq.write_table(changed, parquet, compression="zstd")
    receipt_path = partition / "RECEIPT.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files"]["exact.parquet"] = hashlib.sha256(parquet.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    (partition / "READY").write_text(hashlib.sha256(receipt_path.read_bytes()).hexdigest() + "\n")

    with pytest.raises(Native5mAcquireError, match="SEALED_PARTITION_CHANGED"):
        run_acquisition(plan, output, fetch, source_roots=())


def test_bounded_pilot_reuses_same_full_plan_checkpoint(tmp_path):
    calls = []

    def fetch(staging, request):
        calls.append(request.code)
        (staging / "wire").mkdir()
        (staging / "wire/source.wire").write_bytes(b"ok")
        return _bars(request.code), {"actual_requests": 1, "actual_bytes": 2}

    plan = _plan(2)
    pilot = run_acquisition(plan, tmp_path / "out", fetch, source_roots=(), max_new_partitions=1)
    assert pilot["status"] == "PARTIAL"
    assert pilot["stop_reason"] == "NEW_PARTITION_LIMIT"
    assert pilot["completed"] == pilot["actual_requests"] == 1
    final = run_acquisition(plan, tmp_path / "out", fetch, source_roots=())
    assert final["status"] == "COMPLETE"
    assert final["reused"] == 1
    assert final["actual_requests"] == 2
