from __future__ import annotations

import errno
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quanthus.pipelines.securities_warehouse.domain_base import (
    _EXPECTED_KINDS,
    DomainBaseError,
    _link_or_copy,
    import_domain_candidate,
    promote_domain_base_candidate,
    publish_domain_base,
    seal_domain_base_candidate,
    verify_domain_base,
    verify_domain_candidate,
    verify_imported_domain_candidate,
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class DomainBaseTests(unittest.TestCase):
    def _candidates(self, root: Path) -> tuple[dict[str, Path], dict[str, Path]]:
        candidates = {}
        source_payloads = {}
        ordered = sorted(_EXPECTED_KINDS.items())
        for index, (domain, kind) in enumerate(ordered):
            candidate = root / "candidates" / domain
            payload = candidate / "payload" / domain / f"part-{index}.parquet"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(f"payload-{domain}".encode())
            count = 4 if index < 8 else 1
            tables = {f"dwd_{domain}_{table}": table + 1 for table in range(count)}
            manifest = {
                "kind": kind,
                "run_id": "run-1",
                "input_plan_digest": "sha256:plan",
                "semantic_digest": f"sha256:{index:064x}",
                "row_counts": tables,
                "files": [
                    {
                        "path": payload.relative_to(candidate).as_posix(),
                        "sha256": _sha(payload),
                        "size_bytes": payload.stat().st_size,
                    }
                ],
            }
            (candidate / "manifest.json").write_text(json.dumps(manifest))
            (candidate / "READY").write_text(_sha(candidate / "manifest.json") + "\n")
            candidates[domain] = candidate
            source_payloads[domain] = payload
        return candidates, source_payloads

    def test_publish_hard_links_33_tables_and_advances_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates, source_payloads = self._candidates(root)

            output = root / "base" / "dwd-base-202607-v2"
            current = output.parent / "BASE_CURRENT.json"
            result = publish_domain_base(
                candidates=candidates,
                output=output,
                current=current,
                package_id=output.name,
                run_id="run-1",
                input_plan_digest="sha256:plan",
                ods_base={"package_id": "ods", "manifest_sha256": "sha256:ods"},
                model_sha256="sha256:model",
                model_extension_sha256="sha256:extension",
                code_revision="a" * 40,
            )

            self.assertEqual(result.table_count, 33)
            self.assertEqual(verify_domain_base(output), result)
            self.assertEqual(
                (output / "payload/listed_company/part-4.parquet").stat().st_ino,
                source_payloads["listed_company"].stat().st_ino,
            )
            self.assertEqual(json.loads(current.read_text())["package_id"], output.name)

    def test_seal_candidate_does_not_create_current_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates, _ = self._candidates(root)
            output = root / "export" / "dwd-base-202607-v2.1"

            result = seal_domain_base_candidate(
                candidates=candidates,
                output=output,
                package_id=output.name,
                run_id="run-1",
                input_plan_digest="sha256:plan",
                ods_base={"package_id": "ods", "manifest_sha256": "sha256:ods"},
                model_sha256="sha256:model",
                model_extension_sha256="sha256:extension",
                code_revision="a" * 40,
            )

            self.assertEqual(result.table_count, 33)
            self.assertFalse((output.parent / "BASE_CURRENT.json").exists())

    def test_promote_sealed_candidate_installs_and_compare_and_swaps_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates, _ = self._candidates(root)
            base_root = root / "base"
            current = base_root / "BASE_CURRENT.json"
            publish_domain_base(
                candidates=candidates,
                output=base_root / "dwd-base-202607-v2",
                current=current,
                package_id="dwd-base-202607-v2",
                run_id="run-1",
                input_plan_digest="sha256:plan",
                ods_base={"package_id": "ods", "manifest_sha256": "sha256:ods"},
                model_sha256="sha256:model",
                model_extension_sha256="sha256:extension",
                code_revision="a" * 40,
            )
            source = root / "release" / "dwd-base-202607-v2.1"
            sealed = seal_domain_base_candidate(
                candidates=candidates,
                output=source,
                package_id=source.name,
                run_id="run-1",
                input_plan_digest="sha256:plan",
                ods_base={"package_id": "ods", "manifest_sha256": "sha256:ods"},
                model_sha256="sha256:model",
                model_extension_sha256="sha256:extension",
                code_revision="a" * 40,
            )
            before = _sha(current)

            result = promote_domain_base_candidate(
                source=source,
                base_root=base_root,
                current=current,
                run_id="release-r1",
                expected_current_sha256=before,
            )

            self.assertTrue(result.installed)
            self.assertEqual(result.previous_package_id, "dwd-base-202607-v2")
            self.assertEqual(result.package.manifest_sha256, sealed.manifest_sha256)
            self.assertEqual(json.loads(current.read_text())["run_id"], "release-r1")
            installed = base_root / source.name
            self.assertEqual(verify_domain_base(installed).content_digest, sealed.content_digest)
            source_payload = next((source / "payload").rglob("*.parquet"))
            installed_payload = installed / source_payload.relative_to(source)
            self.assertEqual(source_payload.stat().st_ino, installed_payload.stat().st_ino)

            repeated = promote_domain_base_candidate(
                source=source,
                base_root=base_root,
                current=current,
                run_id="release-r1",
                expected_current_sha256=before,
            )
            self.assertFalse(repeated.installed)

    def test_promote_sealed_candidate_rejects_current_sha_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates, _ = self._candidates(root)
            source = root / "release" / "dwd-base-202607-v2.1"
            seal_domain_base_candidate(
                candidates=candidates,
                output=source,
                package_id=source.name,
                run_id="run-1",
                input_plan_digest="sha256:plan",
                ods_base={"package_id": "ods", "manifest_sha256": "sha256:ods"},
                model_sha256="sha256:model",
                model_extension_sha256="sha256:extension",
                code_revision="a" * 40,
            )
            base_root = root / "base"
            base_root.mkdir()

            with self.assertRaisesRegex(DomainBaseError, "SHA precondition failed"):
                promote_domain_base_candidate(
                    source=source,
                    base_root=base_root,
                    current=base_root / "BASE_CURRENT.json",
                    run_id="release-r1",
                    expected_current_sha256="sha256:" + "0" * 64,
                )
            self.assertFalse((base_root / source.name).exists())

    def test_promotion_copy_function_falls_back_only_for_cross_device_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.write_bytes(b"verified payload")
            with mock.patch(
                "quanthus.pipelines.securities_warehouse.domain_base.os.link",
                side_effect=OSError(errno.EXDEV, "cross-device link"),
            ):
                self.assertEqual(_link_or_copy(str(source), str(destination)), str(destination))
            self.assertEqual(destination.read_bytes(), source.read_bytes())

            with mock.patch(
                "quanthus.pipelines.securities_warehouse.domain_base.os.link",
                side_effect=PermissionError(errno.EACCES, "permission denied"),
            ), self.assertRaises(PermissionError):
                _link_or_copy(str(source), str(root / "forbidden"))

    def test_import_hardlinks_native_candidate_and_revalidates_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates, source_payloads = self._candidates(root)
            source = candidates["listed_company"]
            output = root / "target" / "listed-company"

            imported = import_domain_candidate(
                source=source,
                output=output,
                domain="listed_company",
                source_run_id="run-1",
                source_plan_digest="sha256:plan",
                target_run_id="run-2",
                target_plan_digest="sha256:plan-2",
                target_bindings={"model_sha256": "sha256:model"},
                source_authority={"sha256": "sha256:audit"},
            )

            verified = verify_domain_candidate(
                output,
                domain="listed_company",
                run_id="run-2",
                input_plan_digest="sha256:plan-2",
            )
            self.assertEqual(verified.semantic_digest, imported.semantic_digest)
            self.assertEqual(
                (output / "payload/listed_company/part-4.parquet").stat().st_ino,
                source_payloads["listed_company"].stat().st_ino,
            )
            self.assertEqual(
                verify_imported_domain_candidate(
                    output,
                    domain="listed_company",
                    run_id="run-2",
                    input_plan_digest="sha256:plan-2",
                    expected_bindings={"model_sha256": "sha256:model"},
                ),
                imported,
            )

            source_payloads["listed_company"].write_bytes(b"mutated")
            with self.assertRaisesRegex(DomainBaseError, "checksum differs"):
                verify_domain_candidate(
                    output,
                    domain="listed_company",
                    run_id="run-2",
                    input_plan_digest="sha256:plan-2",
                )


if __name__ == "__main__":
    unittest.main()
