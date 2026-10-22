from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_native_rebind as binding
from quanthus.pipelines.securities_warehouse import daily_base24_precision as precision
from quanthus.pipelines.securities_warehouse import native5m_daily_source as native
from tests.unit.test_daily_base24_precision import DAY, REV, encode, fixture


def inputs(tmp_path):
    _, _, source, cap = fixture(tmp_path)
    market = tmp_path / "market-revision"
    result = precision.build_market_revision(
        source=source,
        expected_source_sha256=precision.file_sha(source / "release.json"),
        captures={"600000": cap},
        expected_capture_hashes={"600000": precision.file_sha(cap)},
        output=market,
        revision=REV,
    )
    securities = {
        "600000": dict(tradestatus="1", close="12.5000", volume="10001"),
        "600001": dict(tradestatus="1", close="12.5000", volume="10000"),
    }
    ref = dict(
        kind="p10_complete_daily_job_reference_v3",
        market_date=DAY,
        policy_id="p10-trading-day-complete-daily-v1",
        population=2,
        source_ods_manifest_sha256=result["manifest_sha256"],
        event_ods_manifest_sha256="sha256:" + "e" * 64,
        universe_policy_id="p10-acquisition-prevention-v1",
        universe_digest=native._digest(sorted(securities)),
        securities=securities,
    )
    ref["reference_digest"] = native._digest(ref)
    reference = tmp_path / "reference.json"
    reference.write_bytes(native.canonical(ref) + b"\n")
    original = {}
    daily = {}
    for code, expected in securities.items():
        rows = []
        for i, label in enumerate(native.m5_grid(DAY)):
            volume = "200" if i < 47 else str(int(expected["volume"]) - 47 * 200)
            rows.append(
                [
                    DAY,
                    label + "00000",
                    "sh." + code,
                    "12.50",
                    "12.50",
                    "12.50",
                    "12.50",
                    volume,
                    "1000.0000000000",
                ]
            )
        raw = encode(
            dict(
                provider=native.PROVIDER,
                frequency="5",
                adjustflag="3",
                code="sh." + code,
                market_date=DAY,
                fields=native.FIELDS.split(","),
                rows=rows,
            )
        )
        p = tmp_path / (code + ".json.gz")
        p.write_bytes(gzip.compress(raw, mtime=0))
        old = dict(
            stock_code=code,
            market_date=DAY,
            raw_sha256=precision._sha(raw),
            source_acquired_at_or_after="2026-10-16T00:00:00+00:00",
            reference_ods_manifest_sha256="sha256:" + "a" * 64,
        )
        original[code] = dict(
            path=str(p),
            compressed_sha256=precision.file_sha(p),
            raw_sha256=precision._sha(raw),
            observed_at_or_after=old["source_acquired_at_or_after"],
            evidence=old,
            origin_kind="prior_quality_status",
            origin_manifest_sha256="sha256:" + "b" * 64,
        )
        daily[code] = dict(
            is_tradable=True, close=Decimal(expected["close"]), volume=Decimal(expected["volume"])
        )
    return market, reference, original, daily, ref


def verify(root, daily, ref):
    return native.verify_native5m_source(
        root,
        precision.file_sha(root / "manifest.json"),
        day=DAY,
        daily=daily,
        daily_sources=dict(
            market_daily_core=ref["source_ods_manifest_sha256"],
            calendar_daily_events=ref["event_ods_manifest_sha256"],
        ),
    )


def reseal(root):
    manifest = json.loads((root / "manifest.json").read_bytes())
    # Include nested market manifests/READY too.
    files = [
        dict(
            path=p.relative_to(root).as_posix(),
            bytes=p.stat().st_size,
            sha256=precision.file_sha(p),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file() and p not in {root / "manifest.json", root / "READY"}
    ]
    manifest.update(files=files, content_digest=native._digest(files))
    (root / "manifest.json").write_bytes(native.canonical(manifest) + b"\n")
    (root / "READY").write_text(precision.file_sha(root / "manifest.json") + "\n")


def test_rebinding_uses_original_bytes_and_preserves_knowledge_after_two_moves(tmp_path):
    market, reference, original, daily, ref = inputs(tmp_path)
    source_bytes = {
        p: p.read_bytes() for p in [reference, *[Path(x["path"]) for x in original.values()]]
    }
    output = tmp_path / "new-native"
    result = binding.build(
        market=market, reference=reference, originals=original, output=output, revision=REV
    )
    assert result["grades"] == {"R0": 2} and result["rows_per_grade"] == {"R0": 96}
    assert verify(output, daily, ref)["rows"] == 96
    assert all(p.read_bytes() == raw for p, raw in source_bytes.items())
    for p in source_bytes:
        p.unlink()
    shutil.rmtree(market)
    for index in range(2):
        target = tmp_path / ("move-" + str(index))
        shutil.copytree(output, target)
        shutil.rmtree(output)
        code = "from pathlib import Path; import json; from decimal import Decimal; "
        code += "from quanthus.pipelines.securities_warehouse.native5m_daily_source "
        code += "import verify_native5m_source as v; "
        code += (
            "v(Path("
            + repr(str(target))
            + "),"
            + repr(precision.file_sha(target / "manifest.json"))
        )
        code += ",day=" + repr(DAY) + ",daily=" + repr(daily) + ",daily_sources="
        code += (
            repr(
                dict(
                    market_daily_core=ref["source_ods_manifest_sha256"],
                    calendar_daily_events=ref["event_ods_manifest_sha256"],
                )
            )
            + ")"
        )
        subprocess.run([sys.executable, "-c", code], check=True)
        output = target


@pytest.mark.parametrize("defect", ["clock", "receipt_hash", "market_reference", "raw"])
def test_rehashed_native_cannot_hide_changed_provenance_or_dependency(tmp_path, defect):
    market, reference, original, daily, ref = inputs(tmp_path)
    output = tmp_path / "native"
    binding.build(
        market=market, reference=reference, originals=original, output=output, revision=REV
    )
    p = output / "evidence/original-bindings.json"
    value = json.loads(p.read_bytes())
    if defect == "clock":
        value["rows"]["600000"]["observed_at_or_after"] = "2026-10-20T00:00:00+00:00"
    elif defect == "receipt_hash":
        value["rows"]["600000"]["evidence"]["raw_sha256"] = "0" * 64
    elif defect == "market_reference":
        p = output / "daily-reference.json"
        value = json.loads(p.read_bytes())
        value["source_ods_manifest_sha256"] = "sha256:" + "f" * 64
    else:
        p = output / "ods/raw/600000.json.gz"
        p.write_bytes(b"broken")
    if defect != "raw":
        p.write_bytes(encode(value))
    reseal(output)
    with pytest.raises((ValueError, OSError)):
        verify(output, daily, ref)


def test_share_mismatch_stays_r3_without_tolerance_and_no_input_changes(tmp_path):
    _, _, original, _, _ = inputs(tmp_path)
    p = Path(original["600000"]["path"])
    before = p.read_bytes()
    quality, bars = binding.qualify(
        gzip.decompress(before), "600000", DAY, dict(tradestatus="1", volume="10000", close="12.50")
    )
    assert quality["quality_grade"] == "R3" and not bars
    assert quality["quality_reason"] == "minute_daily_volume_mismatch"
    assert p.read_bytes() == before
