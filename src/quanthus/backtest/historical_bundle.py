"""Fail-closed gate for one immutable continuous historical replay bundle."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import CorporateActionNormalizationPolicy
from quanthus.data_foundation import DatasetManifest, PublishedSnapshot
from quanthus.snapshot_publish import SnapshotBuildError, verify_materialized_snapshot

REQUIRED_HISTORICAL_REPLAY_DATASETS = frozenset(
    {
        "corporate_action_exceptions",
        "corporate_actions_normalized",
        "daily_ohlcv_none",
        "daily_universe",
        "price_limit_execution_daily",
        "security_code_validity",
        "trading_calendar",
    }
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class HistoricalReplayBundleError(ValueError):
    """Raised when a snapshot cannot authorize continuous frame construction."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class HistoricalReplayBundle:
    root: Path
    snapshot: SnapshotRef
    universe_policy_version: str
    datasets: tuple[DatasetManifest, ...]

    @property
    def ready_for_frame_construction(self) -> bool:
        return True

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return f"sha256:{hashlib.sha256(self.canonical_bytes()).hexdigest()}"

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def partition_paths(self, dataset: str) -> tuple[Path, ...]:
        matching = [item for item in self.datasets if item.dataset == dataset]
        if len(matching) != 1:
            raise KeyError(f"dataset is not bound exactly once: {dataset}")
        return tuple((self.root / partition).resolve() for partition in matching[0].partitions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": {
                "market_date": self.snapshot.market_date.isoformat(),
                "knowledge_cutoff": self.snapshot.knowledge_cutoff.isoformat(),
                "source_revision": self.snapshot.source_revision,
                "snapshot_id": self.snapshot.snapshot_id,
            },
            "universe_policy_version": self.universe_policy_version,
            "datasets": [
                {
                    "dataset": item.dataset,
                    "version": item.version,
                    "manifest_id": item.manifest_id,
                    "content_checksums": dict(sorted(item.content_checksums.items())),
                }
                for item in sorted(self.datasets, key=lambda value: value.dataset)
            ],
            "ready_for_frame_construction": self.ready_for_frame_construction,
            "execution_authorized": self.execution_authorized,
        }


def _load_published_snapshot(snapshot_dir: Path) -> PublishedSnapshot:
    root = snapshot_dir.resolve()
    manifest_path = root / "snapshot.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HistoricalReplayBundleError(
            f"historical replay snapshot does not exist: {manifest_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise HistoricalReplayBundleError(
            f"historical replay snapshot is invalid JSON: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise HistoricalReplayBundleError("historical replay snapshot must be an object")
    if raw.get("format_version") != 2:
        raise HistoricalReplayBundleError("historical replay snapshot format must be version 2")
    try:
        published = PublishedSnapshot.from_dict(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalReplayBundleError(
            "historical replay snapshot violates the published-snapshot contract"
        ) from exc
    if published.reference.snapshot_id != root.name:
        raise HistoricalReplayBundleError("snapshot id does not match its directory")
    return published


def _verify_bundle_integrity(root: Path, published: PublishedSnapshot) -> None:
    try:
        verify_materialized_snapshot(root, published)
    except SnapshotBuildError as exc:
        raise HistoricalReplayBundleError("historical replay snapshot integrity failed") from exc


def _validate_normalized_actions(
    manifest: DatasetManifest,
    exception_manifest: DatasetManifest,
    policy: CorporateActionNormalizationPolicy,
) -> None:
    revisions = manifest.source_revisions
    required_revisions = {
        "normalization_policy_version",
        "normalization_report_digest",
        "corporate_action_source_digest",
        "authority_source_digest",
        "exception_ledger_digest",
        "exception_ledger_version",
        "normalization_exception_count",
    }
    missing = required_revisions - set(revisions)
    if missing:
        raise HistoricalReplayBundleError(
            "normalized corporate actions are missing source revisions: "
            + ", ".join(sorted(missing))
        )
    if revisions["normalization_policy_version"] != policy.policy_version:
        raise HistoricalReplayBundleError("corporate-action normalization policy does not match")
    for field_name in (
        "normalization_report_digest",
        "corporate_action_source_digest",
        "authority_source_digest",
        "exception_ledger_digest",
    ):
        if not _SHA256.fullmatch(revisions[field_name]):
            raise HistoricalReplayBundleError(
                f"corporate-action {field_name} must be a sha256 digest"
            )
    if not revisions["normalization_exception_count"].isdigit():
        raise HistoricalReplayBundleError(
            "corporate-action normalization exception count must be nonnegative"
        )
    if manifest.row_count <= 0:
        raise HistoricalReplayBundleError("normalized corporate actions must not be empty")
    if policy.exception_ledger_version is None:
        raise HistoricalReplayBundleError(
            "corporate-action policy does not authorize a versioned exception ledger"
        )
    if revisions["exception_ledger_version"] != policy.exception_ledger_version:
        raise HistoricalReplayBundleError(
            "corporate-action exception-ledger version does not match"
        )

    exception_revisions = exception_manifest.source_revisions
    required_exception_revisions = {
        "normalization_policy_version",
        "normalization_report_digest",
        "corporate_action_source_digest",
        "authority_source_digest",
        "exception_ledger_digest",
        "exception_ledger_version",
        "replay_disposition",
    }
    missing_exception = required_exception_revisions - set(exception_revisions)
    if missing_exception:
        raise HistoricalReplayBundleError(
            "corporate-action exceptions are missing source revisions: "
            + ", ".join(sorted(missing_exception))
        )
    for field_name in required_exception_revisions - {"replay_disposition"}:
        if exception_revisions[field_name] != revisions[field_name]:
            raise HistoricalReplayBundleError(
                f"corporate-action exception lineage does not match: {field_name}"
            )
    if exception_revisions["replay_disposition"] != "security_fail_closed":
        raise HistoricalReplayBundleError(
            "corporate-action exceptions must fail closed by security"
        )
    exception_count = int(revisions["normalization_exception_count"])
    if exception_manifest.row_count != exception_count:
        raise HistoricalReplayBundleError("corporate-action exception row count does not reconcile")


def load_historical_replay_bundle(
    snapshot_dir: Path,
    policy: CorporateActionNormalizationPolicy,
) -> HistoricalReplayBundle:
    """Load and checksum one complete bundle before any historical frame is read."""

    root = snapshot_dir.resolve()
    published = _load_published_snapshot(root)
    return validate_historical_replay_snapshot(root, published, policy)


def validate_historical_replay_snapshot(
    root: Path,
    published: PublishedSnapshot,
    policy: CorporateActionNormalizationPolicy,
) -> HistoricalReplayBundle:
    """Gate materialized snapshot contents before or after atomic catalog publication."""

    root = root.resolve()
    _verify_bundle_integrity(root, published)
    bundle = HistoricalReplayBundle(
        root=root,
        snapshot=published.reference,
        universe_policy_version=published.universe_policy_version,
        datasets=tuple(published.datasets),
    )
    return validate_historical_replay_bundle_contract(bundle, policy)


def validate_historical_replay_bundle_contract(
    bundle: HistoricalReplayBundle,
    policy: CorporateActionNormalizationPolicy,
) -> HistoricalReplayBundle:
    """Validate replay semantics after the owning envelope has verified file integrity."""

    by_name = {manifest.dataset: manifest for manifest in bundle.datasets}
    missing = REQUIRED_HISTORICAL_REPLAY_DATASETS - set(by_name)
    if missing:
        raise HistoricalReplayBundleError(
            "historical replay bundle is missing datasets: " + ", ".join(sorted(missing))
        )
    _validate_normalized_actions(
        by_name["corporate_actions_normalized"],
        by_name["corporate_action_exceptions"],
        policy,
    )
    required_manifests = tuple(
        by_name[name] for name in sorted(REQUIRED_HISTORICAL_REPLAY_DATASETS)
    )
    return HistoricalReplayBundle(
        root=bundle.root,
        snapshot=bundle.snapshot,
        universe_policy_version=bundle.universe_policy_version,
        datasets=required_manifests,
    )
