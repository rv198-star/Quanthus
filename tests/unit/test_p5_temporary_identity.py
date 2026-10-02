from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.stage_learning import p5_temporary_identity as module

duckdb = pytest.importorskip("duckdb")


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
