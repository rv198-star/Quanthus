from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.backtest.historical_bundle import (
    REQUIRED_HISTORICAL_REPLAY_DATASETS,
    HistoricalReplayBundle,
)
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy import p3_strat_003_input_qualification as input_qualification
from quanthus.expert_strategy import p3_strat_003_semantic_equivalence as semantic_probe
from quanthus.expert_strategy.p3_strat_003_input_qualification import (
    BUNDLE_SCHEMA,
    PARTITION,
    REQUEST_DIGEST,
    _fetch_bound_prior_package,
    _missing_prior_reconstruction,
    _request,
    _scope,
    _semantic_equivalence_prerequisite,
    _verify_replay_bundle,
    _warmup_dates,
)
from quanthus.platform.event_worker import _structure_input, _structure_request_digest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p3-strat-003/input-qualification.v1.json"
CONFIG_V2 = ROOT / "config/research/p3-strat-003/input-qualification.v2.json"


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




def test_b_v2_request_binds_passed_semantic_probe_and_worker_admits_exact_config(
    tmp_path: Path,
) -> None:
    _, binding, identities = _semantic_probe_fixture(tmp_path / "probe")
    raw = json.loads(CONFIG_V2.read_text(encoding="utf-8"))
    raw["semantic_equivalence_prerequisite"] = binding

    prerequisite = _semantic_equivalence_prerequisite(raw)

    assert prerequisite is not None
    assert prerequisite.boundary_market_date == date(2026, 8, 27)
    assert prerequisite.original_package_id == identities["original_package_id"]
    assert prerequisite.original_manifest_sha256 == identities["original_manifest_sha256"]
    assert prerequisite.research_package_id == identities["research_package_id"]
    assert prerequisite.research_manifest_sha256 == identities["research_manifest_sha256"]
    assert prerequisite.consumer_payload_digest == identities["consumer_payload_digest"]

    request = _request(CONFIG_V2)
    assert request["schema_version"] == "quanthus.p3-strat-003-input-qualification.v2"
    assert request["authority"]["2025_outcome_read_authorized"] is False
    assert request["authority"]["2026_strategy_result_read_authorized"] is False
    assert request["authority"]["p6_mutation_authorized"] is False

    job_input = {
        "code_revision": "a" * 40,
        "run_config": "config/research/p3-strat-003/input-qualification.v2.json",
    }
    assert _structure_input(job_input) == job_input
    assert _structure_request_digest(job_input["run_config"]) == digest_value(request)


def test_semantic_prior_fetch_reuses_only_sealed_probe_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.warehouse_sync import WarehouseSyncError

    artifact_root, binding, identities = _semantic_probe_fixture(tmp_path / "probe")
    raw = json.loads(CONFIG_V2.read_text(encoding="utf-8"))
    raw["semantic_equivalence_prerequisite"] = binding
    prerequisite = _semantic_equivalence_prerequisite(raw)
    assert prerequisite is not None

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
        raise WarehouseSyncError("remote exact package unavailable")

    monkeypatch.setattr(input_qualification, "_warehouse_rsync", fail_rsync)
    package, receipt = _fetch_bound_prior_package(
        cache_root=tmp_path / "cache",
        market_date=date(2026, 8, 27),
        package_id=identities["original_package_id"],
        manifest_sha256=identities["original_manifest_sha256"],
        semantic_prerequisite=prerequisite,
    )

    assert package == artifact_root / "missing-boundary" / "package"
    assert receipt["disposition"] == "reused_sealed_semantic_equivalence_reconstruction"
    assert receipt["research_package_id"] == identities["research_package_id"]
    assert receipt["original_manifest_claimed_reproduced"] is False

    with pytest.raises(ExpertEvaluationError, match="remote exact package unavailable"):
        _fetch_bound_prior_package(
            cache_root=tmp_path / "other-cache",
            market_date=date(2026, 8, 28),
            package_id="dwd-daily-20260828-other",
            manifest_sha256="sha256:" + "d" * 64,
            semantic_prerequisite=prerequisite,
        )


def test_semantic_bridge_allows_exactly_the_probe_bound_original_child(
    tmp_path: Path,
) -> None:
    _, binding, identities = _semantic_probe_fixture(tmp_path / "probe")
    raw = json.loads(CONFIG_V2.read_text(encoding="utf-8"))
    raw["semantic_equivalence_prerequisite"] = binding
    prerequisite = _semantic_equivalence_prerequisite(raw)
    assert prerequisite is not None

    left = input_qualification.P6DailyChainPackage(
        root=prerequisite.research_package_root,
        package_id=prerequisite.research_package_id,
        market_date=date(2026, 8, 27),
        manifest_sha256=prerequisite.research_manifest_sha256,
        content_digest="sha256:" + "1" * 64,
        code_revision=prerequisite.code_revision,
        covered_after_date=date(2026, 8, 26),
        prior_package_id="anchor",
        prior_manifest_sha256="sha256:" + "2" * 64,
    )
    right = input_qualification.P6DailyChainPackage(
        root=tmp_path / "child",
        package_id="dwd-daily-20260828-child",
        market_date=date(2026, 8, 28),
        manifest_sha256="sha256:" + "3" * 64,
        content_digest="sha256:" + "4" * 64,
        code_revision="b" * 40,
        covered_after_date=date(2026, 8, 27),
        prior_package_id=prerequisite.original_package_id,
        prior_manifest_sha256=prerequisite.original_manifest_sha256,
    )

    assert input_qualification._semantic_bridge_allows(left, right, prerequisite)

    wrong = input_qualification.P6DailyChainPackage(
        root=right.root,
        package_id=right.package_id,
        market_date=right.market_date,
        manifest_sha256=right.manifest_sha256,
        content_digest=right.content_digest,
        code_revision=right.code_revision,
        covered_after_date=right.covered_after_date,
        prior_package_id=right.prior_package_id,
        prior_manifest_sha256="sha256:" + "5" * 64,
    )
    assert not input_qualification._semantic_bridge_allows(left, wrong, prerequisite)


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




def _consumer_daily_package(
    root: Path,
    *,
    market_date: date,
    package_id: str,
) -> tuple[Path, str, str]:
    package = root.resolve()
    package.mkdir(parents=True)
    files: list[dict[str, object]] = []
    row_counts: dict[str, int] = {}
    for index, table in enumerate(semantic_probe._CONSUMER_TABLES, start=1):
        path = (
            package
            / "payload"
            / "fixture"
            / table
            / f"market_date={market_date.isoformat()}"
            / "part-00000.parquet"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{table}:{market_date.isoformat()}\n".encode("ascii"))
        files.append(
            {
                "path": path.relative_to(package).as_posix(),
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
            }
        )
        row_counts[table] = index
    files.sort(key=lambda item: str(item["path"]))
    content_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            files,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_domain_increment",
        "package_id": package_id,
        "market_date": market_date.isoformat(),
        "files": files,
        "content_digest": content_digest,
        "row_counts": row_counts,
    }
    manifest_path = package / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="ascii",
    )
    manifest_sha = _sha(manifest_path)
    (package / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    consumer_digest, _ = semantic_probe._consumer_payload_identity(package)
    return package, manifest_sha, consumer_digest


def _semantic_probe_fixture(
    root: Path,
) -> tuple[Path, dict[str, object], dict[str, str]]:
    root.mkdir(parents=True)
    code_revision = "a" * 40
    request_digest = "sha256:" + "b" * 64
    original_package_id = "dwd-daily-20260827-aaaaaaaaaaaa-f8f8330"
    original_manifest_sha256 = "sha256:" + "c" * 64
    research_package_id = "dwd-daily-20260827-aaaaaaaaaaaa-aaaaaaa"
    research_root, research_manifest_sha256, consumer_payload_digest = (
        _consumer_daily_package(
            root / "missing-boundary" / "package",
            market_date=date(2026, 8, 27),
            package_id=research_package_id,
        )
    )
    del research_root
    summary = {
        "schema_version": semantic_probe.SUMMARY_SCHEMA,
        "work_item": "P3-STRAT-003-B",
        "request_digest": request_digest,
        "frozen_research_request_digest": REQUEST_DIGEST,
        "code_revision": code_revision,
        "consumer_tables": list(semantic_probe._CONSUMER_TABLES),
        "sentinels": [
            {
                "sentinel_id": "pre",
                "consumer_payload_exact_match": True,
                "selected_releases_exact_match": True,
            },
            {
                "sentinel_id": "post",
                "consumer_payload_exact_match": True,
                "selected_releases_exact_match": True,
            },
        ],
        "missing_boundary": {
            "market_date": "2026-08-27",
            "original_child_binding": {
                "package_id": original_package_id,
                "manifest_sha256": original_manifest_sha256,
            },
            "research_reconstruction": {
                "package_id": research_package_id,
                "manifest_sha256": research_manifest_sha256,
                "consumer_payload_digest": consumer_payload_digest,
            },
            "consumer_semantic_compatibility_precondition_passed": True,
            "original_manifest_claimed_reproduced": False,
        },
        "strategy_or_economic_result_count": 0,
        "target_outcome_label_count": 0,
        "candidate_scoring_count": 0,
        "p6_mutation": False,
        "daily_current_mutation": False,
        "disposition": "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE",
    }
    summary_path = root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="ascii",
    )
    manifest_payload = {
        "schema_version": semantic_probe.ARTIFACT_SCHEMA,
        "work_item": "P3-STRAT-003-B",
        "request_digest": request_digest,
        "frozen_research_request_digest": REQUEST_DIGEST,
        "code_revision": code_revision,
        "files": {
            "summary": {
                "path": "summary.json",
                "sha256": _sha(summary_path),
            }
        },
        "consumer_tables": list(semantic_probe._CONSUMER_TABLES),
        "sentinel_count": 2,
        "strategy_result_produced": False,
        "outcome_labels_produced": False,
        "candidate_scoring_performed": False,
        "p6_mutation": False,
        "daily_current_mutation": False,
        "disposition": "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE",
    }
    manifest = {
        **manifest_payload,
        "result_digest": digest_value(manifest_payload),
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="ascii",
    )
    manifest_sha256 = _sha(manifest_path)
    (root / "READY").write_text(manifest_sha256 + "\n", encoding="ascii")
    binding = {
        "artifact_ref": str(root),
        "code_revision": code_revision,
        "request_digest": request_digest,
        "manifest_sha256": manifest_sha256,
        "result_digest": manifest["result_digest"],
        "disposition": "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE",
        "boundary_market_date": "2026-08-27",
        "reuse_boundary_package": True,
        "allow_original_child_semantic_bridge": True,
        "original_manifest_claimed_reproduced": False,
    }
    identities = {
        "original_package_id": original_package_id,
        "original_manifest_sha256": original_manifest_sha256,
        "research_package_id": research_package_id,
        "research_manifest_sha256": research_manifest_sha256,
        "consumer_payload_digest": consumer_payload_digest,
    }
    return root, binding, identities


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


def test_missing_prior_reconstruction_is_bounded_and_event_worker_transport_isolated() -> None:
    raw = _request(CONFIG)
    reconstruction = _missing_prior_reconstruction(raw, project=ROOT)

    assert reconstruction.minimum_market_date == date(2026, 8, 1)
    assert reconstruction.through_market_date == date(2026, 9, 17)
    assert reconstruction.source_id == "cn01"
    assert reconstruction.revision_allowlist.name == "dwd-revision-allowlist.v1.json"
    assert reconstruction.maximum_prior_candidates == 8
    assert reconstruction.maximum_reconstructed_packages == 8
    assert reconstruction.maximum_calendar_scan_days == 14
    assert reconstruction.maximum_source_attempts_per_lane == 8

    compose = (ROOT / "ops/platform/compose.yaml").read_text(encoding="utf-8")
    event_block = compose.split("  event-worker:", 1)[1].split("  event-shard-worker:", 1)[0]
    replay_block = compose.split("  replay-worker:", 1)[1].split("  event-worker:", 1)[0]

    assert "QUANTHUS_CN01_ODS_RSYNC_KEY: /run/secrets/quanthus-cn01-ods-ro" in event_block
    assert "QUANTHUS_CN01_ODS_RSYNC_USER: root" in event_block
    assert (
        "QUANTHUS_CN01_ODS_RELEASE_ROOT: /root/work/astock/data/shadow/cn01-ods-v1/releases"
        in event_block
    )
    assert "/home/ubuntu/.ssh/cn01_ods:/run/secrets/quanthus-cn01-ods-ro:ro" in event_block
    assert "QUANTHUS_CN01_ODS_RSYNC_KEY" not in replay_block
    assert "quanthus-cn01-ods-ro" not in replay_block


def test_child_bound_package_resolves_pre_result_historical_builder_revision() -> None:
    reconstruction = _missing_prior_reconstruction(_request(CONFIG), project=ROOT)

    assert input_qualification._builder_revision_for_package(
        market_date=date(2026, 9, 11),
        package_id="dwd-daily-20260911-f536d90fb04d-f97f90c",
        reconstruction=reconstruction,
    ) == "f97f90c8e8ef6d14877e54408cb6eb3698813d23"
    assert input_qualification._builder_revision_for_package(
        market_date=date(2026, 9, 16),
        package_id="dwd-daily-20260916-996324770eea-c317fdb",
        reconstruction=reconstruction,
    ) == "c317fdb2ac81e34b5571b9e1abde524da1c5b05b"
    assert input_qualification._builder_revision_for_package(
        market_date=date(2026, 8, 27),
        package_id="dwd-daily-20260827-aaaaaaaaaaaa-f8f8330",
        reconstruction=reconstruction,
    ) == "f8f83309e11815f37212fa87163ceccdaae3e0b4"

    with pytest.raises(ExpertEvaluationError, match="revision_not_frozen"):
        input_qualification._builder_revision_for_package(
            market_date=date(2026, 9, 11),
            package_id="dwd-daily-20260911-f536d90fb04d-0000000",
            reconstruction=reconstruction,
        )


def test_reconstruction_selects_only_latest_ready_release_for_each_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconstruction = _missing_prior_reconstruction(_request(CONFIG), project=ROOT)
    source = SimpleNamespace(release_root="/remote/releases")
    monkeypatch.setattr(
        input_qualification,
        "_resolved_reconstruction_ods_source",
        lambda _reconstruction: source,
    )
    attempts = {
        "daily": ["market-old", "events-old", "market-new", "events-new", "junk"],
        "daily/1m": ["1m-old", "1m-new"],
        "daily/5m": ["5m-old", "5m-new"],
    }
    monkeypatch.setattr(
        input_qualification,
        "_ods_attempt_directories",
        lambda _source, *, schedule, market_date: attempts[schedule],
    )
    created = {
        "market-old": (1, "market_daily_core", "hermes-market-daily-core-old"),
        "market-new": (5, "market_daily_core", "hermes-market-daily-core-new"),
        "events-old": (2, "calendar_daily_events", "hermes-calendar-daily-events-old"),
        "events-new": (6, "calendar_daily_events", "hermes-calendar-daily-events-new"),
        "1m-old": (3, "intraday_1m", "hermes-intraday-archive-1m-old"),
        "1m-new": (7, "intraday_1m", "hermes-intraday-archive-1m-new"),
        "5m-old": (4, "intraday_5m", "hermes-intraday-archive-5m-old"),
        "5m-new": (8, "intraday_5m", "hermes-intraday-archive-5m-new"),
    }

    def probe(
        _source: object,
        *,
        schedule_path: str,
        market_date: date,
        attempt_id: str,
        metadata_root: Path,
    ) -> object:
        del market_date, metadata_root
        value = created.get(attempt_id)
        if value is None:
            return None
        order, lane, release_id = value
        return input_qualification._SelectedOdsRelease(
            lane=lane,
            schedule_path=schedule_path,
            attempt_id=attempt_id,
            release_id=release_id,
            created_at=datetime(2026, 9, 17, 10, order, tzinfo=timezone.utc),
            manifest_sha256="sha256:" + f"{order:x}" * 64,
        )

    monkeypatch.setattr(input_qualification, "_probe_reconstruction_release", probe)
    _, selected = input_qualification._select_reconstruction_ods_releases(
        reconstruction,
        market_date=date(2026, 9, 17),
        metadata_root=tmp_path / "metadata",
    )

    assert {item.lane: item.attempt_id for item in selected} == {
        "calendar_daily_events": "events-new",
        "intraday_1m": "1m-new",
        "intraday_5m": "5m-new",
        "market_daily_core": "market-new",
    }


def test_reconstruction_pull_copies_exactly_four_selected_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconstruction = _missing_prior_reconstruction(_request(CONFIG), project=ROOT)
    source = SimpleNamespace(
        release_root="/remote/releases",
        user="readonly",
        host="cn01.example",
    )
    selected = tuple(
        input_qualification._SelectedOdsRelease(
            lane=lane,
            schedule_path=schedule,
            attempt_id=f"{lane}-attempt",
            release_id=f"{prefix}selected",
            created_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
            manifest_sha256="sha256:" + str(index) * 64,
        )
        for index, (lane, (schedule, prefix)) in enumerate(
            sorted(input_qualification._RECONSTRUCTION_ODS_LANES.items()),
            start=1,
        )
    )
    monkeypatch.setattr(
        input_qualification,
        "_select_reconstruction_ods_releases",
        lambda *_args, **_kwargs: (source, selected),
    )
    transfers: list[str] = []

    def fake_rsync(
        _source: object,
        *arguments: str,
        timeout_seconds: int,
    ) -> SimpleNamespace:
        del timeout_seconds
        transfers.append(arguments[-2])
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(input_qualification, "_ods_rsync", fake_rsync)

    _, inventory = input_qualification._pull_reconstruction_ods(
        reconstruction,
        market_date=date(2026, 9, 17),
        output_root=tmp_path / "pull",
    )

    assert len(transfers) == 4
    assert len(inventory) == 4
    assert {item["lane"] for item in inventory} == set(
        input_qualification._RECONSTRUCTION_ODS_LANES
    )
    assert all(
        item["disposition"] == "selected_ready_release_copied_job_local"
        for item in inventory
    )


def test_suffix_reconstruction_publishes_only_after_exact_child_binding_and_keeps_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _request(CONFIG)
    reconstruction = _missing_prior_reconstruction(raw, project=ROOT)
    daily_root = tmp_path / "daily"
    current = daily_root / "CURRENT.json"
    current.parent.mkdir(parents=True)
    current.write_text('{"sentinel":"unchanged"}\n', encoding="ascii")
    anchor_root = tmp_path / "anchor"
    anchor_root.mkdir()
    (anchor_root / "manifest.json").write_text("{}\n", encoding="ascii")
    anchor = input_qualification._PriorCandidate(
        root=anchor_root,
        market_date=date(2026, 9, 9),
        package_id="dwd-daily-20260909-anchor-c317fdb",
        manifest_sha256="sha256:" + "1" * 64,
    )
    target_date = date(2026, 9, 16)
    target_id = "dwd-daily-20260916-996324770eea-c317fdb"
    target_sha = "sha256:" + "2" * 64
    target_root = tmp_path / "rebuilt-target"
    target_root.mkdir()
    (target_root / "manifest.json").write_text("{}\n", encoding="ascii")
    final = input_qualification._PriorCandidate(
        root=target_root,
        market_date=target_date,
        package_id=target_id,
        manifest_sha256=target_sha,
    )
    monkeypatch.setattr(
        input_qualification,
        "_verified_local_prior_candidates",
        lambda *_args, **_kwargs: (anchor,),
    )
    monkeypatch.setattr(
        input_qualification,
        "_build_reconstruction_suffix",
        lambda **_kwargs: (
            final,
            (final,),
            (
                {
                    "market_date": target_date.isoformat(),
                    "package_id": target_id,
                    "manifest_sha256": target_sha,
                },
            ),
        ),
    )

    cache = tmp_path / "cache"
    package, receipt = input_qualification._reconstruct_exact_prior_package(
        cache_root=cache,
        daily_root=daily_root,
        market_date=target_date,
        package_id=target_id,
        manifest_sha256=target_sha,
        reconstruction=reconstruction,
        base_root=tmp_path / "base",
        transport_failure="exact package absent",
    )

    assert package == cache / "packages/2026-09-16" / target_id
    assert package.is_dir()
    assert receipt["disposition"] == "reconstructed_exact_suffix_from_cn01_ods"
    assert receipt["anchor_market_date"] == "2026-09-09"
    assert receipt["reconstructed_package_count"] == 1
    assert current.read_text(encoding="ascii") == '{"sentinel":"unchanged"}\n'


def test_suffix_builder_chains_all_ready_market_dates_from_one_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _request(CONFIG)
    reconstruction = _missing_prior_reconstruction(raw, project=ROOT)
    dates = (
        date(2026, 9, 10),
        date(2026, 9, 11),
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
    )
    anchor_root = tmp_path / "anchor"
    anchor_root.mkdir()
    (anchor_root / "manifest.json").write_text("{}\n", encoding="ascii")
    anchor = input_qualification._PriorCandidate(
        root=anchor_root,
        market_date=date(2026, 9, 9),
        package_id="dwd-daily-20260909-anchor-c317fdb",
        manifest_sha256="sha256:" + "3" * 64,
    )
    monkeypatch.setattr(
        input_qualification,
        "_reconstruction_market_dates",
        lambda *_args, **_kwargs: dates,
    )
    release_root = tmp_path / "ods"
    release_root.mkdir()
    monkeypatch.setattr(
        input_qualification,
        "_pull_reconstruction_ods",
        lambda *_args, **kwargs: (
            release_root,
            (
                {
                    "market_date": kwargs["market_date"].isoformat(),
                    "schedule": "daily",
                    "attempt_id": "fixture",
                },
            ),
        ),
    )
    monkeypatch.setattr(
        input_qualification,
        "build_daily_ods_catalog",
        lambda **_kwargs: SimpleNamespace(catalog_digest="sha256:" + "4" * 64),
    )

    built_by_root: dict[Path, SimpleNamespace] = {}
    seen_prior_dates: list[str] = []

    def fake_build(**kwargs: object) -> SimpleNamespace:
        pointer = json.loads(Path(kwargs["prior_pointer"]).read_text(encoding="ascii"))
        seen_prior_dates.append(str(pointer["market_date"]))
        output = Path(kwargs["output"])
        output.mkdir(parents=True)
        market_date = dates[len(seen_prior_dates) - 1]
        package_id = (
            f"dwd-daily-{market_date.strftime('%Y%m%d')}-fixture-c317fdb"
        )
        manifest = output / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "cn01_daily_dwd_domain_increment",
                    "package_id": package_id,
                    "market_date": market_date.isoformat(),
                    "files": [],
                    "content_digest": "sha256:"
                    + hashlib.sha256(b"[]").hexdigest(),
                    "row_counts": {},
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="ascii",
        )
        manifest_sha = _sha(manifest)
        (output / "READY").write_text(manifest_sha + "\n", encoding="ascii")
        value = SimpleNamespace(
            package_id=package_id,
            market_date=market_date,
            manifest_sha256=manifest_sha,
        )
        built_by_root[output.resolve()] = value
        return value

    original_verify = input_qualification.verify_daily_domain_increment

    def fake_verify(path: Path) -> object:
        resolved = Path(path).resolve()
        return built_by_root.get(resolved) or original_verify(resolved)

    monkeypatch.setattr(input_qualification, "build_daily_domain_increment", fake_build)
    monkeypatch.setattr(input_qualification, "verify_daily_domain_increment", fake_verify)

    final, built, steps = input_qualification._build_reconstruction_suffix(
        cache_root=tmp_path / "cache",
        anchor=anchor,
        target_date=dates[-1],
        reconstruction=reconstruction,
        base_root=tmp_path / "base",
        attempt_root=tmp_path / "attempt",
        code_revision="c317fdb2ac81e34b5571b9e1abde524da1c5b05b",
    )

    assert final.market_date == dates[-1]
    assert [item.market_date for item in built] == list(dates)
    assert len(steps) == 5
    assert seen_prior_dates == [
        "2026-09-09",
        "2026-09-10",
        "2026-09-11",
        "2026-09-14",
        "2026-09-15",
    ]


def test_suffix_reconstruction_identity_mismatch_fails_without_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconstruction = _missing_prior_reconstruction(_request(CONFIG), project=ROOT)
    anchor_root = tmp_path / "anchor"
    anchor_root.mkdir()
    (anchor_root / "manifest.json").write_text("{}\n", encoding="ascii")
    anchor = input_qualification._PriorCandidate(
        root=anchor_root,
        market_date=date(2026, 9, 9),
        package_id="dwd-daily-20260909-anchor-c317fdb",
        manifest_sha256="sha256:" + "5" * 64,
    )
    wrong_root = tmp_path / "wrong"
    wrong_root.mkdir()
    wrong = input_qualification._PriorCandidate(
        root=wrong_root,
        market_date=date(2026, 9, 16),
        package_id="dwd-daily-20260916-different-c317fdb",
        manifest_sha256="sha256:" + "6" * 64,
    )
    monkeypatch.setattr(
        input_qualification,
        "_verified_local_prior_candidates",
        lambda *_args, **_kwargs: (anchor,),
    )
    monkeypatch.setattr(
        input_qualification,
        "_build_reconstruction_suffix",
        lambda **_kwargs: (wrong, (wrong,), ()),
    )

    with pytest.raises(ExpertEvaluationError, match="produced"):
        input_qualification._reconstruct_exact_prior_package(
            cache_root=tmp_path / "cache",
            daily_root=tmp_path / "daily",
            market_date=date(2026, 9, 16),
            package_id="dwd-daily-20260916-996324770eea-c317fdb",
            manifest_sha256="sha256:" + "7" * 64,
            reconstruction=reconstruction,
            base_root=tmp_path / "base",
            transport_failure="missing",
        )
    assert not (tmp_path / "cache/packages/2026-09-16").exists()



def _prior_transport_fixture(
    monkeypatch: pytest.MonkeyPatch, remote: Path
) -> list[str]:
    from quanthus.warehouse_sync import WarehouseSyncError

    calls: list[str] = []
    monkeypatch.setenv("QUANTHUS_CN01_DWD_HOST", "cn01.example")
    monkeypatch.setattr(
        input_qualification, "_warehouse_ssh_command",
        lambda: ("cn01.example", "22", "readonly", "key", "hosts", "ssh fixture"),
    )
    monkeypatch.setattr(input_qualification, "_warehouse_remote_path", lambda value: value)

    def transfer(*, source: str, destination: Path, **_kwargs: object) -> None:
        relative = source.split(":", 1)[1].rstrip("/")
        calls.append(relative)
        source_path = remote / relative
        if not source_path.exists():
            raise WarehouseSyncError("remote exact package unavailable")
        if source_path.is_dir():
            for child in source_path.iterdir():
                if child.name == "READY":
                    continue
                if child.is_dir():
                    shutil.copytree(child, destination / child.name)
                else:
                    shutil.copy2(child, destination / child.name)
        else:
            shutil.copy2(source_path, destination)

    monkeypatch.setattr(input_qualification, "_warehouse_rsync", transfer)
    return calls


@pytest.mark.parametrize("layout", ["full", "short", "both"])
def test_exact_original_prior_accepts_only_known_layouts(
    tmp_path: Path, layout: str,
) -> None:
    package_id = "dwd-daily-20260831-2cb5f15cb13f-f8f8330"
    daily = tmp_path / "daily"
    full = daily / "packages/2026-08-31" / package_id
    package, manifest_sha, _ = _consumer_daily_package(
        full, market_date=date(2026, 8, 31), package_id=package_id,
    )
    short = full.parent / "2cb5f15cb13f-f8f8330"
    if layout == "short":
        package.rename(short)
    elif layout == "both":
        shutil.copytree(package, short)
    other, _, _ = _consumer_daily_package(
        full.parent / "ffffffffffff-f8f8330", market_date=date(2026, 8, 31),
        package_id="dwd-daily-20260831-ffffffffffff-f8f8330",
    )
    (daily / "CURRENT.json").write_text(json.dumps({"manifest_path": str(other)}))
    observed = input_qualification._resolve_exact_local_prior_package(
        daily_root=daily, market_date=date(2026, 8, 31),
        package_id=package_id, manifest_sha256=manifest_sha,
    )
    assert observed == (short if layout == "short" else full)
    assert input_qualification._resolve_exact_local_prior_package(
        daily_root=daily, market_date=date(2026, 8, 31),
        package_id="dwd-daily-20260831-000000000000-f8f8330",
        manifest_sha256=manifest_sha,
    ) is None


@pytest.mark.parametrize(
    "damage", ["wrong_hash", "wrong_id", "wrong_date", "ready", "payload", "escape", "alias_drift"],
)
def test_exact_local_prior_damage_never_becomes_a_cache_miss(
    tmp_path: Path, damage: str,
) -> None:
    package_id = "dwd-daily-20260831-2cb5f15cb13f-f8f8330"
    daily = tmp_path / "daily"
    short = daily / "packages/2026-08-31/2cb5f15cb13f-f8f8330"
    _, manifest_sha, _ = _consumer_daily_package(
        short, market_date=date(2026, 8, 31), package_id=package_id,
    )
    market_date = date(2026, 8, 31)
    if damage == "wrong_hash":
        manifest_sha = "sha256:" + "0" * 64
    elif damage == "wrong_date":
        market_date = date(2026, 8, 30)
    elif damage == "ready":
        (short / "READY").write_text("sha256:" + "0" * 64)
    elif damage == "payload":
        next((short / "payload").rglob("*.parquet")).write_bytes(b"changed")
    elif damage == "escape":
        escaped = tmp_path / "escaped"
        short.rename(escaped)
        short.symlink_to(escaped, target_is_directory=True)
    elif damage == "alias_drift":
        full = short.parent / package_id
        shutil.copytree(short, full)
        raw = json.loads((full / "manifest.json").read_text())
        raw["extra"] = "different manifest"
        (full / "manifest.json").write_text(json.dumps(raw))
        (full / "READY").write_text(_sha(full / "manifest.json") + "\n")
    else:
        raw = json.loads((short / "manifest.json").read_text())
        raw["package_id"] = "dwd-daily-20260831-ffffffffffff-f8f8330"
        (short / "manifest.json").write_text(json.dumps(raw))
        manifest_sha = _sha(short / "manifest.json")
        (short / "READY").write_text(manifest_sha + "\n")
    with pytest.raises(ExpertEvaluationError):
        input_qualification._resolve_exact_local_prior_package(
            daily_root=daily, market_date=market_date,
            package_id=package_id, manifest_sha256=manifest_sha,
        )


@pytest.mark.parametrize("layout", ["short", "full"])
def test_exact_prior_remote_uses_producer_layout_then_legacy_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    package_id = "dwd-daily-20260831-2cb5f15cb13f-f8f8330"
    remote = tmp_path / "remote"
    name = "2cb5f15cb13f-f8f8330" if layout == "short" else package_id
    package = remote / "packages/2026-08-31" / name
    _, manifest_sha, _ = _consumer_daily_package(
        package, market_date=date(2026, 8, 31), package_id=package_id,
    )
    calls = _prior_transport_fixture(monkeypatch, remote)
    observed, receipt = _fetch_bound_prior_package(
        cache_root=tmp_path / "cache", market_date=date(2026, 8, 31),
        package_id=package_id, manifest_sha256=manifest_sha,
    )
    assert calls[0] == "packages/2026-08-31/2cb5f15cb13f-f8f8330"
    assert calls[-1] == f"packages/2026-08-31/{name}/READY"
    assert len(calls) == (2 if layout == "short" else 3)
    assert observed.name == package_id and _sha(observed / "manifest.json") == manifest_sha
    assert receipt["disposition"] == "fetched_exact_child_binding"


@pytest.mark.parametrize("damage", ["missing_ready", "bad_ready", "wrong_hash", "partial"])
def test_corrupt_remote_short_layout_does_not_fall_back_or_reconstruct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    from quanthus.warehouse_sync import WarehouseSyncError

    package_id = "dwd-daily-20260831-2cb5f15cb13f-f8f8330"
    remote = tmp_path / "remote"
    short = remote / "packages/2026-08-31/2cb5f15cb13f-f8f8330"
    _, manifest_sha, _ = _consumer_daily_package(
        short, market_date=date(2026, 8, 31), package_id=package_id,
    )
    shutil.copytree(short, short.parent / package_id)
    calls = _prior_transport_fixture(monkeypatch, remote)
    if damage == "missing_ready":
        (short / "READY").unlink()
    elif damage == "bad_ready":
        (short / "READY").write_text("sha256:" + "0" * 64)
    elif damage == "wrong_hash":
        manifest_sha = "sha256:" + "0" * 64
    else:
        def partial(*, source: str, destination: Path, **_kwargs: object) -> None:
            calls.append(source)
            (destination / "partial").write_text("incomplete")
            raise WarehouseSyncError("partial transfer")
        monkeypatch.setattr(input_qualification, "_warehouse_rsync", partial)

    def forbidden(**_kwargs: object) -> None:
        pytest.fail("damaged exact input must not trigger reconstruction")

    monkeypatch.setattr(input_qualification, "_reconstruct_exact_prior_package", forbidden)
    with pytest.raises(ExpertEvaluationError):
        _fetch_bound_prior_package(
            cache_root=tmp_path / "cache", market_date=date(2026, 8, 31),
            package_id=package_id, manifest_sha256=manifest_sha,
            daily_root=tmp_path / "daily", base_root=tmp_path / "base",
            reconstruction=_missing_prior_reconstruction(_request(CONFIG), project=ROOT),
        )
    assert all(package_id not in call for call in calls)
    assert not (tmp_path / "cache/packages/2026-08-31" / package_id).exists()


def _chain_daily_fixture(
    root: Path, *, market_date: date, package_id: str,
    prior: tuple[date, str, str] | None,
) -> tuple[Path, str, str]:
    package, _, consumer_digest = _consumer_daily_package(
        root, market_date=market_date, package_id=package_id,
    )
    manifest_path = package / "manifest.json"
    raw = json.loads(manifest_path.read_text())
    raw.update({
        "code_revision": "f8f8330" + "0" * 33,
        "covered_after_date": (prior[0] if prior else date(2026, 8, 25)).isoformat(),
        "base": {"package_id": "dwd-base-202607-v2.1",
                 "manifest_sha256": "sha256:" + "1" * 64,
                 "content_digest": "sha256:" + "2" * 64},
        "prior": None if prior is None else {
            "market_date": prior[0].isoformat(), "package_id": prior[1],
            "manifest_sha256": prior[2],
        },
    })
    manifest_path.write_text(json.dumps(raw, sort_keys=True) + "\n")
    manifest_sha = _sha(manifest_path)
    (package / "READY").write_text(manifest_sha + "\n")
    return package, manifest_sha, consumer_digest


def test_short_original_chain_reaches_only_the_sealed_aug27_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = tmp_path / "daily"
    anchor_id = "dwd-daily-20260826-111111111111-f8f8330"
    anchor, anchor_sha, _ = _chain_daily_fixture(
        daily / "packages/2026-08-26/111111111111-f8f8330",
        market_date=date(2026, 8, 26), package_id=anchor_id, prior=None,
    )
    original_id = "dwd-daily-20260827-aaaaaaaaaaaa-f8f8330"
    original_sha = "sha256:" + "c" * 64
    research_id = "dwd-daily-20260827-aaaaaaaaaaaa-aaaaaaa"
    research, research_sha, consumer_digest = _chain_daily_fixture(
        tmp_path / "probe/missing-boundary/package", market_date=date(2026, 8, 27),
        package_id=research_id, prior=(date(2026, 8, 26), anchor_id, anchor_sha),
    )
    prior = (date(2026, 8, 27), original_id, original_sha)
    expected_dates = [date(2026, 8, 26), date(2026, 8, 27)]
    for market_date, prefix in (
        (date(2026, 8, 28), "333333333333"),
        (date(2026, 8, 31), "2cb5f15cb13f"),
        (date(2026, 9, 1), "444444444444"),
    ):
        package_id = f"dwd-daily-{market_date:%Y%m%d}-{prefix}-f8f8330"
        target, target_sha, _ = _chain_daily_fixture(
            daily / "packages" / market_date.isoformat() / f"{prefix}-f8f8330",
            market_date=market_date, package_id=package_id, prior=prior,
        )
        prior = (market_date, package_id, target_sha)
        expected_dates.append(market_date)
    runtime = SimpleNamespace(
        daily_root=daily, historical_base_root=tmp_path / "base",
        historical_base_through_date=date(2026, 8, 25),
        historical_base_manifest_sha256="sha256:" + "1" * 64,
        historical_base_content_digest="sha256:" + "2" * 64,
    )
    prerequisite = input_qualification._SemanticEquivalencePrerequisite(
        artifact_root=tmp_path / "probe", code_revision="a" * 40,
        request_digest="sha256:" + "b" * 64, manifest_sha256="sha256:" + "d" * 64,
        result_digest="sha256:" + "e" * 64, boundary_market_date=date(2026, 8, 27),
        original_package_id=original_id, original_manifest_sha256=original_sha,
        research_package_root=research, research_package_id=research_id,
        research_manifest_sha256=research_sha, consumer_payload_digest=consumer_digest,
    )
    current = daily / "CURRENT.json"
    current.write_text('{"sentinel":"unchanged"}\n')
    before = {path: path.read_bytes() for path in daily.rglob("manifest.json")}
    before[research / "manifest.json"] = (research / "manifest.json").read_bytes()
    monkeypatch.setattr(input_qualification, "_verify_seed", lambda _config: None)
    calls = _prior_transport_fixture(monkeypatch, tmp_path / "absent-remote")

    def forbidden(**_kwargs: object) -> None:
        pytest.fail("exact short-name original input must not be reconstructed")

    monkeypatch.setattr(input_qualification, "_reconstruct_exact_prior_package", forbidden)
    chain, recovered = input_qualification._load_bound_daily_chain(
        config=runtime, target=SimpleNamespace(package_root=target, manifest_sha256=target_sha),
        cache_root=tmp_path / "cache",
        reconstruction=_missing_prior_reconstruction(_request(CONFIG_V2), project=ROOT),
        semantic_prerequisite=prerequisite,
    )
    assert [item.market_date for item in chain] == expected_dates
    assert chain[0].root == anchor and chain[1].root == research
    assert len(recovered) == 1
    assert recovered[0]["disposition"] == "reused_sealed_semantic_equivalence_reconstruction"
    assert calls == [
        "packages/2026-08-27/aaaaaaaaaaaa-f8f8330",
        f"packages/2026-08-27/{original_id}",
    ]
    assert current.read_text() == '{"sentinel":"unchanged"}\n'
    assert all(path.read_bytes() == content for path, content in before.items())



def _reseal_input_fixture(root: Path, manifest: dict[str, object]) -> None:
    payload = {key: value for key, value in manifest.items() if key != "result_digest"}
    (root / "manifest.json").write_text(
        json.dumps({**payload, "result_digest": digest_value(payload)}, sort_keys=True) + "\n",
    )
    (root / "READY").write_text(_sha(root / "manifest.json") + "\n")


def _qualification_artifact_fixture(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    root.mkdir()
    bundle = _bundle(root / "replay")
    candidate = root / "candidate-input/manifest.json"
    candidate.parent.mkdir()
    candidate.write_text("{}\n")
    candidate_digest = "sha256:" + "a" * 64
    monkeypatch.setattr(
        input_qualification.P3Strat003CandidateInput, "load",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest_digest=candidate_digest, replay_bundle_digest=bundle.digest,
        ),
    )
    summary = {
        "target_release": {"market_date": "2026-09-18"},
        "daily_chain": [
            {"market_date": "2026-08-27", "package_id": "research-27",
             "manifest_sha256": "sha256:" + "b" * 64, "covered_after_date": "2026-08-26",
             "prior_package_id": "anchor-26", "prior_manifest_sha256": "sha256:" + "c" * 64},
            {"market_date": "2026-08-28", "package_id": "original-28",
             "manifest_sha256": "sha256:" + "d" * 64, "covered_after_date": "2026-08-27",
             "prior_package_id": "original-27", "prior_manifest_sha256": "sha256:" + "e" * 64},
        ],
        "prior_chain_recovery": {"mutated_local_daily_root": False},
        "replay_audit": {"ready_for_strategy_replay": True},
        "candidate_scan": {"candidate_events": 0},
        "deferred_daily_artifact_contract": {"daily_package_count": 2},
        "semantic_equivalence_prerequisite": {
            "result_digest": "sha256:" + "f" * 64,
            "disposition": "PASS_CONSUMER_SEMANTIC_EQUIVALENCE_PROBE",
            "original_manifest_claimed_reproduced": False,
            "original_child_binding": {
                "package_id": "original-27", "manifest_sha256": "sha256:" + "e" * 64,
            },
            "research_reconstruction": {
                "package_id": "research-27", "manifest_sha256": "sha256:" + "b" * 64,
            },
        },
        "strategy_or_economic_result_count": 0, "outcome_label_count": 0, "p6_mutation": False,
        "bundle_digest": bundle.digest, "candidate_manifest_digest": candidate_digest,
    }
    (root / "summary.json").write_text(json.dumps(summary) + "\n")
    files = {"summary": "summary.json", "replay_bundle": "replay/bundle.json",
             "candidate_manifest": "candidate-input/manifest.json"}
    manifest: dict[str, object] = {
        "schema_version": input_qualification.ARTIFACT_SCHEMA, "work_item": "P3-STRAT-003-B",
        "code_revision": "a" * 40, "request_digest": "sha256:" + "0" * 64,
        "partition": PARTITION, "strategy_result_produced": False,
        "outcome_labels_produced": False, "p6_mutation": False,
        "qualification_evidence": input_qualification._qualification_evidence(summary),
        "files": {name: {"path": path, "sha256": _sha(root / path)}
                  for name, path in files.items()},
        "bundle_digest": bundle.digest, "candidate_manifest_digest": candidate_digest,
        "semantic_equivalence_result_digest": "sha256:" + "f" * 64,
        "row_count": 0, "candidate_event_count": 0,
    }
    _reseal_input_fixture(root, manifest)
    return manifest


@pytest.mark.parametrize("changed", [None, "target_release", "daily_chain", "bridge"])
def test_qualification_manifest_exposes_one_bridge_and_rejects_resealed_projection_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str | None,
) -> None:
    root = tmp_path / "artifact"
    manifest = _qualification_artifact_fixture(root, monkeypatch)
    verify = input_qualification.verify_p3_strat_003_input_artifact
    result = verify(root, code_revision="a" * 40, request_digest="sha256:" + "0" * 64)
    assert result["execution_authorized"] is False
    evidence = manifest["qualification_evidence"]
    assert isinstance(evidence, dict)
    chain = evidence["daily_chain"]
    bridge = evidence["semantic_equivalence_prerequisite"]
    assert chain[0]["manifest_sha256"] == bridge["research_reconstruction"]["manifest_sha256"]
    assert chain[1]["prior_manifest_sha256"] == bridge["original_child_binding"]["manifest_sha256"]
    assert chain[0]["manifest_sha256"] != chain[1]["prior_manifest_sha256"]
    if changed is None:
        return
    key = "semantic_equivalence_prerequisite" if changed == "bridge" else changed
    evidence[key] = {"unbound": "changed"}
    _reseal_input_fixture(root, manifest)
    with pytest.raises(ExpertEvaluationError, match="projection differs"):
        verify(root, code_revision="a" * 40, request_digest="sha256:" + "0" * 64)


def test_qualification_manifest_size_limit_rejects_oversized_complete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "artifact"
    manifest = _qualification_artifact_fixture(root, monkeypatch)
    manifest["oversized"] = "x" * input_qualification._MAX_QUALIFICATION_MANIFEST_BYTES
    _reseal_input_fixture(root, manifest)
    with pytest.raises(ExpertEvaluationError, match="512 KiB"):
        input_qualification.verify_p3_strat_003_input_artifact(
            root, code_revision="a" * 40, request_digest="sha256:" + "0" * 64,
        )
