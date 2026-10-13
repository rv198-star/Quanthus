"""A revised day uses its exact DWD-bound ODS pair, retaining old sealed sources."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines import p10_complete_daily_cycle as cycle
from quanthus.pipelines.p10_delivery_common import atomic, digest, file_digest
from quanthus.pipelines.securities_warehouse import daily_core_domain as native_daily


def binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = date(2026, 10, 13)
    releases = tmp_path / "releases"
    selected = {}
    expected_paths = {}
    objects = {}
    for lane, prefix in (
        ("market_daily_core", "hermes-market-daily-core-"),
        ("calendar_daily_events", "hermes-calendar-daily-events-"),
    ):
        for version in ("old", "bound", "newer"):
            path = releases / str(target) / (lane + version)
            marker = path / "release.json"
            identity = {"release_id": prefix + version}
            atomic(marker, identity)
            sha = file_digest(marker)
            objects[path] = SimpleNamespace(
                manifest_sha256=sha,
                release=SimpleNamespace(
                    release_id=identity["release_id"], market_date=target, schedule="daily"
                ),
            )
            if version == "bound":
                selected[lane] = {**identity, "manifest_sha256": sha}
                expected_paths[lane] = path
    monkeypatch.setattr(cycle, "verify_ods_release", lambda path: objects[path])
    daily = tmp_path / "daily"
    package = daily / "packages" / str(target) / "bound"
    payload = package / "payload" / "witness.bin"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"native file inventory")
    files = [
        {
            "path": "payload/witness.bin",
            "size_bytes": payload.stat().st_size,
            "sha256": file_digest(payload),
        }
    ]
    # This test exercises the real native physical seal; table projections are
    # independently covered by the existing complete-Daily member fixtures.
    monkeypatch.setattr(native_daily, "_verify_daily_core_payload", lambda *_: None)
    manifest = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_increment",
        "package_id": "native-bound-day",
        "market_date": str(target),
        "product_scope": "daily-core-v1",
        "consumable_intervals": ["1d"],
        "quality_policy": "p10-business-value-warehouse-quality.v1",
        "quality_policy_sha256": native_daily._daily_core_quality_sha256(),
        "unavailable_products": {
            "intraday_1m": "not_part_of_daily_core_not_qualified_as_daily",
            "intraday_5m": "not_part_of_daily_core_see_separate_product_status",
        },
        "row_counts": {},
        "selected_releases": selected,
        "files": files,
        "content_digest": digest(native_daily._canonical_bytes(files)),
    }
    atomic(package / "manifest.json", manifest)
    sha = file_digest(package / "manifest.json")
    (package / "READY").write_text(sha + "\n")
    atomic(
        daily / "date-pointers" / f"{target}.json",
        {
            "kind": "cn01_daily_core_dwd_current",
            "market_date": str(target),
            "manifest_path": str((package / "manifest.json").relative_to(daily)),
            "manifest_sha256": sha,
            "package_id": manifest["package_id"],
            "content_digest": manifest["content_digest"],
        },
    )
    return releases, daily, target, package, selected, expected_paths


def test_exact_pair_wins_over_older_and_newer_sealed_revisions(tmp_path, monkeypatch):
    releases, daily, target, _, selected, paths = binding(tmp_path, monkeypatch)
    market, events_sha = cycle.sealed_daily_sources(releases, target, daily=daily)
    assert market == paths["market_daily_core"]
    assert events_sha == selected["calendar_daily_events"]["manifest_sha256"]


def test_missing_bound_source_cannot_fall_back_to_another_revision(tmp_path, monkeypatch):
    releases, daily, target, _, _, paths = binding(tmp_path, monkeypatch)
    (paths["market_daily_core"] / "release.json").unlink()
    with pytest.raises(ValueError, match="exact daily-bound ODS source absent"):
        cycle.sealed_daily_sources(releases, target, daily=daily)


def test_unsealed_daily_binding_cannot_resolve_ambiguity(tmp_path, monkeypatch):
    releases, daily, target, package, _, _ = binding(tmp_path, monkeypatch)
    (package / "READY").write_text("sha256:" + "0" * 64 + "\n")
    with pytest.raises(ValueError, match="READY differs"):
        cycle.sealed_daily_sources(releases, target, daily=daily)


def test_native_nonempty_file_size_and_hash_are_required(tmp_path, monkeypatch):
    releases, daily, target, package, _, _ = binding(tmp_path, monkeypatch)
    (package / "payload/witness.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="file differs"):
        cycle.sealed_daily_sources(releases, target, daily=daily)


def test_pointer_content_identity_cannot_differ_from_native_manifest(tmp_path, monkeypatch):
    import json

    releases, daily, target, _, _, _ = binding(tmp_path, monkeypatch)
    path = daily / "date-pointers" / f"{target}.json"
    pointer = json.loads(path.read_text())
    pointer["content_digest"] = "sha256:" + "0" * 64
    atomic(path, pointer)
    with pytest.raises(ValueError, match="binding date or content identity differs"):
        cycle.sealed_daily_sources(releases, target, daily=daily)
