"""Materialize immutable P1 snapshot contents from an explicit local publication plan."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest, PublishedSnapshot, SnapshotCatalog


class SnapshotBuildError(RuntimeError):
    """Raised when a snapshot plan cannot be materialized safely."""


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotBuildError(f"{field_name} must be timezone-aware")


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotBuildError(f"{field_name} must be a non-empty string")
    return value


def _parse_datetime(value: Any, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_require_string(value, field_name))
    except ValueError as exc:
        raise SnapshotBuildError(f"{field_name} must be ISO-8601") from exc
    _require_aware(parsed, field_name)
    return parsed


def _parse_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(_require_string(value, field_name))
    except ValueError as exc:
        raise SnapshotBuildError(f"{field_name} must be an ISO-8601 date") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _parquet_metadata(path: Path) -> tuple[int, str]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise SnapshotBuildError("P1 dependencies are missing; run `make bootstrap-p1`") from exc
    try:
        metadata = pq.ParquetFile(path).metadata
        schema = pq.read_schema(path).remove_metadata()
    except Exception as exc:
        raise SnapshotBuildError(f"dataset is not readable Parquet: {path}") from exc
    if metadata is None:
        raise SnapshotBuildError(f"dataset has no Parquet metadata: {path}")
    schema_hash = hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()
    return metadata.num_rows, f"sha256:{schema_hash}"


@dataclass(frozen=True)
class SnapshotDatasetPlan:
    dataset: str
    version: str
    source_path: Path
    source_revisions: Mapping[str, str]
    knowledge_cutoff: datetime
    created_at: datetime
    parent_manifest_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.dataset.strip() or not self.version.strip():
            raise SnapshotBuildError("dataset and version must not be empty")
        if not self.source_path.is_file():
            raise SnapshotBuildError(f"dataset source path does not exist: {self.source_path}")
        if not self.source_revisions or any(
            not name.strip() or not revision.strip()
            for name, revision in self.source_revisions.items()
        ):
            raise SnapshotBuildError("source_revisions must contain non-empty names and revisions")
        _require_aware(self.knowledge_cutoff, "dataset knowledge_cutoff")
        _require_aware(self.created_at, "dataset created_at")


@dataclass(frozen=True)
class SnapshotPublicationPlan:
    reference: SnapshotRef
    universe_policy_version: str
    datasets: tuple[SnapshotDatasetPlan, ...]

    def __post_init__(self) -> None:
        if not self.universe_policy_version.strip():
            raise SnapshotBuildError("universe_policy_version must not be empty")
        names = [dataset.dataset for dataset in self.datasets]
        if not names:
            raise SnapshotBuildError("snapshot plan must contain at least one dataset")
        if len(names) != len(set(names)):
            raise SnapshotBuildError("snapshot plan cannot contain duplicate dataset names")


@dataclass(frozen=True)
class MaterializedSnapshot:
    location: Path
    snapshot: PublishedSnapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": str(self.location),
            "snapshot": self.snapshot.to_dict(),
        }


def _resolve_source_path(value: Any, plan_path: Path) -> Path:
    raw_path = Path(_require_string(value, "dataset path"))
    return (raw_path if raw_path.is_absolute() else plan_path.parent / raw_path).resolve()


def _source_revisions(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SnapshotBuildError("dataset source_revisions must be an object")
    return {
        _require_string(name, "source_revisions key"): _require_string(
            revision, "source_revisions value"
        )
        for name, revision in value.items()
    }


def load_snapshot_plan(path: Path) -> SnapshotPublicationPlan:
    """Load a deterministic, human-reviewable publication plan from JSON."""

    resolved_path = path.resolve()
    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SnapshotBuildError(f"snapshot plan does not exist: {resolved_path}") from exc
    except json.JSONDecodeError as exc:
        raise SnapshotBuildError(f"invalid JSON in snapshot plan: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0.0":
        raise SnapshotBuildError("snapshot plan schema_version must be 1.0.0")
    snapshot_raw = raw.get("snapshot")
    if not isinstance(snapshot_raw, dict):
        raise SnapshotBuildError("snapshot plan snapshot must be an object")
    reference = SnapshotRef(
        market_date=_parse_date(snapshot_raw.get("market_date"), "snapshot.market_date"),
        knowledge_cutoff=_parse_datetime(
            snapshot_raw.get("knowledge_cutoff"), "snapshot.knowledge_cutoff"
        ),
        source_revision=_require_string(
            snapshot_raw.get("source_revision"), "snapshot.source_revision"
        ),
        snapshot_id=_require_string(snapshot_raw.get("snapshot_id"), "snapshot.snapshot_id"),
    )
    datasets_raw = raw.get("datasets")
    if not isinstance(datasets_raw, list):
        raise SnapshotBuildError("snapshot plan datasets must be an array")
    datasets: list[SnapshotDatasetPlan] = []
    for index, dataset_raw in enumerate(datasets_raw):
        if not isinstance(dataset_raw, dict):
            raise SnapshotBuildError(f"datasets[{index}] must be an object")
        parent_ids = dataset_raw.get("parent_manifest_ids", [])
        if not isinstance(parent_ids, list):
            raise SnapshotBuildError(f"datasets[{index}].parent_manifest_ids must be an array")
        datasets.append(
            SnapshotDatasetPlan(
                dataset=_require_string(dataset_raw.get("dataset"), f"datasets[{index}].dataset"),
                version=_require_string(dataset_raw.get("version"), f"datasets[{index}].version"),
                source_path=_resolve_source_path(dataset_raw.get("path"), resolved_path),
                source_revisions=_source_revisions(dataset_raw.get("source_revisions")),
                knowledge_cutoff=_parse_datetime(
                    dataset_raw.get("knowledge_cutoff"), f"datasets[{index}].knowledge_cutoff"
                ),
                created_at=_parse_datetime(
                    dataset_raw.get("created_at"), f"datasets[{index}].created_at"
                ),
                parent_manifest_ids=tuple(
                    _require_string(item, f"datasets[{index}].parent_manifest_ids item")
                    for item in parent_ids
                ),
            )
        )
    return SnapshotPublicationPlan(
        reference=reference,
        universe_policy_version=_require_string(
            raw.get("universe_policy_version"), "universe_policy_version"
        ),
        datasets=tuple(datasets),
    )


def _build_manifest(dataset: SnapshotDatasetPlan) -> DatasetManifest:
    row_count, schema_hash = _parquet_metadata(dataset.source_path)
    destination = Path("datasets") / dataset.dataset / dataset.source_path.name
    return DatasetManifest(
        dataset=dataset.dataset,
        version=dataset.version,
        source_revisions=dataset.source_revisions,
        row_count=row_count,
        partitions=(destination.as_posix(),),
        schema_hash=schema_hash,
        content_checksums={destination.as_posix(): _sha256(dataset.source_path)},
        created_at=dataset.created_at,
        knowledge_cutoff=dataset.knowledge_cutoff,
        parent_manifest_ids=dataset.parent_manifest_ids,
    )


def verify_materialized_snapshot(snapshot_dir: Path, snapshot: PublishedSnapshot) -> None:
    """Verify every published partition against its manifest checksum."""

    root = snapshot_dir.resolve()
    for manifest in snapshot.datasets:
        if set(manifest.partitions) != set(manifest.content_checksums):
            raise SnapshotBuildError(
                f"manifest partitions and checksums differ: {manifest.dataset}"
            )
        for partition, expected_checksum in manifest.content_checksums.items():
            materialized_path = (root / partition).resolve()
            if not materialized_path.is_relative_to(root):
                raise SnapshotBuildError(
                    f"materialized partition escapes snapshot directory: {partition}"
                )
            if not materialized_path.is_file():
                raise SnapshotBuildError(f"materialized partition is missing: {materialized_path}")
            if _sha256(materialized_path) != expected_checksum:
                raise SnapshotBuildError(
                    f"materialized partition checksum mismatch: {materialized_path}"
                )


def publish_snapshot(
    catalog: SnapshotCatalog,
    plan: SnapshotPublicationPlan,
    *,
    validate: Callable[[Path, PublishedSnapshot], None] | None = None,
) -> MaterializedSnapshot:
    """Hard-link planned Parquet inputs and atomically publish their snapshot manifest.

    Hard links make snapshots immutable without silently consuming another full copy of
    the market-data corpus. A cross-filesystem plan fails explicitly rather than falling
    back to a potentially multi-gigabyte copy.
    """

    manifests = tuple(_build_manifest(dataset) for dataset in plan.datasets)
    snapshot = PublishedSnapshot(
        reference=plan.reference,
        datasets=manifests,
        universe_policy_version=plan.universe_policy_version,
    )

    def materialize(staging: Path) -> None:
        for dataset, manifest in zip(plan.datasets, manifests, strict=True):
            partition = next(iter(manifest.content_checksums))
            destination = staging / partition
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(dataset.source_path, destination)
            except OSError as exc:
                raise SnapshotBuildError(
                    "snapshot source and catalog must share a filesystem for hard-link "
                    f"materialization: {dataset.source_path}"
                ) from exc
            expected_checksum = next(iter(manifest.content_checksums.values()))
            if _sha256(destination) != expected_checksum:
                raise SnapshotBuildError(
                    f"source changed while materializing snapshot partition: {dataset.source_path}"
                )

    location = catalog.publish(snapshot, materialize=materialize, validate=validate)
    verify_materialized_snapshot(location, snapshot)
    return MaterializedSnapshot(location=location, snapshot=snapshot)
