from __future__ import annotations

import ast
import gzip
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import base22_financial as adapter
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
    FinancialReportingBuildError,
    FinancialStatementSource,
    financial_reporting_schemas,
)


def fixture(token: object = "12.500000", *, publication: object = "2026-07-31"):
    payload = {
        "result": {
            "data": {
                "report_list": {
                    "20260630": {
                        "rType": "consolidated",
                        "rCurrency": "CNY",
                        "publish_date": publication,
                        "data": [
                            {"item_display_type": 1, "item_title": "heading"},
                            {"item_field": "CASH", "item_title": "Cash", "item_value": token},
                        ],
                    }
                }
            }
        }
    }
    raw = json.dumps(payload, ensure_ascii=False).encode()
    row = {
        "security_id": "000001",
        "stock_code": "000001",
        "statement_type": "balance_sheet",
        "source": "fixture-provider",
        "source_revision": "fixture-v1",
        "acquired_at": "2026-08-01T10:24:43.123456+08:00",
        "period_count": 1,
        "raw_payload_bytes": len(raw),
        "raw_payload_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "raw_payload_gzip": repr(gzip.compress(raw)).encode("ascii"),
    }
    source = FinancialStatementSource(
        "statements",
        Path("/unavailable/source.parquet"),
        "raw/envelope.parquet",
        "sha256:" + "a" * 64,
        "fixture-v1",
        "parent",
    )
    resolver = FinancialCompanyResolver(
        [
            FinancialCompanyIdentity(
                "000001", "security-1", "company-1", datetime(1991, 1, 1, tzinfo=timezone.utc), None
            )
        ]
    )
    return row, source, resolver


@pytest.mark.parametrize(
    "token,classification,decimal,text,nil",
    [
        ("12.500000", "EXACTLY_REPRESENTABLE", Decimal("12.5"), None, False),
        ("-0.000000000000", "EXACTLY_REPRESENTABLE", Decimal("0"), None, False),
        ("1.00000000000", "EXACTLY_REPRESENTABLE", Decimal("1"), None, False),
        ("0.12345678901", "FINITE_SCHEMA_LIMIT", None, "0.12345678901", False),
        ("9" * 28, "EXACTLY_REPRESENTABLE", Decimal("9" * 28), None, False),
        ("1" + "0" * 28, "FINITE_SCHEMA_LIMIT", None, "1" + "0" * 28, False),
        ("NaN", "NONFINITE", None, "NaN", False),
        ("Infinity", "NONFINITE", None, "Infinity", False),
        ("original text", "INVALID_NUMBER", None, "original text", False),
        (None, "NULL", None, None, True),
        ("", "NULL", None, None, True),
        ("  ", "NULL", None, None, True),
    ],
)
def test_exact_tokens_and_typed_roundtrip(tmp_path, token, classification, decimal, text, nil):
    raw, source, resolver = fixture(token)
    with localcontext() as context:
        context.prec = 6
        rows, evidence = adapter.statement_observation(raw, source=source, resolver=resolver)
    fact = rows[adapter.TABLES[2]][0]
    assert (fact["value_decimal"], fact["value_text"], fact["is_nil"]) == (decimal, text, nil)
    assert evidence["scalar_classifications"] == {classification: 1}
    assert fact["published_at"] is None
    assert evidence["actual_acquisition_instant"].microsecond == 123456
    restored = {}
    for table, schema in financial_reporting_schemas().items():
        path = tmp_path / (table + ".parquet")
        pq.write_table(pa.Table.from_pylist(rows[table], schema=schema), path)
        restored[table] = pq.ParquetFile(path).read().to_pylist()
    with patch.object(Path, "open", side_effect=AssertionError("source access forbidden")):
        adapter.verify_statement_observation(
            raw, source=source, resolver=resolver, rows=restored, evidence=evidence
        )


@pytest.mark.parametrize("token", [1.2, 12, True, {"bad": "object"}])
def test_original_numeric_token_unavailable_blocks(token):
    raw, source, resolver = fixture(token)
    with pytest.raises(BoundedError, match="ORIGINAL_SCALAR_TOKEN_UNAVAILABLE"):
        adapter.statement_observation(raw, source=source, resolver=resolver)


def test_decode_once_for_all_three_consumers():
    raw, source, resolver = fixture()
    with patch.object(
        adapter, "decode_financial_envelope", wraps=adapter.decode_financial_envelope
    ) as decoder:
        rows, evidence = adapter.statement_observation(raw, source=source, resolver=resolver)
    assert decoder.call_count == evidence["decoded_dependencies_built"] == 1
    assert all(rows[t] for t in adapter.TABLES)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("period_count", 2, "PERIOD_CENSUS_FAILED"),
        ("raw_payload_bytes", 1048577, "BYTE_BOUND_FAILED"),
        ("acquired_at", "2026-08-01T00:00:00.1234567Z", "CLOCK_PRECISION_UNSUPPORTED"),
    ],
)
def test_closed_bounds(field, value, reason):
    raw, source, resolver = fixture()
    raw[field] = value
    with pytest.raises(BoundedError, match=reason):
        adapter.statement_observation(raw, source=source, resolver=resolver)


@pytest.mark.parametrize("publication", ["2026-08-01T00:00:00Z", "2026-08-01T08:00:00+08:00"])
def test_genuine_publication_instant_preserved(publication):
    raw, source, resolver = fixture(publication=publication)
    rows, _ = adapter.statement_observation(raw, source=source, resolver=resolver)
    assert rows[adapter.TABLES[0]][0]["published_at"] == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_future_publication_blocks_instead_of_retiming():
    raw, source, resolver = fixture(publication="2026-08-03T00:00:00Z")
    with pytest.raises(BoundedError, match="PUBLICATION_AFTER_ACQUISITION"):
        adapter.statement_observation(raw, source=source, resolver=resolver)


def test_hash_mismatch_blocks():
    raw, source, resolver = fixture()
    raw["raw_payload_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(FinancialReportingBuildError, match="SHA-256 differs"):
        adapter.statement_observation(raw, source=source, resolver=resolver)


def test_decompression_is_bounded_even_when_declared_size_is_false():
    raw, source, resolver = fixture()
    packed = gzip.compress(b"x" * (adapter.MAX_RAW_BYTES + 100))
    raw["raw_payload_gzip"] = repr(packed).encode("ascii")
    with pytest.raises(FinancialReportingBuildError, match="decompression bound"):
        adapter.statement_observation(raw, source=source, resolver=resolver)


def test_september_period_is_excluded_without_changing_raw_observation():
    raw, source, resolver = fixture()
    payload = gzip.decompress(ast.literal_eval(raw["raw_payload_gzip"].decode("ascii")))
    payload = payload.replace(b"20260630", b"20260930")
    raw["raw_payload_gzip"] = repr(gzip.compress(payload)).encode("ascii")
    raw["raw_payload_bytes"] = len(payload)
    raw["raw_payload_sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    rows, evidence = adapter.statement_observation(raw, source=source, resolver=resolver)
    assert all(not rows[t] for t in adapter.TABLES)
    assert evidence["quality_exceptions"][0]["code"] == "AFTER_THROUGH_DATE"


def test_unknown_dated_company_stays_explicitly_unavailable():
    raw, source, _ = fixture()
    rows, evidence = adapter.statement_observation(
        raw, source=source, resolver=FinancialCompanyResolver([])
    )
    assert all(not rows[t] for t in adapter.TABLES)
    assert evidence["quality_exceptions"]
    assert not evidence["historical_acceptance_asserted"]


@pytest.mark.parametrize("changed", [False, True])
def test_bounded_batch_retains_only_real_body_versions(changed):
    first, source, resolver = fixture("12")
    second, _, _ = fixture("13" if changed else "12")
    second["acquired_at"] = "2026-08-02T10:24:43+08:00"
    rules = json.loads(Path("config/pipelines/base22-independent-image.v1.json").read_bytes())[
        "tables"
    ]
    rows, evidence = adapter.statement_batch(
        [(first, source), (second, source)], resolver=resolver, rules=rules
    )
    facts = rows[adapter.TABLES[2]]
    assert len(facts) == (2 if changed else 1)
    assert evidence["source_observations_retained"] == 2
    if changed:
        assert facts[0]["knowledge_to"] == facts[1]["knowledge_from"]
        assert facts[1]["knowledge_to"] is None


def test_equal_clock_conflicting_body_fails_without_choosing_source():
    first, source, resolver = fixture("12")
    second, _, _ = fixture("13")
    rules = json.loads(Path("config/pipelines/base22-independent-image.v1.json").read_bytes())[
        "tables"
    ]
    with pytest.raises(RuntimeError, match="EQUAL_CLOCK_BODY_CONFLICT"):
        adapter.statement_batch([(first, source), (second, source)], resolver=resolver, rules=rules)


def test_batch_envelope_bound():
    raw, source, resolver = fixture()
    rules = json.loads(Path("config/pipelines/base22-independent-image.v1.json").read_bytes())[
        "tables"
    ]
    with pytest.raises(BoundedError, match="BATCH_ENVELOPE_BOUND_FAILED"):
        adapter.statement_batch([(raw, source)] * 65, resolver=resolver, rules=rules)


@pytest.mark.parametrize("change", ["value", "time", "source", "rows", "evidence"])
def test_repacked_semantic_attacks_rejected(change):
    raw, source, resolver = fixture()
    rows, evidence = adapter.statement_observation(raw, source=source, resolver=resolver)
    fact = rows[adapter.TABLES[2]][0]
    if change == "value":
        fact["value_decimal"] += 1
    elif change == "time":
        fact["knowledge_from"] = datetime(2026, 7, 1, tzinfo=timezone.utc)
    elif change == "source":
        fact["source_artifact_sha256"] = "sha256:" + "b" * 64
    elif change == "rows":
        rows[adapter.TABLES[2]].clear()
    else:
        evidence["scalar_classifications"] = {}
    with pytest.raises(BoundedError, match="RAW_TO_TYPED_OUTPUT_MISMATCH"):
        adapter.verify_statement_observation(
            raw, source=source, resolver=resolver, rows=rows, evidence=evidence
        )
