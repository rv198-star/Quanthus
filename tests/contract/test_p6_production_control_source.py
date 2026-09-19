from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_p6_production_control_is_versioned_and_source_validates() -> None:
    source = ROOT / "ops/production-control"
    adapter = (source / "quanthus-mcp-production.py").read_text(encoding="utf-8")
    override = (source / "compose.override.yaml").read_text(encoding="utf-8")
    unit = (source / "quanthus-mcp-production.service").read_text(encoding="utf-8")

    assert 'p6_forward = "quanthus.p6-forward-cycle.v1"' in adapter
    assert "replay_runtime_contract_compliant" in adapter
    assert 'QUANTHUS_P6_CONTROLLER_ENABLED: "1"' in override
    assert "QUANTHUS_P6_FORWARD_RUN_ROOT: /data/candidate-runs/p6-forward" in override
    assert "/home/ubuntu/data/quanthus/dwd-daily:/data/dwd-daily:ro" in override
    assert "strategy-replay-development-1f1ea4d-v1" in override
    assert "ExecStart=/usr/local/libexec/quanthus-mcp-production" in unit
    assert "SupplementaryGroups=devspace docker" in unit

    result = subprocess.run(
        ["bash", "ops/bin/verify-quanthus-production-control", "--source-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "source: PASS" in result.stdout
