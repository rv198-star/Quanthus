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


def test_historical_topology_contract_remains_frozen_as_r18_evidence() -> None:
    contract = _load_contract()
    authority = contract["authorized_execution"]
    assert authority["build_node"] == "GPU-P100"
    assert authority["install_node"] == "cn01"
    assert contract["canonical_digest"].startswith("sha256:")


def test_production_runtime_does_not_use_host_or_approval_as_an_execution_gate() -> None:
    preflight = (ROOT / "src/quanthus/pipelines/dwd_v21_rebuild_preflight.py").read_text(
        encoding="ascii"
    )
    monitor = (ROOT / "src/quanthus/pipelines/dwd_v21_rebuild_monitor.py").read_text(
        encoding="ascii"
    )
    full_build = (ROOT / "src/quanthus/pipelines/dwd_v21_full_build.py").read_text(
        encoding="ascii"
    )
    monitor_service = (ROOT / "ops/systemd/quanthus-dwd-v2-1-monitor.service").read_text(
        encoding="ascii"
    )

    assert "preflight must run on GPU-P100" not in preflight
    assert "verify_authority" not in preflight + monitor + full_build
    assert "--approval" not in monitor_service
    assert "awaiting-candidate-approval" not in full_build


def test_historical_approval_receipts_remain_canonical_evidence_only() -> None:
    contract = _load_contract()
    for path in (APPROVAL_PATH, PILOT_APPROVAL_PATH):
        receipt = json.loads(path.read_text(encoding="ascii"))
        supplied = receipt.pop("canonical_digest")
        assert supplied == _canonical_digest(receipt)
        assert receipt["approved_contract_digest"] == contract["canonical_digest"]


def test_full_build_service_is_bounded_and_never_auto_resumes() -> None:
    service = FULL_BUILD_SERVICE.read_text(encoding="ascii")
    assert "PrivateNetwork=true" in service
    assert "MemoryHigh=10G" in service
    assert "MemoryMax=12G" in service
    assert "LimitAS=" not in service
    assert "--thread-limit 2" in service
    assert "--reuse-run-id ${QUANTHUS_DWD_REUSE_RUN_ID}" in service
    assert "CPUQuota=200%" in service
    assert "Restart=no" in service
    assert "SuccessExitStatus=143" in service
    assert "RuntimeMaxSec=72h" in service
    assert "TMPDIR=/srv/quanthus/gpu-p100-dwd-workspace/control/tmp" in service
    assert "publish-dwd-domain-base" not in service
