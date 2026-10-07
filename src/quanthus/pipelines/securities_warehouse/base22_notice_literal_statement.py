"""Exact implemented statement boundaries; original periods and numeric tokens stay intact."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_notice_action import _CASH, _DATE_TABLE, _EX_DATE, _IMPLEMENTED_TITLE, MAX_TEXT_BYTES
from .base22_notice_current_scope import _NON_UNIFORM, _scope_view
from .base22_notice_domestic_scope import parse_domestic_scope_cash
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-literal-current-and-declared-combined-cash-v10"
_DECLARATION = re.compile(
    r"(?:本公司|公司)\d{4}年?(?:年度|半年度)?"
    r"(?:(?:及|、)\d{4}年?(?:年度|中期|第一次中期))?"
    r"(?:权益分派|权益分配|利润分配)(?:及\d{4}年中期权益分派)?"
    r"(?P<combined>合并实施)?方案为[:：]"
)
_TITLES = {"权益分派方案", "本次实施的权益分派方案", "本次实施的利润分配方案"}
_COMBINED_CASH = re.compile(
    r"每(?P<denominator>10|1)股合计派(?P<amount>(?:0|[1-9]\d*)(?:\.\d+)?)"
    r"元人民币现金\(含税(?:\)|[,，;；])"
)
_AGGREGATE = re.compile(
    r"(?:共计(?:A股)?每股(?:派发)?|每股合计派发)现金红利人民币"
    r"(?P<amount>(?:0|[1-9]\d*)(?:\.\d+)?)元\(含税(?:\)|[,，;；])"
)


def parse_literal_current_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    try:
        prior = parse_domestic_scope_cash(
            text, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
    except BoundedError:
        pass
    else:
        return {**prior, "contract_id": CONTRACT_ID, "predecessor_contract": prior["contract_id"]}
    if (
        not isinstance(text, str)
        or not 0 < len(text.encode()) <= MAX_TEXT_BYTES
        or not re.fullmatch(r"\d{6}", stock_code)
        or type(ex_date) is not date
        or ex_date > THROUGH
        or not isinstance(publisher_title, str)
    ):
        raise BoundedError("NOTICE_LITERAL_CURRENT_EXACT_INPUT_REQUIRED")
    view, layout = _scope_view(text)
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", view))
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", publisher_title))
    if not _IMPLEMENTED_TITLE.search(title) or not re.search(
        r"(?:证券代码|股票代码|A股代码)[:：]?" + stock_code + r"(?!\d)", normalized[:4096]
    ):
        raise BoundedError("NOTICE_LITERAL_CURRENT_EXACT_IMPLEMENTATION_IDENTITY_REQUIRED")
    if any(s in normalized for s in ("H股", "B股", "港币", "港元", "美元")):
        raise BoundedError("NOTICE_LITERAL_CURRENT_FOREIGN_CLASS_UNQUALIFIED")
    dates = set()
    for m in _EX_DATE.finditer(normalized):
        try:
            dates.add(date(*(int(m[n]) for n in ("year", "month", "day"))))
        except ValueError:
            raise BoundedError("NOTICE_LITERAL_CURRENT_INVALID_DATE") from None
    for m in _DATE_TABLE.finditer(normalized):
        dates.add(date(*(int(n) for n in m["ex"].split("/"))))
    if dates != {ex_date}:
        raise BoundedError("NOTICE_LITERAL_CURRENT_EXACT_EX_DATE_REQUIRED")
    lines = view.splitlines()
    headings = [
        (i, heading_match["title"].strip().rstrip(":："))
        for i, line in enumerate(lines)
        if (heading_match := _HEADING.fullmatch(line))
    ]
    scopes = []
    amounts = set()
    tokens = []
    with localcontext() as context:
        context.prec = 64
        for index, (start, heading) in enumerate(headings):
            end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
            block = re.sub(r"\s+", "", "\n".join(lines[start + 1 : end]))
            if _NON_UNIFORM.search(block):
                continue
            # Only the publisher's explicit per-share aggregate qualifies; neither
            # component sum nor a total-money sentence grants a per-share amount.
            aggregate = list(_AGGREGATE.finditer(block)) if heading == "分配方案" else []
            if aggregate:
                for m in aggregate:
                    value = Decimal(m["amount"])
                    amounts.add(value)
                    tokens.append({"numeric_token_sha256": digest(m["amount"]), "denominator": 1})
                scopes.append(
                    {
                        "heading_sha256": digest(heading),
                        "current_scope_sha256": digest(block),
                        "grammar": "PUBLISHER_EXPLICIT_PER_SHARE_AGGREGATE_NOT_COMPUTED",
                    }
                )
                continue
            if heading not in _TITLES:
                continue
            if any(marker in block for marker in ("调整后", "实际摊薄", "实施调整为")):
                raise BoundedError("NOTICE_LITERAL_OTHER_CURRENT_ADJUSTMENT_NEEDS_OWN_SCOPE")
            declarations = list(_DECLARATION.finditer(block))
            combined = [m for m in declarations if m["combined"]]
            if combined:
                if len(combined) != 1:
                    raise BoundedError("NOTICE_LITERAL_MULTIPLE_COMBINED_SCOPES")
                declarations = combined
            for m in declarations:
                prefix = block[max(block.rfind("。", 0, m.start()) + 1, 0) : m.start()]
                if re.search(r"拟|预案|审议通过|原为", prefix[-160:]):
                    continue
                end_clause = block.find("。", m.end())
                if end_clause < 0:
                    raise BoundedError("NOTICE_LITERAL_CURRENT_COMPLETE_SENTENCE_REQUIRED")
                scope = block[m.end() : end_clause]
                claims = list((_COMBINED_CASH if m["combined"] else _CASH).finditer(scope))
                values = set()
                for claim in claims:
                    if "人民币" not in claim[0]:
                        raise BoundedError("NOTICE_LITERAL_CURRENT_EXPLICIT_CNY_REQUIRED")
                    token = claim["amount"] or claim["post_amount"]
                    denom = int(claim["denominator"] or "1")
                    values.add(Decimal(token) / Decimal(denom))
                    tokens.append({"numeric_token_sha256": digest(token), "denominator": denom})
                if len(values) != 1:
                    raise BoundedError("NOTICE_LITERAL_CURRENT_SENTENCE_GROSS_NOT_UNIQUE")
                amounts.update(values)
                scopes.append(
                    {
                        "heading_sha256": digest(heading),
                        "declaration_sha256": digest(m[0]),
                        "current_scope_sha256": digest(scope),
                        "grammar": "EXPLICIT_COMBINED_IMPLEMENTED"
                        if m["combined"]
                        else "EXPLICIT_COMPANY_IMPLEMENTED_SENTENCE",
                    }
                )
    if not scopes or len(amounts) != 1:
        raise BoundedError("NOTICE_LITERAL_CURRENT_SCOPE_NOT_UNIQUE_OR_AVAILABLE")
    amount = next(iter(amounts))
    if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
        raise BoundedError("NOTICE_LITERAL_CURRENT_GROSS_NOT_EXACT")
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": stock_code,
        "ex_date": ex_date,
        "currency": "CNY",
        "cash_per_share": amount,
        "cash_atom_sha256": digest(["CNY", amount]),
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(publisher_title),
        "layout_evidence": layout,
        "scopes": scopes,
        "original_numeric_tokens": tokens,
        "aggregate_computed": False,
        "historical_PIT_admission": False,
    }


def select_literal_cash_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Every frozen primary must qualify and agree, with original ancillary diagnostics."""
    indexed = {t["action_id"]: t for t in targets}
    if len(indexed) != len(targets) or not 0 < len(targets) <= 256:
        raise BoundedError("NOTICE_LITERAL_TARGET_SET_INVALID")
    candidates: dict[str, list[dict[str, Any]]] = {k: [] for k in indexed}
    blocked = set()
    seen: set[str] = set()
    diagnostics = []
    for d in documents:
        doc = d["document_id"]
        keys = d["target_action_ids"]
        if (
            doc in seen
            or len(seen) >= 256
            or not keys
            or len(set(keys)) != len(keys)
            or not set(keys) <= set(indexed)
        ):
            raise BoundedError("NOTICE_LITERAL_DOCUMENT_BINDING_INVALID")
        seen.add(doc)
        primary = bool(
            _IMPLEMENTED_TITLE.search(
                re.sub(r"\s+", "", unicodedata.normalize("NFKC", d["publisher_title"]))
            )
        )
        for key in keys:
            t = indexed[key]
            item = {"document_id": doc, "action_id": key, "primary_implementation": primary}
            try:
                if d["publisher_security_code"] != t["stock_code"]:
                    raise BoundedError("NOTICE_LITERAL_PUBLISHER_IDENTITY_MISMATCH")
                atom = parse_literal_current_cash(
                    d["text"],
                    stock_code=t["stock_code"],
                    ex_date=date.fromisoformat(t["ex_date"]),
                    publisher_title=d["publisher_title"],
                )
                candidates[key].append({"document_id": doc, "atom": atom})
                item.update(status="EXACT_CASH_ATOM", atom_sha256=atom["cash_atom_sha256"])
            except BoundedError as e:
                item.update(status="HELD", reason=str(e))
                if primary or str(e) == "NOTICE_LITERAL_PUBLISHER_IDENTITY_MISMATCH":
                    blocked.add(key)
            diagnostics.append(item)
    selected = {}
    dispositions = {}
    for key, atoms in sorted(candidates.items()):
        if key in blocked:
            dispositions[key] = "PRIMARY_IMPLEMENTATION_UNRESOLVED"
        elif not atoms:
            dispositions[key] = "NO_EXACT_PRIMARY_CASH_ATOM"
        elif len({a["atom"]["cash_per_share"] for a in atoms}) != 1:
            dispositions[key] = "PRIMARY_IMPLEMENTATION_CASH_CONFLICT"
        else:
            selected[key] = sorted(atoms, key=lambda a: a["document_id"])[0]
            dispositions[key] = "EXACT_PRIMARY_ISSUER_CASH_ONLY"
    evidence = {
        "contract_id": CONTRACT_ID,
        "exact_target_keys": sorted(indexed),
        "exact_documents": sorted(seen),
        "document_dispositions": diagnostics,
        "key_dispositions": dispositions,
        "aggregate_computed": False,
        "other_action_terms_qualified": False,
        "historical_PIT_admission": False,
        "old_normalization_manifest_restored": False,
    }
    evidence["identity"] = digest(evidence)
    return selected, evidence
