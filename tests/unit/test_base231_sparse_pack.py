import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base231_sparse_pack as adapter
from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget
from quanthus.pipelines.securities_warehouse.base231_f10_selection import select_metadata
from tests.unit.test_base231_domain_pack import LIMITS, prepare
from tests.unit.test_base231_financial_sparse import BODY


def prepared(root, monkeypatch):
    inventory = prepare(root)
    raw = inventory["raw_members"][0]
    path = root / raw["path"]
    rows = pq.read_table(path).to_pylist()
    rows[0].update(content=BODY,
                   content_sha256="sha256:" + hashlib.sha256(BODY.encode()).hexdigest())
    pq.write_table(pa.Table.from_pylist(rows), path)
    raw.update(sha256=file_sha(path), original_path="payload/raw_f10.parquet",
               original_manifest_sha256="sha256:" + "a" * 64)
    selection = root / "evidence/original-selection.json"
    selection.write_bytes(canonical(select_metadata([(raw["path"], path)])))
    catalog = {"records": [{"record_type": "file", "path": raw["original_path"],
                            "sha256": raw["sha256"], "source_revision": raw["source_revision"],
                            "source_manifest_sha256": raw["original_manifest_sha256"]}]}
    (root / "evidence/SOURCE-CATALOG.json").write_bytes(canonical(catalog))
    descriptor = root / "evidence/original-dwd-materialization.json"
    descriptor.write_bytes(canonical({"layout": {
        t: {"files": [{"sha256": file_sha(root / p), "size_bytes": (root / p).stat().st_size}
                      for p in paths]} for t, paths in inventory["parents"].items()}}))
    (root / "evidence/original-dwd-manifest.json").write_bytes(canonical({
        "materialization_sha256": file_sha(descriptor)}))
    (root / "evidence/original-ods-manifest.json").write_bytes(canonical({"synthetic": True}))
    (root / "evidence/qualified-bundle-manifest.json").write_bytes(canonical({"members": [
        {"path": "domain/" + raw["path"], "sha256": raw["sha256"]},
        {"path": "domain/projection/selection.json", "sha256": file_sha(selection)},
    ]}))
    for field, name in [("QUALIFIED", "evidence/qualified-bundle-manifest.json"),
                        ("CATALOG", "evidence/SOURCE-CATALOG.json"),
                        ("ODS", "evidence/original-ods-manifest.json"),
                        ("DWD", "evidence/original-dwd-manifest.json"),
                        ("CONTRACT", "config/base231-domains.v1.json")]:
        monkeypatch.setattr(adapter, field, file_sha(root / name))
    inventory.update(kind=adapter.KIND,
                     code_inputs={p.name: file_sha(p) for p in Path(adapter.__file__).parent.glob(
                         "*.py")},
                     files=[{"path": str(p.relative_to(root)), "sha256": file_sha(p),
                             "bytes": p.stat().st_size}
                            for p in sorted(root.rglob("*")) if p.is_file()])
    (root / "INPUTS.json").write_bytes(canonical(inventory))
    return inventory


def test_source_bound_sparse_output_has_schema_keys_references_and_physical_summaries(
    tmp_path, monkeypatch,
):
    root = tmp_path / "pack"
    with Budget(root, [], LIMITS) as budget:
        prepared(root, monkeypatch)
        result = adapter.project(root, root / "projection", budget)
        manifest = adapter.seal(root, result)
    assert result["rows"]["dwd_financial_fact"] == 4
    assert result["physical_semantics"]["dwd_financial_fact"]["rows"] == 4
    assert result["counts"]["previously_held_frames_with_dated_view"] == 1
    assert not manifest["full_mirror_integration_complete"]
    with pytest.raises(RuntimeError, match="ALREADY_SEALED"):
        adapter.seal(root, result)


@pytest.mark.parametrize("mode", ["revision", "selection", "parent", "purpose", "code"])
def test_rehashed_inventory_cannot_forge_original_qualification(tmp_path, monkeypatch, mode):
    root = tmp_path / "pack"
    root.mkdir()
    inventory = prepared(root, monkeypatch)
    if mode == "revision":
        inventory["raw_members"][0]["source_revision"] = "invented"
    elif mode == "selection":
        path = root / "evidence/original-selection.json"
        path.write_bytes(canonical({"selected": []}))
        for item in inventory["files"]:
            if item["path"] == str(path.relative_to(root)):
                item.update(sha256=file_sha(path), bytes=path.stat().st_size)
    elif mode == "parent":
        inventory["parents"]["dwd_security"] = []
    elif mode == "purpose":
        inventory["consumer_admission"] = "GRANTED"
    else:
        inventory["code_inputs"]["base231_financial_sparse.py"] = "sha256:" + "b" * 64
    (root / "INPUTS.json").write_bytes(canonical(inventory))
    with pytest.raises(RuntimeError, match="SPARSE_PACK"):
        adapter.validate_inputs(root)


def test_corrupt_input_fails_even_when_output_exists(tmp_path, monkeypatch):
    root = tmp_path / "pack"
    with Budget(root, [], LIMITS) as budget:
        inventory = prepared(root, monkeypatch)
        adapter.project(root, root / "projection", budget)
    (root / inventory["raw_members"][0]["path"]).write_bytes(b"bad")
    with pytest.raises(RuntimeError, match="INPUT_BYTES_CHANGED"):
        adapter.validate_inputs(root)
