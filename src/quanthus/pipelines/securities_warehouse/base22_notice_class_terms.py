"""Retained issuer class entitlements, never a guessed uniform dividend.

Exact implemented class-labelled gross CNY amounts are separate term facts. The
publisher's holder classes are not converted to listing classes, ranks or actual
membership. Averages, compensation equations and virtual ex-price amounts stay
outside this contract. Every original body and scope must be locally replayable.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_notice_action import _DATE_TABLE, _EX_DATE, _IMPLEMENTED_TITLE, MAX_TEXT_BYTES
from .base22_notice_current_scope import _scope_view
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-issuer-explicit-class-gross-cny-terms-v1"
_CLASS_ROLES = {
    "无限售股股东": "SOURCE_UNRESTRICTED_SHAREHOLDERS",
    "限售股股东": "SOURCE_RESTRICTED_SHAREHOLDERS",
    "流通股股东": "SOURCE_FLOATING_SHAREHOLDERS",
    "中小股东": "SOURCE_SMALL_MEDIUM_SHAREHOLDERS",
    "大股东": "SOURCE_LARGE_SHAREHOLDERS",
}
_NUMBER = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_CLAIM = re.compile(
    r"(?P<class>无限售股股东|限售股股东|流通股股东|中小股东|大股东)"
    rf"每股(?:应)?派发现金红利人民币(?P<amount>{_NUMBER})元\(含税(?:\)|[,，;；])"
)
_CALCULATION = re.compile(r"\([二2]\)(?:本次)?差异化分红(?:的)?计算[:：]?")
_NEXT = re.compile(
    r"\([三3]\)(?:每股平均分红计算公式|本次差异化分红除权除息)|"
    r"共计派发现金(?:股利|红利)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?元[。；;]"
    r"平均每股分配金额为"
)


def parse_class_cash_terms(
    text: str,
    *,
    stock_code: str,
    ex_date: date,
    publisher_title: str,
) -> dict[str, Any]:
    if (
        not isinstance(text, str)
        or not 0 < len(text.encode()) <= MAX_TEXT_BYTES
        or not re.fullmatch(r"\d{6}", stock_code)
        or type(ex_date) is not date
        or ex_date > THROUGH
        or not isinstance(publisher_title, str)
    ):
        raise BoundedError("NOTICE_CLASS_TERMS_EXACT_INPUT_INVALID")
    view, layout = _scope_view(text)
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", view))
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", publisher_title))
    if not _IMPLEMENTED_TITLE.search(title) or not re.search(
        r"(?:证券代码|股票代码|A股代码)[:：]?" + stock_code + r"(?!\d)", normalized[:4096]
    ):
        raise BoundedError("NOTICE_CLASS_TERMS_IMPLEMENTED_IDENTITY_REQUIRED")
    # This profile currently admits domestic class definitions only. It cannot
    # bypass H/B entitlement/currency qualification using the publisher code.
    if any(s in normalized for s in ("H股", "B股", "美元", "港币", "港元")):
        raise BoundedError("NOTICE_CLASS_TERMS_OTHER_MARKET_SCOPE_UNQUALIFIED")
    dates = set()
    for match in _EX_DATE.finditer(normalized):
        try:
            dates.add(date(*(int(match[n]) for n in ("year", "month", "day"))))
        except ValueError:
            raise BoundedError("NOTICE_CLASS_TERMS_DATE_TOKEN_INVALID") from None
    for match in _DATE_TABLE.finditer(normalized):
        dates.add(date(*(int(n) for n in match["ex"].split("/"))))
    if dates != {ex_date}:
        raise BoundedError("NOTICE_CLASS_TERMS_EXACT_EX_DATE_REQUIRED")
    lines = view.splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    blocks: list[dict[str, Any]] = []
    for index, (start, name) in enumerate(headings):
        if name not in {"分配方案", "利润分配方案", "权益分派方案"}:
            continue
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        whole = re.sub(r"\s+", "", "\n".join(lines[start + 1 : end]))
        calculations = list(_CALCULATION.finditer(whole))
        if len(calculations) != 1 or "差异化分红" not in whole:
            continue
        begin = calculations[0].end()
        next_block = _NEXT.search(whole, begin)
        if next_block is None:
            raise BoundedError("NOTICE_CLASS_TERMS_COMPLETE_CURRENT_CALCULATION_BOUNDARY_REQUIRED")
        selected = whole[begin : next_block.start()]
        if "根据上述计算额度进行补偿后" not in selected:
            raise BoundedError("NOTICE_CLASS_TERMS_CURRENT_AFTER_COMPENSATION_SCOPE_REQUIRED")
        claims = list(_CLAIM.finditer(selected))
        roles: dict[str, dict[str, Any]] = {}
        with localcontext() as context:
            context.prec = 64
            for claim in claims:
                if re.search(
                    r"(?:非|部分|某|少数)$", selected[max(0, claim.start() - 4) : claim.start()]
                ):
                    raise BoundedError("NOTICE_CLASS_TERMS_QUALIFIED_SUBCLASS_SCOPE_UNAVAILABLE")
                label, token = claim["class"], claim["amount"]
                value = Decimal(token)
                if not decimal_profile(value, precision=38, scale=10)["fits"]:
                    raise BoundedError("NOTICE_CLASS_TERMS_EXACT_DECIMAL_REQUIRED")
                # Explicit zero is a real class entitlement, not unavailable.
                if label in roles and roles[label]["amount_per_share"] != value:
                    raise BoundedError("NOTICE_CLASS_TERMS_CONFLICTING_CURRENT_CLASS_AMOUNT")
                roles[label] = {
                    "source_class_label": label,
                    "class_role": _CLASS_ROLES[label],
                    "amount_per_share": value,
                    "currency": "CNY",
                    "tax_basis": "EXPLICIT_GROSS",
                    "original_numeric_token_sha256": digest(token),
                    "original_claim_sha256": digest(claim[0]),
                }
        expected = {
            label
            for label in _CLASS_ROLES
            if re.search(r"(?<!无)" + re.escape(label) + r"每股", selected)
        }
        if len(roles) != 2 or set(roles) != expected:
            raise BoundedError("NOTICE_CLASS_TERMS_ALL_LITERAL_CURRENT_CLASSES_REQUIRED")
        # Only explicitly paired classes, never an invented complement/aggregate.
        if frozenset(roles) not in {
            frozenset({"限售股股东", "流通股股东"}),
            frozenset({"限售股股东", "无限售股股东"}),
            frozenset({"大股东", "中小股东"}),
        }:
            raise BoundedError("NOTICE_CLASS_TERMS_CLASS_PAIR_SEMANTICS_UNQUALIFIED")
        blocks.append(
            {
                "heading_sha256": digest(name),
                "whole_scheme_sha256": digest(whole),
                "current_class_calculation_sha256": digest(selected),
                "terms": sorted(roles.values(), key=lambda r: r["class_role"]),
            }
        )
    if len(blocks) != 1:
        raise BoundedError("NOTICE_CLASS_TERMS_UNIQUE_IMPLEMENTED_CLASS_SCHEME_REQUIRED")
    result = {
        "contract_id": CONTRACT_ID,
        "stock_code": stock_code,
        "ex_date": ex_date,
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(publisher_title),
        "layout_identity": layout["identity"],
        "scheme": blocks[0],
        "general_cash_scalar_selected": False,
        "virtual_or_average_cash_computed": False,
        "actual_holder_membership_inferred": False,
        "historical_PIT_admission": False,
        "old_uniform_term_replacement": "SEPARATE_CANDIDATE_MODEL_MIGRATION_GATE",
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result


def select_class_cash_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Require agreement from every retained primary, with no document cherry picking."""
    indexed = {t["action_id"]: t for t in targets}
    if not 0 < len(targets) <= 256 or len(indexed) != len(targets):
        raise BoundedError("NOTICE_CLASS_TERMS_EXACT_TARGET_SET_INVALID")
    candidates: dict[str, list[dict[str, Any]]] = {k: [] for k in indexed}
    blocked: set[str] = set()
    seen: set[str] = set()
    diagnostics = []
    for document in documents:
        doc, keys = document["document_id"], document["target_action_ids"]
        if (
            doc in seen
            or len(seen) >= 256
            or not keys
            or len(set(keys)) != len(keys)
            or not set(keys) <= set(indexed)
        ):
            raise BoundedError("NOTICE_CLASS_TERMS_DOCUMENT_BINDING_INVALID")
        seen.add(doc)
        title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
        primary = _IMPLEMENTED_TITLE.search(title) is not None
        for key in keys:
            target = indexed[key]
            item = {"document_id": doc, "action_id": key, "primary_implementation": primary}
            try:
                if document["publisher_security_code"] != target["stock_code"]:
                    raise BoundedError("NOTICE_CLASS_TERMS_PUBLISHER_SECURITY_MISMATCH")
                proof = parse_class_cash_terms(
                    document["text"],
                    stock_code=target["stock_code"],
                    ex_date=date.fromisoformat(target["ex_date"]),
                    publisher_title=document["publisher_title"],
                )
                semantic = digest(
                    [
                        [
                            t["class_role"],
                            t["source_class_label"],
                            t["amount_per_share"],
                            t["currency"],
                            t["tax_basis"],
                        ]
                        for t in proof["scheme"]["terms"]
                    ]
                )
                candidates[key].append(
                    {"document_id": doc, "proof": proof, "class_terms_sha256": semantic}
                )
                item.update(status="EXACT_LITERAL_CLASS_TERMS", terms_sha256=semantic)
            except BoundedError as exc:
                item.update(status="HELD", reason=str(exc))
                if primary or str(exc) == "NOTICE_CLASS_TERMS_PUBLISHER_SECURITY_MISMATCH":
                    blocked.add(key)
            diagnostics.append(item)
    selected, dispositions = {}, {}
    for key in sorted(indexed):
        observations = candidates[key]
        if key in blocked:
            dispositions[key] = "PRIMARY_CLASS_SCOPE_UNRESOLVED"
        elif not observations:
            dispositions[key] = "NO_EXACT_PRIMARY_CLASS_TERMS"
        elif len({o["class_terms_sha256"] for o in observations}) != 1:
            dispositions[key] = "PRIMARY_CLASS_TERMS_CONFLICT"
        else:
            selected[key] = sorted(observations, key=lambda o: o["document_id"])[0]
            dispositions[key] = "EXACT_PRIMARY_LITERAL_CLASS_TERMS"
    evidence = {
        "contract_id": CONTRACT_ID,
        "exact_target_keys": sorted(indexed),
        "exact_documents": sorted(seen),
        "document_dispositions": diagnostics,
        "key_dispositions": dispositions,
        "uniform_cash_inferred": False,
        "actual_holder_membership_inferred": False,
        "old_normalization_manifest_restored": False,
        "historical_PIT_admission": False,
    }
    evidence["identity"] = digest(evidence)
    return selected, evidence
