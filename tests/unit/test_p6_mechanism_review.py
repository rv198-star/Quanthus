from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quanthus.forward_validation import snapshot_replay as replay
from quanthus.forward_validation.review import review_snapshot_replay
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError, _tree
from tests.unit.test_p6_base24_runtime import runtime_source as runtime_source
from tests.unit.test_p6_base24_runtime import view as view
from tests.unit.test_p6_snapshot_replay import synthetic_history as synthetic_history

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/forward-validation/runtime/p6-fixed-snapshot-replay.v1.json"


@pytest.fixture
def completed(synthetic_history, tmp_path):
    history, _, _ = synthetic_history
    shared = replay.SharedSnapshotReplay()
    try:
        for name, smoke in [("smoke", True), ("main", False)]:
            replay.run_replay(
                history=history,
                output=tmp_path / name,
                repository=ROOT,
                config=CONFIG,
                smoke=smoke,
                shared=shared,
            )
    finally:
        shared.close()
    replay.run_replay(
        history=history, output=tmp_path / "independent", repository=ROOT, config=CONFIG
    )
    return dict(
        history=history,
        primary=tmp_path / "main",
        independent=tmp_path / "independent",
        shared_owner=tmp_path / "smoke",
        repository=ROOT,
        config=CONFIG,
    )


def audit(args):
    identities = {
        k: file_sha(args[k] / "manifest.json")
        for k in ("history", "primary", "independent", "shared_owner")
    }
    return review_snapshot_replay(**args, expected_manifests=identities)


def repack(root):
    path = root / "manifest.json"
    m = json.loads(path.read_text())
    m["files"] = {
        rel: file_sha(root / rel) for rel in _tree(root) if rel not in {"READY", "manifest.json"}
    }
    path.write_text(json.dumps(m))
    (root / "READY").write_text(file_sha(path) + "\n")


def test_review_recomputes_continuous_ledger_and_never_grants_official_credit(completed):
    report = audit(completed)
    assert report["mechanism_review"] == "VERIFIED"
    assert report["sessions"] == 10
    assert report["ledger_reconciliations"] == 10
    assert report["comparison"]["independent_cycles_byte_equal"] == 10
    assert report["official_budget_credit"] == 0
    assert report["formal_p6_review_ready"] is False
    assert report["claims"]["historical_pit"] is False
    assert report["scope"]["hindsight_engineering_cohort"] is True
    assert "net_total_return" not in json.dumps(report)
    # This fixture deliberately has a five-session rather than 20-session tail.
    assert report["unresolved_forecasts"] == 1


@pytest.mark.parametrize("mutation", ["forged_counts", "broken_chain", "tail_entry"])
def test_repacked_counterfeit_fails_semantics_not_just_old_manifest_hash(completed, mutation):
    root = completed["primary"]
    if mutation == "forged_counts":
        p = root / "result.safe.json"
        v = json.loads(p.read_text())
        v["counters"]["candidates"] += 1
        p.write_text(json.dumps(v))
    else:
        p = sorted((root / "cycles").glob("*.private.json"))[-1]
        v = json.loads(p.read_text())
        if mutation == "broken_chain":
            v["summary"]["ledger_before"] = "sha256:" + "f" * 64
        else:
            entry = next(
                row
                for q in sorted((root / "cycles").glob("*.private.json"))
                for row in json.loads(q.read_text())["entry_decisions"]
            )
            v["entry_decisions"].append(entry)
        p.write_text(json.dumps(v))
        p.with_name(p.name.replace(".private.", ".safe.")).write_text(json.dumps(v["summary"]))
    repack(root)  # including new hashes/READY: the current bytes are deliberately admitted.
    with pytest.raises(ValueError, match="p6_review_(census|ledger|tail)"):
        audit(completed)


def test_independent_divergence_and_unknown_physical_member_fail_closed(completed):
    root = completed["independent"]
    p = root / "candidates.private.json"
    original = p.read_bytes()
    p.write_bytes(original + b" ")
    repack(root)
    with pytest.raises(ValueError, match="p6_review_reproduction"):
        audit(completed)
    p.write_bytes(original)
    repack(root)
    (root / "unexpected").write_text("")
    repack(root)
    with pytest.raises(ValueError, match="p6_review_physical"):
        audit(completed)


def test_manifest_binding_and_no_source_fallback(completed, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("review must not use source network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = audit(completed)
    assert report["mechanism_review"] == "VERIFIED"
    identities = {
        k: file_sha(completed[k] / "manifest.json")
        for k in ("history", "primary", "independent", "shared_owner")
    }
    identities["history"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="p6_review_binding"):
        review_snapshot_replay(**completed, expected_manifests=identities)
    # An identical directory cannot serve as an independent reproduction.
    duplicate = dict(completed, independent=completed["primary"])
    with pytest.raises(ValueError, match="p6_review_independence"):
        audit(duplicate)


def test_input_symlink_is_not_an_independent_artifact(completed, tmp_path):
    root = tmp_path / "linked"
    shutil.copytree(completed["independent"], root)
    p = root / "feature-panel.parquet"
    p.unlink()
    p.symlink_to(completed["shared_owner"] / p.name)
    with pytest.raises(SourceAssessmentError, match="SOURCE_SYMLINK"):
        audit(dict(completed, independent=root))


@pytest.mark.parametrize("name", ["independent", "shared_owner"])
def test_matching_private_bytes_do_not_excuse_false_companion_summary(completed, name):
    p = completed[name] / "result.safe.json"
    value = json.loads(p.read_text())
    value["counters"]["simulated_fills"] += 1
    p.write_text(json.dumps(value))
    repack(completed[name])
    with pytest.raises(ValueError, match="p6_review_census"):
        audit(completed)
