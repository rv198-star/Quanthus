"""Read-only health snapshots for the checkpointed DWD Base V2.1 rebuild."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast


class DwdV21MonitorError(RuntimeError):
    """Raised when monitoring inputs or durable state fail closed."""


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DwdV21MonitorError(f"cannot read {label}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise DwdV21MonitorError(f"{label} must be a JSON object: {path}")
    return cast(dict[str, Any], value)


def _verify_canonical_object(path: Path, label: str) -> dict[str, Any]:
    value = _load_object(path, label)
    supplied = value.get("canonical_digest")
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    actual = _canonical_digest(unsigned)
    if supplied != actual:
        raise DwdV21MonitorError(
            f"{label} canonical digest mismatch: supplied={supplied!r} actual={actual}"
        )
    return value


def verify_execution_contract(
    contract_path: Path,
    expected_contract_digest: str,
) -> dict[str, Any]:
    """Verify the immutable build configuration without assigning authority to a host."""

    contract = _verify_canonical_object(contract_path, "execution contract")
    if contract.get("canonical_digest") != expected_contract_digest:
        raise DwdV21MonitorError("execution contract does not match the deployed digest")
    return contract


def _decode_mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def parse_mountinfo(text: str, target: Path) -> dict[str, str] | None:
    target_text = str(target.resolve())
    matches: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        before, separator, after = raw_line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        trailing = after.split()
        if len(fields) < 6 or len(trailing) < 2:
            continue
        mount_point = _decode_mount_path(fields[4])
        prefix = mount_point.rstrip("/") + "/"
        if target_text != mount_point and not target_text.startswith(prefix):
            continue
        matches.append(
            {
                "filesystem": trailing[0],
                "mount_options": fields[5],
                "mount_point": mount_point,
                "source": trailing[1],
            }
        )
    if not matches:
        return None
    return max(matches, key=lambda item: len(item["mount_point"]))


def _memory_snapshot() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        amount = remainder.strip().split()[0]
        if amount.isdigit():
            values[key] = int(amount) * 1024
    return {
        "available_bytes": values.get("MemAvailable", 0),
        "swap_free_bytes": values.get("SwapFree", 0),
        "total_bytes": values.get("MemTotal", 0),
    }


def _active_run_snapshot(workspace: Path, contract_digest: str) -> dict[str, Any]:
    pointer = workspace / "control" / "active-run.json"
    if not pointer.exists():
        return {"status": "idle", "pointer": str(pointer)}
    value = _load_object(pointer, "active run pointer")
    run_id = value.get("run_id")
    phase = value.get("phase")
    if not isinstance(run_id, str) or not run_id or "/" in run_id:
        raise DwdV21MonitorError("active run pointer has an invalid run id")
    if not isinstance(phase, str) or not phase:
        raise DwdV21MonitorError("active run pointer has an invalid phase")
    if value.get("contract_digest") != contract_digest:
        raise DwdV21MonitorError("active run pointer has contract digest drift")
    pointer_status = value.get("status")
    if pointer_status not in {"running", "passed", "paused", "failed", "interrupted"}:
        raise DwdV21MonitorError("active run pointer has an invalid status")
    run_root = workspace / "runs" / run_id
    phase_receipt = run_root / "state" / f"{phase}.json"
    if not run_root.is_dir() or not phase_receipt.is_file():
        raise DwdV21MonitorError("active run or phase receipt is missing")
    receipt = _verify_canonical_object(phase_receipt, "phase receipt")
    if receipt.get("run_id") != run_id or receipt.get("contract_digest") != contract_digest:
        raise DwdV21MonitorError("phase receipt does not bind the active run and contract")
    if receipt.get("status") != pointer_status:
        raise DwdV21MonitorError("active run pointer status differs from its phase receipt")
    executor_pid = value.get("executor_pid")
    process_status = "not_declared"
    if isinstance(executor_pid, int) and executor_pid > 0:
        try:
            os.kill(executor_pid, 0)
        except ProcessLookupError:
            process_status = "missing"
        except PermissionError:
            process_status = "present_uninspectable"
        else:
            process_status = "present"
    if pointer_status == "running":
        snapshot_status = (
            "active" if process_status in {"present", "present_uninspectable"} else "stale"
        )
    else:
        snapshot_status = str(pointer_status)
    return {
        "executor_pid": executor_pid,
        "phase": phase,
        "phase_receipt": str(phase_receipt),
        "process_status": process_status,
        "run_id": run_id,
        "status": snapshot_status,
    }


def inspect_rebuild(
    *,
    contract_path: Path,
    expected_contract_digest: str,
    workspace: Path,
    minimum_free_bytes: int,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> dict[str, Any]:
    verify_execution_contract(contract_path, expected_contract_digest)
    if not workspace.is_dir() or workspace.is_symlink():
        raise DwdV21MonitorError("workspace is missing or symlinked")
    mount = parse_mountinfo(mountinfo_path.read_text(encoding="ascii"), workspace)
    if mount is None or mount["filesystem"] != "ext4":
        raise DwdV21MonitorError("workspace is not on ext4")
    filesystem = os.statvfs(workspace)
    free_bytes = filesystem.f_bavail * filesystem.f_frsize
    if free_bytes < minimum_free_bytes:
        raise DwdV21MonitorError(
            f"workspace free bytes below monitor floor: {free_bytes} < {minimum_free_bytes}"
        )
    memory = _memory_snapshot()
    active_run = _active_run_snapshot(workspace, expected_contract_digest)
    return {
        "active_run": active_run,
        "contract_digest": expected_contract_digest,
        "free_bytes": free_bytes,
        "host": socket.gethostname(),
        "kind": "dwd_base_v2_1_rebuild_health_snapshot",
        "memory": memory,
        "minimum_free_bytes": minimum_free_bytes,
        "mount": mount,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "status": (
            "healthy"
            if active_run["status"] == "active"
            else "failed"
            if active_run["status"] in {"failed", "stale"}
            else "idle"
        ),
        "workspace": str(workspace),
    }


def write_snapshot(output_root: Path, snapshot: Mapping[str, Any]) -> tuple[Path, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    snapshots = output_root / "snapshots"
    snapshots.mkdir(mode=0o750, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
    observed = str(snapshot["observed_at"]).replace(":", "").replace("+", "_")
    immutable = snapshots / f"{observed}-{digest[:16]}.json"
    with immutable.open("x", encoding="ascii") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    immutable.chmod(0o440)
    temporary = output_root / f".latest.{os.getpid()}.partial"
    with temporary.open("x", encoding="ascii") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output_root / "latest.json")
    for directory in (snapshots, output_root):
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return output_root / "latest.json", immutable


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--expected-contract-digest", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--minimum-free-bytes", type=int, default=10 * 1024**3)
    parser.add_argument("--output-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.minimum_free_bytes < 0:
        parser.error("--minimum-free-bytes must be non-negative")
    try:
        snapshot = inspect_rebuild(
            contract_path=args.contract.resolve(),
            expected_contract_digest=args.expected_contract_digest,
            workspace=args.workspace.resolve(),
            minimum_free_bytes=args.minimum_free_bytes,
        )
        if args.output_root is not None:
            write_snapshot(args.output_root.resolve(), snapshot)
    except DwdV21MonitorError as error:
        print(
            json.dumps(
                {
                    "kind": "dwd_base_v2_1_rebuild_health_snapshot",
                    "status": "failed",
                    "error": str(error),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(snapshot, ensure_ascii=True, sort_keys=True))
    return 2 if snapshot["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
