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
CONSUMER_BINDING_V2 = "config/research/r11/d1b-20261124/p12-consumer.daily-advice-package.v2.json"
CONSUMER_BINDING_SCHEMA = "quanthus.p11.p12-consumer-binding.v1"
PINNED_V2_SCHEMA = "p12.decision-set.v2"
PINNED_V2_LOADER = "quanthus.p12_daily.decision_loader.load_decision_set"
# The pinned v2 consumer admits an existing account mode and a separately
# declared purpose.  The mode is passed explicitly to load_decision_set; the
# purpose is recorded on the binding and never inferred from wire bytes.
PINNED_V2_MODE = "simulation"
PINNED_V2_PURPOSE = "historical_replay"
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
            CONSUMER_BINDING_V2,
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


def consumer_identity_v2(root: Path) -> dict[str, Any]:
    """The candidate v2 consumer binding object.

    Distinct file, distinct schema state.  Since the real p12.decision-set.v2
    loader/schema/digest was delivered (P12 commit 93948784, #723), the binding is
    held to the full pin contract before it counts as bound: a non-admission
    field, a missing real byte hash or a loader entry that is not the pinned one
    keeps the release gate closed.  The pinned synthetic v1 consumer bytes are
    never edited to declare a capability it does not have.
    """
    raw = json.loads((root / CONSUMER_BINDING_V2).read_text())
    required = {
        "schema_version",
        "binding_id",
        "binding_version",
        "use_grade",
        "purpose",
        "mode",
        "consumer_schema",
        "loader_entry",
        "loader_contract",
        "source_commit",
        "source_tree",
        "source_files_sha256",
        "pin_state",
        "production_adoption",
        "capabilities",
    }
    if not isinstance(raw, dict) or raw.get("schema_version") != CONSUMER_BINDING_SCHEMA:
        raise ProductDailyError(
            "consumer_binding_invalid", "versioned v2 consumer binding required"
        )
    missing = required - set(raw)
    if missing:
        raise ProductDailyError(
            "consumer_binding_invalid",
            f"v2 consumer binding missing fields: {sorted(missing)}",
        )
    files = raw.get("source_files_sha256")
    if raw.get("pin_state") == "bound":
        if raw.get("consumer_schema") != PINNED_V2_SCHEMA:
            raise ProductDailyError(
                "consumer_binding_invalid", "bound v2 consumer schema differs"
            )
        if raw.get("loader_entry") != PINNED_V2_LOADER:
            raise ProductDailyError(
                "consumer_binding_invalid", "bound v2 loader entry differs"
            )
        if raw.get("mode") not in {"simulation", "synthetic", "real"}:
            raise ProductDailyError(
                "consumer_binding_invalid", "bound v2 mode is not an admitted account mode"
            )
        if not isinstance(raw.get("purpose"), str) or not raw["purpose"]:
            raise ProductDailyError(
                "consumer_binding_invalid", "bound v2 purpose must be declared"
            )
        if not isinstance(raw.get("capabilities"), dict) or raw["capabilities"].get(
            "supports_held_reduction_v2"
        ) is not True:
            raise ProductDailyError(
                "consumer_binding_invalid", "bound v2 capability declaration required"
            )
        for key in ("source_commit", "source_tree"):
            value = raw.get(key)
            if (
                not isinstance(value, str)
                or len(value) != 40
                or any(ch not in "0123456789abcdef" for ch in value)
            ):
                raise ProductDailyError(
                    "consumer_binding_invalid", f"bound v2 {key} requires a full40hex pin"
                )
        if not isinstance(files, dict) or not files:
            raise ProductDailyError(
                "consumer_binding_invalid", "bound v2 pin requires per-file sha256"
            )
        for path, expected in files.items():
            if (
                not isinstance(path, str)
                or Path(path).is_absolute()
                or ".." in Path(path).parts
                or not isinstance(expected, str)
                or len(expected) != 64
                or any(ch not in "0123456789abcdef" for ch in expected)
            ):
                raise ProductDailyError(
                    "consumer_binding_invalid",
                    f"bound v2 source_files_sha256 entry invalid: {path!r}",
                )
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


def resolve_p12_consumer_root(root: Path, binding: dict[str, Any], override: str | None = None) -> Path:
    """Resolve and byte-verify the consumer checkout named by a bound pin.

    The release path only trusts the versioned binding's per-file SHA-256 map;
    a worktree matching source_commit is auto-discovered (with the same check
    resolve_p12_test_root applies), and an explicit override must verify every
    declared byte.  A commit match whose loader/schema bytes differ is a pin
    drift and fails closed.
    """

    candidate: Path | None = None
    if override:
        candidate = Path(override).resolve()
    else:
        for block in _git(root, "worktree", "list", "--porcelain").split("\n\n"):
            fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
            if fields.get("HEAD") == binding.get("source_commit"):
                candidate = Path(fields["worktree"])
                break
        if candidate is None:
            raise ProductDailyError(
                "consumer_unavailable",
                "pinned consumer commit has no local checkout; set "
                "QUANTHUS_P12_CONSUMER_ROOT (the env override still verifies "
                "every pinned byte)",
            )
    assert candidate is not None
    for path, expected in binding["source_files_sha256"].items():
        try:
            actual = hashlib.sha256((candidate / path).read_bytes()).hexdigest()
        except OSError as exc:
            raise ProductDailyError(
                "consumer_unavailable", f"consumer source missing: {path}"
            ) from exc
        if actual != expected:
            raise ProductDailyError(
                "consumer_release_mismatch",
                f"pinned consumer source drifted: {path}",
            )
    return candidate


def resolve_pinned_v2_loader(
    root: Path, override: str | None = None
) -> "Callable[[dict[str, Any]], Any]":
    """Resolve the real pinned p12.decision-set.v2 loader from the bound binding.

    This is the only authority a v2 release may use: the versioned binding file
    on disk decides the consumer checkout, and every pinned file byte is
    verified before the module is imported.  The returned callable is
    quanthus.p12_daily.decision_loader.load_decision_set from the pinned tree -
    never a caller dict, a module attribute, or an importable name outside the
    pinned closure.  It accepts one raw decision_set dict (expected_schema and
    the declared account mode are already bound) and returns the real
    (MarketDecisionSet, wire_digest) tuple produced by the single pinned call.
    """

    import os
    import sys
    from collections.abc import Callable as _Callable

    binding = consumer_identity_v2(root)
    if binding.get("pin_state") != "bound":
        raise ProductDailyError(
            "consumer_unavailable",
            "the versioned P12 v2 consumer pin is not bound; a caller loader "
            "never opens the release",
        )
    consumer_root = resolve_p12_consumer_root(
        root,
        binding,
        override if override is not None else os.environ.get("QUANTHUS_P12_CONSUMER_ROOT"),
    )
    pinned_src = str(consumer_root / "src")
    insert_at = 0
    # A consumer tree that is already importable through a stale sys.path entry
    # would let a sibling (unpinned) tree satisfy the name.  Resolve through the
    # pinned checkout's own src directory first; the byte check above already
    # guarantees the closure underneath it is exactly the pinned revision.
    while insert_at < len(sys.path) and sys.path[insert_at] == pinned_src:
        insert_at += 1
    sys.path.insert(insert_at, pinned_src)
    try:
        import quanthus.p12_daily.decision_loader as pinned_loader
    finally:
        del sys.path[insert_at]
    module_file = Path(getattr(pinned_loader, "__file__", "")).resolve()
    expected_loader = (consumer_root / "src/quanthus/p12_daily/decision_loader.py").resolve()
    if module_file != expected_loader:
        raise ProductDailyError(
            "consumer_release_mismatch",
            f"pinned loader resolved outside the bound checkout: {module_file}",
        )
    load = pinned_loader.load_decision_set

    def _loader(raw: dict[str, Any]) -> Any:
        """One real pinned call; returns the (validated object, wire_digest) tuple."""

        return load(
            raw,
            expected_schema=PINNED_V2_SCHEMA,
            mode=binding["mode"],
        )

    return _loader
