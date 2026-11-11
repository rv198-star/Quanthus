from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from quanthus.pipelines.hist_1d_full import build, factor_union, merge_reference


@pytest.fixture
def connection():
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    c.execute("SET TimeZone='Asia/Shanghai'")
    yield c
    c.close()


def _factors(c):
    for prefix in ("h", "b"):
        c.execute(f"""CREATE TABLE {prefix}_dwd_adjustment_factor(
        security_id VARCHAR,effective_from TIMESTAMPTZ,effective_to TIMESTAMPTZ,
        adjustment_method VARCHAR,adjustment_direction VARCHAR,
        factor_value DECIMAL(38,18),source_record_id VARCHAR)""")
    c.execute("""INSERT INTO h_dwd_adjustment_factor VALUES
      ('s','2019-01-01 00:00:00+08',NULL,'PROVIDER_ADJUST_FACTOR','PROVIDER_NATIVE',1,'hist')""")
    c.execute("""INSERT INTO b_dwd_adjustment_factor VALUES
      ('s','2019-01-01 00:00:00+08','2021-01-01 00:00:00+08',
       'PROVIDER_ADJUST_FACTOR','PROVIDER_NATIVE',1,'old'),
      ('s','2021-01-01 00:00:00+08',NULL,'PROVIDER_ADJUST_FACTOR','PROVIDER_NATIVE',2,'modern'),
      ('s','2021-01-01 00:00:00+08',NULL,'FORWARD_ADJUSTMENT','FORWARD',3,'other-basis')""")


def test_factor_seam_uses_next_source_event_and_preserves_source_value(connection):
    _factors(connection)
    records = connection.execute(factor_union(connection) + " ORDER BY effective_from").fetchall()
    assert len(records) == 2
    assert records[0][2] == records[1][1]
    assert records[1][2] is None
    assert [str(r[5]) for r in records] == ["1.000000000000000000", "2.000000000000000000"]
    assert [r[6] for r in records] == ["hist", "modern"]


def test_factor_value_conflict_rejects_source_selection(connection):
    _factors(connection)
    connection.execute(
        "UPDATE b_dwd_adjustment_factor SET factor_value=9 WHERE source_record_id='old'"
    )
    with pytest.raises(ValueError, match="NATIVE_FACTOR_PARENT_VALUE_CONFLICT"):
        factor_union(connection)


def _status(c):
    for prefix in ("h", "b"):
        c.execute(f"""CREATE TABLE {prefix}_status(status_id VARCHAR,
        knowledge_from TIMESTAMPTZ,effective_from TIMESTAMPTZ,effective_to TIMESTAMPTZ,
        status_value VARCHAR)""")
    c.execute("""INSERT INTO h_status VALUES
      ('inherited','2026-01-01 00:00:00Z','2010-01-01 00:00:00Z','2020-01-01 00:00:00Z','NORMAL'),
      ('supplement','2026-11-11 00:00:00Z','2005-01-01 00:00:00Z','2006-01-01 00:00:00Z','ST')""")
    c.execute("""INSERT INTO b_status VALUES
      ('inherited','2026-01-01 00:00:00Z','2010-01-01 00:00:00Z',NULL,'NORMAL')""")


def test_reference_merge_keeps_full_parent_interval_and_historical_supplement(connection):
    _status(connection)
    records = connection.execute(merge_reference(connection, "status", "status_id")).fetchall()
    assert len(records) == 2
    assert next(r for r in records if r[0] == "inherited")[3] is None
    assert next(r for r in records if r[0] == "supplement")[4] == "ST"


def test_conflicting_reference_values_fail_instead_of_picking_parent(connection):
    _status(connection)
    connection.execute("UPDATE b_status SET status_value='ST'")
    with pytest.raises(ValueError, match="REFERENCE_PARENT_CONFLICT"):
        merge_reference(connection, "status", "status_id")


def test_unknown_source_interval_may_extend_hist_narrowed_regime(connection):
    _status(connection)
    connection.execute("UPDATE h_status SET status_value='UNKNOWN' WHERE status_id='inherited'")
    connection.execute("UPDATE b_status SET status_value='UNKNOWN',effective_from='1999-01-01'")
    for prefix in ("h", "b"):
        connection.execute(
            f"CREATE VIEW {prefix}_dwd_security_status_interval AS SELECT * FROM {prefix}_status"
        )
    result = connection.execute(
        merge_reference(connection, "dwd_security_status_interval", "status_id")
    ).fetchall()
    inherited = next(row for row in result if row[0] == "inherited")
    assert inherited[2].year == 1999
    assert inherited[4] == "UNKNOWN"


def test_known_status_cannot_gain_an_earlier_unqualified_interval(connection):
    _status(connection)
    connection.execute("UPDATE b_status SET effective_from='1999-01-01'")
    with pytest.raises(ValueError, match="REFERENCE_PARENT_CONFLICT"):
        merge_reference(connection, "status", "status_id")


@pytest.mark.parametrize("relative", ["hist/output", "base/output", "hist", "."])
def test_output_may_not_modify_parent_or_ancestor(tmp_path, relative):
    hist, base = tmp_path / "hist", tmp_path / "base"
    hist.mkdir()
    base.mkdir()
    with pytest.raises(ValueError, match="FULL_DAILY_OUTPUT_NOT_DISJOINT_OR_EXISTS"):
        build(hist, base, tmp_path / relative)


def test_reader_selects_modern_partitions_and_excludes_reference_tables():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("hist1d_reader", root / "scripts/read_hist_1d.py")
    assert spec and spec.loader
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    path = (
        "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/"
        "observation_start_month=10/part.parquet"
    )
    assert reader.choose_partition(["payload/dwd_listing/part.parquet", path], 2026, 10) == path
    with pytest.raises(ValueError, match="DAILY_PARTITION_NOT_FOUND"):
        reader.choose_partition([path], 2020)
