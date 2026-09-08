from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.reference import ReferenceContext, ReferenceKind, ReviewerJudgement
from quanthus.stage_learning.review_ledger import (
    ReviewLedgerError,
    load_reviewer_ledger,
    publish_resolution,
    resolve_complete_review,
    verify_resolution_artifact,
)

ROOT = find_project_root()
PANEL = ROOT / "config/stage-learning/synthetic-expert-panel.v1.json"


def context(name: str) -> ReferenceContext:
    return ReferenceContext(
        context_id=f"sha256:{name * 8}",
        security_code="600519",
        market_date=date(2020, 1, 2),
        anchor_scale=20,
        episode_id=f"ep-{name}",
        anchor_key="anchor",
        anchor_revision="sha256:" + "a" * 64,
        sampling_stratum="random",
        boundary_tags=(),
        evidence_digest="sha256:" + name * 64,
    )


def judgement(
    item: ReferenceContext,
    reviewer: str,
    *,
    role: str = "primary",
    kind: ReferenceKind = ReferenceKind.DEFINITE,
    stages: tuple[int, ...] = (3,),
) -> ReviewerJudgement:
    return ReviewerJudgement(
        context_id=item.context_id,
        reviewer_id=reviewer,
        reviewer_role=role,
        reference_kind=kind,
        supported_stage_codes=stages,
        adjacent_contexts=(),
        reason_codes=("reviewed_t_only_evidence",),
        evidence_digest=item.evidence_digest,
    )


def test_complete_primary_agreement_resolves_without_adjudicator() -> None:
    contexts = (context("1"), context("2"))
    first = tuple(judgement(item, "reviewer-a") for item in contexts)
    second = tuple(judgement(item, "reviewer-b") for item in contexts)
    resolved = resolve_complete_review(contexts, (first, second), None)
    assert len(resolved) == 2
    assert {item.resolution for item in resolved} == {"primary_agreement"}
    assert {item.adjudicator_id for item in resolved} == {None}


def test_disagreement_requires_exact_independent_adjudication() -> None:
    item = context("3")
    first = (judgement(item, "reviewer-a", stages=(3,)),)
    second = (judgement(item, "reviewer-b", stages=(4,)),)
    with pytest.raises(ReviewLedgerError, match="adjudicator ledger"):
        resolve_complete_review((item,), (first, second), None)

    adjudicator = (
        judgement(item, "reviewer-c", role="adjudicator", stages=(4,)),
    )
    resolved = resolve_complete_review((item,), (first, second), adjudicator)
    assert resolved[0].resolution == "third_party_adjudication"
    assert resolved[0].supported_stage_codes == (4,)
    assert resolved[0].adjudicator_id == "reviewer-c"


def test_primary_reviewers_must_be_distinct_and_cover_every_context() -> None:
    contexts = (context("4"), context("5"))
    full = tuple(judgement(item, "reviewer-a") for item in contexts)
    with pytest.raises(ReviewLedgerError, match="distinct"):
        resolve_complete_review(contexts, (full, full), None)
    short = (judgement(contexts[0], "reviewer-b"),)
    with pytest.raises(ReviewLedgerError, match="cover every"):
        resolve_complete_review(contexts, (full, short), None)


def test_future_or_wrong_evidence_cannot_be_smuggled_into_resolution() -> None:
    item = context("6")
    first = (judgement(item, "reviewer-a"),)
    bad = ReviewerJudgement(
        context_id=item.context_id,
        reviewer_id="reviewer-b",
        reviewer_role="primary",
        reference_kind=ReferenceKind.DEFINITE,
        supported_stage_codes=(3,),
        adjacent_contexts=(),
        reason_codes=("future_outcome_used",),
        evidence_digest="sha256:" + "f" * 64,
    )
    with pytest.raises(ReviewLedgerError, match="evidence digest differs"):
        resolve_complete_review((item,), (first, (bad,)), None)


def test_publish_and_verify_complete_resolution_artifact(tmp_path: Path) -> None:
    contexts = (context("8"), context("9"))
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "schema_version": "quanthus.stage-reference-sample-manifest.v1",
                "contexts": [item.to_dict() for item in contexts],
            },
            default=str,
        ),
        encoding="utf-8",
    )
    primary_paths: list[Path] = []
    for reviewer in ("reviewer-a", "reviewer-b"):
        path = tmp_path / f"{reviewer}.jsonl"
        rows = []
        for item in contexts:
            rows.append(
                {
                    "schema_version": "quanthus.stage-reference-review.v1",
                    "context_id": item.context_id,
                    "reviewer_id": reviewer,
                    "reviewer_role": "primary",
                    "reference_kind": "definite",
                    "supported_stage_codes": [3],
                    "adjacent_contexts": [],
                    "reason_codes": ["reviewed_t_only_evidence"],
                    "evidence_digest": item.evidence_digest,
                }
            )
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        primary_paths.append(path)
    result = publish_resolution(
        sample_manifest=sample,
        primary_paths=primary_paths,
        adjudicator_path=None,
        output_root=tmp_path / "out",
    )
    verified = verify_resolution_artifact(Path(result["artifact_root"]))
    assert verified["verified"] is True
    assert verified["resolved_count"] == 2
    assert verified["confirmatory_test_authority"] is False


def test_synthetic_resolution_binds_panel_and_cannot_claim_human_validation(
    tmp_path: Path,
) -> None:
    contexts = (context("a"), context("b"))
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "schema_version": "quanthus.stage-reference-sample-manifest.v1",
                "contexts": [item.to_dict() for item in contexts],
            },
            default=str,
        ),
        encoding="utf-8",
    )
    primary_paths: list[Path] = []
    for reviewer in ("youzi-emotion-cycle-v1", "youzi-capacity-trend-v1"):
        path = tmp_path / f"{reviewer}.jsonl"
        rows = [
            {
                "schema_version": "quanthus.stage-reference-review.v1",
                "context_id": item.context_id,
                "reviewer_id": reviewer,
                "reviewer_role": "primary",
                "reference_kind": "definite",
                "supported_stage_codes": [3],
                "adjacent_contexts": [],
                "reason_codes": ["synthetic_style_blind_review"],
                "evidence_digest": item.evidence_digest,
            }
            for item in contexts
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        primary_paths.append(path)
    result = publish_resolution(
        sample_manifest=sample,
        primary_paths=primary_paths,
        adjudicator_path=None,
        output_root=tmp_path / "out",
        evidence_tier="SYNTHETIC_EXPERT_CONSENSUS",
        panel_config=PANEL,
    )
    verified = verify_resolution_artifact(Path(result["artifact_root"]))
    assert verified["evidence_tier"] == "SYNTHETIC_EXPERT_CONSENSUS"
    assert verified["human_expert_validated"] is False
    assert verified["panel_id"] == "stage-first-group-a-share-public-style-archetypes-v1"


def test_synthetic_resolution_rejects_wrong_persona_identity(tmp_path: Path) -> None:
    item = context("c")
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "schema_version": "quanthus.stage-reference-sample-manifest.v1",
                "contexts": [item.to_dict()],
            },
            default=str,
        ),
        encoding="utf-8",
    )
    paths: list[Path] = []
    for reviewer in ("wrong-persona", "youzi-capacity-trend-v1"):
        path = tmp_path / f"{reviewer}.jsonl"
        row = {
            "schema_version": "quanthus.stage-reference-review.v1",
            "context_id": item.context_id,
            "reviewer_id": reviewer,
            "reviewer_role": "primary",
            "reference_kind": "definite",
            "supported_stage_codes": [3],
            "adjacent_contexts": [],
            "reason_codes": ["synthetic_style_blind_review"],
            "evidence_digest": item.evidence_digest,
        }
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        paths.append(path)
    with pytest.raises(ReviewLedgerError, match="identities differ from panel"):
        publish_resolution(
            sample_manifest=sample,
            primary_paths=paths,
            adjudicator_path=None,
            output_root=tmp_path / "out",
            evidence_tier="SYNTHETIC_EXPERT_CONSENSUS",
            panel_config=PANEL,
        )


def test_ledger_file_has_exact_schema_one_identity_and_role(tmp_path: Path) -> None:
    item = context("7")
    row = {
        "schema_version": "quanthus.stage-reference-review.v1",
        "context_id": item.context_id,
        "reviewer_id": "reviewer-a",
        "reviewer_role": "primary",
        "reference_kind": "definite",
        "supported_stage_codes": [3],
        "adjacent_contexts": [],
        "reason_codes": ["reviewed_t_only_evidence"],
        "evidence_digest": item.evidence_digest,
    }
    path = tmp_path / "review.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    loaded = load_reviewer_ledger(path, expected_role="primary")
    assert loaded[0].reviewer_id == "reviewer-a"

    path.write_text(json.dumps({**row, "unexpected": True}) + "\n", encoding="utf-8")
    with pytest.raises(ReviewLedgerError, match="fields or schema"):
        load_reviewer_ledger(path, expected_role="primary")
