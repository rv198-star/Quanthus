"""The historical experiment keeps frozen policies and truthful late clocks."""

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.backtest.contracts import OrderSide
from quanthus.backtest.engine import BacktestInputError
from quanthus.contracts import SnapshotRef
from quanthus.expert_strategy import formal_p2_accounting_v2 as accounting
from quanthus.expert_strategy import p3_historical_robustness as h
from quanthus.expert_strategy import p3_risk_components as components
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyRunner
from tests.unit.test_formal_expert_strategy import EVENT_ID, _context, _held_facts
from tests.unit.test_p3_retrospective_bridge import ROOT


def test_day_panel_initializes_utc_in_sanitized_offline_process(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "minimal.parquet"
    pq.write_table(
        pa.table({"market_date": [date(2012, 1, 4)], "security_id": ["synthetic"]}), path
    )
    script = """
import sys
from pathlib import Path
from types import SimpleNamespace
from quanthus.expert_strategy.p3_historical_robustness import DayPanel
with DayPanel(Path(sys.argv[1]), SimpleNamespace()) as panel:
    assert panel._connection.execute("SELECT current_setting('TimeZone')").fetchone() == ('UTC',)
    assert panel._connection.execute('SELECT count(*) FROM feature_panel').fetchone() == (1,)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONNOUSERSITE": "1",
            "TZ": "UTC",
            "TMPDIR": str(tmp_path),
        },
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("unexpected_source", [False, True])
def test_runtime_binds_action_original_source_separately_from_portable_envelope(
    tmp_path, monkeypatch, unexpected_source
):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "features.parquet").write_bytes(b"synthetic-admission-only")
    (prepared / "preparation.json").write_text(
        json.dumps(
            {
                "request_digest": h.POPULATION_REQUEST_DIGEST,
                "snapshot_id": h.VERSION
                + "/"
                + h.POPULATION_REQUEST_DIGEST.removeprefix("sha256:"),
            }
        )
    )
    bundle = SimpleNamespace(
        source_snapshot_id="original-action-source",
        historical_bundle=SimpleNamespace(
            snapshot=SimpleNamespace(snapshot_id="portable-envelope")
        ),
    )
    monkeypatch.setattr(h, "open_source", lambda _: bundle)
    monkeypatch.setattr(
        h,
        "BoundedHistoricalReader",
        lambda _: SimpleNamespace(
            close=lambda: None,
            _rows=lambda _: [
                {
                    "source_snapshot_id": "unapproved"
                    if unexpected_source
                    else h.ACTION_SOURCE_SNAPSHOT
                }
            ],
        ),
    )

    def initialize(self, *args, **kwargs):
        self.stack = ExitStack()
        self.engine = SimpleNamespace(policy=None)

    monkeypatch.setattr(h.FormalP2ReplayRuntime, "__init__", initialize)
    request = h.load_request(ROOT)
    admission = {
        "request_digest": h.digest_value(request),
        "role": "primary",
        "source_manifest_sha256": h.SOURCE_SHA,
        "preparation_sha256": h.native._sha(prepared / "preparation.json"),
        "feature_sha256": h.native._sha(prepared / "features.parquet"),
    }
    if unexpected_source:
        with pytest.raises(ValueError, match="HISTORY_ACTION_SOURCE_CLOSURE"):
            h.HistoricalRuntime(ROOT, tmp_path, prepared, admission)
        return
    with h.HistoricalRuntime(ROOT, tmp_path, prepared, admission) as runtime:
        assert runtime.engine.action_snapshot == h.ACTION_SOURCE_SNAPSHOT
        run = SimpleNamespace(
            official=False,
            code_version=h.VERSION + ":synthetic",
            data_snapshot_id=h.ACTION_SOURCE_SNAPSHOT,
        )
        action = SimpleNamespace(known_at=runtime.info, snapshot_id=h.ACTION_SOURCE_SNAPSHOT)
        applied = datetime.fromisoformat("2012-04-10T09:00:00+08:00")
        runtime.engine._validate_action_clock(run, action, applied)
        for obj, field, wrong in (
            (action, "snapshot_id", "portable-envelope"),
            (action, "known_at", datetime.fromisoformat("2026-07-30T00:00:00+00:00")),
            (run, "data_snapshot_id", "unknown-source"),
            (run, "official", True),
            (run, "code_version", "unrelated-purpose"),
        ):
            before = getattr(obj, field)
            setattr(obj, field, wrong)
            with pytest.raises(BacktestInputError, match="HISTORY_ACTION_BINDING"):
                runtime.engine._validate_action_clock(run, action, applied)
            setattr(obj, field, before)


def test_current_request_has_exact_ten_years_and_no_new_authority():
    request = h.load_request(ROOT)
    assert request["scope"] == ["2012-01-04", "2021-12-30", "2021-12-31"]
    assert request["time_blocks"] == [[y, y + 1] for y in range(2012, 2022, 2)]
    assert not request["historical_PIT_claim"]
    assert not request["independent_holdout_claim"]
    assert not request["production_authorized"]
    assert request["code_config_members"]
    assert request["input_preparation_request_digest"] == h.POPULATION_REQUEST_DIGEST
    position = ExpertStrategyRegistry(ROOT).card.semantic_sections["position_and_execution"]
    assert position["unit_count"] == 5
    assert Decimal(position["unit_notional"]) == Decimal("100000.00")


def synthetic_analysis_inputs():
    dates = [
        f"{year}-{month_day}"
        for year in range(2012, 2022)
        for month_day in ("01-05", "02-06", "03-06", "04-08", "05-07", "12-31")
    ]
    artifacts = {}
    for number, arm in enumerate(h.ARMS, 1):
        equity = [{"market_date": "2012-01-04", "system_equity": "500000", "market_value": "0"}]
        daily = []
        amount = Decimal(500000)
        for day in dates:
            following = (amount * (1 + Decimal(number) / 1000)).quantize(Decimal(".01"))
            daily.append(
                {
                    "market_date": day,
                    "return": str(((following / amount) - 1).quantize(Decimal(".00000001"))),
                }
            )
            equity.append(
                {"market_date": day, "system_equity": str(following), "market_value": "0"}
            )
            amount = following
        artifacts[arm] = {
            "equity.json": equity,
            "daily-returns.json": daily,
            "engine-events.json": [],
        }
    return artifacts


def test_fixed_historical_analysis_uses_all_dates_and_continuing_capital():
    request = h.load_request(ROOT)
    data = synthetic_analysis_inputs()
    result = h.analyze_history(request, data)
    assert result["variants"]["B"]["sessions"] == 60
    assert len(result["variants"]["B"]["annual"]) == 10
    assert len(result["variants"]["B"]["two_year"]) == 5
    assert result["complete_22_session_blocks"] == 2
    assert len(result["incomplete_dates_excluded_only_from_resampling"]) == 16
    assert set(result["contrasts"]) == {"T-B", "TCH-B", "TCH-T"}
    for arm in h.ARMS:
        exact = Decimal(data[arm]["equity.json"][-1]["system_equity"]) / 500000 - 1
        assert Decimal(result["variants"][arm]["net_total_return"]) == exact
    assert not result["strategy_accepted"]
    assert result == h.analyze_history(request, data)


@pytest.mark.parametrize("fault", ["arm", "rounding", "dates", "capital"])
def test_historical_analysis_rejects_changed_pairing_and_accounting(fault):
    data = synthetic_analysis_inputs()
    if fault == "arm":
        data.pop("B")
    elif fault == "rounding":
        data["T"]["daily-returns.json"][0]["return"] = ".9"
    elif fault == "dates":
        data["T"]["daily-returns.json"][0]["market_date"] = "2025-01-01"
    else:
        data["T"]["equity.json"][0]["system_equity"] = "400000"
    with pytest.raises(ValueError, match="HISTORY_ANALYSIS_"):
        h.analyze_history(h.load_request(ROOT), data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", ["2022-01-01", "2024-12-30", "2024-12-31"]),
        ("source_manifest_sha256", "sha256:" + "0" * 64),
        ("action_source_snapshot_id", "portable-envelope"),
        ("input_preparation_request_digest", "sha256:" + "0" * 64),
        ("historical_PIT_claim", True),
        ("original_D_activated", True),
        ("independent_holdout_claim", True),
        ("production_authorized", True),
        ("parameter_search", True),
        ("P6_mutation_authorized", True),
    ],
)
def test_request_rejects_scope_source_and_authority_drift(tmp_path, field, value):
    request = json.loads((ROOT / h.REQUEST).read_text())
    request[field] = value
    path = tmp_path / h.REQUEST
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError, match="HISTORY_REQUEST_BINDING"):
        h.load_request(tmp_path)


@pytest.mark.parametrize("vol,rsi", [(".2", "60"), (".51", "76")])
def test_B_T_TCH_keep_reviewed_same_context_decisions_and_held_rules(vol, rsi):
    registry = ExpertStrategyRegistry(ROOT)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    runtime = object.__new__(h.HistoricalRuntime)
    runtime.entry_runners = {}
    kwargs = dict(
        event_id=EVENT_ID,
        context=_context(volatility=Decimal(vol), rsi=Decimal(rsi)),
        trend_state="stable",
        entry_evidence_ready=True,
    )
    refs = {
        "B": runner.evaluate_entry_control(
            event_id=EVENT_ID,
            context=kwargs["context"],
            entry_evidence_ready=True,
            position_risk_enabled=False,
        ),
        "T": runner.evaluate_entry(
            **kwargs, intervention_id="intervention.position-risk-base-policy"
        ),
        "TCH": components.ComponentEntryRunner(runner, "TC").evaluate_component(**kwargs),
    }
    for arm in h.ARMS:
        got = runtime._evaluate_entry_decision(runner, intervention_id=arm, **kwargs)
        assert got == refs[arm]
        assert got.action.final_action == GovernedAction.TRIAL_ENTRY
        facts = _held_facts()
        held = runner.evaluate_held(
            event_id=EVENT_ID,
            context=_context(position_state="held", position_evidence=facts.evidence),
            position_facts=facts,
            intervention_id=runtime._held_intervention_id(arm),
        )
        if arm == "TCH":
            assert held.target_maximum_position == Decimal(".10")
        else:
            assert held.target_maximum_position != Decimal(".10")
    assert (
        h.HistoricalRuntime._marked_return
        is accounting.AccountingCorrectedReplayRuntime._marked_return
    )
    assert (
        h.HistoricalRuntime._record_entry
        is accounting.AccountingCorrectedReplayRuntime._record_entry
    )


@pytest.mark.parametrize("missing", ["rsi", "vol", "entry"])
def test_all_arms_keep_missing_data_block_new(missing):
    registry = ExpertStrategyRegistry(ROOT)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    runtime = object.__new__(h.HistoricalRuntime)
    runtime.entry_runners = {}
    for arm in h.ARMS:
        decision = runtime._evaluate_entry_decision(
            runner,
            intervention_id=arm,
            event_id=EVENT_ID,
            context=_context(
                rsi=None if missing == "rsi" else Decimal(60),
                volatility=None if missing == "vol" else Decimal(".2"),
            ),
            trend_state="stable",
            entry_evidence_ready=missing != "entry",
        )
        assert decision.action.final_action == GovernedAction.NO_ACTION
        assert decision.governance.required_action.value == "block_new"


def signal():
    business = datetime.fromisoformat("2012-01-04T15:00:00+08:00")
    info = datetime.fromisoformat("2026-07-29T03:19:57+00:00")
    return h.HistoricalSignal(
        "historical-signal",
        SnapshotRef(date(2012, 1, 4), info, "test", "test"),
        business,
        "close-to-next-open-v1",
        None,
        "600001",
        OrderSide.BUY,
        "u",
        100,
        Decimal(60),
        "test",
        "test",
    )


def test_true_source_time_is_separate_from_business_time_and_engine_is_purpose_bound():
    value = signal()
    assert value.snapshot.knowledge_cutoff.year == 2026 and value.decision_at.year == 2012
    engine = h.HistoricalEngine(None, value.snapshot.knowledge_cutoff, "test", "old-actions")
    run = SimpleNamespace(official=False, code_version=h.VERSION + ":test")
    engine._validate_signal_clock(run, value)
    with pytest.raises(BacktestInputError, match="HISTORY_SIGNAL_BINDING"):
        engine._validate_signal_clock(
            run, replace(value, snapshot=replace(value.snapshot, snapshot_id="wrong"))
        )
    run.official = True
    with pytest.raises(BacktestInputError, match="HISTORY_SIGNAL_BINDING"):
        engine._validate_signal_clock(run, value)
    with pytest.raises(ValueError, match="HISTORY_SIGNAL_CONTRACT"):
        replace(value, purpose_id="p3-base24-retrospective-comparison-v1")
    with pytest.raises(ValueError, match="HISTORY_SIGNAL_CONTRACT"):
        replace(value, decision_at=value.snapshot.knowledge_cutoff)


def test_exclusive_preparation_and_bad_source_reject_before_read_or_overwrite(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    marker = old / "marker"
    marker.write_bytes(b"unchanged")
    with pytest.raises(ValueError, match="HISTORY_PREPARATION_EXISTS"):
        h.prepare(ROOT, None, old, {})
    assert list(old.iterdir()) == [marker] and marker.read_bytes() == b"unchanged"
    with pytest.raises(ValueError, match="HISTORY_SOURCE_BINDING"):
        (tmp_path / "manifest.json").write_text("{}")
        h.open_source(tmp_path)


def test_three_actual_synthetic_replays_keep_late_clock_and_reconcile(tmp_path):
    from dataclasses import asdict

    from quanthus.backtest.baseline_research import BaselineFeaturePanel
    from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
    from quanthus.event_library.contracts import EventIdentity
    from quanthus.expert_evaluation.serialization import digest_value
    from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
    from tests.unit.test_baseline_research import BaselineResearchTests
    from tests.unit.test_p3_base24_batch import industry

    fixture = BaselineResearchTests()
    fixture.setUp()
    try:
        artifact = BaselineFeaturePanel.materialize(
            fixture.bundle,
            fixture.registry,
            scope_start=fixture.dates[0],
            scope_end=fixture.dates[-1],
            output=tmp_path / "features.parquet",
        )
        original = industry(tmp_path / "industry.json")
        mapping = replace(
            original,
            memberships=tuple(
                replace(original.memberships[0], security_code=sid)
                for sid in ("SSE-600001", "SZSE-000002")
            ),
        )

        class SyntheticRuntime(h.HistoricalRuntime):
            def __init__(self):
                self.info = datetime.fromisoformat("2026-07-29T03:19:57+00:00")
                self.snapshot_id = "explicit-synthetic-historical-test"
                self.request_digest = digest_value({"synthetic": True})
                self.prerequisite_digest = self.request_digest
                self.prepared = tmp_path
                self.preparation = {
                    "feature_report": {
                        **asdict(artifact.report),
                        "scope_start": artifact.report.scope_start.isoformat(),
                        "scope_end": artifact.report.scope_end.isoformat(),
                    }
                }
                self.reader = HistoricalReplayFrameReader(fixture.bundle)
                self.entry_runners = {}
                self.bundle = SimpleNamespace(historical_bundle=fixture.bundle)
                FormalP2ReplayRuntime.__init__(
                    self,
                    ROOT,
                    decision_start=fixture.dates[120],
                    decision_end=fixture.dates[-2],
                    execution_end=fixture.dates[-1],
                    parent_event_run_digest=None,
                    provider_config="explicit-synthetic-only",
                )
                self.stack.callback(self.reader.close)
                self.engine = h.HistoricalEngine(
                    self.engine.policy,
                    self.info,
                    self.snapshot_id,
                    fixture.bundle.snapshot.snapshot_id,
                )

            def _load_event_population(self, event_root):
                d = fixture.dates[121]
                event = h.HistoricalEvent(
                    EventIdentity("SSE-600001", d, "synthetic", "1", self.snapshot_id),
                    self.info,
                    datetime.combine(d, datetime.min.time(), h.MARKET_TIMEZONE).replace(hour=15),
                    *[digest_value({"synthetic": True})] * 4,
                    self.info,
                )
                return (
                    SimpleNamespace(
                        record=event,
                        static_industry_id=mapping.memberships[0].level1_industry_id,
                        panel_security_id="SSE-600001",
                    ),
                ), mapping

        with SyntheticRuntime() as runtime:
            for arm in h.ARMS:
                folder = tmp_path / ("variant." + arm)
                _, artifacts = runtime.run_variant(arm, folder)
                assert json.loads(artifacts["reconciliation.json"])["reconciled"]
                assert json.loads(artifacts["reconciliation.json"])["fill_count"] > 0
                folder.mkdir()
                for name, payload in artifacts.items():
                    (folder / name).write_bytes(payload)
            assert all(f.snapshot.knowledge_cutoff.year == 2026 for f in runtime.frames)
            assert all(f.decision_at.year == 2012 for f in runtime.frames)
    finally:
        fixture.doCleanups()
