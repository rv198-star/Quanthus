"""Complete days in failed native-5m captures remain recoverable and auditable."""

import hashlib
import json
from datetime import datetime

import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import BUCKETS
from quanthus.pipelines.securities_warehouse.base24_native5m_salvage import (
    Native5mSalvageError,
    audit_salvage,
    build_salvage,
)
from quanthus.providers.native_5m_exact import SHANGHAI, ExactBar


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan():
    start, end, code = "2020-01-02", "2020-01-03", "sz.000001"
    identity = hashlib.sha256(f"SZSE\t000001\t{start}\t{end}".encode()).hexdigest()
    value = {
        "contract": "p10-base24-native5m-missing-window-plan-v1",
        "business_through": "2026-09-30",
        "frequency": "5",
        "adjustflag": "3",
        "source": "baostock.query_history_k_data_plus",
        "required_security_days": 2,
        "missing_security_days": 2,
        "windows": [
            {
                "partition_id": identity,
                "code": code,
                "venue": "SZSE",
                "start_date": start,
                "end_date": end,
                "required_dates": [start, end],
                "expected_session_dates": [start, end],
                "required_security_days": 2,
                "expected_source_days": 2,
                "maximum_rows": 96,
            }
        ],
    }
    return json.dumps(value, sort_keys=True).encode(), identity


def _bars():
    values = ("1.00", "1.00", "1.00", "1.00", "0", "0.0")
    rows = []
    for day in ("2020-01-02", "2020-01-03"):
        rows.extend(
            ExactBar(
                "sz.000001",
                datetime.fromisoformat(f"{day}T{bucket}").replace(tzinfo=SHANGHAI),
                values,
            )
            for bucket in BUCKETS
        )
    return tuple(rows[:-1])


def _source(tmp_path):
    plan, identity = _plan()
    plan_sha = hashlib.sha256(plan).hexdigest()
    acquisition = tmp_path / "acquisition"
    failure_root = acquisition / "failures" / f"{identity}-attempt"
    wire_root = failure_root / "wire"
    held_root = acquisition / "held" / identity
    wire_root.mkdir(parents=True)
    held_root.mkdir(parents=True)
    (acquisition / "partitions").mkdir()
    wire = wire_root / "response-0001.wire"
    wire.write_bytes(b"immutable original response")
    wire_receipt = {
        "contract": "p10-native-5m-exact-v1",
        "failure": None,
        "records": [
            {
                "path": wire.name,
                "received_bytes": wire.stat().st_size,
                "complete": True,
                "response_sha256": _sha(wire),
                "last_received_at": "2026-10-18T00:00:00+00:00",
            }
        ],
    }
    (wire_root / "wire-receipt.json").write_text(json.dumps(wire_receipt))
    failure = {
        "contract": "p10-base24-native5m-acquisition-v1",
        "partition_id": identity,
        "plan_sha256": plan_sha,
        "reason_code": "BUCKET_CENSUS_INCOMPLETE",
        "failed_at": "2026-10-18T00:00:01+00:00",
    }
    failure_path = failure_root / "FAILURE.json"
    failure_path.write_text(json.dumps(failure))
    hold = {
        "contract": "p10-base24-native5m-acquisition-v1",
        "disposition": "HELD_AFTER_BOUNDED_FAILURES",
        "partition_id": identity,
        "plan_sha256": plan_sha,
        "code": "sz.000001",
        "start_date": "2020-01-02",
        "end_date": "2020-01-03",
        "required_dates": ["2020-01-02", "2020-01-03"],
        "attempts": 1,
        "reason_counts": {"BUCKET_CENSUS_INCOMPLETE": 1},
        "failure_receipts": [
            {
                "path": failure_path.relative_to(acquisition).as_posix(),
                "sha256": _sha(failure_path),
            }
        ],
        "held_at": "2026-10-18T00:00:02+00:00",
    }
    hold_path = held_root / "HOLD.json"
    hold_path.write_text(json.dumps(hold))
    (held_root / "READY").write_text(_sha(hold_path) + "\n")
    checkpoint = {
        "contract": "p10-base24-native5m-acquisition-v1",
        "status": "COMPLETE_WITH_HELD",
        "plan_sha256": plan_sha,
    }
    checkpoint_path = acquisition / "CHECKPOINT.json"
    checkpoint_path.write_text(json.dumps(checkpoint))
    (acquisition / "READY").write_text(_sha(checkpoint_path) + "\n")
    return plan, identity, acquisition, failure_path


def _parser(raw, request, page, page_size):
    assert raw == b"immutable original response"
    assert request.code == "sz.000001"
    assert (page, page_size) == (1, 20_000)
    return _bars(), True


def _build(plan, acquisition, output, identity):
    return build_salvage(
        plan,
        acquisition,
        output,
        [identity],
        selection_evidence_sha256="a" * 64,
        code_revision="b" * 40,
        module_sha256="c" * 64,
        parser=_parser,
    )


def test_complete_day_is_recovered_and_partial_day_stays_unresolved(tmp_path):
    plan, identity, acquisition, _ = _source(tmp_path)
    output = tmp_path / "salvage"
    result = _build(plan, acquisition, output, identity)
    assert result["complete_source_days"] == 1
    assert result["recovered_required_days"] == 1
    assert result["partial_required_days_not_recovered"] == 1
    assert result["absent_required_days_not_recovered"] == 0
    partition = output / "partitions" / identity
    receipt = json.loads((partition / "RECEIPT.json").read_text())
    assert receipt["recovered_required_dates"] == ["2020-01-02"]
    assert receipt["partial_date_bucket_counts"] == {"2020-01-03": 47}
    assert receipt["partial_required_date_bucket_counts"] == {"2020-01-03": 47}
    assert receipt["absent_required_dates"] == []
    assert receipt["rows"] == pq.read_table(partition / "exact.parquet").num_rows == 48
    assert (partition / "source.wire").read_bytes() == b"immutable original response"
    assert audit_salvage(output, plan, parser=_parser) == result


def test_changed_contained_wire_fails_cold_audit(tmp_path):
    plan, identity, acquisition, _ = _source(tmp_path)
    output = tmp_path / "salvage"
    _build(plan, acquisition, output, identity)
    wire = output / "partitions" / identity / "source.wire"
    wire.write_bytes(b"changed")
    with pytest.raises(Native5mSalvageError, match="PARTITION"):
        audit_salvage(output, plan, parser=_parser)


def test_changed_failure_receipt_blocks_before_output_creation(tmp_path):
    plan, identity, acquisition, failure = _source(tmp_path)
    failure.write_text(failure.read_text() + "changed")
    output = tmp_path / "salvage"
    with pytest.raises(ValueError, match="HELD_PARTITION_CHANGED"):
        _build(plan, acquisition, output, identity)
    assert not output.exists()


def test_source_overlap_is_rejected_without_mutating_source(tmp_path):
    plan, identity, acquisition, _ = _source(tmp_path)
    before = sorted(path.relative_to(acquisition) for path in acquisition.rglob("*"))
    with pytest.raises(ValueError, match="OVERLAP"):
        _build(plan, acquisition, acquisition / "child", identity)
    assert sorted(path.relative_to(acquisition) for path in acquisition.rglob("*")) == before
