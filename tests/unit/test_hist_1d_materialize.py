from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.hist_1d_materialize import daily_records, factor_records, subtype


def test_status_subtypes_require_official_name_and_preserve_binary_unknown() -> None:
    changes = [(date(2005, 1, 4), "S*ST样本"), (date(2005, 1, 7), "ST样本")]
    assert subtype("1", date(2005, 1, 5), changes)[0] == "DELISTING_RISK"
    assert subtype("1", date(2005, 1, 7), changes)[0] == "ST"
    assert subtype("1", date(2005, 1, 5), [])[0] == "UNKNOWN"
    assert subtype("0", date(2005, 1, 5), [])[0] == "NORMAL"
    assert subtype("0", date(1995, 1, 5), [])[0] == "UNKNOWN"


def test_daily_decimal_units_suspension_and_acquisition_clock() -> None:
    fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "tradestatus",
        "isST",
    ]
    acquired = "2026-11-11T00:00:00+00:00"
    payload = {
        "error_code": "0",
        "acquisition_completed_at": acquired,
        "fields": fields,
        "rows": [
            ["2005-01-04", "sz.000001", "1.0000000001", "2", "1", "1.5", "123", "180.25", "1", "0"],
            ["2005-01-05", "sz.000001", "", "", "", "", "0", "0", "0", "1"],
        ],
    }
    task = {
        "listing_id": "l",
        "security_id": "s",
        "code": "sz.000001",
        "venue_id": "v",
        "start_date": "2005-01-01",
        "end_date": "2005-01-31",
    }
    sessions = {
        ("v", date(2005, 1, d)): (
            f"session-{d}",
            datetime(2005, 1, d, 1, 30, tzinfo=timezone.utc),
            datetime(2005, 1, d, 7, tzinfo=timezone.utc),
            d,
        )
        for d in [4, 5]
    }
    rows = daily_records(payload, "sha256:" + "a" * 64, task, sessions, [], None, None)
    assert len(rows["bar"]) == 1
    bar = rows["bar"][0]
    assert bar["open"] == Decimal("1.0000000001")
    assert bar["volume"] == Decimal(123) and bar["turnover"] == Decimal("180.25")
    assert bar["knowledge_from"].isoformat() == acquired
    assert rows["listing-status"][0]["status_value"] == "SUSPENDED"
    assert any(
        r["status_type"] == "PROVIDER_DAILY_STATE" and r["status_value"] == "ST"
        for r in rows["security-status"]
    )
    payload["rows"][0][1] = "sh.600601"
    with pytest.raises(ValueError, match="DAILY_SOURCE_IDENTITY_INVALID"):
        daily_records(payload, "sha256:" + "a" * 64, task, sessions, [], None, None)


def test_native_factor_uses_lexical_native_column_and_separate_knowledge_clock() -> None:
    payload = {
        "error_code": "0",
        "acquisition_completed_at": "2026-11-11T00:00:00+00:00",
        "fields": [
            "code",
            "dividOperateDate",
            "foreAdjustFactor",
            "backAdjustFactor",
            "adjustFactor",
        ],
        "rows": [["sh.600601", "2010-07-12", "0.878719", "5680.849855", "5680.849855"]],
    }
    rows = factor_records(
        payload, "sha256:" + "a" * 64, {"listing_id": "l", "security_id": "s", "code": "sh.600601"}
    )
    assert rows[0]["factor_value"] == Decimal("5680.849855")
    assert rows[0]["adjustment_direction"] == "PROVIDER_NATIVE"
    assert rows[0]["effective_from"].isoformat() == "2010-07-11T16:00:00+00:00"
