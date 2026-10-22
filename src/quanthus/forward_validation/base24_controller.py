"""Bounded CN02 admission for an externally activated Base 2.4 paper runtime.

This controller never creates activation or treats the old OCI mandate as a new
grant. P10 publishes exact qualified envelopes and visibility receipts. Each date
freezes its intake clock once; failures and historical activation rows stay intact.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.forward_validation import base24_cycle as cycle
from quanthus.forward_validation.activation import activation_decision_from_mapping
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.controller import (
    _actor_user_id,
    _disabled_rehearsal_job,
    _first_official_reproduction_complete,
    _first_official_reproduction_job,
    _mount_is_read_only,
    _verified_rehearsal_manifest,
)
from quanthus.forward_validation.data_boundary import load_visibility_receipt
from quanthus.forward_validation.official_cycle import OfficialCycleJobInput
from quanthus.pipelines.securities_warehouse.base22_sources import physical_path
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB, DurableJobQueue
from quanthus.platform.persistence import PlatformStore


def tick(
    store: PlatformStore,
    *,
    repository_root: Path,
    run_root: Path,
    runtime_path: Path,
    code_revision: str,
    now: datetime | None = None,
) -> dict[str, object]:
    instant = now or datetime.now(timezone.utc)
    runtime = cycle.load_runtime(runtime_path, root=repository_root)
    decision_path = physical_path(run_root / "activation" / "base24-enabled.json")
    if not decision_path.is_file():
        return {"state": "waiting_new_base24_activation"}
    raw = json.loads(decision_path.read_text(encoding="utf-8"))
    activation = activation_decision_from_mapping(raw, runtime=runtime, path=decision_path)
    if not activation.enabled:
        return {"state": activation.state}
    if activation.deployment_revision != code_revision or activation.decision_at > instant:
        fail(
            "p6_base24_activation_binding", "exact running revision and actual activation required"
        )
    if not all(_mount_is_read_only(p) for p in (runtime.daily_root, decision_path.parent)):
        return {"state": "blocked_base24_input_or_activation_mount_writable"}
    # An enabled decision binds an actual rehearsal from this exact deployment.
    rehearsal = _disabled_rehearsal_job(store, runtime=runtime, code_revision=code_revision)
    digest = _verified_rehearsal_manifest(rehearsal, run_root)
    if digest is None:
        return {"state": "waiting_disabled_rehearsal", "job_id": rehearsal.job_id}
    if digest != activation.disabled_rehearsal_manifest_digest:
        fail("p6_base24_rehearsal_binding", "activation binds a different rehearsal")

    jobs = store.list_jobs(P6_FORWARD_CYCLE_JOB, 200)
    if len(jobs) == 200:
        fail("p6_base24_job_census_limit", "bounded admission requires a complete census")
    official = [j for j in jobs if j.input_ref.get("mode") == "official_cycle"]
    current = [
        j
        for j in official
        if j.input_ref.get("activation_decision", {}).get("decision_digest")
        == activation.decision_digest
    ]
    if any(j.state in {"failed", "canceled"} for j in current):
        return {"state": "blocked_failed_cycle"}
    # No cross-activation prior-ledger inheritance or concurrent local writers.
    if any(j.state in {"queued", "running"} for j in official if j not in current):
        return {"state": "blocked_other_activation_job_active"}
    successes = sorted(
        (j for j in current if j.state == "succeeded"),
        key=lambda j: str(j.input_ref["market_date"]),
    )
    if successes:
        reproduction = _first_official_reproduction_job(store, successes[0])
        if not _first_official_reproduction_complete(reproduction, successes[0]):
            return {"state": "waiting_first_official_reproduction", "job_id": reproduction.job_id}
        last = successes[-1]
        summary = (last.result_ref or {}).get("summary", {})
        if (
            summary.get("collection_session_count", 0) >= 66
            and summary.get("tail_session_count", 0) >= 20
        ):
            return {"state": "completed_frozen_budget"}
    else:
        last = None

    pointer_path = physical_path(runtime.daily_root / "CURRENT.json")
    if not pointer_path.is_file():
        return {"state": "waiting_qualified_base24_daily"}
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if (
        set(pointer)
        != {"schema_version", "market_date", "package_id", "manifest_sha256", "content_digest"}
        or pointer.get("schema_version") != "quanthus.p6-base24-current.v1"
    ):
        fail("p6_base24_pointer_invalid", "exact successor pointer required")
    from datetime import date

    day = date.fromisoformat(pointer["market_date"])
    package = pointer["package_id"]
    if not isinstance(package, str) or Path(package).name != package or package in {".", ".."}:
        fail("path_escape", "qualified Daily package id")
    if day <= activation.activation_market_date:
        return {"state": "waiting_post_activation_session"}
    receipt_path = physical_path(
        runtime.daily_root / "receipts" / day.isoformat() / f"{package}.json"
    )
    receipt_raw: Any = json.loads(receipt_path.read_text(encoding="utf-8"))
    visibility = load_visibility_receipt(receipt_raw)
    binding = {
        "runtime_sha256": runtime.config_sha256,
        "activation_decision": raw,
        "market_date": day.isoformat(),
        "package_id": package,
        "manifest_sha256": pointer["manifest_sha256"],
        "content_digest": pointer["content_digest"],
        "visibility_receipt": receipt_raw,
    }
    stable_id = (
        "job_"
        + hashlib.sha256(
            f"p6-base24:{activation.decision_digest}:{day.isoformat()}".encode()
        ).hexdigest()[:32]
    )
    existing = store.get_job(stable_id)
    if existing is not None:
        if any(existing.input_ref.get(k) != v for k, v in binding.items()):
            fail("p6_base24_intake_conflict", "same activation/date changed its frozen source")
        return {
            "state": "blocked_failed_cycle"
            if existing.state in {"failed", "canceled"}
            else "enabled",
            "job_id": existing.job_id,
            "job_state": existing.state,
        }
    if any(j.state in {"queued", "running"} for j in current):
        return {"state": "waiting_current_cycle"}
    if last is not None and day <= date.fromisoformat(str(last.input_ref["market_date"])):
        fail("p6_base24_nonmonotone_intake", "cannot move the current activation backward")
    release = cycle.verify_release(
        runtime.daily_root / "packages" / day.isoformat() / package, visibility
    )
    if (
        release.manifest_sha256 != pointer["manifest_sha256"]
        or release.content_digest != pointer["content_digest"]
    ):
        fail("p6_base24_pointer_binding", "pointer differs from verified envelope")
    # Reject old/late inputs before a durable official Job is created. This actual
    # intake instant is retained by the stable Job; later ticks never replace it.
    from quanthus.forward_validation.base24_runtime import AfterCloseDecisionClock

    inner = json.loads(release.inputs.manifest_json)
    AfterCloseDecisionClock(day, datetime.fromisoformat(inner["source_available_at"]), instant)
    if instant < visibility.consumer_visible_at:
        fail("pre_visibility_read", "qualified Daily is not yet visible")
    value = OfficialCycleJobInput(
        cycle_id=f"p6-base24-{day.isoformat()}",
        runtime_sha256=runtime.config_sha256,
        activation_decision=raw,
        market_date=day,
        package_id=package,
        manifest_sha256=release.manifest_sha256,
        content_digest=release.content_digest,
        visibility_receipt=receipt_raw,
        consumed_at=instant,
        prior_run_id=None if last is None else str((last.result_ref or {})["run_id"]),
        prior_run_manifest_digest=None
        if last is None
        else str((last.result_ref or {})["manifest_digest"]),
    )
    submitted, _ = DurableJobQueue(store, {P6_FORWARD_CYCLE_JOB: "replay"}).submit(
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id=_actor_user_id(store),
        idempotency_key=f"p6-base24-{activation.decision_digest[7:19]}-{day.isoformat()}",
        input_ref=value.to_dict(),
        concurrency_key=value.concurrency_key,
        requested_job_id=stable_id,
    )
    return {"state": "enabled", "job_id": submitted.job_id, "job_state": submitted.state}
