"""Process-level contract tests for the bounded live smoke script.

These tests never reach the network: they pin the credential and
endpoint guardrails that must fail closed before any wire use.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE = REPO_ROOT / "scripts" / "decision_api_smoke.py"


def run_smoke(args, env_extra=None):
    env = dict(os.environ)
    env.pop("QUANTHUS_DECISION_API_KEY", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SMOKE), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        cwd=REPO_ROOT,
    )


class SmokeCredentialGuardTests(unittest.TestCase):
    def test_missing_explicit_key_fails_closed_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_smoke(["--output", str(Path(tmp) / "out.json")])
        self.assertEqual(result.returncode, 2)
        self.assertIn("SMOKE_KEY_UNAVAILABLE", result.stderr)
        self.assertIn("no local configuration fallback", result.stderr)

    def test_unapproved_host_rejected_even_with_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_smoke(
                [
                    "--base-url",
                    "https://unapproved-host.example.com",
                    "--output",
                    str(Path(tmp) / "out.json"),
                ],
                env_extra={"QUANTHUS_DECISION_API_KEY": "probe-fake-key"},
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("SMOKE_ENDPOINT_UNAPPROVED", result.stderr)

    def test_plain_http_rejected_even_with_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_smoke(
                [
                    "--base-url",
                    "http://gw.72live.com",
                    "--output",
                    str(Path(tmp) / "out.json"),
                ],
                env_extra={"QUANTHUS_DECISION_API_KEY": "probe-fake-key"},
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("SMOKE_ENDPOINT_SCHEME", result.stderr)

    def test_userinfo_url_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_smoke(
                [
                    "--base-url",
                    "https://user:pw@gw.72live.com",
                    "--output",
                    str(Path(tmp) / "out.json"),
                ],
                env_extra={"QUANTHUS_DECISION_API_KEY": "probe-fake-key"},
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("SMOKE_ENDPOINT_USERINFO", result.stderr)

    def test_chain_mode_missing_keys_fails_closed(self) -> None:
        chain = {
            "schema_version": "quanthus.decision-providers.v1",
            "providers": [
                {
                    "id": "probe",
                    "type": "openai-ga",
                    "base_url": "https://gw.72live.com",
                    "endpoint_path": "/v1/decisions",
                    "model": "google/gemini-decision-1",
                    "api_key_env": "QUANTHUS_SMOKE_TEST_ABSENT_KEY",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            chain_path = Path(tmp) / "chain.json"
            chain_path.write_text(json.dumps(chain))
            result = run_smoke(
                ["--chain", str(chain_path), "--output", str(Path(tmp) / "out.json")]
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("SMOKE_CHAIN_CONFIG", result.stderr)
        self.assertIn("KEY_MISSING", result.stderr)


if __name__ == "__main__":
    unittest.main()
