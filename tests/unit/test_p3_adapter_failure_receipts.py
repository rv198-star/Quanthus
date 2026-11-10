"""Synthetic-only P10 regressions for immutable one-shot attempt failures."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_daily_adapter_probe as probe
from quanthus.expert_strategy import p3_temporary_daily_adapter as adapter
from tests.unit.test_p3_daily_adapter_probe import package as source_package
from tests.unit.test_p3_temporary_daily_adapter import bound_request as source_request

package = source_package
bound_request = source_request


@pytest.fixture(params=[probe, adapter], ids=["probe", "adapter"])
def attempt(request, package, tmp_path, monkeypatch):
    module = request.param
    _connection, seal = package
    source = {**seal(), "node": "OCI"}
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    repo = tmp_path / "repo"
    implementation = "src/quanthus/expert_strategy/" + Path(module.__file__).name
    code = repo / implementation
    code.parent.mkdir(parents=True)
    code.write_bytes(Path(module.__file__).read_bytes())
    authority = repo / "authority.json"
    authority.write_text("{}")
    marker_code = {"code_revision": "a" * 40, "files": {implementation: probe.sha256(code)}}
    (root / "code-receipt.json").write_text(json.dumps(marker_code))
    raw = {
        "authority": {
            "original_research_request": {
                "path": "authority.json",
                "sha256": probe.sha256(authority),
            }
        },
        "nodes": {
            "OCI": {"uid": os.getuid(), "output_root": str(root), "output": str(root / "attempt")}
        },
        "packages": [source],
        "scope": {"rows_per_package": 2},
        "policy_version": "synthetic-p10-failure-receipt-v1",
        "contract": {
            "canonical_price_basis": "UNADJUSTED",
            "unavailable_reason": "P3_ECONOMIC_AVAILABILITY_UNQUALIFIED",
        },
        "lifecycle": {"kind": "content_addressed_cache", "reuse": "disabled"},
        "budgets": {
            "threads": 1,
            "memory_limit": "256MiB",
            "tmp_limit": "64MiB",
            "wall_seconds_per_node": 30,
            "output_max_bytes": 1048576,
            "output_max_bytes_per_node": 1048576,
        },
    }
    if module is probe:
        monkeypatch.setattr(probe, "_request", lambda *args: (raw, "synthetic-request"))

        def build():
            return probe.build(repo=repo, node="OCI", code_revision="a" * 40)
    else:
        monkeypatch.setattr(adapter, "request", lambda *args: (raw, [source]))

        def build():
            return adapter.build(repo, "OCI", "a" * 40)

    return module, raw, root, build


def assert_failure(root, exception_type, reason, stage):
    path = root / "failure.json"
    assert path.is_file(), "claimed attempt must have a durable terminal failure receipt"
    receipt = json.loads(path.read_bytes())
    digest = receipt.pop("result_digest")
    assert digest_value(receipt) == digest
    assert receipt["failure"]["exception_type"] == exception_type
    assert receipt["failure"]["reason"] == reason
    assert receipt["failure"]["stage"] == stage
    assert receipt["disposition"] == "REJECTED_UNQUALIFIED_PARTIAL_OUTPUT"
    assert receipt["attempt_consumed"] is True
    assert receipt["retry_authorized"] is False
    assert receipt["B_accepted"] is receipt["D_activated"] is False
    assert receipt["partial_output"]["disposition"] == "PRESERVED_UNQUALIFIED_DO_NOT_REUSE"
    assert path.stat().st_size <= 65536
    return receipt


@pytest.mark.parametrize(
    "error",
    [
        ValueError("ADAPTER_PAYLOAD_DRIFT"),
        probe.ProbeBudgetExceeded("ADAPTER_PROBE_WALL_BUDGET_EXCEEDED"),
        KeyboardInterrupt("synthetic interrupt"),
        SystemExit("synthetic exit"),
    ],
)
def test_verification_failures_have_durable_receipts_and_cannot_retry(attempt, monkeypatch, error):
    module, _raw, root, build = attempt

    def fail(_packages):
        raise error

    monkeypatch.setattr(module, "verify_packages", fail)
    original_handler = signal.getsignal(signal.SIGALRM)
    with pytest.raises(type(error)) as caught:
        build()
    assert caught.value is error
    assert signal.getsignal(signal.SIGALRM) == original_handler
    assert_failure(root, type(error).__name__, str(error), "verify_source_bytes")
    marker = (root / "attempt.json").read_bytes()
    failure = (root / "failure.json").read_bytes()
    with pytest.raises(FileExistsError):
        build()
    assert (root / "attempt.json").read_bytes() == marker
    assert (root / "failure.json").read_bytes() == failure
    assert not list(root.glob("*/receipt.json"))


def test_output_directory_failure_is_recorded(attempt, monkeypatch):
    _module, _raw, root, build = attempt
    mkdir = Path.mkdir

    def reject_output(path, *args, **kwargs):
        if path.parent == root:
            raise PermissionError("synthetic output denied")
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", reject_output)
    with pytest.raises(PermissionError, match="synthetic output denied"):
        build()
    assert_failure(root, "PermissionError", "synthetic output denied", "create_output")


def test_signal_setup_failure_is_recorded(attempt, monkeypatch):
    _module, _raw, root, build = attempt

    def reject_signal(*_args):
        raise ValueError("synthetic signal setup failure")

    monkeypatch.setattr(signal, "signal", reject_signal)
    with pytest.raises(ValueError, match="synthetic signal setup failure"):
        build()
    assert_failure(root, "ValueError", "synthetic signal setup failure", "install_wall_alarm")


def test_processing_failure_retains_partial_output_as_unqualified(attempt, monkeypatch):
    module, _raw, root, build = attempt

    def fail(*args):
        if module is adapter:
            args[2].write_bytes(b"synthetic incomplete parquet")
        raise RuntimeError("synthetic processing failure")

    monkeypatch.setattr(module, "probe_package" if module is probe else "adapt_package", fail)
    with pytest.raises(RuntimeError, match="synthetic processing failure"):
        build()
    receipt = assert_failure(
        root, "RuntimeError", "synthetic processing failure", "process_packages"
    )
    assert not list(root.glob("*/adapter-tmp-*"))
    if module is adapter:
        file = next(root.glob("*/*.parquet"))
        assert file.read_bytes() == b"synthetic incomplete parquet"
        assert receipt["partial_output"]["entries"] == [
            {"path": file.name, "kind": "file", "size_bytes": file.stat().st_size}
        ]


def test_output_budget_failure_does_not_erase_evidence_or_change_budget(attempt):
    module, raw, root, build = attempt
    key = "output_max_bytes" if module is probe else "output_max_bytes_per_node"
    raw["budgets"][key] = 1
    error_type = ValueError if module is probe else probe.ProbeBudgetExceeded
    reason = (
        "ADAPTER_OUTPUT_BUDGET_EXCEEDED"
        if module is probe
        else ("TEMPORARY_ADAPTER_OUTPUT_BUDGET_EXCEEDED")
    )
    with pytest.raises(error_type, match=reason):
        build()
    assert_failure(root, error_type.__name__, reason, "check_output_budget")
    assert raw["budgets"][key] == 1
    assert not list(root.glob("*/receipt.json"))
    if module is adapter:
        assert list(root.glob("*/*.parquet"))


def test_success_receipt_write_failure_remains_terminal(attempt, monkeypatch):
    module, _raw, root, build = attempt
    write = module._write

    def reject_receipt(path, payload):
        write(path, payload)
        if path.name == "receipt.json":
            raise OSError("synthetic success receipt fsync failure")

    monkeypatch.setattr(module, "_write", reject_receipt)
    with pytest.raises(OSError, match="synthetic success receipt fsync failure"):
        build()
    receipt = assert_failure(
        root, "OSError", "synthetic success receipt fsync failure", "write_receipt"
    )
    assert any(e["path"] == "receipt.json" for e in receipt["partial_output"]["entries"])


def test_success_and_rejected_repeat_do_not_create_failure_receipt(attempt):
    _module, _raw, root, build = attempt
    result = build()
    assert result["B_accepted"] is result["D_activated"] is False
    marker = (root / "attempt.json").read_bytes()
    with pytest.raises(FileExistsError):
        build()
    assert (root / "attempt.json").read_bytes() == marker
    assert not (root / "failure.json").exists()


def test_actual_wall_signal_records_failure_and_restores_handler(attempt, monkeypatch):
    module, _raw, root, build = attempt
    handler = signal.getsignal(signal.SIGALRM)

    def alarm(_packages):
        signal.raise_signal(signal.SIGALRM)

    monkeypatch.setattr(module, "verify_packages", alarm)
    with pytest.raises(probe.ProbeBudgetExceeded):
        build()
    assert_failure(
        root, "ProbeBudgetExceeded", "ADAPTER_PROBE_WALL_BUDGET_EXCEEDED", "verify_source_bytes"
    )
    assert signal.getsignal(signal.SIGALRM) == handler
    assert signal.alarm(0) == 0


def test_duckdb_error_preserves_exact_failure(attempt, monkeypatch):
    module, _raw, root, build = attempt

    def invalid_query(connection, *_args):
        connection.execute("SELECT synthetic_missing_field")

    monkeypatch.setattr(
        module, "probe_package" if module is probe else "adapt_package", invalid_query
    )
    with pytest.raises(Exception) as caught:
        build()
    assert type(caught.value).__name__ == "BinderException"
    assert_failure(root, "BinderException", str(caught.value), "process_packages")


def test_claim_fsync_failure_still_has_failure_receipt(attempt, monkeypatch):
    _module, _raw, root, build = attempt
    fsync = os.fsync
    calls = 0

    def fail_first(descriptor):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic claim fsync failure")
        return fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_first)
    with pytest.raises(OSError, match="synthetic claim fsync failure"):
        build()
    assert_failure(root, "OSError", "synthetic claim fsync failure", "claim_attempt")
    assert json.loads((root / "attempt.json").read_bytes())["maximum_attempts"] == 1


def test_failure_receipt_storage_error_does_not_mask_original(attempt, monkeypatch):
    module, _raw, root, build = attempt
    primary = ValueError("ADAPTER_PAYLOAD_DRIFT")
    storage = OSError("synthetic full filesystem")

    def fail(_packages):
        raise primary

    def no_receipt(*_args):
        raise storage

    monkeypatch.setattr(module, "verify_packages", fail)
    monkeypatch.setattr(probe, "_write_failure", no_receipt)
    with pytest.raises(ValueError) as caught:
        build()
    assert caught.value is primary
    assert caught.value.__cause__ is storage
    assert (root / "attempt.json").exists()
    assert not (root / "failure.json").exists()


def test_pre_claim_failure_does_not_create_attempt_or_failure(attempt):
    _module, _raw, root, build = attempt
    code = root / "code-receipt.json"
    payload = json.loads(code.read_bytes())
    payload["code_revision"] = "b" * 40
    code.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="CODE_RECEIPT_DRIFT"):
        build()
    assert not (root / "attempt.json").exists()
    assert not (root / "failure.json").exists()


def test_failure_receipt_publication_is_exclusive_and_leaves_no_temp(tmp_path):
    path = tmp_path / "failure.json"
    probe._write_failure(path, {"synthetic": "first"})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        probe._write_failure(path, {"synthetic": "second"})
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".failure-*"))


def test_output_inventory_does_not_follow_symlinks_or_read_payloads(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    (root / "link").symlink_to(tmp_path / "absent")
    (root / "payload.parquet").write_bytes(b"synthetic")
    inventory = probe._partial_output(root)
    assert inventory["inventory_complete"] is True
    assert [e["kind"] for e in inventory["entries"]] == ["symlink", "file"]


def test_adapter_receipt_size_alone_cannot_bypass_output_budget(attempt, monkeypatch):
    module, raw, root, build = attempt
    if module is probe:
        pytest.skip("probe has only a result receipt; covered by its output-budget case")
    adapt = adapter.adapt_package

    def exact_payload_budget(*args):
        row = adapt(*args)
        raw["budgets"]["output_max_bytes_per_node"] = row["size_bytes"]
        return row

    monkeypatch.setattr(adapter, "adapt_package", exact_payload_budget)
    with pytest.raises(probe.ProbeBudgetExceeded):
        build()
    assert_failure(
        root,
        "ProbeBudgetExceeded",
        "TEMPORARY_ADAPTER_OUTPUT_BUDGET_EXCEEDED",
        "check_output_budget",
    )


@pytest.mark.parametrize("sidecar_kind", ["file", "broken_symlink", "directory"])
def test_terminal_failure_sidecar_overrides_successful_diagnostic(
    bound_request, tmp_path, sidecar_kind
):
    repo, config, reseal, old_receipt = bound_request
    root = tmp_path / "diagnostic"
    output = root / "attempt"
    output.mkdir(parents=True)
    receipt = output / "receipt.json"
    receipt.write_bytes(old_receipt.read_bytes())
    config["nodes"]["OCI"]["diagnostic_receipt"] = str(receipt)
    reseal()
    assert adapter.request(repo, "OCI")[1]
    sidecar = root / "failure.json"
    if sidecar_kind == "file":
        sidecar.write_text('{"disposition":"REJECTED_UNQUALIFIED_PARTIAL_OUTPUT"}')
    elif sidecar_kind == "broken_symlink":
        sidecar.symlink_to(root / "absent")
    else:
        sidecar.mkdir()
    with pytest.raises(ValueError, match="DIAGNOSTIC_TERMINAL_FAILURE"):
        adapter.request(repo, "OCI")


def test_alarm_cleanup_failure_keeps_original_and_records_cleanup(attempt, monkeypatch):
    module, _raw, root, build = attempt
    primary = ValueError("ADAPTER_PAYLOAD_DRIFT")
    alarm = signal.alarm
    prior_handler = signal.getsignal(signal.SIGALRM)

    def fail(_packages):
        raise primary

    def fail_cancel(seconds):
        if seconds == 0:
            alarm(0)
            raise OSError("synthetic alarm cleanup failure")
        return alarm(seconds)

    monkeypatch.setattr(module, "verify_packages", fail)
    monkeypatch.setattr(signal, "alarm", fail_cancel)
    with pytest.raises(ValueError) as caught:
        build()
    assert caught.value is primary
    receipt = assert_failure(root, "ValueError", str(primary), "verify_source_bytes")
    assert receipt["failure"]["cause"] == {
        "exception_type": "OSError",
        "reason": "synthetic alarm cleanup failure",
    }
    assert signal.getsignal(signal.SIGALRM) == prior_handler


def test_cleanup_only_failure_has_its_own_stage(attempt, monkeypatch):
    _module, _raw, root, build = attempt
    restore = probe._restore_alarm

    def fail_restore(handler):
        restore(handler)
        raise OSError("synthetic cleanup-only failure")

    monkeypatch.setattr(probe, "_restore_alarm", fail_restore)
    with pytest.raises(OSError, match="synthetic cleanup-only failure"):
        build()
    assert_failure(root, "OSError", "synthetic cleanup-only failure", "restore_wall_alarm")
    assert list(root.glob("*/receipt.json"))
