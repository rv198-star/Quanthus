"""Exact official prospectus origin for current financial literals only.

A query's issuer mapping is not historical identity evidence. The full document
must state the retained issuer's name and explicit origin in its own basic
information section. This scope never admits ownership, listing history or PIT.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .financial_reporting import FinancialCompanyIdentity, FinancialCompanyResolver
from .listed_company import _stable_id

CONTRACT_ID = "p10-base231-official-prospectus-current-financial-origin-v1"
SCOPE = "CURRENT_FINANCIAL_LITERAL_EXPLICIT_PROSPECTUS_ORIGIN_ONLY"
TZ = ZoneInfo("Asia/Shanghai")


def verify_source(root: Path, spec: dict[str, Any], *, observed_at: datetime) -> dict[str, Any]:
    """Bind the exact original PDF to its actual issuer-filtered official index."""
    import json

    from .base22 import file_sha
    from .base231_official_holder_replay import verify_current_document_receipts

    index_path = root / spec["index"]
    actual = spec["actual_index_row"]
    index = json.loads(index_path.read_bytes())
    matches = [row for row in index.get("announcements") or []
               if row.get("announcementId") == actual.get("announcementId")]
    if len(matches) != 1 or matches[0] != actual or actual.get("secCode") != spec["code"]:
        raise BoundedError("PROSPECTUS_FINANCIAL_EXACT_INDEX_DOCUMENT_REQUIRED")
    verify_current_document_receipts(
        actual=actual, index_receipt=json.loads((root / spec["index_receipt"]).read_bytes()),
        PDF_receipt=json.loads((root / spec["receipt"]).read_bytes()),
        index_sha256=file_sha(index_path), index_bytes=index_path.stat().st_size,
        PDF_sha256=file_sha(root / spec["PDF"]), PDF_bytes=(root / spec["PDF"]).stat().st_size,
        observed_at=observed_at,
    )
    return dict(actual)


def qualify_origin(
    body: str, *, code: str, expected_name: str, issuer: dict[str, Any],
    parent: FinancialCompanyIdentity, pdf_sha256: str, actual: dict[str, Any],
    received_at: datetime, observed_at: datetime,
) -> tuple[FinancialCompanyIdentity, dict[str, Any], dict[str, Any]]:
    clock, received = instant(observed_at), instant(received_at)
    title = actual.get("announcementTitle", "")
    if (not re.fullmatch(r"[0-9]{6}", code) or code != parent.stock_code
            or issuer.get("code") != code or issuer.get("category") != "A股"
            or not issuer.get("orgId") or actual.get("secCode") != code
            or actual.get("orgId") != issuer["orgId"]
            or parent.company_id != _stable_id("company", "CNINFO_ORG_ID", issuer["orgId"])
            or not isinstance(title, str) or "首次公开发行" not in title
            or not title.endswith("招股说明书") or "摘要" in title
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", pdf_sha256)
            or received > clock or parent.knowledge_from > clock
            or not isinstance(body, str) or not 0 < len(body.encode()) <= 8 * 1024**2
            or "招股说明书" not in body[:10000] or not expected_name):
        raise BoundedError("PROSPECTUS_FINANCIAL_EXACT_SOURCE_ISSUER_OR_CLOCK_REQUIRED")
    starts = list(re.finditer(
        r"(?m)^\s*一[、．.]\s*(?:(?:发行人(?:的)?|公司)基本(?:情况|信息)|发行人概况)\s*$",
        body))
    if len(starts) != 1:
        raise BoundedError("PROSPECTUS_UNIQUE_ISSUER_BASIC_SECTION_REQUIRED")
    block = body[starts[0].end():starts[0].end() + 3500]
    end = re.search(r"(?m)^\s*二[、．.]", block)
    if end:
        block = block[:end.start()]
    prefix = r"(?m)^\s*(?:[（(][一二三四五六七八九十]+[）)]\s*)?"
    names = re.findall(prefix + r"(?:注册名称[（(]中文[）)]|注册中文名称|中文名称|公司名称|"
                       r"发行人名称|发行人)"
                       r"\s*[：:]?\s*(\S+)\s*$",
                       block)
    typography = str.maketrans({"（": "(", "）": ")"})
    if len(names) != 1 or names[0].translate(typography) != expected_name.translate(typography):
        raise BoundedError("PROSPECTUS_EXACT_NATIVE_ISSUER_NAME_REQUIRED")
    fields = re.findall(
        prefix + r"((?:有限(?:责任)?公司|公司)?(?:成立|设立)(?:日期|时间))\s*[：:]?\s*"
        r"([0-9]{4})\s*(?:年|-)\s*([0-9]{1,2})\s*(?:月|-)\s*([0-9]{1,2})(?:\s*日)?"
        r"(?=\s|[（(]|$)", block)
    if len(fields) != 1:
        raise BoundedError("PROSPECTUS_EXPLICIT_UNIQUE_ISSUER_ORIGIN_REQUIRED")
    origin = date(*map(int, fields[0][1:]))
    start = instant(datetime.combine(origin, time(), TZ))
    if origin > date(2026, 9, 30) or start >= parent.effective_from:
        raise BoundedError("PROSPECTUS_FINANCIAL_ORIGIN_IPO_CONFLICT")
    identity = FinancialCompanyIdentity(
        stock_code=code, company_id=parent.company_id, security_id=parent.security_id,
        effective_from=start, effective_to=parent.effective_from, knowledge_from=clock,
    )
    company = dict(
        record_source="cninfo.prospectus.explicit_issuer_origin",
        source_record_id=digest([code, expected_name, fields]), source_revision=CONTRACT_ID,
        source_artifact_sha256=pdf_sha256, knowledge_from=clock, knowledge_to=None,
        effective_from=start, effective_to=None, company_id=parent.company_id,
        display_name=expected_name, company_type="official_source_scoped_financial_issuer",
        resolution_scope=SCOPE,
    )
    proof = dict(
        contract_id=CONTRACT_ID, scope=SCOPE, stock_code=code, company_id=parent.company_id,
        source_pdf_sha256=pdf_sha256, source_text_sha256=digest(body),
        native_basic_section_sha256=digest(block), native_origin_field_sha256=digest(fields),
        original_name_sha256=digest(expected_name), reported_origin_date=origin,
        first_ipo_date=parent.effective_from.astimezone(TZ).date(),
        actual_index_row_sha256=digest(actual), received_at=received, new_knowledge_at=clock,
        original_provider_acquisition=None, legal_form_date_qualified=False,
        historical_legal_continuity_qualified=False, historical_names_asserted=False,
        historical_PIT_qualified=False, security_or_listing_extended=False,
        ownership_qualified=False, consumer_admission="NOT_GRANTED",
    )
    return identity, company, proof


def project_financial(
    raw: dict[str, Any], *, identity: FinancialCompanyIdentity,
    qualification: dict[str, Any], source_sha256: str, source_revision: str,
    observed_at: datetime, contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    from .base231_financial_summary import summary_observation

    if (qualification.get("contract_id") != CONTRACT_ID or qualification.get("scope") != SCOPE
            or qualification.get("stock_code") != identity.stock_code
            or qualification.get("company_id") != identity.company_id
            or qualification.get("ownership_qualified") is not False
            or qualification.get("historical_PIT_qualified") is not False
            or qualification.get("legal_form_date_qualified") is not False
            or identity.knowledge_from != instant(observed_at)
            or qualification.get("new_knowledge_at") != identity.knowledge_from
            or identity.effective_to is None):
        raise BoundedError("PROSPECTUS_FINANCIAL_ONLY_SCOPE_CHANGED")
    output, proof = summary_observation(
        raw, source_sha256=source_sha256, revision=source_revision,
        resolver=FinancialCompanyResolver([identity]), observed_at=observed_at, contract=contract)
    if any(not identity.effective_from <= instant(datetime.combine(
        r["period_end"], time(23, 59, 59), TZ)) < identity.effective_to
        for r in output["dwd_financial_report"]):
        raise BoundedError("PROSPECTUS_FINANCIAL_PERIOD_OUTSIDE_ORIGIN")
    proof.update(origin_contract_id=CONTRACT_ID, origin_scope=SCOPE,
                 origin_qualification_sha256=digest(qualification),
                 historical_PIT_qualified=False, ownership_qualified=False,
                 security_or_listing_extended=False)
    return output, proof
