"""SQLite UnitOfWork for the synthetic funding/account first-buy bridge."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
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
    CycleEntryIntent,
    CycleFillProfile,
    EntryBridgeResult,
    ExecutionReport,
    FundingBridgeService,
    LateFeeFact,
    LateFeeResult,
    LaterAttemptBinding,
    OrderAccountReplay,
    PendingObligation,
    ReductionSellProfile,
    SellExecutionReport,
    SellImpact,
    SellProfile,
    SettlementBridgeResult,
    SyntheticContext,
    SyntheticTemporalProfile,
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
from quanthus.product_daily.application.order_facts import (
    OrderWindow,
    SyntheticOrderSource,
    check_buy_route,
    check_original_reservation,
    identify_buy,
    reconcile_window,
    require_admission,
    restore_window,
    window_body,
)
from quanthus.product_daily.c13_later_signal import AttemptSlot, PlanningAttempt
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


def _report_view(
    report_id: str, lineage: dict[str, Any] | None, reason: str | None
) -> dict[str, Any]:
    return {
        "report_id": report_id,
        "status": "POSTED" if reason is None else "NEEDS_RECONCILIATION",
        "lineage": lineage,
        "reason": reason,
        "receipt_id": f"bridge-receipt-{report_id}",
    }


def _profile_body(profile: CycleFillProfile) -> dict[str, Any]:
    return {**asdict(profile), "confirmed_fee": format(profile.confirmed_fee, "f")}


def _restore_buy(raw: dict[str, Any]) -> ExecutionReport:
    return ExecutionReport(
        **{
            **raw,
            "price": Decimal(raw["price"]),
            "confirmed_fee": Decimal(raw["confirmed_fee"]),
            "intended_price_cap": Decimal(raw["intended_price_cap"]),
            "occurred_at": datetime.fromisoformat(raw["occurred_at"]),
        }
    )


def _alias_matches(original: object, candidate: object) -> bool:
    original_value = original
    candidate_value = candidate
    if isinstance(original_value, dict) and "report" in original_value:
        original_value = original_value["report"]
    if isinstance(candidate_value, dict) and "report" in candidate_value:
        candidate_value = candidate_value["report"]
    if not isinstance(original_value, dict) or not isinstance(candidate_value, dict):
        return False
    ignored = {"fact_id", "report_id"}
    original_fact = {key: value for key, value in original_value.items() if key not in ignored}
    candidate_fact = {key: value for key, value in candidate_value.items() if key not in ignored}
    return bool(candidate_fact) and all(
        key in original_fact and original_fact[key] == value
        for key, value in candidate_fact.items()
    )


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
        self._order_source: SyntheticOrderSource | None = None
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
                    CREATE TABLE bridge_conflict_register(
                      conflict_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, mode TEXT NOT NULL,
                      scope_key TEXT NOT NULL, state TEXT NOT NULL,
                      original_ref TEXT NOT NULL, evidence_json TEXT NOT NULL);
                    CREATE TABLE bridge_lots(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, lot_id TEXT NOT NULL,
                      security_code TEXT NOT NULL, cycle_id TEXT NOT NULL,
                      quantity INTEGER NOT NULL, remaining_quantity INTEGER NOT NULL,
                      cost_basis TEXT NOT NULL, remaining_cost TEXT NOT NULL,
                      source_report_id TEXT NOT NULL,
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
                    CREATE TABLE bridge_later_attempts(
                      account_id TEXT NOT NULL, mode TEXT NOT NULL, slot_identity TEXT NOT NULL,
                      parent_opportunity_id TEXT NOT NULL, later_signal_id TEXT NOT NULL,
                      policy_version TEXT NOT NULL, execution_window_id TEXT NOT NULL,
                      attempt_identity TEXT NOT NULL, command_id TEXT NOT NULL,
                      obligation_id TEXT NOT NULL,
                      result_digest TEXT NOT NULL, created_at TEXT NOT NULL,
                      PRIMARY KEY(account_id,mode,slot_identity),
                      UNIQUE(account_id,mode,obligation_id));
                    PRAGMA user_version=7;
                    """
                )
            elif version in (7, 8):
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
                    "bridge_conflict_register",
                    "bridge_lots",
                    "bridge_operations",
                    "bridge_journal",
                    "bridge_later_attempts",
                }
                if version == 8:
                    expected.add("bridge_order_source")
                if not expected.issubset({row[0] for row in tables}):
                    raise FactError("STORE_SCHEMA_MISMATCH", "cycle bridge schema is incomplete")
            else:
                raise FactError("STORE_SCHEMA_MISMATCH", "unsupported cycle bridge schema")

    def bind_order_source(
        self, context: SyntheticContext, source: SyntheticOrderSource | None
    ) -> None:
        if source is not None and (
            source.account_id != context.account_id or context.mode != "synthetic"
        ):
            raise FactError("UNAUTHORIZED_CONTEXT", "order provider belongs to another account")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if source is None:
                if version == 8:
                    raise FactError(
                        "ORDER_SOURCE_MISMATCH", "source-backed store requires its provider"
                    )
                return
            db.execute(
                "CREATE TABLE IF NOT EXISTS bridge_order_source("
                "account_id TEXT, mode TEXT, source_id TEXT, digest TEXT, manifest TEXT,"
                "PRIMARY KEY(account_id,mode))"
            )
            row = db.execute(
                "SELECT * FROM bridge_order_source WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
            if row is not None:
                if source is None or (row["digest"], row["manifest"]) != (
                    source.identity,
                    source.encoded(),
                ):
                    raise FactError("ORDER_SOURCE_MISMATCH", "reopen requires the pinned provider")
            else:
                if version == 8:
                    raise FactError(
                        "ORDER_SOURCE_MISMATCH", "provider manifest missing or wrong account"
                    )
                existing = db.execute(
                    "SELECT 1 FROM bridge_baseline WHERE account_id=? AND mode=?",
                    (context.account_id, context.mode),
                ).fetchone()
                if existing:
                    raise FactError(
                        "ORDER_SOURCE_MISMATCH",
                        "existing account requires a separate reviewed migration",
                    )
                db.execute(
                    "INSERT INTO bridge_order_source VALUES(?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        source.source_id,
                        source.identity,
                        source.encoded(),
                    ),
                )
                db.execute("PRAGMA user_version=8")
            db.commit()
        self._order_source = source

    def _require_config(self, db: sqlite3.Connection, context: SyntheticContext) -> None:
        if self._order_source is not None:
            self._source(db, context)
        elif db.execute("PRAGMA user_version").fetchone()[0] == 8:
            raise FactError("ORDER_SOURCE_MISMATCH", "this store requires its pinned order source")

    def _source(self, db: sqlite3.Connection, context: SyntheticContext) -> SyntheticOrderSource:
        source = self._order_source
        if source is None or source.account_id != context.account_id:
            raise FactError("C13_ORIGINAL_ORDER_FACT_REQUIRED", "trusted provider missing")
        row = db.execute(
            "SELECT digest,manifest FROM bridge_order_source WHERE account_id=? AND mode=?",
            (context.account_id, context.mode),
        ).fetchone()
        if row is None or (row["digest"], row["manifest"]) != (source.identity, source.encoded()):
            raise FactError("ORDER_SOURCE_MISMATCH", "stored source changed")
        return source

    def _evidence_event(
        self, db: sqlite3.Connection, context: SyntheticContext, event_id: str
    ) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT payload_json,digest FROM bridge_journal WHERE account_id=? "
            "AND mode=? AND transition_id=?",
            (context.account_id, context.mode, event_id),
        ).fetchone()
        if row is None:
            return None
        event: dict[str, Any] = json.loads(row["payload_json"])
        if digest(event) != row["digest"]:
            raise FactError("REBUILD_MISMATCH", "order journal digest differs")
        return event

    def observe_original(
        self, context: SyntheticContext, window: OrderWindow, request_id: str
    ) -> dict[str, Any]:
        body = window_body(window)
        self.receive_operation(context, "order_window", request_id, digest(body), body)
        event_id = f"order_window:{request_id}"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            source = self._source(db, context)
            existing = self._evidence_event(db, context, event_id)
            if existing is not None:
                if existing["window"] != body or existing["source_digest"] != source.identity:
                    raise FactError("IDEMPOTENCY_CONFLICT", "window request binding differs")
                expected_request = db.execute(
                    "SELECT result_json FROM bridge_operations WHERE account_id=? AND mode=? "
                    "AND kind='order_window' AND identity=?",
                    (context.account_id, context.mode, request_id),
                ).fetchone()
                if (
                    expected_request is None
                    or json.loads(expected_request["result_json"]) != existing["receipt"]
                ):
                    raise FactError("STORE_PROJECTION_MISMATCH", "window receipt differs")
                return dict(existing["receipt"])
            state = self._read(db, context)
            raw = db.execute(
                "SELECT received_at FROM bridge_operations WHERE account_id=? AND mode=? "
                "AND kind='order_window' AND identity=?",
                (context.account_id, context.mode, request_id),
            ).fetchone()
            as_received = replace(context, as_of=datetime.fromisoformat(raw["received_at"]))
            pending = self._unresolved_order_facts(db, context)
            observation = reconcile_window(
                source,
                as_received,
                window,
                state,
                self._obligation_rows(db, context),
                unresolved=pending,
            )
            if observation.closed_obligation and source.window_result(
                window, as_received.as_of
            ) != source.window_result(window, context.as_of):
                raise FactError(
                    "ORDER_WINDOW_CHANGED", "reconcile current evidence with a new request"
                )
            receipt = observation.receipt
            if observation.closed_obligation:
                oid = observation.closed_obligation.obligation_id
                changed = db.execute(
                    "UPDATE bridge_obligations SET status='CANCELLED' WHERE account_id=? "
                    "AND mode=? AND obligation_id=? AND status='OPEN'",
                    (context.account_id, context.mode, oid),
                )
                if changed.rowcount != 1:
                    raise FactError("ACCOUNT_REVISION_CONFLICT", "original obligation changed")
                self._write_funding(db, context, observation.state.funding)
                if self._fault_hook:
                    self._fault_hook("after_zero_funding_before_account")
                self._write_account(db, context, observation.state.account)
            payload = {
                "source_digest": source.identity,
                "source_id": source.source_id,
                "window": body,
                "unresolved_at_observation": pending,
                "received_at": as_received.as_of.isoformat(),
                "receipt": receipt,
                "request_id": request_id,
            }
            self._append_journal(
                db, context, transition_id=event_id, kind="order_window", payload=payload
            )
            db.execute(
                "UPDATE bridge_operations SET status='POSTED',result_json=? WHERE account_id=? "
                "AND mode=? AND kind='order_window' AND identity=?",
                (json.dumps(receipt, sort_keys=True), context.account_id, context.mode, request_id),
            )
            self._outbox(db, context, event_id, receipt)
            db.commit()
            return receipt

    def _buy_evidence(
        self,
        db: sqlite3.Connection,
        context: SyntheticContext,
        report: ExecutionReport,
        profile: CycleFillProfile,
        state: BridgeState,
        *,
        received_at: datetime,
    ) -> tuple[dict[str, Any] | None, str | None]:
        source = self._source(db, context)
        lineage = None
        try:
            order, bound = identify_buy(
                source, report, context.as_of, state, received_at=received_at
            )
            lineage = asdict(bound)
            later = db.execute(
                "SELECT * FROM bridge_later_attempts WHERE account_id=? AND mode=? "
                "AND parent_opportunity_id=?",
                (context.account_id, context.mode, order.parent_id),
            ).fetchone()
            slot = None
            if later is not None:
                entry = self._evidence_event(db, context, f"entry:{later['command_id']}")
                if not entry:
                    raise FactError("STORE_PROJECTION_MISMATCH", "later attempt lacks an entry")
                slot = {**dict(later), "execution_at": entry["later_attempt"]["execution_at"]}
            obligation = next(
                (
                    ob
                    for ob in self._obligation_rows(db, context)
                    if ob.reservation_id == bound.reservation_id
                ),
                None,
            )
            owner = (
                db.execute(
                    "SELECT parent_opportunity_id,attempt_identity FROM bridge_later_attempts "
                    "WHERE account_id=? AND mode=? AND obligation_id=?",
                    (context.account_id, context.mode, bound.reservation_id),
                ).fetchone()
                if bound.reservation_id
                else None
            )
            reservation = (
                self._evidence_event(db, context, f"entry:{order.command_id}")
                if obligation
                else None
            )
            reserved_at = (
                datetime.fromisoformat(reservation["account_recorded_at"]) if reservation else None
            )
            check_buy_route(
                order,
                bound,
                report,
                profile,
                obligation,
                slot,
                dict(owner) if owner else None,
                reserved_at,
            )
        except FactError as exc:
            return lineage, exc.reason_code
        return lineage, None

    def resolve_order_buy(
        self, context: SyntheticContext, report: ExecutionReport, profile: CycleFillProfile
    ) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db, context)
            self._validate_later_slots(db, context)
            row = db.execute(
                "SELECT received_at FROM bridge_inbox WHERE account_id=? AND "
                "mode=? AND report_id=?",
                (context.account_id, context.mode, report.report_id),
            ).fetchone()
            received = replace(context, as_of=datetime.fromisoformat(row["received_at"]))
            event_id = f"order_conflict:{report.report_id}"
            existing = self._evidence_event(db, context, event_id)
            if existing:
                raise FactError(existing["reason"], "existing order reconciliation receipt")
            lineage, reason = self._buy_evidence(
                db, context, report, profile, state, received_at=received.as_of
            )
            if reason:
                payload = {
                    "report": report.to_dict(),
                    "received_at": received.as_of.isoformat(),
                    "order_lineage": lineage,
                    "source_digest": self._source(db, context).identity,
                    "profile_data": _profile_body(profile),
                    "reason": reason,
                }
                self._append_journal(
                    db, context, transition_id=event_id, kind="order_conflict", payload=payload
                )
                self._record_conflict(
                    db,
                    context,
                    "order_fact",
                    report.report_id,
                    report.payload_digest,
                    self._source(db, context).identity,
                    json.dumps(payload, sort_keys=True),
                )
                alert_id = (
                    f"c13-unattributed-buy:{report.report_id}"
                    if reason == "C13_ORDER_ATTRIBUTION_REQUIRED"
                    else event_id
                )
                self._outbox(db, context, alert_id, payload)
                db.commit()
                raise FactError(reason, "source report retained for reconciliation")
            assert lineage is not None and lineage["reservation_id"] is not None
            return str(lineage["reservation_id"])

    def order_report(self, context: SyntheticContext, report_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            db.execute("BEGIN")
            self._source(db, context)
            self._require_context_not_before_store(db, context)
            for prefix in ("buy", "order_conflict"):
                event = self._evidence_event(db, context, f"{prefix}:{report_id}")
                if event is not None:
                    return _report_view(report_id, event.get("order_lineage"), event.get("reason"))
            return None

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
                self._require_config(connection, c)
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

    def replay_entry(
        self,
        context: SyntheticContext,
        intent: CycleEntryIntent,
        later_binding: LaterAttemptBinding | None = None,
        original_claim: dict[str, str] | None = None,
    ) -> EntryBridgeResult | None:
        with self._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM bridge_entry_receipts WHERE account_id=? AND mode=? "
                "AND command_id=?",
                (context.account_id, context.mode, intent.command.command_id),
            ).fetchone()
            if row is None:
                return None
            journal = connection.execute(
                "SELECT payload_json,digest FROM bridge_journal WHERE account_id=? AND mode=? "
                "AND transition_id=? AND kind='entry'",
                (
                    context.account_id,
                    context.mode,
                    f"entry:{intent.command.command_id}",
                ),
            ).fetchone()
            funding_payload = json.loads(row["funding_json"])
            cached = json.loads(row["result_json"])
            if journal is None:
                if (
                    later_binding is None
                    or row["accepted"] != 0
                    or not isinstance(cached, dict)
                    or cached.get("accepted") is not False
                ):
                    raise FactError("STORE_PROJECTION_MISMATCH", "entry journal is missing")
                refused = {key: value for key, value in cached.items() if key != "receipt_digest"}
                if cached.get("receipt_digest") != digest(
                    {"result": refused, "funding": funding_payload}
                ):
                    raise FactError("STORE_PROJECTION_MISMATCH", "later refusal receipt differs")
                event = refused
            else:
                event = json.loads(journal["payload_json"])
            stored_binding = event.get("later_attempt")
            expected_binding = later_binding.to_dict() if later_binding is not None else None
            if stored_binding != expected_binding:
                raise FactError("IDEMPOTENCY_CONFLICT", "entry attempt binding differs")
            if original_claim is not None and event.get("original_evidence") is not None:
                proof = event["original_evidence"]
                saved_claim = {
                    "parent_id": proof["window"]["parent_id"],
                    "security_id": proof["window"]["security_id"],
                    "end": proof["window"]["end"],
                    "decision_at": proof["decision_at"],
                    "claimed_state": proof["claimed_state"],
                }
                # ISO offsets may differ while representing the same instant.
                if any(
                    (datetime.fromisoformat(saved_claim[k]) != datetime.fromisoformat(v))
                    if k in {"decision_at", "end"}
                    else saved_claim[k] != v
                    for k, v in original_claim.items()
                ):
                    raise FactError(
                        "IDEMPOTENCY_CONFLICT", "original window or claimed status changed"
                    )
            if later_binding is not None:
                self._validate_later_slots(connection, context)
                result_keys = (
                    "accepted",
                    "replayed",
                    "quantity",
                    "reservation_id",
                    "account_revision",
                    "result_digest",
                    "command_id",
                )
                if journal is not None and (
                    journal["digest"] != digest(event)
                    or {key: event.get(key) for key in result_keys} != cached
                    or row["accepted"] != 1
                    or event.get("funding") != funding_payload
                ):
                    raise FactError(
                        "STORE_PROJECTION_MISMATCH", "later entry receipt differs from journal"
                    )
            original = restore_entry_intent(event["intent"])
            if original.command.body_digest != intent.command.body_digest:
                raise FactError("IDEMPOTENCY_CONFLICT", "entry command payload differs")
            funding = restore_funding_envelope(funding_payload)
            return EntryBridgeResult(
                bool(row["accepted"]),
                True,
                int(event["quantity"]),
                event["reservation_id"],
                funding,
                int(event["account_revision"]),
                str(event["result_digest"]),
                str(event["command_id"]),
                None,
                original,
            )

    def read_snapshot(self, context: SyntheticContext) -> BridgeSnapshot:
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                snapshot = BridgeSnapshot(
                    self._read(connection, context),
                    self._obligation_rows(connection, context),
                    self._lot_rows(connection, context),
                    self._latest_received_at(connection, context),
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
            rows = self._lot_rows(connection, context)
            connection.commit()
        return rows

    def latest_received_at(self, context: SyntheticContext) -> datetime | None:
        with self._connect() as connection:
            return self._latest_received_at(connection, context)

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
                self._require_context_not_before_store(connection, context)
                row = connection.execute(
                    "SELECT * FROM bridge_operations "
                    "WHERE account_id=? AND mode=? AND kind=? AND identity=?",
                    (context.account_id, context.mode, kind, identity),
                ).fetchone()
                if row is not None:
                    if row["payload_digest"] == payload_digest:
                        return str(row["receipt_id"])
                    if row["status"] != "POSTED" and kind == "settlement":
                        self._record_conflict(
                            connection,
                            context,
                            f"{kind}_retry",
                            identity,
                            row["payload_digest"],
                            payload_digest,
                            json.dumps(payload, sort_keys=True),
                            resolved=True,
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
                        context.as_of.isoformat(),
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
                    original_operation = connection.execute(
                        "SELECT payload_json FROM bridge_operations "
                        "WHERE account_id=? AND mode=? AND identity=?",
                        (context.account_id, context.mode, source["report_id"]),
                    ).fetchone()
                    original_inbox = connection.execute(
                        "SELECT payload_json FROM bridge_inbox "
                        "WHERE account_id=? AND mode=? AND report_id=?",
                        (context.account_id, context.mode, source["report_id"]),
                    ).fetchone()
                    original_payload = (
                        original_operation["payload_json"]
                        if original_operation is not None
                        else original_inbox["payload_json"]
                        if original_inbox is not None
                        else None
                    )
                    resolved = original_payload is not None and _alias_matches(
                        json.loads(original_payload), payload
                    )
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{source_id}:{source_sequence}",
                        str(source["receipt_id"]),
                        new_digest,
                        json.dumps(payload, sort_keys=True),
                        resolved=resolved,
                        scope_key=f"source:{source_id}:{source_sequence}",
                    )
                    connection.commit()
                    raise FactError("SOURCE_FACT_DUPLICATE", "source alias differs")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def commit_entry(
        self,
        context: SyntheticContext,
        expected: str,
        result: EntryBridgeResult,
        later_binding: LaterAttemptBinding | None = None,
        original_evidence: dict[str, Any] | None = None,
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
                if later_binding is not None:
                    self._validate_later_slots(connection, context)
                    source = self._source(connection, context)
                    if original_evidence is None:
                        raise FactError("C13_ORIGINAL_ORDER_FACT_REQUIRED", "no window receipt")
                    window = restore_window(original_evidence["window"])
                    proof = require_admission(
                        source,
                        window,
                        datetime.fromisoformat(original_evidence["decision_at"]),
                        context.as_of,
                        original_evidence["claimed_state"],
                    )
                    if (
                        proof != original_evidence
                        or window.parent_id != later_binding.parent_opportunity_id
                    ):
                        raise FactError(
                            "C13_ORIGINAL_ORDER_FACT_REQUIRED", "changed admission evidence"
                        )
                    check_original_reservation(
                        source.window_result(window, context.as_of),
                        source,
                        window,
                        state,
                        self._obligation_rows(connection, context),
                    )
                    if (
                        connection.execute(
                            "SELECT 1 FROM bridge_entry_receipts "
                            "WHERE account_id=? AND mode=? AND command_id=?",
                            (context.account_id, context.mode, result.command_id),
                        ).fetchone()
                        is not None
                    ):
                        raise FactError(
                            "C13_ENTRY_RECEIPT_ALREADY_PRESENT", "retry the original entry command"
                        )
                    occupied = connection.execute(
                        "SELECT count(*) FROM bridge_later_attempts "
                        "WHERE account_id=? AND mode=? AND slot_identity=?",
                        (context.account_id, context.mode, later_binding.slot_identity),
                    ).fetchone()[0]
                    if occupied:
                        raise FactError(
                            "LATER_ATTEMPT_ALREADY_CONSUMED",
                            "one later attempt slot is already consumed",
                        )
                if result.accepted:
                    if later_binding is not None:
                        if result.obligation is None or result.replayed:
                            raise FactError(
                                "INVALID_C13_DECISION", "later reservation lacks a fresh obligation"
                            )
                        if self._unresolved_order_facts(connection, context):
                            raise FactError(
                                "C13_ORIGINAL_FACT_UNRESOLVED",
                                "unreconciled raw original fact precedes later admission",
                            )
                        connection.execute(
                            "INSERT INTO bridge_later_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                context.account_id,
                                context.mode,
                                later_binding.slot_identity,
                                later_binding.parent_opportunity_id,
                                later_binding.later_signal_id,
                                later_binding.policy_version,
                                later_binding.execution_window_id,
                                later_binding.attempt_identity,
                                result.command_id,
                                result.obligation.obligation_id,
                                result.result_digest,
                                result.intent.snapshot.as_of.isoformat()
                                if result.intent is not None
                                else context.as_of.isoformat(),
                            ),
                        )
                        if self._fault_hook is not None:
                            self._fault_hook("after_later_slot_before_entry")
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
                                **(
                                    {"original_evidence": original_evidence}
                                    if later_binding
                                    else {}
                                ),
                                "later_attempt": (
                                    later_binding.to_dict() if later_binding is not None else None
                                ),
                            },
                        )
                entry_receipt = self._entry_dict(result)
                funding_receipt = funding_state_envelope(result.funding)
                if later_binding is not None and not result.accepted:
                    if result.intent is None:
                        raise FactError("INVALID_C13_DECISION", "later refusal lacks an intent")
                    entry_receipt["original_evidence"] = original_evidence
                    entry_receipt["intent"] = entry_intent_to_dict(result.intent)
                    entry_receipt["later_attempt"] = later_binding.to_dict()
                    entry_receipt["receipt_digest"] = digest(
                        {"result": entry_receipt, "funding": funding_receipt}
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO bridge_entry_receipts VALUES(?,?,?,?,?,?)",
                    (
                        c.account_id,
                        c.mode,
                        result.command_id,
                        1 if result.accepted else 0,
                        json.dumps(entry_receipt, sort_keys=True),
                        json.dumps(funding_receipt, sort_keys=True),
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
                self._require_context_not_before_store(connection, context)
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
                        context.as_of.isoformat(),
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
                    original = connection.execute(
                        "SELECT payload_json FROM bridge_inbox "
                        "WHERE account_id=? AND mode=? AND report_id=?",
                        (context.account_id, context.mode, source["report_id"]),
                    ).fetchone()
                    resolved = original is not None and _alias_matches(
                        json.loads(original["payload_json"]), report.to_dict()
                    )
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{report.source_id}:{report.source_sequence}",
                        str(source["receipt_id"]),
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                        resolved=resolved,
                        scope_key=f"source:{report.source_id}:{report.source_sequence}",
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
                self._require_context_not_before_store(connection, context)
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
                    original = connection.execute(
                        "SELECT payload_json FROM bridge_inbox WHERE account_id=? AND mode=? "
                        "AND report_id=?",
                        (context.account_id, context.mode, source["report_id"]),
                    ).fetchone()
                    resolved = original is not None and _alias_matches(
                        json.loads(original["payload_json"]), report.to_dict()
                    )
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{report.source_id}:{report.source_sequence}",
                        str(source["receipt_id"]),
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                        resolved=resolved,
                        scope_key=f"source:{report.source_id}:{report.source_sequence}",
                    )
                    connection.commit()
                    raise FactError(
                        "SOURCE_FACT_DUPLICATE", f"source already posted by {source['report_id']}"
                    )
                self._validate_later_slots(connection, context)
                later = connection.execute(
                    "SELECT parent_opportunity_id,attempt_identity FROM bridge_later_attempts "
                    "WHERE account_id=? AND mode=? AND obligation_id=?",
                    (context.account_id, context.mode, obligation.obligation_id),
                ).fetchone()
                lineage = None
                if self._order_source is not None:
                    row = connection.execute(
                        "SELECT received_at FROM bridge_inbox WHERE account_id=? "
                        "AND mode=? AND report_id=?",
                        (context.account_id, context.mode, report.report_id),
                    ).fetchone()
                    received = replace(context, as_of=datetime.fromisoformat(row["received_at"]))
                    lineage, reason = self._buy_evidence(
                        connection, context, report, profile, state, received_at=received.as_of
                    )
                    if (
                        reason
                        or not lineage
                        or lineage["reservation_id"] != obligation.reservation_id
                    ):
                        raise FactError(
                            reason or "ORDER_SOURCE_CONFLICT", "order changed before posting"
                        )
                elif later is not None:
                    # The generic buy report has no order identity to distinguish two attempts.
                    evidence = {
                        "report": report.to_dict(),
                        "parent_opportunity_id": later["parent_opportunity_id"],
                        "later_attempt_identity": later["attempt_identity"],
                        "reason": "C13_ORDER_ATTRIBUTION_REQUIRED",
                    }
                    self._record_conflict(
                        connection,
                        context,
                        "c13_buy_attribution",
                        report.report_id,
                        str(later["attempt_identity"]),
                        receipt_id,
                        json.dumps(evidence, sort_keys=True),
                        scope_key=f"c13:{later['parent_opportunity_id']}",
                    )
                    alert_id = f"c13-unattributed-buy:{report.report_id}"
                    if (
                        connection.execute(
                            "SELECT 1 FROM bridge_outbox "
                            "WHERE account_id=? AND mode=? AND report_id=?",
                            (context.account_id, context.mode, alert_id),
                        ).fetchone()
                        is None
                    ):
                        self._outbox(connection, context, alert_id, evidence)
                    connection.commit()
                    raise FactError(
                        "C13_ORDER_ATTRIBUTION_REQUIRED",
                        "unattributed buy report retained; later reservation remains open",
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
                    "INSERT INTO bridge_lots VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        context.account_id,
                        context.mode,
                        f"lot:{report.report_id}",
                        report.security_code,
                        obligation.cycle_id,
                        report.quantity,
                        report.quantity,
                        format(report.price * report.quantity, "f"),
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
                        **({"order_lineage": lineage} if lineage else {}),
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
                        "result": self._buy_dict(result),
                        "lot": {
                            "lot_id": f"lot:{report.report_id}",
                            "security_code": report.security_code,
                            "cycle_id": obligation.cycle_id,
                            "quantity": report.quantity,
                            "remaining_quantity": report.quantity,
                            "cost_basis": format(report.price * report.quantity, "f"),
                            "remaining_cost": format(report.price * report.quantity, "f"),
                            "source_report_id": report.report_id,
                            "occurred_at": report.occurred_at.isoformat(),
                            "source_id": report.source_id,
                            "source_sequence": report.source_sequence,
                        },
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
        profile: SellProfile | ReductionSellProfile,
    ) -> BuyBridgeResult | None:
        expected = digest({"report": report.to_dict(), "profile": profile.payload_digest})
        return self._replay_buy_operation(
            context,
            "sell",
            report.report_id,
            expected,
            {"report": report.to_dict(), "profile_digest": profile.payload_digest},
        )

    def commit_sell(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        receipt_id: str,
        report: SellExecutionReport,
        profile: SellProfile | ReductionSellProfile,
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
                    original = connection.execute(
                        "SELECT payload_json FROM bridge_operations WHERE account_id=? AND mode=? "
                        "AND identity=?",
                        (context.account_id, context.mode, source["report_id"]),
                    ).fetchone()
                    resolved = original is not None and _alias_matches(
                        json.loads(original["payload_json"]), report.to_dict()
                    )
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{report.source_id}:{report.source_sequence}",
                        str(source["receipt_id"]),
                        receipt_id,
                        json.dumps(report.to_dict(), sort_keys=True),
                        resolved=resolved,
                        scope_key=f"source:{report.source_id}:{report.source_sequence}",
                    )
                    connection.commit()
                    raise FactError("SOURCE_FACT_DUPLICATE", "sell source already posted")
                updated_lot = connection.execute(
                    "UPDATE bridge_lots SET remaining_quantity=?,remaining_cost=? "
                    "WHERE account_id=? AND mode=? AND lot_id=? AND remaining_quantity=?",
                    (
                        impact.lot.remaining_quantity,
                        format(impact.lot.remaining_cost, "f"),
                        context.account_id,
                        context.mode,
                        impact.lot.lot_id,
                        report.quantity + impact.lot.remaining_quantity,
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
                                "profile_kind": profile.profile_kind,
                                "lot_id": impact.lot.lot_id,
                                "cycle_id": report.cycle_id,
                                "remaining_quantity": impact.lot.remaining_quantity,
                                "remaining_cost": format(impact.lot.remaining_cost, "f"),
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
                        "profile_kind": profile.profile_kind,
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
                            "remaining_quantity": impact.lot.remaining_quantity,
                            "remaining_cost": format(impact.lot.remaining_cost, "f"),
                            "cost_basis": format(impact.lot.cost_basis, "f"),
                            "source_report_id": impact.lot.source_report_id,
                        },
                        "result": self._buy_dict(result),
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
            conflicts_clear = (
                connection.execute(
                    "SELECT count(*) FROM bridge_conflict_register "
                    "WHERE account_id=? AND mode=? AND state='UNRESOLVED'",
                    (context.account_id, context.mode),
                ).fetchone()[0]
                == 0
            )
            unposted_raw = self._has_unreconciled_unposted(connection, context)
            conflicts_clear = conflicts_clear and not unposted_raw
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
                if connection.execute(
                    "SELECT count(*) FROM bridge_conflict_register WHERE account_id=? "
                    "AND mode=? AND state='UNRESOLVED'",
                    (context.account_id, context.mode),
                ).fetchone()[0]:
                    raise FactError(
                        "SETTLEMENT_NOT_READY", "unresolved conflict arrived before commit"
                    )
                if self._has_unreconciled_unposted(connection, context):
                    raise FactError(
                        "SETTLEMENT_NOT_READY", "unreconciled raw fact arrived before commit"
                    )
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
        row = self._replay_operation(
            context,
            "late_fee",
            fact.fact_id,
            fact.payload_digest,
            {
                "fact_id": fact.fact_id,
                "bucket_id": fact.bucket_id,
                "cycle_id": fact.cycle_id,
                "cash_delta": format(fact.cash_delta, "f"),
                "source_id": fact.source_id,
                "source_sequence": fact.source_sequence,
                "occurred_at": fact.occurred_at.isoformat(),
            },
        )
        if row is None or row["status"] != "POSTED":
            return None
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
                    original = connection.execute(
                        "SELECT payload_json FROM bridge_operations WHERE account_id=? AND mode=? "
                        "AND identity=?",
                        (context.account_id, context.mode, source["report_id"]),
                    ).fetchone()
                    resolved = original is not None and _alias_matches(
                        json.loads(original["payload_json"]),
                        {"cash_delta": format(fact.cash_delta, "f")},
                    )
                    self._record_conflict(
                        connection,
                        context,
                        "source_fact",
                        f"{fact.source_id}:{fact.source_sequence}",
                        str(source["receipt_id"]),
                        result.receipt_id,
                        json.dumps(fact.payload_digest),
                        resolved=resolved,
                        scope_key=f"source:{fact.source_id}:{fact.source_sequence}",
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
        return self._replay_account(context, compare_projections=True).state

    def reconstruct_order_account(self, context: SyntheticContext) -> OrderAccountReplay:
        if self._order_source is None:
            raise FactError(
                "C13_ORIGINAL_ORDER_FACT_REQUIRED", "source-backed replay requires a provider"
            )
        return self._replay_account(context, compare_projections=False)

    def _replay_account(
        self, context: SyntheticContext, *, compare_projections: bool
    ) -> OrderAccountReplay:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if compare_projections else "BEGIN")
            self._require_config(connection, context)
            baseline = connection.execute(
                "SELECT * FROM bridge_baseline WHERE account_id=? AND mode=?",
                (context.account_id, context.mode),
            ).fetchone()
            if baseline is None:
                raise FactError("BASELINE_NOT_FOUND", "baseline unavailable")
            account = AccountProjection(0, 0, Decimal(baseline["cash"]), (), 0)
            funding = restore_funding_envelope(json.loads(baseline["funding_json"]))
            obligations: dict[str, PendingObligation] = {}
            lots: dict[str, BuyLot] = {}
            later_orders: dict[str, dict[str, Any]] = {}
            reservation_times: dict[str, datetime] = {}
            later_owners: dict[str, dict[str, Any]] = {}
            reports: dict[str, dict[str, Any]] = {}
            windows: dict[str, dict[str, Any]] = {}
            last_recorded_at: datetime | None = None
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
                if self._order_source is not None:
                    recorded = datetime.fromisoformat(event["account_recorded_at"])
                    if recorded > context.as_of or (
                        last_recorded_at is not None and recorded < last_recorded_at
                    ):
                        raise FactError(
                            "CONTEXT_TIME_REGRESSION", "journal is outside the requested clock"
                        )
                    last_recorded_at = recorded
                if kind == "entry":
                    if event.get("intent") is None:
                        raise FactError("REBUILD_MISMATCH", "entry journal lacks typed intent")
                    intent = restore_entry_intent(event["intent"])
                    entry_result = calculate_entry_transition(
                        BridgeState(account, funding, account.revision),
                        intent,
                        context.source_id,
                    )
                    if self._entry_dict(entry_result) != {
                        key: event[key]
                        for key in (
                            "accepted",
                            "replayed",
                            "quantity",
                            "reservation_id",
                            "account_revision",
                            "result_digest",
                            "command_id",
                        )
                    }:
                        raise FactError("REBUILD_MISMATCH", "entry result identity differs")
                    if entry_result.obligation is not None:
                        reservation_times[entry_result.obligation.reservation_id] = (
                            intent.snapshot.as_of
                        )
                        obligations[entry_result.obligation.obligation_id] = entry_result.obligation
                    later_binding = event.get("later_attempt")
                    if later_binding is not None and entry_result.obligation is not None:
                        original_proof = event.get("original_evidence")
                        if original_proof is None and self._order_source is not None:
                            raise FactError("REBUILD_MISMATCH", "missing original window proof")
                        if original_proof is not None:
                            source = self._source(connection, context)
                            window = restore_window(original_proof["window"])
                            expected_proof = require_admission(
                                source,
                                window,
                                datetime.fromisoformat(original_proof["decision_at"]),
                                intent.snapshot.as_of,
                                original_proof["claimed_state"],
                            )
                            check_original_reservation(
                                source.window_result(window, intent.snapshot.as_of),
                                source,
                                window,
                                BridgeState(account, funding, account.revision),
                                tuple(obligations.values()),
                            )
                            if (
                                expected_proof != original_proof
                                or window.parent_id != later_binding["parent_opportunity_id"]
                            ):
                                raise FactError(
                                    "REBUILD_MISMATCH", "original window admission differs"
                                )
                        later_orders[later_binding["parent_opportunity_id"]] = {
                            **later_binding,
                            "obligation_id": entry_result.obligation.obligation_id,
                            "created_at": intent.snapshot.as_of.isoformat(),
                        }
                        later_owners[entry_result.obligation.obligation_id] = later_orders[
                            later_binding["parent_opportunity_id"]
                        ]
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
                elif kind == "order_window":
                    source = self._source(connection, context)
                    observation = reconcile_window(
                        source,
                        replace(context, as_of=datetime.fromisoformat(event["received_at"])),
                        restore_window(event["window"]),
                        BridgeState(account, funding, account.revision),
                        tuple(obligations.values()),
                        unresolved=event["unresolved_at_observation"],
                    )
                    if (
                        event["source_digest"] != source.identity
                        or observation.receipt != event["receipt"]
                    ):
                        raise FactError("REBUILD_MISMATCH", "source window receipt differs")
                    account, funding = observation.state.account, observation.state.funding
                    windows[event["request_id"]] = observation.receipt
                    if observation.closed_obligation:
                        obligations[observation.closed_obligation.obligation_id] = (
                            observation.closed_obligation
                        )
                elif kind == "order_conflict":
                    source = self._source(connection, context)
                    conflict_report = _restore_buy(event["report"])
                    conflict_profile = CycleFillProfile(
                        **{
                            **event["profile_data"],
                            "confirmed_fee": Decimal(event["profile_data"]["confirmed_fee"]),
                        }
                    )
                    conflict_lineage = None
                    try:
                        order, bound = identify_buy(
                            source,
                            conflict_report,
                            datetime.fromisoformat(event["account_recorded_at"]),
                            BridgeState(account, funding, account.revision),
                            received_at=datetime.fromisoformat(event["received_at"]),
                        )
                        conflict_lineage = asdict(bound)
                        check_buy_route(
                            order,
                            bound,
                            conflict_report,
                            conflict_profile,
                            obligations.get(bound.reservation_id or ""),
                            later_orders.get(order.parent_id),
                            later_owners.get(bound.reservation_id or ""),
                            reservation_times.get(bound.reservation_id or ""),
                        )
                    except FactError as exc:
                        if (
                            exc.reason_code != event["reason"]
                            or conflict_lineage != event["order_lineage"]
                            or source.identity != event["source_digest"]
                        ):
                            raise FactError(
                                "REBUILD_MISMATCH", "unreconciled order provenance differs"
                            ) from exc
                    else:
                        raise FactError("REBUILD_MISMATCH", "order conflict has no source basis")
                    reports[conflict_report.report_id] = _report_view(
                        conflict_report.report_id, conflict_lineage, event["reason"]
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
                    obligation = obligations.get(obligation_id)
                    if obligation is None or obligation.status != "OPEN":
                        raise FactError("REBUILD_MISMATCH", "buy obligation is not derivable")
                    raw_lineage = event.get("order_lineage")
                    if raw_lineage is None and self._order_source is not None:
                        raise FactError("REBUILD_MISMATCH", "missing buy order lineage")
                    if raw_lineage is not None:
                        order, bound = identify_buy(
                            self._source(connection, context),
                            buy_report,
                            datetime.fromisoformat(event["account_recorded_at"]),
                            BridgeState(account, funding, account.revision),
                            received_at=datetime.fromisoformat(raw_lineage["received_at"]),
                        )
                        check_buy_route(
                            order,
                            bound,
                            buy_report,
                            buy_profile,
                            obligation,
                            later_orders.get(order.parent_id),
                            later_owners.get(bound.reservation_id or ""),
                            reservation_times.get(bound.reservation_id or ""),
                        )
                        if raw_lineage != asdict(bound):
                            raise FactError("REBUILD_MISMATCH", "buy lineage differs from provider")
                        reports[buy_report.report_id] = _report_view(
                            buy_report.report_id, raw_lineage, None
                        )
                    buy_impact = apply_confirmed_buy_transition(
                        BridgeState(account, funding, account.revision),
                        buy_report,
                        buy_profile,
                        obligation,
                    )
                    account, funding = buy_impact.account, buy_impact.funding
                    obligations[obligation_id] = replace(obligation, status="FILLED")
                    lot = BuyLot(
                        f"lot:{buy_report.report_id}",
                        buy_report.security_code,
                        obligation.cycle_id,
                        buy_report.quantity,
                        buy_report.quantity,
                        buy_report.price * buy_report.quantity,
                        buy_report.price * buy_report.quantity,
                        buy_report.report_id,
                        buy_report.occurred_at,
                        buy_report.source_id,
                        buy_report.source_sequence,
                    )
                    lots[lot.lot_id] = lot
                    actual_result = self._buy_dict(
                        BuyBridgeResult(
                            f"bridge-receipt-{buy_report.report_id}",
                            "POSTED",
                            buy_impact.account,
                            buy_impact.funding,
                            buy_impact.account.revision,
                            buy_impact.postings,
                        )
                    )
                    if event.get("result") != actual_result:
                        expected = event.get("result", {})
                        diff = {
                            key: {"expected": expected.get(key), "actual": actual_result.get(key)}
                            for key in set(expected) | set(actual_result)
                            if expected.get(key) != actual_result.get(key)
                        }
                        raise FactError("REBUILD_MISMATCH", f"buy result identity differs: {diff}")
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
                    profile_kind = event.get("profile_kind")
                    if not isinstance(profile_kind, str) or profile_kind not in (
                        "full_position",
                        "residual_lot_reduction",
                    ):
                        raise FactError("REBUILD_MISMATCH", "sell profile kind is unsupported")
                    profile_type = (
                        ReductionSellProfile
                        if profile_kind == "residual_lot_reduction"
                        else SellProfile
                    )
                    sell_profile = profile_type(
                        raw_profile["profile_version"],
                        raw_profile["source_id"],
                        raw_profile["order_total_quantity"],
                        raw_profile["final_cumulative_fill_quantity"],
                        raw_profile["cancelled_quantity"],
                        Decimal(raw_profile["confirmed_fee"]),
                        raw_profile["terminal"],
                    )
                    matches = [
                        item
                        for item in lots.values()
                        if item.security_code == sell_report.security_code
                        and item.cycle_id == sell_report.cycle_id
                        and item.remaining_quantity >= sell_report.quantity
                        and item.remaining_quantity > 0
                    ]
                    if len(matches) != 1:
                        raise FactError("REBUILD_MISMATCH", "sell lot is not derivable")
                    lot = matches[0]
                    sell_impact = apply_confirmed_sell_transition(
                        BridgeState(account, funding, account.revision),
                        sell_report,
                        sell_profile,
                        lot,
                    )
                    account, funding = sell_impact.account, sell_impact.funding
                    lots[lot.lot_id] = sell_impact.lot
                    actual_result = self._buy_dict(
                        BuyBridgeResult(
                            f"sell-receipt-{sell_report.report_id}",
                            "POSTED",
                            sell_impact.account,
                            sell_impact.funding,
                            sell_impact.account.revision,
                            sell_impact.postings,
                        )
                    )
                    if event.get("result") != actual_result:
                        raise FactError("REBUILD_MISMATCH", "sell result identity differs")
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
                        account.revision + 1,
                        account.event_sequence,
                        account.cash,
                        account.positions,
                        account.fact_count,
                    )
                    actual_result = self._settlement_dict(
                        SettlementBridgeResult(
                            request.settlement_id,
                            funding,
                            settled,
                            account.revision,
                            f"settlement-receipt-{request.settlement_id}",
                        )
                    )
                    if event.get("result") != actual_result:
                        raise FactError("REBUILD_MISMATCH", "settlement result identity differs")
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
                    late = replace(late, receipt_id=f"late_fee-receipt-{fact.fact_id}")
                    account, funding = late.account, late.funding
                    if event.get("result") != self._late_dict(late):
                        raise FactError("REBUILD_MISMATCH", "late fee result identity differs")
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
            replay = OrderAccountReplay(
                BridgeState(account, funding, account.revision),
                tuple(obligations[key] for key in sorted(obligations)),
                reports,
                windows,
            )
            if not compare_projections:
                return replay
            state = self._read(connection, context)
            if account != state.account:
                raise FactError(
                    "REBUILD_MISMATCH",
                    "account projection differs: "
                    f"rebuilt={account.to_dict()} stored={state.account.to_dict()}",
                )
            if funding_state_envelope(funding) != funding_state_envelope(state.funding):
                raise FactError("REBUILD_MISMATCH", "funding projection differs")
            stored_obligations = self._obligation_rows(connection, context)
            if (
                self._order_source is not None or stored_obligations
            ) and stored_obligations != tuple(obligations[key] for key in sorted(obligations)):
                raise FactError(
                    "STORE_PROJECTION_MISMATCH", "obligation projection differs from journal"
                )
            stored_lots = self._lot_rows(connection, context)
            derived_lots = tuple(lots[key] for key in sorted(lots))
            if (self._order_source is not None or stored_lots) and tuple(
                (
                    item.lot_id,
                    item.security_code,
                    item.cycle_id,
                    item.quantity,
                    item.remaining_quantity,
                    item.cost_basis,
                    item.remaining_cost,
                    item.source_report_id,
                )
                for item in stored_lots
            ) != tuple(
                (
                    item.lot_id,
                    item.security_code,
                    item.cycle_id,
                    item.quantity,
                    item.remaining_quantity,
                    item.cost_basis,
                    item.remaining_cost,
                    item.source_report_id,
                )
                for item in derived_lots
            ):
                raise FactError("STORE_PROJECTION_MISMATCH", "lot projection differs from journal")
            self._validate_later_slots(connection, context, restore_empty=True)
            connection.commit()
            return replay

    def _validate_later_slots(
        self,
        connection: sqlite3.Connection,
        context: SyntheticContext,
        *,
        restore_empty: bool = False,
    ) -> None:
        derived: dict[str, tuple[str, ...]] = {}
        obligations: set[str] = set()
        for row in connection.execute(
            "SELECT transition_id, payload_json, digest FROM bridge_journal "
            "WHERE account_id=? AND mode=? AND kind='entry' ORDER BY sequence",
            (context.account_id, context.mode),
        ):
            try:
                event = json.loads(row["payload_json"])
                if (
                    not isinstance(event, dict)
                    or digest(event) != row["digest"]
                    or "later_attempt" not in event
                ):
                    raise ValueError("invalid entry journal")
                raw = event["later_attempt"]
                if raw is None:
                    continue
                if not isinstance(raw, dict):
                    raise ValueError("invalid later slot")
                fields = (
                    "slot_identity",
                    "parent_opportunity_id",
                    "later_signal_id",
                    "policy_version",
                    "execution_window_id",
                    "attempt_identity",
                    "execution_at",
                )
                if set(raw) != set(fields) or not all(
                    isinstance(raw[key], str) and raw[key].strip() for key in fields
                ):
                    raise ValueError("invalid later slot fields")
                slot = AttemptSlot(context.account_id, raw["parent_opportunity_id"])
                execution_at = datetime.fromisoformat(raw["execution_at"])
                if execution_at.utcoffset() is None:
                    raise ValueError("missing execution timezone")
                attempt = PlanningAttempt(
                    slot,
                    raw["later_signal_id"],
                    raw["policy_version"],
                    raw["execution_window_id"],
                    execution_at,
                )
                intent = restore_entry_intent(event["intent"])
                command, reservation, result = (
                    event["command_id"],
                    event["reservation_id"],
                    event["result_digest"],
                )
                expert = intent.candidate.expert_entry
                if (
                    event["accepted"] is not True
                    or event["replayed"] is not False
                    or row["transition_id"] != f"entry:{command}"
                    or command != intent.command.command_id
                    or command != intent.candidate.opportunity_id
                    or command != attempt.identity
                    or command != raw["attempt_identity"]
                    or slot.identity != raw["slot_identity"]
                    or expert is None
                    or expert.source_digest != raw["later_signal_id"]
                    or not isinstance(reservation, str)
                    or not reservation
                    or not isinstance(result, str)
                    or not result
                    or slot.identity in derived
                    or reservation in obligations
                ):
                    raise ValueError("later slot binding differs")
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise FactError("REBUILD_MISMATCH", "later slot journal is invalid") from exc
            derived[slot.identity] = (
                context.account_id,
                context.mode,
                slot.identity,
                slot.parent_opportunity_id,
                attempt.later_signal_id,
                attempt.policy_version,
                attempt.execution_window_id,
                attempt.identity,
                command,
                reservation,
                result,
                intent.snapshot.as_of.isoformat(),
            )
            obligations.add(reservation)
        stored = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT account_id,mode,slot_identity,parent_opportunity_id,later_signal_id,"
                "policy_version,execution_window_id,attempt_identity,command_id,obligation_id,"
                "result_digest,created_at FROM bridge_later_attempts "
                "WHERE account_id=? AND mode=? ORDER BY slot_identity",
                (context.account_id, context.mode),
            )
        )
        expected = tuple(derived[key] for key in sorted(derived))
        if expected and not stored and restore_empty:
            connection.executemany(
                "INSERT INTO bridge_later_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", expected
            )
        elif expected != stored:
            raise FactError("STORE_PROJECTION_MISMATCH", "later slots differ from journal")

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

    def _lot_rows(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> tuple[BuyLot, ...]:
        rows = connection.execute(
            "SELECT l.*, i.payload_json AS buy_payload_json FROM bridge_lots l "
            "LEFT JOIN bridge_inbox i ON i.account_id=l.account_id AND i.mode=l.mode "
            "AND i.report_id=l.source_report_id WHERE l.account_id=? AND l.mode=? "
            "ORDER BY l.lot_id",
            (context.account_id, context.mode),
        ).fetchall()
        lots: list[BuyLot] = []
        for row in rows:
            raw = json.loads(row["buy_payload_json"]) if row["buy_payload_json"] else {}
            occurred = raw.get("occurred_at")
            if not isinstance(occurred, str):
                raise FactError("STORE_PROJECTION_MISMATCH", "lot buy occurrence is missing")
            lots.append(
                BuyLot(
                    row["lot_id"],
                    row["security_code"],
                    row["cycle_id"],
                    row["quantity"],
                    row["remaining_quantity"],
                    Decimal(row["cost_basis"]),
                    Decimal(row["remaining_cost"]),
                    row["source_report_id"],
                    datetime.fromisoformat(occurred),
                    str(raw.get("source_id", "")),
                    str(raw.get("source_sequence", "")),
                )
            )
        return tuple(lots)

    def _has_unreconciled_unposted(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> bool:
        buy_rows = connection.execute(
            "SELECT report_id,payload_json FROM bridge_inbox WHERE account_id=? AND mode=? "
            "AND status='RECEIVED'",
            (context.account_id, context.mode),
        ).fetchall()
        for row in buy_rows:
            payload = json.loads(row["payload_json"])
            source_id = payload.get("source_id")
            source_sequence = payload.get("source_sequence")
            original = connection.execute(
                "SELECT payload_json FROM bridge_inbox WHERE account_id=? AND mode=? "
                "AND status='POSTED' AND report_id<>?",
                (context.account_id, context.mode, row["report_id"]),
            ).fetchall()
            if not any(
                (candidate := json.loads(item["payload_json"])).get("source_id") == source_id
                and candidate.get("source_sequence") == source_sequence
                and _alias_matches(candidate, payload)
                for item in original
            ):
                return True
        operation_rows = connection.execute(
            "SELECT identity,payload_json FROM bridge_operations WHERE account_id=? "
            "AND mode=? AND status='RECEIVED' AND kind IN ('sell','late_fee')",
            (context.account_id, context.mode),
        ).fetchall()
        for row in operation_rows:
            payload = json.loads(row["payload_json"])
            report = payload.get("report", payload)
            source_id = report.get("source_id")
            source_sequence = report.get("source_sequence")
            source = connection.execute(
                "SELECT report_id FROM bridge_source_facts WHERE account_id=? AND mode=? "
                "AND source_id=? AND source_sequence=?",
                (context.account_id, context.mode, source_id, source_sequence),
            ).fetchone()
            if source is None:
                return True
            original = connection.execute(
                "SELECT payload_json FROM bridge_operations WHERE account_id=? AND mode=? "
                "AND identity=?",
                (context.account_id, context.mode, source["report_id"]),
            ).fetchone()
            if original is None or not _alias_matches(
                json.loads(original["payload_json"]), payload
            ):
                return True
        return False

    def _unresolved_order_facts(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> bool:
        if self._has_unreconciled_unposted(connection, context):
            return True
        return (
            connection.execute(
                "SELECT 1 FROM bridge_conflict_register WHERE account_id=? AND mode=? "
                "AND state='UNRESOLVED' LIMIT 1",
                (context.account_id, context.mode),
            ).fetchone()
            is not None
        )

    def _latest_received_at(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> datetime | None:
        values = [
            row[0]
            for row in connection.execute(
                "SELECT received_at FROM bridge_inbox WHERE account_id=? AND mode=? "
                "UNION ALL SELECT received_at FROM bridge_operations "
                "WHERE account_id=? AND mode=?",
                (context.account_id, context.mode, context.account_id, context.mode),
            )
        ]
        if self._order_source is not None:
            row = connection.execute(
                "SELECT payload_json FROM bridge_journal WHERE account_id=? AND mode=? "
                "ORDER BY sequence DESC LIMIT 1",
                (context.account_id, context.mode),
            ).fetchone()
            if row is not None:
                values.append(json.loads(row["payload_json"])["account_recorded_at"])
        parsed = [datetime.fromisoformat(value) for value in values]
        return max(parsed) if parsed else None

    def _require_context_not_before_store(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> None:
        self._require_config(connection, context)
        latest = self._latest_received_at(connection, context)
        if latest is not None and context.as_of < latest:
            raise FactError("CONTEXT_TIME_REGRESSION", "caller as_of precedes consumed facts")

    def _operation_row(
        self, context: SyntheticContext, kind: str, identity: str
    ) -> sqlite3.Row | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM bridge_operations "
                "WHERE account_id=? AND mode=? AND kind=? AND identity=?",
                (context.account_id, context.mode, kind, identity),
            ).fetchone()
            if row is not None:
                self._validate_operation_journal(connection, context)
            return cast(sqlite3.Row | None, row)

    def _validate_operation_journal(
        self, connection: sqlite3.Connection, context: SyntheticContext
    ) -> None:
        rows = connection.execute(
            "SELECT kind,identity,result_json FROM bridge_operations WHERE account_id=? "
            "AND mode=? AND status='POSTED' AND kind IN ('sell','settlement','late_fee') "
            "ORDER BY kind,identity",
            (context.account_id, context.mode),
        ).fetchall()
        for row in rows:
            journal = connection.execute(
                "SELECT payload_json FROM bridge_journal WHERE account_id=? AND mode=? "
                "AND transition_id=? AND kind=?",
                (
                    context.account_id,
                    context.mode,
                    f"{row['kind']}:{row['identity']}",
                    row["kind"],
                ),
            ).fetchone()
            journal_result = (
                None if journal is None else json.loads(journal["payload_json"]).get("result")
            )
            if journal_result is None or json.loads(row["result_json"]) != journal_result:
                raise FactError(
                    "STORE_PROJECTION_MISMATCH",
                    f"cached {row['kind']} receipt differs from journal",
                )
        inbox = connection.execute(
            "SELECT report_id,result_json FROM bridge_inbox WHERE account_id=? "
            "AND mode=? AND status='POSTED' ORDER BY report_id",
            (context.account_id, context.mode),
        ).fetchall()
        for row in inbox:
            journal = connection.execute(
                "SELECT payload_json FROM bridge_journal WHERE account_id=? AND mode=? "
                "AND transition_id=? AND kind='buy'",
                (context.account_id, context.mode, f"buy:{row['report_id']}"),
            ).fetchone()
            journal_result = (
                None if journal is None else json.loads(journal["payload_json"]).get("result")
            )
            if journal_result is None or json.loads(row["result_json"]) != journal_result:
                raise FactError(
                    "STORE_PROJECTION_MISMATCH", "cached buy receipt differs from journal"
                )
        entries = connection.execute(
            "SELECT command_id,result_json FROM bridge_entry_receipts WHERE account_id=? "
            "AND mode=? AND accepted=1 ORDER BY command_id",
            (context.account_id, context.mode),
        ).fetchall()
        for row in entries:
            journal = connection.execute(
                "SELECT payload_json FROM bridge_journal WHERE account_id=? AND mode=? "
                "AND transition_id=? AND kind='entry'",
                (context.account_id, context.mode, f"entry:{row['command_id']}"),
            ).fetchone()
            if journal is None:
                raise FactError("STORE_PROJECTION_MISMATCH", "entry journal is missing")
            event = json.loads(journal["payload_json"])
            journal_result = {
                key: event[key]
                for key in (
                    "accepted",
                    "replayed",
                    "quantity",
                    "reservation_id",
                    "account_revision",
                    "result_digest",
                    "command_id",
                )
            }
            if json.loads(row["result_json"]) != journal_result:
                raise FactError(
                    "STORE_PROJECTION_MISMATCH", "cached entry receipt differs from journal"
                )

    def _replay_buy_operation(
        self,
        context: SyntheticContext,
        kind: str,
        identity: str,
        expected_digest: str,
        expected_payload: object,
    ) -> BuyBridgeResult | None:
        row = self._replay_operation(context, kind, identity, expected_digest, expected_payload)
        if row is None or row["status"] != "POSTED":
            return None
        return self._buy_from_dict(json.loads(row["result_json"]))

    def _replay_operation(
        self,
        context: SyntheticContext,
        kind: str,
        identity: str,
        expected_digest: str,
        expected_payload: object,
    ) -> sqlite3.Row | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM bridge_operations WHERE account_id=? AND mode=? "
                    "AND kind=? AND identity=?",
                    (context.account_id, context.mode, kind, identity),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                self._validate_operation_journal(connection, context)
                if row["payload_digest"] != expected_digest:
                    self._require_context_not_before_store(connection, context)
                    self._record_conflict(
                        connection,
                        context,
                        f"{kind}_operation",
                        identity,
                        row["payload_digest"],
                        expected_digest,
                        json.dumps(expected_payload, sort_keys=True),
                    )
                    connection.commit()
                    raise FactError("IDEMPOTENCY_CONFLICT", "operation payload differs")
                connection.commit()
                return cast(sqlite3.Row, row)
            except Exception:
                connection.rollback()
                raise

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
        if self._order_source is not None:
            if not isinstance(payload, dict):
                raise FactError("INVALID_REPORT_SHAPE", "journal payload must be an object")
            payload = {**payload, "account_recorded_at": context.as_of.isoformat()}
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
            "postings": [
                {
                    "account": item.account,
                    "amount": format(item.amount.quantize(Decimal("0.01")), "f"),
                }
                for item in result.postings
            ],
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
        *,
        resolved: bool = False,
        scope_key: str | None = None,
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
        state = "RESOLVED_ALIAS" if resolved else "UNRESOLVED"
        scope = scope_key or f"account:{context.account_id}"
        conflict_id = digest(
            [context.account_id, context.mode, identity_kind, identity, old_digest, new_digest]
        )
        connection.execute(
            "INSERT OR IGNORE INTO bridge_conflict_register VALUES(?,?,?,?,?,?,?)",
            (conflict_id, context.account_id, context.mode, scope, state, identity, evidence),
        )

    def _read(self, connection: sqlite3.Connection, context: SyntheticContext) -> BridgeState:
        self._require_context_not_before_store(connection, context)
        self._validate_operation_journal(connection, context)
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
            "postings": [
                {
                    "account": item.account,
                    "amount": format(item.amount.quantize(Decimal("0.01")), "f"),
                }
                for item in result.postings
            ],
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
    temporal_profile: SyntheticTemporalProfile | None = None,
    order_source: SyntheticOrderSource | None = None,
) -> FundingBridgeService:
    authorization.require(context)
    adapter = SQLiteCycleBridgeAdapter(path, fault_hook=fault_hook, clock=clock)
    adapter.bind_order_source(context, order_source)
    return FundingBridgeService(
        adapter,
        context,
        authorization,
        temporal_profile,
        order_source,
    )
