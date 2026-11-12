"""Private, revision-pinned Hugging Face evidence; credentials stay at the operator."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from maintenance_store import canonical, digest

MARKER = "<!-- maintenance.evidence-pointer.v1 -->"
SHA = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
MAX_BYTES = 2_000_000


class EvidenceError(ValueError):
    """A bounded operator-facing error without response bodies or credentials."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class PrivateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme != "https":
            raise EvidenceError("HF evidence redirect requires HTTPS")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if (
            redirected is not None
            and target.netloc != urllib.parse.urlparse(req.full_url).netloc
        ):
            redirected.remove_header("Authorization")
        return redirected


class HFStore:
    """One private dataset, project namespace and server-side credential."""

    def __init__(self, repository, project, environment, token, opener=None):
        if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
            raise EvidenceError("HF dataset repository is not configured")
        if not all(
            isinstance(x, str) and COMPONENT.fullmatch(x)
            for x in (project, environment)
        ):
            raise EvidenceError("HF project/environment namespace is invalid")
        if not isinstance(token, str) or not token.strip():
            raise EvidenceError("HF credential is not configured")
        self.repository, self.project, self.environment = (
            repository,
            project,
            environment,
        )
        self._token = token.strip()
        self._opener = opener or urllib.request.build_opener(PrivateRedirect())

    @classmethod
    def from_config(cls, config, environment):
        selected = config.get("evidence_store", {})
        if selected.get("kind") != "hf-private-dataset":
            raise EvidenceError("HF evidence backend is not selected")
        return cls(
            os.environ.get(selected.get("repository_env", "LOOP3_HF_REPO_ID"), ""),
            selected.get("project"),
            environment,
            os.environ.get(selected.get("token_env", "LOOP3_HF_TOKEN"), ""),
        )

    def _request(self, method, path, body=None, missing=False):
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/x-ndjson"
        req = urllib.request.Request(
            "https://huggingface.co" + path, data=body, headers=headers, method=method
        )
        try:
            with self._opener.open(req, timeout=30) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise EvidenceError("HF evidence response exceeds its size bound")
                return data
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise EvidenceError(
                "HF evidence HTTP status " + str(error.code),
                retryable=error.code in {409, 412, 429, 500, 502, 503, 504},
            ) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise EvidenceError(
                "HF evidence transport failed", retryable=True
            ) from None

    def _metadata(self):
        try:
            value = json.loads(self._request("GET", "/api/datasets/" + self.repository))
        except (TypeError, json.JSONDecodeError):
            raise EvidenceError("HF dataset metadata is invalid") from None
        if value.get("private") is not True:
            raise EvidenceError("HF evidence dataset must be private")
        if not REVISION.fullmatch(str(value.get("sha", ""))):
            raise EvidenceError("HF dataset must have an initialized revision")
        return value

    def _path(self, role, checksum):
        if role not in {
            "receipt",
            "delivery",
            "report",
            "metrics",
            "snapshot",
        } or not SHA.fullmatch(checksum):
            raise EvidenceError("HF evidence role or checksum is invalid")
        return "/".join((self.project, self.environment, role, checksum + ".json"))

    def _resolve(self, revision, path, missing=False):
        url = (
            "/datasets/"
            + self.repository
            + "/resolve/"
            + revision
            + "/"
            + urllib.parse.quote(path, safe="/")
        )
        return self._request("GET", url, missing=missing)

    def _reference(self, role, checksum, revision):
        if not REVISION.fullmatch(str(revision)):
            raise EvidenceError("HF returned an invalid commit revision")
        return {
            "provider": "huggingface",
            "repo_type": "dataset",
            "repository": self.repository,
            "revision": revision,
            "path": self._path(role, checksum),
            "sha256": checksum,
            "role": role,
        }

    def put(self, role, value):
        if not isinstance(value, dict):
            raise EvidenceError("HF evidence must be a JSON object")
        payload = canonical(value).encode()
        if len(payload) > MAX_BYTES:
            raise EvidenceError("HF evidence exceeds its size bound")
        if self._token.encode() in payload:
            raise EvidenceError("HF credential detected in evidence")
        checksum = hashlib.sha256(payload).hexdigest()
        path = self._path(role, checksum)
        # A lost commit response is recovered by finding the same content at HEAD.
        # parentCommit also serializes concurrent writers to different project folders.
        for attempt in range(4):
            try:
                base = self._metadata()["sha"]
                existing = self._resolve(base, path, missing=True)
                if existing is not None:
                    if existing != payload:
                        raise EvidenceError(
                            "HF immutable evidence path has different content"
                        )
                    return self._reference(role, checksum, base)
                items = [
                    {
                        "key": "header",
                        "value": {
                            "summary": "Store Loop3 " + self.project + " " + role,
                            "description": "Content-addressed maintenance evidence",
                            "parentCommit": base,
                        },
                    },
                    {
                        "key": "file",
                        "value": {
                            "path": path,
                            "encoding": "base64",
                            "content": base64.b64encode(payload).decode(),
                        },
                    },
                ]
                body = b"".join(json.dumps(item).encode() + b"\n" for item in items)
                result = json.loads(
                    self._request(
                        "POST",
                        "/api/datasets/" + self.repository + "/commit/main",
                        body,
                    )
                )
                reference = self._reference(role, checksum, result.get("commitOid"))
                if self.read(reference) != value:
                    raise EvidenceError("HF commit readback differs")
                return reference
            except EvidenceError as error:
                if not error.retryable or attempt == 3:
                    raise
            except (TypeError, json.JSONDecodeError):
                # Reconcile an uncertain response before another content-addressed commit.
                if attempt == 3:
                    raise EvidenceError(
                        "HF commit confirmation is unreadable"
                    ) from None
        raise EvidenceError("HF commit remains pending", retryable=True)

    def read(self, reference):
        if not isinstance(reference, dict):
            raise EvidenceError("HF evidence reference is invalid")
        if (
            reference.get("provider") != "huggingface"
            or reference.get("repo_type") != "dataset"
        ):
            raise EvidenceError("HF evidence provider does not match")
        if reference.get("repository") != self.repository:
            raise EvidenceError("HF evidence repository does not match")
        checksum = reference.get("sha256", "")
        revision = reference.get("revision", "")
        expected = self._path(reference.get("role"), checksum)
        if reference.get("path") != expected or not REVISION.fullmatch(str(revision)):
            raise EvidenceError("HF evidence namespace or revision does not match")
        self._metadata()
        data = self._resolve(revision, expected)
        if hashlib.sha256(data).hexdigest() != checksum:
            raise EvidenceError("HF evidence content checksum differs")
        try:
            value = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            raise EvidenceError("HF evidence JSON is invalid") from None
        if not isinstance(value, dict):
            raise EvidenceError("HF evidence must be a JSON object")
        return value


def pointer_for(config, store, receipt, role, value):
    backend = HFStore.from_config(config, receipt["environment_id"])
    row = store.db.execute(
        "SELECT pointer_json FROM evidence_references WHERE request_id=? AND role=?",
        (receipt["request_id"], role),
    ).fetchone()
    if row:
        pointer = json.loads(row[0])
        if digest(
            {k: v for k, v in pointer.items() if k != "pointer_digest"}
        ) != pointer.get("pointer_digest"):
            raise EvidenceError("Stored HF evidence pointer checksum differs")
        if backend.read(pointer["evidence"]) != value:
            raise EvidenceError("Stored HF evidence no longer matches the result")
        return pointer
    reference = backend.put(role, value)
    pointer = {
        "schema": "maintenance.evidence-pointer.v1",
        "artifact_schema": value["schema"],
        "role": role,
        **{
            k: receipt[k]
            for k in (
                "repository",
                "environment_id",
                "request_id",
                "batch_id",
                "challenge",
            )
        },
        "evidence": reference,
        "artifact_digest": value.get("receipt_digest")
        if role == "receipt"
        else value.get("evidence_digest"),
    }
    pointer["pointer_digest"] = digest(pointer)
    with store.atomic():
        store.db.execute(
            "INSERT INTO evidence_references VALUES (?,?,?)",
            (receipt["request_id"], role, canonical(pointer)),
        )
    return pointer


def read_pointer(config, pointer):
    unsigned = {k: v for k, v in pointer.items() if k != "pointer_digest"}
    if pointer.get("schema") != "maintenance.evidence-pointer.v1" or digest(
        unsigned
    ) != pointer.get("pointer_digest"):
        raise EvidenceError("HF evidence pointer checksum differs")
    environments = {x["id"] for x in config.get("environments", [])}
    if (
        pointer.get("repository") != config.get("repository")
        or pointer.get("environment_id") not in environments
    ):
        raise EvidenceError("HF evidence project registration does not match")
    value = HFStore.from_config(config, pointer.get("environment_id")).read(
        pointer["evidence"]
    )
    if any(
        value.get(k) != pointer.get(k)
        for k in ("repository", "environment_id", "request_id", "batch_id", "challenge")
    ):
        raise EvidenceError("HF evidence identity does not match its pointer")
    if value.get("schema") != pointer.get("artifact_schema"):
        raise EvidenceError("HF evidence schema does not match its pointer")
    field = {"receipt": "receipt_digest", "delivery": "evidence_digest"}.get(
        pointer.get("role")
    )
    if (
        field is None
        or value.get(field) != pointer.get("artifact_digest")
        or digest({k: v for k, v in value.items() if k != field}) != value[field]
    ):
        raise EvidenceError("HF artifact digest does not match its pointer")
    return value


def operator_environment(settings, inherited=None):
    """Load only HF/proxy settings from private operator files, without shell evaluation."""
    environment = dict(os.environ if inherited is None else inherited)
    repository = settings.get("repository", environment.get("LOOP3_HF_REPO_ID", ""))
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise EvidenceError("HF dataset repository is not configured")
    environment["LOOP3_HF_REPO_ID"] = repository
    for field, allowed, private in [
        (
            "credentials_file",
            {
                "HF_TOKEN",
                "LOOP3_HF_TOKEN",
                "HUGGING_FACE_HUB_TOKEN",
                "HUGGINGFACE_HUB_TOKEN",
            },
            True,
        ),
        (
            "proxy_file",
            {"HF_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "no_proxy"},
            False,
        ),
    ]:
        if not settings.get(field):
            continue
        path = Path(settings[field])
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > 65536:
            raise EvidenceError("HF operator file is invalid")
        if private and path.stat().st_mode & 0o077:
            raise EvidenceError("HF credential file must be private to its owner")
        values = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            line = line.removeprefix("export ")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() not in allowed:
                continue
            try:
                parts = shlex.split(value)
            except ValueError:
                raise EvidenceError("HF operator assignment is invalid") from None
            if len(parts) != 1:
                raise EvidenceError("HF operator assignment is invalid")
            values[key.strip()] = parts[0]
        if private:
            token = next(
                (
                    values[k]
                    for k in (
                        "LOOP3_HF_TOKEN",
                        "HF_TOKEN",
                        "HUGGING_FACE_HUB_TOKEN",
                        "HUGGINGFACE_HUB_TOKEN",
                    )
                    if values.get(k)
                ),
                "",
            )
            if not token:
                raise EvidenceError("HF credential is not configured")
            environment["LOOP3_HF_TOKEN"] = token
        else:
            environment.update({k: v for k, v in values.items() if k != "HF_PROXY"})
            if values.get("HF_PROXY"):
                environment.setdefault("HTTPS_PROXY", values["HF_PROXY"])
    if not environment.get("LOOP3_HF_TOKEN"):
        raise EvidenceError("HF credential is not configured")
    return environment
