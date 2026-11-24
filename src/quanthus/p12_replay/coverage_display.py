"""Replay-day coverage display (issue #748 real-rule intake round).

Owner reads one replay day as "evaluable N / strategy pool M, the rest
pending dividend/bonus data clearance", then a per-security verdict and
the rule's own scan reason.  The data comes from ONE day's real runner
receipt - never a re-summary, never a fabricated pool.  Under the
synthetic runner there is no real rule receipt yet, so the block
honestly reports pool-only coverage and leaves per-security rows
empty rather than inventing verdicts.

Data contract (the real-rule day receipt from run_e1_replay_day /
run_replay_day), read verbatim, never recomputed:

  receipt["delivered"]                 {code:{bar_rows,window_sessions,
                                        gap_sessions,entry_eligible}} -
                                        the day's strategy pool (M=len).
  receipt["seam"]["evaluated_universe"]   codes the seam evaluated.
  receipt["seam"]["decisions"]            {security_code,disposition,reason}
  receipt["source_closure"]["held_decisions"]   held-path outcomes.
  receipt["excluded_security_codes"]      #737 / upstream drops.
  receipt["coverage_exclusions"]          per-security coverage isolations.

A security counts as evaluable only when the same run actually
evaluated it; a coverage-isolated or absent-pool member stays pending.
Zero-signal days still list every evaluated security - the display is
about coverage, not advice.
"""

from __future__ import annotations

from typing import Any, Mapping

from quanthus.platform.p12.contract_generated import REASON_TEXTS


def _cn_reason(code: Any) -> str | None:
    """Map one internal reason code to a Chinese line; never leak it."""
    if not code:
        return None
    text = REASON_TEXTS.get(str(code).upper())
    return text or "详见记录页"


def _disposition_label(value: Any) -> str:
    v = str(value or "").upper()
    if v == "ENTRY":
        return "新建信号"
    if v in ("EXIT", "REDUCE", "SELL"):
        return "退出/减仓"
    if v in ("HOLD", "HELD"):
        return "继续持有"
    if v in ("REVIEW", "PENDING_REVIEW"):
        return "待复核"
    if v in ("BLOCKED", "EXCLUDED"):
        return "已阻断"
    return v or "—"


def coverage_block(receipt: Mapping[str, Any] | None) -> dict | None:
    """Build the display block from one real-rule day receipt.

    ``receipt`` is the same-run day receipt the runner issued (the dict
    run_replay_day / run_e1_replay_day returns).  Returns None when there
    is no real-rule receipt - the page then shows the pool-only block so
    the synthetic phase never fakes per-security verdicts.
    """
    if not receipt or not isinstance(receipt, Mapping):
        return None

    delivered = receipt.get("delivered") or {}
    pool = sorted(delivered.keys())
    seam = receipt.get("seam") or {}
    evaluated = set(seam.get("evaluated_universe") or ())
    decisions = {
        str(d.get("security_code")): d
        for d in (seam.get("decisions") or ())
        if d.get("security_code")
    }
    source_closure = receipt.get("source_closure") or {}
    held = {
        str(h.get("security_code")): h
        for h in (source_closure.get("held_decisions") or ())
        if h.get("security_code")
    }
    coverage_excl = {
        str(e.get("security_code")): e
        for e in (receipt.get("coverage_exclusions") or ())
        if e.get("security_code")
    }
    excluded = set(receipt.get("excluded_security_codes") or ())

    rows = []
    for code in pool:
        delivery = delivered.get(code) or {}
        eligible = bool(delivery.get("entry_eligible", True))
        decision = decisions.get(code)
        held_row = held.get(code)
        if code in coverage_excl or not eligible:
            state = "pending"
            scan = _cn_reason(
                (coverage_excl.get(code) or {}).get("reason")
            ) or "公司行为/分红送股数据未核清"
        elif code in evaluated:
            state = "evaluated"
            scan = None
        else:
            state = "pending"
            scan = "输入未交付至评估"
        verdict = None
        reason = None
        if decision is not None:
            verdict = _disposition_label(decision.get("disposition"))
            reason = _cn_reason(decision.get("reason"))
        elif held_row is not None:
            action = str(held_row.get("action") or "")
            verdict = {
                "held": "继续持有",
                "reduce": "减仓建议",
                "exit": "退出建议",
                "hold_alert": "持仓提醒",
            }.get(action, action or "持仓评估")
            reason = _cn_reason(held_row.get("reason")) or _cn_reason(
                held_row.get("unavailable_reason")
            )
        if scan is None and reason:
            scan = reason
        rows.append(
            {
                "security_code": code,
                "evaluated": state == "evaluated",
                "state": state,
                "verdict": verdict,
                "scan_reason": scan,
                "entry_eligible": eligible,
            }
        )

    # Members outside the delivered pool but still evaluated (held review
    # on its own facts) stay visible - they are part of the day's coverage.
    for code in sorted(evaluated - set(pool)):
        held_row = held.get(code)
        decision = decisions.get(code)
        verdict = None
        reason = None
        if decision is not None:
            verdict = _disposition_label(decision.get("disposition"))
            reason = _cn_reason(decision.get("reason"))
        elif held_row is not None:
            verdict = "持仓评估"
            reason = _cn_reason(held_row.get("reason")) or _cn_reason(
                held_row.get("unavailable_reason")
            )
        rows.append(
            {
                "security_code": code,
                "evaluated": True,
                "state": "evaluated",
                "verdict": verdict,
                "scan_reason": reason,
                "entry_eligible": False,
            }
        )

    evaluated_count = sum(1 for r in rows if r["evaluated"])
    pending = [r["security_code"] for r in rows if not r["evaluated"]]
    note_parts = []
    if pending:
        note_parts.append(
            "其余 " + str(len(pending)) + " 只待分红送股数据核清或输入未交付"
        )
    if excluded:
        note_parts.append("当日排除 " + str(len(excluded)) + " 只")
    return {
        "schema_version": "p12.replay.coverage-display.v1",
        "pool_count": len(pool),
        "evaluated_count": evaluated_count,
        "pending_count": len(pending),
        "securities": rows,
        "note": "；".join(note_parts),
        "note_text": (
            "本日可评估 "
            + str(evaluated_count)
            + " 只 / 策略池 "
            + str(len(pool))
            + " 只"
            + ("，" + "；".join(note_parts) if note_parts else "")
        ),
        "as_observed": receipt.get("as_observed"),
    }


def pool_only_block(
    pool_count: int,
    *,
    delivered_codes: list[str] | None = None,
) -> dict:
    """Synthetic phase: no real-rule receipt exists yet, so only the day's
    pool size is honest.  Per-security rows stay empty - the page shows
    the coverage note and waits for the real-rule runner, never fakes
    verdicts.
    """
    return {
        "schema_version": "p12.replay.coverage-display.v1",
        "pool_count": pool_count,
        "evaluated_count": None,
        "pending_count": None,
        "securities": [
            {
                "security_code": c,
                "evaluated": None,
                "state": "pending",
                "verdict": None,
                "scan_reason": "合成规则阶段，覆盖明细待真实规则接入",
                "entry_eligible": None,
            }
            for c in (delivered_codes or [])
        ],
        "note": "合成规则阶段 · 覆盖明细待真实规则接入",
        "note_text": "策略池 " + str(pool_count) + " 只 · 覆盖明细待真实规则接入",
        "as_observed": None,
    }
