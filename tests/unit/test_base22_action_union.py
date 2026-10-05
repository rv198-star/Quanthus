from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_action_union as u
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    Limits,
    Member,
)
from quanthus.pipelines.securities_warehouse.disclosure_event import disclosure_event_schemas


def action(identifier, day=date(2026, 8, 3)):
    return {
        "corporate_action_id": identifier,
        "company_id": "company",
        "security_id": "security",
        "ex_date": day,
        "record_source": "synthetic",
        "source_record_id": identifier,
        "source_revision": "synthetic",
        "source_artifact_sha256": "sha256:" + "a" * 64,
        "knowledge_from": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "knowledge_to": None,
        "effective_at": datetime(2026, 8, 2, 16, tzinfo=timezone.utc),
        "announced_at": None,
        "action_type": "CASH_DIVIDEND",
        "action_status": "IMPLEMENTED",
        "record_date": None,
        "payment_date": None,
    }


def save(tmp_path, name, rows, schema):
    path = tmp_path / "inputs" / name
    path.parent.mkdir(exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return Member(path, file_sha(path), path.stat().st_size, len(rows))


@pytest.mark.parametrize(
    "case,reason",
    [
        ("absent", "UNCONTESTED_ABSENT_DATED_EVENT"),
        ("native", "NATIVE_DATED_EVENT_EXISTS"),
        ("native-other-id-status", "NATIVE_DATED_EVENT_EXISTS"),
        ("native-other-file", "NATIVE_DATED_EVENT_EXISTS"),
        ("other-source", "OTHER_QUALIFIED_PROVIDER_DATED_EVENT_EXISTS"),
        ("multiple-incoming", "MULTIPLE_THIS_PROVIDER_ENTITIES"),
        ("no-date", "EX_DATE_UNAVAILABLE"),
    ],
)
def test_whole_native_and_provider_date_blocks_replacement(tmp_path, case, reason):
    schema = disclosure_event_schemas()["dwd_corporate_action"]
    old = action("old", date(2026, 7, 30))
    new = action("new")
    competitor = action("other", date(2026, 8, 5))
    if case in {"native", "native-other-id-status"}:
        old["ex_date"] = new["ex_date"]
        if case == "native-other-id-status":
            old["action_status"] = "CANCELLED"
    if case == "other-source":
        competitor["ex_date"] = new["ex_date"]
    if case == "no-date":
        new["ex_date"] = None
    native = [save(tmp_path, "old.parquet", [old], schema)]
    if case == "native-other-file":
        native.append(save(tmp_path, "old2.parquet", [action("another")], schema))
    rs = [new, action("new2")] if case == "multiple-incoming" else [new]
    incoming = [save(tmp_path, "incoming.parquet", rs, schema)]
    other = [save(tmp_path, "other.parquet", [competitor], schema)]
    before = {m.path: m.path.read_bytes() for m in [*native, *incoming, *other]}
    with Budget(tmp_path / "job", [tmp_path / "inputs"], Limits(free_floor_bytes=1)) as budget:
        result = u.select_uncontested_actions(native, incoming, other, schema=schema, budget=budget)
        assert result["dispositions"] == {reason: len(rs)}
        assert result["selected_entity_ids"] == (["new"] if case == "absent" else [])
        assert not result["source_priority_granted"]
        assert result["native_rows_replaced"] == 0
        body = {k: v for k, v in result.items() if k != "identity"}
        assert result["identity"] == digest(body)
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize(
    "fault", ["duplicate", "schema", "hash", "rows", "future", "identity", "repeat", "bound"]
)
def test_invalid_frozen_inputs_fail_before_output(tmp_path, fault):
    schema = disclosure_event_schemas()["dwd_corporate_action"]
    native = [save(tmp_path, "native.parquet", [action("old", date(2026, 7, 30))], schema)]
    new = action("new")
    if fault == "future":
        new["ex_date"] = date(2026, 9, 1)
    if fault == "identity":
        new["company_id"] = ""
    incoming = [
        save(tmp_path, "incoming.parquet", [new, new] if fault == "duplicate" else [new], schema)
    ]
    if fault == "schema":
        incoming = [save(tmp_path, "wrong.parquet", [{"x": 1}], pa.schema([("x", pa.int64())]))]
    if fault == "hash":
        incoming = [Member(incoming[0].path, "sha256:" + "b" * 64, incoming[0].size_bytes, 1)]
    if fault in {"rows", "bound"}:
        incoming = [Member(incoming[0].path, incoming[0].sha256, incoming[0].size_bytes,
                           2 if fault == "rows" else u.MAX_PROVIDER_ROWS + 1)]
    if fault == "repeat":
        incoming = list(native)
    with Budget(tmp_path / "job", [tmp_path / "inputs"], Limits(free_floor_bytes=1)) as budget:
        with pytest.raises(BoundedError):
            u.select_uncontested_actions(native, incoming, [], schema=schema, budget=budget)
        assert not (budget.root / "payload").exists()


def test_selected_typed_stream_preserves_all_columns_and_true_clocks(tmp_path):
    schema = disclosure_event_schemas()["dwd_corporate_action"]
    rs = [action("old"), action("new")]
    member = save(tmp_path, "incoming.parquet", rs, schema)
    with Budget(tmp_path / "job", [tmp_path / "inputs"], Limits(free_floor_bytes=1)) as budget:
        selected = list(
            u.selected_rows([member], selected_ids={"new"}, schema=schema, budget=budget)
        )
        assert selected == [rs[1]]
    assert member.verify()
