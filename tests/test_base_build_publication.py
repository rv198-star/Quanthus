"""Publication crash/ownership regressions; these do not grant data acceptance."""

from __future__ import annotations

import copy
import os

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha, identity
from quanthus.pipelines.securities_warehouse.base_build_publication import (
    begin_transfer,
    control_bytes,
    prepare_markers,
    product_inventory,
    recover_owned_markers,
    resume_transfer,
    verify_publication_bytes,
    write_markers,
)
from quanthus.pipelines.securities_warehouse.base_build_stages import Stage, StageRunner
from tests.test_base_build_ods import save
from tests.test_base_build_stages import binding


def products(tmp_path):
    work = tmp_path / "job"
    work.mkdir()
    roots = {r: work / r.upper() for r in ("ods", "dwd")}
    for root in roots.values():
        root.mkdir()
    return work, roots


def documents():
    # Recovery only operates on owned bytes. Full semantic preparation and
    # post-seal re-audit are covered using the full source fixture separately.
    return {r: {"synthetic-role": r, "generation": 1} for r in ("ods", "dwd")}


@pytest.mark.parametrize("boundary", range(8))
def test_every_marker_write_and_link_interruption_recovers_without_losing_bytes(
    tmp_path, monkeypatch, boundary
):
    from quanthus.pipelines.securities_warehouse import base_build_publication as publication

    work, roots = products(tmp_path)
    prepared = prepare_markers(work, documents(), attempt="attempt-000001")
    link, unlink = os.link, publication.Path.unlink
    counter = 0

    def interrupt():
        nonlocal counter
        current, counter = counter, counter + 1
        if current == boundary:
            raise RuntimeError("synthetic crash")

    def crash_link(source, target):
        interrupt()
        link(source, target)

    def crash_unlink(path, *args, **kwargs):
        if path.name.endswith(".writing"):
            interrupt()
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(publication.os, "link", crash_link)
        patch.setattr(publication.Path, "unlink", crash_unlink)
        with pytest.raises(RuntimeError, match="synthetic crash"):
            write_markers(work, roots, prepared, attempt="attempt-000001")
    complete = recover_owned_markers(work, roots)
    assert recover_owned_markers(work, roots) == complete
    # Crash after linking the final READY is already a complete pair; otherwise
    # a newly prepared generation is necessary, with all interrupted bytes kept.
    if not complete:
        assert all(
            not (root / n).exists() for root in roots.values() for n in ("manifest.json", "READY")
        )
        prepared = prepare_markers(work, documents(), attempt="attempt-000002")
        write_markers(work, roots, prepared, attempt="attempt-000002")
    assert recover_owned_markers(work, roots)
    for role, root in roots.items():
        assert (root / "manifest.json").read_bytes() == control_bytes(documents()[role])
        assert (root / "READY").read_text().strip() == file_sha(root / "manifest.json")
        assert (root / "READY").stat().st_nlink == 1


def test_recovery_itself_can_be_interrupted_between_atomic_archival_moves(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base_build_publication as publication

    work, roots = products(tmp_path)
    prepared = prepare_markers(work, documents(), attempt="attempt-000001")
    for role in roots:
        (roots[role] / "manifest.json").write_bytes(control_bytes(prepared["documents"][role]))
    rename = os.rename
    moved = []

    def interrupted(source, target):
        rename(source, target)
        moved.append(target)
        raise RuntimeError("archive crash")

    with monkeypatch.context() as patch:
        patch.setattr(publication.os, "rename", interrupted)
        with pytest.raises(RuntimeError, match="archive crash"):
            recover_owned_markers(work, roots)
    assert not recover_owned_markers(work, roots)
    assert all(path.exists() for path in moved)
    archive = work / "publication-writes/attempt-000001/archived-final-markers"
    assert len(list(archive.iterdir())) == 2


@pytest.mark.parametrize(
    "damage", ["unknown-final", "altered-final", "prepared", "partial-writing"]
)
def test_unknown_or_changed_markers_preserved_and_partial_writes_archived(tmp_path, damage):
    work, roots = products(tmp_path)
    if damage == "unknown-final":
        final = roots["ods"] / "READY"
        final.write_bytes(b"unknown")
    else:
        prepared = prepare_markers(work, documents(), attempt="attempt-000001")
        directory = work / "publication-writes/attempt-000001"
        if damage == "partial-writing":
            path = directory / "ods-manifest.json.writing"
            path.write_bytes(b'{"partial":')
            assert not recover_owned_markers(work, roots)
            assert (directory / "ods-manifest.json.interrupted").read_bytes() == b'{"partial":'
            assert not recover_owned_markers(work, roots)
            return
        if damage == "prepared":
            path = directory / "PREPARED.json"
            value = copy.deepcopy(prepared)
            value["documents"]["ods"]["generation"] = 2
            save(path, value)
        else:
            write_markers(work, roots, prepared, attempt="attempt-000001")
            final = roots["ods"] / "READY"
            final.write_bytes(b"changed")
    with pytest.raises(BoundedError, match="BUILD_PUBLICATION_"):
        recover_owned_markers(work, roots)
    if damage != "prepared":
        assert final.exists()


def test_unfinished_prepare_cannot_own_an_arbitrary_marker(tmp_path):
    work, roots = products(tmp_path)
    directory = work / "publication-writes/attempt-000001"
    directory.mkdir(parents=True)
    (directory / ".PREPARED.json.partial").write_bytes(b'{"partial":')
    assert not recover_owned_markers(work, roots)
    (roots["ods"] / "READY").write_bytes(b"arbitrary")
    with pytest.raises(BoundedError, match="PREPARED_MARKERS_REQUIRED"):
        recover_owned_markers(work, roots)


def test_transfer_moves_each_owned_image_once_keeps_producer_receipts_and_blocks_rebuild(
    tmp_path, monkeypatch
):
    from quanthus.pipelines.securities_warehouse import base_build_publication as publication

    def stage(role):
        def build(output, dependencies):
            root = output / role.upper()
            root.mkdir(parents=True)
            save(root / "MATERIALIZATION.json", {"synthetic": role})
            return {"synthetic": role}

        return Stage(role, (), build, lambda output, result: None)

    work = tmp_path / "job"
    runner = StageRunner(
        root=work, sources=[], binding=binding(), stages=[stage("ods"), stage("dwd")]
    )
    result = runner.execute()
    receipts = {r: (v["path"].parent / "SUCCESS.json").read_bytes() for r, v in result.items()}
    outputs = {r: v["path"] for r, v in result.items()}
    intent = begin_transfer(work, outputs, owner_identity=identity(binding()))
    rename = os.rename

    def interrupted(source, target):
        rename(source, target)
        raise RuntimeError("move crash")

    with monkeypatch.context() as patch:
        patch.setattr(publication.os, "rename", interrupted)
        with pytest.raises(RuntimeError, match="move crash"):
            resume_transfer(work, intent)
    roots = resume_transfer(work, intent)
    assert resume_transfer(work, intent) == roots
    assert begin_transfer(work, outputs, owner_identity=identity(binding())) == intent
    for role, output in outputs.items():
        assert (output.parent / "SUCCESS.json").read_bytes() == receipts[role]
        assert not (output / role.upper()).exists()
        assert roots[role].is_dir()
    with pytest.raises(BoundedError, match="PUBLICATION_TRANSFER_OWNS_STAGE_OUTPUTS"):
        runner.execute()


def test_fresh_readback_rejects_same_length_damage_even_after_a_saved_audit(tmp_path):
    work, roots = products(tmp_path)
    root = roots["ods"]
    payload = root / "original.bin"
    payload.write_bytes(b"original")
    save(
        root / "MATERIALIZATION.json",
        {"files": [{"path": "original.bin", "sha256": file_sha(payload), "size_bytes": 8}]},
    )
    expected = file_sha(root / "MATERIALIZATION.json")
    assert verify_publication_bytes(root, "ods", expected)["bytes"] > 8
    payload.write_bytes(b"changed!")
    with pytest.raises(BoundedError, match="FRESH_BYTE_READBACK_FAILED"):
        verify_publication_bytes(root, "ods", expected)
    assert product_inventory(root, "ods")[0]["sha256"] != file_sha(payload)
