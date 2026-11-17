"""SQLite adapter for the isolated P12 synthetic execution-fact slice."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.capabilities.accounting import (
    AccountProjection,
    Position,
    Posting,
    apply_buy_delta,
    apply_correction_delta,
)
from quanthus.capabilities.execution import Correction, ExecutionReport, SyntheticContext
from quanthus.product_daily.application.models import (
    Baseline,
    CorrectionResult,
    Receipt,
    SubmissionResult,
)
from quanthus.product_daily.application.service import SyntheticFactService
from quanthus.product_daily.contracts import ProductDailyError


class SQLiteSyntheticFactsAdapter:
    """Two-transaction synthetic fact store with deterministic rebuild and CAS."""

    def __init__(
        self,
        path: Path | str,
        *,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._path = str(path)
        self._fault_hook = fault_hook
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS baselines(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, request_id TEXT NOT NULL,
                  cash TEXT NOT NULL, quantity INTEGER NOT NULL, security_code TEXT NOT NULL,
                  unsettled_quantity INTEGER NOT NULL, source_id TEXT NOT NULL, as_of TEXT NOT NULL,
                  coverage_id TEXT NOT NULL, PRIMARY KEY(account_id, mode));
                CREATE TABLE IF NOT EXISTS accounts(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL,
                  event_sequence INTEGER NOT NULL, cash TEXT NOT NULL, positions_json TEXT NOT NULL,
                  fact_count INTEGER NOT NULL, digest TEXT NOT NULL,
                  PRIMARY KEY(account_id, mode));
                CREATE TABLE IF NOT EXISTS inbox(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, report_id TEXT NOT NULL,
                  payload_digest TEXT NOT NULL, receipt_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  status TEXT NOT NULL, result_json TEXT, PRIMARY KEY(account_id, mode, report_id));
                CREATE TABLE IF NOT EXISTS conflicts(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, identity TEXT NOT NULL,
                  original_digest TEXT NOT NULL, conflict_digest TEXT NOT NULL,
                  evidence_json TEXT NOT NULL,
                  PRIMARY KEY(account_id, mode, identity, conflict_digest));
                CREATE TABLE IF NOT EXISTS events(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, event_id TEXT PRIMARY KEY,
                  event_sequence INTEGER NOT NULL, revision INTEGER NOT NULL,
                  event_json TEXT NOT NULL, UNIQUE(account_id, mode, event_sequence));
                CREATE TABLE IF NOT EXISTS postings(
                  event_id TEXT NOT NULL, line_no INTEGER NOT NULL, account TEXT NOT NULL,
                  amount TEXT NOT NULL, PRIMARY KEY(event_id, line_no),
                  FOREIGN KEY(event_id) REFERENCES events(event_id));
                CREATE TABLE IF NOT EXISTS facts(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, fact_id TEXT NOT NULL,
                  security_code TEXT NOT NULL, quantity INTEGER NOT NULL, price TEXT NOT NULL,
                  confirmed_fee TEXT NOT NULL, fact_revision INTEGER NOT NULL,
                  PRIMARY KEY(account_id, mode, fact_id));
                CREATE TABLE IF NOT EXISTS source_facts(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, source_fact_id TEXT NOT NULL,
                  report_receipt_id TEXT NOT NULL, PRIMARY KEY(account_id, mode, source_fact_id));
                CREATE TABLE IF NOT EXISTS corrections(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, correction_id TEXT NOT NULL,
                  payload_digest TEXT NOT NULL, receipt_id TEXT NOT NULL, result_json TEXT NOT NULL,
                  PRIMARY KEY(account_id, mode, correction_id));
                CREATE TABLE IF NOT EXISTS outbox(
                  outbox_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
                  mode TEXT NOT NULL, event_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                  delivered_at TEXT);
                """
            )

    def establish_baseline(self, baseline: Baseline) -> AccountProjection:
        context = baseline.context
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM accounts WHERE account_id=? AND mode=?",
                    (context.account_id, context.mode),
                ).fetchone()
                if existing is not None:
                    return self._projection_from_row(existing)
                connection.execute(
                    "INSERT INTO baselines VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        baseline.request_id,
                        format(baseline.cash, "f"),
                        baseline.quantity,
                        baseline.security_code,
                        baseline.unsettled_quantity,
                        context.source_id,
                        context.as_of.isoformat(),
                        context.coverage_id,
                    ),
                )
                positions = (
                    ()
                    if baseline.quantity == 0
                    else (Position(baseline.security_code, baseline.quantity),)
                )
                projection = AccountProjection(0, 0, baseline.cash, positions, 0)
                self._write_projection(connection, context, projection)
                connection.commit()
                return projection
            except Exception:
                connection.rollback()
                raise

    def receive_report(self, context: SyntheticContext, report: ExecutionReport) -> Receipt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_baseline(connection, context)
                row = connection.execute(
                    "SELECT * FROM inbox WHERE account_id=? AND mode=? AND report_id=?",
                    (context.account_id, context.mode, report.report_id),
                ).fetchone()
                if row is not None:
                    if row["payload_digest"] == report.payload_digest:
                        connection.commit()
                        return Receipt(row["receipt_id"], "DUPLICATE", row["status"])
                    evidence = json.dumps(report.to_dict(), sort_keys=True)
                    connection.execute(
                        "INSERT OR IGNORE INTO conflicts VALUES(?,?,?,?,?,?)",
                        (
                            context.account_id,
                            context.mode,
                            report.report_id,
                            row["payload_digest"],
                            report.payload_digest,
                            evidence,
                        ),
                    )
                    connection.commit()
                    return Receipt(
                        row["receipt_id"],
                        "IDEMPOTENCY_CONFLICT",
                        row["status"],
                        ("REPORT_ID_PAYLOAD_CONFLICT",),
                    )
                receipt_id = f"receipt-{report.report_id}"
                connection.execute(
                    "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,NULL)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        report.payload_digest,
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                        "RECEIVED",
                    ),
                )
                connection.commit()
                return Receipt(receipt_id, "RECEIVED")
            except Exception:
                connection.rollback()
                raise

    def post_report(
        self, context: SyntheticContext, receipt: Receipt, report: ExecutionReport
    ) -> SubmissionResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                inbox = connection.execute(
                    "SELECT * FROM inbox WHERE account_id=? AND mode=? AND receipt_id=?",
                    (context.account_id, context.mode, receipt.receipt_id),
                ).fetchone()
                if inbox is None or inbox["payload_digest"] != report.payload_digest:
                    raise ProductDailyError("IDEMPOTENCY_CONFLICT", "receipt payload differs")
                if inbox["status"] == "POSTED":
                    stored = json.loads(inbox["result_json"])
                    connection.commit()
                    return SubmissionResult(
                        receipt.receipt_id,
                        "DUPLICATE_POSTED",
                        self._projection_from_dict(stored["projection"]),
                        tuple(stored.get("deviations", ())),
                    )
                duplicate = connection.execute(
                    "SELECT report_receipt_id FROM source_facts "
                    "WHERE account_id=? AND mode=? AND source_fact_id=?",
                    (context.account_id, context.mode, report.source_fact_id),
                ).fetchone()
                if duplicate is not None and duplicate[0] != receipt.receipt_id:
                    raise ProductDailyError("SOURCE_FACT_DUPLICATE", "source fact already posted")
                current = self._projection(connection, context)
                updated, postings = apply_buy_delta(
                    current,
                    security_code=report.security_code,
                    quantity=report.quantity,
                    price=report.price,
                    fee=report.confirmed_fee,
                )
                event_id = f"fill:{report.report_id}"
                event_json = json.dumps(
                    {
                        "kind": "fill",
                        "fact_id": report.report_id,
                        "security_code": report.security_code,
                        "quantity": report.quantity,
                        "price": format(report.price, "f"),
                        "confirmed_fee": format(report.confirmed_fee, "f"),
                    },
                    sort_keys=True,
                )
                connection.execute(
                    "INSERT INTO events VALUES(?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        event_id,
                        updated.event_sequence,
                        updated.revision,
                        event_json,
                    ),
                )
                self._insert_postings(connection, event_id, postings)
                if self._fault_hook is not None:
                    self._fault_hook("after_fact_event_before_projection")
                self._write_projection(connection, context, updated)
                connection.execute(
                    "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        report.security_code,
                        report.quantity,
                        format(report.price, "f"),
                        format(report.confirmed_fee, "f"),
                        1,
                    ),
                )
                connection.execute(
                    "INSERT INTO source_facts VALUES(?,?,?,?)",
                    (context.account_id, context.mode, report.source_fact_id, receipt.receipt_id),
                )
                self._outbox(connection, context, event_id, {"reason": "FACT_POSTED"})
                result = SubmissionResult(receipt.receipt_id, "POSTED", updated, report.deviations)
                self._store_submission(connection, inbox, result)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def correct_fact(self, context: SyntheticContext, correction: Correction) -> CorrectionResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prior = connection.execute(
                    "SELECT * FROM corrections WHERE account_id=? AND mode=? AND correction_id=?",
                    (context.account_id, context.mode, correction.correction_id),
                ).fetchone()
                if prior is not None:
                    if prior["payload_digest"] != correction.payload_digest:
                        raise ProductDailyError("IDEMPOTENCY_CONFLICT", "correction changed")
                    stored = json.loads(prior["result_json"])
                    return CorrectionResult(
                        prior["receipt_id"],
                        "DUPLICATE",
                        self._projection_from_dict(stored["projection"]),
                        Decimal(stored["cash_delta"]),
                        stored["quantity_delta"],
                    )
                fact = connection.execute(
                    "SELECT * FROM facts WHERE account_id=? AND mode=? AND fact_id=?",
                    (context.account_id, context.mode, correction.fact_id),
                ).fetchone()
                if fact is None:
                    raise ProductDailyError("FACT_NOT_FOUND", "fact is unavailable")
                if fact["fact_revision"] != correction.expected_fact_revision:
                    raise ProductDailyError(
                        "STALE_FACT_REVISION", f"current:{fact['fact_revision']}"
                    )
                old_quantity = int(fact["quantity"])
                old_price = Decimal(fact["price"])
                quantity_delta = correction.quantity - old_quantity
                principal_delta = (correction.quantity * correction.price) - (
                    old_quantity * old_price
                )
                cash_delta = -principal_delta
                current = self._projection(connection, context)
                if quantity_delta == 0 and cash_delta == 0:
                    result = CorrectionResult(
                        f"receipt-{correction.correction_id}",
                        "NO_CHANGE",
                        current,
                        Decimal("0"),
                        0,
                    )
                else:
                    updated, postings = apply_correction_delta(
                        current,
                        security_code=fact["security_code"],
                        quantity_delta=quantity_delta,
                        cash_delta=cash_delta,
                        principal_delta=principal_delta,
                    )
                    event_id = f"correction:{correction.correction_id}"
                    event_json = json.dumps(
                        {
                            "kind": "correction",
                            "fact_id": correction.fact_id,
                            "security_code": fact["security_code"],
                            "quantity_delta": quantity_delta,
                            "cash_delta": format(cash_delta, "f"),
                            "principal_delta": format(principal_delta, "f"),
                        },
                        sort_keys=True,
                    )
                    connection.execute(
                        "INSERT INTO events VALUES(?,?,?,?,?,?)",
                        (
                            context.account_id,
                            context.mode,
                            event_id,
                            updated.event_sequence,
                            updated.revision,
                            event_json,
                        ),
                    )
                    self._insert_postings(connection, event_id, postings)
                    self._write_projection(connection, context, updated)
                    connection.execute(
                        "UPDATE facts SET quantity=?, price=?, fact_revision=fact_revision+1 "
                        "WHERE account_id=? AND mode=? AND fact_id=?",
                        (
                            correction.quantity,
                            format(correction.price, "f"),
                            context.account_id,
                            context.mode,
                            correction.fact_id,
                        ),
                    )
                    self._outbox(
                        connection,
                        context,
                        event_id,
                        {"reason": "FACT_CORRECTED", "fact_id": correction.fact_id},
                    )
                    result = CorrectionResult(
                        f"receipt-{correction.correction_id}",
                        "POSTED",
                        updated,
                        cash_delta,
                        quantity_delta,
                    )
                connection.execute(
                    "INSERT INTO corrections VALUES(?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        correction.correction_id,
                        correction.payload_digest,
                        result.receipt_id,
                        json.dumps(self._correction_dict(result), sort_keys=True),
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def projection(self, context: SyntheticContext) -> AccountProjection:
        with self._connect() as connection:
            return self._projection(connection, context)

    def rebuild(self, context: SyntheticContext) -> AccountProjection:
        with self._connect() as connection:
            baseline = connection.execute(
                "SELECT * FROM baselines WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
            if baseline is None:
                raise ProductDailyError("BASELINE_NOT_FOUND", "baseline is unavailable")
            positions = (
                ()
                if baseline["quantity"] == 0
                else (Position(baseline["security_code"], baseline["quantity"]),)
            )
            projection = AccountProjection(0, 0, Decimal(baseline["cash"]), positions, 0)
            for row in connection.execute(
                "SELECT event_json FROM events "
                "WHERE account_id=? AND mode=? ORDER BY event_sequence",
                (context.account_id, context.mode),
            ):
                event = json.loads(row["event_json"])
                if event["kind"] == "fill":
                    projection, _ = apply_buy_delta(
                        projection,
                        security_code=event["security_code"],
                        quantity=event["quantity"],
                        price=Decimal(event["price"]),
                        fee=Decimal(event["confirmed_fee"]),
                    )
                else:
                    projection, _ = apply_correction_delta(
                        projection,
                        security_code=event["security_code"],
                        quantity_delta=event["quantity_delta"],
                        cash_delta=Decimal(event["cash_delta"]),
                        principal_delta=Decimal(event["principal_delta"]),
                    )
            return projection

    def _require_baseline(self, connection: sqlite3.Connection, context: SyntheticContext) -> None:
        if (
            connection.execute(
                "SELECT 1 FROM baselines WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
            is None
        ):
            raise ProductDailyError("BASELINE_NOT_FOUND", "baseline is unavailable")

    def _projection(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> AccountProjection:
        row = connection.execute(
            "SELECT * FROM accounts WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if row is None:
            raise ProductDailyError("BASELINE_NOT_FOUND", "baseline is unavailable")
        return self._projection_from_row(row)

    def _projection_from_row(self, row: sqlite3.Row) -> AccountProjection:
        raw = json.loads(row["positions_json"])
        return AccountProjection(
            row["revision"],
            row["event_sequence"],
            Decimal(row["cash"]),
            tuple(Position(item["security_code"], item["quantity"]) for item in raw),
            row["fact_count"],
        )

    def _projection_from_dict(self, raw: dict[str, Any]) -> AccountProjection:
        positions = tuple(
            Position(str(item["security_code"]), int(item["quantity"])) for item in raw["positions"]
        )
        return AccountProjection(
            int(raw["revision"]),
            int(raw["event_sequence"]),
            Decimal(str(raw["cash"])),
            positions,
            int(raw["fact_count"]),
        )

    def _write_projection(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        projection: AccountProjection,
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?,?,?,?)",
            (
                context.account_id,
                context.mode,
                projection.revision,
                projection.event_sequence,
                format(projection.cash, "f"),
                json.dumps([item.to_dict() for item in projection.positions], sort_keys=True),
                projection.fact_count,
                projection.digest,
            ),
        )

    def _insert_postings(
        self, connection: sqlite3.Connection, event_id: str, postings: tuple[Posting, ...]
    ) -> None:
        for index, posting in enumerate(postings, start=1):
            connection.execute(
                "INSERT INTO postings VALUES(?,?,?,?)",
                (event_id, index, posting.account, format(posting.amount, "f")),
            )

    def _outbox(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        event_id: str,
        payload: object,
    ) -> None:
        connection.execute(
            "INSERT INTO outbox(account_id,mode,event_id,payload_json) VALUES(?,?,?,?)",
            (context.account_id, context.mode, event_id, json.dumps(payload, sort_keys=True)),
        )

    def _store_submission(
        self, connection: sqlite3.Connection, inbox: sqlite3.Row, result: SubmissionResult
    ) -> None:
        connection.execute(
            "UPDATE inbox SET status=?, result_json=? "
            "WHERE account_id=? AND mode=? AND report_id=?",
            (
                "POSTED",
                json.dumps(self._submission_dict(result), sort_keys=True),
                inbox["account_id"],
                inbox["mode"],
                inbox["report_id"],
            ),
        )

    def _submission_dict(self, result: SubmissionResult) -> dict[str, object]:
        return {
            "projection": result.projection.to_dict(),
            "deviations": list(result.deviations),
        }

    def _correction_dict(self, result: CorrectionResult) -> dict[str, object]:
        return {
            "projection": result.projection.to_dict(),
            "cash_delta": format(result.cash_delta, "f"),
            "quantity_delta": result.quantity_delta,
        }


def build_synthetic_fact_service(
    path: Path | str,
    *,
    fault_hook: Callable[[str], None] | None = None,
) -> SyntheticFactService:
    return SyntheticFactService(SQLiteSyntheticFactsAdapter(path, fault_hook=fault_hook))
