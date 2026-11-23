"""Bounded code and synthetic-consumer provenance for the P11 daily library."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import sha256_digest
from quanthus.product_daily.contracts import ProductDailyError

CONSUMER_BINDING = "config/research/r11/d1b-20261124/p12-consumer.synthetic.v1.json"
CODE_SCOPE = (
    "src",
    "config/expert-strategies",
    "config/expert-evaluations",
    "config/research/r11/d1b-20261124",
)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL
    ).strip()


def code_identity(root: Path) -> dict[str, Any]:
    """Distinguish a metadata HEAD from the last source/config revision and local edits.

    Source hashes identify running files during development too. An uncommitted closure
    is reported as such, never labelled with a preceding commit's source identity.
    """
    paths = sorted((root / "src/quanthus/product_daily/recommendation").glob("*.py"))
    paths.extend(
        root / p
        for p in (
            "src/quanthus/expert_strategy/context.py",
            "src/quanthus/expert_strategy/runner.py",
            "src/quanthus/backtest/baseline_research.py",
            "src/quanthus/event_library/scan.py",
            CONSUMER_BINDING,
        )
    )
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result: dict[str, Any] = {
        "code_files_sha256": files,
        "code_content_digest": sha256_digest(files),
    }
    try:
        head = _git(root, "rev-parse", "HEAD")
        source = _git(root, "log", "-1", "--format=%H", "HEAD", "--", *CODE_SCOPE)
        dirty = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *CODE_SCOPE)
        result.update(
            checkout_head=head,
            source_reference_commit=source,
            source_commit=None if dirty else source,
            source_tree=_git(root, "rev-parse", f"{source}^{{tree}}"),
            working_tree_dirty=bool(dirty),
        )
    except (OSError, subprocess.CalledProcessError):
        result.update(
            checkout_head=None,
            source_reference_commit=None,
            source_commit=None,
            source_tree=None,
            working_tree_dirty=None,
        )
    return result


def consumer_identity(root: Path) -> dict[str, Any]:
    raw = json.loads((root / CONSUMER_BINDING).read_text())
    if not isinstance(raw, dict) or raw.get("use_grade") != "SYNTHETIC_COMPATIBILITY_ONLY":
        raise ProductDailyError("consumer_binding_invalid", "synthetic consumer binding required")
    return {**raw, "binding_digest": sha256_digest(raw)}


def resolve_p12_test_root(root: Path, override: str | None = None) -> Path:
    """Find the pinned test consumer; release directories use an explicit override.

    Only code bytes are inspected. This neither activates a consumer nor reads market data.
    """
    binding = consumer_identity(root)
    if override:
        candidate = Path(override).resolve()
    else:
        candidate = None
        for block in _git(root, "worktree", "list", "--porcelain").split("\n\n"):
            fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
            if fields.get("HEAD") == binding["source_commit"]:
                candidate = Path(fields["worktree"])
                break
        if candidate is None:
            raise ProductDailyError("consumer_unavailable", "set QUANTHUS_P12_WORKTREE_ROOT")
    assert candidate is not None
    for path, expected in binding["source_files_sha256"].items():
        try:
            actual = hashlib.sha256((candidate / path).read_bytes()).hexdigest()
        except OSError as exc:
            raise ProductDailyError(
                "consumer_unavailable", f"consumer source missing: {path}"
            ) from exc
        if actual != expected:
            raise ProductDailyError("consumer_release_mismatch", f"consumer source differs: {path}")
    return candidate
