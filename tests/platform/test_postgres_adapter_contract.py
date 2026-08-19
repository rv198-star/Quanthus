from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from quanthus.platform.persistence import PostgresPlatformStore


class FakeCursor:
    def __init__(self) -> None:
        self.ledger: set[tuple[str, str]] = set()
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []
        self._row: dict[str, int] | None = None
        self.rowcount = 0

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, params: Iterable[Any] | None = None) -> None:
        normalized = " ".join(str(statement).split())
        values = tuple(params) if params is not None else None
        self.executions.append((normalized, values))
        self.rowcount = 0
        self._row = None
        if normalized == "SELECT 1 AS ready":
            self._row = {"ready": 1}
        elif normalized.startswith("SELECT 1 FROM platform_schema_migrations"):
            self._row = {"present": 1} if values in self.ledger else None
        elif normalized.startswith("INSERT INTO platform_schema_migrations"):
            assert values is not None
            self.ledger.add((str(values[0]), str(values[1])))
            self.rowcount = 1

    def fetchone(self) -> dict[str, int] | None:
        return self._row


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> None:  # type: ignore[no-untyped-def]
        self.committed = exc_type is None

    def cursor(self) -> FakeCursor:
        return self._cursor


def test_postgres_adapter_migration_contract_is_ordered_locked_and_idempotent() -> None:
    cursor = FakeCursor()
    connections: list[FakeConnection] = []

    def connect(_dsn: str) -> FakeConnection:
        connection = FakeConnection(cursor)
        connections.append(connection)
        return connection

    store = PostgresPlatformStore("postgresql://acceptance", connect=connect)
    assert store.migrate(("identity", "jobs")) == (
        "identity-0001",
        "jobs-0001",
        "jobs-0002-lean-worker",
        "jobs-0003-candidate-runs",
    )
    assert store.migrate(("identity", "jobs")) == ()
    assert store.ready() is True

    statements = [statement for statement, _ in cursor.executions]
    assert statements[0].startswith("CREATE TABLE IF NOT EXISTS platform_schema_migrations")
    assert statements[1] == (
        "SELECT pg_advisory_xact_lock(hashtext('quanthus-platform-migrations'))"
    )
    assert any(statement.startswith("CREATE TABLE platform_users") for statement in statements)
    assert any(statement.startswith("CREATE TABLE platform_jobs") for statement in statements)
    assert cursor.ledger == {
            ("identity", "identity-0001"),
            ("jobs", "jobs-0001"),
            ("jobs", "jobs-0002-lean-worker"),
            ("jobs", "jobs-0003-candidate-runs"),
        }
    assert all(connection.committed for connection in connections)
