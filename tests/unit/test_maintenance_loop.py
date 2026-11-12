"""Behavioral checks for maintenance intake and interrupted receipt delivery."""
from __future__ import annotations

import copy
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("maintenance_loop", SOURCE / "scripts/maintenance_loop.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.config = json.loads((SOURCE / "config/maintenance-loop.v1.json").read_text())
        self.config["authority_files"] = ["docs/maintenance/loop-contract.md"]
        self.config["dependency_files"] = ["requirements.lock.txt"]
        (self.root / "docs/maintenance").mkdir(parents=True)
        (self.root / "docs/maintenance/loop-contract.md").write_text("versioned authority\n")
        (self.root / "config").mkdir()
        (self.root / "config/maintenance-loop.v1.json").write_text(json.dumps(self.config))
        (self.root / "requirements.lock.txt").write_text("fixture-dependency==1\n")
        for args in [("init",), ("config", "user.email", "fixture@example.invalid"),
                     ("config", "user.name", "Fixture"), ("config", "commit.gpgsign", "false"),
                     ("remote", "add", "origin", "https://github.com/" + self.config["repository"] + ".git"),
                     ("add", "."), ("commit", "-qm", "fixture baseline")]:
            m.git(self.root, *args)
        self.state = Path(self.temp.name) / "state"
        self.store = m.Store(self.state)
        self.addCleanup(self.store.close)
        self.req = {"schema": "maintenance.request.v1", "repository": self.config["repository"],
                    "contract_version": "1.0.0", "environment_id": "local-dev", "request_id": "request-one",
                    "batch_id": "batch-one", "challenge": "challenge-one", "kind": "acceptance_probe", "payload": {}}

    def claim(self, req=None, executor=None):
        return m.claim(self.root, self.root, self.config, "local-dev", req or self.req, self.store, executor)

    def comment(self, req=None, author="rv198-star"):
        return {"id": 1, "user": {"login": author},
                "body": m.REQUEST_MARKER + "\n```json\n" + json.dumps(req or self.req) + "\n```"}

    def test_config_rejects_authority_escape(self):
        altered = dict(self.config, authority_files=["../private.md"])
        (self.root / "config/maintenance-loop.v1.json").write_text(json.dumps(altered))
        with self.assertRaises(ValueError):
            m.load_config(self.root)

    def test_wrong_actor_is_not_an_intake(self):
        self.assertIsNone(m.parse_comment(self.comment(author="outside-user"), self.config, "local-dev"))

    def test_malformed_envelope_is_rejected(self):
        row = self.comment()
        row["body"] += "\nrun some command"
        with self.assertRaises(ValueError):
            m.parse_comment(row, self.config, "local-dev")

    def test_wrong_environment_is_left_for_its_collector(self):
        row = self.comment(dict(self.req, environment_id="other-env"))
        self.assertIsNone(m.parse_comment(row, self.config, "local-dev"))

    def test_invalid_contract_and_identity_are_rejected(self):
        for patch in ({"repository": "other/repo"}, {"contract_version": "old"},
                      {"request_id": "../escape"}, {"request_id": "x:fixture"},
                      {"kind": "shell"}, {"challenge": ""}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                m.request(dict(self.req, **patch), self.config, "local-dev")

    def test_same_id_with_different_scope_is_rejected(self):
        self.store.reserve(self.req)
        with self.assertRaisesRegex(ValueError, "different contract"):
            self.store.reserve(dict(self.req, challenge="changed"))

    def test_snapshot_preserves_private_work_and_reports_counts(self):
        lockfile = self.root / "requirements.lock.txt"
        lockfile.write_text("LOCAL_PRIVATE_CONTENT\n")
        m.git(self.root, "add", "requirements.lock.txt")
        lockfile.write_text("LOCAL_PRIVATE_CONTENT_TWO\n")
        (self.root / "untracked.txt").write_text("PRIVATE_UNTRACKED_CONTENT")
        result = m.snapshot(self.root, self.root, self.config, "local-dev")
        self.assertEqual(result["tracked_dirty_count"], 1)
        self.assertEqual(result["untracked_count"], 1)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(lockfile.read_text(), "LOCAL_PRIVATE_CONTENT_TWO\n")

    def test_snapshot_rejects_another_repository(self):
        m.git(self.root, "remote", "set-url", "origin", "https://github.com/other/repo.git")
        with self.assertRaisesRegex(ValueError, "origin"):
            m.snapshot(self.root, self.root, self.config, "local-dev")

    def test_duplicate_probe_reuses_exact_receipt(self):
        first = self.claim()
        second = self.claim()
        self.assertEqual(first, second)
        self.assertEqual(first["result"]["status"], "pass")
        self.assertTrue(all(first["result"]["checks"].values()))
        self.assertEqual(len(self.store.rows()), 2)  # request and isolated fixture

    def test_receipt_survives_a_new_store(self):
        first = self.claim()
        reopened = m.Store(self.state)
        try:
            self.assertEqual(reopened.get(self.req["request_id"]), first)
        finally:
            reopened.close()
        unsigned = dict(first)
        unsigned.pop("receipt_digest")
        self.assertEqual(m.digest(unsigned), first["receipt_digest"])

    def test_concurrent_receiver_cannot_execute_same_state(self):
        with (self.state / "claim.lock").open("a") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "busy"):
                self.claim()

    def test_interrupted_coding_is_not_redispatched(self):
        req = dict(self.req, kind="repair", payload={"issue_number": 7})
        self.store.reserve(req)
        with mock.patch.object(m, "repair", side_effect=AssertionError("duplicate dispatch")):
            result = self.claim(req)
        self.assertEqual(result["result"]["status"], "execution_uncertain")

    def test_missing_executor_has_explicit_wait_and_can_resume(self):
        req = dict(self.req, kind="repair", payload={"issue_number": 7})
        self.assertEqual(self.claim(req)["result"]["status"], "awaiting_executor")
        with mock.patch.object(m, "repair", return_value={"status": "needs_review"}) as execute:
            result = self.claim(req, "/configured/existing-agent")
        self.assertEqual(result["result"]["status"], "needs_review")
        execute.assert_called_once()

    def test_timeout_requires_native_execution_inspection(self):
        req = dict(self.req, kind="repair", payload={"issue_number": 7})
        with mock.patch.object(m, "repair", side_effect=subprocess.TimeoutExpired("fixture", 1)):
            result = self.claim(req, "/configured/existing-agent")
        self.assertEqual(result["result"]["status"], "execution_uncertain")

    def test_effect_requires_real_use_or_explicit_wait(self):
        event = self.claim()["result"]["effect"]
        rule_sha = event["consumed_rule_digest"]
        for patch in ({"consumed_rule_digest": "0" * 64}, {"baseline_ref": ""},
                      {"eligibility": ""}, {"observed_uses": 0}, {"evidence_refs": []}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                m.effect_event(dict(event, **patch), rule_sha)
        self.assertEqual(m.effect_event(dict(event, decision="wait", observed_uses=0), rule_sha)["decision"], "wait")

    def test_effect_roundtrip_uses_tracked_owning_rule(self):
        event = self.claim()["result"]["effect"]
        req = dict(self.req, request_id="effect-one", kind="effect", payload=event)
        result = self.claim(req)
        self.assertEqual(result["result"]["status"], "recorded")
        self.assertEqual(result["result"]["effect"]["judgment_authority"], "requesting-agent")

    def test_effect_rejects_rule_outside_repository(self):
        event = self.claim()["result"]["effect"]
        event["rule_path"] = "../../private.md"
        result = self.claim(dict(self.req, request_id="effect-outside", kind="effect", payload=event))
        self.assertEqual(result["result"]["status"], "failed")

    def test_outbox_defers_then_publishes_and_rereads_after_restart(self):
        receipt = self.claim()
        with mock.patch.object(m.subprocess, "run") as network:
            self.assertEqual(m.publish(self.config, self.store, []), [])
            network.assert_not_called()
        reopened = m.Store(self.state)
        try:
            response = mock.Mock(returncode=0, stdout=json.dumps({"html_url": "https://github.com/owner/repo/issues/1#issuecomment-2"}))
            with mock.patch.object(m.subprocess, "run", return_value=response) as network:
                urls = m.publish(self.config, reopened, [])
            network.assert_called_once()
            body = json.loads(network.call_args.kwargs["input"])["body"]
            self.assertIn(receipt["receipt_digest"], body)
            self.assertEqual(len(urls), 1)
            self.assertEqual(m.publish(self.config, reopened, []), [])
        finally:
            reopened.close()

    def test_lost_network_ack_is_reconciled_without_duplicate_comment(self):
        receipt = self.claim()
        self.store.published(self.req["request_id"], "deferred-once")
        body = m.RECEIPT_MARKER + "\n```json\n" + json.dumps(receipt, ensure_ascii=False, indent=2) + "\n```"
        comment = {"user": {"login": "rv198-star"}, "body": body,
                   "html_url": "https://github.com/owner/repo/issues/1#issuecomment-2"}
        with mock.patch.object(m.subprocess, "run") as network:
            self.assertEqual(m.publish(self.config, self.store, [comment]), [comment["html_url"]])
            network.assert_not_called()

    def test_poll_continues_after_one_invalid_request(self):
        malformed = self.comment(dict(self.req, request_id="../bad"))
        with mock.patch.object(m, "gh_comments", return_value=[malformed, self.comment()]), mock.patch.object(m, "publish", return_value=[]):
            result = m.poll(self.root, self.root, self.config, "local-dev", self.store)
        self.assertEqual(len(result["rejected_requests"]), 1)
        self.assertEqual(self.store.get(self.req["request_id"])["result"]["status"], "pass")

    def native_fixture(self, *, outside=False):
        executable = Path(self.temp.name) / "fixture-executor"
        target = "outside-scope.txt" if outside else "docs/fixture-change.md"
        executable.write_text("#!" + sys.executable + "\n" +
            "import sys,json,subprocess\nfrom pathlib import Path\n" +
            "root=Path(sys.argv[sys.argv.index('--cd')+1])\n" +
            f"path=root/{target!r}\npath.parent.mkdir(parents=True,exist_ok=True)\npath.write_text('bounded fixture')\n" +
            ("subprocess.run(['git','-C',str(root),'add','.'],check=True)\n"
             "subprocess.run(['git','-C',str(root),'commit','-qm','fixture change'],check=True)\n" if outside else "") +
            "print(json.dumps({'type':'thread.started','thread_id':'native-fixture-thread'}))\n")
        executable.chmod(0o700)
        original = m.run
        def transport(argv, **kwargs):
            if argv[0] == "gh":
                return json.dumps({"state": "open", "html_url": "https://github.com/owner/repo/issues/7",
                                   "title": "bounded fixture", "body": "write one allowed source file"})
            return original(argv, **kwargs)
        req = dict(self.req, kind="repair", payload={"issue_number": 7})
        with mock.patch.object(m, "run", side_effect=transport):
            return self.claim(req, str(executable))

    def test_native_process_returns_candidate_and_preserves_original_workspace(self):
        result = self.native_fixture()
        self.assertEqual(result["result"]["status"], "needs_review")
        self.assertEqual(result["result"]["native_thread_id"], "native-fixture-thread")
        self.assertEqual(result["result"]["changed_path_count"], 1)
        self.assertFalse((self.root / "docs/fixture-change.md").exists())

    def test_committed_outside_scope_change_is_still_detected(self):
        result = self.native_fixture(outside=True)
        self.assertEqual(result["result"]["status"], "failed")
        self.assertFalse(result["result"]["within_declared_paths"])
        self.assertEqual(result["result"]["changed_path_count"], 1)


if __name__ == "__main__":
    unittest.main()
