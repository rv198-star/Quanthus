"""Exact existing implementation-plan replay for a new P10 economic view.

No provider vote, /10 fit, invented old revision, or unknown-to-zero rewrite.
Literal full standardized plans describe entitlements; original nullable raw
fields and every native competitor remain in the evidence and exception ledger.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.corporate_action_normalization import _IMPLEMENTATION_PLAN
from quanthus.expert_evaluation.serialization import digest_value

CONTRACT = "p10-retained-implemented-plan-economic-view.v1"
TERMS = ("CASH_AMOUNT_PER_SHARE", "BONUS_SHARES_PER_SHARE", "TRANSFER_SHARES_PER_SHARE")


def _require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def _digest(value: Any) -> str:
    def encode(v: Any) -> Any:
        if isinstance(v, Decimal):
            return {"Decimal": str(v)}
        if isinstance(v, datetime):
            return {"datetime": v.isoformat()}
        if isinstance(v, date):
            return {"date": v.isoformat()}
        if isinstance(v, Mapping):
            return {k: encode(w) for k, w in v.items()}
        if isinstance(v, (tuple, list)):
            return [encode(w) for w in v]
        return v

    return digest_value(encode(value))


def _day(v: Any) -> date:
    _require(
        isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ]00:00:00)?", v) is not None,
        "ACTION_DATE_COLUMN",
    )
    return date.fromisoformat(v[:10])


def literal_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Units come from literal 10-share text, never from observed price fitting."""
    _require(raw.get("ASSIGN_PROGRESS") == "实施分配", "ACTION_NOT_IMPLEMENTED")
    code = raw.get("SECURITY_CODE")
    _require(isinstance(code, str) and re.fullmatch(r"\d{6}", code) is not None, "ACTION_RAW_CODE")
    ex_date, notice = _day(raw.get("EX_DIVIDEND_DATE")), _day(raw.get("NOTICE_DATE"))
    _require(notice < ex_date, "ACTION_NOTICE_DATE")
    text = raw.get("IMPL_PLAN_PROFILE")
    m = _IMPLEMENTATION_PLAN.fullmatch(text) if isinstance(text, str) else None
    _require(m is not None, "ACTION_NONSTANDARD_PLAN")
    assert m is not None and isinstance(text, str)
    # Nonuniform share classes/tax options are not a standard uniform entitlement.
    suffix = text[text.index("(") :] if "(" in text else ""
    _require(
        not suffix or re.fullmatch(r"\(含税(?:,扣税后\d+(?:\.\d+)?元)?\)", suffix) is not None,
        "ACTION_PLAN_QUALIFIER",
    )
    components, nulls = {}, []
    with localcontext() as ctx:
        ctx.prec = 64
        for key, token, term in zip(
            ("PRETAX_BONUS_RMB", "BONUS_RATIO", "IT_RATIO"),
            ("cash", "bonus", "transfer"),
            TERMS,
            strict=True,
        ):
            declared = Decimal(m.group(token) or "0")
            val = raw.get(key)
            if val is None:
                nulls.append(key)
                _require(m.group(token) is None, "ACTION_STRUCTURED_NONZERO_MISSING")
            else:
                _require(
                    not isinstance(val, (bool, float)) and isinstance(val, (Decimal, int, str)),
                    "ACTION_RAW_DECIMAL_REQUIRED",
                )
                value = Decimal(val)
                _require(
                    value.is_finite() and value >= 0 and value == declared,
                    "ACTION_TEXT_STRUCTURED_CONFLICT",
                )
            components[term] = declared / 10
        _require(any(components.values()), "ACTION_EMPTY_PLAN")
    return {
        "stock_code": code,
        "ex_date": ex_date,
        "notice_date": notice,
        "components": components,
        "raw_row_sha256": _digest(raw),
        "null_raw_fields_preserved": nulls,
        "unit": "literal_per_ten_to_exact_per_share",
        "text_sha256": _digest(text),
        "tax_dependent_net_cash_qualified": False,
    }


def native_components(
    action: Mapping[str, Any],
    terms: Sequence[Mapping[str, Any]],
    *,
    retained_versions: bool = False,
) -> dict[str, Decimal]:
    """Partial components stay partial; unsupported class-specific terms fail."""
    values = {}
    for t in terms:
        typ = t["term_type"]
        _require(
            typ in TERMS and typ not in values and t["option_id"] is None,
            "ACTION_TERM_ROLE_AMBIGUOUS",
        )
        _require(
            t["corporate_action_id"] == action["corporate_action_id"]
            and all(
                t[k] == action[k]
                for k in (
                    "record_source",
                    "source_artifact_sha256",
                    "effective_at",
                )
            ),
            "ACTION_TERM_SOURCE_MISMATCH",
        )
        same_version = all(t[k] == action[k] for k in ("source_revision", "knowledge_from"))
        # The published per-ten revision changed only nonzero share terms and
        # their parent's composition. Unchanged cash/zero terms explicitly retain
        # their own earlier version. Do not generalize to unrelated revisions.
        retained = (
            retained_versions
            and action["record_source"] == "dividend_history"
            and re.fullmatch(
                r"p10-base22-native-dividend-perten-ratio-revision-v1:sha256:[0-9a-f]{64}",
                action["source_revision"],
            )
            is not None
            and t["source_revision"] == "dwd-v2-1-gpu-p100-20260810T132353Z-3156b74e-r13"
            and t.get("source_record_id") == action.get("source_record_id")
            and t.get("source_record_id") is not None
            and t["knowledge_from"] < action["knowledge_from"]
            and t.get("knowledge_to") is None
            and (typ == TERMS[0] or t.get("ratio_numerator") == Decimal(0))
        )
        _require(same_version or retained, "ACTION_TERM_SOURCE_MISMATCH")
        if typ == TERMS[0]:
            _require(t["currency"] == "CNY", "ACTION_CURRENCY")
            value = t["amount_per_share"]
        else:
            n, d = t["ratio_numerator"], t["ratio_denominator"]
            _require(
                isinstance(n, Decimal)
                and isinstance(d, Decimal)
                and n.is_finite()
                and d.is_finite()
                and n >= 0
                and d > 0,
                "ACTION_RATIO",
            )
            with localcontext() as ctx:
                ctx.prec = 64
                value = n / d
                _require(value * d == n, "ACTION_INEXACT_RATIO")
        _require(
            isinstance(value, Decimal) and value.is_finite() and value >= 0, "ACTION_TERM_VALUE"
        )
        values[typ] = value
    _require(bool(values), "ACTION_TERMS_EMPTY")
    return values


def bind_plan(
    action: Mapping[str, Any],
    terms: Sequence[Mapping[str, Any]],
    raw: Mapping[str, Any],
    *,
    listing_code: str,
    raw_file_sha256: str,
    raw_fetched_at: datetime,
    qualified_at: datetime,
) -> dict[str, Any]:
    """Reproduce one existing new-source observation; do not choose competing events."""
    p = literal_plan(raw)
    _require(
        action["record_source"] == "p10_retained_eastmoney_implementation"
        and action["source_artifact_sha256"] == raw_file_sha256
        and action["action_status"] == "IMPLEMENTED",
        "ACTION_SELECTED_SOURCE",
    )
    _require(
        p["stock_code"] == listing_code
        and p["ex_date"] == action["ex_date"]
        and action["effective_at"].astimezone(ZoneInfo("Asia/Shanghai")).date() == p["ex_date"],
        "ACTION_DATED_IDENTITY",
    )
    _require(
        all(
            isinstance(t, datetime) and t.utcoffset() is not None
            for t in (raw_fetched_at, qualified_at, action["knowledge_from"])
        )
        and max(raw_fetched_at, action["knowledge_from"]) <= qualified_at,
        "ACTION_TRUE_KNOWLEDGE",
    )
    values = native_components(action, terms)
    _require(
        all(value == p["components"][kind] for kind, value in values.items())
        and all(value == 0 or kind in values for kind, value in p["components"].items()),
        "ACTION_RAW_NATIVE_MISMATCH",
    )
    return {
        "contract": CONTRACT,
        "corporate_action_id": action["corporate_action_id"],
        "security_id": action["security_id"],
        "company_id": action["company_id"],
        "ex_date": action["ex_date"],
        "components": p["components"],
        "raw_file_sha256": raw_file_sha256,
        "raw_row_sha256": p["raw_row_sha256"],
        "native_action_sha256": _digest(action),
        "native_terms_sha256": _digest(list(terms)),
        "raw_fetched_at": raw_fetched_at,
        "native_knowledge_from": action["knowledge_from"],
        "qualified_at": qualified_at,
        "null_raw_fields_preserved": p["null_raw_fields_preserved"],
        "historical_pit_granted": False,
        "old_normalization_restored": False,
        "competing_events_arbitrated": False,
        "consumer_admission_granted": False,
    }


def reconcile_event(
    actions: Sequence[Mapping[str, Any]],
    terms_by_id: Mapping[str, Sequence[Mapping[str, Any]]],
    proofs: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """New economic identity using the frozen exact-key implementation priority.

    Competing qualified sources must agree on every present component. A legacy
    dividend_history cash column can be classified as a per-ten unit defect only
    against the exact replayed implementation and the frozen source-match tolerance.
    This does not close #471, replace native versions, or grant historical PIT.
    """
    _require(
        policy.get("policy_version") == "p1-corporate-action-normalization-20260730-v3"
        and policy.get("authority_precedence") == ["eastmoney_implementation_plan", "factor_ratio"]
        and policy["eastmoney_implementation_plan"]["exact_key"]
        == ["security_code", "effective_date"]
        and policy["eastmoney_implementation_plan"]["cash_per_share_tolerance"] == "0.00001"
        and policy["exception_ledger"]["replay_disposition"] == "security_fail_closed",
        "ACTION_FROZEN_NORMALIZATION_POLICY",
    )
    _require(bool(actions), "ACTION_GROUP_EMPTY")
    identities = {(a["company_id"], a["security_id"], a["ex_date"]) for a in actions}
    _require(len(identities) == 1 and actions[0]["ex_date"] is not None, "ACTION_GROUP_COORDINATE")
    native = {a["corporate_action_id"]: a for a in actions}
    _require(len(native) == len(actions), "ACTION_GROUP_DUPLICATE_ID")
    bound = {p["corporate_action_id"]: p for p in proofs}
    _require(
        bool(bound) and len(bound) == len(proofs) and set(bound) <= set(native),
        "ACTION_COMPLETE_RAW_PLAN_REQUIRED",
    )
    for ident, p in bound.items():
        _require(
            p["contract"] == CONTRACT
            and p["native_action_sha256"] == _digest(native[ident])
            and p["native_terms_sha256"] == _digest(list(terms_by_id[ident]))
            and p["ex_date"] == native[ident]["ex_date"],
            "ACTION_PROOF_NATIVE_BINDING",
        )
    vectors = {tuple(p["components"][t] for t in TERMS) for p in bound.values()}
    _require(len(vectors) == 1, "ACTION_QUALIFIED_RAW_CONFLICT")
    target = dict(zip(TERMS, next(iter(vectors)), strict=True))
    # Different qualified events cannot be summed simply because they share a date.
    dispositions = []
    for ident, a in sorted(native.items()):
        values = native_components(a, terms_by_id[ident], retained_versions=True)
        reason = "EXACT_PRESENT_COMPONENT_AGREEMENT"
        if ident in bound:
            reason = "EXACT_RAW_IMPLEMENTATION_REPLAY"
        else:
            for kind, value in values.items():
                if value == target[kind]:
                    continue
                if (
                    a["record_source"] == "dividend_history"
                    and kind == TERMS[0]
                    and _within_per_ten(value, target[kind])
                ):
                    reason = "LEGACY_PER_TEN_CASH_NEW_PURPOSE_CORRECTION"
                else:
                    raise ValueError("ACTION_PRESENT_COMPONENT_CONFLICT")
        dispositions.append(
            {
                "action_id": ident,
                "record_source": a["record_source"],
                "native_sha256": _digest(a),
                "disposition": reason,
            }
        )
    content = {
        "contract": CONTRACT,
        "security_id": actions[0]["security_id"],
        "company_id": actions[0]["company_id"],
        "effective_date": actions[0]["ex_date"],
        "cash_dividend_per_share": target[TERMS[0]],
        "bonus_share_ratio": _share_total(target),
        "known_at": max(p["qualified_at"] for p in bound.values()),
        "raw_proof_sha256": sorted(_digest(p) for p in bound.values()),
        "native_dispositions": dispositions,
        "policy_sha256": _digest(policy),
        "factor_cross_check_status": "unavailable",
        "historical_pit_granted": False,
        "old_normalization_restored": False,
        "native_history_replaced": False,
        "consumer_admission_granted": False,
    }
    return {**content, "economic_event_id": _digest(content)}


def _within_per_ten(value: Decimal, target: Decimal) -> bool:
    with localcontext() as context:
        context.prec = 64
        return abs(value / 10 - target) <= Decimal("0.00001")


def _share_total(components: Mapping[str, Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = 64
        return components[TERMS[1]] + components[TERMS[2]]


def f10_plans(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Replay the existing F10 literal-unit contract; do not infer absent events.

    Parser boundary retained from base22_f10_dividend at ab261d0. Only complete,
    standard implemented ordinary-share blocks are qualified. Every dated block
    has an explicit disposition; date precision never becomes a publication time.
    """
    import hashlib

    content = raw.get("content")
    _require(raw.get("category") == "分红扩股" and isinstance(content, str), "F10_CATEGORY")
    assert isinstance(content, str)
    _require(len(content.encode()) <= 1024**2, "F10_CONTENT_BOUND")
    _require(
        hashlib.sha256(content.encode()).hexdigest()
        == str(raw.get("content_sha256")).removeprefix("sha256:"),
        "F10_CONTENT_HASH",
    )
    blocks: list[tuple[str, list[str]]] = []
    active = False
    coordinate = None
    lines: list[str] = []
    for line in content.splitlines():
        if line.strip() == "【分红】":
            _require(not active, "F10_MULTIPLE_SECTIONS")
            active = True
            continue
        if active and line.strip().startswith("【"):
            break
        if not active:
            continue
        cells = [v.strip() for v in line.split("｜")[1:-1]]
        if not cells:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            if coordinate is not None:
                blocks.append((coordinate, lines))
            coordinate, lines = cells[0], []
        if coordinate is not None:
            lines.append(cells[1] if len(cells) > 1 else "")
    if coordinate is not None:
        blocks.append((coordinate, lines))
    _require(len(blocks) <= 512, "F10_BLOCK_BOUND")
    number = r"(?:0|[1-9]\d*)(?:\.\d+)?"
    pattern = re.compile(
        rf"(?<![\d.])10(?:(?:送({number}))?(?:转(?:增)?({number}))?(?:派({number})元)?)\(含税\)"
    )
    output = []
    for i, (_coordinate, lines) in enumerate(blocks):
        text = "\n".join(lines).replace(" ", "").replace("\u3000", "")
        result: dict[str, Any] = {"block_ordinal": i, "block_sha256": _digest(lines)}
        try:
            _require("优先股" not in text, "F10_SHARE_CLASS_UNKNOWN")
            _require("进展说明:实施" in text or "进展说明：实施" in text, "F10_NOT_IMPLEMENTED")
            matches = list(pattern.finditer(text))
            _require(len(matches) == 1 and any(matches[0].groups()), "F10_PLAN_AMBIGUOUS")
            dates = {}
            for label, key in [
                ("除权除息日", "ex_date"),
                ("股权登记日", "record_date"),
                ("红利发放日", "payment_date"),
                ("预案公布日", "notice_date"),
            ]:
                values = {
                    date.fromisoformat(d)
                    for d in re.findall(label + r"[:：](\d{4}-\d{2}-\d{2})", text)
                }
                _require(len(values) <= 1, "F10_DATE_CONFLICT")
                dates[key] = next(iter(values)) if values else None
            _require(dates["ex_date"] is not None, "F10_EX_DATE_UNKNOWN")
            tokens = matches[0].groups()
            with localcontext() as ctx:
                ctx.prec = 64
                components = dict(
                    zip(
                        (TERMS[1], TERMS[2], TERMS[0]),
                        [Decimal(x or "0") / 10 for x in tokens],
                        strict=True,
                    )
                )
            result.update(
                **dates,
                components=components,
                disposition="EXACT_LITERAL_PLAN",
                absent_tokens_preserved=[
                    k
                    for k, v in zip((TERMS[1], TERMS[2], TERMS[0]), tokens, strict=True)
                    if v is None
                ],
            )
        except ValueError as exc:
            result["disposition"] = str(exc)
        output.append(result)
    return output


def bind_f10_plan(
    action: Mapping[str, Any],
    terms: Sequence[Mapping[str, Any]],
    raw: Mapping[str, Any],
    *,
    listing_code: str,
    raw_file_sha256: str,
    qualified_at: datetime,
) -> dict[str, Any]:
    _require(
        action["record_source"] == "base22_f10/dividend_implemented"
        and action["source_artifact_sha256"] == raw_file_sha256
        and action["action_status"] == "IMPLEMENTED",
        "F10_NATIVE_SOURCE",
    )
    acquired = datetime.fromisoformat(str(raw.get("acquired_at")).replace("Z", "+00:00"))
    _require(
        acquired.utcoffset() is not None
        and qualified_at.utcoffset() is not None
        and max(acquired, action["knowledge_from"]) <= qualified_at,
        "F10_TRUE_KNOWLEDGE",
    )
    _require(
        raw.get("stock_code") == listing_code and raw.get("security_id") == listing_code,
        "F10_DATED_IDENTITY",
    )
    candidates = [
        p
        for p in f10_plans(raw)
        if p["disposition"] == "EXACT_LITERAL_PLAN" and p["ex_date"] == action["ex_date"]
    ]
    _require(len(candidates) == 1, "F10_EVENT_AMBIGUOUS")
    p = candidates[0]
    _require(
        action["effective_at"].astimezone(ZoneInfo("Asia/Shanghai")).date() == p["ex_date"],
        "F10_EFFECTIVE_DATE",
    )
    values = native_components(action, terms)
    _require(
        all(v == p["components"][k] for k, v in values.items())
        and all(v == 0 or k in values for k, v in p["components"].items()),
        "F10_RAW_NATIVE_MISMATCH",
    )
    return {
        "contract": CONTRACT,
        "proof_family": "f10_literal_implemented_plan",
        "corporate_action_id": action["corporate_action_id"],
        "security_id": action["security_id"],
        "company_id": action["company_id"],
        "ex_date": action["ex_date"],
        "components": p["components"],
        "raw_file_sha256": raw_file_sha256,
        "raw_row_sha256": _digest(raw),
        "raw_block_sha256": p["block_sha256"],
        "raw_block_ordinal": p["block_ordinal"],
        "native_action_sha256": _digest(action),
        "native_terms_sha256": _digest(list(terms)),
        "raw_fetched_at": acquired,
        "native_knowledge_from": action["knowledge_from"],
        "qualified_at": qualified_at,
        "absent_tokens_preserved": p["absent_tokens_preserved"],
        "historical_pit_granted": False,
        "old_normalization_restored": False,
        "competing_events_arbitrated": False,
        "consumer_admission_granted": False,
    }
