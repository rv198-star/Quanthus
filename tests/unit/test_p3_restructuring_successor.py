"""Narrow composite feature publication retains frozen replay and exact healthy values."""

import copy
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.expert_strategy import p3_restructuring_preparation as p
from quanthus.expert_strategy import p3_restructuring_successor as s

R = Path(__file__).resolve().parents[2]


@pytest.fixture
def feature_fixture(tmp_path):
    original = tmp_path / "original.parquet"
    target = tmp_path / "patch.parquet"
    t = pa.table(
        {
            "security_id": ["a", "000792", "000792", "z"],
            "market_date": [
                date(2020, 3, 25),
                date(2020, 3, 25),
                date(2021, 8, 10),
                date(2021, 8, 10),
            ],
            "exact": pa.array([Decimal("1.23456789012")] * 4, type=pa.decimal128(24, 11)),
            "clock": pa.array(
                [datetime(2026, 10, 25, tzinfo=timezone.utc)] * 4, type=pa.timestamp("us", tz="UTC")
            ),
            "unavailable": pa.array([None] * 4, type=pa.timestamp("us", tz="UTC")),
            "exception": [True, True, True, True],
        }
    )
    pq.write_table(t, original)
    patch = t.slice(1, 2).set_column(5, t.schema.field(5), pa.array([False, False]))
    return original, target, t, patch


def test_complete_affected_patch_matches_full_materialization_without_copying_history(
    feature_fixture,
):
    original, target, before, patch = feature_fixture
    original_bytes = original.read_bytes()
    assert p.write_feature_patch(original, target, patch, ("000792",)) == 2
    assert pq.read_table(target).equals(patch, check_metadata=True)
    c = duckdb.connect()
    c.execute("CREATE TABLE feature_panel AS SELECT * FROM read_parquet(?)", [str(original)])
    c.execute("CREATE UNIQUE INDEX feature_key ON feature_panel(market_date,security_id)")
    assert p.install_feature_patch(c, original, target, ("000792",)) == 2
    actual = c.execute(
        "SELECT * FROM feature_panel ORDER BY security_id,market_date"
    ).fetch_arrow_table()
    expected = pa.concat_tables([before.slice(0, 1), patch, before.slice(3, 1)])
    assert actual.to_pylist() == sorted(
        expected.to_pylist(), key=lambda r: (r["security_id"], r["market_date"])
    )
    assert original.read_bytes() == original_bytes
    assert target.stat().st_nlink == 1
    assert actual["unavailable"].null_count == 4
    c.close()


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "wrong-id", "wrong-date", "schema", "duplicate"]
)
def test_invalid_complete_patch_is_refused_before_publication(feature_fixture, mutation):
    original, target, before, patch = feature_fixture
    if mutation == "missing":
        patch = patch.slice(0, 1)
    if mutation == "extra":
        patch = pa.concat_tables([patch, patch.slice(0, 1)])
    if mutation == "wrong-id":
        patch = patch.set_column(0, patch.schema.field(0), pa.array(["z", "z"]))
    if mutation == "wrong-date":
        patch = patch.set_column(
            1, patch.schema.field(1), pa.array([date(2020, 3, 24), date(2021, 8, 10)])
        )
    if mutation == "schema":
        patch = patch.drop(["clock"])
    if mutation == "duplicate":
        patch = pa.concat_tables([patch.slice(0, 1)] * 2)
    with pytest.raises(ValueError, match="COORDINATES_OR_SCHEMA"):
        p.write_feature_patch(original, target, patch, ("000792",))
    assert not target.exists()
    assert pq.read_table(original).equals(before)


def test_reencoded_bad_patch_refused_before_runtime_delete(feature_fixture):
    original, target, before, patch = feature_fixture
    pq.write_table(patch.slice(0, 1), target)
    c = duckdb.connect()
    c.execute("CREATE TABLE feature_panel AS SELECT * FROM read_parquet(?)", [str(original)])
    with pytest.raises(ValueError, match="COORDINATES_OR_SCHEMA"):
        p.install_feature_patch(c, original, target, ("000792",))
    assert c.execute("SELECT count(*) FROM feature_panel").fetchone()[0] == 4
    c.close()


def test_frozen_science_and_all_arm_consumers_unchanged():
    previous = s.predecessor.load_request(R)
    new = s.load_request(R)
    assert all(new[k] == previous[k] for k in s.PRESERVED)
    assert new["affected_security_ids"] == ["000792"]
    assert s.RestructuringRuntime._sell_schedule is s.parent.DatedShareRuntime._sell_schedule
    assert s.RestructuringRuntime.run_variant is s.parent.DatedShareRuntime.run_variant
    assert new["historical_PIT_claim"] is new["source_guards_waived"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("affected_security_ids", ["002606"]),
        ("historical_PIT_claim", True),
        ("source_guards_waived", True),
        ("production_authorized", True),
        ("maximum_additional_wall_seconds", 7201),
        ("scope", ["2013-01-01", "2021-12-30", "2021-12-31"]),
        ("approved_proposal_sha256", "sha256:" + "a" * 64),
    ],
)
def test_policy_or_authority_change_refused(tmp_path, monkeypatch, field, value):
    old = s.predecessor.load_request(R)
    request = copy.deepcopy(s.load_request(R))
    request[field] = value
    (tmp_path / s.REQUEST).parent.mkdir(parents=True)
    (tmp_path / s.REQUEST).write_text(json.dumps(request))
    monkeypatch.setattr(s.predecessor, "load_request", lambda _: old)
    monkeypatch.setattr(s.native, "_sha", lambda _: request["parent_request_sha256"])
    with pytest.raises(ValueError, match="RESTRUCTURING_REQUEST"):
        s.load_request(tmp_path)


def test_no_admission_cannot_read_source(tmp_path, monkeypatch):
    request = s.load_request(R)
    monkeypatch.setattr(s, "load_request", lambda _: request)
    monkeypatch.setattr(s.p10, "load_input", lambda *a: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="EXACT_ADMISSION"):
        s.RestructuringRuntime(
            tmp_path, tmp_path, tmp_path, {}, parent_source=tmp_path, prior_prepared=tmp_path
        )
