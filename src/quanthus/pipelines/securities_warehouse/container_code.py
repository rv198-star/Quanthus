"""Small, immutable Git source bundles for the P10 batch image (#537)."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

MARKER = "SOURCE.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def bundle(repo: Path, revision: str, output: Path) -> dict[str, Any]:
    """Export tracked source/config/project metadata only, never a checkout's untracked secrets."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("CONTAINER_FULL_REVISION_REQUIRED")
    if output.exists() or output.is_symlink():
        raise ValueError("CONTAINER_NEW_BUNDLE_REQUIRED")
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", revision, "src", "config", "pyproject.toml"], cwd=repo
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
        for member in members:
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or member.issym()
                or member.islnk()
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError("CONTAINER_UNSAFE_ARCHIVE_MEMBER")
        output.mkdir(parents=True)
        for member in members:
            target = output / member.name
            if member.isdir():
                target.mkdir(exist_ok=True, parents=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                assert source is not None
                with source, target.open("xb") as stream:
                    stream.write(source.read())
    files = {
        p.relative_to(output).as_posix(): {"bytes": p.stat().st_size, "sha256": digest(p)}
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    value = {"kind": "p10-git-source-bundle-v1", "revision": revision, "files": files}
    (output / MARKER).write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return value


def verify_bundle(root: Path, revision: str) -> dict[str, Any]:
    """Fail closed on extra/missing files, links, scan failures and code changes."""
    root = root.absolute()
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("CONTAINER_PHYSICAL_BUNDLE_REQUIRED")
    value: dict[str, Any] = json.loads((root / MARKER).read_bytes())
    if value.get("kind") != "p10-git-source-bundle-v1" or value.get("revision") != revision:
        raise ValueError("CONTAINER_SOURCE_REVISION_DIFFERS")
    files = value.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("CONTAINER_SOURCE_INVENTORY_REQUIRED")
    import os

    actual = set()

    def failed(error: OSError) -> None:
        raise error

    for directory, dirs, names in os.walk(root, onerror=failed, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("CONTAINER_SOURCE_LINK_FORBIDDEN")
            if name not in dirs:
                actual.add(path.relative_to(root).as_posix())
    if actual != set(files) | {MARKER}:
        raise ValueError("CONTAINER_SOURCE_FILE_SET_DIFFERS")
    for name, entry in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("CONTAINER_SOURCE_UNSAFE_MEMBER")
        member = root / name
        if member.stat().st_size != entry["bytes"] or digest(member) != entry["sha256"]:
            raise ValueError("CONTAINER_SOURCE_MEMBER_DIFFERS")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collector-repo", type=Path)
    parser.add_argument("--python-image")
    parser.add_argument("--rclone-image")
    args = parser.parse_args()
    if args.collector_repo is not None:
        from .daily_base24_provider import COLLECTOR

        if not re.fullmatch(r"python:[a-zA-Z0-9.-]+@sha256:[0-9a-f]{64}", args.python_image or ""):
            raise ValueError("CONTAINER_PINNED_PYTHON_IMAGE_REQUIRED")
        if not re.fullmatch(
            r"rclone/rclone:[a-zA-Z0-9.-]+@sha256:[0-9a-f]{64}", args.rclone_image or ""
        ):
            raise ValueError("CONTAINER_PINNED_RCLONE_IMAGE_REQUIRED")
        for root in (args.repo, args.collector_repo):
            a, b = args.output.resolve(), root.resolve()
            if a.is_relative_to(b) or b.is_relative_to(a):
                raise ValueError("CONTAINER_CONTEXT_SOURCE_OVERLAP")
        if args.output.exists():
            raise ValueError("CONTAINER_NEW_CONTEXT_REQUIRED")
        bundle(args.repo, args.revision, args.output / "pipeline")
        bundle(args.collector_repo, COLLECTOR, args.output / "collector")
        for name in ("Dockerfile", "Dockerfile.dockerignore", "runtime.lock"):
            content = subprocess.check_output(
                ["git", "show", args.revision + ":ops/data/" + name], cwd=args.repo
            )
            (args.output / name).write_bytes(content)
        (args.output / "python-image.txt").write_text(args.python_image + "\n", encoding="ascii")
        (args.output / "rclone-image.txt").write_text(args.rclone_image + "\n", encoding="ascii")
        print(json.dumps({"revision": args.revision, "context": str(args.output)}))
    else:
        value = bundle(args.repo, args.revision, args.output)
        print(json.dumps({"revision": value["revision"], "files": len(value["files"])}))


if __name__ == "__main__":
    main()
