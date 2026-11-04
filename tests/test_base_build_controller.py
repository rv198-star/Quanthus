"""Controller protocol/ordering tests with simulated workers and ownership.

These tests do not establish namespace, root protection or market-data acceptance.
The separate full pair fixture exercises actual source/native audit semantics.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse import base_build as entry
from quanthus.pipelines.securities_warehouse import base_build_controller as controller
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha, identity
from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
from quanthus.pipelines.securities_warehouse.base_build_stages import Stage, StageRunner
from tests.test_base_build_ods import save
from tests.test_base_build_stages import binding


def context(tmp_path, monkeypatch, *, eligible=True):
    controls = tmp_path / "inputs-control"
    controls.mkdir()
    inputs = {r: str(tmp_path / "original" / r) for r in ("ods", "dwd", "daily", "references")}
    for path in inputs.values():
        Path(path).mkdir(parents=True)
        (Path(path) / "original.bin").write_bytes(b"source truth")
    save(controls / "roots.json", inputs)
    save(controls / "selection.json", {"expected_days": ["2026-10-30"]})
    frozen_id = "sha256:" + "e" * 64
    save(controls / "frozen.json", {"identity": frozen_id})
    save(controls / "environment.json", {})
    wheels = tmp_path / "wheelhouse"
    wheels.mkdir()
    job = json.loads(Path("config/pipelines/base-build-job.example.v1.json").read_bytes())
    job.update(
        selection=str(controls / "selection.json"),
        frozen=str(controls / "frozen.json"),
        roots=str(controls / "roots.json"),
        work_root=str(tmp_path / "job"),
        environment_lock=str(controls / "environment.json"),
        wheelhouse=str(wheels),
        limits={"allocated_bytes": 512 * 1024**2, "free_floor_bytes": 1},
    )
    execution = json.loads(
        Path("config/pipelines/base-build-execution.example.v1.json").read_bytes()
    )
    execution.update(
        runtime_python=sys.executable,
        audit_parent=str(tmp_path / "audit-parent"),
        protected_paths=[str(tmp_path / "production"), str(tmp_path / "unqualified-cache")],
        audit_limits={"allocated_bytes": 512 * 1024**2, "free_floor_bytes": 1},
    )
    contract = delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes()),
        version="2.5",
        predecessor_version="2.4",
        through="2026-10-31",
    )
    environment_id = "sha256:" + "f" * 64
    from quanthus.pipelines.securities_warehouse import base_build_environment

    monkeypatch.setattr(entry, "_require_pinned_code", lambda revision: None)
    monkeypatch.setattr(
        base_build_environment,
        "check_environment",
        lambda **kwargs: {"lock_sha256": environment_id},
    )
    monkeypatch.setattr(controller.os, "geteuid", lambda: 0)
    monkeypatch.setattr(controller.socket, "if_nameindex", lambda: [(1, "lo")])
    monkeypatch.setattr(
        controller.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=1001, pw_gid=1001)
    )
    # Simulate ownership only; root runtime qualification remains separate.
    monkeypatch.setattr(controller.os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller.os, "chmod", lambda *args, **kwargs: None)
    protected = []
    monkeypatch.setattr(controller, "protect_tree", lambda root, **kwargs: protected.append(root))
    monkeypatch.setattr(controller, "require_protected", lambda root: None)
    monkeypatch.setattr(controller, "_no_active_workers", lambda root: None)
    calls = []
    phases = {"eligible": eligible, "fail_audit": False, "stale": False}

    def stage(role):
        def build(output, dependencies):
            product = output / role.upper()
            product.mkdir(parents=True)
            save(product / "MATERIALIZATION.json", {"synthetic": role})
            return {"synthetic": role}

        return Stage(role, (), build, lambda output, result: None)

    def run_service(**kwargs):
        calls.append(kwargs)
        parent = kwargs["parent"]
        parent.mkdir(parents=True)
        (parent / "worker.log").write_text("simulated worker, no runtime qualification\n")
        request = kwargs["job"]
        started = controller.now()
        if kwargs["action"] == "materialize":
            result = StageRunner(
                root=Path(request["work_root"]),
                sources=[],
                binding=binding(),
                stages=[stage(r) for r in ("period", "windows", "ods", "dwd")],
            ).execute()
            return {
                "stage": "materialize",
                "stages": {r: {"output": str(v["path"])} for r, v in result.items()},
            }, started
        output = Path(request["work_root"])
        output.mkdir()
        if phases["fail_audit"]:
            save(output / "FAILURE.json", {"reason_code": "SYNTHETIC_AUDIT_FAILURE"})
            raise BoundedError("SYNTHETIC_AUDIT_FAILURE")
        receipt = {
            "publication_eligible": phases["eligible"],
            "input_identity": frozen_id,
            "synthetic": "no-data-acceptance",
        }
        receipt["identity"] = identity(receipt)
        save(output / "AUDIT.json", receipt)
        completion = {
            "contract": "p10-standard-base-audit-execution-v1",
            "job_identity": identity(request),
            "work_root": request["work_root"],
            "code_revision": request["builder_revision"],
            "policy_identity": identity(kwargs["contract"]),
            "environment_identity": environment_id,
            "audit_sha256": file_sha(output / "AUDIT.json"),
            "audit_identity": receipt["identity"],
            "started_at": "2020-01-01T00:00:00+00:00" if phases["stale"] else started,
            "finished_at": controller.now(),
            "publication_eligible": phases["eligible"],
            "formal_READY": False,
        }
        completion["identity"] = identity(completion)
        save(output / "COMPLETION.json", completion)
        return {"stage": "audit", "execution_identity": completion["identity"]}, started

    monkeypatch.setattr(controller, "run_service", run_service)
    monkeypatch.setattr(
        controller, "pair_documents", lambda **kwargs: {r: {"synthetic": r} for r in ("ods", "dwd")}
    )
    monkeypatch.setattr(controller, "verify_pair_seal", lambda *args: None)
    monkeypatch.setattr(
        controller,
        "verify_pair_registration",
        lambda ods, dwd: {
            "identity": "sha256:" + "a" * 64,
            "materializations": {
                "ods": file_sha(ods / "MATERIALIZATION.json"),
                "dwd": file_sha(dwd / "MATERIALIZATION.json"),
            },
        },
    )
    return job, contract, execution, calls, phases, protected


def test_controller_orders_graph_transfer_protection_fresh_audit_markers_and_sealed_audit(
    tmp_path, monkeypatch
):
    job, contract, execution, calls, _, protected = context(tmp_path, monkeypatch)
    result = controller.execute(job=job, contract=contract, execution=execution)
    assert [c["action"] for c in calls] == ["materialize", "audit", "audit"]
    root = Path(job["work_root"])
    assert root / "published" in protected and root / "stages" in protected
    originals = set(json.loads(Path(job["roots"]).read_bytes()).values())
    for call in calls[1:]:
        denied = set(map(str, call["denied"]))
        assert originals <= denied and str(root / "stages") in denied
        assert set(execution["protected_paths"]) <= denied
        assert set(map(str, call["readonly"])) == {
            str(root / "published/ODS"),
            str(root / "published/DWD"),
        }
    assert result["formal_READY"] is True and result["full_A_B_C"] is False
    pair = (root / "PAIR-READY.json").read_bytes()
    receipts = {
        r: (root / f"stages/{r}/attempt-000001/SUCCESS.json").read_bytes() for r in ("ods", "dwd")
    }
    result2 = controller.execute(job=job, contract=contract, execution=execution)
    assert [c["action"] for c in calls] == ["materialize", "audit", "audit", "audit"]
    assert (root / "PAIR-READY.json").read_bytes() == pair
    assert result2["pair_completion_identity"] == result["pair_completion_identity"]
    for role, value in receipts.items():
        assert (root / f"stages/{role}/attempt-000001/SUCCESS.json").read_bytes() == value


def test_pending_historical_gate_never_writes_markers_and_reruns_audit_without_rematerializing(
    tmp_path, monkeypatch
):
    job, contract, execution, calls, _, _ = context(tmp_path, monkeypatch, eligible=False)
    for _ in range(2):
        result = controller.execute(job=job, contract=contract, execution=execution)
        assert result["formal_READY"] is False and result["status"].endswith(
            "HISTORICAL_GATE_PENDING"
        )
    root = Path(job["work_root"])
    assert not (root / "PAIR-READY.json").exists()
    assert not list((root / "published").rglob("READY"))
    assert [c["action"] for c in calls] == ["materialize", "audit", "audit"]
    assert calls[1]["job"]["work_root"] != calls[2]["job"]["work_root"]
    assert str(Path(calls[1]["job"]["work_root"]).parent) in set(map(str, calls[2]["denied"]))


@pytest.mark.parametrize("fault", ["fail_audit", "stale"])
def test_failed_or_stale_audit_is_retained_and_recovery_requires_a_new_worker(
    tmp_path, monkeypatch, fault
):
    job, contract, execution, calls, phases, _ = context(tmp_path, monkeypatch)
    phases[fault] = True
    with pytest.raises(
        BoundedError, match="SYNTHETIC_AUDIT_FAILURE|CURRENT_AUDIT_COMPLETION_REQUIRED"
    ):
        controller.execute(job=job, contract=contract, execution=execution)
    root = Path(job["work_root"])
    assert len(list((root / "controller").glob("run-*/FAILURE.json"))) == 1
    assert not list((root / "published").rglob("READY"))
    old = calls[-1]["job"]["work_root"]
    phases[fault] = False
    controller.execute(job=job, contract=contract, execution=execution)
    assert sum(c["action"] == "materialize" for c in calls) == 1
    assert old != calls[-1]["job"]["work_root"]
    assert Path(old).exists()


def test_interrupted_marker_link_recovers_before_strict_transfer_census(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base_build_publication

    job, contract, execution, calls, _, _ = context(tmp_path, monkeypatch)
    link = os.link

    def crash(source, target):
        link(source, target)
        if Path(target).name == "READY":
            raise RuntimeError("marker crash after link")

    with monkeypatch.context() as patch:
        patch.setattr(base_build_publication.os, "link", crash)
        with pytest.raises(RuntimeError, match="marker crash"):
            controller.execute(job=job, contract=contract, execution=execution)
    assert any(
        p.stat().st_nlink == 2 for p in (Path(job["work_root"]) / "published").rglob("READY")
    )
    controller.execute(job=job, contract=contract, execution=execution)
    assert [c["action"] for c in calls] == ["materialize", "audit", "audit", "audit"]
    assert list((Path(job["work_root"]) / "publication-writes").rglob("archived-final-markers"))


def test_service_namespace_keeps_scratch_visible_under_private_tmp_and_denies_derived_inputs(
    tmp_path,
):
    execution = json.loads(
        Path("config/pipelines/base-build-execution.example.v1.json").read_bytes()
    )
    execution["runtime_python"] = sys.executable
    job = {"environment_lock": "/opt/base/environment.json", "wheelhouse": "/data/runtime/wheels"}
    args = controller.service_arguments(
        action="audit",
        job=job,
        execution=execution,
        repository=Path("/code"),
        job_path=Path("/controls/job.json"),
        contract_path=Path("/controls/contract.json"),
        readonly=[Path("/data/products/ODS"), Path("/data/products/DWD")],
        denied=[Path("/data/original"), Path("/data/work/stages")],
        writable=Path("/var/tmp/job-audit"),
        unit="unit",
    )
    assert "PrivateNetwork=yes" in args and "ProtectSystem=strict" in args
    assert "PrivateTmp=yes" in args and "BindPaths=/var/tmp/job-audit:/var/tmp/job-audit" in args
    assert "ReadWritePaths=/var/tmp/job-audit" in args and "CapabilityBoundingSet=" in args
    assert "InaccessiblePaths=-/data/original -/data/work/stages" in args
    assert any(a.startswith("BindReadOnlyPaths=") and "/data/products/ODS" in a for a in args)
    assert "User=agentdock" in args


def test_active_or_unreadable_previous_unit_blocks_reentry(tmp_path, monkeypatch):
    controls = tmp_path / "controller"
    path = controls / "run-old/audit-before/INVOCATION.json"
    save(path, {"unit": "quanthus-base-build-" + "a" * 32 + "-audit"})
    for output, reason in (
        ("LoadState=loaded\nActiveState=active\n", "STILL_RUNNING"),
        ("", "STATE_UNREADABLE"),
    ):
        monkeypatch.setattr(
            controller.subprocess,
            "run",
            lambda *args, response=output, **kwargs: SimpleNamespace(stdout=response, returncode=0),
        )
        with pytest.raises(BoundedError, match=reason):
            controller._no_active_workers(controls)
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="LoadState=not-found\nActiveState=inactive\n", returncode=1
        ),
    )
    controller._no_active_workers(controls)


def test_atomic_controller_control_write_preserves_partial_and_rejects_unknown_final(tmp_path):
    path = tmp_path / "CONTROL.json"
    partial = tmp_path / ".CONTROL.json.partial"
    partial.write_bytes(b'{"partial":')
    controller.control_record(path, {"current": True})
    assert list((tmp_path / "interrupted-controls").iterdir())[0].read_bytes() == b'{"partial":'
    os.link(path, partial)
    controller.control_record(path, {"current": True})
    assert path.stat().st_nlink == 1 and not partial.exists()
    with pytest.raises(BoundedError, match="CONTROL_CONFLICT_PRESERVED"):
        controller.control_record(path, {"current": False})
    assert json.loads(path.read_bytes()) == {"current": True}


@pytest.mark.parametrize(
    "fault", ["schema", "empty-denial", "unsafe-path", "root-user", "connected", "not-root"]
)
def test_invalid_execution_fails_before_the_job_is_created(tmp_path, monkeypatch, fault):
    job, contract, execution, _, _, _ = context(tmp_path, monkeypatch)
    if fault == "schema":
        execution["allow_sources"] = True
    elif fault == "empty-denial":
        execution["protected_paths"] = []
    elif fault == "unsafe-path":
        execution["audit_parent"] = "/data/has%specifier"
    elif fault == "root-user":
        monkeypatch.setattr(controller.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=0))
    elif fault == "connected":
        monkeypatch.setattr(controller.socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    else:
        monkeypatch.setattr(controller.os, "geteuid", lambda: 1001)
    with pytest.raises(BoundedError, match="BUILD_EXECUTION_|BUILD_CONTROLLER_"):
        controller.execute(job=job, contract=contract, execution=execution)
    assert not Path(job["work_root"]).exists()


def test_controller_job_lock_prevents_two_simultaneous_drivers(tmp_path):
    with (
        controller.controller_lock(tmp_path / "job"),
        pytest.raises(BoundedError, match="ALREADY_RUNNING"),
        controller.controller_lock(tmp_path / "job"),
    ):
        pytest.fail("duplicate lock acquired")


def test_cli_requires_an_execution_contract_for_build_and_rejects_it_for_other_actions():
    with pytest.raises(BoundedError, match="EXECUTION_CONTRACT_REQUIRED"):
        entry.run(action="build", job={}, contract={})
    with pytest.raises(BoundedError, match="EXECUTION_ONLY_FOR_BUILD"):
        entry.run(action="audit", job={}, contract={}, execution={})


@pytest.mark.parametrize("after_link", [False, True])
def test_interrupted_transfer_intent_keeps_original_success_and_recovers(
    tmp_path, monkeypatch, after_link
):
    from quanthus.pipelines.securities_warehouse import base_build_publication as publication
    from quanthus.pipelines.securities_warehouse.base_build_inputs import canonical

    job, contract, execution, calls, _, _ = context(tmp_path, monkeypatch)
    original = publication._save_new

    def crash(path, value):
        if path.name == "PUBLICATION.json":
            partial = path.with_name(".PUBLICATION.json.partial")
            partial.write_bytes(canonical(value) + b"\n")
            if after_link:
                os.link(partial, path)
            raise RuntimeError("intent interruption")
        original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(publication, "_save_new", crash)
        with pytest.raises(RuntimeError, match="intent interruption"):
            controller.execute(job=job, contract=contract, execution=execution)
    root = Path(job["work_root"])
    receipts = {
        role: (root / f"stages/{role}/attempt-000001/SUCCESS.json").read_bytes()
        for role in ("ods", "dwd")
    }
    controller.execute(job=job, contract=contract, execution=execution)
    assert (root / "PUBLICATION.json").stat().st_nlink == 1
    for role, original_bytes in receipts.items():
        assert (root / f"stages/{role}/attempt-000001/SUCCESS.json").read_bytes() == original_bytes
    assert sum(c["action"] == "materialize" for c in calls) == (1 if after_link else 2)
    if not after_link:
        assert list((root / "controller").glob("interrupted-intent-*"))


def test_parent_completion_link_interruption_keeps_one_completion_and_freshly_reaudits(
    tmp_path, monkeypatch
):
    from quanthus.pipelines.securities_warehouse.base_build_inputs import canonical

    job, contract, execution, calls, _, _ = context(tmp_path, monkeypatch)
    original = controller._save_new

    def crash(path, value):
        if path.name == "PAIR-READY.json":
            partial = path.with_name(".PAIR-READY.json.partial")
            partial.write_bytes(canonical(value) + b"\n")
            os.link(partial, path)
            raise RuntimeError("parent completion interruption")
        original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(controller, "_save_new", crash)
        with pytest.raises(RuntimeError, match="parent completion interruption"):
            controller.execute(job=job, contract=contract, execution=execution)
    path = Path(job["work_root"]) / "PAIR-READY.json"
    before = path.read_bytes()
    controller.execute(job=job, contract=contract, execution=execution)
    assert path.read_bytes() == before and path.stat().st_nlink == 1
    assert [c["action"] for c in calls] == ["materialize", "audit", "audit", "audit"]


def test_unknown_initial_job_directory_is_preserved(tmp_path, monkeypatch):
    job, contract, execution, calls, _, _ = context(tmp_path, monkeypatch)
    root = Path(job["work_root"])
    root.mkdir()
    path = root / "unrelated.bin"
    path.write_bytes(b"preserve")
    with pytest.raises(BoundedError, match="UNKNOWN_INITIAL_JOB_PRESERVED"):
        controller.execute(job=job, contract=contract, execution=execution)
    assert path.read_bytes() == b"preserve" and not calls


def test_protecting_failed_attempts_never_changes_a_hardlinked_original_inode(
    tmp_path, monkeypatch
):
    original = tmp_path / "original.bin"
    original.write_bytes(b"original")
    failed = tmp_path / "failed"
    failed.mkdir()
    linked = failed / "unqualified-link.bin"
    os.link(original, linked)
    changes = []
    monkeypatch.setattr(
        controller.os, "chown", lambda path, *args, **kwargs: changes.append(Path(path))
    )
    monkeypatch.setattr(
        controller.os, "chmod", lambda path, *args, **kwargs: changes.append(Path(path))
    )
    controller.protect_tree(failed, strict=False)
    assert linked not in changes and original not in changes
    assert original.read_bytes() == b"original" and os.path.samefile(linked, original)
    with pytest.raises(BoundedError, match="PRODUCT_NOT_INDEPENDENT"):
        controller.protect_tree(failed, strict=True)


def test_runtime_fixture_generation_restores_mount_flags_and_denies_all_seed_only_inputs(tmp_path):
    from tests.base_build_controller_fixture import prepare

    before = os.statvfs(tmp_path).f_flag
    paths = prepare(
        root=tmp_path / "synthetic-inputs",
        job_root=tmp_path / "runtime-job",
        audit_parent=tmp_path / "audit",
        revision="a" * 40,
        runtime_python=sys.executable,
        runtime_user="agentdock",
        wheelhouse="/runtime/wheels",
        protected=["/production"],
    )
    job = json.loads(Path(paths["job"]).read_bytes())
    assert os.statvfs(tmp_path).f_flag == before
    roots = [Path(p) for p in json.loads(Path(job["roots"]).read_bytes()).values()]
    execution = json.loads(Path(paths["execution"]).read_bytes())
    denied = [Path(p) for p in execution["protected_paths"]]
    assert any(p.name == "original-wire-generation" for p in denied)
    assert any(p.name == "legacy-ods-generation" for p in denied)
    assert all(not (p == r or p in r.parents or r in p.parents) for p in denied for r in roots)
    assert not Path(job["work_root"]).exists()
    assert (
        json.loads(Path(job["frozen"]).read_bytes())["content"]["selection"]["through_date"]
        == "2026-10-09"
    )
