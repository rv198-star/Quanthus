"""Independent regression classes from C5's four public-boundary bypasses."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.publish import (
    V1_DS_SCHEMA,
    V2_DS_SCHEMA,
    _package_file_bytes,
    publish_package,
)
from quanthus.product_daily.recommendation.release_contract import (
    CONFIG,
    REGISTRY,
    load_verified_release,
)
from quanthus.product_daily.recommendation.rule_seam import (
    check_three_piece_consistency,
    load_action_receipts,
    load_machine_policy,
    load_release_manifest,
    verify_release_manifest_closure,
)
from tests.unit.test_product_daily_contract_v2 import _held, _publishable_package
from tests.unit.test_product_daily_recommendation import _candidate, _produced

ROOT = find_project_root()
MANIFEST = CONFIG + "release-manifest.v2.json"


def _pieces(root: Path):
    return dict(
        action_receipts=load_action_receipts(root / CONFIG / "action-receipts.v1.json"),
        machine_policy=load_machine_policy(root / CONFIG / "machine-policy.v2.json"),
        release_manifest=load_release_manifest(root / MANIFEST),
    )


def _verify(root: Path) -> None:
    pieces = _pieces(root)
    verify_release_manifest_closure(
        pieces["release_manifest"], project_root=root, pending_is_failure=False
    )
    check_three_piece_consistency(**pieces, project_root=root)


@pytest.fixture
def checkout(tmp_path):
    """Mutating probes have their own Git index and checkout."""
    destination = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(destination)],
        check=True,
    )
    _verify(destination)
    return destination


@pytest.mark.parametrize("wire", [None, V1_DS_SCHEMA, V2_DS_SCHEMA])
def test_current_package_uses_configured_v2_and_cannot_downgrade(tmp_path, wire):
    package = _publishable_package(_held("exit", None))
    called = []

    def forged_loader(raw, **kwargs):
        called.append(raw)
        raise AssertionError("pending pin must precede any caller loader")

    # The registered v2 release resolves its own pinned loader: a forged caller
    # loader is never invoked, and a caller wire_version cannot downgrade the
    # configured release (v1 assertion mismatches the v2 release).
    if wire == V1_DS_SCHEMA:
        with pytest.raises(ProductDailyError, match="version_inconsistent"):
            publish_package(
                package,
                directory=tmp_path / "published",
                published_at=package.decision_sets[0].published_at,
                wire_version=wire,
                p12_loader=forged_loader,
            )
        assert not (tmp_path / "published").exists()
    else:
        receipt = publish_package(
            package,
            directory=tmp_path / "published",
            published_at=package.decision_sets[0].published_at,
            wire_version=wire,
            p12_loader=forged_loader,
        )
        assert receipt.p12_package_digests is not None
    assert called == []


def test_registered_legacy_fixture_keeps_exact_v1_bytes(tmp_path):
    package = _produced((_candidate(),)).package
    assert package is not None
    receipt = publish_package(
        package, directory=tmp_path, published_at=package.decision_sets[0].published_at
    )
    assert Path(receipt.path).read_bytes() == _package_file_bytes(
        package, wire_version=V1_DS_SCHEMA
    )
    with pytest.raises(ProductDailyError, match="configured release"):
        publish_package(
            package,
            directory=tmp_path / "wrong-wire",
            published_at=package.decision_sets[0].published_at,
            wire_version=V2_DS_SCHEMA,
        )
    assert not (tmp_path / "wrong-wire").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_role",
        "wrong_existing_role",
        "absent_role",
        "missing_strategy",
        "missing_feature",
        "missing_expert",
        "missing_consumer",
        "nonexistent_consumer",
        "missing_policy",
        "missing_receipts",
        "symbolic_commit",
        "short_commit",
        "short_tree",
        "bound_industry_without_sha",
        "missing_release_binding",
    ],
)
def test_every_required_manifest_binding_is_mandatory(mutation):
    pieces = _pieces(ROOT)
    manifest = deepcopy(pieces["release_manifest"])
    code = manifest["code_entry"]
    if mutation == "empty_role":
        code["publish_path"] = ""
    elif mutation == "wrong_existing_role":
        code["publish_path"] = "src/quanthus/policy.py"
    elif mutation == "absent_role":
        code["publish_path"] = "src/quanthus/not-present.py"
    elif mutation == "missing_strategy":
        manifest.pop("strategy")
    elif mutation == "missing_feature":
        manifest["input_closure"]["feature_registry"] = {}
    elif mutation == "missing_expert":
        manifest.pop("expert_binding")
    elif mutation == "missing_consumer":
        manifest["input_closure"].pop("consumer_binding_v2")
    elif mutation == "nonexistent_consumer":
        manifest["input_closure"]["consumer_binding_v2"] = "config/missing.json"
        manifest["release_bindings"]["consumer"]["path"] = "config/missing.json"
    elif mutation == "missing_policy":
        manifest["release_bindings"].pop("machine_policy")
    elif mutation == "missing_receipts":
        manifest["release_bindings"]["action_receipts"]["sha256"] = None
    elif mutation == "symbolic_commit":
        code["code_commit"] = "HEAD"
    elif mutation == "short_commit":
        code["code_commit"] = code["code_commit"][:8]
    elif mutation == "short_tree":
        code["code_tree"] = code["code_tree"][:8]
    elif mutation == "bound_industry_without_sha":
        manifest["input_closure"]["industry_mapping"]["binding_state"] = "bound"
    elif mutation == "missing_release_binding":
        manifest.pop("release_bindings")
    with pytest.raises(ProductDailyError):
        verify_release_manifest_closure(manifest, project_root=ROOT, pending_is_failure=False)
    with pytest.raises(ProductDailyError):
        check_three_piece_consistency(**{**pieces, "release_manifest": manifest}, project_root=ROOT)


@pytest.mark.parametrize("mutation", ["unknown_dependency", "invented_receipt", "empty_receipts"])
def test_composition_binds_supplied_objects_to_strict_loaded_files(mutation):
    pieces = deepcopy(_pieces(ROOT))
    if mutation == "unknown_dependency":
        pieces["machine_policy"]["conditions"][0]["depends_on"] = ["unknown-source"]
    elif mutation == "invented_receipt":
        pieces["action_receipts"] = ({"action": "invented", "adoption_state": "proposed"},)
    else:
        pieces["action_receipts"] = ()
    with pytest.raises(ProductDailyError, match="objects differ"):
        check_three_piece_consistency(**pieces, project_root=ROOT)


@pytest.mark.parametrize("surface", ["hidden_staged_blob", "staged_only_addition", "untracked"])
def test_all_git_surfaces_are_checked(checkout, surface):
    if surface == "hidden_staged_blob":
        rel = "src/quanthus/policy.py"
        path = checkout / rel
        original = path.read_bytes()
        path.write_bytes(original + b"\n# index-only semantic drift\n")
        subprocess.run(["git", "add", rel], cwd=checkout, check=True)
        path.write_bytes(original)
    else:
        rel = "tests/unit/new_semantic_file.py"
        path = checkout / rel
        path.write_bytes(b"# semantic addition\n")
        if surface == "staged_only_addition":
            subprocess.run(["git", "add", rel], cwd=checkout, check=True)
            path.unlink()
    with pytest.raises(ProductDailyError, match="drifted"):
        _verify(checkout)


def test_on_disk_self_manifest_cannot_change_release_semantics(checkout):
    path = checkout / MANIFEST
    raw = json.loads(path.read_bytes())
    raw["release_bindings"]["action_receipts"]["path"] = "config/missing.json"
    path.write_text(json.dumps(raw))
    subprocess.run(["git", "add", MANIFEST], cwd=checkout, check=True)
    with pytest.raises(ProductDailyError, match="self manifest semantic drift"):
        _verify(checkout)


def test_registry_route_edit_cannot_choose_another_wire(checkout):
    path = checkout / REGISTRY
    raw = json.loads(path.read_bytes())
    raw["releases"][0]["manifest_path"] = CONFIG + "release-manifest.g1-compat.v2.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ProductDailyError):
        _verify(checkout)


def test_runtime_planning_config_has_no_directory_wide_exemption(checkout):
    path = checkout / "config/planning/r7-multiop-delivery-request.v1.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ProductDailyError, match="drifted"):
        _verify(checkout)


def test_clean_release_is_consistent_pending_not_ready():
    _verify(ROOT)
    manifest = _pieces(ROOT)["release_manifest"]
    with pytest.raises(ProductDailyError, match="pending/blocked"):
        verify_release_manifest_closure(manifest, project_root=ROOT)
    verified = load_verified_release(ROOT, manifest["manifest_id"])
    assert verified.wire_version == V2_DS_SCHEMA
