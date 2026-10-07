"""Exact implemented cash atoms from an identified mainland issuer notice.

The caller binds the original PDF, publisher detail response, acquisition and
dated security parent. This parser never selects a source priority or a PIT time.
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
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-mainland-issuer-implemented-cash-notice-v1"
MAX_TEXT_BYTES = 1024 * 1024
_NUMBER = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_CASH = re.compile(
    rf"每(?P<denominator>10|1)?股(?:派发|派送|派)?"
    rf"(?:(?:现金红利|现金股利|现金|红利|股利)(?:人民币)?"
    rf"(?P<amount>{_NUMBER})元(?:现金红利|人民币)?|"
    rf"(?P<post_amount>{_NUMBER})元(?:人民币)?(?:现金红利|现金股利|现金))"
    rf"\(含税(?:\)|[,，;；])"
)
_EX_DATE = re.compile(
    r"(?:除权除息日|除权\(息\)日|除权日|除息日)(?:期)?(?:为|是)?[:：]?"
    r"(?P<year>\d{4})(?:年|[-/])(?P<month>\d{1,2})(?:月|[-/])"
    r"(?P<day>\d{1,2})日?"
)
_SLASH_DATE = r"\d{4}/\d{1,2}/\d{1,2}"
_DATE_TABLE = re.compile(
    r"股份类别股权登记日最后交易日除权\(息\)日现金红利发放日"
    rf"A股(?P<record>{_SLASH_DATE})-(?P<ex>{_SLASH_DATE})(?P<pay>{_SLASH_DATE})"
)
_IMPLEMENTED_TITLE = re.compile(
    r"(?:权益分派实施(?:的)?公告|利润分配(?:方案)?实施(?:的)?公告|"
    r"分红派息(?:方案)?实施公告|派发现金红利实施公告)"
)


def parse_implemented_notice_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    """Require an explicit implemented notice, security, ex-date and gross unit.

    Within an identified mainland ordinary-share notice, unqualified '元' is the
    existing CNY cash unit. Mixed H/B/foreign-currency documents are withheld; no
    currency, tax, rounding or per-ten tolerance is inferred to fit other feeds.
    Repeated identical atoms are allowed, conflicting atoms block the document.
    """
    if (
        not isinstance(text, str)
        or not 0 < len(text.encode("utf-8")) <= MAX_TEXT_BYTES
        or not re.fullmatch(r"\d{6}", stock_code)
        or not isinstance(ex_date, date)
        or not isinstance(publisher_title, str)
    ):
        raise BoundedError("NOTICE_IMPLEMENTED_CASH_INPUT_INVALID")
    if ex_date > THROUGH:
        raise BoundedError("NOTICE_BUSINESS_DATE_AFTER_IMAGE_CUTOFF")
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", publisher_title))
    if _IMPLEMENTED_TITLE.search(title) is None:
        raise BoundedError("NOTICE_IMPLEMENTED_STATUS_NOT_ESTABLISHED")
    if not re.search(
        r"(?:证券代码|股票代码|A股代码)[:：]?" + stock_code + r"(?!\d)", normalized[:4096]
    ):
        raise BoundedError("NOTICE_EXACT_SECURITY_CODE_NOT_ESTABLISHED")
    if any(marker in normalized for marker in ("H股", "B股", "港币", "港元", "美元")):
        raise BoundedError("NOTICE_MIXED_SECURITY_OR_CURRENCY_SCOPE_UNQUALIFIED")
    dates = set()
    for matched in _EX_DATE.finditer(normalized):
        try:
            dates.add(date(*(int(matched[name]) for name in ("year", "month", "day"))))
        except ValueError:
            raise BoundedError("NOTICE_EX_DATE_TOKEN_INVALID") from None
    # This exact exchange table grammar has four explicitly ordered columns.
    # A lone date near a heading, or a row without the full header, is not evidence.
    for matched in _DATE_TABLE.finditer(normalized):
        try:
            parsed = {
                name: date(*(int(v) for v in matched[name].split("/")))
                for name in ("record", "ex", "pay")
            }
        except ValueError:
            raise BoundedError("NOTICE_EX_DATE_TOKEN_INVALID") from None
        dates.add(parsed["ex"])
    if dates != {ex_date}:
        raise BoundedError("NOTICE_EXACT_EX_DATE_NOT_ESTABLISHED")
    atoms = []
    with localcontext() as context:
        context.prec = 64
        for matched in _CASH.finditer(normalized):
            denominator = int(matched["denominator"] or "1")
            token = matched["amount"] or matched["post_amount"]
            number = Decimal(token)
            amount = number / Decimal(denominator)
            if not amount.is_finite() or amount <= 0:
                raise BoundedError("NOTICE_POSITIVE_GROSS_CASH_NOT_ESTABLISHED")
            if not decimal_profile(amount, precision=38, scale=10)["fits"]:
                raise BoundedError("NOTICE_CASH_NOT_EXACTLY_REPRESENTABLE")
            atoms.append((amount, denominator, token))
    values = {atom[0] for atom in atoms}
    if len(values) != 1:
        raise BoundedError("NOTICE_UNIQUE_GROSS_CASH_ATOM_NOT_ESTABLISHED")
    amount = next(iter(values))
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": stock_code,
        "ex_date": ex_date,
        "currency": "CNY",
        "cash_per_share": amount,
        "source_unit": "explicit_per_one_or_ten_shares_gross_CNY",
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(publisher_title),
        "cash_atom_sha256": digest(["CNY", amount]),
        "matched_tokens": [
            {"original_numeric_token_sha256": digest(token), "source_denominator": denominator}
            for _, denominator, token in atoms
        ],
        "source_priority_granted": False,
        "historical_publication_or_acquisition_inferred": False,
        "other_action_terms_qualified": False,
    }


def select_notice_cash_candidates(
    targets: list[dict[str, Any]],
    documents: Iterable[dict[str, Any]],
    *,
    parser_profile: str = CONTRACT_ID,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """All primary implementation candidates must agree; opinions grant no priority.

    Documents are the exact frozen retained URI selection, never a search result
    chosen to fit a value. An unparseable primary implementation for a key blocks
    that key even if another document parses. Ancillary opinions stay disclosed.
    Returned atoms are private; only the disposition evidence is safe to export.
    """
    from .base22_notice_adjustment import CONTRACT_ID as ADJUSTMENT_CONTRACT
    from .base22_notice_adjustment import parse_adjusted_current_scheme_cash
    from .base22_notice_current_scope import CONTRACT_ID as CURRENT_SCOPE_CONTRACT
    from .base22_notice_current_scope import parse_current_scope_cash
    from .base22_notice_equivalent_scope import CONTRACT_ID as EQUIVALENT_SCOPE_CONTRACT
    from .base22_notice_equivalent_scope import parse_equivalent_scope_cash
    from .base22_notice_implementation_labels import CONTRACT_ID as IMPLEMENTATION_LABEL_CONTRACT
    from .base22_notice_implementation_labels import parse_implementation_label_cash
    from .base22_notice_labels import CONTRACT_ID as LABEL_CONTRACT
    from .base22_notice_labels import parse_literal_label_cash
    from .base22_notice_scheme import CONTRACT_ID as SCHEME_CONTRACT
    from .base22_notice_scheme import parse_current_scheme_cash

    if parser_profile not in {
        CONTRACT_ID,
        SCHEME_CONTRACT,
        ADJUSTMENT_CONTRACT,
        LABEL_CONTRACT,
        IMPLEMENTATION_LABEL_CONTRACT,
        CURRENT_SCOPE_CONTRACT,
        EQUIVALENT_SCOPE_CONTRACT,
    }:
        raise BoundedError("NOTICE_VERSIONED_PARSER_PROFILE_UNKNOWN")
    parser = {
        CONTRACT_ID: parse_implemented_notice_cash,
        SCHEME_CONTRACT: parse_current_scheme_cash,
        ADJUSTMENT_CONTRACT: parse_adjusted_current_scheme_cash,
        LABEL_CONTRACT: parse_literal_label_cash,
        IMPLEMENTATION_LABEL_CONTRACT: parse_implementation_label_cash,
        CURRENT_SCOPE_CONTRACT: parse_current_scope_cash,
        EQUIVALENT_SCOPE_CONTRACT: parse_equivalent_scope_cash,
    }[parser_profile]
    indexed = {r["action_id"]: r for r in targets}
    if len(indexed) != len(targets) or not 0 < len(targets) <= 256:
        raise BoundedError("NOTICE_EXACT_TARGET_SET_INVALID")
    candidates: dict[str, list[dict[str, Any]]] = {k: [] for k in indexed}
    blocked: dict[str, list[str]] = {k: [] for k in indexed}
    diagnostics = []
    seen: set[str] = set()
    for document in documents:
        doc = document["document_id"]
        if doc in seen or len(seen) >= 256:
            raise BoundedError("NOTICE_DOCUMENT_ID_DUPLICATE_OR_BOUND_EXCEEDED")
        seen.add(doc)
        keys = document["target_action_ids"]
        if not keys or len(set(keys)) != len(keys) or not set(keys) <= set(indexed):
            raise BoundedError("NOTICE_DOCUMENT_TARGET_BINDING_INVALID")
        title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
        primary = _IMPLEMENTED_TITLE.search(title) is not None
        for key in keys:
            target = indexed[key]
            item = {"document_id": doc, "action_id": key, "primary_implementation": primary}
            try:
                if document["publisher_security_code"] != target["stock_code"]:
                    raise BoundedError("NOTICE_PUBLISHER_SECURITY_IDENTITY_MISMATCH")
                atom = parser(
                    document["text"],
                    stock_code=target["stock_code"],
                    ex_date=date.fromisoformat(target["ex_date"]),
                    publisher_title=document["publisher_title"],
                )
                candidates[key].append({"document_id": doc, "atom": atom})
                item.update(status="EXACT_CASH_ATOM", atom_sha256=atom["cash_atom_sha256"])
            except BoundedError as exc:
                item.update(status="HELD", reason=str(exc))
                if primary or str(exc) == "NOTICE_PUBLISHER_SECURITY_IDENTITY_MISMATCH":
                    blocked[key].append(str(exc))
            diagnostics.append(item)
    selected = {}
    dispositions = {}
    for key in sorted(indexed):
        atoms = candidates[key]
        if blocked[key]:
            dispositions[key] = "PRIMARY_IMPLEMENTATION_UNRESOLVED"
        elif not atoms:
            dispositions[key] = "NO_EXACT_PRIMARY_CASH_ATOM"
        elif len({a["atom"]["cash_per_share"] for a in atoms}) != 1:
            dispositions[key] = "PRIMARY_IMPLEMENTATION_CASH_CONFLICT"
        else:
            selected[key] = sorted(atoms, key=lambda a: a["document_id"])[0]
            dispositions[key] = "EXACT_PRIMARY_ISSUER_CASH_ONLY"
    evidence = {
        "contract_id": parser_profile,
        "exact_target_keys": sorted(indexed),
        "exact_documents": sorted(seen),
        "document_dispositions": diagnostics,
        "key_dispositions": dispositions,
        "other_action_terms_qualified": False,
        "historical_PIT_admission": False,
        "old_normalization_manifest_restored": False,
    }
    evidence["identity"] = digest(evidence)
    return selected, evidence
