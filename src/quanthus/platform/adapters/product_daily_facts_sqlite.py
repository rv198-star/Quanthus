"""SQLite adapter for the isolated P12 synthetic execution-fact slice."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
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
    apply_sell_delta,
)
from quanthus.capabilities.execution import (
    FINAL_FILL_GAP,
    Correction,
    ExecutionReport,
    FeeAssessment,
    OrderObservation,
    SyntheticContext,
    baseline_coverage_gap,
    coverage_gap_at,
    effective_order_state,
    validate_context,
)
from quanthus.product_daily.application.funding_bridge import (
    funding_state_envelope,
    restore_funding_envelope,
)
from quanthus.product_daily.application.history import (
    AccountSnapshot,
    ConfigurationBinding,
    CurrentFact,
    FactHistory,
    LedgerEvent,
    OrderRecord,
    PlanStatus,
    ReceivedCorrection,
    ReceivedFeeAssessment,
    ReceivedOrderObservation,
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
from quanthus.product_daily.contracts import (
    CommandDisposition,
    CommandEnvelope,
    ProductDailyError,
    ReasonCode,
    require_bool,
    resolve_command,
)
from quanthus.product_daily.funding import (
    BucketPhase,
    CycleSettlementEvidence,
    FundingDecision,
    FundingState,
    SettlementRequest,
    prepare_cycle,
    prepare_cycle_invocation_payload,
    settle_batch,
    settle_invocation_payload,
)
from quanthus.shared.fact_contracts import FactError, digest

# Evidence is re-derived inside the write transaction; the recorded settlement
# identity deliberately excludes it so an order arriving after settlement does
# not turn a retry into an idempotency conflict.
_EMPTY_EVIDENCE = CycleSettlementEvidence(
    position_flat=True,
    orders_terminal=True,
    fills_complete=True,
    fees_known=True,
    conflicts_clear=True,
)


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
            if version not in (0, 1, 2, 3, 4, 5) or (version == 0 and tables):
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
                  kind TEXT NOT NULL DEFAULT 'fill',
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
                  side TEXT NOT NULL DEFAULT 'buy', order_id TEXT,
                  PRIMARY KEY(account_id, mode, fact_id));
                CREATE TABLE IF NOT EXISTS order_states(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, order_id TEXT NOT NULL,
                  security_code TEXT NOT NULL, side TEXT NOT NULL,
                  accepted_total_qty INTEGER NOT NULL, price_bound TEXT NOT NULL,
                  estimated_fee_upper TEXT NOT NULL,
                  known_fill_qty INTEGER NOT NULL DEFAULT 0,
                  status TEXT NOT NULL, order_state_revision INTEGER NOT NULL DEFAULT 0,
                  final_cumulative_fill_qty INTEGER, cancelled_qty INTEGER,
                  needs_reconciliation INTEGER NOT NULL DEFAULT 0,
                  reason_codes_json TEXT NOT NULL DEFAULT '[]',
                  last_observation_id TEXT,
                  PRIMARY KEY(account_id, mode, order_id));
                CREATE TABLE IF NOT EXISTS order_fees(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL, fee_id TEXT NOT NULL,
                  order_id TEXT NOT NULL, amount TEXT NOT NULL,
                  occurred_at TEXT NOT NULL, supersedes TEXT,
                  PRIMARY KEY(account_id, mode, fee_id));
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
                CREATE TABLE IF NOT EXISTS funding_states(
                  account_id TEXT NOT NULL, mode TEXT NOT NULL,
                  bound_account_revision INTEGER NOT NULL,
                  state_json TEXT NOT NULL,
                  bindings_json TEXT NOT NULL DEFAULT '{}',
                  PRIMARY KEY(account_id, mode));
                PRAGMA user_version=5;
                CREATE TABLE IF NOT EXISTS outbox(
                  outbox_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
                  mode TEXT NOT NULL, event_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                  delivered_at TEXT);
                """
            )
            # Upgrade a version-1 store in place; existing facts stay order-less buys.
            inbox_cols = {
                r[1] for r in connection.execute("PRAGMA table_info(inbox)").fetchall()
            }
            if "kind" not in inbox_cols:
                connection.execute(
                    "ALTER TABLE inbox ADD COLUMN kind TEXT NOT NULL DEFAULT 'fill'"
                )
            fact_cols = {
                r[1] for r in connection.execute("PRAGMA table_info(facts)").fetchall()
            }
            if "side" not in fact_cols:
                connection.execute(
                    "ALTER TABLE facts ADD COLUMN side TEXT NOT NULL DEFAULT 'buy'"
                )
            if "order_id" not in fact_cols:
                connection.execute("ALTER TABLE facts ADD COLUMN order_id TEXT")
            fee_cols = {
                r[1] for r in connection.execute("PRAGMA table_info(order_fees)").fetchall()
            }
            if "supersedes" not in fee_cols:
                connection.execute("ALTER TABLE order_fees ADD COLUMN supersedes TEXT")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS funding_states("
                "account_id TEXT NOT NULL, mode TEXT NOT NULL,"
                "bound_account_revision INTEGER NOT NULL,"
                "state_json TEXT NOT NULL,"
                "bindings_json TEXT NOT NULL DEFAULT '{}',"
                "PRIMARY KEY(account_id, mode))"
            )
            fs_cols = {
                r[1] for r in connection.execute("PRAGMA table_info(funding_states)").fetchall()
            }
            if "bindings_json" not in fs_cols:
                connection.execute(
                    "ALTER TABLE funding_states ADD COLUMN bindings_json TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute("PRAGMA user_version=5")

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
                    "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        report.payload_digest,
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                        self._timestamp(),
                        "fill",
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
                sell_cost_basis: Decimal | None = None
                if report.side == "sell":
                    sell_cost_basis = self._cost_basis(
                        connection, context, report.security_code, report.quantity
                    )
                    impact = apply_sell_delta(
                        current,
                        security_code=report.security_code,
                        quantity=report.quantity,
                        price=report.price,
                        fee=report.confirmed_fee,
                        cost_basis=sell_cost_basis,
                    )
                    updated, postings = impact.projection, impact.postings
                else:
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
                        "side": report.side,
                        "order_id": report.order_id,
                        "quantity": report.quantity,
                        "price": format(report.price, "f"),
                        "cost_basis": (
                            None if sell_cost_basis is None else format(sell_cost_basis, "f")
                        ),
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
                    "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        report.report_id,
                        report.security_code,
                        report.quantity,
                        format(report.price, "f"),
                        format(report.confirmed_fee, "f"),
                        1,
                        report.side,
                        report.order_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO source_facts VALUES(?,?,?,?)",
                    (context.account_id, context.mode, report.source_fact_id, receipt.receipt_id),
                )
                self._apply_fill_to_order(connection, context, report)
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
                    "NEGATIVE_POSITION",
                    "POSITION_MISMATCH",
                    "UNKNOWN_ORDER_EXPOSURE",
                    "ORDER_CONTEXT_MISMATCH",
                    FINAL_FILL_GAP,
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

    # ---- order observations and fees ------------------------------------------------
    def _receive_kind(
        self,
        context: SyntheticContext,
        *,
        kind: str,
        identity: str,
        payload: dict[str, object],
    ) -> Receipt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_baseline(connection, context)
                row = connection.execute(
                    "SELECT * FROM inbox WHERE account_id=? AND mode=? AND report_id=?",
                    (context.account_id, context.mode, identity),
                ).fetchone()
                digest_value = digest(payload)
                if row is not None:
                    if row["payload_digest"] == digest_value:
                        connection.commit()
                        return Receipt(row["receipt_id"], "DUPLICATE", digest_value)
                    connection.execute(
                        "INSERT OR IGNORE INTO conflicts VALUES(?,?,?,?,?,?)",
                        (
                            context.account_id,
                            context.mode,
                            self._identity(context, kind, identity),
                            row["payload_digest"],
                            digest_value,
                            json.dumps(payload, sort_keys=True),
                        ),
                    )
                    connection.commit()
                    return Receipt(
                        row["receipt_id"],
                        "IDEMPOTENCY_CONFLICT",
                        row["payload_digest"],
                        ("REPORT_ID_PAYLOAD_CONFLICT",),
                    )
                receipt_id = "receipt-" + self._identity(context, kind, identity)
                connection.execute(
                    "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                    (
                        context.account_id,
                        context.mode,
                        identity,
                        digest_value,
                        receipt_id,
                        json.dumps(payload, sort_keys=True),
                        self._timestamp(),
                        kind,
                        "RECEIVED",
                    ),
                )
                connection.commit()
                return Receipt(receipt_id, "RECEIVED")
            except Exception:
                connection.rollback()
                raise

    def _check_source(
        self, connection: sqlite3.Connection, context: SyntheticContext,
        source_id: str, source_sequence: str, receipt_id: str,
    ) -> None:
        duplicate = connection.execute(
            "SELECT report_receipt_id FROM source_facts "
            "WHERE account_id=? AND mode=? AND source_fact_id=?",
            (context.account_id, context.mode, digest([source_id, source_sequence])),
        ).fetchone()
        if duplicate is not None and duplicate[0] != receipt_id:
            raise FactError("SOURCE_FACT_DUPLICATE", "source fact already posted")
        connection.execute(
            "INSERT INTO source_facts VALUES(?,?,?,?)",
            (context.account_id, context.mode, digest([source_id, source_sequence]), receipt_id),
        )

    def receive_order_observation(
        self, context: SyntheticContext, observation: OrderObservation
    ) -> Receipt:
        return self._receive_kind(
            context,
            kind="order_observation",
            identity=observation.observation_id,
            payload=observation.to_dict(),
        )

    def post_order_observation(
        self,
        context: SyntheticContext,
        receipt: Receipt,
        observation: OrderObservation,
        accepted_total_qty: int,
        side: str,
        security_code: str,
        price_bound: Decimal,
        estimated_fee_upper: Decimal,
    ) -> SubmissionResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                inbox = connection.execute(
                    "SELECT * FROM inbox WHERE account_id=? AND mode=? AND receipt_id=?",
                    (context.account_id, context.mode, receipt.receipt_id),
                ).fetchone()
                if inbox is None or inbox["payload_digest"] != digest(observation.to_dict()):
                    raise FactError("IDEMPOTENCY_CONFLICT", "receipt payload differs")
                if inbox["status"] == "POSTED":
                    stored = json.loads(inbox["result_json"])
                    connection.commit()
                    return SubmissionResult(
                        receipt.receipt_id,
                        "POSTED",
                        self._projection_from_dict(stored["projection"]),
                    )
                self._check_source(
                    connection, context, observation.source_id,
                    observation.source_sequence, receipt.receipt_id,
                )
                baseline_row = connection.execute(
                    "SELECT as_of FROM baselines WHERE account_id=? AND mode=?",
                    (context.account_id, context.mode),
                ).fetchone()
                gap = coverage_gap_at(
                    observation.observed_at, datetime.fromisoformat(baseline_row["as_of"])
                )
                if gap is not None:
                    raise FactError(gap, "observation predates the baseline without coverage")

                order = self._order_state_row(connection, context, observation.order_id)
                if order is None:
                    connection.execute(
                        "INSERT INTO order_states "
                        "(account_id,mode,order_id,security_code,side,accepted_total_qty,"
                        "price_bound,estimated_fee_upper,known_fill_qty,status,"
                        "order_state_revision,last_observation_id) "
                        "VALUES(?,?,?,?,?,?,?,?,0,'open',0,?)",
                        (
                            context.account_id,
                            context.mode,
                            observation.order_id,
                            security_code,
                            side,
                            accepted_total_qty,
                            format(price_bound, "f"),
                            format(estimated_fee_upper, "f"),
                            observation.observation_id,
                        ),
                    )
                else:
                    # A terminal order is a proven final state at a confirmed
                    # observed_at.  A later observation that is stale relative to
                    # that proof — either non-terminal, or an earlier terminal
                    # reading — is kept in the inbox and ledger as history but
                    # does not reopen the order, release its held unreported
                    # fills, or overwrite the confirmed terminal counts.
                    order_is_terminal = order["status"] == "terminal"
                    current_observed_at = None
                    current_terminal_final = None
                    current_terminal_cancelled = None
                    if order_is_terminal and order["last_observation_id"] is not None:
                        last_row = connection.execute(
                            "SELECT payload_json FROM inbox WHERE account_id=? "
                            "AND mode=? AND report_id=?",
                            (
                                context.account_id,
                                context.mode,
                                order["last_observation_id"],
                            ),
                        ).fetchone()
                        if last_row is not None:
                            last_payload = json.loads(last_row["payload_json"])
                            current_observed_at = datetime.fromisoformat(
                                last_payload["observed_at"]
                            )
                            current_terminal_final = last_payload["final_cumulative_fill_qty"]
                            current_terminal_cancelled = last_payload["cancelled_qty"]
                    stale_terminal = (
                        order_is_terminal
                        and observation.status == "terminal"
                        and current_observed_at is not None
                        and observation.observed_at < current_observed_at
                    )
                    # Two terminal readings at the same observed_at and revision that
                    # disagree on the confirmed counts cannot be ordered by time or
                    # version.  Keep both as history, flag the order for
                    # reconciliation, and hold the larger unreported fill estimate so
                    # the account does not spend the contested exposure.
                    conflicting_terminal = (
                        order_is_terminal
                        and observation.status == "terminal"
                        and current_observed_at is not None
                        and observation.observed_at == current_observed_at
                        and (
                            observation.final_cumulative_fill_qty != current_terminal_final
                            or observation.cancelled_qty != current_terminal_cancelled
                        )
                    )
                    if conflicting_terminal:
                        # Hold the larger unreported-fill reading so the account does
                        # not spend the contested exposure: keep the higher final
                        # cumulative and cancelled counts as the effective terminal
                        # until reconciliation picks one.  effective_order_state then
                        # derives the conservative hold and FINAL_FILL_GAP itself.
                        self._refresh_order_state(
                            connection, context, observation.order_id,
                            pending_observation_id=observation.observation_id,
                        )
                        self._store_submission(connection, inbox, SubmissionResult(
                            receipt.receipt_id, "POSTED",
                            self._projection(connection, context),
                        ))
                        connection.commit()
                        return SubmissionResult(
                            receipt.receipt_id, "POSTED",
                            self._projection(connection, context),
                        )
                    observation_reopens = observation.status != "terminal"
                    if order_is_terminal and (observation_reopens or stale_terminal):
                        self._store_submission(connection, inbox, SubmissionResult(
                            receipt.receipt_id, "POSTED",
                            self._projection(connection, context),
                        ))
                        connection.commit()
                        return SubmissionResult(
                            receipt.receipt_id, "POSTED",
                            self._projection(connection, context),
                        )
                    connection.execute(
                        "UPDATE order_states SET last_observation_id=? "
                        "WHERE account_id=? AND mode=? AND order_id=?",
                        (
                            observation.observation_id,
                            context.account_id,
                            context.mode,
                            observation.order_id,
                        ),
                    )
                self._refresh_order_state(connection, context, observation.order_id,
                                       pending_observation_id=observation.observation_id)

                event_id = "observation:" + self._identity(
                    context, "order_observation", observation.observation_id
                )
                current = self._projection(connection, context)
                updated = AccountProjection(
                    current.revision,
                    current.event_sequence + 1,
                    current.cash,
                    current.positions,
                    current.fact_count,
                )
                event_json = json.dumps(
                    {
                        "kind": "order_observation",
                        "fact_id": observation.observation_id,
                        "receipt_id": receipt.receipt_id,
                        "order_id": observation.order_id,
                        "status": observation.status,
                        "final_cumulative_fill_qty": observation.final_cumulative_fill_qty,
                        "cancelled_qty": observation.cancelled_qty,
                        "side": side,
                        "security_code": security_code,
                        "accepted_total_qty": accepted_total_qty,
                        "price_bound": format(price_bound, "f"),
                        "estimated_fee_upper": format(estimated_fee_upper, "f"),
                        "observed_at": observation.observed_at.isoformat(),
                        "received_at": inbox["received_at"],
                        "recorded_at": self._timestamp(),
                        "quantity": 0,
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
                self._write_projection(connection, context, updated)
                result = SubmissionResult(
                    receipt.receipt_id,
                    "POSTED",
                    updated,
                    (),
                    affected_plans=(),
                )
                self._store_submission(connection, inbox, result)
                connection.commit()
                return result
            except FactError as exc:
                connection.rollback()
                if exc.reason_code not in {
                    "SOURCE_FACT_DUPLICATE",
                    "BASELINE_COVERAGE_UNKNOWN",
                    FINAL_FILL_GAP,
                    "ORDER_STATE_GAP",
                }:
                    raise
                with self._connect() as pending:
                    pending.execute("BEGIN IMMEDIATE")
                    current = self._projection(pending, context)
                    result = SubmissionResult(
                        receipt.receipt_id,
                        "NEEDS_RECONCILIATION",
                        current,
                        (),
                        (exc.reason_code,),
                    )
                    pending.execute(
                        "UPDATE inbox SET status='NEEDS_RECONCILIATION', result_json=? "
                        "WHERE account_id=? AND mode=? AND report_id=?",
                        (
                            json.dumps(self._submission_dict(result), sort_keys=True),
                            context.account_id,
                            context.mode,
                            observation.observation_id,
                        ),
                    )
                    pending.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def receive_fee_assessment(
        self, context: SyntheticContext, fee: FeeAssessment
    ) -> Receipt:
        return self._receive_kind(
            context,
            kind="fee",
            identity=fee.fee_id,
            payload=fee.to_dict(),
        )

    def post_fee_assessment(
        self, context: SyntheticContext, receipt: Receipt, fee: FeeAssessment
    ) -> SubmissionResult:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                inbox = connection.execute(
                    "SELECT * FROM inbox WHERE account_id=? AND mode=? AND receipt_id=?",
                    (context.account_id, context.mode, receipt.receipt_id),
                ).fetchone()
                if inbox is None or inbox["payload_digest"] != digest(fee.to_dict()):
                    raise FactError("IDEMPOTENCY_CONFLICT", "receipt payload differs")
                if inbox["status"] == "POSTED":
                    stored = json.loads(inbox["result_json"])
                    connection.commit()
                    return SubmissionResult(
                        receipt.receipt_id,
                        "POSTED",
                        self._projection_from_dict(stored["projection"]),
                    )
                self._check_source(
                    connection, context, fee.source_id, fee.source_sequence, receipt.receipt_id
                )
                baseline_row = connection.execute(
                    "SELECT as_of FROM baselines WHERE account_id=? AND mode=?",
                    (context.account_id, context.mode),
                ).fetchone()
                gap = coverage_gap_at(
                    fee.occurred_at, datetime.fromisoformat(baseline_row["as_of"])
                )
                if gap is not None:
                    raise FactError(gap, "fee predates the baseline without coverage")
                order = self._order_state_row(connection, context, fee.order_id)
                if order is None:
                    raise FactError("UNKNOWN_ORDER_EXPOSURE", "fee references an unknown order")
                # One whole-order fee total is a single business fact carried by a
                # correction chain of assessments.  A new confirmation restates the
                # total, so the ledger applies only the delta against the prior
                # confirmed total: an identical amount is the same fact and posts
                # nothing, a revised amount corrects by the difference.
                prior_total = connection.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM order_fees "
                    "WHERE account_id=? AND mode=? AND order_id=? AND supersedes IS NULL",
                    (context.account_id, context.mode, fee.order_id),
                ).fetchone()[0]
                prior_total = Decimal(str(prior_total)) if prior_total is not None else Decimal("0")
                delta = (fee.amount - prior_total).quantize(Decimal("0.01"))
                if delta == Decimal("0.00") and prior_total != Decimal("0"):
                    self._store_submission(connection, inbox, SubmissionResult(
                        receipt.receipt_id, "POSTED",
                        self._projection(connection, context),
                    ))
                    connection.commit()
                    return SubmissionResult(
                        receipt.receipt_id, "POSTED",
                        self._projection(connection, context),
                    )
                current = self._projection(connection, context)
                cash = (current.cash - delta).quantize(Decimal("0.01"))
                if cash < 0:
                    raise FactError("INSUFFICIENT_CASH", "confirmed fee exceeds cash")
                updated = AccountProjection(
                    current.revision + 1,
                    current.event_sequence + 1,
                    cash,
                    current.positions,
                    current.fact_count + 1,
                )
                event_id = "fee:" + self._identity(context, "fee", fee.fee_id)
                event_json = json.dumps(
                    {
                        "kind": "fee",
                        "fact_id": fee.fee_id,
                        "receipt_id": receipt.receipt_id,
                        "order_id": fee.order_id,
                        "amount": format(fee.amount, "f"),
                        "delta": format(delta, "f"),
                        "occurred_at": fee.occurred_at.isoformat(),
                        "received_at": inbox["received_at"],
                        "recorded_at": self._timestamp(),
                        "quantity": 0,
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
                self._insert_postings(
                    connection,
                    event_id,
                    (
                        Posting("ASSET_CASH", -delta),
                        Posting("EXPENSE_CONFIRMED_FEE", delta),
                    ),
                )
                self._write_projection(connection, context, updated)
                connection.execute(
                    "UPDATE order_fees SET supersedes=? "
                    "WHERE account_id=? AND mode=? AND order_id=? AND supersedes IS NULL",
                    (fee.fee_id, context.account_id, context.mode, fee.order_id),
                )
                connection.execute(
                    "INSERT INTO order_fees VALUES(?,?,?,?,?,?,NULL)",
                    (
                        context.account_id,
                        context.mode,
                        fee.fee_id,
                        fee.order_id,
                        format(fee.amount, "f"),
                        fee.occurred_at.isoformat(),
                    ),
                )
                self._refresh_order_state(connection, context, fee.order_id)
                result = SubmissionResult(
                    receipt.receipt_id, "POSTED", updated, (), affected_plans=()
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
                    "UNKNOWN_ORDER_EXPOSURE",
                }:
                    raise
                with self._connect() as pending:
                    pending.execute("BEGIN IMMEDIATE")
                    current = self._projection(pending, context)
                    result = SubmissionResult(
                        receipt.receipt_id,
                        "NEEDS_RECONCILIATION",
                        current,
                        (),
                        (exc.reason_code,),
                    )
                    pending.execute(
                        "UPDATE inbox SET status='NEEDS_RECONCILIATION', result_json=? "
                        "WHERE account_id=? AND mode=? AND report_id=?",
                        (
                            json.dumps(self._submission_dict(result), sort_keys=True),
                            context.account_id,
                            context.mode,
                            fee.fee_id,
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
                    if event.get("side", "buy") == "sell":
                        impact = apply_sell_delta(
                            projection,
                            security_code=event["security_code"],
                            quantity=event["quantity"],
                            price=Decimal(event["price"]),
                            fee=Decimal(event["confirmed_fee"]),
                            cost_basis=Decimal(event["cost_basis"]),
                        )
                        projection = impact.projection
                    else:
                        projection, _ = apply_buy_delta(
                            projection,
                            security_code=event["security_code"],
                            quantity=event["quantity"],
                            price=Decimal(event["price"]),
                            fee=Decimal(event["confirmed_fee"]),
                        )
                elif event["kind"] == "fee":
                    updated = AccountProjection(
                        projection.revision + 1,
                        projection.event_sequence + 1,
                        # The event records the whole-order total it confirms;
                        # cash moves only by the delta against the prior total,
                        # which is what the live path posted for this fact.
                        (projection.cash - Decimal(event.get("delta", event["amount"]))).quantize(Decimal("0.01")),
                        projection.positions,
                        projection.fact_count + 1,
                    )
                    projection = updated
                elif event["kind"] == "order_observation":
                    projection = AccountProjection(
                        projection.revision,
                        projection.event_sequence + 1,
                        projection.cash,
                        projection.positions,
                        projection.fact_count,
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
        self._restore_order_states(connection, context)
        baseline = connection.execute(
            "SELECT * FROM baselines WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if baseline is None:
            raise FactError("BASELINE_NOT_FOUND", "baseline is unavailable")
        reports = []
        for row in connection.execute(
            "SELECT * FROM inbox WHERE account_id=? AND mode=? AND kind='fill' ORDER BY rowid",
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
                raw.get("order_id"),
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
                    order_id=report.order_id,
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
                        else event.get("quantity_delta", event.get("quantity", 0))
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
        order_rows = connection.execute(
            "SELECT * FROM order_states WHERE account_id=? AND mode=? ORDER BY order_id",
            (context.account_id, context.mode),
        ).fetchall()
        orders = []
        for row in order_rows:
            fees = self._order_fees(connection, context, row["order_id"])
            fee_total = sum((f.amount for f in fees), Decimal("0"))
            state = effective_order_state(
                order_id=row["order_id"],
                side=row["side"],
                accepted_total_qty=int(row["accepted_total_qty"]),
                price_bound=Decimal(row["price_bound"]),
                known_fill_qty=int(row["known_fill_qty"]),
                latest_observation=None,
                confirmed_fees=fees,
                estimated_fee_upper=Decimal(row["estimated_fee_upper"]),
            )
            status = row["status"]
            needs = bool(row["needs_reconciliation"])
            reasons = tuple(json.loads(row["reason_codes_json"]))
            if status == "terminal":
                unreported = max(
                    0, int(row["final_cumulative_fill_qty"]) - int(row["known_fill_qty"])
                )
                hold_amount = (
                    Decimal(unreported) * Decimal(row["price_bound"])
                    + max(Decimal("0"), Decimal(row["estimated_fee_upper"]) - fee_total)
                ).quantize(Decimal("0.01"))
            else:
                hold_amount = (
                    Decimal(int(row["accepted_total_qty"]) - int(row["known_fill_qty"]))
                    * Decimal(row["price_bound"])
                    + max(Decimal("0"), Decimal(row["estimated_fee_upper"]) - fee_total)
                ).quantize(Decimal("0.01"))
            if row["side"] == "sell":
                hold_amount = Decimal("0.00")
            orders.append(
                OrderRecord(
                    order_id=row["order_id"],
                    security_code=row["security_code"],
                    side=row["side"],
                    price_bound=Decimal(row["price_bound"]),
                    accepted_total_qty=int(row["accepted_total_qty"]),
                    known_fill_qty=int(row["known_fill_qty"]),
                    unreported_fill_qty=(
                        max(0, int(row["final_cumulative_fill_qty"] or 0) - int(row["known_fill_qty"]))
                        if status == "terminal"
                        else 0
                    ),
                    hold_qty=(
                        max(0, int(row["final_cumulative_fill_qty"] or 0) - int(row["known_fill_qty"]))
                        if status == "terminal"
                        else int(row["accepted_total_qty"]) - int(row["known_fill_qty"])
                    ),
                    hold_amount=hold_amount,
                    status=status,
                    needs_reconciliation=needs,
                    reason_codes=reasons,
                    fee_confirmed_total=fee_total.quantize(Decimal("0.01")),
                    settlement_ready=(
                        status == "terminal"
                        and not needs
                        and int(row["known_fill_qty"]) > 0
                        and len(fees) > 0
                    ),
                )
            )
        observations = tuple(
            ReceivedOrderObservation(
                observation_id=row["report_id"],
                receipt_id=row["receipt_id"],
                order_id=json.loads(row["payload_json"])["order_id"],
                status=json.loads(row["payload_json"])["status"],
                observed_at=json.loads(row["payload_json"])["observed_at"],
                final_cumulative_fill_qty=json.loads(row["payload_json"])[
                    "final_cumulative_fill_qty"
                ],
                cancelled_qty=json.loads(row["payload_json"])["cancelled_qty"],
                order_revision=int(json.loads(row["payload_json"])["order_revision"]),
                store_status=row["status"],
            )
            for row in connection.execute(
                "SELECT * FROM inbox WHERE account_id=? AND mode=? AND kind='order_observation' "
                "ORDER BY rowid",
                (context.account_id, context.mode),
            )
        )
        fees_received = tuple(
            ReceivedFeeAssessment(
                fee_id=row["report_id"],
                receipt_id=row["receipt_id"],
                order_id=json.loads(row["payload_json"])["order_id"],
                amount=Decimal(json.loads(row["payload_json"])["amount"]),
                occurred_at=json.loads(row["payload_json"])["occurred_at"],
                store_status=row["status"],
            )
            for row in connection.execute(
                "SELECT * FROM inbox WHERE account_id=? AND mode=? AND kind='fee' ORDER BY rowid",
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
            orders=tuple(orders),
            observations=observations,
            fees=fees_received,
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

    def _cost_basis(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        security_code: str,
        sell_quantity: int,
    ) -> Decimal:
        """Weighted-average cost of the sold quantity from posted buy facts + baseline.

        A baseline-held position joins at the average posted buy price; with no buy
        facts at all the sell stays received and unreconciled instead of inventing
        a cost.
        """
        baseline = connection.execute(
            "SELECT quantity, security_code FROM baselines WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        buy_rows = connection.execute(
            "SELECT quantity, price FROM facts "
            "WHERE account_id=? AND mode=? AND security_code=? AND side='buy' ORDER BY rowid",
            (context.account_id, context.mode, security_code),
        ).fetchall()
        posted_qty = sum(int(r["quantity"]) for r in buy_rows)
        posted_principal = sum(Decimal(r["price"]) * int(r["quantity"]) for r in buy_rows)
        total_qty = posted_qty
        principal = posted_principal
        if baseline is not None and baseline["security_code"] == security_code:
            base_qty = int(baseline["quantity"])
            total_qty += base_qty
            if posted_qty:
                principal += (posted_principal / posted_qty) * base_qty
        if total_qty == 0:
            raise FactError("POSITION_MISMATCH", "sell has no posted position")
        if baseline is not None and baseline["security_code"] == security_code and not buy_rows:
            # Baseline cost is unknown in the synthetic slice; carry the position at
            # zero basis and let the account view mark the cost as unknown.
            return Decimal("0.00")
        unit = (principal / Decimal(total_qty)).quantize(Decimal("0.0001"))
        return (unit * sell_quantity).quantize(Decimal("0.01"))

    def _order_fees(
        self, connection: sqlite3.Connection, context: SyntheticContext, order_id: str
    ) -> tuple[FeeAssessment, ...]:
        return tuple(
            FeeAssessment(
                r["fee_id"], r["order_id"], Decimal(r["amount"]),
                "stored", r["fee_id"], datetime.fromisoformat(r["occurred_at"]),
            )
            for r in connection.execute(
                "SELECT * FROM order_fees WHERE account_id=? AND mode=? AND order_id=? "
                "AND supersedes IS NULL ORDER BY rowid",
                (context.account_id, context.mode, order_id),
            )
        )

    def _order_state_row(
        self, connection: sqlite3.Connection, context: SyntheticContext, order_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM order_states WHERE account_id=? AND mode=? AND order_id=?",
            (context.account_id, context.mode, order_id),
        ).fetchone()

    def _effective_terminal_observation(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        order_id: str,
        fallback_observation_id: str | None,
        pending_observation_id: str | None = None,
    ) -> OrderObservation | None:
        """Return the effective terminal observation for an order.

        The effective reading is the posted terminal observation with the
        largest observed_at.  When two or more posted terminal observations
        share that largest observed_at but disagree on the confirmed counts,
        they cannot be ordered, so a conservative synthetic reading keeps the
        larger final cumulative and cancelled counts.  Deriving it from the
        posted observations (not a stored row) means a fee confirmation, an
        ordinary reopen, or a derived-row rebuild all reproduce the same
        disputed terminal state until reconciliation resolves it.
        """

        rows = connection.execute(
            "SELECT payload_json, status, report_id FROM inbox "
            "WHERE account_id=? AND mode=? AND kind='order_observation'",
            (context.account_id, context.mode),
        ).fetchall()
        observations = []
        for obs_row in rows:
            # Admit only posted observations, plus the one currently posting so
            # its own transaction sees it.  RECEIVED-only and rejected receipts
            # never take effect.
            if obs_row["status"] != "POSTED" and (
                pending_observation_id is None
                or obs_row["report_id"] != pending_observation_id
            ):
                continue
            raw = json.loads(obs_row["payload_json"])
            if raw["order_id"] != order_id or raw["status"] != "terminal":
                continue
            observations.append(
                OrderObservation(
                    raw["observation_id"], raw["order_id"], raw["status"],
                    datetime.fromisoformat(raw["observed_at"]), raw["source_id"],
                    raw["source_sequence"], raw["final_cumulative_fill_qty"],
                    raw["cancelled_qty"], raw["order_revision"],
                    raw["expected_order_state_revision"],
                )
            )
        if not observations:
            # No terminal observation posted yet: keep the stored latest
            # observation so a non-terminal latest reading is still honoured.
            if fallback_observation_id is None:
                return None
            obs_row = connection.execute(
                "SELECT payload_json FROM inbox WHERE account_id=? AND mode=? AND report_id=?",
                (context.account_id, context.mode, fallback_observation_id),
            ).fetchone()
            if obs_row is None:
                return None
            raw = json.loads(obs_row["payload_json"])
            return OrderObservation(
                raw["observation_id"], raw["order_id"], raw["status"],
                datetime.fromisoformat(raw["observed_at"]), raw["source_id"],
                raw["source_sequence"], raw["final_cumulative_fill_qty"],
                raw["cancelled_qty"], raw["order_revision"],
                raw["expected_order_state_revision"],
            )
        latest_at = max(o.observed_at for o in observations)
        candidates = [o for o in observations if o.observed_at == latest_at]
        if len(candidates) == 1:
            return candidates[0]
        # Contested simultaneous terminal readings: merge into the conservative
        # envelope so the disputed unreported exposure stays held.
        first = candidates[0]
        return OrderObservation(
            f"{first.observation_id}:contested",
            order_id,
            "terminal",
            latest_at,
            first.source_id,
            first.source_sequence,
            max((o.final_cumulative_fill_qty or 0) for o in candidates),
            max((o.cancelled_qty or 0) for o in candidates),
            first.order_revision,
        )


    def _refresh_order_state(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        order_id: str,
        pending_observation_id: str | None = None,
    ) -> None:
        row = self._order_state_row(connection, context, order_id)
        if row is None:
            return
        # Derive the effective terminal reading from the admitted (posted)
        # terminal observations for this order — never from a single stored or
        # incoming row.  The observation currently being posted is admitted via
        # pending_observation_id so its own transaction sees it before the
        # receipt flips to POSTED; a same-version restatement therefore folds
        # into the admitted set instead of overwriting a contested pair.
        obs = self._effective_terminal_observation(
            connection, context, order_id, row["last_observation_id"],
            pending_observation_id,
        )
        state = effective_order_state(
            order_id=order_id,
            side=row["side"],
            accepted_total_qty=int(row["accepted_total_qty"]),
            price_bound=Decimal(row["price_bound"]),
            known_fill_qty=int(row["known_fill_qty"]),
            latest_observation=obs,
            confirmed_fees=self._order_fees(connection, context, order_id),
            estimated_fee_upper=Decimal(row["estimated_fee_upper"]),
        )
        connection.execute(
            "UPDATE order_states SET status=?, order_state_revision=?, "
            "final_cumulative_fill_qty=?, cancelled_qty=?, needs_reconciliation=?, "
            "reason_codes_json=? WHERE account_id=? AND mode=? AND order_id=?",
            (
                state.status,
                state.order_state_revision,
                None if obs is None else obs.final_cumulative_fill_qty,
                None if obs is None else obs.cancelled_qty,
                1 if state.needs_reconciliation else 0,
                json.dumps(list(state.reason_codes)),
                context.account_id,
                context.mode,
                order_id,
            ),
        )

    def _apply_fill_to_order(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        report: ExecutionReport,
    ) -> None:
        if report.order_id is None:
            return
        row = self._order_state_row(connection, context, report.order_id)
        if row is None:
            raise FactError("UNKNOWN_ORDER_EXPOSURE", "fill references an unknown order")
        if row["security_code"] != report.security_code or row["side"] != report.side:
            raise FactError("ORDER_CONTEXT_MISMATCH", "fill does not match its order")
        known = int(row["known_fill_qty"]) + report.quantity
        if known > int(row["accepted_total_qty"]):
            raise FactError(FINAL_FILL_GAP, "fill exceeds the accepted order quantity")
        connection.execute(
            "UPDATE order_states SET known_fill_qty=? "
            "WHERE account_id=? AND mode=? AND order_id=?",
            (known, context.account_id, context.mode, report.order_id),
        )
        self._refresh_order_state(connection, context, report.order_id)


    def _restore_order_states(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> None:
        """Rebuild cached order projections from the immutable baseline and events.

        order_states / order_fees are derived read models, not source truth:
        when a cached row is missing (a restored or trimmed store) the committed
        events carry every boundary needed to rebuild it.  Replaying
        observations in order applies the same stale-observation guard as the
        live path, so a rebuilt ledger keeps the proven terminal state.
        """

        order_events: dict[str, list[dict]] = {}
        for row in connection.execute(
            "SELECT event_json FROM events WHERE account_id=? AND mode=? ORDER BY event_sequence",
            (context.account_id, context.mode),
        ):
            event = json.loads(row["event_json"])
            order_id = event.get("order_id")
            if order_id and event.get("kind") in ("fill", "order_observation", "fee"):
                order_events.setdefault(order_id, []).append(event)

        for order_id, events in order_events.items():
            # Only rebuild orders whose cached projection is missing entirely;
            # live rows already carry the latest fills and must not be replayed.
            order_missing = self._order_state_row(connection, context, order_id) is None
            exists = not order_missing
            for event in events:
                if event["kind"] == "order_observation":
                    if not order_missing:
                        continue
                    if order_missing and not exists:
                        if event.get("accepted_total_qty") is None:
                            continue
                        connection.execute(
                            "INSERT INTO order_states "
                            "(account_id,mode,order_id,security_code,side,accepted_total_qty,"
                            "price_bound,estimated_fee_upper,known_fill_qty,status,"
                            "order_state_revision,last_observation_id) "
                            "VALUES(?,?,?,?,?,?,?,?,0,'open',0,NULL)",
                            (
                                context.account_id,
                                context.mode,
                                order_id,
                                event["security_code"],
                                event["side"],
                                int(event["accepted_total_qty"]),
                                event["price_bound"],
                                event["estimated_fee_upper"],
                            ),
                        )
                        exists = True
                    row = self._order_state_row(connection, context, order_id)
                    # Mirror the live guard: a proven terminal state is not
                    # reopened by a stale replayed observation — non-terminal,
                    # or an earlier terminal reading of the same order.
                    if row["status"] == "terminal":
                        if event["status"] != "terminal":
                            continue
                        current_ts = connection.execute(
                            "SELECT payload_json FROM inbox WHERE account_id=? "
                            "AND mode=? AND report_id=?",
                            (
                                context.account_id,
                                context.mode,
                                row["last_observation_id"],
                            ),
                        ).fetchone()
                        # Mirror the live guard exactly: a terminal replayed
                        # observation strictly earlier than the stored one is
                        # stale and skipped, but one at the same observed_at is
                        # admitted so a contested pair rebuilds to the same
                        # conservative effective terminal the live path holds.
                        if (
                            current_ts is not None
                            and datetime.fromisoformat(event["observed_at"])
                            < datetime.fromisoformat(
                                json.loads(current_ts["payload_json"])["observed_at"]
                            )
                        ):
                            continue
                    obs_row = connection.execute(
                        "SELECT payload_json FROM inbox WHERE account_id=? AND mode=? "
                        "AND report_id=?",
                        (context.account_id, context.mode, event["fact_id"]),
                    ).fetchone()
                    if obs_row is not None:
                        connection.execute(
                            "UPDATE order_states SET last_observation_id=? "
                            "WHERE account_id=? AND mode=? AND order_id=?",
                            (
                                event["fact_id"],
                                context.account_id,
                                context.mode,
                                order_id,
                            ),
                        )
                elif event["kind"] == "fill":
                    if not exists or not order_missing:
                        continue
                    current = self._order_state_row(connection, context, order_id)
                    connection.execute(
                        "UPDATE order_states SET known_fill_qty=? "
                        "WHERE account_id=? AND mode=? AND order_id=?",
                        (
                            int(current["known_fill_qty"]) + int(event["quantity"]),
                            context.account_id,
                            context.mode,
                            order_id,
                        ),
                    )
                elif event["kind"] == "fee":
                    present = connection.execute(
                        "SELECT 1 FROM order_fees WHERE account_id=? AND mode=? AND fee_id=?",
                        (context.account_id, context.mode, event["fact_id"]),
                    ).fetchone()
                    if present is None:
                        # Restore this one missing fee row from its event.  The
                        # whole-order fee is a single business fact restated by
                        # a correction chain, so the effective row is the latest
                        # confirmed total.  A row replayed out of event order
                        # (an older total recovered while a newer row still
                        # stands) must not displace the newer effective row: it
                        # is inserted already superseded, keeping both original
                        # events traceable while the effective total stays the
                        # highest confirmed version.
                        current = connection.execute(
                            "SELECT fee_id, amount, occurred_at, supersedes FROM order_fees "
                            "WHERE account_id=? AND mode=? AND order_id=? AND supersedes IS NULL",
                            (context.account_id, context.mode, order_id),
                        ).fetchone()
                        supersedes: str | None = None
                        if current is None:
                            # No effective row survives: this replayed row becomes it.
                            supersedes = None
                        else:
                            # Compare the two rows by their position in this
                            # order's own fee-correction chain.  The events
                            # list is already in committed event order, so the
                            # fee whose event appears later is the later
                            # restatement — regardless of wall-clock
                            # occurred_at ties between same-second postings.
                            fee_order = [
                                e["fact_id"] for e in events if e["kind"] == "fee"
                            ]
                            newer = (
                                event["fact_id"] in fee_order
                                and current["fee_id"] in fee_order
                                and fee_order.index(event["fact_id"])
                                > fee_order.index(current["fee_id"])
                            )
                            if newer:
                                # The replayed row is the later restatement: it
                                # supersedes the standing row and becomes effective.
                                connection.execute(
                                    "UPDATE order_fees SET supersedes=? "
                                    "WHERE account_id=? AND mode=? AND fee_id=?",
                                    (event["fact_id"], context.account_id, context.mode,
                                     current["fee_id"]),
                                )
                                supersedes = None
                            else:
                                # The replayed row is not newer than the standing
                                # effective row (older, or a same-time version whose
                                # standing row already carries the latest total).
                                # Keep it traceable as already superseded without
                                # disturbing the standing row's own chain link.
                                supersedes = current["supersedes"] or current["fee_id"]
                        connection.execute(
                            "INSERT INTO order_fees "
                            "(account_id,mode,fee_id,order_id,amount,occurred_at,supersedes) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (
                                context.account_id,
                                context.mode,
                                event["fact_id"],
                                order_id,
                                event["amount"],
                                event["occurred_at"],
                                supersedes,
                            ),
                        )
            # Refresh only when this replay actually rebuilt the row; a live order
            # already reflects its confirmed facts and must not be recomputed.
            if order_missing and exists:
                # Recompute from the same posted-observation set the live path
                # uses, so a contested simultaneous terminal pair rebuilds to
                # the identical conservative effective terminal.
                self._refresh_order_state(connection, context, order_id)

    # ---- fixed-bucket funding (CP01..CP04) ----------------------------------

    def _read_funding_state(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> FundingState | None:
        row = connection.execute(
            "SELECT state_json FROM funding_states WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if row is None:
            return None
        return restore_funding_envelope(json.loads(row["state_json"]))

    def _bound_event_sequence(
        self, connection: sqlite3.Connection, context: SyntheticContext, bound_rev: int
    ) -> int:
        """Event sequence at the account revision the partition was bound to."""
        row = connection.execute(
            "SELECT MAX(event_sequence) FROM events "
            "WHERE account_id=? AND mode=? AND revision<=?",
            (context.account_id, context.mode, bound_rev),
        ).fetchone()
        return 0 if row is None or row[0] is None else int(row[0])

    def _projection_at(
        self, connection: sqlite3.Connection, context: SyntheticContext, revision: int
    ) -> AccountProjection:
        """Account positions/cash as of a revision, replayed from the baseline
        plus the cash/quantity deltas each event posted up to that revision."""
        row = connection.execute(
            "SELECT cash, quantity, security_code FROM baselines "
            "WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        positions: dict[str, int] = {}
        cash = Decimal(row["cash"]) if row is not None else Decimal("0")
        if row is not None and row["quantity"]:
            positions[row["security_code"]] = int(row["quantity"])
        for event_row in connection.execute(
            "SELECT event_id, event_json FROM events WHERE account_id=? AND mode=? "
            "AND revision<=? ORDER BY event_sequence",
            (context.account_id, context.mode, revision),
        ).fetchall():
            event = json.loads(event_row["event_json"])
            if event.get("kind") == "fill":
                sec = event["security_code"]
                qty = int(event["quantity"])
                positions[sec] = positions.get(sec, 0) + (
                    qty if event["side"] == "buy" else -qty
                )
                if not positions[sec]:
                    positions.pop(sec)
            moved = connection.execute(
                "SELECT COALESCE(SUM(amount),0) FROM postings "
                "WHERE event_id=? AND account='ASSET_CASH'",
                (event_row["event_id"],),
            ).fetchone()[0]
            cash += Decimal(str(moved))
        return AccountProjection(
            revision,
            revision,
            cash.quantize(Decimal("0.01")),
            tuple(Position(sec, qty) for sec, qty in positions.items()),
            0,
        )

    def _cycle_event_floors(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        bound_rev: int,
    ) -> tuple[dict[str, int], int]:
        """bucket_id -> event floor of the bucket's current cycle.

        Each accepted funding write stores the event sequence at which that
        bucket's cycle began in bindings_json.  Buckets without a recorded
        boundary still ride the cycle open at binding, whose default floor is
        the binding revision's event sequence.
        """
        row = connection.execute(
            "SELECT bindings_json FROM funding_states WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        default = self._bound_event_sequence(connection, context, bound_rev)
        raw = json.loads(row["bindings_json"] or "{}") if row is not None else {}
        floors = {
            bucket_id: int(revision)
            for bucket_id, revision in (
                raw.get("cycle_bound_account_revision") or {}
            ).items()
        }
        return floors, default

    def _write_funding_state(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        state: FundingState,
        bound_account_revision: int,
        bindings: dict[str, Any] | None = None,
    ) -> None:
        row = connection.execute(
            "SELECT bindings_json, bound_account_revision FROM funding_states "
            "WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if bindings is None:
            bindings = (
                json.loads(row["bindings_json"]) if row is not None else {}
            )
        # First write pins the reconciliation anchor inside bindings_json;
        # later writes keep it untouched while the column revision tracks the
        # newest accepted write for stale-view purposes.
        if row is None or "initial_bound_account_revision" not in bindings:
            bindings = dict(bindings)
            bindings["initial_bound_account_revision"] = (
                int(row["bound_account_revision"])
                if row is not None
                else bound_account_revision
            )
        connection.execute(
            "INSERT INTO funding_states "
            "(account_id,mode,bound_account_revision,state_json,bindings_json) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(account_id,mode) DO UPDATE SET "
            "bound_account_revision=excluded.bound_account_revision,"
            "state_json=excluded.state_json,"
            "bindings_json=excluded.bindings_json",
            (
                context.account_id,
                context.mode,
                bound_account_revision,
                json.dumps(funding_state_envelope(state), sort_keys=True),
                json.dumps(bindings, sort_keys=True),
            ),
        )

    def _attributed_cash_total(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        state: FundingState,
        bindings: dict[str, Any],
        bound_rev: int,
    ) -> Decimal:
        """Partition cash plus account cash still waiting to be attributed.

        Order-level cash events (fills, corrections, confirmed fees) posted
        after an open cycle's recorded floor remain attributable to that
        cycle's security, so they count toward the partition's accountable
        total until a settlement folds them in and advances the floor.
        """
        securities = self._funding_securities(
            connection, context, state, bindings
        )
        raw_floors = bindings.get("cycle_bound_account_revision") or {}
        default = self._bound_event_sequence(connection, context, bound_rev)
        completed = {
            (item.bucket_id, item.cycle_id) for item in state.completed_settlements
        }
        open_cycles = {
            (item.bucket_id, item.cycle_id)
            for item in state.buckets
            if item.phase is not BucketPhase.EMPTY
        } - completed
        pending = Decimal("0.00")
        for bucket_id, _cycle_id in open_cycles:
            sec = securities.get(bucket_id)
            floor = int(raw_floors.get(bucket_id, default))
            if sec is None or floor < 0:
                # A negative floor marks a cycle whose boundary predates
                # event-sequence tracking; its cash is already partitioned.
                continue
            moved = self._cash_events_after(connection, context, floor)
            pending += moved.get(sec, Decimal("0.00"))
        return state.cash_total + pending

    def _read_funding_binding(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> tuple[FundingState, int, dict[str, Any]] | None:
        row = connection.execute(
            "SELECT state_json, bound_account_revision, bindings_json FROM funding_states "
            "WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if row is None:
            return None
        return (
            restore_funding_envelope(json.loads(row["state_json"])),
            int(row["bound_account_revision"]),
            json.loads(row["bindings_json"] or "{}"),
        )

    def _binding_anchor(self, bound_rev: int, bindings: dict[str, Any]) -> int:
        """Account revision the partition reconciles against.

        Rows written since anchoring record it in bindings_json; rows bound
        before then reconcile against the revision their last write stored,
        which is what older releases tracked as the bound revision.
        """
        return int(bindings.get("initial_bound_account_revision", bound_rev))

    def _funding_securities(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        state: FundingState | None = None,
        bindings: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """bucket_id -> security_code assignment.

        Persisted bindings win.  Otherwise, when the account trades a single
        security it is attributed to the bucket being settled (the fixed-bucket
        product holds one security per bucket); a genuinely multi-security
        account without an explicit binding is left unattributed rather than
        guessed.
        """
        if bindings is None:
            bindings = self._read_bindings(connection, context)
        persisted = dict(bindings.get("bucket_securities") or {})
        if persisted:
            return persisted
        if state is None:
            return persisted
        securities = {
            r[0]
            for r in connection.execute(
                "SELECT DISTINCT security_code FROM order_states "
                "WHERE account_id=? AND mode=? "
                "UNION SELECT security_code FROM baselines "
                "WHERE account_id=? AND mode=?",
                (context.account_id, context.mode, context.account_id, context.mode),
            ).fetchall()
            if r[0]
        }
        if len(securities) == 1:
            only = next(iter(securities))
            # An empty bucket holds no proceeds; attributing the security to it
            # would make an open bucket look ambiguous without cause.
            return {
                item.bucket_id: only
                for item in state.buckets
                if item.phase is not BucketPhase.EMPTY
            }
        return {}

    def funding_state(
        self, context: SyntheticContext
    ) -> tuple[FundingState, int] | None:
        """Return (FundingState, bound_account_revision) or None when unbound."""
        with self._connect() as connection:
            connection.execute("BEGIN")
            binding = self._read_funding_binding(connection, context)
            connection.commit()
            if binding is None:
                return None
            return binding[0], binding[1]

    def funding_accountable_cash(
        self, context: SyntheticContext
    ) -> Decimal | None:
        """Partition cash plus proceeds still waiting to be attributed.

        Returns None when no partition is bound.  The total matches the
        recorded account cash whenever movements so far are explainable by
        the bound buckets' securities; a gap is a real mismatch needing
        manual reconciliation.
        """
        with self._connect() as connection:
            connection.execute("BEGIN")
            binding = self._read_funding_binding(connection, context)
            if binding is None:
                connection.commit()
                return None
            state, bound_rev, bindings = binding
            anchor_rev = self._binding_anchor(bound_rev, bindings)
            total = self._attributed_cash_total(
                connection, context, state, bindings, anchor_rev
            )
            connection.commit()
            return total

    def bind_funding_state(
        self,
        context: SyntheticContext,
        state: FundingState,
        bucket_securities: dict[str, str] | None = None,
    ) -> FundingState:
        """Persist the account's fixed-bucket partition once.

        The partition must reconcile with the account's admitted ledger at the
        moment of binding: the buckets plus shared reserve equal the recorded
        account cash.  ``bucket_securities`` optionally names each bucket's
        single security so later settlement can attribute fills to a cycle.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding_row = self._read_funding_binding(connection, context)
            if binding_row is not None:
                existing, existing_rev, existing_bindings = binding_row
                if bucket_securities:
                    merged = self._merge_bucket_securities(
                        existing_bindings, bucket_securities
                    )
                    if merged is not existing_bindings:
                        self._write_funding_state(
                            connection,
                            context,
                            existing,
                            existing_rev,
                            merged,
                        )
                connection.commit()
                return existing
            bindings = {"bucket_securities": dict(bucket_securities or {})}
            try:
                projection = self._projection(connection, context)
                account_cash = projection.cash
            except FactError:
                account_cash = None
            if account_cash is not None:
                # The partition plus proceeds already returned by each bucket's
                # security must equal the recorded account cash — the binding
                # cannot seed a balance the ledger never produced.
                attributed = Decimal("0.00")
                for sec in bindings["bucket_securities"].values():
                    attributed += self._realized_cash(connection, context, sec)
                if state.cash_total + attributed != account_cash:
                    connection.rollback()
                    raise FactError(
                        "FUNDING_BASELINE_MISMATCH",
                        "funding partition plus realized proceeds differs from "
                        "the recorded account cash",
                    )
            self._write_funding_state(
                connection,
                context,
                state,
                self._bound_account_revision(connection, context),
                bindings,
            )
            connection.commit()
            return state

    def _merge_bucket_securities(
        self,
        bindings: dict[str, Any],
        requested: dict[str, str],
    ) -> dict[str, Any]:
        """Fold a later attribution into persisted bindings.

        Persisted mappings win over a repeated request; new bucket-security
        pairs join them.  A request that would attach one security to a second
        bucket is refused outright — proceeds could then settle two cycles —
        and a request matching what is already stored is a no-op.
        """
        merged = dict(bindings)
        securities = dict(merged.get("bucket_securities") or {})
        incoming = dict(requested)
        for bucket_id, security_code in incoming.items():
            existing = securities.get(bucket_id)
            if existing is not None and existing != security_code:
                raise FactError(
                    "FUNDING_ATTRIBUTION_UNKNOWN",
                    f"bucket {bucket_id} is already attributed to {existing}",
                )
            owner = next(
                (bid for bid, sec in securities.items() if sec == security_code),
                None,
            )
            if owner is not None and owner != bucket_id:
                raise FactError(
                    "FUNDING_ATTRIBUTION_UNKNOWN",
                    "one security's proceeds cannot settle two buckets",
                )
            securities[bucket_id] = security_code
        merged["bucket_securities"] = securities
        if securities == dict(bindings.get("bucket_securities") or {}):
            return bindings
        return merged

    def _bound_account_revision(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> int:
        row = connection.execute(
            "SELECT revision FROM accounts WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        return 0 if row is None else int(row["revision"])

    def _order_facts(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        security_codes: set[str] | None = None,
    ) -> list[sqlite3.Row]:
        """Admitted order facts, optionally narrowed to the bucket's securities."""
        sql = (
            "SELECT order_id, security_code, status, needs_reconciliation, "
            "final_cumulative_fill_qty, known_fill_qty FROM order_states "
            "WHERE account_id=? AND mode=?"
        )
        rows = connection.execute(sql, (context.account_id, context.mode)).fetchall()
        if security_codes is not None:
            wanted = set(security_codes)
            rows = [row for row in rows if row["security_code"] in wanted]
        return rows

    def _order_securities(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> dict[str, str]:
        """order_id -> security_code from admitted order rows and fill facts."""
        mapping: dict[str, str] = {}
        for row in connection.execute(
            "SELECT order_id, security_code FROM order_states "
            "WHERE account_id=? AND mode=? AND order_id IS NOT NULL",
            (context.account_id, context.mode),
        ).fetchall():
            if row["security_code"]:
                mapping.setdefault(row["order_id"], row["security_code"])
        for row in connection.execute(
            "SELECT DISTINCT order_id, security_code FROM facts "
            "WHERE account_id=? AND mode=? AND order_id IS NOT NULL",
            (context.account_id, context.mode),
        ).fetchall():
            mapping.setdefault(row["order_id"], row["security_code"])
        return mapping

    def _evidence_from(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        order_rows: list[sqlite3.Row],
        position_flat: bool,
    ) -> CycleSettlementEvidence:
        orders_terminal = bool(order_rows) and all(
            row["status"] == "terminal" for row in order_rows
        )
        # Fills are complete only when no terminal order still holds an
        # unreported residual (final cumulative beyond the known fills).
        fills_complete = all(
            int(row["final_cumulative_fill_qty"] or 0) <= int(row["known_fill_qty"])
            for row in order_rows
        )
        conflicts_clear = all(not row["needs_reconciliation"] for row in order_rows)
        fees_known = all(
            connection.execute(
                "SELECT COUNT(*) FROM order_fees WHERE account_id=? AND mode=? "
                "AND order_id=? AND supersedes IS NULL",
                (context.account_id, context.mode, row["order_id"]),
            ).fetchone()[0] > 0
            for row in order_rows
        )
        return CycleSettlementEvidence(
            position_flat=position_flat,
            orders_terminal=orders_terminal,
            fills_complete=fills_complete,
            fees_known=fees_known,
            conflicts_clear=conflicts_clear,
        )

    def funding_settlement_evidence(
        self, context: SyntheticContext, security_codes: set[str] | None = None
    ) -> CycleSettlementEvidence:
        """Derive settlement readiness from the admitted order facts.

        ``security_codes`` narrows the check to the orders that belong to one
        bucket's security.  With no filter it reports the whole account.
        """
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                projection = self._projection(connection, context)
                if security_codes is None:
                    position_flat = not projection.positions
                else:
                    wanted = set(security_codes)
                    position_flat = not any(
                        item.security_code in wanted and item.quantity
                        for item in projection.positions
                    )
            except FactError:
                position_flat = True
            order_rows = self._order_facts(connection, context, security_codes)
            evidence = self._evidence_from(connection, context, order_rows, position_flat)
            connection.commit()
            return evidence

    def _realized_cash(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        security_code: str,
        since_sequence: int = 0,
    ) -> Decimal:
        """Net cash a security's fills moved after ``since_sequence``.

        Only fills admitted after the funding partition was bound count toward
        a settlement, so a partition bound to an already-realized balance is
        not charged the same proceeds twice.
        """
        rows = connection.execute(
            "SELECT side, quantity, price, confirmed_fee FROM facts "
            "WHERE account_id=? AND mode=? AND security_code=? ORDER BY rowid",
            (context.account_id, context.mode, security_code),
        ).fetchall()
        total = Decimal("0.00")
        for row in rows:
            principal = Decimal(row["price"]) * int(row["quantity"])
            fee = Decimal(row["confirmed_fee"])
            if row["side"] == "sell":
                total += principal - fee
            else:
                total -= principal + fee
        return total.quantize(Decimal("0.01"))

    def _cash_events_after(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        since_sequence: int,
        until_sequence: int | None = None,
    ) -> dict[str, Decimal]:
        """Per-security net ASSET_CASH moved by events in one sequence window.

        Fill, correction and confirmed whole-order-fee events move cash; plain
        observations only describe orders.  A partition bound before the window
        sees its net delta, while later events keep waiting for the bucket's
        next settlement cycle.
        """
        sql = (
            "SELECT e.event_id, e.event_json FROM events e "
            "WHERE e.account_id=? AND e.mode=? AND e.event_sequence>?"
        )
        params: list[object] = [context.account_id, context.mode, since_sequence]
        if until_sequence is not None:
            sql += " AND e.event_sequence<=?"
            params.append(until_sequence)
        sql += " ORDER BY e.event_sequence"
        rows = connection.execute(sql, params).fetchall()
        order_securities = self._order_securities(connection, context)
        moved: dict[str, Decimal] = {}
        for row in rows:
            event = json.loads(row["event_json"])
            kind = event.get("kind")
            if kind not in ("fill", "correction", "fee"):
                continue
            sec = event.get("security_code") or order_securities.get(event.get("order_id"))
            if sec is None:
                continue
            cash = connection.execute(
                "SELECT COALESCE(SUM(amount),0) FROM postings "
                "WHERE event_id=? AND account='ASSET_CASH'",
                (row["event_id"],),
            ).fetchone()[0]
            moved[sec] = moved.get(sec, Decimal("0.00")) + Decimal(str(cash))
        return moved

    def _event_sequence_by_fact(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        order_ids: tuple[str, ...],
    ) -> int | None:
        """Newest event sequence touching the orders, or None when none posted."""
        if not order_ids:
            return None
        latest: int | None = None
        rows = connection.execute(
            "SELECT event_sequence, event_json FROM events "
            "WHERE account_id=? AND mode=? ORDER BY event_sequence",
            (context.account_id, context.mode),
        ).fetchall()
        wanted = set(order_ids)
        for row in rows:
            event = json.loads(row["event_json"])
            if event.get("kind") not in ("fill", "correction", "fee"):
                continue
            fact_id = event.get("fact_id") or event.get("correction_id")
            order_id = event.get("order_id") or fact_id
            if order_id in wanted or fact_id in wanted:
                seq = int(row["event_sequence"])
                latest = seq if latest is None else max(latest, seq)
        return latest

    def settle_funding(
        self,
        context: SyntheticContext,
        *,
        settlement_id: str,
        requests: tuple[SettlementRequest, ...],
        close_bucket_ids: tuple[str, ...] | None,
    ) -> FundingDecision:
        """Settle closed buckets in confirmed bucket order against one reserve.

        An already-processed ``settle:<id>`` command replays its recorded
        receipt before anything is re-derived, so a retry never moves cash or
        touches a newer reserved cycle.  A first-time submission is re-verified
        inside the write transaction against each bucket's own admitted orders
        — terminal, fill-complete, fee-confirmed and conflict-free — then the
        ledger's net cash moved by that bucket's orders during the cycle is
        folded into the bucket before the shared reserve is split.  A bucket
        still reserved keeps its reservation while its cash tracks the ledger.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding_row = self._read_funding_binding(connection, context)
            if binding_row is None:
                connection.rollback()
                raise FactError("FUNDING_NOT_BOUND", "no funding partition is bound")
            state, bound_rev, bound_bindings = binding_row
            anchor_rev = self._binding_anchor(bound_rev, bound_bindings)
            self._require_reconciled_partition(
                connection, context, state, anchor_rev, bound_bindings
            )
            securities = self._funding_securities(
                connection, context, state, bound_bindings
            )

            targets = set(close_bucket_ids or (item.bucket_id for item in requests))
            # One security's proceeds belong to exactly one bucket.  When the
            # same security is attributed to more than one targeted bucket —
            # possible only through the single-security fallback, since
            # explicit bindings are checked for uniqueness — the attribution
            # is ambiguous and settlement stays out rather than double-count.
            all_secs = list(securities.values())
            if len(all_secs) != len(set(all_secs)):
                connection.rollback()
                raise FactError(
                    "FUNDING_ATTRIBUTION_UNKNOWN",
                    "one security's proceeds cannot settle two buckets; "
                    "bind an explicit bucket-security mapping first",
                )
            targeted_secs = [
                securities[bid]
                for bid in targets
                if bid in securities
            ]
            if len(targeted_secs) != len(set(targeted_secs)):
                connection.rollback()
                raise FactError(
                    "FUNDING_ATTRIBUTION_UNKNOWN",
                    "one security's proceeds cannot settle two buckets; "
                    "bind an explicit bucket-security mapping first",
                )
            if not securities and targets:
                connection.rollback()
                raise FactError(
                    "FUNDING_ATTRIBUTION_UNKNOWN",
                    "bucket-security attribution is required to settle proceeds",
                )
            explicit_map = dict(bound_bindings.get("bucket_securities") or {})
            if len(set(explicit_map.values())) != len(explicit_map.values()):
                connection.rollback()
                raise FactError(
                    "FUNDING_ATTRIBUTION_UNKNOWN",
                    "explicit bindings assign one security to two buckets",
                )
            projection = self._projection(connection, context)
            order_securities = self._order_securities(connection, context)
            # Re-verify every requested bucket against its own admitted facts and
            # rebuild the submission with current evidence.  A bucket whose cycle
            # changed (a newer reservation) is left untouched, and a cycle whose
            # security still has an unreported or pending fill is refused.
            current_requests = []
            for request in requests:
                sec = securities.get(request.bucket_id)
                order_rows = self._order_facts(
                    connection, context, {sec} if sec else None
                )
                evidence = self._evidence_from(
                    connection, context, order_rows,
                    position_flat=not any(
                        p.security_code == sec and p.quantity
                        for p in projection.positions
                    ) if sec else not projection.positions,
                )
                current_requests.append(
                    SettlementRequest(
                        request.settlement_id,
                        request.bucket_id,
                        request.cycle_id,
                        evidence,
                    )
                )

            # Replay check on the stable submission identity (settlement ids,
            # bucket ids, cycles — not the caller's read-time evidence): an
            # already-settled command returns its recorded receipt even when
            # newer orders arrived meanwhile, and a reused id carrying a
            # different stable identity is an idempotency conflict.
            stable_probe_requests = tuple(
                replace(request, evidence=_EMPTY_EVIDENCE)
                for request in current_requests
            )
            probe_payload = settle_invocation_payload(stable_probe_requests)
            probe_command = CommandEnvelope.create(
                "settle:" + settlement_id, probe_payload, state.revision
            )
            try:
                probe = resolve_command(
                    revision=state.revision,
                    processed=state.processed_commands,
                    command=probe_command,
                    operation="settle_batch",
                    invocation_payload=probe_payload,
                )
            except ProductDailyError as conflict:
                if conflict.reason_code == "idempotency_conflict":
                    connection.rollback()
                    raise FactError(
                        "IDEMPOTENCY_CONFLICT",
                        "settlement id was already used with a different "
                        "settlement identity",
                    ) from conflict
                raise
            if probe.disposition is CommandDisposition.DUPLICATE:
                if probe.receipt is None:
                    connection.rollback()
                    raise FactError("INVALID_RECEIPT", "duplicate lacks receipt")
                stored = probe.receipt.result
                reasons = tuple(
                    ReasonCode(item) for item in stored.get("reason_codes", ())
                )
                connection.commit()
                return FundingDecision(
                    operation="settle_batch",
                    accepted=require_bool(stored.get("accepted"), "accepted"),
                    replayed=True,
                    state=state,
                    reason_codes=reasons,
                    ledger_entries=(),
                    details_json=stored.get("details_json", "{}"),
                    result_digest=probe.receipt.result_digest,
                )
            if probe.disposition is CommandDisposition.CAS_CONFLICT:
                connection.commit()
                return FundingDecision(
                    operation="settle_batch",
                    accepted=False,
                    replayed=False,
                    state=state,
                    reason_codes=(ReasonCode.CAS_CONFLICT,),
                    ledger_entries=(),
                    details_json='{"expected_state_unchanged": true}',
                    result_digest=digest(
                        {
                            "operation": "settle_batch",
                            "accepted": False,
                            "reason_codes": [ReasonCode.CAS_CONFLICT.value],
                        }
                    ),
                )

            cycle_floors, default_floor = self._cycle_event_floors(
                connection, context, anchor_rev
            )
            # Fold each targeted bucket's own ledger-moved cash into its cycle:
            # fills, corrections and whole-order fees posted since the bucket's
            # current cycle began, up to that security's newest cash event.
            # Cash arriving after the newest attributed event keeps waiting for
            # the bucket's next settlement cycle, and a bucket that was never
            # prepared for this cycle does not silently absorb unattributed
            # activity.  Buckets still reserved stay reserved.
            ready_by_bucket = {
                request.bucket_id: request.evidence.settlement_ready
                for request in current_requests
            }
            folded = []
            advanced: dict[str, int] = {}
            for item in state.buckets:
                completed = any(
                    done.bucket_id == item.bucket_id and done.cycle_id == item.cycle_id
                    for done in state.completed_settlements
                )
                tracked = item.phase in (BucketPhase.RESERVED, BucketPhase.HELD) or (
                    item.bucket_id in targets
                    and ready_by_bucket.get(item.bucket_id, False)
                )
                if (
                    item.phase is BucketPhase.EMPTY
                    or completed
                    or not tracked
                ):
                    folded.append(item)
                    continue
                sec = securities.get(item.bucket_id)
                if sec is None:
                    connection.rollback()
                    raise FactError(
                        "FUNDING_ATTRIBUTION_UNKNOWN",
                        f"bucket {item.bucket_id} has no attributed security",
                    )
                order_ids = tuple(
                    oid for oid, osec in order_securities.items() if osec == sec
                )
                until = self._event_sequence_by_fact(connection, context, order_ids)
                cycle_start: int
                recorded_floor = cycle_floors.get(item.bucket_id)
                if (
                    recorded_floor is not None
                    and item.phase is not BucketPhase.CLOSED
                ):
                    cycle_start = recorded_floor
                elif item.phase is not BucketPhase.CLOSED:
                    # A freshly reserved or held cycle starts at binding.
                    cycle_start = default_floor
                else:
                    # A closed leftover cycle only folds proceeds the binding
                    # already implied: events after binding belong to it only
                    # when the account held that security at binding (an
                    # inherited position), not for a brand-new order flow.
                    held_at_binding = any(
                        p.security_code == sec and p.quantity
                        for p in self._projection_at(
                            connection, context, anchor_rev
                        ).positions
                    )
                    cycle_start = default_floor if held_at_binding else (until or 0)
                moved = self._cash_events_after(connection, context, cycle_start, until)
                delta = moved.get(sec, Decimal("0.00"))
                if until is not None and until > cycle_start:
                    # The fold consumed this bucket's attributed cash events;
                    # advance its cycle floor so a later settlement cannot
                    # count them again.
                    advanced[item.bucket_id] = until
                folded.append(
                    replace(item, cash=(item.cash + delta).quantize(Decimal("0.01")))
                    if delta
                    else item
                )
            reconciled = []
            for item in folded:
                live_request = next(
                    (r for r in current_requests if r.bucket_id == item.bucket_id),
                    None,
                )
                if (
                    item.bucket_id not in targets
                    or live_request is None
                    or not live_request.evidence.settlement_ready
                ):
                    reconciled.append(item)
                    continue
                reconciled.append(replace(item, phase=BucketPhase.CLOSED))
            closed = replace(state, buckets=tuple(reconciled))
            stable_requests = tuple(
                replace(request, evidence=_EMPTY_EVIDENCE)
                for request in current_requests
            )
            payload = settle_invocation_payload(stable_requests)
            command = CommandEnvelope.create(
                "settle:" + settlement_id, payload, state.revision
            )
            decision = settle_batch(
                closed, command, current_requests, invocation_payload=payload
            )
            if decision.accepted:
                write_rev = self._bound_account_revision(connection, context)
                self._write_funding_state(
                    connection,
                    context,
                    decision.state,
                    write_rev,
                    self._bindings_with_settled(
                        decision.state, bound_bindings, advanced
                    ),
                )
            connection.commit()
            return decision

    def prepare_funding_cycle(
        self,
        context: SyntheticContext,
        *,
        bucket_id: str,
        cycle_id: str,
    ) -> FundingDecision:
        """Top up a bucket's next cycle from the shared reserve and lock its budget."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding_row = self._read_funding_binding(connection, context)
            if binding_row is None:
                connection.rollback()
                raise FactError("FUNDING_NOT_BOUND", "no funding partition is bound")
            state, bound_rev, bound_bindings = binding_row
            anchor_rev = self._binding_anchor(bound_rev, bound_bindings)
            self._require_reconciled_partition(
                connection, context, state, anchor_rev, bound_bindings
            )
            payload = prepare_cycle_invocation_payload(
                bucket_id=bucket_id, cycle_id=cycle_id
            )
            command = CommandEnvelope.create(
                "prepare:" + bucket_id + ":" + cycle_id, payload, state.revision
            )
            decision = prepare_cycle(state, command, bucket_id=bucket_id, cycle_id=cycle_id)
            if decision.accepted and decision.state is not state:
                write_rev = self._bound_account_revision(connection, context)
                cycle_seq = self._bound_event_sequence(connection, context, write_rev)
                self._write_funding_state(
                    connection,
                    context,
                    decision.state,
                    write_rev,
                    self._bindings_mark_cycle(bucket_id, cycle_seq, bound_bindings),
                )
            connection.commit()
            return decision

    def _require_reconciled_partition(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        state: FundingState,
        bound_rev: int,
        bindings: dict[str, Any],
    ) -> None:
        """Refuse a new write while the partition trails the ledger.

        Partition cash plus order cash still waiting to fold into an open
        cycle must equal the recorded account cash.  Rows bound before
        cycle tracking carry no floors, so their open cycles account for
        every post-binding fill; a stored gap on either row shape is an
        unresolved review item, not budget.
        """
        accountable = self._attributed_cash_total(
            connection, context, state, bindings, bound_rev
        )
        try:
            account_cash = self._projection(connection, context).cash
        except FactError:
            account_cash = None
        if account_cash is not None and accountable != account_cash:
            connection.rollback()
            raise FactError(
                "FUNDING_LEDGER_MISMATCH",
                "funding partition plus pending proceeds differs from the "
                "recorded account cash",
            )

    def _read_bindings(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT bindings_json FROM funding_states WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        return json.loads(row["bindings_json"] or "{}") if row is not None else {}

    def _bindings_mark_cycle(
        self,
        bucket_id: str,
        event_sequence: int,
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist the event sequence marking the start of one bucket cycle."""
        bindings = dict(bindings)
        floors = dict(bindings.get("cycle_bound_account_revision") or {})
        floors[bucket_id] = event_sequence
        bindings["cycle_bound_account_revision"] = floors
        return bindings

    def _bindings_with_settled(
        self,
        state: FundingState,
        bindings: dict[str, Any],
        advanced: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Bindings after settlement: settled buckets close their cycle floor.

        A bucket that stays RESERVED/HELD keeps the floor it already had so
        later cash arriving in its cycle still attributes to it, advanced past
        the events this settlement just folded; a bucket whose cycle just
        closed drops the floor so its next cycle starts fresh from the next
        reservation.
        """
        bindings = dict(bindings)
        floors = dict(bindings.get("cycle_bound_account_revision") or {})
        for bucket_id, event_sequence in (advanced or {}).items():
            floors[bucket_id] = event_sequence
        settled = {item.bucket_id for item in state.completed_settlements}
        for bucket in state.buckets:
            if bucket.phase is BucketPhase.CLOSED and bucket.bucket_id in settled:
                floors.pop(bucket.bucket_id, None)
        bindings["cycle_bound_account_revision"] = floors
        return bindings

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
