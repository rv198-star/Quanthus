"""Counterexamples for original selection, common clocks and the R6 resource decision."""

import copy
import importlib
import json
from pathlib import Path

import pytest

from quanthus.hf_clock_response import clock_markout
from quanthus.hf_dwd_research import GuardError
from quanthus.rank1_response import compare, formal, summarize, validate_nominals

duckdb = pytest.importorskip("duckdb")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def analysis():
    a = json.loads((ROOT / "config/research/research-r6/request.v1.json").read_text())["analysis"]
    a["min_paired_days_per_year"] = 1
    a["bootstrap"]["replicates"] = 20
    return a


def sample(analysis, candidate=0.002, control=0.0):
    ledger, cells = [], []
    for year in (2020, 2021):
        day = f"{year}-03-02"
        for arm, value, venue in (("candidate", candidate, "A"), ("control", control, "B")):
            ledger.append(
                {
                    "date": day,
                    "arm": arm,
                    "listing_id": arm,
                    "venue_id": venue,
                    "decision_hold": arm == "candidate",
                    "label_reason": "pass",
                    "ranking_vol12": "0.100000000000",
                    "first_date": day,
                    "second_date": day,
                    "response": value,
                    "actual_r4_closed_entry": False,
                }
            )
        cells.extend(
            [
                {
                    "trade_date": day,
                    "venue_id": "A",
                    "candidate_mean": 0.001,
                    "control_mean": 0.0,
                    "increment": 0.001,
                },
                {
                    "trade_date": day,
                    "venue_id": "B",
                    "candidate_mean": 0.002,
                    "control_mean": 0.0005,
                    "increment": 0.0015,
                },
            ]
        )
    pop = {
        "paired_cells": cells,
        "analysis": {"required_venues": ["A", "B"]},
        "mean_increment": 0.001,
    }
    return ledger, pop


def test_original_hold_is_retained_and_weight_bridge_reconciles(analysis):
    ledger, pop = sample(analysis)
    m = summarize(ledger, pop, analysis)
    assert m["branch"] == "advance"
    assert m["groups"]["candidate"]["decision_hold"] == 2
    assert m["paired_days"] == 2
    assert m["bridge"]["date_component"] == pytest.approx(0.00025)
    assert m["bridge"]["means"]["venue_component"] == pytest.approx(-0.00075)
    assert m["bridge"]["means"]["within_venue_selection_residual"] == pytest.approx(0.0015)
    assert abs(m["bridge"]["additive_residual"]) < 1e-12


def test_missing_label_never_refills_or_erases_denominators(analysis):
    ledger, pop = sample(analysis)
    ledger[0].update(label_reason="cannot_buy", response=None)
    m = summarize(ledger, pop, analysis)
    assert m["nominal_rows"] == 4 and m["paired_days"] == 1
    assert m["valid_unpaired_rows"] == 1
    assert m["sample_gaps"] == ["paired_days.2020"]
    assert m["branch"] == "insufficient"
    assert m["confidence_interval"] == [None, None]
    assert sum(x["count"] for x in m["label_ledger"]) == 4
    assert m["r4_capital_use"] == "park"


def test_bridge_missing_cell_keeps_primary_and_marks_its_smaller_sample(analysis):
    ledger, pop = sample(analysis)
    pop["paired_cells"] = pop["paired_cells"][2:]
    m = summarize(ledger, pop, analysis)
    assert m["paired_days"] == 2
    assert m["bridge"]["missing_dates"] == ["2020-03-02"]
    assert len(m["bridge"]["daily"]) == 1


def test_tie_break_and_original_keys_are_immutable(analysis):
    ledger, _ = sample(analysis)
    old = {"nominal_choices": ledger}
    validate_nominals(old, copy.deepcopy(ledger))
    bad = copy.deepcopy(ledger)
    bad[0]["listing_id"] = "rank2"
    with pytest.raises(GuardError, match="nominal selection"):
        validate_nominals(old, bad)
    with pytest.raises(GuardError, match="duplicate nominal"):
        summarize(ledger + ledger[:1], sample(analysis)[1], analysis)


def test_annual_gate_and_below_threshold_are_distinct(analysis):
    ledger, pop = sample(analysis, candidate=-0.001)
    m = summarize(ledger, pop, analysis)
    assert m["branch"] == "below_minimum" and m["verdict"] == "not_met"
    ledger[0]["response"] = 0.01
    ledger[2]["response"] = -0.002
    assert summarize(ledger, pop, analysis)["branch"] == "uncertain"


def test_exact_calendar_and_right_censor_precede_observed_next_bar(analysis):
    db = duckdb.connect()
    db.execute("""
        CREATE TABLE calendar(calendar_session_id VARCHAR,venue_id VARCHAR,
          next_session VARCHAR,trade_date DATE);
        CREATE TABLE panel(listing_id VARCHAR,calendar_session_id VARCHAR,venue_id VARCHAR,
          security_ok BOOLEAN,op DOUBLE,trade_date DATE);
        CREATE TABLE labelled(trade_date DATE,venue_id VARCHAR,listing_id VARCHAR,
          next_session VARCHAR,
          universe BOOLEAN,base_selected BOOLEAN,selected BOOLEAN,next_open_reason VARCHAR,
          next_open DOUBLE,next_close DOUBLE,execution_date DATE,return_vol20 DOUBLE);
        INSERT INTO calendar VALUES ('s1','A','s2','2021-12-28'),
          ('s2','A','s3','2021-12-29'),('s3','A',NULL,'2021-12-31');
        INSERT INTO labelled VALUES
          ('2021-12-27','A','low','s1',true,true,true,'cannot_buy',10,11,'2021-12-28',0.1),
          ('2021-12-27','A','other','s1',true,true,true,'pass',10,11,'2021-12-28',0.2),
          ('2021-12-27','A','control','s1',true,true,false,'pass',10,10,'2021-12-28',0.1),
          ('2021-12-30','A','low','s3',true,true,true,'pass',10,11,'2021-12-31',0.1),
          ('2021-12-30','A','control','s3',true,true,false,'pass',10,10,'2021-12-31',0.1);
        INSERT INTO panel VALUES ('low','s3','A',true,100,'2021-12-30'),
          ('other','s2','A',true,11,'2021-12-29'),('control','s2','A',true,10,'2021-12-29');
    """)
    q = json.loads((ROOT / "config/research/research-r5/request.v1.json").read_text())
    population = clock_markout(db, q["analysis"])
    old = {
        "nominal_choices": [
            {
                "date": d,
                "arm": a,
                "listing_id": "low" if a == "candidate" else "control",
                "ranking_vol12": "0.100000000000",
                "decision_hold": False,
            }
            for d in ("2021-12-27", "2021-12-30")
            for a in ("candidate", "control")
        ],
        "arms": {a: {"result": {"trades": []}} for a in ("candidate", "control")},
    }
    m = formal(db, old, population, analysis)
    assert m["paired_days"] == 0 and m["nominal_rows"] == 4
    assert m["nominal_ledger"][0]["listing_id"] == "low"
    assert m["nominal_ledger"][0]["label_reason"] == "cannot_buy"
    assert m["nominal_ledger"][0]["second_date"] == "2021-12-29"
    assert m["nominal_ledger"][2]["label_reason"] == "right_censored_second_session"
    db.close()


@pytest.mark.parametrize("kind", ["wall", "rss", "scratch", "cpu"])
def test_guard_rejects_successful_child_terminal_overrun(monkeypatch, kind):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    runner = importlib.import_module("research_r6")
    b = {"max_rss_bytes": 100, "max_scratch_bytes": 100, "max_cpu_seconds": 100}
    values = {"elapsed": 1, "peak": 1, "scratch": 1, "cpu": 1}
    values[{"wall": "elapsed", "rss": "peak", "scratch": "scratch", "cpu": "cpu"}[kind]] = 101
    assert runner.terminal_failure(**values, limit=100, budget=b) == kind + "_exceeded"


def test_comparison_does_not_accept_nan_or_changed_identity():
    with pytest.raises(GuardError):
        compare({"response": 1.0}, {"response": float("nan")})
    with pytest.raises(GuardError):
        compare({"listing": "a"}, {"listing": "b"})


@pytest.mark.parametrize(
    "fault", ["none", "one_arm", "zero_valid", "missing_second_identity", "half_tie", "zero_vol"]
)
def test_independent_construct_covers_actual_column_names_and_original_rank(
    monkeypatch, analysis, fault
):
    import pandas as pd

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    reference = importlib.import_module("research_r6_independent")
    ledger, population = sample(analysis)
    if fault == "half_tie":
        for row in ledger:
            row["ranking_vol12"] = "0.100000000001"
    if fault == "zero_vol":
        for row in ledger:
            row["ranking_vol12"] = "0.000000000000"
    for i, row in enumerate(ledger):
        if fault == "zero_valid" or (fault == "one_arm" and row["arm"] == "candidate"):
            row.update(label_reason="cannot_buy", response=None)
        if fault == "missing_second_identity" and i == 0:
            row.update(label_reason="missing_second_identity", response=None)
    nominal = [
        {k: row[k] for k in ("date", "arm", "listing_id", "ranking_vol12", "decision_hold")}
        for row in ledger
    ]
    prepared = pd.DataFrame(
        [
            {
                "day": pd.Timestamp(row["date"]),
                "selected": row["arm"] == "candidate",
                "listing_id": row["listing_id"],
                "return_vol20": 0
                if fault == "zero_vol"
                else 0.1000000000005
                if fault == "half_tie"
                else 0.1,
                "venue_id": row["venue_id"],
                "label_reason": row["label_reason"],
                "next_day": pd.Timestamp(row["first_date"]),
                "next_session": row["date"] + "s1",
                "second_session": row["date"] + "s2",
                "response": row["response"],
            }
            for row in ledger
        ]
    )
    extra = prepared.iloc[[0]].copy()
    extra["listing_id"] = "zz_opaque_tie" if fault == "half_tie" else "rank2"
    extra["return_vol20"] = 0.1000000000006 if fault == "half_tie" else 0.2
    extra["response"] = 0.99
    prepared = pd.concat([prepared, extra], ignore_index=True)
    q = {
        **analysis,
        "required_venues": ["A", "B"],
        "calendar_dates": {
            row["date"] + clock: row["date"] for row in ledger for clock in ("s1", "s2")
        },
        "calendar_next": {row["date"] + "s1": row["date"] + "s2" for row in ledger},
    }
    old = {
        "nominal_choices": nominal,
        "arms": {a: {"result": {"trades": []}} for a in ("candidate", "control")},
    }
    actual = reference.construct(prepared, old, population, q)
    expected = summarize(ledger, population, analysis)
    expected["original_nominal_sha256"] = reference.canonical(nominal)
    assert compare(expected, actual) > 50


@pytest.fixture
def package_case(tmp_path, monkeypatch):
    import types

    from quanthus.hf_dwd_research import digest, write

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    runner = importlib.import_module("research_r6")
    identity = {"git": "a" * 40}
    result = {"identity": identity, "metrics": {"mean": 0.001, "branch": "advance"}}
    result["digest"] = digest(result)
    reference = result["metrics"]
    write(tmp_path / "identity.json", identity)
    write(tmp_path / "result.json", result)
    write(tmp_path / "independent/metrics.json", reference)
    write(
        tmp_path / "independent/receipt.json",
        {
            "passed": True,
            "identity": identity,
            "formal_digest": result["digest"],
            "rebuilt_digest": digest(reference),
        },
    )
    write(
        tmp_path / "review.json",
        {
            "status": "passed",
            "accepted_identity": identity,
            "formal_digest": result["digest"],
            "rebuilt_digest": digest(reference),
        },
    )
    q = json.loads((ROOT / "config/research/research-r6/request.v1.json").read_text())
    return runner, types.SimpleNamespace(job=tmp_path), q, identity


@pytest.mark.parametrize("payload", ["formal", "reference", "review"])
def test_package_revalidates_actual_accepted_payloads(package_case, payload):
    runner, args, q, identity = package_case
    from quanthus.hf_dwd_research import read, write

    path = (
        args.job
        / {
            "formal": "result.json",
            "reference": "independent/metrics.json",
            "review": "review.json",
        }[payload]
    )
    changed = read(path)
    if payload == "formal":
        changed["metrics"]["mean"] = -0.01
    elif payload == "reference":
        changed["mean"] = -0.01
    else:
        changed["formal_digest"] = "sha256:" + "0" * 64
    write(path, changed)
    with pytest.raises(GuardError):
        runner.package(args, q, identity)


def test_package_recovers_only_missing_ready_for_identical_manifest(package_case):
    runner, args, q, identity = package_case
    target = runner.package(args, q, identity)
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    ready = next(p for p in target.iterdir() if p.name.endswith("-READY"))
    ready.unlink()
    runner.package(args, q, identity)
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before
    ready.write_text("wrong\n")
    with pytest.raises(GuardError, match="READY"):
        runner.package(args, q, identity)
