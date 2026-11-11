"""Back up a verified Hist-1D candidate and upload it to a private HF dataset."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import runpy
import shlex
import tarfile
import time
from pathlib import Path
from urllib.error import URLError

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_release import member, save, verify_manifest


def pack(candidate: Path, workspace: Path, output: Path) -> None:
    verified = verify_manifest(candidate)
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(output / "hist-1d-candidate.tar", "w") as archive:
        archive.add(candidate, arcname="hist-1d-candidate", recursive=True)
    sources = [p for p in workspace.glob("*.json") if p.is_file()]
    for directory in ["raw/baostock", "raw/source-probes"]:
        sources.extend(p for p in (workspace / directory).rglob("*") if p.is_file())
    if any(p.is_symlink() for p in sources):
        raise ValueError("RAW_BACKUP_SYMLINK")
    raw_manifest = {
        "members": [member(workspace, p) for p in sorted(set(sources))],
        "base23_archives": "Reuse verified cloud archive objects; see RECOVERY-RECEIPT.",
    }
    save(output / "RAW-MEMBERS.json", raw_manifest)
    with tarfile.open(output / "hist-1d-raw-supplement.tar", "w") as archive:
        for path in sorted(set(sources)):
            archive.add(path, arcname=path.relative_to(workspace), recursive=False)
        archive.add(output / "RAW-MEMBERS.json", arcname="RAW-MEMBERS.json")
    save(
        output / "BACKUP-MANIFEST.json",
        {
            "status": "PRIVATE_CANDIDATE_BACKUP",
            "candidate": verified,
            "objects": [member(output, p) for p in sorted(output.glob("*.tar"))],
        },
    )
    print(
        json.dumps({"packs": json.loads((output / "BACKUP-MANIFEST.json").read_bytes())}),
        flush=True,
    )


def hf_private(candidate: Path, secret: Path, receipt: Path, repo: str) -> None:
    verified = verify_manifest(candidate)
    settings = {}
    for line in secret.read_text().splitlines():
        tokens = shlex.split(line)
        if tokens and "=" in tokens[-1]:
            key, value = tokens[-1].split("=", 1)
            settings[key] = value
    token = next(
        (
            settings[k]
            for k in ["HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"]
            if settings.get(k)
        ),
        None,
    )
    if not token:
        raise RuntimeError("HF_TOKEN_KEY_MISSING")
    hub = importlib.import_module("huggingface_hub")
    api = hub.HfApi(token=token)
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    if not api.repo_info(repo, repo_type="dataset").private:
        raise RuntimeError("CANDIDATE_REPOSITORY_MUST_BE_PRIVATE")
    commit = api.upload_folder(
        repo_id=repo,
        repo_type="dataset",
        folder_path=str(candidate),
        commit_message="Stage Hist-1D v1 candidate; acceptance pending",
    )
    info = api.repo_info(repo, repo_type="dataset", revision=commit.oid)
    if not info.private:
        raise RuntimeError("CANDIDATE_VISIBILITY_CHANGED")
    result = {
        "status": "PRIVATE_CANDIDATE_UPLOADED",
        "repo": repo,
        "revision": commit.oid,
        "local_verification": verified,
    }
    save(receipt, result)
    print(json.dumps(result), flush=True)


def drive_backup(packs: Path, helper: Path, parent: str, receipt: Path) -> None:
    # Import generic methods only. The legacy helper's Base-specific main is never invoked.
    namespace = runpy.run_path(str(helper), run_name="hist1d_drive_helper")
    drive = namespace["Drive"]()
    drive_error = namespace["DriveFailure"]
    folder = drive.folder(parent, "quanthus-hist-1d-candidate-20261111-a1")
    manifest = json.loads((packs / "BACKUP-MANIFEST.json").read_bytes())
    paths = [packs / row["path"] for row in manifest["objects"]]
    paths.extend([packs / "RAW-MEMBERS.json", packs / "BACKUP-MANIFEST.json"])
    results = []
    for path in paths:
        expected, size = file_sha256(path), path.stat().st_size
        remote = drive.child(folder, path.name, refresh=True)
        if remote and int(remote.get("size", -1)) != size:
            raise RuntimeError("EXISTING_BACKUP_SIZE_MISMATCH:" + path.name)
        if not remote:
            session = drive.create_session(folder, path.name, size)
            position = 0
            with path.open("rb") as stream:
                while position < size:
                    stream.seek(position)
                    block = stream.read(min(8 * 1024**2, size - position))
                    last_byte = position + len(block) - 1
                    for attempt in range(5):
                        try:
                            with drive._open(
                                session,
                                method="PUT",
                                body=block,
                                headers={
                                    "Content-Length": str(len(block)),
                                        "Content-Range": f"bytes {position}-{last_byte}/{size}",
                                },
                                timeout=180,
                            ) as response:
                                remote = json.load(response)
                            position += len(block)
                            break
                        except drive_error as exc:
                            if exc.code == 308:
                                position = namespace["parse_range"](exc.headers.get("Range"))
                                break
                            if exc.code not in [429, 500, 502, 503, 504] or attempt == 4:
                                raise RuntimeError("DRIVE_UPLOAD_HTTP_" + str(exc.code)) from None
                        except (URLError, TimeoutError, OSError):
                            if attempt == 4:
                                raise RuntimeError("DRIVE_UPLOAD_TRANSPORT_FAILED") from None
                        time.sleep(min(20, 2**attempt))
                        completed, offset = drive.query_session(session, size)
                        if completed:
                            remote, position = completed, size
                            break
                        if offset != position:
                            position = offset
                            break
            drive.invalidate(folder)
        if not remote:
            raise RuntimeError("DRIVE_UPLOAD_NO_OBJECT")
        # Full bytes are read back independently; server-side checksum is supplementary.
        digest, received = hashlib.sha256(), 0
        with drive._open(
            "https://www.googleapis.com/drive/v3/files/" + remote["id"] + "?alt=media", timeout=180
        ) as response:
            while block := response.read(1024**2):
                digest.update(block)
                received += len(block)
        if received != size or digest.hexdigest() != expected:
            raise RuntimeError("DRIVE_FULL_READBACK_MISMATCH:" + path.name)
        result = {
            "name": path.name,
            "file_id": remote["id"],
            "bytes": size,
            "sha256": expected,
            "full_readback": "PASSED",
        }
        results.append(result)
        save(receipt, {"status": "BACKUP_IN_PROGRESS", "folder_id": folder, "objects": results})
        print(json.dumps(result), flush=True)
    save(
        receipt,
        {"status": "ALL_OBJECTS_FULL_READBACK_PASSED", "folder_id": folder, "objects": results},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["pack", "hf-private", "drive-backup"])
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--packs", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--hf-secret", type=Path)
    parser.add_argument("--repo", default="rv192/quanthus-hist-1d")
    parser.add_argument("--drive-helper", type=Path)
    parser.add_argument("--drive-parent")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "pack":
        pack(args.candidate, args.workspace, args.packs)
    elif args.action == "hf-private":
        hf_private(args.candidate, args.hf_secret, args.receipt, args.repo)
    else:
        drive_backup(args.packs, args.drive_helper, args.drive_parent, args.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
