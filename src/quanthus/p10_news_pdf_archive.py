"""Private CN02 PDF archive through the existing rclone OAuth channel.

Only news PDFs use this adapter. No Base archive, sharing or credential policy is
changed. A size and SHA256 readback is required before local staging is reclaimed.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
IMAGE = "rclone/rclone@sha256:49766d403471e6f22fe039cf895314e53012dc7fe3731f9066478c9f377a655c"


def instant() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".pending-" + uuid.uuid4().hex)
    with temporary.open("xb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DrivePdfArchive:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.token = ""
        self.expires = 0.0
        self.folders: dict[tuple[str, str], str] = {}
        self.requests = 0
        self.uploaded_bytes = 0
        self.root = str(config["folder_id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", self.root):
            raise ValueError("PDF_ARCHIVE_FOLDER_INVALID")
        self.opener = build_opener(ProxyHandler({"https": "http://127.0.0.1:10800"}))

    def refresh(self) -> None:
        path = Path(self.config["existing_rclone_config"])
        info = path.stat()
        if path.is_symlink() or info.st_mode & 0o077:
            raise ValueError("EXISTING_PRIVATE_RCLONE_CONFIG_REQUIRED")
        name = "p10-news-token-" + uuid.uuid4().hex[:12]
        command = [
            "sudo",
            "-n",
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network=host",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit=64",
            "--memory=512m",
            "--user=" + str(info.st_uid) + ":" + str(info.st_gid),
            "--env",
            "HOME=/tmp",
            "--env",
            "HTTPS_PROXY=http://127.0.0.1:10800",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
            "--volume",
            str(path.parent) + ":/config/rclone:rw",
            IMAGE,
            "about",
            "gdrive:",
            "--config",
            "/config/rclone/rclone.conf",
            "--json",
            "--contimeout",
            "10s",
            "--timeout",
            "20s",
            "--retries",
            "1",
            "--low-level-retries",
            "1",
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=55, check=False)
            if result.returncode:
                raise ValueError("EXISTING_GDRIVE_CHANNEL_REFRESH_FAILED")
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["sudo", "-n", "docker", "stop", "--time", "1", name],
                capture_output=True,
                timeout=5,
                check=False,
            )
            raise ValueError("EXISTING_GDRIVE_CHANNEL_REFRESH_TIMEOUT") from None
        parser = configparser.RawConfigParser()
        parser.read(path)
        self.token = json.loads(parser["gdrive"]["token"])["access_token"]
        self.expires = time.monotonic() + 900

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str | None = None,
        cap: int = 65536,
    ) -> bytes:
        if self.requests >= self.config["max_requests_per_run"]:
            raise ValueError("PDF_ARCHIVE_REQUEST_BUDGET")
        if time.monotonic() >= self.expires:
            self.refresh()
        headers = {"Authorization": "Bearer " + self.token}
        if content_type:
            headers["Content-Type"] = content_type
        self.requests += 1
        try:
            with self.opener.open(
                Request(url, data=body, headers=headers, method=method), timeout=20
            ) as response:
                raw: bytes = response.read(cap + 1)
            if len(raw) > cap:
                raise ValueError("PDF_ARCHIVE_RESPONSE_LIMIT")
            return raw
        except HTTPError as exc:
            # Do not copy OAuth headers or provider response bodies into logs.
            raise ValueError("PDF_ARCHIVE_HTTP_" + str(exc.code)) from None

    def children(self, parent: str, name: str) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
            raise ValueError("PDF_ARCHIVE_OBJECT_NAME_INVALID")
        query = {
            "q": f"'{parent}' in parents and trashed=false and name='{name}'",
            "fields": "nextPageToken,files(id,name,mimeType,size,sha256Checksum)",
            "pageSize": 100,
        }
        obj = json.loads(self.request(API + "?" + urlencode(query)))
        if obj.get("nextPageToken") or len(obj["files"]) > 1:
            raise ValueError("PDF_ARCHIVE_OBJECT_AMBIGUOUS")
        rows: list[dict[str, Any]] = obj["files"]
        return rows

    def folder(self, parent: str, name: str) -> str:
        key = (parent, name)
        if key in self.folders:
            return self.folders[key]
        rows = self.children(parent, name)
        if rows:
            if rows[0]["mimeType"] != "application/vnd.google-apps.folder":
                raise ValueError("PDF_ARCHIVE_FOLDER_CONFLICT")
            result = rows[0]["id"]
        else:
            raw = self.request(
                API + "?fields=id",
                method="POST",
                content_type="application/json",
                body=json.dumps(
                    {
                        "name": name,
                        "parents": [parent],
                        "mimeType": "application/vnd.google-apps.folder",
                    }
                ).encode(),
            )
            result = json.loads(raw)["id"]
        self.folders[key] = str(result)
        return str(result)

    def archive(self, path: Path, expected_sha: str) -> dict[str, Any]:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("PDF_STAGED_SHA_MISMATCH")
        parent = self.folder(self.folder(self.root, "sha256"), expected_sha[:2])
        name = expected_sha + ".pdf"
        rows = self.children(parent, name)
        uploaded = False
        start = instant()
        if not rows:
            boundary = "p10news" + uuid.uuid4().hex
            body = (
                ("--" + boundary + "\r\nContent-Type: application/json\r\n\r\n").encode()
                + json.dumps({"name": name, "parents": [parent]}).encode()
                + ("\r\n--" + boundary + "\r\nContent-Type: application/pdf\r\n\r\n").encode()
                + raw
                + ("\r\n--" + boundary + "--\r\n").encode()
            )
            obj = json.loads(
                self.request(
                    UPLOAD + "?uploadType=multipart&fields=id,size,sha256Checksum",
                    method="POST",
                    body=body,
                    content_type="multipart/related; boundary=" + boundary,
                )
            )
            uploaded = True
            self.uploaded_bytes += len(raw)
        else:
            obj = rows[0]
        uploaded_at = instant() if uploaded else None
        file_id = obj["id"]
        metadata = json.loads(self.request(API + "/" + file_id + "?fields=id,size,sha256Checksum"))
        if int(metadata.get("size", -1)) != len(raw):
            raise ValueError("PDF_ARCHIVE_SIZE_MISMATCH")
        # Full readback binds cloud bytes even when the provider omits SHA256.
        returned = self.request(API + "/" + file_id + "?alt=media", cap=len(raw))
        if len(returned) != len(raw) or hashlib.sha256(returned).hexdigest() != expected_sha:
            raise ValueError("PDF_ARCHIVE_READBACK_SHA_MISMATCH")
        return {
            "schema": "quanthus.p10.news-pdf-archive-receipt.v1",
            "folder_id": self.root,
            "file_id": file_id,
            "remote_path": "sha256/" + expected_sha[:2] + "/" + name,
            "sha256": expected_sha,
            "bytes": len(raw),
            "upload_started_at": start,
            "uploaded_at": uploaded_at,
            "existing_cloud_object_reused": not uploaded,
            "verified_at": instant(),
            "verification": "REMOTE_FULL_READBACK_SHA256_AND_SIZE",
        }
