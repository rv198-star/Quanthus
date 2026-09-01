import unittest
from pathlib import Path
from types import SimpleNamespace

from quanthus.pipelines.securities_warehouse.daily_domain import (
    _DEFERRED_DAILY_ARTIFACTS,
    _RECOGNIZED_ODS_ONLY_DAILY_ARTIFACTS,
    DailyDomainBuildError,
    _audit_non_output_daily_artifacts,
)


class _FakeParquet:
    rows: dict[str, int] = {}

    class ParquetFile:
        def __init__(self, path: Path) -> None:
            self.metadata = SimpleNamespace(num_rows=_FakeParquet.rows[str(path)])


def _selected_artifacts(*, recognized_rows: int, deferred_rows: dict[str, int] | None = None):
    rows = deferred_rows or {}
    selected: dict[str, dict[str, dict[str, tuple[Path, object]]]] = {}
    identities = list(_RECOGNIZED_ODS_ONLY_DAILY_ARTIFACTS) + list(
        _DEFERRED_DAILY_ARTIFACTS
    )
    _FakeParquet.rows = {}
    for lane, name in identities:
        path = Path("/fixture") / lane / f"{name}.parquet"
        selected.setdefault(lane, {"artifacts": {}})["artifacts"][name] = (
            path,
            SimpleNamespace(sha256=f"sha256:{name}"),
        )
        _FakeParquet.rows[str(path)] = (
            recognized_rows
            if (lane, name) in _RECOGNIZED_ODS_ONLY_DAILY_ARTIFACTS
            else rows.get(name, 0)
        )
    return selected


class DailyDomainNonOutputArtifactTest(unittest.TestCase):
    def test_accounts_for_financial_snapshot_as_ods_only_for_any_row_count(self) -> None:
        for row_count in (0, 5, 19):
            with self.subTest(row_count=row_count):
                audited = _audit_non_output_daily_artifacts(
                    _selected_artifacts(recognized_rows=row_count), parquet=_FakeParquet
                )

                self.assertEqual(len(audited), 1)
                self.assertEqual(audited[0]["lane"], "calendar_daily_events")
                self.assertEqual(audited[0]["artifact"], "financial_disclosures_delta")
                self.assertEqual(audited[0]["disposition"], "ods_only")
                self.assertEqual(audited[0]["row_count"], row_count)
                self.assertIn("publish_deadline", audited[0]["reason"])

    def test_other_deferred_non_empty_artifacts_still_fail_closed(self) -> None:
        selected = _selected_artifacts(
            recognized_rows=19, deferred_rows={"dividends_delta": 1}
        )

        with self.assertRaisesRegex(
            DailyDomainBuildError,
            "calendar_daily_events/dividends_delta \\(1 rows\\)",
        ):
            _audit_non_output_daily_artifacts(selected, parquet=_FakeParquet)


if __name__ == "__main__":
    unittest.main()
