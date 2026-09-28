"""Synthetic full-denominator offline evidence; no provider or publishing rights."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

from quanthus.providers import minute_offline_ledger as ledger
from quanthus.providers.minute_references import load_minute_references
from quanthus.providers.tencent_m1_normalize import expected_times
from tests.unit.test_paid_minute_source import DATE, FIELDS, payload

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def fixture(root, *, codes=("000001",), volumes=None, suspended=()):
    daily, statuses, five = [], [], []
    for code in codes:
        volume = (volumes or {}).get(code, 0 if code in suspended else 2500)
        daily.append(dict(stock_code=code, market_date=DATE, volume=volume, close=11))
        statuses.append(
            dict(stock_code=code, market_date=DATE, tradestatus="0" if code in suspended else "1")
        )
        if code not in suspended:
            five.append(dict(stock_code=code, ts=f"{DATE} 09:35:00", volume=150))
    paths = dict(
        daily_path=root / "daily.parquet",
        status_path=root / "status.parquet",
        five_path=root / "five.parquet",
    )
    for key, rows in (("daily_path", daily), ("status_path", statuses), ("five_path", five)):
        if rows:
            pq.write_table(pa.Table.from_pylist(rows), paths[key])
        else:
            pq.write_table(
                pa.table(
                    {
                        "stock_code": pa.array([], type=pa.string()),
                        "ts": pa.array([], type=pa.string()),
                        "volume": pa.array([], type=pa.int64()),
                    }
                ),
                paths[key],
            )
    refs = load_minute_references(**paths, market_date=DATE, require_first_five=True)
    tencent, paid = root / "tencent", root / "paid"
    for path in (tencent / "provider_raw/tencent-m1", paid / "raw", paid / "receipts"):
        path.mkdir(parents=True)
    (tencent / "checkpoint.json").write_text(json.dumps({"symbols": {}}))
    return refs, tencent, paid


def tencent_payload(code="000001", *, suspended=False):
    source = [[stamp, "11", "11", "11", "11", "0.1", {}] for stamp in expected_times(DATE)]
    source.append([DATE.replace("-", "") + "0930", "11", "11", "11", "11", "1", {}])
    return {
        "code": 0,
        "data": {
            ("sh" if code.startswith("6") else "sz") + code: {"m1": [] if suspended else source}
        },
    }


def put_tencent(root, value=None, *, code="000001"):
    raw = json.dumps(value or tencent_payload(code)).encode()
    path = root / "provider_raw/tencent-m1" / f"{code}.json.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    checkpoint = json.loads((root / "checkpoint.json").read_text())
    checkpoint["symbols"][code] = {
        "market_date": DATE,
        "provider": ledger.PRIORITY[0],
        "source_raw_path": str(path),
        "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "done",
    }
    (root / "checkpoint.json").write_text(json.dumps(checkpoint))
    return path


def put_paid(root, value=None, *, code="000001", old_label="unqualified", http=200):
    value = value or payload()
    for row in value["data"]["items"]:
        row[0] = code + (".SH" if code.startswith("6") else ".SZ")
    raw = json.dumps(value).encode()
    path = root / "raw" / f"{code}.json.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    receipt = root / "receipts" / f"{code}.json"
    receipt.write_text(
        json.dumps(
            {
                "stock_code": code,
                "market_date": DATE,
                "http_status": http,
                "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
                "status": old_label,
            }
        )
    )
    return path, receipt


def run(root, refs, tencent, paid, *, name="output", **kwargs):
    output = root / name
    summary = ledger.build_ledger(
        refs=refs,
        tencent_root=tencent,
        paid_root=paid,
        output=output,
        node_label="synthetic-node",
        **kwargs,
    )
    records = [json.loads(line) for line in (output / "records.jsonl").read_text().splitlines()]
    return summary, records, output


def test_current_raw_and_shared_gate_not_old_receipt_tags(tmp_path, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *_a, **_k: pytest.fail("no network allowed"))
    refs, ts, paid = fixture(tmp_path)
    put_tencent(ts)
    _, receipt = put_paid(paid, old_label="unqualified")
    original = receipt.read_bytes()
    summary, rows, output = run(tmp_path, refs, ts, paid)
    assert summary["status"] == "offline_evaluation_completed_not_published"
    assert rows[0]["disposition"] == "qualified_whole_security_candidate"
    assert rows[0]["qualified_providers"] == list(ledger.PRIORITY)
    assert rows[0]["selected_provider"] == ledger.PRIORITY[0]
    assert receipt.read_bytes() == original
    assert all(source["raw_sha256"] for source in rows[0]["sources"])
    assert (
        summary["records_sha256"]
        == hashlib.sha256((output / "records.jsonl").read_bytes()).hexdigest()
    )
    assert not summary["publication_authorized"] and not summary["recovery_authorized"]
    assert not list(output.rglob("*.parquet")) and not list(output.rglob("READY"))


@pytest.mark.parametrize("mode", ["value", "tail", "material"])
def test_bad_paid_candidate_retains_raw_identity_and_cannot_hide_good_peer(tmp_path, mode):
    refs, ts, paid = fixture(tmp_path)
    put_tencent(ts)
    value = payload()
    row = next(row for row in value["data"]["items"] if row[1].endswith("15:00:00"))
    if mode == "material":
        row[FIELDS.index("vol")] = None
    else:
        row[FIELDS.index("close")] = 11.01 if mode == "value" else 11.000000000000002
        row[FIELDS.index("high")] = row[FIELDS.index("close")]
    put_paid(paid, value, old_label="qualified_candidate_not_published")
    _, rows, _ = run(tmp_path, refs, ts, paid)
    record = rows[0]
    assert record["disposition"] == "qualified_whole_security_candidate"
    assert record["selected_provider"] == ledger.PRIORITY[0]
    rejected = record["sources"][1]
    assert rejected["status"] == "unqualified_candidate"
    assert rejected["raw_sha256"] and rejected["receipt_sha256"]
    assert rejected["reason_codes"]


def test_dual_qualified_true_minute_difference_still_blocks(tmp_path):
    refs, ts, paid = fixture(tmp_path)
    put_tencent(ts)
    value = payload()
    value["data"]["items"][10][FIELDS.index("high")] = 11.01
    put_paid(paid, value)
    _, rows, _ = run(tmp_path, refs, ts, paid)
    assert rows[0]["disposition"] == "blocked_cross_source_conflict"
    assert rows[0]["selected_provider"] is None
    assert rows[0]["qualified_providers"] == list(ledger.PRIORITY)


@pytest.mark.parametrize("fault", ["hash", "date", "path", "gzip", "symlink"])
def test_capture_integrity_fault_blocks_even_a_qualified_peer(tmp_path, fault):
    refs, ts, paid = fixture(tmp_path)
    put_tencent(ts)
    raw, receipt = put_paid(paid)
    value = json.loads(receipt.read_text())
    if fault == "hash":
        value["source_raw_sha256"] = "0" * 64
    elif fault == "date":
        value["market_date"] = "2026-09-22"
    elif fault == "path":
        raw.unlink()
    elif fault == "gzip":
        raw.write_bytes(b"not gzip")
    else:
        backup = paid / "original.gz"
        raw.rename(backup)
        raw.symlink_to(backup)
    receipt.write_text(json.dumps(value))
    _, rows, _ = run(tmp_path, refs, ts, paid)
    assert rows[0]["disposition"] == "blocked_capture_integrity"
    assert rows[0]["selected_provider"] is None
    assert rows[0]["sources"][1]["status"] == "integrity_fault"


@pytest.mark.parametrize("missing", [False, True])
def test_actual_zero_minute_vs_inferred_minute_is_explicit(tmp_path, missing):
    refs, ts, paid = fixture(tmp_path, volumes={"000001": 2490})
    value = tencent_payload()
    value["data"]["sz000001"]["m1"][60][5] = "0"
    if missing:
        del value["data"]["sz000001"]["m1"][60]
    put_tencent(ts, value)
    _, rows, _ = run(tmp_path, refs, ts, paid)
    source = rows[0]["sources"][0]
    assert source["status"] == (
        "unqualified_candidate" if missing else "qualified_candidate_not_published"
    )
    if missing:
        assert source["reason_codes"] == ["INFERRED_SOURCE_MINUTES_FORBIDDEN"]
        assert len(source["interpretation"]["inferred_zero_volume_minutes"]) == 1


def test_full_denominator_includes_missing_pending_and_suspended(tmp_path):
    refs, ts, paid = fixture(tmp_path, codes=("000001", "000002", "000003"), suspended=("000003",))
    put_tencent(ts, tencent_payload("000003", suspended=True), code="000003")
    put_paid(paid, code="000002", http=202, old_label="source_pending")
    summary, rows, _ = run(tmp_path, refs, ts, paid)
    assert summary["universe_count"] == summary["processed"] == len(rows) == 3
    assert sum(summary["dispositions"].values()) == 3
    for counts in summary["source_counts"].values():
        assert sum(counts.values()) == 3
    assert rows[0]["disposition"] == "pending_no_qualified_source"
    assert rows[1]["sources"][1]["status"] == "acquisition_pending"
    assert rows[2]["disposition"] == "qualified_whole_security_candidate"
    assert rows[2]["sources"][0]["normalized_rows"] == 0
    assert rows[2]["sources"][1]["status"] == "not_requested_suspended"


def test_identical_capture_reproduces_records_without_reacquiring(tmp_path):
    refs, ts, paid = fixture(tmp_path)
    put_tencent(ts)
    put_paid(paid)
    first, _, _ = run(tmp_path, refs, ts, paid, name="first")
    second, _, _ = run(tmp_path, refs, ts, paid, name="second")
    assert first["records_sha256"] == second["records_sha256"]
    assert first["identity_sha256"] == second["identity_sha256"]


@pytest.mark.parametrize("fault", ["duplicate", "unknown", "malformed"])
def test_checkpoint_identity_fault_has_no_completion_or_output(tmp_path, fault):
    refs, ts, paid = fixture(tmp_path)
    path = ts / "checkpoint.json"
    path.write_text(
        {
            "duplicate": '{"symbols":{},"symbols":{}}',
            "unknown": '{"symbols":{"999999":{}}}',
            "malformed": '{"symbols":{"000001":[]}}',
        }[fault]
    )
    with pytest.raises(ledger.EvidenceFault):
        run(tmp_path, refs, ts, paid)
    assert not (tmp_path / "output").exists()


def test_budget_exhaustion_leaves_partial_not_completed(tmp_path, monkeypatch):
    refs, ts, paid = fixture(tmp_path)
    values = iter((0, 901))
    monkeypatch.setattr(ledger.time, "monotonic", lambda: next(values))
    with pytest.raises(TimeoutError, match="no complete summary"):
        run(tmp_path, refs, ts, paid)
    assert (tmp_path / "output/job.json").exists()
    assert not (tmp_path / "output/summary.json").exists()


def test_unknown_engineering_fault_propagates_without_complete_summary(tmp_path, monkeypatch):
    refs, ts, paid = fixture(tmp_path)

    def broken(*_a, **_k):
        raise RuntimeError("synthetic engineering fault")

    monkeypatch.setattr(ledger, "_evaluate", broken)
    with pytest.raises(RuntimeError):
        run(tmp_path, refs, ts, paid)
    assert not (tmp_path / "output/summary.json").exists()


@pytest.mark.parametrize("target", ["existing", "inside_input", "ancestor"])
def test_output_cannot_overwrite_or_nest_in_inputs(tmp_path, target):
    refs, ts, paid = fixture(tmp_path)
    output = {
        "existing": tmp_path / "existing",
        "inside_input": paid / "new",
        "ancestor": tmp_path,
    }[target]
    if target == "existing":
        output.mkdir()
        (output / "keep").write_text("preserve")
    with pytest.raises((ledger.EvidenceFault, FileExistsError)):
        ledger.build_ledger(
            refs=refs, tencent_root=ts, paid_root=paid, output=output, node_label="test"
        )
    if target == "existing":
        assert (output / "keep").read_text() == "preserve"


@pytest.mark.parametrize("budget", [0, -1, 1801, float("inf"), float("nan")])
def test_invalid_unbounded_budget_rejected_before_output(tmp_path, budget):
    refs, ts, paid = fixture(tmp_path)
    with pytest.raises(ValueError, match="finite"):
        run(tmp_path, refs, ts, paid, budget_seconds=budget)
    assert not (tmp_path / "output").exists()


def test_oversized_prefix_hash_never_claims_full_raw_identity(tmp_path, monkeypatch):
    refs, ts, paid = fixture(tmp_path)
    put_tencent(ts)
    monkeypatch.setattr(ledger, "MAX_RAW", 10)
    _, rows, _ = run(tmp_path, refs, ts, paid)
    source = rows[0]["sources"][0]
    assert source["status"] == "integrity_fault" and source["capture_truncated"]
    assert "raw_sha256" not in source and source["decompressed_prefix_sha256"]


@pytest.mark.parametrize("dirty", [False, True])
def test_cli_requires_verified_handoff_and_clean_revision(tmp_path, monkeypatch, dirty):
    from datetime import date

    from quanthus.pipelines import lane_runner

    refs, ts, paid = fixture(tmp_path)
    put_tencent(ts)
    put_paid(paid)

    def handoff(*, paths, market_date, available_at, require_five):
        assert market_date == date.fromisoformat(DATE) and available_at.tzinfo
        assert require_five and paths.shadow_root == (tmp_path / "shadow").resolve()
        return {
            item.role: (
                Path(item.path),
                {"artifact_sha256": "sha256:" + item.sha256, "release_id": "synthetic-only"},
            )
            for item in refs.inputs
        }

    def git(args, **kwargs):
        assert args[0] == "git"
        return "a" * 40 if kwargs.get("text") else b" M synthetic.py" if dirty else b""

    monkeypatch.setattr(lane_runner, "_sealed_minute_reference_artifacts", handoff)
    monkeypatch.setattr(ledger.subprocess, "check_output", git)
    output = tmp_path / "cli-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "minute_offline_ledger",
            "--home",
            str(tmp_path / "home"),
            "--shadow-root",
            str(tmp_path / "shadow"),
            "--market-date",
            DATE,
            "--tencent-root",
            str(ts),
            "--paid-root",
            str(paid),
            "--output",
            str(output),
            "--node-label",
            "synthetic-node",
        ],
    )
    if dirty:
        with pytest.raises(ledger.EvidenceFault, match="clean versioned"):
            ledger.main()
        assert not output.exists()
    else:
        assert ledger.main() == 0
        identity = json.loads((output / "job.json").read_text())["identity"]
        assert identity["provenance"]["code_revision"] == "a" * 40
        assert set(identity["provenance"]["release_handoff"]) == {
            "sealed_daily",
            "sealed_status",
            "sealed_native_5m",
        }


def test_4992_symbol_denominator_cannot_hide_unrequested_tail(tmp_path):
    codes = tuple(f"{n:06}" for n in range(1, 4993))
    refs, ts, paid = fixture(tmp_path, codes=codes)
    summary, rows, _ = run(tmp_path, refs, ts, paid)
    assert summary["processed"] == summary["universe_count"] == 4992
    assert rows[-1]["stock_code"] == "004992"
    assert summary["dispositions"] == {"pending_no_qualified_source": 4992}
    assert all(counts == {"capture_missing": 4992} for counts in summary["source_counts"].values())
