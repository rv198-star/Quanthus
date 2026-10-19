"""Synthetic ODS Base 2.4 source closure and cold-copy acceptance."""

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.pipelines.securities_warehouse import (
    base24_native5m_acquire as acquisition_module,
)
from quanthus.pipelines.securities_warehouse import (
    base24_ods_image as ods_module,
)
from quanthus.pipelines.securities_warehouse import (
    base24_ods_transport as transport_module,
)
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
    BUCKETS,
    run_acquisition,
)
from quanthus.pipelines.securities_warehouse.base24_ods_image import (
    Base24OdsError,
    audit_ods24,
    freeze_ods24_plan,
    materialize_ods24,
    seal_ods24,
)
from quanthus.pipelines.securities_warehouse.base24_ods_transport import (
    physical_metadata,
    prepare_sealed_copy_inventory,
    verify_restored_fault_copy,
)
from quanthus.pipelines.securities_warehouse.base24_scope import select_default_members
from quanthus.providers.native_5m_exact import ExactBar

POLICY = Path("config/pipelines/base24-default-frequency.v1.json").read_bytes()
ODS24_CONTRACT = Path("config/pipelines/base24-ods.v1.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


class Budget:
    def check(self, **kwargs):
        pass

    def begin_stage(self, name):
        pass


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical(value) + b"\n")


def _make_predecessor(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "ods23"
    root.mkdir()
    five = root / "payload/five.parquet"
    one = root / "payload/one.parquet"
    five.parent.mkdir()
    five.write_bytes(b"retained-five")
    one.write_bytes(b"optional-one")

    def record(path: str, dataset: str) -> dict[str, object]:
        member = root / path
        return {
            "path": path,
            "sha256": file_sha(member),
            "size_bytes": member.stat().st_size,
            "dataset_id": dataset,
            "role": "historical_seed",
            "source_id": "placeholder",
            "format": "raw",
        }

    seed_files = [
        record("payload/five.parquet", "minute_5m_formal_archive"),
        record("payload/one.parquet", "minute_1m_research_archive"),
    ]
    for row in seed_files:
        row.pop("source_id")
    seed = {"content": {"files": seed_files}}
    seed_bytes = canonical(seed)
    seed_sha = hashlib.sha256(seed_bytes).hexdigest()
    monthly_files = []
    for row in seed_files:
        monthly_files.append({**row, "source_id": seed_sha})
    content = {
        "through_date": "2026-09-30",
        "files": monthly_files,
        "source_inventory": [{"role": "historical_seed", "manifest_sha256": "sha256:" + seed_sha}],
    }
    manifest = {
        "schema_version": 1,
        "kind": "quanthus_ods_base_bundle",
        "bundle_id": "synthetic-ods23",
        "content": content,
        "content_digest": digest(content),
    }
    _write_json(root / "manifest.json", manifest)
    (root / "READY").write_text(file_sha(root / "manifest.json") + "\n")
    selected = select_default_members((root / "manifest.json").read_bytes(), seed_bytes, POLICY)
    wrapper = {
        "contract": "p10-base24-ods23-default-member-selection-v1",
        "identity": digest(selected),
        "selection": selected,
        "source_manifest_sha256": file_sha(root / "manifest.json"),
        "source_modified": False,
    }
    selection_path = tmp_path / "selection/selection.json"
    selection_path.parent.mkdir()
    _write_json(selection_path, wrapper)
    return root, selection_path


def _make_acquisition(tmp_path: Path) -> tuple[Path, Path]:
    plan_path = tmp_path / "denominator/missing-window-plan.json"
    plan_path.parent.mkdir()
    identity = hashlib.sha256(b"SSE\t600000\t2026-09-30\t2026-09-30").hexdigest()
    plan = {
        "contract": "p10-base24-native5m-missing-window-plan-v1",
        "frequency": "5",
        "adjustflag": "3",
        "business_through": "2026-09-30",
        "missing_security_days": 1,
        "windows": [
            {
                "partition_id": identity,
                "venue": "SSE",
                "code": "sh.600000",
                "start_date": "2026-09-30",
                "end_date": "2026-09-30",
                "required_security_days": 1,
                "expected_source_days": 1,
                "required_dates": ["2026-09-30"],
                "expected_session_dates": ["2026-09-30"],
                "maximum_rows": 48,
            }
        ],
    }
    _write_json(plan_path, plan)
    root = tmp_path / "acquisition"

    def fetch(staging, request):
        wire = staging / "wire"
        wire.mkdir()
        (wire / "response-0001.wire").write_bytes(b"original-supplier-wire")
        (wire / "wire-receipt.json").write_text('{"records":[]}\n')
        bars = tuple(
            ExactBar(
                request.code,
                datetime.fromisoformat(f"2026-09-30T{bucket}+08:00"),
                ("1.00000000001", "2", "0", "1.5", "0", "0"),
            )
            for bucket in BUCKETS
        )
        return bars, {
            "actual_requests": 1,
            "actual_bytes": 22,
            "elapsed_seconds": 0.1,
            "actually_observed_at": "2026-10-16T08:00:00+00:00",
            "provider_original_acquisition": None,
            "frequency": "5",
            "adjustflag": "3",
        }

    result = run_acquisition(
        plan_path.read_bytes(), root, fetch, source_roots=(tmp_path / "unrelated-source",)
    )
    assert result["status"] == "COMPLETE"
    return root, plan_path


def _make_ledger(tmp_path: Path, acquisition: Path) -> Path:
    root = tmp_path / "ledger"
    root.mkdir()
    result = {"status": "PASS", "R3_security_days": 0, "source_prices_exported": False}
    _write_json(root / "result.safe.json", result)
    manifest = {
        "contract": "p10-base24-native5m-coverage-ledger-package-v1",
        "status": "PASS",
        "inputs": {
            "acquisition_READY_SHA256": file_sha(acquisition / "READY").removeprefix("sha256:")
        },
        "files": [
            {
                "path": "result.safe.json",
                "size_bytes": (root / "result.safe.json").stat().st_size,
                "sha256": file_sha(root / "result.safe.json").removeprefix("sha256:"),
            }
        ],
    }
    _write_json(root / "manifest.json", manifest)
    (root / "READY").write_text(file_sha(root / "manifest.json").removeprefix("sha256:") + "\n")
    return root


@pytest.fixture
def source_closure(tmp_path):
    predecessor, selection = _make_predecessor(tmp_path)
    acquisition, window_plan = _make_acquisition(tmp_path)
    ledger = _make_ledger(tmp_path, acquisition)
    return predecessor, selection, acquisition, window_plan, ledger


def _freeze(source_closure):
    predecessor, selection, acquisition, window_plan, ledger = source_closure
    return freeze_ods24_plan(
        predecessor_root=predecessor,
        selection_path=selection,
        acquisition_root=acquisition,
        window_plan_path=window_plan,
        ledger_root=ledger,
        contract_path=ODS24_CONTRACT,
    )


@pytest.fixture
def transport_image(source_closure, tmp_path):
    root = tmp_path / "transport-image"
    plan = _freeze(source_closure)
    receipt = materialize_ods24(plan, root, Budget())
    seal_ods24(root, receipt, code_revision="b" * 40, budget=Budget())
    return root


def test_freeze_does_not_hash_verified_acquisition_payload_twice(source_closure, monkeypatch):
    acquisition = source_closure[2]
    calls = {}

    def track(function):
        def hashed(path):
            key = str(path)
            calls[key] = calls.get(key, 0) + 1
            return function(path)

        return hashed

    monkeypatch.setattr(acquisition_module, "_sha", track(acquisition_module._sha))
    monkeypatch.setattr(ods_module, "file_sha", track(ods_module.file_sha))
    _freeze(source_closure)
    payloads = [*acquisition.rglob("exact.parquet"), *acquisition.rglob("*.wire")]
    assert payloads
    assert {str(path): calls[str(path)] for path in payloads} == {
        str(path): 1 for path in payloads
    }


def test_acquisition_mutation_during_validation_is_not_cached(source_closure, monkeypatch):
    original = ods_module._verify_sealed

    def changed(root, window, plan_sha):
        result = original(root, window, plan_sha)
        path = root / "wire/response-0001.wire"
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        return result

    monkeypatch.setattr(ods_module, "_verify_sealed", changed)
    with pytest.raises(Base24OdsError, match="PARTITION_CLOSURE_INVALID"):
        _freeze(source_closure)


def test_verified_acquisition_hash_rejects_later_change(source_closure):
    acquisition, window_plan = source_closure[2:4]
    verified = {}
    ods_module._validate_acquisition(acquisition, window_plan, verified=verified)
    path = next(acquisition.rglob("*.wire"))
    original, stamp = path.read_bytes(), path.stat()
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(Base24OdsError, match="SOURCE_CHANGED"):
        ods_module._source_member(
            acquisition, path.relative_to(acquisition).as_posix(), "raw", "payload/raw",
            verified=verified[str(path)],
        )


def test_process_workers_preserve_verified_inventory_and_plan(source_closure, monkeypatch):
    serial = _freeze(source_closure)
    monkeypatch.setattr(ods_module, "VERIFY_BATCH", 1)
    monkeypatch.setattr(ods_module, "ACQUISITION_WORKERS", 2)
    assert _freeze(source_closure) == serial


def test_materialization_and_seal_have_one_full_readback(source_closure, tmp_path, monkeypatch):
    plan = _freeze(source_closure)
    original = ods_module.audit_ods24
    audits = []

    def audited(*args, **kwargs):
        audits.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(ods_module, "audit_ods24", audited)
    output = tmp_path / "one-readback"
    receipt = materialize_ods24(plan, output, Budget())
    assert audits == []
    seal_ods24(output, receipt, code_revision="c" * 40, budget=Budget())
    assert audits == [output]


def test_retirement_reuses_verified_plan_without_source_byte_reads(
    transport_image, source_closure, monkeypatch
):
    plan = json.loads((transport_image / "evidence/MATERIALIZATION.json").read_bytes())
    root = source_closure[2]
    expected_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path == root or root in path.parents:
            pytest.fail("Retirement must not read already verified source bytes")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    inventory = transport_module.prepare_source_retirement_inventory(root, plan=plan)
    assert set(inventory) == expected_paths
    assert all(row["sha256"].startswith("sha256:") for row in inventory.values())


def test_retirement_distinguishes_explicit_discard_from_verified_recovery(
    transport_image, source_closure, monkeypatch
):
    plan = json.loads((transport_image / "evidence/MATERIALIZATION.json").read_bytes())
    root = source_closure[0]
    discard = {}
    for relative in ("payload/one.parquet", "manifest.json", "READY"):
        path = root / relative
        discard[relative] = {"size_bytes": path.stat().st_size, "sha256": file_sha(path)}
    original = Path.open

    def guarded(path, *args, **kwargs):
        if root in path.parents:
            pytest.fail("Retirement must not hash the source again")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    result = transport_module.prepare_source_retirement_inventory(root, plan=plan, discard=discard)
    assert result["payload/one.parquet"]["verification"] == "EXPLICIT_DISCARD_DECLARED_HASH_ONLY"
    assert result["payload/five.parquet"]["verification"] == (
        "COPY_AND_SEAL_VERIFIED_UNCHANGED_FINGERPRINT"
    )


@pytest.mark.parametrize("damage", ["bytes", "extra", "missing", "symlink", "plan"])
def test_retirement_rejects_changed_source_or_plan(transport_image, source_closure, damage):
    plan = json.loads((transport_image / "evidence/MATERIALIZATION.json").read_bytes())
    root = source_closure[2]
    member = next(root.rglob("*.wire"))
    if damage == "bytes":
        original, stamp = member.read_bytes(), member.stat()
        member.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        os.utime(member, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    elif damage == "extra":
        (root / "extra").write_bytes(b"unexpected")
    elif damage == "missing":
        member.unlink()
    elif damage == "symlink":
        member.unlink()
        member.symlink_to(transport_image / "payload/five.parquet")
    else:
        plan["files"][0]["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(RuntimeError):
        transport_module.prepare_source_retirement_inventory(root, plan=plan)


def test_transport_reuses_pinned_hashes_but_cold_audit_still_reads_bytes(transport_image):
    root = transport_image
    expected = prepare_sealed_copy_inventory(
        root, expected_manifest_sha256=file_sha(root / "manifest.json")
    )
    member = root / "payload/five.parquet"
    expected_hash = expected["payload/five.parquet"]["sha256"]
    assert file_sha(member) == expected_hash
    original = member.read_bytes()
    member.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    # A metadata inventory is never a completed audit. Byte validation remains
    # owned by the hashing copy and by each independent cold verifier.
    reused = prepare_sealed_copy_inventory(
        root, expected_manifest_sha256=file_sha(root / "manifest.json")
    )
    assert reused["payload/five.parquet"]["sha256"] == expected_hash != file_sha(member)
    with pytest.raises(Base24OdsError, match="PHYSICAL_BYTES"):
        audit_ods24(root, Budget())


@pytest.mark.parametrize("damage", ["seal", "missing", "extra", "symlink"])
def test_transport_rejects_identity_or_namespace_change(transport_image, damage):
    root = transport_image
    pinned = file_sha(root / "manifest.json")
    if damage == "seal":
        (root / "manifest.json").write_bytes((root / "manifest.json").read_bytes() + b" ")
    elif damage == "missing":
        (root / "payload/five.parquet").unlink()
    elif damage == "extra":
        (root / "extra").write_bytes(b"extra")
    else:
        (root / "extra").symlink_to(root / "payload/five.parquet")
    with pytest.raises(RuntimeError):
        prepare_sealed_copy_inventory(root, expected_manifest_sha256=pinned)


def test_fault_copy_canary_restore_and_untouched_file_guard(transport_image):
    root = transport_image
    audit_ods24(root, Budget())
    before = physical_metadata(root)
    canary = "payload/five.parquet"
    path = root / canary
    original, expected_hash = path.read_bytes(), file_sha(path)
    path.unlink()
    path.write_bytes(original)
    verify_restored_fault_copy(root, before=before, canary=canary, expected_sha256=expected_hash)
    other = root / "payload/source-plan/base24-ods-contract.json"
    stamp = other.stat()
    raw = other.read_bytes()
    other.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    # Same size and restored mtime cannot hide a mutation from the ctime guard.
    os.utime(other, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(Base24OdsError, match="UNTOUCHED_MEMBER_CHANGED"):
        verify_restored_fault_copy(
            root, before=before, canary=canary, expected_sha256=expected_hash
        )


def test_fault_copy_rejects_incorrect_canary_restore(transport_image):
    root = transport_image
    before = physical_metadata(root)
    canary = "payload/five.parquet"
    path = root / canary
    original, expected_hash = path.read_bytes(), file_sha(path)
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(Base24OdsError, match="FAULT_RESTORATION_FAILED"):
        verify_restored_fault_copy(
            root, before=before, canary=canary, expected_sha256=expected_hash
        )


def _supplemental_closure(tmp_path: Path, acquisition: Path, ledger: Path) -> Path:
    root = tmp_path / "supplemental"
    root.mkdir()
    exact = root / "partition/exact.parquet"
    exact.parent.mkdir()
    exact.write_bytes(b"qualified-external-five-minute-source")
    metadata = root / "manifest.json"
    _write_json(metadata, {"contract": "synthetic-supplemental-source-v1"})
    members = []
    for member in (exact, metadata):
        members.append(
            {
                "source_path": str(member),
                "source_root_archival": str(root),
                "source_relative": member.relative_to(root).as_posix(),
                "size_bytes": member.stat().st_size,
                "sha256": file_sha(member),
            }
        )
    closure = {
        "contract": "p10-base24-native5m-supplemental-source-closure-v1",
        "primary_acquisition_root_archival": str(acquisition.resolve()),
        "ledger_manifest_sha256": file_sha(ledger / "manifest.json"),
        "required_exact_source_paths": [str(exact)],
        "members": members,
    }
    closure["identity"] = digest(closure)
    path = tmp_path / "source-plan/supplemental-closure.json"
    path.parent.mkdir()
    _write_json(path, closure)
    return path


def test_ods24_keeps_wire_and_excludes_optional_1m(source_closure, tmp_path):
    plan = _freeze(source_closure)
    paths = {row["path"] for row in plan["files"]}
    assert "payload/five.parquet" in paths
    assert "payload/one.parquet" not in paths
    assert any(path.endswith("wire/response-0001.wire") for path in paths)
    assert plan["default_frequencies"] == ["1d", "5m"]
    assert plan["optional_1m"] == "NOT_INCLUDED"

    output = tmp_path / "output/ods24"
    output.parent.mkdir()
    receipt = materialize_ods24(plan, output, Budget())
    manifest = seal_ods24(output, receipt, code_revision="a" * 40, budget=Budget())
    assert manifest["kind"] == "quanthus_ods_base24_bundle"
    assert (output / "READY").read_text().strip() == file_sha(output / "manifest.json")
    assert audit_ods24(output, Budget())["status"] == "PASS"


def test_ods24_materializes_qualified_supplemental_source_closure(source_closure, tmp_path):
    predecessor, selection, acquisition, window_plan, ledger = source_closure
    closure = _supplemental_closure(tmp_path, acquisition, ledger)
    plan = freeze_ods24_plan(
        predecessor_root=predecessor,
        selection_path=selection,
        acquisition_root=acquisition,
        window_plan_path=window_plan,
        ledger_root=ledger,
        contract_path=ODS24_CONTRACT,
        supplemental_closure_path=closure,
    )
    supplemental = [row for row in plan["files"] if row["role"] == "native5m_supplemental_source"]
    assert len(supplemental) == 2
    assert plan["native5m"]["supplemental_required_exact_sources"] == 1

    output = tmp_path / "output-supplemental/ods24"
    output.parent.mkdir()
    receipt = materialize_ods24(plan, output, Budget())
    seal_ods24(output, receipt, code_revision="d" * 40, budget=Budget())
    shutil.rmtree(tmp_path / "supplemental")
    closure.unlink()
    cold = audit_ods24(output, Budget())
    assert cold["supplemental_native5m_members"] == 2
    assert cold["source_roots_dereferenced"] is False


def test_ods24_rejects_incomplete_supplemental_source_closure(source_closure, tmp_path):
    predecessor, selection, acquisition, window_plan, ledger = source_closure
    closure_path = _supplemental_closure(tmp_path, acquisition, ledger)
    closure = json.loads(closure_path.read_bytes())
    closure["members"] = closure["members"][1:]
    closure.pop("identity")
    closure["identity"] = digest(closure)
    _write_json(closure_path, closure)
    with pytest.raises(Base24OdsError, match="SUPPLEMENTAL_SOURCE_CLOSURE_INVALID"):
        freeze_ods24_plan(
            predecessor_root=predecessor,
            selection_path=selection,
            acquisition_root=acquisition,
            window_plan_path=window_plan,
            ledger_root=ledger,
            contract_path=ODS24_CONTRACT,
            supplemental_closure_path=closure_path,
        )


def test_cold_audit_does_not_reopen_source_roots(source_closure, tmp_path):
    plan = _freeze(source_closure)
    output = tmp_path / "output/ods24"
    output.parent.mkdir()
    receipt = materialize_ods24(plan, output, Budget())
    seal_ods24(output, receipt, code_revision="b" * 40, budget=Budget())
    for source in source_closure:
        if source.is_dir():
            shutil.move(source, source.with_name(source.name + ".removed"))
        elif source.exists():
            source.rename(source.with_name(source.name + ".removed"))
    assert audit_ods24(output, Budget())["source_roots_dereferenced"] is False


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_cold_audit_fails_closed_on_member_damage(source_closure, tmp_path, damage):
    plan = _freeze(source_closure)
    output = tmp_path / "output/ods24"
    output.parent.mkdir()
    receipt = materialize_ods24(plan, output, Budget())
    seal_ods24(output, receipt, code_revision="c" * 40, budget=Budget())
    member = output / "payload/five.parquet"
    if damage == "missing":
        member.unlink()
        reason = "PHYSICAL_FILE_SET"
    else:
        member.write_bytes(b"changed")
        reason = "PHYSICAL_BYTES"
    with pytest.raises(Base24OdsError, match=reason):
        audit_ods24(output, Budget())


def test_seal_performs_full_readback_before_ready(source_closure, tmp_path):
    plan = _freeze(source_closure)
    output = tmp_path / "output-before-seal/ods24"
    output.parent.mkdir()
    receipt = materialize_ods24(plan, output, Budget())
    (output / "payload/five.parquet").write_bytes(b"changed-after-copy")
    with pytest.raises(Base24OdsError, match="PHYSICAL_BYTES"):
        seal_ods24(output, receipt, code_revision="e" * 40, budget=Budget())
    assert not (output / "READY").exists()


def test_changed_selection_and_symlink_output_fail_before_ready(source_closure, tmp_path):
    predecessor, selection, acquisition, window_plan, ledger = source_closure
    changed = json.loads(selection.read_bytes())
    changed["selection"]["included"][0]["size_bytes"] += 1
    _write_json(selection, changed)
    with pytest.raises(Base24OdsError, match="SELECTION_MEMBER_SET"):
        freeze_ods24_plan(
            predecessor_root=predecessor,
            selection_path=selection,
            acquisition_root=acquisition,
            window_plan_path=window_plan,
            ledger_root=ledger,
            contract_path=ODS24_CONTRACT,
        )

    # Restore a valid closure, then prove output indirection is rejected before a write.
    predecessor, selection = _make_predecessor(tmp_path / "fresh")
    plan = freeze_ods24_plan(
        predecessor_root=predecessor,
        selection_path=selection,
        acquisition_root=acquisition,
        window_plan_path=window_plan,
        ledger_root=ledger,
        contract_path=ODS24_CONTRACT,
    )
    real = tmp_path / "real-output"
    real.mkdir()
    link = tmp_path / "linked-output"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(Exception, match="SYMLINK"):
        materialize_ods24(plan, link, Budget())
    assert not (real / "READY").exists()
