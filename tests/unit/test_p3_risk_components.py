"""Fixed component effects preserve guards, native anchors and cold admission."""

import ast
import ctypes.util
import gzip
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_base24_native as n
from quanthus.expert_strategy import p3_component_checkpoint as checkpoint
from quanthus.expert_strategy import p3_retrospective_preparation as prep
from quanthus.expert_strategy import p3_risk_components as c
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner
from tests.unit.test_formal_expert_strategy import EVENT_ID, _context, _held_facts
from tests.unit.test_p3_base24_batch import industry
from tests.unit.test_p3_retrospective_bridge import ROOT
from tests.unit.test_p3_retrospective_replay import build


@pytest.fixture(scope="module")
def runner():
    registry = ExpertStrategyRegistry(ROOT)
    return FormalStrategyRunner(registry.card, registry.topology_manifest.digest)


def admission(result, plan):
    return {
        "purpose_id": prep.PURPOSE,
        "mode": "SYNTHETIC_ONLY",
        "preparation_sha256": result["manifest_sha256"],
        "runtime_version": c.VERSION,
        "accounting_version": accounting.VERSION,
        "code_config_digest": digest_value(plan["code_config_members"]),
        "experiment_id": c.VERSION,
        "experiment_request_digest": digest_value(c.load_request(ROOT)),
    }


def admitted_runtime(tmp_path, changes=None):
    result = build(tmp_path)
    plan = n._json(tmp_path / "prepared/plan.json")
    receipt = admission(result, plan)
    receipt.update(changes or {})
    return c.RiskComponentRuntime(
        ROOT,
        tmp_path / "prepared",
        tmp_path / "runtime",
        manifest_sha256=result["manifest_sha256"],
        industry=industry(tmp_path / "industry.json"),
        consumer_admission=receipt,
    )


@pytest.mark.parametrize(
    "vol,rsi,blocked",
    [
        (".20", "60", False),
        (".50", "75", False),
        (".51", "60", True),
        (".20", "76", True),
        (".51", "76", True),
    ],
)
def test_only_filter_cells_veto_and_cap_still_applies_to_high_risk(runner, vol, rsi, blocked):
    decisions = {}
    for v in c.VARIANTS:
        d = c.ComponentEntryRunner(runner, v).evaluate_component(
            event_id=EVENT_ID,
            context=_context(volatility=Decimal(vol), rsi=Decimal(rsi)),
            trend_state="stable",
            entry_evidence_ready=True,
        )
        decisions[v] = d
        should_block = blocked and v in {"TE", "TR"}
        assert (d.action.final_action == GovernedAction.NO_ACTION) == should_block
        if not should_block:
            assert d.target_maximum_position == Decimal(".30" if v in {"T", "TH"} else ".20")
    assert replace(decisions["T"], intervention_id=None) == replace(
        decisions["TH"], intervention_id=None
    )
    assert replace(decisions["TE"], intervention_id=None) == replace(
        decisions["TR"], intervention_id=None
    )
    tc = decisions["TC"].governance.to_dict()
    assert "control.p3-fixed-entry-cap" in json.dumps(tc)
    assert "system.base-position" in json.dumps(tc)


@pytest.mark.parametrize("missing", ["vol", "rsi", "trend", "entry"])
def test_every_cell_keeps_missing_data_guardians(runner, missing):
    for v in c.VARIANTS:
        d = c.ComponentEntryRunner(runner, v).evaluate_component(
            event_id=EVENT_ID,
            context=_context(
                volatility=None if missing == "vol" else Decimal(".2"),
                rsi=None if missing == "rsi" else Decimal("60"),
            ),
            trend_state=None if missing == "trend" else "stable",
            entry_evidence_ready=missing != "entry",
        )
        assert d.action.final_action == GovernedAction.NO_ACTION
        assert d.governance.required_action.value == "block_new"


def test_held_policy_keeps_absolute_ten_percent_target_and_base_exit(runner):
    runtime = object.__new__(c.RiskComponentRuntime)
    for v in c.VARIANTS:
        facts = _held_facts()
        d = runner.evaluate_held(
            event_id=EVENT_ID,
            context=_context(position_state="held", position_evidence=facts.evidence),
            position_facts=facts,
            intervention_id=runtime._held_intervention_id(c.VERSION + ":" + v),
        )
        assert (d.action.final_action == GovernedAction.REDUCE) == (v in {"TH", "TR"})
        if v in {"TH", "TR"}:
            assert d.target_maximum_position == Decimal(".10")
    assert (
        c.RiskComponentRuntime._review_positions
        is accounting.AccountingCorrectedReplayRuntime._review_positions
    )


def test_five_cells_actual_synthetic_reconciliation_and_no_overwrite(tmp_path):
    with admitted_runtime(tmp_path) as runtime:
        assert (
            runtime._marked_return.__func__
            is accounting.AccountingCorrectedReplayRuntime._marked_return
        )
        report = runtime.execute_all()
        assert tuple(report["variants"]) == c.ORDER
        assert report["session_index_builds"] == 1
        assert report["real_comparisons_executed"] == 0
        sealed = (tmp_path / "runtime/COMPLETE.json").read_bytes()
        with pytest.raises(FileExistsError):
            runtime.execute_all()
        assert (tmp_path / "runtime/COMPLETE.json").read_bytes() == sealed
    for v in c.VARIANTS:
        root = tmp_path / "runtime" / ("variant." + v)
        assert n._json(root / "reconciliation.json")["reconciled"]
        m = n._json(root / "manifest.json")
        assert m["runtime_version"] == c.VERSION
        assert m["experiment_request_digest"] == digest_value(c.load_request(ROOT))
        assert m["production_authorized"] is m["successor_strategy_selected"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"runtime_version": "p3-retrospective-accounting-v2"},
        {"accounting_version": "old"},
        {"code_config_digest": "sha256:" + "0" * 64},
        {"experiment_request_digest": "sha256:" + "0" * 64},
        {"experiment_id": "other"},
    ],
)
def test_wrong_adapter_admission_refuses_before_output(tmp_path, changes):
    with pytest.raises(ValueError, match="ADAPTER_ADMISSION_BINDING"):
        admitted_runtime(tmp_path, changes)
    assert not (tmp_path / "runtime").exists()


def test_unregistered_real_receipt_cannot_activate_components(tmp_path):
    result = build(tmp_path)
    path = tmp_path / "prepared/plan.json"
    plan = n._json(path)
    plan["synthetic"] = False
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="REAL_SOURCE_ADMISSION"):
        c.RiskComponentRuntime(
            ROOT,
            tmp_path / "prepared",
            tmp_path / "denied",
            manifest_sha256=result["manifest_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission=admission(result, plan),
        )
    assert not (tmp_path / "denied").exists()


def row(**changes):
    return dict(
        aggregate_score="0",
        allocated=False,
        decision_key="entry:one",
        filled=False,
        final_action="no_action",
        formal_decision_digest="physical",
        market_date="2025-01-02",
        reason_codes=["veto"],
        surface="entry",
        target_maximum_position=None,
        unit_id="pool",
        **changes,
    )


def test_anchor_keeps_no_fill_reasons_caps_and_requires_exact_coordinates():
    a = row()
    coords = {"entry:one": "security:2025-01-02"}
    expected = c.decision_anchor_digest([a], coords)
    assert (
        c.decision_anchor_digest([{**a, "formal_decision_digest": "new-physical"}], coords)
        == expected
    )
    for field, value in [
        ("reason_codes", ["different"]),
        ("target_maximum_position", ".2"),
        ("filled", True),
        ("final_action", "trial_entry"),
    ]:
        assert c.decision_anchor_digest([{**a, field: value}], coords) != expected
    for rows, mapping, error in [
        ([a, a], coords, "DUPLICATE"),
        ([a], {}, "COORDINATE_MISSING"),
        ([a], {**coords, "entry:two": coords["entry:one"]}, "COORDINATES"),
    ]:
        with pytest.raises(ValueError, match=error):
            c.decision_anchor_digest(rows, mapping)


def test_decision_anchor_failure_stops_before_any_new_cell(tmp_path, monkeypatch):
    runtime = object.__new__(c.RiskComponentRuntime)
    runtime.workspace = tmp_path
    runtime.synthetic = False
    runtime.component_request = {
        "anchor_artifact_sha256": {"T": {}},
        "anchor_decision_sha256": {"T": "sha256:" + "0" * 64},
    }
    runtime.events = [
        SimpleNamespace(
            record=SimpleNamespace(
                event_id="one",
                identity=SimpleNamespace(security_id="security", signal_date=date(2025, 1, 2)),
            )
        )
    ]
    monkeypatch.setattr(runtime, "_guard", lambda: None)
    monkeypatch.setattr(runtime, "_adapter_binding", lambda: {"runtime_version": c.VERSION})
    monkeypatch.setattr(
        runtime,
        "run_research_variant",
        lambda v, p: (
            SimpleNamespace(replay_digest="x"),
            {"decisions.json": json.dumps([row()]).encode()},
        ),
    )
    with pytest.raises(ValueError, match="OLD_DECISION_ANCHOR_MISMATCH"):
        runtime.execute_all()
    assert n._json(tmp_path / "FAILED.json")["failed_variant"] == "T"
    assert not (tmp_path / "variant.TC").exists() and not (tmp_path / "COMPLETE.json").exists()


def test_ledger_anchor_maps_exact_id_only_and_keeps_all_values():
    old = [
        {"last_mark_snapshot_id": "old", "cash": "123.4500", "count": 7},
        {"last_mark_snapshot_id": None, "cash": "0.00", "count": 0},
    ]
    new = [{**old[0], "last_mark_snapshot_id": "new"}, old[1]]
    expected = digest_value(old)
    assert (
        c.ledger_anchor_digest(
            canonical_json_bytes(new), current_snapshot="new", old_snapshot="old"
        )
        == expected
    )
    for key, value in [("cash", "123.4501"), ("count", 8)]:
        changed = [{**new[0], key: value}, new[1]]
        assert (
            c.ledger_anchor_digest(
                canonical_json_bytes(changed), current_snapshot="new", old_snapshot="old"
            )
            != expected
        )
    for unexpected in ["old", "unknown", "new.action.unapproved"]:
        with pytest.raises(ValueError, match="ANCHOR_SNAPSHOT"):
            c.ledger_anchor_digest(
                canonical_json_bytes([{**new[0], "last_mark_snapshot_id": unexpected}]),
                current_snapshot="new",
                old_snapshot="old",
            )


def checkpoint_setup(tmp_path):
    import pyarrow.parquet as pq

    with admitted_runtime(tmp_path) as runtime:
        snapshot = runtime.snapshot_id
        original_clocks = runtime.clocks.copy()
    source = tmp_path / "runtime/sessions.parquet"
    resume = tmp_path / "resume"
    resume.mkdir()
    shutil.copyfile(source, resume / "session-index-checkpoint.parquet")
    plan = n._json(tmp_path / "prepared/plan.json")
    descriptor = {
        "schema_version": "quanthus.p3-component-index-checkpoint.v1",
        "source_result_role": "synthetic",
        "index_sha256": n._sha(source),
        "index_rows": pq.ParquetFile(source).metadata.num_rows,
        "old_snapshot_id": snapshot,
        "input_identity": checkpoint.input_identity(tmp_path / "prepared", plan),
        "producer_proof_sha256": "sha256:" + "1" * 64,
        "current_code_config_digest": digest_value(plan["code_config_members"]),
        "prior_runtime_seconds": 1,
    }
    result = n._json(tmp_path / "prepared/PREPARATION_COMPLETE.json")
    receipt = {**admission(result, plan), "session_index_checkpoint": descriptor}
    return resume, receipt, original_clocks


def test_checkpoint_reuses_exact_own_index_including_first_day_clocks(tmp_path, monkeypatch):
    from quanthus.expert_strategy import p3_retrospective_replay as legacy

    resume, receipt, clocks = checkpoint_setup(tmp_path)

    def forbidden(*args):
        raise AssertionError("expensive index rebuilt")

    monkeypatch.setattr(legacy, "_index", forbidden)
    with c.RiskComponentRuntime(
        ROOT,
        tmp_path / "prepared",
        resume / "run",
        manifest_sha256=receipt["preparation_sha256"],
        industry=industry(tmp_path / "industry.json"),
        consumer_admission=receipt,
    ) as runtime:
        assert runtime.clocks == clocks
        report = runtime.execute_all()
        assert report["session_index_builds"] == 0
        assert report["session_index_checkpoint_reuses"] == 1
    proof = n._json(resume / "run/index-recovery.safe.json")
    assert proof["before_normalized_sha256"] == proof["after_normalized_sha256"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("index_sha256", "sha256:" + "0" * 64),
        ("source_result_role", "primary"),
        ("index_rows", 999999),
        ("input_identity", "sha256:" + "0" * 64),
        ("old_snapshot_id", "wrong"),
        ("current_code_config_digest", "sha256:" + "0" * 64),
        ("prior_runtime_seconds", 3600),
    ],
)
def test_checkpoint_rejects_unbound_role_inputs_bytes_and_budget(tmp_path, field, value):
    resume, receipt, _ = checkpoint_setup(tmp_path)
    receipt["session_index_checkpoint"][field] = value
    with pytest.raises(ValueError, match="COMPONENT_CHECKPOINT"):
        c.RiskComponentRuntime(
            ROOT,
            tmp_path / "prepared",
            resume / "run",
            manifest_sha256=receipt["preparation_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission=receipt,
        )
    assert not (resume / "run/COMPLETE.json").exists()


def test_checkpoint_mapping_changes_only_exact_snapshot(tmp_path):
    import pyarrow.parquet as pq

    resume, receipt, _ = checkpoint_setup(tmp_path)
    output = resume / "mapped"
    output.mkdir()
    descriptor = receipt["session_index_checkpoint"]
    replacement = prep.PURPOSE + "/" + "a" * 64
    checkpoint.resume_index(
        tmp_path / "prepared",
        output,
        n._json(tmp_path / "prepared/plan.json"),
        replacement,
        descriptor,
        "synthetic",
        lambda: None,
    )
    old = pq.read_table(resume / "session-index-checkpoint.parquet").to_pylist()
    new = pq.read_table(output / "sessions.parquet").to_pylist()
    for a, b in zip(old, new, strict=True):
        p = json.loads(b["payload"])
        assert p["session"]["snapshot_id"] == replacement
        p["session"]["snapshot_id"] = descriptor["old_snapshot_id"]
        assert json.loads(a["payload"]) == p
        assert a["market_date"] == b["market_date"] and a["security_id"] == b["security_id"]


@pytest.mark.parametrize("failure", ["first_day_missing", "clock_conflict"])
def test_checkpoint_clocks_cannot_be_fabricated_or_inconsistent(tmp_path, failure):
    first, second = "2025-01-02", "2025-01-03"
    plan = {
        "trading_dates": [first, second],
        "information_cutoff": "2026-10-23T00:00:00+00:00",
        "source_manifest_sha256": "sha256:" + "1" * 64,
        "scope": [first, first, second],
    }
    rows = []
    for security, day in [("a", first), ("a", second), ("b", first)]:
        if failure == "first_day_missing" and day == first:
            continue
        rows.append(
            {
                "security_id": security,
                "business_date": {"date": day},
                "information_cutoff": {"datetime": plan["information_cutoff"]},
                "source_manifest_sha256": plan["source_manifest_sha256"],
                "selected": {
                    "open_at": {"datetime": day + "T09:30:00+08:00"},
                    "close_at": {
                        "datetime": day
                        + ("T14:00:00+08:00" if security == "b" else "T15:00:00+08:00")
                    },
                },
            }
        )
    with gzip.open(tmp_path / "ports.jsonl.gz", "wt") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="COMPONENT_CHECKPOINT_CLOCK"):
        checkpoint._port_clocks(tmp_path, plan, lambda: None)


def series_points():
    rows = [
        {
            "market_date": (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
            "return": "0.001" if i % 2 else "-0.001",
        }
        for i in range(45)
    ]
    return {v: rows[:] for v in c.VARIANTS}, {
        v: {"net_total_return": Decimal(".2"), "maximum_drawdown": Decimal(".1")}
        for v in c.VARIANTS
    }


def test_paired_contrasts_no_spurious_difference_and_preserve_tail():
    series, points = series_points()
    result = c.analyze_components(c.load_request(ROOT), series, points)
    assert (
        result["complete_blocks"] == 2
        and len(result["incomplete_block_dates_excluded_only_from_resampling"]) == 1
    )
    for effect in result["contrasts"].values():
        assert set(map(Decimal, effect["point_delta"].values())) == {Decimal(0)}
        assert all(
            Decimal(x) == 0
            for interval in effect["paired_block_95_interval"].values()
            for x in interval.values()
        )
    assert result["successor_strategy_selected"] is result["confirmatory_acceptance"] is False


@pytest.mark.parametrize("error", ["dates", "return", "missing", "budget"])
def test_analysis_refuses_unpaired_or_changed_sample(error):
    series, points = series_points()
    request = c.load_request(ROOT)
    if error == "dates":
        series["TE"] = series["TE"][1:]
    elif error == "return":
        series["TE"] = [{**r, "return": "NaN"} for r in series["TE"]]
    elif error == "missing":
        del series["TC"]
    else:
        request["analysis"]["resamples"] = 1
    with pytest.raises(ValueError):
        c.analyze_components(request, series, points)


def component_cold_script():
    source = ast.parse((ROOT / "tests/unit/test_p3_retrospective_accounting_v2.py").read_text())
    fn = next(
        x
        for x in source.body
        if isinstance(x, ast.FunctionDef) and x.name.startswith("test_corrected_two_cold_processes")
    )
    script = next(
        ast.literal_eval(x.value)
        for x in fn.body
        if isinstance(x, ast.Assign)
        and isinstance(x.targets[0], ast.Name)
        and x.targets[0].id == "script"
    )
    boundary = "from quanthus.expert_strategy.p3_retrospective_accounting_v2 import"
    return (
        script[: script.index(boundary)]
        + """
from quanthus.expert_strategy.p3_risk_components import RiskComponentRuntime
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.backtest.data_provider import ReplayDataProvider
def reject(*a,**k): raise AssertionError('legacy provider opened')
ReplayDataProvider.open=reject
receipt=json.loads(os.environ['COMPONENT_RECEIPT'])
with RiskComponentRuntime(repository,root,scratch/'run',
 manifest_sha256=receipt['preparation_sha256'],
 industry=load_static_industry_mapping(industry_path,expected_version='static-level1-concentration-research-v1'),
 consumer_admission=receipt) as runtime:
 print(json.dumps(runtime.execute_all(),sort_keys=True))
"""
    )


def test_two_cold_processes_have_no_old_sources_or_network(tmp_path):
    if not ctypes.util.find_library("seccomp"):
        pytest.skip("libseccomp unavailable; isolation not checked")
    result = build(tmp_path)
    industry(tmp_path / "industry.json")
    receipt = admission(result, n._json(tmp_path / "prepared/plan.json"))
    forbidden = tmp_path / "old-source"
    forbidden.write_text("forbidden")
    old = tmp_path / "prepared"
    reports = []
    for i in (1, 2):
        moved = tmp_path / f"moved-{i}"
        shutil.copytree(old, moved)
        shutil.rmtree(old)
        scratch = tmp_path / f"cold-{i}"
        scratch.mkdir()
        done = subprocess.run(
            [
                sys.executable,
                "-c",
                component_cold_script(),
                str(moved),
                str(ROOT),
                str(scratch),
                str(tmp_path / "industry.json"),
                str(forbidden),
            ],
            cwd=scratch,
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(ROOT / "src"),
                "TMPDIR": str(scratch),
                "XDG_CACHE_HOME": str(scratch),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "COMPONENT_RECEIPT": json.dumps(receipt),
            },
        )
        assert done.returncode == 0, done.stderr
        reports.append(json.loads(done.stdout))
        old = moved
    assert reports[0] == reports[1]
