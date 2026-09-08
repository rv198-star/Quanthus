"""Independent-review ledger ingestion and resolution for P3-STAGE-001-D.

This module never creates semantic judgements.  It validates externally supplied reviewer
ledgers against the frozen T-only sample manifest, enforces two distinct primary reviewers,
requires exactly one independent adjudicator for disagreements, and publishes a checksum-bound
resolved-reference package only when every sampled context closes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.stage_learning.reference import (
    ReferenceContext,
    ReferenceKind,
    ReferencePilotError,
    ResolvedReference,
    ReviewerJudgement,
    resolve_reference_reviews,
)
from quanthus.stage_learning.synthetic_panel import load_synthetic_panel
from quanthus.structure_v2_inputs import canonical, digest

REVIEW_SCHEMA = "quanthus.stage-reference-review.v1"
RESOLUTION_SCHEMA = "quanthus.stage-reference-resolution.v1"
ARTIFACT_SCHEMA = "quanthus.stage-reference-resolution-artifact.v1"


class ReviewLedgerError(ValueError):
    """Raised when independent review evidence is incomplete or inconsistent."""


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewLedgerError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ReviewLedgerError(f"{label} must be a JSON object")
    return value


def _jsonl_objects(path: Path, label: str) -> tuple[dict[str, Any], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReviewLedgerError(f"cannot read {label}: {path}") from exc
    output: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewLedgerError(f"{label} line {number} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ReviewLedgerError(f"{label} line {number} must be an object")
        output.append(value)
    return tuple(output)


def load_reference_contexts(sample_manifest: Path) -> tuple[ReferenceContext, ...]:
    raw = _object(sample_manifest, "reference sample manifest")
    if raw.get("schema_version") != "quanthus.stage-reference-sample-manifest.v1":
        raise ReviewLedgerError("reference sample manifest schema differs")
    contexts = raw.get("contexts")
    if not isinstance(contexts, list) or not contexts:
        raise ReviewLedgerError("reference sample manifest contexts are missing")
    output: list[ReferenceContext] = []
    for index, item in enumerate(contexts):
        if not isinstance(item, Mapping):
            raise ReviewLedgerError(f"context {index} must be an object")
        try:
            output.append(
                ReferenceContext(
                    context_id=str(item["context_id"]),
                    security_code=str(item["security_code"]),
                    market_date=date.fromisoformat(str(item["market_date"])),
                    anchor_scale=int(item["anchor_scale"]),
                    episode_id=str(item["episode_id"]),
                    anchor_key=str(item["anchor_key"]),
                    anchor_revision=str(item["anchor_revision"]),
                    sampling_stratum=str(item["sampling_stratum"]),
                    boundary_tags=tuple(str(value) for value in item["boundary_tags"]),
                    evidence_digest=str(item["evidence_digest"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReviewLedgerError(f"context {index} is invalid") from exc
    ids = [item.context_id for item in output]
    if len(set(ids)) != len(ids):
        raise ReviewLedgerError("reference sample manifest contains duplicate contexts")
    return tuple(output)


def _review(value: Mapping[str, Any]) -> ReviewerJudgement:
    expected = {
        "schema_version",
        "context_id",
        "reviewer_id",
        "reviewer_role",
        "reference_kind",
        "supported_stage_codes",
        "adjacent_contexts",
        "reason_codes",
        "evidence_digest",
    }
    if set(value) != expected or value.get("schema_version") != REVIEW_SCHEMA:
        raise ReviewLedgerError("review ledger row fields or schema differ")
    try:
        return ReviewerJudgement(
            context_id=str(value["context_id"]),
            reviewer_id=str(value["reviewer_id"]),
            reviewer_role=str(value["reviewer_role"]),
            reference_kind=ReferenceKind(str(value["reference_kind"])),
            supported_stage_codes=tuple(int(item) for item in value["supported_stage_codes"]),
            adjacent_contexts=tuple(str(item) for item in value["adjacent_contexts"]),
            reason_codes=tuple(str(item) for item in value["reason_codes"]),
            evidence_digest=str(value["evidence_digest"]),
        )
    except (KeyError, TypeError, ValueError, ReferencePilotError) as exc:
        raise ReviewLedgerError("review ledger row is invalid") from exc


def load_reviewer_ledger(path: Path, *, expected_role: str) -> tuple[ReviewerJudgement, ...]:
    rows = tuple(_review(item) for item in _jsonl_objects(path, "review ledger"))
    if not rows:
        raise ReviewLedgerError("review ledger is empty")
    if any(item.reviewer_role != expected_role for item in rows):
        raise ReviewLedgerError("review ledger role differs from the admitted role")
    reviewer_ids = {item.reviewer_id for item in rows}
    if len(reviewer_ids) != 1:
        raise ReviewLedgerError("one ledger file must belong to exactly one reviewer")
    context_ids = [item.context_id for item in rows]
    if len(set(context_ids)) != len(context_ids):
        raise ReviewLedgerError("review ledger contains duplicate contexts")
    return rows


def resolve_complete_review(
    contexts: Sequence[ReferenceContext],
    primary_ledgers: Sequence[Sequence[ReviewerJudgement]],
    adjudicator_ledger: Sequence[ReviewerJudgement] | None,
) -> tuple[ResolvedReference, ...]:
    if len(primary_ledgers) != 2:
        raise ReviewLedgerError("exactly two primary reviewer ledgers are required")
    context_ids = {item.context_id for item in contexts}
    primaries = [tuple(rows) for rows in primary_ledgers]
    primary_ids = [{item.reviewer_id for item in rows} for rows in primaries]
    if any(len(ids) != 1 for ids in primary_ids):
        raise ReviewLedgerError("each primary ledger must have one reviewer identity")
    if next(iter(primary_ids[0])) == next(iter(primary_ids[1])):
        raise ReviewLedgerError("primary reviewer identities must be distinct")
    for rows in primaries:
        if {item.context_id for item in rows} != context_ids:
            raise ReviewLedgerError("each primary reviewer must cover every sampled context")

    by_primary: list[dict[str, ReviewerJudgement]] = [
        {item.context_id: item for item in rows} for rows in primaries
    ]
    disagreements = {
        context_id
        for context_id in context_ids
        if (
            by_primary[0][context_id].reference_kind,
            by_primary[0][context_id].supported_stage_codes,
            by_primary[0][context_id].adjacent_contexts,
        )
        != (
            by_primary[1][context_id].reference_kind,
            by_primary[1][context_id].supported_stage_codes,
            by_primary[1][context_id].adjacent_contexts,
        )
    }
    adjudicators = tuple(adjudicator_ledger or ())
    if disagreements:
        if {item.context_id for item in adjudicators} != disagreements:
            raise ReviewLedgerError(
                "adjudicator ledger must cover exactly the primary-review disagreements"
            )
        adjudicator_ids = {item.reviewer_id for item in adjudicators}
        if len(adjudicator_ids) != 1:
            raise ReviewLedgerError("adjudication must use one reviewer identity")
        if next(iter(adjudicator_ids)) in {
            next(iter(primary_ids[0])),
            next(iter(primary_ids[1])),
        }:
            raise ReviewLedgerError("adjudicator must be independent from both primary reviewers")
    elif adjudicators:
        raise ReviewLedgerError("adjudication is forbidden when primary reviewers fully agree")

    judgements = tuple(item for rows in primaries for item in rows) + adjudicators
    try:
        resolved = resolve_reference_reviews(contexts, judgements)
    except ReferencePilotError as exc:
        raise ReviewLedgerError(str(exc)) from exc
    if len(resolved) != len(contexts):
        raise ReviewLedgerError("resolved reference count differs from the frozen sample")
    return resolved


def publish_resolution(
    *,
    sample_manifest: Path,
    primary_paths: Sequence[Path],
    adjudicator_path: Path | None,
    output_root: Path,
    evidence_tier: str = "HUMAN_EXPERT_REFERENCE",
    panel_config: Path | None = None,
) -> dict[str, Any]:
    contexts = load_reference_contexts(sample_manifest)
    primaries = [load_reviewer_ledger(path, expected_role="primary") for path in primary_paths]
    adjudicator = (
        None
        if adjudicator_path is None
        else load_reviewer_ledger(adjudicator_path, expected_role="adjudicator")
    )
    if evidence_tier not in {"HUMAN_EXPERT_REFERENCE", "SYNTHETIC_EXPERT_CONSENSUS"}:
        raise ReviewLedgerError("reference evidence tier is not admitted")
    panel_id: str | None = None
    if evidence_tier == "SYNTHETIC_EXPERT_CONSENSUS":
        if panel_config is None:
            raise ReviewLedgerError("synthetic reference requires a frozen panel config")
        panel = load_synthetic_panel(panel_config)
        panel_id = str(panel["panel_id"])
        expected_primary = {
            str(panel["assignment"]["primary_a"]),
            str(panel["assignment"]["primary_b"]),
        }
        observed_primary = {rows[0].reviewer_id for rows in primaries if rows}
        if observed_primary != expected_primary:
            raise ReviewLedgerError("synthetic primary reviewer identities differ from panel")
        if adjudicator:
            expected_adjudicator = str(panel["assignment"]["adjudicator"])
            if adjudicator[0].reviewer_id != expected_adjudicator:
                raise ReviewLedgerError("synthetic adjudicator identity differs from panel")
    elif panel_config is not None:
        raise ReviewLedgerError("human reference must not bind a synthetic panel config")
    resolved = resolve_complete_review(contexts, primaries, adjudicator)
    run_id = "stage-reference-resolution-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = output_root.expanduser().resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    resolved_payload = b"".join(
        canonical({"schema_version": RESOLUTION_SCHEMA, **item.to_dict()}) + b"\n"
        for item in resolved
    )
    counts: dict[str, int] = {}
    resolutions: dict[str, int] = {}
    for item in resolved:
        counts[item.reference_kind.value] = counts.get(item.reference_kind.value, 0) + 1
        resolutions[item.resolution] = resolutions.get(item.resolution, 0) + 1
    summary = {
        "schema_version": "quanthus.stage-reference-resolution-summary.v1",
        "run_id": run_id,
        "context_count": len(contexts),
        "resolved_count": len(resolved),
        "reference_kind_counts": dict(sorted(counts.items())),
        "resolution_counts": dict(sorted(resolutions.items())),
        "primary_reviewer_ids": sorted(
            {item.reviewer_id for rows in primaries for item in rows}
        ),
        "adjudicator_id": None if not adjudicator else adjudicator[0].reviewer_id,
        "semantic_reference_status": "RESOLVED_DEVELOPMENT_PILOT_REFERENCE",
        "evidence_tier": evidence_tier,
        "panel_id": panel_id,
        "human_expert_validated": evidence_tier == "HUMAN_EXPERT_REFERENCE",
        "confirmatory_test_authority": False,
        "future_settlement_used_for_resolution": False,
    }
    payloads = {
        "resolved-references.jsonl": resolved_payload,
        "summary.json": canonical(summary),
    }
    for name, payload in payloads.items():
        (root / name).write_bytes(payload)
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": run_id,
        "sample_manifest_digest": digest(_object(sample_manifest, "reference sample manifest")),
        "review_ledger_sha256": {
            str(path): _sha256(path.read_bytes()) for path in primary_paths
        },
        "adjudicator_ledger_sha256": (
            None if adjudicator_path is None else _sha256(adjudicator_path.read_bytes())
        ),
        "files": {name: _sha256(payload) for name, payload in sorted(payloads.items())},
        "resolved_count": len(resolved),
        "evidence_tier": evidence_tier,
        "panel_id": panel_id,
        "human_expert_validated": evidence_tier == "HUMAN_EXPERT_REFERENCE",
        "confirmatory_test_authority": False,
    }
    manifest_bytes = canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "artifact_root": str(root), "manifest_sha256": ready}


def verify_resolution_artifact(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "reference resolution manifest")
    if manifest.get("schema_version") != ARTIFACT_SCHEMA:
        raise ReviewLedgerError("reference resolution artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ReviewLedgerError("reference resolution READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise ReviewLedgerError("reference resolution READY does not bind manifest")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise ReviewLedgerError("reference resolution file inventory is missing")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise ReviewLedgerError("reference resolution file inventory is invalid")
        try:
            observed = _sha256((root / name).read_bytes())
        except OSError as exc:
            raise ReviewLedgerError(f"reference resolution file is unreadable: {name}") from exc
        if observed != expected:
            raise ReviewLedgerError(f"reference resolution file digest differs: {name}")
    summary = _object(root / "summary.json", "reference resolution summary")
    if (
        summary.get("semantic_reference_status")
        != "RESOLVED_DEVELOPMENT_PILOT_REFERENCE"
        or summary.get("resolved_count") != manifest.get("resolved_count")
        or summary.get("evidence_tier") != manifest.get("evidence_tier")
        or summary.get("human_expert_validated") != manifest.get("human_expert_validated")
        or manifest.get("evidence_tier")
        not in {"HUMAN_EXPERT_REFERENCE", "SYNTHETIC_EXPERT_CONSENSUS"}
        or manifest.get("confirmatory_test_authority") is not False
    ):
        raise ReviewLedgerError("reference resolution semantic authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "resolved_count": manifest["resolved_count"],
        "evidence_tier": manifest["evidence_tier"],
        "panel_id": manifest.get("panel_id"),
        "human_expert_validated": manifest["human_expert_validated"],
        "verified": True,
        "confirmatory_test_authority": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    parser.add_argument("--primary", type=Path, action="append", required=True)
    parser.add_argument("--adjudicator", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--evidence-tier",
        choices=("HUMAN_EXPERT_REFERENCE", "SYNTHETIC_EXPERT_CONSENSUS"),
        default="HUMAN_EXPERT_REFERENCE",
    )
    parser.add_argument("--panel-config", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if len(args.primary) != 2:
        raise SystemExit("exactly two --primary ledgers are required")
    result = publish_resolution(
        sample_manifest=args.sample_manifest,
        primary_paths=args.primary,
        adjudicator_path=args.adjudicator,
        output_root=args.output_root,
        evidence_tier=args.evidence_tier,
        panel_config=args.panel_config,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
