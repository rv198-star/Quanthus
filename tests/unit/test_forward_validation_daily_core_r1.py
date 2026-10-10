"""P6-R1 new product is opt-in, source-bound and fails closed at the legacy cutover."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import quanthus.forward_validation.prospective_source as source
from quanthus.forward_validation.activation import load_prospective_runtime_config
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.data_boundary import (
    ProspectiveDwdRelease,
    load_visibility_receipt,
)
from quanthus.forward_validation.serialization import digest_value
from quanthus.pipelines.securities_warehouse.daily_domain import DailyDomainResult

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
R1 = ROOT / "config/forward-validation/activation/p6-prospective-runtime-r1.v1.json"


def test_r1_daily_core_runtime_additive_and_original_frozen_runtime_unchanged() -> None:
    legacy = load_prospective_runtime_config(BASELINE, root=ROOT)
    r1 = load_prospective_runtime_config(R1, root=ROOT)
    assert legacy.daily_product_scope == "legacy-four-lane"
    assert legacy.legacy_daily_root is None
    assert r1.daily_product_scope == "daily-core-v1"
    assert r1.daily_core_first_date == date(2026, 9, 24)
    assert r1.legacy_bridge_market_date == date(2026, 9, 23)
    assert legacy.config_sha256 != r1.config_sha256
    assert r1.bindings["strategy_card"] == legacy.bindings["strategy_card"]
    assert r1.bindings["entry_proposal"] == legacy.bindings["entry_proposal"]


def test_r1_refuses_unqualified_cutover_predecessor_before_exposing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(R1, root=ROOT)
    core = tmp_path / "core"
    legacy = tmp_path / "legacy"
    package_id = "dwd-daily-20260924-aaaaaaaaaaaa-bbbbbbb"
    pkg = core / "packages/2026-09-24/aaaaaaaaaaaa-bbbbbbb"
    pkg.mkdir(parents=True)
    bad_prior_sha = "sha256:" + "b" * 64
    (pkg / "manifest.json").write_text(
        json.dumps(
            {
                "kind": "cn01_daily_core_dwd_increment",
                "code_revision": "a" * 40,
                "market_date": "2026-09-24",
                "package_id": package_id,
                "base": {
                    "package_id": "dwd-base-202607-v2.1",
                    "manifest_sha256": runtime.historical_base_manifest_sha256,
                    "content_digest": runtime.historical_base_content_digest,
                },
                "covered_after_date": "2026-09-23",
                "prior": {
                    "market_date": "2026-09-23",
                    "package_id": runtime.legacy_bridge_package_id,
                    "manifest_sha256": bad_prior_sha,
                },
            }
        )
    )
    monkeypatch.setattr(source, "_verify_seed", lambda _config: None)
    monkeypatch.setattr(
        source,
        "verify_daily_core_increment",
        lambda _root: DailyDomainResult(
            root=pkg,
            package_id=package_id,
            market_date=date(2026, 9, 24),
            manifest_sha256="sha256:" + "7" * 64,
            content_digest="sha256:" + "8" * 64,
            row_counts={},
            payload_bytes=1,
        ),
    )
    runtime = replace(runtime, daily_root=core, legacy_daily_root=legacy)
    semantic = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": package_id,
        "market_date": "2026-09-24",
        "manifest_sha256": "sha256:" + "7" * 64,
        "content_digest": "sha256:" + "8" * 64,
        "ready_observed_at": "2026-09-24T12:00:00+00:00",
        "consumer_visible_at": "2026-09-24T12:00:00+00:00",
        "source_node": "test",
    }
    receipt = load_visibility_receipt({**semantic, "receipt_digest": digest_value(semantic)})
    target = ProspectiveDwdRelease(
        package_root=pkg,
        package_id=package_id,
        market_date=date(2026, 9, 24),
        manifest_sha256=semantic["manifest_sha256"],
        content_digest=semantic["content_digest"],
        code_revision="a" * 40,
        row_counts={},
        payload_bytes=1,
        visibility=receipt,
    )
    with pytest.raises(ForwardValidationContractError, match="p6_daily_core_bridge_drift"):
        source.load_daily_chain(config=runtime, target=target)
