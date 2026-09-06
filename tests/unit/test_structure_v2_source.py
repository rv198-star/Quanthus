from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import duckdb
import pytest

from quanthus.structure_v2_inputs import build_readiness
from quanthus.structure_v2_run import extract_admissions

from .test_structure_facts_run import _frames, _Reader
from .test_structure_v2_inputs import SHA, provider
from .test_structure_v2_run import fixture


def source_session(tmp_path, *, omit_frame=False):
    request, _, _, _ = fixture()
    frames = _frames()
    tables = tmp_path / "tables"
    tables.mkdir()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("""CREATE TABLE bars (
            security_id VARCHAR, date DATE, dwd_security_id VARCHAR, dwd_listing_id VARCHAR,
            identity_exchange VARCHAR, dwd_high_decimal DECIMAL(18,6),
            dwd_low_decimal DECIMAL(18,6), dwd_close_decimal DECIMAL(18,6),
            dwd_bar_source_record_id VARCHAR, dwd_bar_source_revision VARCHAR,
            dwd_bar_source_artifact_sha256 VARCHAR, dwd_bar_knowledge_from VARCHAR)
        """)
        for frame in frames:
            bar = frame.decision_view.bar("600001")
            connection.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
                "600001", bar.market_date, "dwd-security", "dwd-listing", "SSE",
                bar.high_price, bar.low_price, bar.close_price, f"bar-{bar.market_date}",
                "source-r1", SHA, "2026-07-01T00:00:00Z",
            ])
        connection.execute("COPY bars TO ? (FORMAT PARQUET)",
                           [str(tables / "daily_ohlcv_none.parquet")])
        connection.execute("""CREATE TABLE actions (
            security_code VARCHAR, effective_date DATE, action_id VARCHAR, known_at VARCHAR)
        """)
        connection.execute(
            "INSERT INTO actions VALUES ('600001', '2020-01-15', 'split-1', '2020-01-10')",
        )
        connection.execute("COPY actions TO ? (FORMAT PARQUET)",
                           [str(tables / "corporate_actions_normalized.parquet")])
        connection.execute(
            "CREATE TABLE calendar (trade_date DATE, is_open INTEGER, exchange VARCHAR)",
        )
        connection.executemany("INSERT INTO calendar VALUES (?,1,'SSE')",
                               [(frame.snapshot.market_date,) for frame in frames])
        connection.execute("COPY calendar TO ? (FORMAT PARQUET)",
                           [str(tables / "trading_calendar.parquet")])
    finally:
        connection.close()
    reader = _Reader(frames[:-1] if omit_frame else frames)
    reader.bundle = SimpleNamespace(root=tmp_path)
    return SimpleNamespace(reader=reader, identity=provider()), request


def test_adapter_uses_exact_source_members_calendar_and_effective_action_epoch(tmp_path):
    session, request = source_session(tmp_path)
    admissions, frames, policy = extract_admissions(session, request)
    assert len(admissions) == len(frames) == 23
    assert all(item.admitted for item in admissions)
    reset = [item for item in admissions if item.row.reset_reason]
    assert len(reset) == 1 and reset[0].row.market_date == date(2020, 1, 15)
    assert admissions[0].row.listing_id == "dwd-listing"
    assert admissions[0].row.source_revision == "source-r1"
    assert admissions[0].row.coordinate_ref != admissions[-1].row.coordinate_ref
    receipt = build_readiness(admissions, policy, session.identity,
                             report_start=date(2020, 1, 2), report_end=date(2020, 1, 24))
    assert receipt["attempted"] == 23 and not receipt["per_decision_publication_proven"]


def test_missing_entire_calendar_session_cannot_hide_in_inferred_denominator(tmp_path):
    session, request = source_session(tmp_path, omit_frame=True)
    with pytest.raises(ValueError, match="calendar"):
        extract_admissions(session, request)
