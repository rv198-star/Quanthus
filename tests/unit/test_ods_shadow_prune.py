from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from quanthus.pipelines.contracts import verify_ods_release
from quanthus.pipelines.ods_shadow_prune import (
    OdsShadowPruneError,
    prune_ods_shadow,
)

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="ascii",
    )


def _shadow(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "shadow" / "node-ods-v1"
    for name in ("attempts", "releases", "catalogs", "locks"):
        (root / name).mkdir(parents=True)
    return root


def _attempt_root(
    shadow: Path,
    *,
    lane: str,
    market_date: str,
    attempt_id: str,
    stream: str | None,
) -> Path:
    root = shadow / "attempts" / lane
    if stream:
        root /= stream
    return root / market_date / attempt_id


def _write_release(
    shadow: Path,
    *,
    lane: str,
    market_date: str,
    attempt_id: str,
    landing_payload: Path,
    stream: str | None = None,
) -> Path:
    schedule = "weekly" if lane == "weekly-corrections" else "daily"
    relative = Path(schedule)
    if stream:
        relative /= stream
    release = shadow / "releases" / relative / market_date / attempt_id
    artifacts = release / "artifacts"
    artifacts.mkdir(parents=True)
    artifact = artifacts / "payload.parquet"
    os.link(landing_payload, artifact)
    release_id = f"hermes-{lane}"
    if stream:
        release_id += f"-{stream}"
    release_id += f"-{market_date.replace('-', '')}-{attempt_id[-8:]}-v2"
    manifest = {
        "schema_version": 1,
        "release_id": release_id,
        "schedule": schedule,
        "market_date": market_date,
        "created_at": f"{market_date}T12:00:00+00:00",
        "producer": "test",
        "status": "ready",
        "artifacts": [
            {
                "name": "payload",
                "relative_path": "artifacts/payload.parquet",
                "sha256": _sha(artifact),
                "size_bytes": artifact.stat().st_size,
                "source_revision": "test-v1",
                "knowledge_cutoff": f"{market_date}T12:00:00+00:00",
            }
        ],
    }
    _write_json(release / "release.json", manifest)
    (release / "READY").write_text(_sha(release / "release.json") + "\n", encoding="ascii")
    return release


def _write_attempt(
    shadow: Path,
    *,
    lane: str,
    market_date: str,
    attempt_id: str,
    status: str,
    started_at: str,
    finished_at: str | None,
    stream: str | None = None,
    publish: bool = False,
) -> tuple[Path, Path | None]:
    root = _attempt_root(
        shadow,
        lane=lane,
        market_date=market_date,
        attempt_id=attempt_id,
        stream=stream,
    )
    landing = root / "landing"
    landing.mkdir(parents=True)
    payload = landing / "payload.parquet"
    payload.write_bytes(b"bulk-payload")
    (landing / "provider_raw").mkdir()
    (landing / "provider_raw" / "raw.json").write_bytes(b"raw-provider-bulk")
    release = (
        _write_release(
            shadow,
            lane=lane,
            market_date=market_date,
            attempt_id=attempt_id,
            landing_payload=payload,
            stream=stream,
        )
        if publish
        else None
    )
    _write_json(
        root / "attempt.json",
        {
            "lane_id": lane,
            "stream": stream,
            "market_date": market_date,
            "attempt_id": attempt_id,
            "status": status,
            "attempt_dir": str(root),
            "release_dir": None if release is None else str(release),
            "started_at": started_at,
            "finished_at": finished_at,
        },
    )
    _write_json(root / "datasets.json", {"datasets": []})
    return root, release


def _catalog_lane(
    *, shadow: Path, release: Path, lane: str, release_id: str, manifest_sha: str
) -> dict[str, Any]:
    return {
        "lane": lane,
        "release_id": release_id,
        "release_path": release.relative_to(shadow / "releases").as_posix(),
        "release_manifest_sha256": manifest_sha,
        "created_at": "2026-08-10T12:00:00+00:00",
        "artifacts": [],
    }


def _accept_daily_release(shadow: Path, release: Path, *, market_date: str) -> None:
    manifest = json.loads((release / "release.json").read_text(encoding="ascii"))
    selected = _catalog_lane(
        shadow=shadow,
        release=release,
        lane="market_daily_core",
        release_id=manifest["release_id"],
        manifest_sha=_sha(release / "release.json"),
    )
    lanes = [selected]
    for lane in ("calendar_daily_events", "intraday_1m", "intraday_5m"):
        lanes.append(
            {
                "lane": lane,
                "release_id": f"dummy-{lane}",
                "release_path": f"dummy/{lane}",
                "release_manifest_sha256": "sha256:" + "1" * 64,
                "created_at": "2026-08-10T12:00:00+00:00",
                "artifacts": [],
            }
        )
    catalog: dict[str, Any] = {
        "schema_version": 1,
        "kind": "daily_ods_input_catalog",
        "market_date": market_date,
        "schedule_contract": {
            "id": "schedule-v1",
            "revision": "revision-v1",
            "config_sha256": "sha256:" + "2" * 64,
        },
        "lanes": lanes,
    }
    canonical = json.dumps(
        catalog, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    catalog["catalog_digest"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    run_id = "20260810T130000000000Z"
    catalog_root = shadow / "catalogs" / "daily" / market_date / run_id
    _write_json(catalog_root / "catalog.json", catalog)
    (catalog_root / "READY").write_text(
        _sha(catalog_root / "catalog.json") + "\n", encoding="ascii"
    )
    run_root = shadow / "orchestrator" / "daily" / market_date / run_id
    state = {
        "kind": "ods_daily_orchestration",
        "mode": "ods_to_dwd",
        "market_date": market_date,
        "status": "ready",
        "shadow_root": str(shadow),
        "catalog": {"status": "ready", "root": str(catalog_root)},
        "dwd": {"status": "ready"},
    }
    _write_json(run_root / "run.json", state)
    (run_root / "READY").write_text(_sha(run_root / "run.json") + "\n", encoding="ascii")


def test_daily_success_requires_exact_dwd_catalog_binding_and_preserves_release(
    tmp_path: Path,
) -> None:
    shadow = _shadow(tmp_path)
    attempt, release = _write_attempt(
        shadow,
        lane="market-daily-core",
        market_date="2026-08-10",
        attempt_id="20260810-20260810T120000000000Z",
        status="ready",
        started_at="2026-08-10T12:00:00+00:00",
        finished_at="2026-08-10T12:05:00+00:00",
        publish=True,
    )
    assert release is not None
    _accept_daily_release(shadow, release, market_date="2026-08-10")

    dry_run = prune_ods_shadow(shadow, now=NOW)
    assert [item.kind for item in dry_run.actions] == ["successful_attempt_bulk"]
    assert (attempt / "landing").is_dir()
    assert dry_run.reclaimable_bytes < dry_run.logical_bytes

    applied = prune_ods_shadow(shadow, now=NOW, apply=True)
    assert applied.mode == "apply"
    assert not (attempt / "landing").exists()
    assert (attempt / "attempt.json").is_file()
    assert verify_ods_release(release).release.release_id.startswith("hermes-market-daily-core-")


def test_daily_success_without_dwd_binding_is_skipped_closed(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    attempt, _ = _write_attempt(
        shadow,
        lane="market-daily-core",
        market_date="2026-08-10",
        attempt_id="20260810-20260810T120000000000Z",
        status="ready",
        started_at="2026-08-10T12:00:00+00:00",
        finished_at="2026-08-10T12:05:00+00:00",
        publish=True,
    )
    report = prune_ods_shadow(shadow, now=NOW, apply=True)
    assert report.actions == ()
    assert len(report.skipped) == 1
    assert "successful DWD catalog" in report.skipped[0].reason
    assert (attempt / "landing").is_dir()


def test_corrupt_success_release_fails_closed(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    attempt, release = _write_attempt(
        shadow,
        lane="weekly-corrections",
        market_date="2026-08-08",
        attempt_id="20260808-20260808T120000000000Z",
        status="ready",
        started_at="2026-08-08T12:00:00+00:00",
        finished_at="2026-08-08T14:00:00+00:00",
        publish=True,
    )
    assert release is not None
    (release / "READY").write_text("sha256:" + "0" * 64 + "\n", encoding="ascii")
    with pytest.raises(OdsShadowPruneError, match="verification failed"):
        prune_ods_shadow(shadow, now=NOW, apply=True)
    assert (attempt / "landing").is_dir()


def test_failed_attempt_bulk_uses_status_timestamp_and_three_day_grace(
    tmp_path: Path,
) -> None:
    shadow = _shadow(tmp_path)
    old, _ = _write_attempt(
        shadow,
        lane="market-daily-core",
        market_date="2026-08-10",
        attempt_id="20260810-20260810T120000000000Z",
        status="partial",
        started_at="2026-08-10T12:00:00+00:00",
        finished_at="2026-08-10T12:05:00+00:00",
    )
    recent, _ = _write_attempt(
        shadow,
        lane="market-daily-core",
        market_date="2026-08-20",
        attempt_id="20260820-20260820T120000000000Z",
        status="failed",
        started_at="2026-08-20T12:00:00+00:00",
        finished_at="2026-08-20T12:05:00+00:00",
    )
    os.utime(old / "landing", (NOW.timestamp(), NOW.timestamp()))
    report = prune_ods_shadow(shadow, now=NOW, apply=True)
    assert [item.kind for item in report.actions] == ["abandoned_attempt_bulk"]
    assert not (old / "landing").exists()
    assert (recent / "landing").is_dir()


def test_completed_and_abandoned_checkpoints_follow_attempt_state(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    _write_attempt(
        shadow,
        lane="weekly-corrections",
        market_date="2026-08-08",
        attempt_id="20260808-20260808T120000000000Z",
        status="ready",
        started_at="2026-08-08T12:00:00+00:00",
        finished_at="2026-08-08T14:00:00+00:00",
        publish=True,
    )
    _write_attempt(
        shadow,
        lane="weekly-corrections",
        market_date="2026-08-01",
        attempt_id="20260801-20260801T120000000000Z",
        status="failed",
        started_at="2026-08-01T12:00:00+00:00",
        finished_at="2026-08-01T14:00:00+00:00",
    )
    completed = shadow / "provider-checkpoints/weekly-corrections/raw_f10/2026-08-08/batch-1"
    abandoned = shadow / "provider-checkpoints/weekly-corrections/raw_f10/2026-08-01/batch-1"
    completed.mkdir(parents=True)
    abandoned.mkdir(parents=True)
    (completed / "part.json").write_text("{}", encoding="ascii")
    (abandoned / "part.json").write_text("{}", encoding="ascii")

    report = prune_ods_shadow(shadow, now=NOW, apply=True)
    kinds = {item.kind for item in report.actions}
    assert "completed_provider_checkpoint" in kinds
    assert "abandoned_provider_checkpoint" in kinds
    assert not completed.parent.exists()
    assert not abandoned.parent.exists()
    assert (shadow / "releases").is_dir()


def test_recent_retry_after_success_keeps_checkpoint_for_three_days(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    _write_attempt(
        shadow,
        lane="weekly-corrections",
        market_date="2026-08-15",
        attempt_id="20260815-20260815T120000000000Z",
        status="ready",
        started_at="2026-08-15T12:00:00+00:00",
        finished_at="2026-08-15T14:00:00+00:00",
        publish=True,
    )
    _write_attempt(
        shadow,
        lane="weekly-corrections",
        market_date="2026-08-15",
        attempt_id="20260815-20260821T120000000000Z",
        status="failed",
        started_at="2026-08-21T12:00:00+00:00",
        finished_at="2026-08-21T14:00:00+00:00",
    )
    checkpoint = shadow / "provider-checkpoints/weekly-corrections/raw_f10/2026-08-15"
    checkpoint.mkdir(parents=True)
    (checkpoint / "part.json").write_text("{}", encoding="ascii")

    prune_ods_shadow(shadow, now=NOW, apply=True)
    assert checkpoint.is_dir()


def test_active_lane_lock_and_running_orchestrator_refuse_apply(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    lock_path = shadow / "locks/market-daily-core.lock"
    with lock_path.open("a+", encoding="ascii") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(OdsShadowPruneError, match="lane lock is active"):
            prune_ods_shadow(shadow, now=NOW, apply=True)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    context = shadow / "orchestrator/daily/notification-context.json"
    _write_json(context, {"status": "running"})
    with pytest.raises(OdsShadowPruneError, match="active run"):
        prune_ods_shadow(shadow, now=NOW, apply=True)


def test_attempt_audit_expires_only_after_bulk_is_already_absent(tmp_path: Path) -> None:
    shadow = _shadow(tmp_path)
    attempt, _ = _write_attempt(
        shadow,
        lane="market-daily-core",
        market_date="2026-05-01",
        attempt_id="20260501-20260501T120000000000Z",
        status="failed",
        started_at="2026-05-01T12:00:00+00:00",
        finished_at="2026-05-01T12:05:00+00:00",
    )
    first = prune_ods_shadow(shadow, now=NOW, apply=True)
    assert [item.kind for item in first.actions] == ["abandoned_attempt_bulk"]
    assert attempt.is_dir()
    second = prune_ods_shadow(shadow, now=NOW, apply=True)
    assert [item.kind for item in second.actions] == ["expired_attempt_audit"]
    assert not attempt.exists()
