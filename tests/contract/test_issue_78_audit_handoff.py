import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
PACKET = (
    ROOT
    / "docs"
    / "acceptance"
    / "issue-78-dwd-base-v2-pre-rebuild-audit-closure-20260807"
)
EXPECTED_RAW_DIGESTS = {
    "audit-summary.md": "fa086ad743d7f16e70204d7184b557b72a0a7d2b7dc534fe932b6686b8c925f1",
    "findings-ledger.json": "661c7e7a95d1d787b75113d0e075191081a838e52b74b9de31f5d1d83ba75de3",
    "v2.1-remediation-scope.json": (
        "9a279593da4ba668250608172594ce178b6a3ae3e170eb6b64c584a07cc18dd1"
    ),
    "v2.1-build-contract.json": "20a0e5a0c4d2fd56c916a9d53f4729b524139fb5f30d2c618d3bdf0fecad20da",
}
CODE_REVISION = "15bcdb6fccdd7f25caea37c6f509798ecd64991b"


def _load(name: str) -> dict[str, Any]:
    value: Any = json.loads((PACKET / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {name}")
    return cast(dict[str, Any], value)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _assert_canonical_digest(test: unittest.TestCase, value: dict[str, Any]) -> None:
    supplied = value["canonical_digest"]
    unsigned = dict(value)
    unsigned.pop("canonical_digest")
    test.assertEqual(supplied, _canonical_digest(unsigned))


class Issue78AuditHandoffTests(unittest.TestCase):
    def test_imported_cn01_artifact_bytes_are_frozen(self) -> None:
        for name, expected in EXPECTED_RAW_DIGESTS.items():
            actual = hashlib.sha256((PACKET / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)

        sums = (PACKET / "SHA256SUMS").read_text(encoding="ascii")
        for name, expected in EXPECTED_RAW_DIGESTS.items():
            self.assertIn(f"{expected}  {name}", sums)

    def test_machine_artifacts_are_canonical_and_cross_bound(self) -> None:
        ledger = _load("findings-ledger.json")
        scope = _load("v2.1-remediation-scope.json")
        contract = _load("v2.1-build-contract.json")

        for value in (ledger, scope, contract):
            _assert_canonical_digest(self, value)
            self.assertEqual(value["code_revision"], CODE_REVISION)
            self.assertFalse(value["mutation_performed"])

        self.assertEqual(scope["source_ledger_digest"], ledger["canonical_digest"])
        self.assertEqual(contract["finding_ledger_digest"], ledger["canonical_digest"])
        self.assertEqual(contract["remediation_scope_digest"], scope["canonical_digest"])

    def test_all_findings_have_a_closed_v2_1_disposition(self) -> None:
        ledger = _load("findings-ledger.json")
        scope = _load("v2.1-remediation-scope.json")
        findings = ledger["findings"]
        items = scope["items"]

        self.assertEqual(len(findings), 31)
        self.assertEqual(
            Counter(item["severity"] for item in findings),
            {"high": 30, "medium": 1},
        )
        self.assertEqual(
            Counter(item["disposition"] for item in findings),
            {
                "fix_in_v2_1": 25,
                "add_immutable_evidence_then_fix_in_v2_1": 5,
                "retain_with_approved_contract": 1,
            },
        )
        self.assertEqual(
            {item["finding_id"] for item in findings},
            {item["finding_id"] for item in items},
        )
        for item in findings:
            self.assertTrue(item["evidence"])
            self.assertTrue(item["tests"])
            self.assertTrue(item["v2_1_acceptance_check"])

    def test_handoff_does_not_silently_open_the_builder_gate(self) -> None:
        ledger = _load("findings-ledger.json")
        scope = _load("v2.1-remediation-scope.json")
        contract = _load("v2.1-build-contract.json")
        open_scope = ["human_approval_of_findings_and_remediation_scope"]

        self.assertEqual(ledger["builder_gate"], "blocked")
        self.assertEqual(scope["builder_gate"], "blocked")
        self.assertEqual(contract["builder_gate"], "blocked")
        self.assertEqual(contract["status"], "not_authorized")
        self.assertEqual(ledger["open_audit_scope"], open_scope)
        self.assertEqual(scope["open_audit_scope"], open_scope)
        self.assertEqual(contract["open_audit_scope"], open_scope)


if __name__ == "__main__":
    unittest.main()
