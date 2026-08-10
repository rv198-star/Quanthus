from __future__ import annotations

import pytest

from quanthus.pipelines.dwd_execution_resources import (
    DWD_THREAD_LIMIT_ENV,
    DwdExecutionResourceError,
    configure_duckdb_threads,
    dwd_duckdb_memory_limit_mb,
    dwd_thread_limit,
)


class _Connection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str) -> None:
        self.queries.append(query)


def test_thread_limit_defaults_to_one_and_accepts_governed_four() -> None:
    assert dwd_thread_limit({}) == 1
    assert dwd_thread_limit({DWD_THREAD_LIMIT_ENV: "4"}) == 4


@pytest.mark.parametrize("value", ["0", "5", "04", "2.0", "four", "1; SELECT 1"])
def test_thread_limit_rejects_unbounded_or_noncanonical_values(value: str) -> None:
    with pytest.raises(DwdExecutionResourceError, match=DWD_THREAD_LIMIT_ENV):
        dwd_thread_limit({DWD_THREAD_LIMIT_ENV: value})


def test_duckdb_connection_receives_the_exact_governed_limit() -> None:
    connection = _Connection()

    result = configure_duckdb_threads(
        connection,
        {DWD_THREAD_LIMIT_ENV: "4"},
    )

    assert result == 4
    assert connection.queries == ["SET threads=4"]


def test_duckdb_connection_honors_a_stage_specific_maximum() -> None:
    connection = _Connection()

    result = configure_duckdb_threads(
        connection,
        {DWD_THREAD_LIMIT_ENV: "4"},
        maximum=2,
    )

    assert result == 2
    assert connection.queries == ["SET threads=2"]

    for invalid in (0, 5, True):
        with pytest.raises(DwdExecutionResourceError, match="thread maximum"):
            configure_duckdb_threads(connection, maximum=invalid)


def test_duckdb_memory_scales_with_the_governed_worker_count() -> None:
    assert dwd_duckdb_memory_limit_mb(1536, {}) == 1536
    assert dwd_duckdb_memory_limit_mb(1536, {DWD_THREAD_LIMIT_ENV: "4"}) == 6144
    assert dwd_duckdb_memory_limit_mb(1792, {DWD_THREAD_LIMIT_ENV: "4"}) == 7168
    assert (
        dwd_duckdb_memory_limit_mb(
            1792,
            {DWD_THREAD_LIMIT_ENV: "2"},
            minimum_mb=7168,
        )
        == 7168
    )

    for invalid in (0, 2049, True):
        with pytest.raises(DwdExecutionResourceError, match="per-thread memory"):
            dwd_duckdb_memory_limit_mb(invalid, {DWD_THREAD_LIMIT_ENV: "4"})

    for invalid in (0, 8193, True):
        with pytest.raises(DwdExecutionResourceError, match="minimum memory"):
            dwd_duckdb_memory_limit_mb(1792, minimum_mb=invalid)
