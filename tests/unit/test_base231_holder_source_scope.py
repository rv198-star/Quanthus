from copy import deepcopy
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_holder_source_scope import (
    QualifiedSourceMatrix,
    source_scope_views,
    write_source_view,
)
from tests.unit.test_base231_native_delta import compare, row


def scope(rows):
    return QualifiedSourceMatrix("sha256:" + "a" * 64, len(rows), 1)


def select(tmp_path, old, new, view="holder_additions"):
    return compare(
        tmp_path, old, new, configure=lambda scan: source_scope_views(scan, scope(new)), view=view
    )


def test_absent_legacy_member_stays_old_without_becoming_an_exit_or_new_matrix_member(tmp_path):
    a = row()
    legacy = dict(a, shareholder_source_name="历史合成股东", holder_rank=3)
    b = dict(a, shareholder_source_name="新合成股东", holder_rank=2)
    before = deepcopy([a, legacy])
    added, held = select(tmp_path, [a, legacy], [a, b])
    assert len(added) == 1 and added[0]["shareholder_source_name"] == "新合成股东" and held == 0
    assert [a, legacy] == before
    members, _ = select(tmp_path / "membership", [a, legacy], [a, b], "qualified_matrix_members")
    assert len(members) == 2 and legacy not in members


def test_true_numeric_difference_holds_the_whole_source_matrix(tmp_path):
    a = row()
    changed = dict(a, holding_quantity=a["holding_quantity"] + Decimal(1))
    b = dict(a, shareholder_source_name="新合成股东", holder_rank=2)
    added, held = select(tmp_path, [a], [changed, b])
    assert added == [] and held == 1


def test_source_row_census_mismatch_does_not_admit_rows(tmp_path):
    a = row()
    with pytest.raises(RuntimeError):
        compare(tmp_path, [a], [a], configure=lambda s: source_scope_views(s, scope([a, a])))


def test_unchanged_component_is_reused_even_with_different_source_metadata(tmp_path):
    a = row()
    b = dict(a, source_record_id="new-original-source")
    added, held = select(tmp_path, [a], [b])
    assert added == [] and held == 0


def test_native_stream_preserves_required_schema_decimal_and_time(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, GlobalScan, Limits
    from quanthus.pipelines.securities_warehouse.base231_domain_pack import schemas

    schema = schemas()["dwd_shareholding_observation"]
    value = row()
    with Budget(tmp_path / "job", [], Limits(free_floor_bytes=1)) as budget:
        source = budget.root / "input.parquet"
        pq.write_table(pa.Table.from_pylist([value], schema=schema), source)
        with GlobalScan(budget) as scan:
            scan.add("holder_additions", [source])
            output = budget.root / "output.parquet"
            result = write_source_view(scan, "holder_additions", output)
            actual = pq.ParquetFile(output).read()
            assert actual.schema.equals(schema, check_metadata=False)
            assert actual.to_pylist() == [value]
            assert result["rows"] == 1
            with pytest.raises(RuntimeError, match="ALREADY_EXISTS"):
                write_source_view(scan, "holder_additions", output)


def test_duplicate_component_does_not_qualify_a_source_matrix(tmp_path):
    a = row()
    with pytest.raises(RuntimeError, match="FRAME_OR_KEY_CHANGED"):
        select(tmp_path, [a], [a, a])


def test_frame_census_is_verified_separately_from_row_count(tmp_path):
    a = row()
    with pytest.raises(RuntimeError, match="FRAME_OR_KEY_CHANGED"):
        compare(
            tmp_path,
            [a],
            [a],
            configure=lambda s: source_scope_views(
                s, QualifiedSourceMatrix("sha256:" + "a" * 64, 1, 2)
            ),
        )
