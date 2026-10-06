"""Synthetic exact replay, bounded partitions and cumulative concepts."""

from copy import deepcopy
from datetime import timedelta
from itertools import permutations
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, partition
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_financial import TABLES, statement_batch
from quanthus.pipelines.securities_warehouse.base22_financial_audit import FinancialReplaySink
from quanthus.pipelines.securities_warehouse.base22_outputs import financial_concept_versions
from tests.unit.test_base22_financial import fixture


def sample():
    contract, _, _ = load_contract(Path("config/pipelines"))
    raw, source, resolver = fixture()
    rows, _ = statement_batch([(raw, source)], resolver=resolver, rules=contract["tables"])
    return contract["tables"], rows


def test_complete_group_matches_once_and_retains_only_compact_concepts():
    rules, rows = sample()
    seen = []
    sink = FinancialReplaySink(rules, lambda *args: seen.append(args), flush_rows=1)
    sink.add(rows)
    sink.finish()
    assert {t: sum(len(x[3]) for x in seen if x[0] == t) for t in TABLES} == {
        t: len(rows[t]) for t in TABLES
    }
    assert all(not rs for rs in sink.buffers.values())
    assert all(part == partition(rs[0], rules[t]) for t, part, _, rs in seen)


def test_concepts_are_globally_cumulative_across_flushes():
    rules, rows = sample()
    second = deepcopy(rows)
    for rs in second.values():
        for row in rs:
            row["knowledge_from"] += timedelta(days=7)
            if "effective_from" in row:
                row["effective_from"] -= timedelta(days=400)
    expected = financial_concept_versions(rows[TABLES[1]] + second[TABLES[1]])
    seen = []
    sink = FinancialReplaySink(rules, lambda *args: seen.append(args), flush_rows=1)
    sink.add(rows)
    sink.add(second)
    sink.finish()
    assert next(x[3] for x in seen if x[0] == TABLES[1]) == expected


@pytest.mark.parametrize("size", [0, -1, True, 32769])
def test_flush_bound_is_not_an_unbounded_tuning_option(size):
    rules, _ = sample()
    with pytest.raises(BoundedError, match="FLUSH_BOUND"):
        FinancialReplaySink(rules, lambda *args: None, flush_rows=size)


def test_concept_semantic_conflict_cannot_be_hidden_by_repeated_snapshots():
    rules, rows = sample()
    sink = FinancialReplaySink(rules, lambda *args: None, flush_rows=1)
    sink.add(rows)
    altered = deepcopy(rows)
    altered[TABLES[1]][0]["concept_name"] = "conflicting concept"
    with pytest.raises(BoundedError, match="CONCEPT_SOURCE_SEMANTICS_CONFLICT"):
        sink.add(altered)


def test_consumer_receives_exact_tokens_and_instants_not_just_row_counts():
    rules, rows = sample()
    expected = deepcopy(rows)

    def compare(table, part, number, actual):
        assert actual == expected[table]

    sink = FinancialReplaySink(rules, compare, flush_rows=1)
    altered = deepcopy(rows)
    altered[TABLES[2]][0]["value_decimal"] += 1
    with pytest.raises(AssertionError):
        sink.add(altered)


def test_unexpected_table_and_excessive_group_fail_closed():
    rules, rows = sample()
    sink = FinancialReplaySink(rules, lambda *args: None)
    with pytest.raises(BoundedError, match="TABLE_SET"):
        sink.add({**rows, "unknown_table": []})
    with pytest.raises(BoundedError, match="GROUP_ROW_BOUND"):
        sink.add({**rows, TABLES[2]: rows[TABLES[2]] * 32769})


def test_repeated_nonextending_concept_clocks_do_not_accumulate():
    rules, rows = sample()
    sink = FinancialReplaySink(rules, lambda *args: None, flush_rows=1)
    for n in range(8):
        later = deepcopy(rows[TABLES[1]])
        for row in later:
            row["knowledge_from"] += timedelta(days=n)
        sink.add({TABLES[0]: [], TABLES[1]: later, TABLES[2]: []})
        assert len(sink.concepts) == len(rows[TABLES[1]])


@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_compact_prefix_matches_whole_merge_under_interleaved_source_clocks(order):
    rules, rows = sample()
    source = []
    for clock, offset in [(0, 0), (1, 20), (2, -30), (1, -10)]:
        row = deepcopy(rows[TABLES[1]][0])
        row["knowledge_from"] += timedelta(days=clock)
        row["effective_from"] += timedelta(days=offset)
        source.append(row)
    seen = []
    sink = FinancialReplaySink(rules, lambda *args: seen.append(args), flush_rows=1)
    for n in order:
        sink.add({TABLES[0]: [], TABLES[1]: [source[n]], TABLES[2]: []})
    sink.finish()
    assert seen[0][3] == financial_concept_versions(source)


@pytest.mark.parametrize("defect", [None, "missing_company", "missing_concept", "duplicate"])
def test_financial_global_reads_separate_readonly_image_and_keeps_closure(
    tmp_path, monkeypatch, defect
):
    import os
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import file_sha
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base22_financial_audit import (
        _scan_financial_global,
    )
    from quanthus.pipelines.securities_warehouse.financial_reporting import (
        financial_reporting_schemas,
    )

    contract, _, _ = load_contract(Path("config/pipelines"))
    _, rows = sample()
    if defect == "missing_concept":
        rows[TABLES[2]][0]["financial_concept_id"] = "absent-concept"
    if defect == "duplicate":
        rows[TABLES[2]].append(deepcopy(rows[TABLES[2]][0]))
    root = tmp_path / "relocated-readonly-image"
    root.mkdir()
    ss = financial_reporting_schemas()
    outputs = {}
    for table in TABLES:
        path = root / (table + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows[table], schema=ss[table]), path)
        path.chmod(0o444)
        outputs[table] = {"path": path.name, "table": table}
    company = root / "company.parquet"
    pq.write_table(
        pa.table(
            {
                "company_id": ["absent" if defect == "missing_company" else "company-1"],
                "effective_from": [datetime(1991, 1, 1, tzinfo=timezone.utc)],
                "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
                "knowledge_from": [datetime(1991, 1, 1, tzinfo=timezone.utc)],
                "knowledge_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
            }
        ),
        company,
    )
    company.chmod(0o444)
    before = {p.name: file_sha(p) for p in root.iterdir()}
    real_statvfs = os.statvfs
    # Only a synthetic RO flag here; the real harness uses a kernel RO bind.
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda p: SimpleNamespace(f_flag=os.ST_RDONLY)
        if Path(p) == root
        else real_statvfs(p),
    )
    admitted = {
        (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
        for rs in rows.values()
        for r in rs
    }
    with Budget(tmp_path / "audit-TMP", [root], Limits(free_floor_bytes=1)) as budget:
        if defect is None:
            _scan_financial_global(
                root, outputs, {"dwd_listed_company": [company]}, budget, contract, ss, admitted
            )
        else:
            with pytest.raises(BoundedError, match="CLOSURE_FAILED|DUPLICATE"):
                _scan_financial_global(
                    root, outputs, {"dwd_listed_company": [company]}, budget, contract, ss, admitted
                )
    assert before == {p.name: file_sha(p) for p in root.iterdir()}
