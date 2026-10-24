"""Successor changes one strategy veto, with no change to frozen experiments."""

import ctypes.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_base24_native as n
from quanthus.expert_strategy import p3_retrospective_preparation as prep
from quanthus.expert_strategy import p3_risk_components as components
from quanthus.expert_strategy import p3_risk_entry_veto as s
from tests.unit.test_formal_expert_strategy import EVENT_ID, _context, _held_facts
from tests.unit.test_p3_base24_batch import industry
from tests.unit.test_p3_retrospective_bridge import ROOT
from tests.unit.test_p3_retrospective_replay import build
from tests.unit.test_p3_risk_components import (
    checkpoint_setup,
    component_cold_script,
    series_points,
)
from tests.unit.test_p3_risk_components import runner as runner  # noqa: F401


def admission(result, plan):
    return {
        "purpose_id": prep.PURPOSE,
        "mode": "SYNTHETIC_ONLY",
        "preparation_sha256": result["manifest_sha256"],
        "runtime_version": s.VERSION,
        "accounting_version": accounting.VERSION,
        "code_config_digest": digest_value(plan["code_config_members"]),
        "experiment_id": s.VERSION,
        "experiment_request_digest": digest_value(s.load_request(ROOT)),
    }


def admitted(tmp_path, changes=None):
    result = build(tmp_path)
    receipt = admission(result, n._json(tmp_path / "prepared/plan.json"))
    receipt.update(changes or {})
    return s.RiskEntryVetoRuntime(
        ROOT,
        tmp_path / "prepared",
        tmp_path / "successor",
        manifest_sha256=result["manifest_sha256"],
        industry=industry(tmp_path / "industry.json"),
        consumer_admission=receipt,
    )


@pytest.mark.parametrize(
    "vol,rsi,blocked",
    [
        (".2", "60", False),
        (".5", "75", False),
        (".51", "60", True),
        (".2", "76", True),
        (".51", "76", True),
    ],
)
def test_threshold_cap_and_same_context_TC_anchor(runner, vol, rsi, blocked):
    kwargs = dict(
        event_id=EVENT_ID,
        context=_context(volatility=Decimal(vol), rsi=Decimal(rsi)),
        trend_state="stable",
        entry_evidence_ready=True,
    )
    tr = s.EntryVetoRunner(runner, "TR").evaluate_successor(**kwargs)
    tch = s.EntryVetoRunner(runner, "TCH").evaluate_successor(**kwargs)
    tc = components.ComponentEntryRunner(runner, "TC").evaluate_component(**kwargs)
    assert (tr.action.final_action == GovernedAction.NO_ACTION) == blocked
    assert tch.action.final_action == GovernedAction.TRIAL_ENTRY
    assert tch.target_maximum_position == Decimal(".20")
    assert replace(tch, intervention_id=None) == replace(tc, intervention_id=None)


@pytest.mark.parametrize("missing", ["vol", "rsi", "trend", "entry"])
def test_required_features_and_system_guards_retained(runner, missing):
    for variant in s.VARIANTS:
        d = s.EntryVetoRunner(runner, variant).evaluate_successor(
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


def test_full_held_and_corrected_accounting_reused(runner):
    runtime = object.__new__(s.RiskEntryVetoRuntime)
    for variant in s.VARIANTS:
        facts = _held_facts()
        d = runner.evaluate_held(
            event_id=EVENT_ID,
            context=_context(position_state="held", position_evidence=facts.evidence),
            position_facts=facts,
            intervention_id=runtime._held_intervention_id(s.VERSION + ":" + variant),
        )
        assert d.action.final_action == GovernedAction.REDUCE
        assert d.target_maximum_position == Decimal(".10")
    assert (
        s.RiskEntryVetoRuntime._review_positions
        is accounting.AccountingCorrectedReplayRuntime._review_positions
    )
    assert (
        s.RiskEntryVetoRuntime._marked_return
        is accounting.AccountingCorrectedReplayRuntime._marked_return
    )
    assert components.VARIANTS.keys() == {"T", "TC", "TE", "TH", "TR"}
    with pytest.raises(ValueError, match="COMPONENT_VARIANT"):
        components.RiskComponentRuntime.token_for("TCH")


def test_actual_synthetic_two_cells_and_complete_last_no_overwrite(tmp_path):
    with admitted(tmp_path) as runtime:
        report = runtime.execute_all()
        assert tuple(report["variants"]) == ("TR", "TCH")
        assert report["session_index_builds"] == 1
        assert report["real_comparisons_executed"] == 0
        original = (tmp_path / "successor/COMPLETE.json").read_bytes()
        with pytest.raises(FileExistsError):
            runtime.execute_all()
        assert original == (tmp_path / "successor/COMPLETE.json").read_bytes()
    for variant in s.VARIANTS:
        root = tmp_path / "successor" / ("variant." + variant)
        assert n._json(root / "reconciliation.json")["reconciled"]
        m = n._json(root / "manifest.json")
        assert m["runtime_version"] == s.VERSION and m["components"] == s.VARIANTS[variant]
        assert m["successor_strategy_selected"] is m["production_authorized"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("runtime_version", components.VERSION),
        ("accounting_version", "old"),
        ("code_config_digest", "bad"),
        ("experiment_request_digest", "bad"),
        ("experiment_id", "bad"),
        ("result_role", "primary"),
    ],
)
def test_wrong_admission_refused_before_output(tmp_path, key, value):
    with pytest.raises(ValueError, match="ADMISSION_BINDING"):
        admitted(tmp_path, {key: value})
    assert not (tmp_path / "successor").exists()


def test_unregistered_real_denied(tmp_path):
    result = build(tmp_path)
    path = tmp_path / "prepared/plan.json"
    plan = n._json(path)
    plan["synthetic"] = False
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="REAL_SOURCE_ADMISSION"):
        s.RiskEntryVetoRuntime(
            ROOT,
            tmp_path / "prepared",
            tmp_path / "denied",
            manifest_sha256=result["manifest_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission=admission(result, plan),
        )
    assert not (tmp_path / "denied").exists()


def test_bad_TR_anchor_prevents_first_TCH_result(tmp_path, monkeypatch):
    runtime = object.__new__(s.RiskEntryVetoRuntime)
    runtime.workspace = tmp_path
    runtime.synthetic = False
    monkeypatch.setattr(runtime, "_guard", lambda: None)
    monkeypatch.setattr(runtime, "_adapter_binding", lambda: {})
    monkeypatch.setattr(
        runtime, "run_research_variant", lambda v, p: (SimpleNamespace(replay_digest="x"), {})
    )

    def mismatch(*args):
        raise ValueError("TR_ANCHOR_MISMATCH")

    monkeypatch.setattr(runtime, "_verify_anchor", mismatch)
    with pytest.raises(ValueError, match="TR_ANCHOR"):
        runtime.execute_all()
    assert n._json(tmp_path / "FAILED.json")["failed_variant"] == "TR"
    assert not (tmp_path / "variant.TCH").exists() and not (tmp_path / "COMPLETE.json").exists()


def test_small_disposable_corruption_denied(tmp_path):
    result = build(tmp_path)
    plan = n._json(tmp_path / "prepared/plan.json")
    receipt = admission(result, plan)
    with (tmp_path / "prepared/features.parquet").open("ab") as f:
        f.write(b"corrupt")
    with pytest.raises(ValueError):
        s.RiskEntryVetoRuntime(
            ROOT,
            tmp_path / "prepared",
            tmp_path / "denied",
            manifest_sha256=result["manifest_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission=receipt,
        )
    assert not (tmp_path / "denied").exists()


def test_retained_own_role_index_uses_exact_mapping_primitive(tmp_path):
    resume, old, clocks = checkpoint_setup(tmp_path)
    plan = n._json(tmp_path / "prepared/plan.json")
    result = n._json(tmp_path / "prepared/PREPARATION_COMPLETE.json")
    mapping = old["session_index_checkpoint"]
    mapping["prior_runtime_seconds"] = 0
    accepted = n._json(ROOT / "config/research/p3-strat-003/risk-successor-proposal.v1.json")[
        "frozen_bindings"
    ]["step2_acceptance_sha256"]
    proof = {
        "kind": "entry-veto-role-specific-prerequisite-reuse-v1",
        "role": "synthetic",
        "source_acceptance_sha256": accepted,
        "producer_semantics_unchanged": True,
        "cold_uses_primary_prerequisites": False,
        "input_identity": mapping["input_identity"],
        "index_sha256": mapping["index_sha256"],
    }
    (resume / "retained-index-proof.json").write_text(json.dumps(proof))
    mapping["producer_proof_sha256"] = n._sha(resume / "retained-index-proof.json")
    receipt = {
        **admission(result, plan),
        "retained_session_index": {
            "schema_version": "quanthus.p3-entry-veto-retained-index.v1",
            "source_acceptance_sha256": accepted,
            "mapping_descriptor": mapping,
        },
    }
    with s.RiskEntryVetoRuntime(
        ROOT,
        tmp_path / "prepared",
        resume / "run",
        manifest_sha256=result["manifest_sha256"],
        industry=industry(tmp_path / "industry.json"),
        consumer_admission=receipt,
    ) as runtime:
        assert runtime.clocks == clocks
        assert runtime.execute_all()["retained_own_role_index_reuses"] == 1
    proof["role"] = "primary"
    (resume / "retained-index-proof.json").write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="RETAINED_PROOF_HASH"):
        s.RiskEntryVetoRuntime(
            ROOT,
            tmp_path / "prepared",
            resume / "bad",
            manifest_sha256=result["manifest_sha256"],
            industry=industry(tmp_path / "industry.json"),
            consumer_admission=receipt,
        )
    assert not (resume / "bad/COMPLETE.json").exists()


def test_two_cold_new_processes_block_native_sources_and_network(tmp_path):
    if not ctypes.util.find_library("seccomp"):
        pytest.skip("libseccomp absent")
    result = build(tmp_path)
    industry(tmp_path / "industry.json")
    receipt = admission(result, n._json(tmp_path / "prepared/plan.json"))
    forbidden = tmp_path / "old-source"
    forbidden.write_text("deny")
    old = tmp_path / "prepared"
    reports = []
    script = component_cold_script().replace(
        "from quanthus.expert_strategy.p3_risk_components import RiskComponentRuntime",
        (
            "from quanthus.expert_strategy.p3_risk_entry_veto import "
            "RiskEntryVetoRuntime as RiskComponentRuntime"
        ),
    )
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


def test_fixed_paired_analysis_keeps_tail_and_closes_zero_gain():
    series, points = series_points()
    series = {v: series["T"] for v in s.VARIANTS}
    points = {v: points["T"] for v in s.VARIANTS}
    out = s.analyze_successor(s.load_request(ROOT), series, points)
    assert (
        out["complete_blocks"] == 2
        and len(out["incomplete_block_dates_excluded_only_from_resampling"]) == 1
    )
    assert not out["resource_screen_passed"] and out["decision"].startswith("CLOSE")
    assert all(
        Decimal(v) == 0 for b in out["paired_block_95_interval"].values() for v in b.values()
    )
    points["TCH"] = {**points["TCH"], "net_total_return": Decimal(".21")}
    out = s.analyze_successor(s.load_request(ROOT), series, points)
    assert out["resource_screen_passed"] and not out["fresh_pilot_authorized"]


@pytest.mark.parametrize("error", ["dates", "missing", "returns", "budget"])
def test_analysis_binding_refuses_changed_inputs(error):
    series, points = series_points()
    series = {v: series["T"][:] for v in s.VARIANTS}
    points = {v: points["T"] for v in s.VARIANTS}
    request = s.load_request(ROOT)
    if error == "dates":
        series["TCH"] = series["TCH"][1:]
    elif error == "missing":
        del series["TR"]
    elif error == "returns":
        series["TCH"][0] = {**series["TCH"][0], "return": "NaN"}
    else:
        request["analysis"]["resamples"] = 1
    with pytest.raises(ValueError):
        s.analyze_successor(request, series, points)


@pytest.mark.parametrize("corrupt", [None, "engine-events.json", "reconciliation.json"])
def test_full_TR_gate_covers_every_business_artifact_before_TCH(tmp_path, corrupt):
    from quanthus.expert_evaluation.serialization import canonical_json_bytes

    with admitted(tmp_path) as runtime:
        runtime.execute_all()
        target = tmp_path / "successor/variant.TR"
        names = {
            "decisions.json",
            "engine-events.json",
            "equity.json",
            "daily-returns.json",
            "final-ledgers.json",
            "reconciliation.json",
        }
        artifacts = {name: json.loads((target / name).read_bytes()) for name in names}
        coords = {
            "entry:" + e.record.event_id: e.record.identity.security_id
            + ":"
            + e.record.identity.signal_date.isoformat()
            for e in runtime.events
        }
        digests = s.business_bundle(
            artifacts,
            coords,
            snapshot=runtime.snapshot_id,
            anchor_snapshot="old",
            snapshot_suffixes=[],
        )
        anchor = {"source_result_role": "synthetic", "snapshot_suffixes": [], "digests": digests}
        anchor_path = tmp_path / "full-TR-anchor.json"
        anchor_path.write_text(json.dumps(anchor))
        runtime.full_tr_anchor_sha = n._sha(anchor_path)
        runtime.component_request = {
            **runtime.component_request,
            "anchor_snapshot_id": "old",
            "anchor_artifact_sha256": {
                "TR": {
                    name: components.ledger_anchor_digest(
                        canonical_json_bytes(artifacts[name]),
                        current_snapshot=runtime.snapshot_id,
                        old_snapshot="old",
                    )
                    if name == "final-ledgers.json"
                    else n._sha(target / name)
                    for name in ("equity.json", "daily-returns.json", "final-ledgers.json")
                }
            },
            "anchor_decision_sha256": {
                "TR": components.decision_anchor_digest(artifacts["decisions.json"], coords)
            },
        }
        if corrupt == "engine-events.json":
            artifacts[corrupt].append(
                {
                    "event_id": "changed",
                    "event_time": "2025-01-02T15:00:00+08:00",
                    "sequence": 1,
                    "event_type": "daily_snapshot",
                    "reason_code": "changed_cash_flow_reason",
                    "payload": {"cash": "1.00"},
                }
            )
        elif corrupt == "reconciliation.json":
            artifacts[corrupt]["reconciled"] = False
        if corrupt:
            (target / corrupt).write_bytes(canonical_json_bytes(artifacts[corrupt]))
            with pytest.raises(ValueError, match="FULL_TR_BUSINESS_MISMATCH"):
                runtime._verify_anchor(target, "TR")
        else:
            runtime._verify_anchor(target, "TR")


def test_full_anchor_maps_only_exact_ids_preserving_money_and_edges():
    import copy

    from tests.unit.test_p3_risk_components import row

    source = {
        "decisions.json": [{**row(), "formal_decision_digest": "decision-old"}],
        "engine-events.json": [
            {
                "event_id": "e1",
                "event_time": "2025-01-02",
                "sequence": 1,
                "event_type": "signal_accepted",
                "reason_code": "ok",
                "payload": {
                    "signal_id": "signal-old",
                    "decision_version": "card|decision-old",
                    "snapshot_id": "snap-old",
                    "quantity": 100,
                },
            },
            {
                "event_id": "e2",
                "event_time": "2025-01-02",
                "sequence": 2,
                "event_type": "fill",
                "reason_code": "ok",
                "payload": {
                    "signal_id": "signal-old",
                    "fill_id": "fill-old",
                    "gross_amount": "123.4500",
                    "total_fees": ".1200",
                },
            },
            {
                "event_id": "e3",
                "event_time": "2025-01-02",
                "sequence": 3,
                "event_type": "ledger_entry",
                "reason_code": "ok",
                "payload": {
                    "entry_id": "entry-old",
                    "reference_id": "fill-old",
                    "reference_type": "fill",
                    "postings": ["123.4500"],
                },
            },
        ],
        "equity.json": [],
        "daily-returns.json": [],
        "final-ledgers.json": [],
        "reconciliation.json": {"reconciled": True},
    }
    coords = {"entry:one": "security:2025-01-02"}
    expected = s.business_bundle(
        source, coords, snapshot="snap-old", anchor_snapshot="anchor", snapshot_suffixes=[]
    )
    new = json.loads(json.dumps(source).replace("-old", "-new"))
    assert (
        s.business_bundle(
            new, coords, snapshot="snap-new", anchor_snapshot="anchor", snapshot_suffixes=[]
        )
        == expected
    )
    changed = copy.deepcopy(new)
    changed["engine-events.json"][1]["payload"]["total_fees"] = ".1201"
    assert (
        s.business_bundle(
            changed, coords, snapshot="snap-new", anchor_snapshot="anchor", snapshot_suffixes=[]
        )
        != expected
    )
    changed = copy.deepcopy(new)
    changed["engine-events.json"][2]["payload"]["reference_id"] = "unknown"
    with pytest.raises(ValueError, match="ANCHOR_REFERENCE"):
        s.business_bundle(
            changed, coords, snapshot="snap-new", anchor_snapshot="anchor", snapshot_suffixes=[]
        )


def test_generated_ledger_snapshot_and_equal_time_uuid_order_are_identity_only():
    def bundle(suffix):
        return {
            "decisions.json": [],
            "engine-events.json": [
                {
                    "event_id": "evt-" + suffix + "-a",
                    "event_time": "2025-01-02T15:00:00",
                    "event_type": "daily_snapshot",
                    "reason_code": "ok",
                    "sequence": 1,
                    "payload": {
                        "snapshot_id": "lsnap_" + suffix + "-a",
                        "unit_id": "a",
                        "session_date": "2025-01-02",
                        "cash": "100.00",
                        "mark_snapshot_id": "source-" + suffix,
                    },
                },
                {
                    "event_id": "evt-" + suffix + "-b",
                    "event_time": "2025-01-02T15:00:00",
                    "event_type": "daily_snapshot",
                    "reason_code": "ok",
                    "sequence": 1,
                    "payload": {
                        "snapshot_id": "lsnap_" + suffix + "-b",
                        "unit_id": "b",
                        "session_date": "2025-01-02",
                        "cash": "200.00",
                        "mark_snapshot_id": "source-" + suffix,
                    },
                },
            ],
            "equity.json": [],
            "daily-returns.json": [],
            "final-ledgers.json": [],
            "reconciliation.json": {"reconciled": True},
        }

    a, b = bundle("old"), bundle("new")
    b["engine-events.json"].reverse()
    expected = s.business_bundle(
        a, {}, snapshot="source-old", anchor_snapshot="anchor", snapshot_suffixes=[]
    )
    assert (
        s.business_bundle(
            b, {}, snapshot="source-new", anchor_snapshot="anchor", snapshot_suffixes=[]
        )
        == expected
    )
    b["engine-events.json"][0]["payload"]["cash"] = "200.01"
    assert (
        s.business_bundle(
            b, {}, snapshot="source-new", anchor_snapshot="anchor", snapshot_suffixes=[]
        )
        != expected
    )
