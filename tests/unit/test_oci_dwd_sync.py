from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

from quanthus import warehouse_notifications, warehouse_sync


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _write_package(root: Path, market_date: str = "2026-08-04") -> tuple[Path, dict[str, object]]:
    package_id = "dwd-daily-20260804-test"
    package = root / "packages" / market_date / package_id
    package.mkdir(parents=True)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_domain_increment",
        "package_id": package_id,
        "market_date": market_date,
        "files": [],
        "content_digest": "sha256:" + hashlib.sha256(_canonical([])).hexdigest(),
        "row_counts": {},
    }
    manifest_path = package / "manifest.json"
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    manifest_sha = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (package / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    pointer = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_current",
        "package_id": package_id,
        "market_date": market_date,
        "manifest_path": f"packages/{market_date}/{package_id}/manifest.json",
        "manifest_sha256": manifest_sha,
        "content_digest": manifest["content_digest"],
    }
    (root / "CURRENT.json").write_bytes(_canonical(pointer) + b"\n")
    return package, pointer


def test_sync_latest_seals_one_remote_package_and_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    remote = tmp_path / "remote"
    package, _ = _write_package(remote)
    local = tmp_path / "local"
    key = tmp_path / "key"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("key", encoding="ascii")
    key.chmod(0o600)
    known_hosts.write_text("known", encoding="ascii")
    monkeypatch.setenv("QUANTHUS_CN01_DWD_HOST", "cn01.example")
    monkeypatch.setenv("QUANTHUS_CN01_DWD_PORT", "22")
    monkeypatch.setenv("QUANTHUS_CN01_DWD_USER", "quanthus-dwd-ro")
    monkeypatch.setenv("QUANTHUS_CN01_DWD_KEY", str(key))
    monkeypatch.setenv("QUANTHUS_CN01_DWD_KNOWN_HOSTS", str(known_hosts))

    def fake_rsync(*, source: str, destination: Path, ssh: str, extra=()) -> None:
        del ssh, extra
        relative = source.split(":", 1)[1].lstrip("./")
        source_path = remote / relative
        destination = Path(destination)
        if source_path.is_dir():
            shutil.copytree(
                source_path,
                destination,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("READY"),
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)

    monkeypatch.setattr(warehouse_sync, "_run_rsync", fake_rsync)
    result = warehouse_sync.sync_latest(local_root=local, market_date=date(2026, 8, 4))
    assert result.changed is True
    assert (local / "CURRENT.json").is_file()
    assert (local / "packages/2026-08-04/dwd-daily-20260804-test/READY").is_file()
    second = warehouse_sync.sync_latest(local_root=local, market_date=date(2026, 8, 4))
    assert second.changed is False
    assert second.manifest_sha256 == result.manifest_sha256
    assert package.name == second.package_id


def test_notification_is_idempotent_after_success(tmp_path: Path, monkeypatch) -> None:
    daily_root = tmp_path / "daily"
    _write_package(daily_root)
    state_root = tmp_path / "notifications"
    calls: list[str] = []

    def fake_send(**kwargs):
        calls.append(kwargs["event"])
        return {"provider": "test", "status": "sent", "attempts": 1}

    monkeypatch.setattr(warehouse_notifications, "_send_resend", fake_send)
    first = warehouse_notifications.send_daily_event(
        event="cn01_dwd_completed",
        daily_root=daily_root,
        market_date=date(2026, 8, 4),
        state_root=state_root,
    )
    second = warehouse_notifications.send_daily_event(
        event="cn01_dwd_completed",
        daily_root=daily_root,
        market_date=date(2026, 8, 4),
        state_root=state_root,
    )
    assert first["status"] == "sent"
    assert second["status"] == "skipped"
    assert calls == ["cn01_dwd_completed"]


def test_oci_sync_units_define_2030_pull_and_two_notifications() -> None:
    root = Path(__file__).resolve().parents[2]
    service = (root / "ops/systemd/oci-dwd-daily-sync.service").read_text(encoding="ascii")
    timer = (root / "ops/systemd/oci-dwd-daily-sync.timer").read_text(encoding="ascii")
    cn01_notification = (root / "ops/systemd/oci-dwd-cn01-notification.service").read_text(
        encoding="ascii"
    )
    oci_notification = (root / "ops/systemd/oci-dwd-sync-notification.service").read_text(
        encoding="ascii"
    )
    assert "sync-cn01-dwd-daily" in service
    assert "${QUANTHUS_PYTHON}" in service
    assert (
        "OnSuccess=oci-dwd-cn01-notification.service oci-dwd-sync-notification.service" in service
    )
    assert "OnCalendar=Mon..Fri *-*-* 20:30:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
    assert "--event cn01_dwd_completed" in cn01_notification
    assert "--event oci_dwd_sync_completed" in oci_notification
