"""SQLite UnitOfWork for the synthetic funding/account first-buy bridge."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from quanthus.capabilities.accounting import AccountProjection, Position, Posting
from quanthus.product_daily.application.funding_bridge import (
    BridgeSnapshot,
    BridgeState,
    BuyBridgeResult,
    BuyLot,
    CycleBaseline,
    CycleFillProfile,
    EntryBridgeResult,
    ExecutionReport,
    FundingBridgeService,
    LateFeeFact,
    LateFeeResult,
    PendingObligation,
    SellExecutionReport,
    SellImpact,
    SellProfile,
    SettlementBridgeResult,
    SyntheticContext,
    apply_confirmed_buy_transition,
    apply_confirmed_sell_transition,
    apply_late_fee_transition,
    calculate_entry_transition,
    entry_intent_to_dict,
    funding_state_envelope,
    restore_entry_intent,
    restore_funding_envelope,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.contracts import CommandEnvelope, ProcessedCommand, ReasonCode
from quanthus.product_daily.funding import (
    BucketPhase,
    CycleSettlementEvidence,
    FundingDecision,
    FundingLedgerEntry,
    FundingLedgerPosting,
    FundingState,
    SettlementRequest,
    late_cash_invocation_payload,
    settle_batch,
    settle_invocation_payload,
)
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
                      funding_json TEXT NOT NULL, payload_digest TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode));
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
                      status TEXT NOT NULL, result_json TEXT, profile_digest TEXT NOT NULL,
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
                      accepted INTEGER NOT NULL, result_json TEXT NOT NULL,
                      funding_json TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,command_id));
                    CREATE TABLE bridge_obligations(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, obligation_id TEXT NOT NULL,
                      reservation_id TEXT NOT NULL, security_code TEXT NOT NULL,
                      level1_industry_id TEXT NOT NULL, bucket_id TEXT NOT NULL,
                      cycle_id TEXT NOT NULL,
                      quantity INTEGER NOT NULL, price_bound TEXT NOT NULL,
                      fee_upper_bound TEXT NOT NULL, source_id TEXT NOT NULL,
                      status TEXT NOT NULL, filled_report_id TEXT, profile_digest TEXT,
                      PRIMARY KEY(account_id,mode,obligation_id));
                    CREATE TABLE bridge_source_facts(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, source_id TEXT NOT NULL,
                      source_sequence TEXT NOT NULL, receipt_id TEXT NOT NULL,
                      report_id TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,source_id,source_sequence));
                    CREATE TABLE bridge_conflicts(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, identity TEXT NOT NULL,
                      old_digest TEXT NOT NULL, new_digest TEXT NOT NULL,
                      evidence_json TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,identity,old_digest,new_digest));
                    CREATE TABLE bridge_lots(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, lot_id TEXT NOT NULL,
                      security_code TEXT NOT NULL, cycle_id TEXT NOT NULL,
                      quantity INTEGER NOT NULL, remaining_quantity INTEGER NOT NULL,
                      cost_basis TEXT NOT NULL, source_report_id TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,lot_id));
                    CREATE TABLE bridge_operations(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL,
                      identity TEXT NOT NULL, payload_digest TEXT NOT NULL,
                      receipt_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                      received_at TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT,
                      PRIMARY KEY(account_id,mode,kind,identity));
                    CREATE TABLE bridge_journal(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, sequence INTEGER NOT NULL,
                      transition_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                      digest TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,sequence),
                      UNIQUE(account_id,mode,transition_id));
                    PRAGMA user_version=3;
                    """
                )
            elif version == 3:
                expected = {
                    "bridge_baseline",
                    "bridge_account",
                    "bridge_funding",
                    "bridge_inbox",
                    "bridge_events",
                    "bridge_postings",
                    "bridge_outbox",
                    "bridge_entry_receipts",
                    "bridge_obligations",
                    "bridge_source_facts",
                    "bridge_conflicts",
                    "bridge_lots",
                    "bridge_operations",
                    "bridge_journal",
                }
                if not expected.issubset({row[0] for row in tables}):
                    raise FactError("STORE_SCHEMA_MISMATCH", "cycle bridge schema is incomplete")
            else:
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
                    if old["payload_digest"] != baseline.payload_digest:
                        self._record_conflict(
                            connection,
                            c,
                            "baseline",
                            baseline.request_id,
                            old["payload_digest"],
                            baseline.payload_digest,
                            json.dumps(
                                {
                                    "request_id": baseline.request_id,
                                    "security_code": baseline.security_code,
                                    "cash": format(baseline.account_cash, "f"),
                                    "funding": funding_state_envelope(baseline.funding),
                                },
                                sort_keys=True,
                            ),
                        )
                        connection.commit()
                        raise FactError(
                            "IDEMPOTENCY_CONFLICT", "baseline content differs from original"
                        )
                    envelope = json.loads(old["funding_json"])
                    return BridgeState(
                        AccountProjection(0, 0, Decimal(old["cash"]), (), 0),
                        restore_funding_envelope(envelope),
                        0,
                    )
                funding_json = funding_state_envelope(baseline.funding)
                connection.execute(
                    "INSERT INTO bridge_baseline VALUES(?,?,?,?,?,?,?)",
                    (
                        c.account_id,
                        c.mode,
                        baseline.request_id,
                        baseline.security_code,
                        format(baseline.account_cash, "f"),
                        json.dumps(funding_json, sort_keys=True),
                        baseline.payload_digest,
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

    def read_snapshot(self, context: SyntheticContext) -> BridgeSnapshot:
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                snapshot = BridgeSnapshot(
                    self._read(connection, context),
                    self._obligation_rows(connection, context),
                )
                connection.commit()
                return snapshot
            except Exception:
                connection.rollback()
                raise

    def read(self, context: SyntheticContext) -> BridgeState:
        return self.read_snapshot(context).state

    def obligations(self, context: SyntheticContext) -> tuple[PendingObligation, ...]:
        return self.read_snapshot(context).obligations

    def lots(self, context: SyntheticContext) -> tuple[BuyLot, ...]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT * FROM bridge_lots WHERE account_id=? AND mode=? ORDER BY lot_id",
                (context.account_id, context.mode),
            ).fetchall()
            connection.commit()
        return tuple(
            BuyLot(
                row["lot_id"],
                row["security_code"],
                row["cycle_id"],
                row["quantity"],
                row["remaining_quantity"],
                Decimal(row["cost_basis"]),
                row["source_report_id"],
            )
            for row in rows
        )

    def receive_operation(
        self,
        context: SyntheticContext,
        kind: str,
        identity: str,
        payload_digest: str,
        payload: object,
    ) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM bridge_operations "
                    "WHERE account_id=? AND mode=? AND kind=? AND identity=?",
                    (context.account_id, context.mode, kind, identity),
                ).fetchone()
                if row is not None:
                    if row["payload_digest"] == payload_digest:
                        return str(row["receipt_id"])
                    if row["status"] != "POSTED":
                        self._record_conflict(
                            connection,
                            context,
                            f"{kind}_retry",
                            identity,
                            row["payload_digest"],
                            payload_digest,
                            json.dumps(payload, sort_keys=True),
                        )
                        connection.execute(
                            "UPDATE bridge_operations SET payload_digest=?,payload_json=?,"
                            "received_at=? WHERE account_id=? AND mode=? AND kind=? "
                            "AND identity=?",
                            (
                                payload_digest,
                                json.dumps(payload, sort_keys=True),
                                self._timestamp(),
                                context.account_id,
                                context.mode,
                                kind,
                                identity,
                            ),
                        )
                        connection.commit()
                        return str(row["receipt_id"])
                    if row["payload_digest"] != payload_digest:
                        self._record_conflict(
                            connection,
                            context,
                            f"{kind}_operation",
                            identity,
                            row["payload_digest"],
                            payload_digest,
                            json.dumps(payload, sort_keys=True),
                        )
                        connection.commit()
                        raise FactError("IDEMPOTENCY_CONFLICT", "operation payload differs")
                receipt_id = f"{kind}-receipt-{identity}"
                connection.execute(
                    "INSERT INTO bridge_operations VALUES(?,?,?,?,?,?,?,?, 'RECEIVED',NULL)",
                    (
                        context.account_id,
                        context.mode,
                        kind,
                        identity,
                        payload_digest,
                        receipt_id,
                        json.dumps(payload, sort_keys=True),
                        self._timestamp(),
                    ),
                )
                connection.commit()
                return receipt_id
            except Exception:
                connection.rollback()
                raise

    def check_source_alias(
        self,
        context: SyntheticContext,
        *,
        identity: str,
        source_id: str,
        source_sequence: str,
        new_digest: str,
        payload: object,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                source = connection.execute(
                    "SELECT report_id,receipt_id FROM bridge_source_facts "
                    "WHERE account_id=? AND mode=? AND source_id=? AND source_sequence=?",
                    (context.account_id, context.mode, source_id, source_sequence),
                ).fetchone()
                if source is not None and source["report_id"] != identity:
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{source_id}:{source_sequence}",
                        str(source["receipt_id"]),
                        new_digest,
                        json.dumps(payload, sort_keys=True),
                    )
                    connection.commit()
                    raise FactError("SOURCE_FACT_DUPLICATE", "source alias differs")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

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
                if result.accepted:
                    self._write_funding(connection, c, result.funding)
                    self._write_account(
                        connection,
                        c,
                        AccountProjection(
                            result.account_revision,
                            state.account.event_sequence,
                            state.account.cash,
                            state.account.positions,
                            state.account.fact_count,
                        ),
                    )
                    if result.obligation is not None and not result.replayed:
                        obligation = result.obligation
                        connection.execute(
                            "INSERT INTO bridge_obligations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                c.account_id,
                                c.mode,
                                obligation.obligation_id,
                                obligation.reservation_id,
                                obligation.security_code,
                                obligation.level1_industry_id,
                                obligation.bucket_id,
                                obligation.cycle_id,
                                obligation.quantity,
                                format(obligation.price_bound, "f"),
                                format(obligation.fee_upper_bound, "f"),
                                obligation.source_id,
                                obligation.status,
                                None,
                                None,
                            ),
                        )
                    if result.accepted and not result.replayed:
                        self._append_journal(
                            connection,
                            c,
                            transition_id=f"entry:{result.command_id}",
                            kind="entry",
                            payload={
                                **self._entry_dict(result),
                                "funding": funding_state_envelope(result.funding),
                                "intent": (
                                    entry_intent_to_dict(result.intent)
                                    if result.intent is not None
                                    else None
                                ),
                            },
                        )
                connection.execute(
                    "INSERT OR IGNORE INTO bridge_entry_receipts VALUES(?,?,?,?,?,?)",
                    (
                        c.account_id,
                        c.mode,
                        result.command_id,
                        1 if result.accepted else 0,
                        json.dumps(self._entry_dict(result), sort_keys=True),
                        json.dumps(funding_state_envelope(result.funding), sort_keys=True),
                    ),
                )
                connection.commit()
                return BridgeState(state.account, result.funding, result.account_revision)
            except Exception:
                connection.rollback()
                raise

    def receive_buy(
        self, context: SyntheticContext, report: ExecutionReport, profile: CycleFillProfile
    ) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM bridge_inbox WHERE account_id=? AND mode=? AND report_id=?",
                    (context.account_id, context.mode, report.report_id),
                ).fetchone()
                if row is not None:
                    if (
                        row["payload_digest"] != report.payload_digest
                        or row["profile_digest"] != profile.payload_digest
                    ):
                        self._record_conflict(
                            connection,
                            context,
                            "report",
                            report.report_id,
                            f"{row['payload_digest']}:{row['profile_digest']}",
                            f"{report.payload_digest}:{profile.payload_digest}",
                            json.dumps(
                                {
                                    "report": report.to_dict(),
                                    "profile_digest": profile.payload_digest,
                                },
                                sort_keys=True,
                            ),
                        )
                        connection.commit()
                        raise FactError("IDEMPOTENCY_CONFLICT", "report payload differs")
                    return str(row["receipt_id"])
                receipt_id = f"bridge-receipt-{report.report_id}"
                connection.execute(
                    "INSERT INTO bridge_inbox VALUES(?,?,?,?,?,?,?,?,NULL,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        report.payload_digest,
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                        self._timestamp(),
                        "RECEIVED",
                        profile.payload_digest,
                    ),
                )
                source = connection.execute(
                    "SELECT report_id,receipt_id FROM bridge_source_facts "
                    "WHERE account_id=? AND mode=? AND source_id=? AND source_sequence=?",
                    (
                        context.account_id,
                        context.mode,
                        report.source_id,
                        report.source_sequence,
                    ),
                ).fetchone()
                if source is not None and source["report_id"] != report.report_id:
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{report.source_id}:{report.source_sequence}",
                        str(source["receipt_id"]),
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                    )
                    connection.commit()
                    raise FactError(
                        "SOURCE_FACT_DUPLICATE",
                        f"source already posted by {source['report_id']}",
                    )
                connection.commit()
                return receipt_id
            except Exception:
                connection.rollback()
                raise

    def replay_buy(
        self,
        context: SyntheticContext,
        report: ExecutionReport,
        profile: CycleFillProfile,
    ) -> BuyBridgeResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM bridge_inbox WHERE account_id=? AND mode=? AND report_id=?",
                (context.account_id, context.mode, report.report_id),
            ).fetchone()
            if row is None or row["status"] != "POSTED":
                return None
            if (
                row["payload_digest"] != report.payload_digest
                or row["profile_digest"] != profile.payload_digest
            ):
                connection.execute("BEGIN IMMEDIATE")
                self._record_conflict(
                    connection,
                    context,
                    "report",
                    report.report_id,
                    f"{row['payload_digest']}:{row['profile_digest']}",
                    f"{report.payload_digest}:{profile.payload_digest}",
                    json.dumps(
                        {
                            "report": report.to_dict(),
                            "profile_digest": profile.payload_digest,
                        },
                        sort_keys=True,
                    ),
                )
                connection.commit()
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
        obligation: PendingObligation,
        account: AccountProjection,
        funding: FundingState,
        receipt: ProcessedCommand,
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
                source = connection.execute(
                    "SELECT report_id,receipt_id FROM bridge_source_facts "
                    "WHERE account_id=? AND mode=? AND source_id=? AND source_sequence=?",
                    (
                        context.account_id,
                        context.mode,
                        report.source_id,
                        report.source_sequence,
                    ),
                ).fetchone()
                if source is not None:
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{report.source_id}:{report.source_sequence}",
                        str(source["receipt_id"]),
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                    )
                    connection.commit()
                    raise FactError(
                        "SOURCE_FACT_DUPLICATE", f"source already posted by {source['report_id']}"
                    )
                updated_obligation = connection.execute(
                    "UPDATE bridge_obligations SET status='FILLED',filled_report_id=?,"
                    "profile_digest=? WHERE account_id=? AND mode=? AND obligation_id=? "
                    "AND status='OPEN'",
                    (
                        report.report_id,
                        profile.payload_digest,
                        context.account_id,
                        context.mode,
                        obligation.obligation_id,
                    ),
                ).rowcount
                if updated_obligation != 1:
                    raise FactError("ORDER_ALREADY_FILLED", "application obligation is terminal")
                connection.execute(
                    "INSERT INTO bridge_source_facts VALUES(?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.source_id,
                        report.source_sequence,
                        receipt_id,
                        report.report_id,
                    ),
                )
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
                connection.execute(
                    "INSERT INTO bridge_lots VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        f"lot:{report.report_id}",
                        report.security_code,
                        obligation.cycle_id,
                        report.quantity,
                        report.quantity,
                        format(report.price * report.quantity, "f"),
                        report.report_id,
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
                self._append_journal(
                    connection,
                    context,
                    transition_id=f"buy:{report.report_id}",
                    kind="buy",
                    payload={
                        "report": report.to_dict(),
                        "profile": profile.payload_digest,
                        "profile_data": {
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
                        "obligation_id": obligation.obligation_id,
                        "cycle_id": obligation.cycle_id,
                        "account_revision": account.revision,
                        "funding": funding_state_envelope(funding),
                    },
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def replay_sell(
        self,
        context: SyntheticContext,
        report: SellExecutionReport,
        profile: SellProfile,
    ) -> BuyBridgeResult | None:
        expected = digest({"report": report.to_dict(), "profile": profile.payload_digest})
        return self._replay_buy_operation(context, "sell", report.report_id, expected)

    def commit_sell(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        receipt_id: str,
        report: SellExecutionReport,
        profile: SellProfile,
        impact: SellImpact,
    ) -> BuyBridgeResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._read(connection, context)
                if (
                    state.account.digest != expected_account_digest
                    or state.funding.digest != expected_funding_digest
                ):
                    raise FactError("ACCOUNT_REVISION_CONFLICT", "sell CAS conflict")
                source = connection.execute(
                    "SELECT report_id,receipt_id FROM bridge_source_facts "
                    "WHERE account_id=? AND mode=? AND source_id=? AND source_sequence=?",
                    (
                        context.account_id,
                        context.mode,
                        report.source_id,
                        report.source_sequence,
                    ),
                ).fetchone()
                if source is not None:
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{report.source_id}:{report.source_sequence}",
                        str(source["receipt_id"]),
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                    )
                    connection.commit()
                    raise FactError("SOURCE_FACT_DUPLICATE", "sell source already posted")
                updated_lot = connection.execute(
                    "UPDATE bridge_lots SET remaining_quantity=0 "
                    "WHERE account_id=? AND mode=? AND lot_id=? AND remaining_quantity=?",
                    (
                        context.account_id,
                        context.mode,
                        impact.lot.lot_id,
                        report.quantity,
                    ),
                ).rowcount
                if updated_lot != 1:
                    raise FactError("ORDER_ALREADY_FILLED", "sell lot is terminal")
                connection.execute(
                    "INSERT INTO bridge_source_facts VALUES(?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.source_id,
                        report.source_sequence,
                        receipt_id,
                        report.report_id,
                    ),
                )
                self._write_funding(connection, context, impact.funding)
                if self._fault_hook is not None:
                    self._fault_hook("after_sell_funding_before_account")
                self._write_account(connection, context, impact.account)
                connection.execute(
                    "INSERT INTO bridge_events VALUES(?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        impact.account.event_sequence,
                        json.dumps(
                            {
                                "kind": "sell",
                                "report": report.to_dict(),
                                "profile": profile.payload_digest,
                                "lot_id": impact.lot.lot_id,
                                "cycle_id": report.cycle_id,
                            },
                            sort_keys=True,
                        ),
                    ),
                )
                for index, posting in enumerate(impact.postings, start=1):
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
                    receipt_id,
                    "POSTED",
                    impact.account,
                    impact.funding,
                    impact.account.revision,
                    impact.postings,
                )
                self._store_operation(connection, context, "sell", report.report_id, result)
                self._append_journal(
                    connection,
                    context,
                    transition_id=f"sell:{report.report_id}",
                    kind="sell",
                    payload={
                        "report": report.to_dict(),
                        "profile": profile.payload_digest,
                        "profile_data": {
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
                        "lot": {
                            "lot_id": impact.lot.lot_id,
                            "security_code": impact.lot.security_code,
                            "cycle_id": impact.lot.cycle_id,
                            "quantity": impact.lot.quantity,
                            "remaining_quantity": report.quantity,
                            "cost_basis": format(impact.lot.cost_basis, "f"),
                            "source_report_id": impact.lot.source_report_id,
                        },
                        "account_revision": impact.account.revision,
                        "funding": funding_state_envelope(impact.funding),
                    },
                )
                self._outbox(
                    connection,
                    context,
                    f"sell:{report.report_id}",
                    {"action": "REASSESS", "report_id": report.report_id},
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def settlement_facts(
        self, context: SyntheticContext, cycle_id: str
    ) -> tuple[bool, bool, bool, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT kind,payload_json FROM bridge_journal "
                "WHERE account_id=? AND mode=? AND kind IN ('buy','sell') ORDER BY sequence",
                (context.account_id, context.mode),
            ).fetchall()
            matching = [json.loads(row["payload_json"]) for row in rows]
            buy_sell = [
                item
                for item in matching
                if (
                    item.get("cycle_id")
                    or item.get("report", {}).get("cycle_id")
                    or item.get("lot", {}).get("cycle_id")
                )
                == cycle_id
            ]
            orders_terminal = len(buy_sell) >= 2 and all(
                bool(item.get("profile_data", {}).get("terminal")) for item in buy_sell
            )
            open_obligations = connection.execute(
                "SELECT count(*) FROM bridge_obligations "
                "WHERE account_id=? AND mode=? AND status='OPEN'",
                (context.account_id, context.mode),
            ).fetchone()[0]
            fills_complete = open_obligations == 0
            fees_known = all(
                item.get("profile_data", {}).get("confirmed_fee") is not None for item in buy_sell
            )
            # Rejected source aliases are resolved audit evidence, not open reconciliation cases.
            conflicts_clear = True
            connection.commit()
        return orders_terminal, fills_complete, fees_known, conflicts_clear

    def replay_settlement(
        self, context: SyntheticContext, settlement_id: str, cycle_id: str
    ) -> SettlementBridgeResult | None:
        row = self._operation_row(context, "settlement", settlement_id)
        if row is None or row["status"] != "POSTED":
            return None
        payload = json.loads(row["payload_json"])
        requests = payload.get("requests", [])
        if len(requests) != 1 or requests[0].get("cycle_id") != cycle_id:
            raise FactError("IDEMPOTENCY_CONFLICT", "settlement payload differs")
        return self._settlement_from_dict(json.loads(row["result_json"]))

    def commit_settlement(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        result: SettlementBridgeResult,
        request: SettlementRequest,
    ) -> SettlementBridgeResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._read(connection, context)
                if (
                    state.account.digest != expected_account_digest
                    or state.funding.digest != expected_funding_digest
                ):
                    raise FactError("ACCOUNT_REVISION_CONFLICT", "settlement CAS conflict")
                account = AccountProjection(
                    result.account_revision,
                    state.account.event_sequence,
                    state.account.cash,
                    state.account.positions,
                    state.account.fact_count,
                )
                self._write_funding(connection, context, result.funding)
                if self._fault_hook is not None:
                    self._fault_hook("after_settlement_funding_before_projection")
                self._write_account(connection, context, account)
                self._store_operation(
                    connection, context, "settlement", result.settlement_id, result
                )
                self._append_journal(
                    connection,
                    context,
                    transition_id=f"settlement:{result.settlement_id}",
                    kind="settlement",
                    payload={
                        "settlement_id": result.settlement_id,
                        "request": {
                            "settlement_id": request.settlement_id,
                            "bucket_id": request.bucket_id,
                            "cycle_id": request.cycle_id,
                            "evidence": request.evidence.to_dict(),
                        },
                        "result": self._settlement_dict(result),
                        "account_revision": account.revision,
                    },
                )
                self._outbox(
                    connection,
                    context,
                    f"settlement:{result.settlement_id}",
                    {"action": "SETTLEMENT_PUBLISHED"},
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def replay_late_fee(self, context: SyntheticContext, fact: LateFeeFact) -> LateFeeResult | None:
        row = self._operation_row(context, "late_fee", fact.fact_id)
        if row is None or row["status"] != "POSTED":
            return None
        if row["payload_digest"] != fact.payload_digest:
            raise FactError("IDEMPOTENCY_CONFLICT", "late fee payload differs")
        return self._late_from_dict(json.loads(row["result_json"]))

    def commit_late_fee(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        fact: LateFeeFact,
        result: LateFeeResult,
    ) -> LateFeeResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._read(connection, context)
                if (
                    state.account.digest != expected_account_digest
                    or state.funding.digest != expected_funding_digest
                ):
                    raise FactError("ACCOUNT_REVISION_CONFLICT", "late fee CAS conflict")
                source = connection.execute(
                    "SELECT report_id,receipt_id FROM bridge_source_facts "
                    "WHERE account_id=? AND mode=? AND source_id=? AND source_sequence=?",
                    (
                        context.account_id,
                        context.mode,
                        fact.source_id,
                        fact.source_sequence,
                    ),
                ).fetchone()
                if source is not None:
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{fact.source_id}:{fact.source_sequence}",
                        str(source["receipt_id"]),
                        result.receipt_id,
                        json.dumps(fact.payload_digest),
                    )
                    connection.commit()
                    raise FactError("SOURCE_FACT_DUPLICATE", "late fee source already posted")
                connection.execute(
                    "INSERT INTO bridge_source_facts VALUES(?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        fact.source_id,
                        fact.source_sequence,
                        result.receipt_id,
                        fact.fact_id,
                    ),
                )
                self._write_funding(connection, context, result.funding)
                if self._fault_hook is not None:
                    self._fault_hook("after_late_fee_funding_before_projection")
                self._write_account(connection, context, result.account)
                for index, posting in enumerate(result.postings, start=1):
                    connection.execute(
                        "INSERT INTO bridge_postings VALUES(?,?,?,?,?,?)",
                        (
                            context.account_id,
                            context.mode,
                            fact.fact_id,
                            index,
                            posting.account,
                            format(posting.amount, "f"),
                        ),
                    )
                self._store_operation(connection, context, "late_fee", fact.fact_id, result)
                self._append_journal(
                    connection,
                    context,
                    transition_id=f"late_fee:{fact.fact_id}",
                    kind="late_fee",
                    payload={
                        "fact": {
                            "fact_id": fact.fact_id,
                            "bucket_id": fact.bucket_id,
                            "cycle_id": fact.cycle_id,
                            "cash_delta": format(fact.cash_delta, "f"),
                            "source_id": fact.source_id,
                            "source_sequence": fact.source_sequence,
                            "occurred_at": fact.occurred_at.isoformat(),
                        },
                        "result": self._late_dict(result),
                        "account_revision": result.account_revision,
                    },
                )
                self._outbox(
                    connection,
                    context,
                    f"late_fee:{fact.fact_id}",
                    {"action": "REASSESS", "fact_id": fact.fact_id},
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def rebuild(self, context: SyntheticContext) -> BridgeState:
        with self._connect() as connection:
            connection.execute("BEGIN")
            state = self._read(connection, context)
            baseline = connection.execute(
                "SELECT * FROM bridge_baseline WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
            if baseline is None:
                raise FactError("BASELINE_NOT_FOUND", "baseline unavailable")
            account = AccountProjection(0, 0, Decimal(baseline["cash"]), (), 0)
            funding = restore_funding_envelope(json.loads(baseline["funding_json"]))
            obligations = self._obligation_rows(connection, context)
            for row in connection.execute(
                "SELECT kind,payload_json,digest FROM bridge_journal "
                "WHERE account_id=? AND mode=? "
                "ORDER BY sequence",
                (context.account_id, context.mode),
            ):
                event = json.loads(row["payload_json"])
                if row["digest"] != digest(event):
                    raise FactError("REBUILD_MISMATCH", "journal digest differs")
                kind = str(row["kind"])
                if kind == "entry":
                    if event.get("intent") is None:
                        raise FactError("REBUILD_MISMATCH", "entry journal lacks typed intent")
                    intent = restore_entry_intent(event["intent"])
                    entry_result = calculate_entry_transition(
                        BridgeState(account, funding, account.revision),
                        intent,
                        context.source_id,
                    )
                    funding = entry_result.funding
                    if funding_state_envelope(funding) != event["funding"]:
                        raise FactError("REBUILD_MISMATCH", "entry transition output differs")
                    account = AccountProjection(
                        entry_result.account_revision,
                        account.event_sequence,
                        account.cash,
                        account.positions,
                        account.fact_count,
                    )
                elif kind == "buy":
                    raw = event["report"]
                    buy_report = ExecutionReport(
                        raw["report_id"],
                        raw["security_code"],
                        raw["side"],
                        raw["quantity"],
                        Decimal(raw["price"]),
                        Decimal(raw["confirmed_fee"]),
                        datetime.fromisoformat(raw["occurred_at"]),
                        raw["source_id"],
                        raw["source_sequence"],
                        raw["intended_quantity"],
                        Decimal(raw["intended_price_cap"]),
                    )
                    raw_profile = event["profile_data"]
                    buy_profile = CycleFillProfile(
                        raw_profile["profile_version"],
                        raw_profile["source_id"],
                        raw_profile["order_total_quantity"],
                        raw_profile["final_cumulative_fill_quantity"],
                        raw_profile["cancelled_quantity"],
                        Decimal(raw_profile["confirmed_fee"]),
                        raw_profile["terminal"],
                    )
                    obligation_id = event["obligation_id"]
                    obligation = replace(
                        next(item for item in obligations if item.obligation_id == obligation_id),
                        status="OPEN",
                    )
                    buy_impact = apply_confirmed_buy_transition(
                        BridgeState(account, funding, account.revision),
                        buy_report,
                        buy_profile,
                        obligation,
                    )
                    account, funding = buy_impact.account, buy_impact.funding
                elif kind == "sell":
                    raw = event["report"]
                    sell_report = SellExecutionReport(
                        raw["report_id"],
                        raw["security_code"],
                        raw["cycle_id"],
                        raw["quantity"],
                        Decimal(raw["price"]),
                        Decimal(raw["confirmed_fee"]),
                        datetime.fromisoformat(raw["occurred_at"]),
                        raw["source_id"],
                        raw["source_sequence"],
                    )
                    raw_profile = event["profile_data"]
                    sell_profile = SellProfile(
                        raw_profile["profile_version"],
                        raw_profile["source_id"],
                        raw_profile["order_total_quantity"],
                        raw_profile["final_cumulative_fill_quantity"],
                        raw_profile["cancelled_quantity"],
                        Decimal(raw_profile["confirmed_fee"]),
                        raw_profile["terminal"],
                    )
                    raw_lot = event["lot"]
                    lot = BuyLot(
                        raw_lot["lot_id"],
                        raw_lot["security_code"],
                        raw_lot["cycle_id"],
                        raw_lot["quantity"],
                        raw_lot["remaining_quantity"],
                        Decimal(raw_lot["cost_basis"]),
                        raw_lot["source_report_id"],
                    )
                    sell_impact = apply_confirmed_sell_transition(
                        BridgeState(account, funding, account.revision),
                        sell_report,
                        sell_profile,
                        lot,
                    )
                    account, funding = sell_impact.account, sell_impact.funding
                elif kind == "settlement":
                    raw_request = event["request"]
                    evidence = raw_request["evidence"]
                    request = SettlementRequest(
                        raw_request["settlement_id"],
                        raw_request["bucket_id"],
                        raw_request["cycle_id"],
                        CycleSettlementEvidence(
                            evidence["position_flat"],
                            evidence["orders_terminal"],
                            evidence["fills_complete"],
                            evidence["fees_known"],
                            evidence["conflicts_clear"],
                        ),
                    )
                    closed = FundingState(
                        tuple(
                            replace(item, phase=BucketPhase.CLOSED)
                            if item.bucket_id == request.bucket_id
                            else item
                            for item in funding.buckets
                        ),
                        funding.reserve,
                        funding.unallocated_adjustment,
                        funding.revision,
                        funding.processed_commands,
                        funding.completed_settlements,
                        funding.entry_reservations,
                    )
                    payload = settle_invocation_payload((request,))
                    command = CommandEnvelope.create(
                        f"settle:{request.settlement_id}", payload, closed.revision
                    )
                    settled = settle_batch(closed, command, (request,))
                    if not settled.accepted:
                        raise FactError("REBUILD_MISMATCH", "settlement replay refused")
                    funding = settled.state
                    account = AccountProjection(
                        int(event["account_revision"]),
                        account.event_sequence,
                        account.cash,
                        account.positions,
                        account.fact_count,
                    )
                elif kind == "late_fee":
                    raw = event["fact"]
                    fact = LateFeeFact(
                        raw["fact_id"],
                        raw["bucket_id"],
                        raw["cycle_id"],
                        Decimal(raw["cash_delta"]),
                        raw["source_id"],
                        raw["source_sequence"],
                        datetime.fromisoformat(raw["occurred_at"]),
                    )
                    payload = late_cash_invocation_payload(
                        bucket_id=fact.bucket_id,
                        cycle_id=fact.cycle_id,
                        cash_delta=fact.cash_delta,
                    )
                    command = CommandEnvelope.create(fact.fact_id, payload, funding.revision)
                    late = apply_late_fee_transition(
                        BridgeState(account, funding, account.revision), fact, command
                    )
                    account, funding = late.account, late.funding
                else:
                    raise FactError("REBUILD_MISMATCH", f"unsupported journal kind {kind}")
                expected_funding = (
                    event.get("funding")
                    if kind in {"entry", "buy", "sell"}
                    else event.get("result", {}).get("funding")
                )
                if (
                    expected_funding is not None
                    and funding_state_envelope(funding) != expected_funding
                ):
                    raise FactError("REBUILD_MISMATCH", f"{kind} funding transition differs")
                expected_revision = event.get("account_revision")
                if expected_revision is not None and account.revision != int(expected_revision):
                    raise FactError("REBUILD_MISMATCH", "journal account revision differs")
            if account != state.account:
                raise FactError(
                    "REBUILD_MISMATCH",
                    "account projection differs: "
                    f"rebuilt={account.to_dict()} stored={state.account.to_dict()}",
                )
            if funding_state_envelope(funding) != funding_state_envelope(state.funding):
                raise FactError("REBUILD_MISMATCH", "funding projection differs")
            connection.commit()
            return BridgeState(account, funding, state.account_revision)

    def _obligation_rows(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> tuple[PendingObligation, ...]:
        return tuple(
            PendingObligation(
                row["obligation_id"],
                row["reservation_id"],
                row["security_code"],
                row["level1_industry_id"],
                row["bucket_id"],
                row["cycle_id"],
                row["quantity"],
                Decimal(row["price_bound"]),
                Decimal(row["fee_upper_bound"]),
                row["source_id"],
                row["status"],
            )
            for row in connection.execute(
                "SELECT * FROM bridge_obligations WHERE account_id=? AND mode=? "
                "ORDER BY obligation_id",
                (context.account_id, context.mode),
            )
        )

    def _operation_row(
        self, context: SyntheticContext, kind: str, identity: str
    ) -> sqlite3.Row | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM bridge_operations "
                "WHERE account_id=? AND mode=? AND kind=? AND identity=?",
                (context.account_id, context.mode, kind, identity),
            ).fetchone()
            return cast(sqlite3.Row | None, row)

    def _replay_buy_operation(
        self,
        context: SyntheticContext,
        kind: str,
        identity: str,
        expected_digest: str,
    ) -> BuyBridgeResult | None:
        row = self._operation_row(context, kind, identity)
        if row is None or row["status"] != "POSTED":
            return None
        if row["payload_digest"] != expected_digest:
            raise FactError("IDEMPOTENCY_CONFLICT", "operation payload differs")
        return self._buy_from_dict(json.loads(row["result_json"]))

    def _store_operation(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        kind: str,
        identity: str,
        result: object,
    ) -> None:
        if isinstance(result, BuyBridgeResult):
            payload = self._buy_dict(result)
        elif isinstance(result, SettlementBridgeResult):
            payload = self._settlement_dict(result)
        elif isinstance(result, LateFeeResult):
            payload = self._late_dict(result)
        else:
            raise AssertionError("unsupported operation result")
        connection.execute(
            "UPDATE bridge_operations SET status='POSTED',result_json=? "
            "WHERE account_id=? AND mode=? AND kind=? AND identity=?",
            (
                json.dumps(payload, sort_keys=True),
                context.account_id,
                context.mode,
                kind,
                identity,
            ),
        )

    def _append_journal(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        *,
        transition_id: str,
        kind: str,
        payload: object,
    ) -> None:
        sequence = connection.execute(
            "SELECT coalesce(max(sequence),0)+1 FROM bridge_journal WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()[0]
        encoded = json.dumps(payload, sort_keys=True)
        connection.execute(
            "INSERT INTO bridge_journal VALUES(?,?,?,?,?,?,?)",
            (
                context.account_id,
                context.mode,
                sequence,
                transition_id,
                kind,
                encoded,
                digest(payload),
            ),
        )

    def _outbox(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        event_id: str,
        payload: object,
    ) -> None:
        connection.execute(
            "INSERT INTO bridge_outbox(account_id,mode,report_id,payload_json) VALUES(?,?,?,?)",
            (
                context.account_id,
                context.mode,
                event_id,
                json.dumps(payload, sort_keys=True),
            ),
        )

    def _settlement_dict(self, result: SettlementBridgeResult) -> dict[str, object]:
        return {
            "settlement_id": result.settlement_id,
            "funding": funding_state_envelope(result.funding),
            "account_revision": result.account_revision,
            "receipt_id": result.receipt_id,
            "decision": {
                "accepted": result.decision.accepted,
                "replayed": result.decision.replayed,
                "reason_codes": [item.value for item in result.decision.reason_codes],
                "details_json": result.decision.details_json,
                "result_digest": result.decision.result_digest,
                "ledger_entries": [item.to_dict() for item in result.decision.ledger_entries],
            },
        }

    def _settlement_from_dict(self, raw: dict[str, Any]) -> SettlementBridgeResult:
        funding = restore_funding_envelope(raw["funding"])
        decision_raw = raw["decision"]
        ledger = tuple(
            FundingLedgerEntry(
                str(item["reference_id"]),
                str(item["reference_type"]),
                tuple(
                    FundingLedgerPosting(str(posting["account"]), Decimal(str(posting["amount"])))
                    for posting in item["postings"]
                ),
            )
            for item in decision_raw["ledger_entries"]
        )
        decision = FundingDecision(
            "settle_batch",
            bool(decision_raw["accepted"]),
            True,
            funding,
            tuple(ReasonCode(item) for item in decision_raw["reason_codes"]),
            ledger,
            str(decision_raw["details_json"]),
            str(decision_raw["result_digest"]),
        )
        return SettlementBridgeResult(
            str(raw["settlement_id"]),
            funding,
            decision,
            int(raw["account_revision"]),
            str(raw["receipt_id"]),
        )

    def _late_dict(self, result: LateFeeResult) -> dict[str, object]:
        return {
            "receipt_id": result.receipt_id,
            "status": result.status,
            "account": result.account.to_dict(),
            "funding": funding_state_envelope(result.funding),
            "account_revision": result.account_revision,
            "postings": [item.to_dict() for item in result.postings],
        }

    def _late_from_dict(self, raw: dict[str, Any]) -> LateFeeResult:
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
        return LateFeeResult(
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

    def _record_conflict(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        identity_kind: str,
        identity: str,
        old_digest: str,
        new_digest: str,
        evidence: str,
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO bridge_conflicts VALUES(?,?,?,?,?,?)",
            (
                context.account_id,
                context.mode,
                f"{identity_kind}:{identity}",
                old_digest,
                new_digest,
                evidence,
            ),
        )

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
        if account.digest != account_row["digest"]:
            raise FactError("STORE_PROJECTION_MISMATCH", "account digest differs")
        funding = restore_funding_envelope(json.loads(funding_row["envelope_json"]))
        if funding.cash_total != account.cash:
            raise FactError("STORE_PROJECTION_MISMATCH", "account/funding cash differs")
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
            "command_id": result.command_id,
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
