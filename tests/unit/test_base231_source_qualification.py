import json

import pytest

from quanthus.pipelines.securities_warehouse import base231_source_qualification as adapter
from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget
from quanthus.pipelines.securities_warehouse.base231_domain_pack import build_domain_pack
from tests.unit.test_base231_domain_pack import LIMITS, prepare


def prepared(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    root.mkdir()
    domain = root / "domain"
    with Budget(domain, [], LIMITS) as budget:
        inventory = prepare(domain)
        raw = inventory["raw_members"][0]
        raw.update(original_path="payload/raw_f10.parquet",
                   original_manifest_sha256="sha256:" + "a" * 64)
        catalog = {"records": [{"record_type": "file", "path": raw["original_path"],
                                "sha256": raw["sha256"], "source_revision": raw["source_revision"],
                                "source_manifest_sha256": raw["original_manifest_sha256"]}]}
        (domain / "evidence/SOURCE-CATALOG.json").write_bytes(canonical(catalog))
        metadata = {"layout": {t: {"files": [{"sha256": file_sha(domain / p),
                                             "size_bytes": (domain / p).stat().st_size}
                                            for p in paths]}
                               for t, paths in inventory["parents"].items()}}
        (root / "provenance").mkdir()
        descriptor = root / "provenance/original-dwd-materialization.json"
        descriptor.write_bytes(canonical(metadata))
        (domain / "evidence/original-ods-manifest.json").write_bytes(canonical({"synthetic": True}))
        (domain / "evidence/original-dwd-manifest.json").write_bytes(canonical({
            "materialization_sha256": file_sha(descriptor)}))
        for field, path in [("ODS", "evidence/original-ods-manifest.json"),
                            ("DWD", "evidence/original-dwd-manifest.json"),
                            ("CATALOG", "evidence/SOURCE-CATALOG.json"),
                            ("CONTRACT", inventory["domain_contract"])]:
            monkeypatch.setattr(adapter, field, file_sha(domain / path))
        inventory["files"] = [{"path": str(p.relative_to(domain)), "bytes": p.stat().st_size,
                               "sha256": file_sha(p)} for p in domain.rglob("*") if p.is_file()]
        build_domain_pack(domain, inventory, budget)
    return root


def test_real_binding_is_separate_from_projection_reproduction(tmp_path, monkeypatch):
    root = prepared(tmp_path, monkeypatch)
    receipt = adapter.qualify_retained_inputs(root)
    assert receipt["raw_members"] == 1
    assert not receipt["transaction_equivalence_granted"]
    assert receipt["consumer_admission"] != "GRANTED"


def test_arbitrarily_rehashed_raw_member_is_not_retained_source(tmp_path, monkeypatch):
    root = prepared(tmp_path, monkeypatch)
    inventory_path = root / "domain/INPUTS.json"
    inventory = json.loads(inventory_path.read_bytes())
    inventory["raw_members"][0]["original_manifest_sha256"] = "sha256:" + "b" * 64
    inventory_path.write_bytes(canonical(inventory))
    with pytest.raises(RuntimeError, match="NOT_ORIGINAL_QUALIFIED_SOURCE"):
        adapter.qualify_retained_inputs(root)


def test_rehashed_parent_descriptor_cannot_replace_frozen_parent(tmp_path, monkeypatch):
    root = prepared(tmp_path, monkeypatch)
    (root / "provenance/original-dwd-materialization.json").write_bytes(b"{}")
    with pytest.raises(RuntimeError, match="PARENT_DESCRIPTOR_CHANGED"):
        adapter.qualify_retained_inputs(root)


def test_catalog_body_replacement_is_not_an_accepted_new_revision(tmp_path, monkeypatch):
    root = prepared(tmp_path, monkeypatch)
    (root / "domain/evidence/SOURCE-CATALOG.json").write_bytes(b"{}")
    with pytest.raises(RuntimeError, match="BYTES_CHANGED|AUTHORITY_CHANGED"):
        adapter.qualify_retained_inputs(root)


def test_successor_envelope_replays_all_inner_output_without_source_roots(tmp_path, monkeypatch):
    root = prepared(tmp_path, monkeypatch)
    sealed = adapter.seal_qualified_bundle(root)
    with Budget(tmp_path / "replay", [root], LIMITS) as budget:
        receipt = adapter.audit_qualified_bundle(root, budget)
    assert receipt["identity"] == sealed["identity"]
    assert receipt["status"] == "PASS_REAL_SOURCE_BOUND_DOMAIN_COLD_REPLAY"
    assert not receipt["whole_base_acceptance"]
