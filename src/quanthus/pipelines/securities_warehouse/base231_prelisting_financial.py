"""Current-knowledge issuer-reported financial literals before an IPO.

Explicit legal-form dates bound this narrow attribution. This does not extend
security/listing existence, assert historical names or qualify historical PIT.
The original listed-company resolver remains unchanged.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base22_outputs import instant
from .base23_ownership_reconfirmation import RAW_FIELDS
from .base231_financial_summary import summary_observation
from .financial_reporting import FinancialCompanyIdentity, FinancialCompanyResolver
from .listed_company import _stable_id

CONTRACT_ID = "p10-base231-current-issuer-prelisting-financial-literal-v1"
TZ = ZoneInfo("Asia/Shanghai")


def qualify_profile(
    raw: dict[str, Any],
    *,
    raw_row_sha256: str,
    source_sha256: str,
    issuer: dict[str, Any],
    parent: FinancialCompanyIdentity,
    observed_at: datetime,
) -> tuple[FinancialCompanyIdentity, dict[str, Any], dict[str, Any]]:
    import hashlib

    clock = instant(observed_at)
    if (
        set(raw) != RAW_FIELDS
        or digest(raw) != raw_row_sha256
        or raw["category"] != "公司概况"
        or raw["content_sha256"] != "sha256:" + hashlib.sha256(raw["content"].encode()).hexdigest()
        or raw["source"] != "tdx.get_company_info_content"
        or raw["stock_code"] != parent.stock_code
        or issuer.get("code") != parent.stock_code
        or issuer.get("category") != "A股"
        or parent.company_id != _stable_id("company", "CNINFO_ORG_ID", issuer.get("orgId", ""))
        or instant(raw["acquired_at"]) > clock
        or parent.knowledge_from > clock
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)
    ):
        raise BoundedError("PRELISTING_FINANCIAL_EXACT_ISSUER_SOURCE_NOT_PROVEN")
    fields: dict[str, set[str]] = {}
    for line in raw["content"].splitlines():
        cells = _cells(line)
        if len(cells) in {2, 4}:
            for n in range(0, len(cells), 2):
                if cells[n] in {"公司名称", "证券代码", "股份公司设立日期", "上市日期"}:
                    fields.setdefault(cells[n], set()).add(cells[n + 1])
    if set(fields) != {"公司名称", "证券代码", "股份公司设立日期", "上市日期"} or any(
        len(v) != 1 for v in fields.values()
    ):
        raise BoundedError("PRELISTING_FINANCIAL_EXPLICIT_LEGAL_FORM_DATE_REQUIRED")
    named = {k: next(iter(v)) for k, v in fields.items()}
    if named["证券代码"] != parent.stock_code or not named["公司名称"]:
        raise BoundedError("PRELISTING_FINANCIAL_LEGAL_ISSUER_CODE_CONFLICT")
    formation, ipo = (
        date.fromisoformat(named["股份公司设立日期"]),
        date.fromisoformat(named["上市日期"]),
    )
    start = instant(datetime.combine(formation, time(), TZ))
    if start >= parent.effective_from or ipo != parent.effective_from.astimezone(TZ).date():
        raise BoundedError("PRELISTING_FINANCIAL_LEGAL_FORM_IPO_COORDINATE_CONFLICT")
    identity = FinancialCompanyIdentity(
        stock_code=parent.stock_code,
        company_id=parent.company_id,
        security_id=parent.security_id,
        effective_from=start,
        effective_to=parent.effective_from,
        knowledge_from=clock,
    )
    # The entity row records the legal-form assertion at current knowledge.
    # The resolver's IPO upper bound applies only to this financial scope;
    # it is not the company's legal termination date.
    company = dict(
        record_source="tdx.company_overview.explicit_legal_form",
        source_record_id=digest([parent.stock_code, named]),
        source_revision=CONTRACT_ID,
        source_artifact_sha256=source_sha256,
        knowledge_from=clock,
        knowledge_to=None,
        effective_from=start,
        effective_to=None,
        company_id=parent.company_id,
        display_name=named["公司名称"],
        company_type="provider_scoped_financial_issuer",
        resolution_scope="CURRENT_ISSUER_PRELISTING_FINANCIAL_LITERAL_ONLY",
    )
    proof = dict(
        contract_id=CONTRACT_ID,
        stock_code=parent.stock_code,
        raw_row_sha256=raw_row_sha256,
        original_member_sha256=source_sha256,
        legal_fields_sha256=digest(named),
        legal_form_date=formation,
        first_ipo_date=ipo,
        company_id=parent.company_id,
        official_current_orgId=issuer["orgId"],
        original_acquired_at=raw["acquired_at"],
        new_knowledge_at=clock,
        historical_names_asserted=False,
        security_or_listing_extended=False,
        provider_PIT_qualified=False,
        consumer_admission="NOT_GRANTED",
    )
    return identity, company, proof


def project_financial(
    raw: dict[str, Any],
    *,
    identity: FinancialCompanyIdentity,
    source_sha256: str,
    source_revision: str,
    observed_at: datetime,
    contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if (
        raw["stock_code"] != identity.stock_code
        or identity.knowledge_from != instant(observed_at)
        or identity.effective_to is None
    ):
        raise BoundedError("PRELISTING_FINANCIAL_SCOPE_OR_NEW_KNOWLEDGE_CHANGED")
    output, proof = summary_observation(
        raw,
        source_sha256=source_sha256,
        revision=source_revision,
        resolver=FinancialCompanyResolver([identity]),
        observed_at=observed_at,
        contract=contract,
    )
    if any(
        not identity.effective_from
        <= instant(datetime.combine(r["period_end"], time(23, 59, 59), TZ))
        < identity.effective_to
        for r in output["dwd_financial_report"]
    ):
        raise BoundedError("PRELISTING_FINANCIAL_REPORT_OUTSIDE_LEGAL_SCOPE")
    proof.update(
        prelisting_contract_id=CONTRACT_ID,
        legal_scope_start=identity.effective_from,
        legal_scope_end=identity.effective_to,
        legal_scope_is_not_listing_history=True,
        historical_PIT_qualified=False,
    )
    return output, proof
