"""Distributed Base 2.4 acquisition remains one exact governed denominator."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base24_native5m_shards as shards
from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
    Native5mAcquireError,
    run_acquisition,
)
from quanthus.pipelines.securities_warehouse.base24_native5m_shards import (
    freeze_shard_selections,
    freeze_shard_source_index,
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


def test_merge_reuses_pinned_verified_source_index_for_available_shards(tmp_path):
    plan, _, roots = _build_shards(tmp_path)
    source_index = tmp_path / "source-index"
    indexed = freeze_shard_source_index(
        plan,
        sources=roots[:1],
        output=source_index,
        evaluator_version="test-evaluator-v1",
    )
    assert indexed["source_count"] == 1
    assert indexed["completed"] == 2
    index_sha256 = hashlib.sha256((source_index / "INDEX.json").read_bytes()).hexdigest()

    output = tmp_path / "canonical"
    merged = merge_shard_outputs(
        plan,
        sources=roots,
        output=output,
        verified_source_index=source_index,
        verified_source_index_sha256=index_sha256,
        verified_source_index_evaluator_version="test-evaluator-v1",
    )
    assert merged["status"] == "COMPLETE"
    evidence = json.loads((output / "MERGE.json").read_text())
    assert evidence["verified_source_index"] == {
        "path": str(source_index),
        "sha256": index_sha256,
        "evaluator_version": "test-evaluator-v1",
        "reused_source_labels": [roots[0][0]],
    }


def test_merge_rejects_changed_source_bound_to_verified_index(tmp_path):
    plan, _, roots = _build_shards(tmp_path)
    source_index = tmp_path / "source-index"
    freeze_shard_source_index(
        plan,
        sources=roots[:1],
        output=source_index,
        evaluator_version="test-evaluator-v1",
    )
    index_sha256 = hashlib.sha256((source_index / "INDEX.json").read_bytes()).hexdigest()
    parquet = next((roots[0][1] / "partitions").glob("*/exact.parquet"))
    with parquet.open("ab") as stream:
        stream.write(b"changed")

    output = tmp_path / "canonical"
    with pytest.raises(Native5mAcquireError, match="SHARD_SOURCE_INDEX_CHANGED"):
        merge_shard_outputs(
            plan,
            sources=roots,
            output=output,
            verified_source_index=source_index,
            verified_source_index_sha256=index_sha256,
            verified_source_index_evaluator_version="test-evaluator-v1",
        )
    assert not output.exists()


def test_merge_accepts_verified_partial_selection_after_bounded_cutover(tmp_path):
    plan = _plan()
    initial = freeze_shard_selections(
        plan,
        terminal_partition_ids=(),
        shard_ids=("initial",),
        maximum_data_sends=5,
        maximum_control_calls=4,
    )["initial"]
    initial_root = tmp_path / "initial"
    first = run_acquisition(
        plan,
        initial_root,
        _fetch,
        source_roots=(),
        selection_bytes=initial,
        max_new_partitions=1,
    )
    assert first["status"] == "PARTIAL"
    completed = [path.name for path in (initial_root / "partitions").iterdir()]

    continuation = freeze_shard_selections(
        plan,
        terminal_partition_ids=completed,
        shard_ids=("continuation",),
        maximum_data_sends=4,
        maximum_control_calls=4,
    )["continuation"]
    continuation_root = tmp_path / "continuation"
    second = run_acquisition(
        plan,
        continuation_root,
        _fetch,
        source_roots=(),
        selection_bytes=continuation,
    )
    assert second["status"] == "COMPLETE"

    output = tmp_path / "canonical"
    merged = merge_shard_outputs(
        plan,
        sources=(("initial", initial_root), ("continuation", continuation_root)),
        output=output,
    )
    assert merged["status"] == "COMPLETE"
    assert merged["completed"] == 4


def test_merge_allows_verified_partition_to_supersede_prior_hold(tmp_path):
    plan = _plan(1)
    selection = freeze_shard_selections(
        plan,
        terminal_partition_ids=(),
        shard_ids=("original",),
        maximum_data_sends=3,
        maximum_control_calls=4,
    )["original"]
    held_root = tmp_path / "held"

    def fail(staging, request):
        wire = staging / "wire"
        wire.mkdir()
        (wire / "response.wire").write_bytes(request.code.encode())
        raise RuntimeError("WIRE_PAGE_INVALID")

    held = run_acquisition(
        plan,
        held_root,
        fail,
        source_roots=(),
        selection_bytes=selection,
        max_attempts_per_partition=1,
    )
    assert held["status"] == "COMPLETE_WITH_HELD"

    retry_root = tmp_path / "retry"
    retry = run_acquisition(
        plan,
        retry_root,
        _fetch,
        source_roots=(),
        selection_bytes=selection,
    )
    assert retry["status"] == "COMPLETE"

    source_index = tmp_path / "held-source-index"
    freeze_shard_source_index(
        plan,
        sources=(("original", held_root),),
        output=source_index,
        evaluator_version="test-evaluator-v1",
    )
    index_sha256 = hashlib.sha256((source_index / "INDEX.json").read_bytes()).hexdigest()
    output = tmp_path / "canonical"
    merged = merge_shard_outputs(
        plan,
        sources=(("original", held_root), ("retry", retry_root)),
        output=output,
        verified_source_index=source_index,
        verified_source_index_sha256=index_sha256,
        verified_source_index_evaluator_version="test-evaluator-v1",
    )
    assert merged["completed"] == 1
    assert merged["held"] == 0
    evidence = json.loads((output / "MERGE.json").read_text())
    assert evidence["superseded_holds"] == [
        {
            "partition_id": json.loads(plan)["windows"][0]["partition_id"],
            "selected_partition_source": "retry",
            "superseded_hold_sources": ["original"],
        }
    ]


def test_merge_rejects_two_completed_candidates_for_same_partition(tmp_path):
    plan = _plan(1)
    selection = freeze_shard_selections(
        plan,
        terminal_partition_ids=(),
        shard_ids=("only",),
        maximum_data_sends=1,
        maximum_control_calls=2,
    )["only"]
    roots = []
    for name in ("first", "second"):
        root = tmp_path / name
        run_acquisition(
            plan,
            root,
            _fetch,
            source_roots=(),
            selection_bytes=selection,
        )
        roots.append((name, root))
    with pytest.raises(Native5mAcquireError, match="TERMINAL_CLOSURE"):
        merge_shard_outputs(plan, sources=roots, output=tmp_path / "canonical")


def test_merge_retains_latest_of_repeated_verified_holds(tmp_path):
    plan = _plan(1)
    selection = freeze_shard_selections(
        plan,
        terminal_partition_ids=(),
        shard_ids=("only",),
        maximum_data_sends=3,
        maximum_control_calls=2,
    )["only"]

    def fail(staging, request):
        wire = staging / "wire"
        wire.mkdir()
        (wire / "response.wire").write_bytes(request.code.encode())
        raise RuntimeError("WIRE_PAGE_INVALID")

    roots = []
    for name in ("original", "retry"):
        root = tmp_path / name
        result = run_acquisition(
            plan,
            root,
            fail,
            source_roots=(),
            selection_bytes=selection,
            max_attempts_per_partition=1,
        )
        assert result["status"] == "COMPLETE_WITH_HELD"
        roots.append((name, root))

    output = tmp_path / "canonical"
    merged = merge_shard_outputs(plan, sources=roots, output=output)
    assert merged["completed"] == 0
    assert merged["held"] == 1
    evidence = json.loads((output / "MERGE.json").read_text())
    assert evidence["repeated_holds"] == [
        {
            "partition_id": json.loads(plan)["windows"][0]["partition_id"],
            "selected_hold_source": "retry",
            "prior_hold_sources": ["original"],
        }
    ]


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


@pytest.mark.parametrize("invalid_label", ["../escape", "nested/escape", ".", "", "absolute"])
@pytest.mark.parametrize("operation", ["merge", "index"])
def test_source_labels_reject_paths_before_any_output(tmp_path, invalid_label, operation):
    plan, _, roots = _build_shards(tmp_path)
    label = str(tmp_path / "escape") if invalid_label == "absolute" else invalid_label
    sources = [(label, roots[0][1]), roots[1]]
    before = {
        str(path): path.read_bytes()
        for _, root in roots
        for path in root.rglob("*")
        if path.is_file()
    }
    original_children = set(tmp_path.iterdir())
    output = tmp_path / "canonical"
    with pytest.raises(Native5mAcquireError, match="SHARD_MERGE_SOURCE_SET_INVALID"):
        if operation == "merge":
            merge_shard_outputs(plan, sources=sources, output=output)
        else:
            freeze_shard_source_index(
                plan, sources=sources, output=output, evaluator_version="test-v1"
            )
    assert set(tmp_path.iterdir()) == original_children
    assert before == {str(Path(path)): Path(path).read_bytes() for path in before}


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_source_index_scan_errors_reject_incomplete_inventory(tmp_path, monkeypatch, error_type):
    plan, _, roots = _build_shards(tmp_path)
    blocked = roots[0][1] / "unreadable-evidence"
    blocked.mkdir()
    marker = blocked / "sentinel.txt"
    marker.write_bytes(b"unchanged synthetic evidence")
    scandir = shards.os.scandir

    def fail_scan(path):
        if Path(path) == blocked:
            raise error_type("deterministic synthetic scan failure")
        return scandir(path)

    monkeypatch.setattr(shards.os, "scandir", fail_scan)
    output = tmp_path / "source-index"
    with pytest.raises(Native5mAcquireError, match="SHARD_SOURCE_INDEX_SCAN_FAILED"):
        freeze_shard_source_index(
            plan, sources=roots, output=output, evaluator_version="test-v1"
        )
    assert not output.exists()
    assert not output.with_name(".source-index.job").exists()
    assert marker.read_bytes() == b"unchanged synthetic evidence"
