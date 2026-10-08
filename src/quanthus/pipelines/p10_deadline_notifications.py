"""P10 bounded failure and one-per-day quality notices.

Default dry-run; --send calls the existing idempotent Resend warehouse mailer.
Exchange-closed dates, not-yet-due checkpoints and live bounded finalization
are never interpreted as pipeline failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.pipelines.p10_four_node_inventory import daily_snapshot
from quanthus.pipelines.p10_operating_schedule import alert_checkpoint, classify_alert
from quanthus.warehouse_notifications import send_daily_event

CST = ZoneInfo("Asia/Shanghai")
EVENTS = {
    ("CN01", "daily_core"): "p10_cn01_daily_core_failed",
    ("Hermes", "daily_core"): "p10_hermes_daily_core_failed",
    ("OCI", "oci_mirror"): "p10_oci_daily_core_mirror_failed",
    ("CN01", "minute_T"): "p10_cn01_minute_t_failed",
    ("CN01", "minute_Tplus1"): "p10_cn01_minute_tplus1_failed",
}


def _minute_disposition(root: Path, target: date) -> str | None:
    """Require a dated, SHA-bound admission; a bare JSON READY isn't enough."""
    day = target.isoformat()
    ongoing = root / "date-status" / day
    if ongoing.is_dir():
        receipts = sorted(
            ongoing.glob("*.json"), key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        for receipt in receipts:
            obj = json.loads(receipt.read_text())
            if obj.get("market_date") != day:
                raise ValueError("published minute quality date differs")
            expected = obj.get("manifest_sha256")
            files = list((root / "packages" / day).glob("*/manifest.json"))
            matches = [
                path for path in files if path.is_file()
                and "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == expected
            ]
            if len(matches) != 1:
                raise ValueError("minute quality receipt lacks exact sealed source manifest")
            if not (matches[0].parent / "SEALED").is_file():
                raise ValueError("minute grade product not sealed")
            return (
                "RESEARCH_READY_LOSS_AWARE"
                if obj.get("research_ready") is True
                else "PARTIAL_COVERAGE_NO_RESEARCH_READY"
            )
    legacy = root / "date-status" / f"{day}.json"
    if legacy.is_file():
        obj = json.loads(legacy.read_text())
        if obj.get("market_date") != day:
            raise ValueError("minute legacy date status wrong market date")
        return (
            "RESEARCH_READY_LOSS_AWARE"
            if obj.get("research_ready") is True
            else "PARTIAL_COVERAGE_NO_RESEARCH_READY"
            if obj.get("research_ready") is False
            else None
        )
    return None


def _bounded_inflight(
    state: dict, now: datetime,
) -> bool:
    pending = state.get("inflight_operation")
    if pending is None:
        return False
    start = datetime.fromisoformat(str(pending["started_at"]))
    if start.tzinfo is None:
        raise ValueError("unbound operation start timezone")
    timeout = int(pending.get("timeout_seconds", 600))
    if not 1 <= timeout <= 600:
        raise ValueError("invalid in-flight atomic operation deadline")
    # A dead process must not suppress its failure email forever. Allow the
    # independent operation timeout and a bounded termination/reporting grace.
    return now.astimezone(timezone.utc) <= (
        start.astimezone(timezone.utc) + timedelta(seconds=timeout + 45)
    )


def evaluate(
    *,
    node: str,
    phase: str,
    target: date,
    calendar: list[dict],
    daily_root: Path,
    minute_root: Path,
    phase_root: Path,
    at: datetime,
    cn01_sha: str | None = None,
    historical_minute_root: Path | None = None,
) -> dict:
    if (node, phase) not in EVENTS:
        raise ValueError("node is not qualified for this P10 alert phase")
    matches = [r for r in calendar if r.get("trade_date") == target.isoformat()]
    if len(matches) != 1 or str(matches[0].get("is_open")) not in {"0", "1"}:
        raise ValueError("missing or ambiguous target trading calendar")
    if str(matches[0]["is_open"]) == "0":
        return {"status": "calendar_closed_skip", "mail_event": None}
    checkpoint = alert_checkpoint(target, phase, calendar)
    receipt: str | None = None
    inflight = False
    exact_mirror = None
    if phase == "daily_core":
        current = daily_snapshot(root=daily_root)
        if (
            current["pointer_verified"]
            and current["pointer_market_date"] == target.isoformat()
        ):
            receipt = "READY_VERIFIED"
    elif phase == "oci_mirror":
        current = daily_snapshot(root=daily_root)
        exact_mirror = bool(
            current["pointer_verified"]
            and current["pointer_market_date"] == target.isoformat()
            and cn01_sha
            and current["current_manifest_sha256"] == cn01_sha
        )
    else:
        receipt = _minute_disposition(minute_root, target)
        if receipt is None and historical_minute_root is not None:
            receipt = _minute_disposition(historical_minute_root, target)
        name = "T" if phase == "minute_T" else "T_plus_1"
        state_path = phase_root / f"{target.isoformat()}-{name}.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text())
            inflight = _bounded_inflight(state, at)
            if state.get("inflight_operation") is not None and not inflight:
                receipt = None
            elif not inflight:
                terminal = state.get("terminal_disposition")
                if name == "T_plus_1":
                    if terminal == "source_unchanged_stopped":
                        receipt = "SOURCE_UNCHANGED_STOPPED"
                    elif (
                        terminal
                        == "source_changed_requires_new_versioned_quality_requalification"
                    ):
                        receipt = "QUALITY_NOT_READY"
                    elif terminal == "bounded_maturity_probe_incomplete":
                        receipt = None
                    elif terminal == "grade_qualified":
                        if receipt == "RESEARCH_READY_LOSS_AWARE":
                            receipt = "GRADE_RECORDED"
                        elif receipt == "PARTIAL_COVERAGE_NO_RESEARCH_READY":
                            receipt = "QUALITY_NOT_READY"
                        else:
                            receipt = None
                    elif terminal == "historical_source_change_requires_separate_quality_authority":
                        receipt = "QUALITY_NOT_READY"
                    else:
                        receipt = None
                elif terminal == "partial_source_capture_not_published":
                    receipt = None
        elif phase == "minute_Tplus1":
            receipt = None
    outcome = classify_alert(
        kind=phase, checked_at=at, checkpoint=checkpoint,
        receipt_status=receipt, processing_inflight=inflight,
        exact_cn01_mirror=exact_mirror,
    )
    return {
        "kind": "p10_deadline_check_v2",
        "node": node,
        "phase": phase,
        "market_date": target.isoformat(),
        "checkpoint": checkpoint.isoformat(),
        "status": outcome,
        "mail_event": (
            "p10_cn01_minute_quality_degraded" if outcome == "quality_warning"
            else EVENTS[(node, phase)] if outcome == "failure"
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", choices=("CN01", "Hermes", "OCI"), required=True)
    parser.add_argument(
        "--phase",
        choices=("daily_core", "oci_mirror", "minute_T", "minute_Tplus1"),
        required=True,
    )
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--calendar-file", type=Path, required=True)
    parser.add_argument("--daily-root", type=Path, required=True)
    parser.add_argument("--minute-root", type=Path, required=True)
    parser.add_argument("--historical-minute-root", type=Path)
    parser.add_argument("--phase-state-root", type=Path, required=True)
    parser.add_argument("--mail-state-root", type=Path, required=True)
    parser.add_argument("--cn01-manifest-sha")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    result = evaluate(
        node=args.node, phase=args.phase, target=args.market_date,
        calendar=json.loads(args.calendar_file.read_text()),
        daily_root=args.daily_root, minute_root=args.minute_root,
        historical_minute_root=args.historical_minute_root,
        phase_root=args.phase_state_root, at=datetime.now(CST),
        cn01_sha=args.cn01_manifest_sha,
    )
    if args.send and result.get("mail_event"):
        result["delivery"] = send_daily_event(
            event=result["mail_event"], daily_root=args.daily_root,
            market_date=args.market_date, state_root=args.mail_state_root,
            error=(
                "source_native_1m_research_NOT_READY_despite_admitted_R2"
                if result["status"] == "quality_warning"
                else "P10_required_delivery_not_verified_at_checkpoint"
            ),
            context={"node": args.node, "stage": args.phase},
        )
    else:
        result["delivery"] = "not_sent"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
