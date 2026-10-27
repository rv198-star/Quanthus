"""Review regressions: policy truth, trusted panels and executable closure."""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from quanthus.stage_learning.factor_slice import (
    FactorSliceError,
    PanelIdentity,
    consume_queue,
    execution_members,
    load_catalog,
    run,
    verify_execution_closure,
)
from quanthus.structure_v2_inputs import digest
from tests.unit.test_factor_slice import DEFINITION, fact

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("section,key,value", [
    ("facts", "lookback", 60),
    ("facts", "prior_window_excludes_T", False),
    ("factor", "unit", "shares"),
    ("factor", "version", "2.0.0"),
    ("factor", "missing", "fill_zero"),
    ("scope", "business_year", 2025),
    ("scope", "issuers", 129),
])
def test_semantic_declaration_cannot_drift_from_implemented_contract(tmp_path, section, key, value):
    repo = tmp_path / "repository"
    (repo / "config/research").mkdir(parents=True)
    for name in ("config/research/factor541-volume-review.v1.json",
                 "config/p3-baseline-features.research.v1.json"):
        (repo / name).write_bytes((REPO / name).read_bytes())
    name = repo / "config/research/factor541-volume-review.v1.json"
    spec = json.loads(name.read_text())
    spec[section][key] = value
    name.write_text(json.dumps(spec))
    with pytest.raises(FactorSliceError, match="POLICY|CONTRACT"):
        load_catalog(repo)


@pytest.mark.parametrize("field,value", [
    ("definition_digest", "sha256:" + "f" * 64),
    ("source_member_digest", "sha256:" + "e" * 64),
    ("factor_revision", "sha256:" + "d" * 64),
    ("volume_ratio20", "2"),
])
def test_bare_unverified_panel_never_drives_selection(field, value):
    row = deepcopy(fact(1, volume="0.5"))
    row[field] = value
    with pytest.raises(FactorSliceError, match="BINDING|IDENTITY"):
        consume_queue([row], volume_gate=True)


@pytest.mark.parametrize("field,value", [
    ("definition_digest", "sha256:" + "f" * 64),
    ("source_member_digest", "sha256:" + "e" * 64),
    ("factor_revision", "sha256:" + "d" * 64),
    ("volume_ratio20", "2"),
    ("admitted", False),
])
def test_frozen_panel_identity_rejects_mutation_of_values_or_lineage(field, value):
    row = fact(1, volume="0.5")
    identity = PanelIdentity(digest([row]), DEFINITION, (
        (row["security_id"], row["market_date"], row["source_member_digest"]),))
    assert consume_queue([row], volume_gate=True, identity=identity)["actual_review_slots"] == 0
    changed = deepcopy(row)
    changed[field] = value
    with pytest.raises(FactorSliceError, match="BINDING"):
        consume_queue([changed], volume_gate=True, identity=identity)


def test_matching_panel_hash_does_not_replace_canonical_definition_or_source_key():
    row = fact(1)
    original_source = row["source_member_digest"]
    row["source_member_digest"] = "sha256:" + "e" * 64
    identity = PanelIdentity(digest([row]), DEFINITION, (
        (row["security_id"], row["market_date"], original_source),))
    with pytest.raises(FactorSliceError, match="SOURCE_BINDING"):
        consume_queue([row], volume_gate=True, identity=identity)
    row["source_member_digest"] = original_source
    row["definition_digest"] = "sha256:" + "f" * 64
    identity = PanelIdentity(digest([row]), row["definition_digest"], (
        (row["security_id"], row["market_date"], original_source),))
    with pytest.raises(FactorSliceError, match="BINDING"):
        consume_queue([row], volume_gate=True, identity=identity)


def test_matched_panel_hash_still_requires_factor_revision_to_bind_definition():
    row = fact(1)
    row["factor_revision"] = "sha256:" + "d" * 64
    identity = PanelIdentity(digest([row]), DEFINITION, (
        (row["security_id"], row["market_date"], row["source_member_digest"]),))
    with pytest.raises(FactorSliceError, match="BINDING"):
        consume_queue([row], volume_gate=True, identity=identity)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "changed"])
def test_executable_closure_requires_exact_complete_members(mutation):
    members = execution_members(REPO)
    verify_execution_closure(REPO, members)
    if mutation == "missing":
        members = [m for m in members if m["path"] != "src/quanthus/stage_learning/core.py"]
    elif mutation == "duplicate":
        members.append(deepcopy(members[-1]))
    else:
        members[-1]["sha256"] = "0" * 64
    with pytest.raises(FactorSliceError, match="CODE_CLOSURE"):
        verify_execution_closure(REPO, members)


def test_actual_import_origin_must_match_declared_tree(tmp_path):
    import sys
    from types import SimpleNamespace

    module = SimpleNamespace(__file__=str(tmp_path / "foreign.py"))
    with (patch.dict(sys.modules, {"quanthus.foreign_checkout_probe": module}),
          pytest.raises(FactorSliceError, match="EXECUTED_ORIGIN")):
        verify_execution_closure(REPO, execution_members(REPO))


@pytest.mark.parametrize("closure", [[], ["config/research/factor541-volume-review.v1.json"]])
def test_incomplete_code_closure_rejected_before_mkdir_or_source(tmp_path, closure):
    import hashlib

    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    before = tuple(source.iterdir())
    budget = {
        "purpose": "RETROSPECTIVE_RESEARCH_REVIEW_QUEUE_ONLY", "new_source_requests": 0,
        "source_root": str(source), "output": str(output),
        "source_handoff_sha256": "0" * 64,
        "code_and_config_members": [
            {"path": name, "sha256": hashlib.sha256((REPO / name).read_bytes()).hexdigest()}
            for name in closure],
        "deadline": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        "maximum_seconds": 60, "rss_bytes": 4 * 1024**3,
        "minimum_free_bytes": 0, "maximum_output_bytes": 1024,
    }
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(budget))
    with patch("quanthus.stage_learning.factor_slice.read_admissions",
               side_effect=RuntimeError("SOURCE_MUST_NOT_BE_REACHED")) as reader:
        with pytest.raises(FactorSliceError, match="CODE.*CLOSURE"):
            run(source=source, repo=REPO, output=output, budget_path=path)
        reader.assert_not_called()
    assert not output.exists()
    assert tuple(source.iterdir()) == before
