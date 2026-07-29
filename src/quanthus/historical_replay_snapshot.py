"""Publish a complete immutable historical-replay snapshot from verified revisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.backtest.historical_bundle import (
    HistoricalReplayBundle,
    HistoricalReplayBundleError,
    load_historical_replay_bundle,
    validate_historical_replay_snapshot,
)
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import CorporateActionNormalizationPolicy
from quanthus.corporate_action_revision import (
    EXCEPTION_DATASET,
    EXCEPTION_FILE,
    MANIFEST_FILE,
    NORMALIZED_DATASET,
    NORMALIZED_FILE,
    check_corporate_action_revision,
)
from quanthus.data_foundation import PublishedSnapshot, SnapshotCatalog
from quanthus.snapshot_publish import (
    MaterializedSnapshot,
    SnapshotBuildError,
    SnapshotDatasetPlan,
    SnapshotPublicationPlan,
    publish_snapshot,
    verify_materialized_snapshot,
)

TRADING_CALENDAR_DATASET = "trading_calendar"


class HistoricalReplaySnapshotError(RuntimeError):
    """Raised when a complete replay snapshot cannot be assembled safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalReplaySnapshotError(f"cannot load JSON object: {path}") from exc
    if not isinstance(payload, dict):
        raise HistoricalReplaySnapshotError(f"JSON root must be an object: {path}")
    return payload


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalReplaySnapshotError(f"{field_name} must be timezone-aware")


def _load_base_snapshot(base_snapshot_dir: Path) -> PublishedSnapshot:
    root = base_snapshot_dir.resolve()
    try:
        snapshot = SnapshotCatalog(root.parent).load(root.name)
        verify_materialized_snapshot(root, snapshot)
    except (KeyError, OSError, SnapshotBuildError, ValueError) as exc:
        raise HistoricalReplaySnapshotError(
            "base snapshot is not a valid immutable snapshot"
        ) from exc
    return snapshot


@dataclass(frozen=True)
class HistoricalReplayPublication:
    materialized: MaterializedSnapshot
    bundle: HistoricalReplayBundle

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.materialized.snapshot.reference.snapshot_id,
            "location": str(self.materialized.location),
            "bundle_digest": self.bundle.digest,
            "dataset_count": len(self.bundle.datasets),
            "ready_for_frame_construction": self.bundle.ready_for_frame_construction,
            "execution_authorized": self.bundle.execution_authorized,
        }


def publish_historical_replay_snapshot(
    *,
    catalog: SnapshotCatalog,
    base_snapshot_dir: Path,
    corporate_action_revision_dir: Path,
    trading_calendar: Path,
    calendar_source_revision: str,
    calendar_knowledge_cutoff: datetime,
    snapshot_id: str,
    source_revision: str,
    knowledge_cutoff: datetime,
    policy: CorporateActionNormalizationPolicy,
) -> HistoricalReplayPublication:
    """Extend one verified P1 snapshot and gate the result before returning success."""

    _require_aware(calendar_knowledge_cutoff, "calendar_knowledge_cutoff")
    _require_aware(knowledge_cutoff, "knowledge_cutoff")
    if not snapshot_id.strip() or not source_revision.strip():
        raise HistoricalReplaySnapshotError("snapshot_id and source_revision must not be empty")
    if not calendar_source_revision.strip():
        raise HistoricalReplaySnapshotError("calendar_source_revision must not be empty")
    calendar = trading_calendar.resolve()
    if not calendar.is_file():
        raise HistoricalReplaySnapshotError(f"trading calendar does not exist: {calendar}")
    base_root = base_snapshot_dir.resolve()
    base = _load_base_snapshot(base_root)
    revision = check_corporate_action_revision(corporate_action_revision_dir)
    if (
        max(
            base.reference.knowledge_cutoff,
            revision.knowledge_cutoff,
            calendar_knowledge_cutoff,
        )
        > knowledge_cutoff
    ):
        raise HistoricalReplaySnapshotError("snapshot knowledge_cutoff precedes an input revision")
    revision_manifest = _load_object(revision.output / MANIFEST_FILE)
    try:
        revision_created_at = datetime.fromisoformat(str(revision_manifest["built_at"]))
    except (KeyError, ValueError) as exc:
        raise HistoricalReplaySnapshotError(
            "corporate-action revision has an invalid built_at"
        ) from exc
    _require_aware(revision_created_at, "corporate-action built_at")

    replacements = {NORMALIZED_DATASET, EXCEPTION_DATASET, TRADING_CALENDAR_DATASET}
    plans: list[SnapshotDatasetPlan] = []
    for manifest in base.datasets:
        if manifest.dataset in replacements:
            continue
        if len(manifest.partitions) != 1:
            raise HistoricalReplaySnapshotError(
                f"base dataset must have exactly one partition: {manifest.dataset}"
            )
        source_path = (base_root / manifest.partitions[0]).resolve()
        if not source_path.is_relative_to(base_root):
            raise HistoricalReplaySnapshotError(
                f"base dataset partition escapes the snapshot: {manifest.dataset}"
            )
        plans.append(
            SnapshotDatasetPlan(
                dataset=manifest.dataset,
                version=manifest.version,
                source_path=source_path,
                source_revisions=manifest.source_revisions,
                knowledge_cutoff=manifest.knowledge_cutoff,
                created_at=manifest.created_at,
                parent_manifest_ids=manifest.parent_manifest_ids,
            )
        )
    plans.extend(
        [
            SnapshotDatasetPlan(
                dataset=NORMALIZED_DATASET,
                version=revision.revision,
                source_path=revision.output / NORMALIZED_FILE,
                source_revisions=revision.snapshot_source_revisions[NORMALIZED_DATASET],
                knowledge_cutoff=revision.knowledge_cutoff,
                created_at=revision_created_at,
            ),
            SnapshotDatasetPlan(
                dataset=EXCEPTION_DATASET,
                version=revision.revision,
                source_path=revision.output / EXCEPTION_FILE,
                source_revisions=revision.snapshot_source_revisions[EXCEPTION_DATASET],
                knowledge_cutoff=revision.knowledge_cutoff,
                created_at=revision_created_at,
            ),
            SnapshotDatasetPlan(
                dataset=TRADING_CALENDAR_DATASET,
                version=calendar_source_revision,
                source_path=calendar,
                source_revisions={
                    "calendar_source_revision": calendar_source_revision,
                    "calendar_source_digest": _sha256(calendar),
                },
                knowledge_cutoff=calendar_knowledge_cutoff,
                created_at=calendar_knowledge_cutoff,
            ),
        ]
    )

    def validate(staging: Path, published: PublishedSnapshot) -> None:
        try:
            validate_historical_replay_snapshot(staging, published, policy)
        except HistoricalReplayBundleError as exc:
            raise HistoricalReplaySnapshotError(
                "historical replay bundle failed before atomic publication"
            ) from exc

    materialized = publish_snapshot(
        catalog,
        SnapshotPublicationPlan(
            reference=SnapshotRef(
                market_date=base.reference.market_date,
                knowledge_cutoff=knowledge_cutoff,
                source_revision=source_revision,
                snapshot_id=snapshot_id,
            ),
            universe_policy_version=base.universe_policy_version,
            datasets=tuple(plans),
        ),
        validate=validate,
    )
    try:
        bundle = load_historical_replay_bundle(materialized.location, policy)
    except HistoricalReplayBundleError as exc:
        raise HistoricalReplaySnapshotError(
            "published historical replay bundle failed its repeat verification"
        ) from exc
    return HistoricalReplayPublication(materialized=materialized, bundle=bundle)
