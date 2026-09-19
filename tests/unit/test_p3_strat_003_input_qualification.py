from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quanthus.backtest.historical_bundle import (
    REQUIRED_HISTORICAL_REPLAY_DATASETS,
    HistoricalReplayBundle,
)
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy.p3_strat_003_input_qualification import (
    BUNDLE_SCHEMA,
    PARTITION,
    REQUEST_DIGEST,
    _request,
    _scope,
    _verify_replay_bundle,
)
from quanthus.platform.event_worker import _structure_input, _structure_request_digest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p3-strat-003/input-qualification.v1.json"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(root: Path) -> HistoricalReplayBundle:
    root.mkdir()
    cutoff = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
    datasets: list[DatasetManifest] = []
    for name in sorted(REQUIRED_HISTORICAL_REPLAY_DATASETS):
        relative = f"tables/{name}.parquet"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + "\n").encode("ascii"))
        datasets.append(
            DatasetManifest(
                dataset=name,
                version="p3-strat-003-test-v1",
                source_revisions={"fixture": "synthetic-v1"},
                row_count=1,
                partitions=(relative,),
                schema_hash="sha256:" + "1" * 64,
                content_checksums={relative: _sha(path)},
                created_at=cutoff,
                knowledge_cutoff=cutoff,
            )
        )
    bundle = HistoricalReplayBundle(
        root=root,
        snapshot=SnapshotRef(
            market_date=date(2026, 9, 18),
            knowledge_cutoff=cutoff,
            source_revision="sha256:" + "2" * 64,
            snapshot_id="p3-strat-003-b-test",
        ),
        universe_policy_version="p3-strat-003-test-v1",
        datasets=tuple(datasets),
    )
    payload = {
        "schema_version": BUNDLE_SCHEMA,
        "work_item": "P3-STRAT-003-B",
        "request_digest": REQUEST_DIGEST,
        "partition": PARTITION,
        "snapshot": {
            "market_date": bundle.snapshot.market_date.isoformat(),
            "knowledge_cutoff": bundle.snapshot.knowledge_cutoff.isoformat(),
            "source_revision": bundle.snapshot.source_revision,
            "snapshot_id": bundle.snapshot.snapshot_id,
        },
        "universe_policy_version": bundle.universe_policy_version,
        "datasets": [item.to_dict() for item in bundle.datasets],
        "bundle_digest": bundle.digest,
    }
    (root / "bundle.json").write_text(
        json.dumps(
            {**payload, "semantic_digest": digest_value(payload)},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return bundle


def test_b_request_is_fixed_input_only_and_worker_admits_exact_config() -> None:
    raw = _request(CONFIG)
    assert raw["outputs"]["strategy_or_economic_results"] is False
    assert raw["outputs"]["target_outcome_labels"] is False
    assert raw["authority"]["2025_outcome_read_authorized"] is False
    assert raw["authority"]["2026_strategy_result_read_authorized"] is False
    assert raw["authority"]["p6_mutation_authorized"] is False

    start, end, execution, target, warmup = _scope(raw)
    assert (start, end, execution, target, warmup) == (
        date(2025, 1, 1),
        date(2026, 9, 17),
        date(2026, 9, 18),
        date(2026, 9, 18),
        60,
    )

    job_input = {
        "code_revision": "a" * 40,
        "run_config": "config/research/p3-strat-003/input-qualification.v1.json",
    }
    assert _structure_input(job_input) == job_input
    assert _structure_request_digest(job_input["run_config"]) == digest_value(raw)

    with pytest.raises(RuntimeError, match="run_config_not_governed"):
        _structure_input({**job_input, "run_config": "/tmp/not-governed.json"})


def test_b_replay_bundle_verifier_binds_every_dataset_file(tmp_path: Path) -> None:
    root = tmp_path / "replay"
    expected = _bundle(root)

    observed = _verify_replay_bundle(root, request_digest=REQUEST_DIGEST)
    assert observed.digest == expected.digest

    changed = root / "tables/daily_ohlcv_none.parquet"
    changed.write_bytes(b"changed")
    with pytest.raises(ExpertEvaluationError, match="dataset checksum differs"):
        _verify_replay_bundle(root, request_digest=REQUEST_DIGEST)


def test_event_worker_daily_mount_is_read_only_and_does_not_enable_p6_controller() -> None:
    compose = (ROOT / "ops/platform/compose.yaml").read_text(encoding="utf-8")
    event_block = compose.split("  event-worker:", 1)[1].split("  event-shard-worker:", 1)[0]

    assert "QUANTHUS_P6_DWD_DAILY_ROOT: /data/dwd-daily" in event_block
    assert "/home/ubuntu/data/quanthus/dwd-daily:/data/dwd-daily:ro" in event_block
    assert "QUANTHUS_P6_CONTROLLER_ENABLED" not in event_block
    assert "QUANTHUS_P6_ACTIVATION_MANDATE" not in event_block
    assert "QUANTHUS_P6_FORWARD_RUN_ROOT" not in event_block
