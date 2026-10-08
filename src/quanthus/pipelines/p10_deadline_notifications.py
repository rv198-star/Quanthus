"""P10 deadline alerts; no mail is sent without the explicit --send flag.

A known R2 is not a failure; valid NOT_READY is one daily quality notice.
Do not send on exchange holidays, before the checkpoint or during in-flight work.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
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
        if current["pointer_verified"] and current["pointer_market_date"] == target.isoformat():
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
        release = minute_root / "date-status" / (target.isoformat() + ".json")
        if release.exists():
            quality = json.loads(release.read_text())
            if quality.get("market_date") != target.isoformat():
                raise ValueError("minute quality receipt wrong market date")
            if quality.get("research_ready") is True:
                receipt = "RESEARCH_READY_LOSS_AWARE"
            elif quality.get("research_ready") is False:
                receipt = "PARTIAL_COVERAGE_NO_RESEARCH_READY"
        if phase == "minute_Tplus1":
            p = phase_root / (target.isoformat() + "-T_plus_1.json")
            if p.is_file():
                state = json.loads(p.read_text())
                inflight = state.get("inflight_operation") is not None
                disposition = state.get("terminal_disposition")
                if not inflight and disposition == "source_unchanged_stopped":
                    receipt = "SOURCE_UNCHANGED_STOPPED"
                elif not inflight and disposition == "grade_qualified":
                    if receipt == "RESEARCH_READY_LOSS_AWARE":
                        receipt = "GRADE_RECORDED"
                elif not inflight and not disposition:
                    # Starting a probe alone cannot count as finishing T+1.
                    receipt = None
            elif receipt == "RESEARCH_READY_LOSS_AWARE":
                receipt = "GRADE_RECORDED"
            else:
                receipt = None
    status = classify_alert(
        kind=phase, checked_at=at, checkpoint=checkpoint,
        receipt_status=receipt, processing_inflight=inflight,
        exact_cn01_mirror=exact_mirror,
    )
    return {
        "kind": "p10_deadline_check_v1",
        "node": node,
        "phase": phase,
        "market_date": target.isoformat(),
        "checkpoint": checkpoint.isoformat(),
        "status": status,
        "mail_event": (
            "p10_cn01_minute_quality_degraded" if status == "quality_warning"
            else EVENTS[(node, phase)] if status == "failure"
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
    parser.add_argument("--phase-state-root", type=Path, required=True)
    parser.add_argument("--mail-state-root", type=Path, required=True)
    parser.add_argument("--cn01-manifest-sha")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    result = evaluate(
        node=args.node, phase=args.phase, target=args.market_date,
        calendar=json.loads(args.calendar_file.read_text()),
        daily_root=args.daily_root, minute_root=args.minute_root,
        phase_root=args.phase_state_root, at=datetime.now(CST),
        cn01_sha=args.cn01_manifest_sha,
    )
    if args.send and result.get("mail_event"):
        result["delivery"] = send_daily_event(
            event=result["mail_event"], daily_root=args.daily_root,
            market_date=args.market_date, state_root=args.mail_state_root,
            error=(
                "research_grade_not_ready_known_source_defects"
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
