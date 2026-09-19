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
from quanthus.forward_validation.reproduction import (
    OfficialCycleReproductionRun,
    find_official_reproduction_for_input,
    verify_official_reproduction,
)
from quanthus.forward_validation.runner import (
    ForwardRun,
    find_forward_run_for_input,
    verify_forward_run,
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

P6_FORWARD_STAGE_PROGRESS = {
    "binding-inputs": 10,
    "building-prospective-projection": 30,
    "producing-official-decisions": 55,
    "projecting-paper-intents": 75,
    "official-cycle-sealed": 98,
    "official-reproduction-sealed": 98,
    "rehearsal-sealed": 98,
}


@dataclass(frozen=True)
class ExpertChildResult:
    run: ExpertEvaluationRun
    evaluation_identities: tuple[dict[str, Any], ...]
    strategy_identity: dict[str, Any] | None
    summary: dict[str, Any]


@dataclass(frozen=True)
class P6ForwardChildResult:
    run: ForwardRun | OfficialCycleReproductionRun
    summary: dict[str, Any]


def p6_forward_child_command(
    *,
    input_path: Path,
    config_path: Path,
    runtime_path: Path,
    staging: Path,
    run_root: Path,
) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "quanthus.forward_validation.cli",
        "--input",
        str(input_path),
        "--config",
        str(config_path),
        "--runtime-config",
        str(runtime_path),
        "--staging",
        str(staging),
        "--run-root",
        str(run_root),
    )


def validate_p6_forward_child_result(payload: object, run_root: Path) -> P6ForwardChildResult:
    if not isinstance(payload, dict) or set(payload) != {
        "run_id",
        "artifact_ref",
        "manifest_digest",
        "input_digest",
        "summary",
    }:
        raise RuntimeError("p6_forward_child_result_invalid")
    if any(
        not isinstance(payload[name], str)
        for name in ("run_id", "artifact_ref", "manifest_digest", "input_digest")
    ):
        raise RuntimeError("p6_forward_child_result_invalid")
    configured_root = run_root.resolve()
    artifact = Path(str(payload["artifact_ref"])).resolve()
    if artifact.parent != configured_root:
        raise RuntimeError("p6_forward_child_artifact_path_escape")
    summary = payload["summary"]
    if not isinstance(summary, dict):
        raise RuntimeError("p6_forward_child_manifest_mismatch")
    if summary.get("mode") == "official_reproduction":
        run = verify_official_reproduction(
            artifact,
            expected_input_digest=str(payload["input_digest"]),
        )
    else:
        run = verify_forward_run(
            artifact,
            expected_input_digest=str(payload["input_digest"]),
        )
    if run.run_id != payload["run_id"] or run.manifest_digest != payload["manifest_digest"]:
        raise RuntimeError("p6_forward_child_manifest_mismatch")
    if dict(run.summary) != summary:
        raise RuntimeError("p6_forward_child_manifest_mismatch")
    return P6ForwardChildResult(run, summary)


def find_p6_forward_run_for_input(run_root: Path, input_digest: str) -> P6ForwardChildResult | None:
    run = find_forward_run_for_input(run_root, input_digest)
    if run is None:
        return None
    return P6ForwardChildResult(run, dict(run.summary))


def find_p6_forward_reproduction_for_input(
    run_root: Path, input_digest: str
) -> P6ForwardChildResult | None:
    run = find_official_reproduction_for_input(run_root, input_digest)
    if run is None:
        return None
    return P6ForwardChildResult(run, dict(run.summary))


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
