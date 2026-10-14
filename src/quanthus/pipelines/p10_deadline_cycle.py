"""Node-specific P10 delivery checkpoint runner; email is explicit.

Authoritative A-share trade calendar determines the target market date. The
checker reads sealed live products, not the old four-lane minute CURRENT.
When activated by a systemd timer, '--send' uses the existing Resend secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from quanthus.pipelines.p10_deadline_notifications import evaluate
from quanthus.pipelines.p10_minute_daily_cycle import calendar_for, resolve_target
from quanthus.pipelines.p10_minute_delivery_mail import assess_minute_delivery
from quanthus.pipelines.p10_operating_schedule import SHANGHAI
from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
    DAILY_CORE_PRODUCT_SCOPE,
)
from quanthus.warehouse_notifications import (
    send_daily_event,
    send_minute_delivery_outcome,
)
from quanthus.warehouse_sync import (
    WarehouseSyncError,
    _remote_path,
    _run_rsync,
    _ssh_command,
    _validate_pointer,
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


def minute_mail_snapshot(
    *, target: date, minute_root: Path, phase_root: Path, phase: str,
) -> dict[str, str]:
    """Human-readable facts from an exact sealed grade, never raw HTTP success."""
    context: dict[str,str] = {}
    day=target.isoformat()
    dated=minute_root/"date-status"/day
    receipts=sorted(dated.glob("*.json"),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    if receipts:
        receipt=json.loads(receipts[0].read_text())
        package=minute_root/"packages"/day/receipts[0].stem
        manifest=package/"manifest.json"
        if (
            manifest.is_file() and (package/"SEALED").is_file()
            and receipt.get("market_date")==day
            and receipt.get("manifest_sha256")==(
                "sha256:"+hashlib.sha256(manifest.read_bytes()).hexdigest()
            )
            and (package/"SEALED").read_text().strip()==receipt["manifest_sha256"]
        ):
            grades=receipt.get("quality_grades",{})
            context["quality_distribution"]=" / ".join(
                f"{g}={int(grades.get(g,0))}"
                for g in ("R0","R1","R2","R3","SUSPENDED")
            )
            context["research_qualification"]=(
                "READY" if receipt.get("research_ready") is True else "NOT READY"
            )
            reasons=Counter()
            for line in (package/"quality-status.jsonl").read_text().splitlines():
                value=json.loads(line)
                if value.get("quality_grade")=="R3":
                    reasons[str(value.get("quality_reason"))]+=1
            context["quality_reasons"]="; ".join(
                f"{why}: {n}" for why,n in reasons.most_common(3)
            ) or "无R3"
            context["publication_status"]="已封存分级包；是否可研究以 READY 单独判断"
    label="T" if phase=="minute_T" else "T_plus_1"
    ledger=phase_root/f"{day}-{label}.json"
    if ledger.is_file():
        state=json.loads(ledger.read_text())
        ops=state.get("operations",[])
        captures=[op for op in ops if str(op.get("operation_id","")).startswith(
            "capture-" if label=="T" else "revision-"
        )]
        context["collection_status"]=(
            f"任务执行：{sum(op.get('outcome')=='success' for op in captures)}成功/"
            f"{sum(op.get('outcome')=='failed' for op in captures)}失败"
        )
        context["tplus1_state"]=str(state.get("terminal_disposition","未形成最终修订"))
        context["executed_phase"]=label
    if "quality_distribution" not in context:
        context["research_qualification"]="未发现经过验证的正式分钟质量包"
    return context


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
    minute_phase = args.node=="CN01" and args.phase in {
        "minute_T", "minute_Tplus1",
    }
    if minute_phase:
        result=assess_minute_delivery(
            target=target,phase=args.phase,at=now,calendar=calendar,
            minute_root=minute,phase_root=phases,
        )
    else:
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
    if args.send and minute_phase and result.get("mail_event"):
        grade=result.get("grade")
        try:
            detail=minute_mail_snapshot(
                target=target,minute_root=minute,
                phase_root=phases,phase=args.phase,
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            detail={}
        passed=result["status"]=="pass"
        detail.update({
            "node":"CN01",
            "stage":args.phase,
            "executed_phase":"T" if args.phase=="minute_T" else "T+1",
            "delivery_verdict":"PASS" if passed else "FAIL",
            "delivery_reason":result.get("reason") or (
                "R3=0；正式封存和R2受限存储均已验证"
            ),
            "publication_status":(
                "封存包 SHA 与真实 ODS/DWD 已验证" if grade else "无可验证正式分级包"
            ),
            "quality_distribution":(
                " / ".join(f"{key}={grade['grades'][key]}" for key in
                         ("R0","R1","R2","R3","SUSPENDED"))
                if grade else "不可验证"
            ),
            "research_qualification":(
                "READY" if grade["research_ready"]
                else "NOT READY" if grade is not None else "未验证"
            ),
            "grade_manifest_sha256":(
                grade["manifest_sha256"] if grade else "缺失或验证失败"
            ),
            "source_revised_count":str(grade["revised_count"]) if grade else "未验证",
        })
        result["delivery"]=send_minute_delivery_outcome(
            market_date=target,phase=args.phase,passed=passed,
            state_root=args.notification_state_root,
            context=detail,reason=result.get("reason"),
        )
    elif args.send and result.get("mail_event"):
        detail=(
            minute_mail_snapshot(
                target=target,minute_root=minute,
                phase_root=phases,phase=args.phase,
            )
            if args.phase in {"minute_T","minute_Tplus1"} else {}
        )
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
            context={"node":args.node,"stage":args.phase,**detail},
        )
        result["delivery"]=delivery
    else:
        result["delivery"]="not_sent"
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
