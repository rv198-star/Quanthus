from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_st_reuse import qualify_capture


def case():
    early = datetime(2026, 7, 1, tzinfo=timezone.utc)
    original = {
        "interval_id": "synthetic-interval",
        "stock_code": "600001",
        "exchange": "SSE",
        "status_label": "ST",
        "is_st": 1,
        "effective_start": date(2026, 7, 25),
        "effective_end": None,
        "announcement_date": date(2026, 7, 24),
        "knowledge_cutoff": date(2026, 7, 24),
        "source": "synthetic-cninfo",
        "source_url_or_notice_id": "notice-one",
        "source_revision": "original-synthetic-v1",
        "evidence_path": "revisions/synthetic/events.parquet",
        "event_kind": "implement_other",
        "confidence": "proven",
        "notes": "synthetic",
        "built_at": early,
    }
    capture = dict(original, is_st="1", source_revision="weekly-synthetic-v2")
    for field in ("effective_start", "announcement_date", "knowledge_cutoff"):
        capture[field] = str(capture[field])
    capture.update(provider_acquired_at="2026-08-01T08:00:00+08:00", provider_dataset="st-status")
    native = {
        "record_source": "st_status_pit",
        "source_record_id": "ref.parquet#interval_id=synthetic-interval",
        "source_revision": original["source_revision"],
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "effective_from": datetime(2026, 7, 24, 16, tzinfo=timezone.utc),
        "effective_to": None,
        "knowledge_from": datetime(2026, 8, 3, tzinfo=timezone.utc),
        "knowledge_to": None,
        "security_id": "security-one",
        "status_type": "SPECIAL_TREATMENT",
        "status_value": "ST",
        "reason_code": "implement_other",
    }
    listing = dict(
        listing_code="600001", venue_id="venue-one", security_id="security-one",
        effective_from=early, effective_to=None, knowledge_from=early, knowledge_to=None,
    )
    venue = dict(venue_id="venue-one", mic_code="XSHG", knowledge_from=early, knowledge_to=None)
    event = dict(
        stock_code="600001", exchange="SSE", event_kind="implement_other",
        announcement_date=date(2026, 7, 24), effective_start_parsed=date(2026, 7, 25),
        ann_id="notice-one", url="https://synthetic.invalid/notice-one",
    )
    kwargs = dict(
        events=[event], listing=listing, venue=venue,
        original_source_sha256=native["source_artifact_sha256"],
        observed_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
    )
    return capture, original, native, kwargs


def test_exact_original_notice_native_dated_identity_and_clocks_reuse_without_mutation():
    args = case()
    before = deepcopy(args)
    proof = qualify_capture(*args[:3], **args[3])
    assert args == before
    assert proof["disposition"] == "EXACT_NO_CHANGE_NATIVE_ENTITY_REUSE"
    assert proof["original_wire_provider_acquisition"] is None
    assert proof["reported_provider_capture_clock"] == args[0]["provider_acquired_at"]
    assert proof["native_rows_mutated"] == proof["new_intervals_or_backfilled_PIT_times"] == 0
    assert proof["historical_PIT_admission"] is proof["whole_August_or_Base22_acceptance"] is False
    assert proof["native_status_unknown"] is False
    assert qualify_capture(*args[:3], **args[3]) == proof


def test_unknown_original_and_native_remain_unknown_without_a_notice():
    capture, original, native, kwargs = case()
    for row in (capture, original):
        row.update(confidence="not_proven", status_label="unknown", event_kind="no_proven_event",
                   source_url_or_notice_id=None, announcement_date=None)
    native.update(status_value="UNKNOWN", reason_code="BASELINE_NOT_PROVEN")
    kwargs["events"] = []
    proof = qualify_capture(capture, original, native, **kwargs)
    assert proof["native_status_unknown"] is True and proof["notice_row_sha256"] is None


@pytest.mark.parametrize("defect,reason", [
    ("changed-body", "CAPTURE_ORIGINAL_BODY_CHANGED"),
    ("absent-body", "ORIGINAL_BODY_INCOMPLETE"),
    ("unknown-clock", "OBSERVATION_CLOCK_UNKNOWN"),
    ("future-clock", "CAPTURE_AFTER_OBSERVATION"),
    ("unqualified-dataset", "REVISION_OR_DATASET_UNKNOWN"),
    ("missing-notice", "NOTICE_NOT_UNIQUE_OR_MISSING"),
    ("duplicate-notice", "NOTICE_NOT_UNIQUE_OR_MISSING"),
    ("different-notice-date", "NOTICE_NOT_UNIQUE_OR_MISSING"),
    ("different-native-value", "NATIVE_ATOMS_OR_LINEAGE_DIFFER"),
    ("different-native-source", "NATIVE_ATOMS_OR_LINEAGE_DIFFER"),
    ("different-native-date", "NATIVE_ATOMS_OR_LINEAGE_DIFFER"),
    ("wrong-code", "DATED_IDENTITY_VENUE_MISMATCH"),
    ("wrong-venue", "DATED_IDENTITY_VENUE_MISMATCH"),
    ("wrong-security", "DATED_IDENTITY_VENUE_MISMATCH"),
    ("expired-listing", "DATED_IDENTITY_VENUE_MISMATCH"),
    ("parent-unavailable", "PARENT_KNOWLEDGE_MISMATCH"),
    ("september", "BODY_SCOPE_INVALID"),
    ("boolean-flag", "FLAG_UNKNOWN"),
    ("unknown-to-normal", "UNKNOWN_BASELINE_MUST_REMAIN_UNKNOWN"),
])
def test_evidence_gaps_and_changes_fail_without_overwriting_any_input(defect, reason):
    capture, original, native, kwargs = case()
    if defect == "changed-body":
        capture["notes"] = "changed"
    elif defect == "absent-body":
        capture.pop("knowledge_cutoff")
    elif defect == "unknown-clock":
        capture["provider_acquired_at"] = None
    elif defect == "future-clock":
        capture["provider_acquired_at"] = "2026-10-08T00:00:00Z"
    elif defect == "unqualified-dataset":
        capture["provider_dataset"] = "other"
    elif defect == "missing-notice":
        kwargs["events"] = []
    elif defect == "duplicate-notice":
        kwargs["events"] *= 2
    elif defect == "different-notice-date":
        kwargs["events"][0]["effective_start_parsed"] = date(2026, 7, 26)
    elif defect == "different-native-value":
        native["status_value"] = "NORMAL"
    elif defect == "different-native-source":
        native["source_artifact_sha256"] = "sha256:" + "b" * 64
    elif defect == "different-native-date":
        native["effective_from"] += timedelta(days=1)
    elif defect == "wrong-code":
        kwargs["listing"]["listing_code"] = "600002"
    elif defect == "wrong-venue":
        kwargs["venue"]["mic_code"] = "XSHE"
    elif defect == "wrong-security":
        kwargs["listing"]["security_id"] = "other"
    elif defect == "expired-listing":
        kwargs["listing"]["effective_to"] = native["effective_from"]
    elif defect == "parent-unavailable":
        kwargs["venue"]["knowledge_from"] = kwargs["observed_at"]
    elif defect == "september":
        for row in (capture, original):
            row["effective_start"] = date(2026, 9, 1)
    elif defect == "boolean-flag":
        capture["is_st"] = True
    else:
        for row in (capture, original):
            row.update(
                confidence="not_proven", event_kind="no_proven_event", source_url_or_notice_id=None
            )
    before = deepcopy((capture, original, native, kwargs))
    with pytest.raises((BoundedError, RuntimeError), match=reason):
        qualify_capture(capture, original, native, **kwargs)
    assert (capture, original, native, kwargs) == before


def test_future_interval_endpoint_is_preserved_instead_of_truncated_at_business_cutoff():
    capture, original, native, kwargs = case()
    capture["effective_end"] = "2026-09-02"
    original["effective_end"] = date(2026, 9, 2)
    native["effective_to"] = datetime(2026, 9, 2, 16, tzinfo=timezone.utc)
    assert qualify_capture(capture, original, native, **kwargs)["native_row_sha256"]
    assert native["effective_to"] == datetime(2026, 9, 2, 16, tzinfo=timezone.utc)
