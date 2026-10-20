from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import native_base23_reader as legacy
from quanthus.pipelines.securities_warehouse import native_base24_reader as successor
from quanthus.pipelines.securities_warehouse.base24_delivery import ONE_MINUTE_DISPOSITION, PURPOSE


def checksum(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def reseal(root, manifest, materialization, config):
    raw = json.dumps(materialization, sort_keys=True).encode()
    (root / "MATERIALIZATION.json").write_bytes(raw)
    manifest["materialization_sha256"] = checksum(raw)
    manifest.pop("content_digest", None)
    manifest["content_digest"] = checksum(json.dumps(manifest, sort_keys=True).encode())
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "READY").write_text(checksum(raw) + "\n")
    config["layers"]["dwd"].update(
        manifest_sha256=checksum(raw),
        content_digest=manifest["content_digest"],
        materialization_sha256=manifest["materialization_sha256"],
    )


def package(tmp_path, version="2.4"):
    root = tmp_path / ("image-" + version)
    root.mkdir()
    kind = successor.KIND if version == "2.4" else legacy.KIND
    purpose = PURPOSE if version == "2.4" else "p10_governance_baseline_with_disclosed_coverage"
    layout = {}
    for table in legacy.PARENT_TABLES:
        relative = f"payload/{table}/all/part-000000.parquet"
        path = root / relative
        path.parent.mkdir(parents=True)
        raw = (table + " synthetic physical-reference bytes").encode()
        path.write_bytes(raw)
        layout[table] = {
            "files": [
                {
                    "table": table,
                    "partition": "all",
                    "path": relative,
                    "rows": 1,
                    "size_bytes": len(raw),
                    "sha256": checksum(raw),
                }
            ],
            "expected_partition_rows": {"all": 1},
        }
    materialization = {
        "kind": kind,
        "purpose": purpose,
        "layout": layout,
        "source_root_archival": "/unreachable/old-base-and-ods",
    }
    manifest = {
        "kind": kind,
        "version": version,
        "purpose": purpose,
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "consumer_admission": "NOT_GRANTED",
        "default_intervals": ["1d", "5m"],
        "one_minute_disposition": ONE_MINUTE_DISPOSITION,
        "summaries": {t: {"all": {"rows": 1}} for t in legacy.PARENT_TABLES},
    }
    config = {
        "through_date": "2026-09-30",
        "layers": {
            "dwd": {
                "format": "native_base" + version.replace(".", ""),
                "package_id": "reference-test",
            }
        },
    }
    reseal(root, manifest, materialization, config)
    return root, manifest, materialization, config


@pytest.fixture
def native(tmp_path):
    return package(tmp_path)


def test_successor_reads_exact_parent_closure_without_admission(native):
    root, manifest, _, config = native
    result = successor.read_native_parent_references(root, config)
    assert result.binding["kind"] == successor.KIND
    assert result.binding["source_consumer_admission"] == "NOT_GRANTED"
    assert result.binding["content_digest"] == manifest["content_digest"]
    assert set(result.table_paths) == set(legacy.PARENT_TABLES)
    assert result.files("dwd_listing") == [root / "payload/dwd_listing/all/part-000000.parquet"]
    with pytest.raises(successor.NativeBase24Error, match="was not verified"):
        result.files("dwd_security_bar")


def test_legacy_reader_keeps_its_exact_23_contract(tmp_path):
    root, _, _, config = package(tmp_path, "2.3")
    assert legacy.read_native_parent_references(root, config).binding["kind"] == legacy.KIND
    with pytest.raises(successor.NativeBase24Error, match="release binding"):
        successor.read_native_parent_references(root, config)


def test_legacy_reader_rejects_24_even_with_exact_pins(native):
    root, _, _, config = native
    with pytest.raises(legacy.NativeBase23Error, match="release binding"):
        legacy.read_native_parent_references(root, config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", legacy.KIND),
        ("version", "2.3"),
        ("purpose", "trading"),
        ("business_through", "2026-10-01"),
        ("business_timezone", "UTC"),
        ("default_intervals", ["1d", "1m", "5m"]),
        ("one_minute_disposition", "REQUIRED"),
    ],
)
def test_successor_rejects_resealed_wrong_profile(native, field, value):
    root, manifest, materialization, config = native
    manifest[field] = value
    reseal(root, manifest, materialization, config)
    with pytest.raises(successor.NativeBase24Error):
        successor.read_native_parent_references(root, config)


@pytest.mark.parametrize(
    "field", ["manifest_sha256", "content_digest", "materialization_sha256", "format"]
)
def test_successor_requires_all_exact_config_pins(native, field):
    root, _, _, config = native
    config["layers"]["dwd"][field] = "changed"
    with pytest.raises(successor.NativeBase24Error, match="release binding"):
        successor.read_native_parent_references(root, config)


def test_changed_selected_bytes_fail(native):
    root, _, _, config = native
    path = root / "payload/dwd_listing/all/part-000000.parquet"
    path.write_bytes(path.read_bytes().replace(b"synthetic", b"different"))
    with pytest.raises(successor.NativeBase24Error, match="file differs"):
        successor.read_native_parent_references(root, config)


def test_missing_selected_file_does_not_fall_back(native):
    root, _, _, config = native
    (root / "payload/dwd_listing/all/part-000000.parquet").unlink()
    with pytest.raises(successor.NativeBase24Error, match="missing"):
        successor.read_native_parent_references(root, config)


@pytest.mark.parametrize(
    "relative",
    [
        "manifest.json",
        "MATERIALIZATION.json",
        "READY",
        "payload/dwd_listing/all/part-000000.parquet",
    ],
)
def test_successor_rejects_mutable_hardlinks(native, tmp_path, relative):
    root, _, _, config = native
    os.link(root / relative, tmp_path / "external-hardlink")
    with pytest.raises(successor.NativeBase24Error, match="independent file"):
        successor.read_native_parent_references(root, config)


def test_successor_rejects_root_symlink(native, tmp_path):
    root, _, _, config = native
    link = tmp_path / "alias"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(successor.NativeBase24Error, match="symlink"):
        successor.read_native_parent_references(link, config)


def test_unlisted_physical_file_is_rejected(native):
    root, _, _, config = native
    (root / "payload/dwd_listing/all/extra.parquet").write_bytes(b"extra")
    with pytest.raises(successor.NativeBase24Error, match="physical inventory"):
        successor.read_native_parent_references(root, config)


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_table", "row_count", "noncanonical"])
def test_resealed_malformed_layout_is_rejected(native, mutation):
    root, manifest, materialization, config = native
    entry = materialization["layout"]["dwd_listing"]
    if mutation == "duplicate":
        entry["files"].append(deepcopy(entry["files"][0]))
    if mutation == "wrong_table":
        entry["files"][0]["table"] = "dwd_security"
    if mutation == "row_count":
        entry["expected_partition_rows"]["all"] = 2
    if mutation == "noncanonical":
        entry["files"][0]["path"] = "payload//dwd_listing/all/part-000000.parquet"
    reseal(root, manifest, materialization, config)
    with pytest.raises(successor.NativeBase24Error):
        successor.read_native_parent_references(root, config)


@pytest.mark.parametrize("tables", [(), ("dwd_listing", "dwd_listing"), ("unknown",)])
def test_invalid_table_closure_is_rejected(native, tables):
    root, _, _, config = native
    with pytest.raises(successor.NativeBase24Error):
        successor.read_native_tables(root, config, tables)


def test_scan_error_fails_closed(native, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base22_sources

    root, _, _, config = native

    def denied(*args, **kwargs):
        raise PermissionError("synthetic directory denial")

    monkeypatch.setattr(base22_sources, "_tree", denied)
    with pytest.raises(successor.NativeBase24Error, match="physical scan failed"):
        successor.read_native_parent_references(root, config)


def test_relocation_keeps_identity_and_never_opens_archival_sources(native, tmp_path, monkeypatch):
    root, _, _, config = native
    first = successor.read_native_parent_references(root, config)
    moved = tmp_path / "relocated"
    shutil.copytree(root, moved)
    shutil.rmtree(root)
    original_open = Path.open

    def confined_open(path, *args, **kwargs):
        assert path.is_relative_to(moved), "read outside the relocated image"
        return original_open(path, *args, **kwargs)

    def no_network(*args, **kwargs):
        pytest.fail("reference reading attempted network access")

    monkeypatch.setattr(Path, "open", confined_open)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    second = successor.read_native_parent_references(moved, config)
    assert second.binding == first.binding
    assert second.files("dwd_listing")[0].is_relative_to(moved)


@pytest.mark.parametrize("changed", ["payload", "manifest"])
def test_mutation_after_hash_is_rejected(native, monkeypatch, changed):
    root, _, _, config = native
    original_hash = legacy._hash
    victim = root / (
        "payload/dwd_listing/all/part-000000.parquet" if changed == "payload" else "manifest.json"
    )
    triggered = False

    def mutate_after_hash(path):
        nonlocal triggered
        value = original_hash(path)
        if not triggered and path.suffix == ".parquet":
            triggered = True
            # Same bytes still change ctime: post-read mutation must not pass.
            victim.write_bytes(victim.read_bytes())
        return value

    monkeypatch.setattr(legacy, "_hash", mutate_after_hash)
    with pytest.raises(successor.NativeBase24Error, match="changed during verification"):
        successor.read_native_parent_references(root, config)


def test_wrong_frequency_is_rejected_before_parent_payload_reads(native, monkeypatch):
    root, manifest, materialization, config = native
    manifest["default_intervals"] = ["1d", "1m", "5m"]
    reseal(root, manifest, materialization, config)

    def no_parent_hash(path):
        pytest.fail("invalid frequency reached a parent payload hash")

    monkeypatch.setattr(legacy, "_hash", no_parent_hash)
    with pytest.raises(successor.NativeBase24Error, match="release binding"):
        successor.read_native_parent_references(root, config)


def test_resealed_and_repinned_later_cutoff_is_not_this_version(native):
    root, manifest, materialization, config = native
    manifest["business_through"] = config["through_date"] = "2026-10-01"
    reseal(root, manifest, materialization, config)
    with pytest.raises(successor.NativeBase24Error, match="business cutoff differs"):
        successor.read_native_parent_references(root, config)
