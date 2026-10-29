"""No candidate/future read can cure a frozen reference shortage."""
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.stage_learning import prefix_qualification as module
from quanthus.stage_learning.reference import ReferenceContext, ReferenceKind, ResolvedReference


def corpus():
    contexts = tuple(ReferenceContext(str(i), "600001", date(2021, 7, 1), 20,
                                     f"episode-{i}", "anchor", "rev", "sample", (), "sha256:x")
                     for i in range(3))
    references = (
        ResolvedReference("0", ReferenceKind.DEFINITE, (3,), (), "agreement", ("A", "B"),
                          None, "sha256:x"),
        ResolvedReference("1", ReferenceKind.DEFINITE, (4,), (), "agreement", ("A", "B"),
                          None, "sha256:x"),
        ResolvedReference("2", ReferenceKind.AMBIGUOUS_SET, (3, 4), (), "agreement", ("A", "B"),
                          None, "sha256:x"),
    )
    gates = {"minimum_definite_references_per_stage": 1,
             "minimum_ambiguous_references_for_overassertion_audit": 1,
             "maximum_unjudgeable_reference_rate": 0.1}
    return contexts, references, gates


def test_sufficiency_requires_each_definite_stage_and_ambiguity():
    contexts, references, gates = corpus()
    result = module.reference_sufficiency(contexts, references, [], gates)
    assert result["passed"]
    assert result["definite_stage_counts"] == {"3": 1, "4": 1}
    changed = (references[0], replace(references[1], reference_kind=ReferenceKind.COEXISTENCE),
               references[2])
    result = module.reference_sufficiency(contexts, changed, [], gates)
    assert result["reason_codes"] == ["stage_4_reference_count_below_gate"]


@pytest.mark.parametrize("failure", ["missing", "duplicate", "unknown"])
def test_reference_identity_cannot_be_fixed_by_count_only(failure):
    contexts, references, gates = corpus()
    if failure == "missing":
        references = references[:2]
    elif failure == "duplicate":
        references = (references[0], references[0], references[2])
    else:
        references = (references[0], references[1], replace(references[2], context_id="other"))
    with pytest.raises(ValueError, match="REFERENCE_IDENTITY"):
        module.reference_sufficiency(contexts, references, [], gates)


def test_episode_uniqueness_and_quantitative_veto_are_preserved():
    contexts, references, gates = corpus()
    changed = (contexts[0], replace(contexts[1], episode_id=contexts[0].episode_id), contexts[2])
    result = module.reference_sufficiency(changed, references, [{"veto": True}], gates)
    assert set(result["reason_codes"]) == {"duplicate_episode", "quantitative_audit_veto"}


def test_unknown_is_not_negative_and_unjudgeable_limit_still_blocks():
    contexts, references, gates = corpus()
    changed = (references[0], references[1], replace(references[2],
                                                 reference_kind=ReferenceKind.UNJUDGEABLE,
                                                 supported_stage_codes=()))
    result = module.reference_sufficiency(contexts, changed, [], gates)
    assert not result["passed"]
    assert result["unjudgeable_rate"] == 1/3


def test_reference_shortfall_never_constructs_or_predicts_candidate(monkeypatch):
    contexts, references, gates = corpus()
    gates["minimum_definite_references_per_stage"] = 100

    def forbidden(*args, **kwargs):
        pytest.fail("candidate path called before reference sufficiency")

    monkeypatch.setattr(module, "build_learning_observations", forbidden)
    monkeypatch.setattr(module, "predict_stage", forbidden)
    result = module.score_if_sufficient(contexts, references, [], None, (), {}, {}, gates)
    assert result["disposition"] == "INCONCLUSIVE_REFERENCE_SHORTFALL_NOT_SCORED"
    assert result["candidate_scored"] is False
    assert result["consumer_test_authorized"] is False
    assert result["quality_passed"] is False
    assert result["consumer_request_eligible"] is False
    assert result["production_or_P6_admitted"] is False


def test_empty_reference_is_retained_as_inconclusive():
    _, _, gates = corpus()
    result = module.reference_sufficiency((), (), (), gates)
    assert not result["passed"]
    assert result["reference_count"] == 0


def test_sufficient_synthetic_reference_runs_only_frozen_candidate_and_r0(monkeypatch):
    contexts, references, gates = corpus()
    root = Path(__file__).resolve().parents[2]
    full = json.loads((root / "config/stage-learning/qualification.v1.json").read_text())
    gates = {**full["hard_gates"], **gates, "minimum_stage_precision_one_sided_95_lower": 0.0}
    candidate = json.loads((root / full["candidate_config"]).read_text())
    experiment = json.loads((root / full["experiment_config"]).read_text())
    observations = tuple(SimpleNamespace(context_id=c.context_id) for c in contexts)
    seen = []

    def predict(observation, parameters):
        seen.append(parameters.trial_id)
        return {"0": 3, "1": 4, "2": None}[observation.context_id]

    monkeypatch.setattr(module, "build_learning_observations", lambda *args: observations)
    monkeypatch.setattr(module, "predict_stage", predict)
    result = module.score_if_sufficient(contexts, references, [], None, (), candidate,
                                        experiment, gates)
    assert result["candidate_scored"] is True
    assert result["disposition"] == "BOUNDED_DEVELOPMENT_REPLICATION_PASS"
    assert len(seen) == 6 and len(set(seen)) == 2
    assert result["quality_passed"] is True
    assert result["consumer_request_eligible"] is True
    # A quality assessment has no use contract or admission receipt. Even the
    # positive path cannot grant research consumption, production, or P6.
    assert result["consumer_test_authorized"] is False
    assert result["production_or_P6_admitted"] is False


def test_quality_rejection_cannot_become_use_request_eligibility(monkeypatch):
    contexts, references, gates = corpus()
    root = Path(__file__).resolve().parents[2]
    full = json.loads((root / "config/stage-learning/qualification.v1.json").read_text())
    candidate = json.loads((root / full["candidate_config"]).read_text())
    experiment = json.loads((root / full["experiment_config"]).read_text())
    observations = tuple(SimpleNamespace(context_id=c.context_id) for c in contexts)
    monkeypatch.setattr(module, "build_learning_observations", lambda *args: observations)
    monkeypatch.setattr(module, "predict_stage", lambda *args: None)
    monkeypatch.setattr(module, "_hard_gate", lambda *args, **kwargs: {"passed": False})
    result = module.score_if_sufficient(contexts, references, [], None, (), candidate,
                                        experiment, gates)
    assert result["candidate_scored"] is True
    assert result["quality_passed"] is False
    assert result["consumer_request_eligible"] is False
    assert result["consumer_test_authorized"] is False
    assert result["production_or_P6_admitted"] is False
