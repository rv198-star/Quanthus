from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse import p10_session_schedule as s


def fixture():
    row = {
        "calendar_session_id": "session",
        "venue_id": "venue",
        "trade_date": date(2026, 8, 3),
        "is_open": True,
        "session_type": "day",
        "open_at": None,
        "close_at": None,
        "observation_start": datetime.fromisoformat("2026-08-03T00:00:00+08:00"),
        "observation_end": datetime.fromisoformat("2026-08-04T00:00:00+08:00"),
        "knowledge_from": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "source_artifact_sha256": "sha256:" + "a" * 64,
    }
    rule = {
        "mic": "XSHG",
        "body_sha256": s.RULES["XSHG"]["sha256"],
        "acquired_at": "2026-10-14T07:37:28+00:00",
        "evidence_kind": "official_declared_schedule",
    }
    return row, rule


def test_derived_clock_preserves_unknown_native_and_true_knowledge():
    row, rule = fixture()
    before = deepcopy(row)
    r = s.derive_session(
        row,
        mic="XSHG",
        rule=rule,
        calendar_sha256="sha256:" + "b" * 64,
        qualified_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
    )
    assert row == before
    assert r["native_open_at"] is None
    assert r["declared_regular_open_at"] == "2026-08-03T09:30:00+08:00"
    assert r["evidence_kind"] == "rule_derived_not_source_observed"
    assert not r["historical_pit_granted"] and not r["opening_auction_fill_proven"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"trade_date": date(2026, 8, 4)}, "SCHEDULE_CALENDAR_DATE"),
        ({"trade_date": date(2026, 7, 3)}, "SCHEDULE_BUSINESS_SCOPE"),
        ({"is_open": False}, "SCHEDULE_EXACT_MISSING_OPEN_ROW"),
        ({"open_at": datetime(2026, 8, 3, tzinfo=timezone.utc)}, "SCHEDULE_EXACT_MISSING_OPEN_ROW"),
    ],
)
def test_reject_bad_native_scope(change, reason):
    row, rule = fixture()
    row.update(change)
    with pytest.raises(ValueError, match=reason):
        s.derive_session(
            row,
            mic="XSHG",
            rule=rule,
            calendar_sha256="sha256:" + "b" * 64,
            qualified_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
        )


def test_reject_wrong_exchange_and_backdated_qualification():
    row, rule = fixture()
    with pytest.raises(ValueError, match="SCHEDULE_RULE_IDENTITY"):
        s.derive_session(
            row,
            mic="XSHE",
            rule=rule,
            calendar_sha256="sha256:" + "b" * 64,
            qualified_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="SCHEDULE_NOT_AVAILABLE"):
        s.derive_session(
            row,
            mic="XSHG",
            rule=rule,
            calendar_sha256="sha256:" + "b" * 64,
            qualified_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
