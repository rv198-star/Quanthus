from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.schedule_contracts import ScheduleContractError, load_schedule_contract

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "schedules" / "hermes-ods-schedule.v1.json"
LATE_CONFIG = ROOT / "config" / "schedules" / "hermes-ods-schedule.late.v1.json"
TRADING_SESSION = ROOT / "config" / "trading-session.v1.json"


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
        self.assertEqual(
            contract.contract_revision,
            "hermes-full-ods-scope-cn01-throughput-20260731",
        )
        self.assertEqual(len(contract.lanes), 5)

    def test_repository_late_hermes_schedule_is_valid(self) -> None:
        contract = load_schedule_contract(LATE_CONFIG)

        self.assertEqual(contract.schedule_id, "hermes-ods-schedule-late-v1")
        self.assertEqual(
            contract.contract_revision,
            "hermes-late-window-cn01-throughput-20260814",
        )
        self.assertEqual(len(contract.lanes), 5)

    def test_late_schedule_rejects_unversioned_deadline_change(self) -> None:
        payload = json.loads(LATE_CONFIG.read_text(encoding="utf-8"))
        lane = next(
            lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
        )
        lane["completion_deadline"] = "21:00:00"
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "schedule.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ScheduleContractError, "20:46"):
            load_schedule_contract(path)

    def test_2026_trading_session_contract_is_explicit_and_sparse(self) -> None:
        payload = json.loads(TRADING_SESSION.read_text(encoding="utf-8"))
        schedule = json.loads(CONFIG.read_text(encoding="utf-8"))
        intraday = next(lane for lane in schedule["lanes"] if lane["id"] == "intraday-archive")
        minute_contract = intraday["minute_bar_contract"]
        after_hours = payload["after_hours_fixed_price"]

        self.assertEqual(payload["effective_start"], "2026-07-06")
        self.assertEqual(after_hours["matching_start"], "15:05:00")
        self.assertEqual(after_hours["matching_end"], "15:30:00")
        self.assertTrue(after_hours["sparse_events"])
        self.assertFalse(after_hours["included_in_dense_minute_grid"])
        self.assertTrue(after_hours["included_in_final_daily_volume_and_amount"])
        self.assertEqual(payload["risk_warning_daily_buy_cap"]["shares"], 500000)
        self.assertEqual(minute_contract["policy_version"], payload["policy_version"])
        self.assertEqual(
            minute_contract["after_hours_fixed_price_contract"],
            after_hours["dataset_contract"],
        )
        self.assertEqual(
            minute_contract["session_scope"],
            payload["regular_session"]["minute_archive_scope"],
        )

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

    def test_rejects_market_preflight_without_fresh_calendar(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["session_preflight"].pop("calendar_refresh")

        with self.assertRaisesRegex(ScheduleContractError, "calendar_refresh"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_market_acquisition_before_daily_totals_are_final(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["trigger"]["time"] = "15:10:00"

        with self.assertRaisesRegex(ScheduleContractError, "17:30"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_market_sla_without_the_full_100_percent_margin(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["timing_sla"]["safety_margin_percent"] = 50

        with self.assertRaisesRegex(ScheduleContractError, "100% margin"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_incomplete_provider_readiness_sentinel_set(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["provider_readiness"]["sentinel_indices"] = ["sh.000001"]

        with self.assertRaisesRegex(ScheduleContractError, "provider readiness contract"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_unversioned_collection_window_change(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["collection_window_experiment"]["nodes"][1]["timer_start"] = "18:00:00"

        with self.assertRaisesRegex(ScheduleContractError, "collection window experiment"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_collection_window_without_activation_grace(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["collection_window_experiment"]["nodes"][0].pop(
                "activation_grace_seconds"
            )

        with self.assertRaisesRegex(ScheduleContractError, "collection window experiment"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_promoting_provisional_sla_without_five_sessions(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["timing_sla"]["validation_status"] = "accepted"

        with self.assertRaisesRegex(ScheduleContractError, "must remain provisional"):
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

    def test_rejects_minute_contract_that_claims_after_hours_coverage(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(lane for lane in payload["lanes"] if lane["id"] == "intraday-archive")
            lane["minute_bar_contract"]["covers_after_hours_fixed_price"] = True

        with self.assertRaisesRegex(ScheduleContractError, "exclude after-hours"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_weekly_overwrite_of_daily_output(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "weekly-corrections"
            )
            lane["may_overwrite_daily"] = True

        with self.assertRaisesRegex(ScheduleContractError, "must not overwrite"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_market_lane_without_valuation(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "market-daily-core"
            )
            lane["datasets"].remove("valuation_daily_delta")

        with self.assertRaisesRegex(ScheduleContractError, "daily valuation"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_weekly_lane_without_complete_statements(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(
                lane for lane in payload["lanes"] if lane["id"] == "weekly-corrections"
            )
            lane["primary_datasets"].remove("financial_statements")

        with self.assertRaisesRegex(ScheduleContractError, "complete fundamentals"):
            load_schedule_contract(self._mutated(mutate))

    def test_rejects_1m_lane_without_after_hours_receipts(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            lane = next(lane for lane in payload["lanes"] if lane["id"] == "intraday-archive")
            stream = next(stream for stream in lane["streams"] if stream["bar_size"] == "1m")
            stream["additional_datasets"].remove("after_hours_fixed_price_receipts")

        with self.assertRaisesRegex(ScheduleContractError, "per-security receipts"):
            load_schedule_contract(self._mutated(mutate))


if __name__ == "__main__":
    unittest.main()
