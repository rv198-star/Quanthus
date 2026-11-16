"""Container entry: existing Daily through ODS/DWD sealing, or scoped Weekly."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.p10_operational_mail import atomic, notify, validate

from .container_code import verify_bundle


def daily(config: Path, day: date, recovery: bool) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "quanthus.pipelines.securities_warehouse.daily_base24_cycle",
        "--config",
        str(config),
        "--day",
        day.isoformat(),
    ]
    if recovery:
        command.append("--recovery")
        if (
            config.is_file()
            and json.loads(config.read_text()).get("no_trade_regrade_continuation") is True
        ):
            command.append("--no-trade-regrade")
    # Do not buffer provider logs/all rows in application memory.
    with tempfile.TemporaryFile() as log:
        result = subprocess.run(command, stdout=log, stderr=sys.stderr, check=False)
        log.seek(0, 2)
        end = log.tell()
        log.seek(max(0, end - 65536))
        lines = log.read().splitlines()
    if result.returncode:
        raise RuntimeError("P10_DAILY_PIPELINE_FAILED")
    if not lines:
        raise ValueError("P10_DAILY_RESULT_MISSING")
    body = json.loads(lines[-1])
    value = body.get("result", body.get("entry", body))
    if not isinstance(value, dict):
        raise ValueError("P10_DAILY_RESULT_OBJECT_REQUIRED")
    return value  # existing verified reuse is explicit


def weekly(plan_path: Path, day: date, revision: str) -> dict[str, Any]:
    """Build/verify the qualified financial domain; unknown domains stay blocked."""
    from .base22 import file_sha
    from .base22_bounded import Budget, Limits
    from .base24_weekly_package import seal_financial_weekly, verify_financial_weekly

    plan = json.loads(plan_path.read_bytes())
    if (
        plan.get("kind") != "p10-container-weekly-financial-v1"
        or plan.get("execution_authorized") is not False
        or plan.get("domains") != ["financial_statements"]
        or plan.get("code_revision") != revision
        or date.fromisoformat(plan["through"]) > day
    ):
        raise ValueError("P10_WEEKLY_SCOPE_OR_REVISION_DIFFERS")
    config = Path(plan["config"])
    if file_sha(config) != plan["config_sha256"]:
        raise ValueError("P10_WEEKLY_CONFIG_CHANGED")
    sources = [Path(plan[k]) for k in ("base", "source", "receipts", "source_control")]
    limits = Limits(**plan["limits"])
    with Budget(Path(plan["job"]), [config, *sources, plan_path], limits) as budget:
        result = seal_financial_weekly(
            base=sources[0],
            config=json.loads(config.read_bytes()),
            source=sources[1],
            receipts=sources[2],
            source_control=sources[3],
            source_sha256=plan["source_sha256"],
            receipt_sha256=plan["receipt_sha256"],
            source_control_sha256=plan["source_control_sha256"],
            expected_envelopes=plan["expected_envelopes"],
            observed_at=datetime.now(timezone.utc),
            through=date.fromisoformat(plan["through"]),
            code_revision=revision,
            budget=budget,
            output=budget.root / "product",
        )
        verify_financial_weekly(
            budget.root / "product", manifest_sha256=result["manifest_sha256"], budget=budget
        )
    return {
        "status": "WEEKLY_FINANCIAL_VERIFIED_OTHER_DOMAINS_HELD",
        "manifest_sha256": result["manifest_sha256"],
        "market_date": day.isoformat(),
    }


def event(
    role: str, day: date, run_id: str, revision: str, result: dict[str, Any]
) -> dict[str, Any]:
    status = result.get("status")
    mapped = {
        "QUALIFIED_1D_NATIVE5M": "complete",
        "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY": "closed",
        "CORE_READY_5M_HELD_SOURCE_QUALITY": "partial",
        "CORE_READY_5M_PENDING": "partial",
        "WEEKLY_FINANCIAL_VERIFIED_OTHER_DOMAINS_HELD": "partial",
        "DATE_WRITER_ALREADY_ACTIVE": "busy",
        "CONTAINER_RUN_FAILED": "failed",
    }
    if status not in mapped or result.get("market_date", str(day)) != str(day):
        raise ValueError("P10_PIPELINE_TERMINAL_STATUS_OR_DATE_INVALID")
    manifests = [
        result[k]
        for k in (
            "core_manifest_sha256",
            "product_manifest_sha256",
            "event_manifest_sha256",
            "manifest_sha256",
        )
        if k in result
    ]
    value = {
        "kind": "p10-container-job-event-v1",
        "run_id": run_id,
        "role": role,
        "market_date": str(day),
        "status": mapped[status],
        "reason": ("LATE_RECOVERY_" + status)
        if result.get("delivery_timing") == "LATE_RECOVERY_NOT_ON_TIME_SUCCESS"
        else status,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_revision": revision,
        "manifests": manifests,
        "execution_authorized": False,
    }
    validate(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["daily", "recovery", "weekly"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]{1,100}", args.run_id):
        raise ValueError("P10_RUN_ID_INVALID")
    if (
        args.event.parent.resolve() != Path("/run/events")
        or args.event.name != args.run_id + ".json"
    ):
        raise ValueError("P10_EVENT_DESTINATION_INVALID")
    root = Path(__file__).resolve().parents[4]
    revision = json.loads((root / "SOURCE.json").read_bytes())["revision"]
    verify_bundle(root, revision)
    try:
        if args.role != "weekly":
            config = json.loads(args.config.read_bytes())
            if (
                config.get("pipeline_revision") != revision
                or config.get("collector") != "/opt/collector"
            ):
                raise ValueError("P10_CONTAINER_PIPELINE_OR_COLLECTOR_BINDING_DIFFERS")
        result = (
            weekly(args.config, args.day, revision)
            if args.role == "weekly"
            else daily(args.config, args.day, args.role == "recovery")
        )
        value = event(args.role, args.day, args.run_id, revision, result)
    except Exception:
        atomic(
            args.event,
            event(args.role, args.day, args.run_id, revision, {"status": "CONTAINER_RUN_FAILED"}),
        )
        notify(args.event, Path("/run/mail"), Path("/run/secrets/mail.json"))
        raise
    atomic(args.event, value)
    mail = notify(args.event, Path("/run/mail"), Path("/run/secrets/mail.json"))
    print(json.dumps({"event": value, "mail": mail}, sort_keys=True))


if __name__ == "__main__":
    main()
