from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus.backtest.historical_bundle import (
    REQUIRED_HISTORICAL_REPLAY_DATASETS,
    HistoricalReplayBundle,
)
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy import p3_strat_003_input_qualification as input_qualification
from quanthus.expert_strategy.p3_strat_003_input_qualification import (
    BUNDLE_SCHEMA,
    PARTITION,
    REQUEST_DIGEST,
    _fetch_bound_prior_package,
    _request,
    _scope,
    _verify_replay_bundle,
    _warmup_dates,
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



def _daily_package(root: Path, market_date: str, package_id: str) -> str:
    package = root / "packages" / market_date / package_id
    package.mkdir(parents=True)
    files: list[object] = []
    manifest = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_domain_increment",
        "package_id": package_id,
        "market_date": market_date,
        "files": files,
        "content_digest": "sha256:" + hashlib.sha256(
            json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
                "ascii"
            )
        ).hexdigest(),
        "row_counts": {},
    }
    manifest_path = package / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="ascii",
    )
    manifest_sha = _sha(manifest_path)
    (package / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return manifest_sha


def test_bound_prior_fetch_uses_exact_child_identity_and_job_local_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote"
    package_id = "dwd-daily-20260917-fixture"
    manifest_sha = _daily_package(remote, "2026-09-17", package_id)
    monkeypatch.setenv("QUANTHUS_CN01_DWD_HOST", "cn01.example")

    monkeypatch.setattr(
        input_qualification,
        "_warehouse_ssh_command",
        lambda: (
            "cn01.example",
            "22",
            "quanthus-dwd-ro",
            "/run/secrets/key",
            "/run/secrets/known-hosts",
            "ssh fixture",
        ),
    )
    monkeypatch.setattr(
        input_qualification,
        "_warehouse_remote_path",
        lambda relative: relative,
    )

    def fake_rsync(*, source: str, destination: Path, ssh: str, extra=()) -> None:
        del ssh, extra
        relative = source.split(":", 1)[1]
        source_path = remote / relative.rstrip("/")
        destination = Path(destination)
        if source_path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            for child in source_path.iterdir():
                if child.name == "READY":
                    continue
                if child.is_dir():
                    shutil.copytree(child, destination / child.name)
                else:
                    shutil.copy2(child, destination / child.name)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)

    monkeypatch.setattr(input_qualification, "_warehouse_rsync", fake_rsync)
    cache = tmp_path / "cache"

    package, receipt = _fetch_bound_prior_package(
        cache_root=cache,
        market_date=date(2026, 9, 17),
        package_id=package_id,
        manifest_sha256=manifest_sha,
    )

    assert package == cache / "packages/2026-09-17" / package_id
    assert receipt["disposition"] == "fetched_exact_child_binding"
    assert _sha(package / "manifest.json") == manifest_sha

    second, second_receipt = _fetch_bound_prior_package(
        cache_root=cache,
        market_date=date(2026, 9, 17),
        package_id=package_id,
        manifest_sha256=manifest_sha,
    )
    assert second == package
    assert second_receipt["disposition"] == "reused_job_local"

    with pytest.raises(ExpertEvaluationError, match="differs"):
        _fetch_bound_prior_package(
            cache_root=cache,
            market_date=date(2026, 9, 17),
            package_id=package_id,
            manifest_sha256="sha256:" + "0" * 64,
        )


def test_event_worker_bound_prior_transport_is_read_only_and_not_p6_authority() -> None:
    compose = (ROOT / "ops/platform/compose.yaml").read_text(encoding="utf-8")
    event_block = compose.split("  event-worker:", 1)[1].split("  event-shard-worker:", 1)[0]
    dockerfile = (ROOT / "ops/event/Dockerfile").read_text(encoding="utf-8")

    assert "openssh-client rsync" in dockerfile
    assert "QUANTHUS_CN01_DWD_USER: quanthus-dwd-ro" in event_block
    assert "QUANTHUS_CN01_DWD_KEY: /run/secrets/quanthus-cn01-dwd-ro" in event_block
    assert "/home/ubuntu/.ssh/cn01_candidate_ro:/run/secrets/quanthus-cn01-dwd-ro:ro" in event_block
    assert (
        "/home/ubuntu/.ssh/known_hosts_cn01_dual_lane:"
        "/run/secrets/quanthus-cn01-known-hosts:ro"
    ) in event_block
    assert "QUANTHUS_P6_CONTROLLER_ENABLED" not in event_block
    assert "QUANTHUS_P6_ACTIVATION_MANDATE" not in event_block



def test_warmup_dates_resolve_exact_sse_szse_shared_sessions(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    start = date(2024, 10, 1)
    dates = [start + timedelta(days=index) for index in range(70)]
    rows = {
        "venue_id": [],
        "trade_date": [],
        "knowledge_to": [],
        "is_open": [],
    }
    for market_date in dates:
        for venue_id in (
            "venue_bec59e31eb6bb28111a10d7e6d2c689f",
            "venue_8c0bd9088a0dc88f28e5e7fa8efdce87",
        ):
            rows["venue_id"].append(venue_id)
            rows["trade_date"].append(market_date)
            rows["knowledge_to"].append(None)
            rows["is_open"].append(True)

    target = (
        tmp_path
        / "payload/tradability_state/dwd_trading_calendar/trade_date_year=2024/part-00000.parquet"
    )
    target.parent.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "venue_id": pa.array(rows["venue_id"], type=pa.string()),
                "trade_date": pa.array(rows["trade_date"], type=pa.date32()),
                "knowledge_to": pa.array(rows["knowledge_to"], type=pa.timestamp("us", tz="UTC")),
                "is_open": pa.array(rows["is_open"], type=pa.bool_()),
            }
        ),
        target,
    )

    observed = _warmup_dates(tmp_path, date(2025, 1, 1), 60)

    assert len(observed) == 60
    assert observed == tuple(dates[-60:])



def test_bound_prior_fetch_preserves_multiline_transport_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.warehouse_sync import WarehouseSyncError

    monkeypatch.setenv("QUANTHUS_CN01_DWD_HOST", "cn01.example")
    monkeypatch.setattr(
        input_qualification,
        "_warehouse_ssh_command",
        lambda: (
            "cn01.example",
            "22",
            "quanthus-dwd-ro",
            "/run/secrets/key",
            "/run/secrets/known-hosts",
            "ssh fixture",
        ),
    )
    monkeypatch.setattr(
        input_qualification,
        "_warehouse_remote_path",
        lambda relative: relative,
    )

    def fail_rsync(**_kwargs: object) -> None:
        raise WarehouseSyncError(
            "rsync failed with exit code 23:\nremote package missing\nreceiver broken pipe"
        )

    monkeypatch.setattr(input_qualification, "_warehouse_rsync", fail_rsync)

    with pytest.raises(
        ExpertEvaluationError,
        match="remote package missing receiver broken pipe",
    ):
        _fetch_bound_prior_package(
            cache_root=tmp_path / "cache",
            market_date=date(2026, 9, 17),
            package_id="dwd-daily-20260917-fixture",
            manifest_sha256="sha256:" + "1" * 64,
        )
