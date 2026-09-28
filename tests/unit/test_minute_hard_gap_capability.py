"""Capability diagnostics must never certify absent rows or change publishing."""

import gzip
import json

import pytest

from scripts import diagnose_minute_hard_gaps as cap
from tests.unit.test_minute_offline_ledger import fixture, put_paid, tencent_payload
from tests.unit.test_minute_offline_ledger import run as ledger_run


def gaps(root, codes=("000001",)):
    refs, ts, paid = fixture(root, codes=codes)
    summary, _, output = ledger_run(root, refs, ts, paid)
    return output, summary["records_sha256"]


def test_offline_default_never_reads_key_fetches_or_publishes(tmp_path, monkeypatch):
    source, sha = gaps(tmp_path)
    monkeypatch.setattr(cap, "_audit_symbol", lambda *a, **k: pytest.fail("no request"))
    monkeypatch.setattr(cap, "_tencent_one_minute_bars", lambda *a, **k: pytest.fail("no request"))
    output = tmp_path / "capability"
    result = cap.run(source, sha, output)
    assert result["offline_evaluated"] == 1
    assert result["probe_processed"] == 0
    assert not result["all_hard_gaps_qualified"]
    assert not result["publication_authorized"]
    assert not list(output.rglob("*.parquet"))


@pytest.mark.parametrize("mode", ["hash", "summary", "identity", "checkpoint", "oversize"])
def test_changed_or_overbroad_input_fails_closed(tmp_path, mode):
    source, sha = gaps(tmp_path, codes=tuple(f"{i:06}" for i in range(1, 26))
                       if mode == "oversize" else ("000001",))
    if mode == "hash":
        sha = "0" * 64
    elif mode == "summary":
        p = source / "summary.json"
        s = json.loads(p.read_bytes())
        s["processed"] = 0
        p.write_text(json.dumps(s))
    elif mode == "identity":
        p = source / "job.json"
        s = json.loads(p.read_bytes())
        s["identity"]["market_date"] = "2099-01-01"
        p.write_text(json.dumps(s))
    elif mode == "checkpoint":
        (tmp_path / "tencent/checkpoint.json").write_text('{"symbols":{},"changed":true}')
    with pytest.raises(ValueError):
        cap.run(source, sha, tmp_path / "capability")


def test_quality_rejection_does_not_trigger_transport_stop(tmp_path, monkeypatch):
    source, sha = gaps(tmp_path, codes=("000001", "000002"))
    calls = []
    monkeypatch.setattr(cap.time, "sleep", lambda _: None)

    def tencent(code, _day, _refs, root):
        calls.append(("tencent", code))
        root.mkdir(parents=True, exist_ok=True)
        value = tencent_payload(code)
        value["data"]["sz"+code]["m1"].pop(15)
        (root / (code+".json.gz")).write_bytes(gzip.compress(json.dumps(value).encode()))
        raise RuntimeError("quality: source grid/volume mismatch")

    def paid(code, *, output_dir, **_kw):
        calls.append(("paid", code))
        (output_dir / "raw").mkdir(exist_ok=True)
        (output_dir / "receipts").mkdir(exist_ok=True)
        from tests.unit.test_paid_minute_source import payload
        value = payload()
        value["data"]["items"][5][6] += 1
        put_paid(output_dir, value, code=code)
        return {"status": "unqualified"}

    monkeypatch.setattr(cap, "_tencent_one_minute_bars", tencent)
    monkeypatch.setattr(cap, "_audit_symbol", paid)
    result = cap.run(source, sha, tmp_path / "capability", probe=True, key="synthetic")
    assert len(calls) == 4
    assert result["probe_processed"] == 2
    assert result["stop_reason"] == "completed_existing_source_probe"
    assert not result["all_hard_gaps_qualified"]
    assert result["bounded_diagnostic_dispositions"] == {"pending_no_qualified_source": 2}


@pytest.mark.parametrize("status", ["source_pending", "rate_limited", "http_error",
                                  "transport_error"])
def test_acquisition_failure_stops_but_quality_does_not(status):
    assert cap.probe_status_stops({"status": status})
    assert not cap.probe_status_stops({"status": "unqualified"})


def test_probe_without_key_or_with_input_overlap_is_rejected(tmp_path):
    source, sha = gaps(tmp_path)
    with pytest.raises(ValueError, match="stdin key"):
        cap.run(source, sha, tmp_path / "out", probe=True)
    with pytest.raises(ValueError, match="overlaps"):
        cap.run(source, sha, source / "out")


def test_new_capture_integrity_fault_stops_before_next_security(tmp_path, monkeypatch):
    source, sha = gaps(tmp_path, codes=("000001", "000002"))
    calls = []
    monkeypatch.setattr(cap.time, "sleep", lambda _: None)

    def tencent(code, _day, _refs, root):
        calls.append(("tencent", code))
        root.mkdir(parents=True, exist_ok=True)
        (root / (code + ".json.gz")).write_bytes(
            gzip.compress(json.dumps(tencent_payload(code)).encode())
        )

    def paid(code, *, output_dir, **_kw):
        calls.append(("paid", code))
        from tests.unit.test_paid_minute_source import payload
        (output_dir / "raw").mkdir(exist_ok=True)
        (output_dir / "receipts").mkdir(exist_ok=True)
        put_paid(output_dir, payload(), code=code)
        receipt_path = output_dir / "receipts" / (code + ".json")
        receipt = json.loads(receipt_path.read_bytes())
        receipt["source_raw_sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt))
        return receipt

    monkeypatch.setattr(cap, "_tencent_one_minute_bars", tencent)
    monkeypatch.setattr(cap, "_audit_symbol", paid)
    result = cap.run(source, sha, tmp_path / "capability", probe=True, key="synthetic")
    assert calls == [("tencent", "000001"), ("paid", "000001")]
    assert result["probe_processed"] == 1
    assert result["stop_reason"] == "new_capture_integrity_fault"
    assert not result["all_hard_gaps_qualified"]
