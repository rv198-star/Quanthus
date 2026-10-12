import hashlib
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from quanthus.pipelines.securities_warehouse.daily_core_domain import (
    DailyDomainBuildError,
    _base_files,
    _verify_base,
    build_daily_domain_increment,
)
from quanthus.pipelines.securities_warehouse.native_base23_reader import (
    KIND,
    PARENT_TABLES,
    NativeBase23Error,
    read_native_parent_references,
    require_daily_core_parent_admission,
    verify_native_prior_transition,
)


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def seal(root, materialization, manifest, config):
    raw = json.dumps(materialization, sort_keys=True).encode()
    (root / "MATERIALIZATION.json").write_bytes(raw)
    manifest["materialization_sha256"] = digest(raw)
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(raw)
    (root / "READY").write_text(digest(raw) + "\n")
    config["layers"]["dwd"].update(
        manifest_sha256=digest(raw),
        materialization_sha256=manifest["materialization_sha256"],
    )
    (root.parent / "release.json").write_text(json.dumps(config))


@pytest.fixture
def native(tmp_path):
    root = tmp_path / "image"
    root.mkdir()
    layout = {}
    for table in PARENT_TABLES:
        relative = f"payload/{table}/all/part-000000.parquet"
        path = root / relative
        path.parent.mkdir(parents=True)
        raw = (table + " deterministic fixture").encode()
        path.write_bytes(raw)
        layout[table] = {
            "files": [
                {
                    "table": table,
                    "partition": "all",
                    "path": relative,
                    "rows": 1,
                    "size_bytes": len(raw),
                    "sha256": digest(raw),
                }
            ],
            "expected_partition_rows": {"all": 1},
        }
    materialization = {
        "kind": KIND,
        "purpose": "p10_governance_baseline_with_disclosed_coverage",
        "layout": layout,
    }
    manifest = {
        "kind": KIND,
        "version": "2.3",
        "purpose": materialization["purpose"],
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "consumer_admission": "NOT_GRANTED",
        "content_digest": "sha256:" + "b" * 64,
        "summaries": {table: {"all": {"rows": 1}} for table in PARENT_TABLES},
    }
    config = {
        "through_date": "2026-09-30",
        "layers": {
            "dwd": {
                "format": "native_base23",
                "package_id": "native-fixture-2.3",
                "content_digest": manifest["content_digest"],
            }
        },
        "daily_core_parent_reference": {
            "status": "accepted",
            "product_scope": "daily-core-v1",
            "governance_use": "governance_baseline",
            "tables": list(PARENT_TABLES),
            "trading_or_research_admission": False,
            "acceptance_receipts": ["sha256:" + "c" * 64, "sha256:" + "d" * 64],
        },
    }
    seal(root, materialization, manifest, config)
    return root, materialization, manifest, config


def test_native_daily_reader_preserves_native_metadata(native):
    root, _, _, config = native
    before = (root / "manifest.json").read_bytes()
    reader, actual_config = _verify_base(root, root.parent / "release.json")
    assert actual_config == config
    for table in PARENT_TABLES:
        assert len(_base_files(root, reader, table)) == 1
    assert (root / "manifest.json").read_bytes() == before
    assert "files" not in reader.manifest
    assert reader.binding["kind"] == KIND
    assert reader.binding["source_consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("payload", "parent file differs"),
        ("missing", "member is missing"),
        ("materialization", "MATERIALIZATION hash differs"),
        ("ready", "release binding"),
        ("extra", "physical inventory differs"),
        ("symlink", "symlink"),
    ],
)
def test_physical_corruption_is_rejected(native, mutation, match):
    root, materialization, _, config = native
    path = root / materialization["layout"][PARENT_TABLES[0]]["files"][0]["path"]
    if mutation == "payload":
        path.write_bytes(b"corrupt")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "materialization":
        (root / "MATERIALIZATION.json").write_text("{}")
    elif mutation == "ready":
        (root / "READY").write_text("sha256:" + "0" * 64)
    elif mutation == "extra":
        (path.parent / "undeclared.parquet").write_bytes(b"unexpected")
    else:
        original = root.parent / "outside.parquet"
        path.rename(original)
        path.symlink_to(original)
    with pytest.raises(NativeBase23Error, match=match):
        read_native_parent_references(root, config)


def test_sealed_unsafe_path_and_wrong_rows_are_rejected(native):
    root, materialization, manifest, config = native
    original = deepcopy(materialization)
    materialization["layout"][PARENT_TABLES[0]]["files"][0]["path"] = "../outside.parquet"
    seal(root, materialization, manifest, config)
    with pytest.raises(NativeBase23Error, match="unsafe native member"):
        read_native_parent_references(root, config)
    original["layout"][PARENT_TABLES[0]]["files"][0]["rows"] = 2
    seal(root, original, manifest, config)
    with pytest.raises(NativeBase23Error, match="partition counts"):
        read_native_parent_references(root, config)


def test_pending_admission_is_a_separate_gate(native):
    root, _, _, config = native
    config["daily_core_parent_reference"]["status"] = "pending"
    (root.parent / "release.json").write_text(json.dumps(config))
    assert read_native_parent_references(root, config).binding["kind"] == KIND
    with pytest.raises(DailyDomainBuildError, match="acceptance is pending"):
        _verify_base(root, root.parent / "release.json")


def test_duplicate_receipts_do_not_establish_independence(native):
    config = native[3]
    config["daily_core_parent_reference"]["acceptance_receipts"] *= 2
    with pytest.raises(NativeBase23Error, match="acceptance is pending"):
        require_daily_core_parent_admission(config)


def test_legacy_base_still_uses_original_binding(tmp_path):
    manifest = {"kind": "dwd_domain_base_package", "package_id": "legacy", "content_digest": "old"}
    raw = json.dumps(manifest).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    (tmp_path / "READY").write_text(digest(raw))
    config = {"layers": {"dwd": {**manifest, "manifest_sha256": digest(raw)}}}
    (tmp_path / "release.json").write_text(json.dumps(config))
    actual, _ = _verify_base(tmp_path, tmp_path / "release.json")
    assert actual == manifest


def test_exact_october_transition_preserves_prior_identity(native):
    root, _, _, config = native
    reader = read_native_parent_references(root, config)
    prior = {"market_date": "2026-10-09", "base": {"manifest_sha256": "sha256:" + "a" * 64}}
    prior_sha = "sha256:" + "e" * 64
    config["daily_core_transition"] = {
        "accepted_prior_manifests": {
            prior_sha: {
                "market_date": "2026-10-09",
                "base_manifest_sha256": prior["base"]["manifest_sha256"],
            },
        }
    }
    before = deepcopy(prior)
    transition = verify_native_prior_transition(
        native=reader, config=config, prior=prior, prior_sha256=prior_sha
    )
    assert prior == before
    assert transition["prior_base_manifest_sha256"] != transition["new_base_manifest_sha256"]
    with pytest.raises(NativeBase23Error, match="exact versioned transition"):
        verify_native_prior_transition(
            native=reader, config=config, prior=prior, prior_sha256="unknown"
        )
    prior["market_date"] = "2026-09-30"
    with pytest.raises(NativeBase23Error, match="does not follow"):
        verify_native_prior_transition(
            native=reader, config=config, prior=prior, prior_sha256=prior_sha
        )


def test_native_parent_reader_cannot_enable_legacy_minute_product(native):
    root, _, _, _ = native
    output = root.parent / "new-daily"
    with patch(
        "quanthus.pipelines.securities_warehouse.daily_core_domain._read_catalog",
        return_value=({"market_date": "2026-10-08"}, {}),
    ), pytest.raises(DailyDomainBuildError, match="require daily-core-v1"):
        build_daily_domain_increment(
            catalog_root=root.parent,
            release_root=root.parent / "ods",
            base_root=root,
            release_config=root.parent / "release.json",
            output=output,
            code_revision="a" * 40,
        )
    assert not output.exists()
