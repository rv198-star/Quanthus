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


def hf_public(
    root: Path,
    secret: Path,
    receipt: Path,
    repo: str,
    expected_parent: str,
    revision: str = "main",
) -> None:
    verified = verify_manifest(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    ready = json.loads((root / "READY").read_bytes())
    if (
        manifest["status"] != "READY"
        or ready["status"] != "READY"
        or ready["acceptance_sha256"] != file_sha256(root / "ACCEPTANCE.json")
        or ready["contract_sha256"] != file_sha256(root / "contract.json")
    ):
        raise ValueError("RELEASE_READY_BINDINGS_INVALID")
    settings = {}
    for line in secret.read_text().splitlines():
        parts = shlex.split(line)
        if parts and "=" in parts[-1]:
            key, value = parts[-1].split("=", 1)
            settings[key] = value
    api = importlib.import_module("huggingface_hub").HfApi(token=settings["HF_TOKEN"])
    if revision != "main":
        api.create_branch(
            repo, branch=revision, repo_type="dataset", revision=expected_parent, exist_ok=True
        )
    if api.repo_info(repo, repo_type="dataset", revision=revision).sha != expected_parent:
        raise RuntimeError("PUBLIC_RELEASE_PARENT_CHANGED")
    local_paths = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    remote_paths = api.list_repo_files(repo, repo_type="dataset", revision=revision)
    obsolete = sorted(set(remote_paths) - local_paths - {".gitattributes"})
    commit = api.upload_folder(
        repo_id=repo,
        repo_type="dataset",
        folder_path=str(root),
        revision=revision,
        delete_patterns=obsolete,
        parent_commit=expected_parent,
        commit_message="Publish accepted Hist-1D release through "
        + json.loads((root / "contract.json").read_bytes())["scope"].get(
            "business_through", "2019-12-31"
        ),
    )
    api.update_repo_settings(repo, repo_type="dataset", private=False)
    if api.repo_info(repo, repo_type="dataset", revision=commit.oid).private:
        raise RuntimeError("PUBLIC_VISIBILITY_VERIFICATION_FAILED")
    result = {
        "status": "PUBLIC_READY_RELEASE",
        "repo": repo,
        "revision": commit.oid,
        "branch": revision,
        "obsolete_paths_removed": obsolete,
        "local_verification": verified,
        "acceptance_profile": ready["profile"],
    }
    save(receipt, result)
    print(json.dumps(result), flush=True)


def release_delta(
    root: Path, previous: Path, workspace: Path, output: Path, prior_backup: Path, publication: Path
) -> None:
    verified = verify_manifest(root)
    prior = verify_manifest(previous)
    old = {
        x["path"]: x["sha256"]
        for x in json.loads((previous / "manifest.json").read_bytes())["members"]
    }
    current = json.loads((root / "manifest.json").read_bytes())
    changed = [x["path"] for x in current["members"] if old.get(x["path"]) != x["sha256"]]
    changed.append("manifest.json")
    backup = json.loads(prior_backup.read_bytes())
    if backup["status"] != "ALL_OBJECTS_FULL_READBACK_PASSED":
        raise ValueError("PREDECESSOR_BACKUP_NOT_VERIFIED")
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(output / "HIST1D-RELEASE-OVERLAY.tar.gz", "w:gz", compresslevel=1) as archive:
        for name in sorted(changed):
            archive.add(root / name, arcname=name, recursive=False)
    sources = [p for p in (workspace / "raw/baostock-status").rglob("*") if p.is_file()]
    sources.extend(p for p in workspace.glob("BINARY-STATUS-*.json") if p.is_file())
    if any(p.is_symlink() for p in sources):
        raise ValueError("STATUS_RAW_SYMLINK")
    save(output / "RAW-MEMBERS.json", {"members": [member(workspace, p) for p in sorted(sources)]})
    with tarfile.open(
        output / "HIST1D-BINARY-STATUS-RAW.tar.gz", "w:gz", compresslevel=1
    ) as archive:
        for source in sorted(sources):
            archive.add(source, arcname=source.relative_to(workspace), recursive=False)
    save(
        output / "PREDECESSOR-BACKUP.json",
        {"candidate_manifest": prior, "backup": backup, "release_manifest": verified},
    )
    save(output / "PUBLICATION-RECEIPT.json", json.loads(publication.read_bytes()))
    (output / "RESTORE.md").write_text("""# Restore Hist-1D v1

1. Verify and extract hist-1d-candidate.tar from PREDECESSOR-BACKUP.json.
2. Verify and extract HIST1D-RELEASE-OVERLAY.tar.gz into its hist-1d-candidate folder.
3. Run python -m quanthus.pipelines.hist_1d_release --verify RESTORED_FOLDER.
4. Restore prior raw-supplement and HIST1D-BINARY-STATUS-RAW.tar.gz separately;
   validate original/new raw member SHA256 manifests. Raw paths are workspace-relative.

The immutable predecessor and this overlay form the complete release snapshot.
PUBLICATION-RECEIPT.json binds its public dataset revision and anonymous readback.
""")
    objects = [member(output, p) for p in sorted(output.iterdir()) if p.name != "RAW-MEMBERS.json"]
    save(
        output / "BACKUP-MANIFEST.json",
        {"status": "RELEASE_WITH_VERIFIED_PREDECESSOR", "release": verified, "objects": objects},
    )
    print(
        json.dumps(
            {
                "release": verified,
                "changed_members": len(changed),
                "new_backup_bytes": sum(x["bytes"] for x in objects),
            }
        ),
        flush=True,
    )


def drive_backup(
    packs: Path,
    helper: Path,
    parent: str,
    receipt: Path,
    folder_name: str = "quanthus-hist-1d-candidate-20261111-a1",
) -> None:
    # Import generic methods only. The legacy helper's Base-specific main is never invoked.
    namespace = runpy.run_path(str(helper), run_name="hist1d_drive_helper")
    drive = namespace["Drive"]()
    drive_error = namespace["DriveFailure"]
    folder = drive.folder(parent, folder_name)
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
    parser.add_argument(
        "action", choices=["pack", "hf-private", "hf-public", "release-delta", "drive-backup"]
    )
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--packs", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--hf-secret", type=Path)
    parser.add_argument("--repo", default="rv192/quanthus-hist-1d")
    parser.add_argument("--drive-helper", type=Path)
    parser.add_argument("--drive-parent")
    parser.add_argument("--drive-folder", default="quanthus-hist-1d-candidate-20261111-a1")
    parser.add_argument("--previous-candidate", type=Path)
    parser.add_argument("--prior-backup", type=Path)
    parser.add_argument("--publication-receipt", type=Path)
    parser.add_argument("--expected-parent")
    parser.add_argument("--hf-revision", default="main")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "pack":
        pack(args.candidate, args.workspace, args.packs)
    elif args.action == "hf-private":
        hf_private(args.candidate, args.hf_secret, args.receipt, args.repo)
    elif args.action == "hf-public":
        hf_public(
            args.candidate,
            args.hf_secret,
            args.receipt,
            args.repo,
            args.expected_parent,
            args.hf_revision,
        )
    elif args.action == "release-delta":
        release_delta(
            args.candidate,
            args.previous_candidate,
            args.workspace,
            args.packs,
            args.prior_backup,
            args.publication_receipt,
        )
    else:
        drive_backup(
            args.packs, args.drive_helper, args.drive_parent, args.receipt, args.drive_folder
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
