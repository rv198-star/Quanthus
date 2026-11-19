"""Configured shared replay data access for strategy runners."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.backtest.historical_bundle import (
    REQUIRED_HISTORICAL_REPLAY_DATASETS,
    HistoricalReplayBundle,
)
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import (
    load_corporate_action_normalization_policy,
)
from quanthus.data_foundation import DatasetManifest
from quanthus.policy import ConfigurationError, find_project_root
from quanthus.strategy_replay_ads import (
    load_strategy_replay_ads_policy,
    load_strategy_replay_ads_reader,
)

REPLAY_DATA_PROVIDER_SCHEMAS = {
    "quanthus.replay-data-provider.v1",
    "quanthus.replay-data-provider.v2",
    "quanthus.replay-data-provider.v3",
}
DEFAULT_REPLAY_PROVIDER_PARTITIONS = frozenset({"development"})


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be a JSON object")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{label} must be a non-empty string")
    return value.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _date(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(_text(value, label))
    except ValueError as exc:
        raise ConfigurationError(f"{label} must be an ISO date") from exc


def _configured_root(value: Mapping[str, Any], label: str) -> Path:
    env_name = _text(value.get("root_env"), f"{label}.root_env")
    raw = os.environ.get(env_name) or _text(value.get("root"), f"{label}.root")
    return Path(raw).resolve()


@dataclass(frozen=True)
class ReplayProviderScope:
    partition: str
    decision_start: date
    decision_end: date
    execution_end: date
    reader_role: str

    def authorize(self, scope_start: date, scope_end: date) -> None:
        if scope_start > scope_end:
            raise ConfigurationError("replay scope start must not follow scope end")
        if scope_start < self.decision_start or scope_end > self.decision_end:
            raise ConfigurationError(
                "requested replay scope is outside the configured development provider"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "partition": self.partition,
            "decision_start": self.decision_start.isoformat(),
            "decision_end": self.decision_end.isoformat(),
            "execution_end": self.execution_end.isoformat(),
            "reader_role": self.reader_role,
        }


@dataclass(frozen=True)
class ReplayDataProviderConfig:
    path: Path
    provider_id: str
    provider_version: str
    scope: ReplayProviderScope
    replay_root: Path
    replay_policy: Path
    replay_identity: Mapping[str, str]
    dwd_root: Path
    dwd_identity: Mapping[str, str]
    materialization: Mapping[str, Any]
    history_start: date | None = None
    mode: str = "shared-replay-projection"
    correction_root: Path | None = None
    correction_identity: Mapping[str, str] | None = None
    benchmark_path: Path | None = None
    benchmark_sha256: str | None = None
    industry_path: Path | None = None
    industry_sha256: str | None = None

    @property
    def digest(self) -> str:
        payload = json.dumps(
            _load_object(self.path, "replay provider configuration"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def load_replay_data_provider_config(
    path: Path,
    *,
    allowed_partitions: frozenset[str] = DEFAULT_REPLAY_PROVIDER_PARTITIONS,
) -> ReplayDataProviderConfig:
    resolved = path.resolve()
    raw = _load_object(resolved, "replay provider configuration")
    if raw.get("schema_version") not in REPLAY_DATA_PROVIDER_SCHEMAS:
        raise ConfigurationError("unsupported replay data provider schema")
    mode = str(raw.get("mode", ""))
    if (
        mode not in {
            "shared-replay-projection",
            "direct-dwd-job-local",
            "sealed-historical-bundle",
        }
        or raw.get("research_only") is not True
        or raw.get("execution_authorized") is not False
    ):
        raise ConfigurationError("replay provider must remain a supported research-only mode")
    scope_raw = _mapping(raw.get("scope"), "scope")
    dwd = _mapping(raw.get("dwd_authority"), "dwd_authority")
    materialization = _mapping(raw.get("materialization"), "materialization")
    if materialization.get("ordinary_replay_requires_dedicated_ads") is not False:
        raise ConfigurationError("ordinary replay must not require a dedicated ADS")
    scope = ReplayProviderScope(
        partition=_text(scope_raw.get("partition"), "scope.partition"),
        decision_start=_date(scope_raw.get("decision_start"), "scope.decision_start"),
        decision_end=_date(scope_raw.get("decision_end"), "scope.decision_end"),
        execution_end=_date(scope_raw.get("execution_end"), "scope.execution_end"),
        reader_role=_text(scope_raw.get("reader_role"), "scope.reader_role"),
    )
    if not scope.decision_start <= scope.decision_end < scope.execution_end:
        raise ConfigurationError("replay provider scope dates are invalid")
    if scope.partition not in allowed_partitions:
        if allowed_partitions == DEFAULT_REPLAY_PROVIDER_PARTITIONS:
            raise ConfigurationError("the default replay provider may open development only")
        raise ConfigurationError(
            "replay provider partition is outside the explicitly admitted partition set"
        )
    project_root = find_project_root(resolved)
    replay_root = Path("/")
    policy = project_root / "config/strategy-replay-ads.v1.json"
    replay_identity: dict[str, str] = {}
    if mode == "shared-replay-projection":
        replay = _mapping(raw.get("replay_source"), "replay_source")
        if materialization.get("shared_projection_reused") is not True:
            raise ConfigurationError("shared replay mode must reuse its configured projection")
        policy = Path(_text(replay.get("policy"), "replay_source.policy"))
        if not policy.is_absolute():
            policy = project_root / policy
        replay_root = _configured_root(replay, "replay_source")
        replay_identity = {
            key: _text(replay.get(key), f"replay_source.{key}")
            for key in (
                "bundle_id",
                "bundle_content_digest",
                "manifest_sha256",
                "source_snapshot_id",
                "source_manifest_sha256",
            )
        }
    elif mode == "sealed-historical-bundle":
        replay = _mapping(raw.get("replay_source"), "replay_source")
        if materialization.get("sealed_bundle_reused") is not True:
            raise ConfigurationError("sealed replay mode must reuse one immutable bundle")
        replay_root = _configured_root(replay, "replay_source")
        replay_identity = {
            key: _text(replay.get(key), f"replay_source.{key}")
            for key in ("bundle_manifest_sha256", "bundle_digest")
        }
    dwd_identity = {
        key: _text(dwd.get(key), f"dwd_authority.{key}")
        for key in (
            "package_id",
            "through_date",
            "content_digest",
            "manifest_sha256",
            "relationship_to_replay_source",
        )
    }
    expected_relationship = {
        "shared-replay-projection": "authority-baseline-not-direct-replay-parent",
        "direct-dwd-job-local": "direct-job-local-source",
        "sealed-historical-bundle": "sealed-bundle-derived-from-governed-dwd-chain",
    }[mode]
    if dwd_identity["relationship_to_replay_source"] != expected_relationship:
        raise ConfigurationError("DWD relationship does not match the replay provider mode")
    correction_root = None
    correction_identity = None
    benchmark_path = None
    benchmark_sha256 = None
    industry_path = None
    industry_sha256 = None
    if mode == "direct-dwd-job-local":
        history_start = _date(
            materialization.get("history_start"),
            "materialization.history_start",
        )
        if history_start >= scope.decision_start:
            raise ConfigurationError(
                "direct DWD history_start must precede the development decision window"
            )
        correction = _mapping(raw.get("dwd_correction"), "dwd_correction")
        correction_root = _configured_root(correction, "dwd_correction")
        correction_identity = {
            key: _text(correction.get(key), f"dwd_correction.{key}")
            for key in ("correction_id", "content_digest", "manifest_sha256")
        }
        references = _mapping(raw.get("research_references"), "research_references")
        benchmark = _mapping(references.get("benchmark_curve"), "benchmark_curve")
        industry = _mapping(references.get("industry_mapping"), "industry_mapping")
        benchmark_path = Path(_text(benchmark.get("path"), "benchmark_curve.path"))
        industry_path = Path(_text(industry.get("path"), "industry_mapping.path"))
        benchmark_sha256 = _text(benchmark.get("sha256"), "benchmark_curve.sha256")
        industry_sha256 = _text(industry.get("sha256"), "industry_mapping.sha256")
    return ReplayDataProviderConfig(
        path=resolved,
        provider_id=_text(raw.get("provider_id"), "provider_id"),
        provider_version=_text(raw.get("provider_version"), "provider_version"),
        scope=scope,
        replay_root=replay_root,
        replay_policy=policy.resolve(),
        replay_identity=replay_identity,
        dwd_root=_configured_root(dwd, "dwd_authority"),
        dwd_identity=dwd_identity,
        materialization=dict(materialization),
        history_start=history_start if mode == "direct-dwd-job-local" else None,
        mode=mode,
        correction_root=correction_root,
        correction_identity=correction_identity,
        benchmark_path=benchmark_path,
        benchmark_sha256=benchmark_sha256,
        industry_path=industry_path,
        industry_sha256=industry_sha256,
    )


def _load_sealed_historical_bundle(
    config: ReplayDataProviderConfig,
) -> tuple[HistoricalReplayBundle, dict[str, str]]:
    manifest_path = config.replay_root / "bundle.json"
    manifest = _load_object(manifest_path, "sealed replay bundle manifest")
    observed_manifest_sha256 = _sha256(manifest_path)
    expected_manifest_sha256 = config.replay_identity.get("bundle_manifest_sha256")
    if observed_manifest_sha256 != expected_manifest_sha256:
        raise ConfigurationError("sealed replay bundle manifest checksum differs")
    if (
        manifest.get("schema_version") != "quanthus.p3-strat-003-sealed-replay-bundle.v1"
        or manifest.get("work_item") != "P3-STRAT-003-B"
        or manifest.get("partition") != config.scope.partition
    ):
        raise ConfigurationError("sealed replay bundle identity differs")
    snapshot = _mapping(manifest.get("snapshot"), "sealed replay bundle snapshot")
    try:
        snapshot_ref = SnapshotRef(
            market_date=date.fromisoformat(
                _text(snapshot.get("market_date"), "snapshot.market_date")
            ),
            knowledge_cutoff=datetime.fromisoformat(
                _text(snapshot.get("knowledge_cutoff"), "snapshot.knowledge_cutoff")
            ),
            source_revision=_text(
                snapshot.get("source_revision"), "snapshot.source_revision"
            ),
            snapshot_id=_text(snapshot.get("snapshot_id"), "snapshot.snapshot_id"),
        )
        datasets_raw = manifest.get("datasets")
        if not isinstance(datasets_raw, list):
            raise ValueError("datasets must be an array")
        datasets = tuple(DatasetManifest.from_dict(item) for item in datasets_raw)
    except (TypeError, KeyError, ValueError) as exc:
        raise ConfigurationError("sealed replay bundle manifest is invalid") from exc
    names = {item.dataset for item in datasets}
    if names != REQUIRED_HISTORICAL_REPLAY_DATASETS:
        raise ConfigurationError("sealed replay bundle dataset closure differs")
    for dataset in datasets:
        if set(dataset.partitions) != set(dataset.content_checksums):
            raise ConfigurationError(
                f"sealed replay bundle partition bindings differ: {dataset.dataset}"
            )
        for relative, expected in dataset.content_checksums.items():
            path = (config.replay_root / relative).resolve()
            if not path.is_relative_to(config.replay_root):
                raise ConfigurationError("sealed replay bundle partition escapes root")
            if not path.is_file() or _sha256(path) != expected:
                raise ConfigurationError(
                    f"sealed replay bundle partition checksum differs: {dataset.dataset}"
                )
    bundle = HistoricalReplayBundle(
        root=config.replay_root,
        snapshot=snapshot_ref,
        universe_policy_version=_text(
            manifest.get("universe_policy_version"), "universe_policy_version"
        ),
        datasets=datasets,
    )
    expected_bundle_digest = config.replay_identity.get("bundle_digest")
    if (
        manifest.get("bundle_digest") != bundle.digest
        or bundle.digest != expected_bundle_digest
    ):
        raise ConfigurationError("sealed replay bundle semantic digest differs")
    return bundle, {
        "bundle_manifest_sha256": observed_manifest_sha256,
        "bundle_digest": bundle.digest,
    }


class ReplayDataSession:
    def __init__(
        self,
        config: ReplayDataProviderConfig,
        reader: HistoricalReplayFrameReader,
        identity: Mapping[str, Any],
        temporary: tempfile.TemporaryDirectory[str] | None = None,
    ) -> None:
        self.config = config
        self.reader = reader
        self.identity = identity
        self.temporary = temporary

    def __enter__(self) -> ReplayDataSession:
        self.reader.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self.reader.__exit__(*args)
        if self.temporary is not None:
            self.temporary.cleanup()


class ReplayDataProvider:
    """Open one verified reader for all runners in a batch."""

    def __init__(self, config: ReplayDataProviderConfig) -> None:
        self.config = config

    def admitted_manifest(self) -> str:
        """Resolve the reader manifest the admitted digest must match, without
        opening any data tables. Raises for modes that cannot prove one.
        """
        if self.config.mode == "direct-dwd-job-local":
            from quanthus.backtest.direct_dwd_provider import (
                canonical_direct_dwd_manifest_sha256,
            )

            return canonical_direct_dwd_manifest_sha256(self.config)
        if self.config.mode == "shared-replay-projection":
            manifest = self.config.replay_identity.get("manifest_sha256")
            if not isinstance(manifest, str) or not manifest.startswith("sha256:"):
                raise ConfigurationError("shared replay manifest identity is invalid")
            return manifest
        raise ConfigurationError(
            "this replay provider mode exposes no admissible reader manifest"
        )

    def verify_admission(
        self, admitted_sha256: str, *, scope_start: date, scope_end: date
    ) -> None:
        """Validate the pinned identity and its physical source metadata before event reads."""
        self.config.scope.authorize(scope_start, scope_end)
        if self.admitted_manifest() != admitted_sha256:
            raise ConfigurationError(
                "replay provider manifest does not match the admitted data"
            )
        if self.config.mode == "direct-dwd-job-local":
            from quanthus.backtest.direct_dwd_provider import _direct_dwd_authority

            _direct_dwd_authority(self.config, None)
        else:
            self._shared_replay_authority()

    def open(
        self,
        *,
        scope_start: date,
        scope_end: date,
        progress: Callable[[str, Mapping[str, Any]], None] | None = None,
        admitted_reader_manifest_sha256: str | None = None,
    ) -> ReplayDataSession:
        self.config.scope.authorize(scope_start, scope_end)
        if admitted_reader_manifest_sha256 is not None and (
            self.admitted_manifest() != admitted_reader_manifest_sha256
        ):
            raise ConfigurationError(
                "replay provider manifest does not match the admitted data"
            )
        if self.config.mode == "direct-dwd-job-local":
            from quanthus.backtest.direct_dwd_provider import open_direct_dwd_session

            return open_direct_dwd_session(
                self.config,
                scope_start=scope_start,
                scope_end=scope_end,
                progress=progress,
            )
        if self.config.mode == "sealed-historical-bundle":
            bundle, actual_replay = _load_sealed_historical_bundle(self.config)
            dwd_manifest = _load_object(
                self.config.dwd_root / "manifest.json", "DWD authority manifest"
            )
            actual_dwd = {
                "package_id": dwd_manifest.get("package_id"),
                "through_date": dwd_manifest.get("through_date"),
                "content_digest": dwd_manifest.get("content_digest"),
                "manifest_sha256": _sha256(self.config.dwd_root / "manifest.json"),
                "relationship_to_replay_source": (
                    "sealed-bundle-derived-from-governed-dwd-chain"
                ),
            }
            if actual_dwd != dict(self.config.dwd_identity):
                raise ConfigurationError(
                    "DWD authority identity differs from sealed provider configuration"
                )
            reader = HistoricalReplayFrameReader(bundle)
            identity = {
                "provider_id": self.config.provider_id,
                "provider_version": self.config.provider_version,
                "provider_config_digest": self.config.digest,
                "scope": self.config.scope.to_dict(),
                "replay_source": actual_replay,
                "dwd_authority": actual_dwd,
                "point_in_time": True,
                "decision_at_close_execution_at_next_open": True,
                "dedicated_ads_required": False,
                "shared_session": True,
            }
            return ReplayDataSession(self.config, reader, identity)
        actual_replay, actual_dwd = self._shared_replay_authority()
        reader = load_strategy_replay_ads_reader(
            self.config.replay_root,
            ads_policy=load_strategy_replay_ads_policy(self.config.replay_policy),
            reader_role=self.config.scope.reader_role,
            corporate_action_policy=load_corporate_action_normalization_policy(),
        )
        identity = {
            "provider_id": self.config.provider_id,
            "provider_version": self.config.provider_version,
            "provider_config_digest": self.config.digest,
            "scope": self.config.scope.to_dict(),
            "replay_source": actual_replay,
            "dwd_authority": actual_dwd,
            "point_in_time": True,
            "decision_at_close_execution_at_next_open": True,
            "dedicated_ads_required": False,
            "shared_session": True,
        }
        return ReplayDataSession(self.config, reader, identity)

    def _shared_replay_authority(self) -> tuple[dict[str, Any], dict[str, Any]]:
        replay_manifest = _load_object(
            self.config.replay_root / "manifest.json", "shared replay manifest"
        )
        dwd_manifest = _load_object(
            self.config.dwd_root / "manifest.json", "DWD authority manifest"
        )
        actual_replay = {
            "bundle_id": replay_manifest.get("bundle_id"),
            "bundle_content_digest": replay_manifest.get("bundle_content_digest"),
            "manifest_sha256": _sha256(self.config.replay_root / "manifest.json"),
            "source_snapshot_id": _mapping(
                replay_manifest.get("source"), "shared replay source"
            ).get("snapshot_id"),
            "source_manifest_sha256": _mapping(
                replay_manifest.get("source"), "shared replay source"
            ).get("manifest_sha256"),
        }
        if actual_replay != dict(self.config.replay_identity):
            raise ConfigurationError("shared replay identity differs from provider configuration")
        actual_dwd = {
            "package_id": dwd_manifest.get("package_id"),
            "through_date": dwd_manifest.get("through_date"),
            "content_digest": dwd_manifest.get("content_digest"),
            "manifest_sha256": _sha256(self.config.dwd_root / "manifest.json"),
            "relationship_to_replay_source": "authority-baseline-not-direct-replay-parent",
        }
        if actual_dwd != dict(self.config.dwd_identity):
            raise ConfigurationError("DWD authority identity differs from provider configuration")
        return actual_replay, actual_dwd
