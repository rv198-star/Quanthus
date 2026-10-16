"""Freeze disjoint Base 2.4 acquisition shards and merge their sealed outputs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base24_native5m_acquire import (
    CONTRACT,
    SELECTION_CONTRACT,
    Native5mAcquireError,
    _plan,
    _selection,
    _sha,
    _validate_output,
    _verify_held,
    _verify_sealed,
)

MERGE_CONTRACT = "p10-base24-native5m-shard-merge-v1"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def freeze_shard_selections(
    plan_bytes: bytes,
    *,
    terminal_partition_ids: Sequence[str],
    shard_ids: Sequence[str],
    maximum_data_sends: int,
    maximum_control_calls: int,
) -> dict[str, bytes]:
    """Balance remaining immutable windows without changing the parent plan identity."""
    _, windows = _plan(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    known = {window["partition_id"] for window in windows}
    terminal = set(terminal_partition_ids)
    if (
        not shard_ids
        or len(shard_ids) != len(set(shard_ids))
        or not terminal <= known
        or any(
            not shard_id
            or len(shard_id) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in shard_id
            )
            for shard_id in shard_ids
        )
    ):
        raise Native5mAcquireError("SHARD_FREEZE_INPUT_INVALID")
    remaining = [window for window in windows if window["partition_id"] not in terminal]
    if (
        type(maximum_data_sends) is not int
        or maximum_data_sends < len(remaining)
        or type(maximum_control_calls) is not int
        or maximum_control_calls < 2 * len(shard_ids)
    ):
        raise Native5mAcquireError("SHARD_FREEZE_BUDGET_INVALID")
    assignments: dict[str, list[dict[str, Any]]] = {shard_id: [] for shard_id in shard_ids}
    weights = dict.fromkeys(shard_ids, 0)
    order = {window["partition_id"]: index for index, window in enumerate(windows)}
    for window in sorted(
        remaining,
        key=lambda row: (-int(row["maximum_rows"]), order[row["partition_id"]]),
    ):
        shard_id = min(shard_ids, key=lambda item: (weights[item], item))
        assignments[shard_id].append(window)
        weights[shard_id] += int(window["maximum_rows"])
    result = {}
    extra_data_sends, data_remainder = divmod(
        maximum_data_sends - len(remaining), len(shard_ids)
    )
    control_calls, control_remainder = divmod(maximum_control_calls, len(shard_ids))
    for shard_id in shard_ids:
        shard_index = shard_ids.index(shard_id)
        selected = sorted(assignments[shard_id], key=lambda row: order[row["partition_id"]])
        if not selected:
            raise Native5mAcquireError("SHARD_FREEZE_EMPTY_SELECTION")
        result[shard_id] = _json_bytes(
            {
                "contract": SELECTION_CONTRACT,
                "parent_plan_sha256": plan_sha,
                "shard_id": shard_id,
                "partition_ids": [window["partition_id"] for window in selected],
                "maximum_data_sends": len(selected)
                + extra_data_sends
                + (shard_index < data_remainder),
                "maximum_control_calls": control_calls
                + (shard_index < control_remainder),
            }
        )
    selected_ids = [
        identity
        for selection in result.values()
        for identity in json.loads(selection)["partition_ids"]
    ]
    if len(selected_ids) != len(set(selected_ids)) or set(selected_ids) != known - terminal:
        raise Native5mAcquireError("SHARD_FREEZE_CLOSURE_INVALID")
    return result


def _copy_tree(source: Path, target: Path) -> None:
    if source.is_symlink() or target.exists():
        raise Native5mAcquireError("SHARD_MERGE_MEMBER_COLLISION")
    shutil.copytree(source, target, copy_function=shutil.copy2)


def merge_shard_outputs(
    plan_bytes: bytes,
    *,
    sources: Sequence[tuple[str, Path]],
    output: Path,
) -> dict[str, Any]:
    """Materialize one full acquisition root after verifying exact terminal closure."""
    if not sources or len({label for label, _ in sources}) != len(sources):
        raise Native5mAcquireError("SHARD_MERGE_SOURCE_SET_INVALID")
    roots = tuple(root for _, root in sources)
    if any(not root.is_dir() or root.is_symlink() for root in roots):
        raise Native5mAcquireError("SHARD_MERGE_SOURCE_INVALID")
    _validate_output(output, roots)
    staging = output.with_name(f".{output.name}.job")
    _validate_output(staging, (*roots, output))
    if staging.exists() or output.exists():
        raise Native5mAcquireError("SHARD_MERGE_OUTPUT_EXISTS")

    _, windows = _plan(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    plan_ids = {window["partition_id"] for window in windows}
    for _, root in sources:
        physical: set[str] = set()
        for name in ("partitions", "held"):
            member_root = root / name
            if not member_root.is_dir() or member_root.is_symlink():
                raise Native5mAcquireError("SHARD_MERGE_SOURCE_LAYOUT_INVALID")
            for member in member_root.iterdir():
                if not member.is_dir() or member.is_symlink():
                    raise Native5mAcquireError("SHARD_MERGE_SOURCE_LAYOUT_INVALID")
                if member.name in physical:
                    raise Native5mAcquireError("SHARD_MERGE_SOURCE_LAYOUT_INVALID")
                physical.add(member.name)
        if not physical <= plan_ids:
            raise Native5mAcquireError("SHARD_MERGE_SOURCE_LAYOUT_INVALID")
        selection_path = root / "SELECTION.json"
        if selection_path.exists():
            if not selection_path.is_file() or selection_path.is_symlink():
                raise Native5mAcquireError("SHARD_MERGE_SELECTION_INVALID")
            selected, _ = _selection(
                selection_path.read_bytes(),
                plan_sha=plan_sha,
                windows=windows,
            )
            selected_ids = {window["partition_id"] for window in selected}
            if not physical <= selected_ids:
                raise Native5mAcquireError("SHARD_MERGE_SELECTION_INVALID")
    resolved: list[tuple[dict[str, Any], str, Path, Path]] = []
    for window in windows:
        identity = window["partition_id"]
        candidates: list[tuple[str, Path, Path]] = []
        for _, root in sources:
            partition = root / "partitions" / identity
            held = root / "held" / identity
            if partition.is_dir():
                candidates.append(("partition", partition, root))
            if held.is_dir():
                candidates.append(("held", held, root))
        if len(candidates) != 1:
            raise Native5mAcquireError("SHARD_MERGE_TERMINAL_CLOSURE_INVALID")
        kind, member, root = candidates[0]
        if kind == "partition":
            _verify_sealed(member, window, plan_sha)
        else:
            _verify_held(member, root / "failures", window, plan_sha)
        resolved.append((window, kind, member, root))

    staging.mkdir()
    for name in ("partitions", "held", "failures", "shards"):
        (staging / name).mkdir()
    for _, root in sources:
        failure_root = root / "failures"
        if not failure_root.is_dir() or failure_root.is_symlink():
            raise Native5mAcquireError("SHARD_MERGE_FAILURE_HISTORY_INVALID")
        for failure in sorted(failure_root.iterdir()):
            if not failure.is_dir() or failure.is_symlink():
                raise Native5mAcquireError("SHARD_MERGE_FAILURE_HISTORY_INVALID")
            _copy_tree(failure, staging / "failures" / failure.name)

    completed = held_count = actual_requests = actual_bytes = 0
    for window, kind, member, _ in resolved:
        identity = window["partition_id"]
        if kind == "partition":
            _copy_tree(member, staging / "partitions" / identity)
            receipt = json.loads((member / "RECEIPT.json").read_text(encoding="utf-8"))
            metrics = receipt.get("metrics", {})
            actual_requests += int(metrics.get("actual_requests", 0))
            actual_bytes += int(metrics.get("actual_bytes", 0))
            completed += 1
        else:
            _copy_tree(member, staging / "held" / identity)
            held_count += 1
    source_manifest = []
    for label, root in sources:
        evidence = staging / "shards" / label
        evidence.mkdir()
        files = []
        for name in ("SELECTION.json", "CHECKPOINT.json", "CONTROL.jsonl", "READY"):
            path = root / name
            if path.is_file() and not path.is_symlink():
                shutil.copy2(path, evidence / name)
                files.append({"path": name, "sha256": _sha(path)})
        source_manifest.append({"label": label, "files": files})
    checkpoint = {
        "contract": CONTRACT,
        "plan_sha256": plan_sha,
        "plan_total": len(windows),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "total": len(windows),
        "completed": completed,
        "reused": completed,
        "failed": 0,
        "held": held_count,
        "actual_requests": actual_requests,
        "actual_bytes": actual_bytes,
        "failure_circuit": "merged_verified_shards",
        "source_roots_modified": False,
        "status": "COMPLETE" if held_count == 0 else "COMPLETE_WITH_HELD",
    }
    merge = {
        "contract": MERGE_CONTRACT,
        "plan_sha256": plan_sha,
        "sources": source_manifest,
        "completed": completed,
        "held": held_count,
        "source_roots_modified": False,
    }
    (staging / "MERGE.json").write_bytes(_json_bytes(merge))
    (staging / "CHECKPOINT.json").write_bytes(_json_bytes(checkpoint))
    (staging / "READY").write_text(
        _sha(staging / "CHECKPOINT.json") + "\n", encoding="ascii"
    )
    os.rename(staging, output)
    return checkpoint
