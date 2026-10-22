"""Frozen Base2.4 increments: bounded transport with fake cloud, no real API/data."""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus import p10_container_control as host
from quanthus import p10_gdrive_backup as drive
from quanthus import p10_operational_mail as mail
from tests.unit.test_p10_container_delivery import deployment, private_mail, safe_event


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True))


@pytest.fixture
def selected(tmp_path):
    policy = json.loads(drive.POLICY.read_bytes())
    state = tmp_path / "backup"
    state.mkdir()
    packages = []
    for layer in ("ODS", "DWD"):
        for cadence in ("daily", "weekly"):
            identifier = layer.lower() + "-" + cadence
            root = tmp_path / identifier
            root.mkdir()
            (root / "payload.bin").write_bytes(b"synthetic bytes only " + identifier.encode())
            write(root / "qualification.json", {"synthetic": True, "scope": identifier})
            packages.append(
                {
                    "id": identifier,
                    "layer": layer,
                    "cadence": cadence,
                    "business_date": "2026-10-21",
                    "root": str(root),
                    "qualification": "qualified_scope",
                    "limitations": [],
                    "qualification_receipt": "qualification.json",
                    "files": {
                        p.name: {"bytes": p.stat().st_size, "sha256": drive.digest(p)}
                        for p in root.iterdir()
                    },
                }
            )
    plan = {
        "kind": "p10-base24-backup-selection-v1",
        "policy_sha256": drive.digest(drive.POLICY),
        "base_version": "2.4",
        "code_revision": "a" * 40,
        "base_manifest_sha256": policy["base_manifest_sha256"],
        "intervals": ["1d", "5m"],
        "execution_authorized": False,
        "formal_product_deletion": "forbidden",
        "remote_name": "gdrive",
        "folder_ids": {"ODS": "explicit-ods-folder-id", "DWD": "explicit-dwd-folder-id"},
        "packages": packages,
        "limits": {
            "max_source_bytes": 1024**2,
            "max_archive_bytes": 1024**2,
            "free_floor_bytes": 1024,
            "timeout_seconds": 60,
        },
    }
    path, secret = tmp_path / "selection.json", tmp_path / "private-rclone.conf"
    write(path, plan)
    secret.write_text("synthetic secret, never an actual credential")
    return plan, path, state, secret


@pytest.fixture
def fake_cloud(tmp_path, monkeypatch):
    root = tmp_path / "cloud"
    root.mkdir()
    calls = []

    def call(args, plan, secret, until):
        calls.append(args)
        folder = args[args.index("--drive-root-folder-id") + 1]
        remote = args[2] if args[0] == "copy" else args[1]
        dst = root / folder / remote.split(":", 1)[1]
        if args[0] == "copy":
            assert "--immutable" in args and "--checksum" in args
            dst.mkdir(parents=True, exist_ok=True)
            for src in Path(args[1]).iterdir():
                target = dst / src.name
                if target.exists():
                    assert target.read_bytes() == src.read_bytes()
                else:
                    shutil.copyfile(src, target)
            return ""
        assert args[0] == "lsjson"
        return json.dumps(
            [
                {
                    "Name": p.name,
                    "Size": p.stat().st_size,
                    "Hashes": {"MD5": hashlib.md5(p.read_bytes()).hexdigest()},
                }
                for p in dst.iterdir()
            ]
        )

    monkeypatch.setattr(drive, "cloud", call)
    return root, calls, call


def test_four_scopes_separate_folder_ids_archive_inventory_and_source_unchanged(
    selected, fake_cloud
):
    plan, path, state, secret = selected
    before = {
        str(p): (p.stat().st_ino, p.read_bytes())
        for v in plan["packages"]
        for p in Path(v["root"]).iterdir()
    }
    result = drive.backup(path, state, secret)
    assert result["status"] == "cloud_bytes_verified"
    assert result["local_deletion_authorized"] is False
    assert result["data_qualification_changed"] is False
    cloud, calls, _ = fake_cloud
    assert {p.name for p in cloud.iterdir()} == set(plan["folder_ids"].values())
    assert all("/base-2.4/" in a[2] for a in calls if a[0] == "copy")
    for package in plan["packages"]:
        archive = next((state / drive.digest(path) / package["id"]).rglob("data.tar"))
        with tarfile.open(archive) as tar:
            assert set(tar.getnames()) == set(package["files"])
            for name, record in package["files"].items():
                assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == record["sha256"]
    assert before == {name: (Path(name).stat().st_ino, Path(name).read_bytes()) for name in before}
    assert all("synthetic secret" not in p.read_text() for p in state.rglob("*.json"))


@pytest.mark.parametrize(
    "mutation",
    [
        "2.3",
        "1m",
        "base",
        "policy",
        "cadence",
        "folder",
        "missing",
        "extra",
        "receipt",
        "budget",
        "overlap",
    ],
)
def test_wrong_version_scope_or_closure_rejected_before_any_output(selected, mutation):
    plan, path, state, _ = selected
    if mutation == "2.3":
        plan["base_version"] = "2.3"
    elif mutation == "1m":
        plan["intervals"] = ["1d", "1m", "5m"]
    elif mutation == "base":
        plan["base_manifest_sha256"] = "sha256:" + "0" * 64
    elif mutation == "policy":
        plan["policy_sha256"] = "0" * 64
    elif mutation == "cadence":
        plan["packages"][0]["cadence"] = "base"
    elif mutation == "folder":
        plan["folder_ids"]["ODS"] = plan["folder_ids"]["DWD"]
    elif mutation == "missing":
        (Path(plan["packages"][0]["root"]) / "payload.bin").unlink()
    elif mutation == "extra":
        (Path(plan["packages"][0]["root"]) / "extra").touch()
    elif mutation == "receipt":
        plan["packages"][0]["qualification_receipt"] = "missing.json"
    elif mutation == "budget":
        plan["limits"]["max_archive_bytes"] = 1
    else:
        state = Path(plan["packages"][0]["root"])
    write(path, plan)
    before = set(state.iterdir())
    with pytest.raises((ValueError, OSError)):
        drive.selection(path, state)
    assert set(state.iterdir()) == before


def test_pack_hashes_actual_bytes_and_does_not_upload_failed_source(selected, fake_cloud):
    plan, path, state, secret = selected
    package = plan["packages"][0]
    member = Path(package["root"]) / "payload.bin"
    member.write_bytes(b"x" * member.stat().st_size)
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        drive.backup(path, state, secret)
    assert not fake_cloud[1]
    assert not list(state.rglob("VERIFIED.json"))
    assert list(state.rglob("data.tar")), "keep failed evidence"


@pytest.mark.parametrize("mutation", ["hash", "missing", "extra", "duplicate"])
def test_cloud_exact_set_and_md5_required(selected, fake_cloud, monkeypatch, mutation):
    _, path, state, secret = selected
    real = fake_cloud[2]

    def changed(args, *a):
        result = real(args, *a)
        if args[0] == "lsjson":
            files = json.loads(result)
            if mutation == "hash":
                files[0]["Hashes"] = {}
            elif mutation == "missing":
                files.pop()
            elif mutation == "extra":
                files.append({"Name": "extra"})
            else:
                files.append(files[0])
            return json.dumps(files)
        return result

    monkeypatch.setattr(drive, "cloud", changed)
    with pytest.raises(ValueError, match="CLOUD_EXACT"):
        drive.backup(path, state, secret)
    assert not list(state.rglob("VERIFIED.json"))


def test_interrupted_upload_reuses_frozen_archive_without_recollecting(
    selected, fake_cloud, monkeypatch
):
    _, path, state, secret = selected
    real = fake_cloud[2]
    monkeypatch.setattr(drive, "cloud", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        drive.backup(path, state, secret)
    archive = next(state.rglob("data.tar"))
    before = archive.stat().st_ino, archive.read_bytes()
    packed = drive.pack
    calls = []

    def pack(package, *a):
        calls.append(package["id"])
        packed(package, *a)

    monkeypatch.setattr(drive, "pack", pack)
    monkeypatch.setattr(drive, "cloud", real)
    drive.backup(path, state, secret)
    assert before == (archive.stat().st_ino, archive.read_bytes())
    assert "ods-daily" not in calls


def test_partial_weekly_keeps_qualification_limitations(selected, fake_cloud):
    plan, path, state, secret = selected
    plan["packages"][1].update(qualification="partial_scope", limitations=["other domains held"])
    write(path, plan)
    result = drive.backup(path, state, secret)
    assert result["packages"][1]["qualification"] == "partial_scope"
    assert result["packages"][1]["limitations"] == ["other domains held"]


@pytest.mark.parametrize("member_type", ["symlink", "hardlink", "scan_error", "space"])
def test_unsafe_or_unreadable_tree_and_insufficient_space_never_begin(
    selected, monkeypatch, member_type
):
    plan, path, state, _ = selected
    source = Path(plan["packages"][0]["root"])
    if member_type == "symlink":
        member = source / "payload.bin"
        member.unlink()
        member.symlink_to(path)
    elif member_type == "hardlink":
        (source / "unexpected-hardlink").hardlink_to(source / "payload.bin")
    elif member_type == "space":
        monkeypatch.setattr(drive.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    else:
        original = drive.os.walk

        def failed(root, **kwargs):
            kwargs["onerror"](PermissionError("synthetic scan failure"))
            return original(root, **kwargs)

        monkeypatch.setattr(drive.os, "walk", failed)
    with pytest.raises((ValueError, OSError)):
        drive.selection(path, state)
    assert not list(state.iterdir())


def test_rclone_uses_finite_copy_without_inherited_credentials_or_tls_override(
    tmp_path, monkeypatch
):
    def run(command, **kwargs):
        assert command[:2] == ["rclone", "copy"]
        assert kwargs["env"]["PATH"].split(":")[0] == "/usr/local/bin"
        assert "--no-check-certificate" not in command
        assert "RCLONE_CONFIG" not in kwargs["env"]
        assert "RCLONE_DRIVE_TOKEN" not in kwargs["env"]
        assert "--config" in command and "--retries" in command
        kwargs["stdout"].write(b"safe synthetic response")
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("RCLONE_CONFIG", "/do-not-use")
    monkeypatch.setenv("RCLONE_DRIVE_TOKEN", "synthetic-credential-do-not-inherit")
    monkeypatch.setattr(drive.subprocess, "run", run)
    assert (
        drive.cloud(
            ["copy", "source", "gdrive:target"], {}, tmp_path / "conf", drive.time.monotonic() + 60
        )
        == "safe synthetic response"
    )


def test_mail_failure_stays_in_task_and_does_not_change_data_event(tmp_path, monkeypatch):
    path = tmp_path / "event.json"
    write(path, safe_event())
    original = path.read_bytes()
    monkeypatch.setattr(
        mail.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
    )
    monkeypatch.setattr(mail.time, "sleep", lambda _: None)
    assert mail.notify(path, tmp_path / "mail", private_mail(tmp_path)) == "failed_event_retained"
    assert path.read_bytes() == original


@pytest.mark.parametrize("mail_retry", [False, True])
@pytest.mark.parametrize("access", ["direct", "sudo-noninteractive"])
def test_backup_or_mail_retry_launches_only_same_image_delivery_role(
    tmp_path, monkeypatch, mail_retry, access
):
    config = deployment(tmp_path)
    config["docker_access"] = access
    path = Path(config["state"]) / "events/event.json"
    path.parent.mkdir()
    write(path, safe_event())
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[: len(host.docker_command(config))] == host.docker_command(config)
        assert "backup" in args and "daily" not in args and "weekly" not in args
        assert kwargs["env"].get("RCLONE_CONFIG") is None
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(host.subprocess, "run", run)
    result = host.delivery_container(config, path if mail_retry else None)
    assert result["role"] == ("mail" if mail_retry else "backup")
    assert len(calls) == 1


def test_explicit_sealed_ods_aliases_archive_independent_bytes_without_source_changes(
    selected, fake_cloud, tmp_path
):
    plan, path, state, secret = selected
    p = plan["packages"][0]
    root = Path(p["root"])
    alias = tmp_path / "original-provider-alias.bin"
    alias.hardlink_to(root / "payload.bin")
    (root / "READY").write_text("synthetic sealed input")
    (root / "release.json").write_bytes((root / "qualification.json").read_bytes())
    p.update(read_only_source_links=True, qualification_receipt="release.json")
    for name in ["READY", "release.json"]:
        p["files"][name] = {
            "bytes": (root / name).stat().st_size,
            "sha256": drive.digest(root / name),
        }
    write(path, plan)
    before = alias.read_bytes(), alias.stat().st_ino, alias.stat().st_nlink
    result = drive.backup(path, state, secret)
    assert result["status"] == "cloud_bytes_verified"
    assert before == (alias.read_bytes(), alias.stat().st_ino, alias.stat().st_nlink)
    archive = next((state / drive.digest(path) / p["id"]).rglob("data.tar"))
    with tarfile.open(archive) as tar:
        assert all(m.isfile() and not m.islnk() and not m.issym() for m in tar.getmembers())
        assert tar.extractfile("payload.bin").read() == before[0]


def test_ods_alias_mutation_is_detected_and_never_uploaded(
    selected, fake_cloud, tmp_path, monkeypatch
):
    plan, path, state, secret = selected
    p = plan["packages"][0]
    root = Path(p["root"])
    alias = tmp_path / "original-provider-alias.bin"
    alias.hardlink_to(root / "payload.bin")
    for name in ["READY", "release.json"]:
        (root / name).write_text("synthetic")
        p["files"][name] = {
            "bytes": (root / name).stat().st_size,
            "sha256": drive.digest(root / name),
        }
    p.update(read_only_source_links=True, qualification_receipt="release.json")
    write(path, plan)
    original = drive._Reader.read
    changed = []

    def read(self, size=-1):
        data = original(self, size)
        if not changed:
            alias.write_bytes(b"x" * alias.stat().st_size)
            changed.append(True)
        return data

    monkeypatch.setattr(drive._Reader, "read", read)
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        drive.backup(path, state, secret)
    assert not fake_cloud[1]
