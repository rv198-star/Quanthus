from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.contracts import EventOutcome
from quanthus.event_library.label_cards import LabelSettlementStatus, load_outcome_label_card
from quanthus.event_library.label_corpus import (
    _EventInput,
    _Observation,
    _settle_event,
    _source_observations,
)
from quanthus.policy import find_project_root

ROOT = find_project_root()
CARD = load_outcome_label_card(ROOT / "config/p4-event-outcome-label.research.v1.json")


def _dates(count: int = 20) -> tuple[date, ...]:
    values: list[date] = []
    cursor = date(2021, 1, 4)
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor += timedelta(days=1)
    return tuple(values)


def _event(*, executable: bool = True, entry_open: str = "100") -> _EventInput:
    return _EventInput(
        event_id="event:test",
        source_snapshot_id="snapshot:test",
        listing_code="600001",
        entry_date=_dates()[0],
        executable=executable,
        entry_open_price=Decimal(entry_open) if executable else None,
        entry_reason_code="executable_at_t_plus_1_open" if executable else "security_suspended",
        entry_evidence_digest="sha256:" + "a" * 64,
    )


def _observations(
    *,
    positive_session: int | None = None,
    adverse_session: int | None = None,
    structural_session: int | None = None,
    suspended_session: int | None = None,
    missing_session: int | None = None,
    termination_session: int | None = None,
    count: int = 20,
    bonus_session: int | None = None,
    cash_session: int | None = None,
) -> tuple[_Observation, ...]:
    values: list[_Observation] = []
    dates = _dates(count)
    valid_to = None
    if termination_session is not None:
        valid_to = dates[termination_session - 2]
    for index, session_date in enumerate(dates, start=1):
        suspended = suspended_session == index
        missing = missing_session == index
        high = Decimal("100")
        low = Decimal("100")
        if positive_session == index:
            high = Decimal("116")
        if adverse_session == index:
            low = Decimal("92")
        if bonus_session == index:
            high = Decimal("58")
            low = Decimal("50")
        if cash_session == index:
            high = Decimal("110")
        structural = structural_session == index
        values.append(
            _Observation(
                session_index=index,
                session_date=session_date,
                high_price=None if suspended or missing else high,
                low_price=None if suspended or missing else low,
                close_price=None if suspended or missing else Decimal("100"),
                observed_tradestatus="0" if suspended else "1",
                cash_dividend_per_share=Decimal("5") if cash_session == index else Decimal("0"),
                bonus_share_ratio=Decimal("1") if bonus_session == index else Decimal("0"),
                total_return_index=(
                    None
                    if suspended or missing
                    else Decimal("97")
                    if structural
                    else Decimal("100")
                ),
                sma20=(
                    None
                    if suspended or missing
                    else Decimal("98")
                    if structural
                    else Decimal("99")
                ),
                sma60=None if suspended or missing else Decimal("98"),
                feature_ready=not suspended and not missing,
                exception_active=False,
                unexplained_price_transition=False,
                valid_to=valid_to,
            )
        )
    return tuple(values)


def test_real_settlement_positive_failure_and_overlap_ordering() -> None:
    positive = _settle_event(_event(), _observations(positive_session=3), CARD)
    assert positive.status is LabelSettlementStatus.SETTLED
    assert positive.outcome is EventOutcome.POSITIVE
    assert positive.terminal_session_index == 3

    adverse = _settle_event(_event(), _observations(adverse_session=3), CARD)
    assert adverse.outcome is EventOutcome.FAILURE
    assert adverse.reason_code == "adverse_threshold"

    overlap = _settle_event(
        _event(), _observations(positive_session=3, adverse_session=3), CARD
    )
    assert overlap.outcome is EventOutcome.FAILURE
    assert overlap.reason_code == "adverse_threshold"

    structural = _settle_event(_event(), _observations(structural_session=4), CARD)
    assert structural.outcome is EventOutcome.FAILURE
    assert structural.reason_code == "structural_break"


def test_real_settlement_preserves_not_executable_censor_and_unavailable() -> None:
    blocked = _settle_event(_event(executable=False), (), CARD)
    assert blocked.status is LabelSettlementStatus.SETTLED
    assert blocked.outcome is EventOutcome.NOT_EXECUTABLE

    censored = _settle_event(
        _event(), _observations(positive_session=2, count=19), CARD
    )
    assert censored.status is LabelSettlementStatus.CENSORED
    assert censored.outcome is None

    unavailable = _settle_event(_event(), _observations(missing_session=5), CARD)
    assert unavailable.status is LabelSettlementStatus.UNAVAILABLE
    assert unavailable.terminal_session_index == 5


def test_suspension_counts_session_without_fabricating_failure() -> None:
    result = _settle_event(_event(), _observations(suspended_session=4), CARD)
    assert result.status is LabelSettlementStatus.SETTLED
    assert result.outcome is EventOutcome.NEUTRAL
    assert result.terminal_session_index == 20


def test_listing_termination_is_structural_failure() -> None:
    result = _settle_event(_event(), _observations(termination_session=5), CARD)
    assert result.status is LabelSettlementStatus.SETTLED
    assert result.outcome is EventOutcome.FAILURE
    assert result.reason_code == "structural_break_listing_termination"
    assert result.terminal_session_index == 5


def test_post_entry_cash_and_bonus_actions_adjust_economic_threshold_path() -> None:
    bonus = _settle_event(_event(), _observations(bonus_session=2), CARD)
    assert bonus.outcome is EventOutcome.POSITIVE
    assert bonus.terminal_session_index == 2

    cash = _settle_event(_event(), _observations(cash_session=2), CARD)
    assert cash.outcome is EventOutcome.POSITIVE
    assert cash.terminal_session_index == 2


class _Bundle:
    def __init__(self, paths: dict[str, Path]) -> None:
        self.paths = paths

    def partition_paths(self, dataset: str) -> tuple[Path, ...]:
        return (self.paths[dataset],)


def _write(path: Path, rows: list[dict[str, object]], schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def test_source_observation_query_maps_code_calendar_actions_and_features(tmp_path: Path) -> None:
    dates = _dates()
    paths = {
        name: tmp_path / f"{name}.parquet"
        for name in (
            "corporate_actions_normalized",
            "daily_ohlcv_none",
            "price_limit_execution_daily",
            "security_code_validity",
            "trading_calendar",
        )
    }
    _write(
        paths["security_code_validity"],
        [
            {
                "security_id": "600001",
                "stock_code": "600001",
                "exchange": "SSE",
                "valid_from": date(2010, 1, 1),
                "valid_to": date(2025, 1, 1),
            }
        ],
        pa.schema(
            [
                ("security_id", pa.string()),
                ("stock_code", pa.string()),
                ("exchange", pa.string()),
                ("valid_from", pa.date32()),
                ("valid_to", pa.date32()),
            ]
        ),
    )
    _write(
        paths["trading_calendar"],
        [
            {"exchange": exchange, "trade_date": day, "is_open": 1}
            for day in dates
            for exchange in ("SSE", "SZSE")
        ],
        pa.schema([("exchange", pa.string()), ("trade_date", pa.date32()), ("is_open", pa.int8())]),
    )
    bar_rows = [
        {"security_id": "600001", "date": day, "high": 101.0, "low": 99.0, "close": 100.0}
        for day in dates
    ]
    _write(
        paths["daily_ohlcv_none"],
        bar_rows,
        pa.schema(
            [
                ("security_id", pa.string()),
                ("date", pa.date32()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
            ]
        ),
    )
    _write(
        paths["price_limit_execution_daily"],
        [
            {"security_id": "600001", "execution_date": day, "observed_tradestatus": "1"}
            for day in dates
        ],
        pa.schema(
            [
                ("security_id", pa.string()),
                ("execution_date", pa.date32()),
                ("observed_tradestatus", pa.string()),
            ]
        ),
    )
    _write(
        paths["corporate_actions_normalized"],
        [
            {
                "security_id": "600001",
                "security_code": "600001",
                "effective_date": dates[1],
                "cash_dividend_per_share": Decimal("1"),
                "bonus_share_ratio": Decimal("0"),
            }
        ],
        pa.schema(
            [
                ("security_id", pa.string()),
                ("security_code", pa.string()),
                ("effective_date", pa.date32()),
                ("cash_dividend_per_share", pa.decimal128(18, 4)),
                ("bonus_share_ratio", pa.decimal128(18, 4)),
            ]
        ),
    )
    feature_panel = tmp_path / "features.parquet"
    _write(
        feature_panel,
        [
            {
                "security_id": "600001",
                "market_date": day,
                "total_return_index": 100.0,
                "sma20": 99.0,
                "sma60": 98.0,
                "feature_ready": True,
                "exception_active": False,
                "unexplained_price_transition": False,
            }
            for day in dates
        ],
        pa.schema(
            [
                ("security_id", pa.string()),
                ("market_date", pa.date32()),
                ("total_return_index", pa.float64()),
                ("sma20", pa.float64()),
                ("sma60", pa.float64()),
                ("feature_ready", pa.bool_()),
                ("exception_active", pa.bool_()),
                ("unexplained_price_transition", pa.bool_()),
            ]
        ),
    )

    result = _source_observations(
        bundle=_Bundle(paths),
        feature_panel_path=feature_panel,
        events=(_event(),),
    )

    rows = result["event:test"]
    assert len(rows) == 20
    assert rows[0].session_index == 1
    assert rows[1].cash_dividend_per_share == Decimal("1.0000")
    assert rows[-1].session_date == dates[-1]
