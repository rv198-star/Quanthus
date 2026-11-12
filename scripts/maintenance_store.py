"""Additive transport ledger. Existing v1 receipts remain immutable."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identity(prefix):
    return prefix + "-" + uuid.uuid4().hex


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.directory / "transport.sqlite3"
        self.db = sqlite3.connect(self.db_path, timeout=20)
        os.chmod(self.db_path, 0o600)
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS inbox (
            request_id TEXT PRIMARY KEY, input_digest TEXT NOT NULL,
            request_json TEXT NOT NULL, state TEXT NOT NULL,
            receipt_json TEXT, publication TEXT NOT NULL DEFAULT 'pending');
          CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS receiver_runs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS request_meta (
            request_id TEXT PRIMARY KEY, received_at TEXT, first_run_id TEXT,
            parent_id TEXT, queued_run_id TEXT, recovery_run_id TEXT);
          CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY, request_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            run_id TEXT NOT NULL, model TEXT NOT NULL, started_at TEXT NOT NULL,
            completed_at TEXT, completed_run_id TEXT,
            UNIQUE(request_id, ordinal));
          CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
            request_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
          CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
          CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
          CREATE TABLE IF NOT EXISTS deliveries (
            comment_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, input_digest TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS supplements (
            request_id TEXT PRIMARY KEY, value TEXT NOT NULL, url TEXT);
        """)
        self.db.execute(
            "INSERT OR IGNORE INTO ledger_meta VALUES (?,?)", ("id", identity("ledger"))
        )
        self.db.commit()
        self.ledger_id = self.db.execute("SELECT value FROM ledger_meta WHERE key='id'").fetchone()[
            0
        ]
        self.run_info = None
        self.run_context = {}
        self.in_atomic = False

    @contextmanager
    def atomic(self):
        self.run()
        self.db.execute("BEGIN IMMEDIATE")
        self.in_atomic = True
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        finally:
            self.in_atomic = False

    @property
    def handle(self):
        return "maintenance-ledger:" + self.ledger_id

    def close(self):
        self.db.close()

    def start_run(self, **context):
        self.run_context = context or self.run_context
        self.run_info = None

    def run(self):
        if not self.run_info:
            self.run_info = dict(
                self.run_context, receiver_run_id=identity("run"), started_at=now(), pid=os.getpid()
            )
            self.db.execute(
                "INSERT INTO receiver_runs VALUES (?,?)",
                (self.run_info["receiver_run_id"], canonical(self.run_info)),
            )
            self.db.commit()
        return self.run_info

    def events(self, key):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT data FROM events WHERE request_id=? ORDER BY seq", (key,)
            )
        ]

    def event(self, key, kind, **data):
        prior = self.db.execute(
            "SELECT data FROM events WHERE request_id=? ORDER BY seq DESC LIMIT 1", (key,)
        ).fetchone()
        value = {
            "event_id": identity("evt"),
            "request_id": key,
            "kind": kind,
            "occurred_at": now(),
            "receiver_run_id": self.run()["receiver_run_id"],
            "previous_digest": json.loads(prior[0])["event_digest"] if prior else None,
            "data": data,
        }
        value["event_digest"] = digest(value)
        self.db.execute(
            "INSERT INTO events(id,request_id,kind,data) VALUES (?,?,?,?)",
            (value["event_id"], key, kind, canonical(value)),
        )
        if not self.in_atomic:
            self.db.commit()
        return value

    def reserve(self, req, *, parent_id=None):
        key, sha = req["request_id"], digest(req)
        existing = self.db.execute(
            "SELECT input_digest,receipt_json FROM inbox WHERE request_id=?", (key,)
        ).fetchone()
        if existing:
            if existing[0] != sha:
                raise ValueError("request_id is already bound to a different contract")
            return False, json.loads(existing[1]) if existing[1] else None
        # Callers hold the environment claim/poll lock; this transaction also protects ID binding.
        run_id = self.run()["receiver_run_id"]
        with self.atomic():
            old = self.db.execute(
                "SELECT input_digest,receipt_json FROM inbox WHERE request_id=?", (key,)
            ).fetchone()
            if old:
                if old[0] != sha:
                    raise ValueError("request_id is already bound to a different contract")
                return False, json.loads(old[1]) if old[1] else None
            received = now()
            self.db.execute(
                "INSERT INTO inbox(request_id,input_digest,request_json,state) VALUES (?,?,?,'reserved')",
                (key, sha, canonical(req)),
            )
            self.db.execute(
                "INSERT INTO request_meta(request_id,received_at,first_run_id,parent_id,queued_run_id) VALUES (?,?,?,?,?)",
                (key, received, run_id, parent_id, run_id),
            )
            self.event(
                key, "request_received", received_at=received, input_digest=sha, parent_id=parent_id
            )
        return True, None

    def observe_delivery(self, req, comment):
        cid = str(comment["id"])
        old = self.db.execute(
            "SELECT request_id,input_digest FROM deliveries WHERE comment_id=?", (cid,)
        ).fetchone()
        if old:
            if old != (req["request_id"], digest(req)):
                raise ValueError("observed delivery was edited; immutable request binding retained")
            return
        with self.atomic():
            self.db.execute(
                "INSERT INTO deliveries VALUES (?,?,?)", (cid, req["request_id"], digest(req))
            )
            count = self.db.execute(
                "SELECT COUNT(*) FROM deliveries WHERE request_id=?", (req["request_id"],)
            ).fetchone()[0]
            self.event(
                req["request_id"],
                "delivery_received" if count == 1 else "duplicate_delivery",
                comment_id=cid,
                input_digest=digest(req),
                url=comment.get("html_url"),
            )

    def begin_attempt(self, key, model):
        old = self.attempt(key)
        if old:
            return old
        aid, started, run_id = identity("attempt"), now(), self.run()["receiver_run_id"]
        ordinal = (
            self.db.execute("SELECT COUNT(*) FROM attempts WHERE request_id=?", (key,)).fetchone()[
                0
            ]
            + 1
        )
        with self.atomic():
            self.db.execute(
                "INSERT INTO attempts(id,request_id,ordinal,run_id,model,started_at) VALUES (?,?,?,?,?,?)",
                (aid, key, ordinal, run_id, model, started),
            )
            self.event(
                key, "execution_started", attempt_id=aid, execution_model=model, ordinal=ordinal
            )
        return self.attempt(key)

    def attempt(self, key):
        row = self.db.execute(
            "SELECT id,ordinal,run_id,model,started_at,completed_at,completed_run_id FROM attempts WHERE request_id=? ORDER BY ordinal DESC LIMIT 1",
            (key,),
        ).fetchone()
        if not row:
            return None
        return dict(
            zip(
                (
                    "attempt_id",
                    "ordinal",
                    "receiver_run_id",
                    "execution_model",
                    "started_at",
                    "completed_at",
                    "completed_run_id",
                ),
                row,
            )
        )

    def complete_attempt(self, key, status):
        a = self.attempt(key)
        if a and not a["completed_at"]:
            with self.atomic():
                self.db.execute(
                    "UPDATE attempts SET completed_at=?,completed_run_id=? WHERE id=?",
                    (now(), self.run()["receiver_run_id"], a["attempt_id"]),
                )
                self.event(key, "execution_completed", attempt_id=a["attempt_id"], status=status)

    def state(self, key, value=None):
        if value:
            self.db.execute("UPDATE inbox SET state=? WHERE request_id=?", (value, key))
            self.db.commit()
        row = self.db.execute("SELECT state FROM inbox WHERE request_id=?", (key,)).fetchone()
        return row[0] if row else None

    def meta(self, key):
        row = self.db.execute(
            "SELECT received_at,first_run_id,parent_id,queued_run_id,recovery_run_id FROM request_meta WHERE request_id=?",
            (key,),
        ).fetchone()
        return (
            dict(
                zip(
                    (
                        "received_at",
                        "first_receiver_run_id",
                        "parent_request_id",
                        "queued_run_id",
                        "recovery_run_id",
                    ),
                    row,
                )
            )
            if row
            else {"history_status": "legacy_not_recorded", "received_at": None}
        )

    def finish(self, req, receipt):
        old = self.get(req["request_id"])
        signed = dict(receipt, receipt_digest=digest(receipt))
        if old:
            if old != signed:
                raise ValueError("persisted native receipts are immutable")
            return old
        with self.atomic():
            self.db.execute(
                "UPDATE inbox SET receipt_json=?,state='recorded' WHERE request_id=?",
                (canonical(signed), req["request_id"]),
            )
            self.event(
                req["request_id"],
                "result_persisted",
                receipt_digest=signed["receipt_digest"],
                execution_slot=req["request_id"],
                attempt_id=(self.attempt(req["request_id"]) or {}).get("attempt_id"),
            )
        return signed

    def get(self, key):
        row = self.db.execute(
            "SELECT receipt_json FROM inbox WHERE request_id=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def rows(self):
        return self.db.execute(
            "SELECT request_id,request_json,state,receipt_json,publication FROM inbox ORDER BY rowid"
        ).fetchall()

    def published(self, key, value):
        self.db.execute("UPDATE inbox SET publication=? WHERE request_id=?", (value, key))
        self.db.commit()

    def arm_recovery(self, key, query_id):
        meta = self.meta(key)
        if not meta.get("recovery_run_id"):
            with self.atomic():
                self.db.execute(
                    "UPDATE request_meta SET recovery_run_id=? WHERE request_id=?",
                    (self.run()["receiver_run_id"], key),
                )
                self.event(
                    key,
                    "reconciliation",
                    query_request_id=query_id,
                    ledger_handle=self.handle,
                    original_receipt_digest=(self.get(key) or {}).get("receipt_digest"),
                    outbox_state=self.publication(key),
                    attempt_id=(self.attempt(key) or {}).get("attempt_id"),
                )

    def publication(self, key):
        row = self.db.execute("SELECT publication FROM inbox WHERE request_id=?", (key,)).fetchone()
        return row[0] if row else None

    def projection(self, key):
        row = self.db.execute(
            "SELECT input_digest,state,receipt_json,publication FROM inbox WHERE request_id=?",
            (key,),
        ).fetchone()
        if not row:
            raise ValueError("target request is not present in this ledger")
        receipt = json.loads(row[2]) if row[2] else None
        evidence = self.db.execute(
            "SELECT value,url FROM supplements WHERE request_id=?", (key,)
        ).fetchone()
        events = self.events(key)
        run_ids = {e["receiver_run_id"] for e in events}
        runs = {}
        for run_id in sorted(run_ids):
            record = self.db.execute(
                "SELECT data FROM receiver_runs WHERE id=?", (run_id,)
            ).fetchone()
            if record:
                runs[run_id] = json.loads(record[0])
        return {
            "ledger_handle": self.handle,
            "request_id": key,
            "execution_slot": key,
            "request_digest": row[0],
            "transport_state": row[1],
            "receipt_digest": (receipt or {}).get("receipt_digest"),
            "publication": row[3],
            "identity": self.meta(key),
            "native_attempt": self.attempt(key),
            "events": events,
            "receiver_runs": runs,
            "delivery_evidence": {
                "url": evidence[1],
                "evidence_digest": json.loads(evidence[0])["evidence_digest"],
            }
            if evidence
            else None,
        }

    def consumption_count(self, key):
        return self.db.execute(
            "SELECT COUNT(*) FROM events WHERE request_id=? AND kind='rule_consumed'", (key,)
        ).fetchone()[0]
