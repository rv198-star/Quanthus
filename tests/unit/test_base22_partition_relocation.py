from datetime import datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
)
from quanthus.pipelines.securities_warehouse.base22_partition_relocation import (
    plan_relocation,
    relocate_member,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def fixture(tmp_path, *, wrong=True, rows=2):
    schema = pa.schema(
        [
            pa.field("effective_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("knowledge_to", pa.timestamp("us", tz="UTC")),
            pa.field("value", pa.decimal128(18, 11)),
        ]
    )
    clocks = [
        datetime(2020, 12, 31, 17 if wrong else 7, tzinfo=timezone.utc),
        datetime(2020, 12, 1, 3, tzinfo=timezone.utc),
    ]
    data = pa.Table.from_pylist(
        [
            {
                "effective_at": clocks[i % 2],
                "knowledge_from": datetime(2026, 10, 6, tzinfo=timezone.utc),
                "knowledge_to": None,
                "value": [Decimal("0.12345678901"), None][i % 2],
            }
            for i in range(rows)
        ],
        schema=schema,
    )
    source = tmp_path / "source" / "input.parquet"
    source.parent.mkdir()
    pq.write_table(data, source)
    member = Member(source, file_sha(source), source.stat().st_size, rows)
    rule = {"partition_by": ["effective_at_year", "effective_at_month"]}
    return schema, member, rule, data


def test_shanghai_boundary_plan_and_exact_serialization(tmp_path):
    schema, member, rule, original = fixture(tmp_path)
    frozen_bytes = member.path.read_bytes()
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    assert plan["wrong_partition_rows"] == 1
    assert [p["partition"] for p in plan["routes"]] == [
        "effective_at_year=2020/effective_at_month=12",
        "effective_at_year=2021/effective_at_month=1",
    ]
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        result = relocate_member(
            member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=budget.root / "payload",
            budget=budget,
        )
        assert result["all_typed_values_exact"] and result["original_rows"] == 2
        assert sum(p["rows"] for p in result["files"]) == 2
        assert all((budget.root / p["path"]).stat().st_nlink == 1 for p in result["files"])
    assert member.path.read_bytes() == frozen_bytes
    assert original.column("value").to_pylist() == [Decimal("0.12345678901"), None]


@pytest.mark.parametrize(
    "table", ["dwd_disclosure", "dwd_disclosure_subject", "dwd_shareholding_change"]
)
def test_healthy_member_is_independent_exact_byte_copy(tmp_path, table):
    schema, member, rule, _ = fixture(tmp_path, wrong=False)
    plan = plan_relocation(
        member,
        table=table,
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        result = relocate_member(
            member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=budget.root / "payload",
            budget=budget,
        )
        dest = budget.root / result["files"][0]["path"]
        assert file_sha(dest) == member.sha256
        assert dest.stat().st_ino != member.path.stat().st_ino


@pytest.mark.parametrize("mutation", ["route", "count", "identity"])
def test_plan_changes_fail_before_output(tmp_path, mutation):
    schema, member, rule, _ = fixture(tmp_path)
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    if mutation == "route":
        plan["routes"][0]["partition"] = "effective_at_year=2019/effective_at_month=12"
    elif mutation == "count":
        plan["routes"][0]["rows"] += 1
    else:
        plan["identity"] = "sha256:" + "0" * 64
    before = member.path.read_bytes()
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        with pytest.raises(BoundedError, match="FROZEN_PLAN"):
            relocate_member(
                member,
                proof=plan,
                schema=schema,
                rule=rule,
                output=budget.root / "payload",
                budget=budget,
            )
        assert not (budget.root / "payload").exists()
    assert member.path.read_bytes() == before


def test_member_ceiling_and_changed_bytes_reject(tmp_path):
    schema, member, rule, _ = fixture(tmp_path)
    oversize = Member(member.path, member.sha256, member.size_bytes, 8193)
    with pytest.raises(BoundedError, match="ROW_CEILING"):
        plan_relocation(
            oversize, table="dwd_disclosure", schema=schema, rule=rule, original_partition="all"
        )
    member.path.write_bytes(member.path.read_bytes() + b"changed")
    with pytest.raises(BoundedError, match="FROZEN_MEMBER"):
        plan_relocation(
            member, table="dwd_disclosure", schema=schema, rule=rule, original_partition="all"
        )


def test_existing_output_is_not_overwritten(tmp_path):
    schema, member, rule, _ = fixture(tmp_path)
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    with Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget:
        args = dict(
            member=member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=budget.root / "payload",
            budget=budget,
        )
        first = relocate_member(**args)
        before = {p["path"]: file_sha(budget.root / p["path"]) for p in first["files"]}
        with pytest.raises(BoundedError, match="OUTPUT_EXISTS"):
            relocate_member(**args)
        assert {p: file_sha(budget.root / p) for p in before} == before


def test_source_symlink_and_output_escape_leave_input_unchanged(tmp_path):
    schema, member, rule, _ = fixture(tmp_path)
    before = member.path.read_bytes()
    linked = tmp_path / "linked.parquet"
    linked.symlink_to(member.path)
    with pytest.raises(SourceAssessmentError, match="SOURCE_SYMLINK"):
        plan_relocation(
            Member(linked, member.sha256, member.size_bytes, member.rows),
            table="dwd_disclosure",
            schema=schema,
            rule=rule,
            original_partition="all",
        )
    plan = plan_relocation(
        member,
        table="dwd_disclosure",
        schema=schema,
        rule=rule,
        original_partition="effective_at_year=2020/effective_at_month=12",
    )
    with (
        Budget(tmp_path / "job", [member.path.parent], Limits(free_floor_bytes=1)) as budget,
        pytest.raises(BoundedError),
    ):
        relocate_member(
            member,
            proof=plan,
            schema=schema,
            rule=rule,
            output=member.path.parent / "escape",
            budget=budget,
        )
    assert not (member.path.parent / "escape").exists()
    assert member.path.read_bytes() == before
