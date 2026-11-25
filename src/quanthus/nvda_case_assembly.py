"""#736 VAL-NVDA-20261123 case seal assembly and gated reading, contract repair v2.

Assembly copies bytes only, never decodes market rows, titles or bodies. Input and
result closures are physically separate. Result content stays behind a reader gate
whose authorizations come from a versioned authorization file; the caller's
writable output area never expands authority. Rejection is decided before any
blob open, and the access log for rejections uses only the sealed member digest
and request metadata.

Schema binding (verified against CN02 production, metadata only):
- DATES.json uses a "dates" dict; each entry's "core" is a directory path and
  "core_manifest_sha256" is the binding digest (sha256:-prefixed).
- core/manifest.json carries a "files" list of {path, sha256, size_bytes}; bar
  members are bound to those declared member SHA256s, not recomputation alone.
- universe identity comes from source-universe.json codes/company_org_ids plus
  the frozen 12 equities; benchmarks 399006/000300 are result-side only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ASSEMBLER_VERSION = "nvda-case-assembly.v2"

RAW_OBJECT_KINDS = ("wire", "document-events", "markdown")

FROZEN_EQUITIES = (
    "300308",
    "300502",
    "300394",
    "002463",
    "300476",
    "601138",
    "000977",
    "002916",
    "002436",
    "002837",
    "688256",
    "688041",
)
RESULT_BENCHMARKS = ("399006", "000300")
EXCHANGE_BY_PREFIX = {
    "300": "SZSE",
    "002": "SZSE",
    "000": "SZSE",
    "601": "SSE",
    "688": "SSE",
}

ANCHOR_SOURCES = ("anchor-NVDA", "anchor-SOX", "anchor-NDX")
BLOCKED_REFERENCE_STATUSES = ("HTTP_BLOCKED_OR_PARTIAL",)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def sha_file(path: Path) -> str:
    return sha(path.read_bytes())


def member_locations(objects_root: Path, member_sha: str) -> list[Path]:
    found = []
    for kind in sorted(os.listdir(objects_root)):
        candidate = objects_root / kind / member_sha[:2] / member_sha
        if candidate.exists():
            found.append(candidate)
    return found


def link_bytes(source: Path, dest_dir: Path, expected_sha: str) -> dict[str, Any]:
    if sha_file(source) != expected_sha:
        raise ValueError(f"MEMBER_SHA_MISMATCH:{source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / expected_sha
    before = now()
    linked = False
    if target.exists():
        if sha_file(target) != expected_sha:
            raise ValueError(f"IMMUTABLE_OBJECT_CONFLICT:{target}")
    else:
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
        linked = True
    return {
        "sha256": expected_sha,
        "size_bytes": target.stat().st_size,
        "sealed_path": str(target),
        "commit_interval": {"start": before, "end": now()},
        "hardlinked": linked,
    }


def write_json_atomic(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = encoded(value)
    tmp = path.with_name(path.name + ".pending")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    return sha(raw)


def read_record(objects_root: Path, member_sha: str) -> dict[str, Any] | None:
    for kind in ("case-input", "case-result-capture"):
        candidate = objects_root / kind / member_sha[:2] / member_sha
        if candidate.exists():
            record = json.loads(candidate.read_bytes())
            record["_object_kind"] = kind
            return record
    return None


def inventory_items(state_db: Path) -> list[dict[str, Any]]:
    db = sqlite3.connect("file:" + str(state_db) + "?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT id, member_sha, first_seen FROM items ORDER BY id").fetchall()
    finally:
        db.close()
    return [{"id": row[0], "member_sha": row[1], "first_seen": row[2]} for row in rows]


def collect_case_members(
    objects_root: Path, state_db: Path, case_id: str
) -> dict[str, list[dict[str, Any]]]:
    sides: dict[str, list[dict[str, Any]]] = {"input": [], "result": []}
    for row in inventory_items(state_db):
        record = read_record(objects_root, row["member_sha"])
        if not record or record.get("case_id") != case_id:
            continue
        receipt = record.get("raw_source_receipt") or {}
        entry = {
            "source_key": row["id"],
            "member_sha256": row["member_sha"],
            "object_kind": record["_object_kind"],
            "case_role": record.get("case_role"),
            "first_seen_at": record.get("first_seen_at"),
            "version_first_seen_at": record.get("version_first_seen_at"),
            "source_artifact_known_at": record.get("source_artifact_known_at"),
            "response_completed_at": receipt.get("response_completed_at"),
            "source_http_date": receipt.get("source_http_date"),
            "previous_member_sha256": record.get("previous_member_sha256"),
        }
        raw = receipt.get("raw") or {}
        raw_sha = raw.get("sha256")
        if raw_sha:
            locations = member_locations(objects_root, raw_sha)
            if locations:
                entry["raw_object_sha256"] = raw_sha
                entry["raw_object_kind"] = locations[0].parent.parent.name
        side = "input" if record.get("case_role") == "INPUT_VISIBLE_BEFORE_CUTOFF" else "result"
        sides[side].append(entry)
    for side in sides:
        sides[side].sort(key=lambda x: (x["source_key"], x["first_seen_at"] or ""))
    return sides


def exchange_for(code: str) -> str:
    return EXCHANGE_BY_PREFIX.get(code[:3], "UNKNOWN")


def equity_projection(universe: dict[str, Any] | None) -> dict[str, Any]:
    """Frozen-12-only projection. Identity is exchange+org-id from the qualified
    universe metadata; a frozen code absent from the universe is an auditable gap,
    never guessed."""
    rows = []
    gaps = []
    codes = set((universe or {}).get("codes") or [])
    org = (universe or {}).get("company_org_ids") or {}
    for code in FROZEN_EQUITIES:
        if universe is None or code not in codes:
            gaps.append(
                {
                    "item": "equity-identity:" + code,
                    "status": "unavailable",
                    "reason": "FROZEN_CODE_NOT_IN_QUALIFIED_UNIVERSE",
                }
            )
            continue
        rows.append(
            {
                "code": code,
                "exchange": exchange_for(code),
                "cninfo_org_id": org.get(code),
                "identity_source": (
                    "qualified dwd universe source-universe.json codes/company_org_ids"
                ),
            }
        )
    return {
        "schema": "quanthus.p10.nvda-case-equity-projection.v1",
        "frozen_only": True,
        "equities": rows,
        "gaps": gaps,
    }


def dates_entries(index: dict[str, Any]) -> dict[str, Any]:
    if "dates" in index:
        return index["dates"]
    if "days" in index:
        return index["days"]
    raise ValueError("TRADE_INDEX_SCHEMA_UNRECOGNIZED")


def manifest_file_entry(
    manifest: dict[str, Any], table: str, market_date: str
) -> dict[str, Any] | None:
    suffix = (
        "payload/security_market_observation/"
        + table
        + "/market_date="
        + market_date
        + "/part-00000.parquet"
    )
    for entry in manifest.get("files") or []:
        if entry.get("path") == suffix or str(entry.get("path", "")).endswith(suffix):
            return entry
    return None


def seal_core_day(
    core_dir: Path,
    manifest_sha_expected: str,
    market_date: str,
    dest_dir: Path,
    gaps: list[dict[str, Any]],
    label: str,
    side_tag: str | None,
    tables=("dwd_index_bar", "dwd_security_bar"),
) -> list[dict[str, Any]]:
    """Bind the day through its manifest: manifest digest must match the indexed
    binding, then each bar member is verified against the manifest-declared member
    sha256. Recomputation is a consistency check, not the source of truth."""
    rows = []
    manifest_path = core_dir / "manifest.json"
    if not manifest_path.exists():
        gaps.append(
            {
                "item": label + ":" + market_date,
                "status": "unavailable",
                "reason": "CORE_MANIFEST_MISSING",
            }
        )
        return rows
    actual = sha_file(manifest_path)
    expected = str(manifest_sha_expected or "").replace("sha256:", "")
    if expected and actual != expected:
        raise ValueError("CORE_MANIFEST_SHA_MISMATCH:" + market_date)
    manifest = load_json(manifest_path)
    link_bytes(manifest_path, dest_dir / "core-manifests", actual)
    for table in tables:
        declared = manifest_file_entry(manifest, table, market_date)
        if declared is None:
            gaps.append(
                {
                    "item": label + ":" + market_date + ":" + table,
                    "status": "unavailable",
                    "reason": "MEMBER_NOT_IN_CORE_MANIFEST",
                }
            )
            continue
        part = (
            core_dir / declared["path"]
            if not str(declared["path"]).startswith("/")
            else Path(declared["path"])
        )
        if not part.exists():
            part = (
                core_dir
                / "payload"
                / "security_market_observation"
                / table
                / ("market_date=" + market_date)
                / "part-00000.parquet"
            )
        if not part.exists():
            gaps.append(
                {
                    "item": label + ":" + market_date + ":" + table,
                    "status": "unavailable",
                    "reason": "PART_FILE_MISSING",
                }
            )
            continue
        declared_sha = str(declared["sha256"]).replace("sha256:", "")
        if sha_file(part) != declared_sha:
            raise ValueError("MEMBER_SHA_MISMATCH_DECLARED:" + market_date + ":" + table)
        seal = link_bytes(part, dest_dir / "bars", declared_sha)
        row = {
            "market_date": market_date,
            "table": table,
            "sha256": seal["sha256"],
            "size_bytes": seal["size_bytes"],
            "source_path": str(part),
            "core_dir": str(core_dir),
            "core_manifest_sha256": actual,
            "declared_member_sha256": declared_sha,
            "sha_binding": "manifest-declared member sha256 verified, byte copy only",
            "row_access": "NO_MARKET_ROW_DECODE",
            "bytes_copied_only": True,
        }
        if side_tag:
            row["side"] = side_tag
            row["sealed_blind_at"] = now()
        rows.append(row)
    return rows


def verify_inventory_bars(
    members: list[dict[str, Any]], dest_dir: Path, label: str, gaps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    sealed = []
    for member in members:
        source = Path(member["path"])
        if not source.exists():
            gaps.append(
                {
                    "item": label + ":" + str(member.get("market_date")) + ":" + source.name,
                    "status": "unavailable",
                    "reason": "SOURCE_FILE_MISSING",
                }
            )
            continue
        seal = link_bytes(source, dest_dir, member["sha256"])
        sealed.append(
            {
                "market_date": member.get("market_date"),
                "table": "dwd_index_bar" if "dwd_index_bar" in str(source) else "dwd_security_bar",
                "sha256": seal["sha256"],
                "size_bytes": seal["size_bytes"],
                "source_path": str(source),
                "source_manifest_sha256": member.get("source_manifest_sha256"),
                "p10_verified_at_actual": member.get("p10_verified_at_actual"),
                "row_access": member.get("row_access", "NO_MARKET_ROW_DECODE"),
                "bytes_copied_only": True,
            }
        )
    return sealed


def seal_news_side(
    entries: list[dict[str, Any]],
    objects_root: Path,
    dest_dir: Path,
    gaps: list[dict[str, Any]],
    label: str,
) -> list[dict[str, Any]]:
    sealed = []
    for entry in entries:
        locations = member_locations(objects_root, entry["member_sha256"])
        member_source = next(
            (p for p in locations if p.parent.parent.name == entry["object_kind"]), None
        )
        if member_source is None:
            gaps.append(
                {
                    "item": label + ":" + entry["source_key"],
                    "status": "unavailable",
                    "reason": "MEMBER_OBJECT_MISSING",
                }
            )
            continue
        link_bytes(member_source, dest_dir / "members", entry["member_sha256"])
        if entry.get("raw_object_sha256") and entry.get("raw_object_kind") in RAW_OBJECT_KINDS:
            raw_locations = member_locations(objects_root, entry["raw_object_sha256"])
            raw_source = next(
                (p for p in raw_locations if p.parent.parent.name == entry["raw_object_kind"]), None
            )
            if raw_source is not None:
                link_bytes(raw_source, dest_dir / "raw", entry["raw_object_sha256"])
        sealed.append({k: v for k, v in entry.items() if k != "raw_object_kind"})
    return sealed


def seal_anchor(
    anchor_input_root: Path,
    snap: dict[str, Any],
    dest_dir: Path,
    receipts_dir: Path,
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Anchor wires bind by fixed sha256 against the separately staged original
    bytes. Successful anchors (HTTP 200 wire captures) and vendor-blocked
    reference stubs stay distinct; blocked stubs are evidence of the block, not
    content."""
    raw_path = Path(snap.get("raw_path") or anchor_input_root / (snap["source"] + ".wire"))
    entry: dict[str, Any] = {
        "source": snap["source"],
        "role": snap.get("role"),
        "http_status": snap.get("http_status"),
        "source_http_date": snap.get("source_http_date"),
        "response_completed_at": snap.get("response_completed_at"),
        "ingested_at_actual": snap.get("ingested_at_actual"),
        "capture_clock_role": (
            "producer-observed clocks preserved verbatim; vendor publication channel clock unknown"
        ),
    }
    if not raw_path.exists():
        gaps.append(
            {
                "item": "anchor:" + snap["source"],
                "status": "unavailable",
                "reason": "WIRE_FILE_MISSING",
            }
        )
        entry["status"] = "unavailable"
        return entry
    seal = link_bytes(raw_path, dest_dir, snap["raw_sha256"])
    blocked = (
        snap.get("status") in BLOCKED_REFERENCE_STATUSES or (snap.get("http_status") or 0) >= 400
    )
    entry.update(
        {
            "sha256": seal["sha256"],
            "original_path": snap.get("raw_path"),
            "staged_path": str(raw_path),
            "path_binding": (
                "original capture path and staged path verified to the same"
                " fixed raw_sha256 before sealing"
            ),
            "size_bytes": seal["size_bytes"],
            "row_count": snap.get("row_count") if not blocked else None,
            "capture_class": "VENDOR_BLOCKED_REFERENCE_STUB"
            if blocked
            else "SUCCESSFUL_RAW_ANCHOR_CAPTURE",
            "consumer_qualification": "content not usable as reference"
            if blocked
            else snap.get("consumer_qualification", "PENDING"),
            "status": snap.get("status"),
            "bytes_copied_only": True,
        }
    )
    return entry


def assert_output_root_exclusive(output_root: Path) -> None:
    """The seal root is append-once: a rerun over an existing seal refuses before
    writing anything, so original sealed bytes can never be replaced in place."""
    sentinels = [
        output_root / "RECEIPT.json",
        output_root / "input" / "manifest.json",
        output_root / "input" / "SEALED.json",
        output_root / "result" / "manifest.json",
        output_root / "result" / "SEALED.json",
    ]
    existing = [str(p) for p in sentinels if p.exists()]
    if existing:
        raise FileExistsError("SEALED_ROOT_ALREADY_PRESENT:" + ";".join(sorted(existing)))


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    ordered = sorted(
        rows, key=lambda x: (str(x.get("market_date")), x.get("table", ""), x["sha256"])
    )
    for row in ordered:
        existing = seen.get(row["sha256"])
        if existing is None:
            seen[row["sha256"]] = dict(row)
            continue
        merged = dict(existing)
        merged["duplicate_bindings"] = merged.get("duplicate_bindings", 0) + 1
        seen[row["sha256"]] = merged
    return [seen[k] for k in sorted(seen)]


def assemble(
    policy_path: Path,
    inventory_path: Path,
    anchor_result_path: Path,
    anchor_input_root: Path,
    news_root: Path,
    output_root: Path,
    job_index_path: Path | None,
    result_dates: list[str],
    universe_path: Path | None = None,
) -> dict[str, Any]:
    assert_output_root_exclusive(output_root)
    started = now()
    policy = load_json(policy_path)
    inventory = load_json(inventory_path)
    anchors = load_json(anchor_result_path)
    case_id = policy["case_id"]
    gaps: list[dict[str, Any]] = []
    objects_root = news_root / "objects"
    state_db = news_root / "state.sqlite3"

    input_root = output_root / "input"
    result_root = output_root / "result"

    # Frozen-12 equity projection, identity only. Benchmarks never appear here.
    universe = load_json(universe_path) if universe_path and universe_path.exists() else None
    if universe_path is not None and universe is None:
        gaps.append(
            {
                "item": "equity-identity",
                "status": "unavailable",
                "reason": "UNIVERSE_SOURCE_MISSING",
            }
        )
    projection = equity_projection(universe)
    gaps.extend(projection.pop("gaps"))
    write_json_atomic(input_root / "equity-projection.json", projection)

    # Input side: ONLY strictly pre-cutoff inventory members. Overlap-day
    # inventory bytes (>= first result date) are excluded from input and resealed
    # blind on the result side, tagged RESULT_SIDE_POST_CUTOFF.
    result_start = min(result_dates) if result_dates else None
    inventory_members = list(inventory.get("members", []))
    pre_members = [
        m
        for m in inventory_members
        if result_start is None or str(m.get("market_date") or "") < result_start
    ]
    overlap_members = [
        m
        for m in inventory_members
        if result_start is not None and str(m.get("market_date") or "") >= result_start
    ]
    bars_input = verify_inventory_bars(pre_members, input_root / "bars", "input-bars", gaps)
    bars_overlap = verify_inventory_bars(
        overlap_members, result_root / "overlap-inventory", "overlap-inventory-bars", gaps
    )
    for row in bars_overlap:
        row["side"] = "RESULT_SIDE_POST_CUTOFF"
        row["sealed_blind_at"] = now()

    # Input anchors: fixed-sha binding against staged originals.
    anchor_entries = []
    for snap in anchors.get("snapshots", []):
        anchor_entries.append(
            seal_anchor(
                anchor_input_root,
                snap,
                input_root / "anchors",
                input_root / "anchor-receipts",
                gaps,
            )
        )
    for receipt in sorted(anchor_input_root.glob("*.receipt.json")):
        digest = sha_file(receipt)
        link_bytes(receipt, input_root / "anchor-receipts", digest)

    case_members = collect_case_members(objects_root, state_db, case_id)
    input_news = seal_news_side(
        case_members["input"], objects_root, input_root / "news", gaps, "input-news"
    )
    result_news = seal_news_side(
        case_members["result"], objects_root, result_root / "news", gaps, "result-news"
    )

    # Result side bars through the DATES index: "dates" entries bind core dir +
    # core manifest sha + manifest-declared member sha.
    bars_result: list[dict[str, Any]] = []
    index_binding: dict[str, Any] = {
        "job_index_path": str(job_index_path) if job_index_path else None,
        "days": {},
    }
    if job_index_path is None:
        for date in sorted(result_dates):
            gaps.append(
                {
                    "item": "result-bars:" + date,
                    "status": "unavailable",
                    "reason": "TRADE_DAY_INDEX_ABSENT",
                }
            )
    else:
        index = load_json(job_index_path)
        index_binding["job_index_sha256"] = sha_file(job_index_path)
        days = dates_entries(index)
        for date in sorted(result_dates):
            day = days.get(date)
            if not day or not (day.get("core") or day.get("core_dir")):
                gaps.append(
                    {
                        "item": "result-bars:" + date,
                        "status": "unavailable",
                        "reason": "TRADE_DAY_NOT_INDEXED",
                    }
                )
                continue
            core_dir = Path(day.get("core") or day.get("core_dir"))
            rows = seal_core_day(
                core_dir,
                day.get("core_manifest_sha256", ""),
                date,
                result_root,
                gaps,
                "result-bars",
                "RESULT_SIDE_POST_CUTOFF",
            )
            bars_result.extend(rows)
            index_binding["days"][date] = {
                "status": day.get("status"),
                "collector_revision": day.get("collector_revision"),
                "pipeline_revision": day.get("pipeline_revision"),
                "core_manifest_sha256": day.get("core_manifest_sha256"),
                "consumer_admission": day.get("consumer_admission"),
            }

    all_result_bars = dedupe_rows(bars_overlap + bars_result)

    finished = now()
    input_manifest = {
        "schema": "quanthus.p10.nvda-case-input-manifest.v2",
        "case_id": case_id,
        "assembler_version": ASSEMBLER_VERSION,
        "input_cutoff": policy["input_cutoff"],
        "definition_body_sha256": policy["definition_body_sha256"],
        "frozen_main_codes": policy["main_codes"],
        "frozen_control_codes": policy["control_codes"],
        "benchmark_exclusion": (
            "benchmarks are RESULT_SIDE_ONLY; no benchmark member is part of this input closure"
        ),
        "policy_sha256": sha_file(policy_path),
        "inventory_sha256": sha_file(inventory_path),
        "anchor_result_sha256": sha_file(anchor_result_path),
        "bars": bars_input,
        "anchors": anchor_entries,
        "news_members": input_news,
        "counts": {
            "bars": len(bars_input),
            "anchors": len(anchor_entries),
            "news_members": len(input_news),
        },
        "assembled_at_actual": finished,
        "sealed_at_actual": finished,
        "content_disclosure": (
            "metadata only; no market values, titles or bodies in manifest or logs"
        ),
    }
    input_manifest["members_digest"] = sha(
        encoded(
            sorted(m["sha256"] for m in bars_input if "sha256" in m)
            + sorted(a["sha256"] for a in anchor_entries if "sha256" in a)
            + sorted(n["member_sha256"] for n in input_news)
        )
    )
    input_sha = write_json_atomic(input_root / "manifest.json", input_manifest)
    write_json_atomic(
        input_root / "SEALED.json",
        {"sealed": True, "manifest_sha256": input_sha, "sealed_at_actual": finished},
    )

    result_manifest = {
        "schema": "quanthus.p10.nvda-case-result-manifest.v2",
        "case_id": case_id,
        "assembler_version": ASSEMBLER_VERSION,
        "event_window_end": policy.get("event_window_end"),
        "result_dates": sorted(result_dates),
        "benchmark_codes": [
            b["code"] if isinstance(b, dict) else b for b in policy.get("benchmark_codes", [])
        ],
        "benchmark_role": "RESULT_SIDE_ONLY_NOT_DECISION_INPUT",
        "bars": all_result_bars,
        "overlap_inventory_bars_resealed_blind": len(bars_overlap),
        "index_binding": index_binding,
        "news_members": result_news,
        "counts": {"bars": len(all_result_bars), "news_members": len(result_news)},
        "assembled_at_actual": finished,
        "sealed_at_actual": finished,
        "access": {
            "gate": "DECLARED_VALIDATION_AUTHORIZATION_REQUIRED",
            "validation_authorizations": list(policy.get("validation_authorizations") or []),
            "access_log": "ACCESS-LOG.jsonl append-only reader/time/declared_step/artifact sha256",
        },
        "content_disclosure": "metadata only; sealed content never decoded by assembler",
    }
    result_manifest["members_digest"] = sha(
        encoded(
            sorted(m["sha256"] for m in all_result_bars)
            + sorted(n["member_sha256"] for n in result_news)
        )
    )
    result_sha = write_json_atomic(result_root / "manifest.json", result_manifest)
    write_json_atomic(
        result_root / "SEALED.json",
        {"sealed": True, "manifest_sha256": result_sha, "sealed_at_actual": finished},
    )
    access_log = result_root / "ACCESS-LOG.jsonl"
    if not access_log.exists():
        access_log.write_bytes(b"")

    status = "SEALED" if not gaps else "PARTIAL"
    receipt = {
        "schema": "quanthus.p10.nvda-case-seal-receipt.v2",
        "case_id": case_id,
        "status": status,
        "input_manifest_sha256": input_sha,
        "result_manifest_sha256": result_sha,
        "input_counts": input_manifest["counts"],
        "result_counts": result_manifest["counts"],
        "input_members_digest": input_manifest["members_digest"],
        "result_members_digest": result_manifest["members_digest"],
        "gaps": gaps,
        "started_at_actual": started,
        "sealed_at_actual": finished,
        "output_root": str(output_root),
        "market_rows_decoded": 0,
        "titles_or_bodies_printed": 0,
    }
    receipt_sha = write_json_atomic(output_root / "RECEIPT.json", receipt)
    receipt["receipt_sha256"] = receipt_sha
    write_json_atomic(output_root / "RECEIPT.json", receipt)
    return receipt


def verification_hash(artifact: Path) -> str:
    digest = hashlib.sha256()
    with artifact.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AuthorizationError(Exception):
    pass


def validate_authorization_file(auth_path: Path) -> list[dict[str, Any]]:
    """Authorizations come only from this versioned file, bound to reader,
    declared step and fixed sealed member. Its sha256 is itself recorded so the
    exact authority version is auditable. Nothing in the caller-writable output
    area can extend it."""
    auths = load_json(auth_path)
    entries = auths.get("authorizations") or [] if isinstance(auths, dict) else auths
    if not isinstance(entries, list):
        raise AuthorizationError("AUTHORIZATION_FILE_SCHEMA_INVALID")
    validated = []
    for auth in entries:
        if not isinstance(auth, dict):
            raise AuthorizationError("AUTHORIZATION_ENTRY_SCHEMA_INVALID")
        if (
            not auth.get("authorization_id")
            or not auth.get("reader")
            or not auth.get("declared_steps")
            or not auth.get("artifact_member_sha256s")
        ):
            raise AuthorizationError("AUTHORIZATION_ENTRY_INCOMPLETE")
        validated.append(auth)
    return validated


def find_sealed_artifact(result_root: Path, member_sha: str) -> Path | None:
    for candidate in (
        result_root / "bars" / member_sha,
        result_root / "overlap-inventory" / member_sha,
        result_root / "news" / "members" / member_sha,
        result_root / "news" / "raw" / member_sha,
    ):
        if candidate.exists():
            return candidate
    return None


def sealed_member_digests(result_root: Path) -> dict[str, str]:
    """Digests computed from the committed sealed manifest only; used in the
    rejection path so a refused read never opens a result blob."""
    digests: dict[str, str] = {}
    manifest_path = result_root / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        for row in manifest.get("bars") or []:
            if row.get("sha256"):
                digests[row["sha256"]] = row["sha256"]
        for row in manifest.get("news_members") or []:
            if row.get("member_sha256"):
                digests[row["member_sha256"]] = row["member_sha256"]
    return digests


def append_access_log(result_root: Path, entry: dict[str, Any]) -> None:
    access_log = result_root / "ACCESS-LOG.jsonl"
    with access_log.open("ab") as handle:
        handle.write((json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode())


def gated_read(
    result_root: Path,
    authorization_file: Path,
    artifact_member_sha: str,
    reader: str,
    declared_step: str,
) -> dict[str, Any]:
    """Authorization first, blob open after. The rejection path never touches
    result objects: it logs the member digest as committed in the sealed manifest
    plus request metadata only. A valid read binds reader, step and the fixed
    sealed member, and the logged artifact sha256 is reverified at read time."""
    if not (result_root / "manifest.json").exists():
        raise ValueError("RESULT_MANIFEST_MISSING")
    auth_file_sha = sha_file(authorization_file) if authorization_file.exists() else None
    try:
        authorizations = validate_authorization_file(authorization_file)
    except (AuthorizationError, FileNotFoundError, json.JSONDecodeError) as exc:
        authorizations = []
        auth_error = type(exc).__name__
    else:
        auth_error = None

    matched = None
    for auth in authorizations:
        if (
            reader == auth.get("reader")
            and declared_step in (auth.get("declared_steps") or [])
            and artifact_member_sha in (auth.get("artifact_member_sha256s") or [])
        ):
            matched = auth
            break

    entry: dict[str, Any] = {
        "read_at_actual": now(),
        "reader": reader,
        "declared_step": declared_step,
        "artifact_member_sha256": artifact_member_sha,
        "authorization_file_sha256": auth_file_sha,
    }

    if matched is None or artifact_member_sha not in sealed_member_digests(result_root):
        entry["decision"] = (
            "REJECTED_NO_AUTHORIZATION" if matched is None else "REJECTED_MEMBER_NOT_SEALED"
        )
        if auth_error:
            entry["authorization_file_error"] = auth_error
        # rejection path: digest comes from the committed manifest, no blob open
        entry["artifact_manifest_digest"] = sealed_member_digests(result_root).get(
            artifact_member_sha
        )
        append_access_log(result_root, entry)
        raise PermissionError(
            "VALIDATION_AUTHORIZATION_REQUIRED"
            if matched is None
            else "ARTIFACT_MEMBER_NOT_IN_SEALED_MANIFEST"
        )

    artifact = find_sealed_artifact(result_root, artifact_member_sha)
    if artifact is None:
        entry["decision"] = "REJECTED_ARTIFACT_MISSING"
        append_access_log(result_root, entry)
        raise FileNotFoundError("ARTIFACT_MEMBER_NOT_SEALED")

    # authorized path: only now is the blob opened, for the reverification hash
    entry["artifact_sha256"] = verification_hash(artifact)
    if entry["artifact_sha256"] != artifact_member_sha:
        entry["decision"] = "REJECTED_SEALED_CONTENT_DIVERGED"
        append_access_log(result_root, entry)
        raise PermissionError("SEALED_CONTENT_DIVERGES_FROM_MANIFEST")
    entry["decision"] = "AUTHORIZED"
    entry["authorization_id"] = matched.get("authorization_id")
    append_access_log(result_root, entry)
    return {"entry": entry, "artifact_path": str(artifact), "bytes": artifact.stat().st_size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="assemble and seal the VAL-NVDA-20261123 case package"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("assemble")
    a.add_argument("--policy", type=Path, required=True)
    a.add_argument("--inventory", type=Path, required=True)
    a.add_argument("--anchor-result", type=Path, required=True)
    a.add_argument("--anchor-input-root", type=Path, required=True)
    a.add_argument("--news-root", type=Path, required=True)
    a.add_argument("--output-root", type=Path, required=True)
    a.add_argument("--job-index", type=Path, default=None)
    a.add_argument("--result-dates", nargs="*", default=[])
    a.add_argument("--universe", type=Path, default=None)
    r = sub.add_parser("read")
    r.add_argument("--result-root", type=Path, required=True)
    r.add_argument("--authorizations", type=Path, required=True)
    r.add_argument("--artifact-member-sha256", required=True)
    r.add_argument("--reader", required=True)
    r.add_argument("--declared-step", required=True)
    args = parser.parse_args(argv)
    if args.command == "assemble":
        receipt = assemble(
            policy_path=args.policy,
            inventory_path=args.inventory,
            anchor_result_path=args.anchor_result,
            anchor_input_root=args.anchor_input_root,
            news_root=args.news_root,
            output_root=args.output_root,
            job_index_path=args.job_index,
            result_dates=args.result_dates,
            universe_path=args.universe,
        )
        print(
            json.dumps(
                {
                    k: receipt[k]
                    for k in ("status", "input_counts", "result_counts", "receipt_sha256")
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    result = gated_read(
        args.result_root,
        args.authorizations,
        args.artifact_member_sha256,
        args.reader,
        args.declared_step,
    )
    print(json.dumps({"decision": result["entry"]["decision"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
