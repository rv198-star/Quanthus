"""The distributed worker binds one shard and counts its durable supplier ledger."""

import hashlib
import json

import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_worker import (
    Native5mWorkerError,
    _control_calls,
    _data_sends,
    _frozen_inputs,
)


def _plan():
    identity = hashlib.sha256(b"SZSE\t000001\t2020-01-02\t2020-01-03").hexdigest()
    return json.dumps(
        {
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
                    "code": "sz.000001",
                    "venue": "SZSE",
                    "start_date": "2020-01-02",
                    "end_date": "2020-01-03",
                    "required_dates": ["2020-01-02", "2020-01-03"],
                    "expected_session_dates": ["2020-01-02", "2020-01-03"],
                    "required_security_days": 2,
                    "expected_source_days": 2,
                    "maximum_rows": 96,
                }
            ],
        },
        sort_keys=True,
    ).encode()


def _selection(plan):
    identity = json.loads(plan)["windows"][0]["partition_id"]
    return json.dumps(
        {
            "contract": "p10-base24-native5m-selection-v1",
            "parent_plan_sha256": hashlib.sha256(plan).hexdigest(),
            "shard_id": "cn01",
            "partition_ids": [identity],
            "maximum_data_sends": 3,
            "maximum_control_calls": 8,
        },
        sort_keys=True,
    ).encode()


def test_frozen_inputs_validate_parent_and_return_exact_identities():
    plan = _plan()
    selection = _selection(plan)
    value, plan_sha, selection_sha = _frozen_inputs(plan, selection)
    assert value["shard_id"] == "cn01"
    assert plan_sha == hashlib.sha256(plan).hexdigest()
    assert selection_sha == hashlib.sha256(selection).hexdigest()


def test_worker_counts_all_durable_data_and_control_sends(tmp_path):
    output = tmp_path / "output"
    (output / "partitions/a/wire").mkdir(parents=True)
    (output / "failures/b/wire").mkdir(parents=True)
    for path, count in (
        (output / "partitions/a/wire/wire-receipt.json", 1),
        (output / "failures/b/wire/wire-receipt.json", 2),
    ):
        path.write_text(json.dumps({"records": [{}] * count}))
    (output / "CONTROL.jsonl").write_text(
        "\n".join(
            json.dumps({"event": event})
            for event in ("PROCESS_START", "LOGIN_ATTEMPT", "LOGOUT_ATTEMPT")
        )
        + "\n"
    )
    assert _data_sends(output) == 3
    assert _control_calls(output) == 2


def test_worker_rejects_unreadable_durable_send_ledger(tmp_path):
    path = tmp_path / "output/partitions/a/wire"
    path.mkdir(parents=True)
    (path / "wire-receipt.json").write_text("{}")
    with pytest.raises(Native5mWorkerError, match="WIRE_RECEIPT_UNREADABLE"):
        _data_sends(tmp_path / "output")
