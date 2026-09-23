from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "ops/bin/run-cn01-ods-daily"
RETRY_WRAPPER = ROOT / "ops/bin/run-ods-daily-with-retry"
COMPLETION_NOTIFICATION_CHECKER = ROOT / "ops/bin/check-ods-dwd-completion-notification"
MATURITY_RECOVERY = ROOT / "ops/bin/recover-matured-ods-daily"


def _load_maturity_recovery_module():
    loader = importlib.machinery.SourceFileLoader("maturity_recovery", str(MATURITY_RECOVERY))
    spec = importlib.util.spec_from_loader("maturity_recovery", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_ready_release(release_root: Path, *, lane_root: str, release_prefix: str) -> Path:
    release = release_root / lane_root / "2026-08-04" / "ready-release"
    artifacts = release / "artifacts"
    artifacts.mkdir(parents=True)
    payload = artifacts / "payload.parquet"
    payload.write_bytes(b"ready")
    payload_sha = "sha256:" + hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "release_id": release_prefix + "20260804-v2",
        "schedule": "daily",
        "market_date": "2026-08-04",
        "created_at": "2026-08-04T11:00:00+00:00",
        "producer": "test",
        "status": "ready",
        "schedule_contract_id": "schedule-v1",
        "schedule_contract_revision": "revision-v1",
        "schedule_config_sha256": "sha256:" + "1" * 64,
        "artifacts": [
            {
                "name": "payload",
                "relative_path": "artifacts/payload.parquet",
                "sha256": payload_sha,
                "size_bytes": payload.stat().st_size,
                "source_revision": "source-v1",
                "knowledge_cutoff": "2026-08-04T11:00:00+00:00",
            }
        ],
    }
    manifest_path = release / "release.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="ascii")
    manifest_sha = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (release / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return release


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
        [
            str(ORCHESTRATOR),
            "--market-date",
            "2026-08-04",
            "--recovery-window-seconds",
            "20",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    state = json.loads(completed.stdout)
    assert state["status"] == "ready"
    assert state["market_recovery_deadline_at"] is not None
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
    assert called.count("--recovery-deadline-at") == 1
    assert "--lane calendar-daily-events" in called
    assert "--stream 1m" in called
    assert called.count("--stream 5m") == 2
    assert called.index("--lane market-daily-core") < called.index("--stream 5m")
    assert called.index("--stream 5m") < called.index("--stream 1m")
    context = json.loads(
        (tmp_path / "shadow/orchestrator/daily/notification-context.json").read_text(
            encoding="ascii"
        )
    )
    assert context["market_date"] == "2026-08-04"
    assert context["node"] == "CN01"
    assert context["status"] == "ready"
    assert context["run_path"] == str(run_roots[0])


def test_cn01_orchestrator_ods_only_seals_catalog_without_starting_dwd(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    runner = tmp_path / "runner.py"
    _write_executable(
        runner,
        """#!/usr/bin/env python3
from pathlib import Path
import sys
with Path(__CALLS__).open('a', encoding='utf-8') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
raise SystemExit(0)
""".replace("__CALLS__", repr(str(calls))),
    )
    catalog_builder = tmp_path / "catalog.py"
    _write_executable(
        catalog_builder,
        """#!/usr/bin/env python3
import argparse
import hashlib
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--release-root')
parser.add_argument('--market-date')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True)
catalog = args.output / 'catalog.json'
catalog.write_text('{"kind":"daily_ods_input_catalog"}\\n', encoding='ascii')
(args.output / 'READY').write_text(
    'sha256:' + hashlib.sha256(catalog.read_bytes()).hexdigest() + '\\n',
    encoding='ascii',
)
""",
    )
    dwd_marker = tmp_path / "dwd-started"
    dwd_runner = tmp_path / "dwd.py"
    _write_executable(
        dwd_runner,
        "#!/usr/bin/env python3\nfrom pathlib import Path\n"
        f"Path({str(dwd_marker)!r}).touch()\n",
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
        [str(ORCHESTRATOR), "--market-date", "2026-08-04", "--ods-only"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    state = json.loads(completed.stdout)
    assert state["mode"] == "ods_only"
    assert state["status"] == "ods_ready"
    assert state["catalog"]["status"] == "ready"
    assert state["dwd"] == {"status": "not_requested"}
    assert not dwd_marker.exists()
    lane_calls = calls.read_text(encoding="utf-8").splitlines()
    market_call = next(call for call in lane_calls if "--lane market-daily-core" in call)
    deadline_text = market_call.split("--recovery-deadline-at ", 1)[1].split()[0]
    assert state["market_recovery_deadline_at"] == deadline_text
    recovery_deadline = datetime.fromisoformat(deadline_text)
    assert recovery_deadline.tzinfo is not None
    assert recovery_deadline > datetime.fromisoformat(state["started_at"].replace("Z", "+00:00"))
    assert all(
        "--recovery-deadline-at" not in call
        for call in lane_calls
        if "--lane market-daily-core" not in call
    )


def test_cn01_orchestrator_defers_only_a_qualified_current_session_tencent_failure(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls.log"
    runner = tmp_path / "runner.py"
    _write_executable(
        runner,
        """#!/usr/bin/env python3
from pathlib import Path
import sys
args = sys.argv[1:]
with Path(__CALLS__).open('a', encoding='utf-8') as stream:
    stream.write(' '.join(args) + '\\n')
if '--stream' in args and args[args.index('--stream') + 1] == '1m':
    print('RuntimeError: Tencent source qualification failed for 2026-08-04/000001: Tencent/daily volume mismatch')
    raise SystemExit(4)
raise SystemExit(0)
""".replace("__CALLS__", repr(str(calls))),
    )
    env = os.environ.copy()
    env.update(
        {
            "HERMES_ODS_NODE_ID": "cn01",
            "HERMES_ODS_ENV_FILE": str(tmp_path / "missing.env"),
            "HERMES_ODS_SHADOW_ROOT": str(tmp_path / "shadow"),
            "ASTOCK_HOME": str(tmp_path / "home"),
            "HERMES_ODS_RUNNER": str(runner),
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

    assert completed.returncode == 3, completed.stderr + completed.stdout
    state = json.loads(completed.stdout)
    assert state["status"] == "deferred_source_maturity"
    assert state["lanes"]["intraday-1m"]["status"] == "deferred_source_maturity"
    assert state["catalog"] == {"status": "deferred_source_maturity"}
    assert state["dwd"] == {"status": "deferred_source_maturity"}
    run_root = next((tmp_path / "shadow/orchestrator/daily/2026-08-04").iterdir())
    assert not (run_root / "READY").exists()
    context = json.loads(
        (tmp_path / "shadow/orchestrator/daily/notification-context.json").read_text(
            encoding="ascii"
        )
    )
    assert context["status"] == "deferred_source_maturity"
    assert context["error_code"] == "source_maturity_deferred"


def test_completion_notification_checker_refuses_deferred_state(tmp_path: Path) -> None:
    context = tmp_path / "context.json"
    context.write_text('{"status":"deferred_source_maturity"}\n', encoding="ascii")
    deferred = subprocess.run(
        [str(COMPLETION_NOTIFICATION_CHECKER), "--context", str(context)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert deferred.returncode == 1

    context.write_text('{"status":"ready"}\n', encoding="ascii")
    ready = subprocess.run(
        [str(COMPLETION_NOTIFICATION_CHECKER), "--context", str(context)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ready.returncode == 0


def test_maturity_recovery_keeps_a_qualified_pending_source_quiet(tmp_path: Path) -> None:
    module = _load_maturity_recovery_module()
    shadow = tmp_path / "shadow"
    env = {
        "HERMES_ODS_NODE_ID": "cn01",
        "HERMES_ODS_SHADOW_ROOT": str(shadow),
        "QUANTHUS_ODS_DAILY_ORCHESTRATOR": str(tmp_path / "orchestrator"),
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(sys, "argv", [str(MATURITY_RECOVERY), "--market-date", "2026-08-04"]),
        patch.object(module, "_run", return_value=3) as run,
    ):
        assert module.main() == 0
    assert run.call_count == 1
    assert run.call_args.args[0][-1] == "--ods-only"
    assert not (shadow / "recovery/matured-1m/2026-08-04/dwd-complete.json").exists()


def test_maturity_recovery_seals_one_completion_marker_before_notifying(tmp_path: Path) -> None:
    module = _load_maturity_recovery_module()
    shadow = tmp_path / "shadow"
    env = {
        "HERMES_ODS_NODE_ID": "cn01",
        "HERMES_ODS_SHADOW_ROOT": str(shadow),
        "QUANTHUS_ODS_DAILY_ORCHESTRATOR": str(tmp_path / "orchestrator"),
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(sys, "argv", [str(MATURITY_RECOVERY), "--market-date", "2026-08-04"]),
        patch.object(module, "_run", side_effect=[0, 0, 0]) as run,
    ):
        assert module.main() == 0
    marker = shadow / "recovery/matured-1m/2026-08-04/dwd-complete.json"
    assert json.loads(marker.read_text(encoding="ascii"))["status"] == "ready"
    assert run.call_count == 3
    assert run.call_args_list[0].args[0][-1] == "--ods-only"
    assert "--ods-only" not in run.call_args_list[1].args[0]
    assert "--market-date" in run.call_args_list[2].args[0]


def test_maturity_recovery_does_not_repeat_a_marked_completion(tmp_path: Path) -> None:
    module = _load_maturity_recovery_module()
    marker = tmp_path / "shadow/recovery/matured-1m/2026-08-04/dwd-complete.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"status":"ready"}\n', encoding="ascii")
    env = {
        "HERMES_ODS_NODE_ID": "cn01",
        "HERMES_ODS_SHADOW_ROOT": str(tmp_path / "shadow"),
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(sys, "argv", [str(MATURITY_RECOVERY), "--market-date", "2026-08-04"]),
        patch.object(module, "_run") as run,
    ):
        assert module.main() == 0
    run.assert_not_called()


def test_maturity_recovery_uses_existing_lane_receipts_not_a_stale_calendar(tmp_path: Path) -> None:
    module = _load_maturity_recovery_module()
    root = tmp_path / "shadow/orchestrator/daily/2026-09-23/legacy"
    logs = root / "logs"
    logs.mkdir(parents=True)
    minute_log = logs / "intraday-1m.log"
    minute_log.write_text("RuntimeError: Tencent/daily volume mismatch\n", encoding="ascii")
    (root / "run.json").write_text(
        json.dumps(
            {
                "finished_at": "2026-09-23T13:55:29Z",
                "status": "failed",
                "lanes": {
                    "market-daily-core": {"status": "ready"},
                    "calendar-daily-events": {"status": "ready"},
                    "intraday-5m": {"status": "ready"},
                    "intraday-1m": {"status": "failed", "logs": [str(minute_log)]},
                },
            }
        ),
        encoding="ascii",
    )
    assert module._pending_maturity_session(tmp_path / "shadow", date(2026, 9, 24)) == date(
        2026, 9, 23
    )


def test_cn01_orchestrator_units_define_ready_gated_control_plane() -> None:
    service = (ROOT / "ops/systemd/hermes-ods-daily-orchestrator.service").read_text(
        encoding="ascii"
    )
    timer = (ROOT / "ops/systemd/hermes-ods-daily-orchestrator.timer").read_text(encoding="ascii")
    assert "run-ods-daily-with-retry" in service
    assert "run-cn01-ods-daily" in service
    assert "QUANTHUS_ODS_DAILY_RETRY_RECOVERY_SECONDS=14760" in service
    assert "TimeoutStartSec=540min" in service
    assert "SuccessExitStatus=0 3" in service
    assert "OnSuccess=hermes-dwd-success-notification.service" in service
    assert "OnCalendar=Mon..Fri *-*-* 19:30:00 Asia/Shanghai" in timer
    assert "Persistent=false" in timer


def test_daily_retry_wrapper_returns_success_after_a_retry(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts"
    calls = tmp_path / "calls"
    runner = tmp_path / "runner"
    _write_executable(
        runner,
        "#!/usr/bin/env bash\n"
        f"attempts={str(attempts)!r}\n"
        f"calls={str(calls)!r}\n"
        "printf '%s\\n' \"$*\" >> \"$calls\"\n"
        "count=0\n"
        "[[ -f $attempts ]] && count=$(<\"$attempts\")\n"
        "count=$((count + 1))\n"
        "printf '%s\\n' \"$count\" > \"$attempts\"\n"
        "[[ $count -gt 1 ]]\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "QUANTHUS_ODS_DAILY_MAX_ATTEMPTS": "2",
            "QUANTHUS_ODS_DAILY_RETRY_DELAY_SECONDS": "0",
            "QUANTHUS_ODS_DAILY_RETRY_RECOVERY_SECONDS": "14760",
        }
    )

    completed = subprocess.run(
        [str(RETRY_WRAPPER), str(runner)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert attempts.read_text(encoding="ascii") == "2\n"
    assert calls.read_text(encoding="ascii").splitlines() == [
        "",
        "--recovery-window-seconds 14760",
    ]


def test_daily_retry_wrapper_returns_final_failure_status(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts"
    runner = tmp_path / "runner"
    _write_executable(
        runner,
        "#!/usr/bin/env bash\n"
        f"attempts={str(attempts)!r}\n"
        "count=0\n"
        "[[ -f $attempts ]] && count=$(<\"$attempts\")\n"
        "printf '%s\\n' \"$((count + 1))\" > \"$attempts\"\n"
        "exit 23\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "QUANTHUS_ODS_DAILY_MAX_ATTEMPTS": "2",
            "QUANTHUS_ODS_DAILY_RETRY_DELAY_SECONDS": "0",
        }
    )

    completed = subprocess.run(
        [str(RETRY_WRAPPER), str(runner)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 23
    assert attempts.read_text(encoding="ascii") == "2\n"


def test_daily_retry_wrapper_delegates_source_maturity_without_repeating(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts"
    runner = tmp_path / "runner"
    _write_executable(
        runner,
        "#!/usr/bin/env bash\n"
        f"printf '1\\n' > {str(attempts)!r}\n"
        "exit 3\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "QUANTHUS_ODS_DAILY_MAX_ATTEMPTS": "2",
            "QUANTHUS_ODS_DAILY_RETRY_DELAY_SECONDS": "0",
        }
    )

    completed = subprocess.run(
        [str(RETRY_WRAPPER), str(runner)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 3
    assert attempts.read_text(encoding="ascii") == "1\n"


def test_orchestrator_reuses_existing_ready_lane_and_preserves_node(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    existing = _write_ready_release(
        shadow / "releases",
        lane_root="daily",
        release_prefix="hermes-market-daily-core-",
    )
    calls = tmp_path / "calls.log"
    runner = tmp_path / "runner.py"
    _write_executable(
        runner,
        """#!/usr/bin/env python3
from pathlib import Path
import sys
with Path(__CALLS__).open('a', encoding='utf-8') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
raise SystemExit(0)
""".replace("__CALLS__", repr(str(calls))),
    )
    catalog_builder = tmp_path / "catalog.py"
    _write_executable(
        catalog_builder,
        """#!/usr/bin/env python3
import argparse
import hashlib
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--release-root')
parser.add_argument('--market-date')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True)
catalog = args.output / 'catalog.json'
catalog.write_text('{"kind":"daily_ods_input_catalog"}\\n', encoding='ascii')
(args.output / 'READY').write_text(
    'sha256:' + hashlib.sha256(catalog.read_bytes()).hexdigest() + '\\n',
    encoding='ascii',
)
""",
    )
    dwd_runner = tmp_path / "dwd.py"
    _write_executable(dwd_runner, "#!/usr/bin/env python3\nraise SystemExit(0)\n")
    env = os.environ.copy()
    env.update(
        {
            "HERMES_ODS_NODE_ID": "hermes",
            "HERMES_ODS_ENV_FILE": str(tmp_path / "missing.env"),
            "HERMES_ODS_SHADOW_ROOT": str(shadow),
            "ASTOCK_HOME": str(tmp_path / "home"),
            "HERMES_ODS_RUNNER": str(runner),
            "HERMES_ODS_CATALOG_BUILDER": str(catalog_builder),
            "QUANTHUS_DWD_DAILY_RUNNER": str(dwd_runner),
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
    assert state["node"] == "hermes"
    assert state["lanes"]["market-daily-core"]["source"] == "existing_ready_release"
    assert state["lanes"]["market-daily-core"]["attempts"] == 0
    assert state["lanes"]["market-daily-core"]["release_path"] == str(existing.resolve())
    assert "--lane market-daily-core" not in calls.read_text(encoding="utf-8")


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
    context = json.loads(
        (tmp_path / "shadow/orchestrator/daily/notification-context.json").read_text(
            encoding="ascii"
        )
    )
    assert context["status"] == "failed"
    assert context["market_date"] == "2026-08-04"
    assert context["run_path"] == str(run_roots[0])
