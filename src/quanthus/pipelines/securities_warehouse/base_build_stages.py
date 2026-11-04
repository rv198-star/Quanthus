"""Job-local DAG execution with verified reuse and preserved failed attempts.

Concrete builder stages own semantic admission. This runner owns concurrency,
identity, complete byte verification and recovery; it cannot grant data quality.
It never relabels a pre-existing partial directory as a completed stage.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError
from .base22_sources import disjoint_output
from .base_build_inputs import _object, _physical, _tree, canonical, file_sha, identity, member

KIND = "p10-base-build-stage-receipt-v1"


@dataclass(frozen=True)
class Stage:
    name: str
    dependencies: tuple[str, ...]
    run: Callable[[Path, dict[str, Path]], dict[str, Any]]
    verify: Callable[[Path, dict[str, Any]], None]
    configuration: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_new(path: Path, value: Any) -> None:
    """Publish a receipt only after its full bytes reach the filesystem."""
    temporary = path.with_name("." + path.name + ".partial")
    with temporary.open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Link publication provides no-overwrite semantics on this local filesystem.
    os.link(temporary, path)
    temporary.unlink()
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _inventory(root: Path) -> list[dict[str, Any]]:
    _physical(root)
    if not root.is_dir():
        raise BoundedError("BUILD_STAGE_OUTPUT_REQUIRED")
    rows = []
    for relative in sorted(_tree(root)):
        path = member(root, relative)
        rows.append({"path": relative, "size_bytes": path.stat().st_size,
                     "sha256": file_sha(path)})
    return rows


class StageRunner:
    """One lock covers graph execution; source identities are supplied, never guessed.

    The binding is stable JSON containing the complete frozen closure, code,
    environment and policy identities plus scope. Stage configuration additionally
    invalidates the stage and consumers whose dependency identities change.
    """

    def __init__(self, *, root: Path, sources: list[Path], binding: dict[str, Any],
                 stages: list[Stage]) -> None:
        self.root = disjoint_output(root, sources)
        self.binding = json.loads(canonical(binding))
        required = {"input_identity", "code_revision", "environment_identity",
                    "policy_identity", "scope"}
        if (set(binding) != required or not isinstance(binding["scope"], dict)
                or not binding["scope"]
                or not re.fullmatch(r"[0-9a-f]{40}", str(binding["code_revision"]))):
            raise BoundedError("BUILD_STAGE_JOB_BINDING_INVALID")
        for key in required - {"scope", "code_revision"}:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(binding[key])):
                raise BoundedError("BUILD_STAGE_JOB_BINDING_INVALID")
        self.stages = {stage.name: stage for stage in stages}
        if not stages or len(stages) != len(self.stages):
            raise BoundedError("BUILD_STAGE_GRAPH_INVALID")
        for stage in stages:
            if (not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", stage.name)
                    or len(stage.dependencies) != len(set(stage.dependencies))
                    or not set(stage.dependencies) <= self.stages.keys()):
                raise BoundedError("BUILD_STAGE_GRAPH_INVALID")
            canonical(stage.configuration)
        self._order()  # Detect a cycle before any directories are created.

    def _order(self) -> list[str]:
        order: list[str] = []
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise BoundedError("BUILD_STAGE_GRAPH_CYCLE")
            if name in order:
                return
            visiting.add(name)
            for dependency in sorted(self.stages[name].dependencies):
                visit(dependency)
            visiting.remove(name)
            order.append(name)

        for name in sorted(self.stages):
            visit(name)
        return order

    @contextmanager
    def _lock(self) -> Iterator[None]:
        _physical(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "BUILD.lock"
        _physical(path)
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BoundedError("BUILD_STAGE_JOB_ALREADY_RUNNING") from exc
            yield
        finally:
            os.close(fd)

    def _event(self, value: dict[str, Any]) -> None:
        path = _physical(self.root / "events.jsonl")
        with path.open("ab") as stream:
            stream.write(canonical({"at": _now(), **value}) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _existing(self, stage: Stage, key: str, attempts: list[Path]) -> dict[str, Any] | None:
        for attempt in reversed(attempts):
            receipt_path = attempt / "SUCCESS.json"
            if not receipt_path.exists():
                continue
            try:
                receipt = _object(member(attempt, "SUCCESS.json"))
                if (receipt.get("kind") != KIND or receipt.get("stage") != stage.name
                        or (attempt / "FAILURE.json").exists()
                        or receipt.get("identity") != identity({k: v for k, v in receipt.items()
                                                                 if k != "identity"})):
                    raise BoundedError("BUILD_STAGE_RECEIPT_CHANGED")
                if receipt["stage_identity"] != key:
                    continue
                output = member(attempt, "output")
                if _inventory(output) != receipt["content"]["files"]:
                    raise BoundedError("BUILD_STAGE_OUTPUT_BYTES_CHANGED")
                if identity(receipt["content"]) != receipt["output_identity"]:
                    raise BoundedError("BUILD_STAGE_OUTPUT_IDENTITY_CHANGED")
                stage.verify(output, receipt["content"]["result"])
                return {"path": output, "receipt": receipt, "reused": True}
            except (OSError, ValueError, KeyError, BoundedError) as exc:
                # Preserve the receipt and bytes. A fresh attempt is required;
                # a bad completed stage never permits skipping its verification.
                self._event({"stage": stage.name, "attempt": attempt.name,
                             "status": "REUSE_REJECTED", "exception_type": type(exc).__name__})
        return None

    def execute(self) -> dict[str, dict[str, Any]]:
        with self._lock():
            completed: dict[str, dict[str, Any]] = {}
            for name in self._order():
                stage = self.stages[name]
                dependency_ids = {d: {k: completed[d]["receipt"][k]
                                      for k in ("stage_identity", "output_identity")}
                                  for d in sorted(stage.dependencies)}
                key = identity({"kind": KIND, "binding": self.binding, "stage": name,
                                "configuration": stage.configuration,
                                "dependencies": dependency_ids})
                directory = member(self.root, "stages/" + name)
                directory.mkdir(parents=True, exist_ok=True)
                attempts = sorted(directory.iterdir())
                for attempt in attempts:
                    _physical(attempt)
                    if not attempt.is_dir() or not re.fullmatch(r"attempt-[0-9]{6}", attempt.name):
                        raise BoundedError("BUILD_STAGE_UNKNOWN_ATTEMPT_PRESERVED")
                hit = self._existing(stage, key, attempts)
                if hit is not None:
                    self._event({"stage": name, "status": "VERIFIED_REUSE",
                                 "output_identity": hit["receipt"]["output_identity"]})
                    completed[name] = hit
                    continue
                number = 1 + max((int(p.name[8:]) for p in attempts), default=0)
                if number > 999999:
                    raise BoundedError("BUILD_STAGE_ATTEMPT_LIMIT")
                attempt = directory / f"attempt-{number:06d}"
                attempt.mkdir()
                started = time.monotonic()
                output = attempt / "output"
                _save_new(attempt / "START.json", {"kind": KIND, "stage": name,
                          "stage_identity": key, "started_at": _now(), "lifecycle": "TMP"})
                try:
                    result = stage.run(output, {d: completed[d]["path"]
                                                for d in stage.dependencies})
                    # Make result immutable JSON and reject non-finite counters.
                    result = json.loads(canonical(result))
                    stage.verify(output, result)
                    content = {"files": _inventory(output), "result": result}
                    receipt = {"kind": KIND, "stage": name, "stage_identity": key,
                               "dependencies": dependency_ids, "content": content,
                               "output_identity": identity(content), "finished_at": _now(),
                               "elapsed_seconds": time.monotonic() - started,
                               "bytes": sum(r["size_bytes"] for r in content["files"]),
                               "files": len(content["files"]), "lifecycle": "TMP"}
                    receipt["identity"] = identity(receipt)
                    _save_new(attempt / "SUCCESS.json", receipt)
                    completed[name] = {"path": output, "receipt": receipt, "reused": False}
                    self._event({"stage": name, "status": "BUILT_AND_VERIFIED",
                                 "output_identity": receipt["output_identity"],
                                 "elapsed_seconds": receipt["elapsed_seconds"],
                                 "bytes": receipt["bytes"], "files": receipt["files"]})
                except BaseException as exc:
                    if not (attempt / "SUCCESS.json").exists():
                        _save_new(attempt / "FAILURE.json", {"kind": KIND, "stage": name,
                                  "stage_identity": key, "failed_at": _now(),
                                  "exception_type": type(exc).__name__,
                                  "status": "FAILED_PRESERVED"})
                    raise
            return completed
