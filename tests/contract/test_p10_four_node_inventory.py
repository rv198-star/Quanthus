"""Four-node audit respects real role differences and provenance SHA."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quanthus.pipelines.p10_four_node_inventory import (
    DAYS,
    compare_four_nodes,
    daily_snapshot,
    minute_research_snapshot,
)


def _core(root: Path, *, hash_source: str, last_date: str = "2026-10-08") -> dict:
    for day in DAYS:
        dest = root / "packages" / day / "fixed-id"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(
            json.dumps({"fake_fixture_date": day, "value": hash_source})
        )
    current = root / "packages" / last_date / "fixed-id" / "manifest.json"
    digest = "sha256:" + hashlib.sha256(current.read_bytes()).hexdigest()
    pointer = {
        "kind": "cn01_daily_core_dwd_current",
        "market_date": last_date,
        "manifest_path": f"packages/{last_date}/fixed-id/manifest.json",
        "manifest_sha256": digest,
    }
    (root / "CURRENT_DAILY_CORE.json").write_text(json.dumps(pointer))
    return daily_snapshot(root=root)


def test_all_four_node_records_do_not_conflate_cn02_with_prod(tmp_path: Path) -> None:
    cn01 = _core(tmp_path / "cn01", hash_source="CN01")
    oci = _core(tmp_path / "oci", hash_source="CN01")
    hermes = _core(tmp_path / "hermes", hash_source="HERMES")
    distinct_hermes_digest = (
        hermes["current_manifest_sha256"] != cn01["current_manifest_sha256"]
    )
    assert distinct_hermes_digest
    outcomes = compare_four_nodes(
        [
            {"node": "CN01", "daily_core": cn01},
            {"node": "Hermes", "daily_core": hermes},
            {"node": "OCI", "daily_core": oci},
            {"node": "CN02", "CN02_isolated_candidate": {
                "/fake/cn02/base": {"exists": True, "is_dir": True},
            }},
        ]
    )
    assert outcomes["core_reconciliation"]["OCI_exact_CN01_mirror"] == "PASS"
    assert outcomes["core_reconciliation"]["CN02"].startswith("ISOLATED_BASE")
    assert outcomes["latest_core_pointers_and_package_presence_qualified"]
    assert not outcomes["all_market_dates_deep_verified"]
    assert not outcomes["full_four_node_production_mirror_asserted"]


def test_stale_or_wrong_oci_manifest_is_not_declared_mirrored(tmp_path: Path) -> None:
    cn01 = _core(tmp_path / "cn01", hash_source="CN01")
    oci = _core(tmp_path / "oci", hash_source="OCI_different")
    hermes = _core(tmp_path / "hermes", hash_source="HERMES")
    states = [
        {"node": "CN01", "daily_core": cn01},
        {"node": "Hermes", "daily_core": hermes},
        {"node": "OCI", "daily_core": oci},
        {"node": "CN02", "CN02_isolated_candidate": {}},
    ]
    status = compare_four_nodes(states)
    assert status["core_reconciliation"]["OCI_exact_CN01_mirror"] == "NOT_VERIFIED"
    assert not status["latest_core_pointers_and_package_presence_qualified"]


def test_tampered_current_pointer_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "cn01"
    _core(root, hash_source="CN01")
    manifest = root / "packages" / "2026-10-08" / "fixed-id" / "manifest.json"
    manifest.write_text("changed")
    with pytest.raises(ValueError, match="manifest SHA"):
        daily_snapshot(root=root)


def test_missing_historical_package_is_not_claimed_qualified(tmp_path: Path) -> None:
    root = tmp_path / "cn01"
    _core(root, hash_source="CN01")
    (root / "packages" / "2026-09-28" / "fixed-id" / "manifest.json").unlink()
    snap = daily_snapshot(root=root)
    assert snap["dates_with_missing_package"] == ["2026-09-28"]
    assert snap["pointer_verified"]


def test_missing_minute_data_does_not_invent_ready(tmp_path: Path) -> None:
    minute = minute_research_snapshot(tmp_path / "not-present")
    assert minute["product_exists"] is False
    assert minute["research_current_date"] is None
    assert all(x["status"] == "not_published" for x in minute["per_date"].values())


def test_four_node_inputs_must_be_distinct_not_three_producers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="four genuinely distinct"):
        compare_four_nodes(
            [{"node": "CN01", "daily_core": _core(tmp_path / "cn01", hash_source="CN01")}]
        )


def test_manifest_presence_is_not_deep_package_qualification(tmp_path: Path) -> None:
    root = tmp_path / "not-real-package"
    _core(root, hash_source="fixture-only")
    evidence = daily_snapshot(root=root, deep_verify=True)
    assert not evidence["deep_verified_market_dates"]
    assert set(evidence["deep_payload_verification_failures"]) == set(DAYS)
