import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

P1_STACK_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)

if P1_STACK_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.st_status import build_st_status_revision, write_current_pointer


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class StStatusBuildTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        candidate = root / "candidate"
        candidate.mkdir()
        sessions = [date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 8)]
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600000",
                        "stock_code": "600000",
                        "exchange": "SSE",
                        "market_date": day,
                    }
                    for day in sessions
                ]
            ),
            candidate / "daily_universe.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600000",
                        "date": day,
                        "isST": state,
                        "tradestatus": tradestatus,
                        "stock_code": "600000",
                        "exchange": "SSE",
                        "raw_shard": "raw/600000.json",
                    }
                    for day, state, tradestatus in zip(
                        sessions, ("0", "1", "1"), ("1", "1", "0"), strict=True
                    )
                ]
            ),
            candidate / "daily_isST.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600000",
                        "stock_code": "600000",
                        "exchange": "SSE",
                        "status_label": "non_st_class",
                        "is_st_class": False,
                        "effective_start": sessions[0],
                        "effective_end": sessions[0],
                        "knowledge_cutoff": sessions[1],
                        "source": "baostock.isST",
                        "source_revision": "candidate-v1",
                        "raw_shard": "raw/600000.json",
                        "source_market_date_start": sessions[0],
                    },
                    {
                        "security_id": "SSE-600000",
                        "stock_code": "600000",
                        "exchange": "SSE",
                        "status_label": "st_class",
                        "is_st_class": True,
                        "effective_start": sessions[1],
                        "effective_end": sessions[2],
                        "knowledge_cutoff": sessions[2],
                        "source": "baostock.isST",
                        "source_revision": "candidate-v1",
                        "raw_shard": "raw/600000.json",
                        "source_market_date_start": sessions[1],
                    },
                ]
            ),
            candidate / "st_status_pit.parquet",
        )
        (candidate / "META.json").write_text("{}\n", encoding="utf-8")
        checksum_lines = []
        for name in (
            "daily_isST.parquet",
            "daily_universe.parquet",
            "st_status_pit.parquet",
        ):
            digest = hashlib.sha256((candidate / name).read_bytes()).hexdigest()
            checksum_lines.append(f"{digest}  {name}")
        (candidate / "CHECKSUMS.sha256").write_text(
            "\n".join(checksum_lines) + "\n", encoding="utf-8"
        )
        return candidate

    def _policy(self, root: Path) -> Path:
        policy = root / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "policy_version": "test-v1",
                    "authoritative_binary_source": "baostock.isST",
                    "execution_visibility": "next actual session",
                    "unknown_behavior": "block_new_positions",
                    "detail_behavior": "do_not_infer_subclasses",
                    "incremental": {"lookback_calendar_days": 90},
                }
            ),
            encoding="utf-8",
        )
        return policy

    def test_execution_uses_prior_actual_session_and_fails_closed_on_first_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_st_status_revision(
                candidate=self._candidate(root),
                output=root / "formal-v1",
                revision="formal-v1",
                policy_path=self._policy(root),
            )
            rows = pq.read_table(result.output / "st_status_execution_daily.parquet").to_pylist()

        self.assertEqual(result.execution_daily_rows, 3)
        self.assertEqual(result.unknown_rows, 1)
        self.assertEqual(rows[0]["status_label"], "unknown")
        self.assertFalse(rows[0]["can_open_position"])
        self.assertEqual(rows[1]["source_market_date"], date(2025, 1, 3))
        self.assertEqual(rows[1]["execution_date"], date(2025, 1, 6))
        self.assertEqual(rows[1]["status_label"], "non_st_class")
        self.assertEqual(rows[2]["source_market_date"], date(2025, 1, 6))
        self.assertEqual(rows[2]["execution_date"], date(2025, 1, 8))
        self.assertEqual(rows[2]["status_label"], "st_class")

    def test_revision_is_immutable_and_pointer_is_atomic_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "formal-v1"
            kwargs = {
                "candidate": self._candidate(root),
                "output": output,
                "revision": "formal-v1",
                "policy_path": self._policy(root),
            }
            build_st_status_revision(**kwargs)
            with self.assertRaises(FileExistsError):
                build_st_status_revision(**kwargs)
            pointer = root / "CURRENT.json"
            write_current_pointer(revision_dir=output, pointer=pointer)
            payload = json.loads(pointer.read_text(encoding="utf-8"))

        self.assertEqual(payload["revision"], "formal-v1")
        self.assertRegex(payload["manifest_sha256"], r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
