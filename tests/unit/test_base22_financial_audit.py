"""Synthetic exact replay, bounded partitions and cumulative concepts."""

from copy import deepcopy
from datetime import timedelta
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
