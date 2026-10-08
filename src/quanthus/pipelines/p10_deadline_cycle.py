"""Node-specific P10 delivery checkpoint runner; email is explicit.

Authoritative A-share trade calendar determines the target market date. The
checker reads sealed live products, not the old four-lane minute CURRENT.
When activated by a systemd timer, '--send' uses the existing Resend secrets.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from quanthus.pipelines.p10_deadline_notifications import evaluate
from quanthus.pipelines.p10_minute_daily_cycle import calendar_for, resolve_target
from quanthus.pipelines.p10_operating_schedule import SHANGHAI
from quanthus.warehouse_notifications import send_daily_event


def target_for(
    phase: str, now: datetime, calendar: list[dict[str, str]]
) -> date | None:
    today=now.astimezone(SHANGHAI).date()
    if phase=="minute_Tplus1":
        return resolve_target(now,"T_plus_1",calendar)
    if phase=="oci_mirror":
        previous=today-timedelta(days=1)
        if any(
            x.get("trade_date")==previous.isoformat() and str(x.get("is_open"))=="1"
            for x in calendar
        ):
            return previous
        return None
    if phase in {"daily_core","minute_T"}:
        return resolve_target(now,"T",calendar)
    raise ValueError("unsupported P10 email checkpoint phase")


def _roots(node: str) -> tuple[Path, Path, Path]:
    if node not in {"CN01","Hermes","OCI"}:
        raise ValueError("unknown P10 runtime node")
    daily=Path(
        "/home/ubuntu/data/quanthus/dwd-daily-core"
        if node=="OCI" else
        "/srv/quanthus/dwd-dws-v2/daily/products/daily-core"
    )
    minute=Path("/srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-ongoing-v1")
    phases=Path("/srv/quanthus/dwd-dws-v2/intraday/operations/p10-minute")
    return daily,minute,phases


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node",choices=("CN01","Hermes","OCI"),required=True)
    parser.add_argument(
        "--phase",
        choices=("daily_core","oci_mirror","minute_T","minute_Tplus1"),
        required=True,
    )
    parser.add_argument("--send",action="store_true")
    parser.add_argument("--peer-cn01-sha")
    parser.add_argument("--notification-state-root",type=Path,default=Path(
        "/srv/quanthus/dwd-dws-v2/notifications/p10"
    ))
    args=parser.parse_args()
    now=datetime.now(SHANGHAI)
    calendar=calendar_for(now.date())
    target=target_for(args.phase,now,calendar)
    if target is None:
        print(json.dumps({
            "status":"verified_nontrading_skip",
            "phase":args.phase,"node":args.node,
        }))
        return 0
    daily,minute,phases=_roots(args.node)
    result=evaluate(
        node=args.node,phase=args.phase,target=target,
        calendar=calendar,daily_root=daily,
        minute_root=minute,phase_root=phases,
        historical_minute_root=Path(
            "/srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-v2"
        ) if args.node=="CN01" else None,
        at=now,cn01_sha=args.peer_cn01_sha,
    )
    if args.send and result.get("mail_event"):
        delivery=send_daily_event(
            event=result["mail_event"],
            daily_root=daily,market_date=target,
            state_root=args.notification_state_root,
            error=(
                "P10_source_native_quality_not_READY"
                if result["status"]=="quality_warning"
                else "P10_required_node_product_missing_or_unverified_at_checkpoint"
            ),
            context={"node":args.node,"stage":args.phase},
        )
        result["delivery"]=delivery
    else:
        result["delivery"]="not_sent"
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
