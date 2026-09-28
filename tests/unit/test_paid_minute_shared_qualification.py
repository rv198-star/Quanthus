"""Audit and production must qualify the captured bytes with the same exact gate."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from quanthus.providers.minute_source_selection import choose_whole_security, qualify_candidate
from quanthus.providers.paid_minute_source import (
    _audit_symbol,
    _resume_state,
    normalized_candidate,
    qualify_payload,
    qualify_raw,
)
from tests.unit.test_paid_minute_source import DATE, FIELDS, payload, start_labeled_payload


def raw_response(*, mode="end", close="11"):
    value = payload() if mode == "end" else start_labeled_payload()
    closing_time = "15:00:00" if mode == "end" else "14:59:00"
    row = next(row for row in value["data"]["items"] if row[1].endswith(closing_time))
    row[FIELDS.index("close")] = close
    row[FIELDS.index("high")] = str(max(Decimal(close), Decimal(11)))
    row[FIELDS.index("low")] = str(min(Decimal(close), Decimal(11)))
    return json.dumps(value).encode()


def inputs(*, mode="end"):
    return {
        "symbol": "000001.SZ",
        "market_date": DATE,
        "daily_volume": 2500 if mode == "end" else 2400,
        "daily_close": 11,
        "first_5m_volume": 150 if mode == "end" else 50,
    }


def fake_http(monkeypatch, raw):
    import requests

    class Response:
        status_code = 200
        headers = {"x-request-id": "synthetic-request"}
        content = raw

        def json(self):
            raise AssertionError("qualification must use the captured bytes, not response.json()")

    calls = []

    def get(*_args, **_kwargs):
        calls.append("synthetic HTTP only")
        return Response()

    monkeypatch.setattr(requests, "get", get)
    return calls


def audit(monkeypatch, tmp_path, raw, *, mode="end"):
    calls = fake_http(monkeypatch, raw)
    evidence = inputs(mode=mode)
    receipt = _audit_symbol(
        "000001",
        day=DATE,
        key="synthetic-key-never-persist",
        daily={"volume": evidence["daily_volume"], "close": evidence["daily_close"]},
        first_5m_volume=evidence["first_5m_volume"],
        output_dir=tmp_path,
    )
    assert calls == ["synthetic HTTP only"]
    captured = tmp_path / "raw" / "000001.json.gz"
    assert gzip.decompress(captured.read_bytes()) == raw
    assert receipt["source_raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "synthetic-key-never-persist" not in (tmp_path / "receipts/000001.json").read_text()
    return receipt


@pytest.mark.parametrize("mode", ["end", "start"])
@pytest.mark.parametrize("close", ["11.01", "10.99", "11.000000000000002"])
def test_provisional_close_tolerance_never_grants_exact_qualification(
    tmp_path, monkeypatch, mode, close
):
    raw = raw_response(mode=mode, close=close)
    evidence = inputs(mode=mode)
    provisional = qualify_payload(json.loads(raw), **evidence)
    assert provisional["status"] == "interpreted_payload_pending_strict_check"

    with pytest.raises(ValueError, match="minute/daily close mismatch"):
        qualify_raw(raw, raw_ref="synthetic-capture.json.gz", **evidence)
    replay = normalized_candidate(raw, raw_ref="synthetic-capture.json.gz", **evidence)
    with pytest.raises(ValueError, match="minute/daily close mismatch"):
        qualify_candidate(
            replay,
            symbol="000001",
            market_date=DATE,
            daily_volume=evidence["daily_volume"],
            daily_close=11,
            traded=True,
        )

    receipt = audit(monkeypatch, tmp_path, raw, mode=mode)
    assert receipt["http_status"] == 200
    assert receipt["status"] == "unqualified"
    assert "minute/daily close mismatch" in receipt["reason"]
    assert "source_semantic_sha256" not in receipt


@pytest.mark.parametrize("mode", ["end", "start"])
def test_audit_and_replay_share_exact_hash_over_the_same_captured_bytes(
    tmp_path, monkeypatch, mode
):
    raw = raw_response(mode=mode)
    evidence = inputs(mode=mode)
    candidate, detail = qualify_raw(raw, raw_ref="synthetic-capture.json.gz", **evidence)
    replay = normalized_candidate(raw, raw_ref="synthetic-capture.json.gz", **evidence)
    assert candidate == replay
    assert candidate.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert all(row["amount"] is None for row in candidate.rows)
    assert detail["status"] == "qualified_candidate_not_published"
    assert detail["source_semantic_sha256"] == qualify_candidate(
        candidate,
        symbol="000001",
        market_date=DATE,
        daily_volume=evidence["daily_volume"],
        daily_close=11,
        traded=True,
    )
    receipt = audit(monkeypatch, tmp_path, raw, mode=mode)
    assert receipt["status"] == detail["status"]
    assert receipt["source_semantic_sha256"] == detail["source_semantic_sha256"]


def test_invalid_captured_json_is_not_replaced_by_another_decoded_payload(tmp_path, monkeypatch):
    receipt = audit(monkeypatch, tmp_path, b"not JSON: retained immutable evidence")
    assert receipt["status"] == "unqualified"
    assert receipt["http_status"] == 200


@pytest.mark.parametrize("raw", [b"[]", b"null", b"true", b"123"])
def test_non_mapping_captured_json_is_retained_but_not_qualified(tmp_path, monkeypatch, raw):
    receipt = audit(monkeypatch, tmp_path, raw)
    assert receipt["status"] == "unqualified"
    assert "not a data mapping" in receipt["reason"]
    assert "source_semantic_sha256" not in receipt


def test_current_unqualified_http_200_remains_reusable_without_a_refetch(tmp_path, monkeypatch):
    raw = raw_response(close="11.01")
    receipt = audit(monkeypatch, tmp_path, raw)
    assert receipt["status"] == "unqualified"
    before = (tmp_path / "receipts" / "000001.json").read_bytes()
    missing, counts = _resume_state(tmp_path, codes=["000001"], market_date=DATE)
    assert missing == []
    assert counts == {"unqualified": 1}
    assert (tmp_path / "receipts" / "000001.json").read_bytes() == before


def test_one_raw_decode_and_one_adapter_interpretation_per_evaluation(monkeypatch):
    from quanthus.providers import paid_minute_source as source

    raw = raw_response()
    real_loads = source.json.loads
    real_interpret = source.qualify_payload
    calls = []

    def loads(value):
        calls.append("decode")
        return real_loads(value)

    def interpret(value, **kwargs):
        calls.append("interpret")
        return real_interpret(value, **kwargs)

    monkeypatch.setattr(source.json, "loads", loads)
    monkeypatch.setattr(source, "qualify_payload", interpret)
    qualify_raw(raw, raw_ref="synthetic-capture.json.gz", **inputs())
    assert calls == ["decode", "interpret"]


def test_unqualified_relay_candidate_does_not_preempt_a_valid_whole_security_peer():
    rejected = normalized_candidate(
        raw_response(close="11.01"), raw_ref="synthetic-rejected.json.gz", **inputs()
    )
    valid = normalized_candidate(
        raw_response(), raw_ref="synthetic-valid.json.gz", **inputs()
    )
    valid = replace(valid, provider="synthetic-precise-whole-source")
    decision = choose_whole_security(
        [rejected, valid],
        symbol="000001",
        market_date=DATE,
        daily_volume=2500,
        daily_close=11,
        traded=True,
        priority=(valid.provider, rejected.provider),
    )
    assert decision.status == "qualified_whole_security_candidate"
    assert decision.selected_provider == valid.provider
    assert decision.rejected == ((rejected.provider, "minute/daily close mismatch"),)
