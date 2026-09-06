from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quanthus.structure_v2_inputs import (
    DailyInput,
    InputPolicy,
    admit_daily,
    build_readiness,
    digest,
)

TZ = timezone(timedelta(hours=8))
SHA = "sha256:" + "a" * 64


def policy():
    return InputPolicy(SHA, ("000001",), "calendar-v1")


def row(index=0, close="95", **changes):
    day = date(2020, 1, 2) + timedelta(days=index)
    initial = DailyInput(
        "000001", day, index, datetime(day.year, day.month, day.day, 15, tzinfo=TZ),
        "security-A", "listing-A", "SZSE", Decimal("100"), Decimal("90"),
        None if close is None else Decimal(close), Decimal("1000"),
        f"source-{day}", "revision-v1", SHA,
        "2026-07-01T00:00:00+00:00", "initial-epoch", "final",
        calendar_member_digest=SHA, action_member_digest=SHA,
    )
    return replace(initial, **changes)


def provider():
    return {"provider_lineage_digest": SHA, "point_in_time": {
        "strategy_cutoff_enforced": True, "record_view": "retrospective_dwd_record_view",
        "per_decision_publication_proven": False,
    }}


def test_admission_has_explicit_weaker_view_without_fabricated_publication():
    observed = admit_daily(row(), policy())
    assert observed.admitted
    receipt = build_readiness((observed,), policy(), provider(),
                             report_start=row().market_date, report_end=row().market_date)
    assert receipt["permitted_view"] == "retrospective"
    assert not receipt["public_reconstruction_admitted"]
    assert not receipt["platform_as_known_admitted"]
    assert receipt["attempted"] == receipt["admitted"] == 1


@pytest.mark.parametrize("changes,reason", [
    ({"finality": "partial"}, "daily_finality_unproven"),
    ({"finality": "unknown"}, "daily_finality_unproven"),
    ({"close": None}, "invalid_or_missing_daily_price"),
    ({"low": Decimal("110")}, "inconsistent_ohlc"),
    ({"listing_id": ""}, "identity_unproven"),
    ({"coordinate_epoch": ""}, "coordinate_epoch_unproven"),
    ({"source_revision": ""}, "source_version_unproven"),
    ({"quality_reason": "suspended"}, "suspended"),
    ({"quality_reason": "corporate_action_exception"}, "corporate_action_exception"),
])
def test_composed_admission_rejects_each_missing_gate(changes, reason):
    result = admit_daily(row(**changes), policy())
    assert not result.admitted and result.reason == reason
    assert "sell" not in result.reason


def test_optional_volume_does_not_destroy_valid_prices():
    result = admit_daily(row(volume=None), policy())
    assert result.admitted and result.missing_volume


def test_no_view_downgrade_and_no_omitted_security_denominator():
    with pytest.raises(ValueError):
        replace(policy(), view="public_reconstruction")
    other = replace(policy(), security_codes=("000001", "600519"))
    with pytest.raises(ValueError, match="denominator"):
        build_readiness((admit_daily(row(), other),), other, provider(),
                        report_start=row().market_date, report_end=row().market_date)


def test_rejections_stay_in_report_without_rewriting_previous_members():
    earlier = row()
    missing = row(1, quality_reason="missing_bar")
    records = tuple(admit_daily(item, policy()) for item in (earlier, missing))
    receipt = build_readiness(records, policy(), provider(),
                             report_start=earlier.market_date, report_end=missing.market_date)
    assert receipt["attempted"] == 2 and receipt["rejected"] == 1
    assert receipt["rejection_reasons"] == {"missing_bar": 1}
    assert earlier.member_digest == row().member_digest
    assert replace(earlier, session_index=100).member_digest == earlier.member_digest
    assert replace(earlier, source_revision="corrected").member_digest != earlier.member_digest
    assert digest(provider()) == digest(provider())
    corrected_reference = replace(earlier, action_member_digest="sha256:" + "b" * 64)
    assert corrected_reference.coordinate_ref == earlier.coordinate_ref
    assert corrected_reference.member_digest != earlier.member_digest
