from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_quality import (
    industry_exception,
    visibility_exception,
)

CLOCK = datetime(2026, 10, 5, tzinfo=timezone.utc)
SHA = "sha256:" + "a" * 64


def visibility(**changes):
    return {
        "stock_code": "000001",
        "exchange": "SZSE",
        "effective_date": date(2019, 3, 27),
        "source_revision": "original",
        "knowledge_cutoff": "2026-07-24",
        "exception_type": "factor_pit_visibility",
        "reason_code": "no_proveable_announce_after_em_exact_link",
        "visibility_origin": "harvest_day_exception|no_proveable_announce",
        "policy": (
            "factor_unavailable_before_knowledge_cutoff; no forge; degrade/block adj features"
        ),
        "recorded_at": None,
        **changes,
    }


def test_visibility_is_a_new_negative_observation_not_a_repaired_cutoff():
    raw = visibility()
    original = dict(raw)
    row = visibility_exception(raw, detected_at=CLOCK, source_sha256=SHA)
    assert raw == original and raw["knowledge_cutoff"] == "2026-07-24"
    assert "2026-07-24" in row["affected_business_key_json"]
    assert row["detected_at"] == row["knowledge_from"] == CLOCK
    assert row["resolution_state"] == "UNRESOLVED"
    assert row["disposition"] == "PRESERVE_UNAVAILABLE_NO_CONSUMER_ADMISSION"
    _, model, extension = load_contract(Path("config/pipelines"))
    schema = schemas(model, extension)["dwd_quality_exception"]
    restored = pa.Table.from_pylist([row], schema=schema).to_pylist()[0]
    assert restored == row


def test_original_string_date_and_declared_cutoff_remain_original_information():
    raw = visibility(effective_date="2019-03-27")
    row = visibility_exception(raw, detected_at=CLOCK, source_sha256=SHA)
    assert row["affected_effective_at"].date() == date(2019, 3, 27)
    assert raw["effective_date"] == "2019-03-27"
    assert raw["knowledge_cutoff"] == "2026-07-24"
    assert row["knowledge_from"] == CLOCK


@pytest.mark.parametrize(
    "changes",
    [
        {"visibility_origin": "known"},
        {"reason_code": "OTHER_ERROR"},
        {"effective_date": date(2026, 9, 1)},
        {"effective_date": datetime(2026, 7, 31)},
    ],
)
def test_unreviewed_visibility_identity_or_date_is_rejected(changes):
    with pytest.raises(BoundedError):
        visibility_exception(visibility(**changes), detected_at=CLOCK, source_sha256=SHA)


def sample():
    listing = {
        "listing_id": "l",
        "listing_code": "000001",
        "security_id": "s",
        "effective_from": datetime(2000, 1, 1, tzinfo=timezone.utc),
        "effective_to": datetime(2021, 1, 1, tzinfo=timezone.utc),
    }
    meta = {
        "identity_provenance": {
            "security_id": "s",
            "listing_code": "000001",
            "listing_bindings": [{"listing_id": "l"}],
        },
        "native_industry_present": False,
        "static_membership": {"industry": "unqualified"},
    }
    return listing, meta


def test_exact_ended_identity_can_be_unavailable_without_promoting_static_industry():
    listing, metadata = sample()
    row = industry_exception(
        metadata,
        listing=listing,
        detected_at=CLOCK,
        source_sha256=SHA,
        snapshot_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    assert row["affected_table"] == "dwd_company_industry_membership"
    assert "unqualified" not in row["affected_business_key_json"]
    assert row["authority"] == "P10_SOURCE_GOVERNANCE_ONLY"
    assert row["resolution_state"] == "UNRESOLVED"


@pytest.mark.parametrize("change", ["security", "listing", "active", "later_end", "native_present"])
def test_unqualified_metadata_never_invents_historical_identity(change):
    listing, metadata = sample()
    if change == "security":
        listing["security_id"] = "other"
    elif change == "listing":
        listing["listing_id"] = "other"
    elif change == "active":
        listing["effective_to"] = None
    elif change == "later_end":
        listing["effective_to"] = CLOCK
    else:
        metadata["native_industry_present"] = True
    with pytest.raises(BoundedError):
        industry_exception(
            metadata,
            listing=listing,
            detected_at=CLOCK,
            source_sha256=SHA,
            snapshot_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        )
