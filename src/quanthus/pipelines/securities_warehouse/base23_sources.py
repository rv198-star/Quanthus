"""Pinned September source integrity and coverage; never a complete image receipt.

Base 2.2's frozen default remains August. An explicit September successor reads
existing retained ODS only. READY and member bytes do not qualify rows, prove
no-change domains, or grant consumer admission. Missing sessions stay missing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_sources import SourceAssessmentError, assess_package

CONTRACT_ID = "p10-base23-retained-september-sources-v1"
START = date(2026, 9, 1)
THROUGH = date(2026, 9, 30)
EXPECTED_SESSIONS = [
    str(day)
    for offset in range((THROUGH - START).days + 1)
    if (day := START + timedelta(days=offset)).weekday() < 5 and day != date(2026, 9, 25)
]
DAILY_REQUIRED = {
    "daily_ohlcv_none_delta",
    "daily_status_observation_delta",
    "valuation_daily_delta",
    "security_master",
    "stock_list",
    "trading_calendar",
    "index_daily_delta",
    "acquisition_log",
}


def assess_september_release(root: Path, *, manifest_sha256: str) -> dict[str, Any]:
    """All actual member hashes/footers, with explicit opt-in September dates."""
    result = assess_package(
        root,
        manifest_name="release.json",
        expected_manifest_sha256=manifest_sha256,
        ods_business_window=(START, THROUGH),
    )
    if result["identity"].get("kind") != "ods_release":
        raise SourceAssessmentError("BASE23_ODS_RELEASE_REQUIRED")
    result["base23_contract_id"] = CONTRACT_ID
    result["business_window"] = {"start": str(START), "through": str(THROUGH)}
    return result


def coverage_census(
    reports: list[dict[str, Any]], *, calendar_evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Require explicit authoritative dates; nontrading READY never fills a day.

    This is an early necessary gate. PASS here still requires full row/date/key,
    universe, source qualification and every one of the 33 domain closures later.
    Different revisions are kept in the source inventory, not silently preferred.
    """
    if (
        calendar_evidence.get("timezone") != "Asia/Shanghai"
        or calendar_evidence.get("through") != str(THROUGH)
        or calendar_evidence.get("start") != str(START)
        or set(calendar_evidence.get("venues", {})) != {"SSE", "SZSE"}
    ):
        raise SourceAssessmentError("BASE23_AUTHORITATIVE_CALENDAR_REQUIRED")
    expected: dict[str, set[str]] = {}
    for venue, item in calendar_evidence["venues"].items():
        days = item.get("trading_dates", [])
        if (
            days != EXPECTED_SESSIONS
            or any(not START <= date.fromisoformat(day) <= THROUGH for day in days)
            or not item.get("source_url", "").startswith("https://")
            or not item.get("observed_at")
            or not item.get("source_sha256", "").startswith("sha256:")
        ):
            raise SourceAssessmentError("BASE23_AUTHORITATIVE_CALENDAR_REQUIRED")
        expected[venue] = set(days)
    present: dict[str, list[str]] = {}
    failed = []
    empty_or_incomplete = []
    artifact_rows: Counter[str] = Counter()
    for report in reports:
        if report.get("base23_contract_id") != CONTRACT_ID:
            raise SourceAssessmentError("BASE23_SOURCE_REPORT_CONTRACT_CHANGED")
        identity = report["identity"]
        if not START <= date.fromisoformat(identity["market_date"]) <= THROUGH:
            raise SourceAssessmentError("SOURCE_BUSINESS_DATE_OUTSIDE_SCOPE")
        if report["status"] != "PASS":
            failed.append(identity["identity"])
            continue
        members = {item["name"]: item for item in report["files"] if "name" in item}
        if len(members) != len(report["files"]):
            raise SourceAssessmentError("BASE23_DUPLICATE_OR_UNKNOWN_ARTIFACT_NAME")
        for name, member in members.items():
            artifact_rows[name] += member.get("rows", 0)
        if identity["schedule"] != "daily" or "daily_ohlcv_none_delta" not in members:
            continue
        if not set(members) >= DAILY_REQUIRED or any(
            not members[name].get("rows")
            for name in ["daily_ohlcv_none_delta", "daily_status_observation_delta"]
        ):
            empty_or_incomplete.append(identity["identity"])
            continue
        present.setdefault(identity["market_date"], []).append(identity["identity"])
    missing = {venue: sorted(days - set(present)) for venue, days in expected.items()}
    result = {
        "kind": "base23_necessary_source_coverage_not_row_qualification",
        "contract_id": CONTRACT_ID,
        "business_through": str(THROUGH),
        "calendar_evidence_identity": digest(calendar_evidence),
        "observed_core_dates": sorted(present),
        "core_revisions_by_date": present,
        "missing_core_dates_by_venue": missing,
        "physical_failures": sorted(failed),
        "empty_or_incomplete_core_releases": sorted(empty_or_incomplete),
        "artifact_declared_footer_rows": dict(sorted(artifact_rows.items())),
        "status": "BLOCKED_REQUIRED_DAILY_SOURCE"
        if failed or any(missing.values()) or empty_or_incomplete
        else "NECESSARY_GATE_PASS_ROW_AND_33_DOMAIN_QUALIFICATION_NOT_RUN",
        "complete_Base23": False,
        "candidate_READY": False,
        "consumer_admission": "NOT_GRANTED",
        "BSE_coverage": "NOT_ESTABLISHED_BY_SSE_SZSE_CALENDAR",
    }
    result["identity"] = digest(result)
    return result
