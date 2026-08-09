import json
from pathlib import Path

import pytest

from quanthus.pipelines.dwd_v21_rebuild_runtime import (
    DwdV21RuntimeError,
    _active_pointer,
    _phase_receipt,
    _require_pilot_only,
    _verify_sealed,
)

CONTRACT = "sha256:" + "1" * 64
REVISION = "2" * 40


def test_phase_receipt_is_immutable_and_canonically_bound(tmp_path: Path) -> None:
    path = _phase_receipt(
        tmp_path,
        phase="preflight-running",
        run_id="run-1",
        contract_digest=CONTRACT,
        code_revision=REVISION,
        status="running",
    )
    value = _verify_sealed(path, "phase")
    assert value["phase"] == "preflight-running"
    assert path.stat().st_mode & 0o777 == 0o440
    assert (
        _phase_receipt(
            tmp_path,
            phase="preflight-running",
            run_id="run-1",
            contract_digest=CONTRACT,
            code_revision=REVISION,
            status="running",
        )
        == path
    )


def test_phase_receipt_rejects_cross_run_reuse(tmp_path: Path) -> None:
    _phase_receipt(
        tmp_path,
        phase="planning-running",
        run_id="run-1",
        contract_digest=CONTRACT,
        code_revision=REVISION,
        status="running",
    )
    with pytest.raises(DwdV21RuntimeError, match="another run"):
        _phase_receipt(
            tmp_path,
            phase="planning-running",
            run_id="run-2",
            contract_digest=CONTRACT,
            code_revision=REVISION,
            status="running",
        )


def test_phase_receipt_rejects_evidence_drift(tmp_path: Path) -> None:
    _phase_receipt(
        tmp_path,
        phase="candidate-passed",
        run_id="run-1",
        contract_digest=CONTRACT,
        code_revision=REVISION,
        status="passed",
        evidence={"sha256": "sha256:first"},
    )
    with pytest.raises(DwdV21RuntimeError, match="evidence differs"):
        _phase_receipt(
            tmp_path,
            phase="candidate-passed",
            run_id="run-1",
            contract_digest=CONTRACT,
            code_revision=REVISION,
            status="passed",
            evidence={"sha256": "sha256:changed"},
        )


def test_active_pointer_matches_monitor_contract(tmp_path: Path) -> None:
    _active_pointer(
        tmp_path,
        run_id="run-1",
        phase="bootstrap",
        contract_digest=CONTRACT,
        executor_pid=123,
        status="running",
    )
    value = json.loads((tmp_path / "control/active-run.json").read_text(encoding="ascii"))
    assert value["run_id"] == "run-1"
    assert value["phase"] == "bootstrap"
    assert value["contract_digest"] == CONTRACT
    assert value["executor_pid"] == 123
    assert value["status"] == "running"


def test_runtime_requires_pilot_only_environment(tmp_path: Path) -> None:
    environment = tmp_path / "runtime.env"
    environment.write_text(
        "\n".join(
            [
                "QUANTHUS_DWD_BUILDER_AUTHORIZED=0",
                f"QUANTHUS_DWD_CODE_REVISION={REVISION}",
                "QUANTHUS_DWD_EXECUTION_MODE=pilot_only",
                "QUANTHUS_DWD_PILOT_AUTHORIZED=1",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    assert _require_pilot_only(environment, REVISION)["QUANTHUS_DWD_BUILDER_AUTHORIZED"] == "0"
    environment.write_text(
        environment.read_text(encoding="ascii").replace(
            "QUANTHUS_DWD_BUILDER_AUTHORIZED=0",
            "QUANTHUS_DWD_BUILDER_AUTHORIZED=1",
        ),
        encoding="ascii",
    )
    with pytest.raises(DwdV21RuntimeError, match="pilot-only"):
        _require_pilot_only(environment, REVISION)
