"""Freeze disjoint Base 2.4 acquisition shards and merge their sealed outputs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
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
SOURCE_INDEX_CONTRACT = "p10-base24-native5m-source-index-v1"


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


def _validated_source_set(
    sources: Sequence[tuple[str, Path]],
) -> tuple[Path, ...]:
    if (
        not sources
        or any(
            not isinstance(label, str)
            or not label
            or label in {".", ".."}
            or len(label) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in label
            )
            for label, _ in sources
        )
        or len({label for label, _ in sources}) != len(sources)
        or len({root.resolve(strict=False) for _, root in sources}) != len(sources)
    ):
        raise Native5mAcquireError("SHARD_MERGE_SOURCE_SET_INVALID")
    roots = tuple(root for _, root in sources)
    if any(not root.is_dir() or root.is_symlink() for root in roots):
        raise Native5mAcquireError("SHARD_MERGE_SOURCE_INVALID")
    return roots


def _source_layout(
    root: Path,
    *,
    plan_sha: str,
    windows: list[dict[str, Any]],
) -> dict[str, str]:
    plan_ids = {window["partition_id"] for window in windows}
    physical: dict[str, str] = {}
    for name in ("partitions", "held"):
        member_root = root / name
        if not member_root.is_dir() or member_root.is_symlink():
            raise Native5mAcquireError("SHARD_MERGE_SOURCE_LAYOUT_INVALID")
        for member in member_root.iterdir():
            if (
                not member.is_dir()
                or member.is_symlink()
                or member.name in physical
            ):
                raise Native5mAcquireError("SHARD_MERGE_SOURCE_LAYOUT_INVALID")
            physical[member.name] = "partition" if name == "partitions" else "held"
    if not set(physical) <= plan_ids:
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
        if not set(physical) <= selected_ids:
            raise Native5mAcquireError("SHARD_MERGE_SELECTION_INVALID")
    return physical


def _source_file_manifest(
    root: Path,
    *,
    known_hashes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    known = known_hashes or {}
    files = []

    def scan_error(error: OSError) -> None:
        raise Native5mAcquireError("SHARD_SOURCE_INDEX_SCAN_FAILED") from error

    try:
        for directory, subdirectories, names in os.walk(root, onerror=scan_error):
            parent = Path(directory)
            for name in subdirectories:
                path = parent / name
                if path.is_symlink() or not path.is_dir():
                    raise Native5mAcquireError("SHARD_SOURCE_INDEX_LAYOUT_INVALID")
            for name in names:
                path = parent / name
                if path.is_symlink() or not path.is_file():
                    raise Native5mAcquireError("SHARD_SOURCE_INDEX_LAYOUT_INVALID")
                relative = path.relative_to(root).as_posix()
                files.append(
                    {
                        "path": relative,
                        "bytes": path.stat().st_size,
                        "sha256": known.get(relative) or _sha(path),
                    }
                )
    except OSError as exc:
        scan_error(exc)
    files.sort(key=lambda item: str(item["path"]))
    if set(known) - {row["path"] for row in files}:
        raise Native5mAcquireError("SHARD_SOURCE_INDEX_LAYOUT_INVALID")
    return files


def freeze_shard_source_index(
    plan_bytes: bytes,
    *,
    sources: Sequence[tuple[str, Path]],
    output: Path,
    evaluator_version: str,
) -> dict[str, Any]:
    """Fully verify available shard roots once and freeze their exact file closure."""
    if (
        not evaluator_version
        or len(evaluator_version) > 128
        or not evaluator_version.isascii()
    ):
        raise Native5mAcquireError("SHARD_SOURCE_INDEX_EVALUATOR_INVALID")
    started = time.monotonic()
    roots = _validated_source_set(sources)
    _validate_output(output, roots)
    staging = output.with_name(f".{output.name}.job")
    _validate_output(staging, (*roots, output))
    if staging.exists() or output.exists():
        raise Native5mAcquireError("SHARD_SOURCE_INDEX_OUTPUT_EXISTS")
    _, windows = _plan(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    windows_by_id = {window["partition_id"]: window for window in windows}
    indexed_sources = []
    completed = held_count = file_count = byte_count = 0
    for label, root in sources:
        physical = _source_layout(root, plan_sha=plan_sha, windows=windows)
        known_hashes: dict[str, str] = {}
        for identity, kind in sorted(physical.items()):
            window = windows_by_id[identity]
            if kind == "partition":
                member = root / "partitions" / identity
                receipt = _verify_sealed(member, window, plan_sha)
                prefix = f"partitions/{identity}/"
                known_hashes.update(
                    {prefix + name: digest for name, digest in receipt["files"].items()}
                )
                receipt_digest = (member / "READY").read_text(encoding="ascii").strip()
                known_hashes[prefix + "RECEIPT.json"] = receipt_digest
                known_hashes[prefix + "READY"] = _sha(member / "READY")
                completed += 1
            else:
                member = root / "held" / identity
                hold = _verify_held(member, root / "failures", window, plan_sha)
                prefix = f"held/{identity}/"
                hold_digest = (member / "READY").read_text(encoding="ascii").strip()
                known_hashes[prefix + "HOLD.json"] = hold_digest
                known_hashes[prefix + "READY"] = _sha(member / "READY")
                for failure in hold["failure_receipts"]:
                    known_hashes[failure["path"]] = failure["sha256"]
                held_count += 1
        files = _source_file_manifest(root, known_hashes=known_hashes)
        file_count += len(files)
        byte_count += sum(int(row["bytes"]) for row in files)
        indexed_sources.append(
            {
                "label": label,
                "root": str(root.resolve(strict=True)),
                "completed": sum(kind == "partition" for kind in physical.values()),
                "held": sum(kind == "held" for kind in physical.values()),
                "terminal_partition_ids": sorted(physical),
                "files": files,
            }
        )
    value = {
        "contract": SOURCE_INDEX_CONTRACT,
        "lifecycle": "TMP",
        "evaluator_version": evaluator_version,
        "plan_sha256": plan_sha,
        "source_count": len(indexed_sources),
        "completed": completed,
        "held": held_count,
        "file_count": file_count,
        "bytes": byte_count,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "sources": indexed_sources,
    }
    staging.mkdir()
    index_path = staging / "INDEX.json"
    index_path.write_bytes(_json_bytes(value))
    (staging / "READY").write_text(_sha(index_path) + "\n", encoding="ascii")
    os.rename(staging, output)
    return value


def _verified_source_labels(
    source_index: Path | None,
    source_index_sha256: str | None,
    source_index_evaluator_version: str | None,
    *,
    plan_sha: str,
    sources: Sequence[tuple[str, Path]],
) -> set[str]:
    if (
        source_index is None
        and source_index_sha256 is None
        and source_index_evaluator_version is None
    ):
        return set()
    if (
        source_index is None
        or source_index_sha256 is None
        or source_index_evaluator_version is None
    ):
        raise Native5mAcquireError("SHARD_SOURCE_INDEX_IDENTITY_INVALID")
    try:
        actual_members = {path.name for path in source_index.iterdir()}
        index_path = source_index / "INDEX.json"
        ready = (source_index / "READY").read_text(encoding="ascii").strip()
        raw = index_path.read_bytes()
        value = json.loads(raw)
    except (OSError, TypeError, ValueError) as exc:
        raise Native5mAcquireError("SHARD_SOURCE_INDEX_INVALID") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if (
        source_index.is_symlink()
        or actual_members != {"INDEX.json", "READY"}
        or digest != ready
        or digest != source_index_sha256
        or not isinstance(value, dict)
        or value.get("contract") != SOURCE_INDEX_CONTRACT
        or value.get("lifecycle") != "TMP"
        or value.get("evaluator_version") != source_index_evaluator_version
        or value.get("plan_sha256") != plan_sha
        or not isinstance(value.get("sources"), list)
    ):
        raise Native5mAcquireError("SHARD_SOURCE_INDEX_INVALID")
    available = {label: root for label, root in sources}
    verified = set()
    for indexed in value["sources"]:
        try:
            label = indexed["label"]
            root = available[label]
            expected_root = indexed["root"]
            expected_files = indexed["files"]
        except (KeyError, TypeError) as exc:
            raise Native5mAcquireError("SHARD_SOURCE_INDEX_INVALID") from exc
        if (
            not isinstance(label, str)
            or label in verified
            or str(root.resolve(strict=True)) != expected_root
            or _source_file_manifest(root) != expected_files
        ):
            raise Native5mAcquireError("SHARD_SOURCE_INDEX_CHANGED")
        verified.add(label)
    return verified


def merge_shard_outputs(
    plan_bytes: bytes,
    *,
    sources: Sequence[tuple[str, Path]],
    output: Path,
    verified_source_index: Path | None = None,
    verified_source_index_sha256: str | None = None,
    verified_source_index_evaluator_version: str | None = None,
) -> dict[str, Any]:
    """Materialize one full acquisition root after verifying exact terminal closure."""
    roots = _validated_source_set(sources)
    _validate_output(output, roots)
    staging = output.with_name(f".{output.name}.job")
    _validate_output(staging, (*roots, output))
    if staging.exists() or output.exists():
        raise Native5mAcquireError("SHARD_MERGE_OUTPUT_EXISTS")

    _, windows = _plan(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    verified_labels = _verified_source_labels(
        verified_source_index,
        verified_source_index_sha256,
        verified_source_index_evaluator_version,
        plan_sha=plan_sha,
        sources=sources,
    )
    for _, root in sources:
        _source_layout(root, plan_sha=plan_sha, windows=windows)
    resolved: list[tuple[dict[str, Any], str, Path, Path]] = []
    superseded_holds: list[dict[str, Any]] = []
    repeated_holds: list[dict[str, Any]] = []
    for window in windows:
        identity = window["partition_id"]
        candidates: list[tuple[str, Path, Path, str]] = []
        for label, root in sources:
            partition = root / "partitions" / identity
            held = root / "held" / identity
            if partition.is_dir():
                candidates.append(("partition", partition, root, label))
            if held.is_dir():
                candidates.append(("held", held, root, label))
        if not candidates:
            raise Native5mAcquireError("SHARD_MERGE_TERMINAL_CLOSURE_INVALID")
        for kind, member, root, label in candidates:
            if label in verified_labels:
                continue
            if kind == "partition":
                _verify_sealed(member, window, plan_sha)
            else:
                _verify_held(member, root / "failures", window, plan_sha)
        partitions = [candidate for candidate in candidates if candidate[0] == "partition"]
        holds = [candidate for candidate in candidates if candidate[0] == "held"]
        if len(partitions) == 1:
            kind, member, root, selected_label = partitions[0]
            if holds:
                superseded_holds.append(
                    {
                        "partition_id": identity,
                        "selected_partition_source": selected_label,
                        "superseded_hold_sources": sorted(
                            label for _, _, _, label in holds
                        ),
                    }
                )
        elif len(partitions) == 0 and holds:
            kind, member, root, selected_label = holds[-1]
            if len(holds) > 1:
                repeated_holds.append(
                    {
                        "partition_id": identity,
                        "selected_hold_source": selected_label,
                        "prior_hold_sources": [
                            label for _, _, _, label in holds[:-1]
                        ],
                    }
                )
        else:
            raise Native5mAcquireError("SHARD_MERGE_TERMINAL_CLOSURE_INVALID")
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

    actual_requests = actual_bytes = 0
    for _, root in sources:
        for receipt_path in sorted(root.rglob("wire-receipt.json")):
            try:
                records = json.loads(receipt_path.read_text(encoding="utf-8"))["records"]
                if not isinstance(records, list):
                    raise TypeError
                actual_requests += len(records)
                actual_bytes += sum(int(record["received_bytes"]) for record in records)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise Native5mAcquireError("SHARD_MERGE_WIRE_RECEIPT_INVALID") from exc
    completed = held_count = 0
    for window, kind, member, _ in resolved:
        identity = window["partition_id"]
        if kind == "partition":
            _copy_tree(member, staging / "partitions" / identity)
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
        "superseded_holds": superseded_holds,
        "repeated_holds": repeated_holds,
        "source_roots_modified": False,
        "verified_source_index": (
            {
                "path": str(verified_source_index),
                "sha256": verified_source_index_sha256,
                "evaluator_version": verified_source_index_evaluator_version,
                "reused_source_labels": sorted(verified_labels),
            }
            if verified_source_index is not None
            else None
        ),
    }
    (staging / "MERGE.json").write_bytes(_json_bytes(merge))
    (staging / "CHECKPOINT.json").write_bytes(_json_bytes(checkpoint))
    (staging / "READY").write_text(
        _sha(staging / "CHECKPOINT.json") + "\n", encoding="ascii"
    )
    os.rename(staging, output)
    return checkpoint
