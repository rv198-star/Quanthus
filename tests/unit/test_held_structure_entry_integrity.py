"""Disposable entry checks; expert outputs are fixed only after one genuine evaluation.

The population constant is reduced to two synthetic reviews, not the validation
logic. No archive, price file, historical result or network is used.
"""

from __future__ import annotations

import gzip
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.expert_strategy import ExpertStrategyRegistry, FormalStrategyRunner
from quanthus.structure_v2_inputs import canonical
from scripts import expert545_capture_b_trace as capture_driver
from scripts import expert545_held_structure as driver
from tests.unit.test_formal_expert_strategy import EVENT_ID, _context, _held_facts
from tests.unit.test_held_structure_shadow import evaluate, support


def write(path, value):
    path.write_bytes(canonical(value) + b"\n")


@pytest.fixture
def job(tmp_path, monkeypatch):
    registry = ExpertStrategyRegistry(driver.ROOT)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    monkeypatch.setattr(driver, "HELD_POPULATION", (2, 1, 1), raising=False)
    monkeypatch.setattr(driver, "_guard", lambda _: None)
    root = tmp_path / "job"
    for name in ("facts", "trace", "evidence"):
        (root / name).mkdir(parents=True)
    write(
        root / "scope.frozen.json",
        {
            "deadline": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "minimum_free_bytes": 0,
            "maximum_new_bytes": 32 * 1024**2,
        },
    )
    structural = support()
    facts = _held_facts(sessions=3, reductions=0)
    context = _context(position_state="dynamic", position_evidence=facts.evidence)
    result = evaluate(runner)
    monkeypatch.setattr(driver, "evaluate_shadow", lambda *a, **kw: result)
    held = [
        dict(
            market_date="2020-01-06",
            unit_id=f"synthetic-{i}",
            security_code=structural.security_code,
            quantity=300,
            entry_key="one-episode",
        )
        for i in range(2)
    ]
    held_path = root / "held.json"
    write(held_path, {"rows": {"B": held}})
    fact_path = root / "facts" / f"{structural.security_code}.private.json.gz"
    with gzip.open(fact_path, "wb") as f:
        f.write(canonical({"held_decision_inputs": [asdict(structural)]}))
    trace = root / "trace/contexts.gz"
    with gzip.open(trace, "wt") as f:
        for row in held:
            f.write(
                canonical(
                    {
                        **row,
                        "context": asdict(context),
                        "position_facts": asdict(facts),
                        "event_id": EVENT_ID,
                        "base_required_action": "none",
                        "base_reason_code": "base_position_management_clear",
                        "target_notional": "100000",
                        "last_mark_price": "100",
                        "baseline_decision_digest": result.baseline.digest,
                    }
                ).decode()
                + "\n"
            )
    # Before the repair this deliberately represents the old incomplete closure.
    # After repair the same fixture uses the real new closure builder.
    pins = (
        driver.execution_pins(driver.ROOT)
        if hasattr(driver, "execution_pins")
        else {"scripts/expert545_held_structure.py": driver.sha(Path(driver.__file__))}
    )
    frozen = {
        "kind": "expert545-held-structure-inputs-v2",
        "held_file": str(held_path),
        "held_sha256": driver.sha(held_path),
        "trace": str(trace),
        "archive_freeze_sha256": "a" * 64,
        "code_config_sha256": pins,
        "source_pins": {},
        "selected": {},
        "supplemental": [],
    }
    write(root / "trace/launch.frozen.json", {"kind": "synthetic-capture-launch"})
    frozen["capture_launch_sha256"] = driver.sha(root / "trace/launch.frozen.json")
    write(
        root / "trace/receipt.safe.json",
        {
            "rows": 2,
            "source_freeze_sha256": "a" * 64,
            "capture_launch_sha256": frozen["capture_launch_sha256"],
            "comparisons": {
                n: {"equal": True}
                for n in ("decisions.json", "engine-events.json", "manifest.json")
            },
            "trace_sha256": driver.sha(trace),
        },
    )
    frozen["trace_proof_sha256"] = driver.sha(root / "trace/receipt.safe.json")
    write(root / "inputs.final.frozen.json", frozen)
    write(
        root / "evidence/facts-materialization.safe.json",
        {
            "input_freeze_sha256": driver.sha(root / "inputs.final.frozen.json"),
            "receipts": [{"code": structural.security_code, "facts_sha256": driver.sha(fact_path)}],
        },
    )
    return root


def assert_no_output(job):
    assert not (job / "decision-shadow.private.json.gz").exists()
    assert not (job / "evidence/decision-comparison.safe.json").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_key", "two-episodes"),
        ("quantity", 400),
        ("market_date", "2020-01-07"),
        ("unit_id", "rebound-unit"),
    ],
)
def test_changed_held_bytes_fail_before_output(job, field, value):
    path = job / "held.json"
    payload = json.loads(path.read_bytes())
    payload["rows"]["B"][0][field] = value
    write(path, payload)
    with pytest.raises(ValueError, match="HELD_POPULATION_BYTES"):
        driver.compare(job)
    assert_no_output(job)


@pytest.mark.parametrize(
    "name",
    [
        "scripts/expert545_held_structure.py",
        "src/quanthus/aggregator.py",
        "config/stage-10-11-projection.v1.json",
    ],
)
def test_code_config_pin_drift_fails_before_output(job, name):
    path = job / "inputs.final.frozen.json"
    payload = json.loads(path.read_bytes())
    payload["code_config_sha256"][name] = "0" * 64
    write(path, payload)
    with pytest.raises(ValueError, match="HELD_FROZEN_CODE_CHANGED"):
        driver.compare(job)
    assert_no_output(job)


def test_complete_closure_cannot_silently_drop_transitive_code(job):
    path = job / "inputs.final.frozen.json"
    payload = json.loads(path.read_bytes())
    payload["code_config_sha256"].pop("src/quanthus/aggregator.py", None)
    write(path, payload)
    with pytest.raises(ValueError, match="HELD_CODE_CLOSURE"):
        driver.compare(job)
    assert_no_output(job)


def test_unchanged_synthetic_comparison_keeps_one_episode_stop(job):
    receipt = driver.compare(job)
    assert receipt["counts"]["held_rows"] == 2
    assert receipt["changed_distinct_entry_episodes"] == 1
    assert receipt["decision"] == "PARK_NO_DISTRIBUTED_DECISION_DELTA"


def test_duplicate_held_key_fails_even_with_resigned_disposable_plan(job):
    path = job / "held.json"
    payload = json.loads(path.read_bytes())
    payload["rows"]["B"][1]["unit_id"] = "synthetic-0"
    write(path, payload)
    plan = job / "inputs.final.frozen.json"
    frozen = json.loads(plan.read_bytes())
    frozen["held_sha256"] = driver.sha(path)
    write(plan, frozen)
    with pytest.raises(ValueError, match="HELD_POPULATION_DUPLICATE"):
        driver.compare(job)
    assert_no_output(job)


def test_build_revalidates_source_before_output_directory(job):
    source = job.parent / "source.bin"
    source.write_bytes(b"original")
    frozen_path = job / "inputs.final.frozen.json"
    frozen = json.loads(frozen_path.read_bytes())
    frozen["selected"] = {"daily_ohlcv_none": str(source)}
    frozen["source_pins"] = {
        str(source): {"bytes": source.stat().st_size, "sha256": "sha256:" + driver.sha(source)}
    }
    write(frozen_path, frozen)
    source.write_bytes(b"altered!")
    (job / "facts").rename(job / "existing-facts")
    with pytest.raises(ValueError, match="HELD_SELECTED_SOURCE_CHANGED"):
        driver.build(job)
    assert not (job / "facts").exists()
    assert source.read_bytes() == b"altered!"


def test_no_process_input_does_not_invent_one_process():
    value = support(process_id=None, process_revision=None)
    assert value.process_count == 0
    assert value.process_revisions == ()


def test_real_synthetic_materialization_has_zero_process_placeholders(tmp_path):
    from tests.unit.test_held_structure_inputs import DAYS, arguments

    args = arguments()
    config = json.loads(driver.CONFIG.read_bytes())
    driver._security_build(
        {
            "code": args["code"],
            "inputs": args,
            "held_days": DAYS,
            "config": config,
            "output": str(tmp_path),
        }
    )
    with gzip.open(tmp_path / "000001.private.json.gz", "rt") as f:
        values = json.load(f)["held_decision_inputs"]
    assert len(values) == len(DAYS)
    assert all(
        r["process_id"] is None and r["process_count"] == 0 and r["process_revisions"] == []
        for r in values
    )


def test_launch_snapshot_covers_transitive_imports_and_detects_drift(tmp_path):
    freeze = tmp_path / "freeze.json"
    write(freeze, {"synthetic": True})
    identity = capture_driver.launch_identity(driver.ROOT, freeze)
    assert identity["launcher_sha256"] == driver.sha(Path(capture_driver.__file__))
    assert "src/quanthus/aggregator.py" in identity["producer_code_config_sha256"]
    capture_driver.verify_launch(identity, driver.ROOT)
    identity["producer_code_config_sha256"]["src/quanthus/aggregator.py"] = "0" * 64
    with pytest.raises(ValueError, match="TRACE_LAUNCH_CHANGED"):
        capture_driver.verify_launch(identity, driver.ROOT)


def test_capture_rejects_dirty_producer_before_source_read_or_output(tmp_path, monkeypatch):
    freeze = tmp_path / "freeze.json"
    write(freeze, {"repository": str(driver.ROOT)})
    responses = iter([capture_driver.PRODUCER, " M src/quanthus/synthetic.py"])
    monkeypatch.setattr(capture_driver.subprocess, "check_output", lambda *a, **k: next(responses))
    output = tmp_path / "capture"
    with pytest.raises(ValueError, match="TRACE_PRODUCER_CODE_DIRTY"):
        capture_driver.capture(freeze, output)
    assert not output.exists()


def test_capture_seals_launch_before_invoking_producer_and_restores_network(tmp_path, monkeypatch):
    import socket

    freeze = tmp_path / "freeze.json"
    write(freeze, {"repository": str(driver.ROOT), "roles": {"primary": {"bindings": {}}}})
    responses = iter([capture_driver.PRODUCER, ""])
    monkeypatch.setattr(capture_driver.subprocess, "check_output", lambda *a, **k: next(responses))
    monkeypatch.setattr(
        capture_driver.shutil, "disk_usage", lambda _: SimpleNamespace(free=1 << 40)
    )
    output = tmp_path / "capture"
    original = socket.socket.connect

    def invocation(*args):
        launch = json.loads((output / "launch.frozen.json").read_bytes())
        assert launch["source_freeze_sha256"] == driver.sha(freeze)
        assert launch["launcher_sha256"] == driver.sha(Path(capture_driver.__file__))
        assert socket.socket.connect is not original
        raise RuntimeError("synthetic producer failure after launch seal")

    monkeypatch.setattr(capture_driver, "_capture_runtime", invocation)
    with pytest.raises(RuntimeError, match="synthetic producer failure"):
        capture_driver.capture(freeze, output)
    assert socket.socket.connect is original
    assert (output / "launch.frozen.json").is_file()
    assert not (output / "receipt.safe.json").exists()


def test_old_incomplete_freeze_is_evidence_only_not_reusable(job):
    path = job / "inputs.final.frozen.json"
    frozen = json.loads(path.read_bytes())
    frozen["kind"] = "expert545-held-structure-inputs-v1"
    write(path, frozen)
    with pytest.raises(ValueError, match="HELD_LEGACY_INCOMPLETE_CODE_CLOSURE"):
        driver.compare(job)
    assert_no_output(job)


def test_launch_record_replacement_fails_before_comparison_output(job):
    write(job / "trace/launch.frozen.json", {"kind": "altered-launch"})
    with pytest.raises(ValueError, match="HELD_TRACE_CHANGED"):
        driver.compare(job)
    assert_no_output(job)


def test_another_imported_package_copy_cannot_pass_disk_pins(job, tmp_path, monkeypatch):
    other = tmp_path / "other.py"
    other.write_text("# disposable code\n")
    monkeypatch.setitem(
        sys.modules, "quanthus.synthetic_drift", SimpleNamespace(__file__=str(other))
    )
    with pytest.raises(ValueError, match="HELD_IMPORTED_CODE_ORIGIN"):
        driver.compare(job)
    assert_no_output(job)


def test_trace_cannot_reassign_entry_episode_with_resigned_disposable_proof(job):
    trace = job / "trace/contexts.gz"
    with gzip.open(trace, "rt") as f:
        rows = [json.loads(line) for line in f]
    rows[0]["entry_key"] = "different-episode"
    with gzip.open(trace, "wt") as f:
        for row in rows:
            f.write(canonical(row).decode() + "\n")
    proof_path = job / "trace/receipt.safe.json"
    proof = json.loads(proof_path.read_bytes())
    proof["trace_sha256"] = driver.sha(trace)
    write(proof_path, proof)
    plan_path = job / "inputs.final.frozen.json"
    plan = json.loads(plan_path.read_bytes())
    plan["trace_proof_sha256"] = driver.sha(proof_path)
    write(plan_path, plan)
    material_path = job / "evidence/facts-materialization.safe.json"
    material = json.loads(material_path.read_bytes())
    material["input_freeze_sha256"] = driver.sha(plan_path)
    write(material_path, material)
    with pytest.raises(ValueError, match="HELD_CONTEXT_ENTRY_EPISODE"):
        driver.compare(job)
    assert_no_output(job)
