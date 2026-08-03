from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.securities_warehouse.domain_base import (
    _EXPECTED_KINDS,
    publish_domain_base,
    verify_domain_base,
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class DomainBaseTests(unittest.TestCase):
    def test_publish_hard_links_33_tables_and_advances_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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


if __name__ == "__main__":
    unittest.main()
