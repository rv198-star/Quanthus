"""Bounded retries persist independently verified predecessors in ancestry order."""

from datetime import date
from pathlib import Path

import pytest

from quanthus.pipelines import p10_complete_daily_sync as sync
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, object_at


def test_prior_retry_resumes_from_oldest_verified_publication(tmp_path, monkeypatch):
    root = tmp_path / "root"
    stage = root / ".complete-sync-cache-v1"
    day = date(2026, 10, 14)
    original = {"market_date": "2026-10-13", "manifest_sha256": "original"}
    atomic(root / sync.POINTER, original)
    manifests = {}
    pointers = []
    prior = original
    for name in ["first", "second"]:
        content = {"prior": prior}
        sha = digest(encoded(content))
        pointer = {
            "kind": "cn01_complete_daily_dwd_current",
            "market_date": str(day),
            "package_id": name,
            "manifest_path": f"packages/{day}/{name}/manifest.json",
            "manifest_sha256": sha,
        }
        manifests[name] = content
        pointers.append(pointer)
        prior = pointer

    def fetch(*, source, destination, **_):
        name = source.split("/")[-2]
        atomic(destination, manifests[name])

    copies = []
    fail_second = [True]

    def copy(*, pointer, stage, **_):
        name = pointer["package_id"]
        copies.append(name)
        if name == "second" and fail_second[0]:
            raise TimeoutError("simulated remaining service budget")
        path = stage / Path(pointer["manifest_path"]).parent
        path.mkdir(parents=True, exist_ok=True)
        return path, {**pointer, "prior": manifests[name]["prior"]}

    monkeypatch.setattr(sync, "_run_rsync", fetch)
    monkeypatch.setattr(sync, "_copy_immutable", copy)
    monkeypatch.setattr(
        sync,
        "_publish_visible",
        lambda root, package, actual: atomic(
            root / sync.POINTER, pointers[["first", "second"].index(actual["package_id"])]
        ),
    )
    kwargs = dict(root=root, pointer=pointers[1], stage=stage, source="remote", ssh="ssh")
    with pytest.raises(TimeoutError):
        sync._recover_prior(**kwargs, seen=set())
    assert object_at(root / sync.POINTER) == pointers[0]
    assert copies == ["first", "second"]
    fail_second[0] = False
    sync._recover_prior(**kwargs, seen=set())
    assert copies == ["first", "second", "second"]
    assert object_at(root / sync.POINTER) == pointers[1]


def test_wrong_predecessor_manifest_cannot_start_payload_acceptance(tmp_path, monkeypatch):
    root = tmp_path / "root"
    atomic(root / sync.POINTER, {"market_date": "2026-10-13"})
    pointer = {
        "kind": "cn01_complete_daily_dwd_current",
        "market_date": "2026-10-14",
        "package_id": "new",
        "manifest_path": "packages/2026-10-14/new/manifest.json",
        "manifest_sha256": "sha256:" + "a" * 64,
    }
    monkeypatch.setattr(
        sync, "_run_rsync", lambda **kwargs: atomic(kwargs["destination"], {"prior": None})
    )
    monkeypatch.setattr(
        sync,
        "_copy_immutable",
        lambda **_: pytest.fail("payload accepted before manifest identity"),
    )
    with pytest.raises(ValueError, match="predecessor manifest identity"):
        sync._recover_prior(
            root=root, pointer=pointer, stage=root / "cache", source="remote", ssh="ssh", seen=set()
        )
