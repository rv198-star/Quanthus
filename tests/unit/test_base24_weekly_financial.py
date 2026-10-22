from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from quanthus.pipelines.securities_warehouse import base24_weekly_financial as adapter
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialStatementSource,
    decode_financial_envelope,
)
from tests.unit.test_base22_financial_exact_union import raw, resolver

CAPTURE = datetime(2026, 10, 17, 1, 2, 3, 456789, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 10, 20, 1, 2, 3, 567890, tzinfo=timezone.utc)
THROUGH = date(2026, 10, 17)


def inputs(token="1.0000000001", *, publication="2026-10-16", binary=False, number=False):
    row = raw(publication=publication, synthetic_token=token)
    row.update(
        security_id="000001",
        provider_source="fzb",
        market_date=str(THROUGH),
        acquired_at=CAPTURE.isoformat(),
        period_count=1,
    )
    import ast

    payload = gzip.decompress(ast.literal_eval(row["raw_payload_gzip"].decode()))
    if number:
        payload = payload.replace(json.dumps(token).encode(), token.encode())
    packed = gzip.compress(payload, mtime=0)
    row.update(
        raw_payload_gzip=packed if binary else repr(packed).encode(),
        raw_payload_bytes=len(payload),
        raw_payload_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
    )
    receipt = dict(
        dataset="financial_statements",
        security_id="000001",
        request_json=json.dumps({"params": {"source": "fzb"}}),
        status="success_nonempty",
        response_sha256=row["raw_payload_sha256"],
        response_bytes=len(payload),
    )
    source = FinancialStatementSource(
        label="weekly",
        path=Path("/archive/raw.parquet"),
        relative_path="sources/raw.parquet",
        source_sha256="sha256:" + "a" * 64,
        source_revision=row["source_revision"],
        precedence="hotfix",
    )
    return (
        row,
        receipt,
        dict(source=source, resolver=resolver(), observed_at=OBSERVED, through=THROUGH),
    )


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("number", [False, True])
def test_exact_weekly_numeric_tokens_clocks_and_one_shared_decode(binary, number):
    row, receipt, kw = inputs("1.00000000001", binary=binary, number=number)
    before = dict(row)
    with patch.object(
        adapter, "decode_financial_envelope", wraps=adapter.decode_financial_envelope
    ) as decoder:
        tables, proof = adapter.statement_observation(row, receipt, **kw)
    assert decoder.call_count == 1
    fact = tables["dwd_financial_fact"][0]
    assert fact["value_decimal"] is None and fact["value_text"] == "1.00000000001"
    assert fact["published_at"] is None
    assert fact["knowledge_from"] == OBSERVED and row == before
    assert proof["original_acquired_at"] == CAPTURE.isoformat()
    assert proof["decoded_dependencies_built"] == 1 and not proof["historical_PIT_admission"]


@pytest.mark.parametrize("token", ["1.0000000001", "0", "", None])
def test_representable_zero_blank_and_null_remain_distinct(token):
    row, receipt, kw = inputs(token)
    tables, _ = adapter.statement_observation(row, receipt, **kw)
    fact = tables["dwd_financial_fact"][0]
    assert fact["value_decimal"] == (Decimal(token) if token not in ["", None] else None)
    assert fact["is_nil"] == (token in ["", None])


@pytest.mark.parametrize(
    "field,value",
    [
        ("security_id", "other"),
        ("response_sha256", "sha256:" + "b" * 64),
        ("response_bytes", 1),
        ("status", "failed"),
        ("request_json", '{"params":{"source":"lrb"}}'),
    ],
)
def test_receipt_closure_mismatch_fails(field, value):
    row, receipt, kw = inputs()
    receipt[field] = value
    with pytest.raises(RuntimeError, match="RECEIPT_IDENTITY"):
        adapter.statement_observation(row, receipt, **kw)


def test_clock_scope_period_and_publication_guards():
    row, receipt, kw = inputs(publication="2026-10-16T10:11:12.123456+08:00")
    tables, _ = adapter.statement_observation(row, receipt, **kw)
    assert tables["dwd_financial_fact"][0]["published_at"] == datetime(
        2026, 10, 16, 2, 11, 12, 123456, tzinfo=timezone.utc
    )
    for change in [
        dict(period_count=2),
        dict(acquired_at="2026-10-21T00:00:00+00:00"),
        dict(market_date="2026-10-18"),
    ]:
        with pytest.raises(RuntimeError):
            adapter.statement_observation({**row, **change}, receipt, **kw)


def test_decoder_retains_existing_bomb_bound_and_legacy_numeric_mode():
    row, _, _ = inputs("1.00000000001", binary=True, number=True)
    parsed = decode_financial_envelope(
        row, preserve_numeric_tokens=True, max_raw_bytes=row["raw_payload_bytes"]
    )
    assert parsed.report_list["20251231"]["data"][0]["item_value"] == "1.00000000001"
    assert isinstance(
        decode_financial_envelope(row).report_list["20251231"]["data"][0]["item_value"], float
    )
    with pytest.raises(RuntimeError, match="decompression bound"):
        decode_financial_envelope(row, preserve_numeric_tokens=True, max_raw_bytes=1)


def weekly_config():
    return json.loads(Path("config/pipelines/base24-weekly-governance.v1.json").read_text())


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "legacy"),
        ("purpose", "isolated_daily_governance"),
        ("consumer_admission", "GRANTED"),
        ("execution_authorized", True),
    ],
)
def test_weekly_reference_role_cannot_inherit_daily_or_execution(field, value, monkeypatch):
    cfg = weekly_config()
    cfg[field] = value
    monkeypatch.setattr(
        adapter, "read_native_tables", lambda *_: pytest.fail("invalid role opened Base")
    )
    with pytest.raises(RuntimeError, match="REFERENCE_ROLE"):
        adapter.weekly_parent(Path("/unreachable"), cfg)


@pytest.mark.parametrize("mutation", ["duplicate-receipt", "bad-receipt", "extra-table"])
def test_weekly_reference_closure_rejects_incomplete_evidence(mutation, monkeypatch):
    cfg = weekly_config()
    ref = cfg["weekly_identity_reference"]
    if mutation == "duplicate-receipt":
        ref["acceptance_receipts"] = [ref["acceptance_receipts"][0]] * 2
    elif mutation == "bad-receipt":
        ref["acceptance_receipts"] = [{}, {}]
    else:
        ref["tables"].append("dwd_security_bar")
    monkeypatch.setattr(
        adapter, "read_native_tables", lambda *_: pytest.fail("invalid closure opened Base")
    )
    with pytest.raises(RuntimeError, match="REFERENCE_ROLE"):
        adapter.weekly_parent(Path("/unreachable"), cfg)


def test_weekly_explicit_role_reaches_only_exact_native_identity_tables(monkeypatch):
    class Sentinel(Exception):
        pass

    calls = []

    def native(base, cfg, tables):
        calls.append((base, tables))
        raise Sentinel

    monkeypatch.setattr(adapter, "read_native_tables", native)
    with pytest.raises(Sentinel):
        adapter.weekly_parent(Path("/unreachable"), weekly_config())
    assert calls == [(Path("/unreachable"), ("dwd_security", "dwd_security_identifier"))]
