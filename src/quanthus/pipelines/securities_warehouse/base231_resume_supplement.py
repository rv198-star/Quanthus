"""New-knowledge suspension end revisions from bounded official evidence.

This changes a scoped governance view, never the old sealed source flags or
historical consumer admission. The physical caller must replay the official PDF
body and the complete price-free provider response from local hashed evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock

CONTRACT_ID = "p10-base231-official-resume-new-knowledge-v1"
STATE_FIELDS = ["date", "code", "tradestatus", "isST"]


def state_census(
    response: Mapping[str, Any], *, code: str, expected_days: Sequence[date]
) -> dict[date, dict[str, str]]:
    """Exact dated state closure; a missing response is never a trading claim."""
    request = response.get("request", {})
    if (
        response.get("status") != "COMPLETE_SDK_FIELD_STRINGS_WITH_RAW_WIRE"
        or response.get("fields") != STATE_FIELDS
        or request.get("code") != code
        or request.get("fields") != ",".join(STATE_FIELDS)
        or request.get("start_date") != "2026-08-01"
        or request.get("end_date") != "2026-09-30"
        or request.get("frequency") != "d"
        or request.get("adjustflag") != "3"
        or not expected_days
        or len(set(expected_days)) != len(expected_days)
    ):
        raise BoundedError("RESUME_STATE_REQUEST_OR_SCOPE_INVALID")
    result = {}
    for cells in response.get("rows", []):
        if (
            not isinstance(cells, list)
            or len(cells) != 4
            or any(not isinstance(c, str) for c in cells)
            or cells[1] != code
            or cells[2] not in {"0", "1"}
            or cells[3] not in {"0", "1"}
        ):
            raise BoundedError("RESUME_STATE_ROW_INVALID")
        try:
            day = date.fromisoformat(cells[0])
        except ValueError as exc:
            raise BoundedError("RESUME_STATE_ROW_INVALID") from exc
        if day in result or not date(2026, 8, 1) <= day <= date(2026, 9, 30):
            raise BoundedError("RESUME_STATE_DUPLICATE_OR_DATE_INVALID")
        result[day] = dict(zip(STATE_FIELDS, cells, strict=True))
    if set(result) != set(expected_days):
        raise BoundedError("RESUME_STATE_EXACT_CALENDAR_CLOSURE_MISSING")
    return result


def revise_suspension_end(
    before: Mapping[str, Any],
    listing: Mapping[str, Any],
    resume_session: Mapping[str, Any],
    notice: Mapping[str, Any],
    states: Mapping[str, Any],
    *,
    expected_days: Sequence[date],
    binding: Mapping[str, Any],
    observed_at: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """End only the named suspension, retaining its original knowledge version.

    An explicit official opening-time resumption terminates that suspension; it
    does not assert continuous tradability for other/unexamined periods. Provider
    observations corroborate only the exact calendar dates actually returned.
    """
    code = "sz." + listing["listing_code"]
    dated = state_census(states, code=code, expected_days=expected_days)
    clock, resume = _clock(observed_at), _clock(notice["resume_at"])
    day = resume.astimezone(ZoneInfo("Asia/Shanghai")).date()
    required_hashes = ("pdf_sha256", "provider_response_sha256", "input_image_manifest_sha256")
    if (
        binding.get("purpose") != CONTRACT_ID
        or binding.get("before_sha256") != digest(dict(before))
        or binding.get("listing_sha256") != digest(dict(listing))
        or binding.get("resume_session_sha256") != digest(dict(resume_session))
        or binding.get("notice_sha256") != digest(dict(notice))
        or binding.get("states_sha256") != digest(dict(states))
        or any(
            not isinstance(binding.get(k), str)
            or len(binding[k]) != 71
            or not binding[k].startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in binding[k][7:])
            for k in required_hashes
        )
        or notice.get("pdf_sha256") != binding["pdf_sha256"]
        or notice.get("security_code") != listing["listing_code"]
        or notice.get("official_issuer_body_verified") is not True
        or notice.get("explicit_opening_time_resumption") is not True
        or not isinstance(notice.get("announcement_id"), str)
        or not notice["announcement_id"]
        or before["listing_id"] != listing["listing_id"]
        or before["status_type"] != "SUSPENSION"
        or before["status_value"] != "SUSPENDED"
        or before.get("effective_to") is not None
        or before.get("knowledge_to") is not None
        or resume_session.get("venue_id") != listing["venue_id"]
        or resume_session.get("trade_date") != day
        or resume_session.get("is_open") is not True
        or _clock(resume_session["open_at"]) != resume
        or resume <= _clock(before["effective_from"])
        or resume < _clock(listing["effective_from"])
        or listing.get("effective_to") is not None
        and resume >= _clock(listing["effective_to"])
        or clock <= _clock(before["knowledge_from"])
        or clock < _clock(listing["knowledge_from"])
        or listing.get("knowledge_to") is not None
        and clock >= _clock(listing["knowledge_to"])
        or clock < _clock(notice["pdf_received_at"])
        or clock < _clock(states["completed_at"])
        or any(d < day or row["tradestatus"] != "1" for d, row in dated.items())
    ):
        raise BoundedError("RESUME_OFFICIAL_CLOSURE_OR_NEW_KNOWLEDGE_INVALID")
    revision = CONTRACT_ID + ":" + digest(dict(binding))
    old = {**before, "knowledge_to": clock}
    after = {
        **before,
        "knowledge_from": clock,
        "effective_to": resume,
        "record_source": "cninfo.official_issuer_resume",
        "source_record_id": notice["announcement_id"],
        "source_revision": revision,
        "source_artifact_sha256": notice["pdf_sha256"],
    }
    proof = {
        "contract_id": CONTRACT_ID,
        "binding_identity": digest(dict(binding)),
        "before_sha256": digest(dict(before)),
        "closed_old_sha256": digest(old),
        "new_version_sha256": digest(after),
        "listing_id": listing["listing_id"],
        "security_code": listing["listing_code"],
        "resume_at": resume,
        "knowledge_observed_at": clock,
        "corroborated_trade_dates": [str(d) for d in sorted(dated)],
        "provider_flags_changed": False,
        "historical_publication_or_acquisition_inferred": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "force_sale": False,
        "original_sealed_image_changed": False,
    }
    proof["identity"] = digest(proof)
    return [old, after], proof
