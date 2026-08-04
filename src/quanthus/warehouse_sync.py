"""Pull the latest immutable CN01 DWD daily increment into OCI."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse import (
    DailyDomainBuildError,
    verify_daily_domain_increment,
)


class WarehouseSyncError(RuntimeError):
    """Raised when a CN01 DWD package cannot be copied and sealed."""

    def __init__(self, message: str, *, code: str = "sync_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WarehouseSyncResult:
    market_date: date
    package_id: str
    manifest_sha256: str
    content_digest: str
    payload_bytes: int
    changed: bool
    local_package: Path


def _object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WarehouseSyncError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise WarehouseSyncError(f"JSON root is not an object: {path}")
    return value


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _safe_value(value: str, *, label: str, pattern: str) -> str:
    import re

    if not re.fullmatch(pattern, value):
        raise WarehouseSyncError(f"{label} contains unsupported characters")
    return value


def _ssh_command() -> tuple[str, str, str, str, str, str]:
    remote_host = _safe_value(
        os.environ.get("QUANTHUS_CN01_DWD_HOST", ""), label="host", pattern=r"[A-Za-z0-9.-]+"
    )
    remote_port = _safe_value(
        os.environ.get("QUANTHUS_CN01_DWD_PORT", "22"), label="port", pattern=r"[0-9]{1,5}"
    )
    remote_user = _safe_value(
        os.environ.get("QUANTHUS_CN01_DWD_USER", "quanthus-dwd-ro"),
        label="user",
        pattern=r"[A-Za-z_][A-Za-z0-9_-]*",
    )
    key_path = os.environ.get("QUANTHUS_CN01_DWD_KEY", "")
    known_hosts = os.environ.get("QUANTHUS_CN01_DWD_KNOWN_HOSTS", "")
    for label, path in (("key", key_path), ("known-hosts", known_hosts)):
        if not path.startswith("/") or any(part in {"", ".", ".."} for part in Path(path).parts):
            raise WarehouseSyncError(f"{label} path must be an absolute normalized path")
        if not Path(path).is_file():
            raise WarehouseSyncError(f"{label} file is missing: {path}")
    mode = os.stat(key_path).st_mode & 0o777
    if mode not in {0o400, 0o600}:
        raise WarehouseSyncError(f"CN01 DWD key must be mode 0400 or 0600, got {mode:o}")
    remote_root = os.environ.get("QUANTHUS_CN01_DWD_REMOTE_ROOT", ".")
    if remote_root != "." and (
        remote_root.startswith("/") or ".." in PurePosixPath(remote_root).parts
    ):
        raise WarehouseSyncError("remote root must be . or a safe jail-relative path")
    import shlex

    ssh = (
        "ssh -i "
        + shlex.quote(key_path)
        + " -p "
        + shlex.quote(remote_port)
        + " -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10"
        + " -o StrictHostKeyChecking=yes -o UserKnownHostsFile="
        + shlex.quote(known_hosts)
    )
    return remote_host, remote_port, remote_user, key_path, known_hosts, ssh


def _run_rsync(*, source: str, destination: Path, ssh: str, extra: Sequence[str] = ()) -> None:
    command = [
        "rsync",
        "-a",
        "--checksum",
        "--delay-updates",
        "--partial-dir=.rsync-partial",
        "--timeout=300",
        "--mkpath",
        *extra,
        "-e",
        ssh,
        source,
        str(destination),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise WarehouseSyncError(f"rsync could not start: {exc}", code="transfer_failed") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise WarehouseSyncError(
            f"rsync failed with exit code {completed.returncode}: {detail[-1000:]}",
            code="transfer_failed",
        )


def _remote_path(relative: str) -> str:
    remote_root = os.environ.get("QUANTHUS_CN01_DWD_REMOTE_ROOT", ".").rstrip("/") or "."
    return f"{remote_root}/{relative}"


def _validate_pointer(pointer: Mapping[str, object], market_date: date) -> tuple[Path, str]:
    if pointer.get("kind") != "cn01_daily_dwd_current":
        raise WarehouseSyncError("remote DWD CURRENT kind is invalid")
    if pointer.get("market_date") != market_date.isoformat():
        raise WarehouseSyncError("remote DWD CURRENT does not match requested market date")
    raw_manifest = pointer.get("manifest_path")
    if not isinstance(raw_manifest, str):
        raise WarehouseSyncError("remote DWD CURRENT manifest_path is invalid")
    relative = PurePosixPath(raw_manifest)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) != 4
        or relative.parts[0] != "packages"
        or relative.parts[1] != market_date.isoformat()
        or relative.parts[3] != "manifest.json"
    ):
        raise WarehouseSyncError("remote DWD CURRENT points outside the date package")
    package_relative = Path(*relative.parts[:-1])
    package_id = package_relative.name
    _safe_value(package_id, label="package id", pattern=r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}")
    return package_relative, package_id


def _local_current(local_root: Path) -> dict[str, object] | None:
    path = local_root / "CURRENT.json"
    return _object(path) if path.exists() else None


def _ensure_monotonic(
    local_pointer: Mapping[str, object] | None,
    remote_pointer: Mapping[str, object],
    market_date: date,
) -> bool:
    if local_pointer is None:
        return True
    raw_date = local_pointer.get("market_date")
    if not isinstance(raw_date, str):
        raise WarehouseSyncError("local DWD CURRENT market_date is invalid")
    try:
        local_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise WarehouseSyncError("local DWD CURRENT market_date is invalid") from exc
    if local_date > market_date:
        raise WarehouseSyncError("remote DWD CURRENT would move OCI backwards")
    if local_date == market_date:
        if local_pointer.get("manifest_sha256") != remote_pointer.get("manifest_sha256"):
            raise WarehouseSyncError("same-date CN01 DWD CURRENT changed after OCI sync")
        return False
    return True


def sync_latest(*, local_root: Path, market_date: date | None = None) -> WarehouseSyncResult:
    local_root = local_root.resolve()
    local_root.mkdir(parents=True, exist_ok=True)
    expected_date = market_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    _, _, remote_user, _, _, ssh = _ssh_command()
    remote_host = os.environ["QUANTHUS_CN01_DWD_HOST"]
    with (local_root / ".sync.lock").open("a+", encoding="ascii") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        staging = Path(tempfile.mkdtemp(prefix=".cn01-dwd-sync-", dir=local_root))
        try:
            remote_current = staging / "CURRENT.json"
            try:
                _run_rsync(
                    source=f"{remote_user}@{remote_host}:{_remote_path('CURRENT.json')}",
                    destination=remote_current,
                    ssh=ssh,
                )
            except WarehouseSyncError as exc:
                raise WarehouseSyncError(
                    f"CN01 DWD CURRENT transfer failed: {exc}",
                    code="transfer_failed",
                ) from exc
            try:
                pointer = _object(remote_current)
            except WarehouseSyncError as exc:
                raise WarehouseSyncError(
                    f"CN01 DWD CURRENT is invalid: {exc}", code="source_current_invalid"
                ) from exc
            raw_date = pointer.get("market_date")
            if not isinstance(raw_date, str):
                raise WarehouseSyncError(
                    "remote DWD CURRENT market_date is invalid", code="source_current_invalid"
                )
            try:
                source_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise WarehouseSyncError(
                    "remote DWD CURRENT market_date is invalid", code="source_current_invalid"
                ) from exc
            if source_date != expected_date:
                code = (
                    "source_market_date_stale"
                    if source_date < expected_date
                    else "source_market_date_future"
                )
                raise WarehouseSyncError(
                    "remote DWD CURRENT is "
                    f"{source_date.isoformat()}, expected {expected_date.isoformat()}",
                    code=code,
                )
            try:
                package_relative, package_id = _validate_pointer(pointer, source_date)
            except WarehouseSyncError as exc:
                raise WarehouseSyncError(
                    f"CN01 DWD CURRENT package binding is invalid: {exc}",
                    code="source_current_invalid",
                ) from exc
            staged_package = staging / package_relative
            staged_package.mkdir(parents=True, exist_ok=True)
            remote_package = (
                f"{remote_user}@{remote_host}:{_remote_path(package_relative.as_posix())}/"
            )
            try:
                _run_rsync(
                    source=remote_package,
                    destination=staged_package,
                    ssh=ssh,
                    extra=("--exclude=READY",),
                )
                _run_rsync(
                    source=f"{remote_package}READY",
                    destination=staged_package / "READY",
                    ssh=ssh,
                )
            except WarehouseSyncError as exc:
                raise WarehouseSyncError(
                    f"CN01 DWD package transfer failed: {exc}", code="transfer_failed"
                ) from exc
            try:
                result = verify_daily_domain_increment(staged_package)
            except (DailyDomainBuildError, OSError, ValueError) as exc:
                raise WarehouseSyncError(
                    f"CN01 DWD package failed verification: {exc}",
                    code="source_package_invalid",
                ) from exc
            if (
                pointer.get("package_id") != result.package_id
                or pointer.get("manifest_sha256") != result.manifest_sha256
                or pointer.get("content_digest") != result.content_digest
            ):
                raise WarehouseSyncError("remote DWD CURRENT does not bind the package manifest")
            local_package = local_root / package_relative
            local_pointer = _local_current(local_root)
            changed = _ensure_monotonic(local_pointer, pointer, source_date)
            if local_package.exists():
                try:
                    existing = verify_daily_domain_increment(local_package)
                except (DailyDomainBuildError, OSError, ValueError) as exc:
                    raise WarehouseSyncError(
                        f"existing OCI DWD package failed verification: {exc}"
                    ) from exc
                if existing.manifest_sha256 != result.manifest_sha256:
                    raise WarehouseSyncError("existing OCI DWD package differs from CN01")
                shutil.rmtree(staged_package)
            else:
                local_package.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_package, local_package)
            if changed:
                _atomic_bytes(local_root / "CURRENT.json", remote_current.read_bytes())
            receipt = {
                "schema_version": 1,
                "kind": "oci_cn01_dwd_daily_sync",
                "market_date": source_date.isoformat(),
                "package_id": result.package_id,
                "manifest_sha256": result.manifest_sha256,
                "content_digest": result.content_digest,
                "payload_bytes": result.payload_bytes,
                "changed": changed,
                "synced_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            }
            _atomic_bytes(
                local_root / "receipts" / source_date.isoformat() / f"{result.package_id}.json",
                json.dumps(
                    receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                ).encode("ascii")
                + b"\n",
            )
            return WarehouseSyncResult(
                market_date=source_date,
                package_id=result.package_id,
                manifest_sha256=result.manifest_sha256,
                content_digest=result.content_digest,
                payload_bytes=result.payload_bytes,
                changed=changed,
                local_package=local_package,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _parse_market_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WarehouseSyncError("market date must use YYYY-MM-DD") from exc


def _write_failure_receipt(
    *, local_root: Path, market_date: date, error: WarehouseSyncError
) -> None:
    receipt = {
        "schema_version": 1,
        "kind": "oci_cn01_dwd_daily_sync_failure",
        "market_date": market_date.isoformat(),
        "error_code": error.code,
        "error": str(error)[:2000],
        "stage": "oci_dwd_sync",
        "node": os.environ.get("QUANTHUS_NODE", "OCI"),
        "unit": os.environ.get("QUANTHUS_SYSTEMD_UNIT", "oci-dwd-daily-sync.service"),
        "run_path": os.environ.get("QUANTHUS_DWD_RUN_ROOT", ""),
        "cn01_source_failure": error.code.startswith("source_"),
        "failed_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    }
    payload = json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    ) + b"\n"
    _atomic_bytes(local_root / "failures" / market_date.isoformat() / "latest.json", payload)
    _atomic_bytes(local_root / "failures" / "LATEST.json", payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--market-date")
    args = parser.parse_args(argv)
    local_root = args.local_root.resolve()
    try:
        requested_date = _parse_market_date(args.market_date)
    except WarehouseSyncError as exc:
        print(f"CN01 DWD sync failed: {exc}", file=sys.stderr)
        return 2
    expected_date = requested_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        result = sync_latest(
            local_root=local_root, market_date=expected_date
        )
    except WarehouseSyncError as exc:
        try:
            _write_failure_receipt(local_root=local_root, market_date=expected_date, error=exc)
        except OSError as receipt_error:
            print(f"could not write sync failure receipt: {receipt_error}", file=sys.stderr)
        print(f"CN01 DWD sync failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.__dict__, default=str, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
