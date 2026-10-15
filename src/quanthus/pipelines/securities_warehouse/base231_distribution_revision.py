"""Exact three-document payment correction, never two dividend events.

Only the stated payment sentence, bulletin number, corrected title and board
signature date may change. Every other normalized body character and parsed
distribution term must agree. This does not restore historical provider PIT.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_bounded import BoundedError
from .base231_disclosure_body import project_body
from .base231_official_action import parse_distribution

CONTRACT_ID = "p10-base231-exact-official-payment-date-revision-v1"
_PAYMENT = re.compile(
    r"本公司此次委托中国结算深圳分公司代派的A股股东现金红利将于"
    r"(\d{4})年(\d{1,2})月(\d{1,2})日"
    r"通过股东托管证券公司（或其他托管机构）直接划入其资金账户。"
)
_NUMBER = re.compile(r"公告编号[：:](\d{4}-\d{3})")
_TITLE = "2026年中期权益分派实施公告"


def _single(pattern: re.Pattern[str], body: str) -> re.Match[str]:
    matches = list(pattern.finditer(body))
    if len(matches) != 1:
        raise BoundedError("PAYMENT_REVISION_EXACT_SINGLE_CLAUSE_REQUIRED")
    return matches[0]


def qualify_payment_revision(
    previous: dict[str, Any],
    corrected: dict[str, Any],
    notice: dict[str, Any],
    *,
    parent: Any,
    observed_at: datetime,
    expected_ex_date: date,
    currency_scheme_body: str | None,
) -> dict[str, Any]:
    documents = [previous, corrected, notice]
    actuals = [d["selection"]["actual_index_row"] for d in documents]
    orgs = {a.get("orgId") for a in actuals}
    if (
        len({a["announcementId"] for a in actuals}) != 3
        or len(orgs) != 1 or None in orgs or "" in orgs
        or any(a["secCode"] != parent.stock_code for a in actuals)
        or [a["announcementTitle"] for a in actuals] != [
            _TITLE, _TITLE + "（更正后）", "关于" + _TITLE + "的更正公告",
        ]
        or not actuals[0]["announcementTime"] < actuals[1]["announcementTime"]
        == actuals[2]["announcementTime"]
    ):
        raise BoundedError("PAYMENT_REVISION_EXACT_ISSUER_SEQUENCE_REQUIRED")
    source_proofs = []
    for d in documents:
        body = re.sub(r"\s+", "", d["body"])
        if set(re.findall(r"证券代码[：:]?(\d{6})", body[:4000])) != {parent.stock_code}:
            raise BoundedError("PAYMENT_REVISION_BODY_ISSUER_REQUIRED")
        _, proof = project_body(
            d["selection"], d["receipt"], index=d["index"],
            body_sha256=d["PDF_sha256"], body_text=d["body"], parent=parent,
            observed_at=observed_at,
        )
        source_proofs.append(proof)
    old, new, correction = [re.sub(r"\s+", "", d["body"]) for d in documents]
    old_number, new_number = [_single(_NUMBER, b)[1] for b in [old, new]]
    if old_number == new_number or _TITLE + "（更正后）" not in new:
        raise BoundedError("PAYMENT_REVISION_EXPLICIT_CORRECTED_VERSION_REQUIRED")
    before, after = [_single(_PAYMENT, b) for b in [old, new]]
    old_day, new_day = [date(*map(int, m.groups())) for m in [before, after]]
    if old_day == new_day or not expected_ex_date <= new_day <= date(2026, 9, 30):
        raise BoundedError("PAYMENT_REVISION_NEW_DATE_SCOPE_UNPROVEN")
    correction_match = re.search(
        r"更正前[：:](.+?)更正后[：:](.+?)除上述更正内容外，"
        r"公司2026年中期权益分派实施的其他内容均未发生变化。",
        correction,
    )
    prefix = correction.split("更正前", 1)[0]
    if (
        correction_match is None or correction_match.groups() != (before[0], after[0])
        or prefix.count("在巨潮资讯网刊登了《" + _TITLE + "》") != 1
        or re.findall(r"（公告编号[：:](\d{4}-\d{3})）", prefix) != [old_number]
    ):
        raise BoundedError("PAYMENT_REVISION_NOTICE_EXACT_CHANGE_UNPROVEN")

    def unchanged(body: str, payment: re.Match[str]) -> str:
        normalized = body.replace(payment[0], "<EXPLICIT_PAYMENT_CLAUSE>", 1)
        normalized = _NUMBER.sub("公告编号:<VERSION>", normalized, count=1)
        normalized = normalized.replace(_TITLE + "（更正后）", _TITLE, 1)
        normalized, count = re.subn(
            r"(?<=董事会)\d{4}年\d{1,2}月\d{1,2}日$", "<SIGNATURE_DATE>", normalized
        )
        if count != 1:
            raise BoundedError("PAYMENT_REVISION_BOARD_SIGNATURE_REQUIRED")
        return normalized

    if unchanged(old, before) != unchanged(new, after):
        raise BoundedError("PAYMENT_REVISION_UNDISCLOSED_BODY_CHANGE")
    terms = [parse_distribution(
        d["body"], code=parent.stock_code, venue="SZSE", expected_ex_date=expected_ex_date,
        currency_scheme_body=currency_scheme_body,
    )[0] for d in [previous, corrected]]
    if digest(terms[0]) != digest(terms[1]):
        raise BoundedError("PAYMENT_REVISION_OTHER_DISTRIBUTION_TERMS_CHANGED")
    return dict(
        contract_id=CONTRACT_ID, selected_announcement_id=actuals[1]["announcementId"],
        superseded_announcement_id=actuals[0]["announcementId"],
        correcting_notice_id=actuals[2]["announcementId"],
        source_proofs=source_proofs, event_count=1, payment_date=new_day,
        previous_payment_date=old_day, unchanged_terms_sha256=digest(terms[1]),
        unchanged_body_sha256=digest(unchanged(new, after)), observed_at=observed_at,
        old_values_overwritten=False, historical_PIT_restored=False,
        cumulative_adjustment_qualified=False, consumer_admission="NOT_GRANTED",
    )


def load_revision_evidence(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Re-extract the actual local PDF; no archived source_root dereference."""
    return dict(
        selection=json.loads((root / spec["selection"]).read_bytes())["selection"],
        receipt=json.loads((root / spec["receipt"]).read_bytes()),
        index=json.loads((root / spec["index"]).read_bytes()),
        PDF_sha256=file_sha(root / spec["PDF"]),
        body=subprocess.run(
            ["pdftotext", "-layout", str(root / spec["PDF"]), "-"],
            check=True, capture_output=True, timeout=60,
        ).stdout.decode(),
    )
