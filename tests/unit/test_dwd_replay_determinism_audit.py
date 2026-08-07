import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.dwd_replay_determinism_audit import audit_dwd_replay_determinism


class DwdReplayDeterminismAuditTests(unittest.TestCase):
    def test_missing_base_replay_is_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base"
            plan = root / "plan"
            base.mkdir()
            plan.mkdir()
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
            (plan / "input-plan.json").write_text(json.dumps({"run_id": "run", "plan_digest": "sha256:" + "2" * 64}), encoding="ascii")
            report = audit_dwd_replay_determinism(base_root=base, plan_root=plan)
            self.assertEqual(report["summary"]["status"], "finding")
            self.assertEqual(report["summary"]["reason_codes"], ["base_replay_evidence_missing"])
            self.assertFalse(report["summary"]["mutation_performed"])


if __name__ == "__main__":
    unittest.main()
