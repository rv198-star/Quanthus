'''Plain-Chinese presentation for the replay console (issue #748, phase-1
experience update).

The Owner reads the replay as advice, not as reason codes: plan reasons,
reduction/exit conditions and risk notes are rendered as Chinese prose.
Internal codes never reach the page - known codes map through the frozen
REASON_TEXTS contract; an unknown code degrades to a generic Chinese
label with the code kept in the engine log (auditable, never displayed).
'''

from __future__ import annotations

from typing import Any

from quanthus.platform.p12.contract_generated import REASON_TEXTS

# Stage / action labels the page uses verbatim.
ACTION_LABELS = {
    "BUY": "买入建议",
    "SELL": "卖出建议",
    "HOLD": "继续持有",
    "REDUCE": "减仓建议",
    "EXIT": "退出建议",
    "ENDED": "已结束",
    "SKIPPED": "已跳过",
}


def reason_text(code: str | None) -> str | None:
    '''Chinese text for one internal reason code; None when unknown.

    The code itself is never returned to the page - the engine log keeps
    it for audit.  An unregistered code (for example a P11-side evidence
    code transported verbatim) degrades to a generic label rather than
    leaking the internal identifier.'''

    if not code:
        return None
    text = REASON_TEXTS.get(str(code).upper())
    if text:
        return text
    return "详见记录页"


def reasons_text(codes) -> list[str]:
    return [t for t in (reason_text(c) for c in codes or ()) if t]


def plan_reason(item: dict) -> list[str]:
    '''Plain-Chinese reason lines for one workbench opportunity.

    Prefers the producer's own explanation text when it is already
    Chinese-readable; internal english template text is replaced by the
    mapped reason lines so the page never shows raw template English or
    internal codes.'''

    lines: list[str] = []
    for ex in item.get("explanations") or []:
        text = str(ex.get("text") or "").strip()
        if not text:
            continue
        # The synthetic runner's template line is English; keep it off
        # the page - the mapped reason + invalidations carry the story.
        if _looks_internal(text):
            continue
        lines.append(text)
    if not lines:
        lines = reasons_text(item.get("reason_codes"))
    return lines


def _looks_internal(text: str) -> bool:
    '''Heuristic: template English / code-shaped text stays off the page.'''

    lowered = text.lower()
    if "[synthetic]" in lowered or "reason_code" in lowered:
        return True
    # Mostly-ASCII sentences are template text; Chinese prose has CJK.
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    return not has_cjk


def invalidation_texts(plan: dict) -> list[str]:
    '''The plan's exit/reduction conditions as plain Chinese lines.

    The synthetic candidates already carry Chinese invalidations; a
    ``requires:PREOPEN_*`` marker is an internal dependency flag and is
    translated, never shown verbatim.'''

    out: list[str] = []
    for marker in plan.get("invalidations") or ():
        marker = str(marker)
        if marker.startswith("requires:PREOPEN_"):
            name = marker.split(":", 1)[1]
            out.append(f"依赖开盘前事实 {name}，暂缺时会阻断确认")
            continue
        out.append(marker)
    return out


def action_label(item: dict) -> str:
    '''Chinese label for one opportunity's suggested action.'''

    code = str(item.get("action_code") or "").upper()
    if code == "SELL":
        kind = str(item.get("exit_kind") or "").lower()
        if kind == "reduce":
            return ACTION_LABELS["REDUCE"]
        return ACTION_LABELS["EXIT"]
    if code in ACTION_LABELS:
        return ACTION_LABELS[code]
    stage = str(item.get("stage") or "").upper()
    if stage == "EXIT_PLAN":
        kind = str(item.get("exit_kind") or "").lower()
        return ACTION_LABELS["REDUCE"] if kind == "reduce" else ACTION_LABELS["EXIT"]
    if stage == "HOLDING":
        return ACTION_LABELS["HOLD"]
    if stage == "ENDED":
        return ACTION_LABELS["ENDED"]
    return ACTION_LABELS.get(code, "提示")


def cn_state(state: dict) -> dict:
    '''Attach plain-Chinese presentation fields to one day_state payload.

    The raw reason codes stay in the payload for debugging; the page reads
    only the *_cn fields.  Reasons, invalidations and the publication
    status line are translated; nothing is invented - an empty reason
    reads as an empty list.'''

    bench = state.get("workbench") or {}
    out = dict(state)
    cn_opps = []
    for item in bench.get("opportunities") or []:
        plan = item.get("plan") or {}
        cn_opps.append(
            {
                "opportunity_id": item.get("opportunity_id"),
                "security_code": item.get("security_code"),
                "security_name": item.get("security_name"),
                "action_label": action_label(item),
                "reason_lines": plan_reason(item),
                "condition_lines": invalidation_texts(plan),
                "constraint": reason_text(item.get("primary_constraint")),
                "plan_id": plan.get("plan_id"),
                "action_code": item.get("action_code"),
                "exit_kind": item.get("exit_kind"),
                "delta_qty": plan.get("delta_qty"),
                "target_qty": plan.get("target_qty"),
                "price_cap": plan.get("price_cap"),
                "eligible_at": plan.get("eligible_at"),
                "expires_at": plan.get("expires_at"),
                "status": (item.get("applicability") or {}).get("status")
                or item.get("status_code"),
            }
        )
    out["opportunities_cn"] = cn_opps
    return out


def day_review(replay: dict, day_state: dict, records: dict) -> dict:
    '''One screen of day review: advice -> owner action -> fills ->
    account change, built on the records page's 建议→委托→成交 chain.

    Everything reads from the same DTOs the workbench/records pages use -
    the review is a presentation of facts, never a second ledger.'''

    plans_by_id = {}
    for item in (day_state.get("workbench") or {}).get("opportunities") or []:
        plan = item.get("plan") or {}
        if plan.get("plan_id"):
            plans_by_id[plan["plan_id"]] = item

    # Owner actions: from the records' plan status + engine events.
    plan_status = {
        p.get("plan_id"): p.get("status")
        for p in records.get("plans") or []
    }
    # Real skip receipts (plan_id -> {skipped_at, reason, actor}) carried on
    # the records chain; the review shows the receipt time, never "未处理".
    skip_receipts = {
        s.get("plan_id"): s
        for s in (records.get("skips") or records.get("skipped") or [])
    }
    reports = [
        r for r in records.get("reports") or []
    ]
    orders = records.get("orders") or []
    events = records.get("events") or []

    # Fills attributed to plans confirmed for this market date; order ids
    # carry the plan: prefix the engine wrote.
    day = replay.get("current_day")
    fills = [
        r for r in reports
        if str(r.get("occurred_at") or "").startswith(str(day or ""))
    ]

    advice = []
    seen_status = set()
    for plan_id, item in plans_by_id.items():
        status = plan_status.get(plan_id)
        seen_status.add(status)
        linked_orders = [
            o for o in orders if str(o.get("order_id") or "").endswith(plan_id)
        ]
        linked_fills = [r for r in fills if r.get("plan_id") == plan_id]
        advice.append(
            {
                "plan_id": plan_id,
                "security_code": item.get("security_code"),
                "security_name": item.get("security_name"),
                "action_label": action_label(item),
                "reason_lines": plan_reason(item),
                "owner_action": (
                    "已跳过"
                    if status == "SKIPPED"
                    else "已确认" if (linked_orders or linked_fills) else "未处理"
                ),
                "skip_receipt": (
                    skip_receipts.get(plan_id) if status == "SKIPPED" else None
                ),
                "orders": linked_orders,
                "fills": linked_fills,
            }
        )

    account = day_state.get("account") or {}
    return {
        "market_date": day,
        "label": "每日回顾",
        "advice": advice,
        "fills_today": fills,
        "orders_today": [
            o for o in orders
            if str(o.get("order_id") or "").startswith("plan:")
        ],
        "events_today": [
            e for e in events
            if str(e.get("recorded_at") or "").startswith(str(day or ""))
        ],
        "account": account,
        "note": "历史回放·模拟，不代表策略评估",
    }
