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

    rows = []
    for code in pool:
        delivery = delivered.get(code) or {}
        eligible = bool(delivery.get("entry_eligible", True)) and code not in coverage_excl
        decision = decisions.get(code)
        held_row = held.get(code)
        is_evaluated = code in evaluated
        if not eligible or (code in coverage_excl and not is_evaluated):
            state = "pending"
        elif is_evaluated:
            state = "evaluated"
        else:
            state = "pending"
        verdict = None
        if decision is not None:
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
            gate_receipt=gate_receipts.get(code),
            coverage_excl=coverage_excl.get(code),
        )
        if scan is None and state == "pending":
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
        day_block = receipt.get("day_block") or evaluation_receipt.get("day_block")
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
    day_block = receipt.get("day_block") or evaluation_receipt.get("day_block")
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
