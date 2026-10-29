"""Describe the exact original-B entry funnel without executing its policies.

Allocation results absent from this archive remain NOT_RECORDED. The additive
runtime sidecar is deliberately not retrofitted onto historical records.
"""

from __future__ import annotations

import argparse
import html
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quanthus.expert_strategy.decision_chain import read_package, require, write_query
from quanthus.expert_strategy.decision_review import encoded

PURPOSE = "RECORDED_B_ENTRY_FUNNEL_REVIEW_ONLY"


def project_funnel(
    data: Mapping[str, Any],
    index: Mapping[str, Any],
    manifest: Mapping[str, Any],
    trusted_digest: str,
) -> dict[str, Any]:
    """Use an authenticated chain index once for both machine and human views."""
    counts: Counter[str] = Counter()
    groups: Counter[tuple[str, bool, str]] = Counter()
    statuses: Counter[str] = Counter()
    examples: dict[str, Any] = {}
    for key, node in sorted(index["nodes"].items()):
        row = data["decisions"][node["decision"]]
        events = [(i, data["events"][i]) for i in node["events"]]
        orders = [
            (i, e)
            for i, e in events
            if e["event_type"] == "order_created" and e["payload"]["side"] == "buy"
        ]
        filled = bool(node["filled_buy"])
        require(not filled or bool(orders), "FUNNEL_FILL_WITHOUT_ORDER")
        counts["opportunities"] += 1
        if row["final_action"] == "trial_entry":
            counts["trial_entry_actions"] += 1
            counts["trial_entries_without_order"] += not bool(orders)
        else:
            counts["other_actions"] += 1
        counts["scoring_abstentions"] += "all_scoring_abstained_block_new" in row.get(
            "reason_codes", []
        )
        counts["entries_with_order"] += bool(orders)
        counts["buy_orders"] += len(orders)
        counts["filled_entries"] += filled
        counts["entries_ordered_not_filled"] += bool(orders) and not filled
        state = "filled" if filled else "unfilled_order" if orders else "no_order"
        groups[row["final_action"], row["allocated"], state] += 1
        # Use explicit buy order ids: held/sell rejections must not become entry reasons.
        order_ids = {e["payload"]["order_id"] for _, e in orders}
        reasons = [
            {
                "event_row": i,
                "event_id": e["event_id"],
                "reason_code": e["reason_code"],
                "transition": e["payload"]["transition"],
            }
            for i, e in events
            if e["event_type"] == "order_status" and e["payload"]["order_id"] in order_ids
        ]
        if orders and not filled:
            statuses.update(r["reason_code"] for r in reasons)
        group_key = f"{row['final_action']}/{state}"
        examples.setdefault(
            group_key,
            {
                "entry_key": key,
                "security_code": node["security_code"],
                "market_date": row["market_date"],
                "decision_row": node["decision"],
                "formal_decision_digest": row["formal_decision_digest"],
                "strategy_reason_codes": row.get("reason_codes", []),
                "allocation_reason": "NOT_RECORDED",
                "observed_state": state,
                "order_event_rows": [i for i, _ in orders],
                "buy_statuses": reasons,
            },
        )
    # A missing category is zero, not an omitted field with ambiguous meaning.
    names = (
        "opportunities",
        "trial_entry_actions",
        "other_actions",
        "scoring_abstentions",
        "trial_entries_without_order",
        "entries_with_order",
        "buy_orders",
        "filled_entries",
        "entries_ordered_not_filled",
    )
    return {
        "schema_version": "quanthus.recorded-B-entry-funnel.v1",
        "purpose": PURPOSE,
        "trusted_chain_manifest_sha256": trusted_digest,
        "contract_sha256": manifest["contract_sha256"],
        "source_members": manifest["members"],
        "scope": manifest["scope"],
        "variant_id": data["original_manifest"]["variant_id"],
        "execution_authorized": False,
        "historical_PIT_qualified": False,
        "consumer_admission": "RESEARCH_DIAGNOSTIC_ONLY",
        "counts": {k: counts[k] for k in names},
        "groups": [
            {
                "final_action": action,
                "allocated_flag": allocated,
                "observed_state": state,
                "count": count,
            }
            for (action, allocated, state), count in sorted(groups.items())
        ],
        "unfilled_buy_status_reasons": dict(sorted(statuses.items())),
        "allocation": {
            "recorded_results": 0,
            "trial_entry_results_not_recorded": counts["trial_entry_actions"],
            "no_order_trial_entry_reasons_not_recorded": counts["trial_entries_without_order"],
            "status": "NOT_RECORDED_IN_SELECTED_MANIFEST_MEMBERS",
            "allocated_flag_meaning": "PRODUCER_CONSIDERATION_NOT_CAPITAL_RESERVATION",
        },
        "examples": examples,
        "claim_ceiling": (
            "原模拟记录的工程漏斗；没有委托不证明策略失败或错失收益；"
            "未重跑分配或经济路径。"
        ),
    }


def render_funnel(report: Mapping[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    c = report["counts"]
    stages = [
        ("候选机会", c["opportunities"], "原记录的完整分母"),
        ("试探入场动作", c["trial_entry_actions"], "评分动作通过，不等于资金已预留"),
        ("产生买入委托", c["entries_with_order"], "由原 order_created 事件确认"),
        ("实际模拟成交", c["filled_entries"], "由原买入 fill 事件确认"),
    ]
    rows = "".join(
        f"<tr><td>{esc(label)}</td><td>{n:,}</td><td>{esc(note)}</td></tr>"
        for label, n, note in stages
    )
    cases = "".join(
        f"<details><summary>{esc(k)}：{esc(v['entry_key'])}</summary>"
        f"<p>{esc(v['market_date'])} · {esc(v['security_code'])} · "
        f"原决定行 {v['decision_row']} · 委托事件行 {esc(v['order_event_rows'])}</p>"
        f"<p>原动作理由：{esc(v['strategy_reason_codes'])}；分配原因：未记录。</p>"
        f"<p>原买入状态：{esc(v['buy_statuses'])}</p></details>"
        for k, v in report["examples"].items()
    )
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        "<title>原 B 入场漏斗</title><style>body{font:17px/1.7 system-ui;max-width:1000px;"
        "margin:40px auto;padding:0 24px}td,th{padding:10px;text-align:left;"
        "border-bottom:1px solid #ddd}code{overflow-wrap:anywhere}details{margin:16px 0}</style>"
        "<h1>原 B：从入场动作到成交</h1>"
        "<p>只读回溯报告，保留原模拟结果。资金分配原因没有保存在所选原包中。</p>"
        "<table><tr><th>阶段</th><th>机会数</th><th>含义</th></tr>" + rows + "</table>"
        f"<p>{c['scoring_abstentions']:,} 个机会评分弃权；"
        f"{c['trial_entries_without_order']:,} 个试探入场动作未见买入委托，原因未记录；"
        f"{c['entries_ordered_not_filled']:,} 个机会委托后未成交。</p>"
        "<p>“allocated=true”只表示被生产程序考虑，不能解释为资金已预留。"
        "没有委托不代表错误拒绝或损失收益。不能从今天的代码反推每条历史原因。</p>"
        f"<p>原未成交买入状态理由：{esc(report['unfilled_buy_status_reasons'])}</p>"
        "<h2>原字段定位示例</h2>" + cases + "<h2>来源与用途</h2>"
        f"<p>镜像 manifest：<code>{esc(report['trusted_chain_manifest_sha256'])}</code></p>"
        f"<p>原业务范围与实际观察截止：{esc(report['scope'])}</p>"
        "<p>未重训、未重算策略或资金分配，不建立历史当时可知、投资增益或生产/P6资格。</p>"
        "</html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="只读原B入场漏斗，不重新执行分配")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    args = parser.parse_args()
    manifest, data, index = read_package(args.package, args.manifest_sha256)
    report = project_funnel(data, index, manifest, args.manifest_sha256)
    write_query(args.json, encoded(report), args.package, manifest)
    write_query(args.html, render_funnel(report).encode(), args.package, manifest)


if __name__ == "__main__":
    main()
