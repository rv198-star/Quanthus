"""Regression and negative checks derived from all eight bootstrap exit criteria."""

from __future__ import annotations

import copy
import fcntl
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "maintenance_loop", SOURCE / "scripts/maintenance_loop.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
from maintenance_audit import valid_chain, verify
from maintenance_sources import collect, native_tasks, verification


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.config = json.loads((SOURCE / "config/maintenance-loop.v1.json").read_text())
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
            if relative == "config/maintenance-consumer-rule.v1.json" or relative.startswith(
                "scripts/"
            ):
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
                        {"evidence": {"operational_fingerprint": fingerprint, "exit_code": 0}}
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
        import os

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


if __name__ == "__main__":
    unittest.main()
