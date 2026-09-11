"""Seal the frozen Stage-007 semantic opinions, then score one frozen candidate exactly once."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_reference_protocol import (
    PROTOCOL_DIGEST,
    REVIEW_SCHEMA,
    publish_bundle,
    verify_bundle,
)
from quanthus.stage_learning.order_rule_prepare import verify_prepare_artifact
from quanthus.stage_learning.order_rule_screen import RULE_DIGEST
from quanthus.stage_learning.order_rule_validation import (
    VALIDATION_DIGEST,
    score_sealed_bundle,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/order-rule-resolve-score.v1.json"
CONFIG_DIGEST = "sha256:2407e10998b7601f35d7cefa18dea26dc448eb471307cd50d2da32b0f91c1781"
ARTIFACT_SCHEMA = "quanthus.stage-order-rule-resolve-score-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-order-rule-resolve-score-summary.v1"
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_JOB = re.compile(r"^stage_[0-9a-f]{32}$")
PRIMARY = {"S0", "S2", "N", "U2"}
RECENT_WIDTH = {"CONTRACTING", "EXPANDING", "STABLE", "UNKNOWN"}
RECENT_CENTER = {"STABLE", "SHIFTING", "UNKNOWN"}
BUNDLE_FILES = (
    "evidence.json", "reviews.json", "resolved.json", "summary.json", "manifest.json", "READY",
)


class OrderRuleResolveScoreError(ValueError):
    """The frozen C-stage opinion or score contract differs."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _read(path: Path, limit: int = 33554432) -> bytes:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        raise OrderRuleResolveScoreError("bounded_regular_file_required")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise OrderRuleResolveScoreError("file_budget")
    return raw


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_read(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrderRuleResolveScoreError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise OrderRuleResolveScoreError(f"{label}_must_be_object")
    return value


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or find_project_root() / DEFAULT_CONFIG
    config = _object(path, "resolve_score_config")
    if digest(config) != CONFIG_DIGEST:
        raise OrderRuleResolveScoreError("resolve_score_config_drift")
    if (
        config.get("schema_version") != "quanthus.stage-order-rule-resolve-score.v1"
        or config.get("work_item") != "P3-STAGE-007-C"
        or config.get("bindings") != {
            "reference_protocol_digest": PROTOCOL_DIGEST,
            "rule_digest": RULE_DIGEST,
            "validation_digest": VALIDATION_DIGEST,
        }
    ):
        raise OrderRuleResolveScoreError("resolve_score_identity_or_binding")
    authority = config.get("authority")
    required_true = {
        "read_prepared_evidence", "seal_raw_opinions", "score_single_frozen_candidate_after_seal",
    }
    required_false = {
        "new_market_data_read", "opinion_regeneration", "semantic_answer_retry",
        "parameter_search", "sample_replacement", "arbitration_access", "permanent_blind_access",
        "official_labels", "daily_or_strategy", "trading",
    }
    if not isinstance(authority, Mapping):
        raise OrderRuleResolveScoreError("resolve_score_authority")
    if any(authority.get(key) is not True for key in required_true):
        raise OrderRuleResolveScoreError("resolve_score_required_authority")
    if any(authority.get(key) is not False for key in required_false):
        raise OrderRuleResolveScoreError("resolve_score_authority_expanded")
    limits = config.get("limits")
    if not isinstance(limits, Mapping) or limits != {
        "maximum_jobs": 1,
        "maximum_cases": 160,
        "maximum_opinions": 320,
        "worker_wall_seconds": 600,
        "maximum_artifact_bytes": 33554432,
    }:
        raise OrderRuleResolveScoreError("resolve_score_limits")
    return config


def _parse_decisions(root: Path, config: Mapping[str, Any]) -> tuple[bytes, list[dict[str, Any]]]:
    review = config["review_decisions"]
    path = (root / str(review["path"])).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise OrderRuleResolveScoreError("review_decisions_outside_repo") from exc
    raw = _read(path, 512 * 1024)
    if _sha(raw) != review["sha256"]:
        raise OrderRuleResolveScoreError("review_decisions_digest")
    text = raw.decode("utf-8")
    lines = text.splitlines()
    expected_comments = {
        "# schema=quanthus.stage-order-rule-review-decisions.v1",
        "# work_item=P3-STAGE-007-C",
        (
            "# provenance=self_simulated|chatgpt-owner-session-stage007|"
            "stage007-reference-review-20260911"
        ),
        "# reviewers=chatgpt-short-term-stage007|chatgpt-fund-stage007",
        "# candidate_output_seen_during_case_review=false",
        "# cognitive_blinding_claimed=false",
    }
    if set(lines[:6]) != expected_comments:
        raise OrderRuleResolveScoreError("review_decisions_metadata")
    header = (
        "index|case_id|short_primary|fund_primary|recent_width|recent_center|"
        "short_reason|fund_reason"
    )
    if len(lines) < 7 or lines[6] != header:
        raise OrderRuleResolveScoreError("review_decisions_header")
    decisions = []
    for expected_index, line in enumerate(lines[7:]):
        fields = line.split("|")
        if len(fields) != 8:
            raise OrderRuleResolveScoreError("review_decision_fields")
        (
            index, case_id, short_primary, fund_primary,
            recent_width, recent_center, short_reason, fund_reason,
        ) = fields
        if (
            index != str(expected_index)
            or not _SHA.fullmatch(case_id)
            or short_primary not in PRIMARY
            or fund_primary not in PRIMARY
            or recent_width not in RECENT_WIDTH
            or recent_center not in RECENT_CENTER
            or not short_reason.strip()
            or not fund_reason.strip()
        ):
            raise OrderRuleResolveScoreError("review_decision_value")
        decisions.append({
            "index": expected_index,
            "case_id": case_id,
            "short_primary": short_primary,
            "fund_primary": fund_primary,
            "recent_width": recent_width,
            "recent_center": recent_center,
            "short_reason": short_reason,
            "fund_reason": fund_reason,
        })
    if (
        len(decisions) != review["case_count"]
        or len({d["case_id"] for d in decisions}) != len(decisions)
    ):
        raise OrderRuleResolveScoreError("review_decision_denominator")
    counts = Counter()
    for item in decisions:
        left, right = item["short_primary"], item["fund_primary"]
        if left != right or left.startswith("U") or right.startswith("U"):
            counts["AMBIGUOUS"] += 1
        elif left == "S0":
            counts["STAGE_0"] += 1
        elif left == "S2":
            counts["STAGE_2"] += 1
        else:
            counts["NEITHER"] += 1
    if dict(counts) != review["expected_reference_preview"]:
        raise OrderRuleResolveScoreError("review_decision_preview_drift")
    return raw, decisions


def _refs(case: Mapping[str, Any], area: str) -> list[str]:
    refs = []
    for item in case[area].values():
        value = item.get("revision_id")
        if value is not None:
            refs.append(str(value))
    return list(dict.fromkeys(refs))


def _primary(value: str) -> dict[str, str]:
    return {
        "S0": {"stage_0": "TRUE", "stage_2": "FALSE"},
        "S2": {"stage_0": "FALSE", "stage_2": "TRUE"},
        "N": {"stage_0": "FALSE", "stage_2": "FALSE"},
        "U2": {"stage_0": "FALSE", "stage_2": "UNKNOWN"},
    }[value]


def _opinion(
    case: Mapping[str, Any], role: str, primary_code: str, recent_width: str,
    recent_center: str, reason: str, review: Mapping[str, Any],
) -> dict[str, Any]:
    primary = _primary(primary_code)
    primary_refs = _refs(case, "primary")
    recent_refs = _refs(case, "recent")
    positive = "TRUE" in primary.values()
    uncertain = (
        "UNKNOWN" in primary.values()
        or recent_width == "UNKNOWN"
        or recent_center == "UNKNOWN"
    )
    if primary_code == "S2":
        recent_supports = (
            recent_width in {"CONTRACTING", "STABLE"}
            and recent_center == "STABLE"
        )
    elif primary_code == "S0":
        recent_supports = recent_width == "EXPANDING" or recent_center == "SHIFTING"
    else:
        recent_supports = False
    reviewer_id = (
        review["short_term_reviewer_id"] if role == "short_term" else review["fund_reviewer_id"]
    )
    return {
        "schema_version": REVIEW_SCHEMA,
        "case_id": case["case_id"],
        "protocol_digest": PROTOCOL_DIGEST,
        "reviewer_id": reviewer_id,
        "role": role,
        "provenance": {
            "kind": review["kind"],
            "origin_id": review["origin_id"],
            "session_id": review["session_id"],
        },
        "primary_window_sessions": 20,
        "recent_window_sessions": 10,
        "primary": primary,
        "recent": {"width": recent_width, "center": recent_center},
        "support_refs": (
            primary_refs + (recent_refs if recent_supports else []) if positive else []
        ),
        "contradiction_refs": (
            ([] if recent_supports else recent_refs) if positive else primary_refs + recent_refs
        ),
        "reason": reason,
        "unknowns": [reason] if uncertain else [],
    }


def build_opinions(
    evidence: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cases = {case["case_id"]: case for case in evidence["cases"]}
    if (
        len(cases) != config["prepare"]["case_count"]
        or set(cases) != {d["case_id"] for d in decisions}
    ):
        raise OrderRuleResolveScoreError("review_decisions_case_binding")
    opinions = []
    review = config["review_decisions"]
    for item in decisions:
        case = cases[item["case_id"]]
        opinions.append(_opinion(
            case, "short_term", item["short_primary"], item["recent_width"], item["recent_center"],
            item["short_reason"], review,
        ))
        opinions.append(_opinion(
            case, "fund", item["fund_primary"], item["recent_width"], item["recent_center"],
            item["fund_reason"], review,
        ))
    if len(opinions) != review["opinion_count"]:
        raise OrderRuleResolveScoreError("review_opinion_denominator")
    return opinions


def _artifact_files(root: Path) -> dict[str, bytes]:
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        if relative in {"manifest.json", "READY"}:
            continue
        files[relative] = _read(path)
    return files


def run_resolve_score(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise OrderRuleResolveScoreError("exact_code_revision_required")
    started = time.perf_counter()
    root = find_project_root()
    config = load_config(config_path)
    prepare = config["prepare"]
    if not _JOB.fullmatch(prepare["job_id"]):
        raise OrderRuleResolveScoreError("prepare_job_id")
    training_root = training_root.expanduser().resolve()
    prepared = (training_root / str(prepare["artifact_relative_path"])).resolve()
    run_parent = (training_root / "runs/stage-worker" / prepare["job_id"]).resolve()
    try:
        prepared.relative_to(run_parent)
    except ValueError as exc:
        raise OrderRuleResolveScoreError("prepare_artifact_outside_job") from exc
    verified = verify_prepare_artifact(prepared, code_revision=prepare["code_revision"])
    if (
        verified["manifest_sha256"] != prepare["manifest_sha256"]
        or verified["selection_digest"] != prepare["selection_digest"]
        or verified["evidence_digest"] != prepare["evidence_digest"]
        or verified["selected_case_count"] != prepare["case_count"]
        or verified["source_isolation_verified"] is not True
    ):
        raise OrderRuleResolveScoreError("prepare_receipt_binding")
    decisions_raw, decisions = _parse_decisions(root, config)
    evidence = _object(prepared / "evidence.json", "prepared_evidence")
    selection = _object(prepared / "selection.json", "prepared_selection")
    if (
        digest(evidence) != prepare["evidence_digest"]
        or selection.get("selection_digest") != prepare["selection_digest"]
    ):
        raise OrderRuleResolveScoreError("prepare_payload_binding")
    opinions = build_opinions(evidence, decisions, config)

    run_id = (
        "stage-order-rule-resolve-score-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    target = output_root.expanduser().resolve() / run_id
    if target.exists():
        raise OrderRuleResolveScoreError("immutable_artifact_exists")
    target.mkdir(parents=True)
    bundle = target / "reference-bundle"
    bundle_receipt = publish_bundle(bundle, evidence, opinions)
    # Candidate scoring is intentionally after the immutable reference bundle exists.
    score = score_sealed_bundle(
        bundle, selection,
        expected_manifest=bundle_receipt["manifest_sha256"],
        expected_selection=prepare["selection_digest"],
    )
    if score["rule_digest"] != RULE_DIGEST or score["validation_digest"] != VALIDATION_DIGEST:
        raise OrderRuleResolveScoreError("score_frozen_binding")
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-007-C",
        "code_revision": code_revision,
        "config_digest": CONFIG_DIGEST,
        "prepare_job_id": prepare["job_id"],
        "prepare_manifest_sha256": prepare["manifest_sha256"],
        "selection_digest": prepare["selection_digest"],
        "evidence_digest": prepare["evidence_digest"],
        "review_decisions_sha256": config["review_decisions"]["sha256"],
        "reference_manifest_sha256": bundle_receipt["manifest_sha256"],
        "opinion_digest": score["opinion_digest"],
        "case_count": score["benchmark"]["case_count"],
        "opinion_count": len(opinions),
        "primary_reference_counts": bundle_receipt["primary_reference_counts"],
        "primary_disagreement_count": score["primary_disagreement_count"],
        "joint_primary_unknown_count": score["joint_primary_unknown_count"],
        "recent_disagreement_count": score["recent_disagreement_count"],
        "gate_calculation": score["gate_calculation"],
        "independent_validation": False,
        "full_context_blinding_claimed": False,
        "market_representativeness": False,
        "official_labels_authorized": False,
        "runtime_activation_authorized": False,
        "parameter_search": False,
        "new_market_data_reads": 0,
        "opinion_regeneration": False,
        "elapsed_seconds": time.perf_counter() - started,
    }
    (target / "review-decisions.tsv").write_bytes(decisions_raw)
    (target / "score.json").write_bytes(canonical(score))
    (target / "parent.json").write_bytes(canonical({
        "job_id": prepare["job_id"], "manifest_sha256": prepare["manifest_sha256"],
        "selection_digest": prepare["selection_digest"],
        "evidence_digest": prepare["evidence_digest"],
    }))
    (target / "summary.json").write_bytes(canonical(summary))
    files = _artifact_files(target)
    if sum(map(len, files.values())) > config["limits"]["maximum_artifact_bytes"] - 100000:
        raise OrderRuleResolveScoreError("resolve_score_artifact_budget")
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": CONFIG_DIGEST,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: _sha(raw) for name, raw in sorted(files.items())},
    }
    manifest_raw = canonical(manifest)
    (target / "manifest.json").write_bytes(manifest_raw)
    (target / "READY").write_text(_sha(manifest_raw) + "\n", encoding="ascii")
    return verify_resolve_score_artifact(target, code_revision=code_revision)


def verify_resolve_score_artifact(
    path: Path, *, code_revision: str | None = None,
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "resolve_score_manifest")
    raw_manifest = canonical(manifest)
    ready = _read(root / "READY", 1024).decode("ascii").strip()
    if (
        manifest.get("schema_version") != ARTIFACT_SCHEMA
        or manifest.get("config_digest") != CONFIG_DIGEST
        or ready != _sha(raw_manifest)
        or (code_revision is not None and manifest.get("code_revision") != code_revision)
    ):
        raise OrderRuleResolveScoreError("resolve_score_manifest_identity")
    expected_top = {"review-decisions.tsv", "score.json", "parent.json", "summary.json"}
    expected_nested = {"reference-bundle/" + name for name in BUNDLE_FILES}
    expected = expected_top | expected_nested
    actual = {
        str(item.relative_to(root)) for item in root.rglob("*")
        if item.is_file() and str(item.relative_to(root)) not in {"manifest.json", "READY"}
    }
    if actual != expected or set(manifest.get("files", {})) != expected:
        raise OrderRuleResolveScoreError("resolve_score_inventory")
    for name in sorted(expected):
        if _sha(_read(root / name)) != manifest["files"][name]:
            raise OrderRuleResolveScoreError("resolve_score_file_digest:" + name)
    config = load_config()
    prepare = config["prepare"]
    training_root = Path("/home/qiuyu/quanthus-data/training").resolve()
    prepared = (training_root / prepare["artifact_relative_path"]).resolve()
    verified_prepare = verify_prepare_artifact(prepared, code_revision=prepare["code_revision"])
    if (
        verified_prepare["manifest_sha256"] != prepare["manifest_sha256"]
        or verified_prepare["selection_digest"] != prepare["selection_digest"]
        or verified_prepare["evidence_digest"] != prepare["evidence_digest"]
    ):
        raise OrderRuleResolveScoreError("resolve_score_parent_verification")
    decisions_raw, decisions = _parse_decisions(find_project_root(), config)
    if decisions_raw != _read(root / "review-decisions.tsv", 512 * 1024):
        raise OrderRuleResolveScoreError("resolve_score_review_ledger_differs")
    evidence = _object(prepared / "evidence.json", "prepared_evidence")
    selection = _object(prepared / "selection.json", "prepared_selection")
    build_opinions(evidence, decisions, config)
    bundle = root / "reference-bundle"
    bundle_receipt = verify_bundle(bundle)
    score = score_sealed_bundle(
        bundle, selection,
        expected_manifest=bundle_receipt["manifest_sha256"],
        expected_selection=prepare["selection_digest"],
    )
    stored_score = _object(root / "score.json", "stored_score")
    if canonical(score) != canonical(stored_score):
        raise OrderRuleResolveScoreError("resolve_score_reconstruction")
    stored_summary = _object(root / "summary.json", "stored_summary")
    if (
        stored_summary.get("schema_version") != SUMMARY_SCHEMA
        or stored_summary.get("reference_manifest_sha256") != bundle_receipt["manifest_sha256"]
        or stored_summary.get("opinion_digest") != score["opinion_digest"]
        or stored_summary.get("case_count") != 160
        or stored_summary.get("opinion_count") != 320
        or stored_summary.get("independent_validation") is not False
        or stored_summary.get("full_context_blinding_claimed") is not False
        or stored_summary.get("official_labels_authorized") is not False
        or stored_summary.get("parameter_search") is not False
        or stored_summary.get("new_market_data_reads") != 0
        or stored_summary.get("opinion_regeneration") is not False
    ):
        raise OrderRuleResolveScoreError("resolve_score_summary_claim")
    return {
        "verified": True,
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(expected),
        "case_count": 160,
        "opinion_count": 320,
        "reference_manifest_sha256": bundle_receipt["manifest_sha256"],
        "primary_reference_counts": stored_summary["primary_reference_counts"],
        "gate_calculation": score["gate_calculation"],
        "benchmark": score["benchmark"],
        "panels": score["panels"],
        "quarters": score["quarters"],
        "primary_disagreement_count": score["primary_disagreement_count"],
        "joint_primary_unknown_count": score["joint_primary_unknown_count"],
        "recent_disagreement_count": score["recent_disagreement_count"],
        "independent_validation": False,
        "full_context_blinding_claimed": False,
        "official_labels_authorized": False,
        "runtime_activation_authorized": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    run = command.add_parser("run")
    run.add_argument("--config", type=Path, default=find_project_root() / DEFAULT_CONFIG)
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = command.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = (
        run_resolve_score(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
        if args.command == "run"
        else verify_resolve_score_artifact(args.artifact, code_revision=args.code_revision)
    )
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
