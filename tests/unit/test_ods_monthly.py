"""Synthetic contract tests; real ODS acceptance remains a separate mode."""

import copy
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines import ods_monthly as mod
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits


class SyntheticBudget:
    def check(self, reserve=0):
        assert reserve >= 0

    def begin_stage(self, name):
        pass


@pytest.fixture
def inputs(tmp_path):
    selected = []
    for role in ["historical_seed", "core", "events"]:
        root = tmp_path / role
        root.mkdir()
        rel = (
            "payload/reference.parquet"
            if role == "historical_seed"
            else "artifacts/observations.parquet"
        )
        p = root / rel
        p.parent.mkdir()
        pq.write_table(
            pa.table(
                {
                    "id": ["SYNTHETIC"],
                    "precise": pa.array([Decimal("0.12345678901")], type=pa.decimal128(20, 11)),
                    "zero": [0],
                    "unknown": pa.array([None], pa.string()),
                    "acquired_at": ["2026-10-01T01:00:00+08:00"],
                }
            ),
            p,
        )
        entry = {"path": rel, "sha256": file_sha(p), "size_bytes": p.stat().st_size}
        if role == "historical_seed":
            content = {"through_date": "2026-07-31", "files": [entry]}
            m = {
                "schema_version": 1,
                "kind": "quanthus_ods_base_bundle",
                "content": content,
                "content_digest": digest(content),
            }
            name = "manifest.json"
            identity = "synthetic-july-seed"
        else:
            m = {
                "release_id": "synthetic-" + role,
                "status": "ready",
                "market_date": "2026-09-30",
                "created_at": "2026-10-01T01:00:00+08:00",
                "artifacts": [
                    {
                        **entry,
                        "relative_path": rel,
                        "name": "observations",
                        "knowledge_cutoff": "2026-10-01T01:00:00+08:00",
                    }
                ],
            }
            name = "release.json"
            identity = m["release_id"]
        (root / name).write_bytes(canonical(m))
        sha = file_sha(root / name)
        (root / "READY").write_text(sha + "\n")
        selected.append(
            {
                "root": str(root),
                "manifest_name": name,
                "manifest_sha256": sha,
                "identity": identity,
                "role": role,
            }
        )
    contract = {
        "purpose": mod.PURPOSE,
        "business_timezone": "Asia/Shanghai",
        "business_through": "2026-09-30",
        "calendar_authority": {"open_dates": ["2026-09-30"]},
        "historical_seed": {"manifest_sha256": selected[0]["manifest_sha256"]},
        "required_daily_roles": ["core", "events"],
        "required_artifacts": {"core": ["observations"], "events": ["observations"]},
        "admitted_sources": {s["manifest_sha256"]: {"role": s["role"]} for s in selected},
    }
    selection = {"selected": selected}
    plan = mod.freeze_monthly_plan(selection, contract)
    return selection, contract, plan


def repack_plan(root, transform):
    p = root / "MATERIALIZATION.json"
    x = json.loads(p.read_bytes())
    transform(x)
    frozen = {
        k: v for k, v in x.items() if k not in ("identity", "build_UTC", "frozen_plan_identity")
    }
    x["frozen_plan_identity"] = digest(frozen)
    x["identity"] = digest({k: v for k, v in x.items() if k != "identity"})
    p.write_bytes(canonical(x))


def test_real_scratch_budget_is_not_the_independent_image_write_budget(inputs, tmp_path):
    _, contract, plan = inputs
    sources = [Path(s["archive_source_root"]) for s in plan["sources"]]
    assert sum(f["size_bytes"] for f in plan["files"]) > 1024
    with Budget(
        tmp_path / "bounded-audit-TMP",
        sources,
        Limits(allocated_bytes=1024, temp_bytes=1024, free_floor_bytes=1),
    ) as budget:
        result = mod.materialize_monthly(plan, tmp_path / "image", budget)
        assert result["original_manifest_closure"] == "PASS"
        assert result["file_bytes"] > budget.limits.allocated_bytes
        assert budget.peak["allocated_bytes"] <= budget.limits.allocated_bytes
        assert (
            mod.audit_monthly(tmp_path / "image", budget, expected_contract_digest=digest(contract))
            == result
        )


def test_image_destination_free_floor_is_checked_before_mkdir(inputs, tmp_path, monkeypatch):
    _, _, plan = inputs
    budget = SyntheticBudget()
    budget.limits = SimpleNamespace(free_floor_bytes=8192)
    monkeypatch.setattr(mod.os, "statvfs", lambda _: SimpleNamespace(f_bavail=8192, f_frsize=1))
    output = tmp_path / "image"
    with pytest.raises(mod.MonthlyOdsError, match="MONTHLY_ODS_OUTPUT_FREE_FLOOR"):
        mod.materialize_monthly(plan, output, budget)
    assert not output.exists()


def test_growing_disposable_source_never_exceeds_frozen_member_write_bytes(inputs, tmp_path):
    _, _, plan = inputs
    first = plan["files"][0]

    class GrowingSourceBudget(SyntheticBudget):
        def begin_stage(self, name):
            with Path(first["archive_source_path"]).open("ab") as stream:
                stream.write(b"synthetic growth" * 1024)

    output = tmp_path / "image"
    with pytest.raises(mod.MonthlyOdsError, match="MONTHLY_ODS_SOURCE_SIZE_EXCEEDED"):
        mod.materialize_monthly(plan, output, GrowingSourceBudget())
    assert (output / first["path"]).stat().st_size <= first["size_bytes"]


def test_independent_raw_bytes_decimal_unknown_and_original_clocks(inputs, tmp_path):
    selection, contract, plan = inputs
    root = tmp_path / "image"
    before = {
        str(p): file_sha(p)
        for s in selection["selected"]
        for p in Path(s["root"]).rglob("*")
        if p.is_file()
    }
    a = mod.materialize_monthly(plan, root, SyntheticBudget())
    for f in plan["files"]:
        assert (root / f["path"]).read_bytes() == Path(f["archive_source_path"]).read_bytes()
        assert (root / f["path"]).stat().st_ino != Path(f["archive_source_path"]).stat().st_ino
    assert before == {
        str(p): file_sha(p)
        for s in selection["selected"]
        for p in Path(s["root"]).rglob("*")
        if p.is_file()
    }
    for s in selection["selected"]:
        shutil.rmtree(s["root"])
    b = mod.audit_monthly(root, SyntheticBudget(), expected_contract_digest=digest(contract))
    assert a == b
    assert not (root / "READY").exists()


@pytest.mark.parametrize("what", ["unknown_source", "empty_sources", "purpose", "contract"])
def test_repacked_offline_structure_rejected(inputs, tmp_path, what):
    _, contract, plan = inputs
    root = tmp_path / "image"
    mod.materialize_monthly(plan, root, SyntheticBudget())

    def edit(x):
        if what == "unknown_source":
            x["files"][0]["source_id"] = "unknown"
        elif what == "empty_sources":
            x["sources"] = []
        elif what == "purpose":
            x["purpose"] = "intraday_qualified"
        else:
            x["contract"]["required_daily_roles"] = []
            x["contract_sha256"] = digest(x["contract"])

    repack_plan(root, edit)
    with pytest.raises(mod.MonthlyOdsError):
        mod.audit_monthly(root, SyntheticBudget(), expected_contract_digest=digest(contract))


@pytest.mark.parametrize("what", ["missing", "corrupt", "extra_empty"])
def test_physical_fail_closed(inputs, tmp_path, what):
    _, contract, plan = inputs
    root = tmp_path / "image"
    mod.materialize_monthly(plan, root, SyntheticBudget())
    p = root / plan["files"][0]["path"]
    if what == "missing":
        p.unlink()
    elif what == "corrupt":
        b = p.read_bytes()
        p.write_bytes(bytes([b[0] ^ 1]) + b[1:])
    else:
        pq.write_table(pa.table({"id": pa.array([], pa.string())}), root / "extra.parquet")
    with pytest.raises(mod.MonthlyOdsError):
        mod.audit_monthly(root, SyntheticBudget(), expected_contract_digest=digest(contract))


def test_incomplete_daily_role_and_future_business_date(inputs):
    selection, contract, _ = inputs
    c = copy.deepcopy(contract)
    c["required_artifacts"]["core"] = ["missing"]
    with pytest.raises(mod.MonthlyOdsError, match="REQUIRED_DAILY_ROLE_MISSING"):
        mod.freeze_monthly_plan(selection, c)
    root = Path(selection["selected"][1]["root"])
    m = json.loads((root / "release.json").read_bytes())
    m["market_date"] = "2026-10-01"
    (root / "release.json").write_bytes(canonical(m))
    sha = file_sha(root / "release.json")
    old_sha = selection["selected"][1]["manifest_sha256"]
    selection["selected"][1]["manifest_sha256"] = sha
    contract["admitted_sources"][sha] = contract["admitted_sources"].pop(old_sha)
    (root / "READY").write_text(sha + "\n")
    with pytest.raises(mod.MonthlyOdsError, match="AFTER_CUTOFF"):
        mod.freeze_monthly_plan(selection, contract)


def test_input_mutation_before_write_and_sealed_output_reexecution(inputs, tmp_path):
    _, _, plan = inputs
    output = tmp_path / "output"
    mod.materialize_monthly(plan, output, SyntheticBudget())
    before = (output / "MATERIALIZATION.json").read_bytes()
    with pytest.raises(FileExistsError):
        mod.materialize_monthly(plan, output, SyntheticBudget())
    assert before == (output / "MATERIALIZATION.json").read_bytes()
    Path(plan["files"][0]["archive_source_path"]).write_bytes(
        b"changed disposable synthetic source"
    )
    second = tmp_path / "second"
    with pytest.raises(mod.MonthlyOdsError, match="BEFORE_WRITE"):
        mod.materialize_monthly(plan, second, SyntheticBudget())
    assert not second.exists()


@pytest.mark.parametrize("what", ["source_contains_output", "staging_is_source", "staging_symlink"])
def test_path_checks_precede_any_write_and_preserve_sources(inputs, tmp_path, what):
    selection, _, plan = inputs
    source = Path(selection["selected"][0]["root"])
    before = {
        str(p): file_sha(p)
        for s in selection["selected"]
        for p in Path(s["root"]).rglob("*")
        if p.is_file()
    }
    if what == "source_contains_output":
        output = source / "output"
    elif what == "staging_is_source":
        output = tmp_path / "output"
        # A copy of the source becomes the exact sibling staging source, re-freeze it.
        staging = tmp_path / ".output.job"
        shutil.copytree(source, staging)
        selection["selected"][0]["root"] = str(staging)
        plan = mod.freeze_monthly_plan(selection, plan["contract"])
    else:
        output = tmp_path / "output"
        (tmp_path / ".output.job").symlink_to(source, target_is_directory=True)
    with pytest.raises(Exception) as caught:
        mod.materialize_monthly(plan, output, SyntheticBudget())
    assert str(caught.value) in {"SOURCE_OUTPUT_OVERLAP", "SOURCE_SYMLINK"}
    assert not output.exists()
    for p, sha in before.items():
        assert file_sha(Path(p)) == sha


def test_two_new_process_relocations_without_source_paths_or_environment(inputs, tmp_path):
    selection, contract, plan = inputs
    image = tmp_path / "image"
    a = mod.materialize_monthly(plan, image, SyntheticBudget())
    for s in selection["selected"]:
        shutil.rmtree(s["root"])
    code = """import importlib.util,json,socket,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('offline_monthly',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class B:
 def check(self,*a):pass
 def begin_stage(self,*a):pass
def denied(*a,**k):raise RuntimeError('SYNTHETIC_SOURCE_NETWORK_DENIED')
socket.create_connection=denied
r=m.audit_monthly(Path(sys.argv[2]),B(),expected_contract_digest=sys.argv[3])
print(json.dumps({'pid':__import__('os').getpid(),'identity':r['identity']}))"""
    pids = []
    for name in ["move1", "move2"]:
        relocated = tmp_path / name
        shutil.copytree(image, relocated)
        for p in image.rglob("*"):
            if p.is_file():
                assert p.stat().st_ino != (relocated / p.relative_to(image)).stat().st_ino
        shutil.rmtree(image)
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(Path(mod.__file__).absolute()),
                str(relocated),
                digest(contract),
            ],
            capture_output=True,
            text=True,
            check=True,
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(Path.cwd() / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        result = json.loads(r.stdout)
        assert result["identity"] == a["identity"]
        pids.append(result["pid"])
        image = relocated
    assert len(set(pids)) == 2


def test_native_format_cannot_be_repacked_as_unchecked_raw(inputs, tmp_path):
    _, contract, plan = inputs
    root = tmp_path / "image"
    mod.materialize_monthly(plan, root, SyntheticBudget())
    repack_plan(root, lambda x: x["files"][0].update(format="raw"))
    with pytest.raises(mod.MonthlyOdsError, match="SOURCE_BINDING_CHANGED"):
        mod.audit_monthly(root, SyntheticBudget(), expected_contract_digest=digest(contract))


def test_input_namespace_changed_before_write(inputs, tmp_path):
    selection, _, plan = inputs
    (Path(selection["selected"][0]["root"]) / "unexpected.json").write_text("{}")
    output = tmp_path / "output"
    with pytest.raises(mod.MonthlyOdsError, match="NAMESPACE_CHANGED_BEFORE_WRITE"):
        mod.materialize_monthly(plan, output, SyntheticBudget())
    assert not output.exists()


def test_interrupted_copy_preserves_partial_evidence_without_complete_markers(inputs, tmp_path):
    _, _, plan = inputs
    output = tmp_path / "output"

    class FailCopy(SyntheticBudget):
        copying = False

        def begin_stage(self, name):
            self.copying = True

        def check(self, reserve=0):
            if self.copying:
                raise RuntimeError("DISPOSABLE_SYNTHETIC_BUDGET_STOP")

    with pytest.raises(RuntimeError, match="SYNTHETIC_BUDGET_STOP"):
        mod.materialize_monthly(plan, output, FailCopy())
    assert output.is_dir()
    assert not (output / "READY").exists()
    assert not (output / "MATERIALIZATION.json").exists()
    with pytest.raises(FileExistsError):
        mod.materialize_monthly(plan, output, SyntheticBudget())


def synthetic_acceptance(identity):
    return [
        {
            "pid": pid,
            "identity": identity,
            "code_revision": "a" * 40,
            "source_network_denied": True,
            "physical_copy_distinct_inodes": True,
            "source_reads_after_probes": 0,
            "network_calls_after_probes": 0,
        }
        for pid in [11, 22]
    ]


def synthetic_negative_proofs():
    return {
        "missing": "MONTHLY_ODS_PHYSICAL_FILE_SET_CHANGED",
        "corrupt": "MONTHLY_ODS_PHYSICAL_BYTES_CHANGED",
        "restored_all_original_file_hashes": "PASS",
    }


def test_real_budget_keyword_only_interface(inputs, tmp_path):
    _, _, plan = inputs

    class RealInterfaceBudget:
        def check(self, *, reserve=0, extra_rss=0):
            assert reserve >= 0 and extra_rss >= 0

        def begin_stage(self, name):
            pass

    mod.materialize_monthly(plan, tmp_path / "keyword-only", RealInterfaceBudget())


def test_sealed_offline_native_recompute_without_old_sources(inputs, tmp_path):
    selection, contract, _ = inputs
    contract.update(
        contract_id="synthetic-sealed-native",
        stage="ACTIVE_VERSIONED_CONTRACT",
        intraday={"availability_gaps": []},
    )
    plan = mod.freeze_monthly_plan(selection, contract)
    root = tmp_path / "sealed-native"
    original = mod.materialize_monthly(plan, root, SyntheticBudget())
    mod.seal_monthly(
        root,
        SyntheticBudget(),
        expected_contract_digest=digest(contract),
        code_revision="a" * 40,
        independent_receipts=synthetic_acceptance(original["identity"]),
        expected_negative_probes=synthetic_negative_proofs(),
    )
    for s in selection["selected"]:
        shutil.rmtree(s["root"])
    assert not (root / "MATERIALIZATION.json").exists()
    assert (
        mod.audit_monthly(root, SyntheticBudget(), expected_contract_digest=digest(contract))
        == original
    )
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["content"]["policy_sha256"] = "sha256:" + "0" * 64
    manifest["content_digest"] = digest(manifest["content"])
    (root / "manifest.json").write_bytes(canonical(manifest))
    (root / "READY").write_text(file_sha(root / "manifest.json") + "\n")
    with pytest.raises(mod.MonthlyOdsError, match="SEALED_SCOPE_CHANGED"):
        mod.audit_monthly(root, SyntheticBudget(), expected_contract_digest=digest(contract))


@pytest.mark.parametrize("what", ["draft", "same_pid", "source_reachable", "no_negative"])
def test_sealing_gates_never_mark_incomplete_package_ready(inputs, tmp_path, what):
    selection, contract, _ = inputs
    contract["contract_id"] = "disposable-synthetic-ods-contract"
    contract["stage"] = "DRAFT" if what == "draft" else "ACTIVE_VERSIONED_CONTRACT"
    plan = mod.freeze_monthly_plan(selection, contract)
    root = tmp_path / "image"
    r = mod.materialize_monthly(plan, root, SyntheticBudget())
    receipts = synthetic_acceptance(r["identity"])
    negative = synthetic_negative_proofs()
    if what == "same_pid":
        receipts[1]["pid"] = receipts[0]["pid"]
    elif what == "source_reachable":
        receipts[0]["source_reads_after_probes"] = 1
    elif what == "no_negative":
        negative = {}
    with pytest.raises(mod.MonthlyOdsError):
        mod.seal_monthly(
            root,
            SyntheticBudget(),
            expected_contract_digest=digest(contract),
            code_revision="a" * 40,
            independent_receipts=receipts,
            expected_negative_probes=negative,
        )
    assert not (root / "READY").exists()
    assert not (root / "manifest.json").exists()


def test_ordinary_ods_reader_accepts_synthetic_envelope_after_all_gates(inputs, tmp_path):
    from quanthus.pipelines.ods_base import verify_ods_base_bundle

    selection, contract, _ = inputs
    contract.update(
        contract_id="disposable-synthetic-ods-contract",
        stage="ACTIVE_VERSIONED_CONTRACT",
        intraday={"availability_gaps": []},
    )
    plan = mod.freeze_monthly_plan(selection, contract)
    root = tmp_path / "ods-synthetic-monthly"
    r = mod.materialize_monthly(plan, root, SyntheticBudget())
    m = mod.seal_monthly(
        root,
        SyntheticBudget(),
        expected_contract_digest=digest(contract),
        code_revision="a" * 40,
        independent_receipts=synthetic_acceptance(r["identity"]),
        expected_negative_probes=synthetic_negative_proofs(),
    )
    assert (root / "READY").read_text().strip() == file_sha(root / "manifest.json")
    result = verify_ods_base_bundle(root, deep=True)
    assert result.content_digest == m["content_digest"]
    before = file_sha(root / "manifest.json")
    with pytest.raises(FileExistsError):
        mod.seal_monthly(
            root,
            SyntheticBudget(),
            expected_contract_digest=digest(contract),
            code_revision="a" * 40,
            independent_receipts=[],
            expected_negative_probes={},
        )
    assert file_sha(root / "manifest.json") == before
