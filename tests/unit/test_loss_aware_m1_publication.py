"""Source-product publication adversarial file-contract checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quanthus.pipelines.loss_aware_m1_publication import (
    PRODUCT,
    SOURCE_KIND,
    _verify_root_separation,
    verify_source_ods,
)


def _sealed_source_fixture(root: Path) -> Path:
    root.mkdir()
    (root / "raw").mkdir()
    inventory = []
    for index in range(4992):
        member = root / "raw" / f"{index:06}.json.gz"
        data = f"original-source-observation-{index}".encode()
        member.write_bytes(data)
        inventory.append(
            {
                "path": "raw/" + member.name,
                "size_bytes": len(data),
                "compressed_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            }
        )
    grades = root / "source-quality.jsonl"
    grades.write_bytes(b"source-quality-fixture\n")
    inventory.append(
        {
            "path": grades.name,
            "size_bytes": grades.stat().st_size,
            "compressed_sha256": "sha256:" + hashlib.sha256(grades.read_bytes()).hexdigest(),
        }
    )
    manifest = {
        "kind": SOURCE_KIND,
        "schema_version": 1,
        "product_scope": PRODUCT,
        "market_date": "2026-09-24",
        "files": inventory,
    }
    raw = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "SOURCE_EVIDENCE_READY").write_text("sha256:" + hashlib.sha256(raw).hexdigest() + "\n")
    return root


def test_independent_ods_accepts_exact_source_inventory(tmp_path: Path) -> None:
    root = _sealed_source_fixture(tmp_path / "source")
    result = verify_source_ods(root)
    assert result["raw_count"] == 4992
    assert result["manifest_sha256"].startswith("sha256:")


def test_ods_rejects_changed_source_without_manifest_rewrite(tmp_path: Path) -> None:
    root = _sealed_source_fixture(tmp_path / "source")
    (root / "raw" / "000122.json.gz").write_bytes(b"malicious-or-stale-bytes")
    with pytest.raises(ValueError, match="source ODS retained bytes"):
        verify_source_ods(root)


def test_ods_rejects_undeclared_source_member(tmp_path: Path) -> None:
    root = _sealed_source_fixture(tmp_path / "source")
    (root / "raw" / "rogue.json.gz").write_bytes(b"rogue")
    with pytest.raises(ValueError, match="undeclared"):
        verify_source_ods(root)


def test_source_publication_never_reuses_legacy_daily_dwd_root() -> None:
    with pytest.raises(ValueError, match="cannot publish under legacy"):
        _verify_root_separation(Path("/srv/quanthus/dwd-dws-v2/daily/products/daily-core"))
