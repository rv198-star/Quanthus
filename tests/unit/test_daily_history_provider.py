from __future__ import annotations

from datetime import date

from quanthus.providers.daily_history import collect_daily_history


class _Response:
    error_code = "0"
    error_msg = "success"

    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


class _Login:
    error_code = "0"
    error_msg = "success"


class _BaoStock:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.requests: list[tuple[object, ...]] = []

    def login(self) -> _Login:
        return _Login()

    def logout(self) -> None:
        return None

    def query_history_k_data_plus(self, *args: object, **kwargs: object) -> _Response:
        self.requests.append((*args, kwargs))
        return _Response(self.rows)


def test_collect_daily_history_preserves_status_and_zero_turnover() -> None:
    provider = _BaoStock(
        [
            [
                "2026-07-30",
                "sh.600001",
                "10.0",
                "10.5",
                "9.8",
                "10.2",
                "1000",
                "10200",
                "3",
                "1.0",
                "2.0",
                "1",
                "0",
            ],
            [
                "2026-07-31",
                "sh.600001",
                "10.2",
                "10.2",
                "10.2",
                "10.2",
                "",
                "",
                "3",
                "",
                "0.0",
                "0",
                "1",
            ],
        ]
    )

    bars, statuses, receipts, _ = collect_daily_history(
        ["600001"],
        date(2026, 7, 30),
        date(2026, 7, 31),
        bs_module=provider,
        sleep=lambda _seconds: None,
        configure_timeout=lambda _seconds: True,
    )

    assert len(bars) == 2
    assert bars[1]["volume"] == 0.0
    assert bars[1]["amount"] == 0.0
    assert statuses[1]["tradestatus"] == "0"
    assert statuses[1]["isST"] == "1"
    assert receipts[0]["status"] == "success_nonempty"
    assert receipts[0]["response_rows"] == 2


def test_collect_daily_history_fails_closed_on_identity_mismatch() -> None:
    provider = _BaoStock(
        [
            [
                "2026-07-31",
                "sh.600002",
                "10.0",
                "10.5",
                "9.8",
                "10.2",
                "1000",
                "10200",
                "3",
                "1.0",
                "2.0",
                "1",
                "0",
            ]
        ]
    )

    bars, statuses, receipts, _ = collect_daily_history(
        ["600001"],
        date(2026, 7, 31),
        date(2026, 7, 31),
        bs_module=provider,
        sleep=lambda _seconds: None,
        configure_timeout=lambda _seconds: True,
    )

    assert bars == []
    assert statuses == []
    assert receipts[0]["status"] == "failed"
    assert receipts[0]["reason_code"] == "provider_identity_mismatch"
    assert receipts[0]["attempts"] == 2
