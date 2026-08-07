import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from quanthus.pipelines.dwd_downstream_impact_audit import audit_dwd_downstream_impact


class DwdDownstreamImpactAuditTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        dwd_tree = root / "dwd-dws-v2"
        base = dwd_tree / "base" / "dwd-base-202607-v2"
        daily = dwd_tree / "daily"
        base.mkdir(parents=True)
        (daily / "packages" / "2026-08-03" / "run").mkdir(parents=True)
        manifest = {
            "package_id": "dwd-base-202607-v2",
            "content_digest": "sha256:" + "1" * 64,
            "through_date": "2026-07-31",
        }
        (base / "manifest.json").write_text(json.dumps(manifest), encoding="ascii")
        manifest_sha = "sha256:" + hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest()
        pointer = dwd_tree / "base" / "BASE_CURRENT.json"
        pointer.write_text(
            json.dumps(
                {
                    "package_id": manifest["package_id"],
                    "manifest_sha256": manifest_sha,
                    "content_digest": manifest["content_digest"],
                }
            ),
            encoding="ascii",
        )
        daily_manifest = {
            "package_id": "dwd-daily-20260803",
            "market_date": "2026-08-03",
            "base": {
                "package_id": manifest["package_id"],
                "manifest_sha256": manifest_sha,
                "content_digest": manifest["content_digest"],
            },
        }
        (daily / "packages" / "2026-08-03" / "run" / "manifest.json").write_text(
            json.dumps(daily_manifest), encoding="ascii"
        )
        return base, daily, dwd_tree, pointer

    @staticmethod
    def _runner(argv: list[str], **_: object) -> SimpleNamespace:
        if argv[0] != "systemctl":
            return SimpleNamespace(returncode=127, stdout="", stderr="unsupported")
        if argv[1] == "list-unit-files":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=3, stdout="inactive", stderr="")

    def test_verified_compatibility_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, daily, dwd_tree, pointer = self._fixture(Path(temporary))
            report = audit_dwd_downstream_impact(
                base_root=base,
                daily_root=daily,
                dwd_tree=dwd_tree,
                current_pointer=pointer,
                runner=self._runner,
            )
            self.assertEqual(report["summary"]["status"], "verified")
            self.assertFalse(report["summary"]["mutation_performed"])
            self.assertEqual(
                report["checks"]["daily_increment_compatibility"]["checked_package_count"], 1
            )

    def test_daily_identity_mismatch_is_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, daily, dwd_tree, pointer = self._fixture(Path(temporary))
            path = daily / "packages" / "2026-08-03" / "run" / "manifest.json"
            value = json.loads(path.read_text(encoding="ascii"))
            value["base"]["package_id"] = "dwd-base-202607-v1"
            path.write_text(json.dumps(value), encoding="ascii")
            report = audit_dwd_downstream_impact(
                base_root=base,
                daily_root=daily,
                dwd_tree=dwd_tree,
                current_pointer=pointer,
                runner=self._runner,
            )
            ids = {item["finding_id"] for item in report["findings"]}
            self.assertIn("DWDV2-COMPAT-DAILY-BASE", ids)

    def test_enabled_weekly_input_is_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, daily, dwd_tree, pointer = self._fixture(Path(temporary))

            def runner(argv: list[str], **_: object) -> SimpleNamespace:
                if argv[0] == "systemctl" and argv[1] == "list-unit-files":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if (
                    argv[0] == "systemctl"
                    and argv[1] == "is-enabled"
                    and argv[2] == "cn01-dwd-weekly-input.timer"
                ):
                    return SimpleNamespace(returncode=0, stdout="enabled", stderr="")
                return self._runner(argv, **_)

            report = audit_dwd_downstream_impact(
                base_root=base,
                daily_root=daily,
                dwd_tree=dwd_tree,
                current_pointer=pointer,
                runner=runner,
            )
            ids = {item["finding_id"] for item in report["findings"]}
            self.assertIn("DWDV2-COMPAT-WEEKLY-INPUT", ids)


if __name__ == "__main__":
    unittest.main()
