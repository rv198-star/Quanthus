from __future__ import annotations

import json

import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_native_image import (
    KIND,
    materialize_native_image,
)
from quanthus.pipelines.securities_warehouse.base22_native_transfer import OwnedTransfer
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from tests.unit.test_base22_native_image import _native_fixture


def prepare(tmp_path):
    fixture, config, source, plan = _native_fixture(tmp_path)
    old = tmp_path / "job" / "image"
    kwargs = dict(
        plan=plan,
        rules=fixture.contract,
        contract=json.loads((config / "base22-delivery.v2.json").read_bytes()),
        model=fixture.model,
        extension=fixture.extension,
        limitations=[],
        local_evidence={},
        allocated_limit=64 * 1024**2,
        free_floor=1,
        rss_limit=4 * 1024**3,
        wall_seconds=30,
        config_root=config,
    )
    record = materialize_native_image(output=old, **kwargs)
    receipt = tmp_path / "success.json"
    receipt.write_text(
        json.dumps(
            {
                "root": str(old),
                "materialization_sha256": file_sha(old / "MATERIALIZATION.json"),
                "READY": False,
                "selected_rows": sum(x["rows"] for x in plan["tables"].values()),
            }
        )
    )
    reuse = dict(
        root=old,
        materialization_sha256=file_sha(old / "MATERIALIZATION.json"),
        success_receipt=receipt,
    )
    return source, old, plan, record, kwargs, reuse


def test_only_owned_completed_copy_tmp_moves_original_sources_and_receipts_stay_unchanged(tmp_path):
    source, old, plan, record, kwargs, reuse = prepare(tmp_path)
    source_before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    marker = (old / "MATERIALIZATION.json").read_bytes()
    inodes = {
        i["path"]: (old / i["path"]).stat().st_ino
        for t in record["layout"].values()
        for i in t["files"]
    }
    new = tmp_path / "job" / "repaired" / "image"
    result = materialize_native_image(output=new, owned_reuse=reuse, **kwargs)
    assert (old / "MATERIALIZATION.json").read_bytes() == marker
    for table in result["layout"].values():
        for item in table["files"]:
            assert not (old / item["path"]).exists()
            assert (new / item["path"]).stat().st_ino == inodes[item["path"]]
            assert (new / item["path"]).stat().st_nlink == 1
    assert source_before == {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    proof = json.loads((new / "evidence/owned-transfer-registration.json").read_bytes())
    assert not proof["original_input_roots_moved"]
    assert not proof["hardlink_or_symlink_created"]


@pytest.mark.parametrize("case", ["source_root", "symlink", "sealed", "wrong_hash", "other_job"])
def test_invalid_owned_transfer_rejects_before_any_move_or_new_output(tmp_path, case):
    source, old, plan, record, _kwargs, reuse = prepare(tmp_path)
    before = {
        i["path"]: (old / i["path"]).read_bytes()
        for t in record["layout"].values()
        for i in t["files"]
    }
    new = tmp_path / "job" / "repaired" / "image"
    if case == "source_root":
        reuse["root"] = source
    elif case == "symlink":
        link = tmp_path / "job" / "alias"
        link.symlink_to(old, target_is_directory=True)
        reuse["root"] = link
    elif case == "sealed":
        (old / "READY").write_text("sealed")
    elif case == "other_job":
        new = tmp_path / "unrelated-job" / "image"
    else:
        reuse["materialization_sha256"] = "sha256:" + "a" * 64
    with pytest.raises((BoundedError, SourceAssessmentError)):
        OwnedTransfer(plan=plan, output=new, kind=KIND, **reuse)
    assert not new.exists()
    assert before == {p: (old / p).read_bytes() for p in before}
