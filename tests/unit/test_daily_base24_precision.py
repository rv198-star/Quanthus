from __future__ import annotations

import base64
import gzip
import hashlib
import json
import shutil
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.contracts import verify_ods_release
from quanthus.pipelines.securities_warehouse import daily_base24_precision as repair

DAY = "2026-10-15"
OLD = "2026-10-15T19:00:00+08:00"
NEW = "2026-10-20T13:00:00+00:00"
REV = "b" * 40


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


def fixture(tmp_path):
    before = dict(
        stock_code="600000",
        security_id="600000",
        date=DAY,
        market_date=DAY,
        open="12.3400000000",
        high="13.0000",
        low="12.00",
        close="12.5000",
        volume="10000",
        amount="123400.00000",
        adj_mode="none",
        source="tencent.daily_fallback",
        acquired_at=OLD,
        built_at=OLD,
    )
    request = dict(
        provider="baostock",
        api="query_history_k_data_plus",
        code="sh.600000",
        fields=repair.FIELDS,
        frequency="d",
        adjustflag="3",
        start_date=DAY,
        end_date="2026-10-19",
    )
    row = [
        DAY,
        "sh.600000",
        "12.34",
        "13",
        "12",
        "12.5",
        "10001",
        "123400",
        "1",
        "2",
        "1",
        "0",
        "3",
        "4",
        "5",
        "6",
    ]
    raw = encode(dict(fields=repair.FIELDS.split(","), rows=[row], error_code="0"))
    envelope = dict(
        policy_id="p10-acquisition-prevention-v1",
        status="0",
        request=request,
        raw_base64=base64.b64encode(raw).decode(),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_bytes=len(raw),
        request_started_at=NEW,
        response_completed_at=NEW,
        warehouse_observed_at=NEW,
        provider_availability_at=None,
        publication_at=None,
    )
    source = tmp_path / "source"
    (source / "artifacts").mkdir(parents=True)
    path = source / "artifacts/bars.parquet"
    pq.write_table(
        pa.Table.from_pylist([before, {**before, "stock_code": "600001", "security_id": "600001"}]),
        path,
    )
    artifact = dict(
        name="daily_ohlcv_none_delta",
        relative_path="artifacts/bars.parquet",
        sha256=repair.file_sha(path),
        size_bytes=path.stat().st_size,
        source_revision="synthetic-prior@" + "a" * 40,
        knowledge_cutoff=OLD,
    )
    manifest = dict(
        schema_version=1,
        status="ready",
        release_id="hermes-market-daily-core-synthetic",
        schedule="daily",
        market_date=DAY,
        created_at=OLD,
        producer="synthetic",
        artifacts=[artifact],
    )
    (source / "release.json").write_bytes(encode(manifest))
    (source / "READY").write_text(repair.file_sha(source / "release.json") + "\n")
    cap = tmp_path / "capture.json.gz"
    cap.write_bytes(gzip.compress(encode(envelope), mtime=0))
    return before, envelope, source, cap


def test_volume_revision_preserves_price_amount_precision_and_actual_clocks(tmp_path):
    before, envelope, _, cap = fixture(tmp_path)
    after, proof = repair.volume_row(before, cap.read_bytes(), day=DAY, code="600000", revision=REV)
    for key in ["open", "high", "low", "close", "amount", "date", "market_date"]:
        assert after[key] == before[key]
    assert after["volume"] == "10001" and after["acquired_at"] == NEW
    assert proof["provider_availability_at"] is None
    assert proof["original_daily_observed_at"] == OLD
    assert proof["new_volume_observed_at"] == NEW
    assert set(proof["changed_fields"]) <= {"volume", "source", "acquired_at", "built_at"}
    assert envelope["publication_at"] is None


@pytest.mark.parametrize("defect", ["hash", "clock", "frequency", "date", "key", "source"])
def test_invalid_capture_or_scope_is_rejected(tmp_path, defect):
    before, envelope, _, _ = fixture(tmp_path)
    if defect == "hash":
        envelope["raw_sha256"] = "0" * 64
    elif defect == "clock":
        envelope["warehouse_observed_at"] = "2026-10-14T00:00:00+00:00"
    elif defect == "frequency":
        envelope["request"]["frequency"] = "1"
    elif defect == "date":
        envelope["request"]["start_date"] = "2026-10-16"
    elif defect == "key":
        before["security_id"] = "600001"
    else:
        before["source"] = "unknown"
    with pytest.raises(ValueError):
        repair.volume_row(
            before, gzip.compress(encode(envelope)), day=DAY, code="600000", revision=REV
        )


def test_build_preserves_prior_rows_source_files_and_relocatable_evidence(tmp_path):
    _, _, source, cap = fixture(tmp_path)
    frozen = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()} | {cap: cap.read_bytes()}
    output = tmp_path / "revision"
    result = repair.build_market_revision(
        source=source,
        expected_source_sha256=repair.file_sha(source / "release.json"),
        captures={"600000": cap},
        expected_capture_hashes={"600000": repair.file_sha(cap)},
        output=output,
        revision=REV,
    )
    rows = pq.ParquetFile(output / "artifacts/bars.parquet").read().to_pylist()
    old = pq.ParquetFile(source / "artifacts/bars.parquet").read().to_pylist()
    assert rows[1] == old[1] and rows[0]["volume"] == "10001"
    assert (
        pq.ParquetFile(output / "artifacts/precision-revision/prior-daily-bars.parquet")
        .read()
        .to_pylist()
        == old
    )
    assert result["selected"] == result["unchanged_rows"] == 1
    assert all(p.read_bytes() == raw for p, raw in frozen.items())
    moved = tmp_path / "moved"
    shutil.copytree(output, moved)
    shutil.rmtree(source)
    cap.unlink()
    for target in [moved, tmp_path / "moved-again"]:
        if target != moved:
            shutil.copytree(moved, target)
            shutil.rmtree(moved)
        code = "from pathlib import Path; "
        code += "from quanthus.pipelines.securities_warehouse.daily_base24_precision "
        code += "import verify_market_revision; verify_market_revision(Path("
        code += repr(str(target)) + "))"
        subprocess.run([sys.executable, "-c", code], check=True)
        repair.verify_market_revision(target)


def test_capture_mutation_fails_before_output_and_sealed_output_is_not_overwritten(tmp_path):
    _, _, source, cap = fixture(tmp_path)
    kwargs = dict(
        source=source,
        expected_source_sha256=repair.file_sha(source / "release.json"),
        captures={"600000": cap},
        expected_capture_hashes={"600000": "sha256:" + "0" * 64},
        output=tmp_path / "revision",
        revision=REV,
    )
    with pytest.raises(ValueError, match="CAPTURE_CHANGED"):
        repair.build_market_revision(**kwargs)
    assert not kwargs["output"].exists()
    kwargs["expected_capture_hashes"]["600000"] = repair.file_sha(cap)
    repair.build_market_revision(**kwargs)
    before = (kwargs["output"] / "READY").read_bytes()
    with pytest.raises(ValueError, match="NEW_BOUNDED_OUTPUT"):
        repair.build_market_revision(**kwargs)
    assert (kwargs["output"] / "READY").read_bytes() == before


def test_no_ready_is_copied_before_rewrite_failure(tmp_path, monkeypatch):
    _, _, source, cap = fixture(tmp_path)
    output = tmp_path / "revision"

    def fail(*args, **kwargs):
        assert not (output / "READY").exists()
        raise OSError("disposable synthetic write error")

    monkeypatch.setattr(pq, "write_table", fail)
    with pytest.raises(OSError, match="synthetic write error"):
        repair.build_market_revision(
            source=source,
            expected_source_sha256=repair.file_sha(source / "release.json"),
            captures={"600000": cap},
            expected_capture_hashes={"600000": repair.file_sha(cap)},
            output=output,
            revision=REV,
        )
    assert output.exists() and not (output / "READY").exists()


@pytest.mark.parametrize("defect", ["volume", "other_price", "unknown_row", "capture"])
def test_rehashed_revision_cannot_hide_unapproved_change(tmp_path, defect):
    _, _, source, cap = fixture(tmp_path)
    output = tmp_path / "revision"
    repair.build_market_revision(
        source=source,
        expected_source_sha256=repair.file_sha(source / "release.json"),
        captures={"600000": cap},
        expected_capture_hashes={"600000": repair.file_sha(cap)},
        output=output,
        revision=REV,
    )
    bars = output / "artifacts/bars.parquet"
    rows = pq.ParquetFile(bars).read().to_pylist()
    if defect == "capture":
        changed = output / "artifacts/precision-revision/600000.json.gz"
        envelope = json.loads(gzip.decompress(changed.read_bytes()))
        envelope["warehouse_observed_at"] = "2026-10-14T00:00:00+00:00"
        changed.write_bytes(gzip.compress(encode(envelope)))
    else:
        i, field = (
            (1, "amount")
            if defect == "unknown_row"
            else (0, "volume" if defect == "volume" else "close")
        )
        rows[i][field] = "99999"
        pq.write_table(pa.Table.from_pylist(rows), bars)
        changed = bars
    manifest = json.loads((output / "release.json").read_bytes())
    artifact = next(
        a
        for a in manifest["artifacts"]
        if a["relative_path"] == changed.relative_to(output).as_posix()
    )
    artifact.update(sha256=repair.file_sha(changed), size_bytes=changed.stat().st_size)
    (output / "release.json").write_bytes(encode(manifest))
    (output / "READY").write_text(repair.file_sha(output / "release.json") + "\n")
    verify_ods_release(output)  # All packaging hashes are now valid.
    with pytest.raises(ValueError, match="PRECISION_"):
        repair.verify_market_revision(output)
