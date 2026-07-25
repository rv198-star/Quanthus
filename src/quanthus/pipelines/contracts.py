"""Hermes-to-OCI immutable ODS release contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class OdsReleaseError(RuntimeError):
    """Raised when a Hermes ODS release is incomplete or has changed in transit."""


_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OdsReleaseError(f"{key} must be a non-empty string")
    return value


def _checksum(value: str, *, field: str) -> str:
    normalized = value if value.startswith("sha256:") else f"sha256:{value}"
    digest = normalized.removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise OdsReleaseError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _aware_datetime(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OdsReleaseError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OdsReleaseError(f"{field} must include a timezone offset")
    return parsed


@dataclass(frozen=True)
class OdsArtifact:
    name: str
    relative_path: PurePosixPath
    sha256: str
    size_bytes: int
    source_revision: str
    knowledge_cutoff: datetime

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OdsArtifact:
        name = _required_string(payload, "name")
        path_value = _required_string(payload, "relative_path")
        relative_path = PurePosixPath(path_value)
        if (
            relative_path.as_posix() == "."
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or path_value.endswith("/")
        ):
            raise OdsReleaseError(f"artifact {name} has an unsafe relative_path")
        size_bytes = payload.get("size_bytes")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise OdsReleaseError(f"artifact {name} size_bytes must be a non-negative integer")
        return cls(
            name=name,
            relative_path=relative_path,
            sha256=_checksum(_required_string(payload, "sha256"), field=f"{name}.sha256"),
            size_bytes=size_bytes,
            source_revision=_required_string(payload, "source_revision"),
            knowledge_cutoff=_aware_datetime(
                _required_string(payload, "knowledge_cutoff"),
                field=f"{name}.knowledge_cutoff",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "relative_path": self.relative_path.as_posix(),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_revision": self.source_revision,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
        }


@dataclass(frozen=True)
class OdsRelease:
    release_id: str
    schedule: str
    market_date: date
    created_at: datetime
    producer: str
    artifacts: tuple[OdsArtifact, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OdsRelease:
        if payload.get("schema_version") != 1:
            raise OdsReleaseError("unsupported ODS release schema_version")
        if payload.get("status") != "ready":
            raise OdsReleaseError("ODS release status must be ready")
        schedule = _required_string(payload, "schedule")
        if schedule not in {"daily", "weekly", "bootstrap"}:
            raise OdsReleaseError("schedule must be daily, weekly, or bootstrap")
        try:
            market_date = date.fromisoformat(_required_string(payload, "market_date"))
        except ValueError as exc:
            raise OdsReleaseError("market_date must use YYYY-MM-DD") from exc
        artifacts_value = payload.get("artifacts")
        if not isinstance(artifacts_value, list) or not artifacts_value:
            raise OdsReleaseError("artifacts must be a non-empty list")
        artifacts: list[OdsArtifact] = []
        for value in artifacts_value:
            if not isinstance(value, Mapping):
                raise OdsReleaseError("each artifact must be an object")
            artifacts.append(OdsArtifact.from_dict(value))
        names = [artifact.name for artifact in artifacts]
        paths = [artifact.relative_path for artifact in artifacts]
        if len(names) != len(set(names)):
            raise OdsReleaseError("artifact names must be unique")
        if len(paths) != len(set(paths)):
            raise OdsReleaseError("artifact relative paths must be unique")
        release_id = _required_string(payload, "release_id")
        if _RELEASE_ID.fullmatch(release_id) is None:
            raise OdsReleaseError("release_id contains unsupported characters")
        created_at = _aware_datetime(_required_string(payload, "created_at"), field="created_at")
        late_cutoffs = [
            artifact.name for artifact in artifacts if artifact.knowledge_cutoff > created_at
        ]
        if late_cutoffs:
            raise OdsReleaseError(
                "artifact knowledge_cutoff exceeds release created_at: "
                + ", ".join(sorted(late_cutoffs))
            )
        return cls(
            release_id=release_id,
            schedule=schedule,
            market_date=market_date,
            created_at=created_at,
            producer=_required_string(payload, "producer"),
            artifacts=tuple(artifacts),
        )

    @classmethod
    def load(cls, path: Path) -> OdsRelease:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OdsReleaseError(f"cannot load ODS release manifest: {path}") from exc
        if not isinstance(payload, Mapping):
            raise OdsReleaseError("ODS release manifest must be an object")
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "release_id": self.release_id,
            "schedule": self.schedule,
            "market_date": self.market_date.isoformat(),
            "created_at": self.created_at.isoformat(),
            "producer": self.producer,
            "status": "ready",
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }


@dataclass(frozen=True)
class OdsVerificationResult:
    release: OdsRelease
    release_dir: Path
    manifest_sha256: str
    verified_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release.release_id,
            "schedule": self.release.schedule,
            "market_date": self.release.market_date.isoformat(),
            "release_dir": str(self.release_dir),
            "manifest_sha256": self.manifest_sha256,
            "artifact_count": len(self.release.artifacts),
            "verified_bytes": self.verified_bytes,
        }


def verify_ods_release(release_dir: Path) -> OdsVerificationResult:
    """Verify READY, the manifest, every size, and every content checksum."""

    release_dir = release_dir.resolve()
    manifest = release_dir / "release.json"
    ready = release_dir / "READY"
    if not manifest.is_file() or not ready.is_file():
        raise OdsReleaseError(f"ODS release is missing release.json or READY: {release_dir}")
    manifest_sha256 = _sha256(manifest)
    try:
        ready_digest = ready.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise OdsReleaseError(f"cannot read ODS READY marker: {ready}") from exc
    if _checksum(ready_digest, field="READY") != manifest_sha256:
        raise OdsReleaseError("ODS READY marker does not match release.json")

    release = OdsRelease.load(manifest)
    verified_bytes = 0
    for artifact in release.artifacts:
        path = release_dir.joinpath(*artifact.relative_path.parts)
        if path.is_symlink():
            raise OdsReleaseError(f"artifact must not be a symbolic link: {artifact.relative_path}")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise OdsReleaseError(f"artifact is missing: {artifact.relative_path}") from exc
        if release_dir not in resolved.parents or not resolved.is_file():
            raise OdsReleaseError(f"artifact escapes its ODS release: {artifact.relative_path}")
        actual_size = resolved.stat().st_size
        if actual_size != artifact.size_bytes:
            raise OdsReleaseError(
                f"artifact size mismatch for {artifact.name}: "
                f"expected {artifact.size_bytes}, got {actual_size}"
            )
        actual_checksum = _sha256(resolved)
        if actual_checksum != artifact.sha256:
            raise OdsReleaseError(f"artifact checksum mismatch for {artifact.name}")
        verified_bytes += actual_size
    return OdsVerificationResult(
        release=release,
        release_dir=release_dir,
        manifest_sha256=manifest_sha256,
        verified_bytes=verified_bytes,
    )


def publish_ods_release(*, spec_path: Path, output: Path) -> OdsVerificationResult:
    """Snapshot Hermes artifacts and write READY last under an atomic directory rename.

    Source jobs must publish their own files by atomic replacement rather than mutate an
    existing inode. The publisher uses hard links on one filesystem and falls back to a copy
    across filesystems, then verifies that each inode stayed stable while it was checksummed.
    """

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsReleaseError(f"cannot load ODS release spec: {spec_path}") from exc
    if not isinstance(spec, Mapping) or spec.get("schema_version") != 1:
        raise OdsReleaseError("ODS release spec must be a schema_version 1 object")
    artifact_specs = spec.get("artifacts")
    if not isinstance(artifact_specs, list) or not artifact_specs:
        raise OdsReleaseError("ODS release spec artifacts must be a non-empty list")
    output = output.resolve()
    if output.exists():
        existing = verify_ods_release(output)
        requested_artifacts: list[tuple[str, str, str, str]] = []
        for index, value in enumerate(artifact_specs):
            if not isinstance(value, Mapping):
                raise OdsReleaseError(f"artifacts[{index}] must be an object")
            requested_artifacts.append(
                (
                    _required_string(value, "name"),
                    _required_string(value, "relative_path"),
                    _required_string(value, "source_revision"),
                    _aware_datetime(
                        _required_string(value, "knowledge_cutoff"),
                        field=f"artifacts[{index}].knowledge_cutoff",
                    ).isoformat(),
                )
            )
        requested_identity = (
            _required_string(spec, "release_id"),
            _required_string(spec, "schedule"),
            _required_string(spec, "market_date"),
            _required_string(spec, "producer"),
            sorted(requested_artifacts),
        )
        existing_identity = (
            existing.release.release_id,
            existing.release.schedule,
            existing.release.market_date.isoformat(),
            existing.release.producer,
            sorted(
                (
                    artifact.name,
                    artifact.relative_path.as_posix(),
                    artifact.source_revision,
                    artifact.knowledge_cutoff.isoformat(),
                )
                for artifact in existing.release.artifacts
            ),
        )
        if requested_identity != existing_identity:
            raise OdsReleaseError("existing ODS release does not match the requested spec")
        return existing

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        artifacts: list[dict[str, Any]] = []
        for index, value in enumerate(artifact_specs):
            if not isinstance(value, Mapping):
                raise OdsReleaseError(f"artifacts[{index}] must be an object")
            name = _required_string(value, "name")
            source = Path(_required_string(value, "source_path")).resolve(strict=True)
            if not source.is_file():
                raise OdsReleaseError(f"artifact source is not a regular file: {source}")
            path_value = _required_string(value, "relative_path")
            relative_path = PurePosixPath(path_value)
            if (
                relative_path.as_posix() == "."
                or relative_path.is_absolute()
                or ".." in relative_path.parts
                or path_value.endswith("/")
            ):
                raise OdsReleaseError(f"artifact {name} has an unsafe relative_path")
            destination = staging.joinpath(*relative_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            before = source.stat()
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
            checksum = _sha256(destination)
            after = destination.stat()
            stable_fields_before = (before.st_size, before.st_mtime_ns)
            stable_fields_after = (after.st_size, after.st_mtime_ns)
            if stable_fields_before != stable_fields_after:
                raise OdsReleaseError(f"artifact changed while publishing: {source}")
            artifacts.append(
                {
                    "name": name,
                    "relative_path": relative_path.as_posix(),
                    "sha256": checksum,
                    "size_bytes": after.st_size,
                    "source_revision": _required_string(value, "source_revision"),
                    "knowledge_cutoff": _required_string(value, "knowledge_cutoff"),
                }
            )

        payload = {
            "schema_version": 1,
            "release_id": _required_string(spec, "release_id"),
            "schedule": _required_string(spec, "schedule"),
            "market_date": _required_string(spec, "market_date"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "producer": _required_string(spec, "producer"),
            "status": "ready",
            "artifacts": artifacts,
        }
        OdsRelease.from_dict(payload)
        manifest = staging / "release.json"
        manifest.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "READY").write_text(f"{_sha256(manifest)}\n", encoding="ascii")
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_ods_release(output)
