"""Tests for repository-owned ODS provider entrypoints."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines import lane_runner
from quanthus.providers import (
    after_hours_tdx,
    common,
    fundamentals,
    minute_tdx,
    ods_snapshot,
    trading_calendar,
    valuation_history,
)


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


def test_checkpoint_reuse_binds_metadata_revision_and_part_identity(tmp_path: Path) -> None:
    schema = pa.schema([("value", pa.int64())])
    part = tmp_path / "part.parquet"
    common.write_parquet([{"value": 1}], part, schema)
    parts = {"data": (part, schema)}
    expected = {"provider_revision": "provider-v1"}
    saved: dict[str, object] = {
        **expected,
        "failed_receipts": 0,
        "parts": common.checkpoint_part_identities(parts),
    }

    assert common.checkpoint_parts_reusable(saved, expected, parts)
    for malformed in (None, "0", 0.0, False):
        assert not common.checkpoint_parts_reusable(
            {**saved, "failed_receipts": malformed}, expected, parts
        )
    assert not common.checkpoint_parts_reusable(
        saved, {"provider_revision": "provider-v2"}, parts
    )

    common.write_parquet([{"value": 2}], part, schema)
    assert not common.checkpoint_parts_reusable(saved, expected, parts)


def test_statement_bootstrap_is_checkpointed_and_derives_nonempty_quarterly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    (kwargs["checkpoint_dir"] / "part-00000.statements.parquet").write_bytes(b"tampered")
    third = fundamentals.collect_statements_checkpointed(
        ["000001", "600519"], date(2026, 7, 31), **kwargs
    )
    assert third["reused_batches"] == 1
    assert len(calls) == 9
    monkeypatch.setattr(
        fundamentals, "PROVIDER_REVISION", "quanthus-fundamentals-provider-v2-test"
    )
    fourth = fundamentals.collect_statements_checkpointed(
        ["000001", "600519"], date(2026, 7, 31), **kwargs
    )
    assert fourth["reused_batches"] == 0
    assert len(calls) == 15
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


def test_after_hours_eastmoney_fallback_expands_window_and_binds_date() -> None:
    calls: list[tuple[str, str]] = []

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def opener(request, *, timeout: float):
        assert timeout == 12.0
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/trends2/get"):
            return Response(
                {
                    "rc": 0,
                    "data": {
                        "code": "000001",
                        "trends": [
                            "2026-07-31 09:30,10,10,10,10,1,100,10",
                            "2026-07-31 15:00,10,10,10,10,1,100,10",
                        ],
                    },
                }
            )
        limit = query["pos"][0]
        calls.append((query["secid"][0], limit))
        details = (
            [
                "15:10:00,10.00,2,1,1",
                "15:29:00,10.00,3,1,2",
            ]
            if limit == "-100"
            else [
                "15:04:59,9.99,1,1,1",
                "15:10:00,10.00,2,1,1",
                "15:29:00,10.00,3,1,2",
            ]
        )
        return Response({"rc": 0, "data": {"code": "300750", "details": details}})

    def failed_tdx():
        raise RuntimeError("no TDX endpoint returned a valid bars probe")

    rows, receipts, metadata = after_hours_tdx.collect_after_hours(
        ["300750"],
        [],
        date(2026, 7, 31),
        client_factory=failed_tdx,
        eastmoney_opener=opener,
    )

    assert calls == [("0.300750", "-100"), ("0.300750", "-200")]
    assert metadata["provider"] == "eastmoney"
    assert metadata["server"] == "push2delay.eastmoney.com"
    assert len(rows) == 2
    assert rows[0]["source"] == "eastmoney.push2delay.details"
    assert rows[0]["quantity_units"] == 200.0
    assert rows[1]["amount"] == 3000.0
    assert receipts[0]["status"] == "success_nonempty"
    assert receipts[0]["provider_requested_limit"] == 200
    assert receipts[0]["provider_provider_rows"] == 3


def test_after_hours_zero_activity_etf_uses_same_day_quote_evidence() -> None:
    class JsonResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def eastmoney_opener(request, *, timeout: float):
        assert timeout == 12.0
        parsed = urlparse(request.full_url)
        if parsed.path.endswith("/trends2/get"):
            return JsonResponse(
                {
                    "rc": 0,
                    "data": {
                        "code": "000001",
                        "trends": ["2026-07-31 15:00,10,10,10,10,1,100,10"],
                    },
                }
            )
        return JsonResponse({"rc": 100, "data": None})

    class QuoteResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            fields = [""] * 31
            fields[2] = "560000"
            fields[6] = "0"
            fields[30] = "20260731160000"
            return f'v_sh560000="{"~".join(fields)}";'.encode("gb18030")

    def failed_tdx():
        raise RuntimeError("no TDX endpoint returned a valid bars probe")

    rows, receipts, metadata = after_hours_tdx.collect_after_hours(
        [],
        ["560000"],
        date(2026, 7, 31),
        client_factory=failed_tdx,
        eastmoney_opener=eastmoney_opener,
        quote_opener=lambda *_args, **_kwargs: QuoteResponse(),
    )

    assert rows == []
    assert metadata["provider"] == "eastmoney"
    assert receipts[0]["status"] == "justified_empty"
    assert receipts[0]["provider"] == "tencent:qt.gtimg.cn"
    assert receipts[0]["reason_code"] == "same_day_zero_volume_proves_no_after_hours_matches"
    assert receipts[0]["provider_daily_volume"] == 0.0
    assert receipts[0]["provider_quote_market_date"] == "2026-07-31"


def test_after_hours_zero_activity_quote_rejects_nonzero_volume() -> None:
    class QuoteResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            fields = [""] * 31
            fields[2] = "159915"
            fields[6] = "10980443"
            fields[30] = "20260731161403"
            return f'v_sz159915="{"~".join(fields)}";'.encode("gb18030")

    with pytest.raises(RuntimeError, match="nonzero total volume"):
        after_hours_tdx._tencent_zero_activity_evidence(
            "159915",
            date(2026, 7, 31),
            opener=lambda *_args, **_kwargs: QuoteResponse(),
        )


def test_after_hours_eastmoney_fallback_rejects_wrong_market_date() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "rc": 0,
                    "data": {
                        "code": "000001",
                        "trends": ["2026-08-03 15:00,10,10,10,10,1,100,10"],
                    },
                }
            ).encode("utf-8")

    def failed_tdx():
        raise RuntimeError("no TDX endpoint returned a valid bars probe")

    with pytest.raises(RuntimeError, match="does not match requested 2026-07-31"):
        after_hours_tdx.collect_after_hours(
            ["300750"],
            [],
            date(2026, 7, 31),
            client_factory=failed_tdx,
            eastmoney_opener=lambda *_args, **_kwargs: Response(),
        )


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


def test_valuation_history_is_checkpointed_with_per_security_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class Result:
        error_code = "0"
        error_msg = "ok"

        def __init__(self, code: str) -> None:
            self._rows = iter(
                [
                    [
                        "2026-07-20",
                        code,
                        "10.0",
                        "1",
                        "12.5",
                        "1.4",
                        "2.1",
                        "9.0",
                    ]
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
        @staticmethod
        def login():
            return type("Login", (), {"error_code": "0", "error_msg": "ok"})()

        @staticmethod
        def logout() -> None:
            return None

        @staticmethod
        def query_history_k_data_plus(code: str, *_args: object, **_kwargs: object):
            calls.append(code)
            return Result(code)

    collector_kwargs = {
        "bs_module": BaoStock,
        "sleep": lambda _seconds: None,
        "configure_timeout": lambda _seconds: True,
    }
    kwargs = {
        "output": tmp_path / "valuation.parquet",
        "receipts_path": tmp_path / "valuation.receipts.parquet",
        "checkpoint_dir": tmp_path / "checkpoints",
        "batch_size": 1,
        "collector_kwargs": collector_kwargs,
    }
    first = valuation_history.collect_valuation_history_checkpointed(
        ["000001", "600519"],
        date(2026, 7, 20),
        date(2026, 7, 30),
        **kwargs,
    )
    second = valuation_history.collect_valuation_history_checkpointed(
        ["000001", "600519"],
        date(2026, 7, 20),
        date(2026, 7, 30),
        **kwargs,
    )

    assert first["row_count"] == 2
    assert first["receipt_count"] == 2
    assert first["failed_receipts"] == 0
    assert second["reused_batches"] == 2
    assert calls == ["sz.000001", "sh.600519"]
    (kwargs["checkpoint_dir"] / "part-00000.valuation.parquet").write_bytes(b"tampered")
    third = valuation_history.collect_valuation_history_checkpointed(
        ["000001", "600519"],
        date(2026, 7, 20),
        date(2026, 7, 30),
        **kwargs,
    )
    assert third["reused_batches"] == 1
    assert calls == ["sz.000001", "sh.600519", "sz.000001"]
    monkeypatch.setattr(
        valuation_history, "PROVIDER_REVISION", "baostock-valuation-history-v2-test"
    )
    fourth = valuation_history.collect_valuation_history_checkpointed(
        ["000001", "600519"],
        date(2026, 7, 20),
        date(2026, 7, 30),
        **kwargs,
    )
    assert fourth["reused_batches"] == 0
    assert calls[-2:] == ["sz.000001", "sh.600519"]
    rows = pq.read_table(kwargs["output"]).to_pylist()
    assert {row["pe_ttm"] for row in rows} == {12.5}
    receipts = pq.read_table(kwargs["receipts_path"]).to_pylist()
    assert {row["status"] for row in receipts} == {"success_nonempty"}


def test_f10_checkpoint_invalidates_tampering_and_provider_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory_calls = 0

    class Client:
        client = type("Transport", (), {"disconnect": staticmethod(lambda: None)})()

        @staticmethod
        def F10C(*, symbol: str):
            return [{"name": "latest", "filename": f"{symbol}.txt", "start": 0, "length": 1}]

        @staticmethod
        def F10(*, symbol: str, name: str):
            return f"{symbol} {name} profile"

    def client_factory():
        nonlocal factory_calls
        factory_calls += 1
        return Client(), "fixture:7709"

    kwargs = {
        "output": tmp_path / "f10.parquet",
        "receipts_path": tmp_path / "f10.receipts.parquet",
        "checkpoint_dir": tmp_path / "checkpoints",
        "batch_size": 1,
        "client_factory": client_factory,
    }
    codes = ["000001", "600519"]
    first = fundamentals.collect_f10_checkpointed(codes, date(2026, 7, 31), **kwargs)
    second = fundamentals.collect_f10_checkpointed(codes, date(2026, 7, 31), **kwargs)
    assert first["row_count"] == 2
    assert second["reused_batches"] == 2
    assert factory_calls == 2

    (kwargs["checkpoint_dir"] / "part-00000.f10.parquet").write_bytes(b"tampered")
    third = fundamentals.collect_f10_checkpointed(codes, date(2026, 7, 31), **kwargs)
    assert third["reused_batches"] == 1
    assert factory_calls == 3

    monkeypatch.setattr(
        fundamentals, "PROVIDER_REVISION", "quanthus-fundamentals-provider-v2-test"
    )
    fourth = fundamentals.collect_f10_checkpointed(codes, date(2026, 7, 31), **kwargs)
    assert fourth["reused_batches"] == 0
    assert factory_calls == 5


def test_checkpoint_receipt_identity_multiset_rejects_substitution(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipts.parquet"
    common.write_parquet(
        [
            {"dataset": "raw_f10", "security_id": "000001", "request_json": "request-a"},
            {"dataset": "raw_f10", "security_id": "000001", "request_json": "request-a"},
        ],
        receipt_path,
        common.receipt_schema(),
    )
    with pytest.raises(ValueError, match="receipt identities"):
        common.verify_receipt_identities(
            receipt_path,
            [
                ["raw_f10", "000001", "request-a"],
                ["raw_f10", "600519", "request-b"],
            ],
        )


def test_eastmoney_minute_fallback_preserves_exact_amount_and_aggregates_5m(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "code": "000001",
        "trends": [
            "2026-09-09 09:30,11.76,11.76,11.76,11.76,1860,2187360.00,11.760",
            "2026-09-09 09:31,11.75,11.76,11.77,11.74,6856,8055706.00,11.752",
            "2026-09-09 09:32,11.76,11.75,11.76,11.73,4848,5693941.00,11.750",
            "2026-09-09 09:33,11.74,11.76,11.76,11.73,8724,10245170.00,11.747",
            "2026-09-09 09:34,11.76,11.74,11.76,11.73,5087,5974879.00,11.747",
            "2026-09-09 09:35,11.73,11.73,11.74,11.73,3584,4205736.00,11.746",
        ],
    }
    monkeypatch.setattr(minute_tdx, "_eastmoney_payload", lambda _symbol: payload)

    one, one_meta = minute_tdx._eastmoney_bars("000001", "1m")
    assert one_meta["provider"] == "eastmoney-push2delay-trends2"
    assert one["ts"].dt.strftime("%H:%M").tolist() == ["09:31", "09:32", "09:33", "09:34", "09:35"]
    assert one["volume"].sum() == pytest.approx((6856 + 4848 + 8724 + 5087 + 3584) * 100)
    assert one["amount"].sum() == pytest.approx(8055706 + 5693941 + 10245170 + 5974879 + 4205736)

    five, five_meta = minute_tdx._eastmoney_bars("000001", "5m")
    assert five_meta["provider"] == "eastmoney-push2delay-trends2"
    assert five["ts"].dt.strftime("%H:%M").tolist() == ["09:35"]
    row = five.iloc[0]
    assert row["open"] == pytest.approx(11.76)
    assert row["close"] == pytest.approx(11.73)
    assert row["high"] == pytest.approx(11.77)
    assert row["low"] == pytest.approx(11.73)
    assert row["volume"] == pytest.approx((1860 + 6856 + 4848 + 8724 + 5087 + 3584) * 100)
    assert row["amount"] == pytest.approx(
        2187360 + 8055706 + 5693941 + 10245170 + 5974879 + 4205736
    )


def test_eastmoney_minute_fallback_requests_and_filters_target_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "code": "000001",
        "trends": [
            "2026-09-20 15:00,10,10,10,10,1,10,10",
            "2026-09-21 15:00,11,11,11,11,2,22,11",
            "2026-09-22 15:00,12,12,12,12,3,36,12",
        ],
    }
    observed: dict[str, object] = {}

    def fake_payload(symbol: str, **kwargs: object) -> dict[str, object]:
        observed["symbol"] = symbol
        observed.update(kwargs)
        return payload

    monkeypatch.setattr(minute_tdx, "_eastmoney_payload", fake_payload)
    monkeypatch.setattr(minute_tdx, "_eastmoney_ndays", lambda _date: 2)

    frame, _meta = minute_tdx._eastmoney_bars(
        "000001", "1m", market_date="2026-09-21", session=object()
    )

    assert observed["symbol"] == "000001"
    assert observed["ndays"] == 2
    assert frame["ts"].dt.strftime("%Y-%m-%d").tolist() == ["2026-09-21"]


def test_eastmoney_payload_treats_null_data_as_no_data() -> None:
    class Response:
        def read(self) -> bytes:
            return b'{"rc": 0, "data": null}'

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    payload = minute_tdx._eastmoney_payload(
        "000001", opener=lambda *_args, **_kwargs: Response()
    )
    assert payload == {"code": "000001", "trends": [], "no_data": True}


def test_minute_collect_uses_eastmoney_only_for_incremental_tdx_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pandas as pd

    monkeypatch.setattr(
        minute_tdx,
        "tdx_client",
        lambda: (_ for _ in ()).throw(RuntimeError("TDX unavailable")),
    )
    frame = pd.DataFrame(
        [
            {
                "stock_code": "000001",
                "ts": pd.Timestamp("2026-09-09 15:00:00"),
                "open": 11.72,
                "high": 11.72,
                "low": 11.70,
                "close": 11.70,
                "volume": 517700.0,
                "amount": 6057558.0,
            }
        ]
    )
    monkeypatch.setattr(
        minute_tdx,
        "_eastmoney_bars",
        lambda _symbol, _frequency, **_kwargs: (
            frame,
            {"ok": True, "provider": "eastmoney-push2delay-trends2", "rows": 1},
        ),
    )

    output = tmp_path / "minute"
    assert (
        minute_tdx.collect(
            home=output,
            layer="research",
            frequency="1m",
            mode="incr",
            symbols=["000001"],
            max_pages=1,
            sleep_seconds=0,
        )
        == 0
    )
    status = json.loads((output / "status.json").read_text())
    assert status["server"] == "eastmoney-push2delay-trends2"
    assert pq.read_table(output / "000001_1m.parquet").num_rows == 1

    with pytest.raises(RuntimeError, match="TDX unavailable"):
        minute_tdx.collect(
            home=tmp_path / "backfill",
            layer="research",
            frequency="1m",
            mode="backfill",
            symbols=["000001"],
            max_pages=1,
            sleep_seconds=0,
        )


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
    assert observed[observed.index("--market-date") + 1] == "2026-07-24"
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
