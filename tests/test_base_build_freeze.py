"""Portable selection tests, independent of production data and execution gates."""
from __future__ import annotations

import copy
import json
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

    def v2_fixture(self, through="2026-10-31", days=None, predecessor="2.4",
                   predecessor_through="2026-09-30", target="2.5"):
        selection, roots = self.fixture(through, days)
        selection.update(contract=freeze.CONTRACT_V2, predecessor_version=predecessor,
                         target_version=target, predecessor_through=predecessor_through)
        for row in selection["packages"]:
            if row["role"] == "daily":
                continue
            root = Path(roots[row["id"]])
            files = [{"path": path.relative_to(root).as_posix(),
                      "size_bytes": path.stat().st_size, "sha256": inputs.file_sha(path)}
                     for path in sorted(root.rglob("*"))
                     if path.is_file() and path.name not in {"manifest.json", "READY"}]
            manifest = {"kind": inputs.GENERIC_KINDS[row["role"]],
                        "content": {"role": row["role"], "base_version": predecessor,
                                    "through_date": predecessor_through, "files": files}}
            (root / "manifest.json").write_bytes(inputs.canonical(manifest))
            row["manifest_sha256"] = inputs.file_sha(root / "manifest.json")
            (root / "READY").write_text(row["manifest_sha256"] + "\n")
        evidence = self.directory / (through + "-reference")
        evidence.mkdir()
        (evidence / "calendar.json").write_text('{"calendar": "source-observed"}')
        record = {"path": "calendar.json",
                  "size_bytes": (evidence / "calendar.json").stat().st_size,
                  "sha256": inputs.file_sha(evidence / "calendar.json")}
        selection["evidence"] = [{"id": "calendar-reference", "purpose": "calendar_reference",
                                  "parent_manifests": [selection["packages"][0]["manifest_sha256"]],
                                  "files": [record]}]
        selection["expected_days_evidence"] = {"package_id": "calendar-reference",
                                               "path": record["path"], "sha256": record["sha256"]}
        roots["calendar-reference"] = str(evidence)
        return selection, roots

    def test_v2_reference_supplement_is_frozen_without_product_ready(self):
        selection, roots = self.v2_fixture()
        result = freeze.freeze(selection, roots)
        self.assertEqual(result["content"]["contract"], freeze.FROZEN_V2)
        self.assertEqual(result, freeze.cold_verify(result, roots))
        self.assertFalse((Path(roots["calendar-reference"]) / "READY").exists())
        self.assertEqual(result["status"], "FROZEN_BYTES_ONLY_SEMANTIC_GATES_PENDING")

    def test_v2_version_generic_envelopes_for_successive_upgrades(self):
        # Envelope compatibility only; this does not claim a materialize/audit/seal A pass.
        for args in [dict(), dict(through="2026-11-30", days=["2026-11-02"],
                                 predecessor="2.5", predecessor_through="2026-10-31",
                                 target="2.6")]:
            selection, roots = self.v2_fixture(**args)
            self.assertEqual(freeze.freeze(selection, roots),
                             freeze.cold_verify(freeze.freeze(selection, roots), roots))

    def test_v2_relocated_evidence_is_verified_without_old_node(self):
        selection, roots = self.v2_fixture()
        result = freeze.freeze(selection, roots)
        previous = Path(roots["calendar-reference"])
        moved = self.directory / "new-node-reference"
        shutil.copytree(previous, moved)
        shutil.rmtree(previous)
        roots["calendar-reference"] = str(moved)
        self.assertEqual(result, freeze.cold_verify(result, roots))
        (moved / "calendar.json").write_text("conflicting-reference")
        with self.assertRaisesRegex(inputs.BuildInputError, "EVIDENCE_MEMBER_IDENTITY"):
            freeze.cold_verify(result, roots)

    def test_v2_missing_supplement_and_undeclared_extra_are_rejected(self):
        selection, roots = self.v2_fixture()
        with self.assertRaisesRegex(inputs.BuildInputError, "ROOT_BINDINGS"):
            freeze.freeze(selection, {k: v for k, v in roots.items() if k != "calendar-reference"})
        (Path(roots["calendar-reference"]) / "hidden-cache").write_bytes(b"unregistered")
        with self.assertRaisesRegex(inputs.BuildInputError, "EVIDENCE_MEMBER_SET"):
            freeze.freeze(selection, roots)

    def test_v2_unretained_parent_is_not_a_provenance_binding(self):
        selection, _ = self.v2_fixture()
        selection["evidence"][0]["parent_manifests"] = ["sha256:" + "0" * 64]
        with self.assertRaisesRegex(inputs.BuildInputError, "PARENT_NOT_RETAINED"):
            freeze.validate_selection(selection)

    def test_v2_wrong_predecessor_version_and_downgrade_are_rejected(self):
        selection, roots = self.v2_fixture()
        selection["predecessor_version"] = "2.3"
        with self.assertRaisesRegex(inputs.BuildInputError, "PREDECESSOR_VERSION"):
            freeze.freeze(selection, roots)
        selection["target_version"] = "2.2"
        with self.assertRaisesRegex(inputs.BuildInputError, "VERSION_ORDER"):
            freeze.validate_selection(selection)

    def test_v2_generic_metadata_cannot_disguise_layer_role(self):
        selection, roots = self.v2_fixture()
        row = selection["packages"][0]
        root = Path(roots[row["id"]])
        document = json.loads((root / "manifest.json").read_text())
        document["content"]["role"] = "dwd"
        (root / "manifest.json").write_bytes(inputs.canonical(document))
        row["manifest_sha256"] = inputs.file_sha(root / "manifest.json")
        (root / "READY").write_text(row["manifest_sha256"] + "\n")
        # Keep provenance binding valid so this specifically tests layer admission.
        selection["evidence"][0]["parent_manifests"] = [row["manifest_sha256"]]
        with self.assertRaisesRegex(inputs.BuildInputError, "ROLE_KIND"):
            freeze.freeze(selection, roots)


if __name__ == "__main__":
    unittest.main()
