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

try:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in runtimes without parquet support
    pq = None

ASSEMBLER_VERSION = "nvda-case-assembly.v4"

RAW_OBJECT_KINDS = ("wire", "document-events", "markdown")

EXPECTED_OBJECT_KIND_BY_ROLE = {
    "INPUT_VISIBLE_BEFORE_CUTOFF": "case-input",
    "POST_CUTOFF_RESULT_CAPTURE": "case-result-capture",
}

EXPECTED_SIDE_BY_ROLE = {
    "INPUT_VISIBLE_BEFORE_CUTOFF": "input",
    "POST_CUTOFF_RESULT_CAPTURE": "result",
}

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
            record: dict[str, Any] = json.loads(candidate.read_bytes())
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


def parquet_shape(path: Path) -> dict[str, Any]:
    """Return footer/schema shape only. This never decodes market observations."""
    if pq is None:
        return {"available": False, "reason": "PYARROW_UNAVAILABLE"}
    try:
        pf = pq.ParquetFile(path)
        metadata = pf.metadata
        return {
            "available": True,
            "num_rows": metadata.num_rows,
            "num_row_groups": metadata.num_row_groups,
            "num_columns": metadata.num_columns,
            "columns": list(pf.schema_arrow.names),
        }
    except Exception as exc:  # malformed input is a data availability gap, not a row read
        return {"available": False, "reason": type(exc).__name__}


def read_identity_values(path: Path, column: str) -> dict[str, Any]:
    """Read only the requested identity column from a parquet member.

    The assembly never reads open/high/low/close/volume/turnover or any other market
    value column. Identity-column access is necessary because the production DWD bar
    contract keys observations by listing_id/index_id rather than by the frozen
    display code. If the column is absent or unreadable, callers receive a named gap.
    """
    if pq is None:
        return {"available": False, "reason": "PYARROW_UNAVAILABLE", "values": []}
    try:
        pf = pq.ParquetFile(path)
        if column not in pf.schema_arrow.names:
            return {"available": False, "reason": "IDENTITY_COLUMN_MISSING", "values": []}
        values = pf.read(columns=[column]).column(column).to_pylist()
        counts: dict[str, int] = {}
        for value in values:
            if value is None:
                continue
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
        unique = sorted(counts)
        return {
            "available": True,
            "values": unique,
            "counts": counts,
            "identity_rows": len(values),
            "non_null_rows": sum(1 for v in values if v is not None),
        }
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__, "values": []}


def table_identity_column(table: str) -> str | None:
    if table == "dwd_security_bar":
        return "listing_id"
    if table == "dwd_index_bar":
        return "index_id"
    return None


def projection_member_digest(parts: dict[str, Any]) -> str:
    return sha(encoded(parts))


def project_table_members(
    *,
    table_path: Path,
    table: str,
    market_date: str,
    source_sha256: str,
    identities: list[dict[str, Any]],
    side: str,
    gaps: list[dict[str, Any]],
    label: str,
) -> list[dict[str, Any]]:
    """Project one whole-table member into named per-date/per-identity closure rows.

    Whole-table bytes remain source evidence only. The closure rows are deterministic
    metadata members: source date, exact identity, source member SHA, parquet shape,
    identity value binding, row count and projection digest. Absent and duplicate
    identity facts are explicit; presence of the table never implies coverage.
    """
    shape = parquet_shape(table_path)
    column = table_identity_column(table)
    rows: list[dict[str, Any]] = []
    if not shape.get("available"):
        for identity in identities:
            code = str(identity.get("code"))
            gaps.append(
                {
                    "item": label + ":" + market_date + ":" + table + ":" + code,
                    "status": "unavailable",
                    "reason": "SOURCE_PARQUET_SHAPE_UNREADABLE:" + str(shape.get("reason")),
                    "side": side,
                }
            )
        return rows
    if column is None or column not in shape.get("columns", []):
        for identity in identities:
            code = str(identity.get("code"))
            gaps.append(
                {
                    "item": label + ":" + market_date + ":" + table + ":" + code,
                    "status": "unavailable",
                    "reason": "IDENTITY_COLUMN_MISSING",
                    "side": side,
                }
            )
        return rows

    # The identity column is read exactly once per source member. Its value counts
    # are the only evidence for presence, absence and duplicate facts; neither an
    # expected universe identity nor the existence of a whole table can substitute
    # for this member-level check.
    identity_read = read_identity_values(table_path, column)

    observed_values = set(identity_read.get("values") or [])
    observed_counts = identity_read.get("counts") or {}
    for identity in identities:
        code = str(identity.get("code"))
        expected = identity.get("expected_source_identity")
        if expected is None and identity.get("source_identity") is not None:
            expected = identity.get("source_identity")
        item_label = label + ":" + market_date + ":" + table + ":" + code
        if not identity_read.get("available"):
            gaps.append(
                {
                    "item": item_label,
                    "status": "unavailable",
                    "reason": "SOURCE_IDENTITY_READ_FAILED:" + str(identity_read.get("reason")),
                    "side": side,
                }
            )
            continue
        matches: list[str] = []
        duplicate_count = 0
        if expected is not None:
            expected_text = str(expected)
            if expected_text in observed_values:
                matches = [expected_text]
                duplicate_count = int(observed_counts.get(expected_text) or 0)
        else:
            namespace = identity.get("source_identity_namespace")
            aliases = {str(x) for x in identity.get("source_identity_aliases") or []}
            candidates = sorted((aliases | {code}) & observed_values)
            if namespace:
                namespaced = sorted(
                    {namespace + ":" + x for x in aliases | {code}} & observed_values
                )
                candidates = sorted(set(candidates + namespaced))
            matches = candidates
            duplicate_count = sum(int(observed_counts.get(x) or 0) for x in candidates)
        if len(matches) > 1:
            gaps.append(
                {
                    "item": item_label,
                    "status": "unavailable",
                    "reason": "AMBIGUOUS_IDENTITY_BINDINGS",
                    "identity_bindings": matches,
                    "side": side,
                }
            )
            continue
        if duplicate_count > 1:
            gaps.append(
                {
                    "item": item_label,
                    "status": "unavailable",
                    "reason": "DUPLICATE_IDENTITY_ROWS_IN_SOURCE_MEMBER",
                    "identity_bindings": matches,
                    "matching_identity_rows": duplicate_count,
                    "side": side,
                }
            )
            continue
        if not matches:
            gaps.append(
                {
                    "item": item_label,
                    "status": "unavailable",
                    "reason": "IDENTITY_NOT_IN_SOURCE_MEMBER",
                    "side": side,
                }
            )
            continue
        bound_identity = matches[0]
        row_count = int(observed_counts.get(bound_identity) or 0)
        parts = {
            "side": side,
            "market_date": market_date,
            "table": table,
            "code": code,
            "exchange": identity.get("exchange"),
            "cninfo_org_id": identity.get("cninfo_org_id"),
            "benchmark": bool(identity.get("benchmark")),
            "source_member_sha256": source_sha256,
            "source_path": str(table_path),
            "identity_column": column,
            "bound_source_identity": bound_identity,
            "row_count": row_count,
            "table_num_rows": shape.get("num_rows"),
            "table_num_row_groups": shape.get("num_row_groups"),
            "shape_columns": shape.get("columns"),
            "identity_evidence": (
                "identity-column value counts; expected identity verified in source member"
            ),
        }
        rows.append(
            {
                **parts,
                "projection_member": True,
                "projection_sha256": projection_member_digest(parts),
                "projection_scope": "per-date per-identity metadata closure",
                "row_access": "identity columns only; no market values decoded",
            }
        )
    return rows


def identity_rows_for_frozen(
    universe: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    gaps = []
    codes = set((universe or {}).get("codes") or [])
    org = (universe or {}).get("company_org_ids") or {}
    source_identity = (universe or {}).get("source_identity_by_code") or {}
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
                "source_identity": source_identity.get(code),
                "source_identity_aliases": [code],
                "source_identity_namespace": "CN_STOCK_CODE:" + exchange_for(code),
            }
        )
    return rows, gaps


def benchmark_identity_rows() -> list[dict[str, Any]]:
    rows = []
    for code in RESULT_BENCHMARKS:
        rows.append(
            {
                "code": code,
                "exchange": "INDEX",
                "benchmark": True,
                "source_identity": code,
                "source_identity_aliases": [code],
                "source_identity_namespace": "CN_INDEX_CODE",
            }
        )
    return rows


def expected_input_trade_dates(inventory: dict[str, Any]) -> list[str]:
    dates = sorted(
        {
            str(m.get("market_date"))
            for m in inventory.get("members") or []
            if m.get("market_date")
        }
    )
    return dates


def project_inventory_members(
    members: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    side: str,
    gaps: list[dict[str, Any]],
    label: str,
    tables: tuple[str, ...] = ("dwd_security_bar",),
) -> list[dict[str, Any]]:
    """Project inventory members of the requested physical tables only.

    The inventory may contain both equity and index frames for a mixed-use
    date. Callers choose the table closure explicitly; this helper never lets a
    whole-day move decide that a benchmark or an equity disappears from the
    side where the contract requires it.
    """
    projected = []
    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    for member in members:
        date = str(member.get("market_date"))
        table = (
            "dwd_index_bar"
            if "dwd_index_bar" in str(member.get("path"))
            else "dwd_security_bar"
        )
        if table not in tables:
            continue
        by_date.setdefault(date, {})[table] = member
    for date in sorted(by_date):
        for table in sorted(by_date[date]):
            member = by_date[date][table]
            path = Path(member["path"])
            if not path.exists():
                for identity in identities:
                    gaps.append(
                        {
                            "item": (
                                label + ":" + date + ":" + table + ":" + str(identity.get("code"))
                            ),
                            "status": "unavailable",
                            "reason": "SOURCE_FILE_MISSING",
                            "side": side,
                        }
                    )
                continue
            if table in tables:
                projected.extend(
                    project_table_members(
                        table_path=path,
                        table=table,
                        market_date=date,
                        source_sha256=member["sha256"],
                        identities=identities,
                        side=side,
                        gaps=gaps,
                        label=label,
                    )
                )
    return projected


def collect_projection_unavailable(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [g for g in gaps if g.get("status") == "unavailable"],
        key=lambda x: (
            str(x.get("side") or ""),
            str(x.get("item") or ""),
            str(x.get("reason") or ""),
        ),
    )


def parse_clock(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def object_records(objects_root: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    records = []
    for kind in ("case-input", "case-result-capture"):
        kind_root = objects_root / kind
        if not kind_root.exists():
            continue
        for path in sorted(kind_root.glob("*/*")):
            if not path.is_file():
                continue
            try:
                record = json.loads(path.read_bytes())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            record["_object_kind"] = kind
            records.append((path.name, path, record))
    return records


def classify_member_side(
    record: dict[str, Any], input_cutoff: str, window_end: str | None
) -> tuple[str | None, str | None, dict[str, Any]]:
    """Classify one member version independently by observed visibility clocks.

    Producer case_role is evidence, not authority. For a version chain,
    first_seen_at is the item's original visibility clock and is retained as
    provenance, while version_first_seen_at/source_artifact_known_at and the raw
    response completion clock decide this particular member version. Input
    membership requires all relevant version-visibility clocks to be before the
    cutoff; result membership requires post-cutoff visibility no later than the
    declared assembly-window end. Missing or contradictory clocks are gaps and
    do not silently choose a side.
    """
    cutoff = parse_clock(input_cutoff)
    end = parse_clock(window_end) if window_end else None
    receipt = record.get("raw_source_receipt") or {}
    clocks = {
        "first_seen_at": parse_clock(record.get("first_seen_at")),
        "version_first_seen_at": parse_clock(record.get("version_first_seen_at")),
        "source_artifact_known_at": parse_clock(record.get("source_artifact_known_at")),
        "response_completed_at": parse_clock(receipt.get("response_completed_at")),
    }
    if cutoff is None:
        return None, "INPUT_CUTOFF_CLOCK_INVALID", clocks
    decision_values = [
        value
        for key, value in clocks.items()
        if key != "first_seen_at" and value is not None
    ]
    if not decision_values:
        return None, "VISIBILITY_CLOCK_MISSING", clocks
    if all(v < cutoff for v in decision_values):
        return "input", None, clocks
    if all(v >= cutoff for v in decision_values):
        if end is not None and any(v > end for v in decision_values):
            return None, "VISIBLE_AFTER_ASSEMBLY_WINDOW_END", clocks
        return "result", None, clocks
    return None, "VISIBILITY_CLOCK_CONFLICT", clocks


def collect_case_members(
    objects_root: Path,
    state_db: Path,
    case_id: str,
    input_cutoff: str,
    assembly_window_end: str | None,
) -> dict[str, Any]:
    """Enumerate the version chain in one run, including versions no longer at
    the sqlite head. Each version is independently classified by its own actual
    visibility clocks; the sqlite head remains provenance, not a substitute for
    chain enumeration."""
    sides: dict[str, list[dict[str, Any]]] = {"input": [], "result": []}
    gaps: list[dict[str, Any]] = []
    head_rows = inventory_items(state_db)
    heads = {row["id"]: row for row in head_rows}
    key_to_head = {row["member_sha"]: row["id"] for row in head_rows}
    chains: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for member_sha, _path, record in object_records(objects_root):
        if record.get("case_id") != case_id:
            continue
        payload = record.get("payload") or {}
        source = payload.get("source") or record.get("source") or "unknown-source"
        source_record_id = (
            payload.get("source_record_id") or record.get("source_record_id") or member_sha
        )
        source_key = str(source) + ":" + str(source_record_id)
        chains.setdefault(source_key, []).append((member_sha, record))

    # Head-only items whose member object is absent remain named gaps.
    for row in head_rows:
        object_missing = not any(
            row["member_sha"] == member for entries in chains.values() for member, _ in entries
        )
        if object_missing and key_to_head.get(row["member_sha"]):
            gaps.append(
                {
                    "item": "news-version:" + row["id"],
                    "status": "unavailable",
                    "reason": "SQLITE_HEAD_MEMBER_OBJECT_MISSING",
                    "member_sha256": row["member_sha"],
                }
            )

    for source_key, versions in sorted(chains.items()):
        versions.sort(key=lambda item: (item[1].get("version_first_seen_at") or "", item[0]))
        previous_sha = None
        for member_sha, record in versions:
            receipt = record.get("raw_source_receipt") or {}
            declared_previous = record.get("previous_member_sha256")
            side, gap_reason, clocks = classify_member_side(
                record, input_cutoff, assembly_window_end
            )
            role_value = record.get("case_role")
            role = str(role_value) if role_value is not None else ""
            object_kind = record["_object_kind"]
            expected_object_kind = EXPECTED_OBJECT_KIND_BY_ROLE.get(role)
            expected_side = EXPECTED_SIDE_BY_ROLE.get(role)
            if expected_object_kind is None:
                gap_reason = "CASE_ROLE_UNRECOGNIZED"
            elif object_kind != expected_object_kind:
                gap_reason = "CASE_ROLE_OBJECT_KIND_MISMATCH"
            elif side is not None and expected_side != side:
                gap_reason = "CASE_ROLE_VISIBILITY_SIDE_MISMATCH"
            entry = {
                "source_key": source_key,
                "member_sha256": member_sha,
                "object_kind": object_kind,
                "case_role": role,
                "sqlite_head_member_sha256": (heads.get(source_key) or {}).get("member_sha"),
                "sqlite_head_is_this_member": any(
                    row["member_sha"] == member_sha for row in head_rows
                ),
                "first_seen_at": record.get("first_seen_at"),
                "version_first_seen_at": record.get("version_first_seen_at"),
                "source_artifact_known_at": record.get("source_artifact_known_at"),
                "response_completed_at": receipt.get("response_completed_at"),
                "source_http_date": receipt.get("source_http_date"),
                "previous_member_sha256": declared_previous or previous_sha,
                "clock_evidence": {
                    key: value.isoformat().replace("+00:00", "Z") if value else None
                    for key, value in clocks.items()
                },
                "side_decision": "independent actual visibility clocks, not sqlite head role alone",
            }
            raw = receipt.get("raw") or {}
            raw_sha = raw.get("sha256")
            if raw_sha:
                locations = member_locations(objects_root, raw_sha)
                if locations:
                    entry["raw_object_sha256"] = raw_sha
                    entry["raw_object_kind"] = locations[0].parent.parent.name
            if gap_reason:
                gaps.append(
                    {
                        "item": "news-version:" + source_key + ":" + member_sha,
                        "status": "unavailable",
                        "reason": gap_reason,
                        "member_sha256": member_sha,
                        "clock_evidence": entry["clock_evidence"],
                    }
                )
            elif side in sides:
                sides[side].append(entry)
            previous_sha = member_sha
    for side in sides:
        sides[side].sort(
            key=lambda x: (x["source_key"], x["version_first_seen_at"] or "", x["member_sha256"])
        )
    return {"input": sides["input"], "result": sides["result"], "gaps": gaps}


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
        dates: dict[str, Any] = index["dates"]
        return dates
    if "days" in index:
        days: dict[str, Any] = index["days"]
        return days
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
            result: dict[str, Any] = entry
            return result
    return None


def seal_core_day(
    core_dir: Path,
    manifest_sha_expected: str,
    market_date: str,
    dest_dir: Path,
    gaps: list[dict[str, Any]],
    label: str,
    side_tag: str | None,
    tables: tuple[str, ...] = ("dwd_index_bar", "dwd_security_bar"),
) -> list[dict[str, Any]]:
    """Bind the day through its manifest: manifest digest must match the indexed
    binding, then each bar member is verified against the manifest-declared member
    sha256. Recomputation is a consistency check, not the source of truth."""
    rows: list[dict[str, Any]] = []
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
    original_value = snap.get("raw_path")
    original_path = Path(original_value) if original_value else None
    staged_path = anchor_input_root / (snap["source"] + ".wire")
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
    if not staged_path.exists():
        gaps.append(
            {
                "item": "anchor:" + snap["source"],
                "status": "unavailable",
                "reason": "STAGED_WIRE_FILE_MISSING",
            }
        )
        entry["status"] = "unavailable"
        entry["original_path"] = str(original_path) if original_path is not None else None
        entry["staged_path"] = str(staged_path)
        return entry
    if original_path is None:
        gaps.append(
            {
                "item": "anchor:" + snap["source"],
                "status": "unavailable",
                "reason": "ORIGINAL_WIRE_PATH_NOT_DECLARED",
            }
        )
        entry["status"] = "unavailable"
        entry["original_path"] = None
        entry["staged_path"] = str(staged_path)
        return entry
    original_binding = "ORIGINAL_PATH_DECLARED"
    original_verified = False
    if original_path is not None:
        if not original_path.exists():
            original_binding = "ORIGINAL_WIRE_FILE_MISSING"
        elif sha_file(original_path) != snap["raw_sha256"]:
            raise ValueError("ANCHOR_ORIGINAL_SHA_MISMATCH:" + snap["source"])
        else:
            original_binding = (
                "SAME_AS_STAGED_PATH_SHA256_VERIFIED"
                if original_path == staged_path
                else "ORIGINAL_WIRE_SHA256_VERIFIED"
            )
            original_verified = True
        if not original_path.exists():
            gaps.append(
                {
                    "item": "anchor:" + snap["source"],
                    "status": "unavailable",
                    "reason": "ORIGINAL_WIRE_FILE_MISSING",
                }
            )
            entry["status"] = "unavailable"
            entry["original_path"] = str(original_path)
            entry["staged_path"] = str(staged_path)
            return entry
    if sha_file(staged_path) != snap["raw_sha256"]:
        raise ValueError("ANCHOR_STAGED_SHA_MISMATCH:" + snap["source"])
    seal = link_bytes(staged_path, dest_dir, snap["raw_sha256"])
    blocked = (
        snap.get("status") in BLOCKED_REFERENCE_STATUSES or (snap.get("http_status") or 0) >= 400
    )
    entry.update(
        {
            "sha256": seal["sha256"],
            "original_path": str(original_path) if original_path is not None else None,
            "staged_path": str(staged_path),
            "original_binding": original_binding,
            "original_verified": original_verified,
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
    """Create a genuinely new seal root exactly once.

    Any pre-existing root is refused before writing: an existing sealed package,
    an empty directory and a directory containing unrelated junk all receive the
    same fail-closed treatment. os.mkdir is atomic for the final path component,
    so two concurrent assemblers cannot both treat the root as newly available.
    """
    try:
        output_root.mkdir()
    except FileExistsError:
        if any((output_root / name).exists() for name in ("RECEIPT.json", "input", "result")):
            raise FileExistsError("SEALED_ROOT_ALREADY_PRESENT:" + str(output_root)) from None
        raise FileExistsError("OUTPUT_ROOT_ALREADY_EXISTS:" + str(output_root)) from None


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
    # Exclusive creation happens before reading or deriving any result object.
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

    # Input side: inventory members strictly before the market date of the
    # frozen input cutoff are the input source for the frozen equity closure.
    # Dates are not moved wholesale when a result-side benchmark shares the
    # same market date: 2026-11-23 may simultaneously carry input-side frozen
    # equities and result-side benchmark evidence. Inventory bytes stamped on
    # or after the cutoff market date never enter the input closure; they are
    # resealed blind as overlap evidence on the result side.
    inventory_members = list(inventory.get("members", []))
    cutoff_date = str(policy["input_cutoff"])[:10]
    pre_members = [
        m for m in inventory_members if str(m.get("market_date") or "") < cutoff_date
    ]
    input_dates = sorted(
        {
            str(m.get("market_date"))
            for m in pre_members
            if m.get("market_date")
        }
    )
    result_date_set = {str(date) for date in result_dates}
    result_equity_dates = sorted(date for date in result_date_set if date >= cutoff_date)
    pre_member_ids = {id(m) for m in pre_members}
    overlap_members = [m for m in inventory_members if id(m) not in pre_member_ids]
    bars_input = verify_inventory_bars(pre_members, input_root / "bars", "input-bars", gaps)
    frozen_identities, frozen_identity_gaps = identity_rows_for_frozen(universe)
    gaps.extend(frozen_identity_gaps)
    input_member_projection = project_inventory_members(
        pre_members,
        frozen_identities,
        "input",
        gaps,
        "input-projection",
        tables=("dwd_security_bar",),
    )
    write_json_atomic(
        input_root / "member-projection.json",
        {
            "schema": "quanthus.p10.nvda-case-input-member-projection.v1",
            "scope": "frozen 12 equities only; benchmark count zero",
            "dates": input_dates,
            "members": input_member_projection,
        },
    )
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
    for anchor_receipt_path in sorted(anchor_input_root.glob("*.receipt.json")):
        digest = sha_file(anchor_receipt_path)
        link_bytes(anchor_receipt_path, input_root / "anchor-receipts", digest)

    case_members = collect_case_members(
        objects_root,
        state_db,
        case_id,
        policy["input_cutoff"],
        "2026-11-26T05:00:00+08:00",
    )
    gaps.extend(case_members["gaps"])
    input_news = seal_news_side(
        case_members["input"], objects_root, input_root / "news", gaps, "input-news"
    )
    result_news = seal_news_side(
        case_members["result"], objects_root, result_root / "news", gaps, "result-news"
    )

    # Result side bars through the DATES index: "dates" entries bind core dir +
    # core manifest sha + manifest-declared member sha.
    bars_result: list[dict[str, Any]] = []
    result_member_projection: list[dict[str, Any]] = []
    result_benchmark_projection: list[dict[str, Any]] = []
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
                tables=(
                    ("dwd_index_bar", "dwd_security_bar")
                    if date in result_equity_dates
                    else ("dwd_index_bar",)
                ),
            )
            bars_result.extend(rows)
            benchmark_identities = benchmark_identity_rows()
            for row in rows:
                if row.get("table") == "dwd_security_bar" and date in result_equity_dates:
                    result_member_projection.extend(
                        project_table_members(
                            table_path=Path(row["source_path"]),
                            table="dwd_security_bar",
                            market_date=date,
                            source_sha256=row["sha256"],
                            identities=frozen_identities,
                            side="result",
                            gaps=gaps,
                            label="result-projection",
                        )
                    )
                elif row.get("table") == "dwd_index_bar":
                    result_benchmark_projection.extend(
                        project_table_members(
                            table_path=Path(row["source_path"]),
                            table="dwd_index_bar",
                            market_date=date,
                            source_sha256=row["sha256"],
                            identities=benchmark_identities,
                            side="result",
                            gaps=gaps,
                            label="result-benchmark-projection",
                        )
                    )
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
        "member_projection": input_member_projection,
        "member_projection_unavailable": [
            g for g in collect_projection_unavailable(gaps) if g.get("side") == "input"
        ],
        "anchors": anchor_entries,
        "news_members": input_news,
        "counts": {
            "bars": len(bars_input),
            "anchors": len(anchor_entries),
            "news_members": len(input_news),
            "member_projection": len(input_member_projection),
            "benchmark_projection": 0,
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
        "member_projection": result_member_projection,
        "benchmark_projection": result_benchmark_projection,
        "member_projection_unavailable": [
            g for g in collect_projection_unavailable(gaps) if g.get("side") == "result"
        ],
        "overlap_inventory_bars_resealed_blind": len(bars_overlap),
        "index_binding": index_binding,
        "news_members": result_news,
        "counts": {
            "bars": len(all_result_bars),
            "news_members": len(result_news),
            "member_projection": len(result_member_projection),
            "benchmark_projection": len(result_benchmark_projection),
        },
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

        def read_chunk() -> bytes:
            return handle.read(1 << 20)

        for chunk in iter(read_chunk, b""):
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
    auth_file_sha: str | None = (
        sha_file(authorization_file) if authorization_file.exists() else None
    )
    auth_error: str | None
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
