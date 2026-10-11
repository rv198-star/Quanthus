#!/usr/bin/env python3
"""Build native-input overlays from pinned deployed images; never replace services."""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASELINE = "3d74d1f43000e8b9b2d0b938cb48c1026a993781"
RUNTIME_PACKAGE = "/usr/local/lib/python3.11/site-packages/quanthus"
BASE_IMAGES = {
    "api": "sha256:65f35068b6467fd58148a865e0c1421d3a9fef2dc2b8404a4226a1cbb7fb64d8",
    "event-worker": "sha256:3477f9201848cf5ff26ade4ddd1971296bf2def5aba99886667b89595c4efa3d",
    "replay-worker": "sha256:ee9235b066a5334a545ebc92f0b51da4b522769b525252c1d1947c32325dc331",
}
COMMON_PATCHES = (
    "src/quanthus/cli.py",
    "src/quanthus/backtest/direct_dwd_provider.py",
    "src/quanthus/event_library/source.py",
    "src/quanthus/event_library/physical_scan.py",
    "src/quanthus/event_library/shard_tasks.py",
    "src/quanthus/event_library/sharded_batch.py",
)
COMMON_ADDITIONS = (
    "src/quanthus/backtest/native_dwd_source.py",
    "src/quanthus/pipelines/securities_warehouse/native_base23_reader.py",
)
RUNTIME = "src/quanthus/platform/data_runtime.py"
CONFIGS = (
    "config/replay-data-provider.base23.v1.json",
    "config/p4-event-scan-source.base23.v1.json",
    "config/p4-direct-dwd-canonical-projection.base23.v1.json",
    "config/releases/base23-oci-consumers.v1.json",
)


def run(arguments: list[str], *, cwd: Path | None = None, data: bytes | None = None) -> bytes:
    result = subprocess.run(arguments, cwd=cwd, input=data, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"{arguments[0]} operation failed ({result.returncode}): "
            + result.stderr.decode(errors="replace")[-6000:]
        )
    return result.stdout


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def source_patch(project: Path, revision: str, name: str) -> bytes:
    if name != "src/quanthus/event_library/physical_scan.py":
        return run(["git", "diff", BASELINE, revision, "--", name], cwd=project)
    before = run(["git", "show", f"{BASELINE}:{name}"], cwd=project).decode()
    after = (project / name).read_text()
    old_nodes = ast.parse(before).body
    new_nodes = ast.parse(after).body

    def selected(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.ImportFrom) and node.module == "quanthus.event_library.source"
        ) or (
            isinstance(node, ast.FunctionDef)
            and node.name in {"_table_paths", "build_physical_event_scan"}
        )

    old = [node for node in old_nodes if selected(node)]
    new = [node for node in new_nodes if selected(node)]
    if len(old) != 3 or len(new) != 3:
        raise RuntimeError("native physical-scan edit boundaries differ")
    lines = before.splitlines(keepends=True)
    new_lines = after.splitlines(keepends=True)
    for original, replacement in reversed(list(zip(old, new, strict=True))):
        lines[original.lineno - 1 : original.end_lineno] = new_lines[
            replacement.lineno - 1 : replacement.end_lineno
        ]
    minimal = "".join(lines)
    if ast.dump(ast.parse(minimal)) != ast.dump(ast.parse(after)):
        raise RuntimeError("physical-scan minimal patch would omit a semantic change")
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            minimal.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        )
    ).encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=tuple(BASE_IMAGES), required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    revision = run(["git", "rev-parse", "HEAD"], cwd=project).decode().strip()
    if run(["git", "status", "--porcelain"], cwd=project).strip():
        raise RuntimeError("overlay source must be a clean verified GitHub checkout")
    if args.report.exists():
        raise RuntimeError("overlay report already exists")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.report.parent).free < 4 * 1024**3:
        raise RuntimeError("overlay build requires the 4 GiB production disk reserve")
    base_image = BASE_IMAGES[args.kind]
    actual = json.loads(run(["docker", "image", "inspect", base_image]))[0]
    if actual["Id"] != base_image:
        raise RuntimeError("pinned base image identity differs")
    runtime = (
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--read-only",
                "--memory=512m",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint",
                "python",
                base_image,
                "-c",
                "import pathlib,quanthus; print(pathlib.Path(quanthus.__file__).parent)",
            ]
        )
        .decode()
        .strip()
    )
    if runtime != RUNTIME_PACKAGE:
        raise RuntimeError("pinned image runtime package path differs")
    configs = {}
    for name in CONFIGS:
        path = project / name
        if not path.is_file():
            raise RuntimeError(f"required native contract is missing: {name}")
        configs[name] = sha(path)
    receipt = json.loads((project / CONFIGS[2]).read_text())
    if receipt.get("materializer_version") != "direct-dwd-native-base23-v1":
        raise RuntimeError("native input qualification is absent")
    patches = ["src/quanthus/platform/api.py"] if args.kind == "api" else list(COMMON_PATCHES)
    if args.kind == "event-worker":
        patches.append("src/quanthus/platform/event_worker.py")
    additions = [RUNTIME] + ([] if args.kind == "api" else list(COMMON_ADDITIONS))
    tag = f"quanthus/base23-{args.kind}:{revision[:12]}"
    report = {
        "schema_version": "quanthus.base23-consumer-overlay.v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "kind": args.kind,
        "code_revision": revision,
        "patch_baseline": BASELINE,
        "base_image": base_image,
        "image_tag": tag,
        "production_changed": False,
        "runtime_package": runtime,
        "contracts": configs,
        "files": {},
    }
    with tempfile.TemporaryDirectory(prefix="native-overlay-", dir=args.report.parent) as tmp:
        context = Path(tmp)
        overlay = context / "overlay"
        overlay.mkdir()
        container = (
            run(["docker", "create", "--entrypoint", "/bin/true", base_image]).decode().strip()
        )
        try:
            for name in patches:
                destination = overlay / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                run(["docker", "cp", f"{container}:/app/{name}", str(destination)])
                before = sha(destination)
                installed = context / "installed-preimage"
                relative = name.removeprefix("src/quanthus/")
                run(["docker", "cp", f"{container}:{runtime}/{relative}", str(installed)])
                if installed.read_bytes() != destination.read_bytes():
                    raise RuntimeError(f"source/runtime preimages differ: {name}")
                installed.unlink()
                patch = source_patch(project, revision, name)
                if not patch.strip():
                    raise RuntimeError(f"selected overlay has no versioned change: {name}")
                run(["git", "apply", "--check", "-"], cwd=overlay, data=patch)
                run(["git", "apply", "-"], cwd=overlay, data=patch)
                report["files"][name] = {
                    "before": before,
                    "after": sha(destination),
                    "mode": "strict_patch",
                    "source_runtime_preimages_equal": True,
                    "patch_sha256": "sha256:" + hashlib.sha256(patch).hexdigest(),
                }
            for name in additions:
                destination = overlay / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                existing = context / "existing-module"
                relative = name.removeprefix("src/quanthus/")
                for location in (f"/app/{name}", f"{runtime}/{relative}"):
                    copied = subprocess.run(
                        ["docker", "cp", f"{container}:{location}", str(existing)],
                        capture_output=True,
                        check=False,
                    )
                    if copied.returncode == 0:
                        if existing.read_bytes() != (project / name).read_bytes():
                            raise RuntimeError(
                                f"new task module already exists with different bytes: {location}"
                            )
                        existing.unlink()
                    elif b"could not find the file" not in copied.stderr.lower():
                        raise RuntimeError(f"could not establish absent task module: {location}")
                shutil.copyfile(project / name, destination)
                report["files"][name] = {"after": sha(destination), "mode": "new_task_module"}
            for name in CONFIGS:
                destination = overlay / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(project / name, destination)
        finally:
            run(["docker", "rm", container])
        for path in overlay.rglob("*.py"):
            compile(path.read_text(), str(path), "exec")
        (context / "Dockerfile").write_text(
            f"FROM {base_image}\n"
            "COPY overlay/ /app/\n"
            f"COPY overlay/src/quanthus/ {runtime}/\n"
            f'LABEL org.quanthus.base23.overlay-revision="{revision}"\n'
            f'LABEL org.quanthus.base23.base-image="{base_image}"\n'
        )
        build_output = run(
            ["docker", "build", "--network=none", "--pull=false", "-t", tag, "."],
            cwd=context,
        )
        report["build_output_sha256"] = "sha256:" + hashlib.sha256(build_output).hexdigest()
    result = json.loads(run(["docker", "image", "inspect", tag]))[0]
    report["image_id"] = result["Id"]
    for field in ("Entrypoint", "Cmd", "User", "WorkingDir", "Env", "Healthcheck"):
        if result["Config"].get(field) != actual["Config"].get(field):
            raise RuntimeError(
                f"overlay unexpectedly changed inherited image configuration: {field}"
            )
    report["status"] = "BUILT_NOT_DEPLOYED"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "image": tag,
                "image_id": report["image_id"],
                "report": str(args.report),
                "report_sha256": sha(args.report),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
