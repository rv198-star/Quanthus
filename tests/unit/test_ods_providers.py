"""Tests for repository-owned ODS provider entrypoints."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines import lane_runner
from quanthus.providers import after_hours_tdx, fundamentals, ods_snapshot, trading_calendar


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _paths(home: Path, tmp_path: Path) -> lane_runner.LanePaths:
    return lane_runner.LanePaths(
        home=home,
        shadow_root=tmp_path / "shadow",
        calendar=home / "ref_trading_calendar.parquet",
        lock_root=tmp_path / "locks",
    )


def test_ods_snapshot_windows_local_source_and_writes_only_output(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = home / "ref_corporate_actions.parquet"
    _write_parquet(
        source,
        [
            {"stock_code": "000001", "announce_date": "2026-07-20"},
            {"stock_code": "000002", "announce_date": "2026-07-24"},
        ],
    )
    output = tmp_path / "attempt" / "corporate-actions.parquet"

    result = ods_snapshot.collect(
        "corporate-actions",
        date(2026, 7, 23),
        date(2026, 7, 24),
        output,
        home,
    )

    rows = pq.read_table(output).to_pylist()
    assert result["selected_row_count"] == 1
    assert [row["stock_code"] for row in rows] == ["000002"]
    assert output.with_suffix(".parquet.meta.json").is_file()
    assert sorted(path.name for path in home.iterdir()) == [source.name]


def test_ods_snapshot_fails_closed_when_source_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="source missing"):
        ods_snapshot.collect(
            "dividends",
            date(2026, 7, 1),
            date(2026, 7, 24),
            tmp_path / "attempt" / "dividends.parquet",
            tmp_path / "home",
        )


def test_calendar_refresh_requires_fresh_target_and_preserves_history(
    tmp_path: Path,
) -> None:
    source = tmp_path / "base" / "ref_trading_calendar.parquet"
    _write_parquet(
        source,
        [
            {"exchange": exchange, "trade_date": "2026-07-25", "is_open": 0}
            for exchange in ("SSE", "SZSE", "BSE")
        ],
    )
    output = tmp_path / "attempt" / "ref_trading_calendar.parquet"

    result = trading_calendar.refresh_calendar(
        source=source,
        output=output,
        target_date=date(2026, 7, 27),
        fetcher=lambda _start, _end: [
            {"trade_date": "2026-07-26", "is_open": 0},
            {"trade_date": "2026-07-27", "is_open": 1},
        ],
    )

    rows = pq.read_table(output).to_pylist()
    target = [row for row in rows if str(row["trade_date"]) == "2026-07-27"]
    assert result.target_state == "both_open"
    assert result.fetched_base_rows == 2
    assert result.merged_rows == 9
    assert {row["exchange"] for row in target} == {"SSE", "SZSE", "BSE"}
    assert {row["is_open"] for row in target} == {1}
    assert result.metadata_path.is_file()
    assert output.stat().st_mode & 0o777 == 0o644


def test_calendar_refresh_rejects_response_without_target(tmp_path: Path) -> None:
    source = tmp_path / "base" / "ref_trading_calendar.parquet"
    _write_parquet(
        source,
        [
            {"exchange": exchange, "trade_date": "2026-07-25", "is_open": 0}
            for exchange in ("SSE", "SZSE")
        ],
    )

    with pytest.raises(trading_calendar.CalendarRefreshError, match="does not contain target"):
        trading_calendar.refresh_calendar(
            source=source,
            output=tmp_path / "attempt" / "ref_trading_calendar.parquet",
            target_date=date(2026, 7, 27),
            fetcher=lambda _start, _end: [
                {"trade_date": "2026-07-26", "is_open": 0}
            ],
        )


def test_provider_spec_supports_repository_python_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_parquet(
        home / "ref_corporate_actions.parquet",
        [{"stock_code": "000001", "announce_date": "2026-07-24"}],
    )
    spec = tmp_path / "providers.json"
    spec.write_text(
        json.dumps(
            {
                "lanes": {
                    "calendar-daily-events": {
                        "corporate_actions_delta": {
                            "argv": [
                                "{python}",
                                "-m",
                                "quanthus.providers.ods_snapshot",
                                "corporate-actions",
                                "--start",
                                "{window_start}",
                                "--end",
                                "{market_date}",
                                "--output",
                                "{output}",
                                "--home",
                                "{home}",
                            ]
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_ODS_PROVIDER_SPEC", str(spec))
    paths = _paths(home, tmp_path)

    rows, meta = lane_runner._run_configured_provider_collector(
        lane_id="calendar-daily-events",
        dataset="corporate_actions_delta",
        output=tmp_path / "attempt" / "corporate-actions.parquet",
        paths=paths,
        market_date=date(2026, 7, 24),
        window_start=date(2026, 7, 24),
    )

    assert len(rows) == 1
    assert meta["exit_code"] == 0
    assert meta["argv"][0]
    assert "quanthus.providers.ods_snapshot" in meta["argv"]


def test_f10_collector_uses_dynamic_directory_and_per_security_receipts() -> None:
    class Client:
        def F10C(self, *, symbol: str):
            return [{"name": "latest", "filename": f"{symbol}.txt", "start": 1, "length": 9}]

        def F10(self, *, symbol: str, name: str):
            assert name == "latest"
            return f"{symbol} profile\nupdate date: none"

    rows, receipts, metadata = fundamentals.collect_f10(
        ["000001", "600519"],
        date(2026, 7, 31),
        client_factory=lambda: (Client(), "fixture:7709"),
    )

    assert [row["security_id"] for row in rows] == ["000001", "600519"]
    assert {row["status"] for row in receipts} == {"success_nonempty"}
    assert {row["response_rows"] for row in receipts} == {1}
    assert metadata["server"] == "fixture:7709"


def test_statement_bootstrap_is_checkpointed_and_derives_nonempty_quarterly(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    class Response:
        status_code = 200

        def __init__(self, paper_code: str, source: str) -> None:
            self._body = {
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "report_list": {
                            "2026-06-30": {
                                "publish_date": "2026-07-20",
                                "update_time": "2026-07-20 12:00:00",
                                "data": [
                                    {"item_field": "BIZINCO", "item_value": "100"},
                                    {"item_field": "NETPROFIT", "item_value": "10"},
                                ],
                            }
                        }
                    },
                }
            }
            self.content = json.dumps(self._body).encode()
            calls.append((paper_code, source))

        def json(self):
            return self._body

    def get(_url: str, *, params: dict[str, str], **_kwargs: object) -> Response:
        return Response(params["paperCode"], params["source"])

    kwargs = {
        "output": tmp_path / "financial_statements.parquet",
        "receipts_path": tmp_path / "financial_statements.receipts.parquet",
        "quarterly_output": tmp_path / "financial_quarterly.parquet",
        "checkpoint_dir": tmp_path / "checkpoints",
        "periods": 100,
        "workers": 2,
        "batch_size": 1,
        "get": get,
    }
    first = fundamentals.collect_statements_checkpointed(
        ["000001", "600519"], date(2026, 7, 31), **kwargs
    )
    second = fundamentals.collect_statements_checkpointed(
        ["000001", "600519"], date(2026, 7, 31), **kwargs
    )

    assert first["statement_rows"] == 6
    assert first["receipt_rows"] == 6
    assert first["quarterly_rows"] == 2
    assert first["failed_receipts"] == 0
    assert second["reused_batches"] == 2
    assert len(calls) == 6
    quarterly = pq.read_table(kwargs["quarterly_output"]).to_pylist()
    assert {row["revenue"] for row in quarterly} == {"100"}


def test_statement_request_retries_once_before_success() -> None:
    attempts = 0

    class Response:
        status_code = 200
        content = b'{"result":{"status":{"code":0},"data":{"report_list":{}}}}'

        @staticmethod
        def json():
            return json.loads(Response.content)

    def get(*_args: object, **_kwargs: object) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("fixture timeout")
        return Response()

    _rows, receipts, _metadata = fundamentals.collect_statements(
        ["000001"], date(2026, 7, 31), periods=8, workers=1, get=get
    )

    retried = next(row for row in receipts if '"source": "fzb"' in row["request_json"])
    assert retried["status"] == "justified_empty"
    assert retried["attempts"] == 2


def test_industry_missing_membership_is_an_explicit_failure_receipt() -> None:
    class Result:
        error_code = "0"
        error_msg = "ok"

        def __init__(self) -> None:
            self._rows = iter([["2026-07-31", "sz.000001", "Ping An", "J66Bank", "sw"]])
            self.current: list[str] | None = None

        def next(self) -> bool:
            self.current = next(self._rows, None)
            return self.current is not None

        def get_row_data(self) -> list[str]:
            assert self.current is not None
            return self.current

    class BaoStock:
        @staticmethod
        def login():
            return type("Login", (), {"error_code": "0", "error_msg": "ok"})()

        @staticmethod
        def query_stock_industry():
            return Result()

        @staticmethod
        def logout() -> None:
            return None

    rows, receipts, _metadata = fundamentals.collect_industry(
        ["000001", "600519"], date(2026, 7, 31), bs_module=BaoStock
    )

    assert [row["security_id"] for row in rows] == ["000001"]
    failed = next(row for row in receipts if row["security_id"] == "600519")
    assert failed["status"] == "failed"
    assert failed["reason_code"] == "missing_industry_membership"


def test_after_hours_collector_preserves_sparse_matches() -> None:
    class Client:
        @staticmethod
        def transactions(**_kwargs: object):
            return [
                {"time": "15:29", "price": 10.0, "vol": 2, "buyorsell": 5},
                {"time": "15:04", "price": 9.9, "vol": 1, "buyorsell": 1},
            ]

    rows, metadata = after_hours_tdx.collect_symbol(
        Client(),
        code="000001",
        security_type="equity",
        market_date=date(2026, 7, 31),
    )

    assert len(rows) == 1
    assert rows[0]["quantity_units"] == 200.0
    assert rows[0]["buy_or_sell_code"] == 5
    assert metadata["provider_rows"] == 2


def test_after_hours_etf_universe_retries_transient_login_failure() -> None:
    class Result:
        error_code = "0"
        error_msg = "ok"

        def __init__(self) -> None:
            self._rows = iter(
                [
                    ["sh.510300", "ETF", "", "", "5", "1"],
                    ["sh.600000", "Equity", "", "", "1", "1"],
                ]
            )
            self.current: list[str] | None = None

        def next(self) -> bool:
            self.current = next(self._rows, None)
            return self.current is not None

        def get_row_data(self) -> list[str]:
            assert self.current is not None
            return self.current

    class BaoStock:
        login_calls = 0
        logout_calls = 0

        @classmethod
        def login(cls):
            cls.login_calls += 1
            code = "1" if cls.login_calls == 1 else "0"
            return type(
                "Login",
                (),
                {"error_code": code, "error_msg": "temporary network failure"},
            )()

        @staticmethod
        def query_stock_basic() -> Result:
            return Result()

        @classmethod
        def logout(cls) -> None:
            cls.logout_calls += 1

    sleeps: list[float] = []
    codes = after_hours_tdx._etf_universe(
        BaoStock,
        max_attempts=3,
        retry_delay_seconds=0.25,
        sleep=sleeps.append,
    )

    assert codes == ["510300"]
    assert BaoStock.login_calls == 2
    assert BaoStock.logout_calls == 2
    assert sleeps == [0.25]


def test_minute_collector_defaults_to_repository_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "data"
    home.mkdir()
    paths = _paths(home, tmp_path)
    observed: list[str] = []
    observed_timeout: list[int] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.extend(command)
        observed_timeout.append(int(_kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.delenv("HERMES_ODS_MINUTE_COLLECTOR", raising=False)
    monkeypatch.delenv("HERMES_ODS_MINUTE_TIMEOUT_SEC", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = lane_runner._run_minute_collector(
        paths=paths,
        market_date=date(2026, 7, 24),
        bar_size="5m",
        symbols=["000001"],
        raw_home=tmp_path / "attempt" / "minute_5m",
    )

    assert observed[1:3] == ["-m", "quanthus.providers.minute_tdx"]
    assert result["provider"] == "quanthus.providers.minute_tdx"
    assert observed_timeout == [1800]
    assert result["timeout_seconds"] == 1800


@pytest.mark.parametrize(
    ("configured", "message"),
    [
        ("not-a-number", "must be an integer"),
        ("59", "must be at least 60"),
        ("14401", "exceeds the 4-hour service timeout"),
    ],
)
def test_minute_collector_rejects_invalid_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str, message: str
) -> None:
    home = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("HERMES_ODS_MINUTE_TIMEOUT_SEC", configured)

    with pytest.raises(lane_runner.LaneRunError, match=message):
        lane_runner._run_minute_collector(
            paths=_paths(home, tmp_path),
            market_date=date(2026, 7, 24),
            bar_size="5m",
            symbols=["000001"],
            raw_home=tmp_path / "attempt" / "minute_5m",
        )


def test_minute_collector_override_must_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("HERMES_ODS_MINUTE_COLLECTOR", str(tmp_path / "missing.py"))
    with pytest.raises(lane_runner.LaneRunError, match="override missing"):
        lane_runner._run_minute_collector(
            paths=_paths(home, tmp_path),
            market_date=date(2026, 7, 24),
            bar_size="5m",
            symbols=["000001"],
            raw_home=tmp_path / "attempt" / "minute_5m",
        )
