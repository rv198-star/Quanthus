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

from quanthus.capabilities.accounting import AccountProjection, Position, Posting
from quanthus.product_daily.application.funding_bridge import (
    BridgeState,
    BuyBridgeResult,
    CycleBaseline,
    CycleFillProfile,
    EntryBridgeResult,
    ExecutionReport,
    FundingBridgeService,
    PendingObligation,
    SyntheticContext,
    apply_confirmed_buy_transition,
    funding_state_envelope,
    restore_funding_envelope,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.contracts import ProcessedCommand
from quanthus.product_daily.funding import FundingState
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
                    PRAGMA user_version=2;
                    """
                )
            elif version != 2:
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

    def read(self, context: SyntheticContext) -> BridgeState:
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                state = self._read(connection, context)
                connection.commit()
                return state
            except Exception:
                connection.rollback()
                raise

    def obligations(self, context: SyntheticContext) -> tuple[PendingObligation, ...]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                values = self._obligation_rows(connection, context)
                connection.commit()
                return values
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
                            "INSERT INTO bridge_obligations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                c.account_id,
                                c.mode,
                                obligation.obligation_id,
                                obligation.reservation_id,
                                obligation.security_code,
                                obligation.level1_industry_id,
                                obligation.bucket_id,
                                obligation.quantity,
                                format(obligation.price_bound, "f"),
                                format(obligation.fee_upper_bound, "f"),
                                obligation.source_id,
                                obligation.status,
                                None,
                                None,
                            ),
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
                "SELECT count(*) FROM bridge_entry_receipts "
                "WHERE account_id=? AND mode=? AND accepted=1",
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
                "WHERE account_id=? AND mode=? AND accepted=1 ORDER BY rowid",
                (context.account_id, context.mode),
            ).fetchall()
            funding = (
                restore_funding_envelope(json.loads(entry_rows[-1]["funding_json"]))
                if entry_rows
                else restore_funding_envelope(json.loads(baseline["funding_json"]))
            )
            obligations = self._obligation_rows(connection, context)
            for row in connection.execute(
                "SELECT payload_json FROM bridge_events WHERE account_id=? AND mode=? "
                "ORDER BY event_sequence",
                (context.account_id, context.mode),
            ):
                event = json.loads(row["payload_json"])
                report = ExecutionReport(
                    event["report"]["report_id"],
                    event["report"]["security_code"],
                    event["report"]["side"],
                    event["report"]["quantity"],
                    Decimal(event["report"]["price"]),
                    Decimal(event["report"]["confirmed_fee"]),
                    datetime.fromisoformat(event["report"]["occurred_at"]),
                    event["report"]["source_id"],
                    event["report"]["source_sequence"],
                    event["report"]["intended_quantity"],
                    Decimal(event["report"]["intended_price_cap"]),
                )
                raw_profile = event["profile"]
                profile = CycleFillProfile(
                    raw_profile["profile_version"],
                    raw_profile["source_id"],
                    raw_profile["order_total_quantity"],
                    raw_profile["final_cumulative_fill_quantity"],
                    raw_profile["cancelled_quantity"],
                    Decimal(raw_profile["confirmed_fee"]),
                    raw_profile["terminal"],
                )
                matching = tuple(
                    item for item in obligations if item.security_code == report.security_code
                )
                if len(matching) != 1:
                    raise FactError("ENTRY_RESERVATION_MISMATCH", "rebuild obligation differs")
                obligation = PendingObligation(
                    matching[0].obligation_id,
                    matching[0].reservation_id,
                    matching[0].security_code,
                    matching[0].level1_industry_id,
                    matching[0].bucket_id,
                    matching[0].quantity,
                    matching[0].price_bound,
                    matching[0].fee_upper_bound,
                    matching[0].source_id,
                    "OPEN",
                )
                impact = apply_confirmed_buy_transition(
                    BridgeState(account, funding, account.revision),
                    report,
                    profile,
                    obligation,
                )
                account, funding = impact.account, impact.funding
            if account != state.account:
                raise FactError(
                    "REBUILD_MISMATCH",
                    "account projection differs: "
                    f"rebuilt={account.to_dict()} stored={state.account.to_dict()}",
                )
            if funding != state.funding:
                raise FactError("REBUILD_MISMATCH", "funding projection differs")
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
