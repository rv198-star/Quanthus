"""Native input authority fails closed independently of source-image admission."""

import json
from dataclasses import replace
from datetime import date

import pytest

from quanthus.backtest import native_dwd_source as native
from quanthus.backtest.data_provider import load_replay_data_provider_config
from quanthus.event_library.source import (
    EventScanSourceError,
    event_scan_release_path,
    load_event_scan_source_policy,
)
from quanthus.policy import ConfigurationError, find_project_root

ROOT = find_project_root()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


@pytest.fixture
def source(tmp_path):
    image = tmp_path / "native"
    layout = {}
    for table in native.TABLES:
        relative = f"payload/{table}/all/part-000000.parquet"
        path = image / relative
        path.parent.mkdir(parents=True)
        path.write_bytes((table + " source evidence").encode())
        layout[table] = {
            "files": [
                {
                    "table": table,
                    "partition": "all",
                    "path": relative,
                    "rows": 1,
                    "size_bytes": path.stat().st_size,
                    "sha256": native.digest(path),
                }
            ],
            "expected_partition_rows": {"all": 1},
        }
    materialization = {
        "kind": native.KIND,
        "purpose": "p10_governance_baseline_with_disclosed_coverage",
        "layout": layout,
    }
    write(image / "MATERIALIZATION.json", materialization)
    manifest = {
        "kind": native.KIND,
        "version": "2.3",
        "purpose": materialization["purpose"],
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "consumer_admission": "NOT_GRANTED",
        "content_digest": "sha256:" + "a" * 64,
        "materialization_sha256": native.digest(image / "MATERIALIZATION.json"),
        "summaries": {t: {"all": {"rows": 1}} for t in native.TABLES},
    }
    write(image / "manifest.json", manifest)
    (image / "READY").write_text(native.digest(image / "manifest.json") + "\n")
    correction = tmp_path / "attachment" / "legacy"
    member = correction / "payload/state.parquet"
    member.parent.mkdir(parents=True)
    member.write_bytes(b"exact original correction")
    old = {
        "correction_id": "legacy-correction",
        "content_digest": "sha256:" + "b" * 64,
        "parent": {"package_id": "legacy-parent"},
        "files": [
            {
                "path": "payload/state.parquet",
                "size_bytes": member.stat().st_size,
                "sha256": native.digest(member),
            }
        ],
    }
    write(correction / "manifest.json", old)
    (correction / "READY").write_text(native.digest(correction / "manifest.json"))
    correction_identity = {k: old[k] for k in ("correction_id", "content_digest")}
    correction_identity["manifest_sha256"] = native.digest(correction / "manifest.json")
    state = layout["dwd_tradability_state"]["files"][0]
    proof = {
        "status": "COMPARISON_COMPLETE_NOT_CONSUMER_ADMISSION",
        "state_total": {
            "rows": 1,
            "native_equals_old_preimage": 1,
            "missing_native_key": 0,
            "missing_old_key": 0,
            "native_differs_from_both": 0,
        },
        "state_byte_equivalence": {
            "mapping": [
                {
                    "native_path": state["path"],
                    "bytes": state["size_bytes"],
                    "sha256": state["sha256"][7:],
                }
            ]
        },
    }
    write(correction.parent / "proof.json", proof)
    receipt = {
        "schema_version": "quanthus.native-correction-continuation.v1",
        "purpose": "development_retrospective_input_v1",
        "native_manifest_sha256": native.digest(image / "manifest.json"),
        "native_materialization_sha256": manifest["materialization_sha256"],
        "legacy_correction": correction_identity,
        "legacy_parent": old["parent"],
        "normalization_bytes_unchanged": True,
        "raw_action_term_delete_overlay_applied": False,
        "state_preimage_proof_path": "proof.json",
        "state_preimage_proof_sha256": native.digest(correction.parent / "proof.json"),
        "state_replacement_rows": 1,
    }
    write(correction.parent / "continuation.json", receipt)
    config_raw = json.loads((ROOT / "config/replay-data-provider.base23.v1.json").read_text())
    config_raw["dwd_authority"].update(
        root=str(image),
        root_env="TEST_NATIVE_ROOT",
        manifest_sha256=receipt["native_manifest_sha256"],
        content_digest=manifest["content_digest"],
    )
    config_raw["dwd_correction"].update(
        correction_identity, root=str(correction), root_env="TEST_CORR_ROOT"
    )
    options = config_raw["materialization"]["native_base23"]
    options["release"]["layers"]["dwd"].update(
        manifest_sha256=receipt["native_manifest_sha256"],
        content_digest=manifest["content_digest"],
        materialization_sha256=manifest["materialization_sha256"],
    )
    options["continuation"] = {
        "path": "continuation.json",
        "sha256": native.digest(correction.parent / "continuation.json"),
    }
    reference = tmp_path / "reference.json"
    reference.write_text("[]")
    for item in config_raw["research_references"].values():
        item.update(path=str(reference), sha256=native.digest(reference))
    path = tmp_path / "provider.json"
    write(path, config_raw)
    return load_replay_data_provider_config(path)


def test_native_verified_source_preserves_both_sealed_metadata(source):
    before = (source.dwd_root / "manifest.json").read_bytes()
    result, correction, manifest = native.verify_source(source)
    assert set(result.table_paths) == set(native.TABLES)
    assert correction == source.correction_identity
    assert manifest["parent"] == {"package_id": "legacy-parent"}
    assert result.binding["source_consumer_admission"] == "NOT_GRANTED"
    assert (source.dwd_root / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["native", "correction", "continuation", "proof", "extra"])
def test_corrupt_native_or_continuation_fails_closed(source, fault):
    path = {
        "native": source.dwd_root / "payload/dwd_security_bar/all/part-000000.parquet",
        "correction": source.correction_root / "payload/state.parquet",
        "continuation": source.correction_root.parent / "continuation.json",
        "proof": source.correction_root.parent / "proof.json",
        "extra": source.dwd_root / "payload/dwd_security_bar/all/unlisted.parquet",
    }[fault]
    path.write_bytes(b"damaged")
    with pytest.raises(ConfigurationError):
        native.verify_source(source)


def test_protected_scope_is_not_granted_by_native_source(source):
    scope = replace(source.scope, decision_end=date(2022, 1, 4), execution_end=date(2022, 1, 5))
    with pytest.raises(ConfigurationError, match="purpose or visibility"):
        native.verify_source(replace(source, scope=scope))


def test_old_canonical_receipt_cannot_admit_native(source, tmp_path, monkeypatch):
    monkeypatch.setattr(native, "find_project_root", lambda *_: tmp_path)
    receipt = json.loads((ROOT / "config/p4-direct-dwd-canonical-projection.v1.json").read_text())
    write(tmp_path / native.CANONICAL_RECEIPT, receipt)
    with pytest.raises(ConfigurationError, match="admission binding"):
        native.require_canonical_admission(source)


def test_native_event_policy_uses_pinned_successor_release():
    policy_path = ROOT / "config/p4-event-scan-source.base23.v1.json"
    release = event_scan_release_path(policy_path)
    policy = load_event_scan_source_policy(
        policy_path,
        release_path=release,
        provider_path=ROOT / "config/replay-data-provider.base23.v1.json",
    )
    assert policy.base_release.package_id == "dwd-base-202609-v2.3"
    assert release.name == "base23-oci-consumers.v1.json"
    with pytest.raises(EventScanSourceError, match="release authority"):
        load_event_scan_source_policy(
            policy_path, release_path=ROOT / "config/releases/v2-build202607.json"
        )
