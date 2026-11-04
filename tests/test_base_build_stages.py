from __future__ import annotations

import fcntl
import json
from dataclasses import replace

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base_build_stages import Stage, StageRunner


def binding():
    return {"input_identity": "sha256:" + "a" * 64, "code_revision": "b" * 40,
            "environment_identity": "sha256:" + "c" * 64,
            "policy_identity": "sha256:" + "d" * 64,
            "scope": {"predecessor_through": "2026-09-30", "through_date": "2026-10-31"}}


def pipeline(calls, *, crash=None):
    def stage(name, dependencies):
        def run(output, inputs):
            calls.append(name)
            output.mkdir()
            text = "".join((inputs[d] / "value.txt").read_text() for d in sorted(inputs)) + name
            (output / "value.txt").write_text(text)
            if crash == name:
                raise RuntimeError("synthetic interruption")
            return {"rows": len(text)}

        def verify(output, result):
            if result["rows"] != len((output / "value.txt").read_text()):
                raise BoundedError("SYNTHETIC_ROW_COUNT_MISMATCH")

        return Stage(name, tuple(dependencies), run, verify)

    return [stage("source-admission", []), stage("ods", ["source-admission"]),
            stage("dwd", ["source-admission"]), stage("paired-audit", ["ods", "dwd"])]


def runner(tmp_path, calls, **kwargs):
    return StageRunner(root=tmp_path / "job", sources=[], binding=binding(),
                       stages=pipeline(calls, **kwargs))


def test_diamond_shares_one_prerequisite_and_rechecks_full_outputs_on_rerun(tmp_path):
    calls = []
    first = runner(tmp_path, calls).execute()
    assert len(calls) == 4 and calls.count("source-admission") == 1
    assert all(not r["reused"] for r in first.values())
    second = runner(tmp_path, calls).execute()
    assert len(calls) == 4
    assert all(r["reused"] for r in second.values())
    assert {n: r["receipt"]["output_identity"] for n, r in first.items()} == {
        n: r["receipt"]["output_identity"] for n, r in second.items()}


def test_interruption_keeps_partial_and_recovers_only_unfinished_stage_and_consumers(tmp_path):
    calls = []
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        runner(tmp_path, calls, crash="ods").execute()
    failed = tmp_path / "job/stages/ods/attempt-000001"
    assert (failed / "output/value.txt").exists()
    assert (failed / "FAILURE.json").exists()
    assert not (failed / "SUCCESS.json").exists()
    result = runner(tmp_path, calls).execute()
    assert result["source-admission"]["reused"]
    assert not result["ods"]["reused"]
    assert result["ods"]["path"].parent.name == "attempt-000002"
    assert (failed / "FAILURE.json").exists()
    assert calls.count("source-admission") == 1


@pytest.mark.parametrize("damage", ["bytes", "missing", "extra", "receipt", "symlink"])
def test_invalid_completed_stage_is_preserved_and_rebuilt(tmp_path, damage):
    calls = []
    first = runner(tmp_path, calls).execute()
    output = first["ods"]["path"]
    if damage == "bytes":
        (output / "value.txt").write_text("changed")
    elif damage == "missing":
        (output / "value.txt").unlink()
    elif damage == "extra":
        (output / "undeclared.txt").write_text("extra")
    elif damage == "receipt":
        (output.parent / "SUCCESS.json").write_text("{}")
    else:
        (output / "value.txt").unlink()
        (output / "value.txt").symlink_to(tmp_path / "elsewhere")
    second = runner(tmp_path, calls).execute()
    assert second["source-admission"]["reused"]
    assert second["dwd"]["reused"]
    assert not second["ods"]["reused"]
    assert second["ods"]["path"] != output
    assert output.exists()
    events = [json.loads(s) for s in (tmp_path / "job/events.jsonl").read_text().splitlines()]
    assert any(e["status"] == "REUSE_REJECTED" and e["stage"] == "ods" for e in events)


def test_changed_upstream_policy_invalidates_dependents_even_when_bytes_unchanged(tmp_path):
    calls = []
    first = runner(tmp_path, calls).execute()
    stages = pipeline(calls)
    stages[0] = replace(stages[0], configuration={"source-rule-version": 2})
    second = StageRunner(root=tmp_path / "job", sources=[], binding=binding(),
                         stages=stages).execute()
    assert all(not r["reused"] for r in second.values())
    assert (first["source-admission"]["receipt"]["output_identity"]
            == second["source-admission"]["receipt"]["output_identity"])
    assert (first["paired-audit"]["receipt"]["stage_identity"]
            != second["paired-audit"]["receipt"]["stage_identity"])


def test_unrelated_branch_policy_change_preserves_shared_source_and_other_branch(tmp_path):
    calls = []
    runner(tmp_path, calls).execute()
    stages = [replace(s, configuration={"projection-version": 2}) if s.name == "ods" else s
              for s in pipeline(calls)]
    result = StageRunner(root=tmp_path / "job", sources=[], binding=binding(),
                         stages=stages).execute()
    assert result["source-admission"]["reused"]
    assert result["dwd"]["reused"]
    assert not result["ods"]["reused"]
    assert not result["paired-audit"]["reused"]


def test_mutual_exclusion_stops_duplicate_launch(tmp_path):
    calls = []
    root = tmp_path / "job"
    root.mkdir()
    with (root / "BUILD.lock").open("w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BoundedError, match="JOB_ALREADY_RUNNING"):
            runner(tmp_path, calls).execute()
    assert not calls


@pytest.mark.parametrize("damage", ["cycle", "missing-dependency", "unsafe-name", "duplicate"])
def test_invalid_graph_fails_before_creating_job(tmp_path, damage):
    stages = pipeline([])
    if damage == "cycle":
        stages[0] = replace(stages[0], dependencies=("paired-audit",))
    elif damage == "missing-dependency":
        stages[0] = replace(stages[0], dependencies=("missing",))
    elif damage == "unsafe-name":
        stages[0] = replace(stages[0], name="../escape")
    else:
        stages.append(stages[0])
    with pytest.raises(BoundedError, match="BUILD_STAGE_GRAPH"):
        StageRunner(root=tmp_path / "job", sources=[], binding=binding(), stages=stages)
    assert not (tmp_path / "job").exists()


def test_unreceipted_output_after_abrupt_death_is_not_adopted(tmp_path):
    output = tmp_path / "job/stages/source-admission/attempt-000001/output"
    output.mkdir(parents=True)
    (output / "value.txt").write_text("source-admission")
    result = runner(tmp_path, []).execute()
    assert not result["source-admission"]["reused"]
    assert result["source-admission"]["path"].parent.name == "attempt-000002"
    assert (output / "value.txt").read_text() == "source-admission"
