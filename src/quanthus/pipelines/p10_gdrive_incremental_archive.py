"""Bounded incremental archives preserve the exact native package on restoration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_delivery_common import (
    BASE23_CONTENT,
    BASE23_MANIFEST,
    COMPLETE_KIND,
    file_digest,
    object_at,
    safe_path,
    verify_seal,
)


def delta_reference(
    source: Path, state: Path, policy: dict[str, Any], day: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    candidates = [
        state / "LAST_SUCCESS.json",
        Path(policy["bootstrap_archive_state"]) / "LAST_SUCCESS.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        done = object_at(path)
        if (
            done.get("policy_id")
            not in {"p10-complete-daily-gdrive-v3", "p10-complete-daily-gdrive-v4"}
            or done.get("base", {}).get("manifest_sha256") != BASE23_MANIFEST
            or done.get("base", {}).get("content_digest") != BASE23_CONTENT
            or done.get("consumable_intervals") != ["1d", "1m", "5m"]
        ):
            raise ValueError("archive dependency product or purpose binding differs")
        if done.get("status") != "cloud_archive_verified" or not done.get(
            "cloud_ready_confirmed_at"
        ):
            raise ValueError("archive dependency has no actual cloud confirmation")
        confirmed = done["cloud_ready_confirmed_at"]
        if (
            datetime.fromisoformat(
                confirmed.removesuffix("Z") + "+00:00" if confirmed.endswith("Z") else confirmed
            ).tzinfo
            is None
        ):
            raise ValueError("archive dependency confirmation has no timezone")
        if done["market_date"] > day:
            continue
        depth = done.get("archive_depth", 0)
        if type(depth) is not int or depth < 0:
            raise ValueError("invalid archive dependency depth")
        if depth >= policy["max_delta_depth"]:
            return None, {}
        package = Path(done["source_package_path"])
        relative = package.relative_to(source)
        package = safe_path(source, relative.as_posix())
        manifest_path = package / "manifest.json"
        if file_digest(manifest_path) != done["manifest_sha256"]:
            raise ValueError("archive dependency source manifest differs")
        manifest = object_at(manifest_path)
        if (
            manifest.get("kind") != COMPLETE_KIND
            or manifest.get("content_digest") != done["content_digest"]
        ):
            raise ValueError("archive dependency source identity differs")
        reference = {
            key: done[key]
            for key in (
                "remote",
                "archive_name",
                "archive_sha256",
                "archive_md5",
                "archive_bytes",
                "manifest_sha256",
                "content_digest",
                "archive_policy_sha256",
            )
        }
        reference["archive_depth"] = depth
        return reference, {item["path"]: item for item in manifest["files"]}
    return None, {}


def restore_bundles(bundles: list[Path], output: Path, expected_manifest: str) -> dict[str, Any]:
    """Restore in a fresh scratch; no publication, supplier requests or Base scan."""
    if output.exists() or not 1 <= len(bundles) <= 28:
        raise ValueError("restore requires a new output and at most 28 explicit bundles")
    reports = {}
    for bundle in bundles:
        report = object_at(bundle / "archive-manifest.json")
        identity = (report["manifest_sha256"], report["archive_sha256"])
        if identity in reports:
            raise ValueError("ambiguous archive identity")
        reports[identity] = (bundle, report)
    target = object_at(bundles[-1] / "archive-manifest.json")
    if target["manifest_sha256"] != expected_manifest:
        raise ValueError("requested target manifest differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    required = sum(
        report.get("packed_source_bytes", report["source_bytes"]) for _, report in reports.values()
    )
    if shutil.disk_usage(output.parent).free < 4 * 1024**3 + required:
        raise ValueError("restore lacks physical budget plus 4 GiB reserve")
    scratch = Path(tempfile.mkdtemp(prefix=".complete-archive-restore-", dir=output.parent))
    restored: dict[tuple[str, str], Path] = {}
    active: set[tuple[str, str]] = set()

    def restore(identity: tuple[str, str]) -> Path:
        if identity in restored:
            return restored[identity]
        if identity in active or identity not in reports or len(active) >= 28:
            raise ValueError("missing or cyclic archive dependency")
        active.add(identity)
        bundle, report = reports[identity]
        archive = safe_path(bundle, report["archive_name"])
        if file_digest(archive) != report["archive_sha256"]:
            raise ValueError("physical archive checksum differs")
        root = scratch / str(len(restored) + len(active))
        root.mkdir()
        expanded = 0
        ceiling = report.get("packed_source_bytes", report["source_bytes"])
        if type(ceiling) is not int or not 0 <= ceiling <= 4 * 1024**3:
            raise ValueError("archive expansion budget is invalid")
        process = subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE)
        assert process.stdout is not None
        try:
            seen = set()
            with tarfile.open(fileobj=process.stdout, mode="r|") as reader:
                for member in reader:
                    relative = Path(member.name).as_posix()
                    # The v3 full GNU tar archive starts with a root directory.
                    if relative == "." and member.isdir():
                        continue
                    destination = safe_path(root, relative)
                    if relative in seen:
                        raise ValueError("duplicate archive member")
                    seen.add(relative)
                    if len(seen) > 100000:
                        raise ValueError("archive member census exceeds restore budget")
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile() or member.size > 4 * 1024**3:
                        raise ValueError("nonregular or oversized archive member")
                    expanded += member.size
                    if expanded > ceiling:
                        raise ValueError("archive expansion exceeds declared physical budget")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    stream = reader.extractfile(member)
                    assert stream is not None
                    with stream, destination.open("xb") as sink:
                        shutil.copyfileobj(stream, sink, 1024 * 1024)
            process.stdout.close()
            if process.wait(timeout=30):
                raise ValueError("compressed archive stream failed")
        except BaseException:
            process.kill()
            process.wait()
            raise
        reference = report.get("archive_reference")
        inherited = report.get("inherited_files", [])
        if reference:
            parent_identity = (reference["manifest_sha256"], reference["archive_sha256"])
            parent = restore(parent_identity)
            parent_report = reports[parent_identity][1]
            if any(
                parent_report.get(key) != reference[key]
                for key in (
                    "content_digest",
                    "archive_md5",
                    "archive_bytes",
                    "archive_policy_sha256",
                )
            ):
                raise ValueError("archive dependency metadata identity differs")
            for item in inherited:
                src = safe_path(parent, item["path"])
                dst = safe_path(root, item["path"])
                if src.stat().st_size != item["bytes"] or file_digest(src) != item["sha256"]:
                    raise ValueError("inherited file identity differs")
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.link(src, dst)
        elif inherited:
            raise ValueError("inherited files have no dependency")
        if file_digest(root / "manifest.json") != report["manifest_sha256"]:
            raise ValueError("restored manifest identity differs")
        manifest = verify_seal(root, COMPLETE_KIND)
        if manifest["content_digest"] != report["content_digest"]:
            raise ValueError("restored content identity differs")
        pointer = object_at(bundle / "source-pointer.json")
        receipt = object_at(bundle / "source-verification.json")
        if any(
            value.get("manifest_sha256") != report["manifest_sha256"]
            or value.get("content_digest") != report["content_digest"]
            for value in (pointer, receipt)
        ):
            raise ValueError("archive source evidence identity differs")
        if (
            manifest.get("base", {}).get("manifest_sha256") != BASE23_MANIFEST
            or manifest.get("base", {}).get("content_digest") != BASE23_CONTENT
            or receipt.get("base") != manifest["base"]
            or manifest.get("consumable_intervals") != ["1d", "1m", "5m"]
        ):
            raise ValueError("restored source product or purpose binding differs")
        expected = {x["path"] for x in manifest["files"]} | {"manifest.json", "READY"}
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if actual != expected:
            raise ValueError("restored file census differs")
        restored[identity] = root
        active.remove(identity)
        return root

    try:
        root = restore((target["manifest_sha256"], target["archive_sha256"]))
        os.replace(root, output)
        return {
            "status": "EXACT_PHYSICAL_PACKAGE_RESTORED",
            "manifest_sha256": expected_manifest,
            "content_digest": target["content_digest"],
            "dependencies": len(restored) - 1,
            "output": str(output),
            "consumer_publication": False,
            "semantic_receipt": str(bundles[-1] / "source-verification.json"),
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(restore_bundles(args.bundle, args.output, args.expected_manifest_sha256)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
