from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from quanthus.blueprint_progress import ProgressError, digest, encoded, read_json
from quanthus.exploration_governance import (
    LEDGER,
    NEXT,
    check_history,
    context,
    render,
    validate_memory,
    validate_request,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sample() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    catalog = {"work_items": [{"id": "WORK", "modules": ["HEAD", "OTHER"]}]}
    blueprint = {"records": [{"id": "CP", "updates": [{"module": "HEAD"}]}]}
    evidence = {
        "id": "EV",
        "kind": "assessment",
        "location": "external_receipt",
        "uri": "synthetic:receipt",
        "sha256": "a" * 64,
        "verification": "recorded_not_reopened_by_ci",
        "claim_ceiling": "Synthetic only",
    }
    row = {
        "id": "C1",
        "recorded_at": "2026-10-29T00:00:00Z",
        "actor": "test",
        "question_id": "Q",
        "family": "entry",
        "work_item": "WORK",
        "modules": ["HEAD"],
        "question": "Does this candidate meet the fixed gate?",
        "consumer": "fixed consumer",
        "scope": "fixed synthetic population",
        "claim_ceiling": "not a universal negative",
        "decision_effect": "Stop this candidate; retain accepted code",
        "cost_review": "Unknown",
        "execution_status": "completed",
        "verdict": "not_met",
        "disposition": "reject_candidate",
        "consumer_admission": "not_granted_by_this_record",
        "reopen_conditions": ["new purpose"],
        "retained_assets": ["reader, engineering only"],
        "evidence": [evidence],
        "effort_seconds": None,
        "cost_status": "unknown_not_zero",
        "attempt_refs": [],
        "blueprint_checkpoints": ["CP"],
        "supersedes": None,
    }
    memory = {
        "schema_version": "quanthus.exploration-memory.v1",
        "grants_authority": False,
        "records": [row],
    }
    request = {
        "schema_version": "quanthus.exploration-request.v1",
        "grants_authority": False,
        "status": "proposed",
        "id": "NEXT",
        "kind": "delivery",
        "family": "entry",
        "work_item": "WORK",
        "modules": ["HEAD"],
        "question": "Reuse accepted reader?",
        "consumer": "existing caller",
        "scope": "reader API only",
        "deliverable": "usable call",
        "decision_if_pass": "deliver",
        "decision_if_fail": "stop",
        "acceptance": ["caller works"],
        "stop_conditions": ["no caller"],
        "cumulative_cost_review": "Prior cost remains unknown",
        "runtime_authority_boundary": "Requires domain authority and TPlan budget",
        "memory_sha256": digest(encoded(memory)),
        "prior_reviews": [
            {
                "conclusion_id": "C1",
                "relation": "respect",
                "rationale": "Engineering reuse does not reopen value trial",
            }
        ],
        "budget": {
            "wall_seconds": 600,
            "max_new_bytes": 4096,
            "network_requests": 0,
            "accounting_owner": "TPlan",
        },
    }
    return memory, catalog, blueprint, request


def test_negative_candidate_can_deliver_independently_qualified_asset(sample, tmp_path):
    memory, catalog, blueprint, request = sample
    validate_memory(memory, catalog, blueprint, tmp_path)
    result = validate_request(request, memory, catalog, tmp_path)
    assert result["status"] == "PROPOSAL_CHECKED_NOT_AUTHORIZED"
    assert result["grants_authority"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("execution", ["blocked", "invalid"])
def test_unexecuted_or_broken_experiment_never_becomes_negative(sample, tmp_path, execution):
    memory, catalog, blueprint, _ = sample
    row = memory["records"][0]
    row["execution_status"] = execution
    with pytest.raises(ProgressError, match="failed execution"):
        validate_memory(memory, catalog, blueprint, tmp_path)
    row.update(verdict="not_evaluated", disposition="park")
    validate_memory(memory, catalog, blueprint, tmp_path)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("consumer_admission", "accepted", "admission"),
        ("disposition", "candidate_for_adoption", "needs support"),
        ("blueprint_checkpoints", ["UNKNOWN"], "unknown blueprint"),
        ("modules", ["OTHER"], "scope mismatch"),
        ("cost_status", "recorded", "unknown cost"),
        ("effort_seconds", -1, "effort seconds"),
        ("effort_seconds", 0, "attempt receipts"),
        ("effort_seconds", float("nan"), "effort seconds"),
        ("reopen_conditions", [], "reopen conditions"),
    ],
)
def test_invalid_record_cannot_masquerade_as_complete(sample, tmp_path, field, value, reason):
    memory, catalog, blueprint, _ = sample
    memory["records"][0][field] = value
    with pytest.raises(ProgressError, match=reason):
        validate_memory(memory, catalog, blueprint, tmp_path)


def test_exact_evidence_hash_is_verified_not_just_well_formed(sample, tmp_path):
    memory, catalog, blueprint, _ = sample
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/report.md").write_text("original synthetic evidence")
    subprocess.run(["git", "add", "docs"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    evidence = memory["records"][0]["evidence"][0]
    evidence.update(
        location="git",
        revision=sha,
        path="docs/report.md",
        sha256=digest(b"original synthetic evidence"),
    )
    validate_memory(memory, catalog, blueprint, tmp_path)
    evidence["sha256"] = "b" * 64
    with pytest.raises(ProgressError, match="hash mismatch"):
        validate_memory(memory, catalog, blueprint, tmp_path)


def test_recorded_cost_requires_receipts_and_does_not_reset_unknown_history(sample, tmp_path):
    memory, catalog, blueprint, _ = sample
    memory["records"][0].update(effort_seconds=0, attempt_refs=["synthetic:attempt"])
    with pytest.raises(ProgressError, match="recorded cost status"):
        validate_memory(memory, catalog, blueprint, tmp_path)
    memory["records"][0]["cost_status"] = "recorded"
    validate_memory(memory, catalog, blueprint, tmp_path)


def test_same_stopped_exploration_requires_reopening_but_new_scope_is_possible(sample, tmp_path):
    memory, catalog, _, request = sample
    request["kind"] = "exploration"
    with pytest.raises(ProgressError, match="justified reopening"):
        validate_request(request, memory, catalog, tmp_path)
    review = request["prior_reviews"][0]
    review["relation"] = "different_scope"
    with pytest.raises(ProgressError, match="scope difference"):
        validate_request(request, memory, catalog, tmp_path)
    review["scope_difference"] = "Different specified consumer; subject to semantic review"
    assert validate_request(request, memory, catalog, tmp_path)["grants_authority"] is False


def test_reopening_needs_material_change_and_evidence(sample, tmp_path):
    memory, catalog, _, request = sample
    request["kind"] = "exploration"
    review = request["prior_reviews"][0]
    review["relation"] = "reopen"
    with pytest.raises(ProgressError, match="material reopening"):
        validate_request(request, memory, catalog, tmp_path)
    review["material_change"] = "A newly qualified use-specific source receipt"
    with pytest.raises(ProgressError, match="reopening evidence"):
        validate_request(request, memory, catalog, tmp_path)
    review["evidence"] = copy.deepcopy(memory["records"][0]["evidence"])
    assert validate_request(request, memory, catalog, tmp_path)["grants_authority"] is False


def test_family_rename_does_not_hide_related_module_findings(sample, tmp_path):
    memory, catalog, _, request = sample
    request.update(family="renamed-to-hide-cost", prior_reviews=[])
    with pytest.raises(ProgressError, match="unreviewed prior"):
        validate_request(request, memory, catalog, tmp_path)


def test_unrelated_work_not_blocked_by_universal_negative(sample, tmp_path):
    memory, catalog, _, request = sample
    request.update(family="other-question", modules=["OTHER"], prior_reviews=[])
    assert validate_request(request, memory, catalog, tmp_path)["reviewed_conclusions"] == []


def test_new_conclusion_invalidates_even_otherwise_complete_proposal(sample, tmp_path):
    memory, catalog, _, request = sample
    changed = copy.deepcopy(memory["records"][0])
    changed.update(id="C2", supersedes="C1", recorded_at="2026-10-29T01:00:00Z")
    memory["records"].append(changed)
    with pytest.raises(ProgressError, match="stale memory"):
        validate_request(request, memory, catalog, tmp_path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("wall_seconds", 0),
        ("wall_seconds", float("inf")),
        ("wall_seconds", True),
        ("max_new_bytes", -1),
        ("network_requests", -1),
        ("network_requests", 0.5),
        ("network_requests", True),
    ],
)
def test_budget_must_be_finite_and_cannot_encode_unbounded_retry(sample, tmp_path, key, value):
    memory, catalog, _, request = sample
    request["budget"][key] = value
    with pytest.raises(ProgressError, match="invalid"):
        validate_request(request, memory, catalog, tmp_path)


def test_append_only_after_repack_and_valid_superseding_conclusion(sample, tmp_path):
    memory, catalog, blueprint, _ = sample
    old = copy.deepcopy(memory)
    row = copy.deepcopy(memory["records"][0])
    row.update(
        id="C2",
        supersedes="C1",
        verdict="inconclusive",
        disposition="park",
        recorded_at="2026-10-29T01:00:00Z",
        decision_effect="Correct old interpretation",
    )
    memory["records"].append(row)
    validate_memory(memory, catalog, blueprint, tmp_path)
    check_history(old, memory)
    related = context(memory, "entry", [])
    assert [r["current"] for r in related["related"]] == [False, True]
    assert related["unknown_cost_records"] == ["C1", "C2"]
    assert "| C1 |" not in render(memory) and "| C2 |" in render(memory)
    memory["records"][0]["verdict"] = "inconclusive"
    memory["records"][0]["disposition"] = "park"
    validate_memory(memory, catalog, blueprint, tmp_path)
    with pytest.raises(ProgressError, match="history rewritten"):
        check_history(old, memory)


def test_supersession_cannot_rewrite_an_unrelated_question(sample, tmp_path):
    memory, catalog, blueprint, _ = sample
    row = copy.deepcopy(memory["records"][0])
    row.update(id="C2", supersedes="C1", question_id="OTHER")
    memory["records"].append(row)
    with pytest.raises(ProgressError, match="question family"):
        validate_memory(memory, catalog, blueprint, tmp_path)


def test_duplicate_json_fields_fail_closed(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"records": [], "records": [{"id": "hidden"}]}')
    with pytest.raises(ProgressError, match="duplicate JSON"):
        read_json(path)


def test_repo_proposal_and_reader_are_read_only_and_never_authorize(tmp_path):
    before = (ROOT / LEDGER).read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/exploration_governance.py"),
            "preflight",
            "--root",
            str(ROOT),
            "--request",
            str(ROOT / NEXT),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PROPOSAL_CHECKED_NOT_AUTHORIZED"
    assert report["grants_authority"] is False
    assert (ROOT / LEDGER).read_bytes() == before
    assert not list(tmp_path.iterdir())


def test_cli_rejects_unknown_module_instead_of_empty_history():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/exploration_governance.py"),
            "context",
            "--module",
            "TYPO",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "unknown module" in result.stderr
