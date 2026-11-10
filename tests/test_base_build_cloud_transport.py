from __future__ import annotations

import shutil
import time

import pytest

from quanthus.p10_gdrive_backup import pack
from quanthus.pipelines.securities_warehouse.base_build_inputs import (
    file_sha,
    inventory_evidence,
)
from quanthus.pipelines.securities_warehouse.base_build_recovery import recover_path_stream


def calendar_evidence(root):
    root.mkdir()
    for name in (
        "BINDING.json",
        "parent-core-manifest.json",
        "parent-daily-manifest.json",
        "reference-candidate.parquet",
        "reference-source.parquet",
        "source-release.json",
    ):
        (root / name).write_bytes(("original retained bytes: " + name).encode())
    return [
        {"path": p.name, "size_bytes": p.stat().st_size, "sha256": file_sha(p)}
        for p in sorted(root.iterdir())
    ]


def package(root, files):
    return {
        "root": str(root),
        "files": {
            r["path"]: {"bytes": r["size_bytes"], "sha256": r["sha256"][7:]}
            for r in files
        },
    }


def test_cloud_archive_recovers_complete_frozen_calendar_without_original_node(tmp_path):
    source = tmp_path / "original"
    files = calendar_evidence(source)
    expected = inventory_evidence(source, files)
    archive = tmp_path / "complete.tar"
    pack(package(source, files), archive, time.monotonic() + 60)
    shutil.rmtree(source)
    restored = tmp_path / "restored"
    with archive.open("rb") as stream:
        receipt = recover_path_stream(
            stream,
            {"contract": "p10-base-selected-regular-recovery-v1", "members": files},
            restored,
        )
    assert receipt["READY"] is False
    assert inventory_evidence(restored, files) == expected


def test_source_only_subset_cannot_be_published_as_complete_frozen_evidence(tmp_path):
    source = tmp_path / "original"
    files = calendar_evidence(source)
    subset = [r for r in files if r["path"] in {"BINDING.json", "reference-source.parquet",
                                              "source-release.json"}]
    with pytest.raises(ValueError, match="P10_BACKUP_SOURCE_SET_CHANGED_DURING_PACK"):
        pack(package(source, subset), tmp_path / "failed-retained.tar", time.monotonic() + 60)
