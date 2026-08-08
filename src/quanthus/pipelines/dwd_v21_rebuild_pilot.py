"""Bounded read-only pilots for the governed DWD Base V2.1 rebuild."""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from quanthus.pipelines.dwd_replay_determinism_audit import (
    DwdReplayDeterminismAuditError,
    audit_dwd_replay_determinism,
    write_replay_determinism_report,
)
from quanthus.pipelines.dwd_v21_rebuild_monitor import verify_authority
from quanthus.pipelines.dwd_v21_rebuild_preflight import load_environment
from quanthus.pipelines.dwd_v21_rebuild_runtime import (
    DwdV21RuntimeError,
    _advance,
    _atomic_json,
    _object,
    _require_pilot_only,
    _sealed,
    _verify_sealed,
)
from quanthus.pipelines.securities_warehouse.planner import (
    WarehousePlanningError,
    _verify_existing_plan,
)


class DwdV21PilotError(RuntimeError):
    """Raised when a pilot cannot produce trustworthy evidence."""


_WORKLOADS = {
    "financial_reporting": {
        "dataset_id": "source_evidence_weekly_full_fundamentals_20260731",
        "path_suffix": "/artifacts/financial_statements.parquet",
    },
    "security_market_observation": {
        "dataset_id": "minute_5m_formal_archive",
        "path_prefix": "payload/minute/prod_5m/",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise DwdV21PilotError(f"cannot hash pilot input: {path}: {error}") from error
    return "sha256:" + digest.hexdigest()


def _git_revision(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise DwdV21PilotError(f"cannot resolve repository revision: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _require_clean_revision(repository: Path, expected_revision: str) -> None:
    revision = _git_revision(repository)
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        raise DwdV21PilotError(f"cannot inspect repository state: {status.stderr.strip()}")
    if revision != expected_revision:
        raise DwdV21PilotError(
            f"code revision mismatch: observed={revision} expected={expected_revision}"
        )
    if status.stdout.strip():
        raise DwdV21PilotError("repository checkout is not clean")


def _manifest_files(ods_base: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _object(ods_base / "manifest.json", "ODS Base manifest")
    content = manifest.get("content")
    if not isinstance(content, Mapping):
        raise DwdV21PilotError("ODS Base manifest content is not an object")
    raw_files = content.get("files")
    if not isinstance(raw_files, list):
        raise DwdV21PilotError("ODS Base manifest content.files is not a list")
    files: list[dict[str, Any]] = []
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise DwdV21PilotError("ODS Base manifest contains a malformed file entry")
        files.append(dict(raw))
    return manifest, files


def select_representative_workloads(ods_base: Path) -> list[dict[str, Any]]:
    """Select one immutable, deterministic ODS partition for each heavy builder."""

    _, files = _manifest_files(ods_base.resolve())
    selected: list[dict[str, Any]] = []
    for builder, selector in sorted(_WORKLOADS.items()):
        matches: list[dict[str, Any]] = []
        for item in files:
            path = item.get("path")
            if item.get("dataset_id") != selector["dataset_id"] or not isinstance(path, str):
                continue
            prefix = selector.get("path_prefix")
            suffix = selector.get("path_suffix")
            if prefix is not None and not path.startswith(prefix):
                continue
            if suffix is not None and not path.endswith(suffix):
                continue
            if item.get("format") != "parquet":
                continue
            matches.append(item)
        if not matches:
            raise DwdV21PilotError(f"no representative ODS partition found for {builder}")
        entry = max(
            matches,
            key=lambda value: (int(value.get("size_bytes", -1)), str(value["path"])),
        )
        relative = Path(str(entry["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise DwdV21PilotError(f"unsafe ODS manifest path: {relative}")
        source = (ods_base / relative).resolve()
        if (
            not source.is_relative_to(ods_base.resolve())
            or not source.is_file()
            or source.is_symlink()
        ):
            raise DwdV21PilotError(f"representative ODS partition is not a regular file: {source}")
        expected_size = entry.get("size_bytes")
        if not isinstance(expected_size, int) or source.stat().st_size != expected_size:
            raise DwdV21PilotError(f"representative ODS partition size mismatch: {relative}")
        expected_sha = entry.get("sha256")
        if not isinstance(expected_sha, str) or not expected_sha.startswith("sha256:"):
            raise DwdV21PilotError(f"representative ODS partition has no digest: {relative}")
        selected.append(
            {
                "builder": builder,
                "dataset_id": entry["dataset_id"],
                "path": str(relative),
                "sha256": expected_sha,
                "size_bytes": expected_size,
                "declared_row_count": entry.get("row_count"),
                "declared_schema_sha256": entry.get("schema_sha256"),
            }
        )
    return selected


def _scan_parquet(path: Path, *, expected_sha256: str, batch_size_rows: int) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise DwdV21PilotError("PyArrow is required for DWD V2.1 pilots") from error

    actual_sha = _sha256(path)
    if actual_sha != expected_sha256:
        raise DwdV21PilotError(
            "pilot input digest mismatch: "
            f"path={path} actual={actual_sha} expected={expected_sha256}"
        )
    parquet = pq.ParquetFile(path)
    schema_bytes = parquet.schema_arrow.serialize()
    semantic = hashlib.sha256()
    null_counts = [0 for _ in parquet.schema_arrow]
    row_count = 0
    batch_count = 0
    for batch in parquet.iter_batches(batch_size=batch_size_rows, use_threads=False):
        encoded = batch.serialize()
        semantic.update(len(encoded).to_bytes(8, "big"))
        semantic.update(encoded)
        row_count += batch.num_rows
        batch_count += 1
        for index, column in enumerate(batch.columns):
            null_counts[index] += column.null_count
    if parquet.metadata is not None and row_count != parquet.metadata.num_rows:
        raise DwdV21PilotError(f"decoded row count mismatch: {path}")
    return {
        "source_sha256": actual_sha,
        "physical_size_bytes": path.stat().st_size,
        "row_count": row_count,
        "batch_count": batch_count,
        "schema_sha256": "sha256:" + hashlib.sha256(schema_bytes).hexdigest(),
        "column_null_counts": {
            field.name: null_counts[index] for index, field in enumerate(parquet.schema_arrow)
        },
        "decoded_semantic_sha256": "sha256:" + semantic.hexdigest(),
    }


def run_worker(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one isolated read-only replay and return deterministic and resource evidence."""

    memory_limit_bytes = int(spec["memory_limit_bytes"])
    batch_size_rows = int(spec["batch_size_rows"])
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
    started_at = datetime.now(timezone.utc)
    started_ns = time.perf_counter_ns()
    results: list[dict[str, Any]] = []
    for raw in cast(list[dict[str, Any]], spec["workloads"]):
        source = Path(str(spec["ods_base"])) / str(raw["path"])
        scan = _scan_parquet(
            source,
            expected_sha256=str(raw["sha256"]),
            batch_size_rows=batch_size_rows,
        )
        if raw.get("declared_row_count") is not None and scan["row_count"] != raw.get(
            "declared_row_count"
        ):
            raise DwdV21PilotError(f"manifest row count mismatch: {raw['path']}")
        results.append({**raw, "scan": scan})
    completed_at = datetime.now(timezone.utc)
    peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return {
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "elapsed_ms": max(0, (time.perf_counter_ns() - started_ns) // 1_000_000),
        "peak_rss_bytes": peak_rss_bytes,
        "workloads": results,
    }


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _run_isolated_worker(
    *,
    python: Path,
    ods_base: Path,
    workloads: Sequence[Mapping[str, Any]],
    spill_root: Path,
    memory_limit_bytes: int,
    spill_limit_bytes: int,
    thread_limit: int,
    batch_size_rows: int,
) -> dict[str, Any]:
    spill_root.mkdir(parents=True, exist_ok=False)
    before = _directory_bytes(spill_root)
    spec = {
        "ods_base": str(ods_base),
        "workloads": [dict(item) for item in workloads],
        "memory_limit_bytes": memory_limit_bytes,
        "batch_size_rows": batch_size_rows,
    }
    environment = dict(os.environ)
    for key in (
        "TMPDIR",
        "TMP",
        "TEMP",
        "ARROW_TMPDIR",
        "DUCKDB_TEMP_DIRECTORY",
        "POLARS_TEMP_DIR",
    ):
        environment[key] = str(spill_root)
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "POLARS_MAX_THREADS",
        "ARROW_NUM_THREADS",
    ):
        environment[key] = str(thread_limit)
    completed = subprocess.run(
        [
            str(python),
            "-m",
            "quanthus.pipelines.dwd_v21_rebuild_pilot",
            "--worker-spec-json",
            json.dumps(spec, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise DwdV21PilotError(f"isolated pilot worker failed: {completed.stderr.strip()}")
    try:
        result: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise DwdV21PilotError("isolated pilot worker returned invalid JSON") from error
    if not isinstance(result, dict):
        raise DwdV21PilotError("isolated pilot worker result is not an object")
    after = _directory_bytes(spill_root)
    spill_bytes = max(0, after - before)
    peak = result.get("peak_rss_bytes")
    if not isinstance(peak, int) or peak > memory_limit_bytes:
        raise DwdV21PilotError(
            f"pilot memory limit exceeded: observed={peak!r} limit={memory_limit_bytes}"
        )
    if spill_bytes > spill_limit_bytes:
        raise DwdV21PilotError(
            f"pilot spill limit exceeded: observed={spill_bytes} limit={spill_limit_bytes}"
        )
    return {**result, "spill_bytes": spill_bytes}


def _base_release(dwd_base: Path) -> dict[str, Any]:
    manifest = _object(dwd_base / "manifest.json", "DWD Base manifest")
    return {
        "package_id": manifest.get("package_id"),
        "manifest_sha256": _sha256(dwd_base / "manifest.json"),
        "content_digest": manifest.get("content_digest"),
        "through_date": manifest.get("through_date"),
    }


def _replay_receipt(
    *,
    base_release: Mapping[str, Any],
    input_plan: Mapping[str, Any],
    deterministic_results: Sequence[Mapping[str, Any]],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "kind": "dwd_base_v2_replay_execution",
        "base_release": dict(base_release),
        "input_plan": dict(input_plan),
        "mutation_performed": False,
        "workloads": [dict(item) for item in deterministic_results],
        "started_at": started_at,
        "completed_at": completed_at,
    }
    return cast(dict[str, Any], _sealed(value))


def _write_new_sealed(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise DwdV21PilotError(f"refusing to overwrite pilot evidence: {path}")
    _atomic_json(path, value, mode=0o440)


def run_pilots(
    *,
    repository: Path,
    workspace: Path,
    ods_base: Path,
    dwd_base: Path,
    environment_path: Path,
    run_id: str,
    expected_revision: str,
    expected_contract_digest: str,
    python: Path,
    memory_limit_bytes: int,
    spill_limit_bytes: int,
    thread_limit: int,
    batch_size_rows: int,
) -> dict[str, Any]:
    """Run two isolated replays and produce a strong bounded partition-pilot report."""

    repository = repository.resolve()
    workspace = workspace.resolve()
    ods_base = ods_base.resolve()
    dwd_base = dwd_base.resolve()
    environment_path = environment_path.resolve()
    python = python.resolve()
    run_root = workspace / "runs" / run_id
    plan_root = run_root / "plan"
    evidence_root = run_root / "evidence" / "pilot"
    spill_root = run_root / "scratch" / "pilot"
    if memory_limit_bytes < 2 * 1024**3 or spill_limit_bytes < 0:
        raise DwdV21PilotError("pilot resource limits are invalid")
    if thread_limit < 1 or batch_size_rows < 1:
        raise DwdV21PilotError("pilot thread or batch limit is invalid")
    if not python.is_file():
        raise DwdV21PilotError(f"pilot Python does not exist: {python}")
    for selected in (evidence_root, spill_root):
        if selected.exists():
            raise DwdV21PilotError(f"pilot output already exists: {selected}")
        if not selected.is_relative_to(workspace):
            raise DwdV21PilotError(f"pilot output is outside workspace: {selected}")

    contract_path = repository / "config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json"
    approval_path = (
        repository
        / "docs/acceptance/issue-78-dwd-base-v2-1-gpu-p100-execution-approval-20260807.json"
    )
    verify_authority(contract_path, approval_path, expected_contract_digest)
    _require_pilot_only(environment_path, expected_revision)
    environment = load_environment(environment_path)
    if environment.get("QUANTHUS_DWD_BUILDER_AUTHORIZED") != "0":
        raise DwdV21PilotError("full builder authority must remain disabled")
    _require_clean_revision(repository, expected_revision)
    awaiting = _verify_sealed(
        run_root / "state/awaiting-pilot-receipts.json", "awaiting-pilot-receipts phase"
    )
    if (
        awaiting.get("run_id") != run_id
        or awaiting.get("contract_digest") != expected_contract_digest
        or awaiting.get("code_revision") != expected_revision
        or awaiting.get("status") != "paused"
    ):
        raise DwdV21PilotError("run is not at the approved pilot gate")
    planning = _verify_sealed(run_root / "state/planning-passed.json", "planning phase")
    planning_evidence = planning.get("evidence")
    if not isinstance(planning_evidence, Mapping):
        raise DwdV21PilotError("planning phase has no evidence")
    plan_digest = planning_evidence.get("plan_digest")
    if not isinstance(plan_digest, str):
        raise DwdV21PilotError("planning phase has no plan digest")
    try:
        _verify_existing_plan(plan_root, run_id=run_id, plan_digest=plan_digest)
    except WarehousePlanningError as error:
        raise DwdV21PilotError(str(error)) from error
    input_plan_value = _object(plan_root / "input-plan.json", "input plan")
    plan = input_plan_value.get("plan")
    if not isinstance(plan, Mapping) or plan.get("code_revision") != expected_revision:
        raise DwdV21PilotError("input plan is not bound to the expected revision")
    planned_input = plan.get("input")
    if not isinstance(planned_input, Mapping):
        raise DwdV21PilotError("input plan has no input identity")
    if _sha256(ods_base / "manifest.json") != planned_input.get("manifest_sha256"):
        raise DwdV21PilotError("ODS Base differs from the sealed input plan")

    lock_path = workspace / "control/locks/dwd-base-v2-1-rebuild.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o640)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DwdV21PilotError("GPU rebuild lock is already held") from error
        _advance(
            workspace,
            run_root,
            phase="pilot-running",
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="running",
            evidence={"builder_started": False},
        )
        workloads = select_representative_workloads(ods_base)
        executions: list[dict[str, Any]] = []
        for name in ("replay-a", "replay-b"):
            executions.append(
                {
                    "name": name,
                    **_run_isolated_worker(
                        python=python,
                        ods_base=ods_base,
                        workloads=workloads,
                        spill_root=spill_root / name,
                        memory_limit_bytes=memory_limit_bytes,
                        spill_limit_bytes=spill_limit_bytes,
                        thread_limit=thread_limit,
                        batch_size_rows=batch_size_rows,
                    ),
                }
            )
        deterministic = [execution["workloads"] for execution in executions]
        if deterministic[0] != deterministic[1]:
            raise DwdV21PilotError("independent replay results differ")
        base_release = _base_release(dwd_base)
        input_identity = {"run_id": run_id, "plan_digest": plan_digest}
        evidence_root.mkdir(parents=True, exist_ok=False)
        replay_paths: list[Path] = []
        for execution in executions:
            path = evidence_root / f"{execution['name']}.json"
            _write_new_sealed(
                path,
                _replay_receipt(
                    base_release=base_release,
                    input_plan=input_identity,
                    deterministic_results=cast(list[dict[str, Any]], execution["workloads"]),
                    started_at=str(execution["started_at"]),
                    completed_at=str(execution["completed_at"]),
                ),
            )
            replay_paths.append(path)
        replay_report = audit_dwd_replay_determinism(
            base_root=dwd_base,
            plan_root=plan_root,
            replay_a=replay_paths[0],
            replay_b=replay_paths[1],
        )
        if replay_report["summary"]["status"] != "verified":
            raise DwdV21PilotError("replay determinism audit did not verify")
        replay_report_path = evidence_root / "replay-determinism.json"
        write_replay_determinism_report(
            replay_report_path,
            replay_report,
            sealed_roots=(ods_base, dwd_base, plan_root),
        )

        partition_report = _sealed(
            {
                "schema_version": 1,
                "kind": "dwd_base_v2_partition_pilot",
                "status": "verified",
                "mutation_performed": False,
                "run_id": run_id,
                "contract_digest": expected_contract_digest,
                "code_revision": expected_revision,
                "base_release": base_release,
                "input_plan": input_identity,
                "execution_policy": {
                    "execution_mode": "sequential",
                    "max_concurrent_workers": 1,
                    "memory_limit_bytes": memory_limit_bytes,
                    "spill_limit_bytes_per_execution": spill_limit_bytes,
                    "thread_limit": thread_limit,
                    "batch_size_rows": batch_size_rows,
                    "spill_path": str(spill_root),
                    "spill_filesystem_required": "ext4",
                    "local_scratch_fallback_allowed": False,
                    "network_access_required": False,
                },
                "executions": [
                    {
                        "name": execution["name"],
                        "started_at": execution["started_at"],
                        "completed_at": execution["completed_at"],
                        "elapsed_ms": execution["elapsed_ms"],
                        "peak_rss_bytes": execution["peak_rss_bytes"],
                        "spill_bytes": execution["spill_bytes"],
                    }
                    for execution in executions
                ],
                "workloads": deterministic[0],
                "summary": {
                    "heavy_builder_count": len(workloads),
                    "independent_execution_count": len(executions),
                    "peak_rss_bytes": max(int(item["peak_rss_bytes"]) for item in executions),
                    "observed_spill_bytes": sum(int(item["spill_bytes"]) for item in executions),
                    "sequential_execution_verified": True,
                    "limits_verified": True,
                    "builder_started": False,
                },
            }
        )
        partition_path = evidence_root / "partition-pilot.json"
        _write_new_sealed(partition_path, partition_report)
        _advance(
            workspace,
            run_root,
            phase="pilot-passed",
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="passed",
            evidence={
                "replay_report": str(replay_report_path),
                "replay_report_digest": replay_report.get("canonical_digest"),
                "partition_report": str(partition_path),
                "partition_report_digest": partition_report.get("canonical_digest"),
                "builder_started": False,
            },
        )
        _advance(
            workspace,
            run_root,
            phase="awaiting-pilot-acceptance",
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="paused",
            evidence={
                "replay_report_digest": replay_report.get("canonical_digest"),
                "partition_report_digest": partition_report.get("canonical_digest"),
                "builder_started": False,
                "full_build_authorized": False,
            },
        )
        return {
            "run_id": run_id,
            "phase": "awaiting-pilot-acceptance",
            "status": "paused",
            "builder_started": False,
            "replay_report": str(replay_report_path),
            "replay_report_digest": replay_report.get("canonical_digest"),
            "partition_report": str(partition_path),
            "partition_report_digest": partition_report.get("canonical_digest"),
        }
    except (
        DwdReplayDeterminismAuditError,
        DwdV21PilotError,
        DwdV21RuntimeError,
        OSError,
    ) as error:
        stamp = datetime.now(timezone.utc).strftime("pilot-failed-%Y%m%dT%H%M%SZ")
        with contextlib.suppress(DwdV21RuntimeError, OSError):
            _advance(
                workspace,
                run_root,
                phase=stamp,
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="failed",
                evidence={"error": str(error), "builder_started": False},
            )
        if isinstance(error, DwdV21PilotError):
            raise
        raise DwdV21PilotError(str(error)) from error
    finally:
        os.close(lock_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--ods-base", type=Path)
    parser.add_argument("--dwd-base", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--expected-revision")
    parser.add_argument("--expected-contract-digest")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--memory-limit-bytes", type=int, default=12 * 1024**3)
    parser.add_argument("--spill-limit-bytes", type=int, default=8 * 1024**3)
    parser.add_argument("--thread-limit", type=int, default=4)
    parser.add_argument("--batch-size-rows", type=int, default=256)
    parser.add_argument("--worker-spec-json", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.worker_spec_json is not None:
            raw: Any = json.loads(args.worker_spec_json)
            if not isinstance(raw, dict):
                raise DwdV21PilotError("worker specification is not an object")
            print(json.dumps(run_worker(raw), ensure_ascii=True, sort_keys=True))
            return 0
        required = {
            "repository": args.repository,
            "workspace": args.workspace,
            "ods-base": args.ods_base,
            "dwd-base": args.dwd_base,
            "environment": args.environment,
            "run-id": args.run_id,
            "expected-revision": args.expected_revision,
            "expected-contract-digest": args.expected_contract_digest,
        }
        missing = sorted(key for key, value in required.items() if value is None)
        if missing:
            raise DwdV21PilotError(f"missing required arguments: {', '.join(missing)}")
        result = run_pilots(
            repository=cast(Path, args.repository),
            workspace=cast(Path, args.workspace),
            ods_base=cast(Path, args.ods_base),
            dwd_base=cast(Path, args.dwd_base),
            environment_path=cast(Path, args.environment),
            run_id=cast(str, args.run_id),
            expected_revision=cast(str, args.expected_revision),
            expected_contract_digest=cast(str, args.expected_contract_digest),
            python=args.python,
            memory_limit_bytes=args.memory_limit_bytes,
            spill_limit_bytes=args.spill_limit_bytes,
            thread_limit=args.thread_limit,
            batch_size_rows=args.batch_size_rows,
        )
    except (DwdV21PilotError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"DWD V2.1 pilot failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
