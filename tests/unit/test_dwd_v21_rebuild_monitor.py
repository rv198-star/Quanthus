import json
from pathlib import Path

import pytest

from quanthus.pipelines.dwd_v21_rebuild_monitor import (
    DwdV21MonitorError,
    _active_run_snapshot,
    parse_mountinfo,
    verify_execution_contract,
    write_snapshot,
)
from quanthus.pipelines.dwd_v21_rebuild_runtime import _active_pointer, _phase_receipt

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json"
DIGEST = "sha256:bab98bb0a11dbd2c8395881931cdaed50f915ce2ce39065c25ac103e5ea84d8a"


def test_exact_execution_contract_is_hash_bound_without_approval_input() -> None:
    contract = verify_execution_contract(CONTRACT, DIGEST)
    assert contract["authorized_execution"]["build_node"] == "GPU-P100"


def test_execution_contract_rejects_digest_drift() -> None:
    with pytest.raises(DwdV21MonitorError, match="deployed digest"):
        verify_execution_contract(CONTRACT, "sha256:" + "0" * 64)


def test_mountinfo_selects_exact_nested_mount() -> None:
    mountinfo = "\n".join(
        [
            "1 0 0:1 / / rw - ext4 /dev/root rw",
            "2 1 7:0 / /srv/quanthus/gpu-p100-dwd-workspace rw,noatime - ext4 /dev/loop0 rw",
        ]
    )
    observed = parse_mountinfo(
        mountinfo,
        Path("/srv/quanthus/gpu-p100-dwd-workspace/control"),
    )
    assert observed == {
        "filesystem": "ext4",
        "mount_options": "rw,noatime",
        "mount_point": "/srv/quanthus/gpu-p100-dwd-workspace",
        "source": "/dev/loop0",
    }


def test_snapshot_write_is_immutable_and_updates_latest(tmp_path: Path) -> None:
    snapshot = {
        "kind": "dwd_base_v2_1_rebuild_health_snapshot",
        "observed_at": "2026-08-07T21:00:00+00:00",
        "status": "idle",
    }
    latest, immutable = write_snapshot(tmp_path, snapshot)
    assert json.loads(latest.read_text(encoding="ascii"))["status"] == "idle"
    assert immutable.is_file()
    assert latest.read_bytes() == immutable.read_bytes()
    assert latest.stat().st_ino != immutable.stat().st_ino
    assert immutable.stat().st_mode & 0o777 == 0o440


def test_monitor_distinguishes_stale_and_paused_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs/run-1"
    _phase_receipt(
        run_root,
        phase="domain-running",
        run_id="run-1",
        contract_digest=DIGEST,
        code_revision="1" * 40,
        status="running",
    )
    _active_pointer(
        tmp_path,
        run_id="run-1",
        phase="domain-running",
        contract_digest=DIGEST,
        executor_pid=123,
        status="running",
    )

    def missing_process(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("quanthus.pipelines.dwd_v21_rebuild_monitor.os.kill", missing_process)
    assert _active_run_snapshot(tmp_path, DIGEST)["status"] == "stale"

    _phase_receipt(
        run_root,
        phase="awaiting-approval",
        run_id="run-1",
        contract_digest=DIGEST,
        code_revision="1" * 40,
        status="paused",
    )
    _active_pointer(
        tmp_path,
        run_id="run-1",
        phase="awaiting-approval",
        contract_digest=DIGEST,
        executor_pid=123,
        status="paused",
    )
    assert _active_run_snapshot(tmp_path, DIGEST)["status"] == "paused"


def test_systemd_timer_is_observational_and_hourly() -> None:
    service = (ROOT / "ops/systemd/quanthus-dwd-v2-1-monitor.service").read_text(encoding="ascii")
    timer = (ROOT / "ops/systemd/quanthus-dwd-v2-1-monitor.timer").read_text(encoding="ascii")
    environment = (ROOT / "ops/systemd/gpu-p100-dwd-rebuild.env.example").read_text(
        encoding="ascii"
    )
    assert "OnUnitActiveSec=1h" in timer
    assert "Persistent=false" in timer
    assert "ConditionPathIsMountPoint=/srv/quanthus/gpu-p100-dwd-workspace" in service
    assert "inspect-dwd-v2-1-rebuild" in service
    assert "run-dwd-v2-1-rebuild" not in service
    assert "QUANTHUS_DWD_EXECUTION_MODE=pilot_only" in environment
    assert "QUANTHUS_DWD_BUILDER_AUTHORIZED" not in environment
    assert "--approval" not in service
    assert f"QUANTHUS_DWD_EXECUTION_CONTRACT_DIGEST={DIGEST}" in environment
