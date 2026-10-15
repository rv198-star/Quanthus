"""Current official scheme currency evidence; never an old-source/PIT recovery."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant

CONTRACT_ID = "p10-base231-official-current-scheme-currency-corroboration-v1"
REPORT_CONTRACT_ID = "p10-base231-official-report-defined-dividend-currency-v1"
REPORT_TITLE = re.compile(r"(?:.+[:：])?2026年半年度报告(?:[（(]更新后[）)])?")


def qualify_currency_source(
    evidence: dict[str, Any], *, implementation: dict[str, Any], observed_at: datetime
) -> dict[str, Any]:
    """Bind issuer, event period, exact actual official response and new clocks.

    This qualifies a supporting source, not the terms. The independent parser
    must also prove approved/implemented consistency and equal per-ten values.
    """
    actual = evidence["actual_index_row"]
    report = REPORT_TITLE.fullmatch(actual.get("announcementTitle", "")) is not None
    index = evidence["index"]
    index_receipt, receipt = evidence["index_receipt"], evidence["PDF_receipt"]
    clock = instant(observed_at)
    if (
        actual not in (index.get("announcements") or [])
        or actual.get("secCode") != implementation.get("secCode")
        or not isinstance(actual.get("orgId"), str)
        or not actual["orgId"]
        or actual["orgId"] != implementation.get("orgId")
        or not isinstance(actual.get("announcementId"), str)
        or not re.fullmatch(r"[0-9]+", actual["announcementId"])
        or not re.search(r"2026年?(?:半年度|中期)", actual.get("announcementTitle", ""))
        or (not report and not re.search(r"利润分配|分红", actual.get("announcementTitle", "")))
        or re.search(r"实施|取消|终止|调整", actual["announcementTitle"])
        or type(actual.get("announcementTime")) is not int
        or type(implementation.get("announcementTime")) is not int
    ):
        raise BoundedError("DISTRIBUTION_CURRENCY_SCHEME_EVENT_IDENTITY_UNPROVEN")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    published = epoch + timedelta(milliseconds=actual["announcementTime"])
    implementation_at = epoch + timedelta(milliseconds=implementation["announcementTime"])
    day = published.astimezone(ZoneInfo("Asia/Shanghai")).date()
    adjunct = "finalpage/" + day.isoformat() + "/" + actual["announcementId"] + ".PDF"
    if report and actual.get("adjunctUrl") == adjunct[:-4] + ".pdf":
        adjunct = actual["adjunctUrl"]
    uri = "https://static.cninfo.com.cn/" + adjunct
    if (
        actual.get("adjunctUrl") != uri.removeprefix("https://static.cninfo.com.cn/")
        or not published <= implementation_at <= clock
        or day > date(2026, 9, 30)
        or not published <= instant(datetime.fromisoformat(index_receipt["completed_at"])) <= clock
        or not published <= instant(datetime.fromisoformat(receipt["completed_at"])) <= clock
        or instant(datetime.fromisoformat(index_receipt["completed_at"]))
        > instant(datetime.fromisoformat(receipt["completed_at"]))
    ):
        raise BoundedError("DISTRIBUTION_CURRENCY_SCHEME_REAL_CLOCK_UNPROVEN")
    for item, expected_uri, expected_sha in [
        (index_receipt, "https://www.cninfo.com.cn/new/hisAnnouncement/query", digest(index)),
        (receipt, uri, evidence["PDF_sha256"]),
    ]:
        if (
            item.get("URI") != expected_uri
            or type(item.get("http_status")) is not int
            or item["http_status"] != 200
            or item.get("error") is not None
            or item.get("response_cap_reached") is not False
            or item.get("actual_transport") != "urllib.request"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha)
            or item.get("body_sha256")
            != (evidence["index_sha256"] if item is index_receipt else expected_sha)
            or item.get("actual_response_body_bytes")
            != (evidence["index_bytes"] if item is index_receipt else evidence["PDF_bytes"])
        ):
            raise BoundedError("DISTRIBUTION_CURRENCY_SCHEME_SOURCE_RECEIPT_UNPROVEN")
    query = index_receipt.get("request_payload") or {}
    if query.get("stock") != actual["secCode"] + "," + actual["orgId"]:
        raise BoundedError("DISTRIBUTION_CURRENCY_SCHEME_QUERY_ISSUER_UNPROVEN")
    return dict(
        contract_id=REPORT_CONTRACT_ID if report else CONTRACT_ID,
        implementation_index_row_sha256=digest(implementation),
        scheme_index_row_sha256=digest(actual),
        scheme_index_sha256=evidence["index_sha256"],
        scheme_index_receipt_sha256=digest(index_receipt),
        scheme_PDF_sha256=evidence["PDF_sha256"],
        scheme_PDF_receipt_sha256=digest(receipt),
        scheme_body_text_sha256=digest(evidence["body"]),
        scheme_publication_at=published,
        source_known_at=max(
            datetime.fromisoformat(r["completed_at"]) for r in [index_receipt, receipt]
        ),
        observed_at=clock,
        source_priority_granted=False,
        old_acceptance_claimed=False,
        historical_PIT_restored=False,
        factor_admission="NOT_GRANTED",
        **({"currency_scope": "EXPLICIT_REPORT_GLOSSARY_AND_SAME_EVENT_DIVIDEND_ONLY"}
           if report else {}),
    )
