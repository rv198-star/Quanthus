from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base24_execution_repair as repair
from tests.unit.test_base24_p6_purpose import market_row

CUTOFF = datetime(2026, 10, 23, tzinfo=timezone.utc)


def refs():
    start = datetime(2026, 7, 8, 1, tzinfo=timezone.utc)
    known = start - timedelta(days=10)
    row = dict(
        listing_id="L",
        calendar_session_id="C",
        knowledge_from=known,
        knowledge_to=None,
        observation_start=start,
        observation_end=start + timedelta(hours=6),
    )
    listing = dict(
        listing_id="L",
        venue_id="SZSE",
        knowledge_from=known,
        knowledge_to=None,
        effective_from=known,
        effective_to=None,
    )
    calendar = dict(
        calendar_session_id="C",
        venue_id="SZSE",
        knowledge_from=known,
        knowledge_to=None,
        trade_date=date(2026, 7, 8),
    )
    return row, [listing], [calendar]


def test_correct_native_state_closure_preserves_input():
    row, listings, calendars = refs()
    before = deepcopy((row, listings, calendars))
    repair.validate_native(row, listings, calendars, CUTOFF)
    assert (row, listings, calendars) == before


@pytest.mark.parametrize(
    "change,reason",
    [
        ("orphan", "NATIVE_REFERENCE_CARDINALITY"),
        ("wrong_date", "NATIVE_CALENDAR_BUSINESS_DATE"),
        ("wrong_venue", "NATIVE_CALENDAR_VENUE"),
        ("duplicate", "NATIVE_REFERENCE_CARDINALITY"),
        ("expired", "NATIVE_NOT_VISIBLE_AT_CUTOFF"),
    ],
)
def test_repacked_native_semantic_errors_fail(change, reason):
    row, listings, calendars = refs()
    if change == "orphan":
        row["calendar_session_id"] = "unknown"
    elif change == "wrong_date":
        calendars[0]["trade_date"] = date(2026, 8, 3)
    elif change == "wrong_venue":
        calendars[0]["venue_id"] = "SSE"
    elif change == "duplicate":
        calendars.append(dict(calendars[0]))
    else:
        row["knowledge_to"] = CUTOFF
    with pytest.raises(ValueError, match=reason):
        repair.validate_native(row, listings, calendars, CUTOFF)


def test_shanghai_business_date_not_utc_date():
    row, listings, calendars = refs()
    row["observation_start"] = datetime(2026, 7, 7, 23, tzinfo=timezone.utc)
    repair.validate_native(row, listings, calendars, CUTOFF)


def test_required_column_missing_even_for_valid_parquet(tmp_path):
    schema = pa.schema(
        [("calendar_session_id", pa.string()), ("input_revision_digest", pa.string())]
    )
    path = tmp_path / "state.parquet"
    pq.write_table(pa.table({"calendar_session_id": ["C"]}), path)
    with pytest.raises(ValueError, match="NATIVE_FULL_SCHEMA"):
        repair.native_rows(path, schema)


@pytest.mark.parametrize("scope", ["bar", "state", "regimes", "dated_status", "lifecycle"])
def test_actual_context_clock_cannot_follow_consumer(scope):
    row = market_row()
    if scope in {"bar", "state"}:
        row[scope + "_known"] = CUTOFF + timedelta(seconds=1)
    else:
        row[scope][0]["known"] = CUTOFF + timedelta(seconds=1)
    with pytest.raises(ValueError, match="CONTEXT_KNOWLEDGE_AFTER_CUTOFF"):
        repair.validate_context(row, CUTOFF)


def test_proof_cutoff_is_bound_to_consumer_and_original_context_stays_unchanged():
    row = market_row()
    before = deepcopy(row)
    repair.validate_context(row, CUTOFF)
    proof = dict(knowledge_cutoff=CUTOFF + timedelta(seconds=1), rule_known_at=CUTOFF)
    with pytest.raises(ValueError, match="WINDOW_AFTER_CONSUMER_CUTOFF"):
        repair.validate_context(row, CUTOFF, proof)
    assert row == before


def test_ordinary_module_has_no_original_job_or_worktree_dependency():
    text = Path(repair.__file__).read_text()
    assert "/srv/" not in text
    assert "verify-final.py" not in text
