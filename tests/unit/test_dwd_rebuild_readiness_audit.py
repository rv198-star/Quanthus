import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from quanthus.pipelines.dwd_rebuild_readiness_audit import (
    _canonical_digest,
    audit_dwd_rebuild_readiness,
)


def _sealed(path: Path, value: dict) -> None:
    payload = dict(value)
    payload["canonical_digest"] = _canonical_digest(value)
    path.write_text(json.dumps(payload), encoding="ascii")


class DwdRebuildReadinessAuditTests(unittest.TestCase):
    def test_missing_lock_and_pilot_are_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base"
            plan = root / "plan"
            workspace = root / "nfs"
            coordination = root / "coordination"
            base.mkdir()
            plan.mkdir()
            workspace.mkdir()
            (base / "manifest.json").write_text(
                json.dumps(
                    {
                        "package_id": "dwd-base-202607-v2",
                        "content_digest": "sha256:" + "1" * 64,
                        "through_date": "2026-07-31",
                    }
                ),
                encoding="ascii",
            )
            (base / "READY").write_text("ready\n", encoding="ascii")
            (plan / "capacity-estimate.json").write_text(
                json.dumps(
                    {
                        "requirement": {
                            "estimated_final_dwd_bytes": 10,
                        }
                    }
                ),
                encoding="ascii",
            )
            pointer = root / "BASE_CURRENT.json"
            pointer.write_text(
                json.dumps(
                    {
                        "package_id": "dwd-base-202607-v2",
                        "manifest_sha256": "sha256:" + "f" * 64,
                    }
                ),
                encoding="ascii",
            )
            policy = root / "policy.json"
            _sealed(
                policy,
                {
                    "cn01_rebuild_execution_topology": {
                        "single_writer": True,
                        "local_state": {
                            "install_safety_margin_bytes": 10,
                        },
                        "nfs": {
                            "mount_point": str(workspace),
                            "filesystem_type": "nfs",
                            "required_options": ["rw"],
                            "minimum_free_bytes": 1,
                            "write_probe": "passed",
                            "atomic_rename_probe": "passed",
                            "same_mount_hardlink_probe": "passed",
                            "cn01_local_flock_probe": "passed",
                        },
                        "publication": {
                            "expected_current_manifest_sha256": "sha256:" + "f" * 64,
                            "pointer_update": "compare_and_swap",
                            "rollback": "restore_v2_pointer",
                        },
                        "execution_limits": {},
                    }
                },
            )
            mounts = root / "mounts"
            mounts.write_text(f"nfs-server {workspace} nfs rw,hard 0 0\n", encoding="ascii")

            def fake_statvfs(path: str) -> SimpleNamespace:
                if path == str(workspace):
                    return SimpleNamespace(f_bavail=100, f_frsize=1)
                return SimpleNamespace(f_bavail=100, f_frsize=1)

            report = audit_dwd_rebuild_readiness(
                repository=root,
                base_root=base,
                plan_root=plan,
                workspace_root=workspace,
                coordination_root=coordination,
                lock_path=coordination / "locks" / "rebuild.lock",
                current_pointer=pointer,
                ingress_path=root / "incoming",
                installed_successor=root / "successor",
                policy_path=policy,
                mounts_path=mounts,
                statvfs_fn=fake_statvfs,
            )
            self.assertEqual(report["summary"]["status"], "finding")
            ids = {item["finding_id"] for item in report["findings"]}
            self.assertIn("DWDV2-READINESS-SINGLE-WRITER", ids)
            self.assertIn("DWDV2-READINESS-PARTITION-PILOT", ids)


if __name__ == "__main__":
    unittest.main()
