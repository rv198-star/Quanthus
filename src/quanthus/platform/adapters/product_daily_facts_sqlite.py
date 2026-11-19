"""SQLite adapter for the isolated P12 synthetic execution-fact slice."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
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
from quanthus.capabilities.execution import (
    Correction,
    ExecutionReport,
    SyntheticContext,
    baseline_coverage_gap,
    validate_context,
)
from quanthus.product_daily.application.history import (
    AccountSnapshot,
    ConfigurationBinding,
    CurrentFact,
    FactHistory,
    LedgerEvent,
    PlanStatus,
    ReceivedCorrection,
    ReceivedReport,
)
from quanthus.product_daily.application.models import (
    Baseline,
    CorrectionResult,
    CurrentFactChain,
    Receipt,
    SubmissionResult,
    SyntheticRuntimeAuthorization,
)
from quanthus.product_daily.application.service import SyntheticFactService
from quanthus.shared.fact_contracts import FactError, digest


class SQLiteSyntheticFactsAdapter:
    """Two-transaction synthetic fact store with deterministic rebuild and CAS."""

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
            with connection:
                yield connection
        finally:
            connection.close()

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise FactError("INVALID_REPORT_SHAPE", "recorded clock must be aware")
        return value.isoformat()

    @staticmethod
    def _identity(context: SyntheticContext, kind: str, identity: str) -> str:
        return digest([context.account_id, context.mode, kind, identity])

    def _record_conflict(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        kind: str,
        identity: str,
        old_digest: str,
        new_digest: str,
        evidence: str,
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO conflicts VALUES(?,?,?,?,?,?)",
            (
                context.account_id,
                context.mode,
                self._identity(context, kind, identity),
                old_digest,
                new_digest,
                evidence,
            ),
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version not in (0, 1) or (version == 0 and tables):
                raise FactError(
                    "STORE_SCHEMA_MISMATCH",
                    "unversioned candidate store is retained; use a fresh synthetic store",
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS baselines(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, request_id TEXT NOT NULL,
                  cash TEXT NOT NULL, quantity INTEGER NOT NULL, security_code TEXT NOT NULL,
                  unsettled_quantity INTEGER NOT NULL, source_id TEXT NOT NULL, as_of TEXT NOT NULL,
                  coverage_id TEXT NOT NULL, payload_digest TEXT NOT NULL,
                  projection_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
                  PRIMARY KEY(account_id, mode));
                CREATE TABLE IF NOT EXISTS accounts(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL,
                  event_sequence INTEGER NOT NULL, cash TEXT NOT NULL, positions_json TEXT NOT NULL,
                  fact_count INTEGER NOT NULL, digest TEXT NOT NULL,
                  PRIMARY KEY(account_id, mode));
                CREATE TABLE IF NOT EXISTS inbox(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, report_id TEXT NOT NULL,
                  payload_digest TEXT NOT NULL, receipt_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL, received_at TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS correction_inbox(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, correction_id TEXT NOT NULL,
                  payload_digest TEXT NOT NULL, receipt_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  received_at TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT,
                  PRIMARY KEY(account_id,mode,correction_id));
                CREATE TABLE IF NOT EXISTS dependent_plans(
                  account_id TEXT NOT NULL,mode TEXT NOT NULL,plan_id TEXT NOT NULL,
                  based_on_revision INTEGER NOT NULL,status TEXT NOT NULL,
                  PRIMARY KEY(account_id,mode,plan_id));
                CREATE TABLE IF NOT EXISTS configuration_bindings(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, binding_digest TEXT NOT NULL,
                  binding_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
                  PRIMARY KEY(account_id, mode));
                PRAGMA user_version=1;
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
                    "SELECT * FROM baselines WHERE account_id=? AND mode=?",
                    (context.account_id, context.mode),
                ).fetchone()
                if existing is not None:
                    if existing["payload_digest"] != baseline.payload_digest:
                        self._record_conflict(
                            connection,
                            context,
                            "baseline",
                            baseline.request_id,
                            existing["payload_digest"],
                            baseline.payload_digest,
                            json.dumps(baseline.to_dict(), sort_keys=True),
                        )
                        connection.commit()
                        raise FactError(
                            "IDEMPOTENCY_CONFLICT",
                            "baseline already established with different input",
                        )
                    return self._projection_from_dict(json.loads(existing["projection_json"]))
                positions = (
                    ()
                    if baseline.quantity == 0
                    else (Position(baseline.security_code, baseline.quantity),)
                )
                projection = AccountProjection(0, 0, baseline.cash, positions, 0)
                connection.execute(
                    "INSERT INTO baselines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        baseline.payload_digest,
                        json.dumps(projection.to_dict(), sort_keys=True),
                        self._timestamp(),
                    ),
                )
                self._write_projection(connection, context, projection)
                connection.executemany(
                    "INSERT INTO dependent_plans VALUES(?,?,?,?,?)",
                    [
                        (context.account_id, context.mode, identity, 0, "READY")
                        for identity in baseline.dependent_plan_ids
                    ],
                )
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
                        return Receipt(row["receipt_id"], "DUPLICATE", row["payload_digest"])
                    evidence = json.dumps(report.to_dict(), sort_keys=True)
                    connection.execute(
                        "INSERT OR IGNORE INTO conflicts VALUES(?,?,?,?,?,?)",
                        (
                            context.account_id,
                            context.mode,
                            self._identity(context, "report", report.report_id),
                            row["payload_digest"],
                            report.payload_digest,
                            evidence,
                        ),
                    )
                    connection.commit()
                    return Receipt(
                        row["receipt_id"],
                        "IDEMPOTENCY_CONFLICT",
                        row["payload_digest"],
                        ("REPORT_ID_PAYLOAD_CONFLICT",),
                    )
                receipt_id = "receipt-" + self._identity(context, "report", report.report_id)
                connection.execute(
                    "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?,NULL)",
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
                    raise FactError("IDEMPOTENCY_CONFLICT", "receipt payload differs")
                if inbox["status"] == "POSTED":
                    stored = json.loads(inbox["result_json"])
                    connection.commit()
                    return SubmissionResult(
                        receipt.receipt_id,
                        str(stored.get("status", "POSTED")),
                        self._projection_from_dict(stored["projection"]),
                        tuple(stored.get("deviations", ())),
                        affected_plans=tuple(stored.get("affected_plans", ())),
                    )
                duplicate = connection.execute(
                    "SELECT report_receipt_id FROM source_facts "
                    "WHERE account_id=? AND mode=? AND source_fact_id=?",
                    (context.account_id, context.mode, report.source_fact_id),
                ).fetchone()
                if duplicate is not None and duplicate[0] != receipt.receipt_id:
                    raise FactError("SOURCE_FACT_DUPLICATE", "source fact already posted")
                baseline_row = connection.execute(
                    "SELECT as_of FROM baselines WHERE account_id=? AND mode=?",
                    (context.account_id, context.mode),
                ).fetchone()
                gap = baseline_coverage_gap(report, datetime.fromisoformat(baseline_row["as_of"]))
                if gap is not None:
                    raise FactError(gap, "fact predates the takeover snapshot without coverage")
                current = self._projection(connection, context)
                updated, postings = apply_buy_delta(
                    current,
                    security_code=report.security_code,
                    quantity=report.quantity,
                    price=report.price,
                    fee=report.confirmed_fee,
                )
                affected = self._plans(connection, context)
                event_id = "fill:" + self._identity(context, "report", report.report_id)
                event_json = json.dumps(
                    {
                        "kind": "fill",
                        "fact_id": report.report_id,
                        "receipt_id": receipt.receipt_id,
                        "affected_plans": list(affected),
                        "security_code": report.security_code,
                        "quantity": report.quantity,
                        "price": format(report.price, "f"),
                        "confirmed_fee": format(report.confirmed_fee, "f"),
                        "occurred_at": report.occurred_at.isoformat(),
                        "received_at": inbox["received_at"],
                        "recorded_at": self._timestamp(),
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
                self._outbox(
                    connection,
                    context,
                    event_id,
                    {
                        "reason": "FACT_POSTED",
                        "action": "NEEDS_REASSESSMENT",
                        "account_revision": updated.revision,
                        "affected_plans": list(affected),
                    },
                )
                self._mark_plans(connection, context, affected)
                result = SubmissionResult(
                    receipt.receipt_id,
                    "POSTED",
                    updated,
                    report.deviations,
                    affected_plans=affected,
                )
                self._store_submission(connection, inbox, result)
                connection.commit()
                return result
            except FactError as exc:
                connection.rollback()
                if exc.reason_code not in {
                    "SOURCE_FACT_DUPLICATE",
                    "INSUFFICIENT_CASH",
                    "BASELINE_COVERAGE_UNKNOWN",
                }:
                    raise
                with self._connect() as pending:
                    pending.execute("BEGIN IMMEDIATE")
                    current = self._projection(pending, context)
                    result = SubmissionResult(
                        receipt.receipt_id,
                        "NEEDS_RECONCILIATION",
                        current,
                        report.deviations,
                        (exc.reason_code,),
                    )
                    # Keep the reason with the received report so records show why it waits.
                    pending.execute(
                        "UPDATE inbox SET status='NEEDS_RECONCILIATION', result_json=? "
                        "WHERE account_id=? AND mode=? AND report_id=?",
                        (
                            json.dumps(self._submission_dict(result), sort_keys=True),
                            context.account_id,
                            context.mode,
                            report.report_id,
                        ),
                    )
                    pending.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _receive_correction(self, context: SyntheticContext, correction: Correction) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_baseline(connection, context)
            row = connection.execute(
                "SELECT payload_digest FROM correction_inbox WHERE account_id=? AND mode=? AND "
                "correction_id=?",
                (context.account_id, context.mode, correction.correction_id),
            ).fetchone()
            if row is not None:
                if row["payload_digest"] != correction.payload_digest:
                    self._record_conflict(
                        connection,
                        context,
                        "correction",
                        correction.correction_id,
                        row["payload_digest"],
                        correction.payload_digest,
                        json.dumps(
                            {
                                "fact_id": correction.fact_id,
                                "quantity": correction.quantity,
                                "price": str(correction.price),
                                "reason": correction.reason,
                            }
                        ),
                    )
                    connection.commit()
                    raise FactError("IDEMPOTENCY_CONFLICT", "correction changed")
                return
            receipt_id = "receipt-" + self._identity(
                context, "correction", correction.correction_id
            )
            payload = {
                "correction_id": correction.correction_id,
                "fact_id": correction.fact_id,
                "expected_fact_revision": correction.expected_fact_revision,
                "quantity": correction.quantity,
                "price": str(correction.price),
                "reason": correction.reason,
            }
            connection.execute(
                "INSERT INTO correction_inbox VALUES(?,?,?,?,?,?,?,?,NULL)",
                (
                    context.account_id,
                    context.mode,
                    correction.correction_id,
                    correction.payload_digest,
                    receipt_id,
                    json.dumps(payload, sort_keys=True),
                    self._timestamp(),
                    "RECEIVED",
                ),
            )
            connection.commit()

    def correct_fact(self, context: SyntheticContext, correction: Correction) -> CorrectionResult:
        self._receive_correction(context, correction)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prior = connection.execute(
                    "SELECT * FROM corrections WHERE account_id=? AND mode=? AND correction_id=?",
                    (context.account_id, context.mode, correction.correction_id),
                ).fetchone()
                if prior is not None:
                    if prior["payload_digest"] != correction.payload_digest:
                        raise FactError("IDEMPOTENCY_CONFLICT", "correction changed")
                    stored = json.loads(prior["result_json"])
                    return CorrectionResult(
                        prior["receipt_id"],
                        str(stored.get("status", "POSTED")),
                        self._projection_from_dict(stored["projection"]),
                        Decimal(stored["cash_delta"]),
                        stored["quantity_delta"],
                        affected_plans=tuple(stored.get("affected_plans", ())),
                    )
                fact = connection.execute(
                    "SELECT * FROM facts WHERE account_id=? AND mode=? AND fact_id=?",
                    (context.account_id, context.mode, correction.fact_id),
                ).fetchone()
                if fact is None:
                    raise FactError("FACT_NOT_FOUND", "fact is unavailable")
                if fact["fact_revision"] != correction.expected_fact_revision:
                    current = self._projection(connection, context)
                    chain = self._current_chain(connection, context, fact)
                    connection.execute(
                        "UPDATE correction_inbox SET status='NEEDS_RECONCILIATION',error_code=? "
                        "WHERE account_id=? AND mode=? AND correction_id=?",
                        (
                            "STALE_FACT_REVISION",
                            context.account_id,
                            context.mode,
                            correction.correction_id,
                        ),
                    )
                    connection.commit()
                    return CorrectionResult(
                        "receipt-"
                        + self._identity(context, "correction", correction.correction_id),
                        "NEEDS_RECONCILIATION",
                        current,
                        Decimal("0.00"),
                        0,
                        conflicts=("STALE_FACT_REVISION",),
                        current_chain=chain,
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
                        "receipt-"
                        + self._identity(context, "correction", correction.correction_id),
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
                    affected = self._plans(connection, context)
                    event_id = "correction:" + self._identity(
                        context, "correction", correction.correction_id
                    )
                    event_json = json.dumps(
                        {
                            "kind": "correction",
                            "correction_id": correction.correction_id,
                            "affected_plans": list(affected),
                            "fact_id": correction.fact_id,
                            "security_code": fact["security_code"],
                            "quantity_delta": quantity_delta,
                            "cash_delta": format(cash_delta, "f"),
                            "principal_delta": format(principal_delta, "f"),
                            "old_quantity": old_quantity,
                            "old_price": str(old_price),
                            "quantity": correction.quantity,
                            "price": str(correction.price),
                            "expected_fact_revision": correction.expected_fact_revision,
                            "fact_revision": correction.expected_fact_revision + 1,
                            "reason": correction.reason,
                            "recorded_at": self._timestamp(),
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
                        self._fault_hook("after_correction_event_before_projection")
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
                    self._mark_plans(connection, context, affected)
                    self._outbox(
                        connection,
                        context,
                        event_id,
                        {"reason": "FACT_CORRECTED", "fact_id": correction.fact_id},
                    )
                    result = CorrectionResult(
                        "receipt-"
                        + self._identity(context, "correction", correction.correction_id),
                        "POSTED",
                        updated,
                        cash_delta,
                        quantity_delta,
                        affected_plans=affected,
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
                connection.execute(
                    "UPDATE correction_inbox SET status=?,error_code=NULL WHERE account_id=? "
                    "AND mode=? AND correction_id=?",
                    (result.status, context.account_id, context.mode, correction.correction_id),
                )
                connection.commit()
                return result
            except FactError as exc:
                connection.rollback()
                with self._connect() as pending:
                    pending.execute("BEGIN IMMEDIATE")
                    pending.execute(
                        "UPDATE correction_inbox SET status='NEEDS_RECONCILIATION',error_code=? "
                        "WHERE account_id=? AND mode=? AND correction_id=?",
                        (
                            exc.reason_code,
                            context.account_id,
                            context.mode,
                            correction.correction_id,
                        ),
                    )
                    pending.commit()
                raise
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
                raise FactError("BASELINE_NOT_FOUND", "baseline is unavailable")
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

    def history(self, context: SyntheticContext) -> FactHistory:
        """Read-only audit view assembled from the same committed rows the ledger uses."""
        with self._connect() as connection:
            return self._history(connection, context)

    def snapshot(self, context: SyntheticContext) -> AccountSnapshot:
        """Projection and history read inside one SQLite read transaction.

        The shared lock held by the deferred transaction makes a concurrent writer wait for
        commit, so one snapshot shows either the complete old state or the complete new one.
        """
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                projection = self._projection(connection, context)
                if self._fault_hook is not None:
                    self._fault_hook("snapshot_between_projection_and_history")
                history = self._history(connection, context)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return AccountSnapshot(projection, history)

    def bind_configuration(
        self, context: SyntheticContext, binding_digest: str, binding: dict[str, object]
    ) -> ConfigurationBinding:
        """Record the first configuration bound to this account; return the stored one."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_baseline(connection, context)
                connection.execute(
                    "INSERT OR IGNORE INTO configuration_bindings VALUES(?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        binding_digest,
                        json.dumps(binding, sort_keys=True),
                        self._timestamp(),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM configuration_bindings WHERE account_id=? AND mode=?",
                    (context.account_id, context.mode),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return ConfigurationBinding(
                row["binding_digest"], json.loads(row["binding_json"]), row["recorded_at"]
            )

    def configuration_binding(self, context: SyntheticContext) -> ConfigurationBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM configuration_bindings WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
        if row is None:
            return None
        return ConfigurationBinding(
            row["binding_digest"], json.loads(row["binding_json"]), row["recorded_at"]
        )

    def _history(self, connection: sqlite3.Connection, context: SyntheticContext) -> FactHistory:
        baseline = connection.execute(
            "SELECT * FROM baselines WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if baseline is None:
            raise FactError("BASELINE_NOT_FOUND", "baseline is unavailable")
        reports = []
        for row in connection.execute(
            "SELECT * FROM inbox WHERE account_id=? AND mode=? ORDER BY rowid",
            (context.account_id, context.mode),
        ):
            raw = json.loads(row["payload_json"])
            stored = json.loads(row["result_json"]) if row["result_json"] else {}
            report = ExecutionReport(
                raw["report_id"],
                raw["security_code"],
                raw["side"],
                int(raw["quantity"]),
                Decimal(raw["price"]),
                Decimal(raw["confirmed_fee"]),
                datetime.fromisoformat(raw["occurred_at"]),
                raw["source_id"],
                raw["source_sequence"],
                int(raw["intended_quantity"]),
                Decimal(raw["intended_price_cap"]),
                raw.get("suggestion_id"),
            )
            reports.append(
                ReceivedReport(
                    report_id=row["report_id"],
                    receipt_id=row["receipt_id"],
                    status=row["status"],
                    received_at=row["received_at"],
                    security_code=report.security_code,
                    side=report.side,
                    quantity=report.quantity,
                    price=report.price,
                    confirmed_fee=report.confirmed_fee,
                    occurred_at=raw["occurred_at"],
                    intended_quantity=report.intended_quantity,
                    intended_price_cap=report.intended_price_cap,
                    suggestion_id=report.suggestion_id,
                    deviations=report.deviations,
                    conflicts=tuple(stored.get("conflicts", ())),
                )
            )
        facts = tuple(
            CurrentFact(
                row["fact_id"],
                row["security_code"],
                int(row["quantity"]),
                Decimal(row["price"]),
                Decimal(row["confirmed_fee"]),
                int(row["fact_revision"]),
            )
            for row in connection.execute(
                "SELECT * FROM facts WHERE account_id=? AND mode=? ORDER BY rowid",
                (context.account_id, context.mode),
            )
        )
        corrections = []
        for row in connection.execute(
            "SELECT * FROM correction_inbox WHERE account_id=? AND mode=? ORDER BY rowid",
            (context.account_id, context.mode),
        ):
            raw = json.loads(row["payload_json"])
            corrections.append(
                ReceivedCorrection(
                    correction_id=row["correction_id"],
                    receipt_id=row["receipt_id"],
                    fact_id=raw["fact_id"],
                    expected_fact_revision=int(raw["expected_fact_revision"]),
                    quantity=int(raw["quantity"]),
                    price=Decimal(raw["price"]),
                    reason=raw["reason"],
                    status=row["status"],
                    error_code=row["error_code"],
                    received_at=row["received_at"],
                )
            )
        events = []
        for row in connection.execute(
            "SELECT * FROM events WHERE account_id=? AND mode=? ORDER BY event_sequence",
            (context.account_id, context.mode),
        ):
            event = json.loads(row["event_json"])
            lines = {
                item["account"]: Decimal(item["amount"])
                for item in connection.execute(
                    "SELECT account, amount FROM postings WHERE event_id=? ORDER BY line_no",
                    (row["event_id"],),
                )
            }
            events.append(
                LedgerEvent(
                    event_id=row["event_id"],
                    event_sequence=int(row["event_sequence"]),
                    revision=int(row["revision"]),
                    kind=event["kind"],
                    fact_id=event["fact_id"],
                    cash_delta=lines.get("ASSET_CASH", Decimal("0.00")),
                    position_delta=lines.get("ASSET_POSITION", Decimal("0.00")),
                    quantity_delta=int(
                        event["quantity"]
                        if event["kind"] == "fill"
                        else event["quantity_delta"]
                    ),
                    recorded_at=event["recorded_at"],
                )
            )
        plans = tuple(
            PlanStatus(row["plan_id"], row["status"], int(row["based_on_revision"]))
            for row in connection.execute(
                "SELECT * FROM dependent_plans WHERE account_id=? AND mode=? ORDER BY plan_id",
                (context.account_id, context.mode),
            )
        )
        pending = connection.execute(
            "SELECT count(*) FROM outbox "
            "WHERE account_id=? AND mode=? AND delivered_at IS NULL",
            (context.account_id, context.mode),
        ).fetchone()[0]
        return FactHistory(
            baseline_request_id=baseline["request_id"],
            baseline_cash=Decimal(baseline["cash"]),
            baseline_quantity=int(baseline["quantity"]),
            baseline_security_code=baseline["security_code"],
            baseline_as_of=baseline["as_of"],
            baseline_source_id=baseline["source_id"],
            baseline_coverage_id=baseline["coverage_id"],
            reports=tuple(reports),
            facts=facts,
            corrections=tuple(corrections),
            events=tuple(events),
            plans=plans,
            outbox_pending=int(pending),
        )

    def _require_baseline(self, connection: sqlite3.Connection, context: SyntheticContext) -> None:
        if (
            connection.execute(
                "SELECT 1 FROM baselines WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
            is None
        ):
            raise FactError("BASELINE_NOT_FOUND", "baseline is unavailable")

    def _projection(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> AccountProjection:
        row = connection.execute(
            "SELECT * FROM accounts WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if row is None:
            raise FactError("BASELINE_NOT_FOUND", "baseline is unavailable")
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

    def _current_chain(
        self, connection: sqlite3.Connection, context: SyntheticContext, fact: sqlite3.Row
    ) -> CurrentFactChain:
        ids = []
        for row in connection.execute(
            "SELECT correction_id,payload_json FROM correction_inbox WHERE account_id=? AND mode=? "
            "AND status IN ('POSTED','NO_CHANGE') ORDER BY rowid",
            (context.account_id, context.mode),
        ):
            if json.loads(row["payload_json"])["fact_id"] == fact["fact_id"]:
                ids.append(row["correction_id"])
        return CurrentFactChain(
            fact["fact_id"],
            fact["security_code"],
            fact["fact_revision"],
            fact["quantity"],
            Decimal(fact["price"]),
            Decimal(fact["confirmed_fee"]),
            tuple(ids),
        )

    def _plans(self, connection: sqlite3.Connection, context: SyntheticContext) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT plan_id FROM dependent_plans WHERE account_id=? AND mode=? AND "
                "status='READY' ORDER BY plan_id",
                (context.account_id, context.mode),
            )
        )

    def _mark_plans(
        self, connection: sqlite3.Connection, context: SyntheticContext, identities: tuple[str, ...]
    ) -> None:
        connection.executemany(
            "UPDATE dependent_plans SET status='NEEDS_REASSESSMENT' "
            "WHERE account_id=? AND mode=? AND plan_id=?",
            [(context.account_id, context.mode, identity) for identity in identities],
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
            "status": result.status,
            "conflicts": list(result.conflicts),
            "projection": result.projection.to_dict(),
            "deviations": list(result.deviations),
            "affected_plans": list(result.affected_plans),
        }

    def _correction_dict(self, result: CorrectionResult) -> dict[str, object]:
        return {
            "status": result.status,
            "projection": result.projection.to_dict(),
            "cash_delta": format(result.cash_delta, "f"),
            "quantity_delta": result.quantity_delta,
            "affected_plans": list(result.affected_plans),
        }


def build_synthetic_fact_service(
    path: Path | str,
    *,
    context: SyntheticContext,
    authorization: SyntheticRuntimeAuthorization,
    fault_hook: Callable[[str], None] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SyntheticFactService:
    authorization.require(context)
    trusted_context = validate_context(context)
    return SyntheticFactService(
        SQLiteSyntheticFactsAdapter(path, fault_hook=fault_hook, clock=clock),
        trusted_context,
        authorization,
    )
