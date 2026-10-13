import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus import warehouse_sync as sync


def setup_revision(tmp_path, monkeypatch):
    day = date(2026, 10, 13)
    old = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_current",
        "product_scope": "daily-core-v1",
        "market_date": str(day),
        "package_id": "old",
        "manifest_path": f"packages/{day}/old/manifest.json",
        "manifest_sha256": "sha256:" + "a" * 64,
        "content_digest": "sha256:" + "b" * 64,
    }
    new = dict(
        old,
        package_id="new",
        manifest_path=f"packages/{day}/new/manifest.json",
        manifest_sha256="sha256:" + "c" * 64,
        content_digest="sha256:" + "d" * 64,
    )
    parent = {"market_date": "2026-10-12", "manifest_sha256": "sha256:" + "e" * 64}
    manifests = {
        "old": {"base": {"identity": "exact"}, "prior": parent},
        "new": {"base": {"identity": "exact"}, "prior": parent},
    }
    for name in ("old", "new"):
        p = tmp_path / f"packages/{day}/{name}"
        p.mkdir(parents=True)
        (p / "manifest.json").write_text(json.dumps(manifests[name]))
    record = {
        "old": old,
        "new": new,
        "old_package_retained": True,
        "normal_immutable_publication_gate_unchanged": True,
        "difference_acceptance": "sha256:" + "f" * 64,
        "at": "2026-10-13T14:44:18.119198+00:00",
    }
    policy = {"kind": "p10_exact_daily_core_mirror_revisions_v1", "revisions": [record]}
    original = sync._object
    monkeypatch.setattr(
        sync,
        "_object",
        lambda p: policy if p.name == "p10-daily-core-mirror-revisions.v1.json" else original(p),
    )
    monkeypatch.setattr(
        sync,
        "verify_daily_domain_increment",
        lambda p: SimpleNamespace(
            manifest_sha256=old["manifest_sha256"], content_digest=old["content_digest"]
        ),
    )
    return old, new, manifests["new"], policy


def test_exact_authorized_revision_keeps_original_generic_guard(tmp_path, monkeypatch):
    old, new, manifest, policy = setup_revision(tmp_path, monkeypatch)
    with pytest.raises(sync.WarehouseSyncError, match="same-date"):
        sync._ensure_monotonic(old, new, date(2026, 10, 13))
    receipt = sync._approved_daily_core_revision(tmp_path, old, new, manifest)
    assert receipt["old"] == old and receipt["new"] == new
    assert receipt["old_package_retained"] is True
    assert (tmp_path / old["manifest_path"]).exists()


@pytest.mark.parametrize(
    "change", ["unapproved", "base", "prior", "old_file", "wrong_digest", "not_retained"]
)
def test_other_revisions_or_changed_local_evidence_remain_blocked(tmp_path, monkeypatch, change):
    old, new, manifest, policy = setup_revision(tmp_path, monkeypatch)
    if change == "unapproved":
        new = dict(new, manifest_sha256="sha256:" + "0" * 64)
    if change == "base":
        manifest["base"] = {"identity": "other"}
    if change == "prior":
        manifest["prior"] = {"market_date": "2026-10-09"}
    if change == "old_file":
        (tmp_path / old["manifest_path"]).unlink()
    if change == "wrong_digest":
        monkeypatch.setattr(
            sync,
            "verify_daily_domain_increment",
            lambda p: SimpleNamespace(
                manifest_sha256=old["manifest_sha256"], content_digest="changed"
            ),
        )
    if change == "not_retained":
        policy["revisions"][0]["old_package_retained"] = False
    with pytest.raises(sync.WarehouseSyncError):
        sync._approved_daily_core_revision(tmp_path, old, new, manifest)


def test_actual_native_revision_sync_and_retry_preserve_first_receipt(tmp_path, monkeypatch):
    import shutil

    verify_daily_domain_increment = sync.verify_daily_domain_increment

    from tests.unit.test_p10_daily_core_product import _canonical_bytes, _empty_core_package, sha

    remote = tmp_path / "remote/products/daily-core"
    old_package = _empty_core_package(tmp_path)
    day = date(2026, 9, 24)
    old_rel = f"packages/{day}/old"
    new_rel = f"packages/{day}/new"
    shutil.copytree(old_package, remote / old_rel)
    old_manifest = json.loads((remote / old_rel / "manifest.json").read_text())

    def reseal(name, value):
        path = remote / name
        value["package_id"] = Path(name).name
        raw = _canonical_bytes(value) + b"\n"
        (path / "manifest.json").write_bytes(raw)
        (path / "READY").write_text(sha(raw) + "\n")
        result = verify_daily_domain_increment(path)
        return {
            "schema_version": 2,
            "kind": "cn01_daily_core_dwd_current",
            "product_scope": "daily-core-v1",
            "market_date": str(day),
            "package_id": result.package_id,
            "manifest_path": name + "/manifest.json",
            "manifest_sha256": result.manifest_sha256,
            "content_digest": result.content_digest,
        }

    old = reseal(old_rel, old_manifest)
    shutil.copytree(remote / old_rel, remote / new_rel)
    new_manifest = dict(old_manifest, code_revision="b" * 40)
    new = reseal(new_rel, new_manifest)
    local = tmp_path / "local"
    monkeypatch.setenv("QUANTHUS_CN01_DWD_REMOTE_ROOT", "products/daily-core")
    monkeypatch.setenv("QUANTHUS_CN01_DWD_HOST", "fixture")
    monkeypatch.setattr(sync, "_ssh_command", lambda: ("fixture", "22", "readonly", "", "", "ssh"))

    def rsync(*, source, destination, ssh, extra=()):
        origin = tmp_path / "remote" / source.split(":", 1)[1]
        if origin.is_dir():
            shutil.copytree(
                origin, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("READY")
            )
        else:
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)

    monkeypatch.setattr(sync, "_run_rsync", rsync)
    (remote / "CURRENT_DAILY_CORE.json").write_text(json.dumps(old))
    sync.sync_latest(local_root=local, market_date=day, product_scope="daily-core-v1")
    (remote / "CURRENT_DAILY_CORE.json").write_text(json.dumps(new))
    original = sync._object
    policy = {
        "kind": "p10_exact_daily_core_mirror_revisions_v1",
        "revisions": [
            {
                "old": old,
                "new": new,
                "old_package_retained": True,
                "normal_immutable_publication_gate_unchanged": True,
                "difference_acceptance": "sha256:" + "f" * 64,
                "at": "2026-10-13T14:44:18Z",
            }
        ],
    }
    monkeypatch.setattr(
        sync,
        "_object",
        lambda p: policy if p.name == "p10-daily-core-mirror-revisions.v1.json" else original(p),
    )
    first = sync.sync_latest(local_root=local, market_date=day, product_scope="daily-core-v1")
    assert first.changed is True
    receipt = local / f"receipts/{day}/new.json"
    before = receipt.read_bytes()
    assert json.loads(before)["revision_admission"]["old"] == old
    second = sync.sync_latest(local_root=local, market_date=day, product_scope="daily-core-v1")
    assert second.changed is False
    assert receipt.read_bytes() == before
    assert (local / old_rel / "READY").exists()
    assert json.loads((local / "CURRENT_DAILY_CORE.json").read_text()) == new
