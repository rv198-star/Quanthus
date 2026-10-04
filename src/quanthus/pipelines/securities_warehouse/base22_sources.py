"""Read-only real-input integrity assessment; never admits or publishes a Base.

The frozen synthetic image contract remains unchanged. This adapter deliberately
produces evidence, not a synthetic plan masquerading as a real qualification.
Only file identities, schemas and censuses leave the sealed data boundary.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import stat
import time
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path, PurePosixPath
from typing import Any

from .base22 import CONTRACT_SHA, THROUGH, canonical, digest, file_sha, runtime_info


class SourceAssessmentError(RuntimeError):
    """A physical input, its declaration or a frozen identity is invalid."""


def physical_path(path: Path) -> Path:
    """Reject symlinks in every existing component before resolving a path."""
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise SourceAssessmentError("SOURCE_SYMLINK")
    return path


def disjoint_output(output: Path, sources: list[Path]) -> Path:
    """Read-only preflight, including output ancestors; no mkdir or lock here."""
    output = physical_path(output)
    for source in sources:
        source = physical_path(source)
        if output == source or output in source.parents or source in output.parents:
            raise SourceAssessmentError("SOURCE_OUTPUT_OVERLAP")
    return output


def _member(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise SourceAssessmentError("INVALID_MEMBER_PATH")
    part = PurePosixPath(relative)
    if part.is_absolute() or ".." in part.parts or part.as_posix() != relative:
        raise SourceAssessmentError("INVALID_MEMBER_PATH")
    path = physical_path(root / relative)
    if path == root or root not in path.parents:
        raise SourceAssessmentError("INVALID_MEMBER_PATH")
    return path


def _fingerprint(path: Path) -> tuple[int, int, int, int, int]:
    s = path.stat()
    if not stat.S_ISREG(s.st_mode):
        raise SourceAssessmentError("SOURCE_NOT_REGULAR")
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _read_json(path: Path) -> tuple[dict[str, Any], str, tuple[int, int, int, int, int]]:
    before = _fingerprint(path)
    data = path.read_bytes()
    if _fingerprint(path) != before:
        raise SourceAssessmentError("SOURCE_CHANGED")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise SourceAssessmentError("MANIFEST_NOT_OBJECT")
    return value, "sha256:" + hashlib.sha256(data).hexdigest(), before


def _tree(root: Path) -> set[str]:
    def scan_failed(error: OSError) -> None:
        # os.walk otherwise suppresses scandir errors and returns a partial tree.
        raise SourceAssessmentError("SOURCE_SCAN_FAILED") from error

    found: set[str] = set()
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=scan_failed):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink():
                raise SourceAssessmentError("SOURCE_SYMLINK")
            if name in files:
                _fingerprint(path)
                found.add(path.relative_to(root).as_posix())
    return found


def _layout(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    kind = manifest.get("kind")
    if kind == "quanthus_ods_base_bundle" and manifest.get("schema_version") == 1:
        content = manifest["content"]
        if digest(content) != manifest.get("content_digest"):
            raise SourceAssessmentError("CONTENT_DIGEST_MISMATCH")
        if date.fromisoformat(content["through_date"]) > THROUGH:
            raise SourceAssessmentError("SOURCE_BUSINESS_DATE_OUTSIDE_SCOPE")
        return content["files"], {
            "kind": kind,
            "identity": manifest["bundle_id"],
            "through_date": content["through_date"],
            "content_digest": manifest["content_digest"],
            "built_at": manifest.get("built_at"),
            "source_authority": content.get("authority"),
        }
    if kind == "dwd_domain_base_package" and manifest.get("schema_version") == 1:
        keys = (
            "package_id",
            "run_id",
            "through_date",
            "input_plan_digest",
            "ods_base",
            "model_sha256",
            "model_extension_sha256",
            "code_revision",
            "upstream_candidates",
            "row_counts",
            "files",
        )
        content = {key: manifest[key] for key in keys}
        if "repair_provenance" in manifest:
            content["repair_provenance"] = manifest["repair_provenance"]
        if digest(content) != manifest.get("content_digest"):
            raise SourceAssessmentError("CONTENT_DIGEST_MISMATCH")
        if date.fromisoformat(manifest["through_date"]) > THROUGH:
            raise SourceAssessmentError("SOURCE_BUSINESS_DATE_OUTSIDE_SCOPE")
        return manifest["files"], {
            "kind": kind,
            "identity": manifest["package_id"],
            "through_date": manifest["through_date"],
            "content_digest": manifest["content_digest"],
            "ods_base": manifest.get("ods_base"),
            "model_sha256": manifest.get("model_sha256"),
            "model_extension_sha256": manifest.get("model_extension_sha256"),
            "declared_row_counts": manifest.get("row_counts"),
        }
    if kind == "dwd_base_correction" and manifest.get("schema_version") == 1:
        closure = {key: manifest[key] for key in ("parent", "normalization_revision", "files")}
        if digest(closure) != manifest.get("content_digest"):
            raise SourceAssessmentError("CONTENT_DIGEST_MISMATCH")
        return manifest["files"], {
            "kind": kind,
            "identity": manifest["correction_id"],
            "parent": manifest["parent"],
            "built_at": manifest["built_at"],
            "normalization_revision": manifest["normalization_revision"],
            "content_digest": manifest["content_digest"],
            "source_closure_status": "legacy_manifest_unretained",
        }
    if manifest.get("schema_version") == 1 and manifest.get("status") == "ready":
        if manifest.get("schedule") not in {"daily", "weekly"}:
            raise SourceAssessmentError("UNSUPPORTED_RELEASE_SCHEDULE")
        from quanthus.pipelines.contracts import OdsRelease

        # Validate the existing release contract without changing its gates.
        OdsRelease.from_dict(manifest)
        if not date(2026, 8, 1) <= date.fromisoformat(manifest["market_date"]) <= THROUGH:
            raise SourceAssessmentError("SOURCE_BUSINESS_DATE_OUTSIDE_SCOPE")
        files = [dict(x, path=x["relative_path"]) for x in manifest["artifacts"]]
        return files, {
            "kind": "ods_release",
            "identity": manifest["release_id"],
            "market_date": manifest["market_date"],
            "schedule": manifest["schedule"],
            "created_at": manifest["created_at"],
            "schedule_contract_id": manifest.get("schedule_contract_id"),
            "schedule_contract_revision": manifest.get("schedule_contract_revision"),
            "schedule_config_sha256": manifest.get("schedule_config_sha256"),
        }
    raise SourceAssessmentError("UNSUPPORTED_SOURCE_KIND")


def assess_package(
    root: Path,
    *,
    manifest_name: str = "manifest.json",
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Hash every declared member and census every parquet footer, without row output.

    A PASS is byte/physical-schema integrity only. Footers do not establish row
    semantics, PIT, coverage, repair admission, or a usable independent image.
    Mismatches retain evidence and do not prevent assessing other packages.
    """
    pq = importlib.import_module("pyarrow.parquet")

    root = physical_path(root)
    manifest_path = _member(root, manifest_name)
    manifest, manifest_sha, manifest_stat = _read_json(manifest_path)
    if expected_manifest_sha256 and manifest_sha != expected_manifest_sha256:
        raise SourceAssessmentError("FROZEN_MANIFEST_MISMATCH")
    ready = _member(root, "READY")
    ready_stat = _fingerprint(ready)
    if ready.read_text(encoding="ascii").strip() != manifest_sha:
        raise SourceAssessmentError("READY_MISMATCH")
    files, identity = _layout(manifest)
    if not isinstance(files, list) or not files:
        raise SourceAssessmentError("EMPTY_FILE_INVENTORY")
    inventory: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    seen: set[str] = set()
    schema_census: dict[str, dict[str, Any]] = {}
    table_rows: Counter[str] = Counter()
    for entry in files:
        relative = entry["path"]
        if relative in seen:
            raise SourceAssessmentError("DUPLICATE_MEMBER")
        seen.add(relative)
        record = {
            "path": relative,
            "declared_sha256": entry["sha256"],
            "declared_bytes": entry["size_bytes"],
        }
        for key in ("name", "dataset_id", "source_revision", "knowledge_cutoff", "role"):
            if key in entry:
                record[key] = entry[key]
        try:
            path = _member(root, relative)
            before = _fingerprint(path)
            actual_sha = file_sha(path)
            record.update(sha256=actual_sha, size_bytes=before[2])
            if actual_sha != entry["sha256"] or before[2] != entry["size_bytes"]:
                failures.append({"path": relative, "reason": "HASH_OR_SIZE_MISMATCH"})
            if path.suffix == ".parquet":
                parquet = pq.ParquetFile(path)
                schema = [
                    {"name": f.name, "type": str(f.type), "nullable": f.nullable}
                    for f in parquet.schema_arrow
                ]
                schema_sha = digest(schema)
                schema_census.setdefault(schema_sha, {"fields": schema})
                record.update(rows=parquet.metadata.num_rows, schema_sha256=schema_sha)
                for part in PurePosixPath(relative).parts:
                    table = part.removesuffix(".parquet")
                    if table.startswith("dwd_") and relative.startswith("payload/"):
                        record["table"] = table
                        table_rows[table] += parquet.metadata.num_rows
                        break
            if _fingerprint(path) != before:
                raise SourceAssessmentError("SOURCE_CHANGED")
        except (OSError, ValueError, SourceAssessmentError) as exc:
            # Never serialize exceptions containing data values or JSON bodies.
            failures.append(
                {
                    "path": relative,
                    "reason": str(exc)
                    if isinstance(exc, SourceAssessmentError)
                    else type(exc).__name__,
                }
            )
        inventory.append(record)
    actual = _tree(root)
    expected = seen | {manifest_name, "READY"}
    failures.extend({"path": p, "reason": "UNDECLARED_FILE"} for p in sorted(actual - expected))
    failures.extend({"path": p, "reason": "MISSING_FILE"} for p in sorted(expected - actual))
    if _fingerprint(manifest_path) != manifest_stat or _fingerprint(ready) != ready_stat:
        raise SourceAssessmentError("SOURCE_CHANGED")
    declared_rows = identity.get("declared_row_counts")
    if declared_rows is not None and dict(table_rows) != declared_rows:
        failures.append({"path": manifest_name, "reason": "DECLARED_ROW_CENSUS_MISMATCH"})
    return {
        "assessment_kind": "read_only_physical_integrity_not_qualification",
        "root": str(root),
        "manifest_sha256": manifest_sha,
        "manifest_size_bytes": manifest_stat[2],
        "ready_sha256": file_sha(ready),
        "identity": identity,
        "status": "FAIL" if failures else "PASS",
        "files": inventory,
        "schemas": schema_census,
        "table_rows": dict(sorted(table_rows.items())),
        "verified_bytes": sum(x.get("size_bytes", 0) for x in inventory),
        "failures": failures,
        "limitations": [
            "not an atomic snapshot",
            "footer census is not row-level verification",
            "no source admission, no historical repair acceptance, no publication",
        ],
    }


def canonical_daily_basis(batch: Any) -> tuple[Any, dict[str, int]]:
    """Apply the governed none alias without copying Decimal or clock buffers.

    This is an adapter building block, not admission or a final image writer.
    Other intervals remain unchanged. Unknown daily bases block this projection.
    """
    pa = importlib.import_module("pyarrow")
    pc = importlib.import_module("pyarrow.compute")

    from .daily_price_basis import POLICY, contract

    policy = contract(POLICY)
    assert policy is not None
    index = batch.schema.get_field_index("price_basis")
    if index < 0:
        raise SourceAssessmentError("PRICE_BASIS_SCHEMA_MISSING")
    daily = pc.equal(batch.column("interval_size"), "1d")
    known = pc.is_in(batch.column(index), value_set=pa.array(["none", "UNADJUSTED"]))
    if pc.any(pc.and_(daily, pc.invert(pc.fill_null(known, False)))).as_py():
        raise SourceAssessmentError("UNKNOWN_DAILY_PRICE_BASIS")
    mapped = batch.set_column(
        index,
        batch.schema.field(index),
        pc.if_else(daily, pa.scalar(policy["canonical_price_basis"]), batch.column(index)),
    )
    for i in range(len(batch.schema)):
        if i != index and not batch.column(i).equals(mapped.column(i)):
            raise SourceAssessmentError("PRICE_OR_TIME_DRIFT")
    return mapped, {
        "rows": batch.num_rows,
        "daily_rows": pc.sum(pc.cast(daily, pa.int64())).as_py() or 0,
        "changed_basis_rows": pc.sum(
            pc.cast(
                pc.fill_null(pc.not_equal(batch.column(index), mapped.column(index)), False),
                pa.int64(),
            )
        ).as_py()
        or 0,
        "non_basis_field_differences": 0,
    }


def announcement_coverage(earlier: list[Path], rolling: Path) -> dict[str, Any]:
    """Prove rolling-key/content coverage without conflating observation clocks.

    The key is the producer's (stock_code, ann_id). An unchanged business body
    observed again is coverage evidence only; acquired_at is retained and is never
    replaced by the event date. Conflicting revisions require a separate rule.
    """
    pq = importlib.import_module("pyarrow.parquet")

    def read(path: Path) -> tuple[dict[tuple[str, str], set[str]], dict[str, int]]:
        path = physical_path(path)
        before = _fingerprint(path)
        parquet = pq.ParquetFile(path)
        if not {"stock_code", "ann_id", "acquired_at", "publish_time"} <= set(
            parquet.schema_arrow.names
        ):
            raise SourceAssessmentError("ANNOUNCEMENT_SCHEMA_UNKNOWN")
        bodies: dict[tuple[str, str], set[str]] = {}
        clocks = {"rows": 0, "acquired_unknown": 0, "publication_unknown": 0}
        for batch in parquet.iter_batches(batch_size=65536):
            for row in batch.to_pylist():
                key = (row["stock_code"], row["ann_id"])
                if any(not isinstance(value, str) or not value for value in key):
                    raise SourceAssessmentError("ANNOUNCEMENT_KEY_UNKNOWN")
                clocks["rows"] += 1
                clocks["acquired_unknown"] += not bool(row["acquired_at"])
                clocks["publication_unknown"] += not bool(row["publish_time"])
                body = {k: v for k, v in row.items() if k != "acquired_at"}
                bodies.setdefault(key, set()).add(digest(body))
        if _fingerprint(path) != before:
            raise SourceAssessmentError("SOURCE_CHANGED")
        return bodies, clocks

    current, current_clocks = read(rolling)
    results = []
    for path in earlier:
        old, clocks = read(path)
        missing = set(old) - set(current)
        conflicts = sum(
            not values <= current.get(key, set()) for key, values in old.items() if key in current
        )
        results.append(
            {
                "sha256": file_sha(path),
                "keys": len(old),
                **clocks,
                "missing_keys": len(missing),
                "uncovered_business_revisions": conflicts,
            }
        )
    return {
        "kind": "rolling_announcement_coverage_not_merge_admission",
        "rolling_sha256": file_sha(rolling),
        "rolling_keys": len(current),
        "rolling_clocks": current_clocks,
        "earlier": results,
        "status": "PASS"
        if all(x["missing_keys"] == 0 and x["uncovered_business_revisions"] == 0 for x in results)
        else "BLOCKED",
        "acquisition_times": "retained; coverage does not authorize collapsing knowledge versions",
    }


def tail_raw_projection(path: Path, expected_sha256: str, stock_code: str) -> Any:
    """Decode the retained Baostock tail envelope for sealed quality comparison.

    Preserve exact numbers and source status flags. This does not confer historical
    accepted-child identity or invent a missing acquisition/knowledge timestamp.
    The returned table stays inside governance processing; reports contain hashes
    and counts only, never its prices.
    """
    pa = importlib.import_module("pyarrow")

    path = physical_path(path)
    raw, observed, _ = _read_json(path)
    if observed != expected_sha256:
        raise SourceAssessmentError("FROZEN_RAW_MISMATCH")
    fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adjustflag",
        "turn",
        "pctChg",
        "tradestatus",
        "isST",
    ]
    if (
        raw.get("fields") != fields
        or raw.get("n_rows") != len(raw.get("rows", []))
        or str(raw.get("meta", {}).get("error_code")) != "0"
    ):
        raise SourceAssessmentError("TAIL_ENVELOPE_UNKNOWN")
    rows, keys = [], set()
    numbers = ["open", "high", "low", "close", "volume", "amount"]
    for values in raw["rows"]:
        if len(values) != len(fields):
            raise SourceAssessmentError("TAIL_ROW_SHAPE")
        row = dict(zip(fields, values, strict=True))
        if row["code"] != raw.get("bs_code") or row["code"].split(".")[-1] != stock_code:
            raise SourceAssessmentError("TAIL_IDENTITY_CONFLICT")
        if str(row["adjustflag"]) != "3":
            raise SourceAssessmentError("TAIL_BASIS_NOT_UNADJUSTED")
        day = date.fromisoformat(row["date"])
        if day in keys:
            raise SourceAssessmentError("TAIL_DUPLICATE_KEY")
        keys.add(day)
        result = {
            "stock_code": stock_code,
            "trade_date": day,
            "tradestatus": row["tradestatus"],
            "isST": row["isST"],
        }
        for name in numbers:
            try:
                with localcontext() as context:
                    context.prec = 50
                    value = Decimal(row[name]) if row[name] != "" else None
                    if value is not None and (
                        not value.is_finite() or value != value.quantize(Decimal("0.0000000001"))
                    ):
                        raise SourceAssessmentError("TAIL_DECIMAL_PRECISION_LOSS")
            except (InvalidOperation, TypeError) as exc:
                raise SourceAssessmentError("TAIL_NUMBER_UNKNOWN") from exc
            result[name] = value
        rows.append(result)
    schema = pa.schema(
        [
            ("stock_code", pa.string()),
            ("trade_date", pa.date32()),
            ("tradestatus", pa.string()),
            ("isST", pa.string()),
        ]
        + [(name, pa.decimal128(38, 10)) for name in numbers]
    )
    try:
        return pa.Table.from_pylist(rows, schema=schema)
    except Exception:
        raise SourceAssessmentError("TAIL_DECIMAL_SCHEMA_OVERFLOW") from None


def run_assessment(selection_path: Path, output: Path) -> dict[str, Any]:
    """Freeze explicit source identities, retain failures, and never write READY.

    All structural/physical overlap checks precede any output mutation. Callers
    must supply original exact manifest digests, not mutable pointers. Actual
    payload hashes are recomputed on every invocation; no receipt/cache shortcut.
    """
    selection_path = physical_path(selection_path)
    selection, selection_sha, selection_stat = _read_json(selection_path)
    packages = selection.get("packages")
    declared_roots = selection.get("source_roots")
    if (
        selection.get("schema_version") != 1
        or selection.get("purpose") != "p10_base22_source_assessment"
        or selection.get("image_contract_sha256") != CONTRACT_SHA
        or not isinstance(packages, list)
        or not packages
        or not isinstance(declared_roots, list)
        or not declared_roots
        or any(not isinstance(root, str) or not Path(root).is_absolute() for root in declared_roots)
    ):
        raise SourceAssessmentError("ASSESSMENT_SELECTION_INVALID")
    labels: set[str] = set()
    roots: list[Path] = []
    source_roots = [physical_path(Path(root)) for root in declared_roots]
    for item in packages:
        if (
            not isinstance(item, dict)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", item.get("label", "")) is None
            or item["label"] in labels
            or item["label"] in {"selection", "assessment"}
            or item.get("manifest_name") not in {"manifest.json", "release.json"}
            or not isinstance(item.get("root"), str)
            or not Path(item["root"]).is_absolute()
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("expected_manifest_sha256", ""))
            is None
        ):
            raise SourceAssessmentError("ASSESSMENT_MEMBER_INVALID")
        labels.add(item["label"])
        roots.append(physical_path(Path(item["root"])))
        if not any(roots[-1] == source or source in roots[-1].parents for source in source_roots):
            raise SourceAssessmentError("SOURCE_OUTSIDE_FROZEN_ROOTS")
    output = disjoint_output(output, [*source_roots, *roots, selection_path])
    # Exclusive run directory is the only writer; do not recover/overwrite older runs.
    output.mkdir(parents=True, exist_ok=False)
    (output / "selection.json").write_bytes(selection_path.read_bytes())
    summary = []
    for item, root in zip(packages, roots, strict=True):
        start = time.monotonic()
        try:
            report = assess_package(
                root,
                manifest_name=item["manifest_name"],
                expected_manifest_sha256=item["expected_manifest_sha256"],
            )
        except Exception as exc:
            report = {
                "status": "FAIL",
                "root": str(root),
                "reason": str(exc)
                if isinstance(exc, SourceAssessmentError)
                else type(exc).__name__,
            }
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
        (output / (item["label"] + ".json")).write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        summary.append(
            {
                "label": item["label"],
                "status": report["status"],
                "manifest_sha256": report.get("manifest_sha256"),
                "bytes": report.get("verified_bytes"),
                "elapsed_seconds": report["elapsed_seconds"],
            }
        )
    if _fingerprint(selection_path) != selection_stat:
        raise SourceAssessmentError("SOURCE_CHANGED")
    result = {
        "kind": "base22_source_assessment_not_build_plan",
        "selection_sha256": selection_sha,
        "evaluator_sha256": file_sha(Path(__file__)),
        "runtime": runtime_info(),
        "physical_integrity": "PASS" if all(x["status"] == "PASS" for x in summary) else "FAIL",
        "packages": summary,
        "candidate_admission": "NOT_GRANTED",
        "quality_gates": {str(i): "NOT_QUALIFIED_BY_INTEGRITY" for i in range(470, 475)},
    }
    (output / "assessment.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def history_document_lower_bound(
    physical_schemas: dict[str, Any], table_rows: dict[str, int]
) -> dict[str, Any]:
    """Conservative byte floor for the existing full-history SQLite document stage.

    Each canonical row stores every column name and at least one byte per value.
    Zero is a shorter JSON token than any encoded Decimal/time/string/null value.
    This excludes keys, origins, partitions, SQLite pages, indexes and merge copies.
    It uses schema/counts only and never samples or reveals protected prices.
    """
    if set(physical_schemas) != set(table_rows) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in table_rows.values()
    ):
        raise SourceAssessmentError("CAPACITY_CENSUS_INVALID")
    tables = {
        name: {
            "rows": table_rows[name],
            "minimum_document_bytes_per_row": len(canonical(dict.fromkeys(schema.names, 0))),
            "minimum_document_bytes": table_rows[name]
            * len(canonical(dict.fromkeys(schema.names, 0))),
        }
        for name, schema in sorted(physical_schemas.items())
    }
    return {
        "kind": "schema_only_full_history_sqlite_document_lower_bound",
        "minimum_document_bytes": sum(x["minimum_document_bytes"] for x in tables.values()),
        "tables": tables,
        "includes_indexes_or_merge_copy": False,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_assessment(args.selection, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "packages"}))
    raise SystemExit(0 if result["physical_integrity"] == "PASS" else 1)
