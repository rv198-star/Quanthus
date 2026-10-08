"""Node-specific P10 delivery checkpoint runner; email is explicit.

Authoritative A-share trade calendar determines the target market date. The
checker reads sealed live products, not the old four-lane minute CURRENT.
When activated by a systemd timer, '--send' uses the existing Resend secrets.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from quanthus.pipelines.p10_deadline_notifications import evaluate
from quanthus.pipelines.p10_minute_daily_cycle import calendar_for, resolve_target
from quanthus.pipelines.p10_operating_schedule import SHANGHAI
from quanthus.warehouse_notifications import send_daily_event
from quanthus.warehouse_sync import (
    WarehouseSyncError,
    _remote_path,
    _run_rsync,
    _ssh_command,
    _validate_pointer,
)
from quanthus.pipelines.securities_warehouse.daily_ods_catalog import (
    DAILY_CORE_PRODUCT_SCOPE,
)


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


def _source_cn01_daily_digest(target: date) -> str:
    """Read CN01's dated authority over the existing approved OCI SSH jail.

    Do not trust OCI's local mirrored pointer as independent source evidence.
    """
    host, _, user, _, _, ssh = _ssh_command()
    directory = _remote_path("date-pointers")
    with tempfile.TemporaryDirectory(prefix="p10-peer-readonly-") as tmp:
        output = Path(tmp) / f"{target.isoformat()}.json"
        _run_rsync(
            source=f"{user}@{host}:{directory}/{target.isoformat()}.json",
            destination=output,
            ssh=ssh,
        )
        obj = json.loads(output.read_text(encoding="utf-8"))
        _validate_pointer(obj, target, DAILY_CORE_PRODUCT_SCOPE)
        digest = obj.get("manifest_sha256")
        if not isinstance(digest, str) or len(digest) != 71:
            raise WarehouseSyncError("remote source manifest digest is invalid")
        return digest


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
    source_error = None
    peer_sha = args.peer_cn01_sha
    if args.node=="OCI" and args.phase=="oci_mirror" and peer_sha is None:
        try:
            peer_sha = _source_cn01_daily_digest(target)
        except (OSError, ValueError, WarehouseSyncError) as exc:
            source_error = f"CN01 live source unavailable: {type(exc).__name__}"
    result=evaluate(
        node=args.node,phase=args.phase,target=target,
        calendar=calendar,daily_root=daily,
        minute_root=minute,phase_root=phases,
        historical_minute_root=Path(
            "/srv/quanthus/dwd-dws-v2/intraday/products/loss-aware-1m-v2"
        ) if args.node=="CN01" else None,
        at=now,cn01_sha=peer_sha,
    )
    if source_error:
        result["source_recheck"] = source_error
    if args.send and result.get("mail_event"):
        delivery=send_daily_event(
            event=result["mail_event"],
            daily_root=daily,market_date=target,
            state_root=args.notification_state_root,
            error=(
                "P10_source_native_quality_not_READY"
                if result["status"]=="quality_warning"
                else (
                    source_error
                    or "P10_required_node_product_missing_or_unverified_at_checkpoint"
                )
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
