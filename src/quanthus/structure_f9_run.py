"""Fixed-input real-DWD F9 support-failure/rebound facts qualification runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import time
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.backtest.data_provider import load_replay_data_provider_config
from quanthus.backtest.direct_dwd_provider import open_bounded_direct_dwd_session
from quanthus.policy import find_project_root
from quanthus.structure_f9_artifact import FILES, MANIFEST_SCHEMA, verify_structure_f9_artifact
from quanthus.structure_support_facts import (
    SUPPORT_ROLE,
    SupportFactsResult,
    build_support_failure_facts,
    project_stage_10_11,
    support_fact_spec,
    support_report_counts,
    support_review_sample,
)
from quanthus.structure_v2 import StructureV2Result, build_structure_v2
from quanthus.structure_v2_inputs import (
    Admission,
    InputPolicy,
    build_readiness,
    calculation_spec,
    canonical,
    digest,
)
from quanthus.structure_v2_run import _stable_provider, diagnosed_stage, extract_admissions

REQUEST_PATH = "config/structure-f9-run.development.v1.json"
SCHEMA = "quanthus.structure-f9-run.v1"


def load_request(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    keys = {
        "schema_version",
        "provider_config",
        "scan_start",
        "report_start",
        "report_end",
        "security_codes",
        "lookbacks",
        "support_horizon_sessions",
        "support_role",
        "knowledge_view",
        "coordinate_policy",
        "projection",
        "review_sample_limit",
        "maximum_wall_seconds",
        "cold_result_job_budget",
        "research_only",
        "execution_authorized",
        "economic_claim",
        "sealed_partition_access_authorized",
        "permanent_blind_access_authorized",
    }
    if not isinstance(raw, dict) or set(raw) != keys or raw.get("schema_version") != SCHEMA:
        raise ValueError("F9 fixed request schema differs")
    fixed = {
        "provider_config": "config/replay-data-provider.structure-v2.development.v1.json",
        "lookbacks": [20, 60],
        "support_horizon_sessions": 60,
        "support_role": SUPPORT_ROLE,
        "knowledge_view": "retrospective",
        "coordinate_policy": "raw-cny-action-epoch-reset-v1",
        "review_sample_limit": 12,
        "maximum_wall_seconds": 1200,
        "cold_result_job_budget": 2,
        "research_only": True,
        "execution_authorized": False,
        "economic_claim": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
    }
    if any(
        raw.get(key) != value or type(raw.get(key)) is not type(value)
        for key, value in fixed.items()
    ):
        raise ValueError("F9 frozen input policy differs")
    projection = raw.get("projection")
    expected_projection = {
        "stage_10_persistence_sessions": 5,
        "stage_10_failure_depth_abs_min": "0.02",
        "stage_11_rebound_min": "0.05",
        "official_label": False,
        "strategy_consumption_authorized": False,
    }
    if projection != expected_projection:
        raise ValueError("F9 research projection contract differs")
    codes = raw.get("security_codes")
    if (
        not isinstance(codes, list)
        or not codes
        or len(codes) > 3
        or len(set(codes)) != len(codes)
        or any(
            not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None
            for code in codes
        )
    ):
        raise ValueError("F9 bounded securities invalid")
    start, first, last = (
        date.fromisoformat(raw[key]) for key in ("scan_start", "report_start", "report_end")
    )
    if not start <= first <= last or (last - start).days > 365:
        raise ValueError("F9 bounded date scope invalid")
    return raw


def run_from_admissions(
    request: dict[str, Any],
    admissions: tuple[Admission, ...],
    policy: InputPolicy,
    provider_identity: dict[str, Any],
    *,
    code_revision: str,
) -> tuple[
    dict[str, Any],
    StructureV2Result,
    SupportFactsResult,
    tuple[Any, ...],
    dict[str, Any],
    list[dict[str, Any]],
]:
    stages: list[dict[str, Any]] = []
    first, last = (date.fromisoformat(request[key]) for key in ("report_start", "report_end"))
    started = time.perf_counter()
    readiness = build_readiness(
        admissions,
        policy,
        _stable_provider(provider_identity),
        report_start=first,
        report_end=last,
    )
    if not readiness["admitted"]:
        raise ValueError("F9 real input slice has no admissible observations")
    stages.append(
        {
            "stage": "B2-admission",
            "elapsed_seconds": time.perf_counter() - started,
            "input_rows": len(admissions),
            "output_rows": readiness["admitted"],
        }
    )
    started = time.perf_counter()
    structure = build_structure_v2(
        admissions,
        spec_digest=digest(calculation_spec(request)),
        calendar_ref=policy.calendar_ref,
        lookbacks=tuple(request["lookbacks"]),
    )
    stages.append(
        {
            "stage": "B3-B5-base-structure",
            "elapsed_seconds": time.perf_counter() - started,
            "input_rows": len(admissions),
            "output_rows": len(structure.episodes),
        }
    )
    started = time.perf_counter()
    facts = build_support_failure_facts(
        admissions,
        structure,
        spec_digest=digest(support_fact_spec(request)),
        maximum_sessions=int(request["support_horizon_sessions"]),
    )
    stages.append(
        {
            "stage": "B5-F9-support-process",
            "elapsed_seconds": time.perf_counter() - started,
            "input_rows": len(structure.episodes),
            "output_rows": len(facts.observations),
        }
    )
    started = time.perf_counter()
    projection = project_stage_10_11(
        facts,
        persistence_sessions=int(request["projection"]["stage_10_persistence_sessions"]),
        failure_depth_abs_min=Decimal(request["projection"]["stage_10_failure_depth_abs_min"]),
        rebound_min=Decimal(request["projection"]["stage_11_rebound_min"]),
    )
    stages.append(
        {
            "stage": "research-projection-10-11",
            "elapsed_seconds": time.perf_counter() - started,
            "input_rows": len(facts.observations),
            "output_rows": len(projection),
        }
    )
    counts = support_report_counts(facts, projection)
    summary = {
        "schema_version": "quanthus.structure-f9-result.v1",
        "request": request,
        "code_revision": code_revision,
        "research_only": True,
        "execution_authorized": False,
        "economic_claim": False,
        "knowledge_view": "retrospective",
        "base_structure_digest": structure.digest,
        "support_facts_digest": facts.digest,
        "projection_digest": digest([item.to_dict() for item in projection]),
        "input_readiness_digest": digest(readiness),
        "report_counts": counts,
        "review_sample": support_review_sample(
            facts, projection, limit=int(request["review_sample_limit"]), seed=digest(request)
        ),
        "claim_limits": [
            "rolling-low is a preregistered support candidate, not proven universal key support",
            "one support crossing is not Stage 10",
            (
                "Stage 11 consumes a prior immutable Stage-10 confirmation ref while "
                "support remains unreclaimed"
            ),
            "projection is research-only and does not publish official labels or trading authority",
            "2022-2024 and 2025 are outside this fixed development slice",
        ],
    }
    return readiness, structure, facts, projection, summary, stages


def write_result(
    root: Path,
    request: dict[str, Any],
    admissions: tuple[Admission, ...],
    readiness: dict[str, Any],
    structure: StructureV2Result,
    facts: SupportFactsResult,
    projection: tuple[Any, ...],
    summary: dict[str, Any],
    runtime: dict[str, Any],
) -> None:
    root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "input-readiness.json": canonical(readiness),
        "source-inputs.jsonl": b"".join(canonical(asdict(item.row)) + b"\n" for item in admissions),
        "support-facts.json": canonical(facts.semantic()),
        "projection.json": canonical([item.to_dict() for item in projection]),
        "summary.json": canonical(summary),
        "runtime.json": canonical(runtime),
    }
    if set(payloads) != FILES:
        raise ValueError("F9 publication inventory differs")
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "code_revision": summary["code_revision"],
        "request_digest": digest(request),
        "research_only": True,
        "execution_authorized": False,
        "base_structure_digest": structure.digest,
        "support_facts_digest": facts.digest,
        "projection_digest": summary["projection_digest"],
        "input_readiness_digest": digest(readiness),
        "result_digest": digest(summary),
        "files": {
            name: "sha256:" + hashlib.sha256(value).hexdigest() for name, value in payloads.items()
        },
    }
    payloads["manifest.json"] = canonical(manifest)
    payloads["READY"] = canonical(
        {
            "schema_version": "quanthus.structure-f9-ready.v1",
            "manifest_sha256": "sha256:"
            + hashlib.sha256(payloads["manifest.json"]).hexdigest(),
        }
    )
    for name, data in payloads.items():
        with (root / name).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    if (
        Path(args.request).as_posix() != REQUEST_PATH
        or re.fullmatch(r"[0-9a-f]{40}", args.code_revision) is None
    ):
        raise ValueError("F9 runner requires the fixed request and exact code revision")
    root = find_project_root()
    request = load_request(root / REQUEST_PATH)

    def deadline(_signum: int, _frame: object) -> None:
        raise TimeoutError("structure_f9_wall_budget_exceeded")

    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(int(request["maximum_wall_seconds"]))
    config = load_replay_data_provider_config(root / request["provider_config"])
    first, last = (date.fromisoformat(request[key]) for key in ("scan_start", "report_end"))
    if config.scope.partition != "development":
        raise ValueError("F9 sealed/non-development scope forbidden")
    config.scope.authorize(first, last)
    started = time.perf_counter()
    with diagnosed_stage("f9-provider-open"):
        opened = open_bounded_direct_dwd_session(config, scope_start=first, scope_end=last)
    with opened as session:
        provider_seconds = time.perf_counter() - started
        extract_start = time.perf_counter()
        with diagnosed_stage("f9-source-extraction"):
            admissions, _frames, policy = extract_admissions(session, request)
        extract_seconds = time.perf_counter() - extract_start
        readiness, structure, facts, projection, summary, stages = run_from_admissions(
            request,
            admissions,
            policy,
            session.identity,
            code_revision=args.code_revision,
        )
        runtime = {
            "schema_version": "quanthus.structure-f9-runtime.v1",
            "provider_builds": 1,
            "cross_job_cache_hits": 0,
            "provider_lifecycle": "job_local_TMP",
            "stages": [
                {
                    "stage": "Direct-DWD-build-once",
                    "elapsed_seconds": provider_seconds,
                    "disposition": "built_once_shared_by_B2_B5_F9",
                },
                {
                    "stage": "source-member-extraction",
                    "elapsed_seconds": extract_seconds,
                    "output_rows": len(admissions),
                },
                *stages,
            ],
        }
        write_result(
            Path(args.output),
            request,
            admissions,
            readiness,
            structure,
            facts,
            projection,
            summary,
            runtime,
        )
    result = verify_structure_f9_artifact(
        Path(args.output), code_revision=args.code_revision, request_digest=digest(request)
    )
    signal.alarm(0)
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
