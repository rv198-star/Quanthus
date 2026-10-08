"""Independent source-evidence ODS and consumer-limited research DWD publication.

The product never advances legacy ODS/DWD, P1, daily-core, or OCI pointers.
Full original vendor response gz bytes are copied into a separately sealed
source ODS. DWD is an immutable copy of an independently verified research
qualification; its readiness is distinct from source evidence readiness.

No protected partition, exact-240 consumer, live strategy or P6 activation.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.loss_aware_m1_research import (
    PRODUCT,
    _archived_source_ledger,
    _native_raw,
    _reference,
    verify_package,
)

SOURCE_KIND = "p10_loss_aware_native_1m_source_ods_v1"
RELEASE_KIND = "p10_loss_aware_1m_research_publication_v1"
ALLOWED_DATES = ("2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08")


def _bytes(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _atomic(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with tmp.open("xb") as stream:
        stream.write(_bytes(obj))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _verify_root_separation(target: Path) -> None:
    resolved = target.resolve()
    legacy = Path("/srv/quanthus/dwd-dws-v2/daily").resolve()
    if resolved == legacy or resolved.is_relative_to(legacy):
        raise ValueError("research 1m product cannot publish under legacy/daily-core DWD root")
    if resolved == Path("/"):
        raise ValueError("product root cannot be filesystem root")


def _source_dispositions(source_dwd: Path, market_date: str) -> dict[str, dict[str, Any]]:
    ledger = source_dwd / "security-quality.jsonl"
    if not ledger.is_file():
        raise ValueError("per-security source quality ledger missing")
    out: dict[str, dict[str, Any]] = {}
    for line in ledger.read_text(encoding="ascii").splitlines():
        raw = json.loads(line)
        code = raw.get("stock_code")
        if (
            not isinstance(code, str)
            or len(code) != 6
            or not code.isdigit()
            or code in out
            or raw.get("market_date") != market_date
            or raw.get("strict_240_source_qualified") is not False
        ):
            raise ValueError("invalid/duplicate/permitted-strict quality disposition")
        if raw.get("quality_grade") in ("R0", "R1") and not raw.get("source_raw_sha256"):
            raise ValueError("admitted source row without raw source SHA")
        out[code] = raw
    if len(out) != 4992:
        raise ValueError("source grade census does not cover 4,992 securities")
    return out


def verify_source_ods(root: Path) -> dict[str, Any]:
    obj = json.loads((root / "manifest.json").read_text())
    if obj.get("kind") != SOURCE_KIND or obj.get("product_scope") != PRODUCT:
        raise ValueError("source ODS product identity mismatch")
    manifest_sha = _sha(root / "manifest.json")
    if (root / "SOURCE_EVIDENCE_READY").read_text().strip() != manifest_sha:
        raise ValueError("source evidence READY does not bind ODS manifest")
    files = obj.get("files")
    if not isinstance(files, list) or len(files) != 4993:
        raise ValueError("source ODS physical inventory incomplete")
    physical = {
        f.relative_to(root).as_posix()
        for f in root.rglob("*")
        if f.is_file() and not f.is_symlink()
    }
    expected: set[str] = {"manifest.json", "SOURCE_EVIDENCE_READY"}
    for entry in files:
        rel = entry["path"]
        member = (root / rel).resolve()
        if (
            member.is_symlink()
            or not member.is_relative_to(root.resolve())
            or rel in expected
            or not member.is_file()
            or member.stat().st_size != entry["size_bytes"]
            or _sha(member) != entry["compressed_sha256"]
        ):
            raise ValueError("source ODS retained bytes or path differ")
        expected.add(rel)
    if physical != expected:
        raise ValueError("source ODS contains missing/undeclared files")
    return {"manifest_sha256": manifest_sha, "raw_count": 4992}


def _publish_source_ods(
    *,
    market_date: str,
    source_mode: str,
    source_root: Path,
    market_release: Path,
    source_dwd: Path,
    destination: Path,
) -> dict[str, Any]:
    market, daily_sha, _ = _reference(market_release, market_date)
    quality = json.loads((source_dwd / "quality-summary.json").read_text())
    if (
        daily_sha != quality["reference_ods_manifest_sha256"]
        or quality["market_date"] != market_date
        or quality["product"] != PRODUCT
        or quality["all_expected_securities"] != len(market)
        or quality["raw_source_root"] != str(source_root)
    ):
        raise ValueError("source ODS date/source/daily binding differs from DWD")
    dispositions = _source_dispositions(source_dwd, market_date)
    archived = _archived_source_ledger() if source_mode == "archive" else None
    if set(dispositions) != set(market):
        raise ValueError("source DWD security inventory differs from daily core")

    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".source-ods-", dir=destination.parent))
    try:
        (work / "raw").mkdir()
        inventory = []
        for code in sorted(market):
            raw, sha, compare, acquired = _native_raw(
                source_root, code, day=market_date, mode=source_mode, ledger=archived
            )
            if raw is None or sha is None:
                raise ValueError(f"original source raw missing: {code}")
            quality_source_sha = dispositions[code]["source_raw_sha256"]
            if quality_source_sha is not None and quality_source_sha != sha:
                raise ValueError(f"source raw differs from quality assessment: {code}")
            input_path = (
                source_root / f"{code}.json.gz"
                if source_mode == "archive"
                else source_root / "raw" / f"{code}.json.gz"
            )
            compressed = input_path.read_bytes()
            if hashlib.sha256(gzip.decompress(compressed)).hexdigest() != sha:
                raise ValueError("source compressed bytes are no longer original")
            relative = "raw/" + code + ".json.gz"
            output = work / relative
            with output.open("xb") as stream:
                stream.write(compressed)
            inventory.append(
                {
                    "path": relative,
                    "size_bytes": len(compressed),
                    "compressed_sha256": _sha(output),
                    "raw_source_sha256": "sha256:" + sha,
                }
            )
        source_quality = work / "source-quality.jsonl"
        shutil.copy2(source_dwd / "security-quality.jsonl", source_quality)
        inventory.append(
            {
                "path": "source-quality.jsonl",
                "size_bytes": source_quality.stat().st_size,
                "compressed_sha256": _sha(source_quality),
            }
        )
        manifest = {
            "schema_version": 1,
            "kind": SOURCE_KIND,
            "product_scope": PRODUCT,
            "market_date": market_date,
            "source_mode": source_mode,
            "source_raw_count": 4992,
            "daily_source_manifest_sha256": daily_sha,
            "quality_report_sha256": _sha(source_dwd / "quality-summary.json"),
            "quality_state": quality["status"],
            "provider_original_observations": True,
            "source_evidence_only_not_strict_1m_ready": True,
            "source_asof_not_retroactive_live": True,
            "files": inventory,
        }
        _atomic(work / "manifest.json", manifest)
        ready = work / "SOURCE_EVIDENCE_READY"
        ready.write_text(_sha(work / "manifest.json") + "\n", encoding="ascii")
        verify_source_ods(work)
        os.replace(work, destination)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return verify_source_ods(destination)


def publish(
    *,
    day: str,
    raw_source: Path,
    mode: str,
    market_release: Path,
    research_dwd: Path,
    root: Path,
) -> dict[str, Any]:
    if day not in ALLOWED_DATES or mode not in {"archive", "capture"}:
        raise ValueError("unknown recovery day or source mode")
    _verify_root_separation(root)
    verified = verify_package(research_dwd)
    if verified["market_date"] != day or verified["product_scope"] != PRODUCT:
        raise ValueError("research DWD is wrong product or trading date")
    summary = json.loads((research_dwd / "quality-summary.json").read_text())
    if (
        summary["market_date"] != day
        or summary["source_mode"] != mode
        or summary["provider"] != "tencent_mkline_m1"
        or summary["raw_source_root"] != str(raw_source)
        or (research_dwd / "READY").exists()
        != (summary["status"] == "RESEARCH_READY_LOSS_AWARE")
    ):
        raise ValueError("research quality source or READY mismatch")

    identity = verified["manifest_sha256"].split(":")[1][:16]
    ods_dir = root / "ods/packages" / day / identity
    dwd_dir = root / "dwd/packages" / day / identity
    status_file = root / "date-status" / f"{day}.json"
    if status_file.exists():
        existing = json.loads(status_file.read_text())
        if (
            existing.get("research_dwd_manifest_sha256") != verified["manifest_sha256"]
            or existing.get("market_date") != day
        ):
            raise ValueError("date-status is immutable; needs a separately versioned correction")
        verify_source_ods(ods_dir)
        verify_package(dwd_dir)
        return existing

    if not ods_dir.exists():
        ods_result = _publish_source_ods(
            market_date=day,
            source_mode=mode,
            source_root=raw_source,
            market_release=market_release,
            source_dwd=research_dwd,
            destination=ods_dir,
        )
    else:
        ods_result = verify_source_ods(ods_dir)
    dwd_dir.parent.mkdir(parents=True, exist_ok=True)
    if not dwd_dir.exists():
        staging = Path(tempfile.mkdtemp(prefix=".dwd-stage-", dir=dwd_dir.parent))
        try:
            shutil.copytree(research_dwd, staging, dirs_exist_ok=True)
            copied = verify_package(staging)
            if copied["manifest_sha256"] != verified["manifest_sha256"]:
                raise ValueError("research DWD changed during sealed copy")
            os.replace(staging, dwd_dir)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    copied = verify_package(dwd_dir)
    if copied["manifest_sha256"] != verified["manifest_sha256"]:
        raise ValueError("copied DWD differs from qualified source")
    outcome = {
        "schema_version": 1,
        "kind": RELEASE_KIND,
        "product_scope": PRODUCT,
        "market_date": day,
        "source_ods_manifest_sha256": ods_result["manifest_sha256"],
        "research_dwd_manifest_sha256": copied["manifest_sha256"],
        "research_status": summary["status"],
        "research_ready": (dwd_dir / "READY").is_file(),
        "permitted_consumer": "non_execution_gap_aware_development_research_only",
        "legacy_four_lane_ready": False,
        "legacy_minute_or_daily_current_unchanged": True,
        "source_ods_path": ods_dir.relative_to(root).as_posix(),
        "research_dwd_path": dwd_dir.relative_to(root).as_posix(),
        "quality_grades": summary["grades"],
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic(status_file, outcome)
    if outcome["research_ready"]:
        current = root / "CURRENT_RESEARCH_1M.json"
        if current.is_file():
            previous = json.loads(current.read_text())
            if date.fromisoformat(str(previous["market_date"])) >= date.fromisoformat(day):
                if previous.get("market_date") != day:
                    raise ValueError("research pointer cannot move backwards")
                if previous.get("research_dwd_manifest_sha256") != copied["manifest_sha256"]:
                    raise ValueError("research current same-day conflict")
        _atomic(current, outcome)
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("archive", "capture"), required=True)
    parser.add_argument("--market-release", type=Path, required=True)
    parser.add_argument("--research-dwd", type=Path, required=True)
    parser.add_argument("--product-root", type=Path, required=True)
    args = parser.parse_args(argv)
    record = publish(
        day=args.day,
        raw_source=args.source_root,
        mode=args.mode,
        market_release=args.market_release,
        research_dwd=args.research_dwd,
        root=args.product_root,
    )
    print(json.dumps(record, sort_keys=True))
    return 0 if record["research_ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
