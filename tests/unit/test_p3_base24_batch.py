"""Actual candidate/features/four P2 variants on disposable synthetic native rows."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.backtest.baseline_research import BaselineFeaturePanel
from quanthus.backtest.contracts import CorporateAction
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_batch as batch
from quanthus.expert_strategy import p3_base24_bridge as bridge
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.industry_mapping import load_static_industry_mapping
from tests.unit.test_p3_base24_bridge import clock, row, support
from tests.unit.test_p3_base24_native import build as prepare_native
from tests.unit.test_p3_base24_native import source as native_source_fixture

ROOT = Path(__file__).resolve().parents[2]
SHA = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
DAYS = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(65))
SCOPE = (DAYS[61], DAYS[63], DAYS[64])


def industry(path):
    unsigned = {
        "schema_version": "1.0.0",
        "mapping_version": "static-level1-concentration-research-v1",
        "usage": "static_non_pit_concentration_only",
        "source": {
            "provider": "synthetic",
            "endpoint": "fixture",
            "retrieved_at": "2026-10-22T00:00:00+00:00",
            "source_revision": "synthetic",
        },
        "memberships": [
            {
                "security_id": "security-native",
                "stock_code": "000001",
                "exchange": "SZSE",
                "level1_industry_id": "static-L1",
            }
        ],
    }
    path.write_text(json.dumps({**unsigned, "artifact_digest": digest_value(unsigned)}))
    return load_static_industry_mapping(path, expected_version=unsigned["mapping_version"])


def observation(day, *, cutoff, execution=False, missing_support=False, action=False):
    r = row(day)
    # All synthetic execution prices match the existing P2 price tick. The source
    # Decimal scale is nevertheless retained at the native/feature TMP boundary.
    fraction = (
        Decimal("0.005")
        if day == DAYS[60]
        else Decimal("0.02")
        if day == DAYS[61]
        else Decimal("0.01")
    )
    amount = (
        Decimal("5000000")
        if day == DAYS[60]
        else Decimal("20000000")
        if day == DAYS[61]
        else Decimal("10000000")
    )
    r.update(
        open=Decimal("100.0000000000"),
        high=Decimal("100") * (1 + fraction),
        low=Decimal("100"),
        close=Decimal("100.0000000000"),
        turnover=amount,
        volume=Decimal("100000"),
    )
    s = replace(
        support(r),
        upper_limit_price=Decimal("110"),
        lower_limit_price=Decimal("90"),
        adjustment_effective=action,
    )
    return bridge.decision_port(
        r,
        cutoff=cutoff,
        source_manifest_sha256=SHA,
        support=None if missing_support else s,
        static_industry_id="static-L1",
        execution_only=execution,
    )


def parts(*, missing=False, action=False):
    for i in range(61, 64):
        yield batch.NativeCutoff(
            clock(DAYS[i]),
            tuple(
                observation(d, cutoff=clock(DAYS[i]), missing_support=missing and d == DAYS[i])
                for d in DAYS[: i + 1]
            ),
            (observation(DAYS[i + 1], cutoff=clock(DAYS[i + 1]), execution=True, action=action),),
            DAYS[i + 1],
            clock(DAYS[i + 1], 9),
            clock(DAYS[i + 1]),
        )


def build(tmp_path, **kwargs):
    mapping = industry(tmp_path / "source-industry.json")
    return batch.materialize_job(
        ROOT,
        tmp_path / "job",
        cutoffs=parts(**kwargs),
        trading_dates=DAYS,
        industry=mapping,
        source_manifest_sha256=SHA,
        qualification_digest=OTHER,
        scope=SCOPE,
        synthetic=True,
        source_roots=(tmp_path / "source",),
    )


def test_actual_native_candidates_features_and_four_variants_share_one_prerequisite(
    tmp_path, monkeypatch
):
    # A legacy provider access is a regression, not a permitted fallback.
    from quanthus.backtest.data_provider import ReplayDataProvider

    monkeypatch.setattr(
        ReplayDataProvider, "open", lambda *_a, **_kw: pytest.fail("legacy provider")
    )
    job = build(tmp_path)
    assert len(job.events) == 1
    assert job.report.row_count == 3
    with BaselineFeaturePanel(job.root / "features.parquet", job.report) as panel:
        feature = panel.feature(DAYS[61], "security-native")
        assert feature.feature_ready and feature.entry_eligible
        assert feature.sma60 == Decimal("100.0")
    masks = list(batch._lines(job.root / "masks.jsonl"))
    assert masks[0]["eligible"] is True
    assert all(not (job.root / n).exists() for n in ("history-TMP.parquet", "spill"))
    with batch.NativeCompositeBackend(ROOT, tmp_path / "four", job) as backend:
        result = backend.execute_all()
        assert tuple(result.variants) == ("B", "T", "R", "TR")
        assert result.telemetry["provider_builds"] == 1
        assert result.telemetry["feature_panel_builds"] == 1
        assert result.telemetry["cross_batch_cache_used"] is False
        reconciliation = json.loads(
            (backend._variant_root("B") / "reconciliation.json").read_text()
        )
        assert reconciliation["reconciled"] is True
        assert reconciliation["fill_count"] > 0
    assert job.manifest["B_accepted"] is False
    assert job.manifest["D_activated"] is False


def test_missing_support_is_common_unavailable_not_suspension_or_zero(tmp_path):
    job = build(tmp_path, missing=True)
    masks = list(batch._lines(job.root / "masks.jsonl"))
    assert all(not r["eligible"] for r in masks)
    assert job.report.entry_eligible_count == 0
    assert any("ENTRY_SUPPORT_UNAVAILABLE" in r["reasons"] for r in masks)


def test_two_relocations_read_native_job_without_sources_or_legacy_paths(tmp_path):
    job = build(tmp_path)
    original = job.digest
    for number in (1, 2):
        destination = tmp_path / f"moved-{number}"
        shutil.copytree(job.root, destination)
        shutil.rmtree(job.root)
        job = batch.load_job(destination)
        assert job.digest == original
        assert len(job.events) == 1
        s = job.frames[0].session("security-native")
        assert s.open_price.as_tuple() == Decimal("100.0000000000").as_tuple()
        assert s.close_at == clock(DAYS[62])
    (tmp_path / "source-industry.json").unlink()
    with batch.NativeCompositeBackend(ROOT, tmp_path / "cold-four", job) as backend:
        assert len(backend.execute_all().variants) == 4


def test_two_new_processes_rebuild_all_four_variants_with_native_network_denied(tmp_path):
    import ctypes.util

    if not ctypes.util.find_library("seccomp"):
        pytest.skip("libseccomp unavailable; native network denial not verified")
    job = build(tmp_path)
    (tmp_path / "source-industry.json").unlink()
    script = r"""
import ctypes, ctypes.util, errno, json, socket, sys
from pathlib import Path
sec=ctypes.CDLL(ctypes.util.find_library("seccomp"))
sec.seccomp_init.argtypes=[ctypes.c_uint32]; sec.seccomp_init.restype=ctypes.c_void_p
sec.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
sec.seccomp_syscall_resolve_name.restype=ctypes.c_int
sec.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
sec.seccomp_load.argtypes=[ctypes.c_void_p]; sec.seccomp_release.argtypes=[ctypes.c_void_p]
ctx=sec.seccomp_init(0x7fff0000)
assert ctx
for name in (b"socket",b"socketpair",b"connect"):
    syscall=sec.seccomp_syscall_resolve_name(name)
    assert sec.seccomp_rule_add(ctx,0x50000|errno.EACCES,syscall,0)==0
assert sec.seccomp_load(ctx)==0
sec.seccomp_release(ctx)
try: socket.socket(); raise AssertionError("native network allowed")
except PermissionError: pass
from quanthus.expert_strategy.p3_base24_batch import load_job, NativeCompositeBackend
from quanthus.backtest.data_provider import ReplayDataProvider
def reject(*a,**kw): raise AssertionError("legacy provider opened")
ReplayDataProvider.open=reject
job=load_job(Path(sys.argv[1]))
with NativeCompositeBackend(Path(sys.argv[2]),Path(sys.argv[3]),job) as backend:
    result=backend.execute_all()
summary={"input":job.digest,"variants":{k:v.digest for k,v in result.variants.items()}}
print(json.dumps(summary,sort_keys=True))
"""
    summaries = []
    for i in (1, 2):
        moved = tmp_path / f"process-input-{i}"
        shutil.copytree(job.root, moved)
        shutil.rmtree(job.root)
        cwd = tmp_path / f"process-work-{i}"
        cwd.mkdir()
        completed = subprocess.run(
            [sys.executable, "-c", script, str(moved), str(ROOT), str(cwd / "four")],
            cwd=cwd,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(cwd),
                "TMPDIR": str(cwd),
                "XDG_CACHE_HOME": str(cwd),
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert completed.returncode == 0, completed.stderr
        summaries.append(json.loads(completed.stdout))
        job = batch.load_job(moved)
    assert summaries[0] == summaries[1]


def test_verified_native_closure_is_wired_and_execution_does_not_open_a_later_decision(tmp_path):
    source = native_source_fixture.__wrapped__(tmp_path)
    prepared = tmp_path / "prepared"
    receipt = prepare_native(source, prepared)
    cutoff = clock(date(2025, 1, 2))
    with native.open_prepared(prepared, manifest_sha256=receipt["manifest_sha256"]) as view:
        selected = batch.cutoffs_from_native(
            view,
            [(cutoff, clock(date(2025, 1, 4), 9), clock(date(2025, 1, 4)))],
            support_for=lambda _r, _t, _e: None,
            static_industry={"s": "static-L1"},
        )
        part = next(selected)
        history = list(part.history)
        assert len(history) == 1 and history[0].candidate is not None
        # The exact next fixture session is Jan3. Skipping it is not a valid pair.
        with pytest.raises(native.InputError, match="EXACT_NEXT_SESSION"):
            list(part.execution)
        with pytest.raises(native.InputError, match="FROZEN_DECISION_WINDOW"):
            list(view.market_batches(clock(date(2026, 9, 18))))
        direct = list(view.execution_batches(cutoff, decision_date=date(2025, 1, 1)))
        assert sum(b.num_rows for b in direct) == 1


def test_hidden_unknown_action_cannot_be_mistaken_for_a_no_action_observation(tmp_path):
    p = next(parts())
    execution = replace(next(iter(p.execution)), adjustment_effective=None)
    encoded = batch._execution_frame(replace(p, execution=(execution,)), SHA, "synthetic")
    assert encoded["blocked_actions"] == ["security-native"]
    assert encoded["sessions"][0]["close_price"] is None


def test_qualified_action_terms_survive_serialization_and_existing_reference_mark(tmp_path):
    mapping = industry(tmp_path / "industry.json")
    selected = []
    for p in parts(action=True):
        action = batch.NativeAction(
            SHA,
            OTHER,
            CorporateAction(
                "synthetic-" + p.next_session.isoformat(),
                "security-native",
                p.next_session,
                p.cutoff,
                Decimal("1.0000000000"),
                Decimal("0.1000000000"),
                SHA,
                "synthetic",
            ),
        )
        selected.append(replace(p, execution_actions=(action,)))
    job = batch.materialize_job(
        ROOT,
        tmp_path / "job",
        cutoffs=selected,
        trading_dates=DAYS,
        industry=mapping,
        source_manifest_sha256=SHA,
        qualification_digest=OTHER,
        scope=SCOPE,
        synthetic=True,
        source_roots=(tmp_path / "source",),
    )
    loaded = batch.load_job(job.root)
    steps = loaded.frames[0].actions("security-native", Decimal("100"))
    assert steps[0].post_action_mark_price == Decimal("90.00")
    assert steps[0].action.cash_dividend_per_share.as_tuple() == Decimal("1.0000000000").as_tuple()
    assert steps[0].action.known_at == selected[0].cutoff
    one = selected[0].execution_actions[0]
    extra = replace(one, action=replace(one.action, action_id="another-raw-term"))
    with pytest.raises(native.InputError, match="AGGREGATION_REQUIRED"):
        batch._execution_frame(
            replace(selected[0], execution_actions=(one, extra)), SHA, "synthetic"
        )


@pytest.mark.parametrize("defect", ["late", "other_source", "no_action_conflict"])
def test_action_closure_cannot_override_source_clock_or_no_action_evidence(defect):
    p = next(parts(action=True))
    a = batch.NativeAction(
        SHA,
        OTHER,
        CorporateAction(
            "synthetic",
            "security-native",
            p.next_session,
            p.next_close if defect == "late" else p.cutoff,
            Decimal("1"),
            Decimal("0"),
            SHA,
            "s",
        ),
    )
    if defect == "other_source":
        a = replace(a, source_manifest_sha256=OTHER)
    if defect == "no_action_conflict":
        p = replace(p, execution=(replace(next(iter(p.execution)), adjustment_effective=False),))
    with pytest.raises(native.InputError):
        batch._execution_frame(replace(p, execution_actions=(a,)), SHA, "synthetic")


def test_same_qualified_input_independently_rebuilds_prerequisite_bytes(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a, b = build(first), build(second)
    assert a.digest == b.digest
    assert a.manifest["files"] == b.manifest["files"]


@pytest.mark.parametrize("broken", ["features.parquet", "frames.jsonl", "READY"])
def test_broken_job_cannot_fall_back(tmp_path, broken):
    job = build(tmp_path)
    (job.root / broken).write_bytes(b"broken")
    with pytest.raises((native.InputError, ValueError)):
        batch.load_job(job.root)


def test_unknown_action_blocks_held_step_and_does_not_invent_adjustment(tmp_path):
    job = build(tmp_path, action=True)
    frame = job.frames[0]
    assert frame.session("security-native").open_price is None
    with pytest.raises(native.InputError, match="ACTION_EXECUTION_UNQUALIFIED"):
        frame.actions("security-native", Decimal("100"))


def test_stale_source_or_wrong_cutoff_fails_before_ready(tmp_path):
    mapping = industry(tmp_path / "industry.json")
    p = next(parts())
    wrong = replace(p, history=(replace(next(iter(p.history)), source_manifest_sha256=OTHER),))
    with pytest.raises(native.InputError, match="HISTORY_BINDING"):
        batch.materialize_job(
            ROOT,
            tmp_path / "job",
            cutoffs=[wrong],
            trading_dates=DAYS,
            industry=mapping,
            source_manifest_sha256=SHA,
            qualification_digest=OTHER,
            scope=SCOPE,
            synthetic=True,
            source_roots=(tmp_path / "source",),
        )
    assert not (tmp_path / "job/READY").exists()
    assert (tmp_path / "job/plan.json").exists()


def test_source_overlap_and_nonfrozen_scope_are_rejected_before_mkdir(tmp_path):
    mapping = industry(tmp_path / "industry.json")
    for output, synthetic in [(tmp_path, True), (tmp_path / "wrong-scope", False)]:
        with pytest.raises(native.InputError):
            batch.materialize_job(
                ROOT,
                output,
                cutoffs=(),
                trading_dates=DAYS,
                industry=mapping,
                source_manifest_sha256=SHA,
                qualification_digest=OTHER,
                scope=SCOPE,
                synthetic=synthetic,
                source_roots=(tmp_path / "source",),
            )
    assert not (tmp_path / "plan.json").exists()
    assert not (tmp_path / "wrong-scope").exists()
