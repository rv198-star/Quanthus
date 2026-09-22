"""Qualify exact-date native 5m adapter and collector routing."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quanthus.providers import minute_tdx


class _Response:
    error_code = "0"
    error_msg = "success"

    def __init__(self, rows):
        self.rows = iter(rows)

    def next(self):
        self.current = next(self.rows, None)
        return self.current is not None

    def get_row_data(self):
        return self.current


def _row(day="2026-09-22", code="sz.000001"):
    return [
        day,
        "20260922093500000",
        code,
        "11.70",
        "11.71",
        "11.62",
        "11.64",
        "6923200",
        "80704408",
    ]


def test_native_five_minute_parser_preserves_ohlcv_and_lineage():
    client = SimpleNamespace(query_history_k_data_plus=Mock(return_value=_Response([_row()])))
    frame, meta = minute_tdx._baostock_native_5m_bars("000001", "2026-09-22", client=client)
    assert len(frame) == 1
    assert frame.iloc[0]["volume"] == 6923200
    assert frame.iloc[0]["ts"].strftime("%Y-%m-%d %H:%M") == "2026-09-22 09:35"
    assert (meta["provider"], meta["native_frequency"], meta["derived_from"]) == (
        "baostock-native-5m",
        "5m",
        None,
    )
    assert client.query_history_k_data_plus.call_args.kwargs == {
        "start_date": "2026-09-22",
        "end_date": "2026-09-22",
        "frequency": "5",
        "adjustflag": "3",
    }


@pytest.mark.parametrize("bad_row", [_row(day="2026-09-21"), _row(code="sh.000001")])
def test_wrong_date_or_code_fails_closed(bad_row):
    client = SimpleNamespace(query_history_k_data_plus=lambda *a, **k: _Response([bad_row]))
    with pytest.raises(RuntimeError, match="wrong date/code/schema"):
        minute_tdx._baostock_native_5m_bars("000001", "2026-09-22", client=client)


def test_zero_rows_preserved_for_separate_suspension_evidence():
    client = SimpleNamespace(query_history_k_data_plus=lambda *a, **k: _Response([]))
    frame, meta = minute_tdx._baostock_native_5m_bars("002860", "2026-09-22", client=client)
    assert frame.empty
    assert meta["rows"] == 0
    assert meta["source_market_date"] is None


def test_duplicate_native_timestamp_fails_closed():
    client = SimpleNamespace(query_history_k_data_plus=lambda *a, **k: _Response([_row(), _row()]))
    with pytest.raises(RuntimeError, match="duplicate bar timestamps"):
        minute_tdx._baostock_native_5m_bars("000001", "2026-09-22", client=client)


def test_native_five_minute_circuit_keeps_terminal_receipts(tmp_path, monkeypatch):
    client = SimpleNamespace(
        login=Mock(return_value=SimpleNamespace(error_code="0", error_msg="success")),
        logout=Mock(),
        query_history_k_data_plus=Mock(side_effect=RuntimeError("provider unavailable")),
    )
    monkeypatch.setitem(sys.modules, "baostock", client)
    monkeypatch.setattr(minute_tdx, "set_socket_timeout", lambda seconds: True)
    with pytest.raises(RuntimeError, match="circuit opened"):
        minute_tdx.collect(
            home=tmp_path,
            layer="prod",
            frequency="5m",
            mode="incr",
            symbols=["000001", "000002", "000003"],
            max_pages=1,
            sleep_seconds=0,
            market_date="2026-09-22",
        )
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert list(checkpoint["symbols"]) == ["000001", "000002", "000003"]
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "failed"
    client.logout.assert_called_once()


@pytest.mark.parametrize("mode", ["incr", "backfill"])
def test_collector_routes_formal_five_minute_to_native_provider(tmp_path, monkeypatch, mode):
    client = SimpleNamespace(
        login=Mock(return_value=SimpleNamespace(error_code="0", error_msg="success")),
        logout=Mock(),
        query_history_k_data_plus=lambda *a, **k: _Response([_row()]),
    )
    monkeypatch.setitem(sys.modules, "baostock", client)
    monkeypatch.setattr(minute_tdx, "set_socket_timeout", lambda seconds: True)
    monkeypatch.setattr(minute_tdx, "tdx_client", Mock(side_effect=AssertionError("TDX called")))
    monkeypatch.setattr(
        minute_tdx,
        "_merge_write",
        lambda home, symbol, frequency, frame: (len(frame), Path(home) / "000001_5m.parquet"),
    )
    assert (
        minute_tdx.collect(
            home=tmp_path,
            layer="prod",
            frequency="5m",
            mode=mode,
            symbols=["000001"],
            max_pages=1,
            sleep_seconds=0,
            market_date="2026-09-22",
        )
        == 0
    )
    receipt = json.loads((tmp_path / "checkpoint.json").read_text())["symbols"]["000001"]
    assert receipt["provider"] == "baostock-native-5m"
    assert receipt["native_frequency"] == "5m"
    client.logout.assert_called_once()
