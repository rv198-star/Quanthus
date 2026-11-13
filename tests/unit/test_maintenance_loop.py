"""Regression and negative checks derived from all eight bootstrap exit criteria."""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "maintenance_loop", SOURCE / "scripts/maintenance_loop.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
import maintenance_evidence as evidence  # noqa: E402
import maintenance_evidence_tools as evidence_tools  # noqa: E402
from maintenance_audit import valid_chain, verify  # noqa: E402
from maintenance_sources import collect, native_tasks, verification  # noqa: E402


class MaintenanceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.config = json.loads((SOURCE / "config/maintenance-loop.v1.json").read_text())
        # Historical body regressions use the explicit legacy backend.
        self.config.pop("evidence_store", None)
        self.config["authority_files"] = ["docs/maintenance/loop-contract.md"]
        self.config["dependency_files"] = ["requirements.lock.txt"]
        # Unit fixture identity is independent of the deployed server registry.
        self.config["environments"] = [
            {
                "id": "local-dev",
                "required": True,
                "role": "test-fixture",
                "max_snapshot_age_seconds": 93600,
            }
        ]
        self.config["repair_environment"] = "local-dev"
        (self.root / "docs/maintenance").mkdir(parents=True)
        (self.root / "docs/maintenance/loop-contract.md").write_text("versioned authority\n")
        (self.root / "config").mkdir()
        for relative in m.RUNTIME_FILES:
            if relative.startswith(("scripts/", "config/")):
                (self.root / relative).parent.mkdir(exist_ok=True)
                shutil.copy2(SOURCE / relative, self.root / relative)
        (self.root / "config/maintenance-loop.v1.json").write_text(json.dumps(self.config))
        (self.root / "requirements.lock.txt").write_text("fixture-dependency==1\n")
        for args in [
            ("init",),
            ("config", "user.email", "fixture@example.invalid"),
            ("config", "user.name", "Fixture"),
            ("config", "commit.gpgsign", "false"),
            ("remote", "add", "origin", "https://github.com/" + self.config["repository"] + ".git"),
            ("add", "."),
            ("commit", "-qm", "fixture baseline"),
        ]:
            m.git(self.root, *args)
        self.state = Path(self.temp.name) / "state"
        self.store = m.Store(self.state)
        self.addCleanup(self.store.close)
        self.req = {
            "schema": "maintenance.request.v1",
            "repository": self.config["repository"],
            "contract_version": self.config["contract_version"],
            "environment_id": "local-dev",
            "request_id": "request-one",
            "batch_id": "batch-one",
            "challenge": "challenge-one",
            "kind": "acceptance_probe",
            "payload": {},
        }
        self.collect_patch = mock.patch.object(m, "collect", side_effect=self.fake_collection)
        self.collect_patch.start()
        self.addCleanup(self.collect_patch.stop)
        self.comments = []
        self.posts = []

    def fake_collection(self, root, config, env, store, executor, sources, fingerprint):
        return {
            "collected_at": m.now(),
            "coverage": ["unit-fixture-sources"],
            "missing_sources": [],
            "source_status": {
                "github_work": {"status": "ok"},
                "native_tasks": {"status": "ok"},
                "executor": {"status": "ok"},
                "maintenance_executions": {"status": "ok", "receiver": {"environment_id": env}},
                "verification": {
                    "status": "ok",
                    "sources": [
                        {"evidence": {"operational_fingerprint": fingerprint, "exit_code": 0, "runtime": "3.12.14"}}
                    ],
                },
            },
        }

    def new_run(self):
        self.store.start_run(
            repository=self.config["repository"],
            environment_id="local-dev",
            runtime_version=m.VERSION,
        )

    def claim(self, req=None, executor=None, complete=True):
        req = req or self.req
        self.new_run()
        result = m.claim(self.root, self.root, self.config, "local-dev", req, self.store, executor)
        if result is None and complete and req["kind"] == "acceptance_probe":
            self.new_run()
            m.consume_ready(self.root, self.store)
            result = m.claim(
                self.root, self.root, self.config, "local-dev", req, self.store, executor
            )
        return result

    def comment(self, req=None, author="rv198-star", cid=1):
        return {
            "id": cid,
            "user": {"login": author},
            "html_url": f"https://github.com/{self.config['repository']}/issues/1#issuecomment-{cid}",
            "body": m.envelope(m.REQUEST_MARKER, req or self.req),
        }

    def query(self):
        return dict(
            self.req,
            request_id="query-one",
            kind="reconcile",
            payload={"target_request_id": self.req["request_id"]},
        )

    def fake_post(self, config, marker, value, comments):
        old = next((c for c in self.posts if m.matches(c, marker, value, config)), None)
        if old:
            return old
        row = self.comment(cid=100 + len(self.posts))
        row["body"] = m.envelope(marker, value)
        self.posts.append(row)
        return row

    def fake_read(self, config, comment):
        return next(x for x in self.posts if x["html_url"] == comment["html_url"])

    def pipeline(self):
        self.comments = [
            self.comment(cid=1),
            self.comment(cid=2),
            self.comment(self.query(), cid=3),
        ]
        with (
            mock.patch.object(m, "gh_comments", side_effect=lambda _: self.comments + self.posts),
            mock.patch.object(m, "post_or_reuse", side_effect=self.fake_post),
            mock.patch.object(m, "read_comment", side_effect=self.fake_read),
        ):
            for _ in range(4):
                m.poll(self.root, self.root, self.config, "local-dev", self.store)
        receipt = self.store.get("request-one")
        row = self.store.db.execute(
            "SELECT value FROM supplements WHERE request_id=?", ("request-one",)
        ).fetchone()
        return receipt, json.loads(row[0])

    def audit(self, receipt, delivery):
        return verify(
            receipt,
            delivery,
            [{"id": 1, "request": self.req}, {"id": 2, "request": self.req}],
            self.config,
            {
                "source_sha": m.git(self.root, "rev-parse", "HEAD"),
                "operational_fingerprint": m.fingerprint(self.root, self.config),
                "activated_rule": m.load_rule(self.root),
            },
            m.now(),
        )



class MaintenanceTests(MaintenanceFixture):
    def test_deployment_uses_the_owner_confirmed_server(self):
        configured = json.loads((SOURCE / "config/maintenance-loop.v1.json").read_text())
        expected = "cn02-dev" if configured["repository"].endswith("/Quanthus") else "oci-dev"
        self.assertEqual([x["id"] for x in configured["environments"]], [expected])
        self.assertTrue(configured["environments"][0]["required"])
        self.assertEqual(configured["repair_environment"], expected)

    def test_actual_registry_rejects_mac_intake(self):
        configured = json.loads((SOURCE / "config/maintenance-loop.v1.json").read_text())
        with self.assertRaises(ValueError):
            m.request(self.req, configured, "local-dev")

    def test_authority_escape_is_rejected(self):
        (self.root / "config/maintenance-loop.v1.json").write_text(
            json.dumps(dict(self.config, authority_files=["../private.md"]))
        )
        with self.assertRaises(ValueError):
            m.load_config(self.root)

    def test_actor_and_environment_routing(self):
        self.assertIsNone(
            m.parse_comment(self.comment(author="outside-user"), self.config, "local-dev")
        )
        self.assertIsNone(
            m.parse_comment(
                self.comment(dict(self.req, environment_id="other-env")), self.config, "local-dev"
            )
        )

    def test_malformed_envelope_is_rejected(self):
        row = self.comment()
        row["body"] += "\nrun some command"
        with self.assertRaises(ValueError):
            m.parse_comment(row, self.config, "local-dev")

    def test_invalid_identity_contract_and_kind_are_rejected(self):
        for patch in (
            {"repository": "other/repo"},
            {"contract_version": "old"},
            {"request_id": "../escape"},
            {"request_id": "x:fixture"},
            {"kind": "shell"},
            {"challenge": ""},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                m.request(dict(self.req, **patch), self.config, "local-dev")

    def test_same_id_with_different_scope_is_rejected(self):
        self.store.reserve(self.req)
        with self.assertRaisesRegex(ValueError, "different contract"):
            self.store.reserve(dict(self.req, challenge="changed"))

    def test_snapshot_preserves_private_dirty_work(self):
        path = self.root / "requirements.lock.txt"
        path.write_text("PRIVATE_ONE")
        m.git(self.root, "add", str(path))
        path.write_text("PRIVATE_TWO")
        (self.root / "untracked.txt").write_text("PRIVATE_THREE")
        snap = m.snapshot(self.root, self.root, self.config, "local-dev", self.store)
        self.assertEqual(snap["tracked_dirty_count"], 1)
        self.assertEqual(snap["untracked_count"], 1)
        self.assertNotIn("PRIVATE", json.dumps(snap))
        self.assertEqual(path.read_text(), "PRIVATE_TWO")

    def test_snapshot_rejects_another_repository(self):
        m.git(self.root, "remote", "set-url", "origin", "https://github.com/other/repo.git")
        with self.assertRaisesRegex(ValueError, "origin"):
            m.snapshot(self.root, self.root, self.config, "local-dev")

    def test_duplicate_probe_reuses_receipt_and_attempt(self):
        one = self.claim()
        two = self.claim()
        self.assertEqual(one, two)
        self.assertEqual(one["result"]["status"], "pass")
        self.assertEqual(len(self.store.rows()), 2)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2)
        self.assertEqual(self.store.consumption_count("request-one:fixture"), 1)

    def test_child_cannot_run_in_enqueue_run(self):
        self.assertIsNone(self.claim(complete=False))
        m.consume_ready(self.root, self.store)
        self.assertIsNone(self.store.get("request-one:fixture"))
        self.new_run()
        m.consume_ready(self.root, self.store)
        child = self.store.get("request-one:fixture")
        self.assertEqual(child["result"]["status"], "pass")
        self.assertNotEqual(
            child["native_attempt"]["receiver_run_id"],
            self.store.meta("request-one:fixture")["queued_run_id"],
        )

    def test_identity_survives_reopening(self):
        first = self.claim()
        reopened = m.Store(self.state)
        try:
            self.assertEqual(reopened.get("request-one"), first)
            self.assertEqual(reopened.attempt("request-one"), first["native_attempt"])
            self.assertEqual(reopened.handle, first["ledger_handle"])
            self.assertEqual(reopened.meta("request-one")["received_at"], first["received_at"])
        finally:
            reopened.close()

    def test_native_receipt_is_immutable(self):
        self.claim()
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.store.finish(self.req, {"changed": "receipt"})

    def test_events_are_append_only(self):
        self.claim()
        for sql in ('UPDATE events SET kind="fake"', "DELETE FROM events"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute(sql)
        self.assertTrue(valid_chain(self.store.events("request-one")))

    def test_receiver_lock_prevents_concurrent_dispatch(self):
        with (self.state / "claim.lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "busy"):
                self.claim()

    def test_delivery_observation_is_per_comment_not_per_poll(self):
        self.store.reserve(self.req)
        for _ in range(3):
            self.store.observe_delivery(self.req, self.comment(cid=1))
        self.store.observe_delivery(self.req, self.comment(cid=2))
        events = self.store.events("request-one")
        self.assertEqual(sum(e["kind"] == "duplicate_delivery" for e in events), 1)

    def test_edited_delivery_is_rejected(self):
        self.store.reserve(self.req)
        self.store.observe_delivery(self.req, self.comment())
        with self.assertRaises(ValueError):
            self.store.observe_delivery(dict(self.req, challenge="edited"), self.comment())

    def test_interrupted_coding_is_not_redispatched(self):
        req = dict(self.req, kind="repair", payload={"issue_number": 7})
        self.store.reserve(req)
        with mock.patch.object(m, "repair", side_effect=AssertionError("duplicate")):
            result = self.claim(req)
        self.assertEqual(result["result"]["status"], "execution_uncertain")

    def test_missing_executor_explicitly_waits_and_receipt_stays_immutable(self):
        req = dict(self.req, kind="repair", payload={"issue_number": 7})
        first = self.claim(req)
        self.assertEqual(first["result"]["status"], "awaiting_executor")
        with mock.patch.object(m, "repair", side_effect=AssertionError("duplicate")):
            self.assertEqual(self.claim(req, "/existing/executor"), first)

    def test_timeout_retains_uncertain_original_execution(self):
        with mock.patch.object(m, "repair", side_effect=subprocess.TimeoutExpired("fixture", 1)):
            result = self.claim(dict(self.req, kind="repair"), "/existing/executor")
        self.assertEqual(result["result"]["status"], "execution_uncertain")

    def test_effect_requires_baseline_use_and_version(self):
        event = self.claim()["result"]["effect"]
        rule = event["consumed_rule_digest"]
        for patch in (
            {"consumed_rule_digest": "0" * 64},
            {"baseline_ref": ""},
            {"eligibility": ""},
            {"observed_uses": 0},
            {"evidence_refs": []},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                m.effect_event(dict(event, **patch), rule)
        self.assertEqual(
            m.effect_event(dict(event, decision="wait", observed_uses=0), rule)["decision"], "wait"
        )

    def test_effect_uses_tracked_owning_rule(self):
        event = self.claim()["result"]["effect"]
        result = self.claim(dict(self.req, request_id="effect-one", kind="effect", payload=event))
        self.assertEqual(result["result"]["status"], "recorded")
        event["rule_path"] = "../../private.md"
        self.assertEqual(
            self.claim(dict(self.req, request_id="effect-bad", kind="effect", payload=event))[
                "result"
            ]["status"],
            "failed",
        )

    def test_outbox_requires_actual_formal_reconcile(self):
        self.claim()
        self.new_run()
        m.publish(self.config, self.store, [])
        self.assertEqual(self.store.publication("request-one"), "deferred-awaiting-reconcile")
        self.new_run()
        with mock.patch.object(m, "post_or_reuse", side_effect=AssertionError("premature publish")):
            self.assertEqual(m.publish(self.config, self.store, []), [])
        self.claim(self.query())
        self.assertTrue(
            any(e["kind"] == "reconciliation" for e in self.store.events("request-one"))
        )

    def test_public_reconcile_exports_original_attempt_and_result(self):
        receipt = self.claim()
        self.new_run()
        m.publish(self.config, self.store, [])
        query = self.claim(self.query())["result"]["target"]
        self.assertEqual(query["receipt_digest"], receipt["receipt_digest"])
        self.assertEqual(query["native_attempt"], receipt["native_attempt"])
        self.assertEqual(query["publication"], "deferred-awaiting-reconcile")

    def test_wrong_ledger_handle_is_rejected(self):
        self.claim()
        req = self.query()
        req["payload"]["ledger_handle"] = "maintenance-ledger:wrong"
        with self.assertRaises(ValueError):
            self.claim(req)

    def test_four_real_stages_pass_eight_gate_audit(self):
        receipt, delivery = self.pipeline()
        report = self.audit(receipt, delivery)
        self.assertEqual(report["status"], "READY", report)
        self.assertEqual(len(report["checks"]), 8)
        self.assertEqual(self.store.consumption_count("request-one:fixture"), 1)

    def test_replay_after_publication_does_not_consume_again(self):
        receipt, delivery = self.pipeline()
        self.assertEqual(self.claim(), receipt)
        self.assertEqual(self.store.consumption_count("request-one:fixture"), 1)

    def test_audit_blocks_missing_environment_source(self):
        receipt, delivery = self.pipeline()
        receipt = copy.deepcopy(receipt)
        receipt["snapshot"]["missing_sources"] = [
            {"source": "native_tasks", "status": "not_configured"}
        ]
        self.assertFalse(self.audit(receipt, delivery)["checks"]["2_environment"])

    def test_audit_blocks_missing_attempt(self):
        receipt, delivery = self.pipeline()
        receipt = copy.deepcopy(receipt)
        receipt["native_attempt"] = {}
        # Remove only the identity, preserving the rest of the genuine recorded path.
        report = self.audit(receipt, delivery)
        self.assertFalse(report["checks"]["3_execution"])

    def test_audit_blocks_missing_recovery_chain(self):
        receipt, delivery = self.pipeline()
        delivery = copy.deepcopy(delivery)
        delivery["original_ledger"]["events"] = [
            e for e in delivery["original_ledger"]["events"] if e["kind"] != "reconciliation"
        ]
        self.assertFalse(self.audit(receipt, delivery)["checks"]["5_recovery"])

    def test_audit_blocks_unexecuted_child(self):
        receipt, delivery = self.pipeline()
        delivery = copy.deepcopy(delivery)
        delivery["consumer_ledger"]["events"] = []
        report = self.audit(receipt, delivery)
        self.assertFalse(report["checks"]["6_rule_consumer"])
        self.assertFalse(report["checks"]["7_effect"])

    def test_lost_ack_reuses_existing_comment(self):
        receipt, delivery = self.pipeline()
        comment = next(
            c for c in self.posts if m.matches(c, m.RECEIPT_MARKER, receipt, self.config)
        )
        with mock.patch.object(m.subprocess, "run", side_effect=AssertionError("duplicate post")):
            self.assertEqual(
                m.post_or_reuse(self.config, m.RECEIPT_MARKER, receipt, [comment]), comment
            )

    def test_legacy_migration_preserves_request_and_receipt_bytes(self):
        path = Path(self.temp.name) / "legacy"
        path.mkdir()
        db = sqlite3.connect(path / "transport.sqlite3")
        db.execute(
            "CREATE TABLE inbox (request_id TEXT PRIMARY KEY,input_digest TEXT,request_json TEXT,state TEXT,receipt_json TEXT,publication TEXT)"
        )
        req = dict(self.req, contract_version="1.0.0")
        old = {
            "schema": "maintenance.receipt.v1",
            "result": {"status": "pass"},
            "receipt_digest": "old-retained",
        }
        encoded = json.dumps(old, indent=2)
        db.execute(
            "INSERT INTO inbox VALUES (?,?,?,?,?,?)",
            (
                "request-one",
                m.digest(req),
                m.canonical(req),
                "recorded",
                encoded,
                "https://github.com/original",
            ),
        )
        db.commit()
        db.close()
        store = m.Store(path)
        try:
            self.assertEqual(store.get("request-one"), old)
            self.assertEqual(store.rows()[0][3], encoded)
            self.assertEqual(store.meta("request-one")["history_status"], "legacy_not_recorded")
            self.assertEqual(store.events("request-one"), [])
        finally:
            store.close()

    def test_missing_and_failed_sources_are_distinct_from_empty(self):
        with mock.patch(
            "maintenance_sources.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout="[[]]"),
        ):
            value = collect(
                self.root,
                self.config,
                "local-dev",
                self.store,
                None,
                {},
                m.fingerprint(self.root, self.config),
            )
        statuses = value["source_status"]
        self.assertEqual(statuses["github_work"]["status"], "ok")
        self.assertEqual(statuses["github_work"]["open_issues"], 0)
        self.assertEqual(statuses["native_tasks"]["status"], "not_configured")
        self.assertEqual(statuses["verification"]["status"], "not_configured")
        self.assertIn(
            {"source": "native_tasks", "status": "not_configured"}, value["missing_sources"]
        )

    def test_remote_role_does_not_require_coding_executor(self):
        config = dict(
            self.config,
            repair_environment="source-only",
            environments=[{"id": "remote", "role": "project-development-execution"}],
        )
        with mock.patch(
            "maintenance_sources.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout="[[]]"),
        ):
            value = collect(self.root, config, "remote", self.store, None, {}, "fingerprint")
        executor = value["source_status"]["executor"]
        self.assertEqual(executor["status"], "ok")
        self.assertEqual(executor["coding_executor"]["status"], "not_applicable")

    def test_native_task_parser_uses_actual_authority(self):
        path = Path(self.temp.name) / "task.json"
        path.write_text(json.dumps({"id": "native-id", "status": "active", "phase": "execute"}))
        value = native_tasks({"id": "task-source", "kind": "agentdock", "path": str(path)})
        self.assertEqual(value["active_tasks"][0]["native_ref"], "agentdock-task:native-id")
        path.write_text(json.dumps({"id": "native-id", "status": "completed", "phase": "complete"}))
        self.assertEqual(
            native_tasks({"id": "task-source", "kind": "agentdock", "path": str(path)})[
                "active_count"
            ],
            0,
        )

    def test_changed_consumer_is_covered_by_operational_fingerprint(self):
        before = m.fingerprint(self.root, self.config)
        with (self.root / "scripts/maintenance_consumer.py").open("a") as f:
            f.write("\n# changed\n")
        self.assertNotEqual(before, m.fingerprint(self.root, self.config))

    def test_verification_rejects_changed_test_closure(self):
        log = Path(self.temp.name) / "log"
        log.write_text("real captured output")
        value = {
            "schema": "maintenance.verification.v1",
            "operational_fingerprint": "fp",
            "tested_files": {"scripts/maintenance_consumer.py": "wrong"},
            "log_file": "log",
            "log_digest": m.file_digest(log),
        }
        value["evidence_digest"] = m.digest(value)
        path = log.parent / "verify.json"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "closure differs"):
            verification({"id": "verify", "path": str(path)}, self.root, "fp")

    def test_invalid_comment_does_not_block_valid_intake(self):
        with (
            mock.patch.object(
                m,
                "gh_comments",
                return_value=[self.comment(dict(self.req, request_id="../bad")), self.comment()],
            ),
            mock.patch.object(m, "publish", return_value=[]),
        ):
            result = m.poll(self.root, self.root, self.config, "local-dev", self.store)
        self.assertEqual(len(result["rejected_requests"]), 1)
        self.assertEqual(self.store.state("request-one"), "waiting_consumer")

    def test_persistence_and_event_append_are_one_transaction(self):
        self.store.reserve(self.req)
        with (
            mock.patch.object(self.store, "event", side_effect=OSError("storage unavailable")),
            self.assertRaises(OSError),
        ):
            self.store.finish(self.req, {"schema": "fixture"})
        self.assertIsNone(self.store.get("request-one"))
        self.assertEqual(self.store.state("request-one"), "reserved")

    def test_actual_cli_processes_resume_original_ledger(self):

        remote = Path(self.temp.name) / "github.json"
        remote.write_text(
            json.dumps(
                {
                    "comments": [
                        self.comment(cid=1),
                        self.comment(cid=2),
                        self.comment(self.query(), cid=3),
                    ]
                }
            )
        )
        bindir = Path(self.temp.name) / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(
            "#!"
            + sys.executable
            + "\n"
            + """import json,os,sys
from pathlib import Path
p=Path(os.environ['MAINTENANCE_FIXTURE_GH']);d=json.loads(p.read_text());args=sys.argv[1:]
endpoint=next(x for x in args if x.startswith('repos/'))
if '--method' in args:
 body=json.load(sys.stdin)['body'];cid=100+len(d['comments'])
 row={'id':cid,'user':{'login':'rv198-star'},'body':body,'html_url':'https://github.com/fixture/repo/issues/1#issuecomment-'+str(cid)}
 d['comments'].append(row);p.write_text(json.dumps(d));print(json.dumps(row))
elif '/issues/comments/' in endpoint:
 print(json.dumps(next(c for c in d['comments'] if c['id']==int(endpoint.rsplit('/',1)[-1]))))
elif '/comments?' in endpoint:
 print(json.dumps([d['comments']]))
else: print('[[]]')
"""
        )
        gh.chmod(0o700)
        env = dict(
            os.environ,
            PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""),
            MAINTENANCE_FIXTURE_GH=str(remote),
        )
        run_ids = []
        for _ in range(4):
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.root / "scripts/maintenance_loop.py"),
                    "poll",
                    "--root",
                    str(self.root),
                    "--workspace",
                    str(self.root),
                    "--state-dir",
                    str(self.state),
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run_ids.append(json.loads(result.stdout)["receiver_run_id"])
        receipt = self.store.get("request-one")
        child = self.store.get("request-one:fixture")
        self.assertEqual(len(set(run_ids)), 4)
        self.assertNotEqual(
            receipt["native_attempt"]["receiver_run_id"], child["native_attempt"]["receiver_run_id"]
        )
        self.assertEqual(self.store.consumption_count("request-one:fixture"), 1)
        self.assertTrue(self.store.publication("request-one").startswith("https://"))
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM attempts WHERE request_id=?", ("request-one",)
            ).fetchone()[0],
            1,
        )
        self.assertTrue(valid_chain(self.store.events("request-one")))

    def native_fixture(self, outside=False):
        executable = Path(self.temp.name) / "fixture-executor"
        target = "outside-scope.txt" if outside else "docs/fixture-change.md"
        executable.write_text(
            "#!"
            + sys.executable
            + '\nimport sys,json,subprocess\nfrom pathlib import Path\nroot=Path(sys.argv[sys.argv.index("--cd")+1])\n'
            + f'path=root/{target!r}\npath.parent.mkdir(parents=True,exist_ok=True)\npath.write_text("bounded fixture")\n'
            + (
                'subprocess.run(["git","-C",str(root),"add","."],check=True)\nsubprocess.run(["git","-C",str(root),"commit","-qm","fixture"],check=True)\n'
                if outside
                else ""
            )
            + 'print(json.dumps({"type":"thread.started","thread_id":"native-fixture-thread"}))\n'
        )
        executable.chmod(0o700)
        original = m.run

        def transport(argv, **kwargs):
            if argv[0] == "gh":
                if argv[-1].endswith("/git/ref/heads/main"):
                    return json.dumps({"object": {"sha": m.git(self.root, "rev-parse", "HEAD")}})
                return json.dumps(
                    {
                        "state": "open",
                        "html_url": "https://github.com/owner/repo/issues/7",
                        "title": "fixture",
                        "body": "one allowed source change",
                    }
                )
            return original(argv, **kwargs)

        req = dict(
            self.req,
            kind="repair",
            payload={"issue_number": 7, "base_sha": m.git(self.root, "rev-parse", "HEAD")},
        )
        with mock.patch.object(m, "run", side_effect=transport):
            return self.claim(req, str(executable))

    def test_native_process_candidate_preserves_primary_workspace(self):
        result = self.native_fixture()["result"]
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["native_thread_id"], "native-fixture-thread")
        self.assertFalse((self.root / "docs/fixture-change.md").exists())

    def test_committed_outside_scope_change_is_detected(self):
        result = self.native_fixture(outside=True)["result"]
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["within_declared_paths"])



# Private HF storage regressions. These use an in-memory Hub, never real credentials.


class MemoryHF(evidence.HFStore):
    def __init__(self):
        super().__init__('owner/private-evidence', 'fixture', 'local-dev', 'fixture-credential')
        self.head = '0' * 40
        self.trees = {self.head: {}}
        self.private = True
        self.denied = False
        self.conflict_once = False
        self.lose_confirmation_once = False
        self.commits = 0
        self.posts = 0

    def _commit(self, files):
        tree = dict(self.trees[self.head])
        tree.update(files)
        self.head = format(len(self.trees), '040x')
        self.trees[self.head] = tree
        return self.head

    def _request(self, method, path, body=None, missing=False):
        if self.denied:
            raise evidence.EvidenceError('HF evidence HTTP status 401')
        if method == 'GET' and path.startswith('/api/datasets/'):
            return json.dumps({'private': self.private, 'sha': self.head}).encode()
        if method == 'GET' and '/resolve/' in path:
            revision, relative = path.split('/resolve/', 1)[1].split('/', 1)
            value = self.trees.get(revision, {}).get(urllib.parse.unquote(relative))
            if value is None and not missing:
                raise evidence.EvidenceError('HF evidence HTTP status 404')
            return value
        if method == 'POST':
            self.posts += 1
            entries = [json.loads(line) for line in body.splitlines()]
            if self.conflict_once:
                self.conflict_once = False
                self._commit({'another-project/concurrent.json': b'{"retained":true}'})
            if entries[0]['value']['parentCommit'] != self.head:
                raise evidence.EvidenceError('HF evidence HTTP status 409', retryable=True)
            files = {x['value']['path']: base64.b64decode(x['value']['content']) for x in entries[1:]}
            self._commit(files)
            self.commits += 1
            if self.lose_confirmation_once:
                self.lose_confirmation_once = False
                raise evidence.EvidenceError('HF evidence transport failed', retryable=True)
            return json.dumps({'commitOid': self.head}).encode()
        raise AssertionError((method, path))


class HFEvidenceBackendTests(unittest.TestCase):
    def setUp(self):
        self.hub = MemoryHF()
        self.value = {'schema': 'maintenance.receipt.v1', 'result': {'status': 'observed'}}

    def test_private_revision_readback_and_content_path(self):
        ref = self.hub.put('receipt', self.value)
        self.assertEqual(self.hub.read(ref), self.value)
        self.assertRegex(ref['revision'], r'^[0-9a-f]{40}$')
        self.assertTrue(ref['path'].startswith('fixture/local-dev/receipt/'))
        self.assertEqual(ref['sha256'], hashlib.sha256(m.canonical(self.value).encode()).hexdigest())

    def test_repeated_content_reuses_existing_object(self):
        first = self.hub.put('receipt', self.value)
        second = self.hub.put('receipt', self.value)
        self.assertEqual(first, second)
        self.assertEqual(self.hub.commits, 1)

    def test_lost_commit_ack_reconciles_before_reposting(self):
        self.hub.lose_confirmation_once = True
        ref = self.hub.put('receipt', self.value)
        self.assertEqual(self.hub.read(ref), self.value)
        self.assertEqual(self.hub.commits, 1)
        self.assertEqual(self.hub.posts, 1)

    def test_concurrent_other_project_commit_is_preserved(self):
        self.hub.conflict_once = True
        ref = self.hub.put('receipt', self.value)
        self.assertEqual(self.hub.read(ref), self.value)
        self.assertIn('another-project/concurrent.json', self.hub.trees[self.hub.head])
        self.assertEqual(self.hub.posts, 2)

    def test_pinned_revision_survives_head_advancing(self):
        ref = self.hub.put('receipt', self.value)
        self.hub._commit({'unrelated.json': b'{}'})
        self.assertNotEqual(ref['revision'], self.hub.head)
        self.assertEqual(self.hub.read(ref), self.value)

    def test_public_repository_rejects_publication(self):
        self.hub.private = False
        with self.assertRaisesRegex(ValueError, 'private'):
            self.hub.put('receipt', self.value)
        self.assertEqual(self.hub.posts, 0)

    def test_auth_failure_does_not_write(self):
        self.hub.denied = True
        with self.assertRaisesRegex(ValueError, '401'):
            self.hub.put('receipt', self.value)
        self.assertEqual(self.hub.posts, 0)

    def test_token_cannot_be_exported_in_evidence(self):
        with self.assertRaisesRegex(ValueError, 'credential') as caught:
            self.hub.put('report', {'value': 'fixture-credential'})
        self.assertNotIn('fixture-credential', str(caught.exception))
        self.assertEqual(self.hub.posts, 0)

    def test_reference_rejects_cross_project_path_and_mutable_revision(self):
        ref = self.hub.put('receipt', self.value)
        for patch in [{'repository': 'other/repo'}, {'path': '../outside.json'}, {'path': ref['path'].replace('fixture/', 'other/', 1)}, {'revision': 'main'}, {'provider': 'other'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.hub.read(dict(ref, **patch))

    def test_changed_content_at_pinned_path_is_detected(self):
        ref = self.hub.put('receipt', self.value)
        self.hub.trees[ref['revision']][ref['path']] = b'{"different":true}'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.hub.read(ref)

    def test_oversized_evidence_is_rejected_before_network(self):
        with self.assertRaisesRegex(ValueError, 'size bound'):
            self.hub.put('snapshot', {'data': 'x' * evidence.MAX_BYTES})
        self.assertEqual(self.hub.posts, 0)

    def test_redirect_drops_credential_across_hosts(self):
        req = urllib.request.Request('https://huggingface.co/private', headers={'Authorization': 'Bearer fixture-credential'})
        redirected = evidence.PrivateRedirect().redirect_request(req, None, 302, '', {}, 'https://cdn.example.invalid/private')
        self.assertIsNone(redirected.get_header('Authorization'))
        with self.assertRaises(ValueError):
            evidence.PrivateRedirect().redirect_request(req, None, 302, '', {}, 'http://example.invalid/private')


class HFPublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MaintenanceTests('test_duplicate_probe_reuses_receipt_and_attempt')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.config['evidence_store'] = {'kind': 'hf-private-dataset', 'project': 'fixture'}
        self.hub = MemoryHF()
        patcher = mock.patch.object(evidence.HFStore, 'from_config', return_value=self.hub)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pipeline_keeps_full_objects_private_and_indexes_small(self):
        receipt, delivery = self.fixture.pipeline()
        self.assertTrue(all(self.fixture.audit(receipt, delivery)['checks'].values()))
        self.assertTrue(self.fixture.posts)
        for comment in self.fixture.posts:
            self.assertTrue(comment['body'].startswith(evidence.MARKER))
            self.assertNotIn('"snapshot"', comment['body'])
            self.assertNotIn('fixture-credential', comment['body'])
            self.assertLess(len(comment['body'].encode()), 3000)
        self.assertEqual(self.fixture.store.attempt('request-one')['ordinal'], 1)

    def test_pointer_and_original_result_stay_stable_after_restart(self):
        receipt, _ = self.fixture.pipeline()
        before = self.fixture.store.db.execute('SELECT pointer_json FROM evidence_references ORDER BY request_id,role').fetchall()
        commits = self.hub.commits
        self.fixture.store.close()
        self.fixture.store = m.Store(self.fixture.state)
        self.addCleanup(self.fixture.store.close)
        self.assertEqual(self.fixture.claim(), receipt)
        after = self.fixture.store.db.execute('SELECT pointer_json FROM evidence_references ORDER BY request_id,role').fetchall()
        self.assertEqual(before, after)
        self.assertEqual(self.hub.commits, commits)
        self.assertEqual(self.fixture.store.attempt('request-one')['ordinal'], 1)

    def test_hf_outage_retains_result_and_never_falls_back_to_full_body(self):
        f = self.fixture
        receipt = f.claim(dict(f.req, kind='snapshot'))
        attempt = f.store.attempt('request-one')
        self.hub.denied = True
        with mock.patch.object(m, 'post_or_reuse') as post, self.assertRaises(ValueError):
            m.publish(f.config, f.store, [])
        post.assert_not_called()
        self.assertEqual(f.store.get('request-one'), receipt)
        self.assertEqual(f.store.publication('request-one'), 'pending')
        self.hub.denied = False
        with mock.patch.object(m, 'post_or_reuse', side_effect=f.fake_post), mock.patch.object(m, 'read_comment', side_effect=f.fake_read):
            m.publish(f.config, f.store, [])
        self.assertEqual(f.store.attempt('request-one'), attempt)
        self.assertEqual(f.store.get('request-one'), receipt)
        self.assertEqual(len(f.posts), 2)

    def test_hf_pointer_is_saved_before_github_failure_and_reused(self):
        f = self.fixture
        receipt = f.claim(dict(f.req, kind='snapshot'))
        with mock.patch.object(m, 'post_or_reuse', side_effect=ValueError('publication pending')), self.assertRaises(ValueError):
            m.publish(f.config, f.store, [])
        pointer = f.store.db.execute('SELECT pointer_json FROM evidence_references WHERE role="receipt"').fetchone()[0]
        self.assertEqual(self.hub.commits, 1)
        with mock.patch.object(m, 'post_or_reuse', side_effect=f.fake_post), mock.patch.object(m, 'read_comment', side_effect=f.fake_read):
            m.publish(f.config, f.store, [])
        self.assertEqual(f.store.db.execute('SELECT pointer_json FROM evidence_references WHERE role="receipt"').fetchone()[0], pointer)
        self.assertEqual(f.store.get('request-one'), receipt)
        self.assertEqual(self.hub.commits, 2)  # Receipt and delivery, once each.

    def test_persisted_pointer_cannot_be_rewritten(self):
        self.fixture.pipeline()
        with self.assertRaises(sqlite3.IntegrityError):
            self.fixture.store.db.execute('UPDATE evidence_references SET pointer_json="{}"')





class HFOperatorAndToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.credentials = self.root / 'hf.env'
        fixture_value = 'fixture-credential'
        self.credentials.write_text(f'HF_TOKEN="{fixture_value}"\nIGNORED_SECRET=unrelated\n')  # scan-allow: fixture-literal
        self.credentials.chmod(0o600)
        self.proxy = self.root / 'proxy.env'
        self.proxy.write_text('HTTPS_PROXY="http://proxy.example.invalid:8080"\nUNRELATED_SETTING=discard\n')
        self.settings = {'repository': 'owner/private-evidence', 'credentials_file': str(self.credentials), 'proxy_file': str(self.proxy)}

    def test_private_operator_file_maps_only_hf_and_proxy_values(self):
        env = evidence.operator_environment(self.settings, {})
        self.assertEqual(env['LOOP3_HF_REPO_ID'], 'owner/private-evidence')
        self.assertEqual(env['LOOP3_HF_TOKEN'], 'fixture-credential')
        self.assertEqual(env['HTTPS_PROXY'], 'http://proxy.example.invalid:8080')
        self.assertNotIn('IGNORED_SECRET', env)
        self.assertNotIn('UNRELATED_SETTING', env)

    def test_group_readable_operator_secret_is_rejected(self):
        self.credentials.chmod(0o640)
        with self.assertRaisesRegex(ValueError, 'private'):
            evidence.operator_environment(self.settings, {})

    def test_operator_assignment_is_parsed_without_shell_execution(self):
        fixture_value = '$(touch /tmp/never-executed-by-loop3)'
        self.credentials.write_text(f'HF_TOKEN="{fixture_value}"\n')  # scan-allow: fixture-literal
        env = evidence.operator_environment(self.settings, {})
        self.assertEqual(env['LOOP3_HF_TOKEN'], '$(touch /tmp/never-executed-by-loop3)')

    def test_mcp_initialization_discovery_and_notifications(self):
        source = io.StringIO('\n'.join(json.dumps(x) for x in [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        ]) + '\n')
        output = io.StringIO()
        evidence_tools.serve(mock.Mock(), source, output)
        messages = [json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['result']['protocolVersion'], '2024-11-05')
        tools = messages[1]['result']['tools']
        self.assertEqual({x['name'] for x in tools}, {'read_evidence', 'store_report', 'read_report'})
        self.assertTrue(next(x for x in tools if x['name'] == 'read_evidence')['annotations']['readOnlyHint'])

    def test_invalid_json_does_not_reuse_previous_request_id(self):
        source = io.StringIO('{"id":7,"method":"ping"}\ninvalid-json\n')
        output = io.StringIO()
        evidence_tools.serve(mock.Mock(), source, output)
        messages = [json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(messages[0]['id'], 7)
        self.assertIsNone(messages[1]['id'])

    def test_report_write_read_and_retry_keep_stable_hf_reference(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            service = evidence_tools.EvidenceTools(self.settings, self.root / 'state')
            self.addCleanup(service.cache.close)
            hub = MemoryHF()
            report = {'schema': 'maintenance.daily.v1', 'batch_id': 'batch-one', 'challenge': 'challenge-one', 'controller_run_id': 'run-one', 'status': 'NOOP'}
            report['report_digest'] = m.digest(report)
            with mock.patch.object(service, 'backend', return_value=hub):
                pointer = service.call('store_report', {'project': 'controller', 'report': report})
                hub._commit({'other-project/changed.json': b'{}'})
                replay = service.call('store_report', {'project': 'controller', 'report': report})
                self.assertEqual(pointer, replay)
                self.assertEqual(service.call('read_report', {'pointer': pointer}), report)
                self.assertEqual(hub.commits, 1)
                bad = dict(report, status='changed')
                with self.assertRaisesRegex(ValueError, 'digest'):
                    service.call('store_report', {'project': 'controller', 'report': bad})

    def test_tool_rejects_unregistered_project_before_reading(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            service = evidence_tools.EvidenceTools(self.settings, self.root / 'state')
            self.addCleanup(service.cache.close)
            with self.assertRaisesRegex(ValueError, 'registered'):
                service.call('read_evidence', {'pointer': {'repository': 'outside/repo', 'environment_id': 'oci-dev'}})

    def test_pointer_reader_binds_registered_project_and_environment(self):
        config = {'repository': 'expected/repo', 'environments': [{'id': 'oci-dev'}]}
        pointer = {'schema': 'maintenance.evidence-pointer.v1', 'repository': 'other/repo', 'environment_id': 'oci-dev'}
        pointer['pointer_digest'] = m.digest(pointer)
        with self.assertRaisesRegex(ValueError, 'registration'):
            evidence.read_pointer(config, pointer)


class LearningWorkTests(MaintenanceFixture):
    # Reuse setup/helpers without rerunning the inherited bootstrap test cases.
    def review_executor(self, mutate=False):
        path = Path(self.temp.name) / "review-executor"
        path.write_text(
            "#!" + sys.executable + "\n" + "import json,sys\nfrom pathlib import Path\n"
            'a=sys.argv\ntree=Path(a[a.index("--cd")+1])\n'
            'assert a[a.index("--sandbox")+1]=="read-only"\n'
            + ('(tree/"unexpected-write").write_text("bad")\n' if mutate else "")
            + 'Path(a[a.index("--output-last-message")+1]).write_text("Reviewed selected diffs; four categories and follow-up evidence recorded; coverage awaits acceptance.")\n'
            'print(json.dumps({"type":"thread.started","thread_id":"real-cli-shape-fixture"}))\n'
        )
        path.chmod(0o700)
        return str(path)

    def partial_review_fixture(self):
        import maintenance_work as work

        start = m.git(self.root, "rev-parse", "HEAD")
        path = "docs/history path\n名字.txt"
        commits, blobs = [], {}
        for content in ("historical one", "historical two", "current endpoint"):
            (self.root / path).write_text(content)
            m.git(self.root, "add", ".")
            m.git(self.root, "commit", "-qm", content)
            commits.append(m.git(self.root, "rev-parse", "HEAD"))
            blobs[m.git(self.root, "rev-parse", "HEAD:" + path)] = content
        remote = Path(self.temp.name) / "promisor.git"
        work.run(["git", "clone", "--bare", "--no-local", str(self.root), str(remote)])
        m.git(remote, "config", "uploadpack.allowFilter", "true")
        m.git(remote, "config", "uploadpack.allowAnySHA1InWant", "true")
        clone = Path(self.temp.name) / "partial"
        work.run(["git", "clone", "--filter=blob:none", "--no-checkout", remote.as_uri(), str(clone)])
        self.assertEqual(m.git(clone, "config", "remote.origin.promisor"), "true")
        return clone, start, commits, blobs, remote

    def test_review_materializes_partial_clone_history_before_offline_executor(self):
        import maintenance_work as work

        clone, start, commits, blobs, remote = self.partial_review_fixture()
        missing_remote = str(Path(self.temp.name) / "offline.git")
        m.git(clone, "config", "remote.origin.url", missing_remote)
        absent = subprocess.run(
            ["git", "-C", str(clone), "cat-file", "-e", next(iter(blobs))],
            capture_output=True, timeout=10,
        )
        self.assertNotEqual(absent.returncode, 0)
        m.git(clone, "config", "remote.origin.url", remote.as_uri())
        executor = Path(self.temp.name) / "offline-review-executor"
        executor.write_text(
            "#!" + sys.executable + "\nimport os,sys,json,subprocess\nfrom pathlib import Path\n"
            'a=sys.argv\nroot=a[a.index("--cd")+1]\nos.environ["GIT_ALLOW_PROTOCOL"]=""\n'
            + "expected=" + repr(blobs) + "\n"
            'for oid,text in expected.items():\n'
            ' assert subprocess.check_output(["git","-C",root,"cat-file","blob",oid],text=True)==text\n'
            'Path(a[a.index("--output-last-message")+1]).write_text("Historical blobs read with every fetch protocol disabled.")\n'
            'print(json.dumps({"type":"thread.started","thread_id":"offline-history-fixture"}))\n'
        )
        executor.chmod(0o700)
        req = dict(self.req, kind="review", payload={"from_sha": start, "to_sha": commits[-1]})
        with (
            mock.patch.object(work, "followups", return_value=([], [])),
            mock.patch.object(work, "change_context", return_value={}),
        ):
            result = work.review(clone, self.config, req, self.store, str(executor))
        self.assertEqual(result["status"], "needs_coverage_acceptance")
        self.assertEqual(result["object_preparation"]["blob_count"], 3)
        self.assertEqual(result["object_preparation"]["blob_set_digest"], m.digest(sorted(blobs)))
        self.assertTrue(result["source_unchanged"])

    def test_history_preparation_timeout_is_not_a_started_model(self):
        import maintenance_work as work

        sha = m.git(self.root, "rev-parse", "HEAD")
        req = dict(self.req, kind="review", payload={"from_sha": sha, "to_sha": sha})
        with (
            mock.patch.object(work, "followups", return_value=([], [{"changed": True}])),
            mock.patch.object(work, "prepare_review_objects", side_effect=subprocess.TimeoutExpired("git", 120)),
            self.assertRaisesRegex(ValueError, "timed out before sandbox launch"),
        ):
            work.review(self.root, self.config, req, self.store, self.review_executor())
        self.assertFalse((work.job_path(self.store, req["request_id"]) / "native.jsonl").exists())

    def test_unavailable_history_stops_before_worktree_and_executor(self):
        import maintenance_work as work

        clone, start, commits, _, _ = self.partial_review_fixture()
        m.git(clone, "config", "remote.origin.url", str(Path(self.temp.name) / "offline.git"))
        req = dict(self.req, kind="review", payload={"from_sha": start, "to_sha": commits[-1]})
        with (
            mock.patch.object(work, "followups", return_value=([], [])),
            mock.patch.object(work, "change_context", return_value={}),
            self.assertRaisesRegex(ValueError, "historical review objects unavailable"),
        ):
            work.review(clone, self.config, req, self.store, self.review_executor())
        job = work.job_path(self.store, req["request_id"])
        self.assertFalse((job / "review-worktree").exists())
        self.assertFalse((job / "native.jsonl").exists())

    def test_review_returns_contiguous_prefix_and_pending_without_repair_budget(self):
        import maintenance_work as work

        start = m.git(self.root, "rev-parse", "HEAD")
        commits = []
        for content in ("one", "two"):
            (self.root / "docs/change.md").write_text(content)
            m.git(self.root, "add", ".")
            m.git(self.root, "commit", "-qm", content)
            commits.append(m.git(self.root, "rev-parse", "HEAD"))
        config = dict(self.config, review_max_commits=1)
        req = dict(self.req, kind="review", payload={"from_sha": start, "to_sha": commits[-1]})
        with (
            mock.patch.object(work, "followups", return_value=([], [])),
            mock.patch.object(work, "change_context", return_value={}),
        ):
            self.new_run()
            receipt = m.claim(
                self.root, self.root, config, "local-dev", req, self.store, self.review_executor()
            )
        self.assertEqual(receipt["result"]["status"], "needs_coverage_acceptance")
        self.assertEqual(receipt["result"]["window"]["selected_units"], commits[:1])
        self.assertEqual(receipt["result"]["window"]["pending_units"], commits[1:])
        self.assertEqual(receipt["result"]["window"]["proposed_reviewed_through"], commits[0])
        self.assertTrue(receipt["result"]["source_unchanged"])
        self.assertEqual(m.git(self.root, "rev-parse", "HEAD"), commits[-1])
        self.assertEqual(receipt["native_attempt"]["execution_model"], "bounded_code_review")
        with mock.patch.object(m, "review", side_effect=AssertionError("duplicate review")):
            self.assertEqual(self.claim(req), receipt)

    def test_empty_window_still_processes_result_updates(self):
        import maintenance_work as work

        sha = m.git(self.root, "rev-parse", "HEAD")
        req = dict(
            self.req,
            kind="review",
            payload={"from_sha": sha, "to_sha": sha, "followups": [{"issue_number": 141}]},
        )
        update = {"issue_number": 141, "changed": True, "state": "open"}
        with mock.patch.object(
            work, "followups", return_value=([{"comments": ["new result"]}], [update])
        ):
            receipt = self.claim(req, self.review_executor())
        self.assertEqual(receipt["result"]["window"]["selected_units"], [])
        self.assertEqual(receipt["result"]["followup_updates"], [update])

    def test_review_detects_source_mutation_despite_executor_zero_exit(self):
        import maintenance_work as work

        sha = m.git(self.root, "rev-parse", "HEAD")
        req = dict(self.req, kind="review", payload={"from_sha": sha, "to_sha": sha})
        with mock.patch.object(work, "followups", return_value=([], [{"changed": True}])):
            receipt = self.claim(req, self.review_executor(mutate=True))
        self.assertEqual(receipt["result"]["status"], "failed")
        self.assertFalse(receipt["result"]["source_unchanged"])
        self.assertFalse((self.root / "unexpected-write").exists())

    def test_no_new_inputs_does_not_start_a_model_run(self):
        import maintenance_work as work

        sha = m.git(self.root, "rev-parse", "HEAD")
        req = dict(self.req, kind="review", payload={"from_sha": sha, "to_sha": sha})
        with mock.patch.object(work, "followups", return_value=([], [])):
            receipt = self.claim(req, self.review_executor())
        self.assertEqual(receipt["result"]["status"], "no_new_review_inputs")
        self.assertFalse((self.state / "jobs" / req["request_id"]).exists())

    def test_followup_source_digest_detects_edit_to_older_comment(self):
        import maintenance_work as work

        issue = {
            "number": 141,
            "html_url": "https://github.com/example/repo/issues/141",
            "state": "open",
            "updated_at": "2026-11-13T00:00:00Z",
            "body": "incident",
        }
        comments = [
            {"id": 1, "body": "old", "updated_at": "a", "html_url": "first"},
            {"id": 2, "body": "latest unchanged", "updated_at": "b", "html_url": "last"},
        ]

        def fetch():
            return mock.patch.object(
                work, "run", side_effect=[json.dumps(issue), json.dumps([comments])]
            )

        with fetch():
            _, first = work.followups(self.config, [{"issue_number": 141}])
        selection = [{"issue_number": 141, "source_digest": first[0]["source_digest"]}]
        with fetch():
            _, same = work.followups(self.config, selection)
        self.assertFalse(same[0]["changed"])
        comments[0]["body"] = "edited historical evidence"
        with fetch():
            _, edited = work.followups(self.config, selection)
        self.assertTrue(edited[0]["changed"])
        self.assertEqual(edited[0]["latest_comment_url"], "last")
        self.assertNotIn("body", edited[0])

    def test_review_rejects_non_first_parent_boundary(self):
        import maintenance_work as work

        base = m.git(self.root, "rev-parse", "HEAD")
        m.git(self.root, "checkout", "-qb", "side")
        (self.root / "side.txt").write_text("side")
        m.git(self.root, "add", ".")
        m.git(self.root, "commit", "-qm", "side")
        side = m.git(self.root, "rev-parse", "HEAD")
        m.git(self.root, "checkout", "-qb", "main-fixture", base)
        (self.root / "main.txt").write_text("main")
        m.git(self.root, "add", ".")
        m.git(self.root, "commit", "-qm", "main")
        m.git(self.root, "merge", "--no-ff", "-qm", "merge", "side")
        with self.assertRaisesRegex(ValueError, "first-parent"):
            work.inventory(self.root, side, m.git(self.root, "rev-parse", "HEAD"), 12)

    def candidate(self):
        import maintenance_work as work

        base = m.git(self.root, "rev-parse", "HEAD")
        job = work.job_path(self.store, self.req["request_id"])
        job.mkdir(parents=True)
        tree = job / "worktree"
        branch = "codex/loop3-test-candidate"
        m.git(self.root, "worktree", "add", "-b", branch, str(tree), base)
        (tree / "docs/new-untracked.md").write_text("reviewed candidate")
        manifest = work.candidate_manifest(tree, base)
        result = dict(
            manifest,
            status="needs_review",
            candidate_branch=branch,
            issue_number=7,
            candidate_head=base,
        )
        with mock.patch.object(m, "repair", return_value=result):
            self.claim(dict(self.req, kind="repair"), executor="/fixture/executor")
        request = dict(
            self.req,
            kind="handoff",
            request_id="handoff-one",
            payload={
                "target_request_id": self.req["request_id"],
                "candidate_digest": manifest["candidate_digest"],
                "review_ref": "https://github.com/"
                + self.config["repository"]
                + "/issues/7#issuecomment-99",
            },
        )
        return tree, request, manifest

    def test_candidate_digest_binds_untracked_bytes_and_executable_mode(self):
        import maintenance_work as work

        tree, _, first = self.candidate()
        path = tree / "docs/new-untracked.md"
        path.write_text("changed after review")
        second = work.candidate_manifest(tree, first["base_sha"])
        self.assertNotEqual(first["candidate_digest"], second["candidate_digest"])
        path.chmod(0o755)
        self.assertNotEqual(
            second["candidate_digest"],
            work.candidate_manifest(tree, first["base_sha"])["candidate_digest"],
        )

    def test_handoff_stops_before_publication_if_untracked_candidate_changed(self):
        tree, request, _ = self.candidate()
        (tree / "docs/new-untracked.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            m.handoff(self.root, self.config, request, self.store)
        self.assertEqual(m.git(tree, "status", "--porcelain"), "?? docs/new-untracked.md")

    def test_handoff_recovers_lost_pr_create_ack_without_second_pr_or_commit(self):
        import maintenance_work as work

        tree, request, manifest = self.candidate()
        original_run = work.run
        created = []

        def dispatch(argv, **kwargs):
            if argv[0] == "git" and "push" in argv:
                return ""
            if argv[:2] == ["gh", "api"]:
                return json.dumps(
                    {
                        "user": {"login": "rv198-star"},
                        "html_url": request["payload"]["review_ref"],
                        "body": "<!-- maintenance.candidate-review.v1 -->\n```json\n"
                        + json.dumps(
                            {
                                "decision": "accept",
                                "candidate_request_id": self.req["request_id"],
                                "candidate_digest": manifest["candidate_digest"],
                                "candidate_head": manifest["base_sha"],
                                "validation_refs": ["fixture checks passed"],
                                "security_review_ref": "fixture security review",
                            }
                        )
                        + "\n```",
                    }
                )
            value = {
                "url": "https://github.com/" + self.config["repository"] + "/pull/8",
                "headRefOid": m.git(tree, "rev-parse", "HEAD"),
                "baseRefName": "main",
                "state": "OPEN",
                "isDraft": True,
            }
            if argv[:3] == ["gh", "pr", "list"]:
                return json.dumps([value] if created else [])
            if argv[:3] == ["gh", "pr", "create"]:
                created.append(value)
                raise ValueError("lost ack after remote created PR")
            if argv[:3] == ["gh", "pr", "view"]:
                return json.dumps(value)
            return original_run(argv, **kwargs)

        with mock.patch.object(work, "run", side_effect=dispatch):
            with self.assertRaisesRegex(ValueError, "lost ack"):
                m.handoff(self.root, self.config, request, self.store)
            head = m.git(tree, "rev-parse", "HEAD")
            result = m.handoff(self.root, self.config, request, self.store)
        self.assertEqual(len(created), 1)
        self.assertEqual(result["status"], "draft_pr_published")
        self.assertEqual(result["head_sha"], head)

    def test_handoff_rejects_non_accepting_review_even_with_matching_digest(self):
        import maintenance_work as work

        tree, request, manifest = self.candidate()
        run = work.run
        decision = {
            "decision": "reject",
            "candidate_request_id": self.req["request_id"],
            "candidate_digest": manifest["candidate_digest"],
            "candidate_head": manifest["base_sha"],
            "validation_refs": ["checks"],
            "security_review_ref": "security",
        }

        def dispatch(argv, **kwargs):
            if argv[:2] == ["gh", "api"]:
                return json.dumps(
                    {
                        "user": {"login": "rv198-star"},
                        "html_url": request["payload"]["review_ref"],
                        "body": "<!-- maintenance.candidate-review.v1 -->\n```json\n"
                        + json.dumps(decision)
                        + "\n```",
                    }
                )
            return run(argv, **kwargs)

        with mock.patch.object(work, "run", side_effect=dispatch):
            with self.assertRaisesRegex(ValueError, "explicitly accept"):
                m.handoff(self.root, self.config, request, self.store)
        self.assertEqual(m.git(tree, "rev-parse", "HEAD"), manifest["base_sha"])

    def test_handoff_rejects_new_history_with_same_reviewed_content(self):
        import maintenance_work as work

        tree, request, manifest = self.candidate()
        m.git(tree, "commit", "--allow-empty", "-qm", "unreviewed history")
        run = work.run
        decision = {
            "decision": "accept",
            "candidate_request_id": self.req["request_id"],
            "candidate_digest": manifest["candidate_digest"],
            "candidate_head": manifest["base_sha"],
            "validation_refs": ["checks"],
            "security_review_ref": "security",
        }

        def dispatch(argv, **kwargs):
            if argv[:2] == ["gh", "api"]:
                return json.dumps(
                    {
                        "user": {"login": "rv198-star"},
                        "html_url": request["payload"]["review_ref"],
                        "body": "<!-- maintenance.candidate-review.v1 -->\n```json\n"
                        + json.dumps(decision)
                        + "\n```",
                    }
                )
            return run(argv, **kwargs)

        with mock.patch.object(work, "run", side_effect=dispatch):
            with self.assertRaisesRegex(ValueError, "history changed"):
                m.handoff(self.root, self.config, request, self.store)

    def test_handoff_excludes_hidden_index_content_from_reviewed_commit(self):
        import maintenance_work as work

        tree, request, manifest = self.candidate()
        tracked = tree / "requirements.lock.txt"
        original_bytes = tracked.read_bytes()
        tracked.write_text("unreviewed staged content")
        m.git(tree, "add", "requirements.lock.txt")
        tracked.write_bytes(original_bytes)
        self.assertEqual(
            work.candidate_manifest(tree, manifest["base_sha"])["candidate_digest"],
            manifest["candidate_digest"],
        )
        original_run = work.run

        def dispatch(argv, **kwargs):
            if argv[0] == "git" and "push" in argv:
                return ""
            if argv[:2] == ["gh", "api"]:
                decision = {
                    "decision": "accept",
                    "candidate_request_id": self.req["request_id"],
                    "candidate_digest": manifest["candidate_digest"],
                    "candidate_head": manifest["base_sha"],
                    "validation_refs": ["fixture checks"],
                    "security_review_ref": "fixture security review",
                }
                return json.dumps(
                    {
                        "user": {"login": "rv198-star"},
                        "html_url": request["payload"]["review_ref"],
                        "body": "<!-- maintenance.candidate-review.v1 -->\n```json\n"
                        + json.dumps(decision)
                        + "\n```",
                    }
                )
            value = {
                "url": "https://github.com/" + self.config["repository"] + "/pull/8",
                "headRefOid": m.git(tree, "rev-parse", "HEAD"),
                "baseRefName": "main",
                "state": "OPEN",
                "isDraft": True,
            }
            if argv[:3] == ["gh", "pr", "list"]:
                return json.dumps([value])
            if argv[:3] == ["gh", "pr", "view"]:
                return json.dumps(value)
            return original_run(argv, **kwargs)

        with mock.patch.object(work, "run", side_effect=dispatch):
            result = m.handoff(self.root, self.config, request, self.store)
        committed = subprocess.check_output(
            ["git", "-C", str(tree), "show", result["head_sha"] + ":requirements.lock.txt"]
        )
        self.assertEqual(committed, original_bytes)

    def test_interrupted_review_preserves_native_thread_and_worktree_recovery(self):
        import maintenance_work as work

        sha = m.git(self.root, "rev-parse", "HEAD")
        req = dict(self.req, kind="review", payload={"from_sha": sha, "to_sha": sha})
        executor = self.review_executor()
        original_run = subprocess.run

        def dispatch(argv, **kwargs):
            if argv[0] == executor:
                kwargs["stdout"].write(
                    json.dumps({"type": "thread.started", "thread_id": "interrupted-review-thread"})
                    + "\n"
                )
                kwargs["stdout"].flush()
                raise subprocess.TimeoutExpired(argv, 1)
            return original_run(argv, **kwargs)

        with (
            mock.patch.object(work, "followups", return_value=([], [{"changed": True}])),
            mock.patch.object(subprocess, "run", side_effect=dispatch),
        ):
            receipt = self.claim(req, executor)
        self.assertEqual(receipt["result"]["status"], "execution_uncertain")
        self.assertEqual(receipt["result"].get("native_thread_id"), "interrupted-review-thread")
        self.assertEqual(receipt["external_task"]["native_thread_id"], "interrupted-review-thread")
        self.assertTrue(Path(receipt["result"]["recovery"]["worktree"]).is_dir())

    def test_documented_new_handshakes_use_current_contract(self):
        import re
        config = m.load_config(SOURCE)
        text = (SOURCE / "docs/maintenance/loop-contract.md").read_text()
        examples = [json.loads(body) for body in re.findall(r"```json\s*([^`]+?)\s*```", text)]
        requests = [value for value in examples if value.get("schema") == "maintenance.request.v1"]
        self.assertGreaterEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(request["contract_version"], config["contract_version"])
            self.assertEqual(m.request(request, config, request["environment_id"]), request)

    def test_copy_then_delete_changes_candidate_digest_before_handoff(self):
        import maintenance_work as work

        source = self.root / "docs/original.md"
        source.write_text("reviewed candidate")
        m.git(self.root, "add", ".")
        m.git(self.root, "commit", "-qm", "source baseline")
        tree, request, accepted = self.candidate()
        m.git(tree, "add", "docs/new-untracked.md")
        self.assertEqual(
            work.candidate_manifest(tree, accepted["base_sha"])["candidate_digest"],
            accepted["candidate_digest"],
        )
        (tree / "docs/original.md").unlink()
        m.git(tree, "add", "-A")
        self.assertNotEqual(
            work.candidate_manifest(tree, accepted["base_sha"])["candidate_digest"],
            accepted["candidate_digest"],
        )
        with self.assertRaisesRegex(ValueError, "changed"):
            m.handoff(self.root, self.config, request, self.store)

    def test_legitimate_move_binds_deleted_source_and_added_destination(self):
        import maintenance_work as work

        source = self.root / "docs/original.md"
        source.write_text("legitimate move")
        m.git(self.root, "add", ".")
        m.git(self.root, "commit", "-qm", "source baseline")
        base = m.git(self.root, "rev-parse", "HEAD")
        m.git(self.root, "mv", "docs/original.md", "docs/moved.md")
        working = work.candidate_manifest(self.root, base)
        files = {f["path"]: f for f in working["files"]}
        self.assertEqual(set(files), {"docs/original.md", "docs/moved.md"})
        self.assertIsNone(files["docs/original.md"]["sha256"])
        tree = m.git(self.root, "write-tree")
        self.assertEqual(
            work.candidate_manifest(self.root, base, tree)["candidate_digest"],
            working["candidate_digest"],
        )

    def test_rule_triggers_include_renamed_source_path(self):
        import maintenance_checks as checks

        source = self.root / "docs/original.md"
        source.write_text("rule-sensitive source")
        m.git(self.root, "add", ".")
        m.git(self.root, "commit", "-qm", "source baseline")
        base = m.git(self.root, "rev-parse", "HEAD")
        m.git(self.root, "mv", "docs/original.md", "docs/moved.md")
        self.assertIn("docs/original.md", checks.changed_paths(self.root, base))

    def handoff_remote(self, tree, request, manifest, state):
        import maintenance_work as work

        original_run = work.run
        def dispatch(argv, **kwargs):
            if argv[0] == "git" and "push" in argv:
                state["pushes"] = state.get("pushes", 0) + 1
                return ""
            if argv[:2] == ["gh", "api"]:
                decision = {
                    "decision": "accept", "candidate_request_id": self.req["request_id"],
                    "candidate_digest": manifest["candidate_digest"],
                    "candidate_head": manifest["base_sha"], "validation_refs": ["fixture checks"],
                    "security_review_ref": "fixture security review",
                }
                return json.dumps({
                    "user": {"login": "rv198-star"}, "html_url": request["payload"]["review_ref"],
                    "body": "<!-- maintenance.candidate-review.v1 -->\n" + chr(96)*3 + "json\n"
                    + json.dumps(decision) + "\n" + chr(96)*3,
                })
            value = {
                "url": "https://github.com/" + self.config["repository"] + "/pull/8",
                "headRefOid": m.git(tree, "rev-parse", "HEAD"), "baseRefName": "main",
                "state": state.get("state", "OPEN"), "isDraft": state.get("draft", True),
            }
            if argv[:3] == ["gh", "pr", "list"]:
                return json.dumps([value])
            if argv[:3] == ["gh", "pr", "view"]:
                return json.dumps(value)
            return original_run(argv, **kwargs)
        return dispatch

    def test_handoff_reports_closed_merged_and_ready_pr_lifecycle(self):
        import maintenance_work as work

        tree, request, manifest = self.candidate()
        state = {}
        with mock.patch.object(work, "run", side_effect=self.handoff_remote(tree, request, manifest, state)):
            for lifecycle, draft, expected in [
                ("OPEN", True, "draft_pr_published"), ("OPEN", False, "existing_ready_pr"),
                ("CLOSED", False, "handoff_pr_closed"), ("MERGED", False, "handoff_pr_merged"),
            ]:
                with self.subTest(state=lifecycle, draft=draft):
                    state.update(state=lifecycle, draft=draft)
                    result = m.handoff(self.root, self.config, request, self.store)
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(result["pr_state"], lifecycle)
                    self.assertEqual(result["is_draft"], draft)

    def test_handoff_recovers_commit_before_completion_event_without_second_commit(self):
        import maintenance_work as work

        tree, request, manifest = self.candidate()
        state = {}
        original_event = self.store.event
        def crash(key, kind, **data):
            if kind == "candidate_handoff_commit":
                raise OSError("crash after commit before completed journal")
            return original_event(key, kind, **data)
        with mock.patch.object(work, "run", side_effect=self.handoff_remote(tree, request, manifest, state)):
            with mock.patch.object(self.store, "event", side_effect=crash):
                with self.assertRaisesRegex(OSError, "crash after commit"):
                    m.handoff(self.root, self.config, request, self.store)
            head = m.git(tree, "rev-parse", "HEAD")
            self.assertNotEqual(head, manifest["base_sha"])
            self.assertEqual(state.get("pushes", 0), 0)
            result = m.handoff(self.root, self.config, request, self.store)
        self.assertEqual(result["head_sha"], head)
        self.assertEqual(m.git(tree, "rev-list", "--count", manifest["base_sha"] + "..HEAD"), "1")

    def test_formal_g2_rejects_python310_and_accepts_python312_evidence(self):
        self.config["verification_python"] = "3.12"
        receipt, delivery = self.pipeline()
        proof = receipt["snapshot"]["source_status"]["verification"]["sources"][0]["evidence"]
        proof["runtime"] = "3.10.12"
        self.assertFalse(self.audit(receipt, delivery)["checks"]["2_environment"])
        proof["runtime"] = "3.12.14"
        self.assertTrue(self.audit(receipt, delivery)["checks"]["2_environment"])

    def test_trigger_selection_sees_new_untracked_business_path(self):
        import maintenance_checks as checks

        base = m.git(self.root, "rev-parse", "HEAD")
        (self.root / "docs/trigger.md").write_text("new business input")
        paths = checks.changed_paths(self.root, base)
        rule = {
            "lesson_id": "existing-business-rule",
            "state": "active",
            "paths": ["docs/trigger.md"],
        }
        retired = dict(rule, state="retired")
        self.assertEqual(checks.select_rules({"rules": [rule, retired]}, paths), [rule])


if __name__ == "__main__":
    unittest.main()
