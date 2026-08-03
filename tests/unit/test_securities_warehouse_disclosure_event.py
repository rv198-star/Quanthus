from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from quanthus.pipelines.securities_warehouse.disclosure_event import (
    DisclosureEventBuildError,
    _load_vocabulary,
    _provider_timestamp,
    _select_sources,
)


class DisclosureEventTests(unittest.TestCase):
    def test_date_only_availability_uses_shanghai_end_of_day(self) -> None:
        value = _provider_timestamp(date(2026, 7, 31), "published_at")

        self.assertEqual(value.tzinfo, timezone.utc)
        self.assertEqual(value.isoformat(), "2026-07-31T15:59:59.999999+00:00")

    def test_vocabulary_requires_governed_date_policy(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "vocabulary.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "quanthus_disclosure_event_vocabulary",
                        "mapping_version": "fixture-v1",
                        "date_only_availability": "wrong",
                        "action_types": {"cash_div": "CASH_DIVIDEND"},
                        "action_statuses": {"实施": "IMPLEMENTED"},
                        "term_types": {"cash_per_share": "CASH_AMOUNT_PER_SHARE"},
                        "factor_methods": {
                            "adjust_factor": {
                                "method": "PROVIDER",
                                "direction": "PROVIDER_NATIVE",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(DisclosureEventBuildError, "unsupported"):
                _load_vocabulary(path)

    def test_source_selection_binds_container_to_archive(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "payload/canonical/info_archive.db": b"archive",
                "payload/canonical/ref_corporate_actions.parquet": b"actions",
                "payload/canonical/dividend_history.parquet": b"dividends",
                "payload/canonical/ref_adjustment_factors.parquet": b"factors",
            }
            for relative, payload in paths.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            def record(identity: str, relative: str, rows: int | None):
                payload = paths[relative]
                return {
                    "mapping_identity": identity,
                    "record_type": "file",
                    "disposition": "modeled",
                    "role": "source_evidence",
                    "path": relative,
                    "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                    "row_count": rows,
                }

            records = [
                record("announcement_archive", "payload/canonical/info_archive.db", None),
                record(
                    "corporate_actions",
                    "payload/canonical/ref_corporate_actions.parquet",
                    2,
                ),
                record(
                    "dividend_history",
                    "payload/canonical/dividend_history.parquet",
                    3,
                ),
                record(
                    "adjustment_factors_pit",
                    "payload/canonical/ref_adjustment_factors.parquet",
                    4,
                ),
                {
                    "mapping_identity": "announcement_archive|sqlite_table|announcements",
                    "record_type": "container_member",
                    "disposition": "modeled",
                    "container_source_id": "announcement_archive",
                    "container_path": "payload/canonical/info_archive.db",
                    "path": "payload/canonical/info_archive.db#table=announcements",
                    "member": "announcements",
                    "row_count": 5,
                },
            ]

            selected = _select_sources(records, root)

            self.assertEqual(selected["announcements"].row_count, 5)
            self.assertEqual(
                selected["announcements"].path,
                selected["announcement_archive"].path,
            )


if __name__ == "__main__":
    unittest.main()
