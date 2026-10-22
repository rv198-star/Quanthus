import duckdb
import pytest

from quanthus.pipelines.securities_warehouse.base24_repair_view import overlay


def test_explicit_replacement_keeps_original_view_and_unique_new_keys():
    c = duckdb.connect()
    c.execute("CREATE VIEW q AS SELECT * FROM (VALUES ('A',1),('B',2)) t(id,value)")
    c.execute("CREATE VIEW delta AS SELECT * FROM (VALUES ('A',3),('C',4)) t(id,value)")
    result = overlay(c, "q", "delta", ("id",))
    assert result == dict(patch_rows=2, replaced_keys=1, inserted_keys=1)
    assert c.execute("SELECT * FROM q ORDER BY id").fetchall() == [("A", 3), ("B", 2), ("C", 4)]
    assert c.execute("SELECT * FROM unrepaired_q ORDER BY id").fetchall() == [("A", 1), ("B", 2)]
    c.close()


def test_duplicate_successor_keys_fail_before_original_view_is_changed():
    c = duckdb.connect()
    c.execute("CREATE VIEW q AS SELECT 'A' AS id,1 AS value")
    c.execute("CREATE VIEW delta AS SELECT * FROM (VALUES ('A',2),('A',3)) t(id,value)")
    with pytest.raises(ValueError, match="REPAIR_DUPLICATE_PATCH_KEY"):
        overlay(c, "q", "delta", ("id",))
    assert c.execute("SELECT * FROM q").fetchall() == [("A", 1)]
    c.close()


def test_unrelated_ambiguous_identity_does_not_block_bounded_repair(tmp_path):
    from datetime import datetime, timezone

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base24_repair_view import select_references

    root = tmp_path / "source/native"
    root.mkdir(parents=True)
    known = datetime(2026, 1, 1, tzinfo=timezone.utc)
    common = dict(knowledge_from=known, knowledge_to=None, effective_from=known)
    listing = dict(common, listing_id="A", security_id="S", venue_id="V")
    unrelated = dict(common, listing_id="B", security_id="Z", venue_id="V")
    rows = {
        "dwd_listing": [listing, unrelated, dict(unrelated, knowledge_from=known.replace(day=2))],
        "dwd_security": [dict(common, security_id="S")],
        "dwd_trading_venue": [dict(common, venue_id="V")],
        "dwd_trading_calendar": [dict(common, calendar_session_id="C", venue_id="V")],
    }
    for name, values in rows.items():
        pq.write_table(pa.Table.from_pylist(values), root / (name + ".parquet"))
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE dwd_tradability_state AS SELECT 'A' AS listing_id,'C' AS calendar_session_id"
    )
    select_references(c, tmp_path, datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert c.execute("SELECT listing_id FROM dwd_listing").fetchall() == [("A",)]
    c.close()
