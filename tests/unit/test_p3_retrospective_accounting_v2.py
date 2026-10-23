"""Version binding and complete four-variant wiring of the reviewed correction."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from quanthus.backtest.engine import BacktestInputError
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_retrospective_accounting_v2 as corrected
from quanthus.expert_strategy import p3_retrospective_preparation as prep
from quanthus.expert_strategy import p3_retrospective_replay as legacy
from tests.unit.test_p3_base24_batch import industry
from tests.unit.test_p3_retrospective_bridge import INFO, ROOT
from tests.unit.test_p3_retrospective_replay import build


def admitted_runtime(tmp_path, *, changes=None):
    result = build(tmp_path)
    plan = json.loads((tmp_path / "prepared/plan.json").read_text())
    admission = {
        "purpose_id": prep.PURPOSE,
        "mode": "SYNTHETIC_ONLY",
        "preparation_sha256": result["manifest_sha256"],
        "runtime_version": corrected.VERSION,
        "accounting_version": accounting.VERSION,
        "code_config_digest": digest_value(plan["code_config_members"]),
    }
    admission.update({} if changes is None else changes)
    return corrected.CorrectedRetrospectiveRuntime(
        ROOT,
        tmp_path / "prepared",
        tmp_path / "runtime",
        manifest_sha256=result["manifest_sha256"],
        industry=industry(tmp_path / "industry.json"),
        consumer_admission=admission,
    )


def test_wired_four_variants_bind_actual_corrected_hooks_and_reconcile(tmp_path):
    with admitted_runtime(tmp_path) as runtime:
        assert (
            runtime._record_entry.__func__
            is accounting.AccountingCorrectedReplayRuntime._record_entry
        )
        assert (
            runtime._marked_return.__func__
            is accounting.AccountingCorrectedReplayRuntime._marked_return
        )
        assert runtime._code_version().startswith(corrected.VERSION + ":")
        assert type(runtime.engine) is corrected.CorrectedRetrospectiveEngine
        report = runtime.execute_all()
        assert tuple(report["variants"]) == ("B", "T", "R", "TR")
        assert report["session_index_builds"] == 1
        assert report["real_comparisons_executed"] == 0
        assert report["accounting_version"] == accounting.VERSION
    for variant in ("B", "T", "R", "TR"):
        root = tmp_path / "runtime" / ("variant." + variant)
        manifest = json.loads((root / "manifest.json").read_text())
        reconciliation = json.loads((root / "reconciliation.json").read_text())
        assert reconciliation["reconciled"]
        if variant in {"B", "R"}:
            assert reconciliation["fill_count"] > 0
        assert manifest["runtime_version"] == corrected.VERSION
        assert manifest["schema_version"] == corrected.VERSION
        assert manifest["information_cutoff"] == INFO.isoformat()
        assert not manifest["production_authorized"] and not manifest["D_activated"]


@pytest.mark.parametrize(
    "changes",
    [
        {"runtime_version": legacy.VERSION},
        {"accounting_version": "legacy"},
        {"code_config_digest": "sha256:" + "0" * 64},
    ],
)
def test_wrong_adapter_or_code_binding_refused_before_output(tmp_path, changes):
    with pytest.raises(ValueError, match="ADAPTER_ADMISSION_BINDING"):
        admitted_runtime(tmp_path, changes=changes)
    assert not (tmp_path / "runtime").exists()


def test_legacy_synthetic_admission_and_real_receipt_cannot_activate_successor(tmp_path):
    result = build(tmp_path)
    receipt = {
        "purpose_id": prep.PURPOSE,
        "mode": "SYNTHETIC_ONLY",
        "preparation_sha256": result["manifest_sha256"],
    }
    with pytest.raises(ValueError, match="ADAPTER_ADMISSION_BINDING"):
        corrected.CorrectedRetrospectiveRuntime(
            ROOT,
            tmp_path / "prepared",
            tmp_path / "denied",
            manifest_sha256=result["manifest_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission=receipt,
        )
    assert not (tmp_path / "denied").exists()


def test_legacy_engine_refuses_successor_run_and_successor_refuses_legacy(tmp_path):
    from quanthus.backtest.contracts import BacktestRunSpec, OrderSide

    with admitted_runtime(tmp_path) as runtime:
        policy = runtime.engine.policy
        run = BacktestRunSpec(
            code_version=runtime._code_version(),
            parameter_hash="synthetic",
            data_snapshot_id=runtime.snapshot_id,
            feature_version="synthetic",
            rule_version="synthetic",
            run_id="synthetic-corrected",
            execution_policy_version=policy.policy_version,
            execution_profile_id=policy.active_profile.profile_id,
            schedule_version=policy.active_profile.schedule_version,
        )
        signal = runtime._signal(
            run,
            runtime.frames[0],
            "unit-1",
            "security-native",
            OrderSide.BUY,
            100,
            Decimal("1"),
            "test",
            "synthetic-decision",
        )
        runtime.engine._validate_signal_clock(run, signal)
        with pytest.raises(BacktestInputError, match="binding"):
            legacy.RetrospectiveEngine(
                policy, information_cutoff=runtime.info, snapshot_id=runtime.snapshot_id
            )._validate_signal_clock(run, signal)
        with pytest.raises(BacktestInputError, match="binding"):
            runtime.engine._validate_signal_clock(
                replace(run, code_version=legacy.VERSION + ":synthetic"), signal
            )


def test_unregistered_real_successor_refused_before_output(tmp_path):
    result = build(tmp_path)
    path = tmp_path / "prepared/plan.json"
    plan = json.loads(path.read_text())
    plan["synthetic"] = False
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="REAL_SOURCE_ADMISSION"):
        corrected.CorrectedRetrospectiveRuntime(
            ROOT,
            tmp_path / "prepared",
            tmp_path / "denied",
            manifest_sha256=result["manifest_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission={
                "runtime_version": corrected.VERSION,
                "accounting_version": accounting.VERSION,
                "code_config_digest": digest_value(plan["code_config_members"]),
            },
        )
    assert not (tmp_path / "denied").exists()


def test_corrected_two_cold_processes_use_successor_binding_without_sources(tmp_path):
    import ctypes.util
    import os
    import shutil
    import subprocess
    import sys

    if not ctypes.util.find_library("seccomp"):
        pytest.skip("libseccomp unavailable; isolation not checked")
    result = build(tmp_path)
    industry(tmp_path / "industry.json")
    old = tmp_path / "prepared"
    forbidden = tmp_path / "old-source"
    forbidden.write_text("must not read")
    script = r"""
import ctypes, ctypes.util, errno, json, os, socket, sys
from pathlib import Path
root, repository, scratch, industry_path, forbidden = map(Path, sys.argv[1:])
libc=ctypes.CDLL(None, use_errno=True)
class Ruleset(ctypes.Structure):
    _fields_=[('handled_access_fs',ctypes.c_uint64)]
class PathRule(ctypes.Structure):
    _fields_=[('allowed_access',ctypes.c_uint64),('parent_fd',ctypes.c_int32)]
assert libc.syscall(444,0,0,1)>=1
access=1|4|8
spec=Ruleset(access)
fd=libc.syscall(444,ctypes.byref(spec),ctypes.sizeof(spec),0)
assert fd>=0
for path in [root,repository/'src',repository/'config',repository/'docs',scratch,
             industry_path,Path(sys.prefix),
             *[Path(x) for x in ('/usr','/lib','/lib64','/etc','/dev','/proc','/sys')]]:
    if not path.exists(): continue
    parent=os.open(path,os.O_PATH|os.O_CLOEXEC)
    rule=PathRule(access if path.is_dir() else 4,parent)
    assert libc.syscall(445,fd,1,ctypes.byref(rule),0)==0
    os.close(parent)
assert libc.prctl(38,1,0,0,0)==0
assert libc.syscall(446,fd,0)==0
os.close(fd)
sec=ctypes.CDLL(ctypes.util.find_library('seccomp'))
sec.seccomp_init.argtypes=[ctypes.c_uint32];sec.seccomp_init.restype=ctypes.c_void_p
sec.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
sec.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
sec.seccomp_load.argtypes=[ctypes.c_void_p];sec.seccomp_release.argtypes=[ctypes.c_void_p]
ctx=sec.seccomp_init(0x7fff0000)
for name in (b'socket',b'socketpair',b'connect'):
    number=sec.seccomp_syscall_resolve_name(name)
    assert sec.seccomp_rule_add(ctx,0x50000|errno.EACCES,number,0)==0
assert sec.seccomp_load(ctx)==0
sec.seccomp_release(ctx)
try: socket.socket(); raise AssertionError('network allowed')
except PermissionError: pass
import pyarrow as pa
try: pa.OSFile(str(forbidden),'r'); raise AssertionError('native source allowed')
except PermissionError: pass
from quanthus.expert_strategy.p3_retrospective_accounting_v2 import CorrectedRetrospectiveRuntime as RetrospectiveRuntime
from quanthus.expert_strategy.p3_retrospective_preparation import PURPOSE
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.backtest.data_provider import ReplayDataProvider
def reject(*a,**k): raise AssertionError('legacy provider opened')
ReplayDataProvider.open=reject
sha=os.environ['PREPARATION_SHA']
from quanthus.expert_evaluation.serialization import digest_value
plan=json.loads((root/'plan.json').read_bytes())
with RetrospectiveRuntime(repository,root,scratch/'run',manifest_sha256=sha,
 industry=load_static_industry_mapping(
  industry_path,expected_version='static-level1-concentration-research-v1'),
 consumer_admission={'purpose_id':PURPOSE,'mode':'SYNTHETIC_ONLY',
                     'preparation_sha256':sha,
                     'runtime_version':'p3-retrospective-accounting-v2',
                     'accounting_version':'formal-p2-position-accounting-2.0.0',
                     'code_config_digest':digest_value(plan['code_config_members'])}) as runtime:
 print(json.dumps(runtime.execute_all(),sort_keys=True))
"""
    reports = []
    for i in (1, 2):
        moved = tmp_path / f"moved-{i}"
        shutil.copytree(old, moved)
        shutil.rmtree(old)
        scratch = tmp_path / f"cold-work-{i}"
        scratch.mkdir()
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(moved),
                str(ROOT),
                str(scratch),
                str(tmp_path / "industry.json"),
                str(forbidden),
            ],
            cwd=scratch,
            capture_output=True,
            text=True,
            timeout=45,
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(ROOT / "src"),
                "TMPDIR": str(scratch),
                "XDG_CACHE_HOME": str(scratch),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PREPARATION_SHA": result["manifest_sha256"],
            },
        )
        assert completed.returncode == 0, completed.stderr
        reports.append(json.loads(completed.stdout))
        old = moved
    assert reports[0] == reports[1]
