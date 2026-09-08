from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from quanthus.stage_training_capacity import (
    StageTrainingCapacityError,
    load_capacity_config,
    verify_training_node,
)


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _config(root: Path) -> dict:
    return json.loads((root / "config/stage-learning/training-node.v1.json").read_text())


def test_repository_capacity_config_is_bounded_to_development() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_capacity_config(root / "config/stage-learning/training-node.v1.json")
    assert config["benchmark"]["real_dwd"]["years"] == [2020, 2021]
    assert config["data_source"]["development_read_end"] == "2021-12-31"
    assert config["node"]["gpu_required_for_this_gate"] is False
    assert config["decision"]["cloud_gpu"] == "not_authorized_and_not_required"


def test_post_development_benchmark_year_fails_closed(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = _config(root)
    config["benchmark"]["real_dwd"]["years"] = [2021, 2022]
    config["benchmark"]["real_dwd"]["forbidden_years"] = [2023, 2024, 2025, 2026]
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(StageTrainingCapacityError, match="post-development"):
        load_capacity_config(path)


def _fake_training_tree(tmp_path: Path) -> tuple[dict, Path, Path]:
    repo_root = Path(__file__).resolve().parents[2]
    config = _config(repo_root)
    training = tmp_path / "training"
    source = training / config["data_source"]["source_relative_path"]
    base = source / config["data_source"]["base"]["relative_path"]
    correction = source / config["data_source"]["correction"]["relative_path"]
    base.mkdir(parents=True)
    correction.mkdir(parents=True)
    for name in ("cache", "runs", "models", "logs", "manifests", "tmp"):
        (training / name).mkdir(parents=True)

    base_manifest = {
        "package_id": "dwd-base-202607-v2.1",
        "through_date": "2026-07-31",
        "table_count": 33,
        "payload_bytes": 14447400032,
        "content_digest": config["data_source"]["base"]["content_digest"],
    }
    correction_manifest = {
        "content_digest": config["data_source"]["correction"]["content_digest"]
    }
    (base / "manifest.json").write_text(json.dumps(base_manifest, sort_keys=True))
    (correction / "manifest.json").write_text(json.dumps(correction_manifest, sort_keys=True))
    config["data_source"]["base"]["manifest_sha256"] = _digest(base / "manifest.json")
    config["data_source"]["correction"]["manifest_sha256"] = _digest(
        correction / "manifest.json"
    )
    (base / "READY").write_text(config["data_source"]["base"]["manifest_sha256"] + "\n")
    (correction / "READY").write_text(
        config["data_source"]["correction"]["manifest_sha256"] + "\n"
    )
    for directory, directories, files in os.walk(source):
        for name in [*directories, *files]:
            path = Path(directory) / name
            path.chmod(path.stat().st_mode & ~0o222)
    source.chmod(source.stat().st_mode & ~0o222)
    return config, training, source


def test_preflight_verifies_source_identity_and_read_only_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, training, _ = _fake_training_tree(tmp_path)
    gib = 1024**3
    monkeypatch.setattr(
        "quanthus.stage_training_capacity._resource_snapshot",
        lambda root: {
            "logical_cpus": 12,
            "memory_total_bytes": 41 * gib,
            "memory_available_bytes": 35 * gib,
            "swap_total_bytes": 16 * gib,
            "swap_used_bytes": 0,
            "disk_total_bytes": 1007 * gib,
            "disk_free_bytes": 930 * gib,
            "load_average": [0.0, 0.0, 0.0],
        },
    )
    result = verify_training_node(config, training_root=training)
    assert result["source"]["base"]["content_digest"].startswith("sha256:")
    assert result["resources"]["logical_cpus"] == 12


def test_preflight_rejects_writable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, training, source = _fake_training_tree(tmp_path)
    source.chmod(source.stat().st_mode | 0o200)
    gib = 1024**3
    monkeypatch.setattr(
        "quanthus.stage_training_capacity._resource_snapshot",
        lambda root: {
            "logical_cpus": 12,
            "memory_total_bytes": 41 * gib,
            "memory_available_bytes": 35 * gib,
            "swap_total_bytes": 16 * gib,
            "swap_used_bytes": 0,
            "disk_total_bytes": 1007 * gib,
            "disk_free_bytes": 930 * gib,
            "load_average": [0.0, 0.0, 0.0],
        },
    )
    with pytest.raises(StageTrainingCapacityError, match="writable"):
        verify_training_node(config, training_root=training)
