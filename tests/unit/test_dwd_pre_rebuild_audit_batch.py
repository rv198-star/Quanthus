import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quanthus.pipelines.dwd_pre_rebuild_audit_batch import (
    AUDIT_STEPS,
    DwdPreRebuildAuditBatchError,
    _canonical_digest,
    classify_report,
    run_audit_batch,
)


class DwdPreRebuildAuditBatchTests(unittest.TestCase):
    def _report(self, path: Path, *, step_index: int, status: str = "verified") -> None:
        step = AUDIT_STEPS[step_index]
        if step.name == "lineage_authority":
            value = {
                "kind": step.kind,
                "status": status,
                "dwd": {"package_id": "dwd-base-202607-v2"},
                "mutation_performed": False,
            }
        else:
            value = {
                "kind": step.kind,
                "base_release": {"package_id": "dwd-base-202607-v2"},
                "summary": {"status": status, "mutation_performed": False},
            }
        value["canonical_digest"] = _canonical_digest(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="ascii")

    def test_finding_is_completed_evidence_not_execution_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            self._report(report, step_index=0, status="finding")
            classified = classify_report(
                report,
                step=AUDIT_STEPS[0],
                package_id="dwd-base-202607-v2",
            )
            self.assertEqual(classified["status"], "completed_with_findings")

    def test_digest_mismatch_blocks_adopted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            self._report(report, step_index=0)
            value = json.loads(report.read_text())
            value["summary"]["status"] = "finding"
            report.write_text(json.dumps(value))
            with self.assertRaisesRegex(DwdPreRebuildAuditBatchError, "digest mismatch"):
                classify_report(
                    report,
                    step=AUDIT_STEPS[0],
                    package_id="dwd-base-202607-v2",
                )

    def test_batch_adopts_reports_in_registered_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = {}
            for index, step in enumerate(AUDIT_STEPS):
                report = root / "evidence" / step.report_name
                status = "finding" if index == 1 else "verified"
                self._report(report, step_index=index, status=status)
                evidence[step.name] = report
            state = run_audit_batch(
                repository=root / "repo",
                dwd_root=root / "dwd",
                ods_root=root / "ods",
                plan_root=root / "plan",
                output_root=root / "output",
                batch_id="fixture",
                package_id="dwd-base-202607-v2",
                code_revision="a" * 40,
                selected_steps=[step.name for step in AUDIT_STEPS],
                evidence=evidence,
            )
            self.assertEqual(state["status"], "completed_with_findings")
            self.assertEqual(
                state["steps"]["semantic_contracts"]["evidence_mode"],
                "adopted_external",
            )
            self.assertTrue((root / "output" / "fixture" / "batch-state.json").is_file())

    def test_missing_report_from_tool_is_a_failed_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "quanthus.pipelines.dwd_pre_rebuild_audit_batch.subprocess.run"
        ) as run:
            run.return_value.returncode = 2
            root = Path(temporary)
            with self.assertRaisesRegex(DwdPreRebuildAuditBatchError, "cannot read audit report"):
                run_audit_batch(
                    repository=root / "repo",
                    dwd_root=root / "dwd",
                    ods_root=root / "ods",
                    plan_root=root / "plan",
                    output_root=root / "output",
                    batch_id="fixture",
                    package_id="dwd-base-202607-v2",
                    code_revision="a" * 40,
                    selected_steps=["domain_invariants"],
                    evidence={},
                )
            state = json.loads(
                (root / "output" / "fixture" / "batch-state.json").read_text()
            )
            self.assertEqual(state["status"], "failed")

    def test_batch_id_cannot_escape_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(DwdPreRebuildAuditBatchError, "batch id"):
                run_audit_batch(
                    repository=root / "repo",
                    dwd_root=root / "dwd",
                    ods_root=root / "ods",
                    plan_root=root / "plan",
                    output_root=root / "output",
                    batch_id="../escape",
                    package_id="dwd-base-202607-v2",
                    code_revision="a" * 40,
                    selected_steps=["domain_invariants"],
                    evidence={},
                )


if __name__ == "__main__":
    unittest.main()
