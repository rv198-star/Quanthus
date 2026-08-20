"""Static handler contracts shared by the PostgreSQL replay Worker supervisor."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.runner import (
    ExpertEvaluationJobInput,
    ExpertEvaluationRun,
    verify_expert_evaluation_run,
)

EXPERT_STAGE_PROGRESS = {
    "binding-inputs": 5,
    "building-shared-projection": 15,
    "producing-observations": 35,
    "settling-outcomes": 50,
    "building-scorecard": 70,
    "running-interventions": 80,
    "verifying-artifacts": 90,
    "publishing-run": 98,
}


@dataclass(frozen=True)
class ExpertChildResult:
    run: ExpertEvaluationRun
    evaluation_identities: tuple[dict[str, Any], ...]
    strategy_identity: dict[str, Any] | None
    summary: dict[str, Any]


def expert_child_command(
    *, input_path: Path, registry_root: Path, staging: Path, run_root: Path
) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "quanthus.expert_evaluation.cli",
        "--input",
        str(input_path),
        "--registry-root",
        str(registry_root),
        "--staging",
        str(staging),
        "--run-root",
        str(run_root),
    )


def validate_expert_child_result(payload: object, run_root: Path) -> ExpertChildResult:
    if not isinstance(payload, dict) or set(payload) != {
        "run_id",
        "artifact_ref",
        "manifest_digest",
        "evaluation_identities",
        "strategy_identity",
        "summary",
    }:
        raise RuntimeError("expert_child_result_invalid")
    if not isinstance(payload["run_id"], str) or not isinstance(payload["artifact_ref"], str):
        raise RuntimeError("expert_child_result_invalid")
    configured_root = run_root.resolve()
    artifact = Path(payload["artifact_ref"]).resolve()
    if configured_root not in artifact.parents or artifact.parent != configured_root:
        raise RuntimeError("expert_child_artifact_path_escape")
    run = verify_expert_evaluation_run(artifact, expected_run_id=payload["run_id"])
    if run.manifest_digest != payload["manifest_digest"]:
        raise RuntimeError("expert_child_manifest_mismatch")
    identities = run.manifest["evaluation_identities"]
    strategy = run.manifest["strategy_identity"]
    summary = run.manifest["summary"]
    if (
        payload["evaluation_identities"] != identities
        or payload["strategy_identity"] != strategy
        or payload["summary"] != summary
        or not isinstance(identities, list)
        or not identities
        or any(not isinstance(item, dict) for item in identities)
        or (strategy is not None and not isinstance(strategy, dict))
        or not isinstance(summary, dict)
    ):
        raise RuntimeError("expert_child_manifest_mismatch")
    return ExpertChildResult(run, tuple(identities), strategy, summary)


def find_expert_run_for_input(run_root: Path, input_digest: str) -> ExpertChildResult | None:
    if not run_root.exists():
        return None
    matches: list[ExpertChildResult] = []
    for path in sorted(run_root.iterdir()):
        if not path.is_dir() or not path.name.startswith("expert_run_"):
            raise RuntimeError("expert_run_root_inventory_invalid")
        run = verify_expert_evaluation_run(path, expected_run_id=path.name)
        job_input = ExpertEvaluationJobInput.from_dict(run.manifest["job_input"])
        if job_input.input_digest != input_digest:
            continue
        matches.append(
            validate_expert_child_result(
                {
                    "run_id": run.run_id,
                    "artifact_ref": str(run.root),
                    "manifest_digest": run.manifest_digest,
                    "evaluation_identities": run.manifest["evaluation_identities"],
                    "strategy_identity": run.manifest["strategy_identity"],
                    "summary": run.manifest["summary"],
                },
                run_root,
            )
        )
    if len(matches) > 1:
        raise RuntimeError("non_deterministic_expert_run")
    return matches[0] if matches else None
