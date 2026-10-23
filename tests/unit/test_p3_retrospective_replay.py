"""Exercise actual four-variant P2 arithmetic without disguising late knowledge."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.expert_strategy import p3_retrospective_preparation as prep
from quanthus.expert_strategy import p3_retrospective_replay as replay
from tests.unit.test_p3_base24_batch import industry
from tests.unit.test_p3_retrospective_bridge import INFO, ROOT, SOURCE
from tests.unit.test_p3_retrospective_features import DAYS, ports
from tests.unit.test_p3_retrospective_preparation import SCOPE


def build(tmp_path, *, observations=None):
    mapping = industry(tmp_path / "industry.json")
    return prep.materialize(
        ROOT,
        tmp_path / "prepared",
        ports=ports() if observations is None else observations,
        trading_dates=DAYS,
        source_manifest_sha256=SOURCE,
        information_cutoff=INFO,
        support_manifest_sha256=SOURCE,
        input_manifest_sha256=SOURCE,
        industry_manifest_sha256=replay.native._sha(mapping.path),
        source_roots=(tmp_path / "source",),
        scope=SCOPE,
        synthetic=True,
    )


def runtime(tmp_path, **kwargs):
    result = build(tmp_path, **kwargs)
    return replay.RetrospectiveRuntime(
        ROOT,
        tmp_path / "prepared",
        tmp_path / "runtime",
        manifest_sha256=result["manifest_sha256"],
        industry=industry(tmp_path / "industry.json"),
        consumer_admission={
            "purpose_id": prep.PURPOSE,
            "mode": "SYNTHETIC_ONLY",
            "preparation_sha256": result["manifest_sha256"],
        },
    )


def test_actual_four_variants_retain_late_clock_and_reconcile(tmp_path):
    with runtime(tmp_path) as r:
        assert r.events[0].record.knowledge_cutoff == INFO
        assert r.events[0].record.knowledge_cutoff > r.events[0].record.signal_close
        assert all(f.snapshot.knowledge_cutoff == INFO for f in r.frames)
        report = r.execute_all()
        assert tuple(report["variants"]) == ("B", "T", "R", "TR")
        assert report["session_index_builds"] == 1
        assert report["feature_panel_builds"] == report["real_comparisons_executed"] == 0
    base = tmp_path / "runtime/variant.B"
    reconciled = json.loads((base / "reconciliation.json").read_text())
    assert reconciled["fill_count"] > 0 and reconciled["reconciled"]
    for v in ("B", "T", "R", "TR"):
        root = tmp_path / "runtime" / ("variant." + v)
        assert json.loads((root / "reconciliation.json").read_text())["reconciled"]
        manifest = json.loads((root / "manifest.json").read_text())
        assert manifest["information_cutoff"] == INFO.isoformat()
        assert manifest["historical_availability_claim"] is False
        assert manifest["D_activated"] is False


def test_ordinary_engine_rejects_retrospective_signal_and_r2_rejects_wrong_snapshot(tmp_path):
    from quanthus.backtest.contracts import BacktestRunSpec, OrderSide

    with runtime(tmp_path) as r:
        f = r.frames[0]
        policy = r.engine.policy
        run = BacktestRunSpec(
            code_version=r._code_version(),
            parameter_hash="sha256:" + "a" * 64,
            data_snapshot_id=r.snapshot_id,
            feature_version="synthetic",
            rule_version="synthetic",
            run_id="r2-test",
            execution_policy_version=policy.policy_version,
            execution_profile_id=policy.active_profile.profile_id,
            schedule_version=policy.active_profile.schedule_version,
        )
        s = r._signal(
            run, f, "unit-1", "security-native", OrderSide.BUY, 100, Decimal("1"), "test", "digest"
        )
        with pytest.raises(BacktestInputError, match="historical-PIT"):
            BacktestEngine(policy)._validate_signal_clock(run, s)
        r.engine._validate_signal_clock(run, s)
        with pytest.raises(BacktestInputError, match="binding"):
            r.engine._validate_signal_clock(replace(run, official=True), s)
        with pytest.raises(BacktestInputError, match="binding"):
            r.engine._validate_signal_clock(
                run, replace(s, snapshot=replace(s.snapshot, snapshot_id="other"))
            )


def test_refuses_real_admission_and_false_claim_before_writing(tmp_path):
    result = build(tmp_path)
    plan = tmp_path / "prepared/plan.json"
    raw = json.loads(plan.read_text())
    raw["synthetic"] = False
    plan.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="REAL_SOURCE_ADMISSION"):
        replay.RetrospectiveRuntime(
            ROOT,
            tmp_path / "prepared",
            tmp_path / "denied",
            manifest_sha256=result["manifest_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission={"qualified": True},
        )
    assert not (tmp_path / "denied").exists()


def test_same_frozen_arithmetic_matches_strict_synthetic_control(tmp_path):
    from quanthus.expert_strategy.p3_base24_batch import NativeCompositeBackend
    from tests.unit.test_p3_base24_batch import build as strict_build

    a, b = tmp_path / "strict", tmp_path / "r2"
    a.mkdir()
    b.mkdir()
    job = strict_build(a)
    with NativeCompositeBackend(ROOT, a / "runs", job) as backend:
        expected = backend.execute_all()
    with runtime(b) as r:
        r.execute_all()
    for v in ("B", "T", "R", "TR"):
        actual = json.loads((b / "runtime" / ("variant." + v) / "manifest.json").read_text())
        assert actual["metrics"] == {k: str(x) for k, x in expected.variants[v].metrics.items()}


def test_unknown_held_action_remains_blocked_and_keeps_true_clock(tmp_path):
    from tests.unit.test_p3_retrospective_features import ports

    with runtime(tmp_path, observations=ports(unknown_action=63)) as r:
        frame = r.frames[1]
        assert frame.block_reason("security-native") is not None
        with pytest.raises(ValueError, match="HELD_ACTION_UNQUALIFIED"):
            frame.actions("security-native", Decimal("100"))
        held = r._held_event(r.events[0].record, frame)
        assert held.knowledge_cutoff == INFO > held.signal_close
        with pytest.raises(ValueError, match="HELD_ACTION_UNQUALIFIED"):
            r.execute_all()
    assert not (tmp_path / "runtime/COMPLETE.json").exists()
    failure = json.loads((tmp_path / "runtime/FAILED.json").read_text())
    assert failure["failed_variant"] == "B" and failure["complete"] is False


def test_empty_admission_registry_and_output_reuse_cannot_start_results(tmp_path):
    r = runtime(tmp_path)
    r.close()
    p = tmp_path / "prepared"
    sha = replay.native._sha(p / "manifest.json")
    with pytest.raises(ValueError, match="OUTPUT_ISOLATION"):
        replay.RetrospectiveRuntime(
            ROOT,
            p,
            tmp_path / "runtime",
            manifest_sha256=sha,
            industry=industry(tmp_path / "industry.json"),
            consumer_admission={
                "purpose_id": prep.PURPOSE,
                "mode": "SYNTHETIC_ONLY",
                "preparation_sha256": sha,
            },
        )
    assert not (tmp_path / "runtime/COMPLETE.json").exists()


def test_two_cold_processes_run_four_variants_after_relocation_without_sources(tmp_path):
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
from quanthus.expert_strategy.p3_retrospective_replay import RetrospectiveRuntime
from quanthus.expert_strategy.p3_retrospective_preparation import PURPOSE
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.backtest.data_provider import ReplayDataProvider
def reject(*a,**k): raise AssertionError('legacy provider opened')
ReplayDataProvider.open=reject
sha=os.environ['PREPARATION_SHA']
with RetrospectiveRuntime(repository,root,scratch/'run',manifest_sha256=sha,
 industry=load_static_industry_mapping(
  industry_path,expected_version='static-level1-concentration-research-v1'),
 consumer_admission={'purpose_id':PURPOSE,'mode':'SYNTHETIC_ONLY',
                     'preparation_sha256':sha}) as runtime:
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


def test_true_late_action_isolated_from_strict_engine_and_keeps_cash_quantity(tmp_path):
    from quanthus.backtest.contracts import BacktestRunSpec, UnitState

    with runtime(tmp_path) as r:
        f = r.frames[0]
        run = BacktestRunSpec(
            code_version=r._code_version(),
            parameter_hash="sha256:" + "a" * 64,
            data_snapshot_id=r.snapshot_id,
            feature_version="synthetic",
            rule_version="synthetic",
            run_id="action-test",
            execution_policy_version=r.engine.policy.policy_version,
            execution_profile_id=r.engine.policy.active_profile.profile_id,
            schedule_version=r.engine.policy.active_profile.schedule_version,
        )
        action = replay.RetrospectiveCorporateAction(
            "action",
            "security-native",
            f.snapshot.market_date,
            INFO,
            Decimal("0.1"),
            Decimal("0.1"),
            SOURCE,
            r.snapshot_id,
        )
        applied = f.decision_at.replace(hour=9, minute=0)
        with pytest.raises(BacktestInputError, match="historical-PIT"):
            BacktestEngine(r.engine.policy)._validate_action_clock(run, action, applied)
        r.engine._validate_action_clock(run, action, applied)
        with pytest.raises(BacktestInputError, match="binding"):
            r.engine._validate_action_clock(
                run, replace(action, known_at=INFO.replace(year=2027)), applied
            )
        state = UnitState(
            unit_id="u",
            target_notional=Decimal("10000.00"),
            cash=Decimal("9000.00"),
            security_code="security-native",
            settled_quantity=100,
            position_cost=Decimal("1000.00"),
            commission_expense=Decimal("0.00"),
            stamp_duty_expense=Decimal("0.00"),
            transfer_fee_expense=Decimal("0.00"),
            realized_pnl=Decimal("0.00"),
            dividend_income=Decimal("0.00"),
            external_pool=Decimal("0.00"),
            last_mark_price=Decimal("10"),
            last_mark_snapshot_id="before",
        )
        result = r.engine.apply_corporate_action(
            run=run,
            opening_state=state,
            action=action,
            applied_at=applied,
            post_action_mark_price=Decimal("9"),
            mark_snapshot_id="after",
        )
        assert result.state.settled_quantity == 110
        assert result.state.dividend_income == Decimal("10.00")
        assert action.known_at == INFO and action.known_at > applied


def test_normalized_action_file_checks_true_time_and_duplicate_event(tmp_path):
    a = {
        "contract": "p10-retained-implemented-plan-economic-view.v1",
        "economic_event_id": "sha256:" + "a" * 64,
        "security_id": "security-native",
        "effective_date": DAYS[10].isoformat(),
        "known_at": INFO.isoformat(),
        "cash_dividend_per_share": {"Decimal": "0.1"},
        "bonus_share_ratio": {"Decimal": "0.1"},
        "historical_pit_granted": False,
    }
    file = tmp_path / "actions.json"
    file.write_text(json.dumps([a]))
    parsed = prep.load_actions(file, INFO)
    assert parsed[0]["cash_dividend_per_share"] == Decimal("0.1")
    file.write_text(json.dumps([a, a]))
    with pytest.raises(ValueError, match="IDENTITY_OR_TIME"):
        prep.load_actions(file, INFO)


def test_independent_builds_share_semantic_snapshot_not_timing_receipt(tmp_path):
    a, b = tmp_path / "first", tmp_path / "independent"
    a.mkdir()
    b.mkdir()
    with runtime(a) as first:
        identity = first.snapshot_id
        code_identity = first._code_version()
        receipt = first.preparation_sha
        shared = json.loads((a / "prepared/variants.json").read_text())[0][
            "shared_prerequisite_digest"
        ]
    with runtime(b) as second:
        assert second.preparation_sha != receipt  # elapsed time/resource receipts differ
        assert json.loads((b / "prepared/variants.json").read_text())[0][
            "shared_prerequisite_digest"
        ] == shared
        assert second.snapshot_id == identity
        assert second._code_version() == code_identity


def test_session_index_does_not_execute_warmup_or_first_decision_day(tmp_path):
    import gzip
    from datetime import date

    import pyarrow.parquet as pq

    from quanthus.expert_strategy import p3_retrospective_bridge as bridge
    from tests.unit.test_p3_base24_bridge import support
    from tests.unit.test_p3_retrospective_bridge import observation

    days = tuple(map(date.fromisoformat, [
        "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03", "2025-01-06"
    ]))
    prepared, output = tmp_path / "prepared", tmp_path / "index"
    prepared.mkdir()
    output.mkdir()
    with gzip.open(prepared / "ports.jsonl.gz", "wt") as f:
        for day in days:
            row = observation(day)
            if day < date(2025, 1, 1):
                row["r2_observation_role"] = "WARMUP"
            p = bridge.decision_port(
                row, information_cutoff=INFO, source_manifest_sha256=SOURCE,
                support=replace(support(row), execution_known_at=INFO),
                static_industry_id="static-L1", warmup_dates=days[:2],
            )
            assert p.selected is not None
            f.write(json.dumps(p, default=prep._typed) + "\n")
    plan = {
        "trading_dates": [d.isoformat() for d in days],
        "scope": [d.isoformat() for d in days[2:]],
        "information_cutoff": INFO.isoformat(),
        "source_manifest_sha256": SOURCE,
        "support_manifest_sha256": SOURCE,
    }
    clocks = replay._index(prepared, output, plan, "r2-warmup-test", lambda: None)
    assert set(clocks) == set(days)
    assert pq.read_table(output / "sessions.parquet")["market_date"].to_pylist() == list(days[3:])


def test_registered_semantic_receipt_reuses_proof_but_rechecks_every_member(tmp_path, monkeypatch):
    result = build(tmp_path)
    prepared = tmp_path / "prepared"
    verified = prep.verify(ROOT, prepared, manifest_sha256=result["manifest_sha256"])
    receipt = {
        "purpose_id": prep.PURPOSE,
        "preparation_sha256": result["manifest_sha256"],
        "preparation_verification": verified,
    }
    registered = [replay.digest_value(receipt)]
    original_json = replay.native._json

    def configuration(path):
        if path.name == "retrospective-execution.v1.json":
            value = original_json(path)
            return {**value, "admitted_consumer_receipt_sha256": registered}
        return original_json(path)

    monkeypatch.setattr(replay.native, "_json", configuration)

    def no_repeat(*args, **kwargs):
        raise AssertionError("full semantic verification must not repeat after exact admission")

    monkeypatch.setattr(prep, "verify", no_repeat)
    replay._verify_registered_preparation(ROOT, prepared, result["manifest_sha256"], receipt)
    forged = {**receipt, "preparation_verification": {**verified, "eligible_candidates": 999}}
    with pytest.raises(ValueError, match="REGISTERED_VERIFICATION_REQUIRED"):
        replay._verify_registered_preparation(ROOT, prepared, result["manifest_sha256"], forged)
    registered.append(replay.digest_value(forged))
    with pytest.raises(ValueError, match="REGISTERED_VERIFICATION_RESULT"):
        replay._verify_registered_preparation(ROOT, prepared, result["manifest_sha256"], forged)
    (prepared / "features.parquet").write_bytes(b"damaged")
    with pytest.raises(ValueError):
        replay._verify_registered_preparation(ROOT, prepared, result["manifest_sha256"], receipt)
