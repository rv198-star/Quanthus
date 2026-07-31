from __future__ import annotations

import json
import unittest
from pathlib import Path

from quanthus.cli import _parser


class OperationalADSBaseBoundaryTests(unittest.TestCase):
    def test_cli_separates_build_check_and_pointer_promotion(self) -> None:
        parser = _parser()
        build = parser.parse_args(
            [
                "build-operational-ads-base",
                "--plan",
                "/tmp/plan.json",
                "--output",
                "/tmp/ads-base-test-v1",
                "--code-revision",
                "a" * 40,
            ]
        )
        check = parser.parse_args(
            ["check-operational-ads-base", "--bundle", "/tmp/ads-base-test-v1"]
        )
        preflight = parser.parse_args(
            [
                "preflight-operational-ads-base",
                "--plan",
                "/tmp/plan.json",
                "--dwd-current",
                "/tmp/dwd-current.json",
                "--dws-current",
                "/tmp/dws-current.json",
            ]
        )
        promote = parser.parse_args(
            [
                "promote-operational-ads-base",
                "--bundle",
                "/tmp/ads-base-test-v1",
                "--base-root",
                "/tmp/base",
                "--expected-current-manifest-sha256",
                "absent",
            ]
        )
        self.assertEqual(build.plan, Path("/tmp/plan.json"))
        self.assertEqual(preflight.dwd_current, Path("/tmp/dwd-current.json"))
        self.assertEqual(check.bundle, Path("/tmp/ads-base-test-v1"))
        self.assertEqual(promote.base_root, Path("/tmp/base"))
        smoke = parser.parse_args(
            [
                "smoke-operational-ads-replay",
                "--bundle",
                "/tmp/ads-base-test-v1",
                "--scope-start",
                "2024-01-02",
                "--scope-end",
                "2024-01-02",
            ]
        )
        self.assertEqual(smoke.scope_start.isoformat(), "2024-01-02")

    def test_operator_requires_clean_checkout_and_only_promotes_base_current(self) -> None:
        publisher = (
            Path(__file__).resolve().parents[2] / "ops" / "bin" / "publish-operational-ads-base"
        ).read_text(encoding="utf-8")
        self.assertIn("repository checkout must be clean", publisher)
        self.assertIn("preflight-operational-ads-base", publisher)
        self.assertIn("promote-operational-ads-base", publisher)
        self.assertLess(
            publisher.index("preflight-operational-ads-base"),
            publisher.index("build-operational-ads-base"),
        )
        self.assertLess(
            publisher.index("smoke-operational-ads-replay"),
            publisher.index("promote-operational-ads-base"),
        )
        self.assertIn("--expected-current-manifest-sha256", publisher)
        self.assertNotIn("release-set_CURRENT", publisher)
        self.assertNotIn("GO_FOR_CUTOVER", publisher)
        self.assertNotIn("/daily", publisher)

    def test_policy_and_docs_keep_authority_roles_and_open_gates_explicit(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        policy = json.loads(
            (repository / "config" / "operational-ads-base.v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            policy["authority"],
            {
                "holder": "oci",
                "research_only": True,
                "execution_authorized": False,
            },
        )
        self.assertEqual(len(policy["required_upstream_roles"]), 6)
        self.assertEqual(len(policy["tables"]), 10)
        self.assertEqual(policy["tables"]["daily_market_summary"]["role"], "decision_visible")
        self.assertEqual(policy["tables"]["benchmark_curve"]["role"], "outcome_only")
        self.assertNotIn("label_only", policy["consumer_roles"]["replay_runner"])

        design = (
            repository
            / "docs"
            / "design"
            / "lld"
            / "01-data-foundation"
            / "operational-ads-base.md"
        ).read_text(encoding="utf-8")
        runbook = (
            repository / "docs" / "operations" / "dual-lane-data-platform-runbook.md"
        ).read_text(encoding="utf-8")
        evidence = (
            repository / "docs" / "acceptance" / "meta-data-base-003-ads-base-202607" / "README.md"
        ).read_text(encoding="utf-8")
        self.assertIn("META-DATA-BASE-003", design)
        self.assertIn("BASE_CURRENT.json", design)
        self.assertIn("Operational ADS historical Base on OCI", runbook)
        self.assertLess(
            runbook.index("seal-checks all declared"),
            runbook.index("advances only"),
        )
        self.assertIn("Phase A provisional Base accepted; Phase B final Base remains open", evidence)
        self.assertIn("No guessed", evidence)
