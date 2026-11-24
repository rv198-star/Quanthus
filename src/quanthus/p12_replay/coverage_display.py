"""Replay-day coverage display (issue #748 real-rule intake round).

Owner reads one replay day as "evaluable N / strategy pool M, the rest
pending dividend/bonus data clearance", then a per-security verdict and
the rule's own scan reason.  The data comes from ONE day's real runner
receipt - never a re-summary, never a fabricated pool.

Two carrier shapes feed the block, both read verbatim:

* A same-day ``p11.paper-replay-day.v1`` receipt (the dict
  run_e1_replay_day / run_replay_day returns): top-level
  ``delivered`` / ``seam`` / ``coverage_exclusions`` /
  ``excluded_security_codes`` plus ``source_closure.held_decisions``
  and ``source_closure.history_receipts``.
* A P12 run dict carrying ``source_closure`` and/or ``evaluation_receipt``
  (what run_daily persists once the runner produced output): the same
  fields live under ``source_closure`` / ``evaluation_receipt``.
  An INPUT_BLOCKED run dict carries neither, so the block falls back to
  the pool-only display rather than reporting a fake 0/0.

A security counts as evaluable only when the same run actually
evaluated it.  The pool (M) is the day's delivered/strategy-pool set;
a held review outside the pool keeps its verdict on its own row but
never inflates the pool or the evaluated count.  Zero-signal days
still list every evaluated security - coverage is about the scan,
not the advice.
"""

from __future__ import annotations

from typing import Any, Mapping

from quanthus.platform.p12.contract_generated import REASON_TEXTS

_CJK_LO, _CJK_HI = "\u4e00", "\u9fff"


def _has_cjk(text: str) -> bool:
    return any(_CJK_LO <= ch <= _CJK_HI for ch in text)


def _cn_reason(code: Any) -> str | None:
    """Map one internal code to a Chinese line; never leak the code.

    A natural-language reason (the rule's own scan explanation) passes
    through verbatim - it is the "理由原文" the Owner reads.  A bare
    reason CODE maps through REASON_TEXTS; an unregistered code degrades
    to the generic label rather than leaking the identifier.
    """
    if code is None:
        return None
    text = str(code).strip()
    if not text:
        return None
    mapped = REASON_TEXTS.get(text.upper())
    if mapped:
        return mapped
    # A CJK / sentence-form reason is already Owner-readable; pass it.
    if _has_cjk(text) or " " in text:
        return text
    return "详见记录页"


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


_CLOCK_MARKERS = (
    "knowledge_after_t_close",
    "after_t_close",
    "t_close",
)


def _clock_blocked_reason(gate_receipt: Mapping[str, Any] | None) -> str | None:
    """The security's scan verdict needs T-close knowledge: a CLOCK block.

    Returns the gate's own reason line when the event/feature/listing gate
    failed on a clock dependency (e.g. ``scan verdict at T:
    knowledge_after_t_close``); None when it was scanned normally - a
    ``no qualified event window`` failure is a real evaluated-no-signal,
    never a clock block.
    """
    if not isinstance(gate_receipt, Mapping):
        return None
    gates = gate_receipt.get("gates") or {}
    for gate in ("events", "features", "listing"):
        g = gates.get(gate)
        if not isinstance(g, Mapping):
            continue
        reason = str(g.get("reason") or "")
        if any(m in reason.lower() for m in _CLOCK_MARKERS):
            return reason
        # a failed status whose reason names a clock dependency
        if str(g.get("status") or "").lower() == "failed" and (
            "after" in reason.lower() and "close" in reason.lower()
        ):
            return reason
    return None



_CHECK_LABELS = {
    "quiet_range_ratio_max": "前日振幅",
    "quiet_turnover_ratio_max": "前日成交额",
    "active_range_ratio_min": "当日振幅",
    "active_turnover_ratio_min": "当日成交额",
}


def _ratio_text(check: Mapping[str, Any]) -> str | None:
    """'626587/367434' -> '1.7053x' - actual as a plain multiple."""
    actual = str(check.get("actual") or "")
    if "/" not in actual:
        return None
    num_s, den_s = actual.split("/", 1)
    try:
        from fractions import Fraction
        ratio = float(Fraction(num_s) / Fraction(den_s))
    except (ValueError, ZeroDivisionError):
        return None
    return f"{ratio:.4f}x"


def _check_label(key: str, check: Mapping[str, Any]) -> str:
    """'active_range_ratio_min' + '>=3/2' -> '当日振幅 ≥1.50x'."""
    base = _CHECK_LABELS.get(key) or key
    op = str(check.get("op") or "")
    threshold = str(check.get("threshold") or "")
    if "/" in threshold:
        try:
            from fractions import Fraction
            threshold = f"{float(Fraction(threshold)):.2f}"
        except ValueError:
            pass
    symbol = {"<=": "≤", ">=": "≥", "<": "<", ">": ">", "==": "="}.get(op, op)
    return f"{base} {symbol}{threshold}x" if threshold else base


def _scan_details_from_package(receipt: Mapping[str, Any]) -> dict[str, dict]:
    """security_code -> the day's per-security scan detail.

    Reads the published package's decision_sets[0].audit.replay_no_action
    bound to the same day receipt (P11 v2 package carried on the
    p11.paper-replay-day.v1 day dict).  Each scan holds the three
    admission gates plus the four transition checks with raw values - the
    evidence the Owner reads on a qualified no-signal day.  Returns {} when
    no replay_no_action is bound to this package.
    """
    package = receipt.get("package")
    if not isinstance(package, Mapping):
        return {}
    decision_sets = package.get("decision_sets") or ()
    if not isinstance(decision_sets, (list, tuple)) or not decision_sets:
        return {}
    audit = (
        decision_sets[0].get("audit")
        if isinstance(decision_sets[0], Mapping)
        else None
    )
    if not isinstance(audit, Mapping):
        return {}
    rna = audit.get("replay_no_action")
    if not isinstance(rna, Mapping):
        return {}
    out: dict[str, dict] = {}
    for scan in rna.get("scans") or ():
        if not isinstance(scan, Mapping):
            continue
        code = str(scan.get("security_code") or "")
        if not code:
            continue
        gates = scan.get("gates") or {}
        gate_lines = []
        for name, label in (
            ("events", "事件窗"),
            ("features", "特征"),
            ("listing", "上市"),
        ):
            g = gates.get(name)
            if not isinstance(g, Mapping):
                continue
            observed = g.get("observed")
            required = g.get("required")
            mark = "✓" if str(g.get("status") or "") == "satisfied" else "✗"
            gate_lines.append(f"{label} {observed}/{required} {mark}")
        transition = scan.get("transition") or {}
        checks = []
        for key, check in (transition.get("checks") or {}).items():
            if not isinstance(check, Mapping):
                continue
            checks.append(
                {
                    "key": key,
                    "label": _check_label(key, check),
                    "actual": _ratio_text(check)
                    or str(check.get("actual") or ""),
                    "passed": bool(check.get("passed")),
                }
            )
        out[code] = {
            "verdict": scan.get("verdict"),
            "gates": gate_lines,
            "checks": checks,
            "prior_session": transition.get("prior_session"),
            "signal_date": transition.get("signal_date"),
            "prior_quiet": transition.get("prior_quiet"),
            "current_active": transition.get("current_active"),
            "message": rna.get("message"),
            "candidate_count": rna.get("candidate_count"),
        }
    return out


def _scan_reason_for(
    code: str,
    *,
    decision: Mapping[str, Any] | None,
    held_row: Mapping[str, Any] | None,
    gate_receipt: Mapping[str, Any] | None,
    coverage_excl: Mapping[str, Any] | None,
) -> str | None:
    """The rule's own scan explanation for one security, verbatim.

    Preference order: the coverage-isolation reason, the decision's
    reason, the held row's reason, then the history-gate event scan
    receipt (gates.events.reason) - the last carries the "no qualified
    event in the N-session window" line for a scanned-but-no-event
    security, which is the evidence the Owner reads on a zero-signal day.
    """
    if coverage_excl is not None:
        r = _cn_reason(coverage_excl.get("reason")) or _cn_reason(
            coverage_excl.get("member_or_field")
        )
        if r:
            return r
    for source in (decision, held_row):
        if source is not None:
            for key in ("reason", "unavailable_reason", "reason_codes"):
                value = source.get(key)
                if isinstance(value, (list, tuple)):
                    rendered = [_cn_reason(v) for v in value]
                    rendered = [x for x in rendered if x]
                    if rendered:
                        return "；".join(rendered)
                elif value:
                    return _cn_reason(value)
    if gate_receipt is not None:
        gates = gate_receipt.get("gates") or {}
        for gate in ("events", "features", "listing"):
            g = gates.get(gate)
            if isinstance(g, Mapping) and g.get("reason"):
                r = _cn_reason(g.get("reason"))
                if r:
                    return r
    return None



def _day_block_of(
    receipt: Mapping[str, Any],
    evaluation_receipt: Mapping[str, Any],
    seam: Mapping[str, Any],
    source_closure: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """The whole-day block from whichever carrier actually declares one.

    A p11 receipt may keep it on ``day_block``; a blocked day that never
    produced a qualified package instead marks ``seam.day_blocked`` /
    ``source_closure.day_blocked`` + ``disposition`` (e.g. input_not_ready)
    and explains itself on ``diagnostics`` / ``day_block_reason``.  Reads
    them in that order so a real INPUT_BLOCKED day shows its own reason,
    never a silent 0/0 (协调: 整日输入不足 without a top day_block).
    """
    block = receipt.get("day_block") or evaluation_receipt.get("day_block")
    if isinstance(block, Mapping) and (block.get("reason") or block.get("detail")):
        return block
    flagged = (
        seam.get("day_blocked")
        or source_closure.get("day_blocked")
        or str(receipt.get("disposition") or "").lower()
        in ("input_not_ready", "blocked", "day_blocked")
        or str(source_closure.get("account_disposition") or "").lower()
        in ("input_not_ready", "blocked", "day_blocked")
    )
    if not flagged:
        return block if isinstance(block, Mapping) else None
    reason = (
        source_closure.get("day_block_reason")
        or (block.get("reason") if isinstance(block, Mapping) else None)
        or receipt.get("disposition")
        or source_closure.get("account_disposition")
    )
    detail = None
    diags = receipt.get("diagnostics") or source_closure.get("diagnostics") or ()
    if isinstance(diags, (list, tuple)):
        for d in diags:
            if isinstance(d, Mapping) and str(d.get("scope") or "") == "whole_day":
                detail = d.get("reason") or d.get("detail")
                break
        else:
            for d in diags:
                if isinstance(d, Mapping) and (d.get("reason") or d.get("detail")):
                    detail = d.get("reason") or d.get("detail")
                    break
    out = dict(block) if isinstance(block, Mapping) else {}
    if reason and not out.get("reason"):
        out["reason"] = reason
    if detail and not out.get("detail"):
        out["detail"] = detail
    out.setdefault("scope", "whole_day")
    return out or None


def coverage_block(receipt: Mapping[str, Any] | None) -> dict | None:
    """Build the display block from one same-run day receipt.

    Accepts either the ``p11.paper-replay-day.v1`` dict or a P12 run
    dict carrying ``source_closure``/``evaluation_receipt``.  Returns
    None when neither carrier has coverage evidence - the caller then
    shows the pool-only block.
    """
    if not receipt or not isinstance(receipt, Mapping):
        return None

    source_closure = receipt.get("source_closure") or {}
    evaluation_receipt = receipt.get("evaluation_receipt") or {}
    seam = receipt.get("seam") or evaluation_receipt.get("seam") or {}
    delivered = (
        receipt.get("delivered")
        or evaluation_receipt.get("delivered")
        or {}
    )

    # Pool (M): the day's delivered/strategy-pool set.  Under a
    # paper-replay receipt it is the ``delivered`` map; otherwise fall
    # back to the union of evaluated universe + coverage isolations +
    # per-security gate receipts - the securities the run actually
    # addressed on entry, never the held set.
    coverage_exclusions = list(
        receipt.get("coverage_exclusions")
        or evaluation_receipt.get("coverage_exclusions")
        or source_closure.get("coverage_exclusions")
        or ()
    )
    # Project the security key from each exclusion entry BEFORE dedup -
    # source_closure.exclusions is a list of objects; set() on the raw
    # dicts raises TypeError (unhashable).  Strings and mappings both map
    # to their code so an empty-737 day with per-security exclusions
    # still resolves.
    def _code_of(entry: Any) -> str | None:
        if isinstance(entry, str):
            return entry
        if isinstance(entry, Mapping):
            v = entry.get("security_code") or entry.get("listing_code")
            return str(v) if v else None
        return None

    excluded_codes = {
        c
        for c in (
            _code_of(e)
            for e in (
                list(receipt.get("excluded_security_codes") or ())
                + list(source_closure.get("exclusions") or ())
            )
        )
        if c
    }

    evaluated = set(
        seam.get("evaluated_universe")
        or evaluation_receipt.get("evaluated_universe")
        or source_closure.get("evaluated_universe")
        or ()
    )
    gate_receipts = {
        str(h.get("security_code")): h
        for h in (source_closure.get("history_receipts") or ())
        if isinstance(h, Mapping) and h.get("security_code")
    }

    denominator_known = bool(delivered)
    if delivered:
        pool = sorted(delivered.keys())
    else:
        coverage_codes = {
            str(e.get("security_code"))
            for e in coverage_exclusions
            if isinstance(e, Mapping) and e.get("security_code")
        }
        pool = sorted(evaluated | coverage_codes | set(gate_receipts))

    # No coverage carrier at all -> caller falls back to pool-only.
    has_evidence = bool(
        delivered
        or seam
        or source_closure.get("evaluated_universe")
        or source_closure.get("history_receipts")
        or coverage_exclusions
    )
    if not has_evidence:
        return None

    decisions = {
        str(d.get("security_code")): d
        for d in (seam.get("decisions") or ())
        if isinstance(d, Mapping) and d.get("security_code")
    }
    held = {
        str(h.get("security_code")): h
        for h in (source_closure.get("held_decisions") or ())
        if isinstance(h, Mapping) and h.get("security_code")
    }
    coverage_excl = {
        str(e.get("security_code")): e
        for e in coverage_exclusions
        if isinstance(e, Mapping) and e.get("security_code")
    }
    scan_details = _scan_details_from_package(receipt)

    rows = []
    for code in pool:
        delivery = delivered.get(code) or {}
        eligible = bool(delivery.get("entry_eligible", True)) and code not in coverage_excl
        decision = decisions.get(code)
        held_row = held.get(code)
        gate_receipt = gate_receipts.get(code)
        # A scan verdict that needs T-close knowledge is a CLOCK block, not
        # a qualified evaluated-no-signal: it keeps its own clock reason and
        # never counts into N (协调: knowledge_after_t_close).
        clock_reason = _clock_blocked_reason(gate_receipt)
        # An excluded/not_qualified security is never "evaluated": the run
        # addressed it (it sits in evaluated_universe) but its own receipt
        # marks it excluded or not qualified, so it keeps its real status
        # out of N - only a genuinely scanned-and-not-signal security
        # counts (协调: 已评估且未命中信号照常计 N; 时钟阻断/排除不计).
        # "excluded" marks a removed security (not_listed etc); "not_qualified"
        # still counts - a no-qualified-event scan IS an evaluated no-signal.
        hr_status = str(
            (gate_receipt or {}).get("status") or ""
        ).lower()
        qualified = hr_status != "excluded"
        is_evaluated = (
            code in evaluated
            and clock_reason is None
            and qualified
        )
        if clock_reason is not None:
            state = "blocked"
        elif not eligible or (code in coverage_excl and not is_evaluated):
            state = "pending"
        elif is_evaluated:
            state = "evaluated"
        else:
            state = "pending"
        verdict = None
        if clock_reason is not None:
            verdict = "时钟阻断"
        elif decision is not None:
            verdict = _disposition_label(decision.get("disposition"))
        elif held_row is not None:
            action = str(held_row.get("action") or "")
            verdict = {
                "held": "继续持有",
                "reduce": "减仓建议",
                "exit": "退出建议",
                "hold_alert": "持仓提醒",
            }.get(action, action or "持仓评估")
        elif is_evaluated:
            verdict = "未通过入场"
        scan = _scan_reason_for(
            code,
            decision=decision,
            held_row=held_row,
            gate_receipt=gate_receipt,
            coverage_excl=coverage_excl.get(code),
        )
        if clock_reason is not None:
            scan = clock_reason
        elif scan is None and state == "pending":
            scan = "公司行为/分红送股数据未核清" if not eligible else "输入未交付至评估"
        rows.append(
            {
                "security_code": code,
                "evaluated": state == "evaluated",
                "state": state,
                "verdict": verdict,
                "scan_reason": scan,
                "entry_eligible": eligible,
                "in_pool": True,
                "scan_detail": scan_details.get(code),
            }
        )

    # Held members outside the delivered pool keep their own row; they are
    # not counted into M or N - the pool is the day's entry universe only.
    for code in sorted(evaluated - set(pool)):
        held_row = held.get(code)
        decision = decisions.get(code)
        verdict = None
        if decision is not None:
            verdict = _disposition_label(decision.get("disposition"))
        elif held_row is not None:
            verdict = "持仓评估"
        scan = _scan_reason_for(
            code,
            decision=decision,
            held_row=held_row,
            gate_receipt=gate_receipts.get(code),
            coverage_excl=coverage_excl.get(code),
        )
        rows.append(
            {
                "security_code": code,
                "evaluated": False,
                "state": "held",
                "verdict": verdict,
                "scan_reason": scan,
                "entry_eligible": False,
                "in_pool": False,
                "scan_detail": scan_details.get(code),
            }
        )

    evaluated_count = sum(1 for r in rows if r["evaluated"])
    pending = [r for r in rows if r["state"] == "pending"]
    note_parts = []
    if not denominator_known:
        # No authoritative delivered map: the pool size is unknown, not
        # the number of per-security receipts.  Show the actual state -
        # blocked when the day carried a block, else coverage-pending -
        # while the per-security rows stay readable as evidence.
        day_block = _day_block_of(receipt, evaluation_receipt, seam, source_closure)
        block_reason = None
        if isinstance(day_block, Mapping):
            block_reason = day_block.get("reason") or day_block.get("detail")
        state_label = (
            "已阻断" if block_reason else "覆盖待齐"
        )
        note_parts.insert(
            0,
            state_label
            + ("：" + str(_cn_reason(block_reason) or block_reason) if block_reason else "")
            + "，策略池分母待同日权威回执",
        )
        return {
            "schema_version": "p12.replay.coverage-display.v1",
            "pool_count": None,
            "evaluated_count": evaluated_count,
            "pending_count": len(pending),
            "denominator_known": False,
            "day_block": day_block,
            "securities": rows,
            "note": "；".join(note_parts),
            "note_text": (
                "本日可评估 "
                + str(evaluated_count)
                + " 只 / "
                + state_label
                + "（策略池分母待同日权威回执）"
                + ("，" + "；".join(note_parts[1:]) if len(note_parts) > 1 else "")
            ),
            "as_observed": receipt.get("as_observed"),
        }
    # A delivered day can STILL carry a whole-day block (e.g.
    # calendar_coverage_incomplete): the per-security pool is real, but the
    # day's own block reason must stay on the note, never drop to a bare
    # N/M line (协调 R6 item 3).
    day_block = _day_block_of(receipt, evaluation_receipt, seam, source_closure)
    block_reason = None
    if isinstance(day_block, Mapping):
        # The actual reason the day blocked: P11's natural-language detail
        # first (it is the real explanation), then the reason code through
        # the CN map - an unmapped code keeps the raw reason, never the
        # "详见记录页" placeholder for a whole-day block the Owner reads.
        detail = day_block.get("detail")
        code = day_block.get("reason")
        if detail and (_has_cjk(str(detail)) or " " in str(detail)):
            block_reason = str(detail)
        elif code:
            mapped = _cn_reason(code)
            block_reason = mapped if mapped != "详见记录页" else str(code)
        elif detail:
            block_reason = str(detail)
    if block_reason:
        note_parts.insert(
            0,
            "整日阻断：" + str(block_reason),
        )
    if pending:
        note_parts.append(
            "其余 " + str(len(pending)) + " 只待分红送股数据核清或输入未交付"
        )
    if excluded_codes:
        note_parts.append("当日排除 " + str(len(excluded_codes)) + " 只")
    return {
        "schema_version": "p12.replay.coverage-display.v1",
        "pool_count": len(pool),
        "evaluated_count": evaluated_count,
        "pending_count": len(pending),
        "day_block": day_block,
        "securities": rows,
        "replay_message": next(
            (d.get("message") for d in scan_details.values() if d.get("message")),
            None,
        ),
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
    """Synthetic / no-evidence phase: only the day's pool size is honest.

    Per-security rows stay empty - the page shows the coverage note and
    waits for the real-rule runner, never fakes verdicts.
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
                "in_pool": True,
            }
            for c in (delivered_codes or [])
        ],
        "note": "合成规则阶段 · 覆盖明细待真实规则接入",
        "note_text": "策略池 " + str(pool_count) + " 只 · 覆盖明细待真实规则接入",
        "as_observed": None,
    }
