from __future__ import annotations

import copy
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from quanthus.blueprint_progress import (
    CATALOG,
    LEDGER,
    VIEW,
    ProgressError,
    check_append_only,
    check_change_impact,
    current_updates,
    digest,
    encoded,
    event_scope,
    read_json,
    reconcile,
    render,
    validate_ledger,
    verify_evidence,
    work_context,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/blueprint_progress.py"


@pytest.fixture
def setup(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    catalog = {
        "schema_version": "quanthus.blueprint.v1",
        "repository": "rv198-star/Quanthus",
        "grants_authority": False,
        "modules": [
            {
                "id": "MVP",
                "name": "总体",
                "purpose": "交付用途",
                "parent": None,
                "kind": "capability",
                "depends_on": [],
                "focus_facet": "quality",
            },
            {
                "id": "S02",
                "name": "整理",
                "purpose": "状态判断",
                "parent": "MVP",
                "kind": "state",
                "depends_on": [],
                "focus_facet": "quality",
            },
        ],
        "work_items": [
            {"id": "P5-STATE-003", "issue": 543, "modules": ["S02"], "contribution": "固定状态实验"}
        ],
    }
    evidence = {
        "id": "EV",
        "location": "external_receipt",
        "kind": "assessment",
        "uri": "synthetic:unreachable-receipt",
        "sha256": "a" * 64,
        "verification": "recorded_not_reopened_by_ci",
        "claim_ceiling": "synthetic only",
    }
    observation = {
        "work_item": "P5-STATE-003",
        "number": 543,
        "state": "open",
        "state_reason": None,
        "title": "[P5-STATE-003] experiment",
        "updated_at": "2026-10-26T00:00:00Z",
        "body_sha256": "b" * 64,
    }
    update = {
        "module": "S02",
        "facet": "engineering",
        "status": "accepted",
        "scope": "one bounded implementation",
        "remaining": "quality unknown",
        "next_action": "evaluate qualified reference",
        "evidence": ["EV"],
        "supersedes": None,
    }
    baseline = {
        "id": "BASE",
        "work_item": "P5-STATE-003",
        "outcome": "baseline",
        "recorded_at": "2026-10-27T00:00:00Z",
        "actor": "test",
        "summary": "baseline",
        "evidence": [evidence],
        "updates": [update],
        "issue_observations": [observation],
    }
    ledger = {"schema_version": "quanthus.blueprint-progress.v1", "records": [baseline]}
    for path, value in [(CATALOG, catalog), (LEDGER, ledger)]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(encoded(value))
    (tmp_path / VIEW).parent.mkdir(parents=True)
    (tmp_path / VIEW).write_text(render(catalog, ledger))
    return catalog, ledger, tmp_path


def successor(ledger: dict[str, Any], name: str = "NEXT") -> dict[str, Any]:
    record = copy.deepcopy(ledger["records"][0])
    record.update(id=name, outcome="rejected", recorded_at="2026-10-27T01:00:00Z")
    record["updates"][0].update(facet="quality", status="rejected", supersedes=None)
    return record


def cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_engineering_and_closed_rejected_issue_do_not_promote_quality_or_parent(setup):
    catalog, ledger, root = setup
    record = successor(ledger)
    record["issue_observations"][0].update(state="closed", state_reason="completed")
    ledger["records"].append(record)
    validate_ledger(catalog, ledger, root)
    current = current_updates(ledger)
    assert current[("S02", "engineering")]["status"] == "accepted"
    assert current[("S02", "quality")]["status"] == "rejected"
    assert ("S02", "admission") not in current
    assert ("MVP", "quality") not in current
    assert "本候选拒绝" in render(catalog, ledger)


@pytest.mark.parametrize("change", ["closed", "reopened", "body", "identity", "missing"])
def test_lifecycle_drift_is_not_silently_accepted(setup, change):
    catalog, ledger, _ = setup
    live = copy.deepcopy(ledger["records"][0]["issue_observations"])
    if change == "identity":
        live[0]["title"] = "[OTHER] title"
        with pytest.raises(ProgressError, match="identity"):
            reconcile(catalog, ledger, live)
    elif change == "missing":
        with pytest.raises(ProgressError, match="every mapped issue"):
            reconcile(catalog, ledger, [])
    else:
        if change == "closed":
            live[0].update(state="closed", state_reason="completed")
        elif change == "reopened":
            live[0].update(state_reason="reopened", updated_at="2026-10-27T00:00:00Z")
        else:
            live[0]["body_sha256"] = "c" * 64
        assert len(reconcile(catalog, ledger, live)) == 1
        assert current_updates(ledger)[("S02", "engineering")]["status"] == "accepted"


@pytest.mark.parametrize(
    "defect",
    [
        "orphan",
        "cycle",
        "parent_cycle",
        "duplicate_module",
        "unknown_evidence",
        "missing_evidence",
        "stale",
        "duplicate_update",
        "time",
        "authority",
        "second_baseline",
    ],
)
def test_invalid_checkpoint_is_rejected(setup, defect):
    catalog, ledger, root = setup
    record = successor(ledger)
    ledger["records"].append(record)
    update = record["updates"][0]
    if defect == "orphan":
        catalog["work_items"][0]["modules"] = ["UNKNOWN"]
    elif defect == "cycle":
        catalog["modules"][0]["depends_on"] = ["S02"]
        catalog["modules"][1]["depends_on"] = ["MVP"]
    elif defect == "parent_cycle":
        catalog["modules"][0]["parent"] = "S02"
    elif defect == "duplicate_module":
        catalog["modules"].append(catalog["modules"][0])
    elif defect == "unknown_evidence":
        update["evidence"] = ["UNKNOWN"]
    elif defect == "missing_evidence":
        update["evidence"] = []
    elif defect == "stale":
        update.update(facet="engineering", status="partial", supersedes=None)
    elif defect == "duplicate_update":
        record["updates"].append(update)
    elif defect == "time":
        record["recorded_at"] = "2026-01-01T00:00:00Z"
    elif defect == "authority":
        update.update(facet="admission", status="accepted")
    else:
        record["outcome"] = "baseline"
    with pytest.raises(ProgressError):
        validate_ledger(catalog, ledger, root)


def test_no_change_acknowledges_issue_without_advancing_module(setup):
    catalog, ledger, root = setup
    record = successor(ledger)
    record.update(outcome="no_module_change", updates=[], no_change_reason="Issue wording only")
    record["issue_observations"][0]["body_sha256"] = "c" * 64
    before = current_updates(ledger)
    ledger["records"].append(record)
    validate_ledger(catalog, ledger, root)
    assert current_updates(ledger) == before
    assert reconcile(catalog, ledger, record["issue_observations"]) == []
    del record["no_change_reason"]
    with pytest.raises(ProgressError, match="no-module-change"):
        validate_ledger(catalog, ledger, root)


def test_history_cannot_be_rewritten_or_deleted(setup):
    _, ledger, _ = setup
    next_ledger = copy.deepcopy(ledger)
    next_ledger["records"].append(successor(ledger))
    check_append_only(ledger, next_ledger)
    next_ledger["records"][0]["summary"] = "retroactively green"
    with pytest.raises(ProgressError, match="edited or removed"):
        check_append_only(ledger, next_ledger)
    with pytest.raises(ProgressError, match="edited or removed"):
        check_append_only(ledger, {**ledger, "records": []})


def test_record_idempotence_stale_failure_and_recoverable_view(setup):
    _, ledger, root = setup
    record = successor(ledger)
    path = root / "new.json"
    path.write_bytes(encoded(record))
    assert cli(root, "record", "--checkpoint", str(path)).returncode == 0
    before = (root / LEDGER).read_bytes()
    assert cli(root, "record", "--checkpoint", str(path)).returncode == 0
    assert (root / LEDGER).read_bytes() == before
    record["id"] = "STALE"
    path.write_bytes(encoded(record))
    assert cli(root, "record", "--checkpoint", str(path)).returncode == 1
    assert (root / LEDGER).read_bytes() == before
    (root / VIEW).write_text("interrupted disposable view")
    assert cli(root, "check").returncode == 1
    assert cli(root, "render").returncode == 0
    assert cli(root, "check").returncode == 0


def test_two_concurrent_writers_do_not_lose_a_checkpoint(setup):
    _, ledger, root = setup
    paths = []
    for name in ["FIRST", "SECOND"]:
        record = successor(ledger, name)
        path = root / (name + ".json")
        path.write_bytes(encoded(record))
        paths.append(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: cli(root, "record", "--checkpoint", str(p)), paths))
    assert sorted(r.returncode for r in results) == [0, 1]
    assert len(read_json(root / LEDGER)["records"]) == 2
    assert cli(root, "check").returncode == 0


def test_git_evidence_reads_pinned_revision_not_mutable_head(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "docs/evidence.md"
    path.parent.mkdir()
    path.write_text("bounded acceptance")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "evidence",
        ],
        cwd=tmp_path,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    evidence = {
        "location": "git",
        "revision": revision,
        "path": "docs/evidence.md",
        "kind": "acceptance",
        "sha256": digest(path.read_bytes()),
        "claim_ceiling": "bounded development",
    }
    path.write_text("later mutable prose")
    verify_evidence(evidence, tmp_path)
    evidence["sha256"] = "f" * 64
    with pytest.raises(ProgressError, match="hash mismatch"):
        verify_evidence(evidence, tmp_path)


def test_new_declared_work_cannot_skip_registration_but_unrelated_issue_can(setup):
    catalog, _, root = setup
    issue = {"number": 999, "body": "old unrelated issue"}
    assert event_scope(catalog, {"issue": issue}) == "outside"
    issue["body"] = "### Blueprint impact\n\nS02 / quality\n\n### Other\ntext"
    assert event_scope(catalog, {"issue": issue}) == "unmapped"
    event = root / "event.json"
    event.write_bytes(encoded({"issue": issue}))
    result = cli(root, "audit", "--event", str(event), "--output", str(root / "audit.json"))
    assert result.returncode == 2
    assert read_json(root / "audit.json")["status"] == "UNMAPPED_WORK_ITEM"
    issue["body"] = "### Blueprint impact\n\noutside-scope: P10 supply"
    assert event_scope(catalog, {"issue": issue}) == "outside"


def test_real_catalog_has_full_vocabulary_and_preserves_head2_baseline():
    catalog, ledger = read_json(ROOT / CATALOG), read_json(ROOT / LEDGER)
    validate_ledger(catalog, ledger, ROOT)
    states = {m["id"] for m in catalog["modules"] if m["kind"] == "state"}
    assert states == {f"S{n:02}" for n in range(13)}
    context = work_context(catalog, {**ledger, "records": ledger["records"][:1]}, 543, "S02")
    assert context["grants_authority"] is False
    head2 = next(m for m in context["modules"] if m["id"] == "S02")
    assert head2["facets"]["quality"]["status"] == "inconclusive"
    assert head2["facets"]["admission"]["status"] == "not_granted"
    assert len(context["modules"]) == 1
    assert render(catalog, ledger) == (ROOT / VIEW).read_text()


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"state":"blocked","state":"accepted"}')
    with pytest.raises(ProgressError, match="duplicate JSON key"):
        read_json(path)


def test_prepared_draft_cannot_advance_module_or_overwrite_prior_draft(setup):
    _, ledger, root = setup
    snapshot = root / "issues.json"
    snapshot.write_bytes(encoded({"issues": ledger["records"][0]["issue_observations"]}))
    draft = root / "draft.json"
    result = cli(
        root, "prepare", "--issue", "543", "--issues", str(snapshot), "--output", str(draft)
    )
    assert result.returncode == 0
    before = (root / LEDGER).read_bytes()
    assert cli(root, "record", "--checkpoint", str(draft)).returncode == 1
    assert (root / LEDGER).read_bytes() == before
    assert (
        cli(
            root, "prepare", "--issue", "543", "--issues", str(snapshot), "--output", str(draft)
        ).returncode
        == 1
    )


def test_code_change_requires_scoped_update_or_explicit_no_change(setup):
    catalog, ledger, root = setup
    catalog["modules"][1]["watch_paths"] = ["src/quanthus/stage_learning/head2_*.py"]
    changed = ["src/quanthus/stage_learning/head2_temporal.py"]
    with pytest.raises(ProgressError, match="S02"):
        check_change_impact(catalog, ledger, ledger, changed)
    next_ledger = copy.deepcopy(ledger)
    record = successor(ledger)
    record.update(
        updates=[],
        reviewed_modules=["S02"],
        no_change_reason="Internal refactor; prior qualification and limits unchanged",
    )
    next_ledger["records"].append(record)
    validate_ledger(catalog, next_ledger, root)
    check_change_impact(catalog, ledger, next_ledger, changed)
    assert current_updates(next_ledger) == current_updates(ledger)
    check_change_impact(catalog, ledger, ledger, ["src/quanthus/unrelated.py"])


def test_failed_audit_preserves_a_failure_receipt(setup):
    _, _, root = setup
    snapshot = root / "missing-issues.json"
    snapshot.write_bytes(encoded({"issues": []}))
    output = root / "audit.json"
    result = cli(root, "audit", "--issues", str(snapshot), "--output", str(output))
    assert result.returncode == 1
    assert read_json(output)["status"] == "AUDIT_FAILED"
    assert read_json(output)["module_status_changed"] is False
