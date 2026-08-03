from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from quanthus.pipelines.securities_warehouse.ownership_capital import (
    OwnershipCapitalBuildError,
    _file_source,
    _single_record,
)


class OwnershipCapitalTests(unittest.TestCase):
    def test_single_record_rejects_ambiguous_authority(self) -> None:
        records = [
            {
                "mapping_identity": "shareholder_count",
                "record_type": "file",
                "disposition": "modeled",
                "role": "source_evidence",
            },
            {
                "mapping_identity": "shareholder_count",
                "record_type": "file",
                "disposition": "modeled",
                "role": "source_evidence",
            },
        ]

        with self.assertRaisesRegex(OwnershipCapitalBuildError, "found 2"):
            _single_record(
                records,
                identity="shareholder_count",
                record_type="file",
                disposition="modeled",
                role="source_evidence",
            )

    def test_file_source_binds_size_and_digest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "payload/canonical/ref_shareholder_count.parquet"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fixture")
            digest = "sha256:" + hashlib.sha256(b"fixture").hexdigest()
            records = [
                {
                    "mapping_identity": "shareholder_count",
                    "record_type": "file",
                    "disposition": "modeled",
                    "role": "source_evidence",
                    "path": path.relative_to(root).as_posix(),
                    "sha256": digest,
                    "size_bytes": 7,
                    "row_count": 3,
                }
            ]

            source = _file_source(
                records,
                root,
                source_id="shareholder_count",
                identity="shareholder_count",
                role="source_evidence",
            )

            self.assertEqual(source.row_count, 3)
            self.assertEqual(source.sha256, digest)


if __name__ == "__main__":
    unittest.main()
