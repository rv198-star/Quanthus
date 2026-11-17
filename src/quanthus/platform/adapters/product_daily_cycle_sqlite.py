"""SQLite UnitOfWork for the synthetic funding/account first-buy bridge."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.capabilities.accounting import AccountProjection, Position, Posting, apply_buy_delta
from quanthus.product_daily.application.funding_bridge import (
    BridgeState,
    BuyBridgeResult,
    CycleBaseline,
    CycleFillProfile,
    EntryBridgeResult,
    ExecutionReport,
    FundingBridgeService,
    SyntheticContext,
    funding_state_envelope,
    restore_funding_envelope,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.contracts import CommandEnvelope, make_receipt
from quanthus.product_daily.funding import BucketState, FundingState
from quanthus.shared.fact_contracts import FactError, digest


class SQLiteCycleBridgeAdapter:
    def __init__(
        self,
        path: Path | str,
        *,
        fault_hook: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = str(path)
        self._fault_hook = fault_hook
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version == 0 and not tables:
                connection.executescript(
                    """
                    CREATE TABLE bridge_baseline(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, request_id TEXT NOT NULL,
                      security_code TEXT NOT NULL, cash TEXT NOT NULL,
                      funding_json TEXT NOT NULL, PRIMARY KEY(account_id,mode));
                    CREATE TABLE bridge_account(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL,
                      event_sequence INTEGER NOT NULL, cash TEXT NOT NULL,
                      positions_json TEXT NOT NULL, fact_count INTEGER NOT NULL,
                      digest TEXT NOT NULL, PRIMARY KEY(account_id,mode));
                    CREATE TABLE bridge_funding(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL,
                      envelope_json TEXT NOT NULL, digest TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode));
                    CREATE TABLE bridge_inbox(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, report_id TEXT NOT NULL,
                      payload_digest TEXT NOT NULL, receipt_id TEXT NOT NULL,
                      payload_json TEXT NOT NULL, received_at TEXT NOT NULL,
                      status TEXT NOT NULL, result_json TEXT,
                      PRIMARY KEY(account_id,mode,report_id));
                    CREATE TABLE bridge_events(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, report_id TEXT NOT NULL,
                      event_sequence INTEGER NOT NULL, payload_json TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,report_id));
                    CREATE TABLE bridge_postings(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, report_id TEXT NOT NULL,
                      line_no INTEGER NOT NULL, account TEXT NOT NULL, amount TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,report_id,line_no));
                    CREATE TABLE bridge_outbox(
                      outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, report_id TEXT NOT NULL,
                      payload_json TEXT NOT NULL);
                    CREATE TABLE bridge_entry_receipts(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, command_id TEXT NOT NULL,
                      result_json TEXT NOT NULL, funding_json TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,command_id));
                    PRAGMA user_version=1;
                    """
                )
            elif version != 1:
                raise FactError("STORE_SCHEMA_MISMATCH", "unsupported cycle bridge schema")

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise FactError("INVALID_REPORT_SHAPE", "clock must be aware")
        return value.isoformat()

    def establish(self, baseline: CycleBaseline) -> BridgeState:
        c = baseline.context
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                old = connection.execute(
                    "SELECT * FROM bridge_baseline WHERE account_id=? AND mode=?",
                    (c.account_id, c.mode),
                ).fetchone()
                if old is not None:
                    envelope = json.loads(old["funding_json"])
                    return BridgeState(
                        AccountProjection(0, 0, Decimal(old["cash"]), (), 0),
                        restore_funding_envelope(envelope),
                        0,
                    )
                funding_json = funding_state_envelope(baseline.funding)
                connection.execute(
                    "INSERT INTO bridge_baseline VALUES(?,?,?,?,?,?)",
                    (
                        c.account_id,
                        c.mode,
                        baseline.request_id,
                        baseline.security_code,
                        format(baseline.account_cash, "f"),
                        json.dumps(funding_json, sort_keys=True),
                    ),
                )
                account = AccountProjection(0, 0, baseline.account_cash, (), 0)
                self._write_account(connection, c, account)
                self._write_funding(connection, c, baseline.funding)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return BridgeState(account, baseline.funding, 0)

    def read(self, context: SyntheticContext) -> BridgeState:
        with self._connect() as connection:
            return self._read(connection, context)

    def commit_entry(
        self, context: SyntheticContext, expected: str, result: EntryBridgeResult
    ) -> BridgeState:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                c = context
                state = self._read(connection, c)
                actual = digest(
                    {
                        "account": state.account.digest,
                        "funding": state.funding.digest,
                        "revision": state.account_revision,
                    }
                )
                if actual != expected:
                    raise FactError("ACCOUNT_REVISION_CONFLICT", "entry CAS conflict")
                self._write_funding(connection, c, result.funding)
                connection.execute(
                    "UPDATE bridge_account SET revision=? WHERE account_id=? AND mode=?",
                    (result.account_revision, c.account_id, c.mode),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO bridge_entry_receipts VALUES(?,?,?,?,?)",
                    (
                        c.account_id,
                        c.mode,
                        result.reservation_id or result.result_digest,
                        json.dumps(self._entry_dict(result), sort_keys=True),
                        json.dumps(funding_state_envelope(result.funding), sort_keys=True),
                    ),
                )
                connection.commit()
                return BridgeState(state.account, result.funding, result.account_revision)
            except Exception:
                connection.rollback()
                raise

    def receive_buy(self, context: SyntheticContext, report: ExecutionReport) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM bridge_inbox WHERE account_id=? AND mode=? AND report_id=?",
                    (context.account_id, context.mode, report.report_id),
                ).fetchone()
                if row is not None:
                    if row["payload_digest"] != report.payload_digest:
                        raise FactError("IDEMPOTENCY_CONFLICT", "report payload differs")
                    return str(row["receipt_id"])
                receipt_id = f"bridge-receipt-{report.report_id}"
                connection.execute(
                    "INSERT INTO bridge_inbox VALUES(?,?,?,?,?,?,?,?,NULL)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        report.payload_digest,
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                        self._timestamp(),
                        "RECEIVED",
                    ),
                )
                connection.commit()
                return receipt_id
            except Exception:
                connection.rollback()
                raise

    def replay_buy(
        self, context: SyntheticContext, report: ExecutionReport
    ) -> BuyBridgeResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM bridge_inbox WHERE account_id=? AND mode=? AND report_id=?",
                (context.account_id, context.mode, report.report_id),
            ).fetchone()
            if row is None or row["status"] != "POSTED":
                return None
            if row["payload_digest"] != report.payload_digest:
                raise FactError("IDEMPOTENCY_CONFLICT", "report payload differs")
            return self._buy_from_dict(json.loads(row["result_json"]))

    def commit_buy(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        receipt_id: str,
        report: ExecutionReport,
        profile: CycleFillProfile,
        account: AccountProjection,
        funding: FundingState,
        receipt: object,
        postings: tuple[Posting, ...],
    ) -> BuyBridgeResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._read(connection, context)
                if (
                    state.account.digest != expected_account_digest
                    or state.funding.digest != expected_funding_digest
                ):
                    raise FactError("ACCOUNT_REVISION_CONFLICT", "buy CAS conflict")
                self._write_funding(connection, context, funding)
                if self._fault_hook is not None:
                    self._fault_hook("after_funding_before_account")
                self._write_account(connection, context, account)
                connection.execute(
                    "INSERT INTO bridge_events VALUES(?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        account.event_sequence,
                        json.dumps(
                            {
                                "report": report.to_dict(),
                                "profile": {
                                    "profile_version": profile.profile_version,
                                    "source_id": profile.source_id,
                                    "order_total_quantity": profile.order_total_quantity,
                                    "final_cumulative_fill_quantity": (
                                        profile.final_cumulative_fill_quantity
                                    ),
                                    "cancelled_quantity": profile.cancelled_quantity,
                                    "confirmed_fee": format(profile.confirmed_fee, "f"),
                                    "terminal": profile.terminal,
                                },
                            },
                            sort_keys=True,
                        ),
                    ),
                )
                for index, posting in enumerate(postings, start=1):
                    connection.execute(
                        "INSERT INTO bridge_postings VALUES(?,?,?,?,?,?)",
                        (
                            context.account_id,
                            context.mode,
                            report.report_id,
                            index,
                            posting.account,
                            format(posting.amount, "f"),
                        ),
                    )
                result = BuyBridgeResult(
                    receipt_id, "POSTED", account, funding, account.revision, postings
                )
                connection.execute(
                    "UPDATE bridge_inbox SET status='POSTED',result_json=? "
                    "WHERE account_id=? AND mode=? AND report_id=?",
                    (
                        json.dumps(self._buy_dict(result), sort_keys=True),
                        context.account_id,
                        context.mode,
                        report.report_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO bridge_outbox(account_id,mode,report_id,payload_json) "
                    "VALUES(?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        json.dumps({"action": "REASSESS", "report_id": report.report_id}),
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def rebuild(self, context: SyntheticContext) -> BridgeState:
        with self._connect() as connection:
            state = self._read(connection, context)
            baseline = connection.execute(
                "SELECT * FROM bridge_baseline WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
            if baseline is None:
                raise FactError("BASELINE_NOT_FOUND", "baseline unavailable")
            account = AccountProjection(0, 0, Decimal(baseline["cash"]), (), 0)
            entry_count = connection.execute(
                "SELECT count(*) FROM bridge_entry_receipts WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()[0]
            if entry_count:
                account = AccountProjection(
                    entry_count,
                    0,
                    account.cash,
                    account.positions,
                    account.fact_count,
                )
            entry_rows = connection.execute(
                "SELECT funding_json FROM bridge_entry_receipts "
                "WHERE account_id=? AND mode=? ORDER BY rowid",
                (context.account_id, context.mode),
            ).fetchall()
            funding = (
                restore_funding_envelope(json.loads(entry_rows[-1]["funding_json"]))
                if entry_rows
                else restore_funding_envelope(json.loads(baseline["funding_json"]))
            )
            for row in connection.execute(
                "SELECT payload_json FROM bridge_events WHERE account_id=? AND mode=? "
                "ORDER BY event_sequence",
                (context.account_id, context.mode),
            ):
                event = json.loads(row["payload_json"])
                report = event["report"]
                account, _ = apply_buy_delta(
                    account,
                    security_code=report["security_code"],
                    quantity=report["quantity"],
                    price=Decimal(report["price"]),
                    fee=Decimal(report["confirmed_fee"]),
                )
                active = tuple(
                    item
                    for item in funding.active_entry_reservations
                    if item.security_code == report["security_code"]
                )
                if len(active) != 1 or active[0].quantity != report["quantity"]:
                    raise FactError("ENTRY_RESERVATION_MISMATCH", "rebuild reservation differs")
                bucket = funding.bucket(active[0].bucket_id)
                updated_bucket = BucketState(
                    bucket.bucket_id,
                    bucket.cash
                    - Decimal(report["price"]) * report["quantity"]
                    - Decimal(report["confirmed_fee"]),
                    bucket.target_notional,
                    bucket.locked_cycle_budget,
                    bucket.cycle_id,
                    bucket.phase,
                )
                result_body = {
                    "accepted": True,
                    "account_cash": format(account.cash, "f"),
                    "funding_cash": format(updated_bucket.cash + funding.reserve, "f"),
                }
                command = CommandEnvelope.create(
                    f"post:{report['report_id']}", result_body, funding.revision
                )
                receipt = make_receipt(
                    command=command,
                    operation="post_execution_report",
                    invocation_payload=result_body,
                    result=result_body,
                    revision=funding.revision + 1,
                )
                funding = FundingState(
                    (updated_bucket,),
                    funding.reserve,
                    funding.unallocated_adjustment,
                    funding.revision + 1,
                    (*funding.processed_commands, receipt),
                    funding.completed_settlements,
                    funding.entry_reservations,
                )
            if account != state.account:
                raise FactError(
                    "REBUILD_MISMATCH",
                    "account projection differs: "
                    f"rebuilt={account.to_dict()} stored={state.account.to_dict()}",
                )
            if funding != state.funding:
                raise FactError("REBUILD_MISMATCH", "funding projection differs")
            return BridgeState(account, funding, state.account_revision)

    def _read(self, connection: sqlite3.Connection, context: SyntheticContext) -> BridgeState:
        account_row = connection.execute(
            "SELECT * FROM bridge_account WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        funding_row = connection.execute(
            "SELECT * FROM bridge_funding WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if account_row is None or funding_row is None:
            raise FactError("BASELINE_NOT_FOUND", "bridge baseline unavailable")
        positions = tuple(
            Position(item["security_code"], item["quantity"])
            for item in json.loads(account_row["positions_json"])
        )
        account = AccountProjection(
            account_row["revision"],
            account_row["event_sequence"],
            Decimal(account_row["cash"]),
            positions,
            account_row["fact_count"],
        )
        funding = restore_funding_envelope(json.loads(funding_row["envelope_json"]))
        return BridgeState(account, funding, account_row["revision"])

    def _write_account(
        self, connection: sqlite3.Connection, context: SyntheticContext, account: AccountProjection
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO bridge_account VALUES(?,?,?,?,?,?,?,?)",
            (
                context.account_id,
                context.mode,
                account.revision,
                account.event_sequence,
                format(account.cash, "f"),
                json.dumps([item.to_dict() for item in account.positions], sort_keys=True),
                account.fact_count,
                account.digest,
            ),
        )

    def _write_funding(
        self, connection: sqlite3.Connection, context: SyntheticContext, funding: FundingState
    ) -> None:
        envelope = funding_state_envelope(funding)
        connection.execute(
            "INSERT OR REPLACE INTO bridge_funding VALUES(?,?,?,?)",
            (
                context.account_id,
                context.mode,
                json.dumps(envelope, sort_keys=True),
                str(envelope["digest"]),
            ),
        )

    def _buy_dict(self, result: BuyBridgeResult) -> dict[str, object]:
        return {
            "receipt_id": result.receipt_id,
            "status": result.status,
            "account": result.account.to_dict(),
            "funding": funding_state_envelope(result.funding),
            "account_revision": result.account_revision,
            "postings": [item.to_dict() for item in result.postings],
        }

    def _buy_from_dict(self, raw: dict[str, Any]) -> BuyBridgeResult:
        account_raw = raw["account"]
        account = AccountProjection(
            int(account_raw["revision"]),
            int(account_raw["event_sequence"]),
            Decimal(str(account_raw["cash"])),
            tuple(
                Position(item["security_code"], item["quantity"])
                for item in account_raw["positions"]
            ),
            int(account_raw["fact_count"]),
        )
        return BuyBridgeResult(
            str(raw["receipt_id"]),
            str(raw["status"]),
            account,
            restore_funding_envelope(raw["funding"]),
            int(raw["account_revision"]),
            tuple(
                Posting(str(item["account"]), Decimal(str(item["amount"])))
                for item in raw["postings"]
            ),
        )

    def _entry_dict(self, result: EntryBridgeResult) -> dict[str, object]:
        return {
            "accepted": result.accepted,
            "replayed": result.replayed,
            "quantity": result.quantity,
            "reservation_id": result.reservation_id,
            "account_revision": result.account_revision,
            "result_digest": result.result_digest,
        }


def build_funding_bridge_service(
    path: Path | str,
    *,
    context: SyntheticContext,
    authorization: SyntheticRuntimeAuthorization,
    fault_hook: Callable[[str], None] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FundingBridgeService:
    authorization.require(context)
    return FundingBridgeService(
        SQLiteCycleBridgeAdapter(path, fault_hook=fault_hook, clock=clock),
        context,
        authorization,
    )
