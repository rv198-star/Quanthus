from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.schedule_contracts import ScheduleContractError, load_schedule_contract

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "schedules" / "hermes-ods-schedule.v1.json"


class ScheduleContractTests(unittest.TestCase):
    def _mutated(self, mutate: object) -> Path:
        payload = json.loads(CONFIG.read_text(encoding="utf-8"))
        assert callable(mutate)
        mutate(payload)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "schedule.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_repository_schedule_is_valid(self) -> None:
        contract = load_schedule_contract(CONFIG)

        self.assertEqual(contract.schedule_id, "hermes-ods-schedule-v1")
        self.assertEqual(len(contract.lanes), 5)

    def test_rejects_hermes_curated_pg_writes(self) -> None:
        path = self._mutated(
            lambda payload: payload["ownership"].update(
                {"hermes_direct_curated_pg_writes": True}
            )
        )

        with self.assertRaisesRegex(ScheduleContractError, "PG writes must be disabled"):
            load_schedule_contract(path)

    def test_rejects_ambiguous_market_calendar_policy(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["session_preflight"]["missing_or_disagree"] = "continue"

        with self.assertRaisesRegex(ScheduleContractError, "three-state policy"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_synthesized_formal_5m(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(lane for lane in payload["lanes"] if lane["id"] == "intraday-archive")
            stream = next(stream for stream in lane["streams"] if stream["bar_size"] == "5m")
            stream["may_derive_from"] = "1m"

        with self.assertRaisesRegex(ScheduleContractError, "must not be synthesized"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_intraday_without_fail_closed_session_preflight(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(lane for lane in payload["lanes"] if lane["id"] == "intraday-archive")
            lane["session_preflight"]["both_closed"] = "acquire_direct_streams"

        with self.assertRaisesRegex(ScheduleContractError, "intraday session preflight"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_weekly_overwrite_of_daily_output(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "weekly-corrections"
            )
            lane["may_overwrite_daily"] = True

        with self.assertRaisesRegex(ScheduleContractError, "must not overwrite"):
            load_schedule_contract(self._mutated(mutate))


if __name__ == "__main__":
    unittest.main()
