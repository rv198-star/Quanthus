"""P6-GOV-002 composite Forward Worker contract and disabled rehearsal runtime.

The worker path is deliberately executable before activation, but the only admitted mode in this
work item is a zero-data, zero-side-effect disabled rehearsal.  Official prospective execution is
opened only by later P6-PAPER/P6-WEB/P6-ACTIVATE gates.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.platform.jobs import input_digest

_CONFIG_SCHEMA = "quanthus.p6-forward-worker.v1"
_INPUT_SCHEMA = "quanthus.p6-forward-cycle-input.v1"
_RUN_SCHEMA = "quanthus.p6-forward-run.v1"
_REHEARSAL_SCHEMA = "quanthus.p6-disabled-rehearsal.v1"
_JOB_KIND = "quanthus.p6-forward-cycle.v1"
_ENTRY_DIGEST = "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18"
_STAGE_GRAPH = (
    "verify_frozen_bindings_and_activation",
    "verify_and_bind_one_dwd_release",
    "build_one_shared_effective_view_projection",
    "settle_eligible_prior_paper_intents",
    "produce_outcome_blind_expert_observations_and_aggregate_decision",
    "append_shadow_record_without_execution_edge",
    "project_aggregate_decision_to_isolated_paper_intent",
    "settle_matured_forecast_targets",
    "build_role_specific_scorecards_and_loss_attribution",
    "seal_immutable_forward_run",
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("invalid_json", str(path)) from exc
    if not isinstance(value, dict):
        fail("field_mismatch", f"{path} must contain one object")
    return value


def _repo_binding(root: Path, value: object, expected_sha: object, field: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        fail("path_escape", f"{field}.path must be repository relative")
    path = (root / value).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        fail("binding_missing", field)
    if expected_sha != _sha(path):
        fail("binding_digest_drift", field)
    return path


@dataclass(frozen=True)
class P6ForwardWorkerConfig:
    path: Path
    config_sha256: str
    entry_proposal_sha256: str
    data_policy_sha256: str
    scorecard_policy_sha256: str
    stage_graph: tuple[str, ...]


@dataclass(frozen=True)
class ForwardCycleJobInput:
    rehearsal_id: str
    entry_proposal_digest: str
    data_policy_sha256: str
    scorecard_policy_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _INPUT_SCHEMA,
            "mode": "disabled_rehearsal",
            "rehearsal_id": self.rehearsal_id,
            "entry_proposal_digest": self.entry_proposal_digest,
            "data_policy_sha256": self.data_policy_sha256,
            "scorecard_policy_sha256": self.scorecard_policy_sha256,
            "activation_decision": None,
            "dwd_release": None,
            "prior_paper_ledger_head": None,
        }

    @property
    def input_digest(self) -> str:
        return input_digest(self.to_dict())

    @property
    def idempotency_key(self) -> str:
        return f"p6-disabled-rehearsal:{self.input_digest[7:39]}"

    @property
    def concurrency_key(self) -> str:
        return "p6-forward-cycle:disabled-rehearsal"

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ForwardCycleJobInput:
        expected = {
            "schema_version",
            "mode",
            "rehearsal_id",
            "entry_proposal_digest",
            "data_policy_sha256",
            "scorecard_policy_sha256",
            "activation_decision",
            "dwd_release",
            "prior_paper_ledger_head",
        }
        if set(value) != expected or value.get("schema_version") != _INPUT_SCHEMA:
            fail("field_mismatch", "forward cycle input fields differ")
        if value.get("mode") != "disabled_rehearsal":
            fail("official_cycle_not_authorized", "P6-GOV-002 admits only disabled rehearsal")
        if any(value.get(name) is not None for name in (
            "activation_decision", "dwd_release", "prior_paper_ledger_head"
        )):
            fail("rehearsal_side_effect_input_forbidden", "disabled rehearsal cannot carry runtime inputs")
        rehearsal_id = value.get("rehearsal_id")
        if not isinstance(rehearsal_id, str) or not rehearsal_id or rehearsal_id.strip() != rehearsal_id:
            fail("field_mismatch", "rehearsal_id must be canonical text")
        entry = value.get("entry_proposal_digest")
        data = value.get("data_policy_sha256")
        score = value.get("scorecard_policy_sha256")
        for name, item in (("entry", entry), ("data", data), ("score", score)):
            if not isinstance(item, str) or not item.startswith("sha256:") or len(item) != 71:
                fail("invalid_digest", name)
        return cls(rehearsal_id, str(entry), str(data), str(score))


@dataclass(frozen=True)
class ForwardRun:
    root: Path
    run_id: str
    input_digest: str
    manifest_digest: str
    summary: Mapping[str, Any]


def load_forward_worker_config(path: Path, *, root: Path) -> P6ForwardWorkerConfig:
    raw = _load(path)
    if (
        raw.get("schema_version") != _CONFIG_SCHEMA
        or raw.get("work_item") != "P6-GOV-002"
        or raw.get("state") != "FROZEN_DISABLED_REHEARSAL_ONLY"
    ):
        fail("forward_worker_config_drift", "forward worker config identity differs")
    bindings: dict[str, str] = {}
    for key in ("entry_proposal", "data_boundary", "scorecard_policy"):
        value = raw.get(key)
        if not isinstance(value, dict):
            fail("field_mismatch", f"{key} must be object")
        bindings[key] = _sha(_repo_binding(root, value.get("path"), value.get("file_sha256"), key))
    if raw["entry_proposal"].get("proposal_digest") != _ENTRY_DIGEST:
        fail("forward_worker_config_drift", "entry proposal semantic digest differs")
    job = raw.get("job")
    if not isinstance(job, dict) or (
        job.get("kind") != _JOB_KIND
        or job.get("queue") != "replay"
        or job.get("existing_replay_worker") is not True
        or job.get("new_queue_authorized") is not False
        or job.get("new_transport_authorized") is not False
        or job.get("worker_registry_authorized") is not False
        or job.get("redis_celery_rabbitmq_authorized") is not False
        or job.get("concurrency") != 1
    ):
        fail("forward_worker_config_drift", "worker topology differs")
    stage_graph = raw.get("stage_graph")
    if tuple(stage_graph) != _STAGE_GRAPH if isinstance(stage_graph, list) else True:
        fail("forward_worker_config_drift", "stage graph differs")
    rehearsal = raw.get("rehearsal")
    if not isinstance(rehearsal, dict) or rehearsal != {
        "mode": "disabled_rehearsal",
        "allowed": True,
        "requires_dwd_read": False,
        "official_evidence": False,
        "shadow_side_effect": False,
        "paper_side_effect": False,
        "settlement_side_effect": False,
        "scorecard_side_effect": False,
        "purpose": "Exercise the real Replay Worker child/seal/recovery/fencing path while admission remains disabled.",
    }:
        fail("forward_worker_config_drift", "disabled rehearsal semantics differ")
    official = raw.get("official_cycle")
    if not isinstance(official, dict) or (
        official.get("implemented_by_this_work_item") is not False
        or official.get("requires_enabled_activation_decision") is not True
        or official.get("activation_owner") != "P6-ACTIVATE-001"
        or official.get("historical_backfill_as_official") is not False
    ):
        fail("activation_authority_drift", "official-cycle boundary differs")
    authority = raw.get("authority")
    if not isinstance(authority, dict) or (
        authority.get("forward_worker_implementation_authorized") is not True
        or authority.get("disabled_rehearsal_authorized") is not True
    ):
        fail("authority_drift", "forward worker implementation authority missing")
    if any(
        value is not False
        for key, value in authority.items()
        if key not in {"forward_worker_implementation_authorized", "disabled_rehearsal_authorized"}
    ):
        fail("authority_drift", "forward worker config grants downstream authority")
    return P6ForwardWorkerConfig(
        path=path,
        config_sha256=_sha(path),
        entry_proposal_sha256=bindings["entry_proposal"],
        data_policy_sha256=bindings["data_boundary"],
        scorecard_policy_sha256=bindings["scorecard_policy"],
        stage_graph=_STAGE_GRAPH,
    )


def validate_forward_cycle_input(
    value: Mapping[str, object], config: P6ForwardWorkerConfig
) -> ForwardCycleJobInput:
    parsed = ForwardCycleJobInput.from_dict(value)
    if parsed.entry_proposal_digest != _ENTRY_DIGEST:
        fail("binding_digest_drift", "forward input binds another entry proposal")
    if parsed.data_policy_sha256 != config.data_policy_sha256:
        fail("binding_digest_drift", "forward input binds another data policy")
    if parsed.scorecard_policy_sha256 != config.scorecard_policy_sha256:
        fail("binding_digest_drift", "forward input binds another scorecard policy")
    return parsed


def make_disabled_rehearsal_input(
    *, rehearsal_id: str, config: P6ForwardWorkerConfig
) -> ForwardCycleJobInput:
    return ForwardCycleJobInput(
        rehearsal_id=rehearsal_id,
        entry_proposal_digest=_ENTRY_DIGEST,
        data_policy_sha256=config.data_policy_sha256,
        scorecard_policy_sha256=config.scorecard_policy_sha256,
    )


def _run_id(input_digest_value: str) -> str:
    return f"p6_run_{input_digest_value[7:31]}"


def _validate_run_root(path: Path, run_root: Path) -> None:
    root = run_root.resolve()
    resolved = path.resolve()
    if resolved.parent != root:
        raise RuntimeError("forward_run_artifact_path_escape")


def verify_forward_run(
    path: Path, *, expected_input_digest: str | None = None
) -> ForwardRun:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        rehearsal = json.loads((path / "rehearsal.json").read_text(encoding="utf-8"))
        ready = (path / "READY").read_text(encoding="ascii").strip()
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("forward_run_unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != _RUN_SCHEMA:
        raise RuntimeError("forward_run_manifest_mismatch")
    if not isinstance(rehearsal, dict) or rehearsal.get("schema_version") != _REHEARSAL_SCHEMA:
        raise RuntimeError("forward_run_rehearsal_mismatch")
    if ready != _sha(path / "manifest.json"):
        raise RuntimeError("forward_run_ready_mismatch")
    if manifest.get("rehearsal_sha256") != _sha(path / "rehearsal.json"):
        raise RuntimeError("forward_run_rehearsal_checksum_mismatch")
    if expected_input_digest is not None and manifest.get("input_digest") != expected_input_digest:
        raise RuntimeError("forward_run_input_mismatch")
    summary = manifest.get("summary")
    if not isinstance(summary, dict) or summary != rehearsal.get("summary"):
        raise RuntimeError("forward_run_summary_mismatch")
    if (
        summary.get("mode") != "disabled_rehearsal"
        or summary.get("official_evidence") is not False
        or summary.get("dwd_read_count") != 0
        or summary.get("shadow_write_count") != 0
        or summary.get("paper_write_count") != 0
        or summary.get("settlement_write_count") != 0
        or summary.get("scorecard_write_count") != 0
    ):
        raise RuntimeError("forward_run_authority_leak")
    return ForwardRun(
        path.resolve(),
        str(manifest.get("run_id")),
        str(manifest.get("input_digest")),
        _sha(path / "manifest.json"),
        MappingProxyType(dict(summary)),
    )


def find_forward_run_for_input(run_root: Path, input_digest_value: str) -> ForwardRun | None:
    if not run_root.exists():
        return None
    matches: list[ForwardRun] = []
    for path in sorted(run_root.iterdir()):
        if not path.is_dir() or not path.name.startswith("p6_run_"):
            raise RuntimeError("forward_run_root_inventory_invalid")
        run = verify_forward_run(path)
        if run.input_digest == input_digest_value:
            matches.append(run)
    if len(matches) > 1:
        raise RuntimeError("non_deterministic_forward_run")
    return matches[0] if matches else None


def run_disabled_rehearsal(
    *,
    job_input: ForwardCycleJobInput,
    config: P6ForwardWorkerConfig,
    staging: Path,
    run_root: Path,
) -> ForwardRun:
    canonical = job_input.to_dict()
    validated = validate_forward_cycle_input(canonical, config)
    input_digest_value = validated.input_digest
    existing = find_forward_run_for_input(run_root, input_digest_value)
    if existing is not None:
        return existing
    run_id = _run_id(input_digest_value)
    final = run_root.resolve() / run_id
    if final.exists():
        return verify_forward_run(final, expected_input_digest=input_digest_value)
    work = staging.resolve()
    root = run_root.resolve()
    if work == root or root in work.parents:
        # Staging must be outside the final run inventory so recovery scans cannot see partial runs.
        pass
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    stage_status = {
        config.stage_graph[0]: "PASS_BINDINGS_FROZEN_ACTIVATION_DISABLED",
        **{stage: "BLOCKED_ACTIVATION_DISABLED" for stage in config.stage_graph[1:-1]},
        config.stage_graph[-1]: "PASS_REHEARSAL_ARTIFACT_SEALED",
    }
    summary = {
        "mode": "disabled_rehearsal",
        "rehearsal_id": validated.rehearsal_id,
        "official_evidence": False,
        "dwd_read_count": 0,
        "shared_projection_build_count": 0,
        "shadow_write_count": 0,
        "paper_write_count": 0,
        "settlement_write_count": 0,
        "scorecard_write_count": 0,
        "notification_count": 0,
        "broker_action_count": 0,
        "live_capital_action_count": 0,
        "stage_status": stage_status,
    }
    rehearsal = {
        "schema_version": _REHEARSAL_SCHEMA,
        "job_kind": _JOB_KIND,
        "input": canonical,
        "worker_config_sha256": config.config_sha256,
        "summary": summary,
    }
    (work / "rehearsal.json").write_bytes(canonical_json_bytes(rehearsal) + b"\n")
    manifest = {
        "schema_version": _RUN_SCHEMA,
        "run_id": run_id,
        "input_digest": input_digest_value,
        "worker_config_sha256": config.config_sha256,
        "rehearsal_sha256": _sha(work / "rehearsal.json"),
        "summary": summary,
    }
    (work / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    (work / "READY").write_text(_sha(work / "manifest.json") + "\n", encoding="ascii")
    root.mkdir(parents=True, exist_ok=True)
    if final.exists():
        raise RuntimeError("immutable forward Run already exists")
    os.replace(work, final)
    _validate_run_root(final, root)
    return verify_forward_run(final, expected_input_digest=input_digest_value)
