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
from quanthus.pipelines.p10_delivery_common import atomic, file_digest, object_at
from quanthus.warehouse_sync import WarehouseSyncError, _run_rsync, _ssh_command

REMOTE = "products/complete-daily"
LOCAL = Path("/home/ubuntu/data/quanthus/dwd-complete-daily")


def _copy_immutable(
    *, pointer: dict[str, Any], target: date, stage: Path, source: str, ssh: str
) -> tuple[Path, dict[str, Any]]:
    relative = _validate_pointer(pointer, target)
    package = stage / relative
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
    _run_rsync(
        source=f"{source}/{relative.as_posix()}/",
        destination=package,
        ssh=ssh,
        extra=("--exclude=/READY",),
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
    package, actual = _copy_immutable(
        pointer=pointer, target=target, stage=stage, source=source, ssh=ssh
    )
    prior = actual["prior"]
    if prior and prior["market_date"] > str(target):
        raise ValueError("predecessor date is later than its child")
    _recover_prior(root=root, pointer=prior, stage=stage, source=source, ssh=ssh, seen=seen)
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
        stage = Path(tempfile.mkdtemp(prefix=".complete-sync-", dir=root))
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
            existing = root / "date-pointers" / f"{target}.json"
            visibility = root / "visibility" / f"{target}-{pointer['manifest_sha256'][7:23]}.json"
            if existing.exists() and object_at(existing) == pointer and visibility.exists():
                # Reuse unchanged acceptance instead of rehashing every poll.
                receipt = object_at(visibility)
                if receipt.get("manifest_sha256") == pointer["manifest_sha256"]:
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
            return {"status": "synced", **receipt, "minute_row_counts": actual["minute_row_counts"]}
        finally:
            shutil.rmtree(stage, ignore_errors=True)


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
        target = target or target_for(now, rows)
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
            context={"market_date_label": "交易日" if target else "运行日期（目标交易日未确定）"},
        )
        raise
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
