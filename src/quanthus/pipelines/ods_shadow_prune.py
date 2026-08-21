"""Conservative retention for mutable ODS shadow staging data.

The pruner deliberately has no code path that removes releases, catalogs, or DWD
artifacts. Successful daily staging is eligible only after the exact release is
bound into a verified catalog owned by a successful ODS-to-DWD orchestration run.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import (
    OdsReleaseError,
    OdsVerificationResult,
    verify_ods_release,
)
from quanthus.pipelines.securities_warehouse.daily_ods_catalog import (
    DailyOdsCatalogError,
    verify_daily_ods_catalog,
)


class OdsShadowPruneError(RuntimeError):
    """Raised when retention cannot prove that a deletion is safe."""


class _DownstreamNotReady(OdsShadowPruneError):
    """Raised internally when a valid release has not passed its downstream gate."""


_SUCCESS_STATUSES = {"ready", "closed_session"}
_DAILY_LANES = {"market-daily-core", "calendar-daily-events", "intraday-archive"}
_KNOWN_LOCKS = {
    "market-daily-core.lock",
    "calendar-daily-events.lock",
    "weekly-corrections.lock",
    "intraday-1m.lock",
    "intraday-5m.lock",
    "resource-group-pve_market_api.lock",
    "resource-group-pve_event_api.lock",
    "resource-group-pve_bulk_io.lock",
}


@dataclass(frozen=True)
class RetentionPolicy:
    failed_bulk_days: int = 3
    audit_days: int = 90

    def __post_init__(self) -> None:
        if self.failed_bulk_days < 1:
            raise ValueError("failed_bulk_days must be positive")
        if self.audit_days <= self.failed_bulk_days:
            raise ValueError("audit_days must exceed failed_bulk_days")


@dataclass(frozen=True)
class PruneAction:
    kind: str
    path: Path
    reason: str
    logical_bytes: int
    reclaimable_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "reason": self.reason,
            "logical_bytes": self.logical_bytes,
            "reclaimable_bytes": self.reclaimable_bytes,
        }


@dataclass(frozen=True)
class PruneSkip:
    path: Path
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": str(self.path), "reason": self.reason}


@dataclass(frozen=True)
class PruneReport:
    shadow_root: Path
    mode: str
    policy: RetentionPolicy
    actions: tuple[PruneAction, ...]
    skipped: tuple[PruneSkip, ...]

    @property
    def logical_bytes(self) -> int:
        return sum(item.logical_bytes for item in self.actions)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.reclaimable_bytes for item in self.actions)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.kind] = counts.get(action.kind, 0) + 1
        return {
            "schema_version": 1,
            "kind": "ods_shadow_prune",
            "mode": self.mode,
            "shadow_root": str(self.shadow_root),
            "policy": {
                "failed_bulk_days": self.policy.failed_bulk_days,
                "audit_days": self.policy.audit_days,
            },
            "summary": {
                "action_count": len(self.actions),
                "skipped_count": len(self.skipped),
                "counts": counts,
                "logical_bytes": self.logical_bytes,
                "reclaimable_bytes": self.reclaimable_bytes,
            },
            "protected_roots": [
                str(self.shadow_root / "releases"),
                str(self.shadow_root / "catalogs"),
            ],
            "actions": [item.to_dict() for item in self.actions],
            "skipped": [item.to_dict() for item in self.skipped],
        }


@dataclass(frozen=True)
class _Attempt:
    root: Path
    lane_id: str
    stream: str | None
    market_date: date
    attempt_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    release_dir: Path | None

    @property
    def reference_time(self) -> datetime:
        return self.finished_at or self.started_at


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsShadowPruneError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise OdsShadowPruneError(f"{label} must be a JSON object: {path}")
    return payload


def _timestamp(value: object, *, field: str, path: Path) -> datetime:
    if not isinstance(value, str) or not value:
        raise OdsShadowPruneError(f"{field} is missing in {path}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OdsShadowPruneError(f"{field} is invalid in {path}") from exc
    if parsed.tzinfo is None:
        raise OdsShadowPruneError(f"{field} must be timezone-aware in {path}")
    return parsed.astimezone(timezone.utc)


def _inside(path: Path, root: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise OdsShadowPruneError(f"{label} must not be a symlink: {path}")
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise OdsShadowPruneError(f"{label} escapes its protected subtree: {path}")
    return resolved


def _validate_shadow_root(path: Path) -> Path:
    if path.is_symlink():
        raise OdsShadowPruneError(f"shadow root must not be a symlink: {path}")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise OdsShadowPruneError(f"shadow root does not exist: {path}") from exc
    if root == Path(root.anchor) or "shadow" not in {part.lower() for part in root.parts}:
        raise OdsShadowPruneError(f"refusing unsafe shadow root: {root}")
    attempts = root / "attempts"
    releases = root / "releases"
    if not attempts.is_dir() or attempts.is_symlink():
        raise OdsShadowPruneError(f"attempts root is missing or unsafe: {attempts}")
    if not releases.is_dir() or releases.is_symlink():
        raise OdsShadowPruneError(f"releases root is missing or unsafe: {releases}")
    return root


def _load_attempt(path: Path, *, attempts_root: Path, releases_root: Path) -> _Attempt:
    root = _inside(path.parent, attempts_root, label="attempt directory")
    payload = _object(path, label="attempt metadata")
    declared_root = payload.get("attempt_dir")
    if not isinstance(declared_root, str) or Path(declared_root).resolve() != root:
        raise OdsShadowPruneError(f"attempt_dir does not bind its directory: {path}")
    lane_id = payload.get("lane_id")
    attempt_id = payload.get("attempt_id")
    status = payload.get("status")
    market_date_value = payload.get("market_date")
    if not isinstance(lane_id, str) or not lane_id:
        raise OdsShadowPruneError(f"attempt lane_id is incomplete: {path}")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise OdsShadowPruneError(f"attempt_id is incomplete: {path}")
    if not isinstance(status, str) or not status:
        raise OdsShadowPruneError(f"attempt identity is incomplete: {path}")
    if root.name != attempt_id:
        raise OdsShadowPruneError(f"attempt_id does not match its directory: {path}")
    try:
        market_date = date.fromisoformat(str(market_date_value))
    except ValueError as exc:
        raise OdsShadowPruneError(f"market_date is invalid in {path}") from exc
    stream_value = payload.get("stream")
    if stream_value is not None and not isinstance(stream_value, str):
        raise OdsShadowPruneError(f"stream is invalid in {path}")
    finished_value = payload.get("finished_at")
    finished_at = (
        None
        if finished_value is None
        else _timestamp(finished_value, field="finished_at", path=path)
    )
    release_value = payload.get("release_dir")
    release_dir: Path | None = None
    if release_value is not None:
        if not isinstance(release_value, str) or not release_value:
            raise OdsShadowPruneError(f"release_dir is invalid in {path}")
        release_dir = _inside(Path(release_value), releases_root, label="attempt release")
    return _Attempt(
        root=root,
        lane_id=lane_id,
        stream=stream_value,
        market_date=market_date,
        attempt_id=attempt_id,
        status=status,
        started_at=_timestamp(payload.get("started_at"), field="started_at", path=path),
        finished_at=finished_at,
        release_dir=release_dir,
    )


def _tree_usage(path: Path) -> tuple[int, int]:
    inodes: dict[tuple[int, int], tuple[int, int, int]] = {}
    logical = 0
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        directories[:] = [name for name in directories if not (root_path / name).is_symlink()]
        for name in files:
            item = root_path / name
            if item.is_symlink():
                continue
            try:
                stat = item.stat()
            except FileNotFoundError:
                continue
            logical += stat.st_size
            key = (stat.st_dev, stat.st_ino)
            size, seen, links = inodes.get(key, (stat.st_size, 0, stat.st_nlink))
            inodes[key] = (size, seen + 1, links)
    reclaimable = sum(size for size, seen, links in inodes.values() if seen >= links)
    return logical, reclaimable


def _action(kind: str, path: Path, reason: str) -> PruneAction:
    logical, reclaimable = _tree_usage(path)
    return PruneAction(
        kind=kind,
        path=path,
        reason=reason,
        logical_bytes=logical,
        reclaimable_bytes=reclaimable,
    )


def _verified_run_state(
    path: Path, *, shadow_root: Path, market_date: date
) -> dict[str, Any] | None:
    ready = path.parent / "READY"
    try:
        if not ready.is_file() or ready.read_text(encoding="ascii").strip() != _sha256(path):
            return None
        state = _object(path, label="orchestrator run state")
    except (OdsShadowPruneError, OSError):
        return None
    if (
        state.get("kind") != "ods_daily_orchestration"
        or state.get("mode") != "ods_to_dwd"
        or state.get("market_date") != market_date.isoformat()
        or state.get("status") != "ready"
    ):
        return None
    dwd = state.get("dwd")
    catalog = state.get("catalog")
    if not isinstance(dwd, Mapping) or dwd.get("status") != "ready":
        return None
    if not isinstance(catalog, Mapping) or catalog.get("status") != "ready":
        return None
    declared_shadow = state.get("shadow_root")
    if not isinstance(declared_shadow, str) or Path(declared_shadow).resolve() != shadow_root:
        return None
    return state


def _accepted_daily_releases(shadow_root: Path, market_date: date) -> dict[Path, tuple[str, str]]:
    accepted: dict[Path, tuple[str, str]] = {}
    date_root = shadow_root / "orchestrator" / "daily" / market_date.isoformat()
    if not date_root.is_dir() or date_root.is_symlink():
        return accepted
    catalogs_root = shadow_root / "catalogs"
    releases_root = shadow_root / "releases"
    for state_path in sorted(date_root.glob("*/run.json")):
        state = _verified_run_state(state_path, shadow_root=shadow_root, market_date=market_date)
        if state is None:
            continue
        catalog_state = state["catalog"]
        assert isinstance(catalog_state, Mapping)
        catalog_value = catalog_state.get("root")
        if not isinstance(catalog_value, str):
            continue
        try:
            catalog_root = _inside(Path(catalog_value), catalogs_root, label="daily catalog")
            verified_catalog = verify_daily_ods_catalog(catalog_root)
            if verified_catalog.market_date != market_date:
                continue
            catalog = _object(catalog_root / "catalog.json", label="daily ODS catalog")
        except (OdsShadowPruneError, DailyOdsCatalogError, OSError, ValueError):
            continue
        lanes = catalog.get("lanes")
        if not isinstance(lanes, list):
            continue
        for lane in lanes:
            if not isinstance(lane, Mapping):
                continue
            relative = lane.get("release_path")
            release_id = lane.get("release_id")
            manifest_sha = lane.get("release_manifest_sha256")
            if not isinstance(relative, str) or not relative:
                continue
            if not isinstance(release_id, str) or not release_id:
                continue
            if not isinstance(manifest_sha, str) or not manifest_sha:
                continue
            try:
                release_path = _inside(
                    releases_root / relative, releases_root, label="catalog release"
                )
            except OdsShadowPruneError:
                continue
            accepted[release_path] = (release_id, manifest_sha)
    return accepted


def _verify_successful_attempt(
    attempt: _Attempt,
    *,
    shadow_root: Path,
    daily_cache: dict[date, dict[Path, tuple[str, str]]],
) -> OdsVerificationResult:
    if attempt.status not in _SUCCESS_STATUSES or attempt.release_dir is None:
        raise OdsShadowPruneError(f"attempt is not a completed publication: {attempt.root}")
    try:
        verified = verify_ods_release(attempt.release_dir)
    except OdsReleaseError as exc:
        raise OdsShadowPruneError(
            f"successful attempt release verification failed: {attempt.release_dir}: {exc}"
        ) from exc
    release = verified.release
    expected_prefix = f"hermes-{attempt.lane_id}"
    if attempt.stream:
        expected_prefix += f"-{attempt.stream}"
    if (
        release.market_date != attempt.market_date
        or not release.release_id.startswith(expected_prefix + "-")
        or verified.release_dir.name != attempt.attempt_id
    ):
        raise OdsShadowPruneError(f"attempt/release identity differs: {attempt.root}")
    if attempt.lane_id in _DAILY_LANES:
        accepted = daily_cache.setdefault(
            attempt.market_date,
            _accepted_daily_releases(shadow_root, attempt.market_date),
        )
        binding = accepted.get(verified.release_dir)
        if binding != (release.release_id, verified.manifest_sha256):
            raise _DownstreamNotReady(
                "daily attempt release is not bound to a verified successful DWD catalog: "
                f"{attempt.root}"
            )
    elif attempt.lane_id == "weekly-corrections":
        if release.schedule != "weekly":
            raise OdsShadowPruneError(
                f"weekly attempt published a non-weekly release: {attempt.root}"
            )
    else:
        raise OdsShadowPruneError(f"unsupported successful lane: {attempt.lane_id}")
    return verified


def _checkpoint_roots(provider_root: Path) -> Iterator[tuple[str, str, date, Path]]:
    if not provider_root.exists():
        return
    if provider_root.is_symlink() or not provider_root.is_dir():
        raise OdsShadowPruneError(f"provider checkpoints root is unsafe: {provider_root}")
    for lane_root in sorted(provider_root.iterdir()):
        if not lane_root.is_dir() or lane_root.is_symlink():
            continue
        for dataset_root in sorted(lane_root.iterdir()):
            if not dataset_root.is_dir() or dataset_root.is_symlink():
                continue
            for date_root in sorted(dataset_root.iterdir()):
                if not date_root.is_dir() or date_root.is_symlink():
                    continue
                try:
                    market_date = date.fromisoformat(date_root.name)
                except ValueError:
                    continue
                yield lane_root.name, dataset_root.name, market_date, date_root.resolve()


def _active_orchestrator_state(shadow_root: Path) -> None:
    context = shadow_root / "orchestrator" / "daily" / "notification-context.json"
    if not context.is_file():
        return
    payload = _object(context, label="orchestrator notification context")
    if payload.get("status") == "running":
        raise OdsShadowPruneError(f"daily orchestrator reports an active run: {context}")


def _assert_units_inactive(active_units: Sequence[str]) -> None:
    for unit in active_units:
        completed = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            raise OdsShadowPruneError(f"refusing prune while systemd unit is active: {unit}")
        if completed.returncode not in {3, 4}:
            raise OdsShadowPruneError(f"cannot establish systemd unit state: {unit}")


@contextmanager
def _lock_guard(shadow_root: Path, *, apply: bool, active_units: Sequence[str]) -> Iterator[None]:
    _active_orchestrator_state(shadow_root)
    if active_units:
        _assert_units_inactive(active_units)
    lock_root = shadow_root / "locks"
    handles: list[Any] = []
    try:
        paths = set(lock_root.glob("*.lock")) if lock_root.is_dir() else set()
        if apply:
            lock_root.mkdir(parents=True, exist_ok=True)
            paths.update(lock_root / name for name in _KNOWN_LOCKS)
            paths.add(lock_root / "ods-shadow-prune.lock")
        for path in sorted(paths):
            if path.is_symlink():
                raise OdsShadowPruneError(f"lane lock must not be a symlink: {path}")
            mode = "a+" if apply else "r+"
            handle = path.open(mode, encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise OdsShadowPruneError(f"ODS lane lock is active: {path}") from exc
            handles.append(handle)
        _active_orchestrator_state(shadow_root)
        if active_units:
            _assert_units_inactive(active_units)
        yield
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _plan(
    shadow_root: Path, *, now: datetime, policy: RetentionPolicy
) -> tuple[list[PruneAction], list[PruneSkip]]:
    attempts_root = shadow_root / "attempts"
    releases_root = shadow_root / "releases"
    attempts: list[_Attempt] = []
    by_lane_date: dict[tuple[str, date], list[_Attempt]] = {}
    for metadata in sorted(attempts_root.rglob("attempt.json")):
        if metadata.is_symlink():
            raise OdsShadowPruneError(f"attempt metadata must not be a symlink: {metadata}")
        attempt = _load_attempt(metadata, attempts_root=attempts_root, releases_root=releases_root)
        attempts.append(attempt)
        by_lane_date.setdefault((attempt.lane_id, attempt.market_date), []).append(attempt)

    failed_cutoff = now - timedelta(days=policy.failed_bulk_days)
    audit_cutoff = now - timedelta(days=policy.audit_days)
    actions: list[PruneAction] = []
    skipped: list[PruneSkip] = []
    daily_cache: dict[date, dict[Path, tuple[str, str]]] = {}
    verified_attempts: set[Path] = set()

    def verify(attempt: _Attempt) -> None:
        if attempt.root in verified_attempts:
            return
        _verify_successful_attempt(attempt, shadow_root=shadow_root, daily_cache=daily_cache)
        verified_attempts.add(attempt.root)

    for attempt in attempts:
        landing = attempt.root / "landing"
        if landing.exists():
            landing = _inside(landing, attempt.root, label="attempt landing")
            if not landing.is_dir():
                raise OdsShadowPruneError(f"attempt landing is not a directory: {landing}")
            if attempt.status in _SUCCESS_STATUSES:
                try:
                    verify(attempt)
                except _DownstreamNotReady as exc:
                    skipped.append(PruneSkip(path=landing, reason=str(exc)))
                    continue
                actions.append(
                    _action(
                        "successful_attempt_bulk",
                        landing,
                        "release verified and downstream publication gate satisfied",
                    )
                )
            elif attempt.reference_time <= failed_cutoff:
                actions.append(
                    _action(
                        "abandoned_attempt_bulk",
                        landing,
                        f"non-success attempt bulk exceeded {policy.failed_bulk_days}-day grace",
                    )
                )
            continue
        if attempt.reference_time <= audit_cutoff:
            actions.append(
                _action(
                    "expired_attempt_audit",
                    attempt.root,
                    f"compact attempt audit exceeded {policy.audit_days}-day retention",
                )
            )

    provider_root = shadow_root / "provider-checkpoints"
    for lane_id, dataset, market_date, checkpoint in _checkpoint_roots(provider_root):
        records = by_lane_date.get((lane_id, market_date), [])
        latest = max(records, key=lambda item: item.reference_time) if records else None
        if latest is not None and latest.status in _SUCCESS_STATUSES:
            try:
                verify(latest)
            except _DownstreamNotReady:
                skipped.append(
                    PruneSkip(
                        path=checkpoint,
                        reason="latest successful attempt has not passed the downstream gate",
                    )
                )
            else:
                actions.append(
                    _action(
                        "completed_provider_checkpoint",
                        checkpoint,
                        f"{lane_id}/{dataset}/{market_date.isoformat()} has a verified release",
                    )
                )
            continue
        if latest is not None and latest.reference_time <= failed_cutoff:
            actions.append(
                _action(
                    "abandoned_provider_checkpoint",
                    checkpoint,
                    f"latest non-success attempt exceeded {policy.failed_bulk_days}-day grace",
                )
            )
    return (
        sorted(actions, key=lambda item: (str(item.path), item.kind)),
        sorted(skipped, key=lambda item: str(item.path)),
    )


def prune_ods_shadow(
    shadow_root: Path,
    *,
    apply: bool = False,
    now: datetime | None = None,
    policy: RetentionPolicy | None = None,
    active_units: Sequence[str] = (),
) -> PruneReport:
    """Plan or apply safe ODS shadow retention.

    Dry-run is the default. Apply mode holds every lane/resource lock throughout
    verification and deletion so a provider cannot start inside a prune window.
    """

    root = _validate_shadow_root(shadow_root)
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)
    resolved_policy = policy or RetentionPolicy()
    with _lock_guard(root, apply=apply, active_units=active_units):
        actions, skipped = _plan(root, now=observed_at, policy=resolved_policy)
        if apply:
            for action in actions:
                allowed_root = (
                    root / "attempts"
                    if action.kind
                    in {
                        "successful_attempt_bulk",
                        "abandoned_attempt_bulk",
                        "expired_attempt_audit",
                    }
                    else root / "provider-checkpoints"
                )
                target = _inside(action.path, allowed_root, label="prune target")
                if not target.is_dir():
                    raise OdsShadowPruneError(f"prune target changed before deletion: {target}")
                shutil.rmtree(target)
    return PruneReport(
        shadow_root=root,
        mode="apply" if apply else "dry_run",
        policy=resolved_policy,
        actions=tuple(actions),
        skipped=tuple(skipped),
    )


def _active_units(node_id: str) -> tuple[str, ...]:
    return (
        f"{node_id}-ods-daily-orchestrator.service",
        f"{node_id}-ods-weekly-corrections.service",
        f"{node_id}-dwd-weekly-input.service",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-root", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--failed-bulk-days", type=int, default=3)
    parser.add_argument("--audit-days", type=int, default=90)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete eligible data; without this flag the command is read-only",
    )
    args = parser.parse_args(argv)
    if not args.node_id or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in args.node_id
    ):
        parser.error("--node-id must be a normalized systemd node id")
    try:
        report = prune_ods_shadow(
            args.shadow_root,
            apply=args.apply,
            policy=RetentionPolicy(
                failed_bulk_days=args.failed_bulk_days,
                audit_days=args.audit_days,
            ),
            active_units=_active_units(args.node_id),
        )
    except (OdsShadowPruneError, OSError, ValueError) as exc:
        print(f"ODS shadow prune refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
