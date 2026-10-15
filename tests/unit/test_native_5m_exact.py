"""Synthetic exact-parser, serialization and wire-failure contracts."""

import hashlib
import json
import zlib
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from quanthus.providers.native_5m_exact import (
    FIELDS,
    SHANGHAI,
    TRAILER,
    ExactBar,
    Native5mError,
    NativeDay,
    WireCapture,
    captured_socket,
    fetch_captured_day,
    fetch_day,
    parse_response,
    parse_wire_response,
    write_parquet,
)


class Response:
    error_code = "0"
    fields = FIELDS
    code = "sz.000001"
    start_date = end_date = "2020-01-02"
    frequency = "5"
    adjustflag = "3"
    cur_page_num = "1"

    def __init__(self, rows, final_error=False):
        self.rows = iter(rows)
        self.final_error = final_error

    def next(self):
        self.current = next(self.rows, None)
        if self.current is None and self.final_error:
            self.error_code = "failure"
        return self.current is not None

    def get_row_data(self):
        return self.current


def row():
    return [
        "2020-01-02",
        "20200102093500123",
        "sz.000001",
        "1.12345678901234567890",
        "2.00000000000",
        "0.000",
        "1.10",
        "0",
        "1.234567890123456789E+20",
    ]


def test_exact_values_original_scale_zero_time_and_parquet_roundtrip(tmp_path):
    bars = parse_response(Response([row()]), NativeDay("sz.000001", "2020-01-02"))
    assert bars[0].values[0] == Decimal("1.12345678901234567890")
    assert bars[0].lexical_values == tuple(row()[3:])
    assert bars[0].timestamp == datetime(2020, 1, 2, 9, 35, 0, 123000, SHANGHAI)
    path = tmp_path / "exact.parquet"
    write_parquet(path, bars)
    restored = pq.ParquetFile(path).read().to_pylist()[0]
    assert restored["timestamp"] == bars[0].timestamp
    assert restored["source_values"] == list(row()[3:])
    assert tuple(restored[f] for f in FIELDS[3:]) == bars[0].values
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_parquet(path, ())
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "column,value,reason",
    [
        (0, "2020-01-03", "ROW_DATE_CODE_SCHEMA"),
        (2, "sh.000001", "ROW_DATE_CODE_SCHEMA"),
        (1, "20200103093500000", "TIMESTAMP_DATE"),
        (1, "202001020935", "INVALID_SOURCE"),
        (3, "NaN", "NONFINITE"),
        (3, "Infinity", "NONFINITE"),
        (3, "", "INVALID_DECIMAL"),
        (3, 1.1, "ROW_DATE_CODE_SCHEMA"),
    ],
)
def test_bad_row_fails_closed(column, value, reason):
    raw = row()
    raw[column] = value
    with pytest.raises(Native5mError, match=reason):
        parse_response(Response([raw]), NativeDay("sz.000001", "2020-01-02"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("adjustflag", "2"),
        ("frequency", "1"),
        ("code", "sh.000001"),
        ("fields", FIELDS[:-1]),
        ("start_date", "2020-01-01"),
    ],
)
def test_wrong_response_contract_rejected_even_for_empty(key, value):
    response = Response([])
    setattr(response, key, value)
    with pytest.raises(Native5mError, match="RESPONSE_CONTRACT"):
        parse_response(response, NativeDay("sz.000001", "2020-01-02"))


def test_duplicate_and_final_page_error_rejected():
    request = NativeDay("sz.000001", "2020-01-02")
    with pytest.raises(Native5mError, match="DUPLICATE"):
        parse_response(Response([row(), row()]), request)
    with pytest.raises(Native5mError, match="PROVIDER_ERROR"):
        parse_response(Response([row()], final_error=True), request)
    response = Response([row()])
    response.cur_page_num = "3"
    with pytest.raises(Native5mError, match="PAGE_LIMIT"):
        parse_response(response, request)
    assert parse_response(Response([]), request) == ()


def test_query_never_falls_back_or_aggregates():
    calls = []

    def query(*args, **kwargs):
        calls.append((args, kwargs))
        return Response([row()])

    fetch_day(
        SimpleNamespace(query_history_k_data_plus=query), NativeDay("sz.000001", "2020-01-02")
    )
    assert calls == [
        (
            ("sz.000001", ",".join(FIELDS)),
            dict(start_date="2020-01-02", end_date="2020-01-02", frequency="5", adjustflag="3"),
        )
    ]


class Socket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.requests = []

    def send(self, data):
        self.requests.append(data)
        return len(data)

    def recv(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
        return chunk[:size]


def test_actual_response_bytes_and_multiple_requests_preserved(tmp_path):
    raw = b"\x00compressed\xff" + TRAILER
    sock = Socket([raw[:4], raw[4:], raw])
    context = SimpleNamespace(default_socket=sock)
    with captured_socket(context, tmp_path, max_requests=2, max_bytes=1000, seconds=10) as wire:
        wire.send(b"request1")
        wire.recv(4)
        wire.recv(100)
        wire.send(b"request2")
        wire.recv(100)
    assert context.default_socket is sock
    assert (tmp_path / "response-0001.wire").read_bytes() == raw
    assert (tmp_path / "response-0002.wire").read_bytes() == raw
    receipt = json.loads((tmp_path / "wire-receipt.json").read_text())
    assert receipt["failure"] is None
    assert all(
        r["complete"] and r["response_sha256"] == hashlib.sha256(raw).hexdigest()
        for r in receipt["records"]
    )
    assert "request1" not in (tmp_path / "wire-receipt.json").read_text()


@pytest.mark.parametrize("failure", ["bytes", "eof", "request", "time", "truncated"])
def test_swallowed_sdk_errors_still_fail_and_restore_socket(tmp_path, failure):
    sock = Socket([b"somepartial"] if failure != "eof" else [])
    context = SimpleNamespace(default_socket=sock)
    with (
        pytest.raises(Native5mError),
        captured_socket(context, tmp_path, max_requests=1, max_bytes=3, seconds=10) as wire,
    ):
        wire.send(b"query")
        try:
            if failure == "bytes":
                wire.recv(10)
                wire.recv(10)
            elif failure == "eof":
                wire.recv(10)
            elif failure == "time":
                wire.deadline = 0
                wire.recv(10)
            elif failure == "request":
                wire._tail = TRAILER
                wire.send(b"continuation")
            else:
                wire.recv(2)
        except Native5mError:
            pass  # Match the vendor SDK swallowing socket exceptions.
    assert context.default_socket is sock
    assert json.loads((tmp_path / "wire-receipt.json").read_text())["failure"]


def test_capture_rejects_nonempty_root_before_send(tmp_path):
    (tmp_path / "keep").write_bytes(b"untouched")
    sock = Socket([])
    with pytest.raises(Native5mError, match="EMPTY_DIRECTORY"):
        WireCapture(sock, tmp_path, max_requests=1, max_bytes=10, seconds=10)
    assert not sock.requests
    assert (tmp_path / "keep").read_bytes() == b"untouched"


def test_multiple_lexical_scales_survive_and_excess_precision_rejected(tmp_path):
    first = ExactBar(
        "sz.000001",
        datetime(2020, 1, 2, 9, 35, tzinfo=SHANGHAI),
        ("0.00", "2.3", "0.0000", "1E+3", "0", "1.23456789012"),
    )
    second = ExactBar(first.code, first.timestamp.replace(minute=40), ("0.12345",) * 6)
    write_parquet(tmp_path / "mixed.parquet", (first, second))
    restored = pq.ParquetFile(tmp_path / "mixed.parquet").read().to_pylist()
    for actual, expected in zip(restored, (first, second), strict=True):
        assert actual["source_values"] == list(expected.lexical_values)
        assert tuple(actual[n] for n in FIELDS[3:]) == expected.values
    wide = ExactBar(first.code, first.timestamp, ("1E+77",) * 6)
    with pytest.raises(Native5mError, match="STORAGE_RANGE"):
        write_parquet(tmp_path / "wide.parquet", (wide,))
    assert not (tmp_path / "wide.parquet").exists()


def wire_message(*, newline=False, adjustflag="3", page_size=2000, version="00.9.10", rows=None):
    body = "\x01".join(
        [
            "0",
            "success",
            "query_history_k_data_plus",
            "anonymous",
            "1",
            str(page_size),
            json.dumps({"record": [row()] if rows is None else rows}),
            "sz.000001",
            ",".join(FIELDS),
            "2020-01-02",
            "2020-01-02",
            "5",
            adjustflag,
        ]
    ) + ("\n" if newline else "")
    compressed = zlib.compress(body.encode())
    header = f"{version}\x0196\x01{len(compressed):010d}".encode()
    return header + compressed + b"\x01123456789\n" + TRAILER


@pytest.mark.parametrize("newline", [True, False])
def test_original_wire_proves_contract_with_or_without_body_newline(newline):
    parsed = parse_wire_response(
        wire_message(newline=newline), NativeDay("sz.000001", "2020-01-02")
    )
    assert parsed[0].lexical_values == tuple(row()[3:])


@pytest.mark.parametrize("records", [[None, row()], [row(), None], [None]])
def test_null_wire_record_cannot_impersonate_end_of_response(records):
    with pytest.raises(Native5mError, match="WIRE_RECORD_SCHEMA"):
        parse_wire_response(wire_message(rows=records), NativeDay("sz.000001", "2020-01-02"))


def test_real_sdk_unconditionally_slices_basis_but_raw_proof_remains(monkeypatch):
    import baostock.common.context as context
    import baostock.security.history as history

    raw = wire_message()
    plain = raw[:21].decode() + zlib.decompress(raw[21:]).decode()
    monkeypatch.setattr(history.sock, "send_msg", lambda _: plain)
    monkeypatch.setattr(context, "user_id", "anonymous", raising=False)
    result = history.query_history_k_data_plus(
        "sz.000001",
        ",".join(FIELDS),
        start_date="2020-01-02",
        end_date="2020-01-02",
        frequency="5",
        adjustflag="3",
    )
    assert result.adjustflag == ""
    with pytest.raises(Native5mError, match="RESPONSE_CONTRACT"):
        parse_response(result, NativeDay("sz.000001", "2020-01-02"))
    assert len(parse_wire_response(raw, NativeDay("sz.000001", "2020-01-02"))) == 1


@pytest.mark.parametrize(
    "raw,reason",
    [
        (wire_message(adjustflag="2"), "RESPONSE_CONTRACT"),
        (wire_message(page_size=1), "TERMINAL_PAGE_UNPROVEN"),
        (wire_message(version="00.9.99"), "HEADER_INVALID"),
        (wire_message()[:-1], "TRUNCATED"),
        (wire_message()[:30] + b"broken" + TRAILER, "LENGTH_MISMATCH"),
    ],
)
def test_wire_basis_page_version_and_damage_cannot_be_waived(raw, reason):
    with pytest.raises(Native5mError, match=reason):
        parse_wire_response(raw, NativeDay("sz.000001", "2020-01-02"))


def test_captured_query_consumes_fresh_original_frame(tmp_path):
    raw = wire_message()
    sock = Socket([raw])
    context = SimpleNamespace(default_socket=sock)

    def query(*args, **kwargs):
        context.default_socket.send(b"query exact original request")
        context.default_socket.recv(10000)
        return SimpleNamespace(adjustflag="")

    with captured_socket(context, tmp_path, max_requests=1, max_bytes=10000, seconds=10) as wire:
        bars = fetch_captured_day(
            SimpleNamespace(query_history_k_data_plus=query),
            NativeDay("sz.000001", "2020-01-02"),
            wire,
        )
    assert len(bars) == 1 and bars[0].lexical_values == tuple(row()[3:])
