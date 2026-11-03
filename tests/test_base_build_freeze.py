"""Portable selection tests, independent of production data and execution gates."""
from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.securities_warehouse import base_build_freeze as freeze
from quanthus.pipelines.securities_warehouse import base_build_inputs as inputs


class FrozenSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def fixture(self, through="2026-10-31", days=None):
        days = days or ["2026-10-08", "2026-10-09"]
        target = self.directory / through
        target.mkdir()
        roots = {}
        packages = []
        for key, role, day in [("previous-ods", "ods", None),
                               ("previous-dwd", "dwd", None),
                               *[("daily-" + d, "daily", d) for d in days]]:
            root = target / key
            root.mkdir()
            (root / "reference.json").write_text("calendar-evidence")
            row = {"path": "reference.json", "size_bytes": 17,
                   "sha256": inputs.file_sha(root / "reference.json")}
            if role == "dwd":
                descriptor = {"layout": {}, "evidence": {row["path"]: row["sha256"]}}
                (root / "MATERIALIZATION.json").write_bytes(inputs.canonical(descriptor))
                manifest = {"kind": freeze.KINDS[role], "business_through": "2026-09-30",
                            "materialization_sha256": inputs.file_sha(
                                root / "MATERIALIZATION.json")}
            elif role == "ods":
                manifest = {"kind": freeze.KINDS[role],
                            "content": {"through_date": "2026-09-30", "files": [row]}}
            else:
                manifest = {"kind": freeze.KINDS[role], "market_date": day, "files": [row]}
            (root / "manifest.json").write_bytes(inputs.canonical(manifest))
            manifest_sha = inputs.file_sha(root / "manifest.json")
            (root / "READY").write_text(manifest_sha + "\n")
            roots[key] = str(root)
            packages.append({"id": key, "role": role, "day": day,
                             "manifest_sha256": manifest_sha})
        evidence = {"package_id": "previous-ods", "path": "reference.json",
                    "sha256": row["sha256"]}
        return {"contract": freeze.CONTRACT, "through_date": through,
                "predecessor_through": "2026-09-30", "expected_days": days,
                "expected_days_evidence": evidence, "packages": packages}, roots

    def test_two_periods_same_entry(self):
        for end, days in [("2026-10-15", ["2026-10-08"]),
                          ("2026-10-31", ["2026-10-08", "2026-10-09"])]:
            selection, roots = self.fixture(end, days)
            result = freeze.freeze(selection, roots)
            self.assertEqual(result, freeze.cold_verify(result, roots))
            self.assertEqual(result["status"], "FROZEN_BYTES_ONLY_SEMANTIC_GATES_PENDING")

    def test_cold_verify_relocated_packages_after_original_removed(self):
        selection, roots = self.fixture()
        frozen = freeze.freeze(selection, roots)
        replacement = {}
        for key, original in roots.items():
            destination = self.directory / ("relocated-" + key)
            shutil.copytree(original, destination)
            shutil.rmtree(original)
            replacement[key] = str(destination)
        self.assertEqual(frozen, freeze.cold_verify(frozen, replacement))

    def test_missing_expected_day(self):
        selection, _ = self.fixture()
        selection["packages"].pop()
        with self.assertRaisesRegex(inputs.BuildInputError, "PACKAGES"):
            freeze.validate_selection(selection)

    def test_unbound_calendar(self):
        selection, roots = self.fixture()
        selection["expected_days_evidence"]["path"] = "absent.json"
        with self.assertRaisesRegex(inputs.BuildInputError, "CALENDAR_NOT_IN_CLOSURE"):
            freeze.freeze(selection, roots)

    def test_source_identity_mismatch(self):
        selection, roots = self.fixture()
        selection["packages"][0]["manifest_sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(inputs.BuildInputError, "MANIFEST_IDENTITY"):
            freeze.freeze(selection, roots)

    def test_undeclared_cache_root(self):
        selection, roots = self.fixture()
        roots["staging"] = "/local/hidden/cache"
        with self.assertRaisesRegex(inputs.BuildInputError, "ROOT_BINDINGS"):
            freeze.freeze(selection, roots)

    def test_frozen_tamper(self):
        selection, roots = self.fixture()
        frozen = copy.deepcopy(freeze.freeze(selection, roots))
        frozen["content"]["selection"]["through_date"] = "2026-11-30"
        with self.assertRaisesRegex(inputs.BuildInputError, "FROZEN_IDENTITY"):
            freeze.cold_verify(frozen, roots)

    def test_different_date_in_daily_rejected(self):
        selection, roots = self.fixture()
        selection["packages"][-1]["day"] = "2026-10-12"
        selection["expected_days"][-1] = "2026-10-12"
        with self.assertRaisesRegex(inputs.BuildInputError, "DAILY_DATE"):
            freeze.freeze(selection, roots)

    def test_unknown_contract_cannot_disable_gates(self):
        selection, _ = self.fixture()
        selection["contract"] = "relaxed"
        with self.assertRaisesRegex(inputs.BuildInputError, "SCHEMA"):
            freeze.validate_selection(selection)


if __name__ == "__main__":
    unittest.main()
