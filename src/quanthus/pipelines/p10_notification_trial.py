"""Notification-only trial; neither admission nor collection scheduling changes."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_complete_daily_schedule import CST as CST
from quanthus.pipelines.p10_complete_daily_schedule import checkpoint
from quanthus.pipelines.p10_complete_daily_schedule import policy as data_policy
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, object_at
from quanthus.policy import find_project_root

CONFIG = find_project_root() / "config/pipelines/p10-notification-trial.v1.json"
POLICY_ID = "p10-delivery-notification-trial-v1"


def enabled(day: date) -> bool:
    value = object_at(CONFIG)
    if (
        value.get("policy_id") != POLICY_ID
        or value.get("timezone") != "Asia/Shanghai"
        or value.get("success_stages") != ["daily_ods", "minutes"]
        or value.get("data_admission_changes") is not False
        or value.get("historical_success_replay") is not False
    ):
        raise ValueError("notification trial policy identity differs")
    return day >= date.fromisoformat(value["effective_from_market_date"])


def success_enabled(day: date) -> bool:
    return enabled(day) and object_at(CONFIG).get("success_notifications_enabled") is True


def deadline(day: date, stage: str, context: dict[str, Any], state_root: Path) -> datetime:
    key = {
        "minutes": "minute_deadline",
        "complete": "complete_deadline",
        "mirror": "mirror_deadline",
    }.get(stage, "daily_ods_deadline")
    cfg = data_policy()
    if key == "daily_ods_deadline":
        return datetime.combine(day, time.fromisoformat(cfg[key]), tzinfo=CST)
    if context.get("deadline"):
        stamp = datetime.fromisoformat(context["deadline"])
        if (
            stamp.tzinfo is None
            or stamp.astimezone(CST).date() <= day
            or stamp.astimezone(CST).time().replace(tzinfo=None) != time.fromisoformat(cfg[key])
        ):
            raise ValueError("notification deadline identity differs")
        return stamp
    now = datetime.now(CST)
    saved = object_at(state_root.parent / "calendars" / f"{now.date()}.json")
    fetched = datetime.fromisoformat(saved["fetched_at"])
    if (
        saved.get("provider") != "baostock-trading-calendar"
        or saved.get("market_date") != str(now.date())
        or saved.get("rows_sha256") != digest(encoded(saved["rows"]))
        or fetched.tzinfo is None
        or fetched > now
        or fetched.astimezone(CST).date() != now.date()
    ):
        raise ValueError("notification requires saved current authoritative calendar")
    return checkpoint(day, key, saved["rows"])


def failure_decision(
    *,
    day: date,
    stage: str,
    reason: str,
    context: dict[str, Any],
    state_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not enabled(day):
        return {"action": "legacy", "stage": stage}
    now = now or datetime.now(CST)
    if now.tzinfo is None:
        raise ValueError("notification clock must have timezone")
    daily = {"daily_cycle", "daily_ods", "domain_daily", "acquisition_quality"}
    # Only the two hooks for the same daily-core product share a queue identity.
    # Domain continuation and source-quality reports have independent producers/outboxes.
    group = "daily_ods" if stage in {"daily_cycle", "daily_ods"} else stage
    marker = (reason + " " + str(context.get("error_code", ""))).lower()
    actionable = any(
        token in marker
        for token in (
            "baostock_access_denied",
            "baostock_access",
            "黑名单",
            "10001011",
            "disk quota",
            "no space left",
            "disk reserve",
            "disk_reserve",
            "controller_or_calendar_failed",
            "controller_runtime_failed",
            "syntaxerror",
            "modulenotfounderror",
            "permissionerror",
            "process_cleanup_unconfirmed",
            "identity differs",
        )
    )
    if actionable:
        return {"action": "immediate_fault", "stage": stage, "group": group}
    if stage not in daily | {"minutes", "complete", "mirror"}:
        return {"action": "immediate_fault", "stage": stage, "group": group}
    try:
        limit = deadline(day, group, context, state_root)
    except (OSError, ValueError, TypeError, KeyError):
        return {"action": "immediate_evidence_fault", "stage": stage, "group": group}
    return {
        "action": "defer" if now < limit else "deadline_failure",
        "stage": stage,
        "group": group,
        "deadline": limit.isoformat(),
    }


def note_deferred(root: Path, day: date, stage: str, decision: dict[str, Any]) -> None:
    stamp = datetime.now(CST).strftime("%Y%m%dT%H%M%S%f%z")
    atomic(
        root / "observations" / f"{day}-{stage}-{stamp}.json",
        {
            "policy_id": POLICY_ID,
            "market_date": str(day),
            "stage": stage,
            "decision": decision,
            "mail_sent": False,
        },
    )


def daily_evidence(catalog_root: Path, release_root: Path, day: date) -> dict[str, Any]:
    from quanthus.pipelines.contracts import verify_ods_release
    from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
        DAILY_CORE_PRODUCT_SCOPE,
        verify_daily_ods_catalog,
    )

    checked = verify_daily_ods_catalog(catalog_root)
    if checked.market_date != day or checked.product_scope != DAILY_CORE_PRODUCT_SCOPE:
        raise ValueError("daily success requires actual target daily-core catalog")
    catalog = object_at(catalog_root / "catalog.json")
    identities = {}
    for lane in catalog["lanes"]:
        from quanthus.pipelines.p10_delivery_common import safe_path

        actual = verify_ods_release(safe_path(release_root, lane["release_path"]))
        if (
            actual.manifest_sha256 != lane["release_manifest_sha256"]
            or actual.release.market_date != day
        ):
            raise ValueError("daily success source bytes differ from selected catalog")
        identities[lane["lane"]] = lane["release_manifest_sha256"]
    if set(identities) != {"market_daily_core", "calendar_daily_events"}:
        raise ValueError("daily completion source closure differs")
    return {
        "market_date": str(day),
        "verification": "daily_core_catalog_and_source_bytes",
        "catalog_digest": checked.catalog_digest,
        "ods_release_manifests": identities,
        "daily_ods_status": "已完整采集并封存",
        "required_intervals": ["1d"],
        "complete_dwd_status": "未由本通知验收",
        "oci_status": "未由本通知验收",
        "gdrive_status": "未由本通知验收",
    }


def minute_evidence(minutes: dict[str, Any], day: date) -> dict[str, Any]:
    if set(minutes) != {"1m", "5m"}:
        raise ValueError("minute completion requires both native intervals")
    proofs = {}
    for interval, value in minutes.items():
        grades = value.get("quality_grades", {})
        if (
            value.get("research_ready") is not True
            or value.get("market_date") != str(day)
            or value.get("interval") != interval
            or not isinstance(grades, dict)
            or set(grades) - {"R0", "R1", "R2", "R3", "SUSPENDED"}
            or any(not isinstance(count, int) or count < 0 for count in grades.values())
            or not sum(grades.values())
            or grades.get("R3", 0) != 0
            or not str(value.get("manifest_sha256", "")).startswith("sha256:")
        ):
            raise ValueError("minute completion is not verified minimum R2 / R3 zero")
        from quanthus.pipelines.p10_delivery_common import file_digest

        package = Path(value["package_path"])
        manifest = object_at(package / "manifest.json")
        if (
            file_digest(package / "manifest.json") != value["manifest_sha256"]
            or (package / "READY").read_text().strip() != value["manifest_sha256"]
            or manifest.get("grades") != grades
            or manifest.get("research_ready") is not True
            or manifest.get("market_date") != str(day)
            or manifest.get("interval") != interval
            or manifest.get("content_digest") != value.get("content_digest")
            or manifest.get("reference_digest") != value.get("reference_digest")
        ):
            raise ValueError("minute success receipt identity differs from sealed manifest")
        proofs[interval] = {
            k: value[k] for k in ["manifest_sha256", "quality_grades", "market_date", "interval"]
        }
    if minutes["1m"]["reference_digest"] != minutes["5m"]["reference_digest"]:
        raise ValueError("minute completion reference closure differs")
    return {
        "market_date": str(day),
        "verification": "existing_native_physical_verification_receipts",
        "minute_evidence": proofs,
        "quality_distribution": {key: value["quality_grades"] for key, value in proofs.items()},
        "required_intervals": ["1m", "5m"],
        "minute_status": "最低R2，R3=0",
        "complete_dwd_status": "分钟已合格，完整DWD尚待独立确认",
        "oci_status": "尚待完整DWD后精确同步确认",
        "gdrive_status": "尚待云端回执确认",
    }
