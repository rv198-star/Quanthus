from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from quanthus.pipelines.securities_warehouse import base23_financial as adapter
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
    FinancialStatementSource,
)
from tests.unit.test_base23_financial_reconfirmation import fixture


def case(token="1.00000000001"):
    from pathlib import Path

    raw = fixture(token)
    source = FinancialStatementSource("statements", Path("/unreachable/raw.parquet"),
                                    "raw/statements.parquet", "sha256:" + "a" * 64,
                                    "retained-September-v1", "parent")
    resolver = FinancialCompanyResolver([
        FinancialCompanyIdentity("000001", "security-1", "company-1",
                                 datetime(1991, 1, 1, tzinfo=timezone.utc), None)
    ])
    return raw, source, resolver, datetime(2026, 10, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize("token", ["1.00000000001", "-0.00000", "NaN", "", None, "literal"])
def test_complete_capture_decodes_once_for_three_consumers_and_preserves_original_atoms(token):
    raw, source, resolver, observed = case(token)
    with patch.object(adapter, "decode_financial_envelope",
                      wraps=adapter.decode_financial_envelope) as decoder:
        rows, proof = adapter.statement_observation(raw, source=source, resolver=resolver,
                                                    observed_at=observed)
    assert decoder.call_count == proof["decoded_dependencies_built"] == 1
    assert all(r["knowledge_from"] == observed for rs in rows.values() for r in rs)
    fact = rows[adapter.TABLES[2]][0]
    if token == "1.00000000001":
        assert fact["value_decimal"] is None and fact["value_text"] == token
    if token == "-0.00000":
        assert fact["value_decimal"] == Decimal(token)
    assert fact["published_at"] is None
    assert proof["actual_acquisition_instant"].microsecond == 123456
    assert not proof["provider_PIT_qualified"] and not proof["native_history_changed"]


def test_september_period_is_not_silently_cut_off_at_august():
    import ast
    import gzip
    import hashlib
    import json

    raw, source, resolver, observed = case()
    body = json.loads(gzip.decompress(ast.literal_eval(raw["raw_payload_gzip"].decode())))
    report = body["result"]["data"]["report_list"].pop("20260630")
    report["publish_date"] = "2026-09-30"
    body["result"]["data"]["report_list"]["20260930"] = report
    encoded = json.dumps(body).encode()
    raw.update(raw_payload_gzip=repr(gzip.compress(encoded)).encode(),
               raw_payload_bytes=len(encoded),
               raw_payload_sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
               market_date="2026-09-30", acquired_at="2026-09-30T23:00:00+08:00")
    rows, _ = adapter.statement_observation(raw, source=source, resolver=resolver,
                                          observed_at=observed)
    assert rows[adapter.TABLES[0]][0]["period_end"].isoformat() == "2026-09-30"


def test_future_actual_capture_is_rejected():
    raw, source, resolver, observed = case()
    raw["acquired_at"] = "2026-10-10T00:00:00+08:00"
    with pytest.raises(RuntimeError, match="CAPTURE_AFTER_GOVERNANCE"):
        adapter.statement_observation(raw, source=source, resolver=resolver, observed_at=observed)


def test_latest_selection_uses_actual_capture_not_package_order():
    sources = [{"sha256": "sha256:" + c * 64, "rows": 1, "release": "release-" + c}
               for c in "ab"]
    a = dict(member_sha256=sources[0]["sha256"], ordinal=0,
             identity_key=["000001", "balance_sheet", "p", "s", "v"],
             original_raw_acquired_at="2026-09-01T00:00:00+08:00",
             complete_envelope_fingerprint="sha256:" + "c" * 64)
    b = dict(a, member_sha256=sources[1]["sha256"],
             original_raw_acquired_at="2026-09-05T00:00:00+08:00")
    selected = adapter.select_latest_changes([b, a], sources)
    assert selected[0]["member_sha256"] == b["member_sha256"]
    assert selected == adapter.select_latest_changes([a, b], list(reversed(sources)))
    equal = dict(b, original_raw_acquired_at=a["original_raw_acquired_at"])
    same = adapter.select_latest_changes([a, equal], sources)
    assert same == adapter.select_latest_changes([equal, a], sources)
    assert same[0]["release"] == next(s["release"] for s in sources
                                     if s["sha256"] == same[0]["member_sha256"])
    conflict = dict(b, original_raw_acquired_at=a["original_raw_acquired_at"],
                    complete_envelope_fingerprint="sha256:" + "d" * 64)
    with pytest.raises(RuntimeError, match="EQUAL_CLOCK_BODY_CONFLICT"):
        adapter.select_latest_changes([a, conflict], sources)
