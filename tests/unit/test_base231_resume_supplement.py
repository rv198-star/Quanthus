from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base231_resume_supplement import (
    CONTRACT_ID,
    STATE_FIELDS,
    revise_suspension_end,
    state_census,
)


def case():
    old = {
        "listing_id": "L",
        "listing_status_id": "S",
        "status_type": "SUSPENSION",
        "status_value": "SUSPENDED",
        "effective_from": "2026-07-22T16:00:00Z",
        "effective_to": None,
        "knowledge_from": "2026-08-03T23:59:00Z",
        "knowledge_to": None,
        "record_source": "old",
        "source_record_id": "old-row",
        "source_revision": "old-rev",
        "source_artifact_sha256": "sha256:" + "b" * 64,
        "reason_code": "unchanged",
    }
    listing = {
        "listing_id": "L",
        "listing_code": "002036",
        "venue_id": "SZ",
        "effective_from": "2004-09-02T16:00:00Z",
        "effective_to": None,
        "knowledge_from": "2026-08-03T23:59:00Z",
        "knowledge_to": None,
    }
    session = {
        "venue_id": "SZ",
        "trade_date": date(2026, 7, 30),
        "is_open": True,
        "open_at": "2026-07-30T01:30:00Z",
    }
    notice = {
        "security_code": "002036",
        "announcement_id": "1225447271",
        "pdf_sha256": "sha256:" + "a" * 64,
        "resume_at": "2026-07-30T09:30:00+08:00",
        "pdf_received_at": "2026-10-13T09:37:00Z",
        "official_issuer_body_verified": True,
        "explicit_opening_time_resumption": True,
    }
    states = {
        "status": "COMPLETE_SDK_FIELD_STRINGS_WITH_RAW_WIRE",
        "fields": STATE_FIELDS,
        "request": {
            "code": "sz.002036",
            "fields": ",".join(STATE_FIELDS),
            "start_date": "2026-08-01",
            "end_date": "2026-09-30",
            "frequency": "d",
            "adjustflag": "3",
        },
        "rows": [["2026-08-03", "sz.002036", "1", "0"]],
        "completed_at": "2026-10-13T09:38:00Z",
    }
    return [old, listing, session, notice, states]


def project(c, observed_at="2026-10-13T09:39:00Z"):
    keys = ["before", "listing", "resume_session", "notice", "states"]
    binding = {k + "_sha256": digest(v) for k, v in zip(keys, c, strict=True)}
    binding.update(
        purpose=CONTRACT_ID,
        pdf_sha256=c[3]["pdf_sha256"],
        provider_response_sha256="sha256:" + "c" * 64,
        input_image_manifest_sha256="sha256:" + "d" * 64,
    )
    return revise_suspension_end(
        *c, expected_days=[date(2026, 8, 3)], binding=binding, observed_at=observed_at
    )


def test_new_knowledge_closes_only_named_suspension_and_preserves_old_atoms():
    c = case()
    frozen = deepcopy(c)
    rows, proof = project(c)
    assert c == frozen
    assert rows[0] == {**c[0], "knowledge_to": datetime(2026, 10, 13, 9, 39, tzinfo=timezone.utc)}
    assert rows[1]["effective_to"] == datetime(2026, 7, 30, 1, 30, tzinfo=timezone.utc)
    assert rows[1]["status_value"] == "SUSPENDED"
    assert rows[1]["listing_status_id"] == c[0]["listing_status_id"]
    assert proof["provider_flags_changed"] is False
    assert proof["historical_PIT_admission"] is False
    assert proof["consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "which,key,value",
    [
        (3, "security_code", "300242"),
        (3, "official_issuer_body_verified", False),
        (3, "explicit_opening_time_resumption", False),
        (3, "pdf_received_at", "2026-10-14T00:00:00Z"),
        (2, "venue_id", "SH"),
        (2, "trade_date", date(2026, 7, 31)),
        (2, "is_open", False),
        (2, "open_at", "2026-07-30T01:31:00Z"),
        (0, "knowledge_to", "2026-10-12T00:00:00Z"),
        (0, "effective_to", "2026-07-29T00:00:00Z"),
        (1, "effective_to", "2026-07-29T00:00:00Z"),
        (1, "knowledge_to", "2026-10-12T00:00:00Z"),
    ],
)
def test_rehashed_wrong_body_date_venue_listing_and_clock_rejected(which, key, value):
    c = case()
    c[which][key] = value
    with pytest.raises(BoundedError, match="RESUME_OFFICIAL"):
        project(c)


def test_cannot_backdate_repair_to_index_acquisition_or_old_PIT():
    with pytest.raises(BoundedError, match="RESUME_OFFICIAL"):
        project(case(), "2026-08-01T19:30:04+08:00")


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "wrong_code", "price_field", "nontrading", "out_of_scope"]
)
def test_complete_calendar_and_price_free_request_required(fault):
    c = case()
    r = c[4]
    if fault == "missing":
        r["rows"] = []
    elif fault == "duplicate":
        r["rows"].append(r["rows"][0].copy())
    elif fault == "wrong_code":
        r["rows"][0][1] = "sz.300242"
    elif fault == "price_field":
        r["request"]["fields"] += ",close"
    elif fault == "nontrading":
        r["rows"][0][2] = "0"
    else:
        r["rows"][0][0] = "2026-10-01"
    with pytest.raises(BoundedError, match="RESUME_"):
        project(c)


def test_missing_expected_calendar_denominator_rejected():
    with pytest.raises(BoundedError, match="RESUME_STATE_REQUEST"):
        state_census(case()[4], code="sz.002036", expected_days=[])
