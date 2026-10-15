"""One failure email per target/stage, including retries of transport failure."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from contextlib import suppress
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus import warehouse_notifications as mail
from quanthus.pipelines import p10_notification_incident as incident
from quanthus.pipelines import p10_notification_trial as trial
from quanthus.pipelines.p10_complete_daily_schedule import POLICY_ID
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, object_at
from quanthus.policy import find_project_root

EVENTS = {
    "domain_daily": "p10_cn01_daily_domain_failed",
    "natural_day": "p10_cn01_natural_day_ods_failed",
    "weekly_source": "p10_cn01_weekly_source_failed",
    "weekly": "p10_cn01_weekly_continuation_failed",
    "acquisition_quality": "p10_cn01_acquisition_quality_failed",
    "gdrive": "p10_cn01_complete_daily_gdrive_failed",
    "daily_ods": "p10_cn01_daily_ods_failed",
    "daily_cycle": "p10_cn01_daily_cycle_failed",
    "minutes": "p10_cn01_native_minutes_failed",
    "complete": "p10_cn01_complete_daily_failed",
    "mirror": "p10_oci_complete_daily_failed",
}
SUCCESS_EVENTS = {
    "daily_ods": "p10_cn01_daily_ods_completed",
    "minutes": "p10_cn01_tplus1_minutes_completed",
}

SOURCE_GAP_NOTICE = "p10-source-gap-notice-v1"
SOURCE_GAP_REASON = (
    "SOURCE_NOT_REFRESHED: affected domains remain excluded; qualified daily core continues"
)
SOURCE_GAP_NAMES = {
    "adjustment_evidence_delta": "复权证据",
    "suspensions_delta": "停复牌专门来源",
    "corporate_actions_delta": "公司行动",
    "dividends_delta": "分红",
    "factor_evidence_delta": "因子证据",
    "financial_disclosures_delta": "财务披露增量",
}


def source_gap_context(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Only already-excluded, nonblocking domains qualify for a limited notice."""
    policy = object_at(find_project_root() / "config/pipelines/p10-acquisition-prevention.v1.json")
    if not entries or policy.get("policy_id") != "p10-acquisition-prevention-v1":
        raise ValueError("source gap notice requires the existing acquisition policy")
    allowed = set(policy["daily_core_excluded_domains"])
    for entry in entries:
        name = entry.get("dataset")
        if (
            name not in allowed or name not in SOURCE_GAP_NAMES
            or entry.get("daily_core_blocked") is not False
            or entry.get("current_refresh_proven") is not False
            or entry.get("state") != "unknown"
            or entry.get("reason_code") not in {
                "SOURCE_NOT_REFRESHED", "NONEMPTY_DATASET_UNHANDLED",
            }
            or entry.get("blocked_uses") != [name]
        ):
            raise ValueError("source gap cannot be described as nonblocking")
    domains = sorted({entry["dataset"] for entry in entries})
    return {
        "notification_outcome": "limited",
        "source_gap_policy_id": SOURCE_GAP_NOTICE,
        "source_gap_entries": entries,
        "source_gap_summary": "、".join(SOURCE_GAP_NAMES[name] for name in domains),
        "blocked_domains": domains,
        "daily_core_blocked": False,
        "required_intervals": ["1d"],
        "policy_id": "daily-core-v1",
        "daily_ods_status": "本检查仅报告排除域；日频合格状态以独立封存回执为准",
    }


def _event_for(stage: str, context: dict[str, Any]) -> str:
    outcome = context.get("notification_outcome")
    if outcome == "success":
        return SUCCESS_EVENTS[stage]
    if outcome == "limited":
        if stage != "acquisition_quality":
            raise ValueError("limited source notice cannot replace another failure stage")
        checked = source_gap_context(context.get("source_gap_entries", []))
        if any(context.get(key) != value for key, value in checked.items()):
            raise ValueError("limited source notice identity differs")
        return "p10_cn01_acquisition_quality_limited"
    return EVENTS[stage]


def _failure_context(stage: str, reason: str, context: dict[str, Any]) -> dict[str, Any]:
    if context.get("notification_outcome") != "limited":
        return context
    try:
        allowed = set(source_gap_context(context.get("source_gap_entries", []))) | {
            "market_date_label", "notification_policy_id", "notification_group", "error_code",
        }
        valid = (
            stage == "acquisition_quality"
            and reason in {"SOURCE_NOT_REFRESHED", SOURCE_GAP_REASON}
            and context.get("error_code") in {None, "SOURCE_NOT_REFRESHED"}
            and not (set(context) - allowed)
            and _event_for(stage, context) == "p10_cn01_acquisition_quality_limited"
        )
    except (ValueError, KeyError, TypeError):
        valid = False
    if valid:
        return context
    # A genuine fault must retain its own failure event/key, even when a caller
    # accidentally carried the known-debt context into the fault path.
    clean = dict(context)
    for key in (
        "source_gap_entries", "source_gap_policy_id", "source_gap_summary",
        "daily_core_blocked", "daily_ods_status", "policy_id", "required_intervals",
    ):
        clean.pop(key, None)
    clean["notification_outcome"] = "failure"
    return clean


def _notification_key(day: date, stage: str, context: dict[str, Any]) -> str:
    identity = [POLICY_ID, day.isoformat(), stage]
    if str(context.get("market_date_label", "")).startswith("运行日期"):
        identity.append("execution_date_unresolved")
    if context.get("isolated_failure_drill"):
        identity.append(str(context["isolated_failure_drill"]))
    if context.get("notification_outcome") == "success":
        identity.append("success")
    elif context.get("notification_outcome") == "limited":
        identity.append(SOURCE_GAP_NOTICE)
    return digest(encoded(identity))[7:]


def success(
    *, day: date, stage: str, state_root: Path, evidence: dict[str, Any],
    code_revision: str,
) -> dict[str, Any]:
    """Queue verified stage completion; the independent sender owns transport retries."""
    if stage not in SUCCESS_EVENTS or not trial.enabled(day):
        return {"status": "success_notification_not_enabled"}
    if evidence.get("market_date") != str(day) or not evidence.get("verification"):
        raise ValueError("success notification requires exact verified business-date evidence")
    incident.resolve(state_root, day, stage)
    if not trial.success_enabled(day):
        return {"status": "success_notification_not_enabled"}
    details = {
        **evidence, "notification_outcome": "success", "notification_policy_id": trial.POLICY_ID,
        "completed_at": datetime.now(trial.CST).isoformat(), "code_revision": code_revision,
        "policy_id": "daily-core-v1" if stage == "daily_ods" else POLICY_ID,
    }
    key = _notification_key(day, stage, details)
    state_root.mkdir(parents=True, exist_ok=True)
    with (state_root / f"{key}.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        if (state_root / f"{key}.json").exists():
            return {"status": "already_sent", "key": key}
        pending = state_root / "pending" / f"{key}.json"
        if not pending.exists():
            atomic(pending, {
                "policy_id": POLICY_ID, "market_date": str(day), "stage": stage,
                "reason": "verified_stage_completion", "context": details,
                "queued_at": datetime.now(timezone.utc).isoformat(),
            })
    return {"status": "success_notification_queued", "key": key}


def failure(
    *, day: date, stage: str, state_root: Path, reason: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    context = _failure_context(stage, reason, context or {})
    success_notice = (context or {}).get("notification_outcome") == "success"
    limited_notice = (context or {}).get("notification_outcome") == "limited"
    event = _event_for(stage, context or {})
    decision: dict[str, Any] = {}
    if not success_notice:
        decision = trial.failure_decision(
            day=day, stage=stage, reason=reason, context=context or {}, state_root=state_root,
        )
        if decision["action"] == "defer":
            trial.note_deferred(state_root, day, stage, decision)
            return {"status": "deferred_until_deadline", **decision}
        if decision["action"] not in {"legacy", "immediate_fault", "immediate_evidence_fault"}:
            upstream = (context or {}).get("upstream_incident")
            if upstream is None and state_root == incident.CN01_NOTES:
                upstream = incident.upstream(incident.EXPORT, day, stage)
            if isinstance(upstream, dict) and incident.valid(upstream, day, stage):
                trial.note_deferred(state_root, day, stage, {
                    "action": "merged_into_acknowledged_upstream_alert",
                    "upstream_stage": upstream["stage"], "root_alert": upstream["idempotency_key"],
                })
                return {"status": "blocked_upstream_already_notified",
                        "upstream_stage": upstream["stage"]}
    state_root.mkdir(parents=True, exist_ok=True)
    key_context = context or {}
    group = decision.get("group", stage)
    legacy_key = _notification_key(day, stage, key_context)
    if (state_root / "pending" / f"{legacy_key}.json").exists():
        group = key_context.get("notification_group", stage)
    key = _notification_key(day, group, key_context)
    if not success_notice and group == "daily_ods":
        for alias in ("daily_ods", "daily_cycle"):
            old = state_root / f"{_notification_key(day, alias, key_context)}.json"
            if old.exists():
                with suppress(OSError, ValueError, KeyError):
                    incident.acknowledge(state_root, day, group, {
                        **object_at(old), "market_date_label": key_context.get("market_date_label"),
                    })
                return {**object_at(old), "status": "already_sent"}
            queued = state_root / "pending" / old.name
            if queued.exists():
                original = object_at(queued)
                key = queued.stem
                group = original["context"].get("notification_group", original["stage"])
                break
    path = state_root / f"{key}.json"
    if limited_notice and not path.exists():
        legacy_context = {k: v for k, v in key_context.items() if k != "notification_outcome"}
        legacy_path = state_root / f"{_notification_key(day, group, legacy_context)}.json"
        if legacy_path.exists():
            legacy_record = object_at(legacy_path)
            if (
                legacy_record.get("market_date") == str(day)
                and legacy_record.get("stage") == stage
                and legacy_record.get("delivery_status") == "sent"
            ):
                # A format change must not replay already-notified historical
                # source debt or resolve any prior genuine failure incident.
                return {**legacy_record, "status": "already_sent"}
    details = {"policy_id": POLICY_ID, "required_intervals": ["1d", "1m", "5m"], **(context or {})}
    if decision and decision["action"] != "legacy":
        details.update(notification_policy_id=trial.POLICY_ID, notification_group=group)
    if stage in {"daily_cycle", "acquisition_quality"} or group == "daily_ods":
        details.update(policy_id="daily-core-v1", required_intervals=["1d"])
    with (state_root / f"{key}.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        pending = state_root / "pending" / f"{key}.json"
        if path.exists():
            pending.unlink(missing_ok=True)
            return {**object_at(path), "status": "already_sent"}
        if not pending.exists():
            atomic(
                pending,
                {
                    "policy_id": POLICY_ID,
                    "market_date": day.isoformat(),
                    "stage": stage,
                    "reason": mail._bounded_error(reason),
                    "context": details,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        payload = object_at(pending)
        frozen_stage = str(payload.get("stage", ""))
        stage_matches = frozen_stage == stage or (
            group in {"daily_ods", "daily_cycle"}
            and stage in {"daily_ods", "daily_cycle"}
            and frozen_stage in {
                "daily_ods", "daily_cycle",
            }
        )
        if (
            payload.get("policy_id") != POLICY_ID
            or payload.get("market_date") != day.isoformat()
            or not stage_matches
            or not isinstance(payload.get("reason"), str)
            or not isinstance(payload.get("context"), dict)
            or _notification_key(day, payload["context"].get("notification_group", frozen_stage),
                                 payload["context"]) != key
        ):
            raise ValueError("pending notification identity differs")
        event = _event_for(frozen_stage, payload["context"])
        try:
            sent = mail._send_resend(
                event=event,
                market_date=day,
                pointer=None,
                result=None,
                key=key,
                error=None if success_notice else payload["reason"],
                context=payload["context"],
            )
        except Exception as exc:
            atomic(
                state_root / "failures" / f"{key}.json",
                {
                    "market_date": day.isoformat(),
                    "stage": stage,
                    "delivery_status": "failed",
                    "error_class": type(exc).__name__,
                    "policy_id": POLICY_ID,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise
        record = {
            "market_date": day.isoformat(),
            "stage": frozen_stage,
            "delivery_status": "sent",
            "policy_id": POLICY_ID,
            "idempotency_key": key,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "market_date_label": payload["context"].get("market_date_label", "交易日"),
            **sent,
        }
        atomic(path, record)
        pending.unlink(missing_ok=True)
        if not success_notice and not limited_notice:
            with suppress(OSError, ValueError, KeyError):
                incident.acknowledge(state_root, day, group, record)
        return record


def retry_pending(state_root: Path, *, limit: int = 4) -> dict[str, Any]:
    """Replay bounded immutable mail requests without re-running any data stage."""
    if not 1 <= limit <= 4:
        raise ValueError("notification replay limit must be between one and four")
    reconciliation = reconcile_successes(state_root)
    outcomes = []
    # Bytes are immutable; mtime is only the mail queue's round-robin priority.
    pending = sorted(
        (state_root / "pending").glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)
    )
    for path in pending[:limit]:
        try:
            payload = object_at(path)
            day = date.fromisoformat(payload["market_date"])
            stage = payload["stage"]
            if (
                stage not in (SUCCESS_EVENTS if payload.get("context", {}).get(
                    "notification_outcome") == "success" else EVENTS)
                or path.stem != _notification_key(
                    day, payload["context"].get("notification_group", stage), payload["context"]
                )
                or payload.get("policy_id") != POLICY_ID
            ):
                raise ValueError("pending notification identity differs")
            record = failure(
                day=day,
                stage=stage,
                state_root=state_root,
                reason=payload["reason"],
                context=payload["context"],
            )
            if (
                payload["context"].get("notification_outcome") == "limited"
                and record.get("delivery_status") == "sent"
                and record.get("idempotency_key") != path.stem
            ):
                atomic(state_root / "reclassified-source-notices" / path.name, payload)
                path.unlink(missing_ok=True)
            outcomes.append({"key": path.stem, "status": record.get("status", "sent")})
        except Exception as exc:
            with suppress(FileNotFoundError):
                os.utime(path, None)
            outcomes.append(
                {"key": path.stem, "status": "retry_failed", "error_class": type(exc).__name__}
            )
    return {
        "status": "notification_retry_completed",
        "outcomes": outcomes,
        "remaining": len(list((state_root / "pending").glob("*.json"))),
        "completion_reconciliation": reconciliation,
    }


def reconcile_successes(state_root: Path) -> list[dict[str, Any]]:
    """Recover a missed enqueue for only today's two authoritative operating targets."""
    if state_root != incident.CN01_NOTES:
        return []
    from quanthus.pipelines.p10_complete_daily_schedule import checkpoint, target_for

    now = datetime.now(trial.CST)
    try:
        calendar = object_at(state_root.parent / "calendars" / f"{now.date()}.json")
        fetched = datetime.fromisoformat(calendar["fetched_at"])
        if (
            calendar.get("market_date") != str(now.date())
            or calendar.get("provider") != "baostock-trading-calendar"
            or calendar.get("rows_sha256") != digest(encoded(calendar["rows"]))
            or fetched.tzinfo is None or fetched > now
            or fetched.astimezone(trial.CST).date() != now.date()
        ):
            raise ValueError("completion reconciliation calendar identity differs")
        potential = {target_for(now, calendar["rows"], daily=True),
                     target_for(now, calendar["rows"])}
        targets = {day for day in potential if day is not None}
        minute_target = target_for(now, calendar["rows"])
    except (OSError, ValueError, KeyError, TypeError):
        return [{"status": "completion_reconciliation_calendar_unavailable"}]
    results = []
    for day in sorted(targets):
        if not trial.success_enabled(day):
            continue
        for stage in SUCCESS_EVENTS:
            if stage == "minutes" and (
                day != minute_target or now < checkpoint(day, "minute_start", calendar["rows"])
            ):
                continue
            key = _notification_key(day, stage, {"notification_outcome": "success"})
            if any(path.exists() for path in [state_root / f"{key}.json",
                                              state_root / "pending" / f"{key}.json"]):
                continue
            try:
                if stage == "daily_ods":
                    shadow = Path(os.environ.get("HERMES_ODS_SHADOW_ROOT",
                                                 "/root/work/astock/data/shadow/cn01-ods-v1"))
                    records = sorted((shadow / "orchestrator/daily-core" / str(day)).glob(
                        "*/run.json"))[-3:]
                    ready = [object_at(path) for path in records]
                    ready = [item for item in ready
                             if item.get("catalog", {}).get("status") == "ready"]
                    if not ready:
                        continue
                    proof = trial.daily_evidence(Path(ready[-1]["catalog"]["root"]),
                                                 shadow / "releases", day)
                else:
                    status = object_at(state_root.parent / str(day) / "status.json")
                    minutes = status.get("minutes", {})
                    if set(minutes) != {"1m", "5m"} or not all(
                        value.get("research_ready") is True for value in minutes.values()
                    ):
                        continue
                    proof = trial.minute_evidence(minutes, day)
                revision = subprocess.check_output(
                    ["git", "-C", str(Path(__file__).resolve().parents[3]), "rev-parse", "HEAD"],
                    text=True,
                ).strip()
                results.append(success(day=day, stage=stage, state_root=state_root,
                                       evidence=proof, code_revision=revision))
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                results.append({"status": "completion_not_yet_qualified", "stage": stage,
                                "error_class": type(exc).__name__})
    return results


def _service_started_at(unit: str) -> float | None:
    try:
        started = (
            int(
                subprocess.check_output(
                    [
                        "systemctl",
                        "show",
                        unit,
                        "--property=ExecMainStartTimestampMonotonic",
                        "--value",
                    ],
                    text=True,
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
            / 1_000_000
        )
        return time.time() - (time.monotonic() - started) if started > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _daily_service_started_at() -> float | None:
    return _service_started_at("quanthus-daily-core@cn01.service")


def _service_invocation_id(unit: str) -> str | None:
    """Only the originating failure identity, never a newer current invocation."""
    value = os.environ.get("MONITOR_INVOCATION_ID", "")
    if os.environ.get("MONITOR_UNIT") != unit or not re.fullmatch(r"[0-9a-f]{32}", value):
        return None
    return value


def _complete_cycle_failure_target(
    now: datetime, state: Path, *, originating_invocation_id: str | None = None
) -> tuple[date, str, str]:
    """Resolve only the exact originating controller invocation's date and phase."""
    fallback = (now.date(), "minutes", "运行日期（目标交易日未确定）")
    unit = "quanthus-complete-daily.service"
    service_start = _service_started_at(unit)
    invocation = (
        originating_invocation_id
        if originating_invocation_id is not None
        else _service_invocation_id(unit)
    )
    try:
        if (
            service_start is None
            or invocation is None
            or not re.fullmatch(r"[0-9a-f]{32}", invocation)
        ):
            return fallback
        active = object_at(state / "active-run.json")
        started = datetime.fromisoformat(active["started_at"])
        if (
            active.get("kind") != "p10_complete_daily_cycle_activity_v1"
            or active.get("policy_id") != POLICY_ID
            or active.get("invocation_id") != invocation
            or not re.fullmatch(r"[0-9a-f]{40}", str(active.get("code_revision", "")))
            or active.get("stage") not in {"minutes", "complete"}
            or started.tzinfo is None
            or not service_start - 1 <= started.timestamp() <= now.timestamp()
        ):
            return fallback
        return date.fromisoformat(active["market_date"]), active["stage"], "交易日"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return fallback


def _archive_failure_target(now: datetime, state: Path) -> tuple[date, str]:
    fallback = (now.date(), "运行日期（目标交易日未确定）")
    started = _service_started_at("quanthus-gdrive-complete-daily.service")
    if started is None:
        return fallback
    paths = sorted(
        (state / "failures").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in paths[:28]:
        if not started - 1 <= path.stat().st_mtime <= now.timestamp():
            continue
        try:
            payload = object_at(path)
            return date.fromisoformat(payload["market_date"]), "交易日"
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return fallback


def _daily_failure_target(
    now: datetime, path: Path, *, originating_invocation_id: str | None = None
) -> tuple[date, str]:
    fallback = (now.date(), "运行日期（目标交易日未确定）")
    service_start = _daily_service_started_at()
    invocation = (
        originating_invocation_id
        if originating_invocation_id is not None
        else _service_invocation_id("quanthus-daily-core@cn01.service")
    )
    if service_start is None or invocation is None or not re.fullmatch(r"[0-9a-f]{32}", invocation):
        return fallback
    try:
        state = object_at(path)
        started = datetime.fromisoformat(state["started_at"])
        if (
            state.get("invocation_id") != invocation
            or started.tzinfo is None
            or not service_start - 1 <= started.timestamp() <= now.timestamp()
        ):
            return fallback
        failures = [item for item in state.get("attempts", []) if item.get("returncode")]
        candidate = failures[-1]["market_date"] if failures else state.get("active_market_date")
        return (date.fromisoformat(candidate), "交易日") if candidate else fallback
    except (OSError, ValueError, TypeError, KeyError):
        return fallback


def _continuation_failure_target(
    now: datetime, mode: str, state: Path, *, originating_invocation_id: str | None = None
) -> tuple[date, str]:
    """Use this invocation's declared target; stale files remain unresolved."""
    fallback = (now.date(), "运行日期（目标交易日未确定）")
    unit = f"cn01-dwd-domain-continuation@{mode}.service"
    service_start = _service_started_at(unit)
    invocation = (
        originating_invocation_id
        if originating_invocation_id is not None
        else _service_invocation_id(unit)
    )
    if service_start is None or invocation is None or not re.fullmatch(r"[0-9a-f]{32}", invocation):
        return fallback
    try:
        active = object_at(state / "active" / f"{mode}.json")
        started = datetime.fromisoformat(active["started_at"])
        if (
            active.get("mode") != mode
            or active.get("invocation_id") != invocation
            or started.tzinfo is None
            or not service_start - 1 <= started.timestamp() <= now.timestamp()
        ):
            return fallback
        return date.fromisoformat(active["market_date"]), "业务日期"
    except (OSError, ValueError, TypeError, KeyError):
        return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daily-cycle-failure", action="store_true")
    mode.add_argument("--daily-cycle-service-stop", action="store_true")
    mode.add_argument("--complete-cycle-service-failure", action="store_true")
    mode.add_argument("--complete-cycle-service-stop", action="store_true")
    mode.add_argument("--archive-service-failure", action="store_true")
    mode.add_argument("--mirror-service-failure", action="store_true")
    mode.add_argument("--mirror-service-stop", action="store_true")
    mode.add_argument("--continuation-service-failure", choices=("daily", "weekly", "natural-day"))
    mode.add_argument("--continuation-service-stop", choices=("daily", "weekly", "natural-day"))
    mode.add_argument("--retry-state", action="append", type=Path)
    args = parser.parse_args()
    if (
        args.daily_cycle_service_stop
        or args.complete_cycle_service_stop
        or args.continuation_service_stop
    ):
        result = os.environ.get("SERVICE_RESULT")
        if not result:
            raise ValueError("service stop hook requires systemd SERVICE_RESULT")
        if result == "success":
            print(json.dumps({"status": "notification_not_required", "service_result": result}))
            return 0
    if args.retry_state:
        results = [retry_pending(root) for root in args.retry_state]
        print(json.dumps({"results": results}, sort_keys=True))
        return (
            2
            if any(
                item["status"] == "retry_failed"
                for result in results
                for item in result["outcomes"]
            )
            else 0
        )
    from quanthus.pipelines.p10_complete_daily_cycle import DAILY, JOURNAL
    from quanthus.pipelines.p10_complete_daily_schedule import CST

    if args.complete_cycle_service_failure or args.complete_cycle_service_stop:
        target, stage, label = _complete_cycle_failure_target(
            datetime.now(CST),
            JOURNAL,
            originating_invocation_id=os.environ.get("INVOCATION_ID", "")
            if args.complete_cycle_service_stop
            else None,
        )
        error_code = "controller_runtime_failed"
        try:
            status = object_at(JOURNAL / str(target) / "status.json")
            if (
                status.get("invocation_id")
                and status["invocation_id"] == os.environ.get("INVOCATION_ID")
                and status.get("status") in {
                    "minutes_not_qualified", "waiting_for_sealed_daily_ods",
                }
            ):
                error_code = "retryable_stage_incomplete"
        except (OSError, ValueError, KeyError, TypeError):
            pass
        record = failure(
            day=target,
            stage=stage,
            state_root=JOURNAL / "notifications",
            reason="T+1 complete Daily controller failed or timed out; "
            "inspect preserved Job and operation receipts",
            context={
                "market_date_label": label,
                "unit": "quanthus-complete-daily.service",
                "error_code": error_code,
            },
        )
        print(json.dumps(record, sort_keys=True))
        return 0

    if args.mirror_service_stop:
        result = os.environ.get("SERVICE_RESULT")
        if result is None:
            raise ValueError("mirror stop hook requires systemd SERVICE_RESULT")
        if result == "success":
            print(json.dumps({"status": "notification_not_required", "service_result": result}))
            return 0
    if args.mirror_service_failure or args.mirror_service_stop:
        from quanthus.pipelines.p10_complete_daily_sync import CACHE, LOCAL

        now = datetime.now(CST)
        target, label = now.date(), "运行日期（目标交易日未确定）"
        try:
            pending = object_at(LOCAL / CACHE / "pending.json")
            started = datetime.fromisoformat(pending["started_at"])
            service_start = _service_started_at("oci-complete-daily-sync.service")
            if (
                service_start is not None
                and started.tzinfo is not None
                and service_start - 1 <= started.timestamp() <= now.timestamp()
            ):
                target, label = date.fromisoformat(pending["market_date"]), "交易日"
        except (OSError, ValueError, KeyError, TypeError):
            pass
        record = failure(
            day=target,
            stage="mirror",
            state_root=LOCAL / "notifications",
            reason="complete DWD mirror service failed or timed out; private partials retained",
            context={"market_date_label": label, "unit": "oci-complete-daily-sync.service"},
        )
        print(json.dumps(record, sort_keys=True))
        return 0
    if args.continuation_service_failure or args.continuation_service_stop:
        mode_name = args.continuation_service_failure or args.continuation_service_stop
        continuation_state = Path("/var/lib/quanthus/acquisition-continuation-v1")
        target, label = _continuation_failure_target(
            datetime.now(CST),
            mode_name,
            continuation_state,
            originating_invocation_id=os.environ.get("INVOCATION_ID", "")
            if args.continuation_service_stop
            else None,
        )
        record = failure(
            day=target,
            stage="weekly"
            if mode_name == "weekly"
            else ("natural_day" if mode_name == "natural-day" else "domain_daily"),
            state_root=continuation_state / "notifications",
            reason=(
                "automatic domain continuation service failed or timed out; "
                "inspect retained run evidence"
            ),
            context={
                "market_date_label": label,
                "mode": mode_name,
            },
        )
        print(json.dumps(record, sort_keys=True))
        return 0
    if args.archive_service_failure:
        from quanthus.pipelines.p10_complete_daily_gdrive import MAIL_STATE, STATE

        state = STATE
        target, label = _archive_failure_target(datetime.now(CST), state)
        record = failure(
            day=target,
            stage="gdrive",
            state_root=MAIL_STATE,
            reason="complete DWD GDrive service failed or timed out; "
            "inspect its bounded archive log",
            context={
                "market_date_label": label,
                "unit": "quanthus-gdrive-complete-daily.service",
                "error_code": "gdrive_service_failed",
            },
        )
        print(json.dumps(record, sort_keys=True))
        return 0
    target, label = _daily_failure_target(
        datetime.now(CST),
        DAILY / "operations/LATEST.json",
        originating_invocation_id=os.environ.get("INVOCATION_ID", "")
        if args.daily_cycle_service_stop
        else None,
    )
    record = failure(
        day=target,
        stage="daily_cycle",
        state_root=JOURNAL / "notifications",
        reason="independent daily ODS/DWD service failed; inspect its stage record; "
        "this alert does not imply both acquisition and build failed",
        context={"market_date_label": label, "error_code": "daily_cycle_service_failed"},
    )
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
