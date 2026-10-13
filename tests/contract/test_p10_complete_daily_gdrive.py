"""Archive trigger, source admission, primary/fallback, resumable cloud receipts."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

import pytest

from quanthus.pipelines import p10_complete_daily as full
from quanthus.pipelines import p10_complete_daily_gdrive as archive
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, file_digest, verify_seal
from tests.contract.test_p10_complete_daily import DAY, REVISION
from tests.contract.test_p10_complete_daily import members as source_members


@pytest.fixture
def complete(tmp_path: Path) -> Path:
    daily, m1, m5 = source_members.__wrapped__(tmp_path)
    root = tmp_path / "complete"
    full.build(daily=daily, m1=m1, m5=m5, output_root=root, code_revision=REVISION)
    return root


def test_no_complete_pointer_does_not_archive_or_notify(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(archive, "failure", lambda **kwargs: pytest.fail("no false mail"))
    result = archive.run(source=tmp_path / "source", state=tmp_path / "state", revision=REVISION)
    assert result["eligible"] == 0 and result["results"] == []


def test_source_bound_archive_has_finite_large_transfer_budget_and_separate_namespace() -> None:
    policy = archive.object_at(archive.POLICY_PATH)
    assert policy["policy_id"] == "p10-complete-daily-gdrive-v4"
    assert policy["remote_prefix"] == "source-bound-v4"
    assert policy["command_timeout_seconds"] == 7200
    assert policy["route_timeout_seconds"] == 7500
    assert policy["job_timeout_seconds"] == 15000
    assert policy["drive_chunk_size_bytes"] == 4 * 1024**2
    assert policy["transfer_io_timeout_seconds"] == 300
    assert policy["max_packages_per_run"] == 1
    assert policy["disk_reserve_bytes"] == 4 * 1024**3
    assert policy["required_intervals"] == ["1d", "1m", "5m"]
    assert policy["primary_proxy"]["name"] == "US.Stlaure"
    assert policy["delete_remote"] is False


def test_missing_member_R3_or_legacy_base_cannot_be_archived(
    complete: Path, monkeypatch, tmp_path: Path
) -> None:
    package, pointer, _ = archive.admitted(complete, date.fromisoformat(DAY))
    victim = next(p for p in package.rglob("*.parquet"))
    victim.write_bytes(b"corrupt")
    monkeypatch.setattr(archive, "transfer", lambda *a, **k: pytest.fail("bad input uploaded"))
    result = archive.run(source=complete, state=tmp_path / "state", revision=REVISION, notify=False)
    assert result["status"] == "archive_failed"
    assert pointer["manifest_sha256"]


def test_admission_rejects_forged_legacy_binding(complete: Path, monkeypatch) -> None:
    package, _, _ = archive.admitted(complete, date.fromisoformat(DAY))
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["base"]["manifest_sha256"] = "sha256:" + "0" * 64
    monkeypatch.setattr(archive, "verify_seal", lambda *a: manifest)
    with pytest.raises(archive.ArchiveError, match="admission_differs"):
        archive.admitted(complete, date.fromisoformat(DAY))
    manifest["base"]["manifest_sha256"] = archive.BASE23_MANIFEST
    manifest["minute_quality_grades"]["1m"]["R3"] = 1
    with pytest.raises(archive.ArchiveError, match="admission_differs"):
        archive.admitted(complete, date.fromisoformat(DAY))


def test_archive_roundtrip_preserves_exact_package_and_source(
    complete: Path, tmp_path: Path
) -> None:
    if not shutil.which("zstd"):
        pytest.skip("tar.zst requires zstd; Linux runtime acceptance remains required")
    if "GNU tar" not in subprocess.check_output(["tar", "--version"], text=True):
        pytest.skip("GNU tar runtime acceptance runs on CN01")
    state = tmp_path / "state"
    state.mkdir()
    policy = archive.object_at(archive.POLICY_PATH)
    source, pointer, _ = archive.admitted(complete, date.fromisoformat(DAY))
    before = file_digest(source / "manifest.json")
    upload, report = archive.freeze(complete, date.fromisoformat(DAY), state, REVISION, policy)
    restore = tmp_path / "restore"
    restore.mkdir()
    subprocess.run(
        ["tar", "--zstd", "-xf", str(upload / report["archive_name"]), "-C", str(restore)],
        check=True,
    )
    verify_seal(restore, archive.COMPLETE_KIND)
    assert file_digest(restore / "manifest.json") == before == pointer["manifest_sha256"]
    assert full.verify(restore)["content_digest"] == report["content_digest"]
    assert file_digest(source / "manifest.json") == before
    assert archive.freeze(complete, date.fromisoformat(DAY), state, REVISION, policy)[1] == report
    with pytest.raises(archive.ArchiveError, match="identity_changed"):
        archive.freeze(complete, date.fromisoformat(DAY), state, "b" * 40, policy)


def test_space_reserve_cannot_be_lowered_silently(complete: Path, tmp_path: Path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(8, 4, 4))
    with pytest.raises(archive.ArchiveError, match="scratch_insufficient"):
        archive.freeze(
            complete,
            date.fromisoformat(DAY),
            state,
            REVISION,
            archive.object_at(archive.POLICY_PATH),
        )


def test_archive_budget_uses_actual_run_device_and_retains_local_mail_reserve(monkeypatch):
    state, run = Path("/local/state"), Path("/nas/run")
    reserve = 4 * 1024**3
    free = {state: reserve + 1, run: reserve + 1024}
    monkeypatch.setattr(
        archive.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(0, 0, free[p])
    )
    archive.check_archive_space(state, run, 1024, reserve)
    free[run] -= 1
    with pytest.raises(archive.ArchiveError, match="scratch_insufficient"):
        archive.check_archive_space(state, run, 1024, reserve)
    free[run] += 1
    free[state] = reserve - 1
    with pytest.raises(archive.ArchiveError, match="control_state_insufficient"):
        archive.check_archive_space(state, run, 1024, reserve)


def test_primary_first_fallback_then_marker_last(tmp_path: Path, monkeypatch):
    upload = tmp_path / "upload"
    upload.mkdir()
    report = {
        "remote": "gdrive:shadow",
        "manifest_sha256": "a",
        "content_digest": "b",
        "archive_sha256": "c",
        "archive_policy_sha256": "d",
    }
    calls = []
    policy = archive.object_at(archive.POLICY_PATH)

    def fake(args, **kwargs):
        calls.append((kwargs["proxy"], args[0]))
        if kwargs["proxy"] == policy["primary_proxy"]["url"]:
            raise archive.ArchiveError("network_failed")
        return (tmp_path / "CLOUD_READY.json").read_text() if args[0] == "cat" else ""

    monkeypatch.setattr(archive, "rclone", fake)
    result = archive.transfer(
        upload, report, tmp_path / "config", policy, job_until=time.monotonic() + 30
    )
    assert calls[0] == ("http://127.0.0.1:19082", "copy")
    assert [x[1] for x in calls] == ["copy", "copy", "check", "copyto", "cat"]
    assert result["proxy"] == "US.WePC"


def test_cloud_marker_retry_preserves_immutable_bytes(tmp_path: Path, monkeypatch):
    upload = tmp_path / "upload"
    upload.mkdir()
    report = {
        "remote": "gdrive:shadow",
        "manifest_sha256": "a",
        "content_digest": "b",
        "archive_sha256": "c",
        "archive_policy_sha256": "d",
    }
    policy = archive.object_at(archive.POLICY_PATH)
    calls = []

    def fake(args, **kwargs):
        calls.append(args[0])
        if args[0] == "copyto":
            raise archive.ArchiveError("rateLimitExceeded")
        return ""

    monkeypatch.setattr(archive, "rclone", fake)
    with pytest.raises(archive.ArchiveError):
        archive.transfer(
            upload, report, tmp_path / "config", policy, job_until=time.monotonic() + 30
        )
    before = (tmp_path / "CLOUD_READY.json").read_bytes()
    monkeypatch.setattr(
        archive, "rclone", lambda args, **kw: before.decode() if args[0] == "cat" else ""
    )
    archive.transfer(upload, report, tmp_path / "config", policy, job_until=time.monotonic() + 30)
    assert (tmp_path / "CLOUD_READY.json").read_bytes() == before


def test_shared_quota_does_not_retry_other_proxy(tmp_path: Path, monkeypatch):
    upload = tmp_path / "upload"
    upload.mkdir()
    calls = []

    def fake(args, **kwargs):
        calls.append(kwargs["proxy"])
        raise archive.ArchiveError("rateLimitExceeded")

    monkeypatch.setattr(archive, "rclone", fake)
    with pytest.raises(archive.ArchiveError, match="rateLimitExceeded"):
        archive.transfer(
            upload,
            {"remote": "gdrive:shadow"},
            tmp_path / "config",
            archive.object_at(archive.POLICY_PATH),
            job_until=time.monotonic() + 30,
        )
    assert calls == ["http://127.0.0.1:19082"]


def test_retry_budget_and_failure_mail_dedup(complete: Path, tmp_path: Path, monkeypatch):
    state = tmp_path / "state"
    calls = []
    monkeypatch.setattr(
        archive,
        "freeze",
        lambda *args, **kwargs: (_ for _ in ()).throw(archive.ArchiveError("probe")),
    )
    monkeypatch.setattr(archive, "failure", lambda **kw: calls.append(kw["stage"]))
    for _ in range(3):
        assert (
            archive.run(source=complete, state=state, revision=REVISION)["status"]
            == "archive_failed"
        )
    fourth = archive.run(source=complete, state=state, revision=REVISION)
    assert fourth["results"][0]["status"] == "retry_budget_exhausted"
    assert (
        calls == ["gdrive"] * 3
    )  # Sender owns persistent dedup, controller permits failed-mail retries.


def test_delivered_identity_reuse_avoids_repacking_and_remote_calls(
    complete: Path, tmp_path: Path, monkeypatch
):
    pointer = archive.object_at(complete / "date-pointers" / f"{DAY}.json")
    state = tmp_path / "state"
    key = pointer["manifest_sha256"][7:]
    atomic(
        state / "delivered" / f"{key}.json",
        {
            **pointer,
            "status": "cloud_archive_verified",
            "archive_policy_sha256": file_digest(archive.POLICY_PATH),
            "effective_archive_policy_sha256": digest(
                encoded(archive.object_at(archive.POLICY_PATH))
            ),
        },
    )
    monkeypatch.setattr(archive, "freeze", lambda *a: pytest.fail("already archived"))
    assert archive.run(source=complete, state=state, revision=REVISION)["eligible"] == 0


def test_path_trigger_and_recovery_timer_are_independent_of_producer():
    root = archive.find_project_root()
    path = (root / "ops/systemd/quanthus-gdrive-complete-daily.path").read_text()
    service = (root / "ops/systemd/quanthus-gdrive-complete-daily.service").read_text()
    assert "PathChanged=" + str(archive.SOURCE / "date-pointers") in path
    assert "gdrive-complete-current" in service
    assert "data-delivery-current" not in service


def test_rclone_uses_chunk_suffix_supported_by_installed_old_version(tmp_path: Path, monkeypatch):
    commands = []

    def fake(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(archive.subprocess, "run", fake)
    archive.rclone(
        ["lsd", "gdrive:"],
        config=tmp_path / "config",
        proxy="http://127.0.0.1:19082",
        policy=archive.object_at(archive.POLICY_PATH),
        until=time.monotonic() + 30,
    )
    command = commands[0]
    assert command[command.index("--drive-chunk-size") + 1] == "4M"
    assert command[command.index("--timeout") + 1] == "300s"


def test_invalid_transfer_profile_fails_before_network(tmp_path: Path, monkeypatch):
    policy = archive.object_at(archive.POLICY_PATH)
    policy["drive_chunk_size_bytes"] = 123
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(archive.ArchiveError, match="invalid_versioned_transfer_profile"):
        archive.rclone(
            ["lsd", "gdrive:"],
            config=tmp_path / "config",
            proxy="http://127.0.0.1:19082",
            policy=policy,
            until=time.monotonic() + 30,
        )


def test_new_archive_failure_uses_existing_global_mail_receipt(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    state = tmp_path / "private-v3"
    key = "a" * 64
    atomic(source / "date-pointers" / "2026-10-13.json", {"manifest_sha256": "sha256:" + key})
    monkeypatch.setattr(
        archive,
        "freeze",
        lambda *a, **k: (_ for _ in ()).throw(archive.ArchiveError("synthetic_transfer_failure")),
    )
    roots = []

    def mail(**kwargs):
        roots.append(kwargs["state_root"])
        return {"status": "already_sent", "message_id": "existing-provider-receipt"}

    monkeypatch.setattr(archive, "failure", mail)
    result = archive.run(source=source, state=state, revision=REVISION)
    assert result["status"] == "archive_failed"
    assert roots == [archive.MAIL_STATE]
    assert not (state / "LAST_SUCCESS.json").exists()


def test_same_date_revision_is_frozen_before_pack(complete: Path, tmp_path: Path):
    with pytest.raises(archive.ArchiveError, match="source_revision_changed_retry"):
        archive.freeze(
            complete,
            date.fromisoformat(DAY),
            tmp_path / "state",
            REVISION,
            archive.object_at(archive.POLICY_PATH),
            expected_manifest="sha256:" + "f" * 64,
        )


def test_exhausted_job_does_not_start_tar(complete: Path, tmp_path: Path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(
        archive.subprocess, "run", lambda *a, **kw: pytest.fail("no tar after cutoff")
    )
    with pytest.raises(archive.ArchiveError, match="archive_job_budget_exhausted"):
        archive.freeze(
            complete,
            date.fromisoformat(DAY),
            state,
            REVISION,
            archive.object_at(archive.POLICY_PATH),
            job_until=time.monotonic() - 1,
        )


def test_older_exhausted_attempt_does_not_starve_new_day(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    state = tmp_path / "state"
    today = archive.datetime.now(archive.timezone.utc).date().isoformat()
    for n, day in enumerate(["2026-10-12", "2026-10-13"]):
        key = str(n + 1) * 64
        atomic(source / "date-pointers" / (day + ".json"), {"manifest_sha256": "sha256:" + key})
        if n == 0:
            atomic(state / "attempts" / (key + ".json"), {"utc_day": today, "count": 3})
    called = []

    def fake(*a, **kw):
        called.append(str(a[1]))
        raise archive.ArchiveError("synthetic_failure")

    monkeypatch.setattr(archive, "freeze", fake)
    archive.run(source=source, state=state, revision=REVISION, notify=False)
    assert called == ["2026-10-13"]


def test_scoped_oauth_refresh_uses_primary_then_fallback_and_cache(tmp_path: Path, monkeypatch):
    from quanthus.pipelines import p10_gdrive_oauth_bridge as bridge

    secret = tmp_path / "secret.json"
    atomic(
        secret,
        {
            "authid": "synthetic-auth",
            "refresh_secret": "synthetic-refresh",
            "oauth_url": "https://synthetic.invalid",
        },
    )
    monkeypatch.setattr(bridge, "SECRETS", secret)
    routes = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"access_token":"synthetic-token","expires":3600}'

    class Opener:
        def open(self, req, timeout):
            if len(routes) == 1:
                raise OSError("synthetic failure")
            return Response()

    def opener(handler):
        routes.append(handler.proxies["https"])
        return Opener()

    monkeypatch.setattr(bridge.request, "build_opener", opener)
    tokens = bridge.Tokens()
    assert tokens.refresh()["refresh_token"] == "synthetic-refresh"
    assert tokens.refresh()["access_token"] == "synthetic-token"
    assert routes == ["http://127.0.0.1:19082", "http://127.0.0.1:19083"]


def test_v4_state_is_separate_and_uses_global_target_stage_mail_ledger():
    assert str(archive.STATE) == "/var/lib/quanthus/gdrive-complete-v4"
    assert str(archive.MAIL_STATE) == "/var/lib/quanthus/gdrive-complete-v1/notifications"
    assert str(archive.CONFIG) == "/etc/quanthus/gdrive-complete-v1/rclone.conf"
    root = archive.POLICY_PATH.parents[2]
    hook = (root / "src/quanthus/pipelines/p10_complete_daily_notifications.py").read_text()
    assert "state_root=MAIL_STATE" in hook
    replay = (root / "ops/systemd/quanthus-complete-daily-mail-retry.service").read_text()
    for queue in (
        "/srv/quanthus/dwd-dws-v2/intraday/operations/complete-daily-v1/notifications",
        "/var/lib/quanthus/gdrive-complete-v1/notifications",
        "/var/lib/quanthus/gdrive-complete-v2/notifications",
        "/var/lib/quanthus/acquisition-continuation-v1/notifications",
    ):
        assert "--retry-state " + queue in replay
