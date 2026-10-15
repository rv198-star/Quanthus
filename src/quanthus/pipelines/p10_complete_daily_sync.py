"""Exact OCI mirror through the existing CN01 read-only DWD SSH jail."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_complete_daily import publish, verify
from quanthus.pipelines.p10_complete_daily_cycle import calendar_rows
from quanthus.pipelines.p10_complete_daily_notifications import failure
from quanthus.pipelines.p10_complete_daily_schedule import CST, checkpoint, policy, target_for
from quanthus.pipelines.p10_delivery_common import COMPLETE_POINTER as POINTER
from quanthus.pipelines.p10_delivery_common import atomic, file_digest, object_at, safe_path
from quanthus.warehouse_sync import WarehouseSyncError, _run_rsync, _ssh_command

REMOTE = "products/complete-daily"
LOCAL = Path("/home/ubuntu/data/quanthus/dwd-complete-daily")
CACHE = ".complete-sync-cache-v1"


def seed_verified_files(root: Path, package: Path, manifest: dict[str, Any]) -> None:
    """Seed independent exact files; rsync metadata must not mutate a prior package."""
    pointer = root / POINTER
    candidates = []
    if pointer.exists():
        candidates.append(safe_path(root, object_at(pointer)["manifest_path"]).parent)
    # Only inspect this job's bounded package cache, never arbitrary local directories.
    cached = sorted((root / CACHE / "packages").glob("*/*/manifest.json"))
    if len(cached) > 28:
        raise ValueError("private mirror cache exceeds bounded predecessor budget")
    candidates.extend(path.parent for path in cached)
    for item in manifest["files"]:
        relative = item["path"]
        target = safe_path(package, relative)
        # Old resumable jobs may already contain links made by the previous
        # implementation, including rsync's delayed partial files.
        partial_target = safe_path(
            package, (Path(relative).parent / ".rsync-partial" / Path(relative).name).as_posix()
        )
        for existing in (target, partial_target):
            if existing.exists() and existing.stat().st_nlink > 1:
                if existing.stat().st_size > item["bytes"]:
                    raise ValueError("shared mirror partial exceeds declared file budget")
                if shutil.disk_usage(existing.parent).free < existing.stat().st_size + 4 * 1024**3:
                    raise ValueError("mirror detachment lacks file bytes plus 4 GiB reserve")
                with tempfile.NamedTemporaryFile(
                    prefix=".seed-independent-", dir=existing.parent, delete=False
                ) as handle:
                    independent = Path(handle.name)
                try:
                    shutil.copy2(existing, independent)
                    os.replace(independent, existing)
                finally:
                    independent.unlink(missing_ok=True)
        if target.exists():
            continue
        for candidate in candidates:
            source = safe_path(candidate, relative)
            if not source.is_file():
                # --delay-updates retains even complete files here after timeout.
                partial = Path(relative).parent / ".rsync-partial" / Path(relative).name
                source = safe_path(candidate, partial.as_posix())
            if not source.is_file():
                continue
            if source.stat().st_size != item["bytes"] or file_digest(source) != item["sha256"]:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # rsync -a can update timestamps, ownership and permissions even when
            # --checksum skips the payload. A hard link would mutate the sealed
            # candidate package through those metadata updates.
            if shutil.disk_usage(target.parent).free < source.stat().st_size + 4 * 1024**3:
                raise ValueError("mirror seed lacks file bytes plus 4 GiB reserve")
            shutil.copy2(source, target)
            break


def pending_target(root: Path, target: date | None) -> date | None:
    path = root / CACHE / "pending.json"
    if not path.exists():
        return target
    pending = object_at(path)
    day = date.fromisoformat(pending["market_date"])
    if pending.get("kind") != "oci_resumable_complete_sync_v1" or day < date.fromisoformat(
        policy()["baseline_market_date"]
    ):
        raise ValueError("invalid persistent mirror job")
    return day if target is None or day < target else target


def _copy_immutable(
    *, pointer: dict[str, Any], target: date, stage: Path, source: str, ssh: str
) -> tuple[Path, dict[str, Any]]:
    relative = _validate_pointer(pointer, target)
    package = safe_path(stage, relative.as_posix())
    package.mkdir(parents=True, exist_ok=True)
    _run_rsync(
        source=f"{source}/{relative.as_posix()}/manifest.json",
        destination=package / "manifest.json",
        ssh=ssh,
    )
    if file_digest(package / "manifest.json") != pointer["manifest_sha256"]:
        raise ValueError("remote complete manifest differs before payload download")
    manifest = object_at(package / "manifest.json")
    required = sum(item["bytes"] for item in manifest["files"])
    if required < 0 or shutil.disk_usage(stage).free < required + 4 * 1024**3:
        raise ValueError("OCI lacks exact package staging budget plus 4 GiB reserve")
    seed_verified_files(stage.parent, package, manifest)
    # Seeding and detaching resumable files consumed space. Keep the original
    # full transfer allowance available before rsync can allocate replacements.
    if shutil.disk_usage(stage).free < required + 4 * 1024**3:
        raise ValueError("OCI lacks post-seed transfer bytes plus 4 GiB reserve")
    _run_rsync(
        source=f"{source}/{relative.as_posix()}/",
        destination=package,
        ssh=ssh,
        extra=("--exclude=/READY", "--compress"),
    )
    _run_rsync(
        source=f"{source}/{relative.as_posix()}/READY", destination=package / "READY", ssh=ssh
    )
    actual = verify(package)
    if any(
        pointer.get(key) != actual[key]
        for key in ("package_id", "market_date", "manifest_sha256", "content_digest", "base")
    ):
        raise ValueError("copied complete package differs from exact remote pointer")
    return package, actual


def _publish_visible(root: Path, package: Path, actual: dict[str, Any]) -> dict[str, Any]:
    publish(root, package, actual)
    path = root / "visibility" / f"{actual['market_date']}-{actual['manifest_sha256'][7:23]}.json"
    if path.exists():
        previous = object_at(path)
        if (
            previous.get("manifest_sha256") != actual["manifest_sha256"]
            or previous.get("content_digest") != actual["content_digest"]
        ):
            raise ValueError("immutable OCI visibility identity differs")
        return previous
    receipt = {
        "schema_version": 1,
        "kind": "oci_complete_daily_visibility_v1",
        "market_date": actual["market_date"],
        "manifest_sha256": actual["manifest_sha256"],
        "content_digest": actual["content_digest"],
        "package_id": actual["package_id"],
        "intervals": ["1d", "1m", "5m"],
        "first_verified_visible_at": datetime.now(timezone.utc).isoformat(),
        "consumer_purpose": "gap_aware_non_execution_development",
        "execution_authorized": False,
        "exact_source_match": True,
    }
    atomic(path, receipt)
    return receipt


def _recover_prior(
    *,
    root: Path,
    pointer: dict[str, Any] | None,
    stage: Path,
    source: str,
    ssh: str,
    seen: set[str],
) -> None:
    current = object_at(root / POINTER) if (root / POINTER).exists() else None
    if pointer == current:
        return
    if pointer is None or len(seen) >= 28 or pointer["manifest_sha256"] in seen:
        raise ValueError("complete Daily predecessor chain cannot safely reach current")
    target = date.fromisoformat(pointer["market_date"])
    if current and current["market_date"] > str(target):
        raise ValueError("predecessor chain would regress OCI current")
    seen.add(pointer["manifest_sha256"])
    # Resolve the exact small manifest first. Publish oldest prerequisites one
    # at a time, so a bounded service retry resumes from durable acceptance
    # instead of repeatedly verifying all descendants before any publication.
    relative = _validate_pointer(pointer, target)
    manifest_path = safe_path(stage, (relative / "manifest.json").as_posix())
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _run_rsync(
        source=f"{source}/{relative.as_posix()}/manifest.json",
        destination=manifest_path,
        ssh=ssh,
    )
    if file_digest(manifest_path) != pointer["manifest_sha256"]:
        raise ValueError("remote predecessor manifest identity differs")
    prior = object_at(manifest_path)["prior"]
    if prior and prior["market_date"] > str(target):
        raise ValueError("predecessor date is later than its child")
    _recover_prior(root=root, pointer=prior, stage=stage, source=source, ssh=ssh, seen=seen)
    package, actual = _copy_immutable(
        pointer=pointer, target=target, stage=stage, source=source, ssh=ssh
    )
    if actual["prior"] != prior:
        raise ValueError("verified predecessor closure differs")
    destination = root / _validate_pointer(pointer, target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if verify(destination)["manifest_sha256"] != actual["manifest_sha256"]:
            raise ValueError("immutable OCI predecessor conflict")
    else:
        os.replace(package, destination)
    _publish_visible(root, destination, actual)


def _validate_pointer(pointer: dict[str, Any], target: date) -> Path:
    path = Path(pointer.get("manifest_path", ""))
    if (
        pointer.get("kind") != "cn01_complete_daily_dwd_current"
        or pointer.get("market_date") != str(target)
        or path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 4
        or path.parts[0] != "packages"
        or path.parts[1] != str(target)
        or path.parts[2] != pointer.get("package_id")
        or path.parts[3] != "manifest.json"
    ):
        raise ValueError("remote complete Daily pointer identity/path differs")
    return path.parent


def mirror_mail_context(root: Path, target: date) -> dict[str, Any]:
    """Describe the failed delivery separately from upstream source declarations.

    This reads only the exact cached manifest; it is not receiver acceptance.
    """
    context: dict[str, Any] = {
        "source_complete_status": "上游补数及构建状态未取得可绑定证据",
        "mirror_stage_status": "本次OCI同步流程未成功结束",
        "consumer_admission_status": "消费放行以本端精确验收回执为准；本邮件不构成放行",
    }
    try:
        pending = object_at(root / CACHE / "pending.json")
        if pending.get("market_date") != str(target):
            return context
        pointer = pending["source_pointer"]
        if not isinstance(pointer, dict):
            return context
        relative = _validate_pointer(pointer, target)
        path = safe_path(root / CACHE, relative.as_posix()) / "manifest.json"
        manifest = object_at(path)
        if (
            file_digest(path) != pointer["manifest_sha256"]
            or manifest.get("market_date") != str(target)
            or manifest.get("content_digest") != pointer["content_digest"]
        ):
            return context
        context.update(
            source_complete_status="上游完整包已发布；此处仅展示来源声明，不代表 OCI 本端验收状态",
            source_manifest_sha256=pointer["manifest_sha256"],
            source_content_digest=pointer["content_digest"],
            source_minute_quality=str(manifest.get("minute_quality_grades", {})),
        )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return context


def sync(*, root: Path, target: date) -> dict[str, Any]:
    if os.environ.get("QUANTHUS_CN01_DWD_REMOTE_ROOT") != REMOTE:
        raise ValueError("complete mirror requires exact existing read-only jail subroot")
    if (root / "CURRENT.json").exists() or (root / "CURRENT_DAILY_CORE.json").exists():
        raise ValueError("complete mirror cannot overwrite legacy/daily-core product")
    root.mkdir(parents=True, exist_ok=True)
    host, _, user, _, _, ssh = _ssh_command()
    source = f"{user}@{host}:{REMOTE}"
    with (root / ".mirror.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        stage = safe_path(root, CACHE)
        stage.mkdir(mode=0o700, exist_ok=True)
        try:
            incoming = stage / "pointer.json"
            try:
                _run_rsync(
                    source=f"{source}/date-pointers/{target}.json", destination=incoming, ssh=ssh
                )
            except WarehouseSyncError as exc:
                if "No such file or directory" in str(exc):
                    return {"status": "waiting_for_cn01_complete_daily", "market_date": str(target)}
                raise
            pointer = object_at(incoming)
            relative = _validate_pointer(pointer, target)
            atomic(
                stage / "pending.json",
                {
                    "kind": "oci_resumable_complete_sync_v1",
                    "market_date": str(target),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "source_pointer": pointer,
                },
            )
            existing = root / "date-pointers" / f"{target}.json"
            visibility = root / "visibility" / f"{target}-{pointer['manifest_sha256'][7:23]}.json"
            if existing.exists() and object_at(existing) == pointer and visibility.exists():
                # Reuse unchanged acceptance instead of rehashing every poll.
                receipt = object_at(visibility)
                if receipt.get("manifest_sha256") == pointer["manifest_sha256"]:
                    (stage / "pending.json").unlink(missing_ok=True)
                    return {"status": "already_verified", **receipt}
            package, actual = _copy_immutable(
                pointer=pointer, target=target, stage=stage, source=source, ssh=ssh
            )
            current = object_at(root / POINTER) if (root / POINTER).exists() else None
            if current and current["market_date"] > str(target):
                raise ValueError("OCI complete Daily cannot regress")
            if current != pointer and actual["prior"] != current:
                _recover_prior(
                    root=root,
                    pointer=actual["prior"],
                    stage=stage,
                    source=source,
                    ssh=ssh,
                    seen={actual["manifest_sha256"]},
                )
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                prior = verify(destination)
                if prior["manifest_sha256"] != actual["manifest_sha256"]:
                    raise ValueError("immutable OCI package conflict")
            else:
                os.replace(package, destination)
            receipt = _publish_visible(root, destination, actual)
            (stage / "pending.json").unlink(missing_ok=True)
            return {"status": "synced", **receipt, "minute_row_counts": actual["minute_row_counts"]}
        finally:
            # Interrupted transfers keep their private partials, never a consumer pointer.
            (stage / "pointer.json").unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-root", type=Path, default=LOCAL)
    parser.add_argument("--market-date", type=date.fromisoformat)
    parser.add_argument("--calendar-file", type=Path)
    parser.add_argument("--notification-state", type=Path, default=LOCAL / "notifications")
    args = parser.parse_args()
    now = datetime.now(CST)
    target = args.market_date
    try:
        rows = (
            json.loads(args.calendar_file.read_text())
            if args.calendar_file
            else calendar_rows(now.date(), args.local_root / "calendars")
        )
        target = target or pending_target(args.local_root, target_for(now, rows))
        if target is None or target < date.fromisoformat(policy()["baseline_market_date"]):
            result = {"status": "no_eligible_trading_day"}
        elif now < checkpoint(target, "minute_start", rows):
            result = {"status": "waiting_for_minute_start", "market_date": str(target)}
        else:
            if shutil.disk_usage(args.local_root.parent).free < 4 * 1024**3:
                raise ValueError("OCI mirror lacks 4 GiB disk reserve")
            result = sync(root=args.local_root, target=target)
            if result["status"] == "waiting_for_cn01_complete_daily" and now >= checkpoint(
                target, "mirror_deadline", rows
            ):
                failure(
                    day=target,
                    stage="mirror",
                    state_root=args.notification_state,
                    reason="exact qualified CN01 complete DWD absent at T+1 14:00 deadline",
                    context={"deadline": checkpoint(target, "mirror_deadline", rows).isoformat()},
                )
    except Exception as exc:
        failure(
            day=target or now.date(),
            stage="mirror",
            state_root=args.notification_state,
            reason=f"complete DWD synchronization failed: {type(exc).__name__}",
            context={
                "market_date_label": "交易日" if target else "运行日期（目标交易日未确定）",
                **(mirror_mail_context(args.local_root, target) if target else {}),
            },
        )
        raise
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
