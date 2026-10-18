"""The distributed worker binds one shard and counts its durable supplier ledger."""

import hashlib
import json

import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_worker import (
    Native5mWorkerError,
    _control_calls,
    _data_sends,
    _fetch_captured_request,
    _frozen_inputs,
    main,
)
from quanthus.providers.native_5m_exact import NativeWindow


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


@pytest.mark.parametrize("one_day", [True, False])
def test_worker_routes_single_day_to_exact_day_contract(monkeypatch, one_day):
    calls = []
    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base24_native5m_worker.time.sleep",
        lambda value: calls.append(("sleep", value)),
    )
    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base24_native5m_worker.fetch_captured_day",
        lambda client, request, capture: calls.append(
            ("day", client, request.business_date, capture)
        )
        or (),
    )
    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base24_native5m_worker.fetch_captured_window",
        lambda query, request, capture, **limits: calls.append(
            ("window", query, request.end_date, capture, limits)
        )
        or (),
    )
    end = "2020-01-02" if one_day else "2020-01-03"
    client, query, capture = object(), object(), object()
    assert (
        _fetch_captured_request(
            client=client,
            query_page=query,
            request=NativeWindow("sz.000001", "2020-01-02", end),
            capture=capture,
            request_spacing_seconds=1.0,
        )
        == ()
    )
    assert calls[0] == ("sleep", 1.0)
    if one_day:
        assert calls[1] == ("day", client, "2020-01-02", capture)
    else:
        assert calls[1] == (
            "window",
            query,
            "2020-01-03",
            capture,
            {"page_size": 20000, "max_pages": 1, "max_rows": 20000},
        )


def test_worker_binds_selection_before_opening_supplier_session(tmp_path, monkeypatch):
    plan = _plan()
    selection = _selection(plan)
    plan_path = tmp_path / "plan.json"
    selection_path = tmp_path / "selection.json"
    plan_path.write_bytes(plan)
    selection_path.write_bytes(selection)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    output = tmp_path / "output"
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base24_native5m_worker._git_head",
        lambda _: "a" * 40,
    )
    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base24_native5m_worker._git_tracked_clean",
        lambda _: True,
    )
    monkeypatch.setitem(__import__("sys").modules, "baostock", None)
    args = __import__(
        "quanthus.pipelines.securities_warehouse.base24_native5m_worker",
        fromlist=["_parser"],
    )._parser().parse_args(
        [
            "--plan",
            str(plan_path),
            "--selection",
            str(selection_path),
            "--output",
            str(output),
            "--evidence",
            str(evidence),
            "--worktree",
            str(worktree),
            "--expected-plan-sha256",
            hashlib.sha256(plan).hexdigest(),
            "--expected-selection-sha256",
            hashlib.sha256(selection).hexdigest(),
            "--expected-code-sha",
            "a" * 40,
            "--free-floor-bytes",
            "1",
            "--max-allocated-bytes",
            "1000000",
        ]
    )
    with pytest.raises(ModuleNotFoundError):
        __import__(
            "quanthus.pipelines.securities_warehouse.base24_native5m_worker",
            fromlist=["run_worker"],
        ).run_worker(args)
    assert (output / "SELECTION.json").read_bytes() == selection


def test_main_retries_partial_worker_until_complete(monkeypatch):
    results = iter((2, 2, 0))
    calls = []

    def run(_args):
        calls.append("run")
        return next(results)

    monkeypatch.setattr(
        "quanthus.pipelines.securities_warehouse.base24_native5m_worker.run_worker", run
    )
    assert main(["--max-runs", "3", "--retry-wait-seconds", "0"] + _required_args()) == 0
    assert calls == ["run", "run", "run"]


def _required_args():
    return [
        "--plan",
        "plan",
        "--selection",
        "selection",
        "--output",
        "output",
        "--evidence",
        "evidence",
        "--worktree",
        "worktree",
        "--expected-plan-sha256",
        "0" * 64,
        "--expected-selection-sha256",
        "0" * 64,
        "--expected-code-sha",
        "0" * 40,
        "--free-floor-bytes",
        "1",
        "--max-allocated-bytes",
        "1",
    ]
