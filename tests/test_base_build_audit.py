from __future__ import annotations

import json
import os
import shutil

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget
from quanthus.pipelines.securities_warehouse.base_build_audit import audit_structure, physical_guard
from quanthus.pipelines.securities_warehouse.base_build_composition import audit_standard_image
from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha
from tests.test_base_build_composition import limits
from tests.test_base_build_ods import readonly, save
from tests.test_base_build_pair import materialize, pair_fixture
from tests.unit.test_base24_ods_image import source_closure as source_closure
from tests.unit.test_native5m_daily_source import original as original


def audited_fixture(tmp_path, source_closure):
    """Complete structural fixture; source-replay/A remain separate acceptance."""
    f = pair_fixture(tmp_path, source_closure, audit_coherent=True)
    dwd, _ = materialize(tmp_path, f)
    # Original inputs really disappear; product-relative reads must suffice.
    for path in f[10].roots.values():
        shutil.rmtree(path)
    return f, dwd


def test_complete_paired_structural_audit_uses_only_local_products(
    tmp_path, source_closure, monkeypatch
):
    f, dwd = audited_fixture(tmp_path, source_closure)
    ods = f[3]
    readonly(monkeypatch, [ods, dwd])
    with Budget(tmp_path / "audit", [ods, dwd], limits()) as budget:
        receipt = audit_structure(
            ods=ods,
            dwd=dwd,
            contract=f[6],
            expected_input_identity=f[10].verified_identity,
            budget=budget,
        )
    assert receipt["DWD"]["global_keys_versions_references_lineage"] == "PASS"
    assert len(receipt["closure"]["table_counts"]) == 33
    assert receipt["source_replay"] == "PENDING"
    assert receipt["historical_disposition"] == "PENDING"
    assert receipt["publication_eligible"] is False
    assert not (ods / "READY").exists() and not (dwd / "READY").exists()


def test_physical_guard_detects_same_size_inode_replacement_with_restored_mtime(tmp_path):
    root = tmp_path / "product"
    root.mkdir()
    path = root / "payload.bin"
    path.write_bytes(b"original")
    before = physical_guard(root)
    stamp = path.stat()
    replacement = root / "replacement"
    replacement.write_bytes(b"changed!")
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    replacement.replace(path)
    assert physical_guard(root) != before


def test_self_consistent_dwd_cannot_drop_a_predecessor_nonempty_file(
    tmp_path,
    source_closure,
    monkeypatch,
):
    f, dwd = audited_fixture(tmp_path, source_closure)
    record = json.loads((dwd / "MATERIALIZATION.json").read_bytes())
    plan = json.loads((dwd / "evidence/input-plan.json").read_bytes())
    table = "dwd_security_bar"
    original = plan["tables"][table]
    removed_index = next(
        i
        for i, r in enumerate(original["files"])
        if r["source_id"] == "predecessor" and "interval_size=1d/" in r["partition"]
    )
    removed = original["files"].pop(removed_index)
    original["rows"] -= removed["rows"]
    original["expected_partition_rows"][removed["partition"]] -= removed["rows"]
    if original["expected_partition_rows"][removed["partition"]] == 0:
        original["expected_partition_rows"].pop(removed["partition"])
    layout = record["layout"][table]
    deleted = layout["files"].pop(removed_index)
    (dwd / deleted["path"]).unlink()
    moved = []
    for i, row in enumerate(layout["files"]):
        temp = dwd / ("moved-" + str(i))
        (dwd / row["path"]).rename(temp)
        moved.append(temp)
    for i, (row, temp) in enumerate(zip(layout["files"], moved, strict=True)):
        row["path"] = f"payload/{table}/{row['partition']}/part-{i:06d}.parquet"
        temp.rename(dwd / row["path"])
    for directory in sorted((dwd / "payload").rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    layout["expected_partition_rows"] = dict(original["expected_partition_rows"])
    save(dwd / "evidence/input-plan.json", plan)
    record["plan_digest"] = digest(plan)
    record["evidence"]["evidence/input-plan.json"] = file_sha(dwd / "evidence/input-plan.json")
    save(dwd / "MATERIALIZATION.json", record)
    readonly(monkeypatch, [f[3], dwd])
    with Budget(tmp_path / "native-audit", [dwd], limits()) as budget:
        assert audit_standard_image(dwd, budget, f[6])["assessment"]["build_eligible"]
    with (
        Budget(tmp_path / "pair-audit", [f[3], dwd], limits()) as budget,
        pytest.raises(
            BoundedError, match="BUILD_PAIR_AUDIT_INHERIT_OR_PERIOD_MEMBER_CLOSURE_CHANGED"
        ),
    ):
        audit_structure(
            ods=f[3],
            dwd=dwd,
            contract=f[6],
            expected_input_identity=f[10].verified_identity,
            budget=budget,
        )
    assert not (dwd / "READY").exists()


def test_source_period_cold_replay_keeps_identity_with_explicit_paired_parent_projection(
    tmp_path,
    original,
):
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base_build_period import (
        PARENTS,
        VerifiedInputs,
        prepare_period,
    )
    from tests.test_base_build_period import fixture as period_fixture

    args = period_fixture(tmp_path, original)
    inputs = args["inputs"]
    with Budget(
        tmp_path / "baseline-period", [Path(p) for p in inputs.roots.values()], limits()
    ) as budget:
        baseline = prepare_period(**args, output=budget.root / "period", budget=budget)
    archive = tmp_path / "archive/members"
    archive.mkdir(parents=True)
    roots = {}
    for sid, source in inputs.roots.items():
        target = archive / sid
        shutil.move(source, target)
        roots[sid] = str(target)
    verified = VerifiedInputs.verify(inputs.frozen, roots)
    prior_id = next(
        r["id"] for r in inputs.frozen["content"]["selection"]["packages"] if r["role"] == "dwd"
    )
    prior = Path(roots[prior_id])
    record = json.loads((prior / "MATERIALIZATION.json").read_bytes())
    paired = tmp_path / "paired-parent-members"
    paired.mkdir()
    mapped = {}
    for table in PARENTS:
        for row in record["layout"][table]["files"]:
            target = paired / (str(len(mapped)) + ".parquet")
            (prior / row["path"]).rename(target)
            mapped[row["path"]] = target
    args["inputs"] = VerifiedInputs(verified.frozen, roots, verified.verified_identity, mapped)
    with Budget(
        tmp_path / "replayed-period", [Path(p) for p in roots.values()] + [paired], limits()
    ) as budget:
        replay = prepare_period(**args, output=budget.root / "period", budget=budget)
    assert replay == baseline
    assert replay["new_quality_held"] == 0


def test_matching_physical_guard_cannot_replace_full_byte_verification(
    tmp_path,
    source_closure,
    monkeypatch,
):
    from quanthus.pipelines.securities_warehouse import base_build_audit as audit

    f, dwd = audited_fixture(tmp_path, source_closure)
    readonly(monkeypatch, [f[3], dwd])
    fixed = {"files": 1, "bytes": 1, "physical_identity": "sha256:" + "f" * 64}
    monkeypatch.setattr(audit, "physical_guard", lambda root: fixed)
    record = json.loads((dwd / "MATERIALIZATION.json").read_bytes())
    path = dwd / record["layout"]["dwd_security_bar"]["files"][0]["path"]
    original = path.read_bytes()
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with (
        Budget(tmp_path / "audit", [f[3], dwd], limits()) as budget,
        pytest.raises(BoundedError, match="NATIVE_IMAGE_PHYSICAL_BYTES_CHANGED"),
    ):
        audit.audit_structure(
            ods=f[3],
            dwd=dwd,
            contract=f[6],
            expected_input_identity=f[10].verified_identity,
            budget=budget,
        )


def test_full_pair_replays_all_period_sources_from_archived_inputs_after_originals_are_removed(
    tmp_path,
    source_closure,
    original,
    monkeypatch,
):
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base_build_audit import audit_pair
    from quanthus.pipelines.securities_warehouse.base_build_ods import materialize_ods
    from quanthus.pipelines.securities_warehouse.base_build_pair import materialize_dwd
    from quanthus.pipelines.securities_warehouse.base_build_period import prepare_period
    from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
    from quanthus.pipelines.securities_warehouse.base_build_windows import build_windows
    from tests.base_build_full_fixture import native_predecessor, ods_predecessor
    from tests.test_base_build_period import fixture as period_fixture

    args = period_fixture(
        tmp_path,
        original,
        base_factory=lambda root, schemas: native_predecessor(root, schemas, monkeypatch),
        ods_factory=lambda: ods_predecessor(tmp_path, source_closure),
    )
    inputs = args["inputs"]
    roots = [Path(p) for p in inputs.roots.values()]
    c = delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes()),
        version="2.5",
        predecessor_version="2.4",
        through="2026-10-09",
    )
    prior_ods = next(
        Path(inputs.roots[r["id"]])
        for r in inputs.frozen["content"]["selection"]["packages"]
        if r["role"] == "ods"
    )
    prior_dwd = next(
        Path(inputs.roots[r["id"]])
        for r in inputs.frozen["content"]["selection"]["packages"]
        if r["role"] == "dwd"
    )
    with Budget(tmp_path / "materialize-job", roots, limits()) as budget:
        period = budget.root / "period"
        prepare_period(**args, output=period, budget=budget)
        windows = budget.root / "windows"
        build_windows(
            predecessor=prior_ods,
            period=period,
            binding=args["binding"],
            output=windows,
            budget=budget,
        )
        ods = budget.root / "ODS"
        materialize_ods(
            inputs=inputs, period=period, windows=windows, contract=c, output=ods, budget=budget
        )
        dwd = budget.root / "DWD"
        materialize_dwd(
            predecessor=prior_dwd,
            period=period,
            windows=windows,
            ods=ods,
            binding=args["binding"],
            contract=c,
            rules=args["rules"],
            model=json.loads(Path("config/pipelines/dwd-domain-model.v1.json").read_bytes()),
            extension=json.loads(
                Path("config/pipelines/dwd-domain-model.v1.1-extension.json").read_bytes()
            ),
            config_root=Path("config/pipelines"),
            output=dwd,
            budget=budget,
        )
    for root in roots:
        shutil.rmtree(root)
    shutil.rmtree(period)
    shutil.rmtree(windows)
    readonly(monkeypatch, [ods, dwd])
    with Budget(tmp_path / "full-pair-audit", [ods, dwd], limits()) as budget:
        receipt = audit_pair(
            ods=ods,
            dwd=dwd,
            contract=c,
            expected_input_identity=inputs.verified_identity,
            budget=budget,
        )
    assert receipt["source_replay"] == "PASS_ALL_PERIOD_SOURCES_AND_TYPED_DELTA"
    assert receipt["replay_identity"] == receipt["ODS"]["period_admission_identity"]
    assert len(receipt["closure"]["table_counts"]) == 33
    assert receipt["publication_eligible"] is True
    assert receipt["historical_disposition"] == "PASS_NO_APPLICABLE_REGISTERED_CASE"
    assert not (ods / "READY").exists() and not (dwd / "READY").exists()

    # Actual complete source audit -> paired metadata -> complete sealed audit.
    # Unit-only mocked mount flags remain distinct from runtime A/B/C.
    from quanthus.pipelines.securities_warehouse.base_build_publication import (
        pair_documents,
        prepare_markers,
        verify_pair_seal,
        write_markers,
    )

    control = tmp_path / "publication"
    control.mkdir()
    documents = pair_documents(
        ods=ods, dwd=dwd, receipt=receipt, contract=c, revision=args["binding"]["code_revision"]
    )
    prepared = prepare_markers(control, documents, attempt="attempt-000001")
    write_markers(control, {"ods": ods, "dwd": dwd}, prepared, attempt="attempt-000001")
    with Budget(tmp_path / "sealed-pair-audit", [ods, dwd], limits()) as budget:
        fresh = audit_pair(
            ods=ods,
            dwd=dwd,
            contract=c,
            expected_input_identity=inputs.verified_identity,
            budget=budget,
        )
    verify_pair_seal(ods, dwd, fresh)

    from quanthus.pipelines.securities_warehouse.base_build_publication import (
        verify_pair_registration,
    )

    # Rehashing outer markers cannot legitimize a false scan or changed proof.
    for layer, field in ((ods, "content"), (dwd, "scan_identity"), (dwd, "summaries")):
        before = (layer / "manifest.json").read_bytes()
        ready = (layer / "READY").read_bytes()
        damaged = json.loads(before)
        if field == "content":
            damaged[field]["audit_identity"] = "sha256:" + "0" * 64
            damaged["content_digest"] = digest(damaged["content"])
        else:
            damaged[field] = {} if field == "summaries" else "sha256:" + "0" * 64
            damaged["content_digest"] = digest(
                {k: v for k, v in damaged.items() if k != "content_digest"}
            )
        save(layer / "manifest.json", damaged)
        (layer / "READY").write_text(file_sha(layer / "manifest.json") + "\n")
        with pytest.raises(BoundedError, match="BUILD_PUBLICATION_MANIFEST_PROOF_CHANGED"):
            verify_pair_registration(ods, dwd)
        (layer / "manifest.json").write_bytes(before)
        (layer / "READY").write_bytes(ready)


@pytest.mark.parametrize(
    "damage", ["extra-switch", "empty-denial", "duplicate-denial", "bad-input-id"]
)
def test_audit_job_has_no_quality_or_source_denial_bypass(damage):
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse.base_build import validate_audit_job

    job = json.loads(Path("config/pipelines/base-build-audit-job.example.v1.json").read_bytes())
    validate_audit_job(job)
    if damage == "extra-switch":
        job["allow_original_sources"] = True
    elif damage == "empty-denial":
        job["denied_input_roots"] = []
    elif damage == "duplicate-denial":
        job["denied_input_roots"] *= 2
    else:
        job["frozen_input_identity"] = "unbound"
    with pytest.raises(BoundedError, match="BUILD_AUDIT_"):
        validate_audit_job(job)


def audit_worker_job(tmp_path, monkeypatch):
    """Worker ordering/recovery unit fixture; runtime namespace is not accepted here."""
    from pathlib import Path

    from quanthus.pipelines.securities_warehouse import base_build as entry

    job = json.loads(Path("config/pipelines/base-build-audit-job.example.v1.json").read_bytes())
    job.update(
        work_root=str(tmp_path / "worker"),
        limits={"free_floor_bytes": 1},
        denied_input_roots=[str(tmp_path / "absent-original")],
    )
    monkeypatch.setattr(entry, "_require_pinned_code", lambda revision: None)
    monkeypatch.setattr(
        entry, "check_environment", lambda **kwargs: {"lock_sha256": "sha256:" + "a" * 64}
    )
    monkeypatch.setattr(entry, "_preflight_paths", lambda *args: None)
    contract = json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes())
    from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract

    c = delivery_contract(contract, version="2.5", predecessor_version="2.4", through="2026-10-31")
    return entry, job, c


def test_audit_worker_rejects_readable_original_source_before_any_product_scan(
    tmp_path, monkeypatch
):
    entry, job, c = audit_worker_job(tmp_path, monkeypatch)
    original = tmp_path / "original-source"
    original.mkdir()
    job["denied_input_roots"] = [str(original)]
    monkeypatch.setattr(
        entry, "audit_pair", lambda **kwargs: pytest.fail("source boundary must fail first")
    )
    with pytest.raises(BoundedError, match="BUILD_AUDIT_ORIGINAL_SOURCE_STILL_READABLE"):
        entry.run(action="audit", job=job, contract=c)
    assert not (tmp_path / "worker").exists()


def test_audit_worker_preserves_bound_failure_and_does_not_overwrite_a_prior_attempt(
    tmp_path, monkeypatch
):
    entry, job, c = audit_worker_job(tmp_path, monkeypatch)

    def failure(**kwargs):
        raise BoundedError("SYNTHETIC_AUDIT_FAILURE")

    monkeypatch.setattr(entry, "audit_pair", failure)
    with pytest.raises(BoundedError, match="SYNTHETIC_AUDIT_FAILURE"):
        entry.run(action="audit", job=job, contract=c)
    path = tmp_path / "worker/FAILURE.json"
    original = path.read_bytes()
    receipt = json.loads(original)
    assert receipt["reason_code"] == "SYNTHETIC_AUDIT_FAILURE"
    assert receipt["code_revision"] == job["builder_revision"]
    assert receipt["frozen_input_identity"] == job["frozen_input_identity"]
    assert receipt["formal_READY"] is False
    with pytest.raises(FileExistsError):
        entry.run(action="audit", job=job, contract=c)
    assert path.read_bytes() == original
    assert not (tmp_path / "worker/AUDIT.json").exists()


def test_audit_execution_envelope_binds_actual_attempt_and_cannot_reuse_a_saved_success(
    tmp_path, monkeypatch
):
    from datetime import datetime

    from quanthus.pipelines.securities_warehouse.base_build_inputs import identity

    entry, job, c = audit_worker_job(tmp_path, monkeypatch)
    receipt = {"binding": {"code_revision": job["builder_revision"]}, "publication_eligible": False}
    receipt["identity"] = identity(receipt)
    monkeypatch.setattr(entry, "audit_pair", lambda **kwargs: receipt)
    result = entry.run(action="audit", job=job, contract=c)
    path = tmp_path / "worker/COMPLETION.json"
    value = json.loads(path.read_bytes())
    before = path.read_bytes()
    assert value["job_identity"] == identity(job)
    assert value["policy_identity"] == identity(c)
    assert value["work_root"] == job["work_root"]
    assert value["audit_sha256"] == file_sha(tmp_path / "worker/AUDIT.json")
    assert value["audit_identity"] == receipt["identity"]
    assert datetime.fromisoformat(value["started_at"]) <= datetime.fromisoformat(
        value["finished_at"]
    )
    assert value["formal_READY"] is False and value["publication_eligible"] is False
    assert result["execution_identity"] == value["identity"]
    with pytest.raises(FileExistsError):
        entry.run(action="audit", job=job, contract=c)
    assert path.read_bytes() == before
