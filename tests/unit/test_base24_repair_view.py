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
