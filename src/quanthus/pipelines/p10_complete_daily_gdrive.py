"""Archive an already admitted complete Daily; independent from OCI delivery.

The dated pointer and CN01 semantic receipt are reused by exact identity. Only
physical checksums are reread. No Base scan, source acquisition or rebuilding.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_complete_daily import accepted
from quanthus.pipelines.p10_complete_daily_notifications import failure
from quanthus.pipelines.p10_complete_daily_schedule import CST
from quanthus.pipelines.p10_delivery_common import (
    BASE23_CONTENT,
    BASE23_MANIFEST,
    COMPLETE_KIND,
    atomic,
    digest,
    encoded,
    file_digest,
    object_at,
    require_revision,
    safe_path,
    verify_seal,
)
from quanthus.pipelines.p10_gdrive_incremental_archive import delta_reference
from quanthus.policy import find_project_root

SOURCE = Path("/srv/quanthus/dwd-dws-v2/daily/products/complete-daily")
STATE = Path("/var/lib/quanthus/gdrive-complete-v4")
# The notification key is global per target day/stage, so all archive revisions
# must share its durable request, lock and actual provider receipt.
MAIL_STATE = Path("/var/lib/quanthus/gdrive-complete-v1/notifications")
CONFIG = Path("/etc/quanthus/gdrive-complete-v1/rclone.conf")
POLICY_PATH = find_project_root() / "config/pipelines/p10-complete-daily-gdrive.v1.json"


class ArchiveError(RuntimeError):
    """Safe error code only; do not log credentials, API responses or market rows."""


def check_archive_space(state: Path, run: Path, upper_bound: int, reserve: int) -> None:
    """Tar uses the run device; local control/mail state retains its own reserve."""
    if shutil.disk_usage(run).free < reserve + upper_bound:
        raise ArchiveError("archive_scratch_insufficient_with_4GiB_reserve")
    if shutil.disk_usage(state).free < reserve:
        raise ArchiveError("archive_control_state_insufficient_with_4GiB_reserve")


def md5(path: Path) -> str:
    value = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def admitted(source: Path, day: date) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    receipt = accepted(source, day)
    pointer = object_at(source / "date-pointers" / f"{day}.json")
    if receipt["manifest_sha256"] != pointer["manifest_sha256"]:
        raise ArchiveError("source_revision_changed_retry")
    package = safe_path(source, pointer["manifest_path"]).parent
    manifest = verify_seal(package, COMPLETE_KIND)
    if (
        receipt["content_digest"] != manifest["content_digest"]
        or manifest.get("base", {}).get("manifest_sha256") != BASE23_MANIFEST
        or manifest.get("base", {}).get("content_digest") != BASE23_CONTENT
        or manifest.get("consumable_intervals") != ["1d", "1m", "5m"]
        or any(manifest["minute_quality_grades"][i].get("R3", 0) for i in ("1m", "5m"))
    ):
        raise ArchiveError("complete_source_admission_differs")
    return package, pointer, receipt


def freeze(
    source: Path,
    day: date,
    state: Path,
    revision: str,
    policy: dict[str, Any],
    *,
    expected_manifest: str | None = None,
    job_until: float | None = None,
) -> tuple[Path, dict[str, Any]]:
    package, pointer, receipt = admitted(source, day)
    if expected_manifest is not None and pointer["manifest_sha256"] != expected_manifest:
        raise ArchiveError("source_revision_changed_retry")
    key = pointer["manifest_sha256"][7:]
    run = state / "runs" / key
    reference, previous_files = delta_reference(source, state, policy, str(day))
    identity = {
        "market_date": str(day),
        "manifest_sha256": pointer["manifest_sha256"],
        "content_digest": pointer["content_digest"],
        "archive_policy_sha256": file_digest(POLICY_PATH),
        "effective_archive_policy_sha256": digest(encoded(policy)),
        "archive_code_revision": revision,
        "archive_reference": reference,
    }
    if (run / "archive.json").exists():
        saved = object_at(run / "archive.json")
        if any(saved[k] != value for k, value in identity.items()):
            raise ArchiveError("frozen_archive_identity_changed_explicit_revision_required")
        upload = run / "upload"
        if file_digest(upload / saved["archive_name"]) != saved["archive_sha256"]:
            raise ArchiveError("frozen_archive_checksum_differs")
        return upload, saved
    paths = sorted(p for p in package.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in paths)
    source_manifest = object_at(package / "manifest.json")
    inherited = [
        item for item in source_manifest["files"] if previous_files.get(item["path"]) == item
    ]
    inherited_paths = {item["path"] for item in inherited}
    packed_paths = [p for p in paths if p.relative_to(package).as_posix() not in inherited_paths]
    packed_bytes = sum(p.stat().st_size for p in packed_paths)
    # Tar overhead is bounded before writing; no second full source copy.
    upper_bound = size + len(paths) * 8192 + 1024 * 1024
    if size > policy["max_package_bytes"] or upper_bound > policy["max_archive_bytes"]:
        raise ArchiveError("complete_package_exceeds_archive_budget")
    run.mkdir(parents=True, exist_ok=True)
    check_archive_space(state, run, upper_bound, policy["disk_reserve_bytes"])
    upload = run / "upload"
    upload.mkdir(exist_ok=True)
    archive_name = f"dwd-complete-{day}-{key[:16]}.tar.zst"
    archive = upload / archive_name
    incoming = run / "archive.incoming"
    file_list = run / ".tar-files"
    file_list.write_bytes(
        b"".join(p.relative_to(package).as_posix().encode() + b"\0" for p in packed_paths)
    )
    command = [
        "tar",
        "--sort=name",
        "--numeric-owner",
        "--owner=0",
        "--group=0",
        "--mtime=@0",
        "--hard-dereference",
        "-I",
        "zstd -T1 -3",
        "-cf",
        str(incoming),
        "-C",
        str(package),
        "--no-recursion",
        "--null",
        "--files-from",
        str(file_list),
    ]

    def remaining() -> int:
        seconds = int(policy["command_timeout_seconds"])
        if job_until is not None:
            seconds = min(seconds, int(job_until - time.monotonic()))
        if seconds < 1:
            raise ArchiveError("archive_job_budget_exhausted")
        return seconds

    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=remaining(),
        check=False,
    )
    if completed.returncode:
        raise ArchiveError("archive_pack_failed")
    if incoming.stat().st_size > policy["max_archive_bytes"]:
        incoming.unlink()
        raise ArchiveError("archive_exceeds_budget")
    checked = subprocess.run(
        ["tar", "--zstd", "-tf", str(incoming)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=remaining(),
        check=False,
    )
    if checked.returncode:
        raise ArchiveError("archive_structure_invalid")
    # Source is immutable. A changed byte during packing fails before upload.
    remaining()
    verify_seal(package, COMPLETE_KIND)
    incoming.replace(archive)
    prefix = policy["remote_prefix"].strip("/")
    remote = f"gdrive:{prefix + '/' if prefix else ''}{day}/{key}"
    report = {
        **identity,
        "policy_id": policy["policy_id"],
        "base": receipt["base"],
        "consumable_intervals": receipt["consumable_intervals"],
        "minute_quality_grades": receipt["minute_quality_grades"],
        "source_code_revision": receipt["code_revision"],
        "source_package_path": str(package),
        "source_bytes": size,
        "packed_source_bytes": packed_bytes,
        "inherited_files": inherited,
        "archive_depth": reference["archive_depth"] + 1 if reference else 0,
        "archive_kind": "exact_incremental_complete_package_v1",
        "archive_name": archive_name,
        "archive_sha256": file_digest(archive),
        "archive_md5": md5(archive),
        "archive_bytes": archive.stat().st_size,
        "remote": remote,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "restore": "python -m quanthus.pipelines.p10_gdrive_incremental_archive "
        "--bundle <each downloaded dependency, oldest first> --bundle <this bundle> "
        "--expected-manifest-sha256 <exact target> --output <new empty path>; "
        "run producer semantic verifier before explicit pointer publication",
        "retention": "immutable cloud archive; no cloud deletion",
    }
    atomic(upload / "archive-manifest.json", report)
    atomic(upload / "source-pointer.json", pointer)
    atomic(upload / "source-verification.json", receipt)
    checksums = "".join(
        f"{file_digest(p)[7:]}  {p.name}\n"
        for p in sorted(upload.iterdir())
        if p.is_file() and p.name != "SHA256SUMS"
    )
    checksum_temp = run / ".SHA256SUMS.tmp"
    with checksum_temp.open("w") as handle:
        handle.write(checksums)
        handle.flush()
        os.fsync(handle.fileno())
    checksum_temp.replace(upload / "SHA256SUMS")
    atomic(run / "archive.json", report)
    return upload, report


def rclone(
    args: list[str],
    *,
    config: Path,
    proxy: str,
    policy: dict[str, Any],
    until: float,
    output: bool = False,
) -> str:
    chunk = policy["drive_chunk_size_bytes"]
    io_timeout = policy["transfer_io_timeout_seconds"]
    if (
        type(chunk) is not int
        or chunk < 256 * 1024
        or chunk % (256 * 1024)
        or type(io_timeout) is not int
        or not 1 <= io_timeout <= policy["command_timeout_seconds"]
    ):
        raise ArchiveError("invalid_versioned_transfer_profile")
    remaining = min(policy["command_timeout_seconds"], int(until - time.monotonic()))
    if remaining < 1:
        raise ArchiveError("archive_transfer_budget_exhausted")
    env = {
        **os.environ,
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "ALL_PROXY": "",
        "https_proxy": proxy,
        "http_proxy": proxy,
        "all_proxy": "",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    command = [
        "rclone",
        *args,
        "--config",
        str(config),
        "--drive-root-folder-id",
        policy["drive_root_folder_id"],
        "--tpslimit",
        "1",
        "--retries",
        "1",
        "--low-level-retries",
        "2",
        "--contimeout",
        "15s",
        "--timeout",
        f"{io_timeout}s",
        "--drive-chunk-size",
        f"{chunk // (1024**2)}M" if chunk % (1024**2) == 0 else f"{chunk // 1024}k",
        "--checkers",
        "1",
        "--transfers",
        "1",
    ]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=remaining, check=False
    )
    if result.returncode:
        category = next(
            (x for x in ["rateLimitExceeded", "invalid_grant", "timeout"] if x in result.stderr),
            "rclone_transfer_failed",
        )
        raise ArchiveError(category)
    return result.stdout if output else ""


def transfer(
    upload: Path,
    report: dict[str, Any],
    config: Path,
    policy: dict[str, Any],
    *,
    job_until: float,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    for route in (policy["primary_proxy"], policy["fallback_proxy"]):
        until = min(job_until, time.monotonic() + policy["route_timeout_seconds"])
        try:
            call = {"config": config, "proxy": route["url"], "policy": policy, "until": until}
            reference = report.get("archive_reference")
            if reference:
                marker = json.loads(
                    rclone(["cat", reference["remote"] + "/CLOUD_READY.json"], output=True, **call)
                )
                if any(
                    marker.get(k) != reference[k]
                    for k in (
                        "manifest_sha256",
                        "content_digest",
                        "archive_sha256",
                        "archive_md5",
                        "archive_bytes",
                        "archive_policy_sha256",
                    )
                ):
                    raise ArchiveError("cloud_dependency_identity_differs")
                files = json.loads(
                    rclone(
                        ["lsjson", reference["remote"], "--files-only", "--hash"],
                        output=True,
                        **call,
                    )
                )
                physical = [x for x in files if x["Name"] == reference["archive_name"]]
                if len(physical) != 1 or (
                    physical[0]["Size"] != reference["archive_bytes"]
                    or physical[0].get("Hashes", {}).get("MD5") != reference["archive_md5"]
                ):
                    raise ArchiveError("cloud_dependency_physical_identity_differs")
            rclone(["copy", str(upload), report["remote"], "--immutable", "--checksum"], **call)
            # Drive compares physical archive size and MD5; SHA256SUMS is also backed up.
            rclone(["check", str(upload), report["remote"], "--one-way"], **call)
            candidate = {
                **report,
                "status": "cloud_archive_verified",
                "proxy": route["name"],
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "remote_check": "size_and_MD5_all_archive_metadata_files",
                "route_failures": errors,
            }
            marker = upload.parent / "CLOUD_READY.json"
            done = object_at(marker) if marker.exists() else candidate
            if any(
                done[k] != report[k]
                for k in (
                    "manifest_sha256",
                    "content_digest",
                    "archive_sha256",
                    "archive_policy_sha256",
                )
            ):
                raise ArchiveError("persisted_cloud_ready_identity_differs")
            if not marker.exists():
                atomic(marker, done)
            rclone(
                [
                    "copyto",
                    str(marker),
                    report["remote"] + "/CLOUD_READY.json",
                    "--immutable",
                    "--checksum",
                ],
                **call,
            )
            remote_marker = json.loads(
                rclone(["cat", report["remote"] + "/CLOUD_READY.json"], output=True, **call)
            )
            if remote_marker != done:
                raise ArchiveError("cloud_ready_readback_differs")
            return {
                **done,
                "cloud_ready_confirmed_at": datetime.now(timezone.utc).isoformat(),
                "confirmation_proxy": route["name"],
            }
        except (ArchiveError, OSError, subprocess.SubprocessError, ValueError) as exc:
            errors.append(
                {
                    "proxy": route["name"],
                    "error_code": str(exc) if isinstance(exc, ArchiveError) else type(exc).__name__,
                }
            )
            # A Drive project quota or expired token is shared by both routes.
            if isinstance(exc, ArchiveError) and str(exc) in {"rateLimitExceeded", "invalid_grant"}:
                break
    raise ArchiveError("archive_not_verified_" + errors[-1]["error_code"])


def run(
    *,
    source: Path = SOURCE,
    state: Path = STATE,
    config: Path = CONFIG,
    revision: str,
    notify: bool = True,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_revision(revision)
    policy = policy or object_at(POLICY_PATH)
    state.mkdir(parents=True, exist_ok=True)
    until = time.monotonic() + policy["job_timeout_seconds"]
    with (state / ".archive.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "archive_job_already_running"}
        today = datetime.now(CST).date()
        first = max(date(2026, 10, 12), today - timedelta(days=policy["max_backlog_days"]))
        pending = []
        for pointer_path in sorted((source / "date-pointers").glob("*.json")):
            day = date.fromisoformat(pointer_path.stem)
            if not first <= day <= today:
                continue
            pointer = object_at(pointer_path)
            key = pointer["manifest_sha256"][7:]
            if len(key) != 64 or any(x not in "0123456789abcdef" for x in key):
                raise ArchiveError("pointer_hash_invalid")
            done_path = state / "delivered" / f"{key}.json"
            if done_path.exists():
                done = object_at(done_path)
                if (
                    done.get("manifest_sha256") != pointer["manifest_sha256"]
                    or done.get("content_digest") != pointer["content_digest"]
                    or done.get("status") != "cloud_archive_verified"
                    or done.get("archive_policy_sha256") != file_digest(POLICY_PATH)
                    or done.get("effective_archive_policy_sha256") != digest(encoded(policy))
                ):
                    raise ArchiveError("delivered_archive_identity_differs")
                continue
            pending.append((day, key))
        results: list[dict[str, Any]] = []
        processed = 0
        for day, key in pending:
            attempt_path = state / "attempts" / f"{key}.json"
            utc_day = str(datetime.now(timezone.utc).date())
            attempt = object_at(attempt_path) if attempt_path.exists() else {}
            count = attempt.get("count", 0) if attempt.get("utc_day") == utc_day else 0
            if count >= policy["max_attempts_per_utc_day"]:
                results.append({"market_date": str(day), "status": "retry_budget_exhausted"})
                continue
            if processed >= policy["max_packages_per_run"] or time.monotonic() >= until:
                break
            processed += 1
            atomic(attempt_path, {"utc_day": utc_day, "count": count + 1})
            try:
                upload, report = freeze(
                    source,
                    day,
                    state,
                    revision,
                    policy,
                    expected_manifest="sha256:" + key,
                    job_until=until,
                )
                done = transfer(upload, report, config, policy, job_until=until)
                atomic(state / "delivered" / f"{key}.json", done)
                atomic(state / "LAST_SUCCESS.json", done)
                # Only our private, successfully mirrored TMP archive is removed.
                shutil.rmtree(upload)
                results.append(
                    {
                        "market_date": str(day),
                        "status": done["status"],
                        "manifest_sha256": done["manifest_sha256"],
                        "proxy": done["proxy"],
                    }
                )
            except Exception as exc:
                if isinstance(exc, ArchiveError) and str(exc) == "source_revision_changed_retry":
                    results.append({"market_date": str(day), "status": str(exc)})
                    continue
                record = {
                    "market_date": str(day),
                    "status": "cloud_archive_failed",
                    "error_code": str(exc) if isinstance(exc, ArchiveError) else type(exc).__name__,
                    "attempt": count + 1,
                }
                atomic(state / "failures" / f"{key}.json", record)
                if notify:
                    try:
                        failure(
                            day=day,
                            stage="gdrive",
                            state_root=MAIL_STATE,
                            reason="qualified complete DWD GDrive archive failed; "
                            "independent daily and OCI delivery continue",
                            context=record,
                        )
                    except Exception as mail_error:
                        record["notification_error_class"] = type(mail_error).__name__
                results.append(record)
        return {
            "status": "archive_failed"
            if any(x["status"] == "cloud_archive_failed" for x in results)
            else "archive_scan_completed",
            "eligible": len(pending),
            "results": results,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    result = run(revision=args.code_revision)
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "archive_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
