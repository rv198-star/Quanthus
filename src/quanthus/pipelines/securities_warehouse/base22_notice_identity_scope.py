"""Exact publisher/header identity and explicitly excluded foreign operations.

No code alias, currency conversion, cash arithmetic or issuer substitution.
Only a literal header code already matching the original publisher receipt is
labelled in the private parser view; the original PDF and metadata stay intact.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_issuer_titles import issuer_schedule_view, select_issuer_schedule_candidates

CONTRACT_ID = "p10-base22-exact-publisher-header-and-explicit-A-scope-v14"


def exact_identity_view(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    previous, role = issuer_schedule_view(document)
    original_title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
    text = previous["text"]
    compact = re.sub(r"\s+", "", text)
    positive_a = "A股" in original_title or "全体A股股东" in compact
    exclusions = list(re.finditer(
        r"(?:本公司|公司)?H股股东的现金(?:股息|红利|分红)"
        r"(?:发放|派发)?[^。]{0,60}不适用本公告", compact
    ))
    conflicting_h = bool(re.search(r"H股(?:股东)?[^。]{0,30}每(?:10|1)?股", compact))
    h_reflows = 0
    if positive_a and exclusions and not conflicting_h and not any(
        s in compact for s in ("B股", "港币", "港元", "美元")
    ):
        # Only the H label inside the explicit exclusion is reflowed. Other
        # H-share clauses remain visible to the downstream scope gate.
        offsets = [m.start() for m in re.finditer(r"\S", text)]
        spans = [
            (offsets[m.start()], offsets[m.start() + 1] + 1)
            for exclusion in exclusions
            for m in re.finditer(r"H股", compact)
            if exclusion.start() <= m.start() < exclusion.end()
        ]
        for start, end in reversed(spans):
            text = text[:start] + "源文明确不适用的境外股份" + text[end:]
        h_reflows = len(spans)
    # A bare code plus a year/serial heading is not an alias. Both literal
    # identities must agree; quantities, two-code headings and later numbers
    # cannot be promoted into this narrow source-header grammar.
    lines = text.splitlines(keepends=True)
    first = next((n for n, line in enumerate(lines) if line.strip()), None)
    header = None
    code = document["publisher_security_code"]
    if first is not None and re.fullmatch(r"\d{6}", code):
        header = re.fullmatch(r"\s*(?P<code>\d{6})\s+\d{4}[-－]\d{1,4}\s*", lines[first])
    inserted = False
    if header is not None and header["code"] == code:
        lines[first] = re.sub(
            r"(?<!\d)" + code + r"(?!\d)", "证券代码:" + code + ";", lines[first], count=1
        )
        text = "".join(lines)
        inserted = True
    if re.findall(r"\d+", previous["text"]) != re.findall(r"\d+", text):
        raise BoundedError("NOTICE_EXACT_IDENTITY_NUMERIC_SEQUENCE_CHANGED")
    evidence = {
        "contract_id": CONTRACT_ID,
        "document_id": document["document_id"],
        "original_body_sha256": digest(document["text"]),
        "original_title_sha256": digest(document["publisher_title"]),
        "predecessor_role_identity": role["identity"],
        "positive_A_scope_in_original": positive_a,
        "H_operation_exclusion_sha256": [digest(m[0]) for m in exclusions],
        "conflicting_H_per_share_claim": conflicting_h,
        "H_label_reflows": h_reflows,
        "publisher_and_literal_bare_header_code_agree": inserted,
        "original_first_header_sha256": digest(previous["text"].splitlines()[first])
        if first is not None else None,
        "parser_view_sha256": digest(text),
        "code_alias_or_currency_inferred": False,
        "original_PDF_or_publisher_metadata_modified": False,
    }
    evidence["identity"] = digest(evidence)
    return {**previous, "text": text}, evidence


def select_exact_identity_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    originals, views, proofs = {}, [], {}
    for document in documents:
        if document["document_id"] in originals:
            raise BoundedError("NOTICE_EXACT_IDENTITY_DUPLICATE_DOCUMENT")
        originals[document["document_id"]] = document
        view, proof = exact_identity_view(document)
        views.append(view)
        proofs[document["document_id"]] = proof
    # Re-evaluate the unchanged original grammar too. The new exclusion view
    # must not erase an explicit exclusion needed by the predecessor's printed
    # combined-total grammar. This is local parsing of the same original
    # documents, never an old path, cached atom or historical receipt fallback.
    retained, retained_evidence = select_issuer_schedule_candidates(targets, originals.values())
    selected, previous = select_issuer_schedule_candidates(targets, views)
    conflicting = {
        key for key in retained.keys() & selected.keys()
        if retained[key]["atom"]["cash_atom_sha256"] != selected[key]["atom"]["cash_atom_sha256"]
    }
    for key, candidate in retained.items():
        selected[key] = candidate
    for key in conflicting:
        selected.pop(key)
    for candidate in selected.values():
        original = originals[candidate["document_id"]]
        candidate["atom"] = {
            **candidate["atom"], "contract_id": CONTRACT_ID,
            "original_text_sha256": digest(original["text"]),
            "original_publisher_title_sha256": digest(original["publisher_title"]),
            "exact_identity_scope_evidence": proofs[candidate["document_id"]],
        }
    evidence = {
        **{k: v for k, v in previous.items() if k != "identity"},
        "contract_id": CONTRACT_ID,
        "exact_identity_document_evidence": proofs,
        "unchanged_original_profile_evidence": retained_evidence,
        "unchanged_original_profile_selected_keys": sorted(retained),
        "conflicting_original_and_view_keys": sorted(conflicting),
        "key_dispositions": {
            t["action_id"]: "EXACT_PRIMARY_ISSUER_CASH_ONLY"
            if t["action_id"] in selected else "ORIGINAL_AND_VIEW_ATOM_CONFLICT"
            if t["action_id"] in conflicting else previous["key_dispositions"][t["action_id"]]
            for t in targets
        },
    }
    evidence["identity"] = digest(evidence)
    return selected, evidence
