from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.stage_learning import p5_temporary_identity as module
from tests.unit.test_p5_input_census import _fixture as census_fixture

duckdb = pytest.importorskip("duckdb")


def test_session_run_writes_dated_census_receipt_and_removes_tmp(tmp_path, monkeypatch):
    dimensions = fixture_paths(tmp_path)
    census_base, request = census_fixture(tmp_path)
    config = tmp_path / "config/research/p5-stage-001/input-census.v1.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(request))
    monkeypatch.setattr(module, "verify_inputs", lambda *_: (dimensions, {}))
    output = tmp_path / "output"
    result = module.run(tmp_path, tmp_path, output, "a" * 40, census_base)
    assert result == json.loads((output / "receipt.json").read_text())
    bars = result["job_local_census"]["table_counts"]["dwd_security_bar"]
    assert bars["earliest_knowledge_from"] == "2019-01-01T00:00:00+00:00"
    assert result["session_identity"]["bar_slot_denominator"] == 6
    assert result["tmp_removed"] is True
    assert result["B_accepted"] is False
    assert list(output.iterdir()) == [output / "receipt.json"]


def test_receipt_serialization_does_not_silently_stringify_unknown_objects():
    with pytest.raises(TypeError, match="Unsupported receipt value"):
        module.receipt_json({"bad": object()})


def fixture_paths(tmp_path, *, extra_listing=None, extra_security=None):
    connection = duckdb.connect()
    paths = {}
    for table, schema in module.SCHEMAS.items():
        connection.execute(
            f"CREATE TABLE {table} ("
            + ",".join(f"{key} {kind}" for key, kind in schema.items())
            + ")"
        )
        row = {key: "synthetic" for key in schema}
        row.update(
            effective_from=datetime(2011, 12, 31, 16, tzinfo=timezone.utc),
            effective_to=None,
            knowledge_from=datetime(2026, 8, 3, tzinfo=timezone.utc),
            knowledge_to=None,
            security_id="security_1",
        )
        if table == "dwd_listing":
            row.update(listing_id="listing_1", venue_id=module.VENUES[0], listing_code="123456")
        else:
            row.update(company_id="company_1")
        rows = [row]
        extra = extra_listing if table == "dwd_listing" else extra_security
        for values in extra or []:
            rows.append({**row, **values})
        connection.executemany(
            f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in schema) + ")",
            [[r[key] for key in schema] for r in rows],
        )
        path = tmp_path / (table + ".parquet")
        connection.execute(f"COPY {table} TO ? (FORMAT PARQUET)", [str(path)])
        paths[table] = path
    connection.close()
    return paths


def inspect(paths):
    connection = duckdb.connect()
    try:
        return module.inspect_dimensions(connection, paths)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "change,reason",
    [
        ("", "unique_retrospective_identity"),
        (
            "UPDATE dwd_security_bar SET observation_start='2013-01-04 15:00:00+08:00'",
            "session_date_unqualified",
        ),
        (
            "UPDATE dwd_security_bar SET observation_start='2012-01-03 16:00:00+00:00'",
            "unique_retrospective_identity",
        ),
        ("UPDATE dwd_security_bar SET price_basis='ADJUSTED'", "bar_scope_unqualified"),
        ("UPDATE dwd_trading_calendar SET is_open=false", "calendar_unqualified"),
        ("UPDATE dwd_trading_calendar SET session_type='OTHER'", "calendar_unqualified"),
        ("UPDATE dwd_trading_calendar SET venue_id='not_admitted'", "calendar_unqualified"),
        ("UPDATE dwd_listing SET effective_to='2012-01-04 00:00:00+08:00'", "listing_missing"),
        ("UPDATE dwd_security SET effective_to='2012-01-04 00:00:00+08:00'", "security_missing"),
    ],
)
def test_session_scope_and_half_open_boundaries(tmp_path, change, reason):
    paths = fixture_paths(tmp_path)
    connection = duckdb.connect()
    try:
        module.inspect_dimensions(connection, paths)
        connection.execute(
            "CREATE TABLE dwd_trading_calendar AS SELECT 'day1'::VARCHAR calendar_session_id, "
            "?::VARCHAR venue_id, DATE '2012-01-04' trade_date, "
            "'REGULAR_DAY'::VARCHAR session_type, true is_open",
            [module.VENUES[0]],
        )
        connection.execute(
            "CREATE TABLE dwd_security_bar AS SELECT 'listing_1'::VARCHAR listing_id, "
            "'day1'::VARCHAR calendar_session_id, '1d'::VARCHAR interval_size, "
            "'UNADJUSTED'::VARCHAR price_basis, "
            "TIMESTAMPTZ '2012-01-04 15:00:00+08:00' observation_start"
        )
        if change:
            connection.execute(change)
        result = module.inspect_session_identity(connection)
        assert result["reason_counts"] == {reason: 1}
    finally:
        connection.close()


def test_shanghai_half_open_scope_and_no_qualification(tmp_path):
    result = inspect(
        fixture_paths(
            tmp_path,
            extra_listing=[
                {
                    "listing_id": "later",
                    "effective_from": datetime(2021, 12, 31, 16, tzinfo=timezone.utc),
                },
                {
                    "listing_id": "before",
                    "effective_from": datetime(2010, 1, 1, tzinfo=timezone.utc),
                    "effective_to": datetime(2011, 12, 31, 16, tzinfo=timezone.utc),
                },
                {"listing_id": "other_venue", "venue_id": "not_admitted"},
            ],
        )
    )
    assert result["historical_listing_rows"] == 1
    assert result["linked_historical_security_rows"] == 1
    assert result["listing_rows_without_any_overlapping_security_version"] == 0
    assert result["coordinate_window_qualification"] is None
    assert result["session_identity_coverage_qualified"] is None
    assert result["B_accepted"] is False
    assert result["corporate_action_payloads_read"] is False
    assert result["knowledge_provenance"]["dwd_listing"]["post_development_end_rows"] == 1


def test_multiple_knowledge_versions_are_preserved(tmp_path):
    result = inspect(
        fixture_paths(
            tmp_path,
            extra_security=[
                {
                    "knowledge_from": datetime(2026, 7, 1, tzinfo=timezone.utc),
                    "knowledge_to": datetime(2026, 8, 3, tzinfo=timezone.utc),
                },
            ],
        )
    )
    assert result["linked_historical_security_rows"] == 2
    assert result["ids_with_multiple_retained_versions"]["dwd_security"] == 1


@pytest.mark.parametrize(
    "values", [{"effective_from": None}, {"venue_id": None}, {"security_id": ""}]
)
def test_unknown_scope_does_not_silently_disappear(tmp_path, values):
    with pytest.raises(ValueError, match="UNKNOWN_DIMENSION"):
        inspect(fixture_paths(tmp_path, extra_listing=[values]))


def test_unmatched_identity_is_accounted_for(tmp_path):
    result = inspect(
        fixture_paths(tmp_path, extra_listing=[{"listing_id": "unbound", "security_id": "missing"}])
    )
    assert result["listing_rows_without_any_overlapping_security_version"] == 1


def test_proposal_is_frozen():
    root = Path(__file__).resolve().parents[2]
    assert (
        module.file_digest(root / "config/research/p5-stage-001/input-closure-proposal.v1.json")
        == module.PROPOSAL_SHA
    )


def test_session_identity_keeps_missing_and_ambiguous_denominators(tmp_path):
    paths = fixture_paths(
        tmp_path,
        extra_security=[
            {"knowledge_from": datetime(2026, 7, 1, tzinfo=timezone.utc)},
        ],
    )
    connection = duckdb.connect()
    try:
        module.inspect_dimensions(connection, paths)
        connection.execute(
            "CREATE TABLE dwd_trading_calendar("
            "calendar_session_id VARCHAR, venue_id VARCHAR, trade_date DATE, "
            "session_type VARCHAR DEFAULT 'REGULAR_DAY', is_open BOOLEAN DEFAULT true)"
        )
        connection.execute(
            "INSERT INTO dwd_trading_calendar(calendar_session_id,venue_id,trade_date) "
            "VALUES ('day1', ?, '2012-01-04')",
            [module.VENUES[0]],
        )
        connection.execute(
            "CREATE TABLE dwd_security_bar(listing_id VARCHAR, calendar_session_id VARCHAR, "
            "interval_size VARCHAR DEFAULT '1d', price_basis VARCHAR DEFAULT 'UNADJUSTED', "
            "observation_start TIMESTAMPTZ DEFAULT '2012-01-04 15:00:00+08:00')"
        )
        connection.execute(
            "INSERT INTO dwd_security_bar(listing_id,calendar_session_id) VALUES "
            "('listing_1','day1'), ('missing','day1'), ('listing_1','unknown')"
        )
        result = module.inspect_session_identity(connection)
        assert result["bar_slot_denominator"] == 3
        assert result["reason_counts"] == {
            "calendar_unqualified": 1,
            "listing_missing": 1,
            "security_ambiguous": 1,
        }
        assert result["qualified_as_known"] is False
    finally:
        connection.close()
