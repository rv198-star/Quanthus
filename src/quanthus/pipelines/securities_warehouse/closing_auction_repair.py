"""Create a new source package with bounded, observed closing-auction supplements.

Never edits the original capture or package. The core/Daily replay is a separate gate.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .base22 import canonical, file_sha
from .base22_sources import disjoint_output, physical_path
from .closing_auction_source import CONTRACT, PHASE, PROVIDER, instant, observed, replay, sha
from .native5m_daily_source import _digest, minute_schema


def write(path: Path, value: object) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def build(attempt: Path, evidence: Path, output: Path, revision: str) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if output.exists() or len(revision) != 40:
        raise ValueError("AUCTION_NEW_OUTPUT_AND_REVISION_REQUIRED")
    attempt, evidence = physical_path(attempt), physical_path(evidence)
    disjoint_output(output, [attempt, evidence])
    receipt = json.loads((attempt / "receipt.json").read_bytes())
    day = receipt["market_date"]
    if receipt["status"] != "CORE_READY_5M_HELD_SOURCE_QUALITY":
        raise ValueError("AUCTION_HELD_PREDECESSOR_REQUIRED")
    source = physical_path(Path(receipt["native_source"]))
    disjoint_output(output, [source])
    manifest = json.loads((source / "manifest.json").read_bytes())
    if file_sha(source / "manifest.json") != receipt["native_manifest_sha256"] or (
        (source / "READY").read_text().strip() != receipt["native_manifest_sha256"]
    ):
        raise ValueError("AUCTION_PREDECESSOR_CHANGED")
    if manifest.get("processing_revision") is not None:
        raise ValueError("AUCTION_REBIND_PREDECESSOR_NOT_SUPPORTED")
    files = manifest["files"]
    if manifest["content_digest"] != _digest(files):
        raise ValueError("AUCTION_PREDECESSOR_DIGEST_INVALID")
    declared = {"manifest.json", "READY"}
    for member in files:
        rel = member["path"]
        if Path(rel).is_absolute() or ".." in Path(rel).parts or rel in declared:
            raise ValueError("AUCTION_PREDECESSOR_PATH_INVALID")
        declared.add(rel)
        p = source / rel
        if (
            p.is_symlink()
            or p.stat().st_nlink != 1
            or p.stat().st_size != member["bytes"]
            or file_sha(p) != member["sha256"]
        ):
            raise ValueError("AUCTION_PREDECESSOR_MEMBER_CHANGED")
    if {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()} != declared:
        raise ValueError("AUCTION_PREDECESSOR_INVENTORY_DIFFERS")
    ref = json.loads((source / "daily-reference.json").read_bytes())
    statuses = [
        json.loads(line) for line in (source / "quality-status.jsonl").read_text().splitlines()
    ]
    held = {s["stock_code"]: s for s in statuses if s["quality_grade"] == "R3"}
    cap = json.loads((evidence / "CAPTURE.json").read_bytes())
    if (
        cap["market_date"] != day
        or not held
        or set(held) != {p.name[len("tick-count-") : -4] for p in evidence.glob("tick-count-*.raw")}
    ):
        raise ValueError("AUCTION_REPAIR_SET_DIFFERS")
    repairs = {}
    proofs = []
    for code, status in held.items():
        if status["quality_reason"] not in {
            "minute_daily_volume_mismatch",
            "missing_or_wrong_terminal_close",
        }:
            raise ValueError("AUCTION_REPAIR_REASON_OUTSIDE_SCOPE")
        originals = list((attempt / "capture-5m").glob("shard-*/raw/" + code + ".json.gz"))
        if len(originals) != 1:
            raise ValueError("AUCTION_BASE_CAPTURE_NOT_UNIQUE")
        raw = gzip.decompress(originals[0].read_bytes())
        if sha(raw) != status["raw_sha256"]:
            raise ValueError("AUCTION_BASE_CAPTURE_CHANGED")
        entries = {}
        names = {"count": "tick-count-" + code + ".raw", "ticks": "tick-last-" + code + ".raw"}
        if code.startswith("688"):
            names["minute"] = "tencent-minute-" + code + ".json"
        for kind, name in names.items():
            body = (evidence / name).read_bytes()
            entry = cap["files"][name]
            if sha(body) != entry["sha256"]:
                raise ValueError("AUCTION_INPUT_CAPTURE_CHANGED")
            entries[kind] = {**entry, "raw_base64": base64.b64encode(body).decode("ascii")}
        envelope = {
            "provider": PROVIDER,
            "contract": CONTRACT,
            "stock_code": code,
            "market_date": day,
            "base_raw_base64": base64.b64encode(raw).decode("ascii"),
            "base_raw_sha256": sha(raw),
            "evidence": entries,
        }
        combined = canonical(envelope) + b"\n"
        bars = replay(combined, code, day)
        daily = ref["securities"][code]
        total = sum((b["volume_shares"] for b in bars.values()), Decimal(0))
        if total != Decimal(daily["volume"]) or bars[day.replace("-", "") + "1500"][
            "close"
        ] != Decimal(daily["close"]):
            raise ValueError("AUCTION_REPAIRED_DAILY_GATE_FAILED")
        bound = max(instant(e["completed_at"]) for e in entries.values())
        repairs[code] = {"raw": combined, "bars": bars, "observed": bound.isoformat()}
        info = observed(envelope, code, day)
        proofs.append(
            {
                "stock_code": code,
                "original_raw_sha256": sha(raw),
                "compound_raw_sha256": sha(combined),
                "auction": {k: str(v) if isinstance(v, Decimal) else v for k, v in info.items()},
                "source_acquired_at_or_after": bound.isoformat(),
                "daily_gate": "PASS",
            }
        )
    shutil.copytree(source, output, copy_function=shutil.copyfile)
    (output / "ods/raw").mkdir(parents=True, exist_ok=True)
    for name in ["manifest.json", "READY", "quality-status.jsonl", "minute-real-observed.parquet"]:
        (output / name).unlink()
    (output / "evidence").mkdir(exist_ok=True)
    shutil.copyfile(source / "manifest.json", output / "evidence/predecessor-native-manifest.json")
    shutil.copyfile(
        source / "quality-status.jsonl", output / "evidence/predecessor-quality-status.jsonl"
    )
    shutil.copyfile(evidence / "CAPTURE.json", output / "evidence/auction-capture-index.json")
    write(
        output / "evidence/auction-repair.json",
        {
            "contract": CONTRACT,
            "revision": revision,
            "predecessor_manifest_sha256": receipt["native_manifest_sha256"],
            "repairs": proofs,
        },
    )
    for code, entry in repairs.items():
        p = output / "ods/raw" / (code + ".json.gz")
        if p.exists():
            raise ValueError("AUCTION_EXPECTED_HELD_RAW_NOT_IN_PRODUCT")
        p.write_bytes(gzip.compress(entry["raw"], mtime=0))
    for status in statuses:
        code = status["stock_code"]
        if code in repairs:
            status.update(
                quality_grade="R0",
                quality_reason="observed_continuous_and_closing_auction_48_exact_daily",
                formally_ingested=True,
                default_research_admitted=True,
                allowed_consumer="gap_aware_non_execution_development",
                auction_source_observed=True,
                missing_actual_minutes=[],
                raw_sha256=sha(repairs[code]["raw"]),
                source_acquired_at_or_after=repairs[code]["observed"],
                source_composition=PROVIDER,
                base_raw_sha256=next(
                    x["original_raw_sha256"] for x in proofs if x["stock_code"] == code
                ),
            )
    with (output / "quality-status.jsonl").open("xb") as stream:
        for status in statuses:
            stream.write(canonical(status) + b"\n")
    table = pq.read_table(source / "minute-real-observed.parquet")
    added = []
    for code, entry in repairs.items():
        for stamp, bar in sorted(entry["bars"].items()):
            added.append(
                {
                    "stock_code": code,
                    "market_date": day,
                    "source_ts": datetime.strptime(stamp, "%Y%m%d%H%M").isoformat(),
                    "exchange_session_phase": PHASE if stamp.endswith("1500") else "continuous",
                    **bar,
                    "raw_sha256": sha(entry["raw"]),
                    "quality_grade": "R0",
                    "quality_reason": "observed_continuous_and_closing_auction_48_exact_daily",
                    "original_source_observed": True,
                    "default_research_admitted": True,
                }
            )
    table = pa.concat_tables([table, pa.Table.from_pylist(added, schema=minute_schema())]).sort_by(
        [("stock_code", "ascending"), ("source_ts", "ascending")]
    )
    pq.write_table(table, output / "minute-real-observed.parquet", compression="zstd")
    new_files = [
        {"path": p.relative_to(output).as_posix(), "bytes": p.stat().st_size, "sha256": file_sha(p)}
        for p in sorted(output.rglob("*"))
        if p.is_file()
    ]
    manifest.update(
        files=new_files,
        content_digest=_digest(new_files),
        grades=dict(Counter(s["quality_grade"] for s in statuses)),
        rows_per_grade=dict(Counter(table.column("quality_grade").to_pylist())),
        research_ready=all(s["quality_grade"] in {"R0", "SUSPENDED"} for s in statuses),
        sealed_at=datetime.now(timezone.utc).isoformat(),
        code_revision=revision,
        closing_auction_revision={
            "contract": CONTRACT,
            "codes": sorted(repairs),
            "provenance": PROVIDER,
            "predecessor_manifest_sha256": receipt["native_manifest_sha256"],
            "evidence_sha256": file_sha(output / "evidence/auction-repair.json"),
        },
    )
    write(output / "manifest.json", manifest)
    (output / "READY").write_text(file_sha(output / "manifest.json") + "\n")
    return {
        "package_path": str(output),
        "manifest_sha256": file_sha(output / "manifest.json"),
        "grades": manifest["grades"],
        "rows": table.num_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["attempt", "evidence", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(
        json.dumps(build(args.attempt, args.evidence, args.output, args.revision), sort_keys=True)
    )


if __name__ == "__main__":
    main()
