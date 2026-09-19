from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quanthus.backtest.data_provider import (
    ReplayDataProvider,
    load_replay_data_provider_config,
)
from quanthus.backtest.historical_bundle import (
    REQUIRED_HISTORICAL_REPLAY_DATASETS,
    HistoricalReplayBundle,
)
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.policy import ConfigurationError

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "replay-data-provider.development.v1.json"
DIRECT_CONFIG = ROOT / "config" / "replay-data-provider.development.v2.json"


def test_default_provider_reuses_one_development_projection() -> None:
    config = load_replay_data_provider_config(CONFIG)

    assert config.provider_id == "development-shared-replay-v1"
    assert config.scope.partition == "development"
    assert config.scope.reader_role == "development_research"
    assert config.materialization["ordinary_replay_requires_dedicated_ads"] is False
    assert config.materialization["shared_projection_reused"] is True
    assert config.replay_identity["bundle_id"] == (
        "strategy-replay-development-1f1ea4d-v1"
    )
    assert config.dwd_identity["package_id"] == "dwd-base-202607-v2.1"
    assert config.dwd_identity["relationship_to_replay_source"] == (
        "authority-baseline-not-direct-replay-parent"
    )


def test_direct_provider_materializes_one_job_local_projection_from_dwd() -> None:
    config = load_replay_data_provider_config(DIRECT_CONFIG)

    assert config.provider_id == "development-direct-dwd-job-local-v1"
    assert config.mode == "direct-dwd-job-local"
    assert config.replay_identity == {}
    assert config.dwd_identity["relationship_to_replay_source"] == "direct-job-local-source"
    assert config.correction_identity == {
        "correction_id": "dwd-base-202607-v2.1-correction-20260813-001",
        "content_digest": (
            "sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da"
        ),
        "manifest_sha256": (
            "sha256:4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde"
        ),
    }
    assert config.materialization["job_local_projection"] is True
    assert config.history_start == date(2011, 1, 1)
    assert config.materialization["per_decision_publication_proven"] is False


def test_recent_history_partition_requires_explicit_opt_in(
    tmp_path: Path,
) -> None:
    payload = json.loads(DIRECT_CONFIG.read_text(encoding="utf-8"))
    payload["schema_version"] = "quanthus.replay-data-provider.v3"
    payload["scope"] = {
        "partition": "p3-strat-003-recent-history",
        "decision_start": "2025-01-01",
        "decision_end": "2026-09-17",
        "execution_end": "2026-09-18",
        "reader_role": "p3_strat_003_recent_history",
    }
    payload["materialization"]["history_start"] = "2024-10-01"
    candidate = tmp_path / "recent-provider.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="development only"):
        load_replay_data_provider_config(candidate)

    admitted = load_replay_data_provider_config(
        candidate,
        allowed_partitions=frozenset({"p3-strat-003-recent-history"}),
    )
    assert admitted.scope.partition == "p3-strat-003-recent-history"
    assert admitted.scope.reader_role == "p3_strat_003_recent_history"
    assert admitted.scope.decision_start == date(2025, 1, 1)
    assert admitted.scope.execution_end == date(2026, 9, 18)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("scope", "partition"), "arbitration", "development only"),
        (
            ("dwd_authority", "relationship_to_replay_source"),
            "direct-parent",
            "relationship does not match",
        ),
        (
            ("materialization", "ordinary_replay_requires_dedicated_ads"),
            True,
            "must not require a dedicated ADS",
        ),
    ],
)
def test_provider_rejects_scope_lineage_and_materialization_shortcuts(
    tmp_path: Path,
    path: tuple[str, str],
    value: object,
    message: str,
) -> None:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload[path[0]][path[1]] = value
    candidate = tmp_path / "provider.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_replay_data_provider_config(candidate)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sealed_provider_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    replay_root = tmp_path / "sealed-replay"
    replay_root.mkdir()
    datasets: list[DatasetManifest] = []
    cutoff = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
    for name in sorted(REQUIRED_HISTORICAL_REPLAY_DATASETS):
        relative = f"tables/{name}.parquet"
        path = replay_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + "\n").encode("ascii"))
        datasets.append(
            DatasetManifest(
                dataset=name,
                version="p3-strat-003-test-v1",
                source_revisions={"fixture": "synthetic-v1"},
                row_count=1,
                partitions=(relative,),
                schema_hash="sha256:" + "a" * 64,
                content_checksums={relative: _sha(path)},
                created_at=cutoff,
                knowledge_cutoff=cutoff,
            )
        )
    bundle = HistoricalReplayBundle(
        root=replay_root,
        snapshot=SnapshotRef(
            market_date=date(2026, 9, 18),
            knowledge_cutoff=cutoff,
            source_revision="sha256:" + "b" * 64,
            snapshot_id="p3-strat-003-test-snapshot",
        ),
        universe_policy_version="p3-strat-003-test-v1",
        datasets=tuple(datasets),
    )
    bundle_payload = {
        "schema_version": "quanthus.p3-strat-003-sealed-replay-bundle.v1",
        "work_item": "P3-STRAT-003-B",
        "request_digest": "sha256:" + "c" * 64,
        "partition": "p3-strat-003-recent-history",
        "snapshot": {
            "market_date": bundle.snapshot.market_date.isoformat(),
            "knowledge_cutoff": bundle.snapshot.knowledge_cutoff.isoformat(),
            "source_revision": bundle.snapshot.source_revision,
            "snapshot_id": bundle.snapshot.snapshot_id,
        },
        "universe_policy_version": bundle.universe_policy_version,
        "datasets": [item.to_dict() for item in bundle.datasets],
        "bundle_digest": bundle.digest,
        "semantic_digest": "sha256:" + "d" * 64,
    }
    bundle_manifest = replay_root / "bundle.json"
    bundle_manifest.write_text(
        json.dumps(bundle_payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    dwd_root = tmp_path / "base"
    dwd_root.mkdir()
    dwd_manifest = {
        "package_id": "dwd-base-202607-v2.1",
        "through_date": "2026-07-31",
        "content_digest": "sha256:" + "e" * 64,
    }
    (dwd_root / "manifest.json").write_text(
        json.dumps(dwd_manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    provider = {
        "schema_version": "quanthus.replay-data-provider.v3",
        "provider_id": "p3-strat-003-sealed-input-v1",
        "provider_version": "1.0.0",
        "mode": "sealed-historical-bundle",
        "research_only": True,
        "execution_authorized": False,
        "scope": {
            "partition": "p3-strat-003-recent-history",
            "decision_start": "2025-01-01",
            "decision_end": "2026-09-17",
            "execution_end": "2026-09-18",
            "reader_role": "p3_strat_003_recent_history",
        },
        "replay_source": {
            "root_env": "QUANTHUS_TEST_SEALED_REPLAY_ROOT",
            "root": str(replay_root),
            "bundle_manifest_sha256": _sha(bundle_manifest),
            "bundle_digest": bundle.digest,
        },
        "dwd_authority": {
            "root_env": "QUANTHUS_TEST_SEALED_DWD_ROOT",
            "root": str(dwd_root),
            "package_id": dwd_manifest["package_id"],
            "through_date": dwd_manifest["through_date"],
            "content_digest": dwd_manifest["content_digest"],
            "manifest_sha256": _sha(dwd_root / "manifest.json"),
            "relationship_to_replay_source": "sealed-bundle-derived-from-governed-dwd-chain",
        },
        "materialization": {
            "ordinary_replay_requires_dedicated_ads": False,
            "sealed_bundle_reused": True,
        },
    }
    provider_path = tmp_path / "provider.json"
    provider_path.write_text(json.dumps(provider), encoding="utf-8")
    return provider_path, replay_root, bundle.digest


def test_sealed_recent_history_provider_requires_explicit_partition_and_binds_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_path, replay_root, bundle_digest = _sealed_provider_fixture(tmp_path)

    with pytest.raises(ConfigurationError, match="development only"):
        load_replay_data_provider_config(provider_path)

    config = load_replay_data_provider_config(
        provider_path,
        allowed_partitions=frozenset({"p3-strat-003-recent-history"}),
    )
    assert config.mode == "sealed-historical-bundle"
    assert config.replay_root == replay_root
    assert config.replay_identity["bundle_digest"] == bundle_digest
    assert config.dwd_identity["relationship_to_replay_source"] == (
        "sealed-bundle-derived-from-governed-dwd-chain"
    )

    observed: dict[str, object] = {}

    class _Reader:
        def __init__(self, bundle: HistoricalReplayBundle) -> None:
            observed["bundle"] = bundle
            self.bundle = bundle

        def __enter__(self) -> _Reader:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("quanthus.backtest.data_provider.HistoricalReplayFrameReader", _Reader)
    session = ReplayDataProvider(config).open(
        scope_start=date(2025, 1, 1),
        scope_end=date(2026, 9, 17),
    )
    bundle = observed["bundle"]
    assert isinstance(bundle, HistoricalReplayBundle)
    assert bundle.digest == bundle_digest
    assert session.identity["replay_source"]["bundle_digest"] == bundle_digest
    assert session.identity["point_in_time"] is True


def test_sealed_recent_history_provider_rejects_partition_file_drift(
    tmp_path: Path,
) -> None:
    provider_path, replay_root, _bundle_digest = _sealed_provider_fixture(tmp_path)
    config = load_replay_data_provider_config(
        provider_path,
        allowed_partitions=frozenset({"p3-strat-003-recent-history"}),
    )
    (replay_root / "tables/daily_ohlcv_none.parquet").write_bytes(b"changed")

    with pytest.raises(ConfigurationError, match="partition checksum differs"):
        ReplayDataProvider(config).open(
            scope_start=date(2025, 1, 1),
            scope_end=date(2026, 9, 17),
        )


def test_sealed_recent_history_provider_rejects_bundle_manifest_drift(
    tmp_path: Path,
) -> None:
    provider_path, replay_root, _bundle_digest = _sealed_provider_fixture(tmp_path)
    config = load_replay_data_provider_config(
        provider_path,
        allowed_partitions=frozenset({"p3-strat-003-recent-history"}),
    )
    manifest = replay_root / "bundle.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="manifest checksum differs"):
        ReplayDataProvider(config).open(
            scope_start=date(2025, 1, 1),
            scope_end=date(2026, 9, 17),
        )
