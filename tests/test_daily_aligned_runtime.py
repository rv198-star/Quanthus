import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.daily_aligned_runtime import (
    copy_tree,
    cycle_configuration,
    publish_aligned,
)


def test_source_copies_are_independent_and_detect_reuse_corruption(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "receipt").write_bytes(b"original")
    copy_tree(source, target)
    assert (target / "receipt").read_bytes() == b"original"
    assert (target / "receipt").stat().st_ino != (source / "receipt").stat().st_ino
    copy_tree(source, target)
    (target / "receipt").write_bytes(b"modified")
    with pytest.raises(ValueError, match="COPY_CHANGED"):
        copy_tree(source, target)


def test_publication_requires_complete_verified_date_closure(tmp_path):
    (tmp_path / "VERIFIED.json").write_text(
        json.dumps(
            {
                "status": "VERIFIED",
                "request_identity": "identity",
                "base": {},
                "days": [{"market_date": "2026-11-01"}],
            }
        )
    )
    (tmp_path / "PLAN.json").write_text(
        json.dumps(
            {
                "identity": "identity",
                "base": {},
                "entries": [],
            }
        )
    )
    with pytest.raises(ValueError, match="VERIFIED_CLOSURE_REQUIRED"):
        publish_aligned(tmp_path)
    assert not (tmp_path / "DATES.json").exists()


def test_configuration_replaces_old_base_and_bootstrap(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    gov = tmp_path / "governance.json"
    gov.write_text('{"actual":"governance"}')
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "release.json").write_text("{}")
    from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha

    (root / "RUNTIME-READY.json").write_text(
        json.dumps(
            {
                "status": "VERIFIED",
                "base": {"manifest_sha256": "sealed"},
                "days": 0,
                "calendar_reference": {
                    "path": str(reference),
                    "manifest_sha256": file_sha(reference / "release.json"),
                },
            }
        )
    )
    (root / "PLAN.json").write_text(
        json.dumps(
            {
                "base": {"manifest_sha256": "sealed"},
                "base_root": str(tmp_path / "Base"),
                "governance": {"actual": "governance"},
            }
        )
    )
    value = cycle_configuration(root, {"bootstrap_cores": ["old"], "base": "old"}, "a" * 40, gov)
    assert value["base"] == str(tmp_path / "Base")
    assert value["bootstrap_cores"] == []
    assert value["standard_delivery"] is True
    assert value["prior_anchor"] == str(tmp_path)
    assert value["execution_authorized"] is False


def test_delivery_manifest_path_is_resolved_to_source_directory(tmp_path):
    from quanthus.pipelines.securities_warehouse.daily_aligned_runtime import delivery_source

    release = tmp_path / "sources/calendar_reference"
    release.mkdir(parents=True)
    (release / "release.json").write_text("{}")
    body = {"sources": {"calendar_reference": {"path": "sources/calendar_reference/release.json"}}}
    assert delivery_source(tmp_path, body, "calendar_reference") == release


def test_empty_rollover_retains_reference_independently(tmp_path):
    from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha

    source, root = tmp_path / "source", tmp_path / "new-runtime"
    source.mkdir()
    root.mkdir()
    (source / "release.json").write_text("{}")
    (source / "calendar.parquet").write_bytes(b"captured-calendar")
    plan = {
        "base": {"manifest_sha256": "sealed"},
        "identity": "identity",
        "entries": [],
        "calendar_reference": {
            "path": str(source),
            "manifest_sha256": file_sha(source / "release.json"),
        },
    }
    (root / "PLAN.json").write_text(json.dumps(plan))
    (root / "VERIFIED.json").write_text(
        json.dumps(
            {
                "status": "VERIFIED",
                "request_identity": "identity",
                "base": plan["base"],
                "days": [],
                "through": "2026-11-06",
            }
        )
    )
    result = publish_aligned(root)
    retained = Path(result["calendar_reference"]["path"])
    assert retained == root / "calendar-reference"
    assert (retained / "calendar.parquet").read_bytes() == b"captured-calendar"
    assert (retained / "calendar.parquet").stat().st_ino != (
        source / "calendar.parquet"
    ).stat().st_ino
