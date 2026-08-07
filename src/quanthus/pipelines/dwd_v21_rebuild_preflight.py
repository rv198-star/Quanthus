"""Fail-closed GPU-P100 preflight for the governed DWD Base V2.1 rebuild."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from quanthus.pipelines.dwd_v21_rebuild_monitor import parse_mountinfo, verify_authority
from quanthus.pipelines.ods_base_rollup import OdsBaseRollupError, verify_ods_base_rollup
from quanthus.pipelines.securities_warehouse.domain_base import (
    DomainBaseError,
    verify_domain_base,
)


class DwdV21PreflightError(RuntimeError):
    """Raised when a V2.1 execution prerequisite is not proven."""


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DwdV21PreflightError(f"cannot read {label}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise DwdV21PreflightError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _verify_canonical(path: Path, label: str, expected: str) -> dict[str, Any]:
    value = _object(path, label)
    unsigned = dict(value)
    supplied = unsigned.pop("canonical_digest", None)
    actual = _canonical_digest(unsigned)
    if supplied != expected or actual != expected:
        raise DwdV21PreflightError(
            f"{label} canonical digest mismatch: supplied={supplied!r} actual={actual!r}"
        )
    return value


def load_environment(path: Path) -> dict[str, str]:
    """Read the restricted KEY=VALUE deployment environment without evaluating shell code."""

    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise DwdV21PreflightError(f"cannot read environment file: {path}: {error}") from error
    for line_number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.replace("_", "").isalnum() or not key[0].isalpha():
            raise DwdV21PreflightError(
                f"invalid environment assignment at {path}:{line_number}"
            )
        result[key] = value
    return result


def _git_state(repository: Path, expected_revision: str) -> dict[str, Any]:
    def run(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise DwdV21PreflightError(
                f"Git state check failed: {' '.join(arguments)}: {completed.stderr.strip()}"
            )
        return completed.stdout.strip()

    revision = run("rev-parse", "HEAD")
    status = run("status", "--porcelain=v1", "--untracked-files=all")
    if revision != expected_revision:
        raise DwdV21PreflightError(
            f"code revision mismatch: observed={revision} expected={expected_revision}"
        )
    if status:
        raise DwdV21PreflightError("repository checkout is not clean")
    return {"repository": str(repository.resolve()), "revision": revision, "status": "clean"}


def _filesystem_probe(workspace: Path) -> dict[str, bool]:
    control = workspace / "control" / "preflight"
    control.mkdir(parents=True, exist_ok=True)
    probe = Path(tempfile.mkdtemp(prefix=".filesystem-probe.", dir=control))
    try:
        source = probe / "source"
        source.write_bytes(b"quanthus-dwd-v2.1-preflight\n")
        with source.open("rb") as stream:
            os.fsync(stream.fileno())
        hardlink = probe / "hardlink"
        os.link(source, hardlink)
        renamed = probe / "renamed"
        os.replace(hardlink, renamed)
        directory_fd = os.open(probe, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {"directory_fsync": True, "hardlink": True, "rename": True}
    finally:
        shutil.rmtree(probe)


def write_receipt(output_root: Path, receipt: Mapping[str, Any]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    unsigned = dict(receipt)
    unsigned.pop("canonical_digest", None)
    value = {**unsigned, "canonical_digest": _canonical_digest(unsigned)}
    payload = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    timestamp = str(value["completed_at"]).replace(":", "").replace("+", "_")
    immutable = output_root / f"{timestamp}-{value['canonical_digest'][7:23]}.json"
    temporary = output_root / f".{immutable.name}.{os.getpid()}.partial"
    with temporary.open("x", encoding="ascii") as stream:
        os.fchmod(stream.fileno(), 0o440)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, immutable)
    finally:
        temporary.unlink(missing_ok=True)
    directory_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return immutable


def run_preflight(
    *,
    repository: Path,
    workspace: Path,
    ods_base: Path,
    dwd_base: Path,
    contract_path: Path,
    approval_path: Path,
    findings_path: Path,
    remediation_path: Path,
    environment_path: Path,
    expected_revision: str,
    expected_contract_digest: str,
    expected_findings_digest: str,
    expected_remediation_digest: str,
    minimum_free_bytes: int,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    observed_host = socket.gethostname()
    if observed_host.casefold() != "gpu-p100":
        raise DwdV21PreflightError(f"preflight must run on GPU-P100, not {observed_host}")
    contract, approval = verify_authority(
        contract_path, approval_path, expected_contract_digest
    )
    _verify_canonical(findings_path, "findings ledger", expected_findings_digest)
    _verify_canonical(remediation_path, "remediation scope", expected_remediation_digest)
    environment = load_environment(environment_path)
    expected_environment = {
        "QUANTHUS_DWD_BUILDER_AUTHORIZED": "0",
        "QUANTHUS_DWD_CODE_REVISION": expected_revision,
        "QUANTHUS_DWD_EXECUTION_CONTRACT_DIGEST": expected_contract_digest,
        "QUANTHUS_DWD_EXECUTION_MODE": "pilot_only",
        "QUANTHUS_DWD_PILOT_AUTHORIZED": "1",
    }
    if any(environment.get(key) != value for key, value in expected_environment.items()):
        raise DwdV21PreflightError("deployment environment differs from pilot-only authority")
    expected_workspace = contract.get("authorized_execution", {}).get("build_workspace")
    if expected_workspace != str(workspace.resolve()):
        raise DwdV21PreflightError("workspace differs from the approved contract")
    mount = parse_mountinfo(mountinfo_path.read_text(encoding="ascii"), workspace)
    if mount is None or mount["mount_point"] != str(workspace) or mount["filesystem"] != "ext4":
        raise DwdV21PreflightError("workspace is not the exact approved ext4 mount")
    free_bytes = shutil.disk_usage(workspace).free
    if free_bytes < minimum_free_bytes:
        raise DwdV21PreflightError(
            f"workspace capacity is below the floor: {free_bytes} < {minimum_free_bytes}"
        )
    lock_path = workspace / "control" / "locks" / "dwd-base-v2-1-rebuild.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o640)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DwdV21PreflightError("GPU rebuild lock is already held") from error
        filesystem_semantics = _filesystem_probe(workspace)
        git = _git_state(repository, expected_revision)
        ods = verify_ods_base_rollup(ods_base, deep=True)
        dwd = verify_domain_base(dwd_base)
    except (OdsBaseRollupError, DomainBaseError, OSError) as error:
        raise DwdV21PreflightError(str(error)) from error
    finally:
        os.close(lock_fd)
    expected_ods = {
        "bundle_id": "ods-base-202607-v2",
        "manifest_sha256": (
            "sha256:7f74a7655f3f86982b4e662f55b12aff4fa2f28ee23cf9b5db530f9f2aa0955b"
        ),
        "content_digest": (
            "sha256:c95adef6624d5d904a53fc93b27d1e2d78ed4a908a653661d89c47147380ac01"
        ),
    }
    observed_ods = {
        "bundle_id": ods.bundle_id,
        "manifest_sha256": ods.manifest_sha256,
        "content_digest": ods.content_digest,
    }
    expected_dwd = {
        "package_id": "dwd-base-202607-v2",
        "manifest_sha256": (
            "sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f"
        ),
        "content_digest": (
            "sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9"
        ),
    }
    observed_dwd = {
        "package_id": dwd.package_id,
        "manifest_sha256": dwd.manifest_sha256,
        "content_digest": dwd.content_digest,
    }
    if observed_ods != expected_ods or observed_dwd != expected_dwd:
        raise DwdV21PreflightError("deep-verified input identity differs from the contract")
    return {
        "approval_receipt_digest": approval["canonical_digest"],
        "authority": "pilot_only",
        "code": git,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "contract_digest": expected_contract_digest,
        "dwd_base": {**observed_dwd, "table_count": dwd.table_count},
        "filesystem_semantics": filesystem_semantics,
        "findings_digest": expected_findings_digest,
        "free_bytes": free_bytes,
        "host": observed_host,
        "kind": "dwd_base_v2_1_gpu_p100_preflight_receipt",
        "lock": {"path": str(lock_path), "status": "acquired_and_released"},
        "minimum_free_bytes": minimum_free_bytes,
        "mount": mount,
        "ods_base": {**observed_ods, "file_count": ods.file_count},
        "remediation_digest": expected_remediation_digest,
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "status": "passed",
        "workspace": str(workspace),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ods-base", type=Path, required=True)
    parser.add_argument("--dwd-base", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--remediation", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-contract-digest", required=True)
    parser.add_argument("--expected-findings-digest", required=True)
    parser.add_argument("--expected-remediation-digest", required=True)
    parser.add_argument("--minimum-free-bytes", type=int, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = run_preflight(
            repository=args.repository.resolve(),
            workspace=args.workspace.resolve(),
            ods_base=args.ods_base.resolve(),
            dwd_base=args.dwd_base.resolve(),
            contract_path=args.contract.resolve(),
            approval_path=args.approval.resolve(),
            findings_path=args.findings.resolve(),
            remediation_path=args.remediation.resolve(),
            environment_path=args.environment.resolve(),
            expected_revision=args.expected_revision,
            expected_contract_digest=args.expected_contract_digest,
            expected_findings_digest=args.expected_findings_digest,
            expected_remediation_digest=args.expected_remediation_digest,
            minimum_free_bytes=args.minimum_free_bytes,
        )
        path = write_receipt(args.output_root.resolve(), receipt)
    except (DwdV21PreflightError, OSError) as error:
        print(f"DWD V2.1 preflight failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"receipt": str(path), **receipt}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
