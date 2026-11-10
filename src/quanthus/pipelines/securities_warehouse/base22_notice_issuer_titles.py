"""Retained issuer distribution notices with an explicit implementation schedule.

Publisher metadata is never rewritten. The opt-in parser view distinguishes
literal title variants and an approved dated distribution from a preliminary
schedule that explicitly says the implementation notice will follow.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from .base22 import digest
from .base22_notice_gross_layout import select_gross_layout_candidates

CONTRACT_ID = "p10-base22-retained-issuer-explicit-distribution-schedule-v13"


def issuer_schedule_view(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
    original = unicodedata.normalize("NFKC", document["text"])
    compact = re.sub(r"\s+", "", original)
    preliminary = bool(re.search(
        r"(?:权益派发|权益分派|利润分配)实施公告将|(?:A股)?利润分配预计时间安排", compact
    ))
    view_title = document["publisher_title"]
    reason = "UNCHANGED_OR_UNQUALIFIED_TITLE"
    if not preliminary and "利润分派实施公告" in title:
        view_title = title.replace("利润分派实施公告", "利润分配实施公告")
        reason = "LITERAL_IMPLEMENTED_DISTRIBUTION_TITLE"
    elif not preliminary and re.search(r"\d{4}年度(?:A股)?(?:分红派息|利润分配)公告$", title):
        approved = bool(re.search(
            r"方案已(?:经|获)[^。]{0,160}股东大会[^。]{0,50}审议通过", compact
        ))
        actual = bool(re.search(r"现将(?:有关实施|权益分派)事宜公告如下", compact))
        dated = "股权登记日" in compact and bool(re.search(r"除权除息日|除权除息日期", compact))
        if approved and (actual or "A股利润分配公告" in title) and dated:
            view_title = "权益分派实施公告（原始发布标题保留在来源证据中）"
            reason = "APPROVED_EXPLICIT_DATED_DISTRIBUTION_NOT_PROPOSAL"
    view = original
    excluded_h = False
    if reason != "UNCHANGED_OR_UNQUALIFIED_TITLE" and "A股" in title:
        excluded_h = bool(re.search(
            r"H股股东的现金(?:股息|红利)(?:发放|派发)[^。]{0,60}不适用本公告", compact
        )) and not any(s in compact for s in ("B股", "港币", "港元", "美元"))
        if excluded_h:
            view = re.sub(r"H\s*股", "原文明确不适用的境外股份", view)
    view, gross_period = re.subn(r"\(含税[。.]\s*(?=扣税后)", "(含税;", view)
    proof = {
        "contract_id": CONTRACT_ID,
        "document_id": document["document_id"],
        "original_title_sha256": digest(document["publisher_title"]),
        "view_title_sha256": digest(view_title),
        "original_text_sha256": digest(document["text"]),
        "view_text_sha256": digest(view),
        "title_role": reason,
        "preliminary_schedule_not_primary": preliminary,
        "literal_explicit_A_notice_H_exclusion": excluded_h,
        "literal_gross_period_reflows": gross_period,
        "numeric_tokens_changed": False,
        "publisher_metadata_rewritten": False,
    }
    assert re.findall(r"\d+", original) == re.findall(r"\d+", view)
    proof["identity"] = digest(proof)
    return {**document, "text": view, "publisher_title": view_title}, proof


def select_issuer_schedule_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    originals, views, proofs = {}, [], {}
    for document in documents:
        originals[document["document_id"]] = document
        view, proof = issuer_schedule_view(document)
        views.append(view)
        proofs[document["document_id"]] = proof
    selected, previous = select_gross_layout_candidates(targets, views)
    for item in selected.values():
        original = originals[item["document_id"]]
        item["atom"] = {
            **item["atom"], "contract_id": CONTRACT_ID,
            "original_text_sha256": digest(original["text"]),
            "original_publisher_title_sha256": digest(original["publisher_title"]),
            "issuer_schedule_evidence": proofs[item["document_id"]],
        }
    evidence = {**{k: v for k, v in previous.items() if k != "identity"},
                "contract_id": CONTRACT_ID, "issuer_schedule_document_evidence": proofs}
    evidence["identity"] = digest(evidence)
    return selected, evidence
