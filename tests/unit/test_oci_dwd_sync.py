from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path
from urllib.error import URLError

import pytest

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


def test_failure_event_does_not_require_a_dwd_package(tmp_path: Path, monkeypatch) -> None:
    receipt = tmp_path / "failure.json"
    receipt.write_bytes(
        _canonical(
            {
                "kind": "oci_cn01_dwd_daily_sync_failure",
                "market_date": "2026-08-04",
                "error_code": "source_market_date_stale",
                "error": "CN01 CURRENT is still 2026-08-03",
                "stage": "oci_dwd_sync",
                "node": "OCI",
                "unit": "oci-dwd-daily-sync.service",
                "cn01_source_failure": True,
            }
        )
        + b"\n"
    )
    calls: list[dict[str, object]] = []

    def fake_send(**kwargs):
        calls.append(kwargs)
        return {"provider": "test", "status": "sent", "attempts": 1}

    monkeypatch.setattr(warehouse_notifications, "_send_resend", fake_send)
    result = warehouse_notifications.send_daily_event(
        event="cn01_dwd_failed",
        daily_root=tmp_path / "no-package",
        market_date=None,
        state_root=tmp_path / "notifications",
        failure_receipt=receipt,
    )
    assert result["status"] == "sent"
    assert calls[0]["pointer"] is None
    assert calls[0]["result"] is None
    assert calls[0]["error"] == "CN01 CURRENT is still 2026-08-03"


def test_source_failure_sends_oci_and_cn01_events(tmp_path: Path, monkeypatch) -> None:
    receipt = tmp_path / "failure.json"
    receipt.write_bytes(
        _canonical(
            {
                "kind": "oci_cn01_dwd_daily_sync_failure",
                "market_date": "2026-08-04",
                "error_code": "source_market_date_stale",
                "error": "CN01 CURRENT is still 2026-08-03",
                "cn01_source_failure": True,
            }
        )
        + b"\n"
    )
    events: list[str] = []

    def fake_send(**kwargs):
        events.append(kwargs["event"])
        return {"provider": "test", "status": "sent", "attempts": 1}

    monkeypatch.setattr(warehouse_notifications, "_send_resend", fake_send)
    result = warehouse_notifications.send_failure_events(
        failure_receipt=receipt,
        daily_root=tmp_path / "no-package",
        state_root=tmp_path / "notifications",
    )
    assert result["status"] == "sent"
    assert events == ["oci_dwd_sync_failed", "cn01_dwd_failed"]


def test_resend_terminal_failure_writes_receipt_and_can_retry(
    tmp_path: Path, monkeypatch
) -> None:
    receipt = tmp_path / "failure.json"
    receipt.write_bytes(
        _canonical(
            {
                "kind": "oci_cn01_dwd_daily_sync_failure",
                "market_date": "2026-08-04",
                "error_code": "transfer_failed",
                "error": "SSH timeout",
            }
        )
        + b"\n"
    )

    def fail_send(**kwargs):
        del kwargs
        raise warehouse_notifications.WarehouseNotificationError(
            "Resend unavailable", code="resend_request_failed", attempts=3
        )

    monkeypatch.setattr(warehouse_notifications, "_send_resend", fail_send)
    with pytest.raises(warehouse_notifications.WarehouseNotificationError):
        warehouse_notifications.send_daily_event(
            event="oci_dwd_sync_failed",
            daily_root=tmp_path / "no-package",
            market_date=None,
            state_root=tmp_path / "notifications",
            failure_receipt=receipt,
        )
    failures = list((tmp_path / "notifications" / "failures").glob("*.json"))
    assert len(failures) == 1
    saved = json.loads(failures[0].read_text(encoding="ascii"))
    assert saved["delivery_status"] == "failed"
    assert saved["attempts"] == 3


def test_resend_success_uses_bearer_idempotency_and_retries_network_errors(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QUANTHUS_RESEND_API_KEY", "resend-test-key")
    monkeypatch.setenv("QUANTHUS_RESEND_FROM", "report@example.com")
    monkeypatch.setenv("QUANTHUS_RESEND_TO", "ops@example.com")
    monkeypatch.setenv("QUANTHUS_RESEND_RETRIES", "1")
    requests: list[object] = []
    attempts = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            del exc_type, exc_value, traceback
            return False

        def getcode(self):
            return 200

        def read(self):
            return b'{"id":"email-test-1"}'

    def fake_urlopen(http_request, timeout):
        nonlocal attempts
        assert timeout == 15.0
        requests.append(http_request)
        attempts += 1
        if attempts == 1:
            raise URLError("temporary DNS failure")
        return Response()

    monkeypatch.setattr(warehouse_notifications.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(warehouse_notifications.time, "sleep", lambda seconds: None)
    result = warehouse_notifications._send_resend(
        event="oci_dwd_sync_failed",
        market_date=date(2026, 8, 4),
        pointer=None,
        result=None,
        key="idempotency-key-1",
        error="temporary source failure",
        context={"node": "OCI"},
    )
    assert result["status"] == "sent"
    assert result["message_id"] == "email-test-1"
    assert len(requests) == 2
    sent_request = requests[-1]
    assert sent_request.full_url == "https://api.resend.com/emails"
    assert sent_request.get_header("Authorization") == "Bearer resend-test-key"
    assert sent_request.get_header("Idempotency-key") == "idempotency-key-1"
    payload = json.loads(sent_request.data)
    assert payload["to"] == ["ops@example.com"]
    assert "失败" in payload["subject"]


def test_sync_rejects_a_stale_remote_current(
    tmp_path: Path, monkeypatch
) -> None:
    remote = tmp_path / "remote"
    _write_package(remote, market_date="2026-08-04")
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
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source_path, destination)

    monkeypatch.setattr(warehouse_sync, "_run_rsync", fake_rsync)
    with pytest.raises(warehouse_sync.WarehouseSyncError) as captured:
        warehouse_sync.sync_latest(local_root=tmp_path / "local", market_date=date(2026, 8, 5))
    assert captured.value.code == "source_market_date_stale"


def test_sync_main_persists_failure_receipt_for_operator_notification(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        warehouse_sync,
        "_ssh_command",
        lambda: (_ for _ in ()).throw(
            warehouse_sync.WarehouseSyncError("known hosts unavailable", code="transfer_failed")
        ),
    )
    local = tmp_path / "local"
    exit_code = warehouse_sync.main(
        ["--local-root", str(local), "--market-date", "2026-08-04"]
    )
    assert exit_code == 2
    assert "sync failed" in capsys.readouterr().err
    saved = json.loads((local / "failures/LATEST.json").read_text(encoding="ascii"))
    assert saved["error_code"] == "transfer_failed"
    assert saved["cn01_source_failure"] is False


def test_oci_sync_units_define_2030_pull_and_success_and_failure_notifications() -> None:
    root = Path(__file__).resolve().parents[2]
    service = (root / "ops/systemd/oci-dwd-daily-sync.service").read_text(encoding="ascii")
    timer = (root / "ops/systemd/oci-dwd-daily-sync.timer").read_text(encoding="ascii")
    cn01_notification = (root / "ops/systemd/oci-dwd-cn01-notification.service").read_text(
        encoding="ascii"
    )
    oci_notification = (root / "ops/systemd/oci-dwd-sync-notification.service").read_text(
        encoding="ascii"
    )
    failure_notification = (
        root / "ops/systemd/oci-dwd-sync-failure-notification.service"
    ).read_text(encoding="ascii")
    assert "sync-cn01-dwd-daily" in service
    assert "/opt/quanthus/current/.venv/bin/python" in service
    assert "EnvironmentFile=/etc/quanthus/t-close-plus-5h.env" in service
    assert (
        "OnSuccess=oci-dwd-cn01-notification.service oci-dwd-sync-notification.service" in service
    )
    assert "OnFailure=oci-dwd-sync-failure-notification.service" in service
    assert "OnCalendar=Mon..Fri *-*-* 20:30:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
    assert "--event cn01_dwd_completed" in cn01_notification
    assert "--event oci_dwd_sync_completed" in oci_notification
    assert "notify-dwd-failure" in failure_notification
    assert "LATEST.json" in failure_notification
