from __future__ import annotations

import gzip
import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import run_acquisition
from quanthus.providers.native_5m_exact import NativeWindow
from quanthus.providers.tushare_native_5m import (
    BUCKETS,
    ENDPOINT,
    TushareNative5mError,
    fetch_captured_day,
    parse_response,
)


def _raw(*, code: str = "000001.SZ", day: str = "2026-09-30") -> bytes:
    fields = [
        "ts_code",
        "trade_time",
        "freq",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
    ]
    items = [
        [
            code,
            f"{day} {label}",
            "5MIN",
            "10.1000",
            "10.3000",
            "10.0000",
            "10.2000",
            "123.00",
            "1254.600",
        ]
        for label in BUCKETS
    ]
    return json.dumps(
        {
            "code": 0,
            "api_name": "stk_mins",
            "count": len(items),
            "meta": {"coverage": "complete", "partial": False},
            "data": {"fields": fields, "items": items},
        },
        separators=(",", ":"),
    ).encode()


def _request() -> NativeWindow:
    return NativeWindow("sz.000001", "2026-09-30", "2026-09-30")


def test_parse_response_preserves_exact_grid_and_lexical_decimals() -> None:
    rows = parse_response(_raw(), _request())
    assert len(rows) == 48
    assert rows[0].timestamp.isoformat() == "2026-09-30T09:35:00+08:00"
    assert rows[-1].timestamp.isoformat() == "2026-09-30T15:00:00+08:00"
    assert rows[0].lexical_values == (
        "10.1000",
        "10.3000",
        "10.0000",
        "10.2000",
        "123.00",
        "1254.600",
    )
    assert rows[0].values[-1] == Decimal("1254.600")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda body: body["meta"].update(partial=True), "SOURCE_RESPONSE_PARTIAL"),
        (lambda body: body["data"]["items"].pop(), "SOURCE_SCHEMA_OR_COUNT_INVALID"),
        (
            lambda body: body["data"]["items"][0].__setitem__(0, "600000.SH"),
            "SOURCE_ROW_SCOPE_INVALID",
        ),
        (
            lambda body: body["data"]["items"][0].__setitem__(1, "2026-09-29 09:35:00"),
            "SOURCE_ROW_DATE_OR_DUPLICATE_INVALID",
        ),
        (lambda body: body["data"]["items"][0].__setitem__(8, None), "SOURCE_NUMERIC_INVALID"),
    ],
)
def test_parse_response_fails_closed(mutate, reason: str) -> None:
    body = json.loads(_raw())
    mutate(body)
    with pytest.raises(TushareNative5mError, match=reason):
        parse_response(json.dumps(body).encode(), _request())


def test_parse_response_rejects_non_exact_day() -> None:
    request = NativeWindow("sz.000001", "2026-09-29", "2026-09-30")
    with pytest.raises(TushareNative5mError, match="EXACT_DAY_REQUIRED"):
        parse_response(_raw(), request)


def test_fetch_captures_raw_without_persisting_credential(tmp_path) -> None:
    observed = {}

    def get(url, **kwargs):
        observed.update(url=url, **kwargs)
        return SimpleNamespace(
            status_code=200,
            content=_raw(),
            headers={"x-request-id": "request-1"},
        )

    bars, metrics = fetch_captured_day(
        tmp_path,
        _request(),
        api_key="secret-only-in-memory",
        get=get,
    )
    assert len(bars) == 48
    assert observed["url"] == ENDPOINT
    assert observed["params"]["freq"] == "5min"
    assert observed["headers"] == {"X-API-Key": "secret-only-in-memory"}
    assert metrics["native_frequency"] == "5m"
    assert metrics["source_endpoint"] == ENDPOINT
    assert metrics["derived_from"] is None
    assert metrics["credential_persisted"] is False
    assert "secret-only-in-memory" not in json.dumps(metrics)
    with gzip.open(tmp_path / "source-response.json.gz", "rb") as stream:
        assert stream.read() == _raw()


def test_fetch_rejects_http_without_writing_payload(tmp_path) -> None:
    def get(*_args, **_kwargs):
        return SimpleNamespace(status_code=401, content=b"denied", headers={})

    with pytest.raises(TushareNative5mError, match="SOURCE_HTTP_401"):
        fetch_captured_day(tmp_path, _request(), api_key="runtime", get=get)
    assert list(tmp_path.iterdir()) == []


def test_fetcher_seals_through_generic_native5m_acquisition(tmp_path) -> None:
    def get(*_args, **_kwargs):
        return SimpleNamespace(status_code=200, content=_raw(), headers={})

    identity = hashlib.sha256(b"SZSE\t000001\t2026-09-30\t2026-09-30").hexdigest()
    plan = {
        "contract": "p10-base24-native5m-missing-window-plan-v1",
        "frequency": "5",
        "adjustflag": "3",
        "business_through": "2026-09-30",
        "missing_security_days": 1,
        "windows": [
            {
                "partition_id": identity,
                "venue": "SZSE",
                "code": "sz.000001",
                "start_date": "2026-09-30",
                "end_date": "2026-09-30",
                "required_security_days": 1,
                "expected_source_days": 1,
                "required_dates": ["2026-09-30"],
                "expected_session_dates": ["2026-09-30"],
                "maximum_rows": 48,
            }
        ],
    }
    output = tmp_path / "output"
    result = run_acquisition(
        (json.dumps(plan, separators=(",", ":")) + "\n").encode(),
        output,
        lambda staging, request: fetch_captured_day(
            staging,
            request,
            api_key="runtime-only",
            get=get,
        ),
        source_roots=(),
    )
    receipt = json.loads((output / "partitions" / identity / "RECEIPT.json").read_text())
    assert result["status"] == "COMPLETE"
    assert receipt["rows"] == 48
    assert receipt["metrics"]["provider"] == "tushare-stk_mins-native-5m"
    assert "source-response.json.gz" in receipt["files"]
    assert "runtime-only" not in json.dumps(receipt)
