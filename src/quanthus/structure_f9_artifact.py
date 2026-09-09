"""Deep verifier for bounded Structure F9 support-failure/rebound artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.structure_support_facts import (
    build_support_failure_facts,
    project_stage_10_11,
    support_fact_spec,
    support_report_counts,
    support_review_sample,
)
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import (
    DailyInput,
    InputPolicy,
    admit_daily,
    build_readiness,
    calculation_spec,
    canonical,
    digest,
)

FILES = frozenset(
    {
        "input-readiness.json",
        "source-inputs.jsonl",
        "support-facts.json",
        "projection.json",
        "summary.json",
        "runtime.json",
    }
)
MANIFEST_SCHEMA = "quanthus.structure-f9-artifact.v1"


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _row(raw: dict[str, Any]) -> DailyInput:
    if set(raw) != {field.name for field in fields(DailyInput)}:
        raise ValueError("F9 source-member schema differs")
    data = dict(raw)
    data["market_date"] = date.fromisoformat(data["market_date"])
    data["cutoff"] = datetime.fromisoformat(data["cutoff"])
    for name in ("high", "low", "close", "volume"):
        data[name] = None if data[name] is None else Decimal(data[name])
    return DailyInput(**data)


def verify_structure_f9_artifact(
    root: Path, *, code_revision: str, request_digest: str
) -> dict[str, Any]:
    root = root.resolve()
    expected_files = FILES | {"manifest.json", "READY"}
    if not root.is_dir() or {path.name for path in root.iterdir()} != expected_files:
        raise ValueError("F9 artifact file set differs")
    if any(path.is_symlink() or not path.is_file() for path in root.iterdir()):
        raise ValueError("F9 artifact contains indirect or non-file members")
    if sum(path.stat().st_size for path in root.iterdir()) > 64 * 1024 * 1024:
        raise ValueError("F9 bounded verification byte budget exceeded")

    manifest = _load(root / "manifest.json")
    ready = _load(root / "READY")
    expected_manifest_keys = {
        "schema_version",
        "code_revision",
        "request_digest",
        "research_only",
        "execution_authorized",
        "base_structure_digest",
        "support_facts_digest",
        "projection_digest",
        "input_readiness_digest",
        "result_digest",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_manifest_keys:
        raise ValueError("F9 manifest schema differs")
    if (
        manifest["schema_version"] != MANIFEST_SCHEMA
        or manifest["code_revision"] != code_revision
        or manifest["request_digest"] != request_digest
        or manifest["research_only"] is not True
        or manifest["execution_authorized"] is not False
        or set(manifest["files"]) != FILES
    ):
        raise ValueError("F9 manifest authority or identity differs")
    manifest_sha = "sha256:" + hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if ready != {
        "schema_version": "quanthus.structure-f9-ready.v1",
        "manifest_sha256": manifest_sha,
    }:
        raise ValueError("F9 READY binding differs")
    for name in FILES:
        observed = "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
        if manifest["files"].get(name) != observed:
            raise ValueError(f"F9 artifact file hash differs: {name}")

    summary = _load(root / "summary.json")
    expected_summary_keys = {
        "schema_version",
        "request",
        "code_revision",
        "research_only",
        "execution_authorized",
        "economic_claim",
        "knowledge_view",
        "base_structure_digest",
        "support_facts_digest",
        "projection_digest",
        "input_readiness_digest",
        "report_counts",
        "review_sample",
        "claim_limits",
    }
    if not isinstance(summary, dict) or set(summary) != expected_summary_keys:
        raise ValueError("F9 summary schema differs")
    request = summary["request"]
    if (
        summary["schema_version"] != "quanthus.structure-f9-result.v1"
        or digest(request) != request_digest
        or summary["code_revision"] != code_revision
        or summary["research_only"] is not True
        or summary["execution_authorized"] is not False
        or summary["economic_claim"] is not False
        or summary["knowledge_view"] != "retrospective"
        or digest(summary) != manifest["result_digest"]
    ):
        raise ValueError("F9 summary scope or semantic digest differs")

    readiness = _load(root / "input-readiness.json")
    if digest(readiness) != manifest["input_readiness_digest"]:
        raise ValueError("F9 source readiness digest differs")
    policy = InputPolicy(
        digest(calculation_spec(request)),
        tuple(request["security_codes"]),
        readiness["calendar_ref"],
    )
    source_lines = (root / "source-inputs.jsonl").read_text(encoding="utf-8").splitlines()
    if len(source_lines) > 5000:
        raise ValueError("F9 source-row verification budget exceeded")
    source = tuple(_row(json.loads(line)) for line in source_lines)
    admissions = tuple(admit_daily(item, policy) for item in source)
    rebuilt_readiness = build_readiness(
        admissions,
        policy,
        readiness["provider_identity"],
        report_start=date.fromisoformat(request["report_start"]),
        report_end=date.fromisoformat(request["report_end"]),
    )
    if canonical(rebuilt_readiness) != canonical(readiness):
        raise ValueError("F9 readiness denominator or admission differs")

    structure = build_structure_v2(
        admissions,
        spec_digest=digest(calculation_spec(request)),
        calendar_ref=policy.calendar_ref,
        lookbacks=tuple(request["lookbacks"]),
    )
    if structure.digest != manifest["base_structure_digest"]:
        raise ValueError("F9 base Structure reconstruction differs")
    facts = build_support_failure_facts(
        admissions,
        structure,
        spec_digest=digest(support_fact_spec(request)),
        maximum_sessions=int(request["support_horizon_sessions"]),
    )
    if (
        facts.digest != manifest["support_facts_digest"]
        or canonical(facts.semantic()) != (root / "support-facts.json").read_bytes()
    ):
        raise ValueError("F9 support facts reconstruction differs")

    projection = project_stage_10_11(
        facts,
        persistence_sessions=int(request["projection"]["stage_10_persistence_sessions"]),
        failure_depth_abs_min=Decimal(request["projection"]["stage_10_failure_depth_abs_min"]),
        rebound_min=Decimal(request["projection"]["stage_11_rebound_min"]),
    )
    projection_payload = [item.to_dict() for item in projection]
    if (
        digest(projection_payload) != manifest["projection_digest"]
        or canonical(projection_payload) != (root / "projection.json").read_bytes()
    ):
        raise ValueError("F9 research projection reconstruction differs")
    if canonical(support_report_counts(facts, projection)) != canonical(summary["report_counts"]):
        raise ValueError("F9 report counts differ from reconstructed facts")
    expected_sample = support_review_sample(
        facts,
        projection,
        limit=int(request["review_sample_limit"]),
        seed=digest(request),
    )
    if canonical(expected_sample) != canonical(summary["review_sample"]):
        raise ValueError("F9 deterministic review sample differs")

    # Preserve the key semantic boundary: Stage 11 must reference an earlier Stage-10 confirmation.
    by_process: dict[str, list[Any]] = {}
    for item in projection:
        by_process.setdefault(item.process_id, []).append(item)
    for rows in by_process.values():
        known_ref = None
        for item in sorted(rows, key=lambda value: value.market_date):
            if item.prior_stage_10_confirmation_ref is not None:
                known_ref = item.prior_stage_10_confirmation_ref
            if item.stage_11_active and (
                known_ref is None or item.prior_stage_10_confirmation_ref != known_ref
            ):
                raise ValueError("F9 Stage 11 lacks prior immutable Stage-10 confirmation")

    runtime = _load(root / "runtime.json")
    if (
        runtime.get("schema_version") != "quanthus.structure-f9-runtime.v1"
        or runtime.get("provider_builds") != 1
        or runtime.get("cross_job_cache_hits") != 0
        or runtime.get("provider_lifecycle") != "job_local_TMP"
    ):
        raise ValueError("F9 runtime build accounting differs")
    return {
        "artifact_ref": str(root),
        "manifest_sha256": manifest_sha,
        "result_digest": manifest["result_digest"],
        "base_structure_digest": structure.digest,
        "support_facts_digest": facts.digest,
        "projection_digest": manifest["projection_digest"],
        "input_readiness_digest": manifest["input_readiness_digest"],
        "knowledge_view": "retrospective",
        "row_count": readiness["attempted"],
        "input_counts": {
            key: readiness[key] for key in ("attempted", "admitted", "rejected")
        },
        "fact_counts": summary["report_counts"],
        "review_sample": summary["review_sample"],
        "runtime": runtime,
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "execution_authorized": False,
        "official_label": False,
    }
