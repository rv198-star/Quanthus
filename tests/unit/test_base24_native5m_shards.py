"""Distributed Base 2.4 acquisition remains one exact governed denominator."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
    Native5mAcquireError,
    run_acquisition,
)
from quanthus.pipelines.securities_warehouse.base24_native5m_shards import (
    freeze_shard_selections,
    merge_shard_outputs,
)
from quanthus.providers.native_5m_exact import SHANGHAI, ExactBar


def _plan(windows=4):
    rows = []
    for index in range(windows):
        code = f"sz.{index + 1:06d}"
        start = "2020-01-02"
        end = "2020-01-03"
        identity = hashlib.sha256(f"SZSE\t{code[3:]}\t{start}\t{end}".encode()).hexdigest()
        rows.append(
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
    return tuple(rows)


def _fetch(staging, request):
    wire = staging / "wire"
    wire.mkdir()
    (wire / "response.wire").write_bytes(request.code.encode())
    return _bars(request.code), {"actual_requests": 1, "actual_bytes": 9}


def _build_shards(tmp_path: Path):
    plan = _plan()
    selections = freeze_shard_selections(
        plan,
        terminal_partition_ids=(),
        shard_ids=("cn01", "oci"),
        maximum_data_sends=6,
        maximum_control_calls=8,
    )
    roots = []
    for label, selection in selections.items():
        root = tmp_path / label
        result = run_acquisition(
            plan,
            root,
            _fetch,
            source_roots=(),
            selection_bytes=selection,
        )
        assert result["status"] == "COMPLETE"
        roots.append((label, root))
    return plan, selections, roots


def test_freeze_is_disjoint_complete_balanced_and_parent_bound():
    plan = _plan(7)
    value = json.loads(plan)
    excluded = value["windows"][0]["partition_id"]
    selections = freeze_shard_selections(
        plan,
        terminal_partition_ids=(excluded,),
        shard_ids=("cn01", "cn02", "hermes"),
        maximum_data_sends=11,
        maximum_control_calls=20,
    )
    members = [json.loads(raw) for raw in selections.values()]
    ids = [identity for selection in members for identity in selection["partition_ids"]]
    assert len(ids) == len(set(ids)) == 6
    assert set(ids) == {row["partition_id"] for row in value["windows"]} - {excluded}
    assert {row["parent_plan_sha256"] for row in members} == {hashlib.sha256(plan).hexdigest()}
    assert max(map(len, (row["partition_ids"] for row in members))) <= 2
    assert sum(row["maximum_data_sends"] for row in members) == 11
    assert sum(row["maximum_control_calls"] for row in members) == 20


@pytest.mark.parametrize(
    ("data_sends", "control_calls"),
    [(3, 8), (4, 3)],
)
def test_freeze_rejects_budget_that_would_reset_or_oversubscribe_shards(
    data_sends, control_calls
):
    with pytest.raises(Native5mAcquireError, match="SHARD_FREEZE_BUDGET_INVALID"):
        freeze_shard_selections(
            _plan(4),
            terminal_partition_ids=(),
            shard_ids=("cn01", "oci"),
            maximum_data_sends=data_sends,
            maximum_control_calls=control_calls,
        )


def test_merge_revalidates_and_materializes_full_canonical_root(tmp_path):
    plan, _, roots = _build_shards(tmp_path)
    output = tmp_path / "canonical"
    result = merge_shard_outputs(plan, sources=roots, output=output)
    assert result["status"] == "COMPLETE"
    assert result["total"] == result["completed"] == 4
    assert len(list((output / "partitions").glob("*/READY"))) == 4
    assert (output / "READY").read_text().strip() == hashlib.sha256(
        (output / "CHECKPOINT.json").read_bytes()
    ).hexdigest()
    assert not (output / "staging").exists()


@pytest.mark.parametrize("defect", ["missing", "duplicate"])
def test_merge_rejects_missing_or_duplicate_terminal_partition_before_output(tmp_path, defect):
    plan, _, roots = _build_shards(tmp_path)
    sources = roots[:1] if defect == "missing" else [*roots, roots[0]]
    output = tmp_path / "canonical"
    with pytest.raises(Native5mAcquireError, match="SOURCE_SET|TERMINAL_CLOSURE"):
        merge_shard_outputs(plan, sources=sources, output=output)
    assert not output.exists()


@pytest.mark.parametrize("defect", ["extra", "selection"])
def test_merge_rejects_extra_partition_or_selection_mismatch(tmp_path, defect):
    plan, _, roots = _build_shards(tmp_path)
    root = roots[0][1]
    if defect == "extra":
        (root / "partitions/not-in-plan").mkdir()
    else:
        value = json.loads((root / "SELECTION.json").read_text())
        value["partition_ids"] = value["partition_ids"][:-1]
        value["maximum_data_sends"] -= 1
        (root / "SELECTION.json").write_text(json.dumps(value, sort_keys=True))
    output = tmp_path / "canonical"
    with pytest.raises(
        Native5mAcquireError, match="SOURCE_LAYOUT_INVALID|SELECTION_INVALID"
    ):
        merge_shard_outputs(plan, sources=roots, output=output)
    assert not output.exists()
