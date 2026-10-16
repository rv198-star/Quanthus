"""Opt-in exact BaoStock five-minute observations; no legacy collector changes.

The parser preserves lexical decimals and Shanghai timestamps. Wire capture wraps
only an already authenticated process-local socket, never login credentials. Empty
responses are observations, not suspension or full-coverage evidence.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import time
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

FIELDS = ("date", "time", "code", "open", "high", "low", "close", "volume", "amount")
SHANGHAI = ZoneInfo("Asia/Shanghai")
TRAILER = b"<![CDATA[]]>\n"
CONTRACT = "p10-native-5m-exact-v1"
WIRE_SERVER_VERSION = "00.9.10"
MAX_WINDOW_PAGE_SIZE = 20000


class Native5mError(ValueError):
    """Stable reason code, without protected source values."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class NativeDay:
    code: str
    business_date: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"(?:sh|sz)\.[0-9]{6}", self.code):
            raise Native5mError("INVALID_SECURITY_CODE")
        if date.fromisoformat(self.business_date).isoformat() != self.business_date:
            raise Native5mError("INVALID_BUSINESS_DATE")


@dataclass(frozen=True)
class NativeWindow:
    """One security, at most 731 inclusive civil dates, never the whole universe."""

    code: str
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        NativeDay(self.code, self.start_date)
        NativeDay(self.code, self.end_date)
        days = (date.fromisoformat(self.end_date) - date.fromisoformat(self.start_date)).days
        if not 0 <= days <= 730:
            raise Native5mError("INVALID_WINDOW_RANGE")


@dataclass(frozen=True)
class ExactBar:
    code: str
    timestamp: datetime
    lexical_values: tuple[str, ...]

    @property
    def values(self) -> tuple[Decimal, ...]:
        return tuple(Decimal(value) for value in self.lexical_values)


def _check_response(response: Any, request: NativeDay) -> None:
    if str(response.error_code) != "0":
        raise Native5mError("PROVIDER_ERROR")
    if (
        tuple(response.fields) != FIELDS
        or response.code != request.code
        or response.start_date != request.business_date
        or response.end_date != request.business_date
        or str(response.frequency) != "5"
        or str(response.adjustflag) != "3"
    ):
        raise Native5mError("RESPONSE_CONTRACT_MISMATCH")


def parse_response(
    response: Any, request: NativeDay, *, max_rows: int = 1024, max_pages: int = 2
) -> tuple[ExactBar, ...]:
    """Validate all pages, including the final failed/empty pagination response.

    Page limits bound parsing; the wire wrapper separately bounds *actual* sends,
    including an attempted continuation that the vendor SDK might swallow.
    """
    if max_rows < 1 or max_pages < 1:
        raise Native5mError("INVALID_PARSE_LIMIT")
    _check_response(response, request)
    rows = []
    seen = set()
    pages = set()
    while response.next():
        _check_response(response, request)
        page = int(response.cur_page_num)
        pages.add(page)
        if page < 1 or len(pages) > max_pages or page > max_pages:
            raise Native5mError("PAGE_LIMIT")
        raw = response.get_row_data()
        if (
            len(raw) != len(FIELDS)
            or any(not isinstance(value, str) for value in raw)
            or raw[0] != request.business_date
            or raw[2] != request.code
        ):
            raise Native5mError("ROW_DATE_CODE_SCHEMA_MISMATCH")
        if not re.fullmatch(r"[0-9]{17}", raw[1]):
            raise Native5mError("INVALID_SOURCE_TIMESTAMP")
        stamp = datetime.strptime(raw[1], "%Y%m%d%H%M%S%f").replace(tzinfo=SHANGHAI)
        if stamp.date().isoformat() != request.business_date:
            raise Native5mError("TIMESTAMP_DATE_MISMATCH")
        if stamp in seen:
            raise Native5mError("DUPLICATE_BAR_KEY")
        try:
            numbers = tuple(Decimal(value) for value in raw[3:])
        except InvalidOperation as exc:
            raise Native5mError("INVALID_DECIMAL") from exc
        if any(not value.is_finite() for value in numbers):
            raise Native5mError("NONFINITE_DECIMAL")
        seen.add(stamp)
        rows.append(ExactBar(request.code, stamp, tuple(raw[3:])))
        if len(rows) > max_rows:
            raise Native5mError("ROW_LIMIT")
    _check_response(response, request)
    if int(response.cur_page_num) > max_pages:
        raise Native5mError("PAGE_LIMIT")
    return tuple(sorted(rows, key=lambda row: row.timestamp))


def fetch_day(client: Any, request: NativeDay) -> tuple[ExactBar, ...]:
    """One exact unadjusted supplier-native query, without fallback or aggregation."""
    response = client.query_history_k_data_plus(
        request.code,
        ",".join(FIELDS),
        start_date=request.business_date,
        end_date=request.business_date,
        frequency="5",
        adjustflag="3",
    )
    return parse_response(response, request)


def parse_wire_response(raw: bytes, request: NativeDay) -> tuple[ExactBar, ...]:
    """Validate the original envelope, without the SDK's unconditional ``[:-1]``.

    Some live compressed bodies have no final newline. BaoStock 0.9.4 drops the
    final adjustflag character in that case. An empty SDK echo is never accepted:
    the independent original envelope must prove the full request contract.
    This exact-day path requires a terminal short first page, never a page cap.
    """
    parts = _wire_parts(raw)
    try:
        record = json.loads(parts[6])["record"]
        if not isinstance(record, list) or int(parts[4]) != 1:
            raise Native5mError("WIRE_PAGE_INVALID")
        if any(not isinstance(row, list) for row in record):
            # A JSON null must not become the iterator's end sentinel and hide
            # valid records following it, or turn malformed data into empty data.
            raise Native5mError("WIRE_RECORD_SCHEMA_INVALID")
        if not len(record) < int(parts[5]):
            raise Native5mError("WIRE_TERMINAL_PAGE_UNPROVEN")
        response = SimpleNamespace(
            error_code=parts[0],
            cur_page_num=parts[4],
            code=parts[7],
            fields=parts[8].split(","),
            start_date=parts[9],
            end_date=parts[10],
            frequency=parts[11],
            adjustflag=parts[12],
        )
    except (ValueError, IndexError, KeyError, UnicodeDecodeError, zlib.error) as exc:
        if isinstance(exc, Native5mError):
            raise
        raise Native5mError("WIRE_ENVELOPE_INVALID") from exc
    rows = iter(record)

    def advance() -> bool:
        response.current = next(rows, None)
        return response.current is not None

    response.next = advance
    response.get_row_data = lambda: response.current
    return parse_response(response, request)


def _wire_parts(raw: bytes) -> list[str]:
    """Decode one bounded original frame without trusting the SDK object."""
    constants = importlib.import_module("baostock.common.contants")
    header_size = constants.MESSAGE_HEADER_LENGTH
    if not raw.endswith(TRAILER):
        raise Native5mError("WIRE_ENVELOPE_TRUNCATED")
    try:
        header = raw[:header_size].decode("utf-8").split(constants.MESSAGE_SPLIT)
        if (
            len(header) != 3
            or int(header[2]) < 1
            or header[0] != WIRE_SERVER_VERSION
            or header[1] != constants.MESSAGE_TYPE_GETKDATAPLUS_RESPONSE
        ):
            raise Native5mError("WIRE_HEADER_INVALID")
        length = int(header[2])
        # The native frame carries an opaque vendor checksum after the compressed
        # body. Preserve it; its algorithm is not independently qualified here.
        tail = raw[header_size + length : -len(TRAILER)]
        if not re.fullmatch(rb"\x01[0-9]{1,10}\n", tail):
            raise Native5mError("WIRE_LENGTH_MISMATCH")
        body = raw[header_size : header_size + length]
        if header[1] in constants.COMPRESSED_MESSAGE_TYPE_TUPLE:
            decoder = zlib.decompressobj()
            body = decoder.decompress(body, 4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024 or not decoder.eof or decoder.unused_data:
                raise Native5mError("WIRE_DECOMPRESSION_LIMIT_OR_TRAILING_DATA")
        elif len(body) > 4 * 1024 * 1024:
            raise Native5mError("WIRE_PLAIN_LIMIT")
        # Strip only a real terminator, never an arbitrary final character.
        text = body.decode("utf-8").removesuffix("\n")
        parts = text.split(constants.MESSAGE_SPLIT)
        if len(parts) != 13 or parts[2] != "query_history_k_data_plus":
            raise Native5mError("WIRE_BODY_CONTRACT_INVALID")
        if parts[0] != "0":
            raise Native5mError("PROVIDER_ERROR")
        return parts
    except (ValueError, IndexError, KeyError, UnicodeDecodeError, zlib.error) as exc:
        if isinstance(exc, Native5mError):
            raise
        raise Native5mError("WIRE_ENVELOPE_INVALID") from exc


def _window_page(
    raw: bytes, request: NativeWindow, page: int, page_size: int
) -> tuple[tuple[ExactBar, ...], bool]:
    parts = _wire_parts(raw)
    if (parts[7], tuple(parts[8].split(",")), *parts[9:]) != (
        request.code,
        FIELDS,
        request.start_date,
        request.end_date,
        "5",
        "3",
    ):
        raise Native5mError("RESPONSE_CONTRACT_MISMATCH")
    try:
        record = json.loads(parts[6])["record"]
        if (int(parts[4]), int(parts[5])) != (page, page_size):
            raise Native5mError("WIRE_PAGE_INVALID")
        if not isinstance(record, list) or any(not isinstance(row, list) for row in record):
            raise Native5mError("WIRE_RECORD_SCHEMA_INVALID")
        if len(record) > page_size:
            raise Native5mError("WIRE_PAGE_SIZE_EXCEEDED")
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, Native5mError):
            raise
        raise Native5mError("WIRE_PAGE_INVALID") from exc
    bars: list[ExactBar] = []
    for row in record:
        if (
            len(row) != len(FIELDS)
            or any(not isinstance(v, str) for v in row)
            or not request.start_date <= row[0] <= request.end_date
        ):
            raise Native5mError("ROW_DATE_CODE_SCHEMA_MISMATCH")
        day = NativeDay(request.code, row[0])
        response = SimpleNamespace(
            error_code="0",
            code=request.code,
            fields=FIELDS,
            start_date=row[0],
            end_date=row[0],
            frequency="5",
            adjustflag="3",
            cur_page_num="1",
        )
        response.next = iter((True, False)).__next__
        response.get_row_data = lambda current=row: current
        bars.extend(parse_response(response, day, max_rows=1))
    return tuple(bars), len(record) < page_size


def fetch_captured_window(
    query_page: Any,
    request: NativeWindow,
    capture: WireCapture,
    *,
    page_size: int = 2000,
    max_pages: int = 16,
    max_rows: int = 20000,
) -> tuple[ExactBar, ...]:
    """One bounded partition, all pages proved before any output is committed.

    query_page is the installed SDK's version-bound page transport. Its response
    object is ignored. Only a newly captured original frame can establish a page.
    All limits, including terminal-page allowance, share the caller's wire budget.
    """
    if not (1 <= page_size <= MAX_WINDOW_PAGE_SIZE and max_pages >= 1 and max_rows >= 1):
        raise Native5mError("INVALID_PARSE_LIMIT")
    bars: list[ExactBar] = []
    previous: datetime | None = None
    for page in range(1, max_pages + 1):
        before = len(capture.records)
        query_page(
            page,
            page_size,
            request.code,
            ",".join(FIELDS),
            request.start_date,
            request.end_date,
            "5",
            "3",
        )
        capture._finish()
        capture._check()
        if len(capture.records) != before + 1:
            raise Native5mError("ONE_EXACT_RESPONSE_REQUIRED")
        raw = (capture.root / capture.records[-1]["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != capture.records[-1]["response_sha256"]:
            raise Native5mError("CAPTURED_RESPONSE_CHANGED")
        rows, terminal = _window_page(raw, request, page, page_size)
        for bar in rows:
            if previous is not None and bar.timestamp <= previous:
                raise Native5mError("DUPLICATE_OR_UNSORTED_BAR_KEY")
            previous = bar.timestamp
            bars.append(bar)
            if len(bars) > max_rows:
                raise Native5mError("ROW_LIMIT")
        if terminal:
            return tuple(bars)
    raise Native5mError("WINDOW_PAGE_LIMIT_TERMINAL_UNPROVEN")


def fetch_captured_day(
    client: Any, request: NativeDay, capture: WireCapture
) -> tuple[ExactBar, ...]:
    """Use a fresh actual captured response, never repair an SDK echo in place."""
    before = len(capture.records)
    client.query_history_k_data_plus(
        request.code,
        ",".join(FIELDS),
        start_date=request.business_date,
        end_date=request.business_date,
        frequency="5",
        adjustflag="3",
    )
    capture._finish()
    capture._check()
    if len(capture.records) != before + 1:
        raise Native5mError("ONE_EXACT_RESPONSE_REQUIRED")
    raw = (capture.root / capture.records[-1]["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != capture.records[-1]["response_sha256"]:
        raise Native5mError("CAPTURED_RESPONSE_CHANGED")
    return parse_wire_response(raw, request)


def write_parquet(path: Path, bars: tuple[ExactBar, ...]) -> None:
    """Independent exact serialization, retaining each original numeric lexeme.

    A shared column scale may add trailing zeroes in the numeric representation;
    lexical_values retains each source value's original scale. No float is used.
    """
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")

    fields = [
        pa.field("code", pa.string()),
        pa.field("timestamp", pa.timestamp("us", "Asia/Shanghai")),
        pa.field("source_values", pa.list_(pa.string())),
    ]
    for index, name in enumerate(FIELDS[3:]):
        values = [bar.values[index] for bar in bars]
        scale = max([0, *(-int(value.as_tuple().exponent) for value in values)])
        precision = max(
            [
                1,
                *(
                    len(value.as_tuple().digits) + int(value.as_tuple().exponent) + scale
                    for value in values
                ),
                scale,
            ]
        )
        if precision > 76:
            raise Native5mError("DECIMAL_STORAGE_RANGE")
        kind = (
            pa.decimal128(precision, scale) if precision <= 38 else pa.decimal256(precision, scale)
        )
        fields.append(pa.field(name, kind))
    schema = pa.schema(fields, metadata={b"contract": CONTRACT.encode()})
    rows = [
        dict(
            code=bar.code,
            timestamp=bar.timestamp,
            source_values=list(bar.lexical_values),
            **dict(zip(FIELDS[3:], bar.values, strict=True)),
        )
        for bar in bars
    ]
    table = pa.Table.from_pylist(rows, schema=schema)
    with path.open("xb") as stream:
        pq.write_table(table, stream, compression="zstd")


class WireCapture:
    """Bound real socket sends/receives and retain unchanged response bytes.

    Must run in an isolated single-provider process. This is not a scheduler or
    cross-process supplier quota; the caller also owns the registered shared lock
    and cumulative job/request budget. The output directory must already be new,
    private and physically disjoint from every source root.
    """

    def __init__(
        self, sock: Any, root: Path, *, max_requests: int, max_bytes: int, seconds: float
    ) -> None:
        if min(max_requests, max_bytes, seconds) <= 0 or root.is_symlink():
            raise Native5mError("INVALID_CAPTURE_LIMIT_OR_ROOT")
        if not root.is_dir() or any(root.iterdir()):
            raise Native5mError("CAPTURE_REQUIRES_EMPTY_DIRECTORY")
        self.sock = sock
        self.root = root
        self.max_requests = max_requests
        self.max_bytes = max_bytes
        self.deadline = time.monotonic() + seconds
        self.total_bytes = 0
        self.records: list[dict[str, Any]] = []
        self.failure: str | None = None
        self._stream: BinaryIO | None = None
        self._tail = b""
        self._hash = hashlib.sha256()

    def _stop(self, reason: str) -> None:
        self.failure = reason
        raise Native5mError(reason)

    def _check(self) -> None:
        if self.failure:
            raise Native5mError(self.failure)
        if time.monotonic() >= self.deadline:
            self._stop("WIRE_TIME_LIMIT")

    def _finish(self) -> None:
        if self._stream is None:
            return
        self._stream.close()
        self._stream = None
        record = self.records[-1]
        record.update(
            response_sha256=self._hash.hexdigest(),
            ended_at=_utc(),
            complete=self._tail.endswith(TRAILER),
        )
        if not record["complete"] and self.failure is None:
            self.failure = "TRUNCATED_WIRE_RESPONSE"

    def send(self, data: bytes, *args: Any) -> int:
        self._check()
        self._finish()
        self._check()
        if len(self.records) >= self.max_requests:
            self._stop("WIRE_REQUEST_LIMIT")
        name = f"response-{len(self.records) + 1:04}.wire"
        self._stream = (self.root / name).open("xb")
        self._hash = hashlib.sha256()
        self._tail = b""
        self.records.append(
            dict(
                path=name,
                started_at=_utc(),
                request_sha256=hashlib.sha256(data).hexdigest(),
                received_bytes=0,
                complete=False,
            )
        )
        sent: int = self.sock.send(data, *args)
        if sent != len(data):
            self._stop("SHORT_SOCKET_SEND")
        return sent

    def recv(self, size: int, *args: Any) -> bytes:
        self._check()
        remaining = self.max_bytes - self.total_bytes
        if remaining <= 0:
            self._stop("WIRE_BYTE_LIMIT")
        if self._stream is None:
            self._stop("RECV_WITHOUT_REQUEST")
        chunk: bytes = self.sock.recv(min(size, remaining), *args)
        if not chunk:
            self._stop("WIRE_EOF")
        assert self._stream is not None
        self._stream.write(chunk)
        self._stream.flush()
        self._hash.update(chunk)
        self._tail = (self._tail + chunk)[-len(TRAILER) :]
        self.total_bytes += len(chunk)
        self.records[-1]["received_bytes"] += len(chunk)
        self.records[-1]["last_received_at"] = _utc()
        return chunk

    def close(self) -> None:
        self._finish()
        with (self.root / "wire-receipt.json").open("x", encoding="utf-8") as stream:
            json.dump(
                dict(contract=CONTRACT, failure=self.failure, records=self.records),
                stream,
                ensure_ascii=False,
                indent=2,
            )
        if self.failure:
            raise Native5mError(self.failure)


@contextmanager
def captured_socket(context: Any, root: Path, **limits: Any) -> Iterator[WireCapture]:
    """Always restore the SDK socket; vendor swallowed errors still fail closed."""
    original = context.default_socket
    capture = WireCapture(original, root, **limits)
    context.default_socket = capture
    try:
        yield capture
    finally:
        context.default_socket = original
        capture.close()
