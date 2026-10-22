# ruff: noqa: E501
"""Node-local P6 admission controller.

The controller performs no market analysis. It only binds a human-approved conditional mandate,
seeds the real disabled Replay Worker rehearsal, proves disabled admission/rollback semantics,
publishes one immutable activation decision, and submits exact READY daily-DWD envelopes to the
existing durable replay queue. All analytical work remains in the forward-cycle child process.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.forward_validation.activation import (
    P6ProspectiveRuntimeConfig,
    activation_decision_from_mapping,
    load_owner_activation_mandate,
    load_prospective_runtime_config,
    make_activation_decision_payload,
)
from quanthus.forward_validation.official_cycle import OfficialCycleJobInput, P6Runtime
from quanthus.forward_validation.reproduction import (
    OfficialCycleReproductionJobInput,
)
from quanthus.forward_validation.runner import (
    load_forward_worker_config,
    make_disabled_rehearsal_input,
    verify_forward_run,
)
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB, DurableJobQueue, input_digest
from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import PlatformStore

_EXTERNAL_MCP_ACTOR_USER_ID = "usr_18c14af81a06cf878f177579efec3727"
P6_CONTROLLER_DIAGNOSTIC_JOB = "quanthus.p6-controller-diagnostic.v1"
_MARKET_TZ = ZoneInfo("Asia/Shanghai")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{label}_not_object")
    return value


def _write_once(path: Path, payload: dict[str, object]) -> str:
    encoded = canonical_json_bytes(payload) + b"\n"
    digest = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise RuntimeError("immutable_p6_controller_artifact_conflict")
        return digest
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return digest


def _write_pointer(path: Path, payload: dict[str, object]) -> None:
    encoded = canonical_json_bytes(payload) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _actor_user_id(store: PlatformStore) -> str:
    mcp_actor = store.get_user(_EXTERNAL_MCP_ACTOR_USER_ID)
    # This is a fixed service identity, not an interactive operator session. Match the
    # production event-worker contract: existence is sufficient for durable Job attribution even
    # when interactive login for that identity has been disabled. Fallback human identities still
    # require an active operator/admin record below.
    if mcp_actor is not None:
        return mcp_actor.user_id
    eligible = tuple(
        sorted(
            (
                user
                for user in store.list_users()
                if user.active and user.role in {"operator", "admin"}
            ),
            key=lambda user: (
                0 if user.role == "operator" else 1,
                user.username.casefold(),
                user.user_id,
            ),
        )
    )
    if not eligible:
        raise RuntimeError("p6_controller_actor_unavailable")
    return eligible[0].user_id


def record_controller_error(
    store: PlatformStore,
    *,
    code_revision: str | None,
    error: Exception,
) -> str | None:
    """Persist the first controller error for one runtime revision without entering a worker queue."""

    identity = code_revision or "missing-runtime-revision"
    job_id = "job_" + hashlib.sha256(
        f"p6-controller-diagnostic:{identity}".encode()
    ).hexdigest()[:32]
    now = datetime.now(timezone.utc)
    input_ref = {
        "controller": "p6",
        "code_revision": code_revision,
    }
    try:
        store.create_job(
            JobRecord(
                job_id=job_id,
                kind=P6_CONTROLLER_DIAGNOSTIC_JOB,
                actor_user_id=_actor_user_id(store),
                idempotency_key=f"p6-controller-diagnostic-{identity[:32]}",
                input_ref=input_ref,
                state="failed",
                progress=100,
                cancel_requested=False,
                attempt=0,
                result_ref={"error_type": type(error).__name__},
                error_reason=f"{type(error).__name__}:{error}"[:500],
                created_at=now,
                updated_at=now,
                completed_at=now,
                queue="controller",
                input_digest=input_digest(input_ref),
                concurrency_key=f"p6-controller-diagnostic:{identity}",
                available_at=now,
                stage="controller-error",
                progress_detail={"terminal": True},
            )
        )
    except Exception:
        return None
    return job_id


def _platform_green() -> bool:
    for endpoint in ("health", "ready"):
        try:
            with urllib.request.urlopen(
                f"http://api:8080/{endpoint}",
                timeout=2,
            ) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        if endpoint == "health" and payload.get("status") != "healthy":
            return False
        if endpoint == "ready" and payload.get("status") != "ready":
            return False
    return True


def _mount_is_read_only(path: Path) -> bool:
    resolved = path.resolve()
    try:
        rows = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    matches: list[tuple[int, set[str], set[str]]] = []
    for row in rows:
        left, separator, right = row.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        right_fields = right.split()
        if len(fields) < 6 or len(right_fields) < 3:
            continue
        mount_point = Path(fields[4].replace("\\040", " ")).resolve()
        try:
            resolved.relative_to(mount_point)
        except ValueError:
            continue
        matches.append(
            (
                len(mount_point.parts),
                set(fields[5].split(",")),
                set(right_fields[2].split(",")),
            )
        )
    if not matches:
        return False
    _depth, mount_options, super_options = max(matches, key=lambda item: item[0])
    return "ro" in mount_options or "ro" in super_options


def _dwd_mounts_read_only(runtime: P6ProspectiveRuntimeConfig) -> bool:
    return all(
        _mount_is_read_only(path)
        for path in (
            runtime.historical_base_root,
            runtime.correction_root,
            runtime.daily_root,
            runtime.static_industry_mapping,
        )
    )


def _disabled_rehearsal_job(
    store: PlatformStore,
    *,
    runtime: P6Runtime,
    code_revision: str,
):
    config = load_forward_worker_config(runtime.bindings["forward_worker"], root=runtime.root)
    value = make_disabled_rehearsal_input(
        rehearsal_id=f"production-preactivation-{code_revision[:12]}",
        config=config,
    )
    queue = DurableJobQueue(store, {P6_FORWARD_CYCLE_JOB: "replay"})
    stable_job_id = "job_" + hashlib.sha256(
        f"p6-disabled:{code_revision}:{value.input_digest}".encode()
    ).hexdigest()[:32]
    return queue.submit(
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id=_actor_user_id(store),
        idempotency_key=f"p6-disabled-preactivation-{code_revision[:12]}",
        input_ref=value.to_dict(),
        concurrency_key=value.concurrency_key,
        requested_job_id=stable_job_id,
    )[0]


def _verified_rehearsal_manifest(job: Any, run_root: Path) -> str | None:
    if job.state != "succeeded":
        return None
    result = job.result_ref
    if not isinstance(result, dict):
        raise RuntimeError("p6_disabled_rehearsal_result_missing")
    artifact_ref = result.get("artifact_ref")
    manifest_digest = result.get("manifest_digest")
    if not isinstance(artifact_ref, str) or not isinstance(manifest_digest, str):
        raise RuntimeError("p6_disabled_rehearsal_result_invalid")
    artifact = Path(artifact_ref).resolve()
    if artifact.parent != (run_root / "runs").resolve():
        raise RuntimeError("p6_disabled_rehearsal_path_escape")
    verified = verify_forward_run(artifact, expected_input_digest=job.input_digest)
    if verified.manifest_digest != manifest_digest:
        raise RuntimeError("p6_disabled_rehearsal_manifest_drift")
    summary = verified.summary
    zero_fields = (
        "dwd_read_count",
        "shadow_write_count",
        "paper_write_count",
        "settlement_write_count",
        "scorecard_write_count",
        "notification_count",
        "broker_action_count",
        "live_capital_action_count",
    )
    if (
        summary.get("mode") != "disabled_rehearsal"
        or summary.get("official_evidence") is not False
        or any(summary.get(name) != 0 for name in zero_fields)
    ):
        raise RuntimeError("p6_disabled_rehearsal_authority_leak")
    return verified.manifest_digest


def _rollback_receipt(
    store: PlatformStore,
    *,
    activation_root: Path,
    mandate_digest: str,
    code_revision: str,
    rehearsal_manifest_digest: str,
    observed_at: datetime,
) -> tuple[dict[str, object], str]:
    official_jobs = [
        job
        for job in store.list_jobs(P6_FORWARD_CYCLE_JOB, 100)
        if isinstance(job.input_ref, dict)
        and job.input_ref.get("mode") == "official_cycle"
    ]
    if official_jobs:
        raise RuntimeError("p6_rollback_drill_found_pre_activation_official_job")
    semantic: dict[str, object] = {
        "schema_version": "quanthus.p6-rollback-drill.v1",
        "work_item": "P6-ACTIVATE-001",
        "state": "PASS_ADMISSION_DISABLED",
        "activation_mandate_digest": mandate_digest,
        "deployment_revision": code_revision,
        "disabled_rehearsal_manifest_digest": rehearsal_manifest_digest,
        "official_job_count_before_activation": 0,
        "new_admission_enabled": False,
        "published_evidence_preserved": True,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
    }
    payload = {**semantic, "receipt_digest": digest_value(semantic)}
    path = activation_root / "rollback" / f"{code_revision}.json"
    file_digest = _write_once(path, payload)
    if file_digest != _sha(path):
        raise RuntimeError("p6_rollback_receipt_checksum_mismatch")
    return payload, str(payload["receipt_digest"])


def _activation_current(
    activation_root: Path,
    *,
    runtime: P6ProspectiveRuntimeConfig,
    mandate_digest: str,
) -> tuple[dict[str, object], str] | None:
    pointer_path = activation_root / "CURRENT.json"
    if not pointer_path.is_file():
        return None
    pointer = _load(pointer_path, "p6_activation_pointer")
    if set(pointer) != {"schema_version", "decision_path", "decision_sha256"}:
        raise RuntimeError("p6_activation_pointer_invalid")
    relative = Path(str(pointer["decision_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("p6_activation_pointer_path_escape")
    path = (activation_root / relative).resolve()
    if activation_root.resolve() not in path.parents or not path.is_file():
        raise RuntimeError("p6_activation_decision_missing")
    if _sha(path) != pointer["decision_sha256"]:
        raise RuntimeError("p6_activation_pointer_digest_mismatch")
    raw = _load(path, "p6_activation_decision")
    decision = activation_decision_from_mapping(
        raw,
        runtime=runtime,
        path=path,
        expected_mandate_digest=mandate_digest,
    )
    if not decision.enabled:
        return None
    return raw, decision.decision_digest


def _publish_enabled_decision(
    *,
    activation_root: Path,
    runtime: P6ProspectiveRuntimeConfig,
    mandate: Any,
    code_revision: str,
    rehearsal_manifest_digest: str,
    rollback_drill_digest: str,
    now: datetime,
) -> tuple[dict[str, object], str]:
    decision_id = f"p6-activation-{now.astimezone(_MARKET_TZ).strftime('%Y%m%d')}-{code_revision[:12]}"
    payload = make_activation_decision_payload(
        runtime=runtime,
        mandate=mandate,
        decision_id=decision_id,
        state="enabled",
        decision_at=now,
        deployment_revision=code_revision,
        disabled_rehearsal_manifest_digest=rehearsal_manifest_digest,
        rollback_drill_digest=rollback_drill_digest,
    )
    relative = Path("decisions") / f"{decision_id}.json"
    path = activation_root / relative
    decision_sha = _write_once(path, payload)
    _write_pointer(
        activation_root / "CURRENT.json",
        {
            "schema_version": "quanthus.p6-activation-current.v1",
            "decision_path": relative.as_posix(),
            "decision_sha256": decision_sha,
        },
    )
    return payload, str(payload["decision_digest"])


def _visibility_from_sync_receipt(
    daily_root: Path,
    pointer: dict[str, Any],
) -> dict[str, object]:
    market_date = str(pointer.get("market_date", ""))
    package_id = str(pointer.get("package_id", ""))
    receipt_path = daily_root / "receipts" / market_date / f"{package_id}.json"
    sync = _load(receipt_path, "p6_dwd_sync_receipt")
    if (
        sync.get("kind") != "oci_cn01_dwd_daily_sync"
        or sync.get("market_date") != market_date
        or sync.get("package_id") != package_id
        or sync.get("manifest_sha256") != pointer.get("manifest_sha256")
        or sync.get("content_digest") != pointer.get("content_digest")
    ):
        raise RuntimeError("p6_dwd_sync_receipt_binding_mismatch")
    synced_at = datetime.fromisoformat(str(sync.get("synced_at")))
    semantic = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": package_id,
        "market_date": market_date,
        "manifest_sha256": str(pointer["manifest_sha256"]),
        "content_digest": str(pointer["content_digest"]),
        "ready_observed_at": synced_at.astimezone(timezone.utc).isoformat(),
        "consumer_visible_at": synced_at.astimezone(timezone.utc).isoformat(),
        "source_node": "oci-cn01-dwd-sync",
    }
    return {**semantic, "receipt_digest": digest_value(semantic)}


def _official_jobs(store: PlatformStore) -> list[Any]:
    return [
        job
        for job in store.list_jobs(P6_FORWARD_CYCLE_JOB, 100)
        if isinstance(job.input_ref, dict)
        and job.input_ref.get("mode") == "official_cycle"
    ]


def _latest_official_success(store: PlatformStore):
    candidates = [
        job
        for job in _official_jobs(store)
        if job.state == "succeeded" and isinstance(job.result_ref, dict)
    ]
    return max(candidates, key=lambda item: item.completed_at or item.updated_at) if candidates else None


def _first_official_success(store: PlatformStore):
    candidates = [
        job
        for job in _official_jobs(store)
        if job.state == "succeeded" and isinstance(job.result_ref, dict)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: str(item.input_ref.get("market_date")))


def _first_official_reproduction_job(store: PlatformStore, primary: Any):
    result = primary.result_ref
    if not isinstance(result, dict):
        raise RuntimeError("p6_first_official_result_missing")
    primary_run_id = result.get("run_id")
    primary_manifest = result.get("manifest_digest")
    if not isinstance(primary_run_id, str) or not isinstance(primary_manifest, str):
        raise RuntimeError("p6_first_official_result_invalid")
    source = OfficialCycleJobInput.from_dict(primary.input_ref)
    if source.prior_run_id is not None:
        raise RuntimeError("p6_first_official_identity_invalid")
    value = OfficialCycleReproductionJobInput(
        source_input=source,
        primary_run_id=primary_run_id,
        primary_manifest_digest=primary_manifest,
    )
    queue = DurableJobQueue(store, {P6_FORWARD_CYCLE_JOB: "replay"})
    stable_job_id = "job_" + hashlib.sha256(
        f"p6-official-reproduction:{value.input_digest}".encode()
    ).hexdigest()[:32]
    return queue.submit(
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id=_actor_user_id(store),
        idempotency_key=value.idempotency_key,
        input_ref=value.to_dict(),
        concurrency_key=value.concurrency_key,
        requested_job_id=stable_job_id,
    )[0]


def _first_official_reproduction_complete(job: Any, primary: Any) -> bool:
    if job.state in {"failed", "canceled"}:
        raise RuntimeError("p6_first_official_reproduction_failed")
    if job.state != "succeeded":
        return False
    result = job.result_ref
    primary_result = primary.result_ref
    if not isinstance(result, dict) or not isinstance(primary_result, dict):
        raise RuntimeError("p6_first_official_reproduction_result_missing")
    summary = result.get("summary")
    if (
        not isinstance(summary, dict)
        or summary.get("mode") != "official_reproduction"
        or summary.get("official_evidence") is not False
        or summary.get("semantic_match") is not True
        or summary.get("primary_run_id") != primary_result.get("run_id")
        or summary.get("primary_manifest_digest")
        != primary_result.get("manifest_digest")
        or summary.get("reproduced_manifest_digest")
        != primary_result.get("manifest_digest")
    ):
        raise RuntimeError("p6_first_official_reproduction_semantic_mismatch")
    return True


def _official_active(store: PlatformStore) -> bool:
    return any(
        isinstance(job.input_ref, dict)
        and job.input_ref.get("mode") == "official_cycle"
        and job.state in {"queued", "running"}
        for job in store.list_jobs(P6_FORWARD_CYCLE_JOB, 100)
    )


def _submit_current_official(
    store: PlatformStore,
    *,
    runtime: P6ProspectiveRuntimeConfig,
    activation_payload: dict[str, object],
    now: datetime,
):
    if _official_active(store):
        return None
    pointer = _load(runtime.daily_root / "CURRENT.json", "p6_dwd_current")
    if (
        pointer.get("kind") != "cn01_daily_dwd_current"
        or not isinstance(pointer.get("market_date"), str)
        or not isinstance(pointer.get("package_id"), str)
        or not isinstance(pointer.get("manifest_sha256"), str)
        or not isinstance(pointer.get("content_digest"), str)
    ):
        raise RuntimeError("p6_dwd_current_invalid")
    market_date = datetime.fromisoformat(
        str(pointer["market_date"]) + "T00:00:00+08:00"
    ).date()
    activation_market_date = datetime.fromisoformat(
        str(activation_payload["activation_market_date"]) + "T00:00:00+08:00"
    ).date()
    if market_date <= activation_market_date:
        return None
    previous = _latest_official_success(store)
    if previous is not None:
        previous_market_date = str(previous.input_ref.get("market_date"))
        if previous_market_date == market_date.isoformat():
            return previous
        summary = previous.result_ref.get("summary")
        if (
            isinstance(summary, dict)
            and int(summary.get("collection_session_count", 0)) >= 66
            and int(summary.get("tail_session_count", 0)) >= 20
        ):
            return previous
        prior_run_id = str(previous.result_ref.get("run_id"))
        prior_manifest = str(previous.result_ref.get("manifest_digest"))
    else:
        prior_run_id = None
        prior_manifest = None
    visibility = _visibility_from_sync_receipt(runtime.daily_root, pointer)
    value = OfficialCycleJobInput(
        cycle_id=f"p6-official-{market_date.isoformat()}",
        runtime_sha256=runtime.config_sha256,
        activation_decision=activation_payload,
        market_date=market_date,
        package_id=str(pointer["package_id"]),
        manifest_sha256=str(pointer["manifest_sha256"]),
        content_digest=str(pointer["content_digest"]),
        visibility_receipt=visibility,
        consumed_at=now,
        prior_run_id=prior_run_id,
        prior_run_manifest_digest=prior_manifest,
    )
    queue = DurableJobQueue(store, {P6_FORWARD_CYCLE_JOB: "replay"})
    stable_job_id = "job_" + hashlib.sha256(
        f"{P6_FORWARD_CYCLE_JOB}:{value.input_digest}".encode()
    ).hexdigest()[:32]
    return queue.submit(
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id=_actor_user_id(store),
        idempotency_key=f"p6-official-{market_date.isoformat()}-{value.manifest_sha256[7:19]}",
        input_ref=value.to_dict(),
        concurrency_key=value.concurrency_key,
        requested_job_id=stable_job_id,
    )[0]


def controller_tick(
    store: PlatformStore,
    *,
    repository_root: Path,
    p6_forward_run_root: Path,
    runtime_path: Path,
    mandate_path: Path,
    code_revision: str,
    now: datetime | None = None,
) -> dict[str, object]:
    instant = now or datetime.now(timezone.utc)
    runtime = load_prospective_runtime_config(runtime_path, root=repository_root)
    mandate = load_owner_activation_mandate(mandate_path, runtime=runtime)
    activation_root = p6_forward_run_root.resolve() / "activation"
    active = _activation_current(
        activation_root,
        runtime=runtime,
        mandate_digest=mandate.mandate_digest,
    )
    if active is not None and active[0].get("deployment_revision") != code_revision:
        if _official_jobs(store):
            raise RuntimeError("p6_post_evidence_revision_drift")
        # Before the first official observation, a maintenance revision may rebind activation only
        # by replaying every predeclared gate. The prior decision remains immutable and CURRENT
        # advances append-only to the new revision-specific decision.
        active = None
    if active is None:
        rehearsal = _disabled_rehearsal_job(
            store,
            runtime=runtime,
            code_revision=code_revision,
        )
        rehearsal_manifest = _verified_rehearsal_manifest(rehearsal, p6_forward_run_root)
        if rehearsal_manifest is None:
            return {
                "state": "waiting_disabled_rehearsal",
                "job_id": rehearsal.job_id,
                "job_state": rehearsal.state,
            }
        if not _platform_green():
            return {"state": "waiting_platform_health"}
        if not _dwd_mounts_read_only(runtime):
            return {"state": "blocked_dwd_mount_not_read_only"}
        _receipt, rollback_digest = _rollback_receipt(
            store,
            activation_root=activation_root,
            mandate_digest=mandate.mandate_digest,
            code_revision=code_revision,
            rehearsal_manifest_digest=rehearsal_manifest,
            observed_at=instant,
        )
        active = _publish_enabled_decision(
            activation_root=activation_root,
            runtime=runtime,
            mandate=mandate,
            code_revision=code_revision,
            rehearsal_manifest_digest=rehearsal_manifest,
            rollback_drill_digest=rollback_digest,
            now=instant,
        )
    payload, decision_digest = active
    first_official = _first_official_success(store)
    reproduction = None
    if first_official is not None:
        reproduction = _first_official_reproduction_job(store, first_official)
        if not _first_official_reproduction_complete(reproduction, first_official):
            return {
                "state": "waiting_first_official_reproduction",
                "activation_decision_digest": decision_digest,
                "job_id": first_official.job_id,
                "job_state": first_official.state,
                "reproduction_job_id": reproduction.job_id,
                "reproduction_job_state": reproduction.state,
            }
    job = _submit_current_official(
        store,
        runtime=runtime,
        activation_payload=payload,
        now=instant,
    )
    return {
        "state": "enabled",
        "activation_decision_digest": decision_digest,
        "job_id": None if job is None else job.job_id,
        "job_state": None if job is None else job.state,
        "first_official_reproduction_complete": first_official is not None,
        "reproduction_job_id": None if reproduction is None else reproduction.job_id,
    }
