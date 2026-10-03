"""Synthetic tests of the bounded input census, without market payloads."""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from quanthus.stage_learning import p5_input_census as census

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p5-stage-001/input-census.v1.json"


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    request = json.loads(CONFIG.read_text())
    entries = []
    for table in census.TABLES:
        entry = next(
            e for e in request["files"] if e["table"] == table and "year=2020/" in e["path"]
        )
        entries.append(copy.deepcopy(entry))
    root = tmp_path / "base"
    root.mkdir()
    con = duckdb.connect()
    at = datetime(2020, 1, 2, 7, tzinfo=timezone.utc)
    before = datetime(2019, 1, 1, tzinfo=timezone.utc)
    future = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for entry in entries:
        table = entry["table"]
        schema = census.SCHEMAS[table]
        con.execute(
            f"CREATE TABLE {table} (" + ",".join(f'"{k}" {v}' for k, v in schema.items()) + ")"
        )
        common = {
            "observation_start": at.replace(hour=1, minute=30),
            "observation_end": at,
            "knowledge_from": before,
            "record_source": "synthetic",
            "source_record_id": "source-1",
            "source_revision": "v1",
            "source_artifact_sha256": "sha256:" + "a" * 64,
            "calendar_session_id": "cal-1",
        }
        if table == census.TABLES[2]:
            rows = [
                {
                    **common,
                    "venue_id": census.VENUES[0],
                    "trade_date": at.date(),
                    "session_type": "REGULAR_DAY",
                    "is_open": True,
                    "open_at": common["observation_start"],
                    "close_at": at,
                }
            ]
        elif table == census.TABLES[0]:
            base = {**common, "interval_size": "1d", "price_basis": "UNADJUSTED", "currency": "CNY"}
            rows = [
                {**base, "listing_id": x} for x in ("matched", "bar-only", "duplicate", "duplicate")
            ]
            rows += [
                {**base, "listing_id": "orphan", "calendar_session_id": "missing"},
                {**base, "listing_id": "future", "knowledge_from": future},
            ]
        else:
            base = {
                **common,
                "is_tradable": True,
                "can_buy": True,
                "can_sell": True,
                "reason_code": "OK",
                "input_revision_digest": "sha256:" + "b" * 64,
            }
            rows = [
                {**base, "listing_id": x} for x in ("matched", "state-only", "duplicate", "future")
            ]
        for row in rows:
            con.execute(
                f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in schema) + ")",
                [row.get(key) for key in schema],
            )
        path = root / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY {table} TO ? (FORMAT PARQUET)", [str(path)])
        entry["size_bytes"] = path.stat().st_size
        entry["sha256"] = census.file_digest(path)
    con.close()
    entries.sort(key=lambda e: e["path"])
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps({"files": [{k: v for k, v in e.items() if k != "table"} for e in entries]})
    )
    request["base_manifest_sha256"] = census.file_digest(manifest)
    (root / "READY").write_text(request["base_manifest_sha256"] + "\n")
    request["files"] = entries
    request["selection_digest"] = census.digest(entries)
    return root, request


def test_frozen_exact_selection_is_scoped_and_complete() -> None:
    request = json.loads(CONFIG.read_text())
    census.validate_request(request)
    assert len(request["files"]) == 250
    assert sum(e["size_bytes"] for e in request["files"]) == 824617539


def test_census_keeps_missing_duplicate_future_and_unknown_capacity(tmp_path: Path) -> None:
    root, request = _fixture(tmp_path)
    paths = census.verify_inputs(root, request)
    with duckdb.connect() as connection:
        result = census.census_tables(connection, paths)
    joins = result["joins"]
    assert joins["listing_session_union_slots"] == 6
    assert joins["matched_slots"] == 3
    assert joins["bar_only_slots"] == 2
    assert joins["state_only_slots"] == 1
    assert joins["orphan_calendar_slots"] == 1
    assert joins["unique_date_aligned_supply_slots"] == 2
    bars = result["table_counts"][census.TABLES[0]]
    assert bars["duplicate_row_key_excess"] == 1
    assert bars["warehouse_version_after_session_close_rows"] == 1
    assert result["active_population_count"] is None
    assert result["qualified_120_session_windows"] is None
    assert result["b_acceptance"] is False
    assert [s["disposition"] for s in result["stages"]] == ["built_once"] * 3 + ["reused_TMP"]


@pytest.mark.parametrize("mutation", ["year", "minute", "table", "traversal", "duplicate"])
def test_invalid_selection_fails_before_payload_read(tmp_path: Path, mutation: str) -> None:
    _, request = _fixture(tmp_path)
    entry = request["files"][0]
    if mutation == "year":
        entry["path"] = entry["path"].replace("year=2020", "year=2022")
    elif mutation == "minute":
        entry["path"] = entry["path"].replace("interval_size=1d", "interval_size=1m")
    elif mutation == "table":
        entry["table"] = "dwd_listing"
    elif mutation == "traversal":
        entry["path"] = "../" + entry["path"]
    else:
        request["files"].insert(0, dict(entry))
    request["selection_digest"] = census.digest(request["files"])
    with pytest.raises(census.CensusError):
        census.verify_inputs(tmp_path / "nonexistent", request)


def test_changed_payload_and_symlink_are_not_reused(tmp_path: Path) -> None:
    root, request = _fixture(tmp_path)
    path = root / request["files"][0]["path"]
    original = path.read_bytes()
    path.write_bytes(b"bad")
    with pytest.raises(census.CensusError, match="checksum/size"):
        census.verify_inputs(root, request)
    path.unlink()
    other = tmp_path / "payload"
    other.write_bytes(original)
    path.symlink_to(other)
    with pytest.raises(census.CensusError, match="symlink"):
        census.verify_inputs(root, request)


def test_schema_drift_cannot_be_hidden_by_other_members(tmp_path: Path) -> None:
    root, request = _fixture(tmp_path)
    paths = census.verify_inputs(root, request)
    bad = tmp_path / "bad.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "COPY (SELECT CAST(NULL AS TIMESTAMP) knowledge_from) TO ? (FORMAT PARQUET)", [str(bad)]
        )
        paths[census.TABLES[0]].append(bad)
        with pytest.raises(census.CensusError, match="schema"):
            census.census_tables(connection, paths)


def test_run_seals_receipt_and_removes_tmp_without_claiming_population(tmp_path: Path) -> None:
    root, request = _fixture(tmp_path)
    config = tmp_path / "request.json"
    config.write_text(json.dumps(request))
    output = tmp_path / "output"
    result = census.run_census(
        root=root, request_path=config, output=output, code_revision="a" * 40
    )
    assert result["temporary_intermediates_removed"]
    assert set(p.name for p in output.iterdir()) == {"manifest.json", "summary.json", "READY"}
    assert (output / "READY").read_text().strip() == census.file_digest(output / "manifest.json")
    with pytest.raises(census.CensusError, match="already exists"):
        census.run_census(root=root, request_path=config, output=output, code_revision="a" * 40)


def test_half_open_knowledge_interval_and_shanghai_day(tmp_path: Path) -> None:
    root, request = _fixture(tmp_path)
    paths = census.verify_inputs(root, request)
    modified = tmp_path / "versions.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE versions AS SELECT * FROM read_parquet(?)",
            [str(paths[census.TABLES[0]][0])],
        )
        connection.execute("""
          UPDATE versions SET observation_start=TIMESTAMPTZ '2020-01-01 16:30:00+00'
        """)
        connection.execute("""
          UPDATE versions SET knowledge_from=TIMESTAMPTZ '2019-01-01 00:00:00+00',
            knowledge_to=TIMESTAMPTZ '2020-01-02 07:00:00+00' WHERE listing_id='future'
        """)
        connection.execute("""
          INSERT INTO versions SELECT * REPLACE (
            TIMESTAMPTZ '2020-01-02 07:00:00+00' AS knowledge_from,
            CAST(NULL AS TIMESTAMPTZ) AS knowledge_to
          ) FROM versions WHERE listing_id='future'
        """)
        connection.execute("COPY versions TO ? (FORMAT PARQUET)", [str(modified)])
        paths[census.TABLES[0]] = [modified]
        connection.execute("SET TimeZone='America/New_York'")
        result = census.census_tables(connection, paths)
    bars = result["table_counts"][census.TABLES[0]]
    assert bars["multiversion_business_slots"] == 1
    assert bars["warehouse_version_interval_covers_session_close_rows"] == 5
    assert bars["session_date_mismatch_slots"] == 0
    assert result["joins"]["unique_date_aligned_supply_slots"] == 1


def test_calendar_cannot_smuggle_protected_trade_date(tmp_path: Path) -> None:
    root, request = _fixture(tmp_path)
    paths = census.verify_inputs(root, request)
    modified = tmp_path / "protected.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            """CREATE TABLE modified AS SELECT * REPLACE
                           (DATE '2025-01-02' AS trade_date) FROM read_parquet(?)""",
            [str(paths[census.TABLES[2]][0])],
        )
        connection.execute("COPY modified TO ? (FORMAT PARQUET)", [str(modified)])
        paths[census.TABLES[2]] = [modified]
        with pytest.raises(census.CensusError, match="calendar trade date"):
            census.census_tables(connection, paths)


def test_empty_source_counts_are_zero_and_capacity_remains_unknown(tmp_path: Path) -> None:
    root, request = _fixture(tmp_path)
    paths = census.verify_inputs(root, request)
    modified = tmp_path / "empty.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE modified AS SELECT * FROM read_parquet(?) WHERE false",
            [str(paths[census.TABLES[0]][0])],
        )
        connection.execute("COPY modified TO ? (FORMAT PARQUET)", [str(modified)])
        paths[census.TABLES[0]] = [modified]
        result = census.census_tables(connection, paths)
    assert result["table_counts"][census.TABLES[0]]["null_session_rows"] == 0
    assert result["table_counts"][census.TABLES[1]]["invalid_knowledge_interval_rows"] == 0
    assert result["table_counts"][census.TABLES[0]]["earliest_knowledge_from"] is None
    assert result["joins"]["matched_slots"] == 0
    assert result["active_population_count"] is None


@pytest.mark.parametrize("expired", [True, False])
def test_resource_budget_prevents_sealing(tmp_path: Path, expired: bool) -> None:
    with (
        duckdb.connect() as connection,
        pytest.raises(census.CensusError, match="budget exceeded"),
        census.bounded_tmp(
            connection, tmp_path, deadline=time.monotonic() + (-1 if expired else 60), limit=2
        ),
    ):
        (tmp_path / "database").write_bytes(b"four")
