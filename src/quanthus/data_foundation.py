"""Immutable metadata contracts for the local, point-in-time data foundation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from quanthus.contracts import SnapshotRef

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class DatasetManifest:
    """Provenance for one normalized or curated dataset revision."""

    dataset: str
    version: str
    source_revisions: Mapping[str, str]
    row_count: int
    partitions: tuple[str, ...]
    schema_hash: str
    content_checksums: Mapping[str, str]
    created_at: datetime
    knowledge_cutoff: datetime
    parent_manifest_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = {"dataset": self.dataset, "version": self.version, "schema_hash": self.schema_hash}
        blank = [name for name, value in required.items() if not value.strip()]
        if blank:
            raise ValueError(f"dataset manifest fields must not be empty: {', '.join(blank)}")
        if self.row_count < 0:
            raise ValueError("row_count must not be negative")
        if not self.partitions:
            raise ValueError("partitions must not be empty")
        if not self.content_checksums:
            raise ValueError("content_checksums must not be empty")
        if any(not name.strip() or not revision.strip() for name, revision in self.source_revisions.items()):
            raise ValueError("source_revisions must contain non-empty names and revisions")
        invalid_checksum = any(
            not path.strip() or not _SHA256.fullmatch(checksum)
            for path, checksum in self.content_checksums.items()
        )
        if invalid_checksum:
            raise ValueError("content_checksums must use sha256:<64 lowercase hex characters>")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.knowledge_cutoff, "knowledge_cutoff")

    @property
    def manifest_id(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "version": self.version,
            "source_revisions": dict(sorted(self.source_revisions.items())),
            "row_count": self.row_count,
            "partitions": list(self.partitions),
            "schema_hash": self.schema_hash,
            "content_checksums": dict(sorted(self.content_checksums.items())),
            "created_at": self.created_at.isoformat(),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "parent_manifest_ids": list(self.parent_manifest_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DatasetManifest:
        return cls(
            dataset=str(payload["dataset"]),
            version=str(payload["version"]),
            source_revisions={
                str(key): str(value) for key, value in dict(payload["source_revisions"]).items()
            },
            row_count=int(payload["row_count"]),
            partitions=tuple(str(item) for item in payload["partitions"]),
            schema_hash=str(payload["schema_hash"]),
            content_checksums={
                str(key): str(value) for key, value in dict(payload["content_checksums"]).items()
            },
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            knowledge_cutoff=datetime.fromisoformat(str(payload["knowledge_cutoff"])),
            parent_manifest_ids=tuple(str(item) for item in payload.get("parent_manifest_ids", [])),
        )


@dataclass(frozen=True)
class PublishedSnapshot:
    reference: SnapshotRef
    datasets: tuple[DatasetManifest, ...]

    def __post_init__(self) -> None:
        names = [manifest.dataset for manifest in self.datasets]
        if not names:
            raise ValueError("a snapshot must contain at least one dataset manifest")
        if len(names) != len(set(names)):
            raise ValueError("a snapshot cannot contain duplicate dataset names")
        late = [
            manifest.dataset
            for manifest in self.datasets
            if manifest.knowledge_cutoff > self.reference.knowledge_cutoff
        ]
        if late:
            raise ValueError(f"dataset manifests exceed snapshot knowledge_cutoff: {', '.join(sorted(late))}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": 1,
            "snapshot": {
                "market_date": self.reference.market_date.isoformat(),
                "knowledge_cutoff": self.reference.knowledge_cutoff.isoformat(),
                "source_revision": self.reference.source_revision,
                "snapshot_id": self.reference.snapshot_id,
            },
            "datasets": [
                manifest.to_dict() for manifest in sorted(self.datasets, key=lambda item: item.dataset)
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PublishedSnapshot:
        snapshot = payload["snapshot"]
        if not isinstance(snapshot, Mapping):
            raise ValueError("snapshot payload must be an object")
        return cls(
            reference=SnapshotRef(
                market_date=datetime.fromisoformat(str(snapshot["market_date"])).date(),
                knowledge_cutoff=datetime.fromisoformat(str(snapshot["knowledge_cutoff"])),
                source_revision=str(snapshot["source_revision"]),
                snapshot_id=str(snapshot["snapshot_id"]),
            ),
            datasets=tuple(DatasetManifest.from_dict(item) for item in payload["datasets"]),
        )


class SnapshotCatalog:
    """Publishes complete snapshot manifests with an atomic directory rename."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def publish(self, snapshot: PublishedSnapshot) -> Path:
        snapshot_id = snapshot.reference.snapshot_id
        if not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ValueError("snapshot_id contains unsupported characters")

        payload = snapshot.to_dict()
        target = self.root / snapshot_id
        if target.exists():
            existing = self.load(snapshot_id)
            if existing.to_dict() == payload:
                return target
            raise FileExistsError(f"snapshot_id already exists with different contents: {snapshot_id}")

        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=self.root))
        manifest_path = staging / "snapshot.json"
        try:
            with manifest_path.open("wb") as stream:
                stream.write(_canonical_json(payload))
                stream.flush()
                os.fsync(stream.fileno())
            staging.rename(target)
            self._sync_directory()
        except BaseException:
            # A failed publication is left in a clearly named staging directory for diagnosis.
            raise
        return target

    def load(self, snapshot_id: str) -> PublishedSnapshot:
        if not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ValueError("snapshot_id contains unsupported characters")
        manifest_path = self.root / snapshot_id / "snapshot.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(f"snapshot not found: {snapshot_id}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"snapshot manifest is not an object: {manifest_path}")
        published = PublishedSnapshot.from_dict(payload)
        if published.reference.snapshot_id != snapshot_id:
            raise ValueError(f"snapshot id does not match manifest path: {snapshot_id}")
        return published

    def _sync_directory(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
