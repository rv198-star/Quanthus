import hashlib
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "audits" / "dwd-base-v2-1-gpu-p100-execution.v1.json"
APPROVAL_PATH = (
    ROOT
    / "docs"
    / "acceptance"
    / "issue-78-dwd-base-v2-1-gpu-p100-execution-approval-20260807.json"
)
PILOT_APPROVAL_PATH = (
    ROOT / "docs" / "acceptance" / "issue-78-dwd-base-v2-1-gpu-p100-pilot-approval-20260808.json"
)
FULL_BUILD_SERVICE = ROOT / "ops/systemd/quanthus-dwd-v2-1-full-build.service"


def _load_contract() -> dict[str, Any]:
    value: Any = json.loads(CONTRACT_PATH.read_text(encoding="ascii"))
    if not isinstance(value, dict):
        raise AssertionError("GPU-P100 execution contract must be a JSON object")
    return cast(dict[str, Any], value)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def test_contract_canonical_digest() -> None:
    contract = _load_contract()
    supplied = contract.pop("canonical_digest")
    assert supplied == _canonical_digest(contract)


def test_gpu_executes_but_cn01_retains_install_and_cutover_authority() -> None:
    contract = _load_contract()
    authority = contract["authorized_execution"]
    assert authority["build_node"] == "GPU-P100"
    assert authority["execution_backend"] == "cpu"
    assert authority["install_node"] == "cn01"
    assert authority["pointer_authority_node"] == "cn01"
    assert authority["transfer_direction"] == "cn01_pull_from_gpu_p100_read_only_export"


def test_full_builder_and_cutover_remain_fail_closed() -> None:
    contract = _load_contract()
    gate = contract["builder_gate"]
    assert contract["status"] == "pending_exact_digest_approval"
    assert contract["approval"]["exact_digest_approval"] == "pending"
    assert gate["full_build"] == "blocked_until_exact_digest_and_pilot_receipts_approved"
    assert gate["pointer_cutover"] == "blocked_until_separate_exact_candidate_approval"
    assert contract["failure_policy"]["automatic_resume_after_node_return"] is False
    assert contract["failure_policy"]["mutate_v2"] is False


def test_monitor_is_not_an_authority_source() -> None:
    contract = _load_contract()
    monitor = contract["monitoring"]
    assert monitor["cadence"] == "PT1H"
    assert monitor["authority"] == "observe_diagnose_and_bounded_recovery_only"
    assert monitor["control_source"] == "approved_contract_and_phase_receipts"


def test_exact_contract_has_a_separate_approval_receipt() -> None:
    contract = _load_contract()
    approval = json.loads(APPROVAL_PATH.read_text(encoding="ascii"))
    supplied = approval.pop("canonical_digest")
    assert supplied == _canonical_digest(approval)
    assert approval["approved_contract_digest"] == contract["canonical_digest"]
    assert approval["approval_statement"].endswith(contract["canonical_digest"])


def test_accepted_pilot_receipts_have_a_canonical_approval() -> None:
    approval = json.loads(PILOT_APPROVAL_PATH.read_text(encoding="ascii"))
    supplied = approval.pop("canonical_digest")
    assert supplied == _canonical_digest(approval)
    assert approval["approved_contract_digest"] == _load_contract()["canonical_digest"]
    assert approval["approval_statement"].endswith(f"RUN={approval['pilot_run_id']}")


def test_full_build_service_is_bounded_and_never_auto_resumes() -> None:
    service = FULL_BUILD_SERVICE.read_text(encoding="ascii")
    assert "PrivateNetwork=true" in service
    assert "MemoryHigh=10G" in service
    assert "MemoryMax=12G" in service
    assert "LimitAS=" not in service
    assert "--thread-limit 2" in service
    assert "CPUQuota=200%" in service
    assert "Restart=no" in service
    assert "SuccessExitStatus=143" in service
    assert "RuntimeMaxSec=72h" in service
    assert "TMPDIR=/srv/quanthus/gpu-p100-dwd-workspace/control/tmp" in service
    assert "publish-dwd-domain-base" not in service
