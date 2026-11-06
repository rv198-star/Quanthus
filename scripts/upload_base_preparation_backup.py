"""Upload immutable prepared Base files to a new Drive backup folder.

Uses the existing operator OAuth bridge, keeps resumable URLs in private state
files, and verifies Drive size/checksums before producing a success receipt.
Existing files are never replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib import error, parse, request

API = "https://www.googleapis.com/drive/v3/files"
FIELDS = "id,name,size,md5Checksum,sha256Checksum"
CHUNK = 8 * 1024 * 1024


def private_save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".partial")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Drive:
    def __init__(self, config: Path, proxy: str):
        self.secret = json.loads(config.read_bytes())
        self.opener = request.build_opener(request.ProxyHandler({"https": proxy, "http": proxy}))
        self.token = ""

    def refresh(self) -> None:
        req = request.Request(
            self.secret["oauth_url"],
            headers={"X-AuthID": self.secret["authid"], "User-Agent": "Duplicati/2.4.0"},
        )
        with self.opener.open(req, timeout=60) as response:
            self.token = json.load(response)["access_token"]

    def call(self, url: str, method: str = "GET", body=None, headers=None):
        if not self.token:
            self.refresh()
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers = {"Content-Type": "application/json", **(headers or {})}
        for attempt in range(2):
            req = request.Request(
                url,
                data=body,
                method=method,
                headers={"Authorization": "Bearer " + self.token, **(headers or {})},
            )
            try:
                with self.opener.open(req, timeout=60) as response:
                    raw = response.read()
                    return response.status, dict(response.headers), json.loads(raw) if raw else None
            except error.HTTPError as exc:
                status, response_headers = exc.code, dict(exc.headers)
                exc.close()
                if status == 308:
                    return status, response_headers, None
                if status == 401 and attempt == 0:
                    self.refresh()
                    continue
                raise RuntimeError("DRIVE_HTTP_" + str(status)) from None
        raise RuntimeError("DRIVE_AUTH_RETRY_EXHAUSTED")

    def children(self, parent: str) -> list[dict]:
        rows, cursor = [], None
        while True:
            query = {
                "q": f"'{parent}' in parents and trashed=false",
                "fields": "files(" + FIELDS + ",mimeType),nextPageToken",
                "pageSize": 1000,
            }
            if cursor:
                query["pageToken"] = cursor
            _, _, result = self.call(API + "?" + parse.urlencode(query))
            rows.extend(result.get("files", []))
            cursor = result.get("nextPageToken")
            if not cursor:
                return rows

    def folder(self, parent: str, name: str) -> str:
        found = [x for x in self.children(parent) if x["name"] == name]
        if found:
            if len(found) != 1 or found[0]["mimeType"] != "application/vnd.google-apps.folder":
                raise ValueError("BACKUP_FOLDER_CONFLICT")
            return found[0]["id"]
        _, _, result = self.call(
            API + "?fields=id",
            "POST",
            {"name": name, "parents": [parent], "mimeType": "application/vnd.google-apps.folder"},
        )
        return result["id"]


def acknowledged(headers: dict) -> int:
    value = next((v for k, v in headers.items() if k.lower() == "range"), None)
    return int(value.split("-")[-1]) + 1 if value else 0


def verify_remote(result: dict, size: int, md5: str, sha256: str) -> None:
    if int(result["size"]) != size or result.get("md5Checksum") != md5:
        raise ValueError("REMOTE_SIZE_OR_MD5_DIFFERS")
    if result.get("sha256Checksum") and result["sha256Checksum"] != sha256:
        raise ValueError("REMOTE_SHA256_DIFFERS")


def upload(drive: Drive, parent: str, path: Path, state_dir: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("BACKUP_SOURCE_NOT_INDEPENDENT_FILE")
    before = path.stat()
    size = before.st_size
    if size <= 0 or size > 32 * 1024**3:
        raise ValueError("BACKUP_FILE_SIZE_BOUND")
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(16 * 1024 * 1024), b""):
            md5.update(block)
            sha256.update(block)
    md5, sha256 = md5.hexdigest(), sha256.hexdigest()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        path.stat().st_size,
        path.stat().st_mtime_ns,
        path.stat().st_ctime_ns,
    ):
        raise ValueError("BACKUP_SOURCE_CHANGED")
    print(
        json.dumps({"file": path.name, "status": "HASHED", "bytes": size, "sha256": sha256}),
        flush=True,
    )
    existing = [row for row in drive.children(parent) if row["name"] == path.name]
    if existing:
        if len(existing) != 1:
            raise ValueError("BACKUP_REMOTE_NAME_CONFLICT")
        result = existing[0]
        verify_remote(result, size, md5, sha256)
    else:
        state_path = state_dir / (hashlib.sha256(path.name.encode()).hexdigest()[:20] + ".json")
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state["sha256"] != sha256 or state["size"] != size:
                raise ValueError("BACKUP_RESUME_SOURCE_CHANGED")
            session = state["session"]
        else:
            _, headers, _ = drive.call(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields="
                + FIELDS,
                "POST",
                {"name": path.name, "parents": [parent]},
                {
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "application/octet-stream",
                },
            )
            session = next(v for k, v in headers.items() if k.lower() == "location")
            private_save(state_path, {"sha256": sha256, "size": size, "session": session})
        status, headers, result = drive.call(
            session, "PUT", b"", {"Content-Length": "0", "Content-Range": "bytes */" + str(size)}
        )
        offset = acknowledged(headers) if status == 308 else size
        with path.open("rb") as source:
            while offset < size:
                previous_offset = offset
                source.seek(offset)
                block = source.read(min(CHUNK, size - offset))
                for attempt in range(5):
                    try:
                        status, headers, result = drive.call(
                            session,
                            "PUT",
                            block,
                            {
                                "Content-Length": str(len(block)),
                                "Content-Type": "application/octet-stream",
                                "Content-Range": f"bytes {offset}-{offset + len(block) - 1}/{size}",
                            },
                        )
                        break
                    except (RuntimeError, error.URLError, TimeoutError):
                        if attempt == 4:
                            raise
                        time.sleep(2**attempt)
                        status, headers, result = drive.call(
                            session,
                            "PUT",
                            b"",
                            {"Content-Length": "0", "Content-Range": "bytes */" + str(size)},
                        )
                        confirmed = acknowledged(headers) if status == 308 else size
                        if confirmed != offset:
                            offset = confirmed
                            break
                else:
                    raise RuntimeError("UPLOAD_RETRY_EXHAUSTED")
                new_offset = acknowledged(headers) if status == 308 else size
                if new_offset <= previous_offset or new_offset > size:
                    raise ValueError("UPLOAD_ACK_OUTSIDE_SOURCE")
                offset = new_offset
                print(
                    json.dumps({"file": path.name, "uploaded_bytes": offset, "total": size}),
                    flush=True,
                )
        if not result:
            raise ValueError("UPLOAD_COMPLETION_METADATA_MISSING")
        _, _, result = drive.call(API + "/" + result["id"] + "?fields=" + FIELDS)
        verify_remote(result, size, md5, sha256)
    return {
        "id": result["id"],
        "name": path.name,
        "size_bytes": size,
        "md5": md5,
        "sha256": sha256,
        "remote_sha256_verified": bool(result.get("sha256Checksum")),
        "status": "REMOTE_METADATA_SIZE_AND_CHECKSUM_VERIFIED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--auth-config", type=Path, required=True)
    parser.add_argument("--parent-id", required=True)
    parser.add_argument("--folder-name", required=True)
    parser.add_argument("--proxy", default="http://127.0.0.1:10800")
    parser.add_argument("--file", action="append", required=True)
    args = parser.parse_args()
    paths = [args.directory / name for name in args.file]
    if any(Path(name).name != name for name in args.file):
        raise ValueError("BACKUP_FILE_PATH_INVALID")
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    drive = Drive(args.auth_config, args.proxy)
    parent = drive.folder(args.parent_id, args.folder_name)
    receipts = []
    for path in paths:
        receipt = upload(drive, parent, path, args.state_dir)
        receipts.append(receipt)
        private_save(args.state_dir / "PROGRESS.json", {"folder_id": parent, "files": receipts})
    result = {
        "kind": "prepared-base-drive-backup-v1",
        "folder_id": parent,
        "folder_url": "https://drive.google.com/drive/folders/" + parent,
        "files": receipts,
        "scope": "versioned preparation backup; consumer bindings unchanged",
    }
    private_save(args.state_dir / "SUCCESS.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
