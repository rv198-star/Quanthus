from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "ops/bin/run-cn01-ods-daily"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_cn01_orchestrator_retries_market_resource_lock_and_seals_dwd(tmp_path: Path) -> None:
    calls = tmp_path / "calls.log"
    five_marker = tmp_path / "5m-first-attempt"
    runner = tmp_path / "fake-runner.py"
    _write_executable(
        runner,
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
root = pathlib.Path(__ROOT__)
with (root / 'calls.log').open('a', encoding='utf-8') as stream:
    stream.write(' '.join(args) + '\\n')
if (
    '--stream' in args
    and args[args.index('--stream') + 1] == '5m'
    and not pathlib.Path(__MARKER__).exists()
):
    pathlib.Path(__MARKER__).write_text('seen', encoding='ascii')
    print('LaneRunError: resource-group-pve_market_api lock busy')
    raise SystemExit(1)
raise SystemExit(0)
""".replace("__ROOT__", repr(str(tmp_path))).replace("__MARKER__", repr(str(five_marker))),
    )
    catalog_builder = tmp_path / "fake-catalog.py"
    _write_executable(
        catalog_builder,
        """#!/usr/bin/env python3
import argparse
import hashlib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--release-root', required=True)
parser.add_argument('--market-date', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True)
catalog = args.output / 'catalog.json'
catalog.write_text('{"kind":"cn01_daily_ods_input_catalog"}\\n', encoding='ascii')
ready = 'sha256:' + hashlib.sha256(catalog.read_bytes()).hexdigest() + '\\n'
(args.output / 'READY').write_text(ready, encoding='ascii')
print('{"status":"ready"}')
""",
    )
    dwd_runner = tmp_path / "fake-dwd.py"
    _write_executable(
        dwd_runner,
        """#!/usr/bin/env python3
import sys
raise SystemExit(0)
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "HERMES_ODS_NODE_ID": "cn01",
            "HERMES_ODS_ENV_FILE": str(tmp_path / "missing.env"),
            "HERMES_ODS_SHADOW_ROOT": str(tmp_path / "shadow"),
            "ASTOCK_HOME": str(tmp_path / "home"),
            "HERMES_ODS_RUNNER": str(runner),
            "HERMES_ODS_CATALOG_BUILDER": str(catalog_builder),
            "QUANTHUS_DWD_RUNNER": str(dwd_runner),
            "QUANTHUS_DWD_PYTHON": sys.executable,
            "HERMES_ODS_DAILY_SLA_SECONDS": "20",
            "HERMES_ODS_ORCHESTRATOR_POLL_SECONDS": "0.1",
        }
    )
    completed = subprocess.run(
        [str(ORCHESTRATOR), "--market-date", "2026-08-04"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    state = json.loads(completed.stdout)
    assert state["status"] == "ready"
    assert state["lanes"]["market-daily-core"]["status"] == "ready"
    assert state["lanes"]["calendar-daily-events"]["status"] == "ready"
    assert state["lanes"]["intraday-1m"]["status"] == "ready"
    assert state["lanes"]["intraday-5m"]["status"] == "ready"
    assert state["lanes"]["intraday-5m"]["attempts"] == 2
    assert state["catalog"]["status"] == "ready"
    assert state["dwd"]["status"] == "ready"
    # Discover the timestamp directory without relying on clock formatting.
    run_roots = list((tmp_path / "shadow" / "orchestrator" / "daily" / "2026-08-04").iterdir())
    assert len(run_roots) == 1
    assert (run_roots[0] / "READY").is_file()
    called = calls.read_text(encoding="utf-8")
    assert "--lane market-daily-core" in called
    assert "--lane calendar-daily-events" in called
    assert "--stream 1m" in called
    assert called.count("--stream 5m") == 2


def test_cn01_orchestrator_units_define_one_hour_control_plane() -> None:
    service = (ROOT / "ops/systemd/hermes-ods-daily-orchestrator.service").read_text(
        encoding="ascii"
    )
    timer = (ROOT / "ops/systemd/hermes-ods-daily-orchestrator.timer").read_text(encoding="ascii")
    assert "run-cn01-ods-daily" in service
    assert "TimeoutStartSec=70min" in service
    assert "OnCalendar=Mon..Fri *-*-* 18:30:00 Asia/Shanghai" in timer
    assert "Persistent=false" in timer


def test_cn01_orchestrator_keeps_failure_evidence_when_catalog_command_is_missing(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "runner.py"
    _write_executable(
        runner,
        """#!/usr/bin/env python3
raise SystemExit(0)
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "HERMES_ODS_NODE_ID": "cn01",
            "HERMES_ODS_ENV_FILE": str(tmp_path / "missing.env"),
            "HERMES_ODS_SHADOW_ROOT": str(tmp_path / "shadow"),
            "ASTOCK_HOME": str(tmp_path / "home"),
            "HERMES_ODS_RUNNER": str(runner),
            "HERMES_ODS_CATALOG_BUILDER": str(tmp_path / "missing-catalog"),
            "HERMES_ODS_DAILY_SLA_SECONDS": "20",
            "HERMES_ODS_ORCHESTRATOR_POLL_SECONDS": "0.1",
        }
    )
    completed = subprocess.run(
        [str(ORCHESTRATOR), "--market-date", "2026-08-04"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    state = json.loads(completed.stdout)
    assert state["status"] == "failed"
    assert state["catalog"]["status"] == "failed"
    assert state["catalog"]["returncode"] == 127
    assert state["dwd"] is None
    run_roots = list((tmp_path / "shadow" / "orchestrator" / "daily" / "2026-08-04").iterdir())
    assert len(run_roots) == 1
    assert not (run_roots[0] / "READY").exists()
