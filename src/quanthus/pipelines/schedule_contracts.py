"""Validation for the versioned Hermes ODS scheduler contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleContractError(RuntimeError):
    """Raised when a scheduler contract violates a platform invariant."""


_LANE_IDS = {
    "bootstrap",
    "market-daily-core",
    "calendar-daily-events",
    "intraday-archive",
    "weekly-corrections",
}
_CORRECTION_CLASSES = {"same", "missing_in_daily", "changed_upstream", "conflict"}


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScheduleContractError(f"{field} must be an object")
    return value


def _string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScheduleContractError(f"{field} must be a non-empty string")
    return value


def _string_set(value: Any, *, field: str) -> set[str]:
    if not isinstance(value, list) or not value:
        raise ScheduleContractError(f"{field} must be a non-empty list")
    if any(not isinstance(item, str) or not item for item in value):
        raise ScheduleContractError(f"{field} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ScheduleContractError(f"{field} must not contain duplicates")
    return set(value)


@dataclass(frozen=True)
class ScheduleContract:
    schedule_id: str
    contract_revision: str
    timezone: str
    lanes: tuple[str, ...]
    config_path: Path


def load_schedule_contract(path: Path) -> ScheduleContract:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduleContractError(f"cannot load schedule contract: {path}") from exc
    root = _mapping(payload, field="schedule contract")
    if root.get("schema_version") != 1:
        raise ScheduleContractError("unsupported schedule contract schema_version")

    timezone = _string(root.get("timezone"), field="timezone")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleContractError(f"unknown timezone: {timezone}") from exc
    if timezone != "Asia/Shanghai":
        raise ScheduleContractError("Hermes schedules must use Asia/Shanghai")

    ownership = _mapping(root.get("ownership"), field="ownership")
    if ownership.get("hermes") != "immutable_ods_only":
        raise ScheduleContractError("Hermes ownership must be immutable_ods_only")
    if ownership.get("oci") != "dwd_dws_ads_and_pg_publication":
        raise ScheduleContractError("OCI must own DWD/DWS/ADS and PG publication")
    if ownership.get("hermes_direct_curated_pg_writes") is not False:
        raise ScheduleContractError("Hermes direct curated PG writes must be disabled")

    publication = _mapping(root.get("publication"), field="publication")
    required_states = ["scheduled", "preflight", "acquiring", "sealed", "ready"]
    if publication.get("success_states") != required_states:
        raise ScheduleContractError("publication success_states must preserve the state order")
    if set(publication.get("terminal_states", [])) != {
        "closed_session",
        "failed",
        "partial",
    }:
        raise ScheduleContractError("publication terminal_states are incomplete")
    for invariant in ("ready_written_last", "immutable_release_id", "rerun_new_attempt"):
        if publication.get(invariant) is not True:
            raise ScheduleContractError(f"publication.{invariant} must be true")

    lane_values = root.get("lanes")
    if not isinstance(lane_values, list):
        raise ScheduleContractError("lanes must be a list")
    lanes: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(lane_values):
        lane = _mapping(value, field=f"lanes[{index}]")
        lane_id = _string(lane.get("id"), field=f"lanes[{index}].id")
        if lane_id in lanes:
            raise ScheduleContractError(f"duplicate lane id: {lane_id}")
        lanes[lane_id] = lane
    if set(lanes) != _LANE_IDS:
        missing = sorted(_LANE_IDS - set(lanes))
        extra = sorted(set(lanes) - _LANE_IDS)
        raise ScheduleContractError(f"lane set mismatch: missing={missing}, extra={extra}")

    _validate_bootstrap(lanes["bootstrap"])
    _validate_market_daily(lanes["market-daily-core"])
    _validate_calendar_events(lanes["calendar-daily-events"])
    _validate_intraday(lanes["intraday-archive"])
    _validate_weekly(lanes["weekly-corrections"])

    return ScheduleContract(
        schedule_id=_string(root.get("schedule_id"), field="schedule_id"),
        contract_revision=_string(
            root.get("contract_revision"), field="contract_revision"
        ),
        timezone=timezone,
        lanes=tuple(lanes),
        config_path=path.resolve(),
    )


def _validate_bootstrap(lane: Mapping[str, Any]) -> None:
    trigger = _mapping(lane.get("trigger"), field="bootstrap.trigger")
    if trigger.get("kind") != "manual_issue_bound" or lane.get("recurring") is not False:
        raise ScheduleContractError("bootstrap must be manual, issue-bound, and non-recurring")
    if lane.get("enabled") is not False:
        raise ScheduleContractError("bootstrap must not be enabled by the scheduler")


def _validate_market_daily(lane: Mapping[str, Any]) -> None:
    trigger = _mapping(lane.get("trigger"), field="market-daily-core.trigger")
    if trigger.get("weekdays") != [1, 2, 3, 4, 5]:
        raise ScheduleContractError("market-daily-core must run Monday through Friday")
    if trigger.get("kind") != "timer" or trigger.get("time") != "15:40:00":
        raise ScheduleContractError(
            "market-daily-core must start at 15:40 after final daily totals"
        )
    expected_finality = {
        "auction_close_time": "15:00:00",
        "final_trading_time": "15:30:00",
        "finalized_acquisition_not_before": "15:40:00",
    }
    if any(lane.get(key) != value for key, value in expected_finality.items()):
        raise ScheduleContractError("market-daily-core trading-day finality contract is invalid")
    preflight = _mapping(lane.get("session_preflight"), field="market-daily-core.session_preflight")
    if preflight.get("exchanges") != ["SSE", "SZSE"]:
        raise ScheduleContractError("market preflight must independently check SSE and SZSE")
    expected = {
        "both_open": "acquire_market_data",
        "both_closed": "calendar_only_release",
        "missing_or_disagree": "fail_without_provider_calls_or_ready",
    }
    if any(preflight.get(key) != value for key, value in expected.items()):
        raise ScheduleContractError("market session preflight must use the three-state policy")
    if lane.get("completion_deadline") != "20:00:00":
        raise ScheduleContractError("market-daily-core must retain the T+5H deadline")


def _validate_calendar_events(lane: Mapping[str, Any]) -> None:
    trigger = _mapping(lane.get("trigger"), field="calendar-daily-events.trigger")
    if trigger.get("days") != "every_natural_day":
        raise ScheduleContractError("calendar events must collect on every natural day")
    if lane.get("can_create_market_session") is not False:
        raise ScheduleContractError("event acquisition must not create a market session")


def _validate_intraday(lane: Mapping[str, Any]) -> None:
    if lane.get("blocks_market_daily_core") is not False:
        raise ScheduleContractError("intraday archive must remain outside the T+5H critical path")
    preflight = _mapping(lane.get("session_preflight"), field="intraday-archive.session_preflight")
    if preflight.get("exchanges") != ["SSE", "SZSE"]:
        raise ScheduleContractError("intraday preflight must independently check SSE and SZSE")
    expected = {
        "both_open": "acquire_direct_streams",
        "both_closed": "calendar_only_release",
        "missing_or_disagree": "fail_without_provider_calls_or_ready",
    }
    if any(preflight.get(key) != value for key, value in expected.items()):
        raise ScheduleContractError("intraday session preflight must use the three-state policy")
    minute_contract = _mapping(
        lane.get("minute_bar_contract"), field="intraday-archive.minute_bar_contract"
    )
    expected_contract = {
        "policy_version": "cn-a-share-trading-session.v1",
        "session_phase": "auction",
        "session_scope": "continuous_trading_and_closing_call_auction",
        "covers_after_hours_fixed_price": False,
        "after_hours_fixed_price_contract": "after_hours_fixed_price_trades.v1",
    }
    if any(minute_contract.get(key) != value for key, value in expected_contract.items()):
        raise ScheduleContractError(
            "intraday minute bars must explicitly exclude after-hours fixed-price trading"
        )
    streams_value = lane.get("streams")
    if not isinstance(streams_value, list):
        raise ScheduleContractError("intraday-archive.streams must be a list")
    streams = {
        _string(value.get("bar_size"), field="intraday stream bar_size"): value
        for value in streams_value
        if isinstance(value, Mapping)
    }
    if set(streams) != {"1m", "5m"}:
        raise ScheduleContractError("intraday archive must contain direct 1m and 5m streams")
    if any(stream.get("acquisition") != "direct" for stream in streams.values()):
        raise ScheduleContractError("1m and 5m archives must both use direct acquisition")
    if streams["5m"].get("may_derive_from") is not None:
        raise ScheduleContractError("formal 5m data must not be synthesized from 1m")
    locks = {stream.get("lock") for stream in streams.values()}
    checkpoints = {stream.get("checkpoint") for stream in streams.values()}
    if None in locks or len(locks) != 2 or None in checkpoints or len(checkpoints) != 2:
        raise ScheduleContractError("intraday streams require independent locks and checkpoints")


def _validate_weekly(lane: Mapping[str, Any]) -> None:
    if lane.get("output_mode") != "independent_correction_revision":
        raise ScheduleContractError("weekly corrections must publish independent revisions")
    if lane.get("may_overwrite_daily") is not False:
        raise ScheduleContractError("weekly corrections must not overwrite daily output")
    classes = _string_set(lane.get("correction_classes"), field="weekly correction_classes")
    if classes != _CORRECTION_CLASSES:
        raise ScheduleContractError("weekly correction classification is incomplete")
    trigger = _mapping(lane.get("trigger"), field="weekly-corrections.trigger")
    if trigger.get("weekdays") != [6]:
        raise ScheduleContractError("weekly corrections must run on Saturday")
