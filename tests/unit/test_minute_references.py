"""Synthetic reference closure; no provider truth or production activation claims."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from quanthus.providers import minute_tdx, paid_minute_source
from quanthus.providers.minute_references import load_minute_references
from quanthus.providers.minute_source_selection import qualify_candidate
from tests.unit.test_paid_minute_source import DATE, payload

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def write_refs(root, *, volume=2400, first=50):
    root.mkdir(parents=True, exist_ok=True)
    daily = [{"stock_code": "000001", "market_date": DATE, "volume": volume, "close": 11}]
    status = [{"stock_code": "000001", "market_date": DATE, "tradestatus": "1"}]
    five = [{"stock_code": "000001", "ts": f"{DATE} 09:35:00", "volume": first}]
    paths = {
        "daily_path": root / "daily.parquet",
        "status_path": root / "status.parquet",
        "five_path": root / "five.parquet",
    }
    for role, rows in (("daily_path", daily), ("status_path", status), ("five_path", five)):
        pq.write_table(pa.Table.from_pylist(rows), paths[role])
    return paths


def load(paths, **kwargs):
    return load_minute_references(**paths, market_date=DATE, require_first_five=True, **kwargs)


def test_captured_once_and_shared_immutable_view(tmp_path, monkeypatch):
    paths = write_refs(tmp_path)
    read_table = pq.read_table
    reads = []

    def capture(source, **kwargs):
        assert isinstance(source, pa.BufferReader)
        reads.append(kwargs["columns"])
        return read_table(source, **kwargs)

    monkeypatch.setattr(pq, "read_table", capture)
    refs = load(paths)
    selected = refs.select(["000001"], require_first_five=True)
    assert len(reads) == 3
    assert refs["000001"] is selected["000001"]
    assert refs.effective_view_sha256 == selected.effective_view_sha256
    assert refs["000001"]["first_5m_volume"] == Decimal(50)
    with pytest.raises(TypeError):
        refs.rows["000001"] = {}
    with pytest.raises(TypeError):
        refs["000001"]["close"] = Decimal(12)
    with pytest.raises(FrozenInstanceError):
        refs.market_date = "2026-09-24"
    for item in refs.inputs:
        assert (
            item.sha256
            == hashlib.sha256(
                paths[
                    item.role.replace("sealed_native_5m", "five_path")
                    .replace("sealed_daily", "daily_path")
                    .replace("sealed_status", "status_path")
                ].read_bytes()
            ).hexdigest()
        )


def test_replacing_files_cannot_change_the_captured_job_view(tmp_path):
    paths = write_refs(tmp_path)
    captured = load(paths)
    write_refs(tmp_path, first=49)
    changed = load(paths)
    assert captured["000001"]["first_5m_volume"] == 50
    assert changed["000001"]["first_5m_volume"] == 49
    assert captured.effective_view_sha256 != changed.effective_view_sha256
    assert captured.inputs[2].sha256 != changed.inputs[2].sha256


@pytest.mark.parametrize("role", ["daily_path", "status_path"])
@pytest.mark.parametrize("fault", ["duplicate", "wrong_date", "missing_universe", "bad_code"])
def test_daily_status_identity_faults_fail_closed(tmp_path, role, fault):
    paths = write_refs(tmp_path)
    rows = pq.read_table(paths[role]).to_pylist()
    if fault == "duplicate":
        rows += rows
    elif fault == "wrong_date":
        rows[0]["market_date"] = "2026-09-22"
    else:
        rows[0]["stock_code"] = "600000" if fault == "missing_universe" else "1"
    pq.write_table(pa.Table.from_pylist(rows), paths[role])
    with pytest.raises(ValueError):
        load(paths)


@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "-1", True])
@pytest.mark.parametrize("field", ["volume", "close"])
def test_invalid_reference_values_do_not_enter_a_candidate(tmp_path, value, field):
    paths = write_refs(tmp_path)
    row = pq.read_table(paths["daily_path"]).to_pylist()[0]
    row[field] = value
    pq.write_table(pa.Table.from_pylist([row]), paths["daily_path"])
    with pytest.raises(ValueError, match="finite"):
        load(paths)


@pytest.mark.parametrize(
    "fault", ["wrong_date", "duplicate", "missing", "seconds", "timezone", "unknown_code"]
)
def test_opening_reference_must_be_unique_and_date_exact(tmp_path, fault):
    paths = write_refs(tmp_path)
    rows = pq.read_table(paths["five_path"]).to_pylist()
    if fault == "duplicate":
        rows += rows
    elif fault == "wrong_date":
        rows[0]["ts"] = "2026-09-22 09:35:00"
    elif fault == "missing":
        rows[0]["ts"] = f"{DATE} 09:40:00"
    elif fault == "seconds":
        rows[0]["ts"] = f"{DATE} 09:35:01"
    elif fault == "timezone":
        rows[0]["ts"] = f"{DATE} 09:35:00+00:00"
    else:
        rows[0]["stock_code"] = "600000"
    pq.write_table(pa.Table.from_pylist(rows), paths["five_path"])
    with pytest.raises(ValueError):
        load(paths)


@pytest.mark.parametrize("value", [None, "NaN", "-1", True])
def test_opening_volume_is_exact_finite_nonnegative(tmp_path, value):
    paths = write_refs(tmp_path, first=value)
    with pytest.raises(ValueError, match="finite"):
        load(paths)


@pytest.mark.parametrize("role", ["daily_path", "status_path", "five_path"])
def test_reference_symlink_is_never_followed(tmp_path, role):
    paths = write_refs(tmp_path)
    target = paths[role]
    link = tmp_path / "reference-link"
    link.symlink_to(target)
    paths[role] = link
    with pytest.raises((OSError, ValueError)):
        load(paths)


def test_reference_hashes_bind_exact_captured_bytes_not_only_paths(tmp_path):
    paths = write_refs(tmp_path)
    refs = load(paths)
    expected = {item.role: "sha256:" + item.sha256 for item in refs.inputs}
    assert load(paths, expected_sha256=expected).effective_view_sha256 == refs.effective_view_sha256
    write_refs(tmp_path, first=49)
    with pytest.raises(ValueError, match="captured SHA mismatch"):
        load(paths, expected_sha256=expected)
    for incomplete in ({}, {"other": "a" * 64}, None):
        if incomplete is not None:
            with pytest.raises(ValueError, match="roles"):
                load(paths, expected_sha256=incomplete)


def test_selection_identity_changes_and_never_re_reads(tmp_path):
    paths = write_refs(tmp_path)
    refs = load(paths)
    assert refs.select(["000001"]).effective_view_sha256 == refs.effective_view_sha256
    for invalid in ([], ["000001", "000001"], ["600000"]):
        with pytest.raises(ValueError, match="universe"):
            refs.select(invalid)
    evidence = refs.evidence()
    assert evidence["selected_symbols"] == ["000001"]
    assert "not-reuse-authorization" in evidence["lifecycle"]
    assert evidence["disposition"] == "captured_once_in_job"


def test_optional_first_bucket_does_not_add_a_full_48_bucket_gate(tmp_path):
    paths = write_refs(tmp_path)
    # Exactly the applicable existing opening reference, not a new whole-day gate.
    assert len(pq.read_table(paths["five_path"])) == 1
    assert load(paths)["000001"]["first_5m_volume"] == 50
    refs = load_minute_references(
        daily_path=paths["daily_path"], status_path=paths["status_path"], market_date=DATE
    )
    assert refs["000001"]["first_5m_volume"] is None
    with pytest.raises(ValueError, match="opening bucket missing"):
        refs.select(["000001"], require_first_five=True)


def test_paid_replay_without_explicit_reference_stops_before_network_and_output(
    tmp_path, monkeypatch
):
    paths = write_refs(tmp_path / "sealed")
    audit = tmp_path / "audit"
    audit.mkdir()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no source request permitted")

    monkeypatch.setattr(minute_tdx, "_tencent_one_minute_bars", forbidden)
    home = tmp_path / "attempt"
    with pytest.raises(ValueError, match="explicit sealed native-5m"):
        minute_tdx.collect(
            home=home,
            layer="research",
            frequency="1m",
            mode="incr",
            symbols=["000001"],
            max_pages=1,
            sleep_seconds=0,
            market_date=DATE,
            tencent_daily_path=paths["daily_path"],
            tencent_status_path=paths["status_path"],
            paid_audit_dir=audit,
        )
    assert not home.exists()


@pytest.mark.parametrize("first", [50, 49])
def test_audit_and_replay_use_same_explicit_opening_reference(tmp_path, first):
    paths = write_refs(tmp_path / "sealed", first=first)
    refs = load(paths)
    row = refs["000001"]
    raw = json.dumps(payload()).encode()
    audit = tmp_path / "audit"
    (audit / "raw").mkdir(parents=True)
    (audit / "receipts").mkdir()
    capture = audit / "raw/000001.json.gz"
    capture.write_bytes(gzip.compress(raw))
    (audit / "receipts/000001.json").write_text(
        json.dumps(
            {
                "stock_code": "000001",
                "market_date": DATE,
                "http_status": 200,
                "status": "qualified_candidate_not_published",
                "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    )
    args = {
        "symbol": "000001.SZ",
        "market_date": DATE,
        "daily_volume": row["volume"],
        "daily_close": row["close"],
        "first_5m_volume": row["first_5m_volume"],
        "raw_ref": str(capture),
    }
    if first == 49:
        with pytest.raises(ValueError, match="daily/native-5m volume mismatch"):
            paid_minute_source.qualify_raw(raw, **args)
        with pytest.raises(ValueError, match="daily/native-5m volume mismatch"):
            minute_tdx._paid_audit_candidate(audit, "000001", DATE, row)
    else:
        audited, detail = paid_minute_source.qualify_raw(raw, **args)
        replay = minute_tdx._paid_audit_candidate(audit, "000001", DATE, row)
        assert audited == replay
        assert detail["source_semantic_sha256"] == qualify_candidate(
            replay,
            symbol="000001",
            market_date=DATE,
            daily_volume=row["volume"],
            daily_close=row["close"],
            traded=True,
        )


def test_audit_cli_preflights_bad_references_before_key_and_output(tmp_path, monkeypatch):
    paths = write_refs(tmp_path / "sealed")
    row = pq.read_table(paths["five_path"]).to_pylist()[0]
    row["ts"] = "2026-09-22 09:35:00"
    pq.write_table(pa.Table.from_pylist([row]), paths["five_path"])
    output = tmp_path / "quarantine"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit",
            "--market-date",
            DATE,
            "--daily-path",
            str(paths["daily_path"]),
            "--status-path",
            str(paths["status_path"]),
            "--five-path",
            str(paths["five_path"]),
            "--output-dir",
            str(output),
            "--symbols",
            "000001",
        ],
    )

    class NoKey(io.StringIO):
        def readline(self, *_args):
            raise AssertionError("invalid references must not read a key")

    monkeypatch.setattr(sys, "stdin", NoKey())
    with pytest.raises(ValueError, match="wrong-date"):
        paid_minute_source.main()
    assert not output.exists()


def write_audit(root, raw):
    (root / "raw").mkdir(parents=True)
    (root / "receipts").mkdir()
    (root / "raw/000001.json.gz").write_bytes(gzip.compress(raw))
    (root / "receipts/000001.json").write_text(
        json.dumps(
            {
                "stock_code": "000001",
                "market_date": DATE,
                "http_status": 200,
                "status": "qualified_candidate_not_published",
                "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    )


def audit_argv(paths, output, *, resume=False):
    result = [
        "audit",
        "--market-date",
        DATE,
        "--daily-path",
        str(paths["daily_path"]),
        "--status-path",
        str(paths["status_path"]),
        "--five-path",
        str(paths["five_path"]),
        "--output-dir",
        str(output),
        "--symbols",
        "000001",
        "--workers",
        "1",
    ]
    return [*result, "--resume"] if resume else result


def test_audit_cli_records_the_actual_shared_reference_view(tmp_path, monkeypatch):
    import requests

    paths = write_refs(tmp_path / "sealed")
    refs = load(paths)
    output = tmp_path / "audit"
    raw = json.dumps(payload()).encode()

    class Response:
        status_code = 200
        headers = {}
        content = raw

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(sys, "stdin", io.StringIO("synthetic-key-only\n"))
    monkeypatch.setattr(sys, "argv", audit_argv(paths, output))
    monkeypatch.setattr(paid_minute_source.time, "sleep", lambda _seconds: None)
    assert paid_minute_source.main() == 0
    receipt = json.loads((output / "receipts/000001.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    assert receipt["reference_effective_view_sha256"] == refs.effective_view_sha256
    assert summary["reference_inputs"]["effective_view_sha256"] == refs.effective_view_sha256
    assert summary["qualification_reference_scope"] == "this_audit_requests"
    assert summary["production_publication"] is False
    assert "synthetic-key-only" not in json.dumps(receipt)


def test_resume_does_not_claim_old_receipts_were_requalified_under_new_references(
    tmp_path, monkeypatch
):
    paths = write_refs(tmp_path / "sealed", first=49)
    output = tmp_path / "audit"
    write_audit(output, json.dumps(payload()).encode())
    before = (output / "receipts/000001.json").read_bytes()
    monkeypatch.setattr(sys, "argv", audit_argv(paths, output, resume=True))

    class NoKey(io.StringIO):
        def readline(self, *_args):
            raise AssertionError("resume must reuse captured 200 bytes, not fetch")

    monkeypatch.setattr(sys, "stdin", NoKey())
    assert paid_minute_source.main() == 0
    summary = json.loads((output / "summary.json").read_text())
    assert (
        summary["qualification_reference_scope"]
        == "new_requests_only_resumed_receipts_not_requalified"
    )
    assert (output / "receipts/000001.json").read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    ["bad_first", "raw_sha", "provider_unavailable", "old_unqualified_valid", "stale_conflict"],
)
def test_collector_separates_quality_rejection_from_hard_integrity_and_uses_reference(
    tmp_path, monkeypatch, fault
):
    pd = pytest.importorskip("pandas")
    paths = write_refs(tmp_path / "sealed", first=49 if fault == "bad_first" else 50)
    audit = tmp_path / "audit"
    paid_payload = payload()
    if fault == "stale_conflict":
        paid_payload["data"]["items"][0][paid_payload["data"]["fields"].index("high")] = 11.02
    raw = json.dumps(paid_payload).encode()
    write_audit(audit, raw)
    if fault in {"old_unqualified_valid", "stale_conflict"}:
        receipt_path = audit / "receipts/000001.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.update(status="unqualified", reason="synthetic previous-reference rejection")
        receipt_path.write_text(json.dumps(receipt))
    before = (audit / "receipts/000001.json").read_bytes()
    if fault == "raw_sha":
        (audit / "raw/000001.json.gz").write_bytes(gzip.compress(b"changed immutable input"))
    valid, _ = paid_minute_source.qualify_raw(
        json.dumps(payload()).encode(),
        symbol="000001.SZ",
        market_date=DATE,
        daily_volume=2400,
        daily_close=11,
        first_5m_volume=50,
        raw_ref="synthetic-peer-only.json.gz",
    )
    frame = pd.DataFrame(valid.rows)
    for field in ("open", "high", "low", "close", "volume"):
        frame[field] = frame[field].astype(float)
    frame["ts"] = pd.to_datetime(frame["ts"])
    frame["amount"] = pd.Series([pd.NA] * len(frame), dtype="Float64")

    def peer(*_args, **_kwargs):
        if fault in {"provider_unavailable", "old_unqualified_valid"}:
            raise RuntimeError("synthetic transport failure")
        return frame, {
            "ok": True,
            "provider": "tencent-mkline-validated",
            "source_raw_sha256": "b" * 64,
            "source_raw_path": "synthetic-peer-only.json.gz",
        }

    monkeypatch.setattr(minute_tdx, "_tencent_one_minute_bars", peer)
    monkeypatch.setattr(minute_tdx.time, "sleep", lambda _seconds: None)
    home = tmp_path / "attempt"
    result = minute_tdx.collect(
        home=home,
        layer="research",
        frequency="1m",
        mode="incr",
        symbols=["000001"],
        max_pages=1,
        sleep_seconds=0,
        market_date=DATE,
        tencent_daily_path=paths["daily_path"],
        tencent_status_path=paths["status_path"],
        paid_audit_dir=audit,
        sealed_five_path=paths["five_path"],
    )
    checkpoint = json.loads((home / "checkpoint.json").read_text())
    target = home / "000001_1m.parquet"
    if fault in {"raw_sha", "stale_conflict"}:
        assert result == 1
        assert not target.exists()
        assert ("SHA-256" if fault == "raw_sha" else "cross-source minute conflict") in json.dumps(
            checkpoint
        )
    else:
        assert result == 0
        assert target.exists()
        detail = checkpoint["symbols"]["000001"]
        if fault == "bad_first":
            assert detail["provider"] == "tencent-mkline-validated"
            assert "native-5m volume mismatch" in json.dumps(detail["source_rejected"])
        else:
            assert detail["provider"] == "paid-tushare-style-proxy-stk_mins"
        assert pq.read_table(target)["volume"].to_pylist() == [10] * 240
    assert (audit / "receipts/000001.json").read_bytes() == before
    assert not (home / "READY").exists()
